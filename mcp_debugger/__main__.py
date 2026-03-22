"""Standalone entry point for the MCP debugger.
Auto-installs mcp and debugpy in a dedicated venv if needed.
"""
import subprocess
import sys
from pathlib import Path

VENV = Path(__file__).parent / ".venv"
_IS_WINDOWS = sys.platform == "win32"
_BIN_DIR = "Scripts" if _IS_WINDOWS else "bin"
_DEPS = ["mcp", "debugpy"]
_DEPS_VERSION = "1"  # Bump to force reinstall on next startup


def _ensure_venv():
    pip = VENV / _BIN_DIR / "pip"
    python = VENV / _BIN_DIR / "python"
    version_file = VENV / ".deps_version"

    needs_install = not python.exists()
    needs_update = (
        not needs_install
        and (not version_file.exists() or version_file.read_text().strip() != _DEPS_VERSION)
    )

    # Redirect pip output to stderr — stdout is the MCP stdio transport
    if needs_install:
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)],
                              stdout=sys.stderr)
        subprocess.check_call([str(pip), "install", "-q"] + _DEPS,
                              stdout=sys.stderr, stderr=sys.stderr)
        version_file.write_text(_DEPS_VERSION)
    elif needs_update:
        subprocess.check_call([str(pip), "install", "-q", "--upgrade"] + _DEPS,
                              stdout=sys.stderr, stderr=sys.stderr)
        version_file.write_text(_DEPS_VERSION)

    # Inject venv site-packages into sys.path so dependencies are importable
    # without replacing the process (os.execv breaks stdio pipes in some environments)
    if not sys.prefix.startswith(str(VENV.resolve())):
        if _IS_WINDOWS:
            sp = VENV / "Lib" / "site-packages"
        else:
            sp = None
            lib_dir = VENV / "lib"
            if lib_dir.is_dir():
                for d in lib_dir.iterdir():
                    if d.name.startswith("python"):
                        sp = d / "site-packages"
                        break
        if sp and sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))


if __name__ == "__main__":
    _ensure_venv()
    from .server import run_server
    run_server()
