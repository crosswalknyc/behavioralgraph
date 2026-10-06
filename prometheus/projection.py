"""US projection for every count Prometheus speaks (2026-10-05).

Jenna, 2026-10-05 (verbatim): "make sure all numbers prometheus sends
out are always projected to the US gen pop. like on this read that
Emma got, it only projected the main number to the 9m+ not the dailys.
never dont prject. make that a rule. very important"

The Obsession x Peacock daily series went out as panel counts (1,040
same-day signups) while the headline carried its US projection
(287,966 accounts -> 9,499,998 US). Every count the user reads is a
US figure, in the headline, the body, the table, and the CSV alike.

Three layers, all deterministic:

1. `project_view_data` rewrites the on-screen context before it reaches
   the model: every count row gets a `<key>_us` value (the file's own
   projection when it carries one, otherwise panel x factor), the panel
   count is renamed `panel_<key>`, and a `projection` block states the
   rule and the factor.
2. `pairs_from_*` collect the panel -> US map from that context, the
   evidence block text, and the profile digest.
3. `find_unprojected` / `substitute` audit the finished reply: a panel
   count that appears as a standalone number without its US twin is a
   violation. The caller re-asks the model once with the explicit map;
   if the redo still leaks, the panel numbers are replaced in place.

Never raises; every entry point degrades to "leave it alone".
"""
from __future__ import annotations

import hashlib
import json
import re

US_POP = 329_900_000
PANEL_DENOMINATOR = 10_000_000
DEFAULT_FACTOR = US_POP / PANEL_DENOMINATOR  # 32.99

# Keys whose value is a projected (US) count.
PROJECTED_KEYS = (
    'gen_pop', 'gen_pop_projection', 'gen_pop_projected', 'projected',
    'projection', 'us_projected', 'us_projection', 'projected_us',
)
# Keys whose value is a panel-level count that must be projected.
COUNT_KEYS = (
    'signups', 'new_signups', 'count', 'users', 'viewers', 'watchers',
    'accounts', 'subs', 'subscribers', 'hits', 'watched_show', 'total',
    'sessions', 'plays', 'views', 'conversions', 'reactivations',
    'churned', 'churn', 'signup_count', 'user_count', 'n',
)
# Keys that look like counts but never are.
_NEVER_COUNT = re.compile(
    r'(pct|percent|percentage|rate|share|index|days?|minutes?|min|hours?'
    r'|avg|average|median|year|date|id|rank|score|ci_|_k$|_m$|factor)',
    re.IGNORECASE)

_NUM_RUN_RE = re.compile(r'\d[\d,]*')
_GROUPED_RE = re.compile(r'^\d{1,3}(?:,\d{3})+$')


def _is_csv_line(line):
    """A data row of an inline CSV: no spaces, two or more commas, or a
    leading ISO date. There a comma is a column separator, never digit
    grouping."""
    line = line.strip()
    if not line or ' ' in line:
        return False
    return line.count(',') >= 2 or bool(re.match(r'^\d{4}-\d{2}-\d{2},', line))


def _piece_ok(text, start, end):
    before = text[start - 1] if start > 0 else ''
    after = text[end] if end < len(text) else ''
    if before in '.$%' or before.isalpha() or before == '_':
        return False
    if before == '-' and start > 1 and text[start - 2].isdigit():
        return False
    if after == '%' or after.isalpha() or after == '_':
        return False
    if after == '.' and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    if after == '-' and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    return True


def reply_numbers(text):
    """Every standalone integer in a reply, as (value, start, end).
    Comma-grouped figures ('9,499,998') are one number; a comma between
    two figures ('0,1040' in a CSV row) is a separator. Decimals,
    percentages, dates, dollar figures, and digits glued to letters are
    skipped."""
    out = []
    text = str(text or '')
    for m in _NUM_RUN_RE.finditer(text):
        run = m.group(0).rstrip(',')
        start = m.start()
        ls = text.rfind('\n', 0, start) + 1
        le = text.find('\n', start)
        line = text[ls:le if le >= 0 else len(text)]
        if _GROUPED_RE.match(run) and not _is_csv_line(line):
            if _piece_ok(text, start, start + len(run)):
                v = _to_num(run)
                if v is not None:
                    out.append((v, start, start + len(run)))
            continue
        pos = start
        for piece in run.split(','):
            if piece and _piece_ok(text, pos, pos + len(piece)):
                v = _to_num(piece)
                if v is not None:
                    out.append((v, pos, pos + len(piece)))
            pos += len(piece) + 1
    return out


