"""Dedicated chronological audit logging for mosaic hardware activity."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIRECTORY = PROJECT_ROOT / "logs"

class MosaicAuditLogger:
    """Write one line-buffered, timestamped event stream for a mosaic run."""

    def __init__(
        self,
        directory: str | Path = DEFAULT_LOG_DIRECTORY,
        now: Callable[[], datetime] | None = None,
        initial_settings: dict[str, object] | None = None,
    ) -> None:
        # Local time supplies both the required filename and readable event timestamps.
        self._now = now or (lambda: datetime.now().astimezone())
        log_directory = Path(directory)
        log_directory.mkdir(parents=True, exist_ok=True)
        self.path = log_directory / f"{self._now().strftime('%Y_%m_%d_%H-%M-%S')}.log"
        # Exclusive creation prevents two same-second runs from sharing one audit file.
        self._stream = self.path.open("x", encoding="utf-8", buffering=1)
        self._lock = threading.RLock()
        self._closed = False
        # Production mosaic logs commit settings before any callback can append an event.
        if initial_settings is not None:
            self.log("MOSAIC_SETTINGS", initial_settings)

    def log(self, event: str, details: dict[str, object] | None = None) -> None:
        """Synchronously append an event with local timestamp and JSON details."""
        timestamp = self._now().isoformat(timespec="milliseconds")
        payload = json.dumps(details or {}, sort_keys=True, separators=(",", ":"))
        # Lock and flush synchronously so the file reflects completed hardware steps.
        with self._lock:
            if self._closed:
                return
            self._stream.write(f"{timestamp} | {event} | {payload}\n")
            self._stream.flush()

    def close(self) -> None:
        """Flush and close the event stream once all callbacks are detached."""
        # Idempotent cleanup tolerates both normal completion and application shutdown.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stream.flush()
            self._stream.close()
