"""Corpus catalog: one subject-keyed record of every figure Crosswalk has
published anywhere on the dashboard, kept current as data is created and
read in one hit at ask or build time (2026-10-05, Jenna).

Jenna, verbatim: "we need a way to intelligently start caching and
cataloging all of this data as it's created and ensure there is not a
long lag while a user waits on prometheus to find all the data, scour
the corpus, etc and it should always scour everything in the dashboard
which is all stored in s3."

Why it exists
-------------
Alexia's journey was built cold while an Attribution IQ campaign on the
same title sat in the corpus (20x apart on one number). Scott's sample
check sized a show from scratch while its Profile IQ profile sat on the
dashboard (49,918 vs 31,207 in the sample). Every product had its own
inputs and none of them looked at what another product had already
published on the subject. This module is the shared memory.

Shape
-----
    s3://dashboard-inputs/system/corpus_catalog/index.json
        {"version": 1, "updated": iso,
         "subjects": {<subject_key>: {"subject": display, "aliases": [..],
                                      "products": [..], "n_facts": N,
                                      "updated": iso}},
         "sources":  {<s3_key>: {"etag": .., "product": .., "subject_key": ..,
                                 "indexed_at": iso}}}
    s3://dashboard-inputs/system/corpus_catalog/subjects/<subject_key>.json
        {"subject_key": .., "subject": .., "aliases": [..], "facts": [fact..]}

    fact = {"id", "product", "kind", "label", "value", "unit",
            "window": {"start", "end"} | None, "as_of", "source": {...},
            "note"}

    product: profile | journey | attribution | bpiq | chat | deck
    kind:    sample_size | audience_size | platform_count | stage_count |
             funnel_count | conversion_pct | ticketing_partition |
             valuation | stated_number

    A people count may also say what it MEASURES (2026-11-18): "measure"
    is reached | engaged | added | converted, "entry" marks a journey's
    first row, and "anchor" names the valuation file a journey was built
    to hold. The sweep's audit compares two products on the same measure
    only (see MEASURES below).

Index is small (one line per subject and per source) and cached in
process (TTL 300s, ETag-checked), so a lookup is a dict hit. Subject
pages are fetched on demand and cached. Nothing here lists S3 on the
ask path: listing and extraction happen at WRITE time (the hooks in
dashboard_register, journey_iq, mta_iq, bpiq_synthesis, the Prometheus
answer gate) and in the sweep (`migration/corpus_catalog_sync.py`,
every 30 minutes on Hetzner), which only re-reads objects whose ETag
changed.

Consult
-------
    anchors_for(subject, window=None) -> {"subject_key", "subject",
        "facts": [...], "products": [...], "ledger": [...]}
    anchors_block(anchors)             -> prompt text (binding figures)
    profile_anchor(anchors)            -> the profile fact bundle or None
    prior_journey(anchors, window)     -> a journey fact bundle or None

Every function is fail-safe: any S3 or parse trouble returns an empty
result and the caller proceeds. Kill switch CORPUS_CATALOG=0 (host env
only, never a request field).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError, ParamValidationError
except Exception:  # pragma: no cover - import guard for hermetic tests
    boto3 = None

    class ClientError(Exception):
        response = {}

    class ParamValidationError(Exception):
        pass

S3_BUCKET = os.environ.get('CORPUS_CATALOG_BUCKET', 'dashboard-inputs')
INDEX_KEY = 'system/corpus_catalog/index.json'        # subjects only (hot path)
SOURCES_KEY = 'system/corpus_catalog/sources.json'    # s3_key -> etag (sweep only)
RETIRED_KEY = 'system/corpus_catalog/retired_sources.json'   # replies set aside: source key -> {ts, by}
SUBJECT_PREFIX = 'system/corpus_catalog/subjects/'
VERSION = 1

_INDEX_TTL_S = 300
_PAGE_TTL_S = 300
_lock = threading.Lock()
_state = {'client': None, 'index': None, 'index_etag': None, 'index_ts': 0.0,
          'pages': {}, 'retired': None, 'retired_ts': 0.0}
_SUPPORTS_CONDITIONAL_PUT = True


def enabled():
    return os.environ.get('CORPUS_CATALOG', '1').strip() != '0'


def _client():
    with _lock:
        if _state['client'] is not None:
            return _state['client']
    cl = boto3.client('s3')
    with _lock:
        _state['client'] = cl
    return cl


def set_client(cl):
    """Tests inject a fake S3 client here."""
    with _lock:
        _state['handed'] = cl is not None
        _state['client'] = cl
        _state['index'] = None
        _state['index_etag'] = None
        _state['index_ts'] = 0.0
        _state['pages'] = {}
        _state['retired'] = None
        _state['retired_ts'] = 0.0


def _test_without_a_store():
    """True in a test process that handed the catalog no store of its
    own and would therefore write to the real one. A test reads the
    catalog; it never writes to it (2026-11-12: a chat suite and a
    drilldown suite, run with credentials, each rewrote a live subject
    page on every run). A fake handed in by set_client, or a stand-in
    that is not a real storage client, takes the writes as before."""
    if os.environ.get('REGRESSION_TEST_MODE') != '1' or _state.get('handed'):
        return False
    try:
        from botocore.client import BaseClient
        return isinstance(_client(), BaseClient)
    except Exception:
        return False


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ------------------------------------------------------------------ keys
# Where something is watched, bought or heard is a QUALIFIER on the
# subject, never the subject (2026-10-21, Jenna: 'Nip/Tuck on Hulu' is
# Nip/Tuck; the sweep had filed eight such journeys under their
# platform and paired them with the platform's own profile). '+' may
# arrive as '+' or, after the fold's normalization, as ' plus'.
_PLUS = r'(?:\s*\+|\s+plus)?'
_PLATFORM_WORDS = (
    r'amazon prime video|amazon prime|prime video|amazon|prime|'
    r'netflix|hulu|hbo max|hbo|max|disney' + _PLUS + r'|peacock|paramount' + _PLUS + r'|'
    r'apple tv' + _PLUS + r'|starz|showtime|amc' + _PLUS + r'|discovery' + _PLUS + r'|espn' + _PLUS + r'|'
    r'bet' + _PLUS + r'|mgm' + _PLUS + r'|hallmark' + _PLUS + r'|tubi|pluto tv|pluto|'
    r'the roku channel|roku channel|roku|freevee|crunchyroll|britbox|youtube tv|youtube|'
    r'fubo|sling|philo|cbs|nbc|abc|fox|the cw|cw|spotify|apple podcasts|siriusxm|'
    r'audible|kindle|tiktok shop|tiktok|instagram|online grocery|instacart|doordash|'
    r'uber eats|walmart|target|any digital movie ticketing platform'
)
_CONSUMER_NOUNS = r'viewers|watchers|listeners|readers|players|subscribers|users|fans|audience|shoppers|buyers'
# '<Title> on <Platform>', '<Title> viewers on <Platform>', '<Title> Viewers ON HULU'
_PLATFORM_TAIL_RX = re.compile(
    r'^(?P<title>.+?)\s+(?:on|via|at)\s+(?P<plat>(?:' + _PLATFORM_WORDS + r'))(?![a-z0-9])(?P<rest>.*)$', re.I)
# '<Title> - <Platform> Viewers' (a cut named for where the audience watches)
_PLATFORM_CUT_RX = re.compile(
    r'^(?P<title>.+?)\s+-\s+(?P<plat>(?:' + _PLATFORM_WORDS + r'))\s+(?:' + _CONSUMER_NOUNS + r')\s*$', re.I)
_TRAILING_LABEL_RX = re.compile(
    r'\s+(series|tu|total universe|viewers|fans|audience|film|movie|'
    r'\(film\)|\(series\))$')
_CUT_SUFFIX_RX = re.compile(
    r'\s+-\s+(avid fan|casual fan|avid|casual|female|male|f|m|'
    r'\d{2}-\d{2}|1[0-9]-[0-9]{2}|gen z|millennials?|boomers?|gen x|'
    r'[a-z ]+ ca|[a-z ]+ ny|[a-z ]+ tx|[a-z ]+ fl|parents of .*|.*users|'
    r'.*members|.*subscribers|.*shoppers)$', re.I)


# ' - Q4 2025', ' CY2025', ' 2023/2024 TU', ' - Jan 2026', ' 2025 YTD':
# a window label on a profile name is not part of the entity.
_WINDOW_LABEL_RX = re.compile(
    r'(?:\s+-\s+|\s+)(?:q[1-4]\s+(?:20)?\d{2}|(?:19|20)\d{2}(?:\s*/\s*(?:19|20)?\d{2})?(?:\s+tu)?|'
    r'cy\s?(?:20)?\d{2}|calendar\s+(?:20)?\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)'
    r'[a-z]*\s+(?:20)?\d{2}|(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}(?:\s+ytd)?(?:\s+tu)?|(?:20)?\d{2}\s+ytd)\s*$')
_QUALIFIER_RX = re.compile(
    r'[,:]?\s*(?:opening (?:weekend|week|day|night)|premiere (?:week|weekend|night)|'
    r'launch (?:week|weekend)|finale (?:week|weekend)|season \d+ (?:premiere|finale))\b.*$')


def split_platform(name, extra=()):
    """(title, platform) for a name that carries a where-it-is-watched
    qualifier: 'Nip/Tuck on Hulu' -> ('Nip/Tuck', 'Hulu'); 'Gilmore
    Girls Viewers ON HULU' -> ('Gilmore Girls Viewers', 'HULU'); 'BET -
    Paramount+ Viewers' -> ('BET', 'Paramount+'). ``extra`` names (the
    journey's client, its platform label) count as platforms too, so
    'Gunna x Under Armour Athlete Picks on Under Armour' splits for a
    client named Under Armour. (name, '') when there is no qualifier.
    The platform is returned as written."""
    raw = ' '.join(str(name or '').split())
    if not raw:
        return '', ''
    m = _PLATFORM_CUT_RX.match(raw)
    if m:
        return m.group('title').strip(), m.group('plat').strip()
    m = _PLATFORM_TAIL_RX.match(raw)
    if m:
        return m.group('title').strip(' ,'), m.group('plat').strip()
    for ex in extra or ():
        ex = ' '.join(str(ex or '').split())
        if not ex:
            continue
        m = re.match(r'^(?P<title>.+?)\s+(?:on|via|at)\s+' + re.escape(ex) + r'\s*(?:\(.*\))?\s*$', raw, re.I)
        if m:
            return m.group('title').strip(' ,'), ex
    return raw, ''


def entity_fold(name):
    """One key per real-world entity across the spellings the dashboard
    produces: case, '&' vs 'and', punctuation, a trailing platform
    ('on Hulu', 'Viewers ON HULU', ' - Paramount+ Viewers'), a trailing
    Series / TU / (film) label, a cut suffix (' - Avid Fan',
    ' - Female')."""
    t = str(name or '').strip().lower()
    t = t.replace('&', ' and ').replace('\u00d7', ' x ')
    t = re.sub(r'\([^)]*\)', ' ', t)
    t = ' '.join(t.split())
    t = _QUALIFIER_RX.sub('', t)
    t = _WINDOW_LABEL_RX.sub('', t)
    t = _CUT_SUFFIX_RX.sub('', t)
    t, _plat = split_platform(t)
    t = t.replace('+', ' plus ')
    t = ' '.join(t.split())
    t = _TRAILING_LABEL_RX.sub('', t)
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    t = re.sub(r'\bthe\b', ' ', t)
    return ' '.join(t.split())


def subject_key(name):
    f = entity_fold(name)
    return f.replace(' ', '_')[:120] if f else ''


def best_display(aliases, fallback=''):
    """The cleanest name for a subject among its aliases: no cut
    suffix, no parenthetical, then the shortest."""
    cands = [str(a).strip() for a in (aliases or []) if str(a or '').strip()]
    if not cands:
        return fallback
    def _rank(a):
        return (' - ' in a, '(' in a, a.isupper(), len(a))
    return sorted(cands, key=_rank)[0]


def _fact_id(product, source_key, kind, label, window):
    raw = f"{product}|{source_key}|{kind}|{label}|{json.dumps(window, sort_keys=True)}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def make_fact(product, kind, label, value, unit, *, window=None, as_of=None,
              source_key='', source_user='', note='', measure='', anchor=''):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    win = None
    if isinstance(window, dict) and (window.get('start') or window.get('end')):
        win = {'start': str(window.get('start') or '')[:10],
               'end': str(window.get('end') or '')[:10]}
    fact = {'id': _fact_id(product, source_key, kind, label, win),
            'product': product, 'kind': kind, 'label': str(label)[:160],
            'value': (int(v) if unit in ('people', 'individuals') and abs(v - round(v)) < 1e-6
                      else round(v, 4)),
            'unit': unit, 'window': win, 'as_of': (as_of or _now_iso())[:19],
            'source': {'key': source_key, 'user': source_user},
            'note': str(note or '')[:240]}
    if measure:
        fact['measure'] = str(measure)
    if anchor:
        fact['anchor'] = str(anchor)
    return fact


# ------------------------------------------------------------- measures
# What a people count MEASURES (2026-11-18, Jenna). The sweep paired the
# Coach x WNBA journey's buyers (171,621, its end point) with the
# valuation's reach (3,781,941) and mailed it as a disagreement. Both
# were filed as kind 'audience_size'; nothing said one was the bottom of
# a path and the other the top of it. A count now says what it is, and
# two products are compared on the same measure only.
#
#   reached     the people the subject reached: a valuation's reach, a
#               journey's first row
#   engaged     the people who engaged with the brand's content
#   added       the people added to the brand's audience in the window
#   converted   the people who finished the path (bought, subscribed,
#               watched): a journey's end point, a valuation's converters
#
# FUNNEL_ORDER runs upstream to downstream. On one subject a later step
# sitting below an earlier one (buyers below reach) is the expected
# shape, never a finding; only a later step ABOVE an earlier one is.
MEASURE_REACHED = 'reached'
MEASURE_ENGAGED = 'engaged'
MEASURE_ADDED = 'added'
MEASURE_CONVERTED = 'converted'
MEASURE_TOTAL = 'total'
FUNNEL_ORDER = (MEASURE_REACHED, MEASURE_ENGAGED, MEASURE_CONVERTED)

# Order matters: a label is classed by the first family that names it.
# A showtimes page and a ticketing-site visit are two different steps
# (film ladder per no-box-office-prediction.mdc, settled 2026-10-06):
# 'Looked up showtimes' compares with 'reached a showtimes page', never
# with 'went to a ticketing site or app for a ticket'.
STAGE_CLASSES = (
    ('checkout', ('checkout', 'cart', 'paid', 'bought', 'purchase', 'end point',
                  'final ticket step')),
    ('showtimes', ('showtime',)),
    ('ticketing', ('ticketing site', 'ticketing-site', 'ticketing page', 'for a ticket')),
    ('infoseek', ('looked', 'search', 'info', 'research', 'trailer', 'review')),
    ('exposed', ('saw', 'exposed', 'reached by', 'tracked campaign')),
    ('acted', ('acted', 'engaged', 'clicked')),
)


def stage_class(label):
    """The step a path row's label names, or 'other'."""
    low = str(label or '').lower()
    for name, toks in STAGE_CLASSES:
        if any(t in low for t in toks):
            return name
    return 'other'


