"""The existing-profile question and the answers to it (audit of
2026-11-17; Jenna: "audit questions asked today and see how we did and
make smarter from misses").

Armine asked for a profile of Euphoria four ways that afternoon and none
of them built it:

  "Run a profile for Euphoria"
      a correct Euphoria brief, with "We have Sephora (built Oct 16,
      2026) - is that what you meant?" under it. Sephora is a beauty
      retailer; the brief's own subject line read "Euphoria, the series
      on Max".
  "Build Euphoria series viewers"            (typed at that question)
      the Sephora question again.
  "no, build fresh"                          (the chip, twenty minutes on,
                                              the page had lost the brief)
      a read on "Build Euphoria series", not a build.
  "Run a profile for Euphoria series viewers"
      "I could not find Euphoria US as a brand, person, or title ... Did
      you mean Euphoria?" The suggestion was the name she typed.

What holds it now:

  own_subject     the typed name shares no naming word with the profile on
                  offer AND the brief's own identity line names the typed
                  subject (or the brief says it is a series and the
                  profile on offer is not one): that is a different thing,
                  not a spelling. No question; the brief builds what was
                  typed. A changed spelling the brief adopted ("Ordeal"
                  drafted as L'Oreal) is still asked about.
  reply_reading   at the question, the subject said again as an order is a
                  no (build it as stated); the profile on offer named on
                  its own is a yes.
  asked_before    one profile is asked about once in a thread, never twice.
  lane            the answer chips ("no, build fresh", "yes, use it")
                  resolved from the question in the thread when the page
                  no longer holds the brief.
  unresolved      a suggestion that is the name the person typed is taken,
                  not offered back; a sentence is never echoed as a name,
                  whatever its length (2026-11-18, prometheus.followup_refine).

Pure text; no Flask, no network, no model call. Never raises. Wired in
app._apply_universe_qualifier_gate, prometheus.legacy.chat (the
qualifier_match step, the gate call, the subject check) and
prometheus.service (lane: match_answer). Regression:
scripts/test_pm_audit_2026_11_17.py.
"""
from __future__ import annotations

import re

__all__ = ['parse_question', 'last_question', 'asked_before', 'reply_reading', 'own_subject', 'lane', 'unresolved']

_Q_RX = re.compile(
    r"\bWe have (?P<cand>[^\n?]{1,160}?)(?:\s+\((?:built|refreshed|updated)\b[^)]*\))?\s+-\s+is that what you meant\?", re.I)
_SUBJ_RX = re.compile(r"\bno\s+(?:builds|to build)\s+(?P<subj>[^\n]{1,160}?)\s+fresh\b", re.I)
_NO_RX = re.compile(
    r"\b(no|nope|nah|not that|fresh|new|build fresh|something else|different|as stated|keep mine)\b", re.I)
_YES_RX = re.compile(
    r"\b(yes|yep|yeah|yup|correct|exactly|that one|that works|use it|use that|i meant that)\b", re.I)
_MAX_REPLY = 120
_UNRESOLVED_COPY = (
    "I could not find {subj} as a brand, person, or title, so I have "
    "not set up a build for it. Check the spelling, or tell me who or "
    "what it is (a website or the platform it lives on is enough) and "
    "I will set up the brief.")
_UNRESOLVED_LONG_COPY = (
    "I could not tell which brand, person, or title that is about, so I "
    "have not set up a build. Give me the one name the profile should be "
    "built on (a website or the platform it lives on helps) and I will "
    "set up the brief.")


def _squash(s):
    return ' '.join(str(s or '').split())


def _tokens(s):
    try:
        from .subject_rules import identity_tokens
        return set(identity_tokens(s))
    except Exception:
        return set()


def _fold(s):
    return ' '.join(re.sub(r"[^a-z0-9 ]+", ' ', str(s or '').lower()).split())


def _head(name):
    return str(name or '').split(' - ', 1)[0].strip()


def parse_question(text):
    """{'candidate', 'subject'} for an agent turn that asks the
    existing-profile question, else None. The last question in the turn
    wins (the page prints it under a brief)."""
    try:
        t = str(text or '')
        hits = list(_Q_RX.finditer(t))
        if not hits:
            return None
        m = hits[-1]
        cand = _squash(m.group('cand')).strip(' .,;:')
        if not cand:
            return None
        s = _SUBJ_RX.search(t, m.end())
        subj = _squash(s.group('subj')).strip(' .,;:') if s else ''
        return {'candidate': cand, 'subject': subj}
    except Exception:
        return None


def _agent_turns(history):
    out = []
    for t in (history or []):
        if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant'):
            out.append(t)
    return out


def last_question(history):
    """The question the previous agent turn asked, or None when the
    previous agent turn was something else."""
    try:
        turns = _agent_turns(history)
        if not turns:
            return None
        return parse_question(turns[-1].get('text'))
    except Exception:
        return None


