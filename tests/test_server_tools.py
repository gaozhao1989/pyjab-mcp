"""L2-shaped tests for the MCP surface, runnable anywhere.

``tools/list`` and the failure text are exactly what a client sees, and they are the parts
that can be wrong on a machine with no Windows at all: a tool that raises something the SDK
treats as a crash, or a gap message that does not say what to do next, is a bug here rather
than there.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from pyjab_mcp.backend import PyjabApiGap, PyjabUnavailable, predates_the_pin
from pyjab_mcp.server import _tool_error, ServerState, create_server
from pyjab_mcp.backend import ElementChanged, TableReading
from pyjab_mcp.tree import Record
from tests._fakebackend import FakeBackend, FakeFactory, window
from tests._fakedriver import record

# Every test here drives the async server surface.
pytestmark = pytest.mark.asyncio

TOOL_ORDER = [
    "java_diagnostics",
    "java_list_windows",
    "java_attach_window",
    "java_snapshot",
    "java_find",
    "java_get_element",
    "java_get_text",
    "java_get_table",
    "java_click",
    "java_set_text",
    "java_select",
    "java_fill_form",
    "java_send_keys",
    "java_focus_window",
    "java_wait_for",
    "java_screenshot",
]


def make_server(backend=None, *, error=None):
    state = ServerState(open_backend=FakeFactory(backend or FakeBackend(), error=error))
    return create_server(state=state), state


async def text_of(result) -> str:
    return "".join(part.text for part in result.content if getattr(part, "text", None))


async def test_tools_list_is_deterministic_and_annotated():
    server, _state = make_server()

    tools = await server.list_tools()

    assert [tool.name for tool in tools] == TOOL_ORDER, (
        "tools/list order is part of the contract: a stable order keeps client-side prompt "
        "caching useful, and a test is the only thing that keeps it stable"
    )
    by_name = {tool.name: tool for tool in tools}
    assert by_name["java_diagnostics"].annotations.read_only_hint is True
    assert by_name["java_list_windows"].annotations.read_only_hint is True
    # attach_window creates server-side session state, so it is deliberately not read-only;
    # the plan's tool table leaves it unannotated.
    assert by_name["java_attach_window"].annotations.read_only_hint is None
    for tool in tools:
        assert tool.description and len(tool.description) > 60, (
            "a tool description is what the model chooses with; an empty one is a bug"
        )


async def test_diagnostics_reports_the_environment_without_touching_it():
    server, _state = make_server()

    result = await server.call_tool("java_diagnostics", {})

    assert result.is_error is False
    text = await text_of(result)
    assert "pyjab-mcp environment diagnostics" in text
    assert "platform" in text
    assert "pyjab" in text


async def test_diagnostics_load_bridge_reports_a_broken_environment_as_a_finding():
    server, _state = make_server(error=PyjabUnavailable("this is not Windows"))

    result = await server.call_tool("java_diagnostics", {"load_bridge": True})

    assert result.is_error is False, "an unusable environment is what this tool reports"
    text = await text_of(result)
    assert "this is not Windows" in text
    assert "UNAVAILABLE" in text


async def test_list_windows_renders_what_the_backend_returns():
    backend = FakeBackend(windows=[window(0x10001, "Alpha"), window(0x10002, "Beta", None)])
    server, _state = make_server(backend)

    result = await server.call_tool("java_list_windows", {})

    assert result.is_error is False
    text = await text_of(result)
    assert "Alpha" in text and "Beta" in text
    assert "hwnd=65537" in text


async def test_list_windows_with_no_windows_is_a_measurement_not_an_empty_string():
    server, _state = make_server(FakeBackend(windows=[]))

    result = await server.call_tool("java_list_windows", {})

    assert result.is_error is False
    assert "No Java windows are visible" in await text_of(result)


async def test_a_broken_bridge_is_an_anticipated_failure_with_a_next_step():
    server, _state = make_server(error=PyjabUnavailable("this is not Windows"))

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_list_windows", {})

    assert not isinstance(caught.value, UnexpectedToolError), (
        "an environment problem must not be reported to the model as a crash"
    )
    assert "this is not Windows" in str(caught.value)
    assert "java_diagnostics" in str(caught.value)


async def test_the_missing_pyjab_api_reaches_the_model_as_the_release_it_needs():
    class GapBackend(FakeBackend):
        def java_windows(self):
            raise PyjabApiGap(predates_the_pin("send_keys"))

    server, _state = make_server(GapBackend())

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_list_windows", {})

    message = str(caught.value)
    assert "1.11.0" in message, "the release that has the API is what the model can act on"
    assert "pip install -U pyjab" in message


async def test_attach_window_binds_through_the_backend_and_returns_a_session_id():
    backend = FakeBackend()
    server, state = make_server(backend)

    result = await server.call_tool("java_attach_window", {"title": "Alpha"})

    assert result.is_error is False
    text = await text_of(result)
    assert "s:" in text and "Alpha" in text
    assert "java_snapshot" in text, "the reply should say what to do next"
    assert backend.attach_calls == 1
    assert len(state.sessions) == 1


async def test_attach_window_passes_the_hwnd_through():
    backend = FakeBackend()
    server, state = make_server(backend)

    await server.call_tool("java_attach_window", {"hwnd": 0x10002, "title": "Alpha"})

    assert next(iter(state.sessions.values())).hwnd == 0x10002


async def test_attaching_the_same_window_twice_leaves_the_first_session_working(monkeypatch):
    """#71 at the surface the caller sees: attach twice, then use the **first** session.

    This is the trap the L3 chord test walked into by accident. A second
    ``java_attach_window`` for the same window used to release the earlier binding *and* issue
    a new window id, while session A still named the old one -- so every later call on session
    A answered ``window 'w1' is not attached`` and the caller had done nothing wrong. The
    backend now reuses the id, so session A keeps working.

    The real backend is driven here, with ``pyjab.jabdriver`` stubbed, because the fake
    backend's ``attach`` never releases anything and so cannot show the trap at all.
    """
    from pyjab_mcp.backend import PyjabBackend
    from tests._fakedriver import install_fake_jabdriver

    install_fake_jabdriver(monkeypatch)
    # The real pump imports pyjab's Windows modules, which do not exist here, and the pump is
    # not what this test is about. The bridge is stopped below so the stub is not pulled out
    # from under a thread that is still running.
    monkeypatch.setattr(PyjabBackend, "pump", lambda _self: None)
    state = ServerState(open_backend=PyjabBackend)
    server = create_server(state=state)

    try:
        first = await server.call_tool("java_attach_window", {"hwnd": 0x10001})
        first_session = (await text_of(first)).split(" ", 1)[0]
        second = await server.call_tool("java_attach_window", {"hwnd": 0x10001})
        second_session = (await text_of(second)).split(" ", 1)[0]

        assert first_session != second_session, "each attach is still its own session"
        assert state.session(first_session).window_id == state.session(second_session).window_id, (
            "both sessions have to name the same window id -- that reuse is the fix"
        )

        result = await server.call_tool("java_snapshot", {"session_id": first_session})

        assert result.is_error is False, (
            "the first session named a window that is still attached, so its calls must work"
        )
        text = await text_of(result)
        assert "push button" in text and "[e:" in text, text
    finally:
        await state.stop()


async def test_attach_window_reports_a_window_that_cannot_be_bound():
    backend = FakeBackend(attach_error=PyjabUnavailable("no window titled 'Nope' appeared"))
    server, _state = make_server(backend)

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_attach_window", {"title": "Nope"})

    assert "no window titled 'Nope' appeared" in str(caught.value)
    assert "java_diagnostics" in str(caught.value)


async def test_snapshot_renders_the_tree_with_handles_and_a_token_count():
    backend = FakeBackend(items=[
        (0, record("panel", "Form", index_in_parent=0, children_count=1)),
        (1, record("push button", "OK", index_in_parent=0)),
    ])
    server, state = make_server(backend)
    attached = await server.call_tool("java_attach_window", {"title": "Alpha"})
    session_id = (await text_of(attached)).split(" ", 1)[0]

    result = await server.call_tool("java_snapshot", {"session_id": session_id})

    assert result.is_error is False
    text = await text_of(result)
    assert "push button" in text
    assert "[e:" in text
    assert "tokens" in text
    assert len(state.handles) >= 2, "rendered elements should have handles"


async def test_snapshot_offers_handles_a_later_call_can_use():
    backend = FakeBackend(items=[(0, record("push button", "OK", index_in_parent=3))])
    server, state = make_server(backend)
    attached = await server.call_tool("java_attach_window", {"title": "Alpha"})
    session_id = (await text_of(attached)).split(" ", 1)[0]
    await server.call_tool("java_snapshot", {"session_id": session_id})

    entry = next(
        state.handles.get(handle) for handle in state.handles.handles_for(session_id)
    )

    assert entry.locator == "//push button[@indexinparent=3]"
    assert entry.expected.name == "OK"


async def test_snapshot_says_when_it_truncated():
    backend = FakeBackend(items=[
        (0, record("push button", f"B{index}", index_in_parent=index))
        for index in range(10)
    ])
    server, _state = make_server(backend)
    attached = await server.call_tool("java_attach_window", {"title": "Alpha"})
    session_id = (await text_of(attached)).split(" ", 1)[0]

    result = await server.call_tool(
        "java_snapshot", {"session_id": session_id, "max_nodes": 2}
    )

    text = await text_of(result)
    assert "truncated" in text
    assert "node budget" in text


async def test_snapshot_rejects_an_unknown_session_and_lists_the_live_ones():
    server, _state = make_server(FakeBackend())

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_snapshot", {"session_id": "s:beef"})

    message = str(caught.value)
    assert "s:beef" in message
    assert "java_attach_window" in message


async def test_snapshot_rejects_an_unknown_field():
    backend = FakeBackend(items=[(0, record("push button", "OK"))])
    server, _state = make_server(backend)
    attached = await server.call_tool("java_attach_window", {"title": "Alpha"})
    session_id = (await text_of(attached)).split(" ", 1)[0]

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_snapshot", {"session_id": session_id, "fields": ["colour"]}
        )

    assert "unknown field" in str(caught.value)


# ---------------------------------------------------------------------------------
# M3: the read tools
# ---------------------------------------------------------------------------------

BUTTON = Record.from_pyjab(record("push button", "OK", index_in_parent=1))
BUTTON_LOCATOR = "//push button[@name='OK']"


async def attached_server(**kwargs):
    """A server with one attached window and whatever the backend was told to answer."""
    backend = FakeBackend(**kwargs)
    server, state = make_server(backend)
    attached = await server.call_tool("java_attach_window", {"title": "Alpha"})
    session_id = (await text_of(attached)).split(" ", 1)[0]
    return server, state, backend, session_id


async def test_find_returns_a_handle_per_match():
    server, state, backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
    )

    result = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})

    assert result.is_error is False
    text = await text_of(result)
    assert "[e:" in text and BUTTON_LOCATOR in text
    assert backend.find_calls == 1
    assert len(state.handles) == 1


async def test_find_reports_a_measurement_when_nothing_matches():
    server, _state, _backend, session_id = await attached_server(matches={})

    result = await server.call_tool(
        "java_find", {"session_id": session_id, "role": "push button"}
    )

    text = await text_of(result)
    assert "Nothing in window" in text
    assert "contains(" in text, "an empty result should say how to widen the search"


async def test_find_requires_exactly_one_selector():
    server, _state, _backend, session_id = await attached_server()

    with pytest.raises(ToolError) as none_given:
        await server.call_tool("java_find", {"session_id": session_id})
    assert "exactly one selector" in str(none_given.value)

    with pytest.raises(ToolError) as two_given:
        await server.call_tool(
            "java_find", {"session_id": session_id, "name": "OK", "role": "push button"}
        )
    assert "exactly one selector" in str(two_given.value)


async def test_find_lists_a_match_it_cannot_address_without_a_handle():
    server, state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, None)]},
    )

    result = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})

    text = await text_of(result)
    assert "(no handle)" in text
    assert "no locator found them again reliably" in text
    assert len(state.handles) == 0


async def test_get_element_renders_the_record_and_says_it_is_unchanged():
    server, _state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "OK", index_in_parent=1)},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]

    result = await server.call_tool(
        "java_get_element", {"session_id": session_id, "handle": handle}
    )

    assert result.is_error is False
    text = await text_of(result)
    assert 'push button "OK"' in text
    assert "verified:    unchanged" in text
    assert BUTTON_LOCATOR in text


async def test_get_element_refuses_an_element_that_changed():
    """D2's whole point: the difference is reported instead of acted on."""
    server, _state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "Cancel", index_in_parent=1)},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_get_element", {"session_id": session_id, "handle": handle}
        )

    assert "name was 'OK', now 'Cancel'" in str(caught.value)
    assert "fresh snapshot" in str(caught.value)


