"""The archetypes that make up a Digital Twin (Jenna, 2026-11-17).

Jenna, verbatim: "instead of splitting it into 12, or say 16 people you're
forced into it should split them based on archetypes within the overall
cohort that comprise it. so if the sample size for the overall cohort is
1m it would then say what comprises this 1m in terms of archetypes? there
are 14 different overall groups of fish (archetypes) that make up the 1m.
200k are archetype x so that'll be Harold, 54 from NYC with that persona.
354k are y."

A twin stays the cohort model it is (the identity, the twelve dials, the
response curves, the lifecycle). This module reads the kinds of people
inside it and sizes each one, so a room seats one person per kind, each
standing for the people of that kind, instead of twelve equal slices.

HOW THE KINDS ARE FOUND

  1. The skeleton (deterministic, hermetic). Two axes the twin already
     carries define the cells: the relationship stage (discovering,
     trying, in the habit, lapsing, returning: the lifecycle) and the life
     stage (under 35, 35 to 54, 55 and over: the age rows). The habit
     cells split again by intensity (devoted / regular) from the avid
     share, since the avid fan is a subset of the engaged. The cell table
     is raked (iterative proportional fitting) so its margins are the
     twin's own lifecycle and age shares exactly; a plausibility tilt
     (younger people are more often new, older people more often in the
     habit) sets the shape inside those margins. Cells under MIN_SHARE_PCT
     merge into their nearest neighbor; the count lands between MIN_K and
     MAX_K (Jenna's example, fourteen, sits in the middle).
     Each cell carries a demographic profile (the twin's rows for every
     category, tilted by life stage the way real households tilt: parents
     in the middle years, singles under 35), trait values (the twin's
     dials moved by stage, intensity and life stage), a discovery mode,
     and a plain label and two-sentence read written from those parts.
  2. The archetype desk (one strong-model call per twin, cached). Given
     the twin brief, the researched base persona when the twin has one
     and the skeleton, the model names each kind in the subject's own
     terms, writes who they are, how they relate to the subject, how they
     decide, what moves them and what holds them back, nudges a few
     dials, and may merge two cells that are one kind of person or split
     one that is two. Its share nudges are relative and bounded; the
     table is re-raked to the margins afterwards, so the model shapes the
     mix and never the totals. Nothing a reader sees says how the kinds
     were found (the output passes the room scrub).
  3. The representative. Each kind gets one person drawn from its own
     profile: a name from the house banks by gender, an age inside the
     band weighted by the twin's rows, a city from the twin's geography,
     and every demographic label the persona card reads. Harold, 54, from
     New York, standing for 200,114 people.

The result is stored on the twin as ``archetypes`` (schema below) and is
reproducible per (twin fingerprint, ARCHETYPE_VERSION): the same twin
gives the same kinds, shares, people and representatives every time.
People counts are messy, sum to the twin exactly, and no two kinds share
a share at one decimal.

  twin['archetypes'] = {
    'version', 'computed_at', 'fingerprint', 'method': 'reasoned' | 'deterministic',
    'k', 'size_people', 'noun',
    'groups': [{'id', 'label', 'share_pct', 'people', 'stage', 'life', 'intensity',
                'who', 'relationship', 'decides', 'moves_them', 'holds_them_back',
                'discovery', 'traits': {key: value}, 'profile': {category: [{label, pct}]},
                'representative': {name, age, city, gender, ...}}],
    'margins': {'age': {...}, 'lifecycle': {...}},      the margins the table was raked to (internal)
    'trail': {...}                                       internal, never rendered
  }

Rooms: ``seat_archetypes(twin, archetypes, filter, people, seed)`` gives
the kinds inside a cohort (a filter conditions each kind by its own
profile, so a room of women 25 to 44 seats the kinds that women 25 to 44
are made of), sized to the cohort's people; ``fit_count`` merges the
smallest kinds or splits the largest to meet a seat count the user asked
for. Everything here is fail-safe and never raises into a build or a
room.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from typing import Callable, Optional

from migration.digital_twin_engine import clamp, messy_count, natural_count, pct1, unit

ARCHETYPE_VERSION = 1
MIN_K = 12                   # a room always seats at least twelve (Jenna 2026-11-17): fewer kinds are split out, by gender then age, for the nuance
MAX_K = 16
MERGE_FLOOR = 4              # the slivers may merge this far down before the largest kinds split back up to MIN_K
MIN_SHARE_PCT = 2.5          # a kind under this share of the audience merges into its nearest neighbor
MIN_SEGMENT_SHARE_PCT = 3.0  # inside a cohort, a kind under this share of the cohort merges
DESK_MAX_TOKENS = 12000      # the desk's one answer per twin; streamed, so a stall is bounded by the read timeout, not the whole generation
IPF_ROUNDS = 40

STAGES = ('discovering', 'trying', 'habit', 'lapsing', 'returning')
STAGE_LC_KEY = {'discovering': 'discovering_pct', 'trying': 'trying_pct', 'habit': 'habit_pct', 'lapsing': 'lapsing_pct', 'returning': 'returning_pct'}
STAGE_CARD = {'discovering': 'discovering', 'trying': 'trying', 'habit': 'in the habit', 'lapsing': 'lapsing', 'returning': 'returning'}
LIFE = (('young', 18, 34), ('mid', 35, 54), ('older', 55, 99))
LIFE_WORDS = {'young': 'under 35', 'mid': '35 to 54', 'older': '55 and over'}
INTENSITIES = ('devoted', 'regular')

# the plausibility tilt inside the margins: how much more (or less) often a stage shows up at a life stage than independence
# would give; the raking keeps the margins exact, so these only set the shape of the table
_TILT = {
    'discovering': {'young': 1.35, 'mid': 0.95, 'older': 0.65},
    'trying':      {'young': 1.25, 'mid': 1.00, 'older': 0.72},
    'habit':       {'young': 0.86, 'mid': 1.04, 'older': 1.18},
    'lapsing':     {'young': 1.10, 'mid': 1.00, 'older': 0.86},
    'returning':   {'young': 0.90, 'mid': 1.04, 'older': 1.10},
}
# how the twin's dials move for a kind, on top of the twin's own value (clamped to 3..97)
_STAGE_SHIFT = {
    'discovering': {'trial_speed': 10, 'discovery_social_vs_search': 6, 'franchise_loyalty': -8, 'word_of_mouth': 4},
    'trying':      {'trial_speed': 6, 'price_sensitivity': 6, 'franchise_loyalty': -4},
    'habit':       {'franchise_loyalty': 5},
    'lapsing':     {'churn_propensity': 14, 'franchise_loyalty': -10, 'price_sensitivity': 6, 'ad_tolerance': 3},
    'returning':   {'franchise_loyalty': 6, 'word_of_mouth': 4, 'churn_propensity': -3},
}
_INTENSITY_SHIFT = {
    'devoted': {'franchise_loyalty': 12, 'merch_propensity': 10, 'word_of_mouth': 8, 'live_events': 8, 'churn_propensity': -8, 'price_sensitivity': -6, 'creator_follow': 6},
    'regular': {'franchise_loyalty': -2, 'merch_propensity': -4},
}
_LIFE_SHIFT = {
    'young': {'discovery_social_vs_search': 10, 'second_screen': 8, 'binge_vs_appointment': 4, 'creator_follow': 6},
    'mid':   {},
    'older': {'discovery_social_vs_search': -12, 'second_screen': -8, 'binge_vs_appointment': -6, 'ad_tolerance': 4, 'creator_follow': -6},
}
# how the twin's rows tilt inside a life stage (a factor on each row that matches the pattern; the category re-normalizes)
_PROFILE_TILT = {
    'young': {'income': ((r"less than|under|\$25,000|\$35,000|\$50,000 - \$74|\$25,000 - \$49", 1.3), (r"\$100,000|\$150,000|\$200,000|\$250,000|or more", 0.72)),
              'parental': ((r"has child|with child|parent", 0.55), (r"no child|without|non-?parent", 1.3)),
              'relationship': ((r"single|in a relationship", 1.45), (r"married|partnered", 0.62), (r"divorced|separated|widowed", 0.35)),
              'education': ((r"some college|high school", 1.15), (r"graduate or professional", 0.8))},
    'mid':   {'income': ((r"\$75,000|\$100,000|\$150,000", 1.12), (r"less than|under|\$25,000", 0.85)),
              'parental': ((r"has child|with child|parent", 1.55), (r"no child|without|non-?parent", 0.72)),
              'relationship': ((r"married|partnered", 1.3), (r"single", 0.78))},
    'older': {'income': ((r"\$100,000|\$150,000|\$200,000|or more", 1.08), (r"less than|under|\$25,000", 0.95)),
              'parental': ((r"has child|with child|parent", 0.45), (r"no child|without|non-?parent", 1.35)),
              'relationship': ((r"married|partnered", 1.15), (r"widowed", 3.0), (r"divorced|separated", 1.25), (r"single|in a relationship", 0.6)),
              'occupation': ((r"retired", 2.5),)},
}
_INTENSITY_GENDER_TILT = 1.06   # the devoted lean a little further toward the audience's larger gender

_NOUNS = {'title': 'viewers', 'platform': 'subscribers', 'brand': 'buyers', 'talent': 'followers', 'audience': 'people', 'civic': 'voters'}
_FORMAT_NOUNS = {'podcast': 'listeners', 'book': 'readers', 'game': 'players', 'creator': 'followers', 'series': 'viewers', 'film': 'viewers'}

ARCHETYPE_SYSTEM = """You are the audience strategist naming the archetypes that make up one audience, so a focus group can seat one archetype representative per archetype, each standing for the people of that archetype. You are given the audience (who it is, how it behaves, its lifecycle, its platforms and purchases, the research on its base person when there is one) and a skeleton: the archetypes already found from the audience's own numbers (relationship stage x life stage x intensity), each with its share, its demographic profile and its habits. Your job is to make each archetype a real, specific person a studio or brand executive would recognize, in this subject's own terms.
Rules:
- Keep every skeleton id. You may set merge_into on an archetype that is truly the same person as another (then it disappears into that one), and you may split one archetype in two by giving split_label and split_share_pct (the share of the archetype that goes to the second). Keep the total between 6 and 16 archetypes.
- label: three to seven plain words, specific to this subject and this archetype ("Weeknight regulars who found it on Hulu", "Parents who keep it on for the kids", "Lapsed fans waiting for the next season"). No puns. No demographic-only labels ("Women 35 to 54"); the age and gender are already on the card.
- who: two sentences about who they are and how the subject fits their week. relationship: one sentence on where they stand with the subject now. decides: one sentence on how they decide to watch, buy or show up. moves_them: a short phrase. holds_them_back: a short phrase.
- share_adjust_pct: a relative nudge to the archetype's share, between -30 and 30, when the audience's facts argue the skeleton has it too big or too small. The totals are re-fitted afterwards; you shape the mix, not the size of the audience.
- trait_shifts: up to five of these dials moved by -20 to 20 points for this archetype: discovery_social_vs_search, binge_vs_appointment, price_sensitivity, churn_propensity, franchise_loyalty, merch_propensity, second_screen, live_events, creator_follow, ad_tolerance, trial_speed, word_of_mouth.
- There are at least twelve archetypes and the room seats one archetype representative per archetype; a merge that would leave fewer than twelve is ignored. An archetype that reads as two distinct archetypes can be split (split_label, split_share_pct).
- profile_lean: who this archetype is, when it really skews, as the share (percent, 0 to 100) of the archetype on the named rows, by category: gender, sexual_orientation, ethnicity, income, parental, relationship, education. Use the audience's own row labels (the gender rows you were given, for example Female, Male, Non-Binary, Trans Female, Trans Male; the orientation rows, for example LGBTQ+ or Gay or Lesbian). Rows you do not name share what is left in the audience's own proportions. An archetype that is gay men: {"gender": {"Male": 95}, "sexual_orientation": {"LGBTQ+": 95}}; an archetype that is trans women: {"gender": {"Trans Female": 90}, "sexual_orientation": {"LGBTQ+": 85}}; an archetype of young mothers: {"parental": {"Has Children": 92}}. {} when the archetype is the audience's own mix. The other archetypes are re-fitted so the audience's totals hold, and an archetype can never hold more of a row than the audience has (a 7% archetype can be 90% trans women only if the audience is at least 6.3% trans women; if the rows do not hold an archetype, do not name it), so lean only where the facts argue it.
- Plain English a 7th grader follows; third person; no first names; never these words: synthetic, synth, model, modeled, estimated, research, panel, sample, persona, segment, cohort, confidence, data. Never call an archetype a kind or a type of person; the word is archetype, and the person who stands for one is the archetype representative. No em dashes.
Return JSON only:
{"groups": [{"id": "<skeleton id>", "label": "...", "who": "...", "relationship": "...", "decides": "...", "moves_them": "...", "holds_them_back": "...", "share_adjust_pct": 0, "trait_shifts": {}, "profile_lean": {}, "merge_into": null, "split_label": null, "split_share_pct": null}], "notes": "<one sentence on what distinguishes this audience's archetypes>"}"""

_BANNED_RX = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|estimate|research(?:ed)?|panel|panelists?|sample|personas?|segments?|cohorts?|confidence|data)\b", re.I)   # archetype is a reader word (Jenna 2026-11-20)


# ------------------------------------------------------------------ helpers
def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _meta(t):
    return (t or {}).get('meta') or {}


def _demo(t, cat):
    rows = (((t or {}).get('identity') or {}).get('demographics') or {}).get(cat) or []
    return [r for r in rows if isinstance(r, dict) and r.get('label')]


def _trait_values(t) -> dict:
    out = {}
    for k, v in ((t or {}).get('traits') or {}).items():
        try:
            out[k] = float(v.get('value') if isinstance(v, dict) else v)
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _age_band(label: str) -> Optional[str]:
    lab = str(label or '')
    m = re.search(r"(\d{2})", lab)
    if not m:
        return None
    lo = int(m.group(1))
    if lo < 18:
        return None       # account holders are adults; an under-18 row never seats
    for key, a, z in LIFE:
        if a <= lo <= z:
            return key
    return 'older'


def _norm_rows(rows: list) -> list:
    tot = sum(max(0.0, float(r.get('pct') or 0)) for r in rows) or 0.0
    if tot <= 0:
        return [dict(r, pct=round(100.0 / max(1, len(rows)), 4)) for r in rows]
    return [dict(r, pct=round(max(0.0, float(r.get('pct') or 0)) / tot * 100.0, 4)) for r in rows]


def _tilt_rows(rows: list, tilts: tuple) -> list:
    out = []
    for r in rows:
        f = 1.0
        for rx, factor in tilts:
            if re.search(rx, str(r.get('label') or ''), re.I):
                f *= factor
        out.append(dict(r, pct=float(r.get('pct') or 0) * f))
    return _norm_rows(out)


def fingerprint(twin: dict) -> str:
    """What the kinds depend on: the demographics, the lifecycle, the
    dials, the size, the avid share and the version."""
    ident = (twin or {}).get('identity') or {}
    demo = ident.get('demographics') or {}
    body = {'v': ARCHETYPE_VERSION,
            'demo': {k: [(str(r.get('label')), round(float(r.get('pct') or 0), 2)) for r in (v or []) if isinstance(r, dict)] for k, v in sorted(demo.items())},
            'lc': {k: round(float(v or 0), 2) for k, v in ((twin or {}).get('lifecycle') or {}).items() if k != 'read' and isinstance(v, (int, float))},
            'traits': {k: round(v, 1) for k, v in sorted(_trait_values(twin).items())},
            'size': int(float(_meta(twin).get('size_us') or 0)), 'avid': round(float(_meta(twin).get('avid_share_pct') or ident.get('avid_share_pct') or 0), 2),
            'subject': str(_meta(twin).get('subject') or _meta(twin).get('name') or '')}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]


def noun_for(twin: dict) -> str:
    """The people word for this audience: viewers, listeners, buyers,
    subscribers, followers, voters, people."""
    try:
        from migration.digital_twin_room import subject_class
        klass = subject_class(twin)
    except Exception:
        klass = 'audience'
    fmt = str(_meta(twin).get('format') or '').lower()
    if klass == 'title' and fmt in _FORMAT_NOUNS:
        return _FORMAT_NOUNS[fmt]
    if klass == 'talent' and fmt in ('creator', 'influencer', 'host', 'podcaster'):
        return 'followers'
    if klass == 'talent':
        return 'fans'
    return _NOUNS.get(klass, 'people')


def scrub_text(s: str) -> str:
    """Reader copy: the house words off, no em or en dashes."""
    t = str(s or '').replace('\u2014', ', ').replace('\u2013', '-')
    t = _BANNED_RX.sub(lambda m: {'persona': 'person', 'personas': 'people', 'segment': 'group', 'segments': 'groups',
                                   'cohort': 'group', 'cohorts': 'groups', 'sample': 'group', 'panel': 'group', 'data': 'record', 'confidence': 'certainty'}.get(m.group(1).lower(), ''), t)
    t = re.sub(r"\s{2,}", ' ', t).replace(' ,', ',').replace(' .', '.').strip()
    return t


# ---------------------------------------------------------------- the margins
def margins(twin: dict) -> dict:
    """The two margins the table is raked to: the lifecycle shares and
    the adult age shares by life stage, each summing to 100."""
    lc = (twin or {}).get('lifecycle') or {}
    stage = {}
    for s in STAGES:
        try:
            stage[s] = max(0.0, float(lc.get(STAGE_LC_KEY[s]) or 0))
        except (TypeError, ValueError):
            stage[s] = 0.0
    if sum(stage.values()) <= 0:
        stage = {'discovering': 16.0, 'trying': 15.0, 'habit': 43.0, 'lapsing': 15.0, 'returning': 11.0}
    tot = sum(stage.values())
    stage = {k: v / tot * 100.0 for k, v in stage.items()}
    life = {k: 0.0 for k, _a, _z in LIFE}
    for r in _demo(twin, 'age'):
        b = _age_band(r.get('label'))
        if b:
            life[b] += max(0.0, float(r.get('pct') or 0))
    if sum(life.values()) <= 0:
        life = {'young': 43.0, 'mid': 36.0, 'older': 21.0}
    tot = sum(life.values())
    life = {k: v / tot * 100.0 for k, v in life.items()}
    return {'lifecycle': stage, 'age': life}


def _rake(table: dict, row_m: dict, col_m: dict, rounds: int = IPF_ROUNDS) -> dict:
    """Iterative proportional fitting of {(row, col): value} to the row
    and column margins (each summing to the same total). A zero margin
    zeroes its line."""
    t = {k: max(1e-9, float(v)) for k, v in table.items()}
    rows = sorted({r for r, _c in t})
    cols = sorted({c for _r, c in t})
    for _ in range(rounds):
        for r in rows:
            s = sum(t[(r, c)] for c in cols if (r, c) in t) or 1e-9
            f = float(row_m.get(r, 0.0)) / s
            for c in cols:
                if (r, c) in t:
                    t[(r, c)] *= f
        for c in cols:
            s = sum(t[(r, c)] for r in rows if (r, c) in t) or 1e-9
            f = float(col_m.get(c, 0.0)) / s
            for r in rows:
                if (r, c) in t:
                    t[(r, c)] *= f
    return t


# --------------------------------------------------------------- the skeleton
def _cell_profile(twin: dict, life: str, intensity: Optional[str]) -> dict:
    """The twin's rows for every category, tilted the way this life stage
    tilts, the age rows cut to the band."""
    demo = ((twin or {}).get('identity') or {}).get('demographics') or {}
    out = {}
    lo, hi = next(((a, z) for k, a, z in LIFE if k == life), (18, 99))
    for cat, rows in demo.items():
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get('label')]
        if not rows:
            continue
        if cat == 'age':
            keep = [r for r in rows if _age_band(r.get('label')) == life]
            out[cat] = _norm_rows(keep) if keep else [{'label': f"{lo}-{hi if hi < 99 else '+'}", 'pct': 100.0}]
            continue
        tilts = (_PROFILE_TILT.get(life) or {}).get(cat)
        rows2 = _tilt_rows(rows, tilts) if tilts else _norm_rows(rows)
        if cat == 'gender' and intensity == 'devoted' and rows2:
            top = max(rows2, key=lambda r: float(r.get('pct') or 0))
            rows2 = _norm_rows([dict(r, pct=float(r.get('pct') or 0) * (_INTENSITY_GENDER_TILT if r is top else 1.0)) for r in rows2])
        out[cat] = rows2
    return out


def _cell_traits(twin: dict, stage: str, life: str, intensity: Optional[str]) -> dict:
    base = _trait_values(twin)
    out = {}
    keys = set(base) | set(_STAGE_SHIFT.get(stage) or {}) | set(_LIFE_SHIFT.get(life) or {}) | set((_INTENSITY_SHIFT.get(intensity or '') or {}))
    for k in sorted(keys):
        v = base.get(k, 50.0)
        v += (_STAGE_SHIFT.get(stage) or {}).get(k, 0) + (_LIFE_SHIFT.get(life) or {}).get(k, 0) + (_INTENSITY_SHIFT.get(intensity or '') or {}).get(k, 0)
        out[k] = round(clamp(v, 3.0, 97.0), 1)
    return out


def _discovery_mode(traits: dict) -> str:
    v = float(traits.get('discovery_social_vs_search', 50.0))
    if v >= 62:
        return 'the feed and short clips'
    if v <= 40:
        return 'search and word of mouth'
    return 'friends and the feed in equal parts'


def _stage_noun_label(stage: str, intensity: Optional[str], life: str, noun: str) -> str:
    life_w = LIFE_WORDS.get(life, '')
    if stage == 'habit':
        head = 'Devoted regulars' if intensity == 'devoted' else 'Steady regulars'
    else:
        head = {'discovering': f"Newcomers", 'trying': f"Still-deciding {noun}", 'lapsing': f"Drifting {noun}", 'returning': f"Returning {noun}"}[stage]
    return f"{head}, {life_w}" if life_w else head


def _deterministic_copy(twin: dict, stage: str, life: str, intensity: Optional[str], traits: dict, noun: str) -> dict:
    """The plain read of a kind written from its parts, for when the desk
    is off."""
    name = str(_meta(twin).get('name') or _meta(twin).get('subject') or 'the title')
    life_w = LIFE_WORDS.get(life, '')
    try:
        from migration.digital_twin_room import subject_class
        klass = subject_class(twin)
    except Exception:
        klass = 'title'
    verb = {'platform': 'keep', 'brand': 'buy', 'talent': 'follow', 'civic': 'vote in', 'audience': 'make time for'}.get(klass, 'watch')
    social = float(traits.get('discovery_social_vs_search', 50)) >= 58
    binge = float(traits.get('binge_vs_appointment', 50)) >= 55
    price = float(traits.get('price_sensitivity', 50)) >= 60
    rel = {
        'discovering': f"They found {name} in the last year and are still working out how much room it gets.",
        'trying': f"They have given {name} a few tries and have not settled on it.",
        'habit': (f"{name} is a fixture for them; they {verb} it without thinking about it." if intensity == 'devoted' else f"They {verb} {name} most weeks and would miss it."),
        'lapsing': f"They used to {verb} {name} often and have drifted this past year.",
        'returning': f"They came back to {name} after a break and are catching up.",
    }[stage]
    who1 = {
        'young': f"{noun.capitalize()} {life_w}, early in their careers or still in school, with the phone never far away.",
        'mid': f"{noun.capitalize()} {life_w}, in the busiest years of work and family, who protect the hour they give to this.",
        'older': f"{noun.capitalize()} {life_w}, with settled routines and more evenings of their own.",
    }[life]
    who2 = (f"They find new things through {'the feed and clips' if social else 'friends and search'} and " + ('binge when they start.' if binge else 'take it an episode or two at a time.'))
    decides = ('A clip or a friend is usually what tips them; the price is the last check.' if price else 'A clip or a friend is usually what tips them, and they rarely wait for a deal.')
    moves = {'discovering': 'a strong first hour', 'trying': 'a reason to come back next week', 'habit': 'more of what they already love', 'lapsing': 'a reminder that it is still worth it', 'returning': 'a clear way back in'}[stage]
    holds = {'discovering': 'too many other things to try', 'trying': 'a slow start', 'habit': 'very little', 'lapsing': 'the feeling they have seen it', 'returning': 'losing the thread again'}[stage]
    return {'who': who1 + ' ' + who2, 'relationship': rel, 'decides': decides, 'moves_them': moves, 'holds_them_back': holds}


def skeleton(twin: dict) -> dict:
    """The deterministic cells with their shares (summing to 100) and
    everything the desk and the room read off them."""
    mg = margins(twin)
    stage_m, life_m = mg['lifecycle'], mg['age']
    table = {}
    for s in STAGES:
        for l_key, _a, _z in LIFE:
            table[(s, l_key)] = max(0.01, stage_m[s]) * max(0.01, life_m[l_key]) / 100.0 * _TILT[s][l_key]
    table = _rake(table, stage_m, life_m)
    ident = (twin or {}).get('identity') or {}
    try:
        avid = float(_meta(twin).get('avid_share_pct') or ident.get('avid_share_pct') or 0) / 100.0
    except (TypeError, ValueError):
        avid = 0.0
    if avid <= 0:
        avid = 0.22
    engaged = (stage_m['habit'] + stage_m['returning']) / 100.0
    devoted_frac = clamp(avid / max(0.2, engaged), 0.15, 0.8)
    noun = noun_for(twin)
    cells = []
    for (s, l_key), share in table.items():
        parts = [(None, share)] if s != 'habit' else [('devoted', share * devoted_frac), ('regular', share * (1.0 - devoted_frac))]
        for intensity, sh in parts:
            cid = f"{s}-{intensity}-{l_key}" if intensity else f"{s}-{l_key}"
            traits = _cell_traits(twin, s, l_key, intensity)
            copy_ = _deterministic_copy(twin, s, l_key, intensity, traits, noun)
            cells.append({'id': cid, 'stage': s, 'life': l_key, 'intensity': intensity, 'share_pct': float(sh),
                          'label': _stage_noun_label(s, intensity, l_key, noun), 'traits': traits, 'discovery': _discovery_mode(traits),
                          'profile': _cell_profile(twin, l_key, intensity), **copy_, 'members': [cid], 'member_shares': {cid: float(sh)}})
    return {'cells': cells, 'margins': mg, 'noun': noun, 'devoted_frac': round(devoted_frac, 4)}


# ------------------------------------------------------------ merge and split
_LIFE_ORDER = ['young', 'mid', 'older']
_STAGE_ORDER = list(STAGES)


def _distance(a: dict, b: dict) -> float:
    """How far two kinds are: same stage and adjacent life is nearest,
    then same life and adjacent stage, then the rest."""
    d = 0.0
    d += 1.0 * abs(_LIFE_ORDER.index(a['life']) - _LIFE_ORDER.index(b['life']))
    d += 1.4 * abs(_STAGE_ORDER.index(a['stage']) - _STAGE_ORDER.index(b['stage']))
    d += 0.6 if (a.get('intensity') or '') != (b.get('intensity') or '') else 0.0
    return d


def _member_shares(kind: dict) -> dict:
    """{skeleton cell id: share} inside a kind; a kind without the record
    spreads its share evenly over its members."""
    ms = kind.get('member_shares')
    if isinstance(ms, dict) and ms:
        return {str(k): float(v) for k, v in ms.items()}
    mem = list(kind.get('members') or [kind.get('id')])
    sh = float(kind.get('share_pct') or 0)
    return {str(m): sh / len(mem) for m in mem}


def _scale_kind(kind: dict, factor: float) -> dict:
    """The kind at ``factor`` times its share, its member shares scaled
    with it."""
    out = dict(kind)
    out['share_pct'] = float(kind.get('share_pct') or 0) * factor
    out['member_shares'] = {k: v * factor for k, v in _member_shares(kind).items()}
    return out


def _split_kind(kind: dict, share_b: float, id_a: str, id_b: str) -> tuple:
    """Two kinds from one: ``share_b`` of the share goes to the second;
    both carry the member shares in proportion."""
    sh = float(kind.get('share_pct') or 0) or 1.0
    fb = clamp(share_b / sh, 0.0, 1.0)
    a, b = _scale_kind(kind, 1.0 - fb), _scale_kind(kind, fb)
    a['id'], b['id'] = id_a, id_b
    a['members'], b['members'] = list(kind.get('members') or [kind['id']]), list(kind.get('members') or [kind['id']])
    return a, b


def _span_words(rows: list) -> str:
    """The age words for a kind from its own age rows: 'under 35',
    '35 to 54', '55 and over', 'under 55', '35 and over', 'all ages'."""
    bands = {b for b in (_age_band(r.get('label')) for r in rows or []) if b}
    if not bands or bands == {'young', 'mid', 'older'}:
        return 'all ages' if bands else ''
    if bands == {'young'}:
        return LIFE_WORDS['young']
    if bands == {'mid'}:
        return LIFE_WORDS['mid']
    if bands == {'older'}:
        return LIFE_WORDS['older']
    if bands == {'young', 'mid'}:
        return 'under 55'
    if bands == {'mid', 'older'}:
        return '35 and over'
    return 'all ages'


def _relabel_after_merge(kind: dict) -> None:
    """A deterministic label (stage words plus age words) is rewritten to
    the merged kind's age span; a desk label carries no age words and
    stays."""
    lab = str(kind.get('label') or '')
    for w in list(LIFE_WORDS.values()) + ['under 55', '35 and over', 'all ages']:
        if lab.endswith(', ' + w):
            head = lab[:-(len(w) + 2)]
            span = _span_words((kind.get('profile') or {}).get('age') or [])
            kind['label'] = f"{head}, {span}" if span and span != 'all ages' else head
            return


def _merge_into(dst: dict, src: dict) -> dict:
    """``src`` folded into ``dst``: shares add; the profile and the dials
    are the people-weighted blend; the label and copy stay the bigger
    kind's (a deterministic label takes the merged age span)."""
    wa, wb = float(dst['share_pct']), float(src['share_pct'])
    tot = (wa + wb) or 1.0
    out = dict(dst)
    out['share_pct'] = wa + wb
    out['members'] = list(dict.fromkeys(list(dst.get('members') or [dst['id']]) + list(src.get('members') or [src['id']])))
    ms = dict(_member_shares(dst))
    for k, v in _member_shares(src).items():
        ms[k] = ms.get(k, 0.0) + float(v)
    out['member_shares'] = ms
    prof = {}
    for cat in set(dst.get('profile') or {}) | set(src.get('profile') or {}):
        ra = {str(r['label']): float(r.get('pct') or 0) for r in (dst.get('profile') or {}).get(cat) or []}
        rb = {str(r['label']): float(r.get('pct') or 0) for r in (src.get('profile') or {}).get(cat) or []}
        labs = list(dict.fromkeys(list(ra) + list(rb)))
        prof[cat] = _norm_rows([{'label': l, 'pct': (ra.get(l, 0.0) * wa + rb.get(l, 0.0) * wb) / tot} for l in labs])
    out['profile'] = prof
    out['traits'] = {k: round((float(dst['traits'].get(k, 50)) * wa + float(src['traits'].get(k, 50)) * wb) / tot, 1) for k in set(dst['traits']) | set(src['traits'])}
    out['discovery'] = _discovery_mode(out['traits'])
    if wb > wa:
        for k in ('label', 'who', 'relationship', 'decides', 'moves_them', 'holds_them_back', 'stage', 'life', 'intensity'):
            out[k] = src.get(k)
    if dst.get('life') != src.get('life'):
        _relabel_after_merge(out)
    return out


