#!/usr/bin/env python3
"""Pilot Testing phase 2: the screening modes, the diagnostics, the
comparisons, the recruit block, the critics room, the hooks, the report,
the pricing and the partner surface (2026-10-28). Hermetic: FakeS3,
REGRESSION_TEST_MODE=1, no ffmpeg, no model.
"""
from __future__ import annotations

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

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_modes as M  # noqa: E402
from migration import digital_twin_diagnostics as D  # noqa: E402
from migration import digital_twins as DT  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals, _young, FakeS3  # noqa: E402

LOGLINE = 'A chef returns to her hometown diner after a scandal and has one summer to win the town back.'
SCRIPT = """INT. DINER - MORNING

MAE wipes the counter. The bell rings.

          MAE
     We're closed.

          RAY
     Not for me, you're not.

Ray sits. Mae does not look up.

EXT. MAIN STREET - DAY

Mae crosses the street. Everyone looks. A dog barks. The mayor pretends not to see her.

          MAE
     Say it to my face.

          MAYOR
     Welcome home, Mae.

INT. DINER - NIGHT

The room is full. Ray raises a glass.

          RAY
     To second helpings.
"""
TRANSCRIPT = [{'t': 10, 'end': 14, 'text': 'We are closed', 'speaker': 'Mae'}, {'t': 30, 'end': 34, 'text': 'Not for me', 'speaker': 'Ray'},
              {'t': 300, 'end': 304, 'text': 'Say it to my face', 'speaker': 'Mae'}, {'t': 500, 'end': 504, 'text': 'To second helpings', 'speaker': 'Ray'},
              {'t': 540, 'end': 544, 'text': 'Welcome home', 'speaker': 'Mayor'}]
BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'agent', '\u2014', '\u2013')


def _pilot(twin=None, **kw):
    twin = twin or _beals()
    sess = PI.open_session([twin], title=kw.pop('title', 'Pilot'), in_room=kw.pop('in_room', 12), **{k: v for k, v in kw.items() if k in ('mode', 'logline', 'text', 'options', 'rough_cut', 'critics_room')})
    return twin, sess


def _no_banned(obj, where=''):
    blob = json.dumps(obj, default=str).lower()
    hits = [b for b in BANNED if b in blob]
    return hits


