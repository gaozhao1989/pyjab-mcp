#!/usr/bin/env python
"""Build the MCPB bundle: one file a user can install without a build step.

Why this exists, and why it is a script rather than a workflow step
------------------------------------------------------------------

An `.mcpb` is a zip with a `manifest.json` in it, a `server/` directory, and the server's
dependencies vendored alongside so the user needs no package manager. Four things about it are
worth a test, and none of them can be tested inside a `run:` block in YAML: the version it
declares, the platform it declares, the interpreter trees it vendors, and what must *not* be in
it. So the logic lives here and the workflow only invokes it -- the same rule the two guards
before this one follow (`tools/check_release_l3.py`, `tools/check_release_unpublished.py`).

What it does
------------

1. finds the wheel `python -m build` produced in ``dist/``, and refuses with a clear message
   when there is none, because a silent empty bundle is the worst possible outcome;
2. writes ``manifest.json`` with the version taken from ``pyjab_mcp.__version__`` and re-reads
   the file it wrote to assert they agree -- the version has exactly one authority in this
   repository and the manifest must not become a second one;
3. copies the tracked ``mcpb/entry.py`` in as ``server/entry.py``;
4. installs the wheel, for each supported CPython minor, into ``server/vendor/cp3XX``, with the
   dependency closure resolved for **win_amd64** rather than for the machine doing the build;
5. validates the manifest's fields against the documented vocabulary, and optionally against
   Anthropic's own CLI;
6. zips it and prints the path, the size and an inventory of what went in.

It needs one thing at run time and no more: ``pip``, to *install the dependencies*. The rest is
the standard library, so this is testable offline with a stub ``dist/``.

Why one vendored tree per interpreter
-------------------------------------

The dependency closure contains compiled extensions -- ``pydantic-core``, ``cffi``,
``rpds-py``, ``cryptography`` and ``pywin32`` -- and their wheels are tagged for one CPython
*minor* version. A bundle vendored for 3.10 alone would import-fail on 3.12, so "Python 3.10+"
is only a true statement about this bundle if the trees for every supported minor are inside
it. The list of supported minors is read out of ``pyproject.toml``'s classifiers rather than
repeated here.

Nothing is bundled that the machine already has to provide: **no Python interpreter**. This
server drives Java Access Bridge, so a JDK is a prerequisite no bundle can remove, and a
".mcpb" whose whole promise is "no toolchain needed" would be a false promise here. The
manifest and the README state what is actually required.

Usage
-----

    python -m build
    python tools/build_mcpb.py --dist dist --out dist

    # the offline path the tests use: no dependency closure is fetched
    python tools/build_mcpb.py --dist dist --out dist --skip-dependencies
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

# The local-only list is defined once, in the guard that keeps those files out of the sdist,
# and imported here so the bundle cannot drift from it. Running this file as a script puts
# tools/ itself on sys.path; importing it as tools.build_mcpb needs the repository root.
try:
    from tools.check_local_only_files import LOCAL_ONLY
except ImportError:  # pragma: no cover - the script path
    sys.path.insert(0, str(REPO_ROOT))
    from tools.check_local_only_files import LOCAL_ONLY

#: The tracked entry script, copied into the bundle verbatim.
ENTRY_SCRIPT = REPO_ROOT / "mcpb" / "entry.py"

#: Where the vendored dependency trees go inside the bundle, and what the entry script looks
#: for. The two are one fact: `mcpb/entry.py` computes ``vendor/cp3XX`` from ``sys.version_info``.
SERVER_DIR = "server"
VENDOR_DIR = "server/vendor"
ENTRY_IN_BUNDLE = "server/entry.py"

#: The wheel platform the bundle targets. The bundle is for Windows, and a wheel tagged
#: ``win_amd64`` is what a 64-bit Python on Windows loads.
DEFAULT_PLATFORM = "win_amd64"

#: Dependencies pip cannot see when it is asked for another platform.
#:
#: Environment markers are evaluated against the *build* machine, and ``--platform`` does not
#: change that: ``pywin32>=302; sys_platform == "win32"`` is skipped on Linux even when the
#: target is ``win_amd64``. Both ``pyjab`` and ``mcp`` depend on pywin32 on Windows, so it is
#: named explicitly. This is the one place a Windows-only dependency has to be listed, and the
#: inventory this script prints is what makes a missing one visible.
#:
#: The same wart has a milder form for ``python_version`` markers: they are evaluated for the
#: interpreter running the build, so a dependency whose marker says ``python_version >=
#: "3.11"`` resolves for the *newest* vendored tree and not for the older ones. The wheels
#: themselves are still fetched by ``--python-version``/``--abi``, which is what decides
#: whether a native module loads; this only affects which *version* of a pure-Python
#: dependency each tree gets.
TARGET_EXTRAS: Sequence[str] = ("pywin32",)

#: ``references/manifest-schema.md`` is the field reference this is written against. The schema
#: is ``additionalProperties: false``, so this set is the whole vocabulary: a field that is not
#: in it is rejected rather than guessed at.
MANIFEST_FIELDS = frozenset({
    "$schema", "manifest_version", "name", "version", "description", "author", "server",
    "display_name", "long_description", "icon", "icons", "homepage", "repository",
    "documentation", "support", "license", "keywords", "user_config", "compatibility",
    "tools", "prompts", "tools_generated", "prompts_generated", "screenshots", "localization",
    "privacy_policies",
})
MANIFEST_REQUIRED = ("manifest_version", "name", "version", "description", "author", "server")
MANIFEST_VERSION = "0.4"
COMPATIBILITY_FIELDS = frozenset({"claude_desktop", "platforms", "runtimes"})
PLATFORMS = ("darwin", "win32", "linux")
SERVER_TYPES = ("node", "python", "binary")
SCHEMA_URL = (
    "https://raw.githubusercontent.com/anthropics/mcpb/main/schemas/mcpb-manifest-v0.4.schema.json"
)

#: The prose the host shows where a user decides to install -- and the two honest limits (a JDK
#: is still required, and MCPB enforces nothing) belong where that decision is made, not only in
#: a repository document.
#:
#: ``{range}`` and ``{minors}`` are filled in from the same list the vendoring loop uses. That
#: is not tidiness: this paragraph is the only place the bundle states which interpreters it
#: carries, and a hand-written "3.10 or newer" while the zip holds trees for 3.10-3.13 is a
#: claim the artefact cannot keep. The user meets the difference as an ImportError from a native
#: module, which names a DLL and not the mismatch.
LONG_DESCRIPTION = """\
Drive Java desktop applications from an AI agent, through the JVM's own accessibility tree
rather than by guessing at pixels.

