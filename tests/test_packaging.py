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

    Off by default because it needs a downloaded binary and a network round trip, and nothing
    under `.github/` sets `PYJAB_MCP_REGISTRY`, so this test runs only when a person sets it.
    The release's protection is the same validator run directly: `publish-registry.yml` has a
    `mcp-publisher validate` step of its own before `publish`, which is why a schema rejection
    cannot reach PyPI. That is not this test. Run this one by hand with `PYJAB_MCP_REGISTRY=1`
    and `mcp-publisher` on `PATH`; `docs/RELEASING.md` documents it.
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


def _stub_wheel(dist: Path, requires=()):
    """A wheel pip installs offline with no dependencies: what `python -m build` would leave.

    Real enough for pip (``METADATA``, ``WHEEL``, ``RECORD``), and it declares the package's
    own version, so the packer's "the bundle's contents and its manifest agree" check is
    exercised rather than bypassed. ``requires`` writes ``Requires-Dist`` lines, which is how a
    test gives the closure check something to read.
    """
    version = pyjab_mcp.__version__
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / f"pyjab_mcp-{version}-py3-none-any.whl"
    info = f"pyjab_mcp-{version}.dist-info"
    declared = "".join(f"Requires-Dist: {text}\n" for text in requires)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pyjab_mcp/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr(
            f"{info}/METADATA",
            f"Metadata-Version: 2.1\nName: pyjab-mcp\nVersion: {version}\n{declared}\n",
        )
        archive.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: tests/test_packaging.py\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{info}/RECORD", "")
    return wheel


def _write_dist(root: Path, name: str, version: str, requires=()) -> Path:
    """An installed distribution as `pip install --target` leaves one: a `*.dist-info`."""
    root.mkdir(parents=True, exist_ok=True)
    info = root / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    declared = "".join(f"Requires-Dist: {text}\n" for text in requires)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{declared}\n",
        encoding="utf-8",
    )
    return info


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


def vendored_tags(bundle: Path):
    """The ``cp3XX`` trees the zip actually carries, read out of the archive."""
    return sorted({
        member.split("/")[2]
        for member in bundle_members(bundle)
        if member.startswith("server/vendor/cp") and member.count("/") >= 3
    })


def unpacked_entry_script(bundle: Path, tmp_path):
    """Unpack the bundle's ``server/`` tree and import the entry script from *there*.

    From there rather than from the repository, because the script addresses its vendored tree
    relative to its own file: a copy in a scratch directory would look for ``vendor/`` in that
    directory and find nothing, which is a different question from the one being asked.
    """
    with zipfile.ZipFile(bundle) as archive:
        for member in archive.namelist():
            if member.startswith("server/"):
                archive.extract(member, tmp_path)
    path = tmp_path / "server" / "entry.py"
    spec = importlib.util.spec_from_file_location("bundled_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpacked_tree(bundle: Path, tmp_path, tag: str) -> Path:
    """One ``server/vendor/cp3XX`` tree of the bundle, on disk as the host unpacks it."""
    root = tmp_path / "bundles" / tag
    with zipfile.ZipFile(bundle) as archive:
        for member in archive.namelist():
            prefix = f"server/vendor/{tag}/"
            if member.startswith(prefix):
                archive.extract(member, root)
    return root / "server" / "vendor" / tag


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
    entry = unpacked_entry_script(bundle, tmp_path)
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


def test_the_bundle_carries_no_build_machine_path(bundle):
    """``pip install --target`` records the local wheel it used; a stranger gets that path.

    Found by the reviewer of #37 on the *real* bundle: four copies, one per vendored tree,
    each ``server/vendor/cp3XX/pyjab_mcp-<version>.dist-info/direct_url.json`` naming the
    builder's ``dist/`` directory. Nothing imports it, which is exactly why the rule has to be
    mechanical -- and this is the end-to-end half, read out of the archive the host installs.
    """
    leaked = sorted(
        member for member in bundle_members(bundle) if member.endswith("/" + "direct_url.json")
    )

    assert leaked == [], (
        f"the bundle carries {len(leaked)} direct_url.json file(s), and each one records where "
        f"the machine that built the bundle kept the wheel: {leaked}"
    )


def test_the_packer_refuses_a_staged_tree_that_carries_a_build_machine_path(tmp_path):
    """The predicate behind the assertion above, proved able to fail rather than trusted."""
    import tools.build_mcpb as packer

    dist_info = tmp_path / "server" / "vendor" / "cp310" / "pyjab_mcp-0.1.2.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(
        '{"url": "file:///home/runner/work/pyjab-mcp/pyjab-mcp/dist/pyjab_mcp-0.1.2-py3-none-any.whl"}',
        encoding="utf-8",
    )

    problems = packer.staged_problems(tmp_path)

    assert any("BUILD MACHINE PATH IN THE BUNDLE" in problem for problem in problems), problems


