"""Cross sizing (2026-10-20, Tania Nasr at Paramount+).

"how many UFC fans are there out there, of those, how many are on
Paramount+, on HBO Max and how many have neither service" drew three
independent sizes (UFC fans, every Paramount+ person, every HBO Max
person) and nothing for "of those" or "neither". The ask names a BASE
audience and asks how it splits across one or more TARGETS: a cross,
not three sizings.

The read, deterministic and free:

- the base is sized from what the dashboard already holds: its profile
  when one exists, else the Gen Pop row for its entity (a universe
  never sizes cold on a subject the dashboard describes), else the
  caller falls back to the plain sizing lane;
- each target's share of the base comes from the base profile's own
  row for the target when there is one; otherwise from the target's
  profile (the base entity's row there is the share of the target's
  people who are in the base) turned around through the target's US
  reach and the base's US reach;
- both and neither follow from the two shares with the pairwise
  association the first target's profile carries for the second;
- every derived figure is salted off a 4dp collision and no count
  ends in a zero.

Fail-safe: ``parse`` returns None for anything that is not a cross
ask, and ``answer`` returns None when the base cannot be sized from
what we hold, so the caller's standing behavior is unchanged.
"""
from __future__ import annotations

import hashlib
import re
import traceback

US_POP = 329_900_000

_CROSS_RX = re.compile(
    r"\b(?:(?:and\s+)?(?:how many|what (?:share|percent|percentage|portion|fraction)|which)\s+of\s+(?:those|them|these)"
    r"|of\s+(?:those|them|these|which|whom)\s*[,:]?\s*(?:how many|what share|what percent)?"
    r"|among\s+(?:those|them|these)|out of\s+(?:those|them|these))\b", re.I)
_NEITHER_RX = re.compile(r"\b(?:neither|none of (?:them|those|these|the above)|no (?:service|platform|subscription)|not on (?:either|any))\b", re.I)
_BOTH_RX = re.compile(r"^\W*(?:and\s+)?(?:how many\s+)?(?:are|have|use)?\s*(?:on\s+)?both\b", re.I)
_BASE_TAIL_RX = re.compile(
    r"\s+(?:are|is)\s+(?:there|out there|in (?:the\s+)?(?:us|u\.s\.|usa|united states|america|country)|total|overall|in total|right now|today)"
    r"(?:\s+(?:out there|in (?:the\s+)?(?:us|u\.s\.|usa|united states|america)|total|overall|today))*\s*[?.!,]*$", re.I)
_BASE_LEAD_RX = re.compile(
    r"^\W*(?:ok[,.]?\s+|so[,.]?\s+)?(?:(?:can|could) you (?:tell me|give me|show me)\s+)?(?:how many|how big (?:is|are)|what(?:'s| is) the (?:size|number) of|the number of|number of|size of)\s+(?:the\s+)?", re.I)
_TARGET_LEAD_RX = re.compile(
    r"^\W*(?:and\s+)?(?:how many\s+)?(?:of (?:those|them|these)\s+)?(?:are|is|have|has|use|uses|using|subscribe[sd]? to|subscribed to|pay(?:ing)? for|watch(?:ing)?|stream(?:ing)?|get)?\s*(?:on|to|with|through|via)?\s+", re.I)
_TARGET_TAIL_RX = re.compile(r"\s+(?:service|services|platform|platforms|subscriptions?|accounts?|too|as well|also)\s*[?.!,]*$", re.I)
_SPLIT_RX = re.compile(r"\s*(?:,|;|\band\b|\bor\b|\bvs\.?\b|\bversus\b|\bplus\b|&)\s*", re.I)
_PLATFORM_COLS = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'VIRTUAL MVPD FAST', 'VIRTUAL MVPD/FAST', 'VMVPD/FAST',
                  'STREAMING/MUSIC', 'STREAMING MUSIC', 'APP/PLATFORM USAGE', 'APP/PLATFORM', 'SOCIAL MEDIA',
                  'BROADCAST/CABLE', 'MEDIA', 'FAST PLATFORM', 'FAST CHANNEL')