**What this needs on the machine, none of which the bundle can supply:**

* **Windows 10/11** -- Java Access Bridge is a Windows API.
* **64-bit CPython {range}** -- the bundle carries one dependency tree per supported minor and
  nothing for any other interpreter. The minors are ({minors}).
  That is not a choice: `pydantic-core`, `cffi`, `rpds-py`, `cryptography` and `pywin32`
  ship compiled wheels tagged for one minor each, so a tree built for one cannot be imported
  by another. The `pip install` path has no such bound.
* **A JDK with Java Access Bridge enabled** -- `"%JAVA_HOME%\\bin\\jabswitch" -enable`.
* **A Java application that is already running.** This server only ever attaches to
  applications it did not launch; nothing here starts or restarts your app.

**MCPB enforces no permissions.** There is no manifest-level sandbox: the server runs with the
full privileges of the user who installed it, exactly as it would if it had been started by
hand. Read the source before installing a bundle from anyone, this one included.

Start with the `java_diagnostics` tool: it names the part of the environment that is missing
instead of leaving a tool call to fail.
"""


def _minor(version: str) -> "tuple[int, int]":
    major, minor = version.split(".")
    return int(major), int(minor)


def python_range(minors: Sequence[str]) -> str:
    """``3.10-3.13`` for a contiguous run, ``3.10, 3.12`` otherwise, ``3.10`` for one.

    Used for both the manifest's prose and the entry script's refusal, so the range a user reads
    and the range the bundle enforces are the same string.
    """
    ordered = sorted(minors, key=_minor)
    contiguous = all(
        _minor(later) == (_minor(earlier)[0], _minor(earlier)[1] + 1)
        for earlier, later in zip(ordered, ordered[1:])
    )
    if len(ordered) > 1 and contiguous:
        return f"{ordered[0]}-{ordered[-1]}"
    return ", ".join(ordered)


def long_description(minors: Sequence[str]) -> str:
    """The prose, with the CPython range the bundle actually carries filled in."""
    ordered = sorted(minors, key=_minor)
    return LONG_DESCRIPTION.format(range=python_range(ordered), minors=", ".join(ordered))


# ---------------------------------------------------------------------------------
# What the bundle declares
# ---------------------------------------------------------------------------------


def package_version() -> str:
    """``pyjab_mcp.__version__`` -- the one authority for the version in this repository."""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import pyjab_mcp

    return pyjab_mcp.__version__


def manifest(version: str, minors: Sequence[str]) -> Dict[str, object]:
    """The manifest the bundle carries, for the *minors* it is being vendored for.

    Only fields ``references/manifest-schema.md`` documents appear here. Three documented
    fields are deliberately absent, because inventing a value for them would be a guess:

    * ``compatibility.claude_desktop`` -- no floor has been measured against any host;
    * ``compatibility.runtimes`` -- the honest requirement is a set of Python *minors* whose
      vendored trees are present, and the compound range that would express it is not something
      either plausible parser agrees on: Python's ``packaging`` wants ``">=3.10,<3.14"`` and
      node-semver rejects the comma, while the host is a Node application. A range it mis-reads
      could refuse a correct install, so the requirement is stated in ``long_description`` (a
      manifest field) and pinned against the zip by ``tests/test_packaging.py`` instead;
    * ``tools`` / ``prompts`` -- the surface is the 15 registered tools, which
      `docs/ARCHITECTURE.md` is the record of; listing them a second time here is a second
      place for them to go stale.
    """
    return {
        "$schema": SCHEMA_URL,
        "manifest_version": MANIFEST_VERSION,
        "name": "pyjab-mcp",
        "version": version,
        "display_name": "pyjab-mcp",
        "description": (
            "Drive Java desktop applications from an AI agent, through the JVM's own "
            "accessibility tree."
        ),
        "long_description": long_description(minors),
        "author": {
            "name": "Gary Gao",
            "email": "gaozhao89@qq.com",
            "url": "https://github.com/gaozhao1989/pyjab-mcp",
        },
        "homepage": "https://github.com/gaozhao1989/pyjab-mcp",
        # An object, not a URL string: the field reference lists `repository` among the "URLs"
        # and the published schema requires `{type, url}`. The mismatch was found by running
        # `--validate-with-mcpb` (`repository: Expected object, received string`), which is why
        # that check exists and why `check_manifest` below also insists on the object shape.
        "repository": {"type": "git", "url": "https://github.com/gaozhao1989/pyjab-mcp"},
        "documentation": "https://github.com/gaozhao1989/pyjab-mcp/blob/main/docs/CLIENTS.md",
        "support": "https://github.com/gaozhao1989/pyjab-mcp/issues",
        "license": "MIT",
        "keywords": ["mcp", "java", "accessibility", "automation", "swing", "desktop", "jab"],
        "server": {
            "type": "python",
            # `${__dirname}` is the documented substitution for the unpacked bundle's path.
            # `command` is the `python` on the user's PATH: no interpreter is bundled.
            "entry_point": ENTRY_IN_BUNDLE,
            "mcp_config": {
                "command": "python",
                "args": ["${__dirname}/" + ENTRY_IN_BUNDLE],
            },
        },
        "compatibility": {"platforms": ["win32"]},
    }


def check_manifest(document: Dict[str, object]) -> List[str]:
    """Every reason this manifest does not match the documented field reference.

    ``references/manifest-schema.md`` documents the schema as ``additionalProperties: false``,
    so an unknown key is a rejection and not a warning -- the host would refuse the bundle.
    This is the offline half of the validation; ``--validate-with-mcpb`` runs Anthropic's own
    CLI over the file as well when the network allows it.
    """
    problems: List[str] = []

    unknown = sorted(set(document) - MANIFEST_FIELDS)
    if unknown:
        problems.append(
            f"UNDOCUMENTED FIELD(S): {unknown}. The schema is additionalProperties: false, so "
            "the host rejects the bundle rather than ignoring the field. Add the field only "
            "with the reference in front of you."
        )
    for field in MANIFEST_REQUIRED:
        if not document.get(field):
            problems.append(f"MISSING FIELD: {field} is required by the manifest schema.")

    if document.get("manifest_version") != MANIFEST_VERSION:
        problems.append(
            f"WRONG manifest_version: {document.get('manifest_version')!r}, expected "
            f"{MANIFEST_VERSION!r}."
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(document.get("name", ""))):
        problems.append(
            f"BAD name: {document.get('name')!r} is not a lowercase hyphenated identifier."
        )
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?", str(document.get("version", ""))):
        problems.append(f"BAD version: {document.get('version')!r} is not a version.")

    author = document.get("author")
    if not isinstance(author, dict) or not author.get("name"):
        problems.append("BAD author: the schema requires an object with a name.")

    repository = document.get("repository")
    if repository is not None and not (
        isinstance(repository, dict)
        and isinstance(repository.get("type"), str)
        and isinstance(repository.get("url"), str)
    ):
        # The field reference lists `repository` among the URLs; the schema requires an object.
        # The offline check follows the schema, because that is what the host applies.
        problems.append(
            "BAD repository: the schema requires {type, url}, not a URL string."
        )

    server = document.get("server")
    if not isinstance(server, dict):
        problems.append("BAD server: the schema requires an object.")
    else:
        if server.get("type") not in SERVER_TYPES:
            problems.append(
                f"BAD server.type: {server.get('type')!r} is not one of {list(SERVER_TYPES)}."
            )
        if not isinstance(server.get("entry_point"), str) or not server["entry_point"]:
            problems.append("BAD server.entry_point: a relative path is required.")
        launch = server.get("mcp_config")
        if not isinstance(launch, dict) or not launch.get("command"):
            problems.append("BAD server.mcp_config: a command is required to launch the server.")
        elif not isinstance(launch.get("args"), list) or not all(
            isinstance(argument, str) for argument in launch["args"]
        ):
            problems.append("BAD server.mcp_config.args: the schema requires a list of strings.")

    compatibility = document.get("compatibility")
    if compatibility is not None:
        if not isinstance(compatibility, dict):
            problems.append("BAD compatibility: the schema requires an object.")
        else:
            extra = sorted(set(compatibility) - COMPATIBILITY_FIELDS)
            if extra:
                problems.append(f"UNDOCUMENTED compatibility FIELD(S): {extra}.")
            platforms = compatibility.get("platforms")
            if platforms is not None:
                if not isinstance(platforms, list) or not all(
                    platform in PLATFORMS for platform in platforms
                ):
                    problems.append(
                        f"BAD compatibility.platforms: {platforms!r} is not a subset of "
                        f"{list(PLATFORMS)}."
                    )
    return problems


# ---------------------------------------------------------------------------------
# What must not be in the bundle
# ---------------------------------------------------------------------------------


def staged_problems(root: Path) -> List[str]:
    """Why this staged tree must not be zipped: a local-only file, or a build artefact.

    The bundle is a thing a stranger installs. A maintainer note that only exists on this
    machine must never travel inside it, and neither must a ``__pycache__`` -- both are the
    same class of accident as publishing them in an sdist, and this is the artifact-level
    check rather than the rules-level one.
    """
    problems: List[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        for local in LOCAL_ONLY:
            stripped = local.rstrip("/")
            if relative == stripped or relative.startswith(stripped + "/"):
                problems.append(
                    f"LOCAL-ONLY FILE IN THE BUNDLE: {relative} is deliberately kept out of "
                    "everything a user receives. Nothing under it may be copied into the "
                    "staging directory."
                )
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            problems.append(
                f"BUILD ARTEFACT IN THE BUNDLE: {relative}. Compiled bytecode is "
                "platform- and version-locked, and it is not what was reviewed."
            )
    return problems


# ---------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------


def find_wheel(dist: Path) -> Path:
    """The one built wheel in *dist*, or a message naming how to make one."""
    wheels = sorted(dist.glob("pyjab_mcp-*.whl")) if dist.is_dir() else []
    if not wheels:
        raise SystemExit(
            f"NOTHING TO PACK: no pyjab_mcp-*.whl in {dist}.\n"
            "  The bundle is built *from the built wheel*, so build it first:\n"
            "      python -m build\n"
            "  (Nothing is bundled from the working tree, which is why an empty dist/ is "
            "refused rather than turned into an empty bundle.)"
        )
    if len(wheels) > 1:
        raise SystemExit(
            f"AMBIGUOUS: {dist} holds {len(wheels)} pyjab_mcp wheels "
            f"({[wheel.name for wheel in wheels]}). Remove the stale ones -- packing the wrong "
            "one would ship a bundle whose contents do not match its manifest."
        )
    return wheels[0]


def wheel_version(wheel: Path) -> str:
    """The ``Version`` in the wheel's own metadata, so the manifest cannot disagree with it."""
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if not names:
            raise SystemExit(f"NOT A WHEEL: {wheel.name} carries no .dist-info/METADATA.")
        text = archive.read(sorted(names)[0]).decode("utf-8", "replace")
    for line in text.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit(f"NOT A WHEEL: {wheel.name}'s metadata names no version.")


