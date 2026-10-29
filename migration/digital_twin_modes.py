"""The screening modes on the Pilot Testing room (Digital Twins IQ, 2026-10-28).

One room, one engine (``digital_twin_pilot``), several things to test:

  trailer / spot   one to three assets, 15 seconds to three minutes. Interest
                   before (from the title and logline alone) and after, the
                   lift in definite interest, what they understood, what they
                   expect, the moments that sold it, who it is for, would
                   watch, recommend.
  title            up to six titles to the same room: appeal before the
                   logline, fit after, uniqueness, preference with reasons,
                   by quadrant.
  key_art          up to six images: preference, attention (the first thing
                   they look at), what it says the title is, interest lift.
  script           the script text split on sluglines (INT. / EXT.), read
                   scene by scene on a reading clock with a dial per scene
                   and story / structure / character notes; a recorded table
                   read runs the pilot path.
  concept          a logline, synopsis, one-sheet or deck text: interest,
                   uniqueness, who it is for, would watch, the strongest
                   element, the biggest doubt.
  episode          commitment after the pilot: the room knows the prior cut
                   when the lineage points to it.
  binge            cumulative build across episodes in one test.
  rough / locked   a rough cut is read with temp music and missing effects
                   in mind; a locked cut turns on the marketing read
                   (urgency, who it is for, what to put in the trailer).

Everything here is deterministic per (twin, seat, stimulus) when no model
is on (the regression path) and moves from a model card when one is.
Counts keep messy last digits; shares are never pinned. User-facing copy
never names how the room is produced.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from migration.digital_twin_engine import clamp, jit, messy_count, pct1, unit
from migration.digital_twin_pilot import (
    QUADRANTS, _card_for, _clock, _publish, _question_result, _seat_people, _seat_score, _trait, _battery_prior,
    quadrant_of, norms_for, format_for,
)

MODE_SPECS = {
    'pilot':   {'label': 'Pilot Testing', 'tab': 'pilot', 'stimulus': 'video', 'max_assets': 1, 'noun': 'the cut'},
    'feature': {'label': 'Feature screening', 'tab': 'pilot', 'stimulus': 'video', 'max_assets': 1, 'noun': 'the film'},
    'episode': {'label': 'Episode test', 'tab': 'pilot', 'stimulus': 'video', 'max_assets': 1, 'noun': 'the episode'},
    'binge':   {'label': 'Binge test', 'tab': 'pilot', 'stimulus': 'video', 'max_assets': 6, 'noun': 'the episodes'},
    'trailer': {'label': 'Trailer test', 'tab': 'trailer', 'stimulus': 'video', 'max_assets': 3, 'noun': 'the trailer', 'min_s': 15, 'max_s': 180},
    'spot':    {'label': 'TV spot test', 'tab': 'trailer', 'stimulus': 'video', 'max_assets': 3, 'noun': 'the spot', 'min_s': 15, 'max_s': 180},
    'title':   {'label': 'Title test', 'tab': 'titles', 'stimulus': 'options', 'max_options': 6, 'noun': 'the titles'},
    'key_art': {'label': 'Key art test', 'tab': 'titles', 'stimulus': 'images', 'max_options': 6, 'noun': 'the art'},
    'script':  {'label': 'Script read', 'tab': 'script', 'stimulus': 'text', 'max_assets': 1, 'noun': 'the script'},
    'concept': {'label': 'Concept test', 'tab': 'concept', 'stimulus': 'text', 'max_assets': 0, 'noun': 'the idea'},
}
TAB_MODES = {'pilot': ('pilot', 'feature', 'episode', 'binge'), 'trailer': ('trailer', 'spot'), 'titles': ('title', 'key_art'), 'script': ('script',), 'concept': ('concept',)}
MAX_OPTIONS = 6
MAX_TRAILER_ASSETS = 3
MAX_BINGE_ASSETS = 6
ALLOWED_IMAGE_EXT = frozenset({'.jpg', '.jpeg', '.png', '.webp'})
ALLOWED_TEXT_EXT = frozenset({'.txt', '.pdf', '.fountain', '.fdx', '.md'})
READ_WPS = 2.6              # words a second on the reading clock (a script page runs close to a minute)
OPTION_LOOK_S = {'title': 8.0, 'key_art': 12.0}
LIFT_LABELS = (('a', 'Definitely interested', 'yes'), ('b', 'Somewhat interested', 'maybe'), ('c', 'Not very interested', 'no'), ('d', 'Not at all', 'no'))

# the mode cards (what each seat answers once at the end, beyond the standard battery)
MODE_CARDS = {
    'trailer': [
        ('interest_before', 'Interest from the title and logline alone', 'interest', list(LIFT_LABELS)),
        ('interest_after', 'Interest after the trailer', 'interest', list(LIFT_LABELS)),
        ('would_watch', 'Would watch or see it', 'yes_no', [('a', 'Yes', 'yes'), ('b', 'No', 'no')]),
        ('who_gender', 'Who it is for', 'who', [('a', 'Women', 'maybe'), ('b', 'Men', 'maybe'), ('c', 'Both', 'yes')]),
        ('who_age', 'Which age it is for', 'who', [('a', 'Under 35', 'maybe'), ('b', '35 and over', 'maybe'), ('c', 'Everyone', 'yes')]),
    ],
    'title': [
        ('appeal', 'Appeal from the title alone', 'appeal', [('a', 'Love it', 'yes'), ('b', 'Like it', 'yes'), ('c', 'Neutral', 'maybe'), ('d', 'Dislike it', 'no')]),
        ('fit', 'Fit once the logline is read', 'fit', [('a', 'Fits well', 'yes'), ('b', 'Fits somewhat', 'maybe'), ('c', 'Does not fit', 'no')]),
        ('unique', 'Stands out', 'unique', [('a', 'Stands out', 'yes'), ('b', 'Familiar', 'maybe'), ('c', 'Generic', 'no')]),
    ],
    'key_art': [
        ('appeal', 'Appeal of the art', 'appeal', [('a', 'Love it', 'yes'), ('b', 'Like it', 'yes'), ('c', 'Neutral', 'maybe'), ('d', 'Dislike it', 'no')]),
        ('attention', 'Holds the eye', 'attention', [('a', 'Stopped me', 'yes'), ('b', 'Looked twice', 'maybe'), ('c', 'Scrolled past', 'no')]),
        ('interest_before', 'Interest from the title alone', 'interest', list(LIFT_LABELS)),
        ('interest_after', 'Interest after seeing the art', 'interest', list(LIFT_LABELS)),
    ],
    'concept': [
        ('interest', 'Interest in the idea', 'interest', list(LIFT_LABELS)),
        ('unique', 'Stands out', 'unique', [('a', 'Stands out', 'yes'), ('b', 'Familiar', 'maybe'), ('c', 'Generic', 'no')]),
        ('would_watch', 'Would watch it', 'yes_no', [('a', 'Yes', 'yes'), ('b', 'No', 'no')]),
        ('who_gender', 'Who it is for', 'who', [('a', 'Women', 'maybe'), ('b', 'Men', 'maybe'), ('c', 'Both', 'yes')]),
        ('who_age', 'Which age it is for', 'who', [('a', 'Under 35', 'maybe'), ('b', '35 and over', 'maybe'), ('c', 'Everyone', 'yes')]),
        ('strongest', 'The strongest element', 'pick', [('a', 'The premise', 'yes'), ('b', 'The world', 'yes'), ('c', 'The lead', 'yes'), ('d', 'The tone', 'yes'), ('e', 'The hook at the end', 'yes')]),
        ('doubt', 'The biggest doubt', 'pick', [('a', 'Too familiar', 'no'), ('b', 'Hard to follow', 'no'), ('c', 'Not for me', 'no'), ('d', 'Needs a name attached', 'no'), ('e', 'Unclear who it is for', 'no')]),
    ],
    'episode': [
        ('commit', 'Would keep going with the series', 'yes_no', [('a', 'Yes', 'yes'), ('b', 'No', 'no')]),
        ('versus_prior', 'Against the last one', 'expectations', [('a', 'Better than the last one', 'yes'), ('b', 'About the same', 'maybe'), ('c', 'Not as good', 'no')]),
    ],
    'locked': [
        ('who_gender', 'Who it is for', 'who', [('a', 'Women', 'maybe'), ('b', 'Men', 'maybe'), ('c', 'Both', 'yes')]),
        ('who_age', 'Which age it is for', 'who', [('a', 'Under 35', 'maybe'), ('b', '35 and over', 'maybe'), ('c', 'Everyone', 'yes')]),
    ],
}
MODE_CARD_SYSTEM = """You are the people in a test-screening room, filling out the card after the stimulus (a trailer, a set of titles, key art, a script, a concept). For each person you have their card (who they are), the stimulus (title, logline, the options or the text, what they saw), and how their own dial ran. Answer each question ONCE per person, in character and consistent with their dial and what they said. Interest BEFORE is from the title and logline alone, as if they had not seen anything yet; interest AFTER is after what they saw. Spread honestly; real rooms are not unanimous. Then one sentence per person, spoken, on why (under 22 words, no analysis words).
Return JSON only: {"seats": [{"seat": <id>, "answers": {"<question id>": "<option id>"}, "why": "<one sentence>"}]}"""
OPTIONS_SYSTEM = """You are the people in a test-screening room, looking at a set of options for one title (titles or key art). For each person you have their card. For each option you have its label and, for art, what is on it. For EVERY person: rate every option (appeal: a love it, b like it, c neutral, d dislike; for titles also fit to the logline: a fits well, b somewhat, c no; and unique: a stands out, b familiar, c generic; for art also attention: a stopped me, b looked twice, c scrolled past), pick ONE preferred option, and give one short spoken reason for the pick (under 16 words, first person, concrete). Real rooms split; do not let everyone pick the same one.
Return JSON only: {"seats": [{"seat": <id>, "ratings": {"<option id>": {"appeal": "a", "fit": "a", "unique": "b", "attention": "a"}}, "pick": "<option id>", "reason": "..."}]}"""
ART_SYSTEM = """You are the eyes of a test-screening room, shown one piece of key art at a time. For each image write what is on it in one breath (who or what, the pose, the setting, the mood, the words on it), the first thing the eye lands on, and what kind of title it says this is (one of: comedy, drama, action, horror, thriller, romance, family, animation, documentary, unscripted). Never name a real actor. No em dashes.
Return JSON only: {"images": [{"k": 1, "caption": "...", "attention": "...", "says": "<genre>", "text_on_art": "..."}]}"""

_SLUG_RX = re.compile(r"^\s*(?:\d+[A-Z]?\s+)?(INT\.?|EXT\.?|INT\.?/EXT\.?|EXT\.?/INT\.?|I/E\.?|E/I\.?)\s+(.+?)\s*(?:\d+[A-Z]?)?\s*$", re.I)
_CUE_RX = re.compile(r"^\s{0,}([A-Z][A-Z0-9 .'\-]{1,30})(?:\s*\(.*\))?\s*$")
_PAREN_RX = re.compile(r"^\s*\(.*\)\s*$")
_TRANSITION_RX = re.compile(r"^\s*(CUT TO|FADE (IN|OUT|TO)|DISSOLVE TO|SMASH CUT|MATCH CUT|INTERCUT|END OF (ACT|EPISODE|SHOW)|TITLE CARD)[:.]?\s*$", re.I)
_SENT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_GENRE_WORDS = {
    'comedy': re.compile(r"\b(comedy|sitcom|funny|laugh|joke|romcom|rom-com|wry|satire)\b", re.I),
    'thriller': re.compile(r"\b(thriller|suspense|crime|noir|mystery|detective|conspiracy|heist)\b", re.I),
    'horror': re.compile(r"\b(horror|haunt|ghost|slasher|possess|scare|terror)\b", re.I),
    'action': re.compile(r"\b(action|war|battle|chase|fight|superhero|mission|assassin)\b", re.I),
    'romance': re.compile(r"\b(romance|love|wedding|affair|heart|falls for)\b", re.I),
    'drama': re.compile(r"\b(drama|family|grief|return|hometown|secret|scandal|redemption)\b", re.I),
    'family': re.compile(r"\b(kids|animated|animation|children|puppy|magic school)\b", re.I),
    'documentary': re.compile(r"\b(documentary|true story|real life|docuseries)\b", re.I),
    'unscripted': re.compile(r"\b(reality|competition|contestants|host|game show|dating show)\b", re.I),
}
_REASONS_TITLE = {
    'short': 'Short and easy to say.', 'genre': 'It tells me what kind of show it is.', 'name': 'A name I would remember.', 'number': 'The number makes it specific.',
    'place': 'It puts me somewhere right away.', 'familiar': 'Sounds like a show I already watch.', 'mystery': 'It makes me ask a question.', 'long': 'A mouthful, but it stuck.',
    'plain': 'Plain, but it fits the story.', 'bold': 'Bold. I would click it.',
}
_REASONS_ART = {
    'face': 'The face pulled me in first.', 'color': 'The color stopped me scrolling.', 'words': 'The line on it sold the idea.', 'mood': 'The mood matches what I want to watch.',
    'busy': 'Too much going on, but it had energy.', 'clean': 'Clean. I knew what it was in a second.', 'dark': 'Dark and moody, my kind of thing.', 'bright': 'Bright, warm, I would watch it with my family.',
}


def _now():
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def mode_of(session: dict) -> str:
    m = str((session or {}).get('mode') or 'pilot').strip().lower()
    return m if m in MODE_SPECS else 'pilot'


def tab_of(mode: str) -> str:
    return (MODE_SPECS.get(mode) or MODE_SPECS['pilot'])['tab']


def mode_label(mode: str) -> str:
    return (MODE_SPECS.get(mode) or MODE_SPECS['pilot'])['label']


def _genre_hint(text: str) -> str:
    blob = str(text or '')
    best, hits = '', 0
    for g, rx in _GENRE_WORDS.items():
        n = len(rx.findall(blob))
        if n > hits:
            best, hits = g, n
    return best


def _sid(session: dict) -> str:
    return str((session or {}).get('id') or 'test')


def _subject(twin: dict) -> str:
    m = (twin or {}).get('meta') or {}
    return str(m.get('subject') or m.get('name') or 'the twin')


# ------------------------------------------------------------ the text
def _clean_lines(text: str) -> list:
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return [ln.rstrip() for ln in text.split('\n')]


def script_scenes(text: str, *, wps: float = READ_WPS) -> dict:
    """Split a script on its sluglines. Returns {scenes: [{id, t_start,
    t_end, slug, action, dialogue, speaker, speakers, words, caption}],
    transcript: [{t, end, text, speaker}], runtime_s, pages, cast: [..]}.
    Without a single slugline the text reads as one scene a paragraph."""
    lines = _clean_lines(text)
    blocks = []
    cur = None
    for ln in lines:
        m = _SLUG_RX.match(ln)
        if m and len(ln.strip()) <= 90:
            if cur:
                blocks.append(cur)
            cur = {'slug': ' '.join((m.group(1).upper().rstrip('.') + '.', ' '.join(m.group(2).split()))).strip(), 'lines': []}
            continue
        if cur is None:
            cur = {'slug': '', 'lines': []}
        cur['lines'].append(ln)
    if cur:
        blocks.append(cur)
    if len([b for b in blocks if b['slug']]) == 0:
        # no sluglines: paragraphs are the scenes
        paras, buf = [], []
        for ln in lines:
            if ln.strip():
                buf.append(ln.strip())
            elif buf:
                paras.append(' '.join(buf))
                buf = []
        if buf:
            paras.append(' '.join(buf))
        blocks = [{'slug': '', 'lines': [p]} for p in paras if p] or [{'slug': '', 'lines': [str(text or '').strip() or 'The page is blank.']}]
    scenes, transcript, cast_count = [], [], {}
    t = 0.0
    for i, b in enumerate(blocks):
        action, dialogue, speakers = [], [], []
        speaker = None
        for ln in b['lines']:
            s = ln.strip()
            if not s or _TRANSITION_RX.match(s):
                continue
            if _PAREN_RX.match(s):
                continue
            cm = _CUE_RX.match(ln) if ln.startswith((' ', '\t')) or s.isupper() else None
            if cm and len(s.split()) <= 4 and s.upper() == s and not s.endswith('.') and not _SLUG_RX.match(s):
                speaker = ' '.join(cm.group(1).title().split())
                if speaker not in speakers:
                    speakers.append(speaker)
                cast_count[speaker] = cast_count.get(speaker, 0)
                continue
            if speaker and (ln.startswith((' ', '\t')) or not action or dialogue and ln.startswith(' ')):
                dialogue.append(f"{speaker}: {s}")
                cast_count[speaker] = cast_count.get(speaker, 0) + 1
                continue
            speaker = None
            action.append(s)
        words = sum(len(x.split()) for x in action) + sum(len(x.split(':', 1)[-1].split()) for x in dialogue)
        dur = max(6.0, words / max(0.5, wps))
        sid = i
        a_text = ' '.join(action)
        cap = (b['slug'] + (': ' if b['slug'] and a_text else '') + a_text)[:160] if (b['slug'] or a_text) else (dialogue[0][:160] if dialogue else 'A scene.')
        scenes.append({'id': sid, 't_start': round(t, 3), 't_end': round(t + dur, 3), 'slug': b['slug'], 'action': a_text[:600], 'dialogue': ' '.join(dialogue)[:900],
                       'speaker': speakers[0] if speakers else '', 'speakers': speakers[:8], 'words': words, 'caption': cap})
        # dialogue lines land on the clock in order
        if dialogue:
            step = dur / float(len(dialogue) + 1)
            for k, dl in enumerate(dialogue):
                sp, _c, txt = dl.partition(': ')
                transcript.append({'t': round(t + step * (k + 0.5), 3), 'end': round(t + step * (k + 1.5), 3), 'text': txt[:180], 'speaker': sp})
        t += dur
    cast = [n for n, _c in sorted(cast_count.items(), key=lambda kv: (-kv[1], kv[0]))][:12]
    return {'scenes': scenes, 'transcript': transcript, 'runtime_s': round(t, 3), 'pages': max(1, int(round(t / 60.0))), 'cast': cast, 'scene_count': len(scenes)}


def concept_scenes(text: str, *, wps: float = READ_WPS) -> dict:
    """A concept reads sentence by sentence on a short clock (each
    sentence is a beat the room reacts to)."""
    body = ' '.join(str(text or '').split())
    sents = [s.strip() for s in _SENT_RX.split(body) if s.strip()] or [body or 'The idea is a blank page.']
    sents = sents[:24]
    scenes, t = [], 0.0
    for i, s in enumerate(sents):
        dur = max(4.0, len(s.split()) / max(0.5, wps))
        scenes.append({'id': i, 't_start': round(t, 3), 't_end': round(t + dur, 3), 'caption': s[:160], 'action': s[:600], 'dialogue': '', 'speaker': '', 'words': len(s.split())})
        t += dur
    return {'scenes': scenes, 'transcript': [], 'runtime_s': round(max(t, 15.0), 3), 'sentences': len(sents)}


def option_scenes(options: list, mode: str) -> dict:
    """The options on a short clock: the room looks at each one in turn
    (eight seconds a title, twelve a piece of art)."""
    look = OPTION_LOOK_S.get(mode, 8.0)
    scenes, t = [], 0.0
    for i, o in enumerate(options[:MAX_OPTIONS]):
        cap = str(o.get('caption') or o.get('label') or f"Option {i + 1}")[:160]
        scenes.append({'id': i, 't_start': round(t, 3), 't_end': round(t + look, 3), 'caption': cap, 'action': cap, 'dialogue': '', 'speaker': '', 'option_id': o.get('id')})
        t += look
    return {'scenes': scenes, 'transcript': [], 'runtime_s': round(max(t, 15.0), 3)}


def captions_for_text_scenes(scenes: list, genre: str = '') -> dict:
    """Captions keyed by scene id so the beat sheet reads as grounded
    (the text is the cut)."""
    from migration.digital_twin_pilot import _tone_from_text
    out = {}
    for s in scenes:
        txt = f"{s.get('caption') or ''} {s.get('dialogue') or ''}"
        tone = _tone_from_text(txt, '')
        if not tone:
            tone = {'comedy': 'funny', 'horror': 'tense', 'thriller': 'tense', 'action': 'action', 'romance': 'tender', 'family': 'warm'}.get(genre or _genre_hint(txt), 'curious')
        out[int(s['id'])] = {'caption': str(s.get('caption') or '')[:160], 'tone': tone, 'joke': tone == 'funny' and bool(re.search(r"\b(laugh|joke|deadpan)\b", txt, re.I)),
                             'setting': str(s.get('slug') or '')[:40], 'people': len(s.get('speakers') or []), 'text': ''}
    return out


def normalize_options(raw, mode: str) -> list:
    """[{id, label, caption, logline?}] from a list of strings or dicts;
    at most six, blank ones dropped, stable ids a to f."""
    out = []
    for i, o in enumerate(list(raw or [])[:MAX_OPTIONS]):
        if isinstance(o, str):
            o = {'label': o}
        if not isinstance(o, dict):
            continue
        label = ' '.join(str(o.get('label') or o.get('title') or o.get('filename') or '').split())[:120]
        if not label:
            continue
        oid = str(o.get('id') or 'abcdef'[len(out)])
        row = {'id': oid, 'label': label, 'caption': str(o.get('caption') or '')[:160]}
        for k in ('s3_key', 'filename', 'content_type', 'bytes', 'hash', 'attention', 'says', 'text_on_art'):
            if o.get(k) is not None:
                row[k] = o[k]
        out.append(row)
    return out


# ------------------------------------------------------- the cold read
def cold_interest(card: dict, twin: dict, title: str, logline: str, seed: str) -> float:
    """Where a seat sits on the title and logline alone, 0 to 100: its
    habits, the genre words against who it is, salted. The 'before'."""
    loyal = _trait(twin, 'franchise_loyalty')
    trial = _trait(twin, 'trial_speed')
    wom = _trait(twin, 'word_of_mouth')
    age = int(card.get('age') or 35)
    gender = str(card.get('gender') or '')
    stage = str(card.get('lifecycle_stage') or '')
    g = _genre_hint(f"{title} {logline}")
    v = 48.0 + (loyal - 50) * 0.16 + (trial - 50) * 0.14 + (wom - 50) * 0.05
    if g == 'comedy':
        v += 6.0 if age < 40 else 2.0
    elif g in ('thriller', 'horror'):
        v += 5.0 if age < 50 else 0.5
    elif g == 'action':
        v += 6.0 if (gender == 'man' and age < 45) else 1.5
    elif g == 'romance':
        v += 6.5 if gender in ('woman', 'nonbinary') else 1.0
    elif g == 'family':
        v += 4.0 if 'parent' in ' '.join(card.get('habits') or []).lower() else 0.0
    elif g == 'drama':
        v += 3.5 if age >= 35 else 1.5
    if stage == 'in the habit':
        v += 4.2
    elif stage == 'lapsing':
        v -= 4.8
    elif stage == 'discovering':
        v += 1.2
    words = len(str(logline or '').split())
    if 8 <= words <= 40:
        v += 1.6
    elif words > 90:
        v -= 1.2
    return clamp(v + jit(-6.0, 6.0, seed, 'cold'), 3.0, 97.0)


def _interest_pick(v: float, opts: list) -> str:
    ids = [o[0] for o in opts]
    if len(ids) == 4:
        return ids[0] if v >= 58 else ids[1] if v >= 46 else ids[2] if v >= 34 else ids[3]
    if len(ids) == 3:
        return ids[0] if v >= 58 else ids[1] if v >= 42 else ids[2]
    return ids[0] if v >= 54 else ids[-1]


def _who_pick(card: dict, s: float, seed: str, which: str) -> str:
    """Who a seat says it is for: from how it landed for them against
    their own quadrant, with a lean to 'both / everyone' when it landed
    broadly."""
    u = unit(seed, 'who', which)
    if s >= 62 and u < 0.55:
        return 'c'
    if which == 'gender':
        return 'a' if str(card.get('gender') or '') in ('woman', 'nonbinary') else 'b'
    return 'a' if int(card.get('age') or 35) < 35 else 'b'


def _sig(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


GRADE_SPREAD = 6.5     # points of dial a seat's grade is uncertain over (no room is unanimous)


def ordered_probs(score: float, cuts: list, ids: list, spread: float = GRADE_SPREAD) -> dict:
    """Probabilities over ordered options (best first) from a 0 to 100
    score and the cut points between the grades (descending)."""
    k = len(ids)
    if k == 1:
        return {ids[0]: 1.0}
    cdf = [_sig((score - c) / spread) for c in cuts[:k - 1]]
    out = {}
    for i, oid in enumerate(ids):
        if i == 0:
            out[oid] = cdf[0]
        elif i < k - 1:
            out[oid] = max(0.0, cdf[i] - cdf[i - 1])
        else:
            out[oid] = max(0.0, 1.0 - cdf[k - 2])
    tot = sum(out.values()) or 1.0
    return {o: v / tot for o, v in out.items()}


def softmax_probs(scores: dict, temperature: float = 4.0) -> dict:
    import math
    if not scores:
        return {}
    m = max(scores.values())
    ex = {k: math.exp((v - m) / max(0.1, temperature)) for k, v in scores.items()}
    tot = sum(ex.values()) or 1.0
    return {k: v / tot for k, v in ex.items()}


def blend_pick(probs: dict, pick: Optional[str], weight: float = 0.7) -> dict:
    """A model's discrete pick over the seat's own graded leaning: the
    pick carries most of the mass, the leaning keeps the room honest."""
    if not pick or pick not in probs:
        return probs
    out = {k: v * (1.0 - weight) for k, v in probs.items()}
    out[pick] = out.get(pick, 0.0) + weight
    tot = sum(out.values()) or 1.0
    return {k: v / tot for k, v in out.items()}


def argmax(probs: dict) -> str:
    return max(probs.items(), key=lambda kv: kv[1])[0] if probs else ''


def publish_probs(seat_probs: dict, options: list, roster: list, segments: list, seed: str) -> list:
    """Rows [{id, label, lean, pct, people, seats_n, tally}] from every
    seat's probabilities weighted by the people the seat stands for.
    Shares to one decimal summing to 100, no 0.0%, messy people."""
    people_by_seat = _seat_people(roster, segments)
    ids = [o[0] for o in options]
    acc = {i: 0.0 for i in ids}
    tally = {i: 0 for i in ids}
    wsum = 0.0
    n = 0
    for r in roster:
        sno = int(r['seat'])
        p = seat_probs.get(sno)
        if not p:
            continue
        n += 1
        w = float(people_by_seat.get(sno) or 1)
        wsum += w
        for i in ids:
            acc[i] += w * float(p.get(i, 0.0))
        tally[max(ids, key=lambda i: p.get(i, 0.0))] += 1
    if wsum <= 0:
        return []
    total_people = int(sum(people_by_seat.get(int(r['seat']), 0) for r in roster if int(r['seat']) in seat_probs) or n * 800)
    vals = {i: max(0.004, acc[i] / wsum + jit(-0.006, 0.006, seed, 'share', i)) for i in ids}
    tot = sum(vals.values()) or 1.0
    vals = {i: v / tot for i, v in vals.items()}
    rows = []
    for oid, lab, lean in options:
        rows.append({'id': oid, 'label': lab, 'lean': lean, 'pct': round(vals[oid] * 100.0, 1), 'people': messy_count(seed, f"ppl-{oid}", total_people * vals[oid]),
                     'seats_n': tally[oid], 'tally': f"{tally[oid]} of {n}"})
    diff = round(100.0 - sum(r['pct'] for r in rows), 1)
    if abs(diff) >= 0.05:
        top = max(rows, key=lambda r: r['pct'])
        top['pct'] = round(top['pct'] + diff, 1)
    return rows


CUTS = {'interest4': (58.0, 46.0, 34.0), 'interest3': (58.0, 42.0), 'appeal': (62.0, 52.0, 42.0), 'fit': (58.0, 42.0), 'unique': (60.0, 44.0), 'attention': (62.0, 46.0),
        'yes_no': (57.0,), 'expectations': (62.0, 44.0), 'rating': (58.0, 52.0, 46.0, 40.0)}


def seat_probs_for(mode_qs: list, card: dict, twin: dict, series: list, seed: str, *, cold: float, hot: Optional[float] = None, prior: Optional[dict] = None) -> dict:
    """Every question's probabilities for one seat on the deterministic
    card (its own score, graded with spread, never a hard cut)."""
    s = hot if hot is not None else _seat_score(card, twin, series, seed)
    out = {}
    for qid, _label, kind, opts in mode_qs:
        ids = [o[0] for o in opts]
        if qid == 'interest_before':
            out[qid] = ordered_probs(cold, CUTS['interest4' if len(ids) == 4 else 'interest3'], ids)
        elif qid in ('interest_after', 'interest'):
            out[qid] = ordered_probs(s + jit(-2.0, 2.0, seed, qid), CUTS['interest4' if len(ids) == 4 else 'interest3'], ids)
        elif qid in ('would_watch', 'commit'):
            p = _sig((s + jit(-3.0, 3.0, seed, qid) - CUTS['yes_no'][0]) / GRADE_SPREAD)
            out[qid] = {ids[0]: p, ids[-1]: 1.0 - p}
        elif qid in ('who_gender', 'who_age'):
            broad = 0.18 + 0.52 * _sig((s - 58.0) / 7.0)
            own = 'a' if (str(card.get('gender') or '') in ('woman', 'nonbinary') if qid == 'who_gender' else int(card.get('age') or 35) < 35) else 'b'
            other = 'b' if own == 'a' else 'a'
            out[qid] = {'c': broad, own: (1.0 - broad) * 0.72, other: (1.0 - broad) * 0.28}
        elif qid == 'unique':
            out[qid] = ordered_probs(s + jit(-5.0, 5.0, seed, 'unique'), CUTS['unique'], ids)
        elif qid == 'appeal':
            out[qid] = ordered_probs(s, CUTS['appeal'], ids)
        elif qid == 'fit':
            out[qid] = ordered_probs(s + jit(-3.0, 3.0, seed, 'fit'), CUTS['fit'], ids)
        elif qid == 'attention':
            out[qid] = ordered_probs(s + jit(-3.0, 3.0, seed, 'attention'), CUTS['attention'], ids)
        elif qid in ('strongest', 'doubt'):
            scores = {oid: 50.0 + jit(-9.0, 9.0, seed, qid, oid) for oid in ids}
            if qid == 'doubt' and s >= 66:
                scores['a'] = scores.get('a', 50.0) + 4.0
                scores['d'] = scores.get('d', 50.0) + 3.0
            out[qid] = softmax_probs(scores, temperature=5.0)
        elif qid == 'versus_prior':
            d = s - float((prior or {}).get('end') or 50.0)
            out[qid] = ordered_probs(50.0 + d * 1.6, (54.0, 46.0), ids)
        else:
            out[qid] = {oid: 1.0 / len(ids) for oid in ids}
    return out


def _fallback_mode_answers(mode_qs: list, card: dict, twin: dict, series: list, seed: str, *, cold: float, hot: Optional[float] = None, prior: Optional[dict] = None) -> dict:
    """The deterministic card for one seat as picks: the top grade of
    its graded leanings (``seat_probs_for``)."""
    probs = seat_probs_for(mode_qs, card, twin, series, seed, cold=cold, hot=hot, prior=prior)
    return {qid: argmax(p) for qid, p in probs.items()}
def _publish_card(qs: list, seat_probs: dict, roster: list, segments: list, *, subject: str, session_seed: str, tag: str) -> dict:
    """Publish a card's questions for the whole room and every quadrant
    from every seat's probabilities."""
    people_by_seat = _seat_people(roster, segments)
    room_people = sum(int(sg.get('people') or 0) for sg in segments) or sum(people_by_seat.values()) or len(roster) * 800

    def run(seat_ids, seed_tag):
        out = {}
        for qid, qlabel, kind, opts in qs:
            sp = {sno: (seat_probs.get(sno) or {}).get(qid) for sno in seat_ids}
            sp = {k: v for k, v in sp.items() if v}
            if not sp:
                continue
            rows = publish_probs(sp, opts, [r for r in roster if int(r['seat']) in seat_ids], segments, f"{session_seed}|{tag}|{seed_tag}|{qid}")
            out[qid] = _question_result(qid, qlabel, kind, rows)
        return out
    all_ids = [int(r['seat']) for r in roster]
    room = run(all_ids, 'room')
    quads = []
    for key, label in QUADRANTS:
        ids = [int(r['seat']) for r in roster if quadrant_of(r) == key]
        if not ids:
            quads.append({'key': key, 'label': label, 'n': 0, 'people': 0, 'questions': {}})
            continue
        ppl = messy_count(subject, f"{tag}-quad-{key}", sum(people_by_seat.get(i, 0) for i in ids))
        quads.append({'key': key, 'label': label, 'n': len(ids), 'people': int(ppl), 'questions': run(ids, f"quad-{key}")})
    return {'questions': room, 'quadrants': quads, 'room_people': int(room_people)}


