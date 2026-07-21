"""Reusable controls for one digital input or output component."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from acq_staging.configuration import DigitalIOConfig
from acq_staging.models import DigitalDirection, DigitalStatus


class DigitalPanel(QGroupBox):
    """Connection controls and per-pin state for one digital direction."""

    connect_requested = pyqtSignal(object)
    disconnect_requested = pyqtSignal(object)
    refresh_requested = pyqtSignal(object)
    write_requested = pyqtSignal(object, object)

    def __init__(self, direction: DigitalDirection, config: DigitalIOConfig) -> None:
        title = "Digital In" if direction is DigitalDirection.INPUT else "Digital Out"
        super().__init__(title)
        self.direction = direction
        self.config = config
        self.status_label = QLabel("Disconnected")
        self.pin_boxes: dict[int, QCheckBox] = {}

        connect = QPushButton("Connect")
        disconnect = QPushButton("Disconnect")
        refresh = QPushButton("Read Pins")
        apply = QPushButton("Apply Pin States")
        connect.clicked.connect(lambda: self.connect_requested.emit(direction))
        disconnect.clicked.connect(lambda: self.disconnect_requested.emit(direction))
        refresh.clicked.connect(lambda: self.refresh_requested.emit(direction))
        apply.clicked.connect(lambda: self.write_requested.emit(direction, self.output_states()))

        controls = QHBoxLayout()
        controls.addWidget(connect)
        controls.addWidget(disconnect)
        if direction is DigitalDirection.INPUT:
            controls.addWidget(refresh)
        else:
            controls.addWidget(apply)

        root = QVBoxLayout(self)
        root.addWidget(self.status_label)
        root.addLayout(controls)
        self.pin_layout = QGridLayout()
        root.addLayout(self.pin_layout)
        self._rebuild_pins()

    def apply_config(self, config: DigitalIOConfig) -> None:
        """Rebuild pin controls for a newly loaded profile."""
        self.config = config
        self._rebuild_pins()

    def update_status(self, status: DigitalStatus) -> None:
        """Display cached connection and pin states without vendor I/O."""
        text = "Connected" if status.connected else "Disconnected"
        if status.error:
            text = f"Error: {status.error}"
        self.status_label.setText(text)
        for line, box in self.pin_boxes.items():
            if line not in self.config.selected_lines:
                box.setText(f"Line {line}: NOT CONFIGURED")
                continue
            state = status.states.get(line)
            box.setText(f"Line {line}: {self._state_text(state)}")
            if self.direction is DigitalDirection.INPUT and state is not None:
                box.setChecked(state)

    def output_states(self) -> dict[int, bool]:
        """Return all configured output checkbox values as one atomic update."""
        return {
            line: self.pin_boxes[line].isChecked() for line in self.config.selected_lines
        }

    def _rebuild_pins(self) -> None:
        while self.pin_layout.count():
            item = self.pin_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self.pin_boxes.clear()
        selected = set(self.config.selected_lines)
        for line in range(8):
            configured = line in selected
            label = "UNKNOWN" if configured else "NOT CONFIGURED"
            box = QCheckBox(f"Line {line}: {label}")
            box.setObjectName(f"{self.direction.value}_line_{line}")
            box.setEnabled(configured and self.direction is DigitalDirection.OUTPUT)
            if not configured:
                box.setStyleSheet("color: gray;")
            self.pin_boxes[line] = box
            self.pin_layout.addWidget(box, line // 4, line % 4)
        self.setEnabled(self.config.enabled)

    @staticmethod
    def _state_text(state: bool | None) -> str:
        if state is None:
            return "UNKNOWN"
        return "HIGH" if state else "LOW"
