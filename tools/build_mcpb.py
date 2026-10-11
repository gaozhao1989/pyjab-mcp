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
from typing import Dict, List, NamedTuple, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent

# `packaging` is what evaluates a PEP 508 marker, and the closure check below cannot be written
# without it. It is named in `[project.optional-dependencies].dev` **for its own sake** rather
# than relied on as a dependency of `build` and `twine`: those two do carry it today, and the day
# either stopped, this script would fail naming a missing module rather than a missing
# declaration. The jobs that run it install `".[dev]"`, so that one line covers both -- see the
# `mcpb` jobs in `.github/workflows/ci.yml` and `.github/workflows/release.yml`.
# Asserting that here, rather than at the first use, is what makes "the check silently did not
# run" impossible: a missing `packaging` is a refusal, not a skipped step.
try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version
except ImportError as missing:  # pragma: no cover - the environment is wrong
    raise SystemExit(
        f"MISSING TOOL: the dependency closure check needs `packaging` ({missing}).\n"
        "  It comes in with the project's dev extra:  python -m pip install -e \".[dev]\"\n"
        "  Without it a vendored tree can be missing a dependency its own target requires, "
        "and the bundle fails at import on the user's machine."
    )

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

#: The file the host reads first, and the one this script writes and checks.
MANIFEST_NAME = "manifest.json"

#: Where the vendored dependency trees go inside the bundle, and what the entry script looks
#: for. The two are one fact: `mcpb/entry.py` computes ``vendor/cp3XX`` from ``sys.version_info``.
SERVER_DIR = "server"
VENDOR_DIR = "server/vendor"
ENTRY_IN_BUNDLE = "server/entry.py"

#: The one file a ``pip install --target`` writes that records where the *build machine* kept
#: the wheel: ``{"url": "file:///home/runner/work/<repo>/<repo>/dist/<wheel>"}``. Inert for
#: imports, and still the builder's absolute path travelling inside an artifact a stranger
#: installs -- one copy per vendored tree. Measured on a built bundle: four copies under
#: ``pyjab_mcp-*.dist-info/``, and no other file in 6630 members carries a build-machine path.
#: :func:`drop_build_machine_paths` removes it while a tree is staged, and
#: :func:`staged_problems` refuses it so a build that stopped removing it fails rather than
#: shipping the path.
DIRECT_URL = "direct_url.json"

#: The wheel platform this packer builds for, and the *only* one it will.
#:
#: Not a default that a flag may change: the manifest declares `"platforms": ["win32"]`, the
#: prose says 64-bit, and `mcpb/entry.py` refuses a 32-bit interpreter. So a 32-bit target would
#: ship wheels no 64-bit interpreter can load while declaring win32 -- a bundle that every one
#: of its refusals rejects, for a reason nothing in it names. A second target is a second bundle
#: design (its manifest claim, its prose, its refusal), not a flag, and `build()` refuses
#: anything else rather than producing one.
TARGET_PLATFORM = "win_amd64"

#: How many times the closure check may send the packer back to pip. One round is enough for the
#: markers `pip --python-version` cannot honour; a second covers a dependency of one of those.
#: The bound exists so that a distribution nothing can satisfy fails the build instead of looping.
FILL_ROUNDS = 4

#: Dependencies pip cannot see when it is asked for another platform.
#:
#: Environment markers are evaluated against the *build* machine, and ``--platform`` does not
#: change that: ``pywin32>=302; sys_platform == "win32"`` is skipped on Linux even when the
#: target is ``win_amd64``. Both ``pyjab`` and ``mcp`` depend on pywin32 on Windows, so it is
#: named explicitly. This is the one place a Windows-only dependency has to be listed, and the
#: inventory this script prints is what makes a missing one visible.
#:
#: The same wart has a milder form for ``python_version`` markers: they are evaluated for the
#: Environment markers are evaluated against the *build* machine, and ``--platform`` does not
#: change that: ``pywin32>=302; sys_platform == "win32"`` is skipped on Linux even when the
#: target is ``win_amd64``. Both ``pyjab`` and ``mcp`` depend on pywin32 on Windows, so it is
#: named explicitly. This is the one place a Windows-only dependency has to be listed, and the
#: closure check below is what makes a future one visible rather than shipped.
#:
#: The same wart decides more than a version: a marker such as ``python_version < "3.11"`` is
#: false on a newer build machine, so the *dependency itself* never reaches the older tree.
#: ``anyio`` requires ``exceptiongroup`` that way, and a 3.10 tree without it cannot import
#: ``anyio`` at all -- which is what the closure check exists to catch, and what
#: ``FILL_ROUNDS`` below repairs by installing the missing distributions by name.
TARGET_EXTRAS: Sequence[str] = ("pywin32",)

