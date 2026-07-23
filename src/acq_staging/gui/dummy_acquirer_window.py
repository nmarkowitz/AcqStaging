"""Standalone PyQt6 dummy data-acquisition window."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PyQt6.QtCore import QThreadPool, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from acq_staging.acquisition import DummyDataAcquirer
from acq_staging.configuration import DummyAcquirerConfig, default_dummy_acquirer_config
from acq_staging.factory import build_digital_devices
from acq_staging.gui.configuration_dialog import DummyAcquirerConfigurationDialog
from acq_staging.gui.digital_panel import DigitalPanel
from acq_staging.gui.workers import FunctionWorker
from acq_staging.models import DigitalAdapterType, DigitalDirection, DummyAcquirerStatus
from acq_staging.services import DigitalIOService


class DummyAcquirerWindow(QMainWindow):
    """Independent DAQ GUI that emulates acquisition completion acknowledgements."""

    def __init__(self, config: DummyAcquirerConfig | None = None) -> None:
        super().__init__()
        # Safe startup constructs only disconnected simulated DAQ adapters.
        self.config = config or default_dummy_acquirer_config()
        self.digital_service = DigitalIOService(
            self.config,
            build_digital_devices(self.config),
        )
        self.acquirer = DummyDataAcquirer(self.config, self.digital_service)
        thread_pool = QThreadPool.globalInstance()
        if thread_pool is None:
            raise RuntimeError("Qt global thread pool is unavailable")
        self.thread_pool = thread_pool
        self._workers: set[FunctionWorker] = set()
        self._listener_worker: FunctionWorker | None = None
        self._poll_pending = False
        self.setWindowTitle(f"Dummy Data Acquirer — {self.config.profile_name}")
        self.resize(900, 600)

        # Top-level controls mirror the movement GUI without exposing motion actions.
        root = QWidget()
        root_layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.edit_button = QPushButton("Edit Configuration")
        self.connect_all_button = QPushButton("Connect All")
        self.disconnect_all_button = QPushButton("Disconnect All")
        self.start_button = QPushButton("Start Listening")
        self.start_button.setObjectName("start_listening")
        self.stop_button = QPushButton("Stop Listening")
        self.stop_button.setObjectName("stop_listening")
        self.complete_button = QPushButton("Complete Now")
        self.complete_button.setObjectName("complete_now")
        for button in (
            self.edit_button,
            self.connect_all_button,
            self.disconnect_all_button,
            self.start_button,
            self.stop_button,
            self.complete_button,
        ):
            toolbar.addWidget(button)
        root_layout.addLayout(toolbar)

        # Status and counters make every received and completed request visible.
        self.acquisition_status = QLabel("Stopped")
        self.acquisition_status.setObjectName("dummy_acquisition_status")
        self.counters = QLabel("Requests: 0 | Completed: 0")
        self.counters.setObjectName("dummy_acquisition_counters")
        root_layout.addWidget(self.acquisition_status)
        root_layout.addWidget(self.counters)

        # Reuse the same eight-channel DAQ panels and worker-routed controls.
        panels = QHBoxLayout()
        self.digital_panels: dict[DigitalDirection, DigitalPanel] = {}
        for direction in DigitalDirection:
            panel = DigitalPanel(direction, self.config.digital_io[direction])
            self._connect_digital_panel(panel)
            self.digital_panels[direction] = panel
            panels.addWidget(panel)
        root_layout.addLayout(panels)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root_layout.addWidget(self.log)
        self.setCentralWidget(root)
        self.app_status_bar = QStatusBar()
        self.setStatusBar(self.app_status_bar)

        # Button callbacks never perform blocking NI calls in the Qt thread.
        self.edit_button.clicked.connect(self.edit_configuration)
        self.connect_all_button.clicked.connect(
            lambda: self._run("Connect all", self._connect_all)
        )
        self.disconnect_all_button.clicked.connect(
            lambda: self._run("Disconnect all", self.digital_service.disconnect_all)
        )
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)
        self.complete_button.clicked.connect(
            lambda: self._run("Complete acquisition", self.acquirer.complete_now)
        )

        # GUI polling reads cached listener state and polls DAQs only while idle.
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(
            min(component.polling_interval_ms for component in self.config.digital_io.values())
        )
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self._refresh_display()

    def _connect_digital_panel(self, panel: DigitalPanel) -> None:
        # Translate panel intent into the same vendor-neutral DAQ service operations.
        panel.connect_requested.connect(
            lambda direction: self._run(
                f"Connect {direction.value}",
                lambda: self.digital_service.connect(direction),
            )
        )
        panel.disconnect_requested.connect(
            lambda direction: self._run(
                f"Disconnect {direction.value}",
                lambda: self.digital_service.disconnect(direction),
            )
        )
        panel.refresh_requested.connect(
            lambda _direction: self._run("Read digital inputs", self.digital_service.read_inputs)
        )
        panel.write_requested.connect(self._write_outputs)

    def _write_outputs(self, _direction: DigitalDirection, states: object) -> None:
        # Reject malformed Qt payloads before they reach the DAQ service.
        if not isinstance(states, dict):
            self._show_error("invalid digital output state request")
            return
        self._run("Write digital outputs", lambda: self.digital_service.write_outputs(states))

    def _connect_all(self) -> None:
        # Roll back earlier DAQ connections if the second direction fails.
        connected: list[DigitalDirection] = []
        try:
            for direction in DigitalDirection:
                if self.config.digital_io[direction].enabled:
                    self.digital_service.connect(direction)
                    connected.append(direction)
        except Exception:
            for direction in reversed(connected):
                self.digital_service.disconnect(direction)
            raise

    def start_listening(self) -> None:
        """Start one background listener after the operator initializes idle outputs."""
        if self._listener_worker is not None:
            self._show_error("the dummy acquirer is already listening")
            return
        worker: FunctionWorker

        def execute() -> DummyAcquirerStatus:
            # Events cross into Qt through the worker progress signal.
            return self.acquirer.run(
                lambda event, details: worker.signals.progress.emit((event, details))
            )

        worker = FunctionWorker(execute)
        self._listener_worker = worker
        self._workers.add(worker)
        worker.signals.progress.connect(self._show_event)
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._listener_finished(worker))
        self.thread_pool.start(worker)
        self._refresh_display()

    def stop_listening(self) -> None:
        """Request a prompt, safe return to idle without blocking the Qt thread."""
        self.acquirer.request_stop()
        self.app_status_bar.showMessage("Stopping dummy acquirer")

    def _listener_finished(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        if self._listener_worker is worker:
            self._listener_worker = None
        self.app_status_bar.clearMessage()
        self._refresh_display()

    def _show_event(self, payload: object) -> None:
        # Display only well-formed worker event tuples.
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        event, details = payload
        self._append_log(f"{event}: {details}")
        self._refresh_display()

    def edit_configuration(self) -> None:
        """Apply a validated dummy profile only while the listener is stopped."""
        if self._listener_worker is not None:
            self._show_error("stop the dummy acquirer before changing configuration")
            return
        dialog = DummyAcquirerConfigurationDialog(self.config, self)
        if dialog.exec() and isinstance(dialog.validated_config, DummyAcquirerConfig):
            try:
                self._apply_configuration(dialog.validated_config)
            except Exception as exc:
                self._show_error(str(exc))

    def _apply_configuration(self, config: DummyAcquirerConfig) -> bool:
        # Real NI adapter construction requires confirmation but performs no connection or write.
        has_hardware = any(
            component.adapter is DigitalAdapterType.NIDAQMX
            for component in config.digital_io.values()
        )
        if has_hardware and not self._confirm(
            "Enable hardware profile",
            "Construct real DAQ adapters? This does not connect or change pin states.",
        ):
            return False
        devices = build_digital_devices(config, allow_hardware=has_hardware)
        self.digital_service.disconnect_all()
        self.config = config
        self.digital_service = DigitalIOService(config, devices)
        self.acquirer = DummyDataAcquirer(config, self.digital_service)
        for direction, panel in self.digital_panels.items():
            panel.apply_config(config.digital_io[direction])
        self.poll_timer.setInterval(
            min(component.polling_interval_ms for component in config.digital_io.values())
        )
        self.setWindowTitle(f"Dummy Data Acquirer — {config.profile_name}")
        self._refresh_display()
        return True

    def _poll(self) -> None:
        # Avoid competing input reads while the listener owns the handshake loop.
        if self._poll_pending or self.acquirer.status().listening:
            self._refresh_display()
            return
        self._poll_pending = True
        worker = FunctionWorker(self.digital_service.refresh_connected_io)
        self._workers.add(worker)
        worker.signals.succeeded.connect(lambda _result: self._refresh_display())
        worker.signals.finished.connect(lambda: self._poll_finished(worker))
        self.thread_pool.start(worker)

    def _poll_finished(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        self._poll_pending = False

    def _refresh_display(self) -> None:
        # Render cached DAQ and listener state without performing vendor calls.
        for direction, panel in self.digital_panels.items():
            panel.update_status(self.digital_service.status(direction))
        status = self.acquirer.status()
        listener_active = self._listener_worker is not None or status.listening
        state = "Acquiring" if status.acquiring else "Listening" if status.listening else "Stopped"
        if status.error:
            state = f"Error: {status.error}"
        self.acquisition_status.setText(state)
        self.counters.setText(
            f"Requests: {status.requests_received} | Completed: {status.acquisitions_completed}"
        )
        self.start_button.setEnabled(not listener_active)
        self.stop_button.setEnabled(listener_active)
        self.complete_button.setEnabled(status.acquiring)
        self.edit_button.setEnabled(not listener_active)
        self.connect_all_button.setEnabled(not listener_active)
        self.disconnect_all_button.setEnabled(not listener_active)
        for direction, panel in self.digital_panels.items():
            panel.setEnabled(self.config.digital_io[direction].enabled and not listener_active)

    def _run(self, description: str, function: Callable[[], Any]) -> None:
        # Retain every worker until completion to prevent premature Qt collection.
        self.app_status_bar.showMessage(description)
        worker = FunctionWorker(function)
        self._workers.add(worker)
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._operation_finished(worker))
        self.thread_pool.start(worker)

    def _operation_finished(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        self.app_status_bar.clearMessage()
        self._refresh_display()

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) is QMessageBox.StandardButton.Yes

    def _show_error(self, message: str) -> None:
        self._append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "Dummy acquirer error", message)

    def _append_log(self, message: str) -> None:
        logging.getLogger(__name__).info(message)
        self.log.append(message)

    def closeEvent(self, event: Any) -> None:
        # Stop the listener before closing both DAQ tasks during application shutdown.
        self.poll_timer.stop()
        self.acquirer.request_stop()
        with suppress(Exception):
            self.digital_service.disconnect_all()
        event.accept()
