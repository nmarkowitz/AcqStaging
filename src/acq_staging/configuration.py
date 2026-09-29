"""Versioned JSON application configuration."""

from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from acq_staging.errors import ConfigurationError
from acq_staging.models import (
    HOME_ORDER,
    AdapterType,
    AxisName,
    DigitalAdapterType,
    DigitalDirection,
    MosaicOrientation,
    StagePreset,
)
from acq_staging.models import (
    LimitsConfig as LimitsConfig,
)

SCHEMA_VERSION = 7
DUMMY_ACQUIRER_SCHEMA_VERSION = 1
STAGE_PRESETS_SCHEMA_VERSION = 1
DEFAULT_STAGE_PRESETS_PATH = Path(__file__).resolve().parents[2] / "configs" / "stage_presets.json"


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """Application motion parameters."""

    velocity: float
    acceleration: float
    jog_increment: float
    test_distance: float
    position_tolerance: float
    operation_timeout_s: float

    def validate(self, field: str, travel: float | None) -> None:
        values = asdict(self)
        for name, value in values.items():
            if value <= 0:
                raise ConfigurationError(f"{field}.{name}: must be positive")
        # Simulators have profile-defined travel; hardware travel is queried on connect.
        if travel is not None and self.test_distance > travel:
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
    limits: LimitsConfig | None
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
        # Only simulators define application travel; hardware delegates limits to vendors.
        if self.adapter is AdapterType.SIMULATED:
            if self.limits is None:
                raise ConfigurationError(f"{field}.limits: required for simulation")
            self.limits.validate(f"{field}.limits")
            self.motion.validate(f"{field}.motion", self.limits.span)
            if not self.limits.contains(self.simulation_start):
                raise ConfigurationError(f"{field}.simulation_start: outside configured limits")
        else:
            self.motion.validate(f"{field}.motion", None)
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
    """Default XY spacing and full-sequence movement timing parameters."""

    step_x_mm: float
    step_y_mm: float
    settling_time_s: float = 2.0
    dwell_time_s: float = 0.0
    operation_timeout_s: float = 3600.0
    orientation: MosaicOrientation = field(default_factory=MosaicOrientation)

    def validate(self) -> None:
        # XY defaults must be usable when the setup dialog starts with distinct endpoints.
        if self.step_x_mm <= 0 or self.step_y_mm <= 0:
            raise ConfigurationError("mosaic X and Y steps must be positive")
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
class AcquisitionHandshakeConfig:
    """Movement-side DAQ handshake settings for tile acquisition."""

    enabled: bool = False
    ready_output_line: int = 0
    complete_input_line: int = 0
    acquisition_timeout_s: float = 300.0
    reset_timeout_s: float = 30.0
    polling_interval_ms: int = 20

    def validate(self, digital_io: dict[DigitalDirection, DigitalIOConfig]) -> None:
        # Positive timing remains required even while the optional handshake is disabled.
        if self.acquisition_timeout_s <= 0 or self.reset_timeout_s <= 0:
            raise ConfigurationError("acquisition_handshake: timeouts must be positive")
        if self.polling_interval_ms <= 0:
            raise ConfigurationError("acquisition_handshake.polling_interval_ms: must be positive")
        if not self.enabled:
            return
        # Enabled handshake lines must be owned by their matching configured DAQ direction.
        if self.ready_output_line not in digital_io[DigitalDirection.OUTPUT].selected_lines:
            raise ConfigurationError(
                "acquisition_handshake.ready_output_line: not selected by digital_out"
            )
        if self.complete_input_line not in digital_io[DigitalDirection.INPUT].selected_lines:
            raise ConfigurationError(
                "acquisition_handshake.complete_input_line: not selected by digital_in"
            )


