"""A transmitter without a bootloader must be visible and updatable.

A bare-metal build reports the same version as a release and its firmware
cannot say which it is. The Settings card finds out on request (the SDK reads
the flash through the STM32 ROM loader), remembers it per device, and routes a
bare-metal image through the same loader. No hardware or Qt event loop: the
SDK updater is a fake and the mixin's worker threads run inline.
"""
from __future__ import annotations

import struct

import pytest

from lifu import lifu_settings
from lifu.lifu_settings import SettingsMixin


class Emitter:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class SyncThread:
    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()


class Mutex:
    def lock(self):
        pass

    def unlock(self):
        pass


class Hv:
    def __init__(self):
        self.calls = []

    def turn_12v_off(self):
        self.calls.append("off")

    def turn_12v_on(self):
        self.calls.append("on")


class Tx:
    def __init__(self, version="2.0.9", modules=1):
        self.version = version
        self.modules = modules

    def get_version(self, module=0):
        return self.version

    def get_module_count(self):
        return self.modules


MASTER = "5XhQ3rTzk9"      # device ID of the connected master


class Connector(SettingsMixin):
    def __init__(self, tx=None, bootloader=""):
        tx = tx or Tx()
        self._hvConnected = True
        self._txConnected = True
        self._monitoring_paused = False
        self._fw_update_active = False
        self._dfu_kind = ""
        self._dfu_version = ""
        self._interface_mutex = Mutex()
        self._cached_tx_fw_version = {}
        self._cached_tx_info = [{"module": 0, "firmwareVersion": tx.version,
                                 "deviceId": MASTER}]
        # What an earlier check or update established for this master.
        self._tx_bootloader = {MASTER: bootloader} if bootloader else {}
        self.txBootloaderChanged = Emitter()
        self.interface = type("Interface", (), {"hvcontroller": Hv(), "txdevice": tx})()
        self.fwUpdateStatus = Emitter()
        self.fwUpdateProgress = Emitter()
        self.dfuStateChanged = Emitter()
        self.invalidated = []

    @property
    def dfuState(self):
        return f"{self._dfu_kind} {self._dfu_version}".strip()

    def _invalidate_device_caches(self, side):
        self.invalidated.append(side)
        self._cached_tx_info = None      # as the real one does

    def _set_dfu_state(self, *args):
        pass


class FakeUpdater:
    instances = []
    fail_with = None
    probe_result = ("no-bootloader", "")

    def __init__(self, tx=None, **kwargs):
        self.tx = tx
        self.update_calls = []
        self.slave_calls = []
        FakeUpdater.instances.append(self)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        if FakeUpdater.fail_with:
            raise FakeUpdater.fail_with
        return type("Result", (), {"summary": "Done.", "reboot_required": False,
                                   "bl_version": "1.0.1"})()

    def probe_bootloader(self, progress_callback=None):
        if FakeUpdater.fail_with:
            raise FakeUpdater.fail_with
        progress_callback(65536, 65536, "Reading bootloader region")
        return FakeUpdater.probe_result

    def update_slave(self, module, **kwargs):
        self.slave_calls.append((module, kwargs))
        return type("Result", (), {"summary": "Done.", "reboot_required": False})()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeUpdater.instances = []
    FakeUpdater.fail_with = None
    FakeUpdater.probe_result = ("no-bootloader", "")
    import openlifu_sdk.io.LIFUFirmwareUpdate as fu

    monkeypatch.setattr(fu, "LIFUTransmitterFirmwareUpdate", FakeUpdater)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    monkeypatch.setattr(lifu_settings.time, "sleep", lambda s: None)
    yield


def _vector_table(base, handler_offset=0xB7E8):
    table = [0] * 16
    table[0] = 0x2000C000
    for i in (1, 2, 3, 4, 5, 6, 11, 12, 14, 15):
        table[i] = (base + handler_offset) | 1
    return struct.pack("<16I", *table)


@pytest.fixture
def images(tmp_path):
    """One file of each kind the transmitter card can be handed."""
    bootloader = _vector_table(0x08000000, 0x4000).ljust(0x10000, b"\xFF")
    files = {
        "bare": _vector_table(0x08000000) + b"\xA5" * 70000,
        "slot": _vector_table(0x08010400) + b"\xA5" * 70000,
        "signed": b"SFU1" + b"\x00" * 2000,
        "production": bootloader + b"SFU1" + b"\x00" * 2000,
    }
    paths = {}
    for name, data in files.items():
        path = tmp_path / f"{name}.bin"
        path.write_bytes(data)
        paths[name] = str(path)
    return paths


def statuses(c):
    return [(ok, msg) for _dev, ok, msg in c.fwUpdateStatus.calls]


# ---------------------------------------------------------------- detection

def test_bootloader_is_unknown_until_checked():
    assert Connector().txBootloader() == ""


