# bg-webapp on Hetzner - deploy artifacts

Migration prep for moving the Prometheus / Profile IQ / Subscriber IQ / Digital
Journey IQ dashboard from Render `pro_ultra` to the Hetzner box that already
runs ClickHouse + the build queue worker. Saves roughly $472/mo at zero
marginal cost.

Everything here is INFRA + CONFIG. No app code is touched. DNS cutover is
deliberately NOT executed by this workstream; the last step below is
"awaiting Jenna's go".

## What's in this directory

| File | Purpose |
|---|---|
| `bg-webapp.service` | systemd unit for the Flask + gunicorn service |
| `nginx.conf` | reverse-proxy config, TLS-ready, rate limit on `/api/`, 444 for unknown Host |
| `env.example` | template for `/etc/bg-webapp/env` (secrets stay OUT of the repo) |
| `deploy.sh` | one-line deploy: `git pull --ff-only && [re-pip if needed] && systemctl reload` |
| `crons/iq-rankers-daily.{service,timer}` | daily Layer-1 sentiment refresh (09:00 UTC) |
| `crons/microdramas-scrapers.{service,timer}` | daily microdrama scrape (05:30 UTC) |
| `crons/activity-export.{service,timer}` | activity exports (currently suspended on Render, staged) |

## What's already live on Hetzner (as of migration prep)

- `bg-webapp` fresh clone at `/opt/bg-webapp` (branch `main`), venv at `.venv`
- `nginx` installed + `bg-webapp` site enabled at `/etc/nginx/sites-{available,enabled}/bg-webapp`
- `certbot` in isolated venv at `/opt/certbot` (symlinked to `/usr/local/bin/certbot`)
- systemd unit `bg-webapp.service` installed + enabled + active on `127.0.0.1:8000`
- systemd cron units installed but **timers stopped and disabled** until cutover
- `/etc/bg-webapp/env` seeded with real Anthropic + OpenAI + AWS + ClickHouse creds
  from the existing Hetzner env; SECRET_KEY is a TEMPORARY staging value

## Env vars still to paste from Render

The following 12 keys in `/etc/bg-webapp/env` are placeholders
(`REPLACE_WITH_RENDER_*`) and MUST be filled from the Render dashboard
(`https://dashboard.render.com/web/srv-d5gpci5actks73cr4lig` -> Environment)
before cutover:

| Env key | Comes from |
|---|---|
| `SECRET_KEY` | **CRITICAL**: Render env var of the same name; byte-identical or every session is invalidated |
| `APP_PASSWORD` | Render env `APP_PASSWORD` |
| `CRON_SECRET` | Render env `CRON_SECRET` (shared between web + crons) |
| `SNOWFLAKE_USER` | Render env |
| `SNOWFLAKE_PASSWORD` | Render env |
| `SNOWFLAKE_ACCOUNT` | Render env |
| `SNOWFLAKE_TOKEN` | Render env |
| `GOOGLE_CLIENT_ID` | Render env |
| `GOOGLE_CLIENT_SECRET` | Render env |
| `SMTP_PASSWORD` | Render env |
| `BG_DISPATCH_TOKEN` | Render env |
| `PUSH_CACHE_SECRET` | Render env |
| `GITHUB_TOKEN` | Render env |

To paste: SSH to `root@168.119.215.48`, edit `/etc/bg-webapp/env` with vi or
nano (mode 0600, keep it that way), replace the `REPLACE_WITH_*` values with
the live Render values, save, then `systemctl restart bg-webapp.service`.

Do NOT commit `/etc/bg-webapp/env` to git. The `env.example` in this repo is
the sanitized template.

## Ports + interfaces

- gunicorn binds only to `127.0.0.1:8000` (never public)
- nginx listens on `:80` and (post-TLS) `:443`
- The eventual `:8123` ClickHouse lockdown from the `ch_firewall` workstream
  will not affect this app: `CH_HOST=127.0.0.1` in the env file. The web
  service always talks to CH over loopback.

## systemd caps to protect against build-burst starvation

The unit sets:
- `CPUWeight=100` (default weight; queue workers will get lower weight later
  so under contention the web wins CPU scheduling)
- `MemoryMax=8G` hard cap (peak observed under load: 655 MB)
- `MemoryHigh=6G` soft warning
- `LimitNOFILE=65536`
- `TasksMax=4096`

Load test peak was 655 MB and 2,524 req/s to /healthz at 200 concurrent
without a single 500. The 8G cap is a wide margin against a memory leak.

## Load smoke results (from staging, 2026-09-04 UTC)

Peer numbers with box background load ~50%, ClickHouse hot, no build in
flight during the tests:

