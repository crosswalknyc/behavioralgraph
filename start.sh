#!/bin/bash
# Startup script for Render deployment
# Optimized for fast startup, health check, and memory (512MB Starter)
# Workers/threads/timeout can be overridden via env: GUNICORN_WORKERS, GUNICORN_THREADS, GUNICORN_TIMEOUT

echo "🚀 Starting application..."
echo "PORT: ${PORT:-10000}"

WORKERS="${GUNICORN_WORKERS:-1}"
THREADS="${GUNICORN_THREADS:-1}"
TIMEOUT="${GUNICORN_TIMEOUT:-300}"
echo "Gunicorn: workers=${WORKERS} threads=${THREADS} timeout=${TIMEOUT}"

# Use sync workers (NOT eventlet) to avoid DNS resolution issues with snowflake-connector
# Eventlet's greendns has incompatibilities with newer dnspython versions
exec gunicorn app:app \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers "${WORKERS}" \
    --threads "${THREADS}" \
    --timeout "${TIMEOUT}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --log-level warning
