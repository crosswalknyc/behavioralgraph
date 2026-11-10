"""Where things are on the dashboard (Jenna 2026-11-10).

Jenna, verbatim: "it eomeone asks something like this it should tell
them where to find it. like go to the drop dowb menu and click on
digital journey iq kinda thing".

The case: a Sony Television seat, with a Walter Boys read just landed,
asked "where is the digital jounrey in the dashboard?" and the status
lane answered with the finished read glued under "Your read for Select
a Journey finished 5 minutes ago. Here it is:". No directions, a read
that was not asked for, and the picker's placeholder as the subject.

This lane answers a where-is question with directions from
prometheus.dashboard_map, the one map of the dashboard:

* the SELECT PRODUCT dropdown (top right) and the product to click,
  named exactly as the dropdown shows it;
* the product's own picker or tab (Select a journey, Select Profile,
  the Twins tab, Pilot Testing ...);
* the thing the asker holds when they name it ("Your My Life with the
  Walter Boys journey is there under Select a journey"), through the
  host's own listings (their journeys, the profiles on their seat, their
  company's twins); when they name a thing that is not there yet it says
  so and offers the build;
* the deep link when the view takes one (?view=journeyIQ);
* one chip, Open <Product>, which the widget turns into the page switch.

A product the seat cannot open is named with "ask your admin for
access"; a product the admin hid is said to be off the dashboard right
now. Two to four sentences, no other chips, typos tolerated
("jounrey"). Deterministic, never priced, no model. A data question that
happens to start with "where" ("where did the viewers come from", "where
is the fall-off") is not this lane's; neither is a named deliverable
("where is the Howdy deck", the deliverable lane) nor the reader's own
results ("where are my results", the status lane).
"""
from __future__ import annotations

import re
import traceback

from . import dashboard_map as M

__all__ = ['wants', 'answer', 'target_of', 'named_thing', 'directions', 'OPEN_CHIP', 'ADD_CHIP', 'add_order', 'chat_reads']

OPEN_CHIP = 'Open {label}'
# A journey the seat holds only as chat turns (a read answered in the thread,
# nothing written to the Digital Journey IQ registry) is never said to be in
# the picker (Jenna 2026-11-10: the Sony seat's Walter Boys read said "It is
# in the picker now" and was not). The reply says the read is in the chat
# and offers this chip; the chip is a build order for the journey lane.
ADD_CHIP = 'Add it to Digital Journey IQ'
_ADD_RX = re.compile(
    r"^\W*(?:add|put|place|save|move)\s+(?P<what>it|this|that|them|the\s+read|that\s+read|this\s+read|[^?]{2,90}?)\s+"
    r"(?:to|into|in|on)\s+(?:the\s+)?(?:digital\s+journey(?:\s+iq)?|journey\s+iq|select\s+a\s+journey|the\s+journey\s+picker)[.!\s]*$", re.I)
_MAX_LEN = 240

_WHERE_RX = re.compile(
    r"\b(?:where(?:'s|\s+is|\s+are|\s+was|\s+were)\b"
    r"|where\s+(?:do|can|would|could|should|did|does|will|might)\s+(?:i|we|one|you|it|they|my|the|a|that|this)\b"
    r"|how\s+(?:do|can|would|could|should|did)\s+(?:i|we|you)\s+(?:get\s+(?:to|into|back\s+to)|open|find|see|reach|access|navigate\s+to|go\s+to|go\s+back\s+to|pull\s+up|bring\s+up|view|switch\s+to|turn\s+on|enable)\b"
    r"|how\s+to\s+(?:open|find|get\s+to|reach|access|view|switch\s+to)\b"
    r"|which\s+(?:tab|menu|dropdown|drop\s*down|page|screen|section|button|product|module|view)\b"
    r"|what\s+(?:tab|menu|dropdown|drop\s*down|page|screen|section)\b"
    r"|is\s+there\s+(?:a|an)\s+(?:tab|page|menu|button|section|place|view)\b"
    r"|i\s+(?:can'?t|cannot|could\s+not|couldn'?t|don'?t|do\s+not)\s+(?:find|see|locate)\s+(?:the|a|an|my|any)\s+(?:tab|menu|dropdown|drop\s*down|page|button|section|view)\b"
    r"|\bpoint\s+me\s+to\b|\bshow\s+me\s+where\b|\btake\s+me\s+to\b|\bget\s+me\s+to\b|\bhow\s+do\s+i\s+(?:use|work)\s+the\s+(?:dropdown|menu|rail)\b)", re.I)
