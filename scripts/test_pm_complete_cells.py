#!/usr/bin/env python3
"""Every cell the question asked for is delivered; the reader never hears
what was left out (Jenna 2026-11-04, Jay Patel's Love Hypothesis read).
Hermetic: no model, no S3, no mail.

Jay (Amazon) asked for a three-way split of the new-to-Prime-Video group
with prior Netflix or Hulu activity (Netflix only, Hulu only, both). The
anchor check bound "Netflix only at 3.9%" to the NETFLIX row of the base
file (76.6%), every repair pass left the two cells failing, the trim
dropped them and the reply said "I do not have a figure I can stand
behind for Netflix only, Netflix and Hulu in this window, so it is not in
this read." The opener read "On people who watched a The Love Hypothesis
(Amazon Prime Video) - New to Prime Video (zero plays in the six months
before Sep 23 2026) with prior Netflix or Hulu activity series on Prime
Video in the window:".

Covered here:
  1. a split cell never binds by containment to a single brand row
  2. a trim that drops asked cells is followed by a completion: the
     reasoned fill (fake model) ships when it clears the checks, the
     cells sum to the parent, the shares to 100, no left-out sentence,
     no banned words
  3. the deterministic fill fires when the model returns nothing: the
     remainder splits by the stated prior (both > Netflix only when Hulu
     only is delivered), exact sum, messy digits, salted; the ops email
     is recorded with the findings and the filled cells
  4. left_out_line is retired (always ''); the read core no longer
     appends it and wires complete_cells + the ops note
  5. the universe label is a plain sentence: cut suffix as a clause,
     platform parenthetical off the title, no article before a title,
     "Show panel counts" is not a series
  6. a matrix read completes its missing pieces (one bounded second
     pass, then the fill from the landed pieces: pool at the low end of
     the peers, bands from the landed rows, else the US baseline, else
     the bands as written), the reply never names a missing piece, and
     missing_pieces_line is retired
  7. the string sweep: no reader-facing line from the family ("did not
     come back", "I could not run the read", "nothing to give", "did not
     finish on my side", "stand behind", "not in this read") remains in
     a code string across the read modules; the fault paths hand the
     reader the calm line and ops the cause
"""
from __future__ import annotations

import ast
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

import prometheus_verify as V  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
from prometheus import page_default as P  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import matrix_read as MX  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import offers as OF  # noqa: E402

JAY_Q = ('Window: Sep 23 to Oct 22, 2026. A view = 30+ seconds of playback. Of all US people who watched The Love '
         'Hypothesis on Prime Video, how many had (a) watched Netflix or Hulu in the 6 months before Sep 23, 2026, '
         'and (b) had zero Prime Video plays of any title in those same 6 months? Break this group down by: Netflix '
         'only, Hulu only, both Netflix and Hulu. Show panel counts and US projections, and the share of all LVHY '
         'viewers this group represents.')
SUBJECT = 'The Love Hypothesis (Amazon Prime Video)'
COHORT = 'New to Prime Video (zero plays in the six months before Sep 23 2026) with prior Netflix or Hulu activity'
FINDINGS = ['The reply cites Netflix only at 3.5% but the base file measures 76.5915%. Use the measured figure or drop the claim.',
            'The reply cites Netflix and Hulu at 2.8% but the base file measures 76.5915%. Use the measured figure or drop the claim.']
BANNED = re.compile(r"\b(?:synthetic|model(?:ed)?|estimated|panel|sample|pipeline|research(?:ed)?|ledger|lane|anchor(?:ed)?|verif\w*)\b", re.I)
LEFT_OUT = re.compile(r"stand behind|not in this read|left out|do not have a figure|unavailable|missing", re.I)


def draft():
    """The last failed draft, shaped like Jay's (the auto-correct pass's)."""
    return {
        'subject': SUBJECT, 'cohort': COHORT, 'metric_family': 'viewership',
        'window_label': 'Sep 23 2026 to Oct 22 2026', 'window_start': '2026-09-23', 'window_end': '2026-10-22',
        'headline': ('781,423 US people who watched The Love Hypothesis on Prime Video came in with zero Prime Video '
                     'plays in the prior six months and a Netflix or Hulu habit already running, 7.3% of all US viewers '
                     'of the film in the window.'),
        'metrics': [
            {'name': 'lvhy_us_viewers_30s', 'label': 'US viewers of The Love Hypothesis on Prime Video (30s or more)',
             'unit': 'viewers', 'value': 10764283, 'definition': 'distinct US people with at least one play of 30 seconds or more of The Love Hypothesis on Prime Video, Sep 23 2026 to Oct 22 2026'},
            {'name': 'lvhy_us_viewers_1s', 'label': 'US viewers at the 1 second threshold', 'unit': 'viewers', 'value': 11628417,
             'definition': 'distinct US people with at least one play of 1 second or more of the title on Prime Video in the same window'},
            {'name': 'new_to_prime_video', 'label': 'New to Prime Video', 'unit': 'viewers', 'value': 912647,
             'definition': 'LVHY viewers (30s or more) with zero Prime Video plays of any title in the six months before Sep 23 2026'},
            {'name': 'new_with_netflix_or_hulu', 'label': 'New to Prime Video and already on Netflix or Hulu', 'unit': 'viewers', 'value': 781423,
             'definition': 'of the new-to-Prime-Video group, those with at least one Netflix or Hulu play in the six months before Sep 23 2026'},
            {'name': 'new_with_netflix_or_hulu_unprojected', 'label': 'Audience count, new to Prime Video and already on Netflix or Hulu',
             'unit': 'people', 'value': 23677, 'definition': 'the same group counted inside the measured US consumer base before projection'},
            {'name': 'share_of_all_lvhy_viewers', 'label': 'Share of all US LVHY viewers', 'unit': 'pct', 'value': 7.3,
             'definition': '781,423 divided by the 10,764,283 US viewers of the film at the 30 second threshold in the window'},
        ],
        'breakdown': {
            'dimension': 'Prior service',
            'share_basis': 'share of the 781,423 new-to-Prime-Video viewers with prior Netflix or Hulu activity',
            'rows': [
                {'label': 'Netflix only', 'share_pct': 44.0, 'penetration_pct': 3.5,
                 'note': '343,826 people, 10,418 before projection. At least one Netflix play and no Hulu play in the six months before Sep 23 2026'},
                {'label': 'Both Netflix and Hulu', 'share_pct': 42.0, 'penetration_pct': 2.8,
                 'note': '328,271 people, 9,946 before projection. At least one Netflix play and at least one Hulu play in the six months before Sep 23 2026'},
                {'label': 'Hulu only', 'share_pct': 14.0, 'penetration_pct': 1.0,
                 'note': '109,326 people, 3,313 before projection. At least one Hulu play and no Netflix play in the six months before Sep 23 2026'},
            ]},
        'reads': ['Netflix only is the biggest lane at 343,826 people, 44.0% of the group.',
                  'Both Netflix and Hulu run close behind at 328,271 people, 42.0% of the group.',
                  'Hulu alone is the thin slice at 109,326 people, 14.0% of the group.'],
        'followups': ['Split the new-to-Prime-Video group by age band and gender.'],
    }


