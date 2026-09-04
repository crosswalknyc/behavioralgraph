"""Anthropic usage tap for the trends / ranker scrapers.

Every Claude call inside `scripts/trends_scrapers/` calls into this
module immediately after a response comes back (serial + batch, both
paths). The tap folds the response's token counts into the box-wide
per-call day log at

    /root/synth_queue/usage_calls/YYYY_MM_DD.jsonl

with `attribution='trends_ranker'` on every row. `migration/
daily_cost_report.py::load_box_call_log` reads that field to break the
Trends/Ranker line out from the pooled non-run box total in the daily
spend email.

Design notes:
  * Soft import of `migration.usage_tracker`. The trends scrapers ship
    inside the `bg-webapp` submodule; on Hetzner the parent repo lives
    one level up at `/root/finished_codes/`, so we add that to
    `sys.path` and try the import. When the module is not reachable
    (local dev checkout without the parent, older worker box) the tap
    is a no-op and the scraper continues normally.
  * Never raises. A failed record must never break a scraper.
  * Stable run_id (`_trends_ranker`) so all calls in the daily cron
    process fold into one ledger; the process exits at the end of the
    cron so the ledger evaporates on its own.
  * The `metadata={'user_id': 'trends_ranker'}` tag is added at the
    call sites themselves (not here) so it rides along with the
    request to Anthropic; this module is the client-side ground
    truth used by the daily spend report.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

# Add the parent repo root to sys.path so we can import
# `migration.usage_tracker` when running as part of the bg-webapp
# submodule checkout. The layout on Hetzner is
# /root/finished_codes/bg-webapp/scripts/trends_scrapers/_usage_tap.py
# and the tracker lives at
# /root/finished_codes/migration/usage_tracker.py, so we go up three
# levels.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from migration import usage_tracker as _ut  # type: ignore
except Exception:  # noqa: BLE001
    _ut = None

# The metadata tag the trends call sites attach to
# `messages.create(...)` and `messages.batches.create(...)` requests.
# Kept in sync with the label written to every JSONL record here so
# the two attribution surfaces (Anthropic-side request metadata and
# our box-side per-call log) always agree on the same short name.
USER_ID = 'trends_ranker'
ATTRIBUTION = 'trends_ranker'
_RUN_ID = '_trends_ranker'


def record_call(model: str, resp: Any, *,
                web_search_queries: int = 0) -> None:
    """Record one Anthropic response against the Trends/Ranker line.

    `resp` is the object returned by `client.messages.create(...)`; we
    pull its `usage` attribute. Never raises, never blocks the call
    site meaningfully.
    """
    if _ut is None:
        return
    try:
        usage = getattr(resp, 'usage', None)
        _ut.record(model, usage,
                   web_search_queries=int(web_search_queries or 0),
                   run_id=_RUN_ID,
                   attribution=ATTRIBUTION)
    except Exception:  # noqa: BLE001
        return


def record_batch_result(model: str, usage: Any, *,
                        web_search_queries: int = 0) -> None:
    """Record one Anthropic batch result against the Trends/Ranker line.

    `usage` is the raw usage object (or dict) already unwrapped from a
    `MessageBatchIndividualResponse.result.message.usage`. Same
    fail-safe posture as `record_call`. Passes `batch=True` so the
    per-call JSONL cost is priced at the Anthropic batch discount.
    """
    if _ut is None:
        return
    try:
        _ut.record(model, usage,
                   web_search_queries=int(web_search_queries or 0),
                   run_id=_RUN_ID,
                   attribution=ATTRIBUTION,
                   batch=True)
    except Exception:  # noqa: BLE001
        return


def metadata_dict() -> dict:
    """The `metadata` field to attach to every Anthropic request from
    the trends stack: `{'user_id': 'trends_ranker'}`. Returned as a
    fresh dict per call so a caller can safely `params['metadata'] =
    metadata_dict()` without aliasing."""
    return {'user_id': USER_ID}


__all__ = ['record_call', 'record_batch_result', 'metadata_dict',
           'USER_ID', 'ATTRIBUTION']