#: The manifest field vocabulary this packer writes, from the MCPB manifest schema. The schema is
#: ``additionalProperties: false``, so this set is the whole vocabulary: a field that is not in
#: it is rejected rather than guessed at. (The field reference this was read from is a
#: maintainer document that is deliberately not published, so it is not cited here: this file
#: ships in the sdist, and a reader of the sdist cannot open it.)
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
* **The interpreter has to be the `python` on `PATH`** -- the host launches the bare `python` it
  resolves there, and there is no `user_config` fallback. CPython installed but not on `PATH` is
  a client that shows no tools, and this file's own refusal cannot name the cause: no
  interpreter ran to print it. `where.exe python` prints the one that will be used.
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

    Only fields the MCPB manifest schema documents appear here. Three documented fields are
    deliberately absent, because inventing a value for them would be a guess:

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

    The MCPB schema is ``additionalProperties: false``, so an unknown key is a rejection and not
    a warning -- the host would refuse the bundle. This is the offline half of the validation;
    ``--validate-with-mcpb`` runs Anthropic's own CLI over the manifest *inside the built
    archive* as well, when the network allows it.
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
    """Why this staged tree must not be zipped: a local-only file, a build artefact, or a path.

    The bundle is a thing a stranger installs. A maintainer note that only exists on this
    machine must never travel inside it, and neither must a ``__pycache__`` -- both are the
    same class of accident as publishing them in an sdist, and this is the artifact-level
    check rather than the rules-level one. ``direct_url.json`` is the third form of the same
    thing: it is not local-only and it is not bytecode, it is *this machine's directory*
    written into a file the user receives.
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
        if path.is_file() and path.name == DIRECT_URL:
            problems.append(
                f"BUILD MACHINE PATH IN THE BUNDLE: {relative}. pip writes this to record the "
                "local wheel it installed from, so it names a directory on whatever machine "
                "built the bundle -- one copy per vendored tree. Staging a tree removes it; "
                "this check is what says the removal ran."
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
# The closure a vendored tree must satisfy, for the interpreter it is vendored for
# ---------------------------------------------------------------------------------
#
# `pip --platform/--python-version/--abi` changes which *wheels* are eligible and which
# `Requires-Python` values are accepted. It does **not** change environment markers: those are
# evaluated against the interpreter running the build. So a tree vendored for 3.10 on a 3.12
# machine is missing every dependency whose requirement is guarded by a marker that is true only
# on 3.10 -- `anyio` requires `exceptiongroup>=1.0.2; python_version < "3.11"`, and the tree the
# 3.10 user gets has no `exceptiongroup` in it. `anyio` imports it at module scope under that
# version check, so the server dies at startup, and the entry script's refusal cannot help: a
# 3.10 tree *is* present. The check below is the predicate, and the loop in `build()` is the
# fix -- pip cannot express it, so the distributions it names are installed explicitly.
#
# The environment is built for the target, never for the build machine. That is the whole point,
# and `test_the_closure_check_reads_the_target_interpreter_not_the_build_one` pins it in both
# directions on whatever interpreter happens to run the tests.

#: The Windows/AMD64 values a marker can ask about, for the runtime the bundle targets. These are
#: the ones a bundle can answer: the OS family, the machine, the implementation. Anything else a
#: marker names -- see :data:`UNMEASURABLE_MARKERS` -- describes the machine the *user* will run
#: on, and substituting this one would be the same wrong-machine mistake the whole check exists
#: for, so those requirements are carried rather than judged.
TARGET_MARKER_VALUES = {
    "implementation_name": "cpython",
    "os_name": "nt",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
    "platform_system": "Windows",
    "sys_platform": "win32",
}

#: Marker variables this check refuses to answer for.
#:
#: ``platform_release`` and ``platform_version`` describe the OS build the user runs; the bundle
#: is installed years after it is built and cannot know them. ``implementation_version`` is the
#: interpreter's patch level, which is the same problem `python_full_version` has -- and that one
#: is answered over the whole minor range below. A requirement guarded by any of these is
#: *carried*: an extra distribution costs a few megabytes, and a missing one is the failure this
#: check exists for. What must not happen is what did happen -- `packaging` silently filling the
#: value in from the build machine.
UNMEASURABLE_MARKERS = ("implementation_version", "platform_release", "platform_version")


class Missing(NamedTuple):
    """One requirement a tree does not satisfy, and the line that says so."""

    #: The canonical name, so the fill loop can group two dists asking for the same thing.
    name: str
    #: ``str(requirement.specifier)`` -- what the fill has to ask for, without the marker.
    specifier: str
    #: The extras the requirement asks for, which the fill has to keep.
    extras: "tuple[str, ...]"
    #: The complaint: the tree, the distribution that asked, and the requirement as written.
    complaint: str


def marker_environment(minor: str) -> Dict[str, str]:
    """The marker environment of a 64-bit CPython *minor* on Windows.

    ``python_full_version`` is the floor of the minor. A marker that compares it is answered for
    the whole minor by :func:`_applies`, because the patch level belongs to the user: this value
    alone would say ``3.10.2`` is not satisfied by ``3.10``.
    """
    environment = dict(TARGET_MARKER_VALUES)
    environment["python_version"] = minor
    environment["python_full_version"] = minor + ".0"
    return environment


def canonical(name: str) -> str:
    """A distribution name in the form the metadata uses to compare two of them."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _unfolded_headers(text: str) -> List[str]:
    """The metadata headers, with RFC 822 continuation lines joined onto what they continue.

    A ``Requires-Dist`` may be folded across lines, and an unfolded ``baz>=1,`` followed by
    `` <2`` parses happily as ``baz>=1`` -- a truncated requirement reads as satisfied, which is
    the exact failure mode this section exists to prevent.
    """
    headers: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            break  # the headers end at the first blank line; the body is the description
        if line[:1] in (" ", "\t") and headers:
            headers[-1] += " " + line.strip()
        else:
            headers.append(line)
    return headers


