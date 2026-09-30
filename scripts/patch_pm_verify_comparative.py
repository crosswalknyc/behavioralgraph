#!/usr/bin/env python3
"""Comparative-read verification fix (Jenna 2026-09-30, Stella Lefty).

Defect: debip asked a competitive read on Stella Lefty ("where does
she sit competitively? how do her numbers look compared to others?").
The generated reply legitimately cited competitors' figures and
audience-overlap shares. The anchor pass bound every one of those
numbers to Stella's own base file: overlap phrasing ("47.3% into
Stella Lefty's audience") containment-matched her self-pin row at
100%, and competitor demo figures (66.5% / 62.7% / 62.5% female) were
checked against her 76.2% FEMALE row. Every legitimate claim read as
a contradiction, the self-revision and the auto-correct retry could
never satisfy the findings, and the read was HELD: the user got
nothing.

Fix, all fail-open (a skipped claim is simply not anchor-verified;
skipping can never hold a read):

1. prometheus_verify._candidates_for
   - labels ENDING in a cohort-relation noun (audience, file, fans,
     overlap, ...) describe a relationship between audiences, never a
     base row -> no candidates.
   - containment matches never bind to the subject's own-name key
     (the self-pin row); exact matches still do.
2. prometheus_verify.extract_claims grows sentence-level attribution
   for text claims: a figure whose nearest preceding named entity is
   NOT the base subject, or that sits in a sentence naming another
   entity or carrying a field-comparison marker without the base
   subject named before the figure, is marked off_base.
3. anchor_check skips off_base claims. In a comparative ask
   (is_comparative_ask on the question; Gen Pop comparisons never
   count) structured breakdown/metrics claims bind only when their
   label names the base subject.
4. app.py threads question=text into all three verify_read calls.

Single-subject reads keep full strictness: base-attributed text
claims and all structured claims bind exactly as before.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PMV = ROOT / "prometheus_verify.py"
APP = ROOT / "app.py"


def splice(src, old, new, desc, count=1):
    n = src.count(old)
    if n != count:
        raise RuntimeError(f"[{desc}] anchor found {n}x (want {count})")
    return src.replace(old, new)


# ===================================================================
# prometheus_verify.py
# ===================================================================
src = PMV.read_text(encoding="utf-8")

# --- 1. constants + helpers after _GENERIC_LABEL_NORMS ---------------
OLD = """_NORM_RX = re.compile(r'[^a-z0-9]+')


def _norm(label):"""
NEW = """_NORM_RX = re.compile(r'[^a-z0-9]+')

# Labels that END in a cohort-relation noun describe a relationship
# between audiences ("into Stella Lefty's audience", "her own file"),
# never a base row (2026-09-30 Stella Lefty comparative defect: the
# overlap phrasing containment-matched the subject's self-pin row at
# 100% and held the read).
_COHORT_TAIL_TOKENS = frozenset({
    'audience', 'audiences', 'file', 'files', 'fans', 'fanbase',
    'viewers', 'listeners', 'followers', 'base', 'cohort', 'cohorts',
    'universe', 'overlap', 'crossover', 'reach', 'peers',
    'competitors', 'rivals',
})

# Comparative-ask detection on the QUESTION text. Baseline comparisons
# ("vs Gen Pop", "against the US average") are stripped first so they
# never count as competitor comparisons.
_GENPOP_STRIP_RX = re.compile(
    r'(?:versus|vs\\.?|compared\\s+(?:to|with|against)|against)?\\s*'
    r'(?:the\\s+)?(?:gen(?:eral)?\\s*pop\\w*|us average|'
    r'national average|baseline)', re.I)
_COMPARATIVE_RX = re.compile(
    r'\\b(compar\\w+|competit\\w+|versus|vs\\.?|peers?|rivals?|'
    r'side by side|stack(?:s|ed)?\\s+up|head to head|landscape|'
    r'others|other\\s+(?:artists|acts|brands|names|titles))\\b', re.I)

# In-sentence field-comparison markers: a demo/pct figure in a
# sentence carrying one of these describes the field, not the base
# subject, unless the base subject is named before the figure.
_SENT_COMPARE_RX = re.compile(
    r'\\b(peers?|competitors?|rivals?|others|comparable|'
    r'similar\\s+(?:acts|artists|names)|category\\s+(?:average|norm)|'
    r'typical|versus|vs\\.?|compared|the field)\\b', re.I)

