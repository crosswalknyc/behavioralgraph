"""A service-scoped ask that matches a live profile is the existing match (2026-11-12).

Jessie, verbatim: "run a profile on Viewers of GEORGIE & MANDY'S FIRST
MARRIAGE, viewers ONLY on Amazon and HBO MAX. Date range August 7 2026
through November 10 2026". The live profile "Georgie & Mandy's First
Marriage Viewers (HBO Max & Amazon Prime)" covers the same title, the
same two services and the same window, and the ask still drafted as a
new build at $300: the profile is named with the services in the other
order and "Amazon Prime" for "Amazon", so nothing the interpret step
compared read them as the same universe.

The comparison, and nothing wider:

  * the same title (the name before the audience noun; '&', 'And' and an
    apostrophe are spelling);
  * exactly the same set of services, in any order, each read through
    the one table of how a service is written
    (``migration.service_scope_sizing.canon_service``: Amazon / Amazon
    Prime / Prime Video are one, HBO Max / Max / HBOMax are one);
  * the same window (to within the three days the size rule already
    treats as one window).

All three hold: the ask is the existing profile, at no cost. Any one
differs: nothing here changes the draft's decision to a match. A
one-service profile is never the match for a two-service ask, or the
reverse: when the interpret step itself picked a profile whose name
carries a different set of services, the pick is put right (the profile
on the services asked, when one is held on that window; otherwise the
build as asked).

``services_of_name`` / ``services_named`` / ``services_asked`` are the
one reading of services for this comparison and for the saved-answer
lookup (prometheus.platform_scope, the catalog size lookup).

Pure functions over the draft and the catalog; the one outside read is a
profile's own window, from the top of its file. Never raises.
"""
from __future__ import annotations

import re
import time

__all__ = ['services_of_name', 'services_named', 'services_asked', 'services_in_question', 'services_of_label',
           'title_of_name', 'title_fold', 'window_of_draft', 'window_of_key', 'find_profile', 'settle', 'decide',
           'services_of_draft', 'bucket_for', 'replay_fits_services', 'whole_on_record', 'single_home',
           'home_bucket_for']

_NOUN_RX = re.compile(r"\b(?:viewers?|watchers?|streamers?|listeners?)\b", re.I)
_WIN_CACHE = {}
_WIN_TTL = 600


def _S():
    try:
        from migration import service_scope_sizing as s
    except Exception:
        import service_scope_sizing as s  # type: ignore
    return s


def services_named(labels) -> frozenset:
    """The services a list of names is, as one key each."""
    s = _S()
    return frozenset(k for k in ((_service_key(x) or s.canon_service(x)) for x in (labels or [])) if k)


def _service_key(part) -> str:
    """One service's key, or '' when the words are not a service."""
    try:
        s = _S()
        f = s._fold(part)
        if f in s._ALIASES:
            return s._ALIASES[f]
        from .platform_cuts import PLATFORM_ALIASES
        c = PLATFORM_ALIASES.get(' '.join(str(part or '').lower().split()))
        return s.canon_service(c) if c else ''
    except Exception:
        return ''


def services_of_name(name) -> frozenset:
    """The services a profile's own name carries after its audience noun
    ('X Viewers (HBO Max & Amazon Prime)', 'X Viewers ON HULU',
    'X Viewers - Paramount+'). Every word after the noun has to be a
    service: a cut's name ('... (HBO Max) - Avid Fan') carries none."""
    try:
        s = ' '.join(str(name or '').split())
        m = None
        for m in _NOUN_RX.finditer(s):
            pass
        if m is None:
            return frozenset()
        tail = re.sub(r"^\s*(?:only\s+)?(?:on\b|-|\(|:)?\s*", '', s[m.end():], flags=re.I).strip()
        tail = tail[:-1].strip() if tail.endswith(')') else tail
        if not tail:
            return frozenset()
        keys = [_service_key(p) for p in re.split(r"\s*(?:&|,|\band\b|/|\+(?=\s))\s*", tail) if p.strip()]
        return frozenset(keys) if keys and all(keys) else frozenset()
    except Exception:
        return frozenset()