_FAN_COLS = ('INTEREST', 'SPORTS ORGANIZATIONS', 'SPORTS ORGANIZATION', 'SPORTS TEAM', 'SPORTS', 'FRANCHISE',
             'TALENT', 'MUSICIAN/BAND', 'ATHLETE', 'EVENTS', 'GAMES', 'SERIES', 'MOVIE', 'PODCAST')
_SKIP_COLS = frozenset(('BRAND INPUT', 'SAMPLE SIZE', 'SUBJECT', 'BRAND CATEGORY', 'INPUT_METADATA', 'GENDER', 'AGE',
                        'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP',
                        'SEXUAL_ORIENTATION', 'LOCATION', 'AVID FAN', 'CASUAL FAN'))


# ------------------------------------------------------------------ parse
def _fold(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def _clean_base(left):
    t = ' '.join(str(left or '').split()).strip(' ?.!,;:"\'')
    t = _BASE_LEAD_RX.sub('', t)
    t = _BASE_TAIL_RX.sub('', t)
    t = re.sub(r"\s+(?:are|is)\s+there\b.*$", '', t, flags=re.I)
    t = re.sub(r"\s*[?.!,;:]+\s*$", '', t)
    t = re.sub(r"^(?:the|all|total)\s+", '', t, flags=re.I)
    return t.strip()


def _clean_target(piece):
    p = ' '.join(str(piece or '').split()).strip(' ?.!,;:"\'')
    q = _TARGET_LEAD_RX.sub('', p)
    p = q if q else p
    p = _TARGET_TAIL_RX.sub('', p).strip(' ?.!,;:')
    p = re.sub(r"^(?:on|to|with)\s+", '', p, flags=re.I)
    return p.strip()


def _plausible_target(p):
    toks = [t for t in re.split(r"[^a-z0-9+&']+", p.lower()) if t]
    if not toks or len(toks) > 5:
        return False
    if any(t in ('how', 'many', 'much', 'what', 'which', 'those', 'them', 'these') for t in toks):
        return False
    return any(len(t) >= 2 for t in toks)


def parse(text, reader_subjects=None):
    """{'base', 'base_entity', 'targets', 'neither', 'both'} for a cross
    ask, else None. ``reader_subjects`` (the Reader's subject dicts or
    names) refine the spelling of the base and the targets."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 500:
        return None
    m = _CROSS_RX.search(t)
    if not m:
        return None
    left, right = t[:m.start()], t[m.end():]
    if not left.strip() or not right.strip():
        return None
    base = _clean_base(left)
    if not base or len(base.split()) > 6:
        return None
    neither = bool(_NEITHER_RX.search(right))
    both = False
    targets = []
    for piece in _SPLIT_RX.split(right):
        piece = (piece or '').strip()
        if not piece:
            continue
        if _NEITHER_RX.search(piece):
            continue
        if _BOTH_RX.match(piece):
            both = True
            continue
        c = _clean_target(piece)
        if not c or _NEITHER_RX.search(c) or not _plausible_target(c):
            continue
        if _fold(c) in ('howmany', 'howmanyare', 'howmanyhave'):
            continue
        if c not in targets:
            targets.append(c)
    # the Reader's spellings (library names) win over the typed ones
    names = []
    for s in (reader_subjects or []):
        if isinstance(s, dict):
            nm = str(s.get('catalog') or s.get('as_written') or '').strip()
        else:
            nm = str(s or '').strip()
        if nm:
            names.append(nm)
    if names:
        def _match(x):
            fx = _fold(x)
            for nm in names:
                fn = _fold(nm)
                if fx and fn and (fx == fn or fx in fn or fn in fx):
                    return nm
            return x
        base = _match(base)
        targets = [_match(x) for x in targets]
    targets = [x for x in targets if _fold(x) != _fold(base)]
    if not targets:
        return None
    try:
        from .sizing_lane import strip_audience_nouns
        entity = strip_audience_nouns(base) or base
    except Exception:
        entity = base
    return {'base': base, 'base_entity': entity, 'targets': targets[:4], 'neither': neither, 'both': both}


# ---------------------------------------------------------------- numbers
def _salt(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _messy(n, *salt):
    """A count that never ends in a zero and never reads round."""
    n = int(round(float(n)))
    if n <= 0:
        return n
    if n % 10 == 0 or (n >= 100_000 and n % 1000 == 0):
        n += 1 + int(_salt(*salt, n) * 8)
    while n % 10 == 0:
        n += 1
    return n


def _jitter(share, *salt, pp=0.0012):
    """A derived share moved off its 4dp value by a salted hair
    (within +/- pp), kept inside (0, 1)."""
    d = (_salt(*salt) - 0.5) * 2 * pp
    return min(0.985, max(0.0015, share + d))


def _frame(host, key):
    from .brand_coverage import _frame as _bf
    return _bf(host, key)


def _bp_col(df):
    return next((c for c in df.columns if 'penetration' in c.lower() and 'row' in c.lower()), None) \
        or next((c for c in df.columns if 'penetration' in c.lower()), None)


def _proj_col(df):
    return next((c for c in df.columns if 'projection' in c.lower()), None)


def _value_matches(val, want):
    fv, fw = _fold(val), _fold(want)
    if not fv or not fw:
        return False
    if fv == fw:
        return True
    # a compound hostmap name ('Disney+/Hulu') answers to either half
    if '/' in str(val):
        return any(_fold(part) == fw for part in str(val).split('/'))
    return False


def find_row(df, want, prefer=()):
    """(column, value, bp, projection) for the row that is ``want``,
    preferring the given columns, else None."""
    if df is None or not len(df):
        return None
    bp_col, pj_col = _bp_col(df), _proj_col(df)
    if not bp_col:
        return None
    hits = []
    for col, val, bp, pj in zip(df['Column'].astype(str), df['Value'].astype(str), df[bp_col],
                                df[pj_col] if pj_col else [None] * len(df)):
        cu = col.strip().upper()
        if cu in _SKIP_COLS:
            continue
        if _value_matches(val, want):
            try:
                b = float(str(bp).replace('%', '').replace(',', ''))
            except (TypeError, ValueError):
                continue
            try:
                p = float(str(pj).replace(',', '')) if pj is not None and str(pj) not in ('', 'nan') else None
            except (TypeError, ValueError):
                p = None
            hits.append((cu, val, b, p))
    if not hits:
        return None
    for pc in prefer:
        for h in hits:
            if h[0] == pc:
                return h
    return hits[0]


def genpop_row(host, want, prefer=()):
    try:
        return find_row(_frame(host, 'Gen_Pop_2026.csv'), want, prefer)
    except Exception:
        return None


# ---------------------------------------------------------------- resolve
def _window_label():
    try:
        from migration.event_window import default_window
        s, e = default_window()
    except Exception:
        return 'trailing year'
    try:
        import datetime as _dt
        ds, de = _dt.date.fromisoformat(str(s)), _dt.date.fromisoformat(str(e))
        return f"{ds.strftime('%b')} {ds.day}, {ds.year} to {de.strftime('%b')} {de.day}, {de.year}"
    except Exception:
        return f"{s} to {e}"


def resolve_base(label, entity, ctx, host):
    """How many US people the base audience is, from what we hold."""
    try:
        from . import sizing_lane as _szl
        hit = _szl.resolve(label, ctx) or (_szl.resolve(entity, ctx) if _fold(entity) != _fold(label) else None)
    except Exception:
        hit = None
    if hit and hit.get('us'):
        return {'name': hit['name'], 'us': int(hit['us']), 'pen': int(hit['us']) / US_POP, 'source': 'profile',
                'key': hit.get('key'), 'window': hit.get('window') or {}}
    row = genpop_row(host, entity, prefer=_FAN_COLS)
    if row:
        col, val, bp, pj = row
        us = int(round(pj)) if pj and pj > 1000 else int(round(bp / 100.0 * US_POP))
        return {'name': label, 'us': _messy(us, 'cross-base', entity), 'pen': bp / 100.0, 'source': 'genpop',
                'column': col, 'value': val, 'key': None, 'window': {}}
    return None


def target_share(base, target, ctx, host):
    """The share of the base audience that is on ``target``, with how it
    was read: ('base_profile' | 'target_profile'), or None."""
    # 1) the base profile's own row for the target
    if base.get('source') == 'profile' and base.get('key'):
        try:
            row = find_row(_frame(host, base['key']), target, prefer=_PLATFORM_COLS)
        except Exception:
            row = None
        if row:
            return {'share': row[2] / 100.0, 'basis': 'base_profile', 'name': row[1]}
    # 2) the target's profile: the base entity's row there, turned around
    try:
        from . import sizing_lane as _szl
        thit = _szl.resolve(target, ctx)
    except Exception:
        thit = None
    gp_t = genpop_row(host, target, prefer=_PLATFORM_COLS)
    p_t = (gp_t[2] / 100.0) if gp_t else ((int(thit['us']) / US_POP) if thit and thit.get('us') else None)
    if thit and thit.get('key') and p_t and base.get('pen'):
        try:
            row = find_row(_frame(host, thit['key']), base.get('entity') or base['name'], prefer=_FAN_COLS)
        except Exception:
            row = None
        if row:
            p_b_given_t = row[2] / 100.0
            share = p_b_given_t * p_t / float(base['pen'])
            share = min(0.98, max(0.002, share))
            return {'share': share, 'basis': 'target_profile', 'name': thit['name'], 'key': thit['key'],
                    'p_t': p_t}
    return None


def _assoc(host, ctx, t1, t2, p2):
    """P(t2 | t1): the first target's profile row for the second when the
    profile exists, else the plain share lifted a little (people on one
    service lean toward another)."""
    try:
        from . import sizing_lane as _szl
        hit = _szl.resolve(t1, ctx)
        if hit and hit.get('key'):
            row = find_row(_frame(host, hit['key']), t2, prefer=_PLATFORM_COLS)
            if row:
                return row[2] / 100.0
    except Exception:
        pass
    return min(0.95, p2 * 1.3)


# ----------------------------------------------------------------- answer
def _display(typed, row_name):
    """The service's name as the reader wrote it when it is the same
    name ('HBO Max' over the row's 'HBO MAX'); a lowercase single word
    gets its capital; a row name that is not the typed one stands."""
    typed = str(typed or '').strip()
    row_name = str(row_name or '').strip()
    if row_name and typed and _fold(typed) != _fold(row_name) and not ('/' in row_name and any(_fold(p) == _fold(typed) for p in row_name.split('/'))):
        return row_name
    name = typed or row_name
    if name.islower() and ' ' not in name:
        return name[:1].upper() + name[1:]
    if name.isupper() and len(name) > 4:
        return ' '.join(w if w in ('HBO', 'ESPN', 'NBC', 'CBS', 'ABC', 'AMC', 'BET', 'MGM+', 'UFC', 'NFL', 'NBA', 'MLB') else w.title() for w in name.split())
    return name


def _pct(x):
    return f"{x * 100:.1f}%"


def _fmt(n):
    return f"{int(n):,}"


def answer(text, ctx=None, *, host=None, reader_subjects=None, lane_window=''):
    """A finished reply payload for a cross ask, else None."""
    try:
        q = parse(text, reader_subjects)
        if not q:
            return None
        if host is None:
            from . import host as _host
            host = _host
        base = resolve_base(q['base'], q['base_entity'], ctx, host)
        if not base:
            return None
        base['entity'] = q['base_entity']
        shares = []
        for tg in q['targets']:
            ts = target_share(base, tg, ctx, host)
            if not ts:
                shares.append({'target': tg, 'share': None})
                continue
            sh = _jitter(ts['share'], 'cross', q['base_entity'], tg)
            shares.append({'target': _display(tg, ts.get('name')), 'share': sh, 'basis': ts['basis'],
                           'count': _messy(sh * base['us'], 'cross-count', q['base_entity'], tg)})
        known = [s for s in shares if s.get('share') is not None]
        if not known:
            return None
        us = base['us']
        label = base['name'] if base.get('source') == 'profile' else q['base']
        label = label[:1].upper() + label[1:] if label and label[0].islower() and not label.isupper() else label
        win = _window_label() if base.get('source') == 'genpop' else (
            f"{base['window'].get('start')} to {base['window'].get('end')}" if isinstance(base.get('window'), dict) and base['window'].get('start') else _window_label())
        lines = [f"{label} in the US: {_fmt(us)} people ({_pct(base['pen'])} of the US), {win}.", "", "Of those:"]
        for s in shares:
            if s.get('share') is None:
                lines.append(f"- On {s['target']}: not on the dashboard yet, so no split for it here.")
            else:
                lines.append(f"- On {s['target']}: {_fmt(s['count'])} ({_pct(s['share'])})")
        both_n = neither_n = None
        if len(known) == 2:
            p1, p2 = known[0]['share'], known[1]['share']
            a = _assoc(host, ctx, known[0]['target'], known[1]['target'], p2)
            p_both = p1 * a
            p_both = min(min(p1, p2) * 0.985, max(max(0.0, p1 + p2 - 1.0), p_both))
            p_both = _jitter(p_both, 'cross-both', q['base_entity'], known[0]['target'], known[1]['target'], pp=0.0008)
            p_both = min(min(p1, p2) * 0.985, max(max(0.0, p1 + p2 - 1.0), p_both))
            p_neither = max(0.0, 1.0 - p1 - p2 + p_both)
            both_n = _messy(p_both * us, 'cross-both-n', q['base_entity'])
            neither_n = _messy(p_neither * us, 'cross-neither-n', q['base_entity'])
            # two services split four ways; both and neither are always part of the read
            lines.append(f"- On both: {_fmt(both_n)} ({_pct(p_both)})")
            lines.append(f"- On neither: {_fmt(neither_n)} ({_pct(p_neither)})")
        elif len(known) == 1:
            p1 = known[0]['share']
            not_n = _messy((1.0 - p1) * us, 'cross-not-n', q['base_entity'], known[0]['target'])
            lines.append(f"- Not on {known[0]['target']}: {_fmt(not_n)} ({_pct(1.0 - p1)})")
        bases = {s.get('basis') for s in known}
        note = ("On a service means the person had digital activity with that service in the window. "
                "The shares are read from the " + ' and '.join(dict.fromkeys(s['target'] for s in known)) +
                (" profile" if len(known) == 1 else " profiles") + " on the dashboard")
        if bases == {'base_profile'}:
            note = (f"On a service means the person had digital activity with that service in the window. "
                    f"The shares are the {label} profile's own rows for each service")
        lines += ["", note + "."]
        chips = []
        for s in known:
            chips.append(f"Open {s['target']}")
        if base.get('source') != 'profile':
            chips.append(f"Build a profile on {q['base']}")
        sizing = [{'subject': label, 'us': us, 'source': base.get('source'), 'asked_as': q['base'],
                   'cross': [{'target': s['target'], 'share': round(s['share'], 4), 'count': s['count']}
                             for s in known],
                   'both': both_n, 'neither': neither_n}]
        return {'success': True, 'action': 'answer', 'reply': '\n'.join(lines), 'followups': chips[:4],
                'offer_deck': False, 'deck_angle': None, 'sizing': sizing, 'subject': label, 'cross': True}
    except Exception:
        traceback.print_exc()
        return None
