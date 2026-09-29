"""Main PyQt6 application window."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import (
    QDockWidget,
    QGridLayout,
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

from acq_staging.acquisition import DaqTileHandshake
from acq_staging.adapters.zaber import ZaberConnectionManager
from acq_staging.configuration import (
    DEFAULT_STAGE_PRESETS_PATH,
    AppConfig,
    default_simulation_config,
    load_stage_presets,
)
from acq_staging.factory import build_axes, build_digital_devices, build_simulation_axes
from acq_staging.gui.axis_panel import AxisPanel
from acq_staging.gui.configuration_dialog import ConfigurationDialog
from acq_staging.gui.digital_panel import DigitalPanel
from acq_staging.gui.mosaic_progress_dialog import AxisMosaicView
from acq_staging.gui.mosaic_setup_dialog import MosaicSetupDialog
from acq_staging.gui.workers import FunctionWorker
from acq_staging.models import (
    HOME_ORDER,
    AxisName,
    DiagnosticResult,
    DigitalAdapterType,
    DigitalDirection,
    MosaicProgress,
    MosaicSequenceSpec,
    MosaicTile,
    Pose,
    StagePreset,
)
from acq_staging.mosaic_logging import MosaicAuditLogger
from acq_staging.services import DigitalIOService, MotionService


class MainWindow(QMainWindow):
    """Main application window, safely initialized with simulations."""

    def __init__(
        self,
        config: AppConfig | None = None,
        stage_presets_path: str | Path = DEFAULT_STAGE_PRESETS_PATH,
    ) -> None:
        super().__init__()
        # Startup accepts simulation profiles only and performs no automatic connection.
        self.config = config or default_simulation_config()
        self.stage_presets_path = Path(stage_presets_path)
        self._cached_mosaic_spec: MosaicSequenceSpec | None = None
        self.digital_service = DigitalIOService(
            self.config,
            build_digital_devices(self.config),
        )
        self.acquisition_handshake = DaqTileHandshake(
            self.config.acquisition_handshake,
            self.digital_service,
        )
        self.service = MotionService(
            self.config,
            build_simulation_axes(self.config),
            self.acquisition_handshake,
        )
        self.connection_manager: ZaberConnectionManager | None = None
        thread_pool = QThreadPool.globalInstance()
        if thread_pool is None:
            raise RuntimeError("Qt global thread pool is unavailable")
        self.thread_pool = thread_pool
        self._poll_pending = False
        self._workers: set[FunctionWorker] = set()
        self._mosaic_had_error = False
        self._mosaic_logger: MosaicAuditLogger | None = None
        self._mosaic_audit_digital_service: DigitalIOService | None = None
        self.setWindowTitle(f"AcqStaging — {self.config.profile_name}")
        self.resize(1050, 750)

        # Build the global controls and fixed-order pose display.
        root = QWidget()
        root_layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        edit_button = QPushButton("Edit Configuration")
        connect_all = QPushButton("Connect All")
        disconnect_all = QPushButton("Disconnect All")
        home_all = QPushButton("Home All: X → Y → Z → Rotation")
        start_mosaic = QPushButton("Start Mosaic")
        start_mosaic.setObjectName("start_mosaic")
        prepare_imaging = QPushButton("Prepare for Imaging")
        prepare_imaging.setObjectName("prepare_imaging")
        prepare_slicing = QPushButton("Prepare for Slicing")
        prepare_slicing.setObjectName("prepare_slicing")
        toolbar.addWidget(edit_button)
        toolbar.addStretch()
        toolbar.addWidget(connect_all)
        toolbar.addWidget(disconnect_all)
        toolbar.addWidget(home_all)
        toolbar.addWidget(prepare_imaging)
        toolbar.addWidget(prepare_slicing)
        toolbar.addWidget(start_mosaic)
        root_layout.addLayout(toolbar)

        self.pose_label = QLabel("[—, —, —, —]")
        self.pose_label.setObjectName("pose_label")
        root_layout.addWidget(QLabel("Scan-head pose [x mm, y mm, z mm, theta deg]"))
        root_layout.addWidget(self.pose_label)

        # Each logical axis receives an independent but structurally identical panel.
        panels_layout = QGridLayout()
        self.panels: dict[AxisName, AxisPanel] = {}
        for index, axis in enumerate(HOME_ORDER):
            panel = AxisPanel(axis, self.config.components[axis])
            self._connect_panel(panel)
            self.panels[axis] = panel
            panels_layout.addWidget(panel, index // 2, index % 2)
        root_layout.addLayout(panels_layout)

        # DAQ panels use their own vendor-neutral service beside the motion panels.
        digital_layout = QHBoxLayout()
        self.digital_panels: dict[DigitalDirection, DigitalPanel] = {}
        for direction in DigitalDirection:
            digital_panel = DigitalPanel(direction, self.config.digital_io[direction])
            self._connect_digital_panel(digital_panel)
            self.digital_panels[direction] = digital_panel
            digital_layout.addWidget(digital_panel)
        root_layout.addLayout(digital_layout)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        root_layout.addWidget(self.log)
        self.setCentralWidget(root)

        # A fixed dock keeps the only mosaic-progress view attached to the main GUI.
        self.axis_mosaic_view = AxisMosaicView(parent=self)
        self.axis_mosaic_view.setObjectName("axis_mosaic_view")
        viewer_dock = QDockWidget("Axis Locations and Mosaic", self)
        viewer_dock.setObjectName("axis_mosaic_dock")
        viewer_dock.setWidget(self.axis_mosaic_view)
        viewer_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        viewer_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, viewer_dock)
        self.app_status_bar = QStatusBar()
        self.setStatusBar(self.app_status_bar)

        # GUI callbacks submit service work rather than invoking vendor APIs directly.
        edit_button.clicked.connect(self.edit_configuration)
        connect_all.clicked.connect(lambda: self._run("Connect all", self._connect_all))
        disconnect_all.clicked.connect(
            lambda: self._run("Disconnect all", self._disconnect_all)
        )
        home_all.clicked.connect(self.request_home_all)
        start_mosaic.clicked.connect(self.request_mosaic)
        prepare_imaging.clicked.connect(
            lambda: self.request_stage_preset(StagePreset.IMAGING)
        )
        prepare_slicing.clicked.connect(
            lambda: self.request_stage_preset(StagePreset.SLICING)
        )
        self.axis_mosaic_view.pause_requested.connect(self.request_pause_mosaic)
        self.axis_mosaic_view.continue_requested.connect(self.request_continue_mosaic)
        self.axis_mosaic_view.cancel_requested.connect(self.request_cancel_mosaic)

        # Timer ticks enqueue pose polling and never block the Qt event loop.
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(
            min(
                self.config.ui.pose_poll_interval_ms,
                self.config.digital_io[DigitalDirection.INPUT].polling_interval_ms,
                self.config.digital_io[DigitalDirection.OUTPUT].polling_interval_ms,
            )
        )
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self._refresh_display()

    def _connect_panel(self, panel: AxisPanel) -> None:
        # Translate vendor-neutral panel signals into background service operations.
        panel.connect_requested.connect(
            lambda axis: self._run(f"Connect {axis.value}", lambda: self.service.connect(axis))
        )
        panel.disconnect_requested.connect(
            lambda axis: self._run(
                f"Disconnect {axis.value}", lambda: self.service.disconnect(axis)
            )
        )
        panel.home_requested.connect(self.request_home)
        panel.stop_requested.connect(
            lambda axis: self._run(f"Stop {axis.value}", lambda: self.service.stop(axis))
        )
        panel.absolute_requested.connect(self.request_absolute_move)
        panel.relative_requested.connect(self.request_relative_move)
        panel.diagnostic_requested.connect(
            lambda axis: self._run(
                f"Diagnose {axis.value}",
                lambda: self.service.diagnose(axis),
                self._show_diagnostic,
            )
        )
        panel.identify_requested.connect(
            lambda axis: self._run(
                f"Identify {axis.value}", lambda: self.service.identify(axis)
            )
        )
        panel.motion_test_requested.connect(self.request_motion_test)

    def _connect_digital_panel(self, panel: DigitalPanel) -> None:
        # Route every DAQ request through workers to keep NI calls off the GUI thread.
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
            lambda _direction: self._run(
                "Read digital inputs",
                self.digital_service.read_inputs,
            )
        )
        panel.write_requested.connect(self._write_digital_outputs)

    def _write_digital_outputs(
        self,
        _direction: DigitalDirection,
        states: object,
    ) -> None:
        # Reject malformed signal payloads before they reach the digital service.
        if not isinstance(states, dict):
            self._show_error("invalid digital output state request")
            return
        self._run(
            "Write digital outputs",
            lambda: self.digital_service.write_outputs(states),
        )

    def _connect_all(self) -> None:
        # Roll back both subsystems if any enabled component fails to connect.
        self.service.connect_all()
        connected: list[DigitalDirection] = []
        try:
            for direction in DigitalDirection:
                if self.config.digital_io[direction].enabled:
                    self.digital_service.connect(direction)
                    connected.append(direction)
        except Exception:
            for direction in reversed(connected):
                self.digital_service.disconnect(direction)
            self.service.disconnect_all()
            raise

    def _disconnect_all(self) -> None:
        self.digital_service.disconnect_all()
        self.service.disconnect_all()

    def request_home(self, axis: AxisName) -> None:
        if self._confirm("Confirm homing", f"Home {axis.value.upper()} now?"):
            self._run(f"Home {axis.value}", lambda: self.service.home(axis))

    def request_home_all(self) -> None:
        if self._confirm(
            "Confirm Home All",
            "Home in sequence X → Y → Z → Rotation?",
        ):
            self._run_home_all()

    def _run_home_all(self) -> None:
        worker: FunctionWorker

        def execute() -> list[AxisName]:
            return self.service.home_all(worker.signals.progress.emit)

        # One worker preserves strict home order while progress updates remain responsive.
        worker = FunctionWorker(execute)
        self._workers.add(worker)
        worker.signals.progress.connect(
            lambda axis: self.app_status_bar.showMessage(
                f"Homing {axis.value.upper()} — X → Y → Z → Rotation"
            )
        )
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._operation_finished(worker, "Home all"))
        self.thread_pool.start(worker)

    def request_stage_preset(self, preset: StagePreset) -> None:
        """Confirm and run the ordered moves for one preset from the preset file."""
        # Reload on every request so edits to the file apply without restarting.
        try:
            moves = load_stage_presets(self.stage_presets_path).moves(preset)
        except Exception as exc:
            self._show_error(f"Unable to load stage presets: {exc}")
            return
        steps = "\n".join(
            f"{number}. {axis.value.upper()} -> {destination:.4f} "
            f"{'deg' if axis is AxisName.ROTATION else 'mm'}"
            for number, (axis, destination) in enumerate(moves, start=1)
        )
        label = preset.value.capitalize()
        if not self._confirm(
            f"Confirm Prepare for {label}",
            f"Move one axis at a time in this order?\n\n{steps}",
        ):
            return
        worker: FunctionWorker

        def execute() -> list[AxisName]:
            return self.service.move_to_preset(moves, worker.signals.progress.emit)

        # One worker preserves strict move order while progress updates remain responsive.
        worker = FunctionWorker(execute)
        self._workers.add(worker)
        worker.signals.progress.connect(
            lambda axis: self.app_status_bar.showMessage(
                f"Prepare for {label}: moving {axis.value.upper()}"
            )
        )
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(
            lambda: self._operation_finished(worker, f"Prepare for {label}")
        )
        self.thread_pool.start(worker)

    def request_absolute_move(self, axis: AxisName, destination: float) -> None:
        # Large-move confirmation uses the observed current position, not the target field.
        status = self.service.component_status(axis)
        current = status.position
        if current is None:
            self._show_error(f"{axis.value}: position is not known")
            return
        if self._is_large_move(axis, destination - current) and not self._confirm(
            "Confirm large move",
            f"Move {axis.value.upper()} from {current:.4f} to {destination:.4f}?",
        ):
            return
        self._run(
            f"Move {axis.value} absolute",
            lambda: self.service.move_absolute(axis, destination),
        )

    def request_relative_move(self, axis: AxisName, displacement: float) -> None:
        if self._is_large_move(axis, displacement) and not self._confirm(
            "Confirm large move",
            f"Move {axis.value.upper()} by {displacement:.4f}?",
        ):
            return
        self._run(
            f"Move {axis.value} relative",
            lambda: self.service.move_relative(axis, displacement),
        )

    def request_motion_test(self, axis: AxisName) -> None:
        distance = self.config.components[axis].motion.test_distance
        if self._confirm(
            "Confirm motion test",
            f"Move {axis.value.upper()} by up to {distance}, then return to the start?",
        ):
            self._run(f"Motion test {axis.value}", lambda: self.service.motion_test(axis))

    def request_mosaic(self) -> None:
        # Read every required axis status in a worker before opening run setup.
        self._run(
            "Check mosaic readiness",
            self.service.mosaic_readiness,
            self._confirm_mosaic,
        )

    def _confirm_mosaic(self, readiness: object) -> None:
        # A full sequence cannot bypass a disconnected or unhomed required axis.
        if not isinstance(readiness, tuple) or len(readiness) != 2:
            self._show_error("invalid mosaic readiness result")
            return
        disconnected, unhomed = readiness
        if not isinstance(disconnected, tuple) or not isinstance(unhomed, tuple):
            self._show_error("invalid mosaic readiness result")
            return
        if disconnected:
            names = ", ".join(axis.value.upper() for axis in disconnected)
            self._show_error(f"Full mosaic requires connected components: {names}")
            return
        if unhomed:
            names = ", ".join(axis.value.upper() for axis in unhomed)
            self._show_error(f"Full mosaic requires homed components: {names}")
            return

        # A modal setup keeps run-specific geometry separate from the saved profile.
        run_spec = self._get_mosaic_run_spec()
        if run_spec is None:
            return

        handshake_text = (
            " Acquisition DAQ acknowledgement is required for every tile."
            if self.config.acquisition_handshake.enabled
            else " Acquisition handshaking is disabled; dwell timing will be used."
        )
        # Confirmation describes only the tilings selected for this run.
        first = run_spec.tiling_one
        tiling_text = (
            f"Tiling 1: XY {first.beginning} to {first.ending}, Z={first.z_mm}, "
            "rotation=0°."
        )
        if run_spec.run_tiling_two:
            second = run_spec.tiling_two
            tiling_text += (
                f" Tiling 2: XY {second.beginning} to {second.ending}, "
                f"Z={second.z_mm}, rotation={second.rotation_deg}°."
            )
        run_kind = "two-tiling" if run_spec.run_tiling_two else "single-tiling"
        if self._confirm(
            f"Confirm {run_kind} motion",
            f"Run {run_spec.total_tiles} total tiles starting at Tiling "
            f"{run_spec.resume_tiling}, tile {run_spec.resume_tile}? "
            f"{tiling_text} Any required rotation change uses safe "
            f"Z={run_spec.safe_transition_z_mm} mm.{handshake_text}",
        ):
            self._run_mosaic(run_spec)

    def _get_mosaic_run_spec(self) -> MosaicSequenceSpec | None:
        """Show the mosaic editor and return its validated enabled sequence."""
        dialog = MosaicSetupDialog(
            self.config.mosaic,
            self.config.components,
            self.service.pose,
            {
                axis: self.service.axis_limits(axis)
                for axis in HOME_ORDER
                if self.service.axes[axis].status().connected
            },
            self,
            initial_spec=self._cached_mosaic_spec,
        )
        if not dialog.exec():
            return None
        # Keep only accepted geometry; cancelling the editor preserves the previous run.
        self._cached_mosaic_spec = dialog.validated_spec
        return dialog.validated_spec

    def _run_mosaic(
        self,
        run_spec: MosaicSequenceSpec,
    ) -> None:
        worker: FunctionWorker

        # Require an audit file before any automated hardware sequence can begin.
        if self._mosaic_logger is not None:
            self._show_error("a mosaic is already running")
            return
        try:
            # Constructor-level initialization guarantees settings occupy the first line.
            settings = self.service.mosaic_audit_settings(run_spec)
            logger = MosaicAuditLogger(initial_settings=settings)
        except Exception as exc:
            self._show_error(f"Unable to create mosaic log: {exc}")
            return
        self._mosaic_logger = logger
        self._mosaic_audit_digital_service = self.digital_service
        self.digital_service.set_event_callback(logger.log)
        if self.config.acquisition_handshake.enabled:
            # Prevent connection or state changes while the mosaic owns both signal lines.
            for panel in self.digital_panels.values():
                panel.setEnabled(False)
        self._append_log(f"Mosaic audit log: {logger.path}")

        # Replace the attached viewer's prior grid without opening another window.
        self.axis_mosaic_view.prepare_run(run_spec)
        self._mosaic_had_error = False

        def execute() -> tuple[list[MosaicTile], ...]:
            # Audit callbacks run synchronously in this worker for precise ordering.
            return self.service.run_mosaic_sequence(
                run_spec,
                worker.signals.progress.emit,
                logger.log,
            )

        worker = FunctionWorker(execute)
        self._workers.add(worker)
        worker.signals.progress.connect(self._show_mosaic_progress)
        worker.signals.failed.connect(self._mosaic_failed)
        worker.signals.finished.connect(lambda: self._mosaic_finished(worker))
        self.thread_pool.start(worker)

    def _show_mosaic_progress(self, progress: object) -> None:
        # Ignore unrelated worker payloads before updating mosaic widgets.
        if not isinstance(progress, MosaicProgress):
            return
        self.axis_mosaic_view.update_progress(progress)
        tile = progress.tile
        self.app_status_bar.showMessage(
            f"Tiling {progress.tiling_number}, tile {progress.number}/{progress.total}: "
            f"X={tile.x_mm:.4f}, Y={tile.y_mm:.4f}, Z={tile.z_mm:.4f} mm, "
            f"Rotation={tile.theta_deg:.4f}°"
        )

    def _mosaic_failed(self, message: str) -> None:
        self._mosaic_had_error = True
        self.axis_mosaic_view.mark_failed(message)
        if "mosaic cancelled" in message.lower():
            self._append_log("Mosaic cancelled by user")
        else:
            self._show_error(message)

    def _mosaic_finished(self, worker: FunctionWorker) -> None:
        if not self._mosaic_had_error:
            self.axis_mosaic_view.mark_finished()
        self._operation_finished(worker, "Mosaic")
        self._finish_mosaic_audit()

    def _finish_mosaic_audit(self) -> None:
        # Detach DAQ callbacks before closing the stream they may write to.
        if self._mosaic_audit_digital_service is not None:
            self._mosaic_audit_digital_service.set_event_callback(None)
            self._mosaic_audit_digital_service = None
        if self._mosaic_logger is not None:
            self._mosaic_logger.close()
            self._mosaic_logger = None
        for direction, panel in self.digital_panels.items():
            panel.setEnabled(self.config.digital_io[direction].enabled)

    def request_pause_mosaic(self) -> None:
        """Audit and asynchronously request a safe-boundary mosaic pause."""
        if self._mosaic_logger is not None:
            self._mosaic_logger.log("MOSAIC_PAUSE_REQUESTED", {})
        self._run("Pause mosaic", self.service.pause_mosaic)

    def request_continue_mosaic(self) -> None:
        """Audit and asynchronously release the mosaic pause gate."""
        if self._mosaic_logger is not None:
            self._mosaic_logger.log("MOSAIC_CONTINUE_REQUESTED", {})
        self._run("Continue mosaic", self.service.continue_mosaic)

    def request_cancel_mosaic(self) -> None:
        """Audit and asynchronously stop the active mosaic."""
        if self._mosaic_logger is not None:
            self._mosaic_logger.log("MOSAIC_CANCEL_REQUESTED", {})
        self._run("Cancel mosaic", self.service.cancel_mosaic)

    def edit_configuration(self) -> None:
        # The dialog owns file validation; this method applies only accepted profiles.
        dialog = ConfigurationDialog(self.config, self)
        if dialog.exec() and isinstance(dialog.validated_config, AppConfig):
            try:
                self._apply_configuration(dialog.validated_config)
            except Exception as exc:
                self._show_error(str(exc))

    def _apply_configuration(self, config: AppConfig) -> bool:
        # Constructing hardware adapters requires confirmation but does not connect them.
        has_hardware = any(
            component.adapter.value != "simulated"
            for component in config.components.values()
        ) or any(
            component.adapter is DigitalAdapterType.NIDAQMX
            for component in config.digital_io.values()
        )
        if has_hardware and not self._confirm(
            "Enable hardware profile",
            "Construct real hardware adapters? This does not connect or move hardware.",
        ):
            return False
        axes, manager = build_axes(config, allow_hardware=has_hardware)
        digital_devices = build_digital_devices(config, allow_hardware=has_hardware)
        # Release all old resources before replacing the active service and adapters.
        self.digital_service.disconnect_all()
        self.service.disconnect_all()
        if self.connection_manager is not None:
            self.connection_manager.close_all()
        self.config = config
        # A newly applied profile must start from its own defaults and coordinate context.
        self._cached_mosaic_spec = None
        self.digital_service = DigitalIOService(config, digital_devices)
        self.acquisition_handshake = DaqTileHandshake(
            config.acquisition_handshake,
            self.digital_service,
        )
        self.service = MotionService(config, axes, self.acquisition_handshake)
        self.connection_manager = manager
        for axis, panel in self.panels.items():
            panel.apply_config(config.components[axis])
        for direction, digital_panel in self.digital_panels.items():
            digital_panel.apply_config(config.digital_io[direction])
        self.poll_timer.setInterval(
            min(
                config.ui.pose_poll_interval_ms,
                config.digital_io[DigitalDirection.INPUT].polling_interval_ms,
                config.digital_io[DigitalDirection.OUTPUT].polling_interval_ms,
            )
        )
        self.setWindowTitle(f"AcqStaging — {config.profile_name}")
        self._refresh_display()
        return True

    def _run(
        self,
        description: str,
        function: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
    ) -> None:
        # Retain workers until completion so Qt cannot garbage-collect active jobs.
        self.app_status_bar.showMessage(description)
        worker = FunctionWorker(function)
        self._workers.add(worker)
        worker.signals.succeeded.connect(on_success or (lambda _result: None))
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._operation_finished(worker, description))
        self.thread_pool.start(worker)

    def _operation_finished(self, worker: FunctionWorker, description: str) -> None:
        self._workers.discard(worker)
        self.app_status_bar.clearMessage()
        self._append_log(f"{description} finished")
        self._refresh_display()
        # Record every completed operation in controller-home coordinates.
        values = [
            "unknown" if value is None else f"{value:.4f}"
            for value in self.service.pose.as_vector()
        ]
        self._append_log(
            "Controller-home position: "
            f"[x={values[0]}, y={values[1]}, z={values[2]}, theta={values[3]}]"
        )

    def _poll(self) -> None:
        # Skip timer ticks while an earlier poll is still running to prevent a backlog.
        if self._poll_pending:
            return
        self._poll_pending = True
        worker = FunctionWorker(self._poll_components)
        self._workers.add(worker)
        worker.signals.succeeded.connect(lambda _pose: self._refresh_display())
        worker.signals.finished.connect(lambda: self._poll_finished(worker))
        self.thread_pool.start(worker)

    def _poll_finished(self, worker: FunctionWorker) -> None:
        self._workers.discard(worker)
        self._poll_pending = False

    def _poll_components(self) -> Pose:
        # Refresh motion, inputs, and applied outputs in one background operation.
        pose = self.service.refresh_pose()
        self.digital_service.refresh_connected_io()
        return pose

    def _refresh_display(self) -> None:
        for axis, panel in self.panels.items():
            panel.update_status(self.service.component_status(axis))
            # Simulations retain synthetic bounds while hardware editors stay unbounded.
            panel.apply_limits(self.service.axis_limits(axis))
        for direction, digital_panel in self.digital_panels.items():
            digital_panel.update_status(self.digital_service.status(direction))
        self._show_pose(self.service.pose)
        self.axis_mosaic_view.update_pose(self.service.pose)

    def _show_pose(self, pose: Pose) -> None:
        values = ["—" if value is None else f"{value:.4f}" for value in pose.as_vector()]
        self.pose_label.setText(f"[{', '.join(values)}]")

    def _show_diagnostic(self, result: object) -> None:
        if isinstance(result, DiagnosticResult):
            self._append_log(
                f"{result.identity.axis.value}: {result.identity.model} "
                f"{result.identity.serial_number}; {result.message}"
            )

    def _is_large_move(self, axis: AxisName, displacement: float) -> bool:
        threshold = (
            self.config.ui.large_rotation_move_deg
            if axis is AxisName.ROTATION
            else self.config.ui.large_linear_move_mm
        )
        return abs(displacement) >= threshold

    def _confirm(self, title: str, text: str) -> bool:
        return (
            QMessageBox.question(self, title, text)
            is QMessageBox.StandardButton.Yes
        )

    def _show_error(self, message: str) -> None:
        self._append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "AcqStaging error", message)

    def _append_log(self, message: str) -> None:
        logging.getLogger(__name__).info(message)
        self.log.append(message)

    def closeEvent(self, event: Any) -> None:
        # Shutdown requests safe stops before disconnecting and closing shared ports.
        self.poll_timer.stop()
        if self._mosaic_logger is not None:
            self._mosaic_logger.log("APPLICATION_SHUTDOWN", {})
        self._finish_mosaic_audit()
        for axis in HOME_ORDER:
            with suppress(Exception):
                self.service.stop(axis)
        self.service.disconnect_all()
        self.digital_service.disconnect_all()
        if self.connection_manager is not None:
            self.connection_manager.close_all()
        event.accept()