def merge_small(cells: list, min_share: float, k_min: int = MIN_K, k_max: int = MAX_K) -> list:
    """Fold the kinds under ``min_share`` into their nearest neighbor until
    none is under it (never below ``k_min`` kinds), then the smallest
    until at most ``k_max`` remain."""
    cells = [dict(c) for c in cells if float(c.get('share_pct') or 0) > 0]
    while len(cells) > k_min:
        small = [c for c in cells if float(c['share_pct']) < min_share]
        if not small:
            break
        c = min(small, key=lambda x: float(x['share_pct']))
        others = [o for o in cells if o is not c]
        dst = min(others, key=lambda o: (_distance(c, o), -float(o['share_pct'])))
        cells = [(_merge_into(o, c) if o is dst else o) for o in others]
    while len(cells) > k_max:
        c = min(cells, key=lambda x: float(x['share_pct']))
        others = [o for o in cells if o is not c]
        dst = min(others, key=lambda o: (_distance(c, o), -float(o['share_pct'])))
        cells = [(_merge_into(o, c) if o is dst else o) for o in others]
    return cells


SPLIT_MIN_SIDE = 0.18        # a split needs both sides to carry at least this much of the kind, so no sliver kinds are made

_SPLIT_WORDS = {
    'gender': {'Female': 'the women', 'Women': 'the women', 'Male': 'the men', 'Men': 'the men'},
    'parental': {'has': 'with children at home', 'no': 'without children at home'},
    'relationship': {'married': 'married or partnered', 'single': 'single'},
    'income': {'hi': 'higher income', 'lo': 'lower income'},
    'education': {'deg': 'with a degree', 'nodeg': 'without a degree'},
}


