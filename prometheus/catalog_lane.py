"""Deterministic catalog lane (2026-10-06, Jenna: fix the speed gap).

"do we have a profile for Ms. Rachel?" took 77 seconds and drafted a
build; "Do you see the SWAT Exiles Season 1 Subscriber IQ?" took 84.
Those are lookups. The corpus catalog already knows every subject the
dashboard has published on, so existence, "do you see", "what do we
have on", and audience-size asks are answered here from the index with
no model call. Anything the lane cannot answer with certainty falls
through to the normal routing untouched.

Fail-safe: any error returns None and the ask proceeds as before.
"""
from __future__ import annotations

import re

_PRODUCT_WORDS = (
    r"(?:a |an |the |any |that |this )?(?:profile iq|profile|digital journey iq|journey iq|"
    r"journey|subscriber iq|sub iq|subiq|attribution iq|attribution|brand partnership iq|"
    r"partnership read|partnership|deck|one[- ]pager|read|data|anything|something|"
    r"audience|cut|file|report|analysis)s?"
)
_EXISTS_RX = re.compile(
    r"^\s*(?:hey |hi |prometheus,? )?(?:do (?:we|you) (?:already )?(?:have|see|hold|carry|know)|"
    r"is there|are there|have we (?:got|built|run|pulled|done)|did we (?:build|run|pull)|"
    r"what do (?:we|you) have (?:on|for|about)|do you have anything (?:on|for|about)|"
    r"show me what (?:we|you) have (?:on|for|about)|can you (?:find|see|pull up))\s+"
    r"(?P<rest>.+?)[\s?.!]*$", re.I)
_SIZE_RX = re.compile(
    r"^\s*(?:how (?:big|large) is (?:the )?(?P<s1>.+?)(?: audience| universe)?(?: size)?|"
    r"(?:what(?:'s| is) the )?(?:audience )?size (?:of|for) (?:the )?(?P<s2>.+?)(?: audience| universe)?|"
    r"how many (?:people|viewers|users|individuals) (?:watch|watched|stream|streamed|use|used|engage with|engaged with) (?P<s3>.+?)|"
    # the sample size of a held profile (2026-10-07, Scott: "I was asking
    # to know what the sample size within our 10million is for Will and
    # Grace on Hulu" drafted a build)
    r"(?:i was asking to know |i want to know |i need to know |tell me |can you tell me |just )?"
    r"what(?:'s| is)? (?:the )?(?:sample size|sample) (?:within|in|inside|of) (?:our |the )?"
    r"(?:10 ?(?:million|m)|sample|panel)(?: is)? (?:for|of|on) (?:the )?(?P<s4>.+?)|"
    r"(?:what(?:'s| is) (?:the )?)?sample size (?:for|of|on) (?:the )?(?P<s5>.+?)(?: audience| universe| profile)?)"
    r"[\s?.!]*$", re.I)
_STRIP_RX = re.compile(
    r"^(?:" + _PRODUCT_WORDS + r")\s+(?:for|on|about|of|covering)\s+", re.I)
_TAIL_RX = re.compile(
    r"\s+(?:" + _PRODUCT_WORDS + r")(?:\s+(?:yet|already|built|done|run|in the (?:library|dashboard|system)))?$", re.I)
_YET_RX = re.compile(r"\s+(?:yet|already|built|in the (?:library|dashboard|system|corpus))$", re.I)
_PRODUCT_LABEL = {
    'profile': 'Profile IQ', 'journey': 'Digital Journey IQ', 'attribution': 'Attribution IQ',
    'bpiq': 'Brand Partnership IQ', 'chat': 'an earlier answer', 'deck': 'a deck',
    'subiq': 'Subscriber IQ', 'trends': 'Trends IQ',
}


_GENERIC = frozenset((
    'budget', 'time', 'money', 'bandwidth', 'capacity', 'access', 'permission',
    'permissions', 'credits', 'credit', 'numbers', 'results', 'answer', 'answers',
    'option', 'options', 'ability', 'way', 'chance', 'plan', 'plans', 'idea', 'ideas',
    'it', 'this', 'that', 'them', 'these', 'those', 'anything', 'something', 'everything',
    'enough', 'more', 'yet', 'today', 'tomorrow', 'now', 'here', 'there', 'you', 'me', 'us',
))
_STOP = frozenset(('the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by', 'with'))


