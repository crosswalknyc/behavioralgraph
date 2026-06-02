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

echo "✅ Build complete!"