@pytest.mark.parametrize("found, state, message", [
    (("no-bootloader", ""), "no-bootloader", "No bootloader: the transmitter runs a bare-metal image."),
    (("secure-bl", "1.0.1"), "secure-bl 1.0.1", "Secure bootloader 1.0.1 is installed."),
    (("secure-bl", ""), "secure-bl", "Secure bootloader is installed."),
    (("legacy-bl", "0.0.3"), "legacy-bl 0.0.3", "Legacy bootloader 0.0.3 is installed."),
])
def test_check_reads_the_flash_and_remembers_the_answer(found, state, message):
    FakeUpdater.probe_result = found
    c = Connector()

    c.checkTxBootloader()

    (upd,) = FakeUpdater.instances
    assert upd.tx is c.interface.txdevice and upd.update_calls == []
    assert c.txBootloader() == state
    assert c.txBootloaderChanged.calls == [()]
    first_ok, first_msg = statuses(c)[0]
    assert not first_ok and first_msg.startswith("Starting bootloader check")
    ok, msg = statuses(c)[-1]
    assert ok and msg.startswith(message)
    assert c.fwUpdateProgress.calls == [("Reading bootloader region", 65536, 65536)]
    assert c.interface.hvcontroller.calls == ["off", "on"]   # it restarted the TX
    assert c._fw_update_active is False and c._monitoring_paused is False


def test_answer_survives_the_reconnect_the_check_causes():
    c = Connector()
    c.checkTxBootloader()
    c._invalidate_device_caches("TX")            # disconnect
    assert c.txBootloader() == ""                # master not identified yet
    c._cached_tx_info = [{"module": 0, "deviceId": MASTER}]
    assert c.txBootloader() == "no-bootloader"
    c._cached_tx_info = [{"module": 0, "deviceId": "another-unit"}]
    assert c.txBootloader() == ""


def test_failed_check_is_reported_and_leaves_the_state_unknown():
    c = Connector()
    FakeUpdater.fail_with = RuntimeError("Transmitter did not enter STM32 ROM DFU")
    c.checkTxBootloader()
    assert statuses(c)[-1] == (
        False, "Transmitter bootloader check failed: "
               "Transmitter did not enter STM32 ROM DFU")
    assert c.txBootloader() == "" and c.txBootloaderChanged.calls == []
    assert c._fw_update_active is False and c._monitoring_paused is False


def test_check_without_a_console_skips_the_power_cycle():
    c = Connector()
    c.interface.hvcontroller = object()
    c.checkTxBootloader()
    assert statuses(c)[-1][0] is True
    assert c._fw_update_active is False


def test_image_kind(images, tmp_path):
    c = Connector()
    assert c.transmitterImageKind(images["bare"]) == "bare-metal"
    assert c.transmitterImageKind(images["signed"]) == "signed-app"
    assert c.transmitterImageKind(images["production"]) == "production"
    assert c.transmitterImageKind(images["slot"]) == ""      # unsigned slot build
    assert c.transmitterImageKind(str(tmp_path / "missing.bin")) == ""
    assert c.transmitterImageKind("") == ""


def test_file_version_of_a_bare_metal_image_is_its_embedded_string(tmp_path):
    """It has no signed header; read as one, the vector table says 1.0.0."""
    path = tmp_path / "bare.bin"
    path.write_bytes(_vector_table(0x08000000) + b"\xA5" * 500
                     + b"2.0.9-rc.1-3-gabc1234\x00" + b"\xA5" * 70000)
    c = Connector()
    assert c.getFirmwareFileVersion(str(path)) == "2.0.9-rc.1-3-gabc1234"


def test_file_version_of_the_bundled_images_comes_from_the_signed_header():
    from openlifu_sdk.io.LIFUCrypto import parse_signed_image
    from openlifu_sdk.io.LIFUFirmwareUpdate import (
        bundled_transmitter_production_image,
        bundled_transmitter_signed_app,
    )
    c = Connector()
    signed = bundled_transmitter_signed_app()
    triple = parse_signed_image(signed).fw_version_str
    assert c.getFirmwareFileVersion(str(signed)).startswith(triple)
    production = bundled_transmitter_production_image()
    embedded = parse_signed_image(production.read_bytes()[0x10000:]).fw_version_str
    assert c.getFirmwareFileVersion(str(production)).startswith(embedded)


# ------------------------------------------------------------------- update

def test_bare_metal_file_is_written_through_the_rom_loader(images):
    c = Connector()

    c.updateTransmitterFirmware(images["bare"], 0, False)

    (upd,) = FakeUpdater.instances
    assert upd.tx is c.interface.txdevice
    assert upd.update_calls[0]["bare_metal_image"] == images["bare"]
    assert "signed_app" not in upd.update_calls[0]
    assert statuses(c)[0] == (
        False, "Starting transmitter bare-metal flash via STM32 DFU…")
    assert statuses(c)[-1] == (True, "Done.")
    assert c.invalidated == ["TX"]
    assert c.interface.hvcontroller.calls == ["off", "on"]   # 12 V cycle after
    assert c._fw_update_active is False and c._monitoring_paused is False
    # What it left behind is known without another check.
    assert c._tx_bootloader == {MASTER: "no-bootloader"}