def supported_python_versions(pyproject: Optional[Path] = None) -> List[str]:
    """The CPython minors this release claims, read out of ``pyproject.toml``.

    Read rather than repeated, for the same reason the manifest's version is read rather than
    written down: a second copy of "which Pythons are supported" is a second thing to keep in
    step, and getting it wrong here ships a bundle that imports a native module tagged for
    another interpreter.
    """
    path = pyproject or REPO_ROOT / "pyproject.toml"
    found = re.findall(r"Programming Language :: Python :: (3\.\d+)", path.read_text("utf-8"))
    versions = sorted(set(found), key=lambda text: tuple(int(part) for part in text.split(".")))
    if not versions:
        raise SystemExit(
            f"NO SUPPORTED PYTHONS: {path} declares no 'Programming Language :: Python :: "
            "3.x' classifier, so there is nothing to vendor for."
        )
    return versions


def interpreter_tag(version: str) -> str:
    """``3.10`` -> ``cp310``, which is both the wheel ABI tag and the directory name."""
    return "cp" + version.replace(".", "")


# ---------------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------------


def install(target: Path, wheel: Path, version: str, platform: str, dependencies: bool) -> None:
    """Install the wheel, and its closure, into *target* for one interpreter.

    With ``dependencies`` the closure is resolved for ``platform``/``version`` instead of for
    the machine running the build, which is what makes the bundle a Windows bundle even when
    it is built on Linux. Without it -- the path the tests take -- pip is asked for the wheel
    alone and no index is consulted, so the whole run is offline.
    """
    command = [
        sys.executable, "-m", "pip", "install",
        "--target", str(target),
        "--no-compile",
        "--only-binary=:all:",
        "--platform", platform,
        "--python-version", version,
        "--implementation", "cp",
        "--abi", interpreter_tag(version),
    ]
    if dependencies:
        command += [str(wheel), *TARGET_EXTRAS]
    else:
        command += ["--no-deps", "--no-index", str(wheel)]

    # PYTHONDONTWRITEBYTECODE is belt to `--no-compile`'s braces: a __pycache__ that appears
    # anyway fails the build rather than being shipped, so it is worth not creating one.
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
        raise SystemExit(
            f"PIP FAILED for CPython {version} ({platform}), exit {result.returncode}:\n"
            f"    {' '.join(command)}\n{tail}\n"
            "  The bundle is not built: a missing dependency here is a server that cannot "
            "start on the user's machine."
        )


