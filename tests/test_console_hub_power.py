"""A console update must keep the console's USB hub awake on older firmware.

Console firmware older than CONSOLE_DFU_HUB_FIX_FW_VERSION hands over to its
bootloader in a way that leaves a selectively suspended USB hub unable to
report the DFU device, so the update times out with the console stuck in its
bootloader. Before such an update the app asks Windows (UAC) to stop that one
hub from sleeping. These tests pin the version gate, the safety of the
elevated command, and the decision flow, with every system call faked.
"""
from __future__ import annotations

import pytest

from lifu import lifu_settings, usb_hub_power
from lifu.lifu_constants import (
    CONSOLE_DFU_HUB_FIX_FW_VERSION,
    console_needs_awake_hub_for_update,
)
from lifu.lifu_settings import SettingsMixin

HUB = "USB\\VID_0424&PID_2514\\9&2597043D&0&3"


# ------------------------------------------------------------- version gate

@pytest.mark.parametrize("version, needs", [
    ("1.2.6", True),
    ("v1.2.7", True),
    ("1.2.6-rc.6-8-ged4c55a", True),   # dev builds parse to their base version
    (CONSOLE_DFU_HUB_FIX_FW_VERSION, False),
    ("1.3.0", False),
    ("2.0.0", False),
    (None, True),                       # unreadable: ask rather than risk it
    ("garbage", True),
])
def test_version_gate(version, needs):
    assert console_needs_awake_hub_for_update(version) is needs


# -------------------------------------------------- elevated command safety

def test_script_targets_exactly_the_given_hub():
    script = usb_hub_power.build_keep_awake_script([HUB])
    assert f"'{HUB}_0'" in script
    assert "MSPower_DeviceEnable" in script and "Enable = $false" in script
    assert script.rstrip().endswith("exit 0")


@pytest.mark.parametrize("bad", [
    "USB\\VID_0483&PID_57A0\\2070356F5958",            # the console itself
    "USB\\VID_0424&PID_2514\\x'; Remove-Item C:\\ -Recurse; '",
    "USB\\VID_0424&PID_2514\\a$(calc)",
    "",
])
def test_script_refuses_anything_but_a_hub_instance(bad):
    with pytest.raises(ValueError):
        usb_hub_power.build_keep_awake_script([bad])
    assert not usb_hub_power.is_valid_hub_instance(bad)


def test_multi_sz_split():
    assert usb_hub_power._split_multi_sz("A\0B\0\0") == ["A", "B"]


# ------------------------------------------------------------ decision flow

class Emitter:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class Hv:
    def __init__(self, version=None, fail=False):
        self._version, self._fail = version, fail

    def get_version(self):
        if self._fail:
            raise RuntimeError("timeout")
        return self._version


class Connector(SettingsMixin):
    def __init__(self, cached_version=None, tx_connected=False, hv=None):
        self._cached_hv_fw_version = cached_version
        self._txConnected = tx_connected
        self.interface = type("Interface", (), {"hvcontroller": hv or Hv()})()
        self.fwUpdateStatus = Emitter()


@pytest.fixture
def system(monkeypatch):
    """Fake hub lookup / setting / elevation; records prompts."""
    state = {"hubs": [HUB], "may_sleep": True, "result": usb_hub_power.HubPowerResult("applied"),
             "prompts": []}
    monkeypatch.setattr(usb_hub_power, "console_hub_instances", lambda: list(state["hubs"]))
    monkeypatch.setattr(usb_hub_power, "hub_may_sleep", lambda hub: state["may_sleep"])

    def keep_awake(hubs):
        state["prompts"].append(list(hubs))
        return state["result"]

    monkeypatch.setattr(usb_hub_power, "keep_hubs_awake", keep_awake)
    return state


def msgs(c):
    return [m for _dev, _ok, m in c.fwUpdateStatus.calls]


def test_fixed_firmware_never_prompts(system):
    c = Connector(cached_version=CONSOLE_DFU_HUB_FIX_FW_VERSION)
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == [] and msgs(c) == []


