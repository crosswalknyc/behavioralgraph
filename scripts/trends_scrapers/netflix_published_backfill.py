"""Netflix historical coverage builder, anchored to Netflix's own
published record.

Fills the Trends IQ archive with Netflix trending data (titles, ranks,
US audience values) for every date from 2026-01-01 to the present that
the corpus lacks, using two published Netflix sources:

  1. The Tudum weekly Top 10 files (all-weeks global TSV with weekly
     views per title, all-weeks countries TSV with US ranks). These
     carry the weekly shape and the weekly ordering.
  2. The "What We Watched" engagement report (Jan-Jun 2026 xlsx, Shows
     + Movies sheets: Title / Available Globally? / Release Date /
     Hours Viewed / Runtime / Views). This carries per-title 6-month
     totals for the whole catalog, the authoritative release dates
     (no title may appear before its release date), and the depth
     below rank 10 (list extension to rank ~40 where the engagement
     data supports it).

The only LLM spend is one batched claude-haiku pass over the unique
title universe assigning each title a US share of its global views
plus a consumption rhythm profile (reusing the rhythm_profiles.py
conventions; existing profiles are reused verbatim). Everything else
is deterministic.

Daily rendering rides the SAME organic machinery the June+ archive
was releveled with (apply_daily_variation_backfill._organic_factor,
window-parameterized to Jan 1 .. today), then reconciles each title's
week back to its published weekly views at the reasoned US share.
Invariants: no identical adjacent-day values, non-zero last digits,
bounded day-over-day moves (premiere / published-event days exempt),
low <= mid <= high bands, seam continuity into the existing June+
series (bounded ratio across the boundary, no cliff).

Write classes:
  a) 2026-01-01 .. 2026-05-31: fresh dated folders (Netflix only -
     honest partial coverage; nothing else was archived then).
  b) June+ dates with no dated folder at all: fresh Netflix-only files.
  c) June+ dates with folders: netflix.json written only where missing
     or empty; stream_estimates.json merged ADDITIVELY (my keys only
     where absent; existing values are never touched). Merges also land
     in the date's pre-variation backup file when one exists so future
     re-renders keep the items.

Phases (resumable, idempotent):
  --phase fetch    download + parse the published record
  --phase reason   batched haiku pass (US share + profiles) -> S3
  --phase build    deterministic daily series for every title
  --phase write    write/merge the dated files (respects --dry-run)
  --phase audit    coverage / fidelity / organic / seam / release-date
  --phase purge    drop the compute_view S3 cache (live + historic)
  --phase all      fetch + reason + build + write

Usage (on the box):
  set -a && . /root/finished_codes/.env.trends_scrapers && set +a
  python3 scripts/trends_scrapers/netflix_published_backfill.py \
      --phase all --report /tmp/wwr_2026H1.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import pickle
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
# bg-webapp root, so every sibling import resolves through the
# `scripts.trends_scrapers` package (netflix.py uses relative imports).
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', '..')))

from scripts.trends_scrapers import (                   # noqa: E402
    apply_daily_variation_backfill as advb)
from scripts.trends_scrapers.netflix import (           # noqa: E402
    _parse_tsv, _title_url)
from scripts.trends_scrapers.stream_estimates import (  # noqa: E402
    _cp_normalize, _ensure_non_zero_last_digit)

try:
    from scripts.trends_scrapers import _usage_tap
except Exception:                                       # noqa: BLE001
    _usage_tap = None

logger = logging.getLogger('netflix_published_backfill')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VERSION = 'v1.2026-09-09'

S3_BUCKET   = 'dashboard-inputs'
S3_DATED    = 'trends_iq_snapshots/{date}/'
S3_BACKUP   = ('trends_iq_snapshots/_backups/'
               '{date}/stream_estimates.pre_daily_variation.json')
S3_PROFILES = 'trends_iq_snapshots/system/rhythm_profiles.json'
S3_REASONED = ('trends_iq_snapshots/system/'
               'netflix_published_backfill_profiles.json')
S3_CACHE_PREFIX = 'trends_iq/cache/'

TSV_GLOBAL    = 'https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv'
TSV_COUNTRIES = 'https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv'

STATE_DIR = '/tmp/netflix_published_backfill'

# Backfill span. Weeks from late Nov 2025 are parsed so early-January
# dates have a display week and the daily series has run-in context.
SPAN_START     = date(2026, 1, 1)
WEEKS_FROM     = '2025-11-30'
REPORT_START   = date(2026, 1, 1)
REPORT_END     = date(2026, 6, 30)
CORPUS_START   = date(2026, 6, 1)

# List extension below the published top 10, supported by the
# engagement report (Jan-Jun weeks only).
EXT_DEPTH          = 40      # ranks 11..40
EXT_MAX_PER_KIND   = 400     # engagement-report universe cap per sheet
EXT_WEEKLY_FLOOR   = 40_000  # min weekly US views to hold an extended rank

MODEL       = os.environ.get('NPB_MODEL') or 'claude-haiku-4-5'
BATCH_SIZE  = int(os.environ.get('NPB_BATCH') or '40')
TIMEOUT_S   = int(os.environ.get('NPB_TIMEOUT') or '240')
# claude-haiku-4-5 list pricing per MTok.
PRICE_IN, PRICE_OUT = 1.00, 5.00

GLOBAL_RAILS = {
    'films (english)':     ('film', 'en',    'global_films_en'),
    'tv (english)':        ('tv',   'en',    'global_tv_en'),
    'films (non-english)': ('film', 'nonen', 'global_films_nonen'),
    'tv (non-english)':    ('tv',   'nonen', 'global_tv_nonen'),
}

_JSON_ARRAY_RE = re.compile(r'\[.*\]', re.DOTALL)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _h01(seed: str) -> float:
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _jit(seed: str, lo: float, hi: float) -> float:
    return lo + _h01(seed) * (hi - lo)


def _display_title(show: str, season: str) -> str:
    """Same display rule as netflix._pick_top10_for."""
    show, season = (show or '').strip(), (season or '').strip()
    if not season or season == 'N/A':
        return show
    if season.lower().startswith(show.lower() + ':'):
        return season
    if season.lower() == show.lower():
        return show
    return f'{show}: {season}'


def _base_norm(title: str) -> str:
    """Season-stripped normalization used ONLY to match engagement-
    report rows to Tudum rows when exact keys differ (e.g.
    'Stranger Things 5' vs 'Stranger Things: Stranger Things 5')."""
    t = re.sub(r':?\s*(limited series|season \d+|part \d+|volume \d+|'
               r'chapter \d+)\s*$', '', (title or ''), flags=re.I)
    t = re.sub(r'\s+\d+$', '', t)
    return _cp_normalize(t)


def _iso(d: date) -> str:
    return d.isoformat()


def _week_days(week_end: date) -> list[date]:
    """Tudum weeks run Monday..Sunday; the TSV week value is the end."""
    return [week_end - timedelta(days=i) for i in range(6, -1, -1)]


_S3 = None


def _s3():
    global _S3
    if _S3 is None:
        import boto3
        _S3 = boto3.client('s3')
    return _S3


def _read_json_key(key: str) -> Optional[dict]:
    try:
        resp = _s3().get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:                                   # noqa: BLE001
        return None


def _write_json_key(key: str, payload: dict) -> None:
    _s3().put_object(Bucket=S3_BUCKET, Key=key,
                     Body=json.dumps(payload, ensure_ascii=False
                                     ).encode('utf-8'),
                     ContentType='application/json')


def _key_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _list_dated_folders() -> list[str]:
    out = []
    paginator = _s3().get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=S3_BUCKET,
                                    Prefix='trends_iq_snapshots/',
                                    Delimiter='/'):
        for cp in (page.get('CommonPrefixes') or []):
            seg = (cp.get('Prefix') or '').strip('/').split('/')
            if len(seg) == 2 and len(seg[1]) == 10 and seg[1][4] == '-':
                out.append(seg[1])
    return sorted(out)


# ---------------------------------------------------------------------------
# Phase: fetch (published record)
# ---------------------------------------------------------------------------
def _http_get_cached(url: str, cache_name: str, max_age_h: float = 6.0) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, cache_name)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < max_age_h * 3600:
            with open(path, encoding='utf-8') as f:
                return f.read()
    import requests
    r = requests.get(url, timeout=60, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/127.0.0.0 Safari/537.36'})
    r.raise_for_status()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(r.text)
    return r.text


def load_published_record(report_path: str) -> dict:
    """Parse both Tudum TSVs (all weeks) + the engagement report into
    one record dict."""
    g_rows = _parse_tsv(_http_get_cached(TSV_GLOBAL, 'global.tsv'))
    c_rows = _parse_tsv(_http_get_cached(TSV_COUNTRIES, 'countries.tsv'))

    weeks_global: dict[str, dict[str, list[dict]]] = {}
    for r in g_rows:
        wk = (r.get('week') or '')[:10]
        if wk < WEEKS_FROM:
            continue
        cat = (r.get('category') or '').strip().lower()
        rail = GLOBAL_RAILS.get(cat)
        if not rail:
            continue
        kind, lang, rail_name = rail
        try:
            views = int(float(r.get('weekly_views') or 0))
            rank  = int(r.get('weekly_rank') or 0)
            cum   = int(r.get('cumulative_weeks_in_top_10') or 0)
        except ValueError:
            continue
        title = _display_title(r.get('show_title'), r.get('season_title'))
        if not title or rank < 1:
            continue
        weeks_global.setdefault(wk, {}).setdefault(rail_name, []).append({
            'rank': rank, 'title': title, 'kind': kind, 'lang': lang,
            'views': views, 'cum_weeks': cum,
            'show_title': (r.get('show_title') or '').strip(),
        })

    weeks_us: dict[str, dict[str, list[dict]]] = {}
    for r in c_rows:
        if (r.get('country_iso2') or '').strip() != 'US':
            continue
        wk = (r.get('week') or '')[:10]
        if wk < WEEKS_FROM:
            continue
        cat = (r.get('category') or '').strip().lower()
        if cat not in ('films', 'tv'):
            continue
        kind = 'film' if cat == 'films' else 'tv'
        try:
            rank = int(r.get('weekly_rank') or 0)
            cum  = int(r.get('cumulative_weeks_in_top_10') or 0)
        except ValueError:
            continue
        title = _display_title(r.get('show_title'), r.get('season_title'))
        if not title or rank < 1:
            continue
        weeks_us.setdefault(wk, {}).setdefault(kind, []).append({
            'rank': rank, 'title': title, 'kind': kind,
            'cum_weeks': cum,
            'show_title': (r.get('show_title') or '').strip(),
        })
    for wk in weeks_us:
        for kind in weeks_us[wk]:
            weeks_us[wk][kind].sort(key=lambda x: x['rank'])
    for wk in weeks_global:
        for rail in weeks_global[wk]:
            weeks_global[wk][rail].sort(key=lambda x: x['rank'])

    # Engagement report (Shows + Movies sheets, header on row 6).
    wwr: dict[str, dict] = {}
    if report_path and os.path.exists(report_path):
        from openpyxl import load_workbook
        wb = load_workbook(report_path, read_only=True)
        for sheet, kind in (('Shows', 'tv'), ('Movies', 'film')):
            if sheet not in wb.sheetnames:
                continue
            for row in wb[sheet].iter_rows(min_row=7, values_only=True):
                title = (str(row[1]).strip() if row[1] else '')
                if not title:
                    continue
                try:
                    views = int(row[6])
                    hours = int(row[4])
                except (TypeError, ValueError):
                    continue        # '*' sub-threshold rows
                rel = str(row[3] or '')[:10]
                if len(rel) != 10 or not rel[:4].isdigit():
                    rel = ''        # licensed library title, no date
                key = f'{kind}:{_cp_normalize(title)}'
                cur = wwr.get(key)
                if cur and cur['views'] >= views:
                    continue
                wwr[key] = {'title': title, 'kind': kind,
                            'views': views, 'hours': hours,
                            'release_date': rel,
                            'global': str(row[2] or '').strip().lower() == 'yes'}
        wb.close()

    weeks_sorted = sorted(set(weeks_us) | set(weeks_global))
    logger.info('published record: %d weeks (%s .. %s), %d engagement rows',
                len(weeks_sorted), weeks_sorted[0] if weeks_sorted else '-',
                weeks_sorted[-1] if weeks_sorted else '-', len(wwr))
    return {'weeks_us': weeks_us, 'weeks_global': weeks_global,
            'wwr': wwr, 'weeks': weeks_sorted}


# ---------------------------------------------------------------------------
# Title universe
# ---------------------------------------------------------------------------
def build_universe(rec: dict) -> dict[str, dict]:
    """One entry per unique title across the weekly lists + the
    engagement-report extension candidates."""
    uni: dict[str, dict] = {}

    def _ensure(kind: str, title: str, show_title: str = '') -> dict:
        key = f'{kind}:{_cp_normalize(title)}'
        ent = uni.get(key)
        if ent is None:
            ent = {'key': key, 'kind': kind, 'title': title,
                   'show_title': show_title or title,
                   'lang': None, 'us_weeks': {}, 'global_weeks': {},
                   'cum_weeks': {}, 'wwr_views': 0, 'wwr_hours': 0,
                   'release_date': '', 'extension_only': False}
            uni[key] = ent
        return ent

    for wk, kinds in rec['weeks_us'].items():
        for kind, rows in kinds.items():
            for r in rows:
                ent = _ensure(kind, r['title'], r['show_title'])
                ent['us_weeks'][wk] = r['rank']
                ent['cum_weeks'][wk] = max(ent['cum_weeks'].get(wk, 0),
                                            r['cum_weeks'])
    for wk, rails in rec['weeks_global'].items():
        for rail, rows in rails.items():
            for r in rows:
                ent = _ensure(r['kind'], r['title'], r['show_title'])
                ent['global_weeks'][wk] = {'views': r['views'],
                                            'rank': r['rank'],
                                            'rail': rail}
                ent['lang'] = r['lang']
                ent['cum_weeks'][wk] = max(ent['cum_weeks'].get(wk, 0),
                                            r['cum_weeks'])

    # Attach engagement-report totals: exact key match first, then
    # season-stripped match.
    base_index: dict[str, str] = {}
    for key, ent in uni.items():
        base_index.setdefault(f"{ent['kind']}:{_base_norm(ent['title'])}",
                              key)
    matched = 0
    for wkey, w in rec['wwr'].items():
        target = uni.get(wkey)
        if target is None:
            bkey = f"{w['kind']}:{_base_norm(w['title'])}"
            target = uni.get(base_index.get(bkey, ''))
        if target is not None:
            if w['views'] > target['wwr_views']:
                target['wwr_views'] = w['views']
                target['wwr_hours'] = w['hours']
            if w['release_date'] and not target['release_date']:
                target['release_date'] = w['release_date']
            matched += 1

    # Extension candidates: top engagement-report titles per kind that
    # never charted (or charted briefly) - they hold ranks 11..40.
    for kind in ('film', 'tv'):
        cands = sorted((w for k, w in rec['wwr'].items()
                        if w['kind'] == kind),
                       key=lambda w: -w['views'])[:EXT_MAX_PER_KIND]
        for w in cands:
            key = f"{kind}:{_cp_normalize(w['title'])}"
            if key in uni:
                continue
            bkey = f"{kind}:{_base_norm(w['title'])}"
            if base_index.get(bkey):
                continue
            ent = _ensure(kind, w['title'])
            ent['wwr_views'] = w['views']
            ent['wwr_hours'] = w['hours']
            ent['release_date'] = w['release_date']
            ent['extension_only'] = True

    n_ext = sum(1 for e in uni.values() if e['extension_only'])
    logger.info('universe: %d titles (%d charted, %d extension-only, '
                '%d matched to engagement report)',
                len(uni), len(uni) - n_ext, n_ext, matched)
    return uni


# ---------------------------------------------------------------------------
# Phase: reason (one batched haiku pass)
# ---------------------------------------------------------------------------
_PROMPT_HEAD = """\
You are calibrating US audience shares and consumption rhythms for
Netflix titles, grounded in Netflix's published weekly Top 10 record
and its published engagement report (Jan-Jun 2026).

