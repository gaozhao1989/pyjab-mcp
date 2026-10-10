# pyjab-mcp

An MCP server that lets an AI agent drive **Java desktop applications** — Swing, AWT,
JavaFX — through [pyjab](https://pypi.org/project/pyjab/), which reads the JVM's own
accessibility tree via Java Access Bridge.

**It never modifies, restarts or injects into the target application.** That constraint is
the point: launch parameters, classpaths and dependency JARs stay exactly as they are,
which is what makes this usable against a production Java client that you cannot change.

## Status

**M1 and M2 are implemented; nothing here has driven a live Java application yet.**
`java_diagnostics`, `java_list_windows`, `java_attach_window` and `java_snapshot` exist and
are covered by the L1 suite plus a real stdio handshake, but **no tool has yet been run
against a real window** — that needs a Windows desktop, and the end-to-end run is a separate
step. It needs `pyjab>=1.9.0`, which is the first release with the tree walk a snapshot is
built on.

Two gaps in pyjab are recorded rather than worked around, and both are visible in
`java_diagnostics` (`AGENTS.md` §6.2):

* **no public API lists Java windows**, so `java_list_windows` runs through pyjab's
  documented `pyjab-inspect windows --json` in a subprocess — a public interface, at the cost
  of a process per listing;
* **there is no non-destructive detach**, so a bound window stays bound for the life of the
  process. The server holds one driver per attached window, never one per call, and never
  calls `JABDriver.__exit__`, which would terminate the application.

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

## Working on it

```console
python -m pytest tests/                    # the portable suite, any platform
python tools/check_pyjab_api_surface.py    # every pyjab call is public, released, recorded
python tools/check_documented_surface.py   # this README's tool list and the code agree
python tools/check_local_only_files.py     # maintainer notes stay unpublished
python tools/check_pyjab_readiness.py      # what pyjab still owes this project
python tools/check_pyjab_changelog.py      # every marked pyjab change has an answer here
python -m pytest tests/contract/           # the pyjab shapes this project depends on
python -m build && python tools/check_dist_contents.py --dist dist
```

The guards are the project's rules made mechanical, in the same shape pyjab uses for its own:
a registry of what is allowed, checked in both directions, so adding a call site fails until
somebody writes down why it is acceptable. `AGENTS.md` §8 lists them and what each one
enforces.
