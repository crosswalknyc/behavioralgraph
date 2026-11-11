#!/usr/bin/env python3
"""A people count has a ceiling (2026-11-11, the Chime Banking Customers insurance read).

A category answer on the open Chime Banking Customers profile read: "On a
projected base of 8,977,404 people that is about 392,312,555
insurance-active people." The right figure is 3,925,084. The answer had
written the count as arithmetic on two figures it was handed,
[calc: f8*f4 | count] with f8 the audience and f4 the 43.7% share, and
the percent was applied as a whole number. Grace Yin's "31.4% of the
file, 42,957,147 people" on a 1,368,062-person file (2026-11-08) was the
same sum. This suite holds:

  * the cause (prometheus.figures): a percent token is a percent in every
    sum, however the expression is spelled; a /100 the answer wrote itself
    is never applied twice; sums with no percent token are untouched;
  * the category reach block hands the answer the people behind the share,
    and it is the count a cross of behaviors prints for the same category
    (migration.behavior_crosstab.single), so nobody is left to multiply;
  * the composed Chime answer prints 3,925,084 whichever way the answer
    wrote the count;
  * the net (prometheus.people_bounds): a count of US people never passes
    329,900,000; a count stated as part of an audience never passes it; a
    count that sits beside the figure the answer carries is that figure.
    Corrected in place, or the sentence dropped; nothing is held;
  * sentences that are fine are left alone (a world figure, a US total, a
    count of views, dollars, a platform's own size, an ordinary read);
  * the exits: the envelope every chat reply passes, a finished read as it
    lands, the vocabulary pass every deck string and chat reply goes
    through.

Hermetic: no model, no S3. Run:
    REGRESSION_TEST_MODE=1 python3 scripts/test_pm_people_bounds.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from prometheus import figures as F  # noqa: E402
from prometheus import people_bounds as PB  # noqa: E402
from prometheus import category_reach as CRE  # noqa: E402
from migration import behavior_crosstab as BX  # noqa: E402

AUDIENCE = 8_977_404
SAMPLE = 272_125
WRONG = 392_312_555
RIGHT = 3_925_084
KEY = 'Chime_Banking_Customers_05_12_2026_15_12.csv'

# the reply as prod printed it (2026-11-11, about 12:43 PT)
CHIME_REPLY = (
    "Insurance is a flat category for this audience: 43.7% engage with at least one insurance brand, index 101, "
    "in line with the US average (US 43.4%). On a projected base of 8,977,404 people that is about 392,312,555 "
    "insurance-active people.\n\n"
    "The weight sits in direct-response auto carriers, not in health or membership brands. GEICO leads at 26.7%, "
    "with Progressive and Allstate close behind.")
# the evidence the answer was written from, as the page read builds it
DIGEST = ("PROFILE DIGEST\n  panel sample 272,125 (internal, never stated to the reader); projected US audience 8,977,404; "
          "window 2025-01-01 to 2025-12-31")
REACH_BLOCK = ("CATEGORY REACH (measured on this profile; quote exactly)\n"
               "  insurance: 43.7% of this audience engage with at least one insurance brand; projected US people 3,925,084 "
               "(quote verbatim); US 43.4%; index 101 (in line with the US average). Largest single brand: GEICO 26.7%. "
               "35 brands counted.")
ROW_BLOCK = ("EXACT ROWS FOR ENTITIES THIS QUESTION NAMES\n"
             "- CHIME | DIGITAL BANKING: 11.7224% of this audience | projected US people 1,052,381 (quote verbatim) | "
             "gen pop 3.0250% | 3.9x (index 388)")
FACTS = [{'label': 'insurance', 'share': 43.7, 'people': RIGHT, 'base': AUDIENCE}]
READER_BANNED = re.compile(r"[\u2014\u2013]|\b(?:synth\w*|modeled|estimat\w*|calc|token|registry|clamp|guard)\b", re.I)


def _counts_in(text):
    return [int(x.replace(',', '')) for x in re.findall(r"(?<![\d,.$])\d{1,3}(?:,\d{3})+", text)]


def _profile_df():
    """A small profile with the Chime audience's own size and an INSURANCE column."""
    rows = [('BRAND INPUT', 'Chime', 100.0, SAMPLE, AUDIENCE), ('SAMPLE SIZE', 'SAMPLE SIZE (2025-01-01 TO 2025-12-31)', 100.0, SAMPLE, AUDIENCE),
            ('SUBJECT', 'Chime Banking Customers', 100.0, SAMPLE, AUDIENCE)]
    ins = (('GEICO', 26.7012), ('Progressive', 14.2093), ('Allstate', 9.8127), ('State Farm', 8.9034), ('Liberty Mutual', 4.1178),
           ('Farmers', 3.2291), ('Blue Cross Blue Shield', 2.0419), ('United Healthcare', 1.7733))
    shop = (('Walmart', 62.3381), ('Target', 41.0926), ('Costco', 22.6614))
    for col, items in (('INSURANCE', ins), ('WHERE THEY SHOP', shop)):
        for b, p in items:
            raw = int(round(p / 100.0 * SAMPLE))
            rows.append((col, b, p, raw, int(round(raw / 10_000_000 * 329_900_000))))
    df = pd.DataFrame(rows, columns=['Column', 'Value', 'Brand Penetration (Row)', 'Raw', 'Projection'])
    df.attrs['s3_key'] = KEY
    gp = {('INSURANCE', b.upper()): v for b, v in (('GEICO', 22.1), ('Progressive', 13.9), ('Allstate', 9.1), ('State Farm', 12.2),
                                                    ('Liberty Mutual', 4.4), ('Farmers', 3.6), ('Blue Cross Blue Shield', 6.2),
                                                    ('United Healthcare', 5.1))}
    gp.update({('WHERE THEY SHOP', b.upper()): v for b, v in (('Walmart', 62.3), ('Target', 44.0), ('Costco', 27.5))})
    return df, gp


