#!/bin/sh
# Restore users to S3 (production). Uses LOCAL users.json so you get all 33 users regardless of deploy.
# Loads CRON_SECRET from .env if present. Requires the app to be deployed (endpoint lives on Render).
# Usage: ./run_restore_users_from_deployed_file.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$SCRIPT_DIR/.env" ] && set -a && . "$SCRIPT_DIR/.env" && set +a
SECRET="${CRON_SECRET:-$1}"
URL="${APP_URL:-https://behavioralgraph.onrender.com}"
USERS_JSON="$SCRIPT_DIR/users.json"
if [ -z "$SECRET" ]; then
  echo "Set CRON_SECRET in bg-webapp/.env or pass as first argument."
  exit 1
fi
if [ ! -f "$USERS_JSON" ]; then
  echo "Missing $USERS_JSON"
  exit 1
fi
curl -s -X POST "$URL/api/cron/restore-users-from-body" \
  -H "X-Cron-Secret: $SECRET" \
  -H "Content-Type: application/json" \
  -d @"$USERS_JSON"
echo ""
