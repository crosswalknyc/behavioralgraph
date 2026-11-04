"""Short-name subjects are disambiguated before a build (2026-11-04).

A one-token name (EJAE, Ado, Zoomaa, Cloakzy) or a stage name is the
weakest possible key for a person: more than one public figure can carry
it, and the interpret model's first prior is not always the person the
client means. On 2026-11-04 a WME request for "EJAE" drafted "a US-based
R&B/pop singer-songwriter with Beyonce credits" when the client meant
Kim Eun-jae, the singing voice of Rumi in KPop Demon Hunters and the
co-writer of Golden. The model was confident, so nothing asked.

The rule this module implements:

  * When the subject is a short name and the request itself carries no
    identity context, one web-search research call lists the public
    figures who go by the name, ordered by CURRENT digital footprint
    (streams, followers, recent search interest), with verified facts
    only. Credits are never invented.
  * The draft is anchored to the largest footprint. The approval card
    names that person in one plain line ("EJAE, the singer behind Golden
    from KPop Demon Hunters"). Prose drafted from a different identity
    is rewritten from the verified facts.
  * The other figures ride the draft as alternatives behind a
    "Not this EJAE? Pick another" chip. Picking one re-anchors the draft
    to that person; the pick carries a short qualifier into the subject
    (the same-name-version convention, "The Office US") so the build's
    own research resolves the same person and the file never collides
    with the dominant figure's.
  * Nothing blocks. The brief still comes back in one turn.

Pure logic over the draft. The research call itself lives with the
other identity verifiers in app.py (``_verify_short_name_identity``) and
hands its parsed JSON to ``apply``; tests drive ``apply`` with a dict.
"""
from __future__ import annotations

import re

SHORT_NAME_MAX_LETTERS = 8
MAX_CANDIDATES = 5
MAX_FACTS = 6

# A one-token subject whose shape says "handle" rather than "word":
# digits, inner capitals (TimTheTatman), all caps of two or more letters
# (EJAE, IDO), or a leading lowercase run into a capital (iJustine).
_STAGE_SHAPE_RE = re.compile(
    r'(?:\d|[a-z][A-Z]|^[A-Z]{2,}$|^[a-z]+[A-Z])')

# Words that only carry the build order, never the identity.
_ORDER_TOKENS = {
    'run', 'build', 'make', 'create', 'do', 'pull', 'generate', 'launch',
    'start', 'get', 'give', 'need', 'want', 'please', 'can', 'could',
    'would', 'you', 'me', 'us', 'i', 'we', 'a', 'an', 'the', 'new',
    'quick', 'full', 'profile', 'profiles', 'audience', 'one', 'on', 'of',
    'for', 'about', 'iq', 'total', 'universe', 'avid', 'fan', 'fans',
    'plus', 'and', 'with', 'cut', 'cuts', 'trailing', 'months', 'month',
    'year', 'window', 'default', 'today', 'now', 'again', 'another',
    'report', 'read', 'this', 'that', 'it', 'her', 'his', 'their', 'them',
    'to', 'in', 'up', 'out', 'also', 'too', 'just', 'only', 'thanks',
    'thank', 'hi', 'hey', 'hello', 'prometheus', 'crosswalk', 'tu',
}

# Role words that map a researched kind onto the canonical TALENT bucket.
# Whole-word matches, music before writing so 'songwriter' never reads
# as 'writer'.
_KIND_TO_CATEGORY = (
    (('podcaster', 'podcast'), 'PODCASTER'),
    (('comedian', 'comic', 'stand-up', 'standup'), 'COMEDIAN'),
    (('singer', 'songwriter', 'singer-songwriter', 'musician', 'rapper',
      'producer', 'band', 'dj', 'vocalist', 'composer', 'k-pop', 'kpop',
      'idol', 'pianist', 'guitarist', 'drummer', 'cellist', 'violinist',
      'conductor', 'boyband', 'girl group', 'duo', 'trio'),
     'MUSICIAN/BAND'),
    (('athlete', 'player', 'boxer', 'fighter', 'golfer', 'racer',
      'driver', 'wrestler', 'quarterback', 'striker', 'tennis', 'nba',
      'nfl', 'mlb', 'nhl', 'mma', 'ufc', 'footballer', 'gymnast',
      'swimmer', 'skater', 'sprinter'), 'ATHLETE'),
    (('actor', 'actress'), 'ACTOR'),
    (('host', 'presenter', 'anchor', 'personality', 'commentator',
      'broadcaster'), 'HOST/PERSONALITY'),
    (('politician', 'politics', 'activist', 'senator', 'governor',
      'congressman', 'congresswoman', 'mayor', 'president'),
     'POLITICS/ACTIVIST'),
    (('author', 'writer', 'novelist', 'director', 'filmmaker', 'painter',
      'sculptor', 'poet', 'illustrator', 'cartoonist', 'photographer',
      'artist'), 'WRITER/DIRECTOR/AUTHOR/ARTIST'),
    (('creator', 'influencer', 'streamer', 'youtuber', 'tiktoker',
      'vlogger', 'gamer', 'esports', 'twitch'), 'INFLUENCER/CREATOR'),
)
_TALENT_CATEGORIES = {
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR',
    'CREATOR/INFLUENCER', 'EMERGING TALENT', 'HOST/PERSONALITY',
    'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST',
    'WRITER/DIRECTOR/AUTHOR/ARTIST', 'TALENT', '',
}

