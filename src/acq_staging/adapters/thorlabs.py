"""Thorlabs Kinesis BSC101 benchtop stepper-controller adapter."""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from acq_staging.configuration import ComponentConfig
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import (
    AdapterType,
    AxisName,
    ComponentIdentity,
    ComponentStatus,
    LimitsConfig,
)


class KinesisFacade:
    """Lazy pythonnet boundary for the installed Kinesis assemblies."""

    def __init__(self, dll_directory: str) -> None:
        self.dll_directory = Path(dll_directory)
        self._loaded = False
        self.DeviceManagerCLI: Any = None
        self.BenchtopStepperMotor: Any = None
        self.Decimal: Any = None
        self.UInt64: Any = None
        self.Action: Any = None
        self.load()

    def load(self) -> None:
        """Load verified assemblies and types only when hardware is requested."""
        if self._loaded:
            return
        # Import pythonnet here so simulation and test discovery do not require Kinesis.
        import clr

        # Load the dependency assemblies before importing their managed types.
        required = (
            "Thorlabs.MotionControl.DeviceManagerCLI.dll",
            "Thorlabs.MotionControl.GenericMotorCLI.dll",
            "Thorlabs.MotionControl.Benchtop.StepperMotorCLI.dll",
        )
        for name in required:
            path = self.dll_directory / name
            if not path.exists():
                raise AdapterError(f"missing Kinesis DLL: {path}")
            clr.AddReference(str(path))
        from System import Action, Decimal, UInt64
        from Thorlabs.MotionControl.Benchtop.StepperMotorCLI import (
            BenchtopStepperMotor,
        )
        from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI

        self.DeviceManagerCLI = DeviceManagerCLI
        self.BenchtopStepperMotor = BenchtopStepperMotor
        self.Decimal = Decimal
        self.UInt64 = UInt64
        self.Action = Action
        self._loaded = True

    def decimal(self, value: float) -> Any:
        return self.Decimal(value)

    def dec2float(self, value: Any) -> float:
        return float(self.Decimal.ToDouble(value))

    def callback(self, function: Any) -> Any:
        return self.Action[self.UInt64](function)

    def settle(self) -> None:
        """Allow Kinesis time to exchange settings after lifecycle calls."""
        time.sleep(0.25)


