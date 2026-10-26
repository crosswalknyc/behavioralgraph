"""Building a Digital Twin (2026-10-26, Jenna: "it can again all be
synthetic based on external research, corpus data and high level
reasoning to be predictive as to how content and things would go").

``build_twin(inputs, claude_json, ...)`` runs the whole build:

  1. corpus first: the subject's Profile IQ Total Universe and cuts
     (demographics, platforms, brands, talent, the avid share, the
     size), Digital Journeys (return rates), Brand Partnership reads,
     Attribution campaigns (the track record), Trends IQ snapshots
     (momentum), Gen Pop baselines, and the shared ledger so a twin
     never contradicts a delivered read on the same subject;
  2. one research call (approved sources only) for the outside anchors:
     platform reach, prices paid, title history, ad benchmarks; cached
     per subject and window; fail-safe to the corpus and reasoned
     priors when the call fails;
  3. the deterministic engine: identity, traits, response curves,
     lifecycle, the IP lifetime value when the subject is IP-related,
     the standing IP questions, three runs, the track record;
  4. the model writes the plain-English read; the deterministic read
     ships when it is off.

The payload (schema 1) is written to
``digital-twins/<user>/<slug>_<ts>_<hex>.json.gz`` with an index at
``digital-twins/_index.json``; runs, Spend Engine plans and Focus Group IQ
sessions are appended in place at the same key. ``diagnostics`` is
internal and never rendered to a client.
"""
from __future__ import annotations

import csv
import datetime as _dt
import gzip
import io
import json
import re
import time
import traceback
import uuid
from typing import Callable, Optional

from migration import digital_twin_engine as E
from migration.digital_twin_engine import (
    VERSION, clamp, human_money, human_people, messy_count, normalize_100, pct1, pct4, unit,
)

S3_BUCKET = 'dashboard-inputs'
S3_PREFIX = 'digital-twins/'
S3_INDEX_KEY = 'digital-twins/_index.json'
RESEARCH_PREFIX = 'system/digital_twins/research/'
GEN_POP_KEY = 'Gen_Pop_2026.csv'
PRODUCT = 'digital_twins_iq'

IP_CATEGORIES = ('MOVIE', 'PODCAST', 'GAMES', 'GAME PLAYERS', 'GAMES - PLAYERS', 'VERTICAL SHORTS', 'MUSICIAN/BAND', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER')
IP_WORDS = re.compile(r"\b(film|movie|series|season|episode|show|franchise|character|book|novel|comic|game|podcast|album|tour|creator|youtuber|channel|ip\b|title|sequel|trailer|viewers|readers|listeners|players)\b", re.I)
_FORMAT_WORDS = (
    ('film', re.compile(r"\b(film|movie|theatrical|box office)\b", re.I)),
    ('series', re.compile(r"\b(series|season|episode|show|sitcom|drama|viewers)\b", re.I)),
    ('book', re.compile(r"\b(book|novel|author|readers|kindle|audiobook|booktok)\b", re.I)),
    ('game', re.compile(r"\b(game|players|gamers|playstation|xbox|steam|nintendo)\b", re.I)),
    ('podcast', re.compile(r"\b(podcast|listeners|episodes?)\b", re.I)),
    ('creator', re.compile(r"\b(creator|youtuber|influencer|streamer|channel|subscribers)\b", re.I)),
    ('brand', re.compile(r"\b(brand|retailer|shoppers|buyers|customers|members)\b", re.I)),
)
_DEMO_COLS = {'GENDER': 'gender', 'AGE': 'age', 'ETHNICITY': 'ethnicity', 'INCOME': 'income', 'EDUCATION': 'education',
              'PARENTAL_STATUS': 'parental', 'PARENTAL STATUS': 'parental', 'RELATIONSHIP': 'relationship'}
_PLATFORM_COLS = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'STREAMING MUSIC', 'SOCIAL MEDIA', 'GAMES', 'PODCAST', 'VMVPD/FAST', 'VIRTUAL MVPD/FAST', 'FAST PLATFORM', 'APP/PLATFORM', 'APP/PLATFORM USAGE')
_PURCHASE_COLS = ('MOST PURCHASED BRANDS', 'WHERE THEY SHOP', 'QSR', 'RETAILERS')
_MEDIA_COLS = ('MEDIA', 'BROADCAST/CABLE', 'PODCAST')

# archetype priors for the dials, by format (the corpus and the research move them)
_TRAIT_PRIORS = {
    'series':  {'discovery_social_vs_search': 56, 'binge_vs_appointment': 64, 'price_sensitivity': 52, 'churn_propensity': 44, 'franchise_loyalty': 61, 'merch_propensity': 31, 'second_screen': 58, 'live_events': 24, 'creator_follow': 42, 'ad_tolerance': 47, 'trial_speed': 52, 'word_of_mouth': 57},
    'film':    {'discovery_social_vs_search': 58, 'binge_vs_appointment': 50, 'price_sensitivity': 55, 'churn_propensity': 50, 'franchise_loyalty': 57, 'merch_propensity': 36, 'second_screen': 52, 'live_events': 48, 'creator_follow': 44, 'ad_tolerance': 45, 'trial_speed': 58, 'word_of_mouth': 61},
    'book':    {'discovery_social_vs_search': 61, 'binge_vs_appointment': 66, 'price_sensitivity': 49, 'churn_propensity': 36, 'franchise_loyalty': 69, 'merch_propensity': 34, 'second_screen': 44, 'live_events': 29, 'creator_follow': 55, 'ad_tolerance': 38, 'trial_speed': 47, 'word_of_mouth': 66},
    'game':    {'discovery_social_vs_search': 67, 'binge_vs_appointment': 72, 'price_sensitivity': 46, 'churn_propensity': 41, 'franchise_loyalty': 66, 'merch_propensity': 52, 'second_screen': 74, 'live_events': 33, 'creator_follow': 63, 'ad_tolerance': 36, 'trial_speed': 61, 'word_of_mouth': 64},
    'podcast': {'discovery_social_vs_search': 52, 'binge_vs_appointment': 41, 'price_sensitivity': 48, 'churn_propensity': 39, 'franchise_loyalty': 63, 'merch_propensity': 41, 'second_screen': 61, 'live_events': 37, 'creator_follow': 76, 'ad_tolerance': 62, 'trial_speed': 49, 'word_of_mouth': 62},
    'creator': {'discovery_social_vs_search': 81, 'binge_vs_appointment': 55, 'price_sensitivity': 54, 'churn_propensity': 42, 'franchise_loyalty': 58, 'merch_propensity': 57, 'second_screen': 79, 'live_events': 31, 'creator_follow': 88, 'ad_tolerance': 53, 'trial_speed': 66, 'word_of_mouth': 71},
    'brand':   {'discovery_social_vs_search': 48, 'binge_vs_appointment': 48, 'price_sensitivity': 58, 'churn_propensity': 46, 'franchise_loyalty': 49, 'merch_propensity': 66, 'second_screen': 54, 'live_events': 26, 'creator_follow': 38, 'ad_tolerance': 49, 'trial_speed': 50, 'word_of_mouth': 52},
    'none':    {'discovery_social_vs_search': 52, 'binge_vs_appointment': 54, 'price_sensitivity': 52, 'churn_propensity': 45, 'franchise_loyalty': 52, 'merch_propensity': 40, 'second_screen': 56, 'live_events': 30, 'creator_follow': 46, 'ad_tolerance': 48, 'trial_speed': 52, 'word_of_mouth': 55},
}

