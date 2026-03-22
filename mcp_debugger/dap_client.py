"""DAP (Debug Adapter Protocol) client for debug adapters (debugpy, js-debug, etc.).

Handles the low-level JSON-based DAP protocol over TCP sockets.
Each request/response follows the format:
  Header: Content-Length: <n>\r\n\r\n
  Body: JSON message

Supports reverse requests (adapter → client), used by adapters like
vscode-js-debug for multi-session management (startDebugging, etc.).
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DAPClient:
    """Async client for the Debug Adapter Protocol.

    Supports multi-session adapters (like vscode-js-debug) that use
    reverse requests to spawn child debug sessions. When a startDebugging
    request is received, a child connection is opened automatically and
    all subsequent commands are routed through it.
    """

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._read_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._capabilities: dict[str, Any] = {}
        self._stopped_event = asyncio.Event()
        self._stopped_info: dict[str, Any] | None = None
        self._output_events: list[dict[str, Any]] = []
        self._thread_events: list[dict[str, Any]] = []
        self._breakpoint_events: list[dict[str, Any]] = []
        # Multi-session support (js-debug child sessions)
        self._host: str | None = None
        self._port: int | None = None
        self._child: "DAPClient | None" = None  # child session for actual debugging
        self._child_ready = asyncio.Event()
        self._pending_target_id: str | None = None
        self._child_adapter_id: str = "pwa-node"  # set by session before start_debugging

    # ── Connection ──────────────────────────────────────────────

    async def connect(self, host: str, port: int, timeout: float = 10.0) -> None:
        """Connect to a DAP server."""
        logger.info("Connecting to DAP server at %s:%d", host, port)
        self._host = host
        self._port = port
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        self._read_task = asyncio.create_task(self._read_loop())
        logger.info("Connected to DAP server")

    async def disconnect(self) -> None:
        """Disconnect from the DAP server (and child session if any)."""
        if self._child:
            await self._child.disconnect()
            self._child = None
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._initialized = False
        logger.info("Disconnected from DAP server")

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def capabilities(self) -> dict[str, Any]:
        """DAP capabilities reported by the debug adapter."""
        return self._capabilities

    # ── DAP Protocol ────────────────────────────────────────────

    async def _send_nowait(self, command: str, arguments: dict | None = None) -> asyncio.Future[dict[str, Any]]:
        """Send a DAP request and return the future without awaiting the response."""
        if not self._writer:
            raise RuntimeError("Not connected to DAP server")

        seq = self._seq
        self._seq += 1

        msg = {
            "seq": seq,
            "type": "request",
            "command": command,
        }
        if arguments:
            msg["arguments"] = arguments

        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[seq] = future

        self._writer.write(header + body)
        await self._writer.drain()

        logger.debug("DAP request seq=%d command=%s (nowait)", seq, command)
        return future

    async def _await_response(self, future: asyncio.Future[dict[str, Any]], command: str) -> dict:
        """Await a DAP response future with timeout and error handling."""
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            raise TimeoutError(f"DAP request '{command}' timed out")

        if not response.get("success", False):
            error_msg = response.get("message", "Unknown error")
            body_error = response.get("body", {}).get("error", {}).get("format", "")
            raise RuntimeError(
                f"DAP '{command}' failed: {error_msg}" + (f" — {body_error}" if body_error else "")
            )

        return response

    async def _send(self, command: str, arguments: dict | None = None) -> dict:
        """Send a DAP request and wait for the response."""
        future = await self._send_nowait(command, arguments)
        return await self._await_response(future, command)

    async def _read_loop(self) -> None:
        """Read DAP messages from the server continuously."""
        assert self._reader is not None
        try:
            while True:
                raw_header = await self._reader.readuntil(b"\r\n\r\n")
                content_length = self._parse_content_length(raw_header.decode("ascii"))
                body_bytes = await self._reader.readexactly(content_length)
                msg = json.loads(body_bytes)
                self._dispatch(msg)
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionResetError):
            logger.debug("DAP read loop ended")
        except Exception:
            logger.exception("Unexpected error in DAP read loop")

    @staticmethod
    def _parse_content_length(header: str) -> int:
        for line in header.strip().split("\r\n"):
            if line.lower().startswith("content-length:"):
                return int(line.split(":", 1)[1].strip())
        raise ValueError(f"Missing Content-Length in header: {header!r}")

    def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "response":
            seq = msg.get("request_seq")
            future = self._pending.pop(seq, None)
            if future and not future.done():
                future.set_result(msg)
            logger.debug("DAP response command=%s success=%s", msg.get("command"), msg.get("success"))
        elif msg_type == "request":
            # Reverse request from the adapter (e.g. js-debug's startDebugging).
            # We acknowledge it so the adapter can proceed.
            self._handle_reverse_request(msg)
        elif msg_type == "event":
            event_name = msg.get("event")
            logger.debug("DAP event: %s", event_name)
            self._events.append(msg)
            body = msg.get("body", {})
            if event_name == "stopped":
                self._stopped_info = body
                self._stopped_event.set()
            elif event_name == "terminated":
                self._stopped_info = {"reason": "terminated"}
                self._stopped_event.set()
            elif event_name == "output":
                # Cap at 1000 to avoid memory accumulation
                if len(self._output_events) < 1000:
                    self._output_events.append(body)
            elif event_name == "thread":
                self._thread_events.append(body)
            elif event_name == "breakpoint":
                self._breakpoint_events.append(body)

    def _handle_reverse_request(self, msg: dict) -> None:
        """Handle a reverse request from the debug adapter.

        Adapters like vscode-js-debug send reverse requests such as
        'startDebugging' or 'runInTerminal'. For startDebugging, we
        spawn a child DAP session on the same port.
        """
        command = msg.get("command", "?")
        seq = msg.get("seq", 0)
        logger.info("DAP reverse request: %s (seq=%d)", command, seq)

        if command == "startDebugging":
            args = msg.get("arguments", {})
            config = args.get("configuration", {})
            self._pending_target_id = config.get("__pendingTargetId")
            logger.info("startDebugging: pendingTargetId=%s", self._pending_target_id)
            # Spawn child session in background
            asyncio.create_task(self._spawn_child_session())

        # Send a response back to the adapter
        self._send_reverse_response(seq, command)

    def _send_reverse_response(self, request_seq: int, command: str) -> None:
        """Send a response to a reverse request from the adapter."""
        response = {
            "seq": self._seq,
            "type": "response",
            "request_seq": request_seq,
            "command": command,
            "success": True,
        }
        self._seq += 1

        if self._writer and not self._writer.is_closing():
            body = json.dumps(response).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            self._writer.write(header + body)
            asyncio.create_task(self._writer.drain())
            logger.debug("DAP reverse response sent for %s", command)

    async def _spawn_child_session(self) -> None:
        """Open a child DAP connection for multi-session adapters (js-debug).

        The child session connects to the same host:port, initializes,
        and attaches with the __pendingTargetId from startDebugging.
        All subsequent debugging commands are routed through the child.
        """
        if not self._host or not self._port or not self._pending_target_id:
            logger.error("Cannot spawn child session: missing host/port/targetId")
            return

        try:
            child = DAPClient()
            await child.connect(self._host, self._port)
            await child.initialize(adapter_id=self._child_adapter_id)

            # Attach with the pending target ID
            await child.start_debugging("attach", {
                "type": self._child_adapter_id,
                "request": "attach",
                "__pendingTargetId": self._pending_target_id,
            })

            self._child = child
            self._child_ready.set()
            logger.info("Child DAP session ready (targetId=%s)", self._pending_target_id)
        except Exception:
            logger.exception("Failed to spawn child DAP session")

    @property
    def _active(self) -> "DAPClient":
        """Return the child session if available, otherwise self.

        For multi-session adapters, debugging commands should go to the child.
        """
        return self._child if self._child else self

    # ── Routing helpers for multi-session adapters ──────────────

    async def _route_send(self, command: str, arguments: dict | None = None) -> dict:
        """Send a DAP command, routing to the child session if available."""
        if self._child:
            return await self._child._send(command, arguments)
        return await self._send(command, arguments)

    # ── High-level DAP commands ─────────────────────────────────

    async def initialize(self, adapter_id: str = "python") -> dict:
        """Send the DAP initialize handshake.

        Args:
            adapter_id: DAP adapter identifier (e.g. 'python', 'pwa-node').
        """
        resp = await self._send("initialize", {
            "clientID": "claude-mcp-debugger",
            "clientName": "Claude MCP DAP Debugger",
            "adapterID": adapter_id,
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
            "supportsRunInTerminalRequest": False,
            "supportsStartDebuggingRequest": True,
        })
        self._capabilities = resp.get("body", {})
        self._initialized = True
        logger.info("DAP initialized (adapter=%s): capabilities=%s", adapter_id, list(self._capabilities.keys()))
        return resp

    async def start_debugging(
        self,
        request_type: str,
        arguments: dict[str, Any],
        initial_breakpoints: dict[str, list[int]] | None = None,
    ) -> dict:
        """Send a DAP launch or attach request with the configurationDone handshake.

        Most debug adapters delay the launch/attach response until configurationDone
        is received. Initial breakpoints are set in between.

        For multi-session adapters (js-debug), after a 'launch' request the adapter
        sends a startDebugging reverse request. We spawn a child session automatically
        and wait for it to be ready. Breakpoints are set on the child session.

        Args:
            request_type: 'attach' or 'launch'.
            arguments: DAP arguments for the request.
            initial_breakpoints: {file_path: [line_numbers]} to set before
                                 configurationDone (needed for stop_on_entry).
        """
        # 1. Send request (don't await — adapter won't respond until configurationDone)
        request_future = await self._send_nowait(request_type, arguments)

        # 2. Brief pause for the adapter to send the 'initialized' event
        await asyncio.sleep(0.3)

        # 3. Set breakpoints before configurationDone (for single-session adapters)
        if initial_breakpoints and not self._pending_target_id:
            for file_path, lines in initial_breakpoints.items():
                await self.set_breakpoints(file_path, lines)

        # 4. Send configurationDone — this unblocks the request response
        config_future = await self._send_nowait("configurationDone")
        await self._await_response(config_future, "configurationDone")
        resp = await self._await_response(request_future, request_type)

        # 5. For multi-session adapters (js-debug): wait for startDebugging
        #    reverse request and child session. The reverse request may arrive
        #    slightly after the launch response.
        if request_type == "launch" and not self._pending_target_id:
            # Give the read loop time to process a pending startDebugging request
            for _ in range(10):
                await asyncio.sleep(0.2)
                if self._pending_target_id:
                    break

        if self._pending_target_id and not self._child:
            logger.info("Waiting for child DAP session...")
            try:
                await asyncio.wait_for(self._child_ready.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                raise TimeoutError("Child DAP session did not start within timeout")

        # 6. Set breakpoints on the child session (js-debug needs them on the child)
        if initial_breakpoints and self._child:
            for file_path, lines in initial_breakpoints.items():
                await self._child.set_breakpoints(file_path, lines)

        logger.info("DAP %s completed", request_type)
        return resp

    async def set_breakpoints(self, source_path: str, lines: list[int],
                              conditions: dict[int, str] | None = None,
                              hit_conditions: dict[int, str] | None = None,
                              log_messages: dict[int, str] | None = None) -> list[dict]:
        """Set breakpoints in a source file. Returns list of verified breakpoints.

        Args:
            source_path: Absolute path to the source file.
            lines: Line numbers for breakpoints.
            conditions: Optional {line_number: condition_expression} for conditional breakpoints.
            hit_conditions: Optional {line_number: hit_count} — break on Nth hit (e.g. "5").
            log_messages: Optional {line_number: message} — log instead of stopping.
                          Use {expr} for interpolation (e.g. "x = {x}").
        """
        bp_specs = []
        for ln in lines:
            bp: dict[str, Any] = {"line": ln}
            if conditions and ln in conditions:
                bp["condition"] = conditions[ln]
            if hit_conditions and ln in hit_conditions:
                bp["hitCondition"] = hit_conditions[ln]
            if log_messages and ln in log_messages:
                bp["logMessage"] = log_messages[ln]
            bp_specs.append(bp)

        resp = await self._route_send("setBreakpoints", {
            "source": {"path": source_path},
            "breakpoints": bp_specs,
        })
        breakpoints = resp.get("body", {}).get("breakpoints", [])
        verified = [bp for bp in breakpoints if bp.get("verified")]
        logger.info(
            "Set breakpoints in %s: %d/%d verified",
            source_path, len(verified), len(lines),
        )
        return breakpoints

    async def get_loaded_sources(self) -> list[dict]:
        """Get all loaded source files from the debug adapter."""
        resp = await self._route_send("loadedSources")
        return resp.get("body", {}).get("sources", [])

    async def resolve_source(self, file_hint: str) -> dict | None:
        """Find a loaded source matching a file path or URL fragment.

        For browser debugging, scripts are identified by URL-based paths
        (e.g. 'localhost꞉8080/app.js'). This method matches partial names
        against loaded sources so users can just pass 'app.js'.
        """
        sources = await self.get_loaded_sources()
        # Exact match first
        for src in sources:
            if src.get("path") == file_hint or src.get("name") == file_hint:
                return src
        # Partial match: check if file_hint appears at the end of the source path
        for src in sources:
            path = src.get("path", "")
            name = src.get("name", "")
            if path.endswith(file_hint) or name.endswith(file_hint):
                return src
        return None

    async def set_breakpoints_by_source(
        self, source: dict, lines: list[int],
        conditions: dict[int, str] | None = None,
        hit_conditions: dict[int, str] | None = None,
        log_messages: dict[int, str] | None = None,
    ) -> list[dict]:
        """Set breakpoints using a full DAP source object (for browser scripts)."""
        bp_specs = []
        for ln in lines:
            bp: dict[str, Any] = {"line": ln}
            if conditions and ln in conditions:
                bp["condition"] = conditions[ln]
            if hit_conditions and ln in hit_conditions:
                bp["hitCondition"] = hit_conditions[ln]
            if log_messages and ln in log_messages:
                bp["logMessage"] = log_messages[ln]
            bp_specs.append(bp)

        resp = await self._route_send("setBreakpoints", {
            "source": source,
            "breakpoints": bp_specs,
        })
        breakpoints = resp.get("body", {}).get("breakpoints", [])
        verified = [bp for bp in breakpoints if bp.get("verified")]
        logger.info(
            "Set breakpoints in %s: %d/%d verified",
            source.get("name", "?"), len(verified), len(lines),
        )
        return breakpoints

    async def set_function_breakpoints(self, functions: list[str],
                                       conditions: dict[str, str] | None = None) -> list[dict]:
        """Set breakpoints on function names. Returns list of verified breakpoints.

        Args:
            functions: Function names to break on.
            conditions: Optional {function_name: condition_expression}.
        """
        bp_specs = []
        for fn in functions:
            bp: dict[str, Any] = {"name": fn}
            if conditions and fn in conditions:
                bp["condition"] = conditions[fn]
            bp_specs.append(bp)

        resp = await self._route_send("setFunctionBreakpoints", {
            "breakpoints": bp_specs,
        })
        breakpoints = resp.get("body", {}).get("breakpoints", [])
        verified = [bp for bp in breakpoints if bp.get("verified")]
        logger.info("Set function breakpoints: %d/%d verified", len(verified), len(functions))
        return breakpoints

    async def set_exception_breakpoints(self, filters: list[str] | None = None) -> dict:
        """Set exception breakpoints (e.g. 'raised', 'uncaught')."""
        return await self._route_send("setExceptionBreakpoints", {
            "filters": filters or ["uncaught"],
        })

    async def set_variable(self, variables_reference: int, name: str, value: str) -> dict:
        """Set a variable's value. Returns the new value as confirmed by the debugger."""
        resp = await self._route_send("setVariable", {
            "variablesReference": variables_reference,
            "name": name,
            "value": value,
        })
        return resp.get("body", {})

    async def goto_targets(self, source_path: str, line: int) -> list[dict]:
        """Get possible goto targets at a given line."""
        resp = await self._route_send("gotoTargets", {
            "source": {"path": source_path},
            "line": line,
        })
        return resp.get("body", {}).get("targets", [])

    async def goto(self, thread_id: int, target_id: int) -> dict:
        """Jump to a goto target without executing intermediate code."""
        self._active._stopped_event.clear()
        self._active._stopped_info = None
        return await self._route_send("goto", {
            "threadId": thread_id,
            "targetId": target_id,
        })

    async def continue_execution(self, thread_id: int = 1) -> dict:
        """Resume execution."""
        self._active._stopped_event.clear()
        self._active._stopped_info = None
        return await self._route_send("continue", {"threadId": thread_id})

    async def next_step(self, thread_id: int = 1) -> dict:
        """Step over (next line)."""
        self._active._stopped_event.clear()
        self._active._stopped_info = None
        return await self._route_send("next", {"threadId": thread_id})

    async def step_in(self, thread_id: int = 1) -> dict:
        """Step into a function call."""
        self._active._stopped_event.clear()
        self._active._stopped_info = None
        return await self._route_send("stepIn", {"threadId": thread_id})

    async def step_out(self, thread_id: int = 1) -> dict:
        """Step out of the current function."""
        self._active._stopped_event.clear()
        self._active._stopped_info = None
        return await self._route_send("stepOut", {"threadId": thread_id})

    async def wait_for_stop(self, timeout: float = 30.0) -> dict:
        """Wait until the debugger stops (breakpoint, step, exception, or termination)."""
        target = self._active
        try:
            await asyncio.wait_for(target._stopped_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("Debugger did not stop within timeout")
        return target._stopped_info or {}

    async def get_threads(self) -> list[dict]:
        """Get all threads."""
        resp = await self._route_send("threads")
        return resp.get("body", {}).get("threads", [])

    async def get_stack_trace(self, thread_id: int = 1, levels: int = 20) -> list[dict]:
        """Get the call stack for a thread."""
        resp = await self._route_send("stackTrace", {
            "threadId": thread_id,
            "startFrame": 0,
            "levels": levels,
        })
        return resp.get("body", {}).get("stackFrames", [])

    async def get_scopes(self, frame_id: int) -> list[dict]:
        """Get variable scopes for a stack frame."""
        resp = await self._route_send("scopes", {"frameId": frame_id})
        return resp.get("body", {}).get("scopes", [])

    async def get_variables(self, variables_reference: int) -> list[dict]:
        """Get variables for a given scope/container reference."""
        resp = await self._route_send("variables", {
            "variablesReference": variables_reference,
        })
        return resp.get("body", {}).get("variables", [])

    async def evaluate(self, expression: str, frame_id: int | None = None,
                       context: str = "repl") -> dict:
        """Evaluate an expression in the debugger context."""
        args: dict[str, Any] = {
            "expression": expression,
            "context": context,
        }
        if frame_id is not None:
            args["frameId"] = frame_id
        resp = await self._route_send("evaluate", args)
        return resp.get("body", {})

    async def pause(self, thread_id: int = 1) -> dict:
        """Pause a running thread."""
        return await self._route_send("pause", {"threadId": thread_id})

    async def terminate(self) -> dict:
        """Send a graceful terminate request (raises KeyboardInterrupt in debugpy)."""
        return await self._route_send("terminate", {"restart": False})

    async def modules(self, start: int = 0, count: int = 100) -> list[dict]:
        """Get loaded modules."""
        resp = await self._route_send("modules", {
            "startModule": start,
            "moduleCount": count,
        })
        return resp.get("body", {}).get("modules", [])

    async def exception_info(self, thread_id: int = 1) -> dict:
        """Get details about the current exception (type, message, traceback)."""
        resp = await self._route_send("exceptionInfo", {"threadId": thread_id})
        return resp.get("body", {})

    async def dap_disconnect(self, terminate: bool = True) -> None:
        """Send DAP disconnect request, then close the connection."""
        try:
            await self._route_send("disconnect", {
                "restart": False,
                "terminateDebuggee": terminate,
            })
        except Exception:
            logger.debug("Disconnect request failed (debugee may already be terminated)")
        await self.disconnect()

    # ── Convenience ─────────────────────────────────────────────

    async def get_all_variables(self, thread_id: int = 1, frame_index: int = 0) -> dict[str, Any]:
        """Convenience: get all local variables at a given frame.

        Returns a dict of {variable_name: value_string}.
        """
        frames = await self.get_stack_trace(thread_id)
        if frame_index >= len(frames):
            return {}
        frame = frames[frame_index]
        scopes = await self.get_scopes(frame["id"])

        result: dict[str, Any] = {}
        for scope in scopes:
            if scope.get("name") in ("Locals", "Local"):
                variables = await self.get_variables(scope["variablesReference"])
                for var in variables:
                    name = var.get("name", "")
                    # Skip internal/dunder variables
                    if not name.startswith("__"):
                        result[name] = {
                            "value": var.get("value", ""),
                            "type": var.get("type", ""),
                        }
        return result

    def drain_events(self) -> list[dict]:
        """Return and clear collected events."""
        events = self._events.copy()
        self._events.clear()
        return events

    def drain_output_events(self) -> list[dict]:
        """Return and clear collected DAP output events (from both parent and child)."""
        events = self._output_events.copy()
        self._output_events.clear()
        if self._child:
            events.extend(self._child._output_events)
            self._child._output_events.clear()
        return events

    def drain_thread_events(self) -> list[dict]:
        """Return and clear collected thread events."""
        events = self._thread_events.copy()
        self._thread_events.clear()
        return events

    def drain_breakpoint_events(self) -> list[dict]:
        """Return and clear collected breakpoint events."""
        events = self._breakpoint_events.copy()
        self._breakpoint_events.clear()
        return events
