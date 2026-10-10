"""L1 tests for the two pyjab-facing functions: the bounded walk and the re-resolution.

These are the places where a mistake costs Java objects rather than a wrong answer, so they
are tested against a fake driver that refuses a double release and a use after release --
the discipline pyjab's ``tests/_fakejab.py`` established, and the reason two real leaks were
found there.

What can be proved here and what cannot: this shows the walker stops without abandoning the
walk (the fake's iterator records whether ``close()`` was called) and that a resolved element
is always given back, including when the read raises. Whether *real* JAB releases the
reference is pyjab's test to run, not this one's.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import PyjabUnavailable, resolve_driver, walk_driver
from tests._fakedriver import DoubleRelease, FakeDriver, UseAfterRelease, record

ITEMS = [
    (0, record("panel", "Form", index_in_parent=0, children_count=2, object_depth=1)),
    (1, record("text", "User", index_in_parent=0, object_depth=2)),
    (1, record("push button", "OK", index_in_parent=1, object_depth=2)),
]


def test_a_walk_returns_the_root_and_the_nodes_below_it():
    driver = FakeDriver(root=record("root pane", "App"), items=ITEMS)

    walk = walk_driver(driver, max_depth=2, limit=100)

    assert walk.root.label == 'root pane "App"'
    assert [node.record.name for node in walk.nodes] == ["Form", "User", "OK"]
    assert walk.walked == 3
    assert walk.truncated is False


def test_the_walk_stops_at_its_own_cap_and_closes_the_iterator():
    """Stopping early is only bounded if the iterator is closed: pyjab releases on close."""
    driver = FakeDriver(items=ITEMS)

    walk = walk_driver(driver, max_depth=2, limit=2)

    assert walk.walked == 2
    assert walk.truncated is True
    assert "raw walk cap reached" in walk.reason
    assert driver.trees[-1].closed is True, (
        "the walker abandoned the iterator without closing it, which in pyjab leaves the "
        "reference it was holding unreleased"
    )


def test_a_walk_that_finishes_does_not_close_anything():
    """The distinction matters: closing is for stopping early, and the fake sees both."""
    driver = FakeDriver(items=ITEMS)

    walk = walk_driver(driver, max_depth=2, limit=100)

    assert walk.truncated is False
    assert driver.trees[-1].exhausted is True
    assert driver.trees[-1].closed is False


def test_pyjabs_own_limit_is_reported_as_truncation():
    """A walk that ends exactly at pyjab's limit looks identical to a complete one."""
    driver = FakeDriver(items=ITEMS, walk_limit=2)

    walk = walk_driver(driver, max_depth=None, limit=10)

    assert walk.walked == 2
    assert walk.truncated is True
    assert "pyjab walk limit" in walk.reason


def test_the_depth_and_limit_reach_the_driver():
    driver = FakeDriver(items=ITEMS)

    walk_driver(driver, max_depth=2, limit=7)

    assert driver.walk_calls == [{"max_depth": 2, "limit": 7}]


def test_parent_links_and_role_positions_describe_the_tree():
    driver = FakeDriver(items=[
        (0, record("panel", "A", index_in_parent=0)),
        (1, record("push button", "One", index_in_parent=0)),
        (1, record("push button", "Two", index_in_parent=1)),
        (1, record("label", "Caption", index_in_parent=2)),
    ])

    walk = walk_driver(driver)

    panel, one, two, label = walk.nodes
    assert (one.parent, two.parent, label.parent) == (0, 0, 0)
    assert [one.role_position, two.role_position, label.role_position] == [1, 2, 1], (
        "role_position counts siblings sharing a role, which index_in_parent does not"
    )
    assert panel.parent is None


def test_a_resolved_element_is_released_exactly_once():
    driver = FakeDriver(locators={"//push button[@indexinparent=1]": record("push button", "OK")})

    resolved = resolve_driver(driver, "//push button[@indexinparent=1]")

    assert resolved.name == "OK"
    assert driver.handed_out == 1
    assert driver.outstanding == 0, "the reference outlived the call that took it"


def test_a_read_that_raises_still_releases():
    """The reference is taken, the read throws, and the caller still has to give it back."""
    driver = FakeDriver(
        locators={"//panel": record("panel", "P")}, explode={"//panel"}
    )

    with pytest.raises(RuntimeError, match="the bridge refused"):
        resolve_driver(driver, "//panel")

    assert driver.outstanding == 0, "an exception on the read path leaked the reference"


def test_a_locator_that_no_longer_resolves_is_reported_with_the_locator():
    driver = FakeDriver(locators={})

    with pytest.raises(PyjabUnavailable) as caught:
        resolve_driver(driver, "//push button[@indexinparent=9]")

    assert "//push button[@indexinparent=9]" in str(caught.value)
    assert driver.outstanding == 0


def test_the_fake_catches_a_double_release_and_a_use_after_release():
    """The fake has to be able to fail, or every test above proves nothing."""
    driver = FakeDriver(locators={"//panel": record("panel", "P")})
    element = driver.find_element_by_xpath("//panel")

    driver.release_jabelement(element)
    with pytest.raises(UseAfterRelease):
        element.as_record()
    with pytest.raises(DoubleRelease):
        driver.release_jabelement(element)