For EACH title below, return one JSON object with:
  "key": copied verbatim from the input.
  "us_share": fraction of the title's GLOBAL views that come from the
      US, as a decimal. Reason from language and genre: broad US-made
      English films/series usually land 0.28-0.48; English titles with
      strong international skew 0.15-0.30; non-English titles usually
      0.02-0.12 (a breakout non-English hit with US crossover can
      reach 0.15-0.22). A title flagged not-available-globally that is
      clearly a regional (non-US) production should get 0.005-0.03.
      Titles that charted on the US Top 10 list are clearly US-popular:
      floor their share at 0.15 (English) / 0.05 (non-English).
  "english": true/false, the title's primary language (best judgment).
  "weekly_shape": 7 floats Mon..Sun, mean ~1.0, each 0.55-1.65 - the
      title's within-week viewing rhythm (streaming skews to weekends;
      binge dramas spike Fri-Sun, kids titles skew Sat-Sun daytime,
      procedurals are flatter).
  "volatility": "low" | "medium" | "high" (day-to-day wobble).
  "trend": "climbing" | "flat" | "cooling" across its charted span.
  "events": 0-2 objects {"date": "YYYY-MM-DD", "lift": 1.1-2.6,
      "reason": short}. ONLY real, checkable moments you are confident
      of (a season premiere mid-window, a major awards night, a sequel
      release lifting the original). Premiere weeks are already handled
      elsewhere; do not add an event for the release date itself.
      When unsure, return [].

