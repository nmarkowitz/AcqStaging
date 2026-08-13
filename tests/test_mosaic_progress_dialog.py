from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QScrollArea
from pytestqt.qtbot import QtBot

from acq_staging.gui.mosaic_progress_dialog import (
    COMPLETE_COLOR,
    CURRENT_COLOR,
    RESUMED_COLOR,
    MosaicOverview,
    MosaicProgressDialog,
)
from acq_staging.models import (
    AxisName,
    MosaicOrientation,
    MosaicProgress,
    MosaicSequenceSpec,
    MosaicTile,
    MosaicTilePhase,
    MosaicTilingSpec,
    Pose,
)


def sequence(
    resume_tiling: int = 1,
    resume_tile: int = 1,
    run_tiling_two: bool = True,
) -> MosaicSequenceSpec:
    first = MosaicTilingSpec.from_endpoints(1.0, 1.0, 0.0, 0.0, 2.0, 1.0, 5.0, 0.0)
    second = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 10.0, 10.0, 12.0, 11.0, 6.0, 45.0
    )
    return MosaicSequenceSpec(
        first,
        second,
        20.0,
        resume_tiling,
        resume_tile,
        run_tiling_two,
    )


def progress(
    number: int,
    phase: MosaicTilePhase,
    tiling_number: int = 1,
) -> MosaicProgress:
    # Match progress coordinates and overall numbering to the selected tiling.
    offset = 0 if tiling_number == 1 else 6
    tile = MosaicTile(
        float(number - 1),
        0.0,
        5.0 if tiling_number == 1 else 6.0,
        0.0 if tiling_number == 1 else 45.0,
        number - 1,
        0,
        tiling_number - 1,
    )
    return MosaicProgress(
        tiling_number,
        number,
        6,
        offset + number,
        12,
        tile,
        phase,
    )


def test_single_view_colors_tiles_and_switches_to_current_tiling(qtbot: QtBot) -> None:
    view = MosaicProgressDialog(sequence())
    qtbot.addWidget(view)

    view.update_progress(progress(2, MosaicTilePhase.CURRENT))
    assert len(view.findChildren(MosaicOverview)) == 1
    assert view.overview.tile_color((1, 0)) == CURRENT_COLOR
    assert "Tiling 1" in view.status_label.text()

    view.update_progress(progress(2, MosaicTilePhase.COMPLETE))
    assert view.overview.tile_color((1, 0)) == COMPLETE_COLOR

    # The same widget is reset to Tiling 2 when its first progress arrives.
    view.update_progress(progress(1, MosaicTilePhase.CURRENT, tiling_number=2))
    assert view.overview.spec == sequence().tiling_two
    assert view.overview.tile_color((0, 0)) == CURRENT_COLOR
    assert "Tiling 2" in view.tiling_group.title()


def test_resume_from_tiling_two_marks_all_prior_work(qtbot: QtBot) -> None:
    view = MosaicProgressDialog(sequence(resume_tiling=2, resume_tile=2))
    qtbot.addWidget(view)
    view.update_pose(Pose(1.0, 2.0, 3.0, 4.0))

    assert view.axis_labels[AxisName.X].text() == "1.0000"
    assert view.overview.spec == sequence().tiling_two
    assert view.overview.tile_color((0, 0)) == RESUMED_COLOR
    assert view.cancel_button.isEnabled()


def test_single_tiling_run_reports_single_completion(qtbot: QtBot) -> None:
    view = MosaicProgressDialog(sequence(run_tiling_two=False))
    qtbot.addWidget(view)

    view.mark_finished()

    assert view.status_label.text() == "Single-tiling mosaic run complete"


