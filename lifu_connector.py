from PyQt6.QtCore import QObject, pyqtSignal, pyqtProperty, pyqtSlot
import logging
import os
import threading
import numpy as np
import base58
import re
import json
from scripts.generate_ultrasound_plot import generate_ultrasound_plot  # Import the function directly
from openlifu_sdk.io.LIFUInterface import LIFUInterface

# from openlifu.bf.pulse import Pulse
# from openlifu.bf.sequence import Sequence
# from openlifu.geo import Point
# from openlifu.plan.solution import Solution
# from openlifu.xdc import Transducer
# from openlifu.xdc.util import load_transducer_from_file

logger = logging.getLogger("LIFUConnector")
# Set up logging
logger.setLevel(logging.INFO)
logger.propagate = False

# Create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Add formatter to ch
ch.setFormatter(formatter)
# Add ch to logger
logger.addHandler(ch)


# Define system states
DISCONNECTED = 0
TX_CONNECTED = 1
CONFIGURED = 2
READY = 3
RUNNING = 4

class LIFUConnector(QObject):
    # Ensure signals are correctly defined
    signalConnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDisconnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDataReceived = pyqtSignal(str, str)  # (descriptor, data)
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

    stateChanged = pyqtSignal(int)  # Notifies QML when state changes
    connectionStatusChanged = pyqtSignal()  # 🔹 New signal for connection updates
    triggerStateChanged = pyqtSignal(bool)  # 🔹 New signal for trigger state change
    txConfigStateChanged = pyqtSignal(bool)  # 🔹 New signal for tx configured state change

    # Firmware update signals
    fwUpdateProgress = pyqtSignal(str, int, int)  # (label, written, total)
    fwUpdateStatus = pyqtSignal(str, bool, str)   # (device_type, success, message)
    fwVersionRead = pyqtSignal(str, str)           # (device_type, version)

    def __init__(self, hv_test_mode=False):
        super().__init__()
        self.interface = LIFUInterface(HV_test_mode=hv_test_mode, run_async=True)
        self._txConnected = False
        self._hvConnected = False
        self._configured = False
        self._state = DISCONNECTED
        self._trigger_state = False  # Internal state to track trigger status
        self._txconfigured_state = False  # Internal state to track trigger status
        self._num_modules_connected = 0

        self.connect_signals()

    def connect_signals(self):
        """Connect LIFUInterface signals to QML."""
        self.interface.signal_connect.connect(self.on_connected)
        self.interface.signal_disconnect.connect(self.on_disconnected)
        self.interface.signal_data_received.connect(self.on_data_received)

    def update_state(self):
        """Update system state based on connection and configuration."""
        if not self._txConnected and not self._hvConnected:
            self._state = DISCONNECTED
        elif self._txConnected and not self._configured:
            self._state = TX_CONNECTED
        elif self._txConnected and self._hvConnected and self._configured:
            self._state = READY
        elif self._txConnected and self._configured:
            self._state = CONFIGURED
        self.stateChanged.emit(self._state)  # Notify QML of state update
        logger.info(f"Updated state: {self._state}")

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

    @pyqtSlot()
    async def start_monitoring(self):
        """Start monitoring for device connection asynchronously."""
        try:
            logger.info("Starting device monitoring...")
            await self.interface.start_monitoring()
        except Exception as e:
            logger.error(f"Error in start_monitoring: {e}", exc_info=True)

    @pyqtSlot()
    def stop_monitoring(self):
        """Stop monitoring device connection."""
        try:
            logger.info("Stopping device monitoring...")
            self.interface.stop_monitoring()
        except Exception as e:
            logger.error(f"Error while stopping monitoring: {e}", exc_info=True)

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

            except Exception as e:
                logger.error(f"Failed to parse and update trigger state: {e}")

    @pyqtSlot(str, float)
    def configureSolution(self, solutionName, amplitude):
        """Configures the solution and emits status to QML."""
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

    @pyqtSlot(str, str, str, str, str, str, str)
    def generate_plot(self, x, y, z, freq, cycles, trigger, mode):
        """Generates an ultrasound plot and emits data to QML."""
        try:
            logger.info(f"Generating plot: X={x}, Y={y}, Z={z}, Frequency={freq}, Cycles={cycles}, Trigger={trigger}, Mode={mode}")
            image_data = generate_ultrasound_plot(x, y, z, freq, cycles, trigger, mode)

            if image_data == "ERROR":
                logger.error("Plot generation failed")
            else:
                logger.info("Plot generated successfully")
                self.plotGenerated.emit(image_data)  # Send image data to QML

        except Exception as e:
            logger.error(f"Error generating plot: {e}")

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def configure_transmitter(self, xInput, yInput, zInput, freq, voltage, triggerHZ, pulseCount, trainInterval, trainCount, durationS, mode):
        """Simulate configuring the transmitter."""
        if self._txConnected:
            # pulse = Pulse(frequency=float(freq), duration=float(durationS))
            # pt = Point(position=(float(xInput),float(yInput),float(zInput)), units="mm")
            # 
            # self.queryNumModules()
