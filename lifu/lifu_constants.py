"""Shared module-level constants for the lifu package.

Extracted from :mod:`lifu.lifu_connector` so that mixin modules
(:mod:`lifu.lifu_controller`, :mod:`lifu.lifu_transmitter`, etc.) can
import these names at module load time without creating a circular
dependency with ``lifu_connector`` (which imports the mixin classes).
"""
from __future__ import annotations


# =============================================================================
# Application-level state machine surfaced to QML
# =============================================================================
DISCONNECTED = 0
CONNECTED = 1            # TX device connected, no solution configured
READY = 2                # TX configured with a solution; ready to start
RUNNING = 3              # Sonication or verification test in progress
TEST_SCRIPT_READY = 4    # HV connected without TX (verification scripts)


# =============================================================================
# HV enable modes
# =============================================================================
HV_EN_AUTO = 0
HV_EN_ON = 1
HV_EN_OFF = 2
HV_EN_WHILE_RUNNING = 3
HV_EN_MODES = {
    HV_EN_AUTO: "AUTO",
    HV_EN_ON: "ON",
    HV_EN_OFF: "OFF",
    HV_EN_WHILE_RUNNING: "WHILE_RUNNING",
}


# =============================================================================
# Thermal-management thresholds (degrees C, hottest-module)
# =============================================================================
THERMAL_COOLING_THRESHOLD_C = 50.0
THERMAL_SHUTDOWN_THRESHOLD_C = 75.0


# =============================================================================
# Physics / hardware constants
# =============================================================================
SPEED_OF_SOUND = 1500  # m/s, used for time-of-flight calculations
NUM_ELEMENTS_PER_MODULE = 64  # Each TX module has 64 elements


# =============================================================================
# Retry policy
# =============================================================================
# How many extra times a device-write should be retried after a transient
# ``LIFUCommunicationError`` (timeout) before surfacing the error to the
# user. With MAX_TIMEOUT_RETRIES=3 we attempt a write up to 4 times total.
MAX_TIMEOUT_RETRIES = 3


# =============================================================================
# Run-scoped log formatting
# =============================================================================
RUN_LOG_FORMAT = "%(asctime)s [+%(elapsed)8.3fs] %(levelname)-7s %(name)s: %(message)s"
RUN_LOG_DATEFMT = "%H:%M:%S"
