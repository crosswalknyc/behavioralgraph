"""IQ Rankers daily scoring from scraped plus researched signals.

The replacement for the clickstream pass in `iq_rankers.
run_daily_for_all_profiles`. Same entities, same CW IQ Score, same
self-relative property. Different measurement layer.

    volume  <- share of the day's public attention across search, news,
               encyclopedia and chart surfaces, from the scraped boards
    reach   <- the largest researched daily US audience the entity
               reached on any one surface that day

Both come out of `iq_ranker_signals`. The score itself is
`iq_rankers.compute_cw_iq_score`, called unmodified, so Volume stays at
0.50, Reach at 0.30, Momentum at 0.15, Recency at 0.05, every z-score is
still taken against that entity's own trailing baseline, and the
cold-start ramp still holds a brand-new entity down for its first three
days. Nothing reads the clickstream.

Entities with no signal
-----------------------
An entity that matched nothing today AND has nothing in its trailing
baseline gets `has_signal = 0` and a NULL score. It is not given 50.
Feeding zeros into the composite produces exactly 50.0 (every z term
collapses and the sigmoid sits at its midpoint), which would put a
silent actor level with a genuinely average one. The leaderboard should
render these as "No signal" and sort them last; see the FRONTEND note at
the bottom of this docstring.

An entity that matched nothing today but DID have signal recently keeps
a real score. Going quiet is a measurement, not an absence of one.

EVC / TDL / BVP
---------------
Retired, not reconstructed. All three were defined as the share of a
profile's matched clickstream events that landed on a particular class
of host: streaming services for EVC, most-purchased-brand hosts for TDL,
streaming plus cinema for BVP. There is no host class in a scraped board,
so any number we put in those columns would be a different measurement
wearing the old name and the old tooltip.

What replaces them is `surface_mix`, written on every row: the share of
this entity's activity that came from each of search, news, encyclopedia
and charts. It answers a question the old columns gestured at ("is the
attention on this person about their work, or about them") and it is
defined by what actually feeds it.

FRONTEND (not changed here, reported for review)
------------------------------------------------
  1. Drop the EVC, TDL and BVP columns and their sort keys. Add one
     Signal Mix column reading the `surface_mix` field.
  2. Render `has_signal = 0` rows as "No signal", not as a score, and
     sort them below every scored row.
  3. The Engagements column is currently a panel count projected to the
     US population. On this path `signal_reach` is already a US number,
     so the projection must not be applied twice. Label it as the
     audience it is.

Usage on Hetzner:

    python3 -m scripts.trends_scrapers.iq_rankers_signal_daily --create-table
    python3 -m scripts.trends_scrapers.iq_rankers_signal_daily
    python3 -m scripts.trends_scrapers.iq_rankers_signal_daily --date 2026-09-14
    python3 -m scripts.trends_scrapers.iq_rankers_signal_daily \
        --date 2026-09-15 --days 28        # backfill oldest first
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

import iq_ranker_signals as sig      # noqa: E402
import iq_rankers as R               # noqa: E402

BUCKET = os.environ.get('IQR_SIGNAL_BUCKET', sig.S3_DEFAULT_BUCKET)
CACHE_KEY = 'system/s3_cache.json'
TERMS_CACHE_KEY = f'{sig.SNAPSHOT_PREFIX}/system/iq_ranker_entity_terms.json'
TABLE = 'reference.profile_iq_daily_signal_metrics'

CH_HOST = os.environ.get('CLICKHOUSE_HOST', '168.119.215.48')
CH_PORT = os.environ.get('CLICKHOUSE_PORT', '8123')
CH_USER = os.environ.get('CLICKHOUSE_USER', 'bgapp')
CH_PASS = os.environ.get('CLICKHOUSE_PASSWORD', '')

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    snapshot_date     Date,
    profile_subject   String,
    project_name      String,
    category          String,
    subcategory       String,
    s3_key            String,
    signal_volume     Float64,
    signal_reach      UInt64,
    matched_items     UInt16,
    surfaces          Array(String),
    surface_mix       String,
    has_signal        UInt8,
    cw_iq_score       Nullable(Float64),
    prev_volume       Float64,
    prev_cw_iq_score  Nullable(Float64),
    top_items         String,
    generated_at      DateTime
) ENGINE = ReplacingMergeTree(generated_at)
ORDER BY (snapshot_date, category, subcategory, profile_subject)
"""


# ---------------------------------------------------------------------------
# ClickHouse over HTTP - the metrics table only, never an event table
# ---------------------------------------------------------------------------


