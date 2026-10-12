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

import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

import tests.test_mcpb_bundle as bundle_test
import tools.check_dist_contents as dist_guard
import tools.check_pyjab_changelog as changelog_guard
import tools.check_documented_surface as docs_guard
import tools.check_local_only_files as local_guard
import tools.check_pyjab_api_surface as surface_guard
import tools.check_published_references as references_guard
import tools.check_pyjab_readiness as readiness_guard
import tools.check_changelog_immutable as immutable_guard
import tools.check_release_l3 as l3_guard
import tools.check_release_unpublished as unpublished_guard
import tools.check_release_version as release_guard

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


def test_the_api_surface_guard_refuses_an_argument_it_does_not_have():
    """Issue #65: `--strict`, or a typo of `--derived`, used to be silently ignored.

    There is no argparse here, so every argument but `--derived` was dropped and the full
    check ran instead -- answering a different question while exiting 0, which is the shape
    AGENTS.md 2.4 calls a bug.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_pyjab_api_surface.py"), "--strict"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 1, (
        f"the guard exited {result.returncode} for an argument it cannot honour:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "--strict" in result.stdout, (
        "the refusal has to name the argument it could not honour"
    )
    assert "PASSED" not in result.stdout, (
        "a guard that ignored the argument must not then report a pass"
    )


def test_the_api_surface_guard_refuses_an_unreadable_installed_version(monkeypatch, capsys):
    """Issue #65: with no readable pyjab the released-in comparison was skipped, not refused.

    The guard's headline is that every name it uses is in the *installed* pyjab. When the
    version cannot be read that claim cannot be made, and reporting it as satisfied is the
    "I could not read it" / "nothing is wrong" confusion (AGENTS.md 2.4). ``check(None)``
    stays a legitimate library call -- the caller has not probed anything -- so the refusal
    belongs to the command line.
    """
    monkeypatch.setattr(surface_guard, "_installed_version", lambda: None)

    assert surface_guard.main([]) == 1

    out = capsys.readouterr().out
    assert "installed pyjab version could not be read" in out
    assert "PASSED" not in out


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
| observe | `java_screenshot` | M6 |
| act | `java_click` | M7 |
"""


def test_the_documented_surface_guard_passes_on_this_repository():
    problems = docs_guard.check()

    assert problems == [], "\n".join(problems)


def test_the_documented_surface_guard_catches_a_documented_tool_that_is_missing(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics"],
    )

    assert any("MISSING TOOL: java_list_windows" in problem for problem in problems)


def test_the_documented_surface_guard_catches_an_undocumented_tool(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_surprise"],
    )

    assert any("UNDOCUMENTED TOOL: java_surprise" in problem for problem in problems)


def test_the_documented_surface_guard_catches_a_planned_tool_already_registered(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_click"],
    )

    assert any("NOT YET: java_click" in problem for problem in problems)


def test_the_documented_surface_guard_catches_an_undocumented_module(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(MINIMAL_ARCHITECTURE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp", registered=[]
    )

    assert any("UNDOCUMENTED MODULE" in problem for problem in problems)


def test_the_documented_surface_guard_reads_a_release_mark_as_shipping(tmp_path):
    """A tool that lands after the plan's milestones names the release, not a milestone.

    It is still a claim that the tool is in the code now, so an unregistered one is a MISSING
    TOOL -- which is what makes the release mark a mark rather than an escape from the check.
    """
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(
        MINIMAL_ARCHITECTURE.replace(
            "| act | `java_click` | M7 |",
            "| act | `java_click` | M7 |\n| act | `java_focus_window` | 0.3.0 — new tool |",
        ),
        encoding="utf-8",
    )

    shipped = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_snapshot", "java_screenshot", "java_focus_window"],
    )
    assert not any("java_focus_window" in problem for problem in shipped), shipped

    unregistered = docs_guard.check(
        architecture=page, guards=[], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics"],
    )
    assert any("MISSING TOOL: java_focus_window" in problem for problem in unregistered), (
        "a release mark is not a way for a documented tool to be absent from the server"
    )


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


def test_the_local_only_guard_refuses_a_tree_where_git_cannot_be_asked(tmp_path, capsys):
    """Issue #84: with no ``.git`` the guard printed PASSED having never run ``git check-ignore``.

    An extracted sdist has ``.gitignore`` and ``MANIFEST.in`` and no ``.git``, and it is exactly
    the artifact a maintainer inspects when asking "did anything local-only get shipped?".  In
    that tree ``_git_ignores`` returns ``None``, and the caller only complained about ``False``,
    so the guard printed a summary sentence about git without invoking git (AGENTS.md 2.4).

    The tree's two files cover every path on the list, so the refusal is the *only* problem
    that can be reported: without that, the text half would fail on its own and the exit code
    would be 1 for a reason that has nothing to do with git.
    """
    (tmp_path / ".gitignore").write_text(
        "".join(f"/{path}\n" for path in local_guard.LOCAL_ONLY), encoding="utf-8"
    )
    (tmp_path / "MANIFEST.in").write_text(
        "".join(
            f"prune {path}\n" if path.endswith("/") else f"exclude {path}\n"
            for path in local_guard.LOCAL_ONLY
        ),
        encoding="utf-8",
    )

    assert local_guard.main(repo=tmp_path) == 1

    out = capsys.readouterr().out
    assert "git is not available" in out, "the refusal has to name what could not be read"
    assert "check-ignore" in out, "and the check that never ran"
    assert "PASSED" not in out, "a guard that could not ask git must not report the git half"