class TheCause(unittest.TestCase):
    """prometheus.figures: a percent token is a percent."""

    def setUp(self):
        self.reg = F.Registry()
        self.base = self.reg.add(AUDIENCE, 'projected audience', 'profile_file')
        self.pct = self.reg.add(43.7, 'insurance', 'profile_file', kind='pct')
        self.pct2 = self.reg.add(26.7, 'GEICO', 'profile_file', kind='pct')
        self.part = self.reg.add(RIGHT, 'insurance people', 'profile_file')

    def test_the_chime_sum_as_the_answer_wrote_it(self):
        out = self.reg.render(f"On this audience's projected size of [{self.base}], that reach is roughly "
                              f"[calc: {self.base}*{self.pct} | count] people.")
        self.assertNotIn('392,312,555', out)
        self.assertIn('3,923,126 people', out)
        self.assertTrue(all(n <= AUDIENCE for n in _counts_in(out)), out)

    def test_grace_yins_sum(self):
        reg = F.Registry()
        b = reg.add(1_368_062, 'the file', 'profile_file')
        p = reg.add(31.4, 'streaming', 'profile_file', kind='pct')
        out = reg.render(f"31.4% of the file, [calc: {b}*{p} | count] people")
        self.assertIn('429,571 people', out)
        self.assertNotIn('42,957,147', out)

    def test_every_spelling_of_a_share_of_a_count_agrees(self):
        b, p = self.base, self.pct
        want = AUDIENCE * 43.7 / 100.0
        for expr in (f"{b}*{p}", f"{p}*{b}", f"{b}*{p}/100", f"{b}/100*{p}", f"{p}/100*{b}", f"{b}*({p}/100)",
                     f"{b}*{p}*0.01", f"0.01*{p}*{b}"):
            self.assertAlmostEqual(self.reg._calc(expr, 'count'), want, places=3, msg=expr)

    def test_the_rest_of_a_count_and_a_share_of_a_share(self):
        b, p, p2 = self.base, self.pct, self.pct2
        rest = AUDIENCE * (100 - 43.7) / 100.0
        for expr in (f"{b}*(100-{p})", f"{b}*(100-{p})/100", f"{b}*(1-{p}/100)", f"{b}-{b}*{p}", f"{b}-{b}*{p}/100"):
            self.assertAlmostEqual(self.reg._calc(expr, 'count'), rest, places=3, msg=expr)
        self.assertAlmostEqual(self.reg._calc(f"{p}*{p2}", 'pct'), 43.7 * 26.7 / 100.0, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{p}*{p2}/100", 'pct'), 43.7 * 26.7 / 100.0, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{b}*{p}*{p2}", 'count'), AUDIENCE * 0.437 * 0.267, places=3)

    def test_the_base_a_share_sits_on(self):
        c, p = self.part, self.pct
        want = RIGHT / 0.437
        for expr in (f"{c}/{p}", f"{c}/{p}*100", f"100*{c}/{p}", f"{c}/({p}/100)"):
            self.assertAlmostEqual(self.reg._calc(expr, 'count'), want, places=3, msg=expr)

    def test_sums_with_no_percent_token_are_untouched(self):
        b, c, p, p2 = self.base, self.part, self.pct, self.pct2
        self.assertAlmostEqual(self.reg._calc(f"{c}/{b}*100", 'pct'), RIGHT / AUDIENCE * 100, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{b}-{c}", 'count'), AUDIENCE - RIGHT, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{p}/{p2}*100", 'count'), 43.7 / 26.7 * 100, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{p}-{p2}", 'pct'), 17.0, places=6)
        self.assertAlmostEqual(self.reg._calc(f"100-{p}", 'pct'), 56.3, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{b}/100", 'count'), AUDIENCE / 100.0, places=6)
        self.assertAlmostEqual(self.reg._calc(f"{b}*100", 'count'), AUDIENCE * 100.0, places=3)
        self.assertAlmostEqual(self.reg._calc(f"{b}*2", 'count'), AUDIENCE * 2.0, places=3)
        self.assertAlmostEqual(self.reg._calc(f"{c}/{b}", 'x'), RIGHT / AUDIENCE, places=9)

    def test_an_index_the_answer_works_out_prints_as_an_index(self):
        us = self.reg.add(43.4, 'US', 'profile_file', kind='pct')
        out = self.reg.render(f"against [{us}] for the US, index [calc: {self.pct}/{us}*100 | index] (in line with the US average).")
        self.assertEqual(out, "against 43.4% for the US, index 101 (in line with the US average).")
        self.assertNotIn('that figure', out)

    def test_the_rule_tells_the_answer_how_a_share_of_a_count_is_written(self):
        self.assertIn('/100', F.PROMPT_RULE)
        self.assertIn('quote that token', F.PROMPT_RULE)


class TheComposedAnswer(unittest.TestCase):
    """figures.compose on the Chime evidence: the words print the figure the evidence carries."""

    def _reg(self):
        return F.registry_for(ROW_BLOCK + '\n\n' + REACH_BLOCK, DIGEST)

    def _id(self, reg, value):
        return next(f['id'] for f in reg.figures if abs(f['value'] - value) < 1e-6)

    def test_the_block_figures_are_in_the_table(self):
        reg = self._reg()
        vals = {round(f['value'], 4) for f in reg.figures}
        self.assertIn(float(RIGHT), vals)
        self.assertIn(float(AUDIENCE), vals)
        self.assertIn(43.7, vals)

    def test_the_answer_that_multiplies_prints_the_carried_figure(self):
        reg = self._reg()
        b, p = self._id(reg, AUDIENCE), self._id(reg, 43.7)
        raw = (f"[{p}] of Chime Banking Customers engage with at least one insurance brand, index 101. "
               f"On this audience's projected size of [{b}], that reach is roughly [calc: {b}*{p} | count] people. "
               "GEICO is the largest single brand.")
        out, led = F.compose(raw, reg, ROW_BLOCK + '\n\n' + REACH_BLOCK)
        self.assertIn('that reach is roughly 3,925,084 people', out)
        self.assertNotIn('392,312,555', out)
        self.assertNotIn('3,923,126', out)
        self.assertTrue(any(abs(f['value'] - RIGHT) < 1e-6 for f in led), led)

    def test_the_answer_that_quotes_the_token_is_untouched(self):
        reg = self._reg()
        n = self._id(reg, RIGHT)
        out, _ = F.compose(f"43.7% engage with at least one insurance brand, [{n}] people.", reg, REACH_BLOCK)
        self.assertEqual(out, "43.7% engage with at least one insurance brand, 3,925,084 people.")

    def test_a_typed_wrong_count_is_corrected_too(self):
        reg = self._reg()
        out, _ = F.compose(CHIME_REPLY, reg, ROW_BLOCK + '\n\n' + REACH_BLOCK)
        self.assertIn('that is about 3,925,084 insurance-active people', out)
        self.assertNotIn('392,312,555', out)


class TheReachBlock(unittest.TestCase):
    """The category reach block hands over the people, and they are the cross's own."""

    def test_the_line_carries_the_people_and_they_match_the_cross(self):
        df, gp = _profile_df()
        block = CRE.block(df, gp, 'What share of Chime Banking Customers engage with insurance brands overall, and what is the index?')
        m = re.search(r"insurance: (\d+\.\d)% of [^;]+; projected US people ([\d,]+) \(quote verbatim\)", block)
        self.assertIsNotNone(m, block)
        people = int(m.group(2).replace(',', ''))
        ctx = BX.profile_context(df, gp)
        cat = BX.category_condition(df, ctx, 'INSURANCE')
        one = BX.single(cat, BX.sample_of(df), salt=KEY)
        self.assertEqual(people, one['us'])
        self.assertLess(people, AUDIENCE)
        self.assertNotEqual(people % 10, 0)
        self.assertAlmostEqual(people / AUDIENCE * 100.0, float(m.group(1)), delta=0.1)
        # the same category inside a cross of two behaviors prints the same people
        tgt = BX.bind(df, 'Target', context=ctx)['condition']
        res = BX.cross([cat, tgt], BX.sample_of(df), salt=KEY)
        in_cross = next(c for c in res['conditions'] if c['key'] == cat['key'])
        self.assertEqual(in_cross['us'], people)
        self.assertEqual(in_cross['raw'], one['raw'])
        self.assertIn('never multiply a share by the audience yourself', block)

    def test_no_profile_key_no_people(self):
        df, gp = _profile_df()
        df.attrs.pop('s3_key', None)
        block = CRE.block(df, gp, 'What share of this audience engage with insurance brands overall?')
        self.assertIn('insurance:', block)
        self.assertNotIn('projected US people', block.split('\n')[-1])

    def test_facts_are_read_off_the_block(self):
        df, gp = _profile_df()
        block = CRE.block(df, gp, 'What share of this audience engage with insurance brands overall?')
        facts, aud = PB.facts_from_blocks([DIGEST, ROW_BLOCK, block])
        self.assertEqual(aud, AUDIENCE)
        labels = {f['label'].lower(): f for f in facts}
        self.assertIn('insurance', labels)
        self.assertIn('chime', labels)
        self.assertEqual(labels['chime']['people'], 1_052_381)
        self.assertEqual(labels['insurance']['base'], AUDIENCE)


class TheNet(unittest.TestCase):
    """prometheus.people_bounds: the three rules, in place."""

    def test_the_chime_reply_with_the_figure_in_hand(self):
        info = PB.check(CHIME_REPLY, audience=AUDIENCE, facts=FACTS)
        self.assertTrue(info['changed'])
        self.assertIn('On a projected base of 8,977,404 people that is about 3,925,084 insurance-active people.', info['text'])
        self.assertNotIn('392,312,555', info['text'])
        self.assertIn('GEICO leads at 26.7%', info['text'])
        self.assertEqual(info['fixes'][0]['was'], WRONG)
        self.assertEqual(info['fixes'][0]['now'], RIGHT)
        self.assertEqual(info['dropped'], [])

    def test_the_chime_reply_with_no_figure_in_hand_is_the_sum_done_right(self):
        info = PB.check(CHIME_REPLY)
        self.assertIn('that is about 3,923,126 insurance-active people', info['text'])
        self.assertTrue(all(n <= AUDIENCE for n in _counts_in(info['text'])), info['text'])

    def test_over_the_us_population_with_nothing_to_fall_back_on_drops_the_sentence(self):
        t = "The show reached 412,555,121 viewers in the window. It skews young and urban."
        info = PB.check(t)
        self.assertEqual(info['text'], "It skews young and urban.")
        self.assertEqual(len(info['dropped']), 1)
        t2 = "Reach is wide. That is roughly 392.3 million people on this audience. GEICO leads."
        self.assertEqual(PB.check(t2)['text'], "Reach is wide. GEICO leads.")

    def test_part_larger_than_whole(self):
        # under the US population, over its own base: 'a base of M that is N'
        t = CHIME_REPLY.replace('392,312,555', '39,231,255')
        out = PB.check(t)['text']
        self.assertNotIn('39,231,255', out)
        self.assertIn('43.7% engage', out)
        self.assertIn('GEICO leads', out)
        # 'N of the M'
        out = PB.check("Among them, 9,112,443 of the 8,977,404 hold a policy. The rest do not.")['text']
        self.assertEqual(out, "The rest do not.")
        # 'Of the M viewers, N ...'
        out = PB.check("Of the 1,113,363 people new to the show, 6,048,941 people came for her. She leads.")['text']
        self.assertEqual(out, "She leads.")
        # Grace Yin's sentence: the share of the file with its count, base named after
        out = PB.check("That is 31.4% of the file, 42,957,147 people, on a base of 1,368,062 people. More follows.")['text']
        self.assertIn('31.4% of the file, 429,571 people, on a base of 1,368,062 people.', out)
        # the open audience, when the sentence says the count sits inside it
        out = PB.check("Streaming leads at 31.4% of the file, 42,957,147 people. More follows.", audience=1_368_062)['text']
        self.assertIn('31.4% of the file, 429,571 people.', out)

    def test_the_words_print_the_figure_the_answer_carries(self):
        # a second sum that lands near the carried figure is the carried figure
        near = CHIME_REPLY.replace('392,312,555', '3,923,126')
        info = PB.check(near, audience=AUDIENCE, facts=FACTS)
        self.assertIn('that is about 3,925,084 insurance-active people', info['text'])
        self.assertEqual(info['fixes'][0]['why'], 'not the figure the answer carries')
        # a profile row
        row = [{'label': 'CHIME', 'share': 11.7224, 'people': 1_052_381, 'base': AUDIENCE}]
        out = PB.check("Chime itself sits at 11.7% of this audience, 1,050,356 people.", audience=AUDIENCE, facts=row)['text']
        self.assertEqual(out, "Chime itself sits at 11.7% of this audience, 1,052,381 people.")
        # two figures at one share: the one the sentence names
        two = row + [{'label': 'GEICO', 'share': 11.7, 'people': 1_050_401, 'base': AUDIENCE}]
        out = PB.check("Geico sits at 11.7%, 1,050,356 people.", audience=AUDIENCE, facts=two)['text']
        self.assertEqual(out, "Geico sits at 11.7%, 1,050,401 people.")
        # the carried figure itself, and a figure for something else, are left alone
        ok = "43.7% engage with at least one insurance brand, 3,925,084 people. GEICO alone reaches 2,397,005 people (26.7%)."
        self.assertFalse(PB.check(ok, audience=AUDIENCE, facts=FACTS)['changed'])

    def test_a_reply_that_is_one_sentence_is_never_emptied(self):
        info = PB.check("Insurance reaches 43.7% (392,312,555 people).")
        self.assertEqual(info['text'], "Insurance reaches 43.7%.")
        info = PB.check("Insurance reaches 43.7% of this audience, or about 392,312,555 people.")
        self.assertEqual(info['text'], "Insurance reaches 43.7% of this audience.")
        info = PB.check("The show reached 412,555,121 viewers in the window.")
        self.assertFalse(info['changed'])
        self.assertEqual(len(info['unresolved']), 1)
        self.assertTrue(info['text'].strip())

    def test_sentences_that_are_fine_are_left_alone(self):
        for t in (
            "YouTube has 2,500,000,000 users worldwide. Here it reaches 61.2%.",
            "MrBeast has 430,000,000 subscribers. Here he reaches 61.2%.",
            "Of the 8,977,404 people, 3,925,084 people engage with insurance (43.7%). Against 143,176,601 US adults nationally.",
            "Target and Technology on Chime is 77.2%, 6,934,955 US people, index 464.",
            "Among the 1,052,381 Chime users, most also sit inside the 8,977,404 people in this audience.",
            "Of the 1,113,363 people new to NCIS at the premiere, 604,894 came in because of her. LL Cool J, with the bigger "
            "audience of 2,113,555 viewers, brought 195,463.",
            "The trailer drew 912,333,121 views from viewers. Next.",
            "Revenue of $512,333,121 from subscribers.",
            "6,864,526 US people in this audience shop at Target and are interested in technology. That is 76.5% of the audience.",
            "Window: Nov 11, 2025 to Nov 11, 2026. Index 5,548.",
        ):
            info = PB.check(t, audience=AUDIENCE, facts=FACTS)
            self.assertFalse(info['changed'], (t, PB.trail(info)))
            self.assertEqual(info['text'], t)

    def test_a_read_whose_measure_is_not_people_is_never_cut(self):
        # Cara Dorfman's hours by month (2026-11-10): the row pools are hours under a people noun
        read = ("On YouTube and Amazon Prime Video - P18+, split M18-34, F18-34, M35+, F35+:\n\n"
                "US adults spent 64,638,049,641 hours on YouTube in 2025 against 15,364,120,272 hours on Prime Video, a 4.2x gap.\n\n"
                "Month x platform mix, Jan 1 2025 to Dec 31 2025 (share of 2025 adult viewing hours across both platforms)\n"
                "- December 2025 - YouTube: 7.4% share. Pool 5,885,924,493 viewers. M18-34: 1,359,666,001 (23.1%) | F18-34: 1,141,884,004 (19.4%)\n"
                "- January 2025 - YouTube: 7.2% share. Pool 5,723,999,128 viewers. M18-34: 1,322,244,006 (23.1%) | F18-34: 1,110,456,003 (19.4%)\n"
                "- November 2025 - YouTube: 7.0% share. Pool 5,561,977,561 viewers. M18-34: 1,284,822,006 (23.1%) | F18-34: 1,079,028,002 (19.4%)")
        info = PB.check(read)
        self.assertFalse(info['changed'], PB.trail(info))
        self.assertEqual(info['text'], read)
        self.assertEqual(info['dropped'], [])
        # one row of the same table: still a volume, still untouched
        one = '\n'.join(read.split('\n')[:5])
        self.assertFalse(PB.check(one)['changed'])
        import prometheus_analysis as pma
        self.assertIn('Pool 5,885,924,493 viewers.', pma.scrub_user_text(read))
        # and an answer never loses more than two sentences
        many = ("Of the 1,000,001 people, 2,000,003 people did A. Of the 1,000,001 people, 3,000,007 people did B. "
                "Of the 1,000,001 people, 4,000,009 people did C. The rest is fine.")
        out = PB.check(many)
        self.assertEqual(len(out['dropped']), PB.MAX_DROPS)
        self.assertIn('4,000,009 people did C.', out['text'])
        self.assertIn('The rest is fine.', out['text'])

    def test_never_raises_and_never_returns_nothing(self):
        for t in (None, '', 12, 'no figures here', '392,312,555', '(((392,312,555 people'):
            info = PB.check(t)
            self.assertIs(info['text'], t)
        self.assertEqual(PB.clean(None), None)
        self.assertEqual(PB.guard_payload(None), None)

    def test_what_a_reader_sees_carries_no_internal_word_and_no_dash(self):
        for facts in (FACTS, ()):
            out = PB.check(CHIME_REPLY, audience=AUDIENCE, facts=facts)['text']
            self.assertIsNone(READER_BANNED.search(out), out)


class TheExits(unittest.TestCase):
    def test_the_envelope_every_chat_reply_passes(self):
        from prometheus import envelope
        raw = {'success': True, 'action': 'answer', 'reply': CHIME_REPLY, 'followups': [], 'profile': 'Chime Banking Customers'}
        env = envelope.wrap(raw, surface='analyze', decision={'surface': 'analyze', 'reason': 'data_open'})
        text = env.get('text') or ''
        self.assertNotIn('392,312,555', text)
        self.assertIn('insurance-active people', text)
        self.assertTrue(all(n <= AUDIENCE for n in _counts_in(text)), text)
        self.assertIn('reply:people', raw.get('_scrubbed') or [])
        self.assertTrue(env.get('success'))
        self.assertEqual(env.get('kind'), 'answer')

    def test_a_payload_metric_follows_its_count(self):
        raw = {'reply': CHIME_REPLY, 'metrics': [{'label': 'insurance-active people', 'value': WRONG, 'unit': 'count'},
                                                 {'label': 'audience', 'value': AUDIENCE, 'unit': 'count'}]}
        PB.guard_payload(raw)
        self.assertEqual(raw['metrics'][0]['value'], 3_923_126)
        self.assertEqual(raw['metrics'][1]['value'], AUDIENCE)
        self.assertNotIn('392,312,555', raw['reply'])

    def test_the_vocabulary_pass_every_deck_string_and_chat_reply_goes_through(self):
        import prometheus_analysis as pma
        out = pma.scrub_user_text(CHIME_REPLY)
        self.assertNotIn('392,312,555', out)
        self.assertIn('that is about 3,923,126 insurance-active people', out)
        card = "Insurance reaches 43.7% of the file, 392,312,555 people."
        self.assertEqual(pma.scrub_user_text(card), "Insurance reaches 43.7% of the file.")
        fine = "Insurance reaches 43.7% of this audience, 3,925,084 people, index 101."
        self.assertEqual(pma.scrub_user_text(fine), fine)

    def test_the_wiring_is_in_place(self):
        def src(*p):
            with open(os.path.join(ROOT, *p), encoding='utf-8') as fh:
                return fh.read()
        self.assertIn('_pb.guard_payload(raw)', src('prometheus', 'envelope.py'))
        self.assertIn('_pb.guard_composed(', src('prometheus', 'figures.py'))
        self.assertIn('_pb.clean(s)', src('prometheus_analysis.py'))
        chat = src('prometheus', 'legacy', 'chat.py')
        i = chat.index('_rs.shape_finished_read(payload, held)')
        self.assertIn("fromlist=['guard_payload']).guard_payload(payload)", chat[i:i + 400])
        # the finished read is guarded before it lands in the thread, the job record and the email
        self.assertLess(i, chat.index('_pm_append_read_to_history(pm_user, job_id, payload, held=held'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
