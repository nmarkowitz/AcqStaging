# AcqStaging

AcqStaging is a Python 3.12/PyQt6 motion-control application for:

- One Zaber X linear stage
- One Zaber Y linear stage
- One Zaber Z linear stage
- One rotation stage driven by channel 1 of a Thorlabs BSC101 benchtop stepper
  motor controller
- One configurable Digital In component and one Digital Out component using
  simulation or NI-DAQmx

The tracked scan-head pose is displayed as:

```text
[x_mm, y_mm, z_mm, theta_deg]
```

The application provides manual motion, XYZ mosaic traversal, and an optional
DAQ Ready/Done handshake with external acquisition software. It does not control
a camera or acquire data itself; a standalone dummy acquirer is included for testing.

## Hardware safety

This software controls physical motion hardware.

- The application starts with simulated hardware and all components disconnected.
- It never connects, homes, or moves hardware automatically.
- Real hardware adapters are constructed only after loading and confirming a hardware profile.
- Connecting a component does not home or move it.
- Movement and motion tests require the component to be homed.
- Home and Motion Test buttons require confirmation.
- Home All always uses `X -> Y -> Z -> Rotation` and stops if one component fails.
- Configured software limits are checked before vendor movement commands.
- Normal automated tests use simulations or vendor fakes, never physical hardware.
- Connecting Digital Out never writes a default level; output changes require
  the explicit **Apply Pin States** action.
- An enabled acquisition handshake refuses to start unless both DAQ signals are
  connected, initialized, and idle LOW before the first movement.
- Hardware tests and scripts must not be run without explicit authorization and a reviewed configuration.

The real adapters have been tested with fake vendor objects, not laboratory
hardware. Review all ports, serial numbers, limits, velocities, accelerations,
timeouts, and test distances before any hardware use.

## Installation

Open PowerShell in the repository:

```powershell
cd C:\Users\Ayman\Documents\AcqStaging
```

Use the existing Python 3.12 environment or create one, then install the
application and development tools:

```powershell
python --version
python -m pip install -e ".[dev]"
```

`python --version` must report Python 3.12.

The editable installation creates these commands:

```text
acq-staging
acq-staging-diagnostic
acq-staging-dummy-acquirer
```

For real Thorlabs use, the Windows computer must also have Thorlabs Kinesis
installed. The default example expects its DLLs under:

```text
C:\Program Files\Thorlabs\Kinesis
```

## Starting the GUI

After installation:

```powershell
acq-staging
```

Alternatively:

```powershell
python -m acq_staging.app
```

The window starts with four simulated motion components and two simulated
digital components. Nothing is connected or homed at startup.

### Non-blocking interactive Python session

Use IPython or Jupyter's Qt event-loop integration when you want the GUI to stay
responsive while retaining an interactive prompt. From the activated project
environment, start IPython:

```powershell
python -m IPython
```

Then run:

```python
%gui qt
from acq_staging.app import launch_gui

gui = launch_gui()
window = gui.window
motion = window.service
config = window.config
```

`launch_gui()` returns immediately. Keeping `gui` assigned preserves strong
references to both `gui.app` and `gui.window`. The window starts with the same
safe, disconnected simulation profile as the console command. Useful objects
for exploration include `window.config`, `window.service`,
`window.digital_service`, `window.panels`, and `window.service.axes`.

Close the interactive window and run its normal cleanup path with:

```python
gui.close()
```

In a plain Python interpreter without `%gui qt`, the helper is still
non-blocking, but Qt processes updates only when requested:

```python
from acq_staging.app import launch_gui

gui = launch_gui()
gui.process_events()
```

Direct service calls from an interactive prompt do not show the GUI's motion
confirmation dialogs. Keep the default simulations for exploration. Do not load
a hardware profile or call connection, homing, or motion functions without the
same explicit laboratory authorization and profile review required elsewhere
in this guide.

The independent dummy acquisition GUI starts with:

```powershell
acq-staging-dummy-acquirer
```

It also starts disconnected with simulated DAQs and never changes an output on startup.
The two standalone simulation profiles do not share an electrical signal bus;
end-to-end inter-process operation requires correctly wired NI-DAQ lines.

