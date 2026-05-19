#!/usr/bin/env bash
# _install_deps.sh -- shared dependency-install logic called by post-checkout
# and post-merge hooks.  Not meant to be invoked directly.
#
# Usage: source .githooks/_install_deps.sh
#   HOOK_NAME must be set by the caller (used in log messages).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
REQ_FILE="$REPO_ROOT/requirements.txt"
STAMP_FILE="$VENV_DIR/.req_stamp"

# --------------------------------------------------------------------------
# Resolve pip: prefer the repo venv, create it if it doesn't exist yet.
# --------------------------------------------------------------------------
if [ ! -f "$VENV_DIR/bin/pip" ]; then
    echo "[$HOOK_NAME] No .venv found -- creating virtual environment..."
    if command -v python3 &>/dev/null; then
        python3 -m venv "$VENV_DIR"
    else
        echo "[$HOOK_NAME] ERROR: python3 not found. Install Python 3.11+ and retry." >&2
        exit 1
    fi
fi

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

# --------------------------------------------------------------------------
# Check whether requirements.txt changed since the last install.
# We store an MD5 / sha256 of requirements.txt inside the venv as a stamp.
# --------------------------------------------------------------------------
CURRENT_HASH="$(shasum -a 256 "$REQ_FILE" 2>/dev/null | awk '{print $1}' || echo "none")"
STAMP_HASH="$(cat "$STAMP_FILE" 2>/dev/null || echo "")"

if [ "$CURRENT_HASH" = "$STAMP_HASH" ] && [ -n "$STAMP_HASH" ]; then
    echo "[$HOOK_NAME] Dependencies are up to date."
    exit 0
fi

# --------------------------------------------------------------------------
# Install / upgrade Python packages.
# --------------------------------------------------------------------------
echo "[$HOOK_NAME] Installing Python dependencies from requirements.txt..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$REQ_FILE"

# --------------------------------------------------------------------------
# Install Playwright browser (only if playwright binary is present).
# --------------------------------------------------------------------------
if "$PYTHON" -c "import playwright" 2>/dev/null; then
    echo "[$HOOK_NAME] Installing Playwright Chromium browser..."
    "$PYTHON" -m playwright install chromium --quiet 2>/dev/null || true
fi

# --------------------------------------------------------------------------
# Write the stamp so we don't re-install on the next checkout.
# --------------------------------------------------------------------------
echo "$CURRENT_HASH" > "$STAMP_FILE"

echo "[$HOOK_NAME] Dependencies installed successfully."
echo "[$HOOK_NAME] Activate the venv with:  source .venv/bin/activate"
