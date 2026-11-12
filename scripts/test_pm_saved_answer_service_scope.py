#!/usr/bin/env python3
"""A saved answer on named services answers a question about exactly those services (2026-11-12).

Georgie & Mandy's First Marriage is on record three ways: viewers on
Paramount+ (1,285,455), on HBO Max (404,556), on HBO Max and Amazon
Prime Video (957,139). Two holes in the saved-answer lookup:

  * "on Amazon and HBO Max" was looked up under the HBO bucket and found
    nothing: 'Amazon' without 'Prime Video' was not read as the service;
  * a question that named no service ("How many people watched Georgie &
    Mandy's First Marriage?") was handed the Paramount+ figure.

One table reads services everywhere (prometheus.service_scope_match over
migration.service_scope_sizing.canon_service). A question is answered
from the scope it names, in any order and any spelling of the service; a
question that names none is about the whole audience and is never
answered with one service's figure; it gets the whole on record for the
window it states, labelled as the whole, or it goes to a read.

Hermetic: the ledger index and the catalog page are stubs built from the
real rows; no S3, no model, no app.

    python3 scripts/test_pm_saved_answer_service_scope.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import time
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as il  # noqa: E402
from migration import corpus_catalog as cc  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402
from prometheus import service_scope_match as M  # noqa: E402

T = "Georgie & Mandy's First Marriage"
P_SUBJ = T + " Viewers"                                                    # the bucket Jessie's Paramount+ reads sit in
H_SUBJ = T + " (HBO Max viewers, all 3 seasons)"
A_SUBJ = T + " (HBO Max and Amazon Prime Video viewers)"


def record(subject, q, people, universe, ws, we, ts):
    return {'k': il.entry_key(subject, 'viewership', ws, we), 'subject': subject, 'family': 'viewership', 'q': q,
            'qn': il.normalize_question(q), 'route': 'decision_of_record_profile', 'ts': ts, 'ws': ws, 'we': we, 'wl': f'{ws} to {we}',
            'metrics': [{'name': 'unique_us_viewers', 'label': 'Unique US viewers', 'value': people, 'unit': 'viewers'}],
            'anchors': [], 'base': '', 'cohort': '', 'universe': universe, 'breakdown': None,
            'reply': f"On {universe}:\n\n{T} reads {people:,} US viewers.", 'followups': [], 'derivation': '', 'prov': 'decision_of_record'}


def read(subject, q, people, ts):
    e = record(subject, q, people, subject, '2024-10-25', '2026-11-10', ts)
    e.update(route='reasoned_metrics', prov='operator', reply=f"On {subject}:\n\nA Paramount+ only universe of {people:,} projected US people.")
    return e


def index():
    return {'updated': '2026-11-12T09:00:00Z', 'subjects': {
        'georgie_mandy_first_marriage_viewers': {
            'subject': P_SUBJ,
            'aliases': ["GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)", T + " Viewers - Paramount+", T + " Paramount+ viewers"],
            'entries': [
                read(T + " Viewers - Paramount+", "it's wrong. you only ran the profile on the 5 Paramount Plus placements. The profile of "
                     "Georgie & Mandy's First Marriage Viewers ONLY on Paramount+", 1285455, '2026-11-11T23:05:20Z'),
                record(P_SUBJ, f"How many people watched {T} on Paramount+?", 1285455, 'Paramount+ viewers in the US', '2024-10-25', '2026-11-10',
                       '2026-11-12T08:13:24Z'),
                record(P_SUBJ, f"How big is the {T} audience on Paramount+?", 1285455, 'Paramount+ viewers in the US', '2024-10-25', '2026-11-10',
                       '2026-11-12T08:13:26Z')]},
        'georgie_mandy_first_marriage_hbo_max_viewers_all_3_seasons': {
            'subject': H_SUBJ, 'aliases': [T + " Viewers (HBO Max)", T + " HBO Max viewers"],
            'entries': [record(H_SUBJ, f"How many people watched {T} on HBO Max?", 404556, 'HBO Max viewers in the US', '2026-08-07', '2026-11-10',
                               '2026-11-12T06:53:53Z')]},
        'georgie_mandy_first_marriage_hbo_max_and_amazon_prime_video_viewers': {
            'subject': A_SUBJ, 'aliases': [T + " Viewers (HBO Max & Amazon Prime)", T + " Viewers (Amazon Prime Video & HBO Max)"],
            'entries': [record(A_SUBJ, f"How many people watched {T} on HBO Max and Amazon Prime Video?", 957139,
                               'HBO Max and Amazon Prime Video viewers in the US', '2026-08-07', '2026-11-10', '2026-11-12T08:13:30Z'),
                        record(A_SUBJ, f"How big is the {T} audience on HBO Max and Amazon Prime Video?", 957139,
                               'HBO Max and Amazon Prime Video viewers in the US', '2026-08-07', '2026-11-10', '2026-11-12T08:13:32Z')]},
        # the platforms' own buckets, as the live index holds them
        'hbo': {'subject': 'HBO Max', 'entries': [record('HBO Max', 'who uses HBO Max', 131004219, 'HBO Max', '2025-10-08', '2026-10-08', '2026-10-08T01:00:00Z')]},
        'paramount': {'subject': 'Paramount+', 'entries': [record('Paramount+', 'who uses Paramount+', 138271944, 'Paramount+', '2025-10-08', '2026-10-08',
                                                                   '2026-10-08T01:00:00Z')]},
        'netflix': {'subject': 'Netflix', 'entries': [record('Netflix', 'how many people use Netflix', 224871663, 'Netflix viewers in the US',
                                                             '2025-10-08', '2026-10-08', '2026-10-08T01:00:00Z')]},
        'mad_men_viewers': {'subject': 'Mad Men Viewers', 'entries': [record('Mad Men Viewers', 'how many people watched Mad Men', 5231877, '',
                                                                             '2025-11-09', '2026-11-09', '2026-11-09T01:00:00Z')]},
    }}


def use(doc):
    with il._cache_lock:
        il._index_cache['doc'] = doc
        il._index_cache['ts'] = time.time() + 3600


def consult(q, subject=None):
    with contextlib.redirect_stdout(io.StringIO()):
        r = il.consult(subject=subject, question=q)
    ex = r.get('exact') or {}
    people = next((m['value'] for m in (ex.get('metrics') or [])), None)
    return r.get('skey'), people


class OneTable(unittest.TestCase):

    def test_a_service_is_one_however_it_is_written(self):
        for a, b in (('Amazon', 'Prime Video'), ('Amazon Prime', 'Amazon Prime Video'), ('HBO Max', 'Max'), ('HBOMax', 'HBO MAX'),
                     ('Paramount+', 'Paramount Plus')):
            self.assertEqual(M.services_named([a]), M.services_named([b]), (a, b))

    def test_the_services_a_label_is_on(self):
        self.assertEqual(M.services_of_label(H_SUBJ), M.services_named(['HBO Max']))
        self.assertEqual(M.services_of_label(A_SUBJ), M.services_named(['Amazon', 'Max']))
        self.assertEqual(M.services_of_label(T + " Viewers - Paramount+"), M.services_named(['Paramount+']))
        self.assertEqual(M.services_of_label('HBO Max and Amazon Prime Video viewers in the US'), M.services_named(['HBO Max', 'Prime Video']))
        self.assertEqual(M.services_of_label('Paramount+ viewers in the US'), M.services_named(['Paramount+']))
        for plain in (P_SUBJ, 'Mad Men Viewers', 'Netflix', 'Digital original content viewers', 'Heavy podcast listeners', ''):
            self.assertEqual(M.services_of_label(plain), frozenset(), plain)

    def test_the_services_a_question_is_about(self):
        two = M.services_named(['Amazon', 'HBO Max'])
        for q in (f"How many people watched {T} on Amazon and HBO Max?", f"How many people watched {T} on HBO Max and Amazon?",
                  f"How many people watched {T} on Prime Video and HBOMax?", f"what is the size of the {T} viewers on Amazon Prime and HBO Max"):
            self.assertEqual(M.services_in_question(q), two, q)
        self.assertEqual(M.services_in_question(f"How big is the {T} audience on Amazon and Max?", two), two)       # loose words, a title held there
        self.assertEqual(M.services_in_question(f"How big is the {T} audience on Amazon and Max?"), frozenset())    # and nothing without that
        for q in (f"How many people watched {T}?", f"How big is the {T} audience?",
                  f"How many people watched {T} from October 25 2024 to November 10 2026?"):
            self.assertEqual(M.services_in_question(q, two), frozenset(), q)
        for q in ("how many people shop on Amazon", "How big is the Amazon audience", "how big is the Nike audience on Amazon"):
            self.assertEqual(M.services_in_question(q), frozenset(), q)          # the store is not the service


class TheSavedAnswer(unittest.TestCase):

    def setUp(self):
        use(index())

    def test_each_scope_is_answered_from_its_own_record(self):
        self.assertEqual(consult(f"How many people watched {T} on Paramount+?"), ('georgie_mandy_first_marriage_viewers', 1285455))
        self.assertEqual(consult(f"How big is the {T} audience on Paramount+?"), ('georgie_mandy_first_marriage_viewers', 1285455))
        self.assertEqual(consult(f"How many people watched {T} on HBO Max?"),
                         ('georgie_mandy_first_marriage_hbo_max_viewers_all_3_seasons', 404556))

    def test_amazon_alone_is_the_service_in_either_order(self):
        want = ('georgie_mandy_first_marriage_hbo_max_and_amazon_prime_video_viewers', 957139)
        for q in (f"How many people watched {T} on Amazon and HBO Max?", f"How many people watched {T} on HBO Max and Amazon?",
                  f"How many people watched {T} on Prime Video and HBOMax?", f"How many people watched {T} on HBO Max and Amazon Prime Video?",
                  f"How big is the {T} audience on Amazon and Max?"):
            self.assertEqual(consult(q), want, q)

    def test_a_question_that_names_no_service_never_gets_one_services_figure(self):
        for q in (f"How many people watched {T}?", f"How big is the {T} audience?",
                  f"How many people watched {T} from October 25 2024 to November 10 2026?"):
            skey, people = consult(q)
            self.assertIsNone(people, q)                       # no replay: the question goes on to a read (or the whole on record)

    def test_a_service_not_on_record_gets_no_other_services_figure(self):
        for q in (f"How many people watched {T} on Peacock?", f"How many people watched {T} on Amazon?",
                  f"How many people watched {T} on HBO Max, Amazon and Paramount+?"):
            self.assertIsNone(consult(q)[1], q)

    def test_the_question_an_answer_was_given_to_still_replays(self):
        q = ("it's wrong. you only ran the profile on the 5 Paramount Plus placements. The profile of "
             "Georgie & Mandy's First Marriage Viewers ONLY on Paramount+")
        self.assertEqual(consult(q)[1], 1285455)

    def test_a_reader_on_the_profile_is_on_its_service(self):
        ok, why = M.replay_fits_services(index()['subjects']['georgie_mandy_first_marriage_viewers']['entries'][1],
                                         index()['subjects']['georgie_mandy_first_marriage_viewers'],
                                         'how big is this audience', subject="GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)")
        self.assertTrue(ok, why)
        ok, why = M.replay_fits_services(index()['subjects']['georgie_mandy_first_marriage_viewers']['entries'][1],
                                         index()['subjects']['georgie_mandy_first_marriage_viewers'],
                                         'how big is this audience', subject=T + " Viewers (HBO Max)")
        self.assertFalse(ok)

    def test_nothing_else_is_judged(self):
        doc = index()
        # a platform's own audience, and a title with no service on its record
        self.assertEqual(consult('how many people use Netflix'), ('netflix', 224871663))
        self.assertEqual(consult('how many people watched Mad Men'), ('mad_men_viewers', 5231877))
        self.assertTrue(M.replay_fits_services(doc['subjects']['netflix']['entries'][0], doc['subjects']['netflix'], 'how many subscribers')[0])
        self.assertTrue(M.replay_fits_services(doc['subjects']['mad_men_viewers']['entries'][0], doc['subjects']['mad_men_viewers'],
                                               'how big is it on Netflix')[0])
        self.assertTrue(M.replay_fits_services(None, None, 'x')[0])
        self.assertIsNone(M.bucket_for(doc, 'mad_men_viewers', 'how many people watched Mad Men on Netflix'))


# ------------------------------------------------------------------ the size lookup (first in line)
P_KEY = "GEORGIE__MANDYS_FIRST_MARRIAGE_Viewers_(Paramount+)_11_11_2026_22_26.csv"
H_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO_Max)_11_11_2026_15_44.csv"
T_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO Max & Amazon Prime)_11_10_2026_23_17.csv"
W_P = {'start': '2024-10-25', 'end': '2026-11-10'}
W_H = {'start': '2026-08-07', 'end': '2026-11-10'}
WHOLE = {'title': T, 'whole': {'people': 6220660, 'window': dict(W_P)}}


def fact(kind, value, key, window, as_of):
    return {'product': 'profile', 'kind': kind, 'value': value, 'unit': 'people', 'window': window, 'as_of': as_of,
            'source': {'key': key, 'user': ''}, 'label': kind}


PAGE = {'subject_key': 'georgie_and_mandy_s_first_marriage', 'subject': "GEORGIE & MANDY'S FIRST MARRIAGE Viewers",
        'aliases': ["GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)", T + " Viewers (HBO Max & Amazon Prime)", T + " Viewers (HBO Max)"],
        'facts': [fact('sample_size', 29013, T_KEY, W_H, '2026-11-12T05:00:43'), fact('audience_size', 957139, T_KEY, W_H, '2026-11-12T05:00:43'),
                  fact('sample_size', 12263, H_KEY, W_H, '2026-11-12T04:59:09'), fact('audience_size', 404556, H_KEY, W_H, '2026-11-12T04:59:09'),
                  fact('sample_size', 38965, P_KEY, W_P, '2026-11-12T00:00:48'), fact('audience_size', 1285455, P_KEY, W_P, '2026-11-12T00:00:48')]}


class _Catalog:
    def __enter__(self):
        self._a, self._l, self._w = cc.anchors_for, cc.load_subject_page, M.whole_on_record
        cc.anchors_for = lambda subject, **kw: {'subject_key': PAGE['subject_key'], 'subject': PAGE['subject'], 'facts': list(PAGE['facts']),
                                                'products': ['profile'], 'ledger': [], 'related': [], 'trends': []}
        cc.load_subject_page = lambda skey: PAGE if skey == PAGE['subject_key'] else {}
        real = self._w
        M.whole_on_record = lambda title, window, record=None: real(title, window, record=WHOLE if M.title_fold(title) == M.title_fold(T) else {})
        return self

    def __exit__(self, *exc):
        cc.anchors_for, cc.load_subject_page, M.whole_on_record = self._a, self._l, self._w


class TheSizeLookup(unittest.TestCase):

    def test_amazon_alone_in_either_order(self):
        with _Catalog():
            for q in (f"How many people watched {T} on Amazon and HBO Max?", f"How many people watched {T} on HBO Max and Amazon?",
                      f"How many people watched {T} on Prime Video and HBOMax?"):
                r = CL.answer(q)
                self.assertIsNotNone(r, q)
                self.assertIn('957,139 US people (2026-08-07 to 2026-11-10), 29,013 inside', r['reply'], q)
                self.assertTrue(r['reply'].startswith(T + " Viewers (HBO Max & Amazon Prime):"), r['reply'])

    def test_one_service_is_never_the_two_service_profile(self):
        with _Catalog():
            self.assertIsNone(CL.answer(f"How many people watched {T} on Amazon?"))
            self.assertIn('404,556', CL.answer(f"How many people watched {T} on Max?")['reply'])
            self.assertIn('1,285,455', CL.answer(f"How many people watched {T} on Paramount Plus?")['reply'])

    def test_no_service_gets_the_whole_on_record_for_the_window_asked(self):
        with _Catalog():
            r = CL.answer(f"How many people watched {T} from October 25 2024 to November 10 2026?")
            self.assertIsNotNone(r)
            self.assertIn('6,220,660 US people across every service (2024-10-25 to 2026-11-10)', r['reply'])
            self.assertIn('the whole audience on record, not one service', r['reply'])
            self.assertTrue(r['reply'].startswith(T + ":"), r['reply'])
            for n in ('1,285,455', '957,139', '404,556'):
                self.assertNotIn(n, r['reply'])

    def test_no_service_and_no_whole_on_that_window_goes_to_a_read(self):
        with _Catalog():
            self.assertIsNone(CL.answer(f"How many people watched {T}?"))                                         # no window stated
            self.assertIsNone(CL.answer(f"How many people watched {T} from August 7 2026 to November 10 2026?"))   # not the whole's window

    def test_the_whole_on_record(self):
        self.assertEqual(M.whole_on_record(T, W_P, record=WHOLE)['people'], 6220660)
        self.assertEqual(M.whole_on_record(T, {'start': '2024-10-26', 'end': '2026-11-09'}, record=WHOLE)['people'], 6220660)
        self.assertIsNone(M.whole_on_record(T, W_H, record=WHOLE))
        self.assertIsNone(M.whole_on_record(T, W_P, record={}))
        self.assertIsNone(M.whole_on_record(T, None, record=WHOLE))


if __name__ == '__main__':
    unittest.main(verbosity=1)
