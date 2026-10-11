"""L2: the built ``.mcpb``, unpacked, with its **own** entry script driven over stdio.

Everything else this repository says about the bundle is a statement about its *shape*: the
manifest validates, the members are right, the version is injected, no local-only file is
inside. Nothing ran it -- and that is exactly where the last blocking defect lived. The
``cp310`` tree was missing ``exceptiongroup`` (a hard dependency of ``anyio``, whose marker
``python_version < "3.11"`` pip evaluates against the *build* interpreter rather than the
target), so a 3.10 user's server died at import while every static check passed, including
one that compared the declared range with the vendored directory **names** (issue #37).

So this test runs the artifact: it unpacks the bundle the job built, names the vendored tree
the *running* interpreter would import from, and drives a real MCP session through
``server/entry.py`` -- the same mechanism ``tests/test_stdio_server.py`` uses for the console
script (``stdio_client`` + ``ClientSession``, not hand-rolled JSON-RPC). Because the
interpreter that spawns the entry script is the one running this test, one CI leg per
supported minor is what makes each ``vendor/cp3XX`` tree measured rather than declared.

**No site-packages on purpose.** The subprocess is run with ``-I -S``, so it sees the
standard library and the bundle and nothing else. That is not tidiness: the CI job installs
the checkout (``pip install -e ".[dev]"``) so pytest and the MCP *client* exist, and on a
3.10 leg that install brings ``exceptiongroup`` with it. A bundle missing it in ``cp310``
would then import the host's copy and pass -- a green job over the exact defect this test
exists to catch. A `.mcpb` is also installable on a machine with no package manager, which is
the claim being measured, so "nothing installed" is the right environment to measure it in.

**What this does not prove.** The server starts and answers ``tools/list`` on Windows with a
plain interpreter -- no JDK, no Java Access Bridge, no Java application, and no tool call
against a real window. That is L3 and only L3 (``.github/workflows/windows-gui.yml``).

The bundle under test comes from ``PYJAB_MCP_BUNDLE``, which only the ``mcpb-runs`` job in
``.github/workflows/ci.yml`` sets. Unset means **skipped**, so the default suite on a machine
that has not built a bundle, and on every platform that cannot run one, is unaffected. Set
but missing or not runnable means **failed, never skipped**: a job that sets this variable
and gets a skip reports green while nothing ran, and a skip is how a gate stops being one
(the lesson of issue #30, applied here).
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

#: No `pytestmark = pytest.mark.asyncio`: the test below is **synchronous** and drives exactly
#: one event loop itself. The first version was `async def` and called the sync tool-surface
#: authority from inside that loop, which is `RuntimeError: asyncio.run() cannot be called from
#: a running event loop` -- the `mcpb-runs` job caught it on all four legs, before the bundle's
#: own handshake had run once. One loop, started once, is the shape that cannot do that.

#: The variable the CI job sets, and the only thing that decides skip-or-run. Pinned by
#: ``tests/test_guards.py`` against the value the workflow actually sets, so the two ends
#: cannot drift into "the job sets nothing and the test skips".
BUNDLE_ENV = "PYJAB_MCP_BUNDLE"

#: Where the packer puts the entry script, and where it puts the per-interpreter trees. Both
#: names are ``tools/build_mcpb.py``'s; the entry script computes the tree it wants from the
#: same convention (``mcpb/entry.py``).
ENTRY_IN_BUNDLE = Path("server") / "entry.py"
VENDOR_DIR = Path("server") / "vendor"

#: The interpreter's dependencies are the bundle's to carry, so the subprocess is given no
#: site-packages and no environment-variable influence: stdlib + the unpacked bundle.
ISOLATION_ARGS = ("-I", "-S")


def interpreter_tag() -> str:
    """``cp310`` for CPython 3.10 -- the tree a native wheel is tagged for.

    The convention lives in ``mcpb/entry.py`` (and in ``tools/build_mcpb.py`` beside it); it
    is repeated here in one line rather than importing the bundle's payload into the test
    process, because what is being checked is that the payload finds it for itself.
    """
    return "cp%d%d" % (sys.version_info[0], sys.version_info[1])


def bundle_path() -> Path:
    """The one bundle ``PYJAB_MCP_BUNDLE`` names, or a skip that says why there is none.

    The value is a glob because the artifact's filename carries the version, which the CI job
    cannot know before the bundle is built. Exactly one file is required: none means the
    wiring is broken and is a **failure**, more than one means the answer is ambiguous and is
    also a failure -- a test that picked one of several would be measuring something nobody
    chose.
    """
    value = os.environ.get(BUNDLE_ENV, "")
    if not value:
        pytest.skip(
            f"{BUNDLE_ENV} is not set, so there is no built .mcpb to run. The job that builds "
            "one and sets this is `mcpb-runs` in .github/workflows/ci.yml."
        )

    matches = sorted(glob.glob(value))
    if len(matches) != 1:
        pytest.fail(
            f"{BUNDLE_ENV} is set to {value!r} and it matches {len(matches)} file(s) "
            f"({', '.join(matches) or 'none'}). The job is mis-wired or the bundle was not "
            "built: this is a failure rather than a skip, because a skipped run here reports "
            "green while nothing was executed."
        )
    path = Path(matches[0])
    if not path.is_file():
        pytest.fail(f"{BUNDLE_ENV} resolves to {path}, which is not a file.")
    return path


def unpack(bundle: Path, destination: Path) -> None:
    """Unpack the ``.mcpb`` (a zip) into *destination*, refusing quietly-wrong archives."""
    try:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(destination)
    except (zipfile.BadZipFile, OSError) as exc:
        pytest.fail(
            f"{bundle} could not be unpacked as a .mcpb ({type(exc).__name__}: {exc}). The "
            "artifact the job uploaded is not the archive the packer wrote."
        )


def expected_tools() -> "list[str]":
    """The tool surface a client sees, from the single authority this repository has for it.

    ``tools/check_documented_surface.py`` reads it out of the *running server* rather than out
    of the source text, and the documented-surface guard already compares that with the
    architecture page. Taking it from there is what keeps this test from owning a second copy
    of a list that would then have to be kept in step by hand -- and keeping it *separate* from
    the bundle is what stops the comparison being vacuous.

    It owns an ``asyncio.run``, so it has to be called with no loop running: the test below is
    synchronous for that reason.
    """
    from tools import check_documented_surface as docs_guard

    tools = docs_guard.registered_tools()
    assert tools, (
        "this checkout's server registered no tools at all, so comparing the bundle with it "
        "would pass vacuously. Something is wrong with the install, not with the bundle."
    )
    return tools


async def handshake(entry: Path, cwd: Path) -> "list[str]":
    """One real MCP session over the bundle's own entry script: the tool names it answers with.

    The mechanism is ``tests/test_stdio_server.py``'s -- ``stdio_client`` and ``ClientSession``,
    no hand-rolled framing -- pointed at the unpacked ``server/entry.py`` instead of the console
    script.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=[*ISOLATION_ARGS, str(entry)],
        cwd=str(cwd),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "pyjab_mcp", (
                f"the bundle answered initialize() as {initialized.server_info.name!r}, not "
                "'pyjab_mcp'"
            )

            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]