def test_staging_a_tree_drops_the_recorded_wheel_path(tmp_path):
    """The removal that lets a correct build pass the refusal above -- and only that file."""
    import tools.build_mcpb as packer

    dist_info = tmp_path / "cp310" / "pyjab_mcp-0.1.2.dist-info"
    dist_info.mkdir(parents=True)
    recorded = dist_info / "direct_url.json"
    recorded.write_text('{"url": "file:///build/machine/dist/pyjab_mcp-0.1.2-py3-none-any.whl"}',
                        encoding="utf-8")
    (dist_info / "METADATA").write_text("Name: pyjab-mcp\n", encoding="utf-8")

    removed = packer.drop_build_machine_paths(tmp_path)

    assert removed == ["cp310/pyjab_mcp-0.1.2.dist-info/direct_url.json"], removed
    assert not recorded.exists()
    assert (dist_info / "METADATA").is_file(), "only pip's record of the path is removed"


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

    document = packer.manifest(pyjab_mcp.__version__, packer.supported_python_versions())
    document["permissions"] = {"filesystem": "read"}

    problems = packer.check_manifest(document)

    assert any("UNDOCUMENTED FIELD" in problem for problem in problems), problems


def test_the_manifest_validator_refuses_a_manifest_missing_a_required_field():
    """Every field the schema requires has to be present, `author` and `server` included."""
    import tools.build_mcpb as packer

    document = packer.manifest(pyjab_mcp.__version__, packer.supported_python_versions())
    del document["author"]

    problems = packer.check_manifest(document)

    assert any("MISSING FIELD: author" in problem for problem in problems), problems


# ---------------------------------------------------------------------------------
# What the bundle claims about Python, against what it carries
# ---------------------------------------------------------------------------------
#
# The failure this closes is not a red test anywhere else: a bundle that advertises "3.10 or
# newer" while carrying trees for 3.10-3.13 installs happily on 3.14 and then dies inside a
# native import, where the message names a DLL. The declared range is therefore read out of the
# *built* manifest and compared with the directories in the *built* zip -- the claim and the
# artefact, in both directions -- rather than with anything the packer says about itself.

#: The manifest's list of the minors it carries: a parenthesised, comma-separated run of version
#: numbers -- one entry is a list too. Anchored on that shape rather than on a sentence, so the
#: prose around it is free.
DECLARED_MINORS = re.compile(r"\((\d+\.\d+(?:,\s*\d+\.\d+)*)\)")


