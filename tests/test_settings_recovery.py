"""The Settings cards must be able to reach a device parked in a bootloader.

A console or transmitter master whose update was interrupted sits in its
bootloader's USB DFU with no serial port, so the normal update slots can't be
used. These tests pin the contract of the recovery paths without hardware or
a Qt event loop: the SDK updaters and DFU detection are replaced with fakes,
and the mixin's worker threads are run inline.
"""
from __future__ import annotations

import pytest

from lifu import lifu_settings
from lifu.lifu_settings import SettingsMixin, classify_dfu_layout


class Emitter:
    """Stands in for a pyqtSignal: records every emit."""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class SyncThread:
    """threading.Thread stand-in that runs the target inline on start()."""

    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()


class Connector(SettingsMixin):
    """Just enough of LIFUConnector for the firmware slots."""

    def __init__(self, hv_connected=False, tx_connected=False):
        self._hvConnected = hv_connected
        self._txConnected = tx_connected
        self._monitoring_paused = False
        self._fw_update_active = False
        self._dfu_device_type = ""
        self._dfu_kind = ""
        self._dfu_version = ""
        self._dfu_probe_busy = False
        # Any 12 V or serial access during a recovery must fail loudly.
        self.interface = type("Interface", (), {"hvcontroller": object(),
                                                "txdevice": object()})()
        self.fwUpdateStatus = Emitter()
        self.fwUpdateProgress = Emitter()
        self.dfuStateChanged = Emitter()
        self.invalidated = []

    # Mirrors LIFUConnector.dfuState, which the mixin reads for messages.
    @property
    def dfuState(self):
        return f"{self._dfu_kind} {self._dfu_version}".strip()

    def _invalidate_device_caches(self, side):
        self.invalidated.append(side)

    def isConsoleFirmwareSigned(self, path):
        return path.endswith("signed.bin")

    def dfu(self):
        return (self._dfu_device_type, self._dfu_kind, self._dfu_version)


class FakeResult:
    def __init__(self, summary, reboot_required):
        self.summary = summary
        self.reboot_required = reboot_required


class FakeUpdater:
    """Records how an SDK updater class was built and called."""

    instances = []
    fail_with = None
    summary = "Recovered."

    def __init__(self, hv=None, tx=None, **kwargs):
        self.hv = hv
        self.tx = tx
        self.kwargs = kwargs
        self.update_calls = []
        FakeUpdater.instances.append(self)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        if FakeUpdater.fail_with:
            raise FakeUpdater.fail_with
        kwargs["progress_callback"](10, 100, "USB DFU write")
        return FakeResult(FakeUpdater.summary, True)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeUpdater.instances = []
    FakeUpdater.fail_with = None
    FakeUpdater.summary = "Recovered."
    import openlifu_sdk.io.LIFUFirmwareUpdate as fu

    monkeypatch.setattr(fu, "LIFUFirmwareUpdate", FakeUpdater)
    monkeypatch.setattr(fu, "LIFUTransmitterFirmwareUpdate", FakeUpdater)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    yield


def statuses(c):
    return [(ok, msg) for _dev, ok, msg in c.fwUpdateStatus.calls]


def devices(c):
    return {dev for dev, _ok, _msg in c.fwUpdateStatus.calls}


# ----------------------------------------------------------------- console

def test_recover_console_builds_updater_without_hv_and_reports_success():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "console", "legacy-bl", "0.0.2"

    c.recoverConsoleFirmware("fw-signed.bin", False)

    (upd,) = FakeUpdater.instances
    assert upd.hv is None  # no serial port: the SDK classifies over USB
    assert upd.update_calls[0]["signed_app"] == "fw-signed.bin"
    assert upd.update_calls[0]["force"] is False
    first_ok, first_msg = statuses(c)[0]
    assert not first_ok and first_msg.startswith("Starting console recovery")
    assert "legacy-bl 0.0.2" in first_msg and "powered" in first_msg
    assert statuses(c)[-1] == (
        True, "Recovered. Power-cycle the console to boot the new application.")
    assert devices(c) == {"console"}
    assert c.fwUpdateProgress.calls == [("USB DFU write", 10, 100)]
    assert "HV" in c.invalidated and "TX" in c.invalidated
    # Leaving the bootloader: the DFU state is cleared and published.
    assert c.dfu() == ("", "", "")
    assert c.dfuStateChanged.calls


