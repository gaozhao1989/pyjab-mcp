#!/usr/bin/env python
"""Check that what the architecture page says and what the code does are the same thing.

Why this exists
---------------

Two claims in ``docs/ARCHITECTURE.md`` are the kind that drift silently, and each drifts in
the direction nobody notices:

* **the tool surface.** A tool documented as available but not registered is a promise to a
  user that fails at the first call; a tool registered but not documented is a capability
  nobody can find. M1 is a live example of the first: the session tools exist and are wired,
  and they cannot list a window until pyjab grows a public API — so the page has to say so,
  and this check is what keeps that sentence true.
* **the module map.** pyjab keeps the same rule for its own package and states the reason: a
  new module missing from the list is a signal rather than an oversight. A map that has
  stopped being maintained is worse than no map, because it reads as complete.

What it checks
--------------

* every module under ``src/pyjab_mcp/`` appears in the file table, and every file in the
  table exists;
* every tool the server registers is documented as shipping, and every tool documented as
  shipping is registered;
* every tool documented with a later milestone is **not** registered yet — otherwise the
  page is describing the future in the present tense.

The tool list is read from the running server rather than from the source text, because the
question is what a client would see in ``tools/list``.

Usage
-----

    python tools/check_documented_surface.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = REPO_ROOT / "docs" / "ARCHITECTURE.md"
PACKAGE = REPO_ROOT / "src" / "pyjab_mcp"

MODULE_HEADING = "## Where the code lives"
TOOL_HEADING = "## Tool surface"

#: The milestones whose tools must be registered right now.  Everything else in the tool
#: table is a plan, and a plan that is already registered is a documentation bug.
#:
#: This list moves as milestones land, and moving it is the deliberate act: it is what makes
#: a tool's presence in the code a claim the page has to agree with.
SHIPPED_MILESTONES = ("M1", "M2", "M3")

_BACKTICKED = re.compile(r"`([^`]+)`")


def _table_rows(text: str, heading: str) -> List[List[str]]:
    """The rows of the first markdown table after *heading*."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise SystemExit(f"{heading!r} not found in {ARCHITECTURE}")
    rows: List[List[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):    # the header rule
            continue
        rows.append(cells)
    return rows[1:] if rows else []                            # drop the header row


def documented_modules(architecture: Path = ARCHITECTURE) -> Set[str]:
    """Package-relative module paths named in the file table, e.g. ``server.py``."""
    rows = _table_rows(architecture.read_text(encoding="utf-8"), MODULE_HEADING)
    found: Set[str] = set()
    for row in rows:
        for candidate in _BACKTICKED.findall(row[0]):
            if candidate.startswith("src/pyjab_mcp/"):
                found.add(candidate.rsplit("/", 1)[-1])
    return found


def documented_tools(architecture: Path = ARCHITECTURE) -> "Mapping[str, str]":
    """``tool name -> ships`` from the tool table."""
    rows = _table_rows(architecture.read_text(encoding="utf-8"), TOOL_HEADING)
    found = {}
    for row in rows:
        if len(row) < 3:
            continue
        tools = [name for name in _BACKTICKED.findall(row[1]) if name.startswith("java_")]
        ships = row[2].split()[0] if row[2].split() else ""
        for name in tools:
            found[name] = ships
    return found


def registered_tools() -> List[str]:
    """The tools a client would see in ``tools/list``, in order."""
    try:
        from pyjab_mcp.server import create_server
    except Exception as exc:  # noqa: BLE001 - a clear message beats a traceback here
        raise SystemExit(
            f"could not import pyjab_mcp.server ({type(exc).__name__}: {exc}).\n"
            "Install the package first: pip install -e '.[dev]'"
        )
    server = create_server()
    return [tool.name for tool in asyncio.run(server.list_tools())]


def _label(path: Path, repo: Path = REPO_ROOT) -> str:
    """A short path for a message, even when the file is not inside the repository.

    The guard is tested against pages written to a temporary directory, so a bare
    ``relative_to`` would turn a working check into a ValueError.
    """
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def check(
    *,
    architecture: Path = ARCHITECTURE,
    package: Path = PACKAGE,
    registered: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return every disagreement between the page and the code."""
    registered = list(registered_tools() if registered is None else registered)
    problems: List[str] = []

    on_disk = {path.name for path in package.rglob("*.py") if "__pycache__" not in path.parts}
    in_page = documented_modules(architecture)
    for name in sorted(on_disk - in_page):
        problems.append(
            f"UNDOCUMENTED MODULE: src/pyjab_mcp/{name} is not in the file table of "
            f"{_label(architecture)}. Add it with one line saying what it "
            "is for -- pyjab's rule: a module missing from the list is a signal."
        )
    for name in sorted(in_page - on_disk):
        problems.append(
            f"STALE DOC: the file table names src/pyjab_mcp/{name}, which does not exist. "
            "A map that has stopped being maintained reads as complete."
        )

    documented = documented_tools(architecture)
    shipped = {name for name, ships in documented.items() if ships in SHIPPED_MILESTONES}
    planned = {name for name, ships in documented.items() if ships not in SHIPPED_MILESTONES}

    for name in sorted(set(registered) - shipped):
        if name in planned:
            problems.append(
                f"NOT YET: {name} is registered but documented as {documented[name]}, so "
                "the page describes the future in the present tense. Either mark it as "
                "shipping now or do not register it."
            )
        else:
            problems.append(
                f"UNDOCUMENTED TOOL: {name} is registered but does not appear in the tool "
                f"table of {_label(architecture)}."
            )
    for name in sorted(shipped - set(registered)):
        problems.append(
            f"MISSING TOOL: {name} is documented as shipping ({documented[name]}) but the "
            "server does not register it. A documented tool that is not there fails at the "
            "first call."
        )
    return problems


def main() -> int:
    registered = registered_tools()
    documented = documented_tools()
    problems = check(registered=registered)

    print(f"registered tools ({len(registered)}): {', '.join(registered)}")
    for name in sorted(documented):
        mark = "ship" if documented[name] in SHIPPED_MILESTONES else "plan"
        registered_mark = "registered" if name in registered else "not registered"
        print(f"  [{mark}] {name}  ({documented[name]}) -- {registered_mark}")
    print(
        f"\ndocumented modules: {len(documented_modules())} "
        f"(on disk: {len(list(PACKAGE.rglob('*.py')))})"
    )

    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        print()
        print("FAILED: docs/ARCHITECTURE.md and the code disagree.")
        return 1

    print("\nPASSED: the tool surface and the module map match the code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
