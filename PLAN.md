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
  and explicitly confirmed motion-only XYZ mosaic traversal.
- Default to simulation. Never connect, home, move, or run hardware tests automatically.
- Keep vendor calls inside adapters and all potentially blocking work outside the GUI thread.
- Do not implement vibratome or dual-Z behavior.

## Locked decisions

- Camera acquisition remains deferred; motion-only mosaic traversal is in scope.
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

## Current session

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
- Normal verification: 43 passed, 1 hardware test deselected; Ruff and mypy
  pass. Tests remain simulation/fake-only. No hardware was accessed.
- Reconciled the production adapters with the working standalone Zaber and
  BSC101/HDR50 scripts without copying their automatic motion sequences.
- Zaber connection now validates discovered serial/model identity and verifies
  configured application limits against device-reported `limit.min` and
  `limit.max` values.
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
