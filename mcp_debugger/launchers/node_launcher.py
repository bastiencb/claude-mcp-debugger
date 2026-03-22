"""Node.js debug launcher using vscode-js-debug (dapDebugServer.js)."""

import asyncio
import io
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .base import BaseLauncher, LaunchResult

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Where we store downloaded adapters (next to the MCP venv)
_ADAPTERS_DIR = Path(__file__).parent.parent / ".adapters"
_JS_DEBUG_DIR = _ADAPTERS_DIR / "js-debug"
_JS_DEBUG_ENTRY = _JS_DEBUG_DIR / "src" / "dapDebugServer.js"

# GitHub release to download
_JS_DEBUG_REPO = "microsoft/vscode-js-debug"
_JS_DEBUG_TAG = "v1.96.0"
_JS_DEBUG_URL = (
    f"https://github.com/{_JS_DEBUG_REPO}/releases/download/{_JS_DEBUG_TAG}"
    f"/js-debug-dap-{_JS_DEBUG_TAG}.tar.gz"
)

# Output noise patterns from js-debug
_NOISE_PATTERNS = (
    "Debugger attached.",
    "Waiting for the debugger",
)


def _ensure_adapter() -> str:
    """Ensure vscode-js-debug is installed. Returns path to dapDebugServer.js.

    Downloads from GitHub releases on first use.
    """
    if _JS_DEBUG_ENTRY.is_file():
        return str(_JS_DEBUG_ENTRY)

    logger.info("js-debug not found, downloading from %s", _JS_DEBUG_URL)
    _ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with urlopen(_JS_DEBUG_URL) as resp:
            data = resp.read()
    except Exception as e:
        raise RuntimeError(
            f"Failed to download js-debug from {_JS_DEBUG_URL}: {e}\n"
            f"You can manually extract vscode-js-debug into {_JS_DEBUG_DIR}"
        ) from e

    # Extract tar.gz — the archive contains a js-debug/ directory
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(path=_ADAPTERS_DIR, filter="data")
    except Exception:
        # Fallback: some releases may be zip
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(path=_ADAPTERS_DIR)
        except Exception as e2:
            raise RuntimeError(f"Failed to extract js-debug archive: {e2}") from e2

    if not _JS_DEBUG_ENTRY.is_file():
        # The archive might have a different directory name
        for candidate in _ADAPTERS_DIR.iterdir():
            entry = candidate / "src" / "dapDebugServer.js"
            if entry.is_file():
                candidate.rename(_JS_DEBUG_DIR)
                break

    if not _JS_DEBUG_ENTRY.is_file():
        raise RuntimeError(
            f"js-debug extracted but dapDebugServer.js not found at {_JS_DEBUG_ENTRY}. "
            f"Contents of {_ADAPTERS_DIR}: {list(_ADAPTERS_DIR.iterdir())}"
        )

    logger.info("js-debug installed at %s", _JS_DEBUG_DIR)
    return str(_JS_DEBUG_ENTRY)


async def _launch_js_debug_adapter(
    cwd: str,
    port: int,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen, str, str]:
    """Start the js-debug DAP adapter process.

    Shared by NodeLauncher and BrowserLauncher.
    Returns (adapter_process, host, node_bin).
    """
    host = "127.0.0.1"

    node_bin = shutil.which("node")
    if not node_bin:
        raise RuntimeError("Node.js not found in PATH. Install Node.js for JavaScript debugging.")

    dap_server = _ensure_adapter()
    cmd = [node_bin, dap_server, str(port), host]
    logger.info("Starting js-debug DAP server: %s", " ".join(cmd))

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    adapter_process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    await asyncio.sleep(1.0)

    if adapter_process.poll() is not None:
        output = adapter_process.stdout.read() if adapter_process.stdout else ""
        raise RuntimeError(f"js-debug DAP server exited immediately (code={adapter_process.returncode}): {output}")

    return adapter_process, host, node_bin


class NodeLauncher(BaseLauncher):
    """Launch Node.js programs under vscode-js-debug."""

    @property
    def language_id(self) -> str:
        return "node"

    @property
    def adapter_id(self) -> str:
        return "pwa-node"

    def output_filter(self, line: str) -> bool:
        return not any(pattern in line for pattern in _NOISE_PATTERNS)

    def get_dap_request_type(self) -> str:
        return "launch"

    def get_dap_arguments(self, program: str, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        args: dict[str, Any] = {
            "type": "pwa-node",
            "request": "launch",
            "program": str(Path(program).resolve()),
        }
        if cwd:
            args["cwd"] = cwd
        # stopOnEntry is handled natively by js-debug
        stop_on_entry = kwargs.get("stop_on_entry", False)
        if stop_on_entry:
            args["stopOnEntry"] = True
        return args

    async def launch(
        self,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        port: int = 5679,
        **kwargs: Any,
    ) -> LaunchResult:
        work_dir = cwd or str(Path(program).resolve().parent)
        adapter_process, host, node_bin = await _launch_js_debug_adapter(work_dir, port, env)

        return LaunchResult(
            process=adapter_process,
            host=host,
            port=port,
            extra_info={"node": node_bin, "adapter": "js-debug"},
        )
