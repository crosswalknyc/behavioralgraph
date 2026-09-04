"""Per-call Anthropic usage records for the web app's own model calls.

The profile engine's per-run token ledger lives with the engine; the
web app's OWN Claude calls (chat interpret, on-screen analysis, deck
plans) historically discarded the response usage object entirely, so
their spend was invisible. This module closes that gap: call sites tag
each request with a (surface, origin) pair and this module writes one
small JSON record per call to S3.

Records land at:

    s3://dashboard-inputs/system/usage/render_calls/YYYY_MM_DD/<ts>_<uuid>.json

One object per call (no read-modify-write, so concurrent gunicorn
workers can never clobber each other). A nightly aggregation job reads
the dated prefixes and folds them into the day-by-day spend store at
system/usage/daily_costs.json.

Surfaces: 'interpret' (chat request -> draft spec), 'analysis'
(on-screen profile analysis), 'deck' (slide-plan generation).
Origins: 'chatbot' (dashboard chat) or 'partner_api' (/api/v1/*).

Pricing mirrors the engine-side table (migration/usage_tracker.py in
the parent repo); update both when the Anthropic price sheet moves.

Every function here is failure-proof by design: a recording problem
must never affect a user-facing request. Writes happen on a daemon
thread so no S3 latency is added to the request path.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

S3_BUCKET = os.environ.get('RENDER_USAGE_BUCKET') or 'dashboard-inputs'
CALLS_PREFIX = 'system/usage/render_calls/'

# Pay-as-you-go (2026-08-26). Jenna: "we need to have a 110% markup on
# what we are charged" - billed price = our cost x 2.10. Calls made by
# a pay-per-use user carry user/session attribution extras and are
# mirrored one-object-per-call to a second prefix so the session sweep
# (pay_per_use.py) only lists billable rows, not all render traffic.
PPU_MARKUP = 2.10
PPU_CALLS_PREFIX = 'system/usage/ppu_calls/'

# Attribution fields a call site may attach to a usage record.
_EXTRA_FIELDS = ('user', 'user_email', 'session_id', 'request_id')

# USD per 1M tokens. Mirror of migration/usage_tracker.PRICING_USD_PER_MTOK.
PRICING_USD_PER_MTOK = {
    'claude-sonnet-4-5':      {'input': 3.00, 'output': 15.00,
                               'cache_read': 0.30, 'cache_write': 3.75},
    'claude-sonnet-4-6':      {'input': 3.00, 'output': 15.00,
                               'cache_read': 0.30, 'cache_write': 3.75},
    'claude-3-5-sonnet-20241022': {'input': 3.00, 'output': 15.00,
                                   'cache_read': 0.30, 'cache_write': 3.75},
    'claude-opus-4-5':        {'input': 15.00, 'output': 75.00,
                               'cache_read': 1.50, 'cache_write': 18.75},
    'claude-opus-4-6':        {'input': 15.00, 'output': 75.00,
                               'cache_read': 1.50, 'cache_write': 18.75},
    'claude-opus-4-7':        {'input': 15.00, 'output': 75.00,
                               'cache_read': 1.50, 'cache_write': 18.75},
    'claude-opus-4-8':        {'input': 15.00, 'output': 75.00,
                               'cache_read': 1.50, 'cache_write': 18.75},
    'claude-opus-5':          {'input': 15.00, 'output': 75.00,
                               'cache_read': 1.50, 'cache_write': 18.75},
    'claude-haiku-4-5':       {'input': 0.80, 'output': 4.00,
                               'cache_read': 0.08, 'cache_write': 1.00},
    'claude-haiku-4-5-20251001': {'input': 0.80, 'output': 4.00,
                               'cache_read': 0.08, 'cache_write': 1.00},
}
_DEFAULT_PRICES = {'input': 3.00, 'output': 15.00,
                   'cache_read': 0.30, 'cache_write': 3.75}

_s3 = None
_s3_lock = threading.Lock()


def _client():
    global _s3
    with _s3_lock:
        if _s3 is None:
            import boto3
            _s3 = boto3.client('s3')
        return _s3


def _prices_for(model: str) -> dict:
    m = (model or '').strip()
    if m in PRICING_USD_PER_MTOK:
        return PRICING_USD_PER_MTOK[m]
    for k, v in PRICING_USD_PER_MTOK.items():
        if m.startswith(k):
            return v
    ml = m.lower()
    if 'opus' in ml:
        return PRICING_USD_PER_MTOK['claude-opus-4-5']
    if 'haiku' in ml:
        return PRICING_USD_PER_MTOK['claude-haiku-4-5']
    return _DEFAULT_PRICES


def _usage_field(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        try:
            return int(usage.get(name) or 0)
        except Exception:
            return 0
    try:
        return int(getattr(usage, name, 0) or 0)
    except Exception:
        return 0


def cost_usd(model: str, usage: Any) -> float:
    """Dollar cost of one call from its usage object (or dict)."""
    p = _prices_for(model)
    in_tok = _usage_field(usage, 'input_tokens')
    out_tok = _usage_field(usage, 'output_tokens')
    cr_tok = _usage_field(usage, 'cache_read_input_tokens')
    cw_tok = _usage_field(usage, 'cache_creation_input_tokens')
    total = (in_tok * p['input'] + out_tok * p['output']
             + cr_tok * p['cache_read'] + cw_tok * p['cache_write'])
    return round(total / 1_000_000, 6)


def _put_record(record: dict) -> None:
    day = record['ts'][:10].replace('-', '_')
    key = (f"{CALLS_PREFIX}{day}/"
           f"{record['ts'][11:19].replace(':', '')}_{uuid.uuid4().hex[:10]}.json")
    _client().put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=json.dumps(record).encode('utf-8'),
        ContentType='application/json')


def _put_ppu_record(record: dict) -> None:
    """Mirror one billable call (or a logout marker) to the
    pay-per-use prefix the session sweep reads."""
    day = record['ts'][:10].replace('-', '_')
    key = (f"{PPU_CALLS_PREFIX}{day}/"
           f"{record['ts'][11:19].replace(':', '')}_{uuid.uuid4().hex[:10]}.json")
    _client().put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=json.dumps(record).encode('utf-8'),
        ContentType='application/json')


def record_call(surface: str, origin: str, model: str,
                usage: Any, extras: Optional[dict] = None,
                duration_s: Optional[float] = None) -> None:
    """Persist one model call's usage. Never raises; never blocks the
    caller (S3 put runs on a daemon thread).

    ``extras`` (optional) attaches attribution: user, user_email,
    session_id, request_id, and pay_per_use (bool). Every record
    carries billed_usd = cost_usd x PPU_MARKUP; when pay_per_use is
    set the record is also mirrored to PPU_CALLS_PREFIX so the
    session sweep can bill it to the user.

    ``duration_s`` (optional) is the call's wall-clock processing
    time (2026-08-26 Jenna: billing is metered time AND consumption,
    never per query). Stored on the record so session summaries and
    the admin rollup can report active processing minutes and the
    emergent billed-per-active-hour rate."""
    try:
        in_tok = _usage_field(usage, 'input_tokens')
        out_tok = _usage_field(usage, 'output_tokens')
        cr_tok = _usage_field(usage, 'cache_read_input_tokens')
        cw_tok = _usage_field(usage, 'cache_creation_input_tokens')
        if not (in_tok or out_tok or cr_tok or cw_tok):
            return
        cost = cost_usd(model, usage)
        record = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'surface': str(surface or 'other')[:32],
            'origin': str(origin or 'chatbot')[:32],
            'model': str(model or 'unknown')[:80],
            'input_tokens': in_tok,
            'output_tokens': out_tok,
            'cache_read_input_tokens': cr_tok,
            'cache_creation_input_tokens': cw_tok,
            'cost_usd': cost,
            'billed_usd': round(cost * PPU_MARKUP, 6),
        }
        try:
            if duration_s is not None and float(duration_s) > 0:
                record['duration_s'] = round(float(duration_s), 3)
        except (TypeError, ValueError):
            pass
        ppu = False
        if isinstance(extras, dict) and extras:
            for f in _EXTRA_FIELDS:
                v = extras.get(f)
                if v:
                    record[f] = str(v)[:120]
            ppu = bool(extras.get('pay_per_use'))
            if ppu:
                record['pay_per_use'] = True
        if _SYNC_FOR_TESTS:
            _put_record_safe(record, ppu)
            return
        t = threading.Thread(target=_put_record_safe, args=(record, ppu),
                             daemon=True)
        t.start()
    except Exception:
        pass


def _put_record_safe(record: dict, ppu: bool = False) -> None:
    try:
        _put_record(record)
    except Exception as e:
        try:
            print(f"[render-usage] record put failed: {e}")
        except Exception:
            pass
    if ppu:
        try:
            _put_ppu_record(record)
        except Exception as e:
            try:
                print(f"[render-usage] ppu mirror put failed: {e}")
            except Exception:
                pass


def record_ppu_marker(user: str, user_email: str, session_id: str) -> None:
    """Zero-cost logout marker on the pay-per-use prefix only. The
    session sweep treats it as an immediate session end for the user
    (no 30-minute wait). Never raises."""
    try:
        if not (user_email or '').strip():
            return
        record = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'surface': 'logout',
            'user': str(user or '')[:120],
            'user_email': str(user_email or '')[:120],
            'session_id': str(session_id or '')[:120],
            'cost_usd': 0.0,
            'billed_usd': 0.0,
            'pay_per_use': True,
            'logout': True,
        }
        if _SYNC_FOR_TESTS:
            try:
                _put_ppu_record(record)
            except Exception:
                pass
            return
        def _put():
            try:
                _put_ppu_record(record)
            except Exception as e:
                try:
                    print(f"[render-usage] logout marker put failed: {e}")
                except Exception:
                    pass
        threading.Thread(target=_put, daemon=True).start()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Ask log (2026-08-26, Jenna: "log the questions people are asking and
# then figure out how to be smarter with those periodicly")
# ---------------------------------------------------------------------------
# One small JSON object per question, same append-safe pattern as the
# per-call usage records above: no read-modify-write, so concurrent
# gunicorn workers can never clobber each other. Records land at
#
#     s3://dashboard-inputs/system/usage/ask_log/YYYY-MM-DD/<ts>_<uuid>.json
#
# The weekly miner (migration/ask_log_miner.py on the box) folds each
# finished day's objects into system/usage/ask_log/YYYY-MM-DD.jsonl and
# builds the weekly themes report from them.

ASK_PREFIX = 'system/usage/ask_log/'

# Test hook: when True, record_ask writes inline instead of on a daemon
# thread so unit tests can assert on the captured record synchronously.
_SYNC_FOR_TESTS = False


def _put_ask_record(record: dict) -> None:
    day = record['ts'][:10]
    key = (f"{ASK_PREFIX}{day}/"
           f"{record['ts'][11:19].replace(':', '')}_"
           f"{uuid.uuid4().hex[:10]}.json")
    _client().put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=json.dumps(record).encode('utf-8'),
        ContentType='application/json')


def _put_ask_record_safe(record: dict) -> None:
    try:
        _put_ask_record(record)
    except Exception as e:
        try:
            print(f"[ask-log] record put failed: {e}")
        except Exception:
            pass


def record_ask(*, user: str, view: str, question: str, surface: str,
               route: str, outcome: str, ms: int,
               mode: Optional[str] = None,
               subject: Optional[str] = None,
               extra: Optional[dict] = None,
               stages: Optional[dict] = None) -> None:
    """Persist one user question. Never raises; never blocks the caller
    (S3 put runs on a daemon thread, mirroring record_call).

    surface: which endpoint took the question ('analyze' | 'interpret').
    route:   which flow answered it ('page_analysis', 'search_demand',
             'reasoned_metrics', 'profile_build', 'subiq', 'incidence',
             'discovery', 'clarify', ...).
    outcome: what the user got ('answered', 'clarify', 'declined',
             'declined_not_quantifiable', 'declined_no_context',
             'declined_credits', 'error').
    stages:  optional per-stage wall-clock breakdown of the total 'ms'
             (2026-08-28 latency instrumentation): a small dict of
             stage name -> integer, e.g. {'digest': 812, 'anchors': 194,
             'model': 11938}. Values are milliseconds except *_rounds
             keys, which are counts. Capped at 16 entries.
    """
    try:
        q = str(question or '').strip()
        if not q:
            return
        record = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'user': str(user or 'unknown')[:120],
            'view': str(view or '')[:48],
            'question': q[:600],
            'surface': str(surface or 'analyze')[:24],
            'route': str(route or 'unknown')[:40],
            'outcome': str(outcome or 'unknown')[:40],
            'ms': max(int(ms or 0), 0),
        }
        if mode:
            record['mode'] = str(mode)[:40]
        if subject:
            record['subject'] = str(subject)[:120]
        if isinstance(extra, dict) and extra:
            clean = {}
            for k, v in list(extra.items())[:8]:
                if v is None:
                    continue
                clean[str(k)[:40]] = (v if isinstance(v, (int, float, bool))
                                      else str(v)[:200])
            if clean:
                record['extra'] = clean
        if isinstance(stages, dict) and stages:
            clean_stages = {}
            for k, v in list(stages.items())[:16]:
                try:
                    clean_stages[str(k)[:40]] = int(v)
                except (TypeError, ValueError):
                    continue
            if clean_stages:
                record['stages'] = clean_stages
        if _SYNC_FOR_TESTS:
            _put_ask_record_safe(record)
            return
        t = threading.Thread(target=_put_ask_record_safe, args=(record,),
                             daemon=True)
        t.start()
    except Exception:
        pass


__all__ = ['record_call', 'record_ask', 'record_ppu_marker', 'cost_usd',
           'PRICING_USD_PER_MTOK', 'ASK_PREFIX', 'PPU_MARKUP',
           'PPU_CALLS_PREFIX']