# TitleCase entity sequences (1-3 words) for sentence attribution.
# Each word must be pure TitleCase ([A-Z][a-z]{2,}) with no letter
# following, so CamelCase brands (TikTok, YouTube) and short particles
# never register as entities.
_ENTITY_SEQ_RX = re.compile(
    r"\\b[A-Z][a-z]{2,}(?:['\\u2019]s?)?(?![A-Za-z])"
    r"(?:\\s+[A-Z][a-z]{2,}(?:['\\u2019]s?)?(?![A-Za-z])){0,2}")
_ENTITY_COMMON = frozenset({
    'the', 'she', 'her', 'his', 'he', 'they', 'their', 'while',
    'when', 'where', 'which', 'that', 'this', 'these', 'those',
    'and', 'but', 'for', 'with', 'against', 'among', 'across',
    'most', 'both', 'only', 'even', 'still', 'meanwhile', 'however',
    'here', 'there', 'what', 'who', 'its', 'our', 'your', 'you',
    'use', 'not', 'now', 'per', 'via', 'versus', 'compared',
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december',
})


def _base_tokens(name):
    return {w for w in re.split(r'[^a-z0-9]+', str(name or '').lower())
            if len(w) >= 3}


def is_comparative_ask(question):
    \"\"\"True when the ask compares the subject against other entities
    (competitors, peers, other artists). Gen Pop / US-average baseline
    comparisons never count.\"\"\"
    q = _GENPOP_STRIP_RX.sub(' ', str(question or ''))
    return bool(_COMPARATIVE_RX.search(q))


def _sentence_bounds(text, start, end):
    lo = max(0, start - 240)
    s = -1
    for ch in ('.', '!', '?', '\\n'):
        s = max(s, text.rfind(ch, lo, start))
    hi = min(len(text), end + 240)
    nxt = [text.find(ch, end, hi) for ch in ('.', '!', '?', '\\n')]
    nxt = [n for n in nxt if n != -1]
    return (s + 1 if s >= 0 else lo), (min(nxt) if nxt else hi)


def _entities_in(text, base_toks, multi_only=False):
    \"\"\"(base_mentions, foreign_entities) as (pos, text) lists.\"\"\"
    base_hits, foreign = [], []
    for m in _ENTITY_SEQ_RX.finditer(text):
        words = re.split(r'\\s+', m.group(0))
        toks = {re.sub(r'[^a-z0-9]', '', w.lower()) for w in words}
        toks.discard('')
        if len(words) == 1 and (next(iter(toks), '')
                                in _ENTITY_COMMON):
            continue
        if toks & base_toks:
            base_hits.append((m.start(), m.group(0)))
        elif not all(t in _ENTITY_COMMON for t in toks):
            if multi_only and len(words) == 1:
                continue
            foreign.append((m.start(), m.group(0)))
    return base_hits, foreign


def _off_base_text_claim(text, m_start, m_end, base_toks):
    \"\"\"True when the sentence around a text claim attributes the
    figure to someone other than the base subject: a named competitor
    is the nearest entity before the figure, another multi-word name
    follows it in the same sentence, or the sentence is a field
    comparison with the base subject never named before the figure.
    Off-base claims are skipped by the anchor pass (fail-open).\"\"\"
    s, e = _sentence_bounds(text, m_start, m_end)
    before = text[s:m_start]
    base_b, foreign_b = _entities_in(before, base_toks)
    nearest_base = max((p for p, _t in base_b), default=-1)
    nearest_foreign = max((p for p, _t in foreign_b), default=-1)
    if nearest_foreign > nearest_base:
        return True
    if nearest_base >= 0:
        return False
    _ba, foreign_after = _entities_in(text[m_end:e], base_toks,
                                      multi_only=True)
    if foreign_after:
        return True
    return bool(_SENT_COMPARE_RX.search(text[s:e]))


def _norm(label):"""
src = splice(src, OLD, NEW, "pmv constants+helpers")

# --- 2. extract_claims: signature, off flag, text attribution --------
OLD = """def extract_claims(reply, res):
    \"\"\"Concrete measured figures the reply commits to, as claims:
    {'kind': 'pct'|'index'|'demo', 'label', 'value', 'dp', 'src'}.\"\"\"
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
"""
NEW = """def extract_claims(reply, res, base_name=None, comparative=False):
    \"\"\"Concrete measured figures the reply commits to, as claims:
    {'kind': 'pct'|'index'|'demo', 'label', 'value', 'dp', 'src',
     'off_base'}. off_base claims belong to an entity other than the
    base subject (competitor figures in a comparative read) and are
    skipped by the anchor pass.\"\"\"
    claims, seen = [], set()
    base_toks = _base_tokens(base_name)

    def _add(kind, label, value, dp, src, off_base=False):
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
                       'dp': dp, 'src': src, 'off_base': off_base})

    def _structured_off(label):
        # In a comparative read, structured rows are per-entity
        # (competitor names in the labels). Only rows naming the base
        # subject stay bound to the base file.
        if not comparative or not base_toks:
            return False
        words = {re.sub(r'[^a-z0-9]', '', w.lower())
                 for w in re.split(r'\\s+', str(label or ''))}
        words.discard('')
        return not (words & base_toks)
