#!/usr/bin/env python3
"""An open-ended insight request is the analyst's job, and "email to
<a third party>" is what the reader will do with the answer (Jenna
2026-11-19).

Mike Pusateri at Bent Pixels, 2026-11-19 17:18 UTC, asked: "what's a
unique insight we can email to Lego around lego shopping behaviour this
month". Prometheus answered: "Nothing has landed in this chat yet for me
to send. Ask me a data question first and then say Email me this file."
The send-a-file check read "email ... this" as an order to email the
last file. Jenna, verbatim: "prometheus shouild find cool data it could
give that would be impressive to lego doe that make sense."

What this suite holds:

  1. The words: who a send is for. A third party after email / send /
     share / pitch / take to / show is what the READER will do; an order
     on a thing that exists ("email me this file", "send me that deck",
     "email this to me") keeps the send lanes, word for word.
  2. The family: his question as typed and a dozen phrasings across
     brands, titles and talent are read as one request, with the
     subject, who it is for, the angle and the window; the questions
     that are not that request are left alone.
  3. The file: every row is read against the US, the subject's own rows
     and mirrored copies are left out, the angle's sections lead.
  4. The brief and the reply: the strongest finding first, why it
     matters, the support with the US comparison, the forward-ready
     version, the one-pager line; nothing says anything was sent to the
     third party; the house words hold.
  5. The wiring: the lane's place at the door, the two splices in the
     chat module, the Reader's send order, the outreach lane, the line
     ratchet, no mail and no request field.
  6. Through the door, model canned: his question with the library
     holding Lego (nothing open, Lego open, another profile open, the
     older route); the family on other held subjects; a subject the
     library does not hold (the standing quote, unchanged); a question
     the price rules quote (unchanged); the send-a-file cases with
     nothing landed and after a read (unchanged).

Hermetic: no model, no live store, no mail, no app boot. The door checks
run in a child process on the recording host from the parent repo and
are skipped with a note when it is not on the path (PM_DOOR_REQUIRED=1
makes that a failure; the parent repo's wrapper sets it).
"""
from __future__ import annotations

import datetime
import io
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import insight_ask as IA  # noqa: E402

HIS = "what's a unique insight we can email to Lego around lego shopping behaviour this month"
THAT_DAY = datetime.date(2026, 11, 19)
MONTH_LINE = 'November on its own is a separate read; ask for it and I will run it.'
WHAT_HE_READ = ("Nothing has landed in this chat yet for me to send. Ask me a data question first and then say Email me this file.")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def _legacy_email_intent():
    """The send-a-file check as the chat module defines it, built from
    its own source so the test reads the shipped pattern."""
    src = _read('prometheus', 'legacy', 'chat.py')
    a = src.index('_PM_EMAIL_FILE_RE = re.compile(')
    b = src.index('_PM_EMAIL_ADDR_RE = re.compile(', a)
    ns = {'re': re}
    exec(src[a:b], ns)
    rx = ns['_PM_EMAIL_FILE_RE']

    def _intent(text):
        t = str(text or '').strip()
        if not t or len(t) > 90:
            return False
        if t.lower().strip(' .!') == 'email me this file':
            return True
        return bool(rx.search(t))
    return _intent


# the columns a library file carries
HEAD = 'Column,Value,Brand Penetration (Row),Category Share,Original Raw Numbers,US Gen Pop Projection,Gen Pop Penetration,Index vs Gen Pop'
ROWS = (
    ('GENDER', 'FEMALE', 49.5, 49.5, None), ('GENDER', 'MALE', 50.5, 50.5, None),
    ('AGE', '25-34', 22.4, 22.4, None), ('AGE', '35-44', 28.4, 28.4, None), ('AGE', '45-54', 49.2, 49.2, None),
    ('WHERE THEY SHOP', 'AMAZON', 81.3, 3.1, 79.1), ('WHERE THEY SHOP', 'WALMART', 69.2, 2.9, 62.3),
    ('WHERE THEY SHOP', 'EBAY', 49.865, 3.4, 30.1199), ('WHERE THEY SHOP', 'HOBBY LOBBY', 23.8276, 1.6, 5.4702),
    ('WHERE THEY SHOP', 'TRACTOR SUPPLY', 19.3, 1.3, 6.8), ('WHERE THEY SHOP', 'MACYS', 19.6, 1.2, 22.0),
    ('HOME/OUTDOOR', 'CRAYOLA', 19.3165, 3.7, 2.2988), ('MOST PURCHASED BRANDS', 'CRAYOLA', 19.3165, 0.5, 2.2988),
    ('MOST PURCHASED BRANDS', 'NIKE', 31.9, 1.1, 23.1), ('MOST PURCHASED BRANDS', 'OLD NAVY', 7.9, 0.3, 21.9),
    ('TOYS', 'LEGO', 30.0799, 5.0, 9.6992), ('TOYS', 'STAR WARS', 16.1, 2.7, 3.8), ('TOYS', 'MEGA BLOKS', 7.7, 1.2, 2.2),
    ('GAMES', 'LEGO', 31.8786, 3.4, 4.0625), ('FRANCHISE', 'LEGO', 100.0, 6.9, 24.3006),
    ('AMUSEMENT PARKS', 'LEGOLAND', 5.3, 1.1, 3.7),
    ('STREAMING/PLATFORM', 'NETFLIX', 66.8, 9.0, 68.2), ('STREAMING/PLATFORM', 'PEACOCK', 18.0, 2.4, 37.0),
    ('STREAMING/PLATFORM', 'YOUTUBE KIDS', 18.3, 2.5, 8.4),
    ('APP/PLATFORM USAGE', 'UBER EATS', 10.9, 0.4, 25.9), ('QSR', 'MCDONALDS', 55.0, 4.0, 51.7),
    ('CREATOR/INFLUENCER', 'Nick Massacre', 0.9, 0.1, 0.06), ('TALENT', 'TOM HOLLAND', 28.4, 1.0, 17.9),
    ('ACTOR', 'TOM HOLLAND', 28.4, 1.0, 17.9),
)
PEOPLE, SAMPLE = 13619823, 412847


def profile_csv(subject, window='2025-10-20 TO 2026-10-20'):
    out = [HEAD,
           f'BRAND INPUT,{subject},100.0,100.0,{SAMPLE},{PEOPLE},,',
           f'SAMPLE SIZE,{window},100.0,100.0,{SAMPLE},{PEOPLE},,',
           f'BRAND CATEGORY,TOY,100.0,100.0,{SAMPLE},{PEOPLE},,',
           f'SUBJECT,{subject},100.0,100.0,{SAMPLE},{PEOPLE},,']
    for cat, val, pen, share, us in ROWS:
        raw = round(SAMPLE * pen / 100)
        ppl = round(raw / 10_000_000 * 329_900_000)
        out.append(f"{cat},{val},{pen},{share},{raw},{ppl},{'' if us is None else us},{'' if us is None else round(pen / us * 100, 1)}")
    return '\n'.join(out) + '\n'


