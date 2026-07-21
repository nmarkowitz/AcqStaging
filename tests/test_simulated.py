import threading
import time
from dataclasses import replace

import pytest

from acq_staging.adapters.simulated import SimulatedMotionAxis
from acq_staging.configuration import default_simulation_config
from acq_staging.errors import AdapterError, MotionSafetyError
from acq_staging.models import AxisName


def make_axis() -> SimulatedMotionAxis:
    config = default_simulation_config()
    return SimulatedMotionAxis(AxisName.X, config.components[AxisName.X])


def test_simulator_requires_connection_and_home() -> None:
    axis = make_axis()
    with pytest.raises(AdapterError, match="not connected"):
        axis.get_position()
    with pytest.raises(AdapterError, match="not connected"):
        axis.identify()
    axis.connect()
    axis.identify()
    with pytest.raises(MotionSafetyError, match="homed"):
        axis.move_relative(1.0)
    axis.home()
    axis.move_absolute(2.0)
    assert axis.get_position() == 2.0


def test_simulator_enforces_limits() -> None:
    axis = make_axis()
    axis.connect()
    axis.home()
    with pytest.raises(MotionSafetyError, match="outside"):
        axis.move_absolute(301.0)


def test_simulator_injected_failure() -> None:
    axis = make_axis()
    axis.connect()
    axis.inject_failure("simulated fault")
    with pytest.raises(AdapterError, match="simulated fault"):
        axis.home()


def test_stop_interrupts_simulated_motion() -> None:
    config = default_simulation_config()
    component = replace(
        config.components[AxisName.X],
        simulation_latency_s=0.2,
    )
    axis = SimulatedMotionAxis(AxisName.X, component)
    axis.connect()
    errors: list[Exception] = []

    def home() -> None:
        try:
            axis.home()
        except Exception as exc:
            errors.append(exc)

    home_thread = threading.Thread(target=home)
    home_thread.start()
    time.sleep(0.02)
    axis.stop()
    home_thread.join()
    assert errors
    assert not axis.status().homed