def services_asked(text, history=None, title='') -> frozenset:
    """The services the words name as where the audience watches
    ("viewers ONLY on Amazon and HBO MAX", "watched it on Prime Video"),
    as one key each. A service the words only mention is not asked."""
    try:
        from . import named_platforms as N
        hit = N.from_thread(text, history, title)
        if not hit or hit.get('how') != 'audience':
            return frozenset()
        return services_named(hit.get('platforms'))
    except Exception:
        return frozenset()


def title_of_name(name) -> str:
    """The title inside an audience name: the words before the audience noun."""
    s = ' '.join(str(name or '').split())
    m = None
    for m in _NOUN_RX.finditer(s):
        pass
    return (s[:m.start()] if m else s).strip(' -,(')


def title_fold(title) -> str:
    """A title folded for comparison: case, punctuation, '&' and 'and' are spelling."""
    t = str(title or '').lower().replace("'", '').replace('\u2019', '')
    return ''.join(w for w in re.findall(r"[a-z0-9]+", t) if w != 'and')


def _win(a, b):
    a, b = str(a or '')[:10], str(b or '')[:10]
    return {'start': a, 'end': b} if re.match(r"^\d{4}-\d{2}-\d{2}$", a) and re.match(r"^\d{4}-\d{2}-\d{2}$", b) else None


def window_of_draft(draft, text=''):
    """The window the draft is on: its own dates, else the dates the
    words state, else the standing trailing year."""
    try:
        dr = (draft or {}).get('date_range')
        if isinstance(dr, dict) and _win(dr.get('start'), dr.get('end')):
            return _win(dr.get('start'), dr.get('end'))
        for v in (dr if isinstance(dr, str) else '', (draft or {}).get('engine_date_range')):
            m = re.findall(r"\d{4}-\d{2}-\d{2}", str(v or ''))
            if len(m) >= 2:
                return _win(m[0], m[1])
        if text:
            from . import inline_dates as D
            w = D.parse_inline_window(text)
            if w and _win(w.get('start'), w.get('end')):
                return _win(w.get('start'), w.get('end'))
        a, b = _S().window_of({})
        return _win(a.isoformat(), b.isoformat())
    except Exception:
        return None


def window_of_key(s3_key):
    """A profile's own window, read from the SAMPLE SIZE row at the top
    of its file; None when it cannot be read (then nothing matches)."""
    key = str(s3_key or '')
    hit = _WIN_CACHE.get(key)
    if hit and time.time() - hit[0] < _WIN_TTL:
        return hit[1]
    win = None
    try:
        import boto3
        body = boto3.client('s3', region_name='us-east-2').get_object(
            Bucket='dashboard-inputs', Key=key, Range='bytes=0-131071')['Body'].read().decode('utf-8', 'replace')
        m = re.search(r"(?m)^SAMPLE SIZE,\"?SAMPLE SIZE \((\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\)", body)
        if m:
            win = _win(m.group(1), m.group(2))
    except Exception:
        win = None
    _WIN_CACHE[key] = (time.time(), win)
    return win


def _same_window(a, b) -> bool:
    try:
        return bool(a and b) and _S()._same_window(a, b)
    except Exception:
        return False


def find_profile(title, asked, window, catalog, window_fn=None):
    """The catalog's profile of ``title`` on exactly ``asked`` services
    and ``window``; None when there is none. Newest first when several."""
    want = title_fold(title)
    if not want or not asked or not window:
        return None
    window_fn = window_fn or window_of_key
    hits = []
    for c in (catalog or []):
        if not isinstance(c, dict) or not c.get('s3_key'):
            continue
        name = str(c.get('display_name') or c.get('subject') or '')
        got = services_of_name(name)
        if not got or got != asked or title_fold(title_of_name(name)) != want:
            continue
        hits.append(c)
    hits.sort(key=lambda c: str(c.get('last_modified') or ''), reverse=True)
    for c in hits:
        if _same_window(window_fn(c.get('s3_key')), window):
            return c
    return None


