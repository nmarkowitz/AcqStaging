"""Zaber Motion Library adapter for linear stages."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from zaber_motion import Units
from zaber_motion.ascii import Connection

from acq_staging.configuration import (
    ComponentConfig,
    ZaberConnectionConfig,
)
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import (
    AdapterType,
    AxisName,
    ComponentIdentity,
    ComponentStatus,
)


class ZaberConnectionManager:
    """Own and reuse Zaber serial connections."""

    def __init__(
        self,
        configs: dict[str, ZaberConnectionConfig],
        connection_factory: Any = Connection.open_serial_port,
    ) -> None:
        self.configs = configs
        self.connection_factory = connection_factory
        self._connections: dict[str, Any] = {}
        self._devices: dict[str, list[Any]] = {}
        self._users: dict[str, int] = {}
        self._command_locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()

    def acquire(self, connection_id: str) -> list[Any]:
        """Open/detect once and increment the connection user count."""
        with self._lock:
            if connection_id not in self.configs:
                raise AdapterError(f"unknown Zaber connection {connection_id}")
            # Detect once per port because X, Y, and Z may share one daisy chain.
            if connection_id not in self._connections:
                config = self.configs[connection_id]
                connection = self.connection_factory(config.port, config.baud_rate)
                try:
                    devices = list(connection.detect_devices())
                except Exception:
                    connection.close()
                    raise
                self._connections[connection_id] = connection
                self._devices[connection_id] = devices
                self._users[connection_id] = 0
                self._command_locks[connection_id] = threading.RLock()
            self._users[connection_id] += 1
            return self._devices[connection_id]

    def release(self, connection_id: str) -> None:
        """Close a connection when its final adapter disconnects."""
        with self._lock:
            if connection_id not in self._connections:
                return
            self._users[connection_id] -= 1
            if self._users[connection_id] <= 0:
                self._connections.pop(connection_id).close()
                self._devices.pop(connection_id, None)
                self._users.pop(connection_id, None)
                self._command_locks.pop(connection_id, None)

    @contextmanager
    def command(self, connection_id: str) -> Iterator[None]:
        """Serialize one vendor call on a shared serial connection."""
        # Copy the per-port lock while holding the manager's bookkeeping lock.
        with self._lock:
            command_lock = self._command_locks.get(connection_id)
        if command_lock is None:
            raise AdapterError(f"Zaber connection {connection_id} is not open")
        with command_lock:
            yield

    def close_all(self) -> None:
        """Close every managed connection."""
        with self._lock:
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()
            self._devices.clear()
            self._users.clear()
            self._command_locks.clear()


class ZaberLinearStage:
    """One logical Zaber linear axis."""

    def __init__(
        self,
        axis_name: AxisName,
        config: ComponentConfig,
        manager: ZaberConnectionManager,
    ) -> None:
        self.axis_name = axis_name
        self.config = config
        self.manager = manager
        self._device: Any | None = None
        self._axis: Any | None = None
        self._device_limits: tuple[float, float] | None = None
        self._error: str | None = None

    def connect(self) -> None:
        if self._axis is not None:
            return
        # Acquiring the port discovers devices but does not home or move an axis.
        connection_id = self._connection_id
        devices = self.manager.acquire(connection_id)
        try:
            serial = int(self.config.serial_number or "")
            # Serial identity prevents logical axes from binding by discovery order.
            matches = [device for device in devices if int(device.serial_number) == serial]
            if len(matches) != 1:
                raise AdapterError(
                    f"{self.axis_name.value}: expected one device with serial {serial}, "
                    f"found {len(matches)}"
                )
            device = matches[0]
            if (
                self.config.device_address is not None
                and int(device.device_address) != self.config.device_address
            ):
                raise AdapterError(f"{self.axis_name.value}: device address mismatch")
            axis = device.get_axis(self.config.axis_number)
            model = str(device.name)
            if (
                self.config.expected_model
                and self.config.expected_model.lower() not in model.lower()
            ):
                raise AdapterError(
                    f"{self.axis_name.value}: expected model "
                    f"{self.config.expected_model}, got {model}"
                )
            # Device-reported limits bound the stricter application software limits.
            device_minimum = float(
                axis.settings.get("limit.min", Units.LENGTH_MILLIMETRES)
            )
            device_maximum = float(
                axis.settings.get("limit.max", Units.LENGTH_MILLIMETRES)
            )
            if device_minimum >= device_maximum:
                raise AdapterError(
                    f"{self.axis_name.value}: invalid device limits "
                    f"[{device_minimum}, {device_maximum}] mm"
                )
            if (
                self.config.limits.minimum < device_minimum
                or self.config.limits.maximum > device_maximum
            ):
                raise AdapterError(
                    f"{self.axis_name.value}: configured limits "
                    f"[{self.config.limits.minimum}, {self.config.limits.maximum}] mm "
                    f"exceed device limits [{device_minimum}, {device_maximum}] mm"
                )
            self._device = device
            self._axis = axis
            self._device_limits = (device_minimum, device_maximum)
            self._error = None
        except Exception:
            # Undo the shared-port reference count after any partial setup failure.
            self.manager.release(connection_id)
            raise

    def disconnect(self) -> None:
        if self._axis is None:
            return
        self._axis = None
        self._device = None
        self._device_limits = None
        self.manager.release(self._connection_id)

    def identity(self) -> ComponentIdentity:
        device = self._require_device()
        return ComponentIdentity(
            self.axis_name,
            AdapterType.ZABER_ASCII,
            str(device.serial_number),
            str(device.name),
        )

    def identify(self) -> None:
        """Ask the connected controller to identify itself visually."""
        device = self._require_device()
        with self.manager.command(self._connection_id):
            device.identify()

    def status(self) -> ComponentStatus:
        if self._axis is None:
            return ComponentStatus(False, False, False, False, None, self._error)
        try:
            with self.manager.command(self._connection_id):
                homed = bool(self._axis.is_homed())
                busy = bool(self._axis.is_busy())
                position = float(
                    self._axis.get_position(Units.LENGTH_MILLIMETRES)
                )
            return ComponentStatus(
                connected=True,
                enabled=True,
                homed=homed,
                busy=busy,
                position=position,
                error=self._error,
            )
        except Exception as exc:
            self._error = str(exc)
            return ComponentStatus(True, True, False, False, None, self._error)

    def home(self) -> None:
        axis = self._require_axis()
        # Start nonblocking vendor motion, then monitor it outside the GUI thread.
        with self.manager.command(self._connection_id):
            axis.home(wait_until_idle=False)
        self._wait_until_idle()

    def stop(self) -> None:
        if self._axis is not None:
            with self.manager.command(self._connection_id):
                self._axis.stop(wait_until_idle=False)

    def move_absolute(self, destination: float) -> None:
        # Check application limits before issuing any vendor movement command.
        self._validate_destination(destination)
        axis = self._require_axis()
        with self.manager.command(self._connection_id):
            homed = axis.is_homed()
        if not homed:
            raise MotionSafetyError(f"{self.axis_name.value}: must be homed before movement")
        motion = self.config.motion
        with self.manager.command(self._connection_id):
            axis.move_absolute(
                destination,
                Units.LENGTH_MILLIMETRES,
                wait_until_idle=False,
                velocity=motion.velocity,
                velocity_unit=Units.VELOCITY_MILLIMETRES_PER_SECOND,
                acceleration=motion.acceleration,
                acceleration_unit=Units.ACCELERATION_MILLIMETRES_PER_SECOND_SQUARED,
            )
        self._wait_until_idle()

    def move_relative(self, displacement: float) -> None:
        self.move_absolute(self.get_position() + displacement)

    def get_position(self) -> float:
        with self.manager.command(self._connection_id):
            return float(self._require_axis().get_position(Units.LENGTH_MILLIMETRES))

    @property
    def _connection_id(self) -> str:
        if not self.config.connection_id:
            raise AdapterError(f"{self.axis_name.value}: missing connection ID")
        return self.config.connection_id

    def _require_axis(self) -> Any:
        if self._axis is None:
            raise AdapterError(f"{self.axis_name.value}: not connected")
        return self._axis

    def _require_device(self) -> Any:
        if self._device is None:
            raise AdapterError(f"{self.axis_name.value}: not connected")
        return self._device

    def _validate_destination(self, destination: float) -> None:
        if not self.config.limits.contains(destination):
            raise MotionSafetyError(
                f"{self.axis_name.value}: destination outside software limits"
            )

    def _wait_until_idle(self) -> None:
        # Poll explicitly so application timeouts can stop an uncompleted operation.
        axis = self._require_axis()
        deadline = time.monotonic() + self.config.motion.operation_timeout_s
        while True:
            with self.manager.command(self._connection_id):
                busy = axis.is_busy()
            if not busy:
                return
            if time.monotonic() >= deadline:
                # Do not attempt recovery motion when the final position is unknown.
                with self.manager.command(self._connection_id):
                    axis.stop(wait_until_idle=False)
                raise AdapterError(f"{self.axis_name.value}: operation timed out")
            time.sleep(0.05)