def _looks_like_entity(subj):
    toks = [t for t in re.split(r'[^a-z0-9+&]+', subj.lower()) if t]
    if not toks or any(t in _GENERIC for t in toks):
        return False
    content = [t for t in toks if t not in _STOP]
    return bool(content) and (len(content) >= 2 or len(content[0]) >= 4)


_PULL_LEAD_RX = re.compile(r"^(?:(?:a |an |any |the )?(?:data )?pulls?|data|anything|something|numbers|figures)\s+(?:on|for|about|of|covering)\s+", re.I)


def _clean_subject(rest):
    s = str(rest or '').strip().strip('"\'')
    s = _PULL_LEAD_RX.sub('', s)   # "a data pull on fanduel customers" -> "fanduel customers" (2026-10-14)
    s = _STRIP_RX.sub('', s)
    s = _YET_RX.sub('', s)
    s = _TAIL_RX.sub('', s)
    s = _YET_RX.sub('', s)
    s = re.sub(r"\s+" + _ROSTER_SCOPE + r"$", '', s, flags=re.I)   # "Nike in your database" -> "Nike" (2026-10-15)
    s = re.sub(r"^(?:a |an |the )", '', s, flags=re.I).strip(' ?.!,')
    return s


# Roster asks (2026-10-15, Jenna, after Emmet at Kartel asked for "a CSV
# of all the influencers or talent in your network" and got "I do not
# have a recent data read to export yet"): there is no fixed network.
# Prometheus reads any influencer, brand or title the user names, so
# the answer is the offer and the question back: "I can pull any
# influencer you would like. Who is it?" Runs before the existence
# check, which would otherwise look up "influencers in your network"
# as a subject.
_CLASS = (r"(?:influencers?|content creators?|creators?|talents?|celebrities|celebs?|athletes?|"
          r"musicians?|artists?|podcasters?|actors?|actresses|hosts?|personalities|streamers?|"
          r"youtubers?|tiktokers?|comedians?|authors?|brands?|companies|advertisers?|retailers?|"
          r"titles?|shows?|series|movies?|films?|podcasts?|games?|teams?|leagues?|people|names|"
          r"subjects?|profiles?|audiences?|entities)")
_ROSTER_SCOPE = (r"(?:in|on|across|within|from|inside) (?:your|the|our) (?:network|roster|library|"
                 r"system|database|catalog(?:ue)?|platform|panel|data|dashboard|list|universe|"
                 r"coverage|tool|corpus|book|portfolio)")
_ROSTER_DB = (r"(?:your|the|our) (?:talent|influencer|creator|brand|content|artist) "
              r"(?:database|library|network|roster|list|pool|catalog(?:ue)?|system|db)")
# a qualifier after the class or the scope: "who sell their own product",
# "currently selling a course", "that also offer a service"
_ROSTER_QUAL = (r"(?P<{name}>\s+(?:who|that|which|and|also|currently|now|actively|are|is|have|has|with|"
                r"sell\w*|offer\w*|run\w*|own\w*|launch\w*|promot\w*|monetiz\w*|post\w*|do\w*|make\w*|"
                r"work\w*|partner\w*|have launched|had)\b.+?)?")
