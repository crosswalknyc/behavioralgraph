"""Unresolved referents: ask who they mean before anything else runs.

Jenna, 2026-10-02 (verbatim, typos cleaned): "it should have asked
him which 3 influencers he was talking about then actually given him
the answer."

The defect: "review these three creators and prepare a report on
which of the three actually influence product purchases" named no
creator. Nothing on the server stopped to ask; the subject extractor
produced "Three Actually Influence Product Purchases and" and the
priced research-report offer went out on that string.

This module is the gate. It is pure (no Flask, no S3, no model call)
so every surface can run it first and every client gets the same
question back.

Three jobs:

``detect_unresolved(text, history, ctx)``
    A demonstrative or counted plural referent ("these three
    creators", "those brands", "the following shows", "both
    podcasts", "all 4 titles") with no name anywhere the server can
    see: not in the ask, not in the recent user turns of the thread,
    not as extra profiles open on the compare view. Returns the
    referent dict or None.

``clarify_payload(ref, text)``
    The question to send back: which ones, by name, plus what runs the
    moment the names land. No model call, no charge, no offer.

``answer_merge(history, text)``
    The next user turn, when it is the names, folds back into the
    question that triggered the ask so the original question is not
    lost and the read runs on exactly those names.

``plausible_subject(s)``
    A subject string made only of ordinary English words, or ending
    in a connective ("and", "of", "the"), is not a subject. Callers
    that take a subject from a model or an extractor should treat an
    implausible one as empty.
"""
import re

_COUNT_WORDS = {
    'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
    'eight': 8, 'nine': 9, 'ten': 10, 'both': 2, 'couple': 2, 'few': 3,
    'several': 3, 'handful': 4,
}

_NOUNS = (
    r'creators?|influencers?|youtubers?|streamers?|tiktokers?|'
    r'podcasters?|hosts?|talents?|celebrities|celebs?|artists?|'
    r'musicians?|bands?|actors?|actresses|athletes?|players?|teams?|'
    r'brands?|companies|company|retailers?|products?|labels?|'
    r'shows?|titles?|series|seasons?|movies?|films?|franchises?|'
    r'podcasts?|games?|apps?|platforms?|services?|networks?|channels?|'
    r'campaigns?|audiences?|profiles?|cohorts?|segments?|cuts?|'
    r'people|names?|accounts?|handles?|options?|candidates?|picks?'
)

# "these three creators" / "those brands" / "the following shows" /
# "both podcasts" / "all 4 titles" / "each of the three creators" /
# "the three creators" / "the 3 influencers". A bare "the brands" is
# NOT a referent (it is ordinary English: "what are the brands they
# buy").
_REF_RX = re.compile(
    r'\b(?:'
    r'(?P<dem>these|those|the\s+following|the\s+above|the\s+same|'
    r'each\s+of\s+(?:these|those|the)|all\s+of\s+(?:these|those|the)|'
    r'both(?:\s+of\s+(?:these|those|the))?|all|the)'
    r')\s+'
    r'(?P<count>two|three|four|five|six|seven|eight|nine|ten|\d{1,2})?'
    r'\s*(?P<noun>' + _NOUNS + r')\b',
    re.I)

_DEM_ONLY = re.compile(
    r'^(?:these|those|the\s+following|the\s+above|the\s+same|'
    r'each\s+of|all\s+of|both)', re.I)

# A name present anywhere in the ask resolves the referent.
_QUOTED_RX = re.compile(r'["\u201c\u2018\'][^"\u201d\u2019\']{2,60}["\u201d\u2019\']')
_HANDLE_RX = re.compile(r'(?<!\w)@[A-Za-z0-9_.]{2,}')
_LIST_AFTER_COLON_RX = re.compile(r'[:\-]\s*[^,\n]{2,40}(?:,\s*[^,\n]{2,40})+')
_CAP_RUN_RX = re.compile(
    r'\b([A-Z][A-Za-z0-9&\'\+\.]*(?:\s+[A-Z][A-Za-z0-9&\'\+\.]*)*)\b')

