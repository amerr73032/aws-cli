"""Shared logging configuration helpers."""

import logging
from typing import Union

LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_LEVEL = logging.INFO


def configure_logger(name: str, level: Union[str, int] = DEFAULT_LOG_LEVEL) -> logging.Logger:
    """
    Create or retrieve a configured logger with our shared format.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(DEFAULT_LOG_LEVEL)
        formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    set_logger_level(logger, level)
    return logger


def set_logger_level(logger: logging.Logger, level: Union[str, int]):
    """Update a logger (and its handlers) to the requested level."""
    if isinstance(level, str):
        level_value = getattr(logging, level.upper(), DEFAULT_LOG_LEVEL)
    else:
        level_value = level
    logger.setLevel(level_value)
    for handler in logger.handlers:
        handler.setLevel(level_value)


def normalize_log_level(value: str) -> str:
    """Uppercase CLI input so argparse choice matching is case-insensitive."""
    return value.upper()
