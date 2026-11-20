#!/usr/bin/env python3
"""A brief that names its books, its base and its columns is one read on
those books (Grace Yin, Amazon, 2026-11-17; prometheus.read_brief).

Grace asked for the same table five ways in one night and each was lost
a different way:

  * the brief as a numbered list (eleven books, "Audience (base): all US
    people. Do NOT limit to YouTube viewers or any other platform",
    "each calendar year separately: 2021 ... 2026", eighteen columns)
    came back as six year profiles of the profile open on her screen
    (Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers) at $300
    each; with no profile open, as six year profiles of a library file
    one title resembled;
  * the same brief as prose for one year was read as a profile to build
    and never answered;
  * "For The Love Hypothesis and The Map That Leads To You, ... US
    people engaged, split into 1 touch, 2-3 touches and 4+ touches, with
    the count in the data" got the unprojected size of an unrelated
    profile;
  * "Split each title-year by engagement source (book order, Goodreads,
    TikTok, YouTube, search)" was counted on "people who watched ... on
    YouTube", so its totals disagreed with the read it split;
  * asked which was right, the reconcile answer kept the YouTube base.

Hermetic: a stub host, no model, no S3, no ledger. Each case is her
exact words.
"""
from __future__ import annotations

import os
import sys
import unittest

from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import read_brief as RB  # noqa: E402

SPEC = (
    'Audience (base): all US people. Do NOT limit to YouTube viewers or any other platform.\n\nWhat counts: enga'
    'gement with the book only. Exclude all activity on the film or TV adaptation, and exclude activity about '
    "the author that doesn't name the book.\n\nTime: each calendar year separately: 2021, 2022, 2023, 2024, 2025"
    ', and 2026 year to date. Each person is counted once per year.\n\nBooks (one row per book per year):\n1. The'
    ' Love Hypothesis \u2013 Ali Hazelwood\n2. The Map That Leads To You \u2013 J.P. Monninger\n3. Every Summer After \u2013 Ca'
    'rley Fortune\n4. The Deal \u2013 Elle Kennedy\n5. We Were Liars \u2013 E. Lockhart\n6. My Lady Jane \u2013 Cynthia Hand, Br'
    'odi Ashton, Jodi Meadows\n7. The Idea of You \u2013 Robinne Lee\n8. Culpa M\xeda \u2013 Mercedes Ron (book 1 only)\n9. Pe'
    "rfect Addiction \u2013 Claudia Tan\n10. Butcher & Blackbird \u2013 Brynne Weaver\n11. The Bad Boy's Girl \u2013 Blair Hold"
    'en (book 1 only)\n\nColumns, in this order:\n1. Book\n2. Year\n3. Total US people engaged\n4. Count in the data'
    ' (people in your sample)\n5. Prime member AND watched Prime Video that year\n6. Prime member AND did NOT wa'
    'tch Prime Video that year\n7. NOT a Prime member\n8. 1 touch\n9. 2\u20133 touches\n10. 4+ touches\n11. Book orders\n'
    '12. Goodreads\n13. Wattpad\n14. Other fan fiction\n15. TikTok\n16. YouTube\n17. Fan sites\n18. Search\n\nRules:\n-'
    ' Columns 5\u20137 must add up to column 3. Columns 8\u201310 must add up to column 3.\n- Columns 11\u201318: count a pers'
    'on once inside each source (a person can appear in more than one source).\n- Use the same definitions for '
    'every book and year.\n- Please return one CSV with all 54 rows (11 books \xd7 6 years). If a cell is below yo'
    "ur 100-person floor, leave it blank and note it. Don't merge rows.")
SINGLE_2022 = (
    'Please run one read for a single window, January 1 to December 31, 2022. The audience is all US people. P'
    "lease don't limit it to YouTube viewers or any other platform. Count engagement with the book only: leave"
    " out activity on any film or TV adaptation, and leave out activity about the author that doesn't name the"
    ' book.\n\nInclude these eleven books, one row each: The Love Hypothesis by Ali Hazelwood; The Map That Lead'
    's To You by J.P. Monninger; Every Summer After by Carley Fortune; The Deal by Elle Kennedy; We Were Liars'
    ' by E. Lockhart; My Lady Jane by Cynthia Hand, Brodi Ashton and Jodi Meadows; The Idea of You by Robinne '
    'Lee; Culpa M\xeda by Mercedes Ron (book 1 only); Perfect Addiction by Claudia Tan; Butcher & Blackbird by Br'
    "ynne Weaver; and The Bad Boy's Girl by Blair Holden (book 1 only).\n\nFor each book, I'd like the total num"
    'ber of US people engaged and the count in your data behind it. Split that total three ways by Prime statu'
    's: Prime members who watched Prime Video that year, Prime members who did not watch Prime Video that year'
    ', and people who are not Prime members. Those three should add up to the total. Please also split the tot'
    'al by depth (people with 1 touch, 2\u20133 touches, and 4 or more touches), which should also add up to the to'
    'tal. Finally, please show how many people engaged through each source, counting a person once inside each'
    ' source: book orders, Goodreads, Wattpad, other fan fiction, TikTok, YouTube, fan sites, and search.')