# 
            # arr = load_transducer_from_file(fR".\pinmap_{self._num_modules_connected}x.json")
            # logger.info(f"{self._num_modules_connected}x config file loaded")
            # 
            # focus = pt.get_position(units="mm")
# 
            # distances = np.sqrt(np.sum((focus - arr.get_positions(units="mm"))**2, 1))
            # tof = distances*1e-3 / 1500
            # delays = tof.max() - tof
            # apodizations = np.ones(arr.numelements())
            # sequence = Sequence(
            #     pulse_interval=1.0/float(triggerHZ),
            #     pulse_count=int(pulseCount),
            #     pulse_train_interval=float(trainInterval),
            #     pulse_train_count=int(trainCount)
            # )
# 
            # solution = Solution(
            #     id="solution",
            #     name="Solution",
            #     protocol_id="example_protocol",
            #     transducer="example_transducer",
            #     delays = delays,
            #     apodizations = apodizations,
            #     pulse = pulse,
            #     sequence = sequence,
            #     voltage=float(voltage),
            #     target=pt,
            #     foci=[pt],
            #     approved=True
            # )
            # 
            # self.interface.set_solution(solution, trigger_mode=mode)

            self._configured = True
            self.update_state()
            logger.info("Transmitter configured")

        
    @pyqtSlot(int, int, result=bool)
    def setSimpleTxConfig(self, freq: float, pulses: int):
        print(freq, pulses)
        # pulse = Pulse(frequency=freq, duration=float(1e-5), amplitude=1.0)
        # pt = Point(position=(0, 0, 25), units="mm")
# 
        # sequence = Sequence(
        #     pulse_interval=1.0/freq,
        #     pulse_count=int(1),
        #     pulse_train_interval=float(0),
        #     pulse_train_count=int(1)
        # )
# 
        # solution = Solution(
        #     id="solution",
        #     name="Solution",
        #     protocol_id="example_protocol",
        #     transducer_id="example_transducer",
        #     delays = np.zeros((1,64)),
        #     apodizations = np.ones((1,64)),
        #     pulse = pulse,
        #     sequence = sequence,
        #     target=pt,
        #     foci=[pt],
        #     approved=True
        # )
