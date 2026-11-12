"""A genre is a cut, never the subject (Jenna 2026-11-11: "these errors
cannot happen").

Armine asked, in a fresh chat: "Looking at YouTube. What type of
categories do best for digital original content: Comedy, lifestyle/arts
& culture, Faith, Young Adult, Drama, and Horror do in this space.?"
The reply was "I can pull that read on Comedy. $100." On another run the
same words were read against the Horror Streaming Enthusiasts profile.

Which one she got depended on the model's reading of the words, twice
over: the reading lists the six categories as six subjects (sometimes
matching one to a library profile that has the word in its name), and
the one binder took the first of them as the subject of the ask
(`subject_rules.bind`, step 6). When the reading called the ask an
analysis, the pricing gate quoted it and its own platform rule could
correct the name; when the reading called it a comparison, the pricing
gate stood aside, the read step quoted it, and the read step's last
line before the quote (`display_for`) asked the binder again and put
"Comedy" back over the subject it had already been handed.

The rule here does not depend on the reading:

  * `items(text)`: the categories a question lists (three or more genre
    words in a list, or two where the words say "categories" or
    "genres"). Words, punctuation and a fixed vocabulary; no model.
  * `is_cut(name, text)`: `name` is one of those items, or a library
    profile that was only reached through one ("Horror Streaming
    Enthusiasts" when the words say "Horror" and never name that
    profile).
  * `record(text, history)`: the subject on record for the words: the
    read store's subject the words name (a platform the words give is
    where it happens), else the thread's subject.
  * `settle(text, history, candidate, held)`: the subject a quote or a
    read may carry. A candidate that is a cut, or a platform the words
    only give as where, yields to the subject already held, then to the
    subject on record; with neither it is '' and the caller asks.
  * `gate(...)`: the door's step, ahead of every lane: a question that
    lists categories and names no subject of its own is bound to the
    subject on record, or asked which audience it is on.

`quote_name` is the plain spelling of a stored subject for the reader:
the name, the platform once, the cut once.

Nothing here raises into a request.
"""
from __future__ import annotations

import re

__all__ = ['GENRES', 'clean_reading', 'items', 'is_item', 'via_item', 'is_cut', 'record', 'settle', 'hint', 'gate', 'quote_name',
           'lists_categories']

# the vocabulary a list of categories is made of (normalised: lower case, '&', '/' and '-' read as spaces)
_SINGLE = frozenset("""
comedy comedies drama dramas horror faith christian spirituality ya teen kids family lifestyle arts culture
romance romcom thriller thrillers suspense action adventure documentary documentaries docuseries reality
unscripted scripted scifi fantasy anime animation animated news sports music gaming crime mystery western
musical musicals biography history talk variety food cooking travel beauty fashion fitness wellness health
tech technology education educational diy pets entertainment politics business finance sketch dramedy
paranormal supernatural lgbtq holiday nature science automotive autos podcasts vlogs asmr unboxing pranks
challenges reactions tutorials
""".split())
_PHRASES = frozenset((
    'young adult', 'faith based', 'kids family', 'arts culture', 'romantic comedy', 'rom com', 'sci fi',
    'science fiction', 'true crime', 'game show', 'game shows', 'how to', 'stand up', 'action adventure', 'home diy'))
GENRES = _SINGLE | _PHRASES
_CATEGORY_NOUN_RX = re.compile(r"\b(?:categor(?:y|ies)|genres?|verticals?|formats?|content\s+types?|types?\s+of\s+(?:content|"
                               r"shows?|videos?|series|programming|categor(?:y|ies)))\b", re.I)
_AUDIENCE_NOUNS = frozenset(('fans', 'fan', 'viewers', 'viewer', 'lovers', 'watchers', 'audience', 'audiences',
                             'enthusiasts', 'buyers', 'readers', 'listeners', 'players', 'people', 'subscribers',
                             'shoppers', 'consumers', 'customers', 'users', 'followers', 'households'))
_SENTENCE_RX = re.compile(r"(?<=[.?!])\s+|\n+")


def _norm(s):
    s = str(s or '').lower().replace('&', ' ').replace('/', ' ').replace('-', ' ')
    s = re.sub(r"[^a-z0-9 ]+", ' ', s)
    return ' '.join(w for w in s.split() if w != 'and')