def validate_with_mcpb(manifest_path: Path) -> List[str]:
    """Run Anthropic's own ``mcpb validate`` over the manifest, and fail if it rejects it.

    The CLI ships as an npm package, so reaching it is a network operation and it is asked for
    explicitly (``--validate-with-mcpb``). It is an *extra* check over the offline field
    validation above, never a replacement: the offline one is the one that can run in a unit
    test. A non-zero exit fails the build, because a check the build ignores is decoration --
    and "the CLI could not be reached" is reported as its own outcome rather than being
    mistaken for a pass.
    """
    executable = shutil.which("npx") or shutil.which("mcpb")
    if not executable:
        return [
            "  MCPB CLI NOT RUN: neither `npx` nor `mcpb` is on PATH, so the manifest was "
            "checked against the offline field validator only."
        ]
    base = [executable] if executable.endswith("mcpb") else [
        executable, "--yes", "@anthropic-ai/mcpb"
    ]
    version = subprocess.run(base + ["--version"], capture_output=True, text=True)
    result = subprocess.run(base + ["validate", str(manifest_path)], capture_output=True,
                            text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise SystemExit(
            f"MCPB CLI REJECTED THE MANIFEST: `{' '.join(base)} validate` exited "
            f"{result.returncode}.\n{output[-2000:]}\n"
            "  This is the schema the host will apply, so the bundle is not built."
        )
    return [
        f"  mcpb {version.stdout.strip() or '?'} validate {manifest_path.name}: exit 0",
        "  " + output.splitlines()[-1] if output else "",
    ]


def _inventory(root: Path) -> List[str]:
    """One line per thing that went in, so the log says what was shipped rather than how big.

    The vendored trees are summarised one level down -- ``server/vendor/cp310/`` with its size
    -- because listing several hundred dependency files would bury the two lines that matter:
    which manifest and which entry script are in the bundle.
    """
    lines = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        depth = relative.count("/")
        if path.is_file() and depth <= 1:
            lines.append(f"  {relative}  {path.stat().st_size} bytes")
        elif path.is_dir() and path.parent.as_posix().endswith(VENDOR_DIR):
            files = [member for member in path.rglob("*") if member.is_file()]
            size = sum(member.stat().st_size for member in files) / (1024 * 1024)
            lines.append(f"  {relative}/  {len(files)} files, {size:.1f} MB")
    return lines


def _zip(root: Path, bundle: Path) -> None:
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())