class ThorlabsBSC101Stage:
    """Rotation stage driven through channel 1 of a Kinesis BSC101."""

    def __init__(
        self,
        config: ComponentConfig,
        facade: KinesisFacade | Any | None = None,
    ) -> None:
        self.axis_name = AxisName.ROTATION
        self.config = config
        self.facade = facade or KinesisFacade(config.dll_directory or "")
        self._controller: Any | None = None
        self._channel: Any | None = None
        self._motor_configuration: Any | None = None
        self._device_limits: tuple[float, float] | None = None
        self._error: str | None = None

    def connect(self) -> None:
        if self._channel is not None:
            return
        # Connection initializes and validates settings but intentionally causes no motion.
        self.facade.load()
        serial = self.config.serial_number or ""
        self.facade.DeviceManagerCLI.BuildDeviceList()
        controller = self.facade.BenchtopStepperMotor.CreateBenchtopStepperMotor(serial)
        channel: Any | None = None
        try:
            controller.Connect(serial)
            self.facade.settle()
            if not bool(controller.IsConnected):
                raise AdapterError(f"rotation: BSC101 {serial} did not connect")
            # BSC101 exposes the attached HDR50 through its configured channel object.
            channel = controller.GetChannel(self.config.axis_number)
            if not channel.IsSettingsInitialized():
                channel.WaitForSettingsInitialized(
                    int(self.config.motion.operation_timeout_s * 1000)
                )
            if not channel.IsSettingsInitialized():
                raise AdapterError("rotation: settings initialization timed out")
            # Kinesis requires polling and enablement before channel state is reliable.
            channel.StartPolling(self.config.polling_interval_ms)
            self.facade.settle()
            channel.EnableDevice()
            self.facade.settle()
            # Read the existing Kinesis actuator profile without changing its name.
            motor_configuration = channel.LoadMotorConfiguration(serial)
            motor_settings = channel.MotorDeviceSettings
            channel.GetSettings(motor_settings)
            configured_name = str(motor_configuration.DeviceSettingsName)
            expected_name = self.config.device_settings_name or ""
            if configured_name != expected_name:
                raise AdapterError(
                    "rotation: expected Kinesis device settings "
                    f"{expected_name!r}, got {configured_name!r}"
                )
            # Apply the loaded configuration in memory; do not persist it to device memory.
            motor_configuration.UpdateCurrentConfiguration()
            self.facade.settle()
            if not bool(channel.IsActuatorDefined):
                raise AdapterError("rotation: Kinesis actuator is not defined")
            if not bool(channel.IsMotorSettingsValid):
                raise AdapterError("rotation: Kinesis motor settings are invalid")
            # Kinesis exposes the configured stage range through channel motor settings.
            counterclockwise = self.facade.dec2float(
                motor_settings.Limit.CCWSoftLimitUnit
            )
            clockwise = self.facade.dec2float(motor_settings.Limit.CWSoftLimitUnit)
            device_minimum = min(counterclockwise, clockwise)
            device_maximum = max(counterclockwise, clockwise)
            if device_minimum >= device_maximum:
                raise AdapterError(
                    "rotation: Kinesis reported invalid stage limits "
                    f"[{device_minimum}, {device_maximum}] deg"
                )
            self._controller = controller
            self._channel = channel
            self._motor_configuration = motor_configuration
            self._device_limits = (device_minimum, device_maximum)
            self._error = None
        except Exception:
            # A partial connection must still stop polling and release the controller.
            if channel is not None:
                with suppress(Exception):
                    channel.StopPolling()
            with suppress(Exception):
                controller.Disconnect()
            raise

    def disconnect(self) -> None:
        if self._controller is None:
            return
        # Clear references first so repeated cleanup cannot reuse a closing device.
        controller, self._controller = self._controller, None
        channel, self._channel = self._channel, None
        self._motor_configuration = None
        self._device_limits = None
        try:
            if channel is not None:
                channel.StopPolling()
        finally:
            controller.Disconnect()

    def identity(self) -> ComponentIdentity:
        channel = self._require_channel()
        info = channel.GetDeviceInfo()
        model = str(getattr(info, "Name", getattr(info, "Description", "BSC101")))
        if self.config.expected_model and self.config.expected_model.lower() not in model.lower():
            raise AdapterError(
                f"rotation: expected model {self.config.expected_model}, got {model}"
            )
        return ComponentIdentity(
            AxisName.ROTATION,
            AdapterType.THORLABS_BSC101,
            self.config.serial_number or str(channel.DeviceID),
            model,
        )

    def identify(self) -> None:
        """Ask BSC101 channel 1 to identify itself visually."""
        self._require_channel().IdentifyDevice()

    def status(self) -> ComponentStatus:
        if self._channel is None:
            return ComponentStatus(False, False, False, False, None, self._error)
        try:
            status = self._channel.Status
            return ComponentStatus(
                True,
                bool(status.IsEnabled),
                bool(status.IsHomed),
                bool(status.IsInMotion),
                self.facade.dec2float(self._channel.Position),
                self._error,
            )
        except Exception as exc:
            self._error = str(exc)
            return ComponentStatus(True, False, False, False, None, self._error)

    def home(self) -> None:
        self._run_callback_operation(lambda callback: self._require_channel().Home(callback))

    def stop(self) -> None:
        if self._channel is not None:
            self._channel.StopImmediate()

    def move_absolute(self, destination: float) -> None:
        self._require_homed()
        self._validate_destination(destination)
        value = self.facade.decimal(destination)
        self._run_callback_operation(
            lambda callback: self._require_channel().MoveTo(value, callback)
        )

    def move_relative(self, displacement: float) -> None:
        self.move_absolute(self.get_position() + displacement)

    def get_position(self) -> float:
        return self.facade.dec2float(self._require_channel().Position)

    def get_limits(self) -> LimitsConfig:
        """Return Kinesis limits loaded from the attached stage configuration."""
        if self._channel is None or self._device_limits is None:
            raise AdapterError("rotation: limits unavailable while disconnected")
        return LimitsConfig(*self._device_limits)

    def _require_channel(self) -> Any:
        if self._channel is None:
            raise AdapterError("rotation: not connected")
        return self._channel

    def _require_homed(self) -> None:
        if not self.status().homed:
            raise MotionSafetyError("rotation: must be homed before movement")

    def _validate_destination(self, destination: float) -> None:
        if not self.get_limits().contains(destination):
            raise MotionSafetyError("rotation: destination outside device limits")

    def _run_callback_operation(self, start: Any) -> None:
        # Adapt Kinesis callbacks into a blocking worker-thread wait with a timeout.
        completed = threading.Event()

        def callback(_transaction_id: Any) -> None:
            completed.set()

        start(self.facade.callback(callback))
        if not completed.wait(self.config.motion.operation_timeout_s):
            # Timeout is treated as unsafe because completion can no longer be confirmed.
            self.stop()
            raise AdapterError("rotation: operation timed out")
