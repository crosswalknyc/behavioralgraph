"""Debounced, targeted recompute of one category's norm.

`s3://dashboard-inputs/system/category_norms.json` is a precomputed
sample-size-weighted average of every profile's BP values, grouped by
BRAND CATEGORY. It's what the dashboard's "Show Category Norm"
checkbox reads from. If it goes stale (no new recompute since new
profiles landed), the frontend keeps reporting the old N and the old
weighted averages - the exact defect that made LeBron James read
"N=2" for ATHLETE on 2026-07-22 even though 25 athlete files existed.

This module is the self-healing counterpart to
`migration/compute_category_norms.py`. Any time a new profile lands
in the dashboard (via script upload OR web UI), the calling path
schedules `schedule_recompute(profile_category)` here. That:

  1. debounces so a burst of uploads (e.g. reba batch of avid skins)
     coalesces into ONE recompute per category,
  2. runs on a daemon thread so the caller's upload never blocks,
  3. recomputes ONLY the affected category (~1-10 sec, 25-2780 files
     depending on category) - not the whole 65-category file,
  4. rewrites just that entry in the S3 norms file,
  5. busts the in-process cache in the Flask worker so the next
     `/api/category-norms/<cat>` call returns the new value
     immediately (no wait for the 60-second TTL).

Failures are logged and swallowed - the upload itself must never
break because a norm recompute hit an error.

Wired in:
  * migration/dashboard_register.register_profile_in_dashboard
    (covers every script that writes a profile CSV)
  * bg-webapp/app.py at the two auto_add_to_quick_selects call
    sites (covers web-UI uploads and purgatory-release flow)
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

# Debounce window: a follow-up schedule() for the same category
# within this many seconds cancels the pending timer and re-arms it.
# 60s covers typical burst-upload scenarios (a reba avid batch that
# fires ~10 uploads per minute) while still surfacing the refreshed
# norm well within one user session.
_DEBOUNCE_SECS = 60

_lock: threading.Lock = threading.Lock()
_pending: dict[str, tuple[threading.Timer, float]] = {}


def _do_recompute(category: str) -> None:
    """Actual worker. Runs on a daemon thread after the debounce
    delay. Safe to call from anywhere; never raises."""
    # Remove from pending FIRST so a follow-up schedule() during the
    # recompute re-arms the timer instead of colliding on the same
    # entry.
    with _lock:
        _pending.pop(category, None)
    try:
        # Local imports so this module stays cheap to import - and so
        # environments without AWS credentials don't fail at import.
        from datetime import datetime, timezone

        from migration.compute_category_norms import (
            _make_s3,
            _norm_cat,
            compute_norm_for_category,
            load_jobs_cache,
            load_norms,
            save_norms,
        )

        cat = _norm_cat(category)
        if not cat:
            return

        s3 = _make_s3()
        jobs_all = load_jobs_cache(s3)
        jobs = [
            j for j in jobs_all
            if (j.get("category") or "").strip().upper() == cat
        ]
        if not jobs:
            print(f"[cat-norm-refresh] no jobs found for category={cat!r} - skipping")
            return

        norm = compute_norm_for_category(s3, cat, jobs, workers=8)
        if not norm:
            print(f"[cat-norm-refresh] no ingestible profiles for {cat!r} - leaving prior norm in place")
            return
        norm.pop("_meta", None)

        payload = load_norms(s3)
        if not isinstance(payload, dict):
            payload = {"norms": {}, "skipped_categories": {}}
        payload.setdefault("norms", {})[cat] = norm
        # Drop any stale "skipped" marker for this category now that it
        # has enough profiles to compute.
        skipped = payload.setdefault("skipped_categories", {})
        skipped.pop(cat, None)
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        payload["generated_by"] = "category_norm_refresh (targeted, on-upload)"

        save_norms(s3, payload)
        print(
            f"[cat-norm-refresh] refreshed {cat!r} "
            f"(N={norm.get('profile_count')}, "
            f"avg_sample={norm.get('weighted_avg_sample_size', 0):,})"
        )

        # If we're running inside the Flask webapp process, hand the
        # fresh payload to its in-memory cache so the next
        # `/api/category-norms/<cat>` request returns the new value
        # immediately instead of waiting up to 60s for the TTL to
        # expire. We look this up via sys.modules to avoid an import
        # cycle - if `app` was never imported, this is a no-op.
        try:
            app_mod = sys.modules.get("app")
            if app_mod is not None:
                cache = getattr(app_mod, "_category_norms_cache", None)
                if isinstance(cache, dict):
                    cache["payload"] = payload
                    # None here forces the next fetch to revalidate
                    # against S3 rather than reuse a stale ETag.
                    cache["etag"] = None
                    cache["fetched_at"] = time.time()
        except Exception:
            # Best-effort; the S3-side write is already the source
            # of truth so any Flask worker will pick it up on next
            # TTL expiry regardless.
            pass
    except Exception as e:
        # Never let a norm-refresh failure crash the caller. Log with
        # a full traceback so an operator can debug from the worker
        # logs, then swallow.
        print(f"[cat-norm-refresh] ERROR for category={category!r}: {e}")
        traceback.print_exc()


def schedule_recompute(category: str | None, delay_secs: int | None = None) -> None:
    """Schedule a debounced recompute for a single category.

    Called with the profile's BRAND CATEGORY every time a profile is
    added to the dashboard's s3_cache.json. Multiple calls for the
    same category within `_DEBOUNCE_SECS` collapse into one run
    (later call wins, earlier timer is cancelled).

    Never raises. Never blocks - the actual work runs on a daemon
    Timer. Safe to call from any thread, any process.

    A `delay_secs=0` shortcut is accepted for tests / manual triggers
    so the recompute fires immediately in-thread-pool without waiting.
    """
    if not category:
        return
    cat = str(category).strip().upper()
    if not cat:
        return
    delay = _DEBOUNCE_SECS if delay_secs is None else max(0, int(delay_secs))

    with _lock:
        prior = _pending.get(cat)
        if prior is not None:
            timer, _sched = prior
            try:
                timer.cancel()
            except Exception:
                pass
        t = threading.Timer(delay, _do_recompute, args=(cat,))
        t.daemon = True
        _pending[cat] = (t, time.time())
        t.start()


__all__ = ["schedule_recompute"]
