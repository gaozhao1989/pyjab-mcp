"""pyjab-mcp — drive Java desktop applications from an AI agent.

The package's own modules must stay importable on any platform. ``pyjab`` raises
``ImportError`` at module scope off Windows, deliberately, so that a user gets a sentence
rather than a traceback -- and that means **nothing here may import pyjab at module scope
either**, or this package becomes uninstallable-in-practice everywhere else, including on
the machines that only ever run the test suite.

Import it inside functions. ``pyjab/inspector.py`` in the parent project does the same and
says why.

``0.1.0.dev0`` is M1: the bridge thread, diagnostics, and the session tools. It is a dev
version because M1 cannot yet list a window on released pyjab -- see ``AGENTS.md`` §6.
"""

__version__ = "0.1.1"

__all__ = ["__version__"]
