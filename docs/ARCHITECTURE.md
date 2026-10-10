# Architecture

How the server is put together, and the three constraints that shape it.

## What it is

An MCP server in front of [pyjab](https://pypi.org/project/pyjab/), which reads a Java
desktop application's own accessibility tree through Java Access Bridge.

```
MCP client  ──stdio/JSON-RPC──▶  pyjab-mcp  ──▶  pyjab  ──▶  Java Access Bridge  ──▶  JVM
```

The server **never modifies, restarts or injects into the target application**. Launch
parameters, classpaths and dependency JARs stay as they are.

## Constraint 1 — never return the whole tree

A Java application's accessibility tree can hold thousands of nodes with a dozen
attributes each. Returning one is not slow; it is unusable, because it puts hundreds of
thousands of tokens into a model's context.

So every read is a **bounded view**:

```
java_snapshot(depth=2, max_nodes=120, only_interactive=True,
              fields=["name", "role", "states"])
```

and the reply is one compact line per node rather than nested JSON:

```
[e:7f3a] window "Java Control Panel"
  [e:7f3b] root pane
    [e:8a01] panel "Update"
```

Two things follow that are easy to get wrong:

* **Both limits must be real.** A limit that cannot be reached is worse than none,
  because the signature reads as a guarantee. (This has happened in this project's
  sibling repository: a node cap tested against a list that had just been assigned empty
  fired zero times.)
* **A truncated view must say it was truncated.** A snapshot that silently omits half a
  window is indistinguishable from a window that has half as much in it, and the caller
  will act on it.

## Constraint 2 — element handles do not hold references

An agent sees a snapshot naming elements, then acts on one. The handle has to resolve to
the same element later.

pyjab's currency is a JAB reference, which must be released exactly once. Every object
leak found in pyjab so far came from holding a reference longer than the operation that
took it. So:

**A handle is an opaque id mapped to `{window, locator, expected identity}`. Nothing is
held between calls.** On use the locator is resolved again, and the result is **checked
against what the snapshot recorded** — role, accessible name, index among siblings,
bounds. If they disagree, the tool **does not act**; it reports that the element changed.

That last step is the point. "The tree moved and we clicked the wrong thing" becomes a
message instead of a wrong action.

Two honest costs:

* **a lookup per call.** Acceptable because pyjab's traversal is pruned; a deep path is a
  handful of bridge calls rather than thousands.
* **a locator is not always unique.** An element whose identity cannot be expressed as a
  locator gets a handle that **cannot be used for action**, rather than one that might act
  on the wrong node.

If profiling later shows re-resolution is the bottleneck, the answer is a cache with a
short TTL that **re-verifies on hit** — not a table of live references.

## Constraint 3 — one thread owns the bridge

Java Access Bridge delivers accessibility events — including "a window opened" — to the
thread that called `Windows_run()`. **That thread must service its own message queue**,
and every call into the bridge must come from it. A pump on a different thread from the
calls does not work, and it fails by never seeing a window that opened later, which is
the quietest failure available.

So:

```
one dedicated thread
  ├─ constructs the pyjab driver
  ├─ loops: pump the message queue, drain the work queue
  └─ is the only thread that ever touches the bridge

tool handlers  ──submit a callable, await the result──▶  that thread
```

**A trap worth testing for specifically**: constructing the driver on one thread and
calling it from another *appears to work* — until a dialog opens later and is never
found. Only an end-to-end test on a real desktop can catch it, and the test is "start the
executor, wait, open a dialog, then find it".

## Where the code lives

Every module in `src/pyjab_mcp/` is listed here. That is not decoration: a new module
missing from this table is a signal rather than an oversight, and
`tools/check_documented_surface.py` fails the build until it is added. pyjab keeps the same
rule for its own module list.

| file | what |
|---|---|
| `src/pyjab_mcp/__init__.py` | the version, and the rule that nothing here imports pyjab at module scope |
| `src/pyjab_mcp/__main__.py` | the console script: stdio only, and stdout belongs to the protocol |
| `src/pyjab_mcp/server.py` | the MCP surface — server state, the tool functions, failure messages |
| `src/pyjab_mcp/bridge.py` | `BridgeThread`: the one thread that owns the bridge, its pump and its work queue |
| `src/pyjab_mcp/backend.py` | the only module that imports pyjab, plus the recorded requirements it is waiting on |
| `src/pyjab_mcp/diagnostics.py` | the environment report, where `UNAVAILABLE` is never `0` |
| `src/pyjab_mcp/tree.py` | tree records and the bounded renderer: what a snapshot shows, and what it says it left out |
| `src/pyjab_mcp/handles.py` | element handles — a locator plus an expected identity, holding no JAB reference |
| `src/pyjab_mcp/windows.py` | plain window records, and turning a title or hwnd into exactly one of them |

`tools/` holds the guards and verification scripts. They ship in the sdist and not in the
wheel — the same split pyjab uses, and for the same reason: they are maintainer tools, and
a wheel contains only the package.

## Tool surface

Deliberately small. Mapping pyjab's sixty-odd methods onto sixty-odd tools would dilute
the model's tool selection for no gain. The `ships` column is checked against what
`create_server()` actually registers, so a tool cannot be documented as available when it
is not, or registered without being documented.

| group | tools | ships |
|---|---|---|
| diagnostics | `java_diagnostics` | M1 |
| session | `java_list_windows`, `java_attach_window` | M1 |
| observe | `java_snapshot` | M2 |
| observe | `java_find`, `java_get_element`, `java_get_text` | M3 |
| observe | `java_get_table`, `java_screenshot` | M4, M6 |
| act | `java_click`, `java_set_text`, `java_select`, `java_send_keys`, `java_fill_form` | M5 |
| wait | `java_wait_for` | M6 |

`java_get_table` is where the substrate matters most: it reads a `JTable`'s rows and cells
through the JVM's own `AccessibleTable` interface, with paging, rather than through a
platform bridge that does not carry that information.

**Status.** M1, M2 and M3 have shipped: `java_diagnostics`, `java_list_windows`,
`java_attach_window`, `java_snapshot`, `java_find`, `java_get_element`, `java_get_text`. Two of pyjab's own gaps are still recorded rather
than worked around (`AGENTS.md` §6.2): there is no public API that lists Java windows, so
the listing runs through pyjab's documented `pyjab-inspect windows --json`; and there is no
non-destructive detach, so a bound window stays bound for the life of the process. Neither
is a correctness problem for a server driving one application, and both are the reason a
tool's error message names a requirement instead of a private import.

`java_snapshot` reads a bounded part of the tree — pyjab 1.9.0's `JABElement.walk`, which
releases every JAB reference as it advances — and gives each rendered element a handle.
Nothing is held between calls: a handle is a locator plus the identity the snapshot
recorded, and a later call re-finds the element and checks role, name, position and bounds
before doing anything.

## Verification

| layer | runs where | can it drive a live application? |
|---|---|---|
| L1 pure logic | any platform | no — renders from a fake tree |
| L2 integration | `windows-latest`, default CI | no — packaging, import, `tools/list` |
| L3 end to end | `windows-latest` with a real desktop: `windows-gui.yml`, dispatched by hand **or** weekly | **yes** |

L3 is `tests/e2e/`, which is skipped unless `PYJAB_MCP_E2E=1`, and `windows-gui.yml` also
checks `java_diagnostics` on JDK 8, 17 and 21 — the three JDKs put the bridge DLL in different
places, which is exactly what a report that cannot tell "missing" from "unreadable" gets
wrong. The application it drives is pyjab's own test app, compiled from the sdist of the
installed pyjab, because that is the only Swing application a hosted runner has.

The one test in that file worth knowing about is the last one: it starts a **second JVM after
the executor has been idle** and requires its window to appear. A pump on the wrong thread
passes every other test in the repository and fails that one.

**L1 needs a fake built before the thing it fakes.** pyjab's reference-counting test
bridge raises on a double release and on use after release, and it found two real leaks.
A fake that agrees with anything proves nothing.

**L3 is the only layer that can say a tool call worked against a real window**, and it is
the only layer nobody runs by default. Anything about live behaviour is unverified until
it has run there — and a green default CI run says nothing about it.
