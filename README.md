# pyjab-mcp

An MCP server that lets an AI agent drive **Java desktop applications** — Swing, AWT,
JavaFX — through [pyjab](https://pypi.org/project/pyjab/), which reads the JVM's own
accessibility tree via Java Access Bridge.

<!-- The MCP Registry proves ownership of the PyPI package by finding this exact line in the
     README *of the published package*, so it cannot be added after a release. Keep it. -->
mcp-name: io.github.gaozhao1989/pyjab-mcp

**It never modifies, restarts or injects into the target application.** That constraint is
the point: launch parameters, classpaths and dependency JARs stay exactly as they are,
which is what makes this usable against a production Java client that you cannot change.

## Status

**0.1.0 is released** — 15 tools: `java_diagnostics`, `java_list_windows`,
`java_attach_window`, `java_snapshot`, `java_find`, `java_get_element`, `java_get_text`,
`java_get_table`, `java_click`, `java_set_text`, `java_select`, `java_fill_form`,
`java_wait_for`, `java_screenshot`, and `java_send_keys`. It needs `pyjab>=1.10.0`: 1.9.0
brought the tree walk a snapshot is built on, and 1.10.0 brought the public window listing and
`JABDriver.detach()`, which is what lets a session end without terminating the application.

`java_send_keys` sends a chord where the installed pyjab provides one, and reports the
requirement where it does not: no **released** pyjab can send `alt+y` yet — the API is written
and merged upstream (`pyjab#168`) — and the tool probes for it rather than assuming, so the
release that carries it makes the tool work with no change here. Until then the reply says
what to do instead.

**Two elements can share a name**, which is why a handle carries where an element *is* as well as
what it is called: searching by name returns every match, each gets its own handle, and the
end-to-end suite checks that clicking one acts on that one and not the other.

**Nothing is acted on until it has been checked.** Every action re-finds the element and
compares role, name, position among siblings and bounds against what the snapshot recorded,
then acts on the element it just checked — and if anything differs, it does nothing and
reports the difference.

`java_get_table` is where this substrate earns its place: it reads a `JTable` through the
JVM's own `AccessibleTable` interface — row and column counts, then cell text, a page of rows
at a time — rather than through a platform bridge that does not carry that information at all.

Live behaviour is verified on a hosted Windows runner by `windows-gui.yml`: the end-to-end
suite attaches to a real Swing application, snapshots it, resolves a handle against the real
element, and checks that a window opened *after* the bridge had been idle is still seen. That
last one is the reason the layer exists — a message pump on the wrong thread passes every
other test in this repository. What it still cannot cover is your application: the run drives
pyjab's test app, and the one that matters is the one you care about.

It needs `pyjab>=1.10.0`, which brought the two APIs this project spent M1 and M2 waiting on:
`list_java_windows()`, so the window list comes from the bridge directly rather than through
pyjab's CLI, and `JABDriver.detach()`, so a session can be released **without terminating the
application** — the only teardown before it killed the bound process.

One gap is still recorded rather than worked around (tracked as
[gaozhao1989/pyjab#168](https://github.com/gaozhao1989/pyjab/issues/168)): **there is no public
way to send a key sequence**, so `java_send_keys` reports that requirement instead of
pretending `send_text` can express `alt+y`.

A handle from `java_snapshot` is a position, not a reference. A later call re-finds the
element and checks its role, name, position and bounds before doing anything, so a window
that moved on produces a message rather than an action on the wrong control.

The one thing that was already settled is the question the whole direction rested on.
Measured on a real Java window, through the same `UIAutomationCore` that generic Windows
automation uses:

| | elements | named | depth |
|---|---|---|---|
| generic UI Automation, the Java window | **6** | 5 | 3 |
| pyjab, the same window | **601** | 513 | 12 |
| *control: UI Automation, the whole desktop* | *147* | *99* | *finished* |

The control is what makes the six mean anything: the *same* measurement over the whole
desktop finishes, so the measurement works and the Java window is the hard case.

## Where to read first

| file | what |
|---|---|
| `docs/ARCHITECTURE.md` | how the server is put together, the constraints that shape it, and what each guard enforces |
| `docs/CLIENTS.md` | connecting Claude Desktop, Claude Code, Cursor and VS Code |
| `docs/RELEASING.md` | how a release happens, and what has to exist before one |
| `CHANGELOG.md` | what changed, and why |

Maintainer working notes are kept out of this repository deliberately. Everything a user or a
contributor needs is above, and the rules that are not prose are enforced by the guards in
`tools/` — which run in CI on every push.

## Requirements

* Windows
* Python 3.10 or newer
* A JVM on the machine, for the application being driven
* `pyjab`, which brings the rest

## Install

```console
pip install pyjab-mcp
```

Working on the server itself is `pip install -e ".[dev]"` — see [Working on it](#working-on-it).

## Use

The console script the package installs:

```json
{
  "mcpServers": {
    "pyjab-mcp": { "command": "python", "args": ["-m", "pyjab_mcp"] }
  }
}
```

Claude Desktop reads `%APPDATA%\Claude\claude_desktop_config.json`, and takes this (double
the backslashes — JSON needs it):

```json
{
  "mcpServers": {
    "pyjab-mcp": { "command": "C:\\Users\\you\\.venv\\Scripts\\pyjab-mcp.exe", "args": [] }
  }
}
```

Claude Code writes the same entry for you: `claude mcp add pyjab-mcp -- pyjab-mcp`.

**[docs/CLIENTS.md](docs/CLIENTS.md)** has Cursor and VS Code as well, the config file each
one reads, the Windows path problem that makes most setups fail, and what to do when a tool
answers with an error.

Start with `java_diagnostics`. It reports which part of the environment is missing —
Java Access Bridge not enabled, no bridge DLL, a 32-bit interpreter against a 64-bit JVM —
instead of leaving a tool call to fail with a locator error.

## What it does, and what it cannot

Java Access Bridge exposes what an application **declares** about itself: a tree of roles,
names, values, states and the actions a control supports. That is what this server drives, and
it sets the boundary of what it can reach.

**It works on** Java applications that expose accessibility — Swing and AWT applications and
applets **running as desktop applications**, and JavaFX where the accessibility bridge is
present. Buttons, menus, tables, trees, lists, text fields and their values are all readable
and actionable, and `java_get_table` reads a table's cells through the table's own interface
rather than the pixels.

**It cannot reach**, because the information is not there to read:

| Not supported | Why |
|---|---|
| **Canvas-drawn interfaces** — a `JPanel` painting its own widgets | There are no accessible children, so there is nothing to find, click or read. `java_screenshot` will show you a picture, and a picture is not something you can act on. If a panel like this needs to be driven, the application has to expose the controls. **Measured, not just expected.** pyjab investigated a canvas-painted panel (`pyjab#73`) and found no accessible children; this repository now has the regression test, against a fixture application that paints a panel and contains no child components (`tests/e2e/fixtures/BoundaryApp.java`). It asserts the pair: the panel is a leaf with `children_count = 0`, and a screenshot of *the same element* returns real PNG bytes — pixels without structure, which is the whole point. It runs in the `windows-gui` job; pyjab's own test application still has no canvas, which is tracked in `pyjab#169`. |
| **Java inside a browser** (an embedded JVM, a Java Web Start descendant) | The browser owns the window and does not publish the applet's Java accessibility tree through JAB. |
| **Applets in a plugin host** | Same reason: no JAB tree reaches the desktop. |
| **Non-Java Windows applications** | Nothing here speaks UIA. A UIA-based MCP server is the right tool. |
| **Anything pixel-based** — reading a screenshot to guess at layout, clicking coordinates | Deliberately out of scope. Guessing at pixels is what this project exists to replace. |

Also worth knowing:

* **The application must be JAB-enabled before it starts.** Enabling JAB afterwards does not
  add the tree to a process already running; restart it.
* **This server only attaches.** It never launches an application, and it never terminates one
  — `JABDriver.detach()` releases the binding and leaves the process running.
* **A picture is not structure.** `java_screenshot` is for looking; anything you want to
  *act* on has to be found in the tree, because that is where handles and names live.

## Support

* **Free support** — bugs, questions and feature requests go to
  [GitHub issues](https://github.com/gaozhao1989/pyjab-mcp/issues). Include the output of
  `pyjab-mcp --version` and of the `java_diagnostics` tool: between them they answer most
  environmental questions before they are asked.
* **Commercial and enterprise support** — deployment inside a restricted network, adaptation
  to a particular application, or an SLA: **gaozhao89@qq.com** (the address in this package's
  metadata). The open-source package is complete and unrestricted; there is no licence key, no
  quota and no feature held back for it.

## Verifying it

The suite that runs on every push cannot drive an application: it needs Windows, a JDK, a
desktop session and a running Swing app. That layer is `tests/e2e/`, skipped unless
`PYJAB_MCP_E2E=1`, and `windows-gui.yml` runs it on a hosted `windows-latest` runner — by
hand, or on a weekly schedule. It starts a second JVM *after* the bridge has been idling,
because a message pump on the wrong thread passes everything else and fails exactly that.

```console
gh workflow run windows-gui.yml -f java=17     # the end-to-end run and the JDK matrix
gh run watch
```

It is not a status check: nobody runs it unless somebody asks (or the week comes round), so a
green default CI run still says nothing about a live window.

## Releasing

Tag-driven: bump `src/pyjab_mcp/__init__.py`, move the changelog's `Unreleased` section under
the new version, set `server.json`, then `python tools/check_release_version.py v0.1.0` and tag.
[`docs/RELEASING.md`](docs/RELEASING.md) has the whole procedure, the two one-time setups (a
PyPI pending publisher and the registry namespace), and what the four release jobs do.

## Working on it

```console
python -m pytest tests/                    # the portable suite, any platform
python tools/check_pyjab_api_surface.py    # every pyjab call is public, released, recorded
python tools/check_documented_surface.py   # docs/ARCHITECTURE.md's tool table and the code agree
python tools/check_local_only_files.py     # maintainer notes stay unpublished
python tools/check_pyjab_readiness.py      # what pyjab still owes this project
python tools/check_pyjab_changelog.py      # every marked pyjab change has an answer here
python -m pytest tests/contract/           # the pyjab shapes this project depends on
python -m build && python tools/check_dist_contents.py --dist dist
```

The guards are the project's rules made mechanical, in the same shape pyjab uses for its own:
a registry of what is allowed, checked in both directions, so adding a call site fails until
somebody writes down why it is acceptable. `docs/ARCHITECTURE.md` lists them and what each
one enforces.
