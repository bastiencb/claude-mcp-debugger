"""MCP Server exposing DAP debugging tools.

This server lets any MCP client debug programs (Python, Node.js, Java, browser JS)
like a developer in VS Code: set breakpoints, step through code, inspect variables,
evaluate expressions.

All tool outputs return structured JSON for programmatic parsing by AI agents.
"""

import json
import logging
from pathlib import Path
from typing import Any

from .session import DebugSession, get_session, reset_session

logger = logging.getLogger(__name__)

# debugpy virtual groups that contain __dunder__ methods and builtins
_INTERNAL_GROUPS = frozenset({"special variables", "function variables"})

# Lazy import — mcp may not be installed
_mcp = None


def _get_mcp():
    global _mcp
    if _mcp is None:
        try:
            from mcp.server.fastmcp import FastMCP
            _mcp = FastMCP
        except ImportError:
            raise ImportError(
                "MCP SDK not installed. Install it with: pip install mcp"
            )
    return _mcp


# ── Display templates (injected into tool descriptions) ────────
# Appended to tool descriptions so the agent sees them at every call.

# Common to ALL styles: always show file, line, and function context.
_ALL_STYLES_CONTEXT = (
    "\n\n--- All display styles ---\n"
    "ALWAYS include in your output: file name, line number, and function name.\n"
    "Style 1 (Tables): file:line in func() header, then markdown table | Var | Type | Value |.\n"
    "Style 2 (Raw JSON): just the indented JSON — it already contains location.\n"
    "Style 3 (Concise): one line with file:line, func, and key variable values.\n"
    "  Example: 'Stopped at app.py:42 in main() | data={...}, total=274'\n"
    "Style 4 (Rich): see template below."
)

# IMPORTANT: "MUST" and "NEVER" are used intentionally to force compliance.
# Agents tend to skip formatting when batching multiple tool calls.
# Each template is a MANDATORY output format — no exceptions, no shortcuts.

_RICH_STOP = (
    _ALL_STYLES_CONTEXT +
    "\n\n--- Rich display (style 4) ---\n"
    "You MUST format EVERY call to this tool using this EXACT structure. NEVER skip it.\n"
    "  {icon} **`{tool_name}`** @ `{file}:{line}` in `{function}`\n"
    "  {human comment: what happened and where}\n"
    "  ```{lang from file extension}\n"
    "    NN │ previous line\n"
    "  → NN │ current line\n"
    "    NN │ next line\n"
    "  ```\n"
    "  {human comment: what the data means}\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```\n"
    "Icons: ● breakpoint, → step, ✕ exception, ■ stop.\n"
    "Include stacktrace (#0 func file:line) only when multiple frames."
)

_RICH_VARS = (
    _ALL_STYLES_CONTEXT +
    "\n\n--- Rich display (style 4) ---\n"
    "You MUST format EVERY call to this tool using this EXACT structure. NEVER skip it.\n"
    "  **`debug_variables`** @ `{file}:{line}` in `{function}`\n"
    "  {human comment: what scope and context}\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```"
)

_RICH_EVAL = (
    _ALL_STYLES_CONTEXT +
    "\n\n--- Rich display (style 4) ---\n"
    "You MUST format EVERY call to this tool using this EXACT structure. NEVER skip it.\n"
    "  **`debug_evaluate`** @ `{file}:{line}` in `{function}`\n"
    "  `{expression}` = `{value}` ({type})\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```"
)

_RICH_EXPAND = (
    _ALL_STYLES_CONTEXT +
    "\n\n--- Rich display (style 4) ---\n"
    "You MUST format EVERY call to this tool using this EXACT structure. NEVER skip it.\n"
    "  **`debug_expand_variable`** @ `{file}:{line}` in `{function}`\n"
    "  {human comment: what is being expanded}\n"
    "  Tree view with ├── └── │ connectors\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```"
)


_RICH_BREAKPOINTS = (
    "\n\n--- All display styles ---\n"
    "You MUST format EVERY call to this tool. NEVER silently consume the result.\n"
    "Style 1 (Tables): markdown table | Line | Verified | Condition |.\n"
    "Style 2 (Raw JSON): just the indented JSON.\n"
    "Style 3 (Concise): 'Breakpoints set at file:line1, line2 (verified: yes/no)'.\n"
    "Style 4 (Rich):\n"
    "  ● **`debug_set_breakpoints`** @ `{file}`\n"
    "  Breakpoint(s) set at line(s) {lines} — {human comment: why here}\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```"
)

