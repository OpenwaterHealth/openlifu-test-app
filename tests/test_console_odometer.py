"""Console odometer readout: formatting, old-SDK/old-firmware fallbacks, and
the ``queryHvOdometer`` slot's signal contract with the Console page."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openlifu_sdk.io.exceptions import LIFUDeviceError

from lifu.lifu_console import ConsoleMixin, format_duration, read_console_odometer
from lifu.lifu_support import LIFUSupportConnector


@pytest.mark.parametrize("seconds, text", [
    (0, "0 h 00 m 00 s"),
    (59, "0 h 00 m 59 s"),
    (3661, "1 h 01 m 01 s"),
    (1234 * 3600 + 5 * 60 + 9, "1,234 h 05 m 09 s"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_read_console_odometer_without_sdk_support_returns_none():
    # SDKs before the odometer have no HVController.get_odometer().
    assert read_console_odometer(SimpleNamespace()) is None


class _Host(ConsoleMixin):
    """The bits of LIFUConnector that queryHvOdometer relies on."""

    def __init__(self, hv):
        self.interface = SimpleNamespace(hvcontroller=hv)
        self._interface_mutex = MagicMock()
        self.hvOdometerReceived = MagicMock()


def test_query_emits_formatted_totals():
    hv = SimpleNamespace(get_odometer=lambda: SimpleNamespace(power_on_s=7261, hv_on_s=30))
    host = _Host(hv)
    host.queryHvOdometer()
    host.hvOdometerReceived.emit.assert_called_once_with("2 h 01 m 01 s", "0 h 00 m 30 s")
    host._interface_mutex.unlock.assert_called_once()


@pytest.mark.parametrize("hv", [
    SimpleNamespace(get_odometer=lambda: None),  # firmware predates the odometer
    SimpleNamespace(),                           # SDK predates the odometer
])
def test_query_emits_na_when_unsupported(hv):
    host = _Host(hv)
    host.queryHvOdometer()
    host.hvOdometerReceived.emit.assert_called_once_with("N/A", "N/A")
    host._interface_mutex.unlock.assert_called_once()


def test_query_device_error_is_logged_not_emitted():
    def _fail():
        raise LIFUDeviceError("HV: get_odometer returned device error")

    host = _Host(SimpleNamespace(get_odometer=_fail))
    host.queryHvOdometer()
    host.hvOdometerReceived.emit.assert_not_called()
    host._interface_mutex.unlock.assert_called_once()


def test_support_report_lists_odometer_rows():
    sysinfo = {"console": {"firmware_version": "1.2.8", "power_on_s": 7261, "hv_on_s": 30}}
    html = LIFUSupportConnector._build_report_html("2026-10-19 12:00:00", {}, sysinfo)
    assert "HV Power-On Time" in html and "2 h 01 m 01 s" in html
    assert "HV-On Time" in html and "0 h 00 m 30 s" in html


def test_support_report_omits_odometer_rows_when_unsupported():
    html = LIFUSupportConnector._build_report_html(
        "2026-10-19 12:00:00", {}, {"console": {"firmware_version": "1.2.7"}})
    assert "Power-On Time" not in html
