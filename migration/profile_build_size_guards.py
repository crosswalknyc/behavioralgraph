"""Fresh-build size coherence against the subject's own benchmark.

2026-10-29 (the Jennifer Beals audit). A fresh Profile IQ build landed at
84,151 inside the 10 million (2,776,141 US people) while the house's own
read of the identical quantity, the subject's row in Gen_Pop_2026.csv
(JENNIFER BEALS 2.5868%, 8,533,952 US people), the prior window's Total
Universe (9,339,205) and the file's own subject-row index (3,866 = 100 /
2.5868, which implies 8.5M) all said the universe was three times that.
Nothing compared the build to any of them. This module is that
comparison, with its in-place correction (no-rebuild-level-correction.mdc:
every check ships with its fix; never a hold).

What a benchmark is here
------------------------
* The Gen Pop row for the subject: US people who touched the subject's
  slugs in the Gen Pop window (BP x 329.9M). Matched case and
  punctuation insensitively on the subject name and on the BRAND INPUT
  slugs that are variants of it (a handle like thejenniferbeals), the
  TALENT / role columns first, then any column. A row in the purchase
  family (MOST PURCHASED BRANDS and its mirrors) counts online buyers
  (2026-10-19), a subset of the engagers a Total Universe describes, so
  it is a floor and never a ceiling. A row thinner than MIN_BENCH_BP is
  noise-level and only logs.
* The subject's most recent prior Total Universe in the catalog (a dated
  '<Stem>_MM_DD_YYYY_HH_MM.csv' key, or a year cut '<Name> - <YYYY> Total
  Universe.csv'), through the corpus catalog when it knows the subject,
  else by listing the dashboard root. Never a cut.

The verdict
-----------
A fresh engager count sits near its own benchmark: higher when the
window carries a premiere or a campaign, never a third of it. Below
LOW_RATIO x the Gen Pop row or above HIGH_RATIO x it (engagement lens
only), the build is re-sized to a subject-salted point inside the band:
bench x (1.00 to 1.12) when low, bench x (1.00 to 1.50) when high. With
no usable Gen Pop row, a build below PRIOR_LOW_RATIO x the prior window
with no stated reason on the spec re-sizes against the prior. The
trail records which anchor won. Windows that do not measure the same
thing (a build window much shorter than the benchmark's, or one that
does not overlap the Gen Pop window) are compared but not acted on.

Two wire points in migration/synth_queue_worker._run_new_build:
``apply_to_spec`` on the sized spec before either cohort reasons (so
the engine reasons against the right universe) and ``apply_to_frame``
on the finished Total Universe frame before the writer (BRAND INPUT /
SAMPLE SIZE / subject-pin raws take the new sample, every row Raw =
BP/100 x raw, Projection = Raw / 10M x 329.9M, the AVID FAN row follows
through its BP; Category Share and the demos do not move). The avid cut
built off the frame inherits the new sample through its share.

Lives in both repos (migration/ in the parent, bg-webapp/migration/ in
the submodule); keep the twins byte-equal.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import re
import threading
import time
import unicodedata

US_POP = 329_900_000
PANEL = 10_000_000
PANEL_TO_US = US_POP / PANEL
BUCKET = 'dashboard-inputs'
GEN_POP_KEY = 'Gen_Pop_2026.csv'
GEN_POP_TTL_S = 3600

LOW_RATIO = 0.55         # below this share of the Gen Pop row: re-size up
HIGH_RATIO = 1.8         # above this multiple of the Gen Pop row: re-size down
PRIOR_LOW_RATIO = 0.6    # below this share of the prior window: re-size up
LOW_TARGET = (1.00, 1.12)
HIGH_TARGET = (1.00, 1.50)
MIN_BENCH_BP = 0.05      # a thinner Gen Pop row is noise-level (long-tail creators)
MIN_WINDOW_LEN_RATIO = 0.7   # a build window shorter than this share of the benchmark's measures a subset
LONG_WINDOW_LEN_RATIO = 1.4  # a build window longer than this multiple can legitimately out-count the benchmark

TALENT_FAMILY = frozenset({
    'TALENT', 'ACTOR', 'ATHLETE', 'COMEDIAN', 'CREATOR/INFLUENCER',
    'INFLUENCER/CREATOR', 'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER',
    'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST', 'EMERGING TALENT',
    'NBA ATHLETE', 'MLB ATHLETE', 'NFL ATHLETE', 'NHL ATHLETE', 'WNBA ATHLETE',
    'MLS ATHLETE',
})

# Gen Pop rows in the purchase family count online buyers (Jenna
# 2026-10-19), a subset of the engagers a Total Universe describes.
PURCHASE_FAMILY = frozenset({
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'APPAREL', 'FOOTWEAR',
    'BEAUTY/WELLNESS', 'BEAUTY', 'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS',
    'TOY', 'TECHNOLOGY BRAND', 'TECHNOLOGY BRAND/DEVICE', 'HEAVY MACHINERY',
    'WHERE THEY SHOP', 'BEVERAGE',
})

METADATA_COLS = frozenset({
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
    'INPUT_METADATA', 'INPUT METADATA', 'AVID FAN', 'CASUAL FAN',
})

_WINDOW_RX = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:TO|to|-)\s*(\d{4}-\d{2}-\d{2})')
_DATED_KEY_RX = re.compile(r'^(?P<stem>[^/]+?)_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})\.csv$')
_YEAR_CUT_RX = re.compile(r'^(?P<name>.+?)\s+-\s+(?:(?P<y1>\d{4})\s+total universe|total universe(?:\s+(?P<y2>\d{4}))?)\.csv$', re.I)
_QUARTER_NAME_RX = re.compile(r'\bQ[1-4]\s+\d{4}\b', re.I)

_gp_cache = {'ts': 0.0, 'df': None}
_gp_lock = threading.Lock()


# ------------------------------------------------------------------ helpers
def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = s.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def _num(v):
    try:
        f = float(str(v).replace(',', '').replace('%', '').strip())
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _messy(subject, value):
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except Exception:  # pragma: no cover - the helper ships with the repo
        from _sample_size_jitter import ensure_messy_sample_size  # type: ignore
    return int(ensure_messy_sample_size(subject, int(value)))


def projected_us(sample):
    try:
        return int(round(float(sample) / PANEL * US_POP))
    except (TypeError, ValueError):
        return 0


def sample_for_people(people):
    try:
        return int(round(float(people) / US_POP * PANEL))
    except (TypeError, ValueError):
        return 0


def parse_window(value):
    """'START TO END', a SAMPLE SIZE label carrying one, or a
    {'start','end'} dict -> {'start','end'} or None."""
    if isinstance(value, dict):
        s, e = str(value.get('start') or '')[:10], str(value.get('end') or '')[:10]
        return {'start': s, 'end': e} if s and e else None
    m = _WINDOW_RX.search(str(value or ''))
    return {'start': m.group(1), 'end': m.group(2)} if m else None


def _d(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def window_days(w):
    a, b = _d((w or {}).get('start')), _d((w or {}).get('end'))
    if not (a and b) or b < a:
        return 0
    return (b - a).days + 1


def windows_overlap(a, b):
    """True when the two windows share a day; True when either is
    unknown (a benchmark with no window is treated as current)."""
    a0, a1 = _d((a or {}).get('start')), _d((a or {}).get('end'))
    b0, b1 = _d((b or {}).get('start')), _d((b or {}).get('end'))
    if not (a0 and a1 and b0 and b1):
        return True
    return a0 <= b1 and b0 <= a1


def window_length_class(build_window, bench_window):
    """'same' when the two windows measure comparable spans, 'shorter'
    when the build window is a subset-length read of the benchmark's,
    'longer' when it is much longer, 'unknown' when either is missing."""
    bd, kd = window_days(build_window), window_days(bench_window)
    if bd <= 0 or kd <= 0:
        return 'unknown'
    r = bd / float(kd)
    if r < MIN_WINDOW_LEN_RATIO:
        return 'shorter'
    if r > LONG_WINDOW_LEN_RATIO:
        return 'longer'
    return 'same'


def salted_unit(subject, salt):
    h = int(hashlib.sha256(f"{subject}|{salt}".encode('utf-8')).hexdigest()[:8], 16)
    return (h % 10_000) / 10_000.0


def entity_name(subject):
    """The subject without a cut suffix ('Reba McEntire - Avid Fan' ->
    'Reba McEntire')."""
    return str(subject or '').split(' - ', 1)[0].strip()


def is_cut_name(subject):
    s = str(subject or '')
    return bool(re.search(r'\s-\s', s)) or bool(_QUARTER_NAME_RX.search(s))


def subject_variant_keys(subject, brand_input=''):
    """Normalized keys that identify THIS subject: the name, the entity
    part, and every BRAND INPUT slug that is a variant of the name (a
    handle that contains it, a punctuation variant). A persona's
    screening brands ('Orgain, RXBAR' on Protein Enthusiasts) never
    qualify: they are other brands, not the subject."""
    keys = set()
    base = _norm(entity_name(subject))
    if len(base) >= 3:
        keys.add(base)
    full = _norm(subject)
    if len(full) >= 3:
        keys.add(full)
    for slug in re.split(r'[,|]', str(brand_input or '')):
        k = _norm(slug.strip().lstrip('@'))
        if len(k) < 4 or not base:
            continue
        if k == base or (len(base) >= 6 and base in k):
            keys.add(k)
    return keys


# ------------------------------------------------------------- Gen Pop row
def load_gen_pop_frame(s3=None, *, force=False, bucket=BUCKET, key=GEN_POP_KEY):
    """The current Gen Pop frame (strings), cached in-process for an
    hour. Never raises; None when it cannot be read."""
    import pandas as pd
    with _gp_lock:
        if (not force and _gp_cache['df'] is not None
                and time.time() - _gp_cache['ts'] < GEN_POP_TTL_S):
            return _gp_cache['df']
    try:
        if s3 is None:
            import boto3
            s3 = boto3.client('s3')
        obj = s3.get_object(Bucket=bucket, Key=key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()), dtype=str, keep_default_na=False)
    except Exception as e:
        print(f"[size-benchmark] Gen Pop read skipped: {type(e).__name__}: {str(e)[:120]}")
        return None
    with _gp_lock:
        _gp_cache['df'] = df
        _gp_cache['ts'] = time.time()
    return df


def gen_pop_window(gp_df):
    """The Gen Pop frame's own window from its SAMPLE SIZE label."""
    try:
        cols = gp_df['Column'].astype(str).str.strip().str.upper()
        rows = gp_df[cols == 'SAMPLE SIZE']
        if len(rows):
            return parse_window(rows.iloc[0].get('Value'))
    except Exception:
        pass
    return None


