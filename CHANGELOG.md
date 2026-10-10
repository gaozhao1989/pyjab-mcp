# Changelog

All notable changes to pyjab-mcp are documented here, following
[Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/).

Releases are immutable: a published version is never edited. Anything still in flight is
under **Unreleased** until it is tagged.

## Unreleased

### Added

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

### Changed

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