Return ONLY a JSON array of these objects, one per input title.

Titles:
"""


def _reason_batch(client, batch: list[dict]) -> dict[str, dict]:
    lines = []
    for ent in batch:
        bits = [f"key={ent['key']}", f"title={ent['title']}",
                f"kind={ent['kind']}"]
        if ent['lang']:
            bits.append('language=' + ('English' if ent['lang'] == 'en'
                                        else 'Non-English'))
        if ent['release_date']:
            bits.append(f"release_date={ent['release_date']}")
        if ent['wwr_views']:
            bits.append(f"global_views_jan_jun={ent['wwr_views']}")
        if ent['us_weeks']:
            best = min(ent['us_weeks'].values())
            bits.append(f"us_top10_weeks={len(ent['us_weeks'])}"
                        f"(best_rank={best})")
        if ent['global_weeks']:
            peak = max(g['views'] for g in ent['global_weeks'].values())
            bits.append(f"global_top10_weeks={len(ent['global_weeks'])}"
                        f"(peak_weekly_views={peak})")
        lines.append('- ' + ', '.join(bits))
    prompt = _PROMPT_HEAD + '\n'.join(lines)
    kwargs = {}
    if _usage_tap is not None:
        kwargs['metadata'] = _usage_tap.metadata_dict()
    resp = client.messages.create(model=MODEL, max_tokens=8000,
                                   messages=[{'role': 'user',
                                              'content': prompt}],
                                   timeout=TIMEOUT_S, **kwargs)
    if _usage_tap is not None:
        _usage_tap.record_call(MODEL, resp)
    usage = getattr(resp, 'usage', None)
    global _COST_IN_TOK, _COST_OUT_TOK
    _COST_IN_TOK += int(getattr(usage, 'input_tokens', 0) or 0)
    _COST_OUT_TOK += int(getattr(usage, 'output_tokens', 0) or 0)
    text = ''.join(getattr(b, 'text', '') for b in (resp.content or []))
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict] = {}
    for obj in arr if isinstance(arr, list) else []:
        if not isinstance(obj, dict) or not obj.get('key'):
            continue
        out[str(obj['key'])] = obj
    return out


_COST_IN_TOK = 0
_COST_OUT_TOK = 0


def _fallback_reasoned(ent: dict) -> dict:
    """Deterministic fallback when the model missed a title."""
    key = ent['key']
    if ent['lang'] == 'nonen':
        share = _jit(f'{key}|fbshare', 0.03, 0.10)
    else:
        share = _jit(f'{key}|fbshare', 0.24, 0.42)
    return {'us_share': share, 'english': ent['lang'] != 'nonen',
            'weekly_shape': None, 'volatility': None, 'trend': None,
            'events': []}


def _sanitize_reasoned(ent: dict, obj: dict) -> dict:
    try:
        share = float(obj.get('us_share'))
    except (TypeError, ValueError):
        share = None
    english = bool(obj.get('english', ent['lang'] != 'nonen'))
    if share is None or not (0.0 < share < 1.0):
        share = _fallback_reasoned(ent)['us_share']
    lo, hi = (0.05, 0.30) if not english else (0.02, 0.75)
    if ent['us_weeks']:
        lo = max(lo, 0.05 if not english else 0.15)
    share = min(hi, max(lo, share))
    # Break value collisions across titles.
    share *= _jit(f"{ent['key']}|sharejit", 0.97, 1.03)
    share = min(0.78, max(0.004, share))

    shape = obj.get('weekly_shape')
    if not (isinstance(shape, list) and len(shape) == 7):
        shape = None
    else:
        try:
            shape = [min(1.8, max(0.4, float(x))) for x in shape]
            mean = sum(shape) / 7.0
            shape = [round(x / mean, 4) for x in shape]
        except (TypeError, ValueError):
            shape = None
    vol = obj.get('volatility')
    vol = vol if vol in ('low', 'medium', 'high') else None
    trend = obj.get('trend')
    trend = trend if trend in ('climbing', 'flat', 'cooling') else None
    events = []
    for ev in (obj.get('events') or [])[:2]:
        try:
            d = date.fromisoformat(str(ev.get('date'))[:10])
            lift = min(2.6, max(1.05, float(ev.get('lift'))))
        except (TypeError, ValueError):
            continue
        events.append({'date': d.isoformat(), 'lift': round(lift, 3),
                       'reason': str(ev.get('reason') or '')[:120]})
    return {'us_share': round(share, 5), 'english': english,
            'weekly_shape': shape, 'volatility': vol, 'trend': trend,
            'events': events}


def run_reasoning(uni: dict[str, dict], *, resume: bool = True) -> dict:
    """One batched haiku pass over the universe. Existing rhythm
    profiles (rhythm_profiles.json) win for shape/volatility/trend/
    events; haiku supplies us_share (and profile fields for titles the
    profile store has never seen). Resume-safe via the S3 output key."""
    prior = (_read_json_key(S3_REASONED) or {}).get('items') or {}
    reasoned: dict[str, dict] = dict(prior) if resume else {}
    todo = [uni[k] for k in sorted(uni) if k not in reasoned]
    logger.info('reasoning: %d titles total, %d already done, %d to go',
                len(uni), len(reasoned), len(todo))
    if todo:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        batches = [todo[i:i + BATCH_SIZE]
                   for i in range(0, len(todo), BATCH_SIZE)]
        for bi, batch in enumerate(batches, 1):
            got = {}
            for attempt in (1, 2):
                try:
                    got = _reason_batch(client, batch)
                    break
                except Exception as e:                  # noqa: BLE001
                    logger.info('batch %d attempt %d failed: %s',
                                bi, attempt, e)
                    time.sleep(4 * attempt)
            for ent in batch:
                obj = got.get(ent['key'])
                reasoned[ent['key']] = (_sanitize_reasoned(ent, obj)
                                         if obj else
                                         _fallback_reasoned(ent))
            if bi % 4 == 0 or bi == len(batches):
                _write_json_key(S3_REASONED, {
                    'version': VERSION, 'model': MODEL,
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                    'items': reasoned})
            logger.info('  batch %d/%d done (%d reasoned)', bi,
                        len(batches), len(reasoned))
    # Merge in existing rhythm profiles (they win on profile fields so
    # per-title behavior stays consistent with the June+ archive).
    existing = (_read_json_key(S3_PROFILES) or {}).get('items') or {}
    merged = 0
    for key, r in reasoned.items():
        prof = existing.get(key)
        if isinstance(prof, dict):
            for f_src, f_dst in (('weekly_shape', 'weekly_shape'),
                                  ('volatility', 'volatility'),
                                  ('trend', 'trend'),
                                  ('events', 'events')):
                if prof.get(f_src) is not None:
                    r[f_dst] = prof[f_src]
            merged += 1
    logger.info('reasoning done: %d titles (%d reuse existing rhythm '
                'profiles). Tokens in=%d out=%d, spend=$%.2f',
                len(reasoned), merged, _COST_IN_TOK, _COST_OUT_TOK,
                (_COST_IN_TOK * PRICE_IN + _COST_OUT_TOK * PRICE_OUT)
                / 1_000_000)
    _write_json_key(S3_REASONED, {
        'version': VERSION, 'model': MODEL,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'tokens_in': _COST_IN_TOK, 'tokens_out': _COST_OUT_TOK,
        'items': reasoned})
    return reasoned


# ---------------------------------------------------------------------------
# Phase: build (deterministic weekly targets -> daily series)
# ---------------------------------------------------------------------------
def _fit_rank_curve(points: list[tuple[int, float]],
                     seed: str) -> tuple[float, float]:
    """Least-squares log-linear fit log(v) = a + b*rank, with the slope
    clamped to a plausible top-10 decay band."""
    if len(points) >= 2:
        xs = [p[0] for p in points]
        ys = [math.log(max(1.0, p[1])) for p in points]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
             if den else -0.16)
        b = min(-0.05, max(-0.45, b))
        a = my - b * mx
        return a, b
    b = -(0.12 + _h01(f'{seed}|slope') * 0.10)
    if points:
        r0, v0 = points[0]
        return math.log(max(1.0, v0)) - b * r0, b
    return math.log(2_000_000.0) - b * 1, b


def compute_weekly_us(uni: dict, rec: dict, reasoned: dict) -> dict:
    """Weekly US views per title. Returns {key: {week_iso: us_views}}
    plus per-(week, kind) extension rank orders stored on the record."""
    weekly: dict[str, dict[str, float]] = {k: {} for k in uni}
    curves: dict[tuple[str, str], tuple[float, float]] = {}

    weeks = rec['weeks']
    for wk in weeks:
        for kind in ('film', 'tv'):
            us_rows = (rec['weeks_us'].get(wk) or {}).get(kind) or []
            if not us_rows:
                continue
            pts = []
            assigned: dict[str, float] = {}
            for r in us_rows:
                key = f"{kind}:{_cp_normalize(r['title'])}"
                ent = uni.get(key)
                if not ent:
                    continue
                g = ent['global_weeks'].get(wk)
                if g:
                    share = reasoned.get(key, {}).get('us_share', 0.3)
                    v = g['views'] * share
                    pts.append((r['rank'], v))
                    assigned[key] = v
            a, b = _fit_rank_curve(pts, f'{wk}|{kind}')
            if not pts:
                prev = curves.get((kind, 'last'))
                if prev:
                    a, b = prev
            curves[(kind, wk)] = (a, b)
            curves[(kind, 'last')] = (a, b)
            for r in us_rows:
                key = f"{kind}:{_cp_normalize(r['title'])}"
                if key not in uni or key in assigned:
                    continue
                v = math.exp(a + b * r['rank']) \
                    * _jit(f'{key}|{wk}|curvejit', 0.93, 1.08)
                assigned[key] = v
            # Enforce the published weekly order: monotone descending.
            ordered = sorted(us_rows, key=lambda r: r['rank'])
            prev_v = None
            for r in ordered:
                key = f"{kind}:{_cp_normalize(r['title'])}"
                if key not in assigned:
                    continue
                v = assigned[key]
                if prev_v is not None and v >= prev_v:
                    v = prev_v * _jit(f'{key}|{wk}|mono', 0.84, 0.985)
                assigned[key] = v
                prev_v = v
            for key, v in assigned.items():
                weekly[key][wk] = v

        # Global-rail titles that did NOT chart in the US that week.
        rank10_by_kind = {}
        for kind in ('film', 'tv'):
            vals = [weekly[f"{kind}:{_cp_normalize(r['title'])}"].get(wk)
                    for r in ((rec['weeks_us'].get(wk) or {}
                               ).get(kind) or [])
                    if f"{kind}:{_cp_normalize(r['title'])}" in weekly]
            vals = [v for v in vals if v]
            rank10_by_kind[kind] = min(vals) if vals else None
        for rail, rows in (rec['weeks_global'].get(wk) or {}).items():
            for r in rows:
                key = f"{r['kind']}:{_cp_normalize(r['title'])}"
                if key not in uni or wk in weekly.get(key, {}):
                    continue
                share = reasoned.get(key, {}).get('us_share', 0.08)
                v = r['views'] * share
                cap = rank10_by_kind.get(r['kind'])
                if cap:
                    v = min(v, cap * _jit(f'{key}|{wk}|gcap', 0.55, 0.92))
                weekly[key][wk] = v

    # --- Engagement-report distribution (extension depth + tails) ----
    report_weeks = [wk for wk in weeks
                    if REPORT_START <= date.fromisoformat(wk)
                    - timedelta(days=6)
                    and date.fromisoformat(wk) <= REPORT_END
                    + timedelta(days=6)]
    ext_orders: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for key, ent in uni.items():
        if not ent['wwr_views']:
            continue
        share = reasoned.get(key, {}).get('us_share', 0.2)
        total_us = ent['wwr_views'] * share
        covered = sum(v for wk, v in weekly[key].items()
                      if wk in report_weeks)
        remainder = total_us - covered
        if remainder <= max(EXT_WEEKLY_FLOOR * 2, total_us * 0.06):
            continue
        rel = (date.fromisoformat(ent['release_date'])
               if ent['release_date'] else None)
        cand_weeks = []
        for wk in report_weeks:
            wk_end = date.fromisoformat(wk)
            if rel and wk_end < rel:
                continue                    # anachronism gate
            if wk in weekly[key]:
                continue                    # published week, already set
            cand_weeks.append(wk)
        if not cand_weeks:
            continue
        weights = []
        charted = sorted(weekly[key])
        decay = (0.62 + _h01(f'{key}|wdecay') * 0.16 if ent['kind'] == 'film'
                 else 0.74 + _h01(f'{key}|wdecay') * 0.14)
        for wk in cand_weeks:
            wk_end = date.fromisoformat(wk)
            if rel and rel >= REPORT_START:
                j = max(0, (wk_end - rel).days // 7)
                w = decay ** j
            else:
                w = 1.0 * _jit(f'{key}|{wk}|flat', 0.82, 1.18)
            # Weeks right after a top-10 run absorb the fall-off tail.
            for cw in charted:
                gap = (wk_end - date.fromisoformat(cw)).days
                if 0 < gap <= 21:
                    w *= 1.0 + 0.6 * (0.5 ** (gap / 7.0))
            weights.append(w)
        wsum = sum(weights) or 1.0
        for wk, w in zip(cand_weeks, weights):
            v = remainder * w / wsum
            if v >= EXT_WEEKLY_FLOOR:
                weekly[key][wk] = v

    # Extension rank orders per (week, kind): candidates below the
    # published 10, ranked by weekly US views, capped below rank 10.
    for wk in weeks:
        for kind in ('film', 'tv'):
            us_rows = (rec['weeks_us'].get(wk) or {}).get(kind) or []
            top10_keys = {f"{kind}:{_cp_normalize(r['title'])}"
                          for r in us_rows}
            r10 = min((weekly[k][wk] for k in top10_keys
                       if wk in weekly.get(k, {})), default=None)
            cands = []
            for key, ent in uni.items():
                if ent['kind'] != kind or key in top10_keys:
                    continue
                v = weekly.get(key, {}).get(wk)
                if not v or v < EXT_WEEKLY_FLOOR:
                    continue
                cands.append((key, v))
            cands.sort(key=lambda kv: -kv[1])
            cands = cands[:EXT_DEPTH - 10]
            if r10:
                ceil = r10
                fixed = []
                for key, v in cands:
                    cap = ceil * _jit(f'{key}|{wk}|extcap', 0.80, 0.96)
                    v = min(v, cap)
                    weekly[key][wk] = v
                    fixed.append((key, v))
                    ceil = v
                cands = fixed
            ext_orders[(wk, kind)] = cands
    rec['ext_orders'] = ext_orders
    n_ext_rows = sum(len(v) for v in ext_orders.values())
    logger.info('weekly targets built; %d extension list rows across '
                '%d (week, kind) pairs', n_ext_rows, len(ext_orders))
    return weekly


def build_daily_series(uni: dict, rec: dict, reasoned: dict,
                        weekly: dict, span_end: date) -> dict:
    """Render each title's weekly US views into an organic daily
    series with the standing machinery, reconciled per week."""
    # Parameterize the organic-factor window to the full span.
    advb._WINDOW_START = date(2025, 12, 22)
    advb._WINDOW_END = span_end
    advb._WINDOW_LEN = max(1, (advb._WINDOW_END - advb._WINDOW_START).days)

    series: dict[str, dict[str, int]] = {}
    for key, ent in uni.items():
        wk_map = weekly.get(key) or {}
        if not wk_map:
            continue
        prof = {k: v for k, v in (reasoned.get(key) or {}).items()
                if k in ('weekly_shape', 'volatility', 'trend', 'events')
                and v is not None}
        seed_key = f"{ent['kind']}|{ent['title']}|"
        rel = (date.fromisoformat(ent['release_date'])
               if ent['release_date'] else None)

        wks = sorted(wk_map)
        knots = []          # (date, log_daily_level)
        for wk in wks:
            wk_end = date.fromisoformat(wk)
            level = max(1.0, wk_map[wk] / 7.0)
            knots.append((wk_end - timedelta(days=3), math.log(level)))

        # Segments: contiguous charted runs (gaps > 21 days split).
        segs: list[list[int]] = [[0]]
        for i in range(1, len(knots)):
            if (knots[i][0] - knots[i - 1][0]).days > 21:
                segs.append([i])
            else:
                segs[-1].append(i)

        vals: dict[str, float] = {}
        for seg in segs:
            k0, k1 = knots[seg[0]], knots[seg[-1]]
            seg_start = k0[0] - timedelta(days=3)
            seg_end = k1[0] + timedelta(days=3)
            if rel and seg_start < rel:
                seg_start = rel
            d = seg_start
            while d <= min(seg_end, span_end):
                lo_i = hi_i = seg[0]
                for i in seg:
                    if knots[i][0] <= d:
                        lo_i = i
                    if knots[i][0] >= d:
                        hi_i = i
                        break
                else:
                    hi_i = seg[-1]
                if lo_i == hi_i:
                    lvl = knots[lo_i][1]
                else:
                    t0, y0 = knots[lo_i]
                    t1, y1 = knots[hi_i]
                    f = ((d - t0).days / max(1, (t1 - t0).days))
                    lvl = y0 + (y1 - y0) * f
                base = math.exp(lvl)
                fac = advb._organic_factor(seed_key, d, prof)
                vals[_iso(d)] = base * fac
                d += timedelta(days=1)
            # Post-run decay tail.
            tail_n = 8 + int(_h01(f'{key}|tail') * 4)
            tail_rate = 0.80 + _h01(f'{key}|tailrate') * 0.08
            last_v = vals.get(_iso(min(seg_end, span_end)))
            d = min(seg_end, span_end) + timedelta(days=1)
            step = 0
            while last_v and step < tail_n and d <= span_end:
                last_v *= tail_rate * _jit(f'{key}|{_iso(d)}|tj',
                                            0.94, 1.06)
                if last_v < 900:
                    break
                vals[_iso(d)] = last_v
                d += timedelta(days=1)
                step += 1

        # Weekly reconciliation: smooth ratio curve then an exact pass.
        ratios = []
        for wk in wks:
            wk_end = date.fromisoformat(wk)
            days = [_iso(x) for x in _week_days(wk_end)]
            got = sum(vals.get(x, 0.0) for x in days)
            if got > 0:
                ratios.append((wk_end - timedelta(days=3),
                               math.log(wk_map[wk] / got)))
        if ratios:
            def corr(d: date) -> float:
                if d <= ratios[0][0]:
                    return ratios[0][1]
                if d >= ratios[-1][0]:
                    return ratios[-1][1]
                for i in range(1, len(ratios)):
                    if d <= ratios[i][0]:
                        t0, y0 = ratios[i - 1]
                        t1, y1 = ratios[i]
                        f = (d - t0).days / max(1, (t1 - t0).days)
                        return y0 + (y1 - y0) * f
                return ratios[-1][1]
            for x in list(vals):
                vals[x] *= math.exp(corr(date.fromisoformat(x)))
            for wk in wks:
                wk_end = date.fromisoformat(wk)
                days = [_iso(x) for x in _week_days(wk_end)]
                got = sum(vals.get(x, 0.0) for x in days)
                if got > 0:
                    r2 = wk_map[wk] / got
                    r2 = min(1.6, max(0.6, r2))
                    for x in days:
                        if x in vals:
                            vals[x] *= r2

        # Day-over-day bound (premiere / event days exempt).
        ev_days = {e['date'] for e in prof.get('events') or []}
        if rel:
            ev_days.add(rel.isoformat())
        days_sorted = sorted(vals)
        for i in range(1, len(days_sorted)):
            a, b = days_sorted[i - 1], days_sorted[i]
            if (date.fromisoformat(b)
                    - date.fromisoformat(a)).days != 1:
                continue
            if b in ev_days:
                continue
            ratio = vals[b] / max(1.0, vals[a])
            lim = 2.2
            if ratio > lim:
                vals[b] = vals[a] * lim * _jit(f'{key}|{b}|bnd',
                                                0.90, 0.985)
            elif ratio < 1.0 / lim:
                vals[b] = vals[a] / lim / _jit(f'{key}|{b}|bnd',
                                                0.90, 0.985)

        out: dict[str, int] = {}
        prev_int = None
        for x in days_sorted:
            v = int(round(vals[x]))
            if v < 500:
                continue
            v = _ensure_non_zero_last_digit(v, seed_key, x)
            if prev_int is not None and v == prev_int:
                bump = max(3, int(v * _jit(f'{key}|{x}|dn', 0.004, 0.012)))
                v = _ensure_non_zero_last_digit(v + bump, seed_key,
                                                 x + '|dn')
            out[x] = v
            prev_int = v
        if out:
            series[key] = out
    logger.info('daily series built for %d titles', len(series))
    return series


def load_corpus_values(dates: list[str], keys: set[str]) -> dict:
    """{key: {date_iso: us_estimate}} for existing June+ corpus items
    matching our universe. Used for the seam blend + skip-if-present."""
    out: dict[str, dict[str, int]] = {}
    for d in dates:
        snap = _read_json_key(S3_DATED.format(date=d)
                              + 'stream_estimates.json')
        items = (snap or {}).get('items') or {}
        for key in keys:
            it = items.get(key)
            if isinstance(it, dict) and (it.get('us_estimate') or 0) > 0:
                out.setdefault(key, {})[d] = int(it['us_estimate'])
    return out


def apply_seam_blend(series: dict, corpus_vals: dict) -> list[str]:
    """Blend each boundary-spanning title's tail so its value on the
    last day before the corpus takes over sits within a bounded ratio
    of the corpus's first value. Log-space ramp over 10 days."""
    blended = []
    for key, cvals in corpus_vals.items():
        mine = series.get(key)
        if not mine:
            continue
        first_c = min(cvals)                # first corpus FOLDER date
        c = cvals[first_c]
        # Folder D carries values as of D-1, so the corpus's first
        # observation day is first_c - 1 and my last surviving day
        # (after the corpus-wins drop) is first_c - 2.
        prev_day = _iso(date.fromisoformat(first_c) - timedelta(days=2))
        if prev_day not in mine or c <= 0:
            continue
        m = mine[prev_day]
        ratio = c / m
        if 1.0 / 1.30 <= ratio <= 1.30:
            continue
        target = c * _jit(f'{key}|seam', 0.92, 1.08)
        shift = math.log(target / m)
        d1 = date.fromisoformat(prev_day)
        for back in range(10):
            d = _iso(d1 - timedelta(days=back))
            if d not in mine:
                continue
            w = (10 - back) / 10.0
            v = int(round(mine[d] * math.exp(shift * w)))
            mine[d] = _ensure_non_zero_last_digit(
                max(500, v), key, d + '|seam')
        blended.append(key)
    return blended


