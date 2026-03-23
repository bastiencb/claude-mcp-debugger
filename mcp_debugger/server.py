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


def create_server() -> Any:
    """Create and configure the MCP server with all debug tools."""
    FastMCP = _get_mcp()
    mcp = FastMCP(
        "debugger",
        instructions=(
            "Debug programs like a VS Code developer. "
            "Supports Python (.py), Node.js (.js/.ts), Java (.java), "
            "and browser JavaScript (URLs). "
            "Start a session with debug_launch, set breakpoints, "
            "step through code, inspect variables, and evaluate expressions.\n\n"
            "OUTPUT FORMAT: All tools return structured JSON for programmatic parsing. "
            "Each tool's docstring documents its return schema.\n\n"
            "DISPLAY GUIDELINES: When presenting debug results to a user for the first time, "
            "ask their preferred output style among these options:\n\n"
            "1. Formatted tables and trees — variables as aligned tables (Name | Type | Value), "
            "objects as trees (├── └── │), source with → on current line, JSON indented for launch/status.\n\n"
            "2. Raw JSON — the structured JSON output as-is, properly indented.\n\n"
            "3. Concise summary — one-line human context + essential values only "
            "(e.g. 'Stopped at App.java:46 main() | locals: users=ArrayList(4), avg=31').\n\n"
            "4. Rich display (recommended) — human-readable rendering with full JSON at the end. "
            "Structure in order:\n"
            "   a) Human comment about the current action and code location (what happened, where).\n"
            "   b) Stacktrace in classic format (always include when multiple frames, omit for single-frame):\n"
            "      #0  funcName     file:line\n"
            "      #1  caller       file:line\n"
            "   c) Source context as a syntax-highlighted code block with → on the current line:\n"
            "      ```java\n"
            "        45 │ List<User> users = new ArrayList<>();\n"
            "      → 46 │ int avg = computeAverage(users);\n"
            "        47 │ System.out.println(avg);\n"
            "      ```\n"
            "      Use the language from the file extension for syntax highlighting.\n"
            "   d) Human comment about the data (what the variables mean, what to note).\n"
            "   e) The raw JSON output, properly indented in a ```json code block.\n"
            "      This is the full structured JSON — always present, never hidden in <details>.\n\n"
            "   For expand results: use an indented tree with ├── └── │ connectors, "
            "then the JSON below.\n"
            "   For evaluate results: show `expression` = `value` (type), then the JSON below.\n\n"
            "For ALL styles: always include file and line in the context when available "
            "(from the JSON 'location' field, or retained from the last stop event for tools like "
            "debug_expand_variable/debug_evaluate that don't return a location).\n\n"
            "Remember the user's preferences for the rest of the session. "
            "Never show the raw MCP wrapper ({\"result\":\"...\"}) — always unwrap and format.\n\n"
            "IMPORTANT: When asking these questions, prefer using structured question tools "
            "(e.g. AskUserQuestion with clickable options) over free-text prompts, so the user "
            "can simply click their choice instead of typing. If no such tool is available, "
            "present numbered options.\n\n"
            "DEBUG MODE: After asking the display style, also ask the user their preferred "
            "debug mode. This applies to ALL languages (Python, Node.js, Java, browser):\n\n"
            "1. Full auto — the agent drives the entire debug session autonomously. "
            "For scripted programs (Python, Node.js, Java): uses wait=True (blocking) for all "
            "continue/step commands. The agent sets strategic breakpoints, steps through code, "
            "inspects variables, evaluates hypotheses, and reports findings without user intervention.\n"
            "For browser/interactive programs: the agent sets breakpoints, then calls "
            "debug_continue(wait=False) followed immediately by debug_wait_for_event(timeout=300). "
            "This silently waits for the user to interact with the page (click, type, navigate). "
            "When a breakpoint fires, the agent automatically inspects variables, evaluates "
            "expressions, reports findings, and resumes with the same pattern. "
            "The user never needs to signal — the agent detects events automatically.\n"
            "Best for: all debugging — 'find the bug in X', browser debugging, automated analysis.\n\n"
            "2. Interactive — the user explicitly controls the debug flow step by step. "
            "Uses wait=False (non-blocking) for continue commands. The agent sets breakpoints "
            "and tells the user what to do next. The user signals when they have acted, "
            "then the agent checks debug_status and inspects the state. "
            "Best for: learning/teaching, step-by-step walkthroughs where the user wants "
            "to understand each step and decide what to inspect.\n\n"
            "Full auto browser workflow:\n"
            "  1. debug_launch(url) — open Chrome\n"
            "  2. debug_set_breakpoints — set breakpoints in JS files\n"
            "  3. debug_continue(wait=False) — resume execution\n"
            "  4. Poll with debug_wait_for_event(timeout=10) in a loop. Between each call, "
            "print a brief status message so the user knows you are waiting "
            "(e.g. 'Waiting for interaction... (20s)'). This avoids a long silent block "
            "that looks stuck. When the tool returns with a stop reason (not 'running'), "
            "break out of the loop.\n"
            "  5. When breakpoint fires: inspect variables, evaluate, report findings\n"
            "  6. Repeat from step 3\n\n"
            "Interactive mode workflow:\n"
            "  1. debug_launch — start the program\n"
            "  2. debug_set_breakpoints — set breakpoints\n"
            "  3. debug_continue(wait=False) — resume, return immediately\n"
            "  4. Tell the user what to do (click, type, provide input, etc.)\n"
            "  5. When the user signals, call debug_status. "
            "If stopped, inspect. If still running, call debug_wait_for_event(timeout=10) "
            "in a polling loop with status messages.\n"
            "  6. After inspection, debug_continue(wait=False) to resume.\n\n"
            "Default: Full auto for all languages. Suggest Interactive only if the user "
            "explicitly asks for step-by-step control."
        ),
    )

    # ── Session lifecycle ──────────────────────────────────────

    @mcp.tool()
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
        """Launch a program under the debugger.

        Args:
            program: Path to the source file to debug (.py, .js, .ts, .java), or a URL (http://...) for browser debugging.
            args: Command-line arguments for the program.
            cwd: Working directory (defaults to script's directory). For browser mode, used as webRoot for source maps.
            stop_on_entry: Pause at the first executable line (default: True).
            port: DAP port (default: 5679).
            language: Debug language: 'python', 'node', 'browser', 'java'. Auto-detected from extension/URL if omitted.
            python_path: Path to Python interpreter (Python only). Auto-detects project .venv if omitted.
            browser_path: Path to Chrome/Chromium (browser only). Auto-detects if omitted.
            headless: Run browser in headless mode (browser only, default: False).
            java_home: Path to JDK home (Java only). Auto-detects from JAVA_HOME or PATH if omitted.

        Returns:
            JSON: {host, port, program, pid, language-specific keys (python/java/adapter),
                   stopped_at?, reason?} or {error}.
        """
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

    @mcp.tool()
    async def debug_stop() -> str:
        """Stop the current debug session immediately (SIGTERM) and return program output.

        Returns:
            JSON: {stopped: true, output: string|null}.
        """
        output = await reset_session()
        return json.dumps({"stopped": True, "output": output or None})

    @mcp.tool()
    async def debug_terminate() -> str:
        """Gracefully terminate the debugged program (sends KeyboardInterrupt).

        Unlike debug_stop which kills the process immediately, debug_terminate
        lets the program handle cleanup (context managers, finally blocks, atexit).
        Falls back to SIGTERM after 3 seconds.

        Returns:
            JSON: {terminated: true, output: string|null}.
        """
        session = get_session()
        _require_active(session)

        output = await session.terminate()
        return json.dumps({"terminated": True, "output": output or None})

    @mcp.tool()
    async def debug_status() -> str:
        """Check if a debug session is active and get current state.

        Returns:
            JSON: {active, program, threads: [{id, name}], current_location, capabilities: [string]}
            or {error} if no session.
        """
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

    @mcp.tool()
    async def debug_set_breakpoints(
        file: str,
        lines: list[int],
        conditions: dict[str, str] | None = None,
        hit_conditions: dict[str, str] | None = None,
        log_messages: dict[str, str] | None = None,
    ) -> str:
        """Set breakpoints in a source file.

        Args:
            file: Path to the source file, or filename for browser scripts (e.g. 'app.js').
                  For browser debugging, the filename is matched against loaded scripts automatically.
            lines: Line numbers where breakpoints should be set.
            conditions: Optional conditions per line, e.g. {"17": "n > 10"}.
                        The breakpoint only triggers when the condition is true.
            hit_conditions: Optional hit counts per line, e.g. {"17": "5"}.
                            The breakpoint triggers on the Nth hit.
            log_messages: Optional log messages per line, e.g. {"17": "x = {x}"}.
                          Logs the message instead of stopping. Use {expr} for interpolation.

        Returns:
            JSON: {file, breakpoints: [{line, verified, condition?, hit_condition?, log_message?}]}
            or {error}.
        """
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

    @mcp.tool()
    async def debug_set_function_breakpoints(
        functions: list[str],
        conditions: dict[str, str] | None = None,
    ) -> str:
        """Set breakpoints on function names. Breaks when the function is called.

        Args:
            functions: Function names to break on (e.g. ["my_function", "MyClass.method"]).
            conditions: Optional conditions per function, e.g. {"my_function": "x > 10"}.

        Returns:
            JSON: {breakpoints: [{function, verified, line?, condition?}]}.
        """
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

    @mcp.tool()
    async def debug_set_exception_breakpoints(
        filters: list[str] | None = None,
    ) -> str:
        """Break on exceptions.

        Args:
            filters: Exception filter types. Options: 'raised' (all exceptions),
                     'uncaught' (unhandled only). Default: ['uncaught'].

        Returns:
            JSON: {filters: [string]}.
        """
        session = get_session()
        _require_active(session)
        await session.client.set_exception_breakpoints(filters)
        return json.dumps({"filters": filters or ["uncaught"]})

    # ── Execution control ──────────────────────────────────────

    @mcp.tool()
    async def debug_pause(thread_id: int = 1) -> str:
        """Pause a running thread. Use when the program is running (e.g., stuck in a loop).

        Args:
            thread_id: Thread to pause (default: 1, the main thread).

        Returns:
            JSON: {reason, location: {file, line, function}, source_context: [{number, text, current}],
            locals: [{name, type, value, ref?}], exception?: {type, message, traceback}}
            or {terminated: true, output} or {running: true, message}.
        """
        session = get_session()
        _require_active(session)

        await session.client.pause(thread_id)
        return await _wait_and_report(session)

    @mcp.tool()
    async def debug_continue(wait: bool = True) -> str:
        """Resume execution until next breakpoint, exception, or program end.

        Args:
            wait: If True (default), block until the debugger stops and return full context.
                  If False, resume execution and return immediately — useful for interactive
                  browser debugging where the user triggers breakpoints by interacting with the page.
                  After wait=False, use debug_status to check if the debugger has stopped,
                  or debug_wait_for_event to block until the next breakpoint.

        Returns:
            If wait=True: JSON with stop reason, location, source context, and local variables.
            If wait=False: JSON {resumed: true, message: string}.
        """
        session = get_session()
        _require_active(session)

        await session.client.continue_execution()
        if not wait:
            return json.dumps({"resumed": True, "message": "Execution resumed. Use debug_status to check state or debug_wait_for_event to wait for next breakpoint."})
        return await _wait_and_report(session)

    @mcp.tool()
    async def debug_step_over(wait: bool = True) -> str:
        """Step over: execute the current line and stop at the next one.

        Args:
            wait: If True (default), block until stopped. If False, return immediately.

        Returns:
            JSON with stop reason, location, source context, and local variables.
        """
        session = get_session()
        _require_active(session)

        await session.client.next_step()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step over initiated."})
        return await _wait_and_report(session)

    @mcp.tool()
    async def debug_step_into(wait: bool = True) -> str:
        """Step into: enter the function call on the current line.

        Args:
            wait: If True (default), block until stopped. If False, return immediately.

        Returns:
            JSON with stop reason, location, source context, and local variables.
        """
        session = get_session()
        _require_active(session)

        await session.client.step_in()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step into initiated."})
        return await _wait_and_report(session)

    @mcp.tool()
    async def debug_step_out(wait: bool = True) -> str:
        """Step out: run until the current function returns.

        Args:
            wait: If True (default), block until stopped. If False, return immediately.

        Returns:
            JSON with stop reason, location, source context, and local variables.
        """
        session = get_session()
        _require_active(session)

        await session.client.step_out()
        if not wait:
            return json.dumps({"resumed": True, "message": "Step out initiated."})
        return await _wait_and_report(session)

    @mcp.tool()
    async def debug_wait_for_event(timeout: float = 300.0) -> str:
        """Wait for the debugger to stop (breakpoint, exception, or termination).

        Use after debug_continue(wait=False) in interactive browser debugging:
        the user interacts with the page, and this tool blocks until a breakpoint
        is hit or the timeout is reached.

        Args:
            timeout: Maximum seconds to wait (default: 300 = 5 minutes).
                     For browser debugging, the user may take time to interact.

        Returns:
            JSON with stop reason, location, source context, and local variables
            (same format as debug_continue with wait=True).
            If timeout: {running: true, message: string}.
        """
        session = get_session()
        _require_active(session)
        return await _wait_and_report(session, timeout=timeout)

    # ── Inspection ─────────────────────────────────────────────

    @mcp.tool()
    async def debug_stacktrace(thread_id: int = 1) -> str:
        """Get the current call stack.

        Args:
            thread_id: Thread to inspect (default: 1, the main thread).

        Returns:
            JSON: {frames: [{index, file, line, function, current: bool}]} or {error}.
        """
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

    @mcp.tool()
    async def debug_variables(
        scope: str = "local",
        thread_id: int = 1,
        frame_index: int = 0,
    ) -> str:
        """Inspect variables in the current scope.

        Args:
            scope: 'local' or 'global' (default: 'local').
            thread_id: Thread to inspect (default: 1).
            frame_index: Stack frame index, 0 = current (default: 0).

        Returns:
            JSON: {scope, location, variables: [{name, type, value, ref?}]} or {error}.
            Variables with ref > 0 can be expanded with debug_expand_variable.
        """
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

    @mcp.tool()
    async def debug_evaluate(
        expression: str,
        frame_index: int = 0,
        thread_id: int = 1,
    ) -> str:
        """Evaluate a Python expression in the debugger context.

        Args:
            expression: Python expression to evaluate (e.g., 'len(my_list)', 'x + y').
            frame_index: Stack frame for context (0 = current frame).
            thread_id: Thread to use (default: 1).

        Returns:
            JSON: {expression, type, value, ref?}. If ref is present, use debug_expand_variable to drill in.
        """
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

    @mcp.tool()
    async def debug_set_variable(
        name: str,
        value: str,
        scope: str = "local",
        thread_id: int = 1,
        frame_index: int = 0,
    ) -> str:
        """Modify a variable's value during debugging.

        Args:
            name: Variable name to modify.
            value: New value as a Python expression (e.g., "42", '"hello"', "[1,2,3]").
            scope: 'local' or 'global' (default: 'local').
            thread_id: Thread to use (default: 1).
            frame_index: Stack frame index (default: 0, current frame).

        Returns:
            JSON: {name, value, type} or {error}.
        """
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

    @mcp.tool()
    async def debug_goto(
        line: int,
        file: str | None = None,
        thread_id: int = 1,
    ) -> str:
        """Jump to a specific line without executing intermediate code.

        Args:
            line: Target line number to jump to.
            file: Source file path (defaults to current file).
            thread_id: Thread to use (default: 1).

        Returns:
            JSON with stop reason, location, source context, and local variables.
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

    @mcp.tool()
    async def debug_source_context(lines_before: int = 5, lines_after: int = 5) -> str:
        """Show source code around the current execution point.

        Args:
            lines_before: Number of lines to show before current line (default: 5).
            lines_after: Number of lines to show after current line (default: 5).

        Returns:
            JSON: {file, current_line, lines: [{number, text, current: bool}]} or {error}.
        """
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

    @mcp.tool()
    async def debug_output(last_n: int | None = 50, source: str = "all") -> str:
        """Get the program's stdout/stderr output.

        Args:
            last_n: Number of last lines to return (default: 50, None = all).
            source: 'subprocess' (pipe only), 'dap' (DAP output events only), 'all' (both).

        Returns:
            JSON: {output: string|null}.
        """
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

    @mcp.tool()
    async def debug_exception_info(thread_id: int = 1) -> str:
        """Get details about the current exception when stopped on one.

        Args:
            thread_id: Thread to inspect (default: 1).

        Returns:
            JSON: {exception, message: string|null, traceback: string|null} or {error}.
        """
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

    @mcp.tool()
    async def debug_expand_variable(
        variables_reference: int, max_depth: int = 1, skip_internals: bool = True,
    ) -> str:
        """Expand a complex variable (dict, list, object) to see its contents.

        Use the ref=N value from debug_variables or debug_evaluate output.

        Args:
            variables_reference: The reference ID from the "ref" field in debug_variables or debug_evaluate output.
            max_depth: How many levels of nesting to expand (default: 1, no hard limit).
                - 1: immediate children only (keys of a dict, items of a list, attributes of an object).
                - 2-3: good for most inspection needs.
                - 4+: useful for deeply nested structures (JSON configs, ASTs, ORMs).
                Circular references are detected and short-circuited automatically.
                Use debug_evaluate for direct access to a known path (e.g. 'obj.a.b.c.d.e').
            skip_internals: Filter out Python internal groups (default: True).
                When True, hides 'special variables' (__dunder__ methods like __class__,
                __eq__, __repr__...) and 'function variables' (builtin methods like
                .keys(), .append(), .copy()...) that debugpy exposes for every object.
                This drastically reduces output noise — a dict goes from ~50 lines to
                just its keys and values. Set to False when you need to inspect an
                object's full Python interface.

        Returns:
            JSON: {children: [{name, type, value, ref?, children?: [...recursive]}]} or {error}.
            Each child with a ref can be further expanded. Children are only populated up to max_depth.
        """
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

    @mcp.tool()
    async def debug_modules(filter: str | None = None) -> str:
        """List loaded Python modules.

        Args:
            filter: Optional substring to filter module names.

        Returns:
            JSON: {modules: [{name, path}], count} or {error}.
        """
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
