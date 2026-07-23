"""Application logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(
    directory: str | Path = "logs",
    filename: str = "acq_staging.log",
) -> Path:
    """Configure rotating application logs and return the log path."""
    log_directory = Path(directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    path = log_directory / filename
    # Bound disk usage while retaining recent troubleshooting history.
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path
