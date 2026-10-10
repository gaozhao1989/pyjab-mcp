#!/usr/bin/env python
"""Check a built sdist and wheel for what should and should not be inside them.

Why this exists
---------------

Two failures here are both silent until a user hits them:

* **a maintainer document ships.** ``AGENTS.md`` and the design notes are local-only,
  ``.gitignore`` does not protect an sdist, and ``MANIFEST.in`` is the only thing that does.
  ``tools/check_local_only_files.py`` checks the *rules*; this checks the *artifact*, because
  the rules can be right and the build can still be wrong.
* **the console script does not survive packaging.** ``pyproject.toml`` says what should
  happen; the wheel's ``entry_points.txt`` is what did. pyjab learned this the direct way:
  a wheel check finds it on the pull request rather than at tag time.

The stale-copy check is pyjab's: a ``build/`` or ``dist/`` copy or an ``egg-info`` directory
inside the wheel means ``packages.find`` is picking up artefacts.

Usage
-----

    python -m build
    python tools/check_dist_contents.py --dist dist
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent

# The list is defined once, in the guard that checks the rules, and imported here so the
# artifact check cannot drift from it.  Running this file as a script puts tools/ itself on
# sys.path; importing it as tools.check_dist_contents needs the repository root.
try:
    from tools.check_local_only_files import LOCAL_ONLY
except ImportError:  # pragma: no cover - the script path
    sys.path.insert(0, str(REPO_ROOT))
    from tools.check_local_only_files import LOCAL_ONLY

#: What the wheel must contain, and must not.
CONSOLE_SCRIPT = "pyjab-mcp = pyjab_mcp.__main__:main"
WHEEL_FORBIDDEN_PREFIXES = ("build/", "dist/")

#: Files an sdist must contain: the package, the tests, the guards, and the published docs.
SDIST_REQUIRED = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "docs/ARCHITECTURE.md",
    "src/pyjab_mcp/__main__.py",
    "src/pyjab_mcp/bridge.py",
    "tests/test_packaging.py",
    "tools/check_pyjab_api_surface.py",
)


def sdist_members(archive: Path) -> Set[str]:
    """Member paths with the leading ``name-version/`` directory stripped."""
    with tarfile.open(archive, "r:*") as tar:
        names = [member.name for member in tar.getmembers() if member.isfile()]
    stripped = set()
    for name in names:
        parts = name.split("/", 1)
        stripped.add(parts[1] if len(parts) == 2 else name)
    return stripped


def wheel_members(archive: Path) -> Set[str]:
    with zipfile.ZipFile(archive) as zf:
        return set(zf.namelist())


def _local_only_present(members: Iterable[str], kind: str) -> List[str]:
    problems = []
    for path in LOCAL_ONLY:
        stripped = path.rstrip("/")
        hits = sorted(
            member for member in members
            if member == stripped or member.startswith(stripped + "/")
        )
        if hits:
            problems.append(
                f"PUBLISHED: {kind} contains {path}, which is local-only "
                f"({', '.join(hits[:3])}). Check that MANIFEST.in still excludes it -- "
                ".gitignore does not protect an sdist."
            )
    return problems


def check_sdist(archive: Path) -> List[str]:
    members = sdist_members(archive)
    problems = _local_only_present(members, "the sdist")
    for required in SDIST_REQUIRED:
        if required not in members:
            problems.append(
                f"MISSING FROM SDIST: {required} is not in {archive.name}. A source "
                "distribution that cannot run its own tests or guards is not a source "
                "distribution."
            )
    return problems


def check_wheel(archive: Path) -> List[str]:
    members = wheel_members(archive)
    problems = _local_only_present(members, "the wheel")

    stale = sorted(
        member for member in members
        if member.startswith(WHEEL_FORBIDDEN_PREFIXES) or "egg-info" in member
    )
    if stale:
        problems.append(
            f"STALE COPIES IN WHEEL: {stale[:5]}. A build/ or dist/ copy or an egg-info "
            "directory inside the wheel means packages.find is picking up artefacts."
        )

    leaked = sorted(
        member for member in members
        if member.startswith(("tests/", "tools/"))
    )
    if leaked:
        problems.append(
            f"MAINTAINER FILES IN WHEEL: {leaked[:5]}. The wheel ships the package; the "
            "tests and guards belong in the sdist only."
        )

    entry_points = [member for member in members if member.endswith("entry_points.txt")]
    if not entry_points:
        problems.append("NO ENTRY POINTS: the wheel has no entry_points.txt, so the console script is not installed.")
    else:
        with zipfile.ZipFile(archive) as zf:
            text = zf.read(entry_points[0]).decode("utf-8", "replace")
        if CONSOLE_SCRIPT not in text:
            problems.append(
                f"NO CONSOLE SCRIPT: {entry_points[0]} does not contain {CONSOLE_SCRIPT!r}. "
                f"It says:\n{text.strip()}"
            )
    return problems


def check(dist: Path = REPO_ROOT / "dist") -> List[str]:
    """Return every problem with the artifacts in *dist*."""
    if not dist.is_dir():
        return [
            f"NO DIST DIRECTORY: {dist} does not exist. Build first: python -m build"
        ]
    sdists = sorted(dist.glob("*.tar.gz"))
    wheels = sorted(dist.glob("*.whl"))
    problems: List[str] = []
    if not sdists:
        problems.append(f"NO SDIST: no *.tar.gz in {dist}")
    if not wheels:
        problems.append(f"NO WHEEL: no *.whl in {dist}")
    for archive in sdists:
        problems.extend(check_sdist(archive))
    for archive in wheels:
        problems.extend(check_wheel(archive))
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist", default=str(REPO_ROOT / "dist"),
        help="directory holding the built sdist and wheel (default: dist/)",
    )
    args = parser.parse_args(argv)
    dist = Path(args.dist)

    problems = check(dist)
    for archive in sorted(dist.glob("*")):
        print(f"  {archive.name}")
    if problems:
        print()
        for problem in problems:
            print(f"::error:: {problem}")
        print()
        print("FAILED: the built artifact is not what should be published.")
        return 1
    print("\nPASSED: the sdist ships what it should, and the wheel ships only the package.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
