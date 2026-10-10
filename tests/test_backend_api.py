"""L1 tests for the pyjab adapter: what it asks for, and what it does when pyjab says no.

The adapter is the only place pyjab is touched, so these tests are also the tripwire for a
pyjab release that adds the missing API: the probe and the backend are asserted to agree,
which means the day ``list_java_windows`` appears, this suite says so rather than the gap
message quietly going stale.
"""

from __future__ import annotations

import sys

import pytest

from pyjab_mcp.backend import (
    AVAILABLE,
    MISSING,
    REQUIREMENTS,
    PyjabApiGap,
    PyjabBackend,
    PyjabUnavailable,
    open_backend,
    probe_public_api,
    requirement,
)


def test_pyjab_is_installed_and_probed():
    api = probe_public_api()

    assert api.error is None, (
        "pyjab is a dependency of this package, so the probe should find it; "
        f"it said: {api.error}"
    )
    assert api.version
    assert api.facts


def test_the_probe_and_the_backend_agree_about_the_window_listing():
    """One source of truth: the public name is used when it exists, the CLI when it does not.

    Off Windows the fallback cannot answer either -- pyjab is Windows only -- so the
    assertion is that the failure names the *fallback* rather than pretending no listing
    path exists.
    """
    api = probe_public_api()
    backend = PyjabBackend()

    if api.has("public java-window listing"):
        assert isinstance(backend.java_windows(), tuple)
        return

    if sys.platform == "win32":  # pragma: no cover - needs a Windows box with a JVM
        assert isinstance(backend.java_windows(), tuple)
        return

    with pytest.raises(PyjabUnavailable) as caught:
        backend.java_windows()
    assert "pyjab-inspect" in str(caught.value)


def test_the_window_listing_falls_back_to_the_documented_cli():
    """The fallback is a public interface: `pyjab-inspect windows --json`."""
    import json
    from types import SimpleNamespace

    calls = []

    def runner(command):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"hwnd": 65537, "title": "App", "pid": 42}]),
            stderr="",
        )

    windows = PyjabBackend(cli_runner=runner).java_windows()

    assert calls == [[sys.executable, "-m", "pyjab.inspector", "windows", "--json"]]
    assert [(w.hwnd, w.title, w.pid) for w in windows] == [(65537, "App", 42)]


def test_an_empty_cli_answer_is_a_measurement_not_a_failure():
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(returncode=0, stdout="[]", stderr="")
    assert PyjabBackend(cli_runner=runner).java_windows() == ()


def test_a_cli_that_fails_is_reported_with_its_own_message():
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(
        returncode=1, stdout="", stderr="No Java windows found.\nCheck the JVM."
    )

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend(cli_runner=runner).java_windows()

    assert "No Java windows found." in str(caught.value)


def test_a_cli_that_does_not_return_json_is_reported_not_guessed():
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(returncode=0, stdout="not json", stderr="")

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend(cli_runner=runner).java_windows()

    assert "did not return JSON" in str(caught.value)


def test_the_gap_message_names_what_is_missing_and_where_to_read_about_it():
    message = requirement("list_java_windows").message()

    assert "java_list_windows" in message
    assert "expected in:" in message
    assert "AGENTS.md" in message
    # The workaround has to be named as unacceptable, not left as an exercise.
    assert "why not a workaround:" in message


def test_every_requirement_renders_and_has_a_unique_key():
    keys = [item.key for item in REQUIREMENTS]

    assert len(keys) == len(set(keys))
    for item in REQUIREMENTS:
        message = item.message()
        assert item.wanted in message
        assert item.expected_in in message


def test_pumping_needs_pyjabs_windows_modules_which_do_not_exist_here():
    """The pump is real on Windows and a loud ImportError everywhere else -- never silent."""
    backend = PyjabBackend()

    if sys.platform != "win32":
        with pytest.raises(ImportError):
            backend.pump()
    else:  # pragma: no cover - Windows only, and needs JAB present
        backend.pump()


def test_closing_the_backend_never_touches_a_process():
    """``close()`` must be safe: the pyjab detach it would need kills the target."""
    PyjabBackend().close()


def test_open_backend_refuses_a_non_windows_platform_with_a_sentence():
    if sys.platform == "win32":  # pragma: no cover - this suite runs off Windows normally
        pytest.skip("on Windows this path is the real one")

    with pytest.raises(PyjabUnavailable) as caught:
        open_backend()

    message = str(caught.value)
    assert "not Windows" in message
    assert "only on Windows" in message
    assert "cannot cross machines" in message


def test_the_window_listing_record_shape_is_normalised():
    from pyjab_mcp.backend import _to_window

    as_mapping = _to_window({"hwnd": 65537, "title": "App", "pid": 42})
    assert (as_mapping.hwnd, as_mapping.title, as_mapping.pid) == (65537, "App", 42)

    class Record:
        hwnd = 65537
        title = "App"
        pid = None

    as_object = _to_window(Record())
    assert (as_object.hwnd, as_object.title, as_object.pid) == (65537, "App", None)


def test_a_record_without_a_handle_is_reported_as_the_gap_not_guessed_at():
    from pyjab_mcp.backend import _to_window

    with pytest.raises(PyjabApiGap):
        _to_window({"title": "no handle here"})


def test_the_walk_fact_is_unknown_rather_than_missing_off_windows():
    """``pyjab.jabelement`` cannot be imported here, so the answer is not "it is absent"."""
    api = probe_public_api()
    fact = api.fact("tree walk (JABElement.walk)")

    if sys.platform == "win32":  # pragma: no cover
        assert fact.status in {AVAILABLE, MISSING}
    else:
        assert fact.status == "unknown"
        assert "cannot be imported here" in fact.detail
