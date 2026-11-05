"""The lineup banner, for the user's own terminal only (init, --version, no command).

Never printed by hooks, diff, risky or rollback, so nothing the agent reads ever contains it.
"""

import os
import sys
from importlib import resources

BLUE = "\x1b[38;2;111;140;216m"  # #6F8CD8, readable on dark terminals
BLUE_256 = "\x1b[38;5;68m"  # nearest 256-colour shade, for terminals without 24-bit colour
RESET = "\x1b[0m"


def text():
    return resources.files("lineup").joinpath("lineup-banner.txt").read_text(encoding="utf-8")


def _windows_colour_on(stream):
    """Turn on colour codes in a Windows console. False if it can't be done."""
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x0004 or kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError, ImportError):
        return False


def colour(stream):
    """The colour code to use, or "" for plain text."""
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return ""
    if not is_terminal(stream):
        return ""
    if sys.platform == "win32":
        return BLUE if _windows_colour_on(stream) else ""
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return BLUE
    return BLUE_256


def is_terminal(stream):
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def show(stream=None):
    """Print the banner, only to a real terminal (piped or redirected output gets none)."""
    stream = stream or sys.stdout
    if not is_terminal(stream):
        return
    banner = text()
    code = colour(stream)
    if code:
        banner = code + banner.rstrip("\n") + RESET + "\n"
    try:
        stream.write(banner)
    except UnicodeEncodeError:  # e.g. piped output on Windows: send the exact UTF-8 bytes
        stream.flush()
        stream.buffer.write(banner.encode("utf-8"))
        stream.buffer.flush()
    stream.write("\n")
    stream.flush()
