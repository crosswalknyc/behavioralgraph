"""Pre-banking verification for generated reads (2026-08-28, p3-verify).

Every fresh generated read passes three checks after coherence
enforcement and before it banks into the insights ledger:

1. ANCHOR RECOMPUTE - when the read's base is a profile, at least one
   concrete measured figure cited in the reply (a penetration, an
   index, a demo share) is recomputed from the base file itself and
   must match within rounding. The base rows come from the nightly
   precomputed index table when its profile ETag still matches, else
   from the live CSV.
2. LEDGER COHERENCE - the new read's metrics are compared against the
   subject's existing ledger entries for the same family, cohort and
   window (delivered_artifact provenance held to the tightest band).
   A direct numeric contradiction fails the check.
3. SCRUB RESIDUE - the reply has already been through scrub_user_text;
   this check fails only when internal vocabulary or banned characters
   survived the mechanical scrub (which means the model must rewrite,
   not just re-replace).

The caller (app._pm_generate_read_core) runs ONE self-revision loop on
failure: the findings are appended to the generation prompt, the model
regenerates, and the result re-verifies. A second failure holds the
read (never banked, never delivered).

All comparison logic here is pure arithmetic on plain dicts so the
whole pass is testable without S3 or a model key. Only
load_base_lookup touches the network.
"""

import re
import time

# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------
# The anchor pass distinguishes three zones per bound claim:
#   anchored      - within rounding of a base row (tolerance from the
#                   quoted precision plus a small slack)
#   contradiction - far outside any base row for that label (both an
#                   absolute and a relative margin must be exceeded, so
#                   a 1-in-rounding miss can never hold a read)
#   ambiguous     - between the two; counts as neither
PCT_SLACK = 0.3            # added to the half-unit rounding tolerance
PCT_HARD_ABS = 2.0         # percentage points
PCT_HARD_REL = 0.12
IDX_SLACK = 2.0            # index points added to rounding tolerance
IDX_HARD_ABS = 12.0        # index points
IDX_HARD_REL = 0.08

# Ledger coherence bands (new value vs stored value, same metric name,
# same family + cohort + compatible window).
LEDGER_PCT_ABS = 2.5
LEDGER_PCT_REL = 0.12
LEDGER_COUNT_REL = 0.18
# delivered_artifact provenance: figures a client already holds.
LEDGER_PCT_ABS_DELIVERED = 1.5
LEDGER_PCT_REL_DELIVERED = 0.08
LEDGER_COUNT_REL_DELIVERED = 0.12

MAX_CLAIMS = 40
MAX_FINDINGS = 6

_PCT_UNIT_TOKENS = ('pct', 'percent', '%', 'share')

# Residual internal vocabulary that must never survive into a banked
# reply. The mechanical scrub already ran; anything matching here means
# the model itself has to rewrite the sentence.
_RESIDUAL_BANNED = (
    (re.compile(r'\b(claude|anthropic|openai|chatgpt|gpt-\d|llm|'
                r'language model)\b', re.I), 'names the model'),
    (re.compile(r'\bas an ai\b', re.I), 'speaks as an AI'),
    (re.compile(r'\b(synthesi[sz]\w*|synthetic|synth)\b', re.I),
     'internal generation vocabulary'),
    (re.compile(r'\b(hostmap|panelists?|clickhouse|hetzner|'
                r'post-generation enforcer\w*)\b', re.I),
     'internal infrastructure vocabulary'),
    (re.compile(r'\bweb[ -]?search(es|ed|ing)?\b', re.I),
     'names the research mechanism'),
    (re.compile(r'[\u2014\u2013]'), 'em or en dash'),
    (re.compile(r'\bhouseholds?\b(?!\s+income)', re.I),
     'household noun (counts are individual-level)'),
)

_LABEL_STOPWORDS = {
    'the', 'a', 'an', 'and', 'with', 'while', 'where', 'which', 'that',
    'its', 'their', 'this', 'these', 'those', 'both', 'also', 'but',
    'or', 'as', 'at', 'in', 'on', 'for', 'of', 'to', 'by',
}