async def test_an_unknown_handle_says_how_to_get_one():
    server, _state, _backend, session_id = await attached_server()

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_get_element", {"session_id": session_id, "handle": "e:ffff"}
        )

    assert "unknown handle" in str(caught.value)
    assert "java_snapshot" in str(caught.value)


async def test_a_handle_from_another_session_is_refused():
    server, state, _backend, session_id = await attached_server(
        locators={BUTTON_LOCATOR: record("push button", "OK")},
    )
    other = state.new_session(next(iter(state.sessions.values())))
    handle = state.handles.add(
        session_id=other, window_id="w1", locator=BUTTON_LOCATOR,
        expected=Record.from_pyjab(record("push button", "OK")),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_get_text", {"session_id": session_id, "handle": handle}
        )

    assert "belongs to session" in str(caught.value)


async def test_get_text_returns_the_text_and_its_length():
    server, _state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "OK", index_in_parent=1)},
        texts={BUTTON_LOCATOR: "hello world"},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]

    result = await server.call_tool(
        "java_get_text", {"session_id": session_id, "handle": handle}
    )

    text = await text_of(result)
    assert "11 character(s)" in text
    assert "hello world" in text
    assert "cut" not in text


async def test_get_text_slices_and_says_it_cut():
    server, _state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "OK", index_in_parent=1)},
        texts={BUTTON_LOCATOR: "0123456789"},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]

    result = await server.call_tool(
        "java_get_text", {"session_id": session_id, "handle": handle, "start": 2, "length": 3}
    )

    text = await text_of(result)
    assert "showing 2..5" in text
    assert "234" in text
    assert "start=5" in text


