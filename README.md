# pyjab-mcp

An MCP server that lets an AI agent drive **Java desktop applications** — Swing, AWT,
JavaFX — through [pyjab](https://pypi.org/project/pyjab/), which reads the JVM's own
accessibility tree via Java Access Bridge.

**It never modifies, restarts or injects into the target application.** That constraint is
the point: launch parameters, classpaths and dependency JARs stay exactly as they are,
which is what makes this usable against a production Java client that you cannot change.

## Status

**Pre-alpha. Nothing here drives an application yet.** This is the skeleton: the working
agreement, the plan, the design decisions, and enough packaging that CI has something to
run.

The one thing that is settled is the question the whole direction rested on. Measured on
a real Java window, through the same `UIAutomationCore` that generic Windows automation
uses:

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
