"""The one thread that owns the accessibility bridge.

Why a thread, and why exactly one: Java Access Bridge is COM based, and it delivers
accessibility events -- including "a window opened" -- to the thread that called
``Windows_run()``.  That thread must service its own message queue, and every JAB call
must come from it.  ``MsgWaitForMultipleObjects`` and the rest operate on the *calling*
thread's queue, so a pump on a different thread from the calls does nothing for the calls,
and it fails without an exception: a window that opens after startup is simply never seen.

So this module owns:

* one dedicated thread, which constructs the backend,
* a loop that pumps the message queue and drains a work queue,
* the handoff for callers on other threads (``submit`` and ``call``).

Nothing here knows about pyjab.  The backend is injected, which is what makes it testable
on a machine with no Windows: ``tests/_fakebackend.py`` records the thread ident of every
call and raises when it is not the owner, so the rule above has an L1 test rather than only
a comment.

One honest limit: a thread blocked inside a cross-process call cannot be interrupted.
``close()`` therefore reports whether the thread actually joined instead of pretending the
shutdown was clean.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: What the loop pulls out of the queue to know it should stop.  A private object rather
#: than ``None`` so that a job can never be mistaken for it.
_STOP = object()


class BridgeError(RuntimeError):
    """Base class for every failure this module reports."""


class BridgeNotStarted(BridgeError):
    """A call was submitted before :meth:`BridgeThread.start`."""


class BridgeClosed(BridgeError):
    """A call was submitted after :meth:`BridgeThread.close`."""


class BridgeStartupError(BridgeError):
    """The backend could not be opened on the bridge thread.

    Carries the original failure's text, because "the bridge is unavailable" without the
    reason is the sort of message that costs an afternoon.  ``__cause__`` is preserved when
    there is one.
    """

    def __init__(self, message: str, *, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


@dataclass(frozen=True)
class BridgeStats:
    """Counters a diagnostics report can read from another thread.

    ``pump_errors`` is separate from ``pump_calls`` on purpose: a pump that keeps raising
    while the loop keeps serving jobs looks identical to a healthy one if all you count is
    jobs.
    """

    jobs_run: int = 0
    jobs_failed: int = 0
    pump_calls: int = 0
    pump_errors: int = 0
    last_pump_error: Optional[str] = None


class _Job:
    __slots__ = ("fn", "args", "kwargs", "future")

    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict,
                 future: "Future[Any]") -> None:
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.future = future


class BridgeThread:
    """Owns a backend, a message pump and a work queue on one thread.

    The backend object never leaves this class.  Callers submit a callable, which runs on
    the owning thread, and get a :class:`~concurrent.futures.Future` back.

    Args:
        open_backend: Called with no arguments **on the bridge thread**; returns the
            backend.  Raising here is a supported outcome: the error is recorded, every
            queued future is released with it, and later submissions fail fast with
            :class:`BridgeStartupError` rather than hanging.
        pump_interval: How long the loop waits for work before pumping again.  Idle time
            is pump time -- that is how a long-lived server keeps seeing events between
            calls.
        close_timeout: How long :meth:`close` waits for the thread to finish.
        name: Thread name, for the log line and for a stack dump.
    """

    def __init__(
        self,
        open_backend: Callable[[], Any],
        *,
        pump_interval: float = 0.05,
        close_timeout: float = 5.0,
        name: str = "pyjab-mcp-bridge",
    ) -> None:
        self._open_backend = open_backend
        self._pump_interval = pump_interval
        self._close_timeout = close_timeout
        self._name = name

        self._jobs: "queue.Queue[Any]" = queue.Queue()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()

        self._thread: Optional[threading.Thread] = None
        self._thread_ident: Optional[int] = None
        self._backend: Any = None
        self._startup_error: Optional[BaseException] = None
        self._closing = False
        self._joined = False
        self._stats = BridgeStats()

    # -- lifecycle ----------------------------------------------------------------

    def start(self, timeout: Optional[float] = None) -> "BridgeThread":
        """Start the thread and wait until the backend is up or has failed.

        Idempotent: calling it twice does not start a second thread.

        Raises:
            BridgeClosed: The thread was already closed.
            BridgeStartupError: The backend raised, or did not come up within *timeout*
                (in which case the thread is still running -- a backend call cannot be
                cancelled, so this reports rather than pretends).
        """
        with self._lock:
            if self._joined or self._closing:
                raise BridgeClosed("this bridge was closed and cannot be restarted")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name=self._name, daemon=True
                )
                self._thread.start()
            thread = self._thread

        # The wait is outside the lock: _run needs the lock to publish its outcome.
        if not self._ready.wait(timeout):
            raise BridgeStartupError(
                f"the bridge thread did not come up within {timeout!r}s; it is still "
                "running, and the backend call it is inside cannot be cancelled"
            )
        if self._startup_error is not None:
            raise BridgeStartupError(
                f"the bridge could not be opened: {self._startup_error}",
                cause=self._startup_error,
            )
        return self

    def close(self, timeout: Optional[float] = None) -> bool:
        """Ask the thread to stop, and report whether it actually did.

        Returns:
            True if the thread is gone.  False if it is still alive -- which means it is
            blocked inside a backend call.  That call cannot be cancelled, and the honest
            answer is a False rather than a hang.

        Safe to call twice, and safe to call before :meth:`start` (it closes the door, so a
        later ``start`` raises rather than quietly starting a thread nobody will stop).
        """
        with self._lock:
            if self._joined:
                return True
            self._closing = True
            thread = self._thread

        if thread is None:
            with self._lock:
                self._joined = True
            return True

        self._stop.set()
        self._jobs.put(_STOP)
        thread.join(self._close_timeout if timeout is None else timeout)
        joined = not thread.is_alive()

        with self._lock:
            self._joined = joined

        # Anything still queued will never run; release it now rather than leaving the
        # caller waiting on a future nothing will complete.
        self._fail_remaining(BridgeClosed("the bridge was closed before this call ran"))
        return joined

    # -- calls --------------------------------------------------------------------

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> "Future[Any]":
        """Queue *fn* to run on the bridge thread and return its future.

        Raises:
            BridgeNotStarted: :meth:`start` has not been called.
            BridgeStartupError: The backend failed to open; carries the original text.
            BridgeClosed: The bridge was closed.
        """
        with self._lock:
            if self._joined or self._closing:
                raise BridgeClosed("the bridge is closed")
            if self._thread is None:
                raise BridgeNotStarted("start() the bridge before submitting work")
            if self._startup_error is not None:
                raise BridgeStartupError(
                    f"the bridge could not be opened: {self._startup_error}",
                    cause=self._startup_error,
                )
        future: "Future[Any]" = Future()
        self._jobs.put(_Job(fn, args, kwargs, future))
        return future

    async def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Await a call on the bridge thread without blocking the event loop.

        The JAB calls behind this are cross-process and can take seconds; awaiting the
        future means the server keeps answering other requests meanwhile.
        """
        return await asyncio.wrap_future(self.submit(fn, *args, **kwargs))

    def invoke(self, method: str, *args: Any, **kwargs: Any) -> "Future[Any]":
        """Call *method* on the backend, on the owning thread.

        The backend object itself never leaves this class, deliberately: handing it out
        would turn "only the owning thread touches JAB" from a guarantee into a
        convention, and that convention is exactly what fails silently.
        """
        return self.submit(self._call_backend, method, args, kwargs)

    async def call_backend(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Await :meth:`invoke`."""
        return await asyncio.wrap_future(self.invoke(method, *args, **kwargs))

    def _call_backend(self, method: str, args: tuple, kwargs: dict) -> Any:
        return getattr(self._backend, method)(*args, **kwargs)

    # -- state --------------------------------------------------------------------

    @property
    def thread_ident(self) -> Optional[int]:
        """Ident of the owning thread, or None before it has started."""
        return self._thread_ident

    @property
    def stats(self) -> BridgeStats:
        with self._stats_lock:
            return self._stats

    @property
    def startup_error(self) -> Optional[BaseException]:
        return self._startup_error

    @property
    def started(self) -> bool:
        return self._thread is not None and self._ready.is_set()

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- the loop -----------------------------------------------------------------

    def _run(self) -> None:
        self._thread_ident = threading.get_ident()
        logger.debug("bridge thread started", extra={"thread": self._thread_ident})

        try:
            backend = self._open_backend()
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            # Deliberately broad: anything that stops the backend coming up has to come
            # out as a startup error with its text, or the server hangs waiting for a
            # bridge that will never arrive.
            self._startup_error = exc
            self._ready.set()
            logger.error("the bridge could not be opened: %s", exc)
            self._fail_remaining(BridgeStartupError(str(exc), cause=exc))
            return

        self._backend = backend
        self._ready.set()

        try:
            while not self._stop.is_set():
                self._pump(backend)
                try:
                    job = self._jobs.get(timeout=self._pump_interval)
                except queue.Empty:
                    continue
                if job is _STOP:
                    break
                self._execute(job)
        finally:
            # The backend's own teardown runs here, on the owning thread: releasing JAB
            # objects from another thread is the same class of mistake as calling them
            # from another thread.
            self._close_backend(backend)
            logger.debug("bridge thread stopped")

    def _pump(self, backend: Any) -> None:
        try:
            backend.pump()
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            with self._stats_lock:
                self._stats = replace(
                    self._stats,
                    pump_errors=self._stats.pump_errors + 1,
                    last_pump_error=detail,
                )
            # A pump that raises is recorded and the loop continues: a dead pump thread
            # would silently stop delivering events, which is the failure this module
            # exists to prevent.  The count is visible in java_diagnostics.
            logger.warning("the message pump raised: %s", detail)
        else:
            with self._stats_lock:
                self._stats = replace(self._stats, pump_calls=self._stats.pump_calls + 1)

    def _execute(self, job: _Job) -> None:
        if threading.get_ident() != self._thread_ident:
            # Unreachable through submit(); here so that a future refactor that runs jobs
            # from the caller's thread fails loudly instead of working on this machine and
            # failing on a user's.
            raise AssertionError("a job ran outside the bridge thread")
        try:
            result = job.fn(*job.args, **job.kwargs)
        except BaseException as exc:  # noqa: BLE001
            with self._stats_lock:
                self._stats = replace(self._stats, jobs_failed=self._stats.jobs_failed + 1)
            job.future.set_exception(exc)
        else:
            with self._stats_lock:
                self._stats = replace(self._stats, jobs_run=self._stats.jobs_run + 1)
            job.future.set_result(result)

    def _close_backend(self, backend: Any) -> None:
        close = getattr(backend, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("releasing the backend raised: %s", exc)

    def _fail_remaining(self, exc: BaseException) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job is _STOP:
                continue
            if not job.future.done():
                job.future.set_exception(exc)