def typed_measure(f, other=None):
    """The measure a fact carries BY TYPE (never read off a label), or
    ''. ``other`` is the fact it is being compared with.

    A fact indexed before the measures existed still resolves: a
    valuation's 'audience_size' is its reach, a journey's is its end
    point, a valuation's people-valued 'valuation' fact is the people it
    added. A journey's first row is its entry audience; next to a
    valuation, which defines who entered, that is the reach."""
    if not isinstance(f, dict):
        return ''
    m = str(f.get('measure') or '').strip()
    if m:
        return m
    kind, product = f.get('kind'), f.get('product')
    if kind == 'audience_size':
        if product == 'bpiq':
            return MEASURE_REACHED
        if product == 'journey':
            return MEASURE_CONVERTED
        return MEASURE_TOTAL
    if kind == 'valuation' and product == 'bpiq' and f.get('unit') == 'people':
        return MEASURE_ADDED
    if (kind == 'stage_count' and product == 'journey' and f.get('entry')
            and isinstance(other, dict) and other.get('product') == 'bpiq'):
        return {'exposed': MEASURE_REACHED, 'other': MEASURE_REACHED,
                'acted': MEASURE_ENGAGED}.get(stage_class(f.get('label')), '')
    return ''


# ---------------------------------------------------------- extractors
_WINDOW_RX = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:TO|to|-)\s*(\d{4}-\d{2}-\d{2})')


def _parse_window_label(label):
    m = _WINDOW_RX.search(str(label or ''))
    if not m:
        return None
    return {'start': m.group(1), 'end': m.group(2)}


# The fixed 10 million panel projects to the 2026 US population (Rule
# #3a): US people = sample / 10,000,000 x 329,900,000.
PANEL_TO_US = 32.99

# The two size facts a profile yields, named for what they are. The
# AUDIENCE fact is projected US people (the file's US Gen Pop Projection
# at BP 100); the SAMPLE fact is the count inside the 10 million panel
# and is never a US figure. 2026-10-20 (Jenna, the NCIS: New York sweep
# note): 'US audience = 260,390' next to a journey's 3,266,736 read as a
# sample next to a projection. Both were projections; the labels now
# say so, and every reader that compares across products compares
# PROFILE_AUDIENCE_LABEL figures only.
PROFILE_AUDIENCE_LABEL = 'US audience (projected US people)'
PROFILE_SAMPLE_LABEL = 'inside the 10 million sample (not a US count)'


def projected_us(sample):
    """The US-people projection of a count inside the 10 million sample."""
    try:
        return int(round(float(sample) * PANEL_TO_US))
    except (TypeError, ValueError):
        return 0


def facts_from_profile_rows(s3_key, display_name, rows, user=''):
    """rows: iterable of dicts with Column / Value / Brand Penetration
    (Row) / Original Raw Numbers / US Gen Pop Projection (the profile
    CSV columns). Only the metadata rows and the platform rows are
    read; the first few KB of the CSV already carry the size facts.
    The audience fact is ALWAYS projected US people: the file's own
    projection cell, or the canonical chain from the sample when a file
    carries no projection cell (never the sample itself)."""
    facts = []
    sample = proj = None
    window = None
    subject_val = ''
    platform_rows = []
    for r in rows:
        col = str(r.get('Column') or '').strip().upper()
        val = str(r.get('Value') or '').strip()
        if col == 'BRAND INPUT' and sample is None:
            sample = _num(r.get('Original Raw Numbers'))
            proj = _num(r.get('US Gen Pop Projection'))
        elif col == 'SAMPLE SIZE':
            window = window or _parse_window_label(val)
            if sample is None:
                sample = _num(r.get('Original Raw Numbers'))
                proj = _num(r.get('US Gen Pop Projection'))
        elif col == 'SUBJECT':
            subject_val = val
        elif col == 'STREAMING/PLATFORM':
            p = _num(r.get('US Gen Pop Projection'))
            if p:
                platform_rows.append((val, p))
    subject = display_name or subject_val or re.sub(r'_\d{2}_\d{2}_\d{4}.*$', '', s3_key).replace('_', ' ')
    _label = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}\.csv$', '', str(display_name or s3_key)).replace('_', ' ')
    _label = _WINDOW_LABEL_RX.sub('', _label.strip().lower())
    is_cut = bool(re.search(r'\s-\s', _label))
    note = f"Profile IQ: {subject}"
    if sample and not proj:
        # an older file with no projection cell: the audience fact is
        # still US people, through the canonical chain, never the sample
        proj = projected_us(sample)
    if sample:
        facts.append(make_fact('profile', 'sample_size', PROFILE_SAMPLE_LABEL,
                               sample, 'individuals', window=window, source_key=s3_key,
                               source_user=user, note=note))
    if proj:
        facts.append(make_fact('profile', 'audience_size', PROFILE_AUDIENCE_LABEL, proj, 'people',
                               window=window, source_key=s3_key, source_user=user, note=note))
    for val, p in sorted(platform_rows, key=lambda x: -x[1])[:6]:
        facts.append(make_fact('profile', 'platform_count', f"US viewers on {val}", p, 'people',
                               window=window, source_key=s3_key, source_user=user, note=note))
    facts = [f for f in facts if f]
    # 'Gilmore Girls Viewers ON HULU', 'BET - Paramount+ Viewers': the
    # platform is where the audience watches, kept on the facts; the
    # subject key already folds it away.
    _, platform = split_platform(_WINDOW_LABEL_RX.sub('', str(display_name or subject or '').strip()))
    for f in facts:
        f['cut'] = is_cut
        if platform:
            f['platform'] = str(platform)[:60]
    return subject, facts


