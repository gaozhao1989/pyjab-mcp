"""pyjab-mcp — drive Java desktop applications from an AI agent.

The package's own modules must stay importable on any platform. ``pyjab`` raises
``ImportError`` at module scope off Windows, deliberately, so that a user gets a sentence
rather than a traceback -- and that means **nothing here may import pyjab at module scope
either**, or this package becomes uninstallable-in-practice everywhere else, including on
the machines that only ever run the test suite.

Import it inside functions. ``pyjab/inspector.py`` in the parent project does the same and
says why.

``0.1.1`` is the current release: 15 tools that read and drive a Java desktop application
through the JVM's own accessibility tree. See the README for what they are and what the
substrate cannot reach.
"""

__version__ = "0.1.2"

__all__ = ["__version__"]
