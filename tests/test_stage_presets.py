import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from acq_staging.adapters.simulated import SimulatedMotionAxis
from acq_staging.configuration import (
    DEFAULT_STAGE_PRESETS_PATH,
    default_simulation_config,
    load_stage_presets,
    save_stage_presets,
    stage_presets_from_dict,
)
from acq_staging.errors import AdapterError, ConfigurationError, MotionSafetyError
from acq_staging.factory import build_simulation_axes
from acq_staging.gui.main_window import MainWindow
from acq_staging.models import AxisName, StagePreset
from acq_staging.services import MotionService


def preset_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "imaging_order": ["z", "rotation", "x", "y"],
        "positions": {
            "imaging": {"x": 20.0, "y": 30.0, "z": 40.0, "rotation": 90.0},
            "slicing": {"x": 1.0, "y": 2.0, "z": 3.0, "rotation": 0.0},
        },
    }


class RecordingService(MotionService):
    """Record commanded moves and stops without changing preset orchestration."""

    def __init__(self) -> None:
        config = default_simulation_config()
        super().__init__(config, build_simulation_axes(config))
        self.commanded: list[tuple[AxisName, float]] = []
        self.stopped: list[AxisName] = []

    def move_absolute(self, axis: AxisName, destination: float) -> None:
        self.commanded.append((axis, destination))
        super().move_absolute(axis, destination)

    def stop(self, axis: AxisName) -> None:
        self.stopped.append(axis)
        super().stop(axis)


def ready_service() -> RecordingService:
    service = RecordingService()
    service.connect_all()
    service.home_all()
    return service


def test_repository_presets_file_is_valid() -> None:
    presets = load_stage_presets(DEFAULT_STAGE_PRESETS_PATH)
    assert presets.order(StagePreset.SLICING) == tuple(reversed(presets.imaging_order))


def test_slicing_reverses_imaging_order() -> None:
    presets = stage_presets_from_dict(preset_data())

    assert presets.moves(StagePreset.IMAGING) == (
        (AxisName.Z, 40.0),
        (AxisName.ROTATION, 90.0),
        (AxisName.X, 20.0),
        (AxisName.Y, 30.0),
    )
    assert presets.moves(StagePreset.SLICING) == (
        (AxisName.Y, 2.0),
        (AxisName.X, 1.0),
        (AxisName.ROTATION, 0.0),
        (AxisName.Z, 3.0),
    )


def test_presets_round_trip(tmp_path: Path) -> None:
    presets = stage_presets_from_dict(preset_data())
    path = tmp_path / "presets.json"
    save_stage_presets(presets, path)
    assert load_stage_presets(path) == presets


def test_order_may_cover_a_subset_of_axes() -> None:
    data = preset_data()
    data["imaging_order"] = ["z", "x"]
    data["positions"] = {
        "imaging": {"x": 1.0, "z": 2.0},
        "slicing": {"x": 3.0, "z": 4.0},
    }
    presets = stage_presets_from_dict(data)
    assert presets.moves(StagePreset.SLICING) == ((AxisName.X, 3.0), (AxisName.Z, 4.0))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(imaging_order=[]), "at least one axis"),
        (lambda data: data.update(imaging_order=["x", "x", "y", "z"]), "only once"),
        (lambda data: data.update(imaging_order=["x", "y", "z", "tilt"]), "tilt"),
        (lambda data: data.update(imaging_order="xyz"), "list"),
        (lambda data: data["positions"].pop("slicing"), "imaging and slicing"),
        (lambda data: data["positions"]["imaging"].pop("z"), "exactly the axes"),
        (lambda data: data["positions"]["slicing"].update(x="5"), "expected a number"),
        (lambda data: data["positions"]["slicing"].update(x=True), "expected a number"),
        (lambda data: data["positions"]["slicing"].update(x=float("nan")), "finite"),
    ],
)
def test_rejects_invalid_presets(
    change: Callable[[dict[str, Any]], object], message: str
) -> None:
    data = preset_data()
    change(data)
    with pytest.raises(ConfigurationError, match=message):
        stage_presets_from_dict(data)


def test_missing_presets_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_stage_presets(tmp_path / "missing.json")


def test_preset_moves_axes_sequentially_in_order() -> None:
    service = ready_service()
    progress: list[AxisName] = []
    moves = stage_presets_from_dict(preset_data()).moves(StagePreset.IMAGING)

    completed = service.move_to_preset(moves, progress.append)

    assert service.commanded == list(moves)
    assert completed == progress == [axis for axis, _ in moves]
    assert service.pose.as_vector() == (20.0, 30.0, 40.0, 90.0)


