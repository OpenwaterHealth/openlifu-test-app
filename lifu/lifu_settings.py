"""Settings-page methods for :class:`LIFUConnector`.

Currently extracts firmware-update + user-config + test-report methods,
which form a clean cluster bound by Settings.qml. Solution-export /
path helpers are also Settings-page concerns but are tightly coupled
with controller-tab solution-loading logic, so they remain in
``lifu_connector.py`` for now.

Mixed into :class:`~lifu.lifu_connector.LIFUConnector` so Settings.qml
can bind to these slots as if they were defined on the connector.

Signals consumed (defined on ``LIFUConnector``):
    - ``fwUpdateProgress`` (str, int, int)
    - ``fwUpdateStatus`` (str, bool, str)
    - ``fwVersionRead`` (str, str)
    - ``firmwareCheckStatus`` (str, str, str, str, str)
    - ``userConfigRead`` (str, str)
    - ``userConfigStatus`` (str, bool, str)
    - ``testReportLoaded`` (bool, str)

Instance attributes consumed (initialized in ``LIFUConnector.__init__``):
    - ``interface``, ``_interface_mutex``
    - ``_txConnected``
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
import time

from PyQt6.QtCore import pyqtSlot

from openlifu_sdk.io.exceptions import LIFUError
from test_reports.test_reports import (
    check_config_against_device,
    read_test_report,
    test_report_to_config,
)

logger = logging.getLogger(__name__)


def _parse_tx_module(target: str):
    """Parse a target string like 'tx 0', 'tx_0', 'tx0' into an integer module index.
    Returns None if the target is not a TX target (e.g. 'console').
    """
    m = re.match(r"^tx[\s_]?(\d+)$", target.strip().lower())
    if m:
        return int(m.group(1))
    return None


class SettingsMixin:
    """Mixin providing the Settings-page slots/helpers for ``LIFUConnector``."""

    # ------------------------------------------------------------------
    # Firmware update
    # ------------------------------------------------------------------

    @pyqtSlot(str, result=str)
    def getDefaultFirmwarePath(self, device_type: str) -> str:
        """Return the SDK's preferred firmware file for ``device_type``.

        Delegates to ``openlifu_sdk.util.firmware`` so the path picks up
        anything dropped into the SDK's ``firmware/downloads/`` directory
        by ``checkLatest*Firmware`` -- otherwise the operator would only
        see the wheel-bundled binaries here. Falls back to the canonical
        bundled filename if the SDK can't be queried.
        """
        try:
            from openlifu_sdk.util import firmware as sdk_fw
            if device_type == "console":
                return str(sdk_fw.get_console_firmware_path())
            if device_type == "transmitter":
                return str(sdk_fw.get_transmitter_firmware_path())
            return ""
        except Exception as e:
            logger.warning(
                "openlifu-sdk firmware path lookup failed for %s; "
                "falling back to the bundled filename: %s",
                device_type, e,
            )
        try:
            spec = importlib.util.find_spec("openlifu_sdk")
            if spec is None or spec.origin is None:
                return ""
            fw_dir = os.path.join(os.path.dirname(spec.origin), "firmware")
            names = {
                "console": "openlifu-console-fw.signed.bin",
                "transmitter": "openlifu-transmitter-fw.signed.bin",
            }
            name = names.get(device_type, "")
            return os.path.join(fw_dir, name) if name else ""
        except Exception as e:
            logger.error(f"Error locating default firmware for {device_type}: {e}")
            return ""

    @pyqtSlot(str, result=str)
    def getFirmwareFileVersion(self, path: str) -> str:
        """Return the ``MAJOR.MINOR.PATCH`` substring embedded in a
        firmware blob.

        Returns ``""`` if ``path`` is empty/missing/unreadable, so the
        QML side can suppress the version label without special-casing
        every error mode. Uses the SDK's parser to stay byte-for-byte
        compatible with what the device-side firmware update accepts.
        """
        if not path:
            return ""
        try:
            from openlifu_sdk.util.firmware import _get_firmware_version
            return _get_firmware_version(path)
        except Exception as e:
            logger.debug("Could not read firmware version from %s: %s", path, e)
            return ""

    @pyqtSlot()
    def checkLatestConsoleFirmware(self) -> None:
        """Poll GitHub for a newer console firmware build, downloading
        it if found. Status pushed via :attr:`firmwareCheckStatus`.

        Runs in a background thread so the QML "Checking…" /
        "Downloading…" popup can render and the Qt event loop stays
        responsive while the network calls are in flight.
        """
        self._check_and_download_firmware("console")

    @pyqtSlot()
    def checkLatestTransmitterFirmware(self) -> None:
        """Poll GitHub for a newer transmitter firmware build,
        downloading it if found. See :meth:`checkLatestConsoleFirmware`.
        """
        self._check_and_download_firmware("transmitter")

    def _check_and_download_firmware(self, device_type: str) -> None:
        """Worker for ``checkLatest{Console,Transmitter}Firmware``.

        Phases (emitted via :attr:`firmwareCheckStatus`):
          ``checking``   -- contacting GitHub for the latest release
          ``downloading``-- a newer release was found; fetching it
          ``uptodate``   -- already on (or ahead of) the latest release
          ``done``       -- newer release downloaded; ``newPath`` /
                            ``newVersion`` populated so the UI can
                            retarget the firmware file field
          ``error``      -- any failure (network, missing asset, parse).

        ``packaged_*_fw_version`` is cache-cleared on success so the
        next compliance check sees the freshly downloaded binary.
        """
        from lifu.lifu_constants import (
            packaged_console_fw_version,
            packaged_transmitter_fw_version,
        )

        def _run():
            try:
                from openlifu_sdk.util import firmware as sdk_fw
            except Exception as e:
                self.firmwareCheckStatus.emit(
                    device_type, "error", f"openlifu-sdk unavailable: {e}", "", "",
                )
                return

            # The SDK wraps GitHub network failures in
            # ``FirmwareNetworkError`` (with the urllib3 traceback
            # suppressed) so we can render a clean "check your internet
            # connection" message and skip the noisy stack trace. Older
            # SDK builds may not export it; fall back to a sentinel
            # tuple so ``isinstance`` always works.
            FirmwareNetworkError = getattr(sdk_fw, "FirmwareNetworkError", ())

            def _is_network_error(exc: BaseException) -> bool:
                return bool(FirmwareNetworkError) and isinstance(exc, FirmwareNetworkError)

            if device_type == "console":
                check_latest = sdk_fw.check_latest_console_firmware_version
                get_current = sdk_fw.get_console_firmware_version
                get_path = sdk_fw.get_console_firmware_path
                download = sdk_fw.download_latest_console_firmware
                cache_clear = packaged_console_fw_version.cache_clear
                label = "Console"
            elif device_type == "transmitter":
                check_latest = sdk_fw.check_latest_transmitter_firmware_version
                get_current = sdk_fw.get_transmitter_firmware_version
                get_path = sdk_fw.get_transmitter_firmware_path
                download = sdk_fw.download_latest_transmitter_firmware
                cache_clear = packaged_transmitter_fw_version.cache_clear
                label = "Transmitter"
            else:
                self.firmwareCheckStatus.emit(
                    device_type, "error", f"Unknown device type: {device_type}", "", "",
                )
                return

            self.firmwareCheckStatus.emit(
                device_type, "checking",
                f"Checking GitHub for the latest {label} firmware…",
                "", "",
            )

            try:
                current_version = get_current()
                latest_version = check_latest()
            except Exception as e:
                if _is_network_error(e):
                    # Network failures get a one-line warning -- the
                    # SDK already logged the offending URL and there's
                    # no useful stack trace to show.
                    logger.warning("Latest %s firmware lookup failed: %s", label, e)
                    msg = (
                        f"Could not reach GitHub to check for {label} "
                        f"firmware updates. Check your internet connection."
                    )
                else:
                    logger.exception("Latest %s firmware lookup failed", label)
                    msg = f"Could not check for {label} firmware updates: {e}"
                self.firmwareCheckStatus.emit(
                    device_type, "error", msg, "", "",
                )
                return

            from lifu.lifu_constants import parse_firmware_version
            latest_tuple = parse_firmware_version(latest_version)
            current_tuple = parse_firmware_version(current_version)
            # check_latest_*_firmware_version() returns the existing
            # local version when nothing newer is on GitHub, so an
            # equal-version response is the up-to-date case. If
            # ``current`` is unparseable (e.g. mocked SDK returning
            # garbage) we still accept the new build as an upgrade.
            already_current = (
                latest_tuple is None
                or (current_tuple is not None and latest_tuple <= current_tuple)
            )
            if already_current:
                try:
                    current_path = str(get_path())
                except Exception:
                    current_path = ""
                self.firmwareCheckStatus.emit(
                    device_type, "uptodate",
                    f"{label} firmware is already up to date "
                    f"(have v{current_version}).",
                    current_path, current_version,
                )
                return

            self.firmwareCheckStatus.emit(
                device_type, "downloading",
                f"Downloading {label} firmware v{latest_version}…",
                "", latest_version,
            )

            try:
                downloaded = download()
            except Exception as e:
                if _is_network_error(e):
                    logger.warning("%s firmware download failed: %s", label, e)
                    msg = (
                        f"Could not reach GitHub to download {label} "
                        f"firmware. Check your internet connection."
                    )
                else:
                    logger.exception("%s firmware download failed", label)
                    msg = f"{label} firmware download failed: {e}"
                self.firmwareCheckStatus.emit(
                    device_type, "error", msg, "", "",
                )
                return

            if downloaded is None:
                # download_latest_*_firmware returns None for both
                # "already current" and "asset missing" -- the SDK
                # logged the discriminator. Surface a generic message
                # rather than guessing.
                self.firmwareCheckStatus.emit(
                    device_type, "error",
                    f"{label} firmware download did not produce a file. "
                    f"See application logs for details.",
                    "", "",
                )
                return

            cache_clear()
            try:
                new_version = sdk_fw._get_firmware_version(downloaded)
            except Exception:
                new_version = latest_version
            self.firmwareCheckStatus.emit(
                device_type, "done",
                f"Downloaded {label} firmware v{new_version}.",
                str(downloaded), new_version,
            )

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(result=str)
    def readHvFirmwareVersion(self) -> str:
        """Read and return the current console (HV) firmware version.

        Cached after first call; cleared on HV disconnect or after a
        successful console firmware update.
        """
        if self._cached_hv_fw_version is not None:
            self.fwVersionRead.emit("console", self._cached_hv_fw_version)
            self._update_firmware_compliance("HV", self._cached_hv_fw_version)
            return self._cached_hv_fw_version
        self._interface_mutex.lock()
        try:
            version = self.interface.hvcontroller.get_version()
            self._cached_hv_fw_version = version
            self.fwVersionRead.emit("console", version)
            self._update_firmware_compliance("HV", version)
            logger.info(f"Console firmware version: {version}")
            return version
        except LIFUError as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context="Failed to read console firmware version")
            self.fwVersionRead.emit("console", "Error")
            self._update_firmware_compliance("HV", None)
            return "Error"
        except Exception as e:
            self._handle_lifu_error("Firmware Version", e, context="Unexpected error")
            self.fwVersionRead.emit("console", "Error")
            self._update_firmware_compliance("HV", None)
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int, result=str)
    def readTxFirmwareVersion(self, module: int) -> str:
        """Read and return the current transmitter firmware version for a given module.

        Cached per-module after first call; cleared on TX disconnect or
        after a successful transmitter firmware update.
        """
        cached = self._cached_tx_fw_version.get(module)
        if cached is not None:
            self.fwVersionRead.emit(f"transmitter_{module}", cached)
            self._update_firmware_compliance("TX", cached, module=module)
            return cached
        self._interface_mutex.lock()
        try:
            version = self.interface.txdevice.get_version(module=module)
            self._cached_tx_fw_version[module] = version
            self.fwVersionRead.emit(f"transmitter_{module}", version)
            self._update_firmware_compliance("TX", version, module=module)
            logger.info(f"Transmitter module {module} firmware version: {version}")
            return version
        except LIFUError as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context=f"Failed to read transmitter module {module} firmware version")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            self._update_firmware_compliance("TX", None, module=module)
            return "Error"
        except Exception as e:
            self._handle_lifu_error("Firmware Version", e,
                                    context=f"Module {module} unexpected error")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            self._update_firmware_compliance("TX", None, module=module)
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str)
    def updateConsoleFirmware(self, firmware_path: str) -> None:
        """Update the console (HV) firmware using DFU.  Runs in a background thread."""
        def _run():
            # The DFU sequence reboots the device into the bootloader; any
            # polling round-trip in flight against the soon-to-disappear
            # CDC interface causes spurious comm timeouts. Pause polling
            # for the duration of the update.
            prev_paused = self._monitoring_paused
            self._monitoring_paused = True
            try:
                from openlifu_sdk.io.LIFUDFU import LIFUDFUManager

                def _progress(written: int, total: int, label: str) -> None:
                    self.fwUpdateProgress.emit(label, written, total)

                self.fwUpdateStatus.emit("console", False, "Starting console firmware update…")
                logger.info(f"Console firmware update: {firmware_path}")
                mgr = LIFUDFUManager(uart=self.interface.hvcontroller.uart)
                mgr.update_module(
                    module=0,
                    package_file=firmware_path,
                    enter_dfu_fn=self.interface.hvcontroller.enter_dfu,
                    vid=0x0483,
                    pid=0xDF11,
                    libusb_dll=None,
                    dfu_wait_s=5.0,
                    device_type="console",
                    progress_callback=_progress,
                )
                self.fwUpdateStatus.emit("console", True, "Console firmware update complete.")
                logger.info("Console firmware update complete.")
                # New firmware -> cached version/HWID is stale.
                self._invalidate_device_caches("HV")
            except Exception as e:
                msg = f"Console update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("console", False, msg)
            finally:
                self._monitoring_paused = prev_paused

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, int)
    def updateTransmitterFirmware(self, firmware_path: str, module: int) -> None:
        """Update the transmitter firmware for a specific module. Runs in a background thread."""
        def _run():
            # Pause polling for the duration -- see updateConsoleFirmware
            # for rationale.
            prev_paused = self._monitoring_paused
            self._monitoring_paused = True
            self._interface_mutex.lock()
            try:
                def _progress(written: int, total: int, label: str) -> None:
                    self.fwUpdateProgress.emit(label, written, total)

                self.fwUpdateStatus.emit("transmitter", False, f"Starting transmitter firmware update for module {module}…")
                logger.info(f"Transmitter module {module} firmware update: {firmware_path}")
                self.interface.txdevice.update_firmware(
                    module=module,
                    package_file=firmware_path,
                    vid=0x0483,
                    pid=0xDF11,
                    libusb_dll=None,
                    dfu_wait_s=5.0,
                    device_type="transmitter",
                    progress_callback=_progress,
                )
                self.fwUpdateStatus.emit("transmitter", True, f"Transmitter module {module} firmware update complete.")
                logger.info(f"Transmitter module {module} firmware update complete.")
                # New firmware -> cached per-module FW/HWID is stale.
                self._invalidate_device_caches("TX")
            except Exception as e:
                msg = f"Transmitter module {module} update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("transmitter", False, msg)
            finally:
                self._interface_mutex.unlock()
                self._monitoring_paused = prev_paused

                # Power cycle 12v to ensure correct enumeration.
                self.interface.hvcontroller.turn_12v_off()
                time.sleep(3)
                self.interface.hvcontroller.turn_12v_on()
                time.sleep(3)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # User config (read/write JSON to TX module EEPROM)
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def readUserConfig(self, target: str) -> None:
        """Read user configuration from the target device. Emits userConfigRead on success.

        target: "console" (reserved, not yet supported) or "tx_N" / "tx N" (module N).
        """
        def _run():
            self._interface_mutex.lock()
            try:
                module = _parse_tx_module(target)
                if module is None:
                    # Console not yet supported
                    self.userConfigStatus.emit(target, False, f"Unsupported target: {target}")
                    return

                config = self.interface.txdevice.read_config(module=module)
                json_str = config.get_json_str()
                logger.info(f"User config read from {target}: {json_str}")
                self.userConfigRead.emit(target, json_str)
            except LIFUError as e:
                msg = f"{str(e)}"
                logger.error(f"Error reading config from {target}: {msg}")
                self.userConfigStatus.emit(target, False, msg)
            except Exception as e:
                msg = f"Error reading config from {target}: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, str)
    def writeUserConfig(self, target: str, json_str: str) -> None:
        """Write user configuration JSON to the target device.

        target: "console" (reserved, not yet supported) or "tx_N" / "tx N" (module N).
        """
        def _run():
            self._interface_mutex.lock()
            try:
                module = _parse_tx_module(target)
                if module is None:
                    self.userConfigStatus.emit(target, False, f"Unsupported target: {target}")
                    return

                updated = self.interface.txdevice.write_config_json(json_str, module=module)
                msg = f"Config written to {target}. Seq: {updated.header.seq}, CRC: 0x{updated.header.crc:04X}"
                logger.info(msg)
                self.userConfigStatus.emit(target, True, msg)
            except json.JSONDecodeError as e:
                msg = f"Invalid JSON: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            except LIFUError as e:
                msg = f"{str(e)}"
                logger.error(f"Error writing config to {target}: {msg}")
                self.userConfigStatus.emit(target, False, msg)
            except Exception as e:
                msg = f"Error writing config to {target}: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Test report load + validation
    # ------------------------------------------------------------------

    @pyqtSlot(str, str)
    def loadTestReport(self, file_path, target):
        """Load and validate test report against specified TXM module"""
        def _run():
            try:
                # Parse the target to get module number
                module = _parse_tx_module(target)
                if module is None:
                    self.testReportLoaded.emit(False, f"Unsupported target: {target}")
                    return

                # Convert file URL to local path
                if file_path.startswith("file:///"):
                    file_path_clean = file_path[8:]  # Remove file:/// prefix
                elif file_path.startswith("file://"):
                    file_path_clean = file_path[7:]  # Remove file:// prefix
                else:
                    file_path_clean = file_path

                logger.info(f"Loading test report from: {file_path_clean} for {target}")

                # Read the test report
                report_df = read_test_report(file_path_clean)
                config = test_report_to_config(report_df)

                # Extract report information
                report_sn = config.get('sn', 'Unknown')
                report_hwid = config.get('hwid', 'Unknown')
                report_freq = config.get('freq', 'Unknown')

                report_info = f"SN: {report_sn}, HWID: {report_hwid}, Freq: {report_freq} kHz"

                # Check if we have a connected TXM to compare against

                if self._txConnected:
                    self._interface_mutex.lock()
                    try:
                        # Check against specified module
                        check_result = check_config_against_device(self.interface, config, module=module)
                        if check_result is not False:  # None means warnings but valid, False means mismatch
                            # Convert config to JSON string and populate User Config editor
                            json_str = json.dumps(config, indent=2)
                            self.userConfigRead.emit(target, json_str)

                            message = f"Test report matches {target}! {report_info} - Config loaded into editor."
                            self.testReportLoaded.emit(True, message)
                        else:
                            message = f"Test report does NOT match {target}. Report: {report_info}"
                            self.testReportLoaded.emit(False, message)
                    except Exception as e:
                        logger.warning(f"Could not verify report against device: {e}")
                        message = f"Test report loaded but could not verify against {target}: {e}"
                        self.testReportLoaded.emit(False, message)
                    finally:
                        self._interface_mutex.unlock()
                else:
                    message = f"Test report loaded. No TXM connected for verification. {report_info}"
                    self.testReportLoaded.emit(False, message)

            except Exception as e:
                error_msg = f"Failed to load test report: {str(e)}"
                logger.error(error_msg)
                self.testReportLoaded.emit(False, error_msg)

        threading.Thread(target=_run, daemon=True).start()


__all__ = ["SettingsMixin"]
