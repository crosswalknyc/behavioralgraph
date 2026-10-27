#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# refresh_streaming_auth.sh — FALLBACK/emergency Max + Netflix auth refresh.
#
# PRIMARY path (what keeps them "always searching") is the shared session
# donation: `donate_cookies.py netflix.com hbomax.com`, auto-re-donated daily and
# read live from S3 by prod. Netflix needs nothing here; Max only needs this when
# its stable x-disco CLIENT HEADERS change (rare) — the donation refreshes Max's
# cookies, but not its headers.
#
# Use THIS when the donation route is unavailable or you must push a self-captured
# token straight to Render: it re-captures both tokens on a desktop that has the
# logged-in profiles and pushes them to Render (prod + dev) env vars, then
# redeploys prod.
#
#   bash streamscout/refresh_streaming_auth.sh
#
# Needs in .env.local:  MAX_EMAIL/MAX_PASSWORD, NETFLIX_EMAIL/NETFLIX_PASSWORD
# (for the capture) and RENDER_API_KEY (to push to Render). The first capture of
# the day may pop a browser window for a one-time device code — run with a
# desktop/display available.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"                         # streamscout/
ROOT="$(cd .. && pwd)"
ENV_FILE="$ROOT/.env.local"

PROD="srv-d5gpci5actks73cr4lig"              # behavioralgraph  (production)
DEV="srv-d6ktpffgi27c73fk2v30"               # behavioral-graph-dev

# 1) Re-capture fresh tokens on this desktop ----------------------------------
echo "== Refreshing Max auth =="
python3 max_episode_identifier.py --refresh-auth
echo "== Refreshing Netflix auth =="
python3 netflix_title_identifier.py --save-auth

MAX_FILE="${MAX_AUTH_PATH:-$HOME/.max_auth.json}"
NF_FILE="${NETFLIX_AUTH_PATH:-$HOME/.netflix_auth.json}"

# 2) Push tokens to Render env vars (prod + dev) ------------------------------
export "$(grep -E '^RENDER_API_KEY=' "$ENV_FILE" | xargs)"
: "${RENDER_API_KEY:?RENDER_API_KEY not found in $ENV_FILE}"

echo "== Pushing tokens to Render =="
# JSON-encode each token file as a single string value — never printed to stdout.
MAX_FILE="$MAX_FILE" NF_FILE="$NF_FILE" PROD="$PROD" DEV="$DEV" \
RENDER_API_KEY="$RENDER_API_KEY" python3 - <<'PY'
import json, os, urllib.request, urllib.error
api = os.environ["RENDER_API_KEY"]
jobs = [(os.environ["PROD"], "MAX_AUTH_JSON", os.environ["MAX_FILE"]),
        (os.environ["PROD"], "NETFLIX_AUTH_JSON", os.environ["NF_FILE"]),
        (os.environ["DEV"], "MAX_AUTH_JSON", os.environ["MAX_FILE"]),
        (os.environ["DEV"], "NETFLIX_AUTH_JSON", os.environ["NF_FILE"])]
for svc, key, fpath in jobs:
    val = open(fpath).read()
    body = json.dumps({"value": val}).encode()
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{svc}/env-vars/{key}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {api}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        r = urllib.request.urlopen(req)
        print(f"   {key:17s} -> {svc}: HTTP {r.status} ({len(val)} bytes)")
    except urllib.error.HTTPError as e:
        print(f"   {key:17s} -> {svc}: HTTP {e.code} {e.read().decode()[:160]}")
PY

# 3) Redeploy prod so the refreshed env vars take effect ----------------------
echo "== Triggering prod redeploy =="
curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
     -H "Content-Type: application/json" \
     "https://api.render.com/v1/services/$PROD/deploys" \
     -d '{"clearCache":"do_not_clear"}' >/dev/null \
  && echo "   prod redeploy queued."
echo "Done. (Dev is suspended; its vars apply on next resume/deploy.)"
