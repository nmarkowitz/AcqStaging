"""Standalone dummy-acquirer application entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from acq_staging.gui.dummy_acquirer_window import DummyAcquirerWindow
from acq_staging.logging_setup import configure_logging


def main() -> int:
    """Launch the dummy acquirer in a safe disconnected simulation profile."""
    # The standalone process keeps its troubleshooting log separate from movement control.
    configure_logging(filename="dummy_acquirer.log")
    app = QApplication(sys.argv)
    window = DummyAcquirerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