def test_update_console_still_passes_the_live_hv_handle():
    c = Connector(hv_connected=True)
    c.updateConsoleFirmware("fw-signed.bin", True)
    (upd,) = FakeUpdater.instances
    assert upd.hv is c.interface.hvcontroller
    assert upd.update_calls[0]["force"] is True
    assert statuses(c)[0] == (False, "Starting console firmware update…")


def test_recover_console_failure_is_reported_and_keeps_dfu_state():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "console", "secure-bl", "1.0.2"
    FakeUpdater.fail_with = RuntimeError("no DFU device")

    c.recoverConsoleFirmware("fw-signed.bin")

    assert statuses(c)[-1] == (False, "Console recovery failed: no DFU device")
    assert c.dfu() == ("console", "secure-bl", "1.0.2")
    assert c._fw_update_active is False and c._monitoring_paused is False


def test_recover_console_explains_power_cycle_when_secure_dfu_never_appears():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "console", "legacy-bl", "0.0.2"
    FakeUpdater.fail_with = RuntimeError(
        "After the updater ran, the secure bootloader DFU did not appear "
        "(Timed out waiting for DFU kind 'secure-bl' after 30s). The bootloader "
        "replacement may have failed; recover via SWD.")

    c.recoverConsoleFirmware("fw-signed.bin")

    ok, msg = statuses(c)[-1]
    assert not ok and msg.startswith("Console recovery failed: After the updater ran")
    assert "power-cycle the console" in msg and "click Recover again" in msg


def test_recover_console_refuses_unsigned_file_before_touching_the_device():
    c = Connector()
    c.recoverConsoleFirmware("random.bin")
    assert FakeUpdater.instances == []
    assert statuses(c) == [
        (False, "Firmware file is not a valid signed console image.")]


# ------------------------------------------------------------- transmitter

def test_recover_transmitter_builds_updater_without_tx_and_uses_bundled_app():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "transmitter", "legacy-bl", "0.0.3"
    FakeUpdater.summary = "Recovered a legacy-bootloader transmitter over USB."

    c.recoverTransmitterFirmware("")   # empty field = bundled signed app

    (upd,) = FakeUpdater.instances
    assert upd.tx is None and upd.hv is None
    assert upd.update_calls[0]["signed_app"] is None
    first_ok, first_msg = statuses(c)[0]
    assert not first_ok and first_msg.startswith("Starting transmitter recovery")
    assert "legacy-bl 0.0.3" in first_msg and "powered" in first_msg
    assert statuses(c)[-1] == (
        True, "Recovered a legacy-bootloader transmitter over USB. "
              "Power-cycle the transmitter to boot the new application.")
    assert devices(c) == {"transmitter"}
    assert c.invalidated == ["TX"]           # no console cache, and no 12 V cycle
    assert c.dfu() == ("", "", "")


def test_recover_transmitter_passes_a_browsed_signed_file():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "transmitter", "secure-bl", "1.0.4"
    c.recoverTransmitterFirmware("tx-signed.bin")
    (upd,) = FakeUpdater.instances
    assert upd.update_calls[0]["signed_app"] == "tx-signed.bin"


def test_recover_transmitter_refuses_unsigned_file():
    c = Connector()
    c.recoverTransmitterFirmware("random.bin")
    assert FakeUpdater.instances == []
    assert statuses(c) == [
        (False, "Firmware file is not a valid signed transmitter image.")]


def test_recover_transmitter_failure_and_power_cycle_hint():
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "transmitter", "legacy-bl", "0.0.3"
    FakeUpdater.fail_with = RuntimeError(
        "After the updater ran, the secure bootloader DFU did not appear (timeout).")

    c.recoverTransmitterFirmware("")

    ok, msg = statuses(c)[-1]
    assert not ok and msg.startswith("Transmitter recovery failed: After the updater ran")
    assert "power-cycle the transmitter" in msg and "click Recover again" in msg
    assert c.dfu() == ("transmitter", "legacy-bl", "0.0.3")
    assert c._fw_update_active is False and c._monitoring_paused is False


# ---------------------------------------------------------- classification

@pytest.mark.parametrize("layout, expected", [
    # console: STM32F072, 64 x 2 KB
    ("@Internal Flash/0x08000000/32*2Ka,30*2Kg,02*2Ka", "console"),          # secure BL
    ("@Firmware/0x08007800/1*2Kg,94*2Kg", "console"),                       # legacy BL (190 KB described)
    ("@Internal Flash  /0x08000000/064*0002Kg", "console"),                 # ROM loader
    # transmitter: STM32L443, 128 x 2 KB
    ("@Internal Flash/0x08000000/32*002Ka,94*002Kg,02*002Ka", "transmitter"),  # secure BL
    ("@Firmware/0x0800F800/1*2Kg,190*2Kg", "transmitter"),                     # legacy BL
    ("@Internal Flash  /0x08000000/128*0002Kg", "transmitter"),                # ROM loader
    # unknown
    ("", ""),
    ("@Option Bytes  /0x1FFFF800/01*016 e", ""),
    ("garbage", ""),
])
def test_classify_dfu_layout(layout, expected):
    assert classify_dfu_layout(layout) == expected