_ROSTER_TAIL = r"(?:\s+(?:right now|currently|today|so far|to date|yet|please))?[\s?.!]*$"
_ROSTER_RX = re.compile(
    # "all the influencers in your network (who sell their own product)"
    r"\b(?:all(?: of)?|every|list(?:ing)?|csv|spreadsheet|roster|directory|rundown|how many|which|what|"
    r"do (?:you|we) (?:have|hold|carry|cover|track)|have you got|is there|are there|any)\b"
    r".{0,40}?\b(?P<cls>" + _CLASS + r")\b(?:\s*(?:,|/|\bor\b|\band\b|&)\s*(?:the |your )?(?P<cls2>" + _CLASS + r")\b)?"
    r".{0,24}?\b" + _ROSTER_SCOPE + r"\b" + _ROSTER_QUAL.format(name='q1') + _ROSTER_TAIL
    # "influencers you have (that sell a course)"
    + r"|\b(?P<cls3>" + _CLASS + r")\b(?:\s*(?:,|/|\bor\b|\band\b|&)\s*(?:the |your )?(?P<cls4>" + _CLASS + r")\b)?"
    r"\s+(?:do |that |who |which )?(?:you|we) (?:have|cover|track|carry|work with|see|know|offer|support|represent|hold)"
    r"(?: (?:data|profiles|reads|info|information|numbers))?(?: (?:on|for))?" + _ROSTER_QUAL.format(name='q2') + _ROSTER_TAIL
    # "poll your talent database and see which of these influencers also offer a product"
    + r"|\b(?:poll|check|search|scan|query|look (?:through|at|in|into)|go through|comb|screen|see)\b.{0,30}?\b(?P<db>" + _ROSTER_DB + r")\b(?P<rest3>.*)$"
    # "which of these influencers sell a product" with no names anywhere
    + r"|\b(?:which|how many|what)(?: ones?)? of (?:these|those) (?P<cls6>" + _CLASS + r")\b" + _ROSTER_QUAL.format(name='q4') + _ROSTER_TAIL
    # "who are influencers or talent that have a product they are selling" (Emmet, 2026-10-15: "I want to
    # see in the dataset who are influencers or talent that have a product or service they are selling")
    + r"|\b(?:who are|which are|what are|which|what|show me|list|find|identify|see|tell me)\b(?: me)?(?: (?:in|from|across) (?:the|your|our) (?:dataset|data|database|network|library|universe|panel))?"
    r"\s+(?:the |all (?:the |of the )?|any |every )?(?P<cls7>" + _CLASS + r")\b(?:\s*(?:,|/|\bor\b|\band\b|&)\s*(?:the |your )?(?P<cls8>" + _CLASS + r")\b)?"
    r"\s+(?P<q5>(?:(?:who|that|which) )?(?:have|has|sell\w*|offer\w*|run\w*|own\w*|launch\w*|promot\w*|monetiz\w*|are (?:selling|offering|running|launching)|(?:who|that|which) (?:are|is|do|make))\b.+?)[\s?.!]*$",
    re.I)
_ROSTER_PERSON = {
    'influencer': 'influencer', 'creator': 'creator', 'content creator': 'creator', 'talent': 'talent',
    'celebrity': 'celebrity', 'celeb': 'celebrity', 'athlete': 'athlete', 'musician': 'musician',
    'artist': 'artist', 'podcaster': 'podcaster', 'actor': 'actor', 'actress': 'actor', 'host': 'host',
    'personality': 'personality', 'streamer': 'streamer', 'youtuber': 'YouTuber', 'tiktoker': 'TikToker',
    'comedian': 'comedian', 'author': 'author',
}
_ROSTER_THING = {
    'brand': 'brand', 'company': 'brand', 'advertiser': 'brand', 'retailer': 'retailer', 'title': 'title',
    'show': 'show', 'series': 'series', 'movie': 'movie', 'film': 'film', 'podcast': 'podcast',
    'game': 'game', 'team': 'team', 'league': 'league',
}
_PLURAL = {'talent': 'talent', 'celebrity': 'celebrities', 'personality': 'personalities', 'series': 'series',
           'audience': 'audiences', 'company': 'companies'}
_QUAL_CUT_RX = re.compile(r"\s*(?:,|\bor\b|\band\b)?\s*(?:or )?(?:something|anything|stuff|whatever|things?) like\b.*$|\s*,?\s*(?:etc|and so on|or whatever|or anything)\b.*$", re.I)
_DB_REST_RX = re.compile(r"\b(?:which|how many|what)(?: ones?)? of (?:these|those|the|your|our) (?P<cls>" + _CLASS + r")\b(?P<qual>.*?)[\s?.!]*$", re.I)
_QUAL_LEAD_RX = re.compile(r"^(?:who|that|which|and|also|are|is|do|does|currently|now|actively|the ones? (?:who|that))\s+", re.I)


def _class_noun(word):
    w = str(word or '').lower().strip()
    for form in (w, w[:-1] if w.endswith('s') else w, w[:-3] + 'y' if w.endswith('ies') else w,
                 w[:-2] if w.endswith('es') else w):
        if form in _ROSTER_PERSON:
            return _ROSTER_PERSON[form], True
        if form in _ROSTER_THING:
            return _ROSTER_THING[form], False
    return 'audience', False


def _plural(noun):
    return ' or '.join(_PLURAL.get(n, n + 's') for n in str(noun).split(' or '))


def _clean_qual(q):
    """"are currently selling their own product or course or something like
    selling something" -> "currently selling their own product or course"."""
    q = ' '.join(str(q or '').split()).strip(' ?.!,;:')
    q = _QUAL_CUT_RX.sub('', q).strip(' ?.!,;:')
    for _ in range(3):
        q2 = _QUAL_LEAD_RX.sub('', q)
        if q2 == q:
            break
        q = q2
    q = re.sub(r"\s+(?:right now|today|so far|to date|yet|please)$", '', q, flags=re.I).strip(' ?.!,;:')
    if len(q) > 90:
        q = q[:90].rsplit(' ', 1)[0].strip(' ,;:')
    return q if len(q.split()) >= 2 else ''