_BANNED_PROSE_WORDS = re.compile(
    r'\b(modeled|modelled|synthetic|synth|panel|estimated|ai[- ]generated|'
    r'llm|claude|gpt)\b', re.I)


def _norm(s):
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def _clean_line(s, max_len=160):
    """One plain line: no em or en dashes, no newlines, no trailing stop."""
    out = str(s or '').replace('\u2014', ' - ').replace('\u2013', ' - ')
    out = ' '.join(out.split()).strip().rstrip('.').strip()
    if len(out) > max_len:
        out = out[:max_len - 3].rstrip() + '...'
    return out


def entity_part(subject):
    return str(subject or '').split(' - ', 1)[0].strip()


def is_short_name(subject, person=False):
    """True for a one-token subject under SHORT_NAME_MAX_LETTERS letters
    or with a stage-name shape: 'EJAE', 'Ado', 'Zoomaa', 'Cloakzy',
    'TimTheTatman', 'iJustine', 'Ye'. With ``person=True`` (the draft
    reads as a person or a TALENT bucket) every one-token name counts,
    because a person who goes by one word goes by a stage name
    ('Blondish', 'Shakira'). Two-word names and brands do not qualify:
    'Taylor Swift', 'Beyonce Knowles', 'Chipotle Mexican Grill',
    'Starbucks'."""
    ent = entity_part(subject)
    if not ent or ' ' in ent:
        return False
    letters = re.sub(r'[^A-Za-z0-9]', '', ent)
    if len(letters) < 2:
        return False
    if person:
        return True
    if len(letters) < SHORT_NAME_MAX_LETTERS:
        return True
    return bool(_STAGE_SHAPE_RE.search(ent))


def has_identity_context(prompt, subject):
    """True when the request carries words beyond the name and the build
    order ('EJAE the KPop Demon Hunters singer'). 'Build a new profile
    on EJAE' and 'EJAE' carry none."""
    ent = _norm(entity_part(subject))
    toks = re.findall(r"[a-z0-9]+", str(prompt or '').lower())
    left = []
    for t in toks:
        if t in _ORDER_TOKENS or re.fullmatch(r'\d+', t):
            continue
        if ent and (t == ent or _norm(t) in ent or ent in _norm(t)):
            continue
        left.append(t)
    # Two or more content words beyond the name is context; one stray
    # word ('a profile on ejae pls') is not.
    return len(left) >= 2


