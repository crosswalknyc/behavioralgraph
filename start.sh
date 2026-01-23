#!/bin/bash
# Startup script for Render deployment
# Optimized for fast startup and health check response

echo "🚀 Starting application..."
echo "PORT: ${PORT:-10000}"

# Start Gunicorn with optimized settings for Render
# --preload: Load app before forking workers (faster startup, but uses more memory)
# --timeout: Increased to 300s for long-running requests
# --graceful-timeout: Time to wait for workers to finish on shutdown
# --access-logfile -: Log to stdout
# --error-logfile -: Log errors to stderr
# --log-level info: Set log level
exec gunicorn app:app \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers 1 \
    --threads 2 \
    --timeout 300 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --log-level warning