def ch(sql: str, *, read: bool = False) -> str:
    qs = urllib.parse.urlencode({'user': CH_USER, 'password': CH_PASS})
    url = f'http://{CH_HOST}:{CH_PORT}/?{qs}'
    req = urllib.request.Request(url, data=sql.encode('utf-8'), method='POST')
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read().decode('utf-8', errors='ignore')


def _esc(s: str) -> str:
    return (s or '').replace('\\', '\\\\').replace("'", "''")


# ---------------------------------------------------------------------------
# Entities and their names
# ---------------------------------------------------------------------------


def load_entities(s3) -> list[dict]:
    body = s3.get_object(Bucket=BUCKET, Key=CACHE_KEY)['Body'].read()
    jobs = (json.loads(body) or {}).get('jobs') or []
    return list(R._iter_profile_jobs(jobs))


def load_terms_cache(s3) -> dict:
    try:
        body = s3.get_object(Bucket=BUCKET, Key=TERMS_CACHE_KEY)['Body'].read()
        return json.loads(body) or {}
    except Exception:
        return {}


def save_terms_cache(s3, cache: dict) -> None:
    try:
        s3.put_object(Bucket=BUCKET, Key=TERMS_CACHE_KEY,
                      Body=json.dumps(cache).encode(),
                      ContentType='application/json')
    except Exception as e:
        print(f'[signal_daily] terms cache write failed: {e}')


def resolve_terms(s3, entities: list[dict], *, workers: int = 12) -> dict:
    """Resolved ranker terms per profile, cached in S3 by s3_key.

    Same resolver the clickstream path used
    (`iq_rankers._build_ranker_brand_terms` over the profile's BRAND
    INPUT row), so the two scorings are matching on the same names and
    the comparison is apples to apples.
    """
    cache = load_terms_cache(s3)
    missing = [j for j in entities
               if (j.get('s3_key') or '') and (j.get('s3_key') not in cache)]
    if missing:
        print(f'[signal_daily] resolving terms for {len(missing)} profile(s)')

        def _one(job):
            key = job.get('s3_key') or ''
            name = job.get('display_name') or job.get('project_name') or ''
            csv_terms = R.read_brand_input_from_csv(s3, BUCKET, key)
            return key, R._build_ranker_brand_terms(name, csv_terms)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for f in as_completed([ex.submit(_one, j) for j in missing]):
                try:
                    k, terms = f.result()
                    if k:
                        cache[k] = terms
                except Exception:
                    pass
        save_terms_cache(s3, cache)
    return cache


def build_index(entities: list[dict], terms: dict) -> sig.EntityIndex:
    return sig.build_entity_index(
        entities, term_resolver=lambda j: terms.get(j.get('s3_key') or '') or [])


# ---------------------------------------------------------------------------
# Scoring one day
# ---------------------------------------------------------------------------


def _history_from_db(day: str) -> dict[str, list[dict]]:
    """Trailing baseline per entity, most recent first.

    Reads this module's own metrics table. Shaped as `mentions` /
    `unique_uids` so `iq_rankers.compute_cw_iq_score` can be called
    unchanged: volume stands where matched-event count used to stand,
    reach stands where unique people used to stand.
    """
    start = (date.fromisoformat(day)
             - timedelta(days=R.CW_IQ_BASELINE_DAYS)).isoformat()
    sql = (f"SELECT profile_subject, toString(snapshot_date), signal_volume, "
           f"signal_reach, ifNull(cw_iq_score, -1) FROM {TABLE} "
           f"WHERE snapshot_date >= toDate('{start}') "
           f"AND snapshot_date < toDate('{day}') "
           f"ORDER BY snapshot_date DESC FORMAT TSV")
    out: dict[str, list[dict]] = defaultdict(list)
    try:
        for line in ch(sql).splitlines():
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 5:
                continue
            subj, _d, vol, reach, score = parts[:5]
            out[subj].append({'mentions': float(vol or 0),
                              'unique_uids': float(reach or 0),
                              'cw_iq_score': float(score or 0)})
    except Exception as e:
        print(f'[signal_daily] history read failed for {day}: {e}')
    return out