# 
        # sol_dict = solution.to_dict()
        # profile_index = 1
        # profile_increment = True
        # logger.error(f">>>>>>>>>>>>>>>>>>> Set Solution {solution}")
        # ret_status = self.interface.set_solution(solution = solution)

        self._txconfigured_state = True
        self.txConfigStateChanged.emit(self._txconfigured_state)
        return True

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
            self.interface.hvcontroller.turn_hv_on()
            if self.interface.txdevice.start_trigger():
                self._state = RUNNING
            else:
                logger.info("Failed to start trigger")
            self.stateChanged.emit(self._state)
            logger.info("Sonication started")

    @pyqtSlot()
    def stop_sonication(self):
        """Stop the beam and return to READY state."""
        if self._state == RUNNING:
            if self.interface.stop_sonication():
                self._state = READY
            else:
                logger.info("Failed to stop trigger")
            self.stateChanged.emit(self._state)
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
    
    @pyqtSlot()
    def queryHvInfo(self):
        """Fetch and emit device information."""
        try:
            fw_version = self.interface.hvcontroller.get_version()
            logger.info(f"Version: {fw_version}")
            hw_id = self.interface.hvcontroller.get_hardware_id()
            device_id = base58.b58encode(bytes.fromhex(hw_id)).decode()
            self.hvDeviceInfoReceived.emit(fw_version, device_id)
            logger.info(f"Device Info - Firmware: {fw_version}, Device ID: {device_id}")
        except Exception as e:
            logger.error(f"Error querying device info: {e}")

    @pyqtSlot()
    def queryTxInfo(self):
        """Fetch and emit device information for all TX modules as a list."""
        try:
            module_count = self.interface.txdevice.get_module_count()
            modules_info = []
            for module_idx in range(module_count):
                fw_version = self.interface.txdevice.get_version(module=module_idx)
                logger.info(f"Version: {fw_version}")
                hw_id = self.interface.txdevice.get_hardware_id(module=module_idx)
                if hw_id:
                    device_id = base58.b58encode(bytes.fromhex(hw_id)).decode()
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

    @pyqtProperty(int, notify=numModulesUpdated)
    def queryNumModulesConnected(self):
        """Fetch and emit number of connected TX modules."""
        return self._num_modules_connected

    @pyqtSlot()
    def queryHvTemperature(self):
        """Fetch and emit temperature data."""
        try:
            temp1 = self.interface.hvcontroller.get_temperature1()  
            temp2 = self.interface.hvcontroller.get_temperature2()  

            self.temperatureHvUpdated.emit(temp1, temp2)
            logger.info(f"Temperature Data - Temp1: {temp1}, Temp2: {temp2}")
        except Exception as e:
            logger.error(f"Error querying temperature data: {e}")


    @pyqtSlot()
    def queryTxTemperature(self):
        """Fetch and emit temperature data."""
        try:
            for module in range(0, self._num_modules_connected):
                tx_temp = self.interface.txdevice.get_temperature(module=module)  
                amb_temp = self.interface.txdevice.get_ambient_temperature(module=module)  

                self.temperatureTxUpdated.emit(module, tx_temp, amb_temp)
                logger.info(f"Module: {module} Temperature Data - Temp1: {tx_temp}, Temp2: {amb_temp}")
        except Exception as e:
            logger.error(f"Error querying Module: {module} temperature data: {e}")

    @pyqtSlot()
    def queryNumModules(self):
        """Fetch and emit number of connected TX modules."""
        try:
            self._num_modules_connected = self.interface.txdevice.get_tx_module_count()
            self.numModulesUpdated.emit()
            logger.info(f"Number of connected TX modules: {self._num_modules_connected}")

        except Exception as e:
            logger.error(f"Error querying number of TX modules: {e}")

    @pyqtSlot(int)
    def setRGBState(self, state):
        """Set the RGB state using integer values."""
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
            
    @pyqtSlot()
    def queryRGBState(self):
        """Fetch and emit RGB state."""
        try:
            state = self.interface.hvcontroller.get_rgb_led()
            state_text = {0: "Off", 1: "Red", 2: "Green", 3: "Blue"}.get(state, "Unknown")

            logger.info(f"RGB State: {state_text}")
            self.rgbStateReceived.emit(state, state_text)  # Emit both values
        except Exception as e:
            logger.error(f"Error querying RGB state: {e}")

    @pyqtSlot()
    def queryPowerStatus(self):
        """Fetch and emit HV state."""
        try:
            hv_state = self.interface.hvcontroller.get_hv_status()            
            v12_state = self.interface.hvcontroller.get_12v_status()
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error querying Power status: {e}")
    
    @pyqtSlot(bool)
    def setAsyncMode(self, enable: bool):
        """Set the async mode for the interface."""
        try:
            ret = self.interface.txdevice.async_mode(enable)
            logger.info(f"Async mode set to: {ret}")
        except Exception as e:
            logger.error(f"Error setting async mode: {e}")

    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendPingCommand(self, target: str, index: int = 0):
        """Send a ping command to HV device."""
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
        
    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendLedToggleCommand(self, target: str, index: int = 0):
        """Send a LED Toggle command to device."""
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
        
    @pyqtSlot(str, result=bool)
    @pyqtSlot(str, int, result=bool)
    def sendEchoCommand(self, target: str, index: int = 0):
        """Send Echo command to device."""
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
    
    @pyqtSlot(str, result=bool)
    def setHVCommand(self, strval: str):
        """Set High voltage command to device."""
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
    
    @pyqtSlot(int, int, result=bool)
    def setFanLevel(self, fid: int, speed: int):
        """Set Fan Level to device."""
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
    
    @pyqtSlot(str, result=bool)
    def setTrigger(self, triggerjson: str):
        """Set trigger settings on the device using JSON data."""
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

    @pyqtSlot(result=bool)
    def toggleTrigger(self):
        """Toggle the trigger state (start or stop)."""
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

    @pyqtSlot(result=bool)
    def queryTriggerInfo(self):
        """Query the trigger status and update the state accordingly.

        Returns:
            bool: True if the query was successful, False otherwise.
        """
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
        
    @pyqtSlot()
    def softResetHV(self):
        """reset hardware HV device."""
        try:
            if self.interface.hvcontroller.soft_reset():
                logger.info(f"Software Reset Sent")
            else:
                logger.error(f"Failed to send Software Reset")
        except Exception as e:
            logger.error(f"Error Sending Software Reset: {e}")

    @pyqtSlot()
    def toggleHV(self):
        """Toggle HV on console."""
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

    @pyqtSlot()
    def turnOffHV(self):
        """Toggle HV on console."""
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
            logger.info(f"HV State: {hv_state} - 12V State: {v12_state}")
            self.powerStatusReceived.emit(v12_state, hv_state)
        except Exception as e:
            logger.error(f"Error toggling HV: {e}")

    @pyqtSlot()
    def toggleV12(self):
        """Toggle V12 on console."""
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

    @pyqtSlot()
    def getMonitorVoltages(self):
        """Get voltage monitor readings from console."""
        try:
            voltages = self.interface.hvcontroller.get_vmon_values()
            logger.debug(f"Voltage readings: {voltages}")
            # Emit the voltage readings to QML
            self.monVoltagesReceived.emit(voltages)
        except Exception as e:
            logger.error(f"Error getting voltages: {e}")

    @pyqtSlot()
    def softResetTX(self):
        """reset hardware TX device."""
        try:
            if self.interface.txdevice.soft_reset():
                logger.info(f"Software Reset Sent")
            else:
                logger.error(f"Failed to send Software Reset")
        except Exception as e:
            logger.error(f"Error Sending Software Reset: {e}")

    @pyqtSlot(int)
    def softResetTXModule(self, module: int):
        """Soft reset a specific TX module by index."""
        try:
            if self.interface.txdevice.soft_reset(module=module):
                logger.info(f"Software Reset Sent to module {module}")
            else:
                logger.error(f"Failed to send Software Reset to module {module}")
        except Exception as e:
            logger.error(f"Error Sending Software Reset to module {module}: {e}")

        
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
        try:
            version = self.interface.hvcontroller.get_version()
            self.fwVersionRead.emit("console", version)
            logger.info(f"Console firmware version: {version}")
            return version
        except Exception as e:
            logger.error(f"Error reading console firmware version: {e}")
            self.fwVersionRead.emit("console", "Error")
            return "Error"

    @pyqtSlot(int, result=str)
    def readTxFirmwareVersion(self, module: int) -> str:
        """Read and return the current transmitter firmware version for a given module."""
        try:
            version = self.interface.txdevice.get_version(module=module)
            self.fwVersionRead.emit(f"transmitter_{module}", version)
            logger.info(f"Transmitter module {module} firmware version: {version}")
            return version
        except Exception as e:
            logger.error(f"Error reading transmitter module {module} firmware version: {e}")
            self.fwVersionRead.emit(f"transmitter_{module}", "Error")
            return "Error"

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

        threading.Thread(target=_run, daemon=True).start()