def facts_from_profile_csv_text(s3_key, display_name, text, user=''):
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    return facts_from_profile_rows(s3_key, display_name, rows, user=user)


# A journey target that describes the READ rather than the thing read
# ('Mark Rober Audience - YouTube Creator Journey', 'Banking Research -
# Chime Steal-Share Brief', 'Paramount+ Subscriber Lifecycle'): the
# client is the subject there.
_DESCRIPTOR_RX = re.compile(
    r'\b(journey|brief|research|migration|discovery|cross[- ]window|steal[- ]share|case study|lifecycle)\b', re.I)


def journey_identity(meta, inputs=None):
    """(subject, platform) for a journey payload (2026-10-21, Jenna: a
    journey named '<Title> on <Platform>' describes the TITLE; the
    platform is where it is watched). The payload's own title fields
    come first (subject / content_show / title / anchor_title, the
    inputs' subject); then the target with its 'on <Platform>'
    qualifier split off; a descriptor-shaped target hands the identity
    to the client; the client (customer_brand) is the last resort. The
    cbisson watch journeys carried the platform as customer_brand and
    the title only inside target_name, which is how 'Nip/Tuck on Hulu'
    was filed under Hulu."""
    meta = meta if isinstance(meta, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    client = str(meta.get('customer_brand') or '').strip()
    plat_hint = str(meta.get('platform_label') or meta.get('platform') or inputs.get('platform') or '').strip()
    target = str(meta.get('target_name') or meta.get('target') or meta.get('project_name')
                 or meta.get('target_display') or '').strip()
    for f in (meta.get('subject'), meta.get('content_show'), meta.get('title'), meta.get('anchor_title'),
              inputs.get('subject'), inputs.get('title')):
        f = str(f or '').strip()
        if f:
            title, plat = split_platform(f, extra=(client, plat_hint))
            if not plat and target:
                _, plat = split_platform(target, extra=(client, plat_hint))
            return title or f, (plat or plat_hint)
    if target:
        title, plat = split_platform(target, extra=(client, plat_hint))
        if plat:
            return title or target, plat
        if client and _DESCRIPTOR_RX.search(target):
            return client, plat_hint
        return target, plat_hint
    return client, plat_hint


def facts_from_journey(key, payload, user=''):
    meta = (payload or {}).get('meta') or {}
    body = (payload or {}).get('fragrance_shop_journey') or {}
    kpis = (payload or {}).get('kpis') or {}
    subject, platform = journey_identity(meta, (payload or {}).get('inputs'))
    subject = str(subject or '').strip()
    window = {'start': meta.get('start_date') or meta.get('window_start'),
              'end': meta.get('end_date') or meta.get('window_end')}
    as_of = str(meta.get('created_at') or meta.get('generated_at') or '')[:19] or None
    note = f"Digital Journey IQ: {meta.get('project_name') or subject}"
    # A journey built on a valued partnership carries the valuation it
    # holds to (journey_bpiq_anchor stamps meta.valuation_anchor). Its
    # shared counts are typed and name that file, so the audit can check
    # them against the valuation count for count.
    held = meta.get('valuation_anchor') if isinstance(meta.get('valuation_anchor'), dict) else {}
    held_key = str(held.get('key') or '').strip()
    facts = []
    spine = body.get('spine') or []
    if isinstance(spine, dict):
        spine = spine.get('stages') or []
    entry_seen = False
    for st in spine:
        if not isinstance(st, dict):
            continue
        label = str(st.get('label') or st.get('name') or st.get('id') or '').strip()
        cnt = _num(st.get('accounts') if st.get('accounts') is not None
                   else (st.get('count') if st.get('count') is not None else st.get('users')))
        sid = str(st.get('id') or '').lower()
        if sid == 'tam' or cnt == 329_900_000:
            continue
        is_entry, entry_seen = (not entry_seen), True
        if label and cnt:
            shared = sid if (held_key and sid in (MEASURE_REACHED, MEASURE_ENGAGED)) else ''
            f = make_fact('journey', 'stage_count', label, cnt, 'people', window=window,
                          as_of=as_of, source_key=key, source_user=user, note=note,
                          measure=shared, anchor=held_key if shared else '')
            if f and is_entry:
                f['entry'] = True       # the first row: the journey's entry audience
            facts.append(f)
    if not facts and isinstance(kpis, dict):
        # Story-mode payloads (subscriber lifecycle, flywheel) carry
        # their headline figures in kpis; bank the integer ones.
        for kk, vv in list(kpis.items())[:16]:
            cnt = _num(vv)
            if cnt and cnt >= 100 and 'pct' not in str(kk).lower() and not str(kk).endswith('_m'):
                facts.append(make_fact('journey', 'kpi', str(kk).replace('_', ' '), cnt, 'people',
                                       window=window, as_of=as_of, source_key=key,
                                       source_user=user, note=note))
    if kpis.get('total_users'):
        # The end point is the people who FINISHED the path (buyers,
        # subscribers, viewers who converted): the bottom of a funnel,
        # never its top. It keeps kind 'audience_size' for the readers
        # that floor a universe on it; the measure says what it is.
        facts.append(make_fact('journey', 'audience_size', 'journey end point, US people',
                               kpis['total_users'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note,
                               measure=MEASURE_CONVERTED, anchor=held_key))
    if kpis.get('conversion_pct') is not None:
        facts.append(make_fact('journey', 'conversion_pct', 'share reaching the end point',
                               kpis['conversion_pct'], 'pct', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    if not facts:
        # Hand-built story modes carry their figures as fact strings.
        for fx in (payload or {}).get('facts') or []:
            txt = fx.get('fact') if isinstance(fx, dict) else fx
            if isinstance(txt, str) and txt.strip():
                for f in facts_from_answer(subject, txt, user=user, thread_id='', source_label='journey'):
                    f['source']['key'] = key
                    f['note'] = note
                    f['window'] = {'start': window.get('start'), 'end': window.get('end')} if window.get('start') else None
                    facts.append(f)
                if len(facts) >= 40:
                    break
    if held_key:
        # The fourth count a held journey shares with its valuation: the
        # people added to the brand's audience in the window. The row the
        # journey shows wins; the anchor block is the fallback.
        added = None
        for det in (body.get('detours') or []):
            if not isinstance(det, dict) or str(det.get('source') or '') != 'valuation':
                continue
            for row in (det.get('rows') or []):
                if isinstance(row, dict) and str(row.get('label') or '').strip().lower() == 'added in the window':
                    added = _num(row.get('accounts'))
        if added is None:
            added = _num(held.get('added'))
        if added:
            brand = str(held.get('brand') or '').strip() or 'brand'
            facts.append(make_fact('journey', 'funnel_count', f"added to the {brand} audience in the window",
                                   added, 'people', window=window, as_of=as_of, source_key=key,
                                   source_user=user, note=note, measure=MEASURE_ADDED, anchor=held_key))
    for det in (body.get('detours') or []):
        if not isinstance(det, dict):
            continue
        title = str(det.get('title') or det.get('label') or '').lower()
        if 'ticket' in title and 'reached' in title or 'partition' in title:
            for row in (det.get('rows') or [])[:8]:
                if isinstance(row, dict) and row.get('label') is not None and row.get('pct') is not None:
                    facts.append(make_fact('journey', 'ticketing_partition', str(row['label']),
                                           row['pct'], 'pct', window=window, as_of=as_of,
                                           source_key=key, source_user=user, note=note))
    facts = [f for f in facts if f]
    if platform:
        for f in facts:
            f['platform'] = str(platform)[:60]
    return subject, facts


def facts_from_attribution(slug, assets, fit, user=''):
    t = (assets or {}).get('title')
    distributor = ''
    if isinstance(t, dict):
        distributor = str(t.get('distributor') or '')
        t = t.get('display_name') or t.get('title_slug') or ''
    title = str(t or slug.replace('_', ' ')).strip()
    # 'The Influencer Project - Hades' carries the distributor; the
    # subject is the title.
    if ' - ' in title:
        head, tail = title.rsplit(' - ', 1)
        if tail.strip() and (tail.strip().lower() in distributor.lower()
                             or tail.strip().lower() in slug.replace('_', ' ')):
            title = head.strip()
    overall = (fit or {}).get('overall') or {}
    paths = overall.get('paths') or {}
    nest = paths.get('nest') or {}
    where = paths.get('where') or overall.get('where') or {}
    as_of = str((fit or {}).get('as_of') or '')[:19] or None
    starts, ends = [], []
    for ph in (assets or {}).get('phases') or []:
        if isinstance(ph, dict):
            st = ph.get('start_date') or ph.get('start')
            en = ph.get('end_date') or ph.get('end')
            if st:
                starts.append(str(st)[:10])
            if en:
                ends.append(str(en)[:10])
    # an open-ended last phase runs to the fit's as_of date
    window = {'start': min(starts) if starts else None,
              'end': (max(ends) if ends and max(ends) >= (as_of or '')[:10] else (as_of or '')[:10])
              or None}
    note = f"Attribution IQ: {title}"
    key = f"intent/{slug}/"
    facts = []
    # Film ladder nouns per no-box-office-prediction.mdc (2026-10-06):
    # stage 3 is a showtimes page, stage 4 is the ticketing-site visit
    # for a ticket. Never a purchase, checkout or buyer claim.
    labels = {'0_tam': 'US addressable', '1_exposed': 'saw tracked campaign content',
              '2_infoseek': 'looked the title up', '3_ticketer': 'reached a showtimes page',
              '4_paid': 'went to a ticketing site or app for a ticket'}
    items = []
    if isinstance(nest, dict):
        items = [(k, v) for k, v in nest.items()]
    elif isinstance(nest, list):
        items = [(str(r.get('stage') or ''), r) for r in nest if isinstance(r, dict)]
    for stage, row in items:
        if stage == '0_tam':
            continue
        cnt = _num((row or {}).get('us_accounts') if isinstance(row, dict) else row)
        if cnt:
            facts.append(make_fact('attribution', 'stage_count', labels.get(stage, stage), cnt,
                                   'people', window=window, as_of=as_of, source_key=key,
                                   source_user=user, note=note))
    part = where.get('ticketer_partition') or {}
    pairs = []
    if isinstance(part, dict):
        pairs = list(part.items())
    elif isinstance(part, list):
        pairs = [(r.get('surface') or r.get('label'), r.get('pct')) for r in part if isinstance(r, dict)]
    for lbl, pct in pairs:
        if lbl is None:
            continue
        p = _num(pct)
        if True:
            if p is not None:
                facts.append(make_fact('attribution', 'ticketing_partition', str(lbl), p, 'pct',
                                       window=window, as_of=as_of, source_key=key,
                                       source_user=user, note=note))
    return title, [f for f in facts if f]


def facts_from_bpiq(key, payload, user=''):
    p = payload or {}
    subject = str(p.get('project_name') or '').strip()
    window = {'start': p.get('start_date'), 'end': p.get('end_date')}
    as_of = str(p.get('created_at') or '')[:19] or None
    note = f"Brand Partnership IQ: {subject}"
    facts = []
    if p.get('projected_audience_size'):
        facts.append(make_fact('bpiq', 'audience_size', 'US audience reached by the partnership',
                               p['projected_audience_size'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note, measure=MEASURE_REACHED))
    if p.get('audience_size'):
        facts.append(make_fact('bpiq', 'sample_size', 'viewers inside the 10 million sample',
                               p['audience_size'], 'individuals', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    # The valuation's own funnel below its reach (2026-11-18): the people
    # who engaged with the brand and the people who converted, so a
    # journey on the partnership is compared with the valuation step for
    # step and never end point against reach.
    brand = str(p.get('brand_partner') or '').strip() or 'the brand'
    tot = p.get('totals') if isinstance(p.get('totals'), dict) else {}
    conv = p.get('conversions') if isinstance(p.get('conversions'), dict) else {}
    if _num(tot.get('post_users_projected')):
        facts.append(make_fact('bpiq', 'funnel_count', f"US people who engaged with {brand} in the window",
                               tot['post_users_projected'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note, measure=MEASURE_ENGAGED))
    if conv.get('enabled') is not False and _num(conv.get('post_users_projected')):
        facts.append(make_fact('bpiq', 'funnel_count', 'US people who converted in the window',
                               conv['post_users_projected'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note, measure=MEASURE_CONVERTED))
    val = p.get('valuation') or {}
    for k, lbl in (('total_brand_value', 'total brand value, USD'),
                   ('attributable_to_partnership', 'value attributable to the partnership, USD'),
                   ('incremental_users', 'incremental US users')):
        if val.get(k) is not None:
            facts.append(make_fact('bpiq', 'valuation', lbl, val[k],
                                   'people' if k == 'incremental_users' else 'usd',
                                   window=window, as_of=as_of, source_key=key,
                                   source_user=user, note=note,
                                   measure=MEASURE_ADDED if k == 'incremental_users' else ''))
    return subject, [f for f in facts if f]


_NUM_SENT_RX = re.compile(
    r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:million|M\b|K\b|k\b)|\d+(?:\.\d+)?%)')


def facts_from_answer(subject, text, user='', thread_id='', source_label='chat'):
    """Every figure a Prometheus reply states, with the sentence it sat
    in, so the next answer on the subject sees it."""
    facts = []
    txt = str(text or '')
    sents = re.split(r'(?<=[.!?])\s+|\n+', txt)
    seen = set()
    for s in sents:
        s = s.strip()
        if not s or len(s) > 400:
            continue
        for m in _NUM_SENT_RX.finditer(s):
            raw = m.group(1)
            v, unit = _parse_stated(raw)
            if v is None or v < 10 and unit != 'pct':
                continue
            sig = (round(v, 4), unit)
            if sig in seen:
                continue
            seen.add(sig)
            f = make_fact(source_label, 'stated_number', s[:160], v, unit,
                          source_key=f"thread/{user}/{thread_id}", source_user=user,
                          note=s[:240])
            if f:
                facts.append(f)
            if len(facts) >= 24:
                return facts
    return facts


def _parse_stated(raw):
    r = raw.replace(',', '').strip()
    try:
        if r.endswith('%'):
            return float(r[:-1]), 'pct'
        m = re.match(r'^(\d+(?:\.\d+)?)\s*(million|M|K|k)$', r)
        if m:
            n = float(m.group(1))
            mult = 1_000_000 if m.group(2).lower().startswith('m') else 1_000
            return n * mult, 'people'
        return float(r), 'people'
    except ValueError:
        return None, None


def _num(v):
    try:
        if v is None or v == '':
            return None
        f = float(str(v).replace(',', ''))
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ storage
def _read_json_with_etag(key):
    try:
        resp = _client().get_object(Bucket=S3_BUCKET, Key=key)
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('NoSuchKey', '404', 'NotFound'):
            return None, None
        raise
    body = resp['Body'].read().decode('utf-8')
    try:
        doc = json.loads(body) if body.strip() else None
    except Exception:
        doc = None
    return doc, (resp.get('ETag') or '').strip('"') or None


def _is_precondition_failed(err):
    code = (err.response.get('Error') or {}).get('Code', '')
    status = (err.response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
    return code in ('PreconditionFailed', '412') or status == 412


def _put_json_cas(key, doc, etag):
    global _SUPPORTS_CONDITIONAL_PUT
    if _test_without_a_store():
        return True                 # nothing is written; the caller has nothing to retry
    s3 = _client()
    kwargs = dict(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, separators=(',', ':')).encode('utf-8'),
                  ContentType='application/json')
    if _SUPPORTS_CONDITIONAL_PUT:
        k2 = dict(kwargs)
        if etag:
            k2['IfMatch'] = etag
        else:
            k2['IfNoneMatch'] = '*'
        try:
            s3.put_object(**k2)
            return True
        except ParamValidationError:
            _SUPPORTS_CONDITIONAL_PUT = False
        except ClientError as e:
            if _is_precondition_failed(e):
                return False
            raise
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=key)
        current = (head.get('ETag') or '').strip('"')
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('404', 'NoSuchKey', 'NotFound'):
            current = None
        else:
            raise
    if current != etag:
        return False
    s3.put_object(**kwargs)
    return True


def _update_json(key, mutate_fn, max_retries=6):
    import random
    for attempt in range(max_retries + 1):
        try:
            doc, etag = _read_json_with_etag(key)
        except Exception as e:
            print(f"[corpus-catalog] read failed {key}: {e}")
            return None
        doc = doc if isinstance(doc, dict) else {}
        new_doc = mutate_fn(doc)
        if new_doc is None:
            return None
        try:
            if _put_json_cas(key, new_doc, etag):
                return new_doc
        except Exception as e:
            print(f"[corpus-catalog] put failed {key}: {e}")
            return None
        time.sleep(min(0.15 * (2 ** attempt), 2.0) + random.uniform(0, 0.15))
    print(f"[corpus-catalog] gave up on {key} after {max_retries + 1} conflicts")
    return None


def _subject_page_key(skey):
    return f"{SUBJECT_PREFIX}{skey}.json"


def load_index(force=False):
    if not enabled():
        return {'version': VERSION, 'subjects': {}}
    now = time.time()
    with _lock:
        cached = _state['index']
        if cached is not None and not force and now - _state['index_ts'] < _INDEX_TTL_S:
            return cached
    try:
        doc, etag = _read_json_with_etag(INDEX_KEY)
    except Exception as e:
        print(f"[corpus-catalog] index load failed: {e}")
        doc, etag = None, None
    doc = doc if isinstance(doc, dict) else {}
    doc.setdefault('version', VERSION)
    doc.setdefault('subjects', {})
    with _lock:
        _state['index'] = doc
        _state['index_etag'] = etag
        _state['index_ts'] = now
    return doc


def load_subject_page(skey, force=False):
    if not skey or not enabled():
        return None
    now = time.time()
    with _lock:
        hit = _state['pages'].get(skey)
        if hit and not force and now - hit[1] < _PAGE_TTL_S:
            return hit[0]
    try:
        doc, _ = _read_json_with_etag(_subject_page_key(skey))
    except Exception as e:
        print(f"[corpus-catalog] page load failed {skey}: {e}")
        doc = None
    with _lock:
        _state['pages'][skey] = (doc, now)
        if len(_state['pages']) > 400:
            oldest = sorted(_state['pages'].items(), key=lambda kv: kv[1][1])[:100]
            for k, _ in oldest:
                _state['pages'].pop(k, None)
    return doc


def source_etag(s3_key):
    return (load_sources().get(s3_key) or {}).get('etag')


def upsert_source(product, s3_key, subject, facts, etag=None, aliases=()):
    """Record every fact a source object yields under its subject.
    Replaces that source's earlier facts (an in-place correction
    changes the object, so its facts change with it). Returns the
    subject key or '' when nothing was recorded."""
    if not enabled():
        return ''
    skey = subject_key(subject)
    if not skey or not facts:
        return ''
    facts = [f for f in facts if isinstance(f, dict)]
    now = _now_iso()
    alias_set = {str(subject).strip()} | {str(a).strip() for a in aliases if a}
    if not str(s3_key).startswith('thread/'):
        _leave_old_subjects([{'s3_key': s3_key, 'subject': subject}])

    def _mut_page(doc):
        doc.setdefault('subject_key', skey)
        al = set(doc.get('aliases') or []) | alias_set
        doc['aliases'] = sorted(a for a in al if a)[:40]
        doc['subject'] = best_display(doc['aliases'], str(subject).strip())
        kept = [f for f in (doc.get('facts') or [])
                if not (isinstance(f, dict) and (f.get('source') or {}).get('key') == s3_key)]
        kept.extend(facts)
        kept.sort(key=lambda f: (str(f.get('as_of') or ''), str(f.get('kind') or '')), reverse=True)
        doc['facts'] = kept[:600]
        doc['updated'] = now
        return doc

    page = _update_json(_subject_page_key(skey), _mut_page)
    if page is None:
        return ''

    def _mut_index(doc):
        doc.setdefault('version', VERSION)
        subs = doc.setdefault('subjects', {})
        ent = subs.get(skey) or {}
        ent['aliases'] = sorted(set(ent.get('aliases') or []) | alias_set)[:40]
        ent['subject'] = best_display(ent['aliases'], str(subject).strip())
        prods = set(ent.get('products') or []) | {product}
        ent['products'] = sorted(prods)
        ent['n_facts'] = len(page.get('facts') or [])
        ent['updated'] = now
        subs[skey] = ent
        doc.pop('sources', None)
        doc['updated'] = now
        return doc

    def _mut_sources(doc):
        doc[s3_key] = {'etag': etag or '', 'product': product, 'subject_key': skey,
                       'indexed_at': now}
        return doc

    _update_json(INDEX_KEY, _mut_index)
    if not str(s3_key).startswith('thread/'):
        _update_json(SOURCES_KEY, _mut_sources)
        # Dashboard figures outrank chat-stated ones (2026-10-06): a
        # ledger entry that now disagrees with this source is retired.
        try:
            import insights_ledger as _il
            _il.retire_conflicting_entries(str(subject), facts)
        except Exception as e:
            print(f"[corpus-catalog] ledger reconcile skipped: {e}")
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'].pop(skey, None)
    return skey


def bulk_upsert(records):
    """Sweep path: many sources at once. records = iterable of dicts
    {product, s3_key, subject, facts, etag, aliases}. One CAS write per
    subject page and ONE index write, instead of two per source."""
    if not enabled():
        return 0
    by_subject = {}
    records = [r for r in records if r and r.get('facts')]
    for r in records:
        skey = subject_key(r.get('subject'))
        if not skey:
            continue
        by_subject.setdefault(skey, []).append(r)
    if not by_subject:
        return 0
    _leave_old_subjects(records)
    now = _now_iso()
    page_counts = {}

    def _one_subject(skey, recs):
        subject = str(recs[0].get('subject') or '').strip()
        alias_set = set()
        for r in recs:
            alias_set.add(str(r.get('subject') or '').strip())
            alias_set |= {str(a).strip() for a in (r.get('aliases') or ()) if a}
        keys = {r['s3_key'] for r in recs}

        def _mut_page(doc, _recs=recs, _subject=subject, _aliases=alias_set, _keys=keys):
            doc.setdefault('subject_key', skey)
            doc['aliases'] = sorted(a for a in (set(doc.get('aliases') or []) | _aliases) if a)[:40]
            doc['subject'] = best_display(doc['aliases'], _subject)
            kept = [f for f in (doc.get('facts') or [])
                    if not (isinstance(f, dict) and (f.get('source') or {}).get('key') in _keys)]
            for r in _recs:
                kept.extend(f for f in r['facts'] if isinstance(f, dict))
            kept.sort(key=lambda f: (str(f.get('as_of') or ''), str(f.get('kind') or '')), reverse=True)
            doc['facts'] = kept[:600]
            doc['updated'] = now
            return doc

        page = _update_json(_subject_page_key(skey), _mut_page)
        if page is not None:
            page_counts[skey] = len(page.get('facts') or [])

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda kv: _one_subject(*kv), by_subject.items()))

    src_updates = {}

    def _mut_index(doc):
        doc.setdefault('version', VERSION)
        subs = doc.setdefault('subjects', {})
        doc.pop('sources', None)
        for skey, recs in by_subject.items():
            if skey not in page_counts:
                continue
            ent = subs.get(skey) or {}
            al = set(ent.get('aliases') or [])
            prods = set(ent.get('products') or [])
            for r in recs:
                al.add(str(r.get('subject') or '').strip())
                al |= {str(a).strip() for a in (r.get('aliases') or ()) if a}
                prods.add(r['product'])
                src_updates[r['s3_key']] = {'etag': r.get('etag') or '', 'product': r['product'],
                                            'subject_key': skey, 'indexed_at': now}
            ent['aliases'] = sorted(a for a in al if a)[:40]
            ent['subject'] = best_display(ent['aliases'], str(recs[0].get('subject') or '').strip())
            ent['products'] = sorted(prods)
            ent['n_facts'] = page_counts[skey]
            ent['updated'] = now
            subs[skey] = ent
        doc['updated'] = now
        return doc

    _update_json(INDEX_KEY, _mut_index, max_retries=10)

    def _mut_sources(doc):
        doc.update(src_updates)
        return doc

    _update_json(SOURCES_KEY, _mut_sources, max_retries=10)
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'] = {}
    try:
        import insights_ledger as _il
        for skey, recs in by_subject.items():
            facts = [f for r in recs for f in (r.get('facts') or [])]
            _il.retire_conflicting_entries(str(recs[0].get('subject') or ''), facts)
    except Exception as e:
        print(f"[corpus-catalog] ledger reconcile skipped: {e}")
    return len(page_counts)


def load_sources():
    """s3_key -> {etag, product, subject_key, indexed_at}. Sweep only."""
    try:
        doc, _ = _read_json_with_etag(SOURCES_KEY)
    except Exception as e:
        print(f"[corpus-catalog] sources load failed: {e}")
        doc = None
    return doc if isinstance(doc, dict) else {}


def forget_source(s3_key, sources=None):
    """A deleted object leaves the catalog, and so does a source whose
    identity moved to another subject (2026-10-21: 'Nip/Tuck on Hulu'
    from the Hulu page to the Nip/Tuck page). The old page drops the
    source's facts and the index entry follows (products and fact count
    recomputed; an emptied subject leaves the index)."""
    src = (sources if isinstance(sources, dict) else load_sources()).get(s3_key)
    if not src:
        return False
    skey = src.get('subject_key')
    left = {'facts': None}

    def _mut_page(doc):
        doc['facts'] = [f for f in (doc.get('facts') or [])
                        if (f.get('source') or {}).get('key') != s3_key]
        doc['updated'] = _now_iso()
        left['facts'] = list(doc['facts'])
        return doc

    if skey:
        _update_json(_subject_page_key(skey), _mut_page)

        def _mut_index(doc):
            subs = doc.setdefault('subjects', {})
            ent = subs.get(skey)
            if not isinstance(ent, dict):
                return doc
            facts = left['facts'] if left['facts'] is not None else []
            if not facts:
                subs.pop(skey, None)
            else:
                ent['products'] = sorted({str(f.get('product')) for f in facts if f.get('product')})
                ent['n_facts'] = len(facts)
                ent['updated'] = _now_iso()
                subs[skey] = ent
            doc['updated'] = _now_iso()
            return doc

        _update_json(INDEX_KEY, _mut_index)

    def _mut_sources(doc):
        doc.pop(s3_key, None)
        return doc

    _update_json(SOURCES_KEY, _mut_sources)
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'].pop(skey, None)
    return True


def _leave_old_subjects(records, sources=None):
    """Before an upsert: every record whose source was last filed under
    a DIFFERENT subject leaves that subject first, so a moved identity
    never leaves stale facts behind on the old page."""
    try:
        prev = sources if isinstance(sources, dict) else load_sources()
    except Exception:
        return 0
    moved = 0
    for r in records:
        if not isinstance(r, dict) or not r.get('s3_key'):
            continue
        old = (prev.get(r['s3_key']) or {}).get('subject_key')
        new = subject_key(r.get('subject'))
        if old and new and old != new:
            if forget_source(r['s3_key'], sources=prev):
                moved += 1
                print(f"[corpus-catalog] {r['s3_key']}: identity moved {old} -> {new}; left the old page")
    return moved


# ------------------------------------------------------------- consult
def _window_overlap(a, b):
    """Days of overlap between two {'start','end'} windows; None when
    either side is open-ended."""
    try:
        a0, a1 = _d(a.get('start')), _d(a.get('end'))
        b0, b1 = _d(b.get('start')), _d(b.get('end'))
    except Exception:
        return None
    if not (a0 and a1 and b0 and b1):
        return None
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days + 1)


def _d(s):
    s = str(s or '')[:10]
    return datetime.strptime(s, '%Y-%m-%d').date() if s else None


def same_window(a, b):
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    return (str(a.get('start') or '')[:10] == str(b.get('start') or '')[:10]
            and str(a.get('end') or '')[:10] == str(b.get('end') or '')[:10]
            and bool(a.get('start')))


def window_within(inner, outer):
    """True when ``inner`` sits inside ``outer`` (inclusive; identical
    windows count). Two windows that merely overlap (2025-01-01 to
    2025-12-31 against 2025-10-20 to 2026-10-20) are NOT a sub-window
    case: neither contains the other (2026-10-21, Jenna)."""
    if not (isinstance(inner, dict) and isinstance(outer, dict)):
        return False
    try:
        i0, i1 = _d(inner.get('start')), _d(inner.get('end'))
        o0, o1 = _d(outer.get('start')), _d(outer.get('end'))
    except Exception:
        return False
    if not (i0 and i1 and o0 and o1):
        return False
    return i0 >= o0 and i1 <= o1


def resolve_subject(name):
    """The catalog subject key for a name, through aliases."""
    skey = subject_key(name)
    if not skey:
        return ''
    idx = load_index()
    subs = idx.get('subjects') or {}
    if skey in subs:
        return skey
    fold = entity_fold(name)
    for k, ent in subs.items():
        for a in (ent.get('aliases') or []):
            if entity_fold(a) == fold:
                return k
    return ''


def anchors_for(subject, window=None, products=None, limit=60, with_ledger=True):
    """Everything the corpus already says about a subject, newest first,
    with the facts whose window overlaps `window` ranked first."""
    out = {'subject_key': '', 'subject': str(subject or '').strip(), 'facts': [],
           'products': [], 'ledger': []}
    if not enabled() or not str(subject or '').strip():
        return out
    try:
        skey = resolve_subject(subject)
        if skey:
            page = load_subject_page(skey) or {}
            facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)]
            if products:
                facts = [f for f in facts if f.get('product') in set(products)]
            # nothing set aside or retired reaches a read (2026-11-24)
            facts = [f for f in facts if not _fact_held(f, page.get('subject') or subject)]

            def _score(f):
                ov = 0
                if window and f.get('window'):
                    ov = _window_overlap(window, f['window']) or 0
                    if same_window(window, f['window']):
                        ov += 10_000
                return ov

            # newest first, then the window that matches the ask first
            facts.sort(key=lambda f: str(f.get('as_of') or ''), reverse=True)
            facts.sort(key=_score, reverse=True)
            out['subject_key'] = skey
            out['subject'] = best_display(page.get('aliases') or [], page.get('subject') or out['subject'])
            out['facts'] = facts[:limit]
            out['products'] = sorted({f.get('product') for f in facts if f.get('product')})
    except Exception as e:
        print(f"[corpus-catalog] anchors_for failed: {e}")
    try:
        out['related'] = related_subjects(subject, exclude=out.get('subject_key'))
    except Exception:
        out['related'] = []
    try:
        out['trends'] = trends_for(subject)
    except Exception:
        out['trends'] = []
    if with_ledger:
        try:
            _il = _ledger()
            led = _il.consult(subject=str(subject))   # held-back rows never reach a read (insights_ledger._feeds_reads)
            out['ledger'] = list(led.get('entries') or [])[:12]
            if led.get('block'):
                out['ledger_block'] = led['block']
        except Exception:
            pass
    return out


def related_subjects(name, exclude='', limit=5):
    """Catalog subjects whose key carries every token of this name
    (the 'Potential The Influencer Project Ticket Buyer' profiles for
    'The Influencer Project'). Headline facts only."""
    toks = [t for t in entity_fold(name).split() if len(t) > 1]
    if not toks:
        return []
    idx = load_index()
    out = []
    for k, ent in (idx.get('subjects') or {}).items():
        if k == exclude:
            continue
        ktoks = set(k.split('_'))
        if all(t in ktoks for t in toks):
            out.append(k)
    out = out[:limit]
    res = []
    for k in out:
        page = load_subject_page(k) or {}
        facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)
                 and f.get('kind') in ('audience_size', 'sample_size')][:4]
        if facts:
            res.append({'subject_key': k, 'subject': page.get('subject') or k, 'facts': facts})
    return res


