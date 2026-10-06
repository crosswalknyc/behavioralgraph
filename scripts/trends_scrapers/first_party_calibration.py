"""First-party calibration for a streaming service's Trends IQ rail.

2026-10-06. A partner shared the monthly, title-level stream counts
Amazon reports to them for two of their Prime Video add-on channels,
Lionsgate+ and MovieSphere+, and asked that the rankers line up with
what they see. That is the best evidence a rail can have, so it does
not sit in a prompt as a hint: it sets the level.

What the data is
----------------
One workbook per service, a pivot of "Sum of Total Number of Streams"
by title by calendar month, movies only (the pivot's Content Type
filter is MOVIE), US, every offer. Lionsgate+ runs March to August
2026 (the service went live on 2026-04-09, so March is the soft
launch); MovieSphere+ runs October 2024 to August 2026, which also
says the service has been on Amazon Channels far longer than its
2026 US app launch. In August 2026 Lionsgate+ did about 690K film
streams across 725 titles (about 22K a day, top title about 1,100 a
day); MovieSphere+ did about 1.37M across 725 titles (about 44K a
day, top title about 2,000 a day). Our rails were 4x to 10x above
that at every rank, from reasoning alone.

How it is used
--------------
`build_first_party_calibration.py` turns a workbook into one JSON
document per service at

    s3://dashboard-inputs/trends_iq_snapshots/system/first_party/<slug>.json

holding the latest full month's service total, a rank-to-daily table
(rank 1..N, the month's titles sorted by streams, each divided by the
month's days), every title's latest monthly reading and trend, and
the range across months. This module reads that document and:

  1. `apply_to_store` sets every reading the service carries in the
     board store: a row on the service's published chart takes the
     first-party level of its chart position (the chart owns the
     order, the data owns the magnitude); a title the data names
     takes its own latest daily reading; a title the data does not
     name (new since the last month, or a TV title on a movies-only
     feed) is held under the level the data gives rank 10 (rank 3
     for TV, which streams by episode). Every value carries a salt
     from the title and the day so the rail moves organically and no
     two titles land on one number.
  2. `service_prompt_line` / `title_prompt_line` put the same facts
     in front of the model when it reasons about these services, so
     the reasoned number starts in the right place instead of being
     corrected afterwards.

The unit is a stream, which the rails already label as a daily US
view. The feed is movies only; TV rows are bounded by the service's
scale, not set by it. Everything here is fail-safe: no document, no
network, or a malformed one leaves the store exactly as it was.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

BUCKET = os.environ.get('TRENDS_SNAPSHOT_BUCKET', 'dashboard-inputs')
PREFIX = 'trends_iq_snapshots/system/first_party/'
CALIBRATED_SLUGS = ('lionsgateplus', 'moviesphereplus')

# A title the feed does not name is held under these ranks' levels.
UNKNOWN_FILM_RANK = 10
UNKNOWN_TV_RANK = 3
# Salt band around a first-party level: wide enough that a day's board
# moves and two titles at one level separate, narrow enough that the
# partner recognises their own number.
SALT_BAND = 0.09

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL_S = 3600.0


def _s3():
    import boto3
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def key_for(slug: str) -> str:
    return f'{PREFIX}{slug}.json'


def load(slug: str, *, force: bool = False) -> Optional[dict]:
    """The calibration document for `slug`, or None. Cached an hour."""
    if os.environ.get('TRENDS_FIRST_PARTY', '1') == '0':
        return None
    now = time.monotonic()
    hit = _CACHE.get(slug)
    if hit and not force and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    doc = None
    try:
        body = _s3().get_object(Bucket=BUCKET, Key=key_for(slug))['Body'].read()
        doc = json.loads(body)
        if not isinstance(doc, dict) or not doc.get('bands'):
            doc = None
    except Exception as e:  # noqa: BLE001
        if 'NoSuchKey' not in str(e):
            logger.info('first_party: no calibration for %s (%s)', slug, e)
        doc = None
    _CACHE[slug] = (now, doc)
    return doc


def set_cached(slug: str, doc: Optional[dict]) -> None:
    """Tests and the builder: hand the module a document directly."""
    _CACHE[slug] = (time.monotonic(), doc)


def calibrated_slugs() -> list[str]:
    return [s for s in CALIBRATED_SLUGS if load(s)]


# ---------------------------------------------------------------------------
# Reading the document
# ---------------------------------------------------------------------------
def band_for_rank(doc: dict, rank: int) -> Optional[int]:
    """Daily level the latest month gives a title at `rank` (1-based).
    Past the table, the last entry."""
    bands = doc.get('bands') or []
    if not bands:
        return None
    i = max(1, int(rank)) - 1
    if i >= len(bands):
        i = len(bands) - 1
    try:
        return max(1, int(bands[i]))
    except (TypeError, ValueError):
        return None


def title_reading(doc: dict, norm: str) -> Optional[dict]:
    """`{'daily': int, 'month': 'YYYY-MM', 'rank': int, 'monthly': int,
    'trend_pct': float|None}` for a title the feed names, else None."""
    t = (doc.get('titles') or {}).get(norm)
    if not isinstance(t, dict) or not t.get('daily'):
        return None
    return t


def _h01(salt: str) -> float:
    """Deterministic draw in [0, 1) from a salt."""
    h = int(hashlib.sha256(salt.encode('utf-8')).hexdigest()[:12], 16)
    return h / float(0xFFFFFFFFFFFF + 1)


def _salted(value: int, salt: str, band: float = SALT_BAND) -> int:
    """`value` nudged by a deterministic share in [-band, +band]."""
    frac = _h01(salt) * 2.0 - 1.0
    return max(1, int(round(value * (1.0 + band * frac))))


def _natural(value: int, title: str, salt: str) -> int:
    try:
        from scripts.trends_scrapers import stream_estimates as se
        return max(1, se._natural_last_digits(max(1, int(value)), title, salt))
    except Exception:  # noqa: BLE001
        return max(1, int(value))


# ---------------------------------------------------------------------------
# Prompt text
# ---------------------------------------------------------------------------
def service_prompt_line(slug: str) -> str:
    """One sentence for the service's TARGET_PLATFORMS entry."""
    doc = load(slug)
    if not doc:
        return ''
    svc = doc.get('service') or {}
    bands = doc.get('bands') or []

    def b(r):
        v = band_for_rank(doc, r)
        return f'{v:,}' if v else '?'

    month = svc.get('month') or 'the latest month'
    parts = [
        f'PLATFORM-REPORTED (title-level, {month}, films): service-wide '
        f'about {int(svc.get("daily_total") or 0):,} film streams a day '
        f'across {int(svc.get("titles") or len(bands)):,} titles; '
        f'the #1 film about {b(1)} a day, #5 about {b(5)}, #10 about '
        f'{b(10)}, #25 about {b(25)}, #100 about {b(100)}.'
    ]
    rng = doc.get('range') or {}
    if rng.get('daily_total_min') and rng.get('daily_total_max'):
        parts.append(
            f'Across {rng.get("months", "the")} months the service ran '
            f'{int(rng["daily_total_min"]):,} to '
            f'{int(rng["daily_total_max"]):,} film streams a day and no '
            f'film ever exceeded about {int(rng.get("title_daily_max") or 0):,} '
            f'a day.')
    parts.append('These are measured counts. A number for this service '
                 'must sit inside this scale; a TV series streams by '
                 'episode and may sit a little above the film #1, never '
                 'above the service.')
    return ' '.join(parts)