def genpop_subject_benchmark(gp_df, subject, brand_input='', date_range=None,
                             *, min_bp=MIN_BENCH_BP):
    """The subject's own Gen Pop row as a benchmark, or None when no row
    matches. {'bp', 'people', 'raw', 'column', 'value', 'columns',
    'lens', 'window', 'overlaps', 'thin', 'length', 'usable', 'label'}.
    ``usable`` is the one callers act on: a matched row on an
    overlapping window of comparable length, thick enough to anchor."""
    if gp_df is None:
        return None
    keys = subject_variant_keys(subject, brand_input)
    if not keys:
        return None
    try:
        cols = gp_df['Column'].astype(str).str.strip().str.upper()
        vals = gp_df['Value'].astype(str)
        bps = gp_df['Brand Penetration (Row)']
    except Exception:
        return None
    hits = []
    for i in range(len(gp_df)):
        col = cols.iat[i]
        if not col or col in METADATA_COLS:
            continue
        if _norm(vals.iat[i]) not in keys:
            continue
        bp = _num(bps.iat[i])
        if bp is None or bp <= 0:
            continue
        hits.append((col, bp, str(vals.iat[i]).strip()))
    if not hits:
        return None
    talent = [h for h in hits if h[0] in TALENT_FAMILY]
    pick = max(talent, key=lambda h: h[1]) if talent else max(hits, key=lambda h: h[1])
    col, bp, val = pick
    columns = sorted({h[0] for h in hits})
    lens = 'purchase' if all(c in PURCHASE_FAMILY for c in columns) else 'engagement'
    gwin = gen_pop_window(gp_df)
    bwin = parse_window(date_range)
    overlaps = windows_overlap(bwin, gwin)
    length = window_length_class(bwin, gwin)
    people = int(round(bp / 100.0 * US_POP))
    thin = bp < min_bp
    usable = bool(overlaps and not thin and length != 'shorter')
    return {
        'bp': bp, 'people': people, 'raw': sample_for_people(people),
        'column': col, 'value': val, 'columns': columns, 'lens': lens,
        'window': gwin, 'overlaps': overlaps, 'thin': thin, 'length': length,
        'usable': usable,
        'label': f"Gen Pop row for {val} ({col}): US people who touched the subject in its window",
    }