_SENTENCE_START_WORDS = {
    'review', 'compare', 'prepare', 'preapre', 'please', 'can', 'could',
    'would', 'what', 'which', 'who', 'how', 'why', 'where', 'when',
    'show', 'tell', 'give', 'run', 'build', 'pull', 'look', 'analyze',
    'analyse', 'rank', 'list', 'do', 'does', 'is', 'are', 'i', 'we',
    'the', 'a', 'an', 'these', 'those', 'this', 'that', 'for', 'on',
    'of', 'and', 'or', 'to', 'in', 'with', 'about', 'from', 'by',
    'our', 'my', 'your', 'their', 'top', 'best', 'most', 'all', 'both',
    'each', 'some', 'any', 'then', 'also', 'report', 'prometheus',
}

_COMMON_WORDS = {
    # function words + the ordinary English that showed up in the
    # defect subject ("three actually influence product purchases and")
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'for',
    'with', 'by', 'from', 'at', 'as', 'is', 'are', 'was', 'were', 'be',
    'this', 'that', 'these', 'those', 'it', 'its', 'they', 'them',
    'their', 'we', 'our', 'you', 'your', 'i', 'my', 'me', 'he', 'she',
    'his', 'her', 'who', 'which', 'what', 'where', 'when', 'why', 'how',
    'all', 'any', 'some', 'each', 'both', 'few', 'more', 'most', 'other',
    'such', 'no', 'not', 'only', 'own', 'same', 'so', 'than', 'too',
    'very', 'just', 'also', 'then', 'there', 'here', 'about', 'into',
    'over', 'under', 'again', 'further', 'once', 'up', 'down', 'out',
    'off', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
    'eight', 'nine', 'ten', 'first', 'second', 'third', 'last', 'next',
    'new', 'old', 'top', 'best', 'worst', 'big', 'small', 'high', 'low',
    'actually', 'really', 'truly', 'genuinely', 'basically', 'simply',
    'influence', 'influences', 'influencing', 'drive', 'drives', 'move',
    'moves', 'affect', 'affects', 'impact', 'impacts', 'sell', 'sells',
    'convert', 'converts', 'buy', 'buys', 'purchase', 'purchases',
    'purchasing', 'product', 'products', 'brand', 'brands', 'category',
    'categories', 'audience', 'audiences', 'people', 'viewers', 'users',
    'fans', 'customers', 'consumers', 'shoppers', 'buyers', 'creators',
    'creator', 'influencers', 'influencer', 'talent', 'show', 'shows',
    'title', 'titles', 'series', 'movie', 'movies', 'film', 'films',
    'report', 'reports', 'analysis', 'read', 'data', 'numbers', 'list',
    'review', 'compare', 'comparison', 'prepare', 'preapre', 'versus',
    'vs', 'against', 'between', 'among', 'across', 'within', 'per',
    'much', 'many', 'lot', 'lots', 'kind', 'kinds', 'type', 'types',
    'thing', 'things', 'something', 'anything', 'everything', 'nothing',
    'someone', 'anyone', 'everyone', 'way', 'ways', 'time', 'times',
    'year', 'years', 'month', 'months', 'week', 'weeks', 'day', 'days',
    'good', 'bad', 'better', 'worse', 'well', 'like', 'want', 'wants',
    'need', 'needs', 'make', 'makes', 'get', 'gets', 'see', 'sees',
    'know', 'knows', 'think', 'thinks', 'look', 'looks', 'use', 'uses',
    'say', 'says', 'tell', 'tells', 'give', 'gives', 'go', 'goes',
    'come', 'comes', 'take', 'takes', 'find', 'finds', 'work', 'works',
    'run', 'runs', 'build', 'builds', 'pull', 'pulls', 'rank', 'ranks',
    'subject', 'profile', 'profiles', 'cut', 'cuts', 'cohort', 'cohorts',
    'segment', 'segments', 'market', 'markets', 'digital', 'online',
    'social', 'media', 'content', 'video', 'videos', 'music', 'sports',
    'news', 'streaming', 'platform', 'platforms', 'service', 'services',
    'company', 'companies', 'business', 'businesses', 'sales', 'revenue',
    'growth', 'share', 'shares', 'rate', 'rates', 'count', 'counts',
    'total', 'totals', 'average', 'overall', 'general', 'specific',
    'real', 'true', 'false', 'yes', 'no', 'ok', 'okay', 'please', 'thanks',
    'thank', 'hi', 'hello', 'hey', 'us', 'american', 'america', 'national',
}

