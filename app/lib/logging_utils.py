from __future__ import annotations

import logging
from pathlib import Path


def setup_debug_logging(base_dir: Path, *, level: int = logging.DEBUG) -> logging.Logger:
    """
    Configure the shared debug logger that feeds backend/app/logs/debug.log.
    Safe to call multiple times; handlers are added once.
    """
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "debug.log"

    logger = logging.getLogger("birdsong.debug")
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
