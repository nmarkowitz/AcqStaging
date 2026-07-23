"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import cast


class AxisName(StrEnum):
    """Logical motion components in required home order."""

    X = "x"
    Y = "y"
    Z = "z"
    ROTATION = "rotation"


HOME_ORDER = (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)


class AdapterType(StrEnum):
    """Supported component adapter types."""

    SIMULATED = "simulated"
    ZABER_ASCII = "zaber_ascii"
    THORLABS_BSC101 = "thorlabs_bsc101"


class DigitalDirection(StrEnum):
    """Supported digital I/O component directions."""

    INPUT = "digital_in"
    OUTPUT = "digital_out"


class DigitalAdapterType(StrEnum):
    """Supported digital I/O backends."""

    SIMULATED = "simulated"
    NIDAQMX = "nidaqmx"


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    """Identity reported for one component."""

    axis: AxisName
    adapter: AdapterType
    serial_number: str
    model: str


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    """Observable component state."""

    connected: bool
    enabled: bool
    homed: bool
    busy: bool
    position: float | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Pose:
    """Latest observed scan-head pose."""

    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None
    theta_deg: float | None = None
    timestamp: datetime | None = None

    def as_vector(self) -> tuple[float | None, float | None, float | None, float | None]:
        """Return the required [x, y, z, theta] ordering."""
        return (self.x_mm, self.y_mm, self.z_mm, self.theta_deg)

    @classmethod
    def empty(cls) -> Pose:
        """Create an unobserved pose."""
        return cls(timestamp=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """Result of a safe component diagnostic."""

    identity: ComponentIdentity
    status: ComponentStatus
    message: str


@dataclass(frozen=True, slots=True)
class MosaicTile:
    """One absolute XYZ destination in a serpentine mosaic."""

    x_mm: float
    y_mm: float
    z_mm: float
    x_index: int
    y_index: int
    z_index: int


class MosaicInputMode(StrEnum):
    """Operator-selectable ways to define one mosaic run."""

    TILE_COUNTS = "tile_counts"
    ENDPOINTS = "endpoints"


@dataclass(frozen=True, slots=True)
class MosaicRunSpec:
    """Validated transient XYZ geometry for one mosaic run."""

    input_mode: MosaicInputMode
    step_x_mm: float
    step_y_mm: float
    step_z_mm: float
    tiles_x: int
    tiles_y: int
    tiles_z: int
    begin_x_mm: float | None = None
    begin_y_mm: float | None = None
    begin_z_mm: float | None = None
    end_x_mm: float | None = None
    end_y_mm: float | None = None
    end_z_mm: float | None = None

    def __post_init__(self) -> None:
        # Reject unusable numeric geometry before it reaches service preflight.
        steps = self.steps
        if any(not isfinite(step) or step < 0 for step in steps):
            raise ValueError("mosaic step sizes must be finite and non-negative")
        if any(count <= 0 for count in self.tile_counts):
            raise ValueError("mosaic tile counts must be positive")
        for axis, step, count in zip("XYZ", steps, self.tile_counts, strict=True):
            if count > 1 and step == 0:
                raise ValueError(f"{axis} step must be positive for multiple tiles")

        # Each input mode owns one unambiguous set of origin fields.
        coordinates = (*self.beginning, *self.ending)
        if self.input_mode is MosaicInputMode.TILE_COUNTS:
            if any(value is not None for value in coordinates):
                raise ValueError("tile-count mode must use the observed current position")
            return
        if self.input_mode is not MosaicInputMode.ENDPOINTS:
            raise ValueError(f"unsupported mosaic input mode {self.input_mode!r}")
        if any(value is None or not isfinite(value) for value in coordinates):
            raise ValueError("endpoint mode requires finite beginning and end coordinates")

        # Recompute endpoint counts so direct construction cannot bypass divisibility.
        beginning = cast(tuple[float, float, float], self.beginning)
        ending = cast(tuple[float, float, float], self.ending)
        expected = tuple(
            self._count_for_endpoints(axis, begin, end, step)
            for axis, begin, end, step in zip(
                "XYZ",
                beginning,
                ending,
                steps,
                strict=True,
            )
        )
        if expected != self.tile_counts:
            raise ValueError("endpoint tile counts do not match the configured step sizes")

    @classmethod
    def from_tile_counts(
        cls,
        step_x_mm: float,
        step_y_mm: float,
        step_z_mm: float,
        tiles_x: int,
        tiles_y: int,
        tiles_z: int,
    ) -> MosaicRunSpec:
        """Create count-based geometry whose origin is observed at run time."""
        return cls(
            MosaicInputMode.TILE_COUNTS,
            step_x_mm,
            step_y_mm,
            step_z_mm,
            tiles_x,
            tiles_y,
            tiles_z,
        )

    @classmethod
    def from_endpoints(
        cls,
        step_x_mm: float,
        step_y_mm: float,
        step_z_mm: float,
        begin_x_mm: float,
        begin_y_mm: float,
        begin_z_mm: float,
        end_x_mm: float,
        end_y_mm: float,
        end_z_mm: float,
    ) -> MosaicRunSpec:
        """Create endpoint geometry with direction inferred independently per axis."""
        # Convert each inclusive span into a tile count without changing its step.
        steps = (step_x_mm, step_y_mm, step_z_mm)
        beginning = (begin_x_mm, begin_y_mm, begin_z_mm)
        ending = (end_x_mm, end_y_mm, end_z_mm)
        counts = tuple(
            cls._count_for_endpoints(axis, begin, end, step)
            for axis, begin, end, step in zip(
                "XYZ",
                beginning,
                ending,
                steps,
                strict=True,
            )
        )
        return cls(
            MosaicInputMode.ENDPOINTS,
            steps[0],
            steps[1],
            steps[2],
            counts[0],
            counts[1],
            counts[2],
            beginning[0],
            beginning[1],
            beginning[2],
            ending[0],
            ending[1],
            ending[2],
        )

    @property
    def steps(self) -> tuple[float, float, float]:
        """Return positive XYZ step magnitudes in millimetres."""
        return (self.step_x_mm, self.step_y_mm, self.step_z_mm)

    @property
    def tile_counts(self) -> tuple[int, int, int]:
        """Return resolved inclusive XYZ position counts."""
        return (self.tiles_x, self.tiles_y, self.tiles_z)

    @property
    def beginning(self) -> tuple[float | None, float | None, float | None]:
        """Return explicit endpoint-mode beginnings, or unknown count-mode origins."""
        return (self.begin_x_mm, self.begin_y_mm, self.begin_z_mm)

    @property
    def ending(self) -> tuple[float | None, float | None, float | None]:
        """Return explicit endpoint-mode endings, or unknown count-mode endpoints."""
        return (self.end_x_mm, self.end_y_mm, self.end_z_mm)

    @property
    def total_tiles(self) -> int:
        """Return the number of tile destinations in this run."""
        return self.tiles_x * self.tiles_y * self.tiles_z

    @property
    def extents_mm(self) -> tuple[float, float, float]:
        """Return signed XYZ travel from each grid beginning to its end."""
        if self.input_mode is MosaicInputMode.ENDPOINTS:
            beginning = cast(tuple[float, float, float], self.beginning)
            ending = cast(tuple[float, float, float], self.ending)
            return (
                ending[0] - beginning[0],
                ending[1] - beginning[1],
                ending[2] - beginning[2],
            )
        return (
            (self.tiles_x - 1) * self.step_x_mm,
            (self.tiles_y - 1) * self.step_y_mm,
            (self.tiles_z - 1) * self.step_z_mm,
        )

    def coordinate(self, axis_index: int, tile_index: int, observed_origin: float) -> float:
        """Resolve one absolute coordinate from a grid index and observed origin."""
        if tile_index < 0 or tile_index >= self.tile_counts[axis_index]:
            raise IndexError("mosaic tile index is outside the resolved grid")
        if self.input_mode is MosaicInputMode.TILE_COUNTS:
            return observed_origin + tile_index * self.steps[axis_index]

        # Endpoint direction changes the increment sign while preserving its magnitude.
        begin = self.beginning[axis_index]
        end = self.ending[axis_index]
        assert begin is not None and end is not None
        if tile_index == self.tile_counts[axis_index] - 1:
            return end
        direction = 1.0 if end >= begin else -1.0
        return begin + tile_index * direction * self.steps[axis_index]

    @staticmethod
    def _count_for_endpoints(axis: str, begin: float, end: float, step: float) -> int:
        if not isfinite(begin) or not isfinite(end):
            raise ValueError(f"{axis} endpoints must be finite")
        # A zero span represents one plane even when its unused step is zero.
        span = abs(end - begin)
        if isclose(span, 0.0, rel_tol=0.0, abs_tol=1e-12):
            return 1
        if not isfinite(step) or step <= 0:
            raise ValueError(f"{axis} step must be positive when endpoints differ")

        # Accept only floating-point representation noise around a whole interval count.
        intervals = span / step
        rounded = round(intervals)
        if not isclose(intervals, rounded, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"{axis} endpoint span must be an exact multiple of its step")
        return rounded + 1


class MosaicTilePhase(StrEnum):
    """Lifecycle phase reported for a mosaic tile."""

    CURRENT = "current"
    ACQUIRING = "acquiring"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class MosaicProgress:
    """Worker-safe progress event for one mosaic tile."""

    number: int
    total: int
    tile: MosaicTile
    phase: MosaicTilePhase


@dataclass(frozen=True, slots=True)
class DigitalStatus:
    """Observed digital pin states for one configured component."""

    connected: bool
    states: dict[int, bool | None]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DummyAcquirerStatus:
    """Thread-safe status snapshot for the standalone dummy acquirer."""

    listening: bool
    acquiring: bool
    requests_received: int
    acquisitions_completed: int
    error: str | None = None
