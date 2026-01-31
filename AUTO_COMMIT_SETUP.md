# Auto-Commit Setup for Behavioral Graph Web App

Changes are committed and pushed to **https://github.com/crosswalknyc/behavioralgraph.git**

## Current Setup

### 1. Post-Commit Hook (Auto-Push)
Every time you run `git commit`, the changes are **automatically pushed** to GitHub.

To install the hook (e.g., after fresh clone):
```bash
cd bg-webapp && cp scripts/post-commit.sample .git/hooks/post-commit && chmod +x .git/hooks/post-commit
```

### 2. Quick Auto-Commit Script
Run this to add, commit, and push all changes in one command:

```bash
cd bg-webapp
./scripts/auto_commit.sh "Your commit message"
```

Or with auto-generated message:
```bash
./scripts/auto_commit.sh
```

### 3. File Watcher (Optional)
To auto-commit whenever you save files:

```bash
# Install fswatch (macOS)
brew install fswatch

# Run watcher (keeps running, Ctrl+C to stop)
./scripts/watch_and_commit.sh
```

## Manual Commit & Push

```bash
cd bg-webapp
git add -A
git commit -m "Your message"
git push origin main   # Or just commit - post-commit hook pushes automatically
```
