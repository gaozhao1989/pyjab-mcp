# Changelog

All notable changes to pyjab-mcp are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

Releases are immutable: a published version is never edited. Anything still in flight is
under **Unreleased** until it is tagged.

## Unreleased

### Fixed

* **A snapshot could print `children=3` with nothing under it and give no reason.** pyjab reports
  an element's **total** child count while a walk yields the children the tree *exposes*, so a
  collapsed node or an unselected tab legitimately shows fewer children than it reports — pyjab's
  own finding, [pyjab#173](https://github.com/gaozhao1989/pyjab/issues/173). Four situations then
  looked identical: children that are not visible, a walk stopped by its limit, a bug in this
  renderer, or a read that failed.

  `java_snapshot` now marks the difference where it exists — `children=3 (+2)`, on the node and on
  the window line — with one legend explaining that the first number is the element's own total.
  When the walk was also cut short the legend says so, because the gap then has two possible
  causes and the reply must not pick one for the reader. Both appear **only when the `children`
  field is printed**, so a default snapshot does not grow a paragraph it cannot use.

  This project's own `only_interactive` filter is deliberately outside the comparison: a container
  dropped from the *rendering* was still walked, and counting it as a gap would blame the
  application for our choice. Six tests cover the marker, the legend, the truncated variant, the
  filter case, and the ordinary case where nothing is added.

### Added
## [0.1.1] - 2026-10-10

**A patch release, because the MCP Registry registers a PyPI package by reading a line out of
its README — and 0.1.0's README did not have it.** PyPI had already published and a published
version's metadata cannot be edited, so registering 0.1.0 was not possible: the registry
answered `PyPI package 'pyjab-mcp' ownership validation failed. The server name
'io.github.gaozhao1989/pyjab-mcp' must appear as 'mcp-name: io.github.gaozhao1989/pyjab-mcp' in
the package README`. This is pyjab's rule for it: if anything reached PyPI, cut a patch release
rather than move a tag.

### Fixed

* **The registry publish failed at 0.1.0, after PyPI had already published.** The `registry`
  job ran `python -m pytest` in a job that never installed Python — `No module named pytest`,
  exit 1, and the registry validator never even ran. The package went live and its metadata did
  not, and there was no way to publish the metadata alone: re-running the tag's workflow replays
  the *tag's* workflow file, and a new tag would mean a new PyPI release.

  The metadata publish now lives in its own workflow, callable from `release.yml` and
  dispatchable by hand (`gh workflow run publish-registry.yml -f version=0.1.0`), which is the
  repair path — and the likely one, given the registry's own warning that its data may be reset.
  The Python check moved to where Python is installed; the registry's validator stays where the
  registry is.

  Two things this says about the release path, and both are now written down rather than
  remembered: a job that publishes should run only the checks it can actually run, and the
  registry runs *after* PyPI because it points at a package that has to exist — so its
  failures arrive when they can no longer be fixed by re-releasing.

* **`pyjab-mcp --help` prints nothing and exits 0.** The console script takes no arguments, so
  `--help` starts the stdio server, reads EOF and stops. It had been written into the after-tag
  confirmation step in `docs/RELEASING.md`, where it looked like a check and was not one; the
  step now installs the published wheel and lists its tool surface, which is a check. The
  missing `--help`/`--version` is worth adding and is not a release rule.

## [0.1.0] - 2026-10-10

**The first release.** 15 MCP tools that let an agent read and drive a Java desktop
application through the JVM's own accessibility tree: windows, a bounded snapshot with
handles, element search and inspection, text, tables, clicking, typing, selection, waiting
and screenshots — with every action verified against the element the snapshot recorded before
anything is clicked or typed into.

Requires Windows, a JDK with Java Access Bridge enabled, and `pyjab>=1.10.0`. The one tool
that cannot work is `java_send_keys`: pyjab has no public API for a key sequence, so it
reports that requirement and the way round it rather than pretending `send_text` can express
`alt+y` (tracked as gaozhao1989/pyjab#168).

### Added

* **M9: the release pipeline, and the registry validator that caught a real mistake.**
  `.github/workflows/release.yml` is tag-driven: `verify` (the version guard, the suite, every
  guard, a real build and `twine check`) runs before anything is uploaded; then PyPI via OIDC
  trusted publishing with no token in the repository, then the MCP Registry, then the GitHub
  release with the changelog section as its notes. `workflow_dispatch` rehearses the whole
  verify job without publishing.

  `tools/check_release_version.py` refuses a release whose tag, package version, `server.json`
  and changelog do not agree — the four places that name a release, checked before the one
  action that cannot be undone. It also writes the release notes, so the notes and the check
  cannot disagree about what a release contains.

  **The registry's validator rejected the first `server.json`, and it was right.** The published
  JSON schema at `2025-07-09` spells the package fields `registry_type`/`runtime_hint` and
  mentions no other version; the registry API required `registryType`, and treated the
  deprecated `$schema` as a failure to publish. Had this surfaced during a real release it would
  have been discovered *after* PyPI had the package, which cannot be undone. The file now follows
  the current schema (`2025-12-11`), the `registry` job runs `mcp-publisher validate` before
  `publish`, and `PYJAB_MCP_REGISTRY=1` runs the same validator from the test suite. The static
  schema, a published changelog and memory all disagreed with the authority; only the authority
  was right.

  `docs/RELEASING.md` has the procedure and the two things that must exist before the first tag:
  a PyPI pending publisher for `pyjab-mcp` (workflow `release.yml`, environment `pypi`) and the
  registry's GitHub namespace verification.

* **pyjab 1.9.0 is the floor, and M2 is built on it.** The release shipped the tree walk
  (`JABElement.walk` → `JABTree` with a truncation flag) and `JABElement.as_record()`, which
  is what the precheck called R3 and what `java_snapshot` needed. The pin moved to
  `pyjab>=1.9.0` in the same change that started using them, and each API's `released_in` in
  `tools/check_pyjab_api_surface.py` was verified against the 1.9.0 wheel and the 1.8.0 sdist
  rather than remembered.

* **`java_snapshot`: a bounded, handle-carrying view of the tree.** `depth`, `max_nodes` and
  `only_interactive` bound both the walk and the view; when either is cut short the reply
  says so and names the way to narrow it, because a truncated view that does not admit it is
  indistinguishable from a window with less in it. The default budget is asserted under
  4,000 estimated tokens in the tests — a release criterion, not a hope.

* **Element handles that hold no JAB references (D2).** A handle is an opaque id mapped to
  `{session, locator, expected identity}`; nothing is held between calls, the element is
  re-found with `find_element_by_xpath`, checked against what the snapshot recorded (role,
  name, position among siblings, bounds), and released immediately — in a `finally`, because
  the check runs between taking the reference and giving it back. The reference-counting fake
  in `tests/_fakedriver.py` fails the tests if either half of that is wrong.

  Two consequences, both deliberate: eviction releases nothing (there is nothing to release,
  which is the whole argument for this design over a table of live references), and an element
  whose position the application does not report gets **no handle** rather than one that might
  resolve to something else.

* **`java_list_windows` works, through the documented `pyjab-inspect windows --json`.** pyjab
  1.9.0 has the function this needs (`inspector.bridge_without_window`) and does not publish
  it — it is not in that module's `__all__`. The CLI built on it *is* public, so the listing
  runs through that, in a subprocess, and R1 stays recorded. A cost is stated rather than
  hidden: a listing costs a process, not a bridge call.

* **The pyjab drift mechanism (AGENTS.md §1a).** `tools/check_pyjab_changelog.py` fails while
  a pyjab changelog line marked `Affects: pyjab-mcp` has no entry in `acknowledged.yaml`, and
  fails for an entry whose marker has gone — so the answer cannot outlive the question.
  `tests/contract/test_pyjab_contract.py` pins the three shapes this project actually reads
  (the walk, `as_record()`'s fields, the pump), and `.github/workflows/pyjab-drift.yml` runs
  the whole suite against `pyjab@master` weekly on `windows-latest`, which catches drift
  before a release rather than after. `acknowledged.yaml` records the four pre-convention
  commits as unenforced context, because calling them acknowledgements would be a claim the
  check cannot verify.


* **Guards — five checks that make the project's rules mechanical instead of remembered.**
  Modelled on pyjab's `tools/check_*.py`, and on its reasoning: a rule kept by hand is kept
  until somebody is in a hurry. Each prints a report, exits non-zero on a violation, and is
  pinned by `tests/test_guards.py`, which proves it **can** fail — a checker whose failure
  path has never run will not run the day it matters.

  * `tools/check_pyjab_api_surface.py` — every pyjab name this package touches must be
    recorded in `SURFACE` with its status and the release that has it. An unrecorded
    touchpoint, a private name, a stale entry, a public entry with no version, and above all
    **an import of an unreleased API** each fail the build. This is the M1 precheck's lesson
    (the checkout is not the wheel) made a build failure. It found three things on its first
    run, one of which was this project reading `pyjab.__version__`: the version now comes
    from distribution metadata, because the dunder was a touchpoint no rule allowed.
  * `tools/check_documented_surface.py` — the tool table and the module map in
    `docs/ARCHITECTURE.md` must match what `create_server()` registers and what is on disk.
    The page gained a module map and a `ships` column so that both directions are checkable:
    a tool documented as available but not registered, one registered but documented as a
    later milestone, or a module missing from the map all fail.
  * `tools/check_local_only_files.py` — one list of local-only paths checked against both
    mechanisms that keep them out, with `git check-ignore` as the behaviour rather than
    `.gitignore` as the claim.
  * `tools/check_dist_contents.py` — the artifact rather than the rules: a built sdist must
    contain no local-only file and must contain the package, the tests and the guards; the
    wheel must contain only the package, no stale `build/` copies, and an `entry_points.txt`
    naming the console script.
  * `tools/check_pyjab_readiness.py` — the wait for a pyjab release, made re-checkable. It
    asks the installed pyjab the same questions the code asks, reports `READY` /
    `NOT READY` per requirement, and exits non-zero under `--require` while something is
    missing. `UNKNOWN` counts as not ready: "I could not measure it" is not "it is there".

* **A `guards` job and a `package` job in CI.** The guards run as separate steps so a
  failure names the rule it broke; the package job builds the sdist and wheel, runs
  `twine check`, checks the artifact with `check_dist_contents.py`, and installs the wheel
  into a clean environment to prove the console script reaches real code. `build` and
  `twine` are declared in `[project.optional-dependencies].dev` rather than installed in a
  workflow step, which is how pyjab's own workflows drifted apart once.

* **`java_diagnostics` now reports a fourth pyjab API fact, `non-destructive detach`.** The
  R2 gap was recorded in prose and then in `REQUIREMENTS`; the report now also measures it,
  so "what is pyjab still missing" has one answer in one place.

* **The M1 precheck, run against the installed wheel rather than the sibling checkout.**
  Plan §11 gated M1 on answering "is pyjab's public API enough?", and the answer needed
  care: the sibling checkout is 62 commits past the `v1.8.0` tag, so `JABElement.walk`,
  `JABTree`, `pyjab.inspector` and `Win32Utils.java_process_image_paths` all *appear* to
  exist while **none of them is in any released version**. The audit, the two gaps it
  found, and what each blocks are recorded in `AGENTS.md` §6 and encoded as
  `REQUIREMENTS` in `src/pyjab_mcp/backend.py` so the documentation and the message a user
  sees cannot drift apart.

* **`BridgeThread` (`src/pyjab_mcp/bridge.py`) — one thread owns the bridge.** Java Access
  Bridge delivers accessibility events to the thread that called `Windows_run()`, and that
  thread must service its own message queue; a pump on another thread fails by never seeing
  a window that opens later, with no exception. The class constructs the backend on its own
  thread, pumps while idle, drains a work queue, and hands callers a `Future` (or an
  awaitable, so the event loop is never blocked by a cross-process call). A backend that
  cannot be opened is reported as a state: queued work is released with the reason and
  later calls fail fast instead of hanging. `close()` reports whether the thread actually
  joined, because a thread blocked inside a JAB call cannot be cancelled.

* **`tests/_fakebackend.py`, and the L1 suite around it.** The fake records the thread ident
  of every call and raises when it is not the owner — the only way to test the
  thread-affinity rule without a Windows desktop — and it also raises on use after close,
  so a test that leaks a call fails here rather than on a user's machine.

* **`java_diagnostics`.** Reports the platform, interpreter, installed pyjab, which of its
  public APIs are present, whether Java Access Bridge is enabled, where the bridge DLL is,
  and the state of the bridge thread. Every row carries a status, and a row that could not
  be measured reads `UNAVAILABLE (reason)` — never `0`, never "missing". It never writes to
  the environment: in particular it does not construct pyjab's `Service`, whose constructor
  writes `~/.accessibility.properties`.

* **`java_list_windows` and `java_attach_window`.** Wired end to end and exercised by tests
  over a real stdio handshake. On released pyjab they report the missing public API (R1) as
  an actionable error rather than reaching for a private one; `java_attach_window` holds no
  JAB reference, because pyjab has no non-destructive detach (R2) and holding one would
  either leak a Java object per attach or kill the user's application when the session ends.

* **Tests for the console script over the real stdio transport.** It catches the seam
  between the package and the client — a module that cannot be executed, a crash before the
  first request, or a stray `print` corrupting the protocol stream on stdout.

* **Local agent configuration, deliberately not published.** `.agents/skills/` holds four
  skills that reference pyjab (the dependency rules and the R1/R2/R3 gaps, the
  bridge-threading procedure, the test layers, and this machine's setup). They take effect
  from the working tree without being versioned; `.gitignore` keeps them out of git and
  `MANIFEST.in` prunes them from the sdist.


* **L3 in CI: a real window, on a real desktop, on a schedule.** `tests/e2e/` is skipped
  unless `PYJAB_MCP_E2E=1`; `.github/workflows/windows-gui.yml` sets it on a hosted
  `windows-latest` runner, which has an interactive desktop rather than being a service in
  session 0. The application is pyjab's own test app, compiled from the sdist of the
  *installed* pyjab — one version, both sides — and fetched by `tools/fetch_test_app.py`,
  which is a script rather than shell in a workflow for the usual reason.

  Three things it says, in order of how much it would hurt to get wrong: a snapshot of a real
  accessibility tree contains the controls the application builds; a handle resolves and
  verifies against the real element it was taken from; and **a window that opens after the
  executor has been idle is still seen** — the D3 trap, which no L1 test can catch because
  the fake would have to be wrong in the same way the real thing is.

* **`java_diagnostics` checked on JDK 8, 17 and 21.** The same workflow runs a matrix of the
  three, with `tools/verify_diagnostics_jdk.py` asserting not just that a bridge DLL was found
  but that it is the one **paired with that JDK** (JDK 8 keeps it under `jre\bin`, 11+ under
  `bin`). A mismatched pair fails as "pyjab cannot see my window", which is the hardest
  failure in this project to tell apart from a bridge that is simply not enabled.


* **M3: `java_find`, `java_get_element` and `java_get_text`.** A snapshot shows the shape of a
  window; these ask questions about it.

  `java_find` takes exactly one selector — `name`, `role`, `description`, `states` or `xpath`
  (so substrings via pyjab's `contains()`) — and returns a handle per match. A match is only
  given a handle if a locator finds *it* again: the candidates are tried in order, most
  survivable first, and each is resolved and compared before it is kept. A match that no
  locator can pin down is listed **without** a handle, because D2 is explicit that a handle
  which might point at something else is worse than no handle. Every element the search hands
  over is released, including the ones past the result limit — the shape of the leak pyjab's
  own walk had to be fixed for.

  `java_get_element` reports one element in full and says whether it is still the element it
  was; `java_get_text` reads its text, slices it client-side (pyjab's only public text API
  returns the whole string), and reports how much of the whole it carries. An element with no
  text interface says so, which is a different answer from an empty string and is reported as
  one.


* **M4: `java_get_table`.** A Java table read through the JVM's own `AccessibleTable`
  interface — row and column counts, then cell text — which is the capability a platform
  bridge does not carry at all.

  It never returns the whole table: a page is bounded by `row_start`, `row_count` and a
  `max_cells` budget, and the reply says what it left out and how to ask for it. Three cell
  states are kept apart all the way to the reply, because collapsing them would invent data:
  text, `-` for a cell with no Accessible Text interface, and `?` for a cell whose read
  raised, with a count — "this cell has no text" and "I could not read this cell" are
  different answers (AGENTS.md §2.4). Every cell is released as it is read, one at a time; a
  200-row table read without that is 200 Java objects left behind.

  Writing it turned up two bugs in itself, both caught by the tests it came with: the page
  size and the table's height shared a name, so asking for one row returned the whole table,
  and the per-cell budget check was dead code because the row count had already been reduced
  to fit it.


* **M5: the actions — `java_click`, `java_set_text`, `java_select`, `java_fill_form`.** Each
  one verifies the element and then acts **on the element it verified**, inside one element
  lifetime, so there is no window between the check and the action in which the tree could
  move. When the element has changed, nothing happens and the difference is reported: the
  click that would have landed on whatever moved into the position is a message instead.

  `java_click` maps left/double/right onto pyjab's accessibility action, mouse
  double-click and right-click, with `simulate=True` for real input where the action is
  ignored — the trade pyjab documents (window comes to the front, needs real geometry, cannot
  work from a service session). `java_set_text` waits for the field to report the value, so a
  following read sees what was typed. `java_select` picks its shape from the arguments: a
  text option for a combo box, list or tab, or a row or cell for a table, which goes through
  the table's accessible selection because that is the path applications respond to.
  `java_fill_form` is one call for several fields, and it does **not** stop at the first
  failure: a half-filled form with a report is worth more than one without.

* **`java_send_keys` is registered and answers with the requirement it is waiting on (R4).**
  Every key helper pyjab has is private (`Win32Utils._press_key`, `_send_keys`, ...) and
  `send_text` types text rather than a modifier chord, so `alt+y` has no public
  implementation. Driving the private helpers would mean depending on names nobody promised,
  in the one area where a wrong key does something irreversible. The reply names the
  requirement **and** the way round it: `java_set_text` for text, or the menu item that
  exposes the same action, found with `java_find(role="menu item")` and clicked.


* **M8: client setup and the boundary of what this can do.** `docs/CLIENTS.md` has the
  configuration for Claude Desktop, Claude Code, Cursor and VS Code — the file each one reads,
  the JSON shape, and the two calls to make first. It leads with the failure that accounts for
  most setups: a client starts the server without your `PATH`, so a virtual environment's
  `pyjab-mcp` is "not found" until the absolute path is used.

  A README section states what this **cannot** do, because that is the half people discover
  the hard way: canvas-drawn interfaces (no accessible children at all), Java inside a browser,
  applets in a plugin host, non-Java applications, and anything pixel-based. `java_screenshot`
  will show a picture of a canvas; a picture is not something you can act on, and the section
  says so.

  **`server.json`** for the MCP Registry, with a test that checks it against the fields the
  registry's schema actually requires — it uses snake_case (`registry_type`, `runtime_hint`),
  which is not what an earlier draft of that schema said, so it was read rather than
  remembered — and that the version it names is the release the package builds. A registry
  entry pointing at a version that does not exist is worse than no entry.

  A **Support** section, as `docs/PLAN.md` F2/F5 planned: free support through GitHub issues,
  commercial and enterprise support through the address this package already publishes in its
  metadata. The open-source package stays complete — no licence key, no quota, nothing held
  back — which the section says outright.

### Changed

* **The pin is `pyjab>=1.10.0`, and R1 and R2 are retired.** 1.10.0 added
  `pyjab.list_java_windows()` — dicts with `hwnd`, `title` and `pid` — and
  `JABDriver.detach()`, which releases the root element's JAB reference and forgets the window
  **without terminating its process**. Those were the two gaps this project waited through M1
  and M2 for: the listing no longer needs pyjab's CLI in a subprocess, and a session can be
  released without killing the application it was observing.

  `PyjabBackend.close()` now detaches every window instead of dropping the dict, and attaching
  a window that is already bound releases the earlier binding rather than holding a second
  context for it. The CLI listing stays as the fallback for an install below the pin — a floor
  can be worked around — and nothing supported reaches it.

  **1.10.0 was published while this project's CI was running.** The readiness check reported it
  `READY` and a test that asserted the previous world failed in the same run; both were right.
  It is the drift machinery doing what it was built for, and the reason the pin, the registry
  and the readiness check are all verified rather than remembered: each `released_in` here was
  checked against the installed wheel, and the 1.8.0 and 1.9.0 sdists for what came earlier.

* **`java_diagnostics` reports a Windows-only API call off Windows as unavailable**, rather
  than letting pyjab's descriptive `ImportError` cross the tool boundary: the library is
  importable everywhere and only *runs* on Windows, and that is an environment answer.


* **D2's verification is split into what blocks and what is reported, because a real run showed
  why it has to be.** The first end-to-end run through the tools set a text field to `"hello"`,
  then could not read it back: the edit had *renamed* the element. `JTextField("1122233455")`
  with no `setName` reports the text as its accessible name — Swing's
  `AccessibleJTextComponent.getName()` falls back to the content — and D2 as written compared
  the name, so an edit invalidated the handle that made it.

  Blocking now: role, position among siblings, and bounds when both sides report them.
  Reported: the name, **for the four roles whose name is their content** (`text`,
  `password text`, `label`, `table cell`). For every other role a name change still blocks,
  because a renamed button at the same position is a different button — which is the case the
  check exists for. The reply says which of the two happened.

* **A handle from `java_find` is now built from a walk path rather than from a name.**
  Same evidence, other half: a locator containing `[@name='1122233455']` stops resolving the
  moment the field's content changes, so the handle could not read back what it had written.
  Search results are placed against one bounded walk of the window and given the same
  position-based locators a snapshot produces; only when the walk cannot place an element does
  it fall back to a name locator, which resolves now but may not survive the next edit. One
  walk serves every result in the call.


* **R3 is retired, R1 and R2 are restated against 1.9.0** (`AGENTS.md` §6, and the
  `REQUIREMENTS` tuple in `backend.py` that the tools' error messages come from). A
  requirement that outlives its release is a lie the code tells in its own messages.

* **`tools/check_pyjab_readiness.py` distinguishes a release question from a pin question.**
  The tree walk is answered by the pin (`pyjab>=1.9.0` requires the release that has it), not
  by a probe that cannot run off Windows; R1 and R2 are still release questions and still
  report `NOT READY`.

* **The API-surface guard follows a driver passed as an argument.** Extracting the walk and
  the re-resolution into functions that take the driver — which is what makes the ownership
  rule testable off Windows — moved ten touchpoints out of the analyzer's reach until they
  were declared in `DRIVER_PARAMETERS`. The guard noticed the moment they changed, which is
  the property that matters: it fails loudly rather than silently checking less.


* **The MCP SDK floor is `mcp>=2.0`.** The 2.x line renamed `FastMCP` to
  `mcp.server.mcpserver.MCPServer` and `mcp.server.fastmcp` now raises an `ImportError`
  explaining the move, so the decorator shape and the import path are both 2.x. `mcp>=1.0.0`
  was a claim the code did not honour.

* **`pyjab` stays at `>=1.8.0` for M1, and that is deliberate.** M1's tools do not call the
  tree-walk API; M2 raises the floor to `>=1.9.0` in the same change that first calls it,
  because a floor that permits a version without the API is a lie.

* **`README.md` state is now "M1 shipped, blocked on a pyjab release"** rather than
  "pre-alpha skeleton", and the honest boundary is stated: no tool here has yet driven a
  live Java application.

* **`backend.probe_public_api()` reads pyjab's version from distribution metadata.** It used
  `pyjab.__version__`, a dunder in somebody else's package — which the API-surface guard
  refused to record, which is the guard doing its job on its first run. The version it
  reports is therefore the *installed* one, which is the question diagnostics is asking.

* **`docs/ARCHITECTURE.md` gained a module map and a `ships` column** in the tool table, so
  that both directions of "the page and the code agree" are checkable, and `README.md`
  gained a "Working on it" section listing the guards.

### Fixed

* **A real table's cells report no text, and M4 was reading `None` for all of them.** L3
  found it: the table's shape came through (4 columns, ≥5 rows) and every cell read as
  `None`. Swing's `AccessibleJTableCell` puts the cell's *value* in its accessible **name**,
  so `java_get_table` now falls back to the name when there is no AccessibleText — and says
  which cells came from where, because a value should never be presented as something it is
  not. Without this, a whole table of real values would have been reported as unknown.

* **The handle path called a method ``JABDriver`` does not have.** `resolve_driver` released
  the re-resolved element with `driver.release_jabelement(element)`; the method belongs to
  **`JABElement`**, and `JABDriver` has never had one. It would have raised `AttributeError`
  inside the `finally` of every resolution on Windows — the first real desktop run was the
  first time that line executed.

  Two things had to be true for this to survive the L1 suite, and both were: the adapter
  guessed, and `tests/_fakedriver.py` was shaped like the guess, so the fake agreed with the
  mistake instead of with pyjab. The fake now puts the release on the element, where pyjab
  puts it, and `tests/contract/` asserts the API the adapter actually calls — including the
  signature that lets an element release itself.

* **The diagnostics job treated "no Java windows" as a failure.** `pyjab-inspect windows`
  exits 1 when JAB sees nothing, and on a bare runner that is the expected answer rather than
  an error — `UNAVAILABLE` is not `0`, and "nothing is there" is a measurement (AGENTS.md
  §2.4). The step reported it and failed; it now reports it and carries on, so the assertion
  behind it actually runs.

* **The L3 snapshot walked three levels and the controls are seven deep.** The first real run
  found the frame, the layered panes, the menu bar and "Main panel" — and nothing inside it,
  so the two assertions about a button and a table failed. The test walks 12 now, and says why:
  a snapshot's default depth is a budget for an agent, not a fact about the tree.
