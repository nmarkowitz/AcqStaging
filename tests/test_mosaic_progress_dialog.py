from PyQt6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from acq_staging.gui.mosaic_progress_dialog import (
    COMPLETE_COLOR,
    CURRENT_COLOR,
    PENDING_COLOR,
    MosaicProgressDialog,
)
from acq_staging.models import MosaicProgress, MosaicRunSpec, MosaicTile, MosaicTilePhase


def test_mosaic_progress_dialog_colors_current_and_complete_tiles(qtbot: QtBot) -> None:
    run_spec = MosaicRunSpec.from_tile_counts(1.0, 1.0, 1.0, 3, 2, 2)
    dialog = MosaicProgressDialog(run_spec)
    qtbot.addWidget(dialog)
    assert len(dialog.tiles) == 12
    assert all(tile.property("tile_color") == PENDING_COLOR for tile in dialog.tiles.values())

    tile = MosaicTile(1.0, 0.0, 0.0, 1, 0, 0)
    dialog.update_progress(MosaicProgress(2, 12, tile, MosaicTilePhase.CURRENT))
    label = dialog.tiles[(1, 0, 0)]
    assert label.property("tile_color") == CURRENT_COLOR
    assert dialog.status_label.text() == "Running tile 2 of 12"

    dialog.update_progress(MosaicProgress(2, 12, tile, MosaicTilePhase.COMPLETE))
    assert label.property("tile_color") == COMPLETE_COLOR
    legend = {label.text() for label in dialog.findChildren(QLabel)}
    assert {"Pending", "Current", "Complete"} <= legend
