#!/usr/bin/env python3
"""The Reader's call starts while the door walks its lanes (Jenna's
standing ask, 2026-11-24; prometheus.reader_prestart).

1. the module: a call on its own thread; the lane takes it only when the
   words, the page context and the thread match; the Reader's error is
   the lane's error; an empty answer is the answer (one call per ask);
   the progress lines are said on the request thread; the seat's
   attribution rides the call; the switch and the test-mode guard;
2. the door, with the prestart forced: one Reader call per ask, the lane
   takes the early reading, and the route, outcome and reading are the
   ones an unforced run gives on the same asks; an ask whose words a lane
   rewrites falls back to the lane's own call;
3. the wiring: the prestart before the first lane, the take at the reader
   lane, the door's marks, no live line under a bare 'reader' key.
Offline (the harness, a canned Reader).
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, WEB)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ['PM_REPLAY_ALLOW_INPROCESS'] = '1'

FAIL = []


def check(label, ok, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + label + (f'  {detail}' if (detail and not ok) else ''))
    if not ok:
        FAIL.append(label)


from prometheus import reader_prestart as RP  # noqa: E402

print('1. the module')
calls = []


def fake_read(text, ctx, history, user, uname, *, claude=None, progress=None):
    calls.append((text, claude))
    if progress:
        progress('Reading your question'); progress('You want a profile built on X')
    time.sleep(0.05)
    return {'intent': 'build_profile', 'subjects': [{'as_written': text}], 'ms': 50}


os.environ['PM_READER_PRESTART_FORCE'] = '1'
said = []
t0 = time.monotonic()
p = RP.start('run a profile on X', {'primary': {'name': 'P'}}, [{'role': 'user', 'text': 'hi'}], {'username': 'u'}, 'u',
             read=fake_read, say=said.append, extras={'user': 'u', 'user_email': 'u@x.com'}, claude=lambda *a, **k: {})
check('the call starts on its own thread and the caller does not wait', p is not None and (time.monotonic() - t0) < 0.04 and said == ['Reading your question'])
check('the lane takes it when the words, the context and the thread are the ones it read',
      p.matches('run a profile on X', {'primary': {'name': 'P'}}, [{'role': 'user', 'text': 'hi'}]) and not p.matches('run a profile on Y', {'primary': {'name': 'P'}}, [{'role': 'user', 'text': 'hi'}])
      and not p.matches('run a profile on X', {'primary': {'name': 'Q'}}, [{'role': 'user', 'text': 'hi'}]) and not p.matches('run a profile on X', {'primary': {'name': 'P'}}, []))
r = p.take(say=said.append)
check('the reading comes back whole and the progress lines after the first are said at the take', r and r['intent'] == 'build_profile' and said == ['Reading your question', 'You want a profile built on X'] and p.used)
check('one call, on the words it was given', len(calls) == 1 and calls[0][0] == 'run a profile on X')


def boom(text, ctx, history, user, uname, *, claude=None, progress=None):
    raise RuntimeError('reader down')
pe = RP.start('what do they watch', None, [], {}, 'u', read=boom)
try:
    pe.take(); raised = ''
except RuntimeError as e:
    raised = str(e)
check("the Reader's error is the lane's error", raised == 'reader down')
pn = RP.start('what do they watch', None, [], {}, 'u', read=lambda *a, **k: None)
check('an empty answer is the answer (never a second call)', pn.take() is None and pn.used)
check('one word is not read', RP.start('yes', None, [], {}, 'u', read=fake_read) is None)
os.environ['PM_READER_PRESTART'] = '0'
check('the switch stands the prestart down', RP.start('run a profile on X', None, [], {}, 'u', read=fake_read) is None and not RP.enabled())
os.environ.pop('PM_READER_PRESTART', None)
os.environ.pop('PM_READER_PRESTART_FORCE', None)
check('under the test mode there is no early call unless forced', RP.start('run a profile on X', None, [], {}, 'u', read=fake_read) is None)
# the seat's attribution rides the call when the host's claude is used
seen = {}


class _Host:
    def has(self, name): return name == 'claude_data'
    def claude_data(self, system, user_prompt, **kw): seen.update(kw); return {'intent': 'analyze'}
import prometheus.host as _hmod
_orig_host = _hmod.host
_hmod.host = _Host()
os.environ['PM_READER_PRESTART_FORCE'] = '1'
try:
    def read_via_claude(text, ctx, history, user, uname, *, claude=None, progress=None):
        return claude('SYS', 'USER', max_tokens=900, temperature=0.0, surface='reader')
    pc = RP.start('what do they watch on Netflix', None, [], {}, 'u', read=read_via_claude, extras={'user': 'casey', 'user_email': 'c@x.com'})
    pc.take()
    check("the seat's attribution rides the call made off the request thread", seen.get('usage_extras') == {'user': 'casey', 'user_email': 'c@x.com'} and seen.get('surface') == 'reader')
finally:
    _hmod.host = _orig_host

print('2. the door, with the prestart forced')
from prometheus import replay  # noqa: E402
h = replay.harness()
DOOR = replay.DOOR_PATH if 'prometheus_api' in h.app.blueprints else replay._bind_service_door(h)
U = replay.REPLAY_USER['username']
from prometheus import reader as RD  # noqa: E402
n_reads = {'n': 0}
_orig_read = RD.read


def counted_read(*a, **k):
    n_reads['n'] += 1
    return _orig_read(*a, **k)
RD.read = counted_read
ASKS = ['Run a profile for stephen colbert', 'who watches Yellowstone and what else do they stream',
        'How many people in the US watched Landman last month?', 'compare Nike and Adidas on sneaker purchases']


def door(q, force):
    if force:
        os.environ['PM_READER_PRESTART_FORCE'] = '1'
    else:
        os.environ.pop('PM_READER_PRESTART_FORCE', None)
    n_reads['n'] = 0
    n0 = len(replay._STATE['captured'])
    st, payload = h.post(DOOR, {'text': q, 'history': [], 'page_context': {}, 'client': 'dashboard', 'persist': False, 'surface': 'analyze'},
                         session={'username': U}, headers={'X-Prometheus-Caller': 'replay'})
    recs = replay._STATE['captured'][n0:]
    rec = recs[-1] if recs else {}
    stages = rec.get('stages') or {}
    return {'status': st, 'kind': payload.get('kind'), 'surface': payload.get('surface'), 'route': rec.get('route'), 'outcome': rec.get('outcome'),
            'reading': (rec.get('extra') or {}).get('reading'), 'reads': n_reads['n'], 'stages': stages}


for q in ASKS:
    a = door(q, False)
    b = door(q, True)
    check(f'{q[:38]!r}: the same route, outcome and reading with the prestart on, one Reader call each',
          (a['route'], a['outcome'], a['kind'], a['surface']) == (b['route'], b['outcome'], b['kind'], b['surface']) and a['reading'] == b['reading']
          and a['reads'] == b['reads'] == 1, f"{(a['route'], a['outcome'], a['reads'])} vs {(b['route'], b['outcome'], b['reads'])}")
    check(f'{q[:38]!r}: the door marks the lanes ahead of the Reader and the wait (the call too when a reading came back)',
          all(k in b['stages'] for k in ('door_pre_reader', 'door_reader_wait')) and (('reader_call' in b['stages']) == bool(b['reading'])), str(b['stages'])[:200])
# with a logged reading canned on the Reader's call (what production's Reader answers), the early call hands the lane
# the same reading and the ask lands the same way
CASES = [
    {'question': 'How many people in the US watched Landman last month?', 'view_id': 'profileIQ', 'subject': 'Landman',
     'reading': {'intent': 'size', 'confidence': 0.9, 'subjects': [{'as_written': 'Landman', 'catalog': 'Landman', 'kind': 'title'}], 'flags': [], 'restate': 'You want to know how many people watched Landman.'}},
    {'question': 'who watches Yellowstone and what else do they stream', 'view_id': 'profileIQ', 'subject': 'Yellowstone',
     'reading': {'intent': 'analyze', 'confidence': 0.88, 'subjects': [{'as_written': 'Yellowstone', 'catalog': 'Yellowstone', 'kind': 'title'}], 'flags': [], 'restate': 'You want a read on who watches Yellowstone.'}},
]
for c in CASES:
    os.environ.pop('PM_READER_PRESTART_FORCE', None)
    n_reads['n'] = 0
    a = replay.probe_many([dict(c)])[0]; ra = n_reads['n']
    os.environ['PM_READER_PRESTART_FORCE'] = '1'
    n_reads['n'] = 0
    b = replay.probe_many([dict(c)])[0]; rb = n_reads['n']
    check(f"{c['question'][:38]!r} with the logged reading: the same route, outcome and reply, one Reader call each way",
          (a.get('route'), a.get('outcome'), a.get('reply')) == (b.get('route'), b.get('outcome'), b.get('reply')) and ra == rb == 1 and bool(a.get('reply')),
          f"{(a.get('route'), a.get('outcome'), ra)} vs {(b.get('route'), b.get('outcome'), rb)}")
os.environ.pop('PM_READER_PRESTART_FORCE', None)
RD.read = _orig_read

print('3. the wiring')
svc = open(os.path.join(WEB, 'prometheus', 'service.py'), encoding='utf-8').read()
check('the prestart runs before the first lane, on the settled words and context, never for an armed step',
      svc.index("_rdp.start(text, ctx, history, user, uname, read=_rd_pre_mod.read") < svc.index('# lane: intake_cancel')
      and "    _rd_pre = None\n    if not _armed(body):" in svc)
check('the reader lane takes the early call only on matching inputs, else its own call as before',
      "if _rd_pre is not None and _rd_pre.matches(text, ctx, history):" in svc and "reading = _rd_pre.take(say=_pg.say)" in svc
      and "reading = _rd.read(text, ctx, history, user, uname, progress=_pg.say)" in svc)
check("the seat's attribution is captured on the request thread and handed to the call", "_pre_extras = _ag_pre._pm_attrib_extras()" in svc and "extras=_pre_extras" in svc)
check('the door marks: the lanes ahead of the Reader, the wait, the call; none under a key with a live line',
      "_stage_door('door_pre_reader'" in svc and "_stage_door('door_reader_wait'" in svc and "_stage_door('reader_call'" in svc and "_stage_door('reader'," not in svc)
prog = open(os.path.join(WEB, 'prometheus', 'progress.py'), encoding='utf-8').read()
check('the new keys carry no live line of their own', all(f"'{k}'" not in prog for k in ('door_pre_reader', 'door_reader_wait', 'reader_call')))
for rel in ('prometheus/reader_prestart.py', 'scripts/test_pm_reader_prestart.py'):
    check(f'no em dash in {rel}', '\u2014' not in open(os.path.join(WEB, rel), encoding='utf-8').read())
print()
if FAIL:
    print(f'FAILED {FAIL}')
    sys.exit(1)
print('ALL PASSED')
