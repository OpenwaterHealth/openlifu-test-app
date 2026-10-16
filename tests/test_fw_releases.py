"""Offering a downloaded release must never make the app worse when it fails.

Every test here runs offline: ``requests.get`` is patched, so the suite
covers the failure modes that matter on a bench (no DNS, proxy, timeout,
shared-IP rate limit, a download that doesn't verify) without touching the
network. Two invariants are under test throughout:

* a failed check falls back to the SDK-bundled image -- the behaviour that
  predates the GitHub check -- and says it is using the local backup;
* the version reported as "latest" is always the version of a file that is
  on disk and installable right now, never a number we merely saw.
"""

import pytest

requests = pytest.importorskip("requests")

from lifu import lifu_fw_releases as fr


PACKAGED_CONSOLE = fr._PACKAGED_LOOKUP[fr.CONSOLE]()
PACKAGED_TX = fr._PACKAGED_LOOKUP[fr.TRANSMITTER]()

# The autouse fixture below stubs image verification (it needs real firmware
# bytes, which the download tests don't have). Grab the genuine article here
# so the verification tests at the bottom can still exercise it.
REAL_VERIFY = fr._verify_signed_image


def bump(version, by=(1, 0, 0)):
    """A version comfortably newer than ``version``, so a download is wanted."""
    major, minor, patch = fr.parse_firmware_version(version)
    return f"{major + by[0]}.{minor + by[1]}.{patch + by[2]}"


NEWER_CONSOLE = bump(PACKAGED_CONSOLE)
NEWER_TX = bump(PACKAGED_TX)


