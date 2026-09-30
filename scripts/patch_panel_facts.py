#!/usr/bin/env python3
"""Panel facts (Jenna 2026-09-30: "lets now do the panel-fact queries
upgrades"). A factual ask the shipped base file answers exactly (a
demo share, a named brand's reach, a category top list, the audience
size) returns the file's own numbers in one step - byte-consistent
with the dashboard, S3 only, never the clickstream. Anything the file
cannot answer exactly falls through to the full read unchanged."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PMA = ROOT / 'prometheus_analysis.py'
APP = ROOT / 'app.py'


def splice(src, old, new, desc):
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f'[{desc}] anchor count {n}')
    return src.replace(old, new)


# ================= prometheus_analysis.py =========================
pma = PMA.read_text(encoding='utf-8')

SECTION = '''
# ---------------- Panel facts (2026-09-30, Jenna) -----------------
# "lets now do the panel-fact queries upgrades": a factual ask the
# shipped base file answers exactly returns the file's own numbers in
# one step. Scope is what already sits in the bucket (profile files
# plus the US baseline) - never the clickstream. The detector is
# deliberately conservative: anything analytical, comparative, or
# ambiguous returns None and rides the full read unchanged.

_PANEL_FACT_BLOCKERS = re.compile(
    r"\\b(?:why|should|recommend|compare[ds]?|versus|vs\\.?|strategy|"
    r"white\\s*space|opportunit|journey|campaign|creative|pitch|deck|"
    r"churn|over.?index|overlap|trend|trajector|drove|convert|"
    r"partnership|value|worth|report|insight|analy[sz]|deep dive|"
    r"story|angle|persona|summar|against)\\b", re.I)

_PANEL_FACT_FRAME = re.compile(
    r"(?:%|percent(?:age)?|\\bshare\\b|\\bsplit\\b|\\bbreakdown\\b|"
    r"\\bmix\\b|\\bskew\\b|how\\s+(?:old|many)|what\\s+is|what'?s|"
    r"\\bpenetration\\b)", re.I)

# demo keyword -> (canonical Column, bucket or None)
_PANEL_FACT_DEMOS = (
    (re.compile(r"\\bfemales?\\b|\\bwomen\\b", re.I), 'GENDER', 'FEMALE'),
    (re.compile(r"\\bmales?\\b|\\bmen\\b", re.I), 'GENDER', 'MALE'),
    (re.compile(r"\\bgender\\b", re.I), 'GENDER', None),
    (re.compile(r"\\bages?\\b|\\bhow old\\b|\\bage mix\\b", re.I),
     'AGE', None),
    (re.compile(r"\\bincome\\b|\\bhhi\\b|\\bearn\\b", re.I),
     'INCOME', None),
    (re.compile(r"\\bhispanic\\b|\\blatino\\b", re.I),
     'ETHNICITY', 'HISPANIC'),
    (re.compile(r"\\bethnicit", re.I), 'ETHNICITY', None),
    (re.compile(r"\\beducation\\b|\\bcollege\\b", re.I),
     'EDUCATION', None),
    (re.compile(r"\\bparents?\\b|\\bparental\\b", re.I),
     'PARENTAL STATUS', None),
    (re.compile(r"\\bmarried\\b|\\bsingle\\b|\\brelationship\\b", re.I),
     'RELATIONSHIP', None),
    (re.compile(r"\\blgbtq?\\+?\\b|\\borientation\\b", re.I),
     'SEXUAL ORIENTATION', None),
    (re.compile(r"\\boccupation\\b|\\bjobs?\\b", re.I),
     'OCCUPATION', None),
)

_PANEL_FACT_BRAND_RES = (
    re.compile(
        r"(?:what|how)\\s+(?:%|percent(?:age)?\\b|share\\b|many\\b)"
        r"[^.?!\\n]{0,44}?\\b(?:use|uses|watch(?:es)?|stream(?:s)?|"
        r"shop(?:s)?(?:\\s+at)?|bu(?:y|ys)|subscribe(?:s)?\\s+to|"
        r"are\\s+on|on)\\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\\- ]{2,40}?)\\s*\\??$", re.I),
    re.compile(
        r"(?P<brand>[A-Za-z0-9&+'.\\- ]{2,40}?)(?:'s)?\\s+"
        r"penetration\\b", re.I),
    re.compile(
        r"\\bpenetration\\s+(?:of|for)\\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\\- ]{2,40}?)\\s*\\??$", re.I),
    re.compile(
        r"\\bshare\\s+(?:that|who)\\s+(?:use|watch|stream|shop\\s+at|"
        r"buy|subscribe\\s+to)\\s+"
        r"(?P<brand>[A-Za-z0-9&+'.\\- ]{2,40}?)\\s*\\??$", re.I),
)

_PANEL_FACT_BRAND_TAIL = re.compile(
    r"\\s*(?:in|for|on|across|among)\\s+(?:this|that|the|their|her|his"
    r")\\b.*$|\\s*(?:of\\s+them|here|right\\s+now|today)\\s*$", re.I)

_PANEL_FACT_TOP_RE = re.compile(
    r"\\b(?:top|biggest|most\\s+(?:used|watched|shopped|popular))\\s+"
    r"(?:(?P<n>\\d{1,2})\\s+)?"
    r"(?P<cat>[A-Za-z][A-Za-z /&']{2,34}?)"
    r"(?=\\s+(?:for|in|on|of|among|across|here)\\b|\\s*\\??$)", re.I)

_PANEL_FACT_SIZE_RE = re.compile(
    r"\\bhow\\s+(?:big|large)\\b|"
    r"\\baudience\\s+size\\b|\\bsize\\s+of\\s+(?:this|the|that)\\s+"
    r"audience\\b|\\btotal\\s+audience\\b", re.I)

# generic hint -> ordered candidate Columns (first present wins)
_PANEL_FACT_CAT_ALIASES = (
    (('qsr', 'fast food'), ('QSR',)),
    (('streaming service', 'streaming platform', 'streaming video',
      'streaming', 'svod'),
     ('STREAMING/PLATFORM', 'STREAMING VIDEO')),
    (('music',), ('STREAMING MUSIC',)),
    (('social',), ('SOCIAL MEDIA',)),
    (('search',), ('SEARCH ENGINE/AI',)),
    (('retailer', 'store', 'shop'), ('WHERE THEY SHOP', 'RETAILERS')),
    (('app', 'platform'), ('APP/PLATFORM', 'APP/PLATFORM USAGE')),
    (('brand',), ('MOST PURCHASED BRANDS',)),
    (('talent', 'celebrit'), ('TALENT',)),
    (('podcast',), ('PODCAST',)),
    (('game', 'gaming'), ('GAMES',)),
    (('bank',), ('BANKS', 'BANKING', 'DIGITAL BANKING')),
    (('travel',), ('TRAVEL',)),
    (('car', 'auto'), ('AUTOMOBILE',)),
    (('tv', 'cable', 'broadcast'), ('BROADCAST/CABLE',)),
)


def detect_panel_fact(text):
    """Parse a short factual ask into a lookup the base file can
    answer exactly. Returns {'kind': ...} or None (None = ride the
    full read)."""
    t = str(text or '').strip()
    if not t or len(t) > 220:
        return None
    if _PANEL_FACT_BLOCKERS.search(t):
        return None
    if _PANEL_FACT_SIZE_RE.search(t):
        return {'kind': 'size'}
    m_top = _PANEL_FACT_TOP_RE.search(t)
    if m_top:
        hint = re.sub(r"\\s+", ' ', m_top.group('cat') or '').strip()
        hint = re.sub(
            r"\\b(?:brands?|services?|platforms?|stores?|retailers?|"
            r"apps?|channels?|picks?|names?)\\s*$", '', hint,
            flags=re.I).strip()
        if hint:
            n = 5
            try:
                n = max(2, min(10, int(m_top.group('n') or 5)))
            except (TypeError, ValueError):
                pass
            return {'kind': 'top', 'cat_hint': hint, 'n': n}
    if _PANEL_FACT_FRAME.search(t):
        for rx in _PANEL_FACT_BRAND_RES:
            m = rx.search(t)
            if m:
                brand = _PANEL_FACT_BRAND_TAIL.sub(
                    '', m.group('brand') or '').strip(" .,'\\\"")
                if 2 <= len(brand) <= 40 \\
                        and not _PANEL_FACT_BLOCKERS.search(brand):
                    return {'kind': 'brand', 'brand': brand}
        for rx, col, bucket in _PANEL_FACT_DEMOS:
            if rx.search(t):
                return {'kind': 'demo', 'column': col,
                        'bucket': bucket}
    return None


def _panel_fact_rows(df, bp_col, want_col):
    """(value, bp) rows of one Column, BP-parsed, sorted desc."""
    want = _norm_cat(want_col)
    out = []
    for _, row in df.iterrows():
        if _norm_cat(row.get('Column')) != want:
            continue
        v = _parse_bp(row.get(bp_col))
        val = str(row.get('Value') or '').strip()
        if v is None or not val:
            continue
        out.append((val, v))
    out.sort(key=lambda r: -r[1])
    return out


def _panel_fact_column(df, hint):
    """Resolve a spoken category hint to a Column present on the
    file. None when nothing matches confidently."""
    h = _norm_cat(hint)
    if not h:
        return None
    present = []
    seen = set()
    for c in df.get('Column', []):
        n = _norm_cat(c)
        if n and n not in seen and n not in METADATA_COLS \\
                and n not in DEMO_COLS:
            seen.add(n)
            present.append((n, str(c)))
    h_toks = h.split()
    for keys, cands in _PANEL_FACT_CAT_ALIASES:
        hit = False
        for k in keys:
            ku = k.upper()
            if ' ' in ku:
                hit = ku in h
            else:
                # Token-exact (plus plural / long-stem prefix) so a
                # short key never hijacks a longer word: 'app' must
                # not resolve 'apparel'.
                hit = any(tok == ku or tok == ku + 'S'
                          or (len(ku) >= 4 and tok.startswith(ku))
                          for tok in h_toks)
            if hit:
                break
        if hit:
            for cand in cands:
                for n, orig in present:
                    if n == _norm_cat(cand):
                        return orig
    for n, orig in present:
        if h == n or h in n:
            return orig
    return None


_PANEL_FACT_ACRONYMS = {
    'QSR', 'CPG', 'B2B', 'AI', 'TV', 'MLB', 'NBA', 'NFL', 'NHL',
    'MLS', 'WNBA', 'MILB', 'EST', 'TVOD', 'PVOD', 'SVOD', 'AVOD',
    'VMVPD', 'MVPD', 'DMA', 'LGBTQ', 'AL', 'NL', 'AFC', 'NFC',
}


def _panel_fact_cat_display(col):
    """Reader-facing category name: title case with acronyms kept
    upper (QSR stays QSR, never Qsr)."""
    parts = re.split(r'([/\\s]+)', str(col or ''))
    out = []
    for p in parts:
        if not p or re.fullmatch(r'[/\\s]+', p):
            out.append(p)
        elif p.upper() in _PANEL_FACT_ACRONYMS:
            out.append(p.upper())
        else:
            out.append(p.title())
    return ''.join(out)


def _panel_fact_pct(v):
    return f"{v:.1f}%"


def answer_panel_fact(fact, df, meta, genpop_map=None):
    """Answer one detected fact from the loaded base file. Returns
    {'reply', 'family', 'metrics', 'breakdown', 'followups'} or None
    when the file cannot answer exactly."""
    bp_col = (meta or {}).get('bp_col')
    if not bp_col or not isinstance(fact, dict):
        return None
    name = (meta or {}).get('name') or 'this audience'
    kind = fact.get('kind')
    if kind == 'size':
        proj = (meta or {}).get('proj')
        if not proj:
            return None
        window = (meta or {}).get('window')
        reply = (f"The {name} audience projects to {proj:,} people "
                 f"in the US"
                 + (f" across {window}." if window else "."))
        return {
            'reply': reply, 'family': 'audience size',
            'metrics': [{'name': 'projected_us_audience',
                         'label': 'Projected US audience',
                         'value': int(proj), 'unit': 'people',
                         'definition': 'Projected US audience for '
                                       'the base profile'}],
            'breakdown': None,
            'followups': ['Gender split for this audience',
                          'Top brands for this audience']}
    if kind == 'demo':
        col = fact.get('column') or ''
        rows = _panel_fact_rows(df, bp_col, col)
        if not rows and col == 'RELATIONSHIP':
            rows = _panel_fact_rows(df, bp_col, 'RELATIONSHIP STATUS')
        if not rows:
            return None
        dim = col.title().replace('_', ' ')
        bucket = fact.get('bucket')
        metrics = []
        if bucket:
            hit = next((r for r in rows
                        if bucket in _norm_cat(r[0])), None)
            if not hit:
                return None
            if _norm_cat(col) == 'GENDER' and len(rows) >= 2:
                other = next((r for r in rows
                              if _norm_cat(r[0]) != _norm_cat(hit[0])),
                             None)
                reply = (f"The {name} audience is "
                         f"{_panel_fact_pct(hit[1])} "
                         f"{hit[0].strip().lower()}"
                         + (f" and {_panel_fact_pct(other[1])} "
                            f"{other[0].strip().lower()}."
                            if other else "."))
            else:
                reply = (f"{hit[0].strip().title()} is "
                         f"{_panel_fact_pct(hit[1])} of the "
                         f"{name} audience.")
            metrics.append({
                'name': f"{_norm_brand(hit[0])[:40]}_share",
                'label': f"{hit[0].strip().title()} share",
                'value': round(hit[1], 4),
                'unit': 'pct_of_audience',
                'definition': f"{dim} bucket share of the audience"})
        else:
            lead = ', '.join(
                f"{v.strip()} {_panel_fact_pct(b)}"
                for v, b in rows[:6])
            reply = f"{name} {dim.lower()} split: {lead}."
            if len(rows) > 6:
                reply += " The full split rides the CSV."
            for v, b in rows[:8]:
                metrics.append({
                    'name': f"{_norm_brand(v)[:40]}_share",
                    'label': f"{v.strip().title()} share",
                    'value': round(b, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"{dim} bucket share of the "
                                  f"audience"})
        breakdown = {'dimension': dim,
                     'share_basis': 'share of audience',
                     'rows': [{'label': v.strip(),
                               'share_pct': round(b, 4)}
                              for v, b in rows]}
        return {'reply': reply, 'family': 'demographics',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': [f'Full demographic read on {name}',
                              'Top brands for this audience']}
    if kind == 'brand':
        want = _norm_brand(fact.get('brand'))
        if not want:
            return None
        best = None
        for _, row in df.iterrows():
            cat = _norm_cat(row.get('Column'))
            if cat in METADATA_COLS or cat in DEMO_COLS:
                continue
            val = str(row.get('Value') or '').strip()
            if _norm_brand(val) != want:
                continue
            v = _parse_bp(row.get(bp_col))
            if v is None or v >= 99.95:
                continue
            if best is None or v > best[2]:
                best = (val, str(row.get('Column')), v)
        if not best:
            return None
        b_val, b_cat, b_bp = best
        reply = (f"{b_val} reaches {_panel_fact_pct(b_bp)} of the "
                 f"{name} audience.")
        gp = None
        gmap = genpop_map or {}
        gp = gmap.get((_norm_cat(b_cat), want))
        if gp is None:
            cands = [v for (c, b), v in gmap.items() if b == want]
            gp = max(cands) if cands else None
        if gp and gp > 0:
            ratio = b_bp / gp
            reply += (f" The US average is {_panel_fact_pct(gp)}, "
                      f"so this audience runs {ratio:.1f}x the "
                      f"average.")
        peers = [r for r in _panel_fact_rows(df, bp_col, b_cat)
                 if r[1] < 99.95][:10]
        breakdown = {'dimension': _panel_fact_cat_display(b_cat),
                     'share_basis': 'audience reach',
                     'rows': [{'label': v,
                               'penetration_pct': round(b, 4)}
                              for v, b in peers]}
        metrics = [{'name': f"{want[:40]}_reach",
                    'label': f"{b_val} reach",
                    'value': round(b_bp, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"Share of the audience reached "
                                  f"by {b_val} "
                                  f"({_panel_fact_cat_display(b_cat)})"}]
        return {'reply': reply, 'family': 'brand reach',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': [f'Top {_panel_fact_cat_display(b_cat)} for this '
                              f'audience',
                              'Gender split for this audience']}
    if kind == 'top':
        col = _panel_fact_column(df, fact.get('cat_hint'))
        if not col:
            return None
        subj_norm = _norm_brand(name)
        rows = [r for r in _panel_fact_rows(df, bp_col, col)
                if r[1] < 99.95 and _norm_brand(r[0]) != subj_norm]
        if len(rows) < 2:
            return None
        n = int(fact.get('n') or 5)
        picks = rows[:n]
        listing = ', '.join(f"{v} {_panel_fact_pct(b)}"
                            for v, b in picks)
        reply = (f"Top {len(picks)} {_panel_fact_cat_display(col)} for the {name} "
                 f"audience: {listing}.")
        breakdown = {'dimension': _panel_fact_cat_display(col),
                     'share_basis': 'audience reach',
                     'rows': [{'label': v,
                               'penetration_pct': round(b, 4)}
                              for v, b in rows[:max(n, 10)]]}
        metrics = [{'name': f"{_norm_brand(v)[:40]}_reach",
                    'label': f"{v} reach", 'value': round(b, 4),
                    'unit': 'pct_of_audience',
                    'definition': f"Share of the audience reached "
                                  f"by {v} "
                                  f"({_panel_fact_cat_display(col)})"}
                   for v, b in picks[:3]]
        return {'reply': reply, 'family': 'category rank',
                'metrics': metrics, 'breakdown': breakdown,
                'followups': ['Gender split for this audience',
                              f'How {picks[0][0]} compares to the '
                              f'US average']}
    return None


'''

pma = splice(
    pma,
    "CSV_OFFER_CHIP = 'Download this data as a CSV'",
    SECTION.replace('\\\\', '\\')
    + "CSV_OFFER_CHIP = 'Download this data as a CSV'",
    'pma panel facts section')

ast.parse(pma)
PMA.write_text(pma, encoding='utf-8')
print('prometheus_analysis.py: panel facts section added, ast clean')

# ========================= app.py =================================
app = APP.read_text(encoding='utf-8')

FN = '''def _pm_panel_fact_response(pm_user, ppu, text, base):
    """Panel facts (2026-09-30, Jenna: "lets now do the panel-fact
    queries upgrades"): a factual ask the shipped base file answers
    exactly - a demo share, a named brand's reach, a category top
    list, the audience size - returns the file's own numbers in one
    step. Byte-consistent with the dashboard, reads only what already
    shipped, and anything the file cannot answer exactly returns None
    so the full read runs unchanged. Never raises."""
    import prometheus_analysis as pma
    import insights_ledger as il
    key = str((base or {}).get('s3_key') or '')
    if not key.lower().endswith('.csv'):
        return None
    try:
        fact = pma.detect_panel_fact(text)
    except Exception:
        traceback.print_exc()
        return None
    if not fact:
        return None
    try:
        df, _etag = pma.load_profile_df(s3_client, S3_BUCKET, key)
        meta = pma._profile_meta(df, (base or {}).get('subject') or '')
        gmap = (pma.load_genpop_map(s3_client, S3_BUCKET)
                if fact.get('kind') == 'brand' else {})
        ans = pma.answer_panel_fact(fact, df, meta, gmap)
    except Exception:
        traceback.print_exc()
        return None
    if not ans or not ans.get('reply'):
        return None
    subj = meta.get('name') or (base or {}).get('subject') or ''
    _pm_ask_hint(route='panel_fact', outcome='answered', subject=subj)
    # Answered from what already shipped: metered, never free
    # (2026-09-14).
    try:
        _pm_meter_answer('panel_fact', ppu)
    except Exception:
        traceback.print_exc()
    try:
        _pm_remember_ask(pm_user, text, subject=subj,
                         route='panel_fact')
    except Exception:
        traceback.print_exc()
    chips = list(ans.get('followups') or [])[:2]
    entry_kw = dict(
        subject=subj, metric_family=ans.get('family') or '',
        question=text, route='panel_fact',
        metrics=ans.get('metrics') or [], reply=ans['reply'],
        followups=chips, base_profile_key=key,
        breakdown=ans.get('breakdown'),
        window_label=str(meta.get('window') or ''),
        derivation='exact values read from the shipped base profile')
    try:
        il.persist(**entry_kw)
    except Exception:
        traceback.print_exc()
    file_payload = {}
    try:
        entry = il.make_entry(**entry_kw)
        if entry.get('breakdown') or entry.get('metrics'):
            file_payload = _pm_answer_file_payload(
                entry,
                auto_save=bool(_PM_FILE_ASK_RE.search(str(text or ''))),
                username=pm_user, question=text)
            if file_payload and 'Email me this file' not in chips:
                chips.append('Email me this file')
    except Exception:
        traceback.print_exc()
    try:
        _pm_csv_point(subj, text, ans.get('family'))
    except Exception:
        traceback.print_exc()
    return jsonify({
        'success': True, 'action': 'answer', 'reply': ans['reply'],
        'followups': chips, 'offer_deck': False, 'deck_angle': None,
        'profile': subj, **file_payload})


def _pm_generate_metrics_response('''

app = splice(app, 'def _pm_generate_metrics_response(', FN,
             'app panel fact fn')

CALL_OLD = '''        return jsonify({
            'success': True, 'action': 'answer',
            'reply': exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': led.get('subject') or subj_hint or None})
    # No stored read to replay: this is a FRESH generation, which'''
CALL_NEW = '''        return jsonify({
            'success': True, 'action': 'answer',
            'reply': exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': led.get('subject') or subj_hint or None})
    # PANEL FACTS (2026-09-30, Jenna: "lets now do the panel-fact
    # queries upgrades"): a factual ask the base file answers exactly
    # returns the file's own numbers now instead of riding the full
    # read. Comparison pairs and charged research reports never take
    # this path; a miss falls through unchanged.
    if not _two and panel_charge is None:
        _pf_resp = None
        try:
            _pf_resp = _pm_panel_fact_response(_pm_user, _pm_ppu,
                                               text, base)
        except Exception:
            traceback.print_exc()
        if _pf_resp is not None:
            return _pf_resp
    # No stored read to replay: this is a FRESH generation, which'''

app = splice(app, CALL_OLD, CALL_NEW, 'app panel fact callsite')

ast.parse(app)
APP.write_text(app, encoding='utf-8')
print('app.py: panel fact route wired, ast clean')