## GUI controls

### Global controls

- **Edit Configuration** opens a structured sub-window containing every profile
  field. Hover over a field for its purpose and units.
- The configuration window's **Load File** replaces its fields from a validated
  JSON profile; **Save File** validates and writes the displayed profile.
- The configuration window's **Save** validates and applies the displayed
  profile to the GUI; **Cancel** closes it without applying changes.
- **Connect All** connects all enabled components without moving them.
- **Disconnect All** disconnects every component.
- **Home All** asks for confirmation and homes exactly:

```text
X -> Y -> Z -> Rotation
```

- **Start Mosaic** opens a per-run setup window. Choose tile counts from the
  current XYZ position or inclusive absolute beginning/end points, then review
  the resolved grid before confirming motion.
- X/Y/Z steps are prefilled from the profile and may be changed for that run
  without modifying the active or saved configuration.
- Endpoint mode supports increasing or decreasing travel independently on each
  axis and requires each endpoint span to be an exact multiple of its step.
- Starting a mosaic opens a non-modal tiling window with one grid per Z plane.
  Its legend shows gray Pending tiles, orange Current tiles, and green Complete
  tiles while the scan progresses.
- If an XYZ component is disconnected, Start Mosaic lists it and requires
  confirmation before running; acknowledged disconnected axes are skipped.
- **Stop Mosaic** cancels mosaic waiting and requests a stop from X, Y, and Z.
- With acquisition handshaking enabled, each settled tile is held orange while
  movement waits for acquisition Done before marking the tile complete.

The pose display shows the most recently observed `[x, y, z, theta]`.
An em dash means that component's position is not currently known.

### Individual component panels

Each X, Y, Z, and Rotation panel contains:

- **Connect**: connect only that component; no motion occurs.
- **Disconnect**: disconnect only that component.
- **Home**: explicitly home that component after confirmation.
- **Stop**: request an immediate stop.
- **Move Absolute**: move to the position entered in the adjacent field.
- **- Jog / + Jog**: move by the configured increment.
- **Diagnose**: read connection, identity, status, and position without moving.
- **Identify**: ask the already-connected controller to blink or otherwise
  identify itself visually without moving the stage.
- **Motion Test**: after confirmation, move by the configured small test
  distance and return to the observed starting position.

Linear positions and increments use millimetres. Rotation uses degrees.

Moves at or above the configured large-move threshold require an additional
confirmation.

### Digital In and Digital Out panels

Both panels provide independent **Connect** and **Disconnect** controls.
Digital In displays every configured pin as `HIGH`, `LOW`, or `UNKNOWN` and can
be refreshed with **Read Pins**; connected inputs are also polled in a worker.
Digital Out provides one checkbox per configured pin. Select one or many pins
and click **Apply Pin States** to write the complete configured output set in
one operation. Each panel always shows eight slots; pins outside the configured
port width or subset are disabled and labeled `NOT CONFIGURED`.

Hardware output states remain `UNKNOWN` until successfully set. After the first
explicit Apply, the worker continuously reasserts the last applied output state
at the configured polling interval. Merely connecting Digital Out never writes.

### Acquisition handshake

The movement application uses a latched idle-LOW protocol so software polling
cannot miss a short edge:

1. Movement reaches and settles at a tile, then holds Ready HIGH.
2. Acquisition detects Ready HIGH, acquires data, and holds Done HIGH.
3. Movement detects Done HIGH and returns Ready LOW.
4. Acquisition detects Ready LOW and returns Done LOW.
5. Movement verifies both lines are reset before advancing to the next tile.

Before starting, connect both DAQ directions and use **Apply Pin States** to
initialize every configured Digital Out pin, including Ready, LOW. Stale HIGH or
UNKNOWN states fail preflight before the first movement. Manual output writes and
continuous output reassertion are blocked while the handshake owns Digital Out.

When `acquisition_handshake.enabled` is false, mosaics retain the existing
motion-only `dwell_time_s` behavior. An enabled handshake replaces that dwell and
cannot be bypassed through the GUI.

### Dummy data acquirer