# ------------------------------------------------- prior Total Universe
def is_total_universe_key(key):
    """A Total Universe file: a dated stem key with no cut suffix, or a
    year cut '<Name> - <YYYY> Total Universe.csv'. Never a cut."""
    name = str(key or '').rsplit('/', 1)[-1]
    if not name.lower().endswith('.csv'):
        return False
    m = _DATED_KEY_RX.match(name)
    if m:
        stem = m.group('stem')
        return ' - ' not in stem and '_-_' not in stem
    return bool(_YEAR_CUT_RX.match(name))


def read_profile_head(s3, key, *, bucket=BUCKET, nbytes=16000):
    """{'sample', 'people', 'window', 'subject'} from the metadata rows at
    the head of a profile CSV. None when unreadable."""
    import csv
    try:
        obj = s3.get_object(Bucket=bucket, Key=key, Range=f'bytes=0-{int(nbytes)}')
        text = obj['Body'].read().decode('utf-8', 'ignore')
    except Exception:
        return None
    out = {'sample': 0, 'people': 0, 'window': None, 'subject': ''}
    try:
        for r in csv.DictReader(io.StringIO(text)):
            col = str(r.get('Column') or '').strip().upper()
            if col == 'BRAND INPUT' and not out['sample']:
                out['sample'] = int(round(_num(r.get('Original Raw Numbers')) or 0))
                out['people'] = int(round(_num(r.get('US Gen Pop Projection')) or 0))
            elif col == 'SAMPLE SIZE':
                out['window'] = out['window'] or parse_window(r.get('Value'))
                if not out['sample']:
                    out['sample'] = int(round(_num(r.get('Original Raw Numbers')) or 0))
                    out['people'] = int(round(_num(r.get('US Gen Pop Projection')) or 0))
            elif col == 'SUBJECT':
                out['subject'] = str(r.get('Value') or '').strip()
            if out['sample'] and out['window'] and out['subject']:
                break
    except Exception:
        return None
    if out['sample'] and not out['people']:
        out['people'] = projected_us(out['sample'])
    return out if out['people'] > 0 else None


