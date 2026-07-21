"""Versioned JSON application configuration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from acq_staging.errors import ConfigurationError
from acq_staging.models import (
    HOME_ORDER,
    AdapterType,
    AxisName,
    DigitalAdapterType,
    DigitalDirection,
)

SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """Software travel limits."""

    minimum: float
    maximum: float

    def validate(self, field: str) -> None:
        if self.minimum >= self.maximum:
            raise ConfigurationError(f"{field}: minimum must be less than maximum")

    def contains(self, value: float) -> bool:
        """Return whether a position lies inside the configured limits."""
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """Application motion parameters."""

    velocity: float
    acceleration: float
    jog_increment: float
    test_distance: float
    position_tolerance: float
    operation_timeout_s: float

    def validate(self, field: str, travel: float) -> None:
        values = asdict(self)
        for name, value in values.items():
            if value <= 0:
                raise ConfigurationError(f"{field}.{name}: must be positive")
        if self.test_distance > travel:
            raise ConfigurationError(f"{field}.test_distance: exceeds configured travel")


@dataclass(frozen=True, slots=True)
class ZaberConnectionConfig:
    """One reusable Zaber serial connection."""

    port: str
    baud_rate: int = 115200

    def validate(self, field: str) -> None:
        if not self.port.strip():
            raise ConfigurationError(f"{field}.port: must not be empty")
        if self.baud_rate <= 0:
            raise ConfigurationError(f"{field}.baud_rate: must be positive")


@dataclass(frozen=True, slots=True)
class ComponentConfig:
    """Configuration for one logical axis."""

    enabled: bool
    adapter: AdapterType
    limits: LimitsConfig
    motion: MotionConfig
    connection_id: str | None = None
    serial_number: str | None = None
    device_address: int | None = None
    axis_number: int = 1
    expected_model: str | None = None
    device_settings_name: str | None = None
    dll_directory: str | None = None
    polling_interval_ms: int = 250
    simulation_start: float = 0.0
    simulation_latency_s: float = 0.0

    def validate(
        self,
        axis: AxisName,
        connections: dict[str, ZaberConnectionConfig],
    ) -> None:
        field = f"components.{axis.value}"
        # Validate shared safety limits before checking adapter-specific fields.
        self.limits.validate(f"{field}.limits")
        self.motion.validate(f"{field}.motion", self.limits.maximum - self.limits.minimum)
        if not self.limits.contains(self.simulation_start):
            raise ConfigurationError(f"{field}.simulation_start: outside configured limits")
        if self.polling_interval_ms <= 0:
            raise ConfigurationError(f"{field}.polling_interval_ms: must be positive")
        if self.simulation_latency_s < 0:
            raise ConfigurationError(f"{field}.simulation_latency_s: must not be negative")
        if self.adapter is AdapterType.ZABER_ASCII:
            # A Zaber axis is identified by connection, controller serial, and axis number.
            if not self.connection_id or self.connection_id not in connections:
                raise ConfigurationError(f"{field}.connection_id: missing connection reference")
            if not self.serial_number:
                raise ConfigurationError(f"{field}.serial_number: required for Zaber hardware")
            if self.axis_number <= 0:
                raise ConfigurationError(f"{field}.axis_number: must be positive")
        if self.adapter is AdapterType.THORLABS_BSC101:
            # The current hardware contract permits BSC101 channel 1 only for rotation.
            if axis is not AxisName.ROTATION:
                raise ConfigurationError(f"{field}.adapter: BSC101 is only valid for rotation")
            if not self.serial_number:
                raise ConfigurationError(f"{field}.serial_number: required for Thorlabs hardware")
            if not self.dll_directory:
                raise ConfigurationError(f"{field}.dll_directory: required for Thorlabs hardware")
            if self.axis_number != 1:
                raise ConfigurationError(f"{field}.axis_number: BSC101 channel must be 1")
            if not self.device_settings_name:
                raise ConfigurationError(
                    f"{field}.device_settings_name: required for the attached stage"
                )
        if axis is AxisName.ROTATION and self.adapter is AdapterType.ZABER_ASCII:
            raise ConfigurationError(f"{field}.adapter: rotation must use simulated or Thorlabs")
        if axis is not AxisName.ROTATION and self.adapter is AdapterType.THORLABS_BSC101:
            raise ConfigurationError(f"{field}.adapter: linear axes cannot use BSC101")


@dataclass(frozen=True, slots=True)
class UiConfig:
    """GUI timing and confirmation thresholds."""

    pose_poll_interval_ms: int = 250
    large_linear_move_mm: float = 10.0
    large_rotation_move_deg: float = 30.0

    def validate(self) -> None:
        if self.pose_poll_interval_ms <= 0:
            raise ConfigurationError("ui.pose_poll_interval_ms: must be positive")
        if self.large_linear_move_mm <= 0 or self.large_rotation_move_deg <= 0:
            raise ConfigurationError("ui confirmation thresholds must be positive")


@dataclass(frozen=True, slots=True)
class MosaicConfig:
    """Motion-only serpentine mosaic parameters."""

    step_x_mm: float
    step_y_mm: float
    step_z_mm: float
    tiles_x: int
    tiles_y: int
    tiles_z: int
    settling_time_s: float = 2.0
    dwell_time_s: float = 0.0
    operation_timeout_s: float = 3600.0

    def validate(self) -> None:
        for axis, step in (("x", self.step_x_mm), ("y", self.step_y_mm), ("z", self.step_z_mm)):
            if step < 0:
                raise ConfigurationError(f"mosaic.step_{axis}_mm: must not be negative")
        for axis, count in (("x", self.tiles_x), ("y", self.tiles_y), ("z", self.tiles_z)):
            if count <= 0:
                raise ConfigurationError(f"mosaic.tiles_{axis}: must be positive")
        if self.tiles_x > 1 and self.step_x_mm == 0:
            raise ConfigurationError("mosaic.step_x_mm: must be positive for multiple tiles")
        if self.tiles_y > 1 and self.step_y_mm == 0:
            raise ConfigurationError("mosaic.step_y_mm: must be positive for multiple tiles")
        if self.tiles_z > 1 and self.step_z_mm == 0:
            raise ConfigurationError("mosaic.step_z_mm: must be positive for multiple tiles")
        if self.settling_time_s < 0 or self.dwell_time_s < 0:
            raise ConfigurationError("mosaic settling and dwell times must not be negative")
        if self.operation_timeout_s <= 0:
            raise ConfigurationError("mosaic.operation_timeout_s: must be positive")


@dataclass(frozen=True, slots=True)
class DigitalIOConfig:
    """Configuration for one digital input or output port selection."""

    enabled: bool
    adapter: DigitalAdapterType
    device: str
    port: str
    line_count: int
    lines: tuple[int, ...] | None = None
    operation_timeout_s: float = 10.0
    polling_interval_ms: int = 250

    @property
    def selected_lines(self) -> tuple[int, ...]:
        """Expand whole-port mode into the ordered pins shown by the GUI."""
        return self.lines if self.lines is not None else tuple(range(self.line_count))

    @property
    def normalized_port(self) -> str:
        """Convert user-friendly P0 notation to NI's port0 notation."""
        lowered = self.port.strip().lower()
        if lowered.startswith("port"):
            return lowered
        return f"port{lowered[1:]}" if lowered.startswith("p") else lowered

    def validate(self, direction: DigitalDirection) -> None:
        field = f"digital_io.{direction.value}"
        # Hardware profiles need an NI device name; simulations may use a label.
        if self.adapter is DigitalAdapterType.NIDAQMX and not self.device.strip():
            raise ConfigurationError(f"{field}.device: required for NI-DAQ hardware")
        port = self.normalized_port
        if not port.startswith("port") or not port[4:].isdigit():
            raise ConfigurationError(f"{field}.port: expected P0 or port0 notation")
        if self.line_count <= 0 or self.line_count > 8:
            raise ConfigurationError(f"{field}.line_count: must be between 1 and 8")
        # A null line list selects the whole declared port width.
        if self.lines is not None:
            if not self.lines:
                raise ConfigurationError(f"{field}.lines: use null for the whole port")
            if len(set(self.lines)) != len(self.lines):
                raise ConfigurationError(f"{field}.lines: duplicate pins are not allowed")
            if any(line < 0 or line >= self.line_count for line in self.lines):
                raise ConfigurationError(f"{field}.lines: pin outside configured port width")
        if self.operation_timeout_s <= 0 or self.polling_interval_ms <= 0:
            raise ConfigurationError(f"{field}: timeout and polling interval must be positive")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application profile."""

    schema_version: int
    profile_name: str
    connections: dict[str, ZaberConnectionConfig]
    components: dict[AxisName, ComponentConfig]
    ui: UiConfig
    mosaic: MosaicConfig
    digital_io: dict[DigitalDirection, DigitalIOConfig]

    def validate(self) -> None:
        # Reject unknown schema versions instead of silently misreading profiles.
        if self.schema_version != SCHEMA_VERSION:
            raise ConfigurationError(
                f"schema_version: expected {SCHEMA_VERSION}, got {self.schema_version}"
            )
        if not self.profile_name.strip():
            raise ConfigurationError("profile_name: must not be empty")
        for name, connection in self.connections.items():
            connection.validate(f"connections.{name}")
        if set(self.components) != set(HOME_ORDER):
            raise ConfigurationError("components: x, y, z, and rotation are all required")
        assignments: set[tuple[str, str, int]] = set()
        # Validate in motion order and prevent two logical axes claiming one Zaber axis.
        for axis in HOME_ORDER:
            component = self.components[axis]
            component.validate(axis, self.connections)
            if component.enabled and component.adapter is AdapterType.ZABER_ASCII:
                assignment = (
                    component.connection_id or "",
                    component.serial_number or "",
                    component.axis_number,
                )
                if assignment in assignments:
                    raise ConfigurationError(
                        f"components.{axis.value}: duplicate device assignment"
                    )
                assignments.add(assignment)
        self.ui.validate()
        self.mosaic.validate()
        if set(self.digital_io) != set(DigitalDirection):
            raise ConfigurationError("digital_io: digital_in and digital_out are required")
        for direction in DigitalDirection:
            self.digital_io[direction].validate(direction)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "connections": {name: asdict(value) for name, value in self.connections.items()},
            "components": {
                axis.value: {
                    **asdict(component),
                    "adapter": component.adapter.value,
                }
                for axis, component in self.components.items()
            },
            "ui": asdict(self.ui),
            "mosaic": asdict(self.mosaic),
            "digital_io": {
                direction.value: {
                    **asdict(component),
                    "adapter": component.adapter.value,
                }
                for direction, component in self.digital_io.items()
            },
        }


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field}: expected an object")
    return value


def _parse_component(raw: object, field: str) -> ComponentConfig:
    # Construct nested typed records before passing the remaining flat fields through.
    data = _mapping(raw, field)
    try:
        limits = LimitsConfig(**_mapping(data["limits"], f"{field}.limits"))
        motion = MotionConfig(**_mapping(data["motion"], f"{field}.motion"))
        remaining = {key: value for key, value in data.items() if key not in {"limits", "motion"}}
        remaining["adapter"] = AdapterType(remaining["adapter"])
        if remaining.get("serial_number") is not None:
            remaining["serial_number"] = str(remaining["serial_number"])
        return ComponentConfig(limits=limits, motion=motion, **remaining)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field}: {exc}") from exc


def _parse_digital_line(value: object, field: str) -> int:
    # Accept both compact integers and NI-style strings such as line2.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower().startswith("line"):
        suffix = value.strip().lower()[4:]
        if suffix.isdigit():
            return int(suffix)
    raise ConfigurationError(f"{field}: expected an integer or lineN string")


def _parse_digital(raw: object, field: str) -> DigitalIOConfig:
    data = dict(_mapping(raw, field))
    try:
        # Normalize external JSON values before constructing the typed record.
        data["adapter"] = DigitalAdapterType(data["adapter"])
        if data.get("lines") is not None:
            if not isinstance(data["lines"], (list, tuple)):
                raise ConfigurationError(f"{field}.lines: expected a list or null")
            data["lines"] = tuple(
                _parse_digital_line(value, f"{field}.lines") for value in data["lines"]
            )
        return DigitalIOConfig(**data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field}: {exc}") from exc


def config_from_dict(raw: object) -> AppConfig:
    """Parse and validate a configuration dictionary."""
    data = _mapping(raw, "configuration")
    # Convert external strings and mappings into the application's typed model.
    try:
        connections = {
            name: ZaberConnectionConfig(**_mapping(value, f"connections.{name}"))
            for name, value in _mapping(data.get("connections", {}), "connections").items()
        }
        components_raw = _mapping(data["components"], "components")
        components = {
            AxisName(name): _parse_component(value, f"components.{name}")
            for name, value in components_raw.items()
        }
        digital_io = {
            DigitalDirection(name): _parse_digital(value, f"digital_io.{name}")
            for name, value in _mapping(data["digital_io"], "digital_io").items()
        }
        config = AppConfig(
            schema_version=int(data["schema_version"]),
            profile_name=str(data["profile_name"]),
            connections=connections,
            components=components,
            ui=UiConfig(**_mapping(data.get("ui", {}), "ui")),
            mosaic=MosaicConfig(**_mapping(data["mosaic"], "mosaic")),
            digital_io=digital_io,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    config.validate()
    return config


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a JSON profile."""
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{config_path}: {exc}") from exc
    return config_from_dict(raw)