def test_hub_already_awake_does_not_prompt(system):
    system["may_sleep"] = False
    c = Connector(cached_version="1.2.6")
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == []


def test_no_console_hub_found_does_not_prompt(system):
    system["hubs"] = []
    c = Connector(cached_version="1.2.6")
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == []


def test_old_firmware_prompts_for_the_console_hub_only(system):
    c = Connector(cached_version="1.2.6")
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == [[HUB]]
    assert msgs(c)[0].startswith("Starting console firmware update")
    assert "permission" in msgs(c)[0]


def test_unreadable_version_is_queried_then_treated_as_old(system):
    c = Connector(cached_version=None, hv=Hv(fail=True))
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == [[HUB]]


def test_version_read_from_device_when_not_cached(system):
    c = Connector(cached_version=None, hv=Hv(version=CONSOLE_DFU_HUB_FIX_FW_VERSION))
    assert c._keep_console_hub_awake_for_update() is True
    assert system["prompts"] == []


@pytest.mark.parametrize("status", ["declined", "failed"])
def test_refused_without_transmitter_stops_the_update(system, status):
    system["result"] = usb_hub_power.HubPowerResult(status, "Windows permission was not granted")
    c = Connector(cached_version="1.2.6", tx_connected=False)
    assert c._keep_console_hub_awake_for_update() is False
    last_ok, last = c.fwUpdateStatus.calls[-1][1:]
    assert last_ok is False
    # The Settings dialog closes out on "failed".
    assert last.startswith("Console update failed:") and "transmitter" in last


def test_refused_with_transmitter_continues(system):
    system["result"] = usb_hub_power.HubPowerResult("declined", "Windows permission was not granted")
    c = Connector(cached_version="1.2.6", tx_connected=True)
    assert c._keep_console_hub_awake_for_update() is True
    assert msgs(c)[-1].startswith("Starting console firmware update anyway")


# --------------------------------------------------- wired into the update

class SyncThread:
    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()


class FakeUpdater:
    built = []

    def __init__(self, hv=None, **kwargs):
        FakeUpdater.built.append(hv)

    def update(self, **kwargs):
        return type("R", (), {"summary": "Updated.", "reboot_required": False})()


class UpdateConnector(Connector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._monitoring_paused = False
        self._fw_update_active = False
        self.fwUpdateProgress = Emitter()

    _console_dfu_kind = _console_dfu_version = ""

    def isConsoleFirmwareSigned(self, path):
        return True

    def _invalidate_device_caches(self, side):
        pass

    def _set_console_dfu_state(self, *args):
        pass


@pytest.fixture
def sdk(monkeypatch):
    FakeUpdater.built = []
    import openlifu_sdk.io.LIFUFirmwareUpdate as fu
    monkeypatch.setattr(fu, "LIFUFirmwareUpdate", FakeUpdater)
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)


def test_update_is_not_started_when_hub_cannot_be_kept_awake(system, sdk):
    system["result"] = usb_hub_power.HubPowerResult("declined", "Windows permission was not granted")
    c = UpdateConnector(cached_version="1.2.6")
    c.updateConsoleFirmware("fw-signed.bin")
    assert FakeUpdater.built == []               # SDK never touched
    assert c._monitoring_paused is False and c._fw_update_active is False


def test_update_proceeds_after_permission(system, sdk):
    c = UpdateConnector(cached_version="1.2.6")
    c.updateConsoleFirmware("fw-signed.bin")
    assert system["prompts"] == [[HUB]]
    assert FakeUpdater.built == [c.interface.hvcontroller]
    assert c.fwUpdateStatus.calls[-1][1:] == (True, "Updated.")


def test_recovery_does_not_prompt(system, sdk):
    c = UpdateConnector(cached_version="1.2.6")
    c.recoverConsoleFirmware("fw-signed.bin")
    assert system["prompts"] == []
    assert FakeUpdater.built == [None]