def profile_anchor(anchors, window=None):
    """The profile-level size facts (sample, US audience, window) for
    the subject, preferring the one whose window matches."""
    best = None
    for f in anchors.get('facts') or []:
        if f.get('product') != 'profile' or f.get('kind') not in ('sample_size', 'audience_size'):
            continue
        key = f.get('source', {}).get('key')
        best = best or {}
        slot = best.setdefault(key, {'source_key': key, 'window': f.get('window'),
                                     'cut': bool(f.get('cut')), 'as_of': f.get('as_of')})
        slot[f['kind']] = f['value']
    if not best:
        return None
    cands = list(best.values())
    # the parent profile before any of its cuts; newest parent first
    cands.sort(key=lambda c: (c.get('cut', False), ''))
    if window:
        exact = [c for c in cands if same_window(window, c.get('window') or {})]
        if exact:
            return exact[0]
        cands.sort(key=lambda c: (c.get('cut', False),
                                  -(_window_overlap(window, c.get('window') or {}) or 0)))
    return cands[0]


def window_days(w):
    """Length of a {'start','end'} window in days; 0 when open-ended."""
    try:
        a, b = _d((w or {}).get('start')), _d((w or {}).get('end'))
    except Exception:
        return 0
    if not (a and b):
        return 0
    return (b - a).days + 1


