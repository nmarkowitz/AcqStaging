import pytest
from pytestqt.qtbot import QtBot

from acq_staging.configuration import default_simulation_config
from acq_staging.gui.mosaic_setup_dialog import MosaicSetupDialog
from acq_staging.models import AxisName, MosaicInputMode, Pose


def test_mosaic_setup_defaults_to_profile_counts_and_transient_steps(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(
        config.mosaic,
        config.components,
        Pose(1.0, 2.0, 3.0, 0.0),
    )
    qtbot.addWidget(dialog)
    dialog.step_boxes[AxisName.X].setValue(1.5)

    spec = dialog.build_spec()

    assert spec.input_mode is MosaicInputMode.TILE_COUNTS
    assert spec.steps == (
        1.5,
        config.mosaic.step_y_mm,
        config.mosaic.step_z_mm,
    )
    assert config.mosaic.step_x_mm == 2.8
    assert spec.tile_counts == (
        config.mosaic.tiles_x,
        config.mosaic.tiles_y,
        config.mosaic.tiles_z,
    )
    assert dialog.count_group.isEnabled()
    assert not dialog.endpoint_group.isEnabled()


def test_mosaic_setup_builds_descending_endpoint_geometry(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(config.mosaic, config.components, Pose(3.0, 0.0, 0.0, 0.0))
    qtbot.addWidget(dialog)
    dialog.endpoint_mode.setChecked(True)
    dialog.step_boxes[AxisName.X].setValue(1.0)
    dialog.step_boxes[AxisName.Y].setValue(1.0)
    dialog.step_boxes[AxisName.Z].setValue(0.0)
    dialog.begin_boxes[AxisName.X].setValue(3.0)
    dialog.end_boxes[AxisName.X].setValue(1.0)
    dialog.begin_boxes[AxisName.Y].setValue(0.0)
    dialog.end_boxes[AxisName.Y].setValue(2.0)
    dialog.begin_boxes[AxisName.Z].setValue(0.0)
    dialog.end_boxes[AxisName.Z].setValue(0.0)

    spec = dialog.build_spec()

    assert spec.input_mode is MosaicInputMode.ENDPOINTS
    assert spec.tile_counts == (3, 3, 1)
    assert spec.extents_mm == (-2.0, 2.0, 0.0)


def test_mosaic_setup_reports_nondivisible_endpoint_span(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(config.mosaic, config.components, Pose(0.0, 0.0, 0.0, 0.0))
    qtbot.addWidget(dialog)
    dialog.endpoint_mode.setChecked(True)
    dialog.step_boxes[AxisName.X].setValue(0.3)
    dialog.begin_boxes[AxisName.X].setValue(0.0)
    dialog.end_boxes[AxisName.X].setValue(1.0)

    with pytest.raises(ValueError, match="exact multiple"):
        dialog.build_spec()
    assert "Invalid geometry" in dialog.summary.text()
