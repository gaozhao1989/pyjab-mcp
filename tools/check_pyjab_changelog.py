#!/usr/bin/env python
"""Fail while a pyjab change marked as affecting this project has not been looked at.

Why this exists
---------------

The dependency runs one way: this project imports pyjab, and pyjab knows nothing about it.
So a pyjab change can break this project without anything in that repository going red --
pyjab's own suite passes, its release goes out, and the first evidence here is a tool that
misbehaves for a user.

pyjab's changelog carries a marker line for changes it judges to matter here:

    * **Something changed.** ... ``Affects: pyjab-mcp``

A marker is a human judgement and can be forgotten or wrong, which is why this is only one
of the two mechanisms in AGENTS.md §1a -- the load-bearing one is the contract tests in
``tests/contract/``, which pin behaviour rather than announcements. This check covers the
other half: a marker that has been written and nobody has answered.

Why the state is on this side
-----------------------------

pyjab freezes its released changelog sections with a check of its own, so "we have dealt with
this" cannot be written there without editing published text. ``acknowledged.yaml`` at this
repository's root holds it instead: one entry per marked change, saying what was done here.

What fails
----------

* a marker with no entry in ``acknowledged.yaml`` -- nobody has looked;
* an entry for a marker that is no longer in the changelog -- the file has drifted, and a
  stale acknowledgement is a claim about a change that no longer exists;
* (with ``--strict``) a changelog that could not be read at all. Without it, an unreadable
  source is reported as ``UNAVAILABLE`` and the check passes: "I could not read it" is not
  "nothing was marked" (AGENTS.md §2.4), and a check that goes red every time the network
  hiccups is a check people learn to ignore.

Usage
-----

    python tools/check_pyjab_changelog.py                    # find the changelog, report
    python tools/check_pyjab_changelog.py --strict           # unreadable is a failure
    python tools/check_pyjab_changelog.py --changelog CHANGELOG.rst
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ACKNOWLEDGED = REPO_ROOT / "acknowledged.yaml"

#: pyjab's checkout on this machine, used when it is there so a local run needs no network.
SIBLING = Path(
    "/Users/garygao/Library/Mobile Documents/com~apple~CloudDocs/Documents/Projects/"
    "PythonProjects/pyjab/CHANGELOG.rst"
)

RAW_URL = "https://raw.githubusercontent.com/gaozhao1989/pyjab/master/CHANGELOG.rst"

#: The marker itself.  Tolerant about backticks and case, because it is written by hand in
#: somebody else's repository and this side has to read whatever they wrote.
MARKER = re.compile(r"affects:\s*`?pyjab-mcp`?", re.IGNORECASE)

#: A commit-ish token on the marker line, which is how an acknowledgement names the change.
COMMIT = re.compile(r"\b([0-9a-f]{7,40})\b")

#: A changelog section header, for a marker written without a commit: it gives the
#: acknowledgement something human to point at.
SECTION = re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+[^\n]*)$", re.MULTILINE)


@dataclass(frozen=True)
class Marker:
    """One ``Affects: pyjab-mcp`` line, and the id an acknowledgement has to match."""

    id: str
    section: str
    line: str
    line_number: int

    def describe(self) -> str:
        where = f"{self.section}: {self.line}" if self.section else self.line
        return f"{self.id}  (line {self.line_number}) {where}"


def markers(text: str) -> "List[Marker]":
    """Every marker in *text*, each with the id an acknowledgement must carry.

    The id is the commit on the marker line when there is one, and otherwise a digest of the
    line itself.  The fallback is deliberate: editing an unacknowledged marker changes its id,
    so a reworded marker has to be looked at again rather than inheriting the previous
    answer.
    """
    found: List[Marker] = []
    sections = [(match.start(), match.group(1)) for match in SECTION.finditer(text)]
    for number, line in enumerate(text.splitlines(), start=1):
        if not MARKER.search(line):
            continue
        commit = COMMIT.search(line)
        marker_id = commit.group(1) if commit else "line:" + hashlib.sha1(
            line.strip().encode("utf-8")
        ).hexdigest()[:12]
        offset = sum(len(part) + 1 for part in text.splitlines()[: number - 1])
        section = ""
        for start, title in sections:
            if start <= offset:
                section = title
            else:
                break
        found.append(Marker(id=marker_id, section=section, line=line.strip(),
                            line_number=number))
    return found


def load_acknowledged(
    path: Path = ACKNOWLEDGED,
) -> "Tuple[Dict[str, str], Dict[str, str], Optional[str]]":
    """``(id -> what was done, context, error)``.  The error is a sentence, not an exception.

    ``context`` is the unenforced half of the file: pyjab work from before the marker
    convention, kept so the reasons this project pins what it pins are not lost. It is
    reported but never counted as an answer -- an entry there for a change that *does* carry
    a marker must move into ``acknowledged``, or the marker stays unanswered.
    """
    if not path.exists():
        return {}, {}, (
            f"{path.name} does not exist. It is the record of what was done about each "
            "marked pyjab change, and AGENTS.md 1a requires it."
        )
    try:
        import yaml
    except ImportError:
        return {}, {}, (
            "PyYAML is not installed. It is a dev dependency: pip install -e '.[dev]'"
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - a broken file is a reported state
        return {}, {}, f"{path.name} could not be parsed: {type(exc).__name__}: {exc}"
    if not isinstance(loaded, dict):
        return {}, {}, f"{path.name} should be a mapping with an 'acknowledged' list"

    def entries_from(key: str) -> "Tuple[Dict[str, str], Optional[str]]":
        entries = loaded.get(key) or []
        if not isinstance(entries, list):
            return {}, f"{path.name}: {key!r} should be a list"
        collected: Dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict) or "commit" not in entry:
                return {}, (
                    f"{path.name}: every entry in {key!r} needs a 'commit' "
                    "(or 'line:<digest>') key"
                )
            collected[str(entry["commit"])] = str(entry.get("what", ""))
        return collected, None

    acknowledged, error = entries_from("acknowledged")
    if error:
        return {}, {}, error
    context, error = entries_from("context")
    if error:
        return {}, {}, error
    return acknowledged, context, None


def check(found: Sequence[Marker], acknowledged: Dict[str, str]) -> "List[str]":
    """Problems with the acknowledgement state, as sentences."""
    problems: List[str] = []
    ids = {marker.id for marker in found}

    for marker in found:
        if marker.id not in acknowledged:
            problems.append(
                f"UNACKNOWLEDGED: pyjab marked a change as affecting this project and "
                f"nothing here says what was done about it -- {marker.describe()}. Add an "
                "entry to acknowledged.yaml with that id and the outcome, after checking "
                "whether this project has to change."
            )

    for marker_id, what in acknowledged.items():
        if marker_id not in ids:
            problems.append(
                f"STALE: acknowledged.yaml records {marker_id!r} ({what!r}) but pyjab's "
                "changelog no longer carries that marker. Remove the entry, or restore the "
                "marker if it was lost."
            )
    return problems


def read_changelog(source: Optional[str] = None, *, timeout: float = 30.0) -> "Tuple[Optional[str], str]":
    """``(text, where it came from)``.  Text is None when nothing could be read."""
    tried: List[str] = []

    candidates: "List[Tuple[str, object]]" = []
    if source:
        candidates.append((source, Path(source)))
    env_source = os.environ.get("PYJAB_CHANGELOG")
    if env_source:
        candidates.append((env_source, Path(env_source)))
    candidates.append((str(SIBLING), SIBLING))

    for label, path in candidates:
        try:
            return Path(path).read_text(encoding="utf-8"), label
        except OSError as exc:
            tried.append(f"{label} ({type(exc).__name__})")

    try:
        import urllib.request

        with urllib.request.urlopen(RAW_URL, timeout=timeout) as response:
            return response.read().decode("utf-8"), RAW_URL
    except Exception as exc:  # noqa: BLE001 - offline is a reported state
        tried.append(f"{RAW_URL} ({type(exc).__name__}: {exc})")

    return None, "; ".join(tried)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changelog", default=None,
                        help="path to pyjab's CHANGELOG.rst (default: the sibling checkout, "
                             "then $PYJAB_CHANGELOG, then GitHub)")
    parser.add_argument("--strict", action="store_true",
                        help="treat an unreadable changelog as a failure")
    args = parser.parse_args(argv)

    text, where = read_changelog(args.changelog)
    if text is None:
        print(f"UNAVAILABLE: could not read pyjab's changelog. Tried: {where}")
        print(
            "\nThis is not 'nothing is marked'. Pass --changelog, set PYJAB_CHANGELOG, or "
            "give this process network access."
        )
        return 1 if args.strict else 0

    acknowledged, context, error = load_acknowledged()
    found = markers(text)
    print(f"pyjab's changelog read from {where}")
    print(f"{len(found)} marker(s) affecting this project, "
          f"{len(acknowledged)} acknowledged")

    if error:
        print(f"\n::error:: {error}")
        return 1

    if context:
        print(f"\n{len(context)} pre-convention entr(y/ies) recorded as context "
              "(not enforced):")
        for marker_id, what in context.items():
            print(f"  [context] {marker_id}")

    for marker in found:
        what = acknowledged.get(marker.id)
        mark = "acked" if what else "NEW  "
        print(f"  [{mark}] {marker.describe()}")
        if what:
            print(f"          -> {what}")

    problems = check(found, acknowledged)
    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        print()
        print("FAILED: a pyjab change marked as affecting this project is unanswered.")
        return 1

    print("\nPASSED: every marked pyjab change has an answer here.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
