# AcqStaging Implementation Plan and Session Memory

## Project contract

- Control X, Y, and one Z stage through Zaber Motion Library adapters.
- Control a rotation stage through channel 1 of a Thorlabs BSC101 benchtop
  stepper motor controller using a Kinesis adapter.
- Control configurable Digital In and Digital Out ports through simulation or
  NI-DAQmx, using either a whole port or selected pins.
- Track pose as `[x_mm, y_mm, z_mm, theta_deg]`.
- Provide PyQt6 manual control, individual diagnostics and visual device
  identification, explicit homing, Home All in X → Y → Z → Rotation order,
  explicitly confirmed XYZ mosaic traversal, and optional DAQ acquisition handshaking.
- Provide a standalone dummy acquirer that receives movement Ready and returns
  acquisition Done through configurable Digital In and Digital Out lines.
- Default to simulation. Never connect, home, move, or run hardware tests automatically.
- Keep vendor calls inside adapters and all potentially blocking work outside the GUI thread.
- Do not implement vibratome or dual-Z behavior.

## Locked decisions

- Camera and real data acquisition remain deferred; DAQ-coordinated mosaic traversal is in scope.
- Acquisition signaling uses an idle-HIGH active-LOW movement request and an OCT
  response that rises on acceptance and falls on first-image completion.
- Widgets are authored directly in Python.
- Zaber configuration supports shared or separate serial ports.
- Settings are stored in versioned JSON application profiles, never vendor device memory.
- Diagnostics are non-motion by default; motion tests require confirmation and return to their starting point.
- Normal tests use simulation or mocks.

## Verified API ledger

### Zaber Motion Library 9.3.2

- `Connection.open_serial_port(port_name, baud_rate=115200, ...)`
- `connection.detect_devices()`
- `device.get_axis(axis_number)`
- `axis.home(wait_until_idle=False)`
- `axis.move_absolute(..., Units.LENGTH_MILLIMETRES, wait_until_idle=False, velocity=..., acceleration=...)`
- `axis.move_relative(...)`
- `axis.get_position(unit)`, `axis.is_homed()`, `axis.is_busy()`, `axis.stop()`

Primary reference: `references/zaber-examples`.

### Thorlabs Kinesis 1.14.59 / pythonnet / BSC101

- `BenchtopStepperMotor.CreateBenchtopStepperMotor(serial_number)`
- controller `Connect`, `GetChannel(1)`, and `Disconnect`
- channel `WaitForSettingsInitialized`, `StartPolling`, and `EnableDevice`
- channel `LoadMotorConfiguration(serial_number)`, `MotorDeviceSettings`,
  `GetSettings`, and motor-configuration `UpdateCurrentConfiguration`
- callback overloads for channel `Home`, `MoveTo`, and `MoveRelative`
- channel `StopImmediate`, `SetVelocityParams`, `Status`, and `Position`
- channel `StopPolling` during cleanup

Primary reference:
`references/Thorlabs-examples/Python/Kinesis/Benchtop/BSC101/bsc101_pythonnet.py`.
The Kinesis DLL directory is profile-driven and must not be hardcoded.

### NI-DAQmx Python 1.5.0

- `Task.di_channels.add_di_chan` and `Task.do_channels.add_do_chan`
- `LineGrouping.CHAN_FOR_ALL_LINES` for whole-port integer bitmasks
- `LineGrouping.CHAN_PER_LINE` for selected Boolean line channels
- `Task.read(timeout=...)`, `Task.write(..., auto_start=True, timeout=...)`, and `Task.close()`

Primary references: `references/ni-examples/digital_in` and
`references/ni-examples/digital_out`.

## Phase status