def research_prompt(subject, prompt, drafted_identity):
    """(system, user) for the one web-search research call. The model
    lists the public figures who go by the name, largest current digital
    footprint first, verified facts only."""
    ent = entity_part(subject)
    system = (
        "You identify WHICH public figure a short name refers to. Use "
        "web search. A short name or stage name can belong to more than "
        "one public figure; find every one who goes by exactly this "
        "name (at most 5) and order them by CURRENT digital footprint, "
        "largest first: monthly streaming listeners, social followers, "
        "YouTube subscribers, recent search interest, recent press. The "
        "person with the larger current digital footprint is the "
        "default meaning of the name.\n"
        "HARD RULES:\n"
        "  * Verified facts only. Never invent credits, songwriting or "
        "production credits, collaborations, awards, labels, teams, "
        "shows or releases. A fact you could not confirm is left out.\n"
        "  * Judge the DRAFTED DESCRIPTION against what you verified. "
        "If it describes none of the real people, say so.\n"
        "  * Plain confident prose. No em dashes. Never the words "
        "modeled, panel, synthetic, AI, estimated.\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "candidates": [\n'
        "    {\n"
        '      "name": "the name as the person styles it",\n'
        '      "who": "one plain line under 12 words naming what this '
        "person is known for, written to follow the name after a comma "
        "('the singer behind Golden from KPop Demon Hunters')\",\n"
        '      "kind": "singer-songwriter | actor | athlete | creator | '
        'host | comedian | podcaster | author | politician | other",\n'
        '      "qualifier": "2 to 4 words that tell this person apart '
        "from the others ('K-pop songwriter', 'R&B producer')\",\n"
        '      "footprint_note": "the public numbers you found, one '
        'line (22M Spotify monthly listeners, 1.9M Instagram followers)",\n'
        '      "footprint_score": <one integer, the largest current '
        "audience number you verified for this person, 0 when none>,\n"
        '      "facts": ["up to 6 verified facts, one line each"],\n'
        '      "handles": {"instagram": "handle or null", "tiktok": '
        '"handle or null", "x": "handle or null", "youtube": "handle or '
        'null"},\n'
        '      "persona_notes": "for the FIRST candidate only: 120 to '
        "220 words on the REAL audience of this person (age and gender "
        "skew, platforms, genre or scene, how people found them in the "
        "last 12 months). Facts about this person only.\"\n"
        "    }\n"
        "  ],\n"
        '  "drafted_matches": <index of the candidate the DRAFTED '
        "DESCRIPTION describes, or null when it describes none of "
        "them>,\n"
        '  "confidence": "high | medium | low"\n'
        "}\n"
    )
    user = (
        f"NAME: {ent}\n"
        f"USER REQUEST (verbatim): {str(prompt or '').strip()[:600]}\n"
        f"DRAFTED DESCRIPTION (may be wrong): "
        f"{str(drafted_identity or '').strip()[:600] or '(none)'}"
    )
    return system, user


def _category_for_kind(kind, who=''):
    """The canonical TALENT bucket a researched kind names, '' when the
    words place the person nowhere. Whole words only: the kind wins
    over the who-line, and the first bucket in order wins."""
    for text in (str(kind or '').lower(), str(who or '').lower()):
        if not text.strip():
            continue
        for words, cat in _KIND_TO_CATEGORY:
            for w in words:
                if re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])',
                             text):
                    return cat
    return ''


def normalize_candidates(raw):
    """Clean the research JSON into at most MAX_CANDIDATES dicts, largest
    footprint first. Returns [] on any shape problem."""
    if isinstance(raw, dict):
        raw = raw.get('candidates')
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    seen = set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            continue
        name = _clean_line(c.get('name'), 80)
        who = _clean_line(c.get('who'), 110)
        if not name or not who:
            continue
        who = re.sub(r'^(?:is\s+|the\s+person\s+)', '', who, flags=re.I)
        # Follows the name after a comma, so an article opens lowercase
        # ('the singer behind Golden'); a proper noun keeps its case.
        who = re.sub(r'^(The|A|An)\b', lambda m: m.group(1).lower(), who)
        try:
            score = int(float(c.get('footprint_score') or 0))
        except (TypeError, ValueError):
            score = 0
        facts = []
        for f in (c.get('facts') or [])[:MAX_FACTS]:
            fl = _clean_line(f, 180)
            if fl and not _BANNED_PROSE_WORDS.search(fl):
                facts.append(fl)
        handles = {}
        hraw = c.get('handles') if isinstance(c.get('handles'), dict) else {}
        for plat in ('instagram', 'tiktok', 'x', 'youtube'):
            h = hraw.get(plat)
            if isinstance(h, str):
                h = h.strip().rstrip('/').split('/')[-1].lstrip('@').strip()
                if h and ' ' not in h and len(h) <= 40:
                    handles[plat] = h
        key = f"{_norm(name)}|{_norm(who)[:24]}"
        if key in seen:
            continue
        seen.add(key)
        notes = ' '.join(str(c.get('persona_notes') or '').split())
        notes = notes.replace('\u2014', ' - ').replace('\u2013', ' - ')
        if _BANNED_PROSE_WORDS.search(notes):
            notes = ''
        out.append({
            'name': name,
            'who': who,
            'kind': _clean_line(c.get('kind'), 40).lower(),
            'qualifier': _clean_line(c.get('qualifier'), 40),
            'footprint_note': _clean_line(c.get('footprint_note'), 160),
            'footprint_score': max(score, 0),
            'facts': facts,
            'handles': handles,
            'persona_notes': notes[:1800],
            '_order': i,
        })
        if len(out) >= MAX_CANDIDATES:
            break
    out.sort(key=lambda c: (-c['footprint_score'], c['_order']))
    for c in out:
        c.pop('_order', None)
    return out


