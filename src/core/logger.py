"""Structured logging utility for SMC-SNR ML Perpetual Setup Scanner.
"""

import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "scanner",
    level: int = logging.INFO,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """Configure and return a structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if already configured
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


logger = setup_logger()