@pytest.fixture(scope="module")
def declared_bundle(tmp_path_factory):
    """A bundle vendored for exactly the minors `pyproject.toml` declares -- the release shape.

    Separate from `bundle` above, which vendors for the interpreter running the tests so the
    entry script and the packer can be checked against each other on any machine. This one is
    what a release produces, and it is the only one whose declared range can be compared with
    the project's own claim.
    """
    import tools.build_mcpb as packer

    root = tmp_path_factory.mktemp("mcpb-declared")
    dist = root / "dist"
    _stub_wheel(dist)
    out = root / "out"

    result = subprocess.run(
        [sys.executable, str(PACKER),
         "--dist", str(dist), "--out", str(out),
         "--python-versions", ",".join(packer.supported_python_versions()),
         "--skip-dependencies"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, (
        f"the packer exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    return out / f"pyjab-mcp-{pyjab_mcp.__version__}.mcpb"


def test_the_bundles_declared_python_support_is_what_it_vendors(declared_bundle, tmp_path):
    """The declared range, the trees, the closure they satisfy, and the prose: one fact.

    Directory *names* alone were not enough. A tree can be present under the right name and be
    unable to import its own closure -- `exceptiongroup` never reached the 3.10 tree, because
    `pip --python-version` does not move environment markers -- and that is the state this
    compares against now, tree by tree. The prose is checked in the three places a user reads
    it, so a tree added or dropped cannot leave the claim behind.
    """
    import tools.build_mcpb as packer

    document = bundle_manifest(declared_bundle)
    described = document["long_description"]

    matches = DECLARED_MINORS.findall(described)
    assert len(matches) == 1, (
        "the manifest no longer carries exactly one explicit list of the CPython minors it "
        f"vendors for, so the claim cannot be compared with the artifact: {matches!r}"
    )
    declared = [text.strip() for text in matches[0].split(",")]
    carried = ["%s.%s" % (tag[2], tag[3:]) for tag in vendored_tags(declared_bundle)]

    assert declared == carried, (
        f"the manifest says it carries {declared} and the zip carries {carried}. A user on an "
        "interpreter between those two meets an ImportError from a native module."
    )
    assert packer.supported_python_versions() == declared, (
        "the manifest and pyproject.toml disagree about which Pythons this project supports, so "
        "one of the two has to move"
    )

    # The trees themselves, unpacked: a name is not a closure. The fixture's stub distribution
    # declares no requirements, so what this catches end to end is a tree the check cannot read;
    # the substantive evidence for the closure is
    # `test_the_packer_fills_a_tree_the_target_markers_left_short` and the real build.
    for tag in carried:
        tree = unpacked_tree(declared_bundle, tmp_path, tag)
        remaining = packer.unsatisfied_requirements(tree, tag)
        assert remaining == [], (
            f"{tag} does not satisfy its own target's requirements: "
            + "; ".join(item.complaint for item in remaining)
        )

    stated = packer.python_range(declared)
    assert stated in described, (
        f"the manifest's prose does not state the range it carries ({stated!r}): {described!r}"
    )
    for page in ("README.md", "docs/CLIENTS.md", "CHANGELOG.md"):
        text = (REPO_ROOT / page).read_text(encoding="utf-8")
        assert stated in text, (
            f"{page} does not state the range the bundle covers ({stated!r}). The bundle's "
            "prerequisites are given there, so the prose has to move with the trees."
        )
    # ...and the range is about the *bundle*: the install path is not bounded by it, which is
    # what the en-dash claim in docs/CLIENTS.md used to contradict.
    for page in ("README.md", "docs/CLIENTS.md"):
        text = (REPO_ROOT / page).read_text(encoding="utf-8")
        assert "3.10 or newer" in text, (
            f"{page} no longer says the `pip install` path is `3.10 or newer`, so the only "
            f"range it states is the bundle's ({stated!r}) and the two read as the same claim."
        )


# ---------------------------------------------------------------------------------
# What the bundle's text promises, in the pages a user reads
# ---------------------------------------------------------------------------------
#
# Three claims a reader acts on, and none is visible to a test that reads only the code or the
# artifact: the installer launches the bare `python` on `PATH`, so that interpreter has to
# exist; `where.exe python` is how a user sees which one it is; and v0.1.0-v0.1.2 carry no
# assets at all, so no text may claim the bundle is on releases that predate it.
#
# The first two are pinned as the **sentences this repository agreed to**, verbatim, in all
# three places an installer reads: the README's bundle section, the client guide's, and the
# manifest's `long_description` -- what the host shows at install time. A wording change is then
# a deliberate edit here rather than something a rewrite does by accident, and a paraphrase
# around two nearby names cannot satisfy it: an earlier version of this pin asked only for
# `python` near `PATH`, which "The interpreter does *not* have to be the `python` on `PATH`"
# also supplies. The availability sentence is pinned the same way in both pages, and the
# *false* form of it is refused by a rule deliberately broader than one wording.

#: The agreed sentence about the interpreter, verbatim, in all three places. No trailing period
#: on purpose, so the same string can lead a bold paragraph, a bullet, and a clause in the guide.
INTERPRETER_ON_PATH = "The interpreter has to be the `python` on `PATH`"

#: The check that answers "which interpreter will it use?" on the Windows the bundle runs on.
#: `where.exe`, not `where`: in PowerShell `where` is an alias for `Where-Object`, so the bare
#: verb filters objects instead of printing a path.
PATH_CHECK = "where.exe python"

#: The agreed sentence about which releases carry the bundle, verbatim in both pages. It names
#: the three releases that have none -- the earlier wording ("the release that introduces it")
#: described a release nobody could identify while every release on the releases page carried no
#: assets -- and the second clause is true today (no release has been cut since) and after the
#: release that does carry one.
AVAILABILITY_SENTENCE = (
    "v0.1.0–v0.1.2 predate the bundle and carry no assets; every release cut since carries one."
)

#: An "every release" applied to something. The subject may carry up to three words between the
#: quantifier and the noun, which is what "every published release" and "every GitHub release"
#: need; a possessive ("every release's notes") is not a claim about what a release carries, so
#: it is excluded rather than flagged.
UNIVERSAL_RELEASE = re.compile(
    r"\b(?:every|each|all|any)\s+(?:\w+\s+){0,3}releases?\b(?!['’])", re.IGNORECASE
)

#: The bundle, by any of the names these pages use for it.
BUNDLE = re.compile(r"bundle|mcpb", re.IGNORECASE)


def shipped_page_section(page: str, heading: str) -> str:
    """One heading's own text, to the next heading of the same or a higher level.

    Line numbers move whenever the prose above them does, and slicing to the next `##` alone
    would let a later `###` inside the same H2 block stand in for the section this is about.
    """
    text = (REPO_ROOT / page).read_text(encoding="utf-8")
    assert heading in text, (
        f"{page} no longer has a {heading!r} heading, so the bundle's text has no single place "
        "to be checked"
    )
    start = text.index(heading)
    following = re.search(r"(?m)^#{2,6}\s", text[start + len(heading):])
    end = start + len(heading) + following.start() if following else len(text)
    return text[start:end]


def mutable_changelog_text() -> str:
    """`CHANGELOG.md` without the sections that are already released.

    A released section is published inside its sdist and frozen by hash, so its text is not this
    pin's to police. The *next* release's notes are written under `## Unreleased`, and a claim
    there is published by the release this change is preparing: the false one this test was
    written for sat in exactly that section. "Released" is read through the immutable guard's own
    `load_record()` and `sections()` rather than re-derived, so there is one definition of it.
    """
    import tools.check_changelog_immutable as immutable

    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for version, section in immutable.sections(text).items():
        if version in immutable.load_record():
            text = text.replace(section, "")
    return text


def universal_release_claims(flat: str) -> "list[str]":
    """Sentences that say *every* release carries the bundle, which v0.1.0-v0.1.2 do not.

    One sentence at a time, split where a period is followed by whitespace and a sentence
    opening -- so `pyjab-mcp-<version>.mcpb` and `v0.1.2` are not cut in half -- and a sentence is
    a complaint when it both quantifies over releases and mentions the bundle. The agreed
    sentence is removed first: it contains "every release cut since carries one", which is true
    and is pinned by its own assertion, so it cannot be what this looks for. That removal is also
    why this does not exempt a generic "since"/"after": "every release since v0.1.0 carries the
    bundle" reads like the truth and is false, because v0.1.1 and v0.1.2 carry nothing.
    """
    without_agreed = flat.replace(AVAILABILITY_SENTENCE, "")
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=\.)\s+(?=[A-Z*`])", without_agreed)
        if UNIVERSAL_RELEASE.search(sentence) and BUNDLE.search(sentence)
    ]


def test_the_bundle_pages_say_which_interpreter_runs_it_and_which_releases_carry_it(
    declared_bundle,
):
    """The claims the bundle's text makes, in the places a user meets them.

    `declared_bundle` is the built artifact, so the manifest half is read out of the zip a host
    would install rather than from the packer's own constant: a constant and the file it writes
    can disagree, and only one of the two is what a user sees.
    """
    pages = {
        "README.md": shipped_page_section("README.md", "### Or install the bundle"),
        "docs/CLIENTS.md": shipped_page_section("docs/CLIENTS.md", "### The `.mcpb` bundle"),
    }
    described = " ".join(bundle_manifest(declared_bundle)["long_description"].split())

    for page, section in pages.items():
        flat = " ".join(section.split())
        assert INTERPRETER_ON_PATH in flat, (
            f"{page}'s bundle section no longer carries {INTERPRETER_ON_PATH!r}. The manifest "
            "launches bare `python`, so a machine with CPython installed but not on `PATH` gets "
            "a client that shows no tools -- and no refusal that names the cause, because "
            "nothing started an interpreter to print one."
        )
        assert PATH_CHECK in flat, (
            f"{page}'s bundle section no longer gives the check that shows which interpreter "
            f"that is ({PATH_CHECK!r}), the only way a user can see it before installing."
        )
        assert AVAILABILITY_SENTENCE in flat, (
            f"{page}'s bundle section no longer carries {AVAILABILITY_SENTENCE!r}. Which "
            "releases carry the bundle is a deliberate claim, not something a rewrite should be "
            "able to change silently."
        )

    assert INTERPRETER_ON_PATH in described, (
        f"the manifest's `long_description` no longer carries {INTERPRETER_ON_PATH!r}. That is "
        "the text the host shows at install time, and the one nobody re-reads."
    )
    assert PATH_CHECK in described, (
        f"the manifest's `long_description` no longer gives {PATH_CHECK!r}, the check that "
        'turns "no tools appeared" into a named cause.'
    )

    claimed: "list[str]" = []
    for page in ("README.md", "docs/CLIENTS.md"):
        flat = " ".join((REPO_ROOT / page).read_text(encoding="utf-8").split())
        claimed += [f"{page}: {sentence}" for sentence in universal_release_claims(flat)]
    flat_changelog = " ".join(mutable_changelog_text().split())
    claimed += [
        f"CHANGELOG.md (unreleased): {sentence}"
        for sentence in universal_release_claims(flat_changelog)
    ]

    assert claimed == [], (
        "a shipped page claims that every release carries the bundle, and the three published "
        "releases carry no assets at all: " + " | ".join(claimed) + ". Name the releases that "
        "predate it instead -- v0.1.0-v0.1.2 predate the bundle and carry no assets."
    )


def test_the_closure_check_reads_the_target_interpreter_not_the_build_one(tmp_path):
    """The check that closes the 3.10 defect, proved to look at the target's markers.

    `anyio` requires `exceptiongroup` under `python_version < "3.11"`, and the tree below does
    not contain it. One tree, two targets: what the *build machine* thinks the marker says is
    irrelevant -- and the pair of assertions fails on a builder of any version, because the
    machine's own answer would be the same for both minors.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp310"
    _write_dist(tree, "anyio", "4.15.1",
                requires=('exceptiongroup>=1.0.2; python_version < "3.11"',))

    missing = packer.unsatisfied_requirements(tree, "3.10")
    print(missing)

    assert [item.name for item in missing] == ["exceptiongroup"], (
        "the check did not report the dependency a 3.10 interpreter needs, so it is reading the "
        "build interpreter's markers: " + repr(missing)
    )
    assert "anyio" in missing[0].complaint and "exceptiongroup>=1.0.2" in missing[0].complaint, (
        "the complaint has to name the distribution that asked and the requirement as written"
    )
    assert packer.unsatisfied_requirements(tree, "3.12") == [], (
        "the same tree must satisfy 3.12's markers, or the check is not reading the target at "
        "all -- it is applying one interpreter's answer to every tree"
    )


def test_the_closure_check_follows_an_extra_to_its_requirements(tmp_path):
    """`mcp` requires `pyjwt[crypto]`; `cryptography` is only reachable through that extra."""
    import tools.build_mcpb as packer

    tree = tmp_path / "cp312"
    _write_dist(tree, "mcp", "2.3.0", requires=("pyjwt[crypto]>=2.10.1",))
    _write_dist(tree, "pyjwt", "2.15.1",
                requires=('cryptography>=3.4.0; extra == "crypto"',))

    missing = packer.unsatisfied_requirements(tree, "3.12")
    print(missing)

    assert [item.name for item in missing] == ["cryptography"], (
        "a requirement behind an extra the tree asks for is part of the closure: " + repr(missing)
    )


def test_the_closure_check_follows_extras_to_a_fixed_point(tmp_path):
    """One pass is not enough: the second extra is only asked once the first one is known.

    `B[F]; extra == "E"` is false until something asks for `E` on `B`, so a single pass over the
    metadata loses everything below the first level. This is the reviewer's example, at depth
    two: `mcp -> pyjwt[crypto] -> sqlalchemy[asyncio] -> greenlet`.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp312"
    _write_dist(tree, "mcp", "2.3.0", requires=("pyjwt[crypto]>=2.10.1",))
    _write_dist(tree, "pyjwt", "2.15.1",
                requires=('sqlalchemy[asyncio]>=2.0; extra == "crypto"',))
    _write_dist(tree, "sqlalchemy", "2.0.44",
                requires=('greenlet>=1; extra == "asyncio"',))

    missing = packer.unsatisfied_requirements(tree, "3.12")
    print(missing)

    assert [item.name for item in missing] == ["greenlet"], (
        "the extras walk stopped at the first level: " + repr(missing)
    )


def test_the_closure_check_reports_a_present_but_violating_version(tmp_path):
    """Presence is not satisfaction: a tree contradicting its own metadata is reported.

    Measured as unreachable in the four shipped trees today -- every marker-guarded requirement
    there is a floor, and installing by name picks the newest version a minor accepts -- but it
    goes live the moment a dependency gains a marker-guarded upper bound, and the failure would
    be the shape of the one this check was written for.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp312"
    _write_dist(tree, "consumer", "1.0",
                requires=('dep>=2.0,<2.5; python_version >= "3.11"',))
    _write_dist(tree, "dep", "2.9")

    missing = packer.unsatisfied_requirements(tree, "3.12")
    print(missing)

    assert [item.name for item in missing] == ["dep"], (
        "`dep 2.9` does not satisfy `dep>=2.0,<2.5`, and the tree must be reported: "
        + repr(missing)
    )
    assert "2.9" in missing[0].complaint and "<2.5" in missing[0].complaint, missing[0].complaint
    assert missing[0].specifier == "<2.5,>=2.0", (
        "the specifiers have to travel with the report, or the fill cannot repair the version: "
        + repr(missing[0])
    )


def test_the_closure_check_answers_for_the_whole_patch_range(tmp_path):
    """A marker on `python_full_version` is about a patch level the user picks, not us.

    A bundle is built once and installed by whoever turns up, so "3.10" has to mean every
    3.10.x: the requirement below is needed by 3.10.2 and by 3.10.4 alike, and answering at
    `.0` alone -- or at whatever patch this build machine happens to run -- would miss it.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp310"
    _write_dist(tree, "consumer", "1.0", requires=(
        'floor>=1; python_full_version >= "3.10.2"',
        'window>=1; python_full_version >= "3.10.2" and python_full_version < "3.10.5"',
        'old>=1; python_full_version < "3.10.2"',
    ))

    missing = sorted(item.name for item in packer.unsatisfied_requirements(tree, "3.10"))
    print(missing)

    assert missing == ["floor", "old", "window"], (
        "every patch level of the target is a user's, so a marker that is true at any of them "
        "has to be carried -- `old` at 3.10.0, `floor` from 3.10.2, `window` only between "
        "3.10.2 and 3.10.4: " + repr(missing)
    )


def test_the_closure_check_refuses_to_substitute_the_build_machine(tmp_path):
    """A marker naming a variable the target cannot know is carried, never filled in from here.

    `platform_release`, `platform_version` and `implementation_version` describe the machine the
    user will run on. `packaging` falls back to this machine's values for any variable it is not
    given -- the same wrong-machine category as the defect this check exists for -- so a marker
    that names one has to be answered conservatively and said out loud.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp312"
    _write_dist(tree, "consumer", "1.0",
                requires=('kernel>=1; platform_release == "not-this-build-machine"',))

    missing = packer.unsatisfied_requirements(tree, "3.12")
    print(missing)

    assert [item.name for item in missing] == ["kernel"], (
        "the marker was evaluated against this machine's `platform_release`: " + repr(missing)
    )
    assert "platform_release" in missing[0].complaint, (
        "the complaint has to say which unknown it carried rather than judged: "
        + missing[0].complaint
    )


def test_the_closure_check_unfolds_a_continued_requirement(tmp_path):
    """A folded `Requires-Dist` parses as a truncated one, and a truncated one reads as met.

    `baz>=1,` continued on the next line as ` <2` is `baz>=1,<2`. Unfolded it is `baz>=1`, which
    `baz 2.5` satisfies -- so without the unfolding the tree would be reported clean while its
    own metadata says otherwise.
    """
    import tools.build_mcpb as packer

    tree = tmp_path / "cp312"
    info = _write_dist(tree, "consumer", "1.0")
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: consumer\nVersion: 1.0\n"
        "Requires-Dist: baz>=1,\n <2\n\n",
        encoding="utf-8",
    )
    _write_dist(tree, "baz", "2.5")

    missing = packer.unsatisfied_requirements(tree, "3.12")
    print(missing, [str(item.specifier) for item in missing])

    assert [item.name for item in missing] == ["baz"], (
        "the continuation line was dropped, so `baz 2.5` looked like it satisfied `baz>=1`: "
        + repr(missing)
    )
    assert missing[0].specifier == "<2,>=1", missing[0].specifier


def test_the_packer_refuses_an_empty_python_version_list(tmp_path):
    """`--python-versions ,` used to build a bundle with no vendored trees at all."""
    dist = tmp_path / "dist"
    _stub_wheel(dist)

    result = subprocess.run(
        [sys.executable, str(PACKER), "--dist", str(dist), "--out", str(tmp_path / "out"),
         "--python-versions", ",", "--skip-dependencies"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"a bundle with no dependency trees was built:\n{output}"
    assert "NO PYTHON VERSIONS" in output, output
    if (tmp_path / "out").exists():
        assert not list((tmp_path / "out").glob("*.mcpb")), (
            "a refusal that still leaves a bundle behind is not a refusal"
        )


def test_the_packer_fills_a_tree_the_target_markers_left_short(tmp_path, monkeypatch):
    """The fix, offline: the check drives an explicit install and the build then passes.

    `pip --python-version` cannot be told to evaluate markers for another interpreter, so the
    distributions its resolver drops are installed *by name* -- and if a tree is still short
    after the rounds, the build refuses rather than shipping it. Both installers are replaced
    here, so this is the loop and the check, not the network.
    """
    import tools.build_mcpb as packer

    requirement = 'exceptiongroup>=1.0.2; python_version < "3.11"'
    dist = tmp_path / "dist"
    _stub_wheel(dist, requires=(requirement,))
    asked: "list[str]" = []

    def fake_tree(target, wheel, version, platform, dependencies):
        _write_dist(target, "pyjab-mcp", pyjab_mcp.__version__, requires=(requirement,))

    def fake_pip(target, version, platform, requirements, offline=False):
        asked.extend(str(name) for name in requirements)
        for name in requirements:
            # pip is given `name>=spec`, and what it installs is `name`.
            _write_dist(target, re.match(r"[A-Za-z0-9._-]+", str(name)).group(0), "1.3.0")

    monkeypatch.setattr(packer, "install_tree", fake_tree)
    monkeypatch.setattr(packer, "pip_install", fake_pip)

    bundle = packer.build(dist, tmp_path / "out", ["3.10"], dependencies=True)

    assert asked == ["exceptiongroup>=1.0.2"], (
        "the packer did not install the dependency the target's markers require, with the "
        "specifier the requirement carries. It asked pip for "
        f"{asked!r}"
    )
    assert ";" not in asked[0], (
        "the marker was passed to pip, which answers \"markers ... don't match your environment\" "
        "and installs nothing -- the wrong-machine behaviour this whole check exists to repair"
    )
    assert bundle.is_file(), "the build did not finish after filling the tree"


def test_the_packer_refuses_a_tree_it_cannot_satisfy(tmp_path, monkeypatch):
    """And when filling cannot succeed, the build stops -- an unsatisfied tree is not shipped."""
    import tools.build_mcpb as packer

    requirement = 'exceptiongroup>=1.0.2; python_version < "3.11"'
    dist = tmp_path / "dist"
    _stub_wheel(dist, requires=(requirement,))

    def fake_tree(target, wheel, version, platform, dependencies):
        _write_dist(target, "pyjab-mcp", pyjab_mcp.__version__, requires=(requirement,))

    def fake_pip(target, version, platform, requirements, offline=False):
        pass  # pip "succeeds" and installs nothing, which is the failure being pinned

    monkeypatch.setattr(packer, "install_tree", fake_tree)
    monkeypatch.setattr(packer, "pip_install", fake_pip)

    with pytest.raises(SystemExit) as caught:
        packer.build(dist, tmp_path / "out", ["3.10"], dependencies=True)

    message = str(caught.value)
    print(message)
    assert "UNSATISFIED DEPENDENCIES" in message
    assert "exceptiongroup" in message, "the refusal has to name what is missing"
    assert not list((tmp_path / "out").glob("*.mcpb")), (
        "a refusal that still leaves a bundle behind is not a refusal"
    )


def test_the_packer_refuses_a_target_platform_the_bundle_cannot_honour(tmp_path):
    """The target is not a free choice: the manifest, the prose and the refusal all assume it.

    `--platform win32` used to build: the manifest declared win32, the prose said 64-bit, and
    the zip carried 32-bit wheels that every declared interpreter refuses -- a bundle that
    contradicts itself and says so nowhere. Only `win_amd64` is supported, and the refusal says
    why rather than silently shipping something else.
    """
    dist = tmp_path / "dist"
    _stub_wheel(dist)

    result = subprocess.run(
        [sys.executable, str(PACKER), "--dist", str(dist), "--out", str(tmp_path / "out"),
         "--python-versions", RUNNING_MINOR, "--skip-dependencies", "--platform", "win32"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"a 32-bit bundle was built anyway:\n{output}"
    assert "UNSUPPORTED TARGET" in output and "win_amd64" in output, output
    if (tmp_path / "out").exists():
        assert not list((tmp_path / "out").glob("*.mcpb")), (
            "a refusal that still leaves a bundle behind is not a refusal"
        )


def test_the_entry_script_refuses_an_interpreter_the_bundle_does_not_carry(
    bundle, tmp_path, monkeypatch, capsys
):
    """The refusal has to name the interpreter, the trees present, and where to go instead.

    The bundle here vendors only for the interpreter running the tests, so asking it to run
    under another one is the case a user on an un-vendored minor meets. `sys.version_info` is
    what the shipped script reads, so that is what the test changes -- the loaded module is the
    file out of the zip, not a copy of the repository's.
    """
    entry = unpacked_entry_script(bundle, tmp_path)
    tags = vendored_tags(bundle)
    monkeypatch.setattr(sys, "version_info", (2, 7, 18, "final", 0))

    code = entry.main()
    message = capsys.readouterr().err
    print(message)

    assert code == 2, f"the entry script did not refuse an unsupported interpreter:\n{message}"
    assert "2.7.18" in message, "the message has to name the interpreter it found"
    for tag in tags:
        assert entry.minor(tag) in message, (
            f"the message does not name {tag}, which the bundle does carry:\n{message}"
        )
    assert "pip install pyjab-mcp" in message, (
        "the message has to say what to do instead, or it is a dead end"
    )
    assert message.count("\n") < 12, (
        "the refusal is longer than a refusal; it belongs on stderr in front of a person "
        "whose server just did not start"
    )


def test_the_entry_script_turns_a_tree_name_into_a_version_and_nothing_else():
    """A tag this project cannot produce is printed as itself, not as an invented version.

    The supported trees are all ``cp3XX``, so 3.10 is three digits and 3.9 would be two. A tree
    named anything else is not something the packer can have built, and the refusal says so
    rather than rendering `cp400` as `4.00`.
    """
    spec = importlib.util.spec_from_file_location("entry_module", ENTRY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.minor("cp310") == "3.10"
    assert module.minor("cp39") == "3.9"
    assert module.minor("cp400") == "cp400", (
        "a tag outside the shapes this project builds is reported as the tag, not as a version"
    )


def test_the_declared_minor_pattern_accepts_one_minor_as_well_as_many():
    """The pattern is about the *shape* of the claim, and one supported minor is a shape.

    A one-minor list would otherwise fail the comparison for the wrong reason -- "no list" --
    which reads as a missing sentence rather than as a range with one entry.
    """
    assert DECLARED_MINORS.findall("The minors are (3.10).") == ["3.10"]
    assert DECLARED_MINORS.findall("The minors are (3.10, 3.13).") == ["3.10, 3.13"]
    assert DECLARED_MINORS.findall("no version list here") == []


def test_the_cli_is_handed_the_manifest_inside_the_archive(bundle, monkeypatch):
    """The artifact is what gets checked, not the staging copy that the build deletes.

    `--validate-with-mcpb` used to be given the staging `manifest.json`, which is gone by the
    time the build ends -- so the file a host installs was never the file that was validated.
    The CLI is replaced here: what is asserted is *which bytes* it would be handed, which is the
    part of this that a test can hold on to.
    """
    import tools.build_mcpb as packer

    monkeypatch.setattr(packer.shutil, "which", lambda name: "/fake/" + name)
    seen = {}

    def fake_run(command, **kwargs):
        if "validate" in command:
            seen["command"] = command
            seen["bytes"] = Path(command[-1]).read_bytes()
        return subprocess.CompletedProcess(command, 0, "Manifest schema validation passes!", "")

    lines = packer.validate_with_mcpb(bundle, run=fake_run)
    print(lines)

    with zipfile.ZipFile(bundle) as archive:
        inside = archive.read("manifest.json")
    assert seen.get("bytes") == inside, (
        "the CLI was not handed the manifest that is inside the archive, so the bundle a host "
        "installs is not the bundle whose manifest was validated"
    )
    assert any("validate (the manifest inside the archive)" in line for line in lines), lines

