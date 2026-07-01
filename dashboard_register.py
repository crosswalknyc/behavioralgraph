"""Shared helper: register a freshly uploaded profile CSV in the dashboard.

Every script that writes a profile CSV to s3://dashboard-inputs/<root>/ MUST
call `register_profile_in_dashboard(s3_key, ...)` after the upload so the
profile appears in the dashboard's "Select Profile" dropdown immediately.

There are THREE files the dashboard reads from, all in the same bucket:

  1. system/s3_cache.json
       Persisted profile-selector cache. The Flask app loads this at boot
       and refreshes when its ETag changes (see
       `maybe_refresh_persisted_cache_if_changed` in bg-webapp/app.py).
       Each profile is one entry in cache['jobs'] with at minimum:
         job_id / s3_key / display_name / category / status:'cached'
         created_at / last_modified / progress:100 / source:'s3'

  2. metadata/admin_quick_selects.json
       Curated rail. Only profiles whose key maps to True here appear in
       the dropdown. The webapp's auto_add_to_quick_selects() does this
       automatically for files uploaded via the app, but files written
       directly to S3 (via boto3 from a script) bypass that path — so
       this helper does it explicitly.

  3. system/users.json -> users[*].allowed_runs
       Per-user run-access list. Users with allowed_runs=['*'] see
       everything; users with explicit lists need each new key appended
       (gated by the user's allowed_categories — '*' or matching cat).
       The webapp's auto_add_runs_to_all_users() does this automatically
       for files uploaded via the app; direct-to-S3 scripts bypass that
       path. This was the gap that hid the Bridesmaids-Casual /
       Perks-of-Being-a-Wallflower-TU pair from 40 users on 2026-06-18.

  NOT system/quick_selects.json — that's an orphan path some old code
  writes to; the dashboard never reads it. Don't write there.

Usage:

    from migration.dashboard_register import register_profile_in_dashboard

    s3.put_object(Bucket="dashboard-inputs", Key=out_key, Body=body, ...)
    register_profile_in_dashboard(
        out_key,
        display_name="MARK ROBER - Avid Fan",
        category="CREATOR/INFLUENCER",
        source_key="Mark_Rober_06_09_2026_05_43.csv",   # optional
    )

The helper is idempotent: re-registering the same key updates the cache
entry in place and leaves any explicitly admin-False quick_selects flag
unchanged (consistent with auto_add_to_quick_selects in bg-webapp/app.py).
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Optional

import boto3

BUCKET = "dashboard-inputs"
S3_CACHE_KEY = "system/s3_cache.json"
QUICK_SELECTS_KEY = "metadata/admin_quick_selects.json"
USERS_KEY = "system/users.json"


def _norm_subject(s: str) -> str:
    s = re.sub(r"\.csv$", "", str(s), flags=re.IGNORECASE)
    return re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")


def _add_to_users_allowed_runs(s3, s3_key: str, category: Optional[str]) -> dict:
    """Mirror of bg-webapp/app.py:auto_add_runs_to_all_users for direct-to-S3
    scripts. Adds `s3_key` to every user whose allowed_runs is an explicit
    list (not '*') AND whose allowed_categories matches the profile's category
    (or is '*'). Idempotent. Failures are logged-and-swallowed because the
    upload itself should still succeed.
    """
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=USERS_KEY)
        doc = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        return {"users_updated": 0, "users_skipped": 0, "error": f"load: {e}"}

    users = doc.get("users", {})
    cat_upper = (category or "").strip().upper()
    n_updated = 0
    n_explicit_skipped_by_cat = 0
    for uname, u in users.items():
        runs = u.get("allowed_runs", ["*"])
        if not isinstance(runs, list) or "*" in runs:
            continue  # sees everything already
        cats = u.get("allowed_categories", ["*"])
        if isinstance(cats, list) and "*" not in cats:
            cats_upper = {str(c).strip().upper() for c in cats}
            if cat_upper and cat_upper not in cats_upper:
                n_explicit_skipped_by_cat += 1
                continue
        if s3_key in runs:
            continue
        u["allowed_runs"] = list(set(runs) | {s3_key})
        n_updated += 1

    if n_updated == 0:
        return {"users_updated": 0, "users_skipped": n_explicit_skipped_by_cat}

    try:
        s3.put_object(
            Bucket=BUCKET, Key=USERS_KEY,
            Body=json.dumps(doc, indent=2).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-cache, max-age=0",
        )
    except Exception as e:
        return {"users_updated": 0, "users_skipped": n_explicit_skipped_by_cat,
                "error": f"save: {e}"}
    return {"users_updated": n_updated, "users_skipped": n_explicit_skipped_by_cat}


def register_profile_in_dashboard(
    s3_key: str,
    display_name: Optional[str] = None,
    category: Optional[str] = None,
    source_key: Optional[str] = None,
    s3_client=None,
) -> dict:
    """Register a profile CSV uploaded to s3://dashboard-inputs/<root>.

    Updates `system/s3_cache.json` (adds/replaces the cache job entry) AND
    `metadata/admin_quick_selects.json` (adds with True if the key is not
    already present; preserves any admin-set False).

    Returns a dict with `cache_added` / `quick_select_added` / `etag` so the
    caller can log what changed.

    Raises only on hard S3/permission errors. Idempotent across re-runs.
    """
    s3 = s3_client or boto3.client("s3", region_name="us-east-2")

    head = s3.head_object(Bucket=BUCKET, Key=s3_key)
    last_modified_iso = head["LastModified"].astimezone(timezone.utc).isoformat()

    if not display_name:
        display_name = re.sub(r"\.csv$", "", s3_key, flags=re.IGNORECASE).strip()

    cache_resp = s3.get_object(Bucket=BUCKET, Key=S3_CACHE_KEY)
    cache = json.loads(cache_resp["Body"].read().decode("utf-8"))
    src_lookup = {j.get("s3_key"): j for j in cache.get("jobs", [])
                   if isinstance(j, dict)}
    src_entry = src_lookup.get(source_key, {}) if source_key else {}

    new_entry = {
        "job_id": s3_key,
        "project_name": display_name,
        "display_name": display_name,
        "name": display_name,
        "status": "cached",
        "progress": 100,
        "created_at": last_modified_iso,
        "source": "s3",
        "s3_key": s3_key,
        "category": category or src_entry.get("category", "UNCATEGORIZED"),
        "profile_subject": _norm_subject(s3_key),
        "last_modified": last_modified_iso,
    }
    if src_entry.get("custom_image"):
        new_entry["custom_image"] = src_entry["custom_image"]
    if src_entry.get("imdb_id"):
        new_entry["imdb_id"] = src_entry["imdb_id"]
        new_entry["imdb_label"] = src_entry.get("imdb_label", display_name)

    existing_idx = None
    for i, j in enumerate(cache.get("jobs", [])):
        if isinstance(j, dict) and j.get("s3_key") == s3_key:
            existing_idx = i
            break

    if existing_idx is not None:
        # Preserve any side-channel fields (e.g. imdb_id from the
        # IMDB scraper) by merging the existing entry into the new one
        # instead of overwriting.
        merged = dict(cache["jobs"][existing_idx])
        merged.update(new_entry)
        cache["jobs"][existing_idx] = merged
        cache_added = False
    else:
        cache.setdefault("jobs", []).append(new_entry)
        cache_added = True

    cache["file_count"] = len(cache["jobs"])
    cache["last_updated"] = datetime.now(timezone.utc).timestamp()
    s3.put_object(
        Bucket=BUCKET, Key=S3_CACHE_KEY,
        Body=json.dumps(cache).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-cache, max-age=0",
    )

    qs_resp = s3.get_object(Bucket=BUCKET, Key=QUICK_SELECTS_KEY)
    qs = json.loads(qs_resp["Body"].read().decode("utf-8"))
    profiles = qs.setdefault("profiles", {})
    quick_select_added = False
    if s3_key not in profiles:
        profiles[s3_key] = True
        quick_select_added = True
        qs["updated_at"] = datetime.now(timezone.utc).isoformat()
        qs["updated_by"] = qs.get("updated_by", "auto-register")
        s3.put_object(
            Bucket=BUCKET, Key=QUICK_SELECTS_KEY,
            Body=json.dumps(qs, indent=2).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-cache, max-age=0",
        )

    users_result = _add_to_users_allowed_runs(s3, s3_key, new_entry.get("category"))

    return {
        "s3_key": s3_key,
        "cache_added": cache_added,
        "cache_updated": not cache_added,
        "quick_select_added": quick_select_added,
        "quick_select_already_present": not quick_select_added,
        "users_updated": users_result.get("users_updated", 0),
        "users_skipped_by_category": users_result.get("users_skipped", 0),
        "users_error": users_result.get("error"),
    }


__all__ = ["register_profile_in_dashboard"]