async def test_get_text_distinguishes_no_text_interface_from_empty_text():
    server, _state, _backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "OK", index_in_parent=1)},
        texts={BUTTON_LOCATOR: None},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]

    result = await server.call_tool(
        "java_get_text", {"session_id": session_id, "handle": handle}
    )

    text = await text_of(result)
    assert "no text interface" in text
    assert "not the same as being empty" in text


# ---------------------------------------------------------------------------------
# M4: the table tool
# ---------------------------------------------------------------------------------

TABLE = Record.from_pyjab(record("table", "Sports table", index_in_parent=2))
TABLE_LOCATOR = "//table[@name='Sports table']"


async def table_server(reading: TableReading, *, locator_record=None):
    server, state, backend, session_id = await attached_server(
        matches={("role", "table"): [(TABLE, TABLE_LOCATOR)]},
        locators={TABLE_LOCATOR: locator_record or record("table", "Sports table",
                                                          index_in_parent=2)},
        table_readings={TABLE_LOCATOR: reading},
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "role": "table"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]
    return server, state, backend, session_id, handle


async def test_get_table_renders_the_counts_and_the_rows():
    reading = TableReading(
        row_count=200, column_count=3, row_start=0,
        rows=(("Alice", "Tennis", "5"), ("Bob", "Golf", "3")),
        truncated=True, reason="198 more row(s) after this page",
    )
    server, _state, backend, session_id, handle = await table_server(reading)

    result = await server.call_tool(
        "java_get_table", {"session_id": session_id, "handle": handle}
    )

    assert result.is_error is False
    text = await text_of(result)
    assert "200 row(s) x 3 column(s)" in text
    assert "Alice | Tennis | 5" in text
    assert "row 0" in text and "row 1" in text
    assert "truncated" in text and "row_start=2" in text
    assert backend.table_calls == 1


