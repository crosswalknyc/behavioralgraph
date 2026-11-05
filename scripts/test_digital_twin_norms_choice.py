#!/usr/bin/env python3
"""Pilot Testing: the user sets the norm (Jenna 2026-11-04, "let's change
it to where you set what norm to compare it to"). Hermetic.

Covers: procedural is a genre everywhere it must be; the words and the
frames' vote pre-fill the choice; a user's choice wins over the frames;
the norm route switches the norm on a stopped test and re-reads the
norms (re-filing the test in the ledger); the honest 'No norm yet' copy
when a genre has no anchor and under five tests; seeded cells carry
sources that never reach a page; labels name the chosen norm on the
card, the CSV, the report and the page.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import digital_twin_norms as N  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_watch as W  # noqa: E402
import test_digital_twin_pilot as TP  # noqa: E402  (the twin fixture and the fake cut)


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


def _ledger(s3):
    return json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))


def _scored(kind='tense', sid='ncis-ny-1', title='NCIS: New York, episode 1'):
    """A stopped, scored test the way the room leaves one."""
    twin = TP._beals()
    sess = PI.open_session([twin], title=title, in_room=12, session_id=sid)
    PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=300, title=title, analysis=TP._fake_cut(kind))
    PI.stop_session(sess)
    return twin, sess


class Procedural(unittest.TestCase):
    def test_procedural_is_a_genre_everywhere(self):
        self.assertIn('procedural', N.GENRES)
        self.assertEqual(N.GENRE_LABEL['procedural'], 'Procedural')
        self.assertIn('procedural', W.GENRES)                       # the frames can vote it
        self.assertIn('procedural', W.CAPTION_SYSTEM)                # the model is told the list
        self.assertEqual(N._genre('procedural'), 'procedural')
        self.assertEqual(N._genre('cop show'), 'procedural')
        self.assertEqual(N._genre('police procedural'), 'procedural')
        self.assertEqual(N._genre('prestige drama'), 'drama')
        # the Compare-to list runs in the order Jenna gave, the all-genres column first
        self.assertEqual([o['value'] for o in N.norm_options('pilot')][:4], ['', 'procedural', 'drama', 'comedy'])
        self.assertEqual(N.norm_options('pilot')[0]['label'], 'All pilots')

    def test_words_pre_fill_the_choice(self):
        self.assertEqual(PI.keyword_genre('NCIS: New York, episode 1'), 'procedural')
        self.assertEqual(PI.keyword_genre('CSI reboot pilot'), 'procedural')
        self.assertEqual(PI.keyword_genre('Law & Order: Organized Crime'), 'procedural')
        self.assertEqual(PI.keyword_genre('A police drama set in Baltimore'), 'procedural')
        self.assertEqual(PI.keyword_genre('a homicide detective returns'), 'procedural')
        self.assertEqual(PI.keyword_genre('Second Helping', 'a prestige drama about a diner'), 'drama')
        self.assertEqual(PI.keyword_genre('Dramatic series, season 2'), 'drama')
        self.assertEqual(PI.keyword_genre('Roommates', 'a sitcom'), 'comedy')
        self.assertEqual(PI.keyword_genre('Untitled cut'), '')
        self.assertEqual(PI.norms_genre('NCIS: New York'), 'procedural')
        # the room's own vote wins over the words
        self.assertEqual(PI.norms_genre('NCIS: New York', votes={'procedural': 4, 'drama': 1}), 'procedural')
        self.assertEqual(PI.norms_genre('Untitled', votes={'drama': 2, 'procedural': 1}), 'drama')

    def test_the_frames_vote_pre_fills_unless_the_user_chose(self):
        twin = TP._beals()
        an = TP._fake_cut('tense')
        an['genre_votes'] = {'procedural': 3}
        sess = PI.open_session([twin], title='Untitled cut', in_room=12, session_id='vote-1')
        self.assertEqual((sess['genre'], sess['norm_genre'], sess['genre_set_by_user']), ('', '', False))
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=300, title='Untitled cut', analysis=an)
        self.assertEqual((sess['genre'], sess['norm_genre']), ('procedural', 'procedural'))
        self.assertFalse(sess['genre_set_by_user'])
        self.assertEqual(sess['scorecard']['norm_choice']['label'], 'Procedural pilots')
        # the user set it at setup: the frames never override
        sess2 = PI.open_session([twin], title='Untitled cut', in_room=12, session_id='vote-2')
        PI.set_norm(sess2, 'comedy', by_user=True)
        self.assertTrue(sess2['genre_set_by_user'])
        PI.prepare_watch(sess2, {twin['meta']['key']: twin}, duration_s=300, title='Untitled cut', analysis=dict(an, genre_votes={'thriller': 5}))
        self.assertEqual((sess2['genre'], sess2['norm_genre']), ('comedy', 'comedy'))
        self.assertEqual(sess2['scorecard']['genre'], 'comedy')
        self.assertEqual(sess2['scorecard']['norm_choice']['label'], 'Comedy pilots')
        # an untouched pre-fill at setup (the form's guess) keeps following the frames
        sess3 = PI.open_session([twin], title='Untitled cut', in_room=12, session_id='vote-3')
        PI.set_norm(sess3, 'drama', by_user=False)
        self.assertFalse(sess3['genre_set_by_user'])
        PI.prepare_watch(sess3, {twin['meta']['key']: twin}, duration_s=300, title='Untitled cut', analysis=an)
        self.assertEqual(sess3['genre'], 'procedural')
        # choosing the all-genres column on purpose is a choice too
        sess4 = PI.open_session([twin], title='NCIS: New York', in_room=12, session_id='vote-4')
        PI.set_norm(sess4, '', by_user=True)
        PI.prepare_watch(sess4, {twin['meta']['key']: twin}, duration_s=300, title='NCIS: New York', analysis=an)
        self.assertEqual(sess4['genre'], '')
        self.assertEqual(sess4['scorecard']['norm_choice']['label'], 'All pilots')


class NoNormYet(unittest.TestCase):
    def test_a_genre_without_an_anchor_reads_no_norm_yet_until_five_tests(self):
        s3 = FakeS3()
        N.seed(s3)
        n = N.norms_for('pilot', 'procedural', s3=s3)
        self.assertEqual(n['genre_used'], 'all')
        # the pilot column itself is a feature-screening stand-in until five pilot tests sit in it (Liz Huszarik 2026-11-05), so the
        # card is compared to feature screenings and says so, once, in the same words as the genre line
        self.assertEqual(n['choice'], {'genre': 'procedural', 'label': 'Procedural pilots', 'all_label': 'All pilots', 'has_genre_norm': False,
                                       'own_tests': 0, 'needed': N.MIN_OWN_TESTS, 'stand_in': 'feature screening',
                                       'compared_label': 'Feature screenings', 'compared_line': 'Compared to feature screenings.',
                                       'notes': ['No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far.',
                                                 'No procedural norm yet; it fills as tests are filed under procedural, 0 of 5 so far.'],
                                       'note': 'No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far. No procedural norm yet; it fills as tests are filed under procedural, 0 of 5 so far.'})
        for i in range(N.MIN_OWN_TESTS - 1):
            N.record(s3, 'pilot', 'procedural', {'total_positive': 60.0 + i, 'excellent': 30.0 + i}, f'p{i}')
        n = N.norms_for('pilot', 'procedural', s3=s3)
        self.assertEqual(n['genre_used'], 'all')                      # four tests borrowing the all-pilots anchor are not a procedural norm
        self.assertEqual(n['choice']['own_tests'], N.MIN_OWN_TESTS - 1)
        self.assertEqual(n['choice']['notes'][-1], 'No procedural norm yet; it fills as tests are filed under procedural, 4 of 5 so far.')
        self.assertEqual(n['genre']['total_positive']['genre_used'], 'all')
        N.record(s3, 'pilot', 'procedural', {'total_positive': 64.0, 'excellent': 34.0}, 'p-last')
        n = N.norms_for('pilot', 'procedural', s3=s3)
        self.assertEqual(n['genre_used'], 'procedural')
        self.assertTrue(n['choice']['has_genre_norm'])
        self.assertEqual(n['choice']['note'], '')
        self.assertEqual(n['choice']['notes'], [])
        self.assertEqual(n['choice']['compared_label'], 'Procedural pilots')
        self.assertEqual(n['choice']['compared_line'], 'Compared to procedural pilots.')
        self.assertEqual(n['genre']['total_positive']['n'], N.MIN_OWN_TESTS)
        # five pilot tests also sit in the all-pilots column now: it is a pilot norm, read at the running mean
        a = N.norms_for('pilot', 'all', s3=s3)
        self.assertEqual(a['stand_in'], '')
        self.assertEqual(a['choice'], {'genre': '', 'label': 'All pilots', 'all_label': 'All pilots', 'has_genre_norm': False,
                                       'own_tests': 0, 'needed': N.MIN_OWN_TESTS, 'stand_in': '', 'compared_label': 'All pilots',
                                       'compared_line': 'Compared to all pilots.', 'notes': [], 'note': ''})
        # a genre with a published anchor of its own reads as a norm from the first look
        f = N.norms_for('feature', 'family', s3=s3)
        self.assertTrue(f['choice']['has_genre_norm'])
        self.assertEqual(f['choice']['label'], 'Family features')
        self.assertEqual(f['choice']['compared_label'], 'Family features')

    def test_the_card_carries_the_choice_and_the_note(self):
        twin, sess = _scored('tense', sid='card-1', title='NCIS: New York, episode 1')
        sc = sess['scorecard']
        self.assertEqual(sess['genre'], 'thriller')                 # the fake frames vote thriller; the words would say procedural
        PI.set_norm(sess, 'procedural', by_user=True)
        sc = sess['scorecard']
        self.assertEqual(sc['norm_choice']['label'], 'Procedural pilots')
        self.assertFalse(sc['norm_choice']['has_genre_norm'])
        self.assertEqual(sc['norm_choice']['compared_label'], 'Feature screenings')
        self.assertEqual(sc['norm_choice']['notes'], ['No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far.',
                                                      'No procedural norm yet; it fills as tests are filed under procedural, 0 of 5 so far.'])
        for note in sc['norm_choice']['notes']:
            self.assertEqual(sc['norm_lines'].count(note), 1)      # each honest line once
        self.assertTrue(all(not c['genre_own'] for c in sc['norm_columns'].values()))
        self.assertTrue(any('the feature screening norm of' in l for l in sc['norm_lines']))
        self.assertFalse(any('all-pilots norm' in l for l in sc['norm_lines']), 'a feature figure is never called the pilot norm')
        self.assertEqual(sc['norm_columns']['definitely_recommend']['stand_in'], 'feature screening')
        self.assertEqual(sc['norm_columns']['definitely_recommend']['norm'], 50.0)
        pub = PI.public_session(sess)
        blob = json.dumps(pub['scorecard'].get('norms') or {}) + json.dumps(pub['scorecard'].get('norm_choice') or {})
        self.assertNotIn('sources', blob)
        self.assertNotIn('wikileaks', blob)
        for l in sc['norm_lines']:
            self.assertFalse(PI.has_banned_copy(l), l)
            self.assertNotIn('\u2014', l)
        # the CSV names the norms the same way and leaves the procedural column blank until there is one
        text = PI.session_csv(sess)
        self.assertIn('Feature screening norm %', text)
        self.assertIn('Procedural pilots norm %', text)
        self.assertIn('No procedural norm yet; it fills as tests are filed under procedural, 0 of 5 so far.', text)
        self.assertIn(',Feature screenings,', text)                 # the Compared to cell
        self.assertNotIn('All pilots norm', text)
        self.assertNotIn('Procedural pilots norm,,,', text)       # no genre-norm row while there is no genre norm

    def test_seeded_cells_carry_sources_and_never_show_them(self):
        for c in N.SEED_CELLS:
            self.assertTrue(c.get('sources'), c)
            for s in c['sources']:
                self.assertTrue(str(s.get('url') or '').startswith('http'))
            # a genre cell is only ever seeded from a published anchor for that genre
            if c['genre'] != 'all':
                self.assertNotIn('all genres anchor', str(c.get('basis') or ''))
        s3 = FakeS3()
        N.seed(s3)
        doc = _ledger(s3)
        for k, cell in doc['cells'].items():
            if k.split('|')[1] != 'all':
                self.assertTrue(N._own_anchor(cell, k.split('|')[1]), k)
        n = N.norms_for('feature', 'family', s3=s3)
        self.assertNotIn('sources', json.dumps(n))
        self.assertNotIn('note', json.dumps(n['genre']))


class Route(unittest.TestCase):
    KEY = 'digital-twins/jenna/Jennifer_Beals_20261028_120000_abc123def0.json.gz'

    def _client(self, s3, user=None):
        try:
            import flask  # noqa: F401
        except Exception:
            self.skipTest('flask not installed in this interpreter')
        import types
        import digital_twins_api as A
        from prometheus.host import host
        s3.list_objects_v2 = lambda **kw: {'Contents': [{'Key': k, 'LastModified': '2026'} for k in list(s3.objs) if k.startswith(kw.get('Prefix', ''))]}
        user = user or {'username': 'jenna', 'role': 'super_admin', 'has_digital_twins_iq_access': True}
        fake_app = types.SimpleNamespace(s3_client=s3, get_current_user=lambda: user, _normalize_role=lambda r: r, consume_credit=lambda *a, **k: True)
        sys.modules['app'] = fake_app
        host.bind(gate=lambda allow_api_key=False: (user, None))
        app = flask.Flask('t')
        app.secret_key = 'x'
        A.register(app)
        return app.test_client()

    def test_the_norm_route_switches_a_stopped_test_without_a_rerun(self):
        from migration import digital_twin_synthesis as SYN
        s3 = FakeS3()
        N.seed(s3)
        twin, sess = _scored('tense', sid='ncis-route', title='NCIS: New York, episode 1')
        PI.record_norms(sess, s3=s3)                                  # the stop route files the test under its genre (thriller, the frames' vote)
        self.assertIn('ncis-route', _ledger(s3)['cells'][N.cell_key('pilot', 'thriller', 'total_positive')]['tests'])
        payload = {'meta': {'name': 'Jennifer Beals', 'subject': 'Jennifer Beals', 'created_by': 'jenna', 'size_us': 9339205, 'key': self.KEY},
                   'pilot_sessions': [sess], 'runs': []}
        SYN.save_twin(s3, self.KEY, payload)
        c = self._client(s3)
        before_dial = list(sess['dial']['value'])
        before_head = dict(sess['scorecard']['battery']['headline'])
        r = c.post(f'/api/digital-twins/{self.KEY}/pilot/ncis-route/norm', json={'genre': 'procedural'})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        out = r.get_json()
        s = out['session']
        self.assertEqual((s['genre'], s['norm_genre'], s['genre_set_by_user'], s['status']), ('procedural', 'procedural', True, 'stopped'))
        self.assertEqual(s['scorecard']['genre'], 'procedural')
        self.assertEqual(s['scorecard']['norm_choice']['label'], 'Procedural pilots')
        self.assertEqual(out['compared_to']['label'], 'Procedural pilots')
        self.assertEqual(s['scorecard']['norms']['genre_key'], 'procedural')
        self.assertEqual(s['scorecard']['norm_choice']['notes'][-1], 'No procedural norm yet; it fills as tests are filed under procedural, 1 of 5 so far.')   # this test now counts as a procedural
        self.assertEqual(out['compared_to']['compared_label'], 'Feature screenings')   # the pilot column is still a feature-screening stand-in
        self.assertEqual(s['dial']['value'], before_dial)              # nothing re-ran
        self.assertEqual(s['scorecard']['battery']['headline'], before_head)
        self.assertNotIn('sources', json.dumps(s['scorecard']['norms']))
        # the ledger: the test left the thriller cells and sits in the procedural ones; the all-pilots cells kept it once
        doc = _ledger(s3)
        self.assertIn('ncis-route', doc['cells'][N.cell_key('pilot', 'procedural', 'total_positive')]['tests'])
        self.assertNotIn('ncis-route', doc['cells'][N.cell_key('pilot', 'thriller', 'total_positive')]['tests'])
        self.assertEqual(doc['cells'][N.cell_key('pilot', 'all', 'total_positive')]['tests'].count('ncis-route'), 1)
        # saved on the twin
        stored = SYN.load_twin(s3, self.KEY)['pilot_sessions'][0]
        self.assertEqual((stored['genre'], stored['genre_set_by_user']), ('procedural', True))
        # back to the all-genres column, on purpose
        r2 = c.post(f'/api/digital-twins/{self.KEY}/pilot/ncis-route/norm', json={'genre': ''})
        s2 = r2.get_json()['session']
        self.assertEqual((s2['genre'], s2['genre_set_by_user']), ('', True))
        self.assertEqual(s2['scorecard']['norm_choice']['label'], 'All pilots')
        self.assertEqual(s2['scorecard']['norm_choice']['notes'], ['No pilot norm yet; it fills as pilot tests are filed, 1 of 5 so far.'])
        self.assertNotIn('ncis-route', _ledger(s3)['cells'][N.cell_key('pilot', 'procedural', 'total_positive')]['tests'])
        # a word that is not a norm is refused with the list
        r3 = c.post(f'/api/digital-twins/{self.KEY}/pilot/ncis-route/norm', json={'genre': 'western'})
        self.assertEqual(r3.status_code, 400)
        self.assertEqual(r3.get_json()['options'][0]['label'], 'All pilots')
        self.assertEqual(c.post(f'/api/digital-twins/{self.KEY}/pilot/nope/norm', json={'genre': 'drama'}).status_code, 404)

    def test_the_setup_form_choice_rides_on_open(self):
        from migration import digital_twin_synthesis as SYN
        s3 = FakeS3()
        payload = {'meta': {'name': 'Jennifer Beals', 'subject': 'Jennifer Beals', 'created_by': 'jenna', 'size_us': 9339205, 'key': self.KEY},
                   'pilot_sessions': [], 'runs': [], 'identity': TP._beals()['identity'], 'traits': TP._beals()['traits']}
        SYN.save_twin(s3, self.KEY, payload)
        c = self._client(s3)
        r = c.post(f'/api/digital-twins/{self.KEY}/pilot/open', json={'title': 'NCIS: New York, episode 1', 'in_room': 6, 'mode': 'pilot', 'genre': 'procedural', 'genre_set_by_user': True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        s = r.get_json()['session']
        self.assertEqual((s['genre'], s['norm_genre'], s['genre_set_by_user']), ('procedural', 'procedural', True))
        # a guess the person left alone is not theirs yet
        r2 = c.post(f'/api/digital-twins/{self.KEY}/pilot/open', json={'title': 'Second cut', 'in_room': 6, 'mode': 'pilot', 'genre': 'drama', 'genre_set_by_user': False})
        s2 = r2.get_json()['session']
        self.assertEqual((s2['genre'], s2['genre_set_by_user']), ('drama', False))


class Labels(unittest.TestCase):
    def test_choice_labels_name_the_norm(self):
        self.assertEqual(N.choice_label('pilot', 'procedural'), 'Procedural pilots')
        self.assertEqual(N.choice_label('pilot', ''), 'All pilots')
        self.assertEqual(N.choice_label('episode', 'drama'), 'Drama episodes')
        self.assertEqual(N.choice_label('binge', 'all'), 'All episodes')
        self.assertEqual(N.choice_label('spot', 'comedy'), 'Comedy trailers')
        self.assertEqual(N.choice_label('key_art', ''), 'All key art')
        self.assertEqual(N.choice_label('feature', 'family'), 'Family features')

    def test_norm_lines_name_the_chosen_norm(self):
        s3 = FakeS3()
        N.seed(s3)
        for i in range(N.MIN_OWN_TESTS):
            N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 50.0 + i, 'total_positive': 64.0 + i}, f'pr{i}')
        n = N.norms_for('pilot', 'procedural', s3=s3)
        self.assertEqual(n['genre_used'], 'procedural')
        line = N.norm_line('definitely_recommend', 54.0, n, 'pilot', 'procedural')
        self.assertRegex(line, r'^Definitely recommend 54\.0%, (two|three) points above the procedural pilot norm of 5\d%\.$')
        self.assertEqual(N.norm_line('total_positive', 65.6, n, 'pilot', 'procedural'), 'Total positive 65.6%, right at the procedural pilot norm of 65%.')
        plain = N.norm_line('excellent', 33.0, n, 'pilot', 'procedural')       # excellent has no procedural cell: the all column, which is still a feature stand-in, named for what it is
        self.assertEqual(plain, 'Excellent 33.0%, three points above the feature screening norm of 30%.')

    def test_the_report_names_the_norm_once(self):
        """Never 'Pilot, procedural' next to an all-pilots footnote (Liz
        Huszarik 2026-11-05): the subhead and every norm line name the
        norm the card is actually read against, from one helper, and the
        honest lines appear once."""
        from migration import digital_twin_report as R
        twin, sess = _scored('tense', sid='rep-1', title='NCIS: New York, episode 1')
        PI.set_norm(sess, 'procedural', by_user=True)
        payload = {'meta': {'name': 'Jennifer Beals', 'subject': 'Jennifer Beals'}}
        pages = R.report_pages(payload, sess)
        table = [p for p in pages if p.get('kind') == 'table' and p.get('header') == 'Every question on the card, next to its norm.'][0]
        self.assertEqual(table['sub'], 'Compared to feature screenings.')
        tiles = next(p for p in pages if p['kind'] == 'tiles')
        self.assertTrue(tiles['sub'].endswith(' Compared to feature screenings.'), tiles['sub'])
        self.assertNotIn('Pilot, procedural', tiles['sub'])
        self.assertNotIn('Procedural pilots', json.dumps([p['sub'] for p in pages]))
        blob = json.dumps([{k: v for k, v in p.items() if k != 'image'} for p in pages], default=str)
        self.assertEqual(blob.count('Compared to feature screenings.'), 2, 'once on the scorecard page, once over the card table')
        self.assertEqual(blob.count('No procedural norm yet; it fills as tests are filed under procedural, 0 of 5 so far.'), 1)   # not filed in a ledger here
        self.assertEqual(blob.count('No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far.'), 1)
        self.assertNotIn('all-pilots norm', blob)
        self.assertNotIn('wikileaks', blob)
        self.assertNotIn('\u2014', blob)
        # an older card with no norm_choice is read from its norm columns: no genre norm, a stand-in all column
        old = dict(sess, scorecard={k: v for k, v in sess['scorecard'].items() if k != 'norm_choice'})
        self.assertEqual(R._compared_to(old['scorecard'], old), 'Compared to feature screenings.')
        # once the genre has a norm of its own the subhead names it and the lines use it
        s3 = FakeS3()
        N.seed(s3)
        for i in range(N.MIN_OWN_TESTS):
            N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 30.0 + i, 'total_positive': 50.0 + i, 'excellent': 10.0 + i}, f'pr{i}')
        PI.attach_norms(sess['scorecard'], 'pilot', 'procedural', s3=s3)
        pages = R.report_pages(payload, sess)
        table = [p for p in pages if p.get('kind') == 'table' and p.get('header') == 'Every question on the card, next to its norm.'][0]
        self.assertEqual(table['sub'], 'Compared to procedural pilots.')
        self.assertEqual(table['body'], [])
        self.assertTrue(any('the procedural pilot norm of' in l for l in sess['scorecard']['norm_lines']), sess['scorecard']['norm_lines'])

    def test_the_page_carries_the_compare_to_field_and_the_route(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        m = re.search(r"var PI_GENRES = \[([^\]]*)\];", html)
        self.assertIsNotNone(m)
        page_genres = [x.strip().strip("'") for x in m.group(1).split(',')]
        self.assertEqual(page_genres, [g for g in N.GENRES if g != 'all'])          # the page's list is the ledger's
        self.assertIn('id="dtwPiNorm"', html)                                        # the Compare-to field on the setup form
        self.assertIn('>Compare to</label>', html)
        self.assertIn("'Compared to: ' + esc(piComparedLabel(piNormChoice(s)))", html)   # the dial header names the norm the card is read against
        self.assertIn("function piComparedLabel(ch)", html)
        self.assertIn("function piDenominator(s, sc)", html)                          # what the shares are of, on the card
        self.assertIn("function piTallies(bat)", html)                                # the seat tally beside every money share
        self.assertIn("', no norm yet'", html)                                        # would finish never shows a blank norm
        self.assertNotIn("Read against ' + esc(String(ch.all_label", html)            # the old 'read against all pilots until then' line is gone
        self.assertIn("/norm', { method: 'POST'", html)                              # the finished card switches through the norm route
        self.assertIn('body.genre_set_by_user = ', html)
        for word in ('panel', 'synthetic', 'estimated', 'pipeline'):
            self.assertNotIn(word, 'Every score is read against this norm. The room suggests one once it has watched the cut; you can change it any time.')
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn("/pilot/<sid>/norm'", api)
        self.assertIn('PI.set_norm(', api)


if __name__ == '__main__':
    unittest.main(verbosity=1)