@dataclass(frozen=True, slots=True)
class DummyAcquirerHandshakeConfig:
    """Dummy acquisition timing and DAQ line assignments."""

    ready_input_line: int
    complete_output_line: int
    acquisition_duration_s: float = 1.0
    reset_timeout_s: float = 30.0
    polling_interval_ms: int = 20

    def validate(self, digital_io: dict[DigitalDirection, DigitalIOConfig]) -> None:
        # The dummy accepts an immediate acquisition but all wait controls must be positive.
        if self.acquisition_duration_s < 0:
            raise ConfigurationError("handshake.acquisition_duration_s: must not be negative")
        if self.reset_timeout_s <= 0 or self.polling_interval_ms <= 0:
            raise ConfigurationError(
                "handshake: reset timeout and polling interval must be positive"
            )
        if self.ready_input_line not in digital_io[DigitalDirection.INPUT].selected_lines:
            raise ConfigurationError("handshake.ready_input_line: not selected by digital_in")
        if self.complete_output_line not in digital_io[DigitalDirection.OUTPUT].selected_lines:
            raise ConfigurationError("handshake.complete_output_line: not selected by digital_out")


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
    acquisition_handshake: AcquisitionHandshakeConfig

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
        self.acquisition_handshake.validate(self.digital_io)

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
            "mosaic": {
                **asdict(self.mosaic),
                "orientation": {
                    "right_axis": self.mosaic.orientation.right_axis.value,
                    "right_sign": self.mosaic.orientation.right_sign,
                    "down_axis": self.mosaic.orientation.down_axis.value,
                    "down_sign": self.mosaic.orientation.down_sign,
                },
            },
            "digital_io": {
                direction.value: {
                    **asdict(component),
                    "adapter": component.adapter.value,
                }
                for direction, component in self.digital_io.items()
            },
            "acquisition_handshake": asdict(self.acquisition_handshake),
        }


