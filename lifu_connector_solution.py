from __future__ import annotations

import glob
import json
import logging
import os
import sys
from pathlib import Path

from PyQt6.QtCore import pyqtSlot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure scripts/ is importable for plot generation
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from generate_ultrasound_plot import generate_ultrasound_plot_from_solution as _gen_plot
except ImportError:
    _gen_plot = None


class SolutionSlotsMixin:
    """Qt slots for solution/preset and ultrasound plot management."""

    # -----------------------------------------------------------------------
    # Plot generation
    # -----------------------------------------------------------------------

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str)
    def generate_plot(self, x: str, y: str, z: str,
                      frequency: str, voltage: str,
                      pulse_interval: str, pulse_count: str,
                      train_interval: str, train_count: str,
                      duration: str, mode: str):
        """Generate the ultrasound plot and emit :attr:`plotGenerated`."""
        if _gen_plot is None:
            logger.error("generate_plot: generate_ultrasound_plot module not found")
            return
        try:
            solution   = self._build_solution_dict(x, y, z, frequency, voltage,
                                                   pulse_interval, pulse_count,
                                                   train_interval, train_count, duration)
            image_data = _gen_plot(solution, mode="buffer")
            if image_data:
                # Strip data-URI prefix if present (QML handler prepends it)
                if image_data.startswith("data:image/png;base64,"):
                    image_data = image_data[len("data:image/png;base64,"):]
                self.plotGenerated.emit(image_data)
        except Exception as exc:
            logger.error("generate_plot: %s", exc)

    # -----------------------------------------------------------------------
    # Solution file I/O
    # -----------------------------------------------------------------------

    @pyqtSlot(str)
    def loadSolutionFromFile(self, path: str):
        """Load a solution JSON file and update solution state."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._loaded_solution = data
            self._solution_loaded = True
            self.solutionLoadedChanged.emit()
            self.solutionStateChanged.emit()
            name = data.get("name", os.path.basename(path))
            self.solutionFileLoaded.emit(name, f"Loaded: {path}")
        except Exception as exc:
            logger.error("loadSolutionFromFile: %s", exc)
            self._solution_loaded = False
            self._loaded_solution = None
            self.solutionLoadError.emit(str(exc))

    @pyqtSlot(str, str, str, str, str, str, str, str, str, str, str, str, str, str, result=bool)
    def saveSolutionToFile(self, solution_id: str, name: str, path: str,
                           num_modules: str, x: str, y: str, z: str,
                           frequency: str, voltage: str,
                           pulse_interval: str, pulse_count: str,
                           train_interval: str, train_count: str,
                           duration: str) -> bool:
        """Build a solution dict from UI parameters and write it to *path*."""
        try:
            solution = self._build_solution_dict(x, y, z, frequency, voltage,
                                                  pulse_interval, pulse_count,
                                                  train_interval, train_count, duration)
            solution["id"]          = solution_id
            solution["name"]        = name
            solution["num_modules"] = int(float(num_modules))
            dest = os.path.abspath(path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(solution, fh, indent=2)
            self.solutionSaveStatus.emit(True, f"Saved to {dest}")
            return True
        except Exception as exc:
            logger.error("saveSolutionToFile: %s", exc)
            self.solutionSaveStatus.emit(False, str(exc))
            return False

    # -----------------------------------------------------------------------
    # Preset solutions
    # -----------------------------------------------------------------------

    @pyqtSlot(result='QVariant')
    def getPresetSolutions(self) -> list:
        """Return ``[{name, path}, ...]`` for every JSON in ``preset_solutions/``."""
        preset_dir = self.getPresetSolutionsPath()
        results: list[dict] = []
        try:
            for p in sorted(glob.glob(os.path.join(preset_dir, "*.json"))):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    results.append({"name": data.get("name", os.path.basename(p)), "path": p})
                except Exception:
                    results.append({"name": os.path.basename(p), "path": p})
        except Exception as exc:
            logger.warning("getPresetSolutions: %s", exc)
        return results

    @pyqtSlot(result=str)
    def getPresetSolutionsPath(self) -> str:
        return str(Path(__file__).parent / "preset_solutions")

    @pyqtSlot(result=str)
    def getDefaultSolutionFilePath(self) -> str:
        return str(Path(__file__).parent / "preset_solutions" / "default_solution.json")

    # -----------------------------------------------------------------------
    # Solution settings
    # -----------------------------------------------------------------------

    @pyqtSlot(result='QVariant')
    def getLoadedSolutionSettings(self) -> dict:
        if not self._loaded_solution:
            return self.getDefaultSolutionSettings()
        s   = self._loaded_solution
        seq = s.get("sequence", {})
        pls = s.get("pulse", {})
        pos = s.get("target", {}).get("position", [0, 0, 50])
        return {
            "xInput":        pos[0] if len(pos) > 0 else 0,
            "yInput":        pos[1] if len(pos) > 1 else 0,
            "zInput":        pos[2] if len(pos) > 2 else 50,
            "frequency":     pls.get("frequency", 400_000) / 1_000.0,   # Hz → kHz
            "duration":      pls.get("duration", 200e-6) * 1e6,         # s  → µs
            "voltage":       s.get("voltage", 12.0),
            "pulseInterval": seq.get("pulse_interval", 0.1) * 1_000.0,  # s  → ms
            "pulseCount":    seq.get("pulse_count", 1),
            "trainInterval": seq.get("pulse_train_interval", 0.0),
            "trainCount":    seq.get("pulse_train_count", 1),
            "numModules":    s.get("num_modules", self._manual_num_modules),
        }

    @pyqtSlot(result='QVariant')
    def getDefaultSolutionSettings(self) -> dict:
        default_path = self.getDefaultSolutionFilePath()
        if os.path.exists(default_path):
            try:
                with open(default_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._loaded_solution = data
                self._solution_loaded = True
                return self.getLoadedSolutionSettings()
            except Exception as exc:
                logger.warning("getDefaultSolutionSettings: could not load default: %s", exc)
        return {
            "xInput": 0, "yInput": 0, "zInput": 50,
            "frequency": 400.0, "duration": 200.0, "voltage": 12.0,
            "pulseInterval": 100.0, "pulseCount": 1,
            "trainInterval": 0.0, "trainCount": 1,
            "numModules": self._manual_num_modules,
        }

    @pyqtSlot()
    def makeLoadedSolutionEditable(self):
        """Unset solutionLoaded so UI controls become editable."""
        self._solution_loaded = False
        self.solutionLoadedChanged.emit()
        self.solutionStateChanged.emit()

    @pyqtSlot(int)
    def setManualNumModules(self, n: int):
        self._manual_num_modules = n
        if not self._tx_connected:
            self._num_modules = n

    # -----------------------------------------------------------------------
    # Private helper
    # -----------------------------------------------------------------------

    def _build_solution_dict(self, x, y, z, frequency, voltage,
                              pulse_interval, pulse_count,
                              train_interval, train_count, duration) -> dict:
        """Return a minimal solution dict suitable for generate_ultrasound_plot.

        If a solution is already loaded the beam geometry (transducer, delays,
        apodizations) is reused; timing and focus position come from the
        provided parameters.
        """
        freq_hz      = float(frequency) * 1_000.0       # kHz → Hz
        dur_s        = float(duration) / 1e6             # µs  → s
        p_interval_s = float(pulse_interval) / 1_000.0  # ms  → s
        p_count      = int(float(pulse_count))
        t_interval_s = float(train_interval)             # already seconds
        t_count      = int(float(train_count))
        volt         = float(voltage)
        x_mm, y_mm, z_mm = float(x), float(y), float(z)

        base = self._loaded_solution or {}

        # Normalise delays / apodizations to flat 1-D lists for the plot
        raw_delays = base.get("delays", [[0.0]])
        raw_apod   = base.get("apodizations", [[1.0]])
        delays       = raw_delays[0] if isinstance(raw_delays[0], list) else raw_delays
        apodizations = raw_apod[0]   if isinstance(raw_apod[0],   list) else raw_apod

        return {
            "id":           base.get("id", "solution"),
            "name":         base.get("name", "Solution"),
            "delays":       delays,
            "apodizations": apodizations,
            "transducer":   base.get("transducer", {}),
            "voltage":      volt,
            "pulse": {
                "frequency": freq_hz,
                "amplitude": 1.0,
                "duration":  dur_s,
            },
            "sequence": {
                "pulse_interval":       p_interval_s,
                "pulse_count":          p_count,
                "pulse_train_interval": t_interval_s,
                "pulse_train_count":    t_count,
            },
            "target": {
                "position": [x_mm, y_mm, z_mm],
                "units":    "mm",
            },
        }