def identity_line(subject, candidate):
    """'EJAE, the singer behind Golden from KPop Demon Hunters'."""
    name = entity_part(subject) or candidate.get('name') or ''
    who = str(candidate.get('who') or '').strip()
    return _clean_line(f"{name}, {who}" if who else name, 200)


def _thin_persona_notes(subject, candidate):
    name = entity_part(subject)
    who = candidate.get('who') or ''
    facts = candidate.get('facts') or []
    lines = [f"Audience profile for {name}, {who}."]
    if facts:
        lines.append(' '.join(f.rstrip('.') + '.' for f in facts[:4]))
    lines.append(
        "The audience shape (age, gender, platform mix) derives from "
        "engagement with this person only; nothing is borrowed from "
        "anyone else who shares the name.")
    return ' '.join(lines)


def versions_for(subject, candidates):
    """The identity picker list: every candidate as a same-name version
    dict the clarify step already understands (title, version_label,
    medium, platform, qualifier) plus the researched identity so the
    pick can re-anchor the prose. The dominant figure keeps the bare
    name; every other one carries its qualifier in parentheses so a
    pick becomes 'EJAE (R&B producer)' and never collides with the
    dominant figure's file or research."""
    name = entity_part(subject)
    out = []
    for i, c in enumerate(candidates):
        qual = ''
        if i > 0:
            q = c.get('qualifier') or c.get('kind') or ''
            q = _clean_line(q, 40).strip('()')
            qual = f"({q})" if q else ''
        out.append({
            'title': name,
            'version_label': c.get('who') or '',
            'medium': 'person',
            'platform': '',
            'qualifier': qual,
            'who': c.get('who') or '',
            'kind': c.get('kind') or '',
            'facts': list(c.get('facts') or []),
            'persona_notes': c.get('persona_notes') or '',
            'footprint_note': c.get('footprint_note') or '',
            'picked': i == 0,
        })
    return out


def _reanchor_prose(draft, subject, candidate, *, replace_notes):
    """Rewrite the free text from the verified identity. Thin and right
    beats rich and wrong."""
    notes = str(candidate.get('persona_notes') or '').strip()
    if replace_notes or not str(draft.get('persona_notes') or '').strip():
        draft['persona_notes'] = (
            notes if len(notes.split()) >= 40
            else _thin_persona_notes(subject, candidate))
    elif notes and _norm(notes[:60]) not in _norm(
            str(draft.get('persona_notes') or '')):
        # The drafted notes already describe this person; lead with the
        # verified identity sentence so the brief reads from it.
        draft['persona_notes'] = (
            f"{entity_part(subject)}, {candidate.get('who')}. "
            + str(draft.get('persona_notes') or '').strip())[:4000]
    draft['decision_reason'] = (
        f"Subject resolved to {identity_line(subject, candidate)}.")
    draft['assumptions'] = [
        f"Subject is {identity_line(subject, candidate)}. Pick another "
        f"if you meant a different {entity_part(subject)}."]
    draft.pop('category_note', None)


