#!/usr/bin/env python
"""Refuse to publish a version that disagrees with itself.

Three places name the release, and a mismatch between them is the kind of mistake that is
only discovered after the fact: PyPI has the package, the registry has a version that does not
exist, and the Git tag points at neither. PyPI cannot be un-published, so the check runs before
anything is uploaded rather than after.

    python tools/check_release_version.py v0.1.0
    python tools/check_release_version.py --tag "$GITHUB_REF_NAME"

Checks, in the order they would fail:

* the tag is a release tag (``v`` + a PEP 440 release),
* the package's own version is that release -- **not** a ``.devN`` version, because a
  development version is not something that can be published,
* ``server.json`` names the same version in both places it appears,
* ``CHANGELOG.md`` has a section for it, so the release notes are not empty.

Exit code 0 when everything agrees, 1 with every disagreement listed when they do not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RELEASE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def check(tag: str) -> "List[str]":
    """Return every disagreement between *tag* and the files that name the release."""
    problems: "List[str]" = []

    match = RELEASE.match(tag.strip())
    if not match:
        return [
            f"the tag {tag!r} is not a release tag. Expected v<major>.<minor>.<patch>, "
            "because a published version can never be replaced and a tag is a promise."
        ]
    release = match.group(1)

    import pyjab_mcp

    if pyjab_mcp.__version__ != release:
        problems.append(
            f"src/pyjab_mcp/__init__.py says __version__ = {pyjab_mcp.__version__!r}, and the "
            f"tag is {release!r}. A .devN version is not a release: set the version to "
            f"{release!r} in the commit the tag points at."
        )

    server_path = REPO_ROOT / "server.json"
    if not server_path.exists():
        problems.append("server.json is missing, and the registry needs it to publish")
    else:
        server = json.loads(server_path.read_text(encoding="utf-8"))
        if server.get("version") != release:
            problems.append(
                f"server.json says version {server.get('version')!r}, and the tag is "
                f"{release!r}"
            )
        for package in server.get("packages", []):
            if package.get("version") != release:
                problems.append(
                    f"server.json's {package.get('identifier')} package says version "
                    f"{package.get('version')!r}, and the tag is {release!r}"
                )

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^##\s+\[?{re.escape(release)}\]?", changelog, re.MULTILINE):
        problems.append(
            f"CHANGELOG.md has no section for {release!r}. Move what is under 'Unreleased' "
            "into a section named after the release, so the GitHub release has notes."
        )
    return problems


def section(tag: str) -> str:
    """The CHANGELOG section body for *tag*, or an empty string when there is none."""
    match = RELEASE.match(tag.strip())
    if not match:
        return ""
    release = match.group(1)
    lines = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    collected: "List[str]" = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = bool(re.match(rf"^##\s+\[?{re.escape(release)}\]?", line))
            continue
        if inside:
            collected.append(line)
    return "\n".join(collected).strip()


def main(argv: "List[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="the tag being released, e.g. v0.1.0")
    parser.add_argument("--tag", dest="tag_option", help="the tag, when a flag is easier")
    parser.add_argument(
        "--notes", action="store_true",
        help="print the CHANGELOG section for the release instead of checking",
    )
    args = parser.parse_args(argv)
    tag = args.tag_option or args.tag
    if not tag:
        parser.error("give the tag being released, e.g. v0.1.0")
        return 2

    if args.notes:
        print(section(tag))
        return 0

    problems = check(tag)
    if problems:
        print(f"CANNOT RELEASE {tag}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASSED: {tag} agrees with the package, server.json and CHANGELOG.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
