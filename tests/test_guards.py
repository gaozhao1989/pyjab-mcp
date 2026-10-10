"""The guards, and proof that each one can fail.

modelled on pyjab's own practice in two ways.  pyjab keeps its checks in ``tools/`` **and**
pins their behaviour with tests (``tests/test_verify_m0_tool.py`` checks that
``tools/verify_m0.py`` measures what it claims to); and its ``tests/_fakejab.py`` exists
because *a fake that agrees with anything proves nothing*.  The same applies to a checker: a
guard that cannot report a problem is decoration.

So every guard below is exercised twice — once against this repository, where it must pass,
and once against an input that is deliberately wrong, where it must complain.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import tools.check_dist_contents as dist_guard
import tools.check_documented_surface as docs_guard
import tools.check_local_only_files as local_guard
import tools.check_pyjab_api_surface as surface_guard
import tools.check_pyjab_readiness as readiness_guard

REPO_ROOT = Path(__file__).resolve().parent.parent
Entry = surface_guard.Entry


# ---------------------------------------------------------------------------------
# tools/check_pyjab_api_surface.py
# ---------------------------------------------------------------------------------


def test_the_api_surface_guard_passes_on_this_repository():
    problems = surface_guard.check()

    assert problems == [], (
        "a pyjab touchpoint in this package is unrecorded, private, or unreleased:\n"
        + "\n".join(problems)
    )


def test_the_api_surface_guard_notices_a_new_touchpoint():
    sites = {("pyjab", "something_new"): ["import at src/pyjab_mcp/x.py:1"]}

    problems = surface_guard.check(sites=sites, surface={}, installed="1.8.0")

    assert any("UNRECORDED" in problem for problem in problems)


def test_the_api_surface_guard_refuses_a_private_symbol():
    point = ("pyjab.jabelement", "JABElement._search_path")
    sites = {point: ["attribute at src/pyjab_mcp/x.py:1"]}
    surface = {point: Entry(surface_guard.PUBLIC, "1.8.0", "reaching in")}

    problems = surface_guard.check(sites=sites, surface=surface, installed="1.8.0")

    assert any("PRIVATE" in problem for problem in problems)


def test_the_api_surface_guard_has_no_private_status_to_record():
    """There must be no way to write down a private dependency and pass."""
    point = ("pyjab.internal", "helper")
    sites = {point: ["import at src/pyjab_mcp/x.py:1"]}
    surface = {point: Entry("private", "1.8.0", "but it works on my machine")}

    problems = surface_guard.check(sites=sites, surface=surface, installed="1.8.0")

    assert any("PRIVATE" in problem for problem in problems)


def test_the_api_surface_guard_refuses_to_import_an_unreleased_name():
    point = ("pyjab", "walk")
    surface = {point: Entry(surface_guard.UNRELEASED, "", "checkout only")}

    imported = surface_guard.check(
        sites={point: ["import at src/pyjab_mcp/x.py:1"]}, surface=surface, installed="1.8.0"
    )
    probed = surface_guard.check(
        sites={point: ["hasattr probe at src/pyjab_mcp/x.py:1"]},
        surface=surface, installed="1.8.0",
    )

    assert any("UNRELEASED DEPENDENCY" in problem for problem in imported)
    assert not probed, (
        "probing an unreleased API is how this project waits for a release; only "
        "depending on one is the failure"
    )


def test_the_api_surface_guard_requires_a_release_for_a_public_entry():
    point = ("pyjab", "list_java_windows")
    sites = {point: ["hasattr probe at src/pyjab_mcp/x.py:1"]}
    surface = {point: Entry(surface_guard.PUBLIC, "", "")}

    problems = surface_guard.check(sites=sites, surface=surface, installed="1.8.0")

    assert any("NO VERSION" in problem for problem in problems)


def test_the_api_surface_guard_refuses_an_import_the_installed_pyjab_does_not_have():
    """The M1 precheck's whole lesson: the checkout is not the wheel."""
    point = ("pyjab.jabelement", "JABElement.walk")
    sites = {point: ["import at src/pyjab_mcp/x.py:1"]}
    surface = {point: Entry(surface_guard.PUBLIC, "1.9.0", "in the checkout, not in 1.8.0")}

    problems = surface_guard.check(sites=sites, surface=surface, installed="1.8.0")

    assert any("NOT INSTALLED" in problem for problem in problems)


