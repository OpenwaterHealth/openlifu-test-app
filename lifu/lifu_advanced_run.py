"""Advanced runs for the Controller page: repeated, temperature-gated sonications.

A run repeats the configured solution a set number of times. Each
repetition starts only once the hottest module is at or below the start
temperature, and is stopped early if it reaches the shutoff temperature.
Between repetitions the transmitter rests for a set time, either powered
or switched off (HV and 12 V). After that it must be at or below the start
temperature: a powered transmitter waits until it is, and a switched-off
one is powered back up, reconfigured and checked, sleeping again if not.

Each run writes its own log in the format the openlifu-test-scripts
thermal tests use, so ``thermal_log_plot.py`` can plot it.

Mixed into :class:`LIFUConnector`; signals and state live there.
"""
from __future__ import annotations

import logging
import os
import time

from PyQt6.QtCore import pyqtSlot

from openlifu_sdk.io.exceptions import LIFUError

from lifu.lifu_constants import (
    READY,
    RUNNING,
    THERMAL_COOLING_THRESHOLD_C,
    THERMAL_SHUTDOWN_THRESHOLD_C,
)

logger = logging.getLogger(__name__)

__all__ = ["AdvancedRunMixin"]

# The TX firmware stops a sequence at this temperature whatever the app asks.
FIRMWARE_SHUTOFF_C = 75.0
# How long the TX may take to re-enumerate after 12 V comes back on.
POWER_ON_TIMEOUT_S = 90.0
# A temperature older than this is not trusted to start or stop a repetition.
TEMP_MAX_AGE_S = 15.0
# Spacing of the cooldown samples logged while the TX stays powered.
COOLING_LOG_INTERVAL_S = 10.0