The standalone dummy GUI reuses the Digital In and Digital Out panels. Connect
both directions, initialize every configured output LOW, and click **Start
Listening**. Each Ready request starts a configurable timed acquisition;
**Complete Now** ends the current wait early. Done remains HIGH until movement
returns Ready LOW. **Stop Listening** interrupts waits and returns Done LOW.

## Safe simulation walkthrough

This is the recommended first exercise:

1. Start the GUI with `acq-staging`.
2. Click **Connect All**.
3. Confirm that all four panels say `Connected`.
4. Click **Home All** and approve the confirmation.
5. Watch the status message progress through X, Y, Z, and Rotation.
6. Confirm the pose becomes `[0.0000, 0.0000, 0.0000, 0.0000]`.
7. Enter a safe X destination such as `10` and click **Move Absolute**.
8. Try the X **- Jog** and **+ Jog** buttons.
9. Click **Diagnose** for each component.
10. Click **Motion Test** and approve it. The component should return to its
    starting position.
11. Click **Disconnect All**.

Simulation uses the same services and GUI paths as real hardware, while the
adapter itself remains in-process and deterministic.

## Configuration files

Included profiles:

- `configs/simulation.json`: safe all-simulation example
- `configs/hardware.example.json`: hardware template containing placeholders
- `configs/dummy_acquirer.simulation.json`: safe standalone dummy profile
- `configs/dummy_acquirer.hardware.example.json`: placeholder dummy NI-DAQ profile

Do not use `hardware.example.json` unchanged.

The major configuration sections are:

### Top-level settings

- `schema_version`: currently `4`
- `profile_name`: descriptive profile name
- `connections`: reusable Zaber serial connections
- `components`: X, Y, Z, and rotation definitions
- `ui`: GUI polling and confirmation thresholds
- `mosaic`: XYZ tile geometry and motion/acquisition timing
- `digital_io`: Digital In and Digital Out device, port, pin, and timeout settings
- `acquisition_handshake`: optional movement-side Ready/Done line and timeout settings

Schema-version-3 application profiles load with acquisition handshaking disabled
and save back as schema version 4. Older profiles still require the documented
`mosaic` and `digital_io` sections.

### Digital I/O settings

Both `digital_in` and `digital_out` entries support:

- `enabled`: whether Connect All includes the component
- `adapter`: `simulated` or `nidaqmx`
- `device`: NI-DAQmx device name such as `Dev1`
- `port`: user-friendly `P0` or NI-style `port0`
- `line_count`: physical port width from 1 through 8
- `lines`: `null` for the whole port, or a subset such as `["line0", "line2"]`
- `operation_timeout_s`: timeout passed to NI-DAQmx reads and writes
- `polling_interval_ms`: requested Digital In display polling interval

Line entries may be integers or NI-style strings such as `"line2"`. Whole
ports use `CHAN_FOR_ALL_LINES` with an integer bitmask. Pin subsets use
`CHAN_PER_LINE` with Boolean values in configured pin order.

### Mosaic settings

The profile stores defaults for the **Start Mosaic** setup window. Per-run edits
do not change the active profile or a saved JSON file:

- `step_x_mm`, `step_y_mm`, and `step_z_mm`: default distance between tile positions
- `tiles_x`, `tiles_y`, and `tiles_z`: default count-mode positions per dimension
- `settling_time_s`: wait after reaching a tile before reporting it ready
- `dwell_time_s`: optional motion-only placeholder wait at each tile
- `operation_timeout_s`: maximum duration allowed for the entire mosaic

In tile-count mode, the observed XYZ pose becomes the first tile and each span
is `(tiles - 1) * step`. In endpoint mode, beginning and end coordinates are
absolute millimetre positions and both are included. The application derives
each axis direction from the entered points; a nonzero span must divide into a
whole number of unchanged steps, while equal points produce one position.

Traversal snakes across X, advances Y after each row, and repeats the XY grid
for each Z plane. Rotation remains fixed. Every XYZ axis must be connected and
homed, and the entire absolute path must fit configured software limits before
the first movement.
Connected participating axes must be homed. Disconnected axes may be skipped
only after the GUI lists them and the operator explicitly confirms the partial
mosaic; homing is never bypassed for a connected axis.