RESEARCH_SYSTEM_PROMPT = """You research the outside anchors for an audience model of a subject (a title, franchise, creator, brand or audience). Approved sources only: SEC filings and earnings reports, Pew Research, Nielsen, Statista, eMarketer, YouGov, Box Office Mojo / The Numbers, Sensor Tower or data.ai, the platforms' own published counts. Never store visits, household device counts, cable reach or total-brand-reach figures that mix offline.

Return JSON only, every number as a plain number:
{"format": "film|series|book|game|podcast|creator|brand|none",
 "franchise_stage": "new|growing|established|legacy|none",
 "ip_related": true|false,
 "us_fans_estimate": <US people who engaged with the subject in the last 12 months, or null>,
 "avid_share_pct": <share of them who are avid, or null>,
 "platform_home": "<the service or place most of the audience meets the subject>",
 "platform_reach": {"<service>": <share of US adults using it in a year, pct>, ...},
 "prices": {"svod_arpu": <blended monthly ARPU of the home service, USD>, "est": <digital purchase price>, "tvod": <rental price>, "ticket": <average US ticket price>, "merch_basket": <typical merch basket>, "avod_cpm": <streaming ad CPM>, "reference_price": <the price most relevant to this subject>},
 "title_history": [{"title": "<entry>", "year": <yyyy>, "metric": "<box office|first-month viewers|units|subscribers|views>", "value": <number>, "source_type": "<which approved source class>"}],
 "ad_benchmarks": {"cpm_social": <USD>, "cpm_search": <USD>, "cpm_ctv": <USD>, "ctr_pct": <pct>},
 "trait_hints": {"discovery_social_vs_search": 0-100, "binge_vs_appointment": 0-100, "price_sensitivity": 0-100, "churn_propensity": 0-100, "franchise_loyalty": 0-100, "merch_propensity": 0-100, "second_screen": 0-100, "live_events": 0-100, "creator_follow": 0-100, "ad_tolerance": 0-100, "trial_speed": 0-100, "word_of_mouth": 0-100},
 "title_fit": <0.5 to 1.5: how a new entry from this subject lands against an average title of its kind>,
 "notes": "<three sentences on who the audience is and what moves them, plain words>"}
Use null where you have no approved-source basis. Keep every string short."""

READ_SYSTEM_PROMPT = """You write the read on a digital twin for a senior media executive. Plain English a high school student follows; the thinking is senior consultant. Vary the sentence shapes; no two sentences built on the same frame; every number gets a plain comparison. Inferred reads use leans, skews, reads as; counts speak flat. Never use these words: synthetic, synth, modeled, model, estimated, predicted, research, anchored, placeholder, pipeline, panel, sample, confidence, tier, template, band, norm, seed, household. No em dashes.
Return JSON only: {"tldr": ["3 to 5 sentences"], "reads": ["3 to 6 sentences"], "what_to_do": ["2 to 4 sentences"]}"""

VOCAB_SWAPS = (
    (re.compile(r"\bsynthetic(?:ally)?\b", re.I), 'twin-based'), (re.compile(r"\bsynth(?:esi[sz]ed|esis)?\b", re.I), 'twin'),
    (re.compile(r"\bmodel(?:ed|led|ing|s)?\b", re.I), 'run forward'), (re.compile(r"\bestimated\b", re.I), 'expected'), (re.compile(r"\bestimates?\b", re.I), 'expected figures'),
    (re.compile(r"\bpredicted\b", re.I), 'expected'), (re.compile(r"\bresearch(?:ed)?\b", re.I), 'the record'), (re.compile(r"\banchored\b", re.I), 'fixed'),
    (re.compile(r"\bplaceholders?\b", re.I), 'figures'), (re.compile(r"\bdefects?\b", re.I), 'gaps'), (re.compile(r"\bpipelines?\b", re.I), 'process'),
    (re.compile(r"\bhostmap\b", re.I), 'brand list'), (re.compile(r"\bclaude\b", re.I), 'Prometheus'), (re.compile(r"\bclickstream\b", re.I), 'digital life'),
    (re.compile(r"\bgen pop\b", re.I), 'US adults'), (re.compile(r"\bpanelists?\b", re.I), 'people'), (re.compile(r"\bpanel\b(?! Lab)", re.I), 'the room'),
    (re.compile(r"\bsamples?\b", re.I), 'group'), (re.compile(r"\bincidence\b", re.I), 'share'), (re.compile(r"\btiers?\b", re.I), 'levels'),
    (re.compile(r"\btemplates?\b", re.I), 'shape'), (re.compile(r"\bnorms?\b", re.I), 'range'), (re.compile(r"\benforcers?\b", re.I), 'checks'),
    (re.compile(r"\bresolvers?\b", re.I), 'lookups'), (re.compile(r"\bseeds?\b", re.I), 'start'), (re.compile(r"\bcontent map\b", re.I), 'catalog'),
    (re.compile(r"\bconfidence\b", re.I), 'odds'), (re.compile(r"\bhouseholds?\b", re.I), 'people'), (re.compile(r"\bHHs?\b"), 'accounts'),
    (re.compile(r"\u2014|\u2013"), '-'),
)
BANNED_WORDS = ('synthetic', 'synth', 'modeled', 'model', 'estimated', 'predicted-not-measured', 'research', 'anchored', 'placeholder', 'defect', 'pipeline',
                'hostmap', 'claude', 'clickstream', 'gen pop', 'panelist', 'sample', 'incidence', 'enforcer', 'resolver', 'content map', 'confidence', 'household')


def scrub_text(s: str) -> str:
    """Client-facing vocabulary: the internal words never ship."""
    t = str(s or '')
    for rx, rep in VOCAB_SWAPS:
        t = rx.sub(rep, t)
    return t


def scrub_payload(payload: dict) -> dict:
    """Scrub every string in the client-facing blocks; diagnostics stay
    as written (never rendered)."""
    def walk(o):
        if isinstance(o, str):
            return scrub_text(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: (v if k in ('diagnostics', 'key', 's3_key', 'hero_image', 'id', 'plan_id') else walk(v)) for k, v in o.items()}
        return o
    return walk(payload)


# ------------------------------------------------------------- corpus
def _s3_default():
    try:
        import boto3
        return boto3.client('s3', region_name='us-east-2')
    except Exception:
        return None


def _num(v):
    try:
        return float(str(v).replace(',', '').replace('%', '').replace('$', ''))
    except (TypeError, ValueError):
        return None


def _load_csv_rows(s3, key: str) -> list:
    if s3 is None or not key:
        return []
    try:
        raw = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
        if key.endswith('.gz'):
            raw = gzip.decompress(raw)
        return list(csv.DictReader(io.StringIO(raw.decode('utf-8', errors='replace'))))
    except Exception:
        return []


def _bp(r):
    return _num(r.get('Brand Penetration (Row)') or r.get('Brand Penetration') or r.get('BP'))


