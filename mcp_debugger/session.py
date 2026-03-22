"""Debug session manager.

Manages the lifecycle of a debug session: launches the appropriate debug adapter
via a language-specific Launcher, connects the DAP client, and handles cleanup.
"""

import asyncio
import logging
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from .dap_client import DAPClient
from .launchers import BaseLauncher, create_launcher, detect_language

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 5679
_IS_WINDOWS = sys.platform == "win32"


class DebugSession:
    """Manages a single debug session with any supported language."""

    def __init__(self) -> None:
        self.client = DAPClient()
        self._launcher: BaseLauncher | None = None
        self._process: subprocess.Popen | None = None
        self._adapter_process: subprocess.Popen | None = None
        self._host = _DEFAULT_HOST
        self._port = _DEFAULT_PORT
        self._program: str | None = None
        self._output_lines: list[str] = []
        self._output_task: asyncio.Task[None] | None = None

    @property
    def is_active(self) -> bool:
        return self.client.is_connected

    @property
    def program(self) -> str | None:
        return self._program

    @property
    def capabilities(self) -> dict:
        """DAP capabilities reported by the debug adapter."""
        return self.client.capabilities

    async def start(
        self,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stop_on_entry: bool = False,
        port: int | None = None,
        language: str | None = None,
        **launcher_kwargs: Any,
    ) -> dict[str, Any]:
        """Start a debug session.

        Args:
            program: Path to the source file to debug.
            args: Command-line arguments for the program.
            cwd: Working directory for the program.
            env: Additional environment variables.
            stop_on_entry: Whether to pause at the first executable line.
            port: Port for the DAP server (default: 5679).
            language: Debug language ('python', 'node'). Auto-detected if omitted.
            **launcher_kwargs: Language-specific options (e.g. python_path for Python).

        Returns:
            Dict with session info (host, port, program, pid, ...).
        """
        if self.is_active:
            await self.stop()

        self._port = port or _DEFAULT_PORT

        # Detect language first (URLs need special handling)
        lang = language or detect_language(program)

        # For URLs (browser mode), store as-is; for files, resolve and validate
        if lang == "browser":
            self._program = program
        else:
            self._program = str(Path(program).resolve())
            if not Path(self._program).is_file():
                raise FileNotFoundError(f"Program not found: {self._program}")
        self._launcher = create_launcher(lang)

        # Launch the debug adapter + debuggee
        result = await self._launcher.launch(
            program=self._program,
            args=args,
            cwd=cwd,
            env=env,
            port=self._port,
            **launcher_kwargs,
        )
        self._process = result.process
        self._adapter_process = result.adapter_process

        # Collect stdout from the debuggee process. Skip when the process
        # stdout is consumed by another protocol (e.g. JDT LS uses stdout
        # for LSP messages — program output arrives via DAP events instead).
        self._output_lines = []
        proc_stdout_available = (
            self._process
            and self._process.stdout
            and not getattr(self._launcher, '_lsp', None)
        )
        if proc_stdout_available:
            self._output_task = asyncio.create_task(self._collect_output())
        else:
            self._output_task = None

        # Connect DAP client
        try:
            await self.client.connect(result.host, result.port)
            await self.client.initialize(adapter_id=self._launcher.adapter_id)
            self.client._child_adapter_id = self._launcher.adapter_id

            # Build DAP arguments for the attach/launch request
            dap_args = self._launcher.get_dap_arguments(
                program=self._program, cwd=cwd,
                stop_on_entry=stop_on_entry, **launcher_kwargs,
            )

            # For stop_on_entry, set initial breakpoints
            initial_bps = None
            if stop_on_entry:
                first_line = self._launcher.first_code_line(self._program)
                initial_bps = {self._program: [first_line]}
                logger.info("stop_on_entry: breakpoint at line %d", first_line)

            request_type = self._launcher.get_dap_request_type()
            await self.client.start_debugging(
                request_type=request_type,
                arguments=dap_args,
                initial_breakpoints=initial_bps,
            )
        except Exception:
            await self.stop()
            raise

        logger.info("Debug session started for %s [%s] on port %d",
                     self._program, lang, self._port)

        info: dict[str, Any] = {
            "host": result.host,
            "port": result.port,
            "program": self._program,
            "pid": self._process.pid,
        }
        info.update(result.extra_info)
        return info

    async def terminate(self) -> str:
        """Gracefully terminate the debugged program.

        Falls back to SIGTERM after 3s. Returns captured output.
        """
        try:
            if self.client.is_connected:
                await self.client.terminate()
                if self._process and self._process.poll() is None:
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        logger.info("Graceful terminate timed out, falling back to stop()")
                await asyncio.sleep(0.3)
        except Exception:
            logger.debug("Terminate request failed, falling back to stop()", exc_info=True)

        return await self.stop()

    async def stop(self) -> str:
        """Stop the debug session and return captured output."""
        output = ""
        try:
            if self.client.is_connected:
                await self.client.dap_disconnect(terminate=True)
        except Exception:
            logger.debug("Error during DAP disconnect", exc_info=True)

        if self._output_task:
            self._output_task.cancel()
            try:
                await self._output_task
            except asyncio.CancelledError:
                pass

        # Kill main debuggee process
        if self._process:
            self._kill_process(self._process)
            output = "\n".join(self._output_lines)
            self._process = None

        # Kill adapter process if separate (e.g. js-debug)
        if self._adapter_process:
            self._kill_process(self._adapter_process)
            self._adapter_process = None

        # Launcher-specific cleanup
        if self._launcher:
            try:
                await self._launcher.cleanup()
            except Exception:
                logger.debug("Launcher cleanup error", exc_info=True)

        self._program = None
        self._launcher = None
        logger.info("Debug session stopped")
        return output

    async def _collect_output(self) -> None:
        """Collect stdout/stderr from the debuggee process in background."""
        proc = self._process
        if not proc or not proc.stdout:
            return
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                line = line.rstrip("\n")
                # Apply launcher-specific output filter
                if self._launcher and not self._launcher.output_filter(line):
                    continue
                self._output_lines.append(line)
                logger.debug("program output: %s", line)
        except asyncio.CancelledError:
            pass

    def get_output(self, last_n: int | None = None) -> str:
        """Get captured program output."""
        lines = self._output_lines[-last_n:] if last_n else self._output_lines
        return "\n".join(lines)

    @staticmethod
    def _kill_process(proc: subprocess.Popen) -> None:
        """Kill a subprocess, handling platform differences."""
        if proc.poll() is not None:
            return
        if _IS_WINDOWS:
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


# ── Singleton session manager ──────────────────────────────────

_current_session: DebugSession | None = None


def get_session() -> DebugSession:
    """Get or create the current debug session."""
    global _current_session
    if _current_session is None:
        _current_session = DebugSession()
    return _current_session


async def reset_session() -> str:
    """Stop and discard the current session. Returns captured output."""
    global _current_session
    output = ""
    if _current_session is not None:
        output = await _current_session.stop()
        _current_session = None
    return output