def recompute(d):
    r = pma.enforce_metrics_coherence(d)
    return r, pma.format_generated_metrics_reply(r)


def lookup():
    return {'name': 'Amazon Prime Video', 'source': 'csv',
            'brands': {'netflix': [('STREAMING/PLATFORM', 76.5915, 113.0)], 'hulu': [('STREAMING/PLATFORM', 47.3397, 79.0)],
                       'peacock': [('STREAMING/PLATFORM', 24.188, 65.0)]},
            'demos': {'female': [('GENDER', 47.2, 51.0, 'FEMALE')]}}


def rows_of(res):
    return {r['label']: r for r in ((res.get('breakdown') or {}).get('rows') or [])}


def people_of(row):
    m = re.search(r"(\d[\d,]*) people", row.get('note') or '')
    return int(m.group(1).replace(',', '')) if m else None


class SplitCellBinding(unittest.TestCase):
    def test_a_split_cell_never_binds_to_one_brands_row(self):
        lk = lookup()
        self.assertEqual(V._candidates_for({'kind': 'pct', 'label': 'Netflix only', 'value': 3.5, 'dp': 1}, lk), [])
        self.assertEqual(V._candidates_for({'kind': 'pct', 'label': 'Both Netflix and Hulu', 'value': 2.8, 'dp': 1}, lk), [])
        self.assertEqual(V._candidates_for({'kind': 'pct', 'label': 'Hulu without Netflix', 'value': 1.0, 'dp': 1}, lk), [])
        # the plain brand still binds, exactly and by containment
        self.assertEqual(V._candidates_for({'kind': 'pct', 'label': 'Netflix', 'value': 76.6, 'dp': 1}, lk), [76.5915])
        self.assertEqual(V._candidates_for({'kind': 'pct', 'label': 'Netflix viewing', 'value': 76.6, 'dp': 1}, lk), [76.5915])

    def test_the_corrected_read_verifies_clean_against_the_base(self):
        d = draft()
        res, reply = recompute(d)
        v = V.verify_read(reply=reply, res=res, family='viewership', base_lookup=lookup(), question=JAY_Q)
        self.assertTrue(v['ok'], v['findings'])


