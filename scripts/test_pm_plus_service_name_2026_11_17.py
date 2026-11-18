#!/usr/bin/env python3
"""A plus is part of a service's name (2026-11-17, a guest on Jenna's account).

"Please pull a group who has engaged with Auberge Resorts, Discovery Land
Company, Aman Resorts, and Nobu Hotels ..." was read twice on the
Discovery+ library profile. Discovery Land Company builds private club
communities; Discovery+ is a streaming service. The matcher folds
punctuation away, so the library held "Discovery+" as the one word
"discovery" and it answered to any name that opens with that word.

The rule (prometheus.subject_rules.plus_name_not_written, the same fold
the brand identity rule product_line_expansion uses): a plus-named
service is named by its plus ("Discovery+", "discovery plus") or by its
bare word standing on its own ("Disney viewers", as readers write it).
A longer name that opens with the same word is another name: Discovery
Land Company, Disney World, Paramount Pictures. Nothing else about the
library changes: a longer ask over a plain base name (Costco Wholesale,
Taylor Swift Eras Tour) binds as before.

Hermetic: no network, no model, no S3, no app import. The end to end
cases (the read base itself) sit in test_pm_read_base_is_the_question.py.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import subject_rules as SR  # noqa: E402

# the guest's words, as typed in the two threads
GUEST_LINES = "Please pull a group who has engaged with Auberge Resorts\nDiscovery Land Company\nAman Resorts\nNobu Hotels"
GUEST = ("Please pull a group who has engaged with Auberge Resorts, Discovery Land Company, Aman Resorts, and Nobu Hotels "
         "over the last 12 months... also make sure to only look at those who have a household income of 250k+. "
         "So dates are nov 17 2025- nov 17 2026")
GUEST_NEW = ("No pull a new profile IQ for users who have a touchpoint to any of these four resorts and who have a household "
             "income of 250k+... the date range is nov 17 2025 - nov 17 2026 and the touchpoints are any touchpoint to one of "
             "these Auberge Resorts or Discovery Land Company or Aman Resorts or Nobu Hotels")
# the Reader's reading of it, as logged
READING = {'intent': 'build_profile', 'confidence': 0.93, 'subjects': [
    {'as_written': 'Auberge Resorts', 'catalog': '', 'kind': 'brand'},
    {'as_written': 'Discovery Land Company', 'catalog': '', 'kind': 'brand'},
    {'as_written': 'Aman Resorts', 'catalog': '', 'kind': 'brand'},
    {'as_written': 'Nobu Hotels', 'catalog': '', 'kind': 'brand'}]}

LIBRARY = [
    {'s3_key': 'Discovery_Plus_09_14_2026.csv', 'display_name': 'Discovery+', 'subject': 'Discovery+', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Discovery+ - Avid Fan.csv', 'display_name': 'Discovery+ - Avid Fan', 'subject': 'Discovery+', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Disney_Plus.csv', 'display_name': 'Disney+', 'subject': 'Disney+', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Paramount_Plus.csv', 'display_name': 'Paramount+', 'subject': 'Paramount+', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Apple_TV_Plus.csv', 'display_name': 'Apple TV+', 'subject': 'Apple TV+', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Costco_01.csv', 'display_name': 'Costco', 'subject': 'Costco', 'category': 'RETAILERS'},
    {'s3_key': 'Taylor_Swift.csv', 'display_name': 'Taylor Swift', 'subject': 'Taylor Swift', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Amazon.csv', 'display_name': 'Amazon', 'subject': 'Amazon', 'category': 'RETAILERS'},
]


def _names(lib):
    return [e['display_name'] for e in lib]


class TheGuestsSentence(unittest.TestCase):
    """Discovery Land Company is not Discovery+."""

    def test_no_wording_of_the_ask_names_discovery_plus(self):
        for text in (GUEST, GUEST_LINES, GUEST_NEW):
            for reading in (None, READING):
                with self.subTest(text=text[:40], reading=bool(reading)):
                    self.assertTrue(SR.plus_name_not_written('Discovery+', text, reading))
                    left = _names(SR.library_named(LIBRARY, text, reading))
                    self.assertNotIn('Discovery+', left)
                    self.assertNotIn('Discovery+ - Avid Fan', left)

    def test_the_rest_of_the_library_is_untouched(self):
        left = _names(SR.library_named(LIBRARY, GUEST, READING))
        self.assertEqual(left, [n for n in _names(LIBRARY) if not n.startswith('Discovery+')])

    def test_typed_in_lowercase_the_reading_settles_it(self):
        low = GUEST.lower()
        self.assertFalse(SR.plus_name_not_written('Discovery+', low, None))        # no capitals, no reading: unchanged
        self.assertTrue(SR.plus_name_not_written('Discovery+', low, READING))      # the Reader wrote the longer name


class TheServiceIsStillNamed(unittest.TestCase):
    """By its plus, or by the bare word on its own."""

    def test_written_with_its_plus(self):
        for text in ('what do Discovery+ viewers buy', 'discovery plus subscribers by age', 'DiscoveryPlus churn',
                     'compare Discovery + and Netflix', 'Discovery+ viewers who engaged with Discovery Land Company'):
            with self.subTest(text=text):
                self.assertFalse(SR.plus_name_not_written('Discovery+', text))
                self.assertIs(SR.library_named(LIBRARY, text), LIBRARY)

    def test_the_bare_word_on_its_own_is_the_service(self):
        for name, text in (('Discovery+', 'what do Discovery viewers buy'), ('Discovery+', 'what do discovery viewers buy'),
                           ('Disney+', 'Disney subscribers top brands'), ('Paramount+', 'how many Paramount subscribers churned'),
                           ('Disney+', "Disney's audience by age"), ('Disney+', 'Disney Avid Fan top retailers this year'),
                           ('Apple TV+', 'Apple TV viewers by income'), ('Disney+', 'Is Disney bigger than Netflix among parents?'),
                           ('Disney+', 'Disney Viewers Who Shop At Walmart'), ('Disney+', 'DISNEY TOP BRANDS')):
            with self.subTest(text=text):
                self.assertFalse(SR.plus_name_not_written(name, text))

    def test_a_reading_that_wrote_the_bare_word_or_matched_the_entry_wins(self):
        r1 = {'subjects': [{'as_written': 'Disney', 'catalog': '', 'kind': 'platform'}]}
        r2 = {'subjects': [{'as_written': 'Disney Top Brands', 'catalog': 'Disney+', 'kind': 'platform'}]}
        r3 = {'subjects': [{'as_written': 'Disney subscribers', 'catalog': '', 'kind': 'audience'}]}
        for r in (r1, r2, r3):
            self.assertFalse(SR.plus_name_not_written('Disney+', 'what are the Disney Top Brands for parents here', r))

    def test_a_hint_that_writes_the_plus_counts(self):
        left = SR.library_named(LIBRARY, GUEST, None, hint='Discovery+')
        self.assertIn('Discovery+', _names(left))


class OtherLongerNames(unittest.TestCase):
    """The same rule, other services."""

    def test_a_longer_name_on_the_same_word_is_another_name(self):
        for name, text in (('Disney+', 'what do Disney World visitors buy online'),
                           ('Paramount+', 'how many Paramount Pictures fans stream each week'),
                           ('Discovery+', 'people who visited a Discovery Cove park this year'),
                           ('Apple TV+', 'shoppers who bought an Apple TV Stand online this year')):
            with self.subTest(text=text):
                self.assertTrue(SR.plus_name_not_written(name, text))

    def test_named_once_on_its_own_is_enough(self):
        self.assertFalse(SR.plus_name_not_written('Disney+', 'do Disney World visitors also watch Disney at home'))

    def test_a_plain_base_name_under_a_longer_ask_is_not_this_rules_business(self):
        for text in ('what do Costco Wholesale members buy', 'Taylor Swift Eras Tour attendees top brands',
                     'Amazon Music listeners by age'):
            with self.subTest(text=text):
                self.assertIs(SR.library_named(LIBRARY, text), LIBRARY)
        for name in ('Costco', 'Taylor Swift', 'Amazon'):
            self.assertFalse(SR.plus_name_not_written(name, 'Costco Wholesale, Taylor Swift Eras Tour, Amazon Music'))

    def test_never_raises_and_never_guesses(self):
        for name, text, r in ((None, None, None), ('', 'x', {}), ('Discovery+', '', READING), ('Discovery+', 'Hulu viewers', READING),
                              ('Discovery+', GUEST, {'subjects': 'nope'}), ('Discovery+', GUEST, {'subjects': [None, 3, {}]})):
            SR.plus_name_not_written(name, text, r)
        self.assertFalse(SR.plus_name_not_written('Discovery+', 'Hulu viewers', READING))
        self.assertEqual(SR.library_named(None, GUEST), None)
        self.assertEqual(SR.library_named([None, 3, {'subject': 'Discovery+'}], 'Hulu'), [None, 3, {'subject': 'Discovery+'}])


class TheWiring(unittest.TestCase):
    """The read base, the 'does the ask name a library subject' check, the
    two-audience pair and the near-spelling tier all read the library
    through the rule. Source check only: the chat module is not imported."""

    def test_the_base_tiers_read_the_library_through_the_rule(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertGreaterEqual(len(re.findall(r"_subject_rules\.library_named\(_profile_catalog_for_chat\(\), text", src)), 4)
        self.assertIn("_subject_rules.library_named(_profile_catalog_for_chat(), t)", src)
        base = src.split('def _pm_generation_base(', 1)[1].split('\n_pm_generation_base = ', 1)[0]
        self.assertEqual(base.count('_subject_rules.library_named('), 2)
        self.assertNotIn('for entry in _profile_catalog_for_chat():', base)


if __name__ == '__main__':
    unittest.main(verbosity=1)