# a question about the data, not the dashboard: never ours
_DATA_RX = re.compile(
    r"\b(?:fall[- ]?offs?|drop[- ]?offs?|index(?:es|ed)?|penetration|overlaps?|share\s+of|what\s+share|percent(?:age)?|%|how\s+many|how\s+much|"
    r"viewers?|audiences?|subscribers?|fans|signups?|sign-ups?|conversions?|watch(?:ed|ing|es)?|stream(?:ed|ing|s)?|bought|buy(?:ing|s)?|"
    r"shop(?:ped|ping|s)?|came\s+from|come\s+from|coming\s+from|go\s+next|went\s+next|churn(?:ed)?|lift|spend|traffic|visit(?:s|ed|ors)?|"
    r"clicks?|sessions?|minutes|hours|growth|ratings?|revenue|sales|demographics?\s+(?:of|for)|skew|most\s+popular|top\s+\d+|rank(?:ed|ing)?\s+(?:of|for))\b", re.I)
_NAV_WORDS_RX = re.compile(r"\b(?:tab|tabs|menu|dropdown|drop\s*down|button|page|screen|section|dashboard|rail|picker|sidebar|panel|view)\b", re.I)
# the Open chip itself, typed or sent from an older turn ("Open Digital Journey IQ", "take me to profile iq"):
# the widget switches the page on its own; when the words reach the server they get the directions
_OPEN_RX = re.compile(r"^\W*(?:open|go\s+to|take\s+me\s+to|switch\s+to|show\s+me|bring\s+up)\s+(?:the\s+)?(?P<what>[A-Za-z0-9&/ \-]{2,40}?)\s*(?:tab|page|view|dashboard)?[.!\s]*$", re.I)
_PRODUCT_OBJECT_RX = re.compile(r"\b(?:iq|dashboard|tab|menu|page|screen|product|module|tool)\b", re.I)

_NAMED_BEFORE_RX = re.compile(
    r"\b(?:my|the|our|that|this|a|an)\s+(?P<name>[A-Za-z0-9][^?]*?)\s+"
    r"(?P<noun>journeys?|profiles?|twins?|cuts?|campaigns?|valuations?|flywheels?|focus\s+groups?|pilot\s+tests?|pilots?|trackers?|studies|study)\b", re.I)
_NAMED_AFTER_RX = re.compile(
    r"\b(?P<noun>journeys?|profiles?|twins?|cuts?|campaigns?|valuations?|flywheels?|focus\s+groups?|pilot\s+tests?|trackers?|study)\s+"
    r"(?:for|on|of|about|called|named|titled)\s+(?P<name>[^?]+?)\s*(?=\?|$|\s+(?:in|on)\s+(?:the\s+)?(?:dashboard|site|page|app)\b)", re.I)
# noise at the edges of a name only: a title keeps its own words ('My Life
# with the Walter Boys', 'The Office'); a determiner comes off only when the
# reader typed it in lower case ahead of a capitalized name ('the Suits read')
_NAME_LEAD_RX = re.compile(r"^(?:(?:digital|new|newest|latest|finished|completed|done|recent|own|please|that|this)\b\s*)+", re.I)
_NAME_TAIL_RX = re.compile(r"(?:\s+(?:iq|dashboard|prometheus|data|please|one|own|digital|read|journey|audience))+$", re.I)
_TYPED_DET_RX = re.compile(r"^(?:the|my|our|a|an)\s+(?=[A-Z0-9])")


def _trim_name(raw, *, determiner=False):
    name = ' '.join(str(raw or '').split())
    name = _NAME_LEAD_RX.sub('', name)
    if determiner:
        name = _TYPED_DET_RX.sub('', name)
    name = _NAME_TAIL_RX.sub('', name)
    return name.strip(" ,.;:'\"")
_STOP = frozenset(('the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by', 'with', 'from', 'about', 'my', 'our', 'your', 'its'))


def _clean(text):
    return ' '.join(str(text or '').split())


def _tokens(s):
    return [t for t in re.sub(r"[^a-z0-9+&]+", ' ', str(s or '').lower()).split() if t not in _STOP]


