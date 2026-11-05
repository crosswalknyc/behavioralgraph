#!/usr/bin/env python3
"""Digital Twins IQ: screen time never weights appeal.

Jenna, 2026-11-04 (verbatim): "screen time should never affect appeal or
anything. a cameo could be the best part of a show."

The incident: on the NCIS: New York Pilot Testing card Jennifer Beals read
25.2% excellent and 9.8% on 'follow into a second episode' while ten of
sixteen seats had named her as the reason they would come back. A bonus
for time on screen (share / 12, capped at 4.5) sat inside character
appeal and the follow share was a softmax over those scores.

What this file holds:

  1. two casts identical in reactions, ratings and answers but with very
     different time on screen produce identical appeal, follow shares and
     relationship scores (the minutes change the facts on the card, never
     the numbers);
  2. a 90-second cameo with the strongest reactions and the most 'come
     back for them' answers leads the card, the follow, the report page
     and the CSV; the ledger still files the lead under the lead's part;
  3. the appeal formula carries no scene-count or duration term, and the
     follow question never drops a character for having little time.

The follow is a share of the room per person (2026-11-04): a seat can
follow more than one person, so the shares do not add to 100; the ledger
files the lead's share under 'Would follow the lead into a second
episode'.

Hermetic (REGRESSION_TEST_MODE=1, no model, no network). The standing
rule lives in the parent repo at
.cursor/rules/screen-time-never-weights-appeal.mdc.
"""
from __future__ import annotations

import copy
import csv
import inspect
import io
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_diagnostics as D  # noqa: E402
from migration import digital_twin_reconcile as RC  # noqa: E402
from migration import digital_twin_report as REP  # noqa: E402
from test_digital_twin_pilot import _beals  # noqa: E402

LEAD, SECOND, CAMEO = 'Mae Carter', 'Ray Dunn', 'Lorelai Voss'
EXTRAS = ('Tom Hale', 'June Park', 'Owen Reyes')
RUNTIME_S = 1800.0           # a thirty-minute cut on the twelve-second scene grid
CAMEO_FROM, CAMEO_TO = 900.0, 990.0   # her ninety seconds, nobody else speaks inside them
NOW = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'panel', 'sample', 'research', '\u2014', '\u2013')


def _transcript(extras=()):
    """The leads talk through the cut (eight-second lines every forty
    seconds, none inside the cameo's window); the cameo has three
    thirty-second lines from 15:00 to 16:30; extras, when asked for, each
    get a short stretch of their own so the cast runs past five."""
    rows = []
    for t in range(5, int(RUNTIME_S) - 10, 40):
        if not (CAMEO_FROM - 20 <= t <= CAMEO_TO + 10):
            rows.append({'t': t, 'end': t + 8, 'text': 'We are closed', 'speaker': LEAD})
    for t in range(25, int(RUNTIME_S) - 10, 40):
        if not (CAMEO_FROM - 20 <= t <= CAMEO_TO + 10):
            rows.append({'t': t, 'end': t + 8, 'text': 'Not for me', 'speaker': SECOND})
    rows += [{'t': CAMEO_FROM, 'end': CAMEO_FROM + 30, 'text': 'Sit down, both of you', 'speaker': CAMEO},
             {'t': CAMEO_FROM + 30, 'end': CAMEO_FROM + 60, 'text': 'I run this room', 'speaker': CAMEO},
             {'t': CAMEO_FROM + 60, 'end': CAMEO_TO, 'text': 'Bring me the file', 'speaker': CAMEO}]
    for k, name in enumerate(extras):
        base = 200 + 220 * k          # 3:20, 7:00, 10:40: two hundred seconds each, well clear of the cameo
        rows += [{'t': base + j * 20, 'end': base + j * 20 + 18, 'text': 'Copy that', 'speaker': name} for j in range(10)]
    rows.sort(key=lambda r: r['t'])
    return rows


