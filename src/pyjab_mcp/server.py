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
from .backend import PyjabApiGap, PyjabUnavailable, open_backend
from .bridge import BridgeClosed, BridgeNotStarted, BridgeStartupError, BridgeThread
from .diagnostics import collect
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
        self._sessions: "dict[str, JavaWindow]" = {}

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

    def attach(self, window: JavaWindow) -> str:
        """Record *window* under a new session id.

        The id is short and opaque, the same shape the element handles will use in M2.  M1
        deliberately holds **no JAB reference** for a session: pyjab has no non-destructive
        detach (see ``backend.REQUIREMENTS``), so holding a driver would either leak a Java
        object per attach or end the user's application when the session closed.
        """
        with self._lock:
            while True:
                session_id = "s:" + secrets.token_hex(2)
                if session_id not in self._sessions:
                    break
            self._sessions[session_id] = window
            return session_id

    @property
    def sessions(self) -> "dict[str, JavaWindow]":
        with self._lock:
            return dict(self._sessions)


def _tool_error(exc: BaseException) -> ToolError:
    """Turn an anticipated failure into a message a model can act on."""
    if isinstance(exc, PyjabApiGap):
        return ToolError(str(exc))
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
            ``is_error`` with an actionable message when pyjab cannot answer -- including
            the case where the installed pyjab does not yet expose a public way to list
            windows, in which case the message names what is missing and why it cannot be
            worked around.
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
        an exact ``hwnd`` or a ``title``, which may be an fnmatch pattern (``"*Report*"``)
        exactly as pyjab matches titles. Pass ``hwnd`` when several windows share a title
        pattern.

        This never modifies, restarts or injects into the application, and it does not own
        the process: the application keeps running whatever happens to this session.

        Args:
            title: Window title, matched with fnmatch. Ignored when ``hwnd`` is given.
            hwnd: Window handle from java_list_windows. Takes precedence over ``title``.

        Returns:
            The session id, the window it is bound to, and the hwnd, as text.

        Errors:
            ``is_error`` when nothing matched, when a title pattern matched more than one
            window (the message lists them -- acting on the first of several is how a tool
            clicks the wrong thing), or when the bridge or pyjab cannot answer.
        """
        try:
            bridge = await state.started_bridge()
            windows = list(await bridge.call_backend("java_windows"))
            window = resolve_window(windows, hwnd=hwnd, title=title)
        except Exception as exc:  # noqa: BLE001
            raise _tool_error(exc) from exc

        session_id = state.attach(window)
        return (
            f"{session_id} -> hwnd={window.hwnd} title={window.title!r}"
            + (f" pid={window.pid}" if window.pid else "")
            + "\nThe session binds this window for later calls. Releasing a session needs a "
            "non-destructive detach that pyjab does not have yet, so this build deliberately "
            "holds no reference it cannot give back -- see java_diagnostics."
        )

    return server
