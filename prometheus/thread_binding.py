"""The conversation follows the project (2026-10-21, Debi Pomerantz).

Debi, verbatim: "On prometheus - if I am switching between projects, it
would be great if the conversation thread could switch with me, just
like it would in claude or chat." She kept separate threads; flipping
from one profile to another left the chat on the previous thread, one
long thread that "kind of" remembered the previous project. That bleed
is the subject-confusion behind a week of misroutes (Salish Matter in a
DraftKings ask, Golden Axe in an Among Us ask).

The model: a thread may carry a BINDING, the project the dashboard had
open when the thread was made::

    {'subject_key': 'profile:Netflix_10_01_2026_12_00.csv',
     'subject_label': 'Netflix', 'product': 'profileIQ'}

When the user opens a project the widget asks for that project's
thread: the one bound to the same subject_key becomes active (its
history loads); when none exists one is created, titled for the
project, and becomes active. A thread with no binding is the general
chat and is what the user is on when no project is open. Switching
back returns to the project's thread with its history intact. The
user can still open any thread from the rail by hand; doing so never
changes the page.

Where the binding is read: subject_rules.thread_subjects puts the
bound subject ahead of the subjects parsed out of the history and
ahead of the page, so a question in the Golden Axe thread is about
Golden Axe whatever the open tab happens to be (words still win).

Pure functions over the index document; no Flask, no S3. The service
and the legacy thread routes call these with the index they loaded and
persist what comes back.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# Views that are a PROJECT (one thing the user is looking at) and so
# carry a thread. Other views (trends, rankers, the workspace) have no
# single subject; the general chat serves them.
PROJECT_PRODUCTS = ('profileIQ', 'journeyIQ', 'intentIQ', 'attributionIQ',
                    'brandPartnershipIQ', 'subscriberIQ', 'flywheelConversion')

_VIEW_TO_KIND = {
    'profileIQ': 'profile', 'profile-iq': 'profile',
    'journeyIQ': 'journey',
    'intentIQ': 'campaign', 'attributionIQ': 'campaign',
    'brandPartnershipIQ': 'partnership',
    'subscriberIQ': 'title',
    'flywheelConversion': 'flywheel',
}


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _clean(s, limit=160):
    return ' '.join(str(s or '').split()).strip()[:limit]


def subject_key_for(page_context):
    """(subject_key, subject_label, product) for what the dashboard has
    open, or (None, None, None) when nothing project-shaped is open.

    A profile keys on its S3 key (two dated files of one subject are two
    projects, as the dashboard shows them). A journey, campaign or title
    keys on the identity the view serializer carries. The key is stable
    across page loads, which is what lets the thread come back."""
    pc = page_context if isinstance(page_context, dict) else {}
    prim = pc.get('primary') if isinstance(pc.get('primary'), dict) else {}
    vc = pc.get('view_context') if isinstance(pc.get('view_context'), dict) else {}
    view = _clean(vc.get('view_id') or pc.get('view') or '')
    if prim and prim.get('s3_key'):
        label = _clean(prim.get('name')) or re.sub(r"\.csv$", '', str(prim['s3_key'])).replace('_', ' ')
        return 'profile:' + _clean(prim['s3_key']), label, 'profileIQ'
    if view not in PROJECT_PRODUCTS or view == 'profileIQ':
        return None, None, None
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    ident = _clean(vc.get('subject_key') or data.get('key') or data.get('slug')
                   or data.get('title') or data.get('subject') or vc.get('view_title'))
    if not ident:
        return None, None, None
    label = _clean(vc.get('subject_label') or data.get('title') or data.get('subject')
                   or vc.get('view_title') or ident)
    kind = _VIEW_TO_KIND.get(view, view.lower())
    return f"{kind}:{ident}", label, view


def binding_from(subject_key, subject_label, product):
    """The binding dict a thread carries, or None for the general chat."""
    sk = _clean(subject_key)
    if not sk:
        return None
    return {'subject_key': sk,
            'subject_label': _clean(subject_label) or sk.split(':', 1)[-1],
            'product': _clean(product) or 'profileIQ'}


def binding_of_thread(idx, tid):
    """The binding on thread ``tid`` in ``idx``, or None."""
    for t in (idx or {}).get('threads', []) or []:
        if t.get('id') == tid:
            b = t.get('binding')
            return dict(b) if isinstance(b, dict) and b.get('subject_key') else None
    return None


def find_bound(idx, subject_key):
    """The most recently updated thread bound to ``subject_key``, or None."""
    sk = _clean(subject_key)
    if not sk:
        return None
    hits = [t for t in (idx or {}).get('threads', []) or []
            if isinstance(t.get('binding'), dict) and t['binding'].get('subject_key') == sk]
    if not hits:
        return None
    hits.sort(key=lambda t: str(t.get('updated') or ''), reverse=True)
    return hits[0]


def general_thread(idx):
    """The most recently updated thread with no binding, or None."""
    gen = [t for t in (idx or {}).get('threads', []) or []
           if not (isinstance(t.get('binding'), dict) and t['binding'].get('subject_key'))]
    if not gen:
        return None
    gen.sort(key=lambda t: str(t.get('updated') or ''), reverse=True)
    return gen[0]


def apply_cap(idx, cap, keep_id=None):
    """Hold the index at ``cap`` threads. Empty auto-made threads go
    first, then the oldest by last update; the thread being activated is
    never retired. Returns the ids retired (the caller deletes their
    bodies). The existing rule, that a thread with turns can roll off
    only when the cap is genuinely exceeded, is unchanged."""
    threads = list((idx or {}).get('threads', []) or [])
    if cap <= 0 or len(threads) <= cap:
        return []
    retired = []

    def _drop(pred):
        nonlocal threads
        cands = sorted([t for t in threads if pred(t) and t.get('id') != keep_id],
                       key=lambda t: str(t.get('updated') or ''))
        while len(threads) > cap and cands:
            gone = cands.pop(0)
            threads = [t for t in threads if t.get('id') != gone.get('id')]
            retired.append(gone.get('id'))

    _drop(lambda t: t.get('auto') and not t.get('turns'))      # empty project threads nobody wrote in
    _drop(lambda t: not t.get('turns'))                          # any other empty thread
    _drop(lambda t: True)                                        # then the oldest by update
    idx['threads'] = threads
    return retired


def bind(idx, subject_key, subject_label, product, *, cap=40, create=True):
    """Activate the thread for a project (or the general chat when the
    key is empty), creating it when needed. Mutates ``idx``.

    Returns {'tid', 'created', 'switched', 'binding', 'retired'}; tid is
    None only when create is False and nothing is bound."""
    idx.setdefault('threads', [])
    prev = idx.get('active')
    now = _now()
    b = binding_from(subject_key, subject_label, product)
    if b is None:
        th = general_thread(idx)
        created = False
        if th is None:
            if not create:
                return {'tid': None, 'created': False, 'switched': False, 'binding': None, 'retired': []}
            th = {'id': uuid.uuid4().hex[:10], 'title': 'New chat', 'created': now,
                  'updated': now, 'turns': 0}
            idx['threads'].append(th)
            created = True
    else:
        th = find_bound(idx, b['subject_key'])
        created = False
        if th is None:
            if not create:
                return {'tid': None, 'created': False, 'switched': False, 'binding': b, 'retired': []}
            th = {'id': uuid.uuid4().hex[:10], 'title': b['subject_label'][:48] or 'New chat',
                  'created': now, 'updated': now, 'turns': 0, 'binding': dict(b), 'auto': True}
            idx['threads'].append(th)
            created = True
        else:
            # the label can improve (a file renamed, a title resolved)
            if b.get('subject_label') and b['subject_label'] != th['binding'].get('subject_label'):
                th['binding']['subject_label'] = b['subject_label']
                if th.get('auto') and th.get('title') in (None, '', 'New chat', th['binding'].get('subject_label')):
                    th['title'] = b['subject_label'][:48]
    th['opened'] = now
    idx['active'] = th['id']
    retired = apply_cap(idx, cap, keep_id=th['id'])
    return {'tid': th['id'], 'created': created, 'switched': prev != th['id'],
            'binding': (dict(th['binding']) if isinstance(th.get('binding'), dict) else None),
            'retired': retired}


def group_label(thread):
    """The rail group a thread sits in: its project's label, or General."""
    b = thread.get('binding') if isinstance(thread, dict) else None
    if isinstance(b, dict) and b.get('subject_key'):
        return _clean(b.get('subject_label')) or 'Project'
    return 'General'
