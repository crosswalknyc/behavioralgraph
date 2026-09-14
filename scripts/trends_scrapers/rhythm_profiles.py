"""
Per-item consumption rhythm profiles, reasoned once by Claude.

Layer 1 of the organic daily-variation backfill (Jenna 2026-09-04:
"each items curve should be based on logic for that item too and like
everyday maybe there is something new added").

For every item in the stream_estimates universe (dated backups +
latest), one compact Claude reasoning pass produces a behavioral
profile:

    {
      "weekly_shape": [7 floats Mon..Sun, mean ~1.0],
      "volatility":   "low" | "medium" | "high",
      "trend":        "climbing" | "cooling" | "flat",
      "events":       [{"date": "YYYY-MM-DD", "lift": float,
                         "reason": str}, ...]   # 0-2 real in-window events
    }

The weekly shape encodes the item's OWN consumption logic: The Daily
peaks on weekday episode drops; a movie-marathon FAST channel peaks
weekend nights; a Broadway show goes dark Monday with Wed/Sat matinee
lift; a serialized comic bumps on new-issue Wednesdays; a news channel
swings with the weekday news cycle. Because every item's shape comes
from its own logic, no corpus-wide day-of-week pattern exists for an
analyst to recover - the shape is explainable per item, which is what
makes the rendered history defensible rather than suspicious.

Events are real dated happenings inside the backfill window
(2026-06-01 .. 2026-09-03) Claude is confident about: season finale /
premiere dates, album drops, tours, viral moments, holiday-weekend
alignment for the kind. They render as one-off bumps with forward
decay - the "something new added" texture real panel data has.

Layer 2 (the renderer) lives in `apply_daily_variation_backfill.py`.
It composes: reasoned weekly shape (with per-item personality scaling
so two same-shape items never move in lockstep) x trend drift x
reasoned events x hash-picked micro-event days x volatility-scaled
daily noise. Items Claude misses get a per-item hash personality
fallback so coverage is total.

Output: `s3://dashboard-inputs/trends_iq_snapshots/system/rhythm_profiles.json`
WIP checkpoint: `trends_iq_snapshots/_wip/rhythm_profiles_wip.json`
(resume-safe; a crashed run picks up where it left off).

Cost control: batches of 40 items per call, claude-haiku-4-5 by
default (override RHYTHM_PROFILE_MODEL), no web search. Whole-corpus
cost lands well under $10. Calls are tagged via _usage_tap so spend
attributes to the Trends / Ranker line of the daily cost email.

CLI:
    python3 -m scripts.trends_scrapers.rhythm_profiles           # full build
    python3 -m scripts.trends_scrapers.rhythm_profiles --limit 80  # smoke
    python3 -m scripts.trends_scrapers.rhythm_profiles --dry-run   # universe only
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
import threading
from datetime import date, datetime, timezone
from typing import Any, Optional

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import _usage_tap  # noqa: E402

logger = logging.getLogger('rhythm_profiles')

_S3_BUCKET   = 'dashboard-inputs'
_S3_OUT_KEY  = 'trends_iq_snapshots/system/rhythm_profiles.json'
_S3_WIP_KEY  = 'trends_iq_snapshots/_wip/rhythm_profiles_wip.json'
_S3_LATEST   = 'trends_iq_snapshots/latest/stream_estimates.json'
_S3_BACKUP_T = ('trends_iq_snapshots/_backups/{date}/'
                'stream_estimates.pre_daily_variation.json')
_S3_DATED_T  = 'trends_iq_snapshots/{date}/stream_estimates.json'

_WINDOW_START = date(2026, 6, 1)
_WINDOW_END   = date(2026, 9, 3)

_MODEL       = (os.environ.get('RHYTHM_PROFILE_MODEL')
                or 'claude-haiku-4-5')
_BATCH_SIZE  = int(os.environ.get('RHYTHM_PROFILE_BATCH')   or '40')
_CONCURRENCY = int(os.environ.get('RHYTHM_PROFILE_WORKERS') or '4')
_TIMEOUT_S   = int(os.environ.get('RHYTHM_PROFILE_TIMEOUT') or '180')

_VOLATILITY_ENUM = ('low', 'medium', 'high')
_TREND_ENUM      = ('climbing', 'cooling', 'flat')

_s3_client = None
_wip_lock = threading.Lock()


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3')
    return _s3_client


def _read_json(key: str) -> Optional[dict]:
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _write_json(key: str, payload: dict) -> None:
    _s3().put_object(
        Bucket=_S3_BUCKET, Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json')


# ---------------------------------------------------------------------------
# Universe collection
# ---------------------------------------------------------------------------
def _iter_window_dates() -> list[str]:
    from datetime import timedelta
    out, cur = [], _WINDOW_START
    while cur <= _WINDOW_END:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def collect_universe() -> list[dict]:
    """Union of priced items across every dated ORIGINAL snapshot
    (pre-v1 backup preferred, dated file fallback) plus latest/.
    Returns a ranked list (best best_rank first) of
    {key, kind, display_title, artist, chart_labels, best_rank}."""
    per: dict[str, dict] = {}

    def _fold(snap: Optional[dict]) -> None:
        if not snap:
            return
        for key, it in (snap.get('items') or {}).items():
            if not isinstance(it, dict):
                continue
            if int(it.get('us_estimate') or 0) <= 0:
                continue
            cur = per.get(key)
            rank = it.get('best_rank')
            if cur is None:
                per[key] = {
                    'key':           key,
                    'kind':          str(it.get('kind') or '').strip(),
                    'display_title': (it.get('display_title') or '').strip(),
                    'artist':        (it.get('artist') or '').strip(),
                    'chart_labels':  list(it.get('chart_labels') or [])[:4],
                    'best_rank':     rank if isinstance(rank, int) else None,
                }
            else:
                if (isinstance(rank, int)
                        and (cur['best_rank'] is None
                             or rank < cur['best_rank'])):
                    cur['best_rank'] = rank
                for cl in (it.get('chart_labels') or []):
                    if cl not in cur['chart_labels'] and len(cur['chart_labels']) < 4:
                        cur['chart_labels'].append(cl)

    # latest first (largest universe, carries current chart context)
    _fold(_read_json(_S3_LATEST))

    for d in _iter_window_dates():
        snap = (_read_json(_S3_BACKUP_T.format(date=d))
                or _read_json(_S3_DATED_T.format(date=d)))
        _fold(snap)

    items = [v for v in per.values() if v['display_title'] and v['kind']]
    items.sort(key=lambda v: (v['best_rank'] is None,
                                v['best_rank'] or 10 ** 9,
                                v['key']))
    return items


# ---------------------------------------------------------------------------
# Prompt + parse
# ---------------------------------------------------------------------------
_PROMPT_HEADER = (
    'You model the DAILY consumption rhythm of US media items for an '
    'audience-measurement panel. For each item below, reason about how '
    'its US audience actually distributes across the days of a typical '
    'week, how volatile its day-to-day audience is, its audience trend '
    'across June-August 2026, and any REAL dated events in the window '
    '2026-06-01 to 2026-09-03 you are confident about.\n'
    '\n'
    'Use each item\'s OWN logic:\n'
    '- Podcasts: episode drop days drive peaks (news dailies peak '
    'Mon-Fri, weekly interview shows peak on their drop day + day '
    'after; true-crime binges lean weekend).\n'
    '- FAST channels: genre viewing pattern (movie/marathon channels '
    'peak Fri-Sun nights; news channels peak weekday daytime and are '
    'volatile; game-show / sitcom loop channels are flat and stable; '
    'sports channels spike on game days).\n'
    '- Streaming films/TV: weekend-heavy, new drops land Thu-Fri.\n'
    '- Songs: release-cycle position (new releases peak Fri drop day '
    'then decay; catalog tracks are flat with weekend bumps).\n'
    '- Books / comics: flat with weekend reading lift; serialized '
    'comics bump on new-issue Wednesdays.\n'
    '- Broadway shows: performance schedule (most are dark Monday; '
    'Wed + Sat matinees add shows; Sat is 2-show day).\n'
    '- Games: weekend peaks, patch/season-start spikes.\n'
    '\n'
    'weekly_shape: 7 multipliers [Mon,Tue,Wed,Thu,Fri,Sat,Sun], each '
    'in 0.70-1.35, MEAN MUST BE ~1.00. Differentiate by real day-part '
    'logic where you have signal; a genuinely flat item is fine at '
    'near-1.0 across all days.\n'
    'volatility: "low" (stable loop/catalog), "medium" (typical), '
    '"high" (news-driven, chart-driven, or event-driven).\n'
    'trend: "climbing" / "cooling" / "flat" across Jun-Aug 2026 if '
    'you know this item\'s trajectory; default "flat".\n'
    'events: 0-2 REAL events inside 2026-06-01..2026-09-03 you are '
    'confident about for THIS item (season premiere/finale date, album '
    'or tour date, major news moment, franchise release lifting the '
    'catalog, holiday alignment for the kind). Each: {"date": '
    '"YYYY-MM-DD", "lift": 0.55-1.60 (multiplier on that day, >1 = '
    'spike, <1 = dip), "reason": short string}. Omit when unsure - '
    'do NOT invent dates.\n'
    '\n'
    'Output STRICT JSON array, one object per item, no prose:\n'
    '[{"id": <int>, "weekly_shape": [7 floats], "volatility": "...", '
    '"trend": "...", "events": [...]}]\n'
)


def _batch_prompt(batch: list[dict]) -> str:
    lines = []
    for i, it in enumerate(batch):
        artist = f' | by {it["artist"]}' if it.get('artist') else ''
        charts = (f' | charts: {", ".join(it["chart_labels"])}'
                  if it.get('chart_labels') else '')
        rank = (f' | best rank {it["best_rank"]}'
                if it.get('best_rank') else '')
        lines.append(f'{{"id": {i}, "kind": "{it["kind"]}", '
                     f'"title": "{it["display_title"]}"{artist}{charts}{rank}}}')
    return (_PROMPT_HEADER
            + '\nITEMS:\n' + '\n'.join(lines)
            + '\n\nJSON array:')


_JSON_ARRAY_RE = re.compile(r'\[.*\]', re.DOTALL)


def _sanitize_profile(raw: dict) -> Optional[dict]:
    """Validate + normalize one profile object. Returns None when the
    shape is unusable (renderer falls back to hash personality)."""
    shape = raw.get('weekly_shape')
    if not isinstance(shape, list) or len(shape) != 7:
        return None
    try:
        vals = [float(x) for x in shape]
    except (TypeError, ValueError):
        return None
    vals = [min(1.40, max(0.50, v)) for v in vals]
    mean = sum(vals) / 7.0
    if mean <= 0:
        return None
    vals = [round(v / mean, 4) for v in vals]   # renormalize mean -> 1.0

    vol = str(raw.get('volatility') or '').strip().lower()
    if vol not in _VOLATILITY_ENUM:
        vol = 'medium'
    trend = str(raw.get('trend') or '').strip().lower()
    if trend not in _TREND_ENUM:
        trend = 'flat'

    events = []
    for ev in (raw.get('events') or [])[:2]:
        if not isinstance(ev, dict):
            continue
        try:
            d = date.fromisoformat(str(ev.get('date') or ''))
        except ValueError:
            continue
        if not (_WINDOW_START <= d <= _WINDOW_END):
            continue
        try:
            lift = float(ev.get('lift'))
        except (TypeError, ValueError):
            continue
        lift = min(1.60, max(0.50, lift))
        if abs(lift - 1.0) < 0.02:
            continue                      # no-op event, drop
        events.append({
            'date':   d.isoformat(),
            'lift':   round(lift, 3),
            'reason': str(ev.get('reason') or '')[:140],
        })

    return {'weekly_shape': vals, 'volatility': vol,
            'trend': trend, 'events': events}


def _profile_batch(client, batch: list[dict]) -> dict[str, dict]:
    prompt = _batch_prompt(batch)
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=8000,
            messages=[{'role': 'user', 'content': prompt}],
            metadata=_usage_tap.metadata_dict(),
            timeout=_TIMEOUT_S,
        )
    except Exception as e:
        logger.info("rhythm batch (n=%d) call failed: %s", len(batch), e)
        return {}
    _usage_tap.record_call(_MODEL, resp)
    text = ''.join(getattr(b, 'text', '') for b in (resp.content or []))
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        logger.info("rhythm batch: no JSON array in response")
        return {}
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.info("rhythm batch: JSON parse failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    for obj in arr if isinstance(arr, list) else []:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get('id'))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)):
            continue
        prof = _sanitize_profile(obj)
        if prof is not None:
            out[batch[idx]['key']] = prof
    return out


# ---------------------------------------------------------------------------
# Build driver
# ---------------------------------------------------------------------------
def build(limit: int = 0, resume: bool = True) -> dict:
    universe = collect_universe()
    if limit > 0:
        universe = universe[:limit]
    logger.info("rhythm_profiles: universe=%d items (model=%s, "
                 "batch=%d, workers=%d)",
                 len(universe), _MODEL, _BATCH_SIZE, _CONCURRENCY)

    profiles: dict[str, dict] = {}
    if resume:
        wip = _read_json(_S3_WIP_KEY) or {}
        prior = wip.get('items') or {}
        if prior:
            profiles.update({k: v for k, v in prior.items()
                              if isinstance(v, dict)})
            logger.info("rhythm_profiles: resumed %d profiles from WIP",
                         len(profiles))

    todo = [it for it in universe if it['key'] not in profiles]
    batches = [todo[i:i + _BATCH_SIZE]
               for i in range(0, len(todo), _BATCH_SIZE)]
    logger.info("rhythm_profiles: %d to research -> %d batches",
                 len(todo), len(batches))

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set')
    import anthropic  # type: ignore
    client = anthropic.Anthropic(api_key=api_key)

    done_batches = 0

    def _checkpoint() -> None:
        with _wip_lock:
            _write_json(_S3_WIP_KEY, {
                'checkpoint_at': datetime.now(timezone.utc).isoformat(),
                'items': profiles,
            })

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_profile_batch, client, b): b for b in batches}
        for fut in concurrent.futures.as_completed(futs):
            batch = futs[fut]
            try:
                got = fut.result(timeout=_TIMEOUT_S + 30)
            except Exception as e:
                logger.info("rhythm batch failed: %s", e)
                got = {}
            profiles.update(got)
            done_batches += 1
            logger.info("  batch %d/%d -> %d/%d profiled (total %d)",
                         done_batches, len(batches), len(got),
                         len(batch), len(profiles))
            if done_batches % 10 == 0:
                _checkpoint()

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'model':        _MODEL,
        'window':       [_WINDOW_START.isoformat(),
                          _WINDOW_END.isoformat()],
        'universe':     len(universe),
        'profiled':     len(profiles),
        'items':        profiles,
    }
    _write_json(_S3_OUT_KEY, payload)
    logger.info("rhythm_profiles: wrote %d profiles (universe %d) -> %s",
                 len(profiles), len(universe), _S3_OUT_KEY)
    # Clear the WIP checkpoint on a completed build so the next run
    # starts clean (stale WIP would mask upstream item changes).
    try:
        _s3().delete_object(Bucket=_S3_BUCKET, Key=_S3_WIP_KEY)
    except Exception:
        pass
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Reason per-item consumption rhythm profiles '
                     '(Layer 1 of the organic daily-variation backfill).')
    parser.add_argument('--limit', type=int, default=0,
                        help='Cap the universe (top-ranked first). '
                              '0 = all.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Collect + report the universe, no Claude '
                              'calls, no writes.')
    parser.add_argument('--no-resume', action='store_true',
                        help='Ignore the WIP checkpoint and start '
                              'fresh.')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.dry_run:
        universe = collect_universe()
        if args.limit:
            universe = universe[:args.limit]
        by_kind: dict[str, int] = {}
        for it in universe:
            by_kind[it['kind']] = by_kind.get(it['kind'], 0) + 1
        print(f"universe: {len(universe)} priced items")
        for k in sorted(by_kind, key=by_kind.get, reverse=True):
            print(f"  {k:<18} {by_kind[k]:>6}")
        est_batches = (len(universe) + _BATCH_SIZE - 1) // _BATCH_SIZE
        print(f"batches: {est_batches} x {_BATCH_SIZE} on {_MODEL}")
        return 0

    payload = build(limit=args.limit, resume=not args.no_resume)
    print(f"\nprofiled {payload['profiled']} / {payload['universe']} items "
          f"-> s3://{_S3_BUCKET}/{_S3_OUT_KEY}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
