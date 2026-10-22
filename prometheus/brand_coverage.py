"""Brand coverage lane (2026-10-08).

"Is Alexa measured in Crosswalk?" (Zoe) and "what would alexa and echo
be listed under in the behavioral tab" (Scott) both drew the open-page
confirm and were only answered when the user re-sent the question.
They are lookups: whether a brand is one we carry, which behavioral
category it lives under, and what the open profile shows for it. The
Gen Pop file carries every brand we measure with its category, and the
open profile carries its rows for this audience, so the answer needs
no model call.

Fail-safe: anything the lane cannot settle returns None and the ask
proceeds as before.
"""
from __future__ import annotations

import io
import re
import time

_ASK_RXS = (
    # is X measured / tracked / covered / in the data
    re.compile(r"^\s*(?:is|are)\s+(?P<b>.+?)\s+(?:measured|tracked|covered|captured|included|"
               r"available|in (?:the )?(?:data|panel|dashboard|system)|in crosswalk|"
               r"something (?:you|we) (?:measure|track|see)|in your data)\b", re.I),
    # do you / we measure / track / cover X
    re.compile(r"^\s*(?:do|does|can)\s+(?:you|we|crosswalk|prometheus)\s+(?:measure|track|cover|capture|see|carry)\s+"
               r"(?P<b>.+?)[\s?.!]*$", re.I),
    # what category / tab / section is X listed under; where does X live
    re.compile(r"^\s*(?:what|which)\s+(?:behavioral\s+)?(?:category|tab|section|column|bucket|group)\s+"
               r"(?:is|are|would|does|do|will)\s+(?P<b>.+?)\s+(?:be\s+)?(?:listed|found|live|sit|fall|show|appear|land)"
               r"(?:\s+(?:under|in|on))?", re.I),
    re.compile(r"^\s*(?:what|which)\s+(?:behavioral\s+)?(?:category|tab|section|column|bucket)\s+(?:is|are)\s+(?P<b>.+?)\s+(?:in|under)\b", re.I),
    # what would X be listed under (Scott, 2026-10-07)
    re.compile(r"^\s*(?:what|which)\s+(?:would|does|do|is|are|will)\s+(?P<b>.+?)\s+(?:be\s+)?"
               r"(?:listed|found|live|sit|fall|show(?:\s+up)?|appear|land|categori[sz]ed|filed)(?:\s+(?:under|in|on|as))?", re.I),
    re.compile(r"^\s*where\s+(?:is|are|would|does|do|will|can i find)\s+(?P<b>.+?)\s+(?:listed|found|live|sit|show|appear|fall|land|be)"
               r"(?:\s+(?:under|in|on))?", re.I),
    re.compile(r"^\s*where\s+(?:is|are)\s+(?P<b>.+?)\s+(?:in|on)\s+(?:the\s+)?(?:behavioral tab|dashboard|data|profile)", re.I),
    # X is listed under what / falls under which category
    re.compile(r"^\s*(?P<b>.+?)\s+(?:is|are)\s+(?:listed|found|measured)\s+(?:under|in)\s+(?:what|which)\b", re.I),
)
_TAIL_RX = re.compile(r"\s+(?:in|on)\s+(?:the\s+)?(?:behavioral\s+tab|dashboard|data|profile|panel|crosswalk|system|platform)\b.*$", re.I)
_BEHAVIOR_TAIL_RX = re.compile(r"\s+(?:purchases?|activity|data|usage|viewing|sessions?|visits?|engagement|behaviou?r|spend(?:ing)?)$", re.I)
_SPLIT_RX = re.compile(r"\s*(?:,|\band\b|&|\bor\b|/)\s*", re.I)
_PARENTS = frozenset(('amazon', 'google', 'apple', 'meta', 'microsoft', 'samsung', 'sony', 'disney',
                      'nbc', 'cbs', 'fox', 'abc', 'hbo', 'paramount', 'warner', 'comcast', 'verizon',
                      'att', 'at&t', 'tmobile', 'the'))
_GENERIC = frozenset(('it', 'this', 'that', 'they', 'them', 'these', 'those', 'brands', 'brand',
                      'data', 'anything', 'something', 'everything', 'people', 'audience'))

_CACHE: dict = {}
_TTL = 600


def _tokens(s):
    return [t for t in re.split(r"[^a-z0-9+]+", str(s or '').lower()) if t]


