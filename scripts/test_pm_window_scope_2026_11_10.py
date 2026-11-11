#!/usr/bin/env python3
"""The window and the platform a build or a read runs on are the ones
the reader asked for (2026-11-10). Hermetic: no model, no S3.

Four slips on one day, on the readers' exact words:

  1. Armine (Lionsgate): "Cruel Summer series was in 2021 and it only
     has 2 seasons for now", then "can we build a profile for cruel
     summer series". The build ran on Jan 1 to Dec 31, 2021. A year that
     describes the title, or sits in a reply to a seasons clarifier, is
     never the window; only words that ask for one are.
  2. Armine: "Can we build a profile for Las Culturistas Awards that
     aired on Peacock", then "Can we build the profiles only for the
     viewers on peacock". Jessie: "viewers ONLY on Amazon and HBO MAX".
     Grace: "US people who watched Fallout Season 2 on Prime Video". The
     platform the client names scopes the universe, strictly.
  3. Armine: "1/1/2025 -12/30/2025 vs 1/1/2026 - to most recent date
     there is data" was read through Sep 14. An open end is yesterday.
  4. Liz's first read printed Jul 1 2025 to Jun 30 2026. The retired
     pair is never produced; the default is the trailing 12 months.
"""
from __future__ import annotations

import ast
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'AKIATESTNOTREAL000000')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'not-a-real-secret')
os.environ.setdefault('AWS_SHARED_CREDENTIALS_FILE', '/nonexistent/aws/credentials')
os.environ.setdefault('AWS_CONFIG_FILE', '/nonexistent/aws/config')

from prometheus import inline_dates as D  # noqa: E402
from prometheus import named_platforms as N  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import window_scope as W  # noqa: E402

TODAY = datetime.date(2026, 11, 11)
YDAY = '2026-11-10'

ARMINE_TOLD = "Cruel Summer series was in 2021 and it only has 2 seasons for now"
ARMINE_ORDER = "can we build a profile for cruel summer series"
ARMINE_BRIEF = "it was in 2021, only 2 seasons"
Q_SEASONS = "Cruel Summer has 2 seasons. Cover all seasons, the most recent season, or a specific season?"
Q_DATES = ("Before I queue Cruel Summer Viewers: which exact dates should this cover? "
           "The ask is scoped to the premiere, so the window should be those days rather than the standard year.")
A_READ = ("Correct: the series ran two seasons, 2021 and 2023, so Cruel Summer Viewers is a two-season pool of "
          "5,842,173 people. Window: Nov 10 2025 to Nov 10 2026. What stands out is season 1.")

LAS_1 = "Can we build a profile for Las Culturistas Awards that aired on Peacock"
LAS_2 = "Can we build the profiles only for the viewers on peacock"
JESSIE = ("run a profile on Viewers of GEORGIE & MANDY'S FIRST MARRIAGE, viewers ONLY on Amazon and HBO MAX. "
          "Date range August 7 2026 through November 10 2026")
GRACE = ("Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31 2026. "
         "Include reach, age and gender split, top other titles and genres they watch, and their streaming platform mix.")

MAD_MEN = ("For Mad Men series viewers, can we compare: 1/1/2025 -12/30/2025 vs 1/1/2026 - to most recent date there is data. "
           "And these are some of the areas I'd like to compare:\nAge / generational breakdown\nGender")


def _win(text, question=None):
    return D.parse_inline_window(text, today=TODAY, question=question)


