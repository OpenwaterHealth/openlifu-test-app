"""Transmitter Assign writes the identity block, and test-report configs stay readable.

Assign on a TX module fills ``sn``, ``hwid``, ``hw_ver`` (optional),
``fw_ver``, ``sdk_ver`` and ``updated`` like the console, but in place, so a
config loaded from a test report keeps its layout and its ``freq``,
``module`` (calibration) and ``device`` blocks. Reading a test report finds
rows by label, so a report with a row added or removed still loads, and the
config check never raises on a missing key. Device I/O is faked and worker
threads run inline.
"""
from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from openlifu_sdk.io.exceptions import LIFUProtocolError
from openlifu_sdk.io.LIFUUserConfig import LifuUserConfig
from openlifu_sdk.util.hwid import encode_hwid

from lifu import lifu_settings
from lifu.lifu_settings import SettingsMixin
from test_reports import test_reports
from test_reports.test_reports import check_config_against_device, report_value

UIDS = {0: bytes.fromhex("650042000550305259383820"),
        1: bytes.fromhex("C0C1C2C3C4C5C6C7D0D1D2D3")}
SDK = "2.1.0"
FW = "2.0.8"

# What loading a test report onto module 0 writes (test_report_to_config,
# with the hwid rewritten to the device's full form).
REPORT_CONFIG = {
    "sn": "WLM44Q80010",
    "hwid": encode_hwid(UIDS[0], base58=True),
    "freq": 400,
    "hw_ver": "DVT",
    "fw_ver": "2.0.8",
    "sdk_ver": "v2.0.17",
    "updated": "2026-10-26 10:00:00",
    "module": {"id": "txm_400_wlm44q80010", "frequency": 400000.0,
               "sensitivity": [[300000, 3000], [400000, 3500], [500000, 4000]]},
    "device": {},
}


class Emitter:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class Mutex:
    def lock(self):
        pass

    def unlock(self):
        pass


class TxDevice:
    """One user config per module; ``None`` is blank flash."""

    def __init__(self, configs=None):
        self.configs = {0: None, 1: None}
        self.configs.update(copy.deepcopy(configs or {}))

    def read_config(self, module=0):
        if self.configs[module] is None:
            raise LIFUProtocolError("failed to parse config response: Invalid magic")
        return LifuUserConfig(json_data=copy.deepcopy(self.configs[module]))

    def write_config(self, config, module=0):
        self.configs[module] = copy.deepcopy(config.to_dict())

    def get_hardware_id(self, module=0, raw_hex=False, base58=False, legacy_6byte=False):
        return encode_hwid(UIDS[module], raw_hex, base58, legacy_6byte)

    def get_version(self, module=0):
        return FW


class Interface:
    def __init__(self, tx):
        self.txdevice = tx

    @staticmethod
    def get_sdk_version():
        return SDK


class Connector(SettingsMixin):
    def __init__(self, tx):
        self._interface_mutex = Mutex()
        self._cached_hv_fw_version = "1.2.7"
        self.interface = Interface(tx)
        self.userConfigRead = Emitter()
        self.userConfigStatus = Emitter()
        self.serialNumberConflict = Emitter()

    def _user_config_unsupported_msg(self, target):
        return None


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    monkeypatch.setattr(lifu_settings.LIFUInterface, "get_sdk_version", staticmethod(lambda: SDK))

    class SyncThread:
        def __init__(self, target, daemon=False):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(lifu_settings.threading, "Thread", SyncThread)


def assign(tx, serial, hw_ver="", module=0, overwrite=True):
    c = Connector(tx)
    c.writeSerialNumber(f"tx_{module}", serial, hw_ver, overwrite)
    ok, message = c.userConfigStatus.calls[-1][1:]
    assert ok, message
    return tx.configs[module], c


# ---------------------------------------------------------------- Assign

def test_assign_on_a_test_report_config_updates_identity_in_place():
    tx = TxDevice({0: REPORT_CONFIG})
    data, c = assign(tx, "WLM44Q80010", "PVT")
    assert list(data) == list(REPORT_CONFIG)  # same keys, same order
    assert data["hwid"] == encode_hwid(UIDS[0], base58=True)
    assert data["hw_ver"] == "PVT"
    assert data["fw_ver"] == FW
    assert data["sdk_ver"] == SDK
    assert data["updated"] != REPORT_CONFIG["updated"]
    for key in ("sn", "freq", "module", "device"):
        assert data[key] == REPORT_CONFIG[key]
    assert json.loads(c.userConfigRead.calls[-1][1]) == data


