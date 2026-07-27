"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil, isclose, isfinite

from acq_staging.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """Validated physical travel range reported by an adapter or simulator."""

    minimum: float
    maximum: float

    def validate(self, field: str) -> None:
        if self.minimum >= self.maximum:
            raise ConfigurationError(f"{field}: minimum must be less than maximum")

    def contains(self, value: float) -> bool:
        """Return whether a physical position lies inside this travel range."""
        return self.minimum <= value <= self.maximum

    @property
    def span(self) -> float:
        """Return the available physical travel."""
        return self.maximum - self.minimum


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
    """One controller-home destination in a two-tiling mosaic sequence."""

    x_mm: float
    y_mm: float
    z_mm: float
    theta_deg: float
    x_index: int
    y_index: int
    tiling_index: int


@dataclass(frozen=True, slots=True)
class MosaicTilingSpec:
    """One endpoint-defined XY tiling at fixed Z and rotation positions."""

    step_x_mm: float
    step_y_mm: float
    tiles_x: int
    tiles_y: int
    begin_x_mm: float
    begin_y_mm: float
    end_x_mm: float
    end_y_mm: float
    z_mm: float
    rotation_deg: float

    def __post_init__(self) -> None:
        # Reject non-finite destinations before any service can preflight motion.
        coordinates = (*self.beginning, *self.ending, self.z_mm, self.rotation_deg)
        if any(not isfinite(value) for value in coordinates):
            raise ValueError("mosaic positions must be finite")
        if any(not isfinite(step) or step <= 0 for step in self.steps):
            raise ValueError("mosaic X and Y step sizes must be finite and positive")
        if self.tiles_x <= 0 or self.tiles_y <= 0:
            raise ValueError("mosaic tile counts must be positive")
        expected = tuple(
            self._count_for_endpoints(axis, begin, end, step)
            for axis, begin, end, step in zip(
                "XY", self.beginning, self.ending, self.steps,
                strict=True,
            )
        )
        if expected != self.tile_counts:
            raise ValueError("XY tile counts do not match the configured step sizes")

    @classmethod
    def from_endpoints(
        cls,
        step_x_mm: float,
        step_y_mm: float,
        begin_x_mm: float,
        begin_y_mm: float,
        end_x_mm: float,
        end_y_mm: float,
        z_mm: float,
        rotation_deg: float,
    ) -> MosaicTilingSpec:
        """Create XY geometry with outward endpoint rounding on each axis."""
        # Convert each inclusive span into a tile count without changing its step.
        steps = (step_x_mm, step_y_mm)
        beginning = (begin_x_mm, begin_y_mm)
        requested_ending = (end_x_mm, end_y_mm)
        counts = tuple(
            cls._count_for_endpoints(axis, begin, end, step)
            for axis, begin, end, step in zip(
                "XY", beginning, requested_ending, steps,
                strict=True,
            )
        )
        # Extend each non-divisible span to the next full step in its entered direction.
        ending = tuple(
            begin
            if count == 1
            else begin + (1.0 if requested >= begin else -1.0) * (count - 1) * step
            for begin, requested, count, step in zip(
                beginning,
                requested_ending,
                counts,
                steps,
                strict=True,
            )
        )
        return cls(
            steps[0],
            steps[1],
            counts[0],
            counts[1],
            beginning[0],
            beginning[1],
            ending[0],
            ending[1],
            z_mm,
            rotation_deg,
        )

    @property
    def steps(self) -> tuple[float, float]:
        """Return positive XY step magnitudes in millimetres."""
        return (self.step_x_mm, self.step_y_mm)

    @property
    def tile_counts(self) -> tuple[int, int]:
        """Return resolved inclusive XY position counts."""
        return (self.tiles_x, self.tiles_y)

    @property
    def beginning(self) -> tuple[float, float]:
        """Return the controller-home XY beginning."""
        return (self.begin_x_mm, self.begin_y_mm)

    @property
    def ending(self) -> tuple[float, float]:
        """Return the outward-rounded controller-home XY ending."""
        return (self.end_x_mm, self.end_y_mm)

    @property
    def total_tiles(self) -> int:
        """Return the number of tile destinations in this run."""
        return self.tiles_x * self.tiles_y

    @property
    def extents_mm(self) -> tuple[float, float]:
        """Return signed XY travel from the beginning to the rounded ending."""
        return (self.end_x_mm - self.begin_x_mm, self.end_y_mm - self.begin_y_mm)

    def coordinate(self, axis_index: int, tile_index: int) -> float:
        """Resolve one controller-home coordinate from an XY grid index."""
        if tile_index < 0 or tile_index >= self.tile_counts[axis_index]:
            raise IndexError("mosaic tile index is outside the resolved grid")
        # Endpoint direction changes the increment sign while preserving its magnitude.
        begin = self.beginning[axis_index]
        end = self.ending[axis_index]
        if tile_index == self.tile_counts[axis_index] - 1:
            return end
        direction = 1.0 if end >= begin else -1.0
        return begin + tile_index * direction * self.steps[axis_index]

    @staticmethod
    def _count_for_endpoints(axis: str, begin: float, end: float, step: float) -> int:
        if not isfinite(begin) or not isfinite(end):
            raise ValueError(f"{axis} endpoints must be finite")
        if not isfinite(step) or step <= 0:
            raise ValueError(f"{axis} step must be finite and positive")
        # A zero span represents one tile while retaining its configured step.
        span = abs(end - begin)
        if isclose(span, 0.0, rel_tol=0.0, abs_tol=1e-12):
            return 1

        # Round the requested span up so every interval keeps the exact configured step.
        intervals = span / step
        rounded_up = ceil(intervals - 1e-9)
        return rounded_up + 1


@dataclass(frozen=True, slots=True)
class MosaicSequenceSpec:
    """Two tilings executed sequentially with a safe rotation-transition Z."""

    tiling_one: MosaicTilingSpec
    tiling_two: MosaicTilingSpec
    safe_transition_z_mm: float
    resume_tiling: int = 1
    resume_tile: int = 1

    def __post_init__(self) -> None:
        # The first acquisition orientation is a locked part of the run contract.
        if not isclose(self.tiling_one.rotation_deg, 0.0, abs_tol=1e-9):
            raise ValueError("tiling 1 rotation must be 0 degrees")
        if not isfinite(self.safe_transition_z_mm):
            raise ValueError("safe transition Z must be finite")
        if self.resume_tiling not in (1, 2):
            raise ValueError("resume tiling must be 1 or 2")
        active = self.tilings[self.resume_tiling - 1]
        if self.resume_tile < 1 or self.resume_tile > active.total_tiles:
            raise ValueError(
                f"resume tile must be between 1 and {active.total_tiles}"
            )

    @property
    def tilings(self) -> tuple[MosaicTilingSpec, MosaicTilingSpec]:
        """Return both tilings in their mandatory execution order."""
        return (self.tiling_one, self.tiling_two)

    @property
    def total_tiles(self) -> int:
        """Return the total destinations across both tilings."""
        return self.tiling_one.total_tiles + self.tiling_two.total_tiles


class MosaicTilePhase(StrEnum):
    """Lifecycle phase reported for a mosaic tile."""

    CURRENT = "current"
    ACQUIRING = "acquiring"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class MosaicProgress:
    """Worker-safe progress event within a two-tiling sequence."""

    tiling_number: int
    number: int
    total: int
    overall_number: int
    overall_total: int
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
