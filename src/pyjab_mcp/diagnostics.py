"""What is actually true about this machine, in a form that cannot flatter anyone.

``java_diagnostics`` exists because the first thing a user hits is an environment problem
(JAB not enabled, no bridge DLL, 32-bit Python against a 64-bit JVM), and "unavailable"
without the reason is the kind of answer that costs an afternoon.

The rule this module is written to (this project's AGENTS.md §2.4) is that **a measurement
it could not take is not a zero**.  Every row therefore has a status, and the ones that
matter are distinct:

* ``MISSING`` -- measured, and not there;
* ``DIFFERENT`` -- measured, there, and not what it needs to be;
* ``UNAVAILABLE`` -- **not measured**, with the reason in the value.

Nothing here writes anything.  In particular it never constructs pyjab's ``Service``,
whose ``__init__`` writes ``~/.accessibility.properties``: a diagnostics tool that changes
the thing it is reporting on is worse than no tool.
"""

from __future__ import annotations

import platform as platform_module
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .backend import Fact, PyjabApiReport, UNKNOWN, probe_public_api
from .bridge import BridgeThread


class Status:
    """Row verdicts.  ``UNAVAILABLE`` is deliberately not a synonym for ``MISSING``."""

    OK = "ok"
    MISSING = "missing"
    DIFFERENT = "different"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Row:
    label: str
    value: str
    status: str = Status.OK
    detail: str = ""