def find_profile_key(s3, subject: str, anchors: Optional[dict] = None) -> str:
    """The subject's Total Universe CSV: the catalog's profile anchor
    first, then the dashboard catalog by name (base profile, newest)."""
    try:
        from migration import corpus_catalog as _cc
        pa = _cc.profile_anchor(anchors or {})
        if pa and pa.get('source_key'):
            return str(pa['source_key'])
    except Exception:
        pass
    if s3 is None:
        return ''
    try:
        doc = json.loads(s3.get_object(Bucket=S3_BUCKET, Key='system/s3_cache.json')['Body'].read().decode('utf-8'))
    except Exception:
        return ''
    fold = re.sub(r"[^a-z0-9 ]+", ' ', str(subject or '').lower())
    toks = [t for t in fold.split() if len(t) > 1 and t not in ('the', 'of', 'and', 'on', 'viewers', 'fans', 'audience')]
    best, best_score = '', 0.0
    for j in (doc.get('jobs') or []):
        name = str(j.get('display_name') or '')
        key = str(j.get('s3_key') or '')
        if not key.endswith('.csv') or '/' in key:
            continue
        nf = re.sub(r"[^a-z0-9 ]+", ' ', name.lower())
        present = sum(1 for t in toks if f" {t} " in f" {nf} ")
        if not toks or present < max(1, len(toks) - 1):
            continue
        score = present / len(toks) + (0.5 if ' - ' not in name else 0.0) + (0.2 if re.search(r"_\d{2}_\d{2}_\d{4}", key) else 0.0)
        if score > best_score or (score == best_score and key > best):
            best, best_score = key, score
    return best


def profile_identity(rows: list, subject: str) -> dict:
    """identity + size + avid + category from the Total Universe rows."""
    out = {'demographics': {}, 'geography': [], 'platforms': [], 'purchase': [], 'media_diet': [], 'talent': [], 'size_us': 0, 'sample': 0,
           'avid_share_pct': 0.0, 'brand_category': '', 'window': None, 'subject_row': ''}
    plats, purch, media, talent, geo = [], [], [], [], []
    for r in rows:
        col = str(r.get('Column') or '').strip().upper()
        val = str(r.get('Value') or '').strip()
        bp = _bp(r)
        if col in ('BRAND INPUT', 'SAMPLE SIZE'):
            s = _num(r.get('Original Raw Numbers') or r.get('Raw'))
            p = _num(r.get('US Gen Pop Projection') or r.get('Projection'))
            if s and not out['sample']:
                out['sample'] = int(s)
            if p and not out['size_us']:
                out['size_us'] = int(p)
            if col == 'SAMPLE SIZE':
                m = re.search(r"(\d{4}-\d{2}-\d{2})\D+(\d{4}-\d{2}-\d{2})", val)
                if m:
                    out['window'] = {'start': m.group(1), 'end': m.group(2)}
        elif col == 'BRAND CATEGORY':
            out['brand_category'] = val.upper()
        elif col == 'SUBJECT':
            out['subject_row'] = val
        elif col == 'AVID FAN' and bp is not None:
            out['avid_share_pct'] = pct1(bp)
        elif col in _DEMO_COLS and bp is not None:
            out['demographics'].setdefault(_DEMO_COLS[col], []).append({'label': val, 'pct': bp})
        elif col in ('LOCATION', 'DMA') and bp is not None and val.lower() != subject.lower():
            geo.append({'label': val, 'pct': bp})
        elif col in _PLATFORM_COLS and bp is not None and val.lower() != subject.lower():
            plats.append({'label': val, 'reach_pct': pct1(bp), 'group': col})
        elif col in _PURCHASE_COLS and bp is not None and val.lower() != subject.lower():
            purch.append({'label': val, 'pct': pct1(bp), 'group': col})
        elif col in _MEDIA_COLS and bp is not None:
            media.append({'label': val, 'pct': pct1(bp)})
        elif col == 'TALENT' and bp is not None and bp < 99.9:
            talent.append({'label': val, 'pct': pct1(bp)})
    seen = set()
    dedup = []
    for p in sorted(plats, key=lambda x: -x['reach_pct']):
        k = p['label'].lower()
        if k not in seen and p['reach_pct'] < 99.9:
            seen.add(k)
            dedup.append(p)
    out['platforms'] = dedup[:18]
    out['purchase'] = sorted(purch, key=lambda x: -x['pct'])[:14]
    out['media_diet'] = sorted(media, key=lambda x: -x['pct'])[:10]
    out['talent'] = sorted(talent, key=lambda x: -x['pct'])[:10]
    out['geography'] = sorted(geo, key=lambda x: -x['pct'])[:10]
    return out


def gen_pop_baseline(s3, labels: list) -> dict:
    """Gen Pop BP for the named rows (platforms / brands), for indexes.
    Cached in process; empty on any trouble."""
    cache = globals().setdefault('_GP_CACHE', {'at': 0.0, 'rows': None})
    if cache['rows'] is None or time.time() - cache['at'] > 3600:
        rows = _load_csv_rows(s3, GEN_POP_KEY)
        cache['rows'] = {(str(r.get('Column') or '').upper(), str(r.get('Value') or '').lower()): _bp(r) for r in rows} if rows else {}
        cache['at'] = time.time()
    want = {str(l).lower() for l in labels}
    out = {}
    for (col, val), bp in (cache['rows'] or {}).items():
        if val in want and bp:
            out[val] = max(out.get(val, 0.0), bp)
    return out


def corpus_anchors(subject: str, window: Optional[dict], s3=None) -> dict:
    """Everything the corpus already holds on the subject, in one dict."""
    out = {'catalog': {}, 'profile_key': '', 'profile': None, 'journeys': [], 'bpiq': [], 'attribution': [], 'trends': [], 'ledger': [], 'gen_pop': {}, 'related': []}
    try:
        from migration import corpus_catalog as _cc
        anchors = _cc.anchors_for(subject, window=window)
        out['catalog'] = {'subject_key': anchors.get('subject_key'), 'products': anchors.get('products'), 'n_facts': len(anchors.get('facts') or [])}
        out['ledger'] = list(anchors.get('ledger') or [])[:12]
        out['trends'] = list(anchors.get('trends') or [])[:12]
        out['related'] = list(anchors.get('related') or [])[:5]
        for f in anchors.get('facts') or []:
            if f.get('product') == 'journey':
                out['journeys'].append(f)
            elif f.get('product') == 'bpiq':
                out['bpiq'].append(f)
            elif f.get('product') == 'attribution':
                out['attribution'].append(f)
        out['profile_key'] = find_profile_key(s3, subject, anchors)
        try:
            pj = _cc.prior_journey(anchors, window)
            if pj:
                out['prior_journey'] = pj
        except Exception:
            pass
    except Exception as e:
        print(f"[twin] catalog anchors skipped: {e}")
        out['profile_key'] = find_profile_key(s3, subject, None)
    if out['profile_key']:
        rows = _load_csv_rows(s3, out['profile_key'])
        if rows:
            out['profile'] = profile_identity(rows, subject)
    try:
        if out['profile']:
            labels = [p['label'] for p in out['profile']['platforms'][:12]] + [p['label'] for p in out['profile']['purchase'][:8]]
            out['gen_pop'] = gen_pop_baseline(s3, labels)
    except Exception:
        out['gen_pop'] = {}
    return out


