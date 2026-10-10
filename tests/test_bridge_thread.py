"""L1 tests for the bridge thread: the one place the thread-affinity rule is checkable.

None of this drives a real bridge, and none of it needs Windows.  What it can prove is
everything about the *loop*: that the backend is built on the owning thread, that jobs run
there, that the pump keeps running while the loop is idle, that a failure is reported rather
than hung, and that closing releases what it should.

What it cannot prove is that a real JAB call from the wrong thread is invisible until a
dialog opens.  That is L3's job and only L3's; see the pyjab-mcp-testing skill.
"""

from __future__ import annotations

import threading
import time

import pytest

from pyjab_mcp.backend import PyjabUnavailable
from pyjab_mcp.bridge import (
    BridgeClosed,
    BridgeNotStarted,
    BridgeStartupError,
    BridgeThread,
)
from tests._fakebackend import FakeBackend, FakeFactory, UseAfterClose, WrongThread, window


def wait_for(predicate, timeout: float = 2.0, interval: float = 0.005) -> bool:
    """Wait for *predicate*, so a test never sleeps for longer than it has to."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def bridge():
    """A started bridge over a fake backend, closed even when a test fails."""
    backend = FakeBackend()
    factory = FakeFactory(backend)
    thread = BridgeThread(factory, pump_interval=0.01)
    thread.start(timeout=5)
    try:
        yield thread, backend, factory
    finally:
        thread.close()


def test_the_backend_is_constructed_on_the_bridge_thread(bridge):
    thread, _backend, factory = bridge

    assert factory.calls == 1
    assert factory.construct_thread == thread.thread_ident
    assert thread.thread_ident != threading.get_ident(), (
        "the test's own thread must not be the owner, or nothing below proves anything"
    )


def test_a_job_runs_on_the_bridge_thread(bridge):
    thread, backend, _factory = bridge

    # The fake raises WrongThread if this runs anywhere but the owner, so a passing call is
    # the assertion.
    assert thread.submit(backend.java_windows).result(timeout=5) == []
    assert backend.threads_seen == {thread.thread_ident}


def test_the_fake_would_catch_a_call_from_the_wrong_thread():
    """The fake has to be able to fail, or the test above proves nothing."""
    backend = FakeBackend().bind(threading.get_ident() + 1)
    with pytest.raises(WrongThread):
        backend.pump()


def test_the_pump_runs_while_the_loop_is_idle(bridge):
    """Idle time is pump time -- without this, a window that opens later is never seen."""
    thread, backend, _factory = bridge

    assert wait_for(lambda: backend.pump_calls >= 3), (
        f"the pump ran {backend.pump_calls} time(s) with nothing to do"
    )
    assert thread.stats.pump_calls >= 3
    assert thread.stats.pump_errors == 0


def test_a_failing_job_is_reported_and_the_loop_survives(bridge):
    thread, backend, _factory = bridge

    def explode() -> None:
        raise ValueError("job failed")

    failed = thread.submit(explode)
    with pytest.raises(ValueError, match="job failed"):
        failed.result(timeout=5)

    assert thread.stats.jobs_failed == 1
    assert thread.submit(backend.java_windows).result(timeout=5) == []


def test_a_failing_pump_is_counted_and_the_loop_survives():
    backend = FakeBackend(pump_errors=2)
    thread = BridgeThread(FakeFactory(backend), pump_interval=0.01)
    thread.start(timeout=5)
    try:
        assert wait_for(lambda: backend.pump_calls >= 4), backend.pump_calls
        stats = thread.stats
        assert stats.pump_errors == 2
        assert "injected pump failure" in (stats.last_pump_error or "")
        # The loop is still serving: a dead pump thread would be the silent failure this
        # whole module exists to prevent.
        assert thread.submit(backend.java_windows).result(timeout=5) == []
    finally:
        thread.close()


def test_a_backend_that_cannot_open_is_reported_rather_than_hung():
    factory = FakeFactory(error=PyjabUnavailable("this is not Windows"))
    thread = BridgeThread(factory, pump_interval=0.01)

    with pytest.raises(BridgeStartupError) as caught:
        thread.start(timeout=5)
    assert "this is not Windows" in str(caught.value)
    assert isinstance(caught.value.__cause__, PyjabUnavailable)

    # Fail fast afterwards, with the same reason, instead of queueing work for ever.
    with pytest.raises(BridgeStartupError) as caught:
        thread.submit(lambda: None)
    assert "this is not Windows" in str(caught.value)

    assert thread.startup_error is not None
    thread.close()


def test_work_submitted_before_start_is_refused():
    thread = BridgeThread(FakeFactory(), pump_interval=0.01)
    with pytest.raises(BridgeNotStarted):
        thread.submit(lambda: None)
    thread.close()


def test_close_runs_the_backends_own_teardown_on_the_owning_thread(bridge):
    thread, backend, _factory = bridge

    assert thread.close() is True
    assert backend.close_calls == 1
    assert backend.closed is True
    # close() is a backend call, so it went through the fake's thread check as well.
    assert backend.threads_seen == {thread.thread_ident}
    assert thread.alive is False

    with pytest.raises(BridgeClosed):
        thread.submit(lambda: None)


def test_closing_before_starting_closes_the_door():
    thread = BridgeThread(FakeFactory(), pump_interval=0.01)
    assert thread.close() is True
    with pytest.raises(BridgeClosed):
        thread.start(timeout=1)


def test_no_future_is_left_hanging_when_the_bridge_closes(bridge):
    thread, backend, _factory = bridge

    futures = [thread.submit(backend.java_windows) for _ in range(25)]
    thread.close()

    assert all(future.done() for future in futures), (
        "a future nobody completes is a tool call that hangs for ever"
    )


def test_the_fake_refuses_use_after_close():
    backend = FakeBackend()
    backend.close()
    with pytest.raises(UseAfterClose):
        backend.pump()


@pytest.mark.asyncio
async def test_a_job_can_be_awaited_without_blocking_the_loop():
    backend = FakeBackend(windows=[window()])
    thread = BridgeThread(FakeFactory(backend), pump_interval=0.01)
    thread.start(timeout=5)
    try:
        windows = await thread.call_backend("java_windows")
        assert [w.title for w in windows] == ["Test Application"]
        assert backend.window_calls == 1
    finally:
        thread.close()
