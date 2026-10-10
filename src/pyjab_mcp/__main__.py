"""Console script entry point: ``pyjab-mcp`` runs the server over stdio.

**stdout belongs to the MCP stdio transport** -- it carries the JSON-RPC frames -- so
nothing here may print to it.  This server's own messages go through the protocol, and
local logging goes to stderr, where a client will show it without corrupting the stream.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .server import ServerState, create_server

logger = logging.getLogger("pyjab_mcp")


def main() -> None:
    """Run the MCP server on stdio until the client closes the connection."""
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


if __name__ == "__main__":
    main()
