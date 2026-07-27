import pytest
from PyQt6.QtWidgets import QDoubleSpinBox
from pytestqt.qtbot import QtBot

from acq_staging.configuration import default_simulation_config
from acq_staging.gui.mosaic_setup_dialog import MosaicSetupDialog
from acq_staging.models import AxisName, LimitsConfig, Pose


def test_setup_exposes_two_independent_xy_tilings_without_z_steps(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(
        config.mosaic, config.components, Pose(1.0, 2.0, 3.0, 4.0)
    )
    qtbot.addWidget(dialog)

    assert len(dialog.fields) == 2
    assert dialog.fields[0]["rotation"].value() == 0.0
    assert not dialog.fields[0]["rotation"].isEnabled()
    assert dialog.fields[1]["rotation"].value() == 4.0
    assert dialog.findChild(QDoubleSpinBox, "mosaic_step_z_mm") is None


def test_setup_builds_independent_geometry_and_rounds_endpoints(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(
        config.mosaic, config.components, Pose(0.0, 0.0, 5.0, 0.0)
    )
    qtbot.addWidget(dialog)
    first, second = dialog.fields
    first["step_x"].setValue(0.3)
    first["stop_x"].setValue(1.0)
    assert first["stop_x"].value() == 1.0

    # The preview may resolve live, but the typed field changes only after editing ends.
    first["stop_x"].editingFinished.emit()
    assert first["stop_x"].value() == pytest.approx(1.2)
    second["step_y"].setValue(2.0)
    second["stop_y"].setValue(4.0)
    second["z"].setValue(8.0)
    second["rotation"].setValue(45.0)
    dialog.safe_z_box.setValue(20.0)

    spec = dialog.build_spec()

    assert spec.tiling_one.end_x_mm == pytest.approx(1.2)
    assert spec.tiling_two.tile_counts == (1, 3)
    assert spec.tiling_two.z_mm == 8.0
    assert spec.tiling_two.rotation_deg == 45.0
    assert spec.safe_transition_z_mm == 20.0


def test_resume_selector_uses_selected_tiling_tile_count(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(
        config.mosaic, config.components, Pose(0.0, 0.0, 0.0, 0.0)
    )
    qtbot.addWidget(dialog)
    dialog.fields[1]["stop_x"].setValue(5.6)
    dialog.resume_tiling.setCurrentIndex(1)
    dialog.resume_tile.setValue(3)

    spec = dialog.build_spec()

    assert spec.resume_tiling == 2
    assert spec.resume_tile == 3
    assert dialog.resume_tile.maximum() == spec.tiling_two.total_tiles


def test_setup_rejects_rounded_endpoint_beyond_live_limit(qtbot: QtBot) -> None:
    config = default_simulation_config()
    dialog = MosaicSetupDialog(
        config.mosaic,
        config.components,
        Pose(0.0, 0.0, 0.0, 0.0),
        {AxisName.X: LimitsConfig(0.0, 1.0)},
    )
    qtbot.addWidget(dialog)
    dialog.fields[0]["start_x"].setValue(0.8)
    dialog.fields[0]["stop_x"].setValue(1.0)
    dialog.fields[0]["step_x"].setValue(0.3)
    dialog.fields[0]["stop_x"].editingFinished.emit()

    # An outward endpoint beyond the live limit remains visible for correction.
    assert dialog.fields[0]["stop_x"].value() == 1.0
    assert "outside device limits" in dialog.sequence_summary.text()
    with pytest.raises(ValueError, match="outside device limits"):
        dialog.build_spec()
