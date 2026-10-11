#!/usr/bin/env python
"""A released changelog section must never change again.

Copied from pyjab's rule of the same name, and for the incident behind it: at pyjab 1.6.3,
every entry added after the tag went into the 1.6.3 section, because that section sat at the
top of the file and that is where new entries get appended. The repository then claimed 1.6.3
contained the DPI fix, the xpath leak fix and the rest, while the sdist on PyPI -- built from
the tree at the tag -- did not. Nothing failed. It was noticed by hand at the next release,
which is not a mechanism.

Released versions are immutable -- pyjab's rule, and this project's. That had been read as being about PyPI,
which it is -- but the changelog is published too, inside the sdist, and the repository copy is
the one a reader sees. The two can disagree, and only one of them is on PyPI.

Why a recorded hash rather than a comparison against the tag
------------------------------------------------------------

Comparing a section to its tag is the obvious check, and it does not work where the check has
to run: GitHub Actions checks out at depth 1, so there are no tags and no history beyond the
one commit. So the expected value lives in `tools/released_changelog_sections.json`, in the
tree, and is compared against the working copy. No history, no network, and it fails on exactly
one thing: a released section whose text has changed since it was recorded.

Keeping it current
------------------

    $ python tools/check_changelog_immutable.py --record 0.1.0

Run it **after** tagging: `--record` reads the section out of the tag rather than the working
copy, so what gets frozen is what shipped. It refuses a version with no tag, refuses one
already recorded to a different value, and refuses a section it cannot find.

Nothing here substitutes a different question for an input it cannot honour: `--record ""` is
refused rather than read as "no `--record`" (the opposite operation, and a green one), and a
record file that cannot be read is refused rather than reported as "0 sections recorded" and
a pass -- which is the difference between "nothing is recorded" and "I could not read it".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
RECORD = REPO_ROOT / "tools" / "released_changelog_sections.json"

#: `## [1.2.3] - 2026-01-01`, the heading this project uses. `## Unreleased` deliberately does
#: not match, so the place new entries belong is never mistaken for a released section.
HEADING = re.compile(
    r"(?m)^##\s+\[?(\d+\.\d+\.\d+)\]?\s*[-–]\s*(\d{4}-\d{2}-\d{2})\s*$"
)


def sections(text: str) -> "dict[str, str]":
    """Every released section, keyed by version, as its text.

    A section runs to the next level-two heading, so it carries its own subsections and
    bullets. Trailing blank lines are dropped: they are not part of what a reader sees, and
    including them would make the hash depend on how the file happens to end.
    """
    found: "dict[str, str]" = {}
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            following = re.search(r"(?m)^##\s", text[match.end():])
            end = match.end() + following.start() if following else len(text)
        found[match.group(1)] = text[match.start():end].rstrip()
    return found


def digest(section: str) -> str:
    return "sha256:" + hashlib.sha256(section.encode("utf-8")).hexdigest()


def load_record() -> "dict[str, str]":
    if not RECORD.exists():
        raise SystemExit(
            f"FAILED: the changelog-immutability record {RECORD} is not there, so this "
            "guard cannot tell 'no released section is recorded' from 'the record could not "
            "be read'.\n"
            "  With nothing to compare it would print 'released sections recorded: 0' and "
            "PASSED, which turns the freeze off with a green exit. Restore it from git "
            "before trusting a green run."
        )
    return json.loads(RECORD.read_text(encoding="utf-8"))


def save_record(record: "dict[str, str]") -> None:
    RECORD.write_text(
        json.dumps(dict(sorted(record.items())), indent=2) + "\n", encoding="utf-8"
    )


def key(version: str) -> "list[int]":
    return [int(part) for part in version.split(".")]


def tag_section(version: str) -> str:
    """The section as it is in the tag, which is what shipped."""
    tag = f"v{version}"
    result = subprocess.run(
        ["git", "show", f"{tag}:CHANGELOG.md"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"no tag {tag}, or no CHANGELOG.md in it")
    found = sections(result.stdout).get(version)
    if found is None:
        raise SystemExit(f"{tag} has no {version} section in its CHANGELOG.md")
    return found


def check() -> int:
    record = load_record()
    current = sections(CHANGELOG.read_text(encoding="utf-8"))
    failures = 0

    print(f"released sections recorded: {len(record)}")
    for version in sorted(record, key=key):
        if version not in current:
            print(f"  {version}: section is MISSING from CHANGELOG.md")
            failures += 1
        elif digest(current[version]) != record[version]:
            print(f"  {version}: section has CHANGED since it was released")
            failures += 1

    for version in sorted(current, key=key):
        if version not in record:
            print(
                f"  {version}: section here, not recorded yet -- run "
                f"`--record {version}` after its tag exists"
            )

    if failures:
        print(
            f"\nFAILED: {failures} released section(s) are not what shipped.\n"
            "\n  A released version's changelog is published inside its sdist. Editing the\n"
            "  section afterwards makes this repository describe a release that never\n"
            "  happened, and nothing says which copy is right -- the repository is the one\n"
            "  people read, and the sdist is the one that shipped.\n"
            "\n  New entries belong under 'Unreleased'. If a released section genuinely has\n"
            "  to change, say why in the commit that does it, and update\n"
            "  tools/released_changelog_sections.json in the same commit, so the change is\n"
            "  deliberate and visible rather than discovered at the next release."
        )
        return 1

    print("\nPASSED: every recorded released section is unchanged.")
    return 0


def record(version: str) -> int:
    recorded = load_record()
    section = tag_section(version)
    value = digest(section)
    existing = recorded.get(version)
    if existing and existing != value:
        raise SystemExit(
            f"{version} is already recorded as {existing}; refusing to replace it with "
            f"{value}. A released section is not supposed to change."
        )
    recorded[version] = value
    save_record(recorded)
    print(f"recorded {version} from v{version}: {value}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record", metavar="VERSION",
        help="freeze the section for VERSION, read from its tag",
    )
    args = parser.parse_args(argv)
    if args.record is not None:
        if not args.record:
            print(
                "FAILED: --record was given an empty string, which names no release to "
                "freeze.\n"
                "  It used to read as falsy and run the plain check instead -- the "
                "opposite\n  operation, and a green one."
            )
            return 1
        return record(args.record)
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
