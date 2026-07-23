"""Application entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtWidgets import QApplication

from acq_staging.configuration import AppConfig
from acq_staging.gui.main_window import MainWindow
from acq_staging.logging_setup import configure_logging


@dataclass(slots=True)
class GuiSession:
    """Strong references and event-loop controls for an interactive GUI session."""

    app: QApplication
    window: MainWindow

    def process_events(self) -> None:
        """Process pending Qt events once for a plain Python interpreter."""
        self.app.processEvents()

    def exec(self) -> int:
        """Enter the blocking Qt event loop used by the console command."""
        return self.app.exec()

    def close(self) -> None:
        """Close the window and process its normal cleanup callbacks."""
        self.window.close()
        self.app.processEvents()


def launch_gui(
    config: AppConfig | None = None,
    argv: Sequence[str] | None = None,
) -> GuiSession:
    """Show a non-blocking GUI and return objects for interactive exploration.

    IPython and Jupyter callers should enable Qt event-loop integration with
    ``%gui qt`` before calling this function. Plain Python callers can invoke
    ``GuiSession.process_events`` whenever they need the window to refresh.
    """
    # Interactive relaunches reuse the process-wide application required by Qt.
    configure_logging()
    existing = QApplication.instance()
    if existing is None:
        app = QApplication(list(argv) if argv is not None else sys.argv)
    elif isinstance(existing, QApplication):
        app = existing
    else:
        raise RuntimeError("a non-GUI Qt application already exists in this process")

    # MainWindow defaults to disconnected simulations and performs no startup motion.
    window = MainWindow(config)
    window.show()
    return GuiSession(app, window)


def main() -> int:
    """Launch the application in its safe, disconnected simulation configuration."""
    # The console entry point intentionally retains its normal blocking behavior.
    return launch_gui().exec()


if __name__ == "__main__":
    raise SystemExit(main())