def roster_ask(text):
    """(noun, is_person, qualifier) when the ask is for the whole class
    of subjects we hold ("all the influencers in your network", "which
    brands do you cover", "how many athletes in your network sell their
    own product", "poll your talent database and see which of these
    influencers offer a service"), else None. The qualifier is the
    condition on the class, cleaned for a chip, '' when there is none.
    A named subject after the scope ("in your network that Taylor Swift
    fans buy") is a real question and does not match."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 260:
        return None
    m = _ROSTER_RX.search(t)
    if not m:
        return None
    words = [m.group(k) for k in ('cls', 'cls2', 'cls3', 'cls4', 'cls6', 'cls7', 'cls8') if m.group(k)]
    qual = next((m.group(k) for k in ('q1', 'q2', 'q4', 'q5') if m.group(k)), '')
    if m.group('db'):
        # "poll your talent database and see which of these influencers also offer a service"
        m2 = _DB_REST_RX.search(m.group('rest3') or '')
        if m2:
            words, qual = [m2.group('cls')], m2.group('qual') or ''
        else:
            words = [re.sub(r"\s.*$", '', m.group('db').split(' ', 1)[1]).rstrip('s') + 's']
    if not words:
        words = ['influencers'] if re.search(r"\b(?:talent|influencer|creator)\b", t, re.I) else ['subjects']
    nouns, person = [], False
    for w in words:
        n, is_p = _class_noun(w)
        person = person or is_p
        if n not in nouns:
            nouns.append(n)
    if 'audience' in nouns and len(nouns) > 1:
        nouns.remove('audience')
    qual = _clean_qual(qual)
    if qual and re.search(r"(?<![.!?] )\b(?!I\b)[A-Z][A-Za-z&'.-]+", qual):
        return None     # a named person or brand inside the condition ("that Taylor Swift fans follow"): a real question
    return ' or '.join(nouns[:2]), person, qual


def roster_reply(noun, person, qualifier=''):
    """Jenna 2026-10-15: "the response should always default to we can
    pull anything you want, just request it" and "it should ask what
    influencer(s) are you talking about". With a condition on the class,
    offer the profile on that group as well."""
    plural = _plural(noun)
    if not qualifier:
        who = 'Who is it?' if person else 'Which one?'
        each = 'each audience' if person else 'each one'
        return (f"I can pull any {noun} you would like. {who} Name one, or paste a list, "
                f"and I will read {each}."), []
    # Jenna 2026-10-15, on "I want to see who are influencers or talent that
    # have a product or service they are selling": "that should cost $500
    # and list influencers who have their own product lines". The offer is
    # the list, a Custom Ask, not a profile.
    from . import talent_list_job as _tl
    reply, chips, _offer = _tl.offer(plural, qualifier, person)
    return reply, chips


def parse(text):
    """('exists'|'size'|'roster', subject) or None."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 200:
        return None
    _ro = roster_ask(t)
    if _ro:
        return 'roster', _ro
    m = _EXISTS_RX.match(t)
    if m:
        subj = _clean_subject(m.group('rest'))
        if subj and len(subj.split()) <= 10 and _looks_like_entity(subj):
            return 'exists', subj
        return None
    m = _SIZE_RX.match(t)
    if m:
        subj = _clean_subject(m.group('s1') or m.group('s2') or m.group('s3')
                              or m.group('s4') or m.group('s5'))
        if subj and len(subj.split()) <= 10 and _looks_like_entity(subj):
            return 'size', subj
    return None


_SUBIQ_RX = re.compile(r'\bsub(?:scriber)?\s*iq\b', re.I)


