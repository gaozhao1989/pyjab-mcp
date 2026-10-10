"""L1 tests for the table read: paging, the cell budget, and three cell states.

A Java table is the thing this project must never return whole -- pyjab's own test application
has a 200-row one, a real one has thousands -- so paging is not a nicety here, it is the
feature. The first smoke test of this function asked for one row and got the whole table,
because the page size and the table's height shared a name; the tests below are what caught
it and what keeps it caught.

The three cell states are the other half. ``""`` is a cell that is text and empty, ``None`` is
a cell with no Accessible Text interface, and a cell whose read *raised* is neither -- it is
recorded by position, so a reply cannot present a failed read as an empty cell.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import PyjabUnavailable, table_driver
from tests._fakedriver import FakeDriver, record

TABLE = "//table[@name='Sports table']"
GRID = [["Name", "Sport"], ["Alice", "Tennis"], ["Bob", "Golf"]]


def driver(table=GRID, **kwargs) -> FakeDriver:
    return FakeDriver(
        tables={TABLE: table} if table is not None else {"//panel": [[]]},
        locators={TABLE: record("table", "Sports table")},
        **kwargs,
    )


def test_a_page_is_exactly_as_big_as_it_was_asked_for():
    """The regression: asking for one row returned the table's whole height."""
    reading = table_driver(driver(), TABLE, row_start=0, row_count=1)

    assert reading.rows == (("Name", "Sport"),)
    assert reading.row_count == 3
    assert reading.column_count == 2
    assert reading.truncated is True
    assert "2 more row(s)" in reading.reason


def test_paging_advances_by_the_page_size():
    first = table_driver(driver(), TABLE, row_start=0, row_count=2)
    second = table_driver(driver(), TABLE, row_start=2, row_count=2)

    assert first.rows == (("Name", "Sport"), ("Alice", "Tennis"))
    assert second.rows == (("Bob", "Golf"),)
    assert second.truncated is False, "the last page is not a truncated one"


def test_a_page_starting_past_the_end_is_empty_rather_than_truncated():
    reading = table_driver(driver(), TABLE, row_start=99, row_count=5)

    assert reading.rows == ()
    assert reading.truncated is False
    assert reading.row_count == 3, "the counts are still the table's, not the page's"


def test_the_cell_budget_stops_a_wide_table_and_says_so():
    wide = [[f"c{row}-{column}" for column in range(10)] for row in range(50)]

    reading = table_driver(driver(wide), TABLE, row_start=0, row_count=50, max_cells=25)

    assert reading.truncated is True
    assert "cell budget (25 cells)" in reading.reason, reading.reason
    assert "2 row(s) of 10 column(s)" in reading.reason
    assert sum(len(row) for row in reading.rows) <= 25, (
        "the budget is in cells, so the row count has to be derived from it"
    )


def test_a_cell_that_cannot_be_read_is_recorded_by_position():
    target = "//table[@name='Sports table']"
    broken = driver(cells_that_raise={(target, 1, 1)})

    reading = table_driver(broken, TABLE, row_start=0, row_count=3)

    assert reading.unreadable == ((1, 1),), (
        "a failed read is neither an empty string nor a missing text interface; it has to be "
        "identifiable, or the reply reports a hole as a value"
    )
    assert reading.rows[1][1] is None
    assert reading.rows[1][0] == "Alice", "the rest of the row is still read"


def test_a_cell_with_no_text_interface_falls_back_to_its_name_and_says_so():
    """Swing puts a table cell's value in its accessible name; a real table has no text."""
    target = "//table[@name='Sports table']"
    wordless = driver(cells_that_are_empty={(target, 2, 0)})

    reading = table_driver(wordless, TABLE, row_start=0, row_count=3)

    assert reading.unreadable == (), "a name-derived value was read successfully"
    assert reading.from_name == ((2, 0),), (
        "the reply has to be able to say where a value came from, or a name is presented as "
        "text"
    )
    assert reading.rows[2][0] == "cell 2,0", "the fake names its cells after their position"
    assert reading.rows[2][1] == "Golf"


def test_every_cell_is_given_back_including_the_ones_that_raise():
    target = "//table[@name='Sports table']"
    broken = driver(cells_that_raise={(target, 0, 0), (target, 2, 1)})

    table_driver(broken, TABLE, row_start=0, row_count=3)

    assert broken.cells_handed_out == 4, "six cells minus the two whose read raised"
    assert broken.outstanding == 0, (
        "a 200-row table read without releasing its cells leaves 200 Java objects behind"
    )


def test_a_handle_that_is_not_a_table_says_what_it_is_instead():
    # Not in `tables` at all: pyjab's `table` property returns None for a non-table role.
    panel = FakeDriver(locators={"//panel": record("panel", "Main panel")})

    with pytest.raises(PyjabUnavailable) as caught:
        table_driver(panel, "//panel")

    assert "not a table" in str(caught.value)
    assert "java_snapshot" in str(caught.value)


def test_a_table_that_does_not_report_its_shape_is_reported_not_guessed():
    class Wordless(FakeDriver):
        def table_info(self, path):
            return {"rows": 3}

    driver_with_bad_info = Wordless(locators={TABLE: record("table", "T")})

    with pytest.raises(PyjabUnavailable) as caught:
        table_driver(driver_with_bad_info, TABLE)

    assert "did not report its shape" in str(caught.value)
    assert driver_with_bad_info.outstanding == 0


def test_an_empty_table_reads_as_empty_rather_than_failing():
    reading = table_driver(driver([[]]), TABLE, row_start=0, row_count=5)

    assert reading.row_count == 1
    assert reading.column_count == 0
    assert reading.rows == ((),)
    assert reading.unreadable == ()