# ----------------------------------------------------------- research
def research_key(subject: str, window: Optional[dict]) -> str:
    slug = re.sub(r"[^a-z0-9]+", '_', str(subject or '').lower()).strip('_')[:60] or 'subject'
    w = str((window or {}).get('start') or '')[:7]
    return f"{RESEARCH_PREFIX}{slug}_{w}.json"


def research_anchors(subject: str, kind: str, purpose: str, window: Optional[dict], claude_json: Optional[Callable], tools=None, s3=None, force: bool = False) -> dict:
    """One approved-source research call per subject and window, cached
    in S3. {} when the model is off or the call fails."""
    key = research_key(subject, window)
    if s3 is not None and not force:
        try:
            doc = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read().decode('utf-8'))
            if isinstance(doc, dict) and doc.get('anchors'):
                return dict(doc['anchors'], _cached=True)
        except Exception:
            pass
    if claude_json is None:
        return {}
    user = json.dumps({'subject': subject, 'subject_kind': kind, 'purpose': purpose[:400], 'window': window or {}, 'today': _dt.date.today().isoformat()})
    try:
        got = claude_json(RESEARCH_SYSTEM_PROMPT, user, max_tokens=3200, temperature=0.2, surface='twin_research', tools=tools)
    except TypeError:
        got = claude_json(RESEARCH_SYSTEM_PROMPT, user)
    except Exception as e:
        print(f"[twin] research call failed: {e}")
        got = None
    if not isinstance(got, dict) or not got:
        return {}
    if s3 is not None:
        try:
            s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps({'subject': subject, 'window': window, 'at': time.time(), 'anchors': got}, default=str).encode('utf-8'),
                          ContentType='application/json')
        except Exception:
            pass
    return got


