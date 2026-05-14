# Launch Options

**Open-LIFU Test App**

The Open-LIFU Test App is launched from `main.py` (or the packaged
`TestApp.exe`). A handful of command-line flags control which UI is
presented, whether real hardware is required, and how verbose the log
output is.

```bash
python main.py [--context NAME]
               [--simulate [N]] [--hv-test-mode]
               [--loglevel {debug,info,warning,error,critical}]
```

---

## UI mode: engineering vs. operator kiosk

The application has two operating modes, selected by the presence or
absence of `--context`:

| Invocation | Sidebar tabs shown | Intended user |
|------------|--------------------|---------------|
| *(no `--context`)* | Controller, Transmitter, Console, Verification, Settings | Engineering / hardware bring-up |
| `--context=<name>` | *(no sidebar — single Operator page kiosk UI)* | Bedside operator running a fixed protocol |

`--context=<name>` switches the app into operator-kiosk mode. The
sidebar is hidden, the [Operator](operator-interface-user-guide.md)
page fills the window, and the set of preset protocols + UI constants
is sourced from `preset_settings/<name>/`. Currently the repository
ships `vet/` (three 400 kPa presets for hip / knee / spine); adding a
new context is just a matter of creating a new sibling folder.

> **Note:** In operator-kiosk mode the Settings page is unreachable
> from the UI. Configuration changes (logging folder, session ID, etc.)
> are made directly from the Operator page's controls panel.

Packaged kiosk EXEs (e.g. `OpenLIFUDeviceController-vet.exe`) are
built by the release workflow with `OPENLIFU_CONTEXT` set; they
pre-inject `--context=<name>` into `sys.argv` and otherwise behave
identically to `python main.py --context=<name>`.

---

## Simulated hardware (`--simulate`)

```bash
python main.py --simulate                  # 2 simulated TX modules
python main.py --simulate 1                # 1 simulated TX module
python main.py --context=vet --simulate    # Operator-kiosk against the simulator
```

`--simulate` swaps the real `LIFUInterface` for an in-memory fake
([lifu/simulated_interface.py](../lifu/simulated_interface.py)) so the
app runs end-to-end with no USB hardware attached. It is useful for UI
development, demos, and pre-deployment training.

Behaviour:

- A simulated Console and one or more simulated TX modules auto-connect
  about half a second after the app starts.
- Reported temperatures track a simple thermal model: TX modules heat
  up while sonicating (`dT/dt = k·V²·duty`) and cool toward 25 °C
  otherwise.
- `STATUS` telemetry frames are emitted at the configured pulse-train
  cadence so the Controller / Operator progress bars advance just as
  they do against real firmware.
- When `--context=<name>` is passed alongside `--simulate`, the
  simulator is forced to `num_modules=1` regardless of the value
  passed to `--simulate` (the Operator page is a single-module kiosk).
- Only the Operator and Controller tabs are exposed in simulated mode;
  tabs that depend on real device behaviour (Console, Transmitter,
  Verification, Settings) are hidden.

> **Warning:** Simulated runs do **not** energise any rails. Do not
> use simulated logs as proof of hardware behaviour; they reflect only
> the thermal model in `simulated_interface.py`.

---

## High-voltage test mode (`--hv-test-mode`)

```bash
python main.py --hv-test-mode
```

Tells the underlying SDK to drive the system as if an external lab
power supply is attached in place of the on-board high-voltage rails.
HV-on/off commands become no-ops on the firmware side, so the app's
voltage monitor readings reflect whatever the bench supply is
sourcing.

This flag is intended for engineering bring-up only.

---

## Logging level (`--loglevel`)

```bash
python main.py --loglevel debug
```

Sets the level on the `lifu.lifu_connector` logger before the app
starts emitting messages. Accepted values: `debug`, `info` (default),
`warning`, `error`, `critical`.

Notes:

- Console output is always shown; this flag only changes the
  *severity* filter.
- Operator-kiosk runs additionally write a per-run log file under the
  configured log folder, formatted with elapsed-time stamps. Those
  files capture every logger (not just `lifu_connector`) at the level
  selected here, and they include any unhandled exceptions raised
  during the run.
- The `openlifu_sdk` logger is **not** wired up by default; uncomment
  the block at the top of
  [lifu/lifu_connector.py](../lifu/lifu_connector.py) if you want
  SDK-level frame traces.

---

## SDK version check

Every launch verifies the installed `openlifu-sdk` package against the
minimum pinned in `lifu/lifu_connector.py` (`MIN_SDK_VERSION`). If the
installed version is too old, the app pops a "Incompatible
openlifu-sdk version" dialog and exits with status 2. Upgrade with:

```bash
pip install --upgrade "openlifu-sdk>=<min_version>"
```

---

## Common invocations

| Situation | Command |
|-----------|---------|
| Normal engineering use, real hardware | `python main.py` |
| Operator kiosk (vet context), real hardware | `python main.py --context=vet` |
| UI demo or training, no hardware | `python main.py --simulate` |
| Operator-kiosk demo, no hardware | `python main.py --context=vet --simulate` |
| Bench bring-up with external HV supply | `python main.py --hv-test-mode --loglevel debug` |
| Debugging a specific issue with verbose logs | `python main.py --loglevel debug` |