def _split_plan(kind: dict) -> Optional[dict]:
    """The first dimension of a kind's own profile with real spread, so a
    split reads as nuance: gender (two real sides), age inside the band,
    children at home, married or single, income halves, a degree or not,
    then the largest ethnicity against the rest. Each side must carry at
    least SPLIT_MIN_SIDE of the kind. None when nothing splits."""
    prof = kind.get('profile') or {}

    def rows_of(cat):
        return [r for r in (prof.get(cat) or []) if float(r.get('pct') or 0) > 0 and 'prefer' not in str(r.get('label') or '').lower()]

    def two_sides(cat, side_a, side_b, word_a, word_b):
        tot = sum(float(r.get('pct') or 0) for r in side_a + side_b) or 0.0
        if tot <= 0:
            return None
        fb = sum(float(r.get('pct') or 0) for r in side_b) / tot
        if fb < SPLIT_MIN_SIDE or fb > 1.0 - SPLIT_MIN_SIDE:
            return None
        return {'cat': cat, 'frac_b': fb, 'rows_a': _norm_rows([dict(r) for r in side_a]), 'rows_b': _norm_rows([dict(r) for r in side_b]), 'word_a': word_a, 'word_b': word_b}

    g = rows_of('gender')
    if len(g) >= 2:
        sides = sorted(g, key=lambda r: -float(r.get('pct') or 0))
        top, second = sides[0], sides[1]
        w = _SPLIT_WORDS['gender']
        plan = two_sides('gender', [top], [second], w.get(str(top['label']), str(top['label']).lower()), w.get(str(second['label']), str(second['label']).lower()))
        if plan:
            plan['sep'] = ': '
            return plan
    ages = rows_of('age')
    if len(ages) >= 2:
        ages = sorted(ages, key=lambda r: int(re.search(r"\d{2}", str(r.get('label') or '0')).group(0)) if re.search(r"\d{2}", str(r.get('label') or '')) else 0)
        best = None
        for cut in range(1, len(ages)):
            plan = two_sides('age', ages[:cut], ages[cut:], 'younger', 'older')
            if plan and (best is None or abs(plan['frac_b'] - 0.5) < abs(best['frac_b'] - 0.5)):
                best = plan
        if best:
            best['sep'] = ', '
            return best
    par = rows_of('parental')
    if len(par) >= 2:
        has = [r for r in par if re.search(r"has|with|parent", str(r.get('label') or ''), re.I)]
        no = [r for r in par if r not in has]
        plan = two_sides('parental', has, no, _SPLIT_WORDS['parental']['has'], _SPLIT_WORDS['parental']['no']) if has and no else None
        if plan:
            plan['sep'] = ', '
            return plan
    rel = rows_of('relationship')
    if len(rel) >= 2:
        together = [r for r in rel if re.search(r"married|partner|relationship", str(r.get('label') or ''), re.I)]
        alone = [r for r in rel if r not in together]
        plan = two_sides('relationship', together, alone, _SPLIT_WORDS['relationship']['married'], _SPLIT_WORDS['relationship']['single']) if together and alone else None
        if plan:
            plan['sep'] = ', '
            return plan
    inc = rows_of('income')
    if len(inc) >= 2:
        def _lo(r):
            m = re.search(r"\$?([\d,]{2,})", str(r.get('label') or ''))
            return int(m.group(1).replace(',', '')) if m else (0 if re.search(r"less|under", str(r.get('label') or ''), re.I) else 10**9)
        inc = sorted(inc, key=_lo)
        best = None
        for cut in range(1, len(inc)):
            plan = two_sides('income', inc[:cut], inc[cut:], _SPLIT_WORDS['income']['lo'], _SPLIT_WORDS['income']['hi'])
            if plan and (best is None or abs(plan['frac_b'] - 0.5) < abs(best['frac_b'] - 0.5)):
                best = plan
        if best:
            best['sep'] = ', '
            return best
    edu = rows_of('education')
    if len(edu) >= 2:
        deg = [r for r in edu if re.search(r"bachelor|graduate|professional|degree", str(r.get('label') or ''), re.I)]
        nodeg = [r for r in edu if r not in deg]
        plan = two_sides('education', deg, nodeg, _SPLIT_WORDS['education']['deg'], _SPLIT_WORDS['education']['nodeg']) if deg and nodeg else None
        if plan:
            plan['sep'] = ', '
            return plan
    eth = rows_of('ethnicity')
    if len(eth) >= 2:
        sides = sorted(eth, key=lambda r: -float(r.get('pct') or 0))
        plan = two_sides('ethnicity', [sides[0]], sides[1:], str(sides[0]['label']), 'the rest')
        if plan:
            plan['sep'] = ', '
            return plan
    return None