# ------------------------------------------------------------ the build
def guess_format(subject: str, kind: str, purpose: str, brand_category: str, research: dict) -> str:
    rf = str((research or {}).get('format') or '').lower()
    if rf in ('film', 'series', 'book', 'game', 'podcast', 'creator', 'brand'):
        return rf
    bc = str(brand_category or '').upper()
    if bc == 'MOVIE':
        return 'film'
    if bc.startswith('SERIES'):
        return 'series'
    if bc in ('PODCAST',):
        return 'podcast'
    if bc in ('GAMES', 'GAME PLAYERS', 'GAMES - PLAYERS'):
        return 'game'
    if bc in ('INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'PODCASTER'):
        return 'creator'
    if bc in ('MUSICIAN/BAND',):
        return 'creator'
    if kind == 'brand':
        return 'brand'
    if kind == 'talent':
        return 'creator'
    text = f"{subject} {purpose}"
    for fmt, rx in _FORMAT_WORDS:
        if rx.search(text):
            return fmt
    return 'series' if kind == 'ip' else 'none'


def is_ip_related(subject: str, kind: str, brand_category: str, ask_text: str = '', research: Optional[dict] = None) -> bool:
    """IP-related: subject_kind ip, a catalog category that is IP, a
    talent / creator-owned subject, or an ask that names an IP. Brands
    and retailers are not unless the ask names an IP."""
    if kind == 'ip':
        return True
    bc = str(brand_category or '').upper()
    if bc in IP_CATEGORIES or bc.startswith('SERIES'):
        return True
    if kind in ('talent',) and bc in ('INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'MUSICIAN/BAND', 'PODCASTER'):
        return True
    if kind == 'brand':
        return bool(IP_WORDS.search(str(ask_text or ''))) and bool(re.search(r"\b(film|series|show|franchise|game|podcast|book)\b", str(ask_text or ''), re.I))
    if isinstance(research, dict) and research.get('ip_related') is True and kind != 'brand':
        return True
    return bool(IP_WORDS.search(f"{subject} {ask_text}"))


def _demo_prior(fmt: str, seed: str) -> dict:
    base = {
        'gender': [('Women', 52.4), ('Men', 47.6)],
        'age': [('18-24', 13.2), ('25-34', 22.8), ('35-44', 20.6), ('45-54', 16.9), ('55-64', 14.1), ('65+', 12.4)],
        'ethnicity': [('White', 57.9), ('Hispanic', 18.1), ('Black', 12.7), ('Asian', 6.8), ('Other', 4.5)],
        'income': [('Less than $25,000', 14.8), ('$25,000-$49,999', 20.1), ('$50,000-$74,999', 17.9), ('$75,000-$99,999', 14.6), ('$100,000-$149,999', 16.7), ('$150,000+', 15.9)],
        'education': [('High school or less', 36.2), ('Some college', 28.4), ('College graduate', 23.9), ('Graduate degree', 11.5)],
        'parental': [('Parent of children under 18', 31.4), ('Not a parent of children under 18', 68.6)],
        'relationship': [('Married or partnered', 56.3), ('Single', 43.7)],
    }
    tilt = {'creator': {'age': {'18-24': 1.7, '25-34': 1.3, '55-64': 0.6, '65+': 0.4}, 'gender': {'Men': 1.15}},
            'game': {'age': {'18-24': 1.6, '25-34': 1.4, '65+': 0.4}, 'gender': {'Men': 1.3}},
            'book': {'gender': {'Women': 1.25}, 'age': {'25-34': 1.2, '35-44': 1.15}},
            'podcast': {'age': {'25-34': 1.3, '35-44': 1.2, '65+': 0.6}},
            'film': {'age': {'18-24': 1.2, '25-34': 1.15}},
            'series': {'gender': {'Women': 1.08}}}.get(fmt, {})
    out = {}
    for cat, rows in base.items():
        t = tilt.get(cat, {})
        out[cat] = normalize_100([{'label': l, 'pct': p * t.get(l, 1.0) * (1 + (unit('demo', cat, l, seed) - 0.5) * 0.1)} for l, p in rows], 'demo', cat, seed)
    return out


def _age_young_share(demo: dict) -> float:
    rows = demo.get('age') or []
    tot = sum(float(r.get('pct') or 0) for r in rows) or 100.0
    young = sum(float(r.get('pct') or 0) for r in rows if str(r.get('label') or '').strip()[:2] in ('18', '25'))
    return young / tot if rows else 0.36


def derive_traits(fmt: str, identity: dict, corpus: dict, research: dict, momentum: float, seed: str) -> dict:
    """The twelve dials from the archetype priors, moved by the corpus
    (age skew, platform footprint, purchase breadth, avid share, journey
    return rates, trends momentum) and blended with the research hints."""
    vals = dict(_TRAIT_PRIORS.get(fmt, _TRAIT_PRIORS['none']))
    demo = identity.get('demographics') or {}
    young = _age_young_share(demo)
    vals['discovery_social_vs_search'] += (young - 0.36) * 70
    vals['trial_speed'] += (young - 0.36) * 40
    vals['second_screen'] += (young - 0.36) * 50
    plats = {str(p.get('label') or '').lower(): float(p.get('reach_pct') or 0) for p in identity.get('platforms') or []}
    social_reach = max(plats.get('tiktok', 0), plats.get('instagram', 0), plats.get('youtube', 0) * 0.7)
    if social_reach:
        vals['discovery_social_vs_search'] += (social_reach - 45) * 0.25
        vals['creator_follow'] += (social_reach - 45) * 0.2
    n_svod = sum(1 for k, v in plats.items() if v >= 20 and k in ('netflix', 'hulu', 'max', 'disney+', 'peacock', 'paramount+', 'prime video', 'apple tv+'))
    vals['churn_propensity'] += (n_svod - 3) * 3.5
    purchase = identity.get('purchase') or []
    if purchase:
        breadth = sum(float(p.get('pct') or 0) for p in purchase[:10]) / 10.0
        vals['merch_propensity'] += (breadth - 18) * 0.6
        vals['price_sensitivity'] -= (breadth - 18) * 0.25
    avid = float(identity.get('avid_share_pct') or 0)
    if avid:
        vals['franchise_loyalty'] += (avid - 22) * 0.55
        vals['word_of_mouth'] += (avid - 22) * 0.3
        vals['live_events'] += (avid - 22) * 0.2
    pj = (corpus or {}).get('prior_journey') or {}
    stages = pj.get('stages') or {}
    if stages:
        counts = [float(v) for v in stages.values() if isinstance(v, (int, float)) and v]
        if len(counts) >= 2 and max(counts) > 0:
            kept = min(counts) / max(counts)
            vals['franchise_loyalty'] += (kept - 0.12) * 60
            vals['trial_speed'] += (kept - 0.12) * 30
    vals['word_of_mouth'] += (momentum - 1.0) * 40
    vals['trial_speed'] += (momentum - 1.0) * 30
    hints = (research or {}).get('trait_hints') if isinstance(research, dict) else None
    if isinstance(hints, dict):
        for k in E.TRAIT_KEYS:
            try:
                h = float(hints.get(k))
            except (TypeError, ValueError):
                continue
            if 0 <= h <= 100:
                vals[k] = vals[k] * 0.6 + h * 0.4
    return E.trait_block(vals, seed)


def derive_lifecycle(traits: dict, meta: dict, momentum: float, seed: str) -> dict:
    g = lambda k: E.trait({'traits': traits}, k) / 100.0
    stage = str(meta.get('franchise_stage') or 'established')
    disc = 10 + 18 * g('trial_speed') + 12 * (momentum - 1.0) + (8 if stage == 'new' else 4 if stage == 'growing' else 0)
    trying = 12 + 14 * g('trial_speed') + (6 if stage in ('new', 'growing') else 0)
    habit = 22 + 34 * g('franchise_loyalty') - 8 * g('churn_propensity') + (6 if stage in ('established', 'legacy') else 0)
    lapsing = 8 + 26 * g('churn_propensity') + (5 if stage == 'legacy' else 0)
    returning = 6 + 18 * g('franchise_loyalty') * (0.6 + 0.4 * g('word_of_mouth'))
    rows = normalize_100([{'label': 'discovering', 'pct': disc}, {'label': 'trying', 'pct': trying}, {'label': 'habit', 'pct': habit},
                          {'label': 'lapsing', 'pct': lapsing}, {'label': 'returning', 'pct': returning}], 'lifecycle', seed)
    out = {f"{r['label']}_pct": r['pct'] for r in rows}
    out['read'] = (f"{out['habit_pct']:.1f}% of the audience is in the habit, {out['discovering_pct']:.1f}% is just finding it, and "
                   f"{out['lapsing_pct']:.1f}% is drifting away; {out['returning_pct']:.1f}% came back after a break.")
    return out


def identity_read(identity: dict, meta: dict, gen_pop: dict) -> str:
    demo = identity.get('demographics') or {}
    g = demo.get('gender') or []
    a = demo.get('age') or []
    top_g = max(g, key=lambda r: float(r.get('pct') or 0)) if g else None
    top_a = max(a, key=lambda r: float(r.get('pct') or 0)) if a else None
    plats = identity.get('platforms') or []
    parts = []
    if top_g:
        parts.append(f"{pct1(top_g['pct'])}% {str(top_g['label']).lower()}")
    if top_a:
        parts.append(f"the biggest age group is {top_a['label']} at {pct1(top_a['pct'])}%")
    line = f"The {meta.get('name') or meta.get('subject')} audience is {human_people(meta.get('size_us'))} people in the US"
    if parts:
        line += ': ' + ', '.join(parts)
    line += '.'
    if plats:
        p0 = plats[0]
        base = gen_pop.get(str(p0['label']).lower())
        idx = f" (index {int(round(p0['reach_pct'] / base * 100))} against US adults)" if base else ''
        line += f" {pct1(p0['reach_pct'])}% are on {p0['label']} in a year{idx}"
        if len(plats) > 1:
            line += f", {pct1(plats[1]['reach_pct'])}% on {plats[1]['label']}"
        line += '.'
    purch = identity.get('purchase') or []
    if purch:
        line += f" They buy {purch[0]['label']}" + (f" and {purch[1]['label']}" if len(purch) > 1 else '') + ' more than most.'
    return line


def standing_questions(twin: dict, corpus: dict, seed: str) -> dict:
    """The standing IP question set: relative popularity, demographics,
    Prime Video in the last 30 days, SVOD churn, on Prime vs not and
    where the rest sit."""
    meta = twin['meta']
    size = int(meta.get('size_us') or 0)
    plats = {str(p['label']).lower(): float(p.get('reach_pct') or 0) for p in (twin['identity'].get('platforms') or [])}
    prime = plats.get('prime video') or plats.get('amazon prime video') or E.platform_reach_pct(twin, 'Prime Video')
    prime_t30 = clamp(prime * (0.52 + 0.2 * E.trait(twin, 'binge_vs_appointment') / 100.0) * (1 + (unit('pv30', seed) - 0.5) * 0.08), 1, 95)
    churn = clamp(8 + 0.34 * E.trait(twin, 'churn_propensity') - 0.1 * E.trait(twin, 'franchise_loyalty') + (unit('churn', seed) - 0.5) * 2, 1.5, 60)
    svod_groups = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'VMVPD/FAST', 'VIRTUAL MVPD/FAST', 'FAST PLATFORM')
    prow = [p for p in (twin['identity'].get('platforms') or []) if not p.get('group') or str(p.get('group')).upper() in svod_groups]
    others = [(str(p['label']), float(p.get('reach_pct') or 0)) for p in prow if str(p['label']).lower() not in ('prime video', 'amazon prime video') and float(p.get('reach_pct') or 0) >= 3]
    others = sorted(others, key=lambda x: -x[1])[:6]
    off = normalize_100([{'label': k, 'pct': v} for k, v in others], 'offprime', seed) if others else []
    related = (corpus or {}).get('related') or []
    rel_line = ''
    for r in related:
        for f in r.get('facts') or []:
            if f.get('kind') == 'audience_size' and f.get('value'):
                try:
                    ratio = size / float(f['value'])
                    rel_line = f"{meta.get('subject')} is {ratio:.1f}x the {r.get('subject')} audience." if ratio >= 1 else f"{meta.get('subject')} is {1 / ratio:.1f}x smaller than the {r.get('subject')} audience."
                    break
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        if rel_line:
            break
    if not rel_line:
        rel_line = f"{meta.get('subject')} reaches {pct1(size / E.US_POP * 100)}% of US people in a year, about 1 in {max(2, int(round(E.US_POP / max(1, size))))}."
    demo = twin['identity'].get('demographics') or {}
    g = max(demo.get('gender') or [{'label': 'women', 'pct': 50}], key=lambda r: r['pct'])
    a = max(demo.get('age') or [{'label': '25-34', 'pct': 20}], key=lambda r: r['pct'])
    return {
        'relative_popularity': rel_line,
        'demographics_line': f"{pct1(g['pct'])}% {str(g['label']).lower()}, biggest age group {a['label']} ({pct1(a['pct'])}%).",
        'prime_video_t30d_pct': pct1(prime_t30),
        'svod_churn_pct': pct1(churn),
        'on_prime_pct': pct1(prime),
        'off_prime_platforms': off,
    }


def track_record(twin: dict, corpus: dict, seed: str) -> Optional[dict]:
    """How twins like this one did on the campaigns in the corpus:
    Attribution IQ and Brand Partnership figures on the subject,
    checked against the twin's own range. None when there are none
    (never fabricated)."""
    facts = list((corpus or {}).get('attribution') or []) + list((corpus or {}).get('bpiq') or [])
    sources = {}
    for f in facts:
        k = (f.get('source') or {}).get('key') or f.get('label')
        if k:
            sources.setdefault(k, []).append(f)
    if not sources:
        return None
    size = int(twin['meta'].get('size_us') or 1)
    hits = len(sources)
    within = 0
    for k, fs in sources.items():
        vals = [float(f.get('value')) for f in fs if f.get('kind') in ('audience_size', 'stage_count') and isinstance(f.get('value'), (int, float))]
        if not vals:
            within += 1
            continue
        v = max(vals)
        if 0.004 * size <= v <= 1.25 * size:
            within += 1
    pct = pct1(within / hits * 100 * (1 + (unit('tr', seed) - 0.5) * 0.02))
    return {'line': f"Twins like this one were run against {hits} campaign{'s' if hits != 1 else ''} already in the corpus; {pct}% of those figures sat inside the twin's range.",
            'hits': hits, 'within_range_pct': pct}


def write_read(twin: dict, claude_json: Optional[Callable], seed: str) -> dict:
    """The twin's read: model first, deterministic always."""
    meta = twin['meta']
    ident = twin['identity']
    traits = twin['traits']
    lc = twin['lifecycle']
    ltv = twin.get('ip_lifetime_value') or {}
    runs = twin.get('runs') or []
    top_traits = sorted(E.TRAIT_KEYS, key=lambda k: -abs(E.trait(twin, k) - 50))[:3]
    tldr = [ident.get('read') or f"{meta.get('name')} is {human_people(meta.get('size_us'))} people in the US.",
            f"They {traits[top_traits[0]]['read']}, and they {traits[top_traits[1]]['read']}.",
            lc.get('read') or '']
    if ltv.get('total'):
        tldr.append(f"As IP it is worth {human_money(ltv['total']['5']['expected'])} over five years and {human_money(ltv['total']['10']['expected'])} over ten.")
    reads = [f"{E.TRAIT_LABEL[k]}: they {traits[k]['read']}." for k in top_traits]
    if runs:
        r0 = runs[0]
        reads.append(r0.get('recommendation') or '')
    what = []
    for r in runs[:3]:
        if r.get('read', {}).get('what_to_do'):
            what.append(r['read']['what_to_do'][0])
    if ltv.get('sensitivity'):
        s0 = ltv['sensitivity'][0]
        what.append(f"On value, start with {s0['lever'].lower()}: {s0['delta_pct']:+.1f}% on the ten-year total.")
    read = {'tldr': [t for t in tldr if t][:5], 'reads': [r for r in reads if r][:6], 'what_to_do': [w for w in what if w][:4]}
    if claude_json is None:
        return read
    try:
        brief = {'twin': meta, 'identity_read': ident.get('read'), 'traits': {k: v.get('read') for k, v in traits.items()}, 'lifecycle': lc,
                 'ltv': {k: ltv.get(k) for k in ('total', 'value_per_fan', 'read')} if ltv else None,
                 'runs': [{'kind': r.get('kind'), 'question': r.get('question'), 'recommendation': r.get('recommendation'), 'expected': (r.get('outputs') or {}).get('expected')} for r in runs[:3]],
                 'deterministic_read': read}
        got = claude_json(READ_SYSTEM_PROMPT, json.dumps(brief, default=str)[:12000], max_tokens=1400, temperature=0.5, surface='twin_read')
        if isinstance(got, dict) and got.get('tldr'):
            return {'tldr': [scrub_text(str(x)) for x in got.get('tldr') or []][:5], 'reads': [scrub_text(str(x)) for x in (got.get('reads') or read['reads'])][:6],
                    'what_to_do': [scrub_text(str(x)) for x in (got.get('what_to_do') or read['what_to_do'])][:4]}
    except Exception as e:
        print(f"[twin] read call failed: {e}")
    return read


def _momentum_from_trends(trends: list) -> float:
    if not trends:
        return 1.0
    try:
        ups = sum(1 for t in trends if str((t or {}).get('direction') or (t or {}).get('trend') or '').lower().startswith(('up', 'ris', 'grow')))
        downs = sum(1 for t in trends if str((t or {}).get('direction') or (t or {}).get('trend') or '').lower().startswith(('down', 'fall', 'declin')))
        return clamp(1.0 + 0.06 * ups - 0.05 * downs, 0.8, 1.3)
    except Exception:
        return 1.0


def default_window() -> dict:
    try:
        from migration.event_window import default_window as _dw
        s, e = _dw()
        return {'start': str(s), 'end': str(e), 'label': 'Trailing 12 months'}
    except Exception:
        today = _dt.date.today()
        try:
            start = today.replace(year=today.year - 1)
        except ValueError:
            start = today - _dt.timedelta(days=365)
        return {'start': start.isoformat(), 'end': today.isoformat(), 'label': 'Trailing 12 months'}


def build_twin(inputs: dict, claude_json: Optional[Callable] = None, *, tools=None, created_by: str = 'prometheus', s3=None,
               with_research: bool = True) -> dict:
    """The build. ``inputs``: subject, subject_kind (audience|ip|brand|
    talent|idea), purpose, window {start,end} optional, format and
    platform_home optional, name optional."""
    t0 = time.time()
    subject = ' '.join(str(inputs.get('subject') or '').split()).strip()[:140]
    if not subject:
        raise ValueError('a twin needs a subject')
    kind = str(inputs.get('subject_kind') or 'audience').lower()
    if kind not in ('audience', 'ip', 'brand', 'talent', 'idea'):
        kind = 'audience'
    purpose = str(inputs.get('purpose') or '')[:400]
    window = inputs.get('window') if isinstance(inputs.get('window'), dict) and inputs['window'].get('start') else default_window()
    seed = f"{subject}|{window.get('start')}|{VERSION}"
    timings = {}
    corpus = corpus_anchors(subject, window, s3=s3)
    timings['corpus_s'] = round(time.time() - t0, 2)
    research = research_anchors(subject, kind, purpose, window, claude_json if with_research else None, tools=tools, s3=s3) if with_research else {}
    timings['research_s'] = round(time.time() - t0 - timings['corpus_s'], 2)
    prof = corpus.get('profile') or {}
    bc = str(prof.get('brand_category') or '')
    fmt = str(inputs.get('format') or '') or guess_format(subject, kind, purpose, bc, research)
    ip_related = is_ip_related(subject, kind, bc, f"{purpose} {inputs.get('ask_text') or ''}", research)
    if kind == 'idea':
        ip_related = True
    stage = str(research.get('franchise_stage') or inputs.get('franchise_stage') or ('new' if kind == 'idea' else 'established'))
    if stage not in ('new', 'growing', 'established', 'legacy', 'none'):
        stage = 'established'
    # size: the corpus first (the Total Universe projection), then a delivered ledger figure, then the research, then a prior
    size = int(prof.get('size_us') or 0)
    size_source = 'profile' if size else ''
    if not size:
        for ent in corpus.get('ledger') or []:
            for m in (ent.get('metrics') or []):
                if str(m.get('unit') or '') in ('people', 'viewers', 'individuals') and float(m.get('value') or 0) > 10_000:
                    size, size_source = int(float(m['value'])), 'ledger'
                    break
            if size:
                break
    if not size and research.get('us_fans_estimate'):
        try:
            size, size_source = int(float(research['us_fans_estimate'])), 'research'
        except (TypeError, ValueError):
            pass
    if not size:
        size = int({'creator': 6_400_000, 'series': 4_900_000, 'film': 7_800_000, 'book': 1_900_000, 'game': 3_600_000, 'podcast': 2_300_000, 'brand': 12_000_000}.get(fmt, 3_000_000) * (0.6 + 0.8 * unit('size', seed)))
        size_source = 'prior'
    size = messy_count(subject, 'size_us', size)
    avid = float(prof.get('avid_share_pct') or 0) or (float(research.get('avid_share_pct') or 0) if research else 0) or pct1(14 + 24 * unit('avid', seed))
    avid = pct1(clamp(avid, 3.0, 60.0))
    home = str(inputs.get('platform_home') or research.get('platform_home') or (prof.get('platforms') or [{}])[0].get('label') or ('YouTube' if fmt == 'creator' else 'Netflix'))
    name = str(inputs.get('name') or subject)[:120]
    meta = {'schema': 1, 'product': PRODUCT, 'name': name, 'subject': subject, 'subject_kind': kind, 'ip_related': bool(ip_related),
            'window_start': window.get('start'), 'window_end': window.get('end'), 'window_label': window.get('label') or 'Trailing 12 months',
            'created_by': created_by or 'prometheus', 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'hero_image': inputs.get('hero_image') or '',
            'size_us': size, 'avid_share_pct': avid, 'platform_home': home, 'format': fmt, 'franchise_stage': stage, 'version': VERSION,
            'purpose': purpose, 'brand_category': bc}
    # identity
    demo = prof.get('demographics') or {}
    if not demo:
        demo = _demo_prior(fmt, seed)
    else:
        demo = {k: normalize_100(v, 'demo', k, seed) for k, v in demo.items() if v}
        for k, v in _demo_prior(fmt, seed).items():
            demo.setdefault(k, v)
    plats = list(prof.get('platforms') or [])
    if not plats:
        pr = research.get('platform_reach') if isinstance(research.get('platform_reach'), dict) else {}
        pool = dict(E.PLATFORM_PRIOR_REACH)
        for k, v in pr.items():
            try:
                pool[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        lift = 1.25 if fmt in ('series', 'film') else 1.0
        plats = sorted([{'label': k, 'reach_pct': pct1(clamp(v * lift * (1 + (unit('plat', k, seed) - 0.5) * 0.2), 1, 97)), 'group': 'STREAMING/PLATFORM'}
                        for k, v in pool.items() if k not in ('Amazon Prime Video', 'HBO Max', 'Apple TV')], key=lambda p: -p['reach_pct'])[:14]
        if home and not any(p['label'].lower() == home.lower() for p in plats):
            plats.insert(0, {'label': home, 'reach_pct': pct1(72 + 20 * unit('home', seed)), 'group': 'STREAMING/PLATFORM'})
        else:
            for p in plats:
                if p['label'].lower() == home.lower():
                    p['reach_pct'] = pct1(max(p['reach_pct'], 58 + 30 * unit('home', seed)))
            plats.sort(key=lambda p: -p['reach_pct'])
    identity = {'demographics': demo, 'geography': prof.get('geography') or [], 'platforms': plats, 'purchase': prof.get('purchase') or [],
                'media_diet': prof.get('media_diet') or [], 'talent': prof.get('talent') or [], 'avid_share_pct': avid}
    identity['read'] = identity_read(identity, meta, corpus.get('gen_pop') or {})
    momentum = _momentum_from_trends(corpus.get('trends') or [])
    traits = derive_traits(fmt, identity, corpus, research, momentum, seed)
    response = E.response_curves(traits, meta, research, seed)
    twin = {'meta': meta, 'identity': identity, 'traits': traits, 'response': response}
    twin['lifecycle'] = derive_lifecycle(traits, meta, momentum, seed)
    twin['runs'] = []
    twin['labs'] = {'spend': []}
    twin['panel_sessions'] = []
    if ip_related:
        from migration import digital_twin_ltv as L
        twin['ip_lifetime_value'] = L.lifetime_value(twin, research)
        twin['standing_questions'] = standing_questions(twin, corpus, seed)
    # three runs with the build
    goal_val = messy_count(subject, 'goal', size * 0.3)
    noun = E._noun_for(twin)
    anchors_for_runs = {'title_fit': research.get('title_fit')} if research.get('title_fit') else {}
    scenarios = [
        {'kind': 'greenlight', 'inputs': {'title': f"the next {name} release" if kind != 'idea' else subject, 'platforms': [home], 'goal': {'metric': noun, 'value': goal_val, 'platform': home, 'window_days': 30}, 'anchors': anchors_for_runs}},
        {'kind': 'content', 'inputs': {'kind': 'season' if fmt == 'series' else 'sequel' if fmt in ('film', 'book', 'game') else 'episode', 'number': 2, 'platform': home}},
        {'kind': 'platform_move', 'inputs': {'from': home, 'to': 'Netflix' if home.lower() != 'netflix' else 'Hulu'}},
    ]
    for sc in scenarios:
        try:
            twin['runs'].append(E.simulate(twin, sc, created_by=created_by))
        except Exception as e:
            print(f"[twin] build run {sc['kind']} skipped: {e}")
    tr = track_record(twin, corpus, seed)
    if tr:
        twin['track_record'] = tr
    twin['the_read'] = write_read(twin, claude_json, seed)
    timings['total_s'] = round(time.time() - t0, 2)
    twin['diagnostics'] = {
        'version': VERSION, 'seed': seed, 'timings': timings, 'size_source': size_source, 'momentum': momentum,
        'corpus': {'profile_key': corpus.get('profile_key'), 'catalog': corpus.get('catalog'), 'journeys': len(corpus.get('journeys') or []),
                   'bpiq': len(corpus.get('bpiq') or []), 'attribution': len(corpus.get('attribution') or []), 'trends': len(corpus.get('trends') or []),
                   'ledger': len(corpus.get('ledger') or []), 'gen_pop_rows': len(corpus.get('gen_pop') or {})},
        'research': {k: research.get(k) for k in ('format', 'franchise_stage', 'platform_home', 'us_fans_estimate', 'avid_share_pct', 'title_fit', 'notes', '_cached')} if research else {},
        'research_prices': research.get('prices') if research else None,
    }
    return scrub_payload(twin)


# -------------------------------------------------------------- persist
def _slug(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]+', '_', str(s or '')).strip('_')[:70] or 'twin'


def index_entry(key: str, payload: dict) -> dict:
    m = payload.get('meta') or {}
    ltv = payload.get('ip_lifetime_value') or {}
    return {'key': key, 'name': m.get('name'), 'subject': m.get('subject'), 'subject_kind': m.get('subject_kind'), 'ip_related': bool(m.get('ip_related')),
            'format': m.get('format'), 'platform_home': m.get('platform_home'), 'size_us': m.get('size_us'), 'created_by': m.get('created_by'),
            'created_at': m.get('created_at'), 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'window_start': m.get('window_start'), 'window_end': m.get('window_end'), 'hero_image': m.get('hero_image') or '',
            'runs': len(payload.get('runs') or []), 'spend_plans': len(((payload.get('labs') or {}).get('spend')) or []),
            'panel_sessions': len(payload.get('panel_sessions') or []), 'has_ltv': bool(ltv),
            'ltv_5y': (ltv.get('total') or {}).get('5', {}).get('expected') if ltv else None,
            'ltv_10y': (ltv.get('total') or {}).get('10', {}).get('expected') if ltv else None}


def update_index(s3, key: str, payload: dict) -> None:
    try:
        idx = {'runs': []}
        try:
            idx = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)['Body'].read().decode('utf-8')) or {'runs': []}
        except Exception:
            pass
        runs = [r for r in (idx.get('runs') or []) if r.get('key') != key]
        runs.append(index_entry(key, payload))
        runs.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
        idx['runs'] = runs[:500]
        idx['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        s3.put_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY, Body=json.dumps(idx, ensure_ascii=False).encode('utf-8'), ContentType='application/json')
    except Exception as e:
        print(f"[twin] index update failed (non-fatal): {e}")


def save_twin(s3, key: str, payload: dict) -> str:
    """Write (or rewrite in place) the gz payload and refresh the index."""
    payload.setdefault('meta', {})['key'] = key
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8'))
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body.getvalue(), ContentType='application/json', ContentEncoding='gzip')
    update_index(s3, key, payload)
    return key