def _df(subject='Lego'):
    import pandas as pd
    return pd.read_csv(io.StringIO(profile_csv(subject))).fillna('')


BANNED_WORDS_RX = re.compile(
    r"\b(?:synth\w*|model(?:l)?ed|estimat\w*|pipeline|hostmap|content\s+map|source[sd]?|confidence|real[- ]time|households?|"
    r"panel[- ]projected|predicted|illustrative|claude|anthropic|digest|idx)\b|\u2014|\u2013", re.I)


# ============================================================================ 1. the words
class WhoTheSendIsFor(unittest.TestCase):
    def test_his_question_is_not_an_order_to_send_a_file(self):
        legacy = _legacy_email_intent()
        self.assertTrue(legacy(HIS))                                    # the cause: "email ... this" within a few words
        self.assertFalse(IA.not_a_send(legacy)(HIS))                    # the fix
        self.assertEqual(IA.reader_will_share(HIS), 'Lego')
        self.assertFalse(IA.is_send_order(HIS))

    def test_a_third_party_after_the_verb_is_what_the_reader_will_do(self):
        for q, who in (
                ("what's a unique insight we can email to Lego around lego shopping behaviour this month", 'Lego'),
                ("is there something we can send to the client about this", 'the client'),
                ("find me something we can pitch Nike", 'Nike'),
                ("something we can take to Nike", 'Nike'),
                ("what can we email Lego about their shoppers", 'Lego'),
                ("give me a surprising stat about Taylor Swift fans we can share with the Nike team", 'Nike'),
                ("is there anything cool we could show the client about landman", 'the client'),
                ("got an interesting nugget about peacock subscribers for the pitch to NBC", 'NBC'),
                ("what should I pitch Nike", 'Nike'),
                ("give me a killer stat for our Nike meeting", 'Nike'),
                ("an insight worth sharing with Paramount", 'Paramount')):
            self.assertEqual(IA.reader_will_share(q), who, q)
            self.assertTrue(IA.stands_down(q), q)

    def test_the_reader_s_own_self_and_a_typed_address_are_not_a_third_party(self):
        for q in ("email me this file", "send me that deck", "email this to me", "send me that", "send this to my inbox",
                  "what's an insight we can send to me", "email it to mike@bentpixels.com", "what can we send to us",
                  "i want to take a look at lego shoppers", "find me something to use Landman data for"):
            self.assertEqual(IA.reader_will_share(q), '', q)

    def test_the_send_a_file_orders_are_untouched(self):
        legacy = _legacy_email_intent()
        wrapped = IA.not_a_send(legacy)
        for q in ("email me this file", "Email me this file.", "send me that deck", "email this to me", "send me that",
                  "send this to my inbox", "shoot this to my email", "mail it to me please", "email that to my team",
                  "email it to mike@bentpixels.com", "email this to Lego", "email this data to my boss",
                  "please email me the file", "can you email me this csv", "great, email me this so I can forward to Lego",
                  "send this to my inbox so we can share with the client", "email me that data"):
            self.assertEqual(wrapped(q), legacy(q), q)
            self.assertFalse(IA.stands_down(q), q)
        for q in ("email me this file", "email this to me", "send this to my inbox", "email it to mike@bentpixels.com",
                  "email this to Lego", "can you email me this csv"):
            self.assertTrue(wrapped(q), q)                               # still the send lane's
        for q in ("email me this file", "send me that deck", "email this to me", "send me that", "please forward that to the client"):
            self.assertTrue(IA.is_send_order(q), q)

    def test_the_named_send_lane_still_reads_its_orders(self):
        from prometheus import send_file as SF
        for q in ("email me this file", "email this to me", "send me that", "email me the Babylon 5 breakdown",
                  "send me the Nike profile"):
            self.assertTrue(SF.parse(q), q)
            self.assertIsNone(IA.parse(q), q)

    def test_the_outreach_lane_stands_down_for_a_question(self):
        from prometheus import capabilities as PC
        for q in ("what can we send to the client about this", "give me a stat to share with the Nike team",
                  "what's a unique insight we can share with the brand team"):
            self.assertIsNone(PC.action_request(q), q)
        for q in ("reach out to the client for me", "slack the team", "call the client", "schedule a meeting with the client",
                  "refund my credits", "add a user to my account"):
            self.assertIsNotNone(PC.action_request(q), q)                # asking Prometheus to do it is still that lane's

    def test_the_switch(self):
        legacy = _legacy_email_intent()
        os.environ['PM_INSIGHT_ASK'] = '0'
        try:
            self.assertTrue(IA.not_a_send(legacy)(HIS))
            self.assertIsNone(IA.parse(HIS))
        finally:
            os.environ.pop('PM_INSIGHT_ASK', None)
        self.assertIsNotNone(IA.parse(HIS))


# ============================================================================ 2. the family
FAMILY = (
    # (the words, subject, who it is for, angle, window)
    (HIS, 'Lego', 'Lego', 'shopping', 'this month'),
    ("whats a unique insight we can email to lego around lego shopping behaviour this month", 'Lego', 'Lego', 'shopping', 'this month'),
    ("what would impress Lego", 'Lego', 'Lego', 'general', ''),
    ("What would wow the Dr Pepper team?", 'Dr Pepper', 'Dr Pepper', 'general', ''),
    ("find me something we can pitch Nike", 'Nike', 'Nike', 'general', ''),
    ("what stands out about Lego's shoppers this month", 'Lego', '', 'shopping', 'this month'),
    ("give me a hook for Dr Pepper", 'Dr Pepper', '', 'general', ''),
    ("what's a cool insight on Landman viewers we can send to Paramount", 'Landman', 'Paramount', 'viewing', ''),
    ("give me a surprising stat about Taylor Swift fans we can share with the Nike team", 'Taylor Swift', 'Nike', 'general', ''),
    ("what would nike want to know about its own shoppers", 'Nike', 'Nike', 'shopping', ''),
    ("what does Lego not know about its own customers", 'Lego', 'Lego', 'shopping', ''),
    ("any surprising findings on yellowstone viewers", 'Yellowstone', '', 'viewing', ''),
    ("got an interesting nugget about peacock subscribers for the pitch to NBC", 'Peacock', 'NBC', 'general', ''),
    ("is there anything cool we could show the client about landman", 'Landman', 'the client', 'viewing', ''),
    ("what's the most interesting thing about Taylor Swift fans", 'Taylor Swift', '', 'general', ''),
    ("what can we email Lego about their shoppers", 'Lego', 'Lego', 'shopping', ''),
    ("can you email Lego a unique insight about their shoppers", 'Lego', 'Lego', 'shopping', ''),
    ("is there a story we can tell Peacock about Love Island viewers", 'Love Island', 'Peacock', 'viewing', ''),
    ("what's interesting in the data for Lego this month", 'Lego', '', 'general', 'this month'),
    ("give me insights on Nike", 'Nike', '', 'general', ''),
    ("send me something interesting on Lego", 'Lego', '', 'general', ''),
    ("anything unusual with Costco members lately", 'Costco', '', 'general', 'lately'),
)
NOT_FAMILY = (
    "email me this file", "send me that deck", "email this to me", "send me that", "email this to Lego",
    "email it to mike@bentpixels.com", "what is the Nike penetration here", "how many people watched Landman",
    "top 10 retailers Lego shoppers over-index on that we can email to Lego", "build a deck we can send to Lego",
    "write a linkedin post with a cool insight about Lego", "what does this number mean", "what is an insight",
    "show me the insights tab", "how many insights have we run", "what stands out about this audience", "what stands out here",
    "key takeaways from this profile", "i want to take a look at lego shoppers", "what's new with Lego",
    "run a profile on Lego", "give me the numbers we can send to Lego", "the export button does nothing",
    "who watches Yellowstone", "what do Taylor Swift fans buy",
    # from the question history (2026-08-26 to 2026-11-19): words that sit near the family and are not it
    "What stands out in trends today?",
    "Can we see New Heights podcast insights on Amazon, Spotify, Apple podcast channel views, subscribers, listeners, and downloads",
    "can we see new heights podcast insights on amazon",
)