_CONNECTIVE_TAIL = {
    'and', 'or', 'of', 'the', 'a', 'an', 'to', 'for', 'with', 'on',
    'in', 'at', 'by', 'from', 'vs', 'versus', 'against', 'between',
    'but', 'nor', 'so', 'than', 'that', 'which', 'who', 'is', 'are',
}

_CONNECTIVE_HEAD = {
    'and', 'or', 'of', 'the', 'a', 'an', 'to', 'for', 'with', 'on',
    'in', 'at', 'by', 'from', 'vs', 'versus', 'against', 'between',
    'but', 'nor', 'so', 'than', 'that', 'which', 'who', 'actually',
    'really', 'truly',
}

_CLARIFY_TURN_RX = re.compile(
    r'^\s*which\s+(?:\w+\s+)?(?:' + _NOUNS + r')\s*\?', re.I)

_QUESTION_OPEN_RX = re.compile(
    r'^(what|which|who|whose|how|why|where|when|do|does|did|are|is|'
    r'was|were|can|could|would|should|show me|tell me|give me)\b', re.I)

_VERB_IN_ITEM_RX = re.compile(
    r'\b(compare|review|report|influence|drive|rank|build|run|pull|'
    r'analy[sz]e|which|what|how|should|would|could|please)\b', re.I)

_NAME_ITEM_SPLIT_RX = re.compile(r'\s*(?:,|;|/|&|\band\b|\bvs\.?\b|\bversus\b|\n)\s*', re.I)


def _count_of(m):
    raw = (m.group('count') or '').strip().lower()
    dem = (m.group('dem') or '').strip().lower()
    if raw.isdigit():
        return int(raw)
    if raw in _COUNT_WORDS:
        return _COUNT_WORDS[raw]
    if dem.startswith('both'):
        return 2
    return 0


def _names_in_text(text):
    """True when the ask itself carries at least one name: a
    capitalized run that is not a sentence opener, a quoted string, a
    handle, or a listed set after a colon."""
    t = str(text or '')
    if _QUOTED_RX.search(t) or _HANDLE_RX.search(t):
        return True
    if _LIST_AFTER_COLON_RX.search(t):
        return True
    for run in _CAP_RUN_RX.findall(t):
        words = [w for w in run.split()
                 if w.lower().strip('.') not in _SENTENCE_START_WORDS]
        if not words:
            continue
        # A lone capitalized common word mid-sentence ("Report") is
        # not a name; a two-word run or a non-common word is.
        if len(words) >= 2 or words[0].lower() not in _COMMON_WORDS:
            return True
    return False


def _recent_user_turns(history, k=3):
    out = []
    for h in reversed(list(history or [])):
        if not isinstance(h, dict):
            continue
        if str(h.get('role') or '').lower() == 'user':
            out.append(str(h.get('text') or h.get('content') or ''))
            if len(out) >= k:
                break
    return out


def _ctx_profile_count(ctx):
    if not isinstance(ctx, dict):
        return 0
    n = 1 if ctx.get('primary') else 0
    for key in ('extras', 'other_tabs', 'compare', 'secondary'):
        v = ctx.get(key)
        if isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict) and v:
            n += 1
    return n


