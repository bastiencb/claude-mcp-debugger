"""Base classes for language-specific debug launchers."""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LaunchResult:
    """Result of launching a debug session."""

    process: subprocess.Popen
    host: str
    port: int
    extra_info: dict[str, Any] = field(default_factory=dict)
    adapter_process: subprocess.Popen | None = None


class BaseLauncher(ABC):
    """Abstract base for language-specific debug launchers.

    Each launcher knows how to:
    - Start the debug adapter + debuggee process
    - Provide the correct DAP arguments for attach/launch
    - Filter output noise from the adapter
    - Clean up adapter processes on stop
    """

    @property
    @abstractmethod
    def language_id(self) -> str:
        """Language identifier (e.g. 'python', 'node', 'java')."""

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """DAP adapter ID for the initialize handshake."""

    @abstractmethod
    async def launch(
        self,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        port: int = 5679,
        **kwargs: Any,
    ) -> LaunchResult:
        """Start the debug adapter and debuggee process.

        Returns a LaunchResult with process handle, host, port, and extra info.
        """

    @abstractmethod
    def get_dap_arguments(self, program: str, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Build the DAP attach/launch request arguments."""

    @abstractmethod
    def get_dap_request_type(self) -> str:
        """Return 'attach' or 'launch' — the DAP request command."""

    def first_code_line(self, file_path: str) -> int:
        """Return the first executable line for stop-on-entry. Default: 1."""
        return 1

    def output_filter(self, line: str) -> bool:
        """Return True to keep the output line, False to filter it out."""
        return True

    async def cleanup(self) -> None:
        """Language-specific cleanup (kill adapter process, etc.)."""