| Phase | Description | Status |
|---|---|---|
| 0 | Foundation and durable memory | Complete |
| 1 | Models and JSON configuration | Complete |
| 2 | Simulation adapters | Complete |
| 3 | Motion orchestration and pose tracking | Complete |
| 4 | PyQt6 GUI | Complete |
| 5 | Zaber adapter | Complete (mock-verified) |
| 6 | Thorlabs adapter | Complete (mock-verified) |
| 7 | Diagnostics and opt-in hardware tests | Complete |
| 8 | Documentation and final integration | Complete |
| 9 | Motion-only XYZ mosaic tiling | Complete (simulation-verified) |
| 10 | Digital input and output components | Complete (simulation/fake-verified) |
| 11 | DAQ acquisition handshake and dummy acquirer | Complete (simulation/fake-verified) |
| 12 | Per-run count or endpoint mosaic geometry | Complete (simulation-verified) |
| 13 | Non-blocking interactive GUI launch | Complete (simulation-verified) |
| 14 | Attached mosaic viewer, restart, cancel, and API limits | Complete (simulation/fake-verified) |
| 15 | Sequential fixed-Z dual-rotation mosaic runs | Complete (simulation/fake-verified) |
| 16 | Fixed current-tiling viewer and timing estimate | Complete (simulation-verified) |
| 17 | Circular numbered digital I/O indicators | Complete (simulation-verified) |
| 18 | In-place endpoint normalization after editing | Complete (simulation-verified) |
| 19 | Recent-pace duration prediction | Complete (simulation-verified) |
| 20 | Optional second mosaic tiling | In progress |
| 21 | Delegate physical travel limits to vendor controllers | In progress |
| 22 | Active-LOW OCT acquisition trigger handshake | In progress |

Every phase must pass:

```powershell
python -m pytest
python -m ruff check .
python -m mypy src tests scripts
```

Hardware-marked tests are never run without an explicit user request.

## Phase checklist

### Phase 0 — Foundation

- [x] Add Python package, project metadata, test configuration, and safe entry point.
- [x] Add an all-simulation example profile.
- [x] Pass pytest, Ruff, and mypy.

### Phase 1 — Models and configuration

- [x] Add typed identity, status, pose, limits, and settings models.
- [x] Add versioned JSON loading, validation, atomic save, and round-trip tests.
- [x] Pass pytest, Ruff, and mypy.

### Phase 2 — Simulation

- [x] Add reusable simulated motion-axis implementation.
- [x] Cover connection, homing, movement, stopping, limits, and faults.
- [x] Pass pytest, Ruff, and mypy.

### Phase 3 — Orchestration

- [x] Add registry, motion service, pose tracker, diagnostics, and Home All coordinator.
- [x] Enforce limits, connection, homing, timeouts, and exact home order.
- [x] Pass pytest, Ruff, and mypy.

### Phase 4 — GUI

- [x] Add main window, four component panels, per-component Identify controls,
  pose/status display, configuration actions, and logs.
- [x] Keep hardware calls outside the GUI thread and stop available during operations.
- [x] Add pytest-qt coverage and pass all quality checks.

### Phase 5 — Zaber adapter

- [x] Reinspect installed API and local examples immediately before editing.
- [x] Add shared connection manager, serial matching, units, settings, polling, and cleanup.
- [x] Test only with fakes/mocks and pass all quality checks.

### Phase 6 — Thorlabs adapter

- [x] Reinspect installed assemblies and local examples immediately before editing.
- [x] Add lazy BSC101 Kinesis loading, controller/channel lifecycle, Decimal
  conversion, callback motion, status, and cleanup.
- [x] Test only with a fake facade and pass all quality checks.

### Phase 7 — Diagnostics

- [x] Add safe diagnostic CLI and explicit opt-in motion/home flags.
- [x] Add excluded-by-default hardware pytest markers.
- [x] Pass all normal quality checks without hardware access.

### Phase 8 — Final integration

- [x] Document setup, configuration, simulation, GUI use, diagnostics, and hardware safety.
- [x] Verify no vibratome, dual-Z, automatic motion, blocking GUI calls, or vendor leakage.
- [x] Pass all normal quality checks.

### Phase 12 — Per-run mosaic geometry

- [x] Add a Start Mosaic setup dialog with tile-count and absolute-endpoint modes.
- [x] Keep step overrides transient, infer endpoint direction, and require exact spacing.
- [x] Preserve full-path preflight, serpentine traversal, DAQ coordination, and Stop.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 13 — Non-blocking interactive GUI launch

- [x] Add a reusable GUI session that exposes the application and main window.
- [x] Preserve blocking behavior for the `acq-staging` console command.
- [x] Document IPython/Jupyter event-loop integration and interactive safety.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 14 — Mosaic recovery and device limits

- [x] Attach the all-axis position and mosaic-progress viewer to the main GUI.
- [x] Add embedded cancellation and restart from a one-based tile number.
- [x] Round endpoint spans outward while preserving configured step sizes.
- [x] Remove physical profile limits and delegate physical travel enforcement
  to vendor APIs and controllers.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 15 — Sequential dual-rotation mosaics