def _metadata(dist_info: Path) -> "tuple[str, str, List[Requirement]]":
    """The distribution's name, version, and parsed ``Requires-Dist`` lines."""
    name, version = dist_info.parent.name, ""
    requires: List[str] = []
    for header in _unfolded_headers(
        (dist_info / "METADATA").read_text(encoding="utf-8", errors="replace")
    ):
        field, _, value = header.partition(":")
        field = field.strip().lower()
        if field == "name":
            name = value.strip()
        elif field == "version":
            version = value.strip()
        elif field == "requires-dist":
            requires.append(value.strip())
    try:
        return name, version, [Requirement(text) for text in requires]
    except InvalidRequirement as error:
        raise SystemExit(
            f"UNREADABLE METADATA: {dist_info} declares a requirement that is not PEP 508 "
            f"({error}). The tree cannot be checked, so the bundle is not built."
        )


def _unmeasurable(marker: str) -> str:
    """The marker variable the target cannot answer for, or an empty string."""
    for name in UNMEASURABLE_MARKERS:
        if re.search(r"\b%s\b" % name, marker):
            return name
    return ""


def _applies(requirement: Requirement, environment: Dict[str, str], extra: Optional[str]) -> bool:
    """True when this requirement is part of the closure for *environment*, with *extra* asked.

    Two things are answered conservatively rather than by substitution:

    * a marker naming a variable the target cannot know is treated as *applying*, so the
      distribution is carried -- see :data:`UNMEASURABLE_MARKERS`;
    * ``python_full_version`` is evaluated at every patch level the marker mentions (and at the
      ends of the minor), and the answer is true if it is true at any of them. A bundle is built
      once and installed by whoever turns up, so "3.10" has to mean every 3.10.x.
    """
    if requirement.marker is None:
        return True
    marker = str(requirement.marker)
    if _unmeasurable(marker):
        return True
    local = dict(environment)
    # `extra` defaults to the empty string in a PEP 508 evaluation; a requirement guarded by
    # `extra == "crypto"` is not part of the base closure, which is why the caller runs twice.
    local["extra"] = extra or ""
    if "python_full_version" not in marker:
        return requirement.marker.evaluate(local)

    patches = {0, 99}
    for _major, _minor, patch in re.findall(r"\b(\d+)\.(\d+)\.(\d+)\b", marker):
        number = int(patch)
        patches.update({number, number + 1, max(number - 1, 0)})
    return any(
        requirement.marker.evaluate(
            dict(local, python_full_version="%s.%d" % (environment["python_version"], patch))
        )
        for patch in sorted(patches)
    )