# Labels that describe the audience itself, not a base row. They must
# never bind to brand/demo rows: on 2026-08-28 the Shark Tank income
# ask captured 'audience and an' from prose, containment-matched the
# 4-char key 'audi', and verified the income-tilt claim against the
# AUDI car brand row (131 vs 106.3 -> false hold).
_GENERIC_LABEL_NORMS = frozenset({
    'audience', 'audiences', 'viewer', 'viewers', 'fan', 'fans',
    'fanbase', 'base', 'cohort', 'universe', 'population', 'genpop',
    'generalpopulation', 'index', 'tilt', 'share', 'reach', 'profile',
    'file', 'subject', 'read', 'people', 'adults', 'household',
    'households', 'income', 'group', 'segment', 'us',
})

_NORM_RX = re.compile(r'[^a-z0-9]+')


def _norm(label):
    return _NORM_RX.sub('', str(label or '').lower())


def _trim_label(label):
    """Last few meaningful words of a regex-captured label window,
    stopwords stripped from BOTH ends ('the audience and an' ->
    'audience', not 'audience and an')."""
    words = [w for w in re.split(r'\s+', str(label or '').strip()) if w]
    while words and words[0].lower() in _LABEL_STOPWORDS:
        words.pop(0)
    while words and words[-1].lower() in _LABEL_STOPWORDS:
        words.pop()
    return ' '.join(words[-4:])


def _dp(value, text=None):
    """Decimal places the figure was quoted at (drives the rounding
    tolerance)."""
    if text is not None and '.' in str(text):
        return min(len(str(text).split('.', 1)[1]), 4)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if abs(v - round(v)) < 1e-9:
        return 0
    if abs(v * 10 - round(v * 10)) < 1e-6:
        return 1
    return 2


# ---------------------------------------------------------------------------
# Base lookup (anchor source)
# ---------------------------------------------------------------------------

def build_base_lookup(index_doc, genpop=None):
    """Plain lookup dict from a nightly profile index doc:
    {'brands': {norm: [(category, bp, index_or_None)]},
     'demos':  {norm: [(category, bp, genpop_bp_or_None, label)]},
     'name': ...}. `genpop` (optional {(cat, brand_norm): bp}) prices
    demo buckets against Gen Pop so composite tilts (e.g. the $100K+
    income index) are recomputable."""
    if not isinstance(index_doc, dict):
        return None
    brands, demos = {}, {}
    for cat, block in (index_doc.get('categories') or {}).items():
        for row in (block or {}).get('rows') or []:
            try:
                label, bp, idx = row[0], float(row[1]), row[2]
            except (TypeError, ValueError, IndexError):
                continue
            bn = _norm(label)
            if bn:
                brands.setdefault(bn, []).append(
                    (cat, bp, float(idx) if idx is not None else None))
    for row in (index_doc.get('purchase_index') or []):
        try:
            label, bp, idx = row[0], float(row[1]), row[2]
        except (TypeError, ValueError, IndexError):
            continue
        bn = _norm(label)
        if bn:
            brands.setdefault(bn, []).append(
                ('PURCHASE', bp, float(idx) if idx is not None else None))
    for cat, rows in (index_doc.get('demos') or {}).items():
        for row in (rows or []):
            try:
                bucket, bp = row[0], float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            bn = _norm(bucket)
            if bn:
                gp = _genpop_bp_for(genpop, cat, bucket)
                demos.setdefault(bn, []).append(
                    (cat, bp, gp, str(bucket)))
    if not brands and not demos:
        return None
    return {'name': index_doc.get('name') or '', 'source': 'index',
            'brands': brands, 'demos': demos}


def _genpop_bp_for(genpop, cat, label):
    """Gen Pop penetration for a (category, label) pair, tolerant of
    the two key shapes the genpop map is built with."""
    if not genpop:
        return None
    try:
        import prometheus_analysis as pma
        keys = ((str(cat).strip().upper(), pma._norm_brand(str(label))),
                (str(cat).strip().upper(), _norm(label)))
    except Exception:
        keys = ((str(cat).strip().upper(), _norm(label)),)
    for k in keys:
        gp = genpop.get(k)
        if gp is not None:
            try:
                return float(gp)
            except (TypeError, ValueError):
                return None
    return None


