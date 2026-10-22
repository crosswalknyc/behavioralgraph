"""The entity behind an explicit build order (2026-10-21).

Jessie: "run a profile for Listerners of Ball in the Bonzo Family with
Lonzo Ball" (open profile none; two slips; the show is "Ball in the
Family Podcast with Lonzo & Gelo Ball"). The build-entity gate answered
"I read that as a task, not a profile to build" because the drafted
subject, seven words long, tripped the fragment detector, and nothing
tried to resolve what she typed against what the dashboard already
knows. An explicit build verb plus any noun phrase is a build order; the
only question allowed back is WHICH entity, when two resolve equally.

This module does the resolving, typo-tolerant and scope-aware:

  split_scope("Listeners of Ball in the Family")
      -> ("Ball in the Family", "listeners")
  strip_credit("Ball in the Family Podcast with Lonzo & Gelo Ball")
      -> "Ball in the Family Podcast"
  resolve("Ball in the Bonzo Family with Lonzo Ball",
          page="", catalog=[...names...], titles=[...], brands=[...])
      -> {"display": "Ball in the Family Podcast with Lonzo & Gelo Ball",
          "source": "catalog", "scope": "", "candidates": [...],
          "ambiguous": False}

Matching is token coverage with per-token fuzz (a slip of one or two
letters still matches: "bonzo" ~ "lonzo", "mountan" ~ "mountain"),
measured both ways so a long typed phrase cannot claim a short name on
one shared word. The page name, the catalog, the content map's titles
and the hostmap's brands are the candidate pools, in that order of
trust. No network, no model: the caller hands in the pools it has; an
empty pool is simply skipped.
"""
from __future__ import annotations

import difflib
import re

# consumer nouns that scope a universe (listeners of X -> X, listeners)
_SCOPE_NOUNS = ('listeners', 'listener', 'viewers', 'viewer', 'watchers', 'readers', 'reader',
                'players', 'player', 'gamers', 'fans', 'fan', 'subscribers', 'subscriber',
                'buyers', 'buyer', 'shoppers', 'shopper', 'customers', 'customer', 'users', 'user',
                'attendees', 'attendee', 'streamers', 'followers', 'follower', 'audience', 'engagers')
# the slips people type for the nouns (Jessie: Listerners)
_SCOPE_FUZZ = 0.8
_LEAD_SCOPE_RX = re.compile(
    r"^\s*(?:the\s+)?(?:(?:only\s+)?(?P<noun>[A-Za-z]{4,14})\s+(?:of|for)\s+)(?P<rest>.+)$", re.I)
_TAIL_SCOPE_RX = re.compile(
    r"^(?P<rest>.+?)\s+(?P<noun>[A-Za-z]{4,14})(?:\s+only)?\s*$", re.I)
_TYPE_NOUN_RX = re.compile(r"^\s*(?:the\s+)?(?:podcast|show|series|film|movie|book|game|brand|channel)\s+(?:called|named|titled)?\s*", re.I)
# a host / credit suffix is not part of the entity's name
_CREDIT_RX = re.compile(
    r"\s+(?:with|featuring|feat\.?|ft\.?|hosted by|presented by|starring|w/)\s+[A-Z&][^,;]*$")
_STOP = frozenset(('the', 'a', 'an', 'of', 'and', '&', 'in', 'on', 'at', 'to', 'for', 'with',
                   'podcast', 'show', 'series', 'official', 'tv'))
# a name that is only stopwords plus one short token is nothing to match on
_MIN_CONTENT = 1


def _noun_match(word):
    w = str(word or '').lower()
    if w in _SCOPE_NOUNS:
        return w
    best = difflib.get_close_matches(w, _SCOPE_NOUNS, n=1, cutoff=_SCOPE_FUZZ)
    return best[0] if best else ''


def _canon_scope(noun):
    n = noun.rstrip('s') if noun not in ('audience', 'fans', 'engagers') else noun
    table = {'listener': 'listeners', 'viewer': 'viewers', 'watcher': 'viewers', 'reader': 'readers',
             'player': 'players', 'gamer': 'players', 'fan': 'fans', 'fans': 'fans', 'subscriber': 'subscribers',
             'buyer': 'buyers', 'shopper': 'shoppers', 'customer': 'customers', 'user': 'users',
             'attendee': 'attendees', 'streamer': 'viewers', 'follower': 'followers', 'audience': 'audience',
             'engager': 'engagers', 'engagers': 'engagers'}
    return table.get(n, n)


def split_scope(phrase):
    """("Listerners of Ball in the Family", ) -> ("Ball in the Family", "listeners").
    A consumer noun leading ("listeners of X") or trailing ("X listeners")
    is the universe scope, not part of the name. Returns (core, scope)
    with scope '' when the phrase carries none."""
    s = ' '.join(str(phrase or '').split()).strip(' .,;:')
    if not s:
        return '', ''
    m = _LEAD_SCOPE_RX.match(s)
    if m:
        noun = _noun_match(m.group('noun'))
        if noun:
            return _TYPE_NOUN_RX.sub('', m.group('rest')).strip(), _canon_scope(noun)
    m = _TAIL_SCOPE_RX.match(s)
    if m:
        noun = _noun_match(m.group('noun'))
        if noun and m.group('noun').lower() not in ('family', 'ball', 'friends'):
            rest = m.group('rest').strip()
            if rest:
                return rest, _canon_scope(noun)
    return _TYPE_NOUN_RX.sub('', s).strip(), ''


