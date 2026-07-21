"""Adapter construction from validated configuration."""

from __future__ import annotations

from acq_staging.adapters.base import MotionAxis
from acq_staging.adapters.digital import (
    DigitalIODevice,
    NidaqmxDigitalIODevice,
    SimulatedDigitalIODevice,
)
from acq_staging.adapters.simulated import SimulatedMotionAxis
from acq_staging.adapters.thorlabs import ThorlabsBSC101Stage
from acq_staging.adapters.zaber import ZaberConnectionManager, ZaberLinearStage
from acq_staging.configuration import AppConfig
from acq_staging.errors import ConfigurationError
from acq_staging.models import (
    HOME_ORDER,
    AdapterType,
    AxisName,
    DigitalAdapterType,
    DigitalDirection,
)


def build_simulation_axes(config: AppConfig) -> dict[AxisName, MotionAxis]:
    """Build only simulated adapters, rejecting real hardware profiles."""
    # Refuse mixed profiles so the default GUI path cannot construct real hardware.
    axes: dict[AxisName, MotionAxis] = {}
    for axis in HOME_ORDER:
        component = config.components[axis]
        if component.adapter is not AdapterType.SIMULATED:
            raise ConfigurationError(
                f"{axis.value}: real adapter construction requires explicit hardware mode"
            )
        axes[axis] = SimulatedMotionAxis(axis, component)
    return axes


def build_axes(
    config: AppConfig,
    *,
    allow_hardware: bool = False,
) -> tuple[dict[AxisName, MotionAxis], ZaberConnectionManager | None]:
    """Build configured adapters, requiring explicit authority for real hardware."""
    # All Zaber axes share this manager so a common serial port opens only once.
    manager = (
        ZaberConnectionManager(config.connections)
        if any(
            component.adapter is AdapterType.ZABER_ASCII
            for component in config.components.values()
        )
        else None
    )
    axes: dict[AxisName, MotionAxis] = {}
    # Hardware classes may be constructed only after the caller explicitly opts in.
    for axis in HOME_ORDER:
        component = config.components[axis]
        match component.adapter:
            case AdapterType.SIMULATED:
                axes[axis] = SimulatedMotionAxis(axis, component)
            case AdapterType.ZABER_ASCII:
                if not allow_hardware or manager is None:
                    raise ConfigurationError(
                        f"{axis.value}: real adapter requires explicit hardware mode"
                    )
                axes[axis] = ZaberLinearStage(axis, component, manager)
            case AdapterType.THORLABS_BSC101:
                if not allow_hardware:
                    raise ConfigurationError(
                        f"{axis.value}: real adapter requires explicit hardware mode"
                    )
                axes[axis] = ThorlabsBSC101Stage(component)
    return axes, manager


def build_digital_devices(
    config: AppConfig,
    *,
    allow_hardware: bool = False,
) -> dict[DigitalDirection, DigitalIODevice]:
    """Build both configured digital components with explicit hardware authority."""
    # Keep NI object construction behind the same explicit hardware authority gate.
    devices: dict[DigitalDirection, DigitalIODevice] = {}
    for direction in DigitalDirection:
        component = config.digital_io[direction]
        if component.adapter is DigitalAdapterType.SIMULATED:
            devices[direction] = SimulatedDigitalIODevice(direction, component)
        elif not allow_hardware:
            raise ConfigurationError(
                f"{direction.value}: real adapter requires explicit hardware mode"
            )
        else:
            devices[direction] = NidaqmxDigitalIODevice(direction, component)
    return devices
