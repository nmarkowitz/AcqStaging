"""Vendor-neutral motion orchestration."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from acq_staging.adapters.base import MotionAxis
from acq_staging.adapters.digital import DigitalIODevice
from acq_staging.configuration import AppConfig, DummyAcquirerConfig
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import (
    HOME_ORDER,
    AxisName,
    DiagnosticResult,
    DigitalDirection,
    DigitalStatus,
    MosaicInputMode,
    MosaicProgress,
    MosaicRunSpec,
    MosaicTile,
    MosaicTilePhase,
    Pose,
)


class TileAcquisitionHandshake(Protocol):
    """Optional tile-level acquisition handshake used by mosaic orchestration."""

    @property
    def enabled(self) -> bool: ...

    def begin(self) -> None: ...

    def acquire_tile(
        self,
        tile_number: int,
        stop_event: threading.Event,
        mosaic_deadline: float | None,
        on_event: Callable[[str, dict[str, object]], None] | None,
    ) -> None: ...

    def abort(self) -> None: ...

    def finish(self) -> None: ...


class MotionService:
    """Coordinates components while enforcing application safety rules."""

    def __init__(
        self,
        config: AppConfig,
        axes: dict[AxisName, MotionAxis],
        acquisition_handshake: TileAcquisitionHandshake | None = None,
    ) -> None:
        self.config = config
        self.axes = axes
        self.acquisition_handshake = acquisition_handshake
        self._pose = Pose.empty()
        self._mosaic_stop = threading.Event()
        self._mosaic_lock = threading.Lock()
        self._mosaic_deadline: float | None = None

    @property
    def pose(self) -> Pose:
        """Return the most recently observed pose."""
        return self._pose

    def connect(self, axis: AxisName) -> None:
        self._axis(axis).connect()
        self.refresh_position(axis)

    def disconnect(self, axis: AxisName) -> None:
        self._axis(axis).disconnect()
        self._set_pose_value(axis, None)

    def connect_all(self) -> None:
        """Connect all enabled components without causing motion."""
        # Roll back earlier connections if a later component cannot connect.
        connected: list[AxisName] = []
        try:
            for axis in HOME_ORDER:
                if self.config.components[axis].enabled:
                    self.connect(axis)
                    connected.append(axis)
        except Exception:
            for axis in reversed(connected):
                self.disconnect(axis)
            raise

    def disconnect_all(self) -> None:
        # Reverse order mirrors resource acquisition and releases rotation first.
        for axis in reversed(HOME_ORDER):
            if axis in self.axes:
                self.disconnect(axis)

    def home(self, axis: AxisName) -> None:
        self._axis(axis).home()
        self.refresh_position(axis)

    def home_all(
        self,
        on_progress: Callable[[AxisName], None] | None = None,
    ) -> list[AxisName]:
        """Home all components in the required sequence, stopping on failure."""
        # Preflight every axis so Home All never begins with an incomplete system.
        for axis in HOME_ORDER:
            if not self.config.components[axis].enabled:
                raise MotionSafetyError(
                    f"{axis.value}: disabled; Home All requires every component"
                )
            if not self._axis(axis).status().connected:
                raise MotionSafetyError(f"{axis.value}: not connected")
        # Sequential calls enforce X -> Y -> Z -> Rotation and stop on exceptions.
        completed: list[AxisName] = []
        for axis in HOME_ORDER:
            if on_progress is not None:
                on_progress(axis)
            self.home(axis)
            completed.append(axis)
        return completed

    def move_absolute(self, axis: AxisName, destination: float) -> None:
        # Service-level limits protect every caller before reaching a vendor adapter.
        component = self.config.components[axis]
        if not component.limits.contains(destination):
            raise MotionSafetyError(f"{axis.value}: destination outside software limits")
        self._axis(axis).move_absolute(destination)
        self.refresh_position(axis)

    def move_relative(self, axis: AxisName, displacement: float) -> None:
        current = self._axis(axis).get_position()
        self.move_absolute(axis, current + displacement)

    def stop(self, axis: AxisName) -> None:
        self._axis(axis).stop()

    def identify(self, axis: AxisName) -> None:
        """Request a visual identifier from one connected component."""
        adapter = self._axis(axis)
        if not adapter.status().connected:
            raise MotionSafetyError(f"{axis.value}: must be connected before identification")
        adapter.identify()

    def refresh_position(self, axis: AxisName) -> float:
        position = self._axis(axis).get_position()
        self._set_pose_value(axis, position)
        return position

    def refresh_pose(self) -> Pose:
        # Disconnected axes retain their last explicit unknown value without I/O.
        for axis in HOME_ORDER:
            if axis in self.axes and self.axes[axis].status().connected:
                self.refresh_position(axis)
        return self._pose

    def diagnose(self, axis: AxisName) -> DiagnosticResult:
        adapter = self._axis(axis)
        identity = adapter.identity()
        status = adapter.status()
        return DiagnosticResult(identity, status, "Connection, identity, status, and position read")

    def motion_test(self, axis: AxisName) -> tuple[float, float]:
        """Perform a configured small move and return to the observed start."""
        adapter = self._axis(axis)
        status = adapter.status()
        if not status.connected or not status.homed:
            raise MotionSafetyError(f"{axis.value}: motion test requires a connected, homed axis")
        start = adapter.get_position()
        config = self.config.components[axis]
        distance = config.motion.test_distance
        # Prefer positive travel, falling back to negative travel near the upper limit.
        candidates = (start + distance, start - distance)
        target = next((value for value in candidates if config.limits.contains(value)), None)
        if target is None:
            raise MotionSafetyError(f"{axis.value}: no safe direction for motion test")
        # Always request return-to-start, even when verification of the test leg fails.
        try:
            self.move_absolute(axis, target)
            observed = adapter.get_position()
            if abs(observed - target) > config.motion.position_tolerance:
                raise AdapterError(f"{axis.value}: test destination was not reached")
        finally:
            self.move_absolute(axis, start)
        final = adapter.get_position()
        if abs(final - start) > config.motion.position_tolerance:
            raise AdapterError(f"{axis.value}: failed to return to test start")
        return start, target

    def mosaic_readiness(self) -> tuple[tuple[AxisName, ...], tuple[AxisName, ...]]:
        """Return disconnected and connected-but-unhomed mosaic axes."""
        # Separate promptable disconnections from unsafe connected-but-unhomed axes.
        disconnected: list[AxisName] = []
        unhomed: list[AxisName] = []
        for axis in (AxisName.X, AxisName.Y, AxisName.Z):
            status = self._axis(axis).status()
            if not status.connected:
                disconnected.append(axis)
            elif not status.homed:
                unhomed.append(axis)
            else:
                # Refresh setup-dialog defaults without moving any connected stage.
                self.refresh_position(axis)
        return tuple(disconnected), tuple(unhomed)

    def default_mosaic_run_spec(self) -> MosaicRunSpec:
        """Build transient count geometry from the active profile defaults."""
        config = self.config.mosaic
        return MosaicRunSpec.from_tile_counts(
            config.step_x_mm,
            config.step_y_mm,
            config.step_z_mm,
            config.tiles_x,
            config.tiles_y,
            config.tiles_z,
        )

    def plan_mosaic(
        self,
        allowed_disconnected: frozenset[AxisName] = frozenset(),
        run_spec: MosaicRunSpec | None = None,
    ) -> list[MosaicTile]:
        """Plan every tile from the current XYZ position without moving hardware."""
        # Use profile counts only when callers do not supply transient run geometry.
        spec = run_spec or self.default_mosaic_run_spec()

        # Snapshot count-mode origins before generating any absolute tile destination.
        origins: dict[AxisName, float] = {}
        for axis_index, axis in enumerate((AxisName.X, AxisName.Y, AxisName.Z)):
            status = self._axis(axis).status()
            if not status.connected:
                if axis not in allowed_disconnected:
                    raise MotionSafetyError(
                        f"mosaic requires acknowledgement for disconnected {axis.value}"
                    )
                # Use explicit endpoints or the nominal start to describe a skipped axis.
                explicit_begin = spec.beginning[axis_index]
                origins[axis] = (
                    explicit_begin
                    if explicit_begin is not None
                    else self.config.components[axis].simulation_start
                )
            else:
                if not status.homed:
                    raise MotionSafetyError(
                        f"mosaic requires connected axis {axis.value} to be homed"
                    )
                # Endpoint mode starts at its entered beginning instead of the current pose.
                explicit_begin = spec.beginning[axis_index]
                origins[axis] = (
                    explicit_begin
                    if explicit_begin is not None
                    else self._axis(axis).get_position()
                )

        tiles: list[MosaicTile] = []
        # Alternate X direction by Y row while retaining absolute tile coordinates.
        for z_index in range(spec.tiles_z):
            for y_index in range(spec.tiles_y):
                x_indices = range(spec.tiles_x)
                if y_index % 2:
                    x_indices = range(spec.tiles_x - 1, -1, -1)
                for x_index in x_indices:
                    tile = MosaicTile(
                        spec.coordinate(0, x_index, origins[AxisName.X]),
                        spec.coordinate(1, y_index, origins[AxisName.Y]),
                        spec.coordinate(2, z_index, origins[AxisName.Z]),
                        x_index,
                        y_index,
                        z_index,
                    )
                    for axis, destination in (
                        (AxisName.X, tile.x_mm),
                        (AxisName.Y, tile.y_mm),
                        (AxisName.Z, tile.z_mm),
                    ):
                        # Reject the entire path before the first vendor movement command.
                        if not self.config.components[axis].limits.contains(destination):
                            raise MotionSafetyError(
                                f"mosaic tile ({x_index}, {y_index}, {z_index}) places "
                                f"{axis.value}={destination} outside software limits"
                            )
                    tiles.append(tile)
        return tiles

    def run_mosaic(
        self,
        on_progress: Callable[[MosaicProgress], None] | None = None,
        allowed_disconnected: frozenset[AxisName] = frozenset(),
        on_event: Callable[[str, dict[str, object]], None] | None = None,
        run_spec: MosaicRunSpec | None = None,
    ) -> list[MosaicTile]:
        """Execute a preflighted XYZ serpentine mosaic until complete or stopped."""
        # A nonblocking lock prevents overlapping automated traversals.
        if not self._mosaic_lock.acquire(blocking=False):
            raise MotionSafetyError("a mosaic is already running")
        self._mosaic_stop.clear()
        self._mosaic_deadline = time.monotonic() + self.config.mosaic.operation_timeout_s
        handshake_started = False
        try:
            # Plan the complete path before the first command so limits fail safely.
            spec = run_spec or self.default_mosaic_run_spec()
            tiles = self.plan_mosaic(allowed_disconnected, spec)
            handshake = self.acquisition_handshake
            if handshake is not None and handshake.enabled:
                # DAQ preflight occurs before the first movement command.
                handshake.begin()
                handshake_started = True
            if on_event is not None:
                on_event(
                    "MOSAIC_START",
                    {
                        "total_tiles": len(tiles),
                        "allowed_disconnected": sorted(
                            axis.value for axis in allowed_disconnected
                        ),
                        "operation_timeout_s": self.config.mosaic.operation_timeout_s,
                        "acquisition_handshake": handshake_started,
                        "input_mode": spec.input_mode.value,
                        "steps_mm": list(spec.steps),
                        "tile_counts": list(spec.tile_counts),
                        "beginning_mm": (
                            list(spec.beginning)
                            if spec.input_mode is MosaicInputMode.ENDPOINTS
                            else [tiles[0].x_mm, tiles[0].y_mm, tiles[0].z_mm]
                        ),
                        "ending_mm": [
                            spec.coordinate(0, spec.tiles_x - 1, tiles[0].x_mm),
                            spec.coordinate(1, spec.tiles_y - 1, tiles[0].y_mm),
                            spec.coordinate(2, spec.tiles_z - 1, tiles[0].z_mm),
                        ],
                    },
                )
            for number, tile in enumerate(tiles, start=1):
                # Emit audit and GUI events around the exact work for each tile.
                self._require_mosaic_running()
                tile_details: dict[str, object] = {
                    "tile": number,
                    "total_tiles": len(tiles),
                    "x_index": tile.x_index,
                    "y_index": tile.y_index,
                    "z_index": tile.z_index,
                    "x_mm": tile.x_mm,
                    "y_mm": tile.y_mm,
                    "z_mm": tile.z_mm,
                }
                if on_event is not None:
                    on_event("TILE_START", tile_details)
                if on_progress is not None:
                    on_progress(
                        MosaicProgress(number, len(tiles), tile, MosaicTilePhase.CURRENT)
                    )
                # Move one axis at a time and skip coordinates already within tolerance.
                self._move_mosaic_axis(
                    AxisName.Z, tile.z_mm, allowed_disconnected, number, on_event
                )
                self._move_mosaic_axis(
                    AxisName.Y, tile.y_mm, allowed_disconnected, number, on_event
                )
                self._move_mosaic_axis(
                    AxisName.X, tile.x_mm, allowed_disconnected, number, on_event
                )
                if on_event is not None:
                    on_event(
                        "SETTLING_START",
                        {"tile": number, "duration_s": self.config.mosaic.settling_time_s},
                    )
                self._interruptible_wait(self.config.mosaic.settling_time_s)
                if on_event is not None:
                    on_event("SETTLING_COMPLETE", {"tile": number})
                if handshake_started and handshake is not None:
                    # Acquisition completion replaces the motion-only placeholder dwell.
                    if on_progress is not None:
                        on_progress(
                            MosaicProgress(
                                number,
                                len(tiles),
                                tile,
                                MosaicTilePhase.ACQUIRING,
                            )
                        )
                    handshake.acquire_tile(
                        number,
                        self._mosaic_stop,
                        self._mosaic_deadline,
                        on_event,
                    )
                else:
                    if on_event is not None:
                        on_event(
                            "DWELL_START",
                            {"tile": number, "duration_s": self.config.mosaic.dwell_time_s},
                        )
                    self._interruptible_wait(self.config.mosaic.dwell_time_s)
                    if on_event is not None:
                        on_event("DWELL_COMPLETE", {"tile": number})
                if on_progress is not None:
                    on_progress(
                        MosaicProgress(number, len(tiles), tile, MosaicTilePhase.COMPLETE)
                    )
                if on_event is not None:
                    on_event("TILE_COMPLETE", tile_details)
            if on_event is not None:
                on_event("MOSAIC_COMPLETE", {"total_tiles": len(tiles)})
            return tiles
        except Exception as exc:
            # Preserve the original error while logging it and safely stopping all axes.
            if on_event is not None:
                with suppress(Exception):
                    on_event("MOSAIC_FAILED", {"error": str(exc)})
            self._mosaic_stop.set()
            for axis in (AxisName.X, AxisName.Y, AxisName.Z):
                with suppress(Exception):
                    self.stop(axis)
            raise
        finally:
            # Release DAQ ownership after success, preflight failure, timeout, or Stop.
            if handshake_started and self.acquisition_handshake is not None:
                self.acquisition_handshake.finish()
            self._mosaic_deadline = None
            self._mosaic_lock.release()

    def stop_mosaic(self) -> None:
        """Cancel mosaic waits and request a stop from each participating axis."""
        # Wake interruptible waits before requesting vendor stops from every scan axis.
        self._mosaic_stop.set()
        if self.acquisition_handshake is not None:
            with suppress(Exception):
                self.acquisition_handshake.abort()
        for axis in (AxisName.X, AxisName.Y, AxisName.Z):
            self.stop(axis)

    def _move_mosaic_axis(
        self,
        axis: AxisName,
        destination: float,
        allowed_disconnected: frozenset[AxisName],
        tile_number: int,
        on_event: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        # Recheck connection and homing immediately before every possible move.
        self._require_mosaic_running()
        status = self._axis(axis).status()
        if not status.connected:
            if axis in allowed_disconnected:
                if on_event is not None:
                    on_event(
                        "AXIS_SKIPPED",
                        {
                            "tile": tile_number,
                            "axis": axis.value,
                            "reason": "acknowledged disconnected axis",
                            "nominal_destination": destination,
                        },
                    )
                return
            raise MotionSafetyError(f"{axis.value}: disconnected during mosaic")
        if not status.homed:
            raise MotionSafetyError(f"{axis.value}: must be homed during mosaic")
        current = self._axis(axis).get_position()
        if abs(current - destination) > self.config.components[axis].motion.position_tolerance:
            if on_event is not None:
                on_event(
                    "MOVEMENT_START",
                    {
                        "tile": tile_number,
                        "axis": axis.value,
                        "start": current,
                        "destination": destination,
                    },
                )
            self.move_absolute(axis, destination)
            observed = self._axis(axis).get_position()
            if on_event is not None:
                on_event(
                    "MOVEMENT_COMPLETE",
                    {
                        "tile": tile_number,
                        "axis": axis.value,
                        "destination": destination,
                        "observed": observed,
                    },
                )
        elif on_event is not None:
            on_event(
                "POSITION_REUSED",
                {
                    "tile": tile_number,
                    "axis": axis.value,
                    "position": current,
                },
            )

    def _interruptible_wait(self, duration_s: float) -> None:
        # Bound settling and dwell waits by both Stop and the whole-mosaic deadline.
        self._require_mosaic_running()
        deadline = self._mosaic_deadline
        remaining = duration_s
        if deadline is not None:
            remaining = min(duration_s, max(0.0, deadline - time.monotonic()))
        if remaining and self._mosaic_stop.wait(remaining):
            raise AdapterError("mosaic stopped")
        self._require_mosaic_running()

    def _require_mosaic_running(self) -> None:
        if self._mosaic_stop.is_set():
            raise AdapterError("mosaic stopped")
        if self._mosaic_deadline is not None and time.monotonic() >= self._mosaic_deadline:
            raise AdapterError("mosaic operation timed out")

    def _axis(self, axis: AxisName) -> MotionAxis:
        try:
            return self.axes[axis]
        except KeyError as exc:
            raise AdapterError(f"{axis.value}: no adapter configured") from exc

    def _set_pose_value(self, axis: AxisName, value: float | None) -> None:
        # Replace the immutable pose field matching the fixed [x, y, z, theta] order.
        now = datetime.now(UTC)
        match axis:
            case AxisName.X:
                self._pose = replace(self._pose, x_mm=value, timestamp=now)
            case AxisName.Y:
                self._pose = replace(self._pose, y_mm=value, timestamp=now)
            case AxisName.Z:
                self._pose = replace(self._pose, z_mm=value, timestamp=now)
            case AxisName.ROTATION:
                self._pose = replace(self._pose, theta_deg=value, timestamp=now)


class DigitalIOService:
    """Coordinate digital input and output devices without vendor leakage."""

    def __init__(
        self,
        config: AppConfig | DummyAcquirerConfig,
        devices: dict[DigitalDirection, DigitalIODevice],
    ) -> None:
        self.config = config
        self.devices = devices
        self._event_callback: Callable[[str, dict[str, object]], None] | None = None
        self._handshake_active = False
        self._lock = threading.RLock()

    def set_event_callback(
        self,
        callback: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        """Attach an audit callback while a mosaic run is active."""
        self._event_callback = callback

    def connect(self, direction: DigitalDirection) -> None:
        # Confirm a new input task can read once or clean it up as a failed connection.
        device = self.devices[direction]
        device.connect()
        try:
            if direction is DigitalDirection.INPUT:
                device.read_states()
        except Exception:
            device.disconnect()
            raise

    def disconnect(self, direction: DigitalDirection) -> None:
        self.devices[direction].disconnect()

    def disconnect_all(self) -> None:
        for direction in reversed(tuple(DigitalDirection)):
            self.disconnect(direction)

    def status(self, direction: DigitalDirection) -> DigitalStatus:
        return self.devices[direction].status()

    def read_inputs(self) -> dict[int, bool | None]:
        return self.devices[DigitalDirection.INPUT].read_states()

    def write_outputs(self, states: dict[int, bool], source: str = "explicit") -> None:
        # Audit only successful writes so the log reflects actual completed DAQ calls.
        with self._lock:
            if self._handshake_active and source not in {"handshake", "cleanup"}:
                raise MotionSafetyError("digital outputs are reserved by an active handshake")
            self.devices[DigitalDirection.OUTPUT].write_states(states)
            if self._event_callback is not None:
                self._event_callback(
                    "DIGITAL_OUTPUT_WRITE",
                    {
                        "source": source,
                        "states": {str(line): state for line, state in states.items()},
                    },
                )

    def begin_handshake(self, input_line: int, output_line: int) -> None:
        """Reserve the output port after verifying both handshake lines are idle LOW."""
        with self._lock:
            if self._handshake_active:
                raise MotionSafetyError("a DAQ handshake is already active")
            # A fresh input read rejects disconnected or stale acquisition completion.
            inputs = self.read_inputs()
            output = self.status(DigitalDirection.OUTPUT)
            if not output.connected:
                raise MotionSafetyError("digital_out must be connected for acquisition")
            if inputs.get(input_line) is not False:
                raise MotionSafetyError(
                    f"digital_in line{input_line} must be known and LOW before starting"
                )
            if output.states.get(output_line) is not False:
                raise MotionSafetyError(
                    f"digital_out line{output_line} must be initialized LOW before starting"
                )
            self._handshake_active = True

    def end_handshake(self) -> None:
        """Release exclusive output ownership after returning the signal LOW."""
        with self._lock:
            self._handshake_active = False

    def write_output_line(self, line: int, state: bool, source: str = "handshake") -> None:
        """Change one output line while preserving every other initialized state."""
        with self._lock:
            output = self.status(DigitalDirection.OUTPUT)
            if not output.connected:
                raise AdapterError("digital_out: not connected")
            if line not in output.states:
                raise AdapterError(f"digital_out: line{line} is not configured")
            if any(value is None for value in output.states.values()):
                raise MotionSafetyError(
                    "all configured digital outputs must be initialized before handshaking"
                )
            states = {pin: bool(value) for pin, value in output.states.items()}
            states[line] = state
            self.write_outputs(states, source=source)

    def refresh_connected_io(self) -> dict[int, bool | None] | None:
        """Poll inputs and reassert the last explicitly applied output levels."""
        # Unknown outputs have never been applied and must not be written automatically.
        inputs = None
        if self.status(DigitalDirection.INPUT).connected:
            inputs = self.read_inputs()
        output = self.status(DigitalDirection.OUTPUT)
        if (
            output.connected
            and not self._handshake_active
            and all(state is not None for state in output.states.values())
        ):
            states = {line: bool(state) for line, state in output.states.items()}
            self.write_outputs(states, source="continuous_reassertion")
        return inputs