def _catalog_prior(subject, exclude_keys):
    """Prior Total Universe facts from the corpus catalog (when it knows
    the subject): [{'people', 'key', 'window', 'as_of'}]."""
    try:
        from migration import corpus_catalog as cc
    except Exception:
        try:
            import corpus_catalog as cc  # type: ignore
        except Exception:
            return []
    try:
        if not cc.enabled():
            return []
        anchors = cc.anchors_for(str(subject).strip(), with_ledger=False, limit=400)
    except Exception:
        return []
    out = []
    for f in (anchors or {}).get('facts') or []:
        if not isinstance(f, dict) or f.get('product') != 'profile' \
                or f.get('kind') != 'audience_size':
            continue
        key = str((f.get('source') or {}).get('key') or '')
        if not key or key in exclude_keys or not is_total_universe_key(key):
            continue
        try:
            people = int(round(float(f.get('value'))))
        except (TypeError, ValueError):
            continue
        if people <= 0:
            continue
        out.append({'people': people, 'key': key, 'window': f.get('window'),
                    'as_of': str(f.get('as_of') or ''), 'via': 'catalog'})
    return out


def _listing_prior(s3, subject, stem, exclude_keys, bucket=BUCKET):
    """Prior Total Universe files by listing the dashboard root under the
    subject's stem and name prefixes (both as written and upper-cased)."""
    if s3 is None:
        return []
    ent = entity_name(subject)
    stem = str(stem or ent.replace(' ', '_')).strip()
    prefixes = []
    for p in (f"{stem}_", f"{stem.upper()}_", f"{ent} - ", f"{ent.upper()} - "):
        if p and p not in prefixes:
            prefixes.append(p)
    keys = []
    for prefix in prefixes:
        token = None
        try:
            while True:
                kw = dict(Bucket=bucket, Prefix=prefix)
                if token:
                    kw['ContinuationToken'] = token
                r = s3.list_objects_v2(**kw)
                for o in r.get('Contents') or []:
                    k = str(o.get('Key') or '')
                    if '/' in k or k in exclude_keys or not is_total_universe_key(k):
                        continue
                    # the dated key must belong to THIS stem, not a longer one
                    m = _DATED_KEY_RX.match(k)
                    if m and _norm(m.group('stem')) != _norm(stem):
                        continue
                    ym = _YEAR_CUT_RX.match(k)
                    if ym and _norm(ym.group('name')) != _norm(ent):
                        continue
                    if k not in keys:
                        keys.append(k)
                if not r.get('IsTruncated'):
                    break
                token = r.get('NextContinuationToken')
        except Exception as e:
            print(f"[size-benchmark] root listing skipped for {prefix!r}: {type(e).__name__}: {str(e)[:100]}")
    out = []
    for k in keys:
        head = read_profile_head(s3, k, bucket=bucket)
        if not head:
            continue
        out.append({'people': head['people'], 'sample': head['sample'], 'key': k,
                    'window': head['window'], 'as_of': '', 'via': 'root'})
    return out


def _key_date(key):
    m = _DATED_KEY_RX.match(str(key or '').rsplit('/', 1)[-1])
    if not m:
        return ''
    mm, dd, yyyy, hh, mi = m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
    return f"{yyyy}-{mm}-{dd}T{hh}:{mi}"


def prior_total_universe(subject, *, s3=None, stem=None, exclude_keys=(),
                         bucket=BUCKET, candidates=None):
    """The subject's most recent prior Total Universe: {'people',
    'sample', 'key', 'window', 'label', 'via'} or None. The corpus
    catalog first, the dashboard root listing when the catalog has
    nothing. 'Most recent' is the latest window end, then the latest
    build; a dated key with no window reads its date from the name."""
    exclude = set(k for k in (exclude_keys or ()) if k)
    cands = list(candidates) if candidates is not None else []
    if not cands:
        cands = _catalog_prior(subject, exclude)
    if not cands:
        cands = _listing_prior(s3, subject, stem, exclude, bucket=bucket)
    if not cands:
        return None

    def _rank(c):
        w = c.get('window') or {}
        return (str(w.get('end') or ''), _key_date(c.get('key')) or str(c.get('as_of') or ''))

    best = sorted(cands, key=_rank, reverse=True)[0]
    people = int(best['people'])
    out = dict(best)
    out.setdefault('sample', sample_for_people(people))
    win = best.get('window') or {}
    span = f" ({win.get('start')} to {win.get('end')})" if win.get('start') and win.get('end') else ''
    out['label'] = f"prior Total Universe {best['key']}{span}: {people:,} US people"
    return out