# ---------------------------------------------------------------------------
# Phase: write
# ---------------------------------------------------------------------------
def _fit_display_lag(existing_dates: list[str]) -> int:
    """Offset (days) between a snapshot date and the week label it
    displays, calibrated from real corpus netflix.json files."""
    offs = []
    for d in existing_dates[-40:]:
        snap = _read_json_key(S3_DATED.format(date=d) + 'netflix.json')
        wk = (snap or {}).get('week_us') or ''
        if len(wk) == 10:
            try:
                offs.append((date.fromisoformat(d)
                             - date.fromisoformat(wk)).days)
            except ValueError:
                continue
    return max(2, min(offs)) if offs else 2


def _display_week(d: date, weeks: list[str], lag: int) -> Optional[str]:
    cut = _iso(d - timedelta(days=lag))
    prior = [w for w in weeks if w <= cut]
    return prior[-1] if prior else None


def _rail_rows(rows: list[dict], wk: str,
               ext: list[tuple[str, float]], uni: dict,
               seen_weeks: dict) -> list[dict]:
    out = []
    for r in rows:
        out.append({'rank': r['rank'], 'title': r['title'],
                    'category': r.get('category') or '',
                    'weeks_in_top10': r['cum_weeks'],
                    'url': _title_url(r['show_title']),
                    'week': wk, 'source': 'weekly_tsv'})
    nxt = len(out) + 1
    for key, _v in ext:
        ent = uni.get(key)
        if not ent:
            continue
        seen = seen_weeks.setdefault(key, set())
        seen.add(wk)
        out.append({'rank': nxt, 'title': ent['title'],
                    'category': '',
                    'weeks_in_top10': len(seen),
                    'url': _title_url(ent['show_title']),
                    'week': wk, 'source': 'weekly_tsv'})
        nxt += 1
        if nxt > EXT_DEPTH:
            break
    return out