class DescribedYearTests(unittest.TestCase):
    """1. A year that describes the title is not a window."""

    def test_her_words_state_no_window(self):
        for t in (ARMINE_TOLD, ARMINE_BRIEF, "came out in 2021", "it came out in 2021", "aired in 2021",
                  "Cruel Summer viewers, the show aired in 2021", "the series premiered back in 2021",
                  "it was in June 2021", "the show is from 2021"):
            self.assertIsNone(_win(t), t)
        self.assertEqual(D.described_years(ARMINE_TOLD), {2021})

    def test_words_that_ask_for_a_window_still_do(self):
        want = {"Cruel Summer viewers for 2021": ('2021-01-01', '2021-12-31'),
                "Cruel Summer viewers in calendar 2021": ('2021-01-01', '2021-12-31'),
                "Cruel Summer viewers in 2021": ('2021-01-01', '2021-12-31'),
                "Cruel Summer viewers from Jan 5 2021 to Mar 9 2021": ('2021-01-05', '2021-03-09')}
        for t, pair in want.items():
            w = _win(t)
            self.assertIsNotNone(w, t)
            self.assertEqual((w['start'], w['end']), pair, t)
        # the same text can describe and ask: the asked year is not "only described"
        self.assertEqual(D.described_years("it aired in 2021; build it for 2021"), set())

    def test_a_reply_is_read_against_the_question_it_answers(self):
        self.assertEqual(D.question_kind(Q_SEASONS), 'subject')
        self.assertEqual(D.question_kind(Q_DATES), 'date')
        self.assertEqual(D.question_kind(A_READ), '')        # a read asks nothing, whatever words it uses
        # to the seasons question: a year in the reply is the show's
        for t in (ARMINE_BRIEF, "in 2021", "2021", "since 2021", "season 2, it ran 2021 to 2022"):
            self.assertIsNone(_win(t, Q_SEASONS), t)
        # ... unless the reply asks for a window in so many words
        self.assertEqual(_win("all seasons, for 2021", Q_SEASONS)['start'], '2021-01-01')
        self.assertEqual(_win("Jan 19 to Feb 15 2024", Q_SEASONS)['end'], '2024-02-15')
        # to the date question: a short reply's bare year IS the window
        w = _win("2021", Q_DATES)
        self.assertEqual((w['start'], w['end']), ('2021-01-01', '2021-12-31'))
        self.assertIsNone(_win("it was in 2021", Q_DATES))
        # after a read (no question), an ordinary ask reads as before
        self.assertEqual(_win("Cruel Summer viewers in 2021", A_READ)['start'], '2021-01-01')
        self.assertIsNone(_win("Dune 2024 viewers", A_READ))

    def test_the_page_build_order_does_not_take_her_year(self):
        self.assertIsNone(SR.window_in_text(ARMINE_TOLD))
        order = SR.open_profile_build_order("can you make this profile?", "Cruel Summer Viewers",
                                            [{'role': 'user', 'text': ARMINE_TOLD}])
        self.assertEqual((order['start'], order['end']), ('', ''))
        self.assertEqual(SR.window_in_text("run this for 2025"), ('2025-01-01', '2025-12-31'))
        self.assertEqual(SR.window_in_text("do one june 2024- june 2025"), ('2024-06-01', '2025-06-01'))


class BinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A
        cls.ds, cls.de = A._standing_default_dates()

    def _draft(self, **kw):
        d = {'subject': 'Cruel Summer Viewers', 'decision': 'new_build',
             'date_range': {'start': self.ds, 'end': self.de}, 'date_range_explicit': False}
        d.update(kw)
        return d

    def test_armines_exchange_builds_on_the_default(self):
        hist = [{'role': 'user', 'text': ARMINE_TOLD}, {'role': 'agent', 'text': A_READ}]
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, ARMINE_ORDER, chat_history=hist, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})
        self.assertFalse(d.get('date_range_explicit'))
        self.assertEqual(d['engine_date_range'], f"{self.ds} TO {self.de}")

    def test_a_year_the_model_took_from_her_words_is_dropped(self):
        hist = [{'role': 'user', 'text': ARMINE_TOLD}, {'role': 'agent', 'text': A_READ}]
        d = self._draft(date_range={'start': '2021-01-01', 'end': '2021-12-31'}, date_range_explicit=True,
                        date_window_label='Jan 1 to Dec 31, 2021', engine_date_range='2021-01-01 TO 2021-12-31')
        self.assertEqual(self.A._bind_explicit_windows(d, ARMINE_ORDER, chat_history=hist, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})
        self.assertFalse(d.get('date_range_explicit'))
        self.assertNotIn('date_window_label', d)
        self.assertEqual(d['engine_date_range'], f"{self.ds} TO {self.de}")
        # the clarifier shape: the year arrives in the reply itself
        hist2 = [{'role': 'user', 'text': ARMINE_ORDER}, {'role': 'agent', 'text': Q_SEASONS}]
        d = self._draft(date_range={'start': '2021-01-01', 'end': '2021-12-31'}, date_range_explicit=True)
        self.assertEqual(self.A._bind_explicit_windows(d, ARMINE_BRIEF, chat_history=hist2, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})

    def test_a_real_window_is_kept(self):
        # she asks for the year as a window: it binds, and the model's copy of it stays
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, "can we build a profile for cruel summer viewers in 2021",
                                                       decision='new_build'), 'inline')
        self.assertEqual(d['date_range'], {'start': '2021-01-01', 'end': '2021-12-31'})
        # a real date answer to the date question binds
        hist = [{'role': 'user', 'text': ARMINE_ORDER}, {'role': 'agent', 'text': Q_DATES}]
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, "2021", chat_history=hist, decision='new_build'), 'inline')
        self.assertEqual(d['date_range'], {'start': '2021-01-01', 'end': '2021-12-31'})
        self.assertTrue(d['date_range_explicit'])
        # a window the model resolved that her words never described is left alone
        d = self._draft(date_range={'start': '2025-02-09', 'end': '2025-02-09'}, date_range_explicit=True)
        self.A._bind_explicit_windows(d, "Super Bowl 2025 viewers", decision='new_build')
        self.assertEqual(d['date_range'], {'start': '2025-02-09', 'end': '2025-02-09'})

    def test_open_ended_build_window_ends_yesterday(self):
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, "Mad Men viewers from Jan 1 2026 to date", decision='new_build'), 'inline')
        yday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()
        self.assertEqual(d['date_range']['end'], yday)

    def test_default_is_trailing_twelve_and_never_the_retired_pair(self):
        from migration.event_window import default_window
        self.assertEqual((self.ds, self.de), tuple(default_window()))
        self.assertNotEqual((self.ds, self.de), ('2025-07-01', '2026-06-30'))
        # the recognizer pair is never what the default helper hands back, even when the window module is missing
        saved = self.A._ew_default_window
        try:
            self.A._ew_default_window = lambda today=None: (self.A._LEGACY_FISCAL_START, self.A._LEGACY_FISCAL_END)
            self.assertEqual(self.A._standing_default_dates(), (self.ds, self.de))
        finally:
            self.A._ew_default_window = saved
        lbl = self.A._default_window_label()
        self.assertNotIn('Jun 30, 2026', lbl)
        self.assertFalse(hasattr(self.A, '_DEFAULT_WINDOW_LABEL'))
        # a leftover draft that still carries the retired pair is rewritten, never shipped
        d = self._draft(date_range={'start': '2025-07-01', 'end': '2026-06-30'})
        self.A._bind_explicit_windows(d, ARMINE_ORDER, decision='new_build')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})
        self.assertEqual(self.A._draft_window_field({'decision': 'new_build'}, 'new_build'), lbl)

    def test_platform_binder_reaches_the_one_shot_surface(self):
        d = {'decision': 'new_build', 'subject': 'Fallout Season 2 Viewers', 'brand_category': 'SERIES - DRAMA',
             'is_ip_content': True}
        self.assertEqual(self.A._bind_named_platforms(d, GRACE), ['Amazon Prime Video'])
        self.assertEqual(d['subject'], 'Fallout Season 2 Viewers on Amazon Prime Video')


