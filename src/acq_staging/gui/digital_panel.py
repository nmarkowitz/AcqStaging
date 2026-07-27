"""Reusable controls for one digital input or output component."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from acq_staging.configuration import DigitalIOConfig
from acq_staging.models import DigitalDirection, DigitalStatus


class DigitalPinButton(QPushButton):
    """Circular numbered digital-line control with a color-coded state."""

    _STYLE = """
        QPushButton {
            background-color: #d6d6d6;
            border: 2px solid #666666;
            border-radius: 24px;
            color: #202124;
            font-size: 15px;
            font-weight: bold;
        }
        QPushButton[pinState="high"] {
            background-color: #2e9d45;
            border-color: #176b2a;
            color: white;
        }
        QPushButton[pinState="low"] {
            background-color: #d6d6d6;
            border-color: #666666;
            color: #202124;
        }
        QPushButton[pinState="unknown"] {
            background-color: #f2b84b;
            border-color: #9a6b13;
            color: #202124;
        }
        QPushButton[pinState="unconfigured"] {
            background-color: #eeeeee;
            border-color: #aaaaaa;
            border-style: dashed;
            color: #999999;
        }
    """

    def __init__(self, line: int, writable: bool, configured: bool) -> None:
        super().__init__(str(line))
        self.line = line
        self.setObjectName(f"digital_line_{line}")
        self.setFixedSize(48, 48)
        self.setCheckable(writable and configured)
        self.setEnabled(writable and configured)
        self.setStyleSheet(self._STYLE)
        self.setAccessibleName(f"Digital line {line}")
        self.set_pin_state(None, configured=configured)

    def set_pin_state(self, state: bool | None, *, configured: bool = True) -> None:
        """Apply semantic state text and color without changing output selection."""
        state_name = (
            "unconfigured"
            if not configured
            else "unknown"
            if state is None
            else "high"
            if state
            else "low"
        )
        state_text = state_name.replace("unconfigured", "not configured").upper()
        self.setProperty("pinState", state_name)
        self.setToolTip(f"Line {self.line}: {state_text}")
        self.setAccessibleDescription(state_text)

        # Repolish after a dynamic-property change so the fill updates immediately.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()


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
        self.pin_boxes: dict[int, DigitalPinButton] = {}
        self._output_touched: set[int] = set()

        # Buttons emit intent only; DAQ work runs through the main-window worker path.
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
        root.addWidget(QLabel("Green = HIGH   Gray = LOW   Amber = UNKNOWN"))
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
        # Each configured circle reflects observed state without losing output selections.
        for line, box in self.pin_boxes.items():
            if line not in self.config.selected_lines:
                box.set_pin_state(None, configured=False)
                continue
            state = status.states.get(line)
            if self.direction is DigitalDirection.INPUT or state is not None:
                box.set_pin_state(state)
            elif line not in self._output_touched:
                box.set_pin_state(None)

    def output_states(self) -> dict[int, bool]:
        """Return all configured circular-control values as one atomic update."""
        return {
            line: self.pin_boxes[line].isChecked() for line in self.config.selected_lines
        }

    def _rebuild_pins(self) -> None:
        # Always render eight slots so unused channels remain visible but disabled.
        while self.pin_layout.count():
            item = self.pin_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self.pin_boxes.clear()
        self._output_touched.clear()
        selected = set(self.config.selected_lines)
        for line in range(8):
            configured = line in selected
            box = DigitalPinButton(
                line,
                self.direction is DigitalDirection.OUTPUT,
                configured,
            )
            box.setObjectName(f"{self.direction.value}_line_{line}")
            if configured and self.direction is DigitalDirection.OUTPUT:
                box.toggled.connect(
                    lambda checked, pin=line: self._show_output_request(pin, checked)
                )
            self.pin_boxes[line] = box
            self.pin_layout.addWidget(box, line // 4, line % 4)
        self.setEnabled(self.config.enabled)

    def _show_output_request(self, line: int, state: bool) -> None:
        """Color a selected output immediately while Apply remains the write action."""
        self._output_touched.add(line)
        self.pin_boxes[line].set_pin_state(state)
