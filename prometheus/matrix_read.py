"""Big matrix asks run in pieces and land whole (2026-11-04, Liz).

"Genre demographics would be 12 groups (six film genres, six series
genres) by several dimensions, which is far more than the widget will
return in one piece." One pass carries a table of about a dozen cells
cleanly; a 12 by 4 ask lost rows, ran empty, or came back as prose.

The plan is deterministic and read off the question itself: the groups
the reader enumerated ("(1) Music first ... (2) Content first ...",
"six cuts: Film music first, ...", "Family animated (Inside Out 2, ...);
Superhero (...)") and the dimensions they named (age, gender, income,
education, household, region or DMA, streaming subscriptions, social
platforms ...). When groups x dimensions is more than one pass handles,
the read runs as a background job that works group by group with the
SAME base, window and definitions pinned on every piece, assembles one
result (metrics plus breakdown rows for every cell), persists once,
writes one CSV and lands one summary reply with the file link. Progress
is the job's stage line ("piece 4 of 12: Horror series"). A piece that
fails retries once; a piece that still fails is named in the reply so
the reader can ask for it alone. Never an empty or a partial-silent
answer.

Pure planning and assembly here; the read core is handed in as a
callable so the module runs hermetically in tests.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor

__all__ = ['PER_PASS_CELLS', 'plan', 'piece_question', 'run', 'assemble', 'describe']

PER_PASS_CELLS = 12        # groups x dimensions one pass returns whole
DIMS_PER_PIECE = 4         # a piece carries one group and up to this many dimensions
MAX_WORKERS = 3            # pieces in flight at once (inside the model budgets)
MAX_GROUPS = 40
MAX_PIECES = 60

_WORD_NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
             'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'twenty': 20}

# dimension vocabulary: (canonical label, pattern)
_DIMENSIONS = (
    ('Age', re.compile(r"\bage(?:s|\s+bands?|\s+groups?|\s+ranges?)?\b", re.I)),
    ('Gender', re.compile(r"\bgender\b", re.I)),
    ('Income', re.compile(r"\b(?:household\s+)?income(?:\s+bands?)?\b|\bhhi\b", re.I)),
    ('Education', re.compile(r"\beducation(?:\s+bands?|\s+level)?\b", re.I)),
    ('Household', re.compile(r"\bhousehold\s+(?:composition|type|size)\b|\bchildren\s+at\s+home\b|\bparental\s+status\b", re.I)),
    ('Region', re.compile(r"\bregion(?:s)?\b|\btop\s+(?:ten\s+|\d+\s+)?dmas?\b|\bdmas?\b|\bmarkets?\b", re.I)),
    ('Ethnicity', re.compile(r"\bethnicit(?:y|ies)\b|\brace\b", re.I)),
    ('Streaming subscriptions', re.compile(r"\bstreaming\s+(?:subscriptions?|services?)\b|\bsvod\b|\bsubscriptions?\s+by\s+service\b", re.I)),
    ('Social platforms', re.compile(r"\bsocial\s+(?:platforms?|media)(?:\s+use)?\b", re.I)),
    ('Device', re.compile(r"\bdevices?\b|\bby\s+screen\b", re.I)),
    ('Daypart', re.compile(r"\bdayparts?\b|\btime\s+of\s+day\b", re.I)),
)
# the bucket list the reader wrote for a dimension: "Age (18-24, 25-34, ...)"
_BUCKETS_RX = re.compile(
    r"\b(Age(?:\s+bands?)?|Gender|Income(?:\s+bands?)?|Education(?:\s+bands?)?|Household(?:\s+composition)?|Region|Ethnicity)\s*\(([^()]{6,400})\)", re.I)

_NUMBERED_RX = re.compile(r"\(\s*(\d{1,2})\s*\)\s*([^:;(]{2,60}?)\s*(?=:)")
_LIST_HEAD_RX = re.compile(
    r"\b(?P<n>\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:[a-z][a-z-]*\s+){0,3}(?:cuts|groups|cohorts|segments|doors|paths|audiences|buckets)\b[^:.\n]{0,60}:\s*"
    r"(?P<body>[^\n]{4,900}?)(?:\.\s|\.$|\n|$)", re.I)
_PAREN_GROUPS_RX = re.compile(r"([A-Z][A-Za-z0-9&'/+ -]{2,48}?)\s*\(([^()]{3,400})\)")
_FILM_SERIES_RX = re.compile(r"\b(?:split\s+)?film\s+(?:vs\.?|versus|and)\s+series\b|\bseries\s+(?:vs\.?|versus|and)\s+film\b", re.I)
_GROUP_STOP = frozenset(('for', 'each', 'with', 'and', 'the', 'give', 'please', 'all', 'every', 'same', 'base',
                         'window', 'definitions', 'pool', 'count', 'share', 'within', 'cut', 'new', 'read'))


_LEAD_STOP = frozenset(('for', 'please', 'give', 'then', 'and', 'or', 'with', 'each', 'if', 'also', 'tell', 'say',
                        'compare', 'split', 'using', 'same', 'pool', 'share', 'this', 'that', 'these', 'those'))
_CLAUSE_RX = re.compile(r"\b(?:who|that|which|whose|where|when|before|after|without|against)\b", re.I)


def _clean_label(s):
    s = re.sub(r"\s+", ' ', str(s or '')).strip(' ,;:.-')
    s = re.sub(r"^\(?\s*\d{1,2}\s*[).]\s*", '', s)        # "(1) Music first" -> "Music first"
    s = s.split(':', 1)[0].strip(' ,;.-')                 # "Music first: viewers who ..." -> "Music first"
    s = re.sub(r"^(?:the|a|an)\s+", '', s, flags=re.I)
    return s[:60]


def _looks_like_group(label):
    l = _clean_label(label)
    if not l or len(l) < 3 or len(l) > 60:
        return False
    toks = [w for w in re.findall(r"[a-z0-9]+", l.lower())]
    if not toks or len(toks) > 6 or all(w in _GROUP_STOP for w in toks):
        return False
    if toks[0] in _LEAD_STOP or _CLAUSE_RX.search(l):
        return False
    if re.search(r"\d{1,3}(?:,\d{3})+|\$|%", l):
        return False
    return True


def _dedupe(items):
    out, seen = [], set()
    for it in items:
        k = re.sub(r"[^a-z0-9]+", ' ', str(it).lower()).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _numbered_groups(text):
    out = []
    for m in _NUMBERED_RX.finditer(text):
        lab = _clean_label(m.group(2))
        if _looks_like_group(lab):
            out.append(lab)
    return _dedupe(out)


def _listed_groups(text):
    """'six cuts: A, B, C, D, E, F' (the count, when given, must agree)."""
    best = []
    for m in _LIST_HEAD_RX.finditer(text):
        n = m.group('n').lower()
        want = int(n) if n.isdigit() else _WORD_NUM.get(n, 0)
        body = m.group('body')
        parts = [p for p in re.split(r"\s*(?:;|,|\band\b)\s*", body) if p and p.strip()]
        labs = [_clean_label(p) for p in parts]
        labs = _dedupe([l for l in labs if _looks_like_group(l)])
        if want and len(labs) != want:
            # the body may run past the list; keep the first `want` when they read as names
            if len(labs) > want:
                labs = labs[:want]
            else:
                continue
        if len(labs) >= 2 and len(labs) > len(best):
            best = labs
    return best


def _paren_groups(text):
    """'Family animated (Inside Out 2, ...); Superhero (...)': the labels
    ahead of parenthesised member lists, two or more of them."""
    out = []
    for m in _PAREN_GROUPS_RX.finditer(text):
        lab, inner = _clean_label(m.group(1)), m.group(2).strip()
        # the label is the tail of the capture: the last few words
        # before the bracket, never a sentence ("For the Music to Long
        # Form study (61 titles ...)")
        words = lab.split()
        if len(words) > 4:
            lab = ' '.join(words[-3:])
        if not _looks_like_group(lab):
            continue
        # a member list is names with commas; a bucket list ("Age (18-24,
        # ...)") is a dimension and a count in brackets ("(61 titles)") is
        # neither
        if _BUCKETS_RX.match(f"{lab} ({inner})") or re.match(r"^\d", inner):
            continue
        if ',' not in inner or not re.search(r"[A-Za-z]{3,}", inner):
            continue
        # drop a leading connective the regex may have swallowed
        lab = re.sub(r"^(?:genres?|groups?|cuts?|film genres|series genres|and|or)\s*[:;,]?\s*", '', lab, flags=re.I).strip()
        lab = re.sub(r"^(?:[A-Z]{2,}\s+genres?:?\s*)", '', lab).strip()
        if _looks_like_group(lab):
            out.append(lab)
    out = _dedupe(out)
    return out if len(out) >= 3 else []


def _film_series_expand(groups, text):
    """'three doors, split film vs series' is six groups."""
    if not _FILM_SERIES_RX.search(text):
        return groups
    if any(re.search(r"\b(film|series|movie)s?\b", g, re.I) for g in groups):
        return groups
    out = []
    for side in ('Film', 'Series'):
        out.extend(f"{side} - {g}" for g in groups)
    return out


def groups_of(text):
    t = str(text or '')
    for fn in (_listed_groups, _numbered_groups, _paren_groups):
        g = fn(t)
        if len(g) >= 2:
            return _film_series_expand(g, t)[:MAX_GROUPS]
    return []


# "same base and window as the Age and Gender read you just delivered"
# names dimensions of an EARLIER read, not of this one
_REFERENCE_CLAUSE_RX = re.compile(
    r"\bsame\s+(?:base\s+(?:and|,)\s+)?window\s+as\s+the\s+[^.;]{0,100}?\bread\b[^.;]*|"
    r"\bas\s+(?:in\s+)?the\s+[^.;]{0,80}?\bread\b(?:\s+you\s+just\s+delivered)?", re.I)


def dimensions_of(text):
    """(dimensions, buckets): the dimensions named in the ask, in the
    house order, and the bucket list the reader wrote for each."""
    t = _REFERENCE_CLAUSE_RX.sub(' ', str(text or ''))
    dims = [name for name, rx in _DIMENSIONS if rx.search(t)]
    buckets = {}
    for m in _BUCKETS_RX.finditer(t):
        head = m.group(1).lower()
        name = next((n for n, _rx in _DIMENSIONS if n.lower().split()[0] in head), None)
        if not name:
            continue
        # a comma followed by exactly three digits is a thousands
        # separator ("$25,000 to $49,999"), never a band boundary
        parts = [p.strip(' .') for p in re.split(r",\s*(?!\d{3}(?!\d))|\s*;\s*", m.group(2)) if p.strip()]
        parts = [p for p in parts if 1 <= len(p) <= 40]
        if 2 <= len(parts) <= 40:
            buckets[name] = parts
    return dims, buckets


# a table ask names the shape it wants; a complaint, a reconciliation
# question or a which-is-right ask never runs as a matrix read
_TABLE_ASK_RX = re.compile(
    r"\b(?:for\s+(?:each|every)\s+(?:cut|group|genre|door|segment|cohort|path|audience)|every\s+bucket|full\s+(?:rows|bucket|table)|"
    r"per\s+(?:cut|group|genre)|each\s+with|demographics?|split\s+(?:into|by|film)|breakdown|table|pool\s+counts?|"
    r"share\s+within|give\s+(?:me\s+)?(?:the\s+)?(?:full|every|all)|audience\s+(?:profile|mix)|by\s+(?:age|gender|income|education))\b", re.I)
_NOT_TABLE_RX = re.compile(
    r"\b(?:reconcile|which\s+(?:is|one\s+is)\s+(?:correct|right)|contradict\w*|do\s+not\s+start\s+a\s+new\s+read|"
    r"write\s+out\s+in\s+plain\s+text|from\s+that\s+completed\s+read|looks\s+cut\s+off)\b", re.I)


def plan(text, metric_request=None):
    """The piece plan for a matrix ask, or None when one pass carries it.
    Deterministic: the same words make the same plan."""
    t = str(text or '')
    if not t.strip():
        return None
    if _NOT_TABLE_RX.search(t) or not _TABLE_ASK_RX.search(t):
        return None
    groups = groups_of(t)
    dims, buckets = dimensions_of(t)
    if not dims:
        want = str((metric_request or {}).get('dimension') or '').strip()
        if want:
            dims = [want[:40]]
    if len(groups) < 2 or not dims:
        return None
    cells = len(groups) * len(dims)
    if cells <= PER_PASS_CELLS:
        return None
    pieces = []
    for g in groups:
        for i in range(0, len(dims), DIMS_PER_PIECE):
            chunk = dims[i:i + DIMS_PER_PIECE]
            label = g if len(dims) <= DIMS_PER_PIECE else f"{g} by {', '.join(chunk)}"
            pieces.append({'label': label, 'group': g, 'dimensions': chunk})
    if len(pieces) > MAX_PIECES:
        return None
    return {'groups': groups, 'dimensions': dims, 'buckets': buckets,
            'pieces': pieces, 'cells': cells}


def describe(p):
    """'12 groups by 4 dimensions (48 cells), in 12 pieces' for the on-it
    line and the job head."""
    if not p:
        return ''
    g, d = len(p['groups']), len(p['dimensions'])
    return (f"{g} groups by {d} dimension{'s' if d != 1 else ''} ({p['cells']} cells), "
            f"in {len(p['pieces'])} piece{'s' if len(p['pieces']) != 1 else ''}")


def piece_question(text, p, piece):
    """The question one piece runs: the reader's framing kept whole, then
    the piece pinned to one group and its dimensions, same base, window
    and definitions as the full ask."""
    dims = list(piece.get('dimensions') or [])
    lines = [str(text or '').strip(), '',
             f"THIS PIECE: {piece['group']} only. The full ask covers {len(p['groups'])} groups "
             f"({', '.join(p['groups'][:12])}{', ...' if len(p['groups']) > 12 else ''}); every group runs as its own piece "
             f"on the same base, the same window and the same definitions, and the pieces are joined into one table afterwards. "
             f"Do not cover the other groups here.",
             f"Return one breakdown row per dimension for {piece['group']}: {', '.join(dims)}. "
             f"Label each row '{piece['group']}: <dimension>', give pool = the {piece['group']} count, and buckets = every band "
             f"with its count and its share within the cut (shares sum to 100 inside the row). Headline metrics: the "
             f"{piece['group']} pool and one or two figures that place it against the whole."]
    for d in dims:
        bk = (p.get('buckets') or {}).get(d)
        if bk:
            lines.append(f"{d} bands, in this order: {', '.join(bk)}.")
    return '\n'.join(lines)


def _dimension_for_row(row, piece):
    label = str(row.get('label') or '')
    for d in piece.get('dimensions') or []:
        if re.search(rf"\b{re.escape(d.split()[0])}\b", label, re.I):
            return d
    dims = piece.get('dimensions') or []
    return dims[0] if len(dims) == 1 else ''


def _slug(s):
    return re.sub(r"[^a-z0-9]+", '_', str(s or '').lower()).strip('_')[:40]


def assemble(p, results, *, subject, cohort='', text=''):
    """One result dict in the read core's shape from the per-piece
    results ({piece_label: res} for the pieces that landed). Rows for
    every cell that came back, each stamped with its group and
    dimension; shares across rows recomputed from the pools; group
    pools as the metrics; the failed pieces named on `_missing`."""
    rows, metrics, reads = [], [], []
    seen_groups = set()
    window = {}
    for piece in p['pieces']:
        res = results.get(piece['label'])
        if not isinstance(res, dict):
            continue
        if not window:
            for k in ('window_label', 'window_start', 'window_end'):
                if res.get(k):
                    window[k] = res[k]
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        prow_pool = 0
        for r in (bd.get('rows') or []):
            if not isinstance(r, dict):
                continue
            rr = dict(r)
            rr['group'] = piece['group']
            rr['dimension'] = _dimension_for_row(r, piece) or rr.get('dimension') or ''
            lab = str(rr.get('label') or '').strip()
            if piece['group'].lower() not in lab.lower():
                lab = f"{piece['group']}: {rr['dimension'] or lab}" if rr['dimension'] else f"{piece['group']}: {lab}"
            rr['label'] = lab[:60]
            if isinstance(rr.get('pool'), (int, float)) and rr['pool'] > prow_pool:
                prow_pool = int(rr['pool'])
            rows.append(rr)
        if piece['group'] not in seen_groups:
            seen_groups.add(piece['group'])
            pool = prow_pool
            if not pool:
                for m in (res.get('metrics') or []):
                    if isinstance(m, dict) and str(m.get('unit') or 'count') == 'count' and isinstance(m.get('value'), (int, float)):
                        pool = int(m['value'])
                        break
            if pool:
                metrics.append({'name': f"{_slug(piece['group'])}_pool", 'label': f"{piece['group']}, pool",
                                'value': int(pool), 'unit': 'count',
                                'definition': f"people in the {piece['group']} cut, {window.get('window_label') or 'the read window'}"[:220]})
        for rd in (res.get('reads') or [])[:1]:
            if str(rd).strip():
                reads.append(str(rd).strip())
    missing = [piece['label'] for piece in p['pieces'] if not isinstance(results.get(piece['label']), dict)]
    # row shares: each row's pool against the sum of pools (one pool per
    # group counts once), so the column still sums to 100
    pools = {}
    for r in rows:
        if isinstance(r.get('pool'), (int, float)) and r['pool'] > 0:
            pools[r['group']] = max(pools.get(r['group'], 0), int(r['pool']))
    total = float(sum(pools.values())) or 0.0
    for r in rows:
        gp = pools.get(r.get('group')) or 0
        r['share_pct'] = round(100.0 * gp / total, 4) if total and gp else float(r.get('share_pct') or 0)
    g_done = len(seen_groups)
    dims = p['dimensions']
    largest = max(pools.items(), key=lambda kv: kv[1]) if pools else None
    smallest = min(pools.items(), key=lambda kv: kv[1]) if pools and len(pools) > 1 else None
    head = f"{g_done} of {len(p['groups'])} groups by {', '.join(dims)}" if missing else f"All {len(p['groups'])} groups by {', '.join(dims)}"
    if largest:
        head += f": {largest[0]} is the largest at {largest[1]:,}"
        if smallest and smallest[0] != largest[0]:
            head += f", {smallest[0]} the smallest at {smallest[1]:,}"
    head += '.'
    res = {
        'subject': subject,
        'metric_family': 'audience',
        'window_label': window.get('window_label') or '',
        'window_start': window.get('window_start') or '',
        'window_end': window.get('window_end') or '',
        'headline': head[:300],
        'reads': reads[:7],
        'metrics': metrics[:40],
        'cohort': cohort or f"{len(p['groups'])} groups by {', '.join(dims)}",
        'breakdown': ({'dimension': f"Group by {', '.join(dims)}"[:60],
                       'share_basis': 'each row: the group pool as a share of all group pools; buckets: share within the group',
                       'rows': rows} if rows else None),
        '_matrix': {'groups': len(p['groups']), 'dimensions': len(dims), 'pieces': len(p['pieces']),
                    'landed': len(p['pieces']) - len(missing), 'failed': list(missing)},
        '_missing': list(missing),
    }
    return res


def run(p, *, core, text, stage_cb=None, max_workers=MAX_WORKERS, retries=1):
    """Run every piece through `core(piece_text, piece_label) -> res or
    None` with a bounded pool, one retry per piece, progress on
    `stage_cb`. Returns {piece_label: res} for the pieces that landed."""
    pieces = list(p['pieces'])
    n = len(pieces)
    results = {}
    done = {'n': 0}
    lock = threading.Lock()

    def _note(label, i):
        if stage_cb is None:
            return
        try:
            stage_cb(f"piece {i} of {n}: {label}"[:60])
        except Exception:
            pass

    def _one(idx, piece):
        q = piece_question(text, p, piece)
        res = None
        for attempt in range(retries + 1):
            try:
                res = core(q, piece['label'])
            except Exception:
                res = None
            if isinstance(res, dict) and (res.get('breakdown') or res.get('metrics')):
                break
            res = None
        with lock:
            done['n'] += 1
            if res is not None:
                results[piece['label']] = res
            _note(piece['label'], done['n'])
        return res

    if pieces:
        _note(pieces[0]['label'], 0)
    workers = max(1, min(int(max_workers or 1), n))
    if workers == 1:
        for i, piece in enumerate(pieces, 1):
            _one(i, piece)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pm-matrix') as ex:
            list(ex.map(lambda ip: _one(*ip), enumerate(pieces, 1)))
    return results
