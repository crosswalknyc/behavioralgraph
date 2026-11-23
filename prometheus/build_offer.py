"""The build offer on a read about a subject we do not hold (Jenna 2026-11-23).

Jenna approved building profiles for subjects people ask about that we do
not hold. Emma Crouchen's VICE Sports question (2026-11-20) found no VICE
Sports profile and was answered off the Pinterest profile open on her
screen; the read is now researched on the subject she named
(prometheus.knowledge.read_blocks), and its answer ends with one plain
offer:

    We don't have a full profile of VICE Sports yet. I can build one for
    you for $300. Tap "Build a full profile of VICE Sports" and I'll set it
    up for you to review. You only pay if you approve it.

* The price is the reader's own build price, read from the same table the
  build brief prints (host.price_table_for: company rates and currency
  included).
* The chip asks for the build in words. The standard interpret step
  decides what it is (an existing profile, a cut off a parent, a new
  build) and the brief waits for the reader's approval. Nothing is charged
  and nothing is queued here.
* Once per thread: a thread that already carried the offer for the
  subject, or already asked for its build, does not get it again.
* Only in the chat: the dashboard thread and the job result the widget
  reads. The emailed read, the partner API's result and a probe never
  carry it, and a held read (the calm line) carries none.
* Only for a subject we do not hold (the catalog checked first, a cut
  counted as its parent) and only for a reader who can start a build
  (host.gate_pull). The subject is the one the words named; the profile
  open on the screen never stands in.

Every read on such a subject is recorded and its size saved
(migration/unheld_subjects), whether or not the offer shows. Never raises:
any failure returns the payload as it came. Regression:
scripts/test_unheld_subjects.py (parent repo).
"""
from __future__ import annotations

import re

from .host import host
from . import faults as _faults

OFFER_LEAD = "We don't have a full profile of "
_OFFER_RX = re.compile(r"We don't have a full profile of (.+?) yet\.", re.I)
_CHIP_RX = re.compile(r"^\s*build a full profile of (.+?)\s*$", re.I)
_BUILD_VERB_RX = re.compile(r"\b(?:build|run|pull|make|create|start|order)\b", re.I)


def _uh():
    from migration import unheld_subjects as m
    return m


def _knowledge(base):
    kn = (base or {}).get('_knowledge') if isinstance(base, dict) else None
    return kn if isinstance(kn, dict) else {}


def subject_of(base) -> str:
    """The subject a finished read was about when we hold no profile of
    it: the subject the words named (the knowledge layer's own call),
    never the profile open on the screen. '' otherwise."""
    kn = _knowledge(base)
    if not kn.get('unheld'):
        return ''
    return str(kn.get('subject') or kn.get('named_subject') or '').strip()


def offer_text(name, price_label=''):
    """(the closing line, the chip) for a subject."""
    chip = _uh().build_ask(name)
    cost = f"I can build one for you for {price_label}." if price_label else "I can build one for you."
    return (f"{OFFER_LEAD}{name} yet. {cost} Tap \"{chip}\" and I'll set it up for you to review. "
            "You only pay if you approve it."), chip


def _price_label(user) -> str:
    v = 0.0
    try:
        tab = host.price_table_for(user) if host.has('price_table_for') else None
        v = float((tab or {}).get('new_build') or 0)
    except Exception:
        v = 0.0
    if v <= 0:
        try:
            from . import rate_card as _rc
            v = float(_rc.default('profile_iq_build') or 0)
        except Exception:
            v = 0.0
    if v <= 0:
        return ''
    try:
        from prometheus.legacy.watch import _pm_usd_label
        lbl = _pm_usd_label(v, user)
        if lbl:
            return lbl
    except Exception:
        pass
    return f"${v:,.0f}" if abs(v - round(v)) < 0.005 else f"${v:,.2f}"


def _can_build(user) -> bool:
    try:
        data = host.load_users() if host.has('load_users') else {}
        row = ((data or {}).get('users') or {}).get(user) or {}
        return bool(row) and bool(host.gate_pull(row))
    except Exception:
        return False


