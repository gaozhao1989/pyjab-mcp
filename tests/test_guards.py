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
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

import tools.check_dist_contents as dist_guard
import tools.check_pyjab_changelog as changelog_guard
import tools.check_documented_surface as docs_guard
import tools.check_local_only_files as local_guard
import tools.check_pyjab_api_surface as surface_guard
import tools.check_published_references as references_guard
import tools.check_pyjab_readiness as readiness_guard
import tools.check_changelog_immutable as immutable_guard
import tools.check_release_l3 as l3_guard
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
                    "java_click"],
    )

    assert any("NOT YET: java_click" in problem for problem in problems)


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


def test_readiness_is_resolved_by_the_pin_and_measured_otherwise():
    """Three answers, and the difference matters: resolved by the pin, ready by probe, or not.

    ``list_windows``, ``detach`` and ``walk`` are pin questions now -- pyjab released them,
    so what guarantees them is the floor in pyproject.toml rather than a probe on whatever
    machine happens to be running. ``send_keys`` is still a release question.
    """
    verdicts = readiness_guard.readiness(_report("available"))

    assert verdicts["list_windows"][0] == readiness_guard.RESOLVED
    assert verdicts["walk"][0] == readiness_guard.RESOLVED
    assert verdicts["send_keys"][0] == readiness_guard.READY

    old = readiness_guard.readiness(_report("missing", version="1.9.0"))
    assert old["list_windows"][0] == readiness_guard.NOT_READY, (
        "an install below the pin must not be reported as having the release"
    )
    assert old["walk"][0] == readiness_guard.RESOLVED, (
        "1.9.0 does have the walk, and the pin's floor is what says so"
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
    was driven -- which is the risk #30 is about. The only exemption is the log upload, whose
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
        "nothing in windows-gui.yml may be skippable or non-fatal except the log upload, whose "
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
