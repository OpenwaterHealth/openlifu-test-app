"""Transmitter-page slots mixed into :class:`LIFUConnector`.

Holds the TX-device queries, module enumeration, trigger control,
per-module user_config refresh, and TX-side soft resets used by the
Transmitter page.

Mixed into :class:`LIFUConnector` via MRO; relies on the host class
to provide ``self.interface``, ``self._interface_mutex``, the
relevant ``pyqtSignal``\\ s, the trigger-state helper
``_update_trigger_state``, and the connection-state attributes.
"""
from __future__ import annotations

import base58
import json
import logging

from PyQt6.QtCore import pyqtSlot

from openlifu_sdk.io import LIFUInterfaceStatus
from openlifu_sdk.io.exceptions import LIFUError
from openlifu_sdk.io.LIFUConfig import HW_ID_DATA_LENGTH

from lifu.lifu_constants import HV_EN_WHILE_RUNNING, READY

logger = logging.getLogger(__name__)

__all__ = ["TransmitterMixin"]


class TransmitterMixin:
    """TX-side device commands and diagnostics."""

    @pyqtSlot()
    def queryTxInfo(self):
        """Fetch and emit device information for all TX modules as a list."""
        self._interface_mutex.lock()
        try:
            module_count = self.interface.txdevice.get_module_count()
            modules_info = []
            for module_idx in range(module_count):
                try:
                    fw_version = self.interface.txdevice.get_version(module=module_idx)
                except LIFUError as e:
                    logger.warning(f"Module {module_idx}: failed to read firmware version: {e}")
                    fw_version = "N/A"
                try:
                    hw_id = self.interface.txdevice.get_hardware_id(module=module_idx, raw_hex=True)
                except LIFUError as e:
                    logger.warning(f"Module {module_idx}: failed to read hardware id: {e}")
                    hw_id = ""
                if hw_id:
                    if len(hw_id) > HW_ID_DATA_LENGTH:
                        hw_id = base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                    device_id = hw_id
                else:
                    device_id = 'N/A'
                logger.info(f"Module {module_idx} - Firmware Version: {fw_version}, HWID: {device_id}")
                modules_info.append({
                    "module": module_idx,
                    "firmwareVersion": fw_version,
                    "deviceId": device_id
                })
            self.txDeviceInfoReceived.emit(modules_info)
        except LIFUError as e:
            self._handle_lifu_error("TX Info", e)
        except Exception as e:
            self._handle_lifu_error("TX Info", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryTxTemperature(self, warn_after_consecutive_failures=3):
        """Fetch and emit temperature data for all connected modules."""
        if self._num_modules_connected <= 0:
            return
        self._interface_mutex.lock()
        try:
            for module in range(0, self._num_modules_connected):
                try:
                    tx_temp = self.interface.txdevice.get_temperature(module=module)
                    amb_temp = self.interface.txdevice.get_ambient_temperature(module=module)
                    self._temp_poll_failures = 0  # reset on success
                except LIFUError as e:
                    self._temp_poll_failures += 1
                    if self._temp_poll_failures >= warn_after_consecutive_failures:
                        logger.warning(f"Module {module}: failed to read temperature {self._temp_poll_failures} consecutive times: {e}")
                    continue
                self.temperatureTxUpdated.emit(module, tx_temp, amb_temp)
                logger.debug(f"Module: {module} Temperature Data - Temp1: {tx_temp}, Temp2: {amb_temp}")
            self._tx_poll_failures = 0  # at least one module succeeded; reset counter
            try:
                is_running = self.interface.is_running()
                logger.debug(f"Running state during temperature update: {is_running}")
                if not is_running and self.interface.status == LIFUInterfaceStatus.STATUS_RUNNING:
                    # The sequence has completed on the hardware but we
                    # haven't updated our state yet. Mirror stop_sonication's
                    # HV policy: AUTO holds the rail on while still configured;
                    # only WHILE_RUNNING drops it at sonication end.
                    turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
                    self.interface.stop_sonication(turn_hv_off=turn_hv_off)
                    self._state = READY
                    self.stateChanged.emit(self._state)
            except LIFUError as e:
                logger.warning(f"Failed to query running state during temperature update: {e}")
                # LIFU-1001 = device not connected; count as a poll failure so
                # the poll loop can trigger an auto-disconnect.
                if "LIFU-1001" in str(e):
                    self._tx_poll_failures += 1
                    return  # skip further TX work this cycle
        except Exception as e:
            logger.error(f"Error querying Module temperature data: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryNumModules(self):
        """Fetch and emit number of connected TX modules."""
        self._interface_mutex.lock()
        try:
            count = self.interface.txdevice.get_tx_module_count()
            self._num_modules_connected = count
            self.numModulesUpdated.emit()
            logger.debug(f"Number of connected TX modules: {self._num_modules_connected}")
        except LIFUError as e:
            self._handle_lifu_error("TX Modules", e)
        except Exception as e:
            self._handle_lifu_error("TX Modules", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def setTrigger(self, triggerjson: str):
        """Set trigger settings on the device using JSON data."""
        self._interface_mutex.lock()
        try:
            json_trigger_data = json.loads(triggerjson)
            trigger_setting = self.interface.txdevice.set_trigger_json(data=json_trigger_data)
            self._update_trigger_state(trigger_setting)
            logger.info(f"Trigger Setting: {trigger_setting}")
            return True
        except json.JSONDecodeError as e:
            self._emit_device_error("Set Trigger", f"Failed to parse trigger JSON: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Trigger", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Trigger", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(result=bool)
    def toggleTrigger(self):
        """Toggle the trigger state (start or stop)."""
        self._interface_mutex.lock()
        previous_state = self._trigger_state
        try:
            if self._trigger_state:
                # Stop the trigger
                self.interface.txdevice.async_mode(False)
                self.interface.txdevice.stop_trigger()
                logger.info("Trigger stopped successfully.")
                self._trigger_state = False
            else:
                # Start the trigger
                self.interface.txdevice.async_mode(True)
                try:
                    self.interface.txdevice.start_trigger()
                except LIFUError:
                    # Revert to stopped on failure and make sure async mode is off.
                    try:
                        self.interface.txdevice.async_mode(False)
                    except Exception:
                        pass
                    raise
                logger.info("Trigger started successfully.")
                self._trigger_state = True

            self.triggerStateChanged.emit(self._trigger_state)
            return True
        except LIFUError as e:
            self._trigger_state = previous_state
            self.triggerStateChanged.emit(self._trigger_state)
            self._handle_lifu_error("Toggle Trigger", e)
            return False
        except Exception as e:
            self._trigger_state = previous_state
            self.triggerStateChanged.emit(self._trigger_state)
            self._handle_lifu_error("Toggle Trigger", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(result=bool)
    def queryTriggerInfo(self):
        """Query the trigger status and update the state accordingly.

        Returns:
            bool: True if the query was successful, False otherwise.
        """
        self._interface_mutex.lock()
        try:
            trigger_data = self.interface.txdevice.get_trigger_json()
            if isinstance(trigger_data, str):
                trigger_data = json.loads(trigger_data)
            self._update_trigger_state(trigger_data)
            return True
        except json.JSONDecodeError:
            self._emit_device_error("Trigger Status", "Failed to decode trigger status JSON.")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Trigger Status", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Trigger Status", e, context="Unexpected error")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def softResetTX(self):
        """reset hardware TX device."""
        self._interface_mutex.lock()
        try:
            self.interface.txdevice.soft_reset()
            logger.info("Software Reset Sent")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset TX", e)
        except Exception as e:
            self._handle_lifu_error("Soft Reset TX", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int)
    def softResetTXModule(self, module: int):
        """Soft reset a specific TX module by index."""
        self._interface_mutex.lock()
        try:
            self.interface.txdevice.soft_reset(module=module)
            logger.info(f"Software Reset Sent to module {module}")
        except LIFUError as e:
            self._handle_lifu_error("Soft Reset TX Module", e,
                                    context=f"Module {module}")
        except Exception as e:
            self._handle_lifu_error("Soft Reset TX Module", e,
                                    context=f"Module {module} unexpected error")
        finally:
            self._interface_mutex.unlock()
