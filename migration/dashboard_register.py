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

try:
    from migration.s3_json_state import update_json
except ImportError:  # direct-script import path (migration/ on sys.path)
    from s3_json_state import update_json

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
    cat_upper = (category or "").strip().upper()
    counts = {"updated": 0, "skipped": 0}

    def _mutate(doc):
        counts["updated"] = 0
        counts["skipped"] = 0
        users = doc.get("users", {})
        for uname, u in users.items():
            runs = u.get("allowed_runs", ["*"])
            if not isinstance(runs, list) or "*" in runs:
                continue  # sees everything already
            cats = u.get("allowed_categories", ["*"])
            if isinstance(cats, list) and "*" not in cats:
                cats_upper = {str(c).strip().upper() for c in cats}
                if cat_upper and cat_upper not in cats_upper:
                    counts["skipped"] += 1
                    continue
            if s3_key in runs:
                continue
            u["allowed_runs"] = list(set(runs) | {s3_key})
            counts["updated"] += 1
        if counts["updated"] == 0:
            return None  # nothing changed; skip the write entirely
        return doc

    try:
        update_json(BUCKET, USERS_KEY, _mutate, s3=s3,
                    put_extra_args={"CacheControl": "no-cache, max-age=0"})
    except Exception as e:
        return {"users_updated": 0, "users_skipped": counts["skipped"],
                "error": f"save: {e}"}
    return {"users_updated": counts["updated"], "users_skipped": counts["skipped"]}


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

    # `project_name` is the filename-derived fallback (what
    # process_s3_file_metadata in bg-webapp/app.py would compute from the S3
    # key on a fresh refresh). Keeping it distinct from a custom
    # `display_name` is what allows the smart_cache_update preservation
    # check `cached_display != fresh_project` to correctly detect that the
    # entry was custom-registered and preserve it across refreshes.
    #
    # 2026-07-29 (Elton cuts bug): previously both were set to display_name,
    # which made the preservation check a no-op and caused hyphenated cut
    # names like "Elton John - Instagram Followers" to get overwritten by
    # the filename-derived "Elton John Instagram Followers" on the next
    # smart_cache_update, breaking sidebar grouping under the parent.
    _fname = s3_key.rsplit("/", 1)[-1]
    _fname_stem = re.sub(r"\.csv$", "", _fname, flags=re.IGNORECASE)
    _fname_no_ts = re.sub(
        r"_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?$", "", _fname_stem
    )
    project_name_fallback = _fname_no_ts.replace("_", " ").strip()

    if not display_name:
        display_name = project_name_fallback

    # ETag-guarded read-modify-write: 21 workers can register profiles
    # concurrently, and a lost cache write silently hides a profile from
    # the Select Profile dropdown. The mutate fn re-runs on conflict, so
    # everything (src_entry lookup included) lives inside it.
    result_flags = {"cache_added": False, "category": None}

    def _mutate_cache(cache):
        src_lookup = {j.get("s3_key"): j for j in cache.get("jobs", [])
                      if isinstance(j, dict)}
        src_entry = src_lookup.get(source_key, {}) if source_key else {}

        new_entry = {
            "job_id": s3_key,
            "project_name": project_name_fallback,
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
        result_flags["category"] = new_entry["category"]

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
            result_flags["cache_added"] = False
        else:
            cache.setdefault("jobs", []).append(new_entry)
            result_flags["cache_added"] = True

        cache["file_count"] = len(cache["jobs"])
        cache["last_updated"] = datetime.now(timezone.utc).timestamp()
        return cache

    update_json(
        BUCKET, S3_CACHE_KEY, _mutate_cache, s3=s3, indent=None,
        put_extra_args={"CacheControl": "no-cache, max-age=0"},
    )
    cache_added = result_flags["cache_added"]

    qs_flags = {"added": False}

    def _mutate_qs(qs):
        profiles = qs.setdefault("profiles", {})
        if s3_key in profiles:
            qs_flags["added"] = False
            return None  # preserve admin-set False; nothing to write
        profiles[s3_key] = True
        qs_flags["added"] = True
        qs["updated_at"] = datetime.now(timezone.utc).isoformat()
        qs["updated_by"] = qs.get("updated_by", "auto-register")
        return qs

    update_json(
        BUCKET, QUICK_SELECTS_KEY, _mutate_qs, s3=s3,
        put_extra_args={"CacheControl": "no-cache, max-age=0"},
    )
    quick_select_added = qs_flags["added"]

    users_result = _add_to_users_allowed_runs(
        s3, s3_key, result_flags["category"])

    # Fire a targeted, debounced recompute of this category's norm so
    # `s3://dashboard-inputs/system/category_norms.json` stays fresh
    # as new profiles land. Without this the norms file drifts (that
    # was the root cause of ATHLETE reading "N=2" on 2026-07-22 even
    # though 25 athlete files existed - the file hadn't been
    # regenerated since 2026-06-11). The helper debounces bursts,
    # runs on a daemon thread, and never raises. Best-effort - if it
    # fails, the upload itself still succeeds.
    norm_refresh_scheduled = False
    try:
        from migration.category_norm_refresh import schedule_recompute
        schedule_recompute(result_flags["category"])
        norm_refresh_scheduled = True
    except Exception as e:
        print(f"⚠️  category-norm refresh scheduling failed for {s3_key}: {e}")

    return {
        "s3_key": s3_key,
        "cache_added": cache_added,
        "cache_updated": not cache_added,
        "quick_select_added": quick_select_added,
        "quick_select_already_present": not quick_select_added,
        "users_updated": users_result.get("users_updated", 0),
        "users_skipped_by_category": users_result.get("users_skipped", 0),
        "users_error": users_result.get("error"),
        "category_norm_refresh_scheduled": norm_refresh_scheduled,
    }


__all__ = ["register_profile_in_dashboard"]
