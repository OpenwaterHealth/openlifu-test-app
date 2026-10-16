"""Offer each device's newest published firmware release as its update.

The app used to update devices only to the signed image bundled with the
pinned ``openlifu-sdk``. That lags reality: a firmware release lands in its
repo's GitHub releases well before the SDK pin that bundles it moves, so an
operator could be a release behind with the app showing nothing to do.

This module polls each firmware repo's ``releases/latest`` endpoint and, when
that release is newer than the bundled image, downloads its signed ``.bin``
into a local cache. The Settings page then offers that file as the firmware
to install, so the existing update button reads "Upgrade Firmware 1.2.6 ->
1.2.7" exactly as it does for any newer image. Three deliberate properties:

* **The bundled image is the backup, and failure is never fatal.** The app
  runs on benches with no internet. On *any* failure (no DNS, proxy, timeout,
  rate limit, no published release, a download that doesn't verify,
  ``requests`` missing) everything falls back to the SDK-bundled image -- the
  pre-existing behaviour -- and the UI says it's using the local backup.
* **Never a downgrade.** If GitHub's latest tag is not newer than the bundled
  image (the SDK can pin a build ahead of the public release), nothing is
  downloaded and the bundled image is used.
* **Only verified images are offered.** A downloaded file is accepted only if
  it is an SBSFU-signed image whose header version matches the release tag.
  Anything else is discarded and the bundled image stands in. The device
  bootloader verifies the signature again at flash/boot time.

The upshot is a single invariant the UI can rely on: the "latest version" this
module reports is always the version of a firmware file that is present on
disk and installable right now.

Everything here is blocking I/O -- call :func:`refresh_release_versions` from
a worker thread, never from the Qt main thread.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from lifu.lifu_constants import (
    packaged_console_fw_version,
    packaged_transmitter_fw_version,
    parse_firmware_version,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Endpoints / policy
# =============================================================================
# Firmware repos (mirrors openlifu_sdk.util.firmware's CONSOLE/TRANSMITTER
# _FIRMWARE_URL constants; kept app-side because the SDK deliberately has no
# runtime GitHub flow of its own).
CONSOLE_FW_REPO = "OpenwaterHealth/openlifu-console-fw"
TRANSMITTER_FW_REPO = "OpenwaterHealth/openlifu-transmitter-fw"

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/{repo}/releases/latest"
# Web equivalent: 302s to /releases/tag/<tag>. Used when the API is
# rate-limited (see _fetch_via_web_redirect).
GITHUB_LATEST_RELEASE_WEB_URL = "https://github.com/{repo}/releases/latest"
GITHUB_ASSET_URL = "https://github.com/{repo}/releases/download/{tag}/{asset}"

# The signed-image asset each release publishes. These match the canonical
# filenames the SDK bundles (openlifu_sdk.util.firmware), so a downloaded
# release image is a drop-in replacement for the bundled one.
FIRMWARE_ASSET_NAME = {
    "console": "openlifu-console-fw-signed.bin",
    "transmitter": "openlifu-transmitter-fw-signed.bin",
}

# Short on purpose: this runs on app start and on every device connect. An
# unreachable host must not keep a worker thread (or a manual "retry" click)
# hanging around for the better part of a minute.
RELEASE_POLL_TIMEOUT_S = 5.0

# The image download gets a longer budget than the version probe -- it's a
# real transfer, not a header read -- but still bounded.
IMAGE_DOWNLOAD_TIMEOUT_S = 30.0

# These images are tens of KB. The cap exists so a redirect to something
# unexpected (a login page, an error blob) can't stream indefinitely.
MAX_FIRMWARE_IMAGE_BYTES = 8 * 1024 * 1024

# How long a *successful* result is considered fresh. Re-polling on every
# connect would burn through GitHub's 60 req/hr unauthenticated budget.
RELEASE_CACHE_TTL_S = 6 * 60 * 60

# A failed check is retried sooner than a successful one is refreshed -- the
# common case is "the laptop got its network back a few minutes later".
RELEASE_RETRY_AFTER_ERROR_S = 15 * 60

CONSOLE = "console"
TRANSMITTER = "transmitter"


@dataclass(frozen=True)
class ReleaseInfo:
    """Outcome of the last GitHub poll for one firmware repo."""

    repo: str
    version: str | None = None      # tag of the latest release, "v" stripped
    # Verified signed image for ``version`` on local disk. None means "use
    # the bundled backup" -- either the release isn't newer than what the
    # SDK ships, or the download/verification didn't succeed.
    image_path: str | None = None
    error: str | None = None        # why the poll failed, for the UI notice
    # True when the failure was a connectivity one (DNS/proxy/TLS/timeout)
    # rather than GitHub answering with something unusable. Drives the
    # wording of the operator-facing notice: "offline" is misleading when
    # the host was reachable and simply has no published release.
    network_error: bool = False
    attempted_at: float | None = None   # time.monotonic() of the last attempt
    succeeded_at: float | None = None   # time.monotonic() of the last success


_lock = threading.Lock()
_state: dict[str, ReleaseInfo] = {
    CONSOLE: ReleaseInfo(repo=CONSOLE_FW_REPO),
    TRANSMITTER: ReleaseInfo(repo=TRANSMITTER_FW_REPO),
}

_PACKAGED_LOOKUP = {
    CONSOLE: packaged_console_fw_version,
    TRANSMITTER: packaged_transmitter_fw_version,
}


# =============================================================================
# Polling
# =============================================================================

def _describe_failure(exc: BaseException) -> tuple[str, bool]:
    """Turn a failed poll into ``(reason, is_connectivity_problem)``.

    Must not raise: it runs inside the ``except`` block that keeps every
    network failure non-fatal. ``requests`` is resolved defensively because
    one of the failure modes being described is "``requests`` is missing".
    """
    if isinstance(exc, ImportError):
        return ("the HTTP client library is unavailable", True)
    try:
        import requests

        if isinstance(exc, requests.exceptions.Timeout):
            return (f"timed out after {RELEASE_POLL_TIMEOUT_S:.0f}s", True)
        if isinstance(exc, requests.exceptions.ProxyError):
            return ("proxy refused the connection", True)
        if isinstance(exc, requests.exceptions.SSLError):
            return ("TLS handshake failed (proxy or certificate issue)", True)
        if isinstance(exc, requests.exceptions.ConnectionError):
            return ("could not reach github.com", True)
    except Exception:  # noqa: BLE001 - requests unavailable; fall through
        pass
    # GitHub answered, it just couldn't give us a usable version (HTTP error,
    # rate limit, no release published, junk tag). Not a connectivity fault.
    return (str(exc) or exc.__class__.__name__, False)


def _clean_tag(tag: str) -> str:
    """Normalise a release tag to a bare version, raising if it isn't one."""
    tag = (tag or "").strip().lstrip("vV")
    if parse_firmware_version(tag) is None:
        raise RuntimeError(f"unparseable release tag {tag!r}")
    return tag