def _is_genre(phrase):
    """True when every word of the phrase is spent on genre terms
    ("lifestyle/arts & culture", "Young Adult")."""
    words = _norm(phrase).split()
    if not words or len(words) > 6:
        return False
    n = len(words)
    ok = [False] * (n + 1)
    ok[0] = True
    for i in range(n):
        if not ok[i]:
            continue
        for k in (1, 2):
            if i + k > n:
                break
            seg = ' '.join(words[i:i + k])
            if (k == 1 and seg in _SINGLE) or (k == 2 and seg in _PHRASES):
                ok[i + k] = True
    return ok[n]


def _chunk_item(chunk, first):
    """The genre a list chunk carries, or ''. The first chunk of a list
    still has the sentence in front of it ("... content: Comedy") and
    the last has the sentence behind it ("Horror do in this space")."""
    c = re.sub(r"^\s*(?:and|or)\s+", '', str(chunk or ''), flags=re.I).strip(" \t.?!;:\"'()")
    if not c:
        return ''
    if ':' in c:
        c = c.rsplit(':', 1)[1].strip()
    c = re.sub(r"^(?:such as|like|including|e\.?g\.?|for example|namely)\s+", '', c, flags=re.I).strip()
    if not c:
        return ''
    if _is_genre(c):
        return c
    words = c.split()
    # the list's last item with the sentence running on behind it
    for k in range(min(5, len(words) - 1), 0, -1):
        head, nxt = ' '.join(words[:k]), words[k]
        if _is_genre(head) and nxt[:1].islower() and nxt.lower().strip('.,?!') not in _AUDIENCE_NOUNS:
            return head
    # the list's first item with the sentence in front of it
    if first:
        for k in range(min(5, len(words) - 1), 0, -1):
            tail, prev = ' '.join(words[-k:]), words[-k - 1]
            if _is_genre(tail) and (prev[:1].islower() or prev.endswith(':')):
                return tail
    return ''


def items(text):
    """The categories the words list, as written, in order. [] when the
    words do not list categories."""
    try:
        t = str(text or '')
        if len(t) > 4000 or (',' not in t and not re.search(r"\b(?:and|or)\b", t, re.I)):
            return []
        best = []
        for sent in _SENTENCE_RX.split(t):
            if not sent.strip():
                continue
            chunks = []
            for part in sent.split(','):
                chunks.extend(re.split(r"\s+(?:and|or|vs\.?|versus)\s+(?![^()]*\))", part, flags=re.I))
            run, runs = [], []
            for ch in chunks:
                if not ch.strip(" \t.?!;:"):
                    continue             # ", and Horror": the comma and the "and" are one separator
                it = _chunk_item(ch, first=not run)
                if it:
                    run.append(it)
                else:
                    if run:
                        runs.append(run)
                    run = []
            if run:
                runs.append(run)
            for r in runs:
                if len(r) > len(best):
                    best = r
        if len(best) >= 3 or (len(best) >= 2 and _CATEGORY_NOUN_RX.search(t)):
            return best
        return []
    except Exception:
        return []


def lists_categories(text):
    return bool(items(text))


def is_item(name, text):
    """`name` is one of the categories the words list."""
    try:
        n = _norm(name)
        if not n:
            return False
        its = [_norm(i) for i in items(text)]
        if not its:
            return False
        if n in its:
            return True
        # one word of a compound item ("lifestyle" of "lifestyle/arts & culture"), or several items read as one
        pool = set()
        for i in its:
            pool.update(i.split())
        return _is_genre(name) and all(w in pool for w in n.split())
    except Exception:
        return False


def via_item(name, text):
    """`name` is a longer name (a library profile) that the words never
    say and that shares a listed category's word: it was reached through
    the category, not named."""
    try:
        n = _norm(name)
        if not n or len(n.split()) < 2:
            return False
        its = [_norm(i) for i in items(text)]
        if not its or n in its:
            return False
        qn = f" {_norm(text)} "
        if f" {n} " in qn:
            return False                 # the words name it outright
        padded = f" {n} "
        shared = [i for i in its if f" {i} " in padded]
        if not shared:
            return False
        # a stored subject the words spell out (its own name is in the question, categories and all) is that
        # subject; a profile whose other words the question never says was only reached through the category
        spent = set(w for i in shared for w in i.split())
        rest = [w for w in n.split() if w not in spent and len(w) > 2 and w not in ('the', 'and', 'for', 'with')]
        if not rest:
            return True
        said = sum(1 for w in rest if f" {w} " in qn)
        return said * 2 < len(rest)
    except Exception:
        return False


