"""The app installs its own WinUSB driver for the DFU device.

Without a driver for USB\\VID_0483&PID_DF11, libusb can't open a console or
transmitter in its bootloader, so updates and recovery need the bundled
driver (formerly STM32CubeProgrammer's). These tests pin the bundle, the
detection, the elevated command and the Settings decision flow, with every
system call faked.
"""
from __future__ import annotations

import hashlib

import pytest

from lifu import dfu_driver, lifu_settings
from lifu.lifu_settings import DFU_KIND_NO_DRIVER, SettingsMixin

# conftest replaces these for every test; keep the real ones to test them.
REAL_IS_INSTALLED = dfu_driver.is_driver_installed
REAL_INSTALL = dfu_driver.install_driver

BUNDLE = dfu_driver.driver_dir()


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(dfu_driver.sys, "platform", "win32")
    monkeypatch.setattr(lifu_settings.sys, "platform", "win32")
    monkeypatch.setattr(dfu_driver, "_installed_cache", False)


# ------------------------------------------------------------------ bundle

def test_bundle_is_complete_and_cert_is_openwaters():
    assert dfu_driver.check_bundle(BUNDLE) is None


def test_catalog_covers_the_inf_bytes():
    # The .cat signs the INF's SHA-1; any edit (or CRLF rewrite) breaks it.
    inf = (BUNDLE / dfu_driver.DRIVER_INF).read_bytes()
    cat = (BUNDLE / dfu_driver.DRIVER_CAT).read_bytes()
    assert hashlib.sha1(inf).digest() in cat


def test_bundled_inf_binds_df11_to_winusb():
    text = dfu_driver._read_inf_text(BUNDLE / dfu_driver.DRIVER_INF)
    assert dfu_driver.inf_is_dfu_winusb(text)


def test_wrong_cert_is_refused(tmp_path):
    for name in (dfu_driver.DRIVER_INF, dfu_driver.DRIVER_CAT):
        (tmp_path / name).write_bytes((BUNDLE / name).read_bytes())
    (tmp_path / dfu_driver.SIGNING_CERT).write_bytes(b"not the openwater cert")
    assert "thumbprint" in dfu_driver.check_bundle(tmp_path)


def test_missing_file_is_refused(tmp_path):
    assert "missing" in dfu_driver.check_bundle(tmp_path)


# --------------------------------------------------------------- detection

def test_dfuse_driver_does_not_count():
    # ST's legacy DfuSe driver matches DF11 but libusb can't use it.
    assert not dfu_driver.inf_is_dfu_winusb('%D% = I, USB\\VID_0483&PID_DF11\nServiceBinary = STTub30.sys')
    assert not dfu_driver.inf_is_dfu_winusb('USB\\VID_0483&PID_5A5A\nInclude = winusb.inf')
    assert not dfu_driver.inf_is_dfu_winusb('USB\\VID_0483&PID_DF11A\nInclude = winusb.inf')
    assert dfu_driver.inf_is_dfu_winusb('%D% = I, USB\\VID_0483&PID_DF11\nInclude=WinUSB.inf')


def test_detects_driver_in_store(windows, monkeypatch, tmp_path):
    other = tmp_path / "oem1.inf"
    other.write_text("USB\\VID_1234&PID_0001\nInclude = winusb.inf", encoding="latin-1")
    ours = tmp_path / "oem7.inf"
    ours.write_bytes((BUNDLE / dfu_driver.DRIVER_INF).read_bytes())   # UTF-16
    monkeypatch.setattr(dfu_driver, "_driver_store_infs", lambda: [other, ours])
    assert REAL_IS_INSTALLED() is True


def test_missing_driver_is_rechecked(windows, monkeypatch, tmp_path):
    store = []
    monkeypatch.setattr(dfu_driver, "_driver_store_infs", lambda: list(store))
    assert REAL_IS_INSTALLED() is False
    store.append(tmp_path / "oem2.inf")
    store[0].write_bytes((BUNDLE / dfu_driver.DRIVER_INF).read_bytes())
    assert REAL_IS_INSTALLED() is True


def test_not_windows_is_a_noop(monkeypatch):
    monkeypatch.setattr(dfu_driver.sys, "platform", "linux")
    assert REAL_IS_INSTALLED() is False
    assert REAL_INSTALL().status == "unsupported"


# ------------------------------------------------------- elevated command

def test_install_script_quotes_paths(tmp_path):
    folder = tmp_path / "O'Brien app"
    script = dfu_driver.build_install_script(folder)
    assert "'" + str(folder / dfu_driver.SIGNING_CERT).replace("'", "''") + "'" in script
    assert "-addstore TrustedPublisher" in script
    assert "/add-driver" in script and "/install" in script
    assert "@(0, 259, 3010)" in script


@pytest.mark.parametrize("outcome, code, status", [
    (("ok", 0), None, "installed"),
    (("declined", -1), None, "declined"),
    (("failed", 5), None, "failed"),
    (("ok", 10), None, "failed"),
    (("ok", 20), None, "failed"),
])
def test_install_outcomes(windows, monkeypatch, outcome, code, status):
    monkeypatch.setattr(dfu_driver, "_run_elevated_powershell", lambda script: outcome)
    result = REAL_INSTALL()
    assert result.status == status
    assert dfu_driver._installed_cache is (status == "installed")


