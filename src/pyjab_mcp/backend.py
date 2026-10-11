"""Everything this project asks of pyjab, and nothing else.

**This is the only module that imports pyjab, and it always does so inside a function.**
pyjab raises ``ImportError`` at module scope off Windows -- deliberately, so a user gets a
sentence rather than a traceback -- so a module-scope import here would make this package
unimportable everywhere else, including on the machines that only run the test suite.
``tests/test_packaging.py`` enforces that with ``ast``.

The rule that shapes this file is pyjab's own, and this project's: the only
interface is pyjab's **published** API.  When something needed is not public, the answer is
not to reach in -- it is to record the requirement (:data:`REQUIREMENTS`), add the API to
pyjab, release it, and bump the pin.  So the gap is a **reported state** here, never a
private import.

No gap is open any more: the last two -- R4 (``send_keys``) and R5 (bringing a window to the
foreground) -- were both answered by pyjab 1.11.0, which :data:`RETIRED_REQUIREMENTS` records
and the pin now requires.  What is left is re-derived from the installed pyjab at runtime
(:func:`probe_public_api`), not from a version number, so a newer pyjab starts working before
anyone edits it, and an install *older* than the pin is a reported state rather than an
``AttributeError``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .handles import locator_candidates, locator_for, verify
from .tree import Record, Walk, walked_nodes
from .windows import JavaWindow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------
# What this project asks of pyjab, as data: the open gaps (none) and the answered ones.
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """A pyjab-side change this project waits on.

    Kept as data rather than as prose in an error message so that the message a user sees,
    the diagnostics report and this tuple cannot drift apart.
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
            "  This is a gap in the dependency rather than in this server: the tool has\n"
            "  nothing to call. See the issue above for its state."
        )


#: The open gaps -- **empty since 0.2.1.**  R4 (``send_keys``) and R5 (bring-a-window-to-the-
#: foreground) were the last two, and pyjab 1.11.0 answered both; they moved to
#: :data:`RETIRED_REQUIREMENTS`, which is where a reader now finds the release that did it.
#: The tuple stays because the next gap goes *here* rather than into an error message, and
#: :func:`requirement` still raises for a key that is no longer one -- the honest answer when
#: a call site has not been updated with it.
REQUIREMENTS: "tuple[Requirement, ...]" = ()

#: Requirements that were answered, kept as the record of what was waited for and what
#: answered it. R1 (`list_java_windows`) and R2 (`JABDriver.detach`) were both released in
#: pyjab 1.10.0; R3 (the tree walk) in 1.9.0; R4 (`send_keys`) and R5 (bring-to-foreground)
#: in 1.11.0. They are not requirements any more -- nothing reads this list for a decision --
#: but deleting the history outright is how a project forgets why its pin is where it is,
#: and :func:`predates_the_pin` is what an install below the floor is told.
RETIRED_REQUIREMENTS: "Mapping[str, str]" = {
    "list_java_windows": "released in pyjab 1.10.0 as pyjab.list_java_windows()",
    "detach": "released in pyjab 1.10.0 as JABDriver.detach()",
    "walk": "released in pyjab 1.9.0 as JABElement.walk() / JABTree",
    "send_keys": "released in pyjab 1.11.0 as JABDriver.send_keys(keys)",
    "bring_to_foreground": (
        "released in pyjab 1.11.0 as Win32Utils.set_window_foreground(hwnd), with "
        "JABDriver.focus() / JABElement.focus() returning whether the window came forward"
    ),
}


def requirement(key: str) -> Requirement:
    for item in REQUIREMENTS:
        if item.key == key:
            return item
    raise KeyError(key)


def predates_the_pin(key: str) -> str:
    """What to say to an install older than the release that answered ``key``.

    A retired requirement is still reachable: nothing stops a user installing a pyjab below
    the floor, and the probes feature-detect rather than version-compare, so the tool meets
    that install and has to answer rather than raise ``AttributeError`` (§6.3).  Naming the
    release from :data:`RETIRED_REQUIREMENTS` is what keeps this message and the record from
    drifting apart.
    """
    return (
        f"{RETIRED_REQUIREMENTS[key]}, which pyproject.toml now requires; the installed pyjab "
        "predates it. Upgrade it: python -m pip install -U pyjab"
    )


class PyjabUnavailable(RuntimeError):
    """pyjab cannot be used here at all: not installed, not Windows, or not importable."""


class PyjabApiGap(RuntimeError):
    """pyjab is present but its public API does not expose something this project needs.

    ``missing`` is a :class:`Requirement` for an open gap, or the sentence
    :func:`predates_the_pin` writes for one that has been answered: the same state a user
    meets, from the same place.
    """

    def __init__(self, missing: "Requirement | str") -> None:
        if isinstance(missing, Requirement):
            super().__init__(missing.message())
            self.requirement: "Optional[Requirement]" = missing
        else:
            super().__init__(missing)
            self.requirement = None


