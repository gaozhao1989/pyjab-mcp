"""Everything this project asks of pyjab, and nothing else.

**This is the only module that imports pyjab, and it always does so inside a function.**
pyjab raises ``ImportError`` at module scope off Windows -- deliberately, so a user gets a
sentence rather than a traceback -- so a module-scope import here would make this package
unimportable everywhere else, including on the machines that only run the test suite.
``tests/test_packaging.py`` enforces that with ``ast``.

The rule that shapes this file is pyjab's own (§1.1 of this project's AGENTS.md): the only
interface is pyjab's **published** API.  When something needed is not public, the answer is
not to reach in -- it is to record the requirement (:data:`REQUIREMENTS`), add the API to
pyjab, release it, and bump the pin.  So the gap is a **reported state** here, never a
private import.

Two gaps are known and both are recorded below with the reason a workaround is not
acceptable.  They are re-derived from the installed pyjab at runtime
(:func:`probe_public_api`), not from a version number, so the day pyjab grows the public
API this file starts working before anyone edits it.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .windows import JavaWindow

# ---------------------------------------------------------------------------------
# What pyjab does not yet provide, and why the obvious workaround is not one.
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """A pyjab-side change this project waits on.

    Kept as data rather than as prose in an error message so that the message a user sees,
    the diagnostics report and ``AGENTS.md`` §6 cannot drift apart.
    """

    key: str
    blocks: str
    wanted: str
    why: str
    expected_in: str

    def message(self) -> str:
        return (
            f"pyjab does not provide {self.wanted}\n"
            f"  needed by:      {self.blocks}\n"
            f"  why not a workaround: {self.why}\n"
            f"  expected in:    {self.expected_in}\n"
            "  See AGENTS.md §6 in this repository for the audit this came from."
        )


REQUIREMENTS: "tuple[Requirement, ...]" = (
    Requirement(
        key="list_java_windows",
        blocks="java_list_windows and java_attach_window (M1)",
        wanted="a public way to list the Java windows JAB can see",
        why=(
            "JABDriver cannot be constructed without binding a window, and a raw bridge "
            "call is wrong until JABFixedFunc._fix_bridge_functions() has run -- which is "
            "private, and without it ctypes truncates a 64-bit HWND to 32 bits, so "
            "isJavaWindow would answer about a different window. pyjab.inspector."
            "bridge_without_window() is exactly the missing piece and is not public."
        ),
        expected_in="a pyjab minor (1.9.0); unreleased as of pyjab 1.8.0",
    ),
    Requirement(
        key="detach",
        blocks="releasing a session created by java_attach_window",
        wanted="a public, non-destructive way to detach from a window it did not launch",
        why=(
            "the only teardown JABDriver has is __exit__, which terminates the bound "
            "process by pid; calling it on a user's application would kill it. There is no "
            "close()/quit(), so an attached session's accessible-context reference cannot "
            "be released."
        ),
        expected_in="the same pyjab minor",
    ),
)


def requirement(key: str) -> Requirement:
    for item in REQUIREMENTS:
        if item.key == key:
            return item
    raise KeyError(key)


class PyjabUnavailable(RuntimeError):
    """pyjab cannot be used here at all: not installed, not Windows, or not importable."""


class PyjabApiGap(RuntimeError):
    """pyjab is present but its public API does not yet expose something we need."""

    def __init__(self, missing: Requirement) -> None:
        super().__init__(missing.message())
        self.requirement = missing


# ---------------------------------------------------------------------------------
# Probing what the installed pyjab actually offers.  Read-only, no side effects.
# ---------------------------------------------------------------------------------

AVAILABLE = "available"
MISSING = "missing"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fact:
    """One API question and its answer.

    ``UNKNOWN`` exists because "I could not check" is not "it is not there" -- the failure
    mode this project's AGENTS.md §2.4 is about.  On a non-Windows machine, for instance,
    ``pyjab.jabelement`` cannot be imported, so whether it has ``walk`` is genuinely
    unknown rather than absent.
    """

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class PyjabApiReport:
    """What the installed pyjab's published surface provides."""

    version: Optional[str]
    error: Optional[str]
    facts: "tuple[Fact, ...]"

    def fact(self, name: str) -> Fact:
        for item in self.facts:
            if item.name == name:
                return item
        return Fact(name, UNKNOWN, "not probed")

    def has(self, name: str) -> bool:
        return self.fact(name).status == AVAILABLE


