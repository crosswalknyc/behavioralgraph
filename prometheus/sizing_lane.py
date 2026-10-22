"""Sizing lane (2026-10-14, Jenna, after Ben at Kartel).

"i need the size of two data sets, first FanDuel cuatomers, the second
Kalshi Cutomers" drew a $500 custom-read offer; "how big is the fanduel
audience size" drew a $500 viewership-read offer; "the sample size in
the data set of how many fanduel customers you have" drew the ten
million panel line. Every one of those is a sizing question: how many
people are in an audience, inside the sample and projected to the US.
Jenna: "this is just asking for an incidence check or what the
projected band would be for sample size."

The lane answers sizing asks deterministically. As of 2026-10-21
the answer is a $100 read, quoted first (prometheus/priced_read.py).
This module still produces the numbers after they accept:

- one or more subjects are read out of the ask (lists, ordinals, typos,
  audience nouns like customers / users / fans stripped for matching);
- a subject the dashboard already holds answers from the catalog: the
  sample inside the ten million, the projected US audience, the band
  and the window, in one line;
- a subject we do not hold runs the sample check (the same check
  the build surface offers), through the host binding;
- the ten-million panel line is reserved for asks that name no
  audience at all.

Fail-safe: any error returns None and the ask proceeds as before.
"""
from __future__ import annotations

import difflib
import re
import traceback

_SIZE_WORDS_RX = re.compile(
    r"\b(?:sizes?|sizing|how (?:big|large|many)|incidence|sample[- ]size|n[- ]size|audience[- ]size|"
    r"data ?set[- ]size|universe[- ]size|projected band|headcount|head count|reach)\b", re.I)
_CONSUME_RX = re.compile(r"\b(?:watch(?:ed|es|ing)?|view(?:ed|s|ing)?|read(?:s|ing)?|listen(?:ed|s|ing)?|stream(?:ed|s|ing)?|"
                         r"play(?:ed|s|ing)?|bought|buy(?:s|ing)?|purchas(?:e|ed|es|ing)|download(?:ed|s)?|binge[ds]?|tuned)\b", re.I)
# "how many people <did a thing>" is an event count (an action in a window),
# never an audience size (2026-10-21: 'how many people saw a Babylon 5 post
# in June' was sized as an audience named 'saw a Babylon 5 post in June').
# The action verbs beyond consumption: saw, clicked, searched, visited,
# posted, engaged, attended, signed up, subscribed, converted, ...
_EVENT_RX = re.compile(
    r"\bhow many (?:people|users|customers|individuals|folks|consumers|viewers|fans|accounts)\s+"
    r"(?:saw|see|seen|clicked|click(?:s|ed)?|searched|search(?:es|ed)?|visit(?:ed|s)?|post(?:ed|s)?|"
    r"engag(?:ed|es)|attend(?:ed|s)?|sign(?:ed)? up|subscrib(?:ed|es)?|convert(?:ed|s)?|open(?:ed|s)?|"
    r"shar(?:ed|es)?|lik(?:ed|es)?|follow(?:ed|s)?|comment(?:ed|s)?|went|came|left|return(?:ed|s)?|"
    r"ordered|rented|tried|used|installed|redeemed|voted|donated|registered|enrolled|churned|cancel(?:l)?ed)\b", re.I)
_INSTRUCTION_RX = re.compile(r"\b(?:use|set|with|lock|make)\b[^.?!]{0,40}\bsample[- ]?size\b|\bsample[- ]?size\b[^.?!]{0,20}(?:of|should be|=|:)?\s*[\d,]{4,}", re.I)
_NOT_SIZING_RX = re.compile(r"\b(?:deck|slides?|email me|send me the file|download|csv|journey|valuation|partnership|attribution)\b", re.I)