# ---------------------------------------------------------------- routing

def target_of(text):
    """What the words point at: {'kind': 'product'|'tab'|'artifact'|
    'dropdown'|'rail', ...} or None. A tab beats its product when the
    words name the tab ('where is pilot testing'); a product beats a
    bare artifact noun ('the digital journey' is the product, 'my Walter
    Boys journey' is an artifact of it)."""
    t = _clean(text)
    if not t:
        return None
    tab = M.find_tab(t)
    prod = M.find_product(t)
    art = M.find_artifact(t)
    if tab and prod and tab['product'] == prod['value'] and tab['id'] in ('twins', 'insights') \
            and re.search(r"\bdigital\s+twins?\b|\bprofile\s+iq\b", t, re.I):
        tab = None                      # 'digital twins' names the product; its landing tab is the product
    if tab and not (prod and prod['value'] != tab['product'] and len(prod['label']) > len(tab['label'])):
        return {'kind': 'tab', 'tab': tab, 'product': M.product(tab['product'])}
    if prod:
        # 'where is my Walter Boys journey' names a thing in the product; 'where is the digital journey' names the product
        name = named_thing(t, prod)
        if name and art and art in M.ARTIFACTS and M.ARTIFACTS[art][0] == prod['value']:
            return {'kind': 'artifact', 'noun': art, 'name': name, 'product': prod}
        return {'kind': 'product', 'product': prod, 'name': name}
    if M.names_rail(t):
        return {'kind': 'rail'}
    if M.names_dropdown(t):
        return {'kind': 'dropdown'}
    if art:
        pv = M.ARTIFACTS[art][0] if art in M.ARTIFACTS else ''
        return {'kind': 'artifact', 'noun': art, 'name': named_thing(t, M.product(pv) if pv else None), 'product': M.product(pv) if pv else None}
    return None


def named_thing(text, prod=None):
    """The thing the asker names beside the artifact noun ('my Walter
    Boys journey' -> 'Walter Boys'; 'the journey for Suits' -> 'Suits');
    '' when the words name only the product or nothing."""
    t = _clean(text)
    best = ''
    for rx in (_NAMED_AFTER_RX, _NAMED_BEFORE_RX):
        m = rx.search(t)
        if m:
            best = m.group('name')
            break
    if not best:
        return ''
    name = re.sub(r"\b(?:in|on)\s+(?:the\s+)?(?:dashboard|site|page|app)\b.*$", '', best, flags=re.I)
    name = _trim_name(name)
    if not name or M.is_placeholder(name):
        return ''
    toks = _tokens(name)
    if not toks or len(toks) > 8 or not any(len(x) >= 3 for x in toks):
        return ''
    # the words were the product's own name ('digital jounrey', 'twins')
    if M.find_product(name) is not None and len(toks) <= 3:
        return ''
    if M.find_tab(name) is not None and len(toks) <= 3:
        return ''
    if re.fullmatch(r"(?:select|choose|pick)\b.*", name, flags=re.I):
        return ''
    return name


def add_order(text):
    """'Add it to Digital Journey IQ' -> ('it', False); 'Add My Life with
    the Walter Boys to Digital Journey IQ' -> ('My Life with the Walter
    Boys', True); None when the words are not that order."""
    m = _ADD_RX.match(_clean(text))
    if not m:
        return None
    what = ' '.join(m.group('what').split()).strip(" ,.;:'\"")
    if re.fullmatch(r"(?:it|this|that|them|the\s+read|that\s+read|this\s+read)", what, flags=re.I):
        return ('it', False)
    name = re.sub(r"^(?:the\s+|my\s+)?(?:read|journey|audience|profile)\s+(?:on|for|of|about)\s+", '', what, flags=re.I)
    name = _trim_name(name, determiner=True)
    if not name or M.is_placeholder(name) or not _tokens(name):
        return ('it', False)
    return (name, True)


