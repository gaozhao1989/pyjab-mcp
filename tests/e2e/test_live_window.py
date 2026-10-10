"""L3: a real Java window, driven through the real bridge, on a real desktop.

This is the only layer that can say a tool call worked. Everything else in the suite runs
against a fake; here the pyjab driver is real, the JAB bridge is real, the accessibility
tree belongs to a JVM, and the pump has to be on the right thread or the windows simply do
not appear.

**It is skipped unless ``PYJAB_MCP_E2E=1``**, because it needs Windows, a JDK, a desktop
session and a running Swing application, and none of those is something a unit test should
require. `.github/workflows/windows-gui.yml` sets the variable on a hosted
``windows-latest`` runner, which has an interactive desktop (measured by pyjab: session 2,
``console``, ``explorer.exe`` running) rather than being a service in session 0.

The application is pyjab's own test app, not one of ours. Its source ships in pyjab's sdist
and pyjab's GUI suite compiles it on this same runner, so it is a known-good target rather
than a second Java program that has never been compiled. The plan asks for L3 "on the pyjab
test app", and this is that app.

**Every backend call goes through the bridge thread**, including the listing. That is not
tidiness: a JAB call from the test's own thread would be the exact mistake this module exists
to detect, and a test that makes it would be measuring the wrong thing. The one deliberate
exception is ``accessibility_enabled``, which writes a file rather than calling the bridge.

What it is looking for, in order of how much it would hurt to get wrong:

1. **A window that opens after startup is still seen.** This is the D3 trap and the reason
   L3 exists at all: a pump running on a different thread from the JAB calls appears to work
   until a window opens later, and then that window is never announced. No L1 test can catch
   it -- the fake would have to be wrong in the same way the real thing is. So a *second* JVM
   is started after the executor has been idling, and its window has to appear.
2. **A bounded snapshot of a real tree**, with the controls the application actually has.
3. **A handle resolves and verifies against the element it was taken from** -- the D2 loop,
   against a real ``JABElement``, with the reference released afterwards.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Tuple

import pytest

from pyjab_mcp.backend import AttachedWindow, PyjabBackend, PyjabUnavailable, open_backend
from pyjab_mcp.bridge import BridgeThread
from pyjab_mcp.handles import locator_for, verify

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("PYJAB_MCP_E2E") != "1",
        reason="L3 needs Windows, a JDK, a desktop session and a Swing app; "
               "set PYJAB_MCP_E2E=1 (see .github/workflows/windows-gui.yml)",
    ),
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="pyjab drives Java Access Bridge, which exists only on Windows",
    ),
]

APP_CLASS = "PyjabTestApp"
MAIN_TITLE = "pyjab-mcp e2e main window"
LATE_TITLE = "pyjab-mcp e2e late window"

#: Generous on purpose: a cold JVM plus JAB's first attach is seconds, not milliseconds, and
#: a flaky timeout here would be reported as a bug in this project.
STARTUP_TIMEOUT = 120.0
#: A listing costs a process (pyjab 1.9.0 has no public listing API), so polling is not free.
POLL_INTERVAL = 3.0


# ---------------------------------------------------------------------------------
# Getting the application
# ---------------------------------------------------------------------------------


def app_source() -> Path:
    """``PyjabTestApp.java``: an explicit directory, or pyjab's own sdist.

    The fetching itself lives in ``tools/fetch_test_app.py`` so that the CI job and this test
    cannot disagree about which version or which file, and so the download is testable on a
    machine with no JDK at all -- which is where it was written.
    """
    explicit = os.environ.get("PYJAB_TEST_APP_SRC")
    if explicit:
        candidate = Path(explicit) / "PyjabTestApp.java"
        if not candidate.is_file():
            pytest.skip(f"PYJAB_TEST_APP_SRC={explicit!r} has no PyjabTestApp.java")
        return candidate

    from tools.fetch_test_app import fetch

    try:
        return fetch()
    except RuntimeError as exc:
        pytest.skip(f"could not obtain pyjab's test application: {exc}")


@pytest.fixture(scope="session")
def compiled_app() -> Path:
    """Compile the application once, and say plainly when there is no JDK to do it with."""
    if not shutil.which("javac") or not shutil.which("java"):
        pytest.skip("no javac/java on PATH: install a JDK (the CI job uses setup-java)")

    classes = Path(tempfile.mkdtemp(prefix="pyjab-mcp-e2e-classes-"))
    result = subprocess.run(
        [shutil.which("javac"), "-d", str(classes), str(app_source())],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"javac failed:\n{result.stdout}\n{result.stderr}")
    return classes


@pytest.fixture(scope="session", autouse=True)
def accessibility_enabled() -> None:
    """Write the file that makes a JVM load Java Access Bridge -- **before any JVM starts**.

    This is the step pyjab's own GUI fixtures call out: a JVM reads the properties file at
    start-up, so enabling the bridge afterwards produces a window JAB cannot see, which reads
    as "the bridge is broken" rather than as an ordering mistake.

    It writes to the home directory on purpose, which is the one place in this project that
    does. That is what the file is for, and this module only runs when somebody asked for an
    end-to-end test on a disposable CI runner.
    """
    from pyjab import config

    props = Path(config.A11Y_PROPS_PATH)
    props.write_text(config.A11Y_PROPS_CONTENT, encoding="utf-8")
    assert props.is_file(), f"could not write {props}"

    from pyjab.common.service import Service

    assert Service().is_bridge_enabled(), "Java Access Bridge did not end up enabled"


# ---------------------------------------------------------------------------------
# The bridge, and one application instance attached through it
# ---------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live() -> "Tuple[BridgeThread, PyjabBackend]":
    """The real bridge thread and the backend it owns.

    The backend is captured as ``open_backend()`` builds it -- it has to be the *same*
    instance the thread holds, because ``PyjabBackend`` keeps the bound drivers in itself and
    a second instance would have none.
    """
    created: "dict[str, Any]" = {}

    def factory() -> PyjabBackend:
        backend = open_backend()
        assert isinstance(backend, PyjabBackend)
        created["backend"] = backend
        return backend

    thread = BridgeThread(factory, pump_interval=0.05)
    thread.start(timeout=120)
    try:
        yield thread, created["backend"]
    finally:
        thread.close()


def call(bridge: BridgeThread, function, *args, timeout: float = 180.0, **kwargs):
    """Run one backend call on the owning thread.  Nothing here calls JAB directly."""
    return bridge.submit(function, *args, **kwargs).result(timeout=timeout)


def wait_for_window(bridge: BridgeThread, backend: PyjabBackend, title: str,
                    timeout: float = STARTUP_TIMEOUT):
    """Poll the real listing until *title* appears, or fail with what was actually there."""
    deadline = time.monotonic() + timeout
    seen: "list[str]" = []
    while time.monotonic() < deadline:
        try:
            windows = list(call(bridge, backend.java_windows))
        except PyjabUnavailable as exc:
            seen = [f"(listing failed: {exc})"]
            time.sleep(POLL_INTERVAL)
            continue
        seen = [window.title for window in windows]
        for window in windows:
            if window.title == title:
                return window
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"window {title!r} never appeared within {timeout}s. JAB could see: {seen}")


def start_app(title: str, classes: Path) -> subprocess.Popen:
    java = shutil.which("java")
    assert java is not None
    return subprocess.Popen(
        [java, "-cp", str(classes), APP_CLASS, f"--title={title}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def stop_app(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged JVM
        process.kill()


@pytest.fixture(scope="session")
def main_window(live, accessibility_enabled, compiled_app: Path):
    """One running instance, listed and attached through the real bridge."""
    bridge, backend = live
    process = start_app(MAIN_TITLE, compiled_app)
    try:
        wait_for_window(bridge, backend, MAIN_TITLE)
        attached: AttachedWindow = call(bridge, backend.attach, title=MAIN_TITLE)
        yield attached
    finally:
        stop_app(process)


# ---------------------------------------------------------------------------------
# The three things L3 is here to say
# ---------------------------------------------------------------------------------


def test_the_window_is_listed_and_attached(main_window):
    """Step 1 of the plan's end-to-end flow, against a real window."""
    assert main_window.hwnd, "a bound window has a handle"
    assert main_window.title == MAIN_TITLE
    assert main_window.pid, "and a process id, which is how the agent tells instances apart"