def test_pause_continue_and_cancel_controls_follow_run_state(qtbot: QtBot) -> None:
    view = MosaicProgressDialog(sequence())
    qtbot.addWidget(view)

    assert view.pause_button.isEnabled()
    assert not view.continue_button.isEnabled()
    assert view.cancel_button.isEnabled()

    # Pause immediately changes the visible controls while the service reaches a boundary.
    with qtbot.waitSignal(view.pause_requested):
        view.pause_button.click()
    assert not view.pause_button.isEnabled()
    assert view.continue_button.isEnabled()
    assert view.cancel_button.isEnabled()
    assert "Pause requested" in view.status_label.text()

    # Continue restores the running state without disabling cancellation.
    with qtbot.waitSignal(view.continue_requested):
        view.continue_button.click()
    assert view.pause_button.isEnabled()
    assert not view.continue_button.isEnabled()
    assert view.cancel_button.isEnabled()

    view.mark_finished()
    assert not view.pause_button.isEnabled()
    assert not view.continue_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_large_tilings_fit_without_scroll_or_child_tiles(qtbot: QtBot) -> None:
    first = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 0.0, 0.0, 159.0, 99.0, 5.0, 0.0
    )
    second = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 0.0, 0.0, 159.0, 99.0, 6.0, 45.0
    )
    view = MosaicProgressDialog(MosaicSequenceSpec(first, second, 20.0))
    view.resize(1000, 600)
    qtbot.addWidget(view)
    view.show()

    assert view.findChild(QScrollArea) is None
    assert len(view.findChildren(MosaicOverview)) == 1
    last = view.overview.tile_rect((159, 99))
    assert last.width() > 0 and last.height() > 0
    assert view.overview.rect().contains(last.center().toPoint())


def test_overview_uses_logical_rows_and_columns_when_axes_are_swapped(
    qtbot: QtBot,
) -> None:
    orientation = MosaicOrientation(AxisName.Y, 1, AxisName.X, -1)
    first = MosaicTilingSpec.from_endpoints(
        1.0, 1.0, 0.0, 10.0, 1.0, 12.0, 5.0, 0.0, orientation
    )
    view = MosaicProgressDialog(MosaicSequenceSpec(first, first, 20.0))
    qtbot.addWidget(view)

    # Three physical columns come from controller Y and two rows come from X.
    assert first.logical_tile_counts == (3, 2)
    assert view.overview._traversal_number((2, 0)) == 3
    assert view.overview._traversal_number((2, 1)) == 4
    assert view.overview._key_for_number(6) == (0, 1)
    assert view.overview.tile_rect((2, 1)).isValid()


def test_overview_paints_completed_and_current_shading(qtbot: QtBot) -> None:
    view = MosaicProgressDialog(sequence())
    view.resize(900, 550)
    qtbot.addWidget(view)
    view.show()
    view.update_progress(progress(1, MosaicTilePhase.COMPLETE))
    view.update_progress(progress(2, MosaicTilePhase.ACQUIRING))
    image = view.overview.grab().toImage()
    complete = QColor(image.pixel(view.overview.tile_rect((0, 0)).center().toPoint()))
    current = QColor(image.pixel(view.overview.tile_rect((1, 0)).center().toPoint()))

    assert complete.name() == COMPLETE_COLOR
    assert current.name() == CURRENT_COLOR


def test_timing_appears_after_two_completed_tiles_and_updates(qtbot: QtBot) -> None:
    now = [0.0]
    view = MosaicProgressDialog(sequence(), clock=lambda: now[0])
    qtbot.addWidget(view)

    # Start timing on the first active tile but keep both labels hidden.
    view.update_progress(progress(1, MosaicTilePhase.CURRENT))
    now[0] = 5.0
    view.update_progress(progress(1, MosaicTilePhase.COMPLETE))
    assert view.elapsed_label.isHidden()
    assert view.remaining_label.isHidden()

    # Two completed acquisitions provide the first average-duration estimate.
    now[0] = 10.0
    view.update_progress(progress(2, MosaicTilePhase.COMPLETE))
    assert not view.elapsed_label.isHidden()
    assert view.elapsed_label.text() == "Elapsed: 00:00:10"
    assert view.remaining_label.text() == "Estimated remaining: 00:00:20"

    # A slower recent tile raises the weighted estimate instead of diluting it globally.
    now[0] = 19.0
    view.update_progress(progress(3, MosaicTilePhase.COMPLETE))
    assert view.elapsed_label.text() == "Elapsed: 00:00:19"
    assert view.remaining_label.text() == "Estimated remaining: 00:00:21"
    assert "eight recent" in view.remaining_label.toolTip()

    # Automatic transition resets estimates until two Tiling 2 tiles complete.
    view.update_progress(progress(1, MosaicTilePhase.CURRENT, tiling_number=2))
    assert view.elapsed_label.isHidden()
    assert view.remaining_label.isHidden()
