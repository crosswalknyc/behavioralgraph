#!/usr/bin/env python3
"""A title that lives on one service answers a no-service question from that record (2026-11-12).

Round 5 made a question that names no service never take one service's
figure. For a title whose only US home is one service that sends a
reader to a priced read for a number already held: there the one-service
audience is the whole audience.

Jenna: exempt a title only when the verified-carrier record shows it on
exactly one US service for the window. Then a no-service question is
answered from the one-service record, and the reply names the service as
the title's home in plain words. A title on two or more services, or one
whose carriers are unverified, keeps the round 5 behavior. No guessing:
unverified means not exempt.

Held here with the records as the check found them on 2026-11-12:

  Fallout Season 2                 Prime Video only                -> exempt
  My Life with the Walter Boys     Netflix only                    -> exempt
  Helluva Boss                     YouTube AND Prime Video (seasons
                                   1 and 2 from September 10 2025) -> not exempt
  Georgie & Mandy's First Marriage several services                -> never exempt
  a title with no check on record                                  -> not exempt

Hermetic: the ledger index, the catalog page and the title record are
stubs built from the real rows; no S3, no model, no app.

    python3 scripts/test_pm_single_home_title.py
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
from migration import service_scope_sizing as S  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402
from prometheus import service_scope_match as M  # noqa: E402

GM = "Georgie & Mandy's First Marriage"
W_FALLOUT = {'start': '2025-11-01', 'end': '2026-10-31'}
W_WALTER = {'start': '2025-11-10', 'end': '2026-11-10'}
W_HELLUVA = {'start': '2024-01-01', 'end': '2026-09-30'}
W_GM = {'start': '2024-10-25', 'end': '2026-11-10'}


def carriers(names, window):
    return {'carriers': {'services': S.carrier_services(names), 'window': dict(window), 'verified_at': '2026-11-12T11:00:00Z',
                         'basis': ['carriage research']}}


# the title records, as the check wrote them (title key -> record)
RECORDS = {
    'FALLOUT': carriers(['Amazon Prime Video', 'Amazon Prime Video with Ads'], W_FALLOUT),
    'MYLIFEWITHTHEWALTERBOYS': carriers(['Netflix', 'Netflix Standard with Ads'], W_WALTER),
    'HELLUVABOSS': carriers(['YouTube (VivziePop / SpindleHorse official channel)', 'Amazon Prime Video (SVOD)',
                             'Amazon Prime Video with Ads (AVOD)'], W_HELLUVA),
    'GEORGIEMANDYSFIRSTMARRIAGE': carriers(['Max', 'HBO Max Amazon Channel', 'Paramount+', 'Paramount+ Amazon Channel', 'Hulu',
                                            'Amazon Prime Video'], W_GM),
    # 'THESUMMERITURNEDPRETTY': nothing on record (never checked)
}


class _Records:
    """The title record store, for the duration of a test."""

    def __enter__(self):
        self._real = S.load_record
        S.load_record = lambda title, s3=None: dict(RECORDS.get(S.title_key(title)) or {})
        return self

    def __exit__(self, *exc):
        S.load_record = self._real


def entry(subject, q, people, ws, we, ts, route='reasoned_metrics', reply=None):
    return {'k': il.entry_key(subject, 'viewership', ws, we), 'subject': subject, 'family': 'viewership', 'q': q,
            'qn': il.normalize_question(q), 'route': route, 'ts': ts, 'ws': ws, 'we': we, 'wl': f'{ws} to {we}',
            'metrics': [{'name': 'unique_us_viewers', 'label': 'Unique US viewers', 'value': people, 'unit': 'viewers'}],
            'anchors': [], 'base': '', 'cohort': '', 'universe': subject, 'breakdown': None,
            'reply': reply or f"On {subject}:\n\n{people:,} projected US people.", 'followups': [], 'derivation': '', 'prov': 'operator'}


F_SUBJ = "Fallout Season 2 Viewers on Prime Video"
W_SUBJ = "My Life with the Walter Boys Viewers on Netflix"
H_SUBJ = "Helluva Boss (YouTube viewers)"
G_SUBJ = GM + " Viewers (Paramount+)"
U_SUBJ = "The Summer I Turned Pretty Viewers on Prime Video"


def _trailing_year():
    """The standing window on the day the test runs. The ledger's own
    rule (not under test here) replays a read by meaning for a question
    that states no window only when the read is on that window, so the
    saved answers below sit on it."""
    from migration.event_window import default_window
    a, b = default_window()
    return str(a)[:10], str(b)[:10]


NOW_S, NOW_E = _trailing_year()
W_NOW = {'start': NOW_S, 'end': NOW_E}
for _k in ('FALLOUT', 'MYLIFEWITHTHEWALTERBOYS', 'HELLUVABOSS', 'GEORGIEMANDYSFIRSTMARRIAGE'):
    RECORDS[_k + '@now'] = {'carriers': dict(RECORDS[_k]['carriers'], window=dict(W_NOW))}


class _RecordsNow(_Records):
    """The same checks, covering the window the saved answers sit on."""

    def __enter__(self):
        self._real = S.load_record
        S.load_record = lambda title, s3=None: dict(RECORDS.get(S.title_key(title) + '@now') or {})
        return self


def index():
    ts = NOW_E + 'T01:00:00Z'
    return {'updated': ts, 'subjects': {
        'fallout_season_2_viewers': {'subject': F_SUBJ, 'entries': [
            entry(F_SUBJ, "How many people watched Fallout Season 2 on Prime Video?", 13732978, NOW_S, NOW_E, ts)]},
        'my_life_with_the_walter_boys_viewers': {'subject': W_SUBJ, 'entries': [
            entry(W_SUBJ, "How many people watched My Life with the Walter Boys on Netflix?", 13315259, NOW_S, NOW_E, ts)]},
        'helluva_boss_youtube_viewers': {'subject': H_SUBJ, 'entries': [
            entry(H_SUBJ, "How many people watched Helluva Boss on YouTube?", 8071234, NOW_S, NOW_E, ts)]},
        'georgie_mandy_first_marriage_viewers': {'subject': G_SUBJ, 'aliases': [GM + " Viewers - Paramount+"], 'entries': [
            entry(G_SUBJ, f"How many people watched {GM} on Paramount+?", 1285455, NOW_S, NOW_E, ts)]},
        'the_summer_i_turned_pretty_viewers': {'subject': U_SUBJ, 'entries': [
            entry(U_SUBJ, "How many people watched The Summer I Turned Pretty on Prime Video?", 9412387, NOW_S, NOW_E, ts)]},
    }}


def use(doc):
    with il._cache_lock:
        il._index_cache['doc'] = doc
        il._index_cache['ts'] = time.time() + 3600


def consult(q, subject=None):
    with contextlib.redirect_stdout(io.StringIO()):
        r = il.consult(subject=subject, question=q)
    ex = r.get('exact') or {}
    return next((m['value'] for m in (ex.get('metrics') or [])), None), str(ex.get('reply') or '')


class TheRecord(unittest.TestCase):

    def test_one_service_for_the_window_is_a_single_home(self):
        with _Records():
            self.assertEqual(M.single_home('Fallout Season 2', W_FALLOUT), 'amazon')
            self.assertEqual(M.single_home('Fallout', W_FALLOUT), 'amazon')
            self.assertEqual(M.single_home('My Life with the Walter Boys', W_WALTER), 'netflix')
            self.assertEqual(M.single_home('Fallout Season 2', {'start': '2026-01-01', 'end': '2026-06-30'}), 'amazon')   # inside the window checked

    def test_two_services_or_no_check_is_not(self):
        with _Records():
            self.assertEqual(M.single_home('Helluva Boss', W_HELLUVA), '')                    # YouTube and Prime Video
            self.assertEqual(M.single_home(GM, W_GM), '')                                      # several
            self.assertEqual(M.single_home('The Summer I Turned Pretty', W_FALLOUT), '')       # never checked
            self.assertEqual(M.single_home('Fallout Season 2', {'start': '2024-01-01', 'end': '2026-10-31'}), '')        # more than was checked
            self.assertEqual(M.single_home('Fallout Season 2', None), '')                      # no window, no claim
        self.assertEqual(M.single_home('Fallout Season 2', W_FALLOUT), '')                     # hermetic, no store: unverified

    def test_a_tier_of_a_service_is_the_service(self):
        self.assertEqual(S.carrier_services(['Netflix', 'Netflix Standard with Ads']), ['netflix'])
        self.assertEqual(S.carrier_services(['Amazon Prime Video', 'Amazon Prime Video with Ads']), ['amazon'])
        self.assertEqual(S.carrier_services(['YouTube (the official channel)', 'Amazon Prime Video (SVOD)']), ['amazon', 'youtube'])
        self.assertEqual(S.carrier_services(['Peacock Premium', 'Peacock Premium Plus']), ['peacock'])
        self.assertIsNone(S.carrier_services(['(   )']))                                       # a name that cannot be read: nothing is known

    def test_the_latest_check_replaces_the_one_before(self):
        rec = S.merge_record({}, title='Fallout', carriers={'services': ['Amazon Prime Video'], 'window': W_FALLOUT})
        self.assertEqual(rec['carriers']['services'], ['amazon'])
        rec = S.merge_record(rec, carriers={'services': ['Amazon Prime Video', 'Hulu'], 'window': W_FALLOUT})
        self.assertEqual(rec['carriers']['services'], ['amazon', 'hulu'])
        self.assertEqual(S.single_home('Fallout', W_FALLOUT, record=rec), '')
        same = S.merge_record(rec, carriers={'services': [], 'window': W_FALLOUT})             # a check that found nothing changes nothing
        self.assertEqual(same['carriers']['services'], ['amazon', 'hulu'])
        self.assertEqual(S.merge_record(rec, build={'services': ['amazon'], 'people': 5, 'window': W_FALLOUT})['carriers'], rec['carriers'])


class TheSavedAnswer(unittest.TestCase):

    def setUp(self):
        use(index())

    def test_fallout_season_2_is_answered_and_names_prime_video_as_its_home(self):
        with _RecordsNow():
            people, reply = consult("How many people watched Fallout Season 2?")
            self.assertEqual(people, 13732978)
            self.assertTrue(reply.startswith("Prime Video is the only US home of Fallout Season 2 in this window, "
                                             "so this is the title's whole audience."), reply)
            self.assertIn('13,732,978', reply)

    def test_my_life_with_the_walter_boys_is_answered_and_names_netflix(self):
        with _RecordsNow():
            people, reply = consult("How many people watched My Life with the Walter Boys?")
            self.assertEqual(people, 13315259)
            self.assertTrue(reply.startswith("Netflix is the only US home of My Life with the Walter Boys in this window"), reply)

    def test_helluva_boss_is_on_two_services_and_is_not_exempt(self):
        with _RecordsNow():
            for q in ("How many people watched Helluva Boss?", "How big is the Helluva Boss audience?"):
                people, reply = consult(q)
                self.assertIsNone(people, q)                       # the YouTube figure never answers the every-service question
            self.assertEqual(consult("How many people watched Helluva Boss on YouTube?")[0], 8071234)       # the service asked still answers

    def test_georgie_and_mandy_is_never_exempt(self):
        with _RecordsNow():
            for q in (f"How many people watched {GM}?", f"How big is the {GM} audience?"):
                self.assertIsNone(consult(q)[0], q)
            self.assertEqual(consult(f"How many people watched {GM} on Paramount+?")[0], 1285455)

    def test_an_unverified_title_is_not_exempt(self):
        with _RecordsNow():
            for q in ("How many people watched The Summer I Turned Pretty?", "How big is The Summer I Turned Pretty audience?"):
                self.assertIsNone(consult(q)[0], q)
        # and with no record store at all nothing is exempt, Fallout included
        self.assertIsNone(consult("How many people watched Fallout Season 2?")[0])

    def test_the_home_line_is_said_once_and_the_saved_answer_is_untouched(self):
        with _RecordsNow():
            doc = index()
            use(doc)
            _people, reply = consult("How many people watched Fallout Season 2?")
            self.assertEqual(reply.count(M._HOME_MARK), 1)
            saved = doc['subjects']['fallout_season_2_viewers']['entries'][0]['reply']
            self.assertNotIn(M._HOME_MARK, saved)                  # a copy carries the line; the record is as it was
            # a question that names the service gets the answer as saved, without the line
            _people, reply = consult("How many people watched Fallout Season 2 on Prime Video?")
            self.assertNotIn(M._HOME_MARK, reply)
            # and another service is still never answered from it
            self.assertIsNone(consult("How many people watched Fallout Season 2 on Netflix?")[0])

    def test_the_lookup_by_service_on_the_real_windows(self):
        """The judgement itself, on the windows the records are really on
        (no dependence on today's date)."""
        doc = index()
        cases = (('fallout_season_2_viewers', W_FALLOUT, "How many people watched Fallout Season 2?", 'home:amazon'),
                 ('my_life_with_the_walter_boys_viewers', W_WALTER, "How big is the My Life with the Walter Boys audience?", 'home:netflix'))
        with _Records():
            for key, w, q, want in cases:
                b = doc['subjects'][key]
                b['entries'][0].update(ws=w['start'], we=w['end'])
                self.assertEqual(M.home_bucket_for(doc, q, il._has_live_entries), key, q)
                self.assertEqual(M.replay_fits_services(b['entries'][0], b, q), (True, want), q)
            for key, w, q in (('helluva_boss_youtube_viewers', W_HELLUVA, "How many people watched Helluva Boss?"),
                              ('georgie_mandy_first_marriage_viewers', W_GM, f"How many people watched {GM}?"),
                              ('the_summer_i_turned_pretty_viewers', W_FALLOUT, "How many people watched The Summer I Turned Pretty?")):
                b = doc['subjects'][key]
                b['entries'][0].update(ws=w['start'], we=w['end'])
                self.assertIsNone(M.home_bucket_for(doc, q, il._has_live_entries), q)
                ok, why = M.replay_fits_services(b['entries'][0], b, q)
                self.assertFalse(ok, q)
                self.assertIn('names no service', why)
            # a window the check does not cover is not exempt
            b = doc['subjects']['fallout_season_2_viewers']
            b['entries'][0].update(ws='2024-04-01', we='2026-10-31')
            self.assertFalse(M.replay_fits_services(b['entries'][0], b, "How many people watched Fallout Season 2?")[0])
            # an answer with no window of its own is not exempt either
            b['entries'][0].update(ws='', we='')
            self.assertFalse(M.replay_fits_services(b['entries'][0], b, "How many people watched Fallout Season 2?")[0])
            # a question that names the service, or another one, is judged as before
            b['entries'][0].update(ws=W_FALLOUT['start'], we=W_FALLOUT['end'])
            self.assertEqual(M.replay_fits_services(b['entries'][0], b, "How many watched Fallout Season 2 on Prime Video?"), (True, ''))
            self.assertFalse(M.replay_fits_services(b['entries'][0], b, "How many watched Fallout Season 2 on Netflix?")[0])
            self.assertIsNone(M.home_bucket_for(doc, "How many watched Fallout Season 2 on Netflix?", il._has_live_entries))

    def test_a_profile_record_names_its_window(self):
        e = entry(F_SUBJ, 'Profile IQ profile: ' + F_SUBJ, 13732978, '', '', '2026-11-10T22:45:22Z', route='delivered_profile',
                  reply='Profile IQ profile Fallout_Season_2_Viewers_on_Prime_Video_11_10_2026_21_37.csv')
        real = M.window_of_key
        M.window_of_key = lambda key: dict(W_FALLOUT) if key == 'Fallout_Season_2_Viewers_on_Prime_Video_11_10_2026_21_37.csv' else None
        try:
            self.assertEqual(M._entry_window(e), W_FALLOUT)
            with _Records():
                self.assertEqual(M.home_of_entry(e, {'subject': F_SUBJ}), ('amazon', 'Fallout Season 2'))
            self.assertIs(M.with_home_line(e, 'Fallout Season 2', 'amazon'), e)          # a file pointer is not a reply to lead
        finally:
            M.window_of_key = real

    def test_nothing_internal_in_the_line(self):
        line = M.home_line('Fallout Season 2', 'amazon')
        for word in ('verified', 'record', 'carrier', 'research', 'check', 'content map', 'source', 'estimate', 'model'):
            self.assertNotIn(word, line.lower())
        self.assertNotIn('\u2014', line)


# ------------------------------------------------------------------ the size lookup (first in line)
def fact(kind, value, key, window, as_of):
    return {'product': 'profile', 'kind': kind, 'value': value, 'unit': 'people', 'window': window, 'as_of': as_of,
            'source': {'key': key, 'user': ''}, 'label': kind}


F_KEY = "Fallout_Season_2_Viewers_on_Prime_Video_11_10_2026_21_37.csv"
W_KEY = "My_Life_with_the_Walter_Boys_Viewers_on_Netflix_11_10_2026_23_29.csv"
U_KEY = "The_Summer_I_Turned_Pretty_Viewers_on_Prime_Video_11_10_2026_20_00.csv"
P_KEY = "GEORGIE__MANDYS_FIRST_MARRIAGE_Viewers_(Paramount+)_11_11_2026_22_26.csv"
H_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO_Max)_11_11_2026_15_44.csv"
PAGES = {
    'fallout season 2': {'subject_key': 'fallout_season_2', 'subject': F_SUBJ, 'aliases': [F_SUBJ, F_SUBJ + ' - Avid Fan'],
                         'facts': [fact('sample_size', 416277, F_KEY, W_FALLOUT, '2026-11-10T22:45:22'),
                                   fact('audience_size', 13732978, F_KEY, W_FALLOUT, '2026-11-10T22:45:22')]},
    'my life with the walter boys': {'subject_key': 'my_life_with_walter_boys', 'subject': 'My Life with the Walter Boys',
                                     'aliases': ['My Life with the Walter Boys', W_SUBJ, W_SUBJ + ' - Avid Fan'],
                                     'facts': [fact('sample_size', 403615, W_KEY, W_WALTER, '2026-11-11T01:30:00'),
                                               fact('audience_size', 13315259, W_KEY, W_WALTER, '2026-11-11T01:30:00')]},
    'the summer i turned pretty': {'subject_key': 'the_summer_i_turned_pretty', 'subject': U_SUBJ, 'aliases': [U_SUBJ],
                                   'facts': [fact('sample_size', 285310, U_KEY, W_FALLOUT, '2026-11-10T20:00:00'),
                                             fact('audience_size', 9412387, U_KEY, W_FALLOUT, '2026-11-10T20:00:00')]},
    GM.lower(): {'subject_key': 'georgie_and_mandy_s_first_marriage', 'subject': GM + ' Viewers',
                 'aliases': [G_SUBJ, GM + " Viewers (HBO Max)"],
                 'facts': [fact('sample_size', 38965, P_KEY, W_GM, '2026-11-12T00:00:48'), fact('audience_size', 1285455, P_KEY, W_GM, '2026-11-12T00:00:48'),
                           fact('sample_size', 12263, H_KEY, {'start': '2026-08-07', 'end': '2026-11-10'}, '2026-11-12T04:59:09'),
                           fact('audience_size', 404556, H_KEY, {'start': '2026-08-07', 'end': '2026-11-10'}, '2026-11-12T04:59:09')]},
}