- [x] Define two independent endpoint-based XY tilings with fixed per-tiling Z.
- [x] Fix Tiling 1 at 0 degrees and run user-configured Tiling 2 when enabled.
- [x] Move through a configured safe Z before rotation changes.
- [x] Resume by tiling and one-based tile, with progress shown in the attached viewer.
- [x] Require all four axes connected/homed and preflight the complete path.
- [x] Remove the temporary origin/reset workflow and use controller coordinates.
- [x] Preserve one audit stream, optional per-tile DAQ handshaking, and cancellation.

### Phase 16 — Current-tiling viewer and timing

- [x] Reuse one scalable overview for the tiling currently being acquired.
- [x] Keep the progress dock permanently attached to the main window.
- [x] Show elapsed and estimated remaining time after two completed tiles.
- [x] Refresh timing after each later tile and reset it between tilings.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 17 — Circular digital I/O indicators

- [x] Display Digital In and Digital Out as circular pins numbered 0 through 7.
- [x] Use green for HIGH, gray for LOW, amber for UNKNOWN, and dashed disabled pins.
- [x] Preserve atomic output selection and worker-thread DAQ operations.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 18 — In-place endpoint normalization

- [x] Keep live endpoint previews while the operator is typing.
- [x] Replace incompatible XY stop fields with their outward-rounded values when editing ends.
- [x] Leave out-of-limit rounded values uncommitted and display the validation error.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 19 — Recent-pace duration prediction

- [x] Estimate remaining time from a recent, linearly weighted eight-tile window.
- [x] Run the complete simulated suite, Ruff, and mypy.

### Phase 20 — Optional second mosaic tiling

- [x] Add a default-checked checkbox that enables or disables Tiling 2.
- [x] Exclude disabled Tiling 2 geometry from validation, totals, execution,
  progress, restart selection, and audit events.
- [ ] Run the complete simulated suite, Ruff, and mypy.

### Phase 21 — Vendor-owned physical travel limits

- [x] Remove application travel checks and GUI clamping from physical axes.
- [x] Keep configured limits for simulation and preserve vendor-side Kinesis
  and Zaber enforcement.
- [ ] Run the complete simulated and fake suite, Ruff, and mypy.

### Phase 22 — Active-LOW OCT acquisition trigger handshake

- [x] Require movement output HIGH and OCT response LOW at idle.
- [x] Hold movement output LOW through response LOW-to-HIGH acceptance and
  HIGH-to-LOW image completion before resetting HIGH.
- [x] Update the dummy peer to raise its response on acceptance and lower it on
  image completion, then wait for movement reset.
- [x] Initialize the configured movement request HIGH when Start Mosaic begins.
- [ ] Run the complete simulated suite, Ruff, and mypy.

### Phase 23 — Profile-defined physical mosaic orientation

- [x] Store validated physical-right and physical-down controller axis/sign
  mappings in schema-version-7 application profiles.
- [x] Start every tiling at logical top-left and preserve row-wise serpentine
  order for inverted or swapped controller X/Y mounting.
- [x] Keep controller-axis step fields positive while applying profile signs to
  planned destinations, including negative travel.
- [x] Show logical rows and columns consistently in setup previews, progress,
  resume numbering, and audit details.
- [ ] Run the complete simulated suite, Ruff, and mypy.

## Current session

- Added schema-version-7 mosaic orientation mappings so profiles define which
  signed controller axes correspond to physical right and down. Tiling plans,
  setup previews, progress grids, resume numbering, and audit records now use
  the same logical top-left serpentine order.
- Both maintained hardware profiles map physical right to X+ and physical down
  to Y- for the reported bottom-left controller home. The simulation profile
  retains X+/Y+ behavior, and older profiles migrate to that prior default.
- Orientation-focused verification passes with 60 tests; the broader safe run
  passes with 101 tests selected and four unrelated tests excluded/deselected.
  The complete command cannot safely finish because the current standalone
  NI-DAQ script creates tasks at import time, while three configuration tests
  still reference the pre-existing missing `configs/hardware.example.json`.
  Scoped Ruff and mypy pass; repository-wide Ruff/mypy remain blocked by that
  NI-DAQ script and the pre-existing syntax error in `scripts/nidaq_testing.py`.
- No stage was connected, homed, moved, or stopped. The initial normal-suite
  attempt imported the pre-existing NI-DAQ script, attempted DAQ task creation,
  and hung; the process was terminated and no new Python process remains.
- Replaced the earlier idle-LOW Ready/Done exchange with the OCT protocol:
  movement output HIGH idle, LOW request, OCT input HIGH acquisition start, OCT
  input LOW first-image completion, then movement output HIGH reset.
