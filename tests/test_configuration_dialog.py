from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QPushButton,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
)
from pytestqt.qtbot import QtBot

from acq_staging.configuration import (
    default_dummy_acquirer_config,
    default_simulation_config,
    load_config,
)
from acq_staging.gui.configuration_dialog import (
    ConfigurationDialog,
    DummyAcquirerConfigurationDialog,
)


def find_leaf(
    dialog: ConfigurationDialog,
    path: tuple[str, ...],
) -> QTreeWidgetItem:
    iterator = QTreeWidgetItemIterator(dialog.tree)
    while iterator.value() is not None:
        item = iterator.value()
        assert item is not None
        if item.data(0, Qt.ItemDataRole.UserRole) == path:
            return item
        iterator += 1
    raise AssertionError(f"missing configuration field {path}")


def test_configuration_dialog_exposes_editable_tooltipped_fields(qtbot: QtBot) -> None:
    dialog = ConfigurationDialog(default_simulation_config())
    qtbot.addWidget(dialog)

    iterator = QTreeWidgetItemIterator(dialog.tree)
    leaf_count = 0
    while iterator.value() is not None:
        item = iterator.value()
        assert item is not None
        if isinstance(item.data(0, Qt.ItemDataRole.UserRole), tuple):
            leaf_count += 1
            assert item.toolTip(0)
            assert item.toolTip(1)
            assert item.flags() & Qt.ItemFlag.ItemIsEditable
        iterator += 1

    assert leaf_count > 50
    for object_name in ("load_config_file", "save_config_file", "apply_config"):
        assert dialog.findChild(QPushButton, object_name) is not None


def test_configuration_dialog_saves_and_loads_files(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = ConfigurationDialog(default_simulation_config())
    qtbot.addWidget(dialog)
    profile_item = find_leaf(dialog, ("profile_name",))
    profile_item.setText(1, "Saved From Structured Editor")
    destination = Path("configuration-dialog-test.json")
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), "JSON files (*.json)"),
    )

    dialog._save_file()

    assert load_config(destination).profile_name == "Saved From Structured Editor"
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(destination), "JSON files (*.json)"),
    )
    dialog._load_file()
    assert find_leaf(dialog, ("profile_name",)).text(1) == "Saved From Structured Editor"
    destination.unlink(missing_ok=True)


def test_dummy_configuration_dialog_exposes_handshake_tooltips(qtbot: QtBot) -> None:
    dialog = DummyAcquirerConfigurationDialog(default_dummy_acquirer_config())
    qtbot.addWidget(dialog)

    duration = find_leaf(dialog, ("handshake", "acquisition_duration_s"))

    assert "Dummy acquisition duration" in duration.toolTip(0)