def universe_floor(anchors, window, exclude_key=None):
    """The figure a Profile IQ Total Universe on this subject must sit at
    or above (2026-10-20, Jenna on the NCIS: New York sweep note: a
    subject the dashboard already describes never gets a universe
    smaller than what another product measured on a shorter window).

    A Total Universe is the subject's whole audience over its window.
    Every other product's US-people figure on a window CONTAINED in
    ``window`` describes a subset of that audience (a journey's end
    point, a partnership's reach), so the universe must clear the
    largest of them. Returns that fact as {'value', 'product', 'label',
    'note', 'window', 'source_key'} or None. Profile facts (the universe
    itself, its cuts, its other windows) and chat-stated figures never
    floor a build; a figure on a window that is not inside the build's
    (longer, or merely overlapping) is not a subset and is skipped
    (2026-10-21, Jenna: containment, never overlap)."""
    if not isinstance(window, dict) or not (window.get('start') and window.get('end')):
        return None
    mine = window_days(window)
    if mine <= 0:
        return None
    best = None
    for f in (anchors or {}).get('facts') or []:
        if not isinstance(f, dict):
            continue
        if f.get('product') in ('profile', 'chat') or f.get('kind') != 'audience_size' \
                or f.get('unit') != 'people':
            continue
        src = (f.get('source') or {}).get('key') or ''
        if exclude_key and src == exclude_key:
            continue
        fw = f.get('window') or {}
        if not (fw.get('start') and fw.get('end')):
            continue
        if not window_within(fw, window):
            continue
        try:
            v = float(f.get('value'))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        if best is None or v > best['value']:
            best = {'value': int(round(v)), 'product': f.get('product'), 'label': f.get('label'),
                    'note': f.get('note') or f.get('product'), 'window': fw, 'source_key': src}
    return best


