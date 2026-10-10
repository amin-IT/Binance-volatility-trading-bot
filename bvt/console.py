"""Console output: timestamped, optionally coloured log lines."""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime
from typing import TextIO


class Colors:
    """ANSI colour codes used across the bot and the signal modules."""

    BUY = "\033[92m"
    WARNING = "\033[93m"
    SELL_LOSS = "\033[91m"
    SELL_PROFIT = "\033[32m"
    DIM = "\033[2m\033[35m"
    YELLOW = "\033[33m"
    TURQUOISE = "\033[36m"
    DEFAULT = "\033[39m"
    RESET = "\033[0m"


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def profit_color(value: float) -> str:
    return Colors.SELL_PROFIT if value >= 0 else Colors.SELL_LOSS


def _use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class ConsoleFormatter(logging.Formatter):
    """Formats records as ``[YYYY-MM-DD HH:MM:SS] message``.

    Records from loggers outside the ``bvt`` package (i.e. signal modules) are
    prefixed with the logger name so it is clear which process printed them.
    """

    def __init__(self, use_color: bool) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        message = record.getMessage()
        if record.name != "root" and record.name.split(".")[0] not in ("bvt", "__main__"):
            message = f"{record.name}: {message}"
        if record.levelno >= logging.ERROR:
            message = f"{Colors.SELL_LOSS}{message}"
        elif record.levelno >= logging.WARNING:
            message = f"{Colors.WARNING}{message}"
        line = f"{Colors.DIM}[{timestamp}]{Colors.RESET} {message}{Colors.RESET}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line if self.use_color else strip_ansi(line)


def setup_logging(debug: bool = False, stream: TextIO | None = None) -> None:
    """Configure the root logger. Safe to call more than once (e.g. in child processes)."""
    stream = stream or sys.stdout
    try:  # enables ANSI colours on legacy Windows consoles
        import colorama

        colorama.just_fix_windows_console()
    except (ImportError, AttributeError):
        pass

    handler = logging.StreamHandler(stream)
    handler.setFormatter(ConsoleFormatter(use_color=_use_color(stream)))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    set_debug(debug)
    for noisy in ("urllib3", "asyncio", "websockets", "binance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_debug(debug: bool) -> None:
    logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)
