"""Per-run mosaic geometry editor."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from acq_staging.configuration import ComponentConfig, MosaicConfig
from acq_staging.models import AxisName, MosaicInputMode, MosaicRunSpec, Pose

LINEAR_AXES = (AxisName.X, AxisName.Y, AxisName.Z)


class MosaicSetupDialog(QDialog):
    """Collect and validate transient geometry before mosaic confirmation."""

    def __init__(
        self,
        mosaic: MosaicConfig,
        components: dict[AxisName, ComponentConfig],
        current_pose: Pose,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Up Mosaic Run")
        self.setModal(True)
        self.validated_spec: MosaicRunSpec | None = None

        # The mode choice makes inactive geometry fields visibly unavailable.
        self.count_mode = QRadioButton("Number of tiles")
        self.count_mode.setObjectName("mosaic_mode_tile_counts")
        self.endpoint_mode = QRadioButton("Beginning and end points")
        self.endpoint_mode.setObjectName("mosaic_mode_endpoints")
        modes = QButtonGroup(self)
        modes.addButton(self.count_mode)
        modes.addButton(self.endpoint_mode)
        self.count_mode.setChecked(True)
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.count_mode)
        mode_layout.addWidget(self.endpoint_mode)
        mode_layout.addStretch()

        # Step magnitudes are copied from the profile but remain transient edits.
        self.step_boxes: dict[AxisName, QDoubleSpinBox] = {}
        step_group = QGroupBox("Step sizes (mm, this run only)")
        step_form = QFormLayout(step_group)
        profile_steps = (mosaic.step_x_mm, mosaic.step_y_mm, mosaic.step_z_mm)
        for axis, value in zip(LINEAR_AXES, profile_steps, strict=True):
            step_box = QDoubleSpinBox()
            step_box.setObjectName(f"mosaic_step_{axis.value}_mm")
            step_box.setDecimals(6)
            travel = components[axis].limits.maximum - components[axis].limits.minimum
            step_box.setRange(0.0, max(travel, value))
            step_box.setSingleStep(0.001)
            step_box.setValue(value)
            step_box.setSuffix(" mm")
            self.step_boxes[axis] = step_box
            step_form.addRow(axis.value.upper(), step_box)

        # Count mode preserves the profile's current XYZ counts as editable defaults.
        self.count_boxes: dict[AxisName, QSpinBox] = {}
        self.count_group = QGroupBox("Tile counts")
        count_form = QFormLayout(self.count_group)
        profile_counts = (mosaic.tiles_x, mosaic.tiles_y, mosaic.tiles_z)
        for axis, value in zip(LINEAR_AXES, profile_counts, strict=True):
            count_box = QSpinBox()
            count_box.setObjectName(f"mosaic_tiles_{axis.value}")
            count_box.setRange(1, 2_147_483_647)
            count_box.setValue(value)
            self.count_boxes[axis] = count_box
            count_form.addRow(axis.value.upper(), count_box)

        # Endpoint defaults describe the equivalent profile-sized grid from the current pose.
        self.begin_boxes: dict[AxisName, QDoubleSpinBox] = {}
        self.end_boxes: dict[AxisName, QDoubleSpinBox] = {}
        self.endpoint_group = QGroupBox("Absolute beginning and end points (mm)")
        endpoint_form = QFormLayout(self.endpoint_group)
        observed = current_pose.as_vector()[:3]
        for axis, position, step, count in zip(
            LINEAR_AXES,
            observed,
            profile_steps,
            profile_counts,
            strict=True,
        ):
            beginning = (
                position if position is not None else components[axis].simulation_start
            )
            begin_box = self._coordinate_box(axis, "begin", components[axis])
            end_box = self._coordinate_box(axis, "end", components[axis])
            begin_box.setValue(beginning)
            end_box.setValue(beginning + (count - 1) * step)
            self.begin_boxes[axis] = begin_box
            self.end_boxes[axis] = end_box
            row = QHBoxLayout()
            row.addWidget(QLabel("Begin"))
            row.addWidget(begin_box)
            row.addWidget(QLabel("End"))
            row.addWidget(end_box)
            endpoint_form.addRow(axis.value.upper(), row)

        # Live validation exposes derived counts before the operator reviews motion.
        self.summary = QLabel()
        self.summary.setObjectName("mosaic_geometry_summary")
        self.summary.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        review = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if review is not None:
            review.setText("Review Motion")
        buttons.accepted.connect(self._accept_valid_geometry)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose how to define this run. Step edits apply only to this mosaic "
                "and do not change the saved configuration profile."
            )
        )
        layout.addLayout(mode_layout)
        layout.addWidget(step_group)
        layout.addWidget(self.count_group)
        layout.addWidget(self.endpoint_group)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)

        # Every geometry edit immediately refreshes the derived grid summary.
        self.count_mode.toggled.connect(self._update_mode)
        self.endpoint_mode.toggled.connect(self._update_mode)
        coordinate_boxes = (
            *self.step_boxes.values(),
            *self.begin_boxes.values(),
            *self.end_boxes.values(),
        )
        for coordinate_box in coordinate_boxes:
            coordinate_box.valueChanged.connect(self._update_summary)
        for count_box in self.count_boxes.values():
            count_box.valueChanged.connect(self._update_summary)
        self._update_mode()

    @property
    def input_mode(self) -> MosaicInputMode:
        """Return the currently selected geometry mode."""
        return (
            MosaicInputMode.TILE_COUNTS
            if self.count_mode.isChecked()
            else MosaicInputMode.ENDPOINTS
        )

    def build_spec(self) -> MosaicRunSpec:
        """Build validated geometry from the currently displayed values."""
        steps = tuple(self.step_boxes[axis].value() for axis in LINEAR_AXES)
        if self.input_mode is MosaicInputMode.TILE_COUNTS:
            counts = tuple(self.count_boxes[axis].value() for axis in LINEAR_AXES)
            return MosaicRunSpec.from_tile_counts(
                steps[0],
                steps[1],
                steps[2],
                counts[0],
                counts[1],
                counts[2],
            )

        # Endpoint construction derives and validates inclusive tile counts.
        beginning = tuple(self.begin_boxes[axis].value() for axis in LINEAR_AXES)
        ending = tuple(self.end_boxes[axis].value() for axis in LINEAR_AXES)
        return MosaicRunSpec.from_endpoints(
            steps[0],
            steps[1],
            steps[2],
            beginning[0],
            beginning[1],
            beginning[2],
            ending[0],
            ending[1],
            ending[2],
        )

    @staticmethod
    def _coordinate_box(
        axis: AxisName,
        role: str,
        component: ComponentConfig,
    ) -> QDoubleSpinBox:
        """Create one absolute coordinate editor bounded by software limits."""
        box = QDoubleSpinBox()
        box.setObjectName(f"mosaic_{role}_{axis.value}_mm")
        box.setDecimals(6)
        box.setRange(component.limits.minimum, component.limits.maximum)
        box.setSingleStep(0.001)
        box.setSuffix(" mm")
        return box

    def _update_mode(self) -> None:
        # Only the fields owned by the selected input mode remain interactive.
        self.count_group.setEnabled(self.input_mode is MosaicInputMode.TILE_COUNTS)
        self.endpoint_group.setEnabled(self.input_mode is MosaicInputMode.ENDPOINTS)
        self._update_summary()

    def _update_summary(self) -> None:
        # Invalid endpoint spacing stays visible without closing the setup dialog.
        try:
            spec = self.build_spec()
        except ValueError as exc:
            self.summary.setStyleSheet("color: #b00020;")
            self.summary.setText(f"Invalid geometry: {exc}")
            return
        self.summary.setStyleSheet("")
        self.summary.setText(
            f"Resolved grid: {spec.tiles_x} x {spec.tiles_y} x {spec.tiles_z} "
            f"= {spec.total_tiles} tiles; signed extents {spec.extents_mm} mm."
        )

    def _accept_valid_geometry(self) -> None:
        # Accept only a complete spec so the caller never handles partial widget state.
        try:
            self.validated_spec = self.build_spec()
        except ValueError as exc:
            self.summary.setStyleSheet("color: #b00020;")
            self.summary.setText(f"Invalid geometry: {exc}")
            return
        self.accept()
