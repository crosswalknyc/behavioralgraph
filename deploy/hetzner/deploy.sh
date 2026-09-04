#!/bin/bash
# deploy.sh - one-line deploy for bg-webapp on Hetzner.
#
# Runs from the deployed clone at /opt/bg-webapp. Steps:
#   1. Fetch + fast-forward main (never force, never rebase).
#   2. Refresh dependencies if requirements.txt changed.
#   3. SIGHUP gunicorn (graceful worker refresh, no dropped requests).
#
# There are no submodules to update - /opt/bg-webapp is a plain clone
# of the crosswalknyc/behavioralgraph repo. The parent Crosswalk repo
# lives at /root/finished_codes and is managed by box-code-sync.timer
# independently.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/bg-webapp}"
UNIT="${UNIT:-bg-webapp.service}"

cd "${REPO_DIR}"

echo "[deploy] $(date -Iseconds) - fetching origin/main"
git fetch --prune origin main

BEFORE="$(git rev-parse HEAD)"
git checkout main
git pull --ff-only origin main
AFTER="$(git rev-parse HEAD)"

if [ "${BEFORE}" = "${AFTER}" ]; then
    echo "[deploy] already at ${AFTER}; skipping reload"
    exit 0
fi

echo "[deploy] ${BEFORE} -> ${AFTER}"

# Only re-pip if requirements.txt changed. Faster + safer.
if git diff --name-only "${BEFORE}" "${AFTER}" | grep -qE '^requirements\.txt$'; then
    echo "[deploy] requirements.txt changed, updating venv"
    .venv/bin/pip install --quiet -r requirements.txt
fi

# Graceful worker refresh via SIGHUP. Gunicorn spawns new workers with
# the fresh code, waits for the old workers to drain, then reaps them.
# No dropped requests.
echo "[deploy] reloading ${UNIT}"
systemctl reload "${UNIT}" || systemctl restart "${UNIT}"

# Give gunicorn a couple seconds, then sanity-check /healthz.
sleep 3
if curl --silent --show-error --fail --max-time 10 http://127.0.0.1:8000/healthz > /dev/null; then
    echo "[deploy] healthz OK - deploy complete"
else
    echo "[deploy] WARN: /healthz did not respond in 10s; check journalctl -u ${UNIT}"
    exit 2
fi