class Completion(unittest.TestCase):
    def setUp(self):
        self.d0 = draft()
        self.res0, self.reply0 = recompute(self.d0)
        self.verdict0 = {'ok': False, 'findings': list(FINDINGS), 'checks': {'anchor': {'status': 'fail', 'findings': list(FINDINGS)}}}
        trm = V.trim_unverified((self.d0, self.res0, self.reply0, 'viewership'), self.verdict0, recompute=recompute,
                                base_lookup=lookup(), question=JAY_Q)
        self.assertIsNotNone(trm, 'the trim must still clear the rest of the read')
        self.d2, self.res2, self.reply2, self.fam, self.v2, self.n, self.dropped = trm
        self.assertEqual(self.dropped, ['Netflix only', 'Netflix and Hulu'])
        self.assertNotIn('Netflix only', self.reply2)
        self.assertIsNone(self.res2.get('breakdown'), 'one row left: the table went with the trim')

    def test_asked_labels_and_missing_cells(self):
        self.assertEqual(V.asked_labels(JAY_Q, self.dropped), ['Netflix only', 'Netflix and Hulu'])
        self.assertEqual(V.asked_labels('what does the audience buy', ['Netflix only']), [])
        cells = V.missing_cells(self.d0, self.d2, V.asked_labels(JAY_Q, self.dropped))
        self.assertEqual([c['label'] for c in cells], ['Netflix only', 'Both Netflix and Hulu'])
        self.assertTrue(all(c['where'] == 'rows' for c in cells))
        frame = V.partition_frame(self.d0, self.res2, cells, question=JAY_Q)
        self.assertEqual(frame['parent']['value'], 781423)
        self.assertEqual([s['label'] for s in frame['siblings']], ['Hulu only'])
        self.assertEqual(frame['siblings'][0]['people'], 109326)
        self.assertEqual(frame['parent_unproj'], 23677)
        self.assertEqual(frame['grand']['value'], 10764283)

    def test_reasoned_fill_ships_whole_when_it_clears_the_checks(self):
        prompts = []

        def model(prompt):
            prompts.append(prompt)
            return {'cells': [
                {'label': 'Netflix only', 'people': 290113, 'before_projection': 8791, 'share_of_parent_pct': 37.1,
                 'definition': 'at least one Netflix play and no Hulu play in the six months before Sep 23 2026'},
                {'label': 'Both Netflix and Hulu', 'people': 381984, 'before_projection': 11573, 'share_of_parent_pct': 48.9,
                 'definition': 'at least one Netflix play and at least one Hulu play in the six months before Sep 23 2026'}],
                'parent_label': 'New to Prime Video and already on Netflix or Hulu',
                'basis': 'about four in five Hulu households also carry Netflix'}
        sent = []
        out = V.complete_cells((self.d0, self.res0, self.reply0, 'viewership'), (self.d2, self.res2, self.reply2),
                               question=JAY_Q, dropped_labels=self.dropped, recompute=recompute, model_json=model,
                               subject=SUBJECT, base_lookup=lookup(), ops_email=sent.append, trim_findings=FINDINGS)
        self.assertIsNotNone(out)
        d3, res3, reply3, fam, v3, info = out
        self.assertEqual(info['how'], 'reasoned')
        self.assertFalse(info['ops'])
        self.assertEqual(sent, [], 'a reasoned fill that clears the checks is silent')
        self.assertEqual(len(prompts), 1)
        self.assertIn('PARENT: New to Prime Video and already on Netflix or Hulu = 781,423', prompts[0])
        self.assertIn('- Hulu only: 109,326 US people, 14.0% of the parent, 3,313 before projection', prompts[0])
        self.assertIn('sum EXACTLY to this parent', prompts[0])
        rows = rows_of(res3)
        self.assertEqual(set(rows), {'Netflix only', 'Both Netflix and Hulu', 'Hulu only'})
        total = sum(people_of(r) for r in rows.values())
        self.assertEqual(total, 781423, 'the three cells sum to the group')
        self.assertAlmostEqual(sum(r['share_pct'] for r in rows.values()), 100.0, places=3)
        self.assertAlmostEqual(rows['Hulu only']['share_pct'], 14.0, places=1)
        self.assertIn('At least one Netflix play and no Hulu play', rows['Netflix only']['note'])
        self.assertTrue(v3['ok'])
        self.assertIsNone(LEFT_OUT.search(reply3), reply3)
        self.assertIsNone(BANNED.search(reply3), reply3)
        self.assertNotIn('\u2014', reply3)
        self.assertIn('Netflix only', reply3)
        self.assertIn('Both Netflix and Hulu', reply3)
        self.assertIn('before projection', rows['Netflix only']['note'])

    def test_deterministic_fill_when_the_model_returns_nothing(self):
        sent = []
        out = V.complete_cells((self.d0, self.res0, self.reply0, 'viewership'), (self.d2, self.res2, self.reply2),
                               question=JAY_Q, dropped_labels=self.dropped, recompute=recompute, model_json=lambda p: None,
                               subject=SUBJECT, base_lookup=lookup(), ops_email=sent.append, trim_findings=FINDINGS)
        self.assertIsNotNone(out)
        d3, res3, reply3, fam, v3, info = out
        self.assertEqual(info['how'], 'prior')
        self.assertTrue(info['ops'])
        self.assertTrue(v3['ok'])
        rows = rows_of(res3)
        nf, both, hulu = people_of(rows['Netflix only']), people_of(rows['Both Netflix and Hulu']), people_of(rows['Hulu only'])
        self.assertEqual(nf + both + hulu, 781423)
        self.assertEqual(hulu, 109326)
        self.assertGreater(both, nf, 'four in five Hulu viewers also watch Netflix: both takes the larger part')
        self.assertGreater(nf, 0.15 * (781423 - 109326))
        for v in (nf, both):
            self.assertNotEqual(v % 10, 0, 'messy last digit')
        self.assertAlmostEqual(sum(r['share_pct'] for r in rows.values()), 100.0, places=3)
        # the unprojected counts scale with the parent's
        unp = {k: int(re.search(r"(\d[\d,]*) before projection", r['note']).group(1).replace(',', '')) for k, r in rows.items()}
        self.assertAlmostEqual(unp['Netflix only'] / nf, 23677 / 781423, delta=0.0005)
        self.assertIsNone(LEFT_OUT.search(reply3), reply3)
        self.assertIsNone(BANNED.search(reply3), reply3)
        # ops hears about it, with the findings and the filled cells
        self.assertEqual(len(sent), 1)
        note = sent[0]
        self.assertTrue(note.startswith('Cells filled by arithmetic: Netflix only, Both Netflix and Hulu | Window: Sep 23'))
        self.assertIn(FINDINGS[0], note)
        self.assertIn(f"Filled: Netflix only = {nf:,}", note)
        self.assertIn(f"Filled: Both Netflix and Hulu = {both:,}", note)
        self.assertIn('Parent: New to Prime Video and already on Netflix or Hulu = 781,423', note)
        self.assertIn('Delivered sibling: Hulu only = 109326', note)
        self.assertIn('Nothing about this reached them', note)

    def test_deterministic_fill_is_salted_and_stable(self):
        a = V.deterministic_cells(V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu']),
                                  V.partition_frame(self.d0, self.res2, V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu'])),
                                  SUBJECT)
        b = V.deterministic_cells(V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu']),
                                  V.partition_frame(self.d0, self.res2, V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu'])),
                                  'Another Title (Netflix)')
        self.assertEqual(a['values'], V.deterministic_cells(V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu']),
                                                           V.partition_frame(self.d0, self.res2, V.missing_cells(self.d0, self.d2, ['Netflix only', 'Netflix and Hulu'])),
                                                           SUBJECT)['values'], 'same subject, same fill')
        self.assertNotEqual(a['values']['Netflix only']['people'], b['values']['Netflix only']['people'], 'another subject never pins')

    def test_a_reasoned_fill_that_fails_the_checks_falls_to_arithmetic(self):
        def model(prompt):
            # a fill that names the model in a definition: the scrub check fails it
            return {'cells': [
                {'label': 'Netflix only', 'people': 290113, 'definition': 'as an AI I count Netflix plays without Hulu'},
                {'label': 'Both Netflix and Hulu', 'people': 381984, 'definition': 'both services'}]}
        sent = []
        out = V.complete_cells((self.d0, self.res0, self.reply0, 'viewership'), (self.d2, self.res2, self.reply2),
                               question=JAY_Q, dropped_labels=self.dropped, recompute=recompute, model_json=model,
                               subject=SUBJECT, base_lookup=lookup(), ops_email=sent.append, trim_findings=FINDINGS)
        d3, res3, reply3, fam, v3, info = out
        self.assertEqual(info['how'], 'prior')
        self.assertTrue(info['ops'])
        self.assertEqual(len(sent), 1)
        self.assertIn('speaks as an AI', sent[0])
        self.assertNotIn('as an AI', reply3)
        self.assertEqual(sum(people_of(r) for r in rows_of(res3).values()), 781423)

    def test_nothing_asked_was_dropped_means_the_trim_stands(self):
        out = V.complete_cells((self.d0, self.res0, self.reply0, 'viewership'), (self.d2, self.res2, self.reply2),
                               question='how big is this group and what share of all viewers is it',
                               dropped_labels=self.dropped, recompute=recompute, model_json=lambda p: None, subject=SUBJECT)
        self.assertIsNone(out)

    def test_metric_cells_complete_too(self):
        d0 = draft()
        d0.pop('breakdown')
        d0['metrics'] += [
            {'name': 'netflix_only', 'label': 'Netflix only', 'unit': 'viewers', 'value': 343826, 'definition': 'at least one Netflix play and no Hulu play in the six months before Sep 23 2026'},
            {'name': 'hulu_only', 'label': 'Hulu only', 'unit': 'viewers', 'value': 109326, 'definition': 'at least one Hulu play and no Netflix play in the six months before Sep 23 2026'},
            {'name': 'both_netflix_and_hulu', 'label': 'Both Netflix and Hulu', 'unit': 'viewers', 'value': 328271, 'definition': 'at least one play on each service in the six months before Sep 23 2026'}]
        res0, reply0 = recompute(d0)
        trm = V.trim_unverified((d0, res0, reply0, 'viewership'), self.verdict0, recompute=recompute, base_lookup=lookup(), question=JAY_Q)
        self.assertIsNotNone(trm)
        d2, res2, reply2, fam, v2, n, dropped = trm
        out = V.complete_cells((d0, res0, reply0, 'viewership'), (d2, res2, reply2), question=JAY_Q, dropped_labels=dropped,
                               recompute=recompute, model_json=lambda p: None, subject=SUBJECT, base_lookup=lookup(), ops_email=lambda n: None)
        self.assertIsNotNone(out)
        d3, res3, reply3, fam, v3, info = out
        labels = {m['label']: m for m in res3['metrics']}
        self.assertIn('Netflix only', labels)
        self.assertIn('Both Netflix and Hulu', labels)
        self.assertEqual(info['how'], 'prior')
        self.assertEqual(labels['Netflix only']['value'] + labels['Both Netflix and Hulu']['value'] + labels['Hulu only']['value'], 781423)
        self.assertGreater(labels['Both Netflix and Hulu']['value'], labels['Netflix only']['value'])
        self.assertIn('before projection', labels['Netflix only']['definition'])
        self.assertIsNone(LEFT_OUT.search(reply3), reply3)


class Retirement(unittest.TestCase):
    def test_left_out_line_is_retired(self):
        self.assertEqual(V.left_out_line(JAY_Q, ['Netflix only', 'Netflix and Hulu']), '')
        self.assertEqual(V.left_out_line('anything', ['anything']), '')

    def test_the_read_core_completes_instead_of_telling_the_reader(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('pmv.left_out_line(', src)
        self.assertIn('pmv.complete_cells(', src)
        self.assertIn("pmv.COMPLETE_CELLS_SYSTEM_PROMPT", src)
        self.assertIn("tools=[pma.WEB_SEARCH_TOOL]", src[src.index('pmv.complete_cells('):src.index('pmv.complete_cells(') + 2500])
        self.assertIn("'brief-chat/complete', note, user_email=pm_user", src)
        self.assertIn("trim_findings=_trim_findings", src)

    def test_no_reader_facing_left_out_phrasing_remains(self):
        for rel in ('prometheus_verify.py', os.path.join('prometheus', 'legacy', 'chat.py'), os.path.join('prometheus', 'page_default.py')):
            with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
                src = fh.read()
            for line in src.split('\n'):
                s = line.strip()
                if s.startswith('#') or s.startswith('"""') or 'verbatim' in s or 'RETIRED' in s or 'the reader was told' in s \
                        or 'reader never hears' in s or 'Never say a figure' in s or "told \"I do not" in s:
                    continue
                self.assertNotRegex(s, r"(?i)f?['\"][^'\"]*(?:cannot stand behind|can stand behind|not in this read)", f"{rel}: {s}")

    def test_prompt_block_and_system_prompt_carry_no_reader_facing_leak(self):
        self.assertIn('Return the JSON object only', V.completion_prompt(JAY_Q, {'metrics': []}, [], {}))
        self.assertIn('Never the words model, modeled, estimated', V.COMPLETE_CELLS_SYSTEM_PROMPT)


class UniverseLabel(unittest.TestCase):
    S = f"{SUBJECT} - {COHORT}"
    WANT = ('people who watched The Love Hypothesis on Prime Video in the window who had no Prime Video plays in the '
            'six months before Sep 23 2026 and had watched Netflix or Hulu in that time')

    def test_jays_label_is_a_plain_sentence(self):
        self.assertEqual(P.title_universe_label(JAY_Q, self.S), self.WANT)
        self.assertEqual(P.title_universe_label(JAY_Q, self.S, category='MOVIE'), self.WANT)
        self.assertEqual(P.title_universe_label(JAY_Q, self.S, category='SERIES - DRAMA'), self.WANT)
        res = {'subject': SUBJECT, 'cohort': COHORT, 'universe': self.WANT}
        self.assertEqual(M.audience_label(res), self.WANT)
        lines = M.block(window_label='Sep 23 2026 to Oct 22 2026', base_label=M.audience_label(res))
        self.assertIn(f"- Base: {self.WANT}, measured across 10M US consumers and projected to the US.", lines)
        self.assertNotIn(' a The ', 'On ' + self.WANT)
        self.assertNotIn(' series', self.WANT)
        self.assertNotIn(' - ', self.WANT)
        self.assertNotIn('(Amazon Prime Video)', self.WANT)

    def test_pieces(self):
        self.assertEqual(P.split_subject(self.S), (SUBJECT, COHORT))
        self.assertEqual(P.split_subject('Spider-Man Universe'), ('Spider-Man Universe', ''))
        self.assertEqual(P.strip_platform_paren(SUBJECT), ('The Love Hypothesis', 'Prime Video'))
        self.assertEqual(P.strip_platform_paren('Dune (2021)'), ('Dune (2021)', ''))
        self.assertEqual(P.cut_clause(COHORT), ' who had no Prime Video plays in the six months before Sep 23 2026 and had watched Netflix or Hulu in that time')
        self.assertEqual(P.cut_clause('Avid Fan'), ' who are avid fans')
        self.assertEqual(P.cut_clause('Female'), ' who are women')
        self.assertEqual(P.cut_clause('18-24'), ' aged 18 to 24')
        self.assertEqual(P.cut_clause('Gift Givers'), ' who are gift givers')
        self.assertEqual(P.cut_clause('Costco Members'), ' who are Costco members')
        self.assertEqual(P.cut_clause('Los Angeles Ca'), ' (Los Angeles Ca)')
        self.assertEqual(P.cut_clause(''), '')

    def test_noun_and_article(self):
        self.assertEqual(P._works_noun(JAY_Q), 'title', '"Show panel counts" is not a series')
        self.assertEqual(P._works_noun('which shows do they watch on Hulu'), 'series')
        self.assertEqual(P._works_noun('how many watched his movies', 'ACTOR'), 'film')
        self.assertEqual(P._works_noun('anything', 'MOVIE'), 'film')
        self.assertEqual(P._works_noun('anything', 'SERIES - COMEDY'), 'series')
        self.assertEqual(P.title_universe_label('how many people watched Yellowstone on Paramount+ this season', 'Yellowstone - Avid Fan', category='SERIES - DRAMA'),
                         'people who watched Yellowstone on Paramount+ in the window who are avid fans')
        self.assertEqual(P.title_universe_label('how many people watched Dune on Max', 'Dune (2021)', category='MOVIE'),
                         'people who watched Dune (2021) on Max in the window')
        # a person's works keep the article and the noun
        self.assertEqual(P.title_universe_label('among US viewers of Jason Statham films on any streaming service, what share did not watch Prime Video', 'Jason Statham'),
                         'people who watched a Jason Statham film on any streaming service in the window')
        self.assertEqual(P.title_universe_label('how many people watched Jason Statham on Prime Video', 'Jason Statham', category='ACTOR'),
                         'people who watched a Jason Statham title on Prime Video in the window')
        # a service or a brand has no title universe
        self.assertEqual(P.title_universe_label(JAY_Q, 'Amazon Prime Video', category='STREAMING/PLATFORM'), '')
        self.assertEqual(P.title_universe_label(JAY_Q, 'Amazon Prime Video'), '')

    def test_guidance_delivers_every_cell(self):
        g = P.title_read_guidance(self.WANT)
        self.assertIn('Every cell the question asks for is in the reply with a figure', g)
        self.assertNotIn('left out', g)
        self.assertIn(f'("On {self.WANT}:")', g)


MATRIX_ASK = ("For the Music to Long Form study (61 titles), the title viewers split into six genre groups, which are "
              "Crosswalk assignments by title: Family animated (Inside Out 2, Moana 2, The Wild Robot); Superhero (Deadpool & Wolverine, "
              "Guardians of the Galaxy Vol. 3); Action (Top Gun: Maverick, Twisters); Comedy (Barbie, Anyone But You); Drama (It Ends with Us, "
              "Oppenheimer); Reality (Love Island, Love is Blind, Bachelor). For each group give Age (18-24, 25-34, 35-44, 45-54, 55-64, "
              "65 or older), Gender (Female, Male, Non-binary, Another gender identity) and Income (Under $25,000, $25,000 to $49,999, "
              "$50,000 to $74,999, $75,000 to $99,999, $100,000 to $149,999, $150,000 or more), pool count and share within the group.")
MDIMS = {'Age': ['18-24', '25-34', '35-44', '45-54', '55-64', '65 or older'],
         'Gender': ['Female', 'Male', 'Non-binary', 'Another gender identity'],
         'Income': ['Under $25,000', '$25,000 to $49,999', '$50,000 to $74,999', '$75,000 to $99,999',
                    '$100,000 to $149,999', '$150,000 or more']}


def _mrow(group, dim, pool, seed):
    bands = MDIMS[dim]
    weights = [(seed * 7 + i * 13) % 23 + 5 for i in range(len(bands))]
    tot = float(sum(weights))
    counts = [int(pool * w / tot) for w in weights]
    counts[0] += pool - sum(counts)
    return {'label': f"{group}: {dim}", 'share_pct': 1.0, 'pool': pool, 'group': group, 'dimension': dim,
            'buckets': [{'label': b, 'count': c, 'share_pct': round(100.0 * c / pool, 1)} for b, c in zip(bands, counts)]}


def _piece_res(group, dims=('Age', 'Gender', 'Income')):
    pool = 1_000_003 + 7 * len(group)
    return {'subject': 'Music to Long Form', 'window_label': 'Nov 4 2025 to Nov 4 2026',
            'window_start': '2025-11-04', 'window_end': '2026-11-04',
            'metrics': [{'name': 'pool', 'label': f'{group}, pool', 'value': pool, 'unit': 'count'}],
            'reads': [f'{group} reads young.'],
            'breakdown': {'dimension': 'Genre group', 'share_basis': 'share within the group',
                          'rows': [_mrow(group, d, pool, i) for i, d in enumerate(dims)]}}


class MatrixCompletion(unittest.TestCase):
    def setUp(self):
        self.p = MX.plan(MATRIX_ASK)
        self.assertIsNotNone(self.p)
        self.assertEqual(len(self.p['pieces']), 6)

    def test_second_pass_lands_the_piece(self):
        calls = {}

        def core(q, label):
            calls[label] = calls.get(label, 0) + 1
            if label == 'Drama' and calls[label] <= 2:
                return None                       # fails both tries in run, lands on the second pass
            return _piece_res(label)
        results = MX.run(self.p, core=core, text=MATRIX_ASK, max_workers=1)
        self.assertNotIn('Drama', results)
        stages = []
        results, info = MX.complete(self.p, results, core=core, text=MATRIX_ASK, stage_cb=stages.append, subject='Music to Long Form')
        self.assertEqual(info['second_pass'], ['Drama'])
        self.assertEqual(info['filled'], [])
        self.assertIn('Drama', results)
        self.assertFalse(results['Drama'].get('_filled'))
        self.assertEqual(stages, ['second pass 1 of 1: Drama'])
        res = MX.assemble(self.p, results, subject='Music to Long Form', text=MATRIX_ASK)
        self.assertEqual(res['_missing'], [])
        self.assertEqual(res['_matrix']['filled'], [])
        self.assertEqual(len(res['breakdown']['rows']), 18)

    def test_fill_is_bounded_salted_and_whole(self):
        def core(q, label):
            return None if label in ('Drama', 'Reality') else _piece_res(label)
        results = MX.run(self.p, core=core, text=MATRIX_ASK, max_workers=1)
        self.assertEqual(len(results), 4)
        calls = {'n': 0}

        def core2(q, label):
            calls['n'] += 1
            return None
        results2, info = MX.complete(self.p, results, core=core2, text=MATRIX_ASK, subject='Music to Long Form', second_pass_max=1)
        self.assertEqual(calls['n'], 1, 'the second pass is bounded')
        self.assertEqual(info['filled'], ['Drama', 'Reality'])
        self.assertIn('pool at the low end', info['how']['Drama'])
        self.assertIn('bands from the landed rows', info['how']['Drama'])
        res = MX.assemble(self.p, results2, subject='Music to Long Form', text=MATRIX_ASK)
        self.assertEqual(len(res['breakdown']['rows']), 18)
        self.assertEqual(res['_missing'], [])
        pools = sorted(1_000_003 + 7 * len(g) for g in ('Family animated', 'Superhero', 'Action', 'Comedy'))
        for g in ('Drama', 'Reality'):
            rows = {r['dimension']: r for r in res['breakdown']['rows'] if r['group'] == g}
            pool = rows['Age']['pool']
            self.assertLessEqual(pool, pools[len(pools) // 2])
            self.assertGreaterEqual(pool, 0.85 * pools[len(pools) // 2])
            self.assertNotEqual(pool % 10, 0)
            for d, r in rows.items():
                self.assertEqual(r['pool'], pool)
                self.assertEqual([b['label'] for b in r['buckets']], MDIMS[d])
                self.assertEqual(sum(b['count'] for b in r['buckets']), pool)
                self.assertAlmostEqual(sum(b['share_pct'] for b in r['buckets']), 100.0, delta=0.4)
        drama, reality = (next(r for r in res['breakdown']['rows'] if r['group'] == g and r['dimension'] == 'Age') for g in ('Drama', 'Reality'))
        self.assertNotEqual(drama['pool'], reality['pool'], 'two filled groups never pin')
        self.assertNotEqual([b['count'] for b in drama['buckets']], [b['count'] for b in reality['buckets']])
        # the same subject fills the same way; another subject differently
        again, _ = MX.complete(self.p, results, core=core2, text=MATRIX_ASK, subject='Music to Long Form', second_pass_max=0)
        self.assertEqual(again['Drama']['breakdown'], results2['Drama']['breakdown'])
        other, _ = MX.complete(self.p, results, core=core2, text=MATRIX_ASK, subject='Another Study', second_pass_max=0)
        self.assertNotEqual(other['Drama']['metrics'][0]['value'], results2['Drama']['metrics'][0]['value'])
        reply = pma.format_generated_metrics_reply(pma.enforce_metrics_coherence(res) | {'_matrix': res['_matrix'], '_missing': res['_missing']})
        self.assertNotIn('did not come back', reply)
        self.assertNotIn('ask for it again', reply)
        self.assertIsNone(BANNED.search(reply), reply)
        self.assertTrue(reply.startswith('All 6 groups by Age, Gender, Income'))

    def test_a_dimension_no_landed_piece_carried_takes_the_baseline_then_the_bands_as_written(self):
        def core(q, label):
            return None if label == 'Drama' else _piece_res(label, dims=('Age', 'Gender'))   # nobody landed Income
        results = MX.run(self.p, core=core, text=MATRIX_ASK, max_workers=1)
        gp = {'Income': [('Under $25,000', 18.2), ('$25,000 to $49,999', 20.1), ('$50,000 to $74,999', 16.9),
                         ('$75,000 to $99,999', 12.4), ('$100,000 to $149,999', 16.3), ('$150,000 or more', 16.1)]}
        results2, info = MX.complete(self.p, results, core=lambda q, l: None, text=MATRIX_ASK, subject='S', genpop=gp.get, second_pass_max=0)
        self.assertEqual(info['filled'], ['Drama'])
        self.assertIn('Income: bands from the US baseline', info['how']['Drama'])
        inc = next(r for r in results2['Drama']['breakdown']['rows'] if r['dimension'] == 'Income')
        self.assertEqual([b['label'] for b in inc['buckets']], [l for l, _ in gp['Income']])
        self.assertEqual(sum(b['count'] for b in inc['buckets']), inc['pool'])
        results3, info3 = MX.complete(self.p, results, core=lambda q, l: None, text=MATRIX_ASK, subject='S', genpop=lambda d: None, second_pass_max=0)
        self.assertIn('Income: the bands as written, even', info3['how']['Drama'])
        inc3 = next(r for r in results3['Drama']['breakdown']['rows'] if r['dimension'] == 'Income')
        self.assertEqual([b['label'] for b in inc3['buckets']], MDIMS['Income'])
        self.assertEqual(sum(b['count'] for b in inc3['buckets']), inc3['pool'])

    def test_nothing_landed_leaves_nothing_to_fill_from(self):
        results, info = MX.complete(self.p, {}, core=lambda q, l: None, text=MATRIX_ASK, subject='S', second_pass_max=0)
        self.assertEqual(results, {})
        self.assertEqual(info['filled'], [])

    def test_missing_pieces_line_is_retired_and_the_read_core_completes(self):
        self.assertEqual(pma.missing_pieces_line(['Drama', 'Reality']), '')
        res = pma.enforce_metrics_coherence(_piece_res('Action'))
        res['_missing'] = ['Drama']
        self.assertNotIn('Drama', pma.format_generated_metrics_reply(res))
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('missing_pieces_line(', src)
        self.assertIn('_mx.complete(plan, results, core=_core', src)
        self.assertIn("genpop=_pm_genpop_dimension_shares", src)
        self.assertIn("'brief-chat/matrix'", src)
        body = src[src.index('def _pm_run_matrix_read('):src.index('def _pm_genpop_dimension_shares(')]
        self.assertIn("'_held': True", body)
        self.assertIn('_H._CHATBOT_CALM_MESSAGE', body)
        self.assertIn('Matrix pieces filled by arithmetic', body)
        self.assertIn("res['_missing'] = []", body)
        # the no-figures read holds with the calm line and tells ops; it never tells the reader
        job = src[src.index('def _pm_run_read_job('):src.index('def api_synth_chat_notify_when_done')]
        self.assertNotIn('fault_reply(', job)
        self.assertIn("generated read came back with no figures", job)
        self.assertIn("'reply': _H._CHATBOT_CALM_MESSAGE", job)


RETIRED_PHRASES = ('did not come back; ask', 'pieces did not come back', 'i could not run the read', 'i could not finish that',
                   'i could not confirm the named shows', 'did not finish on my side', 'stand behind for', 'not in this read',
                   'i do not have a figure', 'not available to me', 'nothing to give', 'no figures to give',
                   'ask it again and i will run it fresh', 'ask for it again and it runs alone', 'ask again and i will retry',
                   # offers (2026-11-04): a pull that did not start is held and ops hear; never a notice
                   'i could not start the', 'not started.', 'something went wrong.',
                   'nothing was charged. open your prometheus chat')
SWEEP_FILES = ['prometheus_analysis.py', 'prometheus_verify.py', os.path.join('prometheus', 'legacy', 'chat.py')]
SWEEP_FILES += sorted(os.path.join('prometheus', f) for f in os.listdir(os.path.join(ROOT, 'prometheus')) if f.endswith('.py'))


def _code_strings(path):
    """Every string constant in the file that is not a docstring,
    f-string pieces included."""
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            out.append((node.lineno, node.value))
    return out


class StringSweep(unittest.TestCase):
    def test_no_reader_facing_line_from_the_family_remains(self):
        hits = []
        for rel in SWEEP_FILES:
            for lineno, s in _code_strings(os.path.join(ROOT, rel)):
                low = s.lower()
                for ph in RETIRED_PHRASES:
                    if ph in low:
                        hits.append(f"{rel}:{lineno}: {ph!r} in {s[:80]!r}")
        self.assertEqual(hits, [])

    def test_fault_paths_hand_the_reader_the_calm_line(self):
        calm = 'Working on it. Confirmed your task is in progress and I will email you when it completes.'
        self.assertEqual(RR.CALM_LINE, calm)
        self.assertEqual(PR.CALM_LINE, calm)
        self.assertEqual(RR.fault_reply({'question': 'anything'}, 'no_figures', {}), calm)
        self.assertEqual(RR.FINAL_NOTE, calm)
        self.assertEqual(RR.FINAL_NOTE_FREE, calm)
        self.assertEqual(V.left_out_line('q', ['x']), '')
        self.assertEqual(pma.missing_pieces_line(['x']), '')
        with open(os.path.join(ROOT, 'prometheus', 'priced_read.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("'reply': CALM_LINE", src)
        self.assertIn("_mail('prometheus/priced-read'", src)
        with open(os.path.join(ROOT, 'prometheus', 'drilldown.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("host.error_email('prometheus/drilldown',", src)
        self.assertIn('found no confirmable names', src)
        with open(os.path.join(ROOT, 'prometheus', 'read_recovery.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("'brief-chat/read-recovery', fault_note(status, cause, rr)", src)
        self.assertEqual(OF.CALM_LINE, calm)
        self.assertEqual(OF.reply_for('yes', {'label': 'Gunna Concert Goers'}, {'success': False, 'error': calm}), calm)
        self.assertEqual(OF.reply_for('yes', {'label': 'Gunna Concert Goers'}, {}), calm)
        with open(os.path.join(ROOT, 'prometheus', 'offers.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("'held': 'Working on it.'", src)
        self.assertNotIn("'failed': 'Not started.'", src)
        self.assertIn("return page('Working on it.', CALM_LINE, ok=True), 200", src)
        with open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("_state, _reply, _odoc = 'held', _offers.CALM_LINE, None", src)


class _FakeS3:
    def __init__(self):
        self.docs = {}

    def get_object(self, Bucket, Key):
        import io
        if Key not in self.docs:
            raise KeyError(Key)
        return {'Body': io.BytesIO(self.docs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.docs[Key] = Body


class OffersHold(unittest.TestCase):
    """A pull that did not start: one retry after a short backoff, then
    the calm line, the offer back to pending, ops told; never a failure
    notice, never a charge without a run (2026-11-04)."""
    CALM = {'success': False, 'error': OF.CALM_LINE, '_http': 200}      # the approve route's calm payload (queue down)

    def setUp(self):
        self.s3 = _FakeS3()
        self.orig = {k: getattr(OF, k) for k in ('_s3', 'launch', '_sleep', '_ops_email', '_refund', 'append_turns')}
        self.sleeps, self.mails, self.refunds, self.turns = [], [], [], []
        OF._s3 = lambda: (self.s3, 'bucket')
        OF._sleep = self.sleeps.append
        OF._ops_email = lambda err, username='', payload=None: (self.mails.append((err, username, payload)) or True)
        OF._refund = lambda username, credits, reason, job_id='': (self.refunds.append((username, credits, reason, job_id)) or True)
        OF.append_turns = lambda doc, turns: self.turns.extend(turns)
        self.doc, _links = OF.create('eliot', label='Gunna Concert Goers', prompt='people who attended a Gunna concert',
                                     spec_draft={'subject': 'Gunna Concert Goers', 'decision': 'new_build'}, thread_id='t1')

    def tearDown(self):
        for k, v in self.orig.items():
            setattr(OF, k, v)

    def _launch_seq(self, *results):
        it = iter(results)
        calls = {'n': 0}

        def launch(app, doc):
            calls['n'] += 1
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return dict(r)
        OF.launch = launch
        return calls

    def test_a_queue_hiccup_self_heals_on_the_retry(self):
        calls = self._launch_seq(self.CALM, {'success': True, 'run_id': 'run_1', 'credits_charged': 275})
        state, reply, doc = OF.decide(None, self.doc['id'], 'yes', via='email')
        self.assertEqual(state, 'launched')
        self.assertEqual(calls['n'], 2)
        self.assertEqual(self.sleeps, [OF.RETRY_BACKOFF_S])
        self.assertEqual(doc['status'], 'launched')
        self.assertEqual(doc['run_id'], 'run_1')
        self.assertTrue(reply.startswith('On it. Pulling Gunna Concert Goers now.'))
        self.assertIn('charged', reply)
        self.assertEqual(self.mails, [])
        self.assertEqual(self.refunds, [])
        self.assertEqual(len(doc['last_attempt']['attempts']), 2)

    def test_still_not_started_holds_the_offer_and_tells_ops(self):
        calls = self._launch_seq(self.CALM, RuntimeError('queue refused'))
        state, reply, doc = OF.decide(None, self.doc['id'], 'yes', via='chat', user_text='Yes, pull it')
        self.assertEqual(state, 'held')
        self.assertEqual(calls['n'], 2)
        self.assertEqual(reply, OF.CALM_LINE)
        for phrase in ('could not', 'not started', 'nothing was charged', 'ask me in the dashboard'):
            self.assertNotIn(phrase, reply.lower())
        stored = OF.load(self.doc['id'])
        self.assertEqual(stored['status'], 'pending', 'the pull stays pending on the server')
        self.assertIsNone(stored['decision'])
        self.assertIsNone(stored['run_id'])
        self.assertTrue(stored['held'])
        self.assertEqual(stored['last_attempt']['state'], 'held')
        self.assertEqual(stored['last_attempt']['refunded_credits'], 0)
        self.assertEqual(len(self.mails), 1)
        err, username, payload = self.mails[0]
        self.assertEqual(username, 'eliot')
        self.assertIn(self.doc['id'], err)
        self.assertIn('Gunna Concert Goers', err)
        self.assertIn('RuntimeError: queue refused', err)
        self.assertIn('nothing charged', err)
        self.assertEqual(payload['offer_id'], self.doc['id'])
        self.assertEqual(payload['user'], 'eliot')
        self.assertEqual(payload['subject'], 'Gunna Concert Goers')
        self.assertEqual(len(payload['attempts']), 2)
        # the thread got the user's yes and the calm line, marked held
        self.assertEqual([t['role'] for t in self.turns], ['user', 'agent'])
        self.assertEqual(self.turns[1]['text'], OF.CALM_LINE)
        self.assertTrue(self.turns[1]['meta']['held'])
        # a later yes (or an ops retry) starts it: the offer was never closed as failed
        self.turns.clear()
        self._launch_seq({'success': True, 'run_id': 'run_2', 'credits_charged': 0})
        state2, reply2, doc2 = OF.retry(None, self.doc['id'])
        self.assertEqual(state2, 'launched')
        self.assertEqual(doc2['via'], 'ops')
        self.assertEqual(OF.load(self.doc['id'])['status'], 'launched')

    def test_a_reply_meant_for_the_reader_is_shown_and_the_offer_stays_open(self):
        funds = {'success': True, 'action': 'answer', 'reply': 'This pull needs funds on your account. Add funds and say yes again.',
                 'no_funds': True, '_http': 200}
        calls = self._launch_seq(funds)
        state, reply, doc = OF.decide(None, self.doc['id'], 'yes', via='email')
        self.assertEqual(state, 'guidance')
        self.assertEqual(calls['n'], 1, 'a reply for the reader is not retried')
        self.assertEqual(self.sleeps, [])
        self.assertEqual(reply, funds['reply'])
        self.assertEqual(OF.load(self.doc['id'])['status'], 'pending')
        self.assertEqual(self.mails, [])

    def test_a_charge_with_no_run_is_refunded_and_reported(self):
        bad = {'success': False, 'error': OF.CALM_LINE, 'credits_charged': 275, '_http': 200}
        self._launch_seq(bad, bad)
        state, reply, doc = OF.decide(None, self.doc['id'], 'yes', via='email')
        self.assertEqual(state, 'held')
        self.assertEqual(reply, OF.CALM_LINE)
        # one refund per attempt, each with its own idempotency key so a
        # replayed decision can never reverse the same charge twice
        self.assertEqual(self.refunds, [('eliot', 275, f"offer {self.doc['id']}: charged with no run id",
                                         f"offer-{self.doc['id']}-{i}") for i in range(2)])
        self.assertEqual(doc['last_attempt']['refunded_credits'], 550)
        self.assertEqual(len(self.mails), 1)
        self.assertIn('refunded 550 credit(s) charged with no run', self.mails[0][0])

    def test_no_declines_and_a_second_decision_sees_already(self):
        state, reply, doc = OF.decide(None, self.doc['id'], 'no', via='chat', user_text='No')
        self.assertEqual(state, 'declined')
        self.assertIn('I will not pull the Gunna Concert Goers profile', reply)
        state2, reply2, _ = OF.decide(None, self.doc['id'], 'yes', via='email')
        self.assertEqual(state2, 'already')
        self.assertIn('Already noted', reply2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