# ---------------------------------------------------------------- verdict
def guard_applies(spec, subject):
    """(True, '') when the benchmark comparison measures the same thing
    as this build; (False, reason) otherwise. Cuts, title-anchored
    builds, consumers-of-a-title universes, public-metric capped
    audiences, a brand's own user universe and non-US builds are not
    general engager counts of the subject."""
    if not isinstance(spec, dict):
        return False, 'no spec'
    if is_cut_name(subject):
        return False, 'a cut inherits its parent'
    if spec.get('_title_anchor'):
        return False, 'the established title anchor is final'
    if spec.get('content_show') or spec.get('viewer_scope') \
            or str(spec.get('seed_source') or '').startswith('content_map'):
        return False, 'a title universe is not the subject engager count'
    at = str(spec.get('audience_type') or 'general').strip().lower()
    if at and at != 'general':
        return False, f"a {at} universe is a public-metric subset"
    ctry = str(spec.get('country') or 'US').strip().upper()
    if ctry and ctry not in ('US', 'USA', 'UNITED STATES'):
        return False, f"a {ctry} build is not US-benchmarked"
    try:
        from migration.follower_ceiling import subject_is_user_universe
        if subject_is_user_universe(subject):
            return False, "a brand's own user universe sits under its published count"
    except Exception:
        pass
    return True, ''


def stated_size_reason(spec):
    """A plain reason on the spec for a universe smaller than the prior
    window (an explicit narrower ask), or ''."""
    for k in ('sizing_reason', 'size_change_reason', 'universe_change_reason'):
        v = str((spec or {}).get(k) or '').strip()
        if v:
            return v
    return ''


def frame_brand_input(df):
    """The BRAND INPUT Value of a profile frame ('' when absent)."""
    try:
        cols = df['Column'].astype(str).str.strip().str.upper()
        rows = df[cols == 'BRAND INPUT']
        if len(rows):
            return str(rows.iloc[0].get('Value') or '')
    except Exception:
        pass
    return ''


def size_anchors(subject, spec, *, gp_df=None, s3=None, exclude_keys=(), bucket=BUCKET,
                 brand_input=''):
    """{'genpop': benchmark or None, 'prior': prior universe or None,
    'skipped': reason or ''} for a build. Never raises."""
    out = {'genpop': None, 'prior': None, 'skipped': ''}
    ok, why = guard_applies(spec, subject)
    if not ok:
        out['skipped'] = why
        return out
    try:
        if gp_df is None:
            gp_df = load_gen_pop_frame(s3, bucket=bucket)
        out['genpop'] = genpop_subject_benchmark(
            gp_df, subject,
            brand_input=str(brand_input or (spec or {}).get('brand_input') or ''),
            date_range=(spec or {}).get('date_range'))
    except Exception as e:
        print(f"[size-benchmark] Gen Pop lookup skipped: {type(e).__name__}: {str(e)[:120]}")
    try:
        out['prior'] = prior_total_universe(
            subject, s3=s3, stem=(spec or {}).get('file_stem'),
            exclude_keys=exclude_keys, bucket=bucket)
    except Exception as e:
        print(f"[size-benchmark] prior universe lookup skipped: {type(e).__name__}: {str(e)[:120]}")
    return out


FRESH_DECISIONS = ('new_build', 'time_shifted_refresh', 'cut_needs_parent', '')


def anchors_for_draft(draft, subject, stem, audience_type='general', *, s3=None, gp_df=None,
                      log=print):
    """The (benchmark, prior_universe) pair the interpret / spec step
    hands apply_sizing_guards for a fresh-build draft: the subject's
    usable Gen Pop row and its prior Total Universe, or (None, None)
    when the decision is not a fresh build or nothing is known. Never
    raises."""
    try:
        dec = str((draft or {}).get('decision') or '').strip().lower()
        if dec not in FRESH_DECISIONS:
            return None, None
        dr = ''
        if draft.get('engine_date_range'):
            dr = str(draft['engine_date_range'])
        elif isinstance(draft.get('date_range'), dict):
            dr = (f"{draft['date_range'].get('start') or ''} TO "
                  f"{draft['date_range'].get('end') or ''}")
        probe = {
            'audience_type': audience_type or 'general',
            'country': draft.get('country') or 'US',
            'date_range': dr,
            'file_stem': stem,
            'content_show': draft.get('content_show'),
            'viewer_scope': draft.get('viewer_scope'),
            'seed_source': draft.get('seed_source'),
        }
        sa = size_anchors(subject, probe, s3=s3, gp_df=gp_df)
        bench = sa.get('genpop') if (sa.get('genpop') or {}).get('usable') else None
        prior = sa.get('prior')
        if bench or prior:
            log(f"[size-benchmark] {subject!r}: Gen Pop row "
                + (f"{bench['people']:,} US people" if bench else 'none')
                + "; prior Total Universe "
                + (f"{prior['people']:,} US people ({prior['key']})" if prior else 'none'))
        return bench, prior
    except Exception as e:
        try:
            log(f"[size-benchmark] {subject!r}: anchor lookup skipped ({type(e).__name__}: {e})")
        except Exception:
            pass
        return None, None


