"""Attached axis-position and current-mosaic progress viewer."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from time import monotonic

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from acq_staging.models import (
    HOME_ORDER,
    AxisName,
    MosaicProgress,
    MosaicSequenceSpec,
    MosaicTilePhase,
    MosaicTilingSpec,
    Pose,
)

PENDING_COLOR = "#b0b0b0"
CURRENT_COLOR = "#f6a623"
COMPLETE_COLOR = "#4caf50"
RESUMED_COLOR = "#6f8faf"


class MosaicOverview(QWidget):
    """Paint one complete XY tiling as a fitted shaded rectangle."""

    MARGIN = 8.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        self.spec: MosaicTilingSpec | None = None
        self.start_tile = 1
        self.current_tile: int | None = None
        self.completed_through = 0

    def sizeHint(self) -> QSize:
        """Provide useful initial space for one complete tiling."""
        return QSize(720, 320)

    def prepare_run(self, spec: MosaicTilingSpec, start_tile: int = 1) -> None:
        """Reset shading for one tiling, including restart-skipped tiles."""
        if start_tile < 1 or start_tile > spec.total_tiles + 1:
            raise ValueError("overview start tile is outside the tiling")
        self.spec = spec
        self.start_tile = start_tile
        self.current_tile = None
        self.completed_through = start_tile - 1
        self.update()

    def update_progress(self, progress: MosaicProgress) -> None:
        """Highlight an active tile or advance the completed boundary."""
        if progress.phase is MosaicTilePhase.COMPLETE:
            self.completed_through = max(self.completed_through, progress.number)
            self.current_tile = None
        else:
            self.current_tile = progress.number
        self.update()

    def mark_finished(self) -> None:
        """Shade every tile executed by this run complete."""
        if self.spec is not None:
            self.completed_through = self.spec.total_tiles
        self.current_tile = None
        self.update()

    def tile_color(self, key: tuple[int, int]) -> str:
        """Return the semantic shading for one logical XY tile."""
        number = self._traversal_number(key)
        if number < self.start_tile:
            return RESUMED_COLOR
        if number == self.current_tile:
            return CURRENT_COLOR
        if number <= self.completed_through:
            return COMPLETE_COLOR
        return PENDING_COLOR

    def tile_rect(self, key: tuple[int, int]) -> QRectF:
        """Return the fitted on-screen rectangle for one logical XY tile."""
        spec = self._require_spec()
        x_index, y_index = key
        if not (
            0 <= x_index < spec.tiles_horizontal
            and 0 <= y_index < spec.tiles_vertical
        ):
            raise IndexError("mosaic tile index is outside the overview")
        plane = self._plane_rect()
        cell_width = plane.width() / spec.tiles_horizontal
        cell_height = plane.height() / spec.tiles_vertical
        return QRectF(
            plane.left() + x_index * cell_width,
            plane.top() + y_index * cell_height,
            cell_width,
            cell_height,
        )

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """Render pending, resumed, completed, and active areas without tile widgets."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        spec = self.spec
        if spec is None:
            painter.setPen(QColor("#666666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No tiling configured")
            return

        # A gray base represents all pending positions before range shading is applied.
        plane = self._plane_rect()
        painter.fillRect(plane, QColor(PENDING_COLOR))
        self._paint_range(painter, 1, self.start_tile - 1, RESUMED_COLOR)
        self._paint_range(
            painter, self.start_tile, self.completed_through, COMPLETE_COLOR
        )
        self._paint_grid(painter, plane)

        # A minimum-size outline keeps the active location visible in very large grids.
        if self.current_tile is not None:
            active = self.tile_rect(self._key_for_number(self.current_tile))
            painter.fillRect(active, QColor(CURRENT_COLOR))
            marker = active
            if active.width() < 8.0 or active.height() < 8.0:
                marker = QRectF(active.center().x() - 4.0, active.center().y() - 4.0, 8.0, 8.0)
            painter.setPen(QPen(QColor("#222222"), 2.0))
            painter.drawRect(marker)

    def _plane_rect(self) -> QRectF:
        """Fit the whole XY grid while preserving square tile cells."""
        spec = self._require_spec()
        bounds = QRectF(self.rect()).adjusted(
            self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN
        )
        cell_size = max(
            min(
                bounds.width() / spec.tiles_horizontal,
                bounds.height() / spec.tiles_vertical,
            ),
            0.000001,
        )
        width = cell_size * spec.tiles_horizontal
        height = cell_size * spec.tiles_vertical
        return QRectF(
            bounds.left() + (bounds.width() - width) / 2.0,
            bounds.top() + (bounds.height() - height) / 2.0,
            width,
            height,
        )

    def _paint_range(self, painter: QPainter, first: int, last: int, color: str) -> None:
        """Shade a serpentine traversal range with two partial and one full-row fill."""
        spec = self._require_spec()
        if first > last or last < 1 or first > spec.total_tiles:
            return
        first = max(first, 1) - 1
        last = min(last, spec.total_tiles) - 1
        first_row, first_offset = divmod(first, spec.tiles_horizontal)
        last_row, last_offset = divmod(last, spec.tiles_horizontal)
        plane = self._plane_rect()
        self._fill_row(
            painter,
            plane,
            first_row,
            first_offset,
            spec.tiles_horizontal - 1 if first_row < last_row else last_offset,
            color,
        )
        if last_row - first_row > 1:
            cell_height = plane.height() / spec.tiles_vertical
            painter.fillRect(
                QRectF(
                    plane.left(),
                    plane.top() + (first_row + 1) * cell_height,
                    plane.width(),
                    (last_row - first_row - 1) * cell_height,
                ),
                QColor(color),
            )
        if last_row > first_row:
            self._fill_row(painter, plane, last_row, 0, last_offset, color)

    def _fill_row(
        self,
        painter: QPainter,
        plane: QRectF,
        row: int,
        first_offset: int,
        last_offset: int,
        color: str,
    ) -> None:
        """Shade one contiguous traversal segment, mirroring odd serpentine rows."""
        spec = self._require_spec()
        if row % 2:
            first_x = spec.tiles_horizontal - 1 - last_offset
            last_x = spec.tiles_horizontal - 1 - first_offset
        else:
            first_x = first_offset
            last_x = last_offset
        cell_width = plane.width() / spec.tiles_horizontal
        cell_height = plane.height() / spec.tiles_vertical
        painter.fillRect(
            QRectF(
                plane.left() + first_x * cell_width,
                plane.top() + row * cell_height,
                (last_x - first_x + 1) * cell_width,
                cell_height,
            ),
            QColor(color),
        )

    def _paint_grid(self, painter: QPainter, plane: QRectF) -> None:
        """Draw individual boundaries only while cells remain visually useful."""
        spec = self._require_spec()
        cell_width = plane.width() / spec.tiles_horizontal
        cell_height = plane.height() / spec.tiles_vertical
        painter.setPen(QPen(QColor("#666666"), 0.5))
        painter.drawRect(plane)
        if (
            cell_width < 3.0
            or cell_height < 3.0
            or spec.tiles_horizontal + spec.tiles_vertical > 1000
        ):
            return
        for x_index in range(1, spec.tiles_horizontal):
            x = plane.left() + x_index * cell_width
            painter.drawLine(int(x), int(plane.top()), int(x), int(plane.bottom()))
        for y_index in range(1, spec.tiles_vertical):
            y = plane.top() + y_index * cell_height
            painter.drawLine(int(plane.left()), int(y), int(plane.right()), int(y))

    def _traversal_number(self, key: tuple[int, int]) -> int:
        """Convert an XY index into its one-based serpentine traversal number."""
        spec = self._require_spec()
        x_index, y_index = key
        x_order = (
            x_index
            if y_index % 2 == 0
            else spec.tiles_horizontal - 1 - x_index
        )
        return y_index * spec.tiles_horizontal + x_order + 1

    def _key_for_number(self, number: int) -> tuple[int, int]:
        """Convert a one-based serpentine number back into an XY index."""
        spec = self._require_spec()
        if number < 1 or number > spec.total_tiles:
            raise IndexError("mosaic traversal number is outside the overview")
        y_index, x_order = divmod(number - 1, spec.tiles_horizontal)
        x_index = (
            x_order
            if y_index % 2 == 0
            else spec.tiles_horizontal - 1 - x_order
        )
        return x_index, y_index

    def _require_spec(self) -> MosaicTilingSpec:
        """Return active geometry or fail invalid inspection calls."""
        if self.spec is None:
            raise RuntimeError("no tiling is configured")
        return self.spec


class AxisMosaicView(QWidget):
    """Display controller-home axis positions and the active tiling overview."""

    pause_requested = pyqtSignal()
    continue_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(
        self,
        run_spec: MosaicSequenceSpec | None = None,
        parent: QWidget | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(parent)
        self.run_spec: MosaicSequenceSpec | None = None
        self._clock = clock
        self._active_tiling: int | None = None
        self._tiling_started_at: float | None = None
        self._last_completion_at: float | None = None
        self._completed_tiles: set[int] = set()
        self._recent_tile_durations_s: deque[float] = deque(maxlen=8)
        self._pause_requested = False
        self.status_label = QLabel("No mosaic configured")
        self.status_label.setObjectName("mosaic_progress_status")
        self.axis_labels: dict[AxisName, QLabel] = {}

        # The header keeps all controller-home positions visible throughout acquisition.
        axes = QHBoxLayout()
        for axis in HOME_ORDER:
            column = QVBoxLayout()
            name = QLabel(axis.value.upper())
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("—")
            value.setObjectName(f"axis_view_{axis.value}")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.axis_labels[axis] = value
            column.addWidget(name)
            column.addWidget(value)
            axes.addLayout(column)

        # Pause and Continue are mutually exclusive while Cancel remains available.
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("pause_mosaic")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._request_pause)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("continue_mosaic")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._request_continue)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancel_mosaic")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        header = QHBoxLayout()
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(self.pause_button)
        header.addWidget(self.continue_button)
        header.addWidget(self.cancel_button)

        # A single overview is reused if the automatic sequence changes tilings.
        self.overview = MosaicOverview(self)
        self.tiling_group = QGroupBox("Current tiling", self)
        tiling_layout = QVBoxLayout(self.tiling_group)
        tiling_layout.addWidget(self.overview)

        # Timing stays hidden until two completed tiles provide a useful average.
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("mosaic_elapsed_time")
        self.remaining_label = QLabel()
        self.remaining_label.setObjectName("mosaic_remaining_time")
        self.remaining_label.setToolTip(
            "Estimate uses a weighted average of up to eight recent completed tiles."
        )
        self.elapsed_label.hide()
        self.remaining_label.hide()
        timing = QHBoxLayout()
        timing.addWidget(self.elapsed_label)
        timing.addWidget(self.remaining_label)
        timing.addStretch()

        # A fixed legend documents the semantic shading shared by every tiling.
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Legend:"))
        for text, color in (
            ("Pending", PENDING_COLOR),
            ("Current", CURRENT_COLOR),
            ("Complete", COMPLETE_COLOR),
            ("Prior to restart", RESUMED_COLOR),
        ):
            legend.addWidget(self._legend_item(text, color))
        legend.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Controller-home pose [x mm, y mm, z mm, theta deg]"))
        layout.addLayout(axes)
        layout.addLayout(header)
        layout.addLayout(timing)
        layout.addLayout(legend)
        layout.addWidget(self.tiling_group, 1)
        if run_spec is not None:
            self.prepare_run(run_spec)

    def update_pose(self, pose: Pose) -> None:
        """Display all four axes in controller-home coordinates."""
        for axis, position in zip(HOME_ORDER, pose.as_vector(), strict=True):
            self.axis_labels[axis].setText("—" if position is None else f"{position:.4f}")

    def prepare_run(self, run_spec: MosaicSequenceSpec) -> None:
        """Prepare the first tiling that this full run will execute."""
        self.run_spec = run_spec
        self._active_tiling = None
        self._prepare_overview(run_spec.resume_tiling, run_spec.resume_tile)
        self._reset_timing()
        self.status_label.setText(
            f"Preparing full run from Tiling {run_spec.resume_tiling}, "
            f"tile {run_spec.resume_tile}"
        )
        self._pause_requested = False
        self._set_run_controls(active=True, paused=False)

    def update_progress(self, progress: MosaicProgress) -> None:
        """Show only the active tiling and update timing after completed tiles."""
        # The first progress signal starts timing after the tiling transition has finished.
        if progress.tiling_number != self._active_tiling:
            self._begin_tiling(progress.tiling_number, progress.number)
        self.overview.update_progress(progress)
        actions = {
            MosaicTilePhase.CURRENT: "Running",
            MosaicTilePhase.ACQUIRING: "Awaiting acquisition for",
            MosaicTilePhase.COMPLETE: "Completed",
        }
        # Preserve pause feedback while an in-flight step reports its final progress.
        if not self._pause_requested:
            self.status_label.setText(
                f"{actions[progress.phase]} Tiling {progress.tiling_number}, "
                f"tile {progress.number}/{progress.total}"
            )
        # Completion occurs after dwell or DAQ acknowledgement, so it measures acquisition time.
        if progress.phase is MosaicTilePhase.COMPLETE:
            self._update_timing(progress)

    def mark_finished(self) -> None:
        """Complete the visible overview and show successful full-run completion."""
        self.overview.mark_finished()
        if self.run_spec is not None and self.run_spec.run_tiling_two:
            self.status_label.setText("Two-tiling mosaic run complete")
        else:
            self.status_label.setText("Single-tiling mosaic run complete")
        self._pause_requested = False
        self._set_run_controls(active=False, paused=False)

    def mark_failed(self, message: str) -> None:
        """Keep final shading visible and show the terminal reason."""
        if "cancel" in message.lower():
            self.status_label.setText("Mosaic cancelled")
        else:
            self.status_label.setText(f"Mosaic failed: {message}")
        self._pause_requested = False
        self._set_run_controls(active=False, paused=False)

    def _request_pause(self) -> None:
        """Show immediate feedback and ask orchestration to pause safely."""
        self._pause_requested = True
        self.status_label.setText("Pause requested; finishing the current step")
        self._set_run_controls(active=True, paused=True)
        self.pause_requested.emit()

    def _request_continue(self) -> None:
        """Restore running controls and release the service pause gate."""
        self._pause_requested = False
        self.status_label.setText("Continuing mosaic")
        self._set_run_controls(active=True, paused=False)
        self.continue_requested.emit()

    def _set_run_controls(self, *, active: bool, paused: bool) -> None:
        """Keep the three mosaic actions consistent with the visible run state."""
        self.pause_button.setEnabled(active and not paused)
        self.continue_button.setEnabled(active and paused)
        self.cancel_button.setEnabled(active)

    def _begin_tiling(self, tiling_number: int, start_tile: int) -> None:
        """Switch the reusable overview and reset estimates for the new tiling."""
        self._active_tiling = tiling_number
        self._prepare_overview(tiling_number, start_tile)
        self._reset_timing()
        self._tiling_started_at = self._clock()

    def _prepare_overview(self, tiling_number: int, start_tile: int) -> None:
        """Load one tiling geometry and its restart shading into the viewer."""
        if self.run_spec is None:
            raise RuntimeError("no mosaic run is configured")
        tiling = self.run_spec.tilings[tiling_number - 1]
        self.overview.prepare_run(tiling, start_tile)
        self.tiling_group.setTitle(
            f"Tiling {tiling_number} — Z {tiling.z_mm:.4f} mm, "
            f"Rotation {tiling.rotation_deg:.4f}°"
        )

    def _reset_timing(self) -> None:
        """Hide stale estimates until two tiles complete in the active tiling."""
        self._tiling_started_at = None
        self._last_completion_at = None
        self._completed_tiles.clear()
        self._recent_tile_durations_s.clear()
        self.elapsed_label.hide()
        self.remaining_label.hide()

    def _update_timing(self, progress: MosaicProgress) -> None:
        """Estimate remaining time from recently completed tile durations."""
        # Duplicate GUI delivery must not count one acquisition more than once.
        if progress.number in self._completed_tiles or self._tiling_started_at is None:
            return
        completed_at = self._clock()
        interval_start = (
            self._tiling_started_at
            if self._last_completion_at is None
            else self._last_completion_at
        )
        self._recent_tile_durations_s.append(max(completed_at - interval_start, 0.0))
        self._last_completion_at = completed_at
        self._completed_tiles.add(progress.number)
        if len(self._completed_tiles) < 2:
            return

        # Linear weights favor recent tiles while an eight-tile window rejects old pacing.
        weights = range(1, len(self._recent_tile_durations_s) + 1)
        weighted_total = sum(
            duration * weight
            for duration, weight in zip(
                self._recent_tile_durations_s, weights, strict=True
            )
        )
        weight_total = sum(range(1, len(self._recent_tile_durations_s) + 1))
        average_tile_s = weighted_total / weight_total
        elapsed_s = max(completed_at - self._tiling_started_at, 0.0)
        remaining_tiles = max(progress.total - progress.number, 0)
        remaining_s = average_tile_s * remaining_tiles
        self.elapsed_label.setText(f"Elapsed: {self._format_duration(elapsed_s)}")
        self.remaining_label.setText(
            f"Estimated remaining: {self._format_duration(remaining_s)}"
        )
        self.elapsed_label.show()
        self.remaining_label.show()

    @staticmethod
    def _format_duration(duration_s: float) -> str:
        """Format a non-negative duration as hours, minutes, and seconds."""
        total_seconds = max(round(duration_s), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _legend_item(text: str, color: str) -> QLabel:
        """Create one compact semantic color label."""
        label = QLabel(text)
        label.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555; padding: 4px 10px;"
        )
        return label


# Preserve the older import name for callers while the widget remains embedded.
MosaicProgressDialog = AxisMosaicView
