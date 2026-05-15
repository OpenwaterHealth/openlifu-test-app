"""Controller-page slots mixed into :class:`LIFUConnector`.

Holds solution loading, transmitter configuration, sonication
control (start/stop/pause/resume/abort), HV-enable-mode policy, and
direct device parameter setters used by the Controller page (and
partially by the operator interface).

Mixed into :class:`LIFUConnector` via MRO; depends on the host
class for ``self.interface``, ``self._interface_mutex``, signals,
state flags, run-progress helpers, thermal helpers, and module
constants (imported lazily to avoid a circular dependency).
"""
from __future__ import annotations

import copy
import glob
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import numpy as np

from PyQt6.QtCore import pyqtSlot

from openlifu_sdk.io.exceptions import (
    LIFUError,
    LIFUCommunicationError,
    LIFUSolutionError,
    LIFUHVSettleError
)

from plot.plot import generate_ultrasound_plot_from_solution

from lifu.lifu_constants import (
    READY,
    RUNNING,
    HV_EN_AUTO,
    HV_EN_ON,
    HV_EN_OFF,
    HV_EN_WHILE_RUNNING,
    HV_EN_MODES,
    SPEED_OF_SOUND,
    NUM_ELEMENTS_PER_MODULE,
)

logger = logging.getLogger(__name__)

__all__ = ["ControllerMixin"]