def _apply_split(kind: dict, plan: dict) -> tuple:
    """Two kinds from one along the plan's dimension: each side's profile
    for that category is its own rows, the labels gain the side."""
    prof = kind.get('profile') or {}
    share_b = float(kind['share_pct']) * plan['frac_b']
    a, b = _split_kind(kind, share_b, f"{kind['id']}~a", f"{kind['id']}~b")
    a['profile'] = dict(prof, **{plan['cat']: plan['rows_a']})
    b['profile'] = dict(prof, **{plan['cat']: plan['rows_b']})
    base = str(kind.get('label') or '')
    a['label'], b['label'] = f"{base}{plan['sep']}{plan['word_a']}", f"{base}{plan['sep']}{plan['word_b']}"
    return a, b


def split_largest(cells: list, seed: str) -> list:
    """One more kind: the largest kind with a dimension that really
    splits it (``_split_plan``) is split along it, both halves keeping
    the copy and their labels gaining the side. A kind with nothing to
    split on is passed over for the next largest; only when no kind
    splits is the largest halved near-evenly, the labels marked."""
    if not cells:
        return cells
    for big in sorted(cells, key=lambda c: -float(c['share_pct'])):
        plan = _split_plan(big)
        if plan:
            a, b = _apply_split(big, plan)
            return [c for c in cells if c is not big] + [a, b]
    big = max(cells, key=lambda c: float(c['share_pct']))
    half = float(big['share_pct']) * (0.5 + (unit('split', seed, big['id']) - 0.5) * 0.1)
    a, b = _split_kind(big, half, f"{big['id']}~a", f"{big['id']}~b")
    a['label'], b['label'] = f"{big['label']} (one side)", f"{big['label']} (the other side)"
    return [c for c in cells if c is not big] + [a, b]


def fit_count(cells: list, want: int, seed: str = '') -> list:
    """The kinds brought to ``want`` seats: the smallest merge into their
    neighbors until the count fits; the largest split until it does."""
    want = int(max(1, want))
    cells = [dict(c) for c in cells]
    while len(cells) > want and len(cells) > 1:
        c = min(cells, key=lambda x: float(x['share_pct']))
        others = [o for o in cells if o is not c]
        dst = min(others, key=lambda o: (_distance(c, o), -float(o['share_pct'])))
        cells = [(_merge_into(o, c) if o is dst else o) for o in others]
    guard = 0
    while len(cells) < want and guard < 64:
        cells = split_largest(cells, f"{seed}|{guard}")
        guard += 1
    return cells


# ---------------------------------------------------------------- the desk
def _desk_user(twin: dict, sk: dict) -> str:
    try:
        from migration.digital_twin_room import _twin_brief
        brief = _twin_brief(twin)
    except Exception:
        brief = {'name': _meta(twin).get('name'), 'subject': _meta(twin).get('subject')}
    base = (twin or {}).get('persona_base') or {}
    base_view = {k: base.get(k) for k in ('who', 'motivations', 'tastes', 'churn', 'household', 'notes') if base.get(k)} if isinstance(base, dict) else {}
    cells = []
    for c in sk['cells']:
        prof = c.get('profile') or {}
        cells.append({'id': c['id'], 'stage': STAGE_CARD.get(c['stage'], c['stage']), 'life': LIFE_WORDS.get(c['life'], c['life']), 'intensity': c.get('intensity'),
                      'share_pct': round(float(c['share_pct']), 1), 'gender': [{'label': r['label'], 'pct': round(float(r['pct']), 1)} for r in (prof.get('gender') or [])[:3]],
                      'top_income': [r['label'] for r in sorted(prof.get('income') or [], key=lambda r: -float(r.get('pct') or 0))[:2]],
                      'parents_pct': round(sum(float(r.get('pct') or 0) for r in (prof.get('parental') or []) if re.search(r"has child|with child|parent", str(r.get('label') or ''), re.I)), 1),
                      'discovery': c.get('discovery'), 'dials': {k: v for k, v in (c.get('traits') or {}).items()},
                      'draft_label': c.get('label'), 'draft_who': c.get('who')})
    body = {'audience': brief, 'base_person': base_view, 'people_word': sk.get('noun'), 'kinds': cells, 'today': time.strftime('%Y-%m-%d', time.gmtime())}
    return json.dumps(body, default=str)[:24000]


def _apply_desk(sk: dict, data: dict) -> tuple:
    """The desk's answer folded onto the skeleton: copy, dial shifts,
    bounded share nudges, merges and splits. Returns (cells, trail)."""
    cells = {c['id']: dict(c) for c in sk['cells']}
    groups = data.get('groups') if isinstance(data, dict) else None
    if not isinstance(groups, list) or not groups:
        return list(cells.values()), {'applied': False, 'reason': 'no groups'}
    applied, merges, splits = 0, [], []
    valid_traits = set()
    for c in cells.values():
        valid_traits |= set(c.get('traits') or {})
    for g in groups:
        if not isinstance(g, dict):
            continue
        cid = str(g.get('id') or '')
        c = cells.get(cid)
        if c is None:
            continue
        for k in ('label', 'who', 'relationship', 'decides', 'moves_them', 'holds_them_back'):
            v = scrub_text(g.get(k) or '')
            if v and 2 <= len(v) <= (90 if k in ('label', 'moves_them', 'holds_them_back') else 480):
                c[k] = v
        try:
            adj = clamp(float(g.get('share_adjust_pct') or 0), -30.0, 30.0)
        except (TypeError, ValueError):
            adj = 0.0
        if adj:
            scaled = _scale_kind(c, 1.0 + adj / 100.0)
            c['share_pct'], c['member_shares'] = scaled['share_pct'], scaled['member_shares']
        shifts = g.get('trait_shifts') if isinstance(g.get('trait_shifts'), dict) else {}
        n = 0
        for k, v in shifts.items():
            if k in valid_traits and n < 5:
                try:
                    c['traits'][k] = round(clamp(float(c['traits'].get(k, 50)) + clamp(float(v), -20.0, 20.0), 3.0, 97.0), 1)
                    n += 1
                except (TypeError, ValueError):
                    continue
        c['discovery'] = _discovery_mode(c['traits'])
        leans = g.get('profile_lean') if isinstance(g.get('profile_lean'), dict) else {}
        if leans:
            c['profile'], set_rows = _lean_profile(c.get('profile') or {}, leans)
            if set_rows:
                c['set_rows'] = set_rows
        if g.get('merge_into') and str(g['merge_into']) in cells and str(g['merge_into']) != cid:
            merges.append((cid, str(g['merge_into'])))
        if g.get('split_label') and g.get('split_share_pct') is not None:
            try:
                sp = clamp(float(g['split_share_pct']), 15.0, 85.0)
                splits.append((cid, scrub_text(g['split_label'])[:90], sp))
            except (TypeError, ValueError):
                pass
        applied += 1
    out = list(cells.values())
    # merges the desk asked for (bounded: never below MIN_K)
    for src_id, dst_id in merges:
        if len(out) <= MIN_K:
            break
        src = next((c for c in out if c['id'] == src_id), None)
        dst = next((c for c in out if c['id'] == dst_id), None)
        if src is None or dst is None:
            continue
        out = [(_merge_into(dst, src) if c is dst else c) for c in out if c is not src]
    # splits the desk asked for (bounded: never above MAX_K)
    for cid, label_b, sp in splits:
        if len(out) >= MAX_K:
            break
        c = next((x for x in out if x['id'] == cid), None)
        if c is None:
            continue
        share_b = float(c['share_pct']) * sp / 100.0
        a, b = _split_kind(c, share_b, f"{cid}~a", f"{cid}~b")
        b['label'] = label_b or f"{c['label']} (a second kind)"
        out = [x for x in out if x is not c] + [a, b]
    return out, {'applied': applied > 0, 'groups_read': applied, 'merges': merges, 'splits': [s[0] for s in splits], 'notes': scrub_text(str(data.get('notes') or ''))[:240]}


