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


# ---------------------------------------------------------------------------------
# The command line, which is for a person rather than a client
# ---------------------------------------------------------------------------------


def run_cli(*arguments: str):
    """The console script as a user gets it: a subprocess, with stdout and stderr apart."""
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "pyjab_mcp", *arguments],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )


def test_version_prints_the_version_and_exits_cleanly():
    """The first line of every useful bug report, and it did not exist before 0.1.2."""
    import pyjab_mcp

    result = run_cli("--version")

    assert result.returncode == 0
    assert pyjab_mcp.__version__ in result.stdout
    assert result.stdout.startswith("pyjab-mcp ")


def test_help_says_what_it_is_and_what_it_needs():
    result = run_cli("--help")

    assert result.returncode == 0
    # The three things a person needs before anything can work.
    assert "stdio" in result.stdout
    assert "Java Access Bridge" in result.stdout
    assert "mcpServers" in result.stdout, "and how a client is supposed to start it"


def test_help_says_where_to_get_help():
    """The support line lives where a person reads, which is `--help`, not the diagnostics report.

    The plan asked for it in `java_diagnostics`'s output (F1). That output is structured findings
    an agent reads; the address belongs where a human is looking, and the decision is pinned here
    in both directions so it cannot drift back by accident.
    """
    result = run_cli("--help")

    assert result.returncode == 0
    assert "issues" in result.stdout and "github.com" in result.stdout
    assert "gaozhao89@qq.com" in result.stdout, "the commercial address is part of it"
    assert "no licence key" in result.stdout, (
        "and the line says the free version is complete, which is the part a user wonders about"
    )


def test_an_argument_nobody_reads_is_refused_rather_than_ignored():
    """`--help` used to print nothing and exit 0, which reads as a broken install."""
    result = run_cli("--serve")

    assert result.returncode == 2
    assert "--serve" in result.stderr
    assert "takes no arguments" in result.stderr
    assert result.stdout == "", "stdout belongs to the transport, even here"


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