def test_preset_requires_connected_axes_before_any_motion() -> None:
    service = RecordingService()
    service.connect(AxisName.Z)
    service.home(AxisName.Z)

    with pytest.raises(MotionSafetyError, match="x: not connected"):
        service.move_to_preset(((AxisName.Z, 5.0), (AxisName.X, 5.0)))
    assert service.commanded == []


def test_preset_requires_homed_axes_before_any_motion() -> None:
    service = RecordingService()
    service.connect_all()
    service.home(AxisName.Z)

    with pytest.raises(MotionSafetyError, match="homed"):
        service.move_to_preset(((AxisName.Z, 5.0), (AxisName.X, 5.0)))
    assert service.commanded == []


def test_preset_preflights_simulation_limits_before_any_motion() -> None:
    service = ready_service()

    with pytest.raises(MotionSafetyError, match="outside simulation limits"):
        service.move_to_preset(((AxisName.X, 5.0), (AxisName.Z, 500.0)))
    assert service.commanded == []


def test_preset_failure_stops_sequence_and_requests_stop() -> None:
    service = ready_service()
    x_axis = service.axes[AxisName.X]
    assert isinstance(x_axis, SimulatedMotionAxis)
    x_axis.inject_failure("injected")

    with pytest.raises(AdapterError, match="injected"):
        service.move_to_preset(
            ((AxisName.Z, 5.0), (AxisName.X, 5.0), (AxisName.Y, 5.0))
        )

    # Y is never commanded after X fails, and every participating axis is stopped.
    assert service.commanded == [(AxisName.Z, 5.0), (AxisName.X, 5.0)]
    assert service.stopped == [AxisName.Z, AxisName.X, AxisName.Y]
    # The run lock is released so a later explicit request can proceed.
    service.move_to_preset(((AxisName.Y, 6.0),))


def test_preset_rejects_concurrent_sequence() -> None:
    service = ready_service()
    service._mosaic_lock.acquire()
    try:
        with pytest.raises(MotionSafetyError, match="already running"):
            service.move_to_preset(((AxisName.X, 5.0),))
    finally:
        service._mosaic_lock.release()
    assert service.commanded == []


def test_window_exposes_prepare_buttons(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    imaging = window.findChild(QPushButton, "prepare_imaging")
    slicing = window.findChild(QPushButton, "prepare_slicing")
    assert imaging is not None and imaging.text() == "Prepare for Imaging"
    assert slicing is not None and slicing.text() == "Prepare for Slicing"


def test_prepare_requires_confirmation_before_motion(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "presets.json"
    path.write_text(json.dumps(preset_data()), encoding="utf-8")
    window = MainWindow(stage_presets_path=path)
    qtbot.addWidget(window)
    confirmations: list[str] = []
    calls: list[object] = []

    def reject(_title: str, text: str) -> bool:
        confirmations.append(text)
        return False

    monkeypatch.setattr(window, "_confirm", reject)
    monkeypatch.setattr(window.service, "move_to_preset", lambda *args: calls.append(args))

    window.request_stage_preset(StagePreset.SLICING)

    assert calls == []
    text = confirmations[0]
    assert text.index("Y ->") < text.index("X ->") < text.index("ROTATION ->")
    assert text.index("ROTATION ->") < text.index("Z ->")
    assert "0.0000 deg" in text


def test_prepare_runs_ordered_preset_in_worker(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "presets.json"
    path.write_text(json.dumps(preset_data()), encoding="utf-8")
    window = MainWindow(stage_presets_path=path)
    qtbot.addWidget(window)
    window.poll_timer.stop()
    window.service.connect_all()
    window.service.home_all()
    monkeypatch.setattr(window, "_confirm", lambda _title, _text: True)
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    window.request_stage_preset(StagePreset.IMAGING)

    qtbot.waitUntil(lambda: not window._workers, timeout=5000)
    assert errors == []
    assert window.service.pose.as_vector() == (20.0, 30.0, 40.0, 90.0)
    assert "Prepare for Imaging finished" in window.log.toPlainText()


def test_prepare_reports_invalid_presets_file(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    window = MainWindow(stage_presets_path=tmp_path / "missing.json")
    qtbot.addWidget(window)
    errors: list[str] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    def fail_confirm(_title: str, _text: str) -> bool:
        pytest.fail("confirmation must not open for an invalid presets file")

    monkeypatch.setattr(window, "_confirm", fail_confirm)

    window.request_stage_preset(StagePreset.IMAGING)

    assert "Unable to load stage presets" in errors[0]
