# claude-mcp-debugger installer for Windows
# Usage: irm https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$Repo = "https://github.com/bastiencb/claude-mcp-debugger.git"
$InstallDir = "$env:USERPROFILE\.claude\mcp_debugger"
$McpConfig = "$env:USERPROFILE\.claude\.mcp.json"

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

# ── Install ────────────────────────────────────────────────────

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

# ── MCP configuration ─────────────────────────────────────────

if (-not (Test-Path $McpConfig)) {
    Set-Content -Path $McpConfig -Value "{}"
}

$cfg = Get-Content $McpConfig -Raw | ConvertFrom-Json

if ($cfg.mcpServers -and $cfg.mcpServers.debugger) {
    Write-Host "MCP config already contains 'debugger' entry, skipping."
} else {
    Write-Host "Adding 'debugger' entry to $McpConfig..."
    if (-not $cfg.mcpServers) {
        $cfg | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue ([PSCustomObject]@{})
    }
    $claudeDir = "$env:USERPROFILE\.claude" -replace '\\', '/'
    $debuggerEntry = [PSCustomObject]@{
        command = "python"
        args = @("-m", "mcp_debugger")
        cwd = $claudeDir
        env = [PSCustomObject]@{ PYTHONPATH = $claudeDir }
    }
    $cfg.mcpServers | Add-Member -NotePropertyName "debugger" -NotePropertyValue $debuggerEntry
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $McpConfig
}

# ── Done ───────────────────────────────────────────────────────

Write-Host ""
Write-Host "Installation complete!"
Write-Host "The debugger will auto-install its dependencies (mcp, debugpy) on first use."
Write-Host ""
Write-Host "Restart Claude Code to activate the debugger."
