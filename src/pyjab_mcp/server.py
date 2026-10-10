"""The MCP surface: a deliberately small set of tools over the bridge.

The tool count is a design decision, not an accident.  pyjab has sixty-odd public methods
and mapping them one-to-one onto sixty-odd tools would dilute the model's tool selection
for no gain, so the surface is grouped: diagnostics, session, observe, act, wait.  M1 ships
the first two groups; the rest arrive with M2 onwards.

Failure reporting follows one rule: an **anticipated** failure -- no bridge, a missing pyjab
API, a window that does not match -- is raised as ``ToolError`` with an actionable message,
which the SDK delivers to the model as ``is_error=True``.  Anything else propagates and is
logged as a crash, because a bug in this code is not something to explain to a model as if
it were a fact about the world.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import secrets
import threading
from typing import Callable, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .backend import AttachedWindow, PyjabApiGap, PyjabUnavailable, open_backend
from .bridge import BridgeClosed, BridgeNotStarted, BridgeStartupError, BridgeThread
from .diagnostics import collect
from .handles import HandleTable, locator_for
from .tree import DEFAULT_FIELDS, KNOWN_FIELDS
from .windows import JavaWindow, WindowLookupError, render_windows, resolve_window

logger = logging.getLogger(__name__)

#: From PLAN §4: the MCP convention is ``{service}_mcp``, and this is the name a client
#: sees.  The PyPI package is ``pyjab-mcp`` and the console script is ``pyjab-mcp``.
SERVER_NAME = "pyjab_mcp"

INSTRUCTIONS = (
    "Drives Java desktop applications (Swing/AWT/JavaFX) through Java Access Bridge, via "
    "pyjab. It never modifies, restarts or injects into the target application. Start with "
    "java_diagnostics if anything looks wrong: it reports which part of the environment is "
    "missing instead of failing silently. All tools must run on the same Windows machine as "
    "the application."
)


class ServerState:
    """The bridge thread and the sessions, owned by one server instance.

    The bridge starts **lazily**, on the first tool call that needs it, so that
    ``java_diagnostics`` can report a broken environment without having already tried to
    load a DLL.  Starting is idempotent and thread-safe.
    """

    def __init__(
        self,
        *,
        open_backend: Callable[[], object] = open_backend,
        pump_interval: float = 0.05,
        start_timeout: float = 30.0,
    ) -> None:
        self._open_backend = open_backend
        self._pump_interval = pump_interval
        # A backend that never comes up must not leave a tool call waiting for ever. pyjab's
        # own window timeout is 30s, so this matches the substrate's idea of "too long".
        self._start_timeout = start_timeout
        self._lock = threading.Lock()
        self._bridge: Optional[BridgeThread] = None
        self._sessions: "dict[str, AttachedWindow]" = {}
        #: Element handles, bounded and holding no JAB references -- see handles.py.
        self._handles = HandleTable()

    # -- the bridge ---------------------------------------------------------------

    def bridge(self) -> BridgeThread:
        """The bridge object, created but not necessarily started."""
        with self._lock:
            if self._bridge is None:
                self._bridge = BridgeThread(
                    self._open_backend, pump_interval=self._pump_interval
                )
            return self._bridge

    def existing_bridge(self) -> Optional[BridgeThread]:
        """The bridge if something has already asked for it, else None."""
        with self._lock:
            return self._bridge

    async def started_bridge(self) -> BridgeThread:
        """Start the bridge if needed, without blocking the event loop.

        ``start()`` waits for the backend to come up, and the backend's construction is a
        blocking call into pyjab.  It runs in the default executor rather than on the event
        loop, so a slow import cannot stall every other request.
        """
        bridge = self.bridge()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, functools.partial(bridge.start, timeout=self._start_timeout)
        )
        return bridge

    async def stop(self) -> bool:
        """Stop the bridge, if there is one.  Returns whether the thread really went away."""
        bridge = self.existing_bridge()
        if bridge is None:
            return True
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, bridge.close)

    # -- sessions -----------------------------------------------------------------

    def new_session(self, attached: "AttachedWindow") -> str:
        """Record a window the backend has bound, under a new session id.

        The session is what a later call names, and the ``window_id`` inside it is what the
        backend uses to reach the bound driver.  Sessions are not released: pyjab has no
        non-destructive detach (``backend.REQUIREMENTS``, key ``detach``), so a bound driver
        stays bound for the life of the process.  One driver per attached window -- never
        one per call -- is what keeps that bounded.
        """
        with self._lock:
            while True:
                session_id = "s:" + secrets.token_hex(2)
                if session_id not in self._sessions:
                    break
            self._sessions[session_id] = attached
            return session_id

    def session(self, session_id: str) -> "AttachedWindow":
        """The window behind *session_id*.

        Raises:
            KeyError: With the sessions that do exist, so the tool can say what to do rather
                than reporting a bare miss -- a session id from a previous server run is the
                usual reason.
        """
        with self._lock:
            attached = self._sessions.get(session_id)
            known = tuple(self._sessions)
        if attached is None:
            raise KeyError(
                f"unknown session {session_id!r}."
                + (f" Known sessions: {', '.join(known)}." if known
                   else " No window is attached yet -- call java_attach_window first.")
            )
        return attached

    @property
    def handles(self) -> HandleTable:
        return self._handles

    @property
    def sessions(self) -> "dict[str, AttachedWindow]":
        with self._lock:
            return dict(self._sessions)


def _tool_error(exc: BaseException) -> ToolError:
    """Turn an anticipated failure into a message a model can act on."""
    if isinstance(exc, PyjabApiGap):
        return ToolError(str(exc))
    if isinstance(exc, KeyError) and exc.args:
        return ToolError(str(exc.args[0]))
    if isinstance(exc, PyjabUnavailable):
        return ToolError(
            f"{exc}\nRun java_diagnostics for the full picture."
        )
    if isinstance(exc, BridgeStartupError):
        return ToolError(
            f"the accessibility bridge is not available: {exc}\n"
            "Run java_diagnostics to see which part of the environment is missing."
        )
    if isinstance(exc, (BridgeNotStarted, BridgeClosed)):
        return ToolError(f"the accessibility bridge is not usable: {exc}")
    if isinstance(exc, WindowLookupError):
        return ToolError(str(exc))
    return ToolError(f"{type(exc).__name__}: {exc}")


def create_server(*, state: Optional[ServerState] = None) -> MCPServer:
    """Build the server.  Registration order is the order ``tools/list`` reports."""
    state = state or ServerState()
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        name="java_diagnostics",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def java_diagnostics(load_bridge: bool = False) -> str:
        """Report whether this machine can drive a Java application right now.

        Use this first when anything fails, and after installing a JVM or enabling Java
        Access Bridge. It reports the platform, the interpreter, the installed pyjab and
        which of its public APIs are present, whether Java Access Bridge is enabled, where
        the bridge DLL is, and the state of the bridge thread.

        It is read-only and never writes to the environment: it does not enable Java Access
        Bridge, because a diagnostics tool that changes what it is reporting on is worse
        than none.

        Args:
            load_bridge: When true, also start the bridge thread first and report whether it
                came up. This is the end-to-end check; it is off by default so that a mere
                report cannot load a DLL.

        Returns:
            A multi-line report. A line that could not be measured says
            ``UNAVAILABLE (reason)`` and is never reported as zero or as "missing" --
            "nothing is there" and "I could not read" are different answers.

        Errors:
            None for the environment itself: an unusable environment is what this tool
            reports, so it does not fail because of one.
        """
        if load_bridge:
            try:
                await state.started_bridge()
            except BridgeStartupError as exc:
                # Not an error for this tool: it is the finding. The report below carries
                # the same text in the bridge-thread row.
                logger.info("java_diagnostics: the bridge did not start: %s", exc)
        report = collect(bridge=state.existing_bridge())
        return report.render()

    @server.tool(
        name="java_list_windows",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def java_list_windows() -> str:
        """List the Java windows Java Access Bridge can see on this machine.

        Use this to find out whether the application is visible to JAB at all, and to get a
        title or hwnd for java_attach_window. If the application is not in this list,
        nothing else will work and the problem is the attachment or the environment, not a
        locator -- run java_diagnostics next.

        Only windows of Java applications are listed; this is not a general Windows window
        list.

        Returns:
            One line per window, with hwnd, pid and title. "No Java windows are visible" is
            a measurement and is reported as such, not as an empty string.

        Errors:
            ``is_error`` with an actionable message when pyjab cannot answer. On pyjab 1.9.0
            the listing runs through the documented ``pyjab-inspect windows --json``,
            because the function behind it is not public API yet; the message says so when
            even that is unavailable.
        """
        try:
            bridge = await state.started_bridge()
            windows = await bridge.call_backend("java_windows")
        except Exception as exc:  # noqa: BLE001 - re-raised as ToolError below
            raise _tool_error(exc) from exc
        return render_windows(list(windows))

    @server.tool(
        name="java_attach_window",
        annotations=ToolAnnotations(open_world_hint=True),
    )
    async def java_attach_window(title: str = "", hwnd: Optional[int] = None) -> str:
        """Bind to one Java window and return a session id for later calls.

        Use this after java_list_windows, once you know which window to drive. Give either
        an exact ``hwnd`` or a ``title``; a title may be an fnmatch pattern
        (``"*Report*"``) exactly as pyjab matches it. Pass ``hwnd`` when several windows
        share a title pattern.

        Binding is also what arms Java Access Bridge for this server: pyjab loads the DLL,
        hooks the events and registers the JAB prototypes on the thread doing the binding,
        so the first attach is slower than the later ones.

        This never modifies, restarts or injects into the application, and it does not own
        the process: the application keeps running whatever happens to this session.

        Args:
            title: Window title, matched with fnmatch. Ignored when ``hwnd`` is given.
            hwnd: Window handle from java_list_windows. Takes precedence over ``title``.

        Returns:
            A session id, and the window it is bound to. Pass the session id to
            java_snapshot.

        Errors:
            ``is_error`` when the window cannot be bound, naming what was tried. A window
            that is not a Java window, or a title that never appears, is the usual reason;
            java_list_windows shows what JAB can see.
        """
        try:
            bridge = await state.started_bridge()
            attached = await bridge.call_backend(
                "attach", title=title, hwnd=hwnd, timeout=30.0
            )
        except Exception as exc:  # noqa: BLE001
            raise _tool_error(exc) from exc

        session_id = state.new_session(attached)
        return (
            f"{session_id} -> hwnd={attached.hwnd} title={attached.title!r}"
            + (f" pid={attached.pid}" if attached.pid else "")
            + f"\nBound for later calls. Start with java_snapshot(session_id={session_id!r})."
        )

    @server.tool(
        name="java_snapshot",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def java_snapshot(
        session_id: str,
        depth: int = 2,
        max_nodes: int = 120,
        only_interactive: bool = True,
        fields: Optional[list[str]] = None,
    ) -> str:
        """The bounded tree view of an attached window, one line per element, with handles.

        This is the tool to reach for first after attaching: it shows what is in the window,
        what each element is called, and a handle (``[e:1a2b]``) for every element a later
        call can name. Pass a handle to a tool that acts on elements; pass a session id
        here.

        The view is deliberately bounded, and it says so when it was cut short. A Java
        window can hold thousands of nodes, and returning one puts hundreds of thousands of
        tokens into the conversation, so read in rings: snapshot at depth 2, then snapshot
        again if you need to see inside something.

        Handles are positions, not references: nothing is held between calls. Each handle
        records the element's role, name, position among siblings and bounds, and a later
        call re-finds the element and checks those before doing anything. If the window
        moved on -- a list reloaded, a dialog replaced a panel -- the handle fails with the
        difference rather than acting on whatever is there now.

        Args:
            session_id: From java_attach_window.
            depth: How many levels below the window to read. 2 is a good default; raise it
                only when the elements you want are not visible at 2.
            max_nodes: Hard cap on rendered elements. When it is reached the reply says so
                and names the way to narrow the next read.
            only_interactive: Drop pure layout scaffolding (panes, panels, viewports). Set
                it to false when you need the structure rather than the controls, or when
                an element you expect is missing -- filtering is the usual reason.
            fields: Extra per-element fields to print: any of states (the default), bounds,
                description, children, index, depth. Role and name are always printed,
                because they are what a handle checks itself against.

        Returns:
            One line per element, indented by depth, plus a truncation notice when the view
            was cut short and a count of elements that have no handle.

        Errors:
            ``is_error`` for an unknown session id (the message lists the live ones), or
            when the window can no longer be read -- which usually means the application
            closed it.
        """
        try:
            bridge = await state.started_bridge()
            attached = state.session(session_id)
            requested = tuple(fields) if fields else DEFAULT_FIELDS
            unknown = [name for name in requested if name not in KNOWN_FIELDS]
            if unknown:
                raise PyjabUnavailable(
                    f"unknown field(s) {unknown}; known fields are {list(KNOWN_FIELDS)}"
                )
            # The raw walk cap is the node budget times four: the filter drops containers
            # and the cap counts what is kept, so a walk bounded at max_nodes exactly would
            # return fewer kept nodes than asked for on a scaffolding-heavy window.
            walk = await bridge.call_backend(
                "walk", attached.window_id,
                max_depth=max(0, depth), limit=max(1, max_nodes) * 4,
            )
        except Exception as exc:  # noqa: BLE001
            raise _tool_error(exc) from exc

        # Two passes, because a handle only exists for a node that will be rendered and a
        # node's line needs its handle. Both passes are pure and cheap; the bridge is not
        # touched again.
        first = walk.to_text(max_nodes=max_nodes, only_interactive=only_interactive,
                             fields=requested)
        handles: "dict[int, str]" = {}
        unaddressable: "list[int]" = []
        for index in first.rendered_indices:
            locator = locator_for(walk, index)
            if locator is None:
                unaddressable.append(index)
                continue
            handles[index] = state.handles.add(
                session_id=session_id,
                window_id=attached.window_id,
                locator=locator,
                expected=walk.nodes[index].record,
            )
        rendered = walk.to_text(
            max_nodes=max_nodes, only_interactive=only_interactive, fields=requested,
            handles=handles, unaddressable=unaddressable,
            root_note=f"hwnd={attached.hwnd}"
            + (f" pid={attached.pid}" if attached.pid else ""),
        )
        return (
            rendered.text
            + f"\n{rendered.shown} element(s) shown, about {rendered.estimated_tokens} "
            "tokens. Handles are positions, not references: re-snapshot after the window "
            "changes."
        )

    return server
