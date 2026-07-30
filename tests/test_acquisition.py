import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial

import pytest

from acq_staging.acquisition import DaqTileHandshake, DummyDataAcquirer
from acq_staging.adapters.digital import SimulatedDigitalIODevice
from acq_staging.configuration import (
    AcquisitionHandshakeConfig,
    MosaicConfig,
    default_dummy_acquirer_config,
    default_simulation_config,
)
from acq_staging.errors import MotionSafetyError
from acq_staging.factory import build_digital_devices, build_simulation_axes
from acq_staging.models import AxisName, DigitalDirection, MosaicProgress, MosaicTilePhase
from acq_staging.services import DigitalIOService, MotionService


def wait_until(predicate: Callable[[], bool], timeout_s: float = 1.0) -> None:
    """Wait for a test-thread state change without hiding a timeout failure."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("timed out waiting for test state")


def event_count_is(events: list[str], event: str, expected: int) -> bool:
    """Return whether a threaded handshake emitted an event the expected number of times."""
    return events.count(event) == expected


def make_enabled_movement_services() -> tuple[
    MotionService,
    DigitalIOService,
    SimulatedDigitalIODevice,
]:
    """Build a one-tile movement service with an enabled simulated handshake."""
    base = default_simulation_config()
    config = replace(
        base,
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 0.0, 5.0),
        acquisition_handshake=AcquisitionHandshakeConfig(
            True,
            0,
            0,
            acquisition_timeout_s=0.5,
            reset_timeout_s=0.5,
            polling_interval_ms=1,
        ),
    )
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    digital.write_outputs(
        {line: line == 0 for line in config.digital_io[DigitalDirection.OUTPUT].selected_lines}
    )
    handshake = DaqTileHandshake(config.acquisition_handshake, digital)
    motion = MotionService(config, build_simulation_axes(config), handshake)
    motion.connect_all()
    motion.home_all()
    input_device = devices[DigitalDirection.INPUT]
    assert isinstance(input_device, SimulatedDigitalIODevice)
    return motion, digital, input_device


def test_mosaic_requires_low_high_low_exchange_and_resets_output_high() -> None:
    motion, digital, input_device = make_enabled_movement_services()
    events: list[str] = []

    # The peer acknowledges LOW with HIGH, then lowers its input when the image completes.
    def respond() -> None:
        # One acquisition request is completed for each of the two one-tile tilings.
        for request_index in range(2):
            expected_count = request_index + 1
            wait_until(
                partial(
                    event_count_is,
                    events,
                    "ACQUISITION_REQUEST_LOW",
                    expected_count,
                )
            )
            input_device.set_simulated_inputs(
                {line: line == 0 for line in input_device.config.selected_lines}
            )
            wait_until(
                partial(
                    event_count_is,
                    events,
                    "ACQUISITION_STARTED_HIGH",
                    expected_count,
                )
            )
            input_device.set_simulated_inputs(
                {line: False for line in input_device.config.selected_lines}
            )
            wait_until(
                partial(
                    event_count_is,
                    events,
                    "ACQUISITION_RESET_HIGH",
                    expected_count,
                )
            )

    peer = threading.Thread(target=respond)
    peer.start()
    progress: list[MosaicProgress] = []
    motion.run_mosaic_sequence(
        motion.default_mosaic_sequence_spec(),
        progress.append,
        lambda event, _details: events.append(event),
    )
    peer.join(timeout=1.0)

    assert not peer.is_alive()
    assert digital.status(DigitalDirection.OUTPUT).states[0] is True
    assert [item.phase for item in progress] == [
        MosaicTilePhase.CURRENT,
        MosaicTilePhase.ACQUIRING,
        MosaicTilePhase.COMPLETE,
        MosaicTilePhase.CURRENT,
        MosaicTilePhase.ACQUIRING,
        MosaicTilePhase.COMPLETE,
    ]
    assert "ACQUISITION_REQUEST_LOW" in events
    assert "ACQUISITION_STARTED_HIGH" in events
    assert "ACQUISITION_COMPLETE_LOW" in events
    assert "ACQUISITION_RESET_HIGH" in events
    signal_events = [
        event
        for event in events
        if event
        in {
            "ACQUISITION_REQUEST_LOW",
            "ACQUISITION_STARTED_HIGH",
            "ACQUISITION_COMPLETE_LOW",
            "ACQUISITION_RESET_HIGH",
        }
    ]
    assert signal_events == [
        "ACQUISITION_REQUEST_LOW",
        "ACQUISITION_STARTED_HIGH",
        "ACQUISITION_COMPLETE_LOW",
        "ACQUISITION_RESET_HIGH",
    ] * 2

    # The first reset must finish before the next tile can begin or request another image.
    tile_start_indices = [
        index for index, event in enumerate(events) if event == "TILE_START"
    ]
    request_indices = [
        index for index, event in enumerate(events) if event == "ACQUISITION_REQUEST_LOW"
    ]
    reset_indices = [
        index for index, event in enumerate(events) if event == "ACQUISITION_RESET_HIGH"
    ]
    assert reset_indices[0] < tile_start_indices[1] < request_indices[1]


def test_handshake_preflight_rejects_unknown_unrelated_outputs_before_motion() -> None:
    # Leave every simulated output UNKNOWN so unrelated pins cannot be driven implicitly.
    base = default_simulation_config()
    config = replace(
        base,
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 0.0, 5.0),
        acquisition_handshake=replace(base.acquisition_handshake, enabled=True),
    )
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    motion = MotionService(
        config,
        build_simulation_axes(config),
        DaqTileHandshake(config.acquisition_handshake, digital),
    )
    motion.connect_all()
    motion.home_all()

    with pytest.raises(MotionSafetyError, match="non-handshake digital outputs"):
        motion.run_mosaic_sequence(motion.default_mosaic_sequence_spec())

    assert motion.axes[AxisName.X].get_position() == 0.0


def test_handshake_begin_initializes_single_request_output_high() -> None:
    base = default_simulation_config()
    output_config = replace(
        base.digital_io[DigitalDirection.OUTPUT],
        line_count=1,
        lines=(0,),
    )
    config = replace(
        base,
        digital_io={
            **base.digital_io,
            DigitalDirection.OUTPUT: output_config,
        },
        acquisition_handshake=replace(base.acquisition_handshake, enabled=True),
    )
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    handshake = DaqTileHandshake(config.acquisition_handshake, digital)

    # Starting the mosaic handshake is the explicit action that drives request idle HIGH.
    handshake.begin()
    observed = digital.status(DigitalDirection.OUTPUT).states[0]
    handshake.finish()

    assert observed is True


def test_handshake_preflight_rejects_stale_high_response_before_motion() -> None:
    motion, _digital, input_device = make_enabled_movement_services()
    input_device.set_simulated_inputs(
        {line: line == 0 for line in input_device.config.selected_lines}
    )

    with pytest.raises(MotionSafetyError, match="must be known and LOW"):
        motion.run_mosaic_sequence(motion.default_mosaic_sequence_spec())

    assert motion.axes[AxisName.X].get_position() == 0.0


def test_acquisition_timeout_returns_request_high_and_prevents_next_tile() -> None:
    motion, digital, _input_device = make_enabled_movement_services()
    short = replace(
        motion.config.acquisition_handshake,
        acquisition_timeout_s=0.01,
    )
    handshake = DaqTileHandshake(short, digital)
    motion.acquisition_handshake = handshake

    with pytest.raises(Exception, match="timed out waiting for acquisition start"):
        motion.run_mosaic_sequence(motion.default_mosaic_sequence_spec())

    assert digital.status(DigitalDirection.OUTPUT).states[0] is True


def test_dummy_preflight_requires_movement_request_high() -> None:
    config = default_dummy_acquirer_config()
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    digital.write_outputs({0: False})
    acquirer = DummyDataAcquirer(config, digital)

    with pytest.raises(MotionSafetyError, match="must be known and HIGH"):
        acquirer.run()


def test_dummy_acquirer_raises_acceptance_then_lowers_completion() -> None:
    config = replace(
        default_dummy_acquirer_config(),
        handshake=replace(
            default_dummy_acquirer_config().handshake,
            acquisition_duration_s=0.02,
            polling_interval_ms=1,
        ),
    )
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    digital.write_outputs({0: False})
    input_device = devices[DigitalDirection.INPUT]
    assert isinstance(input_device, SimulatedDigitalIODevice)
    input_device.set_simulated_inputs({0: True})
    acquirer = DummyDataAcquirer(config, digital)
    events: list[str] = []

    listener = threading.Thread(
        target=lambda: acquirer.run(lambda event, _details: events.append(event))
    )
    listener.start()
    wait_until(lambda: acquirer.status().listening)
    input_device.set_simulated_inputs({0: False})
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is True)
    assert acquirer.status().requests_received == 1
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is False)
    input_device.set_simulated_inputs({0: True})
    wait_until(lambda: acquirer.status().acquisitions_completed == 1)
    wait_until(lambda: "DUMMY_REQUEST_RESET_HIGH" in events)
    acquirer.request_stop()
    listener.join(timeout=1.0)

    assert not listener.is_alive()
    assert digital.status(DigitalDirection.OUTPUT).states[0] is False
    signal_events = [
        event
        for event in events
        if event
        in {
            "DUMMY_ACQUISITION_ACCEPTED_HIGH",
            "DUMMY_IMAGE_COMPLETE_LOW",
            "DUMMY_REQUEST_RESET_HIGH",
        }
    ]
    assert signal_events == [
        "DUMMY_ACQUISITION_ACCEPTED_HIGH",
        "DUMMY_IMAGE_COMPLETE_LOW",
        "DUMMY_REQUEST_RESET_HIGH",
    ]


def test_dummy_acquisition_can_be_completed_manually() -> None:
    base = default_dummy_acquirer_config()
    config = replace(
        base,
        handshake=replace(
            base.handshake,
            acquisition_duration_s=10.0,
            polling_interval_ms=1,
        ),
    )
    devices = build_digital_devices(config)
    digital = DigitalIOService(config, devices)
    digital.connect(DigitalDirection.INPUT)
    digital.connect(DigitalDirection.OUTPUT)
    digital.write_outputs({0: False})
    input_device = devices[DigitalDirection.INPUT]
    assert isinstance(input_device, SimulatedDigitalIODevice)
    input_device.set_simulated_inputs({0: True})
    acquirer = DummyDataAcquirer(config, digital)

    listener = threading.Thread(target=acquirer.run)
    listener.start()
    wait_until(lambda: acquirer.status().listening)
    input_device.set_simulated_inputs({0: False})
    wait_until(lambda: acquirer.status().acquiring)
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is True)
    acquirer.complete_now()
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is False)
    input_device.set_simulated_inputs({0: True})
    wait_until(lambda: acquirer.status().acquisitions_completed == 1)
    acquirer.request_stop()
    listener.join(timeout=1.0)

    assert not listener.is_alive()
