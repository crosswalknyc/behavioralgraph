#!/bin/bash
# Watch for file changes and auto-commit to GitHub
# Requires: fswatch (install via: brew install fswatch)
# Usage: ./scripts/watch_and_commit.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if ! command -v fswatch &> /dev/null; then
    echo "⚠️ fswatch not found. Install with: brew install fswatch"
    echo "Or run ./scripts/auto_commit.sh manually when you have changes."
    exit 1
fi

echo "👀 Watching for changes (Ctrl+C to stop)..."
echo "   Changes will be auto-committed every 30 seconds if modified."
echo ""

LAST_COMMIT=0
while true; do
    # Wait for file changes (debounce 2 sec)
    fswatch -1 -r -E .git -E __pycache__ -E .venv -E node_modules . 2>/dev/null
    
    NOW=$(date +%s)
    if [ $((NOW - LAST_COMMIT)) -gt 30 ]; then
        if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
            echo ""
            echo "📝 Changes detected at $(date '+%H:%M:%S') - committing..."
            git add -A
            git commit -m "Auto-commit: $(date '+%Y-%m-%d %H:%M:%S')" 2>/dev/null && git push origin main 2>/dev/null && echo "✅ Pushed" || echo "⚠️ Commit/push skipped"
            LAST_COMMIT=$NOW
        fi
    fi
done