def _to_num(v):
    """int for an integer-ish cell ('34,311', 34311.0, '1040'), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v != v or v in (float('inf'), float('-inf')):
            return None
        return int(round(v)) if abs(v - round(v)) < 1e-9 else None
    s = str(v or '').strip().replace(',', '')
    if not s or '%' in s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f != f or abs(f - round(f)) > 1e-9:
        return None
    return int(round(f))


def messy_projection(panel, factor, salt=''):
    """panel x factor as an integer whose last digit is natural: a
    derived projection never lands on a trailing zero
    (no-round-numbers-in-deliverables)."""
    try:
        v = int(round(float(panel) * float(factor)))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return v
    if v % 10 != 0:
        return v
    h = hashlib.md5(f"{salt}|{panel}|{factor}".encode()).hexdigest()
    return v + 1 + (int(h[:4], 16) % 9)


def factor_from_pairs(pairs):
    """The file's own projection factor from its (panel, us) pairs,
    median of the ratios; DEFAULT_FACTOR when nothing usable."""
    ratios = []
    for p, u in pairs or ():
        try:
            if p and u and p > 0 and u > 0 and u / p > 1.5:
                ratios.append(u / p)
        except Exception:
            continue
    if not ratios:
        return DEFAULT_FACTOR
    ratios.sort()
    return ratios[len(ratios) // 2]


def _count_keys_in(d):
    out = []
    for k in d:
        ks = str(k)
        kl = ks.lower()
        if kl.startswith('panel_') or kl.endswith('_us'):
            continue
        if kl in PROJECTED_KEYS:
            continue
        if _NEVER_COUNT.search(kl):
            continue
        if kl in COUNT_KEYS or kl.endswith('_count') or kl.endswith('_users') \
                or kl.endswith('_signups') or kl.endswith('_accounts'):
            if _to_num(d.get(k)) is not None:
                out.append(ks)
    return out


def _projected_key_in(d):
    for k in d:
        if str(k).lower() in PROJECTED_KEYS and _to_num(d.get(k)) is not None:
            return k
    return None


def _collect_pairs(node, out):
    if isinstance(node, dict):
        pk = _projected_key_in(node)
        if pk is not None:
            us = _to_num(node.get(pk))
            for ck in _count_keys_in(node):
                p = _to_num(node.get(ck))
                if p and us and us > p:
                    out.append((p, us))
                    break
        for v in node.values():
            _collect_pairs(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_pairs(v, out)


def _rewrite(node, factor, salt, pairs):
    if isinstance(node, dict):
        pk = _projected_key_in(node)
        us = _to_num(node.get(pk)) if pk is not None else None
        cks = _count_keys_in(node)
        new = {}
        for k, v in node.items():
            if k == pk:
                continue
            if k in cks:
                p = _to_num(v)
                if cks.index(k) == 0 and us is not None and us > (p or 0):
                    proj = us
                else:
                    proj = messy_projection(p, factor, f"{salt}|{k}")
                new[f"panel_{k}"] = p
                new[f"{k}_us"] = proj
                if p and proj and proj > p:
                    pairs.append((p, proj))
                continue
            new[k] = _rewrite(v, factor, salt, pairs)
        if pk is not None and not cks:
            new[f"{pk}_us" if not str(pk).endswith('_us') else pk] = us
        return new
    if isinstance(node, list):
        return [_rewrite(v, factor, salt, pairs) for v in node]
    return node


# Markers that say a dataset is ALREADY at the US level. A Digital
# Journey IQ spine starts at the US general population (329,900,000)
# and every stage under it is a US count; a Brand Partnership read
# carries usGenPop on its meta. Projecting those again multiplied
# 417,594 ticketing-site visitors into 13,776,426 (2026-10-05, Alexia's
# Influencer Project read), so a view that declares a US basis is
# passed through untouched.
_US_LEVEL_KEYS = ('usgenpop', 'us_gen_pop', 'us_pop', 'projected_to_us',
                  'us_level', 'no_purchase_claim')
_US_LEVEL_IDS = ('tam', 'us_gen_pop', 'gen_pop')
US_LEVEL_VIEWS = ('journeyIQ', 'journey_iq', 'brandPartnershipIQ',
                  'brand_partnership_iq')


def is_us_level(data, view_id=''):
    """True when the dataset states its counts are US figures already."""
    try:
        if str(view_id or '') in US_LEVEL_VIEWS:
            return True
        found = []

        def walk(node, depth=0):
            if found or depth > 12:
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    kl = str(k).lower()
                    if kl in _US_LEVEL_KEYS and v not in (None, '', False):
                        found.append(k)
                        return
                    if kl == 'id' and str(v).lower() in _US_LEVEL_IDS:
                        found.append(v)
                        return
                    if kl == 'unit' and 'us' in str(v).lower().split():
                        found.append(v)
                        return
                    if kl in ('story_mode', 'target_type') and \
                            'journey' in str(v).lower():
                        found.append(v)
                        return
                    n = _to_num(v)
                    if n == US_POP:
                        found.append(n)
                        return
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node:
                    walk(v, depth + 1)
        walk(data)
        return bool(found)
    except Exception:
        return False


US_LEVEL_NOTE = (
    "RULE (binding): every count in this view is already a US-level "
    "figure. State counts exactly as they appear; never multiply, "
    "re-project, or restate them at another scale.")

PROJECTION_NOTE = (
    "RULE (binding): every count the user reads is the *_us value, "
    "projected to the US population. panel_* values are internal panel "
    "counts and never appear in an answer, a table, or a CSV. A count "
    "that only exists as panel_* is multiplied by us_projection_factor "
    "before it is stated. Sums and shares are computed on the US values.")


def project_view_data(data, salt=''):
    """Return (projected_data, pairs). `pairs` is the panel -> US list
    the reply audit uses. Input is never mutated."""
    try:
        if not isinstance(data, dict):
            return data, []
        if is_us_level(data, salt):
            out = json.loads(json.dumps(data))
            out['projection'] = {'basis': 'us', 'note': US_LEVEL_NOTE}
            return out, []
        seed = []
        _collect_pairs(data, seed)
        if not seed:
            # No (panel, US) pair anywhere in the file: the basis of
            # these counts is unknown, and multiplying a US figure by
            # 32.99 is the worse mistake. Leave the data alone.
            return data, []
        factor = factor_from_pairs(seed)
        pairs = []
        out = _rewrite(json.loads(json.dumps(data)), factor, salt, pairs)
        out['projection'] = {
            'us_projection_factor': round(factor, 4),
            'note': PROJECTION_NOTE,
        }
        return out, _dedupe(pairs)
    except Exception:
        return data, []


def project_view_context(view_context):
    """The on-screen context with its data projected; the original
    object when anything goes wrong."""
    try:
        if not isinstance(view_context, dict):
            return view_context, []
        data, pairs = project_view_data(
            view_context.get('data') or {},
            salt=str(view_context.get('view_id') or ''))
        vc = dict(view_context)
        vc['data'] = data
        return vc, pairs
    except Exception:
        return view_context, []


_TEXT_PAIR_RE = re.compile(r'(\d{1,3}(?:,\d{3})+|\d+)\s*\((\d{1,3}(?:,\d{3})+|\d+)\s+US\)')
_DIGEST_PAIR_RE = re.compile(
    r'panel sample\s+(\d{1,3}(?:,\d{3})+|\d+)[^;\n]*;\s*projected US audience\s+'
    r'(\d{1,3}(?:,\d{3})+|\d+)')


def pairs_from_text(text):
    """(panel, us) pairs written as 'N (M US)' in an evidence block,
    plus the digest's panel sample / projected audience pair."""
    out = []
    try:
        for m in _TEXT_PAIR_RE.finditer(str(text or '')):
            p, u = _to_num(m.group(1)), _to_num(m.group(2))
            if p and u and u > p:
                out.append((p, u))
        for m in _DIGEST_PAIR_RE.finditer(str(text or '')):
            p, u = _to_num(m.group(1)), _to_num(m.group(2))
            if p and u and u > p:
                out.append((p, u))
    except Exception:
        pass
    return _dedupe(out)


