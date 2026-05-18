"""Verification-test methods for :class:`LIFUConnector`.

These methods drive the four production verification suites (thermal,
long, indefinite, voltage accuracy) and the QML progress widget that
tracks them. Mixed into :class:`~lifu.lifu_connector.LIFUConnector` so
the Testing page's QML can bind to them as if they were defined on the
connector directly.

Signals consumed (defined on ``LIFUConnector``):
    - ``testProgressUpdated`` (float, float, str, str, str, str)

Instance attributes consumed (initialized in ``LIFUConnector.__init__``):
    - ``running_thread``, ``thermal_test_instance``, ``_active_test_kind``
    - ``_abort_requested``, ``_running``, ``_state``, ``interface``
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import datetime, timedelta

from PyQt6.QtCore import QTimer, pyqtSlot

# These imports duplicate ones in lifu_connector.py because the mixin is
# the actual user of these names. Keep both in sync until the verification
# suite is itself moved into the SDK.
from openlifu_verification.prodreqs_base_class import (
    TEST_CASES,
    TIME_BETWEEN_TESTS_TEMPERATURE_CHECK_SECONDS,
)
from openlifu_verification.prodreqs_tx_long_verification_test import (
    TransmitterHeatingPlaceholder,
    parse_arguments,
)
from openlifu_verification.prodreqs_voltage_accuracy_test import (
    TEST_VOLTAGES,
    VoltageAccuracyTest,
)
from openlifu_verification.prodreqs_tx_short_verification_test import (
    TransmitterShortVerificationTest,
)
from openlifu_verification.prodreqs_run_indefinitely_test import (
    TransmitterIndefiniteRun,
)

logger = logging.getLogger(__name__)


class TestingMixin:
    """Mixin providing the Testing-page slots/helpers for ``LIFUConnector``."""

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
        logger.info("Starting progress timer for thermal test")
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


__all__ = ["TestingMixin"]
