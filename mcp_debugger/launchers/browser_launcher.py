"""Browser debug launcher using vscode-js-debug in pwa-chrome mode.

Launches Chrome/Chromium and connects via the Chrome DevTools Protocol,
exposing a standard DAP interface for breakpoints, stepping, and inspection
of client-side JavaScript.
"""

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .base import BaseLauncher, LaunchResult
from .node_launcher import _launch_js_debug_adapter

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Chrome binary candidates, in order of preference
_CHROME_CANDIDATES_LINUX = [
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
]
_CHROME_CANDIDATES_MACOS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
_CHROME_CANDIDATES_WINDOWS = [
    r"Google\Chrome\Application\chrome.exe",
]

# Output noise from js-debug + Chrome
_NOISE_PATTERNS = (
    "Debugger attached.",
    "DevTools listening on",
    "Opening in existing browser session",
)


def _find_chrome(browser_path: str | None = None) -> str:
    """Find Chrome/Chromium binary. Raises RuntimeError if not found."""
    if browser_path:
        if Path(browser_path).is_file() or shutil.which(browser_path):
            return browser_path
        raise RuntimeError(f"Browser not found at specified path: {browser_path}")

    # Platform-specific candidates
    candidates = list(_CHROME_CANDIDATES_LINUX)
    if sys.platform == "darwin":
        candidates = _CHROME_CANDIDATES_MACOS + candidates
    elif _IS_WINDOWS:
        for prog_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            prog_dir = os.environ.get(prog_var, "")
            if prog_dir:
                for rel in _CHROME_CANDIDATES_WINDOWS:
                    candidates.append(str(Path(prog_dir) / rel))

    for name in candidates:
        path = shutil.which(name) or (name if Path(name).is_file() else None)
        if path:
            logger.info("Found Chrome: %s", path)
            return path

    raise RuntimeError(
        "Chrome/Chromium not found in PATH. Install Chrome or pass browser_path explicitly.\n"
        f"Searched: {candidates}"
    )


class BrowserLauncher(BaseLauncher):
    """Launch Chrome for browser JavaScript debugging via pwa-chrome."""

    @property
    def language_id(self) -> str:
        return "browser"

    @property
    def adapter_id(self) -> str:
        return "pwa-chrome"

    def output_filter(self, line: str) -> bool:
        return not any(pattern in line for pattern in _NOISE_PATTERNS)

    def get_dap_request_type(self) -> str:
        return "launch"

    def get_dap_arguments(self, program: str, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        args: dict[str, Any] = {
            "type": "pwa-chrome",
            "request": "launch",
            "url": program,
            "webRoot": cwd or str(Path.cwd()),
        }
        if kwargs.get("stop_on_entry"):
            args["stopOnEntry"] = True
        if kwargs.get("headless"):
            args["runtimeArgs"] = ["--headless=new"]
        browser_path = kwargs.get("browser_path")
        if browser_path:
            args["runtimeExecutable"] = browser_path
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
        work_dir = cwd or str(Path.cwd())

        # Verify Chrome is available (before starting the adapter)
        browser_path = kwargs.get("browser_path")
        chrome = _find_chrome(browser_path)
        kwargs["browser_path"] = chrome

        adapter_process, host, _ = await _launch_js_debug_adapter(work_dir, port, env)

        return LaunchResult(
            process=adapter_process,
            host=host,
            port=port,
            extra_info={
                "browser": chrome,
                "adapter": "js-debug",
                "mode": "pwa-chrome",
            },
        )