class PlatformScopeTests(unittest.TestCase):
    """2. A platform the client names is the universe's platform."""

    def _las(self, **kw):
        d = {'decision': 'new_build', 'subject': 'Las Culturistas Culture Awards Viewers',
             'name': 'Las Culturistas Culture Awards Viewers', 'brand_category': 'SERIES - REALITY',
             'is_ip_content': True, 'named_platforms': [],
             'home_platform_rows': [['STREAMING/PLATFORM', 'Peacock'], ['BROADCAST/CABLE', 'Bravo']]}
        d.update(kw)
        return d

    def test_armines_two_turns(self):
        hist = [{'role': 'user', 'text': LAS_1}, {'role': 'agent', 'text': 'Brief drafted: Las Culturistas Awards (new build)'}]
        d = self._las()
        self.assertEqual(N.bind(d, LAS_2, hist), ['Peacock'])
        self.assertEqual(d['subject'], 'Las Culturistas Culture Awards Viewers on Peacock')
        self.assertEqual(d['name'], d['subject'])
        self.assertEqual(d['named_platforms'], ['Peacock'])
        self.assertEqual(d['seed_source'], 'content_map')
        self.assertEqual(d['content_show'], 'Las Culturistas Culture Awards')
        self.assertEqual(d['ip_scope'], 'consumers')
        self.assertEqual(d['home_platform_rows'], [['STREAMING/PLATFORM', 'Peacock']])     # the home she did not name is not the scope
        self.assertIn('viewers on Peacock only', d['resolved_identity'])
        self.assertEqual(N.universe_phrase(d, 'total viewers universe'), 'total viewers universe on Peacock')
        # idempotent
        self.assertEqual(N.bind(d, LAS_2, hist), ['Peacock'])
        self.assertEqual(d['subject'], 'Las Culturistas Culture Awards Viewers on Peacock')

    def test_first_ask_describes_then_viewers_only_takes_it(self):
        self.assertEqual(N.find(LAS_1)['how'], 'described')
        d = {'decision': 'new_build', 'subject': 'Las Culturistas Awards', 'brand_category': 'SERIES - REALITY', 'is_ip_content': True}
        self.assertEqual(N.bind(d, LAS_1, []), [])                 # a broad title build is not narrowed by a description
        self.assertEqual(d['described_platforms'], ['Peacock'])
        self.assertEqual(d['subject'], 'Las Culturistas Awards')
        d['ip_scope'] = 'consumers'
        d['subject'] = d['name'] = 'Las Culturistas Awards Viewers'  # the reader answered "viewers only"
        self.assertEqual(N.promote(d), ['Peacock'])
        self.assertEqual(d['subject'], 'Las Culturistas Awards Viewers on Peacock')
        # a viewers universe that is described on a platform is scoped at once
        d2 = {'decision': 'new_build', 'subject': 'The Paper Viewers', 'brand_category': 'SERIES - COMEDY', 'is_ip_content': True}
        self.assertEqual(N.bind(d2, "a profile of The Paper viewers, the show that airs on Peacock", []), ['Peacock'])

    def test_the_spec_is_strict_on_the_named_platform(self):
        from prometheus.streamscout_spec import stamp_client_scope
        scrub = lambda v, field='', subject='', max_len=0, single_line=False: str(v).strip()[:max_len or None]  # noqa: E731
        d = self._las()
        N.bind(d, LAS_2, [{'role': 'user', 'text': LAS_1}])
        spec = {'seed_source': d['seed_source'], 'content_show': d['content_show']}
        stamp_client_scope(spec, d, scrub, d['subject'])
        N.stamp_spec(spec, d, scrub, d['subject'])
        self.assertEqual(spec['content_seed_platforms_named'], ['Peacock'])
        self.assertTrue(spec['content_seed_platforms_strict'])
        # an entity build never carries a platform scope
        ent = {'name': 'Reba McEntire'}
        N.stamp_spec(ent, {'named_platforms': ['Hulu'], 'platform_scope_strict': True}, scrub, 'Reba McEntire')
        self.assertEqual(ent, {'name': 'Reba McEntire'})

    def test_jessie_two_platforms_both_strict(self):
        d = {'decision': 'new_build', 'subject': "Georgie & Mandy's First Marriage Viewers", 'brand_category': 'SERIES - COMEDY',
             'is_ip_content': True, 'named_platforms': ['Amazon Prime Video']}      # the model kept one
        self.assertEqual(N.bind(d, JESSIE, []), ['Amazon Prime Video', 'HBO Max'])
        self.assertEqual(d['subject'], "Georgie & Mandy's First Marriage Viewers on Amazon Prime Video and HBO Max")
        self.assertEqual([r[1] for r in d['home_platform_rows']], ['Amazon Prime Video', 'HBO Max'])
        self.assertNotIn('platform', {k: v for k, v in d.items() if k == 'platform' and v})   # no single home for two

    def test_grace_platform_in_the_first_ask(self):
        d = {'decision': 'new_build', 'subject': 'Fallout Season 2 Viewers', 'brand_category': 'SERIES - DRAMA', 'is_ip_content': True}
        self.assertEqual(N.bind(d, GRACE, []), ['Amazon Prime Video'])
        self.assertEqual(d['subject'], 'Fallout Season 2 Viewers on Amazon Prime Video')
        self.assertEqual(d['platform'], 'Amazon Prime Video')

    def test_what_is_not_a_scope(self):
        for t in ("Taylor Swift fans", "viewers of The Bear, not on Hulu",
                  "Men 18-34 who pay for Netflix or Max but watched no Prime Video in the last 90 days",
                  "customers who watched Wuthering Heights on any streaming service", "keep the max volume viewers"):
            self.assertIsNone(N.find(t), t)
        # the store, not the service
        d = {'decision': 'new_build', 'subject': 'Amazon Shoppers', 'brand_category': 'RETAILERS'}
        self.assertEqual(N.bind(d, "people who shop on Amazon", []), [])
        self.assertEqual(d['subject'], 'Amazon Shoppers')
        # a brand build whose words carry a watch verb, and a persona whose name carries a noun, are not title audiences
        d = {'decision': 'new_build', 'subject': 'Nike Shoppers', 'brand_category': 'APPAREL/FOOTWEAR', 'is_ip_content': False}
        self.assertEqual(N.bind(d, "Nike shoppers who watched the Super Bowl on Peacock", []), [])
        d = {'decision': 'new_build', 'subject': 'Heavy Podcast Listeners', 'brand_category': 'SHOPPING INTENT'}
        self.assertEqual(N.bind(d, "heavy podcast listeners on Spotify", []), [])
        self.assertEqual(d['subject'], 'Heavy Podcast Listeners')
        self.assertNotIn('seed_source', d)
        # the multi-service cut shape belongs to platform_cuts
        d = {'decision': 'new_build', 'subject': 'Wuthering Heights Viewers', 'is_ip_content': True}
        self.assertEqual(N.bind(d, "people who watched it on HBO Max", [], cuts_hit={'platforms': ['HBO Max']}), [])
        # a reuse or a cut keeps its parent's universe
        d = {'decision': 'existing_match', 'subject': 'Gilmore Girls Viewers', 'is_ip_content': True}
        self.assertEqual(N.bind(d, "Gilmore Girls viewers on Hulu", []), [])

    def test_a_platform_named_for_an_earlier_build_does_not_ride(self):
        hist = [{'role': 'user', 'text': 'Gilmore Girls viewers on Hulu'},
                {'role': 'agent', 'text': 'Included with your plan. Profile building: Gilmore Girls Viewers on Hulu. I will email you when it is complete.'}]
        d = {'decision': 'new_build', 'subject': 'Mad Men Viewers', 'brand_category': 'SERIES - DRAMA', 'is_ip_content': True}
        self.assertEqual(N.bind(d, "now build a profile for Mad Men viewers", hist), [])
        self.assertEqual(d['subject'], 'Mad Men Viewers')
        # before approval it does: the seasons answer keeps the platform
        hist = [{'role': 'user', 'text': 'Gilmore Girls viewers on Hulu'}, {'role': 'agent', 'text': Q_SEASONS}]
        d = {'decision': 'new_build', 'subject': 'Gilmore Girls Viewers', 'brand_category': 'SERIES - DRAMA', 'is_ip_content': True}
        self.assertEqual(N.bind(d, "all seasons", hist), ['Hulu'])
        self.assertEqual(d['subject'], 'Gilmore Girls Viewers on Hulu')

    def test_names(self):
        self.assertEqual(N.scoped_name('Gilmore Girls Viewers ON HULU', ['Hulu']), 'Gilmore Girls Viewers ON HULU')
        self.assertEqual(N.scoped_name('The Daily', ['Spotify'], 'listeners'), 'The Daily Listeners on Spotify')
        from migration.viewer_content_scope import scoped_subject_name, audience_subject_name
        self.assertEqual(scoped_subject_name('Yellowstone Viewers on Peacock', {'kind': 'series'}, {'mode': 'specific', 'season': 5}),
                         'Yellowstone Season 5 Viewers on Peacock')
        self.assertEqual(scoped_subject_name('Yellowstone Viewers', {'kind': 'series'}, {'mode': 'specific', 'season': 5}),
                         'Yellowstone Season 5 Viewers')
        self.assertEqual(audience_subject_name('Paw Patrol Viewers on Paramount+', 'parents'), 'Parents of Paw Patrol Viewers on Paramount+')

    def test_the_special_not_the_podcast(self):
        self.assertEqual(N.disambiguation("the special, not the podcast"), {'kind': 'special', 'exclude': ['podcast']})
        self.assertEqual(N.disambiguation("not the podcast, the awards special"), {'kind': 'special', 'exclude': ['podcast']})
        d = self._las(content_show='Las Culturistas Podcast')
        N.bind(d, "the special, not the podcast, only viewers on Peacock", [{'role': 'user', 'text': LAS_1}])
        self.assertEqual(d['content_kind'], 'special')
        self.assertEqual(d['content_exclude'], ['podcast'])
        self.assertEqual(d['content_show'], 'Las Culturistas Culture Awards')      # the special is one title, the podcast another
        spec = {'seed_source': 'content_map'}
        N.stamp_spec(spec, d, None, d['subject'])
        self.assertEqual(spec['medium'], 'special')
        self.assertEqual(spec['content_exclude'], ['podcast'])


