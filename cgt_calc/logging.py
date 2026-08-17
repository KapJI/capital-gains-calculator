"""Setup coloured logging."""

from __future__ import annotations

from functools import lru_cache
import logging
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING, ClassVar, TextIO

import colorama

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable


LOGGER = logging.getLogger(__name__)


class ConsoleUI:
    """Colour/emoji aware console printer.

    Detects per call whether the given stream supports colour and emoji, so
    streams replaced after start-up (e.g. colorama's Windows wrappers or
    redirected stdio) are always judged by their current capabilities.
    """

    def supports_colour(self, stream: TextIO) -> bool:
        """Return True if colour output should be used for the given stream."""
        return self._should_use_colour(stream)

    def supports_emoji(self, stream: TextIO) -> bool:
        """Return True if emoji output should be used for the given stream."""
        return self._should_use_emoji(stream)

    def painted(
        self,
        stream: TextIO,
        msg: str,
        colour: str | None = None,
        emoji: str | None = None,
    ) -> str:
        """Return msg wrapped in colour with emoji prefix if enabled."""
        if colour is not None and self.supports_colour(stream):
            msg = f"{colour}{msg}{colorama.Style.RESET_ALL}"
        if emoji is not None and self.supports_emoji(stream):
            msg = f"{emoji}  {msg}"
        return msg

    def _should_use_colour(self, stream: TextIO) -> bool:
        """Return True if colour output should be enabled for the given stream."""

        # Respect NO_COLOR (https://no-color.org/): any non-empty value disables.
        if os.environ.get("NO_COLOR"):
            return False
        # Allow forcing colour in CI or non-TTYs: any non-empty value enables.
        if os.environ.get("FORCE_COLOR"):
            return True
        # Only colourise when writing to a terminal
        try:
            return hasattr(stream, "isatty") and stream.isatty()
        except (AttributeError, OSError):
            return False

    def _should_use_emoji(self, stream: TextIO) -> bool:
        """Return True if emoji output should be enabled for the given stream."""

        # Should be available by now
        if not self.supports_colour(stream):
            return False

        if os.environ.get("NO_EMOJI"):
            return False

        # Encoding must support Unicode
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if "utf" not in encoding:
            return False

        if os.name == "nt":
            # On Windows 10+ with UTF-8 codepage or modern terminals
            return bool(os.environ.get("WT_SESSION") or "utf-8" in encoding)

        return True


class ColourMessageFormatter(logging.Formatter):
    """Formatter that colourises log messages."""

    COLOURS: ClassVar[dict[int, str]] = {
        logging.DEBUG: colorama.Fore.CYAN,
        logging.WARNING: colorama.Fore.YELLOW,
        logging.ERROR: colorama.Fore.RED,
        logging.CRITICAL: colorama.Style.BRIGHT
        + colorama.Fore.WHITE
        + colorama.Back.RED,
    }

    EMOJI: ClassVar[dict[int, str]] = {
        logging.WARNING: "⚠️",
        logging.ERROR: "❌",
    }

    def __init__(self, fmt: str, use_colour: bool, use_emoji: bool) -> None:
        """Initialise the formatter."""
        super().__init__(fmt)
        self.use_colour = use_colour
        self.use_emoji = use_emoji
        # Assumes this formatter is attached to a single handler.
        self._last_was_alert = False

    def format(self, record: logging.LogRecord) -> str:
        """Return a formatted log message, colourised if enabled."""
        message = super().format(record)

        if record.levelno != logging.INFO:
            message = f"{record.levelname.upper()}: {message}"

        colour = self.COLOURS.get(record.levelno)
        if self.use_colour and colour:
            message = f"{colour}{message}{colorama.Style.RESET_ALL}"

        emoji = self.EMOJI.get(record.levelno)
        if self.use_emoji and emoji:
            lines = message.splitlines()
            if lines:
                lines[0] = f"{emoji}  {lines[0]}"
                lines[1:] = [f"    {line}" for line in lines[1:]]
                message = "\n".join(lines)

        is_alert = record.levelno in (logging.WARNING, logging.ERROR)
        if is_alert:
            # Blank lines both sides so alerts stand apart from the progress flow,
            # collapsed to a single blank line within a run of consecutive alerts.
            lead = "" if self._last_was_alert else "\n"
            message = f"{lead}{message}\n"
        self._last_was_alert = is_alert
        return message


@lru_cache(maxsize=1)
def get_ui() -> ConsoleUI:
    """Return the global ConsoleUI instance, creating it lazily once."""
    return ConsoleUI()


def style_text(
    msg: str,
    colour: str | None = None,
    emoji: str | None = None,
    stream: TextIO | None = None,
) -> str:
    """Return the message formatted for terminal display.

    Wraps the message in colour codes and optionally prefixes it with an emoji
    if supported by the target stream. Falls back to plain text when colour or
    emoji output is not enabled for that stream.
    """
    if stream is None:
        stream = sys.stdout
    return get_ui().painted(stream, msg, colour, emoji)


def parsing_msg(file: str | Path | Traversable) -> None:
    """Print styled message for files being parsed."""
    # Strip package root
    package_root = f"{Path(__file__).resolve().parent.parent}{os.path.sep}"
    file = str(file).removeprefix(package_root)
    LOGGER.info(
        style_text(f"Parsing {file}...", colour=colorama.Fore.CYAN, stream=sys.stderr)
    )


def bullet(stream: TextIO) -> str:
    """Return a bullet suitable for the current terminal."""
    return "    • " if get_ui().supports_emoji(stream) else "  "


def setup_logging() -> None:
    """Configure the root logger with coloured output."""

    # Enable ANSI pass-through on Windows (harmless on other platforms)
    colorama.just_fix_windows_console()

    stream = sys.stderr
    fmt = "%(message)s"
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        ColourMessageFormatter(
            fmt, get_ui().supports_colour(stream), get_ui().supports_emoji(stream)
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Replace in-place without reassigning to a new list.
    root.handlers[:] = [handler]