_RICH_SIMPLE = (
    "\n\n--- All display styles ---\n"
    "You MUST format EVERY call to this tool. NEVER silently consume the result.\n"
    "Style 4 (Rich):\n"
    "  {icon} **`{tool_name}`** @ `{file}:{line}` in `{function}`\n"
    "  {human comment}\n"
    "  ```json\n"
    "  {full JSON output, indented}\n"
    "  ```\n"
    "Icons: ◆ launch, ◇ status, ■ stop/terminate."
)


def create_server() -> Any:
    """Create and configure the MCP server with all debug tools."""
    FastMCP = _get_mcp()
    mcp = FastMCP(
        "debugger",
        instructions=(
            "Debug programs like a VS Code developer. "
            "Supports Python (.py), Node.js (.js/.ts), Java (.java), "
            "and browser JavaScript (URLs).\n\n"
            "FIRST USE SETUP — MANDATORY before the first debug call:\n"
            "You MUST call the AskUserQuestion tool (NOT print text) to ask TWO questions.\n"
            "DO NOT skip this. DO NOT ask via text. USE the AskUserQuestion tool.\n\n"
            "Question 1 — call AskUserQuestion with:\n"
            "  question: 'How should debug results be displayed?'\n"
            "  options: ['1. Tables and trees', '2. Raw JSON', '3. Concise summary', "
            "'4. Rich display (recommended)']\n\n"
            "Question 2 — call AskUserQuestion with:\n"
            "  question: 'Which debug mode?'\n"
            "  options: ['1. Full auto (recommended)', '2. Interactive']\n\n"
            "Remember both choices for the entire session.\n\n"
            "OUTPUT RULES:\n"
            "- All tools return structured JSON. Always unwrap the MCP {\"result\":\"...\"} wrapper.\n"
            "- Always include file:line in context (from JSON 'location' field, or retained "
            "from last stop event for tools that don't return location).\n"
            "- EVERY tool call MUST produce formatted output. NEVER silently consume a result.\n"
            "- Each tool's description contains the EXACT display template. "
            "Follow it precisely for EVERY call — it is MANDATORY, not optional.\n\n"
            "FULL AUTO MODE:\n"
            "- Python/Node/Java: use wait=True (blocking) for continue/step commands.\n"
            "- Browser: debug_continue(wait=False), then poll debug_wait_for_event(timeout=10) "
            "in a loop. Print status between calls ('Waiting for interaction... 20s'). "
            "When breakpoint fires, inspect and report automatically, then resume.\n\n"
            "INTERACTIVE MODE:\n"
            "- debug_continue(wait=False), tell user what to do, check debug_status when signaled.\n"
            "- If still running, poll debug_wait_for_event(timeout=10) with status messages."
        ),
    )

    # ── Session lifecycle ──────────────────────────────────────

    @mcp.tool(description="Launch a program under the debugger.\n\n"
        "Args:\n"
        "    program: Path to the source file to debug (.py, .js, .ts, .java), or a URL (http://...) for browser debugging.\n"
        "    args: Command-line arguments for the program.\n"
        "    cwd: Working directory (defaults to script's directory). For browser mode, used as webRoot for source maps.\n"
        "    stop_on_entry: Pause at the first executable line (default: True).\n"
        "    port: DAP port (default: 5679).\n"
        "    language: Debug language: 'python', 'node', 'browser', 'java'. Auto-detected from extension/URL if omitted.\n"
        "    python_path: Path to Python interpreter (Python only). Auto-detects project .venv if omitted.\n"
        "    browser_path: Path to Chrome/Chromium (browser only). Auto-detects if omitted.\n"
        "    headless: Run browser in headless mode (browser only, default: False).\n"
        "    java_home: Path to JDK home (Java only). Auto-detects from JAVA_HOME or PATH if omitted.\n\n"
        "Returns:\n"
        "    JSON: {host, port, program, pid, language-specific keys (python/java/adapter),\n"
        "           stopped_at?, reason?} or {error}.\n" + _RICH_SIMPLE)
    async def debug_launch(
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        stop_on_entry: bool = True,
        port: int = 5679,
        language: str | None = None,
        python_path: str | None = None,
        browser_path: str | None = None,
        headless: bool = False,
        java_home: str | None = None,
    ) -> str:
        """Launch a program under the debugger."""
        session = get_session()
        try:
            info = await session.start(
                program=program,
                args=args,
                cwd=cwd,
                stop_on_entry=stop_on_entry,
                port=port,
                language=language,
                python_path=python_path,
                browser_path=browser_path,
                headless=headless,
                java_home=java_home,
            )
            if stop_on_entry:
                stop_info = await session.client.wait_for_stop(timeout=10.0)
                frames = await session.client.get_stack_trace()
                info["stopped_at"] = _format_location(frames[0]) if frames else "unknown"
                info["reason"] = stop_info.get("reason", "entry")
            return json.dumps(info, indent=2)
        except Exception as e:
            logger.error("Failed to launch debug session: %s", e)
            return json.dumps({"error": str(e)})

    @mcp.tool(description="Stop the current debug session immediately (SIGTERM) and return program output.\n\nReturns:\n    JSON: {stopped: true, output: string|null}." + _RICH_SIMPLE)
    async def debug_stop() -> str:
        """Stop the current debug session immediately (SIGTERM)."""
        output = await reset_session()
        return json.dumps({"stopped": True, "output": output or None})

    @mcp.tool(description="Gracefully terminate (KeyboardInterrupt). Cleanup handlers run. Falls back to SIGTERM after 3s.\n\nReturns:\n    JSON: {terminated: true, output: string|null}." + _RICH_SIMPLE)
    async def debug_terminate() -> str:
        """Gracefully terminate the debugged program."""
        session = get_session()
        _require_active(session)

        output = await session.terminate()
        return json.dumps({"terminated": True, "output": output or None})

    @mcp.tool(description="Check if a debug session is active and get current state.\n\nReturns:\n    JSON: {active, program, threads: [{id, name}], current_location, capabilities: [string]}\n    or {error} if no session." + _RICH_SIMPLE)
    async def debug_status() -> str:
        """Check if a debug session is active and get current state."""
        session = get_session()
        if not session.is_active:
            return json.dumps({"error": "No active debug session. Use debug_launch first."})

        info: dict[str, Any] = {"active": True, "program": session.program}

        try:
            threads = await session.client.get_threads()
            info["threads"] = [{"id": t["id"], "name": t.get("name", "")} for t in threads]

            frames = await session.client.get_stack_trace()
            if frames:
                info["current_location"] = _format_location(frames[0])
        except Exception as e:
            info["error"] = str(e)

        # Include supported capabilities
        caps = session.capabilities
        supported = sorted(k for k, v in caps.items() if isinstance(v, bool) and v)
        if supported:
            info["capabilities"] = supported

        return json.dumps(info, indent=2)

    # ── Breakpoints ────────────────────────────────────────────

    @mcp.tool(description="Set breakpoints in a source file.\n\nArgs:\n"
        "    file: Path to the source file, or filename for browser scripts (e.g. 'app.js').\n"
        "    lines: Line numbers where breakpoints should be set.\n"
        "    conditions: Optional conditions per line, e.g. {\"17\": \"n > 10\"}.\n"
        "    hit_conditions: Optional hit counts per line, e.g. {\"17\": \"5\"}.\n"
        "    log_messages: Optional log messages per line, e.g. {\"17\": \"x = {x}\"}.\n\n"
        "Returns:\n    JSON: {file, breakpoints: [{line, verified, condition?, hit_condition?, log_message?}]}." + _RICH_BREAKPOINTS)
    async def debug_set_breakpoints(
        file: str,
        lines: list[int],
        conditions: dict[str, str] | None = None,
        hit_conditions: dict[str, str] | None = None,
        log_messages: dict[str, str] | None = None,
    ) -> str:
        """Set breakpoints in a source file."""
        session = get_session()
        _require_active(session)

        # Convert string keys to int keys for the DAP client
        int_conditions = {int(k): v for k, v in conditions.items()} if conditions else None
        int_hit_conditions = {int(k): v for k, v in hit_conditions.items()} if hit_conditions else None
        int_log_messages = {int(k): v for k, v in log_messages.items()} if log_messages else None

        # Try local file first (Python, Node.js), then loaded sources (browser)
        file_path = str(Path(file).resolve())
        if Path(file_path).is_file():
            breakpoints = await session.client.set_breakpoints(
                file_path, lines, int_conditions, int_hit_conditions, int_log_messages
            )
        else:
            # No local file — resolve via loaded sources (browser scripts)
            source_obj = await session.client.resolve_source(file)
            if source_obj:
                breakpoints = await session.client.set_breakpoints_by_source(
                    source_obj, lines, int_conditions, int_hit_conditions, int_log_messages
                )
                file_path = source_obj.get("name", file)
            else:
                return json.dumps({"error": f"File not found locally or in loaded sources: {file}"})

        result_list = []
        for bp in breakpoints:
            entry = {"line": bp.get("line"), "verified": bp.get("verified", False)}
            if int_conditions and int(bp.get("line", 0)) in int_conditions:
                entry["condition"] = int_conditions[int(bp["line"])]
            if int_hit_conditions and int(bp.get("line", 0)) in int_hit_conditions:
                entry["hit_condition"] = int_hit_conditions[int(bp["line"])]
            if int_log_messages and int(bp.get("line", 0)) in int_log_messages:
                entry["log_message"] = int_log_messages[int(bp["line"])]
            result_list.append(entry)
        return json.dumps({"file": file_path, "breakpoints": result_list})

    @mcp.tool(description="Set breakpoints on function names. Breaks when the function is called.\n\nArgs:\n    functions: Function names (e.g. [\"my_function\", \"MyClass.method\"]).\n    conditions: Optional conditions per function.\n\nReturns:\n    JSON: {breakpoints: [{function, verified, line?, condition?}]}." + _RICH_BREAKPOINTS)
    async def debug_set_function_breakpoints(
        functions: list[str],
        conditions: dict[str, str] | None = None,
    ) -> str:
        """Set breakpoints on function names."""
        session = get_session()
        _require_active(session)

        breakpoints = await session.client.set_function_breakpoints(functions, conditions)
        result_list = []
        for i, bp in enumerate(breakpoints):
            fn_name = functions[i] if i < len(functions) else "?"
            entry = {"function": fn_name, "verified": bp.get("verified", False)}
            if bp.get("line"):
                entry["line"] = bp["line"]
            if conditions and fn_name in conditions:
                entry["condition"] = conditions[fn_name]
            result_list.append(entry)
        return json.dumps({"breakpoints": result_list})

    @mcp.tool(description="Break on exceptions.\n\nArgs:\n    filters: 'raised' (all) or 'uncaught' (unhandled only). Default: ['uncaught'].\n\nReturns:\n    JSON: {filters: [string]}." + _RICH_BREAKPOINTS)
    async def debug_set_exception_breakpoints(
        filters: list[str] | None = None,
    ) -> str:
        """Break on exceptions."""
        session = get_session()
        _require_active(session)
        await session.client.set_exception_breakpoints(filters)
        return json.dumps({"filters": filters or ["uncaught"]})

    # ── Execution control ──────────────────────────────────────

    @mcp.tool(description="Pause a running thread. Use when the program is running (e.g., stuck in a loop).\n\nArgs:\n    thread_id: Thread to pause (default: 1).\n\nReturns:\n    JSON {reason, location, source_context, locals}." + _RICH_STOP)
    async def debug_pause(thread_id: int = 1) -> str:
        """Pause a running thread."""
        session = get_session()
        _require_active(session)

        await session.client.pause(thread_id)
        return await _wait_and_report(session)

    @mcp.tool(description="Resume execution until next breakpoint, exception, or program end.\n\nArgs:\n    wait: If True (default), block until stopped. If False, return immediately (browser/interactive).\n\nReturns:\n    wait=True: JSON {reason, location, source_context, locals, exception?}.\n    wait=False: JSON {resumed: true, message}." + _RICH_STOP)
    async def debug_continue(wait: bool = True) -> str:
        """Resume execution until next breakpoint, exception, or program end."""
        session = get_session()
        _require_active(session)

        await session.client.continue_execution()
        if not wait:
            return json.dumps({"resumed": True, "message": "Execution resumed. Use debug_status to check state or debug_wait_for_event to wait for next breakpoint."})
        return await _wait_and_report(session)

    @mcp.tool(description="Step over: execute the current line and stop at the next one.\n\nArgs:\n    wait: If True (default), block until stopped. If False, return immediately.\n\nReturns:\n    JSON {reason, location, source_context, locals}." + _RICH_STOP)
    async def debug_step_over(wait: bool = True) -> str:
        """Step over: execute the current line and stop at the next one."""
        session = get_session()
        _require_active(session)

        await session.client.next_step()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step over initiated."})
        return await _wait_and_report(session)

    @mcp.tool(description="Step into: enter the function call on the current line.\n\nArgs:\n    wait: If True (default), block until stopped. If False, return immediately.\n\nReturns:\n    JSON {reason, location, source_context, locals}." + _RICH_STOP)
    async def debug_step_into(wait: bool = True) -> str:
        """Step into: enter the function call on the current line."""
        session = get_session()
        _require_active(session)

        await session.client.step_in()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step into initiated."})
        return await _wait_and_report(session)

    @mcp.tool(description="Step out: run until the current function returns.\n\nArgs:\n    wait: If True (default), block until stopped. If False, return immediately.\n\nReturns:\n    JSON {reason, location, source_context, locals}." + _RICH_STOP)
    async def debug_step_out(wait: bool = True) -> str:
        """Step out: run until the current function returns."""
        session = get_session()
        _require_active(session)

        await session.client.step_out()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step out initiated."})
        return await _wait_and_report(session)

    @mcp.tool(description="Wait for the debugger to stop (breakpoint, exception, or termination).\n\nUse after debug_continue(wait=False) in browser/interactive debugging.\n\nArgs:\n    timeout: Maximum seconds to wait (default: 300).\n\nReturns:\n    JSON {reason, location, source_context, locals}.\n    If timeout: {running: true, message}." + _RICH_STOP)
    async def debug_wait_for_event(timeout: float = 300.0) -> str:
        """Wait for the debugger to stop. Use after debug_continue(wait=False)."""
        session = get_session()
        _require_active(session)
        return await _wait_and_report(session, timeout=timeout)

    # ── Inspection ─────────────────────────────────────────────

    @mcp.tool(description="Get the current call stack.\n\nArgs:\n    thread_id: Thread to inspect (default: 1).\n\nReturns:\n    JSON: {frames: [{index, file, line, function, current: bool}]} or {error}." + _RICH_SIMPLE)
    async def debug_stacktrace(thread_id: int = 1) -> str:
        """Get the current call stack."""
        session = get_session()
        _require_active(session)

        frames = await session.client.get_stack_trace(thread_id)
        if not frames:
            return json.dumps({"error": "No stack frames available."})

        result_frames = []
        for i, frame in enumerate(frames):
            source = frame.get("source", {})
            result_frames.append({
                "index": i,
                "file": source.get("path", "<unknown>"),
                "line": frame.get("line"),
                "function": frame.get("name", "<module>"),
                "current": i == 0,
            })
        return json.dumps({"frames": result_frames})

    @mcp.tool(description="Inspect variables in the current scope.\n\nArgs:\n    scope: 'local' or 'global' (default: 'local').\n    thread_id: Thread to inspect (default: 1).\n    frame_index: Stack frame index, 0 = current.\n\nReturns:\n    JSON: {scope, location, variables: [{name, type, value, ref?}]}.\n    Variables with ref > 0 can be expanded with debug_expand_variable." + _RICH_VARS)
    async def debug_variables(
        scope: str = "local",
        thread_id: int = 1,
        frame_index: int = 0,
    ) -> str:
        """Inspect variables in the current scope."""
        session = get_session()
        _require_active(session)

        frames = await session.client.get_stack_trace(thread_id)
        if frame_index >= len(frames):
            return json.dumps({"error": f"frame_index {frame_index} out of range (stack depth: {len(frames)})"})

        frame = frames[frame_index]
        scopes = await session.client.get_scopes(frame["id"])

        target_scope = None
        scope_lower = scope.lower()
        for s in scopes:
            name = s.get("name", "").lower()
            if scope_lower in name:
                target_scope = s
                break

        if not target_scope:
            available = [s.get("name", "?") for s in scopes]
            return json.dumps({"error": f"Scope '{scope}' not found. Available: {available}"})

        variables = await session.client.get_variables(target_scope["variablesReference"])

        var_list = []
        for var in variables:
            name = var.get("name", "")
            if name.startswith("__") and name.endswith("__"):
                continue
            entry = {"name": name, "type": var.get("type", ""), "value": var.get("value", "")}
            var_ref = var.get("variablesReference", 0)
            if var_ref > 0:
                entry["ref"] = var_ref
            var_list.append(entry)
        return json.dumps({
            "scope": target_scope["name"],
            "location": _format_location(frame),
            "variables": var_list,
        })

    @mcp.tool(description="Evaluate an expression in the debugger context.\n\nArgs:\n    expression: Expression to evaluate (e.g., 'len(my_list)', 'x + y').\n    frame_index: Stack frame for context (0 = current).\n    thread_id: Thread to use (default: 1).\n\nReturns:\n    JSON: {expression, type, value, ref?}." + _RICH_EVAL)
    async def debug_evaluate(
        expression: str,
        frame_index: int = 0,
        thread_id: int = 1,
    ) -> str:
        """Evaluate an expression in the debugger context."""
        session = get_session()
        _require_active(session)

        frame_id = None
        if frame_index is not None:
            frames = await session.client.get_stack_trace(thread_id)
            if frame_index < len(frames):
                frame_id = frames[frame_index]["id"]

        result = await session.client.evaluate(expression, frame_id=frame_id)
        value = result.get("result", "")
        vtype = result.get("type", "")
        var_ref = result.get("variablesReference", 0)

        result_dict = {"expression": expression, "type": vtype, "value": value}
        if var_ref > 0:
            result_dict["ref"] = var_ref
        return json.dumps(result_dict)

    @mcp.tool(description="Modify a variable's value during debugging.\n\nArgs:\n    name: Variable name to modify.\n    value: New value as expression (e.g., \"42\", '\"hello\"', \"[1,2,3]\").\n    scope: 'local' or 'global' (default: 'local').\n\nReturns:\n    JSON: {name, value, type} or {error}." + _RICH_EVAL)
    async def debug_set_variable(
        name: str,
        value: str,
        scope: str = "local",
        thread_id: int = 1,
        frame_index: int = 0,
    ) -> str:
        """Modify a variable's value during debugging."""
        session = get_session()
        _require_active(session)

        scope_ref = await _resolve_scope_reference(session, scope, thread_id, frame_index)
        if scope_ref is None:
            return json.dumps({"error": f"Scope '{scope}' not found."})

        try:
            result = await session.client.set_variable(scope_ref, name, value)
            new_value = result.get("value", value)
            new_type = result.get("type", "")
            return json.dumps({"name": name, "value": new_value, "type": new_type})
        except Exception as e:
            return json.dumps({"error": f"setting {name}: {e}"})

    @mcp.tool(description="Jump to a specific line without executing intermediate code.\n\nArgs:\n    line: Target line number.\n    file: Source file path (defaults to current file).\n    thread_id: Thread to use (default: 1).\n\nReturns:\n    JSON {reason, location, source_context, locals}." + _RICH_STOP)
    async def debug_goto(
        line: int,
        file: str | None = None,
        thread_id: int = 1,
    ) -> str:
        """Jump to a specific line without executing intermediate code.
        """
        session = get_session()
        _require_active(session)

        # Check capability
        if not session.capabilities.get("supportsGotoTargetsRequest"):
            return json.dumps({"error": "goto is not supported by the debug adapter."})

        # Determine source file
        if not file:
            frames = await session.client.get_stack_trace(thread_id)
            if not frames:
                return json.dumps({"error": "No stack frames available."})
            file = frames[0].get("source", {}).get("path", "")
            if not file:
                return json.dumps({"error": "Could not determine current source file."})

        file_path = str(Path(file).resolve())

        # Get goto targets for the line
        targets = await session.client.goto_targets(file_path, line)
        if not targets:
            return json.dumps({"error": f"No goto target available at line {line} in {file_path}."})

        # Jump to the first target
        target = targets[0]
        await session.client.goto(thread_id, target["id"])
        return await _wait_and_report(session)

    @mcp.tool(description="Show source code around the current execution point.\n\nArgs:\n    lines_before: Lines before current (default: 5).\n    lines_after: Lines after current (default: 5).\n\nReturns:\n    JSON: {file, current_line, lines: [{number, text, current: bool}]}." + _RICH_SIMPLE)
    async def debug_source_context(lines_before: int = 5, lines_after: int = 5) -> str:
        """Show source code around the current execution point."""
        session = get_session()
        _require_active(session)

        frames = await session.client.get_stack_trace()
        if not frames:
            return json.dumps({"error": "No stack frames available."})

        frame = frames[0]
        source = frame.get("source", {})
        file_path = source.get("path", "")
        current_line = frame.get("line", 0)

        if not file_path or not Path(file_path).is_file():
            return json.dumps({"error": f"Source not available for: {file_path}"})

        start = max(1, current_line - lines_before)
        end = current_line + lines_after

        with open(file_path) as f:
            all_lines = f.readlines()

        source_lines = []
        for i in range(start - 1, min(end, len(all_lines))):
            line_num = i + 1
            source_lines.append({
                "number": line_num,
                "text": all_lines[i].rstrip(),
                "current": line_num == current_line,
            })
        return json.dumps({"file": file_path, "current_line": current_line, "lines": source_lines})

    @mcp.tool(description="Get the program's stdout/stderr output.\n\nArgs:\n    last_n: Last N lines (default: 50, None = all).\n    source: 'subprocess', 'dap', or 'all' (default).\n\nReturns:\n    JSON: {output: string|null}." + _RICH_SIMPLE)
    async def debug_output(last_n: int | None = 50, source: str = "all") -> str:
        """Get the program's stdout/stderr output."""
        session = get_session()
        parts = []

        if source in ("subprocess", "all"):
            sub_output = session.get_output(last_n)
            if sub_output:
                parts.append(sub_output)

        if source in ("dap", "all"):
            dap_events = session.client.drain_output_events()
            dap_lines = []
            for evt in dap_events:
                text = evt.get("output", "").rstrip("\n")
                if text:
                    category = evt.get("category", "stdout")
                    if category == "stderr":
                        dap_lines.append(f"[stderr] {text}")
                    elif category != "telemetry":
                        dap_lines.append(text)
            if dap_lines:
                if last_n:
                    dap_lines = dap_lines[-last_n:]
                parts.append("\n".join(dap_lines))

        text = "\n".join(parts) if parts else None
        return json.dumps({"output": text})

    @mcp.tool(description="Get details about the current exception when stopped on one.\n\nArgs:\n    thread_id: Thread to inspect (default: 1).\n\nReturns:\n    JSON: {exception, message: string|null, traceback: string|null} or {error}." + _RICH_SIMPLE)
    async def debug_exception_info(thread_id: int = 1) -> str:
        """Get details about the current exception."""
        session = get_session()
        _require_active(session)

        try:
            info = await session.client.exception_info(thread_id)
        except Exception as e:
            return json.dumps({"error": str(e)})

        exc_id = info.get("exceptionId", "Unknown")
        description = info.get("description", "")
        details = info.get("details", {})
        traceback_str = details.get("stackTrace", "")

        return json.dumps({
            "exception": exc_id,
            "message": description or None,
            "traceback": traceback_str or None,
        })

    @mcp.tool(description="Expand a complex variable (dict, list, object) to see its contents.\n\nUse the ref=N value from debug_variables or debug_evaluate.\n\nArgs:\n    variables_reference: The reference ID from the 'ref' field.\n    max_depth: Levels of nesting (default: 1). 1=children, 2-3=most needs, 4+=deep.\n    skip_internals: Filter out __dunder__ and builtins (default: True).\n\nReturns:\n    JSON: {children: [{name, type, value, ref?, children?: [...]}]}." + _RICH_EXPAND)
    async def debug_expand_variable(
        variables_reference: int, max_depth: int = 1, skip_internals: bool = True,
    ) -> str:
        """Expand a complex variable to see its contents."""
        session = get_session()
        _require_active(session)

        # No hard cap — circular references are caught by the visited set

        async def _expand(ref: int, depth: int, visited: set[int]) -> list[dict]:
            if ref in visited:
                return [{"name": "...", "value": "(circular reference)"}]
            if depth > max_depth:
                return [{"name": "...", "value": "(max depth reached)"}]
            visited.add(ref)

            variables = await session.client.get_variables(ref)
            result = []
            for var in variables:
                name = var.get("name", "")
                if skip_internals and name in _INTERNAL_GROUPS:
                    continue

                entry = {"name": name, "type": var.get("type", ""), "value": var.get("value", "")}
                var_ref = var.get("variablesReference", 0)
                if var_ref > 0:
                    entry["ref"] = var_ref
                    if depth < max_depth:
                        entry["children"] = await _expand(var_ref, depth + 1, visited)
                result.append(entry)
            return result

        try:
            children = await _expand(variables_reference, 1, set())
            return json.dumps({"children": children})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(description="List loaded modules.\n\nArgs:\n    filter: Optional substring to filter module names.\n\nReturns:\n    JSON: {modules: [{name, path}], count} or {error}." + _RICH_SIMPLE)
    async def debug_modules(filter: str | None = None) -> str:
        """List loaded modules."""
        session = get_session()
        _require_active(session)

        try:
            modules = await session.client.modules()
        except Exception as e:
            return json.dumps({"error": str(e)})

        if filter:
            modules = [m for m in modules if filter.lower() in m.get("name", "").lower()]

        module_list = [{"name": m.get("name", "?"), "path": m.get("path", "")} for m in modules]
        return json.dumps({"modules": module_list, "count": len(module_list)})

    return mcp