class OpenEndTests(unittest.TestCase):
    """3. 'To date', 'so far' and open-ended ranges end yesterday."""

    def test_reader_ends_on_yesterday(self):
        for t, start in (("sign-ups since March 2026", '2026-03-01'), ("Mad Men viewers from Jan 1 2026 to date", '2026-01-01'),
                         ("Mad Men viewers so far this year", '2026-01-01'), ("Mad Men viewers year to date", '2026-01-01'),
                         ("1/1/2026 - to most recent date there is data", '2026-01-01'), ("viewers 2026 so far", '2026-01-01')):
            w = _win(t)
            self.assertIsNotNone(w, t)
            self.assertEqual((w['start'], w['end']), (start, YDAY), t)
        self.assertIsNone(_win("keep me up to date on Mad Men"))

    def test_compare_2025_vs_2026_to_date(self):
        wins = W.read_windows("compare 2025 vs 2026 to date", TODAY)
        self.assertEqual([(w['start'], w['end']) for w in wins], [('2025-01-01', '2025-12-31'), ('2026-01-01', YDAY)])
        self.assertTrue(wins[-1]['open'])

    def test_armines_mad_men_compare(self):
        wins = W.read_windows(MAD_MEN, TODAY)
        self.assertEqual([(w['start'], w['end']) for w in wins], [('2025-01-01', '2025-12-30'), ('2026-01-01', YDAY)])
        note = W.read_window_note(MAD_MEN, TODAY)
        self.assertIn('2026-01-01 to 2026-11-10', note)
        self.assertIn('Nov 10 2026', note)
        self.assertNotIn('Sep 14', note)
        # no open end, no note: a closed window is read as before
        self.assertEqual(W.read_window_note("Mad Men viewers in 2025", TODAY), '')
        self.assertIn('2021', W.read_window_note(ARMINE_TOLD, TODAY))
        self.assertEqual(W.read_windows("Mad Men viewers, Jan 1 2025 to Dec 30 2025", TODAY), [])

    def test_the_read_prompt_carries_the_window_and_the_rule(self):
        import prometheus_analysis as pma
        up = pma.build_reasoned_metrics_user_prompt(MAD_MEN, [])
        self.assertIn('STATED WINDOW', up)
        self.assertLess(up.index('STATED WINDOW'), up.index("USER'S QUESTION"))
        self.assertNotIn('STATED WINDOW', pma.build_reasoned_metrics_user_prompt("How old are Mad Men viewers?", []))
        # the rule rides the question (the system prompt's size is held by its own ratchet): a year that only
        # describes the title is said once, ahead of the question
        up = pma.build_reasoned_metrics_user_prompt(ARMINE_TOLD, [])
        self.assertIn('only to describe the subject', up)
        self.assertIn('2021', up[up.index('STATED WINDOW'):up.index("USER'S QUESTION")])
        self.assertNotIn('STATED WINDOW', pma.build_reasoned_metrics_user_prompt("Cruel Summer viewers in 2021 by age", []))
        for sp in (pma.REASONED_METRICS_SYSTEM_PROMPT, pma.SEARCH_DEMAND_SYSTEM_PROMPT, pma.INSIGHTS_DECK_SYSTEM_PROMPT):
            self.assertNotIn('__YESTERDAY__', sp)
            self.assertNotIn('__T12_', sp)

    def test_a_finished_read_is_held_to_yesterday(self):
        res = {'subject': 'Mad Men', 'window_label': 'Jan 1 2025 to Dec 30 2025 vs Jan 1 2026 to Sep 14 2026',
               'window_start': '2025-01-01', 'window_end': '2026-09-14',
               'headline': '7,582,850 US viewers in 2025 and 6,418,273 so far in 2026 through Sep 14.',
               'reads': ['The 2026 side runs Jan 1 2026 to Sep 14, 2026.'],
               'metrics': [{'name': 'v26', 'label': 'US viewers, Jan 1 2026 to Sep 14 2026', 'unit': 'count', 'value': 6418273}]}
        out = W.hold_read_window(res, MAD_MEN, TODAY)
        self.assertEqual((out['window_start'], out['window_end']), ('2025-01-01', YDAY))
        self.assertEqual(out['window_label'], 'Jan 1 2025 to Dec 30 2025 vs Jan 1 2026 to Nov 10 2026')
        self.assertIn('through Nov 10', out['headline'])
        self.assertEqual(out['reads'][0], 'The 2026 side runs Jan 1 2026 to Nov 10, 2026.')
        self.assertEqual(out['metrics'][0]['label'], 'US viewers, Jan 1 2026 to Nov 10 2026')
        for blob in (out['window_label'], out['headline'], out['reads'][0], out['metrics'][0]['label']):
            self.assertNotIn('Sep 14', blob)
        # no open end in the ask: only a window that ends after today moves
        fut = W.hold_read_window({'window_start': '2026-01-01', 'window_end': '2026-12-31', 'window_label': 'Jan 1 2026 to Dec 31 2026'},
                                 "Mad Men viewers", TODAY)
        self.assertEqual(fut['window_end'], YDAY)
        self.assertEqual(fut['window_label'], 'Jan 1 2026 to Nov 10 2026')
        past = W.hold_read_window({'window_start': '2025-01-01', 'window_end': '2025-12-31', 'window_label': 'Jan 1 2025 to Dec 31 2025'},
                                  "Mad Men viewers in 2025", TODAY)
        self.assertEqual(past['window_end'], '2025-12-31')

    def test_the_coherence_pass_holds_it(self):
        import prometheus_analysis as pma
        yday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()
        res = {'subject': 'Mad Men', 'window_label': 'Jan 1 2026 to Sep 14 2026', 'window_start': '2026-01-01',
               'window_end': '2026-09-14', 'headline': 'x', 'reads': ['y'],
               'metrics': [{'name': 'v26', 'label': 'US viewers', 'unit': 'count', 'value': 6418273}]}
        out = pma.enforce_metrics_coherence(res, question="Mad Men viewers from 1/1/2026 to date")
        self.assertEqual(out['window_end'], yday)