TWO_BOOKS = (
    'For The Love Hypothesis and The Map That Leads To You, book member only, calendar year 2022: US people en'
    'gaged, split into 1 touch, 2\u20133 touches and 4+ touches, with the count in the data.')
THREE_BOOKS = (
    'For each of these three books, book member only (exclude the adaptation and the author), by calendar year'
    ' from 2021 through 2026 year to date, US:\n 1. Total US people engaged with the book\n 2. Of those people, '
    'how many were, during that same year:    (a) Amazon Prime members who watched anything on Prime Video\n   '
    ' (b) Amazon Prime members who did not watch anything on Prime Video\n    (c) not Amazon Prime members\u258e Ple'
    'ase give the count in the data for every figure, use the same definitions for all three books, and send a'
    ' CSV. Books:  The Love Hypothesis (Ali Hazelwood), The Map That Leads To You (J.P. Monninger), Every Summ'
    'er After (Carley Fortune)')
SOURCE_SPLIT = (
    'Split each title-year by engagement source (book order, Goodreads, TikTok, YouTube, search), people count'
    'ed once inside each source')
RECONCILE = (
    'Today you gave me two reads on The Love Hypothesis, The Map That Leads To You and Every Summer After, bot'
    "h book member only, 2021\u20132026. The totals don't match. For example, The Love Hypothesis 2022 is 2,645,468"
    ' in the Prime-status read and 1,238,471 in the source read, and Map 2021 is 22,417 vs 31,452 (and 11,579 '
    'in your Nov 12 file). Which figures are correct, and why do they differ? The second read also says its ba'
    'se is "people who watched \u2026 on YouTube." Is that right?')

# 2026-11-17 17:35 UTC, the same ask a third way: titles with the authors in brackets, split by semicolons
BRACKETS = (
    'Compare these 11 books for US people, calendar year 2022 (Jan 1 \u2013 Dec 31, 2022): The Love Hypot'
    'hesis (Ali Hazelwood); The Map That Leads To You (J.P. Monninger); Every Summer After (Carley Fortun'
    'e); The Deal (Elle Kennedy); We Were Liars (E. Lockhart); My Lady Jane (Cynthia Hand, Brodi Ashton, '
    'Jodi Meadows); The Idea of You (Robinne Lee); Culpa M\xeda (Mercedes Ron, book 1); Perfect Addiction'
    ' (Claudia Tan); Butcher & Blackbird (Brynne Weaver); The Bad Boy\'s Girl (Blair Holden, book 1).\n\u258e'
    '\n\u258e Use the book member only (no adaptation, no author-only activity), and all US people as the'
    ' base, not YouTube viewers. For each book, give: total US people engaged and the count in the data; '
    'the split into Prime members who watched Prime Video, Prime members who didn\'t, and non-Prime membe'
    'rs; the split into 1 touch, 2\u20133 touches and 4+ touches; and people by source (book orders, Good'
    'reads, Wattpad, other fan fiction, TikTok, YouTube, fan sites, search), with a person counted once i'
    'nside each source.'
)
ELEVEN = ['The Love Hypothesis', 'The Map That Leads To You', 'Every Summer After', 'The Deal', 'We Were Liars', 'My Lady Jane',
          'The Idea of You', 'Culpa M\u00eda', 'Perfect Addiction', 'Butcher & Blackbird', "The Bad Boy's Girl"]
AUTHORS = ['Ali Hazelwood', 'J.P. Monninger', 'Carley Fortune', 'Elle Kennedy', 'E. Lockhart', 'Cynthia Hand', 'Robinne Lee',
           'Mercedes Ron', 'Claudia Tan', 'Brynne Weaver', 'Blair Holden']