def _asked_extras(
    installed: Dict[str, "tuple[str, str, List[Requirement]]"], environment: Dict[str, str]
) -> Dict[str, Set[str]]:
    """Which extras each distribution is asked for, to a fixed point.

    One pass is not enough: ``B[F]; extra == "E"`` is false until something asks for ``E`` on
    ``B``, so ``mcp -> pyjwt[crypto] -> sqlalchemy[asyncio] -> greenlet`` loses ``greenlet`` at
    depth two. Each round re-reads every distribution with the extras known so far and stops when
    another round adds nothing -- which it must, because a metadata graph is finite.
    """
    asked: Dict[str, Set[str]] = {}
    while True:
        grew = False
        for key, (_name, _version, requires) in installed.items():
            for requirement in requires:
                if not requirement.extras:
                    continue
                target = canonical(requirement.name)
                # The extras that make this requirement apply are the ones asked of the
                # distribution whose metadata it is (`key`), not of the one it points at.
                for extra in [None, *sorted(asked.get(key, ()))]:
                    if not _applies(requirement, environment, extra):
                        continue
                    if not requirement.extras <= asked.get(target, set()):
                        asked.setdefault(target, set()).update(requirement.extras)
                        grew = True
        if not grew:
            return asked


def unsatisfied_requirements(tree: Path, minor: str) -> List[Missing]:
    """Every requirement the distributions in *tree* declare for *minor* and the tree violates.

    A *satisfaction* predicate, not a presence one: a tree carrying ``dep 2.9`` while its own
    metadata says ``dep>=2.0,<2.5`` is reported, because that tree contradicts itself exactly as
    a missing distribution does. The answer is about the interpreter the tree was vendored for,
    not the one running this script, which is the defect this exists to catch.
    """
    installed: Dict[str, "tuple[str, str, List[Requirement]]"] = {}
    for dist_info in sorted(tree.glob("*.dist-info")):
        if (dist_info / "METADATA").is_file():
            name, version, requires = _metadata(dist_info)
            installed[canonical(name)] = (name, version, requires)

    environment = marker_environment(minor)
    asked = _asked_extras(installed, environment)

    missing: List[Missing] = []
    for key, (name, version, requires) in sorted(installed.items()):
        for extra in [None, *sorted(asked.get(key, ()))]:
            for requirement in requires:
                if not _applies(requirement, environment, extra):
                    continue
                where = f" (the {extra!r} extra)" if extra else ""
                unknown = _unmeasurable(str(requirement.marker or ""))
                because = (
                    f" -- {unknown} is not knowable for the target, so it is carried rather "
                    "than judged"
                    if unknown else ""
                )
                install_free = canonical(requirement.name)
                problem = _violation(installed, requirement, name, environment)
                if problem is None:
                    continue
                missing.append(Missing(
                    install_free,
                    str(requirement.specifier),
                    tuple(sorted(requirement.extras)),
                    f"{tree.name}: {name} requires {requirement}{where}, and {problem}{because}",
                ))
    return missing


def _violation(
    installed: Dict[str, "tuple[str, str, List[Requirement]]"],
    requirement: Requirement,
    asked_by: str,
    environment: Dict[str, str],
) -> Optional[str]:
    """What is wrong with this requirement in *installed*, or None when it is satisfied."""
    key = canonical(requirement.name)
    carried = installed.get(key)
    if carried is None:
        return f"the tree has no {key}"
    _name, version, _requires = carried
    if not requirement.specifier:
        return None
    try:
        parsed = Version(version)
    except InvalidVersion:
        raise SystemExit(
            f"UNREADABLE VERSION: {asked_by} in the tree reports {version!r}, which is not a "
            "PEP 440 version, so whether it satisfies "
            f"{requirement} cannot be decided. The tree is not built on a guess."
        )
    if parsed in requirement.specifier:
        return None
    return f"the tree carries {key} {version}, and the requirement is {requirement.specifier}"