class TheFamily(unittest.TestCase):
    def test_his_question_as_typed(self):
        p = IA.parse(HIS)
        self.assertEqual((p['subject'], p['recipient'], p['audience'], p['angle'], p['window']),
                         ('Lego', 'Lego', 'shopping behaviour', 'shopping', 'this month'))
        self.assertFalse(p['points_at_screen'])

    def test_the_family_across_brands_titles_and_talent(self):
        for q, subj, who, angle, window in FAMILY:
            p = IA.parse(q)
            self.assertIsNotNone(p, q)
            self.assertEqual((p['subject'], p['recipient'], p['angle'], p['window']), (subj, who, angle, window), q)

    def test_what_is_not_the_family_is_left_alone(self):
        for q in NOT_FAMILY:
            self.assertIsNone(IA.parse(q), q)

    def test_a_pointer_at_the_screen_needs_someone_to_take_it_to_or_a_word_for_striking(self):
        p = IA.parse("what's a cool insight here")
        self.assertTrue(p and p['points_at_screen'] and not p['subject'])
        p = IA.parse("what are the key takeaways from this profile we can send to the client")
        self.assertTrue(p and p['points_at_screen'] and p['recipient'] == 'the client')
        self.assertIsNone(IA.parse("key takeaways from this profile"))

    def test_no_subject_and_no_one_to_take_it_to_is_left_to_the_standing_read(self):
        # from the question history: asked with a profile open, answered by the page read then and now
        p = IA.parse("what are the craziest insights from the consumer's of Brock's channel?")
        self.assertEqual((p['subject'], p['recipient'], p['points_at_screen']), ('', '', False))
        src = _read('prometheus', 'insight_ask.py')
        self.assertIn("elif str(p.get('recipient') or '').strip():", src)   # the thread's subject only when the words say who it is for

    def test_a_long_message_and_a_typed_address_are_not_read_here(self):
        self.assertIsNone(IA.parse(HIS + ' ' + 'and also ' * 60))
        self.assertIsNone(IA.parse("what's a unique insight we can email to jane@lego.com about lego shoppers"))

    def test_the_price_rules_still_read_a_put_together_read(self):
        from prometheus import priced_read as PR
        self.assertIsNone(PR.classify(HIS, [], None))                    # talk on a file: metered, never quoted
        for q in ("top 10 creators Lego shoppers over-index on that we can email to Lego",
                  "rank the top 5 retailers for Lego shoppers we can send to the client"):
            self.assertIsNone(IA.parse(q), q)
            self.assertIsNotNone(PR.classify(q, [], None), q)


# ============================================================================ 3. the file
class TheWholeFile(unittest.TestCase):
    def setUp(self):
        self.found = IA.findings(_df('Lego'), None, subject='Lego', angle='shopping', audience_people=PEOPLE)

    def test_every_row_is_read_against_the_us(self):
        over = {r['value']: r for r in self.found['over']}
        for name in ('Hobby Lobby', 'Crayola', 'eBay', 'Tractor Supply', 'Star Wars', 'Mega Bloks', 'YouTube Kids'):
            self.assertIn(name, over, name)
        hl = over['Hobby Lobby']
        self.assertEqual((hl['pen'], hl['us'], hl['cat'], hl['bought']), (23.8, 5.5, 'WHERE THEY SHOP', True))
        self.assertAlmostEqual(hl['x'], 23.8276 / 5.4702, places=3)
        self.assertEqual(hl['people'], round(round(SAMPLE * 23.8276 / 100) / 10_000_000 * 329_900_000))
        under = {r['value'] for r in self.found['under']}
        self.assertTrue({'Peacock', 'Uber Eats', 'Old Navy'} <= under, under)

    def test_the_subject_s_own_rows_and_thin_rows_are_left_out(self):
        names = [r['value'].lower() for r in self.found['over'] + self.found['under']]
        self.assertFalse([n for n in names if 'lego' in n], names)        # Lego, Legoland: nothing Lego does not already know
        self.assertNotIn('nick massacre', names)                         # a huge multiple on a sliver is not a finding
        self.assertNotIn('amazon', names)                                # at the US rate: not a finding
        self.assertNotIn('female', names)

    def test_one_brand_is_one_row_and_the_angle_leads(self):
        names = [r['value'] for r in self.found['over']]
        self.assertEqual(names.count('Crayola'), 1)
        self.assertEqual(names.count('Tom Holland'), 1)
        self.assertEqual(self.found['over'][0]['value'], 'Crayola')
        shop = [r['value'] for r in self.found['over'] if r['angle']]
        self.assertTrue(shop.index('Hobby Lobby') < shop.index('Tractor Supply') < shop.index('eBay') or 'eBay' in shop)
        view = IA.findings(_df('Lego'), None, subject='Lego', angle='viewing', audience_people=PEOPLE)
        self.assertTrue(next(r for r in view['over'] if r['value'] == 'YouTube Kids')['angle'])

    def test_the_block_the_analysis_pass_reads(self):
        block = IA.findings_block(self.found, subject='Lego', audience_people=PEOPLE, window='2025-10-20 to 2026-10-20', angle='shopping')
        self.assertIn('HIGHER THAN THE US, IN WHAT WAS ASKED ABOUT (shopping behaviour), strongest first:', block)
        self.assertIn('HIGHER THAN THE US, ELSEWHERE IN THE FILE:', block)
        self.assertLess(block.index('| Hobby Lobby |'), block.index('ELSEWHERE IN THE FILE'))
        self.assertLess(block.index('ELSEWHERE IN THE FILE'), block.index('| YouTube Kids |'))
        self.assertTrue(block.startswith('MEASURED FINDINGS FROM THE FULL FILE: Lego, 13,619,823 US people, the 12 months to October 20, 2026'))
        self.assertIn('WHERE THEY SHOP | Hobby Lobby | 23.8% | 5.5% | 4.4 times the US rate | 3,245,', block)
        self.assertIn('confirmed online purchase', block)
        self.assertIn('LOWER THAN THE US', block)
        self.assertIn('STREAMING/PLATFORM | Peacock | 18.0% | 37.0% | 0.49 of the US rate', block)
        self.assertEqual(IA.findings_block({'over': [], 'under': []}, subject='Lego'), '')

    def test_a_row_that_reads_near_zero_or_higher_elsewhere_is_not_a_low(self):
        import pandas as pd
        rows = profile_csv('Lego') + ('PHARMACY,CVS HEALTH,0.02,0.1,83,2738,28.2,0.1\n'
                                      'CASUAL DINING,CHICK-FIL-A,5.1,1.0,21055,694604,35.8,14.2\n'
                                      'QSR,CHICK-FIL-A,43.2,3.0,178350,5883766,35.8,120.7\n')
        found = IA.findings(pd.read_csv(io.StringIO(rows)).fillna(''), None, subject='Lego', angle='shopping', audience_people=PEOPLE)
        low = {r['value'] for r in found['under']}
        self.assertNotIn('CVS Health', low)                              # a row that reads near zero is not a finding
        self.assertNotIn('Chick-fil-A', low)                             # the same brand reads 43.2% in its own section
        self.assertIn('Peacock', low)

    def test_a_file_without_its_own_us_column_reads_the_us_file(self):
        import pandas as pd
        df = pd.read_csv(io.StringIO(
            'Column,Value,Brand Penetration (Row),Raw,Projection,Category Share\n'
            'BRAND INPUT,Nike,100.0000%,123457,4073013,100\n'
            'WHERE THEY SHOP,FOOT LOCKER,31.2000%,38519,1270780,9.1\n'
            'WHERE THEY SHOP,AMAZON,80.0000%,98766,3258410,20.1\n')).fillna('')
        found = IA.findings(df, {('WHERE THEY SHOP', 'footlocker'): 9.1, ('WHERE THEY SHOP', 'amazon'): 79.1}, subject='Nike',
                            angle='shopping', audience_people=4073013)
        self.assertEqual([(r['value'], r['pen'], r['us'], r['people']) for r in found['over']], [('Foot Locker', 31.2, 9.1, 1270780)])

    def test_the_window_in_plain_words(self):
        self.assertEqual(IA.window_plain('2025-10-20 to 2026-10-20'), 'the 12 months to October 20, 2026')
        self.assertEqual(IA.window_plain('2025-10-20 TO 2026-10-20'), 'the 12 months to October 20, 2026')
        self.assertEqual(IA.window_plain('01/01/2025 to 06/30/2025'), 'January 1, 2025 to June 30, 2025')
        self.assertEqual(IA.window_plain(''), '')