_LEAN_CATS = ('gender', 'sexual_orientation', 'ethnicity', 'income', 'parental', 'relationship', 'education', 'occupation', 'party', 'urbanicity')
PIN_SHARE = 70.0     # a kind whose rows sit at or above this on one label, and well above the audience's own share of it, is that label
PIN_OVER = 1.25      # ... 'well above': at least this many times the audience's own share (so a 72% female audience does not pin every kind female)
PIN_HARD = 90.0      # a row at or above this pins whatever the audience's share (a kind of gay men seats a gay man)


def _lean_profile(profile: dict, leans: dict) -> tuple:
    """The desk's word on who a kind is: the named rows take the shares
    given (percent of the kind, case-insensitive on the label); the rows
    not named share what is left in their own proportions. Returns (the
    profile, {category: [labels set]}) so the re-fit to the audience's
    margins leaves the set rows alone. Unknown categories and labels are
    ignored; a set that asks for more than 100 is scaled to 100."""
    out = {k: list(v) for k, v in (profile or {}).items()}
    set_rows = {}
    for cat, want in (leans or {}).items():
        cat = str(cat or '').strip().lower().replace(' ', '_')
        cat = {'orientation': 'sexual_orientation', 'parents': 'parental', 'kids': 'parental'}.get(cat, cat)
        if cat not in _LEAN_CATS or not isinstance(want, dict) or not out.get(cat):
            continue
        by = {}
        for k, v in want.items():
            try:
                by[str(k).strip().lower()] = clamp(float(v), 0.0, 100.0)
            except (TypeError, ValueError):
                continue
        rows = out[cat]
        hit = [r for r in rows if str(r.get('label') or '').strip().lower() in by]
        if not hit:
            continue
        asked = sum(by[str(r.get('label') or '').strip().lower()] for r in hit)
        scale = min(1.0, 100.0 / asked) if asked > 0 else 1.0
        rest = [r for r in rows if r not in hit]
        rest_tot = sum(float(r.get('pct') or 0) for r in rest) or 1.0
        left = max(0.0, 100.0 - asked * scale)
        new_rows = []
        for r in rows:
            key = str(r.get('label') or '').strip().lower()
            if key in by:
                new_rows.append(dict(r, pct=by[key] * scale))
            else:
                new_rows.append(dict(r, pct=float(r.get('pct') or 0) / rest_tot * left))
        out[cat] = _norm_rows(new_rows)
        set_rows[cat] = [str(r.get('label')) for r in hit]
    return out, set_rows


def fit_profiles_to_margins(cells: list, twin: dict, rounds: int = 6) -> list:
    """Across the kinds, every category's rows re-fitted so the
    share-weighted sum of the kinds' rows is the twin's own row for each
    label (the room's totals stay the audience's, however the kinds
    differ). Age is left alone (its margin is held by the cell raking).
    Iterative: each label scaled by twin over implied, each kind
    renormalized, a few rounds."""
    demo = ((twin or {}).get('identity') or {}).get('demographics') or {}
    out = [dict(c, profile={k: list(v) for k, v in (c.get('profile') or {}).items()}) for c in cells]
    tot_share = sum(float(c.get('share_pct') or 0) for c in out) or 100.0
    for cat in _LEAN_CATS:
        target = {str(r.get('label')): float(r.get('pct') or 0) for r in (demo.get(cat) or []) if isinstance(r, dict) and r.get('label')}
        ttot = sum(target.values())
        if not target or ttot <= 0:
            continue
        target = {k: v / ttot * 100.0 for k, v in target.items()}
        kinds_with = [c for c in out if c['profile'].get(cat)]
        if not kinds_with:
            continue
        # the kinds the desk set on this category stand as set; the rest are fitted so the audience's totals hold
        fixed = [c for c in kinds_with if cat in (c.get('set_rows') or {})]
        free = [c for c in kinds_with if c not in fixed]
        if not free:
            continue
        w_all = sum(float(c.get('share_pct') or 0) / tot_share for c in kinds_with) or 1.0

        def _fixed_part():
            part = {}
            for c in fixed:
                w = float(c.get('share_pct') or 0) / tot_share / w_all
                for r in c['profile'][cat]:
                    part[str(r.get('label'))] = part.get(str(r.get('label')), 0.0) + w * float(r.get('pct') or 0)
            return part

        fixed_part = _fixed_part()
        # the audience's rows win over the desk's word: a kind cannot hold more of a label than the audience has (a 7% kind
        # of trans women in an audience that is 0.2% trans women is scaled to what the audience holds, with a sliver left over)
        over = {lab: target.get(lab, 0.0) * 0.98 / v for lab, v in fixed_part.items() if v > 0 and v > target.get(lab, 0.0) * 0.98}
        if over:
            for c in fixed:
                rows = [dict(r) for r in c['profile'][cat]]
                removed = 0.0
                for r in rows:
                    f = over.get(str(r.get('label')))
                    if f is not None:
                        cut = float(r.get('pct') or 0) * (1.0 - f)
                        r['pct'] = float(r.get('pct') or 0) - cut
                        removed += cut
                if removed > 0:
                    # what the kind cannot be goes back to its other rows in their own proportions (never a renormalize, which
                    # would inflate the capped row again)
                    others = [r for r in rows if str(r.get('label')) not in over]
                    o_tot = sum(float(r.get('pct') or 0) for r in others)
                    for r in others:
                        r['pct'] = float(r.get('pct') or 0) + (removed * float(r.get('pct') or 0) / o_tot if o_tot > 0 else removed / max(1, len(others)))
                    c.setdefault('lean_capped', []).append(cat)
                c['profile'][cat] = _norm_rows(rows)
            fixed_part = _fixed_part()
        free_w = sum(float(c.get('share_pct') or 0) / tot_share / w_all for c in free) or 1.0
        free_target = {k: max(0.0, target.get(k, 0.0) - fixed_part.get(k, 0.0)) / free_w for k in set(target) | set(fixed_part)}
        for _ in range(rounds):
            implied = {}
            for c in free:
                w = float(c.get('share_pct') or 0) / tot_share / w_all / free_w
                for r in c['profile'][cat]:
                    implied[str(r.get('label'))] = implied.get(str(r.get('label')), 0.0) + w * float(r.get('pct') or 0)
            factor = {k: (free_target.get(k, 0.0) / implied[k] if implied.get(k, 0.0) > 0 else 1.0) for k in implied}
            for c in free:
                c['profile'][cat] = _norm_rows([dict(r, pct=float(r.get('pct') or 0) * clamp(factor.get(str(r.get('label')), 1.0), 0.2, 5.0)) for r in c['profile'][cat]])
    return out


def _rerake(cells: list, mg: dict) -> list:
    """After the desk's nudges, the kinds are re-fitted so the stage and
    the life-stage margins are the twin's again (a kind that spans
    members keeps its share of each member cell)."""
    stage_m, life_m = mg['lifecycle'], mg['age']
    by_cell = {}
    for c in cells:
        for m, v in _member_shares(c).items():
            by_cell[_cell_key(m)] = by_cell.get(_cell_key(m), 0.0) + float(v)
    if not by_cell:
        return cells
    fitted = _rake(by_cell, stage_m, life_m)
    factor = {k: (fitted[k] / by_cell[k] if by_cell[k] > 0 else 1.0) for k in by_cell}
    out = []
    for c in cells:
        ms = {m: float(v) * factor.get(_cell_key(m), 1.0) for m, v in _member_shares(c).items()}
        out.append(dict(c, share_pct=sum(ms.values()), member_shares=ms))
    tot = sum(float(c['share_pct']) for c in out) or 100.0
    return [dict(c, share_pct=float(c['share_pct']) / tot * 100.0, member_shares={m: v / tot * 100.0 for m, v in c['member_shares'].items()}) for c in out]


def _cell_key(member_id: str) -> tuple:
    """(stage, life) of a skeleton cell id ('habit-devoted-mid~a' -> ('habit', 'mid'))."""
    parts = str(member_id).split('~')[0].split('-')
    return parts[0], parts[-1]


def _settle_people(cells: list, size: int, seed: str) -> list:
    """Shares to one decimal (no two alike, summing to 100.0) and messy
    people summing to ``size`` exactly, largest kind first."""
    cells = sorted(cells, key=lambda c: -float(c['share_pct']))
    n = len(cells)
    if n == 0:
        return cells
    raw = [float(c['share_pct']) for c in cells]
    tot = sum(raw) or 100.0
    raw = [r / tot * 100.0 for r in raw]
    # the people come from the raw shares (never from rounded tenths), messy, summing to the size exactly
    people = []
    for i, c in enumerate(cells):
        v = int(round(raw[i] / 100.0 * size))
        people.append(messy_count(seed, f"arch|{c['id']}|people", max(1, v)) if size >= 500 else max(1, v))
    people[0] = max(1, people[0] + (size - sum(people)))
    if size >= 500 and n > 1:
        # no two kinds on the same count and none ending in 0: a later duplicate steps up by a few units, the largest kind gives them
        seen = set()
        for i in range(1, n):
            guard = 0
            while (people[i] in seen or people[i] % 10 == 0) and guard < 30:
                d = 1 + int(unit(seed, 'dup', cells[i]['id'], guard) * 7)
                people[i] += d
                people[0] -= d
                guard += 1
            seen.add(people[i])
        guard = 0
        while (people[0] % 10 == 0 or people[0] in seen) and guard < 20:
            people[0] -= 1
            people[1] += 1
            guard += 1
        people[0] = max(1, people[0])
    # the shares print from the people at one decimal and sum to 100.0 (the largest takes the rounding gap)
    if size > 0:
        pcts = [round(p / float(size) * 100.0, 1) for p in people]
    else:
        pcts = [round(r, 1) for r in raw]
    pcts[0] = round(pcts[0] + round(100.0 - sum(pcts), 1), 1)
    # no two kinds on the same tenth when a one-tenth nudge settles it (a room of many small kinds keeps its true shares instead)
    seen = {}
    for i in range(n):
        seen.setdefault(pcts[i], []).append(i)
    dupes = [idx for idx in seen.values() if len(idx) > 1]
    if dupes and n <= MAX_K:
        used = set(pcts)
        for idx in dupes:
            for j in idx[1:]:
                for step in (-0.1, 0.1, -0.2, 0.2):
                    cand = round(pcts[j] + step, 1)
                    if cand >= 0.1 and cand not in used:
                        used.add(cand)
                        pcts[j] = cand
                        break
        gap = round(100.0 - sum(pcts), 1)
        if abs(gap) <= 0.5:
            cand = round(pcts[0] + gap, 1)
            if cand not in set(pcts[1:]):
                pcts[0] = cand
        else:
            pcts = [round(p / float(size) * 100.0, 1) for p in people] if size > 0 else [round(r, 1) for r in raw]
            pcts[0] = round(pcts[0] + round(100.0 - sum(pcts), 1), 1)
    out = []
    for c, p, ppl, r in zip(cells, pcts, people, raw):
        ms = _member_shares(c)
        tot_ms = sum(ms.values()) or 1.0
        out.append(dict(c, share_pct=max(0.0, p), people=int(ppl), member_shares={m: v / tot_ms * r for m, v in ms.items()}))
    return out