- Start Mosaic now initializes its configured request output HIGH automatically;
  additional configured output pins must already have explicit known states.
- Automatic request initialization passes 12 focused handshake/adapter tests;
  scoped Ruff and mypy pass. The safe complete suite reports 88 passed,
  3 failed, and 1 hardware guard deselected because the absent
  `configs/hardware.example.json` still breaks three unrelated tests. No
  hardware was accessed.
- Focused handshake verification passes with 14 tests; Ruff and mypy pass. The
  complete suite reports 87 passed, 3 failed, and 1 hardware guard deselected
  because the pre-existing deletion of `configs/hardware.example.json` still
  breaks three configuration tests. No hardware was accessed.
- Added a standalone two-input/two-output NI-DAQ movement-side handshake script
  with explicit hardware confirmation and fake-task coverage. It performs one
  synchronized LOW request, HIGH acceptance, LOW completion, and HIGH reset.
- The two new fake-task tests pass; Ruff and mypy pass. The complete suite
  reports 89 passed, 3 failed, and 1 hardware guard deselected because the
  absent `configs/hardware.example.json` still breaks three unrelated tests.
  No hardware was accessed.
- Removed application travel bounds from physical-axis GUI controls, service
  preflight, and real adapters. Physical destinations now reach Zaber or
  Kinesis unchanged so their existing controller limits remain authoritative;
  simulations continue to enforce configured limits.
- Focused physical-limit delegation verification passes with 40 tests; Ruff and
  mypy pass. The complete suite reports 85 passed, 3 failed, and 1 hardware
  guard deselected because the pre-existing deletion of
  `configs/hardware.example.json` still breaks three configuration tests. No
  hardware was accessed.
- Added a default-checked **Run Tiling 2 after Tiling 1** checkbox. Clearing it
  disables Tiling 2 editing and restart selection and omits that tiling from
  validation, totals, execution, progress, and audit events.
- Focused mosaic verification passes with 43 tests; Ruff and mypy pass. The
  complete suite reports 81 passed, 3 failed, and 1 hardware guard deselected
  because the pre-existing deletion of `configs/hardware.example.json` breaks
  three configuration tests. No hardware was accessed.
- Remaining-time prediction now favors up to eight recent tile durations so it
  adapts to changing movement or acquisition pace.
- Removed the System Status and Health dashboard and all of its GUI hooks.
- Normal verification: 80 passed, 1 hardware safety guard deselected; Ruff and
  mypy pass. Tests used simulations and GUI fakes; no hardware was accessed.
- Mosaic endpoint fields now commit their outward-rounded stop values when the
  edited start, stop, or step box loses focus or accepts Enter.
- Replaced text-heavy Digital In and Digital Out checkboxes with matching round,
  numbered 0-through-7 pin controls. HIGH is green while LOW is gray.
- Normal verification: 80 passed, 1 hardware safety guard deselected; Ruff and
  mypy pass. Tests used simulation and GUI fakes only; no hardware was accessed.
- Replaced the single XYZ mosaic with one full run containing two sequential,
  independently configured XY tilings at fixed Z positions. Tiling 1 is fixed
  at 0 degrees and Tiling 2 uses the operator-defined rotation.
- Replaced the two simultaneous progress overviews with one fixed, non-detachable
  current-tiling view. It switches automatically when Tiling 2 begins.
- Added per-tiling elapsed and estimated-remaining labels after two completed
  tiles, updated after each later completion and reset between tilings.
- Added a safe-Z rotation transition, tiling/tile resume, full four-axis
  readiness and path preflight.
- Removed application-origin controls and reference offsets; positions and audit
  entries now use the controller-home coordinate system.
- Normal verification: 79 passed, 1 hardware safety guard deselected; Ruff and
  mypy pass. All tests used simulation or vendor fakes; no hardware was accessed.
- Replaced the per-tile scroll area with a scalable painted mosaic overview that
  keeps every XY tile and Z plane visible while shading current and completed work.
- Normal verification: 78 passed, 1 hardware test deselected; Ruff and mypy pass.
  The scalable viewer was exercised with a 64,000-tile simulated run.
- Added an attached all-axis/mosaic viewer, Cancel Mosaic, user-defined origins,
  origin-relative motion and logs, sequential reset, and restart-tile selection.
- Endpoint runs now extend incompatible endpoints outward to the next full step.
- Schema 5 introduced synthetic simulation limits and API-derived physical
  limits; Phase 21 later removed application use of the physical values.
