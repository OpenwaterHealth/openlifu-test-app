from turtle import mode

from PyQt6.QtCore import QObject, QRecursiveMutex, QTimer, pyqtSignal, pyqtProperty, pyqtSlot
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import glob

def _base_path():
    """Return the directory containing bundled data files.
    Works in both frozen (PyInstaller) and normal Python execution."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))
import threading
import time
from datetime import datetime, timedelta
import numpy as np
import re
import base58
import json
import copy
from scripts.generate_ultrasound_plot import generate_ultrasound_plot_from_solution  # Import the function directly
from scripts.test_reports import read_test_report, test_report_to_config, check_config_against_device
from openlifu_sdk.io import LIFUInterface
from openlifu_sdk.io.LIFUConfig import HW_ID_DATA_LENGTH

# import verification-tests
from verification.prodreqs_base_class import *
from verification.prodreqs_tx_long_verification_test import TransmitterHeatingPlaceholder, parse_arguments
from verification.prodreqs_voltage_accuracy_placeholder import VoltageAccuracyTest, TEST_VOLTAGES
from verification.prodreqs_tx_short_verification_test import TransmitterShortVerificationTest
from verification.prodreqs_run_indefinitely_placeholder import TransmitterIndefiniteRun

logger = logging.getLogger("LIFUConnector")
# Set up logging
logger.setLevel(logging.INFO)
logger.propagate = True

# Create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)
logger.setLevel(logging.INFO)
logger.propagate = True

sdklogger = logging.getLogger('openlifu_sdk.io')
sdklogger.setLevel(logging.INFO)
#print(sdklogger)


def _parse_tx_module(target: str):
    """Parse a target string like 'tx 0', 'tx_0', 'tx0' into an integer module index.
    Returns None if the target is not a TX target (e.g. 'console').
    """
    import re as _re
    m = _re.match(r'^tx[\s_]?(\d+)$', target.strip().lower())
    if m:
        return int(m.group(1))
    return None


# Define system states
DISCONNECTED = 0
TX_CONNECTED = 1
CONFIGURED = 2
READY = 3
RUNNING = 4
TEST_SCRIPT_READY = 5

#
SPEED_OF_SOUND = 1500  # Speed of sound in m/s, used for time-of-flight calculations
NUM_ELEMENTS_PER_MODULE = 64  # Assuming each module has 64 elements, adjust as needed


class _Bridge(QObject):
    """Thread-safe bridge from OWSignal to pyqtSignal."""
    sig_connected = pyqtSignal(str, str) # (descriptor, port)
    sig_disconnected = pyqtSignal(str, str) # (descriptor, port)
    sig_data = pyqtSignal(str, str) # (descriptor, data)
    sig_error = pyqtSignal(str, int, str)
    
class LIFUConnector(QObject):
    plotGenerated = pyqtSignal(str)  # Signal to notify QML when a new plot is ready
    solutionConfigured = pyqtSignal(str)  # Signal for solution configuration feedback

    powerStatusReceived = pyqtSignal(bool, bool)  # Signal for power status updates
    rgbStateReceived = pyqtSignal(int, str)  # Emit both integer value and text

    # New Signals for data updates
    hvDeviceInfoReceived = pyqtSignal(str, str)  # (firmwareVersion, deviceId)
    monVoltagesReceived = pyqtSignal(list)  # Signal for voltage monitor readings
    txDeviceInfoReceived = pyqtSignal('QVariantList')  # list of {module, firmwareVersion, deviceId}
    temperatureHvUpdated = pyqtSignal(float, float)  # (temp1, temp2)
    temperatureTxUpdated = pyqtSignal(int, float, float)  # (tx_temp, amb_temp)
    numModulesUpdated    = pyqtSignal()  # (num_modules)

    # Signals exposed to QML for connection/data events
    signalConnected = pyqtSignal(str, str)     # (descriptor, port)
    signalDisconnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDataReceived = pyqtSignal(str, str)  # (descriptor, data)

    stateChanged = pyqtSignal(int)  # Notifies QML when state changes
    connectionStatusChanged = pyqtSignal()  # 🔹 New signal for connection updates
    triggerStateChanged = pyqtSignal(bool)  # 🔹 New signal for trigger state change
    txConfigStateChanged = pyqtSignal(bool)  # 🔹 New signal for tx configured state change

    # Firmware update signals
    fwUpdateProgress = pyqtSignal(str, int, int)  # (label, written, total)
    fwUpdateStatus = pyqtSignal(str, bool, str)   # (device_type, success, message)
    fwVersionRead = pyqtSignal(str, str)           # (device_type, version)

    # Test sequence signals
    testProgressUpdated = pyqtSignal(float, float, str, str, str, str)  # (total_frac, case_frac, total_label, case_label, status_color, log_file_path)


    # User config signals
    userConfigRead = pyqtSignal(str, str)   # (target, json_str)  target: "console" | "tx_N"
    userConfigStatus = pyqtSignal(str, bool, str)  # (target, success, message)
    
    # Solution loading signals
    solutionFileLoaded = pyqtSignal(str, str)  # (solution_name, message)
    solutionLoadError = pyqtSignal(str)  # (error_message)
    solutionStateChanged = pyqtSignal()  # Notifies when solution is loaded/unloaded
    solutionSaveStatus = pyqtSignal(bool, str)  # (success, message)
    testReportLoaded = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, hv_test_mode=False):
        super().__init__()
        self.interface = LIFUInterface(HV_test_mode=hv_test_mode,
                                       run_async=True, 
                                       voltage_table_selection="evt0",
                                       sequence_time_selection="stress_test",
                                       duty_cycle_selection="stress_test")
        self._txConnected = False
        self._hvConnected = False
        self._configured = False
        self._running = False
        self._abort_requested = False
        self.thermal_test_instance = None
        self._active_test_kind = ""
        self.running_thread = None
        self._state = DISCONNECTED
        self._trigger_state = False  # Internal state to track trigger status
        self._txconfigured_state = False  # Internal state to track trigger status
        self._num_modules_connected = 0
        self._manual_num_modules = 1  # fallback when TX not connected
        
        # Solution loading state
        self._solution_loaded = False
        self._loaded_solution_data = None
        self._solution_name = ""

        self._interface_mutex = QRecursiveMutex()

        self._ensure_preset_solutions_seeded()

        self._bridge = _Bridge()

        # Wire OWSignals -> bridge
        self.interface.hvcontroller.signal_connected.connect(self._bridge.sig_connected.emit)
        self.interface.hvcontroller.signal_disconnected.connect(self._bridge.sig_disconnected.emit)
        self.interface.hvcontroller.signal_data_received.connect(self._bridge.sig_data.emit)
        self.interface.hvcontroller.signal_error.connect(self._bridge.sig_error.emit)

        self.interface.txdevice.signal_connected.connect(self._bridge.sig_connected.emit)
        self.interface.txdevice.signal_disconnected.connect(self._bridge.sig_disconnected.emit)
        self.interface.txdevice.signal_data_received.connect(self._bridge.sig_data.emit)
        self.interface.txdevice.signal_error.connect(self._bridge.sig_error.emit)

        # Wire bridge -> UI
        self._bridge.sig_connected.connect(self.on_connected)
        self._bridge.sig_disconnected.connect(self.on_disconnected)
        self._bridge.sig_data.connect(self.on_data_received)
        self._bridge.sig_error.connect(self.on_error)

        QTimer.singleShot(0, lambda: asyncio.ensure_future(self.interface.start_monitoring()))

    def close(self):
        """Shut down the underlying LIFU interface cleanly."""
        try:
            self.interface.close()
        except Exception as e:
            logger.error(f"Error closing LIFU interface: {e}")

    def update_state(self):
        """Update system state based on connection and configuration."""
        if self._running:
            self._state = RUNNING
        elif not self._txConnected and not self._hvConnected:
            self._state = DISCONNECTED
        elif self._txConnected and self._hvConnected and self._configured:
            self._state = READY
        elif self._txConnected and self._configured:
            self._state = CONFIGURED
        elif self._txConnected and not self._configured:
            self._state = TX_CONNECTED
        elif self._hvConnected and not self._txConnected:
            self._state = TEST_SCRIPT_READY
        # Notify QML of state change
        self.stateChanged.emit(self._state)

    def _update_trigger_state(self, trigger_data):
        """Helper method to update trigger state and emit signal."""
        try:
            trigger_status = trigger_data.get("TriggerStatus", "STOPPED")
            new_trigger_state = trigger_status == "RUNNING"

            if new_trigger_state != self._trigger_state:
                self._trigger_state = new_trigger_state
                self.triggerStateChanged.emit(self._trigger_state)

        except Exception as e:
            logger.error(f"Error updating trigger state: {e}")

    @pyqtSlot(str, result=dict)
    def parse_status_string(self, status_str):
        result = {
            "status": None,
            "mode": None,
            "pulse_train_percent": None,
            "pulse_percent": None,
            "temp_tx": None,
            "temp_ambient": None
        }

        try:
            # Try pattern WITH PULSE field
            pattern_with_pulse = re.compile(
                r"STATUS:(\w+),"
                r"MODE:(\w+),"
                r"PULSE_TRAIN:\[(\d+)/(\d+)\],"
                r"PULSE:\[(\d+)/(\d+)\],"
                r"TEMP_TX:([0-9.]+),"
                r"TEMP_AMBIENT:([0-9.]+)"
            )
            match = pattern_with_pulse.match(status_str.strip())

            if match:
                (
                    status,
                    mode,
                    pt_current, pt_total,
                    p_current, p_total,
                    temp_tx,
                    temp_ambient
                ) = match.groups()

                # Convert and compute percentages
                pt_current = int(pt_current)
                pt_total = int(pt_total)
                p_current = int(p_current)
                p_total = int(p_total)

                result["status"] = status
                result["mode"] = mode
                result["pulse_train_percent"] = (pt_current / pt_total * 100) if pt_total > 0 else 0
                result["pulse_percent"] = (p_current / p_total * 100) if p_total > 0 else 0
                result["temp_tx"] = float(temp_tx)
                result["temp_ambient"] = float(temp_ambient)

            else:
                # Try pattern WITHOUT PULSE field
                pattern_without_pulse = re.compile(
                    r"STATUS:(\w+),"
                    r"MODE:(\w+),"
                    r"PULSE_TRAIN:\[(\d+)/(\d+)\],"
                    r"TEMP_TX:([0-9.]+),"
                    r"TEMP_AMBIENT:([0-9.]+)"
                )
                match = pattern_without_pulse.match(status_str.strip())

                if not match:
                    raise ValueError("Input string format is invalid.")

                (
                    status,
                    mode,
                    pt_current, pt_total,
                    temp_tx,
                    temp_ambient
                ) = match.groups()

                # Convert and compute percentages
                pt_current = int(pt_current)
                pt_total = int(pt_total)

                result["status"] = status
                result["mode"] = mode
                result["pulse_train_percent"] = (pt_current / pt_total * 100) if pt_total > 0 else 0
                result["pulse_percent"] = None  # No pulse data available
                result["temp_tx"] = float(temp_tx)
                result["temp_ambient"] = float(temp_ambient)

            return result

        except Exception as e:
            logger.error(f"Failed to parse status string: {e}")
            return result

    def on_error(self, desc: str, pkt_id: int, msg: str):
        if desc != "Console":
            return
        logger.error(f"ERROR id={pkt_id} {msg}")

    @pyqtSlot(str, str)
    def on_connected(self, descriptor, port):
        """Handle device connection."""
        if descriptor == "TX":
            self._txConnected = True
        elif descriptor == "HV":
            self._hvConnected = True
        self.signalConnected.emit(descriptor, port)
        self.connectionStatusChanged.emit() 
        self.update_state()

    @pyqtSlot(str, str)
    def on_disconnected(self, descriptor, port):
        """Handle device disconnection."""
        if descriptor == "TX":
            self._txConnected = False
        elif descriptor == "HV":
            self._hvConnected = False
        self.signalDisconnected.emit(descriptor, port)
        self.connectionStatusChanged.emit() 
        self.update_state()

    @pyqtSlot(str, str)
    def on_data_received(self, descriptor, message):
        """Handle incoming data from the LIFU device."""
        logger.info(f"Data received from {descriptor}: {message}")
        self.signalDataReceived.emit(descriptor, message)

        if descriptor == "TX":
            try:
                parsed = self.parse_status_string(message)
                if parsed["status"] in {"RUNNING", "STOPPED"}:
                    # Update internal trigger state based on parsed status
                    new_trigger_state = parsed["status"] == "RUNNING"
                    
                    if new_trigger_state != self._trigger_state:
                        self._trigger_state = new_trigger_state
                        self.triggerStateChanged.emit(self._trigger_state)
                        logger.info(f"Trigger state updated to: {'RUNNING' if self._trigger_state else 'STOPPED'}")
                    
                    if parsed["status"] == "STOPPED":
                        logger.info("Trigger is stopped.")
                        self._state = READY
                        self.stateChanged.emit(self._state)

                    # Forward temperature data embedded in STATUS messages so that
                    # separate queryTxTemperature() calls are not needed while the
                    # SDK monitoring thread owns the serial port.
                    if parsed["temp_tx"] is not None and parsed["temp_ambient"] is not None:
                        self.temperatureTxUpdated.emit(0, float(parsed["temp_tx"]), float(parsed["temp_ambient"]))

            except Exception as e:
                logger.error(f"Failed to parse and update trigger state: {e}")

    @pyqtSlot(str, float)
    def configureSolution(self, solutionName, amplitude):
        """Configures the solution and emits status to QML."""
        self._interface_mutex.lock()
        try:
            logger.debug("Configuring solution: %s with amplitude: %s", solutionName, amplitude)
            solution = None  # Replace with actual configuration logic
            if self.interface.set_solution(solution):
                logger.info("Solution '%s' configured successfully.", solutionName)
                self.solutionConfigured.emit(f"Solution '{solutionName}' configured.")
            else:
                logger.error("Failed to configure solution '%s'.", solutionName)
                self.solutionConfigured.emit("Configuration failed.")
        except Exception as e:
            logger.error("Error configuring solution: %s", e)
            self.solutionConfigured.emit("Configuration error.")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def generate_plot(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode="buffer"):
        """Generates an ultrasound plot and emits data to QML."""
        try:
            #logger.info(f"Generating plot: X={x}, Y={y}, Z={z}, Frequency={freq}, Cycles={cycles}, Trigger={trigger}, Mode={mode}")
            solution = self.get_solution(xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, validate=self._txConnected)
            image_data = generate_ultrasound_plot_from_solution(solution, mode)
            #image_data = generate_ultrasound_plot(x, y, z, freq, cycles, trigger, mode)
            if image_data == "ERROR":
                logger.error("Plot generation failed")
            else:
                logger.info("Plot generated successfully")
                self.plotGenerated.emit(image_data)  # Send image data to QML

        except Exception as e:
            logger.error(f"Error generating plot: {e}")

    def get_solution(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, validate=False):
        """Simulate configuring the transmitter."""
        num_modules = self._num_modules_connected if self._num_modules_connected > 0 else self._manual_num_modules
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

    def _to_json_compatible(self, value):
        """Convert numpy and nested structures into JSON-serializable Python values."""
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {k: self._to_json_compatible(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_json_compatible(v) for v in value]
        if isinstance(value, tuple):
            return [self._to_json_compatible(v) for v in value]
        return value

    def _infer_num_modules_from_solution(self, data: dict) -> int:
        """Infer the number of TX modules from a solution's transducer element count."""
        try:
            elements = data.get('transducer', {}).get('elements', [])
            n = len(elements)
            if n > 0 and NUM_ELEMENTS_PER_MODULE > 0:
                return max(1, n // NUM_ELEMENTS_PER_MODULE)
        except Exception:
            pass
        return 0

    @pyqtSlot(int)
    def setManualNumModules(self, n: int):
        """Set the manual module count used when TX hardware is not connected."""
        self._manual_num_modules = max(1, int(n))

    def _get_preset_templates_path(self) -> str:
        return os.path.join(_base_path(), "preset_templates")

    def _get_user_data_root(self) -> str:
        """Return the writable per-user application data directory."""
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "OpenLIFU-TestApp")

    def _get_legacy_preset_solutions_path(self) -> str:
        """Return the legacy preset directory next to the executable, if applicable."""
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "preset_solutions")
        return os.path.join(_base_path(), "preset_solutions")

    def _get_runtime_preset_solutions_path(self) -> str:
        if getattr(sys, 'frozen', False):
            return os.path.join(self._get_user_data_root(), "preset_solutions")
        return os.path.join(_base_path(), "preset_solutions")

    def _get_default_solution_path(self) -> str:
        return os.path.join(self._get_runtime_preset_solutions_path(), "default_solution.json")

    def _extract_solution_settings(self, data):
        """Extract UI-editable settings from a solution-like dict."""
        target = data.get('target', {})
        focus_position = target.get('position', [0, 0, 50])

        pulse = data.get('pulse', {})
        frequency = pulse.get('frequency', 400000)
        duration = pulse.get('duration', 2e-4)

        sequence = data.get('sequence', {})
        pulse_interval = sequence.get('pulse_interval', 0.1)
        pulse_count = sequence.get('pulse_count', 1)
        pulse_train_interval = sequence.get('pulse_train_interval', 1)
        pulse_train_count = sequence.get('pulse_train_count', 1)

        voltage = data.get('voltage', 12.0)

        def _r(value, digits):
            return round(float(value), digits)

        return {
            'xInput': _r(focus_position[0], 3),
            'yInput': _r(focus_position[1], 3),
            'zInput': _r(focus_position[2], 3),
            'frequency': _r(float(frequency) / 1e3, 3),
            'duration': _r(float(duration) * 1e6, 3),
            'voltage': _r(voltage, 3),
            'pulseInterval': _r(float(pulse_interval) * 1e3, 3),
            'pulseCount': int(pulse_count),
            'trainInterval': _r(pulse_train_interval, 6),
            'trainCount': int(pulse_train_count),
            'numModules': self._infer_num_modules_from_solution(data),
        }

    def _build_solution_export_data(self, solution_id, solution_name, num_modules,
                                    xInput, yInput, zInput, freq, voltage,
                                    pulseInterval, pulseCount, trainInterval, trainCount, durationS):
        solution = self.get_solution(
            xInput, yInput, zInput,
            freq, voltage, pulseInterval, pulseCount,
            trainInterval, trainCount, durationS,
            validate=True
        )
        if solution is None:
            raise ValueError("failed to build a valid solution")

        solution_data = self._to_json_compatible(solution)
        cleaned_id = (solution_id or "").strip() or "solution"
        cleaned_name = (solution_name or "").strip() or cleaned_id
        target_position = [float(xInput), float(yInput), float(zInput)]

        pinmap_data = self._load_pinmap_data(num_modules)
        solution_data["id"] = cleaned_id
        solution_data["name"] = cleaned_name
        solution_data["target"] = {
            "position": target_position,
            "units": "mm"
        }
        solution_data["foci"] = [{
            "position": target_position,
            "units": "mm"
        }]
        solution_data["transducer"] = self._build_transducer_from_pinmap(pinmap_data)
        return solution_data

    def _write_solution_json(self, file_path, solution_data):
        normalized_path = os.path.normpath(file_path)
        if not normalized_path.lower().endswith(".json"):
            normalized_path += ".json"

        parent_dir = os.path.dirname(normalized_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(normalized_path, 'w', encoding='utf-8') as f:
            json.dump(solution_data, f, indent=4)

        return normalized_path

    def _ensure_preset_solutions_seeded(self):
        """Seed the runtime preset directory from tracked templates if needed."""
        preset_dir = self._get_runtime_preset_solutions_path()
        os.makedirs(preset_dir, exist_ok=True)

        existing_json = glob.glob(os.path.join(preset_dir, "*.json"))
        if not existing_json:
            legacy_dir = self._get_legacy_preset_solutions_path()
            legacy_json = []
            if os.path.normcase(os.path.abspath(legacy_dir)) != os.path.normcase(os.path.abspath(preset_dir)):
                legacy_json = glob.glob(os.path.join(legacy_dir, "*.json"))

            if legacy_json:
                for legacy_path in legacy_json:
                    destination = os.path.join(preset_dir, os.path.basename(legacy_path))
                    shutil.copy2(legacy_path, destination)
                    logger.info(f"Migrated legacy preset file to user data: {destination}")
            else:
                template_dir = self._get_preset_templates_path()
                for template_path in glob.glob(os.path.join(template_dir, "*.json")):
                    destination = os.path.join(preset_dir, os.path.basename(template_path))
                    shutil.copy2(template_path, destination)
                    logger.info(f"Seeded preset file: {destination}")

        default_path = self._get_default_solution_path()
        if not os.path.exists(default_path):
            default_solution = self._build_solution_export_data(
                "default_solution",
                "Default Solution",
                1,
                "0", "0", "50",
                "400", "12.0",
                "100", "1", "0", "1", "200"
            )
            self._write_solution_json(default_path, default_solution)
            logger.info(f"Created default solution: {default_path}")

    @pyqtSlot(result=str)
    def getPresetSolutionsPath(self) -> str:
        """Return the absolute path to the preset_solutions folder for QML use."""
        self._ensure_preset_solutions_seeded()
        return self._get_runtime_preset_solutions_path()

    @pyqtSlot(result=str)
    def getDefaultSolutionFilePath(self) -> str:
        self._ensure_preset_solutions_seeded()
        return self._get_default_solution_path()

    @pyqtSlot(result='QVariantMap')
    def getDefaultSolutionSettings(self):
        """Return the boot-time default UI settings from default_solution.json."""
        try:
            self._ensure_preset_solutions_seeded()
            with open(self._get_default_solution_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            return self._extract_solution_settings(data)
        except Exception as e:
            logger.error(f"Error loading default solution settings: {e}")
            return {}

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def saveSolutionToFile(self, solution_id, solution_name, file_path, num_modules_str,
                           xInput, yInput, zInput, freq, voltage,
                           pulseInterval, pulseCount, trainInterval, trainCount, durationS):
        """Save the current solution to a JSON file.

        num_modules_str: number of TX modules to use for the transducer field.
        When TX is connected, this is read from hardware; when offline it comes from the UI spinbox.
        """
        try:
            try:
                num_modules = int(num_modules_str)
            except (ValueError, TypeError):
                num_modules = 0

            if num_modules <= 0:
                if self._txConnected:
                    self.queryNumModules()
                    num_modules = self._num_modules_connected
                if num_modules <= 0:
                    message = "Cannot save solution: number of TX modules must be > 0."
                    self.solutionSaveStatus.emit(False, message)
                    return False

            solution_data = self._build_solution_export_data(
                solution_id, solution_name, num_modules,
                xInput, yInput, zInput,
                freq, voltage, pulseInterval, pulseCount,
                trainInterval, trainCount, durationS
            )
            normalized_path = self._write_solution_json(file_path, solution_data)

            message = f"Saved solution '{solution_data['name']}' to {normalized_path}"
            logger.info(message)
            self.solutionSaveStatus.emit(True, message)
            return True
        except Exception as e:
            message = f"Error saving solution: {e}"
            logger.error(message)
            self.solutionSaveStatus.emit(False, message)
            return False

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def configure_transmitter(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode):
        """Simulate configuring the transmitter."""
        if not self._txConnected:
            logger.error("Cannot configure transmitter: No TX device connected")
            return
        self.queryNumModules()
        solution = self.get_solution(xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS)        
        
        self._interface_mutex.lock()
        try:
            self.interface.set_solution(solution, trigger_mode=mode)
            self._configured = True
            self.update_state()
            logger.info("Transmitter configured")
        except Exception as e:
            logger.error(f"Error configuring transmitter: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def reset_configuration(self):
        """Reset system configuration to defaults."""
        self._configured = False
        self.update_state()
        logger.info("Configuration reset")

    @pyqtSlot()
    def start_sonication(self):
        """Start the beam, transitioning to RUNNING state."""
        if self._state == READY:
            self._interface_mutex.lock()
            try:
                if self.interface.start_sonication(async_mode=False):
                    self._state = RUNNING
                else:
                    raise RuntimeError("Failed to start sonication")
                self.stateChanged.emit(self._state)
                logger.info("Sonication started")
            except Exception as e:
                logger.error(f"Error starting sonication: {e}")
            finally:
                self._interface_mutex.unlock()

    @pyqtSlot()
    def stop_sonication(self):
        """Stop the beam and return to READY state."""
        if self._state == RUNNING:
            self._interface_mutex.lock()
            try:
                if self.interface.stop_sonication():
                    self._state = READY
                else:
                    raise RuntimeError("Failed to stop sonication")
                self.stateChanged.emit(self._state)
                logger.info("Sonication stopped")
            except Exception as e:
                logger.error(f"Error stopping sonication: {e}")
            finally:
                self._interface_mutex.unlock()
            logger.info("Sonication stopped")

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def txConnected(self):
        """Expose TX connection status to QML."""
        return self._txConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def hvConnected(self):
        """Expose HV connection status to QML."""
        return self._hvConnected

    @pyqtProperty(int, notify=stateChanged)
    def state(self):
        """Expose state as a QML property."""
        return self._state
    
    @pyqtProperty(bool, notify=connectionStatusChanged)
    def txConnected(self):
        """Expose TX connection status to QML."""
        return self._txConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def hvConnected(self):
        """Expose HV connection status to QML."""
        return self._hvConnected
        
    @pyqtProperty(bool, notify=triggerStateChanged)
    def triggerEnabled(self):
        """Expose trigger enabled status to QML."""
        return self._trigger_state
    
    @pyqtProperty(bool, notify=solutionStateChanged)
    def solutionLoaded(self):
        """Expose solution loaded status to QML."""
        return self._solution_loaded
    
    @pyqtProperty(str, notify=solutionStateChanged)
    def solutionName(self):
        """Expose loaded solution name to QML."""
        return self._solution_name
    
    @pyqtSlot()
    def queryHvInfo(self):
        """Fetch and emit device information."""
        self._interface_mutex.lock()
        try:
            fw_version = self.interface.hvcontroller.get_version()
            logger.info(f"Version: {fw_version}")
            hw_id = self.interface.hvcontroller.get_hardware_id(raw_hex=True)
            if hw_id:
                if len(hw_id) > HW_ID_DATA_LENGTH:
                    hw_id =  base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                device_id = hw_id 
            else:
                device_id = 'N/A'
            self.hvDeviceInfoReceived.emit(fw_version, device_id)
            logger.info(f"Device Info - Firmware: {fw_version}, Device ID: {device_id}")
        except Exception as e:
            logger.error(f"Error querying device info: {e}")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot()
    def queryTxInfo(self):
        """Fetch and emit device information for all TX modules as a list."""
        self._interface_mutex.lock()
        try:
            module_count = self.interface.txdevice.get_module_count()
            modules_info = []
            for module_idx in range(module_count):
                fw_version = self.interface.txdevice.get_version(module=module_idx)
                logger.info(f"Version: {fw_version}")
                hw_id = self.interface.txdevice.get_hardware_id(module=module_idx, raw_hex=True)
                if hw_id:
                    if len(hw_id) > HW_ID_DATA_LENGTH:
                        hw_id =  base58.b58encode(bytes.fromhex(hw_id[:HW_ID_DATA_LENGTH])).decode('utf-8')
                    device_id = hw_id 
                else:
                    device_id = 'N/A'
                logger.info(f"Module {module_idx} - Firmware: {fw_version}, Device ID: {device_id}")
                modules_info.append({
                    "module": module_idx,
                    "firmwareVersion": fw_version,
                    "deviceId": device_id
                })
            self.txDeviceInfoReceived.emit(modules_info)
        except Exception as e:
            logger.error(f"Error querying device info: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtProperty(int, notify=numModulesUpdated)
    def queryNumModulesConnected(self):
        """Fetch and emit number of connected TX modules."""
        return self._num_modules_connected

    @pyqtSlot()
    def queryHvTemperature(self):
        """Fetch and emit temperature data."""
        self._interface_mutex.lock()
        try:
            temp1 = self.interface.hvcontroller.get_temperature1()  
            temp2 = self.interface.hvcontroller.get_temperature2()  

            self.temperatureHvUpdated.emit(temp1, temp2)
            logger.info(f"Temperature Data - Temp1: {temp1}, Temp2: {temp2}")
        except Exception as e:
            logger.error(f"Error querying temperature data: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryTxTemperature(self):
        """Fetch and emit temperature data for all connected modules."""
        if self._num_modules_connected <= 0:
            return
        self._interface_mutex.lock()
        try:
            for module in range(0, self._num_modules_connected):
                tx_temp = self.interface.txdevice.get_temperature(module=module)
                amb_temp = self.interface.txdevice.get_ambient_temperature(module=module)
                self.temperatureTxUpdated.emit(module, tx_temp, amb_temp)
                logger.info(f"Module: {module} Temperature Data - Temp1: {tx_temp}, Temp2: {amb_temp}")
        except Exception as e:
            logger.error(f"Error querying Module temperature data: {e}")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot()
    def queryNumModules(self):
        """Fetch and emit number of connected TX modules."""
        self._interface_mutex.lock()
        try:
            self._num_modules_connected = self.interface.txdevice.get_tx_module_count()
            self.numModulesUpdated.emit()
            logger.info(f"Number of connected TX modules: {self._num_modules_connected}")

        except Exception as e:
            logger.error(f"Error querying number of TX modules: {e}")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot(int)
    def setRGBState(self, state):
        """Set the RGB state using integer values."""
        self._interface_mutex.lock()
        try:
            valid_states = [0, 1, 2, 3]
            if state not in valid_states:
                logger.error(f"Invalid RGB state value: {state}")
                return

            if self.interface.hvcontroller.set_rgb_led(state) == state:
                logger.info(f"RGB state set to: {state}")
            else:
                logger.error(f"Failed to set RGB state to: {state}")
        except Exception as e:
            logger.error(f"Error setting RGB state: {e}")
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
            self.rgbStateReceived.emit(state, state_text)  # Emit both values
        except Exception as e:
            logger.error(f"Error querying RGB state: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def queryPowerStatus(self):
        """Fetch and emit HV state."""
        self._interface_mutex.lock()
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()            
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error querying Power status: {e}")
        finally:
            self._interface_mutex.unlock()
    
    @pyqtSlot(bool)
    def setAsyncMode(self, enable: bool):
        """Set the async mode for the interface."""
        self._interface_mutex.lock()
        try:
            ret = self.interface.txdevice.async_mode(enable)
            logger.debug(f"Async mode set to: {ret}")
        except Exception as e:
            logger.error(f"Error setting async mode: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, target: str, index: int = 0):
        """Send a ping command to HV device."""
        self._interface_mutex.lock()
        try:
            if target == "HV":
                if self.interface.hvcontroller.ping():
                    logger.info(f"Ping command sent successfully")
                    return True
                else:
                    logger.error(f"Failed to send ping command")
                    return False
            elif target == "TX":
                logger.info(f"Ping command sent to Module {index}")
                if self.interface.txdevice.ping(module=index):
                    logger.info(f"Ping command sent successfully")
                    return True
                else:
                    logger.error(f"Failed to send ping command")
                    return False
            else:
                logger.error(f"Invalid target for ping command")
                return False
        except Exception as e:
            logger.error(f"Error sending ping command: {e}")
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
                if self.interface.hvcontroller.toggle_led():
                    logger.info(f"Toggle command sent successfully")
                    return True
                else:
                    logger.error(f"Failed to Toggle command")
                    return False
            elif target == "TX":                
                logger.info(f"Toggle command sent to Module {index}")
                if self.interface.txdevice.toggle_led(module=index):
                    logger.info(f"Toggle command sent successfully")
                    return True
                else:
                    logger.error(f"Failed to send Toggle command")
                    return False
            else:
                logger.error(f"Invalid target for Toggle command")
                return False
        except Exception as e:
            logger.error(f"Error sending Toggle command: {e}")
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
                logger.info(f"Echo command sent to Module {index}")
                echoed_data, data_len = self.interface.txdevice.echo(echo_data=expected_data, module=index)
            else:
                logger.error("Invalid target for Echo command")
                return False

            if echoed_data == expected_data and data_len == len(expected_data):
                logger.info("Echo command successful - Data matched")
                return True
            else:
                logger.error("Echo command failed - Data mismatch")
                return False
            
        except Exception as e:
            logger.error(f"Error sending Echo command: {e}")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def setHVCommand(self, strval: str):
        """Set High voltage command to device."""
        self._interface_mutex.lock()
        try:
            voltage = float(strval)
            if self.interface.hvcontroller.set_voltage(voltage=voltage):
                logger.info("Voltage set successfully")
                return True
            else:   
                logger.error("Failed to set voltage")
                return False    
                        
        except Exception as e:
            logger.error(f"Error setting High Voltage: {e}")
            return False
        finally:
            self._interface_mutex.unlock()
    
    @pyqtSlot(int, int, result=bool)
    def setFanLevel(self, fid: int, speed: int):
        """Set Fan Level to device."""
        self._interface_mutex.lock()
        try:
            
            if self.interface.hvcontroller.set_fan_speed(fan_id=fid, fan_speed=speed) == speed:
                logger.info(f"Fan set successfully")
                return True
            else:   
                logger.error(f"Failed to set Fan Speed")
                return False    
                        
        except Exception as e:
            logger.error(f"Error setting Fan Speed: {e}")
            return False
        finally:
            self._interface_mutex.unlock()
    
    @pyqtSlot(str, result=bool)
    def setTrigger(self, triggerjson: str):
        """Set trigger settings on the device using JSON data."""
        self._interface_mutex.lock()
        try:
            json_trigger_data = json.loads(triggerjson)
            
            trigger_setting = self.interface.txdevice.set_trigger_json(data=json_trigger_data)

            if trigger_setting:
                self._update_trigger_state(trigger_setting)  # Update trigger state dynamically
                logger.info(f"Trigger Setting: {trigger_setting}")
                return True
            else:
                logger.error("Failed to set trigger setting.")
                return False

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON data: {e}")
            return False

        except AttributeError as e:
            logger.error(f"Invalid interface or method: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error while setting trigger: {e}")
            return False
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(result=bool)
    def toggleTrigger(self):
        """Toggle the trigger state (start or stop)."""
        self._interface_mutex.lock()
        try:
            if self._trigger_state:
                # Stop the trigger
                self.interface.txdevice.async_mode(False)
                success = self.interface.txdevice.stop_trigger()
                if success:
                    logger.info("Trigger stopped successfully.")
                    self._trigger_state = False
                else:
                    logger.error("Failed to stop trigger.")
            else:
                # Start the trigger
                self.interface.txdevice.async_mode(True)
                success = self.interface.txdevice.start_trigger()
                if success:
                    logger.info("Trigger started successfully.")
                    self._trigger_state = True
                else:
                    logger.error("Failed to start trigger.")

            # Emit the updated trigger state
            self.triggerStateChanged.emit(self._trigger_state)
            return success

        except AttributeError as e:
            logger.error(f"Invalid interface or method: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error while toggling trigger: {e}")
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
            logger.error("Failed to decode trigger status JSON.")
            return False

        except AttributeError as e:
            logger.error(f"Invalid interface or method: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error while querying trigger info: {e}")
            return False
        finally:
            self._interface_mutex.unlock()
        
    @pyqtSlot()
    def softResetHV(self):
        """reset hardware HV device."""
        self._interface_mutex.lock()
        try:
            if self.interface.hvcontroller.soft_reset():
                logger.info(f"Software Reset Sent")
            else:
                logger.error(f"Failed to send Software Reset")
        except Exception as e:
            logger.error(f"Error Sending Software Reset: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleHV(self):
        """Toggle HV on console."""
        self._interface_mutex.lock()
        try:
            # Check the current state of HV
            if self.interface.hvcontroller.get_hv_status():
                # If HV is on, turn it off
                if self.interface.hvcontroller.turn_hv_off():
                    logger.info("HV turned off successfully")
                else:
                    logger.error("Failed to turn off HV")
            else:
                # If HV is off, turn it on
                if self.interface.hvcontroller.turn_hv_on():
                    logger.info("HV turned on successfully")
                else:
                    logger.error("Failed to turn on HV")
            hv_state = self.interface.hvcontroller.get_hv_status()            
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error toggling HV: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def turnOffHV(self):
        """Toggle HV on console."""
        self._interface_mutex.lock()
        try:
            # Check the current state of HV
            if self.interface.hvcontroller.get_hv_status():
                # If HV is on, turn it off
                if self.interface.hvcontroller.turn_hv_off():
                    logger.info("HV turned off successfully")
                else:
                    logger.error("Failed to turn off HV")

            hv_state = self.interface.hvcontroller.get_hv_status()            
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.debug(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error toggling HV: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def toggleV12(self):
        """Toggle V12 on console."""
        self._interface_mutex.lock()
        try:
            # Check the current state of HV
            if self.interface.hvcontroller.get_12v_status():
                # If HV is on, turn it off
                if self.interface.hvcontroller.turn_12v_off():
                    logger.info("V12 turned off successfully")
                else:
                    logger.error("Failed to turn off HV")
            else:
                # If HV is off, turn it on
                if self.interface.hvcontroller.turn_12v_on():
                    logger.info("V12 turned on successfully")
                else:
                    logger.error("Failed to turn on V12")
            hv_state = self.interface.hvcontroller.get_hv_status()            
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error toggling HV: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def getMonitorVoltages(self):
        """Get voltage monitor readings from console."""
        self._interface_mutex.lock()
        try:
            voltages = self.interface.hvcontroller.get_vmon_values()
            logger.debug(f"Voltage readings: {voltages}")
            # Emit the voltage readings to QML
            self.monVoltagesReceived.emit(voltages)
        except Exception as e:
            logger.error(f"Error getting voltages: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot()
    def softResetTX(self):
        """reset hardware TX device."""
        self._interface_mutex.lock()
        try:
            if self.interface.txdevice.soft_reset():
                logger.info(f"Software Reset Sent")
            else:
                logger.error(f"Failed to send Software Reset")
        except Exception as e:
            logger.error(f"Error Sending Software Reset: {e}")
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int)
    def softResetTXModule(self, module: int):
        """Soft reset a specific TX module by index."""
        self._interface_mutex.lock()
        try:
            if self.interface.txdevice.soft_reset(module=module):
                logger.info(f"Software Reset Sent to module {module}")
            else:
                logger.error(f"Failed to send Software Reset to module {module}")
        except Exception as e:
            logger.error(f"Error Sending Software Reset to module {module}: {e}")
        finally:
            self._interface_mutex.unlock()

        
    @pyqtProperty(str, constant=True)
    def sdkVersion(self) -> str:
        """Expose SDK version as a constant QML property."""
        try:
            # Attempt to get SDK version from LIFUInterface
            return LIFUInterface.get_sdk_version()
        except Exception:
            # Fallback to default version if the function doesn't exist or package metadata is missing
            return "0.3.2"

    # ------------------------------------------------------------------
    # Firmware update
    # ------------------------------------------------------------------

    @pyqtSlot(str, result=str)
    def getDefaultFirmwarePath(self, device_type: str) -> str:
        """Return the bundled firmware file path for the given device type (console or transmitter)."""
        try:
            import importlib.util
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

    @pyqtSlot(result=str)
    def readHvFirmwareVersion(self) -> str:
        """Read and return the current console (HV) firmware version."""
        self._interface_mutex.lock()
        try:
            version = self.interface.hvcontroller.get_version()
            self.fwVersionRead.emit("console", version)
            logger.info(f"Console firmware version: {version}")
            return version
        except Exception as e:
            logger.error(f"Error reading console firmware version: {e}")
            self.fwVersionRead.emit("console", "Error")
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(int, result=str)
    def readTxFirmwareVersion(self, module: int) -> str:
        """Read and return the current transmitter firmware version for a given module."""
        self._interface_mutex.lock()
        try:
            version = self.interface.txdevice.get_version(module=module)
            self.fwVersionRead.emit(f"transmitter_{module}", version)
            logger.info(f"Transmitter module {module} firmware version: {version}")
            return version
        except Exception as e:
            logger.error(f"Error reading transmitter module {module} firmware version: {e}")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            return "Error"
        finally:
            self._interface_mutex.unlock()

    @pyqtSlot(str)
    def updateConsoleFirmware(self, firmware_path: str) -> None:
        """Update the console (HV) firmware using DFU.  Runs in a background thread."""
        def _run():
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
            except Exception as e:
                msg = f"Console update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("console", False, msg)

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, int)
    def updateTransmitterFirmware(self, firmware_path: str, module: int) -> None:
        """Update the transmitter firmware for a specific module. Runs in a background thread."""
        def _run():
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
            except Exception as e:
                msg = f"Transmitter module {module} update failed: {e}"
                logger.error(msg)
                self.fwUpdateStatus.emit("transmitter", False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

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
                if config is None:
                    self.userConfigStatus.emit(target, False, "Failed to read config – no response from device.")
                    return

                json_str = config.get_json_str()
                logger.info(f"User config read from {target}: {json_str}")
                self.userConfigRead.emit(target, json_str)
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
                if updated is None:
                    self.userConfigStatus.emit(target, False, "Write failed – no response from device.")
                    return

                msg = f"Config written to {target}. Seq: {updated.header.seq}, CRC: 0x{updated.header.crc:04X}"
                logger.info(msg)
                self.userConfigStatus.emit(target, True, msg)
            except json.JSONDecodeError as e:
                msg = f"Invalid JSON: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            except Exception as e:
                msg = f"Error writing config to {target}: {e}"
                logger.error(msg)
                self.userConfigStatus.emit(target, False, msg)
            finally:
                self._interface_mutex.unlock()

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Solution loading functionality
    # ------------------------------------------------------------------

    @pyqtSlot(result='QVariantList')
    def getPresetSolutions(self):
        """Return preset solutions found in preset_solutions/*.json.

        Each item contains {"name": <solution name>, "path": <absolute file path>}.
        Invalid JSON files are skipped.
        """
        try:
            self._ensure_preset_solutions_seeded()
            preset_dir = self._get_runtime_preset_solutions_path()
            pattern = os.path.join(preset_dir, "*.json")
            preset_files = sorted(glob.glob(pattern))

            presets = []
            for file_path in preset_files:
                if os.path.basename(file_path).lower() == "default_solution.json":
                    continue
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        solution_data = json.load(f)

                    if not isinstance(solution_data, dict):
                        logger.warning(f"Skipping preset with invalid JSON root type: {file_path}")
                        continue

                    display_name = solution_data.get('name') or os.path.splitext(os.path.basename(file_path))[0]
                    presets.append({
                        "name": str(display_name),
                        "path": os.path.normpath(file_path)
                    })
                except Exception as e:
                    logger.warning(f"Skipping unreadable preset file {file_path}: {e}")

            presets.sort(key=lambda item: item.get("name", "").lower())
            logger.info(f"Discovered {len(presets)} preset solution(s)")
            return presets
        except Exception as e:
            logger.error(f"Error indexing preset solutions: {e}")
            return []

    @pyqtSlot(str, result=bool)
    def loadPresetSolution(self, file_path):
        """Load a preset solution by file path.

        This is a convenience wrapper that uses the same logic as loading any solution file.
        """
        return self.loadSolutionFromFile(file_path)
    
    @pyqtSlot(str, result=bool)
    def loadSolutionFromFile(self, file_path):
        """Load a solution from a JSON file and apply it to the UI controls.
        
        Args:
            file_path: The path to the solution JSON file
            
        Returns:
            bool: True if loading was successful, False otherwise
        """
        try:
            logger.info(f"Attempting to load solution from: {file_path}")
            
            # Normalize the path for the current OS
            normalized_path = os.path.normpath(file_path)
            logger.info(f"Normalized path: {normalized_path}")
            
            # Validate file exists and is readable
            if not os.path.exists(normalized_path):
                error_msg = f"File not found: {normalized_path}"
                logger.error(error_msg)
                self.solutionLoadError.emit(error_msg)
                return False
                
            if not os.path.isfile(normalized_path):
                error_msg = f"Path is not a file: {normalized_path}"
                logger.error(error_msg)
                self.solutionLoadError.emit(error_msg)
                return False
                
            with open(normalized_path, 'r', encoding='utf-8') as f:
                solution_data = json.load(f)
                
            logger.info(f"Successfully parsed JSON from {normalized_path}")
            logger.info(f"JSON data type: {type(solution_data)}")
            if isinstance(solution_data, dict):
                logger.info(f"JSON keys: {list(solution_data.keys())}")
            else:
                logger.warning(f"Unexpected JSON data type: {type(solution_data)}, value: {str(solution_data)[:100]}")
            
            # Validate solution structure
            if not self._validate_solution_format(solution_data):
                return False
                
            # If transducer is connected, verify element count matches modules
            if self._txConnected:
                self.queryNumModules()  # Update module count
                expected_elements = self._num_modules_connected * NUM_ELEMENTS_PER_MODULE
                actual_elements = len(solution_data.get('transducer', {}).get('elements', []))
                
                if expected_elements != actual_elements:
                    error_message = f"Element count mismatch!\nExpected: {expected_elements} elements ({self._num_modules_connected} modules × {NUM_ELEMENTS_PER_MODULE})\nFound in solution: {actual_elements} elements"
                    self.solutionLoadError.emit(error_message)
                    return False
            
            # Store loaded solution data
            self._loaded_solution_data = solution_data
            self._solution_loaded = True
            self._solution_name = solution_data.get('name', 'Unnamed Solution')
            
            # Emit success signal with solution details
            if "name" in solution_data:
                message = f"Loaded solution '{solution_data['name']}' from file"
            else:
                message = f"Loaded solution with {len(solution_data.get('transducer', {}).get('elements', []))} elements"
            logger.info(message)
            self.solutionFileLoaded.emit(self._solution_name, message)
            self.solutionStateChanged.emit()
            
            logger.info(f"Successfully loaded solution: {self._solution_name}")
            return True
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON format: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
        except PermissionError as e:
            error_msg = f"Permission denied accessing file: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Error loading solution: {str(e)}"
            logger.error(f"Error loading solution from {file_path}: {e}")
            self.solutionLoadError.emit(error_msg)
            return False
    
    def _validate_solution_format(self, solution_data):
        """Validate that the solution file has the required structure.
        
        Args:
            solution_data: The parsed JSON solution data
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # First check if solution_data is actually a dict
            if not isinstance(solution_data, dict):
                self.solutionLoadError.emit(f"Invalid solution format: expected JSON object, got {type(solution_data).__name__}")
                return False
            
            logger.info(f"Validating solution with keys: {list(solution_data.keys())}")
            
            # Check for required top-level fields
            required_fields = ['transducer', 'pulse', 'sequence']
            for field in required_fields:
                if field not in solution_data:
                    self.solutionLoadError.emit(f"Missing required field: {field}")
                    return False
            
            # Validate transducer structure
            transducer = solution_data['transducer']
            if not isinstance(transducer, dict):
                self.solutionLoadError.emit("Transducer field must be an object")
                return False
                
            if 'elements' not in transducer:
                self.solutionLoadError.emit("Missing 'elements' in transducer data")
                return False
                
            if not isinstance(transducer['elements'], list):
                self.solutionLoadError.emit("Transducer elements must be a list")
                return False
                
            # Validate pulse structure
            pulse = solution_data['pulse']
            if not isinstance(pulse, dict):
                self.solutionLoadError.emit("Pulse field must be an object")
                return False
                
            pulse_fields = ['frequency', 'duration']
            for field in pulse_fields:
                if field not in pulse:
                    self.solutionLoadError.emit(f"Missing pulse field: {field}")
                    return False
            
            # Validate sequence structure
            sequence = solution_data['sequence']
            if not isinstance(sequence, dict):
                self.solutionLoadError.emit("Sequence field must be an object")
                return False
                
            sequence_fields = ['pulse_interval', 'pulse_count']
            for field in sequence_fields:
                if field not in sequence:
                    self.solutionLoadError.emit(f"Missing sequence field: {field}")
                    return False
                    
            logger.info("Solution validation passed")
            return True
            
        except Exception as e:
            error_msg = f"Error validating solution format: {str(e)}"
            logger.error(error_msg)
            self.solutionLoadError.emit(error_msg)
            return False
    
    @pyqtSlot(result='QVariantMap')
    def getLoadedSolutionSettings(self):
        """Get the loaded solution settings to populate UI controls.
        
        Returns:
            QVariantMap: Dictionary containing solution settings
        """
        if not self._solution_loaded or not self._loaded_solution_data:
            return {}
        
        try:
            return self._extract_solution_settings(self._loaded_solution_data)
            
        except Exception as e:
            logger.error(f"Error extracting solution settings: {e}")
            return {}
    
    @pyqtSlot()
    def makeLoadedSolutionEditable(self):
        """Release the loaded solution data while preserving UI field values."""
        if self._solution_loaded:
            solution_name = self._solution_name
            self._solution_loaded = False
            self._loaded_solution_data = None
            self._solution_name = ""
            self.solutionStateChanged.emit()
            logger.info(f"Released solution '{solution_name}' - UI fields preserved, controls are now editable")
    
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
                            import json
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

    @pyqtSlot(int, int)
    def runThermalTest(self, frequency, num_modules):
        """Run the short-duration verification test."""
        logger.info(f"runThermalTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "short",
            lambda: TransmitterShortVerificationTest(args=args),
            "short-duration verification",
        )

    @pyqtSlot(int, int)
    def runLongVerificationTest(self, frequency, num_modules):
        """Run the full long verification sequence over PRODREQS cases."""
        logger.info(f"runLongVerificationTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "long",
            lambda: TransmitterHeatingPlaceholder(args=args),
            "long verification",
        )

    @pyqtSlot(int, int)
    def runIndefiniteTest(self, frequency, num_modules):
        """Run the indefinite-loop verification test."""
        logger.info(f"runIndefiniteTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "indefinite",
            lambda: TransmitterIndefiniteRun(args=args),
            "indefinite run verification",
        )

    @pyqtSlot(int, int)
    def runVoltageAccuracyTest(self, frequency, num_modules):
        """Run console voltage-accuracy test sequence."""
        logger.info(f"runVoltageAccuracyTest called: frequency={frequency}, num_modules={num_modules}")
        args = self._build_verification_args(frequency, num_modules)
        self._start_verification_test(
            "voltage",
            lambda: VoltageAccuracyTest(args=args),
            "voltage accuracy verification",
        )

    def _build_verification_args(self, frequency, num_modules, test_case=None):
        args = parse_arguments()
        args.frequency = int(frequency)
        args.num_modules = int(num_modules)
        args.interface = self.interface
        if test_case is not None:
            args.test_case = int(test_case)
        return args

    def _start_verification_test(self, test_kind, factory, display_name):
        if self.running_thread is not None and self.running_thread.is_alive():
            logger.warning("Previous test still shutting down, ignoring start request")
            return

        self._abort_requested = False
        self._running = True
        self._active_test_kind = test_kind
        self.update_state()

        try:
            self.thermal_test_instance = factory()
        except Exception as e:
            self._running = False
            self._active_test_kind = ""
            self.update_state()
            logger.error(f"Failed to initialize {display_name}: {e}")
            return

        self._start_progress_timer()

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                logger.info(f"Running {display_name}...")

                if self._abort_requested and self.thermal_test_instance:
                    with contextlib.suppress(Exception):
                        self.thermal_test_instance.shutdown_event.set()
                    self.thermal_test_instance.test_status = "aborted by user"
                    logger.info(f"{display_name} aborted before execution started")
                    return

                self.thermal_test_instance.run()

            except Exception as e:
                logger.exception(f"\n !! Fatal error in {display_name} worker: {e}")
                with contextlib.suppress(Exception):
                    if self.thermal_test_instance:
                        self.thermal_test_instance.shutdown_event.set()
            finally:
                self._running = False
                self.update_state()
                loop.close()
                logger.info(f"Updated state: {self._state}")

        self.running_thread = threading.Thread(target=_run, daemon=True)
        self.running_thread.start()
        logger.info("Thread started, returning to event loop")

    @pyqtSlot()
    def _stop_thermal_test(self):
        self._abort_requested = True
        if self.thermal_test_instance:
            with contextlib.suppress(Exception):
                self.thermal_test_instance.shutdown_event.set()
            self.thermal_test_instance.test_status = "aborted by user"
            logger.info("Thermal test stop requested")
        else:
            logger.info("Thermal test stop requested before runner initialization")

    @pyqtSlot()
    def stopVerificationTest(self):
        self._stop_thermal_test()

    def _start_progress_timer(self):
        logger.info("_____Starting progress timer for thermal test")
        if hasattr(self, "_progress_timer") and self._progress_timer.isActive():
            self._progress_timer.stop()
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(250)
        self._progress_timer.timeout.connect(self._emit_test_progress)
        self._progress_timer.start()
        logger.info(f"Timer active: {self._progress_timer.isActive()}")

    def _stop_progress_timer(self):
        if hasattr(self, "_progress_timer"):
            self._progress_timer.stop()

    def _emit_test_progress(self):
        runner = self.thermal_test_instance
        if runner is None:
            self._stop_progress_timer()
            return

        worker_alive = hasattr(self, "running_thread") and self.running_thread is not None and self.running_thread.is_alive()

        test_kind = self._active_test_kind or "short"

        status = str(getattr(runner, "test_status", "not started") or "not started")
        status_lower = status.lower()
        sequence_duration = float(getattr(runner, "sequence_duration", 0) or 0)
        test_case_start_time = float(getattr(runner, "test_case_start_time", 0.0) or 0.0)
        start_time = float(getattr(runner, "start_time", 0.0) or 0.0)
        is_in_cooldown = bool(getattr(runner, "is_in_cooldown", False))
        log_file_path = str(getattr(runner, "log_file_path", "") or "")

        terminal_status = {"passed", "temperature shutdown", "voltage deviation", "error", "aborted by user"}

        if test_kind == "indefinite":
            current_case = int(getattr(runner, "test_case_num", getattr(runner, "test_case", 1)) or 1)

            if status_lower == "running" and sequence_duration > 0 and test_case_start_time > 0:
                elapsed_case = time.time() - test_case_start_time
                case_frac = min(elapsed_case / sequence_duration, 1.0)
            elif status_lower in terminal_status and test_case_start_time > 0:
                case_frac = 1.0
            else:
                case_frac = 0.0

            total_frac = case_frac
            total_label = "Overall - indefinite run"
            if is_in_cooldown:
                check_time = datetime.now() + timedelta(seconds=TIME_BETWEEN_TESTS_TEMPERATURE_CHECK_SECONDS)
                case_label = f"Cycle test status: cooldown, checking again at {check_time.strftime('%H:%M')}"
            else:
                case_label = f"Cycle test case {current_case}: {status}"
        else:
            if test_kind == "short":
                total_cases = 1
                starting_case = 1
            elif test_kind == "voltage":
                total_cases = len(TEST_VOLTAGES)
                starting_case = int(getattr(runner, "starting_test_case", 1) or 1)
            else:
                total_cases = len(TEST_CASES)
                starting_case = int(getattr(runner, "starting_test_case", 1) or 1)

            current_case = int(getattr(runner, "test_case_num", starting_case) or starting_case)
            actual_total_cases = max(total_cases - starting_case + 1, 1)

            if status_lower == "running" and sequence_duration > 0 and test_case_start_time > 0:
                elapsed_case = time.time() - test_case_start_time
                case_frac = min(elapsed_case / sequence_duration, 1.0)
            elif status_lower in terminal_status and test_case_start_time > 0:
                case_frac = 1.0
            else:
                case_frac = 0.0

            cases_completed = max(current_case - starting_case, 0)
            if test_kind == "voltage":
                if is_in_cooldown:
                    total_frac = min(cases_completed / actual_total_cases, 1.0)
                else:
                    total_frac = min((cases_completed + case_frac) / actual_total_cases, 1.0)

                if (not worker_alive) and status_lower in terminal_status and current_case >= (starting_case + actual_total_cases - 1):
                    total_frac = 1.0

                total_label = f"Overall - case {min(cases_completed + 1, actual_total_cases)}/{actual_total_cases}"
            else:
                if is_in_cooldown:
                    total_frac = min(cases_completed / actual_total_cases, 1.0)
                else:
                    total_frac = min((cases_completed + case_frac) / actual_total_cases, 1.0)

                total_label = f"Overall - case {min(cases_completed + 1, actual_total_cases)}/{actual_total_cases}"

            if is_in_cooldown:
                check_time = datetime.now() + timedelta(seconds=TIME_BETWEEN_TESTS_TEMPERATURE_CHECK_SECONDS)
                case_label = f"Test Status: cooldown, checking again at {check_time.strftime('%H:%M')}"
            else:
                case_label = f"Test Status: {status}"

        if is_in_cooldown:
            status_color = "#3498DB"       # blue for cooldown
        elif status_lower == "running":
            status_color = "#E2A84A"       # yellow
        elif status_lower == "passed":
            status_color = "#2ECC71"       # green
        elif status_lower in ("temperature shutdown", "voltage deviation", "error"):
            status_color = "#E74C3C"       # red
        elif status_lower == "aborted by user":
            status_color = "#F39C12"       # orange
        else:
            status_color = "#BDC3C7"       # grey/idle

        self.testProgressUpdated.emit(total_frac, case_frac, total_label, case_label, status_color, log_file_path)

        # Keep polling until the worker thread has fully exited run(), including its finally block.
        if not worker_alive:
            self._stop_progress_timer()
