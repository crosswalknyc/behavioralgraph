#!/usr/bin/env python3
"""Debounced hold-notice delivery for ship-gate blocks and vetting holds.

Jenna 2026-08-27 (verbatim): "yes I only want real emails not gate
blocks, just if the final cannnot ship". Context: a "YMCA - Avid Fan"
hold notice (255 findings) emailed immediately, then the machinery
auto-repaired and republished within ~2 hours - the email was noise by
the time it was read.

Policy implemented here:

  * A gate block or vetting hold does NOT email immediately. The
    fully-rendered notice (subject + body, same content as before) is
    recorded as a PENDING notice. Quarantine copies and logging are
    unchanged at the call sites.
  * If the same deliverable (same subject+cohort identity, i.e. the
    output filename minus its build timestamp) publishes gate-green
    later, the pending notice is cancelled and logged as auto-resolved.
    No email ever sends.
  * If the hold outlives the debounce window (DEBOUNCE_MINUTES), or the
    run reaches a terminal failed / ops_hold state sooner
    (flush_now_for), ONE email sends: the original notice plus one line
    noting automatic repair was attempted and did not clear it.
  * Never more than one email per (deliverable identity, reason class)
    per UTC day.
  * Fail-safe: if the pending state cannot be recorded at all, the
    notice sends immediately (the pre-debounce behavior). A
    final-cannot-ship signal is never lost to a state outage.

State lives at s3://dashboard-inputs/system/pending_hold_notices.json
behind the ETag-CAS helper in migration/s3_json_state, so it survives
restarts and is shared across every worker. A local JSON file is the
best-effort fallback when S3 is unreachable (and the test backend via
HOLD_NOTICE_STATE_FILE).

Flush triggers (all idempotent; single-send is guaranteed by the CAS
claim: an entry is moved pending -> sent in one conditional write and
only the writer that wins the CAS sends the email):

  * systemd timer: migration/systemd/hold-notice-flush.timer (every
    12 minutes) runs `python3 -m migration.hold_notice_debounce --flush`.
  * opportunistic: the queue worker calls flush_due() at job
    boundaries, so overdue notices send even if the timer is down.
  * terminal: _finalize_failed_run / ops-hold paths call
    flush_now_for(key) so a dead run's notice sends without waiting.

Ops-failure emails (watchdog worker_restart_loop, pipeline failure
alerts) are NOT routed through this module: those paths have no
auto-repair pathway and keep their immediate sends.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

BUCKET = "dashboard-inputs"
STATE_KEY = "system/pending_hold_notices.json"

# Debounce window: how long a hold may sit pending before the notice
# sends. 80 minutes sits inside the mandated 75-90 minute band and
# comfortably covers the observed auto-repair cycle (~2h worst case was
# quarantine -> rebuild -> republish; the rebuild itself lands well
# inside 80 min, and the daily dedupe absorbs a second block).
DEBOUNCE_MINUTES = 80

# On a send failure the claimed entry is re-queued this far out.
RESEND_BACKOFF_MINUTES = 10

# sent-markers older than this are pruned (dedupe horizon is one UTC
# day; 3 days keeps a short audit tail).
SENT_RETENTION_DAYS = 3
LOG_CAP = 200

REPAIR_LINE = "Automatic repair was attempted and did not clear it."

_TS_SUFFIX_RE = re.compile(r"[_\s]\d{2}[_\s]\d{2}[_\s]\d{4}.*$")


def _now():
    """UTC now. Module-level so tests can monkeypatch time."""
    return datetime.now(timezone.utc)


def _suppressed_by_env():
    """Test-harness guard (2026-08-28: a test run recorded a real
    pending vetting hold that the production flusher then emailed).
    BG_SUPPRESS_HOLD_NOTICES=1 makes record_pending a logged no-op:
    nothing lands in the shared pending state, so nothing ever sends.
    Set by the regression harness; production leaves it unset. The
    debounce machinery's own tests pop the var explicitly."""
    v = (os.environ.get("BG_SUPPRESS_HOLD_NOTICES") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _parse_ts(value):
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def deliverable_identity(s3_key) -> str:
    """Stable identity for a deliverable: subject+cohort, independent of
    the build timestamp. 'YMCA_-_Avid_Fan_08_25_2026_14_30.csv' and a
    rebuilt 'YMCA_-_Avid_Fan_08_25_2026_16_10.csv' share one identity,
    so a rebuild under a fresh timestamp still cancels the pending
    notice recorded against the blocked attempt."""
    base = os.path.basename(str(s3_key or "").strip())
    if base.lower().endswith(".csv"):
        base = base[:-4]
    base = re.sub(r"\.(rejected|vetting_hold|pre)_.*$", "", base)
    base = _TS_SUFFIX_RE.sub("", base)
    return base.strip(" _-").casefold()


def _verify_prefix(s3_key) -> str:
    """Original-casing key prefix for the publish-verify LIST."""
    base = os.path.basename(str(s3_key or "").strip())
    if base.lower().endswith(".csv"):
        base = base[:-4]
    return _TS_SUFFIX_RE.sub("", base).strip()


# ---------------------------------------------------------------------------
# State backend: S3 ETag-CAS primary, local JSON best-effort fallback
# ---------------------------------------------------------------------------

def _local_state_path():
    p = os.environ.get("HOLD_NOTICE_STATE_FILE")
    if p:
        return p
    for cand in ("/var/lib/bg/pending_hold_notices.json",
                 "/tmp/pending_hold_notices.json"):
        d = os.path.dirname(cand)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return cand
    return "/tmp/pending_hold_notices.json"


def _local_update(mutate_fn):
    path = _local_state_path()
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh) or {}
    except Exception:
        state = {}
    new_state = mutate_fn(state)
    if new_state is None:
        return None
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(new_state, fh, indent=2, default=str)
    os.replace(tmp, path)
    return new_state