| Endpoint | Requests | Concurrent | p50 | p95 | p99 | Throughput | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| `/healthz` | 2000 | 100 | 23 ms | 38 ms | 63 ms | 3,654 req/s | 0 |
| `/api/version` | 1000 | 50 | 7 ms | 20 ms | 30 ms | 5,444 req/s | 0 (post rate-limit bump) |
| `/login` | 500 | 30 | 6 ms | 9 ms | 24 ms | 4,180 req/s | 0 |
| `/healthz` peak | 5000 | 200 | 41 ms | 185 ms | 635 ms | 2,524 req/s | 1 x 502 (0.02%) |

Memory peak across the whole sweep: **655 MB** (vs. the 8 GiB cap).

ClickHouse round-trip from the app venv: `SELECT count() FROM system.tables`
in 12 ms, `SELECT count() FROM reference.host_mapping` (43,414 rows) in 6 ms.
Trivially fast now that CH is local instead of cross-region.

SES: `no_reply@crosswalknyc.com` verified, `crosswalknyc.com` domain
verified, quota 50,000/24h, rate 14/s.

## Deploy story

`box-code-sync.timer` (already installed on this box, unrelated to this
workstream) runs every 90 s and fast-forwards `/root/finished_codes` to
`origin/main`. It uses `--ignore-submodules=all`, so it never touches
`bg-webapp/`.

**bg-webapp on Hetzner is a separate clone at `/opt/bg-webapp`.** Deploy is:

```bash
ssh root@168.119.215.48
/opt/bg-webapp/deploy/hetzner/deploy.sh
```

That script:
1. `git fetch --prune origin main`
2. `git pull --ff-only origin main`
3. If `requirements.txt` changed, `.venv/bin/pip install -r requirements.txt`
4. `systemctl reload bg-webapp.service` (SIGHUP -> graceful worker refresh)
5. `curl 127.0.0.1:8000/healthz` sanity check

Failure at any step exits non-zero and leaves the previous version serving.

If you want to automate this on push:
- Option A (recommended): use a GitHub Actions `workflow_dispatch` that
  SSH's in and runs `deploy.sh`. Zero infrastructure on Hetzner.
- Option B: install a systemd path unit on `/opt/bg-webapp/.git/HEAD` that
  triggers `deploy.sh` when the file changes. Simpler; no external creds.
- Option C: keep it manual (fine given the low deploy frequency of the
  Flask app in this codebase).

Not automating here on purpose. Jenna picks after cutover.

## TLS

`certbot` is installed but no cert has been issued because Jenna needs to
pick the staging hostname first. When she picks (say `staging-app.crosswalknyc.com`):

```bash
# 1. Set up DNS: A record staging-app.crosswalknyc.com -> 168.119.215.48
# 2. Issue cert (nginx-mode; certbot rewrites the site in place):
certbot --nginx \
    -d staging-app.crosswalknyc.com \
    --non-interactive --agree-tos --email jenna@crosswalknyc.com \
    --redirect --hsts
# 3. Auto-renew is already wired via /etc/systemd/system/certbot.timer on
#    the base Ubuntu install; verify with `systemctl list-timers | grep certbot`.
```

Renewal is auto; certificate expires in 90 days and certbot renews ~30 days
before that.

## DNS cutover plan (DO NOT EXECUTE without Jenna's go)

