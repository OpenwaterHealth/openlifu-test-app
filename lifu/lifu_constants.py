"""Shared module-level constants for the lifu package.

Extracted from :mod:`lifu.lifu_connector` so that mixin modules
(:mod:`lifu.lifu_controller`, :mod:`lifu.lifu_transmitter`, etc.) can
import these names at module load time without creating a circular
dependency with ``lifu_connector`` (which imports the mixin classes).
"""
from __future__ import annotations

import logging
import re

# Import limits from SDK
from openlifu_sdk.io.LIFUTXDevice import MIN_PROFILE_SWITCH_INTERVAL, VALID_DELAY_PROFILES


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
# Multi-focus (rastered) sonication limits derived from SDK
# =============================================================================
#   MAX_FOCUS_POINTS              delay-RAM profile slots (one per focus)
#   MIN_PROFILE_SWITCH_INTERVAL_S inter-pulse dead time the firmware needs
#                                 to perform the SPI profile-switch writes
MAX_FOCUS_POINTS = len(VALID_DELAY_PROFILES)
MIN_PROFILE_SWITCH_INTERVAL_S = MIN_PROFILE_SWITCH_INTERVAL


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
#   packaged_*_fw_version()         the version of the signed images
#                                   the app ships in ``firmware/``
#                                   (``lifu/firmware_images.py``).
#                                   Devices older than this (but >= the
#                                   minimum) get an advisory "Firmware
#                                   Update Available".
#
# ``validate_firmware_version_pins()`` runs at app startup and raises if
# either minimum exceeds the shipped firmware -- otherwise an operator
# could see "Firmware Update Required" with no image able to satisfy it.
MIN_CONSOLE_FW_VERSION = "1.2.5"
MIN_TRANSMITTER_FW_VERSION = "2.0.7"

# The console only gained the user-config command (OW_CMD_USR_CFG) after
# 1.2.6 -- older consoles NAK it, which surfaced as a raw device error in
# the Settings page.
#
# NOTE: this is app-side on purpose for now. The authoritative home for
# "which firmware introduced this command" is openlifu-sdk (alongside
# LIFUUserConfig); move it there once the SDK grows a capability check.
MIN_CONSOLE_USER_CONFIG_FW_VERSION = "1.2.7"

# Compliance buckets surfaced to QML. Order matters: aggregate "worst"
# state across modules picks the numerically larger value.
FW_COMPLIANCE_OK = 0
FW_COMPLIANCE_UNKNOWN = 1
FW_COMPLIANCE_UPDATE_AVAILABLE = 2
FW_COMPLIANCE_UPDATE_REQUIRED = 3


def packaged_console_fw_version() -> str | None:
    """Version of the console firmware the app ships and installs.

    Read from the signed image's SBSFU header (``lifu/firmware_images.py``).
    openlifu-sdk 2.1.0-rc.4 and later carry no firmware, so the app ships
    the images itself. Returns ``None`` if the image is missing or
    unreadable -- callers must treat that as "unknown" and skip the
    update-available advisory.
    """
    from lifu import firmware_images
    return firmware_images.shipped_version(firmware_images.CONSOLE_SIGNED_APP)


def packaged_transmitter_fw_version() -> str | None:
    """Version of the transmitter firmware the app ships and installs.

    See :func:`packaged_console_fw_version`.
    """
    from lifu import firmware_images
    return firmware_images.shipped_version(firmware_images.TRANSMITTER_SIGNED_APP)


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


def console_supports_user_config(version_str) -> bool:
    """True if a console running ``version_str`` implements user config.

    Unreadable/unparseable versions return ``True``: a failed version
    query must not lock the operator out of the config editor. The
    attempt then fails on the device NAK, which is the same outcome as
    before this check existed.
    """
    parsed = parse_firmware_version(version_str)
    if parsed is None:
        return True
    floor = parse_firmware_version(MIN_CONSOLE_USER_CONFIG_FW_VERSION)
    return floor is None or parsed >= floor


def validate_firmware_version_pins():
    """Sanity-check the firmware version pins. Raises ValueError on misconfig.

    Run at startup so a release that pins a minimum higher than the
    firmware the app ships in ``firmware/`` fails fast (a
    developer/dependency guardrail) rather than locking operators out
    of configuration with an unfulfillable "Firmware Update Required"
    status.
    """
    for label, min_v, pkg_v in (
        ("console", MIN_CONSOLE_FW_VERSION, packaged_console_fw_version()),
        ("transmitter", MIN_TRANSMITTER_FW_VERSION, packaged_transmitter_fw_version()),
    ):
        parsed_min = parse_firmware_version(min_v)
        if parsed_min is None:
            raise ValueError(
                f"Invalid {label} minimum firmware version: {min_v!r}"
            )
        if pkg_v is None:
            raise ValueError(
                f"Could not determine the version of the {label} "
                f"firmware shipped with the app (firmware/); cannot "
                f"verify minimum firmware pin {min_v!r}."
            )
        parsed_pkg = parse_firmware_version(pkg_v)
        if parsed_pkg is None:
            raise ValueError(
                f"The {label} firmware shipped with the app has an "
                f"unparseable version: {pkg_v!r}"
            )
        if parsed_min > parsed_pkg:
            raise ValueError(
                f"{label} minimum firmware version {min_v} exceeds the "
                f"version shipped with the app ({pkg_v}); update the "
                f"image in firmware/ or lower MIN_{label.upper()}_FW_VERSION."
            )