def _top(q: dict, oid: str = 'a') -> Optional[float]:
    for r in (q or {}).get('rows') or []:
        if r.get('id') == oid:
            return round(float(r.get('pct') or 0), 1)
    return None


def _top_people(q: dict, oid: str = 'a') -> int:
    for r in (q or {}).get('rows') or []:
        if r.get('id') == oid:
            return int(r.get('people') or 0)
    return 0


def _who_line(qg: dict, qa: dict) -> dict:
    g = max(((r.get('id'), float(r.get('pct') or 0)) for r in (qg or {}).get('rows') or []), key=lambda x: x[1], default=('c', 0))
    a = max(((r.get('id'), float(r.get('pct') or 0)) for r in (qa or {}).get('rows') or []), key=lambda x: x[1], default=('c', 0))
    gl = {'a': 'women', 'b': 'men', 'c': 'women and men'}.get(g[0], 'women and men')
    al = {'a': 'under 35', 'b': '35 and over', 'c': 'every age'}.get(a[0], 'every age')
    return {'gender': gl, 'gender_pct': round(g[1], 1), 'age': al, 'age_pct': round(a[1], 1), 'line': f"Reads as a title for {gl}, {al}."}


def _seat_summaries(session: dict, roster: list) -> dict:
    from migration.digital_twin_pilot import _seat_summary
    beats = session.get('beats') or []
    scenes_by_id = {int(s['id']): s for s in (session.get('scenes') or [])}
    out = {}
    for r in roster:
        sno = int(r['seat'])
        out[sno] = _seat_summary(sno, (session.get('seat_dials') or {}).get(str(sno)) or [], beats, scenes_by_id, session.get('events') or [])
    return out


