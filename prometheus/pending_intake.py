"""The brief a guided intake proposed, kept server-side (2026-10-13).

The widget keeps the proposed brief in page state and sends it back
with "Run the journey". A reload, a second tab, a reply typed hours
later, or a turn Prometheus posted into the thread itself all lose
that state, and the run order then read as "Nothing is waiting on an
approval right now" (Debi's "go ahead", 2026-10-12). The proposal is
now stashed per reader for a day; a run order with no payload picks
it up.

Layout: s3://dashboard-inputs/system/pm_pending_intake/<user_key>.json
    {"flows": {"digital_journey": {"payload": {...}, "ts": ..., "run_label": "Run the journey"}, ...}}
"""
from __future__ import annotations

import json
import re
import time

PREFIX = 'system/pm_pending_intake/'
TTL_S = 24 * 3600

_RUN_RX = re.compile(
    r"^\s*(?:(?:yes|yep|yeah|ok|okay|sure|great|perfect)[,.!]?\s+)?(?:please\s+)?"      # "yes, run it" (Carolyn, 2026-10-14: the card came back twice)
    r"(?:yes|yep|yeah|ok|okay|sure|go|go ahead|do it|run it|run that|run this|ship it|proceed|approve[d]?|confirm(?:ed)?|"
    r"run the (?:journey|valuation|flywheel|study|read|campaign|tracking|twin)|build the twin|start tracking|run (?:it|them)|add (?:the assets|them|the urls|the links))\s*(?:please)?\s*[.!]?\s*$",
    re.I)


def _key(user):
    import prometheus_memory as pmm
    return PREFIX + pmm._user_key(user) + '.json'


def _load(s3, bucket, user):
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=_key(user))['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save(s3, bucket, user, doc):
    try:
        s3.put_object(Bucket=bucket, Key=_key(user), Body=json.dumps(doc, default=str).encode('utf-8'),
                      ContentType='application/json')
        return True
    except Exception:
        return False


def stash(s3, bucket, user, flow, payload, run_label=''):
    """Remember the brief just proposed to this reader for `flow`."""
    if not user or not flow or not isinstance(payload, dict):
        return False
    doc = _load(s3, bucket, user)
    flows = doc.get('flows') if isinstance(doc.get('flows'), dict) else {}
    flows[str(flow)] = {'payload': payload, 'ts': time.time(), 'run_label': str(run_label or '')}
    doc['flows'] = flows
    return _save(s3, bucket, user, doc)


def is_run_order(text):
    return bool(_RUN_RX.match(str(text or '')))


def pick(s3, bucket, user, flow, text):
    """The stashed brief for `flow` when `text` is a run order with no
    payload of its own, else None. Picking consumes it."""
    if not user or not is_run_order(text):
        return None
    doc = _load(s3, bucket, user)
    flows = doc.get('flows') if isinstance(doc.get('flows'), dict) else {}
    hit = flows.get(str(flow))
    if not isinstance(hit, dict):
        return None
    if time.time() - float(hit.get('ts') or 0) > TTL_S:
        flows.pop(str(flow), None)
        doc['flows'] = flows
        _save(s3, bucket, user, doc)
        return None
    # a run order naming another flow's chip is not this flow's yes
    label = str(hit.get('run_label') or '').strip().lower()
    t = str(text or '').strip().lower().rstrip('.!')
    if t.startswith('run the ') and label and t != label:
        return None
    flows.pop(str(flow), None)
    doc['flows'] = flows
    _save(s3, bucket, user, doc)
    payload = hit.get('payload')
    return payload if isinstance(payload, dict) else None


def peek(s3, bucket, user, flow):
    """The stashed brief for `flow` without consuming it. None when
    nothing is waiting or the stash is older than a day."""
    if not user or not flow:
        return None
    doc = _load(s3, bucket, user)
    flows = doc.get('flows') if isinstance(doc.get('flows'), dict) else {}
    hit = flows.get(str(flow))
    if not isinstance(hit, dict):
        return None
    if time.time() - float(hit.get('ts') or 0) > TTL_S:
        return None
    payload = hit.get('payload')
    return payload if isinstance(payload, dict) else None


def clear(s3, bucket, user, flow=None):
    doc = _load(s3, bucket, user)
    flows = doc.get('flows') if isinstance(doc.get('flows'), dict) else {}
    if flow is None:
        flows = {}
    else:
        flows.pop(str(flow), None)
    doc['flows'] = flows
    return _save(s3, bucket, user, doc)


def waiting(s3, bucket, user, text):
    """True when a brief is stashed for this reader and `text` is a run
    order: the bare-reply lane steps aside so the flow's own confirm
    step picks the brief up. Never consumes."""
    if not user or not is_run_order(text):
        return False
    doc = _load(s3, bucket, user)
    flows = doc.get('flows') if isinstance(doc.get('flows'), dict) else {}
    now = time.time()
    return any(isinstance(h, dict) and now - float(h.get('ts') or 0) <= TTL_S for h in flows.values())