The legacy tilt-mosaic, vibratome, dual-Z, cut, notification, and acquisition
parameters are intentionally excluded. Only acquisition coordination signals are in scope.

### Acquisition handshake settings

- `enabled`: require DAQ acquisition acknowledgement for every tile
- `ready_output_line`: movement Digital Out line connected to acquisition Ready input
- `complete_input_line`: movement Digital In line connected to acquisition Done output
- `acquisition_timeout_s`: maximum wait for Done HIGH
- `reset_timeout_s`: maximum wait for Done to return LOW after Ready is lowered
- `polling_interval_ms`: interval used while checking the latched input state

The separate dummy profile contains matching `ready_input_line` and
`complete_output_line` assignments plus `acquisition_duration_s`. The physical
wiring must cross movement Ready output to dummy Ready input and dummy Done
output to movement Done input. Verify device, port, and line assignments before connection.

### Zaber connection settings

- `port`: Windows serial port such as `COM3`
- `baud_rate`: normally `115200`

X, Y, and Z may share a connection or use different connection IDs.

### Per-component settings

- `enabled`
- `adapter`: `simulated`, `zaber_ascii`, or `thorlabs_bsc101`
- `limits.minimum` and `limits.maximum`
- `motion.velocity`
- `motion.acceleration`
- `motion.jog_increment`
- `motion.test_distance`
- `motion.position_tolerance`
- `motion.operation_timeout_s`
- `polling_interval_ms`

Zaber components also use:

- `connection_id`
- `serial_number`
- `device_address`, if address verification is desired
- `axis_number`
- `expected_model`, if model verification is desired

The Thorlabs rotation component uses:

- `serial_number`: the BSC101 controller serial number
- `axis_number`: BSC101 channel number; must be `1`
- `expected_model`: normally `BSC101`
- `device_settings_name`: exact Kinesis actuator profile already selected for
  channel 1, such as `HDR50`; connection fails visibly if Kinesis reports a
  different profile
- `dll_directory`: the local Kinesis installation directory; it is never
  hardcoded by the adapter

When a real Zaber component connects, the adapter discovers the daisy chain,
matches the configured serial number, and reads `limit.min` and `limit.max` in
millimetres. The application's configured limits must fit inside that
device-reported range. This check does not replace installation-specific
software limits with the full mechanical travel.

When the BSC101 rotation component connects, the adapter follows the verified
channel-1 sequence from `scripts/thorlabs_rotation_testing.py`: connect by
serial, wait for settings initialization, start polling, enable the channel,
load the existing motor configuration, read the channel settings, verify the
configured actuator profile, and apply the profile's velocity and acceleration.
It does not change `DeviceSettingsName`, persist settings, home, or move during
connection.

Simulation components may use:

- `simulation_start`
- `simulation_latency_s`

Profiles can mix real and simulated adapters. For example, X can be real while
Y, Z, and Rotation remain simulated for isolated component work.

Saving a configuration changes the JSON application profile only. It does not
write permanent settings to vendor device memory.

## Application components

The production package is under `src/acq_staging`.

| Component | Purpose |
|---|---|
| `app.py` | Creates the Qt application, configures logging, and opens the main window. |
| `models.py` | Axis names, adapter types, identity, status, diagnostics, and pose models. |
| `configuration.py` | JSON parsing, validation, defaults, loading, and atomic saving. |
| `services.py` | Connection, homing, motion, pose tracking, diagnostics, motion tests, and mosaic orchestration. |
| `acquisition.py` | Movement Ready/Done coordination and the dummy acquisition state machine. |
| `dummy_app.py` | Starts the independent dummy acquisition application. |
| `factory.py` | Creates simulated or explicitly authorized real adapters from a profile. |
| `logging_setup.py` | Creates rotating logs under `logs/acq_staging.log`. |
| `adapters/base.py` | Vendor-neutral `MotionAxis` interface. |
| `adapters/simulated.py` | Safe simulated stage used by normal operation and tests. |
| `adapters/zaber.py` | Zaber shared connections and linear-stage adapter. |
| `adapters/thorlabs.py` | Lazy Kinesis loader and BSC101 channel-1 rotation adapter. |
| `adapters/digital.py` | Simulated and lazy NI-DAQmx digital I/O adapters. |
| `gui/main_window.py` | Main window, configuration actions, global controls, polling, and background operations. |
| `gui/dummy_acquirer_window.py` | Standalone dummy acquisition controls, status, counters, and worker lifecycle. |
| `gui/axis_panel.py` | Reusable individual component panel. |
| `gui/digital_panel.py` | Reusable Digital In/Out pin-state and control panel. |
| `gui/configuration_dialog.py` | Structured, tooltipped profile editor with load, apply, and save actions. |
| `gui/workers.py` | Qt thread-pool workers that keep operations out of the GUI thread. |
| `diagnostics/cli.py` | Standalone safe diagnostic, explicit home, and motion-test command. |