def _session(session_id='screentime0001', extras=(), title='A procedural pilot'):
    twin = _beals()
    sess = PI.open_session([twin], title='Pilot', in_room=16, session_id=session_id)   # a fixed id keeps every salted draw the same run to run
    sess['cast'] = [LEAD, SECOND, CAMEO] + list(extras)
    sess['play_charged'] = True
    rows = _transcript(extras)
    PI.prepare_watch(sess, {'jb': twin}, duration_s=RUNTIME_S, title=title, transcript=rows)
    sess['transcript'] = copy.deepcopy(rows)     # a cut read off a file carries its transcript on the test; the cast's clock reads it
    D.run_diagnostics(sess, twin)
    return twin, sess


def _rec(qid, q, options, picks, *, clock='29:55', framing='', kind='open', lines=None):
    rec = {'id': qid, 'q': q, 'framing': framing, 'kind': kind, 'stage': 'done', 'engine': 'room-2', 'asked_at': NOW, 'started_at': NOW, 'finished_at': NOW,
           'playhead_clock': clock, 'playhead_s': 1795.0, 'options': [{'id': i, 'label': lab, 'lean': lean, 'pct': 0.0, 'people': 0, 'seats': []} for i, lab, lean in options],
           'option_labels': [lab for _i, lab, _l in options], 'answers': [], 'voices': [], 'read': 'The room answered.', 'distribution': [], 'by_segment': [], 'tally': []}
    for seat, oid in sorted(picks.items()):
        rec['answers'].append({'seat': seat, 'option': oid, 'confidence': 4, 'line': (lines or {}).get(seat, 'Sure.')})
    return rec


def _watch_more(namers, pair_seats, rest, lines=None):
    picks = {}
    for s in namers:
        picks[s] = 'a'
    for s in pair_seats:
        picks[s] = 'b'
    for s in rest:
        picks[s] = 'c'
    return _rec('q1', f"what would make you watch another episode? if you knew there was more {CAMEO}?",
                [('a', f"More of {CAMEO} brings me back", 'yes'), ('b', f"More of {LEAD} and {SECOND} together", 'yes'),
                 ('c', 'Only if the next case has a real hook', 'maybe'), ('d', 'Nothing here, one episode was enough for me', 'no')],
                picks, framing='The cut has ended. What would get you to press play on episode two?', lines=lines)


def _feel(name, picks):
    return _rec('q2', f"how did you feel about {name}?", [('a', 'The best thing in it', 'yes'), ('b', 'Liked them fine', 'maybe'), ('c', 'Too stiff, no warmth', 'no')], picks)


def _cast(sess):
    return {c['name']: c for c in D.cast_from_session(sess)}


def _appeal(ch):
    """Everything on the characters card that is a number the room gave,
    keyed by name; the time facts (share, clock, role, norm) left out."""
    out = {}
    for c in ch['characters']:
        out[c['name']] = {'excellent_pct': c['excellent_pct'], 'total_positive_pct': c['total_positive_pct'], 'likability': c['likability'],
                          'rows': [(r['id'], r['pct']) for r in c['rows']], 'draw_seats': c.get('draw_seats')}
    return out


def _no_banned(obj):
    blob = json.dumps(obj, default=str).lower()
    return [b for b in BANNED if b in blob]