def _thread_of(user, job_id, thread_id) -> str:
    if str(thread_id or '').strip():
        return str(thread_id).strip()
    try:
        if job_id and host.has('thread_for_job'):
            t = host.thread_for_job(user, job_id)
            if t:
                return str(t)
    except Exception:
        pass
    try:
        from flask import g as _g, has_request_context
        if has_request_context():
            return str(getattr(_g, '_pm_thread_id', '') or '')
    except Exception:
        pass
    return ''


def _via(job_id, via) -> str:
    if str(via or '').strip():
        return str(via).strip().lower()
    try:
        if job_id:
            from . import service as _svc
            v = _svc.job_doc_field(job_id, 'via')
            if v:
                return str(v).strip().lower()
    except Exception:
        pass
    try:
        from flask import g as _g, has_request_context
        if has_request_context() and getattr(_g, '_pm_api_key_owner', None):
            return 'api'
    except Exception:
        pass
    return 'dashboard'


def thread_had_offer(history, name) -> bool:
    """True when the thread already carried the offer for this subject or
    already asked for its build."""
    uh = _uh()
    key = uh.subject_key(name)
    if not key:
        return False
    for t in history or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get('role') or '').lower()
        txt = str(t.get('text') or t.get('content') or '')
        if role in ('agent', 'assistant'):
            for m in _OFFER_RX.finditer(txt):
                if uh.subject_key(m.group(1)) == key:
                    return True
            meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
            opts = list(meta.get('options') or []) + list(t.get('followups') or [])
            for o in opts:
                lbl = str((o.get('send') or o.get('label')) if isinstance(o, dict) else o or '')
                m = _CHIP_RX.match(lbl)
                if m and uh.subject_key(m.group(1)) == key:
                    return True
        elif role == 'user' and len(key) >= 4 and _BUILD_VERB_RX.search(txt) and re.search(r"\bprofiles?\b", txt, re.I):
            if key in _uh()._ss().core(txt):
                return True
    return False


def with_offer(payload, *, base=None, user='', question='', history=None, job_id='', thread_id='', via=None,
               held=False, probe=False):
    """The chat copy of a finished read: the read as it came, with the
    build offer at the end when the read was on a subject we do not hold.
    Records the read and saves the subject's size either way. Returns the
    payload unchanged when there is nothing to offer. Never raises."""
    try:
        if not isinstance(payload, dict) or not payload.get('success') or not str(payload.get('reply') or '').strip():
            return payload
        subject = subject_of(base)
        if not subject or probe or _faults.is_probe():
            return payload
        uh = _uh()
        name = uh.subject_name(subject)
        if not uh.subject_key(name) or uh.is_probe_user(user):
            return payload
        holds = uh.catalog_holds(name)
        if holds:
            return payload            # the catalog carries it, or its parent: an existing profile comes first
        kn = _knowledge(base)
        tid = _thread_of(user, job_id, thread_id)
        size = None
        if kn.get('size_source') == 'research' and kn.get('size_cap'):
            size = uh.save_size(name, kn.get('size_cap'), window=kn.get('size_window'), question=question,
                                aliases=(kn.get('named_subject') or '',), read_key=kn.get('key') or '')
        if not size:
            size = uh.saved_size(name)
        uh.record_read(name, user=user, thread_id=tid, asked_as=kn.get('named_subject') or subject,
                       question=question, size=size)
        if held or holds is None or _via(job_id, via) == 'api':
            return payload
        if (tid and uh.offered(name, user, tid)) or thread_had_offer(history, name):
            return payload
        if not _can_build(user):
            return payload
        line, chip = offer_text(name, _price_label(user))
        out = dict(payload)
        out['reply'] = str(payload.get('reply') or '').rstrip() + '\n\n' + line
        fu = [f for f in (payload.get('followups') or []) if isinstance(f, str) and f.strip() and f.strip() != chip]
        out['followups'] = [chip] + fu
        out['build_offer'] = {'subject': name, 'chip': chip}
        if tid:
            uh.mark_offered(name, user, tid)
        print(f"[build-offer] offered a full profile of {name!r} to {user} (thread {tid or 'unknown'})")
        return out
    except Exception as e:
        _faults.note('build_offer', e)
        return payload


__all__ = ['OFFER_LEAD', 'subject_of', 'offer_text', 'thread_had_offer', 'with_offer']