`PLAN.md` records implementation status, verified vendor APIs, test evidence,
and the next-session handoff.

## Running the normal automated checks

Run all commands from the repository root.

### 1. Automated tests

On Windows, set Qt to headless mode for GUI tests:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -p no:cacheprovider
```

The expected current result is:

```text
56 passed, 1 deselected
```

The deselected item is the hardware safety placeholder. The default pytest
configuration excludes everything marked `hardware`.

Remove the headless setting before trying to display the GUI in the same
PowerShell window:

```powershell
Remove-Item Env:QT_QPA_PLATFORM
```

### 2. Ruff source checks

```powershell
python -m ruff check .
```

This checks imports, common errors, style, and selected safety/clarity rules.

### 3. mypy type checks

```powershell
python -m mypy src tests scripts
```

This verifies the typed interfaces used between configuration, services, GUI,
simulations, and vendor adapters.

### Complete normal verification

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -p no:cacheprovider
python -m ruff check .
python -m mypy src tests scripts
Remove-Item Env:QT_QPA_PLATFORM
```

## What each automated test file covers

| Test file | Coverage |
|---|---|
| `test_models.py` | Required home order and `[x, y, z, theta]` pose ordering. |
| `test_configuration.py` | Profile round trips, required components, software-limit validation, and JSON serialization. |
| `test_simulated.py` | Connection and homing requirements, movement, limits, injected failures, and stop interruption. |
| `test_services.py` | Home All, pose, motion tests, mosaic snake ordering, and path preflight. |
| `test_gui.py` | Safe simulated startup, four panels, Identify, and explicit mosaic controls. |
| `test_configuration_dialog.py` | Structured fields, tooltips, validation, and profile file actions. |
| `test_mosaic_progress_dialog.py` | Multi-plane tile layout, legend, and progress colors. |
| `test_mosaic_logging.py` | Timestamped mosaic filename and chronological event format. |
| `test_diagnostics.py` | Non-motion diagnostics and the acknowledgement required for home or motion. |
| `test_zaber_adapter.py` | Serial-number matching, model/device-limit validation, Zaber units, nonblocking command arguments, and connection cleanup using fakes. |
| `test_thorlabs_adapter.py` | BSC101 controller/channel lifecycle, exact actuator-settings validation, cleanup, and callback movement using fakes. |
| `test_digital_adapter.py` | Whole-port bitmask reads and selected multi-pin writes with NI-DAQ fakes. |
| `test_acquisition.py` | Idle preflight, latched Ready/Done exchange, timeout cleanup, and dummy responses. |
| `test_hardware_markers.py` | Guard proving hardware tests are excluded from normal pytest runs. |

The Zaber and Thorlabs adapter tests do not access physical devices.

## Standalone component diagnostics

These commands are useful for testing one component in isolation.

### Generic diagnostic command

Simulation:

```powershell
acq-staging-diagnostic --component x
acq-staging-diagnostic --component y
acq-staging-diagnostic --component z
acq-staging-diagnostic --component rotation
```

The default diagnostic connects only the selected simulated component, reads
identity/status/position, prints JSON, and disconnects. It does not home or move.

### Per-component scripts

Equivalent script launchers are:

