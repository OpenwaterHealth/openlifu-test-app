"""Writing the console's serial number also writes its identity block, hwid
included. The hwid must be base58 of the full 12-byte UID (SR-004), the form
the operator interface checks, not the legacy 6-byte form.
"""
from __future__ import annotations

import base58
import pytest

from openlifu_sdk.io.exceptions import LIFUProtocolError
from openlifu_sdk.io.LIFUUserConfig import LifuUserConfig
from openlifu_sdk.util.hwid import encode_hwid

from lifu import lifu_settings
from lifu.lifu_settings import SettingsMixin

UID = bytes.fromhex("C0C1C2C3C4C5C6C7D0D1D2D3")


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


class FakeHV:
    """A blank console: no readable config until one is written."""

    def __init__(self):
        self.written = None

    def read_config(self):
        if self.written is None:
            raise LIFUProtocolError("blank config")
        return LifuUserConfig(json_data=dict(self.written))

    def write_config(self, config):
        self.written = config.to_dict()

    def get_hardware_id(self, raw_hex=False, base58=False, legacy_6byte=False):
        return encode_hwid(UID, raw_hex, base58, legacy_6byte)

    def get_version(self):
        return "1.3.0"


class Connector(SettingsMixin):
    def __init__(self):
        self._interface_mutex = type("Mutex", (), {"lock": lambda s: None,
                                                   "unlock": lambda s: None})()
        self.interface = type("Interface", (), {"hvcontroller": FakeHV()})()
        self.userConfigStatus = Emitter()
        self.userConfigRead = Emitter()
        self.serialNumberConflict = Emitter()

    def _user_config_unsupported_msg(self, target):
        return None


@pytest.fixture(autouse=True)
def inline_threads(monkeypatch):
    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)


def test_console_serial_write_stores_full_12_byte_base58_hwid():
    c = Connector()
    c.writeSerialNumber("console", "CON-0001", "1.0")

    assert c.userConfigStatus.calls[-1][1], c.userConfigStatus.calls
    hwid = c.interface.hvcontroller.written["hwid"]
    assert hwid == encode_hwid(UID, base58=True)
    assert base58.b58decode(hwid) == UID
