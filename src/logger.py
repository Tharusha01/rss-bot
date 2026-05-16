"""
Logging configuration for the RSS News Bot.
Supports both console and rotating file logging with Unicode support.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """
    Configure root logger with a console handler and optional rotating file handler.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file:  Path to the log file. If None, file logging is disabled.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    # Ensure stdout handles Unicode (especially Sinhala characters)
    if hasattr(console_handler.stream, "reconfigure"):
        console_handler.stream.reconfigure(encoding="utf-8", errors="replace")
    root_logger.addHandler(console_handler)

    # ── File handler (rotating, max 10 MB, keep 5 backups) ────────────────────
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Quiet down noisy third-party libraries
    for noisy_lib in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    logging.info("Logging initialised — level=%s  file=%s", log_level, log_file or "disabled")