# ---------------------------------------------------------- representatives
def _draw(rows: list, u: float, default: str = '') -> str:
    tot = sum(max(0.0, float(r.get('pct') or 0)) for r in rows) or 0.0
    if tot <= 0:
        return str(rows[0].get('label') or default) if rows else default
    acc = 0.0
    for r in rows:
        acc += max(0.0, float(r.get('pct') or 0)) / tot
        if u <= acc:
            return str(r.get('label') or default)
    return str(rows[-1].get('label') or default)


def representative(twin: dict, kind: dict, seed: str, seat_no: int = 0) -> dict:
    """One person drawn from the kind's own profile: a name by gender
    from the house banks, an age inside the band weighted by the twin's
    rows, a city from the twin's geography, and the labels the persona
    card reads. Deterministic per (seed, kind)."""
    from migration import digital_twin_panel as P
    sid = f"{seed}|rep|{kind.get('id')}"
    prof = kind.get('profile') or {}
    g_label = _draw(prof.get('gender') or [], unit('gender', sid), 'Female')
    g = P._gender_code(g_label)
    bank = P._NAMES_F if g == 'F' else (P._NAMES_X if g == 'X' else P._NAMES_M)
    name = bank[int(unit('name', sid) * len(bank))]
    age_rows = [r for r in (prof.get('age') or []) if _age_band(r.get('label'))]
    age_label = _draw(age_rows, unit('age', sid), '')
    lo, hi = next(((a, z) for k, a, z in LIFE if k == kind.get('life')), (25, 54))
    if age_label:
        age = P._age_in_bucket(age_label, unit('agein', sid))
    else:
        age = lo + int((min(hi, 74) - lo) * unit('agein', sid))
    age = int(clamp(age, 18, 84))
    city = P._draw_city(twin, unit('city', sid))
    orientation = _draw(prof.get('sexual_orientation') or [], unit('orient', sid), '')
    seat = {'seat': seat_no, 'name': name, 'age': age, 'city': city, 'gender': g, 'gender_label': g_label,
            'orientation': orientation,
            'education': _draw(prof.get('education') or [], unit('edu', sid), ''),
            'income': _draw(prof.get('income') or [], unit('inc', sid), ''),
            'ethnicity': _draw(prof.get('ethnicity') or [], unit('eth', sid), ''),
            'relationship': _draw(prof.get('relationship') or [], unit('rel', sid), ''),
            'parental': _draw(prof.get('parental') or [], unit('par', sid), ''),
            'occupation': _draw(prof.get('occupation') or [], unit('occ', sid), ''),
            'party': _draw(prof.get('party') or [], unit('party', sid), ''),
            'urbanicity': _draw(prof.get('urbanicity') or [], unit('urb', sid), ''),
            'stage': STAGE_CARD.get(str(kind.get('stage') or ''), 'in the habit'),
            'intensity': kind.get('intensity') or '',
            'archetype': {'id': kind.get('id'), 'label': kind.get('label'), 'share_pct': kind.get('share_pct'), 'people': kind.get('people'),
                          'who': kind.get('who'), 'relationship': kind.get('relationship'), 'decides': kind.get('decides'),
                          'moves_them': kind.get('moves_them'), 'holds_them_back': kind.get('holds_them_back'), 'discovery': kind.get('discovery'),
                          'traits': dict(kind.get('traits') or {})}}
    if age >= 66 and seat['occupation'] and not re.search(r"retired", seat['occupation'], re.I) and unit('retire', sid) < 0.7:
        seat['occupation'] = 'Retired'
    return seat


# ------------------------------------------------------------------ public
def derive_archetypes(twin: dict, *, call: Optional[Callable] = None, force: bool = False, log: Optional[Callable] = None) -> dict:
    """The kinds of people in this twin. ``call(system, user, max_tokens,
    temperature) -> dict | None`` is the archetype desk's model; when it
    is None (REGRESSION_TEST_MODE, no key) the deterministic kinds ship.
    Stored on the twin as ``archetypes`` and returned. Never raises."""
    fp = fingerprint(twin)
    have = (twin or {}).get('archetypes')
    if isinstance(have, dict) and have.get('groups') and not force and have.get('fingerprint') == fp and int(have.get('version') or 0) >= ARCHETYPE_VERSION:
        if have.get('method') == 'reasoned' or call is None:
            return have
    try:
        size = int(float(_meta(twin).get('size_us') or 0))
    except (TypeError, ValueError):
        size = 0
    seed = f"arch|{_meta(twin).get('subject') or _meta(twin).get('name')}|{fp}"
    sk = skeleton(twin)
    cells = merge_small(sk['cells'], MIN_SHARE_PCT, k_min=MERGE_FLOOR)
    if len(cells) < MIN_K:
        # never a question of how many to seat: fewer than twelve kinds split out, the largest first, by gender then by age
        # inside the band, so the room is always at least twelve and the split reads as nuance (Jenna 2026-11-17)
        cells = fit_count(cells, MIN_K, f"{seed}|floor")
    method, trail = 'deterministic', {'desk': None}
    if call is not None:
        try:
            # the answer runs about 470 tokens a kind (who, relationship, decides, the leans and the dial shifts): 16 kinds
            # need about 8,000; at 6,000 every call truncated and re-ran at twice the spend (the box, 2026-11-17)
            data = call(ARCHETYPE_SYSTEM, _desk_user(twin, {'cells': cells, 'noun': sk['noun']}), DESK_MAX_TOKENS, 0.3)
        except Exception as e:
            data = None
            trail['desk'] = {'error': str(e)[:160]}
        if isinstance(data, dict) and data.get('groups'):
            cells, t = _apply_desk({'cells': cells}, data)
            cells = merge_small(cells, MIN_SHARE_PCT)
            if len(cells) < MIN_K:
                cells = fit_count(cells, MIN_K, f"{seed}|floor|desk")
            trail['desk'] = t
            method = 'reasoned' if t.get('applied') else 'deterministic'
        elif trail['desk'] is None:
            trail['desk'] = {'applied': False, 'reason': 'no answer'}
    cells = _rerake(cells, sk['margins'])
    cells = fit_profiles_to_margins(cells, twin)
    cells = _settle_people(cells, size, seed)
    groups = []
    for i, c in enumerate(cells):
        groups.append({'id': c['id'], 'label': scrub_text(c.get('label') or ''), 'share_pct': float(c['share_pct']), 'people': int(c['people']),
                       'stage': c['stage'], 'life': c['life'], 'intensity': c.get('intensity'),
                       'who': scrub_text(c.get('who') or ''), 'relationship': scrub_text(c.get('relationship') or ''), 'decides': scrub_text(c.get('decides') or ''),
                       'moves_them': scrub_text(c.get('moves_them') or ''), 'holds_them_back': scrub_text(c.get('holds_them_back') or ''),
                       'discovery': c.get('discovery'), 'traits': dict(c.get('traits') or {}), 'profile': c.get('profile') or {}, 'members': list(c.get('members') or [c['id']]),
                       'member_shares': {k: round(float(v), 6) for k, v in _member_shares(c).items()}})
    # the people who stand for the kinds on the twin page: the same draw a room on the whole twin seats
    from migration import digital_twin_panel as P
    seen = set()
    for g, rep in zip(groups, roster_from_kinds(twin, groups, seed)):
        bank = P._NAMES_F if rep['gender'] == 'F' else (P._NAMES_X if rep['gender'] == 'X' else P._NAMES_M)
        if rep['name'] in seen:
            rep['name'] = P._free_name(rep['name'], bank, seen)
        seen.add(rep['name'])
        g['representative'] = {k: v for k, v in rep.items() if k not in ('archetype', 'people')}
    out = {'version': ARCHETYPE_VERSION, 'computed_at': _now(), 'fingerprint': fp, 'method': method, 'k': len(groups), 'size_people': size,
           'noun': sk['noun'], 'groups': groups, 'margins': sk['margins'], 'trail': dict(trail, devoted_frac=sk['devoted_frac']), 'seed': seed}
    for g in groups:   # the person who stands for each archetype is one person, wherever they sit (Jenna 2026-11-20)
        if isinstance(g.get('representative'), dict):
            g['representative']['person_id'] = str(g['id'])
    if isinstance(twin, dict):
        twin['archetypes'] = out
    if log:
        try:
            log('archetypes', {'method': method, 'k': len(groups)})
        except Exception:
            pass
    return out


