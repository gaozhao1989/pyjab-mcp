#!/usr/bin/env python
"""A shipped file must not send a reader to a file that is not shipped.

`AGENTS.md`, `docs/PLAN.md`, `docs/DESIGN.md`, `docs/RELEASE_READINESS.md` and `.agents/` are
deliberately local: maintainer notes, kept out of git and out of the sdist (AGENTS.md §4, and
`tools/check_local_only_files.py` enforces both halves of that). Which means a reader of the
GitHub repository **or** of an installed sdist has never seen them -- and a comment, a docstring
or a README line that says "see AGENTS.md §6" points at nothing.

This was found by reading the published README, and it was worse there than in a comment: the
"Where to read first" table listed three files a GitHub visitor cannot open as *the* way to
understand the project, and a tool's error message told a user installing from PyPI to consult
`AGENTS.md §6` for the audit -- a file that is not in what they installed.

The rule this makes mechanical:

    A file that ships may state a rule, explain a decision, or link to a public document.
    It may not cite a local-only file as somewhere to read more.

The rule is about what the reader can open, not about how the path is spelled, and matching the
local-only paths by name was not enough. A shipped `tools/build_mcpb.py` cited
`references/manifest-schema.md`, which exists **only** inside the local skill tree -- no
`.agents/` prefix, so name matching stayed green while the reader was sent to a file they
cannot open. So every path-like citation is also *resolved*: a token with a directory component
that suffix-matches a file in this tree is a problem **when every file it matches is
local-only**. If a shipped file of that name exists, the reader can open that one and there is
nothing to report -- which is what keeps the rule from firing on `docs/CLIENTS.md`.

That second half is a claim about *this* tree, and it can only be checked where the local-only
files are: CI checks out none of them, so there every citation resolves against nothing and the
resolving half cannot complain, while the name half still fires. The guard's own "0 on this
repository" test therefore passes vacuously in CI, and the run that exercises the resolving half
is a maintainer's, on a machine that has the notes. That is issue #48's limit, recorded rather
than solved: committing a generated index of local-only paths so CI could resolve against it was
considered and deferred.

Three shapes are deliberately not flagged, because none of them names a location in this tree:

* a **bare filename** (`manifest-schema.md`, `SKILL.md`) -- with no directory there is nothing
  to resolve, and flagging every filename that happens to exist under `.agents/` would make the
  rule unusable;
* a **directory** (`references/`) -- resolution is to a file, and a directory is not one;
* a **URL** -- the host is part of the token, so a link can never suffix-match a path here. A
  link to a public document is what the rule asks for.

A leading `./` or `../` is dropped before resolving, because `../references/x.md` is still a
citation of a file the reader cannot open. A citation inside a fenced code block is not special
either: a reader still reads it. What makes the difference is whether it resolves, not how it
is written -- so Windows separators are normalised too, because `references\\manifest-schema.md`
is the same citation to a reader on Windows, and in a `.py` docstring or a `.toml` it is a path
rather than an escape.

Two kinds of exemption, both because the rule would otherwise forbid the mechanism that keeps
the rule:

* `.gitignore`, `MANIFEST.in` and `pyproject.toml` exist to name what is excluded, and
  `tools/check_local_only_files.py` *is* the list. Excluding them from the check is not a
  loophole; it is the only way the exclusion can be written down.
* `tests/test_guards.py` builds fixtures that write `.gitignore`/`MANIFEST.in` content to prove
  the exclusion works. A test of the mechanism has to name the paths the mechanism names.

Sections of `CHANGELOG.md` that are already recorded in
`tools/released_changelog_sections.json` are skipped, and that is not an exemption either: their
hashes are pinned, so they cannot change, and they are already published inside the sdists on
PyPI. Everything else -- including the rest of `CHANGELOG.md` -- is checked.

Usage:

    python tools/check_published_references.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from check_local_only_files import LOCAL_ONLY  # noqa: E402 - the list lives in one place
from check_changelog_immutable import sections as changelog_sections  # noqa: E402

#: The paths a reader of the repository or the sdist actually has. Everything named in a
#: shipped file should be one of these, an issue or pull request URL, or nothing at all.
SHIPPED_SUFFIXES = (".py", ".md", ".toml", ".cfg", ".in", ".json", ".txt", ".yaml", ".yml")

#: Directories that are not part of this project's source: build output and caches.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "build", "dist", ".pytest_cache", ".mypy_cache",
    "node_modules", ".venv",
})

#: Paths whose job is to name the local-only files. See the module docstring.
EXEMPT = frozenset({
    ".gitignore",
    "MANIFEST.in",
    "pyproject.toml",
    "tools/check_local_only_files.py",
    "tools/check_published_references.py",
    "tests/test_guards.py",
})


#: The local-only files themselves. They are not shipped, so a reference from one of them to
#: another is a maintainer note to a maintainer note -- which is what they are for. This check
#: is about what a reader who has *only* the repository or *only* the sdist can open.
NOT_SHIPPED = tuple(name.rstrip("/") for name in LOCAL_ONLY)

#: A path-like citation: a directory component, then a dotted filename. The directory is
#: required on purpose, and the dotted filename is too -- see the module docstring on bare
#: filenames and directories. Either separator is accepted, and normalised before resolving.
RELATIVE_CITATION = re.compile(r"[\w.\-]+[/\\][\w.\-/\\]*\.[A-Za-z0-9]+")


def files_to_check(root: Path = REPO_ROOT) -> "List[Path]":
    """Every shipped file that carries prose, minus the build output and the exemptions."""
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SHIPPED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        else:
            relative = path.relative_to(root).as_posix()
        if relative in EXEMPT or relative.startswith(NOT_SHIPPED):
            continue
        found.append(path)
    return found


def references(text: str) -> "List[Tuple[int, str]]":
    """``(line number, the local-only path named)`` for each mention in *text*.

    This is the spelling half: a path is named in full, as `AGENTS.md` or `.agents/skills/x`.
    The resolving half is `relative_references`.
    """
    names = [name.rstrip("/") for name in LOCAL_ONLY if name != ".agents/"] + [".agents"]
    pattern = re.compile("|".join(re.escape(name) for name in names))
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            found.append((number, match.group(0)))
    return found


def repository_files(root: Path = REPO_ROOT) -> "List[str]":
    """Every file under *root* as a relative POSIX path, build output excluded.

    The local-only files are the point of being here: resolving a citation means asking what
    the citation *could* mean in this tree, which is exactly the content that is not shipped.
    """
    found: "List[str]" = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        found.append(path.relative_to(root).as_posix())
    return found


def is_local_only(relative: str) -> bool:
    """True when *relative* is one of the paths that are deliberately kept local."""
    return any(
        relative == name or relative.startswith(name + "/") for name in NOT_SHIPPED
    )


def relative_references(
    text: str, index: "Sequence[str]"
) -> "List[Tuple[int, str]]":
    """``(line number, the relative path cited)`` for each citation that resolves only local.

    A citation resolves to a local-only file when some path in *index* ends with it **and**
    every path that ends with it is under a local-only directory. The second half is what keeps
    the rule honest: when a shipped file of that name exists, the reader can open it, and a
    complaint would be a false positive -- and a guard with false positives gets switched off.
    """
    found: "List[Tuple[int, str]]" = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in RELATIVE_CITATION.finditer(line):
            cited = re.sub(r"^(?:\.\.?/)+", "", match.group(0).replace("\\", "/"))
            resolved = [
                path for path in index if path == cited or path.endswith("/" + cited)
            ]
            if resolved and all(is_local_only(path) for path in resolved):
                found.append((number, cited))
    return found


def fresh_changelog_text(root: Path) -> str:
    """CHANGELOG.md with its frozen sections removed.

    Frozen sections are pinned by hash in ``released_changelog_sections.json``, so they cannot
    be edited, and they are already published inside the sdists on PyPI. Checking them would be
    asking for a change that the other guard forbids.
    """
    path = root / "CHANGELOG.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    kept: "List[str]" = []
    frozen: "Sequence[str]" = ()
    record = root / "tools" / "released_changelog_sections.json"
    if record.exists():
        import json

        frozen = tuple(json.loads(record.read_text(encoding="utf-8")))
    current = changelog_sections(text)
    for version, section in current.items():
        if version in frozen:
            # Replace the frozen text with a line naming it, so line numbers stay usable.
            kept.append(section.splitlines()[0])
            text = text.replace(section, "\n".join(kept[-1:]))
    return text


def check(root: Path = REPO_ROOT) -> "List[str]":
    """Return one complaint per shipped file that points at a local-only path."""
    problems: "List[str]" = []
    index = repository_files(root)
    for path in files_to_check(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "CHANGELOG.md":
            text = fresh_changelog_text(root)
        hits = references(text) + relative_references(text, index)
        if hits:
            hits.sort(key=lambda hit: hit[0])
            relative = path.relative_to(root).as_posix()
            where = ", ".join(f"line {number} ({name})" for number, name in hits[:4])
            more = "" if len(hits) <= 4 else f", and {len(hits) - 4} more"
            problems.append(f"{relative}: {where}{more}")
    return problems


def main(argv: "List[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    problems = check(args.root)
    if problems:
        print(f"FAILED: {len(problems)} shipped file(s) point at a local-only path.\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\n  A reader of the GitHub repository or of an installed sdist has never seen\n"
            f"  {', '.join(LOCAL_ONLY)}. State the rule where it is needed, or link to a\n"
            "  public document or issue -- do not cite a file they cannot open.\n"
            "\n  A relative path that resolves only inside one of them (a `references/x.md`\n"
            "  that exists only under `.agents/`) is the same failure in a different shape.\n"
            "\n  Files whose job is to name these paths (.gitignore, MANIFEST.in,\n"
            "  pyproject.toml, tools/check_local_only_files.py, tests/test_guards.py) are\n"
            "  exempt, and so are changelog sections whose hashes are already pinned."
        )
        return 1

    print(
        f"PASSED: no shipped file cites one of the {len(LOCAL_ONLY)} local-only path(s), "
        "by name or by a relative name that resolves only inside one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