"""
src = splice(src, OLD, NEW, "extract_claims head")

OLD = """        pen = row.get('penetration_pct')
        if isinstance(pen, (int, float)):
            _add('pct', row.get('label'), pen, _dp(pen), 'breakdown')"""
NEW = """        pen = row.get('penetration_pct')
        if isinstance(pen, (int, float)):
            _add('pct', row.get('label'), pen, _dp(pen), 'breakdown',
                 off_base=_structured_off(row.get('label')))"""
src = splice(src, OLD, NEW, "breakdown off flag")

OLD = """        if 'index' in nl:
            _add('index', label or name, val, _dp(val), 'metrics')
        elif any(t in unit for t in _PCT_UNIT_TOKENS) \\
                and 'share' not in nl and 'composition' not in nl:
            _add('pct', label or name, val, _dp(val), 'metrics')"""
NEW = """        if 'index' in nl:
            _add('index', label or name, val, _dp(val), 'metrics',
                 off_base=_structured_off(label or name))
        elif any(t in unit for t in _PCT_UNIT_TOKENS) \\
                and 'share' not in nl and 'composition' not in nl:
            _add('pct', label or name, val, _dp(val), 'metrics',
                 off_base=_structured_off(label or name))"""
src = splice(src, OLD, NEW, "metrics off flag")

OLD = """    text = str(reply or '')
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
    return claims"""
NEW = """    text = str(reply or '')
    for rx in _IDX_TEXT_RX:
        for m in rx.finditer(text):
            _add('index', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks))
    for rx in _PCT_TEXT_RX:
        for m in rx.finditer(text):
            _add('pct', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks))
    for rx in _DEMO_TEXT_RX:
        for m in rx.finditer(text):
            lab = _DEMO_ALIASES.get(m.group('label').lower(),
                                    m.group('label').lower())
            _add('demo', lab, m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks))
    return claims"""
src = splice(src, OLD, NEW, "text claims attribution")

# --- 3. _candidates_for guards ---------------------------------------
OLD = """    ln = _norm(claim['label'])
    if claim['kind'] == 'index':
        threshold = _income_threshold_from_label(claim['label'])
        if threshold is not None:
            comp = _income_composite_index(lookup, threshold)
            return [comp] if comp is not None else []
    if ln in _GENERIC_LABEL_NORMS:
        return []
    table = lookup['demos'] if claim['kind'] == 'demo' \\
        else lookup['brands']
    rows = list(table.get(ln) or [])
    if not rows and claim['kind'] == 'pct':
        rows = list(lookup['demos'].get(ln) or [])
    if not rows and len(ln) >= 5:
        for key, krows in table.items():
            if len(key) >= 5 and (ln in key or key in ln):
                rows.extend(krows)
            if len(rows) >= 8:
                break"""
NEW = """    ln = _norm(claim['label'])
    if claim['kind'] == 'index':
        threshold = _income_threshold_from_label(claim['label'])
        if threshold is not None:
            comp = _income_composite_index(lookup, threshold)
            return [comp] if comp is not None else []
    if ln in _GENERIC_LABEL_NORMS:
        return []
    # A label ending in a cohort-relation noun ("into Stella Lefty's
    # audience", "her own file") describes a relationship between
    # audiences, never a base row.
    tail_words = [w for w in re.split(r'[^a-z0-9]+',
                                      str(claim['label']).lower()) if w]
    if tail_words and tail_words[-1] in _COHORT_TAIL_TOKENS:
        return []
    subj_norm = _norm(lookup.get('name') or '')
    table = lookup['demos'] if claim['kind'] == 'demo' \\
        else lookup['brands']
    rows = list(table.get(ln) or [])
    if not rows and claim['kind'] == 'pct':
        rows = list(lookup['demos'].get(ln) or [])
    if not rows and len(ln) >= 5:
        for key, krows in table.items():
            # Containment never binds to the subject's own self-pin
            # row; only an exact label match may cite the subject row.
            if subj_norm and key == subj_norm:
                continue
            if len(key) >= 5 and (ln in key or key in ln):
                rows.extend(krows)
            if len(rows) >= 8:
                break"""
src = splice(src, OLD, NEW, "_candidates_for guards")

# --- 4. anchor_check + verify_read thread the question ---------------
OLD = """def anchor_check(reply, res, base_lookup):
    \"\"\"Recompute cited figures from the base rows. Returns
    {'status': 'pass'|'fail'|'skip', 'detail', 'anchored',
     'findings': [...]}.\"\"\"
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
        cands = _candidates_for(c, base_lookup)"""
NEW = """def anchor_check(reply, res, base_lookup, question=None):
    \"\"\"Recompute cited figures from the base rows. Returns
    {'status': 'pass'|'fail'|'skip', 'detail', 'anchored',
     'findings': [...]}. Claims attributed to entities other than the
    base subject (competitor figures in a comparative read) are
    skipped, never held.\"\"\"
    if not base_lookup:
        return {'status': 'skip', 'detail': 'base is not a profile',
                'anchored': 0, 'findings': []}
    claims = extract_claims(
        reply, res, base_name=(base_lookup or {}).get('name') or '',
        comparative=is_comparative_ask(question))
    if not claims:
        return {'status': 'skip', 'detail': 'no recomputable figure cited',
                'anchored': 0, 'findings': []}
    anchored, findings, bound = 0, [], 0
    sample_hits = []
    for c in claims:
        if c.get('off_base'):
            continue
        cands = _candidates_for(c, base_lookup)"""
src = splice(src, OLD, NEW, "anchor_check attribution")

OLD = """def verify_read(*, reply, res, family=None, base_lookup=None,
                prior_entries=None):
    \"\"\"Run all three checks. Returns
    {'ok', 'checks': {'anchor', 'ledger', 'scrub'}, 'findings'}.\"\"\"
    anchor = anchor_check(reply, res, base_lookup)"""
NEW = """def verify_read(*, reply, res, family=None, base_lookup=None,
                prior_entries=None, question=None):
    \"\"\"Run all three checks. Returns
    {'ok', 'checks': {'anchor', 'ledger', 'scrub'}, 'findings'}.\"\"\"
    anchor = anchor_check(reply, res, base_lookup, question=question)"""
src = splice(src, OLD, NEW, "verify_read question param")

PMV.write_text(src, encoding="utf-8")
print(f"prometheus_verify.py patched ({len(src)} bytes)")

# ===================================================================
# app.py: thread question=text into the three verify_read calls
# ===================================================================
src = APP.read_text(encoding="utf-8")

OLD = """            verdict = pmv.verify_read(
                reply=reply, res=res, family=fam0,
                base_lookup=_v_lookup,
                prior_entries=_pm_verify_prior_entries(res, fam0, led))"""
NEW = """            verdict = pmv.verify_read(
                reply=reply, res=res, family=fam0,
                base_lookup=_v_lookup, question=text,
                prior_entries=_pm_verify_prior_entries(res, fam0, led))"""
src = splice(src, OLD, NEW, "verify call 1")

OLD = """                    verdict2 = pmv.verify_read(
                        reply=reply2, res=res2, family=fam2,
                        base_lookup=_v_lookup,
                        prior_entries=_pm_verify_prior_entries(
                            res2, fam2, led))"""
NEW = """                    verdict2 = pmv.verify_read(
                        reply=reply2, res=res2, family=fam2,
                        base_lookup=_v_lookup, question=text,
                        prior_entries=_pm_verify_prior_entries(
                            res2, fam2, led))"""
src = splice(src, OLD, NEW, "verify call 2")

OLD = """                        verdict3 = pmv.verify_read(
                            reply=reply3, res=res3, family=fam3,
                            base_lookup=_v_lookup,
                            prior_entries=_pm_verify_prior_entries(
                                res3, fam3, led))"""
NEW = """                        verdict3 = pmv.verify_read(
                            reply=reply3, res=res3, family=fam3,
                            base_lookup=_v_lookup, question=text,
                            prior_entries=_pm_verify_prior_entries(
                                res3, fam3, led))"""
src = splice(src, OLD, NEW, "verify call 3")

APP.write_text(src, encoding="utf-8")
print(f"app.py patched ({len(src)} bytes)")
print("OK")
