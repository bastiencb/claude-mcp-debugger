"""Java debug launcher using Eclipse JDT LS + java-debug plugin.

Architecture:
1. Download JDT LS and java-debug plugin on first use
2. Launch JDT LS headless with the java-debug bundle
3. Communicate via LSP to resolve main class, classpath, and start a DAP session
4. Return the DAP port for our standard DAPClient to connect to
"""

import asyncio
import hashlib
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .base import BaseLauncher, LaunchResult
from .lsp_client import LSPClient

logger = logging.getLogger(__name__)

# ── Storage paths ───────────────────────────────────────────────

_JAVA_DEBUG_BASE = Path(__file__).parent.parent / ".adapters" / "java-debug"
_JDTLS_DIR = _JAVA_DEBUG_BASE / "jdtls"
_JAVA_DEBUG_PLUGIN_DIR = _JAVA_DEBUG_BASE / "plugin"
_WORKSPACE_DATA_DIR = _JAVA_DEBUG_BASE / "workspace-data"

# ── Download URLs ───────────────────────────────────────────────

_JDTLS_VERSION = "1.40.0"
_JDTLS_TIMESTAMP = "202409261450"
_JDTLS_URL = (
    f"https://download.eclipse.org/jdtls/milestones/{_JDTLS_VERSION}/"
    f"jdt-language-server-{_JDTLS_VERSION}-{_JDTLS_TIMESTAMP}.tar.gz"
)

# java-debug plugin from Open VSX (VSIX is a ZIP)
_JAVA_DEBUG_VSIX_VERSION = "0.58.1"
_JAVA_DEBUG_VSIX_URL = (
    f"https://open-vsx.org/api/vscjava/vscode-java-debug/"
    f"{_JAVA_DEBUG_VSIX_VERSION}/file/vscjava.vscode-java-debug-"
    f"{_JAVA_DEBUG_VSIX_VERSION}.vsix"
)


# ── Java binary detection ───────────────────────────────────────

def _find_java(java_home: str | None = None) -> str:
    """Find the java binary. Checks JAVA_HOME, then PATH."""
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        if candidate.is_file():
            return str(candidate)

    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        candidate = Path(env_home) / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        if candidate.is_file():
            return str(candidate)

    java_bin = shutil.which("java")
    if java_bin:
        return java_bin

    raise RuntimeError(
        "Java not found. Install JDK 17+ and ensure 'java' is in PATH or set JAVA_HOME."
    )


# ── JDT LS platform config ─────────────────────────────────────

def _jdtls_config_dir() -> Path:
    """Return the platform-specific JDT LS config directory."""
    system = platform.system().lower()
    if system == "darwin":
        config_name = "config_mac"
    elif system == "windows":
        config_name = "config_win"
    else:
        config_name = "config_linux"
    return _JDTLS_DIR / config_name


def _jdtls_launcher_jar() -> Path:
    """Find the equinox launcher JAR in the JDT LS plugins directory."""
    plugins_dir = _JDTLS_DIR / "plugins"
    if not plugins_dir.is_dir():
        raise RuntimeError(f"JDT LS plugins directory not found: {plugins_dir}")
    for jar in plugins_dir.glob("org.eclipse.equinox.launcher_*.jar"):
        return jar
    raise RuntimeError(f"Equinox launcher JAR not found in {plugins_dir}")


# ── Download helpers ────────────────────────────────────────────

def _ensure_jdtls() -> Path:
    """Download and extract JDT LS if not present. Returns the launcher JAR path."""
    try:
        return _jdtls_launcher_jar()
    except RuntimeError:
        pass

    logger.info("JDT LS not found, downloading from %s", _JDTLS_URL)
    _JDTLS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with urlopen(_JDTLS_URL) as resp:
            data = resp.read()
    except Exception as e:
        raise RuntimeError(
            f"Failed to download JDT LS: {e}\n"
            f"Download manually from {_JDTLS_URL} and extract to {_JDTLS_DIR}"
        ) from e

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(path=_JDTLS_DIR, filter="data")

    jar = _jdtls_launcher_jar()
    logger.info("JDT LS installed at %s", _JDTLS_DIR)
    return jar