def filter_sig(f: Optional[dict]) -> str:
    """A short stable signature of a segment's filter (the slice of the
    audience a cohort is cut to); '' for the whole audience."""
    f = {k: v for k, v in dict(f or {}).items() if v not in (None, '', [], {}) and k != 'city'}
    if not f:
        return ''
    return hashlib.sha1(json.dumps(f, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:10]


def rep_seed(twin: dict, f: Optional[dict] = None) -> str:
    """The seed every room draws its archetype representatives from
    (Jenna 2026-11-20: one stable person per archetype, so memory has
    someone to belong to): the twin's own archetype seed, so a room on
    the whole audience seats the very people the twin page shows; a
    cohort cut to a filter adds the filter's signature, so the same
    conditioned people come back room after room."""
    arch = (twin or {}).get('archetypes') if isinstance(twin, dict) else {}
    arch = arch if isinstance(arch, dict) else {}
    base = str(arch.get('seed') or '')
    if not base:
        m = _meta(twin)
        base = f"arch|{m.get('subject') or m.get('name')}|{arch.get('fingerprint') or fingerprint(twin)}"
    sig = filter_sig(f)
    return f"{base}|cut|{sig}" if sig else base


def person_id_for(kind_id: str, sig: str = '') -> str:
    """The stable id of the person who stands for an archetype (or a
    split or merged part of one) in rooms on the whole audience, or on
    one cut of it (``sig``)."""
    return f"{kind_id}@{sig}" if sig else str(kind_id)


def ensure_archetypes(twin: dict, *, models=None, usage_extras: Optional[dict] = None, force: bool = False) -> dict:
    """The kinds on a twin, computed on first use: the room's model
    helper when one is given or available, the deterministic kinds in
    REGRESSION_TEST_MODE or when every call fails."""
    have = (twin or {}).get('archetypes')
    if isinstance(have, dict) and have.get('groups') and not force and have.get('fingerprint') == fingerprint(twin) and int(have.get('version') or 0) >= ARCHETYPE_VERSION:
        return have
    call = None
    budget = 300.0
    try:
        from migration import digital_twin_room as ROOM
        budget = float((ROOM.BUDGET_S or {}).get('archetype') or budget)
        if models is not None or not ROOM.regression_mode():
            M = models or ROOM.Models(usage_extras=usage_extras)

            def call(system, user, max_tokens, temperature):
                try:
                    data, _rec = M.call('archetype', system, user, max_tokens=max_tokens, temperature=temperature, tools=None, stream=True)
                except TypeError:
                    data, _rec = M.call('archetype', system, user, max_tokens=max_tokens, temperature=temperature, tools=None)
                return data
    except Exception:
        call = None
    if call is None:
        return derive_archetypes(twin, call=None, force=force)
    # the desk answers on a shadow of the twin under a wall budget: a stalled request can never hold a build or a room open,
    # and an answer that lands after the budget is dropped rather than written over the kinds already in use
    shadow = copy.deepcopy(twin)
    box: dict = {}

    def _run():
        try:
            box['arch'] = derive_archetypes(shadow, call=call, force=force)
        except Exception as e:
            box['err'] = e
    th = threading.Thread(target=_run, name='twin-archetype-desk', daemon=True)
    t0 = time.time()
    th.start()
    th.join(max(1.0, budget))
    arch = box.get('arch')
    if th.is_alive() or not isinstance(arch, dict) or not arch.get('groups'):
        why = f"did not answer within {budget:.0f}s" if th.is_alive() else f"failed: {str(box.get('err') or 'no kinds')[:120]}"
        print(f"[archetypes] the archetype desk {why}; the deterministic archetypes stand and the desk runs at the next open")
        return derive_archetypes(twin, call=None, force=True)
    twin['archetypes'] = arch
    print(f"[archetypes] {arch.get('k')} archetypes ({arch.get('method')}) in {time.time() - t0:.1f}s")
    return arch


def public_groups(arch: dict) -> list:
    """The reader's view of the kinds: label, share, people, the person
    who stands for them and the plain copy. No profile, no dials, no
    trail."""
    out = []
    for g in ((arch or {}).get('groups') or []):
        rep = g.get('representative') or {}
        out.append({'id': g.get('id'), 'label': g.get('label'), 'share_pct': g.get('share_pct'), 'people': g.get('people'),
                    'who': g.get('who'), 'relationship': g.get('relationship'), 'decides': g.get('decides'),
                    'moves_them': g.get('moves_them'), 'holds_them_back': g.get('holds_them_back'),
                    'representative': {'name': rep.get('name'), 'age': rep.get('age'), 'city': rep.get('city'), 'gender': rep.get('gender_label'),
                                       'occupation': rep.get('occupation'), 'income': rep.get('income')}})
    return out


def archetype_lines(twin: dict, limit: int = 16) -> list:
    """Plain lines for the twin page, the notice and the chat: one per
    kind, largest first. 'Devoted regulars, 35 to 54: 200,114 people
    (20.0%). Harold, 54, New York.'"""
    arch = (twin or {}).get('archetypes') or {}
    noun = arch.get('noun') or 'people'
    out = []
    for g in (arch.get('groups') or [])[:limit]:
        rep = g.get('representative') or {}
        who = f" {rep.get('name')}, {rep.get('age')}, {rep.get('city')}." if rep.get('name') else ''
        out.append(f"{g.get('label')}: {int(g.get('people') or 0):,} {noun} ({float(g.get('share_pct') or 0):.1f}%).{who}")
    return out


def archetype_summary_line(twin: dict) -> str:
    arch = (twin or {}).get('archetypes') or {}
    groups = arch.get('groups') or []
    if not groups:
        return ''
    noun = arch.get('noun') or 'people'
    size = int(arch.get('size_people') or 0)
    top = groups[0]
    # Jenna 2026-11-20: an archetype is called an archetype, never a kind
    return (f"{len(groups)} archetypes make up the {size:,} {noun} in this audience; the largest is {str(top.get('label') or '').lower()} "
            f"at {float(top.get('share_pct') or 0):.1f}% ({int(top.get('people') or 0):,} {noun}).")


# ------------------------------------------------------------------ rooms
def _kind_as_twin(twin: dict, kind: dict) -> dict:
    """A kind read as a twin, so the panel's own filter arithmetic can
    say how much of it a cohort filter keeps."""
    lc = {STAGE_LC_KEY[s]: (100.0 if s == kind.get('stage') else 0.0) for s in STAGES}
    return {'meta': dict(_meta(twin)), 'identity': {'demographics': kind.get('profile') or {}, 'geography': ((twin or {}).get('identity') or {}).get('geography') or [],
                                                     'place_scoped': ((twin or {}).get('identity') or {}).get('place_scoped')}, 'lifecycle': lc}


def _age_overlap(label: str, lo: int, hi: int) -> float:
    """The part of an age row inside [lo, hi] (a row half inside counts
    half)."""
    lab = str(label or '')
    m = re.match(r"\s*(\d{2})\s*[-\u2013]\s*(\d{2})", lab)
    if m:
        a, z = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r"\s*(\d{2})\s*(?:\+|or older|and over)", lab, re.I)
        if not m:
            return 0.0
        a, z = int(m.group(1)), 84
    if z < a:
        return 0.0
    inter = max(0.0, min(z, hi) - max(a, lo) + 1)
    return clamp(inter / float(z - a + 1), 0.0, 1.0)


def _restrict(rows: list, weight_fn) -> tuple:
    """(kept share of the rows, the rows re-weighted by ``weight_fn`` and
    renormalized). Rows that weigh nothing drop. (0, rows) when nothing
    is kept; (1, rows) when the category has no rows."""
    rows = [r for r in (rows or []) if r.get('label')]
    if not rows:
        return 1.0, rows
    tot = sum(max(0.0, float(r.get('pct') or 0)) for r in rows) or 1.0
    kept = []
    hit = 0.0
    for r in rows:
        w = clamp(float(weight_fn(str(r.get('label') or ''))), 0.0, 1.0)
        if w > 0:
            kept.append(dict(r, pct=float(r.get('pct') or 0) * w))
            hit += float(r.get('pct') or 0) * w
    if not kept:
        return 0.0, rows
    return hit / tot, _norm_rows(kept)


