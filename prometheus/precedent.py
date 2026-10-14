"""Precedent lookup (Jenna 2026-10-13: "if she says like Babylon 5 will
it find what it did for that then do it?").

When an ask points at a prior deliverable ("just like the one you did
for Babylon 5", "the same analysis as Obsession", "same as the Gunna
read"), Prometheus finds what it actually produced for that name across
everything it holds: the journey shelf, the delivery records (every
product that finished through the delivered contract, including hand-
built packages written back), and the corpus anchors (profiles,
Attribution IQ campaigns, Brand Partnership reads). The prior
deliverable then lends its shape to the new ask: a journey its start,
end step and kind; a Brand Partnership or Attribution read its
structural inputs; a package its request list. The confirm reply says
what was found in one plain sentence, so the reader knows the new pull
is built the same way.
"""
from __future__ import annotations

import json
import re
import traceback

_STOP = {'the', 'a', 'an', 'on', 'of', 'for', 'and', 'in', 'at', 'to', 'with', 'from', 'by', 'vs'}
_PLATFORM_TAIL = re.compile(r"\s+(?:on\s+)?(?:amazon\s+prime\s+video|prime\s+video|amazon\s+prime|amazon|netflix|peacock|hulu|max|hbo\s+max|disney\+?|"
                            r"paramount\+?|apple\s+tv\+?|youtube|tiktok|tubi|pluto\s+tv|roku|starz|spotify)$", re.I)


def fold(s):
    t = re.sub(r"['\u2019]s\b", '', str(s or '').lower())
    t = re.sub(r"[^a-z0-9]+", ' ', t).strip()
    t = _PLATFORM_TAIL.sub('', t).strip()
    return t


def _tokens(s):
    return [w for w in fold(s).split() if w not in _STOP and len(w) > 1]


def _match(name, candidate):
    """The candidate names the same thing: folded equality, or every
    distinctive token of the shorter inside the longer."""
    a, b = fold(name), fold(candidate)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(_tokens(name)), set(_tokens(candidate))
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big and sum(len(w) for w in small) >= 5


def reference(text):
    """The prior deliverable an ask points at, or ''."""
    try:
        from . import intake_reader as _ir
        return _ir.find_template_reference(text)
    except Exception:
        return ''


def _journey_runs(s3, bucket='dashboard-inputs'):
    try:
        idx = json.loads(s3.get_object(Bucket=bucket, Key='journey-iq/_index.json')['Body'].read())
        return [r for r in (idx.get('runs') or []) if isinstance(r, dict)]
    except Exception:
        return []


def resolve(name, uname='', *, host=None, s3=None, bucket='dashboard-inputs'):
    """What Prometheus produced for `name`: {'kind', 'name', 'key', 'when',
    'where', 'created_by', 'shape' | 'inputs'} or None. The caller's own
    deliveries first, then the shared shelves."""
    if not str(name or '').strip():
        return None
    s3 = s3 or (getattr(host, 's3_client', None) if host is not None else None)
    found = []
    # 1. delivery records (every product that finished through the contract, plus packages written back)
    try:
        if s3 is not None and uname:
            from . import delivered as _dl
            for rec in _dl.list_records(s3, bucket, uname, limit=60):
                if _match(name, rec.get('subject')):
                    found.append({'kind': rec.get('kind'), 'name': rec.get('subject'), 'key': rec.get('key'),
                                  'when': rec.get('recorded_at'), 'where': rec.get('where'), 'created_by': uname,
                                  'inputs': rec.get('inputs') or {}, 'files': rec.get('files') or [], 'source': 'delivery_record'})
    except Exception:
        traceback.print_exc()
    # 2. the journey shelf (any user: the shelf is shared)
    try:
        if s3 is not None:
            for r in _journey_runs(s3, bucket):
                if _match(name, r.get('project_name') or r.get('target')):
                    found.append({'kind': 'journey', 'name': r.get('project_name') or r.get('target'), 'key': r.get('key'),
                                  'when': r.get('created_at'), 'where': 'the Digital Journey IQ tab', 'created_by': r.get('created_by'),
                                  'source': 'journey_index', 'shelf': r.get('shelf')})
    except Exception:
        traceback.print_exc()
    # 3. corpus anchors: profiles, Attribution IQ campaigns, Brand Partnership reads, prior journeys
    try:
        from migration import corpus_catalog as _cc
        anchors = _cc.anchors_for(name, with_ledger=False) or {}
        for k, label, where in (('attribution', 'attribution', 'the Attribution IQ tab'), ('bpiq', 'bpiq', 'the Brand Partnership IQ tab'),
                                ('profile', 'profile', 'Profile IQ, Select Profile'), ('prior_journey', 'journey', 'the Digital Journey IQ tab')):
            a = anchors.get(k)
            if isinstance(a, dict) and a:
                found.append({'kind': label, 'name': a.get('name') or a.get('subject') or a.get('title') or name,
                              'key': a.get('source_key') or a.get('key') or a.get('slug') or '', 'when': a.get('as_of') or a.get('created_at'),
                              'where': where, 'created_by': a.get('created_by'), 'source': f'corpus_{k}', 'anchor': a})
    except Exception:
        traceback.print_exc()
    if not found:
        return None
    # the caller's own, most recent, journeys before anchors
    rank = {'delivery_record': 0, 'journey_index': 1}
    found.sort(key=lambda f: (0 if str(f.get('created_by') or '') == str(uname or '') else 1, rank.get(f.get('source'), 2),
                              -(len(str(f.get('when') or '')))))
    return found[0]


def describe(found):
    """One plain sentence about what was found."""
    if not found:
        return ''
    kind = {'journey': 'a Digital Journey', 'bpiq': 'a Brand Partnership IQ read', 'attribution': 'an Attribution IQ campaign',
            'profile': 'a profile', 'flywheel': 'a Flywheel read', 'package': 'a set of data files', 'subiq': 'a Subscriber IQ tracker',
            'deck': 'a deck'}.get(str(found.get('kind') or ''), 'a read')
    when = str(found.get('when') or '')[:10]
    when_txt = f" on {when}" if when else ''
    return f"I built {kind} for {found.get('name')}{when_txt}; the new one is built the same way."


def apply(flow, parsed, found, name):
    """Lend the prior deliverable's shape to the new ask's parsed inputs.
    Journeys reuse start / end / kind; Brand Partnership, Attribution and
    Flywheel reuse structural inputs that the new ask did not name.
    Returns parsed (changed in place)."""
    if not isinstance(parsed, dict) or not found:
        return parsed
    try:
        if flow == 'digital_journey':
            from . import intake_reader as _ir
            shape = None
            try:
                from migration import corpus_catalog as _cc
                prior = _cc.prior_journey(_cc.anchors_for(found.get('name') or name, with_ledger=False))
                shape = _ir.journey_shape(prior, found.get('name') or name) if prior else None
            except Exception:
                shape = None
            return _ir.apply_journey_template(parsed, shape, found.get('name') or name)
        inputs = found.get('inputs') or (found.get('anchor') or {}).get('inputs') or {}
        if isinstance(inputs, dict):
            skip = {'subject', 'title', 'brand', 'name', 'talent', 'campaign', 'campaign_name', 'project_name', 'url', 'clip_url'}
            for k, v in inputs.items():
                if k in skip or k.startswith('_'):
                    continue
                if not parsed.get(k) and v not in (None, '', [], {}):
                    parsed[k] = v
        parsed.setdefault('_precedent', {'kind': found.get('kind'), 'name': found.get('name'), 'key': found.get('key')})
    except Exception:
        traceback.print_exc()
    return parsed
