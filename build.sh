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

# Remove nanoarrow C extension to force Snowflake connector to use JSON
# result format. The nanoarrow parser has a bug that crashes on certain
# numeric values ('Invalid value X for dtype float64').
echo "🔧 Removing nanoarrow to force JSON result format..."
python -c "
import os, glob
import snowflake.connector as sc
d = os.path.dirname(sc.__file__)
removed = 0
for pattern in ['nanoarrow_arrow_iterator*', 'nanoarrow_cpp/*']:
    for f in glob.glob(os.path.join(d, pattern)):
        try:
            os.remove(f)
            print(f'  Removed: {os.path.basename(f)}')
            removed += 1
        except Exception as e:
            print(f'  Could not remove {f}: {e}')
print(f'  Removed {removed} nanoarrow files')
"

echo "✅ Build complete!"