def _model_card(session: dict, twin: dict, roster: list, qs: list, cards: dict, *, meta: dict, models=None, usage_extras=None, seed: str) -> dict:
    try:
        from migration import digital_twin_watch as W
        got = W.battery_with(qs, twin, roster, _seat_summaries(session, roster), cards=cards, meta=meta, models=models, session_seed=seed, usage_extras=usage_extras,
                             system=MODE_CARD_SYSTEM)
        return got if not got.get('fallback') else {}
    except Exception as e:
        print(f"[twin-modes] mode card skipped: {e}")
        return {}


def _sold_it(session: dict, limit: int = 4) -> list:
    """The moments that sold it: the strongest love / wow / lean-in
    events, one per scene, in clock order."""
    evs = [e for e in session.get('events') or [] if e.get('type') in ('love', 'wow', 'lean-in', 'laugh')]
    evs.sort(key=lambda e: -float(e.get('intensity') or 0))
    seen, picked = set(), []
    for e in evs:
        if e.get('scene_id') in seen:
            continue
        seen.add(e.get('scene_id'))
        picked.append({'clock': _clock(e.get('t')), 't': e.get('t'), 'name': e.get('name'), 'type': e.get('type'), 'line': e.get('line'), 'scene_id': e.get('scene_id')})
        if len(picked) >= limit:
            break
    return sorted(picked, key=lambda p: float(p.get('t') or 0))


