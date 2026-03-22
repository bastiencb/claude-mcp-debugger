"""Launcher registry and language auto-detection."""

from pathlib import Path

from .base import BaseLauncher, LaunchResult

__all__ = ["BaseLauncher", "LaunchResult", "detect_language", "create_launcher"]

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    # Node.js / TypeScript
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ts": "node",
    ".mts": "node",
    ".cts": "node",
    # Java
    ".java": "java",
}


def detect_language(program: str) -> str:
    """Auto-detect the debug language from the file extension or URL scheme.

    Raises ValueError if the extension is not recognized.
    """
    # URLs → browser debugging
    if program.startswith(("http://", "https://")):
        return "browser"

    ext = Path(program).suffix.lower()
    lang = _EXTENSION_MAP.get(ext)
    if not lang:
        supported = ", ".join(sorted(set(_EXTENSION_MAP.values()))) + ", browser (URLs)"
        raise ValueError(
            f"Cannot detect language for '{program}' (extension: '{ext}'). "
            f"Specify language explicitly. Supported: {supported}"
        )
    return lang


def create_launcher(language: str) -> BaseLauncher:
    """Create a launcher instance for the given language.

    Raises ValueError if the language is not supported.
    """
    if language == "python":
        from .python_launcher import PythonLauncher
        return PythonLauncher()
    elif language == "node":
        from .node_launcher import NodeLauncher
        return NodeLauncher()
    elif language == "browser":
        from .browser_launcher import BrowserLauncher
        return BrowserLauncher()
    elif language == "java":
        from .java_launcher import JavaLauncher
        return JavaLauncher()
    else:
        supported = "python, node, browser, java"
        raise ValueError(f"Unsupported language: '{language}'. Supported: {supported}")