def lookup_from_frame(df, name, genpop=None):
    """CSV fallback: the same lookup dict built from the live profile
    frame (used when the nightly index is stale or missing)."""
    import prometheus_analysis as pma
    bp_c = pma._bp_col(df)
    brands, demos = {}, {}
    genpop = genpop or {}
    for cat, grp in df.groupby('Column', sort=False):
        catU = pma._norm_cat(cat)
        if not catU or catU in pma.METADATA_COLS:
            continue
        is_demo = catU in pma.DEMO_COLS
        for label, bpv in zip(grp['Value'].tolist(), grp[bp_c].tolist()):
            v = pma._parse_bp(bpv)
            label = str(label or '').strip()
            if v is None or not label:
                continue
            bn = _norm(label)
            if not bn:
                continue
            if is_demo:
                gp = _genpop_bp_for(genpop, catU, label)
                demos.setdefault(bn, []).append((catU, v, gp, label))
            else:
                gp = genpop.get((catU, pma._norm_brand(label)))
                idx = (round(v / gp * 100.0, 1)
                       if gp and gp >= 0.01 else None)
                brands.setdefault(bn, []).append((catU, v, idx))
    if not brands and not demos:
        return None
    return {'name': name or '', 'source': 'csv',
            'brands': brands, 'demos': demos}


def load_base_lookup(s3_client, bucket, s3_key, name=''):
    """Anchor source for one base profile: the nightly index table when
    its stored ETag still matches the live profile object, else the
    CSV. None when the base is not a loadable profile."""
    key = str(s3_key or '')
    if not key.lower().endswith('.csv'):
        return None
    import prometheus_analysis as pma
    live_etag = None
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        live_etag = (head.get('ETag') or '').strip('"')
    except Exception:
        return None
    genpop = None
    try:
        genpop = pma.load_genpop_map(s3_client, bucket)
    except Exception:
        genpop = None
    try:
        doc = pma.load_profile_index(s3_client, bucket, key)
        if isinstance(doc, dict) and live_etag \
                and doc.get('etag') == live_etag:
            lk = build_base_lookup(doc, genpop=genpop)
            if lk:
                return lk
    except Exception:
        pass
    try:
        df, _etag = pma.load_profile_df(s3_client, bucket, key)
        return lookup_from_frame(df, name, genpop)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

_IDX_TEXT_RX = (
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"index(?:es)?\s+(?:of\s+|at\s+|is\s+|to\s+)?"
               r"(?P<val>\d{2,4})(?![\d.%])"),
    re.compile(r"index(?:es)?\s+(?:of\s+|at\s+)?(?P<val>\d{2,4})\s+"
               r"(?:for|on|against)\s+"
               r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40})"),
)
_PCT_TEXT_RX = (
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"(?:at|reaches|hits|sits at|lands at|shows|measures)\s+"
               r"(?P<val>\d{1,2}(?:\.\d{1,2})?)%"),
    re.compile(r"(?P<label>[A-Za-z$][A-Za-z0-9&'\.\+\-,$ ]{1,40}?)\s+"
               r"pen(?:etration)?\s+(?:of\s+|at\s+|is\s+)?"
               r"(?P<val>\d{1,2}(?:\.\d{1,2})?)%"),
)
_DEMO_TEXT_RX = (
    re.compile(r"(?P<val>\d{1,2}(?:\.\d)?)%\s+"
               r"(?P<label>female|male|women|men)\b", re.I),
    re.compile(r"\b(?P<label>female|male|women|men)\b[^.\d%]{0,16}?"
               r"(?P<val>\d{1,2}(?:\.\d)?)%", re.I),
)
_DEMO_ALIASES = {'women': 'female', 'men': 'male'}


