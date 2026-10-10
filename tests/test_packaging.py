"""What can be checked about the package itself, on any machine.

These assert the things that are cheap to check and expensive to discover late: that the
package imports anywhere, that the entry point names something real, that no module has
started importing pyjab at module scope, that local-only files stay local, and that the MCPB
bundle -- the artifact a user actually installs -- says what it is and carries what it needs.

The pyjab one is not hypothetical. ``pyjab`` raises ``ImportError`` off Windows by design,
so a module-scope import here would make this package unimportable on every non-Windows
machine -- including the ones that only run the tests.

The bundle tests build a *real* bundle with a *stub* ``dist/`` and read the zip back. Every
failure the bundle can have is one a user meets as an uninstallable file rather than as a red
test, so the assertions are about the artifact: the version it declares, the platform, the
entry point the manifest names, the directory the shipped entry script looks in, and what must
not be inside.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import pyjab_mcp

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "src" / "pyjab_mcp"
PACKER = REPO_ROOT / "tools" / "build_mcpb.py"
ENTRY_SCRIPT = REPO_ROOT / "mcpb" / "entry.py"
BUNDLE_MANIFEST = "manifest.json"
BUNDLE_ENTRY = "server/entry.py"


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


# ---------------------------------------------------------------------------------
# The MCPB bundle -- `tools/build_mcpb.py`, read back out of the zip it produces
# ---------------------------------------------------------------------------------
#
# The packer is run as a script here, against a stub `dist/` and with `--skip-dependencies`,
# so this is offline and needs no network. What it produces is then read as a zip: the
# assertions are about the artifact, because the artifact is what a user installs. The one
# fact that needs both halves -- where the entry script looks for the dependencies -- is
# asserted by *executing the file that is in the bundle*, not the file in the repository.


def _stub_wheel(dist: Path) -> Path:
    """A wheel pip installs offline with no dependencies: what `python -m build` would leave.

    Real enough for pip (``METADATA``, ``WHEEL``, ``RECORD``), and it declares the package's
    own version, so the packer's "the bundle's contents and its manifest agree" check is
    exercised rather than bypassed.
    """
    version = pyjab_mcp.__version__
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / f"pyjab_mcp-{version}-py3-none-any.whl"
    info = f"pyjab_mcp-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pyjab_mcp/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr(
            f"{info}/METADATA",
            f"Metadata-Version: 2.1\nName: pyjab-mcp\nVersion: {version}\n\n",
        )
        archive.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: tests/test_packaging.py\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{info}/RECORD", "")
    return wheel


#: The interpreter the stub bundle is vendored for. It is the one running the tests, so the
#: entry script's own arithmetic -- `cp3XX` from `sys.version_info` -- is what decides the
#: directory name, and the packer has to agree with it rather than with a constant.
RUNNING_MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A bundle built once, from a stub `dist/`, for the tests below to read."""
    root = tmp_path_factory.mktemp("mcpb")
    dist = root / "dist"
    _stub_wheel(dist)
    out = root / "out"

    result = subprocess.run(
        [sys.executable, str(PACKER),
         "--dist", str(dist), "--out", str(out),
         "--python-versions", RUNNING_MINOR, "--skip-dependencies"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"the packer exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )

    built = out / f"pyjab-mcp-{pyjab_mcp.__version__}.mcpb"
    assert built.is_file(), f"the packer did not write {built}:\n{result.stdout}"
    return built


def bundle_members(bundle: Path):
    with zipfile.ZipFile(bundle) as archive:
        return set(archive.namelist())


def bundle_manifest(bundle: Path):
    with zipfile.ZipFile(bundle) as archive:
        return json.loads(archive.read(BUNDLE_MANIFEST).decode("utf-8"))


def loaded_entry_script(tmp_path, source: str):
    """Import the entry script *as the bundle ships it*, without starting the server."""
    path = tmp_path / "bundled-entry.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("bundled_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bundle_manifest_carries_the_package_version(bundle):
    """The version has one authority. A second place it lives is a second place to drift."""
    document = bundle_manifest(bundle)

    assert document["version"] == pyjab_mcp.__version__


def test_the_bundle_declares_windows_only(bundle):
    """Java Access Bridge is a Windows API; installing this anywhere else is a dead end."""
    document = bundle_manifest(bundle)

    assert document["compatibility"]["platforms"] == ["win32"]


def test_the_entry_point_the_manifest_names_is_inside_the_zip(bundle):
    """A manifest naming a file the bundle does not contain fails on the user's machine.

    The launch command is checked in the same place because it is the other half of the same
    fact: the host runs `command args`, and both have to survive into the bundle.
    """
    document = bundle_manifest(bundle)
    members = bundle_members(bundle)
    launch = document["server"]["mcp_config"]

    assert document["server"]["entry_point"] in members, (
        f"the manifest names {document['server']['entry_point']!r}, which is not in the zip"
    )
    assert launch["command"] == "python", (
        "no interpreter is bundled, so the command has to be the `python` on the user's PATH"
    )
    assert launch["args"] == ["${__dirname}/" + document["server"]["entry_point"]], (
        "the args have to address the bundle with ${__dirname}, or the launch path is wrong "
        "wherever the host unpacks it"
    )


def test_the_entry_script_looks_where_the_packer_vendored(bundle, tmp_path):
    """The two halves of "the dependencies are on sys.path", asserted against the artifact.

    The entry script computes ``vendor/cp3XX`` from ``sys.version_info`` and prepends it; the
    packer installs into a directory of its own naming. If they disagree the bundle fails
    inside a native import on the user's machine, which is the failure hardest to read.
    """
    with zipfile.ZipFile(bundle) as archive:
        source = archive.read(BUNDLE_ENTRY).decode("utf-8")
    entry = loaded_entry_script(tmp_path, source)

    vendored = "server/" + entry.vendor_name().replace(os.sep, "/")
    members = bundle_members(bundle)
    inside = sorted(member for member in members if member.startswith(vendored + "/"))

    assert inside, (
        f"the entry script prepends {vendored!r}, which the bundle does not contain. It "
        f"carries {sorted(m for m in members if m.startswith('server/vendor'))[:3]} instead."
    )
    assert any(member.endswith("/pyjab_mcp/__init__.py") for member in inside), (
        f"{vendored!r} is in the bundle but does not hold the package: {inside[:5]}"
    )


def test_the_bundle_ships_the_tracked_entry_script(bundle):
    """What runs is what is in the repository: a generated copy is a copy nobody reviewed."""
    with zipfile.ZipFile(bundle) as archive:
        shipped = archive.read(BUNDLE_ENTRY)

    assert shipped == ENTRY_SCRIPT.read_bytes()


def test_the_bundle_carries_no_local_only_file_and_no_bytecode(bundle):
    """The bundle is a thing a stranger installs, so it may not carry maintainer notes.

    The names come from the guard that keeps them out of the sdist and out of git rather than
    being repeated here: there is one list of what stays local, and this is the third
    mechanism that has to agree with it.
    """
    from tools.check_local_only_files import LOCAL_ONLY

    members = bundle_members(bundle)
    leaked = []
    for local in LOCAL_ONLY:
        stripped = local.rstrip("/")
        leaked += [m for m in members if m == stripped or m.startswith(stripped + "/")]
    bytecode = sorted(
        member for member in members
        if "__pycache__" in member.split("/") or member.endswith((".pyc", ".pyo"))
    )

    assert leaked == [], f"local-only files are inside the bundle: {leaked}"
    assert bytecode == [], (
        f"compiled bytecode is inside the bundle: {bytecode[:5]}. It is platform- and "
        "version-locked, and it is not what was reviewed."
    )


def test_the_packer_refuses_a_staged_tree_that_carries_a_local_only_file(tmp_path):
    """The artifact-level check, proved able to fail rather than trusted.

    The end-to-end assertion above is necessarily about what *this* repository happens to
    contain, and a maintainer note is not in the working tree of a checkout that ignores it --
    so the predicate is tested directly, with a tree that does contain one.
    """
    import tools.build_mcpb as packer
    from tools.check_local_only_files import LOCAL_ONLY

    (tmp_path / LOCAL_ONLY[0].rstrip("/")).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / LOCAL_ONLY[0].rstrip("/")).write_text("maintainer notes\n", encoding="utf-8")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "__pycache__").mkdir()
    (tmp_path / "server" / "entry.pyc").write_text("", encoding="utf-8")

    problems = packer.staged_problems(tmp_path)

    assert any("LOCAL-ONLY FILE IN THE BUNDLE" in problem for problem in problems), problems
    assert any("BUILD ARTEFACT IN THE BUNDLE" in problem for problem in problems), problems