def _draft_title(draft):
    try:
        from . import named_platforms as N
        return str(draft.get('content_show') or '').strip() or N._bare_title(draft.get('subject') or draft.get('name'))
    except Exception:
        return str((draft or {}).get('content_show') or '')


def _take(draft, c, note):
    disp = str(c.get('display_name') or c.get('subject') or c.get('s3_key'))
    draft['decision'] = 'existing_match'
    draft['existing_match_s3_key'] = c.get('s3_key')
    draft['existing_match_display_name'] = disp
    draft['existing_match_days_old'] = c.get('days_old')
    draft['existing_match_last_modified'] = c.get('last_modified')
    draft['subject'] = disp
    draft['name'] = disp
    draft.pop('file_stem', None)
    draft['decision_reason'] = note
    draft['service_scope_match'] = True
    return True, note


def services_of_draft(draft) -> frozenset:
    """The services a draft is already scoped to, read from the draft
    itself: the strict scope the binder wrote, else the services its own
    name carries after the audience noun. Empty when it names none."""
    try:
        if not isinstance(draft, dict):
            return frozenset()
        if draft.get('platform_scope_strict') and isinstance(draft.get('named_platforms'), list):
            got = services_named(draft.get('named_platforms'))
            if got:
                return got
        return services_of_name(draft.get('subject') or draft.get('name'))
    except Exception:
        return frozenset()


def decide(draft, text='', history=None, catalog=None, window_fn=None, cuts_hit=None):
    """THE comparison, for every surface (chat interpret, chat approve,
    the key's check and run, the local operator tool's default). Puts
    the draft's decision right for a title's audience on named services.
    Returns (acted, note). Mutates in place; never raises.

      * new build, and the catalog holds the same title on exactly the
        same services and window: the existing match;
      * a match or a refresh the interpret step picked on a profile
        whose name carries a different set of services: the profile on
        the services asked when one is held on that window, otherwise
        the build as asked.

    The services are read from the words when the surface has them
    (``text``, else the ask the draft carries), else from the draft's
    own scope. ``catalog`` may be the list or a callable that returns
    it (read only once the ask is seen to name services). A draft a
    reader already turned down the match for ("No, run what I asked")
    is left as they chose. Takes no instruction from the caller: there
    is nothing here a request can switch off or on."""
    try:
        if not isinstance(draft, dict) or cuts_hit or draft.get('platform_cuts'):
            return False, ''
        dec = str(draft.get('decision') or 'new_build').strip().lower()
        if dec not in ('new_build', 'existing_match', 'time_shifted_refresh'):
            return False, ''
        if dec == 'new_build' and draft.get('service_scope_match') and not draft.get('existing_match_s3_key'):
            return False, ''          # matched once, and the reader chose the build as asked
        title = _draft_title(draft)
        if not title:
            return False, ''
        text = text or str(draft.get('user_prompt') or '')
        asked = (services_asked(text, history, title) if text else frozenset()) or services_of_draft(draft)
        if not asked:
            return False, ''
        if callable(catalog):
            catalog = catalog()
        if not catalog:
            return False, ''
        window = window_of_draft(draft, text)
        if dec == 'new_build':
            c = find_profile(title, asked, window, catalog, window_fn)
            if c is None:
                return False, ''
            return _take(draft, c, 'the same title, the same services and the same window as a profile already in the library')
        picked = str(draft.get('existing_match_display_name') or '')
        have = services_of_name(picked)
        if not have or have == asked:
            return False, ''
        # the pick is on other services than the ask: never handed out for it
        c = find_profile(title, asked, window, catalog, window_fn)
        if c is not None:
            return _take(draft, c, 'the profile on the services asked, not the one on other services')
        for k in ('existing_match_s3_key', 'existing_match_display_name', 'existing_match_days_old', 'existing_match_last_modified'):
            draft.pop(k, None)
        draft['decision'] = 'new_build'
        draft['decision_reason'] = 'the profile on record is on other services than the ask names'
        return True, f"a profile on other services ({picked}) is not this ask's profile; built as asked"
    except Exception:
        return False, ''


