import pytest

from acq_staging.models import (
    HOME_ORDER,
    AxisName,
    MosaicSequenceSpec,
    MosaicTilingSpec,
    Pose,
)


def tiling(rotation: float = 0.0) -> MosaicTilingSpec:
    return MosaicTilingSpec.from_endpoints(
        1.0, 2.0, 3.0, -2.0, 1.0, 2.0, 5.0, rotation
    )


def test_home_order_is_required_sequence() -> None:
    assert HOME_ORDER == (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)


def test_pose_vector_order() -> None:
    assert Pose(1.0, 2.0, 3.0, 4.0).as_vector() == (1.0, 2.0, 3.0, 4.0)


def test_xy_tiling_infers_direction_and_holds_z_and_rotation() -> None:
    spec = tiling(35.0)

    assert spec.tile_counts == (3, 3)
    assert spec.extents_mm == (-2.0, 4.0)
    assert spec.z_mm == 5.0
    assert spec.rotation_deg == 35.0
    assert [spec.coordinate(0, index) for index in range(3)] == [3.0, 2.0, 1.0]


def test_xy_tiling_rounds_partial_endpoint_outward() -> None:
    spec = MosaicTilingSpec.from_endpoints(
        0.3, 1.0, 0.0, 0.0, 1.0, 0.0, 4.0, 0.0
    )

    assert spec.tiles_x == 5
    assert spec.end_x_mm == pytest.approx(1.2)


def test_xy_tiling_requires_positive_steps_even_for_single_tile() -> None:
    with pytest.raises(ValueError, match="X step"):
        MosaicTilingSpec.from_endpoints(0.0, 1.0, 2.0, 2.0, 2.0, 2.0, 4.0, 0.0)


def test_sequence_requires_zero_degree_first_tiling() -> None:
    with pytest.raises(ValueError, match="tiling 1 rotation"):
        MosaicSequenceSpec(tiling(10.0), tiling(45.0), 20.0)


def test_sequence_resume_tile_must_fit_selected_tiling() -> None:
    with pytest.raises(ValueError, match="between 1 and 9"):
        MosaicSequenceSpec(tiling(), tiling(45.0), 20.0, resume_tiling=2, resume_tile=10)


def test_sequence_can_disable_second_tiling() -> None:
    spec = MosaicSequenceSpec(tiling(), tiling(45.0), 20.0, run_tiling_two=False)

    assert spec.tilings == (spec.tiling_one,)
    assert spec.total_tiles == spec.tiling_one.total_tiles
    with pytest.raises(ValueError, match="tiling 2 is disabled"):
        MosaicSequenceSpec(
            tiling(),
            tiling(45.0),
            20.0,
            resume_tiling=2,
            run_tiling_two=False,
        )
