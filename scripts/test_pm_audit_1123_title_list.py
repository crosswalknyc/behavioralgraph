"""A pasted list of titles is answered title by title (Nov 23 2026 ask
audit, fix 6; Jenna 2026-11-23).

The live case, sanitized: a reader pasted five titles with their
availability windows and asked for each one's US audience on YouTube's
Free with ads shelf. What shipped: one read over the whole list whose
figures disagreed with the single-title reads she then ran; the answer
and the emailed file labeled "Including U.S. Unique viewers", a phrase
from her own text; and later, the same pasted template with a different
title at its end was answered with the FIRST title's read (the ledger
matched questions on their first 300 characters).

The rules under test:

* `title_list_read.plan` reads two or more titles off the words (a list
  after "the titles are:", a comma or line list, quoted titles, the
  Reader's titles when the words carry them), one platform for all of
  them, each title's own window when the list gives one; a single title,
  a two-title compare, a list of platforms, and a dashboard question
  make no plan.
* `piece_question` pins one title, its window and the shared platform
  and names the subject as the title, never a phrase from the text.
* `assemble` names the subject as the titles, carries each title's own
  metrics, a breakdown by title whose shares add to 100, the landed and
  missing titles; `fill` sizes a missing title at the low end of its
  landed peers and marks it; `format_reply` and `csv_rows` carry one
  block and one row per title.
* `question_universe` never reads a metric phrase ("including U.S.
  unique viewers") as the audience.
* `insights_ledger.normalize_question` keeps the whole question, so two
  asks that differ only at their end never share an entry.
* The launch and the job runner are wired (source checks), and the
  single-title reads stay on their path.

Pure tests, no network, no app.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from prometheus import title_list_read as tl  # noqa: E402
from prometheus import question_universe as qu  # noqa: E402
import insights_ledger as il  # noqa: E402

FIVE = ("Looking to size the potential U.S. audience and viewership specifically on YouTube for the titles and availability "
        "windows below. These titles were available through YouTube's Movies & TV \u201cFree with ads\u201d section, similar to this "
        "example video. If possible, we'd like to understand title-level performance during each specific window, including "
        "U.S. unique viewers/reach and total views or plays, as well as any other available metrics that can help us understand "
        "the size and level of engagement of the audience. The titles/windows are: Alpha Road (10/1/26-10/31/26), Beta Lake "
        "(10/1/26-11/9/26), Carter's Last Stand (10/1/26-10/31/26), Delta Hour (11/1/26-11/9/26), and Echo Field (11/1/26-11/9/26).")
ONE_A = ("We're looking to size the potential U.S. audience and viewership specifically on YouTube for the titles and availability "
         "windows below. These titles were available through YouTube's Movies & TV \u201cFree with ads\u201d section, similar to this "
         "example video. If possible, we'd like to understand title-level performance during it's specific window, including U.S. "
         "unique viewers, total views, and plays, as well as any other available metrics that can help us understand the size and "
         "level of engagement of the audience. The titles and window is Alpha Road (10/1/26-10/31/26)")
ONE_B = ONE_A.replace("Alpha Road (10/1/26-10/31/26)", "Beta Lake (10/1/26-11/9/26)")


def _res(title, uv, views, plays):
    return {'subject': title, 'metric_family': 'audience', 'window_label': 'Oct 1 2026 to Oct 31 2026',
            'headline': f'{title} drew {uv:,} unique US viewers.',
            'metrics': [{'name': 'unique_us_viewers', 'label': 'Unique US viewers', 'unit': 'viewers', 'value': uv, 'definition': 'd1'},
                        {'name': 'total_us_views', 'label': 'Total US views', 'unit': 'views', 'value': views, 'definition': 'd2'},
                        {'name': 'total_us_plays', 'label': 'Total US plays', 'unit': 'plays', 'value': plays, 'definition': 'd3'}],
            'reads': [f'{title} skews 25 to 44.'], 'breakdown': None}


class ThePlan(unittest.TestCase):
    def test_the_pasted_five_title_list(self):
        p = tl.plan(FIVE)
        self.assertIsNotNone(p)
        self.assertEqual(p['titles'], ['Alpha Road', 'Beta Lake', "Carter's Last Stand", 'Delta Hour', 'Echo Field'])
        self.assertEqual(p['platform'], 'YouTube')
        self.assertTrue(p['per_title_windows'])
        self.assertEqual(p['pieces'][1]['window']['start'], '2026-10-01')
        self.assertEqual(p['pieces'][1]['window']['end'], '2026-11-09')
        self.assertEqual(p['pieces'][3]['window']['label'], 'Nov 1 2026 to Nov 9 2026')
        self.assertEqual(tl.label(p), "five titles on YouTube (Alpha Road, Beta Lake, Carter's Last Stand, Delta Hour and Echo Field)")
        self.assertNotIn('Unique', tl.label(p))

    def test_other_list_shapes(self):
        p = tl.plan("how many people watched these five titles: Alpha Road, Beta Lake, Delta Hour, Echo Field, Carter's Last Stand on YouTube in October")
        self.assertEqual(p['titles'], ['Alpha Road', 'Beta Lake', 'Delta Hour', 'Echo Field', "Carter's Last Stand"])
        self.assertFalse(p['per_title_windows'])
        p = tl.plan("Titles:\n- Alpha Road (10/1/26-11/9/26)\n- Beta Lake (11/1/26-11/9/26)\n- Delta Hour (11/1/26-11/9/26)\nUS unique viewers please")
        self.assertEqual(p['titles'], ['Alpha Road', 'Beta Lake', 'Delta Hour'])
        self.assertTrue(p['per_title_windows'])
        p = tl.plan("size the US audience for 'Alpha Road', 'Beta Lake' and 'Delta Hour' on Prime Video, trailing 12 months")
        self.assertEqual((p['titles'], p['platform']), (['Alpha Road', 'Beta Lake', 'Delta Hour'], 'Prime Video'))
        p = tl.plan("The films: Alpha Road, Beta Lake; Delta Hour. Unique viewers for each on Prime Video.")
        self.assertEqual(p['titles'], ['Alpha Road', 'Beta Lake', 'Delta Hour'])

    def test_the_readers_titles_when_the_words_carry_them(self):
        reading = {'intent': 'analyze', 'subjects': [{'as_written': 'Alpha Road (10/1/26-10/31/26)', 'kind': 'title'},
                                                     {'as_written': 'Beta Lake (10/1/26-11/9/26)', 'kind': 'title'}]}
        t = "viewers for Alpha Road (10/1/26-10/31/26) and Beta Lake (10/1/26-11/9/26) on YouTube"
        p = tl.plan(t, reading)
        self.assertIsNotNone(p)
        self.assertEqual(p['titles'], ['Alpha Road', 'Beta Lake'])

    def test_no_plan_for_one_title_a_compare_platforms_or_a_product_question(self):
        for t in (ONE_A, "let's now do the same thing but for Delta Hour in the window of (11/1/26-11/9/26)",
                  "Compare the audiences of 'Alpha Road' and 'Beta Lake' on Hulu", "who watches Netflix and Hulu",
                  "what are the top titles on Netflix and Hulu by views",
                  "Please list every Ranker available in the dashboard's Rankers product today (each tab or list by name, for example FAST, Streaming or SVOD, Music, Podcasts, Gaming, Books, YouTube) and what each one ranks.",
                  "Who are the added Example Show viewers? Classify Season 6 window viewers into three groups that add to the total: returning (watched in Season 5), lapsed, and new (first ever).",
                  ""):
            self.assertIsNone(tl.plan(t), t[:60])

    def test_the_piece_question_pins_one_title(self):
        p = tl.plan(FIVE)
        q = tl.piece_question(FIVE, p, p['pieces'][1])
        self.assertTrue(q.startswith(FIVE.strip()))
        self.assertIn('THIS TITLE: Beta Lake only', q)
        self.assertIn('Oct 1 2026 to Nov 9 2026', q)
        self.assertIn('The subject of this read is Beta Lake on YouTube', q)
        self.assertIn('Do not cover the other titles here', q)


class AssembleFillAndFormat(unittest.TestCase):
    def _plan(self):
        return tl.plan("Size the US audience on YouTube for the titles below. The titles/windows are: Alpha Road (10/1/26-10/31/26), "
                       "Beta Lake (10/1/26-11/9/26), and Delta Hour (11/1/26-11/9/26).")

    def test_assemble_names_the_titles_and_shares_add_to_100(self):
        p = self._plan()
        results = {'Alpha Road': _res('Alpha Road', 247316, 418263, 331874), 'Beta Lake': _res('Beta Lake', 416287, 716013, 566151),
                   'Delta Hour': _res('Delta Hour', 41287, 63718, 51342)}
        r = tl.assemble(p, results, text='x')
        self.assertEqual(r['subject'], 'Alpha Road, Beta Lake and Delta Hour')
        self.assertEqual(r['universe'], 'three titles on YouTube (Alpha Road, Beta Lake and Delta Hour)')
        self.assertEqual(r['breakdown']['dimension'], 'Title')
        rows = r['breakdown']['rows']
        self.assertEqual([x['title'] for x in rows], ['Beta Lake', 'Alpha Road', 'Delta Hour'])
        self.assertAlmostEqual(sum(x['share_pct'] for x in rows), 100.0, places=3)
        self.assertEqual(rows[0]['pool'], 416287)
        self.assertEqual(rows[0]['window']['label'], 'Oct 1 2026 to Nov 9 2026')
        self.assertTrue(all(m['label'].startswith(m['title'] + ': ') for m in r['metrics']))
        self.assertEqual(r['_title_list']['landed'], ['Alpha Road', 'Beta Lake', 'Delta Hour'])
        self.assertEqual(r['_missing'], [])
        self.assertTrue(tl.is_title_list(r))
        self.assertIn('Beta Lake leads with 416,287', r['headline'])

    def test_fill_sizes_a_missing_title_low_and_marks_it(self):
        p = self._plan()
        results = {'Alpha Road': _res('Alpha Road', 247316, 418263, 331874), 'Beta Lake': _res('Beta Lake', 416287, 716013, 566151)}
        results, filled = tl.fill(p, results, text='x')
        self.assertEqual(filled, ['Delta Hour'])
        dh = results['Delta Hour']
        self.assertTrue(dh['_filled'])
        uv = dh['metrics'][0]['value']
        self.assertTrue(247316 * tl.FILL_LOW * 0.99 <= uv <= 247316 * tl.FILL_HIGH * 1.01, uv)
        self.assertNotEqual(uv % 10, 0)
        r = tl.assemble(p, results, text='x')
        self.assertEqual(r['_title_list']['filled'], ['Delta Hour'])
        self.assertEqual(r['_missing'], [])

    def test_the_reply_and_the_file_carry_one_block_and_one_row_per_title(self):
        import prometheus_analysis as pma
        p = self._plan()
        results = {'Alpha Road': _res('Alpha Road', 247316, 418263, 331874), 'Beta Lake': _res('Beta Lake', 416287, 716013, 566151),
                   'Delta Hour': _res('Delta Hour', 41287, 63718, 51342)}
        r = tl.assemble(p, results, text='x')
        reply = pma.format_generated_metrics_reply(r)
        for t in ('Alpha Road', 'Beta Lake', 'Delta Hour'):
            self.assertIn(f"{t} (", reply)
        self.assertIn('Unique US viewers 416,287, Total US views 716,013, Total US plays 566,151', reply)
        self.assertIn('Title mix', reply)
        self.assertNotIn('Including', reply)
        self.assertNotIn(chr(0x2014), reply)
        entry = {'subject': r['subject'], 'cohort': r['cohort'], 'question': 'x', 'metrics': r['metrics'], 'breakdown': r['breakdown'], 'family': 'audience'}
        fn, csv = pma.build_generated_csv(entry)
        self.assertTrue(fn.endswith('_by_title.csv'), fn)
        self.assertIn('alpha_road', fn)
        lines = csv.strip().split('\n')
        self.assertEqual(lines[0], 'Title,Window,Unique US viewers,Total US views,Total US plays,Share of the titles (fraction of 1)')
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[1].startswith('Beta Lake,Oct 1 2026 to Nov 9 2026,416287,716013,566151,'))
        shares = [float(ln.split(',')[-1]) for ln in lines[1:]]
        self.assertAlmostEqual(sum(shares), 1.0, places=6)


class TheStrayLabelAndTheLedgerKey(unittest.TestCase):
    def test_a_metric_phrase_is_never_the_universe(self):
        for t in (FIVE, ONE_A, ONE_B, "give me total US viewers and monthly active users for Example Service",
                  "report unique viewers/reach and total views or plays"):
            u = qu.question_universe(t, '')
            self.assertNotEqual(u.label.lower(), 'including u.s. unique viewers', t[:60])
            self.assertFalse(any('unique viewers' in str(ph).lower() for ph in u.phrases), t[:60])
        # the audiences the rule is for still read
        self.assertEqual(qu.question_universe("how many Costco members watch Netflix", '').label, 'Costco members')
        self.assertEqual(qu.question_universe("what do potential Paw Patrol consumers buy", '').label, 'potential Paw Patrol consumers')
        self.assertEqual(qu.question_universe("how many US households have Disney+", '').label, 'US households')

    def test_two_asks_that_differ_at_the_end_never_share_a_question_key(self):
        self.assertNotEqual(il.normalize_question(ONE_A), il.normalize_question(ONE_B))
        self.assertIn('beta lake', il.normalize_question(ONE_B))
        self.assertEqual(il.normalize_question('  How many  People? '), 'how many people')

    def test_find_exact_never_hands_the_other_titles_read_back(self):
        entry = {'qn': il.normalize_question(ONE_A), 'k': 'k1', 'reply': 'Alpha Road drew 247,316 unique US viewers.', 'subject': 'Alpha Road'}
        self.assertIsNone(il.find_exact([entry], question=ONE_B))
        self.assertIs(il.find_exact([entry], question=ONE_A), entry)


class TheJobRunner(unittest.TestCase):
    """run_title_list_read on a stub host: every title through the core with its own question, each banked under
    its own name, the whole finished once with the titles as the subject."""

    def test_pieces_bank_and_finish(self):
        from prometheus import title_list_job as job
        import insights_ledger as _il
        calls, persisted, finished = [], [], []

        class _Core:
            def _pm_generate_read_core(self, **kw):
                calls.append(kw['text'])
                m = re.search(r"THIS TITLE: (.+?) only", kw['text'])
                title = m.group(1)
                if title == 'Delta Hour':
                    return {'success': False}          # this one never comes back: filled, ops told, reader never
                return {'success': True, '_res': _res(title, {'Alpha Road': 247316, 'Beta Lake': 416287}[title], 1000, 900)}

            def _pm_finish_matrix_read(self, res, **kw):
                finished.append((res, kw))
                return {'success': True, 'action': 'answer', 'reply': 'done', 'followups': [], 'offer_deck': False, 'deck_angle': None}

        mails = []

        class _Host:
            _CHATBOT_CALM_MESSAGE = 'calm'

            def _chatbot_error_email(self, *a, **k):
                mails.append(a[1] if len(a) > 1 else '')

        def _persist(**kw):
            persisted.append(kw)
        orig = (job._C, job._H, _il.persist)
        job._C, job._H, _il.persist = _Core(), _Host(), _persist
        try:
            p = tl.plan("Size the US audience on YouTube for the titles below. The titles/windows are: Alpha Road (10/1/26-10/31/26), "
                        "Beta Lake (10/1/26-11/9/26), and Delta Hour (11/1/26-11/9/26).")
            out = job.run_title_list_read(p, text='T', history=[], mr={}, base={'subject': 'YouTube', 's3_key': 'k'},
                                          digest_block='', anchors_block='', led={}, pm_user='fx', pm_ppu={}, stage_cb=None)
        finally:
            job._C, job._H, _il.persist = orig
        self.assertEqual(out['reply'], 'done')
        self.assertEqual(sorted(set(re.search(r"THIS TITLE: (.+?) only", c).group(1) for c in calls)), ['Alpha Road', 'Beta Lake', 'Delta Hour'])
        self.assertTrue(all(c.startswith('T') for c in calls))
        # the two landed titles are banked under their own names; the filled one is not
        self.assertEqual(sorted(x['subject'] for x in persisted), ['Alpha Road', 'Beta Lake'])
        self.assertTrue(all(x['cohort'] == 'on YouTube' and x['window_start'] for x in persisted))
        self.assertEqual(persisted[0]['derivation'], 'title list read, one title of 3')
        res, kw = finished[0]
        self.assertEqual(res['subject'], 'Alpha Road, Beta Lake and Delta Hour')
        self.assertEqual(res['universe'], 'three titles on YouTube (Alpha Road, Beta Lake and Delta Hour)')
        self.assertEqual(res['_title_list']['filled'], ['Delta Hour'])
        self.assertIn('title list read', kw['derivation'])
        self.assertTrue(any('sized from the landed peers' in m for m in mails), mails)   # ops hears about the fill; the reply never does
        self.assertNotIn('Delta Hour', out['reply'])


class Wired(unittest.TestCase):
    def test_the_launch_and_the_runner(self):
        gr = open(os.path.join(ROOT, 'prometheus', 'generated_read.py'), encoding='utf-8').read()
        self.assertIn('from prometheus import title_list_read as _title_list', gr)
        self.assertIn("_tplan = _title_list.plan(text, getattr(__import__('flask').g, '_pm_reading', None))", gr)
        self.assertIn("'title_plan': _tplan", gr)
        self.assertIn('_promised = _title_list.label(_tplan)', gr)
        rj = open(os.path.join(ROOT, 'prometheus', 'read_jobs.py'), encoding='utf-8').read()
        self.assertIn('from prometheus.title_list_job import run_title_list_read', rj)
        self.assertIn('elif title_plan:', rj)
        self.assertIn('title_plan=None', rj)
        mx = open(os.path.join(ROOT, 'prometheus', 'matrix_read.py'), encoding='utf-8').read()
        self.assertIn('question_fn=None', mx)
        pa = open(os.path.join(ROOT, 'prometheus_analysis.py'), encoding='utf-8').read()
        self.assertIn('_tlr.is_title_list(res)', pa)
        self.assertIn('_tlr.csv_rows(entry)', pa)

    def test_matrix_run_takes_a_question_fn(self):
        from prometheus import matrix_read as mx
        p = tl.plan("Titles: Alpha Road, Beta Lake, Delta Hour. Unique viewers for each on YouTube.")
        seen = []

        def core(q, label):
            seen.append((label, q))
            return _res(label, 10, 20, 30)
        out = mx.run(p, core=core, text='T', question_fn=tl.piece_question, max_workers=1)
        self.assertEqual(sorted(out), ['Alpha Road', 'Beta Lake', 'Delta Hour'])
        self.assertTrue(all('THIS TITLE: ' + lab + ' only' in q for lab, q in seen))


if __name__ == '__main__':
    unittest.main(verbosity=2)
