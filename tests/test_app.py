from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

import acq_staging.app as app_module
from acq_staging.app import launch_gui


def test_launch_gui_returns_nonblocking_simulated_session(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "configure_logging", lambda: Path("unused.log"))

    session = launch_gui(argv=("acq-staging-interactive",))
    qtbot.addWidget(session.window)
    session.process_events()

    assert session.app is QApplication.instance()
    assert session.window.isVisible()
    assert all(not axis.status().connected for axis in session.window.service.axes.values())
    session.close()


def test_main_keeps_the_console_entry_point_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def exec(self) -> int:
            return 17

    monkeypatch.setattr(app_module, "launch_gui", FakeSession)

    assert app_module.main() == 17
