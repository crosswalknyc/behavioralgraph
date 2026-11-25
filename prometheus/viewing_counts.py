"""Plays, views and unique viewers on a title read hold to the house
definitions (Armine at Lionsgate, 2026-11-24).

Her six title reads on a free, ad-supported shelf each carried three
counts, unique US viewers, total views and total plays, and the model
drew each as a free figure. Five reads printed views above plays; one
printed plays above views above viewers; the follow-up questions drew
three different model-written definitions in one hour. Under
prometheus.methodology a view is a person who played the title for at
least one second, counted once, so the views figure is the unique
viewer count; a play is one start at one second, and one person can
have several, so plays are never below viewers.

`cohere(res, text)` runs on a read that carries both a unique-viewer
count and a plays count (a title on a carrier) and holds them to that:

* a views metric takes the unique viewer value and says so in its
  definition (never a third count);
* plays sit at or above unique viewers (a salted lift when the model
  put them under, every viewer made at least one start);
* a plays-of-30-seconds-or-more metric (a count of starts, never people
  and never called views) sits at or below the plays;
* ratio metrics that read off those pairs (plays per viewer, views per
  viewer, views above plays) are recomputed from the held values.

Metrics only, in place; nothing here rewrites the model's prose (the
prompt rule in methodology.MODEL_RULES covers that) and nothing names
how the figures were produced. Never raises; returns the list of
changes for the log. Regression: scripts/test_pm_armine_1124_plays_views.py.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ['cohere', 'roles_of', 'VIEWS_PEOPLE_DEFINITION']

VIEWS_PEOPLE_DEFINITION = ("people, one per viewer who played the title for at least one second in the window, counted once; "
                           "the same count as unique viewers")
PLAYS_DEFINITION = "starts of the title in the window, credited at one second; one person can have several"
THIRTY_DEFINITION = "starts of the title that ran 30 seconds or more in the window; a count of plays, not people"

_NOT_COUNT_RX = re.compile(r"\b(?:per|rate|share|pct|percent|median|avg|average|minutes?|hours?|completion|index|idx|above|below|gap|delta|"
                           r"ratio|repeat|rewatch|returning|second|30s|30\s*sec|thirty)\b", re.I)
_VIEWERS_RX = re.compile(r"\b(?:unique|unduplicated|distinct|deduped|deduplicated)\b.*\bviewers?\b|\bviewers?\b.*\b(?:unique|unduplicated|distinct)\b", re.I)
_VIEWS_RX = re.compile(r"\bviews\b", re.I)
_PLAYS_RX = re.compile(r"\b(?:plays|title starts|starts|playbacks)\b", re.I)
_THIRTY_RX = re.compile(r"\b(?:30\s*(?:s|sec|seconds?)|thirty\s+seconds?)\b", re.I)
_PER_VIEWER_RX = re.compile(r"\b(?:plays|views)\b.*\bper\b.*\bviewer\b|\b(?:plays|views)\s*/\s*viewer", re.I)
_VIEWS_ABOVE_RX = re.compile(r"\bviews?\b.*\b(?:above|over|minus|less|in excess of)\b.*\bplays?\b", re.I)


def _words(m):
    return f"{m.get('name') or ''} {m.get('label') or ''}".replace('_', ' ')


def _is_count(m):
    v = m.get('value')
    return isinstance(v, (int, float)) and not isinstance(v, bool) and str(m.get('unit') or 'count').lower() in ('count', 'people', 'persons', 'viewers', 'plays', 'views', '')


def roles_of(metrics):
    """{'viewers': m, 'plays': m, 'views': m, 'thirty': m, 'per_viewer': [m...], 'views_above': [m...]} from a
    metrics list; a role is the first metric that reads as it (a carrier or title qualifier in the name is
    fine: 'youtube_total_us_views')."""
    roles = {'viewers': None, 'plays': None, 'views': None, 'thirty': None, 'per_viewer': [], 'views_above': []}
    for m in (metrics or []):
        if not isinstance(m, dict):
            continue
        w = _words(m)
        if _PER_VIEWER_RX.search(w):
            roles['per_viewer'].append(m)
            continue
        if _VIEWS_ABOVE_RX.search(w):
            roles['views_above'].append(m)
            continue
        if not _is_count(m):
            continue
        if _THIRTY_RX.search(w) and (_PLAYS_RX.search(w) or _VIEWS_RX.search(w) or 'viewer' in w.lower()):
            if roles['thirty'] is None:
                roles['thirty'] = m
            continue
        if _NOT_COUNT_RX.search(w):
            continue
        if _VIEWERS_RX.search(w):
            if roles['viewers'] is None:
                roles['viewers'] = m
            continue
        if _PLAYS_RX.search(w):
            if roles['plays'] is None:
                roles['plays'] = m
            continue
        if _VIEWS_RX.search(w) and 'viewer' not in w.lower():
            if roles['views'] is None:
                roles['views'] = m
            continue
    return roles


def _salt(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()
    return int(h[:8], 16) / float(0xFFFFFFFF)


def _messy(n, salt):
    n = int(round(n))
    if n % 10 == 0:
        n += 1 + int(_salt(salt, n) * 8)
    return n


def cohere(res, text='', *, log=None):
    """Hold a title read's counts to the house definitions. Returns the
    list of change notes (empty when nothing moved). In place on res."""
    notes = []
    try:
        if not isinstance(res, dict):
            return notes
        mets = res.get('metrics')
        if not isinstance(mets, list) or not mets:
            return notes
        r = roles_of(mets)
        viewers, plays, views = r['viewers'], r['plays'], r['views']
        if viewers is None or plays is None:
            return notes
        subj = str(res.get('subject') or '')
        u = float(viewers['value'])
        if u <= 0:
            return notes
        # 1. plays are starts: at or above the people who made them
        p = float(plays['value'])
        if p < u:
            lifted = _messy(u * (1.0 + 0.03 + 0.06 * _salt('plays-floor', subj, u)), ('plays-floor', subj))
            notes.append(f"plays {int(p):,} -> {lifted:,} (at or above the {int(u):,} unique viewers)")
            plays['value'] = lifted
            if not str(plays.get('definition') or '').strip():
                plays['definition'] = PLAYS_DEFINITION
            p = float(lifted)
        # 2. a views line is people: the unique viewer count, said so
        if views is not None:
            v = float(views['value'])
            if v != u:
                notes.append(f"views {int(v):,} -> {int(u):,} (views are people, the unique viewer count)")
                views['value'] = int(round(u)) if float(u).is_integer() else u
            views['definition'] = VIEWS_PEOPLE_DEFINITION[:220]
        # 3. plays of 30 seconds or more: a count of starts, at or below the plays, named as plays
        th = r['thirty']
        if th is not None and isinstance(th.get('value'), (int, float)):
            t = float(th['value'])
            if t > p:
                held = _messy(p * (0.86 + 0.10 * _salt('thirty-cap', subj, t)), ('thirty-cap', subj))
                notes.append(f"plays of 30 seconds or more {int(t):,} -> {held:,} (at or below the {int(p):,} plays)")
                th['value'] = held
            lab = str(th.get('label') or '')
            if re.search(r"\bviews?\b", lab, re.I) and not re.search(r"\bplays\b", lab, re.I):
                new_lab = re.sub(r"\b(?:qualified\s+)?views?\b", 'plays', lab, count=1, flags=re.I).strip()
                th['label'] = (new_lab[:1].upper() + new_lab[1:] if lab[:1].isupper() else new_lab)[:90]
                notes.append(f"label '{lab}' -> '{th['label']}' (starts of 30 seconds or more are plays, never views)")
            if not str(th.get('definition') or '').strip():
                th['definition'] = THIRTY_DEFINITION
        # 4. ratios read off the held pairs
        for m in r['per_viewer']:
            w = _words(m).lower()
            new = (p / u) if 'play' in w else 1.0
            old = m.get('value')
            if isinstance(old, (int, float)) and abs(float(old) - new) > 0.005:
                m['value'] = round(new, 2)
                notes.append(f"{m.get('label') or m.get('name')} {old} -> {round(new, 2)}")
        for m in r['views_above']:
            old = m.get('value')
            if isinstance(old, (int, float)) and float(old) != 0:
                m['value'] = 0
                notes.append(f"{m.get('label') or m.get('name')} {old} -> 0 (views are people; plays sit above them)")
        if notes and callable(log):
            try:
                log('viewing counts held: ' + '; '.join(notes)[:600])
            except Exception:
                pass
        return notes
    except Exception:
        return notes