def test_a_snapshot_renders_the_controls_the_application_has(live, main_window):
    """Step 2: a bounded view of a real tree, with the elements the app actually builds."""
    bridge, backend = live

    # Deeper than a snapshot's default on purpose: the window frame, layered panes and
    # content pane come first, so a button lives seven or eight levels down. The first real
    # run of this file walked three and found the menu bar and "Main panel" but nothing
    # inside it -- which is a fact about depth, not about the tree.
    walk = call(bridge, backend.walk, main_window.window_id, max_depth=12, limit=4000)
    rendered = walk.to_text(max_nodes=200, only_interactive=True, fields=("states",))

    names = [node.record.name for node in walk.nodes]
    # These are set with setName() in PyjabTestApp, so this is a statement about the
    # accessibility tree JAB reports, not about our renderer.
    for expected in ("Main panel", "Sports table", "Middle button"):
        assert expected in names, (
            f"{expected!r} is not in the tree JAB reported. First 30 names: {names[:30]}"
        )
    assert "window" in rendered.text.splitlines()[0]
    assert rendered.estimated_tokens < 4000, (
        f"the default budget produced ~{rendered.estimated_tokens} tokens on a real window"
    )
    print(f"\n{rendered.text[:2000]}")
    print(f"\n[L3] walked {walk.walked} node(s); truncated={walk.truncated} ({walk.reason})")


