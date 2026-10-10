"""L1 tests for acting: verify the element, then act on *that* element, or do nothing.

This is the layer where D2 stops being a design note and becomes the difference between a
message and a wrong click. Every action here resolves the locator, compares what is there now
against what the handle recorded, and only then acts -- on the same element it checked, so
there is no window between checking and acting in which the tree could move.

The assertion that matters most in this file is a negative one: when the element has changed,
**no action is recorded at all**. A test that only checked for the exception would pass even
if the click had already been sent.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import ElementChanged, PyjabUnavailable, act_driver
from pyjab_mcp.tree import Record
from tests._fakedriver import FakeDriver, record

LOCATOR = "//push button[@name='OK']"
EXPECTED = Record.from_pyjab(record("push button", "OK", index_in_parent=1))


def driver(**kwargs) -> FakeDriver:
    return FakeDriver(locators={LOCATOR: kwargs.pop("at", record("push button", "OK",
                                                                 index_in_parent=1))},
                      **kwargs)


@pytest.mark.parametrize("action,expected_call", [
    ("click", {"action": "click", "simulate": False}),
    ("double_click", {"action": "double_click"}),
    ("context_click", {"action": "context_click"}),
    ("set_text", {"action": "send_text", "value": "admin", "simulate": False}),
    ("select", {"action": "select", "option": "Tennis", "simulate": False}),
])
def test_each_action_reaches_the_pyjab_method_it_names(action, expected_call):
    subject = driver()

    act_driver(subject, LOCATOR, EXPECTED, action, value="admin" if action == "set_text" else "Tennis")

    assert subject.actions == [{**expected_call, "path": LOCATOR}]
    assert subject.outstanding == 0


def test_a_changed_element_is_not_acted_on_at_all():
    """The negative assertion: an exception alone would not prove nothing was clicked."""
    subject = driver(at=record("push button", "Cancel", index_in_parent=1))

    with pytest.raises(ElementChanged) as caught:
        act_driver(subject, LOCATOR, EXPECTED, "click")

    assert "name was 'OK', now 'Cancel'" in str(caught.value)
    assert "Nothing was done" in str(caught.value)
    assert subject.actions == [], "the click must not have been sent"
    assert subject.outstanding == 0


def test_a_moved_element_is_not_acted_on_either():
    """Bounds only count when both sides report them -- pyjab's own rule for as_record()."""
    was = Record.from_pyjab(record("push button", "OK", index_in_parent=1,
                                   bounds={"x": 1, "y": 1, "width": 2, "height": 2}))
    now = record("push button", "OK", index_in_parent=1,
                 bounds={"x": 9, "y": 1, "width": 2, "height": 2})
    subject = driver(at=now)

    with pytest.raises(ElementChanged) as caught:
        act_driver(subject, LOCATOR, was, "click")

    assert "bounds" in str(caught.value)
    assert subject.actions == []


def test_a_locator_that_no_longer_resolves_is_reported_before_anything_happens():
    subject = FakeDriver(locators={})

    with pytest.raises(PyjabUnavailable) as caught:
        act_driver(subject, LOCATOR, EXPECTED, "click")

    assert LOCATOR in str(caught.value)
    assert subject.actions == []
    assert subject.outstanding == 0


def test_table_selection_reaches_the_table_methods():
    cell = driver()
    act_driver(cell, LOCATOR, EXPECTED, "select_cell", row=2, column=1)
    act_driver(cell, LOCATOR, EXPECTED, "select_row", row=3)

    assert [call["action"] for call in cell.actions] == ["select_cell", "select_row"]
    assert cell.actions[0]["row"] == 2 and cell.actions[0]["column"] == 1
    assert cell.actions[1]["row"] == 3
    assert cell.outstanding == 0


def test_an_unknown_action_is_a_bug_rather_than_a_silent_no_op():
    subject = driver()

    with pytest.raises(ValueError, match="unknown action"):
        act_driver(subject, LOCATOR, EXPECTED, "teleport")

    assert subject.actions == []


def test_simulate_is_passed_through_where_pyjab_supports_it():
    subject = driver()

    act_driver(subject, LOCATOR, EXPECTED, "click", simulate=True)
    act_driver(subject, LOCATOR, EXPECTED, "set_text", value="x", simulate=True)

    assert subject.actions[0]["simulate"] is True
    assert subject.actions[1]["simulate"] is True