def chat_reads(history):
    """The reads that landed in this thread, newest first, one per
    subject: [{'subject', 'ts', 'job_id'}]. A landed read is an agent turn
    carrying a read job id with its payload (prometheus.read_landing)."""
    out, seen = [], set()
    try:
        from .read_landing import _is_landed_turn as _landed
    except Exception:
        return out
    for t in reversed([h for h in (history or []) if isinstance(h, dict)]):
        try:
            if not _landed(t):
                continue
            m = t.get('meta') if isinstance(t.get('meta'), dict) else {}
            subj = str(m.get('subject') or '').strip()
            if not subj:
                head = str(t.get('text') or '').strip().split('\n', 1)[0]
                mm = re.match(r"^On\s+(?P<s>.{2,120}?)(?:\s+-\s+[^:]{1,80})?:\s*$", head)
                subj = mm.group('s').strip() if mm else ''
            if not subj or M.is_placeholder(subj):
                continue
            key = ' '.join(_tokens(subj))
            if key in seen:
                continue
            seen.add(key)
            out.append({'subject': subj, 'ts': str(t.get('ts') or ''), 'job_id': str(m.get('read_job_id') or '')})
        except Exception:
            continue
    return out


def _subject_from_history(history):
    """What 'it' is in 'Add it to Digital Journey IQ': the newest landed
    read's subject, else the thing the reader last named beside an
    artifact noun. '' when the thread names nothing."""
    reads = chat_reads(history)
    if reads:
        return reads[0]['subject']
    for t in reversed([h for h in (history or []) if isinstance(h, dict)]):
        if str(t.get('role') or '') != 'user':
            continue
        nm = named_thing(str(t.get('text') or ''))
        if nm:
            return nm
    return ''


def wants(text):
    """A where-is question about the dashboard, a product, a tab, an
    artifact's home, the dropdown or the rail; never a data question or
    a named deliverable. Also the 'Add it to Digital Journey IQ' order,
    which this lane turns into the journey build."""
    t = _clean(text)
    if not t or len(t) > _MAX_LEN or len(t.split()) > 34:
        return False
    if add_order(t):
        return True
    if not _WHERE_RX.search(t):
        m = _OPEN_RX.match(t)
        if not m:
            return False
        what = m.group('what').strip()
        # 'Open Digital Journey IQ' / 'go to pilot testing' name a product or a tab and nothing else;
        # 'open a focus group with the Gilmore twin', 'open the Howdy profile' and the twins' own
        # 'Open in Digital Twins IQ' / 'Open Focus Group IQ' chips are their own lanes
        if re.search(r"\b(?:a|an|my|this|that|with|on|for|in|of)\b", what, re.I) or len(_tokens(what)) > 4:
            return False
        tgt = target_of(what)
        # products only: 'Open Pilot Testing' and the other tab chips belong to the Digital Twins IQ lane
        if not tgt or tgt['kind'] != 'product' or tgt.get('name'):
            return False
        if (tgt.get('product') or {}).get('value') == M.ANALYSIS_IQ['value']:
            return False
        return True
    if _DATA_RX.search(t) and not _NAV_WORDS_RX.search(t):
        return False
    tgt = target_of(t)
    if not tgt:
        return False
    if tgt['kind'] == 'artifact':
        noun = tgt.get('noun')
        if noun == 'read':
            return False               # the reader's own results: the status lane
        if noun in ('deck', 'email') and tgt.get('name'):
            return False               # a named file: the deliverable lane
        if noun in ('deck', 'export', 'email'):
            try:
                from . import deliverables as _dlv
                p = _dlv.parse(t)
                if p and p.get('subject'):
                    return False       # 'where is the Howdy deck' is a lookup, not directions
            except Exception:
                pass
        if noun == 'profile' and not tgt.get('name') and re.search(r"\bprofile\b", t, re.I) and not _PRODUCT_OBJECT_RX.search(t) \
                and re.search(r"\b(?:my|the)\s+profile\b", t, re.I) and re.search(r"\b(?:did|does|will|has|is)\b.*\b(?:go|land|finish|done|ready)\b", t, re.I):
            return False               # 'where did my profile go' while it builds: the status lane knows the run
    return True


# -------------------------------------------------------------- holdings

def _journeys(uname, host, user):
    try:
        from . import send_file as _sf
        return [dict(j) for j in (_sf.user_journeys(uname, host, user=user) or [])]
    except Exception:
        return []


