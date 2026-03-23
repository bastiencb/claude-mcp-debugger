"""Python debug launcher using debugpy."""

import ast
import asyncio
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import BaseLauncher, LaunchResult

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_BIN_DIR = "Scripts" if _IS_WINDOWS else "bin"
_PATHSEP = ";" if _IS_WINDOWS else ":"

# Lines matching these substrings are filtered from debugpy output
_NOISE_PATTERNS = (
    "Debugger warning:",
    "frozen modules",
    "PYDEVD_DISABLE_FILE_VALIDATION",
    "Debugging will proceed",
)


def _find_project_python(program_path: str, cwd: str | None = None) -> str:
    """Find the project's Python venv, searching from cwd/program upward."""
    search_dir = (Path(cwd) if cwd else Path(program_path).parent).resolve()
    for d in [search_dir] + list(search_dir.parents):
        for venv_name in (".venv", "venv"):
            candidate = d / venv_name / _BIN_DIR / "python"
            if candidate.is_file():
                logger.info("Found project Python: %s", candidate)
                return str(candidate)
            if _IS_WINDOWS:
                candidate_exe = candidate.with_suffix(".exe")
                if candidate_exe.is_file():
                    logger.info("Found project Python: %s", candidate_exe)
                    return str(candidate_exe)
        if d == Path.home() or d == d.parent:
            break
    return ""


def _debugpy_site_packages() -> str:
    """Get the site-packages path containing debugpy in the MCP venv."""
    mcp_venv_lib = Path(__file__).parent.parent / ".venv" / "lib"
    if _IS_WINDOWS:
        mcp_venv_lib = Path(__file__).parent.parent / ".venv" / "Lib"
    if not mcp_venv_lib.is_dir():
        return ""
    if _IS_WINDOWS:
        sp = mcp_venv_lib / "site-packages"
        if sp.is_dir() and (sp / "debugpy").is_dir():
            return str(sp)
        return ""
    for pydir in mcp_venv_lib.iterdir():
        sp = pydir / "site-packages"
        if sp.is_dir() and (sp / "debugpy").is_dir():
            return str(sp)
    return ""


def _first_code_line(file_path: str) -> int:
    """Find the first executable line in a Python file using AST.

    Skips module docstrings to find the first real statement.
    """
    try:
        with open(file_path) as f:
            tree = ast.parse(f.read())
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            return node.lineno
    except Exception:
        logger.debug("Could not parse %s for first code line, defaulting to 1", file_path)
    return 1


def _is_port_in_use(host: str, port: int) -> bool:
    """Check if a port is currently in use (non-intrusive bind test)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return False  # bind succeeded → port is free
    except OSError:
        return True  # bind failed → port is in use
    finally:
        s.close()


def _find_pid_on_port(port: int) -> int | None:
    """Find the PID of the process listening on the given port. Returns None if not found."""
    try:
        if _IS_WINDOWS:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    return int(parts[-1])
        else:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                return int(result.stdout.strip().splitlines()[0])
    except Exception as e:
        logger.debug("Could not find PID on port %d: %s", port, e)
    return None


def _kill_port_holder(port: int) -> bool:
    """Kill any process listening on the given port. Returns True if a process was killed."""
    pid = _find_pid_on_port(port)
    if pid is None:
        logger.debug("No process found on port %d", port)
        return False

    logger.info("Killing stale process PID %d on port %d", pid, port)
    try:
        if _IS_WINDOWS:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                logger.warning("taskkill failed for PID %d: %s", pid, result.stderr.strip())
                return False
            logger.info("Killed process tree for PID %d", pid)
        else:
            os.kill(pid, 9)
            logger.info("Killed PID %d with SIGKILL", pid)
        return True
    except ProcessLookupError:
        logger.debug("PID %d already exited", pid)
        return True
    except Exception as e:
        logger.warning("Failed to kill PID %d on port %d: %s", pid, port, e)
        return False


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill a process and all its children. Cross-platform."""
    if proc.poll() is not None:
        return
    pid = proc.pid
    logger.info("Killing process tree for PID %d", pid)
    try:
        if _IS_WINDOWS:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                logger.warning("taskkill /T failed for PID %d: %s, falling back to proc.kill()", pid, result.stderr.strip())
                proc.kill()
        else:
            # Kill the entire process group
            try:
                os.killpg(os.getpgid(pid), 9)
            except ProcessLookupError:
                pass
            except PermissionError:
                logger.debug("Cannot killpg PID %d, falling back to proc.kill()", pid)
                proc.kill()
    except Exception as e:
        logger.warning("Error killing process tree PID %d: %s, trying proc.kill()", pid, e)
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
        logger.debug("Process PID %d reaped", pid)
    except Exception:
        logger.warning("Process PID %d did not exit after kill", pid)