def test_the_bundled_entry_script_answers_a_real_mcp_handshake():
    """The handshake, the server name and the tool count -- through ``server/entry.py``."""
    bundle = bundle_path()
    expected = expected_tools()
    root = Path(tempfile.mkdtemp(prefix="pyjab-mcpb-"))
    try:
        unpack(bundle, root)

        assert (root / "manifest.json").is_file(), (
            f"{bundle} has no manifest.json at its top level, so it is not a bundle the host "
            f"would read. Members: {sorted(str(p.relative_to(root)) for p in root.iterdir())}"
        )
        entry = root / ENTRY_IN_BUNDLE
        assert entry.is_file(), (
            f"{bundle} has no {ENTRY_IN_BUNDLE.as_posix()}: there is nothing to run. The "
            "manifest launches this file, so its absence is a bundle that cannot start."
        )
        tree = root / VENDOR_DIR / interpreter_tag()
        assert tree.is_dir(), (
            f"{bundle} carries no {tree.relative_to(root).as_posix()} tree, and this is "
            f"CPython {sys.version_info.major}.{sys.version_info.minor}: the entry script "
            "would refuse this interpreter instead of starting the server. Present: "
            f"{sorted(p.name for p in (root / VENDOR_DIR).iterdir())}"
        )

        names = asyncio.run(handshake(entry, root))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert names == expected, (
        f"the bundle's tools/list returned {len(names)} tools under CPython "
        f"{sys.version_info.major}.{sys.version_info.minor} ({names}), and this checkout's "
        f"server registers {len(expected)} ({expected}). A shorter list is the bundle "
        "importing a different closure than the one it claims to carry."
    )
