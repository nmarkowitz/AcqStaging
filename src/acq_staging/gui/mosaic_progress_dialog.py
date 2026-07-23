"""Visual mosaic tile progress window."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from acq_staging.models import MosaicProgress, MosaicRunSpec, MosaicTilePhase

PENDING_COLOR = "#b0b0b0"
CURRENT_COLOR = "#f6a623"
COMPLETE_COLOR = "#4caf50"


class MosaicProgressDialog(QDialog):
    """Display pending, active, and completed tiles for every Z plane."""

    def __init__(self, run_spec: MosaicRunSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mosaic Tiling Progress")
        self.setModal(False)
        self.resize(760, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.status_label = QLabel("Preparing mosaic...")
        self.status_label.setObjectName("mosaic_progress_status")
        self.tiles: dict[tuple[int, int, int], QLabel] = {}

        # A fixed legend makes tile state readable without relying on status text.
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Legend:"))
        legend.addWidget(self._legend_item("Pending", PENDING_COLOR))
        legend.addWidget(self._legend_item("Current", CURRENT_COLOR))
        legend.addWidget(self._legend_item("Complete", COMPLETE_COLOR))
        legend.addStretch()

        content = QWidget()
        planes = QVBoxLayout(content)
        # Each Z plane receives a separate XY grid using serpentine traversal numbers.
        order = self._tile_order(run_spec)
        for z_index in range(run_spec.tiles_z):
            group = QGroupBox(f"Z plane {z_index + 1} of {run_spec.tiles_z}")
            grid = QGridLayout(group)
            grid.addWidget(QLabel("Y ↓ / X →"), 0, 0)
            for x_index in range(run_spec.tiles_x):
                grid.addWidget(QLabel(str(x_index)), 0, x_index + 1)
            for y_index in range(run_spec.tiles_y):
                grid.addWidget(QLabel(str(y_index)), y_index + 1, 0)
                for x_index in range(run_spec.tiles_x):
                    number = order[(x_index, y_index, z_index)]
                    tile = QLabel(str(number))
                    tile.setObjectName(f"mosaic_tile_{z_index}_{y_index}_{x_index}")
                    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    tile.setMinimumSize(46, 34)
                    tile.setToolTip(
                        f"Tile {number}: X index {x_index}, Y index {y_index}, "
                        f"Z index {z_index}"
                    )
                    self._set_color(tile, PENDING_COLOR)
                    self.tiles[(x_index, y_index, z_index)] = tile
                    grid.addWidget(tile, y_index + 1, x_index + 1)
            planes.addWidget(group)
        planes.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(legend)
        layout.addWidget(scroll)

    def update_progress(self, progress: MosaicProgress) -> None:
        """Apply one current or complete event to its tile."""
        key = (progress.tile.x_index, progress.tile.y_index, progress.tile.z_index)
        tile = self.tiles.get(key)
        if tile is None:
            return
        # Movement and acquisition keep the tile current until completion turns it green.
        color = COMPLETE_COLOR if progress.phase is MosaicTilePhase.COMPLETE else CURRENT_COLOR
        self._set_color(tile, color)
        actions = {
            MosaicTilePhase.CURRENT: "Running",
            MosaicTilePhase.ACQUIRING: "Awaiting acquisition for",
            MosaicTilePhase.COMPLETE: "Completed",
        }
        action = actions[progress.phase]
        self.status_label.setText(f"{action} tile {progress.number} of {progress.total}")

    def mark_finished(self) -> None:
        """Show successful completion after the worker exits."""
        self.status_label.setText("Mosaic complete")

    def mark_failed(self, message: str) -> None:
        """Keep the last tile visible and show the failure reason."""
        self.status_label.setText(f"Mosaic stopped: {message}")

    @staticmethod
    def _tile_order(run_spec: MosaicRunSpec) -> dict[tuple[int, int, int], int]:
        # Match service traversal so displayed numbers reproduce the execution order.
        order: dict[tuple[int, int, int], int] = {}
        number = 1
        for z_index in range(run_spec.tiles_z):
            for y_index in range(run_spec.tiles_y):
                x_indices = range(run_spec.tiles_x)
                if y_index % 2:
                    x_indices = range(run_spec.tiles_x - 1, -1, -1)
                for x_index in x_indices:
                    order[(x_index, y_index, z_index)] = number
                    number += 1
        return order

    @staticmethod
    def _legend_item(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555; padding: 4px 10px;"
        )
        return label

    @staticmethod
    def _set_color(tile: QLabel, color: str) -> None:
        # Store the semantic color as a property for tests and future accessibility work.
        tile.setProperty("tile_color", color)
        tile.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555; font-weight: bold;"
        )