def _understood_lines(session: dict, roster: list, cards: dict, genre: str, seed: str) -> list:
    """What they understood it is about, one line per seat (the
    deterministic card: the scene with the most dialogue names the
    subject)."""
    scenes = session.get('scenes') or []
    talk = sorted(scenes, key=lambda s: -len(str(s.get('dialogue') or '')))
    anchor = str((talk[0] if talk else (scenes[0] if scenes else {})).get('caption') or '').strip().rstrip('.')
    gl = genre or 'story'
    out = []
    for r in roster[:12]:
        c = cards[int(r['seat'])]
        u = unit(seed, 'understood', r['seat'])
        if anchor:
            a = anchor[0].lower() + anchor[1:]
            line = (f"A {gl} about {a}." if u < 0.5 else f"Someone {a}, and it goes wrong." if u < 0.75 else f"It is a {gl}. The bit where {a} told me that.")
        else:
            line = f"A {gl}. I got the tone more than the plot." if u < 0.5 else f"Hard to say what it is about yet; the {gl} feel is clear."
        out.append({'seat': r['seat'], 'name': r.get('name'), 'line': line[:160]})
    return out


def _expect_lines(roster: list, cards: dict, answers: dict, seed: str) -> list:
    out = []
    for r in roster[:12]:
        a = (answers.get(int(r['seat'])) or {}).get('interest_after', 'b')
        u = unit(seed, 'expect', r['seat'])
        if a == 'a':
            line = 'I expect it to pay off the hook fast; I am in for the first one.' if u < 0.5 else 'I expect a lot of that energy and I want it.'
        elif a == 'b':
            line = 'I expect it is better than the trailer makes it look.' if u < 0.5 else 'I would need a friend to tell me it holds up.'
        else:
            line = 'I expect more of the same, and that is not for me.' if u < 0.5 else 'I did not get what it was; I would scroll past.'
        out.append({'seat': r['seat'], 'name': r.get('name'), 'line': line})
    return out


# ------------------------------------------------------------- trailer
def trailer_read(session: dict, twin: dict, *, title: str = '', logline: str = '', models=None, usage_extras=None, asset_label: str = '') -> dict:
    """The trailer card for one asset (the session or a sub-session read
    for an extra asset): interest before and after, the lift, what they
    understood, what they expect, the moments that sold it, who it is
    for, would watch, recommend, by quadrant."""
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    if not roster:
        return {}
    media = session.get('media') or {}
    seed = f"trailer|{_sid(session)}|{media.get('hash') or ''}"
    subject = _subject(twin)
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    seat_dials = session.get('seat_dials') or {}
    qs = list(MODE_CARDS['trailer'])
    genre = str(session.get('genre') or '')
    meta = {'title': title or media.get('title') or session.get('title') or '', 'logline': logline or session.get('logline') or '', 'genre': genre, 'format': 'trailer',
            'runtime': _clock(media.get('runtime_s') or 0)}
    model = _model_card(session, twin, roster, qs, cards, meta=meta, models=models, usage_extras=usage_extras, seed=seed)
    seat_ans, why, seat_probs = {}, {}, {}
    for r in roster:
        sno = int(r['seat'])
        sseed = f"{seed}|seat{sno}"
        cold = cold_interest(cards[sno], twin, meta['title'], meta['logline'], sseed)
        got = (model.get('answers') or {}).get(sno) or {}
        probs = seat_probs_for(qs, cards[sno], twin, seat_dials.get(str(sno)) or [], sseed, cold=cold)
        seat_probs[sno] = {qid: blend_pick(probs[qid], got.get(qid)) for qid, _l, _k, _o in qs}
        seat_ans[sno] = {qid: argmax(seat_probs[sno][qid]) for qid, _l, _k, _o in qs}
        if (model.get('why') or {}).get(sno):
            why[sno] = model['why'][sno]
    pub = _publish_card(qs, seat_probs, roster, segments, subject=subject, session_seed=f"{seed}|{media.get('hash') or ''}", tag='trailer')
    room = pub['questions']
    before = _top(room.get('interest_before')) or 0.0
    after = _top(room.get('interest_after')) or 0.0
    lift = round(after - before, 1)
    bat = ((session.get('scorecard') or {}).get('battery') or {}).get('headline') or {}
    quads = []
    for qd in pub['quadrants']:
        qq = qd.get('questions') or {}
        b, a = _top(qq.get('interest_before')), _top(qq.get('interest_after'))
        quads.append({'key': qd['key'], 'label': qd['label'], 'n': qd['n'], 'people': qd['people'], 'interest_before_pct': b, 'interest_after_pct': a,
                      'definite_interest_lift_pp': (round(a - b, 1) if a is not None and b is not None else None), 'would_watch_pct': _top(qq.get('would_watch'))})
    who = _who_line(room.get('who_gender'), room.get('who_age'))
    sold = _sold_it(session)
    out = {'asset_label': asset_label or str(session.get('cut_label') or media.get('title') or 'Trailer'), 'title': meta['title'], 'logline': meta['logline'],
           'interest_before_pct': before, 'interest_after_pct': after, 'definite_interest_lift_pp': lift,
           'interest_before_people': _top_people(room.get('interest_before')), 'interest_after_people': _top_people(room.get('interest_after')),
           'would_watch_pct': _top(room.get('would_watch')), 'recommend_pct': bat.get('definitely_recommend_pct'), 'total_positive_pct': bat.get('total_positive_pct'),
           'urgency_opening_pct': bat.get('urgency_opening_pct'),
           'understood': _understood_lines(session, roster, cards, genre, seed), 'expect': _expect_lines(roster, cards, seat_ans, seed), 'sold_it': sold, 'who_for': who,
           'questions': [room[q] for q, _l, _k, _o in qs if q in room], 'quadrants': quads,
           'by_seat': [{'seat': int(r['seat']), 'name': r.get('name'), 'answers': seat_ans[int(r['seat'])], 'why': why.get(int(r['seat']), '')} for r in roster],
           'runtime_s': media.get('runtime_s'), 'model': bool(model)}
    verb = 'lifts' if lift > 0 else 'does not lift'
    out['read'] = (f"Definite interest goes from {pct1(before)}% on the title alone to {pct1(after)}% after the trailer ({'+' if lift >= 0 else ''}{lift} points). "
                   f"{who['line']} " + (f"The moment that sold it: {sold[0]['clock']}, {sold[0]['line']}" if sold else '')).strip()
    out['norm_metrics'] = {'interest_before': before, 'interest_after': after, 'definite_interest_lift': lift, 'would_watch': out['would_watch_pct']}
    return out


def trailer_compare(reads: list) -> dict:
    """Up to three trailer reads side by side: the winner on definite
    interest lift, then would watch."""
    rows = [r for r in reads if isinstance(r, dict) and r.get('interest_after_pct') is not None]
    if not rows:
        return {}
    rows = sorted(rows, key=lambda r: (-(float(r.get('definite_interest_lift_pp') or 0)), -(float(r.get('would_watch_pct') or 0))))
    win = rows[0]
    line = f"{win.get('asset_label')} wins on definite interest lift ({'+' if float(win.get('definite_interest_lift_pp') or 0) >= 0 else ''}{win.get('definite_interest_lift_pp')} points)."
    if len(rows) > 1:
        line += f" {rows[1].get('asset_label')} sits {abs(float(win.get('definite_interest_lift_pp') or 0) - float(rows[1].get('definite_interest_lift_pp') or 0)):.1f} points behind."
    return {'winner': win.get('asset_label'), 'ranked': [{'asset_label': r.get('asset_label'), 'definite_interest_lift_pp': r.get('definite_interest_lift_pp'), 'interest_after_pct': r.get('interest_after_pct'),
                                                        'would_watch_pct': r.get('would_watch_pct'), 'recommend_pct': r.get('recommend_pct')} for r in rows], 'read': line}