class PythonLauncher(BaseLauncher):
    """Launch Python programs under debugpy."""

    @property
    def language_id(self) -> str:
        return "python"

    @property
    def adapter_id(self) -> str:
        return "python"

    def first_code_line(self, file_path: str) -> int:
        return _first_code_line(file_path)

    def output_filter(self, line: str) -> bool:
        return not any(pattern in line for pattern in _NOISE_PATTERNS)

    def get_dap_request_type(self) -> str:
        return "attach"

    def get_dap_arguments(self, program: str, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "type": "python",
            "request": "attach",
            "justMyCode": True,
            "subProcess": True,
        }

    async def launch(
        self,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        port: int = 5679,
        **kwargs: Any,
    ) -> LaunchResult:
        host = "127.0.0.1"
        program_path = str(Path(program).resolve())
        work_dir = cwd or str(Path(program).resolve().parent)

        # Determine Python interpreter
        python_path = kwargs.get("python_path")
        project_python = _find_project_python(program_path, work_dir)
        python_exe = python_path or project_python or sys.executable
        logger.info("Using Python interpreter: %s", python_exe)

        # Merge environment
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        # Inject debugpy from MCP venv if using a different Python
        if python_exe != sys.executable:
            dbgpy_sp = _debugpy_site_packages()
            if dbgpy_sp:
                existing = proc_env.get("PYTHONPATH", "")
                proc_env["PYTHONPATH"] = f"{dbgpy_sp}{_PATHSEP}{existing}" if existing else dbgpy_sp
                logger.info("Injected debugpy from MCP venv: %s", dbgpy_sp)

        # Suppress frozen modules warning (Python 3.12+)
        proc_env["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"

        # Free the port if a stale process is holding it
        if _is_port_in_use(host, port):
            stale_pid = _find_pid_on_port(port)
            logger.warning(
                "Port %d already in use (PID %s), attempting cleanup",
                port, stale_pid or "unknown",
            )
            if _kill_port_holder(port):
                # Wait for the OS to release the port
                for i in range(6):
                    await asyncio.sleep(0.5)
                    if not _is_port_in_use(host, port):
                        logger.info("Port %d freed after %.1fs", port, (i + 1) * 0.5)
                        break
                else:
                    raise RuntimeError(
                        f"Port {port} is still in use 3s after killing PID {stale_pid}. "
                        f"Try a different port or check for other processes."
                    )
            else:
                raise RuntimeError(
                    f"Port {port} is in use by PID {stale_pid} and could not be freed. "
                    f"Try a different port or kill the process manually."
                )

        # Build debugpy command
        cmd = [
            python_exe, "-Xfrozen_modules=off",
            "-m", "debugpy",
            "--listen", f"{host}:{port}",
            "--wait-for-client",
            program_path,
        ]
        if args:
            cmd.extend(args)

        logger.info("Starting debugpy: %s", " ".join(cmd))

        process = subprocess.Popen(
            cmd,
            cwd=work_dir,
            env=proc_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.info("debugpy process started with PID %d", process.pid)

        # Wait for debugpy to start listening
        try:
            await self._wait_for_port(host, port, process)
        except Exception:
            # Ensure we don't leave a zombie process on failure
            logger.error("debugpy startup failed, cleaning up PID %d", process.pid)
            _kill_process_tree(process)
            raise

        return LaunchResult(
            process=process,
            host=host,
            port=port,
            extra_info={"python": python_exe},
        )

    @staticmethod
    async def _wait_for_port(
        host: str, port: int, process: subprocess.Popen,
        max_wait: float = 10.0, interval: float = 0.3,
    ) -> None:
        """Poll until debugpy is listening or the process dies."""
        elapsed = 0.0
        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval

            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(
                    f"debugpy exited with code {process.returncode} after {elapsed:.1f}s. "
                    f"Output: {output}"
                )

            try:
                with socket.create_connection((host, port), timeout=1):
                    logger.info("debugpy ready after %.1fs on port %d", elapsed, port)
                    return
            except OSError:
                logger.debug("debugpy not ready yet (%.1fs)", elapsed)

        # Timeout — gather diagnostics before raising
        alive = process.poll() is None
        output = ""
        if process.stdout:
            try:
                process.kill()
                output = process.stdout.read()
            except Exception:
                pass
        raise RuntimeError(
            f"debugpy did not start listening on {host}:{port} within {max_wait}s. "
            f"Process was {'alive' if alive else 'dead'}. Output: {output!r}"
        )
