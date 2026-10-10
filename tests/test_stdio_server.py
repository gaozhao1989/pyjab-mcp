"""L2: the real console script, spoken to over the real stdio transport.

Everything else in this suite calls the server in-process. This one starts
``python -m pyjab_mcp`` as a subprocess and does the MCP handshake, which is the only way
to catch the failures that live in the seam: a module that cannot be executed, a console
script that points nowhere, a crash before the first request, or anything printed to
**stdout** -- which belongs to the transport, and a stray ``print`` corrupts it.

It still proves nothing about a Java application.  That is L3 and only L3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.asyncio


async def test_the_console_script_answers_a_real_mcp_handshake():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pyjab_mcp"], cwd=REPO_ROOT
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "pyjab_mcp"

            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == [
                "java_diagnostics",
                "java_list_windows",
                "java_attach_window",
            ]

            # A tool call over the wire, not just tools/list: this is the end of the
            # environment problem being reported as data rather than as a crash.
            result = await session.call_tool("java_diagnostics", {})
            assert result.is_error is False
            assert "pyjab-mcp environment diagnostics" in result.content[0].text


async def test_a_jab_tool_fails_with_a_next_step_instead_of_a_crash():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pyjab_mcp"], cwd=REPO_ROOT
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("java_list_windows", {})

            assert result.is_error is True
            text = result.content[0].text
            # Which next step depends on the machine: off Windows the bridge cannot open,
            # and on Windows released pyjab has no public window listing yet. Both have to
            # end in something the model can act on rather than a bare failure.
            assert ("Run java_diagnostics" in text) or ("expected in:" in text), (
                "an error a model cannot act on is only marginally better than a crash"
            )
            assert "Error executing tool java_list_windows" in text
