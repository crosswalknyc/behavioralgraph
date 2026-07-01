#!/bin/bash
# Auto-commit and push behavioral graph web app changes to GitHub
# Usage: ./scripts/auto_commit.sh [commit message]
# Run from bg-webapp directory or project root

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ -n "$1" ]; then
    MESSAGE="$1"
else
    MESSAGE="Auto-commit: $(date '+%Y-%m-%d %H:%M:%S') - deck builder and workspace updates"
fi

echo "🔄 Auto-committing behavioral graph web app..."

# Check for changes
if git diff --quiet && git diff --cached --quiet 2>/dev/null; then
    echo "✅ No changes to commit."
    exit 0
fi

git add -A
git status

git commit -m "$MESSAGE"
if [ $? -ne 0 ]; then
    echo "⚠️ Nothing to commit (no changes or empty commit)"
    exit 0
fi

git push origin main
if [ $? -eq 0 ]; then
    echo "✅ Pushed to https://github.com/crosswalknyc/behavioralgraph.git"
else
    echo "❌ Push failed. Run 'git push origin main' manually."
    exit 1
fi
