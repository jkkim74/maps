"""Logging configuration for MAPS."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from maps.common.settings import MapsSettings, get_settings


def configure_logging(settings: MapsSettings | None = None) -> None:
    """Configure console and rotating file logging.

    The function is idempotent enough for app startup and tests: existing MAPS
    handlers are replaced, while unrelated handlers are left alone.
    """
    s = settings or get_settings()
    level = getattr(logging, s.maps_log_level.upper(), logging.INFO)
    log_dir = Path(s.maps_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / s.maps_log_file

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console._maps_handler = True  # type: ignore[attr-defined]

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=s.maps_log_max_bytes,
        backupCount=s.maps_log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._maps_handler = True  # type: ignore[attr-defined]

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_maps_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
