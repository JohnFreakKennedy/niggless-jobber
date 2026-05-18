#!/usr/bin/env bash
# Activate the shared git hooks in .githooks/ for this clone.
# Run once after cloning: bash setup_hooks.sh

set -euo pipefail
git config core.hooksPath .githooks
echo "Git hooks activated. Pre-commit tests will run on every git commit."
