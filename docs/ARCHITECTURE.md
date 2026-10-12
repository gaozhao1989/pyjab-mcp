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
| `src/pyjab_mcp/backend.py` | the only module that imports pyjab, plus the pyjab requirements it records — none open, and an answered one names the release that did it |
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
| observe | `java_get_table` | M4 |
| act | `java_click`, `java_set_text`, `java_select`, `java_fill_form` | M5 |
| act | `java_send_keys` | M5 — sends a chord where pyjab provides one, probed rather than assumed, and names the release that has it (`JABDriver.send_keys`, pyjab 1.11.0) where it does not |
| act | `java_focus_window` | 0.3.0 — brings a window forward and reports whether it came forward, because "asked for focus" and "got focus" are two different facts (issue #57) |
| wait | `java_wait_for` | M6 |
| observe | `java_screenshot` | M6 |

`java_get_table` is where the substrate matters most: it reads a `JTable`'s rows and cells
through the JVM's own `AccessibleTable` interface, with paging, rather than through a
platform bridge that does not carry that information.

**Status.** M1 to M6 have shipped: `java_diagnostics`, `java_list_windows`,
`java_attach_window`, `java_snapshot`, `java_find`, `java_get_element`, `java_get_text`,
`java_get_table`, `java_click`, `java_set_text`, `java_select`, `java_fill_form`,
`java_send_keys`, and `java_focus_window` — the last one after the plan's milestones, which is
why its `ships` cell names the release rather than a milestone.

Every action verifies first and acts on the element it verified, inside one element lifetime,
so there is no window between the check and the action in which the tree could move. When the
element has changed, **nothing is done** — the difference is reported instead.

**Two answers to "how many children".** pyjab reports an element's **total** child count in
`as_record()`, while a walk yields the children the accessibility tree **exposes** — so a
collapsed node or an unselected tab legitimately reports children the walk never produces.
`java_snapshot` prints the element's own number and marks the difference (`children=3 (+2)`)
with one legend explaining it, because a reply that shows `children=3` above nothing cannot be
told apart from a walk that stopped early, a rendering bug, or a read that failed. This
project's own `only_interactive` filter is deliberately **not** part of that number: a dropped
container was still walked, and blaming the application for our rendering would be a lie in the
other direction.

`java_wait_for` waits for a window to appear or an element's text to change, polling between
bridge calls rather than inside one: the bridge has a single owning thread, so a wait that ran
on it would block every other call for its whole duration. A timeout is reported as a result
rather than an error, because "not yet" is something an agent can act on. The two gaps this project waited on were both answered by pyjab 1.10.0:
`pyjab.list_java_windows()` returns the windows with their handles, titles and pids — so the
listing comes from the bridge, with pyjab's documented `pyjab-inspect windows --json` left as
the fallback for an install below the pin — and `JABDriver.detach()` releases a window
**without terminating its process**, which is what lets a session end without killing the
application it was observing. The last two, R4 and R5, were answered by pyjab 1.11.0:
`JABDriver.send_keys` is the chord primitive `java_send_keys` needs, and `JABDriver.focus()`
(with `Win32Utils.set_window_foreground` alongside it) is the public way to bring a window
forward that R5 was waiting on — which `java_focus_window` now uses, and whose **return value**
it reports, because pyjab answers with whether the window *is* the foreground window rather
than with whether the request was made. Both call sites still probe rather than assume, which is
the same mechanism that made the window listing and `detach()` start working the day 1.10.0
landed: an install below the floor is told which release has the API instead of raising an
`AttributeError`.

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

## Guards

The rules that are enforced rather than remembered. Each prints a report and exits non-zero,
each runs in CI, and each is pinned by a test that proves it can fail — a checker that cannot
report a problem is decoration.

| guard | the rule it makes mechanical |
|---|---|
| `check_pyjab_api_surface.py` | Every pyjab name this project touches is recorded with its public/released status. A private name, an unrecorded touchpoint, or a dependency on something unreleased fails the build. |
| `check_documented_surface.py` | The three lists on this page and the code agree, in both directions: the **file table** (a module on disk with no row, or a row whose module is gone), the **tool table** (a tool documented as shipping but not registered, and the reverse), and the **guards table** (a `tools/check_*.py` with no row, or a row whose script was deleted). |
| `check_local_only_files.py` | The maintainer's own notes are excluded from git **and** from the sdist. `git check-ignore` is the behaviour rather than the claim, because `.gitignore` does not protect an sdist — it is built from the working tree. |
| `check_dist_contents.py` | The artifact, not the rules: a built sdist must contain the package, tests and guards and no maintainer note; a wheel must contain only the package, with a working `entry_points.txt`. |
| `check_published_references.py` | A shipped file may state a rule or link to a public document, but may not send a reader to a maintainer note they cannot open — in the repository or in an installed sdist. |
| `check_changelog_immutable.py` | A released changelog section is published inside its sdist, so editing one afterwards makes this repository describe a release that never happened. |
| `check_release_version.py` | The tag, the package version, `server.json` and the changelog have to agree before anything is uploaded, because a PyPI version cannot be unpublished. It also writes the release notes. |
| `check_release_l3.py` | The release needs a **green L3 run on the commit being tagged**, where L3 is `windows-gui.yml`, which is not a pull-request status check. It refuses on a missing run, a failed one, an unreadable answer, and a conclusion that is anything but success. |
| `check_release_unpublished.py` | **A version already on PyPI cannot be replaced.** Called immediately after the version guard: 404 proceeds, 200 refuses and names both versions, and anything else refuses as unread, because during a release "I could not check" is not "nothing is there". |
| `check_pyjab_changelog.py` | Every pyjab change marked as affecting this project has a written answer here, and no answer outlives its marker. |
| `check_pyjab_readiness.py` | Which pyjab APIs this project's pin expects, made re-checkable: it reports whether each exists yet and whether the declared version resolves it. All five are RESOLVED and `REQUIREMENTS` is empty, so this is a tripwire for the next pin move rather than a list of what is missing. Report-only, because a job that fails until somebody else publishes is a job people learn to ignore. |

Two are not guards but the other half of the same idea: `tests/contract/` pins the *behaviour*
this project reads from pyjab, so a break is caught whether or not anybody wrote it down, and
`.github/workflows/pyjab-drift.yml` runs those tests weekly against pyjab's development branch —
early enough to fix before a release rather than after one.
