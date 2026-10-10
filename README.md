# pyjab-mcp

An MCP server that lets an AI agent drive **Java desktop applications** — Swing, AWT,
JavaFX — through [pyjab](https://pypi.org/project/pyjab/), which reads the JVM's own
accessibility tree via Java Access Bridge.

**It never modifies, restarts or injects into the target application.** That constraint is
the point: launch parameters, classpaths and dependency JARs stay exactly as they are,
which is what makes this usable against a production Java client that you cannot change.

## Status

**M1 is implemented; nothing here has driven a live Java application yet.** The bridge
thread, the environment self-check and the session tools exist and are covered by the L1
suite plus a real stdio handshake, but **no tool has yet been run against a real window** —
that needs a Windows desktop, and the end-to-end run is a separate step.

M1 is also blocked in one specific, measured way. Against released `pyjab` (1.8.0) there is
**no public way to list the Java windows JAB can see**: `JABDriver` cannot be constructed
without binding a window, and the piece that arms a bridge without one
(`pyjab.inspector.bridge_without_window()`) is private. So `java_list_windows` and
`java_attach_window` report exactly what is missing and why the workaround is not
acceptable, rather than reaching into a private API. One further requirement — a
non-destructive detach, without which a session cannot be released without killing the
application — and one sequencing fact — the tree walk M2 needs is unreleased — are recorded
the same way. The audit is in `AGENTS.md` §6 and the requirements are data in
`src/pyjab_mcp/backend.py`.

The one thing that was already settled is the question the whole direction rested on.
Measured on a real Java window, through the same `UIAutomationCore` that generic Windows
automation uses:

| | elements | named | depth |
|---|---|---|---|
| generic UI Automation, the Java window | **6** | 5 | 3 |
| pyjab, the same window | **601** | 513 | 12 |
| *control: UI Automation, the whole desktop* | *147* | *99* | *finished* |

See `docs/PLAN.md` §2.3.2. The control is what makes the six mean anything.

## Where to read first

| file | what |
|---|---|
| `AGENTS.md` | the working agreement — read before changing anything |
| `docs/PLAN.md` | the full plan: product, architecture, milestones, risks |
| `docs/DESIGN.md` | the three decisions that shape M1, with the arguments |
| `docs/ARCHITECTURE.md` | how the server is put together, and the constraints that shape it |
| `CHANGELOG.md` | what changed, and why |

## Requirements

* Windows
* Python 3.10 or newer
* A JVM on the machine, for the application being driven
* `pyjab`, which brings the rest

## Install

Not published yet.

```console
pip install -e ".[dev]"
```

## Use

Not published yet, so the client config is a path rather than a package name:

```json
{
  "mcpServers": {
    "pyjab-mcp": { "command": "python", "args": ["-m", "pyjab_mcp"] }
  }
}
```

Start with `java_diagnostics`. It reports which part of the environment is missing —
Java Access Bridge not enabled, no bridge DLL, a 32-bit interpreter against a 64-bit JVM —
instead of leaving a tool call to fail with a locator error.