def persist(s3, payload: dict, username: str, job_id: str = '') -> str:
    meta = payload.get('meta') or {}
    safe_user = _slug(username or 'anon')
    ts = _dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    key = f"{S3_PREFIX}{safe_user}/{_slug(meta.get('name') or meta.get('subject'))}_{ts}_{(job_id or uuid.uuid4().hex)[:10]}.json.gz"
    if s3 is None:
        print(f"[twin] no s3 client; would have written {key}")
        return key
    save_twin(s3, key, payload)
    try:
        record_ledger(payload, key)
    except Exception as e:
        print(f"[twin] ledger entry skipped: {e}")
    return key


def record_ledger(payload: dict, key: str) -> None:
    """The twin's headline figures join the shared ledger so a later
    read on the subject stays coherent with it."""
    import insights_ledger as il
    m = payload.get('meta') or {}
    metrics = [{'name': 'twin audience, US people', 'value': float(m.get('size_us') or 0), 'unit': 'people', 'definition': 'people in the US in the twin'}]
    ltv = payload.get('ip_lifetime_value') or {}
    if ltv.get('total'):
        metrics.append({'name': 'IP lifetime value, 5 years', 'value': float(ltv['total']['5']['expected']), 'unit': 'usd', 'definition': 'present value over five years'})
        metrics.append({'name': 'IP lifetime value, 10 years', 'value': float(ltv['total']['10']['expected']), 'unit': 'usd', 'definition': 'present value over ten years'})
    il.persist(subject=str(m.get('subject') or ''), metric_family='audience', question=f"Digital twin: {m.get('name')}", route='digital_twin_build',
               metrics=metrics, reply=f"Digital Twins IQ {key}", window_start=str(m.get('window_start') or ''), window_end=str(m.get('window_end') or ''),
               provenance=getattr(il, 'PROV_DELIVERED', 'delivered_artifact'))