class _Catalog:
    def __enter__(self):
        self._a, self._l = cc.anchors_for, cc.load_subject_page

        def anchors(subject, **kw):
            want = str(subject or '').strip().lower()
            page = next((p for name, p in PAGES.items() if want.startswith(name)), None)
            if not page:
                return {'facts': [], 'related': []}
            return {'subject_key': page['subject_key'], 'subject': page['subject'], 'facts': list(page['facts']),
                    'products': ['profile'], 'ledger': [], 'related': [], 'trends': []}
        cc.anchors_for = anchors
        cc.load_subject_page = lambda skey: next((p for p in PAGES.values() if p['subject_key'] == skey), {})
        return self

    def __exit__(self, *exc):
        cc.anchors_for, cc.load_subject_page = self._a, self._l


class TheSizeLookup(unittest.TestCase):

    def test_fallout_season_2(self):
        with _Catalog(), _Records():
            for q in ("How many people watched Fallout Season 2?", "How big is the Fallout Season 2 audience?"):
                r = CL.answer(q)
                self.assertIsNotNone(r, q)
                self.assertEqual(r['reply'], "Fallout Season 2 Viewers on Prime Video: 13,732,978 US people (2025-11-01 to 2026-10-31), "
                                             "416,277 inside the 10 million sample. Prime Video is the only US home of Fallout Season 2 "
                                             "in this window, so this is the title's whole audience.")

    def test_my_life_with_the_walter_boys(self):
        with _Catalog(), _Records():
            for q in ("How many people watched My Life with the Walter Boys?", "How big is the My Life with the Walter Boys audience?"):
                r = CL.answer(q)
                self.assertIsNotNone(r, q)
                self.assertIn("13,315,259 US people (2025-11-10 to 2026-11-10), 403,615 inside the 10 million sample.", r['reply'])
                self.assertTrue(r['reply'].endswith("Netflix is the only US home of My Life with the Walter Boys in this window, "
                                                    "so this is the title's whole audience."), r['reply'])

    def test_the_same_window_stated_is_answered_and_another_is_not(self):
        with _Catalog(), _Records():
            r = CL.answer("How many people watched Fallout Season 2 from November 1 2025 to October 31 2026?")
            self.assertIsNotNone(r)
            self.assertIn('13,732,978', r['reply'])
            self.assertIsNone(CL.answer("How many people watched Fallout Season 2 from January 1 2026 to March 31 2026?"))

    def test_georgie_and_mandy_is_never_exempt(self):
        with _Catalog(), _Records():
            self.assertIsNone(CL.answer(f"How many people watched {GM}?"))
            self.assertIsNone(CL.answer(f"How big is the {GM} audience?"))
            self.assertIn('1,285,455', CL.answer(f"How many people watched {GM} on Paramount+?")['reply'])

    def test_an_unverified_title_is_not_exempt(self):
        with _Catalog(), _Records():
            self.assertIsNone(CL.answer("How many people watched The Summer I Turned Pretty?"))
        with _Catalog():                                           # no record store: nothing is exempt
            self.assertIsNone(CL.answer("How many people watched Fallout Season 2?"))

    def test_a_service_named_is_answered_as_before(self):
        with _Catalog(), _Records():
            r = CL.answer("How many people watched Fallout Season 2 on Prime Video?")
            self.assertIn('13,732,978 US people', r['reply'])
            self.assertIn('That is the profile on the dashboard.', r['reply'])
            self.assertNotIn(M._HOME_MARK, r['reply'])
            self.assertIsNone(CL.answer("How many people watched Fallout Season 2 on Netflix?"))


if __name__ == '__main__':
    unittest.main(verbosity=1)