class SameRoomDifferentMinutes(unittest.TestCase):
    """Cast A and cast B: the same seats, the same dials, the same
    reactions, the same answers to the same questions. In B the cameo
    carries most of the cut's time and the lead almost none. Every
    number the room gave must match to the last digit."""

    def _pair(self):
        twin, a = _session()
        b = copy.deepcopy(a)
        for row in b['transcript']:
            if row['speaker'] == CAMEO:
                row['end'] = row['t'] + 700.0       # her lines now run most of the cut
            elif row['speaker'] == LEAD:
                row['end'] = row['t'] + 0.5         # his barely register on the clock
        namers = [1, 2, 3, 5, 6, 8, 9, 11, 13]      # nine of sixteen come back for her
        lines = {s: 'She held the room every time she was in it, I want more of her running it.' for s in namers}
        for sess in (a, b):
            sess['questions'] = [_watch_more(namers, [4, 7, 10], [12, 14, 15, 16], lines=lines), _feel(SECOND, {s: ('a' if s % 3 == 0 else 'b' if s % 3 == 1 else 'c') for s in range(1, 17)})]
        return twin, a, b

    def test_the_minutes_are_very_different(self):
        twin, a, b = self._pair()
        ca, cb = _cast(a), _cast(b)
        self.assertLess(ca[CAMEO]['share_pct'], 20.0, ca[CAMEO])
        self.assertGreater(cb[CAMEO]['share_pct'], 50.0, cb[CAMEO])
        self.assertEqual(ca[LEAD]['role'], 'lead')
        self.assertEqual(ca[CAMEO]['role'], 'supporting')
        self.assertEqual(cb[CAMEO]['role'], 'lead', 'in cast B the clock makes her the lead of the cut')
        self.assertNotEqual(ca[LEAD]['role'], cb[LEAD]['role'])
        # what did not change: the scenes each character is in, the dials, the reactions
        for name in (LEAD, SECOND, CAMEO):
            self.assertEqual(ca[name]['scenes'], cb[name]['scenes'])
        self.assertEqual(a['seat_dials'], b['seat_dials'])
        self.assertEqual(a['events'], b['events'])

    def test_identical_appeal_follow_and_relationship(self):
        twin, a, b = self._pair()
        cha, chb = D.character_table(a, twin), D.character_table(b, twin)
        self.assertEqual(_appeal(cha), _appeal(chb))
        self.assertEqual([(f['name'], f['pct']) for f in cha['follow']], [(f['name'], f['pct']) for f in chb['follow']])
        self.assertEqual(set(cha['relationship']['pair']), set(chb['relationship']['pair']))
        self.assertEqual(cha['relationship']['excellent_pct'], chb['relationship']['excellent_pct'])
        self.assertEqual(cha['relationship']['total_positive_pct'], chb['relationship']['total_positive_pct'])
        self.assertEqual({r['seat']: r['ratings'] for r in cha['by_seat']}, {r['seat']: r['ratings'] for r in chb['by_seat']})
        self.assertEqual(cha['from_room']['draw'], CAMEO)
        self.assertEqual(chb['from_room']['draw'], CAMEO)
        self.assertEqual([c['name'] for c in cha['characters']], [c['name'] for c in chb['characters']], 'the card ranks the cast by the room, so the order matches too')
        # the facts moved, as facts should: the clock, the share, the part, the usual mark for the part
        ra, rb = {c['name']: c for c in cha['characters']}, {c['name']: c for c in chb['characters']}
        self.assertNotEqual(ra[CAMEO]['screen_clock'], rb[CAMEO]['screen_clock'])
        self.assertNotEqual(ra[CAMEO]['share_pct'], rb[CAMEO]['share_pct'])
        self.assertNotEqual(ra[CAMEO]['role'], rb[CAMEO]['role'])
        self.assertEqual(_no_banned(cha), [])

    def test_before_the_room_speaks_too(self):
        twin, a, b = self._pair()
        a['questions'], b['questions'] = [], []
        cha, chb = D.character_table(a, twin), D.character_table(b, twin)
        self.assertEqual(_appeal(cha), _appeal(chb))
        self.assertEqual([(f['name'], f['pct']) for f in cha['follow']], [(f['name'], f['pct']) for f in chb['follow']])
        self.assertEqual(cha['relationship']['excellent_pct'], chb['relationship']['excellent_pct'])


