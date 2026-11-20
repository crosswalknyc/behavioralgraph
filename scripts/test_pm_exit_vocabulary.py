#!/usr/bin/env python3
"""The one exit pipeline and the one vocabulary table (stage 4 of the
2026-11-19 rebuild, approved by Jenna 2026-11-20).

Properties, not string pins:

  * the table is the only copy: every old list name in guards,
    prometheus_analysis, envelope, outbound_mail and twin_notify IS the
    table's object (aliases, not copies);
  * the pipeline is idempotent, never empties a non-empty string, never
    rewrites a name (a title, a brand, a person), returns every held-out
    line byte for byte;
  * no banned word leaves: no source name, no method word, no HH or
    household, no dash character, on every kind the pipeline runs;
  * the two gaps the stage closed stay closed: a key caller's string
    loses its dash character and its HHs; a payload's chips and fields
    get the same rules whatever lane composed them;
  * the mail door refuses the story and the banned token, and sends
    the clean copy From Prometheus;
  * exit.check is empty on clean text and names the leak on dirty text.

Hermetic: no network, no S3, no model. Run from either repo.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB = os.path.dirname(_HERE)
if not os.path.exists(os.path.join(_WEB, 'prometheus', 'exit.py')):
    _WEB = os.path.join(_WEB, 'bg-webapp')
if _WEB not in sys.path:
    sys.path.insert(0, _WEB)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import exit as X  # noqa: E402
from prometheus import vocabulary as V  # noqa: E402
from prometheus import guards as G  # noqa: E402
from prometheus import envelope as E  # noqa: E402
from prometheus import outbound_mail as OM  # noqa: E402
from prometheus import twin_notify as TN  # noqa: E402
import prometheus_analysis as PMA  # noqa: E402

DIRTY = [
    "We estimated the audience at 3.1M using panel data from Nielsen. The share is 12%.",
    "According to Statista, 41% of US adults streamed Peacock last month. The read is 38.2%.",
    "The synth pipeline anchored to Comscore gives 12.4M. The count is 12.4M.",
    "Reach is 4.2M \u2014 up 10%. The HHs grew to 2,311 households; per-HH spend $42.",
    "The Claude model (Opus 4) wrote this on Hetzner and ClickHouse; the figure is 2.1M.",
    "Panelists in the panel were panel-projected; the panelist count is 10,000 people.",
    "Per Variety, the show drew 4.1M. Deadline reported 3.9M.",
    "The researched count is 4.3M. Crosswalk modeled this from public data. The figure is 4.3M.",
    "Household income over $100K: 34.2%. Households with kids: 41%. HH count 2.3M.",
    "estimate: 3.1M. Our estimates land at 4M. The Estimate is high.",
    "The penetration_pct column and share_pct are the same shared audience.",
    "On my side the defect was a placeholder; the figure is 1.2M.",
]
CLEAN = [
    "Reach was 4.2M in the window, up 10% on the year before.",
    "Hot Ones, Architectural Digest Open Door and Tiny Desk reach 3.2M people.",
    "The Doors and Three Doors Down index 1,240 against the US average.",
    "Which subject should this be on? Name the show, brand or person and I will take it from there.",
    "Working on it. The read will land in this thread when it is ready.",
    "Pulling this data will cost $100.",
    "Included with your plan.",
    "Your read landed above.",
    "No data can be pulled from before January 1, 2021.",
    "Every row carries both the projected US figure and the count inside the 10 million US consumers we measure (the Raw column).",
]
# Known and left as is (reported to Jenna 2026-11-20, not changed here): the
# sample pass reads every "sample" as the research word, so a verb or a
# brand use ("Sample the album") is rewritten at the payload exit.
NAMES = ['Architectural Digest Open Door', 'The Doors', 'Three Doors Down', 'Reba McEntire', 'Gilmore Girls',
         'Chicago P.D.', 'Mr. Beast', 'Gen Pop Studios', 'Sonnet Software', 'The Estimate', 'Panel Picture Co.']
# people and brands that share a word with a model name: kept, in prose and on the belt (2026-11-20)
NAMED_PEOPLE = ['Jean-Claude Van Damme reaches 3.2M people in the window.',
                'Claude Monet draws 1.1M; the Claude Rains film draws 0.4M.',
                'Opus One wine indexes 240; Sonnet 18 was read by 2M; a sonnet a day.']
MODEL_REFS = ['Claude wrote this answer.', 'The Claude model (Opus 4) wrote this.', 'Sonnet 4.6 wrote this with Opus 4.',
              'We ran it on Opus and Sonnet.', 'Claude Opus and Claude AI agree.', "Claude's answer is 4.2M."]


class TableIsTheOnlyCopy(unittest.TestCase):
    def test_old_names_are_the_tables_objects(self):
        self.assertIs(G._METHOD_SENTENCE_RX, V.METHOD_SENTENCE_RX)
        self.assertIs(G._METHOD_SAFE_SWAPS, V.METHOD_SAFE_SWAPS)
        self.assertIs(G._METHOD_WORD_SUBS, V.METHOD_WORD_SUBS)
        self.assertIs(G._CAUSE_SENTENCE_RX, V.CAUSE_SENTENCE_RX)
        self.assertIs(G._CAUSE_MAIL_RX, V.CAUSE_MAIL_RX)
        self.assertIs(G.SAMPLE_VOCAB_RX, V.SAMPLE_VOCAB_RX)
        self.assertIs(G.PARTNER_BANNED_TOKENS, V.PARTNER_BANNED_TOKENS)
        self.assertIs(G._PARTNER_HARD_RX, V.PARTNER_HARD_RX)
        self.assertIs(G._PARTNER_WORD_SWAPS, V.PARTNER_WORD_SWAPS)
        self.assertIs(G._JARGON_SUBS, V.JARGON_SUBS)
        self.assertIs(G._SCRUB_SKIP_KEYS, V.SCRUB_SKIP_KEYS)
        self.assertIs(PMA._SCRUB_RULES, V.WORD_SWAPS)
        self.assertIs(PMA._SCRUB_COMPILED, V.WORD_SWAPS_COMPILED)
        self.assertIs(PMA._CITATION_RX, V.CITATION_RX)
        self.assertIs(PMA._OFFCLICK_RX, V.OFFCLICK_RX)
        self.assertIs(E._INFRA_RX, V.INFRA_RX)
        self.assertIs(OM.BANNED, V.MAIL_BANNED)
        self.assertIs(TN.BANNED_RX, V.TWIN_NOTICE_RX)

    def test_source_names_come_from_the_one_list(self):
        from migration import source_names as SN
        self.assertIs(V.SOURCE_HARD_RX, SN.HARD_RX)
        self.assertIs(V.strip_named_sources, SN.strip_named_sources)
        self.assertIs(V.PARTNER_VENDOR_RX, SN.HARD_RX)

    def test_app_partner_tokens_read_the_table(self):
        with open(os.path.join(_WEB, 'app.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('from prometheus.guards import PARTNER_BANNED_TOKENS as _V1_BANNED_TOKENS', src)

    def test_the_hh_rule_is_in_the_table(self):
        self.assertIn('Accounts', [rep for _p, rep in V.HH_SWAPS])
        self.assertEqual(V.swap_hh('2.3M HHs reached; per-HH spend $4; HH count up; HH:MM; the HHS said so'),
                         '2.3M Accounts reached; per account spend $4; account count up; HH:MM; the HHS said so')


class PipelineProperties(unittest.TestCase):
    def test_idempotent(self):
        for s in DIRTY + CLEAN:
            for kind in (X.PROSE, X.CHIP, X.DECK_COPY, X.MAIL, X.PARTNER, X.BRIEF_FIELD):
                once = X.text(s, kind)
                self.assertEqual(X.text(once, kind), once, (kind, s))

    def test_never_empties(self):
        for s in DIRTY + CLEAN:
            for kind in (X.PROSE, X.CHIP, X.DECK_COPY, X.MAIL, X.BRIEF_FIELD):
                self.assertTrue(X.text(s, kind).strip(), (kind, s))

    def test_clean_text_is_untouched(self):
        for s in CLEAN:
            self.assertEqual(X.text(s), s, s)
            self.assertEqual(X.check(s), [], s)

    def test_names_are_never_rewritten(self):
        for n in NAMES:
            self.assertEqual(X.text(n, X.NAME), n)
            self.assertEqual(X.check(n, X.NAME), [])
        # and inside prose, a title that carries a plain word keeps it
        s = 'Architectural Digest Open Door and The Doors reach 3.2M people.'
        self.assertEqual(X.text(s), s)

    def test_a_person_or_brand_named_like_a_model_is_kept(self):
        for s in NAMED_PEOPLE:
            self.assertEqual(X.text(s), s)
            self.assertEqual(X.text(s, X.PARTNER), s)
            self.assertEqual(X.check(s), [])
        for s in MODEL_REFS:
            out = X.text(s).lower()
            for w in ('claude', 'opus', 'sonnet'):
                self.assertNotIn(w, out, (s, out))
            self.assertEqual(X.text(s, X.PARTNER), '', s)        # the belt fails closed on the model
            self.assertTrue(G.partner_violations(s), s)

    def test_a_doubled_article_reads_once(self):
        self.assertEqual(X.text('I can run the the last read again.'), 'I can run the last read again.')
        self.assertEqual(X.text('On the The Fundamentals of Caring audience: 1.6x.'), 'On The Fundamentals of Caring audience: 1.6x.')
        self.assertEqual(X.text('The the Doors reach 2M. A share of of 12%.'), 'The Doors reach 2M. A share of 12%.')
        raw = {'success': True, 'reply': 'Show me the The Doors read.', 'followups': ['Show me the The Doors']}
        E.wrap(raw, surface='analyze', decision={})
        self.assertEqual((raw['reply'], raw['followups']), ('Show me The Doors read.', ['Show me The Doors']))

    def test_held_lines_come_back_byte_for_byte(self):
        for s in V.allowlist():
            self.assertEqual(X.text(s), s)
            self.assertEqual(X.text(s, X.MAIL), s)
            self.assertEqual(X.check(s), [])

    def test_no_banned_word_leaves_prose(self):
        for s in DIRTY:
            out = X.text(s)
            low = out.lower()
            for bad in ('nielsen', 'statista', 'comscore', 'variety', 'deadline', 'claude', 'opus', 'hetzner', 'clickhouse',
                        'synth', 'pipeline', 'anchored', 'estimated', 'modeled', 'panel', 'hhs', 'household', 'placeholder',
                        'penetration_pct', 'share_pct', '\u2014', '\u2013'):
                self.assertNotIn(bad, low, (bad, s, out))
            self.assertEqual([h for h in X.check(s) if not h.startswith('method sentence')], [], (s, out))

    def test_a_figure_survives_the_scrub(self):
        out = X.text("We estimated the audience at 3.1M using panel data from Nielsen. The share is 12%.")
        self.assertIn('12%', out)
        out = X.text("Per Variety, the show drew 4.1M. Deadline reported 3.9M.")
        self.assertIn('4.1M', out)
        self.assertIn('3.9M', out)

    def test_the_boundary_statement_is_kept_and_the_claim_is_dropped(self):
        keep = 'In-store purchases sit outside what we measure, so the read stops at the digital trace.'
        self.assertEqual(X.text(keep), keep)
        drop = 'Foot traffic rose 4% in the window. Digital reach was 2.1M.'
        self.assertEqual(X.text(drop), 'Digital reach was 2.1M.')
        mixed = 'Foot traffic rose 4%, which sits outside our data. Digital reach was 2.1M.'
        self.assertEqual(X.text(mixed), 'Digital reach was 2.1M.')

    def test_our_first_party_data_is_the_house_voice(self):
        s = 'Our first-party data shows 4.2M viewers in the window. The share is 12%.'
        self.assertEqual(X.text(s), s)
        s2 = "This comes from Crosswalk's own first-party data: 4.2M viewers."
        self.assertEqual(X.text(s2), s2)
        # a figure put at the client's door still goes
        self.assertNotIn('first-party', X.text('The client first-party report says 3M accounts. The read is 2.9M.'))

    def test_order_is_fixed_and_named(self):
        self.assertEqual(X.ORDER[0], 'hold_definitions')
        self.assertEqual(X.ORDER[-1], 'release_definitions')
        self.assertLess(X.ORDER.index('sources'), X.ORDER.index('words'))
        self.assertLess(X.ORDER.index('words'), X.ORDER.index('method'))
        self.assertLess(X.ORDER.index('method'), X.ORDER.index('people_bounds'))
        self.assertIn('doubled_article', X.ORDER)


class TheGapsStayClosed(unittest.TestCase):
    def test_partner_belt_drops_dashes_and_hhs(self):
        out = X.text('Reach is 4.2M \u2014 up 10%. The HHs grew to 2,311.', X.PARTNER)
        self.assertEqual(out, 'Reach is 4.2M - up 10%. The Accounts grew to 2,311.')
        self.assertNotIn('\u2014', out)
        v1 = {'answer': {'text': 'Reach 4.2M \u2013 HHs 2,311.', 'facts': [{'k': 1}]}}
        X.partner(v1)
        self.assertEqual(v1['answer']['text'], 'Reach 4.2M - Accounts 2,311.')

    def test_payload_fields_and_chips_get_the_light_rules(self):
        raw = {'success': True, 'reply': 'Reach is 4.2M \u2014 up 10%. HHs 2,311.',
               'followups': ['Show HHs by month', 'Compare \u2013 last year'],
               'options': [{'label': 'HHs by DMA', 'send': 'HHs by DMA'}],
               'spec_draft': {'estimated_audience_sentence': 'Estimated audience: about 1,204,311 to 1,502,211 US people.'}}
        env = E.wrap(raw, surface='analyze', decision={})
        self.assertEqual(env['text'], 'Reach is 4.2M - up 10%. Accounts 2,311.')
        self.assertEqual(raw['followups'], ['Show Accounts by month', 'Compare - last year'])
        self.assertEqual(raw['options'][0]['label'], 'Accounts by DMA')
        self.assertEqual(raw['spec_draft']['estimated_audience_sentence'], 'Estimated audience: about 1,204,311 to 1,502,211 US people.')
        self.assertIn('reply:light', raw.get('_scrubbed', []))

    def test_payload_runs_the_method_scrub_as_before(self):
        raw = {'success': True, 'reply': 'We estimated this from panel data. The share is 12%.'}
        E.wrap(raw, surface='analyze', decision={})
        self.assertEqual(raw['reply'], 'The share is 12%.')

    def test_payload_leaves_a_clean_lane_reply_alone(self):
        for s in CLEAN:
            raw = {'success': True, 'reply': s, 'followups': ['Show me more']}
            E.wrap(raw, surface='analyze', decision={})
            self.assertEqual(raw['reply'], s)
            self.assertEqual(raw['followups'], ['Show me more'])


class TheMailDoor(unittest.TestCase):
    def test_refuses_the_story_before_any_scrub(self):
        subj, body, refusal = X.mail('Your read', 'We fixed a defect on our side and re-read the file. Reach is 4.2M.')
        self.assertTrue(refusal.startswith('explains what happened'))

    def test_refuses_a_banned_token_left_after_the_scrub(self):
        # a word the prose scrub has no swap for and the mail list refuses
        subj, body, refusal = X.mail('Your read', 'A regression on the window gives 4.2M. Reach is 4.2M.')
        self.assertIn('banned vocabulary after scrub', refusal)
        self.assertIn('regression', refusal)
        # a method sentence is dropped by the scrub first, so the mail may go
        subj, body, refusal = X.mail('Your read', 'The research shows 4.2M. Reach is 4.2M.')
        self.assertEqual((body, refusal), ('Reach is 4.2M.', ''))

    def test_sends_clean_copy(self):
        subj, body, refusal = X.mail('Your read \u2014 Peacock', 'Reach is 4.2M in the window. The HHs grew to 2,311.')
        self.assertEqual(refusal, '')
        self.assertEqual(subj, 'Your read - Peacock')
        self.assertIn('Accounts grew', body)
        self.assertNotIn('\u2014', subj + body)

    def test_the_door_module_uses_the_exit(self):
        rec = OM.send_user_email(to='reader@example.com', subject='Your read', body='We fixed a bug on our end. Reach is 4.2M.',
                                 instructed=True, dry_run=True)
        self.assertFalse(rec['sent'])
        self.assertTrue(str(rec.get('reason', '')).startswith('explains what happened'))
        rec = OM.send_user_email(to='reader@example.com', subject='Your read', body='Reach is 4.2M in the window.',
                                 instructed=True, dry_run=True)
        self.assertEqual(rec.get('reason'), 'dry run')
        self.assertEqual(OM.FROM, 'Prometheus <prometheus@crosswalknyc.com>')


class TheChecker(unittest.TestCase):
    def test_names_the_leak(self):
        hits = V.hits('Per Nielsen the panel estimated 4.2M \u2014 up 10%.')
        low = ' '.join(hits).lower()
        for want in ('dash', 'nielsen', 'panel', 'estimated'):
            self.assertIn(want, low, hits)

    def test_mail_kind_adds_the_mail_list(self):
        self.assertIn('research', V.hits('The research shows 4.2M.', X.MAIL))
        self.assertEqual(V.hits('Reach is 4.2M.', X.MAIL), [])

    def test_partner_kind_adds_the_belt_tokens(self):
        self.assertIn('hostmap', V.hits('The hostmap has it.', X.PARTNER))

    def test_brief_field_checks_only_the_sample_words(self):
        self.assertEqual(V.hits('Estimated audience: about 1,204,311 to 1,502,211 US people.', X.BRIEF_FIELD), [])
        self.assertIn('sample', ' '.join(V.hits('Estimated audience: 12 inside the 10 million sample.', X.BRIEF_FIELD)).lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
