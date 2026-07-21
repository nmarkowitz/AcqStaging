from acq_staging.models import HOME_ORDER, AxisName, Pose


def test_home_order_is_required_sequence() -> None:
    assert HOME_ORDER == (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)


def test_pose_vector_order() -> None:
    assert Pose(1.0, 2.0, 3.0, 4.0).as_vector() == (1.0, 2.0, 3.0, 4.0)