def _ensure_java_debug_plugin() -> Path:
    """Download java-debug plugin JAR if not present. Returns the JAR path."""
    _JAVA_DEBUG_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    for jar in _JAVA_DEBUG_PLUGIN_DIR.glob("com.microsoft.java.debug.plugin-*.jar"):
        return jar

    logger.info("java-debug plugin not found, downloading from Open VSX")

    try:
        with urlopen(_JAVA_DEBUG_VSIX_URL) as resp:
            data = resp.read()
    except Exception as e:
        raise RuntimeError(f"Failed to download java-debug plugin: {e}") from e

    # VSIX is a ZIP; extract the plugin JAR from extension/server/
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        all_names = zf.namelist()
        for name in all_names:
            if name.startswith("extension/server/com.microsoft.java.debug.plugin") and name.endswith(".jar"):
                jar_data = zf.read(name)
                jar_name = Path(name).name
                jar_path = _JAVA_DEBUG_PLUGIN_DIR / jar_name
                jar_path.write_bytes(jar_data)
                logger.info("java-debug plugin installed: %s", jar_path)
                return jar_path

    raise RuntimeError(
        f"java-debug plugin JAR not found in VSIX. "
        f"Contents: {[n for n in all_names if 'server' in n.lower()]}"
    )


# ── Eclipse project scaffolding ─────────────────────────────────

_PROJECT_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<projectDescription>
    <name>{name}</name>
    <buildSpec>
        <buildCommand>
            <name>org.eclipse.jdt.core.javabuilder</name>
        </buildCommand>
    </buildSpec>
    <natures>
        <nature>org.eclipse.jdt.core.javanature</nature>
    </natures>
</projectDescription>
"""

_CLASSPATH_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<classpath>
    <classpathentry kind="src" path=""/>
    <classpathentry kind="con" path="org.eclipse.jdt.launching.JRE_CONTAINER"/>
    <classpathentry kind="output" path=""/>
</classpath>
"""


def _ensure_eclipse_project(work_dir: str) -> str:
    """Create a minimal Eclipse .project + .classpath if none exists.

    This lets JDT LS recognize the directory as a Java project, enabling
    classpath resolution and expression evaluation. Returns the project name.
    """
    project_file = Path(work_dir) / ".project"
    classpath_file = Path(work_dir) / ".classpath"
    project_name = Path(work_dir).name

    if not project_file.exists():
        project_file.write_text(_PROJECT_TEMPLATE.format(name=project_name))
        logger.info("Created Eclipse .project in %s", work_dir)

    if not classpath_file.exists():
        classpath_file.write_text(_CLASSPATH_TEMPLATE)
        logger.info("Created Eclipse .classpath in %s", work_dir)

    return project_name