def is_cut(name, text):
    return is_item(name, text) or via_item(name, text)


def _scope_platform(name, text):
    """`name` is a platform the words only give as where."""
    try:
        from . import platform_scope as _ps
        p = _ps.canon(str(name or '').split(' - ')[0])
        return bool(p) and _ps.named(text).get(p) == 'scope'
    except Exception:
        return False


# ---------------------------------------------------------------- the subject on record
def _ledger_by_words(text, ledger):
    """The read store's subject the words name when they give no
    platform: three or more of its own words, most of its name."""
    try:
        from . import platform_scope as _ps
        doc = ledger._load_index() if ledger is not None else None
        subjects = (doc or {}).get('subjects') or {}
        live = getattr(ledger, '_has_live_entries', None)
        qwords = set(_ps._words(text))
        best, best_score = None, None
        for k, v in subjects.items():
            if not isinstance(v, dict):
                continue
            subj = str(v.get('subject') or k.replace('_', ' '))
            if _ps.canon(subj) or (live is not None and not live(v)):
                continue
            n, frac, chars = _ps._coverage(subj, qwords)
            if n < 3 or frac < 0.6 or chars < 14:
                continue
            scoped = bool(_ps.scope_of(subj) or [p for p in _ps.named(subj)])
            score = (0 if scoped else 1, frac, n)      # the words give no platform: the unscoped subject first
            if best_score is None or score > best_score:
                best, best_score = {'subject': subj, 'bucket': k, 'source': 'ledger', 'cohort': '', 'platform': ''}, score
        return best
    except Exception:
        return None


def record(text, history=None, ledger=None):
    """{'subject', 'source', 'cohort', 'platform'} for the subject on
    record the words are about, or None."""
    try:
        from . import platform_scope as _ps
        if ledger is None:
            import insights_ledger as ledger
        scoped = [p for p, how in _ps.named(text).items() if how == 'scope']
        if scoped:
            hit = _ps.for_read(text, history, None, ledger, '', '')
            if hit and hit.get('subject') and not is_item(hit['subject'], text) and not _ps.canon(str(hit['subject'])):
                return {'subject': str(hit['subject']), 'source': str(hit.get('source') or ''), 'cohort': str(hit.get('cohort') or ''),
                        'platform': str(hit.get('platform') or '')}
        else:
            hit = _ledger_by_words(text, ledger)
            if hit and not is_item(hit['subject'], text):
                return hit
        ts = _ps._thread_subject(history)
        if ts and not _ps.canon(ts.split(' - ')[0]) and not is_item(ts, text) and not _is_genre(ts):
            return {'subject': ts, 'source': 'thread', 'cohort': (f"on {scoped[0]}" if scoped and scoped[0].lower() not in ts.lower() else ''),
                    'platform': scoped[0] if scoped else ''}
    except Exception:
        pass
    return None


def settle(text, history, candidate, held='', ledger=None):
    """(subject, asked). The subject a quote or a read may carry for these
    words. `candidate` is what a lane has in hand; `held` is a subject
    the reader already chose or was quoted. Unchanged when the words
    list no categories or the candidate is a subject of its own."""
    c = str(candidate or '').strip()
    try:
        if not items(text):
            return c, False
        h = str(held or '').strip()
        if h and (is_item(h, text) or _is_genre(h) or _scope_platform(h, text)):
            h = ''
        if c and not is_cut(c, text) and not _scope_platform(c, text):
            # a subject of its own, unless it is only a piece of the stored subject's name ("Digital Original
            # Content" when the words spell out the stored subject in full): then the stored subject
            rec = record(text, history, ledger) if not h else None
            if rec and _part_of(c, rec['subject']):
                return rec['subject'], False
            return c, False
        if h:
            return h, False
        rec = record(text, history, ledger)
        if rec:
            return rec['subject'], False
        return '', True
    except Exception:
        return c, False


def _part_of(name, whole):
    a, b = _norm(name).split(), set(_norm(whole).split())
    return bool(a) and len(a) < len(b) and all(w in b for w in a)


def hint(text, name):
    """A lane's starting guess, with a cut taken out ('' then): a guess
    that is one of the listed categories names no subject."""
    n = str(name or '').strip()
    try:
        if n and items(text) and (is_cut(n, text) or _scope_platform(n, text)):
            return ''
    except Exception:
        pass
    return n


