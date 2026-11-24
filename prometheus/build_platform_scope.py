"""A platform named in a build order scopes the audience; it is a cut of
the subject, never dropped and never the subject (Nov 23 2026 ask audit,
fix 4; Armine Meseldzhyan at Lionsgate).

"can we build a profile of Paul Rodriquez on YouTube" and, a minute
later, "Build a profile of Paul Rodriguez only on YouTube" both came back
as the existing whole-audience Paul Rodriguez profile ("I already have
Paul Rodriguez from 6 days ago"), the YouTube scope gone. She pushed back
("pull fresh") and the window question that followed ended the thread.

The house rule for an embedded qualifier (avid-and-cut-skin-rules 6b and
3c): the Total Universe is the full universe and every qualifier rides as
a derived cut named '{Subject} - {Cut}'. A platform the order scopes the
subject to is a behavioral cut, '{Platform} users', sized by the parent's
own row for that platform and the platform's US audience (the two
ceilings in 3c). So an existing match for the whole audience becomes a
derive_cut of that file with the platform cut; the parent stays as it is.

What is NOT re-routed:

* a universe that IS a platform's audience ("YouTube viewers", "who uses
  TikTok"): the platform is the subject;
* a consumption universe on a named service ("Example Show viewers on
  Hulu", "people who watched X on Netflix"): the named service defines
  that universe (the StreamScout contract), never a cut;
* a content or platform subject (a series, a film, a podcast, a
  service): the scope there is the title's carriage, not a behavior cut;
* a draft that already carries the platform in its cut or its name;
* two or more platforms named (the order is for the model to settle).

Pure dicts and text. Never raises; returns True when it re-routed.
Regression: scripts/test_pm_audit_1123_platform_build.py.
"""
from __future__ import annotations

import re

from . import platform_scope as _ps

__all__ = ['apply', 'platform_scope_of', 'is_consumption_universe']

_CONSUMER_NOUN_RX = re.compile(
    r"\b(?:viewers?|watchers?|listeners?|players?|readers?|streamers?|subscribers?|members?|customers?|buyers?|renters?|"
    r"shoppers?|users?|audience)\b", re.I)
_WATCH_VERB_RX = re.compile(r"\b(?:watch(?:ed|ing|es)?|stream(?:ed|ing|s)?|listen(?:ed|ing|s)?|play(?:ed|ing|s)?|read(?:ing|s)?|"
                            r"saw|seen|view(?:ed|ing|s)?|binge[ds]?|subscrib(?:e|ed|es|ing))\b", re.I)
_CONTENT_CATEGORY_RX = re.compile(r"^(?:SERIES\b|MOVIE\b|PODCAST\b|GAMES?\b|GAME PLAYERS\b|VERTICAL SHORTS\b|BROADCAST|STREAMING|FAST |APP/PLATFORM|"
                                  r"PLATFORMS\b|SOCIAL MEDIA\b|SEARCH ENGINE|MEDIA\b|VMVPD|VIRTUAL MVPD|MOVIE THEATER\b)", re.I)


def platform_scope_of(text):
    """The one platform the words scope the subject to ("on YouTube",
    "only on YouTube", "YouTube only"), or '' when none or several, or
    when the platform is the subject of the ask itself."""
    try:
        named = _ps.named(text)
    except Exception:
        return ''
    scoped = [p for p, how in (named or {}).items() if how == 'scope']
    # one platform in the whole order, named as where: a second platform in any role ("on YouTube and TikTok")
    # or the platform as the subject itself leaves the order to the model
    if len(scoped) != 1 or len(named or {}) != 1:
        return ''
    return scoped[0]


def is_consumption_universe(text, subject=''):
    """True when the order describes people consuming something on the
    service: a consumer noun or a watch verb beside the platform. That
    universe is defined by the service and is never cut by it."""
    blob = f"{subject or ''} {text or ''}"
    return bool(_CONSUMER_NOUN_RX.search(blob)) or bool(_WATCH_VERB_RX.search(str(text or '')))


def apply(draft, text, compose=None):
    """Re-route an existing_match whose order scopes the subject to a
    platform into a derive_cut of that file with a '{Platform} users'
    cut. Mutates the draft in place. Returns True when it acted."""
    try:
        if not isinstance(draft, dict):
            return False
        decision = str(draft.get('decision') or '').strip().lower()
        if decision != 'existing_match':
            return False
        plat = platform_scope_of(text)
        if not plat:
            return False
        display = str(draft.get('existing_match_display_name') or '').strip()
        subject = str(draft.get('subject') or display).strip()
        base = (display or subject).split(' - ', 1)[0].strip()
        if not base or _ps.canon(base) or _ps.canon(subject.split(' - ', 1)[0]):
            return False               # the platform's own audience is the subject
        if is_consumption_universe(text, subject):
            return False               # a viewers / listeners universe on a named service is that universe
        cat = str(draft.get('brand_category') or '').strip()
        if cat and _CONTENT_CATEGORY_RX.match(cat):
            return False               # a title or a service: the scope is carriage, not a behavior cut
        label = f"{plat} users"
        norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()
        carriers = [display, subject, str(draft.get('cut_label') or ''), str(draft.get('cut_label_clean') or '')]
        if any(norm(plat) in norm(c) for c in carriers if c):
            return False               # the match or the draft already carries the platform
        parent_key = str(draft.get('existing_match_s3_key') or '').strip()
        draft['decision'] = 'derive_cut'
        if parent_key:
            draft['existing_match_s3_key'] = parent_key
        draft['derive_type'] = 'behavioral'
        draft['cut_label'] = label
        draft['cut_label_clean'] = label
        draft['parent_display_name'] = base
        draft['cohort_description'] = f"People in the {base} audience who use {plat}: the {base} profile cut to its {plat} users."
        try:
            name = compose(base, label) if callable(compose) else ''
        except Exception:
            name = ''
        name = str(name or '').strip() or f"{base} - {label}"
        draft['subject'] = name
        draft['name'] = name
        draft.pop('file_stem', None)
        draft['run_avid'] = False
        note = (f"{plat} scopes the audience, so this ships as a cut of the {base} profile on file: "
                f"'{name}', sized to the {base} people who use {plat}. The {base} profile itself is unchanged.")
        draft['_platform_scope_note'] = note
        assumptions = draft.get('assumptions')
        if isinstance(assumptions, list):
            assumptions.append(note)
        elif isinstance(assumptions, str) and assumptions.strip():
            draft['assumptions'] = assumptions.rstrip() + ' ' + note
        else:
            draft['assumptions'] = [note]
        return True
    except Exception:
        return False
