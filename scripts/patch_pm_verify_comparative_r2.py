#!/usr/bin/env python3
"""Round 2 of the comparative-verify fix (same defect, two refinements).

1. Subject-name claims never bind. The only base row under the
   subject's own name is the structural 100% self-pin; binding
   "Stella Lefty lands at 76.2% female" (pct capture, label 'Stella
   Lefty') against it is the same false-contradiction class as the
   overlap phrasing. Exact matches on the subject key now return no
   candidates, same as containment.
2. Possessive attributors inside the label window mark the claim
   off-base. "Gracie Abrams' audience shows Spotify at 81.4%" puts
   the competitor inside the captured label, invisible to the
   sentence-before scan. A possessive foreign entity between label
   start and the value now counts as the owner of the figure.
   Non-possessive label entities (the bind target itself, "Her
   Spotify share") stay exempt.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PMV = ROOT / "prometheus_verify.py"


def splice(src, old, new, desc, count=1):
    n = src.count(old)
    if n != count:
        raise RuntimeError(f"[{desc}] anchor found {n}x (want {count})")
    return src.replace(old, new)


src = PMV.read_text(encoding="utf-8")

# --- 1. _off_base_text_claim: label-zone possessive attributors ------
OLD = '''def _off_base_text_claim(text, m_start, m_end, base_toks):
    """True when the sentence around a text claim attributes the
    figure to someone other than the base subject: a named competitor
    is the nearest entity before the figure, another multi-word name
    follows it in the same sentence, or the sentence is a field
    comparison with the base subject never named before the figure.
    Off-base claims are skipped by the anchor pass (fail-open)."""
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
    return bool(_SENT_COMPARE_RX.search(text[s:e]))'''
NEW = '''def _off_base_text_claim(text, m_start, m_end, base_toks,
                         lab_start=None, val_start=None):
    """True when the sentence around a text claim attributes the
    figure to someone other than the base subject: a named competitor
    is the nearest entity before the claim, a possessive competitor
    sits inside the label window itself ("Gracie Abrams' audience
    shows Spotify at 81.4%"), another multi-word name follows in the
    same sentence, or the sentence is a field comparison with the base
    subject never named before the figure. Off-base claims are skipped
    by the anchor pass (fail-open)."""
    s, e = _sentence_bounds(text, m_start, m_end)
    before = text[s:m_start]
    base_b, foreign_b = _entities_in(before, base_toks)
    # Possessive attributors inside the label window own the figure.
    # Non-possessive label entities are the bind target itself ("Her
    # Spotify share") and stay exempt.
    if lab_start is not None and val_start is not None \\
            and lab_start < val_start:
        for m2 in _ENTITY_SEQ_RX.finditer(text[lab_start:val_start]):
            seg = m2.group(0).rstrip()
            if not seg.endswith(("'", "'s", "\\u2019", "\\u2019s")):
                continue
            toks = {re.sub(r'[^a-z0-9]', '', w.lower())
                    for w in seg.split()}
            toks.discard('')
            if toks and not (toks & base_toks) \\
                    and not all(t in _ENTITY_COMMON for t in toks):
                foreign_b.append((lab_start + m2.start(), seg))
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
    return bool(_SENT_COMPARE_RX.search(text[s:e]))'''
src = splice(src, OLD, NEW, "off_base label-zone possessives")

# --- 2. text extractors pass label/value offsets ----------------------
OLD = """    for rx in _IDX_TEXT_RX:
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
                     text, m.start(), m.end(), base_toks))"""
NEW = """    for rx in _IDX_TEXT_RX:
        for m in rx.finditer(text):
            _add('index', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks,
                     lab_start=m.start('label'),
                     val_start=m.start('val')))
    for rx in _PCT_TEXT_RX:
        for m in rx.finditer(text):
            _add('pct', m.group('label'), m.group('val'),
                 _dp(m.group('val'), m.group('val')), 'text',
                 off_base=_off_base_text_claim(
                     text, m.start(), m.end(), base_toks,
                     lab_start=m.start('label'),
                     val_start=m.start('val')))"""
src = splice(src, OLD, NEW, "extractor offsets")

# --- 3. subject-name claims never bind --------------------------------
OLD = """    subj_norm = _norm(lookup.get('name') or '')
    table = lookup['demos'] if claim['kind'] == 'demo' \\
        else lookup['brands']
    rows = list(table.get(ln) or [])"""
NEW = """    subj_norm = _norm(lookup.get('name') or '')
    # The only base row under the subject's own name is the structural
    # 100% self-pin. A penetration attributed to the subject's name is
    # audience-share phrasing, never a citation of that pin; binding it
    # manufactures a contradiction. Subject-name claims never bind.
    if subj_norm and ln == subj_norm:
        return []
    table = lookup['demos'] if claim['kind'] == 'demo' \\
        else lookup['brands']
    rows = list(table.get(ln) or [])"""
src = splice(src, OLD, NEW, "subject-name never binds")

PMV.write_text(src, encoding="utf-8")
print(f"prometheus_verify.py round-2 patched ({len(src)} bytes)")
