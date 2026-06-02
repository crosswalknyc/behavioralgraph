#!/bin/sh
# Fix admin role on production. Set CRON_SECRET in env or pass as first arg.
# Usage: CRON_SECRET=yoursecret ./run_repair_admin_role.sh
#    or: ./run_repair_admin_role.sh yoursecret
SECRET="${CRON_SECRET:-$1}"
URL="${APP_URL:-https://behavioralgraph.onrender.com}"
if [ -z "$SECRET" ]; then
  echo "Set CRON_SECRET or pass it as first argument."
  exit 1
fi
curl -s -X POST "$URL/api/cron/repair-admin-role" -H "X-Cron-Secret: $SECRET"
echo ""