def test_a_handle_resolves_and_verifies_against_the_real_element(live, main_window):
    """Step 3, and the D2 loop where it matters: a real element, re-found and checked."""
    bridge, backend = live

    walk = call(bridge, backend.walk, main_window.window_id, max_depth=12, limit=4000)
    target = next(
        (index for index, node in enumerate(walk.nodes)
         if node.record.name == "Middle button"),
        None,
    )
    assert target is not None, "the button the snapshot should have found is not there"

    locator = locator_for(walk, target)
    assert locator, "a real element with a reported position must be addressable"

    resolved = call(bridge, backend.resolve, main_window.window_id, locator)

    result = verify(walk.nodes[target].record, resolved)
    assert result.ok, f"{locator} resolved to something else: {result.message()}"
    print(f"\n[L3] {locator} -> {resolved.label} (verified)")


def test_a_window_that_opens_later_is_seen(live, main_window, compiled_app: Path):
    """The D3 trap, and the reason this module exists.

    A pump on a different thread from the JAB calls works right up until a window opens
    afterwards, and then that window is never announced -- no exception, no timeout, just a
    window that is not there. So: let the executor idle, start a *second* JVM, and require
    its window to appear.
    """
    bridge, backend = live

    # Let the loop pump with nothing to do, so this is a late window rather than a race
    # against start-up.
    time.sleep(5)

    process = start_app(LATE_TITLE, compiled_app)
    try:
        window = wait_for_window(bridge, backend, LATE_TITLE)
        assert window.hwnd

        attached = call(bridge, backend.attach, title=LATE_TITLE)
        assert attached.hwnd != 0

        walk = call(bridge, backend.walk, attached.window_id, max_depth=2, limit=500)
        assert walk.nodes, "the late window's tree should not be empty"
        print(
            "\n[L3] a window opened after the executor had been running for a while was "
            f"seen, attached and walked ({walk.walked} nodes)"
        )
    finally:
        stop_app(process)


def test_a_real_table_reads_its_cells_a_page_at_a_time(live, main_window):
    """Step 4: the capability a platform bridge does not carry.

    ``find`` locates the table by role, then the table is read through its own
    accessibility interface. The values are pyjab's test application's own row data, so this
    asserts that JAB handed over the *table* rather than that our renderer can print strings.
    """
    bridge, backend = live

    matches = call(bridge, backend.find, main_window.window_id, by="role", value="table")
    located = [(record, locator) for record, locator in matches if locator]
    assert located, f"no addressable table in the window; matches were {matches}"

    table_record, locator = located[0]
    assert table_record.role == "table"

    reading = call(bridge, backend.table, main_window.window_id, locator,
                   row_start=0, row_count=3)
    assert reading.row_count >= 5, f"the table reports {reading.row_count} rows"
    assert reading.column_count == 4, f"the table has {reading.column_count} columns"

    first_row = reading.rows[0]
    print(
        f"\n[L3] row 0 = {first_row}; from_name={reading.from_name}; "
        f"unreadable={reading.unreadable}"
    )
    assert first_row[0] == "Kathy", (
        f"first cell read as {first_row[0]!r}; the application reports no AccessibleText for "
        f"cells, so this comes from the accessible name (from_name={reading.from_name})"
    )
    assert first_row[1] == "Snowboarding", f"second cell read as {first_row[1]!r}"

    later_page = call(bridge, backend.table, main_window.window_id, locator,
                      row_start=1, row_count=1)
    assert len(later_page.rows) == 1, "a one-row page is one row, not the whole table"
    assert later_page.rows[0][0] == "John"
    print(
        f"\n[L3] table {table_record.name!r}: {reading.row_count} rows x "
        f"{reading.column_count} columns; page 1 row 0 = {first_row}"
    )


