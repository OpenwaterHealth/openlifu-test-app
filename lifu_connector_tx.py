from __future__ import annotations

import copy
import json
import logging
import numpy as np
import os
import sys

from PyQt6.QtCore import pyqtSlot

from lifu_connector_constants import (
    STATE_DISCONNECTED,
    STATE_TX_CONNECTED,
    STATE_CONFIGURED,
    STATE_READY,
    STATE_RUNNING,
)

logger = logging.getLogger(__name__)

SPEED_OF_SOUND = 1500  # Speed of sound in m/s, used for time-of-flight calculations
NUM_ELEMENTS_PER_MODULE = 64  # Assuming each module has 64 elements, adjust as needed

def _base_path():
    """Return the directory containing bundled data files.
    Works in both frozen (PyInstaller) and normal Python execution."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

class TxSlotsMixin:
    """Qt slots for the TX transmitter device."""

    # -----------------------------------------------------------------------
    # Query / telemetry
    # -----------------------------------------------------------------------

    @pyqtSlot()
    def queryNumModules(self):
        if not self._tx_connected:
            return
        try:
            count = self._interface.transmitter.get_module_count()
            if count > 0:
                self._num_modules = count
            self.numModulesUpdated.emit()
        except Exception as exc:
            logger.error("queryNumModules: %s", exc)

    @pyqtSlot()
    def queryTxInfo(self):
        if not self._tx_connected:
            return
        try:
            modules = []
            for i in range(self._num_modules):
                fw    = self._interface.transmitter.get_version(module=i)
                hw_id = self._interface.transmitter.get_hardware_id(module=i) or "N/A"
                modules.append({"firmwareVersion": fw, "deviceId": hw_id})
            self.txDeviceInfoReceived.emit(modules)
        except Exception as exc:
            logger.error("queryTxInfo: %s", exc)

    @pyqtSlot()
    def queryTxTemperature(self):
        if not self._tx_connected:
            return
        try:
            for i in range(self._num_modules):
                tx_temp  = self._interface.transmitter.get_temperature(module=i) or 0.0
                amb_temp = self._interface.transmitter.get_ambient(module=i) or 0.0
                self.temperatureTxUpdated.emit(i, tx_temp, amb_temp)
        except Exception as exc:
            logger.error("queryTxTemperature: %s", exc)

    @pyqtSlot()
    def queryTriggerInfo(self):
        if not self._tx_connected:
            return
        try:
            trigger = self._interface.transmitter.get_trigger()
            if trigger is not None:
                self.txConfigStateChanged.emit(True)
        except Exception as exc:
            logger.error("queryTriggerInfo: %s", exc)

    # -----------------------------------------------------------------------
    # Trigger control
    # -----------------------------------------------------------------------

    @pyqtSlot(str, result=bool)
    def setTrigger(self, json_string: str) -> bool:
        if not self._tx_connected:
            return False
        try:
            data   = json.loads(json_string)
            result = self._interface.transmitter.set_trigger(data)
            if result is not None:
                self.txConfigStateChanged.emit(True)
                return True
        except Exception as exc:
            logger.error("setTrigger: %s", exc)
        return False

    @pyqtSlot(result=bool)
    def toggleTrigger(self) -> bool:
        if not self._tx_connected:
            return False
        try:
            if self._trigger_enabled:
                ok = self._interface.transmitter.stop_trigger()
                if ok:
                    self._trigger_enabled = False
                    self.triggerStateChanged.emit(False)
                return ok
            else:
                ok = self._interface.transmitter.start_trigger()
                if ok:
                    self._trigger_enabled = True
                    self.triggerStateChanged.emit(True)
                return ok
        except Exception as exc:
            logger.error("toggleTrigger: %s", exc)
        return False

    @pyqtSlot('QVariant', 'QVariant', 'QVariant', 'QVariant', 'QVariant',
              'QVariant', 'QVariant', 'QVariant', 'QVariant', 'QVariant', str)
    def configure_transmitter(self, x, y, z, frequency, voltage,
                               pulse_interval, pulse_count,
                               train_interval, train_count,
                               duration, mode: str):
        """Set trigger parameters and advance the state machine to CONFIGURED."""
        if not self._tx_connected:
            logger.warning("configure_transmitter: TX not connected")
            return
        try:
            
            solution = self.get_solution(x, y, z, frequency, voltage, pulse_interval, pulse_count, train_interval, train_count, duration)        
            if solution is not None:
                ok = self._interface.set_solution(solution)
                if ok:
                    self._set_state(STATE_CONFIGURED)
                    self.txConfigStateChanged.emit(True)

        except Exception as exc:
            logger.error("configure_transmitter: %s", exc)

    # -----------------------------------------------------------------------
    # Sonication lifecycle
    # -----------------------------------------------------------------------

    @pyqtSlot()
    def start_sonication(self):
        if self._state != STATE_READY:
            logger.warning("start_sonication: not in READY state (current=%d)", self._state)
            return
        try:
            ok = self._interface.transmitter.start_trigger()
            if ok:
                self._trigger_enabled = True
                self._set_state(STATE_RUNNING)
                self.triggerStateChanged.emit(True)
        except Exception as exc:
            logger.error("start_sonication: %s", exc)

    @pyqtSlot()
    def stop_sonication(self):
        if self._state != STATE_RUNNING:
            return
        try:
            self._interface.transmitter.stop_trigger()
            self._trigger_enabled = False
            self.triggerStateChanged.emit(False)
            self._set_state(STATE_READY if self._hv_connected else STATE_CONFIGURED)
        except Exception as exc:
            logger.error("stop_sonication: %s", exc)

    @pyqtSlot()
    def reset_configuration(self):
        """Drop back to TX_CONNECTED (unconfigured) state."""
        if self._tx_connected:
            self._set_state(STATE_TX_CONNECTED)
        else:
            self._set_state(STATE_DISCONNECTED)
        self.txConfigStateChanged.emit(False)

    @pyqtSlot(bool)
    def setAsyncMode(self, enabled: bool):
        """Enable or disable the continuous trigger on the transmitter."""
        if not self._tx_connected:
            return
        try:
            if not enabled and self._trigger_enabled:
                self._interface.transmitter.stop_trigger()
                self._trigger_enabled = False
                self.triggerStateChanged.emit(False)
        except Exception as exc:
            logger.error("setAsyncMode: %s", exc)

    # -----------------------------------------------------------------------
    # Device management
    # -----------------------------------------------------------------------

    @pyqtSlot(int)
    def softResetTXModule(self, module_index: int):
        if not self._tx_connected:
            return
        try:
            self._interface.transmitter.soft_reset()
        except Exception as exc:
            logger.error("softResetTXModule: %s", exc)

    @pyqtSlot(int)
    def readTxFirmwareVersion(self, module_index: int):
        if not self._tx_connected:
            return
        try:
            ver = self._interface.transmitter.get_version()
            self.fwVersionRead.emit(f"transmitter-{module_index}", ver)
        except Exception as exc:
            logger.error("readTxFirmwareVersion: %s", exc)

    @pyqtSlot(str, int)
    def updateTransmitterFirmware(self, path: str, module_index: int = 0):
        if not self._tx_connected:
            return
        try:
            self.fwUpdateProgress.emit("Entering DFU mode on transmitter…", 10)
            self._interface.transmitter.enter_dfu()
            self.fwUpdateProgress.emit("DFU mode entered. Flash firmware with DFU tool.", 100)
            self.fwUpdateStatus.emit("transmitter", True, "DFU mode entered. Flash firmware with DFU tool.")
        except Exception as exc:
            logger.error("updateTransmitterFirmware: %s", exc)
            self.fwUpdateProgress.emit(f"Error: {exc}", -1)
            self.fwUpdateStatus.emit("transmitter", False, str(exc))


    def get_solution(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, validate=False):
        """Simulate configuring the transmitter."""
        num_modules = self._num_modules if self._num_modules > 0 else self._manual_num_modules
        if self._solution_loaded:
            logger.info("Using loaded solution for configuration")
            solution = self._loaded_solution_data
            #check if delays and apodizations match the number of elements in the loaded solution
            delays_arr = np.array(solution["delays"]).reshape(-1)  # Ensure it's a 1D array
            apodizations_arr = np.array(solution["apodizations"]).reshape(-1)  # Ensure it's a 1D array
            if validate:
                if delays_arr.ndim == 1:
                    n_delays = delays_arr.shape[0]
                else:
                    n_delays = delays_arr.shape[1]
                if n_delays != num_modules * NUM_ELEMENTS_PER_MODULE:
                    logger.error(f"Loaded solution has {len(delays_arr)} delays, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    self.solutionLoadError.emit(f"Loaded solution has {len(delays_arr)} delays, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    return
                if apodizations_arr.ndim == 1:
                    n_apodizations = apodizations_arr.shape[0]
                else:
                    n_apodizations = apodizations_arr.shape[1]
                if n_apodizations != num_modules * NUM_ELEMENTS_PER_MODULE:
                    logger.error(f"Loaded solution has {len(apodizations_arr)} apodizations, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    self.solutionLoadError.emit(f"Loaded solution has {len(apodizations_arr)} apodizations, but expected {num_modules * NUM_ELEMENTS_PER_MODULE} for {num_modules} modules.")
                    return
        else:
            # Demo UI displays frequency in kHz, duration in microseconds, and pulse interval in ms.
            frequency_hz = float(freq) * 1e3
            duration_seconds = float(durationS) * 1e-6
            pulse_interval_seconds = float(pulseInterval) * 1e-3

            pulse = {"frequency": frequency_hz,
                    "duration": duration_seconds,
                    "amplitude": 1.0
                    }
            focus = np.array([float(xInput), float(yInput), float(zInput)])
            pinmap_data = self._load_pinmap_data(num_modules)
            element_positions = self._extract_element_positions_from_pinmap(pinmap_data)
            numelements = element_positions.shape[0]
            print(f"{num_modules}x config file loaded")
            distances = np.sqrt(np.sum((focus - element_positions)**2, 1))
            tof = distances*1e-3 / SPEED_OF_SOUND
            delays = tof.max() - tof
            apodizations = np.ones(numelements)
            sequence = {"pulse_interval": pulse_interval_seconds,
                        "pulse_count": int(pulseCount),
                        "pulse_train_interval": float(trainInterval),
                        "pulse_train_count": int(trainCount)}
            transducer_dummy = self._build_transducer_from_pinmap(pinmap_data)
            solution = {
                "id": "solution",
                "name": "Solution",
                "delays": delays,
                "apodizations": apodizations,
                "pulse": pulse,
                "sequence": sequence,
                "voltage": float(voltage),
                "transducer": transducer_dummy}
        return solution
    
    def _load_pinmap_data(self, num_modules: int):
        """Load pinmap data for a given module count."""
        pinmap_path = os.path.join(_base_path(), f"pinmap_{num_modules}x.json")
        with open(pinmap_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _extract_element_positions_from_pinmap(self, pinmap_data):
        """Extract element positions (Nx3) in array/world coordinates from pinmap JSON."""
        if "type" in pinmap_data and pinmap_data["type"] == "TransducerArray":
            modules = []
            for module in pinmap_data.get('modules', []):
                module_transform = np.array(module['transform'])
                element_positions = np.array([elem['position'] for elem in module['elements']])
                element_positions = np.hstack((element_positions, np.ones((element_positions.shape[0], 1))))
                world_positions = (np.linalg.inv(module_transform) @ element_positions.T).T[:, :3]
                modules.append(world_positions)
            return np.vstack(modules)

        return np.array([elem['position'] for elem in pinmap_data.get('elements', [])])
    
    def _build_transducer_from_pinmap(self, pinmap_data):
        """Build a solution-compatible transducer object from pinmap JSON."""
        transducer = {
            "id": pinmap_data.get("id", ""),
            "name": pinmap_data.get("name", ""),
            "elements": []
        }

        if "type" in pinmap_data and pinmap_data["type"] == "TransducerArray":
            flattened_elements = []
            global_index = 1
            for module in pinmap_data.get("modules", []):
                module_transform = np.array(module["transform"])
                inv_transform = np.linalg.inv(module_transform)
                for element in module.get("elements", []):
                    element_copy = copy.deepcopy(element)
                    local_position = np.array(list(element_copy.get("position", [0.0, 0.0, 0.0])) + [1.0])
                    world_position = (inv_transform @ local_position)[:3]
                    element_copy["position"] = [float(world_position[0]), float(world_position[1]), float(world_position[2])]
                    element_copy["index"] = global_index
                    flattened_elements.append(element_copy)
                    global_index += 1
            transducer["elements"] = flattened_elements
            return transducer

        transducer["elements"] = copy.deepcopy(pinmap_data.get("elements", []))
        return transducer