ELEVEN_SUBJECT = 'The Love Hypothesis, The Map That Leads To You, Every Summer After and 8 more titles'
HAZBIN = {'primary': {'name': 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers',
                      's3_key': 'Hazbin_Hotel_Season_1_Amazon_Prime_New_Sign_Ups_Viewers_11_05_2026_19_12.csv'}}
EFRON = {'primary': {'name': 'Zac Efron', 's3_key': 'Zac_Efron_10_02_2026_14_20.csv'}}
YEAR_OFFER = 'needs one profile per year'


class TheWords(unittest.TestCase):
    def test_the_list_names_all_eleven_books(self):
        got = RB.titles(SPEC)
        self.assertEqual([t for t, _a in got], ELEVEN)
        self.assertEqual([a.split(',')[0] for _t, a in got], AUTHORS)

    def test_the_prose_names_all_eleven_books(self):
        self.assertEqual([t for t, _a in RB.titles(SINGLE_2022)], ELEVEN)

    def test_authors_in_brackets_name_all_eleven_books(self):
        # three authors in one bracket (My Lady Jane) and "book 1" inside it (Culpa M\u00eda, The Bad Boy's Girl)
        # dropped three of her eleven: the read said "and 5 more titles"
        got = RB.titles(BRACKETS)
        self.assertEqual([t for t, _a in got], ELEVEN)
        self.assertEqual(dict(got)['My Lady Jane'], 'Cynthia Hand, Brodi Ashton, Jodi Meadows')
        self.assertEqual(dict(got)['Culpa M\u00eda'], 'Mercedes Ron')
        self.assertEqual(RB.read(BRACKETS)['subject'], ELEVEN_SUBJECT)

    def test_no_file_is_the_base_of_a_brief_on_us_people(self):
        # the read was started on a Subscriber IQ file called We Were Liars, the series, because one of her eleven
        # books shares its name
        for words in (SPEC, SINGLE_2022, BRACKETS, TWO_BOOKS, THREE_BOOKS):
            self.assertTrue(RB.own_base_brief(words), words[:60])
        carried = RB.carry(SOURCE_SPLIT, [{'role': 'user', 'text': THREE_BOOKS}])
        self.assertTrue(RB.own_base_brief(carried), carried[:80])
        for words in ('How many Hazbin Hotel viewers also watch Prime Video?',
                      'We Were Liars viewers on Prime Video: what else do they watch',
                      'What share of all US adults watch We Were Liars (E. Lockhart) on Prime Video?',
                      'Compare The Idea of You (Robinne Lee) and Culpa M\u00eda (Mercedes Ron, book 1) viewers on Prime Video by age',
                      'size the fan audience for The Deal by Elle Kennedy'):
            self.assertFalse(RB.own_base_brief(words), words)

    def test_the_read_path_asks_before_it_takes_a_file(self):
        src = _legacy_view()
        resolver = src[src.index('def _pm_generation_base('):src.index("_pm_generation_base = _primary_profile.held(")]
        # the resolver itself returns no file for such a brief, before the open product, the page, the library and Subscriber IQ
        self.assertIn('_page_default.brief_on_own_base(text)', resolver)
        self.assertLess(resolver.index('brief_on_own_base(text)'), resolver.index('# 0. The open product is the base'))
        self.assertLess(resolver.index('brief_on_own_base(text)'), resolver.index('# 3. Subscriber IQ runs count as pulled bases too.'))
        # the read drops a file it reached through the brief's label and never pairs two library files under it
        self.assertIn("if base and not _pinned_base and _page_default.brief_on_own_base(text):", src)
        self.assertIn('_two = None if _page_default.brief_on_own_base(text) else _pm_two_base_detect(text, ctx)', src)
        from prometheus import page_default as PD, platform_scope as PS
        self.assertTrue(PD.brief_on_own_base(BRACKETS))
        self.assertFalse(PD.brief_on_own_base('We Were Liars viewers on Prime Video: what else do they watch'))
        # a service the brief lists as a source or a split never scopes the read

        class _Ledger:
            def __getattr__(self, name):
                raise AssertionError('the ledger is not asked for a brief on its own base')
        for words in (SPEC, BRACKETS):
            self.assertIsNone(PS.for_read(words, [], {'subject': 'YouTube', 'source': 'catalog', 's3_key': 'YouTube.csv'}, _Ledger(), '', ''))
            self.assertIsNone(PS.for_read(words, [], None, _Ledger(), 'YouTube', ''))
        # a read run again is run on the whole brief, columns and rules included
        rr = __import__('prometheus.read_recovery', fromlist=['resume_record']).resume_record(text=SPEC, history=[], base={})
        self.assertEqual(rr['text'], SPEC)

    def test_a_read_started_on_the_series_file_is_run_again_on_the_brief(self):
        # her 17:35 read was started on the We Were Liars Subscriber IQ file; a run of it after a restart is on her titles
        RR = __import__('prometheus.read_recovery', fromlist=['resume_record'])
        series = {'subject': 'We Were Liars', 's3_key': 'subiq:We_Were_Liars_04_07_2026_19_50.csv', 'source': 'subscriber_iq', 'bound': True}
        rr = RR.resume_record(text=BRACKETS, history=[], base=series)
        self.assertEqual(rr['bind_subject'], 'We Were Liars')          # what the job record holds today
        self.assertIsNone(RR.resumed_base(rr))
        self.assertEqual(RR.resumed_bind(rr), ELEVEN_SUBJECT)
        # any other read keeps the base and the subject it was started on
        rr2 = RR.resume_record(text='What else do We Were Liars viewers watch?', history=[], base=series)
        self.assertEqual(RR.resumed_base(rr2)['s3_key'], series['s3_key'])
        self.assertEqual(RR.resumed_bind(rr2), 'We Were Liars')

    def test_titles_at_the_lead_with_no_author(self):
        self.assertEqual(RB.titles(TWO_BOOKS), [('The Love Hypothesis', ''), ('The Map That Leads To You', '')])

    def test_each_is_a_brief_to_run(self):
        for q, subject in ((SPEC, ELEVEN_SUBJECT), (SINGLE_2022, ELEVEN_SUBJECT),
                           (TWO_BOOKS, 'The Love Hypothesis vs The Map That Leads To You'),
                           (THREE_BOOKS, 'The Love Hypothesis, The Map That Leads To You and Every Summer After')):
            got = RB.read(q)
            self.assertIsNotNone(got, q[:50])
            self.assertEqual(got['subject'], subject)

    def test_the_words_state_their_own_base(self):
        for q in (SPEC, SINGLE_2022):
            self.assertTrue(RB.own_population(q), q[:50])
            self.assertEqual(RB.population(q), 'US people')
        for q in (SPEC, SINGLE_2022, TWO_BOOKS, THREE_BOOKS, SOURCE_SPLIT):
            self.assertTrue(RB.stands_on_its_own(q), q[:50])
        self.assertGreaterEqual(RB.sources_listed(SOURCE_SPLIT), 3)

    def test_the_frame_carries_every_title(self):
        block = '\n'.join(RB.framing_lines(SPEC))
        for t, a in zip(ELEVEN, AUTHORS):
            self.assertIn(f"{t} ({a}", block)
        self.assertIn('No profile, twin or file on the dashboard is the base', block)

    def test_what_is_not_a_brief_stays_where_it_was(self):
        for q in ('How many people watched Gilmore Girls on Netflix by year 2019-2026?',
                  'Size the audience for Reba McEntire',
                  'How big is the fan audience for The Love Hypothesis by Ali Hazelwood?',
                  'Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31 2026. '
                  'Deliver as a CSV with the unprojected panelist count for each figure.',
                  'Give me a 4-5 year analysis of Will and Grace',
                  'For Amazon Prime Members, what share watched Prime Video in 2022?',
                  'For this audience, split by age and gender with the count in the data.',
                  'How has the Hazbin Hotel audience changed each calendar year 2021 through 2026?',
                  'What does a profile cost?', 'cancel that'):
            self.assertFalse(RB.is_brief(q), q)
            self.assertIsNone(RB.read(q), q)
            self.assertEqual(RB.carry(q, [{'role': 'user', 'text': THREE_BOOKS}]), '', q)
        for q in ('How has the Hazbin Hotel audience changed each calendar year 2021 through 2026?',
                  'Give me a 4-5 year analysis of Will and Grace', 'What is the unprojected count for this profile?',
                  'How many people in your data watched Hazbin Hotel?'):
            self.assertFalse(RB.stands_on_its_own(q), q)


