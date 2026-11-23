"""Fixture candidates become replay fixtures on their own (Jenna 2026-11-23,
round 2 follow-up 2).

A candidate the corrections module queued carries a reader's real words
(addresses, links and phone numbers already redacted, the seat replaced).
Before it can be a committed fixture every real person, company, seat,
email, thread id and client-specific title in it is replaced with an
invented stand-in, consistently across the question, the history, the
subject and the expected block (one deterministic mapping per candidate,
from its id, so the case still reads coherently and a rerun gives the same
fixture), never a real name; the shape that made it a defect stays. Then a
scrub check runs IN MEMORY against the real identifiers the weekly job
hands over (the seats' names, emails and domains from users.json, the
catalog's subjects): it returns counts by kind, never a value, and nothing
here prints or saves a real identifier or the mapping.

`sanitize` -> the fixture-shaped case and a report of counts;
`scrub_check` -> {'ok', 'hits': {kind: n}}; `to_fixture` -> one line in the
shape of scripts/pm_corpus/fixtures/sanitized_cases.jsonl. The hand-off to
the weekly corpus job lives in the parent repo
(migration/pm_fixture_candidates.py), which commits a fixture only after
the scrub passes and leaves anything else in the queue for a hand look.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable, Optional

__all__ = ['POOLS', 'stand_ins', 'kind_of', 'apply_mapping', 'identifier_patterns', 'sanitize', 'scrub_check', 'to_fixture',
           'PUBLIC_DOMAINS', 'MIN_NAME_LEN']

# invented stand-ins, never a real brand, person, company or title
POOLS = {
    'brand': ('Example Brand', 'Northline Goods', 'Bluewater Supply', 'Harbor Street Market', 'Coastline Outfitters', 'Ridgeway Tools',
              'Fernhill Foods', 'Saltmarsh Apparel'),
    'person': ('Example Talent', 'Casey Harbor', 'Jordan Vale', 'Riley Northam', 'Morgan Lake', 'Avery Stone', 'Quinn Fairweather',
               'Rowan Ashby'),
    'title': ('Harbor Lights', 'The Quiet Mile', 'Northline Station', 'Second Winter', 'The Long Ferry', 'Blue Hour', 'Saltmarsh Road',
              'The Ridgeway Letters'),
    'company': ('Example Company', 'Northline Media', 'Harbor Street Group', 'Bluewater Partners', 'Ridgeway Studios'),
    'seat': ('fx',),
}
PUBLIC_DOMAINS = frozenset(('gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'aol.com', 'me.com', 'live.com',
                            'crosswalknyc.com', 'example.com'))
MIN_NAME_LEN = 3
_PERSON_HINT = re.compile(r"\b(?:actor|actress|talent|host|singer|musician|athlete|creator|influencer|podcaster|comedian|director|author|writer)\b", re.I)
_TITLE_HINT = re.compile(r"\b(?:viewers|watched|series|season|episode|film|movie|show|podcast|listeners|premiere|on (?:netflix|hulu|peacock|prime video|apple tv|paramount\+|max|disney\+|youtube|tubi))\b", re.I)
_COMPANY_HINT = re.compile(r"\b(?:inc|llc|ltd|group|media|studios|partners|agency|network|corp)\b", re.I)
_ID_RX = re.compile(r"\b(?:api-[0-9a-f]{8}(?:-[a-z]+-\d{4}-[0-9a-f]{4})?|[0-9a-f]{10,12}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.I)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RX = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
_URL_RX = re.compile(r"https?://\S+|www\.\S+", re.I)
_STOP = frozenset(('the', 'and', 'for', 'with', 'from', 'that', 'this', 'what', 'how', 'many', 'much', 'who', 'are', 'was', 'were', 'has',
                   'have', 'does', 'did', 'can', 'you', 'your', 'our', 'their', 'about', 'into', 'over', 'under', 'between', 'audience',
                   'viewers', 'fans', 'people', 'us', 'users', 'buyers', 'brand', 'series', 'film', 'show', 'read', 'deck', 'profile',
                   # generic business and place words a company or seat name may carry; the stand-ins use some of them
                   'media', 'group', 'inc', 'llc', 'ltd', 'company', 'studio', 'studios', 'partners', 'agency', 'network', 'tools', 'goods',
                   'supply', 'market', 'street', 'foods', 'apparel', 'outfitters', 'example', 'talent', 'sports', 'entertainment', 'digital',
                   'global', 'news', 'games', 'music', 'films', 'pictures', 'records', 'labs', 'capital', 'holdings', 'brands', 'the film',
                   'com', 'net', 'org', 'www'))


def _fold(s) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def _pick(seed: str, pool: tuple, taken: set) -> str:
    """A deterministic member of the pool for this seed, skipping ones
    already taken in this candidate; a second letter when the pool is
    exhausted."""
    if not pool:
        return 'Example'
    h = int(hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8], 16)
    for i in range(len(pool)):
        cand = pool[(h + i) % len(pool)]
        if cand not in taken:
            taken.add(cand)
            return cand
    for suffix in 'BCDEFGH':
        cand = f"{pool[h % len(pool)]} {suffix}"
        if cand not in taken:
            taken.add(cand)
            return cand
    return pool[h % len(pool)]


def kind_of(name: str, *, context: str = '', category: str = '') -> str:
    """person | title | company | brand for a name, from the catalog's
    category when known, else from the words around it and its shape."""
    cat = str(category or '').upper()
    if cat:
        if any(k in cat for k in ('ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER', 'CREATOR', 'HOST', 'MUSICIAN', 'PODCASTER', 'POLITIC', 'WRITER', 'TALENT', 'EMERGING')):
            return 'person'
        if any(k in cat for k in ('SERIES', 'MOVIE', 'PODCAST', 'GAME', 'VERTICAL', 'CONTENT')):
            return 'title'
    n = str(name or '')
    if _COMPANY_HINT.search(n):
        return 'company'
    ctx = f"{context or ''} {n}"
    if _TITLE_HINT.search(n) or (_TITLE_HINT.search(ctx) and not _PERSON_HINT.search(ctx)):
        return 'title'
    if _PERSON_HINT.search(ctx):
        return 'person'
    words = [w for w in re.split(r'\s+', n.strip()) if w]
    if len(words) == 2 and all(w[:1].isupper() and w[1:].islower() and w.isalpha() for w in words):
        return 'person'
    return 'brand'


def _split_pairing(name: str) -> list:
    parts = re.split(r"\s+x\s+|\s+×\s+", str(name or ''), flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def stand_ins(seed: str, names: Iterable[str], *, context: str = '', categories: Optional[dict] = None) -> dict:
    """{real name: invented stand-in}, deterministic for (seed, name);
    a pairing 'A x B' maps each side on its own and the pair as the two
    stand-ins joined; a cut suffix (' - Avid Fan') rides unchanged on
    the stand-in."""
    taken = set()
    mapping = {}
    cats = categories or {}
    flat = []
    for n in names:
        n = str(n or '').strip()
        if not n:
            continue
        for part in _split_pairing(n):
            base, _, cut = part.partition(' - ')
            if base and base not in flat:
                flat.append(base)
    flat.sort(key=lambda s: (-len(s), s))
    for base in flat:
        kind = kind_of(base, context=context, category=str(cats.get(base) or cats.get(_fold(base)) or ''))
        mapping[base] = _pick(f"{seed}|{_fold(base)}", POOLS.get(kind) or POOLS['brand'], taken)
    for n in names:
        n = str(n or '').strip()
        if not n or n in mapping:
            continue
        sides = _split_pairing(n)
        if len(sides) > 1:
            mapping[n] = ' x '.join(mapping.get(s.partition(' - ')[0], s) + ((' - ' + s.partition(' - ')[2]) if ' - ' in s else '') for s in sides)
        elif ' - ' in n:
            base, _, cut = n.partition(' - ')
            if base in mapping:
                mapping[n] = f"{mapping[base]} - {cut}"
    return mapping


def apply_mapping(text, mapping: dict) -> str:
    """Every real name replaced by its stand-in, longest first, whole
    words, any case, possessives kept."""
    out = str(text or '')
    if not out or not mapping:
        return out
    for real in sorted(mapping, key=lambda s: (-len(s), s)):
        fake = mapping[real]
        if not real or len(real) < 2:
            continue
        pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(real).replace(r'\ ', r'\s+') + r"(?![A-Za-z0-9])", re.I)
        out = pat.sub(lambda m, f=fake: f, out)
    return out


def identifier_patterns(text) -> dict:
    """Counts of raw identifiers in a text: thread or job ids, emails,
    phone numbers, links. Never the values."""
    t = str(text or '')
    return {'thread_id': len(_ID_RX.findall(t)), 'email': len(_EMAIL_RX.findall(t)), 'phone': len(_PHONE_RX.findall(t)), 'link': len(_URL_RX.findall(t))}


def _strip_identifiers(text) -> str:
    t = str(text or '')
    t = _EMAIL_RX.sub('[email]', t)
    t = _URL_RX.sub('[link]', t)
    t = _PHONE_RX.sub('[phone]', t)
    t = _ID_RX.sub('[id]', t)
    return t


def _name_tokens(names: Iterable[str]) -> set:
    """The whole names and their distinctive words (3 letters and up, not
    stop words), folded, for the scrub."""
    out = set()
    for n in names or ():
        raw = str(n or '').strip()
        f = _fold(raw)
        if not f:
            continue
        if len(f) >= MIN_NAME_LEN:
            out.add(f)
        if '@' in raw or '.' in raw.strip('.'):
            continue                      # an address or a username is one token, never its parts
        for w in f.split():
            if len(w) >= MIN_NAME_LEN and w not in _STOP and not w.isdigit():
                out.add(w)
    return out


def sanitize(candidate: dict, *, real_names: Iterable[str] = (), real_subjects: Iterable[str] = (), categories: Optional[dict] = None,
             seed: str = '') -> tuple:
    """(fixture-shaped case, report). Every name the candidate lists to
    invent, its subject, and every real seat name or catalog subject
    found in its text takes an invented stand-in; identifiers come out;
    the report carries counts only. The mapping is used here and not
    returned."""
    c = candidate if isinstance(candidate, dict) else {}
    seed = seed or str(c.get('id') or 'cand')
    texts = [str(c.get('question') or ''), str(c.get('subject') or '')]
    for t in (c.get('history') or []):
        if isinstance(t, dict):
            texts.append(str(t.get('text') or ''))
    exp = c.get('expected') if isinstance(c.get('expected'), dict) else {}
    texts += [str(exp.get('never_says') or ''), str(exp.get('correction') or '')]
    corpus = '\n'.join(texts)
    folded = _fold(corpus)
    names = []
    for n in list(c.get('names_to_invent') or []) + ([str(c.get('subject') or '')] if c.get('subject') else []):
        n = str(n or '').strip()
        if n and n not in names:
            names.append(n)
    found_subjects = 0
    for s_ in real_subjects or ():
        s_ = str(s_ or '').strip()
        if len(s_) >= MIN_NAME_LEN and _fold(s_) and re.search(r"(?<![a-z0-9])" + re.escape(_fold(s_)) + r"(?![a-z0-9])", folded):
            if s_ not in names:
                names.append(s_)
                found_subjects += 1
    mapping = stand_ins(seed, names, context=corpus, categories=categories)
    # the seats: a person's full name takes one invented person, and each of
    # their names alone takes the matching half of it, so 'Samantha Kolton',
    # 'Samantha' and 'Kolton' read as one invented person; a username or an
    # address is the seat marker
    found_names = 0
    taken = set(mapping.values())
    people = sorted({str(n).strip() for n in (real_names or ()) if str(n or '').strip() and ' ' in str(n) and '@' not in str(n)}, key=lambda x: (-len(x), x))
    singles = {str(n).strip() for n in (real_names or ()) if str(n or '').strip() and (' ' not in str(n) or '@' in str(n))}
    for full in people:
        if _fold(full) and re.search(r"(?<![a-z0-9])" + re.escape(_fold(full)) + r"(?![a-z0-9])", folded):
            fake = _pick(f"{seed}|{_fold(full)}", POOLS['person'], taken)
            mapping[full] = fake
            found_names += 1
            fparts, rparts = fake.split(), full.split()
            if len(rparts) >= 2 and len(fparts) >= 2:
                mapping.setdefault(rparts[0], fparts[0])
                mapping.setdefault(rparts[-1], fparts[-1])
                singles.discard(rparts[0]); singles.discard(rparts[-1])
    for one in sorted(singles, key=lambda x: (-len(x), x)):
        if len(one) < MIN_NAME_LEN or not _fold(one):
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(_fold(one)) + r"(?![a-z0-9])", folded):
            found_names += 1
            if '@' in one or one.islower() or '.' in one or '_' in one:
                mapping[one] = 'fx'
            else:
                mapping[one] = _pick(f"{seed}|{_fold(one)}", POOLS['person'], taken).split()[0]

    def _san(t):
        return apply_mapping(_strip_identifiers(t), mapping)

    out = {
        'question': _san(c.get('question'))[:500],
        'view': str(c.get('view') or '')[:40],
        'surface': str(c.get('surface') or 'analyze'),
        'subject': (_san(c.get('subject'))[:120] or None) if c.get('subject') else None,
        'history': [{'role': ('user' if str(t.get('role') or '') == 'user' else 'agent'), 'text': _san(t.get('text'))[:600]}
                    for t in (c.get('history') or []) if isinstance(t, dict) and str(t.get('text') or '').strip()],
        'reading': None,
        'recorded': None,
        'seat': 'fx',
        'expected': {
            'never_says': _san(exp.get('never_says'))[:400],
            'correction': _san(exp.get('correction'))[:400],
            'figures': [dict(m) for m in (exp.get('figures') or []) if isinstance(m, dict)][:12],
            'window': dict(exp.get('window') or {}),
        },
        'kind': str(c.get('kind') or 'wrong_answer')[:40],
        'by': str(c.get('by') or 'reader')[:12],
        'class': str(c.get('class') or 'read')[:40],
        'note': 'from a correction: the subject and every name are invented stand-ins; the wrong answer, the correction and the figures keep their shape',
        'candidate_id': str(c.get('id') or '')[:40],
    }
    report = {'names_mapped': len(mapping), 'subjects_found': found_subjects, 'seat_names_found': found_names,
              'identifiers_removed': sum(identifier_patterns(corpus).values()), 'history_turns': len(out['history'])}
    return out, report


def scrub_check(fixture: dict, *, real_names: Iterable[str] = (), real_emails: Iterable[str] = (), real_domains: Iterable[str] = (),
                real_subjects: Iterable[str] = ()) -> dict:
    """{'ok', 'hits': {name, subject, email, domain, thread_id, phone, link}}
    for a sanitized case: how many real identifiers survive, by kind.
    Values are never returned, printed or saved."""
    text = json.dumps(fixture, ensure_ascii=False)
    folded = _fold(text)
    hits = {'name': 0, 'subject': 0, 'email': 0, 'domain': 0, 'thread_id': 0, 'phone': 0, 'link': 0}
    for tok in _name_tokens(real_names):
        if re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", folded):
            hits['name'] += 1
    for tok in _name_tokens(real_subjects):
        if re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", folded):
            hits['subject'] += 1
    low = text.lower()
    for e in real_emails or ():
        e = str(e or '').strip().lower()
        if e and e in low:
            hits['email'] += 1
    for d in real_domains or ():
        d = str(d or '').strip().lower()
        if d and d not in PUBLIC_DOMAINS and re.search(r"(?<![a-z0-9])" + re.escape(d) + r"(?![a-z0-9])", low):
            hits['domain'] += 1
    ids = identifier_patterns(text)
    hits['thread_id'] += ids['thread_id']
    hits['email'] += ids['email']
    hits['phone'] += ids['phone']
    hits['link'] += ids['link']
    return {'ok': not any(hits.values()), 'hits': hits}


def to_fixture(case: dict, fixture_id: str, recorded: Optional[dict] = None) -> dict:
    """One line for sanitized_cases.jsonl: the case shape with its id,
    `recorded` pinned (what service.ask does on main), the expected block
    and the note. `known_miss` is set when the recorded route is the
    one the correction said was wrong (the fixture then expects the fix)."""
    out = {'id': str(fixture_id), 'question': case.get('question'), 'view': case.get('view') or '', 'surface': case.get('surface') or 'analyze',
           'subject': case.get('subject'), 'history': list(case.get('history') or []), 'reading': None,
           'recorded': dict(recorded) if isinstance(recorded, dict) else None, 'seat': 'fx',
           'note': str(case.get('note') or ''), 'expected': dict(case.get('expected') or {}), 'kind': case.get('kind'), 'by': case.get('by')}
    cid = str(case.get('candidate_id') or case.get('from_candidate') or '').strip()
    if cid:
        # the queue row this fixture came from (a minted id, never client text): a fixture later committed by hand is
        # matched back to its row so the queued replay never runs it twice (2026-11-23)
        out['from_candidate'] = cid[:40]
    return out
