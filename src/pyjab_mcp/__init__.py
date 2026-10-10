"""pyjab-mcp — drive Java desktop applications from an AI agent.

The package's own modules must stay importable on any platform. ``pyjab`` raises
``ImportError`` at module scope off Windows, deliberately, so that a user gets a sentence
rather than a traceback -- and that means **nothing here may import pyjab at module scope
either**, or this package becomes uninstallable-in-practice everywhere else, including on
the machines that only ever run the test suite.

Import it inside functions. ``pyjab/inspector.py`` in the parent project does the same and
says why.
"""

__version__ = "0.0.0"

__all__ = ["__version__"]