def apply(draft, research, *, allow_ask=True):
    """Anchor the draft to the largest-footprint figure. Returns
    {'applied': bool, 'picked': dict | None, 'alternatives': int,
     'replaced_prose': bool, 'reason': str}. Never raises on shape."""
    info = {'applied': False, 'picked': None, 'alternatives': 0,
            'replaced_prose': False, 'reason': ''}
    if not isinstance(draft, dict):
        info['reason'] = 'no draft'
        return info
    cands = normalize_candidates(research)
    if not cands:
        info['reason'] = 'no candidates'
        return info
    subject = str(draft.get('subject') or '').strip()
    ent = entity_part(subject)
    top = cands[0]
    # Official styling of the same name ('Ejae' -> 'EJAE'); never a
    # different name.
    if _norm(top['name']) == _norm(ent) and top['name'] != ent:
        rest = subject[len(ent):]
        draft['subject'] = top['name'] + rest
        draft['name'] = draft['subject']
        draft.pop('file_stem', None)
        subject = draft['subject']
        ent = top['name']
    matched = research.get('drafted_matches') if isinstance(
        research, dict) else None
    try:
        matched = int(matched) if matched is not None else None
    except (TypeError, ValueError):
        matched = None
    drafted_is_top = (matched == 0)
    replace = not drafted_is_top
    line = identity_line(subject, top)
    draft['identity_note'] = line
    draft['resolved_identity'] = line
    draft['resolved_title'] = ent
    draft['identity_confident'] = True
    draft['medium'] = 'person'
    draft.pop('identity_versions', None)
    draft['identity_pick'] = {
        'name': top['name'], 'who': top['who'],
        'footprint_note': top.get('footprint_note') or '',
        'handles': dict(top.get('handles') or {}),
    }
    cur_cat = str(draft.get('brand_category') or '').strip().upper()
    want_cat = _category_for_kind(top.get('kind'), top.get('who'))
    if want_cat and cur_cat in _TALENT_CATEGORIES and cur_cat != want_cat:
        draft['brand_category'] = want_cat
        replace = True
    _reanchor_prose(draft, subject, top, replace_notes=replace)
    if replace:
        # Affinity rows reasoned for a different person are poison; the
        # self-pins stay and the build's own research fills the rest.
        draft['extra_rows'] = []
        info['replaced_prose'] = True
    if len(cands) >= 2:
        draft['identity_alternatives'] = versions_for(subject, cands)
        info['alternatives'] = len(cands) - 1
    else:
        draft.pop('identity_alternatives', None)
    info.update({'applied': True, 'picked': top})
    return info


def chip_options(draft):
    """The approval-card chip when alternatives exist, else None."""
    if not isinstance(draft, dict):
        return None
    alts = draft.get('identity_alternatives')
    if not isinstance(alts, list) or len(alts) < 2:
        return None
    if draft.get('ask_identity'):
        return None
    name = entity_part(draft.get('subject')) or 'this one'
    return [{'id': 'identity_other',
             'label': f"Not this {name}? Pick another"}]


def apply_pick(draft, pick):
    """After the user picks a figure in the identity step: re-anchor
    the identity line, the prose and the category to that person. Runs
    after the generic version apply (which already put the qualifier
    into the subject). No-op for a pick without researched identity."""
    if not isinstance(draft, dict) or not isinstance(pick, dict):
        return False
    who = str(pick.get('who') or pick.get('version_label') or '').strip()
    if not who:
        return False
    cand = {
        'name': str(pick.get('title') or draft.get('subject') or ''),
        'who': who,
        'kind': str(pick.get('kind') or ''),
        'facts': list(pick.get('facts') or []),
        'persona_notes': str(pick.get('persona_notes') or ''),
        'footprint_note': str(pick.get('footprint_note') or ''),
    }
    subject = str(draft.get('subject') or '').strip()
    line = identity_line(subject, cand)
    draft['identity_note'] = line
    draft['resolved_identity'] = line
    draft['identity_confident'] = True
    draft['medium'] = 'person'
    cur_cat = str(draft.get('brand_category') or '').strip().upper()
    want_cat = _category_for_kind(cand['kind'], who)
    if want_cat and cur_cat in _TALENT_CATEGORIES and cur_cat != want_cat:
        draft['brand_category'] = want_cat
    _reanchor_prose(draft, subject, cand, replace_notes=True)
    draft['extra_rows'] = []
    draft['identity_pick'] = {'name': cand['name'], 'who': who,
                              'footprint_note': cand['footprint_note']}
    draft.pop('identity_alternatives', None)
    return True


def confirm_head(pick, title):
    """'Confirmed - EJAE (R&B producer), the R&B producer behind ...'
    for a person pick; '' when the pick carries no researched line."""
    who = str((pick or {}).get('who') or '').strip()
    if not who:
        return ''
    return f"Confirmed - {_clean_line(title, 80)}, {who}."


__all__ = [
    'apply', 'apply_pick', 'chip_options', 'confirm_head',
    'has_identity_context', 'identity_line', 'is_short_name',
    'normalize_candidates', 'research_prompt', 'versions_for',
]
