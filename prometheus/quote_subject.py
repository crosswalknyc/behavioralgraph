"""A quote names the subject it will read (Jenna 2026-11-13).

Samantha Kolton (CAA), 2026-11-04: "what IP (content, film, toys,
characters) are ram's fans very interested in?" was quoted "I can pull
that read on Ram's Fans. Pulling this data will cost $100." She
approved, and the read came back on Ram Trucks fans. She meant the Los
Angeles Rams. The quote had printed her own words back; which Ram the
read would be on was decided only after she approved.

The rule, held at the one place a quote is made
(``priced_read.offer_or_charge``), before anything is priced or
started:

  * a name that could be two or more different well-known things with
    different audiences (a truck brand and a football team), where the
    question's other words do not settle which, is asked about first:
    "Do you mean Ram Trucks (the pickup truck brand), or the Los Angeles
    Rams (the NFL team)?" with each as a chip. No price is shown and
    nothing is charged or started until it is answered. The pick re-runs
    the question bound to that subject (the which-one confirm the
    service already resolves), and the quote then names it;
  * a name that places cleanly is quoted under its plain name ("Ram
    Trucks fans", "Los Angeles Rams fans"), so the reader sees what the
    read will be on before Approve. The read runs on the subject that
    was quoted;
  * a subject that is already settled gets no extra question and no
    extra work: one the dashboard holds under that name, the profile on
    the reader's screen, the thread's own subject, one the reader just
    picked, a subject with several names in it, a demographic segment,
    everyone in the US.

Placing a name the dashboard does not hold is one small model call
(``resolve_name``): it returns the plain name and three to six words on
what it is, or the candidates when it is two things. It never invents
one; a name it cannot place is quoted as typed, as before. A test or a
replay never spends the call unless it hands a resolver in.

Pure but for that call. Never raises. Kill switch ``PM_QUOTE_SUBJECT=0``.
"""
from __future__ import annotations

import json
import os
import re

__all__ = ['enabled', 'split', 'settled_already', 'resolve_name', 'settle', 'ask_payload', 'pick_by_words']

_AUD_TAIL_RX = re.compile(
    r"\s+(audience|viewers|watchers|listeners|readers|players|fans|fan ?base|fanbase|followers|subscribers|buyers|shoppers|"
    r"users|customers|members|visitors|people|streamers|consumers|owners|drinkers|eaters|goers)\s*$", re.I)
_MULTI_RX = re.compile(r"\s(?:and|vs\.?|versus|x|or|plus|with)\s|[,/;]|\s&\s.+\s&\s", re.I)
_POSSESSIVE_RX = re.compile(r"(?:'|\u2019)s\b", re.I)
_STOP = frozenset("the a an of for on in at to and or one ones its it is this that these those my our your their i mean meant "
                  "actually not no yes please just was were be".split())
MAX_NAME_WORDS = 3
MAX_CANDIDATES = 4


