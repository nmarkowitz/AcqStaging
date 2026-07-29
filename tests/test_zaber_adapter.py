from types import SimpleNamespace

from acq_staging.adapters.zaber import ZaberConnectionManager, ZaberLinearStage
from acq_staging.configuration import (
    ComponentConfig,
    LimitsConfig,
    MotionConfig,
    ZaberConnectionConfig,
)
from acq_staging.models import AdapterType, AxisName


class FakeAxis:
    def __init__(self) -> None:
        self.position = 0.0
        self.homed = False
        self.busy = False
        self.moves: list[tuple[float, object, dict[str, object]]] = []

    def home(self, *, wait_until_idle: bool) -> None:
        assert wait_until_idle is False
        self.homed = True

    def is_homed(self) -> bool:
        return self.homed

    def is_busy(self) -> bool:
        return self.busy

    def get_position(self, _unit: object) -> float:
        return self.position

    def move_absolute(self, position: float, unit: object, **kwargs: object) -> None:
        self.moves.append((position, unit, kwargs))
        self.position = position

    def stop(self, *, wait_until_idle: bool) -> None:
        assert wait_until_idle is False
        self.busy = False


class FakeConnection:
    def __init__(self, device: object) -> None:
        self.device = device
        self.closed = False

    def detect_devices(self) -> list[object]:
        return [self.device]

    def close(self) -> None:
        self.closed = True


def test_zaber_adapter_matches_serial_and_sends_units() -> None:
    fake_axis = FakeAxis()
    identified: list[bool] = []
    device = SimpleNamespace(
        serial_number=1234,
        device_address=1,
        name="X-LRQ300HL-DE51",
        get_axis=lambda _number: fake_axis,
        identify=lambda: identified.append(True),
    )
    connection = FakeConnection(device)
    manager = ZaberConnectionManager(
        {"main": ZaberConnectionConfig("COM1")},
        connection_factory=lambda _port, _baud: connection,
    )
    component = ComponentConfig(
        True,
        AdapterType.ZABER_ASCII,
        LimitsConfig(0.0, 300.0),
        MotionConfig(10.0, 50.0, 1.0, 1.0, 0.01, 1.0),
        connection_id="main",
        serial_number="1234",
    )
    stage = ZaberLinearStage(AxisName.X, component, manager)
    stage.connect()
    stage.identify()
    stage.home()
    stage.move_absolute(12.5)
    assert fake_axis.moves[0][0] == 12.5
    assert fake_axis.moves[0][2]["wait_until_idle"] is False
    assert identified == [True]
    stage.disconnect()
    assert connection.closed


def test_zaber_adapter_delegates_travel_limits_to_controller() -> None:
    fake_axis = FakeAxis()
    device = SimpleNamespace(
        serial_number=1234,
        device_address=1,
        name="X-LRQ300HL-DE51",
        get_axis=lambda _number: fake_axis,
    )
    connection = FakeConnection(device)
    manager = ZaberConnectionManager(
        {"main": ZaberConnectionConfig("COM1")},
        connection_factory=lambda _port, _baud: connection,
    )
    component = ComponentConfig(
        True,
        AdapterType.ZABER_ASCII,
        LimitsConfig(-1.0, 300.0),
        MotionConfig(10.0, 50.0, 1.0, 1.0, 0.01, 1.0),
        connection_id="main",
        serial_number="1234",
    )

    stage = ZaberLinearStage(AxisName.X, component, manager)
    stage.connect()
    stage.home()
    stage.move_absolute(400.0)

    assert stage.get_limits() is None
    assert fake_axis.moves[-1][0] == 400.0
    stage.disconnect()
    assert connection.closed
