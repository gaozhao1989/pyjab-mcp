"""The MCPB bundle's entry point: find the vendored dependencies, then run the server.

This file is **bundle payload**. ``tools/build_mcpb.py`` copies it verbatim into
``server/entry.py`` inside the ``.mcpb``, and the manifest launches it with the ``python`` on
the user's ``PATH``. It is tracked in the repository rather than generated so that what the
bundle runs can be read and reviewed like any other source file.

Three things a bare ``sys.path.insert`` would get wrong, and what is done instead:

* **the dependencies are per interpreter.** ``pydantic-core``, ``cffi``, ``rpds-py``,
  ``cryptography`` and ``pywin32`` ship compiled extensions whose wheels are tagged for one
  CPython *minor* version, so the bundle vendors one tree per supported minor under
  ``vendor/cp3XX`` and this picks the tree that matches the interpreter that is running. The
  bundle therefore covers a *range* of interpreters and not "3.10 or newer", and an interpreter
  outside it is refused with a sentence naming what was found and what is carried -- because a
  bundle that put the wrong tree on the path would not fail with a sentence, it would fail
  inside a native import.
* **``pip install --target`` writes ``.pth`` files, and a plain ``sys.path`` entry does not
  process them.** ``pywin32`` needs its own: the ``.pth`` adds ``win32``, ``win32/lib`` and
  ``pythonwin`` to the path and imports ``pywin32_bootstrap``, which is what puts
  ``pywin32_system32`` on the DLL search path. ``site.addsitedir`` is what a real
  site-packages install does, so it is what is done here.
* **the vendored copy has to win.** A user may already have a different ``pyjab`` or ``mcp``
  installed; the bundle must still run the versions it shipped with, so the vendored tree goes
  to the *front* of ``sys.path`` after ``addsitedir`` has appended it.

Nothing here writes to stdout: stdout belongs to the MCP stdio transport, so every message
this file prints goes to stderr.
"""

from __future__ import annotations

import os
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Where the packer put the vendored dependency trees -- one directory per CPython minor.
VENDOR = os.path.join(HERE, "vendor")


def interpreter_tag(version_info=None) -> str:
    """``cp310`` for CPython 3.10 -- the tag a native wheel carries for one interpreter."""
    info = sys.version_info if version_info is None else version_info
    return "cp%d%d" % (info[0], info[1])


def vendor_name(version_info=None) -> str:
    """The vendored tree for *version_info*, relative to this script."""
    return os.path.join("vendor", interpreter_tag(version_info))


def vendor_dir(version_info=None) -> str:
    """The absolute path of the vendored tree for *version_info*."""
    info = sys.version_info if version_info is None else version_info
    return os.path.join(HERE, vendor_name(info))


def vendored_interpreters() -> "list[str]":
    """The ``cp3XX`` trees this bundle actually contains, for the message when one is missing."""
    try:
        names = os.listdir(VENDOR)
    except OSError:
        return []
    return sorted(
        name for name in names if os.path.isdir(os.path.join(VENDOR, name))
    )


def minor(tag: str) -> str:
    """``cp310`` -> ``3.10``: how a person writes the interpreter a tree is for.

    A CPython ABI tag is ``cp`` followed by the version digits without padding -- ``cp39`` for
    3.9, ``cp310`` for 3.10 -- so three digits mean a two-digit minor, and a two-digit minor
    never begins with a zero. A tag that does not fit those shapes is not something this project
    can have built, and it is reported as the tag it is rather than as a version it is not.
    """
    digits = tag[2:]
    if not digits.isdigit():
        return tag
    if len(digits) == 3 and digits[1] != "0":
        return "%s.%s" % (digits[0], digits[1:])
    if len(digits) == 2:
        return "%s.%s" % (digits[0], digits[1])
    return tag


def unsupported(present: "list[str]", running=None) -> str:
    """The sentence for an interpreter this bundle carries nothing for.

    It has to name three things, because the failure it replaces names none of them: an
    ImportError out of a native module reports a DLL. What the user needs is which interpreter
    was found, which ones the file does carry, and where to go instead.
    """
    info = sys.version_info if running is None else running
    return (
        "pyjab-mcp: this bundle carries dependencies for CPython %s (64-bit Windows) and "
        "nothing for CPython %d.%d.%d, which is what is running. Install the server with "
        "`pip install pyjab-mcp` instead, or run the bundle with one of those interpreters.\n"
        % (
            ", ".join(minor(tag) for tag in present) or "no interpreter at all",
            info[0], info[1], info[2],
        )
    )


def main(argv=None) -> int:
    """Put the vendored dependencies on ``sys.path`` and run the server."""
    path = vendor_dir()
    if not os.path.isdir(path):
        sys.stderr.write(unsupported(vendored_interpreters()))
        return 2
    if sys.maxsize <= 2 ** 32:
        # The vendored wheels are win_amd64; a 32-bit interpreter cannot load them, and the
        # failure inside a native import names a DLL rather than the mismatch.
        sys.stderr.write(
            "pyjab-mcp: this bundle vendors 64-bit Windows wheels and it is running on a "
            "32-bit Python. Use a 64-bit Python, or install the server with "
            "`pip install pyjab-mcp`.\n"
        )
        return 2

    site.addsitedir(path)
    # addsitedir appends, and it processes the .pth files without which pywin32 cannot load
    # its own DLLs. The vendored tree then has to come first, so it is removed and re-added
    # at the front rather than left where addsitedir put it.
    sys.path[:] = [entry for entry in sys.path if entry != path]
    sys.path.insert(0, path)

    from pyjab_mcp.__main__ import main as run_server

    return run_server(argv)


if __name__ == "__main__":
    raise SystemExit(main())