def _auto_compile(program: str, java_bin: str) -> None:
    """Compile a .java file with debug info (-g) if the .class is stale or missing."""
    source = Path(program)
    class_file = source.with_suffix(".class")

    if class_file.exists() and class_file.stat().st_mtime >= source.stat().st_mtime:
        return  # .class is up to date

    javac = shutil.which("javac")
    if not javac:
        # Try to find javac next to java
        java_path = Path(java_bin)
        candidate = java_path.parent / ("javac.exe" if sys.platform == "win32" else "javac")
        if candidate.is_file():
            javac = str(candidate)
        else:
            logger.warning("javac not found — cannot auto-compile %s", program)
            return

    logger.info("Auto-compiling %s with debug info (-g)", source.name)
    result = subprocess.run(
        [javac, "-g", str(source)],
        cwd=str(source.parent),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Compilation failed for {source.name}:\n{result.stderr}")


# ── JavaLauncher ────────────────────────────────────────────────

class JavaLauncher(BaseLauncher):
    """Launch Java programs using Eclipse JDT LS + java-debug plugin."""

    def __init__(self) -> None:
        self._lsp: LSPClient | None = None
        self._main_class: str = ""
        self._classpath: list[str] = []
        self._module_path: list[str] = []
        self._project_name: str = ""

    @property
    def language_id(self) -> str:
        return "java"

    @property
    def adapter_id(self) -> str:
        return "java"

    def output_filter(self, line: str) -> bool:
        return True

    def get_dap_request_type(self) -> str:
        return "launch"

    def get_dap_arguments(self, program: str, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        args: dict[str, Any] = {
            "type": "java",
            "request": "launch",
            "mainClass": self._main_class,
            "classPaths": self._classpath,
            "modulePaths": self._module_path,
            "projectName": self._project_name,
        }
        if cwd:
            args["cwd"] = cwd
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
        java_bin = _find_java(kwargs.get("java_home"))

        # Auto-compile with debug info if needed
        _auto_compile(program, java_bin)

        # Create Eclipse project scaffolding for standalone files
        eclipse_project_name = _ensure_eclipse_project(work_dir)

        # Download dependencies
        launcher_jar = _ensure_jdtls()
        debug_plugin_jar = _ensure_java_debug_plugin()

        # Build JDT LS command
        # Use a hash-based workspace data dir to avoid conflicts
        data_dir = _WORKSPACE_DATA_DIR / hashlib.md5(work_dir.encode()).hexdigest()[:12]
        data_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            java_bin,
            "-Declipse.application=org.eclipse.jdt.ls.core.id1",
            "-Dosgi.bundles.defaultStartLevel=4",
            "-Declipse.product=org.eclipse.jdt.ls.core.product",
            "-Dlog.level=ERROR",
            "-Xmx1G",
            "--add-modules=ALL-SYSTEM",
            "--add-opens", "java.base/java.util=ALL-UNNAMED",
            "--add-opens", "java.base/java.lang=ALL-UNNAMED",
            "-jar", str(launcher_jar),
            "-configuration", str(_jdtls_config_dir()),
            "-data", str(data_dir),
        ]

        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        # Start JDT LS via LSP client
        self._lsp = LSPClient()
        await self._lsp.start(cmd, cwd=work_dir, env=proc_env)

        # LSP handshake with java-debug bundle
        init_options = {
            "bundles": [str(debug_plugin_jar)],
        }
        await self._lsp.initialize(work_dir, init_options=init_options)

        # Wait for JDT LS to be ready (it needs time to index)
        await self._wait_for_ready()

        # Resolve main class from the .java file
        program_path = str(Path(program).resolve())
        self._main_class, self._project_name = await self._resolve_main_class(
            work_dir, program_path
        )
        # Use Eclipse project name as fallback (required for evaluate)
        if not self._project_name:
            self._project_name = eclipse_project_name

        # Resolve classpath
        self._classpath, self._module_path = await self._resolve_classpath(work_dir)

        # Start debug session — JDT LS returns a DAP port
        dap_port = await self._lsp.execute_command(
            "vscode.java.startDebugSession", []
        )
        if not isinstance(dap_port, int):
            raise RuntimeError(f"Expected DAP port (int), got: {dap_port!r}")

        logger.info("JDT LS debug session started on port %d", dap_port)

        return LaunchResult(
            process=self._lsp._process,
            host="127.0.0.1",
            port=dap_port,
            extra_info={
                "java": java_bin,
                "main_class": self._main_class,
                "adapter": "jdt-java-debug",
            },
        )

    async def _wait_for_ready(self, timeout: float = 30.0) -> None:
        """Wait for JDT LS to finish initialization.

        We try resolveMainClass in a loop — it fails until JDT LS is ready.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        last_error = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._lsp.execute_command(
                    "vscode.java.resolveMainClass", []
                )
                logger.info("JDT LS is ready")
                return
            except Exception as e:
                last_error = e
                await asyncio.sleep(1.0)
        raise TimeoutError(
            f"JDT LS did not become ready within {timeout}s. Last error: {last_error}"
        )

    async def _resolve_main_class(
        self, workspace: str, program_path: str
    ) -> tuple[str, str]:
        """Find the main class for a .java file."""
        result = await self._lsp.execute_command(
            "vscode.java.resolveMainClass", [workspace]
        )
        if not result:
            # Fallback: derive class name from filename
            class_name = Path(program_path).stem
            logger.warning("No main class resolved, using filename: %s", class_name)
            return class_name, ""

        # Match by file path
        for entry in result:
            file_path = entry.get("filePath", "")
            if file_path and Path(file_path).resolve() == Path(program_path).resolve():
                return entry["mainClass"], entry.get("projectName", "")

        # If no match, use the first one
        first = result[0]
        logger.info(
            "Using first resolved main class: %s (from %s)",
            first["mainClass"], first.get("filePath", "?"),
        )
        return first["mainClass"], first.get("projectName", "")

    async def _resolve_classpath(self, work_dir: str) -> tuple[list[str], list[str]]:
        """Resolve classpath and modulepath for the main class.

        Falls back to the working directory if JDT LS can't resolve.
        """
        try:
            result = await self._lsp.execute_command(
                "vscode.java.resolveClasspath",
                [self._main_class, self._project_name],
            )
            if isinstance(result, list) and len(result) >= 2:
                cp, mp = result[0], result[1]
                if cp or mp:
                    return cp, mp
        except Exception as e:
            logger.warning("Failed to resolve classpath via JDT LS: %s", e)

        # Fallback: use the working directory as classpath
        logger.info("Using working directory as classpath: %s", work_dir)
        return [work_dir], []

    async def cleanup(self) -> None:
        """Shut down JDT LS."""
        if self._lsp:
            await self._lsp.stop()
            self._lsp = None