# ------------------------------------------------------------- options
def _option_features(label: str, logline: str, caption: str = '') -> dict:
    words = str(label or '').split()
    return {'short': len(words) <= 2, 'long': len(words) >= 5, 'number': bool(re.search(r"\d", label or '')), 'name': any(w[:1].isupper() and len(w) > 2 for w in words[1:]),
            'genre': bool(_genre_hint(label)), 'place': bool(re.search(r"\b(street|avenue|city|town|county|island|bay|hill|valley|house|hotel|diner|bar)\b", label or '', re.I)),
            'mystery': label.strip().endswith('?') or bool(re.search(r"\b(who|what|last|lost|secret|missing|after)\b", label or '', re.I)),
            'familiar': any(w.lower() in str(logline or '').lower() for w in words if len(w) > 3),
            'face': bool(re.search(r"\b(face|woman|man|girl|boy|eyes|looking|stare|portrait|close-up|close up)\b", caption or '', re.I)),
            'words': bool(re.search(r"\b(tagline|words|text|line|title treatment)\b", caption or '', re.I)),
            'dark': bool(re.search(r"\b(dark|night|shadow|black|moody|rain)\b", caption or '', re.I)),
            'bright': bool(re.search(r"\b(bright|sun|yellow|warm|smil|beach|pastel)\b", caption or '', re.I)),
            'busy': bool(re.search(r"\b(crowd|many|ensemble|collage|busy|group of)\b", caption or '', re.I))}


def _option_score(card: dict, twin: dict, opt: dict, logline: str, mode: str, seed: str) -> float:
    f = _option_features(opt.get('label') or '', logline, opt.get('caption') or '')
    age = int(card.get('age') or 35)
    gender = str(card.get('gender') or '')
    loyal = _trait(twin, 'franchise_loyalty')
    v = 50.0 + (loyal - 50) * 0.08
    if mode == 'title':
        v += 5.0 if f['short'] else (-3.5 if f['long'] else 0.0)
        v += 3.0 if f['genre'] else 0.0
        v += 2.5 if f['name'] and age >= 35 else 0.0
        v += 2.0 if f['mystery'] and age < 45 else 0.0
        v += 2.2 if f['familiar'] else 0.0
        v += 1.5 if f['place'] else 0.0
        v += 1.0 if f['number'] else 0.0
    else:
        v += 5.0 if f['face'] else 0.0
        v += 3.0 if f['words'] else 0.0
        v += 3.5 if f['dark'] and age < 45 and gender == 'man' else (1.0 if f['dark'] else 0.0)
        v += 3.5 if f['bright'] and (gender in ('woman', 'nonbinary') or age >= 45) else (0.8 if f['bright'] else 0.0)
        v -= 2.5 if f['busy'] else 0.0
    g = _genre_hint(f"{opt.get('label') or ''} {opt.get('caption') or ''}")
    if g and g == _genre_hint(logline):
        v += 2.0
    return clamp(v + jit(-9.0, 9.0, seed, 'opt', opt.get('id')), 4.0, 96.0)


def _reason_for(opt: dict, logline: str, mode: str, seed: str) -> str:
    f = _option_features(opt.get('label') or '', logline, opt.get('caption') or '')
    bank = _REASONS_TITLE if mode == 'title' else _REASONS_ART
    hits = [k for k, on in f.items() if on and k in bank]
    if not hits:
        hits = ['plain', 'bold'] if mode == 'title' else ['clean', 'mood']
    return bank[hits[int(unit(seed, 'reason', opt.get('id')) * len(hits)) % len(hits)]]


def options_read(session: dict, twin: dict, *, logline: str = '', models=None, usage_extras=None) -> dict:
    """Up to six titles or pieces of key art to the same room: appeal,
    fit (titles), uniqueness, attention (art), interest lift (art),
    preference with reasons, by quadrant; ranked."""
    mode = mode_of(session)
    mode = mode if mode in ('title', 'key_art') else 'title'
    options = normalize_options(session.get('options') or [], mode)
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    if not options or not roster:
        return {}
    media = session.get('media') or {}
    seed = f"{mode}|{_sid(session)}|{hashlib.sha1('|'.join(o['label'] for o in options).encode('utf-8')).hexdigest()[:10]}"
    subject = _subject(twin)
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    logline = logline or str(session.get('logline') or '')
    seat_dials = session.get('seat_dials') or {}
    qs = list(MODE_CARDS[mode])
    model_rows = {}
    try:
        from migration.digital_twin_room import Models, regression_mode, model_available
        models = models or Models(usage_extras=usage_extras)
        if not (models.fn is None and (regression_mode() or not model_available())):
            import json as _json
            from migration.digital_twin_watch import _seat_card_text
            user = _json.dumps({'title_set': {'logline': logline, 'mode': mode, 'options': [{'id': o['id'], 'label': o['label'], 'on_it': o.get('caption') or ''} for o in options]},
                                'room': [{'seat': int(r['seat']), 'name': r.get('name'), 'who': _seat_card_text(cards[int(r['seat'])], 480)} for r in roster]}, default=str)
            data, _call = models.call('battery', OPTIONS_SYSTEM, user, max_tokens=int(clamp(400 + len(roster) * len(options) * 30, 1500, 8000)), temperature=0.5, tools=None)
            if isinstance(data, dict) and isinstance(data.get('seats'), list):
                for row in data['seats']:
                    try:
                        model_rows[int(row.get('seat'))] = row
                    except (TypeError, ValueError, AttributeError):
                        continue
    except Exception as e:
        print(f"[twin-modes] options card skipped: {e}")
    valid_ids = {o['id'] for o in options}
    per_seat = {}
    for r in roster:
        sno = int(r['seat'])
        sseed = f"{seed}|seat{sno}"
        scores = {o['id']: _option_score(cards[sno], twin, o, logline, mode, sseed) for o in options}
        mr = model_rows.get(sno) or {}
        ratings = {}
        for o in options:
            s = scores[o['id']]
            got = ((mr.get('ratings') or {}).get(o['id']) or {}) if isinstance(mr.get('ratings'), dict) else {}
            probs = seat_probs_for(qs, cards[sno], twin, seat_dials.get(str(sno)) or [], f"{sseed}|{o['id']}", cold=cold_interest(cards[sno], twin, o['label'], '', f"{sseed}|{o['id']}|cold"), hot=s)
            row = {}
            for qid, _l, _k, opts in qs:
                v = str(got.get(qid) or '').strip().lower()
                row[qid] = blend_pick(probs[qid], v if v in {x[0] for x in opts} else None)
            ratings[o['id']] = row
        pick = str(mr.get('pick') or '').strip()
        if pick not in valid_ids:
            pick = max(scores.items(), key=lambda kv: kv[1])[0]
        reason = str(mr.get('reason') or '').strip()
        try:
            from migration.digital_twin_room import scrub
            reason = scrub(reason)[:140] if reason else ''
        except Exception:
            reason = reason[:140]
        if not reason:
            reason = _reason_for(next(o for o in options if o['id'] == pick), logline, mode, sseed)
        per_seat[sno] = {'scores': scores, 'ratings': ratings, 'pick': pick, 'reason': reason}
    people_by_seat = _seat_people(roster, segments)
    room_people = sum(int(s.get('people') or 0) for s in segments) or sum(people_by_seat.values()) or len(roster) * 800
    # preference: each seat's leaning over the options (a softmax over its own scores, the pick on top), weighted by the people behind the seat
    pref_options = [(o['id'], o['label'], 'yes') for o in options]
    pref_probs = {}
    for r in roster:
        sno = int(r['seat'])
        pref_probs[sno] = blend_pick(softmax_probs(per_seat[sno]['scores'], temperature=4.5), per_seat[sno]['pick'], weight=0.55)
    pref_rows = publish_probs(pref_probs, pref_options, roster, segments, f"{seed}|pref")
    pref_by_id = {r['id']: r for r in pref_rows}
    quad_pref = {}
    for key, label in QUADRANTS:
        ids = [int(r['seat']) for r in roster if quadrant_of(r) == key]
        if not ids:
            quad_pref[key] = {'label': label, 'n': 0, 'people': 0, 'rows': {}}
            continue
        ppl = messy_count(subject, f"{mode}-quad-{key}", sum(people_by_seat.get(i, 0) for i in ids))
        qrows = publish_probs({k: v for k, v in pref_probs.items() if k in ids}, pref_options, [r for r in roster if int(r['seat']) in ids], segments, f"{seed}|pref|{key}")
        quad_pref[key] = {'label': label, 'n': len(ids), 'people': int(ppl), 'rows': {r['id']: round(float(r['pct']), 1) for r in qrows}}
    out_opts = []
    for o in options:
        probs_by_seat = {int(r['seat']): per_seat[int(r['seat'])]['ratings'][o['id']] for r in roster}
        pub = _publish_card(qs, probs_by_seat, roster, segments, subject=subject, session_seed=f"{seed}|{o['id']}", tag=f"{mode}-{o['id']}")
        room = pub['questions']
        reasons = [{'seat': int(r['seat']), 'name': r.get('name'), 'line': per_seat[int(r['seat'])]['reason']} for r in roster if per_seat[int(r['seat'])]['pick'] == o['id']][:5]
        row = {'id': o['id'], 'label': o['label'], 'caption': o.get('caption') or '', 'preference_pct': round(float(pref_by_id.get(o['id'], {}).get('pct') or 0), 1),
               'preference_people': int(pref_by_id.get(o['id'], {}).get('people') or 0), 'picked_by': sum(1 for s in per_seat.values() if s['pick'] == o['id']),
               'appeal_top_pct': (round(float(_top(room.get('appeal')) or 0) + float(_top(room.get('appeal'), 'b') or 0), 1) if room.get('appeal') else None),
               'love_it_pct': _top(room.get('appeal')), 'reasons': reasons, 'by_quadrant': {k: v['rows'].get(o['id']) for k, v in quad_pref.items()},
               'questions': [room[q] for q, _l, _k, _o in qs if q in room]}
        if mode == 'title':
            row['fit_pct'] = _top(room.get('fit'))
            row['unique_pct'] = _top(room.get('unique'))
        else:
            row['attention_pct'] = _top(room.get('attention'))
            row['unique_pct'] = None
            b, a = _top(room.get('interest_before')), _top(room.get('interest_after'))
            row['interest_before_pct'], row['interest_after_pct'] = b, a
            row['interest_lift_pp'] = round((a or 0) - (b or 0), 1) if a is not None and b is not None else None
            for k in ('attention', 'says', 'text_on_art'):
                if o.get(k):
                    row[k if k != 'attention' else 'eye_lands_on'] = o[k]
        out_opts.append(row)
    out_opts.sort(key=lambda r: (-r['preference_pct'], -(r.get('appeal_top_pct') or 0)))
    for i, r in enumerate(out_opts, 1):
        r['rank'] = i
    win = out_opts[0]
    second = out_opts[1] if len(out_opts) > 1 else None
    if mode == 'title':
        read = (f"{win['label']} leads: {pct1(win['preference_pct'])}% prefer it" + (f", {pct1(win['fit_pct'])}% say it fits the logline" if win.get('fit_pct') is not None else '')
                + (f". {second['label']} is next at {pct1(second['preference_pct'])}%." if second else '.'))
    else:
        read = (f"{win['label']} leads: {pct1(win['preference_pct'])}% prefer it" + (f", it holds the eye for {pct1(win['attention_pct'])}%" if win.get('attention_pct') is not None else '')
                + (f", interest moves {'+' if (win.get('interest_lift_pp') or 0) >= 0 else ''}{win.get('interest_lift_pp')} points" if win.get('interest_lift_pp') is not None else '')
                + (f". {second['label']} is next at {pct1(second['preference_pct'])}%." if second else '.'))
    quads_out = []
    for key, label in QUADRANTS:
        v = quad_pref.get(key)
        if not v:
            continue
        top = max(v['rows'].items(), key=lambda kv: kv[1]) if v['rows'] else None
        quads_out.append({'key': key, 'label': label, 'n': v['n'], 'people': v['people'], 'winner': next((o['label'] for o in options if top and o['id'] == top[0]), ''),
                          'winner_pct': round(top[1], 1) if top else None, 'rows': v['rows']})
    nm = {'option_preference': win['preference_pct']}
    if mode == 'title':
        nm.update({'title_appeal': win.get('appeal_top_pct'), 'title_fit': win.get('fit_pct'), 'title_unique': win.get('unique_pct')})
    else:
        nm.update({'art_attention': win.get('attention_pct'), 'art_interest_lift': win.get('interest_lift_pp')})
    return {'mode': mode, 'logline': logline, 'options': out_opts, 'winner': win['id'], 'winner_label': win['label'], 'read': read, 'quadrants': quads_out,
            'by_seat': [{'seat': int(r['seat']), 'name': r.get('name'), 'pick': per_seat[int(r['seat'])]['pick'], 'reason': per_seat[int(r['seat'])]['reason']} for r in roster],
            'norm_metrics': {k: v for k, v in nm.items() if v is not None}, 'model': bool(model_rows)}


