from types import SimpleNamespace
from typing import Any

import pytest

from acq_staging.adapters.thorlabs import ThorlabsBSC101Stage
from acq_staging.configuration import (
    ComponentConfig,
    LimitsConfig,
    MotionConfig,
)
from acq_staging.models import AdapterType


class FakeChannel:
    def __init__(self) -> None:
        self.DeviceID = "40000001"
        self.Position = 0.0
        self.Status = SimpleNamespace(
            IsEnabled=True,
            IsHomed=False,
            IsInMotion=False,
        )
        self.stopped = False
        self.polling_stopped = False
        self.identified = False
        self.IsActuatorDefined = True
        self.IsMotorSettingsValid = True
        self.MotorDeviceSettings = SimpleNamespace()
        self.settings_read = False
        self.configuration_updated = False
        self.velocity_params: tuple[object, object] | None = None
        self.reject_velocity_params = False

    def IsSettingsInitialized(self) -> bool:
        return True

    def StartPolling(self, _interval: int) -> None:
        return None

    def EnableDevice(self) -> None:
        return None

    def IdentifyDevice(self) -> None:
        self.identified = True

    def LoadMotorConfiguration(self, _serial: str) -> object:
        return SimpleNamespace(
            DeviceSettingsName="HDR50",
            UpdateCurrentConfiguration=self._update_configuration,
        )

    def _update_configuration(self) -> bool:
        self.configuration_updated = True
        return False

    def GetSettings(self, settings: object) -> None:
        assert settings is self.MotorDeviceSettings
        self.settings_read = True

    def SetVelocityParams(self, velocity: object, acceleration: object) -> None:
        # Require settings initialization before overriding the loaded motion defaults.
        assert self.configuration_updated
        if self.reject_velocity_params:
            raise RuntimeError("velocity parameters rejected")
        self.velocity_params = (velocity, acceleration)

    def Home(self, callback: Any) -> int:
        self.Status.IsHomed = True
        callback(1)
        return 1

    def MoveTo(self, position: float, callback: Any) -> int:
        self.Position = position
        callback(2)
        return 2

    def StopImmediate(self) -> None:
        self.stopped = True

    def StopPolling(self) -> None:
        self.polling_stopped = True

    def GetDeviceInfo(self) -> object:
        return SimpleNamespace(Name="BSC101")


class FakeController:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.connected_serial: str | None = None
        self.requested_channel: int | None = None
        self.disconnected = False
        self.IsConnected = False

    def Connect(self, serial: str) -> None:
        self.connected_serial = serial
        self.IsConnected = True

    def GetChannel(self, channel_number: int) -> FakeChannel:
        self.requested_channel = channel_number
        return self.channel

    def Disconnect(self) -> None:
        self.disconnected = True
        self.IsConnected = False


class FakeFacade:
    def __init__(self) -> None:
        self.channel = FakeChannel()
        self.controller = FakeController(self.channel)
        self.DeviceManagerCLI = SimpleNamespace(BuildDeviceList=lambda: None)
        self.BenchtopStepperMotor = SimpleNamespace(
            CreateBenchtopStepperMotor=lambda _serial: self.controller
        )
    def load(self) -> None:
        return None

    def decimal(self, value: float) -> float:
        return value

    def dec2float(self, value: float) -> float:
        return value

    def callback(self, function: Any) -> Any:
        return function

    def settle(self) -> None:
        return None


def test_bsc101_adapter_uses_channel_one_and_callback_motion() -> None:
    component = ComponentConfig(
        True,
        AdapterType.THORLABS_BSC101,
        LimitsConfig(0.0, 360.0),
        MotionConfig(10.0, 20.0, 5.0, 5.0, 0.05, 1.0),
        serial_number="40000001",
        axis_number=1,
        expected_model="BSC101",
        device_settings_name="HDR50",
        dll_directory="unused-by-fake",
    )
    facade = FakeFacade()
    stage = ThorlabsBSC101Stage(component, facade)

    stage.connect()
    # Connection must apply distinct supplied values in vendor order without motion.
    assert facade.channel.velocity_params == (10.0, 20.0)
    assert not facade.channel.Status.IsHomed
    assert facade.channel.Position == 0.0
    stage.identify()
    stage.home()
    stage.move_absolute(720.0)

    assert facade.controller.connected_serial == "40000001"
    assert facade.controller.requested_channel == 1
    assert facade.channel.identified
    assert facade.channel.settings_read
    assert facade.channel.configuration_updated
    # The adapter forwards targets beyond reported settings and leaves Kinesis in control.
    assert stage.get_position() == 720.0
    assert stage.get_limits() is None
    assert stage.identity().model == "BSC101"

    stage.disconnect()
    assert facade.channel.polling_stopped
    assert facade.controller.disconnected


def test_bsc101_adapter_cleans_up_when_motion_settings_are_rejected() -> None:
    # Use a fake vendor rejection to verify partial connection cleanup without hardware.
    component = ComponentConfig(
        True,
        AdapterType.THORLABS_BSC101,
        None,
        MotionConfig(7.5, 12.25, 5.0, 5.0, 0.05, 1.0),
        serial_number="40000001",
        axis_number=1,
        device_settings_name="HDR50",
        dll_directory="unused-by-fake",
    )
    facade = FakeFacade()
    facade.channel.reject_velocity_params = True
    stage = ThorlabsBSC101Stage(component, facade)

    # Preserve the vendor error and leave no connected adapter or polling session.
    with pytest.raises(RuntimeError, match="velocity parameters rejected"):
        stage.connect()
    assert facade.channel.polling_stopped
    assert facade.controller.disconnected
    assert not stage.status().connected


def test_bsc101_adapter_rejects_wrong_stage_settings_and_cleans_up() -> None:
    component = ComponentConfig(
        True,
        AdapterType.THORLABS_BSC101,
        LimitsConfig(0.0, 360.0),
        MotionConfig(10.0, 20.0, 5.0, 5.0, 0.05, 1.0),
        serial_number="40000001",
        axis_number=1,
        expected_model="BSC101",
        device_settings_name="HDR50/M",
        dll_directory="unused-by-fake",
    )
    facade = FakeFacade()
    stage = ThorlabsBSC101Stage(component, facade)

    try:
        stage.connect()
    except Exception as error:
        assert "expected Kinesis device settings 'HDR50/M', got 'HDR50'" in str(error)
    else:
        raise AssertionError("stage settings mismatch should fail")

    assert facade.channel.polling_stopped
    assert facade.controller.disconnected