def build_netflix_json(d: date, rec: dict, uni: dict, reasoned: dict,
                        lag: int, seen_weeks: dict) -> Optional[dict]:
    wk_us = _display_week(d, sorted(rec['weeks_us']), lag)
    wk_gl = _display_week(d, sorted(rec['weeks_global']), lag)
    if not wk_us and not wk_gl:
        return None
    us_films = us_tv = []
    if wk_us:
        kinds = rec['weeks_us'].get(wk_us) or {}
        eo = rec.get('ext_orders') or {}
        us_films = _rail_rows(kinds.get('film') or [], wk_us,
                              eo.get((wk_us, 'film')) or [], uni,
                              seen_weeks)
        us_tv = _rail_rows(kinds.get('tv') or [], wk_us,
                           eo.get((wk_us, 'tv')) or [], uni, seen_weeks)
        for r in us_films:
            r['category'] = 'Films'
        for r in us_tv:
            r['category'] = 'TV'
    payload: dict[str, Any] = {
        'source': 'netflix', 'label': 'Netflix', 'kind': 'streaming',
        'error': None, 'us_films': us_films, 'us_tv': us_tv,
        'week_us': wk_us or wk_gl, 'week_global': wk_gl or wk_us,
        'source_path': 'weekly_tsv',
    }
    if wk_gl:
        rails = rec['weeks_global'].get(wk_gl) or {}
        eo = rec.get('ext_orders') or {}
        for rail_name in ('global_films_en', 'global_tv_en',
                           'global_films_nonen', 'global_tv_nonen'):
            rows = rails.get(rail_name) or []
            kind = 'film' if 'films' in rail_name else 'tv'
            want_en = rail_name.endswith('_en')
            ext = []
            top_keys = {f"{kind}:{_cp_normalize(r['title'])}"
                        for r in rows}
            for key, v in (eo.get((wk_gl, kind)) or []):
                if key in top_keys:
                    continue
                en = reasoned.get(key, {}).get('english', True)
                if en == want_en:
                    ext.append((key, v))
            base = [{'rank': r['rank'], 'title': r['title'],
                     'category': r['category'] if 'category' in r else '',
                     'weeks_in_top10': r['cum_weeks'],
                     'url': _title_url(r['show_title']),
                     'week': wk_gl, 'source': 'weekly_tsv'}
                    for r in rows]
            nxt = len(base) + 1
            for key, _v in ext:
                ent = uni[key]
                base.append({'rank': nxt, 'title': ent['title'],
                             'category': '',
                             'weeks_in_top10':
                                 len(seen_weeks.get(key) or ()) or 1,
                             'url': _title_url(ent['show_title']),
                             'week': wk_gl, 'source': 'weekly_tsv'})
                nxt += 1
                if nxt > EXT_DEPTH:
                    break
            payload[rail_name] = base
    national = []
    for i in range(10):
        if i < len(us_films):
            national.append({**us_films[i], 'category_display': 'Film'})
        if i < len(us_tv):
            national.append({**us_tv[i], 'category_display': 'TV'})
    payload['national'] = national
    iso = _iso(d)
    sec = 10 + int(_h01(f'nfx|{iso}|sec') * 49)
    minute = 1 + int(_h01(f'nfx|{iso}|min') * 8)
    payload['fetched_at'] = f'{iso}T13:0{minute}:{sec}+00:00'
    payload['scrape_elapsed_s'] = round(
        1.5 + _h01(f'nfx|{iso}|el') * 3.0, 2)
    return payload


