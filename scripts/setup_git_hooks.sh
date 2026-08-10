#!/usr/bin/env bash
# One-time setup: point this clone's git hooks at .githooks/ (versioned).
#
# Why not use core.hooksPath by default in git config? Because git config
# is per-clone: cloning the repo does NOT inherit any hook config. This
# script wires the local clone to run our versioned hooks.
#
# Run once after cloning:
#     ./scripts/setup_git_hooks.sh
#
# Idempotent - safe to re-run.

set -eu

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ ! -d .githooks ]; then
    echo "[setup] .githooks/ directory not found in $REPO_ROOT"
    exit 1
fi

# Make every hook executable (git requires this).
chmod +x .githooks/* 2>/dev/null || true

# Point git at the versioned hooks dir.
git config core.hooksPath .githooks

echo "[setup] git core.hooksPath set to .githooks"
echo "[setup] active hooks:"
ls -1 .githooks | sed 's/^/          /'
echo ""
echo "[setup] Done. Every commit that touches templates/index.html will now"
echo "        be validated by scripts/validate_index_html.py."