def prior_journey(anchors, window=None):
    """A prior Digital Journey on the subject (same window when one
    exists), as {source_key, window, stages: {label: count}, total}."""
    by_key = {}
    for f in anchors.get('facts') or []:
        if f.get('product') != 'journey':
            continue
        key = f.get('source', {}).get('key')
        slot = by_key.setdefault(key, {'source_key': key, 'window': f.get('window'),
                                       'stages': {}, 'total': None, 'as_of': f.get('as_of')})
        if f.get('kind') == 'stage_count':
            slot['stages'][f['label']] = f['value']
        elif f.get('kind') == 'audience_size':
            slot['total'] = f['value']
    if not by_key:
        return None
    cands = list(by_key.values())
    if window:
        exact = [c for c in cands if same_window(window, c.get('window') or {})]
        if exact:
            exact.sort(key=lambda c: str(c.get('as_of') or ''), reverse=True)
            return exact[0]
    cands.sort(key=lambda c: str(c.get('as_of') or ''), reverse=True)
    return cands[0]


TRENDS_KEY = 'system/corpus_catalog/trends_latest.json'


def write_trends_latest(day, entries):
    """entries: {subject_key: [{source, label, rank, title, as_of}]}
    for the latest Trends IQ snapshot day. One document, replaced
    whole; read lazily by anchors_for."""
    doc = {'day': day, 'updated': _now_iso(), 'subjects': entries}
    if _test_without_a_store():
        return False
    try:
        _client().put_object(Bucket=S3_BUCKET, Key=TRENDS_KEY,
                             Body=json.dumps(doc, separators=(',', ':')).encode('utf-8'),
                             ContentType='application/json')
        with _lock:
            _state['trends'] = (doc, time.time())
        return True
    except Exception as e:
        print(f"[corpus-catalog] trends write failed: {e}")
        return False


def trends_for(subject):
    """[{source, label, rank, title, as_of}] for the subject on the
    latest Trends IQ day, or []."""
    if not enabled():
        return []
    skey = subject_key(subject)
    if not skey:
        return []
    now = time.time()
    with _lock:
        hit = _state.get('trends')
    if not hit or now - hit[1] > _PAGE_TTL_S:
        try:
            doc, _ = _read_json_with_etag(TRENDS_KEY)
        except Exception:
            doc = None
        hit = (doc if isinstance(doc, dict) else {}, now)
        with _lock:
            _state['trends'] = hit
    return list(((hit[0] or {}).get('subjects') or {}).get(skey) or [])


def _fmt_value(f):
    v = f.get('value')
    u = f.get('unit')
    try:
        if u == 'pct':
            return f"{float(v):.1f}%"
        if u == 'usd':
            return f"${float(v):,.2f}"
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_window(w):
    if not isinstance(w, dict) or not (w.get('start') or w.get('end')):
        return ''
    return f" [{w.get('start') or '?'} to {w.get('end') or '?'}]"


