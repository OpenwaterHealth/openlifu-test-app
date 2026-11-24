"""The app ships the firmware images the SDK stopped carrying (openlifu-sdk
2.1.0-rc.4, 87cd427) and passes them to every update path."""
import hashlib
import inspect
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

# SHA-256 from the open-lifu-{console,transmitter}-bl-updater 1.0.0 releases.
BL_UPDATER_SHA256 = {
    fi.CONSOLE_BL_UPDATER:
        "b8fcf5a14c328507487779000f28b69b3e2141369c038a23be60b6afce27f793",
    fi.TRANSMITTER_BL_UPDATER:
        "e718a3b4bde7b9f99236558309e077740c22fa152a1d59f0489b40fc6491cdb8",
}


@pytest.mark.parametrize("name", ALL_IMAGES)
def test_every_image_ships_with_the_app(name):
    path = fi.image(name)
    assert path.parent == fi.firmware_dir()
    assert path.stat().st_size > 0


@pytest.mark.parametrize("name", sorted(BL_UPDATER_SHA256))
def test_bl_updaters_are_the_released_images(name):
    data = fi.image(name).read_bytes()
    assert data[:4] == b"SFU1"
    assert hashlib.sha256(data).hexdigest() == BL_UPDATER_SHA256[name]


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
    for kw in (legacy, secure):
        assert kw["production_image"] == str(fi.image(fi.TRANSMITTER_PRODUCTION))
        assert kw["signed_app"] == str(fi.image(fi.TRANSMITTER_SIGNED_APP))
        assert kw["bl_updater"] == str(fi.image(fi.TRANSMITTER_BL_UPDATER))


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