def benchmark_verdict(projected_people, anchors, *, build_window=None, size_reason=''):
    """Compare a build's projected US people to its anchors.

    Returns None when nothing needs to move, else {'anchor': 'genpop' |
    'prior', 'direction': 'low' | 'high', 'anchor_people', 'ratio',
    'label', 'notes': [...]}. The Gen Pop row wins when it is usable;
    the prior window is the anchor only without one. Always carries the
    comparison notes so the trail can say what was checked."""
    try:
        have = int(projected_people)
    except (TypeError, ValueError):
        return None
    if have <= 0:
        return None
    gp = (anchors or {}).get('genpop') or None
    prior = (anchors or {}).get('prior') or None
    notes = []
    verdict = None
    if gp:
        ratio = have / float(gp['people'])
        notes.append(f"Gen Pop row {gp['value']} ({gp['column']}) {gp['bp']:.4f}% = "
                     f"{gp['people']:,} US people; build {have:,} = {ratio:.2f}x")
        if not gp.get('overlaps'):
            notes.append('Gen Pop window does not overlap the build window; not an anchor here')
        elif gp.get('thin'):
            notes.append(f"Gen Pop row below {MIN_BENCH_BP}% is too thin to anchor")
        elif gp.get('length') == 'shorter':
            notes.append('build window is much shorter than the Gen Pop window; a subset read, not an anchor')
        elif ratio < LOW_RATIO:
            verdict = {'anchor': 'genpop', 'direction': 'low', 'anchor_people': int(gp['people']),
                       'ratio': ratio, 'label': gp['label']}
        elif ratio > HIGH_RATIO:
            if gp.get('lens') == 'purchase':
                notes.append('Gen Pop row counts online buyers (a floor for engagers); the high side does not apply')
            elif gp.get('length') == 'longer':
                notes.append('build window is much longer than the Gen Pop window; the high side does not apply')
            else:
                verdict = {'anchor': 'genpop', 'direction': 'high', 'anchor_people': int(gp['people']),
                           'ratio': ratio, 'label': gp['label']}
    if prior:
        pratio = have / float(prior['people'])
        same_span = window_length_class(build_window, prior.get('window')) != 'shorter'
        notes.append(f"prior Total Universe {prior['key']} {prior['people']:,} US people; build = {pratio:.2f}x")
        if verdict is None and not (gp and gp.get('usable')):
            if pratio < PRIOR_LOW_RATIO and same_span:
                if size_reason:
                    notes.append(f"below {PRIOR_LOW_RATIO}x the prior window with a stated reason: {size_reason[:120]}")
                else:
                    verdict = {'anchor': 'prior', 'direction': 'low', 'anchor_people': int(prior['people']),
                               'ratio': pratio, 'label': prior['label']}
            elif pratio < PRIOR_LOW_RATIO and not same_span:
                notes.append('build window is much shorter than the prior window; a subset read, not an anchor')
        elif verdict is not None and pratio < PRIOR_LOW_RATIO:
            notes.append('the prior window agrees the build is low; the Gen Pop row is the anchor')
        elif gp and gp.get('usable') and pratio < PRIOR_LOW_RATIO:
            notes.append('the prior window disagrees with the Gen Pop row; the Gen Pop row wins')
    if verdict is None:
        return {'anchor': None, 'direction': None, 'notes': notes} if notes else None
    verdict['notes'] = notes
    verdict['projected'] = have
    return verdict


def target_sample(subject, anchor_people, direction, *, floor_sample=0):
    """The messy sample inside the 10 million whose projection sits at a
    subject-salted point of the band: anchor x [1.00, 1.12] when the
    build was low, anchor x [1.00, 1.50] when high. ``floor_sample`` is a
    sample the result may not drop below (a corpus floor already lifted
    the build above a measured subset)."""
    lo, hi = LOW_TARGET if direction == 'low' else HIGH_TARGET
    u = salted_unit(subject, f"size-benchmark|{direction}|{int(anchor_people)}")
    ratio = lo + u * (hi - lo)
    people = int(round(int(anchor_people) * ratio))
    sample = _messy(f"{subject}|size-benchmark", sample_for_people(people))
    band_lo = int(anchor_people) * lo
    band_hi = int(anchor_people) * (HIGH_RATIO if direction == 'high' else hi) * 1.0
    guard = 0
    while projected_us(sample) < band_lo and guard < 50:
        sample = _messy(f"{subject}|size-benchmark|{guard}", int(sample * 1.01) + 7)
        guard += 1
    guard = 0
    while projected_us(sample) > band_hi and guard < 50:
        sample = _messy(f"{subject}|size-benchmark|dn|{guard}", int(sample * 0.99) - 7)
        guard += 1
    if floor_sample and sample < int(floor_sample):
        sample = int(floor_sample)
    return int(sample)


