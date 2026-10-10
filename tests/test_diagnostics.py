"""L1 tests for the environment report.

The rule under test is this: a measurement that could not be taken
is not a zero and not a "missing".  Each of these pins one of the three answers apart from
the other two, because collapsing them is how a report starts flattering whoever wrote it.
"""

from __future__ import annotations

import pytest

from pyjab_mcp.backend import PyjabApiReport, PyjabUnavailable, Fact, AVAILABLE, UNKNOWN
from pyjab_mcp.bridge import BridgeStartupError, BridgeThread
from pyjab_mcp.diagnostics import (
    Status,
    check_accessibility_properties,
    check_bridge_dll,
    check_bridge_thread,
    collect,
)
from tests._fakebackend import FakeFactory

EXPECTED = "assistive_technologies=com.sun.java.accessibility.AccessBridge\n"


# -- the properties file ------------------------------------------------------------


def test_an_absent_properties_file_is_missing_not_unavailable(tmp_path):
    row = check_accessibility_properties(tmp_path / "nope.properties", EXPECTED)

    assert row.status == Status.MISSING
    assert "not enabled" in row.value
    assert "does not exist" in row.detail


def test_a_matching_properties_file_is_ok(tmp_path):
    path = tmp_path / "accessibility.properties"
    path.write_text(EXPECTED, encoding="utf-8")

    row = check_accessibility_properties(path, EXPECTED)

    assert row.status == Status.OK
    assert row.value == "enabled"


def test_a_different_properties_file_is_its_own_answer(tmp_path):
    """Present, readable, and not what JAB needs -- which is neither missing nor ok."""
    path = tmp_path / "accessibility.properties"
    path.write_text("assistive_technologies=com.example.Other\n", encoding="utf-8")

    row = check_accessibility_properties(path, EXPECTED)

    assert row.status == Status.DIFFERENT
    assert "does not enable JAB" in row.value
    assert "does not rewrite it" in row.detail


def test_an_unreadable_properties_file_is_unavailable_not_missing(tmp_path):
    """A directory is the portable stand-in for "the read failed"."""
    row = check_accessibility_properties(tmp_path, EXPECTED)

    assert row.status == Status.UNAVAILABLE
    assert row.value.startswith("UNAVAILABLE (")


def test_undecodable_properties_bytes_are_unavailable_not_missing(tmp_path):
    path = tmp_path / "accessibility.properties"
    path.write_bytes(b"\xff\xfe\x00\x01")

    row = check_accessibility_properties(path, EXPECTED)

    assert row.status == Status.UNAVAILABLE
    assert "UnicodeDecodeError" in row.value


# -- the bridge DLL -----------------------------------------------------------------


def test_a_found_dll_is_reported_with_its_path():
    row = check_bridge_dll(
        dll_bit=64,
        find=lambda bit: f"C:/jdk/bin/WindowsAccessBridge-{bit}.dll",
        describe=lambda bit: "probed 3 locations",
    )

    assert row.status == Status.OK
    assert row.value.endswith("WindowsAccessBridge-64.dll")
    assert row.detail == "probed 3 locations"


def test_a_dll_that_is_not_there_is_missing_with_the_search_description():
    row = check_bridge_dll(dll_bit=64, find=lambda bit: None, describe=lambda bit: "probed 3")

    assert row.status == Status.MISSING
    assert row.value == "not found"
    assert row.detail == "probed 3"


def test_a_search_that_raises_is_unavailable_not_missing():
    def explode(bit):
        raise OSError("the recursive search was refused")

    row = check_bridge_dll(dll_bit=32, find=explode)

    assert row.status == Status.UNAVAILABLE
    assert "OSError" in row.value


def test_a_description_that_raises_does_not_lose_the_verdict():
    row = check_bridge_dll(
        dll_bit=64,
        find=lambda bit: "C:/x.dll",
        describe=lambda bit: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert row.status == Status.OK
    assert "could not describe the search" in row.detail


# -- the bridge thread row ----------------------------------------------------------


def test_the_bridge_row_reports_a_startup_failure_instead_of_a_verdict():
    thread = BridgeThread(FakeFactory(error=PyjabUnavailable("not Windows")),
                          pump_interval=0.01)
    with pytest.raises(BridgeStartupError):
        thread.start(timeout=5)
    try:
        row = check_bridge_thread(thread)
        assert row.status == Status.UNAVAILABLE
        assert "not Windows" in row.value
    finally:
        thread.close()


def test_the_bridge_row_before_anything_started_is_not_a_problem():
    row = check_bridge_thread(None)

    assert row.status == Status.OK
    assert "not started" in row.value


# -- the whole report ---------------------------------------------------------------


def test_a_non_windows_platform_never_claims_a_jab_verdict(tmp_path):
    report = collect(
        platform="darwin",
        python="3.14.7",
        bitness=64,
        api=PyjabApiReport("1.8.0", None, ()),
        bridge=None,
    )
    by_label = {row.label: row for row in report.rows}

    assert by_label["platform"].status == Status.UNAVAILABLE
    for label in ("Java Access Bridge enabled", "bridge DLL (64-bit)"):
        assert by_label[label].status == Status.UNAVAILABLE
        assert by_label[label].value.startswith("UNAVAILABLE (")
        assert "not measured" in by_label[label].detail

    rendered = report.render()
    assert "UNAVAILABLE (not Windows" in rendered


def test_a_windows_report_uses_the_injected_finders(tmp_path):
    props = tmp_path / "accessibility.properties"
    props.write_text(EXPECTED, encoding="utf-8")

    report = collect(
        platform="win32",
        python="3.12.0",
        bitness=64,
        api=PyjabApiReport("1.8.0", None, ()),
        props_path=props,
        props_expected=EXPECTED,
        dll_bit=64,
        find_dll=lambda bit: "C:/jdk/bin/WindowsAccessBridge-64.dll",
        describe_search=lambda bit: "probed 4 locations",
    )
    by_label = {row.label: row for row in report.rows}

    assert by_label["platform"].status == Status.OK
    assert by_label["Java Access Bridge enabled"].status == Status.OK
    assert by_label["bridge DLL (64-bit)"].status == Status.OK


def test_an_unknown_api_fact_is_unavailable_not_missing():
    api = PyjabApiReport(
        "1.8.0", None,
        (Fact("tree walk (JABElement.walk)", UNKNOWN, "cannot be imported here"),),
    )

    report = collect(platform="darwin", api=api)
    row = next(r for r in report.rows if "tree walk" in r.label)

    assert row.status == Status.UNAVAILABLE
    assert row.value.startswith("UNAVAILABLE (")
    assert row.detail == ""


def test_an_available_api_fact_reads_as_available():
    api = PyjabApiReport("9.9.9", None, (Fact("public java-window listing", AVAILABLE),))

    report = collect(platform="darwin", api=api)
    row = next(r for r in report.rows if "java-window listing" in r.label)

    assert row.status == Status.OK
    assert row.value == "available"


def test_a_pyjab_that_cannot_be_imported_is_reported_with_its_reason():
    report = collect(
        platform="darwin",
        api=PyjabApiReport(None, "ImportError: no module named pyjab", ()),
    )
    row = next(r for r in report.rows if r.label == "pyjab")

    assert row.status == Status.UNAVAILABLE
    assert "no module named pyjab" in row.value
