"""Dedicated chronological audit logging for mosaic hardware activity."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class MosaicAuditLogger:
    """Write one line-buffered, timestamped event stream for a mosaic run."""

    def __init__(
        self,
        directory: str | Path = "logs/mosaics",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now().astimezone())
        log_directory = Path(directory)
        log_directory.mkdir(parents=True, exist_ok=True)
        self.path = log_directory / f"{self._now().strftime('%Y_%m_%d_%H-%M-%S')}.log"
        self._stream = self.path.open("x", encoding="utf-8", buffering=1)
        self._lock = threading.RLock()
        self._closed = False

    def log(self, event: str, details: dict[str, object] | None = None) -> None:
        """Synchronously append an event with local timestamp and JSON details."""
        timestamp = self._now().isoformat(timespec="milliseconds")
        payload = json.dumps(details or {}, sort_keys=True, separators=(",", ":"))
        with self._lock:
            if self._closed:
                return
            self._stream.write(f"{timestamp} | {event} | {payload}\n")
            self._stream.flush()

    def close(self) -> None:
        """Flush and close the event stream once all callbacks are detached."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stream.flush()
            self._stream.close()
