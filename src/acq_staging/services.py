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
    ComponentStatus,
    DiagnosticResult,
    DigitalDirection,
    DigitalStatus,
    LimitsConfig,
    MosaicProgress,
    MosaicSequenceSpec,
    MosaicTile,
    MosaicTilePhase,
    MosaicTilingSpec,
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
    """Coordinate controller-home motion and preflighted mosaic sequences."""

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
        self._mosaic_pause = threading.Event()
        self._mosaic_lock = threading.Lock()
        self._mosaic_deadline: float | None = None

    @property
    def pose(self) -> Pose:
        """Return the latest controller-home pose."""
        return self._pose

    def component_status(self, axis: AxisName) -> ComponentStatus:
        """Return adapter status in controller-home coordinates."""
        return self._axis(axis).status()

    def axis_limits(self, axis: AxisName) -> LimitsConfig | None:
        """Return configured simulation limits or no application limit for hardware."""
        return self._axis(axis).get_limits()

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
        """Disconnect all components in reverse acquisition order."""
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
        """Home all components in required X, Y, Z, Rotation order."""
        # Preflight the complete set so homing never begins with a missing component.
        for axis in HOME_ORDER:
            if not self.config.components[axis].enabled:
                raise MotionSafetyError(f"{axis.value}: disabled; Home All requires every axis")
            if not self._axis(axis).status().connected:
                raise MotionSafetyError(f"{axis.value}: not connected")
        completed: list[AxisName] = []
        for axis in HOME_ORDER:
            if on_progress is not None:
                on_progress(axis)
            self.home(axis)
            completed.append(axis)
        return completed

    def move_absolute(self, axis: AxisName, destination: float) -> None:
        """Move one homed axis to a controller-home destination."""
        adapter = self._axis(axis)
        limits = adapter.get_limits()
        # Simulations enforce their synthetic range while hardware delegates to its vendor.
        if limits is not None and not limits.contains(destination):
            raise MotionSafetyError(f"{axis.value}: destination outside simulation limits")
        adapter.move_absolute(destination)
        self.refresh_position(axis)

    def move_relative(self, axis: AxisName, displacement: float) -> None:
        current = self._axis(axis).get_position()
        self.move_absolute(axis, current + displacement)

    def stop(self, axis: AxisName) -> None:
        self._axis(axis).stop()

    def identify(self, axis: AxisName) -> None:
        """Request visual identification from one connected component."""
        adapter = self._axis(axis)
        if not adapter.status().connected:
            raise MotionSafetyError(f"{axis.value}: must be connected before identification")
        adapter.identify()

    def refresh_position(self, axis: AxisName) -> float:
        position = self._axis(axis).get_position()
        self._set_pose_value(axis, position)
        return position

    def refresh_pose(self) -> Pose:
        """Refresh every connected component without commanding motion."""
        for axis in HOME_ORDER:
            if axis in self.axes and self.axes[axis].status().connected:
                self.refresh_position(axis)
        return self._pose

    def diagnose(self, axis: AxisName) -> DiagnosticResult:
        adapter = self._axis(axis)
        return DiagnosticResult(
            adapter.identity(),
            adapter.status(),
            "Connection, identity, status, and position read",
        )

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
        limits = adapter.get_limits()
        target = (
            candidates[0]
            if limits is None
            else next((value for value in candidates if limits.contains(value)), None)
        )
        if target is None:
            raise MotionSafetyError(f"{axis.value}: no safe direction for motion test")
        # Always request return-to-start even if destination verification fails.
        try:
            adapter.move_absolute(target)
            if abs(adapter.get_position() - target) > config.motion.position_tolerance:
                raise AdapterError(f"{axis.value}: test destination was not reached")
        finally:
            adapter.move_absolute(start)
            self.refresh_position(axis)
        if abs(adapter.get_position() - start) > config.motion.position_tolerance:
            raise AdapterError(f"{axis.value}: failed to return to test start")
        return start, target

    def mosaic_readiness(self) -> tuple[tuple[AxisName, ...], tuple[AxisName, ...]]:
        """Return disconnected and unhomed axes required by the full sequence."""
        disconnected: list[AxisName] = []
        unhomed: list[AxisName] = []
        for axis in HOME_ORDER:
            status = self._axis(axis).status()
            if not status.connected:
                disconnected.append(axis)
            elif not status.homed:
                unhomed.append(axis)
            else:
                self.refresh_position(axis)
        return tuple(disconnected), tuple(unhomed)

    def default_mosaic_sequence_spec(self) -> MosaicSequenceSpec:
        """Build a one-tile-per-plane sequence from the observed pose."""
        values = self.pose.as_vector()
        if any(value is None for value in values):
            raise MotionSafetyError("all axis positions must be known before mosaic setup")
        x_value, y_value, z_value, theta_value = values
        assert x_value is not None and y_value is not None
        assert z_value is not None and theta_value is not None
        x_mm, y_mm, z_mm, theta_deg = x_value, y_value, z_value, theta_value
        config = self.config.mosaic
        first = MosaicTilingSpec.from_endpoints(
            config.step_x_mm,
            config.step_y_mm,
            x_mm,
            y_mm,
            x_mm,
            y_mm,
            z_mm,
            0.0,
            config.orientation,
        )
        second = MosaicTilingSpec.from_endpoints(
            config.step_x_mm,
            config.step_y_mm,
            x_mm,
            y_mm,
            x_mm,
            y_mm,
            z_mm,
            theta_deg,
            config.orientation,
        )
        return MosaicSequenceSpec(first, second, z_mm)

    def plan_mosaic_tiling(
        self,
        spec: MosaicTilingSpec,
        tiling_index: int,
    ) -> list[MosaicTile]:
        """Plan one fixed-Z, fixed-rotation serpentine XY tiling."""
        tiles: list[MosaicTile] = []
        for row in range(spec.tiles_vertical):
            columns = range(spec.tiles_horizontal)
            if row % 2:
                columns = range(spec.tiles_horizontal - 1, -1, -1)
            for column in columns:
                # Logical row and column preserve top-left serpentine order for every mounting.
                x_mm, y_mm = spec.controller_position(column, row)
                tiles.append(
                    MosaicTile(
                        x_mm,
                        y_mm,
                        spec.z_mm,
                        spec.rotation_deg,
                        column,
                        row,
                        tiling_index,
                    )
                )
        return tiles

    def plan_mosaic_sequence(
        self,
        spec: MosaicSequenceSpec,
    ) -> tuple[list[MosaicTile], ...]:
        """Preflight every transition and tile before the first motion command."""
        # A full run never bypasses connection or homing for any participating axis.
        disconnected, unhomed = self.mosaic_readiness()
        if disconnected:
            names = ", ".join(axis.value for axis in disconnected)
            raise MotionSafetyError(f"mosaic requires connected axes: {names}")
        if unhomed:
            names = ", ".join(axis.value for axis in unhomed)
            raise MotionSafetyError(f"mosaic requires homed axes: {names}")

        # Simulations preflight configured ranges; hardware delegates all limits to vendors.
        z_limits = self.axis_limits(AxisName.Z)
        rotation_limits = self.axis_limits(AxisName.ROTATION)
        if z_limits is not None and not z_limits.contains(spec.safe_transition_z_mm):
            raise MotionSafetyError("safe transition Z is outside simulation limits")
        for number, tiling in enumerate(spec.tilings, start=1):
            if z_limits is not None and not z_limits.contains(tiling.z_mm):
                raise MotionSafetyError(f"tiling {number} Z is outside simulation limits")
            if rotation_limits is not None and not rotation_limits.contains(
                tiling.rotation_deg
            ):
                raise MotionSafetyError(
                    f"tiling {number} rotation is outside simulation limits"
                )

        # Disabled tilings are omitted before any geometry or motion is considered.
        planned = tuple(
            self.plan_mosaic_tiling(tiling, tiling_index)
            for tiling_index, tiling in enumerate(spec.tilings)
        )
        # Checking all XY destinations up front prevents partial acquisition on bad geometry.
        for number, tiles in enumerate(planned, start=1):
            for tile in tiles:
                for axis, destination in ((AxisName.X, tile.x_mm), (AxisName.Y, tile.y_mm)):
                    limits = self.axis_limits(axis)
                    if limits is not None and not limits.contains(destination):
                        raise MotionSafetyError(
                            f"tiling {number} tile ({tile.x_index}, {tile.y_index}) places "
                            f"{axis.value}={destination} outside simulation limits"
                        )
        return planned

    def run_mosaic_sequence(
        self,
        spec: MosaicSequenceSpec,
        on_progress: Callable[[MosaicProgress], None] | None = None,
        on_event: Callable[[str, dict[str, object]], None] | None = None,
    ) -> tuple[list[MosaicTile], ...]:
        """Run enabled preflighted tilings sequentially under one timeout and handshake."""
        if not self._mosaic_lock.acquire(blocking=False):
            raise MotionSafetyError("a mosaic sequence is already running")
        self._mosaic_stop.clear()
        self._mosaic_pause.clear()
        self._mosaic_deadline = time.monotonic() + self.config.mosaic.operation_timeout_s
        handshake_started = False
        active_tiling: int | None = None
        try:
            planned = self.plan_mosaic_sequence(spec)
            handshake = self.acquisition_handshake
            if handshake is not None and handshake.enabled:
                # DAQ ownership is acquired once and retained across all enabled tilings.
                handshake.begin()
                handshake_started = True

            # Resume skips all earlier work while retaining absolute traversal numbering.
            for tiling_index in range(spec.resume_tiling - 1, len(spec.tilings)):
                tiling_number = tiling_index + 1
                active_tiling = tiling_number
                tiling = spec.tilings[tiling_index]
                tiles = planned[tiling_index]
                start_tile = spec.resume_tile if tiling_number == spec.resume_tiling else 1
                if on_event is not None:
                    on_event(
                        "TILING_START",
                        {
                            "tiling": tiling_number,
                            "start_tile": start_tile,
                            "total_tiles": len(tiles),
                            "absolute_z_mm": tiling.z_mm,
                            "absolute_rotation_deg": tiling.rotation_deg,
                        },
                    )
                self._transition_to_tiling(
                    tiling_number, tiling, spec.safe_transition_z_mm, on_event,
                    spec.tilings[tiling_index - 1] if tiling_index > 0 else None,
                )
                self._run_tiling(
                    tiling_number,
                    tiles,
                    start_tile,
                    spec.total_tiles,
                    handshake if handshake_started else None,
                    on_progress,
                    on_event,
                )
                if on_event is not None:
                    on_event("TILING_COMPLETE", {"tiling": tiling_number})
                active_tiling = None
            if on_event is not None:
                on_event("FULL_RUN_COMPLETE", {"total_tiles": spec.total_tiles})
            return planned
        except Exception as exc:
            # Failure prevents the next tiling and requests a safe stop from every axis.
            if on_event is not None:
                with suppress(Exception):
                    if active_tiling is not None:
                        on_event(
                            "TILING_FAILED",
                            {"tiling": active_tiling, "error": str(exc)},
                        )
                    on_event("FULL_RUN_FAILED", {"error": str(exc)})
            self._mosaic_stop.set()
            for axis in HOME_ORDER:
                with suppress(Exception):
                    self.stop(axis)
            raise
        finally:
            # DAQ ownership and the run lock are released on every terminal path.
            if handshake_started and self.acquisition_handshake is not None:
                self.acquisition_handshake.finish()
            self._mosaic_deadline = None
            self._mosaic_pause.clear()
            self._mosaic_lock.release()

    def pause_mosaic(self) -> None:
        """Request a pause at the next safe mosaic orchestration boundary."""
        if not self._mosaic_lock.locked():
            raise MotionSafetyError("no mosaic sequence is running")
        self._mosaic_pause.set()

    def continue_mosaic(self) -> None:
        """Release a paused mosaic so worker-thread orchestration can continue."""
        if not self._mosaic_lock.locked():
            raise MotionSafetyError("no mosaic sequence is running")
        self._mosaic_pause.clear()

    def cancel_mosaic(self) -> None:
        """Cancel the full sequence and request a stop from all four axes."""
        # Release the pause gate so cancellation is observed without waiting for Continue.
        self._mosaic_stop.set()
        self._mosaic_pause.clear()
        if self.acquisition_handshake is not None:
            with suppress(Exception):
                self.acquisition_handshake.abort()
        for axis in HOME_ORDER:
            with suppress(Exception):
                self.stop(axis)

    def _transition_to_tiling(
        self,
        tiling_number: int,
        tiling: MosaicTilingSpec,
        safe_z_mm: float,
        on_event: Callable[[str, dict[str, object]], None] | None,
        previous_tiling: MosaicTilingSpec | None = None,
    ) -> None:
        """Reach safe Z and the previous tiling center before changing rotation."""
        self._require_mosaic_running()
        if on_event is not None:
            on_event("TRANSITION_START", {"tiling": tiling_number})
        rotation = self._axis(AxisName.ROTATION).get_position()
        tolerance = self.config.components[AxisName.ROTATION].motion.position_tolerance
        if abs(rotation - tiling.rotation_deg) > tolerance:
            # Rotation is allowed only after Z reaches the explicitly reviewed safe height.
            self._move_sequence_axis(
                AxisName.Z, safe_z_mm, tiling_number, None, "safe_rotation_z", on_event
            )
            # The midpoint stays inside the previously preflighted XY bounds, even on resume.
            if previous_tiling is not None:
                for axis, begin, end in (
                    (AxisName.X, previous_tiling.begin_x_mm, previous_tiling.end_x_mm),
                    (AxisName.Y, previous_tiling.begin_y_mm, previous_tiling.end_y_mm),
                ):
                    self._move_sequence_axis(
                        axis, begin / 2.0 + end / 2.0, tiling_number, None,
                        "rotation_center", on_event,
                    )
            self._move_sequence_axis(
                AxisName.ROTATION,
                tiling.rotation_deg,
                tiling_number,
                None,
                "rotation_target",
                on_event,
            )
        self._move_sequence_axis(
            AxisName.Z, tiling.z_mm, tiling_number, None, "tiling_z", on_event
        )
        if on_event is not None:
            on_event("TRANSITION_COMPLETE", {"tiling": tiling_number})

    def _run_tiling(
        self,
        tiling_number: int,
        tiles: list[MosaicTile],
        start_tile: int,
        overall_total: int,
        handshake: TileAcquisitionHandshake | None,
        on_progress: Callable[[MosaicProgress], None] | None,
        on_event: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        """Execute one XY tiling while Z and rotation remain fixed."""
        overall_offset = 0 if tiling_number == 1 else overall_total - len(tiles)
        for number, tile in enumerate(tiles[start_tile - 1 :], start=start_tile):
            self._require_mosaic_running()
            overall_number = overall_offset + number
            details: dict[str, object] = {
                "tiling": tiling_number,
                "tile": number,
                "total_tiles": len(tiles),
                "overall_tile": overall_number,
                "overall_total": overall_total,
                "x_index": tile.x_index,
                "y_index": tile.y_index,
                "absolute_x_mm": tile.x_mm,
                "absolute_y_mm": tile.y_mm,
                "absolute_z_mm": tile.z_mm,
                "absolute_rotation_deg": tile.theta_deg,
            }
            if on_event is not None:
                on_event("TILE_START", details)
            self._emit_progress(
                on_progress, tiling_number, number, len(tiles), overall_number, overall_total, tile,
                MosaicTilePhase.CURRENT,
            )
            # Per-tile movement is XY-only because transition established fixed Z and rotation.
            self._move_sequence_axis(
                AxisName.Y, tile.y_mm, tiling_number, number, "tile", on_event
            )
            self._move_sequence_axis(
                AxisName.X, tile.x_mm, tiling_number, number, "tile", on_event
            )
            if on_event is not None:
                on_event(
                    "SETTLING_START",
                    {"tiling": tiling_number, "tile": number,
                     "duration_s": self.config.mosaic.settling_time_s},
                )
            self._interruptible_wait(self.config.mosaic.settling_time_s)
            if on_event is not None:
                on_event("SETTLING_COMPLETE", {"tiling": tiling_number, "tile": number})
            if handshake is not None:
                self._emit_progress(
                    on_progress, tiling_number, number, len(tiles), overall_number,
                    overall_total, tile, MosaicTilePhase.ACQUIRING,
                )
                handshake.acquire_tile(
                    overall_number, self._mosaic_stop, self._mosaic_deadline, on_event
                )
            else:
                if on_event is not None:
                    on_event(
                        "DWELL_START",
                        {"tiling": tiling_number, "tile": number,
                         "duration_s": self.config.mosaic.dwell_time_s},
                    )
                self._interruptible_wait(self.config.mosaic.dwell_time_s)
                if on_event is not None:
                    on_event("DWELL_COMPLETE", {"tiling": tiling_number, "tile": number})
            self._emit_progress(
                on_progress, tiling_number, number, len(tiles), overall_number, overall_total, tile,
                MosaicTilePhase.COMPLETE,
            )
            if on_event is not None:
                on_event("TILE_COMPLETE", details)

    @staticmethod
    def _emit_progress(
        callback: Callable[[MosaicProgress], None] | None,
        tiling_number: int,
        number: int,
        total: int,
        overall_number: int,
        overall_total: int,
        tile: MosaicTile,
        phase: MosaicTilePhase,
    ) -> None:
        """Publish one immutable progress record when a GUI callback is present."""
        if callback is not None:
            callback(
                MosaicProgress(
                    tiling_number,
                    number,
                    total,
                    overall_number,
                    overall_total,
                    tile,
                    phase,
                )
            )

    def _move_sequence_axis(
        self,
        axis: AxisName,
        destination: float,
        tiling_number: int,
        tile_number: int | None,
        stage: str,
        on_event: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        """Move one required axis and synchronously audit start and observed completion."""
        self._require_mosaic_running()
        status = self._axis(axis).status()
        if not status.connected or not status.homed:
            raise MotionSafetyError(f"{axis.value}: disconnected or unhomed during mosaic")
        current = self._axis(axis).get_position()
        units = "deg" if axis is AxisName.ROTATION else "mm"
        details: dict[str, object] = {
            "tiling": tiling_number,
            "tile": tile_number,
            "stage": stage,
            "axis": axis.value,
            "units": units,
        }
        tolerance = self.config.components[axis].motion.position_tolerance
        if abs(current - destination) <= tolerance:
            if on_event is not None:
                on_event("POSITION_REUSED", {**details, "absolute_position": current})
            return
        if on_event is not None:
            on_event(
                "MOVEMENT_START",
                {
                    **details,
                    "absolute_start": current,
                    "absolute_destination": destination,
                },
            )
        self.move_absolute(axis, destination)
        observed = self.refresh_position(axis)
        if on_event is not None:
            on_event(
                "MOVEMENT_COMPLETE",
                {
                    **details,
                    "absolute_destination": destination,
                    "absolute_observed": observed,
                },
            )

    def mosaic_audit_settings(self, spec: MosaicSequenceSpec) -> dict[str, object]:
        """Build the complete settings preamble for a mosaic audit log."""
        # Every per-run destination is an absolute controller-home coordinate.
        tilings = [
            {
                "tiling": number,
                "absolute_beginning": {
                    "x_mm": tiling.begin_x_mm,
                    "y_mm": tiling.begin_y_mm,
                },
                "absolute_ending": {
                    "x_mm": tiling.end_x_mm,
                    "y_mm": tiling.end_y_mm,
                },
                "step_x_mm": tiling.step_x_mm,
                "step_y_mm": tiling.step_y_mm,
                "tiles_x": tiling.tiles_x,
                "tiles_y": tiling.tiles_y,
                "logical_tiles_horizontal": tiling.tiles_horizontal,
                "logical_tiles_vertical": tiling.tiles_vertical,
                "orientation": {
                    "right_axis": tiling.orientation.right_axis.value,
                    "right_sign": tiling.orientation.right_sign,
                    "down_axis": tiling.orientation.down_axis.value,
                    "down_sign": tiling.orientation.down_sign,
                },
                "absolute_z_mm": tiling.z_mm,
                "absolute_rotation_deg": tiling.rotation_deg,
            }
            for number, tiling in enumerate(spec.tilings, start=1)
        ]

        # Record configured motion rates for all axes with explicit linear/rotary units.
        components = {}
        for axis in HOME_ORDER:
            component = self.config.components[axis]
            linear = axis is not AxisName.ROTATION
            components[axis.value] = {
                "adapter": component.adapter.value,
                "enabled": component.enabled,
                "velocity": component.motion.velocity,
                "velocity_units": "mm/s" if linear else "deg/s",
                "acceleration": component.motion.acceleration,
                "acceleration_units": "mm/s^2" if linear else "deg/s^2",
            }

        # Preserve profile defaults separately from the effective per-run tiling geometry.
        configured_mosaic = self.config.mosaic
        configured_orientation = configured_mosaic.orientation
        handshake = self.config.acquisition_handshake
        return {
            "profile_name": self.config.profile_name,
            "coordinate_reference": "absolute_controller_home",
            "configured_mosaic_defaults": {
                "step_x_mm": configured_mosaic.step_x_mm,
                "step_y_mm": configured_mosaic.step_y_mm,
                "settling_time_s": configured_mosaic.settling_time_s,
                "dwell_time_s": configured_mosaic.dwell_time_s,
                "operation_timeout_s": configured_mosaic.operation_timeout_s,
                "orientation": {
                    "right_axis": configured_orientation.right_axis.value,
                    "right_sign": configured_orientation.right_sign,
                    "down_axis": configured_orientation.down_axis.value,
                    "down_sign": configured_orientation.down_sign,
                },
            },
            "safe_transition_absolute_z_mm": spec.safe_transition_z_mm,
            "resume_tiling": spec.resume_tiling,
            "resume_tile": spec.resume_tile,
            "run_tiling_two": spec.run_tiling_two,
            "total_tiles": spec.total_tiles,
            "settling_time_s": configured_mosaic.settling_time_s,
            "dwell_time_s": configured_mosaic.dwell_time_s,
            "operation_timeout_s": configured_mosaic.operation_timeout_s,
            "components": components,
            "acquisition_handshake": {
                "enabled": handshake.enabled,
                "ready_output_line": handshake.ready_output_line,
                "complete_input_line": handshake.complete_input_line,
                "acquisition_timeout_s": handshake.acquisition_timeout_s,
                "reset_timeout_s": handshake.reset_timeout_s,
                "polling_interval_ms": handshake.polling_interval_ms,
            },
            "tilings": tilings,
        }

    def _interruptible_wait(self, duration_s: float) -> None:
        """Wait while remaining responsive to pause, cancellation, and the deadline."""
        self._require_mosaic_running()
        wait_end = time.monotonic() + duration_s
        if self._mosaic_deadline is not None:
            wait_end = min(wait_end, self._mosaic_deadline)
        while (remaining := wait_end - time.monotonic()) > 0:
            # Poll briefly so Pause can freeze settling or dwell without consuming it.
            if self._mosaic_stop.wait(min(remaining, 0.05)):
                raise AdapterError("mosaic cancelled")
            paused_s = self._wait_if_mosaic_paused()
            wait_end += paused_s
        self._require_mosaic_running()

    def _require_mosaic_running(self) -> None:
        """Block at a pause boundary, then enforce cancellation and timeout."""
        if self._mosaic_stop.is_set():
            raise AdapterError("mosaic cancelled")
        self._wait_if_mosaic_paused()
        if self._mosaic_stop.is_set():
            raise AdapterError("mosaic cancelled")
        if self._mosaic_deadline is not None and time.monotonic() >= self._mosaic_deadline:
            raise AdapterError("mosaic operation timed out")

    def _wait_if_mosaic_paused(self) -> float:
        """Wait on the worker thread and exclude acknowledged pause time from timeout."""
        if not self._mosaic_pause.is_set():
            return 0.0

        # A short cancellation-aware poll keeps Cancel responsive while paused.
        paused_at = time.monotonic()
        while self._mosaic_pause.is_set():
            if self._mosaic_stop.wait(0.05):
                raise AdapterError("mosaic cancelled")
        paused_s = time.monotonic() - paused_at

        # Extend the whole-run deadline only after orchestration reaches the pause gate.
        if self._mosaic_deadline is not None:
            self._mosaic_deadline += paused_s
        return paused_s

    def _axis(self, axis: AxisName) -> MotionAxis:
        try:
            return self.axes[axis]
        except KeyError as exc:
            raise AdapterError(f"{axis.value}: no adapter configured") from exc

    def _set_pose_value(self, axis: AxisName, value: float | None) -> None:
        # Replace the immutable pose field matching fixed [x, y, z, theta] order.
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

    def begin_handshake(
        self,
        input_line: int,
        output_line: int,
        *,
        input_idle_state: bool,
        output_idle_state: bool,
        initialize_output: bool = False,
    ) -> None:
        """Establish requested idle levels and reserve Digital Out for a handshake."""
        with self._lock:
            if self._handshake_active:
                raise MotionSafetyError("a DAQ handshake is already active")

            # Confirm the output task and selected handshake line exist before any write.
            output = self.status(DigitalDirection.OUTPUT)
            if not output.connected:
                raise MotionSafetyError("digital_out must be connected for acquisition")
            if output_line not in output.states:
                raise MotionSafetyError(
                    f"digital_out line{output_line} must be configured for acquisition"
                )

            if initialize_output:
                # Never invent levels for unrelated pins in a multi-line output task.
                if any(
                    state is None
                    for line, state in output.states.items()
                    if line != output_line
                ):
                    raise MotionSafetyError(
                        "all non-handshake digital outputs must be initialized before starting"
                    )
                states = {
                    line: output_idle_state if line == output_line else bool(state)
                    for line, state in output.states.items()
                }
                self.write_outputs(states, source="handshake")
            elif output.states.get(output_line) is not output_idle_state:
                output_level = "HIGH" if output_idle_state else "LOW"
                raise MotionSafetyError(
                    f"digital_out line{output_line} must be initialized {output_level} "
                    "before starting"
                )

            # Check the peer only after the local output has reached its idle level.
            inputs = self.read_inputs()
            if inputs.get(input_line) is not input_idle_state:
                input_level = "HIGH" if input_idle_state else "LOW"
                raise MotionSafetyError(
                    f"digital_in line{input_line} must be known and {input_level} "
                    "before starting"
                )
            self._handshake_active = True

    def end_handshake(self) -> None:
        """Release exclusive output ownership after the caller restores its idle level."""
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
