# Launch Options

**Open-LIFU Test App**

The Open-LIFU Test App is launched from `main.py` (or the packaged
`OpenLIFU-TestApp.exe`). A handful of command-line flags control which UI
mode is presented, whether real hardware is required, and how verbose the
log output is.

```bash
python main.py [--mode {default,vet,all}] [--vet] [--all]
               [--simulate [N]] [--hv-test-mode]
               [--loglevel {debug,info,warning,error,critical}]
```

---

## UI mode

The application can present three different sidebar layouts. Pick one
(they are mutually exclusive):

| Flag | Sidebar tabs shown | Intended user |
|------|--------------------|---------------|
| *(none)* / `--mode default` | Controller, Transmitter, Console, Verification, Settings | Engineering / hardware bring-up |
| `--vet` / `--mode vet` | *(no sidebar — single Vet page kiosk UI)* | Veterinary operator running a fixed protocol |
| `--all` / `--mode all` | Vet + every default tab | Development / debugging |

`--mode` is only used to scope the UI. It does **not** change which
hardware is required, and on real hardware all modes share the same
underlying connection state.

> **Note:** When the sidebar is hidden (`--vet` mode), the Settings page
> is unreachable from the UI. Configuration changes (logging folder,
> session ID, etc.) are made directly from the Vet page's controls panel.

---

## Simulated hardware (`--simulate`)

```bash
python main.py --simulate          # 2 simulated TX modules
python main.py --simulate 1        # 1 simulated TX module
python main.py --vet --simulate    # Vet-mode kiosk against the simulator
```

`--simulate` swaps the real `LIFUInterface` for an in-memory fake
([lifu/simulated_interface.py](../lifu/simulated_interface.py)) so the app
runs end-to-end with no USB hardware attached. It is useful for UI
development, demos, and pre-deployment training.

Behaviour:

- A simulated Console and one or more simulated TX modules auto-connect
  about half a second after the app starts.
- Reported temperatures track a simple thermal model: TX modules heat up
  while sonicating (`dT/dt = k·V²·duty`) and cool toward 25 °C otherwise.
- `STATUS` telemetry frames are emitted at the configured pulse-train
  cadence so the Controller / Vet progress bars advance just as they do
  against real firmware.
- In `--vet` mode the simulator is forced to `num_modules=1` regardless
  of the value passed to `--simulate` (the Vet page is a single-module
  kiosk).
- Only the Vet and Controller tabs are exposed in simulated mode; tabs
  that depend on real device behaviour (Console, Transmitter,
  Verification, Settings) are hidden.

> **Warning:** Simulated runs do **not** energise any rails. Do not use
> simulated logs as proof of hardware behaviour; they reflect only the
> thermal model in `simulated_interface.py`.

---

## High-voltage test mode (`--hv-test-mode`)

```bash
python main.py --hv-test-mode
```

Tells the underlying SDK to drive the system as if an external lab
power supply is attached in place of the on-board high-voltage rails.
HV-on/off commands become no-ops on the firmware side, so the app's
voltage monitor readings reflect whatever the bench supply is sourcing.

This flag is intended for engineering bring-up only.

---

## Logging level (`--loglevel`)

```bash
python main.py --loglevel debug
```

Sets the level on the `lifu.lifu_connector` logger before the app starts
emitting messages. Accepted values: `debug`, `info` (default), `warning`,
`error`, `critical`.

Notes:

- Console output is always shown; this flag only changes the *severity*
  filter.
- Vet-mode runs additionally write a per-run log file under the
  configured log folder, formatted with elapsed-time stamps. Those
  files capture every logger (not just `lifu_connector`) at the level
  selected here, and they include any unhandled exceptions raised
  during the run.
- The `openlifu_sdk` logger is **not** wired up by default; uncomment
  the block at the top of [lifu/lifu_connector.py](../lifu/lifu_connector.py)
  if you want SDK-level frame traces.

---

## SDK version check

Every launch verifies the installed `openlifu-sdk` package against the
minimum pinned in `lifu/lifu_connector.py` (`MIN_SDK_VERSION`). If the
installed version is too old, the app pops a "Incompatible openlifu-sdk
version" dialog and exits with status 2. Upgrade with:

```bash
pip install --upgrade "openlifu-sdk>=<min_version>"
```

---

## Common invocations

| Situation | Command |
|-----------|---------|
| Normal engineering use, real hardware | `python main.py` |
| Veterinary operator, real hardware | `python main.py --vet` |
| UI demo or training, no hardware | `python main.py --simulate` |
| Vet-mode demo, no hardware | `python main.py --vet --simulate` |
| Bench bring-up with external HV supply | `python main.py --hv-test-mode --loglevel debug` |
| Debugging a specific issue with verbose logs | `python main.py --loglevel debug` |
