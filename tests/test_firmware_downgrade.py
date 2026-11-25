"""Update Firmware refuses a downgrade before anything is written.

On a 1.0.x secure bootloader the SDK installs the bootloader updater first,
which empties the application slot and the anti-rollback floor, so the
SDK's own downgrade check then finds nothing installed and lets an older
app through. The app checks the file against the running version first
(PR #66 review). Also pins that a slave's update route reaches the SDK with
the matching DFU stub.
"""
from __future__ import annotations

import pytest

from lifu import firmware_images as fi
from lifu import lifu_settings
from lifu.lifu_settings import SettingsMixin

CONSOLE_APP = str(fi.image(fi.CONSOLE_SIGNED_APP))
CONSOLE_VER = fi.shipped_version(fi.CONSOLE_SIGNED_APP)
TX_VER = fi.shipped_version(fi.TRANSMITTER_SIGNED_APP)


def _parts(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in version.split("-")[0].split(".")[:3])
    return major, minor, patch


def _newer(version: str) -> str:
    major, minor, patch = _parts(version)
    return f"{major}.{minor}.{patch + 1}"


def _older(version: str) -> str:
    major, minor, _ = _parts(version)
    return f"{major}.{minor - 1}.0" if minor else f"{major - 1}.9.0"


# ------------------------------------------------------------- the check

@pytest.mark.parametrize("installed,refused", [
    (lambda: _newer(CONSOLE_VER), True),
    (lambda: _newer(CONSOLE_VER) + "-rc.2", True),
    (lambda: CONSOLE_VER, False),             # reinstall
    (lambda: _older(CONSOLE_VER), False),     # upgrade
    (lambda: None, False),                    # unreadable: the SDK decides
])
def test_downgrade_refusal(installed, refused):
    msg = SettingsMixin._downgrade_refusal("console", CONSOLE_APP, installed())
    assert (msg is not None) is refused
    if refused:
        assert "Nothing was written" in msg


def test_unreadable_file_is_left_to_the_sdk(tmp_path):
    junk = tmp_path / "x.bin"
    junk.write_bytes(b"\0" * 64)
    assert SettingsMixin._downgrade_refusal("console", str(junk), "9.9.9") is None


# ------------------------------------------------------- console worker

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


class FakeConsoleUpdater:
    built = []

    def __init__(self, hv=None, **kwargs):
        FakeConsoleUpdater.built.append(hv)

    def update(self, **kwargs):
        return type("R", (), {"summary": "Updated.", "reboot_required": False})()


class ConsoleConnector(SettingsMixin):
    _console_dfu_kind = _console_dfu_version = ""

    def __init__(self, installed):
        self._cached_hv_fw_version = installed
        self._txConnected = True
        self._monitoring_paused = False
        self._fw_update_active = False
        self.interface = type("I", (), {"hvcontroller": object()})()
        self.fwUpdateStatus = Emitter()
        self.fwUpdateProgress = Emitter()

    def isConsoleFirmwareSigned(self, path):
        return True

    def _keep_console_hub_awake_for_update(self):
        return True

    def _invalidate_device_caches(self, side):
        pass

    def _set_console_dfu_state(self, *args):
        pass


@pytest.fixture
def console_sdk(monkeypatch):
    FakeConsoleUpdater.built = []
    import openlifu_sdk.io.LIFUFirmwareUpdate as fu
    monkeypatch.setattr(fu, "LIFUFirmwareUpdate", FakeConsoleUpdater)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)


def test_console_downgrade_is_refused_before_the_sdk_runs(console_sdk):
    c = ConsoleConnector(installed=_newer(CONSOLE_VER))
    c.updateConsoleFirmware(CONSOLE_APP)
    assert FakeConsoleUpdater.built == []
    ok, msg = c.fwUpdateStatus.calls[-1][1:]
    assert ok is False and msg.startswith("Console update failed: Downgrade refused")
    assert "Force" in msg