def fill_closure(tree: Path, minor: str, platform: str) -> None:
    """Install what this tree's own target requires and the tree does not satisfy.

    This is the fix for what the check detects, and it is why the check *drives* the build rather
    than only reporting: pip cannot be asked to evaluate markers for another interpreter, so the
    distributions its resolver dropped are installed explicitly.

    Two details are deliberate. The **specifiers** travel with the request, so a tree carrying
    ``dep 2.9`` against ``dep<2.5`` is repaired by installing something below 2.5 rather than by
    reinstalling the newest one. The **marker does not**: `pip install "exceptiongroup>=1.0.2;
    python_version < '3.11'"` on a 3.12 build machine answers *"Ignoring exceptiongroup: markers
    ... don't match your environment"* and installs nothing -- the same wrong-machine behaviour
    this section exists to repair.
    """
    remaining = unsatisfied_requirements(tree, minor)
    for _ in range(FILL_ROUNDS):
        if not remaining:
            return
        specifiers: Dict[str, Set[str]] = {}
        extras: Dict[str, Set[str]] = {}
        for item in remaining:
            specifiers.setdefault(item.name, set()).update(
                part for part in item.specifier.split(",") if part
            )
            extras.setdefault(item.name, set()).update(item.extras)
        requirements = [
            name
            + ("[%s]" % ",".join(sorted(extras[name])) if extras[name] else "")
            + ",".join(sorted(specifiers[name]))
            for name in sorted(specifiers)
        ]
        print(f"  {tree.name}: CPython {minor} needs {requirements}; installing them by name")
        pip_install(tree, minor, platform, requirements)
        remaining = unsatisfied_requirements(tree, minor)
    raise SystemExit(
        f"UNSATISFIED DEPENDENCIES in {tree.name} for CPython {minor}:\n"
        + "\n".join(f"  {item.complaint}" for item in remaining)
        + f"\n  {FILL_ROUNDS} rounds of installing what the target's markers asked for did not "
        "satisfy the tree, so the bundle is not built: it would fail at import on a machine it "
        "claims to support."
    )


# ---------------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------------