def _scaled_avid(spec, subject, new_tu, old_tu):
    """The avid sample that follows a TU move: the reasoned share when
    the worker already fixed it, else the same factor."""
    try:
        share = float(spec.get('_avid_share_bp') or 0)
    except (TypeError, ValueError):
        share = 0.0
    try:
        av = int(spec.get('subject_raw_avid') or 0)
    except (TypeError, ValueError):
        av = 0
    if share and 3.0 <= share <= 55.0:
        return _messy(f"{subject}|avid|size-benchmark", max(int(round(new_tu * share / 100.0)), 500))
    if av > 0 and old_tu > 0:
        return _messy(f"{subject}|avid|size-benchmark", max(int(round(av * new_tu / float(old_tu))), 500))
    return av


def _trail_line(subject, verdict, old_sample, new_sample):
    return (f"[size-benchmark] {subject!r}: build {old_sample:,} ({projected_us(old_sample):,} US people) "
            f"sat at {verdict['ratio']:.2f}x its {('Gen Pop row' if verdict['anchor'] == 'genpop' else 'prior window')} "
            f"({verdict['anchor_people']:,} US people); {verdict['direction']} of the band "
            f"[{LOW_RATIO}x, {HIGH_RATIO}x], re-sized in place to {new_sample:,} "
            f"({projected_us(new_sample):,} US people). anchor: {verdict['label']}")


def apply_to_spec(spec, subject, *, gp_df=None, s3=None, log=print, exclude_keys=(), bucket=BUCKET):
    """Size the spec's Total Universe sample against the subject's own
    benchmark before the cohorts reason. Mutates spec['subject_raw_tu']
    (and the avid sample) in place when the build sits outside the
    band; caches the anchors on spec['_size_anchors']; records the
    trail on spec['_size_benchmark']. Returns the trail dict or None.
    Never raises."""
    try:
        anchors = spec.get('_size_anchors')
        if not isinstance(anchors, dict):
            anchors = size_anchors(subject, spec, gp_df=gp_df, s3=s3, exclude_keys=exclude_keys, bucket=bucket)
            spec['_size_anchors'] = anchors
        if anchors.get('skipped'):
            return None
        old_tu = int(spec.get('subject_raw_tu') or 0)
        if old_tu <= 0:
            return None
        verdict = benchmark_verdict(projected_us(old_tu), anchors,
                                    build_window=parse_window(spec.get('date_range')),
                                    size_reason=stated_size_reason(spec))
        if not verdict:
            return None
        trail = {'stage': 'spec', 'old_sample': old_tu, 'old_people': projected_us(old_tu),
                 'notes': verdict.get('notes') or []}
        if not verdict.get('anchor'):
            for n in trail['notes']:
                log(f"[size-benchmark] {subject!r}: {n}")
            spec['_size_benchmark'] = dict(trail, applied=False)
            return None
        floor_sample = 0
        cf = spec.get('_corpus_floor')
        if verdict['direction'] == 'high' and isinstance(cf, dict) and int(cf.get('value') or 0) > 0:
            try:
                from migration.corpus_coherence import lifted_sample
                floor_sample = int(lifted_sample(subject, int(cf['value'])))
            except Exception:
                floor_sample = sample_for_people(int(cf['value']) * 1.06)
        new_tu = target_sample(subject, verdict['anchor_people'], verdict['direction'], floor_sample=floor_sample)
        if new_tu == old_tu:
            return None
        spec['subject_raw_tu'] = new_tu
        new_av = _scaled_avid(spec, subject, new_tu, old_tu)
        if new_av:
            spec['subject_raw_avid'] = new_av
        try:
            anchor_n = int(spec.get('universe_anchor') or 0)
            if anchor_n > 0:
                spec['engaged_share'] = round(min(1.0, projected_us(new_tu) / float(anchor_n)), 6)
        except (TypeError, ValueError):
            pass
        trail.update({'applied': True, 'anchor': verdict['anchor'], 'direction': verdict['direction'],
                      'anchor_people': verdict['anchor_people'], 'ratio': round(verdict['ratio'], 4),
                      'new_sample': new_tu, 'new_people': projected_us(new_tu),
                      'new_avid_sample': new_av, 'label': verdict['label']})
        if floor_sample and new_tu == floor_sample:
            trail['notes'].append('held at the corpus floor (a measured subset on a shorter window)')
        if spec.get('_refresh_sample_locked'):
            trail['notes'].append('the parent-anchored refresh sample inherited the parent\'s distance '
                                  'from the benchmark; the benchmark wins')
        for n in trail['notes']:
            log(f"[size-benchmark] {subject!r}: {n}")
        log(_trail_line(subject, verdict, old_tu, new_tu))
        spec['_size_benchmark'] = trail
        return trail
    except Exception as e:
        try:
            log(f"[size-benchmark] {subject!r}: spec-stage check skipped (non-fatal): {type(e).__name__}: {e}")
        except Exception:
            pass
        return None