def probe_public_api() -> PyjabApiReport:
    """Ask the installed pyjab what it offers, without importing anything Windows-only.

    ``import pyjab`` is safe everywhere on purpose (pyjab keeps ``config`` and
    ``common.service`` platform independent), so the version and the top-level names are
    readable on any machine.  ``pyjab.jabelement`` is not, and its status is reported as
    ``UNKNOWN`` there rather than as a missing feature.
    """
    try:
        import pyjab
    except Exception as exc:  # noqa: BLE001 - any import failure is the same answer here
        return PyjabApiReport(None, f"{type(exc).__name__}: {exc}", ())

    # The version comes from the installed distribution metadata rather than from
    # ``pyjab.__version__``.  The dunder is an implementation detail of somebody else's
    # package, and reading it was a touchpoint the API-surface guard refused to record --
    # which is the guard doing its job on its first run.
    version = _installed_version()
    facts = [
        Fact(
            "public java-window listing",
            AVAILABLE if hasattr(pyjab, "list_java_windows") else MISSING,
            "pyjab.list_java_windows()" if hasattr(pyjab, "list_java_windows")
            else requirement("list_java_windows").expected_in,
        ),
        _probe_walk(),
        _probe_detach(),
        Fact(
            "pyjab.inspector",
            AVAILABLE if importlib.util.find_spec("pyjab.inspector") else MISSING,
            "ships with the unreleased tree-walk work",
        ),
    ]
    return PyjabApiReport(version, None, tuple(facts))


def _installed_version() -> Optional[str]:
    """pyjab's version from distribution metadata, or None when it is not installed."""
    try:
        return importlib.metadata.version("pyjab")
    except importlib.metadata.PackageNotFoundError:
        return None


def _probe_walk() -> Fact:
    name = "tree walk (JABElement.walk)"
    try:
        from pyjab.jabelement import JABElement  # noqa: PLC0415 - Windows only
    except ImportError as exc:
        return Fact(name, UNKNOWN, f"cannot be imported here: {exc}")
    if hasattr(JABElement, "walk"):
        return Fact(name, AVAILABLE, "JABElement.walk -> JABTree (.truncated)")
    return Fact(name, MISSING, "expected in the pyjab minor that carries PR #156")


def _probe_detach() -> Fact:
    """Whether a bound window can be released without terminating its process.

    ``JABDriver.__exit__`` sends ``SIGTERM`` to the bound pid, so it is not a detach.  The
    names checked are a guess at the shape the public API would take -- the requirement is
    recorded in :data:`REQUIREMENTS` as ``detach`` -- and the fact says so when it is
    missing, rather than implying pyjab has nothing.
    """
    name = "non-destructive detach"
    try:
        from pyjab.jabdriver import JABDriver  # noqa: PLC0415 - Windows only
    except ImportError as exc:
        return Fact(name, UNKNOWN, f"cannot be imported here: {exc}")
    for candidate in ("close", "quit", "detach"):
        if hasattr(JABDriver, candidate):
            return Fact(name, AVAILABLE, f"JABDriver.{candidate}()")
    return Fact(
        name,
        MISSING,
        "only __exit__, which terminates the bound process by pid; "
        + requirement("detach").expected_in,
    )


# ---------------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------------


