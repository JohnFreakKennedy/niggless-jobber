#!/usr/bin/env bash
# Run once after cloning to activate shared git hooks and install dependencies.
#
#   bash setup_hooks.sh
#
# What this does:
#   1. Points git at .githooks/ so pre-commit, post-checkout, and post-merge
#      hooks are picked up automatically.
#   2. Runs the dependency installer immediately so the repo is ready to use.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "[setup] Activating git hooks in .githooks/ ..."
git config core.hooksPath .githooks
echo "[setup] Git hooks activated."
echo ""
echo "[setup] Hooks enabled:"
echo "  pre-commit   -- runs pytest before every commit"
echo "  post-checkout -- installs dependencies on branch switch / fresh clone"
echo "  post-merge   -- installs dependencies after git pull"
echo ""

# Run the dependency installer right now so the venv is ready immediately.
HOOK_NAME="setup"
export HOOK_NAME
# shellcheck source=.githooks/_install_deps.sh
source "$REPO_ROOT/.githooks/_install_deps.sh"