def test_the_local_only_guard_asks_git_about_a_path_that_is_not_on_disk(tmp_path, monkeypatch):
    """Issue #84, measured second instance: the git half was skipped when the path was absent.

    ``check()`` asked git only when ``(repo / path).exists()``, so in a fresh worktree or a CI
    checkout -- where none of the local-only paths are on disk -- ``git check-ignore`` was never
    run, and PASSED was vacuous there as well.  Git answers for an absent path (measured), so the
    question is about the pattern, not about the file.
    """
    (tmp_path / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
    (tmp_path / "MANIFEST.in").write_text("exclude AGENTS.md\n", encoding="utf-8")
    asked = []

    def recording_ignores(path, repo=tmp_path):
        asked.append(path)
        return True

    monkeypatch.setattr(local_guard, "_git_ignores", recording_ignores)

    assert local_guard.check(repo=tmp_path, local_only=("AGENTS.md",)) == []
    assert "AGENTS.md" in asked, (
        "an absent path still has a .gitignore pattern to ask git about; "
        f"git was asked about {asked!r} instead"
    )


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


def test_readiness_is_resolved_by_the_pin_and_measured_otherwise():
    """Three answers, and the difference matters: resolved by the pin, ready by probe, or not.

    **All five are pin questions now** -- pyjab has released each one, so what guarantees it
    is the floor in pyproject.toml rather than a probe on whatever machine happens to be
    running. ``send_keys`` and ``bring_to_foreground`` joined the list in 1.11.0. The probe
    still decides when the installed version is *below* that floor, which is how a checkout or
    a pre-release that has the API reports READY rather than NOT READY.
    """
    current = readiness_guard.readiness(_report("available"))

    for key in ("list_windows", "detach", "walk", "send_keys", "bring_to_foreground"):
        assert current[key][0] == readiness_guard.RESOLVED, key

    older = readiness_guard.readiness(_report("available", version="1.9.0"))
    assert older["walk"][0] == readiness_guard.RESOLVED, (
        "1.9.0 does have the walk, and the pin's floor is what says so"
    )
    assert older["send_keys"][0] == readiness_guard.READY, (
        "below the floor the probe is the measurement -- that is the drift job's pyjab@master"
    )

    old = readiness_guard.readiness(_report("missing", version="1.9.0"))
    assert old["list_windows"][0] == readiness_guard.NOT_READY, (
        "an install below the pin must not be reported as having the release"
    )

    before_1_11 = readiness_guard.readiness(_report("missing", version="1.10.0"))
    for key in ("send_keys", "bring_to_foreground"):
        assert before_1_11[key][0] == readiness_guard.NOT_READY, (
            f"{key} is a 1.11.0 API, and 1.10.0 must not be reported as having it"
        )


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

* **Affects: pyjab-mcp** -- a thing moved, and it says why.

  **Affects: pyjab-mcp** -- a thing with a commit. (a5dacfb)

Unreleased
----------

Added
~~~~~

* **A note that mentions pyjab-mcp** without the marker word.
"""

#: Verbatim from pyjab's ``CHANGELOG.rst``, in its released 1.11.0 section: a sentence about
#: the marker convention, in the paragraph under the marker it points at.  ``guards`` went
#: red on ``main`` because the check read this sentence as a marker of its own.
PYJAB_SENTENCE_MENTIONING_THE_MARKER = (
    "  could reach. **No separate ``Affects: pyjab-mcp`` marker is needed**: this is the same\n"
    "  ``RuntimeError`` the entry above already marks, with a more specific message.\n"
)


def test_a_sentence_mentioning_the_marker_is_not_a_marker():
    """The real regression, from pyjab's own changelog.

    A marker begins the line; this sentence merely contains the phrase.  Against the
    unanchored pattern this returned one marker, and one unacknowledged marker fails the
    ``guards`` job.
    """
    assert changelog_guard.markers(PYJAB_SENTENCE_MENTIONING_THE_MARKER) == []


def test_a_marker_and_a_sentence_about_it_are_told_apart():
    """The pair as pyjab wrote it, so "no markers at all" cannot pass this.

    The note above is a marker and the sentence below it is not, and the check has to say
    exactly that: the sentence on its own cannot tell a fixed check from one that stopped
    finding markers altogether.
    """
    text = (
        "1.11.0 - 2026-10-11\n"
        "-------------------\n"
        "\n"
        "  **Affects: pyjab-mcp**, first released in **1.11.0**. A JAB call that returns\n"
        "  falsy now raises ``RuntimeError`` from inside the call.\n"
        "\n"
        "  could reach. **No separate ``Affects: pyjab-mcp`` marker is needed**: this is the\n"
        "  same ``RuntimeError`` the entry above already marks.\n"
    )

    found = changelog_guard.markers(text)

    assert len(found) == 1
    assert found[0].line.startswith("**Affects: pyjab-mcp**")


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
    first = changelog_guard.markers("* **Affects: pyjab-mcp** and something.\n")[0]
    second = changelog_guard.markers("* **Affects: pyjab-mcp** and something else.\n")[0]

    assert first.id != second.id
    assert changelog_guard.check([second], {first.id: "answered the old wording"})


def test_the_changelog_guard_reports_a_source_it_could_not_read(tmp_path, capsys):
    """'I could not read it' is not 'nothing was marked' -- AGENTS.md 2.4.

    An explicitly named source is the one case where falling through is worse than failing:
    the sibling checkout on this machine *does* exist, so the old chain answered for that
    file and exited 0 while the caller asked about this one.
    """
    missing = tmp_path / "nowhere" / "CHANGELOG.rst"

    text, where = changelog_guard.read_changelog(str(missing))

    assert text is None, "a named source that cannot be read fell through to another changelog"
    assert str(missing) in where


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


def test_the_changelog_guard_refuses_an_explicit_source_it_cannot_read(tmp_path):
    """The defect of issue #64, on the command line and without ``--strict``.

    ``--changelog PATH`` used to be only the first *candidate*: a path that was not there was
    skipped, and the guard read the sibling checkout instead and exited 0. This machine has
    that sibling, so the old behaviour was "checked, and it is fine" about a file nobody read.
    The empty string is here because "named" is not "truthy": ``--changelog ""`` used to be a
    quiet way back into the chain.
    """
    missing = tmp_path / "nowhere" / "CHANGELOG.rst"
    script = str(REPO_ROOT / "tools" / "check_pyjab_changelog.py")

    for given in (str(missing), ""):
        result = subprocess.run(
            [sys.executable, script, "--changelog", given],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
        )

        assert result.returncode == 1, (
            f"a named changelog ({given!r}) that cannot be read has to refuse, with or "
            f"without --strict:\n{result.stdout}\n{result.stderr}"
        )
        assert "UNAVAILABLE" in result.stdout
        assert "PASSED" not in result.stdout, (
            "the guard reported a pass for a file it could not read -- it read another changelog"
        )
        if given:
            assert given in result.stdout, (
                "the refusal has to name the file that was asked for"
            )
        else:
            assert "was named explicitly" in result.stdout, (
                "the empty string is a named source too, and the refusal has to say so"
            )


def test_the_strict_flag_is_what_makes_an_unreadable_source_fail(tmp_path, monkeypatch, capsys):
    """The strict/non-strict difference, made visible rather than remembered (issue #64).

    With no source named the chain is deliberately advisory: a network hiccup must not redden
    a developer's local run. The release pipeline is where that default is wrong, which is why
    ``release.yml`` passes ``--strict`` -- pinned by the workflow case below.
    """
    monkeypatch.delenv("PYJAB_CHANGELOG", raising=False)
    monkeypatch.setattr(changelog_guard, "SIBLING", tmp_path / "nowhere" / "CHANGELOG.rst")
    monkeypatch.setattr(changelog_guard, "RAW_URL", "http://127.0.0.1:1/CHANGELOG.rst")

    assert changelog_guard.main([]) == 0, "the no-argument default is report-only"
    assert "UNAVAILABLE" in capsys.readouterr().out
    assert changelog_guard.main(["--strict"]) == 1, (
        "`--strict` has to turn an unreadable changelog into a failure -- it is the whole "
        "reason the release path passes it"
    )


# ---------------------------------------------------------------------------------
# The scripts themselves, which is what CI runs
# ---------------------------------------------------------------------------------

GUARD_SCRIPTS = (
    "check_pyjab_api_surface.py",
    "check_documented_surface.py",
    "check_local_only_files.py",
    # Report-only, and exits 0 with nothing required: that is its whole point.
    "check_pyjab_readiness.py",
    "check_changelog_immutable.py",
    "check_published_references.py",
)


@pytest.mark.parametrize("script", GUARD_SCRIPTS)
def test_the_guard_script_runs_and_exits_zero(script):
    """The functions above can pass while the command line is broken.

    This is the **argument-free** subset: each script here exits 0 against this repository
    with no arguments, which is why the cases are bare.  The other five of the 11 guards in
    `tools/` are covered differently, and naming the split is the point (issue #40) -- a
    list whose *scope* is unnamed is read as wider than it is.

    * `check_dist_contents.py` and `check_release_version.py` need an input only a release
      produces -- a built sdist and wheel, and a tag the tree agrees with.  Their *refusing*
      forms are the two cases right below, with their arguments; their *passing* forms are
      CI's: `python -m build` then `--dist dist` in `ci.yml`'s `package` job, and
      `check_release_version.py "$TAG"` in `release.yml`'s `verify` job.  A test that built
      an sdist here would be measuring `build` rather than the guard.
    * `check_pyjab_changelog.py`, `check_release_l3.py` and `check_release_unpublished.py`
      already have their own command-line cases with arguments further down this file
      (`test_the_changelog_guard_script_answers_for_one_explicit_file`,
      `test_the_l3_gate_script_is_what_the_workflow_runs` and
      `test_the_unpublished_gate_script_is_what_the_workflow_runs`), so they are in neither
      list here.  For the changelog guard a bare case would be worse than none: with no
      `--changelog` it reads the sibling checkout, then `$PYJAB_CHANGELOG`, then GitHub, and
      without `--strict` an *unreadable* changelog prints `UNAVAILABLE` and exits 0 -- a
      green that cannot tell "every marker is answered" from "nothing was read"
      (AGENTS.md 2.4).  The network-reading form runs in CI instead (`--strict`, in
      `ci.yml`'s `guards` job, where the network is).

    One entry *within* this list is narrower than the rest, and the difference is the one
    issue #48 is about: `check_published_references.py` resolves a citation against the
    files on disk, and CI checks out none of the local-only ones, so there it runs the name
    half only and exits 0 **by vacuity**.  This case therefore may not be read as the
    resolving half being proved by CI.
    `test_no_shipped_file_in_this_repository_cites_a_local_only_path`, below, is where that
    difference is visible -- on a machine that has the files, which is not a CI runner.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / script)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, f"{script} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"


#: The two guards above whose *passing* form needs a built artifact or a real tag, as
#: ``(script, argv from tmp_path, the line the refusal has to carry)``.  The argv is a
#: function of ``tmp_path`` because the hermetic input *is* the temporary directory: what
#: `check_dist_contents.py` must report when nothing has been built, and what
#: `check_release_version.py` must report for a tag no file in this tree agrees with.
GUARD_SCRIPTS_REFUSING = (
    pytest.param(
        "check_release_version.py",
        lambda tmp_path: ("v9.9.9",),
        "CANNOT RELEASE v9.9.9",
        id="release-version-against-this-tree",
    ),
    pytest.param(
        "check_dist_contents.py",
        lambda tmp_path: ("--dist", str(tmp_path / "nowhere")),
        "NO DIST DIRECTORY",
        id="dist-contents-with-nothing-built",
    ),
)


@pytest.mark.parametrize("script, arguments, complaint", GUARD_SCRIPTS_REFUSING)
def test_the_guard_script_that_needs_arguments_refuses_a_wrong_one(
    tmp_path, script, arguments, complaint
):
    """A green command line proves nothing for a guard that needs a release to go green.

    What has to hold either way is the argument form and the refusal -- the exit status and
    the line that names what is wrong -- so both are asserted, and they are the ones the
    pipeline that does supply the real input depends on.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / script), *arguments(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 1, (
        f"{script} exited {result.returncode} on an input it has to refuse:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert complaint in result.stdout, (
        f"{script} refused, but not with {complaint!r}:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------------
# tools/check_release_version.py
# ---------------------------------------------------------------------------------


def test_the_release_guard_refuses_a_version_that_disagrees_with_itself():
    """Three files name the release; PyPI cannot be un-published, so they have to agree first."""
    problems = release_guard.check("v9.9.9")

    assert any("__init__.py" in problem for problem in problems)
    assert any("server.json" in problem for problem in problems)
    assert any("CHANGELOG.md" in problem for problem in problems)


def test_the_release_guard_refuses_a_tag_that_is_not_a_release():
    problems = release_guard.check("nightly-2026-10-10")

    assert len(problems) == 1
    assert "not a release tag" in problems[0]


def test_the_release_guard_accepts_a_tree_that_agrees_with_itself(tmp_path, monkeypatch):
    """The passing path, proved rather than assumed."""
    import json

    import pyjab_mcp

    (tmp_path / "server.json").write_text(json.dumps({
        "version": "0.1.0",
        "packages": [{"identifier": "pyjab-mcp", "version": "0.1.0"}],
    }), encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## [0.1.0] - 2026-10-10\n\n### Added\n\n- it works\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pyjab_mcp, "__version__", "0.1.0")

    assert release_guard.check("v0.1.0") == []
    assert release_guard.check("0.1.0") == [], "the v is conventional, not required"


def test_the_release_notes_come_from_the_changelog_section(tmp_path, monkeypatch):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n- in flight\n\n"
        "## [0.2.0] - 2026-11-01\n\n### Added\n\n- the new thing\n\n"
        "## [0.1.0] - 2026-10-10\n\n- the old thing\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release_guard, "REPO_ROOT", tmp_path)

    notes = release_guard.section("v0.2.0")

    assert "the new thing" in notes
    assert "the old thing" not in notes, "a section ends at the next heading"
    assert "in flight" not in notes, "Unreleased is not a release's notes"


# ---------------------------------------------------------------------------------
# tools/check_release_l3.py -- the release gate's verdict, and the invariant it trusts
# ---------------------------------------------------------------------------------
#
# The verdict used to be fifteen cases of Python inside release.yml's `run:` block, where
# nothing could exercise it: a regression would have been noticed by a failed release. These
# cases are the ones that block a tag, each pinned by its exit status **and** the text that
# tells its refusal apart from the others -- "no green run" and "could not read the answer"
# both refuse, and must never swap.

#: The commit the gate is asked about, so the expected text is readable.
L3_SHA = "5f2a1c9d4e6b8a0c"


def l3_body(tmp_path, payload, name="body.json"):
    """An Actions API answer, or a deliberately wrong stand-in, as the file curl wrote."""
    path = tmp_path / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return path


def l3_verdict(tmp_path, payload, http_code="200", dry_run=False):
    """The exit status and the lines the step would print, for one answer."""
    decision = l3_guard.decide(
        l3_body(tmp_path, payload), http_code, L3_SHA, dry_run=dry_run
    )
    return decision.exit_code, "\n".join(decision.lines)


def l3_answer(*runs, total=None):
    """The shape the Actions API returns; *total* models a truncated page."""
    return {
        "total_count": len(runs) if total is None else total,
        "workflow_runs": list(runs),
    }


def a_run(run_id, conclusion, status="completed"):
    return {
        "id": run_id,
        "event": "schedule",
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example.test/runs/{run_id}",
    }


def test_the_l3_gate_proceeds_on_a_successful_run_and_names_it(tmp_path):
    exit_code, text = l3_verdict(tmp_path, l3_answer(a_run(7, "success")))

    assert exit_code == 0
    assert f"windows-gui.yml at {L3_SHA}: 1 run(s) match, 1 read" in text
    assert "  run 7: event=schedule status=completed conclusion=success" in text
    assert f"PASSED: run 7 of windows-gui.yml succeeded at {L3_SHA}" in text
    assert "https://example.test/runs/7" in text, "the run the release rode on has to be named"


def test_the_l3_gate_finds_a_green_run_that_is_not_the_first(tmp_path):
    """Every candidate is scanned: a later run can be the green one."""
    exit_code, text = l3_verdict(tmp_path, l3_answer(a_run(1, "failure"), a_run(2, "success")))

    assert exit_code == 0
    assert "PASSED: run 2" in text


def test_the_l3_gate_refuses_candidates_where_none_succeeded(tmp_path):
    exit_code, text = l3_verdict(
        tmp_path,
        l3_answer(a_run(1, "failure"), a_run(2, None, status="in_progress")),
    )

    assert exit_code == 1
    assert "::error::" in text
    assert f"no run of windows-gui.yml at {L3_SHA} succeeded; rejected:" in text
    assert "1 (completed/failure)" in text
    assert "2 (in_progress/None)" in text, "a run still going is not a run that passed"


def test_the_l3_gate_refuses_a_truncated_page_as_truncated_not_as_no_green_run(tmp_path):
    """Candidates that were never read are not 'no green run' either."""
    exit_code, text = l3_verdict(tmp_path, l3_answer(a_run(1, "failure"), total=150))

    assert exit_code == 1
    assert "150 runs match" in text
    assert "but only 1 were read, so a green one cannot be ruled out" in text
    assert "succeeded; rejected" not in text, (
        "a truncated page must not be reported as 'none of the runs I read was green'"
    )


def test_the_l3_gate_refuses_a_commit_that_was_never_driven_end_to_end(tmp_path):
    exit_code, text = l3_verdict(tmp_path, l3_answer())

    assert exit_code == 1
    assert "0 run(s) match, 0 read" in text
    assert f"no windows-gui.yml run exists at {L3_SHA}: this commit was never driven" in text
    assert "succeeded; rejected" not in text, "no runs at all is not 'no green run'"


@pytest.mark.parametrize("code", ["500", "000"])
def test_the_l3_gate_refuses_an_unreadable_answer_as_unread(tmp_path, code):
    """`000` is curl's 'no HTTP response at all'; neither is a verdict about L3."""
    exit_code, text = l3_verdict(tmp_path, '{"message":"boom"}', http_code=code)

    assert exit_code == 1
    assert "the Actions API could not be read" in text
    assert f"(HTTP {code})" in text
    assert "no run of windows-gui.yml" not in text, (
        "an answer that could not be read is not evidence that no green run exists"
    )
    assert "never driven" not in text


@pytest.mark.parametrize("code", ["401", "403"])
def test_the_l3_gate_keeps_the_actions_read_hint(tmp_path, code):
    exit_code, text = l3_verdict(tmp_path, '{"message":"nope"}', http_code=code)

    assert exit_code == 1
    assert f"HTTP {code} from the Actions API usually means this workflow is missing" in text
    assert "actions: read" in text
    assert "a green dry run does not prove the permission" in text
    assert "a malformed body warns the same way" in text, (
        "a dry run is green however the gate refuses, and the hint has to say so"
    )


def test_the_l3_gate_shows_the_head_of_an_answer_it_could_not_use(tmp_path):
    """For an API error the body is the only evidence a maintainer gets, so it is printed.

    The step used `head -c 2000` before the verdict moved here. `return []` or a shorter slice
    would leave every verdict above green, and a maintainer staring at an HTTP 403 with no body
    has nothing to read.
    """
    exit_code, text = l3_verdict(tmp_path, "x" * 2000 + "TAIL-MUST-NOT-APPEAR", http_code="500")

    assert l3_guard.BODY_PREVIEW == 2000, "the previous step printed 2000 bytes of the answer"
    assert exit_code == 1
    assert "x" * 2000 in text
    assert "TAIL-MUST-NOT-APPEAR" not in text, "the preview is capped, not the whole body"


def test_the_l3_gate_refuses_a_body_it_cannot_parse(tmp_path):
    exit_code, text = l3_verdict(tmp_path, "{not json")

    assert exit_code == 1
    assert "the Actions API answer could not be read" in text
    assert "unverified, which is not the same as satisfied" in text
    assert "never driven" not in text


@pytest.mark.parametrize(
    "payload",
    [
        [1, 2, 3],
        {"total_count": 1, "workflow_runs": "nope"},
        {"total_count": 1, "workflow_runs": ["nope"]},
    ],
    ids=["a-json-list", "runs-not-a-list", "a-run-that-is-not-an-object"],
)
def test_the_l3_gate_refuses_a_wrongly_shaped_body_as_unread(tmp_path, payload):
    exit_code, text = l3_verdict(tmp_path, payload)

    assert exit_code == 1
    assert "the Actions API answer could not be read" in text
    assert "no run of windows-gui.yml" not in text


DRY_RUN_REFUSALS = [
    pytest.param(l3_answer(a_run(1, "failure")), "200", id="none-succeeded"),
    pytest.param(l3_answer(a_run(1, "failure"), total=150), "200", id="truncated"),
    pytest.param(l3_answer(), "200", id="no-runs"),
    pytest.param('{"message":"boom"}', "500", id="http-500"),
    pytest.param("{not json", "200", id="unparsable"),
    pytest.param({"total_count": 1, "workflow_runs": "nope"}, "200", id="wrong-shape"),
]


@pytest.mark.parametrize("payload, code", DRY_RUN_REFUSALS)
def test_a_dry_run_warns_where_a_tag_push_refuses(tmp_path, payload, code):
    """`workflow_dispatch` runs on main's HEAD, where no L3 run normally exists yet.

    The version guard in the same job already warns instead of failing there, and a green dry
    run is what makes the `actions: read` permission unprovable from a dispatch -- hence the
    hint the previous case pins.
    """
    tag_exit, _ = l3_verdict(tmp_path, payload, http_code=code)
    dry_exit, dry_text = l3_verdict(tmp_path, payload, http_code=code, dry_run=True)

    assert tag_exit == 1
    assert dry_exit == 0
    assert "::warning::" in dry_text
    assert "::error::" not in dry_text


def test_the_l3_gate_script_is_what_the_workflow_runs(tmp_path):
    """The function above can pass while the command line -- or the workflow -- is broken."""
    body = l3_body(tmp_path, l3_answer(a_run(1, "failure")))
    script = str(REPO_ROOT / "tools" / "check_release_l3.py")
    argv = [sys.executable, script, "--body", str(body), "--http-code", "200",
            "--sha", L3_SHA]

    refused = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180)
    warned = subprocess.run(argv + ["--dry-run"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=180)

    assert refused.returncode == 1
    assert "no run of windows-gui.yml" in refused.stdout
    assert warned.returncode == 0
    assert "::warning::" in warned.stdout


def test_the_release_workflow_wires_the_gate_so_that_it_can_still_refuse():
    """A tool the workflow does not call -- or cannot fail through -- is not a gate.

    Every one of these mutations left the tool's own tests green: appending `|| true`, dropping
    the `000` fallback, deleting the `--dry-run` branch, inverting the event test, swapping
    `--body` for `--http-code`, or making the step conditional. The tool is only worth what its
    wiring is, so for a release gate the brittle assertion is the point: change the wiring
    deliberately, and change this test in the same commit.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]
    step = next(
        (step for step in steps if step.get("name") == "L3 was green on this commit (windows-gui.yml)"),
        None,
    )

    assert step is not None, "the L3 gate step was renamed or removed"
    script = step["run"]

    required = (
        # the event decides it: a push must refuse, a dispatch must warn and pass
        '[ "${{ github.event_name }}" != "push" ]',
        "DRY_RUN=(--dry-run)",
        '"${DRY_RUN[@]}"',
        # curl's exit status is not the HTTP status; 000 is "no response at all"
        '|| CODE="000"',
        # the arguments, in the form the tool reads them
        '--body "$BODY"',
        '--http-code "$CODE"',
        '--sha "$GITHUB_SHA"',
        "tools/check_release_l3.py",
    )
    for fragment in required:
        assert fragment in script, (
            f"the L3 step no longer contains {fragment!r}, so the gate can stop refusing a "
            "release -- or start deciding on the wrong input -- while every test of the tool "
            "stays green."
        )

    for fragment in ("|| true", "2>/dev/null"):
        assert fragment not in script, (
            f"the L3 step contains {fragment!r}. A gate that cannot fail is decoration, and "
            "this step's non-zero exit is the only thing that stops a tag."
        )

    assert "if" not in step, (
        "the L3 gate step is conditional, so `verify` can go green with the gate never run"
    )
    assert not step.get("continue-on-error"), (
        "the L3 gate step ignores its own failure, so the release proceeds however it decides"
    )
    assert "<<'PY'" not in script, (
        "the verdict logic is back in the YAML, where tests/test_guards.py cannot reach it"
    )


def windows_gui_jobs():
    """The workflow whose run conclusion the release gate reads, parsed into jobs."""
    path = REPO_ROOT / ".github" / "workflows" / "windows-gui.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}
    assert jobs, "windows-gui.yml declares no jobs, so these pins checked nothing"
    return jobs


def test_nothing_in_windows_gui_can_leave_the_run_green_without_running():
    """GitHub's own syntax reference, which is why this rule is not just about jobs:

    * ``jobs.<job_id>.steps[*].if`` -- "prevent a step from running";
    * ``jobs.<job_id>.steps[*].continue-on-error`` -- "prevents a job from failing when a step
      fails";
    * ``jobs.<job_id>.continue-on-error`` -- "prevents a workflow run from failing when a job
      fails";
    * ``jobs.<job_id>.if`` -- skips the job entirely.

    None of those turns the *run* red. ``tools/check_release_l3.py`` reads the run's
    ``conclusion``, so a release gate that trusts it would accept a green run in which nothing
    was driven -- which is the risk #30 is about. The only exemption is the artifact upload, whose
    ``if: always()`` exists so the evidence survives a failure.

    If a legitimate ``if:`` or ``continue-on-error`` is ever wanted elsewhere here, the answer is
    to revisit the gate -- check the job and step conclusions it is given -- not this pin.
    """
    jobs = windows_gui_jobs()

    skipped_jobs = sorted(name for name, job in jobs.items() if "if" in (job or {}))
    ignored_jobs = sorted(
        name for name, job in jobs.items() if (job or {}).get("continue-on-error")
    )
    assert skipped_jobs == [], (
        f"windows-gui.yml declares a job-level `if:` on {skipped_jobs}. A skipped job leaves the "
        "run's conclusion at success, so the release gate would accept a green run in which that "
        "job never ran."
    )
    assert ignored_jobs == [], (
        f"windows-gui.yml declares a job-level `continue-on-error` on {ignored_jobs}. A failing "
        "job that is ignored leaves the run green, and the release gate reads the run."
    )

    offenders = []
    for job_name, job in jobs.items():
        for step in (job or {}).get("steps") or []:
            where = f"{job_name}/{step.get('name') or step.get('uses')}"
            if step.get("continue-on-error"):
                offenders.append(f"{where} sets continue-on-error")
            if "if" in step and not str(step.get("uses", "")).startswith(
                "actions/upload-artifact"
            ):
                offenders.append(f"{where} is conditional: {step['if']}")
    assert offenders == [], (
        "nothing in windows-gui.yml may be skippable or non-fatal except the artifact upload, whose "
        "`if: always()` is how the evidence survives a failure -- and this is a release gate, so "
        f"the constraint is deliberate: {offenders}"
    )


def test_the_windows_gui_suite_step_exists_and_would_really_drive_a_window():
    """The step the whole run exists for: present by name, and unable to be skipped or neutered."""
    e2e = windows_gui_jobs().get("e2e") or {}
    suite = [step for step in e2e.get("steps") or [] if step.get("name") == "Run the L3 suite"]

    assert len(suite) == 1, (
        "windows-gui.yml no longer has exactly one step named 'Run the L3 suite'. If it was "
        "renamed, retarget this pin; if it was deleted, the release gate can accept a green run "
        "that never drove a window."
    )
    step = suite[0]
    assert "tests/e2e" in step.get("run", ""), (
        f"the L3 suite step no longer runs tests/e2e: {step.get('run')!r}"
    )
    assert str(step.get("env", {}).get("PYJAB_MCP_E2E")) == "1", (
        "without PYJAB_MCP_E2E=1 every test in tests/e2e is skipped and pytest still exits 0, "
        "so the run would be green with no window driven -- see tests/e2e/test_live_window.py"
    )
    assert "if" not in step, "the step that drives the window is conditional"
    assert not step.get("continue-on-error"), "the step that drives the window cannot fail the job"


# ---------------------------------------------------------------------------------
# tools/check_release_unpublished.py -- the pre-flight, before anything is published
# ---------------------------------------------------------------------------------
#
# `github-release` needs only `verify`, while the job that actually refuses a duplicate
# version (`pypi`) waits behind an approval, so a version that is already on PyPI used to
# reach the release job first and produce a GitHub release with no matching package. Every
# branch of that refusal is pinned below by its exit status **and** the text that tells it
# apart from the others: "already published" and "the index could not be read" both refuse,
# and a check that cannot tell them apart passes exactly when it is blind.

#: The version the tag under test names, so the expected text is readable.
UNPUBLISHED_VERSION = "0.2.0"


def unpublished_body(tmp_path, payload, name="pypi.json"):
    """A PyPI JSON answer, or a deliberately wrong stand-in, as the file curl wrote."""
    path = tmp_path / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return path


def unpublished_verdict(
    tmp_path, pypi_payload, pypi_code="404", release_code=None, dry_run=False
):
    """The exit status and the lines the step would print, for one pair of answers."""
    decision = unpublished_guard.decide(
        UNPUBLISHED_VERSION,
        pypi_code,
        unpublished_body(tmp_path, pypi_payload),
        release_code=release_code,
        dry_run=dry_run,
    )
    return decision.exit_code, "\n".join(decision.lines)


def a_pypi_answer(version):
    """The shape PyPI's JSON API returns; only ``info.version`` is read."""
    return {"info": {"name": "pyjab-mcp", "version": version}}


def test_the_unpublished_gate_proceeds_on_a_404_and_names_the_url_it_checked(tmp_path):
    exit_code, text = unpublished_verdict(tmp_path, a_pypi_answer(UNPUBLISHED_VERSION))

    assert exit_code == 0
    assert f"PASSED: pyjab-mcp {UNPUBLISHED_VERSION} is not on PyPI" in text
    assert (
        f"https://pypi.org/pypi/pyjab-mcp/{UNPUBLISHED_VERSION}/json" in text
    ), "the log has to say what 'unpublished' was measured against"


def test_the_unpublished_gate_refuses_a_200_and_says_a_new_version_is_needed(tmp_path):
    exit_code, text = unpublished_verdict(tmp_path, a_pypi_answer("0.1.2"), pypi_code="200")

    assert exit_code == 1
    assert "::error::" in text
    assert "pyjab-mcp 0.2.0 is already published" in text
    assert "https://pypi.org/pypi/pyjab-mcp/0.2.0/json" in text
    assert "a published version cannot be replaced, so this release needs a new version" in text


def test_the_unpublished_gate_prints_the_version_the_index_reports(tmp_path):
    exit_code, text = unpublished_verdict(tmp_path, a_pypi_answer("0.2.0"), pypi_code="200")

    assert exit_code == 1
    assert "The index reports version 0.2.0" in text


def test_the_unpublished_gate_says_so_when_the_index_disagrees_about_the_version(tmp_path):
    """If the index answers for another version, the check read the wrong input."""
    exit_code, text = unpublished_verdict(tmp_path, a_pypi_answer("0.3.0"), pypi_code="200")

    assert exit_code == 1
    assert "0.3.0" in text
    assert "not '0.2.0'" in text
    assert "confused about which version it asked for" in text


@pytest.mark.parametrize("code", ["000", "500", "403"])
def test_the_unpublished_gate_refuses_an_index_it_could_not_read(tmp_path, code):
    """`000` is curl's 'no HTTP response at all'; a network failure is not permission."""
    exit_code, text = unpublished_verdict(tmp_path, '{"message":"boom"}', pypi_code=code)

    assert exit_code == 1
    assert "the index could not be read" in text
    assert f"(HTTP {code}" in text
    assert "unverified, which is not the same as 'not published'" in text
    assert "is already published" not in text, (
        "an index that could not be read is not evidence that the version is there"
    )


def test_the_unpublished_gate_still_refuses_a_200_whose_body_it_cannot_parse(tmp_path):
    """The status says the version is there, so an unreadable body is not 'unread'.

    It is a *published* version the tool cannot double-check, and the line has to say which
    of the two it is -- otherwise a 200 with a bad body would read as 'the index was down'.
    """
    exit_code, text = unpublished_verdict(tmp_path, "{not json", pypi_code="200")

    assert exit_code == 1
    assert "could not be parsed" in text
    assert "the index answered 200" in text, (
        "the headline has to lead with what was seen -- HTTP 200 whose body is not PyPI's JSON, "
        "which a proxy or a captive portal produces and which is not 'the index was down'"
    )
    assert "the index could not be read" not in text, (
        "a 200 is a verdict about the version, not a failure to read the index"
    )


@pytest.mark.parametrize(
    "release_code, expected",
    [
        pytest.param("200", "already exists for this tag", id="already-there"),
        pytest.param("404", "does not exist yet", id="absent"),
        pytest.param("000", "could not be read", id="unread"),
    ],
)
def test_the_release_code_is_informational_and_never_changes_the_verdict(
    tmp_path, release_code, expected
):
    """A re-run of a tag whose release was already created is a legitimate state.

    `github-release` needs only `verify`, so a 200 here is what a re-run after a pending
    approval looks like. Refusing on it would block an otherwise correct release, and that is
    why this must not be "fixed" into a failure later.
    """
    exit_code, text = unpublished_verdict(
        tmp_path, a_pypi_answer(UNPUBLISHED_VERSION), release_code=release_code
    )

    assert exit_code == 0, "the release answer must never decide"
    assert expected in text
    assert "::error::" not in text


def test_the_release_code_stays_informational_when_the_index_refuses(tmp_path):
    """The two answers are independent: a release that exists does not soften a 200."""
    exit_code, text = unpublished_verdict(
        tmp_path, a_pypi_answer("0.2.0"), pypi_code="200", release_code="200"
    )

    assert exit_code == 1
    assert "is already published" in text
    assert "already exists for this tag" in text


def test_the_unpublished_gate_says_nothing_about_a_release_it_was_not_asked_about(tmp_path):
    """The release half is optional; passing no code must not invent an answer."""
    _, text = unpublished_verdict(tmp_path, a_pypi_answer(UNPUBLISHED_VERSION))

    assert "GitHub release" not in text


UNPUBLISHED_REFUSALS = [
    pytest.param(a_pypi_answer("0.2.0"), "200", id="already-published"),
    pytest.param("{not json", "200", id="a-200-it-cannot-parse"),
    pytest.param('{"message":"boom"}', "000", id="no-response"),
    pytest.param('{"message":"boom"}', "500", id="http-500"),
    pytest.param('{"message":"boom"}', "403", id="http-403"),
]


@pytest.mark.parametrize("payload, code", UNPUBLISHED_REFUSALS)
def test_a_dry_run_warns_where_a_tag_push_refuses(tmp_path, payload, code):
    """`workflow_dispatch` runs on main's HEAD, where the version is normally unpublished.

    The version guard and the L3 gate in the same job already warn instead of failing there,
    and -- like them -- this changes the annotation and the exit status, never the verdict.
    """
    tag_exit, _ = unpublished_verdict(tmp_path, payload, pypi_code=code)
    dry_exit, dry_text = unpublished_verdict(tmp_path, payload, pypi_code=code, dry_run=True)

    assert tag_exit == 1
    assert dry_exit == 0
    assert "::warning::" in dry_text
    assert "::error::" not in dry_text


def test_the_unpublished_gate_script_is_what_the_workflow_runs(tmp_path):
    """The function above can pass while the command line -- or the workflow -- is broken."""
    body = unpublished_body(tmp_path, a_pypi_answer("0.2.0"))
    script = str(REPO_ROOT / "tools" / "check_release_unpublished.py")
    argv = [
        sys.executable, script,
        "--version", UNPUBLISHED_VERSION,
        "--pypi-code", "200",
        "--pypi-body", str(body),
        "--release-code", "200",
    ]

    refused = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180)
    warned = subprocess.run(argv + ["--dry-run"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=180)

    assert refused.returncode == 1
    assert "is already published" in refused.stdout
    assert "already exists for this tag" in refused.stdout
    assert warned.returncode == 0
    assert "::warning::" in warned.stdout
    assert "::error::" not in warned.stdout


def test_the_release_workflow_checks_this_before_the_l3_gate_and_before_publishing():
    """A tool the workflow does not call -- or cannot fail through -- is not a gate.

    Every one of these mutations left the tool's own tests green: appending `|| true`, dropping
    the `000` fallback, removing the version output it reads, deleting the `--dry-run` branch,
    or moving the step after the gate it is supposed to pre-empt. The tool is only worth what
    its wiring is, so for a release gate the brittle assertion is the point: change the wiring
    deliberately, and change this test in the same commit.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]

    step = next(
        (step for step in steps if step.get("name") == "The version is not already published"),
        None,
    )
    assert step is not None, "the pre-flight step was renamed or removed"
    script = step["run"]

    required = (
        # it reads the version the version guard wrote, not one of its own
        'VERSION="${{ steps.version.outputs.version }}"',
        # the two calls: PyPI's JSON API, and the release for this tag
        "https://pypi.org/pypi/pyjab-mcp/${VERSION}/json",
        "releases/tags/v${VERSION}",
        # curl's exit status is not the HTTP status; 000 is "no response at all"
        '|| PYPI_CODE="000"',
        '|| RELEASE_CODE="000"',
        # the event decides it: a push must refuse, a dispatch must warn and pass
        '[ "${{ github.event_name }}" != "push" ]',
        "DRY_RUN=(--dry-run)",
        '"${DRY_RUN[@]}"',
        # the arguments, in the form the tool reads them
        '--version "$VERSION"',
        '--pypi-code "$PYPI_CODE" --pypi-body "$PYPI_BODY"',
        '--release-code "$RELEASE_CODE"',
        "tools/check_release_unpublished.py",
    )
    for fragment in required:
        assert fragment in script, (
            f"the pre-flight step no longer contains {fragment!r}, so the gate can stop refusing "
            "a release -- or start deciding on the wrong input -- while every test of the tool "
            "stays green."
        )

    for fragment in ("|| true", "2>/dev/null"):
        assert fragment not in script, (
            f"the pre-flight step contains {fragment!r}. A gate that cannot fail is decoration, "
            "and this step's non-zero exit is what stops a tag for a version already on PyPI."
        )

    assert "if" not in step, (
        "the pre-flight step is conditional, so `verify` can go green with the gate never run"
    )
    assert not step.get("continue-on-error"), (
        "the pre-flight step ignores its own failure, so a duplicate version is released anyway"
    )
    assert "<<'PY'" not in script, (
        "the verdict logic is back in the YAML, where tests/test_guards.py cannot reach it"
    )

    # Position: after the version guard (whose output it reads) and before the L3 gate, whose
    # job it is to pre-empt -- a check that runs after the fact is not a pre-flight.
    names = [s.get("name") for s in steps]
    guard = steps.index(next(s for s in steps if s.get("id") == "version"))
    here = names.index("The version is not already published")
    l3_gate = names.index("L3 was green on this commit (windows-gui.yml)")

    assert guard < here, "the pre-flight no longer runs after the version guard it depends on"
    assert here < l3_gate, (
        "the pre-flight now runs after the L3 gate, so it no longer precedes the job that "
        "would refuse this run"
    )


# The arguments that are the gate's input, and the call they have to end on. A step can contain
# every string the wiring pin above asks for and still be disarmed -- an assignment inserted
# after the pinned one, a shim step rewriting the tool, or a `|| exit 0` on the call -- so these
# are the predicates that separate the baseline from a bypass.
UNPUBLISHED_STEP = "The version is not already published"
UNPUBLISHED_INVOCATION = '--release-code "$RELEASE_CODE" "${DRY_RUN[@]}"'


def unpublished_step():
    """The pre-flight step as the workflow declares it."""
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]
    step = next((s for s in steps if s.get("name") == UNPUBLISHED_STEP), None)
    assert step is not None, "the pre-flight step was renamed or removed"
    assert "run" in step, "the pre-flight step has no `run:`, so nothing checks the version"
    return step


def test_the_pre_flight_control_flow_cannot_swallow_the_refusal():
    """The invocation has to be the last thing the shell does, and its inputs unchanged.

    A gate is only as good as the status the step returns. The reviewer demonstrated each of
    these with the pin above still green, and with the step passing against a genuine PyPI 200:
    `|| exit 0` on the invocation, `if ! python ...; then ...; fi`, a `set +e` before it, and
    `VERSION="0.0.0"` or `PYPI_CODE="404"` assigned *after* the pinned assignment (so the step
    asked about the wrong version, or hard-coded the answer that proceeds).
    """
    script = unpublished_step()["run"]
    lines = script.splitlines()
    # Comments are stripped so that a commented-out invocation is not mistaken for the last one.
    code = [line.rstrip() for line in lines if line.strip() and not line.lstrip().startswith("#")]

    calls = [i for i, line in enumerate(code) if "check_release_unpublished.py" in line]
    assert len(calls) == 1, (
        f"the pre-flight step names the tool {len(calls)} times; exactly one line may call it, "
        "or a condition or a shim can decide which of them runs"
    )
    call = calls[0]
    # The invocation spans several lines with trailing backslashes, so it is the last *command*
    # even though it is not the last line: every line after the tool's name is a continuation.
    reach = [line for line in code[call + 1:] if line.strip() and not line[:1].isspace()]
    assert reach == [], (
        f"something runs after the invocation of tools/check_release_unpublished.py: {reach!r}. "
        "The invocation has to be the last command -- an `|| exit 0`, an `if ! ...; then`, or a "
        "trailing `echo` all let a refusal through."
    )
    continuations = "\n".join(code[call + 1:])
    for operator in (";", "&&", "|", "<", "$("):
        assert operator not in continuations, (
            f"the invocation's argument lines contain {operator!r}, so the call has been chained "
            "to something that can swallow its exit status"
        )
    assert code[-1].rstrip().endswith(UNPUBLISHED_INVOCATION), (
        f"the invocation no longer ends with {UNPUBLISHED_INVOCATION!r}; it has been chained to "
        "something, or its arguments have changed"
    )
    # A step can also be disarmed by turning off the shell's failure handling before the call.
    for disarmed in ("set +e", "set +o errexit"):
        assert disarmed not in script, (
            f"the pre-flight step contains {disarmed!r}, so a non-zero exit need not reach the "
            "job"
        )
    for name in ("VERSION", "PYPI_CODE"):
        assignments = [line for line in code if re.match(rf"\s*{name}=", line)]
        assert len(assignments) == 1, (
            f"{name} is assigned {len(assignments)} times in this step: {assignments!r}. A second "
            "assignment after the curl decides the verdict instead of the index's answer."
        )


def test_the_verify_job_is_exactly_the_release_checks_in_order():
    """The ordered step list, pinned on purpose, because an inserted step can disable the gate.

    The reviewer demonstrated it with a step *before* the pre-flight that rewrote
    `tools/check_release_unpublished.py` into a shim -- the tool's own tests stay green, the
    wiring pin stays green, and the gate passes however the index answers. This is brittle
    deliberately: add a step, and update this list in the same commit.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]
    names = [s.get("name") for s in steps]
    expected = [
        None,  # actions/checkout@v4
        None,  # actions/setup-python@v5
        "Install",
        "The tag, the package, server.json and the changelog have to agree",
        UNPUBLISHED_STEP,
        "L3 was green on this commit (windows-gui.yml)",
        "The portable suite",
        "The guards",
        "Build, and check the artifact rather than the rules",
        None,  # actions/upload-artifact@v4
    ]

    assert names == expected, (
        f"the `verify` job's steps are {names!r}. If a step was added or moved, update this pin "
        "deliberately -- an inserted step can rewrite the tool this gate runs, and the gate's "
        "own pins cannot see it."
    )
    version_steps = [s for s in steps if s.get("id") == "version"]
    assert len(version_steps) == 1, (
        "the `version` output comes from somewhere other than exactly one step, so the "
        "pre-flight may be reading a version nobody else agrees with"
    )


def test_the_release_pipeline_runs_the_changelog_guard_as_strictly_as_ci_does():
    """Issue #64 defect 2: the tag path used to run the guard without ``--strict``.

    ``ci.yml``'s `guards` job passes it; `release.yml`'s `verify` did not, so an
    unacknowledged marker or an unreadable changelog was printed and the release proceeded --
    the one path where §1a says an unanswered marker must not be green. Read the invocation
    rather than trusting the source, and refuse a *second*, advisory invocation too.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify"]["steps"]
    step = next((s for s in steps if s.get("name") == "The guards"), None)

    assert step is not None, "the `verify` job's guards step was renamed or removed"
    lines = shell_lines(step)
    invocations = [line for line in lines if "check_pyjab_changelog.py" in line]

    assert invocations == ["python tools/check_pyjab_changelog.py --strict"], (
        f"`verify` runs the changelog guard as {invocations!r}, and this pin requires exactly "
        "['python tools/check_pyjab_changelog.py --strict']. Without `--strict` an "
        "unacknowledged marker, a stale acknowledgement or an unreadable changelog is printed "
        "and the step exits 0, so the tag proceeds -- the split between ci.yml and release.yml "
        "that issue #64 is about."
    )


def test_the_release_job_graph_keeps_the_half_published_hazard_closed():
    """`github-release` needing only `verify` is the hazard; this pins every job's `needs`.

    The reviewer demonstrated that `github-release` losing its only dependency leaves every
    other pin green -- and that is the exact condition issue #31 is about: the release job then
    runs without the checks, so a duplicate version produces a GitHub release with no package.
    The `mcpb` job is pinned here for the same reason from the other side: it attaches a file
    *to* that release, so its dependencies are what decide whether the attachment lands
    somewhere, and off what.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    def needs(name):
        declared = jobs[name].get("needs")
        return [] if declared is None else [declared] if isinstance(declared, str) else declared

    assert needs("verify") == [], "the release checks must be the job that starts the run"
    assert needs("pypi") == ["verify"], (
        "`pypi` must depend on the checks directly: a tag whose version guard or pre-flight "
        "refused must never reach the upload"
    )
    assert needs("github-release") == ["verify"], (
        "`github-release` must depend on `verify` and nothing else -- it is the job that runs "
        "*before* `pypi` and is why a duplicate version produced a GitHub release with no "
        "matching package. Losing this is how the half-published hazard returns."
    )
    assert needs("registry") == ["verify", "pypi"], (
        "the registry points at a package that should exist first, so its metadata publish must "
        "wait for `pypi`"
    )
    assert needs("mcpb") == ["verify", "github-release"], (
        "`mcpb` attaches the bundle to the GitHub release, so it has to wait for that release "
        "to exist, and it waits for `verify` so the bundle can only be built from an artifact "
        "whose checks passed. Either dependency lost is a bundle built from a dist/ nobody "
        "checked, or one attached to a release that is not there."
    )


def test_the_verify_job_cannot_be_told_to_ignore_its_own_failure():
    """A job-level `continue-on-error` makes `verify` decorative while every pin stays green.

    `windows-gui.yml` has the equivalent pin for the same reason: a gate that cannot turn the
    run red is not a gate. The other jobs' `if:` conditions are checked too -- an `if:` on
    `github-release` would skip it silently, which is a different failure from the one this
    change closes, but the same shape.
    """
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert not jobs["verify"].get("continue-on-error"), (
        "`verify` declares a job-level `continue-on-error`, so a refused release goes green and "
        "every job that needs it still runs"
    )
    for name in ("pypi", "github-release", "registry"):
        assert name in jobs, f"the `{name}` job was renamed or removed"
        assert not jobs[name].get("continue-on-error"), (
            f"`{name}` declares a job-level `continue-on-error`"
        )


# ---------------------------------------------------------------------------------
# The `mcpb` job -- the bundle runs on both triggers, and only the tag touches the release
# ---------------------------------------------------------------------------------
#
# The hazard here is not a wrong verdict, it is a *skipped job*. `github-release` carries
# `if: github.event_name == 'push'`, and GitHub skips a job whose `needs` were skipped, so the
# obvious `needs: [verify, github-release]` would mean the bundle was never built or validated
# on a `workflow_dispatch` -- the one run a maintainer can make before the irreversible step.
# That is why `always()` is part of the contract and not a detail, and why it is pinned.

MCPB_JOB = "mcpb"
MCPB_BUILD_STEP = "Build the MCPB bundle"
MCPB_UPLOAD_STEP = "Attach the bundle to the release"

#: The job's condition, as an *expression* and not as a set of substrings it must contain.
#: Substrings were measured to be worth nothing here: appending `&& github.event_name == 'push'`
#: (which kills the dispatch rehearsal `always()` exists for) or `&& github.event_name ==
#: 'workflow_dispatch'` (which kills the tag attachment, the feature itself) left every pin
#: green. An exact comparison is brittle on purpose: change the condition, change this line.
MCPB_JOB_IF = "always() && needs.verify.result == 'success'"

#: The attach step's condition. It is the tag push *and* the release having been created, because
#: `always()` lets this job run when `github-release` failed, and an ungated upload would then
#: report "release not found" instead of the failure that caused it.
MCPB_UPLOAD_IF = "github.event_name == 'push' && needs.github-release.result == 'success'"

#: The two commands, whole. A step can be disarmed without changing either command, but not
#: without changing the line: `; exit 0`, `|| true`, `|| echo skipped` and a trailing
#: `--clobber` are all inside what is compared.
MCPB_BUILD_COMMAND = "python tools/build_mcpb.py --dist dist --out dist --validate-with-mcpb"
MCPB_UPLOAD_COMMAND = 'gh release upload "${{ github.ref_name }}" dist/*.mcpb'
MCPB_ARTIFACT_PATH = "dist/*.mcpb"

#: The job's steps, whole and in order, as `name` or `uses`. Pinning the *named* steps alone was
#: not enough: a second step named `Re-attach the bundle` carrying the same `gh release upload`
#: with `--clobber`, and a whole additional job doing the same, both left every pin green --
#: because those pins asked whether *the* attach step had been disarmed, never whether another
#: one had been added. `uses` stands in where a step has no name, so the two unnamed action steps
#: are told apart.
MCPB_STEPS = (
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "Install the build tooling",
    "actions/download-artifact@v4",
    MCPB_BUILD_STEP,
    "actions/upload-artifact@v4",
    MCPB_UPLOAD_STEP,
)


def release_jobs():
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    return workflow["jobs"]


def flat(value) -> str:
    """A scalar with its whitespace collapsed, so two spellings compare equal and only two."""
    return " ".join(str(value).split())


def shell_lines(step) -> "list[str]":
    """What a step's `run:` would execute: the comment and blank lines taken out."""
    return [
        line.rstrip()
        for line in str(step.get("run", "")).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def mcpb_step(name: str):
    """The one step of the `mcpb` job with this name, or a failure that says so."""
    steps = release_jobs()[MCPB_JOB].get("steps") or []
    found = [step for step in steps if step.get("name") == name]
    assert len(found) == 1, (
        f"`{MCPB_JOB}` has {len(found)} steps named {name!r}. If it was renamed, retarget this "
        "pin; if it was deleted, the job no longer does the thing the pin is about."
    )
    return found[0]


def test_the_mcpb_job_builds_on_every_trigger_and_attaches_only_on_a_tag():
    """Both halves of issue #35's wiring, pinned by *effect* rather than by presence.

    Eight ways to leave this job unable to do its work while every pin stayed green were
    demonstrated against the first version of this test, because it asserted that strings were
    present rather than that the workflow did one thing: an extra `&& github.event_name == ...`
    on the job condition, `--clobber` on the upload, `|| echo skipped`, a step-level
    `continue-on-error`, `exit 0` after the build, `if-no-files-found: ignore`, and an artifact
    glob retargeted to `*.mcpbz` (which "contains `*.mcpb`"). Each is a mutation of the file
    below, and each reddens one of the assertions here -- see the table in the pull request.
    """
    jobs = release_jobs()
    assert MCPB_JOB in jobs, "the mcpb job was renamed or removed"
    job = jobs[MCPB_JOB]

    # The step set itself, before any of its contents: an *added* step or an added job is the way
    # every content pin above is bypassed, and none of them can see it.
    steps = job.get("steps") or []
    assert tuple(step.get("name") or step.get("uses") for step in steps) == MCPB_STEPS, (
        f"`{MCPB_JOB}`'s steps are "
        f"{tuple(step.get('name') or step.get('uses') for step in steps)!r}, and this pin requires "
        f"exactly {MCPB_STEPS!r}. A step added here can attach the bundle without going through "
        "the step whose every argument is pinned above -- by name, that is `Re-attach the bundle` "
        "with the same `gh release upload` and a `--clobber` on it."
    )

    workflow_text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    workflow_code = "\n".join(
        line for line in workflow_text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--clobber" not in workflow_code, (
        "`release.yml` contains `--clobber` in something that is not a comment. The attach step "
        "forbids it -- a released asset must not be replaced, because two builds of this bundle "
        "are not byte-identical -- and a whole-file assertion is the only kind that sees a "
        "*second* place doing the uploading, whether that is a step or a job. A comment saying "
        "why not is not a place, which is why the comments are stripped first."
    )
    assert workflow_code.count("gh release upload") == 1, (
        f"`release.yml` attaches the bundle with `gh release upload` "
        f"{workflow_code.count('gh release upload')} times. There has to be exactly one place "
        "that does it: a second job after the release is created would bypass every pin that is "
        "about the step."
    )

    assert flat(job.get("if")) == MCPB_JOB_IF, (
        f"`{MCPB_JOB}` has if: {job.get('if')!r}, and this pin requires exactly {MCPB_JOB_IF!r}. "
        "`always()` is what makes the job run on a `workflow_dispatch` (a job whose `needs` were "
        "skipped is skipped with them); `needs.verify.result == 'success'` is what keeps a tag "
        "whose checks failed from getting a bundle. An `&&` on either end removes one of the two "
        "behaviours, and both removals are silent."
    )
    assert not job.get("continue-on-error"), (
        f"`{MCPB_JOB}` declares a job-level `continue-on-error`, so a bundle that cannot be "
        "built leaves the run green"
    )

    build = mcpb_step(MCPB_BUILD_STEP)
    assert shell_lines(build) == [MCPB_BUILD_COMMAND], (
        f"the build step runs {shell_lines(build)!r}, and this pin requires exactly "
        f"[{MCPB_BUILD_COMMAND!r}]. Every line matters: a trailing `; exit 0`, an `|| true`, or a "
        "second command after the packer lets the step report success for a bundle that was "
        "never built, and the packaging logic is only reachable through this invocation."
    )
    assert "if" not in build, (
        "the build step is conditional, so the bundle can be skipped while the job stays green"
    )
    assert not build.get("continue-on-error"), (
        "the build step ignores its own failure, so a bundle that was not built is uploaded"
    )

    artifacts = [
        step for step in job.get("steps") or []
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(artifacts) == 1, (
        f"`{MCPB_JOB}` has {len(artifacts)} `actions/upload-artifact` steps. On a dispatch "
        "nothing is attached to a release, so the workflow artifact is the only thing a reviewer "
        "can download; there has to be exactly one and it has to be this one."
    )
    artifact = artifacts[0]
    assert flat(artifact.get("with", {}).get("path")) == MCPB_ARTIFACT_PATH, (
        f"the artifact step uploads {artifact.get('with', {}).get('path')!r}, and this pin "
        f"requires exactly {MCPB_ARTIFACT_PATH!r}. A path that merely *contains* `*.mcpb` -- "
        "`*.mcpbz`, `*.mcpb.asc` -- uploads a different set of files while reading like the "
        "right one."
    )
    assert flat(artifact.get("with", {}).get("if-no-files-found", "")) != "ignore", (
        "the artifact step is set to ignore a missing bundle, so a build that produced nothing "
        "leaves the job green and the dispatch keeps nothing to verify"
    )
    assert "if" not in artifact, (
        "the artifact step is conditional, so the bundle is kept on one trigger and thrown away "
        "on the other"
    )
    assert not artifact.get("continue-on-error"), "the artifact step ignores its own failure"

    upload = mcpb_step(MCPB_UPLOAD_STEP)
    assert flat(upload.get("if")) == MCPB_UPLOAD_IF, (
        f"the release upload is guarded by {upload.get('if')!r}, and this pin requires exactly "
        f"{MCPB_UPLOAD_IF!r}. It has to be the tag push -- a dispatch has no release to attach "
        "to -- and it has to be a release that was actually created, or a failed "
        "`github-release` surfaces here as a secondary error that hides the first one."
    )
    assert not upload.get("continue-on-error"), (
        "the upload step ignores its own failure, so the release is left without its bundle on a "
        "green run"
    )
    assert shell_lines(upload) == [MCPB_UPLOAD_COMMAND], (
        f"the attach step runs {shell_lines(upload)!r}, and this pin requires exactly "
        f"[{MCPB_UPLOAD_COMMAND!r}]. `--clobber` would replace a released asset on a re-run, and "
        "`|| ...` would report success for an upload that did not happen."
    )
    for fragment in ("--clobber", "||", "2>/dev/null", "; true"):
        assert fragment not in str(upload.get("run", "")), (
            f"the attach step contains {fragment!r}. `--clobber` is forbidden by the comment "
            "above it -- the dependencies are resolved fresh on every run, so two builds are not "
            "byte-identical and a released asset must not be replaced -- and everything else "
            "swallows the failure."
        )


# ---------------------------------------------------------------------------------
# The bundle jobs in ci.yml -- the .mcpb is *run*, not only inspected (issue #37)
# ---------------------------------------------------------------------------------
#
# Every other statement about the bundle was a statement about its *shape*, and that is where
# the last blocking defect lived: the `cp310` tree was missing `exceptiongroup`, so a 3.10
# user's server died at startup while every static check passed, including one that compared
# the declared range with the vendored directory **names**. The jobs pinned here are the
# dynamic half -- one build, four legs on `windows-latest`, each unpacking the artifact and
# driving a handshake through the bundle's own entry script with the interpreter of its leg.
#
# The hazard is not a wrong verdict. It is a job that does not run, or a test that skips:
#   * a `paths:` filter on the workflow, or an `if:` on a job or the test step, leaves the run
#     green with nothing executed -- "a job that is skipped by a filter is not a gate", which
#     this repository has learned twice;
#   * the test skips when `PYJAB_MCP_BUNDLE` is unset (so the default suite is unaffected), so
#     a leg that stops setting it would report green while nothing ran. That is the lesson of
#     issue #30, and the reason the skip condition is pinned as behaviour and not as text.

CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

BUNDLE_BUILD_JOB = "mcpb-build"
BUNDLE_RUN_JOB = "mcpb-runs"
BUNDLE_ARTIFACT = "mcpb"
BUNDLE_BUILD_STEP = "Build the MCPB bundle"
BUNDLE_TEST_STEP = "Run the bundle's own entry script on this interpreter"

#: The two commands, whole. `; exit 0`, `|| true` and a second command after the packer are all
#: inside what is compared, which is what makes "the job ran it" mean "the job ran *this*".
BUNDLE_BUILD_COMMAND = "python tools/build_mcpb.py --dist dist --out dist"
BUNDLE_TEST_COMMAND = "python -m pytest tests/test_mcpb_bundle.py"

#: The variable the test reads, and the value the job sets on it. The two ends are tied
#: together here -- the key is `tests/test_mcpb_bundle.BUNDLE_ENV` itself, so renaming the
#: constant without the workflow (or the reverse) reddens this pin rather than silently
#: making every leg skip.
BUNDLE_ENV_VALUE = "${{ github.workspace }}/bundle/*.mcpb"

#: Both jobs' steps, whole and in order, as `name` or `uses` -- the same framing as the
#: release pipeline's `mcpb` job, and for the same reason: a pin that asks whether *the* step
#: was disarmed cannot see a *second* one added beside it.
BUNDLE_BUILD_STEPS = (
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "Install the build tooling",
    "Build the wheel",
    BUNDLE_BUILD_STEP,
    "actions/upload-artifact@v4",
)
BUNDLE_RUN_STEPS = (
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "Install the test process's own dependencies",
    "actions/download-artifact@v4",
    BUNDLE_TEST_STEP,
)

#: The minors the packer vendors a tree for. All four are needed: the defect was in one of
#: them, and a leg that is missing is exactly how the `cp310` gap stayed invisible.
BUNDLE_MINORS = ["3.10", "3.11", "3.12", "3.13"]

#: A bundle-shaped zip whose entry script writes this marker and exits, for the pin that has to
#: prove the test's *body* ran. Nothing about it speaks MCP, so the handshake fails -- which is
#: the point: the marker is what says the failure came from the stub entry point rather than
#: from something in the test above it.
STUB_MARKER = "STUB ENTRY POINT RAN"
STUB_ENTRY = (
    "import sys\n"
    f"sys.stderr.write({STUB_MARKER!r} + chr(10))\n"
    "raise SystemExit(3)\n"
)


def minimal_bundle(path: Path) -> Path:
    """A ``.mcpb`` with a manifest, a stub entry script, and a tree for *this* interpreter.

    Enough for ``tests/test_mcpb_bundle.py`` to get past its member assertions and spawn the
    script, which is all this fixture is for. The tree is empty: the stub never imports from it.
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"manifest_version": "0.4"}))
        archive.writestr(f"server/vendor/{bundle_test.interpreter_tag()}/", "")
        archive.writestr("server/entry.py", STUB_ENTRY)
    return path


def ci_workflow() -> dict:
    """`.github/workflows/ci.yml`, parsed."""
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


def ci_triggers(workflow: dict) -> dict:
    """The workflow's ``on:`` block, however PyYAML spelled the key.

    YAML 1.1 resolves the bare word ``on`` to a boolean, so ``safe_load`` gives this block the
    key ``True`` and a pin that only looked for the string ``"on"`` would find nothing and pass
    vacuously -- which is the shape of pin this repository has already been burned by.
    """
    assert "on" in workflow or True in workflow, (
        f"ci.yml has no `on:` block at all: {sorted(map(str, workflow))!r}"
    )
    block = workflow.get("on", workflow.get(True))
    assert isinstance(block, dict), f"ci.yml's `on:` block is not a mapping: {block!r}"
    return block


def ci_steps(job: dict) -> "list[dict]":
    """A job's steps, as the workflow declares them."""
    return list(job.get("steps") or [])


def ci_step(job: dict, name: str) -> dict:
    """The one step of *job* with this name, or a failure that says so."""
    found = [step for step in ci_steps(job) if step.get("name") == name]
    assert len(found) == 1, (
        f"the job has {len(found)} steps named {name!r}. If it was renamed, retarget this pin; "
        "if it was deleted, the job no longer does the thing the pin is about."
    )
    return found[0]


def ci_step_names(job: dict) -> "tuple":
    """Each step as its `name`, or its `uses` where it has none."""
    return tuple(step.get("name") or step.get("uses") for step in ci_steps(job))


def test_the_workflow_has_no_path_filter_that_could_leave_the_bundle_jobs_unrun():
    """A job skipped by a filter is not a gate, and this repository has forgotten that twice."""
    for trigger, config in ci_triggers(ci_workflow()).items():
        config = config if isinstance(config, dict) else {}
        for key in ("paths", "paths-ignore"):
            assert key not in config, (
                f"ci.yml's `{trigger}` trigger carries `{key}`. A path filter skips whole jobs "
                "-- including the bundle jobs -- and a skipped job leaves the run green without "
                "executing anything, which is exactly what these jobs exist to prevent."
            )


def test_the_bundle_builder_job_runs_the_packer_and_uploads_the_artifact():
    """Issue #37's first half: one bundle, built from the wheel and kept for the four legs."""
    jobs = ci_workflow().get("jobs") or {}
    assert BUNDLE_BUILD_JOB in jobs, (
        f"ci.yml has no `{BUNDLE_BUILD_JOB}` job: nothing builds the .mcpb in default CI, so "
        "the four legs have nothing to run."
    )
    job = jobs[BUNDLE_BUILD_JOB]

    assert ci_step_names(job) == BUNDLE_BUILD_STEPS, (
        f"`{BUNDLE_BUILD_JOB}`'s steps are {ci_step_names(job)!r}, and this pin requires "
        f"exactly {BUNDLE_BUILD_STEPS!r}. A step added here can build a different bundle, or "
        "upload a different file, without touching the step this pin is about."
    )
    assert "if" not in job, (
        f"`{BUNDLE_BUILD_JOB}` is conditional, so the bundle can be skipped on some event and "
        "the four legs then run against an artifact from an earlier run, or none at all"
    )
    assert not job.get("continue-on-error"), (
        f"`{BUNDLE_BUILD_JOB}` ignores its own failure, so a bundle that could not be built "
        "leaves the run green"
    )

    build = ci_step(job, BUNDLE_BUILD_STEP)
    assert shell_lines(build) == [BUNDLE_BUILD_COMMAND], (
        f"the build step runs {shell_lines(build)!r}, and this pin requires exactly "
        f"[{BUNDLE_BUILD_COMMAND!r}]. A trailing `; exit 0`, an `|| true`, or a second command "
        "after the packer reports success for a bundle that was never built."
    )
    assert "if" not in build, (
        "the build step is conditional, so the artifact the four legs run can be stale"
    )
    assert not build.get("continue-on-error"), (
        "the build step ignores its own failure, so a bundle that was not built is uploaded"
    )

    artifacts = [
        step for step in ci_steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(artifacts) == 1, (
        f"`{BUNDLE_BUILD_JOB}` has {len(artifacts)} `actions/upload-artifact` steps; there has "
        "to be exactly one, and it has to be the one the four legs download."
    )
    upload = artifacts[0]
    assert flat(upload.get("with", {}).get("name")) == BUNDLE_ARTIFACT, (
        f"the builder uploads an artifact named {upload.get('with', {}).get('name')!r}, and the "
        f"runner job downloads {BUNDLE_ARTIFACT!r}"
    )
    assert flat(upload.get("with", {}).get("path")) == "dist/*.mcpb", (
        f"the artifact step uploads {upload.get('with', {}).get('path')!r}, and this pin "
        "requires exactly 'dist/*.mcpb'. A path that merely *contains* `*.mcpb` -- `*.mcpbz`, "
        "`*.mcpb.asc` -- keeps a different set of files while reading like the right one."
    )
    assert flat(upload.get("with", {}).get("if-no-files-found", "")) == "error", (
        "the artifact step does not fail when the build produced nothing, so the builder can "
        "be green with four legs about to fail over a missing file"
    )
    assert "if" not in upload and not upload.get("continue-on-error"), (
        "the artifact step is conditional or ignores its failure, so the legs can get no bundle"
    )


def test_the_bundle_runner_job_executes_it_on_every_supported_minor():
    """Issue #37's second half: unpack and *run* it, per minor, on Windows."""
    jobs = ci_workflow().get("jobs") or {}
    assert BUNDLE_RUN_JOB in jobs, (
        f"ci.yml has no `{BUNDLE_RUN_JOB}` job, so nothing in default CI ever executes a .mcpb"
    )
    build_job = jobs.get(BUNDLE_BUILD_JOB) or {}
    job = jobs[BUNDLE_RUN_JOB]

    assert BUNDLE_BUILD_JOB in (job.get("needs") or []), (
        f"`{BUNDLE_RUN_JOB}` does not need `{BUNDLE_BUILD_JOB}`, so it can start before the "
        "artifact it downloads exists"
    )
    assert flat(job.get("runs-on")) == "windows-latest", (
        f"`{BUNDLE_RUN_JOB}` runs on {job.get('runs-on')!r}. The vendored trees are win_amd64 "
        "wheels and the manifest declares win32, so a leg on another platform measures a "
        "bundle nobody ships."
    )
    assert "if" not in job, (
        f"`{BUNDLE_RUN_JOB}` is conditional: a job that a filter or a condition can skip is not "
        "a gate on a merge"
    )
    assert not job.get("continue-on-error"), (
        f"`{BUNDLE_RUN_JOB}` ignores its own failure, so a bundle that cannot start leaves the "
        "run green"
    )

    matrix = ((job.get("strategy") or {}).get("matrix")) or {}
    assert list(matrix.get("python-version") or []) == BUNDLE_MINORS, (
        f"`{BUNDLE_RUN_JOB}` is a matrix over {matrix.get('python-version')!r}, and this pin "
        f"requires {BUNDLE_MINORS!r}. One minor is what the bundle vendors per tree, and the "
        "defect this job exists for was in exactly one of them (`cp310`)."
    )
    assert "exclude" not in matrix, (
        "the matrix excludes a leg. Excluding one drops a minor from the run while the declared "
        "list still reads as four -- `cp310` is the one that would be dropped, and the tree "
        "that was broken."
    )

    assert ci_step_names(job) == BUNDLE_RUN_STEPS, (
        f"`{BUNDLE_RUN_JOB}`'s steps are {ci_step_names(job)!r}, and this pin requires exactly "
        f"{BUNDLE_RUN_STEPS!r}."
    )

    downloads = [
        step for step in ci_steps(job)
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]
    assert len(downloads) == 1, (
        f"`{BUNDLE_RUN_JOB}` has {len(downloads)} download steps; there has to be exactly one"
    )
    download = downloads[0]
    assert flat(download.get("with", {}).get("name")) == BUNDLE_ARTIFACT, (
        f"the runner downloads {download.get('with', {}).get('name')!r}, not "
        f"{BUNDLE_ARTIFACT!r} -- the artifact the builder uploads"
    )
    assert str(download.get("with", {}).get("path", "")).strip(), (
        "the download step names no path, so where the bundle lands is whatever the action "
        "defaults to and the variable below points somewhere else"
    )

    test_step = ci_step(job, BUNDLE_TEST_STEP)
    assert shell_lines(test_step) == [BUNDLE_TEST_COMMAND], (
        f"the test step runs {shell_lines(test_step)!r}, and this pin requires exactly "
        f"[{BUNDLE_TEST_COMMAND!r}]. This is the step that does the work: `|| true`, `; exit 0`, "
        "a `--ignore` or a second pytest invocation after it all report success for a handshake "
        "that never happened."
    )
    assert "if" not in test_step, (
        "the test step is conditional, which is how a leg reports green without running the "
        "bundle"
    )
    assert not test_step.get("continue-on-error"), "the test step ignores its own failure"
    for fragment in ("||", "; true", "2>/dev/null", "--ignore", "continue-on-error"):
        assert fragment not in str(test_step.get("run", "")), (
            f"the test step contains {fragment!r}, which swallows or skips the failure"
        )

    env = test_step.get("env") or {}
    assert list(env) == [bundle_test.BUNDLE_ENV], (
        f"the test step sets {sorted(env)!r} and the test reads "
        f"{bundle_test.BUNDLE_ENV!r} and only it. This is the tie between the two ends: without "
        "it a renamed variable leaves every leg *skipped* and the job green."
    )
    assert flat(env[bundle_test.BUNDLE_ENV]) == BUNDLE_ENV_VALUE, (
        f"the test step sets {bundle_test.BUNDLE_ENV} to {env[bundle_test.BUNDLE_ENV]!r}, and "
        f"this pin requires exactly {BUNDLE_ENV_VALUE!r}. The filename carries the version, so "
        "the job cannot name the file -- but an empty value, or a path that matches nothing, "
        "must not be allowed to pass."
    )

    # The two jobs are one mechanism, so the artifact names are one fact.
    builder_uploads = [
        step for step in ci_steps(build_job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    if builder_uploads:
        assert flat(builder_uploads[0].get("with", {}).get("name")) == flat(
            download.get("with", {}).get("name")
        ), "the builder uploads an artifact the runner does not download, or the reverse"


def test_the_bundle_test_skips_only_without_the_variable_and_fails_on_a_broken_one(monkeypatch,
                                                                                  tmp_path):
    """The skip is a decision about one variable, and it is pinned as *behaviour*.

    A mutation of `tests/test_mcpb_bundle.py` that turns `pytest.fail` into `pytest.skip` -- or
    that adds a `pytestmark` skipping the module -- leaves every leg green while no bundle is
    ever run. So the outcome *kind* is asserted, not the presence of a string: the pin catches
    whatever is raised and then says which kind it has to be.
    """
    # Unset: skipped, and the reason names the variable the job sets.
    monkeypatch.delenv(bundle_test.BUNDLE_ENV, raising=False)
    with pytest.raises(BaseException) as unset:  # noqa: PT011 - the kind is asserted below
        bundle_test.bundle_path()
    assert isinstance(unset.value, pytest.skip.Exception), (
        f"with {bundle_test.BUNDLE_ENV} unset, bundle_path() raised "
        f"{type(unset.value).__name__} ({unset.value}) instead of skipping. The default suite "
        "on a machine with no bundle has to be unaffected, and the skip is the only outcome "
        "allowed to be silent."
    )
    assert bundle_test.BUNDLE_ENV in str(unset.value), (
        "the skip reason does not name the variable, so a reader of a green run cannot tell "
        "why nothing ran"
    )

    # Set and broken: a *failure*. A skip here is the #30 hole -- a green job over nothing.
    monkeypatch.setenv(bundle_test.BUNDLE_ENV, str(tmp_path / "absent.mcpb"))
    with pytest.raises(BaseException) as broken:  # noqa: PT011 - the kind is asserted below
        bundle_test.bundle_path()
    assert isinstance(broken.value, pytest.fail.Exception), (
        f"with {bundle_test.BUNDLE_ENV} set to a path that does not exist, bundle_path() "
        f"raised {type(broken.value).__name__} ({broken.value}). It has to *fail*: a leg that "
        "sets the variable and skips reports green while nothing was executed."
    )
    assert bundle_test.BUNDLE_ENV in str(broken.value)


def test_the_bundle_test_file_cannot_be_left_skipped_by_anything_else(tmp_path):
    """Run the file the job runs, and read *why* it stopped -- three ways, not two.

    The pins above call one helper, and a helper can be bypassed above it: a
    `pytestmark = pytest.mark.skip(...)`, or a `skipif` decorator, leaves both of them passing
    and the job green with no bundle executed. Only running the file sees that, and the reason
    it prints is what distinguishes "no bundle here" from "somebody switched it off".

    The third case exists because the first version of this pin had *two* and could not tell
    two different worlds apart: `assert "1 failed" in stdout` is satisfied both by "the variable
    is set and the bundle is absent" and by "the test itself is broken". That was measured the
    expensive way -- the test called the sync tool-surface authority from inside its own running
    event loop, all four `mcpb-runs` legs failed with
    `RuntimeError: asyncio.run() cannot be called from a running event loop`, and this pin and
    the whole suite were green. So the reasons are asserted, and a *real* minimal bundle is
    handed over so the test's body -- unpack, members, the authority, the spawn -- runs here
    rather than only on four Windows runners.
    """
    test_file = "tests/test_mcpb_bundle.py"
    environment = dict(os.environ)
    environment.pop(bundle_test.BUNDLE_ENV, None)

    def run():
        return subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider"],
            cwd=REPO_ROOT, capture_output=True, text=True, env=environment, timeout=600,
        )

    skipped = run()
    assert skipped.returncode == 0, (
        f"with {bundle_test.BUNDLE_ENV} unset the file has to skip, and it exited "
        f"{skipped.returncode}:\n{skipped.stdout[-2000:]}\n{skipped.stderr[-2000:]}"
    )
    assert "1 skipped" in skipped.stdout and "1 passed" not in skipped.stdout, (
        f"expected exactly one skipped test, and got:\n{skipped.stdout[-2000:]}"
    )
    assert f"{bundle_test.BUNDLE_ENV} is not set" in skipped.stdout, (
        "the skip reason is not the variable being unset. Every other reason -- a marker, a "
        "skipif, a collection error turned into a skip -- is a run that proves nothing while "
        "reporting green.\n" + skipped.stdout[-2000:]
    )

    # Set, pointing nowhere: a failure, and the failure has to be *this* one. The reason is
    # what separates it from a test that broke before it ever looked at the variable.
    environment[bundle_test.BUNDLE_ENV] = str(REPO_ROOT / "no-such-bundle.mcpb")
    absent = run()
    output = absent.stdout + absent.stderr
    assert absent.returncode != 0, (
        f"with {bundle_test.BUNDLE_ENV} set to a file that is not there, the file exited 0 -- "
        "the same hole, one layer down:\n" + absent.stdout[-2000:]
    )
    assert "1 failed" in absent.stdout, (
        f"expected one failure rather than a skip or an error:\n{absent.stdout[-2000:]}"
    )
    assert "matches 0 file(s)" in output, (
        "the failure is not the bundle being absent: the message `bundle_path()` writes for "
        "that case is missing, so something else failed first and this pin cannot tell the two "
        "apart.\n" + output[-2000:]
    )

    # Set, pointing at a real (minimal) bundle: the test's *body* runs. Its entry script writes
    # a marker and exits without speaking MCP, so the run still fails -- and the marker is what
    # proves the body got as far as spawning it.
    environment[bundle_test.BUNDLE_ENV] = str(minimal_bundle(tmp_path / "stub.mcpb"))
    spawned = run()
    output = spawned.stdout + spawned.stderr
    assert spawned.returncode != 0, (
        "a bundle whose entry point cannot start has to fail the test, and it exited "
        f"0:\n{spawned.stdout[-2000:]}"
    )
    assert STUB_MARKER in output, (
        "the stub entry point's marker is not in the output, so the test never spawned the "
        "bundle's own server: the body failed before it got there. That is exactly how the "
        "first version of `tests/test_mcpb_bundle.py` passed this pin while every CI leg was "
        f"red.\n{output[-2000:]}"
    )


# ---------------------------------------------------------------------------------
# tools/check_changelog_immutable.py
# ---------------------------------------------------------------------------------

CHANGELOG_FIXTURE = """# Changelog

## Unreleased

- in flight, and not a released section

## [0.2.0] - 2026-11-01

### Added

- the new thing

## [0.1.0] - 2026-10-10

### Added

- the old thing
"""


def immutable_tree(tmp_path, monkeypatch, record: dict, changelog: str = CHANGELOG_FIXTURE):
    """A repository whose changelog and record are the ones a test wants."""
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    record_path = tmp_path / "released_changelog_sections.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(immutable_guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(immutable_guard, "CHANGELOG", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(immutable_guard, "RECORD", record_path)
    return record_path


def test_unreleased_is_not_a_released_section():
    """The place new entries belong must never be mistaken for one that shipped."""
    found = immutable_guard.sections(CHANGELOG_FIXTURE)

    assert sorted(found) == ["0.1.0", "0.2.0"]
    assert "in flight" not in found["0.2.0"], "a section ends at the next level-two heading"
    assert "the new thing" in found["0.2.0"]
    assert "the old thing" not in found["0.2.0"]


def test_a_recorded_section_that_changed_fails(tmp_path, monkeypatch):
    """The pyjab 1.6.3 incident: the repository claimed what the sdist did not contain."""
    immutable_tree(tmp_path, monkeypatch, {"0.1.0": "sha256:0000"})

    assert immutable_guard.check() == 1


def test_a_recorded_section_that_vanished_fails(tmp_path, monkeypatch):
    immutable_tree(tmp_path, monkeypatch, {"9.9.9": "sha256:0000"})

    assert immutable_guard.check() == 1


def test_an_unchanged_record_passes(tmp_path, monkeypatch):
    section = immutable_guard.sections(CHANGELOG_FIXTURE)["0.1.0"]
    immutable_tree(tmp_path, monkeypatch, {"0.1.0": immutable_guard.digest(section)})

    assert immutable_guard.check() == 0


def test_recording_refuses_a_version_with_no_tag(tmp_path, monkeypatch):
    immutable_tree(tmp_path, monkeypatch, {})

    with pytest.raises(SystemExit) as caught:
        immutable_guard.record("0.1.0")

    assert "no tag v0.1.0" in str(caught.value)


def test_recording_refuses_to_replace_a_different_hash(tmp_path, monkeypatch):
    """A released section is not supposed to change, so --record is not a way to bless it."""
    section = immutable_guard.sections(CHANGELOG_FIXTURE)["0.1.0"]
    record_path = immutable_tree(
        tmp_path, monkeypatch, {"0.1.0": immutable_guard.digest(section)}
    )
    monkeypatch.setattr(
        immutable_guard, "tag_section", lambda version: section + "\n- and a lie"
    )

    with pytest.raises(SystemExit) as caught:
        immutable_guard.record("0.1.0")

    assert "refusing to replace" in str(caught.value)
    assert json.loads(record_path.read_text())["0.1.0"] == immutable_guard.digest(section)


def test_recording_freezes_what_the_tag_says(tmp_path, monkeypatch):
    section = immutable_guard.sections(CHANGELOG_FIXTURE)["0.1.0"]
    record_path = immutable_tree(tmp_path, monkeypatch, {})
    monkeypatch.setattr(immutable_guard, "tag_section", lambda version: section)

    assert immutable_guard.record("0.1.0") == 0

    assert json.loads(record_path.read_text()) == {"0.1.0": immutable_guard.digest(section)}
    assert immutable_guard.check() == 0


def test_recording_refuses_an_empty_version(tmp_path, monkeypatch, capsys):
    """Issue #65: `--record ""` is falsy, so the guard ran the plain check and exited 0.

    That is the opposite operation and a green one: the release path asked to freeze a
    section and was told the sections were unchanged.
    """
    section = immutable_guard.sections(CHANGELOG_FIXTURE)["0.1.0"]
    immutable_tree(tmp_path, monkeypatch, {"0.1.0": immutable_guard.digest(section)})

    assert immutable_guard.main(["--record", ""]) == 1, (
        "an empty version names no release, so it cannot fall through to check()"
    )

    out = capsys.readouterr().out
    assert "empty string" in out, "the refusal has to name the input it could not honour"
    assert "PASSED" not in out


def test_a_record_that_cannot_be_read_refuses_rather_than_passing(tmp_path, monkeypatch):
    """Issue #65: a missing record file printed "recorded: 0" and then PASSED, exit 0.

    "No released section is recorded" and "the record could not be read" are different
    states, and only one of them is a pass (AGENTS.md 2.4). The refusal is a ``SystemExit``
    rather than a returned 1 because ``load_record`` is also what ``--record`` reads, where
    an unreadable record would silently drop every section already frozen.
    """
    immutable_tree(tmp_path, monkeypatch, {})
    missing = tmp_path / "no-such-record.json"
    monkeypatch.setattr(immutable_guard, "RECORD", missing)

    with pytest.raises(SystemExit) as caught:
        immutable_guard.check()

    assert "FAILED" in str(caught.value)
    assert missing.name in str(caught.value), (
        "the refusal has to name the file it could not read"
    )


# ---------------------------------------------------------------------------------
# tools/check_published_references.py
# ---------------------------------------------------------------------------------


def reference_tree(tmp_path, monkeypatch, files: "dict[str, str]", record: "dict | None" = None):
    """A repository whose shipped files are the ones a test wants."""
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "released_changelog_sections.json").write_text(
        json.dumps(record or {}), encoding="utf-8"
    )
    monkeypatch.setattr(references_guard, "REPO_ROOT", tmp_path)
    return tmp_path


def test_a_shipped_file_citing_a_maintainer_note_is_a_problem(tmp_path, monkeypatch):
    """The README bug this guard exists for: it presented files a visitor cannot open."""
    root = reference_tree(tmp_path, monkeypatch, {
        "README.md": "| `AGENTS.md` | the working agreement |\n",
    })

    problems = references_guard.check(root)

    assert problems, "a shipped file must not send a reader to a file they cannot open"
    assert "README.md" in problems[0] and "AGENTS.md" in problems[0]


def test_a_shipped_file_that_states_the_rule_is_fine(tmp_path, monkeypatch):
    root = reference_tree(tmp_path, monkeypatch, {
        "README.md": "Every guard prints a report and exits non-zero.\n",
        "src/pyjab_mcp/thing.py": "# a measurement that could not be taken is not a zero\n",
    })

    assert references_guard.check(root) == []


def test_the_exclusion_mechanisms_and_their_tests_may_name_the_paths(tmp_path, monkeypatch):
    """`.gitignore` exists to name them, and so does the test that proves the exclusion works."""
    root = reference_tree(tmp_path, monkeypatch, {
        ".gitignore": "AGENTS.md\n",
        "MANIFEST.in": "exclude docs/DESIGN.md\n",
        "pyproject.toml": "# prunes docs/PLAN.md from the sdist\n",
        "tools/check_local_only_files.py": "LOCAL_ONLY = ('AGENTS.md',)\n",
        "tests/test_guards.py": 'text = "AGENTS.md"\n',
    })

    assert references_guard.check(root) == []


def test_the_maintainer_notes_themselves_are_not_shipped_so_not_checked(tmp_path, monkeypatch):
    root = reference_tree(tmp_path, monkeypatch, {
        "AGENTS.md": "see docs/DESIGN.md and .agents/skills/x/SKILL.md\n",
        "docs/PLAN.md": "see AGENTS.md\n",
        ".agents/skills/x/SKILL.md": "see AGENTS.md\n",
    })

    assert references_guard.check(root) == []


def test_a_frozen_changelog_section_is_not_checked_because_it_cannot_change(
    tmp_path, monkeypatch
):
    """Its hash is pinned by the other guard, so asking for a change would be a contradiction."""
    changelog = (
        "# Changelog\n\n## Unreleased\n\nnothing yet\n\n"
        "## [0.1.0] - 2026-10-10\n\n- see AGENTS.md 6 for the audit\n"
    )
    import hashlib

    section = references_guard.changelog_sections(changelog)["0.1.0"]
    digest = "sha256:" + hashlib.sha256(section.encode("utf-8")).hexdigest()
    root = reference_tree(tmp_path, monkeypatch, {"CHANGELOG.md": changelog},
                          record={"0.1.0": digest})

    assert references_guard.check(root) == []


def test_an_unreleased_changelog_section_is_checked(tmp_path, monkeypatch):
    """The escape hatch is the record, not the top of the file."""
    root = reference_tree(tmp_path, monkeypatch, {
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- see AGENTS.md 6\n",
    })

    problems = references_guard.check(root)

    assert problems and "CHANGELOG.md" in problems[0]


def test_a_shipped_file_citing_a_local_only_file_by_relative_name_is_a_problem(
    tmp_path, monkeypatch
):
    """The #38 shape: a relative path with no `.agents/` prefix, which name matching misses.

    This is the case `tools/build_mcpb.py` went through -- it cited `references/manifest-schema.md`
    while that file existed only under `.agents/skills/build-mcpb/`, and the guard stayed green.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": '"""See references/manifest-schema.md for the fields."""\n',
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    problems = references_guard.check(root)

    assert problems, "a relative citation of a local-only file is the same failure"
    assert "tools/build_mcpb.py" in problems[0]
    assert "references/manifest-schema.md" in problems[0]


def test_a_relative_name_is_fine_when_a_shipped_file_of_that_name_exists(
    tmp_path, monkeypatch
):
    """The false-positive guard, and the case that decides whether this rule is usable.

    A reader who can open the file has not been sent to nothing. An implementation that
    reports the citation anyway would complain about every path that happens to have a copy
    under `.agents/`, and a guard that cries wolf gets switched off.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": "See references/manifest-schema.md for the fields.\n",
        "references/manifest-schema.md": "# the shipped copy\n",
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    assert references_guard.check(root) == []


def test_a_relative_name_that_resolves_to_a_shipped_file_is_fine(tmp_path, monkeypatch):
    """A citation of a file the reader can open, anywhere in the tree, is not a complaint."""
    root = reference_tree(tmp_path, monkeypatch, {
        "README.md": "Start with src/pyjab_mcp/server.py, then docs/CLIENTS.md.\n",
        "src/pyjab_mcp/server.py": "# the server\n",
        "docs/CLIENTS.md": "# the clients\n",
    })

    assert references_guard.check(root) == []


def test_a_bare_filename_does_not_name_a_location_so_it_is_not_flagged(
    tmp_path, monkeypatch
):
    """`manifest-schema.md` alone could be anywhere, including beside the reader.

    Resolution needs a directory component; without one there is nothing to resolve. This is
    deliberate, not an oversight: flagging every filename that also exists under `.agents/`
    would fire on ordinary prose.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": '"""The fields the manifest accepts are in manifest-schema.md."""\n',
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    assert references_guard.check(root) == []


def test_a_citation_inside_a_code_fence_is_still_read_by_a_reader(
    tmp_path, monkeypatch
):
    """A fence is not an exemption: whoever copies the path out of it still cannot open it."""
    root = reference_tree(tmp_path, monkeypatch, {
        "docs/CLIENTS.md": "```\nreferences/manifest-schema.md\n```\n",
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    problems = references_guard.check(root)

    assert problems and "references/manifest-schema.md" in problems[0]


def test_a_leading_parent_does_not_hide_the_citation(tmp_path, monkeypatch):
    """`../references/x.md` is the same citation with a prefix, so the prefix is dropped."""
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": '"""See ../references/manifest-schema.md."""\n',
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    problems = references_guard.check(root)

    assert problems and "references/manifest-schema.md" in problems[0]


def test_no_shipped_file_in_this_repository_cites_a_local_only_path():
    """The rule on this tree, where the maintainer notes exist to be resolved against.

    In CI the local-only files are absent (they are gitignored), so this passes by vacuity
    there; on a machine that has them it is the check that a new rule has no false positives.
    """
    assert references_guard.check(REPO_ROOT) == []


def test_a_citation_written_with_windows_separators_is_a_problem(tmp_path, monkeypatch):
    """#49: the same citation as a Windows reader sees it, which the token regex used to miss.

    A backslash escapes only in some Markdown; in the `.py` docstrings, `.toml` and `.in` files
    this guard also reads, `references\\manifest-schema.md` is a Windows path and nothing
    escapes it. A reader there can no more open the local-only file than one reading the
    forward-slash form, so the separators are normalised before the citation is resolved.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": '"""See references\\manifest-schema.md for the fields."""\n',
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    problems = references_guard.check(root)

    assert problems, "the Windows spelling is the same citation to a reader there"
    assert "tools/build_mcpb.py" in problems[0]
    assert "references/manifest-schema.md" in problems[0]


def test_a_windows_separated_citation_is_fine_when_a_shipped_file_of_that_name_exists(
    tmp_path, monkeypatch
):
    """The `every match is local-only` clause, for the spelling the fix added.

    Normalising the separators widens what the guard can see, and widening what a guard sees is
    how a guard acquires false positives: the clause that keeps the forward-slash form silent
    has to hold for the backslash form too.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": "See references\\manifest-schema.md for the fields.\n",
        "references/manifest-schema.md": "# the shipped copy\n",
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    assert references_guard.check(root) == []


@pytest.mark.parametrize(
    "spelling",
    [
        "See references/manifest schema.md for the fields.",
        "See references/manifest-sch\u00e9ma.md for the fields.",
        "See /tmp/x/references/manifest-schema.md for the fields.",
        "See C:\\x\\references\\manifest-schema.md for the fields.",
        "See references/\nmanifest-schema.md for the fields.",
    ],
    ids=["space", "non-ascii", "absolute", "drive-letter", "split-lines"],
)
def test_a_name_that_is_not_a_citation_of_this_tree_stays_silent(
    tmp_path, monkeypatch, spelling
):
    """The negative rows of #49, pinned because the fix widens the token regex.

    A filename with a space, a non-ASCII name, an absolute path, a drive letter and a path split
    across two lines each fail to name a location in this tree, so each resolves to nothing and
    the guard says nothing. Widening the separators must not turn a lookup that found nothing
    into a complaint, which is a false positive a maintainer would have to work around.
    """
    root = reference_tree(tmp_path, monkeypatch, {
        "tools/build_mcpb.py": spelling + "\n",
        ".agents/skills/build-mcpb/references/manifest-schema.md": "# the local copy\n",
    })

    assert references_guard.check(root) == []

GUARDS_PAGE = MINIMAL_ARCHITECTURE + """
## Guards

| guard | the rule it makes mechanical |
|---|---|
| `check_a.py` | a rule |
"""


def test_the_documented_surface_guard_catches_a_guard_missing_from_the_table(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(GUARDS_PAGE, encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=["check_a.py", "check_b.py"], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_snapshot", "java_screenshot", "java_click"],
    )

    assert any("UNDOCUMENTED GUARD: tools/check_b.py" in problem for problem in problems)


def test_the_documented_surface_guard_catches_a_guard_row_that_has_no_script(tmp_path):
    page = tmp_path / "ARCHITECTURE.md"
    page.write_text(
        MINIMAL_ARCHITECTURE + """
## Guards

| guard | the rule it makes mechanical |
|---|---|
| `check_a.py` | a rule |
| `check_gone.py` | a rule whose script was deleted |
""", encoding="utf-8")

    problems = docs_guard.check(
        architecture=page, guards=["check_a.py"], package=REPO_ROOT / "src" / "pyjab_mcp",
        registered=["java_diagnostics", "java_list_windows", "java_attach_window",
                    "java_snapshot", "java_screenshot", "java_click"],
    )

    assert any("STALE DOC: the guards table names tools/check_gone.py" in problem
               for problem in problems)
