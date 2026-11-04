#!/usr/bin/env python3
"""Big asks land whole, never empty, never contradicting themselves
(2026-11-04, Liz on the Music to Long Form study). Hermetic.

The three defects her agent reported, each pinned here:

16 blocks   a 12-group by 3-dimension breakdown is kept whole in the
            result, the ledger entry and the CSV (every row, every
            bucket); the chat shows the top rows and names the file.
ran empty   a resumed read that comes back with an offer or a clarify
            never lands as the result: the plain-words line lands once,
            the ask log settles 'faulted' once, a re-ask runs fresh; the
            resume record carries the base and the screen it was
            started on.
contradict  a second pass on a stored figure for the same definition
            and window keeps the stored figure and says so; a view's
            reads bank under the view's own title.
matrix      a groups by dimensions ask splits deterministically, pieces
            retry once, a piece that still fails is named in the reply,
            the API envelope carries the file link and the table facts.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as IL  # noqa: E402
import prometheus_analysis as PMA  # noqa: E402
from prometheus import contract_v1 as CV  # noqa: E402
from prometheus import figure_binding as FB  # noqa: E402
from prometheus import matrix_read as MX  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402
from prometheus import read_registry as REG  # noqa: E402
from prometheus import view_base as VB  # noqa: E402

GROUPS = ['Family animated', 'Superhero', 'Action', 'Comedy', 'Drama', 'Other',
          'Sci-fi and fantasy', 'Romance drama', 'Comedy and dramedy', 'Series drama', 'Thriller and crime', 'Reality']
DIMS = {'Age': ['18-24', '25-34', '35-44', '45-54', '55-64', '65 or older'],
        'Gender': ['Female', 'Male', 'Non-binary', 'Another gender identity'],
        'Income': ['Under $25,000', '$25,000 to $49,999', '$50,000 to $74,999', '$75,000 to $99,999',
                   '$100,000 to $149,999', '$150,000 or more']}


def _row(group, dim, pool, seed):
    bands = DIMS[dim]
    n = len(bands)
    weights = [(seed * 7 + i * 13) % 23 + 5 for i in range(n)]
    tot = float(sum(weights))
    counts = [int(pool * w / tot) for w in weights]
    counts[0] += pool - sum(counts)
    return {'label': f"{group}: {dim}", 'share_pct': 1.0, 'pool': pool, 'group': group, 'dimension': dim,
            'buckets': [{'label': b, 'count': c, 'share_pct': round(100.0 * c / pool, 1)} for b, c in zip(bands, counts)]}


def matrix_rows():
    rows, seed = [], 3
    for gi, g in enumerate(GROUPS):
        pool = 1_204_311 + gi * 97_013
        for d in DIMS:
            rows.append(_row(g, d, pool, seed))
            seed += 1
    return rows


def matrix_data():
    return {'subject': 'Music to Long Form', 'metric_family': 'audience',
            'window_label': 'Nov 4 2025 to Nov 4 2026', 'window_start': '2025-11-04', 'window_end': '2026-11-04',
            'headline': 'All twelve genre groups by age, gender and income.',
            'reads': ['Family animated runs youngest.'],
            'metrics': [{'name': f"{g.lower().replace(' ', '_')}_pool", 'label': f"{g}, pool", 'value': 1_204_311 + i * 97_013,
                         'unit': 'count', 'definition': 'title viewers in the group'} for i, g in enumerate(GROUPS)],
            'cohort': 'title viewers by genre group',
            'breakdown': {'dimension': 'Genre group by Age, Gender, Income', 'share_basis': 'share within the group',
                          'rows': matrix_rows()}}


class NoSilentCap(unittest.TestCase):
    def test_result_keeps_every_row_and_bucket(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        rows = res['breakdown']['rows']
        self.assertEqual(len(rows), 36)                      # 12 groups x 3 dimensions, not 16
        self.assertTrue(all(len(r['buckets']) >= 4 for r in rows))
        self.assertEqual(len(res['metrics']), 12)            # the context stats are not cut to three
        self.assertAlmostEqual(sum(r['share_pct'] for r in rows), 100.0, places=2)

    def test_single_row_distribution_is_a_table(self):
        one = {'subject': 'X', 'metrics': [], 'breakdown': {'dimension': 'Income', 'share_basis': 'share within the cut',
               'rows': [_row('Series no music touch', 'Income', 2_517_849, 5)]}}
        res = PMA.enforce_metrics_coherence(one)
        self.assertIsNotNone(res['breakdown'])
        self.assertEqual(len(res['breakdown']['rows'][0]['buckets']), 6)

    def test_ledger_entry_stores_the_whole_table(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        entry = IL.make_entry(subject='Music to Long Form', metric_family='audience', question='genre demographics',
                              route='reasoned_metrics', metrics=res['metrics'], breakdown=res['breakdown'],
                              window_start='2025-11-04', window_end='2026-11-04', reply='x' * 9000)
        self.assertEqual(len(entry['breakdown']['rows']), 36)
        self.assertEqual(len(entry['metrics']), 12)
        self.assertTrue(all(r.get('buckets') and r.get('pool') for r in entry['breakdown']['rows']))
        self.assertEqual(len(entry['reply']), 9000)          # the stored reply is no longer cut at 4,000
        block = IL.render_block([entry])
        self.assertIn('Family animated: Gender', block)
        self.assertIn('Female', block)                        # the distribution is a binding line, not a sentence

    def test_csv_exports_every_row_and_bucket(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        name, text = PMA.build_generated_csv({'subject': res['subject'], 'cohort': res['cohort'],
                                              'metrics': res['metrics'], 'breakdown': res['breakdown']})
        rows = list(csv.reader(io.StringIO(text)))
        header, body = rows[0], [r for r in rows[1:] if r and r[0] and r[0] != 'Note']
        self.assertEqual(header[:3], ['Group', 'Dimension', 'Bucket'])
        self.assertEqual(len(body), 12 * (6 + 4 + 6))        # one line per group x dimension x bucket
        self.assertEqual(len({(r[0], r[1]) for r in body}), 36)
        self.assertIn('Reality', {r[0] for r in body})
        self.assertTrue(name.endswith('.csv'))

    def test_chat_reply_shows_top_rows_and_names_the_file(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        res['_matrix'] = {'groups': 12, 'dimensions': 3}
        reply = PMA.format_generated_metrics_reply(res)
        self.assertEqual(sum(1 for ln in reply.split('\n') if ln.startswith('- ') and ': ' in ln and 'share' in ln), PMA.CHAT_BREAKDOWN_ROWS)
        self.assertIn('The full table, all 12 groups by every dimension (3 dimensions, 36 rows), is in the file under this answer.', reply)
        self.assertNotIn('\u2014', reply)

    def test_missing_piece_is_named(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        res['_missing'] = ['Horror series by Income']
        reply = PMA.format_generated_metrics_reply(res)
        self.assertIn('Horror series by Income did not come back; ask for it again and it runs alone.', reply)


class MatrixPlan(unittest.TestCase):
    ASK = ("For the Music to Long Form study (61 titles), the title viewers split into twelve genre groups, which are "
           "Crosswalk assignments by title: Family animated (Inside Out 2, Moana 2, The Wild Robot); Superhero (Deadpool & Wolverine, "
           "Guardians of the Galaxy Vol. 3); Action (Top Gun: Maverick, Twisters); Comedy (Barbie, Anyone But You); Drama (It Ends with Us, "
           "Oppenheimer); Other (A Minecraft Movie, Sinners); Sci-fi and fantasy (Stranger Things, Wednesday, Fallout); Romance drama "
           "(Bridgerton, Virgin River); Comedy and dramedy (The White Lotus, The Bear, Beef); Series drama (Euphoria, Landman, The Crown); "
           "Thriller and crime (You, Outer Banks, Paradise); Reality (Love Island, Love is Blind, Bachelor). For each group give Age "
           "(18-24, 25-34, 35-44, 45-54, 55-64, 65 or older), Gender (Female, Male, Non-binary, Another gender identity) and Income "
           "(Under $25,000, $25,000 to $49,999, $50,000 to $74,999, $75,000 to $99,999, $100,000 to $149,999, $150,000 or more), "
           "pool count and share within the group.")

    def test_twelve_by_three_splits_deterministically(self):
        p1, p2 = MX.plan(self.ASK), MX.plan(self.ASK)
        self.assertIsNotNone(p1)
        self.assertEqual(p1, p2)
        self.assertEqual(len(p1['groups']), 12)
        self.assertEqual(p1['dimensions'], ['Age', 'Gender', 'Income'])
        self.assertEqual(p1['cells'], 36)
        self.assertEqual(len(p1['pieces']), 12)
        self.assertEqual([pc['group'] for pc in p1['pieces']], p1['groups'])
        self.assertEqual(p1['buckets']['Income'][1], '$25,000 to $49,999')   # the thousands comma is not a band boundary
        self.assertIn('12 groups by 3 dimensions (36 cells), in 12 pieces', MX.describe(p1))

    def test_small_asks_run_in_one_pass(self):
        small = ("Series side only, three path cuts: Series music first, Series content first, Series no music touch. "
                 "Two dimensions: Income (Under $25,000, $25,000 to $49,999, $50,000 or more) and Education (High school or less, "
                 "Some college, Bachelors). Pool count and share within the cut.")
        self.assertIsNone(MX.plan(small))
        self.assertIsNone(MX.plan('Two things to reconcile before this goes to the client. (1) Your first read said ... (2) Your second read said ...'))

    def test_doors_split_film_vs_series(self):
        ask = ("Audience demographics for the three viewer doors, split film vs series (six cuts): (1) Music first: viewers who played "
               "a tracked song before the title; (2) Content first: viewers who watched first; (3) No music touch: viewers who watched "
               "without a song. For each cut give age bands, gender, income, education, household composition (children at home), "
               "region or top DMAs, streaming subscriptions and social platform use.")
        p = MX.plan(ask)
        self.assertEqual(p['groups'], ['Film - Music first', 'Film - Content first', 'Film - No music touch',
                                       'Series - Music first', 'Series - Content first', 'Series - No music touch'])
        self.assertEqual(len(p['dimensions']), 8)
        self.assertEqual(len(p['pieces']), 12)               # six groups, two dimension chunks each

    def test_piece_question_pins_the_base_window_and_bands(self):
        p = MX.plan(self.ASK)
        q = MX.piece_question(self.ASK, p, p['pieces'][2])
        self.assertIn('THIS PIECE: Action only', q)
        self.assertIn('same base, the same window and the same definitions', q)
        self.assertIn("Label each row 'Action: <dimension>'", q)
        self.assertIn('Income bands, in this order: Under $25,000, $25,000 to $49,999', q)

    def test_run_retries_once_and_names_the_piece_that_failed(self):
        p = MX.plan(self.ASK)
        calls = {}

        def core(piece_text, label):
            calls[label] = calls.get(label, 0) + 1
            if label == 'Drama':
                return None                                   # fails every time
            if label == 'Reality' and calls[label] == 1:
                return None                                   # fails once, then lands
            group = label
            return {'subject': 'Music to Long Form', 'window_label': 'Nov 4 2025 to Nov 4 2026',
                    'window_start': '2025-11-04', 'window_end': '2026-11-04',
                    'metrics': [{'name': 'pool', 'label': f'{group}, pool', 'value': 1_000_003 + len(group), 'unit': 'count'}],
                    'reads': [f'{group} reads young.'],
                    'breakdown': {'dimension': 'Genre group', 'share_basis': 'share within the group',
                                  'rows': [_row(group, d, 1_000_003 + len(group), i) for i, d in enumerate(DIMS)]}}

        results = MX.run(p, core=core, text=self.ASK, stage_cb=lambda s: None, max_workers=3)
        self.assertEqual(calls['Drama'], 2)
        self.assertEqual(calls['Reality'], 2)
        self.assertEqual(calls['Action'], 1)
        self.assertEqual(len(results), 11)
        res = MX.assemble(p, results, subject='Music to Long Form', text=self.ASK)
        self.assertEqual(len(res['breakdown']['rows']), 33)
        self.assertEqual(res['_missing'], ['Drama'])
        self.assertEqual(res['_matrix']['failed'], ['Drama'])
        self.assertEqual(res['window_start'], '2025-11-04')
        self.assertEqual({r['group'] for r in res['breakdown']['rows']} & {'Drama'}, set())
        self.assertTrue(all(r['dimension'] in DIMS for r in res['breakdown']['rows']))
        reply = PMA.format_generated_metrics_reply(PMA.enforce_metrics_coherence(res) | {'_matrix': res['_matrix'], '_missing': res['_missing']})
        self.assertIn('Drama did not come back; ask for it again and it runs alone.', reply)
        self.assertIn('is in the file under this answer.', reply)

    def test_stage_lines_show_progress(self):
        p = MX.plan(self.ASK)
        seen = []
        MX.run(p, core=lambda q, l: {'metrics': [{'name': 'x', 'value': 1}]}, text=self.ASK, stage_cb=seen.append, max_workers=1)
        self.assertEqual(seen[-1], 'piece 12 of 12: Reality')
        self.assertTrue(any(s.startswith('piece 1 of 12') for s in seen))


class NeverEmpty(unittest.TestCase):
    OFFER = {'success': True, 'action': 'answer', 'panel_offer': {'kind': 'report', 'price_label': '$100', 'subject': 'Digital Journey IQ'},
             'reply': 'I can pull that read on Digital Journey IQ. Pulling this data will cost $100.',
             'followups': ['Approve ($100, not billed to a staff account)', 'Cancel']}
    CLARIFY = {'success': True, 'action': 'answer', 'followups': [],
               'reply': 'Which subject should this be on? Name the show, brand or person (one line is enough) and I will take it from there.'}
    REPLAY = {'success': True, 'action': 'answer', 'followups': [], 'file_link': {'url': 'https://x/y.csv', 'label': 'Download y.csv'},
              'reply': 'On Music to Long Form - Series no music touch:\n\nIncome mix ...' + 'x' * 300}

    def test_offers_and_clarifies_are_not_answers(self):
        self.assertFalse(RR.is_real_answer(self.OFFER))
        self.assertFalse(RR.is_real_answer(self.CLARIFY))
        self.assertTrue(RR.is_real_answer(self.REPLAY))

    def test_fault_line_names_the_cut_as_typed(self):
        st = {'subject': 'Music to Long Form',
              'question': 'The last run returned no result blocks. Run it again, one cut only so it fits in one read: Series no music touch '
                          '(series viewers with no music touch), two dimensions, Income and Education, share within the cut.'}
        rr = {'base': {'subject': 'Music to Long Form', 'source': 'view', 'product': 'Digital Journey IQ'}}
        line = RR.fault_reply(st, 'no_base', rr)
        self.assertIn('Series no music touch', line)
        self.assertIn('Music to Long Form', line)
        self.assertIn('Open Digital Journey IQ', line)
        self.assertIn('no figures to give', line)
        self.assertNotIn('Pulling this data will cost', line)
        for banned in ('synthetic', 'model', 'estimated', 'panel', 'pipeline', 'ledger', 'lane', 'job'):
            self.assertNotIn(banned, line.lower())
        self.assertNotIn('\u2014', line)

    def test_sync_offer_settles_as_one_fault(self):
        writes, appended, settled = [], [], []

        class FakeChat:
            def _pm_read_status_write(self, job_id, doc):
                writes.append(doc)

            def _pm_panel_refund(self, charge):
                pass

            def _pm_thread_for_job(self, u, j):
                return 'tid'

            def _pm_load_thread_or_active(self, u, t):
                return list(appended)

            def _pm_history_has_job_turn(self, h, k, j):
                return any(((t.get('meta') or {}).get(k) == j) for t in h)

            def _pm_iso_now(self):
                return '2026-11-04T18:00:00Z'

            def _pm_save_thread_or_active(self, u, t, h):
                appended[:] = h

            def _pm_settle_ask(self, job_id, outcome, reply='', started_at=None):
                settled.append((job_id, outcome, reply))

            def _pm_notify_delete(self, job_id):
                pass

            def _pm_append_read_to_history(self, *a, **k):
                raise AssertionError('an offer never lands as a read')

        fake = FakeChat()
        orig_chat, orig_s3 = RR._chat, RR._s3
        RR._chat = lambda: fake
        RR._s3 = lambda: (None, 'bucket')
        try:
            status = {'job_id': 'abc123def456', 'user': 'liz', 'status': 'working', 'subject': 'Music to Long Form',
                      'question': 'Go ahead and run it that way: Series - No music touch, every bucket for Age, Gender, Income and Education.',
                      'started_at': 1.0}
            rr = {'text': status['question'], 'base': {'subject': 'Music to Long Form', 'source': 'view', 'product': 'Digital Journey IQ'}}
            out = RR.settle_sync_result('abc123def456', status, self.OFFER, rr=rr, uname='liz')
            self.assertEqual(out, 'final')
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]['status'], 'done')
            self.assertEqual(writes[0]['fault'], 'no_base')
            self.assertEqual(writes[0]['payload']['faulted'], 'no_base')
            self.assertNotIn('Pulling this data will cost', writes[0]['payload']['reply'])
            self.assertEqual(len(appended), 1)
            self.assertEqual(appended[0]['meta']['read_job_id'], 'abc123def456')
            self.assertEqual([s[1] for s in settled], ['faulted'])
            self.assertIn('Series - No music touch', settled[0][2])
            # the same settle again adds nothing: once
            RR.settle_sync_result('abc123def456', status, self.OFFER, rr=rr, uname='liz')
            self.assertEqual(len(appended), 1)
        finally:
            RR._chat, RR._s3 = orig_chat, orig_s3

    def test_faulted_record_never_replays(self):
        class S3:
            def __init__(self):
                self.docs = {REG.doc_key('liz'): json.dumps({'k': 1}).encode()}

            def get_object(self, Bucket, Key):
                if Key not in self.docs:
                    raise KeyError(Key)
                return {'Body': io.BytesIO(self.docs[Key])}

            def put_object(self, Bucket, Key, Body, **kw):
                self.docs[Key] = Body
        s3 = S3()
        REG.mark(s3, 'b', 'liz', 'run the income split', 'job000000001', subject='X')
        s3.docs['system/prometheus_reads/job000000001.json'] = json.dumps({
            'job_id': 'job000000001', 'status': 'done', 'fault': 'no_base',
            'payload': {'reply': 'I could not run the read ...', 'faulted': 'no_base'}}).encode()
        self.assertIsNone(REG.recent(s3, 'b', 'system/prometheus_reads/', 'liz', 'run the income split'))
        self.assertTrue(REG.clear(s3, 'b', 'liz', 'run the income split'))

    def test_resume_record_carries_the_base_and_the_screen(self):
        vc = {'view_id': 'journeyIQ', 'view_title': 'Digital Journey IQ',
              'data': {'title': 'Music to Long Form', 'kpis': {'viewers': 9296367}, 'facts': ['x'] * 3}}
        base = dict(VB.view_base(vc), view_context=vc)
        self.assertEqual(base['subject'], 'Music to Long Form')      # the study's own title, not the tab label
        rec = RR.resume_record(text='Series no music touch by income', history=[], base=base)
        self.assertEqual(rec['base']['subject'], 'Music to Long Form')
        self.assertEqual(rec['base']['source'], 'view')
        self.assertEqual(rec['view_context']['data']['title'], 'Music to Long Form')
        again = RR.resumed_base(rec)
        self.assertEqual(again['source'], 'view')
        self.assertEqual(again['view_context']['view_id'], 'journeyIQ')
        self.assertEqual(RR.resumed_ctx(rec)['view_context']['view_title'], 'Digital Journey IQ')
        self.assertIsNone(RR.resumed_base({'text': 'x', 'bind_subject': 'Tracker'}))   # an old record pins nothing

    def test_big_screen_is_trimmed_not_dropped(self):
        vc = {'view_id': 'journeyIQ', 'view_title': 'Digital Journey IQ',
              'data': {'title': 'Music to Long Form', 'clusters': [{'k': 'v' * 500}] * 400, 'kpis': {'a': 1}}}
        rec = RR.resume_record(text='x', history=[], base=dict(VB.view_base(vc), view_context=vc))
        self.assertLessEqual(len(json.dumps(rec['view_context'])), RR.VIEW_CONTEXT_MAX_CHARS)
        self.assertEqual(rec['view_context']['data']['kpis'], {'a': 1})


class ConsistentPasses(unittest.TestCase):
    def _stored(self):
        res = PMA.enforce_metrics_coherence({
            'subject': 'Music to Long Form', 'metric_family': 'audience', 'window_start': '2025-11-04', 'window_end': '2026-11-04',
            'cohort': 'six path cuts', 'metrics': [{'name': 'all_cuts', 'label': 'All six cuts', 'value': 9296367, 'unit': 'count'}],
            'breakdown': {'dimension': 'Path cut by Gender', 'share_basis': 'share within the cut', 'rows': [
                {'label': 'Film music first: Gender', 'share_pct': 50, 'pool': 1118963, 'buckets': [
                    {'label': 'Female', 'count': 641166, 'share_pct': 57.3}, {'label': 'Male', 'count': 448704, 'share_pct': 40.1},
                    {'label': 'Non-binary', 'count': 19022, 'share_pct': 1.7}, {'label': 'Another gender identity', 'count': 10071, 'share_pct': 0.9}]},
                {'label': 'Series music first: Gender', 'share_pct': 50, 'pool': 912419, 'buckets': [
                    {'label': 'Female', 'count': 558402, 'share_pct': 61.2}, {'label': 'Male', 'count': 332119, 'share_pct': 36.4},
                    {'label': 'Non-binary', 'count': 14599, 'share_pct': 1.6}, {'label': 'Another gender identity', 'count': 7299, 'share_pct': 0.8}]}]}})
        return IL.make_entry(subject='Music to Long Form', metric_family='audience', question='gender by door', route='reasoned_metrics',
                             metrics=res['metrics'], breakdown=res['breakdown'], window_start='2025-11-04', window_end='2026-11-04',
                             cohort='six path cuts', reply='first pass')

    def test_second_pass_keeps_the_stored_figure(self):
        stored = self._stored()
        second = PMA.enforce_metrics_coherence({
            'subject': 'Music to Long Form', 'metric_family': 'audience', 'window_start': '2025-11-04', 'window_end': '2026-11-04',
            'cohort': 'six path cuts', 'metrics': [{'name': 'all_cuts', 'label': 'All six cuts', 'value': 9100000, 'unit': 'count'}],
            'breakdown': {'dimension': 'Path cut by Gender', 'share_basis': 'share within the cut', 'rows': [
                {'label': 'Film music first: Gender', 'share_pct': 50, 'pool': 1284537, 'buckets': [
                    {'label': 'Female', 'count': 517668, 'share_pct': 40.3}, {'label': 'Male', 'count': 737324, 'share_pct': 57.4},
                    {'label': 'Non-binary', 'count': 17984, 'share_pct': 1.4}, {'label': 'Another gender identity', 'count': 11561, 'share_pct': 0.9}]},
                {'label': 'Series music first: Gender', 'share_pct': 50, 'pool': 1186427, 'buckets': [
                    {'label': 'Female', 'count': 517282, 'share_pct': 43.6}, {'label': 'Male', 'count': 643043, 'share_pct': 54.2},
                    {'label': 'Non-binary', 'count': 15424, 'share_pct': 1.3}, {'label': 'Another gender identity', 'count': 10678, 'share_pct': 0.9}]}]}})
        out, bound = FB.bind_to_stored(second, [stored], ask_text='Run the six path cuts again, same base and window.')
        rows = {r['label']: r for r in out['breakdown']['rows']}
        film = {b['label']: b for b in rows['Film music first: Gender']['buckets']}
        self.assertEqual(film['Female']['share_pct'], 57.3)      # the stored figure stands
        self.assertEqual(film['Female']['count'], 641166)
        self.assertEqual(rows['Film music first: Gender']['pool'], 1118963)
        self.assertEqual(out['metrics'][0]['value'], 9296367)
        names = [b['name'] for b in bound]
        self.assertIn('Film music first: Gender', names)
        self.assertIn('Series music first: Gender', names)
        line = FB.already_reported_line(bound)
        self.assertTrue(line.startswith('Already reported in this thread and kept as first reported: '))
        self.assertIn('Film music first: Gender', line)

    def test_a_different_window_or_a_fresh_draw_is_left_alone(self):
        stored = self._stored()
        other = PMA.enforce_metrics_coherence({
            'subject': 'Music to Long Form', 'metric_family': 'audience', 'window_start': '2025-07-01', 'window_end': '2026-06-30',
            'cohort': 'six path cuts', 'metrics': [], 'breakdown': {'dimension': 'Path cut by Gender', 'share_basis': 'x', 'rows': [
                {'label': 'Film music first: Gender', 'share_pct': 100, 'pool': 1284537, 'buckets': [
                    {'label': 'Female', 'count': 517668, 'share_pct': 40.3}, {'label': 'Male', 'count': 737324, 'share_pct': 57.4}]}]}})
        out, bound = FB.bind_to_stored(other, [stored], ask_text='Run the six path cuts on Jul 1 2025 to Jun 30 2026.')
        self.assertEqual(bound, [])
        self.assertEqual(out['breakdown']['rows'][0]['buckets'][0]['share_pct'], 40.3)
        same_win = PMA.enforce_metrics_coherence({
            'subject': 'Music to Long Form', 'metric_family': 'audience', 'window_start': '2025-11-04', 'window_end': '2026-11-04',
            'cohort': 'six path cuts', 'metrics': [], 'breakdown': {'dimension': 'Path cut by Gender', 'share_basis': 'x', 'rows': [
                {'label': 'Film music first: Gender', 'share_pct': 100, 'pool': 1284537, 'buckets': [
                    {'label': 'Female', 'count': 517668, 'share_pct': 40.3}, {'label': 'Male', 'count': 737324, 'share_pct': 57.4}]}]}})
        out2, bound2 = FB.bind_to_stored(same_win, [stored], ask_text='Independent verification. Recompute from the data without reusing any figure from earlier.')
        self.assertEqual(bound2, [])

    def test_rows_carry_their_definition_and_window(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        FB.stamp_definitions(res)
        d = res['breakdown']['rows'][0]['definition']
        self.assertIn('Family animated: Age', d)
        self.assertIn('Nov 4 2025 to Nov 4 2026', d)
        entry = IL.make_entry(subject='Music to Long Form', metric_family='audience', question='q', route='reasoned_metrics',
                              metrics=[], breakdown=res['breakdown'], reply='r')
        self.assertEqual(entry['breakdown']['rows'][0]['definition'], d)


class ApiDoor(unittest.TestCase):
    def test_envelope_carries_the_file_and_the_table(self):
        res = PMA.enforce_metrics_coherence(matrix_data())
        raw = {'success': True, 'action': 'answer', 'reply': 'On Music to Long Form: ...', 'followups': ['Email me this file'],
               'figures': [], 'metrics': res['metrics'], 'breakdown': res['breakdown'],
               'matrix': {'groups': 12, 'dimensions': 3, 'failed': []},
               'file_link': {'url': 'https://dashboard-inputs.s3.us-east-2.amazonaws.com/generated_data/abc/Digital%20Journey%20IQ.csv?X-Amz-Signature=1',
                             'label': 'Download Digital Journey IQ.csv', 'name': 'Digital Journey IQ.csv', 'key': 'generated_data/abc/Digital Journey IQ.csv'}}
        env = {'kind': 'answer', 'text': raw['reply'], 'options': [], 'success': True}
        v1 = CV.to_v1(env, raw, job_id='abc123def456')
        self.assertEqual(v1['kind'], 'answer')
        ans = v1['answer']
        self.assertEqual(ans['file_link']['name'], 'Digital Journey IQ.csv')
        self.assertTrue(ans['file_link']['url'].startswith('https://'))
        self.assertEqual(ans['files'][0]['url'], '/api/prometheus/v1/jobs/abc123def456/files/Digital Journey IQ.csv')
        self.assertEqual(ans['table'], {'groups': 12, 'dimensions': 3, 'rows': 36, 'missing': []})
        labels = [f['label'] for f in ans['facts']]
        self.assertIn('Family animated: Age: 18-24', labels)
        self.assertIn('Reality: Income: $150,000 or more share', labels)
        self.assertGreater(len(ans['facts']), 60)             # the old 60-fact cap is gone
        # the partner belt leaves the link intact
        from prometheus import guards as G
        G.scrub_partner_payload(v1)
        self.assertTrue(v1['answer']['file_link']['url'].startswith('https://'))
        self.assertEqual(v1['answer']['files'][0]['url'], '/api/prometheus/v1/jobs/abc123def456/files/Digital Journey IQ.csv')

    def test_schema_allows_the_new_answer_fields(self):
        with open(os.path.join(ROOT, "prometheus", "schemas", "ask.v1.json")) as fh:
            schema = json.load(fh)
        props = schema['properties']['answer']['properties']
        for k in ('file_link', 'files', 'table'):
            self.assertIn(k, props)


class ViewSubject(unittest.TestCase):
    def test_view_base_prefers_the_study_title(self):
        vc = {'view_id': 'journeyIQ', 'view_title': '🗺️ Digital Journey IQ', 'data': {'title': 'Music to Long Form', 'kpis': {'a': 1}}}
        self.assertEqual(VB.view_base(vc)['subject'], 'Music to Long Form')
        vc2 = {'view_id': 'journeyIQ', 'view_title': 'NCIS: New York premiere', 'data': {'title': 'ncis_ny'}}
        self.assertEqual(VB.view_base(vc2)['subject'], 'NCIS: New York premiere')
        self.assertTrue(VB.names_view('New read, Music to Long Form study (61 titles), six cuts by age and gender', vc, product_nouns=False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