class Modes(unittest.TestCase):
    def test_each_mode_opens_with_its_battery_and_norms_cell(self):
        twin = _beals()
        for mode in ('pilot', 'feature', 'episode', 'binge', 'trailer', 'spot', 'title', 'key_art', 'script', 'concept'):
            kw = {}
            if mode in ('title', 'key_art'):
                kw['options'] = ['The Diner', 'Hometown Heat', 'Second Helping']
            if mode in ('script', 'concept'):
                kw['text'] = SCRIPT if mode == 'script' else LOGLINE
            sess = PI.open_session([twin], title=f"{mode} test", in_room=8, mode=mode, logline=LOGLINE, **kw)
            self.assertEqual(sess['mode'], mode)
            self.assertEqual(PI.charge_due(sess), DT.MODE_PRICE_KEY[mode])
            PI.prepare_watch(sess, {'jb': twin}, duration_s=(95 if mode in ('trailer', 'spot') else 600 if mode in ('pilot', 'feature', 'episode', 'binge') else 0), title=f"{mode} cut")
            self.assertEqual(sess['status'], 'ready')
            fmt = sess['format']
            self.assertIn(fmt, ('pilot', 'feature', 'episode', 'trailer', 'title', 'key_art', 'script', 'concept'))
            bat = sess['scorecard']['battery']
            self.assertTrue(bat['questions'], mode)
            self.assertEqual(bat['format'], fmt)
            norms = sess['scorecard']['norms']
            self.assertEqual(norms['format'], fmt)
            self.assertTrue(norms['all'].get('total_positive') or norms['all'].get('tune_in'), mode)
            self.assertIn('mode_read', sess)
            self.assertEqual(sess['mode_read'].get('mode'), mode)

    def test_trailer_lift_computed_and_in_range(self):
        twin, sess = _pilot(mode='trailer', logline=LOGLINE, in_room=10)
        PI.prepare_watch(sess, {'jb': twin}, duration_s=95, title='Hurry Up Tomorrow trailer')
        r = sess['mode_read']
        for k in ('interest_before_pct', 'interest_after_pct', 'definite_interest_lift_pp', 'would_watch_pct', 'who_for', 'understood', 'expect', 'sold_it', 'quadrants', 'read'):
            self.assertIn(k, r)
        self.assertGreaterEqual(r['interest_before_pct'], 0.0)
        self.assertLessEqual(r['interest_before_pct'], 100.0)
        self.assertAlmostEqual(r['definite_interest_lift_pp'], round(r['interest_after_pct'] - r['interest_before_pct'], 1), places=1)
        self.assertGreaterEqual(r['definite_interest_lift_pp'], -100.0)
        self.assertLessEqual(r['definite_interest_lift_pp'], 100.0)
        self.assertEqual(len(r['understood']), 10)
        self.assertTrue(r['who_for']['line'].startswith('Reads as a title for'))
        self.assertEqual(len(r['quadrants']), 4)
        self.assertEqual(sess['format'], 'trailer')
        self.assertEqual(PI.play_quantity(sess), 1)
        sess['assets'] = [{'id': 'a1', 's3_key': 'x'}, {'id': 'a2', 's3_key': 'y'}, {'id': 'a3', 's3_key': 'z'}, {'id': 'a4', 's3_key': 'w'}]
        self.assertEqual(PI.play_quantity(sess), 3)
        self.assertFalse(_no_banned(r))

    def test_trailer_second_asset_same_room_and_compare(self):
        twin, sess = _pilot(mode='trailer', logline=LOGLINE, in_room=8)
        PI.prepare_watch(sess, {'jb': twin}, duration_s=60, title='Trailer A')
        rec = M.prepare_extra_asset(sess, {'id': 'b', 'slot': 1, 'label': 'Trailer B', 'title': 'Trailer B', 'runtime_s': 45}, {'jb': twin})
        self.assertIn('trailer', rec)
        self.assertEqual(len(rec['dial']), len(rec['t']))
        self.assertEqual(set(rec['seat_dials'].keys()), set(sess['seat_dials'].keys()))
        M.finish_mode_read(sess, {'jb': twin})
        cmp_ = sess['mode_read'].get('compare')
        self.assertTrue(cmp_)
        self.assertEqual(len(cmp_['ranked']), 2)
        self.assertIn(cmp_['winner'], ('Trailer A', 'Trailer B'))

    def test_title_test_ranks_six_options_with_reasons(self):
        opts = ['The Diner', 'Hometown Heat', 'Second Helping', 'What Happened to Mae', 'Service', 'Burned']
        twin, sess = _pilot(mode='title', logline=LOGLINE, options=opts, in_room=12)
        PI.prepare_watch(sess, {'jb': twin}, title='Six titles')
        r = sess['mode_read']
        self.assertEqual(r['mode'], 'title')
        self.assertEqual(len(r['options']), 6)
        self.assertEqual([o['rank'] for o in r['options']], [1, 2, 3, 4, 5, 6])
        tot = sum(o['preference_pct'] for o in r['options'])
        self.assertAlmostEqual(tot, 100.0, delta=0.6)
        self.assertTrue(all(o['preference_pct'] > 0 for o in r['options']))
        self.assertTrue(r['options'][0]['reasons'], 'the winner carries reasons')
        self.assertTrue(all('fit_pct' in o and 'unique_pct' in o for o in r['options']))
        self.assertEqual(len(r['quadrants']), 4)
        self.assertEqual(len(sess['scenes']), 6)
        self.assertIn(r['winner_label'], opts)
        self.assertTrue(r['read'])

    def test_key_art_test_has_attention_and_lift(self):
        twin, sess = _pilot(mode='key_art', logline=LOGLINE, options=[{'label': 'Art A', 'caption': 'A woman in a diner doorway at night, the title in red'},
                                                                        {'label': 'Art B', 'caption': 'A bright sunny street, the whole ensemble smiling'}], in_room=10)
        PI.prepare_watch(sess, {'jb': twin}, title='Two pieces of art')
        r = sess['mode_read']
        self.assertEqual(r['mode'], 'key_art')
        for o in r['options']:
            self.assertIn('attention_pct', o)
            self.assertIn('interest_lift_pp', o)
        self.assertTrue(M.accept_image_name('poster.png'))
        self.assertFalse(M.accept_image_name('poster.mp4'))

    def test_script_scene_split_on_sluglines(self):
        sc = M.script_scenes(SCRIPT)
        self.assertEqual(sc['scene_count'], 3)
        self.assertEqual(sc['scenes'][0]['slug'], 'INT. DINER - MORNING')
        self.assertEqual(sc['scenes'][1]['slug'], 'EXT. MAIN STREET - DAY')
        self.assertIn('Mae', sc['cast'])
        self.assertIn('Ray', sc['cast'])
        self.assertTrue(sc['transcript'])
        self.assertGreater(sc['runtime_s'], 15)
        twin, sess = _pilot(mode='script', text=SCRIPT, in_room=8)
        PI.prepare_watch(sess, {'jb': twin}, title='Second Helping script')
        self.assertEqual(sess['format'], 'script')
        self.assertEqual(len(sess['scenes']), 3)
        r = sess['mode_read']
        self.assertEqual(len(r['scenes']), 3)
        for row in r['scenes']:
            self.assertIn('story', row['notes'])
            self.assertIn('structure', row['notes'])
            self.assertIn('character', row['notes'])
        self.assertIn('script_story', r['norm_metrics'])
        # no sluglines: paragraphs are the scenes
        self.assertGreaterEqual(M.script_scenes('A short pitch.\n\nAnother paragraph.')['scene_count'], 2)
        self.assertTrue(M.accept_text_name('script.pdf'))
        self.assertEqual(M.text_from_upload(b'hello world', 'a.txt'), 'hello world')

    def test_concept_read_has_battery(self):
        twin, sess = _pilot(mode='concept', text=LOGLINE + ' Her ex runs the bank that holds the note.', in_room=10)
        PI.prepare_watch(sess, {'jb': twin}, title='Second Helping')
        r = sess['mode_read']
        for k in ('definitely_interested_pct', 'unique_pct', 'who_for', 'would_watch_pct', 'strongest', 'doubt', 'quadrants', 'read'):
            self.assertIn(k, r)
        self.assertEqual(r['strongest'], sorted(r['strongest'], key=lambda x: -x['pct']))
        self.assertEqual(r['doubt'], sorted(r['doubt'], key=lambda x: -x['pct']))
        self.assertEqual(sess['format'], 'concept')
        self.assertFalse(_no_banned(r))

    def test_episode_knows_the_prior_cut_and_binge_builds(self):
        twin, a = _pilot(title='Pilot', in_room=8)
        PI.prepare_watch(a, {'jb': twin}, duration_s=300, title='Episode 1')
        b = PI.open_session([twin], title='Episode 2', in_room=8, mode='episode')
        b['prior_read'] = {'id': a['id'], 'title': a['title'], 'scorecard': a['scorecard'], 'seat_dials': a['seat_dials'], 'frame': 'the room watched episode 1'}
        PI.prepare_watch(b, {'jb': twin}, duration_s=300, title='Episode 2')
        r = b['mode_read']
        self.assertEqual(r['mode'], 'episode')
        self.assertIsNotNone(r['commitment_pct'])
        self.assertEqual(r['prior']['session_id'], a['id'])
        self.assertIsNotNone(r['tune_in_delta_pp'])
        reads = [{'label': 'Ep 1', 'scorecard': a['scorecard'], 'dial': a['dial']['value']}, {'label': 'Ep 2', 'scorecard': b['scorecard'], 'dial': b['dial']['value']}]
        bg = M.binge_read(b, reads)
        self.assertEqual(len(bg['episodes']), 2)
        self.assertEqual(len(bg['cumulative_tune_in']), 2)
        self.assertTrue(bg['read'])

    def test_rough_cut_skips_marketing_locked_runs_it(self):
        twin, rough = _pilot(title='Rough', in_room=8, rough_cut=True)
        PI.prepare_watch(rough, {'jb': twin}, duration_s=300, title='Rough cut')
        self.assertNotIn('marketing_read', rough)
        twin, locked = _pilot(title='Locked', in_room=8)
        PI.prepare_watch(locked, {'jb': twin}, duration_s=300, title='Locked cut')
        mk = locked['marketing_read']
        self.assertIn('who_for', mk)
        self.assertIn('trailer_moments', mk)
        self.assertTrue(mk['read'])

    def test_set_stimulus_and_options_patch(self):
        twin, sess = _pilot(mode='title', in_room=6)
        ch = PI.set_options(sess, {'options': ['A', 'B', 'C', 'D', 'E', 'F', 'G'], 'logline': LOGLINE, 'cast': ['Mae', 'Ray'], 'critics_room': True})
        self.assertEqual(ch['options'], 6)
        self.assertTrue(ch['logline'])
        self.assertEqual(ch['cast'], 2)
        self.assertTrue(sess['critics_room'])
        self.assertEqual([o['id'] for o in sess['options']], list('abcdef'))

    def test_graded_shares_are_not_unanimous(self):
        rows = M.ordered_probs(60.0, M.CUTS['interest4'], list('abcd'))
        self.assertAlmostEqual(sum(rows.values()), 1.0, places=6)
        self.assertGreater(rows['a'], 0.4)
        self.assertGreater(rows['b'], 0.1)
        young = _young()
        sess = PI.open_session([young], title='Young', in_room=12, mode='trailer', logline=LOGLINE)
        PI.prepare_watch(sess, {'ym': young}, duration_s=80, title='An action trailer')
        r = sess['mode_read']
        self.assertGreater(r['interest_after_pct'], 1.0)
        self.assertLess(r['interest_after_pct'], 99.0)


