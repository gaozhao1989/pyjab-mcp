"""L1 tests for the handle table.

The property worth testing here is a negative one: the table holds no JAB references, so
eviction has nothing to release and no TTL is needed for the reason PLAN §3.2 gave. That is
D2's whole argument, and a test is the only thing that keeps it true -- a "small cache of live
elements for speed" added later would pass every functional test and reintroduce the leak
pyjab has found twice.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.handles import DEFAULT_CAPACITY, HandleTable
from pyjab_mcp.tree import Record
from tests._fakedriver import record

OK = Record.from_pyjab(record("push button", "OK"))


def table(**kwargs) -> HandleTable:
    return HandleTable(**kwargs)


def test_a_handle_carries_everything_needed_to_find_the_element_again():
    handles = table()

    handle = handles.add(
        session_id="s:1", window_id="w1",
        locator="//push button[@indexinparent=1]", expected=OK,
    )

    entry = handles.get(handle)
    assert entry is not None
    assert (entry.session_id, entry.window_id) == ("s:1", "w1")
    assert entry.locator == "//push button[@indexinparent=1]"
    assert entry.expected == OK
    assert handle.startswith("e:")


def test_handles_are_not_reused_while_they_are_live():
    handles = table()
    seen = {
        handles.add(session_id="s:1", window_id="w1", locator=f"//x[{i}]", expected=OK)
        for i in range(50)
    }

    assert len(seen) == 50


def test_the_table_is_bounded_and_forgets_the_least_recently_used_first():
    handles = table(capacity=2)
    first = handles.add(session_id="s:1", window_id="w1", locator="//a", expected=OK)
    second = handles.add(session_id="s:1", window_id="w1", locator="//b", expected=OK)

    handles.get(first)                                  # first becomes the recent one
    third = handles.add(session_id="s:1", window_id="w1", locator="//c", expected=OK)

    assert len(handles) == 2
    assert handles.get(second) is None, "the least recently used handle should have gone"
    assert handles.get(first) is not None
    assert handles.get(third) is not None


def test_an_unknown_handle_is_none_rather_than_an_error():
    """A handle from a previous server run is an ordinary thing for an agent to send."""
    assert table().get("e:ffff") is None


def test_clearing_a_session_leaves_other_sessions_alone():
    handles = table()
    handles.add(session_id="s:1", window_id="w1", locator="//a", expected=OK)
    keep = handles.add(session_id="s:2", window_id="w2", locator="//b", expected=OK)

    removed = handles.clear("s:1")

    assert removed == 1
    assert handles.handles_for("s:1") == ()
    assert handles.get(keep) is not None


def test_clearing_everything_reports_how_many_went():
    handles = table()
    for index in range(3):
        handles.add(session_id="s:1", window_id="w1", locator=f"//{index}", expected=OK)

    assert handles.clear() == 3
    assert len(handles) == 0


def test_the_default_capacity_is_the_plans_number():
    assert DEFAULT_CAPACITY == 256
    assert table().capacity == 256


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        table(capacity=0)


def test_eviction_holds_no_reference_to_release():
    """The negative property D2 buys: nothing to give back means eviction cannot leak."""
    handles = table(capacity=1)
    handles.add(session_id="s:1", window_id="w1", locator="//a", expected=OK)

    # No release call exists to make, and the entry that goes is a dict entry. If this ever
    # grows a release path, it is because somebody reintroduced held references.
    assert not hasattr(handles, "release")
    handles.add(session_id="s:1", window_id="w1", locator="//b", expected=OK)
    assert len(handles) == 1