class TheCameoLeadsTheCard(unittest.TestCase):
    """Ninety seconds on screen, the strongest reactions in the room and
    twelve of sixteen naming her as the reason to come back: she leads
    the card, the follow, the report page and the CSV."""

    def _room(self):
        twin, sess = _session(session_id='screentime0002')
        hers = set(_cast(sess)[CAMEO]['scenes'])
        self.assertTrue(hers)
        roster = sess['group']['roster']
        for sno, series in sess['seat_dials'].items():
            for i, bt in enumerate(sess['beats']):
                if i < len(series) and bt.get('scene_id') in hers:
                    series[i] = min(97.0, series[i] + 16.0)      # the room's dial peaks while she is on
        first = min(hers)
        for r in roster:
            sno = int(r['seat'])
            sess['events'] = [e for e in sess['events'] if not (int(e.get('seat') or -1) == sno and e.get('scene_id') in hers)]
            for kind in ('love', 'wow'):
                sess['events'].append({'t': CAMEO_FROM + 5.0, 'type': kind, 'seat': sno, 'name': r.get('name'), 'scene_id': first, 'line': 'She runs the room.', 'live': True})
        sess['events'].sort(key=lambda e: float(e.get('t') or 0))
        D.run_diagnostics(sess, twin)
        PI.stop_session(sess)
        namers = [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 13, 15]
        lines = {s: 'She held the room every time she was in it, I want more of her running it.' for s in namers}
        sess['questions'] = [_watch_more(namers, [7, 11], [14, 16], lines=lines)]
        out = RC.reconcile_session(sess, twin)
        self.assertTrue(out['changed'])
        return twin, sess

    def test_she_leads_the_card_and_the_follow(self):
        twin, sess = self._room()
        ch = sess['diagnostics']['characters']
        rows = {c['name']: c for c in ch['characters']}
        her, lead, second = rows[CAMEO], rows[LEAD], rows[SECOND]
        # the facts: a cameo by the clock
        self.assertEqual(her['role'], 'supporting')
        self.assertLess(her['share_pct'], 20.0, her)
        self.assertLess(her['screen_s'] * 3, lead['screen_s'], (her['screen_s'], lead['screen_s']))
        self.assertEqual(her['draw_seats'], 12)
        # the numbers: she leads
        self.assertEqual(ch['characters'][0]['name'], CAMEO, [(c['name'], c['excellent_pct']) for c in ch['characters']])
        self.assertGreater(her['excellent_pct'], lead['excellent_pct'])
        self.assertGreater(her['excellent_pct'], second['excellent_pct'])
        self.assertGreater(her['total_positive_pct'], lead['total_positive_pct'])
        # the follow is a share of the room per person (a seat can follow more than one): twelve of sixteen named her, so she
        # leads it; the leads carry their own shares and the three do not add to 100
        self.assertEqual(ch['follow'][0]['name'], CAMEO)
        self.assertGreaterEqual(ch['follow'][0]['pct'], 60.0, ch['follow'])
        self.assertEqual(ch['follow'][0]['seats'], 12)
        self.assertGreater(sum(f['pct'] for f in ch['follow']), 100.0, ch['follow'])
        self.assertEqual(ch['follow_note'], 'A seat can follow more than one person.')
        # the read leads with the room's word, and the clock rides as a fact in prose
        self.assertTrue(ch['read'].startswith(f"{CAMEO} is the draw: 12 of 16 would come back."), ch['read'])
        self.assertIn('of the screen time', ch['read'])
        self.assertIn(her['time_vs_like'], ('earns more time', 'carries the time', 'in balance', 'gets more time than the room gives back'))
        self.assertEqual(_no_banned(ch), [])

    def test_the_report_and_the_csv_rank_by_the_room(self):
        twin, sess = self._room()
        pages = REP.report_pages(twin, sess)
        chars_page = next(p for p in pages if p.get('head') == ('Character', 'Excellent', 'Norm and screen time'))
        self.assertTrue(chars_page['rows'][0][0].startswith(CAMEO), chars_page['rows'])
        self.assertTrue(chars_page['header'].startswith(f"{CAMEO} is the draw"), chars_page['header'])
        concl = pages[-1]['body']
        self.assertTrue(any(line.startswith(f"Give {CAMEO} more of the next episode") for line in concl), concl)
        self.assertTrue(any(LEAD in line and 'lead' in line for line in concl), 'the lead line still speaks of the part the cut is built around')
        text = PI.session_csv(sess)
        table = list(csv.reader(io.StringIO(text)))
        head_i = next(i for i, r in enumerate(table) if r and r[0] == 'Character')
        self.assertEqual(table[head_i + 1][0], CAMEO, table[head_i:head_i + 4])
        self.assertIn('On screen', table[head_i])

    def test_the_ledger_files_the_lead_under_the_lead(self):
        twin, sess = self._room()
        rows = {c['name']: c for c in sess['diagnostics']['characters']['characters']}
        m = D.diagnostics_norm_metrics(sess)
        self.assertEqual(m['lead_character'], rows[LEAD]['excellent_pct'])
        self.assertEqual(m['secondary_character'], rows[SECOND]['excellent_pct'])
        self.assertEqual(m['supporting_character'], rows[CAMEO]['excellent_pct'])
        # the ledger's follow cell is the lead's share ('Would follow the lead into a second episode'), not the top of a list
        # the cameo leads
        follow = {f['name']: f['pct'] for f in sess['diagnostics']['characters']['follow']}
        self.assertEqual(sess['diagnostics']['characters']['follow'][0]['name'], CAMEO)
        self.assertEqual(m['follow_second_episode'], follow[LEAD])
        self.assertNotEqual(m['follow_second_episode'], follow[CAMEO])