def extract_claims(reply, res):
    """Concrete measured figures the reply commits to, as claims:
    {'kind': 'pct'|'index'|'demo', 'label', 'value', 'dp', 'src'}."""
    claims, seen = [], set()

    def _add(kind, label, value, dp, src):
        label = _trim_label(label)
        ln = _norm(label)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if not ln or len(ln) < 3:
            return
        if kind in ('pct', 'demo') and not (0.0 < v <= 100.0):
            return
        if kind == 'index' and not (25.0 <= v <= 2500.0):
            return
        dk = (kind, ln, round(v, 4))
        if dk in seen or len(claims) >= MAX_CLAIMS:
            return
        seen.add(dk)
        claims.append({'kind': kind, 'label': label, 'value': v,
                       'dp': dp, 'src': src})

    res = res or {}
    for row in ((res.get('breakdown') or {}).get('rows') or []):
        if not isinstance(row, dict):
            continue
        pen = row.get('penetration_pct')
        if isinstance(pen, (int, float)):
            _add('pct', row.get('label'), pen, _dp(pen), 'breakdown')
    for m in (res.get('metrics') or []):
        if not isinstance(m, dict):
            continue
        name = str(m.get('name') or '')
        label = str(m.get('label') or name)
        unit = str(m.get('unit') or '').lower()
        val = m.get('value')
        if not isinstance(val, (int, float)):
            continue
        nl = (name + ' ' + label).lower()
        if 'index' in nl:
            _add('index', label or name, val, _dp(val), 'metrics')
        elif any(t in unit for t in _PCT_UNIT_TOKENS) \
                and 'share' not in nl and 'composition' not in nl:
            _add('pct', label or name, val, _dp(val), 'metrics')

    text = str(reply or '')
    for rx in _IDX_TEXT_RX:
        for m in rx.finditer(text):
            _add('index', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text')
    for rx in _PCT_TEXT_RX:
        for m in rx.finditer(text):
            _add('pct', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text')
    for rx in _DEMO_TEXT_RX:
        for m in rx.finditer(text):
            lab = _DEMO_ALIASES.get(m.group('label').lower(),
                                    m.group('label').lower())
            _add('demo', lab, m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text')
    return claims


# ---------------------------------------------------------------------------
# Check 1: anchor recompute
# ---------------------------------------------------------------------------

_INCOME_AMOUNT_RX = re.compile(
    r'\$\s*(?P<full>\d{1,3}(?:,\d{3})+)|(?<![\d.])(?P<k>\d{2,3})\s*[kK]\b')
_INCOME_PLUS_RX = re.compile(
    r'\+|or more|plus|and up|above|over|at least|minimum', re.I)
_INCOME_WORD_RX = re.compile(
    r'income|earn|household|hhi|affluent|\$', re.I)
_BUCKET_FLOOR_RX = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{4,})')


def _income_threshold_from_label(label):
    """Dollar floor when a claim label reads as an income-threshold
    tilt ('$100K+ households', 'earning 100k or more'); else None."""
    text = str(label or '')
    m = _INCOME_AMOUNT_RX.search(text)
    if not m:
        return None
    if not _INCOME_PLUS_RX.search(text):
        return None
    if not _INCOME_WORD_RX.search(text):
        return None
    if m.group('full'):
        return int(m.group('full').replace(',', ''))
    return int(m.group('k')) * 1000


def _income_composite_index(lookup, threshold):
    """Recompute the audience-vs-Gen-Pop index for the income buckets
    at or above `threshold` from the base demo rows. None when the
    buckets or their Gen Pop denominators are unavailable."""
    aud = gp = 0.0
    n = 0
    for rows in (lookup.get('demos') or {}).values():
        for row in rows:
            if len(row) < 4 or str(row[0]).strip().upper() != 'INCOME':
                continue
            label = str(row[3])
            low = 0
            if not re.search(r'less than|under', label, re.I):
                fm = _BUCKET_FLOOR_RX.search(label)
                if not fm:
                    continue
                low = int(fm.group(1).replace(',', ''))
            if low < threshold:
                continue
            if row[2] is None:
                return None
            aud += float(row[1])
            gp += float(row[2])
            n += 1
    if not n or gp <= 0.01:
        return None
    return round(aud / gp * 100.0, 1)


def _candidates_for(claim, lookup):
    """Base rows a claim can bind to, by normalized-label match (exact
    first, containment when both sides are 5+ chars). Generic audience
    nouns never bind; income-threshold index claims bind to the
    recomputed bucket composite."""
    ln = _norm(claim['label'])
    if claim['kind'] == 'index':
        threshold = _income_threshold_from_label(claim['label'])
        if threshold is not None:
            comp = _income_composite_index(lookup, threshold)
            return [comp] if comp is not None else []
    if ln in _GENERIC_LABEL_NORMS:
        return []
    table = lookup['demos'] if claim['kind'] == 'demo' \
        else lookup['brands']
    rows = list(table.get(ln) or [])
    if not rows and claim['kind'] == 'pct':
        rows = list(lookup['demos'].get(ln) or [])
    if not rows and len(ln) >= 5:
        for key, krows in table.items():
            if len(key) >= 5 and (ln in key or key in ln):
                rows.extend(krows)
            if len(rows) >= 8:
                break
    out = []
    for row in rows:
        if claim['kind'] == 'index':
            idx = row[2] if len(row) > 2 else None
            if idx is not None:
                out.append(float(idx))
        else:
            out.append(float(row[1]))
    return out


def anchor_check(reply, res, base_lookup):
    """Recompute cited figures from the base rows. Returns
    {'status': 'pass'|'fail'|'skip', 'detail', 'anchored',
     'findings': [...]}."""
    if not base_lookup:
        return {'status': 'skip', 'detail': 'base is not a profile',
                'anchored': 0, 'findings': []}
    claims = extract_claims(reply, res)
    if not claims:
        return {'status': 'skip', 'detail': 'no recomputable figure cited',
                'anchored': 0, 'findings': []}
    anchored, findings, bound = 0, [], 0
    sample_hits = []
    for c in claims:
        cands = _candidates_for(c, base_lookup)
        if not cands:
            continue
        bound += 1
        if c['kind'] == 'index':
            tol = max(1.0, 0.5 * 10 ** -c['dp']) + IDX_SLACK
            hard_abs, hard_rel = IDX_HARD_ABS, IDX_HARD_REL
        else:
            tol = 0.5 * 10 ** -c['dp'] + PCT_SLACK
            hard_abs, hard_rel = PCT_HARD_ABS, PCT_HARD_REL
        best = min(cands, key=lambda b: abs(b - c['value']))
        dist = abs(best - c['value'])
        if dist <= tol:
            anchored += 1
            if len(sample_hits) < 3:
                sample_hits.append(
                    f"{c['label']} {c['value']:g} vs base {best:g}")
        elif dist > max(hard_abs, 5 * tol) \
                and dist / max(abs(best), 1e-9) > hard_rel:
            unit = '' if c['kind'] == 'index' else '%'
            findings.append(
                f"The reply cites {c['label']} at {c['value']:g}{unit} "
                f"but the base file measures {best:g}{unit}. Use the "
                f"measured figure or drop the claim.")
    if findings:
        return {'status': 'fail', 'anchored': anchored,
                'detail': f"{len(findings)} figure(s) contradict the "
                          f"base rows ({bound} bound)",
                'findings': findings[:MAX_FINDINGS]}
    if anchored:
        return {'status': 'pass', 'anchored': anchored,
                'detail': f"anchored {anchored}/{bound} bound claim(s): "
                          + '; '.join(sample_hits),
                'findings': []}
    return {'status': 'skip', 'anchored': 0,
            'detail': f"{len(claims)} claim(s), none bindable to base "
                      "rows", 'findings': []}


# ---------------------------------------------------------------------------
# Check 2: ledger coherence
# ---------------------------------------------------------------------------

def _cohort_sig(text):
    try:
        import insights_ledger as il
        sig = il.cohort_signature(text)
        return (sig.get('ages'), sig.get('parents'))
    except Exception:
        return (_norm(text),)


def _windows_compatible(new_ws, new_we, old_ws, old_we):
    """Blank windows on either side compare as compatible (undated
    anchors are standing figures); dated windows must overlap."""
    if not (new_ws and new_we and old_ws and old_we):
        return True
    return not (new_we < old_ws or old_we < new_ws)


def _is_pct_unit(unit, name=''):
    u = str(unit or '').lower()
    nl = str(name or '').lower()
    return any(t in u for t in _PCT_UNIT_TOKENS) or u.endswith('_pct') \
        or 'share' in nl and not u


def ledger_check(res, family, prior_entries):
    """Compare the new read's metrics against stored entries for the
    same family + cohort + compatible window. delivered_artifact
    provenance gets the tightest contradiction band."""
    res = res or {}
    new_metrics = {}
    for m in (res.get('metrics') or []):
        if isinstance(m, dict) and isinstance(m.get('value'),
                                              (int, float)):
            nn = _norm(m.get('name'))
            if nn:
                new_metrics[nn] = m
    if not new_metrics or not prior_entries:
        return {'status': 'skip', 'detail': 'no comparable history',
                'findings': []}
    fam = str(family or '').strip().lower()
    sig = _cohort_sig(res.get('cohort'))
    new_ws, new_we = str(res.get('window_start') or ''), \
        str(res.get('window_end') or '')
    compared, findings = 0, []
    for e in prior_entries:
        if not isinstance(e, dict):
            continue
        if str(e.get('family') or '').strip().lower() != fam:
            continue
        if _cohort_sig(e.get('cohort')) != sig:
            continue
        if not _windows_compatible(new_ws, new_we,
                                   str(e.get('ws') or ''),
                                   str(e.get('we') or '')):
            continue
        delivered = str(e.get('prov') or '') == 'delivered_artifact'
        for om in (e.get('metrics') or []):
            if not isinstance(om, dict):
                continue
            nn = _norm(om.get('name'))
            nm = new_metrics.get(nn)
            if not nm or not isinstance(om.get('value'), (int, float)):
                continue
            nv, ov = float(nm['value']), float(om['value'])
            compared += 1
            rel = abs(nv - ov) / max(abs(ov), 1e-9)
            if _is_pct_unit(nm.get('unit'), nm.get('name')) \
                    and _is_pct_unit(om.get('unit'), om.get('name')):
                abs_band = LEDGER_PCT_ABS_DELIVERED if delivered \
                    else LEDGER_PCT_ABS
                rel_band = LEDGER_PCT_REL_DELIVERED if delivered \
                    else LEDGER_PCT_REL
                bad = abs(nv - ov) > abs_band and rel > rel_band
            else:
                rel_band = LEDGER_COUNT_REL_DELIVERED if delivered \
                    else LEDGER_COUNT_REL
                bad = rel > rel_band
            if bad:
                src = 'a figure the client already holds' if delivered \
                    else 'the stored read'
                findings.append(
                    f"This read puts {nm.get('label') or nm.get('name')} "
                    f"at {nv:g} but {src} from {e.get('ts', '')[:10]} "
                    f"for the same subject, cohort and window says "
                    f"{ov:g}. Reconcile with the established figure or "
                    f"state a window that genuinely differs.")
    if findings:
        return {'status': 'fail',
                'detail': f"{len(findings)} contradiction(s) across "
                          f"{compared} compared metric(s)",
                'findings': findings[:MAX_FINDINGS]}
    if compared:
        return {'status': 'pass',
                'detail': f"consistent with {compared} stored "
                          f"metric(s)", 'findings': []}
    return {'status': 'skip', 'detail': 'no comparable history',
            'findings': []}


# ---------------------------------------------------------------------------
# Check 3: scrub residue
# ---------------------------------------------------------------------------

def scrub_check(reply):
    text = str(reply or '')
    if not text.strip():
        return {'status': 'fail', 'detail': 'empty reply',
                'findings': ['The reply came back empty. Produce the '
                             'full answer.']}
    findings = []
    for rx, why in _RESIDUAL_BANNED:
        m = rx.search(text)
        if m:
            findings.append(
                f"The reply contains \"{m.group(0)}\" ({why}). Rewrite "
                f"the sentence without it.")
    if findings:
        return {'status': 'fail',
                'detail': f"{len(findings)} residual term(s)",
                'findings': findings[:MAX_FINDINGS]}
    return {'status': 'pass', 'detail': 'clean', 'findings': []}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def verify_read(*, reply, res, family=None, base_lookup=None,
                prior_entries=None):
    """Run all three checks. Returns
    {'ok', 'checks': {'anchor', 'ledger', 'scrub'}, 'findings'}."""
    anchor = anchor_check(reply, res, base_lookup)
    ledger = ledger_check(res, family, prior_entries or [])
    scrub = scrub_check(reply)
    findings = (anchor['findings'] + ledger['findings']
                + scrub['findings'])[:MAX_FINDINGS + 4]
    ok = all(c['status'] != 'fail' for c in (anchor, ledger, scrub))
    return {'ok': ok,
            'checks': {'anchor': anchor, 'ledger': ledger,
                       'scrub': scrub},
            'findings': findings}


def render_findings_block(findings):
    """Prompt block appended for the single self-revision call."""
    lines = [
        'VERIFICATION FINDINGS - REVISE',
        '==============================',
        'The previous draft failed the pre-delivery number check on the',
        'specific points below. Regenerate the FULL answer in the same',
        'JSON contract: fix ONLY these issues, keep every figure and',
        'sentence that was correct, and never introduce a number that',
        'is not grounded in the evidence blocks above.',
    ]
    for f in (findings or [])[:MAX_FINDINGS + 4]:
        lines.append(f"- {f}")
    return '\n'.join(lines)


def stamp(verdict, revised=False, held=False):
    """Compact verification stamp for the ledger entry and the read-job
    JSON."""
    checks = (verdict or {}).get('checks') or {}

    def _st(k):
        return str((checks.get(k) or {}).get('status') or 'skip')

    outcome = 'held' if held else ('revised_pass' if revised else 'pass')
    return {
        'outcome': outcome,
        'anchor': _st('anchor'),
        'ledger': _st('ledger'),
        'scrub': _st('scrub'),
        'anchored': int((checks.get('anchor') or {}).get('anchored') or 0),
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