def settle(draft, text, history=None, catalog=None, window_fn=None, cuts_hit=None):
    """The name the chat binder calls ``decide`` by."""
    return decide(draft, text, history, catalog, window_fn, cuts_hit)


# ---------------------------------------------------------------- saved answers
# The same reading of services for the saved-answer lookup (2026-11-12). A
# title held as several profiles, each on its own service or services, has
# a saved answer per scope. A question is answered from the scope it names:
# exactly those services, "Amazon" and "Prime Video" being one. A question
# that names no service is about the whole audience and is never answered
# with one service's figure.
#
# One exception, and only on evidence (2026-11-12): a title whose
# verified-carrier record shows it on exactly ONE US service for the
# window. There the one-service audience is the whole audience, so a
# question that names no service is answered from the one-service record,
# and the reply says which service is the title's home. Two or more
# services on the record, or no check on record, and nothing changes:
# unverified is never a single home.

_HOME_NAMES = {'amazon': 'Prime Video', 'hbomax': 'HBO Max', 'netflix': 'Netflix', 'youtube': 'YouTube', 'hulu': 'Hulu',
               'paramount': 'Paramount+', 'peacock': 'Peacock', 'disney': 'Disney+', 'appletv': 'Apple TV+',
               'mgmplus': 'MGM+', 'hallmarkplus': 'Hallmark+', 'youtubetv': 'YouTube TV', 'amcplus': 'AMC+',
               'discoveryplus': 'discovery+', 'tubi': 'Tubi', 'starz': 'Starz', 'philo': 'Philo'}
_HOME_MARK = 'only US home of'


def single_home(title, window, record=None) -> str:
    """The one US service ``title`` could be watched on in ``window`` by
    the verified-carrier record, as a service key; '' when the record
    shows several, holds no check, or the check does not cover the
    window. Never raises."""
    try:
        return str(_S().single_home(title, window, record=record) or '')
    except Exception:
        return ''


def home_name(service_key) -> str:
    """A service's name as a reader knows it."""
    k = str(service_key or '')
    return _HOME_NAMES.get(k) or k.title()


def home_line(title, service_key) -> str:
    """The plain line that says what a one-service figure covers when
    that service is the title's only US home."""
    return (f"{home_name(service_key)} is the {_HOME_MARK} {str(title or 'the title').strip()} in this window, "
            f"so this is the title's whole audience.")


def with_home_line(entry, title, service_key):
    """A saved answer, leading with the line that names the title's home
    (a copy; the saved answer itself is not changed). The same entry
    when it already says so."""
    try:
        reply = str(entry.get('reply') or '')
        if not reply or _HOME_MARK in reply or reply.startswith('Profile IQ profile'):
            return entry
        out = dict(entry)
        out['reply'] = home_line(title, service_key) + '\n\n' + reply
        out['home_service'] = service_key
        return out
    except Exception:
        return entry


def _entry_window(entry):
    """The window a saved answer is on: its own, else the window of the
    profile a delivered-profile record names. None when neither is known."""
    try:
        w = _win(entry.get('ws'), entry.get('we'))
        if w:
            return w
        m = re.search(r"Profile IQ profile\s+(.+?\.csv)\s*$", str(entry.get('reply') or '').strip())
        if m:
            return window_of_key(m.group(1).strip())
    except Exception:
        pass
    return None


def home_of_entry(entry, bucket):
    """(service key, title) when the saved answer is filed on one
    service and that service is the title's only US home for the
    answer's window; ('', title) otherwise."""
    try:
        label = str((bucket or {}).get('subject') or '')
        title = title_of_name(re.sub(r"\s*\([^()]*\)\s*$", '', label)) or label
        have = services_of_label(entry.get('subject')) or _bucket_services(bucket or {})
        if len(have) != 1:
            return '', title
        home = single_home(title, _entry_window(entry))
        return (home if home and home in have else ''), title
    except Exception:
        return '', ''


