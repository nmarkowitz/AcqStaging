# AcqStaging Agent Guide

## Project contract

AcqStaging is a Python 3.12 and PyQt6 motion-control application for three Zaber
linear axes (X, Y, and one Z) and a rotation stage driven through channel 1 of
a Thorlabs BSC101 benchtop stepper motor controller.
Track the scan-head pose in the fixed order `[x_mm, y_mm, z_mm, theta_deg]`.

The current scope is the motion-control foundation. Do not add a vibratome,
dual-Z behavior, camera acquisition, mosaic scanning, or an automated scan
workflow unless the project contract is explicitly changed by the user.

Read `PLAN.md` before making changes. Treat its project contract and locked
decisions as authoritative. Use `README.md` for current user-facing behavior and
commands. Do not revive requirements from `old_plan.md` or the legacy reference
application when they conflict with `PLAN.md`.

## Non-negotiable hardware safety

This repository controls physical hardware. Preserve all of these rules:

- Default to simulated hardware and disconnected components.
- Never connect to, home, jog, move, stop, or otherwise command physical
  hardware automatically.
- Never run hardware-marked tests or real-hardware diagnostic commands unless
  the user explicitly requests that exact hardware action in the current task.
- Do not treat permission to inspect or connect as permission to home or move.
- Before any user-approved hardware action, require review of the profile's
  ports, serial numbers, device and axis assignments, software limits,
  velocities, accelerations, timeouts, and test distances.
- Connecting must not home or move. Homing and motion must remain separate,
  explicit user actions with confirmation.
- Home All must run sequentially in exactly `X -> Y -> Z -> Rotation` order and
  stop on the first failure.
- Enforce configured software limits before issuing a vendor movement command.
- Motion diagnostics must require explicit confirmation, use the configured
  small test distance, and attempt to return to the observed starting position.
  Diagnostics are non-motion by default.
- Never weaken a limit, bypass homing, retry motion automatically, or conceal a
  device mismatch merely to make an operation succeed.
- On timeout or failure, prefer a safe stop and a visible error. Do not initiate
  recovery motion without explicit user direction.

Normal automated tests must use `SimulatedMotionAxis`, mocks, or vendor fakes.
The `hardware` pytest marker is excluded by default and must stay excluded.

## Architecture boundaries

- Keep vendor-neutral contracts in `adapters/base.py`, domain and configuration
  types in `models.py`, orchestration in `services.py`, and adapter construction
  in `factory.py`.
- Keep every Zaber call in `adapters/zaber.py` and every Kinesis/pythonnet call
  in `adapters/thorlabs.py`. Vendor objects and vendor-specific types must not
  leak into services, models, diagnostics, or GUI code.
- Use the same vendor-neutral motion-axis interface for simulation and real
  hardware. Extend that interface deliberately instead of branching on vendor
  types throughout the application.
- Keep shared Zaber serial-connection ownership and cleanup centralized; X, Y,
  and Z may share a port or use separate ports.
- Load Thorlabs DLLs lazily. Simulation and normal test collection must work on
  systems without Kinesis installed.
- Store settings only in validated, versioned JSON application profiles. Saving
  a profile must not write settings to vendor device memory.
- Preserve atomic profile saving, required X/Y/Z/Rotation entries, explicit
  units (millimetres for linear axes and degrees for rotation), and typed model
  validation.
- Keep widgets authored directly in Python. Put long-running or potentially
  blocking connection, polling, homing, movement, and diagnostic work in the
  existing worker/service path, never in the GUI thread.
- Keep Stop available during operations and ensure shutdown cleans up workers,
  polling, devices, and shared connections.

## Vendor API work

Never invent a vendor method, overload, status property, unit, or lifecycle
step. Before modifying a vendor integration, inspect both the installed package
or assembly available in the active environment and the local primary
reference. If the installed version and examples disagree, stop and document
the discrepancy rather than guessing.

For Zaber, use `references/zaber-examples` as the primary API reference. The
project is pinned to `zaber-motion==9.3.2`; preserve explicit units and
nonblocking vendor commands such as `wait_until_idle=False`, with waiting and
timeouts handled outside the GUI thread. Do not copy example behavior that
connects or homes at startup.

For Thorlabs BSC101 work, use
`references/Thorlabs-examples/Python/Kinesis/Benchtop/BSC101/bsc101_pythonnet.py`
as the primary example, then verify callable overloads and properties against
the installed Kinesis assemblies through pythonnet. Preserve the controller and
channel-1 lifecycle, profile-driven DLL directory, .NET `Decimal` conversion,
polling cleanup, controller disconnect, and callback-based/nonblocking motion
handling. Do not copy the example's automatic home/move sequence or its motor
configuration mutation into production paths.

Use `references/OCTStages` only to understand legacy intent. It is not the API
or architecture source of truth; its C++/Qt implementation, vibratome support,
dual-stage assumptions, and old Thorlabs setup must not be carried forward.

## Change workflow

1. Read the relevant parts of `PLAN.md`, `README.md`, implementation, tests, and
   local vendor examples before editing.
2. Inspect the working tree and preserve unrelated user changes. Keep each
   change scoped to the requested behavior.
3. For hardware-facing changes, begin with or extend simulation/fake coverage.
   Do not validate by touching real devices.
4. Keep safety checks in services or adapters as appropriate, not only in GUI
   confirmation dialogs. CLI and future callers must receive the same safety.
5. Update configuration examples and user documentation when behavior or schema
   changes. Never put real laboratory serial numbers or ports into committed
   example profiles.
6. Run the normal verification suite after each implementation phase. Report
   failures and do not describe a phase as complete until all three checks pass.

## Required normal verification

Run from the repository root in PowerShell:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -p no:cacheprovider
python -m ruff check .
python -m mypy src tests scripts
Remove-Item Env:QT_QPA_PLATFORM
```

Do not run `python -m pytest -m hardware`. The present hardware-marked test is a
safety guard, not a laboratory validation suite. For focused iteration, run the
smallest relevant simulated/fake test first, but still run the complete normal
suite before considering a phase complete.

When adding behavior, add or update tests for the applicable invariant:

- configuration validation and JSON round trips;
- connection and homing prerequisites;
- software limits, timeouts, stopping, and cleanup;
- exact Home All ordering and failure short-circuiting;
- pose ordering and updates;
- non-motion diagnostics and explicit motion acknowledgement;
- safe simulated GUI startup and worker-thread execution;
- vendor units, arguments, lifecycle, identity checks, and cleanup using fakes.

## Completion standard

A change is complete only when it preserves simulation-first startup, explicit
hardware authorization, adapter isolation, responsive GUI behavior, validated
configuration, and cleanup on failure; includes proportional simulated or fake
tests; updates documentation when needed; and passes pytest, Ruff, and mypy.

Always state whether any physical hardware was accessed. Unless explicitly
requested and authorized, the expected statement is that no hardware was
connected, homed, moved, stopped, or tested.