def clean_reading(reading, text):
    """The Reader's reading with the listed categories taken out of its
    subjects (kept under 'cuts'): nothing downstream of the door can
    take one, or a library profile matched to one, as a subject. The
    reading is returned as it came when the words list no categories or
    none of its subjects is one."""
    try:
        if not isinstance(reading, dict):
            return reading
        its = items(text)
        subs = reading.get('subjects')
        if not its or not isinstance(subs, list):
            return reading
        keep, cut = [], []
        for sd in subs:
            if isinstance(sd, dict) and (is_item(sd.get('as_written'), text)
                                         or (sd.get('catalog') and is_cut(sd.get('catalog'), text))
                                         or (not sd.get('as_written') and is_cut(sd.get('catalog'), text))):
                cut.append(sd)
            else:
                keep.append(sd)
        if not cut:
            return reading
        out = dict(reading, subjects=keep, cuts=[str(i) for i in its], cut_subjects=cut)
        if len(keep) < 2 and isinstance(out.get('flags'), list):
            out['flags'] = [f for f in out['flags'] if f != 'multiple_subjects']
        return out
    except Exception:
        return reading


# ---------------------------------------------------------------- the door
def gate(text, history, ctx, body, reading=None):
    """The door's step for a question that lists categories. None when
    the step does not apply (no list, a subject already chosen, or the
    words name a subject of their own); {'bind': subject, 'cohort': ..}
    when the subject on record is the subject; {'ask': True} when there
    is none and the reader is asked which audience."""
    try:
        if not items(text):
            return None
        body = body if isinstance(body, dict) else {}
        extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
        if str(body.get('bind_subject') or extra.get('bind_subject') or '').strip():
            return None
        if isinstance(body.get('panel_confirm'), dict) or isinstance(extra.get('panel_confirm'), dict):
            return None
        from . import subject_rules as _sr
        page = _sr._page_name(ctx)
        s = _sr.bind(text, history, page, reading=reading if isinstance(reading, dict) else {})
        if s and s.source == 'words' and s.name:
            if (s.qualifiers or {}).get('on_record'):
                coh = str((s.qualifiers or {}).get('record_cohort') or '')
                plat = re.sub(r"^on\s+", '', coh, flags=re.I).strip()
                if plat and plat.lower() in s.name.lower():
                    coh = ''             # the stored name already says where
                return {'bind': s.name, 'cohort': coh, 'source': 'record'}
            return None                  # the words name a subject of their own
        if s and s.source == 'thread' and s.name:
            return {'bind': s.name, 'cohort': '', 'source': 'thread'}
        if s and s.source == 'page' and s.name:
            return {'bind': s.name, 'cohort': '', 'source': 'page'}     # the words point at the open page
        return {'ask': True}
    except Exception:
        return None


# ---------------------------------------------------------------- the reader's spelling
_TRAIL_PAREN_RX = re.compile(r"\s*\((?P<inner>[^()]*)\)\s*$")
_CATEGORIES_WORD_RX = re.compile(r"\s+categor(?:y|ies)\b", re.I)


def quote_name(subject):
    """A stored subject the way a quote says it: the name, the platform
    once, the cut once ("Digital original content on YouTube, by category
    (Comedy, Drama and Horror)"). Unchanged when the stored name carries
    no list of categories. What is priced and what the Approve carries
    back is the stored subject, untouched."""
    s = ' '.join(str(subject or '').split())
    try:
        m = _TRAIL_PAREN_RX.search(s)
        if not m:
            return s
        cuts = [c.strip() for c in m.group('inner').split(',') if c.strip()]
        if len(cuts) < 2 or sum(1 for c in cuts if _is_genre(c)) < 2:
            return s
        body = s[:m.start()].strip()
        from . import platform_scope as _ps
        plat = ''
        for p in _ps.named(body):
            pm = re.search(r"\s+(?:on|in|across)\s+" + re.escape(p) + r"\b", body, re.I) or \
                re.search(r"(?<![\w+])" + re.escape(p) + r"(?:'s)?\s+", body, re.I)
            if pm:
                plat = p
                body = (body[:pm.start()] + ' ' + body[pm.end():]).strip()
                break
        head = ' '.join(_CATEGORIES_WORD_RX.sub('', body).split()).strip(' ,;:-')
        if not head:
            return s
        lst = ', '.join(cuts[:-1]) + ' and ' + cuts[-1]
        return f"{head}" + (f" on {plat}" if plat else '') + f", by category ({lst})"
    except Exception:
        return s