def test_the_packer_says_how_to_make_a_dist_when_there_is_none(tmp_path):
    """An empty `dist/` must be a sentence, not an empty bundle.

    "I could not find the wheel" and "there is no wheel" both have to end the build: the
    packer bundles the *built* artifact and nothing from the working tree, so a silent empty
    bundle is the worst outcome available to it.
    """
    empty = tmp_path / "dist"
    empty.mkdir()

    result = subprocess.run(
        [sys.executable, str(PACKER), "--dist", str(empty), "--out", str(tmp_path / "out"),
         "--python-versions", RUNNING_MINOR, "--skip-dependencies"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"the packer packed an empty dist/:\n{output}"
    assert "python -m build" in output, (
        "the refusal has to name the command that fixes it, or the reader is left to guess"
    )
    if (tmp_path / "out").exists():
        assert not list((tmp_path / "out").glob("*.mcpb")), (
            "a refusal that still leaves a bundle behind is not a refusal"
        )


def test_the_manifest_validator_refuses_a_field_the_schema_does_not_document(tmp_path):
    """`additionalProperties: false` means an invented field is a refusal, not a warning."""
    import tools.build_mcpb as packer

    document = packer.manifest(pyjab_mcp.__version__)
    document["permissions"] = {"filesystem": "read"}

    problems = packer.check_manifest(document)

    assert any("UNDOCUMENTED FIELD" in problem for problem in problems), problems


def test_the_manifest_validator_refuses_a_manifest_missing_a_required_field():
    """Every field the schema requires has to be present, `author` and `server` included."""
    import tools.build_mcpb as packer

    document = packer.manifest(pyjab_mcp.__version__)
    del document["author"]

    problems = packer.check_manifest(document)

    assert any("MISSING FIELD: author" in problem for problem in problems), problems

