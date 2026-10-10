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
import json
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

from .handles import locator_candidates, verify
from .tree import Record, Walk, walked_nodes
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
        blocks="java_list_windows without the pyjab-inspect fallback",
        wanted="a public API that lists the Java windows JAB can see",
        why=(
            "JABDriver cannot be constructed without binding a window, and a raw bridge "
            "call is wrong until JABFixedFunc._fix_bridge_functions() has run -- which is "
            "private, and without it ctypes truncates a 64-bit HWND to 32 bits, so "
            "isJavaWindow would answer about a different window. pyjab 1.9.0 ships "
            "pyjab.inspector.bridge_without_window(), which is exactly the missing piece, "
            "but it is not in that module's __all__ and no documentation promises it."
        ),
        # Restated after 1.9.0: the release shipped the walk (R3, retired) and the inspector
        # module, and did not promote this function or add a listing API.
        expected_in="a pyjab minor after 1.9.0; 1.9.0 does not have it",
    ),
    Requirement(
        key="detach",
        blocks="releasing a session without ending the application (M2 sessions)",
        wanted="a public, non-destructive way to detach from a window it did not launch",
        why=(
            "the only teardown JABDriver has is __exit__, which terminates the bound "
            "process by pid; calling it on a user's application would kill it. 1.9.0 added "
            "no close()/quit()/detach(), so a session's accessible-context reference cannot "
            "be given back. This server therefore holds at most one driver per attached "
            "window, never calls __exit__, and says so in java_diagnostics."
        ),
        expected_in="a pyjab minor after 1.9.0; 1.9.0 does not have it",
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
            "pyjab.inspector (CLI fallback)",
            AVAILABLE if importlib.util.find_spec("pyjab.inspector") else MISSING,
            "shipped in 1.9.0; the documented `pyjab-inspect windows --json` is what this "
            "server falls back to for listing, because its bridge_without_window() helper "
            "is not public",
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

    def close(self) -> None:
        """Release what this backend holds, without touching the applications it observes."""


@dataclass(frozen=True)
class AttachedWindow:
    """A window this backend has bound, and the id other calls use to reach it."""

    window_id: str
    hwnd: int
    title: str
    pid: Optional[int] = None


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
            return tuple(_to_window(record) for record in listing())
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

    def close(self) -> None:
        """Drop the bound drivers **without ending the applications they observe**.

        This is the R2 gap made explicit. A driver holds an accessible-context reference and
        the only teardown pyjab offers is ``JABDriver.__exit__``, which sends ``SIGTERM`` to
        the bound pid. So this releases nothing and says so: the contexts go when the process
        does. One driver per attached window, never one per call, is what keeps that bounded.
        """
        self._drivers.clear()

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
        for element in elements[:limit]:
            record = Record.from_pyjab(element.as_record())
            found.append((record, _first_matching_locator(driver, record)))
    finally:
        # Every element the search handed over is ours, including the ones past the limit.
        for element in elements:
            element.release_jabelement()
    return found


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
            see no Java windows, which is a measurement (AGENTS.md §2.4).
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
