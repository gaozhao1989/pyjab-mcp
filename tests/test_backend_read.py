"""L1 tests for the read paths: searching, and reading text.

Both go through elements this layer owns and must give back, which is the part worth testing
against a fake that counts references. ``find_driver`` is the sharper case: a search hands
over *every* match, including the ones past the result limit, so a loop that breaks early
leaks the rest -- the exact shape of the leak pyjab's own walk had to be fixed for.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import PyjabUnavailable, find_driver, text_driver
from pyjab_mcp.handles import locator_candidates
from pyjab_mcp.tree import Record
from tests._fakedriver import FakeDriver, record

BUTTON = record("push button", "OK", index_in_parent=1)
BY_NAME = "//push button[@name='OK']"


def driver_with(*, matches=(BUTTON,), locators=None, text=None, **kwargs) -> FakeDriver:
    return FakeDriver(
        search={("name", "OK"): list(matches)},
        locators=locators if locators is not None else {BY_NAME: BUTTON},
        text=text,
        **kwargs,
    )


def test_find_returns_each_match_with_the_locator_that_finds_it_again():
    driver = driver_with()

    found = find_driver(driver, by="name", value="OK")

    assert len(found) == 1
    found_record, locator = found[0]
    assert found_record.name == "OK"
    assert locator == BY_NAME


def test_find_gives_every_handed_over_element_back():
    """Including the ones past its own limit, which is where a `break` would leak."""
    driver = driver_with(matches=[BUTTON, BUTTON, BUTTON])

    found = find_driver(driver, by="name", value="OK", limit=1)

    assert len(found) == 1
    assert driver.outstanding == 0, (
        "the search handed over three elements and this kept one; the other two are still "
        "outstanding, which in pyjab is a Java object per search"
    )


def test_find_hands_out_nothing_when_the_search_matches_nothing():
    driver = driver_with(matches=[])

    assert find_driver(driver, by="name", value="OK") == []
    assert driver.outstanding == 0


def test_a_match_that_no_locator_can_find_gets_no_locator():
    """D2: a handle that might point somewhere else is worse than no handle."""
    driver = driver_with(locators={})

    found = find_driver(driver, by="name", value="OK")

    assert len(found) == 1
    assert found[0][1] is None
    assert driver.outstanding == 0


def test_find_falls_back_to_the_next_candidate_when_the_first_is_ambiguous():
    """A name shared by two controls: the name-only locator resolves to the wrong one."""
    other = record("push button", "OK", index_in_parent=9)
    with_index = "//push button[@name='OK'][@indexinparent=1]"
    driver = driver_with(locators={BY_NAME: other, with_index: BUTTON})

    found = find_driver(driver, by="name", value="OK")

    assert found[0][1] == with_index
    assert driver.outstanding == 0


def test_find_names_the_searches_it_supports_when_asked_for_an_unknown_one():
    driver = driver_with()

    with pytest.raises(ValueError) as caught:
        find_driver(driver, by="colour", value="red")

    assert "colour" in str(caught.value)
    assert "name" in str(caught.value) and "xpath" in str(caught.value)


@pytest.mark.parametrize("by", ["name", "role", "description", "states", "xpath"])
def test_find_calls_the_search_method_that_matches_the_selector(by):
    driver = driver_with()

    find_driver(driver, by=by, value="OK")

    assert driver.searches[0]["by"] == by


def test_text_returns_what_the_element_reports():
    driver = driver_with(text={BY_NAME: "hello"})

    assert text_driver(driver, BY_NAME) == "hello"
    assert driver.outstanding == 0


def test_text_is_none_when_the_element_has_no_text_interface():
    """None is pyjab's answer for 'no Accessible Text', and it is not the empty string."""
    driver = driver_with(text={BY_NAME: None})

    assert text_driver(driver, BY_NAME) is None


def test_text_is_empty_string_for_a_text_interface_with_no_characters():
    driver = driver_with(text={BY_NAME: ""})

    assert text_driver(driver, BY_NAME) == ""


def test_text_reports_a_locator_that_no_longer_resolves():
    driver = driver_with(locators={}, text={})

    with pytest.raises(PyjabUnavailable) as caught:
        text_driver(driver, BY_NAME)

    assert BY_NAME in str(caught.value)
    assert driver.outstanding == 0


def test_text_gives_the_reference_back_even_when_the_read_raises():
    driver = driver_with(text={BY_NAME: "x"}, explode={BY_NAME})

    with pytest.raises(RuntimeError):
        text_driver(driver, BY_NAME)

    assert driver.outstanding == 0


# -- locator candidates -------------------------------------------------------------


def test_candidates_are_ordered_by_what_survives_a_change():
    candidates = locator_candidates(Record.from_pyjab(BUTTON))

    assert candidates == (
        "//push button[@name='OK']",
        "//push button[@name='OK'][@indexinparent=1]",
    ), (
        "a name is stable and an index is not, so the name-only locator comes first and the "
        "index only refines it; a bare index is no fallback for a named element, because a "
        "changed name fails verification anyway"
    )


def test_candidates_use_the_index_when_there_is_no_name():
    candidates = locator_candidates(Record.from_pyjab(record("panel", "", index_in_parent=3)))

    assert candidates == ("//panel[@indexinparent=3]",)


def test_candidates_use_the_description_when_the_name_is_empty():
    labelled = record("text", "")
    labelled["description"] = "User name"
    candidates = locator_candidates(Record.from_pyjab(labelled))

    assert candidates == (
        "//text[@description='User name']",
        "//text[@description='User name'][@indexinparent=0]",
    )


def test_candidates_fall_back_to_the_bare_role():
    anonymous = record("panel", "", index_in_parent=-1)
    candidates = locator_candidates(Record.from_pyjab(anonymous))

    assert candidates == ("//panel",)


def test_a_name_holding_both_quote_characters_cannot_be_a_literal():
    """Better to say so than to emit something that parses as a different locator."""
    tricky = record("push button", "it's \"quoted\"")
    candidates = locator_candidates(Record.from_pyjab(tricky))

    assert candidates == ("//push button[@indexinparent=0]",), (
        "the name candidates are skipped when they cannot be quoted, and the index remains"
    )


def test_a_position_locator_is_preferred_over_one_that_embeds_the_name():
    """A name locator stops resolving when the content changes -- which for a text field is
    exactly what java_set_text does, so the handle could not read back what it wrote."""
    field = record("text", "1122233455", index_in_parent=0)
    driver = FakeDriver(
        items=[(0, field)],
        search={("name", "1122233455"): [field]},
        locators={"//text[@name='1122233455']": field},
    )

    found = find_driver(driver, by="name", value="1122233455")

    assert found[0][1] == "//text[@indexinparent=0]", (
        "the walk knows where the element sits; the name is content and will change"
    )


def test_a_walk_that_cannot_place_the_element_falls_back_to_a_name_locator():
    button = record("push button", "OK", index_in_parent=1)
    driver = FakeDriver(
        items=[],                                    # the walk sees nothing
        search={("name", "OK"): [button]},
        locators={"//push button[@name='OK']": button},
    )

    found = find_driver(driver, by="name", value="OK")

    assert found[0][1] == "//push button[@name='OK']"


def test_a_search_result_that_neither_walk_nor_candidate_can_find_gets_no_handle():
    driver = FakeDriver(
        items=[],
        search={("name", "OK"): [record("push button", "OK", index_in_parent=1)]},
        locators={},
    )

    found = find_driver(driver, by="name", value="OK")

    assert found[0][1] is None
    assert driver.outstanding == 0
