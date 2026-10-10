"""Console script entry point: ``pyjab-mcp`` runs the server over stdio.

**stdout belongs to the MCP stdio transport** -- it carries the JSON-RPC frames -- so
nothing here may print to it.  This server's own messages go through the protocol, and
local logging goes to stderr, where a client will show it without corrupting the stream.

The one exception is ``--help`` and ``--version``, which are for a person rather than a
client: they print to stdout and exit *before* the server starts, so there is no stream to
corrupt. Nothing else is accepted -- a client passes no arguments, and an argument nobody
reads is how "``--help`` prints nothing" happened.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from . import __version__
from .server import ServerState, create_server

logger = logging.getLogger("pyjab_mcp")

HELP = """\
pyjab-mcp {version} -- an MCP server that drives Java desktop applications.

It speaks the Model Context Protocol over stdio, so a client starts it:

    {{"mcpServers": {{"pyjab-mcp": {{"command": "pyjab-mcp"}}}}}}

See docs/CLIENTS.md for Claude Desktop, Claude Code, Cursor and VS Code.

Requires Windows, a JDK with Java Access Bridge enabled, and a Java application that is
already running -- this server attaches to applications, it never launches or restarts
them. Ask the java_diagnostics tool what is missing; it names the part of the environment
that is not in place rather than leaving a tool call to fail.

Options:
  -h, --help      show this message and exit
  -V, --version   show the version and exit

This is free software, and complete: no licence key, no quota, nothing held back. Bugs and
questions go to https://github.com/gaozhao1989/pyjab-mcp/issues -- include the output of
`pyjab-mcp --version` and of the java_diagnostics tool, which between them answer most
environmental questions before they are asked. Commercial and enterprise support (a restricted
network, an adaptation to one application, an SLA) is available: gaozhao89@qq.com.
"""


def main(argv: "list[str] | None" = None) -> int:
    """Run the MCP server on stdio until the client closes the connection."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        if arguments[0] in ("-h", "--help"):
            print(HELP.format(version=__version__))
            return 0
        if arguments[0] in ("-V", "--version"):
            print(f"pyjab-mcp {__version__}")
            return 0
        # Refusing is the point: silently ignoring arguments is how `--help` came to print
        # nothing and exit 0, which reads as a broken install.
        print(
            f"pyjab-mcp takes no arguments (got {arguments[0]!r}). It speaks MCP over stdio "
            "and is started by a client, not by hand:\n"
            '  {"mcpServers": {"pyjab-mcp": {"command": "pyjab-mcp"}}}\n'
            "Run `pyjab-mcp --help` for what it needs.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = ServerState()
    server = create_server(state=state)
    try:
        server.run("stdio")
    finally:
        if not asyncio.run(state.stop()):
            # Honest rather than clean: a thread blocked inside a cross-process call cannot
            # be cancelled, and the process is exiting anyway.
            logger.warning(
                "the bridge thread did not stop; it is blocked inside a backend call that "
                "cannot be cancelled"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
