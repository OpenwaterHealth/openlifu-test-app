"""The app ships the firmware images the SDK stopped carrying (openlifu-sdk
2.1.0-rc.4, 87cd427) and passes them to every update path. The images come
from the firmware releases pinned in scripts/fetch_firmware.py (PINS),
recorded in firmware/manifest.json."""
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

from lifu import firmware_images as fi
from lifu.lifu_constants import (
    packaged_console_fw_version,
    packaged_transmitter_fw_version,
    parse_firmware_version,
    validate_firmware_version_pins,
)

ALL_IMAGES = (
    fi.CONSOLE_SIGNED_APP, fi.CONSOLE_PRODUCTION, fi.CONSOLE_LEGACY_UPDATER,
    fi.TRANSMITTER_SIGNED_APP, fi.TRANSMITTER_PRODUCTION,
    fi.TRANSMITTER_LEGACY_UPDATER, fi.TRANSMITTER_DFU_STUB,
    fi.TRANSMITTER_DFU_STUB_SIGNED,
    fi.CONSOLE_BL_UPDATER, fi.TRANSMITTER_BL_UPDATER,
)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "firmware" / "manifest.json").read_text(encoding="utf-8"))["files"]

# Refreshed from the pinned stable release of these repositories.
RELEASED = {
    fi.CONSOLE_SIGNED_APP: "openlifu-console-fw",
    fi.CONSOLE_PRODUCTION: "openlifu-console-fw",
    fi.TRANSMITTER_SIGNED_APP: "openlifu-transmitter-fw",
    fi.TRANSMITTER_PRODUCTION: "openlifu-transmitter-fw",
    fi.CONSOLE_BL_UPDATER: "open-lifu-console-bl-updater",
    fi.TRANSMITTER_BL_UPDATER: "open-lifu-transmitter-bl-updater",
}


def _load_fetch_script():
    spec = importlib.util.spec_from_file_location(
        "fetch_firmware", ROOT / "scripts" / "fetch_firmware.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ALL_IMAGES)
def test_every_image_ships_with_the_app(name):
    path = fi.image(name)
    assert path.parent == fi.firmware_dir()
    assert path.stat().st_size > 0


def test_manifest_lists_exactly_the_shipped_images():
    on_disk = {p.name for p in fi.firmware_dir().iterdir()
               if p.is_file() and p.name != "manifest.json"}
    assert on_disk == set(MANIFEST) == set(ALL_IMAGES)


@pytest.mark.parametrize("name", ALL_IMAGES)
def test_every_image_matches_its_manifest_entry(name):
    entry = MANIFEST[name]
    assert hashlib.sha256(fi.image(name).read_bytes()).hexdigest() == entry["sha256"]
    if name in RELEASED:
        assert entry["repo"] == f"OpenwaterHealth/{RELEASED[name]}"
        assert "-rc." not in entry["tag"]          # official releases only


def test_the_shipped_images_come_from_the_pinned_releases():
    """firmware/ holds exactly the releases PINS names: moving to new
    firmware is a change to PINS plus a refetch, reviewed together."""
    pins = _load_fetch_script().PINS
    for name, repo in RELEASED.items():
        assert MANIFEST[name]["tag"] == pins[repo], name
    assert all("-rc." not in tag for tag in pins.values())


def test_fetch_script_covers_every_released_image():
    mod = _load_fetch_script()
    assert {(target, repo) for repo, target, _ in mod.SOURCES} == {
        (name, repo) for name, repo in RELEASED.items()}


def test_signed_app_version_matches_its_release_tag():
    for name in (fi.CONSOLE_SIGNED_APP, fi.TRANSMITTER_SIGNED_APP):
        assert parse_firmware_version(fi.shipped_version(name)) ==             parse_firmware_version(MANIFEST[name]["tag"])


@pytest.mark.parametrize("name", [fi.CONSOLE_BL_UPDATER, fi.TRANSMITTER_BL_UPDATER])
def test_bl_updaters_are_signed_images(name):
    assert fi.image(name).read_bytes()[:4] == b"SFU1"


def test_hex_to_bin_fills_gaps_from_flash_base():
    mod = _load_fetch_script()

    def rec(addr, kind, data):
        body = bytes([len(data), addr >> 8, addr & 0xFF, kind]) + data
        return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()

    text = "\n".join([rec(0, 4, b"\x08\x00"), rec(0x0000, 0, b"\x01\x02"),
                      rec(0x0004, 0, b"\x05"), rec(0, 1, b"")])
    assert mod.hex_to_bin(text) == b"\x01\x02\xff\xff\x05"
    with pytest.raises(ValueError):                    # not at 0x08000000
        mod.hex_to_bin(rec(0, 0, b"\x01"))
    bad = rec(0x0000, 0, b"\x01\x02")
    with pytest.raises(ValueError):                    # bad checksum
        mod.hex_to_bin(text.replace(bad, bad[:-2] + "00"))

def test_spec_bundles_the_firmware_folder():
    spec = (Path(__file__).resolve().parent.parent / "OpenLIFU-TestApp.spec").read_text()
    assert '"firmware",' in spec


def test_missing_image_raises_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "firmware_dir", lambda: tmp_path)
    with pytest.raises(FileNotFoundError):
        fi.image(fi.CONSOLE_SIGNED_APP)


def test_signed_images_carry_their_version_in_the_header():
    assert fi.image(fi.CONSOLE_SIGNED_APP).read_bytes()[:4] == b"SFU1"
    assert fi.image(fi.TRANSMITTER_SIGNED_APP).read_bytes()[:4] == b"SFU1"
    assert parse_firmware_version(packaged_console_fw_version()) is not None
    assert parse_firmware_version(packaged_transmitter_fw_version()) is not None


