#!/usr/bin/env python
"""Whether pyjab has released what this project is waiting for.

Why this exists
---------------

M1's session tools are wired end to end and cannot list a window, because released pyjab
(1.8.0) has no public way to arm a bridge without binding an application window. That is a
waiting state, and waiting states rot in a specific way: the note saying "pyjab does not
have this yet" is true when written, nobody re-reads it, and one day it is false and nobody
knows.

So the wait is made mechanical. This script asks the installed pyjab the same questions the
code asks — through ``pyjab_mcp.backend.probe_public_api()``, so the report and
``java_diagnostics`` cannot disagree — and it exits non-zero when told to require something
that is not there:

    python tools/check_pyjab_readiness.py                    # report, always exit 0
    python tools/check_pyjab_readiness.py --require all      # fail while anything is missing
    python tools/check_pyjab_readiness.py --require walk     # what M2 will be gated on

``UNKNOWN`` counts as not ready on purpose: on a non-Windows machine the Windows-only
modules cannot be imported, and "I could not measure it" is not "it is there" (this
project's AGENTS.md §2.4).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:                    # a checkout, not an install
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pyjab_mcp.backend import (                               # noqa: E402
    AVAILABLE,
    MISSING,
    UNKNOWN,
    PyjabApiReport,
    probe_public_api,
    requirement,
)

#: What this project is waiting for: ``(probe key, fact name, requirement key)``.
#:
#: The requirement key indexes ``backend.REQUIREMENTS`` when there is one; the tree walk has
#: none, because it is not a design gap -- the code exists in the sibling checkout and needs
#: a release (AGENTS.md §6.2 R3).
WAITING = (
    ("list_windows", "public java-window listing", "list_java_windows"),
    ("detach", "non-destructive detach", "detach"),
    ("walk", "tree walk (JABElement.walk)", None),
)

WALK_NOTE = (
    "the tree walk exists in the sibling pyjab checkout (PR #156) and needs a release; "
    "M2's pin must be >= that release (AGENTS.md 6.2 R3)"
)

READY = "READY"
NOT_READY = "NOT READY"


def _one_line(detail: str) -> str:
    """pyjab's Windows-only ImportError is a multi-line sentence; a table wants one line."""
    lines = [line for line in detail.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0] + (" ..." if len(lines) > 1 else "")


def readiness(api: PyjabApiReport) -> "Dict[str, tuple]":
    """``probe key -> (verdict, detail)`` for each thing being waited on."""
    report = {}
    for key, fact_name, _requirement_key in WAITING:
        fact = api.fact(fact_name)
        if fact.status == AVAILABLE:
            report[key] = (READY, fact.detail or "available")
        elif fact.status == MISSING:
            report[key] = (NOT_READY, fact.detail or "missing")
        elif fact.status == UNKNOWN:
            report[key] = (NOT_READY, f"cannot be measured here: {fact.detail}")
        else:  # pragma: no cover - probe_public_api only produces the three above
            report[key] = (NOT_READY, fact.detail)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--require", default="",
        help="comma-separated keys, or 'all', that must be READY for exit 0 "
             f"(known: {', '.join(key for key, _fact, _req in WAITING)})",
    )
    args = parser.parse_args(argv)

    api = probe_public_api()
    print(f"installed pyjab: {api.version or 'not installed'}")
    if api.version is None:
        print("  (pip install pyjab -- nothing below can be measured without it)")
    print()

    table = readiness(api)
    width = max(len(key) for key in table)
    for key, (verdict, detail) in table.items():
        print(f"  {key.ljust(width)}  {verdict:<9}  {_one_line(detail)}")

    print()
    for key, _fact_name, requirement_key in WAITING:
        verdict, _ = table[key]
        if verdict == READY:
            continue
        if requirement_key is None:
            print(f"  {key}: waiting on a pyjab release for the tree walk")
            print(f"      {WALK_NOTE}")
            continue
        wanted = requirement(requirement_key)
        print(f"  {key}: waiting on pyjab for {wanted.wanted}")
        print(f"      blocks: {wanted.blocks}")
        print(f"      expected in: {wanted.expected_in}")

    required = [item.strip() for item in args.require.split(",") if item.strip()]
    keys = [key for key, _fact, _req in WAITING]
    if required == ["all"]:
        required = list(keys)
    unknown = [key for key in required if key not in keys]
    if unknown:
        parser.error(f"unknown requirement key(s): {', '.join(unknown)}")

    blocked = [key for key in required if table[key][0] != READY]
    if blocked:
        print()
        print(f"::error:: required but not ready: {', '.join(blocked)}")
        return 1

    if not required:
        print()
        print("Nothing required (pass --require to make this exit non-zero).")
    else:
        print()
        print(f"Ready: {', '.join(required)}. Bump the pin in pyproject.toml in the change "
              "that starts using it, and retire the matching REQUIREMENTS entry.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
