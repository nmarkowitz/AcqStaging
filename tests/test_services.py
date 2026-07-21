from dataclasses import replace

import pytest

from acq_staging.adapters.simulated import SimulatedMotionAxis
from acq_staging.configuration import MosaicConfig, default_simulation_config
from acq_staging.errors import MotionSafetyError
from acq_staging.factory import build_simulation_axes
from acq_staging.models import HOME_ORDER, AxisName
from acq_staging.services import MotionService


def make_service() -> MotionService:
    config = default_simulation_config()
    return MotionService(config, build_simulation_axes(config))


def test_home_all_and_pose_order() -> None:
    service = make_service()
    service.connect_all()
    progress: list[AxisName] = []
    assert service.home_all(progress.append) == list(HOME_ORDER)
    assert progress == list(HOME_ORDER)
    service.move_absolute(AxisName.X, 1.0)
    service.move_absolute(AxisName.Y, 2.0)
    service.move_absolute(AxisName.Z, 3.0)
    service.move_absolute(AxisName.ROTATION, 4.0)
    assert service.pose.as_vector() == (1.0, 2.0, 3.0, 4.0)


def test_home_all_stops_on_failure() -> None:
    service = make_service()
    service.connect_all()
    y_axis = service.axes[AxisName.Y]
    assert isinstance(y_axis, SimulatedMotionAxis)
    y_axis.inject_failure("home failed")
    with pytest.raises(Exception, match="home failed"):
        service.home_all()
    assert service.axes[AxisName.X].status().homed
    assert not service.axes[AxisName.Z].status().homed


def test_motion_test_returns_to_start() -> None:
    service = make_service()
    service.connect(AxisName.X)
    service.home(AxisName.X)
    start, target = service.motion_test(AxisName.X)
    assert target != start
    assert service.axes[AxisName.X].get_position() == start


def test_identify_requires_connection_and_does_not_move() -> None:
    service = make_service()
    with pytest.raises(MotionSafetyError, match="connected"):
        service.identify(AxisName.X)
    service.connect(AxisName.X)
    start = service.axes[AxisName.X].get_position()
    service.identify(AxisName.X)
    assert service.axes[AxisName.X].get_position() == start


def test_mosaic_plans_and_runs_xyz_serpentine() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 2.0, 3.0, 3, 2, 2, 0.0, 0.0),
    )
    service = MotionService(config, build_simulation_axes(config))
    service.connect_all()
    service.home_all()

    tiles = service.run_mosaic()

    assert [(tile.x_index, tile.y_index, tile.z_index) for tile in tiles] == [
        (0, 0, 0),
        (1, 0, 0),
        (2, 0, 0),
        (2, 1, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (2, 0, 1),
        (2, 1, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]
    assert service.pose.as_vector()[:3] == (0.0, 2.0, 3.0)


def test_mosaic_preflight_rejects_entire_path_before_motion() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(200.0, 1.0, 0.0, 3, 1, 1, 0.0, 0.0),
    )
    service = MotionService(config, build_simulation_axes(config))
    service.connect_all()
    service.home_all()

    with pytest.raises(MotionSafetyError, match="outside software limits"):
        service.run_mosaic()

    assert service.axes[AxisName.X].get_position() == 0.0


def test_mosaic_timeout_stops_the_operation() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 1, 1, 1, 0.1, 0.0, 0.01),
    )
    service = MotionService(config, build_simulation_axes(config))
    service.connect_all()
    service.home_all()

    with pytest.raises(Exception, match="mosaic operation timed out"):
        service.run_mosaic()


def test_mosaic_can_skip_only_acknowledged_disconnected_axes() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 2, 2, 1, 0.0, 0.0, 10.0),
    )
    service = MotionService(config, build_simulation_axes(config))
    for axis in (AxisName.X, AxisName.Y):
        service.connect(axis)
        service.home(axis)

    with pytest.raises(MotionSafetyError, match="acknowledgement"):
        service.run_mosaic()

    tiles = service.run_mosaic(allowed_disconnected=frozenset({AxisName.Z}))
    assert len(tiles) == 4
    assert service.axes[AxisName.Z].status().connected is False