def _profiles(uname, host, user):
    try:
        from .legacy import chat as _chat
        cat = _chat._profile_catalog_for_chat() or []
    except Exception:
        cat = []
    if not cat:
        try:
            from . import bulk_pull as _bp
            cat = _bp.catalog_from_cache(host.s3_client, host.bucket) if host is not None and host.has('s3_client') else []
        except Exception:
            cat = []
    allowed = (user or {}).get('allowed_runs') if isinstance(user, dict) else None
    allowed = set(str(x) for x in allowed) if isinstance(allowed, list) else {'*'}
    out = []
    for j in cat:
        if not isinstance(j, dict):
            continue
        key = str(j.get('s3_key') or j.get('key') or '')
        if '*' not in allowed and key not in allowed:
            continue
        name = str(j.get('display_name') or j.get('subject') or j.get('name') or '').strip()
        if name:
            out.append({'name': name, 'key': key, 'created_at': str(j.get('created_at') or j.get('last_modified') or '')})
    out.sort(key=lambda r: r['created_at'], reverse=True)
    return out


def _twins(uname, host, user):
    try:
        from migration import digital_twins as DT
        s3 = host.s3_client if host is not None and host.has('s3_client') else None
        runs = DT.list_runs(s3)
        vis = DT.visible_runs(runs, user) if isinstance(user, dict) else []
        return [{'name': str(r.get('name') or r.get('subject') or ''), 'key': str(r.get('key') or ''), 'created_at': str(r.get('created_at') or '')}
                for r in vis if r.get('name') or r.get('subject')]
    except Exception:
        return []


_LISTERS = {'journey': _journeys, 'profile': _profiles, 'cut': _profiles, 'twin': _twins}


def _match(name, items):
    """The items whose name carries every token of the name (an exact
    name wins alone)."""
    want = _tokens(name)
    if not want:
        return []
    exact = [i for i in items if _tokens(i['name']) == want]
    if exact:
        return exact[:1]
    hits = []
    for i in items:
        have = set(_tokens(i['name']))
        if all(w in have for w in want):
            hits.append(i)
    return hits


def holdings(noun, uname, host, user, name=''):
    """{'items': [...], 'found': [...]}: what the seat holds of this
    artifact kind, and the ones a name points at. Fail-safe: empty."""
    lister = _LISTERS.get(noun)
    if not lister or host is None:
        return {'items': [], 'found': []}
    try:
        items = lister(uname, host, user)
    except Exception:
        traceback.print_exc()
        items = []
    return {'items': items, 'found': _match(name, items) if name else []}


def _running(noun, name, uname, host, user):
    """A job of this kind still running (or just finished) for the seat
    whose subject carries the name: {'status', 'subject'} or None."""
    if host is None or not name:
        return None
    kinds = {'journey': ('jiq',), 'profile': ('profile',), 'cut': ('profile',), 'twin': ('twin',), 'campaign': ('aiq',),
             'valuation': ('bpiq',), 'flywheel': ('fw',)}.get(noun) or ()
    if not kinds:
        return None
    try:
        from . import status_lane as _stl
        items = _stl._job_items(host, uname) + _stl._engine_runs(host, user or {'username': uname})
    except Exception:
        return None
    want = set(_tokens(name))
    for it in items:
        if it.get('kind') not in kinds:
            continue
        have = set(_tokens(it.get('subject') or ''))
        if want and want <= have:
            return {'status': str(it.get('status') or ''), 'subject': str(it.get('subject') or name)}
    return None


# ------------------------------------------------------------- directions

def _list_names(items, n=2):
    names = []
    for i in items:
        nm = str(i.get('name') or '').strip()
        if nm and nm not in names:
            names.append(nm)
        if len(names) >= n:
            break
    return names


def _noun_phrase(noun, plural=False):
    return {'journey': 'journey' + ('s' if plural else ''), 'profile': 'profile' + ('s' if plural else ''), 'cut': 'cut' + ('s' if plural else ''),
            'twin': 'twin' + ('s' if plural else ''), 'campaign': 'campaign' + ('s' if plural else ''), 'valuation': 'valuation' + ('s' if plural else ''),
            'flywheel': 'flywheel' + ('s' if plural else ''), 'tracker': 'tracker' + ('s' if plural else '')}.get(noun, noun)


def _build_chip(noun, name):
    return {'journey': f"Build a Digital Journey for {name}", 'profile': f"Run a profile on {name}", 'cut': f"Run a profile on {name}",
            'twin': f"Build a twin of {name}", 'campaign': f"Set up Attribution IQ for {name}", 'valuation': "Value a brand partnership",
            'flywheel': f"Build a Flywheel for {name}"}.get(noun, '')


