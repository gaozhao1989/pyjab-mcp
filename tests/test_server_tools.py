"""L2-shaped tests for the MCP surface, runnable anywhere.

``tools/list`` and the failure text are exactly what a client sees, and they are the parts
that can be wrong on a machine with no Windows at all: a tool that raises something the SDK
treats as a crash, or a gap message that does not say what to do next, is a bug here rather
than there.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from pyjab_mcp.backend import PyjabApiGap, PyjabUnavailable, requirement
from pyjab_mcp.server import ServerState, create_server
from tests._fakebackend import FakeBackend, FakeFactory, window
from tests._fakedriver import record

# Every test here drives the async server surface.
pytestmark = pytest.mark.asyncio

TOOL_ORDER = [
    "java_diagnostics",
    "java_list_windows",
    "java_attach_window",
    "java_snapshot",
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


async def test_the_missing_pyjab_api_reaches_the_model_as_its_requirement_text():
    class GapBackend(FakeBackend):
        def java_windows(self):
            raise PyjabApiGap(requirement("list_java_windows"))

    server, _state = make_server(GapBackend())

    with pytest.raises(ToolError) as caught:
        await server.call_tool("java_list_windows", {})

    message = str(caught.value)
    assert "pyjab does not provide" in message
    assert "expected in:" in message
    assert "AGENTS.md" in message


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