async def test_get_table_keeps_no_text_and_unreadable_cells_apart():
    reading = TableReading(
        row_count=2, column_count=2, row_start=0,
        rows=(("Alice", None), (None, "Golf")),
        unreadable=((1, 0),),
    )
    server, _state, _backend, session_id, handle = await table_server(reading)

    result = await server.call_tool(
        "java_get_table", {"session_id": session_id, "handle": handle}
    )

    text = await text_of(result)
    assert "row 0: Alice | -" in text, "a cell with no text interface is '-'"
    assert "row 1: ? | Golf" in text, "a cell that could not be read is '?'"
    assert "1 cell(s) could not be read" in text
    assert "different answer" in text


async def test_get_table_on_something_that_is_not_a_table_says_what_to_do():
    server, _state, _backend, session_id = await attached_server(
        matches={}, locators={"//panel": record("panel", "Main panel")},
    )
    state_handle = _state.handles.add(
        session_id=session_id, window_id="w1", locator="//panel",
        expected=Record.from_pyjab(record("panel", "Main panel")),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_get_table", {"session_id": session_id, "handle": state_handle}
        )

    assert "not a table" in str(caught.value)
    assert "java_snapshot" in str(caught.value)


async def test_get_table_pages_through_the_tool():
    reading = TableReading(row_count=5, column_count=1, row_start=3,
                           rows=(("d",), ("e",)))
    server, _state, backend, session_id, handle = await table_server(reading)

    result = await server.call_tool(
        "java_get_table",
        {"session_id": session_id, "handle": handle, "row_start": 3, "row_count": 2},
    )

    assert result.is_error is False
    assert "showing rows 3..4" in await text_of(result)
    assert backend.table_calls == 1