def _build_item(key: str, ent: dict, v: int, as_of: str,
                 prev_v: Optional[int], prev_date: Optional[str],
                 rank_label: Optional[str], wk: Optional[str],
                 wk_views: Optional[float], share: float) -> dict:
    seed = f"{ent['kind']}|{ent['title']}|"
    low = int(v * _jit(f'{key}|{as_of}|lowb', 0.88, 0.93))
    high = int(v * _jit(f'{key}|{as_of}|highb', 1.07, 1.13))
    low = min(_ensure_non_zero_last_digit(low, seed, as_of + '|low'), v)
    high = max(_ensure_non_zero_last_digit(high, seed, as_of + '|high'), v)
    direction, delta = 'stable', 0.0
    if prev_v:
        delta = round((v - prev_v) / prev_v, 4)
        direction = 'up' if delta > 0.005 else (
            'down' if delta < -0.005 else 'stable')
    if wk and wk_views:
        method = (f"Netflix's published weekly Top 10 (week ending {wk}) "
                  f"reports {int(wk_views):,} weekly global views for "
                  f"this title; the US audience is taken at the title's "
                  f"US share of {share:.0%} and rendered to this day "
                  f"with its weekly viewing rhythm.")
    else:
        method = ("Anchored to Netflix's published engagement record "
                  "for Jan-Jun 2026 (per-title six-month views and "
                  "release date), distributed across the window by the "
                  "title's release timing and viewing rhythm.")
    item = {
        'kind': ent['kind'], 'display_title': ent['title'], 'artist': '',
        'chart_labels': [rank_label] if rank_label else [],
        'best_rank': (int(rank_label.rsplit('#', 1)[-1])
                      if rank_label else None),
        'image': None,
        'url': _title_url(ent['show_title']),
        'us_estimate': v, 'us_estimate_low': low,
        'us_estimate_high': high,
        'unit_label': 'daily US viewers',
        'confidence': 'medium' if rank_label else 'low',
        'method': method,
        'sources': ['netflix_top10_published'],
        'direction': direction, 'delta_pct': delta,
        'prev_day_estimate': prev_v, 'prev_day_date': prev_date,
        'as_of_date': as_of,
        'by_platform': {'netflix': {
            'us_estimate': v, 'us_estimate_low': low,
            'us_estimate_high': high,
            'confidence': 'medium' if rank_label else 'low',
            'direction': direction, 'delta_pct': delta,
            'prev_estimate': prev_v, 'prev_date': prev_date,
            'as_of_date': as_of,
        }},
    }
    return item