class TheLanesThatTookIt(unittest.TestCase):
    def test_the_year_profile_offer_stands_aside(self):
        from prometheus.legacy import rankers as R
        for q in (SPEC, SINGLE_2022, TWO_BOOKS, THREE_BOOKS, SOURCE_SPLIT):
            self.assertIsNone(R._pm_detect_multi_year_ask(q), q[:50])
        # her own base and years with a list the detector could not read is still not a study of the page
        self.assertIsNone(R._pm_detect_multi_year_ask(
            'Audience (base): all US people. Do NOT limit to YouTube viewers. What counts: engagement with the book only. '
            'Time: each calendar year separately: 2021, 2022, 2023, 2024, 2025, and 2026 year to date. Total US people engaged.'))
        # the study it was built for is still detected
        self.assertEqual(R._pm_detect_multi_year_ask('How has the Hazbin Hotel audience changed 2021-2026?'),
                         [2021, 2022, 2023, 2024, 2025, 2026])
        self.assertTrue(R._pm_detect_multi_year_ask('Give me a 4-5 year analysis of Will and Grace'))

    def test_the_unprojected_lane_stands_aside(self):
        from prometheus import unprojected as U
        for q in (TWO_BOOKS, SPEC[:590]):
            self.assertFalse(U.wants_unprojected(q), q[:50])
        # the ask it was built for is still its own
        self.assertTrue(U.wants_unprojected('What is the unprojected count behind the 6,195,357?'))

    def test_the_universe_is_us_people_whatever_is_open(self):
        from prometheus import question_universe as QU
        for page in (HAZBIN['primary']['name'], EFRON['primary']['name'], ''):
            for q in (SPEC, SINGLE_2022, TWO_BOOKS, THREE_BOOKS):
                u = QU.question_universe(q, page)
                self.assertEqual((u.kind, u.base), ('named', 'US people'), (page, q[:40]))
        self.assertEqual(QU.question_universe('What share of this audience is women 18-34?', HAZBIN['primary']['name']).kind, 'page')

    def test_a_service_in_a_list_of_sources_is_never_the_base(self):
        from prometheus import page_default as PD
        for subject in ('The Love Hypothesis (book member only)', 'The Love Hypothesis', 'Every Summer After'):
            for q in (SOURCE_SPLIT, SPEC, SINGLE_2022, THREE_BOOKS):
                self.assertEqual(PD.title_universe_label(q, subject, 'MOVIE'), '', (subject, q[:40]))
        # a viewing question still names its viewers
        self.assertEqual(PD.title_universe_label('How many people watched The Love Hypothesis on Prime Video?', 'The Love Hypothesis', 'MOVIE'),
                         'people who watched The Love Hypothesis on Prime Video in the window')

    def test_the_next_question_is_on_the_briefs_titles(self):
        hist = [{'role': 'user', 'text': THREE_BOOKS}, {'role': 'agent', 'text': 'On US people: ...'}]
        got = RB.carry(SOURCE_SPLIT, hist)
        self.assertTrue(got.endswith(SOURCE_SPLIT))
        for t in ('The Love Hypothesis by Ali Hazelwood', 'The Map That Leads To You by J.P. Monninger', 'Every Summer After by Carley Fortune',
                  'all US people'):
            self.assertIn(t, got)
        self.assertTrue(RB.is_brief(got))
        self.assertEqual(RB.population(got), 'US people')
        # no brief in this thread, a brief carried from another thread, a question about figures: nothing is carried
        self.assertEqual(RB.carry(SOURCE_SPLIT, []), '')
        self.assertEqual(RB.carry(SOURCE_SPLIT, [{'role': 'user', 'text': THREE_BOOKS, 'carried': True}]), '')
        self.assertEqual(RB.carry(RECONCILE, hist), '')

    def test_the_reconcile_answer_keeps_the_briefs_base(self):
        from prometheus import reconcile_lane as RL
        us = {'universe': 'US people engaged with The Love Hypothesis (book member only)'}
        yt = {'universe': 'people who watched The Love Hypothesis (book member only) on YouTube in the window'}
        self.assertFalse(RL._service_base(us))
        self.assertTrue(RL._service_base(yt))
        self.assertTrue(RL._prefers_whole_base(RECONCILE, []))                                   # she disputes the base
        self.assertTrue(RL._prefers_whole_base('Which figures are correct?', [{'role': 'user', 'text': THREE_BOOKS}]))
        self.assertFalse(RL._prefers_whole_base('Which figures are correct?', []))
        canon = {'universe': 'Every Summer After (book member only)', 'window_days': 365, 'count': {'value': 10}}
        why = RL._why(canon, {'after', 'book'}, [canon])
        self.assertNotIn('After book', why)


