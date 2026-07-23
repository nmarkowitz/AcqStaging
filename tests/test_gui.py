import pytest
from PyQt6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot

from acq_staging.gui.dummy_acquirer_window import DummyAcquirerWindow
from acq_staging.gui.main_window import MainWindow
from acq_staging.models import AxisName, DigitalDirection


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


def test_window_exposes_explicit_mosaic_controls(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.findChild(QPushButton, "start_mosaic") is not None
    assert window.findChild(QPushButton, "stop_mosaic") is not None


def test_window_has_digital_input_and_output_panels(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert set(window.digital_panels) == set(DigitalDirection)
    assert len(window.digital_panels[DigitalDirection.INPUT].pin_boxes) == 8
    output = window.digital_panels[DigitalDirection.OUTPUT]
    assert len(output.pin_boxes) == 8
    assert output.pin_boxes[3].isEnabled()
    assert not output.pin_boxes[4].isEnabled()
    assert "NOT CONFIGURED" in output.pin_boxes[4].text()


def test_configuration_files_are_managed_from_editor_window(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    labels = {button.text() for button in window.findChildren(QPushButton)}

    assert "Edit Configuration" in labels
    assert "Load Configuration" not in labels
    assert "Save Configuration" not in labels


def test_partial_mosaic_confirmation_lists_disconnected_axes(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    prompts: list[str] = []
    runs: list[tuple[frozenset[AxisName], object]] = []
    run_spec = window.service.default_mosaic_run_spec()

    def confirm(_title: str, text: str) -> bool:
        prompts.append(text)
        return True

    monkeypatch.setattr(window, "_confirm", confirm)
    monkeypatch.setattr(window, "_get_mosaic_run_spec", lambda: run_spec)
    monkeypatch.setattr(
        window,
        "_run_mosaic",
        lambda allowed, spec: runs.append((allowed, spec)),
    )

    window._confirm_mosaic(((AxisName.Z,), ()))

    assert "Z" in prompts[0]
    assert runs == [(frozenset({AxisName.Z}), run_spec)]


def test_dummy_acquirer_window_starts_disconnected_with_explicit_controls(
    qtbot: QtBot,
) -> None:
    window = DummyAcquirerWindow()
    qtbot.addWidget(window)

    assert "Dummy Acquirer Simulation" in window.windowTitle()
    for name in ("start_listening", "stop_listening", "complete_now"):
        assert window.findChild(QPushButton, name) is not None
    assert set(window.digital_panels) == set(DigitalDirection)