`https://behavioralgraph.onrender.com` is the current prod URL. The domain is
`behavioralgraph.onrender.com` (Render's own subdomain) so we cannot
CNAME-hijack it. To fully cut over we need to pick a Crosswalk-owned domain
(likely `behavioralgraph.crosswalknyc.com` or `app.crosswalknyc.com`) and
route Jenna + all clients there.

Two-phase cutover, safest order:

**Phase 1: point a new Crosswalk domain at Hetzner (no user impact)**
1. Add a DNS A record: `behavioralgraph.crosswalknyc.com` -> `168.119.215.48`
2. Set TTL to 60 s
3. Issue TLS cert for the new hostname:
   ```
   certbot --nginx -d behavioralgraph.crosswalknyc.com \
       --non-interactive --agree-tos --email jenna@crosswalknyc.com \
       --redirect --hsts
   ```
4. Verify from a laptop:
   ```
   curl -I https://behavioralgraph.crosswalknyc.com/healthz  # expect 200
   curl -I https://behavioralgraph.crosswalknyc.com/login    # expect 200
   ```
5. Do a real login (with the pasted `APP_PASSWORD`) and click through a few
   Profile IQ views. Confirm ClickHouse-backed reads are visibly faster than
   Render.

**Phase 2: send prod traffic to the new domain**
Because `behavioralgraph.onrender.com` is owned by Render, we cannot flip it
directly. Options:

- **Preferred**: put a 302 redirect on Render itself pointing at
  `behavioralgraph.crosswalknyc.com`, or configure Render to serve a static
  HTML page that redirects. Users' bookmarks + any hardcoded URLs update
  themselves within a session.
- **Alternative**: message Jenna's team + the API partners to switch base
  URLs. Track adoption via the Render access log.

Rollback (in either phase):
- Phase 1: remove the new DNS record + delete the cert; no user impact.
- Phase 2: revert the Render-side redirect; TTL 60 s means users bounce back
  to Render within a minute.

Keep the Render web service warm for **48 hours** after Phase 2 as a
rollback insurance. After 48 h of clean traffic on Hetzner, suspend the
Render web service (do not delete for another week).

## Cron migration

The 3 Render crons are staged as systemd services + timers here. **All 3
timers are installed but stopped and disabled** until cutover. Enable at
cutover:

```bash
# On Hetzner:
systemctl enable --now iq-rankers-daily.timer
systemctl enable --now microdramas-scrapers.timer
# activity-export is currently suspended on Render; only enable if Jenna wants it running:
# systemctl enable --now activity-export.timer
```

Then verify next-fire times:
```bash
systemctl list-timers iq-rankers-daily microdramas-scrapers activity-export
```

At the same time, **suspend the Render crons** so they don't double-fire:
- `iq-rankers-daily-cron-dev` (`crn-d85n22v7f7vs73cp25fg`)
- `microdramas-scrapers-cron` (`crn-d9kebinavr4c73aqtfag`)
- `microdramas-scrapers-daily-cron` (`crn-d9jp7iad0e5s7395c5rg`) - the HTTP-wrapper twin; also suspend

After 48 h of the Hetzner cron running clean, delete the Render crons.

## Risks + unknowns to flag before cutover

1. **`SECRET_KEY` mismatch invalidates sessions.** Every open session gets a
   401 the moment the box takes traffic if this is wrong. Verify twice
   before flipping DNS.
2. **The `/root/finished_codes/` `box-code-sync` is currently in an ff-fail
   state** (Gen_Pop_2026.csv locally dirty + untracked
   `migration/kartel_api_usage_report.py`, etc.). This is an unrelated
   incident but worth noting because the queue worker is running slightly
   stale code (commit `1b11b6d1` vs. origin `35b7e223`). Unblock separately.
3. **Trends scraper cron** still runs from `/root/finished_codes/bg-webapp`
   (populated by rsync) with the old crontab entry. Post-cutover, migrate
   those scrapers to `/opt/bg-webapp` too; not required for this workstream.
4. **The ClickHouse `:8123` port** is still open to the public internet on
   this box. The `ch_firewall` workstream is closing 9000/9004/9005/9009 and
   deferring 8123. Once :8123 is locked down externally, our app is
   unaffected (`CH_HOST=127.0.0.1`), but any client that was hitting
   `168.119.215.48:8123` externally will break. Coordinate before the
   :8123 lockdown.
5. **We inherited AWS creds from `~/.aws/credentials`** and copied them into
   `/etc/bg-webapp/env`. The Render-live values may differ; verify at
   cutover (in particular the S3 access + SES send scopes).
6. **The Render `render.yaml` in this repo is out of date** (references
   `behavioral-graph` which is suspended; the live prod service is
   `behavioralgraph`). Leave it alone during this workstream; it's not
   consulted by the running Render service (settings are stored in Render's
   own DB, edited via the dashboard).

## Time-and-effort estimate for the actual DNS cutover

Once Jenna gives the go:

| Step | Estimate |
|---|---|
| Paste the 12 remaining env-var placeholders | 5-10 min |
| `systemctl restart bg-webapp.service` + verify | 1 min |
| Add DNS A record + wait for propagation (TTL 60 s) | 2-5 min |
| Issue TLS cert via certbot | 1-2 min |
| End-to-end smoke via HTTPS from a laptop | 5 min |
| Enable systemd timers, suspend Render crons | 2 min |
| Post-flip watch: journalctl -f + nginx access log | 30-60 min |
| **Total: <15 min hands-on, <90 min including watch** | |

## Operational one-liners

```bash
# Live logs
journalctl -u bg-webapp.service -f

# nginx access log (JSON, machine-parseable)
tail -f /var/log/nginx/bg-webapp.access.log

# Reload after a config change
nginx -t && systemctl reload nginx

# Restart just the app (drops in-flight requests after 45s)
systemctl restart bg-webapp.service

# Graceful worker refresh (no dropped requests)
systemctl reload bg-webapp.service     # -> SIGHUP to gunicorn master

# Check the timers
systemctl list-timers iq-rankers-daily microdramas-scrapers activity-export

# Fire a cron unit manually
systemctl start iq-rankers-daily.service

# Deploy latest main
/opt/bg-webapp/deploy/hetzner/deploy.sh
```
