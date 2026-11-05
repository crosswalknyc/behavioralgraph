#!/usr/bin/env python3
"""Talent attach reads the room once the room has watched (Jenna,
2026-11-04, on the NCIS: New York Pilot test: "how does this resolve if
she scored better than him everywhere else?").

Before this change the lift was the twin's calibrated base times how
much the audience already followed the name times a billing multiplier
(1.0 for a lead, star or host, 0.55 for anyone entered as supporting),
and nothing read the room: Jennifer Beals, the room's draw at 57.2%
excellent and 60.1% on follow, lifted 5.6% as 'supporting' while Scott
Caan at 46.8% and 13.8% lifted 10.2% as 'lead'. Now the billing role is
a fact in the sentence and never a term in the number, and the lift is
the base times the name's pull (the audience row, the Talent Fit IQ
overlap) times the room's verdict (follow, excellent, named as the
reason to come back) against an even room.

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_talent_attach.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_compare as C  # noqa: E402
from migration import digital_twin_engine as E  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402

BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'agent', 'panel', 'sample',
          'research', 'confidence', 'multiplier', 'weight', '\u2014', '\u2013', ' door')


def _twin(size=83_791_763, base=10.2):
    twin = _beals('Paramount+', 'pp')
    twin['meta']['size_us'] = size
    twin['response'] = {'cast_attach_lift': {'reach_lift_pct': base, 'return_lift_pct': 6.5}}
    twin['identity']['talent'] = [{'label': 'THE Weeknd', 'pct': 73.1}, {'label': 'Ariana Grande', 'pct': 69.7}]
    return twin


def _session(twin, scored=True, in_room=16):
    sess = PI.open_session([twin], title='NCIS: New York Pilot', in_room=in_room)
    if scored:
        PI.prepare_watch(sess, {'pp': twin}, duration_s=600, title='NCIS: New York Pilot')
    return sess


def _card(sess, rows, follow, draw=None, draw_seats=0, said=None):
    """The characters card as the session stores it after the reconcile:
    the rows (name, role, excellent, total positive, draw_seats), the
    per-person follow shares, who the room named as the draw."""
    chars = []
    for name, role, exc, pos, n_draw in rows:
        row = {'name': name, 'role': role, 'excellent_pct': exc, 'total_positive_pct': pos, 'share_pct': 10.0, 'screen_clock': '1:30', 'likability': 60.0, 'time_vs_like': 'in balance', 'norm': None}
        if n_draw:
            row['draw_seats'] = n_draw
        if said and name in said:
            row['room_said'] = {'said': said[name], 'quote': None, 'clocks': []}
        chars.append(row)
    sess.setdefault('diagnostics', {})['characters'] = {
        'characters': chars, 'follow': [{'name': n, 'pct': p, 'people': int(p * 1000)} for n, p in follow],
        'relationship': None, 'read': 'The characters, rated.',
        'from_room': {'questions': ['q1'], 'draw': draw, 'draw_seats': draw_seats, 'draw_lift': 0.0} if draw else {'questions': ['q1'], 'draw': None, 'draw_seats': 0, 'draw_lift': 0.0},
    }
    return sess


NCIS_ROWS = [('LL Cool J', 'lead', 57.1, 74.7, 0), ('Scott Caan', 'second lead', 46.8, 67.1, 0), ('Jennifer Beals', 'supporting', 57.2, 75.7, 10)]
NCIS_FOLLOW = [('LL Cool J', 66.1), ('Jennifer Beals', 60.1), ('Scott Caan', 13.8)]
NCIS_SAID = {'Jennifer Beals': 'All sixteen seats liked her and every one wanted more of her in it. Ten of the sixteen said more of her running the team brings them back.',
             'Scott Caan': 'Scott got the laughs, grinning to himself in the car, and fifteen of sixteen seats rated him excellent.',
             'LL Cool J': 'The wall slam and the BQE ride drew the biggest love, and all sixteen seats rated him excellent.'}


def _ncis():
    twin = _twin()
    sess = _card(_session(twin), NCIS_ROWS, NCIS_FOLLOW, draw='Jennifer Beals', draw_seats=10, said=NCIS_SAID)
    return twin, sess


def _tenths(v) -> int:
    return int(round(float(v) * 10)) % 10


class RoleIsAFact(unittest.TestCase):
    def test_role_never_changes_the_lift(self):
        """The same name entered as lead, supporting and host carries one lift; only the words in brackets change."""
        twin, sess = _ncis()
        got = {}
        for role in ('lead', 'supporting', 'host', 'second lead'):
            s = copy.deepcopy(sess)
            rec = C.talent_attach(s, twin, 'Jennifer Beals', role)
            got[role] = rec
            self.assertIn(f"Jennifer Beals (billed {role}) lifts first-month reach", rec['read'])
            self.assertEqual(rec['role'], role)
        lifts = {r: got[r]['outputs']['reach_lift_pct'] for r in got}
        self.assertEqual(len(set(lifts.values())), 1, lifts)
        self.assertEqual(len({got[r]['outputs']['expected'] for r in got}), 1)
        self.assertEqual(len({got[r]['outputs']['return_lift_pct'] for r in got}), 1)
        self.assertEqual(len({json.dumps(got[r]['by_quadrant'], sort_keys=True) for r in got}), 1)
        # the engine alone, with no room at all: the same answer for every billing
        plain = {role: E.run_talent_attach(twin, {'talent': 'Scott Caan', 'role': role}, 'seed|x')['outputs']['reach_lift_pct'] for role in ('lead', 'supporting', 'star', 'host')}
        self.assertEqual(len(set(plain.values())), 1, plain)

    def test_no_role_or_time_term_is_left_in_the_code(self):
        with open(os.path.join(ROOT, 'migration', 'digital_twin_engine.py'), encoding='utf-8') as fh:
            src = fh.read()
        body = src[src.index('def run_talent_attach'):src.index('def run_merch_drop')]
        self.assertNotIn("'lead', 'star', 'host'", body)
        self.assertNotIn('0.55', body)
        for tok in (r"(?<![a-z_])share_pct", r"screen_s\b", 'screen_clock', r"\bturns\b"):
            self.assertIsNone(re.search(tok, body), tok)
        with open(os.path.join(ROOT, 'migration', 'digital_twin_compare.py'), encoding='utf-8') as fh:
            cmp_src = fh.read()
        attach = cmp_src[cmp_src.index('def room_for'):cmp_src.index('def activation_brief')]
        for tok in ('share_pct', 'screen_s', 'screen_clock', "role.lower() in", "'supporting' else"):
            self.assertNotIn(tok, attach, tok)


class TheRoomIsInTheLift(unittest.TestCase):
    def test_the_room_the_name_drew_lifts_more(self):
        """Two names with the same name pull: the one the room would follow at 60% and rates 57% excellent lifts more than the one at 14% and 47%."""
        twin = _twin()
        sess = _card(_session(twin), [('Ava Pine', 'lead', 47.0, 66.0, 0), ('Mara Quill', 'supporting', 57.0, 75.0, 0)], [('Mara Quill', 60.0), ('Ava Pine', 14.0)])
        a = C.talent_attach(sess, twin, 'Ava Pine', 'lead')
        b = C.talent_attach(sess, twin, 'Mara Quill', 'supporting')
        self.assertEqual(a['outputs']['already_follow_index'], b['outputs']['already_follow_index'])
        self.assertGreater(b['outputs']['reach_lift_pct'], a['outputs']['reach_lift_pct'])
        self.assertGreater(b['outputs']['expected'], a['outputs']['expected'])
        self.assertGreater(b['outputs']['return_lift_pct'], a['outputs']['return_lift_pct'])
        # the room above an even room lifts more than the calibration says, below it less
        self.assertGreater(b['outputs']['reach_lift_pct'], 10.2)
        self.assertLess(a['outputs']['reach_lift_pct'], 10.2)
        self.assertEqual(b['outputs']['room']['parts'], {'follow': 60.0, 'excellent': 57.0})
        self.assertEqual(b['outputs']['basis'], 'name and room')
        self.assertIn('; in the room 60.0% would follow them into a second episode and 57.0% rated them excellent.', b['read'])
        # the quadrant split keeps the colder-quadrant rule and follows the new lift
        qa = [q['reach_lift_pct'] for q in a['by_quadrant'] if q.get('n')]
        qb = [q['reach_lift_pct'] for q in b['by_quadrant'] if q.get('n')]
        self.assertTrue(qa and qb)
        self.assertGreater(min(qb), max(qa))

    def test_the_ncis_test_puts_beals_above_caan(self):
        twin, sess = _ncis()
        recs = {n: C.talent_attach(sess, twin, n, r) for n, r in (('LL Cool J', 'lead'), ('Scott Caan', 'lead'), ('Jennifer Beals', 'supporting'))}
        beals, caan, ll = recs['Jennifer Beals'], recs['Scott Caan'], recs['LL Cool J']
        self.assertGreater(beals['outputs']['reach_lift_pct'], caan['outputs']['reach_lift_pct'])
        self.assertGreater(beals['outputs']['reach_lift_pct'], 10.2, 'the draw lifts more than the calibration')
        self.assertTrue(beals['outputs']['room']['is_draw'])
        self.assertEqual(beals['outputs']['room']['parts'], {'follow': 60.1, 'excellent': 57.2, 'draw': 62.5})
        self.assertEqual(ll['outputs']['room']['parts'], {'follow': 66.1, 'excellent': 57.1, 'draw': 0.0})
        self.assertRegex(beals['read'], r"^Jennifer Beals \(billed supporting\) lifts first-month reach \d+\.\d% \([\d.]+[KM] more people\), most among [a-z0-9+ ]+ \(\d+\.\d%\); "
                                        r"in the room she was the draw \(10 of 16 named her as the reason to come back\): 60\.1% would follow her into a second episode and 57\.2% rated her excellent\.")
        self.assertIn('; in the room 13.8% would follow him into a second episode and 46.8% rated him excellent.', caan['read'])
        self.assertIn('; in the room 66.1% would follow him into a second episode and 57.1% rated him excellent.', ll['read'])
        self.assertIn('Asked who they would watch it with:', beals['read'])
        # one record per person, in attach order
        self.assertEqual([r['talent'] for r in sess['talent_attach']], ['LL Cool J', 'Scott Caan', 'Jennifer Beals'])
        # the standalone engine run on the same room values agrees with the session path
        again = E.run_talent_attach(twin, {'talent': 'Jennifer Beals', 'role': 'supporting', 'room': beals['room']}, f"{sess['id']}|talent|jennifer beals")
        self.assertEqual(again['outputs']['reach_lift_pct'], beals['outputs']['reach_lift_pct'])

    def test_a_bigger_following_with_a_weaker_verdict_is_explained(self):
        """The audience follows one name and the room went the other way: the copy says so, across the names on the test and on a name alone."""
        twin, sess = _ncis()
        twin['identity']['talent'].append({'label': 'Scott Caan', 'pct': 31.4})
        caan = C.talent_attach(sess, twin, 'Scott Caan', 'lead')
        self.assertGreater(caan['outputs']['already_follow_index'], 200)
        self.assertEqual(caan['outputs']['name_pull']['basis'], 'audience row')
        self.assertIn("Scott Caan's name draws with this audience today; the performance drew less in the room.", caan['read'])
        beals = C.talent_attach(sess, twin, 'Jennifer Beals', 'supporting')
        line = "Scott Caan's name draws more today; Jennifer Beals drew more in the room."
        self.assertIn(line, beals['read'])
        self.assertIn(line, sess['talent_attach'][0]['read'], 'the earlier record carries the line too')
        self.assertEqual(sess['talent_attach'][0]['contrast'], line)
        self.assertNotIn('draws with this audience today', sess['talent_attach'][0]['read'], 'one line, not two')
        # the room still outranks the name: his bigger following does not put him above her
        self.assertGreater(caan['outputs']['name_pull']['factor'], beals['outputs']['name_pull']['factor'])
        self.assertEqual(sess['talent_attach'][0]['read_base'].split(' Scott Caan\'s name')[0], sess['talent_attach'][0]['read_base'])
        # two names the room rated alike and the audience follows alike carry no line
        twin2, sess2 = _ncis()
        C.talent_attach(sess2, twin2, 'LL Cool J', 'lead')
        C.talent_attach(sess2, twin2, 'Jennifer Beals', 'supporting')
        self.assertFalse(any(r['contrast'] for r in sess2['talent_attach']))

    def test_talent_fit_overlap_counts_as_name_pull(self):
        twin, sess = _ncis()
        plain = E.run_talent_attach(twin, {'talent': 'Jennifer Beals', 'role': 'supporting'}, 'seed')
        with_overlap = E.run_talent_attach(twin, {'talent': 'Jennifer Beals', 'role': 'supporting', 'overlap_pct': 24.0}, 'seed')
        self.assertEqual(plain['outputs']['already_follow_index'], 100)
        self.assertGreater(with_overlap['outputs']['already_follow_index'], 200)
        self.assertEqual(with_overlap['outputs']['name_pull']['basis'], 'talent fit overlap')
        self.assertGreater(with_overlap['outputs']['reach_lift_pct'], plain['outputs']['reach_lift_pct'])
        self.assertEqual(C._overlap_pct({'overlap': '24.0%'}), 24.0)
        self.assertEqual(C._overlap_pct({'overlap': 0.24}), 24.0)
        self.assertIsNone(C._overlap_pct({'overlap': None}))
        self.assertIsNone(C._overlap_pct({'overlap': 'n/a'}))


class BeforeTheRoomWatched(unittest.TestCase):
    def test_no_scorecard_falls_back_to_name_pull(self):
        twin = _twin()
        sess = _session(twin, scored=False)
        self.assertFalse(sess.get('scorecard'))
        rec = C.talent_attach(sess, twin, 'Jennifer Beals', 'supporting')
        self.assertEqual(rec['outputs']['basis'], 'name only')
        self.assertIsNone(rec['outputs']['room'])
        self.assertIsNone(rec['room'])
        self.assertIn("on the name's pull alone, before the room watched.", rec['read'])
        self.assertTrue(rec['read'].startswith('Jennifer Beals (billed supporting) lifts first-month reach '))
        self.assertEqual(rec['outputs']['already_follow_index'], 100)
        # the calibrated base, on messy tenths, for a name with no row
        self.assertLess(abs(rec['outputs']['reach_lift_pct'] - 10.2), 0.5)
        self.assertIn("No room has watched a cut with this name yet", ' '.join(rec['drivers']))

    def test_a_name_the_cut_did_not_carry_is_name_pull_alone(self):
        twin, sess = _ncis()
        rec = C.talent_attach(sess, twin, 'Mark Harmon', 'lead')
        self.assertEqual(rec['outputs']['basis'], 'name only')
        self.assertEqual(rec['room'], {'seen': False})
        self.assertIn("on the name's pull alone; the room did not see Mark Harmon in this cut.", rec['read'])
        self.assertNotIn('before the room watched', rec['read'])

    def test_the_scored_lift_is_read_against_the_same_base(self):
        """Before the watch a name lifts the calibrated base; after it, the draw lifts more and the name the room passed on lifts less."""
        twin = _twin()
        before = C.talent_attach(_session(twin, scored=False), twin, 'Jennifer Beals', 'supporting')['outputs']['reach_lift_pct']
        _t, sess = _ncis()
        after = C.talent_attach(sess, twin, 'Jennifer Beals', 'supporting')['outputs']['reach_lift_pct']
        passed = C.talent_attach(sess, twin, 'Scott Caan', 'lead')['outputs']['reach_lift_pct']
        self.assertGreater(after, before)
        self.assertLess(passed, before)


class MessyTenthsAndWords(unittest.TestCase):
    def test_messy_tenths(self):
        twin, sess = _ncis()
        seen = set()
        for i, name in enumerate(['LL Cool J', 'Scott Caan', 'Jennifer Beals'] + [f"Name {k}" for k in range(12)]):
            rec = C.talent_attach(sess, twin, name, 'lead')
            v = rec['outputs']['reach_lift_pct']
            self.assertNotEqual(_tenths(v), 0, (name, v))
            self.assertNotEqual(_tenths(rec['outputs']['return_lift_pct']), 0)
            self.assertTrue(0.5 <= v <= 60.0)
            for q in rec['by_quadrant']:
                if q.get('n'):
                    self.assertTrue(0.5 <= q['reach_lift_pct'] <= 60.0)
            self.assertNotEqual(rec['outputs']['expected'] % 10, 0)
            self.assertLess(rec['outputs']['low'], rec['outputs']['expected'])
            self.assertGreater(rec['outputs']['high'], rec['outputs']['expected'])
            seen.add(v)
        # twelve names the room never saw share one input (the base) and still spread across the salted tenths around it
        self.assertGreaterEqual(len(seen), 5, seen)
        for x in (10.0, 60.0, 0.5, 12.04, 7.96, 33.3):
            for salt in range(30):
                v = E.messy_tenths(x, 0.5, 60.0, 'salt', salt)
                self.assertNotEqual(_tenths(v), 0, (x, salt, v))
                self.assertTrue(0.5 <= v <= 60.0)
                self.assertLess(abs(v - x), max(0.35, x * 0.03) + 0.0001)

    def test_no_banned_words(self):
        twin, sess = _ncis()
        twin['identity']['talent'].append({'label': 'Scott Caan', 'pct': 31.4})
        for n, r in (('LL Cool J', 'lead'), ('Scott Caan', 'lead'), ('Jennifer Beals', 'supporting'), ('Mark Harmon', 'host')):
            C.talent_attach(sess, twin, n, r)
        C.talent_attach(_session(twin, scored=False), twin, 'Jennifer Beals', 'supporting')
        blob = json.dumps([{k: v for k, v in r.items() if k != 'room'} for r in sess['talent_attach']], default=str).lower()
        for tok in BANNED:
            self.assertNotIn(tok, blob, tok)
        for r in sess['talent_attach']:
            self.assertNotIn(' as lead', r['read'])
            self.assertNotIn(' as supporting', r['read'])
            for sent in re.split(r"(?<=\.)\s+", r['read']):
                self.assertLessEqual(len(sent.split()), 48, sent)
        doc = E.__doc__ + '\n' + C.talent_attach.__doc__ + '\n' + E.run_talent_attach.__doc__
        self.assertNotIn('\u2014', doc)


class OneRecordPerPersonAndTheSurfaces(unittest.TestCase):
    def test_one_record_per_person_and_the_recompute(self):
        twin, sess = _ncis()
        C.talent_attach(sess, twin, 'jennifer beals', 'lead')
        C.talent_attach(sess, twin, 'Jennifer Beals', 'supporting')
        C.talent_attach(sess, twin, 'LL Cool J', 'lead')
        self.assertEqual([r['talent'] for r in sess['talent_attach']], ['Jennifer Beals', 'LL Cool J'])
        self.assertEqual(sess['talent_attach'][0]['role'], 'supporting')
        before = {r['talent']: r['outputs']['reach_lift_pct'] for r in sess['talent_attach']}
        # the room moves (a question lands, the follow share for LL Cool J changes): the recompute reads the session as it stands
        for f in sess['diagnostics']['characters']['follow']:
            if f['name'] == 'LL Cool J':
                f['pct'] = 26.1
        recs = C.recompute_talent_attach(sess, twin)
        self.assertEqual([r['talent'] for r in recs], ['Jennifer Beals', 'LL Cool J'])
        self.assertEqual(recs[0]['role'], 'supporting')
        after = {r['talent']: r['outputs']['reach_lift_pct'] for r in recs}
        self.assertEqual(after['Jennifer Beals'], before['Jennifer Beals'])
        self.assertLess(after['LL Cool J'], before['LL Cool J'])
        self.assertIn('26.1% would follow him', recs[1]['read'])
        # a recompute with nothing attached is a no-op; a broken session never raises
        self.assertEqual(C.recompute_talent_attach({'talent_attach': []}, twin), [])

    def test_a_room_that_moved_reads_as_stale_and_the_page_read_refreshes_it(self):
        twin, sess = _ncis()
        self.assertFalse(C.talent_attach_stale(sess), 'nothing attached')
        C.talent_attach(sess, twin, 'Jennifer Beals', 'supporting')
        C.talent_attach(sess, twin, 'Mark Harmon', 'lead')
        self.assertFalse(C.talent_attach_stale(sess))
        # the words can move (the moderator re-reads the person) without the numbers: not stale
        sess['diagnostics']['characters']['characters'][2]['room_said']['said'] = 'They wanted more of her.'
        self.assertFalse(C.talent_attach_stale(sess))
        # the follow share moves: stale
        for f in sess['diagnostics']['characters']['follow']:
            if f['name'] == 'Jennifer Beals':
                f['pct'] = 71.3
        self.assertTrue(C.talent_attach_stale(sess))
        C.recompute_talent_attach(sess, twin)
        self.assertFalse(C.talent_attach_stale(sess))
        self.assertIn('71.3% would follow her', sess['talent_attach'][0]['read'])
        # a record written before the lift read the room (no room snapshot, no basis) is stale
        old = {'talent': 'Scott Caan', 'role': 'lead', 'outputs': {'reach_lift_pct': 10.2, 'expected': 1775743}, 'by_quadrant': [], 'watch_with': [], 'read': 'Scott Caan as lead lifts first-month reach 10.2%.'}
        sess['talent_attach'].append(old)
        self.assertTrue(C.talent_attach_stale(sess))
        recs = C.recompute_talent_attach(sess, twin)
        self.assertEqual([r['talent'] for r in recs], ['Jennifer Beals', 'Mark Harmon', 'Scott Caan'])
        self.assertTrue(all('read_base' in r and 'basis' in r['outputs'] for r in recs))
        # the page read runs the same refresh when the room moved (the route helper), and saves
        with open(os.path.join(ROOT, 'digital_twins_api.py'), 'r', encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('if _refresh_talent_attach(payload, sess):', api)
        self.assertIn('CMP.recompute_talent_attach(sess, payload, s3=_s3())', api)
        self.assertGreater(api.index('if _refresh_talent_attach(payload, sess):'), api.index('if _reconcile(payload, sess):'), 'after the reconcile, before the save')

    def test_the_room_landing_refreshes_the_attach(self):
        with open(os.path.join(ROOT, 'migration', 'digital_twin_room.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("CMP.recompute_talent_attach(session, twin, s3=s3)", src)
        self.assertGreater(src.index("CMP.recompute_talent_attach(session, twin, s3=s3)"), src.index("TR.attach_talent_read(session, twin, models=models, reason="), 'after the talent read, before the landing')

    def test_the_report_page_and_the_csv_carry_the_block(self):
        twin, sess = _ncis()
        for n, r in (('LL Cool J', 'lead'), ('Scott Caan', 'lead'), ('Jennifer Beals', 'supporting')):
            C.talent_attach(sess, twin, n, r)
        payload = {'meta': twin['meta']}
        pages = R.report_pages(payload, sess, with_chart=False)
        pg = next(p for p in pages if 'first month' in p['header'] and p['kind'] == 'table')
        self.assertEqual(pg['header'], 'Jennifer Beals adds the most to the first month, ' + f"{sess['talent_attach'][-1]['outputs']['reach_lift_pct']:.1f}%.")
        self.assertLessEqual(len(pg['header'].split()), 12)
        self.assertEqual(pg['head'], ('Name', 'First-month lift', 'People, and the room'))
        self.assertEqual([r[0] for r in pg['rows']][0], 'Jennifer Beals (billed supporting)')
        self.assertIn('10 of 16 the reason to come back', pg['rows'][0][2])
        self.assertEqual(len(pg['rows']), 3)
        self.assertTrue(pg['body'][0].startswith('Jennifer Beals (billed supporting) lifts first-month reach'))
        self.assertGreater(pages.index(pg), [p['kind'] for p in pages].index('table'), 'after the scorecard table')
        csv = PI.session_csv(sess, payload)
        lines = csv.splitlines()
        self.assertIn('Talent attach,Billed,First-month reach lift %,More people,Low,High,Return lift %,Name pull index,Would follow into a second episode %,Excellent %,Named as the reason to come back,Read', lines)
        start = lines.index('Talent attach,Billed,First-month reach lift %,More people,Low,High,Return lift %,Name pull index,Would follow into a second episode %,Excellent %,Named as the reason to come back,Read')
        block = lines[start + 1:start + 4]
        beals = next(l for l in block if l.startswith('Jennifer Beals,supporting,'))
        self.assertIn(',60.1,57.2,10 of 16,', beals)
        self.assertTrue(any(l.startswith('Scott Caan,lead,') and ',13.8,46.8,0 of 16,' in l for l in block))
        self.assertIn('Talent attach by quadrant,Name,Quadrant,Seats,People,Reach lift %', lines)
        self.assertTrue(any(l.startswith(',Jennifer Beals,Women 35+,') or l.startswith(',Jennifer Beals,Men 35+,') for l in lines))
        self.assertIn(pg['header'], csv, 'the report block carries the page')
        low = csv.lower()
        for tok in ('synth', 'modeled', 'estimated', '\u2014'):
            self.assertNotIn(tok, low)
        # a test with nothing attached carries no block and no page
        twin2, sess2 = _ncis()
        self.assertFalse([p for p in R.report_pages(payload, sess2, with_chart=False) if 'first month' in p['header'] and p['kind'] == 'table'])
        self.assertNotIn('Talent attach,Billed', PI.session_csv(sess2, payload))

    def test_the_console_shows_every_attached_name(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), 'r', encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("tal.slice(-6).map(function(t)", src)
        self.assertNotIn("tal.slice(-2).map(function(t)", src)
        self.assertIn('Billing is shown, never weighed.', src)


class EngineParts(unittest.TestCase):
    def test_room_verdict_weights_and_even_room(self):
        self.assertIsNone(E.room_verdict(None))
        self.assertIsNone(E.room_verdict({'seats': 16}))
        even = E.room_verdict({'follow_pct': 50.0, 'excellent_pct': 50.0, 'seats': 16, 'draw_seats': 8, 'draw_asked': True})
        self.assertAlmostEqual(even['factor'], 1.0)
        self.assertFalse(even['is_draw'], 'half the room is the line, not over it')
        v = E.room_verdict({'follow_pct': 60.1, 'excellent_pct': 57.2, 'seats': 16, 'draw_seats': 10})
        self.assertEqual(v['parts'], {'follow': 60.1, 'excellent': 57.2, 'draw': 62.5})
        self.assertAlmostEqual(v['verdict_pct'], round((0.5 * 60.1 + 0.3 * 57.2 + 0.2 * 62.5), 1))
        self.assertTrue(v['is_draw'])
        # the room not asked about the draw: the verdict is follow and excellent, renormalized
        v2 = E.room_verdict({'follow_pct': 60.0, 'excellent_pct': 40.0, 'seats': 16})
        self.assertEqual(v2['parts'], {'follow': 60.0, 'excellent': 40.0})
        self.assertAlmostEqual(v2['verdict_pct'], round((0.5 * 60 + 0.3 * 40) / 0.8, 1))
        # asked, and nobody named them: the draw share is zero and counts
        v3 = E.room_verdict({'follow_pct': 60.0, 'excellent_pct': 40.0, 'seats': 16, 'draw_asked': True})
        self.assertEqual(v3['parts']['draw'], 0.0)
        self.assertLess(v3['verdict_pct'], v2['verdict_pct'])
        self.assertEqual(sum(E.ROOM_WEIGHTS.values()), 1.0)
        self.assertEqual(E.ROOM_EVEN_SHARE, 50.0)

    def test_name_pull(self):
        twin = _twin()
        self.assertEqual(E.name_pull(twin, 'Jennifer Beals'), {'factor': 1.0, 'index': 100, 'audience_share_pct': None, 'basis': 'base'})
        twin['identity']['talent'].append({'label': 'Jennifer Beals', 'pct': 20.0})
        self.assertEqual(E.name_pull(twin, 'Jennifer Beals')['index'], 200, 'a fifth of the audience doubles the pull')
        self.assertEqual(E.name_pull(twin, 'THE Weeknd')['index'], 250, 'the band caps a mass following')
        self.assertEqual(E.name_pull(twin, 'Jennifer Beals', {'overlap_pct': 10.0})['basis'], 'audience row', 'the larger of the two stands')
        self.assertEqual(E.name_pull(twin, 'Jennifer Beals', {'overlap_pct': 30.0})['basis'], 'talent fit overlap')


if __name__ == '__main__':
    unittest.main(verbosity=2)