def detect_unresolved(text, history=None, ctx=None):
    """The unresolved referent in this ask, or None.

    Returns {'phrase', 'noun', 'count', 'count_word'} when the ask
    points at a specific set of things by demonstrative or by count
    and no name for them is visible anywhere: not in the ask, not in
    the last few user turns, not as profiles open on the compare view.
    """
    t = str(text or '').strip()
    if not t or len(t) > 1200:
        return None
    best = None
    for m in _REF_RX.finditer(t):
        dem = (m.group('dem') or '').strip().lower()
        count = _count_of(m)
        # Plain "the brands" / "all titles" with no count is ordinary
        # English, not a pointer at a specific unnamed set.
        if dem in ('the', 'all') and not count:
            continue
        noun = (m.group('noun') or '').strip().lower()
        # "these people" / "those names" are the users themselves or
        # a list already given; handled by anaphora elsewhere unless
        # a count is attached.
        if noun in ('people', 'names', 'name') and not count:
            continue
        cand = {'phrase': m.group(0).strip(), 'noun': noun,
                'count': count,
                'count_word': (m.group('count') or '').strip().lower()
                or ('both' if dem.startswith('both') else '')}
        if best is None or (cand['count'] and not best['count']):
            best = cand
    if best is None:
        return None
    if _names_in_text(t):
        return None
    for prev in _recent_user_turns(history, k=3):
        if _names_in_text(prev):
            return None
    n_open = _ctx_profile_count(ctx)
    if n_open >= 2 and (not best['count'] or n_open >= best['count']):
        return None
    return best


def _plural(noun):
    n = noun.lower()
    if n in ('series', 'people', 'company', 'companies'):
        return {'company': 'companies'}.get(n, n)
    if n.endswith('s'):
        return n
    if n.endswith('y') and not n.endswith(('ay', 'ey', 'oy', 'uy')):
        return n[:-1] + 'ies'
    return n + 's'


def _task_clause(text):
    """What runs once the names land, in the reader's own terms."""
    t = str(text or '')
    tl = t.lower()
    if re.search(r'\b(influence|drive|move|convert)\w*\b[^.]{0,40}'
                 r'\b(purchase|buy|sale|product)', tl):
        return ("I'll run the comparison on exactly those: who "
                "actually drives product purchases, and in which "
                "categories.")
    if re.search(r'\bcompare|\bversus\b|\bvs\.?\b|\bwhich of\b|\brank\b|'
                 r'\bagainst\b|\bside by side\b', tl):
        return "I'll run the comparison on exactly those."
    if re.search(r'\breport\b|\bdeck\b|\bone.?pager\b|\bwrite.?up\b', tl):
        return "I'll put the read together on exactly those."
    return "I'll answer on exactly those."


def clarify_payload(ref, text):
    """The clarifying question, as a chat payload. No charge, no
    offer, no model call. ``referent_clarify`` lets the next turn
    merge the names back into this question."""
    noun = _plural(ref.get('noun') or 'names')
    cw = str(ref.get('count_word') or '').strip()
    count = int(ref.get('count') or 0)
    if cw.isdigit():
        cw = {2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six',
              7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}.get(
                  int(cw), cw)
    head = f"Which {cw} {noun}?" if cw and cw != 'both' else (
        f"Which two {noun}?" if count == 2 else f"Which {noun}?")
    reply = f"{head} Name them and {_task_clause(text)}"
    return {
        'success': True,
        'action': 'clarify',
        'reply': reply,
        'followups': [],
        'offer_deck': False,
        'deck_angle': None,
        'referent_clarify': {
            'question': str(text or ''),
            'noun': ref.get('noun'),
            'count': count,
            'phrase': ref.get('phrase'),
        },
    }


def _split_names(t):
    items = [s.strip(' .') for s in _NAME_ITEM_SPLIT_RX.split(t) if s]
    items = [s for s in items if s]
    return items


def _looks_like_names(t, expected=0):
    s = str(t or '').strip()
    if not s or len(s) > 240 or '?' in s:
        return []
    if _QUESTION_OPEN_RX.match(s):
        return []
    items = _split_names(s)
    if not items or len(items) > 8:
        return []
    for it in items:
        if len(it.split()) > 5 or _VERB_IN_ITEM_RX.search(it):
            return []
    if expected and len(items) == 1 and expected > 1:
        # One token for a counted set could be a name ("MrBeast") or
        # a stray word; accept only when it reads as a name.
        return items if _names_in_text(items[0]) else []
    return items


