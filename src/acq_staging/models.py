"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


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


@dataclass(frozen=True, slots=True)
class DigitalStatus:
    """Observed digital pin states for one configured component."""

    connected: bool
    states: dict[int, bool | None]
    error: str | None = None
