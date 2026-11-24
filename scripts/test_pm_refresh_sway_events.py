#!/usr/bin/env python3
"""The near-twin holds respect a refresh's researched sway (2026-11-24).

A time-shifted refresh that moves off its parent on dated, researched
events in its new window records those events internally
(migration/refresh_sway). The knowledge layer's near-twin holds read
them as their research override, so a figure the events moved is never
clamped back to the earlier one:

  [1] prometheus.consistency knows the refresh event kinds
  [2] subject_facts carries the subject's recorded refresh events (and
      never puts them in the reader block)
  [3] cross_hold lifts a figure when a recorded event sits in the
      changed part of the window, and holds it without one
  [4] the read hold (hold_pending) merges the recorded events
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')
os.environ.setdefault('PM_STORE_URL', '')
os.environ.pop('REFRESH_SWAY', None)

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import consistency as X  # noqa: E402
from prometheus import knowledge as K  # noqa: E402
from migration import corpus_catalog as CC  # noqa: E402
from migration import refresh_sway as RS  # noqa: E402
from migration import saved_subject_size as SSS  # noqa: E402

FAIL = 0
SUBJ = 'Stephen Colbert'
PW = {'start': '2025-01-01', 'end': '2025-12-31'}
NW = {'start': '2025-11-24', 'end': '2026-11-24'}


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def seed_record():
    store = RS.MemoryStore()
    RS.set_store(store)
    doc = {'events': [{'date': '2026-01-10', 'kind': 'award', 'name': 'a January award', 'direction': 'up',
                       'source_type': 'trade_press'},
                      {'date': '2026-05-21', 'kind': 'finale', 'name': 'series finale', 'direction': 'up',
                       'source_type': 'measurement'}],
           'anchors': [{'metric': 'channel views', 'older_value': 10, 'newer_value': 18, 'source_type': 'platform'}],
           'ratio_low': 1.5, 'ratio_high': 2.1}
    v = RS.decide(SUBJ, 594_748, PW, NW, doc=doc)
    RS.write_record(SUBJ, v, run_id='t', parent_key='Stephen_Colbert_06_15_2026_13_56.csv', delivered=v['sample'])
    return v


def test_kinds():
    print('[1] the refresh event kinds')
    check('finale, final season, cancellation, tour, new show, viral, hiatus and ended are event kinds',
          all(k in X.EVENT_KINDS for k in ('finale', 'final_season', 'cancellation', 'tour', 'new_show', 'viral',
                                           'hiatus', 'ended')))
    ev = X.event_in_changed_window([{'start': '2026-01-01', 'end': '2026-11-24', 'change': 'added'}],
                                   [{'date': '2026-05-21', 'kind': 'finale', 'name': 'series finale'}])
    check('a finale inside the changed part is found', ev and ev['kind'] == 'finale')
    line = X.lifted_line(ev, 'up')
    check('its line reads plainly', line == 'The window now includes the May 21, 2026 series finale, which lifted the audience reached.'
          or ('May 21' in line and 'finale' in line and '\u2014' not in line))


def test_facts():
    print('[2] subject_facts carries the recorded events')
    seed_record()
    K._FACTS_CACHE.clear()
    with mock.patch.object(CC, 'anchors_for', lambda *a, **k: {'facts': [], 'marks': []}), \
            mock.patch.object(SSS, 'lookup', lambda s: None):
        sf = K.subject_facts(SUBJ, window=NW, attribution=False)
    dates = [e['date'] for e in sf.get('events') or []]
    check(f'the recorded events are on the facts ({dates})', dates == ['2026-01-10', '2026-05-21'])
    check('they never enter the reader block', 'finale' not in (sf.get('block') or '') and 'award' not in (sf.get('block') or ''))
    check('_refresh_events reads them too', [e['date'] for e in K._refresh_events(SUBJ)] == dates)


def test_cross_hold():
    print('[3] cross_hold lifts on a recorded event, holds without one')
    seed_record()
    anchor = K._fig('profile', 'audience_people', 19_620_737, label='US audience', window=PW, as_of='2026-06-15')
    w = {'start': '2025-01-15', 'end': '2026-01-14'}
    facts = {'figures': [anchor], 'events': RS.recorded_events(SUBJ)}
    v, rec = K.cross_hold(48_588_992, kind='audience_people', facts=facts, window=w, salt='sway', label='audience', product='profile')
    check(f"a figure the January award moved is lifted, not held (why={rec.get('why')})",
          rec.get('why') == 'lifted' and v == 48_588_992 and (rec.get('event') or {}).get('date') == '2026-01-10')
    v2, rec2 = K.cross_hold(48_588_992, kind='audience_people', facts=dict(facts, events=[]), window=w, salt='sway',
                            label='audience', product='profile')
    check(f"without a recorded event the same figure is held to the band (why={rec2.get('why')})",
          rec2.get('why') == 'held' and v2 < 48_588_992)
    late = {'figures': [anchor], 'events': [{'date': '2026-05-21', 'kind': 'finale', 'name': 'series finale'}]}
    _v3, rec3 = K.cross_hold(48_588_992, kind='audience_people', facts=late, window=w, salt='sway', label='audience',
                             product='profile')
    check('an event outside the changed part of the window does not lift it', rec3.get('why') == 'held')


def test_read_hold():
    print('[4] the read hold merges the recorded events')
    src = (WEB / 'prometheus' / 'knowledge.py').read_text()
    i = src.find('def hold_pending(')
    blk = src[i:i + 5000]
    check('hold_pending reads the recorded refresh events beside its own', "list(pend.get('events') or []) + _refresh_events(subject)" in blk)
    RS.set_store(None)


def main():
    test_kinds()
    test_facts()
    test_cross_hold()
    test_read_hold()
    print()
    if FAIL:
        print(f'{FAIL} FAILED')
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