def asked_before(history, candidate, max_turns=16):
    """True when this thread already asked whether ``candidate`` was the
    profile meant. One profile is asked about once."""
    try:
        want = _fold(_head(candidate))
        if not want:
            return False
        for t in _agent_turns(history)[-max_turns:]:
            q = parse_question(t.get('text'))
            if q and _fold(_head(q['candidate'])) == want:
                return True
        return False
    except Exception:
        return False


def reply_reading(answer, subject='', candidate=''):
    """(said_yes, said_no) for a reply to the question. The plain words
    are read first. A reply with neither that names the subject again
    and not the profile on offer ("Build Euphoria series viewers") is a
    no: build it as stated. One that names only the profile on offer is
    a yes."""
    try:
        low = _squash(answer).lower()
        if not low:
            return False, False
        yes, no = bool(_YES_RX.search(low)), bool(_NO_RX.search(low))
        if yes or no or len(low) > _MAX_REPLY:
            return yes, no
        at, st, ct = _tokens(low), _tokens(subject), _tokens(_head(candidate))
        if st and st <= at and not (ct & at):
            return False, True
        if ct and ct <= at and not (st & at):
            return True, False
        return False, False
    except Exception:
        return False, False


def own_subject(typed, draft, candidate, kind_conflict=False):
    """True when the name the person typed is its own subject, not a
    spelling of the profile on offer. Both must hold: the typed name and
    the profile share no naming word, and the brief itself says so (its
    identity line names the typed subject and not the profile, or the
    kind of thing the brief describes is not the kind the profile is)."""
    try:
        tt, ct = _tokens(typed), _tokens(_head(candidate))
        if not tt or not ct or (tt & ct):
            return False
        if kind_conflict:
            return True
        d = draft if isinstance(draft, dict) else {}
        text = ' '.join(str(d.get(k) or '') for k in ('resolved_identity', 'resolved_title', 'identity_note'))
        it, fold = _tokens(text), _fold(text)
        if not it or not tt <= it:
            return False
        if (ct & it) or any(len(c) >= 5 and c in fold for c in ct):
            return False
        return True
    except Exception:
        return False


def lane(text, history):
    """The answer chips with nothing armed (the page lost the brief).
    Returns {'kind': 'no'|'yes', 'rewrite', 'subject', 'candidate'} when
    the previous agent turn asked the question and these words answer
    it, else None."""
    try:
        t = _squash(text)
        if not t or len(t) > _MAX_REPLY:
            return None
        q = last_question(history)
        if not q:
            return None
        # only the plain words: the subject said again as its own order
        # runs as typed, and the build flow does not ask a second time
        yes, no = bool(_YES_RX.search(t)), bool(_NO_RX.search(t))
        if yes == no:
            return None
        if no:
            if not q.get('subject'):
                return None
            return {'kind': 'no', 'rewrite': f"Build a new profile on {q['subject']}",
                    'subject': q['subject'], 'candidate': q['candidate']}
        return {'kind': 'yes', 'rewrite': f"Run a profile for {_head(q['candidate'])}",
                'subject': _head(q['candidate']), 'candidate': q['candidate']}
    except Exception:
        return None


def unresolved(subj, suggest, ask_text):
    """What to do with a build subject that did not resolve.

    {'adopt': name} when the suggestion is the name the person typed
    (every naming word of what they typed, nothing more): the brief
    carries on with it. Otherwise {'copy', 'chips'}: the reply and its
    one chip. A subject that is a sentence is never echoed."""
    try:
        name, sug = _squash(subj), _squash(suggest)
        differs = bool(sug) and sug.lower() != name.lower()
        if differs:
            try:
                from .subject_rules import explicit_build_subject
                typed = str(explicit_build_subject(ask_text) or '').strip()
            except Exception:
                typed = ''
            st, tt = _tokens(sug), _tokens(typed)
            if st and tt and st == tt:
                return {'adopt': sug}
        long_name = len(name.split()) > 8 or len(name) > 70
        if not long_name:
            # a sentence of any length is never said back as a name (Grace Yin 2026-11-18: "I could not find But
            # The Title Just Launched Svod as a brand, person, or title"); prometheus.followup_refine reads it
            try:
                from . import followup_refine as _fur
                long_name = bool(_fur.never_echo(name, ask_text))
            except Exception:
                long_name = False
        copy = _UNRESOLVED_LONG_COPY if long_name else _UNRESOLVED_COPY.format(subj=name)
        chips = [f"Build a profile for {sug}"] if differs else []
        if chips:
            copy += f" Did you mean {sug}?"
        return {'copy': copy, 'chips': chips}
    except Exception:
        return {'copy': _UNRESOLVED_LONG_COPY, 'chips': []}
