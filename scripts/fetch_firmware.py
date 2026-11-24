"""Refresh firmware/ from the pinned official firmware releases.

The app installs the images in ``firmware/`` (lifu/firmware_images.py). The
application and bootloader-updater images come from the release tags pinned
in ``PINS`` below, which must be stable releases (no ``-rc.N``). Moving to new
firmware is a reviewed change to ``PINS``, not whatever was released last:

- openlifu-console-fw: signed app and production image
- openlifu-transmitter-fw: signed app and production image
- open-lifu-console-bl-updater / open-lifu-transmitter-bl-updater: the
  signed bootloader updaters

A release that publishes only ``-production.hex`` is converted to the flat
``.bin`` the SDK writes (from 0x08000000, gaps filled with 0xFF). Every
download is checked against the SHA-256 digest GitHub reports for the asset.
The legacy updaters and slave DFU stubs have no releases and stay as
committed (from openlifu-sdk 2.1.0-rc.3).

``firmware/manifest.json`` records where every image came from (repository,
tag, asset, SHA-256); tests/test_firmware_images.py checks the folder
against it.

Usage::

    python scripts/fetch_firmware.py           # refresh firmware/
    python scripts/fetch_firmware.py --check   # report only; exit 1 if stale

CI runs it before PyInstaller, so a release build ships exactly the pinned
releases. ``--check`` also reports a newer stable release of a pinned
repository. ``GH_TOKEN`` / ``GITHUB_TOKEN`` is used if set (rate limits).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

OWNER = "OpenwaterHealth"
ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = ROOT / "firmware"
MANIFEST = FIRMWARE_DIR / "manifest.json"
FLASH_BASE = 0x08000000

# The release each repository's images come from. Stable tags only.
PINS = {
    "openlifu-console-fw": "1.3.1",
    "openlifu-transmitter-fw": "2.1.0",
    "open-lifu-console-bl-updater": "1.0.0",
    "open-lifu-transmitter-bl-updater": "1.0.0",
}

# (repository, file in firmware/, asset names to try in order)
SOURCES = (
    ("openlifu-console-fw", "openlifu-console-fw-signed.bin",
     ("openlifu-console-fw-signed.bin",)),
    ("openlifu-console-fw", "openlifu-console-fw-production.bin",
     ("openlifu-console-fw-production.bin", "openlifu-console-fw-production.hex")),
    ("openlifu-transmitter-fw", "openlifu-transmitter-fw-signed.bin",
     ("openlifu-transmitter-fw-signed.bin",)),
    ("openlifu-transmitter-fw", "openlifu-transmitter-fw-production.bin",
     ("openlifu-transmitter-fw-production.bin", "openlifu-transmitter-fw-production.hex")),
    ("open-lifu-console-bl-updater", "open-lifu-console-bl-updater-signed.bin",
     ("open-lifu-console-bl-updater-signed.bin",)),
    ("open-lifu-transmitter-bl-updater", "open-lifu-transmitter-bl-updater-signed.bin",
     ("open-lifu-transmitter-bl-updater-signed.bin",)),
)


def _request(url: str, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "openlifu-test-app"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def latest_release(repo: str) -> dict:
    """The latest stable release (GitHub's /releases/latest skips
    pre-releases and drafts)."""
    url = f"https://api.github.com/repos/{OWNER}/{repo}/releases/latest"
    return json.loads(_request(url, "application/vnd.github+json"))


def pinned_release(repo: str) -> dict:
    """The release ``PINS`` names for *repo*; it must be a published, stable
    release."""
    tag = PINS[repo]
    if "-rc." in tag:
        raise RuntimeError(f"{repo}: pin {tag} is a release candidate")
    url = f"https://api.github.com/repos/{OWNER}/{repo}/releases/tags/{tag}"
    rel = json.loads(_request(url, "application/vnd.github+json"))
    if rel.get("draft") or rel.get("prerelease"):
        raise RuntimeError(f"{repo} {tag}: not a published stable release")
    return rel


def hex_to_bin(text: str) -> bytes:
    """Flatten an Intel HEX image to bytes from FLASH_BASE, gaps 0xFF."""
    mem: dict[int, int] = {}
    base = 0
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"HEX line {n}: no start code")
        rec = bytes.fromhex(line[1:])
        length, addr, kind = rec[0], (rec[1] << 8) | rec[2], rec[3]
        data = rec[4:4 + length]
        if len(data) != length or (sum(rec) & 0xFF) != 0:
            raise ValueError(f"HEX line {n}: bad length or checksum")
        if kind == 0:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif kind == 1:
            break
        elif kind == 2:
            base = int.from_bytes(data, "big") << 4
        elif kind == 4:
            base = int.from_bytes(data, "big") << 16
    if not mem:
        raise ValueError("HEX file holds no data")
    lo, hi = min(mem), max(mem)
    if lo != FLASH_BASE:
        raise ValueError(f"HEX image starts at {lo:#x}, expected {FLASH_BASE:#x}")
    return bytes(mem.get(a, 0xFF) for a in range(lo, hi + 1))


def fetch(repo: str, release: dict, asset_names: tuple[str, ...]) -> tuple[str, bytes]:
    """Download the first asset of *asset_names* in *release*, verified
    against its digest. Returns (asset name, image bytes as installed)."""
    assets = {a["name"]: a for a in release.get("assets", [])}
    for name in asset_names:
        asset = assets.get(name)
        if asset is None:
            continue
        data = _request(asset["browser_download_url"], "application/octet-stream")
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            got = hashlib.sha256(data).hexdigest()
            if got != digest.split(":", 1)[1]:
                raise RuntimeError(f"{repo} {release['tag_name']} {name}: SHA-256 "
                                   f"{got} does not match the release ({digest})")
        if name.endswith(".hex"):
            data = hex_to_bin(data.decode("ascii"))
        return name, data
    raise RuntimeError(f"{repo} {release['tag_name']}: none of {asset_names} "
                       "is attached to the release")


def load_manifest() -> dict:
    if MANIFEST.is_file():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"files": {}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report what would change and any newer stable release; "
                         "exit 1 if firmware/ does not match the pins")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    files = manifest.setdefault("files", {})
    releases: dict[str, dict] = {}
    updates: dict[str, tuple[dict, bytes]] = {}
    for repo, target, asset_names in SOURCES:
        rel = releases.get(repo) or releases.setdefault(repo, pinned_release(repo))
        asset, data = fetch(repo, rel, asset_names)
        entry = {"repo": f"{OWNER}/{repo}", "tag": rel["tag_name"],
                 "asset": asset, "sha256": hashlib.sha256(data).hexdigest()}
        if asset.endswith(".hex"):
            entry["converted"] = f"Intel HEX -> bin from {FLASH_BASE:#010x}"
        current = FIRMWARE_DIR / target
        have = hashlib.sha256(current.read_bytes()).hexdigest() if current.is_file() else None
        state = "up to date" if have == entry["sha256"] else "UPDATE"
        print(f"{target:48s} {repo} {rel['tag_name']:8s} {state}")
        if have != entry["sha256"] or files.get(target) != entry:
            updates[target] = (entry, data)

    if args.check:
        for repo in PINS:
            newest = latest_release(repo)["tag_name"]
            if newest != PINS[repo]:
                print(f"note: {repo} has a newer stable release {newest} (pinned {PINS[repo]})")
        return 1 if updates else 0
    for target, (entry, data) in updates.items():
        fd, tmp = tempfile.mkstemp(dir=FIRMWARE_DIR, prefix=".fetch-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, FIRMWARE_DIR / target)
        files[target] = entry
    manifest["files"] = dict(sorted(files.items()))
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{len(updates)} file(s) updated; manifest: {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