# ============================================================================ 4. the brief and the reply
class TheBriefAndTheReply(unittest.TestCase):
    def setUp(self):
        self.p = IA.parse(HIS)
        self.found = IA.findings(_df('Lego'), None, subject='Lego', angle='shopping', audience_people=PEOPLE)

    def test_the_brief_asks_for_the_hand_over(self):
        b = IA.brief(self.p, subject='Lego', window='2025-10-20 to 2026-10-20')
        for must in ('INSIGHT REQUEST', 'put in front of Lego', 'Pick the ONE finding', 'The first sentence of the reply IS that finding',
                     'why it would matter to Lego', 'Three rows at most above the forward-ready version', IA.FORWARD_LABEL, 'one-pager', 'call them "the Lego audience"',
                     'never call them shoppers, buyers or customers as a group',
                     'Nothing is sent to Lego by you', 'the 12 months to October 20, 2026', 'The reader asked about "this month"',
                     'never households', 'never "real-time" or "today"', 'times the US rate', IA.ONE_PAGER_CHIP,
                     'naming one row only', 'a multiple is quoted as its line prints it', 'plain words a high school student would follow',
                     'The words "In our profile of the Lego audience" open the reply', "In Crosswalk's profile of the Lego audience,",
                     'a large share with a large multiple of the US rate beats a huge multiple on a sliver'):
            self.assertIn(must, b, must)
        self.assertNotIn('\u2014', b)
        two = IA.brief(IA.parse("what's a cool insight on Landman viewers we can send to Paramount"), subject='Landman')
        self.assertIn('put in front of Paramount', two)
        self.assertIn('Paramount is a third party', two)
        self.assertIn('call them "Landman viewers"', two)                 # the reader's own word for a title's people stands
        self.assertEqual(IA._they(self.p, 'Costco Members'), 'Costco Members')   # a file that names its people keeps the name

    def test_the_read_that_follows_a_quote_lands_in_the_same_shape(self):
        import prometheus_analysis as PMA
        rb = IA.read_brief("give me a hook for Dr Pepper")
        self.assertIn(IA.FORWARD_LABEL, rb)
        self.assertIn('Dr Pepper', rb)
        self.assertEqual(IA.read_brief("how many people watched Landman in 2024"), '')
        up = PMA.build_reasoned_metrics_user_prompt("give me a hook for Dr Pepper", [])
        self.assertIn('INSIGHT REQUEST', up)
        self.assertLess(up.index("USER'S QUESTION"), up.index('INSIGHT REQUEST'))
        self.assertNotIn('INSIGHT REQUEST', PMA.build_reasoned_metrics_user_prompt("how many people watched Landman in 2024", []))
        self.assertNotIn('INSIGHT REQUEST', PMA.build_analysis_user_prompt('PROFILE: Lego', [], 'what stands out about this audience'))

    def test_the_reply_written_from_the_rows(self):
        text = IA.compose(self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', audience_people=PEOPLE)
        first = text.split('\n')[0]
        self.assertEqual(first, 'In our profile of the Lego audience, 19.3% bought Crayola, against 2.3% of US people, 8.4 times the US rate.')
        self.assertIn('Lego cannot see this in its own sales', text)
        self.assertIn('\n- ', text)
        self.assertIn(IA.FORWARD_LABEL + '\n', text)
        self.assertTrue(text.split(IA.FORWARD_LABEL + '\n')[1].startswith("In Crosswalk's profile of the Lego audience, 19.3% bought Crayola"))
        self.assertNotIn('shoppers', text)                               # the file holds the people who engaged with Lego, not only its buyers
        self.assertTrue(text.rstrip().endswith(IA.ONE_PAGER_LINE))
        self.assertIn('the 12 months to October 20, 2026', text)
        self.assertNotIn('this month', text.lower())                     # the file does not separate the month he asked about
        self.assertIsNone(BANNED_WORDS_RX.search(text), BANNED_WORDS_RX.search(text))
        # every share carries its US comparison
        for line in [ln for ln in text.split('\n') if re.search(r"\d+\.\d%", ln) and 'covers' not in ln]:
            self.assertRegex(line, r"US people|US rate", line)
        # counts are the file's own, never a tidy placeholder
        for n in re.findall(r"\b\d{1,3}(?:,\d{3})+\b", text):
            self.assertFalse(n.endswith(',000'), n)

    def test_the_reply_passes_the_house_scrubs_as_written(self):
        import prometheus_analysis as PMA
        from prometheus import guards as PG
        text = IA.compose(self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', audience_people=PEOPLE)
        self.assertEqual(PMA.scrub_user_text(text), text)
        done = IA.finish(text, self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', today=THAT_DAY)
        self.assertEqual(done, text + '\n\n' + MONTH_LINE)               # only the line about the month he asked for is added
        self.assertEqual(PMA.scrub_user_text(done), done)
        scrub = getattr(PG, 'scrub_reply', None) or getattr(PG, 'scrub', None)
        if callable(scrub):
            out = scrub(done)
            out = out[0] if isinstance(out, tuple) else out
            self.assertEqual(str(out).strip(), done.strip())

    def test_the_month_he_asked_about_is_said_plainly(self):
        mo = IA.asked_month(self.p, today=THAT_DAY)
        self.assertEqual((mo['label'], mo['start'], mo['end']), ('November', datetime.date(2026, 11, 1), datetime.date(2026, 11, 18)))
        self.assertEqual(mo['range'], 'November 1 to November 18, 2026')    # the data is a day behind
        self.assertEqual(IA.window_line(self.p, '2025-10-20 to 2026-10-20', today=THAT_DAY),
                         'These figures cover the 12 months to October 20, 2026. ' + MONTH_LINE)
        self.assertEqual(IA.window_chip(self.p, 'Lego', today=THAT_DAY), 'Lego shopping behaviour from November 1 to November 18, 2026')
        last = IA.asked_month(IA.parse("what would impress Lego about their shoppers last month"), today=THAT_DAY)
        self.assertEqual(last['range'], 'October 1 to October 31, 2026')
        first = IA.asked_month(self.p, today=datetime.date(2026, 12, 1))
        self.assertEqual(first['range'], 'November 1 to November 30, 2026')
        self.assertIsNone(IA.asked_month(IA.parse("what would impress Lego"), today=THAT_DAY))
        self.assertEqual(IA.window_line(IA.parse("what would impress Lego"), '2025-10-20 to 2026-10-20', today=THAT_DAY), '')
        bare = IA.finish('23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people.', self.p, subject='Lego',
                         found=self.found, window='', today=THAT_DAY)
        self.assertNotIn('separate read', bare)                           # no window on the file: nothing is claimed about it
        for w in ('this month', 'today', 'real-time', 'right now'):
            self.assertNotIn(w, IA.window_line(self.p, '2025-10-20 to 2026-10-20', today=THAT_DAY).lower())
        chips = IA._chips(['Email this to Lego', 'Where else do they shop?', 'turn it into a one-pager'], self.p, subject='Lego', today=THAT_DAY)
        self.assertEqual(chips, [IA.ONE_PAGER_CHIP, 'Lego shopping behaviour from November 1 to November 18, 2026', 'Where else do they shop?'])

    def test_nothing_says_it_was_sent_to_the_third_party(self):
        said = ("23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people, 4.4 times the US rate. "
                "I have emailed this to Lego for you. That leans toward a crafting household.\n\n"
                "I will send the full note to Lego's team this afternoon.\n\n"
                "Forward-ready version:\nYour audience bought from Hobby Lobby at 4.4 times the US rate. We can share the rest with your team.")
        out = IA.finish(said, self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', today=THAT_DAY)
        self.assertNotIn('emailed this to Lego', out)
        self.assertNotIn('I will send the full note', out)
        self.assertIn('23.8% of the Lego audience bought from Hobby Lobby', out)
        self.assertIn('We can share the rest with your team.', out)        # the reader's own voice, left as written
        self.assertNotIn('household', out.lower())
        self.assertIn(IA.ONE_PAGER_LINE, out)

    def test_a_multiple_is_the_file_s_own(self):
        said = ("23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people, 4.3x the US rate, which works out to "
                "3,245,292 people.\n\n- Tractor Supply: 19.3% bought, against 6.8% of US people, 2.7 times the US rate.\n\n"
                "Forward-ready version:\n23.8% of your audience bought from Hobby Lobby, against 5.5% of US people, 4.3x the US rate.")
        out = IA.finish(said, self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', today=THAT_DAY)
        self.assertEqual(out.count('against 5.5% of US people, 4.4 times the US rate'), 2)
        self.assertIn('against 6.8% of US people, 2.8 times the US rate', out)      # 19.3 over 6.8 in the fixture
        self.assertNotRegex(out, r"\dx the US rate")

    def test_a_reply_without_the_forward_ready_version_gets_one(self):
        out = IA.finish('23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people.', self.p, subject='Lego',
                        found=self.found, window='2025-10-20 to 2026-10-20', today=THAT_DAY)
        self.assertIn(IA.FORWARD_LABEL + '\n', out)
        self.assertIn(IA.ONE_PAGER_LINE, out)
        self.assertEqual(out.count(IA.FORWARD_LABEL), 1)
        self.assertTrue(out.endswith(MONTH_LINE))
        again = IA.finish(out, self.p, subject='Lego', found=self.found, window='2025-10-20 to 2026-10-20', today=THAT_DAY)
        self.assertEqual(again, out)                                     # idempotent


# ============================================================================ 5. the wiring
class TheWiring(unittest.TestCase):
    def test_the_lane_s_place_at_the_door(self):
        src = _read('prometheus', 'service.py')
        at = src.index('# lane: insight_ask')
        for earlier in ('# lane: intake_cancel', '# lane: dashboard_behavior', '# lane: offer_acceptance',
                        '# lane: priced_offer_acceptance', '# lane: offer_answer', '# lane: document'):
            self.assertLess(src.index(earlier), at, earlier)             # an armed step, an offer's answer and an attached file come first
        for later in ('# lane: read_brief', '# lane: continuation', '# lane: multi_ask', '# lane: reader', '# lane: email_file',
                      '# lane: surface_decision', '# lane: work_gate'):
            self.assertLess(at, src.index(later), later)                 # ahead of the Reader, the send lane and every lane that quotes
        lane = src[at:src.index('# lane: read_brief')]
        self.assertIn("if not _armed(body) and not _is_order and not _no_build and not body.get('_meta_replay'):", lane)
        self.assertIn('_ia_go = bool(_ia.wants(text)) and bool(host.gate_analyze(user))', lane)
        self.assertIn('_ia_go = bool(_sg_ia.check(user)[0])', lane)        # the daily spend cap holds here as on every model surface
        self.assertIn("_ia_raw.get('insight_ask')", lane)
        self.assertIn("'reason': 'insight_ask'", lane)
        for banned in ('consume_credit', 'offer_or_charge', 'panel_offer', 'send_file', 'outbound_mail'):
            self.assertNotIn(banned, lane, banned)                       # the lane prices nothing and sends nothing
        from prometheus import service as S
        self.assertEqual(S.lane_markers(), list(S.LANE_ORDER))
        self.assertEqual(S.LANE_ORDER[S.LANE_ORDER.index('deck_conversion') + 1], 'insight_ask')
        ex = src[src.index('def _execute_reading('):src.index('def _restate_reads_clean(')]
        self.assertIn('_ia_cmd.stands_down(text)', ex)
        self.assertLess(ex.index('_ia_cmd.stands_down(text)'), ex.index('raw = _sf.answer(text, uname, user=user'))

    def test_the_two_splices_in_the_chat_module(self):
        chat = _read('prometheus', 'legacy', 'chat.py')
        wrap = "_pm_email_file_intent = __import__('prometheus.insight_ask', fromlist=['not_a_send']).not_a_send(_pm_email_file_intent)"
        hook = "__import__('prometheus.insight_ask', fromlist=['core_hook']).core_hook(text, body, history, user, _pm_ppu, globals())"
        self.assertEqual(chat.count(wrap), 1)
        self.assertEqual(chat.count(hook), 1)
        self.assertLess(chat.index('def _pm_email_file_intent(text):'), chat.index(wrap))
        self.assertLess(chat.index(wrap), chat.index('def _pm_email_file_response(user, text, history=None):'))
        call = "    if _pm_email_file_intent(text):\n        return _pm_email_file_response(user, text, history)"
        self.assertEqual(chat.count(call), 1)
        self.assertLess(chat.index(hook), chat.index(call))              # read as a question before it can be read as a send
        self.assertLess(chat.index('_funds_resp = _pm_funds_gate(user)'), chat.index(hook))   # behind the access and funds gates
        self.assertIn(WHAT_HE_READ.split('. ')[0], chat)                 # the line itself stays for a real send with nothing landed
        self.assertLess(len(chat.splitlines()), 19900)                   # the line ratchet holds

    def test_the_core_hook_stands_aside(self):
        calls = []
        G = {'_H': None, 'jsonify': lambda d: d, '_pm_validate_page_context': lambda pc: calls.append(pc) or (None, None)}
        self.assertIsNone(IA.core_hook("email me this file", {}, [], {}, None, G))
        self.assertIsNone(IA.core_hook("how many people watched Landman", {}, [], {}, None, G))
        for k in ('panel_confirm', 'bind_subject', 'mode', '_pm_order', '_meta_replay'):
            self.assertIsNone(IA.core_hook(HIS, {k: {'x': 1}}, [], {}, None, G), k)   # a step being answered is that step's
        self.assertEqual(calls, [])
        self.assertIsNone(IA.core_hook("top 10 creators Lego shoppers over-index on that we can email to Lego", {}, [], {}, None, G))
        self.assertEqual(calls, [])                                      # a put-together read keeps its quote

    def test_the_library_s_file_is_the_subject_the_words_name(self):
        for a, b in (('Lego', 'Lego'), ('lego', 'LEGO'), ('Lego', 'Lego Buyers'), ('Nike', 'Nike - Avid Fan'), ('Dr Pepper', 'Dr. Pepper'),
                     ('Taylor Swift', 'Taylor Swift Fans'), ('Landman', 'Landman Viewers'), ('Costco', 'Costco Members 2025')):
            self.assertTrue(IA._same_subject(a, b), (a, b))
        for a, b in (('Amazon', 'Amazon Prime Video'), ('Power', 'Power On Amazon Prime'), ('NCIS', 'NCIS: Sydney'), ('Nike', 'Lego'),
                     ('Law & Order', 'Law & Order: Special Victims Unit'), ('Lego', 'Legoland'), ('', 'Lego'), ('Lego', '')):
            self.assertFalse(IA._same_subject(a, b), (a, b))               # one name inside another is another subject

    def test_no_new_mail_no_price_and_no_request_field(self):
        src = _read('prometheus', 'insight_ask.py')
        for banned in ('send_email', 'send_raw_email', 'boto3', "client('ses'", '@crosswalknyc.com', 'request.json', 'request.get_json',
                       'consume_credit', 'offer_or_charge', 'outbound_mail', 'put_object'):
            self.assertNotIn(banned, src, banned)
        self.assertNotIn('\u2014', src.replace("'\\u2014'", '').replace('\\u2014', ''))
        self.assertIn("PM_INSIGHT_ASK", src)
        ana = _read('prometheus_analysis.py')
        self.assertEqual(ana.count('insight_txt = _ia.read_brief(text)'), 1)


# ============================================================================ 6. the door
_CHILD = r'''
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], 'scripts'))
import test_pm_insight_ask as T
SHELF = ['Lego', 'Nike', 'Landman', 'Taylor Swift', 'Peacock']
def _key(n):
    import re
    return re.sub(r'[^A-Za-z0-9]+', '_', n).strip('_') + '_10_20_2026_20_59.csv'
cat = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump({'jobs': [{'display_name': n, 's3_key': _key(n), 'category': 'GENERAL', 'status': 'cached', 'source': 's3'} for n in SHELF],
           'genpop_csv': ''}, cat)
cat.close()
os.environ['PM_REPLAY_CATALOG'] = cat.name
from prometheus import replay
replay.install_isolation()
h = replay.harness()
h.host.set('_neutralize_data_delimiters', lambda s, *a, **k: str(s or ''))
from prometheus.host import host as PH
for n in SHELF:
    h.s3.put_object(Bucket='dashboard-inputs', Key=_key(n), Body=T.profile_csv(n).encode('utf-8'))
replay._STATE['baseline'] = dict(h.s3.objects)
MODEL = {
    'good': {'action': 'answer', 'reply': (
        "23.8% of the Lego audience bought from Hobby Lobby in the 12 months to October 20, 2026, against 5.5% of US people, 4.4 times the US rate.\n\n"
        "That leans toward a maker audience Lego could reach in the craft aisle.\n\n"
        "- 19.3% bought Crayola, against 2.3% of US people.\n\n"
        "Forward-ready version:\nYour audience bought from Hobby Lobby at 4.4 times the US rate over the 12 months to October 20, 2026.\n\n"
        "I can turn this into a one-pager."), 'followups': ['Turn this into a one-pager', 'Where else do they shop?']},
    'bare': {'action': 'answer', 'reply': '23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people.', 'followups': []},
    'sent': {'action': 'answer', 'reply': ("23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people. "
                                           "I have emailed this to Lego for you."), 'followups': ['Email this to Lego']},
    'empty': {},
}
out = []
for it in json.load(sys.stdin):
    replay._restore_baseline(h)
    replay._STATE['question'] = it['question']
    page = it.get('page') or ''
    replay._canned_dual(h, page or None, None)
    canned = replay._canned_for('analyze', page or 'This audience', None)
    calls = []
    def _json(system, user, *a, _c=canned, _m=it.get('model'), **k):
        blob = str(system) + str(user)
        ins = 'INSIGHT REQUEST' in str(user)
        calls.append({'fn': 'json', 'surface': k.get('surface'), 'insight': ins, 'findings': 'MEASURED FINDINGS FROM THE FULL FILE' in blob,
                      'hobby': 'Hobby Lobby | 23.8% | 5.5%' in blob, 'window': 'the 12 months to October 20, 2026' in blob,
                      'for': (blob.split('put in front of ')[1].split('.')[0] if 'put in front of ' in blob else ''),
                      'subject': (blob.split('MEASURED FINDINGS FROM THE FULL FILE: ')[1].split(',')[0] if 'MEASURED FINDINGS FROM THE FULL FILE: ' in blob else '')})
        if ins and _m:
            return {'success': bool(MODEL[_m]), 'data': json.loads(json.dumps(MODEL[_m])), 'model': 'replay'}
        return {'success': True, 'data': json.loads(json.dumps(_c)), 'model': 'replay'}
    h.patch('_pm_claude_json', _json)
    def _data(system, user, _r=it.get('reading'), _c=canned, **k):
        calls.append({'fn': 'data', 'surface': k.get('surface')})
        if k.get('surface') == 'reader':
            return dict(_r) if _r else {}
        return dict(_c)
    PH.bind(claude_data=_data, can_open_profile=lambda u, k: True)
    pc = {}
    if page:
        pc = {'primary': {'s3_key': _key(page), 'name': page}, 'view_context': {'view_id': 'profileIQ', 'view_title': page, 'data': {}}}
    legacy = bool(it.get('legacy'))
    path = '/api/brief-chat/analyze' if legacy else (replay._STATE.get('door') or '/api/brief-chat/analyze')
    body = {'text': it['question'], 'history': it.get('history') or [], 'page_context': pc}
    if not legacy:
        body.update({'client': 'dashboard', 'persist': False, 'surface': it.get('surface') or 'analyze'})
    n0 = len(replay._STATE['captured'])
    status, payload = h.post(path, body, session={'username': replay.REPLAY_USER['username']})
    rec = (replay._STATE['captured'][n0:] or [{}])[-1]
    raw = payload.get('raw') if isinstance(payload, dict) and isinstance(payload.get('raw'), dict) else payload
    raw = raw if isinstance(raw, dict) else {}
    out.append({'route': rec.get('route'), 'outcome': rec.get('outcome'), 'subject': rec.get('subject'),
                'reply': replay._reply_text(raw) or str((payload or {}).get('text') or ''),
                'offer': bool(raw.get('panel_offer')), 'draft': isinstance(raw.get('spec_draft'), dict),
                'jobs': [j for j in [raw.get('read_job_id'), raw.get('run_id')] if j],
                'chips': [str(c) for c in (raw.get('followups') or [])], 'calls': calls, 'insight': raw.get('insight_ask'),
                'reason': ((payload or {}).get('decision') or {}).get('reason') if isinstance(payload, dict) else None,
                'writes': sorted(k for k in h.s3.objects if k not in replay._STATE['baseline'])})
os.unlink(cat.name)
print('@@@' + json.dumps(out))
'''

READ_LANDED = [
    {'role': 'user', 'text': 'what do Lego shoppers buy'},
    {'role': 'agent', 'text': 'On Lego: 23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people.',
     'meta': {'subject': 'Lego'}}]

DOOR_ITEMS = (
    # 0-3: his question as typed: nothing open, Lego open, another profile open, the older route
    [dict(question=HIS, model='good'), dict(question=HIS, page='Lego', model='good'), dict(question=HIS, page='Nike', model='good'),
     dict(question=HIS, legacy=True, model='good')]
    # 4-5: the pass gives a bare line, or nothing: the same hand-over, written from the rows
    + [dict(question=HIS, model='bare'), dict(question=HIS, model='empty')]
    # 6: the pass says it emailed Lego: it did not, and the reply never says so
    + [dict(question=HIS, model='sent')]
    # 7-14: the family on subjects the library holds (brands, a title, talent, the screen)
    + [dict(question="what would impress Lego", model='good'),
       dict(question="find me something we can pitch Nike", model='good'),
       dict(question="what's a cool insight on Landman viewers we can send to Paramount", model='good'),
       dict(question="give me a surprising stat about Taylor Swift fans we can share with the Nike team", model='good'),
       dict(question="got an interesting nugget about peacock subscribers for the pitch to NBC", model='good'),
       dict(question="what stands out about Lego's shoppers this month", page='Nike', model='good'),
       dict(question="what's a cool insight here we can send to the client", page='Landman', model='good'),
       dict(question="can you email Lego a unique insight about their shoppers", model='good')]
    # 15-17: a subject the library does not hold: the standing quote, unchanged
    + [dict(question="give me a hook for Dr Pepper"),
       dict(question="what's a unique insight we can email to Dr Pepper about their shoppers this month"),
       dict(question="what would impress Dr Pepper", page='Lego')]
    # 18: a question the price rules quote keeps its quote
    + [dict(question="top 10 creators Lego shoppers over-index on that we can email to Lego")]
    # 19-24: the send-a-file cases, nothing landed and after a read
    + [dict(question="email me this file"), dict(question="email me this file", legacy=True),
       dict(question="email this to me", history=READ_LANDED), dict(question="email me this file", history=READ_LANDED, legacy=True),
       dict(question="send me that deck"), dict(question="email me this file", page='Lego')]
    # 25-26: questions next to the family that keep their own lanes
    + [dict(question="what stands out about this audience", page='Lego'), dict(question="how many people watched Landman in 2024")]
    # 27-28: the words name who it is for and no subject: the thread's subject, never the page; with no one named, the standing read
    + [dict(question="what would impress them", history=READ_LANDED, page='Nike', model='good'),
       dict(question="what are the craziest insights from the consumer's of Brock's channel?", page='Lego', model='good')]
)


def _door(items):
    env = dict(os.environ, REGRESSION_TEST_MODE='1')
    try:
        p = subprocess.run([sys.executable, '-c', _CHILD, ROOT], input=json.dumps(items), capture_output=True, text=True,
                           env=env, timeout=900)
    except Exception:
        return None
    i = p.stdout.rfind('@@@')
    if i < 0:
        if os.environ.get('PM_DOOR_DEBUG') == '1':
            sys.stderr.write((p.stdout or '')[-3000:] + '\n' + (p.stderr or '')[-6000:])
        return None
    try:
        return json.loads(p.stdout[i + 3:])
    except Exception:
        return None


class ThroughTheDoor(unittest.TestCase):
    """The words through the door as the dashboard sends them."""

    @classmethod
    def setUpClass(cls):
        cls.res = _door(list(DOOR_ITEMS))

    def setUp(self):
        if not self.res:
            if os.environ.get('PM_DOOR_REQUIRED') == '1':
                self.fail('the door checks did not run: the recording host is required here')
            self.skipTest('the recording host (scripts/_pm_behavior.py in the parent repo) is not on the path here')
        self.assertEqual(len(self.res), len(DOOR_ITEMS))

    def _is_the_hand_over(self, r, subject, who=None):
        self.assertEqual((r['route'], r['outcome']), ('insight_ask', 'answered'), r['reply'][:200])
        self.assertEqual(r['subject'], subject)
        self.assertEqual(r['jobs'], [])                                  # nothing started
        self.assertFalse(r['offer'])                                     # nothing quoted
        self.assertFalse(r['draft'])                                     # nothing drafted
        self.assertNotIn('Nothing has landed in this chat', r['reply'])
        self.assertIn(IA.FORWARD_LABEL, r['reply'])
        self.assertRegex(r['reply'], r"one-?pager")
        self.assertEqual(r['chips'][0], IA.ONE_PAGER_CHIP)
        self.assertIsNone(BANNED_WORDS_RX.search(r['reply']), BANNED_WORDS_RX.search(r['reply']))
        ins = [c for c in r['calls'] if c.get('insight')]
        self.assertEqual(len(ins), 1, r['calls'])                        # one pass, on the whole file
        self.assertTrue(ins[0]['findings'] and ins[0]['hobby'] and ins[0]['window'], ins[0])
        self.assertEqual(ins[0]['subject'], subject)
        if who:
            self.assertEqual(ins[0]['for'], who)
        self.assertEqual((r['insight'] or {}).get('subject'), subject)

    def test_his_question_as_typed(self):
        for r in self.res[0:4]:
            self._is_the_hand_over(r, 'Lego', 'Lego')
            self.assertTrue(r['reply'].startswith('23.8% of the Lego audience bought from Hobby Lobby'), r['reply'][:120])
            self.assertEqual((r['insight'] or {}).get('asked_window'), 'this month')
            self.assertEqual((r['insight'] or {}).get('window'), 'the 12 months to October 20, 2026')
        self.assertEqual(self.res[0]['reason'], 'insight_ask')
        self.assertEqual(self.res[2]['subject'], 'Lego')                 # the profile open on the screen was Nike

    def test_a_pass_that_gives_too_little_still_hands_it_over(self):
        bare, empty = self.res[4], self.res[5]
        self._is_the_hand_over(bare, 'Lego', 'Lego')
        self.assertTrue(bare['reply'].startswith('23.8% of the Lego audience bought from Hobby Lobby, against 5.5% of US people.'))
        self.assertIn(IA.ONE_PAGER_LINE, bare['reply'])
        self._is_the_hand_over(empty, 'Lego', 'Lego')
        self.assertRegex(empty['reply'].split('\n')[0], r"^In our profile of the Lego audience, \d+\.\d% bought .+, against \d+\.\d% of US people, \d+\.\d times the US rate\.$")
        self.assertIn('the 12 months to October 20, 2026', empty['reply'])

    def test_it_never_says_it_was_sent(self):
        r = self.res[6]
        self._is_the_hand_over(r, 'Lego', 'Lego')
        self.assertNotIn('emailed this to Lego', r['reply'])
        self.assertFalse([c for c in r['chips'] if re.search(r"to Lego", c)], r['chips'])

    def test_the_family_on_what_the_library_holds(self):
        want = (('Lego', 'Lego'), ('Nike', 'Nike'), ('Landman', 'Paramount'), ('Taylor Swift', 'Nike'), ('Peacock', 'NBC'),
                ('Lego', 'Lego'), ('Landman', 'the client'), ('Lego', 'Lego'))
        for it, r, (subject, who) in zip(DOOR_ITEMS[7:15], self.res[7:15], want):
            with self.subTest(q=it['question']):
                self._is_the_hand_over(r, subject, who)

    def test_a_subject_the_library_does_not_hold_keeps_the_standing_quote(self):
        for it, r in zip(DOOR_ITEMS[15:18], self.res[15:18]):
            with self.subTest(q=it['question']):
                self.assertNotEqual(r['route'], 'insight_ask')
                self.assertNotEqual(r['route'], 'email_file')
                self.assertNotEqual(r['route'], 'cant_do')
                self.assertTrue(r['offer'], r['reply'][:200])
                self.assertEqual(r['reply'], 'I can pull a read on Dr Pepper and hand you the strongest finding in a form you can '
                                             'forward. Pulling this data will cost $100.')
                self.assertEqual(r['chips'][:2], ['Approve', 'Cancel'])
                self.assertEqual(r['jobs'], [])
                self.assertFalse([c for c in r['calls'] if c.get('insight')])   # no pass ran before the quote

    def test_every_other_quote_reads_as_it_always_did(self):
        self.assertEqual(IA.offer_lead("how many people watched Landman in 2024", 'Landman'), '')
        self.assertEqual(IA.offer_lead("what do Dr Pepper drinkers buy", 'Dr Pepper'), '')
        self.assertEqual(IA.offer_lead("give me a hook for Dr Pepper", ''), '')
        self.assertIn('Landman', self.res[26]['reply'])
        self.assertNotIn('strongest finding', self.res[26]['reply'])
        self.assertNotIn('strongest finding', self.res[18]['reply'])

    def test_a_put_together_read_keeps_its_quote(self):
        r = self.res[18]
        self.assertNotEqual(r['route'], 'insight_ask')
        self.assertNotEqual(r['route'], 'email_file')
        self.assertTrue(r['offer'], r['reply'][:200])
        self.assertIn('Pulling this data will cost', r['reply'])

    def test_the_send_a_file_cases_are_unchanged(self):
        nothing, nothing_core, after, after_core, deck, page = self.res[19:25]
        self.assertEqual(nothing['route'], 'email_file')
        self.assertTrue(nothing['reply'].startswith('Which file?'), nothing['reply'][:120])
        self.assertEqual((nothing_core['route'], nothing_core['outcome']), ('email_file', 'no_file'))
        self.assertEqual(nothing_core['reply'], WHAT_HE_READ)            # the line still answers a real send with nothing landed
        for r in (after, after_core, page):
            self.assertEqual(r['route'], 'email_file', r['reply'][:160])  # the send lane took it (the mail door is closed in a test)
            self.assertNotIn('Forward-ready version', r['reply'])
        self.assertEqual(deck['route'], 'deliverable_lookup')
        for r in self.res[19:25]:
            self.assertFalse([c for c in r['calls'] if c.get('insight')])

    def test_the_questions_next_to_the_family_keep_their_lanes(self):
        for r in self.res[25:27]:
            self.assertNotEqual(r['route'], 'insight_ask', r['reply'][:160])
            self.assertFalse([c for c in r['calls'] if c.get('insight')])

    def test_the_thread_s_subject_when_the_words_say_only_who_it_is_for(self):
        self._is_the_hand_over(self.res[27], 'Lego', 'them')            # the thread was on Lego; Nike was only open on the screen
        r = self.res[28]
        self.assertNotEqual(r['route'], 'insight_ask', r['reply'][:160])
        self.assertFalse([c for c in r['calls'] if c.get('insight')])

    def test_nothing_is_sent_charged_or_left_behind(self):
        for it, r in zip(DOOR_ITEMS[:15], self.res[:15]):
            self.assertFalse([k for k in r['writes'] if 'generated_data/' in k or 'outbound' in k or 'pm_read' in k],
                             (it['question'], r['writes']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