def _fetch_via_api(repo: str) -> str:
    """Latest release tag via the documented REST endpoint.

    ``requests`` is imported here rather than at module scope so that a build
    that somehow ships without it degrades to the packaged-version path
    instead of failing to import the whole ``lifu`` package.
    """
    import requests

    resp = requests.get(
        GITHUB_LATEST_RELEASE_URL.format(repo=repo),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=RELEASE_POLL_TIMEOUT_S,
    )
    if resp.status_code == 404:
        # Unauthenticated callers also get 404 for a repo they can't see.
        raise RuntimeError("no public release found (private repo, or none "
                           "published yet)")
    if resp.status_code in (403, 429):
        # Unauthenticated callers share 60 requests/hour *per IP* -- a lab
        # behind one NAT burns through that quickly, hence the web fallback.
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            raise RuntimeError("GitHub API rate limit reached for this network")
        raise RuntimeError(f"GitHub refused the request (HTTP {resp.status_code})")
    resp.raise_for_status()
    return _clean_tag((resp.json() or {}).get("tag_name") or "")


def _fetch_via_web_redirect(repo: str) -> str:
    """Latest release tag from github.com's ``/releases/latest`` redirect.

    Fallback for when the REST API is rate-limited: the web endpoint 302s to
    ``/releases/tag/<tag>`` and is not subject to the API's per-IP hourly
    budget. Redirects are not followed -- the Location header is the answer,
    so no page body is downloaded.
    """
    import requests

    resp = requests.get(
        GITHUB_LATEST_RELEASE_WEB_URL.format(repo=repo),
        allow_redirects=False,
        timeout=RELEASE_POLL_TIMEOUT_S,
    )
    location = resp.headers.get("Location", "")
    marker = "/releases/tag/"
    if marker not in location:
        # No releases at all redirects to /releases; anything else means the
        # page shape changed and the API path is the one to trust anyway.
        raise RuntimeError("no release tag in the redirect")
    return _clean_tag(location.rsplit(marker, 1)[1])