def test_blank_hw_ver_keeps_the_test_reports_hardware_rev():
    tx = TxDevice({0: REPORT_CONFIG})
    data, _ = assign(tx, "WLM44Q80010", "")
    assert data["hw_ver"] == "DVT"


def test_blank_hw_ver_keeps_an_existing_value_whatever_its_case():
    stored = {"HW_VER": "rev-A", "module": {"sensitivity": [[400000, 3500]]}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-1")
    assert data["HW_VER"] == "rev-A"
    assert "hw_ver" not in data


def test_assign_on_a_blank_module_writes_the_whole_identity():
    tx = TxDevice()
    data, _ = assign(tx, "TXM-2", "", module=1)
    assert data == {"sn": "TXM-2", "hwid": encode_hwid(UIDS[1], base58=True), "hw_ver": "",
                    "fw_ver": FW, "sdk_ver": SDK, "updated": data["updated"]}


def test_missing_identity_keys_are_added_without_moving_the_rest():
    stored = {"freq": 400, "module": {"sensitivity": [[400000, 3500]]}, "device": {"id": "d"}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-3", "EVT")
    assert list(data)[-3:] == ["freq", "module", "device"]
    assert {k: data[k] for k in stored} == stored
    assert {"sn", "hwid", "hw_ver", "fw_ver", "sdk_ver", "updated"} <= set(data)


def test_stale_identity_in_other_casing_is_replaced_once():
    stored = {"SN": "OLD", "HWID": "sJ9EfsKu", "module": {"x": 1}, "sn": "dup"}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "NEW-1")
    assert [k for k in data if k.lower() == "sn"] == ["sn"]
    assert [k for k in data if k.lower() == "hwid"] == ["hwid"]
    assert data["hwid"] == encode_hwid(UIDS[0], base58=True)


def test_invalid_hw_ver_never_touches_the_module():
    tx = TxDevice({0: REPORT_CONFIG})
    c = Connector(tx)
    c.writeSerialNumber("tx_0", "WLM44Q80010", "rev/B", True)
    assert c.userConfigStatus.calls[-1][1] is False
    assert tx.configs[0] == REPORT_CONFIG


# ------------------------------------------------- reading after Assign

class Ifx:
    def __init__(self, tx):
        self.txdevice = tx

    @staticmethod
    def get_sdk_version():
        return SDK


def test_an_assigned_test_report_config_still_matches_its_module():
    tx = TxDevice({0: REPORT_CONFIG})
    data, _ = assign(tx, "WLM44Q80010", "PVT")
    assert check_config_against_device(Ifx(tx), data, module=0) is True
    assert data["module"]["sensitivity"] == REPORT_CONFIG["module"]["sensitivity"]


@pytest.mark.parametrize("removed", ["hw_ver", "fw_ver", "sdk_ver", "updated", "sn"])
def test_a_config_missing_an_identity_key_is_checked_without_raising(removed):
    config = {k: v for k, v in REPORT_CONFIG.items() if k != removed}
    assert check_config_against_device(Ifx(TxDevice()), config, module=0) is True


def test_a_config_missing_its_hwid_does_not_match():
    config = {k: v for k, v in REPORT_CONFIG.items() if k != "hwid"}
    assert check_config_against_device(Ifx(TxDevice()), config, module=0) is False


def test_a_v_prefixed_firmware_version_still_matches():
    config = dict(REPORT_CONFIG, fw_ver="v2.0.8")
    assert check_config_against_device(Ifx(TxDevice()), config, module=0) is True


def test_a_different_firmware_version_still_fails():
    config = dict(REPORT_CONFIG, fw_ver="2.0.7")
    assert check_config_against_device(Ifx(TxDevice()), config, module=0) is False


# ------------------------------------------------- reading a test report

def report(rows):
    """A report frame as read_test_report builds it: code index, Item, Value, Section."""
    return pd.DataFrame(
        [{"Item": item, "Value": value, "Section": section} for _, section, item, value in rows],
        index=[code for code, *_ in rows])


TEMPLATE = [
    ("A.4", "info", "SDK Version", "v2.0.17"),
    ("B.1", "txm", "Serial Number", "WLM44Q80010 "),
    ("B.2", "txm", "Frequency", 400),
    ("B.3", "txm", "Hardware Rev", "DVT"),
    ("B.4", "txm", "Hardware ID", "2uYhng6tjcUqKmhUb"),
    ("B.5", "txm", "Firmware Version", "2.0.8"),
    ("C.1", "console", "Serial Number", "WL134Q80019"),
]


@pytest.fixture
def stub_matrix(monkeypatch):
    monkeypatch.setattr(test_reports, "report_to_matrix_dict",
                        lambda df: {"sensitivity": [[400000, 3500]]})


def test_report_values_are_found_by_label():
    df = report(TEMPLATE)
    assert report_value(df, "sn") == "WLM44Q80010"  # trailing space dropped
    assert report_value(df, "hw_ver") == "DVT"
    assert report_value(df, "hwid") == "2uYhng6tjcUqKmhUb"


def test_a_row_inserted_before_the_identity_rows_shifts_nothing(stub_matrix):
    rows = [TEMPLATE[0], TEMPLATE[1], ("B.2", "txm", "Model", "TXM-400")]
    rows += [(f"B.{int(code[2:]) + 1}", s, i, v) for code, s, i, v in TEMPLATE[2:6]]
    config = test_reports.test_report_to_config(report(rows))
    assert (config["freq"], config["hw_ver"], config["hwid"], config["fw_ver"]) == \
        (400, "DVT", "2uYhng6tjcUqKmhUb", "2.0.8")


def test_a_removed_row_leaves_its_key_out(stub_matrix):
    rows = [r for r in TEMPLATE if r[2] != "Hardware Rev"]
    config = test_reports.test_report_to_config(report(rows))
    assert "hw_ver" not in config
    assert list(config) == ["sn", "hwid", "freq", "fw_ver", "sdk_ver", "updated", "module", "device"]
    json.dumps(config)


def test_an_empty_cell_counts_as_missing(stub_matrix):
    rows = [(c, s, i, float("nan") if i == "Firmware Version" else v) for c, s, i, v in TEMPLATE]
    assert "fw_ver" not in test_reports.test_report_to_config(report(rows))


def test_console_rows_are_never_read_as_the_transmitters():
    rows = [r for r in TEMPLATE if not (r[1] == "txm" and r[2] == "Serial Number")]
    assert report_value(report(rows), "sn") is None


def test_a_report_without_a_serial_number_is_refused(stub_matrix):
    rows = [r for r in TEMPLATE if not (r[1] == "txm" and r[2] == "Serial Number")]
    with pytest.raises(ValueError, match="Serial Number"):
        test_reports.test_report_to_config(report(rows))


# ------------------------------------------------------------ key order

ORDER = ["sn", "hwid", "hw_ver", "fw_ver", "sdk_ver", "updated"]


def test_a_module_assigned_by_the_old_assign_gets_identity_in_order():
    # The old TX Assign wrote only sn and updated, at the top.
    stored = {"sn": "TXM-9", "updated": "2026-11-01 09:00:00",
              "freq": 400, "module": {"sensitivity": [[400000, 3500]]}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-9", "EVT")
    assert list(data) == ORDER + ["freq", "module"]


def test_sn_goes_above_an_existing_hwid():
    stored = {"hwid": "sJ9EfsKu", "freq": 400, "module": {"x": 1}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-10", "EVT")
    assert list(data) == ORDER + ["freq", "module"]


def test_missing_keys_go_between_their_neighbours():
    stored = {"freq": 400, "sn": "TXM-11", "module": {"x": 1}, "sdk_ver": "2.0.17", "device": {}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-11", "EVT")
    assert list(data) == ["freq", "sn", "hwid", "hw_ver", "fw_ver", "module", "sdk_ver",
                          "updated", "device"]


def test_a_kept_upper_case_hw_ver_still_anchors_the_order():
    stored = {"sn": "TXM-12", "HW_VER": "rev-A", "module": {"x": 1}}
    tx = TxDevice({0: stored})
    data, _ = assign(tx, "TXM-12")
    assert list(data) == ["sn", "hwid", "HW_VER", "fw_ver", "sdk_ver", "updated", "module"]