- Normal verification: 76 passed, 1 hardware test deselected; Ruff and mypy
  pass. Vendor-limit tests use fakes only. No physical hardware was accessed.
- Added `launch_gui()` and `GuiSession` for non-blocking interactive exploration
  while preserving the existing blocking console entry point.
- Documented `%gui qt`, returned application/window references, manual event
  processing, normal cleanup, and the simulation-only interactive safety boundary.
- Normal verification: 67 passed, 1 hardware test deselected; Ruff and mypy
  pass. Interactive launch tests used simulations only. No hardware was accessed.
- Added transient per-run mosaic geometry with tile-count mode starting at the
  observed XYZ pose and endpoint mode using inclusive absolute begin/end positions.
- Added editable per-run XYZ steps, inferred positive or negative axis direction,
  exact endpoint-spacing validation, derived grid previews, and unchanged schema-4 profiles.
- Normal verification: 65 passed, 1 hardware test deselected; Ruff and mypy
  pass. Tests remain simulation/fake-only. No hardware was accessed.
- Added schema-version-4 optional acquisition handshake settings with automatic
  in-memory migration of schema-version-3 application profiles.
- Added a preflighted tile handshake that replaces dwell, remains Stop-aware,
  preserves other output states, and fails on stale signals. Phase 22 later
  changed its levels and edge meanings to match the OCT protocol.
- Added the standalone `acq-staging-dummy-acquirer` GUI with independent DAQ
  configuration, timed or manual completion, counters, logging, and safe cleanup.
- Added separate simulated and placeholder hardware dummy-acquirer profiles.
- Normal verification: 56 passed, 1 hardware test deselected; Ruff and mypy
  pass. Acquisition tests use simulations only. No hardware was accessed.
- Added one dedicated `logs/mosaics/YYYY_MM_DD_HH-mm-ss.log` audit stream per
  run with synchronous tile, movement, position, timing, failure, and DAQ-output events.
- Added a non-modal mosaic tiling window with one grid per Z plane, a
  Pending/Current/Complete color legend, and worker-driven per-tile updates.
- Replaced the raw JSON configuration editor and separate toolbar file actions
  with one structured, tooltipped configuration window containing Load File,
  Save File, Save-to-application, and Cancel actions.
- Added schema-version-3 Digital In and Digital Out settings with simulated and
  lazy NI-DAQmx adapters, whole-port or selected-pin addressing, and I/O timeouts.
- Added two GUI panels for independent connection, input polling, current
  HIGH/LOW/UNKNOWN display, and atomic multi-pin output writes.
- Digital panels expose eight slots, gray out unconfigured lines, accept
  integer or `lineN` pin names, and continuously read inputs and reassert only
  output states that the operator has explicitly applied.
- NI-DAQ behavior is simulation/fake-verified only; no physical hardware was accessed.
- Added schema-version-2 mosaic settings for XYZ step sizes, tile counts,
  settling time, dwell time, and an enforced whole-mosaic timeout.
- Added full-path preflight and an explicitly confirmed serpentine XYZ mosaic
  worker with GUI progress and Stop Mosaic controls.
- Partial mosaics list disconnected XYZ components and require explicit
  acknowledgement before those axes are skipped; connected axes remain homed-only.
- Kept rotation fixed and excluded legacy vibratome, dual-Z, tilt, cutting,
  notification, and camera-acquisition behavior.
- Normal verification: 45 passed, 1 hardware test deselected; Ruff and mypy
  pass. Tests remain simulation/fake-only. No hardware was accessed.
- Reconciled the production adapters with the working standalone Zaber and
  BSC101/HDR50 scripts without copying their automatic motion sequences.
- Zaber connection validates discovered serial/model identity. Its earlier
  application-side `limit.min`/`limit.max` enforcement was removed in Phase 21.
- BSC101 connection now loads and reads the existing channel motor
  configuration, verifies the configured actuator settings name, and rejects
  undefined or invalid motor settings before motion is possible.
- Normal verification: 25 passed, 1 hardware test deselected; Ruff and mypy
  pass. Tests remain simulation/fake-only. No hardware was accessed.

## Next-session handoff

The next session should begin with a user-approved laboratory configuration review.
Before any hardware action, verify every port, serial number, axis assignment,
software limit, velocity, acceleration, timeout, and test distance. Hardware
adapter behavior is mock-verified only.

## Session history

- 2026-07-16: Implemented phases 0–8. Normal verification: 20 passed,
  1 hardware test deselected; Ruff clean; mypy clean. No hardware accessed.
