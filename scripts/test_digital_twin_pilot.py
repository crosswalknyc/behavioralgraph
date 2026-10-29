#!/usr/bin/env python3
"""Pilot Testing: dial, events, scorecard, questions on the clock (2026-10-28).
Hermetic. No ffmpeg required for the math tests.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


def _beals(name='Jennifer Beals', key='jb'):
    return {
        'meta': {'name': name, 'key': key, 'subject': name,
                 'profile_key': 'Jennifer_Beals_06_17_2026_17_09.csv'},
        'identity': {'demographics': {
            'gender': [{'label': 'Female', 'pct': 72.3516}, {'label': 'Male', 'pct': 24.0352},
                       {'label': 'Non-Binary', 'pct': 2.6698}],
            'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 62.3226},
                                   {'label': 'LGBTQ+', 'pct': 33.3786}],
            'education': [{'label': 'Bachelors Degree', 'pct': 33.4009},
                          {'label': 'Some College', 'pct': 22.1124},
                          {'label': 'High School or Less', 'pct': 18.8934},
                          {'label': 'Graduate or Professional Degree', 'pct': 25.5933}],
            'occupation': [{'label': 'Management/Business/Professional', 'pct': 36.0779},
                           {'label': 'Healthcare', 'pct': 13.0412}, {'label': 'Service', 'pct': 11.5613},
                           {'label': 'Education/Library', 'pct': 10.0934}, {'label': 'Sales', 'pct': 8.1123}],
            'age': [{'label': '55-64', 'pct': 25.0213}, {'label': '45-54', 'pct': 22.1124},
                    {'label': '35-44', 'pct': 18.4012}, {'label': '65+', 'pct': 16.3012},
                    {'label': '25-34', 'pct': 12.1012}, {'label': '18-24', 'pct': 6.0627}],
        }},
        'traits': {
            'franchise_loyalty': {'value': 71.4, 'label': 'loyalty', 'read': ''},
            'second_screen': {'value': 38.2, 'label': 'second', 'read': ''},
            'binge_vs_appointment': {'value': 62.1, 'label': 'binge', 'read': ''},
            'word_of_mouth': {'value': 64.8, 'label': 'wom', 'read': ''},
            'trial_speed': {'value': 44.2, 'label': 'trial', 'read': ''},
        },
        'lifecycle': {'habit_pct': 48.2, 'discovering_pct': 12.1, 'trying_pct': 10.4,
                      'lapsing_pct': 16.3, 'returning_pct': 13.0},
    }


def _young(name='Young Men Cut', key='ym'):
    t = _beals(name, key)
    t['identity']['demographics']['gender'] = [
        {'label': 'Male', 'pct': 78.2012}, {'label': 'Female', 'pct': 21.7988}]
    t['identity']['demographics']['age'] = [
        {'label': '18-24', 'pct': 41.2012}, {'label': '25-34', 'pct': 38.1124},
        {'label': '35-44', 'pct': 20.6864}]
    t['traits']['franchise_loyalty'] = {'value': 31.7, 'label': 'loyalty', 'read': ''}
    t['traits']['second_screen'] = {'value': 78.4, 'label': 'second', 'read': ''}
    t['lifecycle'] = {'habit_pct': 14.2, 'discovering_pct': 38.1, 'trying_pct': 22.4,
                      'lapsing_pct': 18.3, 'returning_pct': 7.0}
    return t


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
        return {'Body': _Body(self.objs[Key]), 'ContentType': 'application/json', 'ContentLength': len(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


class Session(unittest.TestCase):
    def test_open_is_draft_and_free(self):
        sess = PI.open_session([_beals()], title='Season 2 pilot', in_room=12, created_by='jenna')
        self.assertEqual(sess['kind'], 'pilot')
        self.assertEqual(sess['status'], 'draft')
        self.assertFalse(sess['play_charged'])
        self.assertEqual(sess['questions_paid'], 0)
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot')
        self.assertEqual(len(sess['group']['roster']), 12)

    def test_prepare_dial_matches_duration(self):
        twin = _beals()
        sess = PI.open_session([twin], title='NCIS New York pilot', in_room=8)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='NCIS: New York pilot')
        self.assertEqual(sess['status'], 'ready')
        ts = sess['dial']['t']
        vals = sess['dial']['value']
        self.assertEqual(len(ts), len(vals))
        self.assertGreaterEqual(len(vals), 8)
        self.assertAlmostEqual(ts[-1], 120.0, delta=4.1)
        for v in vals:
            self.assertGreaterEqual(v, 4.0)
            self.assertLessEqual(v, 96.6)
            self.assertFalse(f"{v:.4f}".endswith('00'))

    def test_events_have_timestamps_and_types(self):
        twin = _beals()
        sess = PI.open_session([twin], title='The diner cut', in_room=12)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=180, title='A comedy pilot')
        ev = sess['events']
        self.assertTrue(ev)
        types = {e['type'] for e in ev}
        self.assertTrue(types & set(PI.EVENT_TYPES))
        self.assertTrue(types & {'love', 'laugh', 'lean-in', 'wow'})
        for e in ev:
            self.assertIsNotNone(e.get('t'))
            self.assertGreaterEqual(float(e['t']), 0)
            self.assertLessEqual(float(e['t']), 180.1)
            self.assertTrue(e.get('line'))
            self.assertIn(e['type'], PI.EVENT_TYPES)

    def test_question_tagged_with_playhead(self):
        rec = {'q': 'Would you keep watching?', 'kind': 'yes_no'}
        PI.stamp_question(rec, 12 * 60 + 40)
        self.assertEqual(rec['playhead_s'], 760.0)
        self.assertEqual(rec['playhead_clock'], '12:40')

    def test_aggregate_in_range(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Cut A', in_room=10)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=90, title='Cut A')
        for v in sess['dial']['value']:
            self.assertGreater(v, 8)
            self.assertLessEqual(v, 96.6)
        sc = sess['scorecard']
        self.assertGreater(sc['overall_like'], 8)
        self.assertLessEqual(sc['overall_like'], 96.6)
        self.assertTrue(sc['favorite_moment']['clock'])
        self.assertTrue(sc['dead_spot']['clock'])

    def test_two_subjects_do_not_pin(self):
        a = _beals('Jennifer Beals', 'jb')
        b = _young()
        sa = PI.open_session([a], title='Same cut', in_room=8, session_id='aaa111')
        sb = PI.open_session([b], title='Same cut', in_room=8, session_id='bbb222')
        PI.prepare_watch(sa, {'jb': a}, duration_s=96, title='Same cut')
        PI.prepare_watch(sb, {'ym': b}, duration_s=96, title='Same cut')
        self.assertNotEqual(sa['dial']['value'], sb['dial']['value'])
        same = sum(1 for x, y in zip(sa['dial']['value'], sb['dial']['value']) if abs(x - y) < 0.00015)
        self.assertLess(same, 3)

    def test_charge_on_play_then_more(self):
        sess = PI.open_session([_beals()], title='T', in_room=12)
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot')
        PI.mark_play_paid(sess)
        self.assertTrue(sess['play_charged'])
        self.assertIsNone(PI.charge_due(sess))
        sess['questions'] = [{'q': str(i)} for i in range(12)]
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot_more')

    def test_delete_keeps_30_day_copy(self):
        s3 = FakeS3()
        twin = _beals()
        payload = {'meta': twin['meta'], 'pilot_sessions': []}
        sess = PI.open_session([twin], title='Delete me', in_room=6, session_id='del99')
        payload['pilot_sessions'].append(sess)
        key = 'digital-twins/jenna/Jennifer_Beals_x.json.gz'
        removed = PI.delete_session(s3, key, payload, 'del99', deleted_by='jenna')
        self.assertEqual(removed['id'], 'del99')
        self.assertEqual(payload['pilot_sessions'], [])
        undo = PI.deleted_pilot_key(key, 'del99')
        self.assertIn(undo, s3.objs)
        rec = json.loads(s3.objs[undo].decode('utf-8'))
        self.assertIn('undo_until', rec)
        self.assertEqual(rec['session']['id'], 'del99')
        self.assertEqual(PI.DELETED_PILOT_KEEP_DAYS, 30)

    def test_csv_has_dial_and_events(self):
        twin = _beals()
        sess = PI.open_session([twin], title='CSV cut', in_room=6)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=60, title='CSV cut')
        text = PI.session_csv(sess)
        self.assertIn('Dial average', text)
        self.assertIn('Total positive', text)
        self.assertIn('Definitely recommend', text)
        self.assertIn('Quadrant', text)
        self.assertIn('Norm %', text)
        self.assertIn('Dial', text)
        self.assertIn('Clock', text)
        self.assertNotIn('\u2014', text)
        self.assertNotIn('\u2013', text)


class Copy(unittest.TestCase):
    def test_no_em_dash_in_engine_copy(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Copy check', in_room=8)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=80, title='A funny pilot')
        blob = json.dumps(sess, ensure_ascii=False)
        self.assertNotIn('\u2014', blob)
        self.assertNotIn('\u2013', blob)
        for tok in ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://'):
            self.assertNotIn(tok, blob.lower())
        for e in sess['events']:
            self.assertFalse(PI.has_banned_copy(e['line']))

    def test_how_to_item(self):
        doc = DT.how_to()
        ids = [it['id'] for it in doc['items']]
        self.assertIn('pilot', ids)
        it = next(x for x in doc['items'] if x['id'] == 'pilot')
        self.assertEqual(it['title'], 'Pilot Testing')
        blob = json.dumps(it, ensure_ascii=False) + doc['where']
        self.assertIn('Play', it['answers'])
        self.assertIn('dial', it['answers'])
        self.assertIn('$5,500', it['cost'])
        self.assertIn('Focus Group IQ', it['cost'])
        self.assertNotIn('\u2014', blob)
        for tok in ('synth', 'synthetic', 'modeled', 'claude', 'hostmap', 'pipeline'):
            self.assertNotIn(tok, blob.lower())
        self.assertIn('Pilot Testing tab', doc['where'])

    def test_pricing_line(self):
        lines = DT.pricing_lines()
        self.assertTrue(any(l.startswith('Pilot Testing - $5,500') for l in lines))
        self.assertEqual(DT.PRICING['digital_twin_pilot'], 5500.0)
        self.assertEqual(DT.PRICING['digital_twin_pilot_more'], 5500.0)
        self.assertEqual(DT.PULL_TYPES['digital_twin_pilot'], 'Digital Twins IQ Pilot Testing')

    def test_tab_id_on_the_page(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('data-tab="pilot"', page)
        self.assertIn('id="dtwTabPilot"', page)
        self.assertIn('Pilot Testing', page)
        self.assertNotIn('Screen Engine', page)
        self.assertNotIn('Virtuworks', page)
        pane = page[page.find('id="dtwTabPilot"'):page.find('id="dtwTabPilot"') + 280]
        js = ''
        mark = page.find('function renderPilotConsole')
        if mark >= 0:
            js = page[mark:mark + 6000]
        start = page.find('Watch a cut with the room')
        if start >= 0:
            js += page[start:start + 4000]
        for blob in (pane, js):
            low = blob.lower()
            for tok in ('synth', 'synthetic', 'modeled', 'claude', 'hostmap'):
                self.assertNotIn(tok, low)

    def test_api_routes_named(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('/pilot/open', api)
        self.assertIn('/pilot/<sid>/play', api)
        self.assertIn('/pilot/<sid>/ask', api)
        self.assertIn('/pilot/<sid>/export.csv', api)
        self.assertIn('digital_twin_pilot', api)
        self.assertNotIn('\u2014', api)


class Beats(unittest.TestCase):
    def test_genre_shifts_tone(self):
        funny = PI.build_beats('A comedy pilot', 60, 'hash-a', notes='sitcom')
        tense = PI.build_beats('A thriller cut', 60, 'hash-b', notes='horror')
        self.assertTrue(any(b['tone'] == 'funny' for b in funny))
        self.assertTrue(any(b['tone'] in ('tense', 'quiet', 'wow') for b in tense))

    def test_transcript_pins_dialogue(self):
        beats = PI.build_beats('Title', 40, 'h', transcript=[{'t': 8, 'text': 'I never left Chicago.'}])
        hit = [b for b in beats if 'Chicago' in (b.get('dialogue') or '')]
        self.assertTrue(hit)

    def test_beats_carry_scene_fields(self):
        an = _fake_cut('funny')
        beats = PI.build_beats('Same title', 300, 'h', scenes=an['scenes'], captions=an['captions'], transcript=an['transcript'])
        b = beats[5]
        for k in ('t_start', 't_end', 'scene_id', 'caption', 'dialogue', 'speaker', 'tone', 'act'):
            self.assertIn(k, b)
        self.assertTrue(b['caption'])
        self.assertIn('leftovers', b['dialogue'])
        self.assertEqual(len({x['scene_id'] for x in beats}), 25)


# ------------------------------------------------------------ the watch
def _fake_cut(kind, n=25, scene_s=12.0):
    """A fake read of a file: scenes, a caption per scene, a transcript."""
    scenes = [{'id': i, 't_start': i * scene_s, 't_end': (i + 1) * scene_s} for i in range(n)]
    caps, tr = {}, []
    for i in range(n):
        if kind == 'funny':
            caps[i] = {'caption': f"Two roommates trade jokes in a bright kitchen, beat {i}", 'tone': 'funny', 'joke': i % 2 == 0, 'setting': 'kitchen', 'people': 2, 'text': ''}
            tr.append({'t': i * scene_s + 2, 'end': i * scene_s + 6, 'text': 'Did you really eat my leftovers again?'})
        else:
            caps[i] = {'caption': f"A woman walks alone down a dark hallway, beat {i}", 'tone': 'tense' if i % 3 else 'quiet', 'joke': False, 'setting': 'hallway', 'people': 1, 'text': ''}
            tr.append({'t': i * scene_s + 2, 'end': i * scene_s + 6, 'text': 'Is someone there?'})
    return {'runtime_s': n * scene_s, 'scenes': scenes, 'captions': caps, 'transcript': tr, 'silences': [],
            'genre_votes': {'comedy' if kind == 'funny' else 'thriller': 3}, 'source': 'file', 'tools': {}, 'notes': []}


def _run(kind, twin=None, sid=None, in_room=12, fake_model=None, analysis=None, duration=300):
    from migration import digital_twin_room as R
    twin = twin or _beals()
    sess = PI.open_session([twin], title='Same title', in_room=in_room, session_id=sid or f"s-{kind}")
    R.set_model_fn(fake_model)
    try:
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=duration, title='Same title', analysis=analysis if analysis is not None else _fake_cut(kind))
    finally:
        R.set_model_fn(None)
    return sess


def _fake_watch_model(role, system, user, **kw):
    """A model that reacts to the captions: jokes run high, hallways run low."""
    d = json.loads(user)
    if role == 'watch':
        seats = []
        for p in d['room']:
            rows = []
            for s in d['scenes']:
                funny = 'joke' in s['on_screen'].lower()
                lvl = 78 if funny else 34
                if p['seat'] % 2 == 0:
                    lvl -= 6
                ev = 'laugh' if (funny and s['id'] % 3 == 0) else ('check-out' if (not funny and s['id'] % 4 == 0) else '')
                rows.append({'id': s['id'], 'dial': lvl, 'event': ev, 'say': ('That leftovers line got me.' if ev == 'laugh' else ('I drifted in the hallway.' if ev else ''))})
            seats.append({'seat': p['seat'], 'scenes': rows, 'act_note': 'fine'})
        return {'seats': seats, '_stats': {'model': 'fake', 'input_tokens': 10, 'output_tokens': 10}}
    if role == 'battery':
        return {'seats': [{'seat': p['seat'], 'answers': {'overall': 'a' if p['seat'] % 3 else 'b', 'recommend': 'a' if p['seat'] % 2 else 'b', 'finish': 'a', 'tune_in': 'a',
                                                           'urgency_pilot': 'a', 'expectations': 'a'}, 'why': 'It made me laugh.'} for p in d['room']]}
    if role == 'guide':
        return {'questions': [{'t': 12, 'q': 'What is this about?', 'kind': 'open', 'why': 'cold open'}, {'t': 84, 'q': 'Who do you like?', 'kind': 'open', 'why': 'setup'},
                              {'t': 150, 'q': 'Keep watching?', 'kind': 'yes_no', 'why': 'midpoint'}, {'t': 288, 'q': 'Rate it.', 'kind': 'rating', 'why': 'end'}]}
    return None


class Watch(unittest.TestCase):
    def test_two_cuts_same_title_differ_without_a_model(self):
        a = _run('funny', sid='a1')
        b = _run('tense', sid='b1')
        self.assertEqual(a['watch']['source'], 'file')
        self.assertFalse(a['watch']['persona'])
        da, db = a['dial']['value'], b['dial']['value']
        self.assertEqual(len(da), len(db))
        diff = sum(abs(x - y) for x, y in zip(da, db)) / len(da)
        self.assertGreater(diff, 1.0)
        self.assertNotEqual({e['type'] for e in a['events']}, {e['type'] for e in b['events']})

    def test_two_cuts_same_title_differ_with_the_room_watching(self):
        a = _run('funny', sid='a2', fake_model=_fake_watch_model)
        b = _run('tense', sid='b2', fake_model=_fake_watch_model)
        self.assertTrue(a['watch']['persona'])
        ma = sum(a['dial']['value']) / len(a['dial']['value'])
        mb = sum(b['dial']['value']) / len(b['dial']['value'])
        self.assertGreater(ma - mb, 15)
        self.assertTrue(any(e['source'] == 'watch' and 'leftovers' in e['line'] for e in a['events']))
        self.assertTrue(any(e['type'] == 'check-out' for e in b['events']))
        self.assertEqual(a['genre'], 'comedy')
        self.assertEqual(b['genre'], 'thriller')
        self.assertEqual(a['guide']['source'], 'reasoned')

    def test_fallback_is_deterministic(self):
        a = _run('funny', sid='same')
        b = _run('funny', sid='same')
        self.assertEqual(a['dial']['value'], b['dial']['value'])
        self.assertEqual([(e['t'], e['seat'], e['type']) for e in a['events']], [(e['t'], e['seat'], e['type']) for e in b['events']])
        self.assertEqual(a['scorecard']['battery']['headline'], b['scorecard']['battery']['headline'])

    def test_persona_read_is_cached_and_not_rebilled(self):
        from migration import digital_twin_room as R
        calls = []

        def counting(role, system, user, **kw):
            calls.append(role)
            return _fake_watch_model(role, system, user, **kw)
        twin = _beals()
        sess = _run('funny', twin=twin, sid='cache', fake_model=counting)
        n_watch = calls.count('watch')
        self.assertGreaterEqual(n_watch, 1)
        calls.clear()
        R.set_model_fn(counting)
        try:
            PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=300, title='Same title', analysis=_fake_cut('funny'))
        finally:
            R.set_model_fn(None)
        self.assertEqual(calls.count('watch'), 0)
        self.assertEqual(calls.count('battery'), 0)

    def test_dial_never_pins_at_the_edges(self):
        a = _run('funny', sid='edge')
        vals = a['dial']['value']
        self.assertLess(max(vals), 96.0)
        self.assertGreater(min(vals), 4.5)
        for key, sp in a['dial']['splits'].items():
            self.assertNotEqual(sp['value'], vals, key)
        self.assertLess(sum(1 for x, y in zip(vals, a['dial']['splits']['women']['value']) if abs(x - y) < 0.00015), 3)

    def test_public_session_hides_internals(self):
        a = _run('funny', sid='pub', fake_model=_fake_watch_model)
        a['media']['s3_key'] = 'digital-twins/pilot-tests/jenna/pub/cut.mp4'
        pub = PI.public_session(a)
        self.assertTrue(pub['media']['has_file'])
        self.assertNotIn('s3_key', pub['media'])
        self.assertNotIn('by_seat', pub['watch'])
        self.assertNotIn('sources', json.dumps(pub['scorecard'].get('norms') or {}))


class Battery(unittest.TestCase):
    def test_shares_sum_to_100_no_zero_messy_counts(self):
        for sid, fake in (('bat-det', None), ('bat-model', _fake_watch_model)):
            sess = _run('funny', sid=sid, fake_model=fake)
            bat = sess['scorecard']['battery']
            self.assertEqual(bat['format'], 'pilot')
            for q in bat['questions']:
                rows = q['rows']
                self.assertAlmostEqual(sum(r['pct'] for r in rows), 100.0, places=1, msg=q['id'])
                for r in rows:
                    self.assertGreater(r['pct'], 0.0, (q['id'], r['label']))
                    self.assertGreater(r['people'], 0)
                    self.assertNotEqual(r['people'] % 10, 0, (q['id'], r['label'], r['people']))
            head = bat['headline']
            ov = next(q for q in bat['questions'] if q['id'] == 'overall')
            self.assertAlmostEqual(ov['total_positive_pct'], round(ov['rows'][0]['pct'] + ov['rows'][1]['pct'], 1), places=1)
            self.assertEqual(head['total_positive_pct'], ov['total_positive_pct'])
            self.assertEqual(head['excellent_pct'], ov['rows'][0]['pct'])
            rc = next(q for q in bat['questions'] if q['id'] == 'recommend')
            self.assertEqual(head['definitely_recommend_pct'], rc['rows'][0]['pct'])
            self.assertIsNotNone(head['finish_pct'])
            self.assertIsNotNone(head['tune_in_pct'])
            self.assertEqual(sess['scorecard']['recommend_pct'], head['definitely_recommend_pct'])

    def test_feature_battery_has_urgency_and_full_price(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Long one', in_room=8, session_id='feat')
        sess['mode'] = 'feature'
        PI.prepare_watch(sess, {'jb': twin}, duration_s=100 * 60, title='Long one')
        ids = [q['id'] for q in sess['scorecard']['battery']['questions']]
        self.assertIn('urgency', ids)
        self.assertIn('full_price', ids)
        self.assertEqual(sess['format'], 'feature')
        self.assertIsNotNone(sess['scorecard']['battery']['headline']['full_price_pct'])

    def test_norm_columns_and_lines(self):
        sess = _run('funny', sid='norms')
        sc = sess['scorecard']
        self.assertEqual(sc['genre'], 'comedy')
        cols = sc['norm_columns']
        self.assertEqual(cols['excellent']['norm'], 30.0)
        self.assertEqual(cols['excellent']['genre_norm'], 48.0)
        self.assertEqual(cols['total_positive']['norm'], 65.0)
        self.assertEqual(cols['definitely_recommend']['genre_norm'], 71.0)
        self.assertTrue(sc['norm_lines'])
        self.assertTrue(any(l.startswith('Definitely recommend') and 'comedy screenings usually land near 71%' in l for l in sc['norm_lines']))
        for l in sc['norm_lines']:
            self.assertFalse(PI.has_banned_copy(l), l)

    def test_norm_metrics_for_the_ledger(self):
        sess = _run('funny', sid='metrics')
        m = PI.norm_metrics(sess)
        for k in ('excellent', 'total_positive', 'definitely_recommend', 'finish', 'tune_in', 'urgency_opening', 'confusion'):
            self.assertIn(k, m)
        s3 = FakeS3()
        PI.stop_session(sess)
        doc = PI.record_norms(sess, s3=s3)
        self.assertTrue(doc)
        self.assertIn('norms_recorded_at', sess)
        from migration import digital_twin_norms as N
        cell = doc['cells'][N.cell_key('pilot', 'comedy', 'definitely_recommend')]
        self.assertEqual(cell['n'], 1)
        self.assertIsNone(PI.record_norms(sess, s3=s3))   # the same test counts once: nothing to write
        self.assertEqual(N.load(s3)['cells'][N.cell_key('pilot', 'comedy', 'definitely_recommend')]['n'], 1)


class Splits(unittest.TestCase):
    def _quad_room(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Quadrants', in_room=16, session_id='quad')
        roster = sess['group']['roster']
        for i, r in enumerate(roster):   # four seats in every quadrant
            r['gender'] = 'F' if i % 2 == 0 else 'M'
            r['gender_label'] = 'Female' if r['gender'] == 'F' else 'Male'
            r['age'] = 27 if (i // 2) % 2 == 0 else 48
        PI.prepare_watch(sess, {'jb': twin}, duration_s=300, title='Quadrants', analysis=_fake_cut('funny'))
        return sess

    def test_quadrant_splits_are_four_lines(self):
        sess = self._quad_room()
        sp = sess['dial']['splits']
        for key in ('women_under_35', 'women_35_plus', 'men_under_35', 'men_35_plus'):
            self.assertIn(key, sp)
            self.assertEqual(sp[key]['group'], 'quadrant')
            self.assertEqual(sp[key]['n'], 4)
            self.assertEqual(len(sp[key]['value']), len(sess['dial']['value']))
        lines = [sp[k]['value'] for k in ('women_under_35', 'women_35_plus', 'men_under_35', 'men_35_plus')]
        self.assertEqual(len({tuple(l) for l in lines}), 4)
        quads = sess['scorecard']['battery']['quadrants']
        self.assertEqual([q['key'] for q in quads], ['total', 'women_under_35', 'women_35_plus', 'men_under_35', 'men_35_plus'])
        for q in quads:
            self.assertEqual(q['n'], 16 if q['key'] == 'total' else 4)
            self.assertIsNotNone(q['total_positive_pct'])
            self.assertNotEqual(q['people'] % 10, 0)

    def test_every_demo_split_is_available(self):
        sess = self._quad_room()
        groups = {v['group'] for v in sess['dial']['splits'].values()}
        for g in ('gender', 'quadrant', 'age', 'orientation', 'education', 'lifecycle', 'fanship'):
            self.assertIn(g, groups)
        self.assertTrue(any(k.startswith('eth_') for k in sess['dial']['splits']) or not any(r.get('ethnicity') for r in sess['group']['roster']))

    def test_split_dial_takes_any_predicate(self):
        sess = self._quad_room()
        line = PI.split_dial(sess['group']['roster'], sess['seat_dials'], lambda s: int(s['seat']) <= 5, 'Jennifer Beals', 'mk', 'first-five')
        self.assertTrue(line)
        self.assertEqual(len(line), len(sess['dial']['value']))


class Moment(unittest.TestCase):
    def test_question_window_carries_the_last_minute(self):
        sess = _run('funny', sid='win')
        win = PI.question_window(sess, 150)
        self.assertEqual(win['clock'], '2:30')
        self.assertEqual(win['from_clock'], '1:15')
        self.assertTrue(60 <= (win['playhead_s'] - win['from_s']) <= 90)
        self.assertTrue(win['beats'])
        for b in win['beats']:
            self.assertTrue(75 <= b['t'] <= 150.05)
            self.assertTrue(b['on_screen'])
        self.assertIsNotNone(win['dial_now'])
        self.assertTrue(win['frame'].startswith('At 2:30, right after'))
        self.assertTrue(win['scene'] and win['scene']['on_screen'])

    def test_ask_stimulus_is_in_cut_with_seat_windows(self):
        sess = _run('funny', sid='stim')
        stim = PI.ask_stimulus(sess, 200)
        self.assertTrue(stim['in_cut'])
        self.assertEqual(len(stim['seat_windows']), 12)
        one = next(iter(stim['seat_windows'].values()))
        self.assertIn('dial_now', one)
        self.assertEqual(stim['cut']['format'], 'pilot')
        from migration import digital_twin_room as R
        self.assertTrue(R._in_cut(stim))
        view = R._moment_view(stim)
        self.assertEqual(view['clock'], '3:20')
        self.assertTrue(view['the_last_minute'])
        mine = R._moment_for_seat(stim, 1)
        self.assertIn('your_dial_in_these_seconds', mine)

    def test_probes_and_guide(self):
        sess = _run('funny', sid='probe')
        self.assertTrue(sess['probes'])
        kinds = {p['kind'] for p in sess['probes']}
        self.assertTrue(kinds & {'valley', 'peak'})
        for p in sess['probes']:
            self.assertTrue(p['chip'].startswith('Ask'))
            self.assertTrue(p['q'].endswith('?'))
            self.assertFalse(PI.has_banned_copy(p['q'] + p['chip']))
        g = sess['guide']
        self.assertGreaterEqual(len(g['questions']), 5)
        self.assertLessEqual(len(g['questions']), 8)
        self.assertEqual(g['questions'][0]['kind'], 'open')
        self.assertTrue(g['battery'])


class Security(unittest.TestCase):
    class S3Sign(FakeS3):
        def generate_presigned_url(self, op, Params=None, ExpiresIn=600):
            return f"https://dashboard-inputs.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=abc"

    def test_signed_link_owner_only(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Secure', in_room=6, created_by='jenna', session_id='sec1')
        sess['media']['s3_key'] = 'digital-twins/pilot-tests/jenna/sec1/cut.mp4'
        self.assertTrue(PI.can_view_media(sess, 'jenna'))
        self.assertFalse(PI.can_view_media(sess, 'someone_else'))
        self.assertTrue(PI.can_view_media(sess, 'someone_else', is_admin=True))
        link = PI.signed_media_url(self.S3Sign(), sess)
        self.assertEqual(link['expires_in'], PI.MEDIA_URL_EXPIRES_S)
        self.assertEqual(PI.MEDIA_URL_EXPIRES_S, 600)
        self.assertIn('X-Amz-Expires=600', link['url'])
        self.assertIn('expires_at', link)
        self.assertIsNone(PI.signed_media_url(self.S3Sign(), PI.open_session([twin], title='No file', in_room=4)))

    def test_route_wiring(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn("/pilot/<sid>/media-url", api)
        i = api.find("def dt_pilot_media_url")
        body = api[i:i + 1800]
        self.assertLess(body.find('can_view_media'), body.find('signed_media_url'))
        self.assertIn('403', body)
        j = api.find("def dt_pilot_media(")
        self.assertIn('can_view_media', api[j:j + 900])
        self.assertIn('purge_media', api)
        self.assertIn('record_norms', api)
        self.assertIn("pilot_prepare_job", api)

    def test_purge_media_keeps_the_read(self):
        s3 = FakeS3()
        twin = _beals()
        sess = _run('funny', twin=twin, sid='purge')
        sess['media']['s3_key'] = 'digital-twins/pilot-tests/jenna/purge/cut.mp4'
        s3.objs[sess['media']['s3_key']] = b'x' * 100
        sess['purge_media_after'] = True
        self.assertTrue(PI.purge_media(s3, sess, 'done'))
        self.assertNotIn('digital-twins/pilot-tests/jenna/purge/cut.mp4', s3.objs)
        self.assertEqual(sess['media']['s3_key'], '')
        self.assertIn('purged_at', sess['media'])
        self.assertTrue(sess['dial']['value'])
        self.assertTrue(sess['events'])
        self.assertTrue(sess['scorecard'])

    def test_delete_moves_the_file_into_the_undo_copy(self):
        class S3Copy(FakeS3):
            def copy_object(self, Bucket, CopySource, Key, **kw):
                self.objs[Key] = self.objs[CopySource['Key']]
        s3 = S3Copy()
        twin = _beals()
        payload = {'meta': twin['meta'], 'pilot_sessions': []}
        sess = PI.open_session([twin], title='Delete me', in_room=6, session_id='del77')
        sess['media']['s3_key'] = 'digital-twins/pilot-tests/jenna/del77/cut.mp4'
        s3.objs[sess['media']['s3_key']] = b'y' * 100
        payload['pilot_sessions'].append(sess)
        key = 'digital-twins/jenna/Jennifer_Beals_x.json.gz'
        PI.delete_session(s3, key, payload, 'del77', deleted_by='jenna')
        self.assertNotIn('digital-twins/pilot-tests/jenna/del77/cut.mp4', s3.objs)
        rec = json.loads(s3.objs[PI.deleted_pilot_key(key, 'del77')].decode('utf-8'))
        self.assertTrue(rec['media_undo_key'])
        self.assertIn(rec['media_undo_key'], s3.objs)

    def test_watermark_and_signed_link_on_the_page(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('dtw-pi-wm a', page)
        self.assertIn('dtw-pi-wm b', page)
        self.assertIn('pointer-events: none', page[page.find('.dtw-pi-wm {'):page.find('.dtw-pi-wm {') + 200])
        self.assertIn('MutationObserver', page[page.find('function ensureWatermark'):page.find('function ensureWatermark') + 3000])
        self.assertIn("/media-url'", page)
        self.assertIn('scheduleMediaRefresh', page)
        self.assertNotIn("'/media')", page[page.find('function hasMedia'):page.find('window.dtwPilotRename')])
        self.assertIn('dtwPiPurge', page)
        self.assertIn('Remove the file when the test is done', page)


class Seams(unittest.TestCase):
    def test_session_schema(self):
        sess = PI.open_session([_beals()], title='Seams', in_room=6)
        self.assertEqual(sess['mode'], 'pilot')
        self.assertIsNone(sess['lineage'])
        self.assertEqual(sess['cut_label'], 'Cut A')
        self.assertFalse(sess['rough_cut'])
        self.assertEqual(sess['cast'], [])
        self.assertEqual(sess['comps'], [])
        self.assertEqual(sess['genre'], '')
        self.assertFalse(sess['purge_media_after'])
        for m in ('feature', 'episode', 'binge', 'trailer', 'spot', 'title', 'key_art', 'script', 'concept'):
            self.assertIn(m, PI.MODES)
        self.assertEqual(PI.open_session([_beals()], title='F', in_room=4, mode='feature')['mode'], 'feature')

    def test_set_options(self):
        sess = _run('funny', sid='opts')
        changed = PI.set_options(sess, {'genre': 'horror', 'rough_cut': True, 'purge_media_after': True, 'cut_label': 'Cut B', 'mode': 'feature'})
        self.assertEqual(changed['genre'], 'horror')
        self.assertTrue(sess['genre_set_by_user'])
        self.assertEqual(sess['scorecard']['genre'], 'horror')
        self.assertEqual(sess['scorecard']['norms']['genre_key'], 'horror')
        self.assertTrue(sess['rough_cut'])
        self.assertTrue(sess['purge_media_after'])
        self.assertEqual(sess['cut_label'], 'Cut B')
        self.assertEqual(sess['mode'], 'feature')
        self.assertEqual(PI.set_options(sess, {'mode': 'nonsense'}), {})

    def test_norms_for_is_public(self):
        n = PI.norms_for('pilot', 'comedy')
        self.assertEqual(n['genre_used'], 'comedy')
        self.assertEqual(n['genre']['definitely_recommend']['value'], 71.0)
        self.assertEqual(n['all']['definitely_recommend']['value'], 50.0)
        self.assertNotIn('sources', json.dumps(n))
        n2 = PI.norms_for('pilot', 'unscripted')
        self.assertEqual(n2['genre_used'], 'all')
        self.assertEqual(n2['genre']['excellent']['value'], 30.0)


class UIStrings(unittest.TestCase):
    def test_pilot_page_copy_is_clean(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        start = page.find('function hasMedia(s)')
        end = page.find('window.dtwPilotRename')
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        blob = page[start:end]
        self.assertNotIn('\u2014', blob)
        self.assertNotIn('\u2013', blob)
        low = blob.lower()
        for tok in ('synth', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3', 'panel-projected', 'screen engine', 'nrg', 'marketcast', 'fishdog'):
            self.assertNotIn(tok, low, tok)
        for must in ('Scorecard', 'By quadrant', 'Moderator guide', 'Overlay any two groups', 'Preparing the cut', 'Total positive', 'Definitely recommend'):
            self.assertIn(must, blob)
        css = page[page.find('.dtw-pi-wm {'):page.find('.dtw-pi-wm {') + 4000]
        self.assertNotIn('\u2014', css)

    def test_how_to_protect_line(self):
        doc = DT.how_to()
        it = next(x for x in doc['items'] if x['id'] == 'pilot')
        self.assertIn('protect', it)
        for tok in ('synth', 'modeled', 'claude', 'hostmap', 'pipeline', 's3', 'bucket', 'signed url', 'presign'):
            self.assertNotIn(tok, it['protect'].lower(), tok)
        self.assertIn('expire', it['protect'])
        self.assertIn('every frame', it['protect'])
        self.assertIn('$5,500', it['cost'])
        self.assertIn('norm', it['answers'])
        text = DT.how_to_text('pilot testing')
        self.assertIn('How we protect your cut', text)
        self.assertNotIn('\u2014', text)

    def test_engine_copy_is_clean(self):
        from migration import digital_twin_watch as W
        for s in (W.CAPTION_SYSTEM, W.WATCH_SYSTEM, W.BATTERY_SYSTEM, W.GUIDE_SYSTEM):
            self.assertNotIn('\u2014', s)
            self.assertNotIn('\u2013', s)
        sess = _run('funny', sid='copy2', fake_model=_fake_watch_model)
        blob = json.dumps(PI.public_session(sess), ensure_ascii=False)
        self.assertNotIn('\u2014', blob)
        for tok in ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://'):
            self.assertNotIn(tok, blob.lower())
        for q in sess['guide']['questions']:
            self.assertFalse(PI.has_banned_copy(q['q']), q)
        for e in sess['events']:
            self.assertFalse(PI.has_banned_copy(e['line']), e)
        for l in PI.session_lines(sess):
            self.assertFalse(PI.has_banned_copy(l), l)


if __name__ == '__main__':
    unittest.main(verbosity=1)
