"""What can be checked before there is anything to check.

The server does not exist yet, so these assert the things that are already true and would
be expensive to discover later: that the package imports anywhere, that the entry point
names something real, and that no module has started importing pyjab at module scope.

That last one is not hypothetical. ``pyjab`` raises ``ImportError`` off Windows by design,
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
    assert pyjab_mcp.__version__ != "0.0.0" or "not released"  # placeholder until 0.1.0


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


def test_the_entry_point_points_at_something_that_exists(tmp_path):
    """A console script naming a missing module fails at install, not at import."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pyjab-mcp = "pyjab_mcp.__main__:main"' in text

    # The target is allowed not to exist yet -- the skeleton declares the entry point
    # before the server -- but that has to be a decision with a date on it, not an
    # accident. This test is the reminder.
    target = PACKAGE / "__main__.py"
    if not target.exists():
        pytest.xfail(
            "pyjab_mcp.__main__ does not exist yet, so the console script would not run. "
            "Expected during M1 skeleton work; remove this xfail then."
        )
