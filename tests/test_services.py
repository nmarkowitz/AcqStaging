from dataclasses import replace

import pytest

from acq_staging.adapters.simulated import SimulatedMotionAxis
from acq_staging.configuration import AppConfig, MosaicConfig, default_simulation_config
from acq_staging.errors import MotionSafetyError
from acq_staging.factory import build_simulation_axes
from acq_staging.models import (
    HOME_ORDER,
    AxisName,
    LimitsConfig,
    MosaicProgress,
    MosaicSequenceSpec,
    MosaicTilePhase,
    MosaicTilingSpec,
)
from acq_staging.services import MotionService


def make_service() -> MotionService:
    config = default_simulation_config()
    return MotionService(config, build_simulation_axes(config))


def make_sequence(
    *,
    first_stop_x: float = 2.0,
    second_stop_x: float = 12.0,
    resume_tiling: int = 1,
    resume_tile: int = 1,
    run_tiling_two: bool = True,
) -> MosaicSequenceSpec:
    first = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 0.0, 0.0, first_stop_x, 1.0, 5.0, 0.0
    )
    second = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 10.0, 10.0, second_stop_x, 11.0, 6.0, 45.0
    )
    return MosaicSequenceSpec(
        first,
        second,
        20.0,
        resume_tiling,
        resume_tile,
        run_tiling_two,
    )


def ready_service(config: AppConfig | None = None) -> MotionService:
    actual = config if config is not None else default_simulation_config()
    service = MotionService(actual, build_simulation_axes(actual))
    service.connect_all()
    service.home_all()
    return service


def test_home_all_and_pose_order() -> None:
    service = make_service()
    service.connect_all()
    progress: list[AxisName] = []
    assert service.home_all(progress.append) == list(HOME_ORDER)
    assert progress == list(HOME_ORDER)
    for axis, destination in zip(HOME_ORDER, (1.0, 2.0, 3.0, 4.0), strict=True):
        service.move_absolute(axis, destination)
    assert service.pose.as_vector() == (1.0, 2.0, 3.0, 4.0)


def test_service_delegates_unbounded_physical_move_to_adapter() -> None:
    config = default_simulation_config()
    # This fake accepts a broad range internally but exposes no application software limit.
    component = replace(
        config.components[AxisName.X],
        limits=LimitsConfig(-1_000.0, 1_000.0),
    )

    class VendorLimitedFakeAxis(SimulatedMotionAxis):
        def get_limits(self) -> LimitsConfig | None:
            return None

    axis = VendorLimitedFakeAxis(AxisName.X, component)
    service = MotionService(config, {AxisName.X: axis})
    service.connect(AxisName.X)
    service.home(AxisName.X)

    service.move_relative(AxisName.X, 1_400.0)

    assert axis.get_position() == 400.0


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


def test_motion_test_returns_to_controller_home_position() -> None:
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


def test_sequence_plans_two_independent_serpentine_xy_tilings() -> None:
    service = ready_service()
    first, second = service.plan_mosaic_sequence(make_sequence())

    assert [(tile.x_mm, tile.y_mm) for tile in first] == [
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.0),
        (2.0, 1.0),
        (1.0, 1.0),
        (0.0, 1.0),
    ]
    assert all(tile.z_mm == 5.0 and tile.theta_deg == 0.0 for tile in first)
    assert all(tile.z_mm == 6.0 and tile.theta_deg == 45.0 for tile in second)


def test_sequence_runs_first_then_safe_rotation_transition_then_second() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 0.0, 30.0),
    )
    service = ready_service(config)
    events: list[tuple[str, dict[str, object]]] = []
    progress: list[MosaicProgress] = []

    service.run_mosaic_sequence(
        make_sequence(), progress.append, lambda event, data: events.append((event, data))
    )

    names = [event for event, _details in events]
    first_complete = names.index("TILING_COMPLETE")
    second_start = names.index("TILING_START", first_complete + 1)
    assert first_complete < second_start
    second_moves = [
        details
        for event, details in events[second_start:]
        if event == "MOVEMENT_START"
    ]
    assert [(item["axis"], item["destination"]) for item in second_moves[:3]] == [
        ("z", 20.0),
        ("rotation", 45.0),
        ("z", 6.0),
    ]
    assert service.pose.as_vector() == (10.0, 11.0, 6.0, 45.0)
    assert progress[-1].tiling_number == 2
    assert progress[-1].phase is MosaicTilePhase.COMPLETE


