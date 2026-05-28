# Launch Options

**Open-LIFU Test App**

**Docs Navigation:** [README](../README.md) | [Controller](controller-page-user-guide.md) | [Transmitter](transmitter-page-user-guide.md) | [Console](console-page-user-guide.md) | [Verification](testing-page-user-guide.md) | [Settings](settings-page-user-guide.md) | [Support](support-page-user-guide.md)

<a id="in-this-page"></a>

## In This Page

- [Simulated hardware (`--simulate`)](#simulated-hardware)
- [High-voltage test mode (`--hv-test-mode`)](#high-voltage-test-mode)
- [Logging level (`--loglevel`)](#logging-level)
- [SDK version check](#sdk-version-check)
- [Common invocations](#common-invocations)

The Open-LIFU Test App is launched from `main.py` (or the packaged
`TestApp.exe`). A handful of command-line flags control whether real
hardware is required and how verbose the log output is.

```bash
python main.py [--simulate [N]] [--hv-test-mode]
               [--loglevel {debug,info,warning,error,critical}]
```

The app always presents the engineering sidebar (Controller,
Transmitter, Console, Verification, Settings). Operator-kiosk builds
live in the separate `openlifu-operator-interface` repository.

---

<a id="simulated-hardware"></a>

## Simulated hardware (`--simulate`)

**[Back to top](#in-this-page)**

```bash
python main.py --simulate                  # 2 simulated TX modules
python main.py --simulate 1                # 1 simulated TX module
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
  cadence so the Controller progress bars advance just as they do
  against real firmware.
- Only the Controller tab is exposed in simulated mode; tabs that
  depend on real device behaviour (Console, Transmitter, Verification,
  Settings) are hidden.

> **Warning:** Simulated runs do **not** energise any rails. Do not
> use simulated logs as proof of hardware behaviour; they reflect only
> the thermal model in `simulated_interface.py`.

---

<a id="high-voltage-test-mode"></a>

## High-voltage test mode (`--hv-test-mode`)

**[Back to top](#in-this-page)**

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

<a id="logging-level"></a>

## Logging level (`--loglevel`)

**[Back to top](#in-this-page)**

```bash
python main.py --loglevel debug
```

Sets the level on the `lifu.lifu_connector` logger before the app
starts emitting messages. Accepted values: `debug`, `info` (default),
`warning`, `error`, `critical`.

Notes:

- Console output is always shown; this flag only changes the
  *severity* filter.
- The `openlifu_sdk` logger is **not** wired up by default; uncomment
  the block at the top of
  [lifu/lifu_connector.py](../lifu/lifu_connector.py) if you want
  SDK-level frame traces.

---

<a id="sdk-version-check"></a>

## SDK version check

**[Back to top](#in-this-page)**

Every launch verifies the installed `openlifu-sdk` package against the
minimum pinned in `lifu/lifu_connector.py` (`MIN_SDK_VERSION`). If the
installed version is too old, the app pops a "Incompatible
openlifu-sdk version" dialog and exits with status 2. Upgrade with:

```bash
pip install --upgrade "openlifu-sdk>=<min_version>"
```

---

<a id="common-invocations"></a>

## Common invocations

**[Back to top](#in-this-page)**

| Situation | Command |
|-----------|---------|
| Normal engineering use, real hardware | `python main.py` |
| UI demo or training, no hardware | `python main.py --simulate` |
| Bench bring-up with external HV supply | `python main.py --hv-test-mode --loglevel debug` |
| Debugging a specific issue with verbose logs | `python main.py --loglevel debug` |

---

**Next:** [Controller Page - User Guide](controller-page-user-guide.md)
