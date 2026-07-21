"""Thread-pool workers for non-GUI operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    """Signals emitted by a background operation."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)
    finished = pyqtSignal()


class FunctionWorker(QRunnable):
    """Run one callable in the global Qt thread pool."""

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        # Convert worker outcomes into queued Qt signals safe for GUI consumption.
        try:
            result = self.function()
        except Exception as exc:  # Errors cross the Qt boundary as text.
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
