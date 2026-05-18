"""Compatibility shim.

The simulated LIFU device interface has moved into the SDK at
``openlifu_sdk.ui.simulated_interface``. New code should import from there.
This module is preserved only so that existing imports of
``lifu.simulated_interface`` keep working.
"""

from __future__ import annotations

from openlifu_sdk.ui.simulated_interface import (
    SimulatedHVController,
    SimulatedLIFUInterface,
    SimulatedTxDevice,
)
from openlifu_sdk.ui.status_frame import format_status_frame as _format_status_frame

__all__ = [
    "SimulatedHVController",
    "SimulatedLIFUInterface",
    "SimulatedTxDevice",
    "_format_status_frame",
]