def pip_install(target: Path, version: str, platform: str, requirements: Sequence[str],
                offline: bool = False) -> None:
    """One ``pip install --target``, for the *target* interpreter and platform.

    ``offline`` is the path the tests take: the wheel alone, with no index consulted, so a stub
    ``dist/`` builds a bundle without a network.
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
    if offline:
        command += ["--no-deps", "--no-index"]
    command += [str(requirement) for requirement in requirements]

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


def install_tree(target: Path, wheel: Path, version: str, platform: str,
                 dependencies: bool) -> None:
    """Put our wheel, and its closure, into *target*.

    With ``dependencies`` pip resolves the closure for ``platform``/``version`` -- which is what
    makes the bundle a Windows bundle even when it is built on Linux -- and that is where the
    resolver stops being able to help: see :func:`unsatisfied_requirements`.
    """
    if dependencies:
        pip_install(target, version, platform, [wheel, *TARGET_EXTRAS])
    else:
        pip_install(target, version, platform, [wheel], offline=True)


def drop_build_machine_paths(tree: Path) -> List[str]:
    """Delete the ``direct_url.json`` files under *tree*, returning what was removed.

    Not a tidy-up. The file is pip recording the path it installed the wheel *from*, so it
    names a directory on the machine doing the build, and nothing reads it -- which is exactly
    why it would go unnoticed in an artifact a stranger installs. :func:`staged_problems`
    refuses it, and this is what makes a correct build able to pass that refusal.
    """
    removed: List[str] = []
    for path in sorted(tree.rglob(DIRECT_URL)):
        if path.is_file():
            path.unlink()
            removed.append(path.relative_to(tree).as_posix())
    return removed


def validate_with_mcpb(bundle: Path, run=subprocess.run) -> List[str]:
    """Check the *artifact* with Anthropic's CLI: the manifest inside it, and the archive.

    The manifest is read back out of the ``.mcpb`` rather than validated from the staging copy,
    which is deleted when the build ends: the archive is what the host installs, so the archive
    is what is asked about. ``mcpb info`` runs as well, and it is described as what it is --
    measured, it exits 0 for a bundle whose manifest carries an undeclared field, so it shows
    the archive is readable by the tool and proves nothing about the manifest. ``validate`` is
    the check that can refuse, and a refusal fails the build: a check the build ignores is
    decoration.
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
    version = run(base + ["--version"], capture_output=True, text=True)
    with zipfile.ZipFile(bundle) as archive:
        extracted = archive.read(MANIFEST_NAME)

    lines: List[str] = []
    with tempfile.TemporaryDirectory(prefix="pyjab-mcp-validate-") as scratch:
        path = Path(scratch) / MANIFEST_NAME
        path.write_bytes(extracted)
        validate = run(base + ["validate", str(path)], capture_output=True, text=True)
    output = (validate.stdout + validate.stderr).strip()
    lines.append(
        f"  mcpb {version.stdout.strip() or '?'} validate (the manifest inside the archive): "
        f"exit {validate.returncode}"
        + (f" -- {output.splitlines()[-1]}" if output else "")
    )
    if validate.returncode != 0:
        raise SystemExit(
            f"MCPB CLI REJECTED THE MANIFEST IN THE ARCHIVE: `{' '.join(base)} validate` exited "
            f"{validate.returncode}.\n{output[-2000:]}\n"
            "  This is the schema the host will apply to the file it installs, so the bundle is "
            "not built."
        )

    info = run(base + ["info", str(bundle)], capture_output=True, text=True)
    lines.append(f"  mcpb info {bundle.name}: exit {info.returncode}")
    if info.returncode != 0:
        raise SystemExit(
            f"MCPB CLI COULD NOT READ THE ARCHIVE: `{' '.join(base)} info` exited "
            f"{info.returncode}.\n{(info.stdout + info.stderr).strip()[-2000:]}"
        )
    return lines


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
    platform: str = TARGET_PLATFORM,
    dependencies: bool = True,
    with_mcpb: bool = False,
) -> Path:
    """Build the bundle from the wheel in *dist* and return the file it wrote."""
    if not list(versions):
        raise SystemExit(
            "NO PYTHON VERSIONS: nothing to vendor for. `--python-versions` was empty -- a bare "
            "comma parses to no minors -- and the project declares none either, so the bundle "
            "would carry no dependency tree at all and its own prose would read \"The minors are "
            "()\". Name at least one minor, or leave the flag off to use pyproject.toml."
        )
    if platform != TARGET_PLATFORM:
        raise SystemExit(
            f"UNSUPPORTED TARGET: --platform {platform!r}. This packer builds {TARGET_PLATFORM!r} "
            "and nothing else, because everything else about the bundle is already committed to "
            "it: the manifest declares win32, the prose says 64-bit, and `mcpb/entry.py` refuses "
            "a 32-bit interpreter. A different target would declare one thing and ship another, "
            "so it needs its own manifest claim, its own prose and its own refusal -- not a flag."
        )
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

        dropped: List[str] = []
        for minor in versions:
            tree = staging / VENDOR_DIR / interpreter_tag(minor)
            install_tree(tree, wheel, minor, platform, dependencies)
            if dependencies:
                fill_closure(tree, minor, platform)
            # After the fill, not before: anything pip installs from a local path records one.
            dropped += drop_build_machine_paths(tree)

        problems = staged_problems(staging)
        if problems:
            raise SystemExit("REFUSING TO PACK:\n" + "\n".join(f"  {p}" for p in problems))

        _inventory_lines = _inventory(staging)
        _zip(staging, bundle)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if with_mcpb:
        print("\n".join(validate_with_mcpb(bundle)))

    # Printed because it is a removal, and a removal nothing reports is a removal nobody can
    # see in the log. `--skip-dependencies` still records one per tree: that install is a
    # local wheel path too.
    print(
        f"  removed {len(dropped)} {DIRECT_URL} file(s) -- pip's record of the local wheel path, "
        "which names this build machine and is not something the bundle may carry"
    )

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
    parser.add_argument("--platform", default=TARGET_PLATFORM,
                        help=f"the wheel platform to vendor for; only {TARGET_PLATFORM!r} is "
                             "supported, and anything else is refused rather than built")
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