class Diagnostics(unittest.TestCase):
    def _pilot_with_cast(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Pilot', in_room=12)
        sess['cast'] = ['Mae', 'Ray', 'Lorelai']
        PI.prepare_watch(sess, {'jb': twin}, duration_s=600, title='A comedy pilot', transcript=TRANSCRIPT)
        return twin, sess

    def test_character_table_from_transcript_with_norms(self):
        twin, sess = self._pilot_with_cast()
        d = sess['diagnostics']['characters']
        names = [c['name'] for c in d['characters']]
        self.assertIn('Mae', names)
        self.assertIn('Ray', names)
        self.assertIn('Mayor', names)
        lead = d['characters'][0]
        self.assertEqual(lead['role'], 'lead')
        self.assertIsNotNone(lead['excellent_pct'])
        self.assertEqual(lead['norm'], 50.0)
        self.assertEqual(d['characters'][1]['norm'], 45.0)
        self.assertTrue(d['relationship'])
        self.assertTrue(d['follow'])
        self.assertTrue(d['read'])
        for c in d['characters']:
            self.assertGreater(c['excellent_pct'], 0.0)
            self.assertLess(c['excellent_pct'], 100.0)
        self.assertFalse(_no_banned(d))

    def test_pace_confusion_ending_present(self):
        twin, sess = self._pilot_with_cast()
        p = sess['diagnostics']['pace']
        self.assertEqual([a['act'] for a in p['acts']], ['beginning', 'middle', 'turn', 'ending'])
        for a in p['acts']:
            self.assertAlmostEqual((a['too_slow_pct'] or 0) + (a['just_right_pct'] or 0) + (a['too_fast_pct'] or 0), 100.0, delta=0.6)
        self.assertIn('confusion_pct', p)
        self.assertIn('confusions', p)
        self.assertIsNotNone(p['beginning']['excellent_pct'])
        self.assertIsNotNone(p['ending']['excellent_pct'])
        self.assertTrue(p['running_time_read'])
        self.assertTrue(p['read'])

    def test_scenes_liked_best_and_least_with_counts_and_quotes(self):
        twin, sess = self._pilot_with_cast()
        s = sess['diagnostics']['scenes']
        self.assertTrue(s['liked_best'])
        self.assertTrue(s['liked_least'])
        top = s['liked_best'][0]
        self.assertGreaterEqual(top['mentions'], 1)
        self.assertTrue(top['quotes'])
        self.assertTrue(top['quotes'][0]['line'])
        self.assertEqual(s['liked_best'], sorted(s['liked_best'], key=lambda x: -x['mentions']))
        self.assertIn('not_recommending', s)
        self.assertIn('similar_titles', s)
        self.assertTrue(s['appeal']['line'].startswith('Plays as a title for'))

    def test_components_with_norms(self):
        twin, sess = self._pilot_with_cast()
        c = sess['diagnostics']['components']
        keys = [r['key'] for r in c['rows']]
        self.assertEqual(keys, ['humor', 'story', 'beginning', 'ending', 'music', 'settings', 'action', 'suspense'])
        humor = c['rows'][0]
        self.assertEqual(humor['norm'], 40.0)
        self.assertIsNotNone(humor['excellent_pct'])
        nm = PI.norm_metrics(sess)
        self.assertIn('component_humor', nm)
        self.assertIn('lead_character', nm)
        self.assertIn('component_pace_just_right', nm)

    def test_diagnostics_lines_clean(self):
        twin, sess = self._pilot_with_cast()
        lines = D.diagnostics_lines(sess)
        self.assertGreaterEqual(len(lines), 3)
        for l in lines:
            self.assertFalse(_no_banned(l), l)
            self.assertNotIn('\u2014', l)


class Pricing(unittest.TestCase):
    def test_charge_due_per_mode(self):
        twin = _beals()
        want = {'pilot': 'digital_twin_pilot', 'feature': 'digital_twin_pilot', 'episode': 'digital_twin_pilot', 'binge': 'digital_twin_pilot', 'trailer': 'digital_twin_trailer',
                'spot': 'digital_twin_trailer', 'title': 'digital_twin_title', 'key_art': 'digital_twin_key_art', 'concept': 'digital_twin_concept', 'script': 'digital_twin_script'}
        for mode, key in want.items():
            sess = PI.open_session([twin], title='t', in_room=12, mode=mode)
            self.assertEqual(PI.charge_due(sess), key, mode)
            self.assertEqual(DT.PRICING[key], {'digital_twin_pilot': 5500.0, 'digital_twin_trailer': 2500.0, 'digital_twin_title': 1500.0, 'digital_twin_key_art': 1500.0,
                                               'digital_twin_concept': 2500.0, 'digital_twin_script': 3500.0}[key])
            PI.mark_play_paid(sess)
            self.assertIsNone(PI.charge_due(sess))
            sess['questions'] = [{'q': str(i)} for i in range(12)]
            self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot_more')
        recut = PI.open_session([twin], title='Cut B', in_room=12)
        recut['lineage'] = {'parent': 'abc', 'label': 'Cut B'}
        self.assertEqual(PI.charge_due(recut), 'digital_twin_recut')
        self.assertEqual(DT.PRICING['digital_twin_recut'], 2750.0)
        for k in ('digital_twin_trailer', 'digital_twin_title', 'digital_twin_key_art', 'digital_twin_concept', 'digital_twin_script', 'digital_twin_recut'):
            self.assertIn(k, DT.PULL_TYPES)

    def test_how_to_items_for_every_tab(self):
        ids = [it['id'] for it in DT.how_to()['items']]
        for tab in ('pilot', 'trailer', 'titles', 'script', 'concept', 'norms'):
            self.assertIn(tab, ids)
        text = json.dumps(DT.how_to(), default=str).lower()
        for tok in ('screen engine', 'nrg', 'marketcast', 'fishdog', 'synth', 'claude', 'pipeline', '\u2014'):
            self.assertNotIn(tok, text)
        self.assertIn('Trailers', DT.how_to_text('how do I run a trailer test'))
        self.assertIn('Norms', DT.how_to_text('what is the norms tab'))

    def test_rate_card_carries_the_modes(self):
        from prometheus import rate_card as RC
        text = RC.render()
        for frag in ('Trailer or TV spot test', 'Title or logline test', 'Key art test', 'Concept test', 'Script read', 'Recut re-test'):
            self.assertIn(frag, text)
        self.assertEqual(RC.default('digital_twin_trailer'), 2500.0)
        self.assertNotIn('\u2014', text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