# ── Helpers ────────────────────────────────────────────────────


def _require_active(session: DebugSession) -> None:
    """Raise if no active debug session."""
    if not session.is_active:
        raise RuntimeError(
            "No active debug session. Use debug_launch to start one first."
        )


async def _resolve_scope_reference(
    session: DebugSession, scope: str, thread_id: int = 1, frame_index: int = 0
) -> int | None:
    """Find the variablesReference for a given scope name."""
    frames = await session.client.get_stack_trace(thread_id)
    if frame_index >= len(frames):
        return None
    scopes = await session.client.get_scopes(frames[frame_index]["id"])
    scope_lower = scope.lower()
    for s in scopes:
        if scope_lower in s.get("name", "").lower():
            return s["variablesReference"]
    return None


def _format_location(frame: dict) -> str:
    """Format a stack frame as 'file:line in function'."""
    source = frame.get("source", {})
    path = source.get("path", "<unknown>")
    # Shorten path for readability
    try:
        path = str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        pass
    line = frame.get("line", "?")
    name = frame.get("name", "<module>")
    return f"{path}:{line} in {name}"


async def _wait_and_report(session: DebugSession, timeout: float = 30.0) -> str:
    """Wait for debugger to stop and report current location + context as JSON."""
    try:
        stop_info = await session.client.wait_for_stop(timeout=timeout)
    except TimeoutError:
        return json.dumps({"running": True, "message": f"Program did not stop within {timeout}s. It may still be running — use debug_status to check."})

    reason = stop_info.get("reason", "unknown")

    if reason == "terminated":
        output = await session.stop()
        return json.dumps({"terminated": True, "output": output or None})

    frames = await session.client.get_stack_trace()
    if not frames:
        return json.dumps({"reason": reason, "location": None})

    frame = frames[0]
    source = frame.get("source", {})
    file_path = source.get("path", "")
    current_line = frame.get("line", 0)

    location = {
        "file": file_path,
        "line": current_line,
        "function": frame.get("name", "<module>"),
    }

    # Source context
    source_context = []
    if file_path and Path(file_path).is_file():
        with open(file_path) as f:
            all_lines = f.readlines()
        start = max(0, current_line - 3)
        end = min(len(all_lines), current_line + 2)
        for i in range(start, end):
            source_context.append({
                "number": i + 1,
                "text": all_lines[i].rstrip(),
                "current": i + 1 == current_line,
            })

    # Local variables
    local_vars = []
    try:
        vars_dict = await session.client.get_all_variables(frame_index=0)
        for k, v in list(vars_dict.items())[:10]:
            entry = {"name": k, "type": v.get("type", ""), "value": v.get("value", "")}
            if v.get("variablesReference", 0) > 0:
                entry["ref"] = v["variablesReference"]
            local_vars.append(entry)
    except Exception:
        pass

    result = {
        "reason": reason,
        "location": location,
        "source_context": source_context,
        "locals": local_vars,
    }

    # Exception info
    if reason == "exception":
        try:
            exc_info = await session.client.exception_info()
            result["exception"] = {
                "type": exc_info.get("exceptionId", ""),
                "message": exc_info.get("description", ""),
                "traceback": exc_info.get("details", {}).get("stackTrace", ""),
            }
        except Exception:
            pass

    return json.dumps(result)


# ── Entry point ────────────────────────────────────────────────


def run_server(transport: str = "stdio") -> None:
    """Run the MCP debug server."""
    mcp = create_server()
    logger.info("Starting MCP debug server (transport=%s)", transport)
    mcp.run(transport=transport)
