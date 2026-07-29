"""User-friendly editor for a one- or two-tiling mosaic sequence."""

from __future__ import annotations

from math import isclose

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from acq_staging.configuration import ComponentConfig, MosaicConfig
from acq_staging.models import (
    AxisName,
    LimitsConfig,
    MosaicSequenceSpec,
    MosaicTilingSpec,
    Pose,
)


class MosaicSetupDialog(QDialog):
    """Collect one required and one optional endpoint-defined XY tiling."""

    def __init__(
        self,
        mosaic: MosaicConfig,
        components: dict[AxisName, ComponentConfig],
        current_pose: Pose,
        axis_limits: dict[AxisName, LimitsConfig | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Up Mosaic Run")
        self.setModal(True)
        self.resize(1050, 620)
        self.validated_spec: MosaicSequenceSpec | None = None
        supplied_limits = axis_limits or {}

        # Simulators retain configured bounds while physical axes remain application-unbounded.
        self.limits = {
            axis: supplied_limits.get(axis, components[axis].limits)
            for axis in (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)
        }
        pose_values = current_pose.as_vector()

        def observed(index: int, axis: AxisName) -> float:
            value = pose_values[index]
            return components[axis].simulation_start if value is None else value

        self.current = {
            axis: observed(index, axis)
            for index, axis in enumerate(
                (AxisName.X, AxisName.Y, AxisName.Z, AxisName.ROTATION)
            )
        }

        # Matching side-by-side cards keep the optional second geometry available to review.
        self.fields: list[dict[str, QDoubleSpinBox]] = []
        self.summaries: list[QLabel] = []
        self.tiling_groups: list[QGroupBox] = []
        tilings_layout = QHBoxLayout()
        for tiling_number in (1, 2):
            group = self._build_tiling_group(tiling_number, mosaic)
            self.tiling_groups.append(group)
            tilings_layout.addWidget(group)

        # Tiling 2 remains enabled by default to preserve existing run behavior.
        self.run_tiling_two = QCheckBox("Run Tiling 2 after Tiling 1")
        self.run_tiling_two.setObjectName("run_tiling_two")
        self.run_tiling_two.setChecked(True)

        # Safe Z and resume controls apply to the complete automatic sequence.
        run_group = QGroupBox("Full-run controls")
        run_form = QFormLayout(run_group)
        self.safe_z_box = self._position_box(
            "safe_transition_z_mm", self.limits[AxisName.Z], " mm"
        )
        self.safe_z_box.setValue(self.current[AxisName.Z])
        self.safe_z_box.setToolTip(
            "Z position reached before any required rotation change. Verify collision safety."
        )
        self.resume_tiling = QComboBox()
        self.resume_tiling.setObjectName("resume_tiling")
        self.resume_tiling.addItems(("Tiling 1", "Tiling 2"))
        self.resume_tile = QSpinBox()
        self.resume_tile.setObjectName("resume_tile")
        self.resume_tile.setRange(1, 1)
        run_form.addRow("Safe rotation-transition Z", self.safe_z_box)
        run_form.addRow("Resume from", self.resume_tiling)
        run_form.addRow("Start/resume tile", self.resume_tile)

        self.sequence_summary = QLabel()
        self.sequence_summary.setObjectName("mosaic_sequence_summary")
        self.sequence_summary.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        review = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if review is not None:
            review.setText("Review Full Motion")
        buttons.accepted.connect(self._accept_valid_geometry)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Configure the required first XY tiling and optionally run Tiling 2 "
                "after it completes successfully. All positions use controller home."
            )
        )
        layout.addWidget(self.run_tiling_two)
        layout.addLayout(tilings_layout)
        layout.addWidget(run_group)
        layout.addWidget(self.sequence_summary)
        layout.addWidget(buttons)

        # Value changes preview geometry while editingFinished commits rounded stop fields.
        geometry_keys = (
            "start_x",
            "stop_x",
            "step_x",
            "start_y",
            "stop_y",
            "step_y",
        )
        for index, fields in enumerate(self.fields):
            for box in fields.values():
                box.valueChanged.connect(self._update_summary)
            for key in geometry_keys:
                fields[key].editingFinished.connect(
                    lambda tiling_index=index: self._normalize_stop_fields(tiling_index)
                )
        self.safe_z_box.valueChanged.connect(self._update_summary)
        self.resume_tiling.currentIndexChanged.connect(self._update_summary)
        self.resume_tile.valueChanged.connect(self._update_summary)
        self.run_tiling_two.toggled.connect(self._set_tiling_two_enabled)
        self._set_tiling_two_enabled(True)
        self._update_summary()

    def build_spec(self) -> MosaicSequenceSpec:
        """Build the validated full sequence currently displayed by the dialog."""
        tilings = (self._build_tiling(0), self._build_tiling(1))
        enabled_tilings = tilings if self.run_tiling_two.isChecked() else tilings[:1]
        # Only enabled tilings can block a run after outward endpoint rounding.
        for tiling_number, tiling in enumerate(enabled_tilings, start=1):
            for axis, ending in zip(
                (AxisName.X, AxisName.Y), tiling.ending, strict=True
            ):
                limits = self.limits[axis]
                if limits is not None and not limits.contains(ending):
                    raise ValueError(
                        f"Tiling {tiling_number} rounded {axis.value.upper()} stop "
                        f"{ending} is outside simulation limits"
                    )
        return MosaicSequenceSpec(
            tilings[0],
            tilings[1],
            self.safe_z_box.value(),
            resume_tiling=self.resume_tiling.currentIndex() + 1,
            resume_tile=self.resume_tile.value(),
            run_tiling_two=self.run_tiling_two.isChecked(),
        )

    def _build_tiling_group(self, tiling_number: int, mosaic: MosaicConfig) -> QGroupBox:
        """Create one consistently ordered tiling card and retain its editors."""
        group = QGroupBox(
            "Tiling 1 — Rotation fixed at 0°"
            if tiling_number == 1
            else "Tiling 2 — User rotation"
        )
        form = QFormLayout(group)
        fields: dict[str, QDoubleSpinBox] = {}

        # X and Y each expose start, stop, and unchanged step magnitude.
        for axis in (AxisName.X, AxisName.Y):
            for role in ("start", "stop"):
                key = f"{role}_{axis.value}"
                box = self._position_box(
                    f"tiling_{tiling_number}_{key}_mm", self.limits[axis], " mm"
                )
                box.setValue(self.current[axis])
                fields[key] = box
                form.addRow(f"{axis.value.upper()} {role}", box)
            step = QDoubleSpinBox()
            step.setObjectName(f"tiling_{tiling_number}_step_{axis.value}_mm")
            step.setDecimals(6)
            limits = self.limits[axis]
            step.setRange(
                0.000001,
                limits.span if limits is not None else float("inf"),
            )
            step.setValue(
                mosaic.step_x_mm if axis is AxisName.X else mosaic.step_y_mm
            )
            step.setSuffix(" mm")
            fields[f"step_{axis.value}"] = step
            form.addRow(f"{axis.value.upper()} step", step)

        # Each tiling owns one fixed Z and one fixed rotation target.
        z_box = self._position_box(
            f"tiling_{tiling_number}_z_mm", self.limits[AxisName.Z], " mm"
        )
        z_box.setValue(self.current[AxisName.Z])
        rotation = self._position_box(
            f"tiling_{tiling_number}_rotation_deg",
            self.limits[AxisName.ROTATION],
            "°",
        )
        rotation.setValue(0.0 if tiling_number == 1 else self.current[AxisName.ROTATION])
        rotation.setEnabled(tiling_number == 2)
        fields["z"] = z_box
        fields["rotation"] = rotation
        form.addRow("Fixed Z", z_box)
        form.addRow("Rotation", rotation)

        summary = QLabel()
        summary.setObjectName(f"tiling_{tiling_number}_summary")
        summary.setWordWrap(True)
        form.addRow("Resolved geometry", summary)
        self.fields.append(fields)
        self.summaries.append(summary)
        return group

    def _build_tiling(self, index: int) -> MosaicTilingSpec:
        """Normalize one card into endpoint geometry with outward rounding."""
        fields = self.fields[index]
        return MosaicTilingSpec.from_endpoints(
            fields["step_x"].value(),
            fields["step_y"].value(),
            fields["start_x"].value(),
            fields["start_y"].value(),
            fields["stop_x"].value(),
            fields["stop_y"].value(),
            fields["z"].value(),
            0.0 if index == 0 else fields["rotation"].value(),
        )

    @staticmethod
    def _position_box(
        name: str,
        limits: LimitsConfig | None,
        suffix: str,
    ) -> QDoubleSpinBox:
        """Create a simulation-bounded or application-unbounded position editor."""
        box = QDoubleSpinBox()
        box.setObjectName(name)
        box.setDecimals(6)
        minimum = limits.minimum if limits is not None else float("-inf")
        maximum = limits.maximum if limits is not None else float("inf")
        box.setRange(minimum, maximum)
        box.setSingleStep(0.001)
        box.setSuffix(suffix)
        return box

    def _update_summary(self) -> None:
        """Refresh normalized geometry and keep resume tile inside the selected tiling."""
        try:
            tilings = (self._build_tiling(0), self._build_tiling(1))
        except ValueError as exc:
            self.sequence_summary.setStyleSheet("color: #b00020;")
            self.sequence_summary.setText(f"Invalid geometry: {exc}")
            return

        # Each enabled card exposes adjusted endpoints so outward rounding is never hidden.
        for index, tiling in enumerate(tilings):
            if index == 1 and not self.run_tiling_two.isChecked():
                self.summaries[index].setText("Disabled for this run")
                continue
            requested = (
                self.fields[index]["stop_x"].value(),
                self.fields[index]["stop_y"].value(),
            )
            adjustment = (
                f"; requested stop rounded outward to {tiling.ending}"
                if requested != tiling.ending
                else ""
            )
            self.summaries[index].setText(
                f"{tiling.tiles_x} x {tiling.tiles_y} = {tiling.total_tiles} tiles; "
                f"resolved stop {tiling.ending}{adjustment}"
            )

        selected = tilings[self.resume_tiling.currentIndex()]
        self.resume_tile.blockSignals(True)
        self.resume_tile.setMaximum(selected.total_tiles)
        self.resume_tile.blockSignals(False)
        try:
            spec = self.build_spec()
        except ValueError as exc:
            self.sequence_summary.setStyleSheet("color: #b00020;")
            self.sequence_summary.setText(f"Invalid geometry: {exc}")
            return
        self.sequence_summary.setStyleSheet("")
        tiling_count = len(spec.tilings)
        self.sequence_summary.setText(
            f"Full run: {tiling_count} tiling{'s' if tiling_count > 1 else ''}, "
            f"{spec.total_tiles} total tiles; resume at Tiling "
            f"{spec.resume_tiling}, tile {spec.resume_tile}; safe rotation Z "
            f"{spec.safe_transition_z_mm:.6f} mm."
        )

    def _set_tiling_two_enabled(self, enabled: bool) -> None:
        """Apply the optional-tiling choice to editing and restart controls."""
        # A disabled second tiling cannot remain selected as the resume target.
        self.tiling_groups[1].setEnabled(enabled)
        if not enabled:
            self.resume_tiling.setCurrentIndex(0)
        self.resume_tiling.setEnabled(enabled)
        self._update_summary()

    def _normalize_stop_fields(self, index: int) -> None:
        """Commit outward-rounded XY stops after the user finishes one geometry edit."""
        try:
            tiling = self._build_tiling(index)
        except ValueError:
            self._update_summary()
            return

        # Update endpoints within simulation bounds; physical fields remain unrestricted.
        fields = self.fields[index]
        for axis, stop_box, resolved_stop in (
            (AxisName.X, fields["stop_x"], tiling.end_x_mm),
            (AxisName.Y, fields["stop_y"], tiling.end_y_mm),
        ):
            limits = self.limits[axis]
            if (limits is None or limits.contains(resolved_stop)) and not isclose(
                stop_box.value(), resolved_stop, rel_tol=0.0, abs_tol=0.0000005
            ):
                stop_box.blockSignals(True)
                stop_box.setValue(resolved_stop)
                stop_box.blockSignals(False)

        # Refresh once after both fields settle so counts and resume bounds agree.
        self._update_summary()

    def _accept_valid_geometry(self) -> None:
        """Accept only a complete sequence so callers never handle partial state."""
        try:
            self.validated_spec = self.build_spec()
        except ValueError as exc:
            self.sequence_summary.setStyleSheet("color: #b00020;")
            self.sequence_summary.setText(f"Invalid geometry: {exc}")
            return
        self.accept()
