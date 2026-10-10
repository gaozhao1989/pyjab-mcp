# Connecting a client to pyjab-mcp

This server speaks MCP over **stdio**, so any client that can start a command can use it. What
follows is each client's own way of saying that.

> **Checked when this page was written:** 2026-10-10, against the clients' documentation. The
> **command** here is verified by this repository's own tests (a real stdio handshake runs in
> CI on Windows, macOS and Linux); the **file locations and CLI flags belong to the clients**
> and do move between releases — if yours differs, the JSON shape is the part that lasts.

## Before you connect anything

| Requirement | Why, and how to check |
|---|---|
| **Windows 10/11** | Java Access Bridge is a Windows API. `java_diagnostics` reports this as `UNAVAILABLE` elsewhere rather than pretending. |
| **A JDK, Java 8 or later** | The bridge DLL ships with it. Install one and set `JAVA_HOME`. |
| **JAB enabled** | `"%JAVA_HOME%\bin\jabswitch" -enable`, then **restart the Java application**. |
| **A Java application already running** | This server only ever **attaches** to applications it did not launch. Nothing here starts your app. |
| **Python 3.10+** | `pyjab-mcp` supports 3.10–3.13. |

```console
pip install pyjab-mcp
```

Then find the command your client needs. The console script is `pyjab-mcp`; `python -m
pyjab_mcp` is the same server and is useful when a client cannot see your `PATH`:

```console
> where pyjab-mcp                 # Windows: the absolute path to put in a config file
C:\Users\you\.venv\Scripts\pyjab-mcp.exe
> pyjab-mcp --help
```

**Use the absolute path in a client config if the plain name does nothing.** Clients start
processes without your shell's environment, so a server installed into a virtual environment
is frequently "not found" when a bare `pyjab-mcp` is used. This is the single most common
setup failure, and it looks like the client ignoring the server rather than an error.

## Claude Desktop

`claude_desktop_config.json` — Windows: `%APPDATA%\Claude\claude_desktop_config.json`;
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`. (The server itself
only runs on Windows; the macOS path is here because the file also holds servers that do.)

```json
{
  "mcpServers": {
    "pyjab-mcp": {
      "command": "C:\\Users\\you\\.venv\\Scripts\\pyjab-mcp.exe",
      "args": []
    }
  }
}
```

Restart Claude Desktop after editing. **Backslashes must be doubled in JSON**, which is why
the path above has `\\` where Windows uses `\`.

## Claude Code

The CLI writes the config for you, which avoids the path problem entirely:

```console
claude mcp add pyjab-mcp -- pyjab-mcp
claude mcp list
```

Add `--scope project` to put it in a shared `.mcp.json` (checked in, one entry per teammate)
or `--scope user` for every project. The file it writes looks like this:

```json
{
  "mcpServers": {
    "pyjab-mcp": {
      "type": "stdio",
      "command": "pyjab-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

## Cursor

Global: `~/.cursor/mcp.json`. Per project: `.cursor/mcp.json` in the repository root.

```json
{
  "mcpServers": {
    "pyjab-mcp": {
      "command": "C:\\Users\\you\\.venv\\Scripts\\pyjab-mcp.exe",
      "args": []
    }
  }
}
```

## VS Code (Copilot Chat)

`.vscode/mcp.json` — note the key is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "pyjab-mcp": {
      "type": "stdio",
      "command": "C:\\Users\\you\\.venv\\Scripts\\pyjab-mcp.exe"
    }
  }
}
```

## Anything else

A client needs three facts: **command**, **args**, and that the transport is stdio with no
URL. Anything that asks for a JSON server entry takes the `mcpServers` shape above.

## The first two calls to make

1. **`java_diagnostics`** — before anything else. It reports the bridge DLL, whether JAB is
   enabled, whether the pump thread is running, the pyjab version in use, and which pyjab APIs
   are present, each as `OK` / `MISSING` / `DIFFERENT` / `UNAVAILABLE`. `UNAVAILABLE` is not
   the same as `0`, and the report never collapses the two.
2. **`java_list_windows`** — the Java applications the bridge can see. If it is empty, the
   application is either not running, not JAB-enabled, or started before JAB was enabled.

Then `java_attach_window` and `java_snapshot`, and the model has structure with handles.

## When it does not work

| What you see | What it usually is |
|---|---|
| The client shows no `pyjab-mcp` tools at all | The command is not on the client's `PATH`. Use the absolute path from `where pyjab-mcp`. |
| `FileNotFoundError: Java Access Bridge DLL (WindowsAccessBridge-64.dll) could not be located` | No JDK, or `JAVA_HOME` is unset. pyjab's message lists the directories it searched. |
| `java_list_windows` returns nothing, `java_diagnostics` is green | JAB was enabled after the application started, or the app has no accessible top-level window. Restart the application. |
| `the Java Access Bridge is Windows-only` | You are not on Windows. This server cannot work there. |
| `the element has changed since the snapshot` | The window changed since the snapshot. That is the safety rule doing its job — take a fresh snapshot. |
| A tool answers with `pyjab does not provide ...` | A known gap in the dependency, named with the version expected to close it. See `AGENTS.md` §6 for the list. |

Every error a tool returns is meant to name the next step. If one does not, that is a bug
worth reporting.