@dataclass(frozen=True, slots=True)
class DummyAcquirerConfig:
    """Complete standalone dummy-acquirer profile."""

    schema_version: int
    profile_name: str
    digital_io: dict[DigitalDirection, DigitalIOConfig]
    handshake: DummyAcquirerHandshakeConfig

    def validate(self) -> None:
        # Reject incompatible dummy profiles before constructing any DAQ adapter.
        if self.schema_version != DUMMY_ACQUIRER_SCHEMA_VERSION:
            raise ConfigurationError(
                "schema_version: expected "
                f"{DUMMY_ACQUIRER_SCHEMA_VERSION}, got {self.schema_version}"
            )
        if not self.profile_name.strip():
            raise ConfigurationError("profile_name: must not be empty")
        if set(self.digital_io) != set(DigitalDirection):
            raise ConfigurationError("digital_io: digital_in and digital_out are required")
        for direction in DigitalDirection:
            self.digital_io[direction].validate(direction)
        self.handshake.validate(self.digital_io)

    def to_dict(self) -> dict[str, Any]:
        """Convert the dummy profile to a JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "digital_io": {
                direction.value: {
                    **asdict(component),
                    "adapter": component.adapter.value,
                }
                for direction, component in self.digital_io.items()
            },
            "handshake": asdict(self.handshake),
        }


@dataclass(frozen=True, slots=True)
class StagePresetsConfig:
    """Imaging and slicing poses plus the ordered axis moves between them."""

    schema_version: int
    imaging_order: tuple[AxisName, ...]
    positions: dict[StagePreset, dict[AxisName, float]]

    def validate(self) -> None:
        if self.schema_version != STAGE_PRESETS_SCHEMA_VERSION:
            raise ConfigurationError(
                "schema_version: expected "
                f"{STAGE_PRESETS_SCHEMA_VERSION}, got {self.schema_version}"
            )
        if not self.imaging_order:
            raise ConfigurationError("imaging_order: must list at least one axis")
        if len(set(self.imaging_order)) != len(self.imaging_order):
            raise ConfigurationError("imaging_order: each axis may appear only once")
        if set(self.positions) != set(StagePreset):
            raise ConfigurationError("positions: imaging and slicing are both required")
        # Every preset must name exactly the ordered axes so no move is implied or ignored.
        for preset, targets in self.positions.items():
            if set(targets) != set(self.imaging_order):
                raise ConfigurationError(
                    f"positions.{preset.value}: must define exactly the axes in imaging_order"
                )
            for axis, value in targets.items():
                if not math.isfinite(value):
                    raise ConfigurationError(
                        f"positions.{preset.value}.{axis.value}: must be finite"
                    )

    def order(self, preset: StagePreset) -> tuple[AxisName, ...]:
        """Return the move order; slicing reverses the imaging order."""
        if preset is StagePreset.IMAGING:
            return self.imaging_order
        return tuple(reversed(self.imaging_order))

    def moves(self, preset: StagePreset) -> tuple[tuple[AxisName, float], ...]:
        """Return ordered absolute controller-home destinations for one preset."""
        targets = self.positions[preset]
        return tuple((axis, targets[axis]) for axis in self.order(preset))

    def to_dict(self) -> dict[str, Any]:
        """Convert the presets to a JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "imaging_order": [axis.value for axis in self.imaging_order],
            "positions": {
                preset.value: {axis.value: value for axis, value in targets.items()}
                for preset, targets in self.positions.items()
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
        limits_data = data.get("limits")
        limits = (
            None
            if limits_data is None
            else LimitsConfig(**_mapping(limits_data, f"{field}.limits"))
        )
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


def _parse_digital_io(raw: object) -> dict[DigitalDirection, DigitalIOConfig]:
    # Share the exact DAQ profile parser between the movement and dummy applications.
    return {
        DigitalDirection(name): _parse_digital(value, f"digital_io.{name}")
        for name, value in _mapping(raw, "digital_io").items()
    }


def _parse_handshake_line(value: object, field: str) -> int:
    # Handshake assignments accept the same integer or lineN syntax as DAQ selections.
    return _parse_digital_line(value, field)


def _parse_orientation_sign(value: object, field: str) -> int:
    """Accept only an integer -1 or 1 for a signed profile direction."""
    # Reject booleans and fractional numbers instead of silently coercing malformed JSON.
    if isinstance(value, int) and not isinstance(value, bool) and value in (-1, 1):
        return value
    raise ValueError(f"{field}: expected integer -1 or 1")


def config_from_dict(raw: object) -> AppConfig:
    """Parse and validate a configuration dictionary."""
    data = deepcopy(_mapping(raw, "configuration"))
    # Upgrade schema-3 profiles in memory without changing the source file.
    if data.get("schema_version") == 3:
        data["schema_version"] = 4
        data["acquisition_handshake"] = asdict(AcquisitionHandshakeConfig())
    # Schema 5 removes user-authored travel limits from physical hardware entries.
    if data.get("schema_version") == 4:
        data["schema_version"] = 5
        for component in _mapping(data.get("components", {}), "components").values():
            component_data = _mapping(component, "components entry")
            if component_data.get("adapter") != AdapterType.SIMULATED.value:
                component_data["limits"] = None
    # Schema 6 replaces XYZ count defaults with two transient endpoint-defined XY tilings.
    if data.get("schema_version") == 5:
        data["schema_version"] = 6
        mosaic = _mapping(data.get("mosaic", {}), "mosaic")
        for obsolete in ("step_z_mm", "tiles_x", "tiles_y", "tiles_z"):
            mosaic.pop(obsolete, None)
    # Schema 7 makes physical right/down directions explicit while preserving old +X/+Y order.
    if data.get("schema_version") == 6:
        data["schema_version"] = SCHEMA_VERSION
        mosaic = _mapping(data.get("mosaic", {}), "mosaic")
        mosaic["orientation"] = {
            "right_axis": AxisName.X.value,
            "right_sign": 1,
            "down_axis": AxisName.Y.value,
            "down_sign": 1,
        }
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
        digital_io = _parse_digital_io(data["digital_io"])
        handshake_data = dict(
            _mapping(data["acquisition_handshake"], "acquisition_handshake")
        )
        handshake_data["ready_output_line"] = _parse_handshake_line(
            handshake_data["ready_output_line"],
            "acquisition_handshake.ready_output_line",
        )
        handshake_data["complete_input_line"] = _parse_handshake_line(
            handshake_data["complete_input_line"],
            "acquisition_handshake.complete_input_line",
        )
        mosaic_data = dict(_mapping(data["mosaic"], "mosaic"))
        orientation_data = dict(
            _mapping(mosaic_data.pop("orientation"), "mosaic.orientation")
        )
        orientation = MosaicOrientation(
            right_axis=AxisName(orientation_data["right_axis"]),
            right_sign=_parse_orientation_sign(
                orientation_data["right_sign"], "mosaic.orientation.right_sign"
            ),
            down_axis=AxisName(orientation_data["down_axis"]),
            down_sign=_parse_orientation_sign(
                orientation_data["down_sign"], "mosaic.orientation.down_sign"
            ),
        )
        config = AppConfig(
            schema_version=int(data["schema_version"]),
            profile_name=str(data["profile_name"]),
            connections=connections,
            components=components,
            ui=UiConfig(**_mapping(data.get("ui", {}), "ui")),
            mosaic=MosaicConfig(orientation=orientation, **mosaic_data),
            digital_io=digital_io,
            acquisition_handshake=AcquisitionHandshakeConfig(**handshake_data),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    config.validate()
    return config


def dummy_acquirer_config_from_dict(raw: object) -> DummyAcquirerConfig:
    """Parse and validate a standalone dummy-acquirer profile."""
    data = _mapping(raw, "configuration")
    # Build the shared DAQ records before validating dummy-specific handshake fields.
    try:
        digital_io = _parse_digital_io(data["digital_io"])
        handshake_data = dict(_mapping(data["handshake"], "handshake"))
        handshake_data["ready_input_line"] = _parse_handshake_line(
            handshake_data["ready_input_line"], "handshake.ready_input_line"
        )
        handshake_data["complete_output_line"] = _parse_handshake_line(
            handshake_data["complete_output_line"], "handshake.complete_output_line"
        )
        config = DummyAcquirerConfig(
            schema_version=int(data["schema_version"]),
            profile_name=str(data["profile_name"]),
            digital_io=digital_io,
            handshake=DummyAcquirerHandshakeConfig(**handshake_data),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    config.validate()
    return config


def _parse_preset_position(value: object, field: str) -> float:
    # Reject booleans and strings rather than coercing them into a motion target.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ConfigurationError(f"{field}: expected a number")


def stage_presets_from_dict(raw: object) -> StagePresetsConfig:
    """Parse and validate an imaging/slicing preset dictionary."""
    data = _mapping(raw, "stage presets")
    try:
        order_raw = data["imaging_order"]
        if not isinstance(order_raw, list):
            raise ConfigurationError("imaging_order: expected a list of axis names")
        positions = {
            StagePreset(name): {
                AxisName(axis): _parse_preset_position(value, f"positions.{name}.{axis}")
                for axis, value in _mapping(targets, f"positions.{name}").items()
            }
            for name, targets in _mapping(data["positions"], "positions").items()
        }
        config = StagePresetsConfig(
            schema_version=int(data["schema_version"]),
            imaging_order=tuple(AxisName(axis) for axis in order_raw),
            positions=positions,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    config.validate()
    return config


def load_stage_presets(path: str | Path = DEFAULT_STAGE_PRESETS_PATH) -> StagePresetsConfig:
    """Load and validate the imaging/slicing preset file."""
    presets_path = Path(path)
    try:
        raw = json.loads(presets_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{presets_path}: {exc}") from exc
    return stage_presets_from_dict(raw)


def save_stage_presets(config: StagePresetsConfig, path: str | Path) -> None:
    """Atomically save a validated imaging/slicing preset file."""
    config.validate()
    _atomic_save(config.to_dict(), path)


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a JSON profile."""
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{config_path}: {exc}") from exc
    return config_from_dict(raw)


def load_dummy_acquirer_config(path: str | Path) -> DummyAcquirerConfig:
    """Load and validate a standalone dummy-acquirer profile."""
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{config_path}: {exc}") from exc
    return dummy_acquirer_config_from_dict(raw)


def save_config(config: AppConfig, path: str | Path) -> None:
    """Atomically save a validated JSON profile."""
    config.validate()
    _atomic_save(config.to_dict(), path)


def save_dummy_acquirer_config(config: DummyAcquirerConfig, path: str | Path) -> None:
    """Atomically save a validated standalone dummy-acquirer profile."""
    config.validate()
    _atomic_save(config.to_dict(), path)


def _atomic_save(data: dict[str, Any], path: str | Path) -> None:
    # Both applications use the same replace-on-success persistence guarantee.
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
            json.dump(data, stream, indent=2)
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
        mosaic=MosaicConfig(2.8, 2.8, 2.0, 0.0, 3600.0),
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
        acquisition_handshake=AcquisitionHandshakeConfig(),
    )
    config.validate()
    return config


def default_dummy_acquirer_config() -> DummyAcquirerConfig:
    """Return the safe disconnected simulation profile for the dummy acquirer."""
    # Separate simulated ports mirror the two physical wire directions without I/O.
    config = DummyAcquirerConfig(
        schema_version=DUMMY_ACQUIRER_SCHEMA_VERSION,
        profile_name="Dummy Acquirer Simulation",
        digital_io={
            DigitalDirection.INPUT: DigitalIOConfig(
                True, DigitalAdapterType.SIMULATED, "SimDAQ", "P2", 1, (0,)
            ),
            DigitalDirection.OUTPUT: DigitalIOConfig(
                True, DigitalAdapterType.SIMULATED, "SimDAQ", "P3", 1, (0,)
            ),
        },
        handshake=DummyAcquirerHandshakeConfig(0, 0),
    )
    config.validate()
    return config
