from dataclasses import replace
from math import isinf

import pytest
from PyQt6.QtWidgets import QApplication, QDockWidget, QPushButton
from pytestqt.qtbot import QtBot

from acq_staging.configuration import default_simulation_config
from acq_staging.gui.axis_panel import AxisPanel
from acq_staging.gui.dummy_acquirer_window import DummyAcquirerWindow
from acq_staging.gui.main_window import MainWindow
from acq_staging.gui.workers import FunctionWorker
from acq_staging.models import AdapterType, AxisName, DigitalDirection, DigitalStatus


def test_main_window_starts_in_safe_simulation(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert "Default Simulation" in window.windowTitle()
    assert window.pose_label.text() == "[—, —, —, —]"


def test_window_has_four_independent_panels(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert set(window.panels) == set(window.config.components)
    for axis, panel in window.panels.items():
        identify = panel.findChild(QPushButton, f"{axis.value}_identify")
        assert identify is not None
        assert identify.text() == "Identify"
    assert QApplication.instance() is not None


def test_physical_axis_panel_has_no_application_travel_bounds(qtbot: QtBot) -> None:
    config = default_simulation_config().components[AxisName.ROTATION]
    component = replace(
        config,
        adapter=AdapterType.THORLABS_BSC101,
        limits=None,
    )
    panel = AxisPanel(AxisName.ROTATION, component)
    qtbot.addWidget(panel)

    assert isinf(panel.target.minimum()) and panel.target.minimum() < 0
    assert isinf(panel.target.maximum()) and panel.target.maximum() > 0
    assert isinf(panel.increment.maximum())


def test_window_exposes_explicit_mosaic_controls(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.findChild(QPushButton, "start_mosaic") is not None
    assert window.findChild(QPushButton, "stop_mosaic") is None
    assert window.findChild(QPushButton, "pause_mosaic") is not None
    assert window.findChild(QPushButton, "continue_mosaic") is not None
    assert window.findChild(QPushButton, "cancel_mosaic") is not None
    viewer_dock = window.findChild(QDockWidget, "axis_mosaic_dock")
    assert viewer_dock is not None
    assert (
        viewer_dock.features()
        == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
    )
    assert not viewer_dock.isFloating()


def test_window_removes_origin_and_reset_controls(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.findChild(QPushButton, "set_origin") is None
    assert window.findChild(QPushButton, "reset_to_origin") is None


def test_operation_log_labels_pose_reference(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    worker = FunctionWorker(lambda: None)

    window._operation_finished(worker, "Test operation")

    assert "Controller-home position" in window.log.toPlainText()


def test_window_has_digital_input_and_output_panels(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert set(window.digital_panels) == set(DigitalDirection)
    digital_input = window.digital_panels[DigitalDirection.INPUT]
    assert len(digital_input.pin_boxes) == 8
    output = window.digital_panels[DigitalDirection.OUTPUT]
    assert len(output.pin_boxes) == 8
    assert [output.pin_boxes[line].text() for line in range(8)] == [
        str(line) for line in range(8)
    ]
    assert all(
        button.width() == button.height() == 48
        for button in output.pin_boxes.values()
    )
    assert output.pin_boxes[3].isEnabled()
    assert not output.pin_boxes[4].isEnabled()
    assert "NOT CONFIGURED" in output.pin_boxes[4].toolTip()

    # Input and output circles use the same green semantic state.
    digital_input.update_status(DigitalStatus(True, {0: True, 1: False}))
    assert digital_input.pin_boxes[0].property("pinState") == "high"
    assert digital_input.pin_boxes[1].property("pinState") == "low"
    output.pin_boxes[3].setChecked(True)
    assert output.pin_boxes[3].property("pinState") == "high"
    assert output.output_states()[3] is True


def test_configuration_files_are_managed_from_editor_window(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    labels = {button.text() for button in window.findChildren(QPushButton)}

    assert "Edit Configuration" in labels
    assert "Load Configuration" not in labels
    assert "Save Configuration" not in labels


def test_full_mosaic_rejects_disconnected_axes(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    window._confirm_mosaic(((AxisName.Z,), ()))

    assert "Z" in errors[0]


def test_full_mosaic_confirmation_passes_one_sequence(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.service.connect_all()
    window.service.home_all()
    run_spec = window.service.default_mosaic_sequence_spec()
    runs: list[object] = []
    monkeypatch.setattr(window, "_get_mosaic_run_spec", lambda: run_spec)
    monkeypatch.setattr(window, "_confirm", lambda _title, _text: True)
    monkeypatch.setattr(window, "_run_mosaic", runs.append)

    window._confirm_mosaic(((), ()))

    assert runs == [run_spec]


def test_single_tiling_confirmation_omits_second_geometry(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.service.connect_all()
    window.service.home_all()
    run_spec = replace(
        window.service.default_mosaic_sequence_spec(),
        run_tiling_two=False,
    )
    confirmations: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_get_mosaic_run_spec", lambda: run_spec)

    def reject_after_capture(title: str, text: str) -> bool:
        confirmations.append((title, text))
        return False

    monkeypatch.setattr(window, "_confirm", reject_after_capture)

    window._confirm_mosaic(((), ()))

    title, text = confirmations[0]
    assert "single-tiling" in title
    assert "Tiling 1:" in text
    assert "Tiling 2:" not in text


def test_dummy_acquirer_window_starts_disconnected_with_explicit_controls(
    qtbot: QtBot,
) -> None:
    window = DummyAcquirerWindow()
    qtbot.addWidget(window)

    assert "Dummy Acquirer Simulation" in window.windowTitle()
    for name in ("start_listening", "stop_listening", "complete_now"):
        assert window.findChild(QPushButton, name) is not None
    assert set(window.digital_panels) == set(DigitalDirection)
