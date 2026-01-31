#!/bin/bash
# Startup script for Render deployment
# Optimized for fast startup and health check response

echo "🚀 Starting application..."
echo "PORT: ${PORT:-10000}"

# Start Gunicorn with optimized settings for Render
# Use eventlet worker for WebSocket/real-time collaboration (when flask-socketio is installed)
# --timeout: Increased to 300s for long-running requests
# --graceful-timeout: Time to wait for workers to finish on shutdown
# --access-logfile -: Log to stdout
# --error-logfile -: Log errors to stderr
if python -c "import flask_socketio" 2>/dev/null; then
    exec gunicorn app:app \
        --worker-class eventlet \
        -w 1 \
        --bind 0.0.0.0:${PORT:-10000} \
        --timeout 300 \
        --graceful-timeout 30 \
        --access-logfile - \
        --error-logfile - \
        --keep-alive 5 \
        --max-requests 1000 \
        --max-requests-jitter 50 \
        --log-level warning
else
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
fi
