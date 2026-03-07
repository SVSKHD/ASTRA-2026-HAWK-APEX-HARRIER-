# core/logger.py
from __future__ import annotations

"""
Centralized Logging Module — Single source of truth for all logging.

Usage:
    from core.logger import get_logger

    logger = get_logger("executor")
    logger.info("Message")
    logger.error("Error", exc_info=True)

Features:
    - Console output (colored)
    - File output (rotating)
    - Trade-specific log file
    - Configurable via environment
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 10 * 1024 * 1024))  # 10MB
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))
LOG_TO_FILE = os.environ.get("LOG_TO_FILE", "true").lower() in ("true", "1", "yes")
LOG_TO_CONSOLE = os.environ.get("LOG_TO_CONSOLE", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# ANSI Colors for console
# ---------------------------------------------------------------------------

class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"


LEVEL_COLORS = {
    "DEBUG": Colors.GRAY,
    "INFO": Colors.GREEN,
    "WARNING": Colors.YELLOW,
    "ERROR": Colors.RED,
    "CRITICAL": Colors.BOLD + Colors.RED,
}


# ---------------------------------------------------------------------------
# Custom Formatters
# ---------------------------------------------------------------------------

class ColoredFormatter(logging.Formatter):
    """Formatter with ANSI colors for console output."""

    def __init__(self, fmt: str = None, datefmt: str = None):
        super().__init__(fmt, datefmt)
        self.base_fmt = fmt or "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s"
        self.datefmt = datefmt or "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        # Add color based on level
        color = LEVEL_COLORS.get(record.levelname, "")

        # Format the message
        record.levelname = f"{color}{record.levelname}{Colors.RESET}"
        record.name = f"{Colors.CYAN}{record.name}{Colors.RESET}"

        # Add symbols for key events
        msg = record.getMessage()
        if "✅" in msg or "opened" in msg.lower():
            pass  # Already has emoji
        elif "❌" in msg or "failed" in msg.lower() or "error" in msg.lower():
            pass
        elif record.levelno >= logging.ERROR:
            record.msg = f"❌ {record.msg}"

        return super().format(record)


class FileFormatter(logging.Formatter):
    """Clean formatter for file output (no colors)."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


class TradeFormatter(logging.Formatter):
    """Structured formatter for trade log."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


# ---------------------------------------------------------------------------
# Logger Setup
# ---------------------------------------------------------------------------

_loggers = {}
_initialized = False


def _ensure_log_dir():
    """Create log directory if it doesn't exist."""
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)


def _get_file_handler(name: str) -> RotatingFileHandler:
    """Create rotating file handler."""
    _ensure_log_dir()

    log_file = os.path.join(LOG_DIR, f"{name}.log")
    handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(FileFormatter())
    handler.setLevel(logging.DEBUG)  # File gets everything
    return handler


def _get_console_handler() -> logging.StreamHandler:
    """Create colored console handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColoredFormatter())
    handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    return handler


def get_logger(name: str) -> logging.Logger:
    """
    Get or create a logger with the given name.

    Args:
        name: Logger name (e.g., "executor", "trade", "strategy")

    Returns:
        Configured logger instance
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Capture all, handlers filter
    logger.propagate = False  # Don't propagate to root

    # Clear existing handlers
    logger.handlers.clear()

    # Add console handler
    if LOG_TO_CONSOLE:
        logger.addHandler(_get_console_handler())

    # Add file handler
    if LOG_TO_FILE:
        logger.addHandler(_get_file_handler(name))

    _loggers[name] = logger
    return logger


# ---------------------------------------------------------------------------
# Specialized Loggers
# ---------------------------------------------------------------------------

def get_trade_logger() -> logging.Logger:
    """
    Get logger specifically for trade events.
    Writes to trades.log with structured format.
    """
    name = "trades"

    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    # Console
    if LOG_TO_CONSOLE:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(ColoredFormatter())
        console.setLevel(logging.INFO)
        logger.addHandler(console)

    # Trade-specific file
    if LOG_TO_FILE:
        _ensure_log_dir()
        trade_file = os.path.join(LOG_DIR, "trades.log")
        file_handler = RotatingFileHandler(
            trade_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(TradeFormatter())
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


def get_error_logger() -> logging.Logger:
    """
    Get logger specifically for errors.
    Writes to errors.log for easy debugging.
    """
    name = "errors"

    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.handlers.clear()

    # Console
    if LOG_TO_CONSOLE:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(ColoredFormatter())
        console.setLevel(logging.ERROR)
        logger.addHandler(console)

    # Error-specific file
    if LOG_TO_FILE:
        _ensure_log_dir()
        error_file = os.path.join(LOG_DIR, "errors.log")
        file_handler = RotatingFileHandler(
            error_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(FileFormatter())
        file_handler.setLevel(logging.ERROR)
        logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


# ---------------------------------------------------------------------------
# Trade Logging Helpers
# ---------------------------------------------------------------------------

_trade_logger = None


def log_trade_open(
        symbol: str,
        side: str,
        price: float,
        lot: float,
        ticket: int,
        strategy: str,
        mode: str = "ACTIVE",
):
    """Log a trade open event."""
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = get_trade_logger()

    emoji = "📈" if side.lower() == "buy" else "📉"
    _trade_logger.info(
        f"{emoji} OPEN | {mode} | {symbol} | {side.upper()} | "
        f"{lot} lots @ {price:.2f} | ticket={ticket} | {strategy}"
    )


def log_trade_close(
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        profit: float,
        ticket: int,
        strategy: str,
        mode: str = "ACTIVE",
):
    """Log a trade close event."""
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = get_trade_logger()

    emoji = "🟢" if profit >= 0 else "🔴"
    _trade_logger.info(
        f"{emoji} CLOSE | {mode} | {symbol} | {side.upper()} | "
        f"{entry_price:.2f} → {exit_price:.2f} | P&L: ${profit:.2f} | "
        f"ticket={ticket} | {strategy}"
    )


def log_trade_error(
        symbol: str,
        action: str,
        error: str,
        strategy: str = "",
):
    """Log a trade error."""
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = get_trade_logger()

    _trade_logger.error(
        f"❌ ERROR | {symbol} | {action} | {error} | {strategy}"
    )


# ---------------------------------------------------------------------------
# Daily Log Rotation (call at midnight)
# ---------------------------------------------------------------------------

def rotate_daily_logs():
    """
    Create daily log archives.
    Call this at MT5 day rollover.
    """
    _ensure_log_dir()

    today = datetime.now().strftime("%Y-%m-%d")

    for name, logger in _loggers.items():
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                handler.doRollover()


# ---------------------------------------------------------------------------
# Convenience Exports
# ---------------------------------------------------------------------------

# Pre-create common loggers
executor_logger = None
trade_logger = None
strategy_logger = None
pricing_logger = None


def init_loggers():
    """Initialize all common loggers."""
    global executor_logger, trade_logger, strategy_logger, pricing_logger

    executor_logger = get_logger("executor")
    trade_logger = get_trade_logger()
    strategy_logger = get_logger("strategy")
    pricing_logger = get_logger("pricing")

    executor_logger.info("Loggers initialized")
    executor_logger.info(f"Log level: {LOG_LEVEL}")
    executor_logger.info(f"Log directory: {LOG_DIR}")


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    "get_logger",
    "get_trade_logger",
    "get_error_logger",
    "log_trade_open",
    "log_trade_close",
    "log_trade_error",
    "init_loggers",
    "rotate_daily_logs",
]