def _fetch_latest_tag(repo: str) -> str:
    """Return the tag of ``repo``'s latest (non-draft, non-prerelease) release.

    Raises on any failure -- callers convert that into a fallback + notice.
    The documented API is tried first; the web redirect covers the common
    rate-limited case. If both fail, the API's reason is the one reported:
    it's the more precise diagnosis (and, when the box is simply offline,
    both fail the same way anyway).
    """
    try:
        return _fetch_via_api(repo)
    except Exception as api_exc:
        try:
            return _fetch_via_web_redirect(repo)
        except Exception as web_exc:
            logger.debug("Web-redirect fallback for %s also failed: %s",
                         repo, web_exc)
            raise api_exc


# =============================================================================
# Downloading + verifying the release image
# =============================================================================

def _cache_root() -> Path:
    """Directory the downloaded release images live in.

    Deliberately not a Qt path: this module is imported by tests that never
    construct a QApplication.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "OpenLIFU" / "firmware-cache"


def _cached_image_path(side: str, tag: str) -> Path:
    """Where the signed image for ``side`` at ``tag`` is cached.

    Keyed by version so a new release lands beside the old one instead of
    overwriting an image an operator may be mid-way through installing.
    """
    return _cache_root() / side / tag / FIRMWARE_ASSET_NAME[side]


def _verify_signed_image(path: Path, expected_version: str) -> None:
    """Raise unless ``path`` is a signed image for exactly ``expected_version``.

    Both repos publish SBSFU ('SFU1') signed images whose header carries the
    firmware version, so this is a real check that the bytes we fetched are
    the firmware we asked for -- not an error page, not a stale artifact, not
    a different release. The bootloader verifies the cryptographic signature
    independently at flash/boot time; this is the app-side gate on what it
    will even offer to install.
    """
    data = path.read_bytes()
    if data[:4] != b"SFU1":
        raise RuntimeError("downloaded file is not a signed firmware image")
    from openlifu_sdk.io.LIFUCrypto import parse_signed_image

    found = parse_signed_image(str(path)).fw_version_str
    if parse_firmware_version(found) != parse_firmware_version(expected_version):
        raise RuntimeError(
            f"downloaded image reports v{found}, expected v{expected_version}")


def _download_release_image(side: str, repo: str, tag: str) -> Path:
    """Fetch and verify the signed image for ``tag``. Returns its cached path.

    Raises on any failure; the caller degrades to the bundled image. The
    download streams to a temporary file and is only moved into place after
    verification, so a cached path always refers to a usable image (an
    interrupted download can never be mistaken for a complete one).
    """
    import requests

    target = _cached_image_path(side, tag)
    if target.is_file():
        try:
            _verify_signed_image(target, tag)
            return target
        except Exception as e:  # noqa: BLE001 - re-fetch a bad cache entry
            logger.warning("Cached %s image %s failed verification (%s); "
                           "re-downloading", side, target, e)
            target.unlink(missing_ok=True)

    url = GITHUB_ASSET_URL.format(repo=repo, tag=tag,
                                  asset=FIRMWARE_ASSET_NAME[side])
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    tmp = Path(tmp_name)
    try:
        with requests.get(url, stream=True,
                          timeout=IMAGE_DOWNLOAD_TIMEOUT_S) as resp:
            resp.raise_for_status()
            written = 0
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    written += len(chunk)
                    if written > MAX_FIRMWARE_IMAGE_BYTES:
                        raise RuntimeError("release asset is implausibly large")
                    fh.write(chunk)
        _verify_signed_image(tmp, tag)
        # Atomic-ish publish: verified bytes, then move under the real name.
        shutil.move(str(tmp), str(target))
        logger.info("Downloaded %s firmware v%s to %s", side, tag, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


def _resolve_release_image(side: str, repo: str, tag: str) -> str | None:
    """Path to the image to offer for ``tag``, or None to use the bundle.

    Returns None (no error) when the published release isn't newer than the
    bundled image -- there is nothing to suggest, so nothing to download.
    """
    packaged = _PACKAGED_LOOKUP[side]()
    parsed_pkg = parse_firmware_version(packaged)
    parsed_tag = parse_firmware_version(tag)
    if parsed_pkg is not None and parsed_tag is not None and parsed_tag <= parsed_pkg:
        logger.debug("Published %s release v%s is not newer than the bundled "
                     "v%s; keeping the bundled image", side, tag, packaged)
        return None
    return str(_download_release_image(side, repo, tag))


def _poll_one(side: str) -> ReleaseInfo:
    """Poll a single repo, fetch its image if newer, fold both into the cache."""
    with _lock:
        info = _state[side]
    now = time.monotonic()
    try:
        version = _fetch_latest_tag(info.repo)
        image = _resolve_release_image(side, info.repo, version)
    except Exception as e:  # noqa: BLE001 - every failure mode is non-fatal here
        reason, is_network = _describe_failure(e)
        logger.warning("%s firmware release check failed (%s); using the "
                       "bundled image", side, reason)
        # Keep the last good version/image: a transient drop shouldn't throw
        # away a release we already downloaded and verified this session.
        updated = replace(info, error=reason, network_error=is_network,
                          attempted_at=now)
    else:
        if image:
            logger.info("Offering %s firmware v%s from GitHub", side, version)
        updated = replace(info, version=version, image_path=image, error=None,
                          network_error=False,
                          attempted_at=now, succeeded_at=now)
    with _lock:
        _state[side] = updated
    return updated


def refresh_release_versions() -> bool:
    """Poll both firmware repos. Blocking -- run me on a worker thread.

    Returns True if every repo answered (and, where newer, its image was
    downloaded and verified). A False return means at least one side fell
    back to its bundled image and :func:`release_check_notice` now has
    something to say.
    """
    # Poll every side before reducing: a short-circuiting ``all()`` would
    # skip the transmitter whenever the console failed first, leaving it
    # permanently "never attempted" (and its failure unreported).
    results = [_poll_one(side) for side in (CONSOLE, TRANSMITTER)]
    return all(info.error is None for info in results)


def needs_refresh() -> bool:
    """True if a poll would actually do something useful right now.

    Used by the on-connect hook so repeated connect/disconnect cycles don't
    hammer the API: a fresh success is good for ``RELEASE_CACHE_TTL_S`` and a
    failure is not retried for ``RELEASE_RETRY_AFTER_ERROR_S``.
    """
    now = time.monotonic()
    with _lock:
        infos = list(_state.values())
    for info in infos:
        if info.attempted_at is None:
            return True
        if info.error is not None:
            if now - info.attempted_at >= RELEASE_RETRY_AFTER_ERROR_S:
                return True
        elif now - (info.succeeded_at or 0.0) >= RELEASE_CACHE_TTL_S:
            return True
    return False


# =============================================================================
# Public: what to install, and whether the check is healthy
# =============================================================================

def release_version(side: str) -> str | None:
    """Latest release tag GitHub reported for ``side``, or None if never read."""
    with _lock:
        return _state[side].version


def release_image_path(side: str) -> str | None:
    """Verified downloaded image to offer for ``side``, or None for the bundle.

    This is the whole point of the module: when it returns a path, the
    Settings page uses it as the firmware to install and the update button
    naturally reads "Upgrade Firmware <device> -> <release>".
    """
    with _lock:
        path = _state[side].image_path
    # The cache lives outside the app; a cleaner could have removed it since
    # the download. Fall back rather than hand the updater a missing file.
    if path and os.path.isfile(path):
        return path
    return None


def latest_fw_version(side: str) -> str | None:
    """Version of the firmware that would actually be installed for ``side``.

    That is the downloaded release when one is being offered, otherwise the
    SDK-bundled image. Returns ``None`` only if neither could be read --
    callers treat that as "unknown" and skip the update advisory.
    """
    if release_image_path(side) is not None:
        return release_version(side)
    return _PACKAGED_LOOKUP[side]()


def latest_console_fw_version() -> str | None:
    """Version of the console firmware currently being offered."""
    return latest_fw_version(CONSOLE)


def latest_transmitter_fw_version() -> str | None:
    """Version of the transmitter firmware currently being offered."""
    return latest_fw_version(TRANSMITTER)


def is_from_github(side: str) -> bool:
    """True when the firmware being offered for ``side`` came from a release."""
    return release_image_path(side) is not None


def _sides(side: str | None) -> tuple[str, ...]:
    return (side,) if side else (CONSOLE, TRANSMITTER)


def _degraded(side: str | None = None) -> list[tuple[str, ReleaseInfo]]:
    """Sides whose check failed *and* that therefore fell back to the bundle.

    A side that failed its latest re-check but still has a verified release
    image on disk is NOT degraded: the operator is being offered the newest
    firmware we know of, so telling them we're on the backup would be a lie.
    """
    wanted = _sides(side)
    with _lock:
        infos = [(s, info) for s, info in _state.items()
                 if s in wanted and info.error is not None]
    # release_image_path takes the lock, so resolve it outside the block.
    return [(s, info) for s, info in infos if release_image_path(s) is None]


def using_local_backup(side: str | None = None) -> bool:
    """True when a failed check has us falling back to the bundled image.

    A successful check that simply found nothing newer is not "using the
    backup" in any sense worth reporting: the bundled image *is* current.
    """
    return bool(_degraded(side))


def release_check_notice(side: str | None = None) -> str:
    """Short operator-facing line about a degraded check, or "".

    Deliberately short and concrete -- it renders inline on the firmware
    cards. The per-repo diagnosis lives in :func:`release_check_detail`.
    """
    failed = _degraded(side)
    if not failed:
        return ""
    return ("Unable to reach GitHub — using local backup."
            if any(info.network_error for _, info in failed)
            else "Couldn't check GitHub — using local backup.")


def release_check_detail(side: str | None = None) -> str:
    """Full per-repo reason the check is degraded, for a tooltip. "" if healthy."""
    failed = _degraded(side)
    if not failed:
        return ""
    details = "\n".join(f"  • {s}: {info.error}" for s, info in failed)
    return (f"{release_check_notice(side)}\n\n{details}\n\n"
            f"The firmware included with the installed SDK will be used "
            f"instead of the newest published release.")


def release_check_is_connectivity(side: str | None = None) -> bool:
    """True when the degraded check is specifically a network problem.

    Lets the UI say "unable to reach GitHub" only when that's actually true,
    rather than when GitHub answered but had nothing usable to give us.
    """
    return any(info.network_error for _, info in _degraded(side))


def release_check_pending() -> bool:
    """True until the first poll of every repo has been attempted."""
    with _lock:
        return any(info.attempted_at is None for info in _state.values())


def release_status_lines() -> list[str]:
    """Report lines naming the firmware each device would be updated to.

    Folded into the connector's firmware status tooltip so an operator can
    tell "you're current" apart from "we couldn't check".
    """
    lines: list[str] = []
    for side, label in ((CONSOLE, "console"), (TRANSMITTER, "transmitter")):
        version = latest_fw_version(side) or "?"
        with _lock:
            info = _state[side]
        if is_from_github(side):
            # A kept-from-earlier download is still a real GitHub release,
            # but say so when the most recent re-check didn't get through.
            source = ("GitHub release" if info.error is None
                      else "GitHub release; last check failed")
        elif info.attempted_at is None:
            source = "SDK-included; GitHub check in progress"
        elif info.error is not None:
            source = ("SDK-included backup; GitHub unreachable"
                      if info.network_error
                      else "SDK-included backup; GitHub check failed")
        else:
            # GitHub answered; its latest release isn't newer than the image
            # the SDK already bundles, so there's nothing to download.
            source = "SDK-included, up to date"
        lines.append(f"Update to {label}: v{version} ({source})")
    return lines


__all__ = [
    "CONSOLE",
    "CONSOLE_FW_REPO",
    "FIRMWARE_ASSET_NAME",
    "RELEASE_POLL_TIMEOUT_S",
    "ReleaseInfo",
    "TRANSMITTER",
    "TRANSMITTER_FW_REPO",
    "is_from_github",
    "latest_console_fw_version",
    "latest_fw_version",
    "latest_transmitter_fw_version",
    "needs_refresh",
    "refresh_release_versions",
    "release_check_detail",
    "release_check_is_connectivity",
    "release_check_notice",
    "release_check_pending",
    "release_image_path",
    "release_status_lines",
    "release_version",
    "using_local_backup",
]