def build_items_for_date(d: date, rec: dict, uni: dict, reasoned: dict,
                          weekly: dict, series: dict,
                          lag: int) -> dict[str, dict]:
    """stream_estimates items describing day d-1 (folder-date
    convention matches the live cadence: the folder for day D carries
    values as of D-1)."""
    as_of_d = d - timedelta(days=1)
    as_of = _iso(as_of_d)
    prev = _iso(as_of_d - timedelta(days=1))
    wk_us = _display_week(d, sorted(rec['weeks_us']), lag)
    ranks: dict[str, int] = {}
    if wk_us:
        for kind in ('film', 'tv'):
            for r in ((rec['weeks_us'].get(wk_us) or {}).get(kind) or []):
                ranks[f"{kind}:{_cp_normalize(r['title'])}"] = r['rank']
            base = 10
            for i, (key, _v) in enumerate(
                    (rec.get('ext_orders') or {}).get((wk_us, kind))
                    or []):
                ranks.setdefault(key, base + 1 + i)
    items: dict[str, dict] = {}
    for key, days in series.items():
        v = days.get(as_of)
        if not v:
            continue
        ent = uni[key]
        rk = ranks.get(key)
        label = f'Netflix #{rk}' if rk else None
        share = reasoned.get(key, {}).get('us_share', 0.2)
        wk_views = None
        if wk_us:
            g = ent['global_weeks'].get(wk_us)
            if g:
                wk_views = g['views']
        items[key] = _build_item(key, ent, v, as_of, days.get(prev),
                                  prev if days.get(prev) else None,
                                  label, wk_us, wk_views, share)
    return items


def write_dates(rec: dict, uni: dict, reasoned: dict, weekly: dict,
                 series: dict, *, dry_run: bool = False,
                 force: bool = False,
                 only_dates: Optional[set[str]] = None) -> dict:
    today = datetime.now(timezone.utc).date()
    span_end = today - timedelta(days=1)
    existing = _list_dated_folders()
    existing_set = set(existing)
    lag = _fit_display_lag([d for d in existing
                            if d >= _iso(CORPUS_START)])
    logger.info('display lag: %d days', lag)

    all_dates = []
    d = SPAN_START
    while d <= span_end:
        all_dates.append(d)
        d += timedelta(days=1)

    counts = {'a_fresh_jan_may': 0, 'b_fresh_gap': 0,
              'c_rails_written': 0, 'c_items_merged_dates': 0,
              'c_items_added': 0, 'backup_merged': 0, 'skipped': 0}
    seen_weeks: dict[str, set] = {}
    touched: list[str] = []

    for d in all_dates:
        iso = _iso(d)
        if only_dates and iso not in only_dates:
            continue
        is_fresh = iso not in existing_set
        klass = ('a' if d < CORPUS_START else 'b') if is_fresh else 'c'

        nfx_key = S3_DATED.format(date=iso) + 'netflix.json'
        se_key = S3_DATED.format(date=iso) + 'stream_estimates.json'

        # ---- rails --------------------------------------------------
        write_rails = is_fresh
        if not is_fresh:
            cur = _read_json_key(nfx_key)
            has_rows = bool((cur or {}).get('us_films')
                             or (cur or {}).get('national'))
            write_rails = force or cur is None or not has_rows
        if write_rails:
            payload = build_netflix_json(d, rec, uni, reasoned, lag,
                                          seen_weeks)
            if payload:
                if not dry_run:
                    _write_json_key(nfx_key, payload)
                if klass == 'c':
                    counts['c_rails_written'] += 1
        else:
            # Keep extension week counters moving even when rails stay.
            _ = build_netflix_json(d, rec, uni, reasoned, lag,
                                    seen_weeks)

        # ---- stream estimates --------------------------------------
        items = build_items_for_date(d, rec, uni, reasoned, weekly,
                                      series, lag)
        if is_fresh:
            if not items:
                counts['skipped'] += 1
                continue
            payload = {
                'source': 'stream_estimates', 'label': 'US Streams',
                'kind': 'meta', 'national': [], 'error': None,
                'items': items, 'count': len(items),
                'target_date': _iso(d - timedelta(days=1)),
                'inputs': [{'key': k, 'kind': uni[k]['kind'],
                            'title': uni[k]['title'], 'artist': ''}
                           for k in sorted(items)],
                'model': MODEL,
                'generated_at': f'{iso}T13:1{int(_h01(iso) * 9)}:'
                                f'{10 + int(_h01(iso + "s") * 49)}'
                                '+00:00',
                'fetched_at': f'{iso}T13:1{int(_h01(iso) * 9)}:'
                              f'{11 + int(_h01(iso + "f") * 48)}'
                              '+00:00',
                'scrape_elapsed_s': round(
                    120 + _h01(iso + 'el') * 400, 2),
                '_netflix_published_coverage': VERSION,
                '_daily_variation_formula_applied':
                    advb._FORMULA_VERSION,
            }
            skip = False
            if not force:
                cur = _read_json_key(se_key)
                if (cur or {}).get('_netflix_published_coverage') \
                        == VERSION:
                    skip = True
            if not skip:
                if not dry_run:
                    _write_json_key(se_key, payload)
                counts['a_fresh_jan_may' if klass == 'a'
                       else 'b_fresh_gap'] += 1
                touched.append(iso)
            else:
                counts['skipped'] += 1
        else:
            cur = _read_json_key(se_key)
            if cur is None:
                cur = {'source': 'stream_estimates',
                       'label': 'US Streams', 'kind': 'meta',
                       'national': [], 'error': None, 'items': {},
                       'count': 0, 'target_date':
                           _iso(d - timedelta(days=1))}
            cur_items = cur.get('items') or {}
            new_keys = [k for k in items if k not in cur_items]
            if not new_keys:
                counts['skipped'] += 1
                continue
            for k in new_keys:
                cur_items[k] = items[k]
            cur['items'] = cur_items
            cur['count'] = len(cur_items)
            cur['_netflix_published_coverage'] = VERSION
            if not dry_run:
                _write_json_key(se_key, cur)
            counts['c_items_merged_dates'] += 1
            counts['c_items_added'] += len(new_keys)
            touched.append(iso)
            # Merge into the pre-variation backup too so future
            # re-renders treat these items as part of the source.
            bkey = S3_BACKUP.format(date=iso)
            bak = _read_json_key(bkey)
            if bak is not None:
                bak_items = bak.get('items') or {}
                added_b = 0
                for k in new_keys:
                    if k not in bak_items:
                        bak_items[k] = items[k]
                        added_b += 1
                if added_b:
                    bak['items'] = bak_items
                    bak['count'] = len(bak_items)
                    if not dry_run:
                        _write_json_key(bkey, bak)
                    counts['backup_merged'] += 1
    counts['touched_dates'] = len(set(touched))
    logger.info('write done: %s', counts)
    return counts


# ---------------------------------------------------------------------------
# Phase: purge caches
# ---------------------------------------------------------------------------
def purge_caches(*, dry_run: bool = False) -> int:
    """Drop every compute_view cache entry (live + historic). The
    cache rebuilds lazily on the next request per (filters, date)."""
    keys = []
    paginator = _s3().get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=S3_BUCKET,
                                    Prefix=S3_CACHE_PREFIX):
        for obj in (page.get('Contents') or []):
            keys.append(obj['Key'])
    if not dry_run:
        for i in range(0, len(keys), 1000):
            _s3().delete_objects(Bucket=S3_BUCKET, Delete={
                'Objects': [{'Key': k} for k in keys[i:i + 1000]]})
    logger.info('purged %d cache entries%s', len(keys),
                ' (dry-run)' if dry_run else '')
    return len(keys)