_LEAD_RX = re.compile(
    r"^\W*(?:no[,.]?\s+|ok[,.]?\s+|so[,.]?\s+)?"
    r"(?:(?:i|we) (?:just )?(?:need|want|would like|'d like|am looking|are looking)(?: to (?:know|see|get|understand))?\s+|"
    r"(?:can|could) (?:you|i|we) (?:get|tell me|see|show me|give me)\s+|what(?:'s| is| are| was)\s+|tell me\s+|give me\s+|show me\s+|"
    r"how (?:big|large) (?:is|are|was|were)\s+|how many (?:people|users|customers|individuals|folks|panelists|consumers) (?:are |is )?(?:in|inside|within)\s+|"
    r"how many (?:people|users|customers|individuals|folks|panelists|consumers|respondents) (?:would|will|do|did|could|can) (?:we|you|i) (?:have|get|see|find|expect)\s+(?:for|on|in|with|of)\s+|"
    r"do (?:we|you) (?:have|know)\s+)?"
    r"(?:the |an? )?(?:total |projected |estimated |approximate |overall )?"
    r"(?:sizes?|sizing|sample[- ]sizes?|audience[- ]sizes?|incidence|n|headcount|reach|projected band|band)?\s*"
    r"(?:of|for|on)?\s*(?:the |these |those |both |our |your )?(?:two|three|four|2|3|4)?\s*"
    r"(?:data ?sets?|audiences?|profiles?|universes?|groups?|cohorts?|pulls?|segments?)?\s*[:,-]?\s*", re.I)
_SPLIT_RX = re.compile(r"\s*(?:,|;|\band\b|\bvs\.?\b|\bversus\b|\bthen\b|\bplus\b|&)\s*", re.I)
_ORDINAL_RX = re.compile(r"^(?:the\s+)?(?:first|second|third|fourth|1st|2nd|3rd|4th|one|two|three|\d\)|\d\.)\s*[:,-]?\s*(?:one |is |being )?", re.I)
_TAIL_RX = re.compile(
    r"(?<=\w)\s+(?:(?:you|we) (?:have|hold|carry)\b.*|to (?:decide|see|figure|know|understand)\b.*|so (?:i|we) can\b.*|before (?:i|we)\b.*|"
    r"in (?:the |your |our )?(?:data ?set|sample|panel|system|library|dashboard)\b.*|first|please|thanks?)$", re.I)
_NOUN_TAIL_RX = re.compile(
    r"\s+(?:audience[- ]size|data ?set|dataset|audience|universe|profile|sample|size|cohort|group|segment|numbers?|figures?|count)s?$", re.I)
_DATE_TAIL_RX = re.compile(
    r"\s+(?:from|since|between|during|over|in|for)\s+(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d|\d{4}\b|"
    r"the (?:last|past|trailing|previous|next)\b|(?:a |the )?(?:last |past |trailing |next )?\d+\s*(?:days?|weeks?|months?|years?)\b|"
    r"q[1-4]\b|ytd\b|today\b|yesterday\b).*$", re.I)
_DEIXIS_RX = re.compile(r"^(?:this|that|it|the open (?:one|profile|page)|this (?:one|profile|page|audience)|the one open)$", re.I)
_STOP = frozenset(('the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by', 'with', 'how', 'many', 'size', 'sizes'))

# Audience nouns a reader attaches to a brand ("FanDuel customers");
# matched with typo tolerance so "cuatomers" and "cutomers" strip too.
AUDIENCE_NOUNS = ('customers', 'customer', 'users', 'user', 'fans', 'fan', 'audience', 'viewers', 'viewer', 'subscribers',
                  'subscriber', 'members', 'member', 'buyers', 'buyer', 'shoppers', 'shopper', 'players', 'player',
                  'listeners', 'listener', 'readers', 'reader', 'consumers', 'consumer', 'holders', 'owners', 'owner',
                  'bettors', 'bettor', 'traders', 'trader', 'visitors', 'visitor', 'clients', 'client', 'accounts',
                  'people', 'individuals', 'households', 'engagers', 'panelists', 'panelist', 'respondents', 'respondent')


def _is_noun(tok):
    t = str(tok or '').lower()
    if t in AUDIENCE_NOUNS:
        return True
    return bool(difflib.get_close_matches(t, AUDIENCE_NOUNS, n=1, cutoff=0.8)) if len(t) >= 5 else False


