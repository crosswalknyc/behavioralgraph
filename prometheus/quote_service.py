"""A quote never swaps a named service silently (Jenna 2026-11-12).

"How many people watched Fallout Season 2 on Netflix?" was quoted "I can
pull how many people in the US watched Fallout Season 2 on Prime Video
across one year." The profile on record is the title on Prime Video; the
quote printed the record's name and the service the reader named was
gone, with nothing said.

The rule, held at the one place a quote is made
(`priced_read.offer_or_charge`), before anything is priced:

  * the reader named a service, and the subject about to be quoted is
    the title on a different one;
  * when the carrier record shows that other service is the title's only
    US home in the window, the quote stays on it and opens with one plain
    sentence saying so ("Fallout Season 2 is on Prime Video in the US,
    not Netflix."), so the read that is priced is a read on a service the
    title is on;
  * when the record cannot settle it (several homes, no check on record,
    a check that does not cover the window), the quote is on what was
    asked: the title on the service the reader named.

A count question ("How many people watched Fallout Season 2 on
Netflix?") is not quoted at all when the dashboard already holds the
figure on the title's home: the sentence, then the figure, worded by the
lane that answers the same question with no service in it
(`held_figure`). The reader is never charged for a figure we hold
because they named the wrong service.

Nothing is decided here about whether a held profile answers free, and
nothing is priced here. The readings of services, titles and the carrier
record are prometheus.service_scope_match's; this module only calls
them. Never raises: on any doubt the subject is returned as it came.
"""
from __future__ import annotations

import re

__all__ = ['settle', 'names', 'held_figure']

_NOUN_RX = re.compile(r"\b(viewers?|watchers?|streamers?|listeners?)\b", re.I)


def names(keys, ssm=None):
    """Service names as a reader knows them, joined in plain words."""
    if ssm is None:
        from . import service_scope_match as ssm
    order = sorted(str(k) for k in keys)
    out = [ssm.home_name(k) for k in order]
    if len(out) <= 1:
        return ''.join(out)
    return ', '.join(out[:-1]) + ' and ' + out[-1]


def _noun(subject):
    m = None
    for m in _NOUN_RX.finditer(str(subject or '')):
        pass
    return m.group(1) if m else 'viewers'


def _home(ssm, title, text, record, window):
    """The title's only US home by the carrier record, as a service key,
    or ''. The window is the one the ask states. An ask that states none
    is about the profile on record, so it is judged on the window the
    record's own check covers (a check always ends a little before
    today; the standing trailing year would never sit inside it)."""
    try:
        if window:
            return ssm.single_home(title, window, record=record)
        asked_win = ssm.window_of_draft({}, text)
        got = ssm.single_home(title, asked_win, record=record) if isinstance(asked_win, dict) else ''
        if got or asked_win != ssm.window_of_draft({}, ''):
            return got                               # settled, or the words state their own dates
        rec = record
        if rec is None:
            try:
                from migration import service_scope_sizing as sss
            except Exception:
                import service_scope_sizing as sss  # type: ignore
            rec = sss.load_record(title)
        checked = ((rec or {}).get('carriers') or {}).get('window') if isinstance(rec, dict) else None
        return ssm.single_home(title, checked, record=rec) if isinstance(checked, dict) else ''
    except Exception:
        return ''


def settle(text, history, subject, record=None, window=None):
    """(subject to quote, opening sentence or '').

    `record` is the title's carrier record when the caller already holds
    it (tests hand one in); `window` the window the ask is on."""
    subj = ' '.join(str(subject or '').split())
    try:
        from . import service_scope_match as ssm
        held = ssm.services_of_name(subj)
        if not subj or not held:
            return subject, ''
        asked = ssm.services_in_question(text, known=held)
        if not asked:
            return subject, ''                       # the reader named no service
        if asked <= held:
            # The subject came in on the service the reader named (it is read from the words on some
            # turns and from the dashboard on others; the same question was quoted on Netflix three
            # times in seven on prod, 2026-11-12). The record is asked all the same: a read is never
            # priced on a service the title is not on.
            title = ssm.title_of_name(subj)
            home = _home(ssm, title, text, record, window) if title else ''
            if home and home not in held:
                return (f"{title} {_noun(subj)} on {ssm.home_name(home)}",
                        f"{title} is on {ssm.home_name(home)} in the US, not {names(held, ssm)}.")
            return subject, ''                       # only services the subject is on, and nothing on record says otherwise
        other = asked - held
        title = ssm.title_of_name(subj)
        if not title:
            return subject, ''
        home = _home(ssm, title, text, record, window)
        if home and home in held:
            # the record settles it: the title's only US home is the one we hold
            return subject, f"{title} is on {ssm.home_name(home)} in the US, not {names(other, ssm)}."
        # the record cannot settle it: the quote is on what was asked
        return f"{title} {_noun(subj)} on {names(asked, ssm)}", ''
    except Exception:
        return subject, ''


_FIGURE_RX = re.compile(r"\d[\d,]{3,}\s+US people\b")


def held_figure(text, subject, sentence, ctx=None):
    """The whole reply for a count question about a title on a service it
    is not on, when the figure on its home is already on the dashboard:
    `sentence`, then the figure as the catalog lane words it for the same
    question with no service in it. None when the words are not a count
    question, state their own dates, or nothing is held (the quote on the
    title's home then stands, opening with the sentence)."""
    try:
        if not sentence:
            return None
        from . import catalog_lane as cl
        from . import service_scope_match as ssm
        parsed = cl.parse(text)
        if not parsed or parsed[0] != 'size':
            return None
        if ssm.window_of_draft({}, text) != ssm.window_of_draft({}, ''):
            return None                              # the words state their own dates
        title = ssm.title_of_name(' '.join(str(subject or '').split()))
        if not title:
            return None
        got = cl.answer(f"How many people watched {title}?", ctx)
        if not isinstance(got, dict) or got.get('memory_confirm') or got.get('roster_ask'):
            return None
        words = ' '.join(str(got.get('reply') or '').split())
        if not _FIGURE_RX.search(words):
            return None
        # only the figure for that home: the same title, on the service the sentence names and no other
        got_subj = ' '.join(str(got.get('subject') or '').split())
        on, home = ssm.services_of_name(got_subj), ssm.services_of_name(' '.join(str(subject or '').split()))
        if not on or not home or not on <= home:
            return None
        if ' '.join(str(ssm.title_of_name(got_subj) or '').lower().split()) != ' '.join(title.lower().split()):
            return None
        out = dict(got)
        out['reply'] = f"{sentence} {words}"
        out['service_settled'] = True
        return out
    except Exception:
        return None