def _access_lines(prod, status):
    label = prod['label']
    if status == 'hidden':
        return f"{label} is not on the dashboard right now; ask your admin to turn it back on under Live Features. {prod.get('blurb') or ''}".strip()
    if status == 'locked':
        return f"{label} is locked for your company right now; ask your admin. {prod.get('blurb') or ''}".strip()
    return f"{label} is not on your {M.DROPDOWN} menu yet; ask your admin for access. {prod.get('blurb') or ''}".strip()


def directions(target, *, access, hidden=None, locked=False, held=None, running=None, chat_only=None):
    """The reply for a target: (sentences, chips, action_hint). ``held`` is
    the holdings dict for an artifact target; ``running`` a running job
    for the named thing; ``chat_only`` the subjects of reads that landed
    in this chat and are NOT in the Digital Journey IQ registry (never
    claimed to be in the picker)."""
    kind = target.get('kind')
    if kind == 'rail':
        return [M.RAIL['line']], [], None
    if kind == 'dropdown':
        mine = [p['label'] for p in M.PRODUCTS if M.can_open(p['value'], access, hidden, locked=locked) == 'open' and p['value'] != 'admin']
        s = [f"The {M.DROPDOWN} dropdown is at the top right of the dashboard, the menu with the product name on it."]
        if mine:
            s.append("It lists every product on your seat: " + ', '.join(mine) + ".")
        s.append("Click a product to switch to it; the chat stays with you.")
        return s, [], None
    prod = target.get('product')
    if prod and prod.get('value') == M.ANALYSIS_IQ['value']:
        return [M.ANALYSIS_IQ['line']], [], None
    if kind == 'artifact' and not prod:
        noun = target.get('noun')
        return [f"Your {_noun_phrase(noun, plural=True) if noun not in ('export', 'email', 'deck', 'read', 'chat') else noun + ('s' if noun != 'chat' else 's')} "
                f"land {M.where_lands(noun)}."], [], None
    status = M.can_open(prod['value'], access, hidden, locked=locked)
    if status != 'open':
        return [_access_lines(prod, status)], [], None
    label = prod['label']
    chip = OPEN_CHIP.format(label=label)
    hint = {'view': prod['value'], 'label': chip}
    sents = []
    if kind == 'tab':
        tab = target['tab']
        hint['tab'] = tab['id']
        sents.append(f"Open {M.DROPDOWN_WHERE}, click {label}, then the {tab['label']} tab across the top of the page.")
        if prod.get('picker_line'):
            sents.append(prod['picker_line'])
        link = M.deep_link(prod['value'], tab=tab['id'])
    else:
        sents.append(f"Open {M.DROPDOWN_WHERE} and click {label}.")
        if prod.get('picker_line'):
            sents.append(prod['picker_line'])
        link = M.deep_link(prod['value'])
    noun = target.get('noun') if kind == 'artifact' else ''
    name = str(target.get('name') or '').strip()
    held = held or {}
    chat_only = [s for s in (chat_only or []) if s]
    picker = prod.get('picker') or 'the landing page'
    where = f"under {picker}" if picker.lower().startswith('select') else f"on {picker}" if 'tab' in picker else f"in {picker}"
    chips = [chip]
    if name and noun:
        found = held.get('found') or []
        if found:
            names = _list_names(found, 2)
            if len(names) == 1:
                sents.append(f"Your {names[0]} {_noun_phrase(noun)} is there {where}.")
            else:
                sents.append(f"Your {' and '.join(names)} {_noun_phrase(noun, plural=True)} are there {where}.")
        elif running and running.get('status') not in ('done', 'complete', 'completed', 'ready', 'error', 'failed'):
            sents.append(f"Your {running.get('subject') or name} {_noun_phrase(noun)} is still building; it appears there {where} the moment it lands.")
        elif noun == 'journey' and chat_only:
            # the read is in the chat and nowhere else: never "it is in the picker"
            sents.append(f"Your {chat_only[0]} read is in this chat, above, with its CSV link; it is not in Digital Journey IQ yet. "
                         f"Say '{ADD_CHIP}' and I will build the journey so it shows under Select a journey.")
            chips.append(ADD_CHIP)
        else:
            line = f"I do not see a {name} {_noun_phrase(noun)} there yet."
            bc = _build_chip(noun, name)
            if bc:
                line += f" Say '{bc}' and I will build it."
            sents.append(line)
    elif kind == 'product' and prod['value'] == 'journeyIQ' and chat_only:
        sents.append(f"Your {chat_only[0]} read is in this chat, above, with its CSV link; it is not in Digital Journey IQ yet. "
                     f"Say '{ADD_CHIP}' and I will build the journey so it shows under Select a journey.")
        chips.append(ADD_CHIP)
    elif kind == 'product' and held and held.get('items') and prod.get('picker'):
        pl = {'journeyIQ': 'journey', 'profileIQ': 'profile', 'digitalTwinsIQ': 'twin'}.get(prod['value'])
        names = _list_names(held['items'], 2)
        if pl and names:
            sents.append(f"Your newest {_noun_phrase(pl, plural=len(names) > 1)} there: {', '.join(names)}.")
    if link:
        sents.append(f"Direct link: {link}")
    return sents[:4], chips, hint


