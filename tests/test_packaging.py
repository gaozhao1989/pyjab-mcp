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
import json
import os
import re
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


# ---------------------------------------------------------------------------------
# server.json -- the MCP Registry metadata (M8)
# ---------------------------------------------------------------------------------


def test_server_json_satisfies_the_registry_schema_it_names():
    """The fields the registry's current schema requires, in the names it requires.

    Read from the published schema rather than remembered, and the reading mattered twice:

    * the version this project first used (2025-07-09) spells these ``registry_type`` and
      ``runtime_hint``; the current one (2025-12-11) and the registry API spell them
      ``registryType`` and ``runtimeHint``. The registry's own validator returned
      ``422 ... body.packages[0].registryType expected length >= 1`` for the snake_case file,
      which is the kind of failure that would otherwise have arrived *after* PyPI had already
      published the package.
    * 2025-07-09 also requires ``version`` inside a package; 2025-12-11 does not. Keeping it
      is still valid, and it is what a client needs to install a specific release.
    """
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    assert server["$schema"] == (
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    ), "a deprecated schema is reported by mcp-publisher as a failure to publish"

    # ServerDetail: required
    for key in ("name", "description", "version"):
        assert server.get(key), f"the registry requires {key!r}"
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", server["name"]), (
        "the name is reverse-DNS with exactly one slash"
    )
    assert len(server["description"]) <= 100, "the schema caps the description at 100 characters"

    package = server["packages"][0]
    # Package: required registryType, identifier, transport
    for key in ("registryType", "identifier", "transport"):
        assert package.get(key), f"the registry requires packages[].{key!r}"
    assert package["registryType"] == "pypi", "the documented values are npm/pypi/oci/nuget/mcpb"
    assert package["identifier"] == "pyjab-mcp"
    assert package["transport"] == {"type": "stdio"}, "this server is stdio only"


@pytest.mark.skipif(
    not os.environ.get("PYJAB_MCP_REGISTRY"),
    reason="needs the mcp-publisher binary and the network; set PYJAB_MCP_REGISTRY=1",
)
def test_the_registry_accepts_server_json():
    """The authority on the schema is the registry, so this asks it when asked to.

    Off by default because it needs a downloaded binary and a network round trip, and it is
    *on* in the release workflow, where publishing metadata the registry rejects would
    otherwise be discovered after PyPI already had the package.
    """
    import shutil
    import subprocess

    publisher = shutil.which("mcp-publisher") or str(REPO_ROOT / "mcp-publisher")
    if not Path(publisher).exists():
        pytest.skip("mcp-publisher is not installed")

    result = subprocess.run(
        [publisher, "validate"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, (
        f"the registry rejected server.json:\n{result.stdout}\n{result.stderr}"
    )
    assert "is valid" in result.stdout


def test_server_json_names_the_release_the_package_declares():
    """A registry entry pointing at a version that does not exist is worse than none.

    The version is read from the **source** rather than from the installed metadata, and that is
    a correction rather than a preference: `importlib.metadata` reads the editable install's
    `.egg-info`, which lags until something rebuilds it, so this test failed spuriously in the
    minute between bumping `__version__` and running `python -m build`. `pyproject.toml` sets the
    distribution version from that same attribute, so the source is also the authority.
    """
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    package_version = pyjab_mcp.__version__

    release = re.match(r"\d+\.\d+\.\d+", package_version)
    assert release, f"cannot read a release out of {package_version!r}"
    assert server["version"] == release.group(0), (
        "server.json names the release, not the development version: it must be "
        f"{release.group(0)}, not {server['version']!r}"
    )
    assert server["packages"][0]["version"] == server["version"], (
        "the server and its package are the same release"
    )


def test_server_json_points_at_this_repository():
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    assert server["repository"]["source"] == "github"
    assert server["repository"]["url"].endswith("gaozhao1989/pyjab-mcp")


def test_the_readme_carries_the_registry_ownership_line():
    """The MCP Registry proves ownership of a PyPI package by finding this line in its README.

    Learned by publishing 0.1.0: PyPI succeeded, the registry refused with

        400 registry validation failed for package 0 (pyjab-mcp): PyPI package 'pyjab-mcp'
        ownership validation failed. The server name 'io.github.gaozhao1989/pyjab-mcp' must
        appear as 'mcp-name: io.github.gaozhao1989/pyjab-mcp' in the package README

    and a published PyPI version cannot have its metadata edited, so 0.1.1 was the only way to
    register. This test makes the line's absence a build failure rather than something a release
    discovers, and it reads the expected value out of server.json so the two cannot drift.
    """
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert f"mcp-name: {server['name']}" in readme, (
        "the registry reads this line out of the *published* package's README; it has to be "
        "here before the tag, because it cannot be added afterwards"
    )
