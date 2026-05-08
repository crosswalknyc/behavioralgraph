#!/bin/bash
# Startup script for Render deployment.
# Workers/threads/timeout can be overridden via env: GUNICORN_WORKERS, GUNICORN_THREADS, GUNICORN_TIMEOUT.

echo "🚀 Starting application..."
echo "PORT: ${PORT:-10000}"

WORKERS="${GUNICORN_WORKERS:-1}"
THREADS="${GUNICORN_THREADS:-4}"
TIMEOUT="${GUNICORN_TIMEOUT:-600}"
echo "Gunicorn: workers=${WORKERS} threads=${THREADS} timeout=${TIMEOUT}"

# Use sync workers (NOT eventlet) to avoid DNS resolution issues with snowflake-connector.
# Eventlet's greendns has incompatibilities with newer dnspython versions.
#
# CRITICAL: do NOT use `--max-requests`. The BG dashboard runs an in-process
# job queue: heavy profile pipelines execute on a background thread that lives
# inside the gunicorn worker. `--max-requests` recycles the worker every N HTTP
# requests, which:
#   • kills any pipeline currently executing on that worker (jobs go to
#     "Crashed: no heartbeat"),
#   • orphans every queued job (in-memory queue is wiped on import),
#   • resets all in-process state (heavy_throttle counters, CH-throttle stats,
#     heartbeat pulser thread).
# At a few hundred dashboard polls/hour this fires multiple times per day. On
# the Starter plan it was a defense against memory leaks; on Pro Ultra with
# 32 GiB RAM it does nothing useful and actively breaks long-running runs.
exec gunicorn app:app \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers "${WORKERS}" \
    --threads "${THREADS}" \
    --timeout "${TIMEOUT}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --keep-alive 5 \
    --log-level warning
