"""Lightweight LSP (Language Server Protocol) client for JDT LS communication.

Implements JSON-RPC 2.0 over stdin/stdout with Content-Length framing.
Used by JavaLauncher to orchestrate JDT LS and obtain a DAP debug port.
"""

import asyncio
import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class LSPClient:
    """Async client for communicating with a Language Server via stdin/stdout.

    Uses subprocess.Popen (not asyncio subprocess) for compatibility with
    session.py's output collection which expects a Popen process.
    The read loop runs in a thread executor for async operation.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._seq = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._initialized = False

    # ── Process management ──────────────────────────────────────

    async def start(self, cmd: list[str], cwd: str, env: dict | None = None) -> None:
        """Start the LSP server process and begin reading responses."""
        logger.info("Starting LSP server: %s", " ".join(cmd[:5]))
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        self._read_task = asyncio.create_task(self._read_loop())

    async def stop(self, timeout: float = 5.0) -> None:
        """Gracefully shut down the LSP server."""
        if not self._process:
            return
        try:
            await self.send_request("shutdown", {})
            await self.send_notification("exit", None)
        except Exception:
            pass
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    # ── Low-level communication ─────────────────────────────────

    def _send_raw(self, message: dict) -> None:
        """Send a JSON-RPC message with Content-Length framing."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("LSP server not running")
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    async def send_request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        req_id = self._seq
        self._seq += 1

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        self._send_raw(msg)

        try:
            result = await asyncio.wait_for(future, timeout=60.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"LSP request '{method}' timed out after 60s")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"LSP error in '{method}': {err.get('message', err)}")
        return result.get("result")

    async def send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send_raw(msg)

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from stdout in a thread executor."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                # Read lines in executor (blocking I/O)
                header_line = await loop.run_in_executor(
                    None, self._process.stdout.readline
                )
                if not header_line:
                    break
                line = header_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("Content-Length:"):
                    continue
                content_length = int(line.split(":")[1].strip())

                # Read until empty line (end of headers)
                while True:
                    sep = await loop.run_in_executor(
                        None, self._process.stdout.readline
                    )
                    if not sep or sep.strip() == b"":
                        break

                # Read body
                body = await loop.run_in_executor(
                    None, self._process.stdout.read, content_length
                )
                if not body:
                    break
                msg = json.loads(body)
                self._handle_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("LSP read loop error")

    def _handle_message(self, msg: dict) -> None:
        """Dispatch a JSON-RPC message."""
        if "id" in msg and ("result" in msg or "error" in msg):
            # Response to a request
            req_id = msg["id"]
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                # Thread-safe: schedule on the event loop
                future.get_loop().call_soon_threadsafe(future.set_result, msg)
            logger.debug("LSP response id=%s", req_id)
        elif "method" in msg:
            # Notification or server request
            method = msg["method"]
            logger.debug("LSP notification: %s", method)
            # Handle server-initiated requests (need a response)
            if "id" in msg:
                self._handle_server_request(msg)

    def _handle_server_request(self, msg: dict) -> None:
        """Respond to server-initiated requests (e.g. window/workDoneProgress/create)."""
        response = {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": None,
        }
        self._send_raw(response)

    # ── High-level LSP operations ───────────────────────────────

    async def initialize(self, workspace_path: str, init_options: dict | None = None) -> dict:
        """Perform the LSP initialize + initialized handshake."""
        params = {
            "processId": None,
            "rootUri": f"file://{workspace_path}",
            "capabilities": {
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                },
                "textDocument": {},
                "window": {
                    "workDoneProgress": True,
                },
            },
            "workspaceFolders": [
                {"uri": f"file://{workspace_path}", "name": "workspace"}
            ],
        }
        if init_options:
            params["initializationOptions"] = init_options

        result = await self.send_request("initialize", params)
        await self.send_notification("initialized", {})
        self._initialized = True
        logger.info("LSP initialized for workspace: %s", workspace_path)
        return result

    async def execute_command(self, command: str, arguments: list | None = None) -> Any:
        """Execute a workspace command (workspace/executeCommand)."""
        params: dict[str, Any] = {"command": command}
        if arguments:
            params["arguments"] = arguments
        return await self.send_request("workspace/executeCommand", params)
