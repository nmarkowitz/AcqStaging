from types import SimpleNamespace

from acq_staging.adapters.digital import (
    DigitalIODevice,
    NidaqmxDigitalIODevice,
    SimulatedDigitalIODevice,
)
from acq_staging.configuration import DigitalIOConfig, default_simulation_config
from acq_staging.models import DigitalAdapterType, DigitalDirection
from acq_staging.services import DigitalIOService


class FakeChannels:
    def __init__(self) -> None:
        self.added: list[tuple[str, object]] = []

    def add_di_chan(self, physical: str, *, line_grouping: object) -> None:
        self.added.append((physical, line_grouping))

    def add_do_chan(self, physical: str, *, line_grouping: object) -> None:
        self.added.append((physical, line_grouping))


class FakeTask:
    def __init__(self, read_value: object = 0) -> None:
        self.di_channels = FakeChannels()
        self.do_channels = FakeChannels()
        self.read_value = read_value
        self.read_timeout: float | None = None
        self.write_call: tuple[object, bool, float] | None = None
        self.closed = False

    def read(self, *, timeout: float) -> object:
        self.read_timeout = timeout
        return self.read_value

    def write(self, data: object, *, auto_start: bool, timeout: float) -> None:
        self.write_call = (data, auto_start, timeout)

    def close(self) -> None:
        self.closed = True


class FakeFacade:
    def __init__(self, task: FakeTask) -> None:
        self.task = task
        self.Task = lambda: task
        self.LineGrouping = SimpleNamespace(
            CHAN_FOR_ALL_LINES="all",
            CHAN_PER_LINE="per-line",
        )

    def load(self) -> None:
        return None


def test_nidaq_whole_port_input_decodes_bitmask() -> None:
    config = DigitalIOConfig(
        True,
        DigitalAdapterType.NIDAQMX,
        "Dev1",
        "P0",
        4,
        operation_timeout_s=3.0,
    )
    task = FakeTask(0b1010)
    device = NidaqmxDigitalIODevice(DigitalDirection.INPUT, config, FakeFacade(task))

    device.connect()
    states = device.read_states()

    assert task.di_channels.added == [("Dev1/port0", "all")]
    assert states == {0: False, 1: True, 2: False, 3: True}
    assert task.read_timeout == 3.0


def test_nidaq_selected_outputs_write_multiple_boolean_channels() -> None:
    config = DigitalIOConfig(
        True,
        DigitalAdapterType.NIDAQMX,
        "Dev1",
        "port1",
        8,
        lines=(0, 2, 5),
        operation_timeout_s=4.0,
    )
    task = FakeTask()
    device = NidaqmxDigitalIODevice(DigitalDirection.OUTPUT, config, FakeFacade(task))

    device.connect()
    device.write_states({0: True, 2: False, 5: True})

    assert [physical for physical, _grouping in task.do_channels.added] == [
        "Dev1/port1/line0",
        "Dev1/port1/line2",
        "Dev1/port1/line5",
    ]
    assert task.write_call == ([True, False, True], True, 4.0)
    assert device.status().states == {0: True, 2: False, 5: True}


class RecordingOutput(SimulatedDigitalIODevice):
    def __init__(self, config: DigitalIOConfig) -> None:
        super().__init__(DigitalDirection.OUTPUT, config)
        self.write_count = 0

    def write_states(self, states: dict[int, bool]) -> None:
        self.write_count += 1
        super().write_states(states)


def test_output_is_reasserted_only_after_first_explicit_write() -> None:
    config = default_simulation_config()
    input_device = SimulatedDigitalIODevice(
        DigitalDirection.INPUT,
        config.digital_io[DigitalDirection.INPUT],
    )
    output_device = RecordingOutput(config.digital_io[DigitalDirection.OUTPUT])
    service = DigitalIOService(
        config,
        {
            DigitalDirection.INPUT: input_device,
            DigitalDirection.OUTPUT: output_device,
        },
    )
    events: list[tuple[str, dict[str, object]]] = []
    service.set_event_callback(lambda event, data: events.append((event, data)))
    service.connect(DigitalDirection.OUTPUT)

    service.refresh_connected_io()
    assert output_device.write_count == 0

    requested = {line: line == 2 for line in output_device.config.selected_lines}
    service.write_outputs(requested)
    service.refresh_connected_io()
    assert output_device.write_count == 2
    assert [data["source"] for event, data in events if event == "DIGITAL_OUTPUT_WRITE"] == [
        "explicit",
        "continuous_reassertion",
    ]


def test_handshake_line_write_preserves_other_initialized_outputs() -> None:
    config = default_simulation_config()
    devices: dict[DigitalDirection, DigitalIODevice] = {
        DigitalDirection.INPUT: SimulatedDigitalIODevice(
            DigitalDirection.INPUT,
            config.digital_io[DigitalDirection.INPUT],
        ),
        DigitalDirection.OUTPUT: SimulatedDigitalIODevice(
            DigitalDirection.OUTPUT,
            config.digital_io[DigitalDirection.OUTPUT],
        ),
    }
    service = DigitalIOService(config, devices)
    service.connect(DigitalDirection.INPUT)
    service.connect(DigitalDirection.OUTPUT)
    initial = {0: False, 1: True, 2: False, 3: True}
    service.write_outputs(initial)

    service.begin_handshake(0, 0)
    service.write_output_line(0, True)
    observed = service.status(DigitalDirection.OUTPUT).states
    service.write_output_line(0, False)
    service.end_handshake()

    assert observed == {0: True, 1: True, 2: False, 3: True}