# ---------------------------------------------------------------------------------
# M5: the action tools
# ---------------------------------------------------------------------------------


async def acting_server(**kwargs):
    """A server with one attached window, one findable button and a handle for it."""
    server, state, backend, session_id = await attached_server(
        matches={("name", "OK"): [(BUTTON, BUTTON_LOCATOR)]},
        locators={BUTTON_LOCATOR: record("push button", "OK", index_in_parent=1)},
        **kwargs,
    )
    found = await server.call_tool("java_find", {"session_id": session_id, "name": "OK"})
    handle = (await text_of(found)).split("[", 1)[1].split("]", 1)[0]
    return server, state, backend, session_id, handle


@pytest.mark.parametrize("click,action", [
    ("left", "click"), ("double", "double_click"), ("right", "context_click"),
])
async def test_click_maps_the_three_kinds_onto_pyjabs_methods(click, action):
    server, _state, backend, session_id, handle = await acting_server()

    result = await server.call_tool(
        "java_click", {"session_id": session_id, "handle": handle, "click": click}
    )

    assert result.is_error is False
    assert backend.acts == [{
        "locator": BUTTON_LOCATOR, "action": action, "value": None,
        "simulate": False, "row": None, "column": None,
    }]
    assert "Re-run java_snapshot" in await text_of(result)


async def test_click_refuses_a_kind_that_is_not_one_of_the_three():
    server, _state, backend, session_id, handle = await acting_server()

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_click", {"session_id": session_id, "handle": handle, "click": "middle"}
        )

    assert "left, right, double" in str(caught.value) or "double, left, right" in str(caught.value)
    assert backend.acts == [], "a bad argument must not reach the application"


async def test_an_element_that_changed_stops_the_action_and_says_nothing_was_done():
    server, _state, backend, session_id, handle = await acting_server(
        act_error=ElementChanged("name was 'OK', now 'Cancel'"),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_click", {"session_id": session_id, "handle": handle}
        )

    assert "Nothing was done" in str(caught.value)
    assert "name was 'OK', now 'Cancel'" in str(caught.value)


async def test_set_text_types_the_value_and_says_how():
    server, _state, backend, session_id, handle = await acting_server()

    result = await server.call_tool(
        "java_set_text",
        {"session_id": session_id, "handle": handle, "value": "admin", "simulate": True},
    )

    assert result.is_error is False
    assert backend.acts[0]["action"] == "set_text"
    assert backend.acts[0]["value"] == "admin"
    assert backend.acts[0]["simulate"] is True
    assert "simulated keystrokes" in await text_of(result)
    assert "java_get_text" in await text_of(result)


