# claude-mcp-debugger installer for Windows
# Usage: irm https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/bastiencb/claude-mcp-debugger.git"
$InstallDir = "$env:USERPROFILE\.claude\mcp_debugger"

Write-Host "=== claude-mcp-debugger installer ==="

# ── Checks ─────────────────────────────────────────────────────

# Python 3.10+
try {
    $pyVersion = & python -c "import sys; print(sys.version_info.minor)" 2>$null
    if ([int]$pyVersion -lt 10) {
        Write-Error "Python 3.10+ is required (found 3.$pyVersion)."
        exit 1
    }
} catch {
    Write-Error "python is required but not found. Install it from https://python.org"
    exit 1
}

# git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git is required but not found."
    exit 1
}

# ── Install files ─────────────────────────────────────────────

if (Test-Path $InstallDir) {
    Write-Host "Updating existing installation in $InstallDir..."
    git -C $InstallDir pull --ff-only 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: could not pull updates, using existing files."
    }
} else {
    Write-Host "Installing to $InstallDir..."
    $TempDir = "$env:TEMP\claude-mcp-debugger-install"
    if (Test-Path $TempDir) { Remove-Item -Recurse -Force $TempDir }
    git clone --depth 1 $Repo $TempDir
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) | Out-Null
    Copy-Item -Recurse "$TempDir\mcp_debugger" $InstallDir
    Remove-Item -Recurse -Force $TempDir
}

# ── Create venv and install dependencies ──────────────────────

$VenvDir = "$InstallDir\.venv"
$VenvPython = "$VenvDir\Scripts\python.exe"

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment..."
    & python -m venv $VenvDir
}

Write-Host "Installing dependencies..."
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet "mcp[cli]>=1.0" debugpy

# ── Register MCP server ──────────────────────────────────────

$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if ($claudeCmd) {
    Write-Host "Registering debugger with Claude Code..."
    try {
        & claude mcp add -s user -t stdio debugger -- $VenvPython -m mcp_debugger 2>$null
        Write-Host "MCP server registered via 'claude mcp add'."
    } catch {
        Write-Host "Warning: 'claude mcp add' failed. You may need to register manually."
        Write-Host "Run: claude mcp add -s user -t stdio debugger -- $VenvPython -m mcp_debugger"
    }
} else {
    Write-Host ""
    Write-Host "Claude Code CLI not found in PATH."
    Write-Host "Register the server manually by running:"
    Write-Host "  claude mcp add -s user -t stdio debugger -- $VenvPython -m mcp_debugger"
    Write-Host ""
    Write-Host "Or add to ~\.claude.json under mcpServers:"
    $VenvPythonFwd = $VenvPython -replace '\\', '/'
    Write-Host "  `"debugger`": {"
    Write-Host "    `"type`": `"stdio`","
    Write-Host "    `"command`": `"$VenvPythonFwd`","
    Write-Host "    `"args`": [`"-m`", `"mcp_debugger`"],"
    Write-Host "    `"env`": {`"PYTHONPATH`": `"$($env:USERPROFILE -replace '\\', '/')/.claude`"}"
    Write-Host "  }"
}

# ── Done ───────────────────────────────────────────────────────

Write-Host ""
Write-Host "Installation complete!"
Write-Host "Restart Claude Code to activate the debugger."