def title_prompt_line(slug: str, title: str) -> str:
    """One line for the item block when the feed names this title."""
    doc = load(slug)
    if not doc:
        return ''
    try:
        from scripts.trends_scrapers import stream_estimates as se
        norm = se._cp_normalize(title)
    except Exception:  # noqa: BLE001
        return ''
    t = title_reading(doc, norm) or title_reading(doc, _loose_norm(norm))
    if not t:
        return ''
    label = (doc.get('label') or slug)
    line = (f'PLATFORM-REPORTED for "{title}" on {label}: '
            f'{int(t["monthly"]):,} streams in {t["month"]} '
            f'(about {int(t["daily"]):,} a day, #{int(t["rank"])} of the '
            f'service\'s films that month)')
    tp = t.get('trend_pct')
    if tp is not None and -90.0 < float(tp) < 300.0:
        # A title added mid-month reads as +20,000% the month after;
        # that is arrival, not a trend, so it is left unsaid.
        line += f', {float(tp):+.0f}% on the month before'
    return line + '. Start from this number.'


def _loose_norm(norm: str) -> str:
    """Drop edition and year tags the storefront adds and the feed
    sometimes carries: 'red 4k uhd' -> 'red', 'wrong turn 2020' ->
    'wrong turn', 'rambo last blood extended cut' -> 'rambo last blood'."""
    toks = norm.split()
    drop = {'4k', 'uhd', 'hd', 'extended', 'cut', 'unrated', 'theatrical',
            'edition', 'feature', 'film', 'version', 'remastered', 'directors'}
    out = [t for t in toks if t not in drop and not (t.isdigit() and len(t) == 4
                                                   and 1900 <= int(t) <= 2100)]
    return ' '.join(out) or norm


