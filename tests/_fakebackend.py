"""A fake backend that refuses to agree with anything.

Modelled on pyjab's ``tests/_fakejab.py``, and for the same reason: a fake that agrees with
whatever it is handed proves nothing.  This one raises on the two mistakes the real backend
cannot show you on a machine without Windows --

* **a call from the wrong thread** (:class:`WrongThread`).  ``BridgeThread`` exists to make
  that impossible, and there is no other way to test it here: on a real desktop the same
  mistake appears as a dialog that is never seen, with no exception at all.
* **a call after close** (:class:`UseAfterClose`), which models reaching for a released
  JAB reference.

It also counts pumps and jobs, because the pump running *while idle* is a requirement, and
an injected pump failure is how "a failing pump must not kill the loop" is tested.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Sequence

from pyjab_mcp.backend import AttachedWindow
from pyjab_mcp.tree import Record, Walk, walked_nodes
from pyjab_mcp.windows import JavaWindow
from tests._fakedriver import record


class WrongThread(AssertionError):
    """The backend was touched by a thread that does not own it."""


class UseAfterClose(AssertionError):
    """The backend was used after it was released."""


class FakeBackend:
    """Records what happened to it, and refuses the two impossible-in-tests mistakes."""

    def __init__(
        self,
        *,
        windows: Sequence[JavaWindow] = (),
        pump_errors: int = 0,
        items: Sequence = (),
        attach_error: Optional[BaseException] = None,
        listing_error: Optional[BaseException] = None,
        matches: Optional[Dict[Any, Any]] = None,
        locators: Optional[Dict[str, Any]] = None,
        texts: Optional[Dict[str, Any]] = None,
        table_readings: Optional[Dict[str, Any]] = None,
        act_error: Optional[BaseException] = None,
    ) -> None:
        self.owner = threading.get_ident()
        self.windows = list(windows)
        self.pump_errors_remaining = pump_errors
        self.pump_calls = 0
        self.window_calls = 0
        self.attach_calls = 0
        self.walk_calls = 0
        self.find_calls = 0
        self.resolve_calls = 0
        self.text_calls = 0
        self.table_calls = 0
        self.acts: "list[dict]" = []
        self.act_error = act_error
        self.close_calls = 0
        self.closed = False
        self.threads_seen: "set[int]" = set()
        self.items = list(items)
        self.attach_error = attach_error
        self.listing_error = listing_error
        self.attached: "Dict[str, AttachedWindow]" = {}
        #: ``(by, value) -> [(Record, locator | None)]`` for the search the tool runs.
        self.matches = dict(matches or {})
        #: ``locator -> record`` for re-resolution.
        self.locators = dict(locators or {})
        #: ``locator -> text``.
        self.texts = dict(texts or {})
        #: ``locator -> TableReading``.
        self.table_readings = dict(table_readings or {})

    def bind(self, owner: int) -> "FakeBackend":
        """Declare which thread owns this backend.

        The real backend is constructed *by the bridge thread*, so a fake built in a test
        body would otherwise be owned by the test's thread and would refuse the executor's
        calls.  ``FakeFactory`` calls this with the thread that constructs it, which is
        what makes the ownership check mean anything.
        """
        self.owner = owner
        self.threads_seen = set()
        return self

    def _check(self, what: str) -> None:
        self.threads_seen.add(threading.get_ident())
        if self.closed:
            raise UseAfterClose(f"{what}() after close()")
        if threading.get_ident() != self.owner:
            raise WrongThread(
                f"{what}() ran on thread {threading.get_ident()}, "
                f"but {self.owner} owns the backend"
            )

    def pump(self) -> None:
        self._check("pump")
        self.pump_calls += 1
        if self.pump_errors_remaining > 0:
            self.pump_errors_remaining -= 1
            raise RuntimeError("injected pump failure")

    def java_windows(self) -> "list[JavaWindow]":
        self._check("java_windows")
        self.window_calls += 1
        if self.listing_error is not None:
            raise self.listing_error
        return list(self.windows)

    def attach(self, *, title: str = "", hwnd: Optional[int] = None,
               timeout: float = 30.0) -> AttachedWindow:
        self._check("attach")
        self.attach_calls += 1
        if self.attach_error is not None:
            raise self.attach_error
        window_id = f"w{self.attach_calls}"
        attached = AttachedWindow(
            window_id=window_id,
            hwnd=int(hwnd or 0x10001),
            title=title or "Test Application",
            pid=4242,
        )
        self.attached[window_id] = attached
        return attached

    def walk(self, window_id: str, *, max_depth: Optional[int] = None,
             limit: Optional[int] = None) -> Walk:
        self._check("walk")
        self.walk_calls += 1
        if window_id not in self.attached:
            raise RuntimeError(f"window {window_id!r} is not attached")
        nodes = walked_nodes(self.items)
        return Walk(
            root=Record.from_pyjab(record("root pane", "Test Application")),
            nodes=nodes, walked=len(nodes),
        )

    def resolve(self, window_id: str, locator: str) -> "Record":
        self._check("resolve")
        self.resolve_calls += 1
        if locator not in self.locators:
            from pyjab_mcp.backend import PyjabUnavailable

            raise PyjabUnavailable(f"the locator {locator!r} no longer resolves")
        return Record.from_pyjab(self.locators[locator])

    def find(self, window_id: str, *, by: str, value: Any, visible: bool = False,
             limit: int = 20):
        self._check("find")
        self.find_calls += 1
        return list(self.matches.get((by, value), []))[:limit]

    def text(self, window_id: str, locator: str) -> "Optional[str]":
        self._check("text")
        self.text_calls += 1
        if locator not in self.texts:
            from pyjab_mcp.backend import PyjabUnavailable

            raise PyjabUnavailable(f"the locator {locator!r} no longer resolves")
        return self.texts[locator]

    def act(self, window_id: str, locator: str, expected, action: str, *, value: Any = None,
            simulate: bool = False, row: Any = None, column: Any = None) -> None:
        self._check("act")
        self.acts.append({
            "locator": locator, "action": action, "value": value,
            "simulate": simulate, "row": row, "column": column,
        })
        if self.act_error is not None:
            raise self.act_error

    def table(self, window_id: str, locator: str, *, row_start: int = 0,
              row_count: int = 20, max_cells: int = 400):
        self._check("table")
        self.table_calls += 1
        reading = self.table_readings.get(locator)
        if reading is None:
            from pyjab_mcp.backend import PyjabUnavailable

            raise PyjabUnavailable(
                f"the element at {locator!r} is not a table, so it has no cells. "
                "java_snapshot or java_get_element will show what it is."
            )
        return reading

    def close(self) -> None:
        self._check("close")
        self.close_calls += 1
        self.closed = True


class FakeFactory:
    """The callable ``BridgeThread`` calls to open the backend.

    Records which thread did the constructing, because "the backend is built on the owning
    thread" is the half of the rule that a caller cannot see from the outside.
    """

    def __init__(
        self,
        backend: Optional[FakeBackend] = None,
        *,
        error: Optional[BaseException] = None,
    ) -> None:
        self.backend = backend if backend is not None else FakeBackend()
        self.error = error
        self.calls = 0
        self.construct_thread: Optional[int] = None

    def __call__(self) -> FakeBackend:
        self.calls += 1
        self.construct_thread = threading.get_ident()
        if self.error is not None:
            raise self.error
        # The backend now belongs to the thread that built it -- the bridge thread, in every
        # real use. Without this the fake would refuse the executor's own calls.
        return self.backend.bind(self.construct_thread)


def window(hwnd: int = 0x10001, title: str = "Test Application",
           pid: Optional[int] = 4242) -> JavaWindow:
    """One window record, for tests that only need one."""
    return JavaWindow(hwnd=hwnd, title=title, pid=pid)
