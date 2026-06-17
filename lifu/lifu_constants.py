"""Shared module-level constants for the lifu package.

Extracted from :mod:`lifu.lifu_connector` so that mixin modules
(:mod:`lifu.lifu_controller`, :mod:`lifu.lifu_transmitter`, etc.) can
import these names at module load time without creating a circular
dependency with ``lifu_connector`` (which imports the mixin classes).
"""
from __future__ import annotations

import functools
import logging
import re

logger = logging.getLogger(__name__)


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


# =============================================================================
# Firmware-compliance pins
# =============================================================================
# Per-device firmware compliance is computed from three values:
#
#   MIN_*_FW_VERSION                hard floor; older firmware locks out
#                                   the configure-device flows
#                                   (Configure on Controller; Write
#                                   Config and Add Device Configuration
#                                   on Settings) until updated.
#   packaged_*_fw_version()         the version of the firmware files
#                                   actually available through the
#                                   pinned ``openlifu-sdk`` (bundled
#                                   binaries, plus anything dropped into
#                                   the SDK's ``firmware/downloads/``
#                                   directory by a runtime updater).
#                                   Devices older than this (but >= the
#                                   minimum) get an advisory "Firmware
#                                   Update Available".
#
# ``validate_firmware_version_pins()`` runs at app startup and raises if
# either minimum exceeds what the SDK can actually deliver -- otherwise
# an operator could see "Firmware Update Required" without the bundled
# installer being able to satisfy it.
MIN_CONSOLE_FW_VERSION = "1.2.2"
MIN_TRANSMITTER_FW_VERSION = "2.0.3"

# Lowest device firmware version that supports being updated over the
# wire from the app (DFU/bootloader path used by
# ``updateConsoleFirmware`` / ``updateTransmitterFirmware``). Devices
# running anything older than this must be recovered with a hardware
# debugger / programmer -- attempting a firmware update from the app
# will brick or stall them. The Settings page blocks the update button
# and shows an error popup when the connected device reports a version
# below these pins.
MIN_DFU_CONSOLE_FW_VERSION = "1.2.3"
MIN_DFU_TRANSMITTER_FW_VERSION = "2.0.4"

# Compliance buckets surfaced to QML. Order matters: aggregate "worst"
# state across modules picks the numerically larger value.
FW_COMPLIANCE_OK = 0
FW_COMPLIANCE_UNKNOWN = 1
FW_COMPLIANCE_UPDATE_AVAILABLE = 2
FW_COMPLIANCE_UPDATE_REQUIRED = 3


@functools.lru_cache(maxsize=1)
def packaged_console_fw_version() -> str | None:
    """Latest console firmware version the pinned SDK can provide.

    Cached for the lifetime of the process: the SDK's bundled binaries
    don't change, and any newer file dropped into its
    ``firmware/downloads/`` between launches will be picked up on the
    next start. Returns ``None`` if the SDK isn't importable or doesn't
    expose any console firmware on disk -- callers must treat that as
    "unknown" and skip the update-available advisory.
    """
    try:
        from openlifu_sdk.util.firmware import get_console_firmware_version
        return get_console_firmware_version()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Could not read packaged console firmware version: %s", e)
        return None


@functools.lru_cache(maxsize=1)
def packaged_transmitter_fw_version() -> str | None:
    """Latest transmitter firmware version the pinned SDK can provide.

    See :func:`packaged_console_fw_version` for caching/error semantics.
    """
    try:
        from openlifu_sdk.util.firmware import get_transmitter_firmware_version
        return get_transmitter_firmware_version()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Could not read packaged transmitter firmware version: %s", e)
        return None


def parse_firmware_version(version_str):
    """Parse the first ``MAJOR.MINOR[.PATCH]`` triple from a string.

    Returns a 3-tuple of ints so comparisons are type-consistent across
    every call site (release tags, packaged firmware blobs, prefixed
    simulator strings like ``sim-1.0.7``, etc.). Returns ``None`` if
    no version-like substring is present. This matches the parser used
    inside ``openlifu_sdk.util.firmware``.
    """
    if not version_str:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(version_str))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def firmware_compliance(version_str, min_version, packaged_version):
    """Return the ``FW_COMPLIANCE_*`` bucket for ``version_str``.

    ``packaged_version`` may be ``None`` -- in that case the
    update-available advisory is skipped (the minimum-version check
    still runs).
    """
    parsed = parse_firmware_version(version_str)
    if parsed is None:
        return FW_COMPLIANCE_UNKNOWN
    parsed_min = parse_firmware_version(min_version)
    parsed_pkg = parse_firmware_version(packaged_version)
    if parsed_min is not None and parsed < parsed_min:
        return FW_COMPLIANCE_UPDATE_REQUIRED
    if parsed_pkg is not None and parsed < parsed_pkg:
        return FW_COMPLIANCE_UPDATE_AVAILABLE
    return FW_COMPLIANCE_OK


def validate_firmware_version_pins():
    """Sanity-check the firmware version pins. Raises ValueError on misconfig.

    Run at startup so a release that pins a minimum higher than the
    firmware versions the SDK can actually deliver fails fast (a
    developer/dependency guardrail) rather than locking operators out
    of configuration with an unfulfillable "Firmware Update Required"
    status.
    """
    for label, min_v, pkg_v, dfu_min_v in (
        (
            "console",
            MIN_CONSOLE_FW_VERSION,
            packaged_console_fw_version(),
            MIN_DFU_CONSOLE_FW_VERSION,
        ),
        (
            "transmitter",
            MIN_TRANSMITTER_FW_VERSION,
            packaged_transmitter_fw_version(),
            MIN_DFU_TRANSMITTER_FW_VERSION,
        ),
    ):
        parsed_min = parse_firmware_version(min_v)
        if parsed_min is None:
            raise ValueError(
                f"Invalid {label} minimum firmware version: {min_v!r}"
            )
        parsed_dfu_min = parse_firmware_version(dfu_min_v)
        if parsed_dfu_min is None:
            raise ValueError(
                f"Invalid {label} DFU minimum firmware version: {dfu_min_v!r}"
            )
        if pkg_v is None:
            raise ValueError(
                f"Could not determine the {label} firmware version "
                f"shipped by openlifu-sdk; cannot verify minimum "
                f"firmware pin {min_v!r}."
            )
        parsed_pkg = parse_firmware_version(pkg_v)
        if parsed_pkg is None:
            raise ValueError(
                f"openlifu-sdk reported an unparseable {label} firmware "
                f"version: {pkg_v!r}"
            )
        if parsed_min > parsed_pkg:
            raise ValueError(
                f"{label} minimum firmware version {min_v} exceeds the "
                f"version shipped by openlifu-sdk ({pkg_v}); bump the "
                f"SDK pin or lower MIN_{label.upper()}_FW_VERSION."
            )