async def test_select_chooses_the_shape_from_the_arguments():
    server, _state, backend, session_id, handle = await acting_server()

    await server.call_tool(
        "java_select", {"session_id": session_id, "handle": handle, "option": "Tennis"}
    )
    await server.call_tool(
        "java_select", {"session_id": session_id, "handle": handle, "row": 2}
    )
    await server.call_tool(
        "java_select", {"session_id": session_id, "handle": handle, "row": 2, "column": 1}
    )

    assert [act["action"] for act in backend.acts] == ["select", "select_row", "select_cell"]
    assert backend.acts[0]["value"] == "Tennis"
    assert backend.acts[2]["row"] == 2 and backend.acts[2]["column"] == 1


async def test_select_needs_an_option_or_a_row():
    server, _state, backend, session_id, handle = await acting_server()

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_select", {"session_id": session_id, "handle": handle}
        )

    assert "option=<text>" in str(caught.value)
    assert backend.acts == []


async def test_fill_form_attempts_every_field_and_reports_each_one():
    """A half-filled form with no report is worse than one with a report."""
    server, _state, backend, session_id, handle = await acting_server()

    result = await server.call_tool("java_fill_form", {
        "session_id": session_id,
        "fields": [
            {"handle": handle, "value": "admin"},
            {"handle": "e:ffff", "value": "never"},
            {"value": "no handle at all"},
        ],
    })

    assert result.is_error is False, "a field that failed is a result, not a failed call"
    text = await text_of(result)
    assert "1 of 3 field(s) set" in text
    assert "unknown handle 'e:ffff'" in text
    assert "needs 'handle' and 'value'" in text
    assert [act["value"] for act in backend.acts] == ["admin"]


async def test_fill_form_needs_at_least_one_field():
    server, _state, _backend, session_id, _handle = await acting_server()

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_fill_form", {"session_id": session_id, "fields": []})

    assert "at least one field" in str(caught.value)


async def test_send_keys_sends_the_chord_when_pyjab_provides_the_api():
    """The point of #15: the tool has to start working on the release that answers R4."""
    server, _state, backend, session_id, _handle = await acting_server()

    result = await server.call_tool(
        "java_send_keys", {"session_id": session_id, "keys": "alt+y"}
    )

    assert result.is_error is False
    assert backend.keys_sent == ["alt+y"]
    text = await text_of(result)
    assert "java_focus_window" in text, (
        "the focus semantics are the part a caller gets wrong, so the reply points at the tool "
        "that can settle them rather than leaving the reader with a workaround that used to be "
        "the only route"
    )


async def test_send_keys_answers_with_the_release_when_the_api_is_absent():
    """An older pyjab: the probe finds nothing and the release it needs is the answer."""
    server, _state, _backend, session_id, _handle = await acting_server(
        keys_error=PyjabApiGap(predates_the_pin("send_keys")),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_send_keys", {"session_id": session_id, "keys": "alt+y"}
        )

    message = str(caught.value)
    assert "1.11.0" in message, "the release that has the API, not just 'missing'"
    assert "pip install -U pyjab" in message
    assert "java_set_text" in message, "it should also say what to do instead"