def anchors_block(anchors, max_lines=40):
    """Prompt block: the figures already published on this subject.
    Binding: a new read must agree with these where it overlaps, and
    must sit inside them for a sub-window."""
    facts = list((anchors or {}).get('facts') or [])
    lines = []
    for f in facts[:max_lines]:
        lines.append(f"- {f.get('note') or f.get('product')}: {f.get('label')} = "
                     f"{_fmt_value(f)}{_fmt_window(f.get('window'))}")
    for tr in ((anchors or {}).get('trends') or [])[:8]:
        lines.append(f"- Trends IQ ({tr.get('as_of')}): {tr.get('title')} ranks #{tr.get('rank')} on {tr.get('label')}")
    for rel in (anchors or {}).get('related') or []:
        for f in rel.get('facts') or []:
            lines.append(f"- RELATED {rel.get('subject')}: {f.get('label')} = "
                         f"{_fmt_value(f)}{_fmt_window(f.get('window'))}")
    led = (anchors or {}).get('ledger_block') or ''
    if not lines and not led:
        return ''
    head = ("PUBLISHED FIGURES ON THIS SUBJECT (binding). Crosswalk already "
            "shows these on the dashboard or stated them in chat. A new read "
            "must agree with them where the windows overlap, must sit inside "
            "them for a sub-window, and must reuse the same partitions and "
            "names. Never contradict them; if the ask needs a different window, "
            "derive from these and say which window you are on. Dashboard "
            "figures (Profile IQ, Digital Journey IQ, Attribution IQ, Brand "
            "Partnership IQ) win over chat-stated figures when they disagree.\n")
    body = '\n'.join(lines)
    if led:
        body = (body + '\n' if body else '') + led
    return head + body


# ---------------------------------------------------------------- hooks
def extract_profile_from_s3(s3_key, display_name='', user='', etag=None, head_bytes=16384):
    """Read-only half of index_profile_from_s3: the record bulk_upsert
    takes, or None."""
    try:
        s3 = _client()
        kw = dict(Bucket=S3_BUCKET, Key=s3_key)
        if head_bytes:
            kw['Range'] = f'bytes=0-{head_bytes - 1}'
        resp = s3.get_object(**kw)
        text = resp['Body'].read().decode('utf-8', errors='replace')
        if head_bytes:
            text = text[:text.rfind('\n')] if '\n' in text else text
        et = etag or (resp.get('ETag') or '').strip('"')
        subject, facts = facts_from_profile_csv_text(s3_key, display_name, text, user=user)
        if head_bytes and not any(f.get('kind') == 'sample_size' for f in facts):
            # Rows sorted by Column (ACCESSORIES first) or a very long
            # BRAND INPUT push the size rows past the head: read it all.
            resp = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
            text = resp['Body'].read().decode('utf-8', errors='replace')
            subject, facts = facts_from_profile_csv_text(s3_key, display_name, text, user=user)
        if not facts:
            return None
        return {'product': 'profile', 's3_key': s3_key, 'subject': subject, 'facts': facts,
                'etag': et, 'aliases': [display_name] if display_name else []}
    except Exception as e:
        print(f"[corpus-catalog] profile extract failed {s3_key}: {e}")
        return None


def index_profile_from_s3(s3_key, display_name='', user='', etag=None, head_bytes=16384):
    """Catalog a profile CSV from its first bytes (BRAND INPUT, SAMPLE
    SIZE rows sit at the top; a full read when they do not). Returns
    subject key or ''."""
    if not enabled():
        return ''
    rec = extract_profile_from_s3(s3_key, display_name, user=user, etag=etag,
                                  head_bytes=head_bytes)
    if not rec:
        return ''
    try:
        return upsert_source('profile', rec['s3_key'], rec['subject'], rec['facts'],
                             etag=rec.get('etag'), aliases=rec.get('aliases') or ())
    except Exception as e:
        print(f"[corpus-catalog] profile index failed {s3_key}: {e}")
        return ''


def index_journey(key, payload, user='', etag=None):
    try:
        subject, facts = facts_from_journey(key, payload, user=user)
        if not subject:
            return ''
        return upsert_source('journey', key, subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] journey index failed {key}: {e}")
        return ''


def index_attribution(slug, assets, fit, user='', etag=None):
    try:
        subject, facts = facts_from_attribution(slug, assets, fit, user=user)
        if not subject:
            return ''
        return upsert_source('attribution', f"intent/{slug}/", subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] attribution index failed {slug}: {e}")
        return ''


def index_bpiq(key, payload, user='', etag=None):
    try:
        subject, facts = facts_from_bpiq(key, payload, user=user)
        if not subject:
            return ''
        return upsert_source('bpiq', key, subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] bpiq index failed {key}: {e}")
        return ''


# ------------------------------------------------------- what a turn is about
# A turn's figures are filed under what the turn is about (2026-11-25, after
# Armine's film reads were filed under Prime Video, MovieSphere+, Tyler Perry,
# Target and Martin Lawrence because those names appeared in the text): its
# bound subject first, then its base profile, then the thread's subject when
# the turn names it; a comparison files each figure under the title its
# sentence names. With none of these, a figure files only under a known
# subject that opens its sentence. A name in passing (after on, in, via,
# than ..., or after the figure, or a common word like 'target') never counts.
_TURN_SENT_RX = re.compile(r'(?<=[.!?])\s+|\n+')
_PASSING_RX = re.compile(r"\S.*?\b(?:on|in|via|from|across|at|to|than|vs\.?|versus|with|through|inside|within|by|over|"
                         r"under|like|including|against|into|onto|starring|featuring|beside|after|before)"
                         r"\s+(?:the\s+)?$", re.I | re.S)   # mid-sentence only: 'For X,' / 'On X:' open the topic
_COMPARE_SPLIT_RX = re.compile(r'\s+(?:vs\.?|versus)\s+', re.I)
_COMPARE_ASK_RX = re.compile(r'\b(?:vs\.?|versus|compare|compared|comparison|against|side by side)\b', re.I)
_NOT_A_SUBJECT_RX = re.compile(r'^(?:on\s+)?including\b|^(?:unique|total)\s+(?:u\.?s\.?\s+)?(?:viewers|views|plays)\b', re.I)


def _plain(s):
    return str(s or '').replace('\u2019', "'").replace('\u2018', "'")


def _clean_subject(name):
    s = ' '.join(_plain(name).split())
    if not s or len(s) > 200 or _NOT_A_SUBJECT_RX.search(s) or not subject_key(s):
        return ''
    return s


def _name_spans(text, name):
    """Where `name` stands in `text` as a name: word-bounded, case-blind,
    and written with a capital (so 'a target audience' is not Target)."""
    nm = _plain(name).strip()
    if len(nm) < 2:
        return []
    t = _plain(text)
    out = []
    for m in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(nm) + r'(?![A-Za-z0-9])', t, re.I):
        if t[m.start()].isupper() or t[m.start()].isdigit():
            out.append(m)
    return out


def names_subject(text, name):
    return bool(_name_spans(text, name))


def leads_figure(sentence, name, names=()):
    """True when `name` is the first known name of the sentence, before its
    first figure and never in passing (after on, in, via, than ...)."""
    first = _NUM_SENT_RX.search(_plain(sentence))
    if not first:
        return False
    spans = []
    for nm in set([name] + list(names or [])):
        for m in _name_spans(sentence, nm):
            if m.start() < first.start() and not _PASSING_RX.search(_plain(sentence)[:m.start()]):
                spans.append((m.start(), -len(nm), nm))
    if not spans:
        return False
    return min(spans)[2].lower() == _plain(name).strip().lower()


def compared_subjects(name):
    """['Moonfall', 'Dogma'] for 'Moonfall vs Dogma'; the titles after the
    colon of a list ('YouTube Movies & TV Free with ads: Forever My Girl,
    Moonfall, ...'); [] for one subject ('Anne Hathaway x Shiseido' is one
    pairing)."""
    s = _clean_subject(name)
    if not s:
        return []
    bare = re.sub(r'\([^)]*\)?', ' ', s)    # '(61 titles: 21 films, 40 series)' is a description, never a list
    if _COMPARE_SPLIT_RX.search(bare):
        parts = _COMPARE_SPLIT_RX.split(bare)
    elif ':' in bare and ',' in bare.split(':', 1)[1]:
        parts = re.split(r',\s*|\s+and\s+', bare.split(':', 1)[1])
    else:
        return []
    parts = [_clean_subject(p.strip(' .;:')) for p in parts]
    parts = [p for p in parts if p and not p[0].isdigit()]
    return parts if len(parts) >= 2 else []


def _fact_sentence(f):
    return str(f.get('note') or f.get('label') or '')


def _keep_compared(part, parts):
    others = [p for p in parts if p != part]

    def keep(f):
        s = _fact_sentence(f)
        return names_subject(s, part) or not any(names_subject(s, o) for o in others)
    return keep


def _keep_led_by(name, names):
    def keep(f):
        return leads_figure(_fact_sentence(f), name, names)
    return keep