def _update_state(mutate_fn, s3_client=None):
    """Apply mutate_fn under CAS. mutate_fn may run multiple times
    against fresh snapshots (s3_json_state retry semantics), so it must
    recompute its decisions from the state it is handed each call.
    Returns the written state, or None when mutate_fn aborted.
    Raises only when BOTH backends are unavailable."""
    if os.environ.get("HOLD_NOTICE_STATE_FILE"):
        return _local_update(mutate_fn)
    try:
        try:
            from migration.s3_json_state import update_json
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from s3_json_state import update_json  # type: ignore
        return update_json(BUCKET, STATE_KEY, mutate_fn, default={},
                           s3=s3_client)
    except Exception as e:
        print(f"[hold-notice] S3 state unavailable ({type(e).__name__}: "
              f"{e}); using local best-effort state")
        return _local_update(mutate_fn)


def _read_state(s3_client=None):
    """Read-only snapshot (fast path for cancel_on_publish)."""
    if os.environ.get("HOLD_NOTICE_STATE_FILE"):
        try:
            with open(_local_state_path(), encoding="utf-8") as fh:
                return json.load(fh) or {}
        except Exception:
            return {}
    try:
        try:
            from migration.s3_json_state import read_json_with_etag
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from s3_json_state import read_json_with_etag  # type: ignore
        obj, _ = read_json_with_etag(BUCKET, STATE_KEY, s3=s3_client)
        return obj or {}
    except Exception:
        return {}


def _prune(state, now):
    """In-place hygiene: drop stale sent-markers, cap the event log."""
    sent = state.get("sent") or {}
    horizon = now - timedelta(days=SENT_RETENTION_DAYS)
    for k in [k for k, ts in sent.items()
              if (_parse_ts(ts) or now) < horizon]:
        sent.pop(k, None)
    state["sent"] = sent
    log = state.get("log") or []
    if len(log) > LOG_CAP:
        state["log"] = log[-LOG_CAP:]
    return state


def _log(state, now, event, key, **extra):
    entry = {"ts": now.isoformat(), "event": event, "key": key}
    entry.update({k: v for k, v in extra.items() if v is not None})
    state.setdefault("log", []).append(entry)