def enabled():
    return str(os.environ.get('PM_QUOTE_SUBJECT', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(s):
    return ' '.join(str(s or '').split()).strip(" .,;:'\"")


def _fold(s):
    return re.sub(r"[^a-z0-9]+", '', _POSSESSIVE_RX.sub('', str(s or '').lower()).replace('&', 'and'))


def _stem(tok):
    t = _POSSESSIVE_RX.sub('', str(tok or '').lower())
    t = re.sub(r"[^a-z0-9]+", '', t)
    if len(t) > 3 and t.endswith('s') and not t.endswith('ss'):
        t = t[:-1]
    return t


def _toks(s):
    return [x for x in (_stem(w) for w in re.findall(r"[A-Za-z0-9&'\u2019+.-]+", str(s or ''))) if x]


def split(subject):
    """('Ram's', 'Fans') for "Ram's Fans"; ('Ram Trucks', '') for a bare
    name. The name keeps the reader's own spelling."""
    s = _clean(subject)
    m = _AUD_TAIL_RX.search(s)
    if not m:
        return s, ''
    return _clean(s[:m.start()]), m.group(1)


def _with_noun(name, noun):
    name = _clean(name)
    if not noun:
        return name
    n = noun if noun[:1].isupper() else noun.lower()
    return f"{name} {n}"


def _thread_names(history, page=''):
    out = []
    try:
        from . import subject_rules as _sr
        out = [str(x) for x in (_sr.thread_subjects(history, page) or []) if x]
    except Exception:
        out = []
    return out


def _picked_options(history):
    """Names the reader was offered in a which-one question on this
    thread: a subject that is one of them was picked, never asked twice."""
    out = []
    for t in (history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else (
            t.get('memory_confirm') if isinstance(t.get('memory_confirm'), dict) else {})
        for o in (mc.get('options') or []):
            if isinstance(o, dict):
                out += [str(o.get('subject') or ''), str(o.get('label') or '')]
    return [x for x in out if x]


def _on_record(name, ctx=None):
    """The dashboard's own spelling when it holds the name, else ''."""
    try:
        from . import priced_read as _pr
        return str(_pr._record_spelling(name, ctx) or '')
    except Exception:
        return ''


def settled_already(subject, text='', history=None, page='', ctx=None):
    """True when the subject needs no placing: several names, a segment,
    everyone, a long descriptive phrase, the page, the thread's subject,
    one the reader picked, or one the dashboard holds."""
    name, _noun = split(subject)
    if not name or name.lower() in ('that subject', 'this', 'that', 'it'):
        return True
    if _MULTI_RX.search(f" {name} ") or len(name.split()) > MAX_NAME_WORDS:
        return True
    try:
        from . import priced_read as _pr
        if _pr.is_everyone(name) or _pr.is_everyone(subject):
            return True
    except Exception:
        pass
    try:
        from . import segment_sizing as _seg
        if _seg.is_segment_label(_clean(subject)):
            return True
    except Exception:
        pass
    f, fs = _fold(name), _fold(subject)
    for known in [page] + _thread_names(history, page) + _picked_options(history):
        k = _fold(known)
        if k and k in (f, fs, _fold(split(known)[0])):
            return True
        if k and f == _fold(split(known)[0]):
            return True
    if _on_record(name, ctx):
        return True
    return False


_SYSTEM = (
    "You place a short name from a question for a US audience measurement desk. Return STRICT JSON only, no other "
    "text: {\"ambiguous\": bool, \"name\": str, \"what\": str, \"candidates\": [{\"name\": str, \"what\": str}]}.\n"
    "name: the plain name people use for the ONE thing the name refers to in this question, as short as it can be "
    "and still be unmistakable (\"Ram Trucks\", \"Los Angeles Rams\", \"Etsy\", \"Paige Spiranac\"). what: three to "
    "six plain words on what it is (\"the pickup truck brand\", \"the NFL team\").\n"
    "ambiguous is true ONLY when the name, as it is used in this question, could reasonably be two or more "
    "DIFFERENT well-known things whose audiences differ (a brand and a sports team, a person and a title that share "
    "a name, two shows with one title) AND the rest of the question does not settle which. Then put the two to four "
    "most likely in candidates, most likely first, each with its plain name and what it is; leave name empty. A name "
    "never carries brackets: when two candidates go by the same name, give both that name and let what tell them "
    "apart.\n"
    "When the question's own words settle it (a city, a league, a product word, 'the show', 'the trucks'), "
    "ambiguous is false and name is that one thing.\n"
    "A name with one obvious meaning is never ambiguous: do not go looking for obscure namesakes.\n"
    "When you cannot place the name at all, return {\"ambiguous\": false, \"name\": \"\", \"what\": \"\", "
    "\"candidates\": []}. Never invent an entity.")


def _parse(out):
    try:
        s = str(out or '')
        a, b = s.find('{'), s.rfind('}')
        if a < 0 or b <= a:
            return None
        d = json.loads(s[a:b + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def resolve_name(name, text, *, reason=None):
    """{'ambiguous', 'name', 'what', 'candidates'} for a name the
    dashboard does not hold, or None (no model at hand, a test, nothing
    usable back). ``reason(system, user)`` returns text."""
    try:
        if reason is None:
            if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_REPLAY_CHILD'):
                return None
            try:
                from claude_client import claude_reason_json as _crj
            except Exception:
                return None
            reason = lambda sys_, usr: _crj(system=sys_, user=usr, max_tokens=700, temperature=0.0,  # noqa: E731
                                            usage_tag=('analyze', 'quote-subject'))
        user = json.dumps({'name': _clean(name), 'question': ' '.join(str(text or '').split())[:600]})
        d = _parse(reason(_SYSTEM, user))
        if not d:
            return None
        cands = []
        for c in (d.get('candidates') or []):
            if isinstance(c, dict) and _clean(c.get('name')):
                cands.append({'name': _clean(c['name'])[:60], 'what': _clean(c.get('what'))[:60]})
        return {'ambiguous': bool(d.get('ambiguous')), 'name': _clean(d.get('name'))[:60],
                'what': _clean(d.get('what'))[:60], 'candidates': cands[:MAX_CANDIDATES]}
    except Exception:
        return None


def _plausible(name, typed):
    """A placed name is used only when it reads as a name and keeps a
    word of what was typed: 'Ram Trucks' for "Ram's", never a name that
    shares nothing with the reader's own."""
    n = _clean(name)
    if not n or len(n.split()) > 6 or re.search(r"[?!]|\d{5,}", n):
        return False
    a, b = set(_toks(typed)) - _STOP, set(_toks(n))
    return bool(a and a & b)


def _bare(name):
    """'Dark Matter (Pearl Jam)' -> 'Dark Matter': a name never carries brackets."""
    return _clean(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", '', str(name or ''))) or _clean(name)


def _what(c):
    return re.sub(r"^(?:the|a|an)\s+", '', _clean(c.get('what')), flags=re.I)


def _says(c):
    w = _what(c)
    return f"{_bare(c['name'])} (the {w})" if w else _bare(c['name'])


def ask_payload(text, candidates, noun=''):
    """The which-one question: every candidate named, each a chip, no
    price and no charge. It is the which-one confirm the service already
    resolves, so the pick re-runs the question bound to that subject."""
    cands = [c for c in (candidates or []) if isinstance(c, dict) and c.get('name')][:MAX_CANDIDATES]
    said = [_says(c) for c in cands]
    said = [re.sub(r"^The ", 'the ', s) for s in said]
    if len(said) == 2:
        line = f"Do you mean {said[0]}, or {said[1]}?"
    else:
        line = "Do you mean " + ', '.join(said[:-1]) + f", or {said[-1]}?"
    # the chip is the plain name; two things that go by one name carry what each is ("Dark Matter (Pearl Jam album)")
    bares = [_fold(_bare(c['name'])) for c in cands]
    opts = []
    for c in cands:
        base, w = _bare(c['name']), _what(c)
        label = f"{base} ({w})" if (bares.count(_fold(base)) > 1 and w) else base
        opts.append({'label': label, 'subject': _with_noun(label, noun), 'what': _clean(c.get('what'))})
    return {'success': True, 'action': 'answer', 'reply': line,
            'followups': [o['label'] for o in opts] + ['Something else'],
            'offer_deck': False, 'deck_angle': None, 'subject': '', 'subject_ambiguous': True,
            'memory_confirm': {'question': str(text or ''), 'options': opts}}


def settle(text, subject, history=None, *, panel_confirm=None, page='', ctx=None, resolve=None):
    """(subject to quote, ask payload or None).

    An accepted quote is never looked at again: the subject is the one
    that was quoted. A settled subject passes through untouched. A name
    that is two things returns the which-one question; a name that
    places cleanly returns its plain name with the reader's own audience
    word ("Ram Trucks Fans"); anything else returns the subject as it
    came."""
    subj = str(subject or '').strip()
    try:
        if not enabled() or isinstance(panel_confirm, dict) or not subj:
            return subj, None
        if settled_already(subj, text, history, page, ctx):
            return subj, None
        name, noun = split(subj)
        got = (resolve or resolve_name)(name, text)
        if not isinstance(got, dict):
            return subj, None
        cands = [c for c in (got.get('candidates') or []) if isinstance(c, dict) and _plausible(c.get('name'), name)]
        # the same thing under two spellings is one thing
        seen, uniq = set(), []
        for c in cands:
            k = (_fold(_bare(c['name'])), _fold(_what(c)) if _fold(_bare(c['name'])) == _fold(name) else '')
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        if got.get('ambiguous') and len(uniq) >= 2:
            return subj, ask_payload(text, uniq, noun)
        placed = got.get('name') or (uniq[0]['name'] if (got.get('ambiguous') and len(uniq) == 1) else '')
        if placed and _plausible(placed, name) and _fold(placed) != _fold(name):
            return _with_noun(placed, noun), None
        return subj, None
    except Exception:
        return subj, None


def pick_by_words(reply, options):
    """The option a typed reply points at when it is not a label: "the
    football team", "LA rams (the football team)", "the trucks". The one
    option whose name and description share more words with the reply
    than any other; None on a tie or no overlap."""
    try:
        words = set(_toks(reply)) - _STOP
        if not words:
            return None
        best, best_n, tie = None, 0, False
        for o in (options or []):
            if not isinstance(o, dict):
                continue
            ow = set(_toks(f"{o.get('label') or ''} {o.get('what') or ''}"))
            # initials stand for the name they open ("LA" for Los Angeles)
            initials = ''.join(w[:1] for w in re.findall(r"[A-Za-z]+", str(o.get('label') or '')))[:2].lower()
            n = len(words & ow) + (1 if initials and initials in words else 0)
            if n > best_n:
                best, best_n, tie = o, n, False
            elif n and n == best_n:
                tie = True
        return None if (tie or not best_n) else best
    except Exception:
        return None