def test_bad_bundle_never_elevates(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(dfu_driver, "driver_dir", lambda: tmp_path)
    # conftest's _run_elevated_powershell stub raises if reached.
    assert REAL_INSTALL().status == "failed"


# ----------------------------------------------------- Settings decisions

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


class Connector(SettingsMixin):
    def __init__(self):
        self._hvConnected = False
        self._fw_update_active = False
        self._monitoring_paused = False
        self._console_dfu_kind = self._console_dfu_version = ""
        self._console_dfu_probe_busy = False
        self.fwUpdateStatus = Emitter()
        self.fwUpdateProgress = Emitter()
        self.consoleDfuStateChanged = Emitter()


@pytest.fixture
def driver(windows, monkeypatch):
    state = {"installed": False, "result": dfu_driver.DriverResult("installed"), "prompts": 0}
    monkeypatch.setattr(dfu_driver, "is_driver_installed", lambda: state["installed"])

    def install():
        state["prompts"] += 1
        return state["result"]

    monkeypatch.setattr(dfu_driver, "install_driver", install)
    return state


def test_present_driver_never_prompts(driver):
    driver["installed"] = True
    c = Connector()
    assert c._ensure_dfu_driver("console") is True
    assert driver["prompts"] == 0 and c.fwUpdateStatus.calls == []


def test_missing_driver_is_installed_first(driver):
    c = Connector()
    assert c._ensure_dfu_driver("console") is True
    assert driver["prompts"] == 1
    assert "Installing the USB driver" in c.fwUpdateStatus.calls[0][2]


@pytest.mark.parametrize("status", ["declined", "failed"])
def test_declined_install_stops_the_update(driver, status):
    driver["result"] = dfu_driver.DriverResult(status, "Windows permission was not granted")
    c = Connector()
    assert c._ensure_dfu_driver("transmitter") is False
    device, ok, msg = c.fwUpdateStatus.calls[-1]
    assert (device, ok) == ("transmitter", False)
    assert "not started" in msg and "Windows permission was not granted" in msg


def test_console_update_stops_before_touching_the_device(driver, monkeypatch):
    driver["result"] = dfu_driver.DriverResult("declined", "Windows permission was not granted")
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    c = Connector()
    monkeypatch.setattr(Connector, "isConsoleFirmwareSigned", lambda self, p: True)
    c.recoverConsoleFirmware("fw.bin")       # no interface: reaching the SDK would raise
    assert c._monitoring_paused is False and c._fw_update_active is False
    assert "not started" in c.fwUpdateStatus.calls[-1][2]


def test_slave_update_needs_no_driver(driver, monkeypatch):
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    monkeypatch.setattr(lifu_settings.time, "sleep", lambda s: None)
    hv = type("Hv", (), {"turn_12v_off": lambda self: None, "turn_12v_on": lambda self: None})()
    c = Connector()
    # No txdevice: the slave path fails on its own after the driver gate.
    c.interface = type("Interface", (), {"hvcontroller": hv, "txdevice": None})()
    c._invalidate_device_caches = lambda *a: None
    c._cached_tx_fw_version = {1: "2.0.9"}
    c.updateTransmitterFirmware("", 1)
    assert driver["prompts"] == 0
    assert "Installing the USB driver" not in str(c.fwUpdateStatus.calls)


def test_master_update_installs_driver_first(driver, monkeypatch):
    driver["result"] = dfu_driver.DriverResult("declined", "Windows permission was not granted")
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    c = Connector()                           # no interface: reaching the SDK would raise
    c.updateTransmitterFirmware("", 0)
    assert driver["prompts"] == 1
    assert c._monitoring_paused is False and c._fw_update_active is False
    assert "not started" in c.fwUpdateStatus.calls[-1][2]


def _probe_raising(monkeypatch, exc):
    import openlifu_sdk.io.LIFUDFU as sdk_dfu

    class Manager:
        def detect_console_dfu_kind(self):
            raise exc

    monkeypatch.setattr(sdk_dfu, "LIFUDFUManager", Manager)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    c = Connector()
    c.probeConsoleDfu()
    return c


def test_unopenable_dfu_device_without_driver_offers_recover(driver, monkeypatch):
    c = _probe_raising(monkeypatch, OSError("Operation not supported or unimplemented"))
    assert c._console_dfu_kind == DFU_KIND_NO_DRIVER


def test_unopenable_dfu_device_with_driver_is_just_a_failure(driver, monkeypatch):
    driver["installed"] = True
    c = _probe_raising(monkeypatch, OSError("busy"))
    assert c._console_dfu_kind == ""


def test_no_dfu_device_stays_empty(driver, monkeypatch):
    c = _probe_raising(monkeypatch, RuntimeError("No USB DFU device found"))
    assert c._console_dfu_kind == ""