def caption_art(options: list, *, models=None, usage_extras=None, title: str = '') -> list:
    """Captions for key art images (what is on it, where the eye lands,
    what it says the title is). A vision call when a model is on; the
    option labels stand in otherwise. Never raises."""
    try:
        from migration.digital_twin_room import Models, regression_mode, model_available
        from migration.digital_twin_watch import _vision_json, _frame_bytes
        import base64
        models = models or Models(usage_extras=usage_extras)
        if models.fn is None and (regression_mode() or not model_available()):
            return options
        blocks = [{'type': 'text', 'text': f"The title: {title or 'untitled'}. {len(options)} pieces of key art follow in order."}]
        k = 0
        order = []
        for o in options:
            path = o.get('local_path')
            raw = _frame_bytes(str(path)) if path else None
            if raw is None:
                continue
            k += 1
            order.append(o)
            blocks.append({'type': 'text', 'text': f"Image {k}: {o.get('label') or ''}"})
            mt = 'image/png' if str(o.get('filename') or '').lower().endswith('.png') else ('image/webp' if str(o.get('filename') or '').lower().endswith('.webp') else 'image/jpeg')
            blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': base64.b64encode(raw).decode('ascii')}})
        if not order:
            return options
        data, _m, _s = _vision_json(ART_SYSTEM, blocks, max_tokens=1400, usage_extras=usage_extras, surface='twin_watch_art')
        rows = (data or {}).get('images') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return options
        from migration.digital_twin_room import scrub
        for i, o in enumerate(order, 1):
            row = next((r for r in rows if isinstance(r, dict) and int(r.get('k') or 0) == i), None)
            if not row:
                continue
            o['caption'] = scrub(str(row.get('caption') or ''))[:160] or o.get('caption') or ''
            o['attention'] = scrub(str(row.get('attention') or ''))[:100]
            o['says'] = str(row.get('says') or '').strip().lower()[:20]
            o['text_on_art'] = scrub(str(row.get('text_on_art') or ''))[:80]
    except Exception as e:
        print(f"[twin-modes] art captions skipped: {e}")
    return options


# ------------------------------------------------------------- concept
def concept_read(session: dict, twin: dict, *, text: str = '', models=None, usage_extras=None) -> dict:
    """Interest, uniqueness, who it is for, would watch, the strongest
    element, the biggest doubt; by quadrant; a plain read."""
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    if not roster:
        return {}
    text = text or str(session.get('text') or '')
    media = session.get('media') or {}
    seed = f"concept|{_sid(session)}|{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"
    subject = _subject(twin)
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    seat_dials = session.get('seat_dials') or {}
    qs = list(MODE_CARDS['concept'])
    genre = str(session.get('genre') or '') or _genre_hint(text)
    meta = {'title': media.get('title') or session.get('title') or '', 'text': text[:1800], 'genre': genre, 'format': 'concept'}
    model = _model_card(session, twin, roster, qs, cards, meta=meta, models=models, usage_extras=usage_extras, seed=seed)
    seat_ans, why, seat_probs = {}, {}, {}
    for r in roster:
        sno = int(r['seat'])
        sseed = f"{seed}|seat{sno}"
        cold = cold_interest(cards[sno], twin, meta['title'], text, sseed)
        series = seat_dials.get(str(sno)) or []
        hot = (0.55 * cold + 0.45 * _seat_score(cards[sno], twin, series, sseed)) if series else cold
        got = (model.get('answers') or {}).get(sno) or {}
        probs = seat_probs_for(qs, cards[sno], twin, series, sseed, cold=cold, hot=hot)
        seat_probs[sno] = {qid: blend_pick(probs[qid], got.get(qid)) for qid, _l, _k, _o in qs}
        seat_ans[sno] = {qid: argmax(seat_probs[sno][qid]) for qid, _l, _k, _o in qs}
        if (model.get('why') or {}).get(sno):
            why[sno] = model['why'][sno]
    pub = _publish_card(qs, seat_probs, roster, segments, subject=subject, session_seed=seed, tag='concept')
    room = pub['questions']
    who = _who_line(room.get('who_gender'), room.get('who_age'))
    strongest = sorted([{'label': r['label'], 'pct': round(float(r['pct']), 1), 'people': int(r['people'])} for r in (room.get('strongest') or {}).get('rows') or []], key=lambda x: -x['pct'])
    doubt = sorted([{'label': r['label'], 'pct': round(float(r['pct']), 1), 'people': int(r['people'])} for r in (room.get('doubt') or {}).get('rows') or []], key=lambda x: -x['pct'])
    interest = _top(room.get('interest')) or 0.0
    interest_any = round(interest + float(_top(room.get('interest'), 'b') or 0), 1)
    unique = _top(room.get('unique'))
    watch = _top(room.get('would_watch'))
    quads = []
    for qd in pub['quadrants']:
        qq = qd.get('questions') or {}
        quads.append({'key': qd['key'], 'label': qd['label'], 'n': qd['n'], 'people': qd['people'], 'definitely_interested_pct': _top(qq.get('interest')),
                      'would_watch_pct': _top(qq.get('would_watch')), 'unique_pct': _top(qq.get('unique'))})
    read = (f"{pct1(interest)}% are definitely interested ({pct1(interest_any)}% at least somewhat); {pct1(watch)}% would watch. "
            + (f"{pct1(unique)}% say it stands out. " if unique is not None else '')
            + (f"The strongest element is {strongest[0]['label'].lower()} ({pct1(strongest[0]['pct'])}%); the biggest doubt is {doubt[0]['label'].lower()} ({pct1(doubt[0]['pct'])}%). " if strongest and doubt else '')
            + who['line'])
    return {'text': text[:4000], 'genre': genre, 'definitely_interested_pct': interest, 'interested_pct': interest_any, 'unique_pct': unique, 'would_watch_pct': watch,
            'who_for': who, 'strongest': strongest, 'doubt': doubt, 'quadrants': quads, 'questions': [room[q] for q, _l, _k, _o in qs if q in room],
            'by_seat': [{'seat': int(r['seat']), 'name': r.get('name'), 'answers': seat_ans[int(r['seat'])], 'why': why.get(int(r['seat']), '')} for r in roster],
            'read': read, 'norm_metrics': {'concept_interest': interest, 'concept_unique': unique, 'would_watch': watch}, 'model': bool(model)}


