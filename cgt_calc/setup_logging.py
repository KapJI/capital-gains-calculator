"""Setup coloured logging."""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, TextIO

import colorama

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Final


class ColourMessageFormatter(logging.Formatter):
    """Formatter that colourises log messages."""

    COLOURS: Final[Mapping[int, str]] = {
        logging.DEBUG: colorama.Fore.CYAN,
        logging.INFO: "",
        logging.WARNING: colorama.Fore.YELLOW,
        logging.ERROR: colorama.Fore.RED,
        logging.CRITICAL: colorama.Style.BRIGHT
        + colorama.Fore.WHITE
        + colorama.Back.RED,
    }

    def __init__(self, fmt: str, use_colour: bool) -> None:
        """Initialise the formatter."""
        super().__init__(fmt)
        self.use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        """Return a formatted log message, colourised if enabled."""
        message = super().format(record)

        if record.levelno != logging.INFO:
            message = f"{record.levelname.upper()}: {message}"

        colour = self.COLOURS.get(record.levelno, "")
        if self.use_colour and colour:
            return f"{colour}{message}{colorama.Style.RESET_ALL}"
        return message


def _should_use_colour(stream: TextIO) -> bool:
    """Return True if colour output should be enabled for the given stream."""

    # Respect NO_COLOR (https://no-color.org/)
    if os.environ.get("NO_COLOR"):
        return False
    # Allow forcing colour in CI or non-TTYs
    if os.environ.get("FORCE_COLOR"):
        return True
    # Only colourise when writing to a terminal
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except (AttributeError, OSError):
        return False


def setup_logging() -> None:
    """Configure the root logger with coloured output."""

    stream = sys.stderr

    # Enable ANSI pass-through on Windows (harmless on other platforms)
    colorama.just_fix_windows_console()

    use_colour = _should_use_colour(stream)

    fmt = "%(message)s"
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ColourMessageFormatter(fmt, use_colour))

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Replace in-place without reassigning to a new list.
    root.handlers[:] = [handler]