def test_console_force_installs_the_older_file(console_sdk):
    c = ConsoleConnector(installed=_newer(CONSOLE_VER))
    c.updateConsoleFirmware(CONSOLE_APP, True)
    assert FakeConsoleUpdater.built == [c.interface.hvcontroller]
    assert c.fwUpdateStatus.calls[-1][1:] == (True, "Updated.")


def test_console_recovery_is_not_checked(console_sdk):
    c = ConsoleConnector(installed=_newer(CONSOLE_VER))
    c.recoverConsoleFirmware(CONSOLE_APP)
    assert FakeConsoleUpdater.built == [None]


def test_console_upgrade_proceeds(console_sdk):
    c = ConsoleConnector(installed=_older(CONSOLE_VER))
    c.updateConsoleFirmware(CONSOLE_APP)
    assert FakeConsoleUpdater.built == [c.interface.hvcontroller]


# --------------------------------------------------- transmitter worker

class FakeTxUpdater:
    calls = []

    def __init__(self, tx=None, **kwargs):
        FakeTxUpdater.calls.append(("built",))

    def update(self, **kwargs):
        FakeTxUpdater.calls.append(("update", kwargs))
        return type("R", (), {"summary": "Updated."})()

    def update_slave(self, module, **kwargs):
        FakeTxUpdater.calls.append(("update_slave", module, kwargs))
        return type("R", (), {"summary": "Updated."})()


class Hv:
    def turn_12v_off(self):
        pass

    def turn_12v_on(self):
        pass


class Tx:
    def __init__(self, readable):
        self.readable = readable

    def get_version(self, module=0):
        if not self.readable:
            raise RuntimeError("no reply")
        return "2.0.5"

    def get_module_count(self):
        return 2


class TxConnector(SettingsMixin):
    def __init__(self, versions, readable=True):
        self._cached_tx_fw_version = dict(versions)
        self._monitoring_paused = False
        self._fw_update_active = False
        self.interface = type("I", (), {"hvcontroller": Hv(), "txdevice": Tx(readable)})()
        self.fwUpdateStatus = Emitter()
        self.fwUpdateProgress = Emitter()

    def _ensure_dfu_driver(self, device_type):
        return True

    def _invalidate_device_caches(self, side):
        pass


@pytest.fixture
def tx_sdk(monkeypatch):
    FakeTxUpdater.calls = []
    import openlifu_sdk.io.LIFUFirmwareUpdate as fu
    monkeypatch.setattr(fu, "LIFUTransmitterFirmwareUpdate", FakeTxUpdater)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)
    monkeypatch.setattr(lifu_settings.time, "sleep", lambda s: None)


def test_transmitter_downgrade_is_refused_before_the_sdk_runs(tx_sdk):
    c = TxConnector({0: _newer(TX_VER)})
    c.updateTransmitterFirmware("", 0)
    assert FakeTxUpdater.calls == []
    ok, msg = c.fwUpdateStatus.calls[-1][1:]
    assert ok is False and "Downgrade refused" in msg


def test_transmitter_reinstall_proceeds(tx_sdk):
    c = TxConnector({0: TX_VER})
    c.updateTransmitterFirmware("", 0)
    assert [call[0] for call in FakeTxUpdater.calls] == ["built", "update"]


def test_legacy_slave_gets_the_raw_stub_and_its_route(tx_sdk):
    c = TxConnector({1: "2.0.5"})
    c.updateTransmitterFirmware("", 1)
    _, module, kw = FakeTxUpdater.calls[-1]
    assert module == 1 and kw["legacy"] is True
    assert kw["stub_bin"] == str(fi.image(fi.TRANSMITTER_DFU_STUB))


def test_unreadable_slave_gets_no_stub(tx_sdk):
    c = TxConnector({}, readable=False)
    c.updateTransmitterFirmware("", 1)
    _, module, kw = FakeTxUpdater.calls[-1]
    assert module == 1 and "legacy" not in kw and "stub_bin" not in kw