# A thousands-grouped figure is one token (2026-10-22, Alexia: "where does
# the 12,004,203 potential ticket buyers number come from" was split on its
# commas into the brands "12" and "004"). The comma inside \d,\d{3} is never
# a list separator, and a question about where a figure comes from is a
# question about the previous answer, never a coverage ask.
_FIGURE_RX = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_PROVENANCE_RX = re.compile(r"\b(?:come|comes|coming|came)\s+from\b|\bnumber\b|\bfigure\b|\bwhere\s+(?:did|does|do)\s+you\s+get\b", re.I)


_SPLIT_KEEP_RX = re.compile(r"(\s*(?:,|\band\b|&|\bor\b|/)\s*)", re.I)


def _split_keep_figures(raw, resolves=None):
    """Split a brand list on , / and / & / or, never inside 12,004,203,
    and never inside a name that carries '&' or 'and' (2026-10-22, the
    same class as the figure split: 'Ben & Jerry's' is one brand, 'Law &
    Order: SVU' one title, 'Netflix and Hulu' two services). With a
    resolver (does this string name something we carry?) two pieces
    joined by & / and stay one when the joined name resolves, or when
    neither piece resolves on its own; they split when both do, or when
    exactly one does (the known brand keeps its own answer)."""
    hold = {}

    def _stash(m):
        key = f"\x00{len(hold)}\x00"
        hold[key] = m.group(0)
        return key

    def _restore(p):
        for key, val in hold.items():
            p = p.replace(key, val)
        return p.strip(' ?.!,"\'')
    safe = _FIGURE_RX.sub(_stash, raw)
    pieces = _SPLIT_KEEP_RX.split(safe)
    parts = [_restore(x) for x in pieces[0::2]]
    seps = [str(x or '') for x in pieces[1::2]]
    if resolves is None or len(parts) < 2:
        return [p for p in parts if p]
    out = [parts[0]]
    for sep, nxt in zip(seps, parts[1:]):
        cur = out[-1]
        if sep.strip().lower() in ('&', 'and') and cur and nxt:
            joined = f"{cur}{sep}{nxt}"          # the reader's own spacing (AT&T stays AT&T)
            try:
                joined_ok = resolves(joined) or resolves(f"{cur} & {nxt}") or resolves(f"{cur} and {nxt}")
                a_ok, b_ok = resolves(cur), resolves(nxt)
            except Exception:
                joined_ok, a_ok, b_ok = False, True, True
            if joined_ok or not (a_ok or b_ok):
                out[-1] = joined
                continue
        out.append(nxt)
    return [p for p in out if p]