def test_sequence_skips_disabled_second_tiling_entirely() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 1.0, 0.0, 0.0, 30.0),
    )
    service = ready_service(config)
    events: list[tuple[str, dict[str, object]]] = []
    progress: list[MosaicProgress] = []
    spec = make_sequence(
        first_stop_x=0.0,
        second_stop_x=400.0,
        run_tiling_two=False,
    )

    planned = service.run_mosaic_sequence(
        spec,
        progress.append,
        lambda event, data: events.append((event, data)),
    )

    assert len(planned) == 1
    assert {item.tiling_number for item in progress} == {1}
    starts = [details["tiling"] for event, details in events if event == "TILING_START"]
    assert starts == [1]
    full_run = next(details for event, details in events if event == "FULL_RUN_START")
    audited_tilings = full_run["tilings"]
    assert isinstance(audited_tilings, list)
    assert len(audited_tilings) == 1
    assert progress[-1].overall_total == spec.tiling_one.total_tiles
    assert service.pose.as_vector() == (0.0, 1.0, 5.0, 0.0)


def test_sequence_resume_from_second_skips_first_tiling() -> None:
    service = ready_service()
    events: list[tuple[str, dict[str, object]]] = []
    progress: list[MosaicProgress] = []
    spec = make_sequence(resume_tiling=2, resume_tile=2)

    service.run_mosaic_sequence(
        spec, progress.append, lambda event, data: events.append((event, data))
    )

    assert {item.tiling_number for item in progress} == {2}
    assert progress[0].number == 2
    starts = [details["tiling"] for event, details in events if event == "TILING_START"]
    assert starts == [2]


def test_sequence_preflight_rejects_second_path_before_any_motion() -> None:
    service = ready_service()
    spec = make_sequence(second_stop_x=400.0)

    with pytest.raises(MotionSafetyError, match="outside simulation limits"):
        service.run_mosaic_sequence(spec)

    assert service.pose.as_vector() == (0.0, 0.0, 0.0, 0.0)


def test_first_tiling_failure_prevents_second_tiling() -> None:
    service = ready_service()
    x_axis = service.axes[AxisName.X]
    assert isinstance(x_axis, SimulatedMotionAxis)
    x_axis.inject_failure("simulated tiling failure")
    events: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(Exception, match="simulated tiling failure"):
        service.run_mosaic_sequence(
            make_sequence(),
            on_event=lambda event, data: events.append((event, data)),
        )

    starts = [details["tiling"] for event, details in events if event == "TILING_START"]
    assert starts == [1]
    assert ("TILING_FAILED", {"tiling": 1, "error": "simulated tiling failure"}) in events
    assert events[-1][0] == "FULL_RUN_FAILED"


def test_sequence_requires_all_four_axes_connected_and_homed() -> None:
    service = make_service()
    for axis in (AxisName.X, AxisName.Y, AxisName.Z):
        service.connect(axis)
        service.home(axis)

    with pytest.raises(MotionSafetyError, match="rotation"):
        service.run_mosaic_sequence(make_sequence())


def test_sequence_timeout_stops_full_operation() -> None:
    config = replace(
        default_simulation_config(),
        mosaic=MosaicConfig(1.0, 1.0, 0.1, 0.0, 0.01),
    )
    service = ready_service(config)
    one_tile = make_sequence(first_stop_x=0.0, second_stop_x=10.0)

    with pytest.raises(Exception, match="mosaic operation timed out"):
        service.run_mosaic_sequence(one_tile)


def test_stop_mosaic_requests_stop_from_rotation_too() -> None:
    service = ready_service()
    rotation = service.axes[AxisName.ROTATION]
    assert isinstance(rotation, SimulatedMotionAxis)

    service.stop_mosaic()

    assert rotation.status().busy is False
