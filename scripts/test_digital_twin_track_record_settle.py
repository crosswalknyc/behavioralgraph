#!/usr/bin/env python3
"""The track record settles itself (Digital Twins IQ, 2026-11-20).

Jenna's choices, 2026-11-20: "Only questions with a real result to check:
forecasts, election and premiere calls, the final question of a
Predictive Analytics run, and behavior we can count ourselves"; "Only
calls we deliberately log before the result; practice rooms stay
internal, and the 24 open room answers come off clients' view now";
client results "Yes"; the weekly summary to "Jenna, Jessie and Liz";
"All of it, starting with the two flaws and the daily scoring".

Held here:
  * a call is frozen when it is made: a different prediction is a dated
    revision row, the first keeps its prediction, hash and date; a
    forecast re-read after its title aired never replaces the call;
  * a Crosswalk practice row never reaches a client; a call logged on
    purpose does; an outcome is written once;
  * what a call is: the words of a room answer, dates, events, the final
    question of a run, the three pilot forecast calls;
  * logging on purpose: a room question with its due day, check, measure
    and scoring; refused once the result is knowable;
  * the daily check, with fakes for our own count and both lookups: a
    counted call settles, a published or certified one settles once the
    lookup finds a sourced result and retries otherwise, a pilot forecast
    finds its air date and then settles, a client is asked once, a call
    made after its result was knowable is post hoc, a miss stays, one
    runner holds the lock, five failed days alert jenna@ and jessie@;
  * the weekly summary goes to jenna@, jessie@ and liz@ and only when
    something is new;
  * the cleanup of the open rows: not calls leave the record, an answer
    asked again folds into the first;
  * the routes: the public record read live with no sign-in and a short
    cache, the drawer's Add your result for a client's own open call, the
    staff Log a call; the page carries the forms and the live fetch.

Hermetic: REGRESSION_TEST_MODE, an in-memory ledger or a fake S3, no
model, no warehouse, no mail.
    python3 scripts/test_digital_twin_track_record_settle.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('DTIQ_ROOM_HOST', '0')

from migration import digital_twin_calls as CALLS  # noqa: E402
from migration import digital_twin_counted as COUNT  # noqa: E402
from migration import digital_twin_ledger as LG  # noqa: E402
from migration import digital_twin_settle as ST  # noqa: E402

DAY = 86400.0
T0 = 1795000000.0          # 2026-11-18 (UTC); every clock below is relative to it
BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|research(?:ed)?|panel|survey|confidence|calibrat\w*|nielsen|kinds?)\b|\u2014|\u2013", re.I)


def iso(t):
    return ST._iso(t)


class FakeConn:
    """The warehouse: the content map rows and one canned count."""

    def __init__(self, mapped=(('Hulu', 'hulu.com/watch/abc123-def456'),), count=(1234,), ret=(800, 512)):
        self.mapped, self.count, self.ret, self.sql = list(mapped), count, ret, []

    def cursor(self):
        return self

    def execute(self, sql):
        self.sql.append(sql)
        self._last = sql

    def fetchall(self):
        if 'content_mapping' in self._last:
            return self.mapped
        if 'countIf' in self._last:
            return [self.ret]
        return [self.count]

    def close(self):
        pass


def _counted_call(subject='Gilmore Girls', metric='title_viewers', ws='2026-11-01', we='2026-11-08', due='2026-11-09'):
    return CALLS.make_call(resolver='counted', comparator='number', due_at=due, knowable_at=ws,
                           measure={'subject': subject, 'what': 'People who watch it on Hulu in its first week', 'metric': metric, 'title': subject, 'platform': 'Hulu',
                                    'window_start': ws, 'window_end': we, 'unit': 'people'})


class Frozen(unittest.TestCase):
    def setUp(self):
        LG.reset_memory()

    def test_a_different_prediction_is_a_revision_and_the_first_call_stands(self):
        first = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_000_003, 'unit': 'people'}, measure='week_one_audience', session_id='s1',
                                     created_at='2026-11-01T10:00:00Z', company='Acme')
        later = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_200_007, 'unit': 'people'}, measure='week_one_audience', session_id='s1', company='Acme')
        self.assertNotEqual(later['id'], first['id'])
        self.assertEqual(later['status'], 'revision')
        self.assertEqual(later['revises'], first['id'])
        self.assertFalse(later['public'])
        kept = LG.load_record(None, first['id'])
        self.assertEqual(kept['prediction'], {'point': 1_000_003, 'unit': 'people'})
        self.assertEqual(kept['hash'], first['hash'])
        self.assertEqual(kept['created_at'], '2026-11-01T10:00:00Z')
        again = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_200_007, 'unit': 'people'}, measure='week_one_audience', session_id='s1', company='Acme')
        self.assertEqual(again['id'], later['id'], 'the same revision is one row')
        # a revision is never scored and never read
        self.assertIsNone(LG.record_outcome(None, later['id'], value=1_100_009))
        self.assertEqual([r['id'] for r in LG.open_rows(None, staff=True)], [first['id']])
        LG.record_outcome(None, first['id'], value=1_100_009, source_class='client')
        self.assertEqual(LG.summary(None)['n_scored'], 1, 'the first call is the one that counts')

    def test_a_forecast_read_again_after_air_never_replaces_the_call(self):
        from migration import digital_twin_forecast as FC
        twin = {'meta': {'name': 'NCIS', 'subject': 'NCIS', 'key': 'digital-twins/acme/NCIS_1.json.gz', 'platform_home': 'Paramount+', 'company': 'Acme'}}
        sess = {'id': 'pt1', 'created_by': 'carol', 'scorecard': {'battery': {'headline': {'tune_in_pct': 71.4}}}}
        out = {'show': 'NCIS', 'base_people': 5_000_003, 'episode': {'week_one_audience': 3_100_007, 'low': 2_600_009, 'high': 3_600_001, 'definition': 'People who watch the episode within seven days of release, on every screen.'},
               'series': {'season_completion_pct': 61.3}, 'calibration': {'scope': 'none', 'anchors': []}, 'actual': None, 'firmness': {'word': 'early'}}
        FC._ledger_rows(None, twin, sess, out, 'NCIS')
        rows = LG.load_index(None)['records']
        self.assertEqual(sorted(r['measure'] for r in rows), ['return_pct', 'season_completion_pct', 'week_one_audience'])
        wk = next(r for r in rows if r['measure'] == 'week_one_audience')
        made = LG.load_record(None, wk['id'])
        self.assertFalse(made['post_hoc'])
        self.assertEqual(made['created_by'], 'carol')
        self.assertEqual(made['call']['resolver'], 'published')
        self.assertEqual(made['call']['due_at'], '', 'the due day waits on the air date')
        # the title aired; the forecast is read again with the actual: a later read, never a rewrite
        out2 = json.loads(json.dumps(out))
        out2['episode']['week_one_audience'] = 3_400_001
        out2['actual'] = {'week_one_audience': 3_500_003, 'aired': '2026-10-06'}
        out2['calibration']['anchors'] = [{'show': 'NCIS'}]
        FC._ledger_rows(None, twin, sess, out2, 'NCIS')
        kept = LG.load_record(None, wk['id'])
        self.assertEqual(kept['prediction']['point'], 3_100_007)
        self.assertEqual(kept['hash'], made['hash'])
        revs = [r for r in LG.load_index(None)['records'] if r['status'] == 'revision']
        self.assertTrue(revs and all(r['post_hoc'] for r in revs))


class Visibility(unittest.TestCase):
    def setUp(self):
        LG.reset_memory()

    def test_practice_rows_stay_internal_and_logged_calls_reach_everyone(self):
        practice = LG.record_prediction(None, 'room_count', 'Amazon Prime Video', {'distribution': [{'label': 'Netflix', 'pct': 30.3}], 'unit': 'pct'},
                                        measure='share:Netflix', session_id='r1', question_id='q1', question='Which app do you open first?', company='Crosswalk')
        logged = LG.record_prediction(None, 'call', 'Texas runoff voters', {'call': 'A', 'distribution': [{'label': 'A', 'pct': 56.1}, {'label': 'B', 'pct': 43.9}], 'unit': 'pct'},
                                      session_id='r2', question_id='q2', company='Crosswalk', logged=True, logged_by='jenna')
        idx = {r['id']: r for r in LG.load_index(None)['records']}
        self.assertFalse(LG.visible(idx[practice['id']], company='Paramount+'))
        self.assertTrue(LG.visible(idx[practice['id']], staff=True))
        self.assertTrue(LG.visible(idx[logged['id']], company='Paramount+'))
        LG.update_call(None, practice['id'], status='not_a_call', status_reason='the past')
        self.assertEqual([r['id'] for r in LG.open_rows(None, staff=True)], [logged['id']], 'a row that is not a call reaches nobody')
        LG.record_outcome(None, logged['id'], winner='A', source_class='certified result')
        self.assertIsNone(LG.record_outcome(None, logged['id'], winner='B'), 'a miss is never revised')
        self.assertEqual(LG.load_record(None, logged['id'])['outcome']['winner'], 'A')
        self.assertEqual([r['subject'] for r in LG.public_rows(None, site=True)], ['Texas runoff voters'])


class WhatACallIs(unittest.TestCase):
    def test_room_answers_that_are_not_calls(self):
        today = '2026-11-18'
        for q, why in (("Which of these would get you most excited if it finally became a show?", 'what if'),
                       ("What stops you from adding another video service right now?", 'opinion'),
                       ("In the past year, did you start paying for any video service just to watch one specific show?", 'the past'),
                       ("When you sit down to watch something at home, which app do you open first?", 'the past'),
                       ("what brands do you associate with Jennifer Beals?", 'opinion'),
                       ("If the Lululemon and Ganni collection restocked tomorrow, would you buy a piece?", 'what if'),
                       ("after you watch it on APV will you stay a subscriber", 'no date'),
                       ("Who did you vote for on November 3?", 'the past'),
                       ("Who wins the race on November 3, 2026?", 'a date already past')):
            self.assertEqual(CALLS.classify_answer(q, today=today), ('not_a_call', why, ''), q)

    def test_dated_real_world_outcomes_are_calls(self):
        today = '2026-11-18'
        self.assertEqual(CALLS.classify_answer('Who will you vote for in the Texas runoff on December 9?', today=today), ('call', 'election', '2026-12-09'))
        self.assertEqual(CALLS.classify_answer('Will you watch the series premiere on Nov 30, 2026?', today=today), ('call', 'premiere', '2026-11-30'))
        self.assertEqual(CALLS.classify_answer('Will you see it on opening weekend, December 19?', today=today), ('call', 'opening_weekend', '2026-12-19'))
        self.assertEqual(CALLS.find_date('January 4', today), '2027-01-04')
        self.assertEqual(CALLS.find_date('on 11/30/2026', today), '2026-11-30')
        self.assertEqual(CALLS.find_date('3 December 2026', today), '2026-12-03')
        self.assertEqual(CALLS.find_date('next week, sometime in December', today), '', 'a bare month is not a date')

    def _run_sess(self, q, kind='multiple_choice', opts=(('Talarico', 56.2, 4_100_003), ('Paxton', 43.8, 3_200_009))):
        rec = {'id': 'q7', 'guided_i': 4, 'kind': kind, 'q': q, 'finished_at': '2026-11-18T10:00:00Z',
               'options': [{'label': l, 'pct': p, 'people': n} for l, p, n in opts]}
        return {'id': 'sess1', 'created_by': 'carol', 'questions': [rec]}

    def test_the_final_question_of_a_run_is_a_call_only_with_an_outcome_and_a_date(self):
        twin = {'meta': {'name': 'Texas runoff voters', 'subject': 'Texas runoff voters'}}
        plan = {'asked': 'Will Talarico win the runoff on December 9?', 'goal': 'Who wins the runoff.', 'final': 4,
                'questions': [{'q': 'x'}] * 4 + [{'q': 'Who will you vote for in the runoff?', 'kind': 'multiple_choice'}]}
        c = CALLS.guided_call(plan, self._run_sess('Who will you vote for in the runoff?'), twin, today='2026-11-18')
        self.assertEqual(c['call']['resolver'], 'certified')
        self.assertEqual(c['call']['comparator'], 'winner')
        self.assertEqual(c['call']['knowable_at'], '2026-12-09')
        self.assertEqual(c['call']['due_at'], '2026-12-10')
        self.assertEqual(c['prediction']['call'], 'Talarico')
        undated = dict(plan, asked='Will Talarico win the runoff?')
        self.assertIsNone(CALLS.guided_call(undated, self._run_sess('Who will you vote for in the runoff?'), twin, today='2026-11-18'))
        past = dict(plan, asked='Did Talarico win the runoff on November 3?')
        self.assertIsNone(CALLS.guided_call(past, self._run_sess('Who will you vote for in the runoff?'), twin, today='2026-11-18'))
        opinion = dict(plan, asked='Which brand should Jennifer Beals work with?', goal='Which brand fits her.')
        self.assertIsNone(CALLS.guided_call(opinion, self._run_sess('Which brand fits her best?'), twin, today='2026-11-18'))
        # a yes or no on a premiere: the people behind yes against the published first week
        prem = {'asked': 'Will the Gilmore Girls revival premiere on December 5 pull this audience in its first week?', 'goal': 'First week pull.', 'final': 0,
                'questions': [{'q': 'Would you watch it the week it premieres?', 'kind': 'yes_no'}]}
        sess = self._run_sess('Would you watch it the week it premieres?', kind='yes_no', opts=(('Yes', 41.2, 2_200_007), ('No', 58.8, 3_100_003)))
        sess['questions'][0]['guided_i'] = 0
        c2 = CALLS.guided_call(prem, sess, {'meta': {'subject': 'Gilmore Girls'}}, today='2026-11-18')
        self.assertEqual((c2['call']['resolver'], c2['call']['comparator'], c2['prediction']['point'], c2['prediction']['unit']), ('published', 'number', 2_200_007, 'people'))

    def test_the_three_pilot_forecast_calls(self):
        twin = {'meta': {'name': 'Gilmore Girls', 'subject': 'Gilmore Girls', 'platform_home': 'Hulu'}}
        sess = {'scorecard': {'battery': {'headline': {'tune_in_pct': 77.3}}}}
        fc = {'show': 'Gilmore Girls', 'base_people': 4_000_001, 'episode': {'week_one_audience': 2_000_003, 'low': 1, 'high': 3}, 'series': {'season_completion_pct': 58.1}}
        calls = {c['measure']: c for c in CALLS.forecast_calls(twin, sess, fc, aired_on='2026-12-05', content_map_has=lambda show, plat: True)}
        self.assertEqual(calls['week_one_audience']['call']['resolver'], 'published')
        self.assertEqual(calls['week_one_audience']['call']['due_at'], '2026-12-13')
        self.assertEqual(calls['season_completion_pct']['call']['resolver'], 'client')
        self.assertEqual(calls['return_pct']['call']['resolver'], 'counted')
        self.assertEqual(calls['return_pct']['call']['measure']['window_end'], '2026-12-19')
        off_map = {c['measure']: c for c in CALLS.forecast_calls(twin, sess, fc, content_map_has=lambda show, plat: False)}
        self.assertEqual(off_map['return_pct']['call']['resolver'], 'client', 'a title not in the content map is settled by the client')
        for c in off_map.values():
            ok, why = CALLS.validate_call(c['call'])
            self.assertTrue(ok, why)

    def test_the_open_rows_cleanup_folds_repeats_and_drops_non_calls(self):
        LG.reset_memory()
        for i, (q, sid) in enumerate((('What stops you from adding another video service right now?', 'a'),
                                      ('What stops you from adding another video service right now?', 'a'),
                                      ('What stops you from adding another video service right now?', 'b'),
                                      ('Who will you vote for in the runoff on December 9?', 'c'))):
            LG.record_prediction(None, 'room_count', 'Prime Video', {'distribution': [{'label': 'x', 'pct': 40.0 + i}], 'unit': 'pct'}, measure='share:x', session_id=sid,
                                 question_id=f'q{i}', question=q, company='Crosswalk', created_at=f'2026-11-18T0{i}:00:00Z')
        import twins_ledger_cleanup as CLEAN
        steps = CLEAN.plan(None)
        got = sorted((s[0]['session_id'], s[1]) for s in steps)
        self.assertEqual(got, [('a', 'folded'), ('a', 'not_a_call'), ('b', 'not_a_call'), ('c', 'call')])
        CLEAN.apply(None, steps)
        statuses = sorted(r['status'] for r in LG.load_index(None)['records'])
        self.assertEqual(statuses, ['call', 'folded', 'not_a_call', 'not_a_call'])
        self.assertEqual(len(LG.load_index(None)['records']), 4, 'nothing is deleted')
        self.assertEqual(len(LG.open_rows(None, company='Paramount+')), 0)


class LogOnPurpose(unittest.TestCase):
    def setUp(self):
        LG.reset_memory()

    def _payload(self, company='Crosswalk'):
        rec = {'id': 'q1', 'kind': 'multiple_choice', 'q': 'Who wins the runoff on December 9?', 'finished_at': '2026-11-18T10:00:00Z',
               'options': [{'label': 'Talarico', 'pct': 55.4, 'people': 1_000_003}, {'label': 'Paxton', 'pct': 44.6, 'people': 800_009}], 'trail': {'anchor_status': 'derived'}}
        return {'meta': {'name': 'Texas runoff voters', 'subject': 'Texas runoff voters', 'key': 'digital-twins/admin/Texas_1.json.gz', 'company': company},
                'panel_sessions': [{'id': 's1', 'created_by': 'jenna', 'questions': [rec]}]}

    def test_a_room_question_logged_before_its_result(self):
        payload = self._payload()
        body = {'twin_key': payload['meta']['key'], 'session_id': 's1', 'question_id': 'q1', 'due_at': '2099-12-10', 'knowable_at': '2099-12-09',
                'check': 'certified', 'scored_as': 'winner', 'event': 'election'}
        rec, err, code = CALLS.log_on_purpose(None, body, by='jenna', load_twin=lambda k: payload)
        self.assertEqual((err, code), ('', 200))
        self.assertTrue(rec['logged'] and rec['public'], 'a Crosswalk call logged on purpose is public')
        self.assertEqual(rec['call']['measure']['what'], 'Who wins the runoff on December 9?')
        self.assertEqual(rec['logged_by'], 'jenna')
        # a client's call logged on purpose stays private
        LG.reset_memory()
        client = self._payload(company='Acme')
        rec2, _e, _c = CALLS.log_on_purpose(None, body, by='jenna', load_twin=lambda k: client)
        self.assertTrue(rec2['logged'])
        self.assertFalse(rec2['public'])

    def test_refused_once_the_result_is_knowable_or_the_call_is_incomplete(self):
        payload = self._payload()
        late = {'twin_key': 'k', 'session_id': 's1', 'question_id': 'q1', 'due_at': '2026-11-04', 'check': 'certified', 'scored_as': 'winner'}
        self.assertEqual(CALLS.log_on_purpose(None, late, by='jenna', load_twin=lambda k: payload, today='2026-11-18')[2], 409)
        bad = dict(late, due_at='2099-01-01', check='a hunch')
        self.assertEqual(CALLS.log_on_purpose(None, bad, by='jenna', load_twin=lambda k: payload)[2], 400)
        nowin = dict(late, due_at='2099-01-01', question_id='nope')
        self.assertEqual(CALLS.log_on_purpose(None, nowin, by='jenna', load_twin=lambda k: payload)[2], 400)
        self.assertEqual(LG.load_index(None)['records'], [])


class DailyCheck(unittest.TestCase):
    def setUp(self):
        LG.reset_memory()
        self.sent = []
        self.internal = []

    def _run(self, now, **kw):
        kw.setdefault('lookup', lambda rec, spec: None)
        kw.setdefault('aired_fn', lambda twin, sess: None)
        kw.setdefault('client_send', lambda **m: (self.sent.append(m) or {'sent': True}))
        kw.setdefault('internal_send', lambda to, subject, body: (self.internal.append((tuple(to), subject, body)) or {'sent': True}))
        kw.setdefault('users', {'users': {'carol': {'email': 'carol@acme.com', 'first_name': 'Carol'}}})
        return ST.run(None, now=now, **kw)

    def test_a_counted_call_settles_from_our_own_count(self):
        r = LG.record_prediction(None, 'call', 'Gilmore Girls', {'point': 41_000, 'unit': 'people'}, measure='behavior', session_id='s1', company='Acme',
                                 created_at='2026-10-20T00:00:00Z', call=_counted_call())
        conn = FakeConn(count=(1_301,))
        res = self._run(T0, connect=lambda: conn)
        self.assertEqual(res['counts'], {'settled': 1})
        rec = LG.load_record(None, r['id'])
        self.assertEqual(rec['outcome']['source_class'], 'our own count')
        self.assertEqual(rec['outcome']['value'], COUNT.project(1_301))
        self.assertTrue(any('multiSearchAnyCaseInsensitive' in s and 'hulu.com' in s for s in conn.sql))
        self.assertEqual(self._run(T0 + DAY, connect=lambda: FakeConn(count=(9,)))['checked'], 0, 'a settled call is never checked again')
        self.assertEqual(LG.load_record(None, r['id'])['outcome']['value'], COUNT.project(1_301), 'a miss stays as it landed')

    def test_a_published_result_settles_only_once_it_is_found_with_a_source(self):
        spec = CALLS.make_call(resolver='certified', comparator='winner', due_at='2026-11-10', knowable_at='2026-11-09',
                               measure={'subject': 'Runoff voters', 'what': 'Who wins the runoff', 'options': ['A', 'B']})
        r = LG.record_prediction(None, 'call', 'Runoff voters', {'call': 'A', 'distribution': [{'label': 'A', 'pct': 55.0}, {'label': 'B', 'pct': 45.0}], 'unit': 'pct'},
                                 session_id='s2', company='Crosswalk', logged=True, created_at='2026-11-01T00:00:00Z', call=spec)
        guess = self._run(T0, lookup=lambda rec, s: {'known': True, 'winner': 'A', 'source': '', '_searches': 0})
        self.assertEqual(guess['counts'], {'retry': 1}, 'an answer with no source and no search is never taken')
        self.assertEqual(self._run(T0 + 3600, lookup=lambda rec, s: {'known': True, 'winner': 'A', 'source': 'x'})['counts'], {'waiting': 1}, 'one check a day')
        found = self._run(T0 + DAY, lookup=lambda rec, s: {'known': True, 'winner': 'A', 'distribution': [{'label': 'A', 'pct': 54.2}, {'label': 'B', 'pct': 45.8}],
                                                              'source': 'the secretary of state', 'source_class': 'certified result', 'as_of': '2026-11-16', '_searches': 3})
        self.assertEqual(found['counts'], {'settled': 1})
        rec = LG.load_record(None, r['id'])
        self.assertTrue(rec['score']['hit'])
        self.assertEqual(rec['score']['tvd'], 0.8)
        pub = LG.public_rows(None, site=True)[0]
        self.assertEqual(pub['outcome_class'], 'certified result')
        self.assertNotIn('secretary', json.dumps(pub).lower(), 'the public page never names a source')

    def test_a_pilot_forecast_finds_its_air_date_then_settles(self):
        spec = CALLS.make_call(resolver='published', comparator='number', due_rule='seven days after the title is released, plus a day',
                               measure={'subject': 'NCIS', 'what': 'People who watch the episode within seven days of release, on every screen.', 'metric': 'week_one_audience'})
        r = LG.record_prediction(None, 'forecast', 'NCIS', {'point': 9_800_003, 'unit': 'people'}, measure='week_one_audience', twin_key='k1', session_id='pt1',
                                 company='Crosswalk', created_at='2026-10-01T00:00:00Z', call=spec)
        payload = {'pilot_sessions': [{'id': 'pt1'}]}
        calls = []

        def aired(twin, sess):
            calls.append(1)
            return {'aired': False} if len(calls) == 1 else {'aired': True, 'aired_on': '2026-10-06', 'people': 10_250_003}
        self.assertEqual(self._run(T0, aired_fn=aired, load_twin=lambda k: payload)['counts'], {'waiting': 1})
        self.assertEqual(self._run(T0 + DAY, aired_fn=aired, load_twin=lambda k: payload)['counts'], {'waiting': 1}, 'the air date is checked once a week')
        self.assertEqual(len(calls), 1)
        res = self._run(T0 + 8 * DAY, aired_fn=aired, load_twin=lambda k: payload, lookup=lambda rec, s: {'known': True, 'value': 10_250_003, 'source': 'the network', '_searches': 2})
        self.assertEqual(res['counts'], {'settled': 1})
        rec = LG.load_record(None, r['id'])
        self.assertEqual(rec['call']['due_at'], '2026-10-14')
        self.assertEqual(rec['call']['measure']['window_end'], '2026-10-13')
        self.assertFalse(rec['post_hoc'])
        self.assertAlmostEqual(rec['score']['rel_error'], 0.0439, places=3)

    def test_a_call_made_after_its_result_was_knowable_is_post_hoc(self):
        spec = CALLS.make_call(resolver='published', comparator='number', due_at='2026-10-14', knowable_at='2026-10-06',
                               measure={'subject': 'Late title', 'what': 'Week one', 'metric': 'week_one_audience'})
        r = LG.record_prediction(None, 'forecast', 'Late title', {'point': 1_000_003, 'unit': 'people'}, measure='week_one_audience', session_id='late',
                                 company='Crosswalk', logged=True, created_at='2026-10-07T00:00:00Z', call=spec)
        self._run(T0, lookup=lambda rec, s: {'known': True, 'value': 1_100_009, 'source': 'the network', '_searches': 1})
        rec = LG.load_record(None, r['id'])
        self.assertTrue(rec['post_hoc'])
        self.assertFalse(rec['public'])
        self.assertEqual(LG.summary(None)['n_scored'], 0, 'a post hoc call counts toward nothing')

    def test_the_client_is_asked_once_and_their_result_scores_the_call(self):
        spec = CALLS.make_call(resolver='client', comparator='number', due_at='2026-11-12', measure={'subject': 'NCIS', 'what': 'Season completion', 'metric': 'season_completion_pct'})
        r = LG.record_prediction(None, 'forecast', 'NCIS', {'point': 61.3, 'unit': 'pct'}, measure='season_completion_pct', session_id='pt2', company='Acme',
                                 created_by='carol', created_at='2026-10-01T00:00:00Z', call=spec)
        self.assertEqual(self._run(T0)['counts'], {'asked': 1})
        self.assertEqual(self._run(T0 + DAY)['counts'], {'waiting': 1})
        self.assertEqual(len(self.sent), 1)
        m = self.sent[0]
        self.assertEqual(m['to'], 'carol@acme.com')
        self.assertTrue(m['instructed'])
        self.assertIn('Hi Carol,', m['body'])
        self.assertIn('record=1', m['body'])
        self.assertIsNone(BANNED.search(m['subject'] + ' ' + m['body']), m['body'])
        self.assertTrue(LG.record_outcome(None, r['id'], 58.9, source_class='client'))
        self.assertEqual(LG.load_record(None, r['id'])['outcome']['source_class'], 'client')
        self.assertEqual(LG.outcome_label('client'), 'client results')

    def test_one_runner_and_five_failed_days_alert_jenna_and_jessie(self):
        self.assertTrue(ST.acquire_lock(None, 'someone-else', T0))
        self.assertEqual(self._run(T0 + 60)['ran'], False, 'another runner holds the lock')
        ST.release_lock(None, 'someone-else')
        spec = CALLS.make_call(resolver='published', comparator='number', due_at='2026-11-01', measure={'subject': 'X', 'what': 'Opening weekend', 'metric': 'gross'})
        LG.record_prediction(None, 'call', 'X', {'point': 30_000_001, 'unit': 'usd'}, measure='opening', session_id='x', company='Crosswalk', created_at='2026-10-01T00:00:00Z', call=spec)
        for d in range(5):
            self._run(T0 + d * DAY)
        self.assertEqual(len(self.internal), 1)
        to, subject, body = self.internal[0]
        self.assertEqual(to, ST.ALERT_TO)
        self.assertNotIn('liz@crosswalknyc.com', to)
        self.assertIsNone(BANNED.search(subject + ' ' + body), body)
        self._run(T0 + 5 * DAY)
        self.assertEqual(len(self.internal), 1, 'one alert a week per call')

    def test_the_weekly_summary_goes_to_jenna_jessie_and_liz_when_something_is_new(self):
        sent = []
        quiet = ST.weekly_summary(None, now=T0, send=lambda to, s, b: (sent.append((to, s, b)) or {'sent': True}))
        self.assertFalse(quiet['sent'])
        r = LG.record_prediction(None, 'call', 'Runoff voters', {'call': 'A', 'distribution': [{'label': 'A', 'pct': 55.0}, {'label': 'B', 'pct': 45.0}], 'unit': 'pct'},
                                 session_id='w1', company='Crosswalk', logged=True, created_at='2026-11-01T00:00:00Z')
        LG.record_outcome(None, r['id'], winner='B', source_class='certified result', observed_at=iso(T0 - DAY))
        res = ST.weekly_summary(None, now=T0, send=lambda to, s, b: (sent.append((to, s, b)) or {'sent': True}))
        self.assertTrue(res['sent'])
        to, subject, body = sent[-1]
        self.assertEqual(tuple(to), ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com', 'liz@crosswalknyc.com'))
        self.assertIn('0 called right, 1 called wrong', body)
        self.assertIsNone(BANNED.search(subject + ' ' + body), body)
        again = ST.weekly_summary(None, now=T0 + 3600, send=lambda to, s, b: (sent.append((to, s, b)) or {'sent': True}))
        self.assertFalse(again['sent'], 'the next summary covers only what is new since this one')

    def test_no_mail_leaves_a_test(self):
        self.assertEqual(ST._send_internal(ST.SUMMARY_TO, 's', 'b')['reason'], 'test mode')


class Counted(unittest.TestCase):
    def test_the_count_by_title_window_and_return(self):
        conn = FakeConn(ret=(800, 512))
        got = COUNT.settle(_counted_call(metric='return_pct', we='2026-11-15'), connect=lambda: conn)
        self.assertEqual(got['value'], 64.0)
        self.assertIn('512 of 800', got['note'])
        with self.assertRaises(COUNT.Unavailable):
            COUNT.title_viewers('Missing', 'Hulu', '2026-11-01', '2026-11-08', connect=lambda: FakeConn(mapped=()))
        with self.assertRaises(ValueError):
            COUNT.settle(_counted_call(metric='finish_pct'), connect=lambda: conn)
        self.assertEqual(COUNT.project(10_000), 329_901, 'a projected count never lands on a trailing zero')
        self.assertFalse(COUNT.has_title('Anything'), 'a test process never opens the warehouse')
        self.assertEqual(COUNT.host_for('Paramount Plus'), 'paramountplus.com')


class Routes(unittest.TestCase):
    def setUp(self):
        from test_digital_twin_pricing import Harness
        LG.reset_memory()
        self.h = Harness(self)
        self.s3 = self.h.s3

    def tearDown(self):
        self.h.close()

    def _seed(self):
        acme = LG.record_prediction(self.s3, 'forecast', 'Jennifer Beals', {'point': 61.3, 'unit': 'pct'}, measure='season_completion_pct', session_id='pt9', company='Acme',
                                    created_by='carol', call=CALLS.make_call(resolver='client', comparator='number', due_at='2026-11-12',
                                                                             measure={'subject': 'Jennifer Beals', 'what': 'Season completion', 'metric': 'season_completion_pct'}))
        practice = LG.record_prediction(self.s3, 'room_count', 'Amazon Prime Video', {'distribution': [{'label': 'Netflix', 'pct': 30.3}], 'unit': 'pct'},
                                        measure='share:Netflix', session_id='r1', question_id='q1', question='Which app do you open first?', company='Crosswalk')
        logged = LG.record_prediction(self.s3, 'call', 'Texas runoff voters', {'call': 'A', 'distribution': [{'label': 'A', 'pct': 56.1}, {'label': 'B', 'pct': 43.9}], 'unit': 'pct'},
                                      session_id='r2', question_id='q2', company='Crosswalk', logged=True)
        LG.record_outcome(self.s3, logged['id'], winner='A', source_class='certified result')
        return acme, practice, logged

    def test_the_public_record_reads_live_with_no_sign_in(self):
        acme, practice, logged = self._seed()
        import flask
        import digital_twins_api as A
        app = flask.Flask('anon')
        A.register(app)
        r = app.test_client().get('/site/api/twins-track-record')
        self.assertEqual(r.status_code, 200)
        self.assertIn('max-age=300', r.headers.get('Cache-Control', ''))
        doc = r.get_json()
        self.assertEqual([x['subject'] for x in doc['rows']], ['Texas runoff voters'])
        for k in ('company', 'twin_key', 'session_id', 'id'):
            self.assertNotIn(k, doc['rows'][0])
        self.assertEqual(doc['summary']['calls_scored'], 1)

    def test_a_client_adds_its_own_result_and_never_sees_practice_rooms(self):
        acme, practice, logged = self._seed()
        d = self.h.c.get('/api/digital-twins/track-record').get_json()
        self.assertEqual([x['subject'] for x in d['open']], ['Jennifer Beals'])
        self.assertTrue(d['open'][0]['can_add'])
        self.assertEqual(d['open'][0]['check'], 'your own results')
        self.assertNotIn('Amazon Prime Video', json.dumps(d))
        bad = self.h.c.post(f"/api/digital-twins/ledger/{acme['id']}/result", json={'value': 'lots'})
        self.assertEqual(bad.status_code, 400)
        ok = self.h.c.post(f"/api/digital-twins/ledger/{acme['id']}/result", json={'value': '58.9%', 'note': 'our own dashboard'})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        again = self.h.c.post(f"/api/digital-twins/ledger/{acme['id']}/result", json={'value': '60.1'})
        self.assertEqual(again.status_code, 409, 'a scored call is never revised')
        other = self.h.c.post(f"/api/digital-twins/ledger/{practice['id']}/result", json={'value': '1'})
        self.assertEqual(other.status_code, 404, 'a client never touches a Crosswalk row')
        rec = LG.load_record(self.s3, acme['id'])
        self.assertEqual((rec['outcome']['value'], rec['outcome']['source_class']), (58.9, 'client'))
        self.assertFalse(rec['public'], 'a client call stays private until an admin releases it')

    def test_staff_log_a_call_and_clients_cannot(self):
        no = self.h.c.post('/api/digital-twins/ledger/log', json={'ledger_id': 'x'})
        self.assertEqual(no.status_code, 403)
        acme, practice, logged = self._seed()
        self.h.as_staff()
        r = self.h.c.post('/api/digital-twins/ledger/log', json={'ledger_id': acme['id'], 'due_at': '2099-03-01', 'check': 'client', 'scored_as': 'number',
                                                                 'what': 'Season completion from your own results'})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertFalse(r.get_json()['public'], "a client's call logged by staff still waits for a release")
        late = self.h.c.post('/api/digital-twins/ledger/log', json={'ledger_id': practice['id'], 'due_at': '2026-11-01', 'check': 'counted', 'scored_as': 'number', 'what': 'x'})
        self.assertIn(late.status_code, (400, 409))
        d = self.h.c.get('/api/digital-twins/track-record').get_json()
        self.assertTrue(d['staff'])
        self.assertIn('Amazon Prime Video', json.dumps(d), 'staff read every row')


class Page(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as f:
            self.src = f.read()
        with open(os.path.join(ROOT, 'site', 'twins-track-record.html'), encoding='utf-8') as f:
            self.site = f.read()

    def test_the_drawer_carries_add_your_result_the_due_day_and_log_a_call(self):
        for needle in ('function recAddForm(r)', "dtwRecAdd(\\'", "'/ledger/' + encodeURIComponent(id) + '/result'", 'function recLogForm(d)', "api('/ledger/log'",
                       "r.due_on || 'Not set yet'", "Settled by ' + esc(r.check)", "(staff ? recLogForm(d) : '')", 'fromUrl.record'):
            self.assertIn(needle, self.src, needle)

    def test_the_drawer_renders_in_plain_words(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which('node'):
            self.skipTest('node is not installed')

        def fn(name):
            a = self.src.index('            function ' + name + '(')
            b = self.src.index('\n            function ', a + 10)
            return self.src[a:b]
        js = '\n'.join(fn(n) for n in ('recUnit', 'recSaid', 'recMiss', 'recKind', 'recCap', 'recTable', 'recAddForm', 'recLogForm', 'renderRecord'))
        doc = {'success': True, 'staff': True, 'line': 'Every call the twins made before its result, next to what happened.',
               'summary': {'calls_scored': 1, 'hit_rate': 1.0, 'mean_relative_miss': 0.04},
               'rows': [{'id': 'a', 'subject': 'Texas runoff voters', 'what': 'Who wins the runoff', 'kind': 'call', 'unit': 'pct', 'said': 'A', 'happened': 'A', 'hit': True,
                         'outcome_class': 'certified result', 'called_on': '2026-11-01', 'settled_on': '2026-11-16', 'house': True, 'company': 'Crosswalk', 'public': True}],
               'open': [{'id': 'b', 'subject': 'Jennifer Beals', 'what': 'Season completion', 'kind': 'forecast', 'unit': 'pct', 'said': 61.3, 'firmness': 'early',
                         'due_on': '2026-12-12', 'check': 'your own results', 'scored_as': 'number', 'called_on': '2026-11-02', 'can_add': True, 'company': 'Acme'}]}
        node = ("var window = {}; var S = {runs: [{key: 'k', name: 'Paramount+'}], recAdd: 'b', recLog: null}; var DOM = {};"
                "function el(id) { return DOM[id] || (DOM[id] = {innerHTML: '', textContent: '', value: ''}); }"
                "function esc(x) { return String(x == null ? '' : x).replace(/[&<>\"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]; }); }"
                "function jsq(s) { return String(s || ''); } function fmtN(n) { return String(n); } function twinOptionLabel(r) { return r.name; }"
                "function pct(v) { return v + '%'; } function api() { return Promise.resolve({}); }\n" + js +
                "\nrenderRecord(" + json.dumps(doc) + "); console.log(DOM['dtwRecordBody'].innerHTML);")
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(node)
            path = f.name
        out = subprocess.run(['node', path], capture_output=True, text=True, timeout=60)
        os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        text = re.sub(r'<[^>]+>', ' ', out.stdout)
        for words in ('Add your result', 'Log a call', 'Settled by your own results', '2026-12-12', 'certified result', 'Once it is added the call is scored'):
            self.assertIn(words, text, words)
        self.assertIsNone(BANNED.search(text), BANNED.search(text) and BANNED.search(text).group(0))

    def test_the_public_page_reads_the_record_live_with_the_file_as_fallback(self):
        self.assertIn("fetch('/site/api/twins-track-record'", self.site)
        self.assertIn("fetch('assets/data/twins-track-record.json'", self.site)
        self.assertLess(self.site.index("'/site/api/twins-track-record'"), self.site.index("'assets/data/twins-track-record.json'"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
