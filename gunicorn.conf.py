"""Gunicorn config for the Behavioral Graph webapp on Render.

Render's HTTP health check (/healthz) times out after 5 seconds. Without
preload, each worker re-imports the full app (pandas + boto3 + flask +
clickhouse-connect + 27k-line app.py + bg.py once it's lazily touched) in
parallel and competes for the same CPU cores. On a cold start this can
exceed 5s before the workers bind to $PORT, causing Render to mark the
instance unhealthy and try to spin up a replacement which itself can't
boot in 5s -> "Instance failed: HTTP health check failed" loops, often
appearing nightly when Render rolls a host or our long-running Profile
Analysis makes the existing instance look unresponsive.

With preload=True, the master process does the heavy import ONCE and
forks workers that are already loaded. Workers bind to $PORT and start
serving /healthz immediately after fork, dropping cold-start health-check
risk dramatically. Memory is also lower because forked workers share
COW-mapped pages until they mutate.

Render's start command picks up this file automatically when present at
the working directory passed to gunicorn:
    gunicorn app:app --bind 0.0.0.0:$PORT
"""
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = int(os.environ.get('WEB_THREADS', '4'))
worker_class = 'gthread'
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '300'))
graceful_timeout = int(os.environ.get('GUNICORN_GRACEFUL_TIMEOUT', '30'))
keepalive = int(os.environ.get('GUNICORN_KEEPALIVE', '5'))

# THE important one. Loads app once in the master, then forks workers from
# the already-loaded process. Workers bind to $PORT and start serving
# /healthz immediately after fork.
preload_app = True

# Recycle workers occasionally to release any slow leaks (S3 client cache,
# pandas DataFrame fragments, etc.) without disrupting traffic.
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', '2000'))
max_requests_jitter = int(os.environ.get('GUNICORN_MAX_REQUESTS_JITTER', '200'))

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')


def post_fork(server, worker):
    """Reset any random / boto3 client state that shouldn't be shared post-fork."""
    try:
        import random
        random.seed()
    except Exception:
        pass


def when_ready(server):
    server.log.info("[gunicorn] master ready; %d workers about to fork (preload=%s)", workers, preload_app)
