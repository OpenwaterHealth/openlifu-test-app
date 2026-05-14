"""Console-page slots mixed into :class:`LIFUConnector`.

This mixin holds the HV/console-side device queries, power and fan
control, ping/echo/LED diagnostics (for both HV and TX targets), and
the telemetry-polling pause/async-mode helpers used by the Console
and Support pages.

Mixed into :class:`LIFUConnector` via MRO; relies on the host class
to provide ``self.interface``, ``self._interface_mutex``, the
relevant ``pyqtSignal``\\ s, ``_handle_lifu_error``,
``_emit_device_error``, and the connection-state attributes.
"""
from __future__ import annotations

import base58
import json
import logging

from PyQt6.QtCore import pyqtSlot

from openlifu_sdk.io.exceptions import LIFUError
from openlifu_sdk.io.LIFUConfig import HW_ID_DATA_LENGTH

logger = logging.getLogger(__name__)

__all__ = ["ConsoleMixin"]


class ConsoleMixin:
    """Console/HV-side device commands and diagnostics."""

    @pyqtSlot()
    def queryHvInfo(self):
        """Fetch and emit device information."""
        self._interface_mutex.lock()
        try:
            fw_version = self.interface.hvcontroller.get_version()
            hw_id = self.interface.hvcontroller.get_hardware_id(raw_hex=True)
            if hw_id:
                if len(hw_id) > HW_ID_DATA_LENGTH:
                    hw_id = base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                device_id = hw_id
            else:
                device_id = 'N/A'
            self.hvDeviceInfoReceived.emit(fw_version, device_id)
            logger.info(f"Console - Firmware Version: {fw_version}, HWID: {device_id}")
        except LIFUError as e:
            self._handle_lifu_error("Console Info", e)
        except Exception as e:
            self._handle_lifu_error("Console Info", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryHvTemperature(self):
        """Fetch and emit temperature data."""
        self._interface_mutex.lock()
        try:
            temp1 = self.interface.hvcontroller.get_temperature1()
            temp2 = self.interface.hvcontroller.get_temperature2()
            self.temperatureHvUpdated.emit(temp1, temp2)
            logger.debug(f"Temperature Data - Temp1: {temp1}, Temp2: {temp2}")
        except LIFUError as e:
            # Avoid popups for periodic polling; log only.
            logger.warning(f"Failed to read console temperatures: {e}")
        except Exception as e:
            logger.error(f"Error querying temperature data: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int)
    def setRGBState(self, state):
        """Set the RGB state using integer values."""
        self._interface_mutex.lock()
        try:
            valid_states = [0, 1, 2, 3]
            if state not in valid_states:
                self._emit_device_error("Set RGB State", f"Invalid RGB state value: {state}")
                return
            self.interface.hvcontroller.set_rgb_led(state)
            logger.info(f"RGB state set to: {state}")
        except LIFUError as e:
            self._handle_lifu_error("Set RGB State", e)
            # Re-query so the UI snaps back to the hardware's real state.
            self.queryRGBState()
        except Exception as e:
            self._handle_lifu_error("Set RGB State", e, context="Unexpected error")
            self.queryRGBState()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryRGBState(self):
        """Fetch and emit RGB state."""
        self._interface_mutex.lock()
        try:
            state = self.interface.hvcontroller.get_rgb_led()
            state_text = {0: "Off", 1: "Red", 2: "Green", 3: "Blue"}.get(state, "Unknown")
            logger.info(f"RGB State: {state_text}")
            self.rgbStateReceived.emit(state, state_text)
        except LIFUError as e:
            self._handle_lifu_error("RGB State", e)
        except Exception as e:
            self._handle_lifu_error("RGB State", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryPowerStatus(self):
        """Fetch and emit HV state."""
        self._interface_mutex.lock()
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
            self._hv_poll_failures = 0  # reset on success
        except LIFUError as e:
            # Don't emit a popup on poll failures – log only and let the
            # poll loop detect consecutive failures and trigger disconnect.
            self._hv_poll_failures += 1
            logger.warning(f"Power Status poll failure ({self._hv_poll_failures}): {e}")
        except Exception as e:
            self._handle_lifu_error("Power Status", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    def _set_async_mode(self, enable: bool, reason: str = "") -> bool:
        """Enable/disable the TX device's unsolicited STATUS stream.

        This is the only place that should call ``txdevice.async_mode()``.
        It serializes the toggle through the interface mutex, verifies the
        device echoed the requested state, and tracks the result on
        ``self._async_mode_enabled``. It is a no-op (and returns the
        cached state) when the TX device is not connected.

        Args:
            enable: target state.
            reason: short tag for the log line, for traceability.

        Returns:
            True if the device confirmed the requested state, False
            otherwise. Communication failures are logged but not raised
            so callers can use this in finally blocks without masking
            the original exception.
        """
        if not self._txConnected:
            self._async_mode_enabled = False
            return not enable
        if self._async_mode_enabled == enable:
            return True
        self._interface_mutex.lock()
        try:
            reported = self.interface.txdevice.async_mode(enable)
            if reported == enable:
                self._async_mode_enabled = enable
                tag = f" ({reason})" if reason else ""
                logger.debug(f"Async mode -> {enable}{tag}")
                return True
            logger.warning(
                f"TX device did not accept async mode {enable} "
                f"(reported {reported}); reason={reason}"
            )
            self._async_mode_enabled = bool(reported)
            return False
        except LIFUError as e:
            logger.warning(f"Async mode toggle failed ({reason}): {e}")
            return False
        except Exception as e:
            logger.warning(f"Async mode toggle failed ({reason}): {e}")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(bool)
    def pauseMonitoring(self, pause: bool):
        """Pause or resume background telemetry polling.

        Call with ``True`` while the diagnostics tab is active so that the
        poll thread does not race hardware calls made by the diagnostic tests.
        Call with ``False`` when leaving the diagnostics tab to resume normal
        telemetry.
        """
        self._monitoring_paused = pause
        logger.info("Telemetry polling %s", "PAUSED" if pause else "RESUMED")

    @pyqtSlot(bool)
    def setAsyncMode(self, enable: bool):
        """QML/diagnostic slot to manually toggle the TX async stream.

        Routes through :meth:`_set_async_mode` so the connector's tracked
        state stays consistent with the device.
        """
        if not self._set_async_mode(enable, reason="manual"):
            self._emit_device_error(
                "Async Mode",
                f"Device did not accept async mode {enable}.",
            )

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, target: str, index: int = 0):
        """Send a ping command to HV device."""
        self._interface_mutex.lock()
        try:
            if target == "HV":
                self.interface.hvcontroller.ping()
                logger.info("HV ping command sent successfully")
                return True
            elif target == "TX":
                self.interface.txdevice.ping(module=index)
                logger.info(f"TX module {index} ping command sent successfully")
                return True
            else:
                self._emit_device_error("Ping", f"Invalid target for ping command: {target}")
                return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("Ping", e, context=f"{label} ping failed")
            return False
        except Exception as e:
            self._handle_lifu_error("Ping", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendLedToggleCommand(self, target: str, index: int = 0):
        """Send a LED Toggle command to device."""
        self._interface_mutex.lock()
        try:
            if target == "HV":
                self.interface.hvcontroller.toggle_led()
                logger.info("HV LED toggle command sent successfully")
                return True
            elif target == "TX":
                self.interface.txdevice.toggle_led(module=index)
                logger.info(f"TX module {index} LED toggle command sent successfully")
                return True
            else:
                self._emit_device_error("LED Toggle", f"Invalid target for toggle command: {target}")
                return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("LED Toggle", e, context=f"{label} LED toggle failed")
            return False
        except Exception as e:
            self._handle_lifu_error("LED Toggle", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendEchoCommand(self, target: str, index: int = 0):
        """Send Echo command to device."""
        self._interface_mutex.lock()
        try:
            expected_data = b"Hello FROM Test Application!"
            if target == "HV":
                echoed_data, data_len = self.interface.hvcontroller.echo(echo_data=expected_data)
            elif target == "TX":
                echoed_data, data_len = self.interface.txdevice.echo(echo_data=expected_data, module=index)
            else:
                self._emit_device_error("Echo", f"Invalid target for echo command: {target}")
                return False

            if echoed_data == expected_data and data_len == len(expected_data):
                logger.info("Echo command successful - Data matched")
                return True
            self._emit_device_error("Echo", "Echo command failed - data mismatch.")
            return False
        except LIFUError as e:
            label = "HV" if target == "HV" else f"TX module {index}"
            self._handle_lifu_error("Echo", e, context=f"{label} echo failed")
            return False
        except Exception as e:
            self._handle_lifu_error("Echo", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def setHVCommand(self, strval: str):
        """Set High voltage command to device."""
        self._interface_mutex.lock()
        try:
            voltage = float(strval)
            self.interface.hvcontroller.set_voltage(voltage=voltage)
            logger.info("Voltage set successfully")
            return True
        except ValueError as e:
            self._emit_device_error("Set HV Voltage", f"Invalid voltage value '{strval}': {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set HV Voltage", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set HV Voltage", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int, int, result=bool)
    def setFanLevel(self, fid: int, speed: int):
        """Set Fan Level to device."""
        self._interface_mutex.lock()
        try:
            result = self.interface.hvcontroller.set_fan_speed(fan_id=fid, fan_speed=speed)
            if result == speed:
                logger.info("Fan set successfully")
                return True
            self._emit_device_error(
                "Set Fan Speed",
                f"Fan {fid} did not accept speed {speed}% (reported {result})."
            )
            return False
        except ValueError as e:
            self._emit_device_error("Set Fan Speed", f"Invalid fan parameters: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Fan Speed", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Fan Speed", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def softResetHV(self):
        """reset hardware HV device."""
        self._interface_mutex.lock()
        try:
            self.interface.hvcontroller.soft_reset()
            logger.info("Software Reset Sent")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset HV", e)
        except Exception as e:
            self._handle_lifu_error("Soft Reset HV", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleHV(self):
        """Toggle HV on console."""
        self._interface_mutex.lock()
        try:
            # Check the current state of HV
            current_hv = self.interface.hvcontroller.get_hv_status()

            if current_hv:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off successfully")
            else:
                self.interface.hvcontroller.turn_hv_on()
                logger.info("HV turned on successfully")

            # Re-query the hardware and emit the real state.
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Toggle HV", e)
            # Refresh UI with whatever the hardware currently reports.
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Toggle HV", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def turnOffHV(self):
        """Turn HV off on console (no-op if already off)."""
        self._interface_mutex.lock()
        try:
            current_hv = self.interface.hvcontroller.get_hv_status()
            if current_hv:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off successfully")

            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.debug(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Turn Off HV", e)
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Turn Off HV", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleV12(self):
        """Toggle V12 on console."""
        self._interface_mutex.lock()
        try:
            current_v12 = self.interface.hvcontroller.get_12v_status()

            if current_v12:
                self.interface.hvcontroller.turn_12v_off()
                logger.info("V12 turned off successfully")
            else:
                self.interface.hvcontroller.turn_12v_on()
                logger.info("V12 turned on successfully")

            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except LIFUError as e:
            self._handle_lifu_error("Toggle 12V", e)
            self._refresh_power_status_silent()
        except Exception as e:
            self._handle_lifu_error("Toggle 12V", e, context="Unexpected error")
            self._refresh_power_status_silent()
        finally:
            self._interface_mutex.unlock()

    def _refresh_power_status_silent(self):
        """Best-effort re-query of HV/12V state to keep the UI in sync after a failure.

        Any further errors are logged but not surfaced as popups so that a
        single operation produces at most one popup.
        """
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()
            v12_state = self.interface.hvcontroller.get_12v_status()
            self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
        except Exception as e:
            logger.warning(f"Could not refresh power status after failure: {e}")

    @pyqtSlot()
    def getMonitorVoltages(self):
        """Get voltage monitor readings from console."""
        self._interface_mutex.lock()
        try:
            voltages = self.interface.hvcontroller.get_vmon_values()
            logger.debug(f"Voltage readings: {voltages}")
            self.monVoltagesReceived.emit(voltages)
        except LIFUError as e:
            # Do not spam popups on periodic polling; log only.
            logger.warning(f"Failed to read voltage monitor values: {e}")
        except Exception as e:
            logger.error(f"Error getting voltages: {e}")
        finally:
            self._interface_mutex.unlock()
