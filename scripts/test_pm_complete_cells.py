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

import prometheus_verify as V  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
from prometheus import page_default as P  # noqa: E402
from prometheus import methodology as M  # noqa: E402

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