@dataclass(frozen=True)
class Report:
    rows: "tuple[Row, ...]"

    def render(self) -> str:
        width = max((len(row.label) for row in self.rows), default=0)
        lines = ["pyjab-mcp environment diagnostics", ""]
        for row in self.rows:
            lines.append(f"{row.label.ljust(width)}  {row.value}")
            if row.detail:
                lines.append(f"{' ' * width}    {row.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------------
# Rows that are pure functions of what they are given, so they are testable anywhere.
# ---------------------------------------------------------------------------------


def one_line(text: str) -> str:
    """*text* as a single line, because pyjab's Windows-only ImportError is a paragraph.

    The first line is the sentence; the rest is advice that belongs in a document rather
    than in a table cell. Used by the row that reports it and by the readiness check, so the
    two cannot disagree about what a fact says.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0] + (" ..." if len(lines) > 1 else "")


def check_accessibility_properties(path: Any, expected: str) -> Row:
    """Whether *path* already enables Java Access Bridge.

    Compared in full against what pyjab would write, rather than by looking for the class
    name inside the file: a file that merely mentions ``AccessBridge`` may also configure
    other assistive technologies, and rewriting it would silently take those away.  That is
    pyjab's own reasoning, and this reads the same file it would write.
    """
    label = "Java Access Bridge enabled"
    try:
        current = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Row(
            label,
            "not enabled",
            Status.MISSING,
            f"{path} does not exist. pyjab writes that file the first time it needs the "
            "bridge; a JVM started before it exists will not load Java Access Bridge.",
        )
    except (OSError, UnicodeDecodeError) as exc:
        # Distinct from "not enabled": the file may say exactly the right thing and be
        # unreadable.  Reporting MISSING here would invent a verdict.
        return Row(label, f"UNAVAILABLE ({type(exc).__name__}: {exc})", Status.UNAVAILABLE,
                   f"could not read {path}")
    if current == expected:
        return Row(label, "enabled", Status.OK, str(path))
    return Row(
        label,
        "present but does not enable JAB",
        Status.DIFFERENT,
        f"{path} exists and its content is not what pyjab writes; it may configure other "
        "assistive technologies, so this project does not rewrite it.",
    )


def check_bridge_dll(
    *,
    dll_bit: int,
    find: Callable[[int], Any],
    describe: Optional[Callable[[int], str]] = None,
) -> Row:
    """Where the bridge DLL is, from pyjab's own discovery.

    ``find`` and ``describe`` are injected so the row can be tested without pyjab and
    without Windows.  The real ones are ``pyjab.config.find_bridge_dll`` and
    ``pyjab.config.describe_bridge_dll_search``, both public and side-effect free.
    """
    label = f"bridge DLL ({dll_bit}-bit)"
    detail = ""
    if describe is not None:
        try:
            detail = describe(dll_bit)
        except Exception as exc:  # noqa: BLE001 - a description failing is not a verdict
            detail = f"(could not describe the search: {type(exc).__name__}: {exc})"
    try:
        found = find(dll_bit)
    except Exception as exc:  # noqa: BLE001
        return Row(label, f"UNAVAILABLE ({type(exc).__name__}: {exc})", Status.UNAVAILABLE,
                   detail)
    if found is None:
        return Row(label, "not found", Status.MISSING, detail)
    return Row(label, str(found), Status.OK, detail)


def check_bridge_thread(bridge: Optional[BridgeThread]) -> Row:
    """The executor's state, which is where a silent pump failure would show up."""
    label = "bridge thread"
    if bridge is None:
        return Row(label, "not started", Status.OK,
                   "the first tool that needs the bridge starts it")
    if bridge.startup_error is not None:
        return Row(label, f"UNAVAILABLE ({bridge.startup_error})", Status.UNAVAILABLE,
                   "the bridge thread is running but has no backend; every JAB-backed "
                   "tool call fails fast with this reason")
    stats = bridge.stats
    value = (
        f"running on thread {bridge.thread_ident}; "
        f"{stats.jobs_run} job(s), {stats.pump_calls} pump(s)"
    )
    if stats.pump_errors:
        return Row(
            label,
            f"pump failing ({stats.pump_errors} error(s))",
            Status.UNAVAILABLE,
            f"last error: {stats.last_pump_error}",
        )
    return Row(label, value, Status.OK)


# ---------------------------------------------------------------------------------
# Assembling the report
# ---------------------------------------------------------------------------------

_FACT_STATUS = {
    "available": Status.OK,
    "missing": Status.MISSING,
    "unknown": Status.UNAVAILABLE,
}


def collect(
    *,
    platform: Optional[str] = None,
    python: Optional[str] = None,
    bitness: Optional[int] = None,
    api: Optional[PyjabApiReport] = None,
    props_path: Any = None,
    props_expected: Optional[str] = None,
    dll_bit: Optional[int] = None,
    find_dll: Optional[Callable[[int], Any]] = None,
    describe_search: Optional[Callable[[int], str]] = None,
    bridge: Optional[BridgeThread] = None,
) -> Report:
    """Build the report.  Every input can be injected, which is what makes it testable.

    Omitted arguments are discovered the real way: ``sys.platform``, the interpreter's own
    bitness, the installed pyjab, and pyjab's ``config`` module for the DLL search and the
    properties-file location.
    """
    platform = sys.platform if platform is None else platform
    python = platform_module.python_version() if python is None else python
    bitness = 8 * struct.calcsize("P") if bitness is None else bitness
    api = probe_public_api() if api is None else api

    rows = [
        Row("platform", platform if platform == "win32" else
            f"UNAVAILABLE (not Windows: sys.platform={platform!r})",
            Status.OK if platform == "win32" else Status.UNAVAILABLE,
            "pyjab drives Java Access Bridge, which exists only on Windows and cannot "
            "cross machines" if platform != "win32" else "and Java Access Bridge is "
            "reachable from here"),
        Row("python", f"{python} ({bitness}-bit)", Status.OK,
            "a 32-bit interpreter needs the 32-bit bridge DLL"),
    ]
    rows.append(_pyjab_row(api))
    rows.extend(_fact_rows(api))

    if platform == "win32":
        rows.extend(_jab_rows(
            props_path=props_path, props_expected=props_expected, dll_bit=dll_bit,
            find_dll=find_dll, describe_search=describe_search,
        ))
    else:
        reason = "UNAVAILABLE (must run on the machine with the target application)"
        rows.append(Row("Java Access Bridge enabled", reason, Status.UNAVAILABLE,
                        "not measured: this is not Windows"))
        rows.append(Row(f"bridge DLL ({bitness}-bit)", reason, Status.UNAVAILABLE,
                        "not measured: this is not Windows"))

    rows.append(check_bridge_thread(bridge))
    return Report(tuple(rows))


def _pyjab_row(api: PyjabApiReport) -> Row:
    if api.error is not None:
        return Row("pyjab", f"UNAVAILABLE ({api.error})", Status.UNAVAILABLE,
                   "pip install pyjab; it is required, and this package imports it lazily")
    return Row("pyjab", api.version or "installed, version unknown", Status.OK,
               "the substrate: it reads the JVM's own accessibility tree")


def _fact_rows(api: PyjabApiReport) -> "list[Row]":
    rows = []
    for fact in api.facts:
        rows.append(_fact_row(fact))
    return rows


def _fact_row(fact: Fact) -> Row:
    status = _FACT_STATUS.get(fact.status, Status.UNAVAILABLE)
    if fact.status == "available":
        value = "available"
    elif fact.status == "missing":
        value = "MISSING"
    else:
        value = f"UNAVAILABLE ({fact.detail})" if fact.detail else "UNAVAILABLE"
    detail = fact.detail if fact.status != UNKNOWN else ""
    return Row(f"  pyjab API: {fact.name}", one_line(value), status, detail)


def _jab_rows(
    *,
    props_path: Any,
    props_expected: Optional[str],
    dll_bit: Optional[int],
    find_dll: Optional[Callable[[int], Any]],
    describe_search: Optional[Callable[[int], str]],
) -> "list[Row]":
    """The two Windows-only rows, discovered from pyjab's public ``config`` when not given."""
    config = None
    config_error: Optional[str] = None
    if props_path is None or props_expected is None or find_dll is None or dll_bit is None:
        try:
            import pyjab.config as config_module
        except Exception as exc:  # noqa: BLE001
            config_error = f"{type(exc).__name__}: {exc}"
        else:
            config = config_module

    rows = []
    if props_path is None and config is not None:
        props_path = getattr(config, "A11Y_PROPS_PATH", None)
    if props_expected is None and config is not None:
        props_expected = getattr(config, "A11Y_PROPS_CONTENT", None)
    if props_path is None or props_expected is None:
        rows.append(Row("Java Access Bridge enabled",
                        f"UNAVAILABLE ({config_error or 'pyjab.config gave no properties path'})",
                        Status.UNAVAILABLE, "not measured"))
    else:
        rows.append(check_accessibility_properties(props_path, props_expected))

    if dll_bit is None:
        dll_bit = 8 * struct.calcsize("P")
    if find_dll is None and config is not None:
        find_dll = getattr(config, "find_bridge_dll", None)
    if describe_search is None and config is not None:
        describe_search = getattr(config, "describe_bridge_dll_search", None)
    if find_dll is None:
        rows.append(Row(f"bridge DLL ({dll_bit}-bit)",
                        f"UNAVAILABLE ({config_error or 'pyjab.config.find_bridge_dll is missing'})",
                        Status.UNAVAILABLE, "not measured"))
    else:
        rows.append(check_bridge_dll(dll_bit=dll_bit, find=find_dll,
                                     describe=describe_search))
    return rows
