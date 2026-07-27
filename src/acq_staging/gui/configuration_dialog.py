"""Structured application configuration editor."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from acq_staging.configuration import (
    AppConfig,
    DummyAcquirerConfig,
    config_from_dict,
    dummy_acquirer_config_from_dict,
    load_config,
    load_dummy_acquirer_config,
    save_config,
    save_dummy_acquirer_config,
)
from acq_staging.errors import ConfigurationError

PathPart = str | int


class ConfigurationDialog(QDialog):
    """Edit, load, validate, apply, and save complete configuration profiles."""

    def __init__(
        self,
        config: AppConfig | DummyAcquirerConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Configuration")
        self.resize(900, 720)
        self.validated_config: AppConfig | DummyAcquirerConfig | None = None
        self._config = config

        # The tree mirrors the nested JSON profile while editing only leaf values.
        self.tree = QTreeWidget()
        self.tree.setObjectName("configuration_tree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(("Configuration field", "Value"))
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        if header is None:
            raise RuntimeError("configuration tree header is unavailable")
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # File actions remain inside this window so all paths share validation.
        load_button = QPushButton("Load File")
        load_button.setObjectName("load_config_file")
        save_file_button = QPushButton("Save File")
        save_file_button.setObjectName("save_config_file")
        save_button = QPushButton("Save")
        save_button.setObjectName("apply_config")
        cancel_button = QPushButton("Cancel")
        load_button.clicked.connect(self._load_file)
        save_file_button.clicked.connect(self._save_file)
        save_button.clicked.connect(self._validate_and_accept)
        cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(load_button)
        buttons.addWidget(save_file_button)
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Edit values directly. Hover over any field for its purpose and units. "
                "Save applies the profile to the GUI; Save File writes a JSON profile."
            )
        )
        layout.addWidget(self.tree)
        layout.addLayout(buttons)
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        # Recreate every item after loading so paths and original types stay accurate.
        self.tree.clear()
        raw = self._config.to_dict()
        root = self.tree.invisibleRootItem()
        if root is None:
            raise RuntimeError("configuration tree root is unavailable")
        for key, value in raw.items():
            self._add_value(root, key, value, (key,))
        self.tree.expandToDepth(1)

    def _add_value(
        self,
        parent: QTreeWidgetItem,
        name: str,
        value: object,
        path: tuple[PathPart, ...],
    ) -> None:
        tooltip = self._tooltip(path)
        if isinstance(value, dict):
            # Mapping nodes organize fields but are not directly editable.
            item = QTreeWidgetItem(parent, (name, ""))
            item.setToolTip(0, tooltip)
            item.setToolTip(1, tooltip)
            for key, child in value.items():
                self._add_value(item, str(key), child, (*path, str(key)))
            return

        # Leaf items retain their path and type for later parsing and reconstruction.
        item = QTreeWidgetItem(parent, (name, self._format_value(value)))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        item.setData(1, Qt.ItemDataRole.UserRole, value)
        item.setToolTip(0, tooltip)
        item.setToolTip(1, tooltip)

    def _validated_from_tree(self) -> AppConfig | DummyAcquirerConfig:
        # Apply edited leaves to a copy before running full typed profile validation.
        raw = copy.deepcopy(self._config.to_dict())
        iterator = QTreeWidgetItemIterator(self.tree)
        while True:
            item = iterator.value()
            if item is None:
                break
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, tuple):
                original = item.data(1, Qt.ItemDataRole.UserRole)
                value = self._parse_value(item.text(1), original, ".".join(map(str, path)))
                self._set_path(raw, path, value)
            iterator += 1
        return self._config_from_dict(raw)

    def _config_from_dict(self, raw: object) -> AppConfig | DummyAcquirerConfig:
        """Parse one edited profile using the dialog's application schema."""
        return config_from_dict(raw)

    def _load_config(self, path: str) -> AppConfig | DummyAcquirerConfig:
        """Load one movement-application profile."""
        return load_config(path)

    def _save_config(
        self,
        config: AppConfig | DummyAcquirerConfig,
        path: Path,
    ) -> None:
        """Save one movement-application profile after narrowing its type."""
        if not isinstance(config, AppConfig):
            raise ConfigurationError("expected a movement application profile")
        save_config(config, path)

    def _validate_and_accept(self) -> None:
        # Save applies only a completely valid profile to the parent GUI.
        try:
            self.validated_config = self._validated_from_tree()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        self.accept()

    def _load_file(self) -> None:
        # Loading replaces displayed values but does not apply them until Save.
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load configuration",
            "",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            self._config = self._load_config(path)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        self._rebuild_tree()

    def _save_file(self) -> None:
        # Validate before asking where to write so invalid profiles never reach disk.
        try:
            config = self._validated_from_tree()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save configuration",
            "",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            self._save_config(config, Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Unable to save configuration", str(exc))
            return
        self._config = config

    @staticmethod
    def _format_value(value: object) -> str:
        if value is None or isinstance(value, (bool, list, tuple)):
            return json.dumps(value)
        return str(value)

    @staticmethod
    def _parse_value(text: str, original: object, field: str) -> object:
        stripped = text.strip()
        try:
            # Parse with the original leaf type while allowing null/list JSON transitions.
            if original is None or isinstance(original, (bool, list, tuple)):
                return json.loads(stripped)
            if isinstance(original, int):
                return int(stripped)
            if isinstance(original, float):
                return float(stripped)
            return text
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"{field}: invalid value {text!r}") from exc

    @staticmethod
    def _set_path(raw: dict[str, Any], path: tuple[PathPart, ...], value: object) -> None:
        # Walk the stored key path to replace exactly one nested profile leaf.
        current: Any = raw
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = value

    @staticmethod
    def _tooltip(path: tuple[PathPart, ...]) -> str:
        # Known leaves receive operator guidance and uncommon fields get a safe fallback.
        dotted = ".".join(map(str, path))
        leaf = str(path[-1])
        descriptions = {
            "schema_version": "Profile format version; must match the application schema.",
            "profile_name": "Human-readable name shown in the main window title.",
            "enabled": "Whether Connect All includes this component.",
            "adapter": "Backend used for simulation or physical hardware communication.",
            "minimum": "Lowest simulated position; hardware limits come from its API.",
            "maximum": "Highest simulated position; hardware limits come from its API.",
            "velocity": "Maximum commanded motion velocity in the component's configured units.",
            "acceleration": "Maximum commanded motion acceleration in configured units.",
            "jog_increment": "Distance or angle used by the GUI jog buttons.",
            "test_distance": "Small displacement used by an acknowledged motion test.",
            "position_tolerance": "Allowed position error in millimetres or degrees.",
            "operation_timeout_s": "Maximum permitted operation duration in seconds.",
            "port": "Serial port, or DAQ port in P0 or port0 notation.",
            "baud_rate": "Serial communication speed in bits per second.",
            "serial_number": "Vendor device serial number used for exact identity matching.",
            "axis_number": "Vendor channel or axis number assigned to this component.",
            "device": "NI-DAQmx device name, for example Dev1.",
            "line_count": "Physical DAQ port width from 1 through 8.",
            "lines": "DAQ pins to use, or null for the whole configured port.",
            "polling_interval_ms": "Background state refresh interval in milliseconds.",
            "step_x_mm": "Default X step for the per-run mosaic setup, in millimetres.",
            "step_y_mm": "Default Y step for the per-run mosaic setup, in millimetres.",
            "settling_time_s": "Wait after reaching a mosaic tile, in seconds.",
            "dwell_time_s": "Optional per-tile dwell duration in seconds.",
            "ready_output_line": "Digital Out line held HIGH when movement is complete.",
            "complete_input_line": "Digital In line raised by acquisition when data is ready.",
            "acquisition_timeout_s": "Maximum wait for acquisition Done to become HIGH.",
            "reset_timeout_s": "Maximum wait for the peer signal to return LOW.",
            "ready_input_line": "Digital In line receiving movement Ready.",
            "complete_output_line": "Digital Out line held HIGH after dummy acquisition.",
            "acquisition_duration_s": "Dummy acquisition duration before Done becomes HIGH.",
        }
        return descriptions.get(leaf, f"Configuration value for {dotted}.")


class DummyAcquirerConfigurationDialog(ConfigurationDialog):
    """Edit standalone dummy-acquirer profiles with the shared tree interface."""

    def __init__(self, config: DummyAcquirerConfig, parent: QWidget | None = None) -> None:
        super().__init__(config, parent)
        self.setWindowTitle("Edit Dummy Acquirer Configuration")

    def _config_from_dict(self, raw: object) -> DummyAcquirerConfig:
        return dummy_acquirer_config_from_dict(raw)

    def _load_config(self, path: str) -> DummyAcquirerConfig:
        return load_dummy_acquirer_config(path)

    def _save_config(
        self,
        config: AppConfig | DummyAcquirerConfig,
        path: Path,
    ) -> None:
        if not isinstance(config, DummyAcquirerConfig):
            raise ConfigurationError("expected a dummy-acquirer profile")
        save_dummy_acquirer_config(config, path)
