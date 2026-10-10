"""L1 tests for the pyjab adapter: what it asks for, and what it does when pyjab says no.

The adapter is the only place pyjab is touched, so these tests are also the tripwire for a
pyjab release that adds the missing API: the probe and the backend are asserted to agree,
which means the day ``list_java_windows`` appears, this suite says so rather than the gap
message quietly going stale.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

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


def test_the_installed_pyjab_has_what_the_pin_buys():
    """First, and it names the fix and the missing capability.

    Learned the hard way: a virtual environment left on pyjab 1.9.0 after the pin moved to
    1.10.0 produced nine failures across the suite -- a KeyError for a retired requirement, a
    contract test for a function that was not there, a diagnostics fact that had moved --
    every one of them true, and none of them saying "upgrade pyjab". One test that says so is
    worth nine that do not.

    Feature-detected rather than version-compared, like everything else here (§6.3): what
    matters is the capability, not the number.
    """
    import pyjab

    missing: "List[str]" = []
    if not hasattr(pyjab, "list_java_windows"):
        missing.append("pyjab.list_java_windows() -- java_list_windows needs it (1.10.0)")

    if sys.platform == "win32":  # pragma: no cover - the import is Windows-only
        from pyjab.jabdriver import JABDriver

        if not hasattr(JABDriver, "detach"):
            missing.append("JABDriver.detach() -- releasing a session needs it (1.10.0)")
    else:
        # Importing jabdriver off Windows raises pyjab's own descriptive ImportError, so the
        # API's presence is read from the installed source **without importing it**.
        driver_source = (Path(pyjab.__path__[0]) / "jabdriver.py").read_text(encoding="utf-8")
        if "def detach" not in driver_source:
            missing.append("JABDriver.detach() -- releasing a session needs it (1.10.0)")

    assert not missing, (
        f"this environment has pyjab {getattr(pyjab, '__version__', 'unknown')}, which is "
        f"missing: "
        + "; ".join(missing)
        + ". Upgrade it: python -m pip install -U pyjab"
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
    """One source of truth: the public API when it exists, the CLI when it does not.

    pyjab 1.10.0 added ``list_java_windows()``, so this is now the path a supported install
    takes. Off Windows it raises pyjab's own descriptive ImportError, which the adapter turns
    into an unusable-environment answer rather than passing an import failure up to the tool.
    """
    api = probe_public_api()
    backend = PyjabBackend()

    if api.has("public java-window listing"):
        if sys.platform == "win32":  # pragma: no cover - needs a Windows box with a JVM
            assert isinstance(backend.java_windows(), tuple)
            return
        with pytest.raises(PyjabUnavailable) as caught:
            backend.java_windows()
        assert "Windows-only" in str(caught.value)
        return

    with pytest.raises(PyjabUnavailable) as caught:
        backend.java_windows()
    assert "pyjab-inspect" in str(caught.value), (
        "an install below the pin falls back to the CLI; off Windows that fails too, and the "
        "message has to name what was tried"
    )


@pytest.fixture
def below_the_pin(monkeypatch):
    """Force the CLI fallback, which a supported install no longer reaches."""
    monkeypatch.setattr(
        "pyjab_mcp.backend._public_window_listing", lambda: None
    )


def test_the_window_listing_falls_back_to_the_documented_cli(below_the_pin):
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


def test_an_empty_cli_answer_is_a_measurement_not_a_failure(below_the_pin):
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(returncode=0, stdout="[]", stderr="")
    assert PyjabBackend(cli_runner=runner).java_windows() == ()


def test_a_cli_that_fails_is_reported_with_its_own_message(below_the_pin):
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(
        returncode=1, stdout="", stderr="No Java windows found.\nCheck the JVM."
    )

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend(cli_runner=runner).java_windows()

    assert "No Java windows found." in str(caught.value)


def test_a_cli_that_does_not_return_json_is_reported_not_guessed(below_the_pin):
    from types import SimpleNamespace

    runner = lambda command: SimpleNamespace(returncode=0, stdout="not json", stderr="")

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend(cli_runner=runner).java_windows()

    assert "did not return JSON" in str(caught.value)


def test_the_gap_message_names_what_is_missing_and_where_to_read_about_it():
    message = requirement("send_keys").message()

    assert "key sequence or shortcut" in message
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


def test_a_record_without_a_handle_is_reported_rather_than_guessed_at():
    """A malformed record is a fact about the response, not a missing API."""
    from pyjab_mcp.backend import _to_window

    with pytest.raises(PyjabUnavailable) as caught:
        _to_window({"title": "no handle here"})

    assert "without an hwnd" in str(caught.value)


def test_the_walk_fact_is_unknown_rather_than_missing_off_windows():
    """``pyjab.jabelement`` cannot be imported here, so the answer is not "it is absent"."""
    api = probe_public_api()
    fact = api.fact("tree walk (JABElement.walk)")

    if sys.platform == "win32":  # pragma: no cover
        assert fact.status in {AVAILABLE, MISSING}
    else:
        assert fact.status == "unknown"
        assert "cannot be imported here" in fact.detail


def test_detaching_releases_the_window_and_leaves_the_process_alone():
    """R2's answer, and the reason `close()` no longer just clears a dict."""
    from tests._fakedriver import FakeDriver

    backend = PyjabBackend()
    driver = FakeDriver()
    backend._drivers["w1"] = driver

    assert backend.detach("w1") is True
    assert driver.detached is True, "the window's JAB reference has to be given back"
    assert backend.detach("w1") is False, "detaching twice is not an error, just nothing to do"


def test_a_detach_that_raises_does_not_stop_the_others(caplog):
    """It runs on the way out: one uncooperative window must not strand the rest."""
    from tests._fakedriver import FakeDriver

    class Exploding(FakeDriver):
        def detach(self) -> None:
            raise RuntimeError("the bridge refused")

    backend = PyjabBackend()
    backend._drivers["w1"] = Exploding()
    backend._drivers["w2"] = FakeDriver()

    backend.close()

    assert backend._drivers == {}


def test_a_bridge_that_cannot_be_used_is_an_environment_answer(monkeypatch):
    """Windows without a JDK: pyjab raises FileNotFoundError for the DLL.

    It is an environment fact, not an unexpected crash, and pyjab's own text says which
    directories were searched -- so it is kept rather than summarised away.
    """
    def listing():
        raise FileNotFoundError(
            "Java Access Bridge DLL (WindowsAccessBridge-64.dll) could not be located.\n"
            "  Install a JDK and set JAVA_HOME"
        )

    monkeypatch.setattr("pyjab_mcp.backend._public_window_listing", lambda: listing)

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend().java_windows()

    assert "could not list windows" in str(caught.value)
    assert "WindowsAccessBridge-64.dll" in str(caught.value), (
        "pyjab's probing detail is the useful part of this message"
    )
    assert "JAVA_HOME" in str(caught.value)


def test_a_windows_only_api_off_windows_is_an_environment_answer(monkeypatch):
    def listing():
        raise ImportError("pyjab drives the Windows Java Access Bridge and only runs on Windows.")

    monkeypatch.setattr("pyjab_mcp.backend._public_window_listing", lambda: listing)

    with pytest.raises(PyjabUnavailable) as caught:
        PyjabBackend().java_windows()

    assert "Windows-only" in str(caught.value)