def parse(text, resolves=None):
    """List of brand strings the ask is about, or [] when it is not a
    coverage question. `resolves(name) -> bool` keeps a compound name
    whole (see _split_keep_figures)."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 220:
        return []
    if _FIGURE_RX.search(t) and _PROVENANCE_RX.search(t):
        return []   # a question about a stated figure, not about a brand
    for rx in _ASK_RXS:
        m = rx.match(t)
        if not m:
            continue
        raw = _TAIL_RX.sub('', m.group('b')).strip(' ?.!,"\'')
        raw = re.sub(r"^(?:the|a|an)\s+", '', raw, flags=re.I)
        parts = _split_keep_figures(raw, resolves)
        out = []
        for p in parts:
            p = _BEHAVIOR_TAIL_RX.sub('', p).strip()
            toks = _tokens(p)
            if not toks or len(toks) > 5 or all(x in _GENERIC for x in toks):
                continue
            if all(re.fullmatch(r"[\d,.%$]+", x) for x in p.split()):
                continue   # a number is never a brand
            out.append(p)
        return out[:4]
    return []


def _frame(host, key):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    import pandas as pd
    body = host.s3_client.get_object(Bucket=host.bucket, Key=key)['Body'].read()
    df = pd.read_csv(io.BytesIO(body))
    _CACHE[key] = (now, df)
    return df


def _matches(df, brand):
    """[(column, value, bp, index)] rows that are this brand: exact
    token match, or the brand under a parent prefix (Amazon Alexa for
    alexa). Near-matches (other words attached) come back separately."""
    want = _tokens(brand)
    if not want:
        return [], []
    bp_col = next((c for c in df.columns if 'penetration' in c.lower() and 'row' in c.lower()), None) \
        or next((c for c in df.columns if 'penetration' in c.lower()), None)
    idx_col = next((c for c in df.columns if 'index' in c.lower()), None)
    exact, near = [], []
    skip_cols = {'BRAND INPUT', 'SAMPLE SIZE', 'SUBJECT', 'BRAND CATEGORY', 'GENDER', 'AGE', 'ETHNICITY',
                 'EDUCATION', 'INCOME', 'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'LOCATION'}
    for col, val, bp, ix in zip(df['Column'].astype(str), df['Value'].astype(str),
                                df[bp_col] if bp_col else [None] * len(df),
                                df[idx_col] if idx_col else [None] * len(df)):
        if col.upper() in skip_cols:
            continue
        have = _tokens(val)
        if not all(w in have for w in want):
            continue
        extra = [h for h in have if h not in want]
        try:
            bpv = float(str(bp).replace('%', '')) if bp is not None and str(bp) not in ('nan', '') else None
        except ValueError:
            bpv = None
        try:
            ixv = float(ix) if ix is not None and str(ix) not in ('nan', '') else None
        except ValueError:
            ixv = None
        row = (col, val, bpv, ixv)
        if not extra or all(e in _PARENTS for e in extra):
            exact.append(row)
        else:
            near.append(row)
    return exact, near


def _pct(v):
    return f"{v:.1f}%" if isinstance(v, float) else ''


def _pretty(name):
    """Gen Pop spellings are often ALL CAPS; present a brand as a name."""
    n = str(name or '').strip()
    if not n:
        return n
    if n.isupper() and len(n) > 4:
        return ' '.join(w if (len(w) <= 3 and w.isalpha()) else w.capitalize() for w in n.split())
    if n.islower():
        return ' '.join(w.capitalize() for w in n.split())
    return n


def _name(rows):
    # the most common spelling across the rows
    names = {}
    for _c, v, _b, _i in rows:
        names[v] = names.get(v, 0) + 1
    return max(names, key=names.get) if names else ''


def answer(text, ctx=None, host=None):
    brands = parse(text)
    if not brands:
        return None
    if host is None:
        try:
            from . import host as _host
            host = _host
        except Exception:
            return None
    page = ''
    page_key = ''
    try:
        page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        page_key = str(((ctx or {}).get('primary') or {}).get('s3_key') or '').strip()
    except Exception:
        pass
    try:
        gp = _frame(host, 'Gen_Pop_2026.csv')
    except Exception:
        return None
    prof = None
    if page_key:
        try:
            prof = _frame(host, page_key)
        except Exception:
            prof = None

    def _resolves(name):
        try:
            if _matches(gp, name)[0]:
                return True
            return prof is not None and bool(_matches(prof, name)[0])
        except Exception:
            return False
    # the list again, with the brand universe in hand: a name that
    # carries & / and stays one brand (2026-10-22)
    brands = parse(text, resolves=_resolves) or brands
    lines, chips = [], []
    for b in brands:
        g_exact, g_near = _matches(gp, b)
        p_exact = _matches(prof, b)[0] if prof is not None else []
        label = _pretty(_name(g_exact) or _name(p_exact) or b)
        if g_exact or p_exact:
            cols = []
            for c, _v, _bp, _i in (g_exact or p_exact):
                if c not in cols:
                    cols.append(c)
            where = cols[0] if len(cols) == 1 else ', '.join(cols[:-1]) + ' and ' + cols[-1]
            line = f"Yes, {label} is measured. It lives under {where}"
            if p_exact:
                c, v, bpv, ixv = p_exact[0]
                if bpv is not None:
                    line += f"; on {page} it reads {_pct(bpv)} of the audience"
                    if ixv:
                        line += f" (index {ixv:.0f} against the US)"
                line += '.'
            elif page:
                line += f". {page} does not carry a row for it in this window."
            else:
                line += '.'
            lines.append(line)
        else:
            line = f"{label} is not one of the brands we carry on its own today."
            if g_near:
                c, v, _bp, _i = g_near[0]
                line += f" The closest name we do carry is {_pretty(v)} under {c}, which is a different brand."
            lines.append(line)
    if not lines:
        return None
    reply = '\n\n'.join(lines)
    if page:
        chips = [f"Show me the top brands under {(_matches(gp, brands[0])[0] or [('', '', None, None)])[0][0] or 'this category'} on {page}"]
        chips = [c for c in chips if 'this category' not in c][:1]
    return {'success': True, 'action': 'answer', 'reply': reply,
            'followups': chips, 'offer_deck': False, 'deck_angle': None,
            'subject': page or None}
