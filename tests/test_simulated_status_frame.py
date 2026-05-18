"""Round-trip the simulator's STATUS-frame format through the connector's
parser. If this test fails, the unsolicited progress signal in
``--simulate`` mode will silently stop driving the UI.
"""

import re

import pytest

from lifu.simulated_interface import _format_status_frame


# Re-implementation of the `pattern_with_pulse` regex from
# ``LIFUConnector.parse_status_string`` so the test doesn't have to spin
# up Qt to import the connector.
_PARSER_RE = re.compile(
    r"STATUS:(\w+),"
    r"MODE:(\w+),"
    r"PULSE_TRAIN:\[(\d+)/(\d+)\],"
    r"PULSE:\[(\d+)/(\d+)\],"
    r"TEMP_TX:([0-9.]+),"
    r"TEMP_AMBIENT:([0-9.]+)"
)


@pytest.mark.parametrize("status", ["RUNNING", "STOPPED"])
def test_status_frame_round_trips_through_parser(status):
    frame = _format_status_frame(
        pt_curr=7, pt_total=120, p_curr=0, p_total=0,
        temp_tx=42.13, temp_amb=25.04, status=status,
    )
    m = _PARSER_RE.match(frame)
    assert m is not None, f"Frame did not match parser regex: {frame!r}"
    assert m.group(1) == status
    assert m.group(3) == "7"
    assert m.group(4) == "120"
    assert float(m.group(7)) == pytest.approx(42.13)
    assert float(m.group(8)) == pytest.approx(25.04)
