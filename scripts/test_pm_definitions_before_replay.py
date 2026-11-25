#!/usr/bin/env python3
"""A definition or how-to-read question gets the definition lane, never a
stored read replayed by meaning (2026-11-25).

After Armine's Nov 23 asks, "can you explain how to read the insight ..."
asked right after a read replayed that read's figures record through
consult's replay by meaning, and a bare "penetration vs share" went to the
model instead of the glossary. A question that clearly asks for the
figures still replays the read.

Holds:
  1. what counts as a definition-style ask, and what asks for the figures
  2. replay_eligible: never for a definition-style ask, as before for the rest
  3. the glossary takes a bare pair of its terms ('penetration vs share'),
     view-aware, and never a pair of titles or a term with a subject
  4. consult on a ledger holding a figures record: no replay for a
     definition or how-to-read ask; the figures ask and the record's own
     question still replay it
  5. through the door (the replay harness, the record banked, the read in
     the thread): the glossary or a fresh explanation for the definition
     asks, the replay for the figures asks (needs flask; skipped without)

Hermetic: REGRESSION_TEST_MODE=1, the ledger's in-memory store, canned model.
"""
import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'
os.environ['PM_REPLAY_ALLOW_INPROCESS'] = '1'

import insights_ledger as il  # noqa: E402
from prometheus import kpi_definitions as kd  # noqa: E402

PASS, FAIL = 0, []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}  {detail}')


SUBJ = 'Example Skater'
RECORD = ('Comedy series the Example Skater searchers watch. The group is the 1,604,213 US people who searched for '
          'Example Skater in that year. 781,409 of them watched at least one comedy series, and Night Shift Diner '
          'leads at 311,872 viewers, 28.4% penetration and 17.9% share.')
RECORD_Q = 'what comedy series do the people who searched Example Skater watch'
DEFINITION_ASKS = ['what metric is penetration vs share', 'penetration vs share', 'what is share', 'how do I read this',
                   'can you explain how to read the insight for the comedy series viewers',
                   'why are the views higher than plays']
FIGURES_ASKS = ['what were the numbers for the comedy series', 'how many people watched Night Shift Diner',
                'what is the penetration for Night Shift Diner', RECORD_Q]


def bank():
    il.use_test_ledger({})
    il.bank_record(subject=SUBJ, source='comedy_series_test', question=RECORD_Q, reply=RECORD,
                   metrics=[{'name': 'cohort_base', 'label': 'People who searched Example Skater', 'value': 1604213, 'unit': 'people'},
                            {'name': 'series_viewers', 'label': 'Comedy series viewers', 'value': 781409, 'unit': 'people'}],
                   window_start='2025-11-17', window_end='2026-11-17', metric_family='viewership', retire_others=False,
                   asker='staff', questions=[('which comedy series do the Example Skater searchers watch', RECORD)])