def strip_credit(name):
    """"Ball in the Family Podcast with Lonzo & Gelo Ball" -> "Ball in the Family Podcast"."""
    return _CREDIT_RX.sub('', str(name or '').strip()).strip()


def tokens(s):
    return [t for t in re.sub(r"[^a-z0-9&' ]+", ' ', str(s or '').lower()).replace("'s", '').split() if t]


def content_tokens(s):
    return [t for t in tokens(s) if t not in _STOP and len(t) > 1]


def _tok_hit(t, pool):
    if t in pool:
        return 1.0
    best = 0.0
    for p in pool:
        if abs(len(p) - len(t)) > 3:
            continue
        r = difflib.SequenceMatcher(None, t, p).ratio()
        if r > best:
            best = r
    return best if best >= 0.8 else 0.0


def _coverage_score(q, c):
    if not q or not c:
        return 0.0
    q_cov = sum(_tok_hit(t, c) for t in q) / len(q)
    c_cov = sum(_tok_hit(t, q) for t in c) / len(c)
    # a phrase cannot claim a name on one shared word; one stray typed
    # word (Jessie's "Bonzo") is forgiven when the rest of the name is all there
    if not ((q_cov >= 0.75 and c_cov >= 0.5) or (q_cov >= 0.6 and c_cov >= 0.9)):
        return 0.0
    # a one- or two-word phrase must cover most of the name it claims
    # ("Dew" is not Mountain Dew; "Lonzo Ball" is not his podcast)
    if len(q) <= 2 and c_cov < 0.66:
        return 0.0
    # the first content token agreeing is worth a little: a title is read from its start
    first = 0.05 if (q[0] == c[0] or _tok_hit(q[0], [c[0]]) > 0) else 0.0
    return round(min(1.0, 0.6 * q_cov + 0.35 * c_cov + first), 4)


def score(query, candidate):
    """Two-way token coverage with per-token fuzz, 0..1. The query's
    content tokens must be found in the candidate (a slip counts at 0.8)
    and enough of the candidate must be present in the query. Measured
    twice, with the host credit kept ("with Lonzo & Gelo Ball" tells
    the two Ball shows apart) and with it stripped (the credit is not
    the name), and the better reading counts."""
    q_full, c_full = content_tokens(query), content_tokens(candidate)
    q_core, c_core = content_tokens(strip_credit(query)), content_tokens(strip_credit(candidate))
    return max(_coverage_score(q_core, c_core), _coverage_score(q_full, c_full))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def resolve(phrase, *, page='', catalog=(), titles=(), brands=(), min_score=0.72, gap=0.08):
    """The entity an explicit build order names, from what the dashboard
    already knows. ``catalog`` are display names (a cut's ' - ' suffix is
    dropped), ``titles`` content-map shows, ``brands`` hostmap brands.
    Returns a dict:
        display     the resolved name ('' when nothing resolves)
        source      page | catalog | titles | brands | ''
        scope       listeners | viewers | ... | ''  (from the words)
        core        the typed phrase with its scope and credit removed
        candidates  [(name, score, source)] best first
        ambiguous   True when the top two sit within ``gap`` of each other
    """
    core, scope = split_scope(phrase)
    core_n = _norm(strip_credit(core))
    pools = (('page', [page] if str(page or '').strip() else []),
             ('catalog', list(catalog or [])),
             ('titles', list(titles or [])),
             ('brands', list(brands or [])))
    seen = set()
    cands = []
    for source, pool in pools:
        for name in pool:
            base = str(name or '').split(' - ')[0].strip()
            if not base:
                continue
            key = (_norm(base))
            if key in seen:
                continue
            seen.add(key)
            # a catalog universe named for its consumers ("New Heights
            # Podcast Listeners") is the show: its scope noun is not a name token
            base_core = split_scope(base)[0] or base
            if key and (key == core_n or _norm(strip_credit(base_core)) == core_n):
                cands.append((base, 1.0, source))
                continue
            sc = max(score(core, base), score(core, base_core))
            if sc >= min_score:
                cands.append((base, sc, source))
    order = {'page': 0, 'catalog': 1, 'titles': 2, 'brands': 3}
    cands.sort(key=lambda x: (-x[1], order[x[2]]))
    out = {'display': '', 'source': '', 'scope': scope, 'core': strip_credit(core), 'candidates': cands[:5],
           'ambiguous': False}
    if not cands:
        return out
    top = cands[0]
    rivals = [c for c in cands[1:] if top[1] - c[1] < gap and _norm(c[0]) != _norm(top[0])]
    if rivals and top[1] < 1.0:
        out['ambiguous'] = True
        return out
    out['display'], out['source'] = top[0], top[2]
    return out


def which_question(candidates):
    """The one question allowed back: which of these."""
    names = []
    for c in candidates:
        n = c[0] if isinstance(c, (list, tuple)) else str(c)
        if n and n not in names:
            names.append(n)
    names = names[:3]
    if len(names) < 2:
        return ''
    opts = ', '.join(names[:-1]) + ' or ' + names[-1]
    return f"Which one should this profile be on: {opts}?"
