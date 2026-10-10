"""Plain records for Java windows, and turning a request into one of them.

Kept apart from :mod:`pyjab_mcp.backend` so that the matching rules are testable without
pyjab, and so that the record the rest of the package passes around has no pyjab lifetime
attached to it -- the same reasoning as pyjab's own tree walk yielding dicts rather than
elements.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class JavaWindow:
    """One window Java Access Bridge can see.

    Attributes:
        hwnd: The window handle, as a plain ``int``.  pyjab keeps ``vmid`` as a plain int
            for the same reason: a ``c_long`` that leaks out of ctypes is not a value this
            layer should hand around.
        title: The window title, as the OS reports it.
        pid: The owning process, when it could be read.  ``None`` is "not known", never 0.
    """

    hwnd: int
    title: str
    pid: Optional[int] = None


class WindowLookupError(LookupError):
    """A window request did not identify exactly one window."""


class WindowNotFound(WindowLookupError):
    """Nothing matched.  The message lists what did exist."""


class AmbiguousWindow(WindowLookupError):
    """More than one window matched.

    A separate class from :class:`WindowNotFound` because the fix is different -- narrow it
    or pass an ``hwnd`` -- and because acting on the first of several matches is how a tool
    clicks the wrong thing.
    """


def resolve_window(
    windows: Sequence[JavaWindow],
    *,
    hwnd: Optional[int] = None,
    title: str = "",
) -> JavaWindow:
    """Pick exactly one window out of *windows*.

    ``hwnd`` wins when given; otherwise ``title`` is matched the way pyjab matches it, with
    :func:`fnmatch.fnmatch` -- so ``"*Report*"`` is a valid title and matches
    case-insensitively on Windows, which is the behaviour a user of the library already
    has.

    Raises:
        WindowNotFound: Nothing matched, or neither ``hwnd`` nor ``title`` was given.
        AmbiguousWindow: More than one title matched.
    """
    if hwnd is not None:
        for window in windows:
            if window.hwnd == hwnd:
                return window
        raise WindowNotFound(
            f"no Java window has hwnd={hwnd}." + _available(windows)
        )

    if not title:
        raise WindowNotFound(
            "give either a window title (fnmatch patterns allowed) or an hwnd."
            + _available(windows)
        )

    matches = [w for w in windows if fnmatch.fnmatch(w.title, title)]
    if not matches:
        raise WindowNotFound(f"no Java window title matches {title!r}." + _available(windows))
    if len(matches) > 1:
        shown = ", ".join(f"hwnd={w.hwnd} {w.title!r}" for w in matches[:8])
        raise AmbiguousWindow(
            f"{len(matches)} Java windows match {title!r}: {shown}. "
            "Narrow the pattern, or pass hwnd to choose one."
        )
    return matches[0]


def _available(windows: Iterable[JavaWindow], limit: int = 12) -> str:
    """The actionable half of a lookup failure: what was actually there.

    An error that says only "not found" leaves the caller guessing whether the window
    exists, is not a Java window, or is spelled differently -- the reason pyjab grew
    ``pyjab-inspect``.
    """
    windows = list(windows)
    if not windows:
        return " No Java windows are visible to Java Access Bridge right now."
    shown = ", ".join(f"{w.title!r}" for w in windows[:limit])
    more = "" if len(windows) <= limit else f", and {len(windows) - limit} more"
    return f" Visible Java windows: {shown}{more}."


def render_windows(windows: Sequence[JavaWindow]) -> str:
    """One line per window, for a tool result.

    The empty case is spelled out rather than rendered as an empty string: "no Java windows
    are visible" is a measurement, and an empty reply reads like a failure.
    """
    if not windows:
        return (
            "No Java windows are visible to Java Access Bridge. "
            "Check that the application is running, and run java_diagnostics -- "
            "JAB must be enabled for the JVM being driven."
        )
    lines = [f"{len(windows)} Java window(s):"]
    lines += [
        f"  hwnd={w.hwnd}" + (f"  pid={w.pid}" if w.pid else "") + f"  title={w.title!r}"
        for w in windows
    ]
    lines.append("")
    lines.append("Pass a title (fnmatch patterns allowed) or an hwnd to java_attach_window.")
    return "\n".join(lines)