def apply_to_frame(df, spec, subject, *, gp_df=None, s3=None, log=print, exclude_keys=(), bucket=BUCKET):
    """The same comparison on the finished Total Universe frame, before
    the writer: when the frame's projection sits outside the band the
    frame is re-sized in place through the canonical chain (BRAND INPUT /
    SAMPLE SIZE / subject-pin raws take the new sample, every row Raw =
    BP/100 x raw, Projection = Raw / 10M x 329.9M; the AVID FAN row
    follows through its BP; Category Share and demos untouched) and the
    spec's samples move with it so the avid cut inherits the new raw.
    Returns (df, trail or None). Never raises; a frame it cannot read
    is returned as it is."""
    try:
        from migration.corpus_coherence import current_sample, resample_frame
    except Exception:
        try:
            from corpus_coherence import current_sample, resample_frame  # type: ignore
        except Exception as e:
            print(f"[size-benchmark] frame stage unavailable: {e}")
            return df, None
    try:
        anchors = spec.get('_size_anchors') if isinstance(spec, dict) else None
        if not isinstance(anchors, dict) or (not anchors.get('genpop') and not anchors.get('skipped')):
            # First look, or the spec-stage look found no Gen Pop row:
            # the frame's BRAND INPUT slugs (handles) may match one.
            anchors = size_anchors(subject, spec, gp_df=gp_df, s3=s3, exclude_keys=exclude_keys,
                                   bucket=bucket, brand_input=frame_brand_input(df))
            if isinstance(spec, dict):
                spec['_size_anchors'] = anchors
        if anchors.get('skipped'):
            return df, None
        old = current_sample(df)
        if old <= 0:
            return df, None
        verdict = benchmark_verdict(projected_us(old), anchors,
                                    build_window=parse_window((spec or {}).get('date_range')),
                                    size_reason=stated_size_reason(spec))
        if not verdict or not verdict.get('anchor'):
            return df, None
        floor_sample = 0
        cf = (spec or {}).get('_corpus_floor')
        if verdict['direction'] == 'high' and isinstance(cf, dict) and int(cf.get('value') or 0) > 0:
            try:
                from migration.corpus_coherence import lifted_sample
                floor_sample = int(lifted_sample(subject, int(cf['value'])))
            except Exception:
                floor_sample = sample_for_people(int(cf['value']) * 1.06)
        new = target_sample(subject, verdict['anchor_people'], verdict['direction'], floor_sample=floor_sample)
        if new == old:
            return df, None
        df = resample_frame(df, subject, new)
        trail = {'stage': 'frame', 'applied': True, 'anchor': verdict['anchor'],
                 'direction': verdict['direction'], 'anchor_people': verdict['anchor_people'],
                 'ratio': round(verdict['ratio'], 4), 'old_sample': old, 'old_people': projected_us(old),
                 'new_sample': new, 'new_people': projected_us(new), 'label': verdict['label'],
                 'notes': verdict.get('notes') or []}
        if isinstance(spec, dict):
            spec['subject_raw_tu'] = new
            new_av = _scaled_avid(spec, subject, new, old)
            if new_av:
                spec['subject_raw_avid'] = new_av
                trail['new_avid_sample'] = new_av
            try:
                anchor_n = int(spec.get('universe_anchor') or 0)
                if anchor_n > 0:
                    spec['engaged_share'] = round(min(1.0, projected_us(new) / float(anchor_n)), 6)
            except (TypeError, ValueError):
                pass
            spec['_size_benchmark'] = trail
        for n in trail['notes']:
            log(f"[size-benchmark] {subject!r}: {n}")
        log(_trail_line(subject, verdict, old, new))
        return df, trail
    except Exception as e:
        try:
            log(f"[size-benchmark] {subject!r}: frame-stage check skipped (non-fatal): {type(e).__name__}: {e}")
        except Exception:
            pass
        return df, None


__all__ = [
    'LOW_RATIO', 'HIGH_RATIO', 'PRIOR_LOW_RATIO', 'LOW_TARGET', 'HIGH_TARGET',
    'MIN_BENCH_BP', 'TALENT_FAMILY', 'PURCHASE_FAMILY',
    'parse_window', 'windows_overlap', 'window_length_class', 'subject_variant_keys',
    'load_gen_pop_frame', 'gen_pop_window', 'genpop_subject_benchmark',
    'is_total_universe_key', 'read_profile_head', 'prior_total_universe',
    'guard_applies', 'size_anchors', 'anchors_for_draft', 'benchmark_verdict', 'target_sample',
    'apply_to_spec', 'apply_to_frame', 'projected_us', 'sample_for_people',
]