# ------------------------------------------------------------------- probe

class FakeManager:
    result = ("legacy-bl", "0.0.2")
    error = None

    def detect_console_dfu_kind(self):
        if FakeManager.error:
            raise FakeManager.error
        return FakeManager.result


@pytest.fixture
def fake_probe(monkeypatch):
    import openlifu_sdk.io.LIFUDFU as dfu

    FakeManager.result = ("legacy-bl", "0.0.2")
    FakeManager.error = None
    monkeypatch.setattr(dfu, "LIFUDFUManager", FakeManager)
    layouts = {"value": "@Firmware/0x08007800/1*2Kg,94*2Kg"}
    monkeypatch.setattr(lifu_settings, "_read_dfu_interface_layout",
                        lambda: layouts["value"])
    FakeManager.layouts = layouts
    return FakeManager


def test_probe_attributes_console_by_layout_and_publishes_once(fake_probe):
    c = Connector()
    c.probeDfuDevice()
    assert c.dfu() == ("console", "legacy-bl", "0.0.2")
    assert len(c.dfuStateChanged.calls) == 1
    c.probeDfuDevice()  # same answer: no re-notify
    assert len(c.dfuStateChanged.calls) == 1
    assert c._dfu_probe_busy is False


def test_probe_attributes_transmitter_by_layout(fake_probe):
    fake_probe.result = ("secure-bl", "1.0.4")
    fake_probe.layouts["value"] = "@Internal Flash/0x08000000/32*002Ka,94*002Kg,02*002Ka"
    c = Connector(hv_connected=True)      # console app is up, TX missing
    c.probeDfuDevice()
    assert c.dfu() == ("transmitter", "secure-bl", "1.0.4")


def test_probe_unreadable_layout_leaves_type_unknown(fake_probe):
    fake_probe.layouts["value"] = ""
    c = Connector()
    c.probeDfuDevice()
    assert c.dfu() == ("", "legacy-bl", "0.0.2")


def test_probe_clears_state_when_nothing_is_enumerated(fake_probe):
    c = Connector()
    c._dfu_device_type, c._dfu_kind, c._dfu_version = "console", "legacy-bl", "0.0.2"
    fake_probe.error = RuntimeError("No USB DFU device found")
    c.probeDfuDevice()
    assert c.dfu() == ("", "", "")
    assert len(c.dfuStateChanged.calls) == 1


def test_probe_treats_unknown_dfu_kind_as_nothing(fake_probe):
    fake_probe.result = ("unknown", "")
    c = Connector()
    c.probeDfuDevice()
    assert c.dfu() == ("", "", "")
    assert c.dfuStateChanged.calls == []


def test_probe_is_skipped_when_both_connected_or_update_running(fake_probe):
    c = Connector(hv_connected=True, tx_connected=True)
    c.probeDfuDevice()
    assert c.dfuStateChanged.calls == []
    c = Connector()
    c._fw_update_active = True
    c.probeDfuDevice()
    assert c.dfuStateChanged.calls == []


def test_probe_runs_when_only_one_device_is_connected(fake_probe):
    c = Connector(hv_connected=True)
    c.probeDfuDevice()
    assert c.dfu() == ("console", "legacy-bl", "0.0.2")   # layout wins over attribution


# -------------------------------------------------------------- attribution

def test_unknown_type_is_attributed_to_the_missing_device():
    c = Connector(hv_connected=True, tx_connected=False)
    c._dfu_kind, c._dfu_version = "secure-bl", "1.0.4"
    assert c._dfu_attributed_type() == "transmitter"
    c = Connector(hv_connected=False, tx_connected=True)
    c._dfu_kind = "secure-bl"
    assert c._dfu_attributed_type() == "console"
    c = Connector()
    c._dfu_kind = "secure-bl"
    assert c._dfu_attributed_type() == ""        # both missing: ambiguous
    c._dfu_device_type = "transmitter"
    assert c._dfu_attributed_type() == "transmitter"  # layout always wins