async def test_a_chord_the_installed_pyjab_does_not_recognise_is_an_input_error():
    """pyjab raises ValueError rather than sending nothing, and the reply says how to write one."""
    server, _state, backend, session_id, _handle = await acting_server(
        keys_error=ValueError("unknown key name(s) ['banana'] in 'alt+banana'"),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool(
            "java_send_keys", {"session_id": session_id, "keys": "alt+banana"}
        )

    message = str(caught.value)
    assert "banana" in message, "the name it did not recognise is the useful part"
    assert "separated by '+'" in message, "and how to write a chord instead"
    assert "java_diagnostics" not in message, (
        "this is an argument problem, not an environment one, so the environment hint would "
        "send the reader the wrong way"
    )


async def test_focus_reports_that_the_window_came_forward():
    """The return value is the tool's whole point, so the first answer is the one it returns."""
    server, state, backend, session_id, _handle = await acting_server(focus_result=True)

    result = await server.call_tool("java_focus_window", {"session_id": session_id})

    assert result.is_error is False
    assert backend.focused == [state.session(session_id).window_id]
    text = await text_of(result)
    assert "is now the foreground window" in text, text


async def test_focus_reports_that_the_window_did_not_come_forward():
    """`False` is a result, not a failed call -- Windows refuses foreground from a service.

    Reporting it as an error would be wrong twice over: nothing failed, and a model that reads
    `is_error` would retry something no amount of retrying changes.
    """
    server, _state, _backend, session_id, _handle = await acting_server(focus_result=False)

    result = await server.call_tool("java_focus_window", {"session_id": session_id})

    assert result.is_error is False, "the window not coming forward is an answer, not a failure"
    text = await text_of(result)
    assert "did NOT" in text and "not the foreground window" in text, text
    assert "normal answer" in text, "and why it is normal, so the reader does not retry"
    assert "java_send_keys" in text, "and what it means for the chord that needs the focus"


async def test_focus_does_not_file_an_unrecognised_answer_under_false():
    """AGENTS §2.4: "I could not tell" is not the same measurement as "it did not come forward"."""
    server, _state, _backend, session_id, _handle = await acting_server(focus_result=None)

    result = await server.call_tool("java_focus_window", {"session_id": session_id})

    assert result.is_error is False
    text = await text_of(result)
    assert "neither True nor False" in text, text
    assert "cannot say" in text, text
    assert "did NOT" not in text, (
        "an answer that is not a bool must not be reported as the window refusing to come "
        "forward -- that is the collapse this project's measurement rule exists to stop"
    )


async def test_focus_answers_with_the_release_when_the_api_is_absent():
    """An older pyjab: the probe finds nothing, and the release it needs is the answer.

    No workaround, deliberately: the whole point of the requirement record is that a missing
    API is a reported state rather than a silent substitute.
    """
    server, _state, _backend, session_id, _handle = await acting_server(
        focus_error=PyjabApiGap(predates_the_pin("bring_to_foreground")),
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_focus_window", {"session_id": session_id})

    message = str(caught.value)
    assert "1.11.0" in message, "the release that has the API, not just 'missing'"
    assert "pip install -U pyjab" in message
    assert "focus" in message, "and the name of what is missing"


async def test_focus_needs_a_known_session():
    server, _state, _backend, _session_id, _handle = await acting_server()

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_focus_window", {"session_id": "s:nope"})

    assert "unknown session" in str(caught.value)


async def test_an_unexpected_failure_still_tells_the_model_what_to_do():
    """The invariant every tool error has to keep, including the ones nobody predicted.

    It was not hypothetical: moving the window listing onto pyjab 1.10.0's public API let a
    FileNotFoundError for the bridge DLL (Windows, no JDK) reach the model with no next step,
    and the L2 test that asks every JAB tool for one caught it. This is the L1 half.
    """
    error = _tool_error(FileNotFoundError("WindowsAccessBridge-64.dll could not be located"))

    assert "FileNotFoundError" in str(error), "the original failure is not hidden"
    assert "java_diagnostics" in str(error), "and the model is told where to look next"


async def test_screenshot_returns_an_image_block_and_nothing_else():
    """Found by accident, and worth a test: the fake backend had no ``screenshot`` at all.

    Nothing noticed, because java_screenshot was only ever exercised at L3. A tool with no
    tool-level test is a tool whose plumbing nobody checks -- `call_backend("screenshot")`
    resolves the method by name, so a typo or a missing method is a runtime AttributeError.
    """
    server, _state, backend, session_id = await attached_server(
        matches={}, screenshots={None: b"\x89PNG\r\n\x1a\n a whole window"},
    )

    result = await server.call_tool("java_screenshot", {"session_id": session_id})

    assert result.is_error is False
    assert backend.screenshot_calls == 1
    types = [part.type for part in result.content]
    assert types == ["image"], f"the reply is the image and nothing else: {types}"
    import base64

    assert base64.b64decode(result.content[0].data)[:8] == b"\x89PNG\r\n\x1a\n"


async def test_a_screenshot_of_nothing_is_reported_rather_than_returned_empty():
    """An empty PNG would look like a black window; saying so is the honest answer."""
    server, _state, _backend, session_id = await attached_server(
        matches={}, screenshots={None: b""},
    )

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_screenshot", {"session_id": session_id})

    assert "came back empty" in str(caught.value)
    assert "java_get_element" in str(caught.value), "and where to look at what it does report"