class ControllerMixin:
    """Controller-page solution/sonication/HV-mode slots."""

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
        num_modules = self._num_modules_connected if self._num_modules_connected > 0 else 1
        if self._solution_loaded:
            logger.info("Using loaded solution for configuration")
            solution = self._loaded_solution_data
            if solution['sequence']['pulse_train_interval'] == 0:
                solution['sequence']['pulse_train_interval'] = solution['sequence']['pulse_count'] * solution['sequence']['pulse_interval']
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
            # Controller UI displays frequency in kHz, duration in microseconds, and pulse interval in ms.
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
            logger.debug(f"{num_modules}x config file loaded")
            distances = np.sqrt(np.sum((focus - element_positions)**2, 1))
            tof = distances*1e-3 / SPEED_OF_SOUND
            delays = tof.max() - tof
            apodizations = np.ones(numelements)
            transducer_dummy = self._build_transducer_from_pinmap(pinmap_data)

            pulse_count = int(pulseCount)
            pulse_train_interval = float(trainInterval)
            if pulse_train_interval == 0:
                pulse_train_interval = pulse_count * pulse_interval_seconds
            sequence = {"pulse_interval": pulse_interval_seconds,
                        "pulse_count": pulse_count,
                        "pulse_train_interval": pulse_train_interval,
                        "pulse_train_count": int(trainCount)}
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

    @pyqtSlot(str, result=bool)
    def directSetVoltage(self, voltage_str):
        """Directly set the HV rail voltage without reconfiguring the solution."""
        if not self._hvConnected:
            logger.error("Cannot set voltage: No HV device connected")
            return False
        self._interface_mutex.lock()
        try:
            voltage = float(voltage_str)
            ok = self._call_with_comm_retry(
                "Set Voltage",
                self.interface.hvcontroller.set_voltage,
                voltage=voltage,
            )
            if ok:
                logger.info(f"Voltage directly set to {voltage} V")
                return True
            logger.error("Failed to directly set voltage")
            return False
        except LIFUCommunicationError as e:
            # Final retry attempt failed -- surface the timeout to the
            # user. Drop out of READY so they have to reconfigure before
            # starting (the device's solution state is now suspect).
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Voltage", e,
                                    context="Communication timeout")
            return False
        except (ValueError, TypeError) as e:
            self._emit_device_error("Set Voltage", f"Invalid voltage value: {e}")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Voltage", e)
            return False
        except Exception as e:
            logger.error(f"Error in directSetVoltage: {e}")
            return False
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot(str, str, str, str, str, result=bool)
    def directSetSequence(self, pulseInterval, pulseCount, trainInterval, trainCount, mode):
        """Directly update trigger/sequence parameters without re-running the full configuration."""
        if not self._txConnected:
            self._emit_device_error("Set Sequence", "No TX device connected.")
            return False
        # Pause telemetry polling so the 1 Hz poll thread does not inject
        # temperature / voltage queries between this burst's chunks; see
        # ``LIFUConnector._pause_polling_during_burst`` for rationale.
        prev_paused = self._monitoring_paused
        self._monitoring_paused = True
        self._interface_mutex.lock()
        prev_async = self._async_mode_enabled
        self._set_async_mode(False, reason="directSetSequence")
        try:
            pulse_interval_s = float(pulseInterval) * 1e-3  # UI ms -> s
            pulse_count = int(pulseCount)
            pulse_train_interval_s = float(trainInterval)   # UI already in seconds
            if pulse_train_interval_s == 0:
                pulse_train_interval_s = pulse_count * pulse_interval_s
            pulse_train_count = int(trainCount)            
            trigger_mode = str(mode).lower()
            result = self._call_with_comm_retry(
                "Set Sequence",
                self.interface.txdevice.set_trigger,
                pulse_interval=pulse_interval_s,
                pulse_count=pulse_count,
                pulse_train_interval=pulse_train_interval_s,
                pulse_train_count=pulse_train_count,
                trigger_mode=trigger_mode,
            )
            self._update_trigger_state(result)
            logger.info(
                "Sequence settings directly updated "
                "(pulse_int=%sms pulses=%s train_int=%ss trains=%s mode=%s)",
                pulseInterval, pulseCount, trainInterval, trainCount, mode,
            )
            return True
        except LIFUCommunicationError as e:
            # All retries exhausted. The on-device trigger config is
            # now in an unknown state; force the user back through
            # Configure before another Start.
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Sequence", e,
                                    context="Communication timeout")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Sequence", e)
            return False
        except (ValueError, TypeError) as e:
            self._emit_device_error("Set Sequence", f"Invalid sequence parameters: {e}")
            return False
        except Exception as e:
            self._handle_lifu_error("Set Sequence", e, context="Unexpected error")
            return False
        finally:
            if prev_async and self._state == RUNNING:
                self._set_async_mode(True, reason="directSetSequence-restore")
            self._interface_mutex.unlock()
            self._monitoring_paused = prev_paused


    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def directSetPulse(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode):
        """Directly update pulse/transducer settings without touching the HV controller."""
        if not self._txConnected:
            self._emit_device_error("Set Pulse", "No TX device connected.")
            return False
        # Pause telemetry polling for the duration of the burst.
        prev_paused = self._monitoring_paused
        self._monitoring_paused = True
        self._interface_mutex.lock()
        prev_async = self._async_mode_enabled
        self._set_async_mode(False, reason="directSetPulse")
        try:
            solution = self.get_solution(xInput, yInput, zInput, freq, voltage,
                                         pulseInterval, pulseCount, trainInterval, trainCount, durationS)
            if solution is None:
                self._emit_device_error("Set Pulse", "Failed to build a valid solution.")
                return False
            transducer = solution.get("transducer") if isinstance(solution, dict) else None
            invert_flag = bool(transducer["module_invert"]) if (
                transducer is not None and "module_invert" in transducer
            ) else False
            self._call_with_comm_retry(
                "Set Pulse (module invert)",
                self.interface.txdevice.set_module_invert,
                invert_flag,
            )
            self._call_with_comm_retry(
                "Set Pulse",
                self.interface.txdevice.set_solution,
                pulse=solution['pulse'],
                delays=solution['delays'],
                apodizations=solution['apodizations'],
                sequence=solution['sequence'],
                trigger_mode=str(mode).lower(),
            )
            logger.info(
                "Pulse settings directly updated "
                "(voltage=%sV freq=%skHz focus=(%s,%s,%s)mm "
                "pulse_len=%sus pulse_int=%sms pulses=%s "
                "train_int=%ss trains=%s mode=%s)",
                voltage, freq, xInput, yInput, zInput,
                durationS, pulseInterval, pulseCount,
                trainInterval, trainCount, mode,
            )
            return True
        except LIFUCommunicationError as e:
            # All retries exhausted. The on-device pulse/solution is
            # now in an unknown state; force the user back through
            # Configure before another Start.
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Set Pulse", e,
                                    context="Communication timeout")
            return False
        except LIFUError as e:
            self._handle_lifu_error("Set Pulse", e)
            return False
        except Exception as e:
            self._handle_lifu_error("Set Pulse", e, context="Unexpected error")
            return False
        finally:
            if prev_async and self._state == RUNNING:
                self._set_async_mode(True, reason="directSetPulse-restore")
            self._interface_mutex.unlock()
            self._monitoring_paused = prev_paused


    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def configure_transmitter(self, xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS, mode):
        """Simulate configuring the transmitter."""
        if not self._txConnected:
            self._emit_device_error("Configure Transmitter", "No TX device connected.")
            return
        self.queryNumModules()
        solution = self.get_solution(xInput, yInput, zInput, freq, voltage, pulseInterval, pulseCount, trainInterval, trainCount, durationS)
        if solution is None:
            self._emit_device_error("Configure Transmitter", "Failed to build a valid solution.")
            return

        # Pause telemetry polling for the duration of the burst. The
        # full configure pipeline includes a large set_solution that
        # chunks into many write_block round-trips; keeping the poll
        # thread out of the queue during that burst materially reduces
        # spurious comm timeouts under GUI load.
        prev_paused = self._monitoring_paused
        self._monitoring_paused = True
        self._interface_mutex.lock()
        # Async STATUS frames share the TX device's CDC IN endpoint with
        # command responses; large set_solution writes (write_block chunks)
        # routinely race with STATUS emissions when async is left on.
        # Force async OFF for the duration of the write.
        self._set_async_mode(False, reason="configure_transmitter")
        try:
            self._call_with_comm_retry(
                "Configure Transmitter",
                self.interface.set_solution,
                solution,
                trigger_mode=mode,
            )
            self._configured = True
            self.update_state()
            self._apply_auto_hv_for_state()
            logger.info(
                f"[CONFIGURE] Transmitter configured: "
                f"voltage={voltage}V freq={freq}kHz focus=({xInput},{yInput},{zInput})mm "
                f"pulse_len={durationS}us pulse_int={pulseInterval}ms pulses={pulseCount} "
                f"train_int={trainInterval}s trains={trainCount} mode={mode}"
            )
        except LIFUCommunicationError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e,
                                    context="Communication timeout")
        except LIFUSolutionError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e,
                                    context="Solution failed safety checks")
        except LIFUError as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e)
        except Exception as e:
            self._configured = False
            self.update_state()
            self._handle_lifu_error("Configure Transmitter", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()
            self._monitoring_paused = prev_paused


    @pyqtSlot()
    def reset_configuration(self):
        """Reset system configuration to defaults.

        Also releases any solution that was loaded from file so the UI
        controls become editable again and the next Configure rebuilds
        the solution from the on-screen parameters rather than re-using
        the buffered file-loaded solution.
        """
        self._configured = False
        if self._solution_loaded:
            released = self._solution_name
            self._solution_loaded = False
            self._loaded_solution_data = None
            self._solution_name = ""
            self.solutionStateChanged.emit()
            logger.info(f"Released loaded solution '{released}' on reset")
        self.update_state()
        self._apply_auto_hv_for_state()
        logger.info("Configuration reset")


    @pyqtSlot()
    def start_sonication(self):
        """Start the beam, transitioning to RUNNING state."""
        if self._state != READY:
            return
        if not self._hv_ready():
            self._emit_device_error(
                "Start Sonication",
                "HV is not ready (disconnected or HV enable set to OFF).",
            )
            return
        self._interface_mutex.lock()
        try:
            # Determine HV control parameters based on enable mode.
            # AUTO already energized HV at Configure time, but turn_hv_on=True
            # is harmless if it's already on. WHILE_RUNNING turns it on now.
            turn_hv_on = self._hv_enable_mode == HV_EN_WHILE_RUNNING
            wait_for_settle = turn_hv_on  # Always wait for settle

            # Enable the unsolicited STATUS stream so the UI gets push-mode
            # temperature and trigger-state updates without polling the TX
            # device while it is sonicating.
            logger.info('[START] Turning on Sonication')
            self._call_with_comm_retry(
                "Start Sonication",
                self.interface.start_sonication,
                turn_hv_on=turn_hv_on,
                wait_for_settle=wait_for_settle,
                async_mode=True,
            )
            self._async_mode_enabled = True
            self._state = RUNNING
            self.stateChanged.emit(self._state)
            logger.info(f"[START] Sonication started "
                        f"(HV mode: {HV_EN_MODES[self._hv_enable_mode]}, "
                        f"turn_hv_on={turn_hv_on})")
        except LIFUCommunicationError as e:
            # SDK leaves async OFF on early-write failures. Stay in
            # READY -- start failure doesn't invalidate the loaded
            # solution, the user can just try Start again.
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e,
                                    context="Communication timeout")
        except LIFUHVSettleError as e:
            # SDK leaves async OFF on settle failure (start_sonication only
            # toggles it after the HV settle succeeds). Mirror that here.
            self._async_mode_enabled = False
            # Stay in READY state; notify UI of the failure.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e,
                                    context="HV rail did not settle")
        except LIFUError as e:
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e)
        except Exception as e:
            self._async_mode_enabled = False
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Start Sonication", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot()
    def stop_sonication(self):
        """Stop the beam and return to READY state."""
        if self._state != RUNNING:
            return
        self._interface_mutex.lock()
        try:
            # AUTO keeps HV energized while still configured; only WHILE_RUNNING
            # drops the rail at sonication-stop.
            turn_hv_off = (self._hv_enable_mode == HV_EN_WHILE_RUNNING)
            self._call_with_comm_retry(
                "Stop Sonication",
                self.interface.stop_sonication,
                turn_hv_off=turn_hv_off,
            )
            # SDK's stop_sonication() turns async OFF after stopping the
            # trigger; mirror that on our tracker so subsequent state
            # queries (e.g. directSet*) don't try to restore it.
            self._async_mode_enabled = False
            self._state = READY
            self.stateChanged.emit(self._state)
            logger.info(f"[STOP] Sonication stopped "
                        f"(HV mode: {HV_EN_MODES[self._hv_enable_mode]}, "
                        f"turn_hv_off={turn_hv_off})")
        except LIFUCommunicationError as e:
            # Do not change local state -- hardware may still be running.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e,
                                    context="Communication timeout")
        except LIFUError as e:
            # Do not change local state if the stop failed â€“ hardware may still be running.
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e)
        except Exception as e:
            self.stateChanged.emit(self._state)
            self._handle_lifu_error("Stop Sonication", e, context="Unexpected error")
        finally:
            self._interface_mutex.unlock()


    @pyqtSlot(int)
    def setHvEnableMode(self, hv_en_mode):
        """Set HV enable mode (0=AUTO, 1=ON, 2=OFF, 3=WHILE_RUNNING)."""
        if hv_en_mode not in HV_EN_MODES:
            logger.warning(f"Invalid HV enable mode: {hv_en_mode}")
            return

        # Prevent changing HV mode while running
        if self._state == RUNNING:
            logger.warning("Cannot change HV enable mode while running")
            return

        # Prevent setting "ON" mode when HV is not connected
        if hv_en_mode == HV_EN_ON and not self._hvConnected:  # ON mode
            logger.warning("Cannot set HV to ON mode: HV device not connected")
            return

        old_mode = self._hv_enable_mode
        self._hv_enable_mode = hv_en_mode
        self.hvEnableModeChanged.emit(hv_en_mode)
        logger.info(f"HV enable mode changed: {HV_EN_MODES.get(old_mode, 'Unknown')} -> {HV_EN_MODES.get(hv_en_mode, 'Unknown')}")

        # Handle immediate HV changes for ON/OFF/AUTO/WHILE_RUNNING modes.
        # AUTO turns the rail on as soon as the device is configured (READY),
        # otherwise it stays off. WHILE_RUNNING never asserts here (waits for
        # start_sonication).
        if self._hvConnected:
            try:
                if hv_en_mode == HV_EN_ON:
                    self.interface.hvcontroller.turn_hv_on()
                    logger.info("HV turned on (ON mode)")
                elif hv_en_mode == HV_EN_OFF:
                    self.interface.hvcontroller.turn_hv_off()
                    logger.info("HV turned off (OFF mode)")
                elif hv_en_mode == HV_EN_AUTO:
                    if self._state >= READY:
                        self.interface.hvcontroller.turn_hv_on()
                        logger.info("HV turned on (AUTO mode, configured)")
                    else:
                        self.interface.hvcontroller.turn_hv_off()
                        logger.info("HV turned off (AUTO mode, not configured)")
                elif hv_en_mode == HV_EN_WHILE_RUNNING:
                    # Drop the rail unless we're already mid-run; only
                    # start_sonication should energize in this mode.
                    if self._state != RUNNING:
                        self.interface.hvcontroller.turn_hv_off()
                        logger.info("HV turned off (WHILE_RUNNING mode, not running)")
            except LIFUError as e:
                self._handle_lifu_error("HV Enable Mode", e,
                                        context=f"Failed to apply mode '{HV_EN_MODES.get(hv_en_mode, 'Unknown')}'")
            except Exception as e:
                self._handle_lifu_error("HV Enable Mode", e, context="Unexpected error")

            # Refresh the QML side immediately so the HV LED reacts without
            # waiting for the next telemetry poll.
            try:
                hv_state = self.interface.hvcontroller.get_hv_status()
                v12_state = self.interface.hvcontroller.get_12v_status()
                self.powerStatusReceived.emit(bool(v12_state), bool(hv_state))
            except Exception as e:
                logger.warning(f"Could not refresh power status after HV mode change: {e}")

        # Update state after mode change (important for OFF->ON transitions)
        self.update_state()
    

    @pyqtSlot(result='QStringList')
    def getHvEnableModes(self):
        """Return the list of HV enable mode options.

        Index order matches the HV_EN_* integer constants and is the
        contract used by QML ComboBoxes (Controller, Operator) for index-based
        selection. Append-only.
        """
        return ["AUTO", "ON", "OFF", "WHILE_RUNNING"]
        

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
                    error_message = f"Element count mismatch!\nExpected: {expected_elements} elements ({self._num_modules_connected} modules Ã— {NUM_ELEMENTS_PER_MODULE})\nFound in solution: {actual_elements} elements"
                    self.solutionLoadError.emit(error_message)
                    return False
            
            # Store loaded solution data
            self._loaded_solution_data = solution_data
            self._solution_loaded = True
            self._solution_name = solution_data.get('name', 'Unnamed Solution')

            # A freshly loaded solution must be re-Configured before it can
            # run; drop the configured flag and refresh the UI state.
            self._configured = False
            self.update_state()

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
        if self._hv_enable_mode == HV_EN_ON and self._hvConnected:
            try:
                self.interface.hvcontroller.turn_hv_off()
                logger.info("HV turned off to allow editing")
            except LIFUError as hv_e:
                self._handle_lifu_error("Edit Solution", hv_e,
                                        context="Failed to turn off HV")
            except Exception as hv_e:
                self._handle_lifu_error("Edit Solution", hv_e,
                                        context="Unexpected error turning off HV")
        if self._solution_loaded:
            solution_name = self._solution_name
            self._solution_loaded = False
            self._loaded_solution_data = None
            self._solution_name = ""
            self.solutionStateChanged.emit()
            logger.info(f"Released solution '{solution_name}' - UI fields preserved, controls are now editable")