_LABEL_PAREN_RX = re.compile(r"\(([^()]*?)\s+(?:viewers?|watchers?|streamers?|listeners?)\b[^()]*\)", re.I)
_LEAD_RX = re.compile(r"^\s*(?:on\s+)?(.+?)\s+(?:viewers?|watchers?|streamers?|listeners?)\b", re.I)
_SPLIT_RX = re.compile(r"\s*(?:&|,|\band\b|\bor\b|/|\+(?=\s))\s*", re.I)
_ON_TAIL_RX = re.compile(r"\b(?:on|across|via|through)\s+((?:the\s+)?[^?.!;]+)", re.I)


def _keys_of_list(text) -> frozenset:
    keys = [_service_key(p.strip(" .?!'\"")) for p in _SPLIT_RX.split(str(text or '')) if p.strip(" .?!'\"")]
    return frozenset(keys) if keys and all(keys) else frozenset()


def services_of_label(label) -> frozenset:
    """The services a saved answer's label is on, in any of the shapes a
    label takes: 'X Viewers (Paramount+)', 'X Viewers - Paramount+',
    'X (HBO Max viewers, all 3 seasons)', 'X (HBO Max and Amazon Prime
    Video viewers)', or a universe line ('Paramount+ viewers in the US').
    Empty when the label is not a title on named services."""
    try:
        s = ' '.join(str(label or '').split())
        got = services_of_name(s)
        if got:
            return got
        m = _LABEL_PAREN_RX.search(s)
        if m:
            got = _keys_of_list(m.group(1))
            if got:
                return got
        if '(' not in s:
            m = _LEAD_RX.match(s)
            if m:
                return _keys_of_list(m.group(1))
    except Exception:
        pass
    return frozenset()


def services_in_question(text, known=frozenset()) -> frozenset:
    """The services a question is about, as one key each: the ones it
    names as where the audience watches, the ones it scopes to ('the X
    audience on HBO Max'), and a service the title is known to be held
    on written loosely after 'on' ('on Amazon and Max')."""
    got = set(services_asked(text))
    try:
        from . import platform_scope as PS
        for plat, how in PS.named(text).items():
            if how in ('scope', 'subject'):
                k = _service_key(plat)
                if k:
                    got.add(k)
    except Exception:
        pass
    if known:
        for m in _ON_TAIL_RX.finditer(str(text or '')):
            tail = re.split(r"\b(?:from|between|in the|over the|during|since|for the)\b", m.group(1), maxsplit=1, flags=re.I)[0]
            for part in _SPLIT_RX.split(tail):
                k = _service_key(part.strip(" .?!'\""))
                if k and k in known:
                    got.add(k)
    return frozenset(got)


def _bucket_services(v) -> frozenset:
    got = services_of_label(v.get('subject'))
    if got:
        return got
    for a in (v.get('aliases') or []):
        got = services_of_label(a)
        if got:
            return got
    return frozenset()


def _bucket_title(v) -> str:
    label = str(v.get('subject') or '')
    return title_fold(title_of_name(re.sub(r"\s*\([^()]*\)\s*$", '', label)))


def _title_buckets(index_doc, question, live=None) -> dict:
    """{bucket key: (bucket, services)} for the live buckets of a title the question names whole."""
    subjects = (index_doc or {}).get('subjects') or {}
    q = title_fold(question)
    out = {}
    for k, v in subjects.items():
        if not isinstance(v, dict) or (live is not None and not live(v)):
            continue
        t = _bucket_title(v)
        if len(t) >= 8 and t in q:
            out[k] = (v, _bucket_services(v))
    return out


