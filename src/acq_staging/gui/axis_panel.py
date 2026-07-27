"""Reusable controls for one motion component."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
)

from acq_staging.configuration import ComponentConfig
from acq_staging.models import AxisName, ComponentStatus, LimitsConfig


class AxisPanel(QGroupBox):
    """Independent controls and state for one axis."""

    # Signals keep this widget vendor-neutral and delegate all operations to services.
    connect_requested = pyqtSignal(object)
    disconnect_requested = pyqtSignal(object)
    home_requested = pyqtSignal(object)
    stop_requested = pyqtSignal(object)
    absolute_requested = pyqtSignal(object, float)
    relative_requested = pyqtSignal(object, float)
    diagnostic_requested = pyqtSignal(object)
    identify_requested = pyqtSignal(object)
    motion_test_requested = pyqtSignal(object)

    def __init__(self, axis: AxisName, config: ComponentConfig) -> None:
        super().__init__(axis.value.upper())
        self.axis = axis
        self.config = config
        self.status_label = QLabel("Disconnected")
        self.position_label = QLabel("—")
        self.position_label.setObjectName(f"{axis.value}_position")

        # Hardware ranges are replaced by API-reported limits after connection.
        initial_limits = config.limits or LimitsConfig(-1_000_000.0, 1_000_000.0)
        self.target = QDoubleSpinBox()
        self.target.setRange(initial_limits.minimum, initial_limits.maximum)
        self.target.setDecimals(4)
        self.target.setValue(config.simulation_start)
        self.increment = QDoubleSpinBox()
        self.increment.setRange(0.0001, initial_limits.span)
        self.increment.setDecimals(4)
        self.increment.setValue(config.motion.jog_increment)

        connect = QPushButton("Connect")
        disconnect = QPushButton("Disconnect")
        home = QPushButton("Home")
        stop = QPushButton("Stop")
        move = QPushButton("Move Absolute")
        minus = QPushButton("- Jog")
        plus = QPushButton("+ Jog")
        diagnose = QPushButton("Diagnose")
        identify = QPushButton("Identify")
        identify.setObjectName(f"{axis.value}_identify")
        motion_test = QPushButton("Motion Test")

        # Buttons emit intent only; the main window schedules the actual work.
        connect.clicked.connect(lambda: self.connect_requested.emit(axis))
        disconnect.clicked.connect(lambda: self.disconnect_requested.emit(axis))
        home.clicked.connect(lambda: self.home_requested.emit(axis))
        stop.clicked.connect(lambda: self.stop_requested.emit(axis))
        move.clicked.connect(lambda: self.absolute_requested.emit(axis, self.target.value()))
        minus.clicked.connect(
            lambda: self.relative_requested.emit(axis, -self.increment.value())
        )
        plus.clicked.connect(lambda: self.relative_requested.emit(axis, self.increment.value()))
        diagnose.clicked.connect(lambda: self.diagnostic_requested.emit(axis))
        identify.clicked.connect(lambda: self.identify_requested.emit(axis))
        motion_test.clicked.connect(lambda: self.motion_test_requested.emit(axis))

        # Reuse one compact layout for X, Y, Z, and Rotation panels.
        layout = QGridLayout(self)
        layout.addWidget(QLabel("Status"), 0, 0)
        layout.addWidget(self.status_label, 0, 1, 1, 3)
        layout.addWidget(QLabel("Position"), 1, 0)
        layout.addWidget(self.position_label, 1, 1)
        layout.addWidget(connect, 2, 0)
        layout.addWidget(disconnect, 2, 1)
        layout.addWidget(home, 2, 2)
        layout.addWidget(stop, 2, 3)
        layout.addWidget(self.target, 3, 0, 1, 2)
        layout.addWidget(move, 3, 2, 1, 2)
        layout.addWidget(self.increment, 4, 0)
        layout.addWidget(minus, 4, 1)
        layout.addWidget(plus, 4, 2)
        layout.addWidget(diagnose, 5, 0)
        layout.addWidget(identify, 5, 1)
        layout.addWidget(motion_test, 5, 2, 1, 2)

    def update_status(self, status: ComponentStatus) -> None:
        """Display an observed component status."""
        state = "Disconnected"
        if status.connected:
            state = "Busy" if status.busy else ("Homed" if status.homed else "Connected")
        if status.error:
            state = f"Error: {status.error}"
        self.status_label.setText(state)
        self.position_label.setText("—" if status.position is None else f"{status.position:.4f}")

    def apply_config(self, config: ComponentConfig) -> None:
        """Update editable ranges and defaults for a newly loaded profile."""
        self.config = config
        # A broad disconnected range avoids inventing physical travel before API discovery.
        limits = config.limits or LimitsConfig(-1_000_000.0, 1_000_000.0)
        self.target.setRange(limits.minimum, limits.maximum)
        self.target.setValue(config.simulation_start)
        self.increment.setRange(0.0001, limits.span)
        self.increment.setValue(config.motion.jog_increment)

    def apply_limits(self, limits: LimitsConfig) -> None:
        """Apply adapter-reported limits translated into the current reference frame."""
        # Updating ranges clamps stale entries before they can become motion requests.
        self.target.setRange(limits.minimum, limits.maximum)
        self.increment.setRange(0.0001, limits.span)
