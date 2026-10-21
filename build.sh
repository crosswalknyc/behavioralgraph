#!/bin/bash
# Optimized build script for Render
# Pre-installs heavy dependencies with caching

set -e  # Exit on error

echo "🔨 Starting optimized build..."

# Upgrade pip and build tools first
echo "📦 Upgrading pip, setuptools, wheel..."
pip install --upgrade pip setuptools wheel

# Install dependencies with optimizations
echo "📦 Installing Python dependencies..."
pip install --no-cache-dir \
    --upgrade-strategy only-if-needed \
    -r requirements.txt

# Chromium for the StreamScout US relay (2026-10-21, SiriusXM). SiriusXM's
# gateway refuses any session minted outside the US (the anonymous token
# carries the country it was minted in), and the build server sits in
# Germany, so the dashboard here in Oregon mints the session and lists the
# episodes for it. The browser lives under PLAYWRIGHT_BROWSERS_PATH, which
# the service env sets to the same path so build and runtime agree (the
# relay also defaults to this path when the directory exists). A failed
# browser install never fails the build: the relay then reports the
# platform as unverified, which is never an empty SiriusXM.
PW_DIR="${PLAYWRIGHT_BROWSERS_PATH:-/opt/render/project/.cache/ms-playwright}"
echo "🌐 Installing Chromium for the StreamScout relay into ${PW_DIR}..."
if PLAYWRIGHT_BROWSERS_PATH="${PW_DIR}" python -m playwright install chromium 2>&1 | tail -3; then
    echo "   Chromium ready."
else
    echo "   Chromium install did not complete; the relay will report SiriusXM as unverified until it does."
fi

echo "✅ Build complete!"