def bucket_for(index_doc, skey, question, live=None):
    """The bucket of a title on exactly the services the question names,
    when the title is held on named services. Returns {'key', 'subject',
    'services'}; {'key': None, 'refuse': True} when the title is held on
    named services and none is the set asked (nothing on record answers
    it); or None (the lookup stays where it resolved)."""
    try:
        fam = _title_buckets(index_doc, question, live)
        scoped = {k: sv for k, (v, sv) in fam.items() if sv}
        if not scoped:
            return None
        known = frozenset().union(*scoped.values())
        asked = services_in_question(question, known)
        if not asked:
            return None
        for k, sv in scoped.items():
            if sv == asked:
                return {'key': k, 'subject': fam[k][0].get('subject'), 'services': sorted(sv)}
        # the title is held on named services and none of them is this set: nothing on record answers it,
        # neither another set's figure nor a platform's own audience
        return {'key': None, 'refuse': True, 'services': sorted(asked)}
    except Exception:
        pass
    return None


def home_bucket_for(index_doc, question, live=None):
    """The bucket of a title that is filed on one service, for a
    question that names the title whole and no service, when that
    service is the title's only US home on the verified-carrier record.
    The bucket's key, or None. (Whether a given saved answer's window is
    covered is judged per answer, in ``replay_fits_services``.)"""
    try:
        fam = _title_buckets(index_doc, question, live)
        scoped = {k: sv for k, (v, sv) in fam.items() if sv}
        if len(scoped) != 1:
            return None
        key, sv = next(iter(scoped.items()))
        if len(sv) != 1 or services_in_question(question, sv):
            return None
        label = str(fam[key][0].get('subject') or '')
        title = title_of_name(re.sub(r"\s*\([^()]*\)\s*$", '', label)) or label
        c = (_S().load_record(title) or {}).get('carriers')
        if isinstance(c, dict) and frozenset(c.get('services') or []) == sv:
            return key
    except Exception:
        pass
    return None


def _is_platform_label(label) -> bool:
    try:
        from . import platform_scope as PS
        return bool(PS.canon(label)) or bool(_service_key(label))
    except Exception:
        return False


def replay_fits_services(entry, bucket, question, subject=''):
    """(fits, why). A saved answer on named services answers a question
    about exactly those services, and only that. The services are the
    ones the question names, or the ones the profile the reader is on
    carries (``subject``: 'X Viewers (Paramount+)'). Applies to a
    title's saved answers; a platform's own audience is not judged here.
    Fails open: (True, '') whenever the scope cannot be read."""
    try:
        if not isinstance(entry, dict):
            return True, ''
        label = str((bucket or {}).get('subject') or '')
        if _is_platform_label(label) or not _bucket_title(bucket or {}):
            return True, ''
        if entry.get('journey'):
            return True, ''                      # a viewing chain carries its own platform and is matched on it
        # the scope is the one the answer is filed under (its own subject, or the bucket's label and names);
        # a service that only appears in a read's universe line is not a filing
        have = services_of_label(entry.get('subject')) or _bucket_services(bucket or {})
        if not have:
            return True, ''
        qn = re.sub(r"[^a-z0-9]+", ' ', str(question or '').lower()).strip()[:300]
        if qn and qn == str(entry.get('qn') or ''):
            return True, ''                      # the question this answer was given to
        asked = services_in_question(question, have) or services_of_label(subject)
        if asked == have:
            return True, ''
        if not asked:
            home, _title = home_of_entry(entry, bucket)
            if home:
                return True, 'home:' + home           # the one service is the title's only US home: its audience is the whole audience
            return False, 'the saved answer is one service\'s figure and the question names no service'
        return False, 'the saved answer is on other services than the question names'
    except Exception:
        return True, ''


def whole_on_record(title, window, record=None):
    """The title's whole audience on record for ``window`` (every
    service), as {'people', 'window'}; None when none is held on that
    window. The record is the one every service-scoped build is read
    against (system/title_service_sizes.json)."""
    try:
        rec = record if record is not None else _S().load_record(title)
        whole = (rec or {}).get('whole') if isinstance(rec, dict) else None
        if not isinstance(whole, dict) or int(whole.get('people') or 0) <= 0:
            return None
        if not _same_window(whole.get('window') or {}, window or {}):
            return None
        return {'people': int(whole['people']), 'window': dict(whole.get('window') or {})}
    except Exception:
        return None