class ElementChanged(RuntimeError):
    """The element at a handle's locator is not the element the handle was taken from.

    Raised *instead of* acting. D2's whole point is that "the tree moved and we clicked the
    wrong thing" becomes a message, so nothing that changes an application may proceed on an
    element that failed verification.
    """

    def __init__(self, message: str, *, handle: str = "", locator: str = "") -> None:
        super().__init__(
            f"{message}. Nothing was done. Take a fresh snapshot"
            + (f" (handle {handle}, locator {locator})" if handle else "")
            + "."
        )
        self.handle = handle
        self.locator = locator


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
    mode this project's rule about measurements is about -- a measurement that could not be\n    taken is not a zero.  On a non-Windows machine, for instance,
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
            else predates_the_pin("list_java_windows"),
        ),
        _probe_walk(),
        _probe_keys(),
        _probe_foreground(),
        _probe_detach(),
        Fact(
            "pyjab.inspector (CLI fallback)",
            AVAILABLE if importlib.util.find_spec("pyjab.inspector") else MISSING,
            "shipped in 1.9.0. Since 1.10.0 added list_java_windows() this is only reached "
            "by an install older than this project's pin; the documented "
            "`pyjab-inspect windows --json` stays as that fallback rather than as the path",
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
    if hasattr(JABElement, "walk") and hasattr(JABElement, "as_record"):
        return Fact(
            name, AVAILABLE,
            "JABElement.walk -> JABTree (.truncated), and as_record() for the fields a "
            "handle re-verifies against",
        )
    # 1.8.0 and earlier: walk was in the checkout, not in any release.
    return Fact(name, MISSING, "released in pyjab 1.9.0; this install is older")


def _probe_keys() -> Fact:
    """Whether a key sequence or shortcut can be sent through public API.

    The names checked are the shapes the public API could take, and pyjab 1.11.0 has
    ``Win32Utils.send_keys`` -- which is the release R4 was waiting on.  When none is found
    the install is below the pin, and the fact says which release has it rather than
    implying pyjab has nothing.
    """
    name = "public key/shortcut sending"
    try:
        from pyjab.common.win32utils import Win32Utils  # noqa: PLC0415 - Windows only
        from pyjab.jabelement import JABElement  # noqa: PLC0415 - Windows only
    except ImportError as exc:
        return Fact(name, UNKNOWN, f"cannot be imported here: {exc}")
    for owner, candidate in (
        (Win32Utils, "send_keys"), (Win32Utils, "press_key"),
        (JABElement, "send_keys"), (JABElement, "press_key"),
    ):
        if hasattr(owner, candidate):
            return Fact(name, AVAILABLE, f"{owner.__name__}.{candidate}()")
    return Fact(
        name,
        MISSING,
        "every key helper pyjab has is private (Win32Utils._press_key, _send_keys, ...), and "
        "send_text() types text rather than a modifier chord; "
        + predates_the_pin("send_keys"),
    )


def _probe_foreground() -> Fact:
    """Whether an attached window can be brought to the foreground through public API.

    The candidate names are the shapes it could take, and pyjab 1.11.0 has
    ``Win32Utils.set_window_foreground`` -- R5's answer, with ``JABDriver.focus()`` and
    ``JABElement.focus()`` alongside it and returning whether the window came forward.  The
    gap this project measured upstream as pyjab#180 is closed; when no candidate is found the
    install is below the pin, and the fact names the release that has it.
    """
    name = "public way to bring a window to the foreground"
    try:
        from pyjab.common.win32utils import Win32Utils  # noqa: PLC0415 - Windows only
        from pyjab.jabdriver import JABDriver  # noqa: PLC0415 - Windows only
        from pyjab.jabelement import JABElement  # noqa: PLC0415 - Windows only
    except ImportError as exc:
        return Fact(name, UNKNOWN, f"cannot be imported here: {exc}")
    candidates = (
        (Win32Utils, "set_window_foreground"),
        (Win32Utils, "bring_to_foreground"),
        (Win32Utils, "foreground_window"),
        (JABDriver, "bring_to_foreground"),
        (JABDriver, "set_foreground"),
        (JABDriver, "activate"),
        (JABElement, "bring_to_foreground"),
        (JABElement, "set_foreground"),
    )
    for owner, candidate in candidates:
        if hasattr(owner, candidate):
            return Fact(name, AVAILABLE, f"{owner.__name__}.{candidate}()")
    return Fact(
        name,
        MISSING,
        "every foreground helper pyjab has is private (Win32Utils._set_window_foreground), and "
        "the supported way to give a window focus is an interactive call -- which activates "
        "what it lands on; " + predates_the_pin("bring_to_foreground"),
    )


def _probe_detach() -> Fact:
    """Whether a bound window can be released without terminating its process.

    ``JABDriver.__exit__`` sends ``SIGTERM`` to the bound pid, so it is not a detach.  The
    names checked are the shapes the public API could take -- R2 was answered by
    ``JABDriver.detach`` in pyjab 1.10.0 -- and a fact that finds none says which release has
    it, rather than implying pyjab has nothing.
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
        + predates_the_pin("detach"),
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

    def attach(self, *, title: str = "", hwnd: Optional[int] = None,
               timeout: float = 30.0) -> "AttachedWindow":
        """Bind to one window and register it; returns the handle other calls use."""

    def walk(self, window_id: str, *, max_depth: Optional[int] = None,
             limit: Optional[int] = None) -> Walk:
        """A bounded walk of the attached window."""

    def resolve(self, window_id: str, locator: str) -> Record:
        """Re-resolve a locator against the attached window."""

    def find(self, window_id: str, *, by: str, value: Any, visible: bool = False,
             limit: int = 20) -> "List[Tuple[Record, Optional[str]]]":
        """Search the window, returning each match with the locator that finds it again."""

    def text(self, window_id: str, locator: str) -> Optional[str]:
        """The text of the element a locator resolves to, or None when it has none."""

    def table(self, window_id: str, locator: str, *, row_start: int = 0,
              row_count: int = 20, max_cells: int = 400) -> TableReading:
        """One page of a table, with its row and column counts."""

    def act(self, window_id: str, locator: str, expected: Record, action: str, *,
            value: Any = None, simulate: bool = False, row: Optional[int] = None,
            column: Optional[int] = None) -> None:
        """Verify the element and act on it, in one element lifetime."""

    def screenshot(self, window_id: str, locator: Optional[str] = None,
                   expected: Optional[Record] = None) -> bytes:
        """PNG bytes for the bound window, or for one verified element."""

    def send_keys(self, window_id: str, keys: str) -> None:
        """Send a keyboard chord to whichever window has the keyboard focus."""

    def close(self) -> None:
        """Release what this backend holds, without touching the applications it observes."""


@dataclass(frozen=True)
class AttachedWindow:
    """A window this backend has bound, and the id other calls use to reach it."""

    window_id: str
    hwnd: int
    title: str
    pid: Optional[int] = None


@dataclass(frozen=True)
class TableReading:
    """One page of a Java table, with the counts that make the page's size meaningful.

    ``rows`` holds ``None`` for a cell with no value this layer could read and ``""`` for a
    cell that is text and empty -- two different answers, and pyjab's own ``text`` property
    keeps them apart. A cell that could not be *read* is neither: it is listed by position in
    ``unreadable``, because "I could not read it" is a third state and not a value, and
    collapsing it into either of the others would invent an empty table.

    ``from_name`` lists the cells whose value came from the cell's accessible **name** rather
    than from AccessibleText. That fallback is not a guess: Swing's ``AccessibleJTableCell``
    reports the cell's value as its name, and a real table in pyjab's test application reports
    no text interface at all -- the first L3 run read ``None`` for "Kathy" and this is why. The
    reply says which cells came from where, so a value is never presented as something it is
    not.
    """

    row_count: int
    column_count: int
    row_start: int
    rows: "Tuple[Tuple[Optional[str], ...], ...]"
    #: ``(row, column)`` of cells whose read raised, in row-major order.
    unreadable: "Tuple[Tuple[int, int], ...]" = ()
    #: ``(row, column)`` of cells whose value came from the accessible name.
    from_name: "Tuple[Tuple[int, int], ...]" = ()
    truncated: bool = False
    reason: str = ""


class PyjabBackend:
    """The real backend, built from pyjab's published API only.

    What 1.9.0 makes possible, and what it does not:

    * **Binding a window** is public (``JABDriver(hwnd=...)`` / ``JABDriver(title=...)``) and
      is also what arms the bridge: ``JABDriver.__init__`` loads the DLL, calls
      ``Windows_run()`` on this thread and registers the JAB prototypes. So attaching is the
      point at which the bridge becomes usable -- and it happens on the bridge thread.
    * **Reading a bounded subtree** is public (``JABElement.walk`` → ``JABTree``), and the
      walk releases every reference it takes, so this layer holds none.
    * **Re-finding an element** is public (``find_element_by_xpath`` → a ``JABElement`` this
      layer owns) and ``as_record()`` is what the handle re-verifies against. The reference
      is released immediately -- see :func:`resolve_driver`.
    * **Listing windows** is still not public. 1.9.0 ships
      ``pyjab.inspector.bridge_without_window()``, which is exactly the missing piece, but
      it is not in that module's ``__all__``; so the fallback is the *documented* CLI,
      ``pyjab-inspect windows --json``. The requirement stays recorded and
      ``java_diagnostics`` says which path answered.

    The two functions this class delegates the pyjab-facing work to -- :func:`walk_driver`
    and :func:`resolve_driver` -- take the driver as an argument precisely so the ownership
    rule inside them can be tested against a reference-counting fake on any platform.
    """

    def __init__(self, *, cli_runner: Optional[Callable[[Sequence[str]], Any]] = None) -> None:
        self._drivers: "Dict[str, Any]" = {}
        self._counter = 0
        self._cli_runner = cli_runner

    # -- the pump ------------------------------------------------------------------

    def pump(self) -> None:
        # Public and documented: its docstring states the thread-affinity rule. Called on
        # the owning thread -- that is the whole point of BridgeThread.
        from pyjab.common.win32utils import Win32Utils  # noqa: PLC0415

        Win32Utils().pump_messages()

    # -- windows -------------------------------------------------------------------

    def java_windows(self) -> Sequence[JavaWindow]:
        listing = _public_window_listing()
        if listing is not None:
            try:
                return tuple(_to_window(record) for record in listing())
            except ImportError as exc:
                # pyjab is importable everywhere but only *runs* on Windows, and its
                # descriptive ImportError is what crossing that line looks like from here.
                # Passing it through would hand the tool layer an import failure to explain;
                # it is an ordinary unusable-environment answer instead.
                raise PyjabUnavailable(
                    f"the Java Access Bridge is Windows-only, so it cannot list windows "
                    f"here: {exc}"
                ) from exc
            except PyjabUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure here is the environment
                # On Windows without a usable JDK this is a FileNotFoundError for the bridge
                # DLL, with pyjab's own probing report inside it -- which is exactly an
                # environment answer, so it is typed as one and pyjab's text is kept: it says
                # which directories were searched, and that is worth more than a summary.
                raise PyjabUnavailable(
                    f"the Java Access Bridge could not list windows: {exc}"
                ) from exc
        # Reached only when the installed pyjab is older than this project's pin (or someone
        # has vendored one). Kept because the CLI is a public interface rather than a private
        # one, and because a floor can be worked around -- not because it is expected.
        return tuple(_cli_java_windows(self._run_cli))

    def attach(self, *, title: str = "", hwnd: Optional[int] = None,
               timeout: float = 30.0) -> AttachedWindow:
        """Bind a driver to one window, on this (the bridge) thread.

        ``JABDriver`` is constructed here and **never** used as a context manager: its
        ``__exit__`` terminates the bound process by pid, which is right for an application
        pyjab launched and wrong for one the user is running. pyjab's own ``pyjab-inspect``
        makes the same choice for the same reason.

        Raises:
            PyjabUnavailable: When the window cannot be bound, with pyjab's own message --
                which names the title it waited for, or the handle it rejected.
        """
        from pyjab.jabdriver import JABDriver  # noqa: PLC0415 - Windows only

        kwargs: "Dict[str, Any]" = {"timeout": int(timeout)}
        if hwnd is not None:
            kwargs["hwnd"] = hwnd
        if title:
            kwargs["title"] = title
        try:
            driver = JABDriver(**kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised with the reason intact
            raise PyjabUnavailable(
                f"could not bind to the window ({title or hwnd}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Attaching the same window twice would hold two contexts for it, and the second is
        # pure waste: release the one already bound to this handle first.
        for existing, bound in list(self._drivers.items()):
            if int(getattr(bound, "hwnd", 0) or 0) == int(driver.hwnd or 0):
                self.detach(existing)

        self._counter += 1
        window_id = f"w{self._counter}"
        self._drivers[window_id] = driver
        return AttachedWindow(
            window_id=window_id,
            hwnd=int(driver.hwnd or 0),
            title=str(driver.title or title),
            pid=driver.pid,
        )

    # -- reading -------------------------------------------------------------------

    def walk(self, window_id: str, *, max_depth: Optional[int] = None,
             limit: Optional[int] = None) -> Walk:
        """A bounded walk of the attached window.  See :func:`walk_driver`."""
        return walk_driver(self._driver(window_id), max_depth=max_depth, limit=limit)

    def resolve(self, window_id: str, locator: str) -> Record:
        """Re-resolve a locator against the attached window.  See :func:`resolve_driver`."""
        return resolve_driver(self._driver(window_id), locator)

    def find(self, window_id: str, *, by: str, value: Any, visible: bool = False,
             limit: int = 20) -> "List[Tuple[Record, Optional[str]]]":
        """Search the attached window.  See :func:`find_driver`."""
        return find_driver(
            self._driver(window_id), by=by, value=value, visible=visible, limit=limit
        )

    def text(self, window_id: str, locator: str) -> Optional[str]:
        """Read text through the attached window.  See :func:`text_driver`."""
        return text_driver(self._driver(window_id), locator)

    def table(self, window_id: str, locator: str, *, row_start: int = 0,
              row_count: int = 20, max_cells: int = 400) -> TableReading:
        """Read a page of a table.  See :func:`table_driver`."""
        return table_driver(
            self._driver(window_id), locator,
            row_start=row_start, row_count=row_count, max_cells=max_cells,
        )

    def act(self, window_id: str, locator: str, expected: Record, action: str, *,
            value: Any = None, simulate: bool = False, row: Optional[int] = None,
            column: Optional[int] = None) -> None:
        """Act on an element.  See :func:`act_driver`."""
        return act_driver(
            self._driver(window_id), locator, expected, action,
            value=value, simulate=simulate, row=row, column=column,
        )

    def screenshot(self, window_id: str, locator: Optional[str] = None,
                   expected: Optional[Record] = None) -> bytes:
        """A screenshot.  See :func:`screenshot_driver`."""
        return screenshot_driver(
            self._driver(window_id), locator=locator, expected=expected
        )

    def send_keys(self, window_id: str, keys: str) -> None:
        """Send a keyboard chord.  See :func:`send_keys_driver`."""
        return send_keys_driver(self._driver(window_id), keys)

    def close(self) -> None:
        """Detach every bound window, releasing its JAB reference and leaving it running.

        This used to be ``self._drivers.clear()`` with a comment saying nothing could be
        released: the only teardown pyjab had was ``__exit__``, which terminates the bound
        process. ``JABDriver.detach()`` arrived in 1.10.0 and does the part this project
        wanted -- release the root element's reference, forget the window, leave the
        application alone -- so a session now ends without killing anything.
        """
        for window_id in list(self._drivers):
            self.detach(window_id)

    def detach(self, window_id: str) -> bool:
        """Release one attached window.  Returns whether there was one to release.

        A failure here is logged rather than raised: this runs on the way out, and a bridge
        that cannot release a reference must not stop the other windows from being released.
        """
        driver = self._drivers.pop(window_id, None)
        if driver is None:
            return False
        try:
            driver.detach()
        except Exception as exc:  # noqa: BLE001
            logger.warning("detaching %s raised: %s", window_id, exc)
        return True

    # -- helpers -------------------------------------------------------------------

    def _driver(self, window_id: str) -> Any:
        driver = self._drivers.get(window_id)
        if driver is None:
            raise PyjabUnavailable(
                f"window {window_id!r} is not attached; call java_attach_window first"
            )
        return driver

    def _run_cli(self, command: Sequence[str]) -> Any:
        if self._cli_runner is not None:
            return self._cli_runner(command)
        return subprocess.run(command, capture_output=True, text=True, timeout=120)


def walk_driver(driver: Any, *, max_depth: Optional[int] = None,
                limit: Optional[int] = None) -> Walk:
    """Walk *driver*'s window, bounded, and **stop** rather than truncate afterwards.

    Takes the driver rather than reaching for one, so this -- the part with the ownership
    rule in it -- is testable against a reference-counting fake on any platform, which is
    the only way it is testable at all off Windows.

    The intermediate generator is closed explicitly when the cap is reached. That is not a
    detail: pyjab's walk releases each reference as it advances and its ``finally`` is what
    releases the child it was holding, so abandoning the generator without closing it turns
    a bounded read into a leak. ``tests/_fakedriver.py`` fails the test if that happens.
    """
    root_element = driver.root_element
    tree = root_element.walk(max_depth=max_depth, limit=limit)
    generator = iter(tree)

    records: "List[Tuple[int, Mapping[str, Any]]]" = []
    stopped = False
    while True:
        if limit is not None and len(records) >= limit:
            stopped = True
            break
        try:
            records.append(next(generator))
        except StopIteration:
            break
    if stopped:
        close = getattr(generator, "close", None)
        if close is not None:
            close()

    nodes = walked_nodes(records)
    truncated = stopped or bool(getattr(tree, "limit_hit", False))
    reason = ""
    if stopped:
        reason = f"raw walk cap reached ({limit} records)"
    elif truncated:
        reason = f"pyjab walk limit reached ({limit} records)"
    return Walk(
        root=Record.from_pyjab(root_element.as_record()),
        nodes=nodes,
        truncated=truncated,
        reason=reason,
        walked=len(records),
    )


@contextmanager
def element_for(driver: Any, locator: str) -> "Iterator[Any]":
    """Yield the element *locator* resolves to, and give its reference back afterwards.

    The reference is released in a ``finally`` because the caller does work between taking it
    and giving it back, and work that raises must not leak -- which is the shape of the
    ownership rule pyjab has broken twice, and issue #43's shape.

    ``element.release_jabelement()`` and not ``driver.release_jabelement(element)``: the
    release belongs to ``JABElement``, and the version of this file that said otherwise
    survived the L1 suite because the fake was shaped like the guess. A contract test on a
    real Windows runner is what found it.

    Raises:
        PyjabUnavailable: When the locator does not resolve. That is an ordinary outcome of a
            tree that moved, so it is reported with the locator in it.
    """
    try:
        element = driver.find_element_by_xpath(locator)
    except Exception as exc:  # noqa: BLE001
        raise PyjabUnavailable(
            f"the locator {locator!r} no longer resolves: {type(exc).__name__}: {exc}"
        ) from exc
    if element is None:
        raise PyjabUnavailable(f"the locator {locator!r} no longer resolves")
    try:
        yield element
    finally:
        element.release_jabelement()


def resolve_driver(driver: Any, locator: str) -> Record:
    """Re-resolve *locator* and return what is there now, holding nothing afterwards."""
    with element_for(driver, locator) as element:
        return Record.from_pyjab(element.as_record())


def text_driver(driver: Any, locator: str) -> Optional[str]:
    """The text of the element *locator* resolves to, or None when it has no text interface.

    pyjab's only public text API returns the whole string; there is no public range read. So
    the whole value is fetched and the tool slices what it returns, which is a real cost on a
    document-sized text area rather than a hot path -- stated here because the alternative
    (reaching for the private range helpers) is what this project does not do.
    """
    with element_for(driver, locator) as element:
        return element.text


def table_driver(driver: Any, locator: str, *, row_start: int = 0,
                 row_count: int = 20, max_cells: int = 400) -> TableReading:
    """Read one page of the table *locator* resolves to.

    Paged from the start because a Java table is exactly the thing that must never be
    returned whole: pyjab's own test application has a 200-row table, and this project's
    first constraint is that a read is bounded. ``max_cells`` is the hard stop even when the
    requested rows would exceed it, and the result says when it stopped.

    Cells are taken and given back one at a time inside the element's own lifetime; the
    element itself is released by :func:`element_for`. A cell whose read raises is counted
    rather than skipped, so a table with one broken cell cannot look like a table with a hole
    in it.

    Raises:
        PyjabUnavailable: When the locator does not resolve, or the element is not a table --
            the message says what it is instead, which is the difference between "your handle
            is stale" and "that handle is a panel".
    """
    with element_for(driver, locator) as element:
        info = element.table
        if not info:
            raise PyjabUnavailable(
                f"the element at {locator!r} is not a table, so it has no cells. "
                "java_snapshot or java_get_element will show what it is."
            )
        # Subscript rather than `.get`: the keys are part of the shape this project depends
        # on (pyjab documents `info = table.table`), and a missing key should say so instead
        # of quietly becoming a table with no rows.
        try:
            total_rows = int(info["row_count"])
            total_columns = int(info["column_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PyjabUnavailable(
                f"the table did not report its shape as row_count/column_count: {exc}"
            ) from exc

        # `total_rows` and the caller's `row_count` are different numbers, and an earlier
        # version of this function shadowed one with the other: the page size became the
        # table's height, so asking for one row returned the whole table. The names are the
        # fix, and the paging tests are what caught it.
        start = max(0, min(row_start, total_rows))
        # The budget is in cells, so it decides a row count: floor(max_cells / columns). An
        # earlier version also carried a per-cell break that could never fire, because the row
        # count had already been reduced to fit -- dead code that read as a safety net.
        rows_that_fit = max(1, max_cells // max(1, total_columns))
        limited_by_cells = rows_that_fit < row_count
        wanted = max(1, min(row_count, rows_that_fit))
        last = min(total_rows, start + wanted)

        rows: "List[Tuple[Optional[str], ...]]" = []
        unreadable: "List[Tuple[int, int]]" = []
        from_name: "List[Tuple[int, int]]" = []
        cells = 0
        stopped_early = False
        for row in range(start, last):
            if cells >= max_cells:
                # Reachable only if the table reports more columns than it did a moment ago;
                # a table whose shape changes mid-read must not be able to blow the budget.
                stopped_early = True
                break
            values: "List[Optional[str]]" = []
            for column in range(total_columns):
                try:
                    cell = element.get_cell(row=row, column=column)
                except Exception:  # noqa: BLE001 - one cell failing is not the table failing
                    unreadable.append((row, column))
                    values.append(None)
                    continue
                try:
                    value = cell.text
                    if value is None:
                        # No Accessible Text: Swing reports a cell's value as its accessible
                        # name, and a table that does this reports None for every cell rather
                        # than an empty string.
                        raw = cell.as_record()
                        name = raw["name"] if "name" in raw else ""
                        if name:
                            value = name
                            from_name.append((row, column))
                    values.append(value)
                except Exception:  # noqa: BLE001
                    unreadable.append((row, column))
                    values.append(None)
                finally:
                    cell.release_jabelement()
                cells += 1
            rows.append(tuple(values))
            if stopped_early:
                break

        truncated = stopped_early or last < total_rows
        reason = ""
        if stopped_early:
            reason = f"cell budget reached ({max_cells} cells)"
        elif limited_by_cells:
            reason = (
                f"the cell budget ({max_cells} cells) allowed {len(rows)} row(s) of "
                f"{total_columns} column(s)"
            )
        elif last < total_rows:
            reason = f"{total_rows - last} more row(s) after this page"
        return TableReading(
            row_count=total_rows, column_count=total_columns, row_start=start,
            rows=tuple(rows), unreadable=tuple(unreadable), from_name=tuple(from_name),
            truncated=truncated, reason=reason,
        )


def act_driver(driver: Any, locator: str, expected: Record, action: str, *,
               value: Any = None, simulate: bool = False, row: Optional[int] = None,
               column: Optional[int] = None) -> None:
    """Verify the element at *locator*, then act on **that same element**.

    Resolving, checking and acting happen inside one element lifetime on purpose. Asking the
    tool layer to verify and then act would resolve twice, and between the two resolutions the
    tree could change -- which is exactly the race D2 exists to close.

    ``expected`` is the record the handle was taken from. Any disagreement stops the action:
    an element that is not the one the snapshot named must not be clicked, typed into or
    selected, however plausible it looks.

    Args:
        driver: The window's driver.
        locator: Where the element was.
        expected: What the handle recorded.
        action: ``"click"``, ``"double_click"``, ``"context_click"``, ``"set_text"``,
            ``"clear"``, ``"select"``, ``"select_cell"`` or ``"select_row"``.
        value: The text to type, or the option to select.
        simulate: Use simulated mouse/keyboard input instead of the accessibility action.
            pyjab's docs are explicit about the trade: it brings the window to the front and
            cannot work from a non-interactive session.
        row, column: Which cell or row to select, for the table actions.

    Raises:
        ElementChanged: The element is not the one the handle recorded.  Nothing is done.
        PyjabUnavailable: The locator no longer resolves at all.
    """
    with element_for(driver, locator) as element:
        actual = Record.from_pyjab(element.as_record())
        result = verify(expected, actual)
        if not result.ok:
            raise ElementChanged(result.message())

        if action == "click":
            element.click(simulate=simulate)
        elif action == "double_click":
            element.double_click()
        elif action == "context_click":
            element.context_click()
        elif action == "set_text":
            element.send_text(value, simulate=simulate)
        elif action == "clear":
            element.clear(simulate=simulate)
        elif action == "select":
            element.select(value, simulate=simulate)
        elif action == "select_cell":
            element.select_cell(row=row, column=column)
        elif action == "select_row":
            element.select_row(row=row)
        else:  # pragma: no cover - the tools only pass the names above
            raise ValueError(f"unknown action {action!r}")


def send_keys_driver(driver: Any, keys: str) -> None:
    """Send a keyboard chord -- ``alt+y`` -- to whichever window has the keyboard focus.

    pyjab 1.11.0 released ``JABDriver.send_keys(keys)`` (and the same on ``JABElement`` and
    ``Win32Utils``), which is what R4 was waiting on after gaozhao1989/pyjab#168 closed. The
    call is still **probed rather than assumed**, exactly like the window listing and
    ``detach()`` before their releases: an install below the pin is reachable, and a missing
    API is a reported state rather than an ``AttributeError``. The probe stays; only the
    sentence changes, and it names the release that has the method.

    Two things about the semantics belong in this docstring, because they are what a caller
    gets wrong:

    * **Nothing is focused and nothing is clicked.** The chord goes to whatever currently has
      the keyboard focus, which is normally the attached window because attaching brought it
      forward. pyjab's own note says why it is built that way: clicking a control to focus its
      window would activate that control, and avoiding that is usually the whole point of a
      shortcut.
    * **An unknown key name raises ``ValueError``** rather than doing nothing. That is
      deliberate on pyjab's side -- a shortcut that silently does nothing, or sends the wrong
      key, acts on somebody else's application and cannot be taken back.
    """
    send = getattr(driver, "send_keys", None)
    if send is None:
        raise PyjabApiGap(predates_the_pin("send_keys"))
    send(keys)


def screenshot_driver(driver: Any, *, locator: Optional[str] = None,
                      expected: Optional[Record] = None) -> bytes:
    """PNG bytes for the whole bound window, or for one element inside it.

    pyjab returns the bytes and nothing else, which is the right shape for this layer: no
    image library is pulled in on either side. ``get_screenshot_as_png`` builds the PNG from
    the pixels with ctypes and zlib in 1.9.0 -- Pillow became an extra in that release, and
    this project does not need it.

    With a locator, the element is verified first, exactly as an action is: photographing
    something is a read, but it is a read *of a particular element*, and a picture of whatever
    moved into the position would be as misleading as a click on it.
    """
    if locator is None:
        return driver.get_screenshot_as_png()
    with element_for(driver, locator) as element:
        if expected is not None:
            result = verify(expected, Record.from_pyjab(element.as_record()))
            if not result.ok:
                raise ElementChanged(result.message())
        return element.get_screenshot_as_png()


def find_driver(driver: Any, *, by: str, value: Any, visible: bool = False,
                limit: int = 20) -> "List[Tuple[Record, Optional[str]]]":
    """Search the window and return each match with the locator that finds it again.

    The search methods hand out elements this function owns, so every one of them is released
    here. Each match then gets a locator: the candidates from
    :func:`~pyjab_mcp.handles.locator_candidates` are resolved one at a time and the first
    that comes back as the same element wins. **An element none of them can find gets no
    locator** -- D2 is explicit that a locator which might point at something else is worse
    than no handle at all.

    ``by`` is matched with an explicit chain rather than ``getattr(driver, f"find_elements_by_{by}")``
    on purpose: a dynamically built method name is invisible to
    ``tools/check_pyjab_api_surface.py``, and a guard with a hole in it is worse than no guard.
    """
    methods = {
        "name": driver.find_elements_by_name,
        "role": driver.find_elements_by_role,
        "description": driver.find_elements_by_description,
        "states": driver.find_elements_by_states,
        "xpath": driver.find_elements_by_xpath,
    }
    method = methods.get(by)
    if method is None:
        raise ValueError(
            f"unknown search {by!r}; use one of {', '.join(sorted(methods))}"
        )

    elements = method(value, visible) or []
    found: "List[Tuple[Record, Optional[str]]]" = []
    try:
        records = [
            Record.from_pyjab(element.as_record()) for element in elements[:limit]
        ]
        positioned = _position_locators(driver, records)
        for position, record in enumerate(records):
            locator = positioned.get(position)
            if locator is None:
                # No walk path: fall back to a name or index locator, which resolves now but
                # may not survive the element's content changing.
                locator = _first_matching_locator(driver, record)
            found.append((record, locator))
    finally:
        # Every element the search handed over is ours, including the ones past the limit.
        for element in elements:
            element.release_jabelement()
    return found


#: How far the position walk goes. Deep on purpose: a locator is only as good as the path to
#: it, and the default snapshot depth is a budget for an agent rather than for identity.
POSITION_WALK_DEPTH = 12
POSITION_WALK_LIMIT = 4000


def _position_locators(driver: Any,
                       records: "Sequence[Record]") -> "Dict[int, Optional[str]]":
    """Locators built from a walk path, for the records a walk can find.

    Why this rather than a name: a locator built from a name stops resolving the moment the
    name changes -- and for a text field the name changes when *we* change it, because Swing
    reports the content as the name. A search result given a name locator could therefore not
    read back what it had just written. A walk records where an element *sits*, and a path does
    not move when the content does.

    One walk serves every result in the call. ``None`` values are results the walk could not
    place -- past its depth or limit, or not present in it -- and the caller falls back to a
    name locator for those.
    """
    if not records:
        return {}
    walk = walk_driver(driver, max_depth=POSITION_WALK_DEPTH, limit=POSITION_WALK_LIMIT)
    by_identity: "Dict[Tuple[Any, ...], List[int]]" = {}
    for index, node in enumerate(walk.nodes):
        by_identity.setdefault(node.record.identity(), []).append(index)

    located: "Dict[int, Optional[str]]" = {}
    for position, record in enumerate(records):
        matches = by_identity.get(record.identity(), [])
        if len(matches) > 1 and record.bounds:
            narrowed = [
                index for index in matches if walk.nodes[index].record.bounds == record.bounds
            ]
            matches = narrowed or matches
        if len(matches) == 1:
            located[position] = locator_for(walk, matches[0])
    return located


def _first_matching_locator(driver: Any, record: Record) -> Optional[str]:
    """The first locator candidate that resolves back to *record*, or None."""
    for candidate in locator_candidates(record):
        try:
            resolved = resolve_driver(driver, candidate)
        except PyjabUnavailable:
            continue
        if verify(record, resolved).ok:
            return candidate
    return None


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


def _cli_java_windows(run: Callable[[Sequence[str]], Any]) -> "List[JavaWindow]":
    """List windows through ``pyjab-inspect windows --json``, pyjab's documented CLI.

    Why a subprocess, and why this is acceptable as a stopgap: pyjab 1.9.0 has the function
    we need (``inspector.bridge_without_window``) but does not publish it, and the *CLI built
    on it* is public -- documented in ``docs/3-pyjab.md`` and shipped in the wheel. Using a
    documented interface beats importing an unpublished one, and the requirement to do
    better stays recorded.

    The cost is real and is not hidden: a second process arms its own bridge, so a listing
    costs a process startup rather than a bridge call. It is a listing, not a hot path.

    Raises:
        PyjabUnavailable: When the CLI cannot be run or does not answer with JSON. Never
            returns an empty list to mean "I could not tell" -- an empty list means JAB can
            see no Java windows, which is a measurement rather than a failure.
    """
    command = [sys.executable, "-m", "pyjab.inspector", "windows", "--json"]
    try:
        completed = run(command)
    except Exception as exc:  # noqa: BLE001
        raise PyjabUnavailable(
            f"could not run {command[-3:]} to list Java windows: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    if getattr(completed, "returncode", 1) != 0:
        message = (stderr.strip() or stdout.strip() or "no output")
        raise PyjabUnavailable(
            f"pyjab-inspect could not list Java windows (exit "
            f"{getattr(completed, 'returncode', '?')}): {message.splitlines()[0]}"
        )
    try:
        records = json.loads(stdout)
    except ValueError as exc:
        raise PyjabUnavailable(
            f"pyjab-inspect --json did not return JSON: {exc}. Output began: "
            f"{stdout.strip()[:120]!r}"
        ) from exc
    if not isinstance(records, list):
        raise PyjabUnavailable(
            f"pyjab-inspect --json returned {type(records).__name__}, expected a list"
        )
    return [_to_window(record) for record in records]


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
        # Not a missing API: pyjab's listing is documented to return hwnd/title/pid, so a
        # record without them is a response this project cannot read, and saying which part
        # was missing beats guessing.
        raise PyjabUnavailable(
            f"pyjab's window listing returned a record without an hwnd or a title: {record!r}"
        )
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