class Backend(Protocol):
    """What :class:`~pyjab_mcp.bridge.BridgeThread` needs from a bridge.

    Deliberately tiny.  Everything the tools do is expressed through it, so the fake in
    ``tests/_fakebackend.py`` and the real thing are interchangeable and the L1 suite
    covers the loop rather than a mock of it.
    """

    def pump(self) -> None:
        """Service pending Windows/COM messages.  Must be cheap when the queue is empty."""

    def java_windows(self) -> Sequence[JavaWindow]:
        """The Java windows JAB can see, or raise if that cannot be determined."""

    def close(self) -> None:
        """Release what this backend holds, without touching the applications it observes."""


class PyjabBackend:
    """The real backend, built from pyjab's published API only.

    What it can do on released pyjab 1.8.0 is small and worth stating plainly: it can pump
    the message queue, and that is all.  Listing windows needs a public armed bridge that
    does not exist yet (requirement ``list_java_windows``), so the method raises
    :class:`PyjabApiGap` with the requirement text instead of reaching for a private name.
    """

    def pump(self) -> None:
        # Public and documented: its docstring in 1.8.0 states the thread-affinity rule.
        # It is called on the owning thread -- that is the whole point of BridgeThread.
        from pyjab.common.win32utils import Win32Utils  # noqa: PLC0415

        Win32Utils().pump_messages()

    def java_windows(self) -> Sequence[JavaWindow]:
        listing = _public_window_listing()
        if listing is None:
            raise PyjabApiGap(requirement("list_java_windows"))
        records = listing()
        return tuple(_to_window(record) for record in records)

    def close(self) -> None:
        """Nothing to release on released pyjab 1.8.0, and that is a finding, not a no-op.

        A driver bound to a window takes an accessible-context reference, and the only way
        pyjab offers to give it back is ``JABDriver.__exit__`` -- which kills the bound
        process.  This project therefore holds no driver at all until pyjab grows a
        non-destructive detach (requirement ``detach``).
        """


def _public_window_listing() -> Optional[Callable[[], Any]]:
    """The public listing function, or None when the installed pyjab has none.

    Feature-detected rather than version-compared: a newer pyjab then works with no change
    here, and an older one gets the requirement text rather than an ``AttributeError``.
    """
    try:
        import pyjab
    except Exception as exc:  # noqa: BLE001
        raise PyjabUnavailable(f"pyjab is not importable: {exc}") from exc
    return getattr(pyjab, "list_java_windows", None)


def _to_window(record: Any) -> JavaWindow:
    """Normalise one record from pyjab's listing into :class:`JavaWindow`.

    The shape is the one requirement ``list_java_windows`` asks for -- mappings with
    ``hwnd``, ``title`` and an optional ``pid`` -- and a mismatch is reported rather than
    guessed at.  Accepting attributes as well costs nothing and covers a record type.
    """
    if isinstance(record, Mapping):
        get: Callable[[str], Any] = record.get
    else:
        get = lambda name: getattr(record, name, None)  # noqa: E731

    hwnd = get("hwnd")
    title = get("title")
    if hwnd is None or title is None:
        raise PyjabApiGap(requirement("list_java_windows"))
    pid = get("pid")
    return JavaWindow(hwnd=int(hwnd), title=str(title), pid=int(pid) if pid else None)


def open_backend() -> Backend:
    """Construct the backend on the bridge thread.

    Called by :class:`~pyjab_mcp.bridge.BridgeThread` on the owning thread -- which matters,
    because everything the backend will do later has to happen there too.

    Raises:
        PyjabUnavailable: With a sentence naming the reason.  Failing here is a supported
            outcome: the executor records it, reports it through ``java_diagnostics``, and
            fails later calls fast rather than hanging.
    """
    if sys.platform != "win32":
        raise PyjabUnavailable(
            f"this is not Windows (sys.platform={sys.platform!r}). pyjab drives Java "
            "Access Bridge, which exists only on Windows, and Java Access Bridge cannot "
            "cross machines."
        )
    try:
        import pyjab.common.win32utils  # noqa: F401,PLC0415
    except ImportError as exc:
        raise PyjabUnavailable(
            f"pyjab's Windows modules are not importable: {exc}"
        ) from exc
    return PyjabBackend()