class AdvancedRunMixin:
    """Repeat-run state machine, driven by a 1 s timer on the Qt thread."""

    # ------------------------------------------------------------------
    # QML slots
    # ------------------------------------------------------------------

    @pyqtSlot(result='QVariantMap')
    def getAdvancedRunDefaults(self):
        """Start/shutoff temperatures and train-count choices for the dialog.

        Taken from a loaded JSON file when it has them, otherwise the app's
        thermal thresholds.
        """
        thermal = {}
        selections = []
        if self._solution_loaded and self._loaded_solution_data:
            thermal = self._loaded_solution_data.get("thermal") or {}
            selections = self._loaded_solution_data.get("pulse_train_count_selections") or []
        return {
            "startC": float(thermal.get("start_C", THERMAL_COOLING_THRESHOLD_C)),
            "shutoffC": float(thermal.get("shutoff_C", THERMAL_SHUTDOWN_THRESHOLD_C)),
            "fromFile": bool(thermal),
            "trainCountSelections": [int(c) for c in selections],
            "firmwareShutoffC": FIRMWARE_SHUTOFF_C,
        }

    @pyqtSlot('QVariantList', 'QVariantMap', result=bool)
    def startAdvancedRun(self, configure_args, settings):
        """Start a repeated run of the configured solution.

        *configure_args* are the Configure button's arguments, reused to
        reprogram the TX after each power cycle. *settings* holds
        ``repetitions``, ``startC``, ``shutoffC``, ``restMinutes`` (time
        between repetitions) and ``powerOff`` (transmitter off during it).
        """
        if self._ar_phase != "idle":
            return False
        try:
            reps = int(settings["repetitions"])
            start_c = float(settings["startC"])
            shutoff_c = float(settings["shutoffC"])
            rest_min = float(settings["restMinutes"])
            power_off = bool(settings["powerOff"])
        except (KeyError, TypeError, ValueError):
            self._emit_device_error("Advanced Run", "The advanced settings are incomplete or not numbers.")
            return False
        problem = None
        if reps < 1:
            problem = "Repetitions must be at least 1."
        elif start_c >= shutoff_c:
            problem = "The start temperature must be below the shutoff temperature."
        elif rest_min < 0:
            problem = "The time between repetitions can't be negative."
        elif power_off and rest_min <= 0:
            problem = "Turning the transmitter off needs more than 0 minutes between repetitions."
        elif len(configure_args) < 11 or configure_args[10] == "Continuous":
            problem = "Advanced runs need the Sequence or Single trigger mode; Continuous never ends."
        elif self._state != READY:
            problem = "Configure the transmitter before starting."
        elif not self._hv_ready():
            problem = "HV is not ready (disconnected or HV enable set to OFF)."
        if problem:
            self._emit_device_error("Advanced Run", problem)
            return False

        self._ar_configure_args = list(configure_args)
        self._ar_settings = {"repetitions": reps, "start_c": start_c, "shutoff_c": shutoff_c,
                             "rest_min": rest_min, "power_off": power_off}
        self._ar_modules = self._num_modules_connected
        self._ar_rep = 1
        self._ar_temps = {}
        self._ar_open_log()
        self._ar_log_header()
        self._ar_timer.start()
        try:
            self._ar_begin_rep()
        except Exception as e:
            logger.exception("Advanced run failed to start")
            self._ar_abort(f"Unexpected error: {e}")
        return self._ar_phase != "idle"

    @pyqtSlot()
    def stopAdvancedRun(self):
        """Abort the run, stopping a repetition in progress and powering the TX back up."""
        if self._ar_phase == "idle":
            return
        self._ar_write("Advanced run stopped by user.")
        if self._state == RUNNING:
            self.stop_sonication()
        if self._ar_phase in ("powered_off", "powering_on"):
            self._ar_set_12v(True)
        self._ar_finish("Stopped by user.")

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _ar_tick(self):
        # PyQt6 aborts the app on an exception escaping a slot, so a
        # surprise here ends the run (stopping the TX) instead.
        try:
            self._ar_step()
        except Exception as e:
            logger.exception("Advanced run step failed")
            self._ar_abort(f"Unexpected error: {e}")

    def _ar_step(self):
        phase = self._ar_phase
        if phase == "idle":
            self._ar_timer.stop()
            return
        if phase not in ("powered_off", "powering_on") and not self._txConnected:
            self._ar_abort("The transmitter disconnected during the advanced run.")
            return
        now = time.monotonic()

        if phase == "running":
            if self._state != RUNNING:
                self._ar_rep_finished()
                return
            reading = self._ar_hottest()
            if reading is None:
                return
            temp, ambient = reading
            self._ar_write(f"TX Temp: {temp:.2f}C, Ambient Temp: {ambient:.2f}C")
            if temp >= self._ar_settings["shutoff_c"]:
                self._ar_write(
                    f"TX temperature of {temp:.2f}C reached the shutoff temperature of "
                    f"{self._ar_settings['shutoff_c']:g}C. Stopping test case {self._ar_rep}.")
                self.stop_sonication()
                if self._state != RUNNING:
                    self._ar_rep_finished()
            return

        if phase == "cooling":
            reading = self._ar_hottest()
            if reading is None:
                return
            temp = reading[0]
            if temp <= self._ar_settings["start_c"]:
                self._ar_proceed(temp)
            elif now - self._ar_last_cool_log >= COOLING_LOG_INTERVAL_S:
                self._ar_last_cool_log = now
                self._ar_write(self._ar_too_hot_text(temp) + " Waiting with the transmitter on.")
                self._ar_set_status(f"cooling with the transmitter on ({temp:.1f} °C)")
            return

        if phase == "resting":
            if now < self._ar_deadline:
                return
            reading = self._ar_hottest()
            if reading is None:
                return
            temp = reading[0]
            if temp <= self._ar_settings["start_c"]:
                self._ar_proceed(temp)
            else:
                self._ar_wait_powered(temp)
            return

        if phase == "powered_off":
            if now >= self._ar_deadline:
                self._ar_power_on()
            return

        if phase == "powering_on":
            if self._txConnected and self._num_modules_connected > 0:
                self._ar_after_power_on()
            elif now >= self._ar_deadline:
                self._ar_abort(
                    f"The transmitter did not come back within {POWER_ON_TIMEOUT_S:g} s "
                    "of turning 12 V back on.")

    def _ar_begin_rep(self):
        """Log the repetition's header and gate its start on temperature."""
        k, s = self._ar_rep, self._ar_settings
        self._ar_set_status("checking temperature")
        self._ar_write(f"Starting test case {k} of {s['repetitions']}")
        self._ar_write(self._ar_case_config_text(k))
        self._ar_wait_started = time.monotonic()
        reading = self._ar_hottest(refresh=True)
        if reading is None:
            self._ar_abort("No transmitter temperature reading is available.")
            return
        temp = reading[0]
        self._ar_write(f"Initial TX temperature: {temp:.2f}C")
        if k > 1 and s["rest_min"] > 0:
            # The rest between repetitions comes first, however cool it is.
            if s["power_off"]:
                self._ar_write(f"Transmitter will turn off for {s['rest_min']:g} minutes "
                               "to cool down and then check again.")
                self._ar_power_off()
            else:
                self._ar_write(f"Transmitter will stay on for {s['rest_min']:g} minutes "
                               "before the next repetition.")
                self._ar_phase = "resting"
                self._ar_deadline = time.monotonic() + s["rest_min"] * 60
                self._ar_set_status(f"resting with the transmitter on until {self._ar_clock(s['rest_min'])}")
        elif temp <= s["start_c"]:
            self._ar_proceed(temp, cooled=False)
        elif s["power_off"]:
            self._ar_write(self._ar_too_hot_text(temp)
                           + f" Transmitter will turn off for {s['rest_min']:g} minutes "
                           "to cool down and then check again.")
            self._ar_power_off()
        else:
            self._ar_wait_powered(temp)

    def _ar_wait_powered(self, temp):
        """Wait with the transmitter on until it is cool enough to start."""
        self._ar_last_cool_log = time.monotonic()
        self._ar_write(self._ar_too_hot_text(temp) + " Waiting with the transmitter on.")
        self._ar_phase = "cooling"
        self._ar_set_status(f"cooling with the transmitter on ({temp:.1f} °C)")

    def _ar_proceed(self, temp, cooled=True):
        """Start the current repetition, reconfiguring first if the TX lost it."""
        k, s = self._ar_rep, self._ar_settings
        if cooled:
            minutes = (time.monotonic() - self._ar_wait_started) / 60
            self._ar_write(f"TX module took ~{round(minutes)} minutes to cool down to "
                           f"starting temperature of {s['start_c']:g}C.")
        self._ar_write(f"TX temperature of {temp:.2f}C is within the allowed starting "
                       f"temperature of {s['start_c']:g}C. Proceeding with test case {k}.")
        if self._state != READY and not self._ar_configure():
            return
        self.start_sonication()
        if self._state != RUNNING:
            self._ar_abort(f"Repetition {k} did not start.")
            return
        self._ar_phase = "running"
        self._ar_rep_started = time.monotonic()
        self._ar_set_status("running")
        self.advancedRunRepStarted.emit(k, s["repetitions"])

    def _ar_rep_finished(self):
        k, n = self._ar_rep, self._ar_settings["repetitions"]
        elapsed = int(round(time.monotonic() - self._ar_rep_started))
        self._ar_write("Trigger stopped successfully.")
        self._ar_write(f"TEST CASE {k} ran for a total of {elapsed // 60} min {elapsed % 60:02d} s.")
        if k >= n:
            self._ar_write("Repetition complete." if n == 1 else f"All {n} repetitions complete.")
            self._ar_finish(f"Finished {n}/{n} repetitions.")
            return
        self._ar_rep += 1
        self._ar_begin_rep()

    def _ar_power_off(self):
        self._ar_power_cycling = True
        self._ar_set_12v(False)
        self._ar_phase = "powered_off"
        self._ar_deadline = time.monotonic() + self._ar_settings["rest_min"] * 60
        self._ar_set_status(f"transmitter off, checking again at {self._ar_clock(self._ar_settings['rest_min'])}")

    @staticmethod
    def _ar_clock(minutes):
        """Wall-clock time *minutes* from now, e.g. '14:32'."""
        return time.strftime("%H:%M", time.localtime(time.time() + minutes * 60))

    def _ar_power_on(self):
        self._ar_write("Turning the transmitter back on.")
        self._ar_temps = {}
        self._ar_set_12v(True)
        self._ar_phase = "powering_on"
        self._ar_deadline = time.monotonic() + POWER_ON_TIMEOUT_S
        self._ar_set_status("powering the transmitter back on")

    def _ar_after_power_on(self):
        """Reprogram the re-enumerated TX, then start or go back to sleep."""
        self._ar_power_cycling = False
        if self._num_modules_connected != self._ar_modules:
            self._ar_abort(f"{self._num_modules_connected} module(s) came back after power-up, "
                           f"but the run started with {self._ar_modules}.")
            return
        if not self._ar_configure():
            return
        reading = self._ar_hottest(refresh=True)
        if reading is None:
            self._ar_abort("No transmitter temperature reading after power-up.")
            return
        temp = reading[0]
        if temp <= self._ar_settings["start_c"]:
            self._ar_proceed(temp)
        else:
            self._ar_write(self._ar_too_hot_text(temp)
                           + f" Transmitter will turn off for {self._ar_settings['rest_min']:g} "
                           "minutes to cool down and then check again.")
            self._ar_power_off()

    def _ar_configure(self):
        self.configure_transmitter(*self._ar_configure_args)
        if self._state != READY:
            self._ar_abort("Configure failed while reprogramming the transmitter.")
            return False
        return True

    def _ar_set_12v(self, on):
        """Switch the TX's 12 V (and HV with it when turning off)."""
        if not self._hvConnected:
            return
        self._interface_mutex.lock()
        try:
            if on:
                self.interface.hvcontroller.turn_12v_on()
            else:
                self.interface.hvcontroller.turn_hv_off()
                self.interface.hvcontroller.turn_12v_off()
            self._ar_write(f"{'12V turned on' if on else 'HV and 12V turned off'}.")
        except LIFUError as e:
            self._ar_write(f"Could not turn 12V {'on' if on else 'off'}: {e}", logging.ERROR)
        finally:
            self._interface_mutex.unlock()

    def _ar_abort(self, message):
        self._ar_write(f"Advanced run aborted: {message}", logging.ERROR)
        if self._state == RUNNING:
            self.stop_sonication()
        if self._ar_phase in ("powered_off", "powering_on"):
            self._ar_set_12v(True)
        self._ar_finish(f"Aborted: {message}")
        self._emit_device_error("Advanced Run", message)

    def _ar_finish(self, status):
        self._ar_timer.stop()
        self._ar_phase = "idle"
        self._ar_power_cycling = False
        self._ar_status = status
        self._ar_close_log()
        self.advancedRunChanged.emit()

    # ------------------------------------------------------------------
    # Temperatures
    # ------------------------------------------------------------------

    def _ar_on_temperature(self, module, tx_temp, amb_temp):
        self._ar_temps[int(module)] = (float(tx_temp), float(amb_temp), time.monotonic())

    def _ar_hottest(self, refresh=False):
        """(tx, ambient) of the hottest module with a recent reading, or None."""
        if refresh and self._state != RUNNING:
            self.queryTxTemperature()
        now = time.monotonic()
        fresh = [(t, a) for t, a, at in self._ar_temps.values() if now - at <= TEMP_MAX_AGE_S]
        return max(fresh) if fresh else None

    # ------------------------------------------------------------------
    # Run log
    # ------------------------------------------------------------------

    def _ar_open_log(self):
        logs_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        self._ar_log_path = os.path.join(logs_dir, time.strftime("%Y%m%d_%H%M%S") + "_advanced_run.log")
        handler = logging.FileHandler(self._ar_log_path, encoding="utf-8")
        # The test scripts' format, which thermal_log_plot.py parses.
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        run_logger = logging.getLogger("lifu.advanced_run.file")
        run_logger.setLevel(logging.INFO)
        run_logger.addHandler(handler)
        self._ar_log_handler = handler
        logger.info("Advanced run log: %s", self._ar_log_path)

    def _ar_close_log(self):
        if self._ar_log_handler is not None:
            logging.getLogger("lifu.advanced_run.file").removeHandler(self._ar_log_handler)
            self._ar_log_handler.close()
            self._ar_log_handler = None

    def _ar_write(self, message, level=logging.INFO):
        logging.getLogger("lifu.advanced_run.file").log(level, message)

    def _ar_log_header(self):
        s = self._ar_settings
        between = (f"{s['rest_min']:g} minutes between repetitions with the transmitter "
                   + ("off" if s["power_off"] else "on"))
        self._ar_write(f"Advanced run: {s['repetitions']} repetition(s), {between}.")
        self._ar_write(f"Max Starting Temperature: {s['start_c']:g}C")
        self._ar_write(f"Shutoff Temperature: {s['shutoff_c']:g}C")
        if s["shutoff_c"] > FIRMWARE_SHUTOFF_C:
            self._ar_write(f"The TX firmware still stops at {FIRMWARE_SHUTOFF_C:g}C.", logging.WARNING)
        minutes = self._ar_run_minutes()
        if minutes is not None:
            self._ar_write(f"Each test case will run for {minutes:.2f} minutes.")
        if self._solution_loaded and self._loaded_solution_path:
            self._ar_write(f"Solution source file: {self._loaded_solution_path}")
        for info in self._cached_tx_info or []:
            self._ar_write(f"TX Device {info.get('module')} HWID: {info.get('deviceId')}")

    def _ar_run_minutes(self):
        """Length of one repetition from the Configure arguments, or None."""
        args = self._ar_configure_args
        try:
            pulse_interval_s = float(args[5]) / 1000
            pulse_count = int(args[6])
            train_interval_s = float(args[7]) or pulse_interval_s * pulse_count
            train_count = int(args[8])
        except (IndexError, TypeError, ValueError):
            return None
        if args[10] == "Single":
            return pulse_interval_s * pulse_count / 60
        return train_interval_s * train_count / 60

    def _ar_case_config_text(self, k):
        """'Test Case k: ...' line in the test scripts' format."""
        args = self._ar_configure_args
        try:
            volts = round(float(args[4]))
            pulse_ms = round(float(args[9]) / 1000)
            pri_ms = round(float(args[5]))
            duty = round(100 * float(args[9]) / 1000 / float(args[5]))
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            return f"Test Case {k}: settings unavailable"
        return (f"Test Case {k}: {volts}V, {duty}% Duty Cycle, {pulse_ms}ms duration, "
                f"{pri_ms}ms PRI, Max Starting Temperature: {self._ar_settings['start_c']:g}C")

    def _ar_too_hot_text(self, temp):
        return (f"Current temperature of {temp:.2f}C is greater than max starting temperature "
                f"of {self._ar_settings['start_c']:g}C for test case {self._ar_rep}.")

    def _ar_set_status(self, text):
        self._ar_status = f"Rep {self._ar_rep}/{self._ar_settings['repetitions']}: {text}"
        self.advancedRunChanged.emit()