def save_config(config: AppConfig, path: str | Path) -> None:
    """Atomically save a validated JSON profile."""
    config.validate()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the destination so os.replace remains an atomic filesystem operation.
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(config.to_dict(), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"{destination}: {exc}") from exc


def default_simulation_config() -> AppConfig:
    """Return the safe built-in simulation profile."""
    # This built-in profile guarantees startup never constructs a hardware adapter.
    linear_motion = MotionConfig(10.0, 50.0, 1.0, 1.0, 0.01, 30.0)
    rotation_motion = MotionConfig(10.0, 20.0, 5.0, 5.0, 0.05, 60.0)
    config = AppConfig(
        schema_version=SCHEMA_VERSION,
        profile_name="Default Simulation",
        connections={},
        components={
            AxisName.X: ComponentConfig(
                True, AdapterType.SIMULATED, LimitsConfig(0.0, 300.0), linear_motion
            ),
            AxisName.Y: ComponentConfig(
                True, AdapterType.SIMULATED, LimitsConfig(0.0, 300.0), linear_motion
            ),
            AxisName.Z: ComponentConfig(
                True, AdapterType.SIMULATED, LimitsConfig(0.0, 100.0), linear_motion
            ),
            AxisName.ROTATION: ComponentConfig(
                True,
                AdapterType.SIMULATED,
                LimitsConfig(0.0, 360.0),
                rotation_motion,
            ),
        },
        ui=UiConfig(),
        mosaic=MosaicConfig(2.8, 2.8, 0.001, 7, 5, 1, 2.0, 0.0, 3600.0),
        digital_io={
            DigitalDirection.INPUT: DigitalIOConfig(
                True, DigitalAdapterType.SIMULATED, "SimDAQ", "P0", 8
            ),
            DigitalDirection.OUTPUT: DigitalIOConfig(
                True,
                DigitalAdapterType.SIMULATED,
                "SimDAQ",
                "P1",
                8,
                (0, 1, 2, 3),
            ),
        },
    )
    config.validate()
    return config