class TheFormula(unittest.TestCase):
    def test_the_same_reactions_earn_the_same_lift_in_one_scene_or_twenty(self):
        twin = _beals()
        beats = [{'i': i, 'scene_id': i // 2, 't': 6.0 * i} for i in range(40)]      # twenty scenes, two beats each
        series = [52.0] * 40
        one = {'name': 'Nell Price', 'scenes': [0]}
        twenty = {'name': 'Nell Price', 'scenes': list(range(20))}
        for events in ([], [{'seat': 3, 'type': 'laugh', 'scene_id': 0}], [{'seat': 3, 'type': 'love', 'scene_id': 0}, {'seat': 3, 'type': 'confuse', 'scene_id': 0}]):
            a = D._seat_char_score(series, beats, one, {}, twin, 'seed|seat3', events=events, seat_no=3)
            b = D._seat_char_score(series, beats, twenty, {}, twin, 'seed|seat3', events=events, seat_no=3)
            self.assertEqual(a, b, (events, a, b))
        quiet = D._seat_char_score(series, beats, twenty, {}, twin, 'seed|seat3', events=[], seat_no=3)
        laughed = D._seat_char_score(series, beats, twenty, {}, twin, 'seed|seat3', events=[{'seat': 3, 'type': 'laugh', 'scene_id': 7}], seat_no=3)
        self.assertGreater(laughed, quiet, 'a reaction in any of her scenes still counts in full')

    def test_the_formula_carries_no_duration_term(self):
        src = inspect.getsource(D._seat_char_score)
        for term in ('share_pct', 'screen_s', 'screen_clock', 'sqrt', 'len(scene_ids)', 'len(idxs)', 't_end', 't_start', 'turns', 'runtime'):
            self.assertNotIn(term, src, term)

    def test_the_follow_question_never_drops_the_cameo_for_her_minutes(self):
        twin, sess = _session(session_id='screentime0003', extras=EXTRAS)
        cast = D.cast_from_session(sess)
        self.assertEqual(len(cast), 6)
        self.assertEqual(cast[-1]['name'], CAMEO, 'by the clock she is sixth of six')
        namers = [1, 2, 3, 5, 6, 8, 9, 11, 13]
        sess['questions'] = [_watch_more(namers, [4, 7], [10, 12, 14, 15, 16])]
        ch = D.character_table(sess, twin)
        names = [f['name'] for f in ch['follow']]
        self.assertIn(CAMEO, names, names)
        self.assertEqual(len(names), 5)
        # her share is the nine seats that named her, at their confidence, by the people they stand for; the extras with far
        # more minutes sit under her, and the shares are per person (never a split that her minutes could squeeze)
        her = next(f for f in ch['follow'] if f['name'] == CAMEO)
        self.assertEqual(her['seats'], 9)
        self.assertGreaterEqual(her['pct'], 50.0, ch['follow'])
        for f in ch['follow']:
            if f['name'] in EXTRAS:
                self.assertLess(f['pct'], her['pct'], (f, her))
        self.assertGreater(sum(f['pct'] for f in ch['follow']), 100.0, ch['follow'])
        # the ledger's supporting mark is the mean of every supporting row, not the row with the most minutes
        sess['diagnostics']['characters'] = ch
        supp = [c['excellent_pct'] for c in ch['characters'] if c['role'] == 'supporting']
        self.assertEqual(len(supp), 4)
        self.assertEqual(D.diagnostics_norm_metrics(sess)['supporting_character'], round(sum(supp) / len(supp), 1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
