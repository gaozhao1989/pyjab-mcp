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
    RETIRED_REQUIREMENTS,
    PyjabApiGap,
    PyjabBackend,
    PyjabUnavailable,
    open_backend,
    predates_the_pin,
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

    package = Path(pyjab.__path__[0])
    missing: "List[str]" = []
    if not hasattr(pyjab, "list_java_windows"):
        missing.append("pyjab.list_java_windows() -- java_list_windows needs it (1.10.0)")

    if sys.platform == "win32":  # pragma: no cover - the imports are Windows-only
        from pyjab.common.win32utils import Win32Utils
        from pyjab.jabdriver import JABDriver

        has_detach = hasattr(JABDriver, "detach")
        has_send_keys = hasattr(JABDriver, "send_keys")
        has_foreground = hasattr(Win32Utils, "set_window_foreground")
    else:
        # Importing jabdriver and win32utils off Windows raises pyjab's own descriptive
        # ImportError, so the APIs' presence is read from the installed source **without
        # importing it** -- which is also the only way to see the wheel's actual contents.
        driver_source = (package / "jabdriver.py").read_text(encoding="utf-8")
        win32_source = (package / "common" / "win32utils.py").read_text(encoding="utf-8")
        has_detach = "def detach" in driver_source
        has_send_keys = "def send_keys" in driver_source
        has_foreground = "def set_window_foreground" in win32_source

    if not has_detach:
        missing.append("JABDriver.detach() -- releasing a session needs it (1.10.0)")
    if not has_send_keys:
        missing.append("JABDriver.send_keys() -- java_send_keys needs it (1.11.0)")
    if not has_foreground:
        missing.append("Win32Utils.set_window_foreground() -- R5's answer (1.11.0)")

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


def test_the_requirements_are_empty_because_1_11_0_answered_the_last_two():
    """R4 and R5 are retired, and this is the tripwire for the next gap.

    ``REQUIREMENTS`` stays -- it is where the next one goes rather than into an error
    message -- but pyjab 1.11.0 released ``JABDriver.send_keys`` (R4) and
    ``Win32Utils.set_window_foreground`` (R5), so it is empty. A new entry fails this test on
    purpose: the change that adds one also has to name the release it waits for.
    """
    assert REQUIREMENTS == ()

    for key in ("send_keys", "bring_to_foreground"):
        assert key in RETIRED_REQUIREMENTS, (
            f"{key!r} was recorded as a gap and is not recorded as answered"
        )
        assert "1.11.0" in RETIRED_REQUIREMENTS[key], (
            "the record has to name the release that answered it, or the floor in "
            "pyproject.toml has no reason written down"
        )

    with pytest.raises(KeyError):
        requirement("send_keys")  # retired, so it is not a requirement any more


def test_an_install_below_the_pin_is_told_which_release_answered_the_gap():
    """The probe is a feature-detect, so an older pyjab is answered rather than crashed on."""
    for key, provided in (("send_keys", "send_keys"),
                          ("bring_to_foreground", "set_window_foreground")):
        message = predates_the_pin(key)

        assert "1.11.0" in message, f"{key}: the release to install has to be named"
        assert provided in message, f"{key}: and what that release provides"
        assert "pip install -U pyjab" in message, f"{key}: and the next step"


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


def test_attaching_the_same_window_twice_reuses_the_id_the_first_session_holds(monkeypatch):
    """#71: the window id belongs to the caller, so a re-attach must not retire it.

    ``attach`` releases the earlier binding for the same ``hwnd`` on purpose -- one JAB context
    per window -- but it used to issue a fresh id as well, and the session that named the old
    one was then a session that could never work again: every later call on it answered
    ``window 'w1' is not attached``.  The id is reused instead, so attach-twice is idempotent
    for the id a caller holds, and the release stays inside the backend.
    """
    from tests._fakedriver import install_fake_jabdriver

    drivers = install_fake_jabdriver(monkeypatch)
    backend = PyjabBackend()

    first = backend.attach(hwnd=0x10001)
    again = backend.attach(hwnd=0x10001)

    assert again.window_id == first.window_id, (
        "a caller holds the window id; re-attaching the same window must not issue a new one"
    )
    assert len(drivers) == 2 and drivers[0].detached is True, (
        "and the earlier binding is still released -- one JAB context per window, done inside "
        "the backend where the caller cannot see it"
    )
    # What a session does with the id it holds still reaches a *bound* driver.
    backend.send_keys(first.window_id, "alt+y")
    assert drivers[-1].keys_sent == ["alt+y"]

    other = backend.attach(hwnd=0x10002)
    assert other.window_id != first.window_id, (
        "a different window is a different binding, and still gets its own id"
    )


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


def test_a_chord_goes_to_the_driver_when_pyjab_has_the_api():
    from pyjab_mcp.backend import send_keys_driver
    from tests._fakedriver import FakeDriver

    driver = FakeDriver()

    send_keys_driver(driver, "alt+y")

    assert driver.keys_sent == ["alt+y"]


def test_the_gap_is_reported_when_pyjab_has_no_public_send_keys():
    """The probe, not a version comparison: an older pyjab gets the release it needs."""
    from pyjab_mcp.backend import PyjabApiGap, send_keys_driver
    from tests._fakedriver import FakeDriver

    class WithoutIt(FakeDriver):
        send_keys = None

    with pytest.raises(PyjabApiGap) as caught:
        send_keys_driver(WithoutIt(), "alt+y")

    assert "1.11.0" in str(caught.value), "the release that has it is the actionable part"
    assert "predates it" in str(caught.value)


def test_a_chord_pyjab_does_not_recognise_is_an_error_rather_than_a_no_op():
    """pyjab raises on purpose: a wrong chord acts on somebody else's application."""
    from pyjab_mcp.backend import send_keys_driver
    from tests._fakedriver import FakeDriver

    driver = FakeDriver()

    with pytest.raises(ValueError) as caught:
        send_keys_driver(driver, "alt+banana")

    assert "banana" in str(caught.value)
    assert driver.keys_sent == [], "nothing was sent"


def test_the_probe_reports_the_foreground_capability():
    """`java_diagnostics` has to say it, or the capability lives only in a docstring.

    Only the fact's existence and its status are asserted here. On this machine it is
    ``unknown`` (the Windows module cannot be imported) and in CI it is ``available`` (the pin
    has it), so a ``missing`` branch would be an assertion no run could falsify. What an
    install *below* the pin is told is pinned by
    ``test_an_install_below_the_pin_is_told_which_release_answered_the_gap``, which runs
    everywhere.
    """
    facts = {fact.name: fact for fact in probe_public_api().facts}

    fact = facts["public way to bring a window to the foreground"]
    from pyjab_mcp import backend as backend_module

    assert fact.status in {
        backend_module.AVAILABLE, backend_module.MISSING, backend_module.UNKNOWN
    }
