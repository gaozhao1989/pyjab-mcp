"""What can be checked about the package itself, on any machine.

These assert the things that are cheap to check and expensive to discover late: that the
package imports anywhere, that the entry point names something real, that no module has
started importing pyjab at module scope, and that local-only files stay local.

The pyjab one is not hypothetical. ``pyjab`` raises ``ImportError`` off Windows by design,
so a module-scope import here would make this package unimportable on every non-Windows
machine -- including the ones that only run the tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pyjab_mcp

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "src" / "pyjab_mcp"


def modules():
    return sorted(PACKAGE.rglob("*.py"))


def test_the_package_imports_and_has_a_version():
    assert pyjab_mcp.__version__
    assert pyjab_mcp.__version__ != "0.0.0"


def test_there_is_something_to_check():
    """A suite that collects nothing passes, and says nothing."""
    assert modules(), f"no modules under {PACKAGE}"


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_nothing_imports_pyjab_at_module_scope(path):
    """pyjab is Windows-only at import time, and this package is not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    offenders = []
    for node in tree.body:                      # body, not walk: only the top level
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if a.name.split(".")[0] == "pyjab")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "pyjab":
                offenders.append(node.module or "")

    assert not offenders, (
        f"{path.name} imports {offenders} at module scope, which fails off Windows. "
        "Import it inside functions instead -- see this package's __init__."
    )


def test_every_package_module_is_reachable_without_windows():
    """Import them all: a Windows-only import at module scope would fail here first."""
    import importlib

    for path in modules():
        name = path.stem
        if name == "__init__":
            continue
        importlib.import_module(f"pyjab_mcp.{name}")


def test_the_entry_point_points_at_something_that_exists():
    """A console script naming a missing module fails at install, not at import."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pyjab-mcp = "pyjab_mcp.__main__:main"' in text

    # M1 shipped __main__.py, so this is now a real assertion rather than the xfail the
    # skeleton carried while the entry point existed before the server did.
    assert (PACKAGE / "__main__.py").is_file()


def test_local_only_files_are_kept_out_of_git_and_out_of_the_sdist():
    """Two mechanisms, and both are needed: an sdist is built from the working tree.

    ``.gitignore`` does not protect an sdist, so a note that is ignored by git and not
    pruned by ``MANIFEST.in`` is published by the next ``python -m build``. That is pyjab's
    reasoning, and it has already gone wrong there once.

    The list of local files lives in the guard, once, and this delegates to it rather than
    repeating the names: ``tools/check_local_only_files.py`` is also what CI runs and what
    ``tests/test_guards.py`` proves can fail.
    """
    from tools.check_local_only_files import check

    assert check() == [], (
        "a maintainer note would be published -- run "
        "`python tools/check_local_only_files.py` for the detail"
    )
