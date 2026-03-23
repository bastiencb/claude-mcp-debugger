# claude-mcp-debugger

[Français](README.fr.md) | [Español](README.es.md)

An MCP debug server for AI coding agents. Debug **Python, Node.js, Java, and browser JavaScript** — like a developer in VS Code.

Works with any [MCP](https://modelcontextprotocol.io/)-compatible AI agent — optimized for **Claude Code** with one-command install.

> **Any AI agent, any language, no IDE.** This server speaks the standard Model Context Protocol — it works with Claude Code, but also with any MCP client (Cursor, Windsurf, custom agents, CI pipelines). No VS Code, no IDE, no GUI required.

<p align="center">
<img src="assets/browser-debug-demo.gif" alt="Real-time browser debugging demo">
</p>

## Supported languages

| Language | Adapter | Auto-setup | Requirements |
|----------|---------|------------|--------------|
| Python | [debugpy](https://github.com/microsoft/debugpy) | `pip install` on first use | Python 3.10+ |
| Node.js | [vscode-js-debug](https://github.com/microsoft/vscode-js-debug) | Downloaded on first use | Node.js 18+ |
| Java | [JDT LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls) + [java-debug](https://github.com/microsoft/java-debug) | Downloaded on first use (~55 MB) | JDK 17+ |
| Browser JS | vscode-js-debug (pwa-chrome) | Shared with Node.js | Chrome/Chromium |

## Features

- **22 debug tools**: full debugging lifecycle — launch, breakpoints, stepping, inspection, variable modification, and more
- **Multi-language**: Python, Node.js, Java, and browser JavaScript through a unified interface
- **Browser debugging**: debug client-side JS in Chrome/Chromium — set breakpoints, catch click handlers, inspect the DOM. Works with local dev servers and remote URLs
- **Standalone**: no IDE required — works headless, in CI/CD, anywhere an MCP client runs
- **Auto-setup**: all adapters and dependencies are downloaded automatically on first use
- **Smart detection**: auto-detects language from file extension, finds project venvs (Python), compiles with debug info (Java)
- **Advanced breakpoints**: conditional, hit count, logpoints, and function breakpoints
- **Variable expansion**: drill into dicts, lists, and objects with configurable depth and internal filtering
- **Live modification**: change variable values mid-execution
- **Exception details**: automatic traceback display when stopped on exceptions
- **Cross-platform**: works on Linux, macOS, and Windows

## Install

### Claude Code (recommended)

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.ps1 | iex
```

> If you get an execution policy error, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` first.

> **What does this do?** The script copies the server to `~/.claude/mcp_debugger/` and adds an entry to your Claude Code MCP config. That's it — review the [install.sh](install.sh) / [install.ps1](install.ps1) source before running.

**After installing, restart Claude Code.** The debugger will be available in all your projects.

<details>
<summary><b>Claude Code — manual install</b></summary>

**1. Copy files:**

Linux / macOS:
```bash
git clone https://github.com/bastiencb/claude-mcp-debugger.git
cp -r claude-mcp-debugger/mcp_debugger ~/.claude/mcp_debugger
```

Windows (PowerShell):
```powershell
git clone https://github.com/bastiencb/claude-mcp-debugger.git
Copy-Item -Recurse claude-mcp-debugger\mcp_debugger $env:USERPROFILE\.claude\mcp_debugger
```

**2. Create venv and install dependencies:**

Linux / macOS:
```bash
python3 -m venv ~/.claude/mcp_debugger/.venv
~/.claude/mcp_debugger/.venv/bin/python3 -m pip install "mcp[cli]>=1.0" debugpy
```

Windows (PowerShell):
```powershell
python -m venv $env:USERPROFILE\.claude\mcp_debugger\.venv
& $env:USERPROFILE\.claude\mcp_debugger\.venv\Scripts\python.exe -m pip install "mcp[cli]>=1.0" debugpy
```

**3. Register with Claude Code:**

Linux / macOS:
```bash
claude mcp add -s user -t stdio debugger -- ~/.claude/mcp_debugger/.venv/bin/python3 -m mcp_debugger
```

Windows (PowerShell):
```powershell
claude mcp add -s user -t stdio debugger -- $env:USERPROFILE\.claude\mcp_debugger\.venv\Scripts\python.exe -m mcp_debugger
```

> This writes to `~/.claude.json` (the Claude Code config). You can verify with `claude mcp list`.

Then restart Claude Code.

</details>

<details>
<summary><b>Other MCP clients (Cursor, Windsurf, custom agents...)</b></summary>

Clone the repository anywhere and point your MCP client to the server:

```bash
git clone https://github.com/bastiencb/claude-mcp-debugger.git /path/to/claude-mcp-debugger
```

Add to your client's MCP configuration:

```json
{
  "command": "python3",
  "args": ["-m", "mcp_debugger"],
  "cwd": "/path/to/claude-mcp-debugger",
  "env": { "PYTHONPATH": "/path/to/claude-mcp-debugger" }
}
```

The server exposes 22 tools prefixed with `debug_` — any MCP client can use them.

</details>

### Requirements

- Python 3.10+ (required — the server itself is Python)
- Node.js 18+ (optional, for JavaScript/TypeScript debugging)
- JDK 17+ (optional, for Java debugging)
- Chrome or Chromium (optional, for browser JavaScript debugging)

## Usage

Once installed, your AI agent can debug code. These examples show Claude Code, but the same tools work identically from any MCP client.

**Python:**
```
You: Debug my script app.py — it crashes on line 42

Claude: [uses debug_launch to start app.py]
        [sets breakpoint at line 42]
        [continues execution]
        [inspects variables when breakpoint hits]
        [finds the bug and explains it]
```

**Node.js:**
```
You: Debug server.js — the /api/users endpoint returns wrong data

Claude: [uses debug_launch with language="node" on server.js]
        [sets breakpoint in the route handler]
        [inspects request and response objects]
        [identifies the bug in the query logic]
```

**Java:**
```
You: Debug Main.java — the sort algorithm produces wrong output

Claude: [uses debug_launch on Main.java — auto-compiles with javac -g]
        [sets breakpoint in the sort method]
        [inspects array contents and loop variables]
        [evaluates expressions: names.size(), scores.get("Alice")]
```

**Browser (Chrome):**
```
You: Debug my frontend — the form validation fails on submit

Claude: [uses debug_launch on http://localhost:3000]
        [sets breakpoint in validator.js]
        ... you click "Submit" in Chrome ...
        [catches the click, inspects form data and validation errors]
        [finds the bug in the validation logic]
```

## Tools

| Tool | Description |
|------|-------------|
| **Session** | |
| `debug_launch` | Launch a program under the debugger (Python, Node.js, Java, browser) |
| `debug_stop` | Stop the session immediately (SIGTERM) |
| `debug_terminate` | Graceful termination (KeyboardInterrupt, cleanup handlers run) |
| `debug_status` | Check session state, location, and capabilities |
| **Breakpoints** | |
| `debug_set_breakpoints` | Set breakpoints with conditions, hit counts, or logpoints |
| `debug_set_function_breakpoints` | Break when a named function is called |
| `debug_set_exception_breakpoints` | Break on raised/uncaught exceptions |
| **Execution** | |
| `debug_pause` | Pause a running thread (e.g., stuck in a loop) |
| `debug_continue` | Resume until next breakpoint or end |
| `debug_step_over` | Execute current line, stop at next |
| `debug_step_into` | Enter the function call on current line |
| `debug_step_out` | Run until current function returns |
| `debug_goto` | Jump to a line without executing intermediate code |
| **Inspection** | |
| `debug_stacktrace` | Get the call stack |
| `debug_variables` | Inspect local/global variables (with expandable markers) |
| `debug_expand_variable` | Drill into dicts, lists, objects |
| `debug_evaluate` | Evaluate an expression in context |
| `debug_exception_info` | Get exception type, message, and traceback |
| `debug_source_context` | Show source code around current line |
| `debug_modules` | List loaded modules |
| **Modification** | |
| `debug_set_variable` | Change a variable's value mid-execution |
| **Output** | |
| `debug_output` | Get captured stdout/stderr (subprocess and/or DAP events) |

## How it works

**Python:**
1. `debug_launch` starts your script under [debugpy](https://github.com/microsoft/debugpy) in `--wait-for-client` mode
2. The MCP server connects as a DAP client over TCP
3. `stop_on_entry` is simulated by setting a breakpoint at the first executable line (AST-based detection)

**Node.js:**
1. `debug_launch` starts [vscode-js-debug](https://github.com/microsoft/vscode-js-debug) as a DAP server
2. The adapter launches your script and manages multi-session debugging (parent + child sessions)
3. `stop_on_entry` is handled natively by js-debug

**Java:**
1. `debug_launch` auto-compiles your `.java` file with `javac -g` (debug info)
2. [Eclipse JDT LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls) starts headless with the [java-debug](https://github.com/microsoft/java-debug) plugin
3. The launcher communicates via LSP to resolve the main class, classpath, and start a DAP session
4. Expression evaluation is fully supported (JDT LS compiles expressions on the fly)

**Browser (Chrome):**
1. `debug_launch` starts vscode-js-debug in `pwa-chrome` mode
2. Chrome opens the target URL (local or remote)
3. Set breakpoints by filename (e.g., `app.js`) — resolved automatically against loaded scripts
4. User interacts with the page, debugger catches breakpoints in real time

All four modes share the same DAP client and MCP tool interface — the experience is identical.

## Architecture

```
mcp_debugger/
├── __init__.py              # Package metadata
├── __main__.py              # Entry point with auto-venv setup
├── server.py                # MCP server — 22 debug tools
├── session.py               # Language-agnostic session lifecycle
├── dap_client.py            # DAP protocol client (multi-session support)
└── launchers/
    ├── base.py              # BaseLauncher ABC + LaunchResult
    ├── python_launcher.py   # debugpy integration
    ├── node_launcher.py     # vscode-js-debug (pwa-node)
    ├── browser_launcher.py  # vscode-js-debug (pwa-chrome)
    ├── java_launcher.py     # JDT LS + java-debug
    └── lsp_client.py        # LSP/JSON-RPC client for JDT LS
```

Adding a new language requires only a new launcher — the DAP client, session manager, and MCP tools are fully reusable.

## License

MIT
