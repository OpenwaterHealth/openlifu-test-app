"""SR-004: a calibration record's ``hwid`` must match the device it is
loaded onto, in the full 12-byte base58 form or, for units calibrated
earlier, the legacy 6-byte form (accepted with a warning).
"""

import logging
from unittest.mock import MagicMock

import pytest

from test_reports.test_reports import check_config_against_device

FULL = "FullForm12ByteId"
LEGACY = "Legacy6B"


def _ifx():
    ifx = MagicMock()
    ifx.get_sdk_version.return_value = "2.1.0"
    ifx.txdevice.get_version.return_value = "1.2.3"

    def get_hardware_id(module=0, raw_hex=False, base58=False, legacy_6byte=False):
        assert base58 and not raw_hex, "hwid must be compared in base58 form"
        return f"{LEGACY if legacy_6byte else FULL}-m{module}"

    ifx.txdevice.get_hardware_id.side_effect = get_hardware_id
    return ifx


def _config(**overrides):
    config = {"sdk_ver": "2.1.0", "fw_ver": "1.2.3"}
    config.update(overrides)
    return config


def test_full_form_hwid_matches_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert check_config_against_device(_ifx(), _config(hwid=f"{FULL}-m1"), module=1) is True
    assert caplog.records == []


def test_legacy_form_hwid_matches_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert check_config_against_device(_ifx(), _config(hwid=f"{LEGACY}-m1"), module=1) is True
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert "Module 1" in message
    assert f"{LEGACY}-m1" in message and f"{FULL}-m1" in message
    assert "legacy 6-byte" in message
    assert "recalibrate" in message and "full form" in message


@pytest.mark.parametrize("hwid", [f"{FULL}-m0", f"{LEGACY}-m0", "someone-else"])
def test_hwid_of_another_module_or_device_mismatches(hwid, caplog):
    with caplog.at_level(logging.WARNING):
        assert check_config_against_device(_ifx(), _config(hwid=hwid), module=1) is False
    assert "does not match" in caplog.text


@pytest.mark.parametrize("config", [_config(), _config(hwid=""), _config(hwid=None), _config(hwid=123)],
                         ids=["missing", "empty", "none", "non-string"])
def test_missing_or_invalid_hwid_mismatches(config):
    assert check_config_against_device(_ifx(), config) is False


def test_firmware_mismatch_still_fails_before_hwid_check():
    ifx = _ifx()
    assert check_config_against_device(ifx, _config(fw_ver="0.0.1", hwid=f"{FULL}-m0")) is False
    ifx.txdevice.get_hardware_id.assert_not_called()