def condition_kind(twin: dict, kind: dict, f: Optional[dict]) -> tuple:
    """A cohort filter on one kind: (the part of the kind the filter
    keeps, the kind's profile restricted to it). A kind whose rows for a
    filtered category none of which match keeps nothing; a kind with no
    rows for the category is untouched. Uses the room's own filter
    vocabulary (``digital_twin_panel.parse_segment``)."""
    from migration import digital_twin_panel as P
    f = dict(f or {})
    prof = {k: list(v) for k, v in (kind.get('profile') or {}).items()}
    keep = 1.0

    def apply(cat, weight_fn):
        nonlocal keep
        frac, rows = _restrict(prof.get(cat) or [], weight_fn)
        if prof.get(cat):
            keep *= frac
            prof[cat] = rows
    demos = f.get('demos') if isinstance(f.get('demos'), dict) else {}
    if demos:
        try:
            from migration import digital_twin_demos as D
            for d in D.DEMOS:
                vals = demos.get(d['key'])
                if not vals or d['key'] in ('PRIMARY_LANGUAGE', 'NUMBER_OF_CHILDREN', 'AGE_OF_CHILDREN'):
                    continue
                pred = D.value_pred(d['key'], vals)
                apply(d['cat'], lambda lab, pred=pred: 1.0 if pred(lab) else 0.0)
        except Exception:
            pass
    if f.get('gender') in ('F', 'M'):
        pred = P._gender_pred(f['gender'])
        apply('gender', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('age'):
        lo, hi = f['age'] if isinstance(f['age'], (list, tuple)) else (18, 84)
        apply('age', lambda lab: _age_overlap(lab, int(lo), int(hi)))
    if f.get('lifecycle'):
        want = {'lapsed': 'lapsing', 'lapsing': 'lapsing', 'new': 'discovering', 'discovering': 'discovering', 'trying': 'trying', 'habit': 'habit',
                'returning': 'returning', 'avid': 'habit', 'casual': None}.get(f['lifecycle'])
        if f['lifecycle'] == 'avid':
            keep *= 1.0 if (kind.get('stage') == 'habit' and kind.get('intensity') == 'devoted') else 0.04
        elif f['lifecycle'] == 'casual':
            keep *= 0.1 if (kind.get('stage') == 'habit' and kind.get('intensity') == 'devoted') else 1.0
        elif want:
            keep *= 1.0 if kind.get('stage') == want else 0.04
    if f.get('parent') is not None:
        pred = P._parent_pred(bool(f['parent']))
        apply('parental', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('ethnicity'):
        pred = P._eth_pred(f['ethnicity'])
        apply('ethnicity', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('income_min') or f.get('income_max'):
        apply('income', P._income_overlap(float(f.get('income_min') or 0), float(f.get('income_max') or 10_000_000)))
    if f.get('college') is not None:
        pred = P._college_pred(bool(f['college']))
        apply('education', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('relationship'):
        pred = P._rel_pred(str(f['relationship']))
        apply('relationship', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('lgbtq'):
        apply('sexual_orientation', lambda lab: 1.0 if P._lgbtq_pred(lab) else 0.0)
    if f.get('party'):
        pred = P._party_pred(str(f['party']))
        apply('party', lambda lab: 1.0 if pred(lab) else 0.0)
    if f.get('urbanicity'):
        pred = P._urban_pred(str(f['urbanicity']))
        apply('urbanicity', lambda lab: 1.0 if pred(lab) else 0.0)
    return clamp(keep, 0.0, 1.0), prof


def seat_archetypes(twin: dict, arch: dict, f: Optional[dict], people: int, seed: str, want: Optional[int] = None, max_seats: int = 40) -> list:
    """The kinds inside one cohort, sized to its people: each kind's share
    of the cohort is its share of the twin times the part of it the
    filter keeps (read off the kind's own profile, which is restricted to
    the filter too, so a room of women seats women), kinds under
    MIN_SEGMENT_SHARE_PCT of a filtered cohort merge into their
    neighbors, and ``want`` seats are met by merging the smallest or
    splitting the largest. A room on the whole twin seats the twin's
    kinds as they are. Each returned kind carries ``people`` for this
    cohort."""
    groups = [dict(g) for g in ((arch or {}).get('groups') or [])]
    if not groups:
        return []
    f = {k: v for k, v in dict(f or {}).items() if v not in (None, '', [], {}) and k != 'city'}
    keep = []
    for g in groups:
        g['members'] = list(g.get('members') or [g['id']])
        g['member_shares'] = _member_shares(g)
        if f:
            try:
                frac, prof = condition_kind(twin, g, f)
            except Exception:
                frac, prof = 1.0, g.get('profile') or {}
            g = _scale_kind(g, frac)
            g['profile'] = prof
            if g['share_pct'] <= 0:
                continue
        keep.append(g)
    if not keep:
        keep = [dict(g, member_shares=_member_shares(g)) for g in groups]
    tot = sum(float(g['share_pct']) for g in keep) or 1.0
    keep = [_scale_kind(g, 100.0 / tot) for g in keep]
    if f:
        # inside a cohort the slivers fold (a kind under three percent of the cohort), as far down as MERGE_FLOOR; the room
        # lifts the seats back to at least twelve across its cohorts (digital_twin_panel._kinds_roster)
        cells = merge_small(keep, MIN_SEGMENT_SHARE_PCT, k_min=min(MERGE_FLOOR, len(keep)), k_max=min(MAX_K, max_seats))
    else:
        cells = merge_small(keep, 0.0, k_min=min(MIN_K, len(keep)), k_max=min(MAX_K, max_seats))
    if want:
        cells = fit_count(cells, int(min(want, max_seats)), seed)
    cells = _settle_people(cells, int(max(0, people)), f"{seed}|seg")
    return cells


_ROSTER_CATS = (('gender', 'gender_label'), ('age', 'age_label'), ('sexual_orientation', 'orientation'), ('education', 'education'), ('income', 'income'), ('ethnicity', 'ethnicity'),
                ('relationship', 'relationship'), ('parental', 'parental'), ('occupation', 'occupation'), ('party', 'party'), ('urbanicity', 'urbanicity'))


def _quota_counts(targets: dict, n: int) -> dict:
    """Largest-remainder seats per label from people-weighted shares."""
    tot = sum(max(0.0, v) for v in targets.values()) or 1.0
    quota = {k: max(0.0, v) / tot * n for k, v in targets.items()}
    out = {k: int(q) for k, q in quota.items()}
    rem = {k: quota[k] - out[k] for k in quota}
    left = n - sum(out.values())
    for k in sorted(quota, key=lambda kk: (-rem[kk], -quota[kk], str(kk)))[:max(0, left)]:
        out[k] += 1
    return out


def _rows_for(kinds: list, cat: str) -> dict:
    """{kind id: {label: share}} for one category, prefer-not-to-say
    rows dropped (they never take a seat), zero rows dropped."""
    rows_by = {}
    for k in kinds:
        rows = [r for r in ((k.get('profile') or {}).get(cat) or []) if r.get('label') and float(r.get('pct') or 0) > 0]
        soft = [r for r in rows if 'prefer' not in str(r.get('label') or '').lower()]
        rows_by[k['id']] = {str(r.get('label')): max(0.0, float(r.get('pct') or 0)) for r in (soft or rows)}
    return rows_by


def _assign_within(kids: list, rows_by: dict, people: dict, cat: str, seed: str, margin: Optional[dict] = None) -> dict:
    """The quota over one set of kinds. The room's seats per label are
    the people-weighted quota of every kind's own rows; a kind that is
    one thing (a row at PIN_HARD, or at PIN_SHARE and well above the
    audience's own share of it, so the audience's majority label never
    pins every kind) takes that label outright and uses up one of its
    seats; the seats left go to the free kinds whose profile carries the
    label most (ties broken by a salted draw per category, so two
    categories never land on the same kinds by accident of size). Only a
    kind that carries a label can seat it."""
    out = {}
    free = []
    margin = margin or {}

    def _share(kid, lab):
        rows = rows_by.get(kid) or {}
        return rows.get(lab, 0.0) / (sum(rows.values()) or 1.0)

    targets = {}
    for kid in kids:
        rows = rows_by.get(kid) or {}
        tot = sum(rows.values()) or 1.0
        for lab, v in rows.items():
            targets[lab] = targets.get(lab, 0.0) + people[kid] * v / tot
    if not targets:
        return {kid: '' for kid in kids}
    counts = _quota_counts(targets, len(kids))
    for kid in kids:
        rows = rows_by.get(kid) or {}
        tot = sum(rows.values()) or 1.0
        pinned = None
        for lab, v in rows.items():
            share = v / tot * 100.0
            base = float(margin.get(lab, 0.0) or 0.0)
            if share >= PIN_HARD or (share >= PIN_SHARE and share >= base * PIN_OVER):
                pinned = lab
                break
        if pinned is not None:
            out[kid] = pinned
            counts[pinned] = max(0, counts.get(pinned, 0) - 1)      # the pin uses one of its label's seats
        else:
            free.append(kid)
    for lab in sorted(counts, key=lambda l: (-counts[l], -targets.get(l, 0.0), l)):
        need = counts[lab]
        while need > 0 and free:
            # only a kind that carries the label can seat it (a kind of people under 35 never seats a 70-year-old because the
            # quota rounded 65+ up by one); a seat the carrying kinds cannot fill falls to the free kinds' own top labels below
            carrying = [kid for kid in free if _share(kid, lab) > 0.005]
            if not carrying:
                break
            best = max(carrying, key=lambda kid: (round(_share(kid, lab), 4), unit('tie', seed, cat, kid, lab), kid))
            out[best] = lab
            free.remove(best)
            need -= 1
    for kid in free:
        rows = rows_by.get(kid) or {}
        out[kid] = max(rows, key=lambda l: rows[l]) if rows else ''
    return out


def assign_labels(kinds: list, cat: str, seed: str, groups: Optional[dict] = None, margin: Optional[dict] = None) -> dict:
    """{kind id: label} for one demographic category across the seats of
    a room. The room's labels are a people-weighted quota of the kinds'
    own profiles (twelve kinds of a 72% female audience seat about nine
    women); a kind that is one thing (a row at PIN_SHARE or more: a kind
    of gay men, of trans women) takes that label outright; the rest goes
    to the kinds whose profile carries it most. With ``groups`` ({kind
    id: group label}, the seats' genders) the quota runs inside each
    group from that group's own kinds, so who is LGBTQ+ among the men is
    read from the kinds that are men, never from a rule that fills one
    gender first (Jenna 2026-11-17). Deterministic; a kind with no rows
    gets ''."""
    rows_by = _rows_for(kinds, cat)
    if not any(rows_by.values()):
        return {k['id']: '' for k in kinds}
    people = {k['id']: max(1.0, float(k.get('people') or k.get('share_pct') or 1)) for k in kinds}
    if margin is None:
        # the audience's own share of each row: the people-weighted mean of the kinds (what the margin re-fit holds them to)
        margin = {}
        tot_p = sum(people.values()) or 1.0
        for kid, rows in rows_by.items():
            t = sum(rows.values()) or 1.0
            for lab, v in rows.items():
                margin[lab] = margin.get(lab, 0.0) + people[kid] / tot_p * v / t * 100.0
    if not groups:
        return _assign_within([k['id'] for k in kinds], rows_by, people, cat, seed, margin)
    out = {}
    by_group = {}
    for k in kinds:
        by_group.setdefault(str(groups.get(k['id']) or ''), []).append(k['id'])
    for g, kids in by_group.items():
        out.update(_assign_within(kids, rows_by, people, cat, f"{seed}|{g}", margin))
    return out


def roster_from_kinds(twin: dict, kinds: list, seed: str, start_seat: int = 1, person_sig: str = '') -> list:
    """One seat per kind: the kind's representative with the kind's
    people, in share order. Across the seats every demographic label is
    a people-weighted quota of the kinds' profiles (``assign_labels``), so
    the room mirrors the audience on gender, party, ethnicity and the
    rest while each seat still carries its own kind's tilt. Every seat
    carries ``person_id`` (Jenna 2026-11-20): the same person stands for
    an archetype in every room on the audience (``person_sig`` '') and
    in every room on the same cut of it; a seat that is the twin page's
    own representative carries that person's face."""
    from migration import digital_twin_panel as P
    roster = []
    stored = {str(g.get('id')): (g.get('representative') or {}) for g in (((twin or {}).get('archetypes') or {}).get('groups') or []) if isinstance(g, dict)}
    labels = {cat: assign_labels(kinds, cat, seed) for cat, _field in _ROSTER_CATS if cat != 'sexual_orientation'}
    # who is LGBTQ+ is read inside each gender from the kinds that carry that gender: a kind of gay men seats a gay man, a kind of
    # trans women a trans woman; nothing fills one gender first (Jenna 2026-11-17: "it should be based on the archetype")
    labels['sexual_orientation'] = assign_labels(kinds, 'sexual_orientation', seed, groups=labels.get('gender') or {})
    for i, k in enumerate(kinds):
        rep = representative(twin, k, seed, seat_no=start_seat + i)
        for cat, field in _ROSTER_CATS:
            lab = (labels.get(cat) or {}).get(k['id'])
            if lab:
                rep[field] = lab
        if rep.get('age_label'):
            # the room's ages are a people-weighted quota of the kinds' own age rows too; the seat's age sits inside its label
            rep['age'] = int(clamp(P._age_in_bucket(rep['age_label'], unit('agein', f"{seed}|rep|{k.get('id')}")), 18, 84))
        g = P._gender_code(rep.get('gender_label') or '')
        if g != rep.get('gender'):
            # the quota moved this seat's gender: the name follows it
            rep['gender'] = g
            bank = P._NAMES_F if g == 'F' else (P._NAMES_X if g == 'X' else P._NAMES_M)
            rep['name'] = bank[int(unit('name', f"{seed}|rep|{k.get('id')}", g) * len(bank))]
        if rep.get('age', 0) >= 66 and rep.get('occupation') and not re.search(r"retired", str(rep['occupation']), re.I) and unit('retire', seed, k['id']) < 0.7:
            rep['occupation'] = 'Retired'
        rep['people'] = int(k.get('people') or 0)
        rep['archetype'] = dict(rep.get('archetype') or {}, share_pct=k.get('share_pct'), people=k.get('people'))
        rep['person_id'] = person_id_for(str(k.get('id')), person_sig)
        own = stored.get(str(k.get('id'))) if not person_sig else None
        if isinstance(own, dict) and own.get('portrait_url') and all(str(own.get(f) or '') == str(rep.get(f) or '') for f in ('name', 'age', 'gender', 'city')):
            rep['portrait_url'] = own['portrait_url']
            if own.get('portrait_hash'):
                rep['portrait_hash'] = own['portrait_hash']
        roster.append(rep)
    return roster


__all__ = ['ARCHETYPE_VERSION', 'MIN_K', 'MAX_K', 'MERGE_FLOOR', 'MIN_SHARE_PCT', 'derive_archetypes', 'ensure_archetypes', 'public_groups', 'archetype_lines',
           'archetype_summary_line', 'seat_archetypes', 'roster_from_kinds', 'representative', 'fit_count', 'merge_small', 'skeleton', 'margins',
           'rep_seed', 'filter_sig', 'person_id_for',
           'fingerprint', 'noun_for', 'scrub_text', 'ARCHETYPE_SYSTEM']
