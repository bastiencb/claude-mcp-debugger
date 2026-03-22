#!/usr/bin/env bash
set -euo pipefail

# claude-mcp-debugger installer
# Usage: curl -fsSL https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.sh | bash

REPO="https://github.com/bastiencb/claude-mcp-debugger.git"
INSTALL_DIR="$HOME/.claude/mcp_debugger"
MCP_CONFIG="$HOME/.claude/.mcp.json"

# ── Checks ─────────────────────────────────────────────────────

echo "=== claude-mcp-debugger installer ==="

# Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found." >&2
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.minor}")')
if [ "$PY_VERSION" -lt 10 ]; then
    echo "Error: Python 3.10+ is required (found 3.$PY_VERSION)." >&2
    exit 1
fi

# git
if ! command -v git &>/dev/null; then
    echo "Error: git is required but not found." >&2
    exit 1
fi

# ── Install ────────────────────────────────────────────────────

if [ -d "$INSTALL_DIR" ]; then
    echo "Updating existing installation in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || {
        echo "Warning: could not pull updates, using existing files."
    }
else
    echo "Installing to $INSTALL_DIR..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO" /tmp/claude-mcp-debugger-install
    cp -r /tmp/claude-mcp-debugger-install/mcp_debugger "$INSTALL_DIR"
    rm -rf /tmp/claude-mcp-debugger-install
fi

# ── MCP configuration ─────────────────────────────────────────

if [ ! -f "$MCP_CONFIG" ]; then
    echo '{}' > "$MCP_CONFIG"
fi

# Check if already configured
if python3 -c "
import json, sys
with open('$MCP_CONFIG') as f:
    cfg = json.load(f)
if 'debugger' in cfg.get('mcpServers', {}):
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
    echo "MCP config already contains 'debugger' entry, skipping."
else
    echo "Adding 'debugger' entry to $MCP_CONFIG..."
    python3 -c "
import json
with open('$MCP_CONFIG') as f:
    cfg = json.load(f)
cfg.setdefault('mcpServers', {})['debugger'] = {
    'command': 'python3',
    'args': ['-m', 'mcp_debugger'],
    'cwd': '$HOME/.claude',
    'env': {'PYTHONPATH': '$HOME/.claude'}
}
with open('$MCP_CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
"
fi

# ── Done ───────────────────────────────────────────────────────

echo ""
echo "Installation complete!"
echo "The debugger will auto-install its dependencies (mcp, debugpy) on first use."
echo ""
echo "Restart Claude Code to activate the debugger."