def test_bare_metal_file_is_refused_for_a_slave(images):
    c = Connector(Tx(modules=2))
    c.updateTransmitterFirmware(images["bare"], 1, False)
    assert FakeUpdater.instances == []
    ok, msg = statuses(c)[-1]
    assert not ok and "only the master" in msg


def test_force_production_wins_over_a_bare_metal_file(images):
    c = Connector()
    c.updateTransmitterFirmware(images["bare"], 0, True)
    (upd,) = FakeUpdater.instances
    assert upd.update_calls[0]["force_production"] is True
    assert upd.update_calls[0]["production_image"] is None    # bundled image
    assert "bare_metal_image" not in upd.update_calls[0]
    assert c._tx_bootloader == {MASTER: "secure-bl"}


def test_unit_not_checked_takes_the_version_route():
    """Nothing known: the SDK goes by the app version, as it always has."""
    c = Connector()
    c.updateTransmitterFirmware("", 0, False)
    (upd,) = FakeUpdater.instances
    assert upd.update_calls[0]["signed_app"] is None
    assert "force_production" not in upd.update_calls[0]
    assert c._tx_bootloader == {MASTER: "secure-bl 1.0.1"}   # it went through one


def test_known_bare_metal_unit_gets_the_bootloader_with_an_empty_field():
    """Its version would send it down the signed-app path, which it cannot take."""
    c = Connector(bootloader="no-bootloader")
    c.updateTransmitterFirmware("", 0, False)
    (upd,) = FakeUpdater.instances
    assert upd.update_calls[0]["force_production"] is True
    assert upd.update_calls[0]["production_image"] is None    # bundled image
    assert statuses(c)[-1] == (
        True, "Transmitter production reflash complete (bootloader + app).")
    assert c._tx_bootloader == {MASTER: "secure-bl"}


def test_known_bare_metal_unit_takes_a_browsed_production_image(images):
    c = Connector(bootloader="no-bootloader")
    c.updateTransmitterFirmware(images["production"], 0, False)
    (upd,) = FakeUpdater.instances
    assert upd.update_calls[0]["force_production"] is True
    assert upd.update_calls[0]["production_image"] == images["production"]


def test_file_that_needs_a_bootloader_is_refused_on_a_known_bare_metal_unit(images):
    for name in ("signed", "slot"):
        c = Connector(bootloader="no-bootloader")
        c.updateTransmitterFirmware(images[name], 0, False)
        assert FakeUpdater.instances == []
        ok, msg = statuses(c)[-1]
        assert not ok and "has no bootloader" in msg
        assert c._tx_bootloader == {MASTER: "no-bootloader"}


def test_signed_file_goes_to_a_unit_with_a_bootloader_or_not_checked(images):
    for known in ("secure-bl 1.0.1", ""):
        FakeUpdater.instances = []
        c = Connector(bootloader=known)
        c.updateTransmitterFirmware(images["signed"], 0, False)
        (upd,) = FakeUpdater.instances
        assert upd.update_calls[0]["signed_app"] == images["signed"]


def test_slave_update_ignores_what_is_known_about_the_master():
    c = Connector(Tx(modules=2), bootloader="no-bootloader")
    c.updateTransmitterFirmware("", 1, False)
    (upd,) = FakeUpdater.instances
    assert upd.slave_calls[0][0] == 1 and upd.update_calls == []
    assert c._tx_bootloader == {MASTER: "no-bootloader"}


def test_bare_metal_flash_failure_is_reported(images):
    c = Connector()
    FakeUpdater.fail_with = RuntimeError("Transmitter did not enter STM32 ROM DFU")
    c.updateTransmitterFirmware(images["bare"], 0, False)
    assert statuses(c)[-1] == (
        False, "Transmitter module 0 update failed: "
               "Transmitter did not enter STM32 ROM DFU")
    assert c._fw_update_active is False
    assert c._tx_bootloader == {}


# ----------------------------------------------------------------- recovery

def test_recover_accepts_a_bare_metal_file_for_a_unit_in_the_rom_loader(images):
    c = Connector()
    c._txConnected = False
    c._dfu_kind = "stm32-rom"

    c.recoverTransmitterFirmware(images["bare"])

    (upd,) = FakeUpdater.instances
    assert upd.tx is None
    assert upd.update_calls[0]["bare_metal_image"] == images["bare"]
    assert statuses(c)[-1] == (True, "Done.")


def test_recover_still_refuses_an_unsigned_slot_build(images):
    c = Connector()
    c.recoverTransmitterFirmware(images["slot"])
    assert FakeUpdater.instances == []
    assert statuses(c) == [
        (False, "Firmware file is not a valid signed transmitter image.")]