class RetiredPairTests(unittest.TestCase):
    """4. No module produces the retired Jul 1 2025 to Jun 30 2026 pair."""

    RETIRED = ('2025-07-01', '2026-06-30')
    RETIRED_WORDS = ('Jul 1, 2025 to Jun 30, 2026', 'Jul 1 2025 to Jun 30 2026', '07-01-2025', '06-30-2026',
                     '07/01/2025', '06/30/2026', 'July 1, 2025', 'June 30, 2026')
    # the one place the pair may be spelled: the recognizer, so a leftover
    # draft is rewritten to the standing default
    RECOGNIZER = {os.path.join('migration', 'event_window.py'): {'LEGACY_DEFAULT_START', 'LEGACY_DEFAULT_END'},
                  'app.py': {'_LEGACY_FISCAL_START', '_LEGACY_FISCAL_END'}}

    def _sources(self):
        out = []
        for base in ('', 'prometheus', os.path.join('prometheus', 'legacy'), 'migration'):
            d = os.path.join(ROOT, base)
            for fn in sorted(os.listdir(d)):
                if not fn.endswith('.py'):
                    continue
                rel = os.path.join(base, fn)
                if base == '' and not (fn in ('app.py', 'bg.py') or fn.startswith('prometheus')):
                    continue
                out.append(rel)
        return out

    def _literal_hits(self, rel):
        """String constants in real code (docstrings excluded) that carry the pair."""
        with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
            src = fh.read()
        if not any(tok in src for tok in self.RETIRED + self.RETIRED_WORDS):
            return []
        tree = ast.parse(src)
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                docs.add(id(node.body[0].value))
        allowed_targets = self.RECOGNIZER.get(rel, set())
        allowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and any(isinstance(t, ast.Name) and t.id in allowed_targets for t in node.targets):
                allowed.add(id(node.value))
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and id(node) not in allowed:
                v = node.value
                if any(tok in v for tok in self.RETIRED) or any(tok in v for tok in self.RETIRED_WORDS[:6]):
                    hits.append((rel, node.lineno, v[:80]))
        return hits

    def test_no_module_emits_the_retired_pair(self):
        hits = []
        for rel in self._sources():
            hits += self._literal_hits(rel)
        self.assertEqual(hits, [], f"the retired pair is still produced: {hits}")

    def test_default_window_is_the_only_default(self):
        from migration.event_window import default_window, is_default_window
        s, e = default_window(TODAY)
        self.assertEqual((s, e), ('2025-11-11', '2026-11-11'))
        self.assertEqual(W.standing_default(TODAY), (s, e))
        self.assertEqual(W.default_label(TODAY), 'Nov 11, 2025 to Nov 11, 2026')
        self.assertTrue(is_default_window(*self.RETIRED))          # recognized (and rewritten), never produced
        import prometheus_memory as M
        self.assertTrue(M._is_default_pair(*self.RETIRED))
        self.assertTrue(M._is_default_pair('2025-11-10', '2026-11-10', '2026-11-10T21:54:51Z'))
        self.assertFalse(M._is_default_pair('2024-06-01', '2025-06-01', '2026-11-10T21:54:51Z'))
        import prometheus.legacy.chat as C
        import inspect
        src = inspect.getsource(C)
        self.assertNotIn("'2025-07-01'", src)
        # the sizing prompt named the retired pair as its default and its example (sizings were banked on it into
        # November); it carries the computed trailing year now
        sizing = src[src.index('def _synth_chat_incidence_check('):]
        sizing = sizing[:sizing.index('\ndef ', 10)]
        self.assertNotIn('07-01-2025', sizing)
        self.assertIn('_std_mdy', sizing)

    def test_the_read_prompts_default_to_the_trailing_year(self):
        import prometheus_analysis as pma
        s, e = pma.REASONED_METRICS_SYSTEM_PROMPT, pma.SEARCH_DEMAND_SYSTEM_PROMPT
        from migration.event_window import default_window
        a, b = default_window()
        for p in (s, e):
            self.assertIn(f"{a} to {b}", p)
            self.assertNotIn('2026-06-30', p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