```powershell
python scripts/test_x_stage.py
python scripts/test_y_stage.py
python scripts/test_z_stage.py
python scripts/test_rotation_stage.py
```

`scripts/diagnose_component.py` is the generic script form:

```powershell
python scripts/diagnose_component.py --component x
```

### Simulation home and motion test

Homing or motion requires the exact acknowledgement:

```text
I-UNDERSTAND-HARDWARE-WILL-MOVE
```

Example using simulation:

```powershell
acq-staging-diagnostic `
  --component x `
  --home `
  --motion-test `
  --acknowledge I-UNDERSTAND-HARDWARE-WILL-MOVE
```

Although this example is simulated, the same acknowledgement is required so
the command cannot accidentally be reused against hardware without an obvious
motion warning.

## Real hardware diagnostics

Do not run these commands until the profile has been reviewed and physical
movement has been explicitly authorized.

### Read-only hardware diagnostic

This connects, reads identity/status/position, and disconnects. It does not
home or intentionally move:

```powershell
acq-staging-diagnostic `
  --hardware `
  --config configs\lab.json `
  --component x
```

Replace `x` with `y`, `z`, or `rotation` to isolate another component.

### Explicit hardware home

This physically homes the selected component:

```powershell
acq-staging-diagnostic `
  --hardware `
  --config configs\lab.json `
  --component x `
  --home `
  --acknowledge I-UNDERSTAND-HARDWARE-WILL-MOVE
```

### Explicit hardware motion test

The component must already report as homed. The command moves by the configured
`test_distance` and then attempts to return to the starting position:

```powershell
acq-staging-diagnostic `
  --hardware `
  --config configs\lab.json `
  --component x `
  --motion-test `
  --acknowledge I-UNDERSTAND-HARDWARE-WILL-MOVE
```

To explicitly home and then test in one invocation, provide both `--home` and
`--motion-test`. This causes physical movement.

## Hardware pytest markers

The configured markers are:

- `hardware`
- `requires_home`
- `requires_motion`

Normal pytest runs exclude `hardware`.

There is not yet a real laboratory hardware pytest suite. The current
`test_hardware_markers.py` item intentionally fails if someone overrides the
default exclusion. It is a guard, not a test to run.

Do not run:

```powershell
python -m pytest -m hardware
```

until real, reviewed, opt-in hardware tests replace that placeholder and the
user explicitly authorizes their execution.

## Logs and troubleshooting

The GUI writes rotating logs to:

```text
logs/acq_staging.log
```

The standalone dummy acquirer writes rotating logs to `logs/dummy_acquirer.log`.

Each mosaic also creates a dedicated audit log under:

```text
logs/mosaics/YYYY_MM_DD_HH-mm-ss.log
```

Every line contains a local ISO timestamp, event name, and compact JSON details.
Events include mosaic start/failure/completion, tile indices and absolute XYZ
targets, movement start and observed completion positions, reused positions,
acknowledged disconnected-axis skips, settling and dwell boundaries, acquisition
Ready/Done transitions, reset completion, timeout failures, and tile completion.
Successful Digital Out writes include their pin states and source.

Common errors:

- **Not connected**: click Connect for that component first.
- **Must be homed before movement**: use the explicit Home control.
- **Destination outside software limits**: correct the requested position or
  the validated profile; do not weaken limits merely to bypass the error.
- **Serial or address mismatch**: verify the physical device and JSON identity.
- **Missing Kinesis DLL**: verify Kinesis installation and `dll_directory`.
- **Settings initialization timed out**: stop and inspect the Thorlabs/Kinesis
  configuration rather than retrying motion automatically.
- **Operation timed out**: the adapter issues a stop request and reports the
  failure. Inspect the device before continuing.

## Current limitations

- No vibratome support
- One Z stage only
- No camera acquisition
- No real data acquisition; the included acquirer is a timing/handshake simulator
- No laboratory-validated hardware pytest suite yet
- Real vendor adapters are mock-tested but still require controlled hardware validation

Vendor-specific code must remain inside adapter classes. New vendor methods
must be verified against the installed package, assembly metadata, and supplied
reference examples before use.