def build(
    dist: Path,
    out: Path,
    versions: Sequence[str],
    platform: str = DEFAULT_PLATFORM,
    dependencies: bool = True,
    with_mcpb: bool = False,
) -> Path:
    """Build the bundle from the wheel in *dist* and return the file it wrote."""
    version = package_version()
    wheel = find_wheel(dist)
    declared = wheel_version(wheel)
    if declared != version:
        raise SystemExit(
            f"VERSION MISMATCH: {wheel.name} was built as {declared}, and "
            f"pyjab_mcp.__version__ is {version}.\n"
            "  Rebuild it -- `python -m build` -- so that the bundle's contents and its "
            "manifest describe the same release."
        )
    if not ENTRY_SCRIPT.is_file():
        raise SystemExit(
            f"NO ENTRY SCRIPT: {ENTRY_SCRIPT} does not exist.\n"
            "  It is tracked in the repository and is not part of the sdist, so the bundle "
            "has to be built from a git checkout."
        )

    out.mkdir(parents=True, exist_ok=True)
    bundle = out / f"pyjab-mcp-{version}.mcpb"
    staging = Path(tempfile.mkdtemp(prefix="pyjab-mcp-build-"))
    try:
        server = staging / SERVER_DIR
        server.mkdir(parents=True)
        (server / ENTRY_SCRIPT.name).write_bytes(ENTRY_SCRIPT.read_bytes())

        document = manifest(version, versions)
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

        # The assertion the version rule needs: what was written, read back, is the version
        # the package declares. A manifest that disagreed with `__version__` would be a fifth
        # place the version lives, which is the thing `tools/check_release_version.py` exists
        # to prevent.
        written = json.loads(manifest_path.read_text(encoding="utf-8"))
        if written.get("version") != version:
            raise SystemExit(
                f"MANIFEST DRIFT: wrote version {written.get('version')!r}, and "
                f"pyjab_mcp.__version__ is {version!r}."
            )
        problems = check_manifest(written)
        if problems:
            raise SystemExit("INVALID MANIFEST:\n" + "\n".join(f"  {p}" for p in problems))

        for minor in versions:
            install(staging / VENDOR_DIR / interpreter_tag(minor), wheel, minor, platform,
                    dependencies)

        problems = staged_problems(staging)
        if problems:
            raise SystemExit("REFUSING TO PACK:\n" + "\n".join(f"  {p}" for p in problems))

        _inventory_lines = _inventory(staging)
        if with_mcpb:
            print("\n".join(validate_with_mcpb(manifest_path)))

        _zip(staging, bundle)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    size = bundle.stat().st_size / (1024 * 1024)
    print(f"  manifest.json  version {version}, from pyjab_mcp.__version__")
    print("\n".join(_inventory_lines))
    print(
        f"PASSED: {bundle} -- {size:.1f} MB, {len(list(versions))} interpreter(s), "
        f"dependencies {'installed' if dependencies else 'SKIPPED'}, platform {platform}."
    )
    return bundle


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The bundle is built from dist/*.whl, so run `python -m build` first. Nothing is "
            "bundled from the working tree: an empty dist/ is refused rather than turned into "
            "an empty bundle."
        ),
    )
    parser.add_argument("--dist", default=str(REPO_ROOT / "dist"),
                        help="directory holding the built sdist and wheel (default: dist/)")
    parser.add_argument("--out", default=None,
                        help="where to write the .mcpb (default: the --dist directory)")
    parser.add_argument("--python-versions", default=None,
                        help="comma-separated CPython minors to vendor for "
                             "(default: the classifiers in pyproject.toml)")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM,
                        help=f"the wheel platform to vendor for (default: {DEFAULT_PLATFORM})")
    parser.add_argument("--skip-dependencies", action="store_true",
                        help="install the wheel alone, offline: the path the tests take, and "
                             "not a bundle anybody should ship")
    parser.add_argument("--validate-with-mcpb", action="store_true",
                        help="also run Anthropic's `mcpb validate` over the manifest; it "
                             "downloads the CLI, so it needs the network")
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    out = Path(args.out) if args.out else dist
    versions = (
        [part.strip() for part in args.python_versions.split(",") if part.strip()]
        if args.python_versions
        else supported_python_versions()
    )
    bundle = build(dist, out, versions, platform=args.platform,
                   dependencies=not args.skip_dependencies, with_mcpb=args.validate_with_mcpb)
    print(f"bundle: {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