def turn_subjects(text, *, meta=None, turn_subject='', question='', carried='', base_subject='', known=()):
    """[(subject, keep)] the turn's figures are filed under: `keep` is None
    (every figure) or a test on each fact. `known` are catalog subject names
    (display names and aliases). [] when nothing says what the turn is about."""
    meta = meta if isinstance(meta, dict) else {}
    bound = _clean_subject(meta.get('subject') or turn_subject or '') or _clean_subject(base_subject)
    if not bound and carried and _clean_subject(carried) and names_subject(text, carried):
        bound = _clean_subject(carried)
    if bound:
        parts = compared_subjects(bound)
        if parts:
            named = [p for p in parts if names_subject(text, p)]
            if named:
                return [(p, _keep_compared(p, parts)) for p in named]
        return [(bound, None)]
    low = _plain(text).lower()
    present = [k for k in known if k and _plain(k).lower() in low and names_subject(text, k)]
    asked = [k for k in present if any(not _PASSING_RX.search(_plain(question)[:m.start()])
                                       for m in _name_spans(question, k))]   # 'on MovieSphere+' is never a compared title
    if len(asked) >= 2 and _COMPARE_ASK_RX.search(_plain(question)):
        return [(k, _keep_compared(k, asked)) for k in asked]
    sents = [s for s in _TURN_SENT_RX.split(_plain(text)) if _NUM_SENT_RX.search(s)]
    out, seen = [], set()
    for k in present:
        if subject_key(k) in seen:
            continue
        if any(leads_figure(s, k, present) for s in sents):
            seen.add(subject_key(k))
            out.append((k, _keep_led_by(k, present)))
    return out


def subject_for_source(s3_key, sources=None):
    """The catalog subject a profile (or any indexed source) is filed under,
    '' when it is not indexed."""
    if not s3_key:
        return ''
    try:
        src = (sources if sources is not None else load_sources()) or {}
        skey = str((src.get(str(s3_key)) or {}).get('subject_key') or '')
        if not skey:
            return ''
        ent = (load_index().get('subjects') or {}).get(skey) or {}
        return str(ent.get('subject') or skey.replace('_', ' '))
    except Exception:
        return ''


def unfile_source(key, skey):
    """Take one source's facts off one subject page (a turn filed under a
    subject it is not about), the index line following the page. The source
    is not marked retired: it stays filed wherever it belongs. Returns the
    number of facts removed."""
    if not enabled() or not key or not skey:
        return 0
    left = {'facts': None, 'n': 0}

    def _mut(doc, _key=key):
        facts = [f for f in (doc.get('facts') or []) if isinstance(f, dict)]
        kept = [f for f in facts if (f.get('source') or {}).get('key') != _key]
        if len(kept) == len(facts):
            return None
        left['n'] = len(facts) - len(kept)
        doc['facts'] = kept
        doc['updated'] = _now_iso()
        left['facts'] = list(kept)
        return doc

    _update_json(_subject_page_key(skey), _mut)
    if left['facts'] is not None:
        def _mut_index(doc, _skey=skey, _facts=left['facts']):
            ent = (doc.get('subjects') or {}).get(_skey)
            if not isinstance(ent, dict):
                return None
            if not _facts:
                doc['subjects'].pop(_skey, None)
            else:
                ent['products'] = sorted({str(f.get('product')) for f in _facts if f.get('product')})
                ent['n_facts'] = len(_facts)
                ent['updated'] = _now_iso()
            doc['updated'] = _now_iso()
            return doc

        _update_json(INDEX_KEY, _mut_index)
        with _lock:
            _state['index_ts'] = 0.0
            _state['pages'].pop(skey, None)
    return left['n']


def record_answer(subject, text, user='', thread_id='', product='chat', keep=None):
    """Bank the figures a delivered reply states, under its subject. Never
    a reply the insights ledger holds back (Jenna 2026-11-24: a set-aside
    answer never feeds a later read, for anyone): its words were set aside,
    or it restates figures of a retired read. The write hook and the
    thread sweep both come through here. `keep` (the sweep's, see
    turn_subjects) keeps only the figures that are about this subject."""
    if not enabled() or not str(subject or '').strip():
        return ''
    try:
        facts = facts_from_answer(subject, text, user=user, thread_id=thread_id,
                                  source_label=product)
        if callable(keep):
            facts = [f for f in facts if keep(f)]
        if not facts:
            return ''
        key = f"thread/{user}/{thread_id}/{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:10]}"
        if is_retired_source(key):
            return ''       # a reply that was set aside is never banked again (the sweep re-reads its thread)
        if _held_back(text, subject, user=user, thread_id=thread_id):
            return ''       # its words were set aside, or it restates a retired read's figures (2026-11-24)
        for f in facts:
            f['source']['key'] = key
        return upsert_source(product, key, subject, facts)
    except Exception as e:
        print(f"[corpus-catalog] record_answer failed: {e}")
        return ''


def _ledger():
    """The insights ledger (what was set aside, what was retired), or None.
    The catalog runs in the dashboard, beside insights_ledger.py, and on the
    build server, where the dashboard sits one level down in bg-webapp/."""
    try:
        import insights_ledger as _il
        return _il
    except Exception:
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for d in (root, os.path.join(root, 'bg-webapp')):
        if os.path.isfile(os.path.join(d, 'insights_ledger.py')) and d not in sys.path:
            sys.path.append(d)
    try:
        import insights_ledger as _il
        return _il
    except Exception:
        return None


def _held_back(text, subject, user='', thread_id=''):
    """insights_ledger.held_back for a reply about to be banked: '' when it
    may be, else the reason."""
    il = _ledger()
    fn = getattr(il, 'held_back', None)
    if not callable(fn):
        return ''
    try:
        return fn(str(text or ''), subject=str(subject or '') or None, user=user or None,
                  thread=thread_id or None) or ''
    except Exception:
        return ''


def _fact_held(f, subject):
    """A stored fact no read may lean on: its reply was set aside, or it is
    a chat figure a retired read carried (a count of 10,000 or more on its
    own, any other figure beside the name the retired read gave it)."""
    src = str((f.get('source') or {}).get('key') or '')
    if src and is_retired_source(src):
        return True
    if f.get('kind') != 'stated_number':
        return False
    il = _ledger()
    fn = getattr(il, 'quoted_retired_figures', None)
    vt = getattr(il, 'value_tokens', None)
    if not callable(fn) or not callable(vt):
        return False
    try:
        tok = vt(f.get('value'))
        return bool(tok) and bool(fn(str(f.get('note') or f.get('label') or ''), subject,
                                     min_weight=0.5, must_include=(tok,)))
    except Exception:
        return False


def retired_sources(force=False):
    """{source key: {'ts', 'by'}} for every reply that was set aside.
    Small, cached in process like the index. {} on any trouble."""
    if not enabled():
        return {}
    now = time.time()
    with _lock:
        cached = _state.get('retired')
        if cached is not None and not force and now - _state.get('retired_ts', 0.0) < _INDEX_TTL_S:
            return cached
    try:
        doc, _ = _read_json_with_etag(RETIRED_KEY)
    except Exception as e:
        print(f"[corpus-catalog] retired sources load failed: {e}")
        with _lock:
            return _state.get('retired') or {}
    doc = doc if isinstance(doc, dict) else {}
    with _lock:
        _state['retired'] = doc
        _state['retired_ts'] = now
    return doc


def is_retired_source(key):
    try:
        return bool(key) and str(key) in retired_sources()
    except Exception:
        return False


def _note_retired(key, by='reader'):
    """Remember that a reply was set aside, so neither the write hook
    nor the sweep (which re-reads a thread whenever it changes) banks
    its figures again. The thread itself is never touched."""
    stamp = {'ts': _now_iso(), 'by': str(by or 'reader')[:40]}

    def _mut(doc):
        if key in doc:
            return None
        doc[key] = stamp
        return doc

    try:
        _update_json(RETIRED_KEY, _mut)
    except Exception as e:
        print(f"[corpus-catalog] retired source not noted {key}: {e}")
    with _lock:
        _state['retired'] = None
        _state['retired_ts'] = 0.0


def retire_answer(user, thread_id, text, subject=None, by='reader'):
    """A reply that was set aside (2026-10-06, the learning loop: one the
    user rejected; 2026-11-18: one an operator set aside): its banked
    figures leave the catalog so the next answer cannot lean on them,
    and it stays out (the sweep re-reads its thread on every change and
    would bank it again). Returns the number of facts removed."""
    if not enabled() or not str(text or '').strip():
        return 0
    try:
        key = f"thread/{user}/{thread_id}/{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:10]}"
        _note_retired(key, by=by)
        idx = load_index(force=True)
        keys = [subject_key(subject)] if subject else list((idx.get('subjects') or {}).keys())
        removed = 0
        for skey in keys:
            if not skey:
                continue
            page = load_subject_page(skey, force=True) or {}
            facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)]
            hit = [f for f in facts if (f.get('source') or {}).get('key') == key]
            if not hit:
                continue

            left = {'facts': None}

            def _mut(doc, _key=key, _left=left):
                doc['facts'] = [f for f in (doc.get('facts') or [])
                                if (f.get('source') or {}).get('key') != _key]
                doc['updated'] = _now_iso()
                _left['facts'] = list(doc['facts'])
                return doc

            _update_json(_subject_page_key(skey), _mut)
            if left['facts'] is not None:
                # the index line follows the page, as it does when a source is forgotten
                def _mut_index(doc, _skey=skey, _facts=left['facts']):
                    ent = (doc.get('subjects') or {}).get(_skey)
                    if not isinstance(ent, dict):
                        return None
                    if not _facts:
                        doc['subjects'].pop(_skey, None)
                    else:
                        ent['products'] = sorted({str(f.get('product')) for f in _facts if f.get('product')})
                        ent['n_facts'] = len(_facts)
                        ent['updated'] = _now_iso()
                    doc['updated'] = _now_iso()
                    return doc

                _update_json(INDEX_KEY, _mut_index)
                with _lock:
                    _state['index_ts'] = 0.0
            removed += len(hit)
            with _lock:
                _state['pages'].pop(skey, None)
            if subject:
                break
        if removed:
            print(f"[corpus-catalog] retired {removed} fact(s) from a reply that was set aside ({user}/{thread_id})")
        return removed
    except Exception as e:
        print(f"[corpus-catalog] retire_answer failed: {e}")
        return 0


def record_answer_async(subject, text, user='', thread_id='', product='chat'):
    t = threading.Thread(target=record_answer, args=(subject, text, user, thread_id, product),
                         daemon=True)
    t.start()
    return t