class ThroughTheDoor(unittest.TestCase):
    """service.ask with a stub host, the profile she had open on the screen."""

    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls, cls.mails, cls.hints = [], [], []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text, dict(body)))
            return {'success': True, 'action': 'answer', 'reply': 'On it: reading the titles now.', 'followups': [], 'read_job_id': 'job1'}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text, dict(body)))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: cls.hints.append(k), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: cls.mails.append(a),
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
                  load_users=lambda: {'users': {'canary:brief17': {'username': 'canary:brief17', 'unlimited': True, 'company': 'Amazon'}}},
                  check_user_credits=lambda u: (True, 100))
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'canary:brief17', 'unlimited': True, 'company': 'Amazon'}

    def _ask(self, text, surface='analyze', history=None, page=HAZBIN, **extra):
        self.calls.clear(), self.mails.clear(), self.hints.clear()
        body = {'text': text, 'surface': surface, 'client': 'probe', 'history': list(history or [])}
        if page:
            body['page_context'] = page
        body.update(extra)
        return self.service.ask(self.user, body)

    def _routes(self):
        return [h.get('route') for h in self.hints if h.get('route')]

    def _one_read(self, q, subject, env, st):
        self.assertEqual(st, 200)
        self.assertEqual(len(self.calls), 1, self.calls)                 # one core call: never split, never two reads
        kind, text, body = self.calls[0]
        self.assertEqual(kind, 'analyze', q[:50])                        # a read, never the build flow
        self.assertEqual(text, q)                                        # her words, whole
        self.assertEqual(body.get('bind_subject'), subject)              # bound to the books, never the page
        self.assertNotIn('Hazbin', str(body.get('bind_subject')))
        self.assertNotIn(YEAR_OFFER, env['text'])
        self.assertNotIn('$300', env['text'])
        self.assertEqual(self.mails, [])
        self.assertIn('read_brief', self._routes())
        for gone in ('multi_ask', 'priced_read', 'cross', 'sizing', 'unprojected', 'build_order', 'segment_sizing'):
            self.assertNotIn(gone, self._routes(), q[:50])

    def test_the_numbered_brief_is_one_read_with_hazbin_hotel_open(self):
        env, st = self._ask(SPEC)
        self._one_read(SPEC, ELEVEN_SUBJECT, env, st)

    def test_the_numbered_brief_is_one_read_with_nothing_open(self):
        env, st = self._ask(SPEC, page=None)
        self._one_read(SPEC, ELEVEN_SUBJECT, env, st)

    def test_the_numbered_brief_on_the_build_surface_is_still_a_read(self):
        env, st = self._ask(SPEC, surface='interpret')
        self._one_read(SPEC, ELEVEN_SUBJECT, env, st)

    def test_the_single_window_prose_is_a_read_never_a_profile_to_build(self):
        for surface in ('analyze', 'interpret'):
            for page in (HAZBIN, None):
                env, st = self._ask(SINGLE_2022, surface=surface, page=page)
                self._one_read(SINGLE_2022, ELEVEN_SUBJECT, env, st)

    def test_two_titles_with_the_count_in_the_data_is_a_read_not_the_size_of_the_open_profile(self):
        env, st = self._ask(TWO_BOOKS, page=EFRON)
        self._one_read(TWO_BOOKS, 'The Love Hypothesis vs The Map That Leads To You', env, st)
        self.assertNotIn('Unprojected, the Zac Efron audience', env['text'])

    def test_the_split_by_source_is_read_on_the_briefs_titles(self):
        hist = [{'role': 'user', 'text': THREE_BOOKS}, {'role': 'agent', 'text': 'On US people: The Love Hypothesis 2022: 2,645,468.'}]
        env, st = self._ask(SOURCE_SPLIT, history=hist)
        self.assertEqual(st, 200)
        self.assertEqual([c[0] for c in self.calls], ['analyze'])
        _kind, text, body = self.calls[0]
        self.assertEqual(body.get('bind_subject'), 'The Love Hypothesis, The Map That Leads To You and Every Summer After')
        self.assertTrue(text.endswith(SOURCE_SPLIT))
        self.assertIn('all US people', text)
        self.assertIn('read_brief', self._routes())


if __name__ == '__main__':
    unittest.main(verbosity=1)