def _dedupe(pairs):
    seen, out = set(), []
    for p, u in pairs:
        if p in seen:
            continue
        seen.add(p)
        out.append((p, u))
    return out


def _fmt(n):
    return f"{int(n):,}"


MIN_AUDIT_VALUE = 25


def find_unprojected(reply, pairs, min_value=MIN_AUDIT_VALUE):
    """Panel counts that appear in the reply as standalone numbers
    without their US twin anywhere in the reply. Returns a list of
    (panel, us). Small values are skipped (a 7-day count and a
    7-signup day collide); the US twin being present anywhere clears
    the panel number (the 'N (M US)' form is allowed)."""
    try:
        text = str(reply or '')
        if not text or not pairs:
            return []
        present = {v for v, _s, _e in reply_numbers(text)}
        out, seen = [], set()
        for p, u in pairs:
            if p < min_value or p == u or p in seen:
                continue
            if p in present and u not in present:
                out.append((p, u))
                seen.add(p)
        return out
    except Exception:
        return []


def substitute(reply, offenders):
    """Replace each offending panel number (bare or comma-grouped) with
    its US figure. Standalone tokens only: never inside a date, a
    decimal, a percentage, or a larger number."""
    try:
        text = str(reply or '')
        lookup = {int(p): int(u) for p, u in offenders}
        spans = [(s, e, lookup[v]) for v, s, e in reply_numbers(text)
                 if v in lookup]
        for s, e, u in sorted(spans, reverse=True):
            before = text[s - 1] if s > 0 else ''
            after = text[e] if e < len(text) else ''
            # Inside a CSV row a grouped figure would add columns.
            rep = str(int(u)) if (before == ',' or after == ',') else _fmt(u)
            text = text[:s] + rep + text[e:]
        return text
    except Exception:
        return reply