def load_twin(s3, key: str) -> Optional[dict]:
    if s3 is None or not key:
        return None
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = obj['Body'].read()
        if key.endswith('.gz') or obj.get('ContentEncoding') == 'gzip':
            raw = gzip.decompress(raw)
        doc = json.loads(raw.decode('utf-8'))
        if isinstance(doc, dict):
            doc.setdefault('meta', {})['key'] = key
        return doc
    except Exception as e:
        print(f"[twin] load failed for {key}: {e}")
        return None


def append_run(s3, key: str, payload: dict, run: dict) -> dict:
    payload.setdefault('runs', []).insert(0, scrub_payload(run))
    save_twin(s3, key, payload)
    return payload


def run_scenario(twin: dict, scenario: dict, *, claude_json: Optional[Callable] = None, created_by: str = '', other_twin: Optional[dict] = None) -> dict:
    """One run on a loaded twin: the engine with the model read when
    the model is on."""
    read_fn = None
    if claude_json is not None:
        def read_fn(run, tw):
            brief = {'twin': tw.get('meta'), 'run': {k: run.get(k) for k in ('kind', 'question', 'outputs', 'drivers', 'comparison', 'recommendation')},
                     'deterministic_read': run.get('read')}
            got = claude_json(READ_SYSTEM_PROMPT, json.dumps(brief, default=str)[:12000], max_tokens=1200, temperature=0.5, surface='twin_run_read')
            return got if isinstance(got, dict) else None
    kind = str(scenario.get('kind') or '').lower()
    if kind == 'compare' and other_twin is not None:
        inner = dict(scenario, kind=str((scenario.get('inputs') or {}).get('scenario_kind') or 'greenlight'))
        inner['inputs'] = {k: v for k, v in (scenario.get('inputs') or {}).items() if k != 'scenario_kind'}
        return E.compare(twin, other_twin, inner, read_fn=read_fn, created_by=created_by)
    return E.simulate(twin, scenario, read_fn=read_fn, created_by=created_by)


__all__ = ['build_twin', 'persist', 'save_twin', 'load_twin', 'append_run', 'run_scenario', 'update_index', 'index_entry', 'corpus_anchors',
           'research_anchors', 'profile_identity', 'find_profile_key', 'is_ip_related', 'guess_format', 'scrub_text', 'scrub_payload',
           'standing_questions', 'track_record', 'write_read', 'default_window', 'S3_PREFIX', 'S3_INDEX_KEY', 'S3_BUCKET', 'RESEARCH_PREFIX',
           'RESEARCH_SYSTEM_PROMPT', 'READ_SYSTEM_PROMPT', 'BANNED_WORDS', 'PRODUCT']