# ---------------------------------------------------------------------------
# SES delivery
# ---------------------------------------------------------------------------

def _send_ses(payload, extra_line=None):
    """Send one notice. Isolated so tests monkeypatch it and so the
    immediate-fallback path shares the exact send. Raises on failure so
    flush_due can re-queue the claimed entry."""
    body = str(payload.get("body") or "")
    if extra_line:
        body = f"{body.rstrip()}\n\n{extra_line}"
    import boto3
    ses = boto3.client("ses", region_name="us-east-2")
    ses.send_email(
        Source=payload.get("source") or "Crosswalk Ops <jenna@crosswalknyc.com>",
        Destination={"ToAddresses": list(payload.get("to") or
                                         ["jenna@crosswalknyc.com"])},
        Message={
            "Subject": {"Data": str(payload.get("subject_line") or
                                    "Profile held before delivery")},
            "Body": {"Text": {"Data": body}},
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_pending(s3_key, reason_class, email_payload, *,
                   quarantine_key=None, n_findings=None, s3_client=None,
                   verbose=True) -> str:
    """Record (or refresh) a pending hold notice instead of emailing.

    email_payload: {"subject_line", "body", "to", "source"} - the
    fully-rendered notice exactly as it would have been sent today.

    Returns one of:
      "recorded"  - new pending entry; emails at due_at unless cancelled
      "refreshed" - entry already pending; payload updated, the ORIGINAL
                    due_at stands (window measures from the first block)
      "deduped"   - a notice for this (identity, reason) already sent
                    today; nothing recorded, nothing will send
      "sent_immediate_fallback" - state unavailable; emailed now
    """
    if _suppressed_by_env():
        print(f"[hold-notice] BG_SUPPRESS_HOLD_NOTICES set; notice for "
              f"{os.path.basename(str(s3_key or ''))} "
              f"({reason_class}) suppressed (test run; nothing "
              f"recorded, nothing will send)")
        return "suppressed"
    ident = deliverable_identity(s3_key)
    reason_class = str(reason_class or "hold").strip() or "hold"
    entry_key = f"{ident}|{reason_class}"
    outcome = {"disposition": "recorded"}

    def mutate(state):
        now = _now()
        state = _prune(state, now)
        day_key = f"{entry_key}|{now.strftime('%Y-%m-%d')}"
        pending = state.setdefault("pending", {})
        sent = state.setdefault("sent", {})
        if day_key in sent:
            outcome["disposition"] = "deduped"
            _log(state, now, "suppressed_daily_dedupe", entry_key)
            pending.pop(entry_key, None)
            return state
        prior = pending.get(entry_key)
        entry = {
            "filename": os.path.basename(str(s3_key or "")),
            "verify_prefix": _verify_prefix(s3_key),
            "reason_class": reason_class,
            "subject_line": str(email_payload.get("subject_line") or ""),
            "body": str(email_payload.get("body") or ""),
            "to": list(email_payload.get("to") or []),
            "source": str(email_payload.get("source") or ""),
            "quarantine_key": quarantine_key,
            "n_findings": n_findings,
            "last_seen_at": now.isoformat(),
        }
        if prior:
            entry["created_at"] = prior.get("created_at", now.isoformat())
            entry["due_at"] = prior.get(
                "due_at",
                (now + timedelta(minutes=DEBOUNCE_MINUTES)).isoformat())
            entry["occurrences"] = int(prior.get("occurrences") or 1) + 1
            outcome["disposition"] = "refreshed"
        else:
            entry["created_at"] = now.isoformat()
            entry["due_at"] = (
                now + timedelta(minutes=DEBOUNCE_MINUTES)).isoformat()
            entry["occurrences"] = 1
            outcome["disposition"] = "recorded"
        pending[entry_key] = entry
        _log(state, now, outcome["disposition"], entry_key,
             filename=entry["filename"])
        return state

    try:
        _update_state(mutate, s3_client=s3_client)
    except Exception as e:
        # Fail-safe: a final-cannot-ship signal must never be lost to a
        # state outage. Send now, exactly as before the debounce.
        print(f"[hold-notice] pending state write failed "
              f"({type(e).__name__}: {e}); sending the notice now")
        try:
            _send_ses(email_payload)
            return "sent_immediate_fallback"
        except Exception as se:
            print(f"[hold-notice] immediate fallback send failed: {se}")
            return "failed"
    if verbose:
        print(f"[hold-notice] {outcome['disposition']}: {entry_key} "
              f"(window {DEBOUNCE_MINUTES} min)")
    return outcome["disposition"]


def cancel_on_publish(s3_key, *, s3_client=None, verbose=True) -> int:
    """A deliverable published gate-green: silently cancel every pending
    notice for its identity (any reason class). Returns the number of
    cancelled entries. Never raises."""
    try:
        ident = deliverable_identity(s3_key)
        if not ident:
            return 0
        # Fast path: publishes are frequent, holds are rare. Skip the
        # CAS write entirely when nothing is pending for this identity.
        snap = _read_state(s3_client=s3_client)
        if not any(k.split("|", 1)[0] == ident
                   for k in (snap.get("pending") or {})):
            return 0
        removed = {"n": 0}

        def mutate(state):
            now = _now()
            state = _prune(state, now)
            pending = state.setdefault("pending", {})
            hits = [k for k in pending if k.split("|", 1)[0] == ident]
            removed["n"] = len(hits)
            if not hits:
                return None  # lost the race to a flusher; nothing to do
            for k in hits:
                entry = pending.pop(k)
                _log(state, now, "auto_resolved", k,
                     filename=entry.get("filename"),
                     resolved_by=os.path.basename(str(s3_key or "")))
            return state

        _update_state(mutate, s3_client=s3_client)
        if removed["n"] and verbose:
            print(f"[hold-notice] auto-resolved {removed['n']} pending "
                  f"notice(s) for {ident} (published gate-green)")
        return removed["n"]
    except Exception as e:
        print(f"[hold-notice] cancel_on_publish failed (non-fatal): {e}")
        return 0


def _published_since(entry, s3_client=None):
    """Publish-verify at flush time: True when any object sharing the
    entry's deliverable identity has a LastModified NEWER than the hold
    was recorded. Catches publish paths that never call
    cancel_on_publish (cut engines, legacy writers). On any S3 error
    returns False: fail toward sending, never toward losing a signal."""
    prefix = str(entry.get("verify_prefix") or "").strip()
    created = _parse_ts(entry.get("created_at"))
    if not prefix or created is None:
        return False
    ident = deliverable_identity(entry.get("filename"))
    try:
        import boto3
        s3c = s3_client or boto3.client("s3")
        resp = s3c.list_objects_v2(Bucket=BUCKET, Prefix=prefix,
                                   MaxKeys=100)
        for obj in resp.get("Contents") or []:
            key = str(obj.get("Key") or "")
            if "/" in key:
                continue  # bucket-root deliverables only
            if deliverable_identity(key) != ident:
                continue
            lm = obj.get("LastModified")
            if lm is not None and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            if lm is not None and lm > created:
                return True
    except Exception:
        return False
    return False


def flush_due(*, force_identities=None, s3_client=None, verbose=True) -> dict:
    """Send every overdue pending notice (once), auto-resolve any whose
    deliverable republished, and enforce the per-day dedupe.

    force_identities: identities (or keys/filenames) to flush regardless
    of due_at - the terminal-failure path. Single-send: entries move
    pending -> sent inside one CAS write; only the winner sends.
    """
    forced = {deliverable_identity(x) for x in (force_identities or set())}
    forced.discard("")
    summary = {"sent": [], "auto_resolved": [], "deduped": [],
               "send_failed": []}

    # Snapshot to decide which entries need a publish-verify LIST. The
    # verification is done OUTSIDE the CAS mutate (it is an S3 read);
    # results stay valid across mutate retries.
    snap = _read_state(s3_client=s3_client)
    now0 = _now()
    candidates = {}
    for k, entry in (snap.get("pending") or {}).items():
        due = _parse_ts(entry.get("due_at"))
        ident = k.split("|", 1)[0]
        if ident in forced or (due is not None and due <= now0):
            candidates[k] = entry
    if not candidates:
        return summary
    resolved_keys = {k for k, e in candidates.items()
                     if _published_since(e, s3_client=s3_client)}

    claimed = {}

    def mutate(state):
        claimed.clear()
        now = _now()
        state = _prune(state, now)
        pending = state.setdefault("pending", {})
        sent = state.setdefault("sent", {})
        changed = False
        for k in list(pending.keys()):
            entry = pending[k]
            ident = k.split("|", 1)[0]
            due = _parse_ts(entry.get("due_at"))
            if not (ident in forced or (due is not None and due <= now)):
                continue
            if k in resolved_keys:
                pending.pop(k)
                _log(state, now, "auto_resolved", k,
                     filename=entry.get("filename"),
                     resolved_by="publish-verify")
                changed = True
                continue
            day_key = f"{k}|{now.strftime('%Y-%m-%d')}"
            if day_key in sent:
                pending.pop(k)
                _log(state, now, "suppressed_daily_dedupe", k)
                summary["deduped"].append(k)
                changed = True
                continue
            pending.pop(k)
            sent[day_key] = now.isoformat()
            _log(state, now, "sent", k, filename=entry.get("filename"),
                 occurrences=entry.get("occurrences"))
            claimed[k] = entry
            changed = True
        return state if changed else None

    try:
        _update_state(mutate, s3_client=s3_client)
    except Exception as e:
        print(f"[hold-notice] flush state update failed: {e}")
        return summary
    summary["auto_resolved"] = sorted(
        k for k in resolved_keys if k in candidates and k not in claimed)

    for k, entry in claimed.items():
        try:
            _send_ses(entry, extra_line=REPAIR_LINE)
            summary["sent"].append(k)
            if verbose:
                print(f"[hold-notice] sent hold notice for {k} "
                      f"(held since {entry.get('created_at')})")
        except Exception as e:
            print(f"[hold-notice] send failed for {k} ({e}); re-queueing")
            summary["send_failed"].append(k)

            def requeue(state, k=k, entry=entry):
                now = _now()
                entry2 = dict(entry)
                entry2["due_at"] = (
                    now + timedelta(minutes=RESEND_BACKOFF_MINUTES)
                ).isoformat()
                state.setdefault("pending", {})[k] = entry2
                day_key = f"{k}|{now.strftime('%Y-%m-%d')}"
                state.setdefault("sent", {}).pop(day_key, None)
                _log(state, now, "requeued_after_send_failure", k)
                return state

            try:
                _update_state(requeue, s3_client=s3_client)
            except Exception as re_e:
                print(f"[hold-notice] re-queue failed for {k}: {re_e}")
    return summary


def flush_now_for(s3_key, *, s3_client=None, verbose=True) -> dict:
    """Terminal path: the run failed / ops-held with no further repair
    coming, so its pending notice sends now instead of waiting out the
    window. Daily dedupe still applies. Never raises."""
    try:
        return flush_due(force_identities={s3_key}, s3_client=s3_client,
                         verbose=verbose)
    except Exception as e:
        print(f"[hold-notice] flush_now_for failed (non-fatal): {e}")
        return {"sent": [], "auto_resolved": [], "deduped": [],
                "send_failed": []}


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--show" in args:
        print(json.dumps(_read_state(), indent=2, default=str))
    elif "--flush" in args or not args:
        out = flush_due(verbose=True)
        print(f"[hold-notice] flush: {len(out['sent'])} sent, "
              f"{len(out['auto_resolved'])} auto-resolved, "
              f"{len(out['deduped'])} deduped, "
              f"{len(out['send_failed'])} send-failed")
    else:
        print("usage: python3 -m migration.hold_notice_debounce "
              "[--flush | --show]")
        raise SystemExit(2)