def test_the_api_surface_guard_notices_an_entry_that_is_no_longer_used():
    surface = {("pyjab", "list_java_windows"): Entry(surface_guard.UNRELEASED, "", "")}

    problems = surface_guard.check(sites={}, surface=surface, installed="1.8.0")

    assert any("STALE" in problem for problem in problems)


# ---------------------------------------------------------------------------------
# tools/check_documented_surface.py
# ---------------------------------------------------------------------------------

MINIMAL_ARCHITECTURE = """# Architecture

## Where the code lives

| file | what |
|---|---|
| `src/pyjab_mcp/__init__.py` | the version |

## Tool surface

| group | tools | ships |
|---|---|---|
| diagnostics | `java_diagnostics` | M1 |
| session | `java_list_windows`, `java_attach_window` | M1 |
| observe | `java_snapshot` | M2 |
"""


def test_the_documented_surface_guard_passes_on_this_repository():
    problems = docs_guard.check()

    assert problems == [], "\n".join(problems)


def test_the_documented_surface_guard_catches_a_documented_tool_that_is_missing(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics"],
    )

    assert any("MISSING TOOL: java_list_windows" in problem for problem in problems)


def test_the_documented_surface_guard_catches_an_undocumented_tool(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_surprise"],
    )

    assert any("UNDOCUMENTED TOOL: java_surprise" in problem for problem in problems)


def test_the_documented_surface_guard_catches_a_planned_tool_already_registered(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_snapshot"],
    )

    assert any("NOT YET: java_snapshot" in problem for problem in problems)


def test_the_documented_surface_guard_catches_an_undocumented_module(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, package=REPO_ROOT / "src" / "pyjab_mcp", registered=[]
    )

    assert any("UNDOCUMENTED MODULE" in problem for problem in problems)


# ---------------------------------------------------------------------------------
# tools/check_local_only_files.py
# ---------------------------------------------------------------------------------


def test_the_local_only_guard_passes_on_this_repository():
    problems = local_guard.check()

    assert problems == [], "\n".join(problems)


def test_the_local_only_guard_catches_each_mechanism_separately(tmp_path):
    (tmp_path / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
    (tmp_path / "MANIFEST.in").write_text("exclude docs/DESIGN.md\n", encoding="utf-8")

    problems = local_guard.check(
        repo=tmp_path,
        local_only=("AGENTS.md", "docs/DESIGN.md"),
        use_git=False,
    )

    assert any("NOT EXCLUDED FROM THE SDIST: AGENTS.md" in problem for problem in problems)
    assert any("NOT IGNORED: docs/DESIGN.md" in problem for problem in problems)


def test_the_local_only_guard_accepts_a_prune_for_a_directory(tmp_path):
    (tmp_path / ".gitignore").write_text("/.agents/\n", encoding="utf-8")
    (tmp_path / "MANIFEST.in").write_text("prune .agents\n", encoding="utf-8")

    problems = local_guard.check(repo=tmp_path, local_only=(".agents/",), use_git=False)

    assert problems == []


# ---------------------------------------------------------------------------------
# tools/check_dist_contents.py
# ---------------------------------------------------------------------------------


def test_the_dist_guard_catches_a_published_maintainer_document(tmp_path):
    archive = tmp_path / "pyjab_mcp-0.1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("AGENTS.md", "docs/PLAN.md", "README.md"):
            member = tmp_path / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_text("x", encoding="utf-8")
            tar.add(member, arcname=f"pyjab_mcp-0.1.0/{name}")

    problems = dist_guard.check_sdist(archive)

    assert any("PUBLISHED: the sdist contains AGENTS.md" in problem for problem in problems)
    assert any("PUBLISHED: the sdist contains docs/PLAN.md" in problem for problem in problems)
    assert any("MISSING FROM SDIST" in problem for problem in problems)


def test_the_dist_guard_catches_a_wheel_with_stale_copies_and_no_console_script(tmp_path):
    archive = tmp_path / "pyjab_mcp-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pyjab_mcp/__init__.py", "")
        zf.writestr("build/lib/pyjab_mcp/stale.py", "")
        zf.writestr("tools/check_pyjab_api_surface.py", "")
        zf.writestr("tests/test_packaging.py", "")

    problems = dist_guard.check_wheel(archive)

    assert any("STALE COPIES IN WHEEL" in problem for problem in problems)
    assert any("MAINTAINER FILES IN WHEEL" in problem for problem in problems)
    assert any("NO ENTRY POINTS" in problem for problem in problems)