# ---------------------------------------------------------------------------
# Applying to the store
# ---------------------------------------------------------------------------
def _kind_of(key: str, it: dict) -> str:
    k = key.split(':', 1)[0]
    if k == 'film':
        return 'film'
    if k == 'tv':
        return 'tv'
    cd = str(it.get('category_display') or '').strip().lower()
    if cd.startswith(('film', 'movie')):
        return 'film'
    if cd:
        return 'tv'
    return 'film'


def apply_to_store(researched: dict[str, dict], target_date_iso: str,
                   *, slugs: Optional[list[str]] = None) -> dict[str, Any]:
    """Set every reading each calibrated service carries.

    Two passes per service. The first classifies every row the service
    carries: on the service's published chart today (`chart`), named by
    the partner feed (`titled`), or neither (`held`). The second sets the
    levels so the three groups agree with each other:

      chart   the feed's level for that position, strictly descending in
              chart order (the chart owns order, the feed owns scale);
      titled  the title's own feed reading, the whole group scaled down
              as one block when its top would clear the chart's last slot
              (the feed's order among them is kept, nothing is promoted
              above a charted title);
      held    only ever pulled DOWN, onto the feed's rank bands starting
              at rank 3 (TV, which streams by episode) or rank 10 (film),
              in the order our own reading already had them, and under
              the chart floor when there is one.

    Returns counts per slug: chart / titled / held / unchanged."""
    from scripts.trends_scrapers import stream_estimates as se
    stats: dict[str, Any] = {'moved': 0, 'slugs': {}}
    for slug in (slugs or list(CALIBRATED_SLUGS)):
        doc = load(slug)
        if not doc:
            continue
        per = {'chart': 0, 'titled': 0, 'held': 0, 'unchanged': 0}
        index = None
        try:
            snap = se._read_snapshot(se.published_chart_snapshot(slug))
            index = se.published_chart_index(slug, snap) if snap else None
        except Exception:  # noqa: BLE001
            index = None
        titles = doc.get('titles') or {}
        loose: dict[str, dict] = {}
        for n, t in titles.items():
            ln = _loose_norm(n)
            if ln != n and ln not in titles:
                loose.setdefault(ln, t)

        # ---- pass 1: classify ------------------------------------------
        chart_rows: list[tuple[int, str, dict, int]] = []   # (pos, key, it, cur)
        titled_rows: list[tuple[str, dict, int, int]] = []  # (key, it, cur, daily)
        held_rows: dict[str, list[tuple[str, dict, int]]] = {'tv': [], 'film': []}
        for key, it in researched.items():
            if not isinstance(it, dict):
                continue
            blk = (it.get('by_platform') or {}).get(slug)
            if not isinstance(blk, dict):
                continue
            cur = int(blk.get('us_estimate') or 0)
            if cur <= 0 or not key.startswith(('film:', 'tv:', 'title:')):
                continue
            norm = key.split(':', 1)[1]
            title = str(it.get('display_title') or norm)
            kind = _kind_of(key, it)
            hit = None
            if index:
                for k in ((kind,) if kind != 'film' else ('film', 'tv')):
                    hit = se.published_rank_for(index, k, title) \
                        or se.published_rank_for(index, k, norm)
                    if hit:
                        break
            if hit and band_for_rank(doc, int(hit[0])):
                chart_rows.append((int(hit[0]), key, it, cur))
                continue
            t = title_reading(doc, norm) or title_reading(doc, _loose_norm(norm)) \
                or loose.get(norm) or loose.get(_loose_norm(norm))
            if t and int(t.get('daily') or 0) > 0:
                titled_rows.append((key, it, cur, int(t['daily'])))
                continue
            held_rows['tv' if kind == 'tv' else 'film'].append((key, it, cur))

        def _write(key: str, it: dict, cur: int, target: int, why: str) -> None:
            title = str(it.get('display_title') or key.split(':', 1)[1])
            salt = f'{target_date_iso}|first_party|{slug}|{key}'
            target = max(1, _natural(int(target), title, salt))
            if target == cur:
                per['unchanged'] += 1
                return
            if se._set_platform_reading(it, slug, target, key, salt):
                per[why] += 1
                stats['moved'] += 1
            else:
                per['unchanged'] += 1

        # ---- pass 2a: chart rows, descending in chart order ------------
        chart_rows.sort(key=lambda r: r[0])
        floor: Optional[int] = None
        prev: Optional[int] = None
        for pos, key, it, cur in chart_rows:
            salt = f'{target_date_iso}|first_party|{slug}|{key}'
            target = _salted(band_for_rank(doc, pos) or 1, salt, 0.04)
            if prev is not None and target >= prev:
                target = max(1, int(prev * (1.0 - 0.012 - 0.03 * _h01(salt + '|dn'))))
            prev = target
            floor = target
            _write(key, it, cur, target, 'chart')
        if floor is not None:
            floor = max(1, int(floor * 0.97))

        # ---- pass 2b: titled rows, scaled as a block under the floor ---
        if titled_rows:
            top = max(d for _, _, _, d in titled_rows)
            factor = 1.0
            if floor is not None and top > floor:
                factor = floor / float(top)
            for key, it, cur, daily in titled_rows:
                salt = f'{target_date_iso}|first_party|{slug}|{key}'
                _write(key, it, cur, _salted(int(daily * factor), salt), 'titled')

        # ---- pass 2c: held rows, pulled down onto the rank bands --------
        # A held row is only touched while it sits ABOVE its group's
        # scale (the group's top band, or the chart floor). Rows already
        # inside the scale keep the reading our own reasoning gave them,
        # so a second pass on the same day finds nothing to move.
        for kind, rows in held_rows.items():
            rows.sort(key=lambda r: (-r[2], r[0]))
            start = UNKNOWN_TV_RANK if kind == 'tv' else UNKNOWN_FILM_RANK
            top = band_for_rank(doc, start) or 1
            if floor is not None:
                top = min(top, floor)
            for i, (key, it, cur) in enumerate(rows):
                if cur <= top:
                    per['unchanged'] += 1
                    continue
                salt = f'{target_date_iso}|first_party|{slug}|{key}'
                lid = band_for_rank(doc, start + i)
                if lid is None:
                    lid = band_for_rank(doc, len(doc.get('bands') or [1])) or 1
                lid = _salted(lid, salt)
                if floor is not None:
                    lid = min(lid, max(1, int(floor * (1.0 - 0.01 * (i + 1)))))
                _write(key, it, cur, min(lid, cur), 'held')
        stats['slugs'][slug] = per
        logger.info('first_party: %s -> %s', slug, per)
    return stats


# ---------------------------------------------------------------------------
# CLI: apply to the live store once
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='Apply first-party calibration '
                                 'to the live board store')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--slug', action='append', default=[])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base
    from scripts.trends_scrapers.run_guard import BoardLock
    with BoardLock('First-party calibration'):
        board = se._read_snapshot('stream_estimates') or {}
        items = board.get('items') or {}
        if not items:
            print('no store'); return 2
        day = board.get('target_date') or ''
        if args.slug:
            stats = apply_to_store(items, day, slugs=args.slug)
            print(json.dumps(stats))
        # The finalize chain carries the first-party step itself.
        fin = se._finalize_published_charts(items, day)
        print('finalize:', {k: (v.get('moved') if isinstance(v, dict) else v)
                            for k, v in fin.items()})
        if args.dry_run:
            print('dry-run: nothing written'); return 0
        _base.write_snapshot('stream_estimates', board)
        try:
            import trends_iq
            n = trends_iq.invalidate_live_compute_view_caches()
            print(f'purged {n} live cache entries')
        except Exception:  # noqa: BLE001
            logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