def _referent_of_clarify(turn):
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    rc = turn.get('referent_clarify') or meta.get('referent_clarify')
    if isinstance(rc, dict) and rc.get('question'):
        return rc
    raw = turn.get('raw') if isinstance(turn.get('raw'), dict) else {}
    rc = raw.get('referent_clarify')
    if isinstance(rc, dict) and rc.get('question'):
        return rc
    return None


def answer_merge(history, text):
    """When the previous agent turn was our which-ones question and
    this turn supplies the names, return (merged_question, label).
    Otherwise ('', '')."""
    t = str(text or '').strip()
    turns = [h for h in (history or []) if isinstance(h, dict)]
    last_agent = None
    idx = -1
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = turns[i]
            idx = i
            break
        if role == 'user':
            break
    if last_agent is None:
        return '', ''
    agent_text = str(last_agent.get('text') or last_agent.get('content')
                     or '')
    rc = _referent_of_clarify(last_agent)
    if rc is None and not _CLARIFY_TURN_RX.match(agent_text):
        return '', ''
    orig = (rc or {}).get('question') or ''
    if not orig:
        for i in range(idx - 1, -1, -1):
            if str(turns[i].get('role') or '').lower() != 'user':
                continue
            cand = str(turns[i].get('text') or turns[i].get('content')
                       or '').strip()
            if cand and detect_unresolved(cand):
                orig = cand
                break
    if not orig:
        return '', ''
    ref = detect_unresolved(orig) or {}
    expected = int((rc or {}).get('count') or ref.get('count') or 0)
    items = _looks_like_names(t, expected)
    if not items:
        return '', ''
    if len(items) == 1:
        label = items[0]
    elif len(items) == 2:
        label = f"{items[0]} and {items[1]}"
    else:
        label = ', '.join(items[:-1]) + f" and {items[-1]}"
    phrase = (rc or {}).get('phrase') or ref.get('phrase')
    if phrase and phrase.lower() in orig.lower():
        i = orig.lower().index(phrase.lower())
        merged = orig[:i] + label + orig[i + len(phrase):]
    else:
        merged = f"{orig}\n\nNames: {label}"
    return merged, label


_SELF_WORDS = frozenset(('crosswalk', 'crosswalks', 'prometheus', 'sample',
                         'samples', 'panel', 'panels', 'panelist',
                         'panelists', 'teh'))


_TITLE_ARTICLE_RX = re.compile(r"^(?:The|A|An)\s+[A-Z][A-Za-z0-9&'\+\.]*")


def plausible_subject(s):
    """False for a subject made of ordinary words, or one that starts
    or ends on a connective. 'Three Actually Influence Product
    Purchases and' is the shape this rejects."""
    subj = str(s or '').strip()
    if not subj or len(subj) < 2:
        return False
    toks = [w for w in re.findall(r"[A-Za-z0-9&'\+\.]+", subj)]
    if not toks:
        return False
    low = [w.lower().strip('.') for w in toks]
    # Crosswalk itself, its sample, and Prometheus are never a subject
    # to build (2026-10-05, 'Teh Crosswalk Sample' was offered as a
    # profile when Scott asked how big the sample is).
    if any(w in _SELF_WORDS for w in low):
        return False
    # A capitalized title that opens on "The" is a subject, however
    # ordinary the next word is: The Office, The Bear, The Crown, The
    # Paper, The Voice (2026-10-08, East Tree Media: "The Office" was
    # rejected and the ask bound to "Office"). Source casing decides;
    # "the audience" stays ordinary words.
    if _TITLE_ARTICLE_RX.match(subj) and 2 <= len(low) <= 5 \
            and low[-1] not in _CONNECTIVE_TAIL:
        return True
    if low[-1] in _CONNECTIVE_TAIL or low[0] in _CONNECTIVE_HEAD:
        return False
    if all(w in _COMMON_WORDS for w in low):
        return False
    if len(low) >= 5 and sum(w in _COMMON_WORDS for w in low) >= len(low) - 1:
        return False
    return True
