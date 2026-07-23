import pytest

from acq_staging.models import HOME_ORDER, AxisName, MosaicInputMode, MosaicRunSpec, Pose


def test_home_order_is_required_sequence() -> None:
    assert HOME_ORDER == (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)


def test_pose_vector_order() -> None:
    assert Pose(1.0, 2.0, 3.0, 4.0).as_vector() == (1.0, 2.0, 3.0, 4.0)


def test_count_mosaic_uses_positive_steps_from_an_observed_origin() -> None:
    spec = MosaicRunSpec.from_tile_counts(1.0, 2.0, 0.0, 3, 2, 1)

    assert spec.input_mode is MosaicInputMode.TILE_COUNTS
    assert spec.total_tiles == 6
    assert spec.extents_mm == (2.0, 2.0, 0.0)
    assert spec.coordinate(0, 2, 10.0) == 12.0


def test_endpoint_mosaic_infers_direction_and_includes_both_ends() -> None:
    spec = MosaicRunSpec.from_endpoints(
        1.0,
        2.0,
        0.5,
        3.0,
        -2.0,
        5.0,
        1.0,
        2.0,
        5.0,
    )

    assert spec.tile_counts == (3, 3, 1)
    assert spec.extents_mm == (-2.0, 4.0, 0.0)
    assert [spec.coordinate(0, index, 99.0) for index in range(3)] == [3.0, 2.0, 1.0]
    assert spec.coordinate(1, 2, 99.0) == 2.0


def test_endpoint_mosaic_rejects_a_partial_final_step() -> None:
    with pytest.raises(ValueError, match="exact multiple"):
        MosaicRunSpec.from_endpoints(
            0.3,
            1.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
        )