class FakeResponse:
    """Stands in for both the JSON probes and the streamed image download."""

    def __init__(self, status_code, payload=None, headers=None, body=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self._body = body

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def clean_release_cache(monkeypatch, tmp_path):
    """Pristine module state + a throwaway image cache for every test.

    The release cache is global (one app, one set of firmware repos) and the
    image cache is on disk, so without this tests would leak freshness,
    backoff and downloaded-file state into each other.
    """
    monkeypatch.setattr(fr, "_state", {
        fr.CONSOLE: fr.ReleaseInfo(repo=fr.CONSOLE_FW_REPO),
        fr.TRANSMITTER: fr.ReleaseInfo(repo=fr.TRANSMITTER_FW_REPO),
    })
    monkeypatch.setattr(fr, "_cache_root", lambda: tmp_path / "firmware-cache")
    # Signature/header parsing is the SDK's job and needs real firmware
    # bytes; tests drive it through a stub and cover it separately below.
    monkeypatch.setattr(fr, "_verify_signed_image",
                        lambda path, expected: None)
    yield


def patch_get(monkeypatch, fn):
    monkeypatch.setattr(requests, "get", fn)


def github_ok(console=None, transmitter=None, image=b"SFU1-image-bytes"):
    """Both probes answer, and both image downloads succeed."""
    console = console or NEWER_CONSOLE
    transmitter = transmitter or NEWER_TX

    def _get(url, **kwargs):
        tag = console if "console" in url else transmitter
        if "/releases/download/" in url:
            return FakeResponse(200, body=image)
        return FakeResponse(200, {"tag_name": "v" + tag})
    return _get


def connection_error(url, **kwargs):
    raise requests.exceptions.ConnectionError("getaddrinfo failed")


# ---------------------------------------------------------------- happy path

def test_newer_release_is_downloaded_and_offered(monkeypatch):
    patch_get(monkeypatch, github_ok())

    assert fr.release_check_pending() is True
    assert fr.refresh_release_versions() is True

    # The offered version is the release, and it is backed by a real file.
    assert fr.latest_console_fw_version() == NEWER_CONSOLE
    assert fr.latest_transmitter_fw_version() == NEWER_TX
    image = fr.release_image_path(fr.CONSOLE)
    assert image is not None
    assert image.endswith(fr.FIRMWARE_ASSET_NAME[fr.CONSOLE])
    assert fr.is_from_github(fr.CONSOLE) is True

    assert fr.release_check_notice() == ""
    assert fr.using_local_backup() is False
    assert fr.release_check_pending() is False
    # A fresh success must not be re-polled on the next device connect.
    assert fr.needs_refresh() is False


def test_nothing_is_downloaded_when_the_bundle_is_current(monkeypatch):
    """A release that isn't newer is not a suggestion -- and not a download."""
    downloads = []

    def _get(url, **kwargs):
        if "/releases/download/" in url:
            downloads.append(url)
            return FakeResponse(200, body=b"SFU1")
        tag = PACKAGED_CONSOLE if "console" in url else PACKAGED_TX
        return FakeResponse(200, {"tag_name": "v" + tag})

    patch_get(monkeypatch, _get)
    assert fr.refresh_release_versions() is True

    assert downloads == []
    assert fr.release_image_path(fr.CONSOLE) is None
    assert fr.is_from_github(fr.CONSOLE) is False
    # Falls through to the bundled image, with nothing to report.
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.release_check_notice() == ""
    assert fr.using_local_backup() is False


def test_older_release_never_downgrades_the_offer(monkeypatch):
    """The SDK can bundle a build ahead of the public release."""
    patch_get(monkeypatch, github_ok(console="0.0.1", transmitter="0.0.1"))
    fr.refresh_release_versions()

    assert fr.release_image_path(fr.CONSOLE) is None
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.release_check_notice() == ""


# ------------------------------------------------------- falling back safely

def test_unreachable_github_uses_the_local_backup(monkeypatch):
    patch_get(monkeypatch, connection_error)

    assert fr.refresh_release_versions() is False

    assert fr.release_image_path(fr.CONSOLE) is None
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.latest_transmitter_fw_version() == PACKAGED_TX
    assert fr.using_local_backup() is True
    assert fr.release_check_is_connectivity() is True
    assert fr.release_check_notice() == "Unable to reach GitHub — using local backup."
    # Both sides are diagnosed: a short-circuit here once hid the transmitter.
    detail = fr.release_check_detail()
    assert "console:" in detail and "transmitter:" in detail
    # Backoff: a failure isn't retried on every reconnect.
    assert fr.needs_refresh() is False


def test_timeout_is_reported_as_a_connectivity_problem(monkeypatch):
    def _timeout(url, **kwargs):
        raise requests.exceptions.Timeout()

    patch_get(monkeypatch, _timeout)
    fr.refresh_release_versions()

    assert "timed out" in fr.release_check_detail()
    assert fr.release_check_is_connectivity() is True
    assert fr.release_check_notice().startswith("Unable to reach GitHub")


def test_missing_http_client_is_not_fatal(monkeypatch):
    """A build without ``requests`` degrades instead of raising."""
    import sys

    monkeypatch.setitem(sys.modules, "requests", None)
    assert fr.refresh_release_versions() is False
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.using_local_backup() is True


def test_last_good_download_survives_a_later_failure(monkeypatch):
    patch_get(monkeypatch, github_ok())
    fr.refresh_release_versions()

    patch_get(monkeypatch, connection_error)
    fr.refresh_release_versions()

    # The verified image is still on disk, so keep offering it.
    assert fr.release_image_path(fr.CONSOLE) is not None
    assert fr.latest_console_fw_version() == NEWER_CONSOLE
    # ...and don't claim we're on the backup when we aren't. The failed
    # re-check is still visible in the diagnostic report.
    assert fr.using_local_backup() is False
    assert fr.release_check_notice() == ""
    assert "last check failed" in " ".join(fr.release_status_lines())


def test_one_repo_failing_does_not_warn_on_the_other_card(monkeypatch):
    """Notices are per-device: a console outage mustn't flag the transmitter."""
    def _get(url, **kwargs):
        if "console" in url:
            raise requests.exceptions.ConnectionError("getaddrinfo failed")
        if "/releases/download/" in url:
            return FakeResponse(200, body=b"SFU1-image-bytes")
        return FakeResponse(200, {"tag_name": "v" + NEWER_TX})

    patch_get(monkeypatch, _get)
    fr.refresh_release_versions()

    assert fr.using_local_backup(fr.CONSOLE) is True
    assert fr.release_check_notice(fr.CONSOLE).startswith("Unable to reach GitHub")
    assert "transmitter" not in fr.release_check_detail(fr.CONSOLE)

    assert fr.using_local_backup(fr.TRANSMITTER) is False
    assert fr.release_check_notice(fr.TRANSMITTER) == ""
    assert fr.latest_transmitter_fw_version() == NEWER_TX


def test_a_deleted_cache_file_falls_back_to_the_bundle(monkeypatch):
    """The cache lives outside the app; a cleaner may remove it."""
    patch_get(monkeypatch, github_ok())
    fr.refresh_release_versions()

    import os
    os.remove(fr.release_image_path(fr.CONSOLE))

    assert fr.release_image_path(fr.CONSOLE) is None
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.is_from_github(fr.CONSOLE) is False


# ------------------------------------------- GitHub answered, but unusably

@pytest.mark.parametrize("response, expected", [
    (FakeResponse(403, headers={"X-RateLimit-Remaining": "0"}), "rate limit"),
    (FakeResponse(404), "no public release found"),
    (FakeResponse(500), "HTTP 500"),
    (FakeResponse(200, {"tag_name": "nightly"}), "unparseable release tag"),
])
def test_unusable_api_answers_fall_back_without_claiming_offline(
        monkeypatch, response, expected):
    def _get(url, **kwargs):
        if url.startswith("https://api.github.com"):
            return response
        return FakeResponse(302, headers={"Location": "https://github.com/o/r/releases"})

    patch_get(monkeypatch, _get)
    fr.refresh_release_versions()

    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert expected in fr.release_check_detail()
    # GitHub was reachable -- don't tell the operator they're offline.
    assert fr.release_check_is_connectivity() is False
    assert fr.release_check_notice() == "Couldn't check GitHub — using local backup."
    assert fr.needs_refresh() is False


def test_a_failed_image_download_falls_back_to_the_bundle(monkeypatch):
    """The version probe worked but the asset didn't -- still no half-state."""
    def _get(url, **kwargs):
        if "/releases/download/" in url:
            return FakeResponse(404)
        tag = NEWER_CONSOLE if "console" in url else NEWER_TX
        return FakeResponse(200, {"tag_name": "v" + tag})

    patch_get(monkeypatch, _get)
    assert fr.refresh_release_versions() is False

    assert fr.release_image_path(fr.CONSOLE) is None
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    assert fr.using_local_backup() is True


def test_an_oversized_asset_is_rejected(monkeypatch):
    monkeypatch.setattr(fr, "MAX_FIRMWARE_IMAGE_BYTES", 16)
    patch_get(monkeypatch, github_ok(image=b"x" * 4096))

    assert fr.refresh_release_versions() is False
    assert fr.release_image_path(fr.CONSOLE) is None
    assert "implausibly large" in fr.release_check_detail()
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE


def test_an_unverifiable_image_is_discarded(monkeypatch):
    """Only images that verify as the requested release get offered."""
    def _reject(path, expected):
        raise RuntimeError("downloaded file is not a signed firmware image")

    monkeypatch.setattr(fr, "_verify_signed_image", _reject)
    patch_get(monkeypatch, github_ok(image=b"<html>login</html>"))

    assert fr.refresh_release_versions() is False
    assert fr.release_image_path(fr.CONSOLE) is None
    assert "not a signed firmware image" in fr.release_check_detail()
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE
    # The rejected bytes must not be left behind as a cache entry.
    assert not (fr._cached_image_path(fr.CONSOLE, NEWER_CONSOLE)).exists()


def test_a_cached_image_is_reused_without_redownloading(monkeypatch):
    patch_get(monkeypatch, github_ok())
    fr.refresh_release_versions()
    first = fr.release_image_path(fr.CONSOLE)

    downloads = []

    def _count(url, **kwargs):
        if "/releases/download/" in url:
            downloads.append(url)
        return github_ok()(url, **kwargs)

    patch_get(monkeypatch, _count)
    fr.refresh_release_versions()

    assert downloads == []
    assert fr.release_image_path(fr.CONSOLE) == first


# ------------------------------------------------------- web-redirect path

def test_web_redirect_covers_a_rate_limited_api(monkeypatch):
    """Unauthenticated API calls share 60/hour *per IP*; a lab behind one NAT
    exhausts that, so the un-limited web redirect has to carry the check."""
    seen = {}

    def _get(url, **kwargs):
        if url.startswith("https://api.github.com"):
            return FakeResponse(403, headers={"X-RateLimit-Remaining": "0"})
        if "/releases/download/" in url:
            return FakeResponse(200, body=b"SFU1-image-bytes")
        seen["allow_redirects"] = kwargs.get("allow_redirects")
        tag = NEWER_CONSOLE if "console" in url else NEWER_TX
        return FakeResponse(302, headers={
            "Location": f"https://github.com/o/r/releases/tag/v{tag}"})

    patch_get(monkeypatch, _get)

    assert fr.refresh_release_versions() is True
    assert fr.latest_console_fw_version() == NEWER_CONSOLE
    assert fr.release_check_notice() == ""
    # The fallback reads the Location header; it must not pull down the page.
    assert seen["allow_redirects"] is False


def test_api_reason_is_reported_when_both_paths_fail(monkeypatch):
    def _get(url, **kwargs):
        if url.startswith("https://api.github.com"):
            return FakeResponse(403, headers={"X-RateLimit-Remaining": "0"})
        return FakeResponse(302, headers={"Location": "https://github.com/o/r/releases"})

    patch_get(monkeypatch, _get)
    fr.refresh_release_versions()

    assert "rate limit" in fr.release_check_detail()
    assert fr.latest_console_fw_version() == PACKAGED_CONSOLE


# ------------------------------------------------------- image verification

def test_verify_rejects_a_non_sbsfu_file(tmp_path):
    """An error page saved as .bin must never be treated as firmware."""
    bogus = tmp_path / "fw.bin"
    bogus.write_bytes(b"<html>not firmware</html>")

    with pytest.raises(RuntimeError, match="not a signed firmware image"):
        REAL_VERIFY(bogus, "1.2.7")


def test_verify_accepts_the_sdk_bundled_image_at_its_own_version():
    """Round-trip the real parser against firmware we know is genuine."""
    from openlifu_sdk.util.firmware import get_console_firmware_path

    REAL_VERIFY(get_console_firmware_path(), PACKAGED_CONSOLE)


def test_verify_rejects_a_version_mismatch():
    """A signed image for a different release must not be accepted."""
    from openlifu_sdk.util.firmware import get_console_firmware_path

    with pytest.raises(RuntimeError, match="expected"):
        REAL_VERIFY(get_console_firmware_path(), bump(PACKAGED_CONSOLE))