def _fmt_people(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _window_text(w):
    if not isinstance(w, dict) or not w.get('start'):
        return ''
    return f" ({w.get('start')} to {w.get('end')})"


def _summarize(anchors):
    """One line per product the catalog holds on the subject."""
    lines = []
    facts = anchors.get('facts') or []
    by_product = {}
    for f in facts:
        by_product.setdefault(f.get('product'), []).append(f)
    for product in ('profile', 'journey', 'attribution', 'bpiq', 'subiq', 'trends', 'deck'):
        fs = by_product.get(product)
        if not fs:
            continue
        label = _PRODUCT_LABEL.get(product, product)
        if product == 'profile':
            parents = [f for f in fs if f.get('kind') == 'audience_size' and not f.get('cut')]
            cuts = {(f.get('note') or '').replace('Profile IQ: ', '') for f in fs if f.get('cut')}
            if parents:
                p = parents[0]
                lines.append(f"{label}: {_fmt_people(p['value'])} US people{_window_text(p.get('window'))}"
                             + (f", plus {len(cuts)} cut{'s' if len(cuts) != 1 else ''}" if cuts else ''))
            elif cuts:
                lines.append(f"{label}: {len(cuts)} cut{'s' if len(cuts) != 1 else ''}")
            else:
                lines.append(label)
        elif product == 'journey':
            ends = [f for f in fs if f.get('kind') == 'audience_size']
            if ends:
                e = ends[0]
                lines.append(f"{label}: {_fmt_people(e['value'])} US people at the end point{_window_text(e.get('window'))}")
            else:
                lines.append(label)
        elif product == 'attribution':
            top = [f for f in fs if f.get('kind') == 'stage_count']
            top.sort(key=lambda f: -float(f.get('value') or 0))
            if top:
                lines.append(f"{label}: {_fmt_people(top[0]['value'])} US people {top[0].get('label')}{_window_text(top[0].get('window'))}")
            else:
                lines.append(label)
        elif product == 'bpiq':
            aud = [f for f in fs if f.get('kind') == 'audience_size']
            if aud:
                lines.append(f"{label}: {_fmt_people(aud[0]['value'])} US people reached{_window_text(aud[0].get('window'))}")
            else:
                lines.append(label)
        else:
            lines.append(label)
    return lines


def answer(text, ctx=None):
    """A finished reply payload for an existence or size ask the
    catalog can settle, else None."""
    parsed = parse(text)
    if not parsed:
        return None
    kind, subj = parsed
    if kind == 'roster':
        reply, chips = roster_reply(*subj)
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True, 'roster_ask': True}
    try:
        from migration import corpus_catalog as cc
    except Exception:
        return None
    try:
        anchors = cc.anchors_for(subj, with_ledger=False)
    except Exception:
        return None
    facts = anchors.get('facts') or []
    if not facts:
        # a near spelling or an audience noun on the name ("fanduel
        # cusotmers", "Kalshi bettors") still names a held profile
        # (2026-10-14, Ben: 'No. There is nothing on data pull on fanduel
        # cusotmers' while FanDuel sat on the dashboard)
        try:
            from . import sizing_lane as _szl
            hit = _szl.resolve(subj, ctx)
            if hit and hit.get('name'):
                anchors = cc.anchors_for(hit['name'], with_ledger=False)
                facts = anchors.get('facts') or []
        except Exception:
            pass
    related = anchors.get('related') or []
    display = anchors.get('subject') or subj
    if kind == 'size':
        pa = cc.profile_anchor(anchors) if facts else None
        if not pa or not pa.get('audience_size'):
            return None  # the reasoning path sizes it
        reply = (f"{display}: {_fmt_people(pa['audience_size'])} US people"
                 f"{_window_text(pa.get('window'))}, {_fmt_people(pa.get('sample_size'))} "
                 f"inside the 10 million sample. That is the profile on the dashboard.")
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': [f"Open {display}", f"Who is the {display} audience?"],
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'subject': display}
    if facts:
        lines = _summarize(anchors)
        body = '; '.join(lines) if lines else 'it is on the dashboard'
        reply = f"Yes. On {display} we have {body}. Open it from the dashboard, or ask me about it here."
        chips = [f"Open {display}", f"Who is the {display} audience?"]
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'subject': display}
    if _SUBIQ_RX.search(str(text or '')):
        return None  # the Subscriber IQ lookup lane owns a miss here
    if related:
        names = [r.get('subject') for r in related[:4] if r.get('subject')]
        reply = (f"Not under that exact name. Close matches on the dashboard: "
                 f"{', '.join(names)}. Say which one you mean, or say 'build a profile on {subj}' "
                 f"and I will draft it.")
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': names[:3] + [f"Build a profile on {subj}"],
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'memory_confirm': {'question': text,
                                   'options': [{'label': n, 'subject': n} for n in names[:3]]}}
    reply = (f"No. There is nothing on {subj} on the dashboard yet. Say 'build a profile on {subj}' "
             f"and I will draft it for your approval.")
    return {'success': True, 'action': 'answer', 'reply': reply,
            'followups': [f"Build a profile on {subj}"],
            'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True}