# ---------------------------------------------------------------------------------
# The flow PLAN §10 asks for, through the MCP tools rather than the backend
# ---------------------------------------------------------------------------------

async def tool(server, name: str, arguments: dict) -> str:
    """Call one tool and fail loudly if it reports an error.

    The point of this test is the flow, so an error is a failure here rather than something to
    inspect: each tool's own error paths are covered by L1, and what cannot be covered there
    is whether the six steps work in sequence on a real window.
    """
    result = await server.call_tool(name, arguments)
    text = "".join(
        part.text for part in result.content if getattr(part, "text", None)
    )
    assert result.is_error is False, f"{name} failed: {text}"
    return text


def handle_in(text: str) -> str:
    """The first ``e:...`` handle in a tool's reply."""
    import re

    match = re.search(r"\[(e:[0-9a-f]+)\]", text)
    assert match, f"no handle in the reply: {text}"
    return match.group(1)


async def wait_for_tool_window(server, title: str, timeout: float = STARTUP_TIMEOUT) -> str:
    """Poll java_list_windows through the tools until *title* appears."""
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        seen = await tool(server, "java_list_windows", {})
        if title in seen:
            return seen
        await asyncio.sleep(POLL_INTERVAL)
    pytest.fail(f"{title!r} never appeared through java_list_windows. Last reply:\n{seen}")


@pytest.mark.asyncio
async def test_the_whole_flow_through_the_mcp_tools(live, main_window):
    """The six steps PLAN §10 names, as an agent would take them.

    list windows → attach → snapshot → find an element → set text → read it back → read the
    table → click a button and see a new window appear. Everything below goes through the tool
    surface, so what is verified is the thing the user actually talks to, not the layer under
    it.
    """
    from pyjab_mcp.server import ServerState, create_server

    bridge, _backend = live
    state = ServerState(bridge=bridge)
    server = create_server(state=state)

    # 1. Which windows are there?  (The CLI fallback, over the tool surface.)
    listing = await tool(server, "java_list_windows", {})
    assert MAIN_TITLE in listing, listing

    # 2. Bind to it.
    attached = await tool(server, "java_attach_window", {"title": MAIN_TITLE})
    session_id = attached.split(" ", 1)[0]
    assert session_id.startswith("s:"), attached

    # 3. A bounded view, deep enough to hold the controls.
    snapshot = await tool(
        server, "java_snapshot",
        {"session_id": session_id, "depth": 12, "max_nodes": 400},
    )
    assert "push button" in snapshot and "[e:" in snapshot, snapshot[:400]

    # 4. The text field, found by the text it currently holds.
    field = await tool(server, "java_find", {"session_id": session_id, "name": "1122233455"})
    field_handle = handle_in(field)

    # 5. Type into it, then read back what was typed.  This is the pair that a real run broke:
    #    the edit renames the field (its name *is* its text) and invalidates a name locator.
    await tool(
        server, "java_set_text",
        {"session_id": session_id, "handle": field_handle, "value": "hello from l3"},
    )
    read_back = await tool(
        server, "java_get_text", {"session_id": session_id, "handle": field_handle}
    )
    assert "hello from l3" in read_back, read_back

    # 6. The table, read as data rather than as pixels.
    table = await tool(server, "java_find", {"session_id": session_id, "role": "table"})
    table_handle = handle_in(table)
    cells = await tool(
        server, "java_get_table",
        {"session_id": session_id, "handle": table_handle, "row_start": 0, "row_count": 2},
    )
    assert "Kathy" in cells and "Snowboarding" in cells, cells

    # 7. Click a button and watch a new window arrive -- the flow's last step, and the one
    #    that would catch a stale handle: the button is verified before it is clicked.
    dialog_button = await tool(
        server, "java_find", {"session_id": session_id, "name": "Show dialog"}
    )
    await tool(
        server, "java_click",
        {"session_id": session_id, "handle": handle_in(dialog_button)},
    )
    await wait_for_tool_window(server, "A Dialog")
    print("\n[L3] the six-step flow completed through the MCP tools")

    # 8. And the form-filling shortcut reports each field it was given.
    filled = await tool(server, "java_fill_form", {
        "session_id": session_id,
        "fields": [{"handle": field_handle, "value": "second pass"}],
    })
    assert "1 of 1 field(s) set" in filled, filled
    assert "second pass" in await tool(
        server, "java_get_text", {"session_id": session_id, "handle": field_handle}
    )
