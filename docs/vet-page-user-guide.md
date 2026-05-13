# Vet Page — User Guide

**Open-LIFU Engineering App**

---

## Overview

The **Vet** page is a single-screen kiosk UI for veterinary operators.
It hides every parameter that should not be touched at the bedside and
exposes only:

- a small set of **preset protocols** (hip / knee / spine, all 400 kPa),
- a **total duration** picker,
- session bookkeeping (operator-defined session name, log save toggle
  and folder),
- start / stop / pause controls,
- live progress, temperature, and HV-rail telemetry.

The Vet page is the *only* page shown when the app is launched with
`--vet` (or `--mode vet`). In that mode the sidebar is hidden and the
page fills the entire window. See
[launch-options.md](launch-options.md) for the full mode matrix.

![Vet tab — full layout](vet_tab.png)

---

## Page layout

The page is divided into a top row (three columns) and a bottom controls
panel.

| Region | Purpose |
|--------|---------|
| **Top-left** | Session controls + preset/duration pickers + collapsible Output Parameters readout |
| **Top-centre** | Pre-rendered intensity plot for the selected preset |
| **Top-right** | Connection status, system state, temperatures, HV rails |
| **Bottom** | Run progress bar + Configure / Start / Pause / Stop buttons |

---

## Session controls

| Field | Behaviour |
|-------|-----------|
| **Session Name** | Free-text label for the run. Sanitised into a `session_id` used in log file names. Persists across launches via `vet_session_settings.json` |
| **Save Log** | When checked, every run produces a per-run `.log` file under the chosen folder. Disabled while a run is active |
| **Log path text** | Click to open the destination folder in Explorer. Shows the *projected* file name for the next run when **Save Log** is on |
| **Change** | Open a folder picker for a new log destination |

The log file captures every logger (lifu_connector, openlifu_sdk,
verification, etc.) with elapsed-time stamps, plus any unhandled
exceptions raised during the run.

---

## Preset and duration

| Control | Source |
|---------|--------|
| **Preset** | Folders under [preset_vet_settings/](../preset_vet_settings) — currently `hip_400kpa`, `knee_400kpa`, `spine_400kpa` |
| **Total Duration** | Fixed list: 30 sec, 1 min, 2 min, 5 min, 10 min |

Switching the preset reloads voltage, frequency, focal depth, and pulse
parameters from the preset JSON. If the device is already configured the
new values are pushed live without needing to re-press **Configure**.
The plot area updates to the matching pre-rendered intensity PNG that
ships with each preset.

### Output Parameters (collapsible)

Click the **▶ Output Parameters** header to expand a read-only summary
of the active preset:

- **Voltage** — final per-rail voltage after applying per-device
  sensitivity scaling (so a particular module always produces the
  preset's calibration pressure regardless of sensitivity variation).
- **Pulse Length**, **Total Duration**, **Focal Depth**.
- **MI**, **TIS**, **ISPPA**, **ISTPA**, **PNP** — acoustic analysis
  values from the preset's `analysis` block.

---

## Status panel (top-right)

| Indicator | Meaning |
|-----------|---------|
| **TX LED** | Red = disconnected · Dark green = connected · Blue = sonication running · Green = configured |
| **HV LED** | Red = disconnected · Blue = HV rail energised · Green = ready · Dark green = connected, rail off |
| **System State** | Disconnected / Connected / Ready / Running / Cooling Down / Test Script Ready |
| **Temp [...]** | Per-module TX driver temperatures in °C |
| **Rails +x.xx / -x.xx V** | Most recent monitored HV rail readings |

---

## Run controls (bottom panel)

| Button | Behaviour |
|--------|-----------|
| **Configure** | Push the active preset + duration to the device. Required at least once before Start can be enabled. Re-pressing is harmless |
| **Start** | Begin sonication. The progress bar starts filling; system state goes to `Running` |
| **Pause** | Suspend a run partway. The bar stays at its current fraction and turns yellow; HV behaviour follows the connector's pause policy |
| **Resume** | Continue from where Pause left off |
| **Stop** | Abort the run. The bar turns orange and shows `Aborted` |

### Progress states

| State | Bar colour | Notes |
|-------|------------|-------|
| Idle | Grey | No run yet |
| Running | Blue | Shows percent + remaining time |
| Paused | Yellow | Cooling down or operator pause |
| Aborted | Orange | Stop pressed or thermal shutdown |
| Finished | Green | Run completed normally |

When a preset's full duration would push the TX above the cooling
threshold, the run engine automatically splits it into multiple
**blocks** with cool-down pauses between them. The progress text reads
e.g. *"Running [42%] (...applied in 3 blocks over...)"* in that case.

### Thermal safety

Two thresholds in [lifu/lifu_connector.py](../lifu/lifu_connector.py)
gate the run engine:

- **Cooling threshold** (50 °C by default) — the engine pauses, drops
  HV, and waits for the hottest module to come back below the
  threshold before resuming.
- **Shutdown threshold** (75 °C by default) — the engine aborts the
  run and pops a Vet-specific Thermal Shutdown dialog.

---

## Log toast

Every saved run is announced by a small transient banner at the bottom
of the page showing the full path of the log file. The banner auto-hides
after a few seconds. Click the log path text under **Save Log** at any
time to reopen the containing folder.

---

## Typical workflow

1. Connect the Console and Transmitter.
2. Type a **Session Name** (e.g. patient/animal ID).
3. Pick a **Preset** and **Total Duration**.
4. *(Optional)* Toggle **Save Log** off, or pick a different folder.
5. Click **Configure** — system state goes to `Ready`.
6. Click **Start**. Watch the progress bar and temperatures.
7. When the run finishes, the log toast shows where the `.log` file was
   written.

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Configure / Start greyed out | TX or HV not connected | Check the LEDs at the top-right |
| Progress turns yellow mid-run | Cooling-down pause | Wait — the run resumes automatically when modules cool below the threshold |
| Thermal Shutdown popup | Module temperature exceeded the shutdown threshold | Allow the system to cool before retrying. Check airflow / coupling |
| "Save Log" path text is greyed out | A run is in progress or paused | Stop or finish the current run before changing the destination |
| Output Parameters values look wrong for a preset | Per-device sensitivity scaling differs from calibration | Voltage is intentionally adjusted per module to hit the preset's calibration pressure |