# -------------------------------------------------------------- script
def script_notes(session: dict) -> dict:
    """Story, structure and character notes per scene from the dial
    and the cast: where it runs long, where a character arrives
    without a want, where the turn lands; the room's act notes when a
    model read it."""
    scenes = session.get('scenes') or []
    dial = (session.get('dial') or {}).get('value') or []
    beats = session.get('beats') or []
    events = session.get('events') or []
    roster = (session.get('group') or {}).get('roster') or []
    n = len(roster) or 1
    if not scenes:
        return {}
    by_scene_dial = {}
    for i, b in enumerate(beats):
        if i < len(dial):
            by_scene_dial.setdefault(b.get('scene_id'), []).append(dial[i])
    seen_speakers = set()
    rows = []
    prev = None
    act_notes = {}
    w = session.get('watch') or {}
    for sno, acts in (w.get('act_notes') or {}).items():
        for act, note in (acts or {}).items():
            act_notes.setdefault(act, []).append(note)
    story_hits = structure_hits = char_hits = 0
    for s in scenes:
        sid = int(s['id'])
        vals = by_scene_dial.get(sid) or []
        level = round(sum(vals) / len(vals), 1) if vals else None
        delta = round(level - prev, 1) if (level is not None and prev is not None) else 0.0
        lost = sum(1 for e in events if e.get('scene_id') == sid and e.get('type') in ('check-out', 'confuse'))
        liked = sum(1 for e in events if e.get('scene_id') == sid and e.get('type') in ('love', 'wow', 'laugh', 'lean-in'))
        words = int(s.get('words') or len(str(s.get('dialogue') or '').split()))
        speakers = list(s.get('speakers') or ([s.get('speaker')] if s.get('speaker') else []))
        new = [sp for sp in speakers if sp and sp not in seen_speakers]
        seen_speakers.update(sp for sp in speakers if sp)
        slug = str(s.get('slug') or s.get('caption') or f"Scene {sid + 1}")[:70]
        story, structure, character = '', '', ''
        if delta >= 4.0 or liked >= max(2, n // 3):
            story = f"The scene lands: the room rose {abs(delta):.0f} points here." if delta >= 4.0 else 'The room leaned in here.'
            story_hits += 1
        elif delta <= -4.0 or lost >= max(2, n // 3):
            story = f"The room dropped {abs(delta):.0f} points; the stakes are not clear yet." if delta <= -4.0 else 'A few seats lost the thread here.'
        dur = float(s.get('t_end') or 0) - float(s.get('t_start') or 0)
        if words >= 220 or dur >= 150:
            structure = 'Runs long on the page; a trim here keeps the act moving.'
            structure_hits += 1
        elif lost and dur >= 60:
            structure = 'The room drifted before the scene turned.'
            structure_hits += 1
        elif s.get('act') in ('turn',) and delta > 0:
            structure = 'The turn arrives on time and the room goes with it.'
        if new and len(new) <= 2 and words >= 40:
            character = f"{' and '.join(new[:2])} arrive{'s' if len(new) == 1 else ''}; the room wants to know what {'they want' if len(new) > 1 else 'they want'} before the next scene."
            char_hits += 1
        elif len(speakers) >= 4:
            character = f"{len(speakers)} voices in one scene; the room tracked {speakers[0]} most."
        rows.append({'id': sid, 'slug': slug, 'clock': _clock(s.get('t_start')), 'dial': level, 'delta': delta, 'liked': liked, 'lost': lost, 'words': words,
                     'speakers': speakers[:6], 'notes': {'story': story, 'structure': structure, 'character': character}})
        if level is not None:
            prev = level
    total = len(rows) or 1
    story_pct = round(clamp(100.0 * sum(1 for r in rows if r['notes']['story'] and 'lands' in r['notes']['story'] or 'leaned' in r['notes']['story']) / total + jit(-0.8, 0.8, _sid(session), 'story'), 0.1, 99.9), 1)
    structure_pct = round(clamp(100.0 * (1 - structure_hits / total) + jit(-0.8, 0.8, _sid(session), 'structure'), 0.1, 99.9), 1)
    character_pct = round(clamp(100.0 * (1 - char_hits / total) + jit(-0.8, 0.8, _sid(session), 'character'), 0.1, 99.9), 1)
    longest = sorted(rows, key=lambda r: -(r['words']))[:2]
    read = (f"{len(rows)} scenes, about {max(1, int(round(float((session.get('media') or {}).get('runtime_s') or 60) / 60)))} pages on the reading clock. "
            + (f"The stretch to trim first: {longest[0]['slug']} ({longest[0]['words']} words)." if longest else '')
            + (f" The scene that lands: {max(rows, key=lambda r: r['delta'])['slug']}." if rows else ''))
    return {'scenes': rows, 'act_notes': act_notes, 'read': read, 'story_pct': story_pct, 'structure_pct': structure_pct, 'character_pct': character_pct,
            'norm_metrics': {'script_story': story_pct, 'script_structure': structure_pct, 'script_character': character_pct}}


# ------------------------------------------------------- episode / binge
def episode_read(session: dict, twin: dict, prior: Optional[dict], *, models=None, usage_extras=None) -> dict:
    """Commitment after the pilot: would they keep going, and how this
    one sits against the last one (the room knows the prior cut when
    the lineage points to it)."""
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    if not roster:
        return {}
    media = session.get('media') or {}
    seed = f"episode|{_sid(session)}|{media.get('hash') or ''}"
    subject = _subject(twin)
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    seat_dials = session.get('seat_dials') or {}
    qs = list(MODE_CARDS['episode'])
    prior_head = (((prior or {}).get('scorecard') or {}).get('battery') or {}).get('headline') or {}
    prior_seat = {}
    for sno_s, series in ((prior or {}).get('seat_dials') or {}).items():
        if series:
            tail = max(2, int(len(series) * 0.25))
            prior_seat[int(sno_s)] = {'end': sum(series[-tail:]) / tail, 'mean': sum(series) / len(series)}
    meta = {'title': media.get('title') or session.get('title') or '', 'genre': session.get('genre') or '', 'format': 'episode',
            'frame': (f"The room watched the prior cut, {(prior or {}).get('title') or 'the pilot'}: total positive {prior_head.get('total_positive_pct')}%, would watch the next one {prior_head.get('tune_in_pct')}%." if prior else 'A first episode.')}
    model = _model_card(session, twin, roster, qs, cards, meta=meta, models=models, usage_extras=usage_extras, seed=seed)
    seat_probs = {}
    for r in roster:
        sno = int(r['seat'])
        sseed = f"{seed}|seat{sno}"
        got = (model.get('answers') or {}).get(sno) or {}
        probs = seat_probs_for(qs, cards[sno], twin, seat_dials.get(str(sno)) or [], sseed, cold=50.0, prior=prior_seat.get(sno))
        seat_probs[sno] = {qid: blend_pick(probs[qid], got.get(qid)) for qid, _l, _k, _o in qs}
    pub = _publish_card(qs, seat_probs, roster, segments, subject=subject, session_seed=f"{seed}|{media.get('hash') or ''}", tag='episode')
    room = pub['questions']
    commit = _top(room.get('commit'))
    better = _top(room.get('versus_prior'))
    head = ((session.get('scorecard') or {}).get('battery') or {}).get('headline') or {}
    delta = (round(float(head.get('tune_in_pct') or 0) - float(prior_head.get('tune_in_pct') or 0), 1) if prior and head.get('tune_in_pct') is not None and prior_head.get('tune_in_pct') is not None else None)
    read = f"{pct1(commit)}% would keep going with the series."
    if prior:
        read += f" {pct1(better)}% call it better than {(prior or {}).get('title') or 'the last one'}" + (f"; would watch the next one moves {'+' if (delta or 0) >= 0 else ''}{delta} points." if delta is not None else '.')
    return {'commitment_pct': commit, 'better_than_prior_pct': better, 'prior': ({'session_id': (prior or {}).get('id'), 'title': (prior or {}).get('title'), 'tune_in_pct': prior_head.get('tune_in_pct'),
                                                                                  'total_positive_pct': prior_head.get('total_positive_pct')} if prior else None),
            'tune_in_delta_pp': delta, 'questions': [room[q] for q, _l, _k, _o in qs if q in room], 'quadrants': [{'key': qd['key'], 'label': qd['label'], 'n': qd['n'], 'people': qd['people'],
                                                                                                                 'commitment_pct': _top((qd.get('questions') or {}).get('commit'))} for qd in pub['quadrants']],
            'read': read, 'norm_metrics': {'tune_in': commit}, 'model': bool(model)}


def binge_read(session: dict, asset_reads: list) -> dict:
    """Cumulative build across episodes in one test: per-episode would
    watch the next one and total positive, and the cumulative line."""
    rows = []
    cum = []
    acc = 0.0
    for i, ar in enumerate(asset_reads or []):
        head = ((ar.get('scorecard') or {}).get('battery') or {}).get('headline') or {}
        tune = head.get('tune_in_pct')
        rows.append({'episode': i + 1, 'label': ar.get('label') or f"Episode {i + 1}", 'total_positive_pct': head.get('total_positive_pct'), 'tune_in_pct': tune,
                     'dial_mean': (round(sum(ar.get('dial') or [50]) / max(1, len(ar.get('dial') or [50])), 1) if ar.get('dial') else None)})
        if tune is not None:
            acc = (acc * i + float(tune)) / (i + 1)
        cum.append(round(acc, 1))
    if not rows:
        return {}
    first, last = rows[0], rows[-1]
    trend = (float(last.get('tune_in_pct') or 0) - float(first.get('tune_in_pct') or 0)) if len(rows) > 1 else 0.0
    read = (f"Across {len(rows)} episodes the share who would watch the next one {'builds' if trend >= 3 else 'holds' if trend > -3 else 'slips'}: "
            f"{pct1(first.get('tune_in_pct'))}% after the first, {pct1(last.get('tune_in_pct'))}% after the last.")
    return {'episodes': rows, 'cumulative_tune_in': cum, 'trend_pp': round(trend, 1), 'read': read}


# ------------------------------------------------------------ marketing
def marketing_read(session: dict, twin: dict, *, models=None, usage_extras=None) -> dict:
    """The locked-cut read: urgency, who it is for, what to put in the
    trailer (the moments that sold it)."""
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    if not roster:
        return {}
    media = session.get('media') or {}
    seed = f"locked|{_sid(session)}|{media.get('hash') or ''}"
    subject = _subject(twin)
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    seat_dials = session.get('seat_dials') or {}
    qs = list(MODE_CARDS['locked'])
    seat_probs = {int(r['seat']): seat_probs_for(qs, cards[int(r['seat'])], twin, seat_dials.get(str(r['seat'])) or [], f"{seed}|seat{r['seat']}", cold=50.0) for r in roster}
    pub = _publish_card(qs, seat_probs, roster, segments, subject=subject, session_seed=f"{seed}|{media.get('hash') or ''}", tag='locked')
    room = pub['questions']
    who = _who_line(room.get('who_gender'), room.get('who_age'))
    head = ((session.get('scorecard') or {}).get('battery') or {}).get('headline') or {}
    sold = _sold_it(session, limit=5)
    return {'urgency_opening_pct': head.get('urgency_opening_pct'), 'who_for': who, 'trailer_moments': sold,
            'read': (f"{who['line']} " + (f"Put these in the trailer: " + '; '.join(f"{m['clock']} ({m['type']})" for m in sold[:3]) + '.' if sold else '')
                     + (f" {pct1(head.get('urgency_opening_pct'))}% would go the first week." if head.get('urgency_opening_pct') is not None else '')).strip()}


# ------------------------------------------------------------- the glue
def mode_norm_metrics(session: dict) -> dict:
    """The mode read's metrics for the norms ledger."""
    out = {}
    for key in ('mode_read', 'marketing_read'):
        r = session.get(key) or {}
        nm = r.get('norm_metrics') if isinstance(r, dict) else None
        if isinstance(nm, dict):
            out.update({k: float(v) for k, v in nm.items() if v is not None})
    return out


def _open_session_for_mode(session: dict):
    mode = mode_of(session)
    session.setdefault('assets', [])
    session.setdefault('options', [])
    session.setdefault('logline', '')
    session.setdefault('text', '')
    session.setdefault('mode_read', {})
    session.setdefault('asset_reads', {})
    return mode


def set_stimulus(session: dict, body: dict) -> dict:
    """The mode's stimulus from a request body: options (titles), text
    (a script or a concept), logline, cast. Returns what changed."""
    changed = {}
    if not isinstance(body, dict):
        return changed
    _open_session_for_mode(session)
    if 'options' in body:
        opts = normalize_options(body.get('options'), mode_of(session))
        session['options'] = opts
        changed['options'] = len(opts)
    if 'logline' in body:
        ll = ' '.join(str(body.get('logline') or '').split())[:600]
        session['logline'] = ll
        changed['logline'] = bool(ll)
    if 'text' in body:
        tx = str(body.get('text') or '').strip()[:400000]
        session['text'] = tx
        changed['text'] = len(tx)
        if tx and mode_of(session) in ('script', 'concept'):
            session['status'] = 'draft'
            session['watch'] = {}
            session['prepare_note'] = 'The room is ready to read it. Press Play.'
    if 'cast' in body and isinstance(body.get('cast'), list):
        session['cast'] = [' '.join(str(c).split())[:60] for c in body['cast'] if str(c).strip()][:16]
        changed['cast'] = len(session['cast'])
    if changed:
        session['updated_at'] = _now()
    return changed


def text_analysis(session: dict) -> Optional[dict]:
    """The analysis a text or option stimulus hands to prepare_watch
    (scenes, captions, transcript, runtime), or None when the mode
    plays a file."""
    mode = mode_of(session)
    genre = str(session.get('genre') or '')
    if mode == 'script' and str(session.get('text') or '').strip() and not (session.get('media') or {}).get('s3_key'):
        sc = script_scenes(session['text'])
        caps = captions_for_text_scenes(sc['scenes'], genre or _genre_hint(session['text']))
        if not session.get('cast'):
            session['cast'] = sc.get('cast') or []
        return {'runtime_s': sc['runtime_s'], 'scenes': sc['scenes'], 'captions': caps, 'transcript': sc['transcript'], 'silences': None, 'frames': [], 'tools': {}, 'notes': [], 'source': 'text',
                'pages': sc.get('pages')}
    if mode == 'concept':
        cs = concept_scenes(session.get('text') or (session.get('media') or {}).get('title') or session.get('title') or '')
        return {'runtime_s': cs['runtime_s'], 'scenes': cs['scenes'], 'captions': captions_for_text_scenes(cs['scenes'], genre or _genre_hint(session.get('text') or '')), 'transcript': [], 'silences': None,
                'frames': [], 'tools': {}, 'notes': [], 'source': 'text'}
    if mode in ('title', 'key_art') and session.get('options'):
        opts = normalize_options(session['options'], mode)
        os_ = option_scenes(opts, mode)
        return {'runtime_s': os_['runtime_s'], 'scenes': os_['scenes'], 'captions': captions_for_text_scenes(os_['scenes'], genre), 'transcript': [], 'silences': None,
                'frames': [], 'tools': {}, 'notes': [], 'source': 'options'}
    return None


def finish_mode_read(session: dict, twins_by_key: dict, *, models=None, usage_extras=None, prior: Optional[dict] = None) -> dict:
    """After prepare_watch: the mode's own read on the session (trailer
    card, options ranking, concept read, script notes, episode
    commitment, the marketing read on a locked cut). Idempotent; never
    raises."""
    twins = list((twins_by_key or {}).values()) or [{}]
    twin = twins[0]
    mode = _open_session_for_mode(session)
    media = session.get('media') or {}
    out = {}
    try:
        if mode in ('trailer', 'spot'):
            out = trailer_read(session, twin, title=str(media.get('title') or session.get('title') or ''), logline=str(session.get('logline') or ''), models=models, usage_extras=usage_extras,
                               asset_label=str((session.get('assets') or [{}])[0].get('label') if session.get('assets') else '') or str(session.get('cut_label') or 'Trailer A'))
            extra = [r for r in (session.get('asset_reads') or {}).values() if isinstance(r, dict) and r.get('trailer')]
            reads = [out] + [r['trailer'] for r in sorted(extra, key=lambda r: r.get('slot', 9))]
            if len(reads) > 1:
                out['compare'] = trailer_compare(reads)
        elif mode in ('title', 'key_art'):
            out = options_read(session, twin, logline=str(session.get('logline') or ''), models=models, usage_extras=usage_extras)
        elif mode == 'concept':
            out = concept_read(session, twin, text=str(session.get('text') or ''), models=models, usage_extras=usage_extras)
        elif mode == 'script' and not media.get('s3_key'):
            out = script_notes(session)
        elif mode == 'episode':
            out = episode_read(session, twin, prior, models=models, usage_extras=usage_extras)
        elif mode == 'binge':
            reads = [{'label': (session.get('assets') or [{}])[0].get('label') if session.get('assets') else session.get('cut_label'), 'scorecard': session.get('scorecard'), 'dial': (session.get('dial') or {}).get('value')}]
            reads += [r for r in sorted((session.get('asset_reads') or {}).values(), key=lambda r: r.get('slot', 9)) if isinstance(r, dict)]
            out = binge_read(session, reads)
    except Exception as e:
        print(f"[twin-modes] mode read skipped: {e}")
        out = {}
    out = out or {}
    out['mode'] = mode
    session['mode_read'] = out
    try:
        if mode in ('pilot', 'feature', 'episode', 'binge') and not session.get('rough_cut'):
            session['marketing_read'] = marketing_read(session, twin, models=models, usage_extras=usage_extras)
        else:
            session.pop('marketing_read', None)
    except Exception as e:
        print(f"[twin-modes] marketing read skipped: {e}")
    session['updated_at'] = _now()
    return out


def prepare_extra_asset(session: dict, asset: dict, twins_by_key: dict, *, analysis: Optional[dict] = None, transcript: Optional[list] = None,
                        models=None, usage_extras=None, s3=None, progress=None) -> dict:
    """A second or third trailer (or a further episode in a binge test)
    watched by the same room: a sub-session on the same roster runs the
    whole engine and its read lands under asset_reads[asset id]."""
    from migration import digital_twin_pilot as PI
    _open_session_for_mode(session)
    sub = {'id': f"{_sid(session)}-{asset.get('id')}", 'kind': 'pilot', 'title': str(asset.get('label') or asset.get('title') or session.get('title') or ''),
           'group': session.get('group'), 'questions': [], 'media': {k: asset.get(k) for k in ('title', 'runtime_s', 's3_key', 'filename', 'content_type', 'bytes', 'hash') if asset.get(k) is not None},
           'status': 'draft', 'mode': mode_of(session), 'genre': session.get('genre') or '', 'genre_set_by_user': bool(session.get('genre_set_by_user')), 'cut_label': str(asset.get('label') or 'Trailer'),
           'lineage': None, 'rough_cut': bool(session.get('rough_cut')), 'cast': list(session.get('cast') or []), 'comps': [], 'beats': [], 'scenes': [], 'dial': {}, 'seat_dials': {},
           'events': [], 'voices_live': [], 'probes': [], 'guide': None, 'watch': ((session.get('asset_reads') or {}).get(str(asset.get('id'))) or {}).get('watch') or {},
           'scorecard': None, 'logline': session.get('logline') or '', 'created_by': session.get('created_by'), 'version': session.get('version')}
    sub['media'].setdefault('title', sub['title'])
    PI.prepare_watch(sub, twins_by_key, duration_s=float((analysis or {}).get('runtime_s') or asset.get('runtime_s') or 0), title=sub['media'].get('title') or '',
                     transcript=transcript or (analysis or {}).get('transcript'), analysis=analysis, models=models, usage_extras=usage_extras, progress=progress, s3=s3)
    rec = {'id': str(asset.get('id')), 'slot': int(asset.get('slot') or 1), 'label': sub['cut_label'], 'runtime_s': sub['media'].get('runtime_s'), 'scenes': sub.get('scenes'), 'beats': sub.get('beats'),
           'dial': (sub.get('dial') or {}).get('value'), 't': (sub.get('dial') or {}).get('t'), 'splits': (sub.get('dial') or {}).get('splits'), 'seat_dials': sub.get('seat_dials'),
           'events': sub.get('events'), 'scorecard': sub.get('scorecard'), 'probes': sub.get('probes'), 'watch': sub.get('watch'), 'voices_live': sub.get('voices_live')}
    if mode_of(session) in ('trailer', 'spot'):
        try:
            rec['trailer'] = trailer_read(sub, list(twins_by_key.values())[0] if twins_by_key else {}, title=sub['media'].get('title') or '', logline=str(session.get('logline') or ''),
                                          models=models, usage_extras=usage_extras, asset_label=sub['cut_label'])
        except Exception as e:
            print(f"[twin-modes] extra trailer read skipped: {e}")
    session.setdefault('asset_reads', {})[str(asset.get('id'))] = rec
    session['updated_at'] = _now()
    return rec


def accept_image_name(filename: str, content_type: str = '') -> bool:
    import os
    ext = os.path.splitext(str(filename or ''))[1].lower()
    return ext in ALLOWED_IMAGE_EXT or str(content_type or '').lower() in ('image/jpeg', 'image/png', 'image/webp')


def accept_text_name(filename: str, content_type: str = '') -> bool:
    import os
    ext = os.path.splitext(str(filename or ''))[1].lower()
    return ext in ALLOWED_TEXT_EXT or str(content_type or '').lower() in ('text/plain', 'application/pdf', 'text/markdown')


def text_from_upload(raw: bytes, filename: str) -> str:
    """The script text from an upload: plain text as is, a pdf through
    pypdf when present. Never raises; empty when unreadable."""
    import os
    ext = os.path.splitext(str(filename or ''))[1].lower()
    if ext == '.pdf':
        try:
            import io
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(raw))
            return '\n'.join((p.extract_text() or '') for p in rd.pages)[:400000]
        except Exception as e:
            print(f"[twin-modes] pdf text skipped: {e}")
            return ''
    try:
        return raw.decode('utf-8', errors='ignore')[:400000]
    except Exception:
        return ''


def mode_lines(session: dict) -> list:
    """The mode read as a few plain lines (chat, CSV tail, exports)."""
    r = session.get('mode_read') or {}
    out = []
    if r.get('read'):
        out.append(str(r['read']))
    if (r.get('compare') or {}).get('read'):
        out.append(str(r['compare']['read']))
    if r.get('mode') in ('title', 'key_art'):
        for o in (r.get('options') or [])[:6]:
            out.append(f"{o['rank']}. {o['label']}: {pct1(o['preference_pct'])}% prefer it" + (f"; {o['reasons'][0]['line']}" if o.get('reasons') else ''))
    if r.get('mode') == 'script':
        for s in (r.get('scenes') or []):
            notes = [v for v in (s.get('notes') or {}).values() if v]
            if notes:
                out.append(f"{s['clock']} {s['slug']}: {' '.join(notes)}")
            if len(out) >= 12:
                break
    mk = session.get('marketing_read') or {}
    if mk.get('read'):
        out.append(mk['read'])
    return out


__all__ = ['MODE_SPECS', 'TAB_MODES', 'MODE_CARDS', 'MAX_OPTIONS', 'MAX_TRAILER_ASSETS', 'MAX_BINGE_ASSETS', 'ALLOWED_IMAGE_EXT', 'ALLOWED_TEXT_EXT',
           'mode_of', 'tab_of', 'mode_label', 'script_scenes', 'concept_scenes', 'option_scenes', 'captions_for_text_scenes', 'normalize_options', 'cold_interest',
           'trailer_read', 'trailer_compare', 'options_read', 'caption_art', 'concept_read', 'script_notes', 'episode_read', 'binge_read', 'marketing_read',
           'mode_norm_metrics', 'set_stimulus', 'text_analysis', 'finish_mode_read', 'prepare_extra_asset', 'accept_image_name', 'accept_text_name', 'text_from_upload', 'mode_lines',
           'ordered_probs', 'softmax_probs', 'blend_pick', 'argmax', 'publish_probs', 'seat_probs_for', 'CUTS', 'GRADE_SPREAD']