def answer(user, text, page=None, *, host=None, uname=None, history=None, access=None, hidden=None, locked=None, holdings_fn=None):
    """The wayfinding payload for a where-is question, or None when the
    words are not one. ``access``/``hidden``/``locked`` override the
    host's reads (tests); ``holdings_fn(noun, name)`` overrides the
    listings."""
    t = _clean(text)
    if not wants(t):
        return None
    uname = uname or str((user or {}).get('username') or '')
    order = add_order(t)
    if order:
        # 'Add it to Digital Journey IQ': a build order for the journey lane.
        # The words are rewritten to the build and the ask goes on; 'it' is
        # the newest read in this chat (or the thing last named).
        subj = order[0] if order[1] else _subject_from_history(history)
        if not subj:
            return {'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                    'reply': "Which read should I add to Digital Journey IQ? Name the title and I will build the journey so it shows under Select a journey.",
                    'wayfinding': {'kind': 'add_journey', 'product': 'journeyIQ', 'name': ''}}
        return {'success': True, 'action': 'rewrite', 'rewrite_text': f"Build a Digital Journey for {subj}", 'subject': subj,
                'wayfinding': {'kind': 'add_journey', 'product': 'journeyIQ', 'name': subj}}
    target = target_of(t)
    if not target:
        return None
    if access is None:
        access = M.access_for(user, host)
    if hidden is None:
        hidden = M.hidden_products(host)
    if locked is None:
        locked = False
        try:
            if host is not None and host.has('twins_locked'):
                locked = bool(host.twins_locked(user))
        except Exception:
            locked = False
    held, running, chat_only = None, None, []
    prod = target.get('product')
    if prod and M.can_open(prod['value'], access, hidden, locked=locked) == 'open':
        noun = target.get('noun') if target.get('kind') == 'artifact' else {'journeyIQ': 'journey', 'profileIQ': 'profile', 'digitalTwinsIQ': 'twin'}.get(prod['value'], '')
        name = str(target.get('name') or '')
        if noun in _LISTERS:
            try:
                held = holdings_fn(noun, name) if callable(holdings_fn) else holdings(noun, uname, host, user, name)
            except Exception:
                held = None
            if name and not (held or {}).get('found'):
                try:
                    running = None if callable(holdings_fn) else _running(noun, name, uname, host, user)
                except Exception:
                    running = None
        if noun == 'journey':
            # reads that landed in this chat and are not in the registry: said to be in the chat, never in the picker
            items = (held or {}).get('items') or []
            for r in chat_reads(history):
                subj = r['subject']
                if name and not (set(_tokens(name)) <= set(_tokens(subj))):
                    continue
                if _match(subj, items):
                    continue
                chat_only.append(subj)
                if len(chat_only) >= 2:
                    break
    sents, chips, hint = directions(target, access=access, hidden=hidden, locked=locked, held=held, running=running, chat_only=chat_only)
    if not sents:
        return None
    reply = ' '.join(s.strip() for s in sents if s and s.strip())
    out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips), 'offer_deck': False, 'deck_angle': None,
           'wayfinding': {'kind': target.get('kind'), 'product': (prod or {}).get('value') or '', 'name': str(target.get('name') or '')}}
    if hint:
        out['action_hint'] = hint
        out['open_view'] = hint['view']
    return out