def test_the_dist_guard_catches_a_wheel_whose_console_script_is_wrong(tmp_path):
    archive = tmp_path / "pyjab_mcp-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pyjab_mcp-0.1.0.dist-info/entry_points.txt",
                    "[console_scripts]\nsomething-else = other:main\n")

    problems = dist_guard.check_wheel(archive)

    assert any("NO CONSOLE SCRIPT" in problem for problem in problems)


def test_the_dist_guard_says_so_when_nothing_has_been_built(tmp_path):
    problems = dist_guard.check(tmp_path / "nowhere")

    assert any("NO DIST DIRECTORY" in problem for problem in problems)


# ---------------------------------------------------------------------------------
# tools/check_pyjab_readiness.py
# ---------------------------------------------------------------------------------


def _report(status: str, detail: str = "because"):
    from pyjab_mcp.backend import Fact

    facts = tuple(
        Fact(name, status, detail) for _key, name, _req in readiness_guard.WAITING
    )
    from pyjab_mcp.backend import PyjabApiReport

    return PyjabApiReport("9.9.9", None, facts)


def test_readiness_is_ready_only_when_the_probe_says_available():
    assert all(
        verdict == readiness_guard.READY
        for verdict, _ in readiness_guard.readiness(_report("available")).values()
    )


@pytest.mark.parametrize("status", ["missing", "unknown"])
def test_readiness_treats_missing_and_unknown_as_not_ready(status):
    """'I could not measure it' is not 'it is there' -- AGENTS.md 2.4, applied to a wait."""
    verdicts = readiness_guard.readiness(_report(status))

    assert all(verdict == readiness_guard.NOT_READY for verdict, _ in verdicts.values())


def test_every_waiting_entry_names_a_fact_the_probe_actually_produces():
    """A readiness check that asks about a fact nobody measures reads as READY for ever."""
    from pyjab_mcp.backend import probe_public_api

    api = probe_public_api()
    measured = {fact.name for fact in api.facts}

    for key, fact_name, requirement_key in readiness_guard.WAITING:
        assert fact_name in measured, (
            f"the readiness check waits on {key!r} via the fact {fact_name!r}, which "
            "probe_public_api does not produce"
        )
        if requirement_key is not None:
            from pyjab_mcp.backend import requirement

            assert requirement(requirement_key).key == requirement_key


# ---------------------------------------------------------------------------------
# The scripts themselves, which is what CI runs
# ---------------------------------------------------------------------------------

GUARD_SCRIPTS = (
    "check_pyjab_api_surface.py",
    "check_documented_surface.py",
    "check_local_only_files.py",
    "check_pyjab_readiness.py",
)


@pytest.mark.parametrize("script", GUARD_SCRIPTS)
def test_the_guard_script_runs_and_exits_zero(script):
    """The functions above can pass while the command line is broken."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / script)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, f"{script} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
