#!/usr/bin/env python
"""The list of files that must never be published, and both mechanisms that keep them out.

Why this exists
---------------

``AGENTS.md``, the plan and the design notes are maintainer documents: they carry product
strategy, local machine paths and reasoning that is for whoever is working on the project,
not for a user who installed it. pyjab's working agreement puts the rule well: keep
``.gitignore`` and the sdist manifest in step, **because an sdist is built from the working
tree and ``.gitignore`` does not protect it.**

Two mechanisms, then, and they fail differently:

* ``.gitignore`` keeps the file out of the repository;
* ``MANIFEST.in`` keeps it out of the sdist that ``python -m build`` produces from that
  same working tree.

Adding a file to one and not the other is invisible until somebody publishes — which is
exactly the shape of mistake this project's rules are about, so the list lives here once
and both mechanisms are checked against it.

A tree where git cannot be asked at all is a third state, and it is a **refusal** rather than
a pass: an extracted sdist has ``.gitignore`` and ``MANIFEST.in`` and no ``.git``, and it is
exactly the artifact a maintainer inspects when asking "did anything local-only get shipped?".
``.gitignore`` is a claim and ``git check-ignore`` is the behaviour, so a guard whose summary
sentence is about git must not print that sentence having never invoked git.  A caller that
wants the sdist half alone asks for it explicitly, with ``check(use_git=False)``.

Usage
-----

    python tools/check_local_only_files.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = REPO_ROOT / ".gitignore"
MANIFEST = REPO_ROOT / "MANIFEST.in"

#: Paths that stay local.  A trailing slash means a directory.
#:
#: This is the single source of truth: `tools/check_dist_contents.py` imports it to check a
#: built artifact, and `tests/test_packaging.py` asserts the guard itself passes.
LOCAL_ONLY: Sequence[str] = (
    "AGENTS.md",
    "docs/PLAN.md",
    "docs/DESIGN.md",
    "docs/RELEASE_READINESS.md",
    ".agents/",
    ".dsh/",
)

#: Patterns that are security-related rather than merely local.  They are excluded by
#: ``.gitignore`` and ``MANIFEST.in`` like the list above, but they are **not** checked by
#: this guard: ``_gitignore_covers`` compares a path's own line, and a glob such as ``*.pem``
#: matches by a rule the comparison does not evaluate.  Saying so here is the point -- a
#: reader should know which mechanism covers which, rather than assuming this list is all of
#: it (AGENTS.md section 8).
SECRET_PATTERNS: Sequence[str] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "credentials*",
)

#: The prefix of a refusal: git could not be asked, so the git half of the check has no
#: verdict.  It is not ``False`` -- see ``_git_ignores``.
GIT_UNREADABLE = "GIT UNREADABLE"


def _gitignore_covers(path: str, text: str) -> bool:
    """True when some ``.gitignore`` line excludes *path*."""
    wanted = path.rstrip("/")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.rstrip("/")
        if line.lstrip("/") == wanted or line == wanted:
            return True
    return False


def _manifest_covers(path: str, text: str) -> bool:
    """True when ``MANIFEST.in`` excludes or prunes *path*."""
    wanted = path.rstrip("/")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        command, argument = parts[0].lower(), parts[1].strip().lstrip("/").rstrip("/")
        if command == "exclude" and argument == wanted:
            return True
        if command == "prune" and (argument == wanted or wanted.startswith(argument + "/")):
            return True
    return False


def _git_ignores(path: str, repo: Path = REPO_ROOT) -> Optional[bool]:
    """Ask git, or None when git or the repository is unavailable.

    The file's own text is a claim; this is the behaviour.  They disagree more often than
    people expect -- a negation later in the file, or a directory that never matched.

    ``git check-ignore`` exits 0 for ignored, 1 for not ignored, and something else when it
    could not answer at all (128 outside a repository, for one).  Only the first two are
    verdicts: the third is the unreadable state, which must not be spelled ``False`` -- that
    reads as "git says no" and is a different thing from "git never said anything".
    """
    if not (repo / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=repo, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _git_can_be_asked(repo: Path) -> bool:
    """True when ``git check-ignore`` answers for *repo* at all.

    Measured: ``check-ignore`` answers for a path that is not on disk, so this is a question
    about git and the repository, not about whether the file is present in this checkout.
    """
    return _git_ignores(".gitignore", repo) is not None


def check(
    *,
    repo: Path = REPO_ROOT,
    local_only: Sequence[str] = LOCAL_ONLY,
    use_git: bool = True,
) -> List[str]:
    """Return every local-only path that is not kept out of both git and the sdist.

    With *use_git* left true -- the default, and what the command line does -- a tree where
    git cannot be asked is a refusal, because the summary this check prints is a claim about
    git.  ``use_git=False`` is the explicit way to ask for the sdist half alone.
    """
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    manifest = (repo / "MANIFEST.in").read_text(encoding="utf-8")
    problems: List[str] = []

    git_ok = use_git and _git_can_be_asked(repo)
    if use_git and not git_ok:
        problems.append(
            f"{GIT_UNREADABLE}: git is not available in {repo}, so the git half of this "
            "check did not run. .gitignore is a claim and `git check-ignore` is the "
            "behaviour, and it was never asked. Run this in a git checkout, or call "
            "check(use_git=False) for the sdist half alone."
        )

    for path in local_only:
        if not _gitignore_covers(path, gitignore):
            problems.append(
                f"NOT IGNORED: {path} is local-only but no .gitignore line excludes it, so "
                "the next `git add -A` publishes it."
            )
        if not _manifest_covers(path, manifest):
            problems.append(
                f"NOT EXCLUDED FROM THE SDIST: {path} is local-only and MANIFEST.in does "
                "not exclude or prune it. .gitignore does not protect an sdist -- it is "
                "built from the working tree."
            )
        if git_ok:
            ignored = _git_ignores(path, repo)
            if ignored is False:
                problems.append(
                    f"GIT DISAGREES: .gitignore looks like it covers {path}, but "
                    "`git check-ignore` says it does not."
                )
            elif ignored is None:
                problems.append(
                    f"{GIT_UNREADABLE}: `git check-ignore` could not answer for {path}, so "
                    "whether git ignores it was not established."
                )
    return problems


def main(repo: Path = REPO_ROOT) -> int:
    print(f"{len(LOCAL_ONLY)} local-only path(s) that must stay out of git and the sdist:")
    for path in LOCAL_ONLY:
        print(f"  {path}")

    problems = check(repo=repo)
    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        print()
        if _git_can_be_asked(repo):
            print("FAILED: a maintainer document would be published.")
        else:
            print(
                f"FAILED: git is not available in {repo}, so the git half of this check "
                "did not run."
            )
        return 1

    print("\nPASSED: every one is ignored by git and excluded from the sdist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
