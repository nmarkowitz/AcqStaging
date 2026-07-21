"""Application entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from acq_staging.gui.main_window import MainWindow
from acq_staging.logging_setup import configure_logging


def main() -> int:
    """Launch the application in its safe, disconnected simulation configuration."""
    # MainWindow supplies the disconnected simulation profile when none is provided.
    configure_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
