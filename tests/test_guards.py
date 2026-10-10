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
import tools.check_pyjab_changelog as changelog_guard
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
| observe | `java_find` | M3 |
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
                    "java_find"],
    )

    assert any("NOT YET: java_find" in problem for problem in problems)


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


def _report(status: str, detail: str = "because", version: str = "9.9.9"):
    from pyjab_mcp.backend import Fact, PyjabApiReport

    facts = tuple(
        Fact(name, status, detail) for _key, name, _req, _resolved in readiness_guard.WAITING
    )
    return PyjabApiReport(version, None, facts)


def test_readiness_is_ready_when_the_probe_says_available_or_the_pin_resolves_it():
    verdicts = readiness_guard.readiness(_report("available"))

    # The walk entry is a pin question: 1.9.0 shipped it, so the pin is the answer even if
    # a probe on this platform cannot measure it.
    assert verdicts["list_windows"][0] == readiness_guard.READY
    assert verdicts["walk"][0] == readiness_guard.RESOLVED

    old = readiness_guard.readiness(_report("available", version="1.8.0"))
    assert all(
        verdict != readiness_guard.RESOLVED for verdict, _ in old.values()
    ), "an older pyjab must not be reported as having the release"


@pytest.mark.parametrize("status", ["missing", "unknown"])
def test_readiness_treats_missing_and_unknown_as_not_ready(status):
    """'I could not measure it' is not 'it is there' -- AGENTS.md 2.4, applied to a wait."""
    verdicts = readiness_guard.readiness(_report(status, version="1.8.0"))

    assert all(verdict == readiness_guard.NOT_READY for verdict, _ in verdicts.values())


def test_every_waiting_entry_names_a_fact_the_probe_actually_produces():
    """A readiness check that asks about a fact nobody measures reads as READY for ever."""
    from pyjab_mcp.backend import probe_public_api

    api = probe_public_api()
    measured = {fact.name for fact in api.facts}

    for key, fact_name, requirement_key, resolved_in in readiness_guard.WAITING:
        assert fact_name in measured, (
            f"the readiness check waits on {key!r} via the fact {fact_name!r}, which "
            "probe_public_api does not produce"
        )
        if requirement_key is not None:
            from pyjab_mcp.backend import requirement

            assert requirement(requirement_key).key == requirement_key
        elif resolved_in is None:
            raise AssertionError(
                f"{key!r} has neither a requirement nor a release that resolved it, so "
                "nothing would ever make it READY"
            )


# ---------------------------------------------------------------------------------
# tools/check_pyjab_changelog.py
# ---------------------------------------------------------------------------------

CHANGELOG_WITH_MARKERS = """\
1.9.0 - 2026-01-01
------------------

Changed
~~~~~~~

* **A thing moved.** ``Affects: pyjab-mcp`` and it says why.

* **A thing with a commit.** ``Affects: pyjab-mcp`` (a5dacfb)

Unreleased
----------

Added
~~~~~

* **A note that mentions pyjab-mcp** without the marker word.
"""


def test_the_changelog_guard_finds_markers_and_ignores_mentions():
    found = changelog_guard.markers(CHANGELOG_WITH_MARKERS)

    assert len(found) == 2
    assert found[0].section.startswith("1.9.0")
    assert found[1].id == "a5dacfb", "a commit on the line is the id to acknowledge"
    assert found[0].id.startswith("line:"), (
        "without a commit the id comes from the text, so rewording forces a re-answer"
    )


def test_the_changelog_guard_catches_an_unacknowledged_marker():
    found = changelog_guard.markers(CHANGELOG_WITH_MARKERS)

    problems = changelog_guard.check(found, {"a5dacfb": "nothing needed"})

    assert any("UNACKNOWLEDGED" in problem for problem in problems)
    assert any(found[0].id in problem for problem in problems)


def test_the_changelog_guard_passes_when_every_marker_is_answered():
    found = changelog_guard.markers(CHANGELOG_WITH_MARKERS)
    acknowledged = {marker.id: "looked at it" for marker in found}

    assert changelog_guard.check(found, acknowledged) == []


def test_the_changelog_guard_catches_an_acknowledgement_whose_marker_has_gone():
    """A stale entry is a claim about a change that no longer exists."""
    problems = changelog_guard.check([], {"deadbee": "nothing needed"})

    assert any("STALE" in problem for problem in problems)
    assert any("deadbee" in problem for problem in problems)


def test_reworded_marker_needs_a_new_answer():
    """The content-derived id is what makes that true rather than aspirational."""
    first = changelog_guard.markers("* something. Affects: pyjab-mcp\n")[0]
    second = changelog_guard.markers("* something else. Affects: pyjab-mcp\n")[0]

    assert first.id != second.id
    assert changelog_guard.check([second], {first.id: "answered the old wording"})


def test_the_changelog_guard_reports_a_source_it_could_not_read(tmp_path, capsys):
    """'I could not read it' is not 'nothing was marked' -- AGENTS.md 2.4."""
    missing = tmp_path / "nowhere" / "CHANGELOG.rst"

    text, where = changelog_guard.read_changelog(str(missing))

    assert text is None or isinstance(text, str)
    if text is None:
        assert "nowhere" in where


def test_the_acknowledgement_file_is_a_list_of_commits_and_whats():
    acknowledged, context, error = changelog_guard.load_acknowledged()

    assert error is None, error
    assert isinstance(acknowledged, dict)
    assert isinstance(context, dict)
    assert context, (
        "the pre-convention entries are how the reasons for the current pin survive; an "
        "empty context means somebody deleted them rather than moving them"
    )


def test_the_acknowledgement_loader_reports_a_missing_file_rather_than_passing(tmp_path):
    acknowledged, context, error = changelog_guard.load_acknowledged(tmp_path / "no.yaml")

    assert error and "does not exist" in error
    assert acknowledged == {} and context == {}


def test_the_changelog_guard_script_answers_for_one_explicit_file(tmp_path):
    """The CLI, with a source given, so this test never needs the network."""
    changelog = tmp_path / "CHANGELOG.rst"
    changelog.write_text("1.9.0 - 2026-01-01\n\n* Affects: pyjab-mcp\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_pyjab_changelog.py"),
         "--changelog", str(changelog)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 1, "an unanswered marker has to fail the check"
    assert "UNACKNOWLEDGED" in result.stdout


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