def score_day(s3, *, day: str, entities: list[dict], index: sig.EntityIndex,
              write: bool = True, cache_board: bool = True) -> dict:
    t0 = time.time()
    board = sig.load_day_board(s3_client=s3, day=day, bucket=BUCKET,
                               cache_to_s3=cache_board)
    if not board:
        return {'day': day, 'status': 'no_board', 'rows': 0}
    census = sig.load_encyclopedia_census(s3_client=s3, day=day, bucket=BUCKET)
    totals = sig.augment_totals_with_census(sig.surface_totals(board), census)
    signals = sig.compute_signal_metrics_for_day(board=board, index=index,
                                                 totals=totals, census=census)
    history = _history_from_db(day)

    rows: list[str] = []
    stats = {'day': day, 'board_items': len(board),
             'census_entities': len(census), 'entities': len(entities),
             'matched': 0, 'scored': 0, 'no_signal': 0,
             'by_category_matched': defaultdict(int),
             'by_category_total': defaultdict(int)}

    for job in entities:
        subj = job.get('profile_subject') or ''
        if not subj:
            continue
        sub = R.normalize_subcategory(job.get('category'))
        master = R.get_master_category(sub)
        stats['by_category_total'][master] += 1

        s = signals.get(subj)
        hist = history.get(subj) or []
        matched_today = bool(s)
        if matched_today:
            stats['matched'] += 1
            stats['by_category_matched'][master] += 1

        volume = float(s['volume']) if s else 0.0
        reach = int(s['reach']) if s else 0
        hist_has_signal = any((h.get('mentions') or 0) > 0 for h in hist)
        has_signal = 1 if (matched_today or hist_has_signal) else 0

        if has_signal:
            score = R.compute_cw_iq_score(
                today={'mentions': volume, 'unique_uids': reach},
                history=hist, snapshot_date=day,
                # A row is written for every entity every night, so
                # counting rows would hand a full history factor to an
                # entity that has never once been seen and let its first
                # day of signal land on 100.0. Count the days it was
                # actually there.
                damping_basis='observed')
            score_lit = f'{score}'
            stats['scored'] += 1
        else:
            # No signal today and none in the baseline. Scoring this
            # entity would mean publishing a number with nothing behind
            # it, so it reads as no signal instead.
            score = None
            score_lit = 'NULL'
            stats['no_signal'] += 1

        prev_vol = float(hist[0]['mentions']) if hist else 0.0
        prev_score = hist[0].get('cw_iq_score') if hist else None
        prev_lit = ('NULL' if prev_score is None or prev_score < 0
                    else f'{prev_score}')

        mix = json.dumps((s or {}).get('surface_mix') or {})
        tops = json.dumps((s or {}).get('top_items') or [])
        surfaces = (s or {}).get('surfaces') or []
        surf_lit = '[' + ', '.join(f"'{_esc(x)}'" for x in surfaces) + ']'

        rows.append(
            f"(toDate('{day}'),'{_esc(subj)}',"
            f"'{_esc(job.get('display_name') or job.get('project_name') or subj)}',"
            f"'{_esc(master)}','{_esc(sub)}','{_esc(job.get('s3_key') or '')}',"
            f"{volume},{reach},{(s or {}).get('matched_items', 0)},"
            f"{surf_lit},'{_esc(mix)}',{has_signal},{score_lit},"
            f"{prev_vol},{prev_lit},'{_esc(tops)}',now())"
        )

    if write and rows:
        cols = ('snapshot_date, profile_subject, project_name, category, '
                'subcategory, s3_key, signal_volume, signal_reach, '
                'matched_items, surfaces, surface_mix, has_signal, '
                'cw_iq_score, prev_volume, prev_cw_iq_score, top_items, '
                'generated_at')
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            ch(f'INSERT INTO {TABLE} ({cols}) VALUES ' + ','.join(chunk))

    stats['rows'] = len(rows)
    stats['elapsed_s'] = round(time.time() - t0, 1)
    stats['by_category_matched'] = dict(stats['by_category_matched'])
    stats['by_category_total'] = dict(stats['by_category_total'])
    stats['surface_totals'] = {k: round(v) for k, v in totals.items()}
    stats['status'] = 'ok'
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='ISO date (default: yesterday)')
    ap.add_argument('--days', type=int, default=1,
                    help='score this many days ending at --date, oldest first')
    ap.add_argument('--create-table', action='store_true')
    ap.add_argument('--dry-run', action='store_true',
                    help='compute and report, write nothing')
    args = ap.parse_args()

    if not CH_PASS:
        print('[signal_daily] CLICKHOUSE_PASSWORD not set', file=sys.stderr)
        return 2

    if args.create_table:
        ch(DDL)
        print(f'[signal_daily] {TABLE} ready')

    s3 = boto3.client('s3')
    entities = load_entities(s3)
    terms = resolve_terms(s3, entities)
    index = build_index(entities, terms)
    print(f'[signal_daily] {len(entities)} entities, '
          f'{len(index)} indexed by name')

    end = args.date or (date.today() - timedelta(days=1)).isoformat()
    days = list(reversed(sig.recent_days(end, max(1, args.days))))

    for day in days:
        st = score_day(s3, day=day, entities=entities, index=index,
                       write=not args.dry_run)
        print(json.dumps(st, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
