#!/usr/bin/env python3
"""Render cron job (daily at 6:00 UTC).

1. Wake up the web service (Render starter plan may sleep after inactivity).
2. Run scheduled activity-export email jobs.
3. Run Fin IQ alpha-ideas + digest.

NOTE: The legacy `sync_users` step (which POSTed the deployed repo's
users.json to /api/cron/restore-users-from-deployed-file and overwrote
S3) was REMOVED on 2026-05-27. S3 (s3://dashboard-inputs/system/users.json)
is now the single source of truth for the user list; the repo copy is
only a fallback bootstrap. Re-enabling that step will wipe live user
edits (new accounts, role changes, credit grants, must_reset_password
flags) made through the admin UI since the last commit.
"""
import os
import sys
import time
import urllib.request
import urllib.error

BASE = (os.environ.get('APP_URL') or 'https://behavioral-graph.onrender.com').rstrip('/')
SECRET = os.environ.get('CRON_SECRET', '')
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds between retries


def _post(path, timeout=180):
    """POST to BASE+path with CRON_SECRET header. Returns (status, body)."""
    url = BASE + path
    req = urllib.request.Request(url, method='POST', headers={'X-Cron-Secret': SECRET})
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode()
    return resp.status, body


def _post_with_retry(path, label, timeout=180):
    """POST with retries. Returns True on 200, False otherwise."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status, body = _post(path, timeout=timeout)
            print(f"  [{label}] {status} {body}")
            if status == 200:
                return True
            if status == 403:
                print(f"  [{label}] 403 Unauthorized — CRON_SECRET mismatch between cron and web service")
                return False
        except urllib.error.HTTPError as e:
            msg = e.read().decode() if e.fp else ''
            print(f"  [{label}] attempt {attempt}/{MAX_RETRIES}: HTTP {e.code} {msg}")
            if e.code == 403:
                print(f"  [{label}] CRON_SECRET mismatch — set the same value on both web and cron services in Render")
                return False
        except Exception as e:
            print(f"  [{label}] attempt {attempt}/{MAX_RETRIES}: {e}")

        if attempt < MAX_RETRIES:
            print(f"  [{label}] retrying in {RETRY_DELAY}s ...")
            time.sleep(RETRY_DELAY)

    print(f"  [{label}] FAILED after {MAX_RETRIES} attempts")
    return False


def wake_up():
    """Ping /healthz to wake a sleeping Render service before real work."""
    print("Step 1: Waking up web service ...")
    url = BASE + '/healthz'
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=60) as resp:
                print(f"  /healthz → {resp.status}")
                return True
        except Exception as e:
            print(f"  wake-up attempt {attempt}: {e}")
            time.sleep(10)
    print("  WARNING: could not wake service; continuing anyway")
    return False


def sync_users():
    """DISABLED 2026-05-27.

    Previously POSTed /api/cron/restore-users-from-deployed-file, which
    overwrote the live S3 users.json with whatever stale copy was committed
    in the repo. That wiped accounts created/edited through the admin UI
    since the last commit and reset every user's last_inactive_email_sent
    cooldown (which caused the flood of inactive-user alert emails on
    2026-05-27 when an admin clicked "Check inactive users" hours later).

    S3 is now the source of truth. Do not re-enable.
    """
    print("Step 2: SKIPPED (sync_users disabled — S3 is source of truth).")
    return True


def run_exports():
    """Trigger scheduled activity CSV exports."""
    print("Step 3: Running activity export jobs ...")
    return _post_with_retry('/api/cron/run-activity-export-jobs', 'exports')


def run_fin_iq_alpha():
    """Trigger Fin IQ alpha ideas generation + email digest (endpoint handles day-of-week logic)."""
    print("Step 4: Running Fin IQ alpha ideas + digest ...")
    return _post_with_retry('/api/cron/hf-alpha-ideas', 'fin-iq-alpha', timeout=600)


def main():
    if not SECRET:
        print("FATAL: CRON_SECRET env var is not set on the cron service.")
        print("  Set it in Render Dashboard → activity-export-cron → Environment.")
        sys.exit(1)

    print(f"=== activity-export-cron  target={BASE} ===")
    wake_up()
    sync_ok = sync_users()  # no-op since 2026-05-27, always True
    export_ok = run_exports()
    alpha_ok = run_fin_iq_alpha()

    results = {'sync': sync_ok, 'exports': export_ok, 'alpha': alpha_ok}
    critical = {'exports': export_ok}
    if all(results.values()):
        print("=== ALL STEPS OK ===")
        sys.exit(0)
    elif all(critical.values()):
        summary = '  '.join(f"{k}={'OK' if v else 'FAIL'}" for k, v in results.items())
        print(f"=== DONE (non-critical failure)  {summary} ===")
        sys.exit(0)
    else:
        summary = '  '.join(f"{k}={'OK' if v else 'FAIL'}" for k, v in results.items())
        print(f"=== DONE WITH ERRORS  {summary} ===")
        sys.exit(1)


if __name__ == '__main__':
    main()
