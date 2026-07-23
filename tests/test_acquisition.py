import threading
import time
from collections.abc import Callable
from dataclasses import replace

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


def make_enabled_movement_services() -> tuple[
    MotionService,
    DigitalIOService,
    SimulatedDigitalIODevice,
]:
    """Build a one-tile movement service with an enabled simulated handshake."""
    base = default_simulation_config()
    config = replace(
        base,
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 1, 1, 1, 0.0, 0.0, 5.0),
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
        {line: False for line in config.digital_io[DigitalDirection.OUTPUT].selected_lines}
    )
    handshake = DaqTileHandshake(config.acquisition_handshake, digital)
    motion = MotionService(config, build_simulation_axes(config), handshake)
    motion.connect_all()
    motion.home_all()
    input_device = devices[DigitalDirection.INPUT]
    assert isinstance(input_device, SimulatedDigitalIODevice)
    return motion, digital, input_device


def test_mosaic_waits_for_complete_and_resets_both_lines() -> None:
    motion, digital, input_device = make_enabled_movement_services()

    # The peer holds Done HIGH until movement acknowledges by lowering Ready.
    def respond() -> None:
        wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is True)
        input_device.set_simulated_inputs(
            {line: line == 0 for line in input_device.config.selected_lines}
        )
        wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is False)
        input_device.set_simulated_inputs(
            {line: False for line in input_device.config.selected_lines}
        )

    peer = threading.Thread(target=respond)
    peer.start()
    events: list[str] = []
    progress: list[MosaicProgress] = []
    motion.run_mosaic(
        progress.append,
        on_event=lambda event, _details: events.append(event),
    )
    peer.join(timeout=1.0)

    assert not peer.is_alive()
    assert digital.status(DigitalDirection.OUTPUT).states[0] is False
    assert [item.phase for item in progress] == [
        MosaicTilePhase.CURRENT,
        MosaicTilePhase.ACQUIRING,
        MosaicTilePhase.COMPLETE,
    ]
    assert "ACQUISITION_READY_HIGH" in events
    assert "ACQUISITION_DONE_LOW" in events


def test_handshake_preflight_rejects_unknown_output_before_motion() -> None:
    # Leave every simulated output UNKNOWN to reproduce an uninitialized real task.
    base = default_simulation_config()
    config = replace(
        base,
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 2, 1, 1, 0.0, 0.0, 5.0),
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

    with pytest.raises(MotionSafetyError, match="initialized LOW"):
        motion.run_mosaic()

    assert motion.axes[AxisName.X].get_position() == 0.0


def test_acquisition_timeout_returns_ready_low_and_prevents_next_tile() -> None:
    motion, digital, _input_device = make_enabled_movement_services()
    short = replace(
        motion.config.acquisition_handshake,
        acquisition_timeout_s=0.01,
    )
    handshake = DaqTileHandshake(short, digital)
    motion.acquisition_handshake = handshake

    with pytest.raises(Exception, match="timed out waiting for acquisition completion"):
        motion.run_mosaic()

    assert digital.status(DigitalDirection.OUTPUT).states[0] is False


def test_dummy_acquirer_latches_done_until_ready_resets() -> None:
    config = replace(
        default_dummy_acquirer_config(),
        handshake=replace(
            default_dummy_acquirer_config().handshake,
            acquisition_duration_s=0.0,
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
    acquirer = DummyDataAcquirer(config, digital)

    listener = threading.Thread(target=acquirer.run)
    listener.start()
    wait_until(lambda: acquirer.status().listening)
    input_device.set_simulated_inputs({0: True})
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is True)
    assert acquirer.status().requests_received == 1
    input_device.set_simulated_inputs({0: False})
    wait_until(lambda: acquirer.status().acquisitions_completed == 1)
    acquirer.request_stop()
    listener.join(timeout=1.0)

    assert not listener.is_alive()
    assert digital.status(DigitalDirection.OUTPUT).states[0] is False


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
    acquirer = DummyDataAcquirer(config, digital)

    listener = threading.Thread(target=acquirer.run)
    listener.start()
    wait_until(lambda: acquirer.status().listening)
    input_device.set_simulated_inputs({0: True})
    wait_until(lambda: acquirer.status().acquiring)
    acquirer.complete_now()
    wait_until(lambda: digital.status(DigitalDirection.OUTPUT).states[0] is True)
    input_device.set_simulated_inputs({0: False})
    wait_until(lambda: acquirer.status().acquisitions_completed == 1)
    acquirer.request_stop()
    listener.join(timeout=1.0)

    assert not listener.is_alive()