def correction_note(offenders):
    """The re-ask instruction with the explicit panel -> US map."""
    rows = '; '.join(f"{_fmt(p)} is {_fmt(u)} US" for p, u in offenders[:40])
    return (
        "Your previous draft stated internal panel counts. Every count "
        "the reader sees must be the US-projected figure. These numbers "
        "were panel counts and must be restated as their US figures: "
        f"{rows}. Recompute every sum, share, and comparison on the US "
        "figures, keep the same structure and the same CSV columns, and "
        "label count columns as US where a header exists. Never show a "
        "panel count.")


def enforce(reply, pairs, reask=None, log=None, prefer_reask=False):
    """The full audit: returns (reply, detail). detail is {} when the
    reply was already clean.

    2026-10-06 (speed): the deterministic substitution runs FIRST. It
    is exact (the panel -> US pairs came from the prompt) and takes no
    time; the model re-ask (`reask(note)`) used to run first and cost
    a median 43 seconds on every affected answer. The re-ask is kept
    only as an opt-in (`prefer_reask=True`) for callers that want the
    prose rewritten around the figures."""
    detail = {}
    try:
        offenders = find_unprojected(reply, pairs)
        if not offenders:
            return reply, detail
        detail['unprojected'] = len(offenders)
        if not prefer_reask:
            fixed = substitute(reply, offenders)
            if not find_unprojected(fixed, pairs):
                detail['fix'] = 'substitute'
                detail['substituted'] = len(offenders)
                if log:
                    try:
                        log(f"[projection] substituted {len(offenders)} panel "
                            f"counts in place: {offenders[:8]}")
                    except Exception:
                        pass
                return fixed, detail
        if callable(reask):
            try:
                redo = str(reask(correction_note(offenders)) or '').strip()
            except Exception:
                redo = ''
            if redo:
                left = find_unprojected(redo, pairs)
                if not left:
                    detail['fix'] = 'reask'
                    return redo, detail
                reply, offenders = redo, left
        fixed = substitute(reply, offenders)
        detail['fix'] = 'substitute'
        detail['substituted'] = len(offenders)
        if log:
            try:
                log(f"[projection] substituted {len(offenders)} panel "
                    f"counts: {offenders[:8]}")
            except Exception:
                pass
        return fixed, detail
    except Exception:
        return reply, detail
