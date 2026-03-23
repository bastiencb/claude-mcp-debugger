#!/usr/bin/env bash
set -euo pipefail

# claude-mcp-debugger installer
# Usage: curl -fsSL https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.sh | bash

REPO="https://github.com/bastiencb/claude-mcp-debugger.git"
INSTALL_DIR="$HOME/.claude/mcp_debugger"

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

# ── Install files ─────────────────────────────────────────────

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

# ── Create venv and install dependencies ──────────────────────

VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet "mcp[cli]>=1.0" debugpy

# ── Register MCP server ──────────────────────────────────────

if command -v claude &>/dev/null; then
    echo "Registering debugger with Claude Code..."
    claude mcp add -s user -t stdio -e "PYTHONPATH=$HOME/.claude" debugger -- "$VENV_PYTHON" -m mcp_debugger 2>/dev/null && {
        echo "MCP server registered via 'claude mcp add'."
    } || {
        echo ""
        echo "Warning: 'claude mcp add' failed."
        echo "Register manually from a terminal where Claude Code is available:"
        echo "  claude mcp add -s user -t stdio -e \"PYTHONPATH=\$HOME/.claude\" debugger -- $VENV_PYTHON -m mcp_debugger"
    }
else
    echo ""
    echo "Claude Code CLI not found in PATH."
    echo "Open a terminal in VS Code (or where Claude Code is installed) and run:"
    echo "  claude mcp add -s user -t stdio -e \"PYTHONPATH=\$HOME/.claude\" debugger -- $VENV_PYTHON -m mcp_debugger"
fi

# ── Done ───────────────────────────────────────────────────────

echo ""
echo "Installation complete!"
echo "Restart Claude Code to activate the debugger."