_FILE_WORDS = frozenset(('one', 'profile', 'profiles', 'file', 'files', 'data', 'dataset', 'cut', 'read', 'pull', 'audience'))


def strip_audience_nouns(phrase):
    """'FanDuel cuatomers' -> 'FanDuel'; 'Kalshi Cutomers' -> 'Kalshi';
    'the noah wyle one' -> 'noah wyle' (a reader pointing at a file by
    name, 2026-10-14)."""
    toks = str(phrase or '').split()
    while toks and (_is_noun(toks[-1]) or toks[-1].lower().strip(',.') in _FILE_WORDS) and len(toks) > 1:
        toks.pop()
    while toks and (_is_noun(toks[0]) or toks[0].lower() in ('the', 'that', 'this', 'your', 'our', 'my')) and len(toks) > 1:
        toks.pop(0)
    return ' '.join(toks).strip(' ,.-')


def wants_sizing(text):
    """A question about how many people an audience holds."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400 or t.endswith(':'):
        return False
    if not _SIZE_WORDS_RX.search(t) or _INSTRUCTION_RX.search(t) or _NOT_SIZING_RX.search(t):
        return False
    if _CONSUME_RX.search(t) and not re.search(r"\b(?:sample|incidence|data ?set)\b", t, re.I):
        return False   # "how many people watched X" is a viewership read
    if _EVENT_RX.search(t):
        return False   # "how many people saw / clicked / searched X" is an event count, not an audience size
    return True


def _clean(phrase):
    p = ' '.join(str(phrase or '').split()).strip(' ?.!,;:"\'')
    p = _ORDINAL_RX.sub('', p)
    p = re.sub(r"^(?:in|inside|within)\s+(?:the |our |your )?(?:data ?set|sample|panel|library|dashboard|system)\s+(?:of|for|on)?\s*", '', p, flags=re.I)
    p = re.sub(r"^(?:how many|the number of|number of)\s+", '', p, flags=re.I)
    p = _TAIL_RX.sub('', p).strip(' ?.!,;:')
    p = _DATE_TAIL_RX.sub('', p).strip(' ?.!,;:')
    p = re.sub(r"\s+(?:do|does|did|is|are|was|were|have|has|had)$", '', p, flags=re.I)
    p = re.sub(r"^(?:the|a|an|of|for|on)\s+", '', p, flags=re.I)
    p = re.sub(r"^(?:how many|the number of|number of)\s+", '', p, flags=re.I)
    p = re.sub(r"^(?:the|a|an|of|for|on)\s+", '', p, flags=re.I)
    for _ in range(3):
        q = _NOUN_TAIL_RX.sub('', p).strip(' ?.!,;:')
        q = re.sub(r"^(?:size|sizes|sizing|sample size|audience size|incidence)\s+(?:of|for|on)\s+", '', q, flags=re.I)
        if q == p:
            break
        p = q
    return p.strip(' ?.!,;:"\'')


_PANEL_NOUN_RX = re.compile(r"^(?:the |our |your |all |total )*(?:panelists?|respondents?|people|users|individuals|members|consumers|folks|sample|panel|data|data ?set|audience)(?: in (?:the |your |our )?(?:sample|panel|data ?set|data))?$", re.I)


def is_panel_noun(phrase):
    """'panelists', 'people in the panel', 'the sample': the panel itself,
    not an audience inside it."""
    low = ' '.join(str(phrase or '').lower().split())
    return _is_panel_self(low) or bool(_PANEL_NOUN_RX.match(low))


def _is_panel_self(phrase):
    low = ' '.join(str(phrase or '').lower().replace('teh ', 'the ').split())
    if low in PANEL_SELF:
        return True
    toks = re.split(r"[^a-z0-9]+", low)
    if any(difflib.SequenceMatcher(None, t, 'crosswalk').ratio() >= 0.8 for t in toks if len(t) >= 6):
        return True
    return bool(re.match(r"^(?:the |our |your |whole |entire |full |total )*(?:sample|panel|gen pop|general population)\b", low))


def _plausible(phrase):
    toks = [t for t in re.split(r"[^a-z0-9+&']+", str(phrase or '').lower()) if t]
    if not toks or len(toks) > 8:
        return False
    if re.match(r"^\d", toks[0]) or not any(re.search(r"[a-z]{3,}", t) for t in toks):
        return False   # a date or a bare number is not an audience
    if any(t in ('would', 'will', 'could', 'should', 'does', 'did', 'do', 'can') for t in toks):
        return False   # a verb phrase, not a name
    content = [t for t in toks if t not in _STOP and not _is_noun(t)]
    if not content:
        return False
    return len(content) >= 2 or len(content[0]) >= 3


# phrases that mean the panel itself, never an audience to size
PANEL_SELF = frozenset(('crosswalk', 'crosswalk sample', 'crosswalk panel', 'the crosswalk sample', 'sample', 'panel',
                        'our sample', 'your sample', 'the sample', 'the panel', 'gen pop', 'general population',
                        'us gen pop', 'us general population', 'us', 'usa', 'data', 'data set', 'dataset', 'audience'))
_IS_THE_BRAND_RX = re.compile(r"^\W*(?P<s>(?!(?:how|what|which|where|who|is|are|the|size|tell|give)\b)[\w'&.+-]+(?: (?!is the\b)[\w'&.+-]+){0,4}?)\s+is the (?:brand|subject|title|company|one)\b", re.I)


def names_an_audience(text, ctx=None):
    """True when the ask names an audience to size (so it is not a
    question about the ten million panel itself)."""
    return any(not _is_panel_self(s) for s in subjects(text, ctx))


def subjects(text, ctx=None):
    """The audience phrases the ask names, in order; the open page when
    the ask points at it ('this audience') or names nothing."""
    t = ' '.join(str(text or '').split()).strip()
    m = _IS_THE_BRAND_RX.match(t)
    if m:
        return [m.group('s').strip()]
    body = _LEAD_RX.sub('', t, count=1)
    if body == t:
        body = re.sub(r"^.*?\b(?:size|sizes|sizing|incidence|how (?:big|large) (?:is|are)|how many)\b\s*(?:of|for|on|is|are)?\s*(?:the )?", '', t, count=1, flags=re.I)
    parts = [p for p in _SPLIT_RX.split(body) if p and p.strip()]
    out = []
    for p in parts:
        c = _clean(p)
        if not c:
            continue
        if _DEIXIS_RX.match(c):
            out.append('__page__')
            continue
        if _plausible(c) and not _is_panel_self(c):
            out.append(c)
    page = ''
    try:
        page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        page = ''
    res = []
    for s in out:
        if s == '__page__':
            if page and page not in res:
                res.append(page)
        elif s not in res:
            res.append(s)
    if not res and page and re.search(r"\b(?:this|that|it|here|the open)\b", t, re.I):
        res.append(page)
    return res[:4]


# ------------------------------------------------------------ resolve
def _catalog():
    try:
        from migration import corpus_catalog as cc
    except Exception:
        try:
            import corpus_catalog as cc  # type: ignore
        except Exception:
            return None
    return cc


def _anchor(cc, name):
    try:
        a = cc.anchors_for(name, with_ledger=False)
    except Exception:
        return None, None
    if not a.get('facts'):
        return None, a
    pa = cc.profile_anchor(a)
    if not pa or not pa.get('sample_size'):
        return None, a
    return {'name': a.get('subject') or name, 'sample': int(pa['sample_size']),
            'us': int(pa.get('audience_size') or round(int(pa['sample_size']) * 32.99)),
            'window': pa.get('window') or {}, 'key': pa.get('source_key')}, a


def fuzzy_catalog_name(cc, phrase, cutoff=0.86):
    """The catalog subject whose folded key is a near spelling of the
    phrase (typo tolerant), or ''."""
    try:
        fold = cc.entity_fold(phrase)
        if not fold:
            return ''
        idx = cc.load_index() or {}
        keys = list((idx.get('subjects') or {}).keys())
        spaced = {k.replace('_', ' '): k for k in keys}
        hit = difflib.get_close_matches(fold, list(spaced.keys()), n=1, cutoff=cutoff)
        if hit:
            k = spaced[hit[0]]
            ent = (idx.get('subjects') or {}).get(k) or {}
            als = ent.get('aliases') or []
            return cc.best_display(als, k.replace('_', ' ')) if hasattr(cc, 'best_display') else (als[0] if als else k)
    except Exception:
        traceback.print_exc()
    return ''


def resolve(phrase, ctx=None):
    """{'name','sample','us','window','key','matched'} for a phrase the
    dashboard holds a profile on, else None."""
    cc = _catalog()
    if cc is None:
        return None
    tried = []
    cands = [phrase]
    bare = strip_audience_nouns(phrase)
    if bare and bare.lower() != str(phrase).lower():
        cands.append(bare)
    page = ''
    try:
        page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        page = ''
    for c in cands:
        if not c or c in tried:
            continue
        tried.append(c)
        hit, _a = _anchor(cc, c)
        if hit:
            hit['matched'] = c
            return hit
    for c in list(cands):
        fz = fuzzy_catalog_name(cc, c)
        if fz and fz not in tried:
            tried.append(fz)
            hit, _a = _anchor(cc, fz)
            if hit:
                hit['matched'] = fz
                return hit
    if page:
        pf, bf = cc.entity_fold(page), cc.entity_fold(bare or phrase)
        if pf and bf and (pf == bf or difflib.SequenceMatcher(None, pf, bf).ratio() >= 0.86 or bf in pf.split()):
            hit, _a = _anchor(cc, page)
            if hit:
                hit['matched'] = page
                return hit
    return None


# -------------------------------------------------------------- answer
def _fmt(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _window_text(w):
    if not isinstance(w, dict) or not w.get('start'):
        return ''
    return f"{w.get('start')} to {w.get('end')}"


def _band(host, sample):
    try:
        if host is not None and host.has('audience_range'):
            r = host.audience_range(sample)
            if r and r.get('low') and r.get('high'):
                return int(r['low']), int(r['high'])
    except Exception:
        pass
    return None


def profile_line(hit, host=None):
    """One plain line for a held profile: sample, incidence, US people,
    band, window."""
    # I5 (2026-10-20): US people in plain words; the sample count, the
    # incidence and the ten million stay inside (Tania Nasr asked what
    # the 10 million was). The sizing block on the payload keeps the
    # sample for the operator surfaces.
    sample = int(hit['sample'])
    line = f"{hit['name']}: {_fmt(hit['us'])} people in the US"
    band = _band(host, sample)
    if band:
        line += f"; audience range {_fmt(band[0])} to {_fmt(band[1])}"
    w = _window_text(hit.get('window'))
    if w:
        try:
            from . import guards as _g
            w = _g.plain_window(w) or w
        except Exception:
            pass
        line += f" ({w})"
    return line + ". That is the profile on the dashboard."


def _check_line(host, phrase, history, window_txt=''):
    """The free sample check for a subject we do not hold, as text; ''
    when the host cannot run it."""
    try:
        if host is None or not host.has('incidence_check'):
            return ''
        resp = host.incidence_check(f"sample check for {phrase} {window_txt}".strip(), history or [])
        data = None
        if isinstance(resp, tuple):
            resp = resp[0]
        if hasattr(resp, 'get_json'):
            data = resp.get_json(silent=True)
        elif isinstance(resp, dict):
            data = resp
        ic = (data or {}).get('incidence_check') or {}
        msg = str(ic.get('message') or '').strip()
        if not msg:
            return ''
        msg = re.sub(r"\n\nReply 'run it'.*$", '', msg, flags=re.S).strip()
        # the first line names the audience the reader asked about
        m = re.match(r"^Sample check for (?P<who>.+?) \(", msg)
        who = (m.group('who') if m else '').strip()
        want = strip_audience_nouns(phrase) or phrase
        if m and (who.lower() == 'this audience' or not (set(re.findall(r"[a-z0-9]+", who.lower())) & set(re.findall(r"[a-z0-9]+", want.lower())))):
            msg = msg.replace(f"Sample check for {who} (", f"Sample check for {phrase} (", 1)
        return msg
    except Exception:
        traceback.print_exc()
        return ''


_VERDICT_LINE_RX = re.compile(r"\n*Verdict:[^\n]*", re.I)


def reader_safe(msg, staff=True):
    """The sample check as a reader sees it. The verdict line is an
    operator's build-decision note and a confidence tier (never public,
    2026-09-14); it leaves for anyone outside Crosswalk (2026-10-20,
    Tania Nasr at Paramount+ read 'Verdict: Strong - deep sample')."""
    if staff:
        return msg
    return _VERDICT_LINE_RX.sub('', str(msg or '')).strip()


def answer(text, ctx=None, *, host=None, history=None, subjects=None, staff=True, reader_subjects=None):
    """A finished reply payload for a sizing ask, else None. `subjects`
    (from the Reader) overrides the phrase extraction. `staff` is False
    for a reader outside Crosswalk (the operator's verdict line leaves).
    A cross ask (a base audience split across services: 'of those, how
    many are on Paramount+, on HBO Max, neither') is answered as the
    cross, never as independent sizes (2026-10-20, Tania Nasr). A
    subscribe / pay / incremental overlap of a held audience is not
    this lane: that is a Custom Ask (2026-10-21, Bria)."""
    try:
        try:
            from . import overlap_read as _ov
            if _ov.is_overlap_read(text):
                return None
        except Exception:
            traceback.print_exc()
        try:
            from . import cross_sizing as _cx
            _cross = _cx.answer(text, ctx, host=host, reader_subjects=reader_subjects or subjects)
        except Exception:
            traceback.print_exc()
            _cross = None
        if _cross:
            return _cross
        if subjects:
            subs = [str(x).strip() for x in subjects if str(x or '').strip() and not _is_panel_self(str(x))]
        else:
            if not wants_sizing(text):
                return None
            subs = [x for x in globals()['subjects'](text, ctx) if not _is_panel_self(x)]
        if not subs:
            return None   # a question about the ten million panel itself is the product fact's
        lines, chips, sizing = [], [], []
        # a window of its own ("over 90 days", "in 2025") is the sample
        # check's: the catalog line is the profile's trailing year
        wm = _DATE_TAIL_RX.search(' ' + ' '.join(str(text or '').split()))
        window_txt = wm.group(0).strip() if wm else ''
        for phrase in subs:
            hit = None if window_txt else resolve(phrase, ctx)
            if hit:
                lines.append(profile_line(hit, host))
                chips.append(f"Open {hit['name']}")
                sizing.append({'subject': hit['name'], 'sample': hit['sample'], 'us': hit['us'],
                               'window': hit.get('window'), 'source': 'profile', 'asked_as': phrase})
                continue
            msg = reader_safe(_check_line(host, phrase, history, window_txt), staff)
            if msg:
                lines.append(msg)
                chips.append(f"Build a profile on {strip_audience_nouns(phrase) or phrase}")
                sizing.append({'subject': phrase, 'source': 'sample_check'})
            else:
                lines.append(f"{phrase}: not on the dashboard yet. Say 'build a profile on {phrase}' and I will size it first, then draft the build for your approval.")
                chips.append(f"Build a profile on {phrase}")
                sizing.append({'subject': phrase, 'source': 'none'})
        if not lines:
            return None
        reply = '\n\n'.join(lines)
        if len(subs) > 1:
            reply += ("\n\nBoth figures count people with at least one digital touchpoint with the brand in the window, projected to the US.") if len(subs) == 2 else \
                     "\n\nEach figure counts people with at least one digital touchpoint with the brand in the window, projected to the US."
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips[:4],
                'offer_deck': False, 'deck_angle': None, 'sizing': sizing,
                'subject': sizing[0].get('subject') if sizing else ''}
    except Exception:
        traceback.print_exc()
        return None