def test_startup_pin_check_passes_on_the_shipped_images():
    validate_firmware_version_pins()


def test_startup_pin_check_fails_without_the_images(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "firmware_dir", lambda: tmp_path)
    fi.shipped_version.cache_clear()
    try:
        with pytest.raises(ValueError, match="shipped with the app"):
            validate_firmware_version_pins()
    finally:
        fi.shipped_version.cache_clear()


@pytest.fixture
def sdk_bl_updater(monkeypatch):
    """Pretend the installed SDK does (True) or does not (False) take
    ``bl_updater``."""
    def set_(value):
        monkeypatch.setattr(fi, "sdk_takes_bl_updater", lambda method: value)
    return set_


def test_console_update_gets_every_route_image(sdk_bl_updater):
    sdk_bl_updater(True)
    kw = fi.console_update_images()
    assert kw == {
        "production_image": str(fi.image(fi.CONSOLE_PRODUCTION)),
        "updater_bin": str(fi.image(fi.CONSOLE_LEGACY_UPDATER)),
        "signed_app": str(fi.image(fi.CONSOLE_SIGNED_APP)),
        "bl_updater": str(fi.image(fi.CONSOLE_BL_UPDATER)),
    }
    assert fi.console_update_images("browsed.bin")["signed_app"] == "browsed.bin"


def test_transmitter_master_update_gets_every_route_image(sdk_bl_updater):
    sdk_bl_updater(True)
    kw = fi.transmitter_update_images()
    assert kw == {
        "updater_bin": str(fi.image(fi.TRANSMITTER_LEGACY_UPDATER)),
        "signed_app": str(fi.image(fi.TRANSMITTER_SIGNED_APP)),
        "production_image": str(fi.image(fi.TRANSMITTER_PRODUCTION)),
        "bl_updater": str(fi.image(fi.TRANSMITTER_BL_UPDATER)),
    }
    kw = fi.transmitter_update_images("app.bin", production_image="prod.bin")
    assert kw["signed_app"] == "app.bin" and kw["production_image"] == "prod.bin"


def test_slave_stub_follows_the_route(sdk_bl_updater):
    sdk_bl_updater(True)
    legacy = fi.transmitter_slave_images(legacy=True)
    secure = fi.transmitter_slave_images(legacy=False)
    assert legacy["stub_bin"] == str(fi.image(fi.TRANSMITTER_DFU_STUB))
    assert secure["stub_bin"] == str(fi.image(fi.TRANSMITTER_DFU_STUB_SIGNED))
    # The route goes to the SDK with its stub, so it cannot pick the other.
    assert legacy["legacy"] is True and secure["legacy"] is False
    for kw in (legacy, secure):
        assert kw["production_image"] == str(fi.image(fi.TRANSMITTER_PRODUCTION))
        assert kw["signed_app"] == str(fi.image(fi.TRANSMITTER_SIGNED_APP))
        assert kw["bl_updater"] == str(fi.image(fi.TRANSMITTER_BL_UPDATER))


def test_unknown_slave_route_passes_no_stub_and_no_route(sdk_bl_updater):
    # Version unreadable: the SDK detects the route, and both stub routes
    # refuse a missing stub before writing (never the wrong stub).
    sdk_bl_updater(True)
    kw = fi.transmitter_slave_images(legacy=None)
    assert "stub_bin" not in kw and "legacy" not in kw
    assert kw["signed_app"] == str(fi.image(fi.TRANSMITTER_SIGNED_APP))


def test_bl_updater_left_out_for_an_sdk_without_it(sdk_bl_updater):
    # openlifu-sdk <= 2.1.0-rc.3 has no bl_updater argument (TypeError).
    sdk_bl_updater(False)
    assert "bl_updater" not in fi.console_update_images()
    assert "bl_updater" not in fi.transmitter_update_images()
    assert "bl_updater" not in fi.transmitter_slave_images(legacy=False)


def test_sdk_bl_updater_detection_reads_the_signature(monkeypatch):
    from openlifu_sdk.io import LIFUFirmwareUpdate as mod

    class Old:
        def update(self, *, signed_app=None): ...

    class New:
        def update(self, *, signed_app=None, bl_updater=None): ...

    fi.sdk_takes_bl_updater.cache_clear()
    try:
        monkeypatch.setattr(mod, "LIFUFirmwareUpdate", Old, raising=False)
        assert not fi.sdk_takes_bl_updater("LIFUFirmwareUpdate.update")
        fi.sdk_takes_bl_updater.cache_clear()
        monkeypatch.setattr(mod, "LIFUFirmwareUpdate", New, raising=False)
        assert fi.sdk_takes_bl_updater("LIFUFirmwareUpdate.update")
        assert not fi.sdk_takes_bl_updater("NoSuchClass.update")
    finally:
        fi.sdk_takes_bl_updater.cache_clear()


def test_installed_sdk_accepts_every_argument_the_app_passes():
    from openlifu_sdk.io.LIFUFirmwareUpdate import (
        LIFUFirmwareUpdate,
        LIFUTransmitterFirmwareUpdate,
    )

    def params(fn):
        return set(inspect.signature(fn).parameters)

    assert set(fi.console_update_images()) <= params(LIFUFirmwareUpdate.update)
    assert set(fi.transmitter_update_images()) <= params(
        LIFUTransmitterFirmwareUpdate.update)
    assert set(fi.transmitter_slave_images(legacy=True)) <= params(
        LIFUTransmitterFirmwareUpdate.update_slave)