def main():
    print('1. what is a definition-style ask')
    for q in DEFINITION_ASKS:
        check(f'definition: {q!r}', il.is_definition_style(q))
    for q in FIGURES_ASKS:
        check(f'figures: {q!r}', not il.is_definition_style(q))
    check('"what were the numbers for ..." asks for the figures', il.asks_for_figures('what were the numbers for the comedy series'))

    print('2. replay_eligible')
    check('never for a definition-style ask', not any(il.replay_eligible(q) for q in DEFINITION_ASKS),
          str([q for q in DEFINITION_ASKS if il.replay_eligible(q)]))
    check('as before for a figures ask', all(il.replay_eligible(q) for q in FIGURES_ASKS),
          str([q for q in FIGURES_ASKS if not il.replay_eligible(q)]))

    print('3. the glossary takes a bare pair of its terms')
    sa = kd.standing_answer('penetration vs share', {'view_context': {'view_id': 'profileIQ'}}) or {}
    check("'penetration vs share' on a profile is the two entries", sa.get('kpi_definition') == ['penetration', 'category_share'],
          str(sa.get('kpi_definition')))
    check("'index or penetration' too", kd.is_definition_ask('index or penetration', 'profileIQ'))
    check('never a term with a subject, a pair of titles, or a short term off its view',
          not kd.is_definition_ask('penetration or share for Nike', 'profileIQ')
          and not kd.is_definition_ask('Night Shift Diner vs Harbor Lights', 'profileIQ')
          and not kd.is_definition_ask('penetration vs share', 'subiq'))

    print('4. consult on a ledger holding the figures record')
    bank()
    for q in DEFINITION_ASKS:
        led = il.consult(subject=SUBJ, question=q)
        check(f'no replay: {q!r}', not led.get('exact'), str((led.get('exact') or {}).get('route')))
    for q in ('what were the numbers for the comedy series', RECORD_Q):
        led = il.consult(subject=SUBJ, question=q)
        check(f'still replays: {q!r}', (led.get('exact') or {}).get('reply') == RECORD, str(led.get('match')))
    led = il.consult(subject=SUBJ, question='what metric is penetration vs share')
    check('the figures still bind the next read (the block)', '1,604,213' in (led.get('block') or ''))

    print('5. through the door')
    for d in (os.environ.get('PM_PARENT_SCRIPTS') or '', str(ROOT.parent / 'scripts')):
        if d and (Path(d) / '_pm_behavior.py').is_file() and d not in sys.path:
            sys.path.insert(0, d)
    try:
        import flask  # noqa: F401
        import _pm_behavior  # noqa: F401
    except ImportError:
        print('  skip the door needs flask and the parent repo beside this checkout (the build server runs this part)')
    else:
        from prometheus import replay
        h = replay.harness()
        il._SYNC_FOR_TESTS = True
        il._s3 = h.s3
        bank()
        il._s3 = h.s3
        il.bank_record(subject=SUBJ, source='comedy_series_test', question=RECORD_Q, reply=RECORD,
                       metrics=[{'name': 'cohort_base', 'label': 'People who searched Example Skater', 'value': 1604213, 'unit': 'people'}],
                       window_start='2025-11-17', window_end='2026-11-17', metric_family='viewership', retire_others=False,
                       asker='staff')
        pc = replay._page_context(h, 'profileIQ', SUBJ, None)
        hist = [{'role': 'user', 'text': 'what comedy series do the name searchers watch'},
                {'role': 'agent', 'text': RECORD, 'meta': {'kind': 'read', 'subject': SUBJ}}]

        def ask(q):
            n0 = len(replay._STATE['captured'])
            replay._canned_dual(h, SUBJ, None)
            _st, payload = h.post(replay.DOOR_PATH, {'text': q, 'history': copy.deepcopy(hist), 'page_context': pc,
                                                     'client': 'dashboard', 'persist': False, 'surface': 'analyze'},
                                  session={'username': 'canary:deflane'})
            rec = (replay._STATE['captured'][n0:] or [{}])[-1]
            return rec.get('route'), str(payload.get('text') or '')

        for q, want in (('what metric is penetration vs share', 'kpi_definition'), ('penetration vs share', 'kpi_definition'),
                        ('what is share', 'kpi_definition')):
            route, text = ask(q)
            check(f'{q!r}: the glossary', route == want and '781,409' not in text, route)
        for q in ('how do I read this', 'can you explain how to read the insight for the comedy series viewers'):
            route, text = ask(q)
            check(f'{q!r}: never the stored read replayed', route != 'ledger_replay' and '781,409' not in text, route)
        route, text = ask('what were the numbers for the comedy series')
        check("'what were the numbers for ...' still replays the read", route == 'ledger_replay' and '781,409' in text, route)

    src = Path(__file__).read_text(encoding='utf-8')
    check('no long dash in this file', '\u2014' not in src and '\u2013' not in src)
    print(f'\n{PASS} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