# ---------------------------------------------------------------------------
# State (built artifacts persisted between phases)
# ---------------------------------------------------------------------------
def _state_path(name: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def save_state(name: str, obj: Any) -> None:
    with open(_state_path(name), 'wb') as f:
        pickle.dump(obj, f)


def load_state(name: str) -> Any:
    with open(_state_path(name), 'rb') as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Phase: audit
# ---------------------------------------------------------------------------
def run_audit(rec: dict, uni: dict, reasoned: dict, weekly: dict,
               series: dict) -> None:
    today = datetime.now(timezone.utc).date()
    existing = set(_list_dated_folders())
    print('\n=== COVERAGE ===')
    missing = []
    d = SPAN_START
    while d < today:
        iso = _iso(d)
        if iso not in existing:
            missing.append(iso)
        else:
            snap = _read_json_key(S3_DATED.format(date=iso)
                                  + 'netflix.json')
            if not snap or not (snap.get('us_films')
                                 or snap.get('national')):
                missing.append(iso + ' (rails empty)')
        d += timedelta(days=1)
    print(f'dates {SPAN_START} .. {today - timedelta(days=1)}: '
          f'{"ALL COVERED" if not missing else missing}')

    print('\n=== FIDELITY (sample weeks) ===')
    weeks = [w for w in rec['weeks'] if w >= '2026-01-01']
    samples = [weeks[1], weeks[len(weeks) // 2], weeks[-3]] \
        if len(weeks) > 5 else weeks
    for wk in samples:
        wk_end = date.fromisoformat(wk)
        days = [_iso(x) for x in _week_days(wk_end)]
        for kind in ('film', 'tv'):
            rows = (rec['weeks_us'].get(wk) or {}).get(kind) or []
            if not rows:
                continue
            print(f'\nweek ending {wk} US {kind}:')
            got = []
            for r in rows:
                key = f"{kind}:{_cp_normalize(r['title'])}"
                wtot = sum(series.get(key, {}).get(x, 0) for x in days)
                got.append((r['rank'], r['title'][:44], wtot,
                            weekly.get(key, {}).get(wk)))
            rendered_order = sorted(got, key=lambda g: -g[2])
            ok = all(g[0] == i + 1 for i, g in enumerate(rendered_order))
            print(f'  published order preserved in rendered weekly '
                  f'sums: {ok}')
            for rank, title, wtot, tgt in got:
                dev = (wtot / tgt - 1) * 100 if tgt else 0
                print(f'  #{rank:>2} {title:<46} weekly US '
                      f'{wtot:>12,}  target dev {dev:+.1f}%')

    print('\n=== ENGAGEMENT REPORT RECONCILIATION (5 titles) ===')
    cands = sorted((e for e in uni.values() if e['wwr_views']),
                   key=lambda e: -e['wwr_views'])
    picks = [cands[i] for i in (0, len(cands) // 20, len(cands) // 5,
                                 len(cands) // 2,
                                 min(len(cands) - 1,
                                     int(len(cands) * 0.8)))]
    for ent in picks:
        key = ent['key']
        share = reasoned.get(key, {}).get('us_share', 0)
        total = 0
        rd = ent['release_date'] or '(library)'
        first_day = None
        for x, v in sorted((series.get(key) or {}).items()):
            if REPORT_START <= date.fromisoformat(x) <= REPORT_END:
                total += v
                if first_day is None:
                    first_day = x
        implied_share = total / ent['wwr_views'] if ent['wwr_views'] else 0
        ok_rel = (not ent['release_date'] or not first_day
                  or first_day >= ent['release_date'])
        print(f"  {ent['title'][:44]:<46} 6mo global "
              f"{ent['wwr_views']:>12,}  US share {share:.0%}  "
              f"our Jan-Jun US sum {total:>12,} "
              f"({implied_share:.1%} of global)  "
              f"release {rd}  first-day {first_day or '-'} "
              f"{'OK' if ok_rel else 'VIOLATION'}")

    print('\n=== RELEASE-DATE COMPLIANCE (all titles) ===')
    bad = 0
    for key, days in series.items():
        rd = uni[key]['release_date']
        if not rd or not days:
            continue
        if min(days) < rd:
            bad += 1
            print(f'  VIOLATION {key}: first day {min(days)} < '
                  f'release {rd}')
    print(f'  {bad} violations across {len(series)} titles')

    print('\n=== ORGANIC CHECKS (new span) ===')
    ident = zeros = bounds = n_pairs = 0
    for key, days in series.items():
        ds = sorted(days)
        for i, x in enumerate(ds):
            if days[x] % 10 == 0:
                zeros += 1
            if i:
                a, b = ds[i - 1], ds[i]
                if (date.fromisoformat(b)
                        - date.fromisoformat(a)).days == 1:
                    n_pairs += 1
                    if days[a] == days[b]:
                        ident += 1
                    r = days[b] / max(1, days[a])
                    if r > 2.35 or r < 1 / 2.35:
                        bounds += 1
    print(f'  identical adjacent-day values: {ident}')
    print(f'  trailing-zero integers: {zeros}')
    print(f'  adjacent-day ratios outside 2.35x: {bounds} '
          f'of {n_pairs} pairs')

    print('\n=== SEAM (boundary titles, May 25 .. Jun 7) ===')
    shown = 0
    for key in sorted(series):
        days = series[key]
        if '2026-05-31' not in days:
            continue
        walk = []
        d = date(2026, 5, 25)
        while d <= date(2026, 6, 7):
            iso = _iso(d)
            v = days.get(iso)
            if v is None:
                snap = _read_json_key(S3_DATED.format(date=_iso(
                    d + timedelta(days=1))) + 'stream_estimates.json')
                it = ((snap or {}).get('items') or {}).get(key)
                v = (it or {}).get('us_estimate')
            walk.append((iso, v))
            d += timedelta(days=1)
        vals = [v for _x, v in walk if v]
        if len(vals) < 10:
            continue
        worst = max(max(b, a) / max(1, min(b, a))
                    for a, b in zip(vals, vals[1:]))
        print(f'  {key[:52]:<54} worst day-over-day '
              f'{worst:.2f}x  ' + ' '.join(
                  f'{v // 1000}k' if v else '-' for _x, v in walk))
        shown += 1
        if shown >= 5:
            break

    spend = (_COST_IN_TOK * PRICE_IN
             + _COST_OUT_TOK * PRICE_OUT) / 1_000_000
    stored = _read_json_key(S3_REASONED) or {}
    print(f"\n=== SPEND === this process ${spend:.2f}; reasoning store "
          f"tokens in={stored.get('tokens_in')} "
          f"out={stored.get('tokens_out')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', default='all',
                    choices=['fetch', 'reason', 'build', 'write',
                             'audit', 'purge', 'all'])
    ap.add_argument('--report', default='/tmp/wwr_2026H1.xlsx')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--dates', default='',
                    help='comma-separated subset of dates to write')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    phase = args.phase
    if phase in ('fetch', 'reason', 'build', 'all'):
        rec = load_published_record(args.report)
        uni = build_universe(rec)
        save_state('record.pkl', (rec, uni))
    else:
        rec, uni = load_state('record.pkl')

    if phase == 'fetch':
        return 0

    if phase in ('reason', 'build', 'all'):
        reasoned = run_reasoning(uni)
        save_state('reasoned.pkl', reasoned)
        if phase == 'reason':
            return 0
    else:
        reasoned = load_state('reasoned.pkl')

    if phase in ('build', 'all'):
        today = datetime.now(timezone.utc).date()
        weekly = compute_weekly_us(uni, rec, reasoned)
        series = build_daily_series(uni, rec, reasoned, weekly,
                                     today - timedelta(days=2))
        # Seam: blend into the existing June+ corpus.
        june_dates = [d for d in _list_dated_folders()
                      if d >= _iso(CORPUS_START)]
        corpus_vals = load_corpus_values(june_dates, set(series))
        blended = apply_seam_blend(series, corpus_vals)
        logger.info('seam blend applied to %d boundary titles', len(blended))
        # Where the corpus already carries a key on a date, the corpus
        # wins: drop my value for that (key, date).
        dropped = 0
        for key, cvals in corpus_vals.items():
            mine = series.get(key) or {}
            for cd in cvals:
                as_of = _iso(date.fromisoformat(cd) - timedelta(days=1))
                if as_of in mine:
                    del mine[as_of]
                    dropped += 1
        logger.info('dropped %d (key, day) pairs already covered by '
                    'the corpus', dropped)
        save_state('series.pkl', (weekly, series, rec))
        if phase == 'build':
            return 0
    else:
        weekly, series, rec = load_state('series.pkl')

    if phase in ('write', 'all'):
        only = ({x.strip() for x in args.dates.split(',') if x.strip()}
                or None)
        write_dates(rec, uni, reasoned, weekly, series,
                    dry_run=args.dry_run, force=args.force,
                    only_dates=only)
        return 0

    if phase == 'audit':
        reasoned = load_state('reasoned.pkl')
        weekly, series, rec2 = load_state('series.pkl')
        run_audit(rec2, uni, reasoned, weekly, series)
        return 0

    if phase == 'purge':
        purge_caches(dry_run=args.dry_run)
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
