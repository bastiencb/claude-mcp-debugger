"""Python debug launcher using debugpy."""

import ast
import asyncio
import logging
import os
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Give debugpy time to start listening
        await asyncio.sleep(1.0)

        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"debugpy exited immediately (code={process.returncode}): {output}")

        return LaunchResult(
            process=process,
            host=host,
            port=port,
            extra_info={"python": python_exe},
        )
