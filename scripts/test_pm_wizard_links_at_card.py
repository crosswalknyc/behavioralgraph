#!/usr/bin/env python3
"""The Brand Partnership wizard takes the campaign links at the confirm
card, and at any other step (Jenna 2026-11-24).

A UTA seat at the card ("Here's what I'll run ... I'll find the assets
... This is $500.") said "actually i ahve the links", then pasted 53
campaign URLs across Instagram, Facebook, TikTok and YouTube. Both times
the card came back unchanged, with "I'll find the assets" and "Say Accept
to run it, or Decline to stop.", and she declined. apply_answer had no
branch for the charge step: the message fell through, the step stayed at
the card, and the card was shown again.

Covers, hermetically (no model, no S3, no mail; the stub host and the
lanes of service.ask):
  * links_switch: "I have the links", "actually i ahve the links" (the
    typo as typed), "here are the links", "use these links", "I've got
    the links" switch the asset step; "find the assets", "I don't have
    the links", a question and a dates correction never do;
  * links_message: a message that is mostly URLs is the asset list, any
    platform, deduped and verbatim; a sentence with one link inside long
    prose is not;
  * at the card: the phrase alone asks for the links; the phrase plus
    links, links alone, links with a few words, mixed platforms and
    duplicates all land on the card with "Using your N links" and the
    price as quoted; "find the assets" at the card goes back to finding;
    Accept after the links runs with them;
  * before the card: links at the dates step are kept and the step asked
    again, and the card later counts them (the thread replay keeps them
    too); the phrase alone at the dates step switches the mode and the
    links are asked for when the walk reaches them;
  * the interrupt rules hold: a dates correction at the card still
    replaces the dates and keeps the links; a URL list is never an
    unrelated message; the wizard still opens only on an explicit order
    (links or the phrase with nothing open never start it);
  * a card that counts the links is read back as asset mode links; the
    replay fixtures carry the case with invented names.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import understanding as U  # noqa: E402
from prometheus import wizard_interrupt as WI  # noqa: E402

TALENT, BRAND = 'Example Talent', 'Northwind Beauty'
PHRASE = 'actually i ahve the links'           # as typed, typo included
URLS = ([f'https://www.instagram.com/p/EXAMPLE{i:02d}/' for i in range(20)]
        + [f'https://www.tiktok.com/@examplecreator/video/70000000000000{i:02d}' for i in range(20)]
        + [f'https://www.youtube.com/watch?v=EXAMPLE{i}' for i in range(8)]
        + [f'https://www.facebook.com/examplebrand/posts/{i}' for i in range(5)])
BLOB = '\n'.join(URLS)
CARD_TEXT = (f"Here's what I'll run:\n{TALENT}\n{BRAND}\nCampaign dates: August 27 to September 8, 2026\n"
             "Attribution window: August 27 to October 8, 2026\nControl group: no\nI'll find the assets\nThis is $500.")
TO_DATES = [
    {'role': 'user', 'text': 'Value a brand partnership'},
    {'role': 'agent', 'text': 'Who is the property or talent?', 'meta': {'options': ['Cancel']}},
    {'role': 'user', 'text': TALENT},
    {'role': 'agent', 'text': 'What campaign should I value?', 'meta': {'options': ['Cancel']}},
    {'role': 'user', 'text': BRAND},
    {'role': 'agent', 'text': 'What are the campaign dates?', 'meta': {'options': ['Cancel']}},
]
WINDOW_TEXT = (f'{TALENT}, {BRAND}: I read the campaign as August 27 to September 8, 2026. The standard window is the campaign '
               'dates plus 30 days after it ends. Attribution window: August 27 to October 8, 2026.')
AT_CARD = TO_DATES + [
    {'role': 'user', 'text': '8/27/26-9/8/26'},
    {'role': 'agent', 'text': WINDOW_TEXT, 'meta': {'options': ['Accept', 'Use a different window', 'Cancel']}},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': 'Include a control group?', 'meta': {'options': ['Yes', 'No', 'Cancel']}},
    {'role': 'user', 'text': 'No'},
    {'role': 'agent', 'text': W.ASK_ASSETS, 'meta': {'options': [W.CHIP_PASTE, W.CHIP_FIND, 'Cancel']}},
    {'role': 'user', 'text': 'Find the assets'},
    {'role': 'agent', 'text': CARD_TEXT, 'meta': {'options': ['Accept $500', 'Decline', 'Cancel']}},
]


def _card_draft(**kw):
    d = W.empty_draft()
    d.update({'talent': TALENT, 'brand_partner': BRAND, 'brand_named': True, 'campaign': BRAND,
              'campaign_start': '2026-08-27', 'campaign_end': '2026-09-08', 'attr_start': '2026-08-27', 'attr_end': '2026-10-08',
              'attr_ok': True, 'control_group': False, 'asset_mode': 'find', 'links': [], 'step': W.STEP_CHARGE})
    d.update(kw)
    return W._price_draft(d)


def _card_lines(reply):
    return [ln.strip() for ln in reply.split('\n') if ln.strip()]


# ------------------------------------------------------------------ the words

class Words(unittest.TestCase):
    def test_the_switch_phrases(self):
        for t in (PHRASE, 'I have the links', 'actually I have the links', 'here are the links', 'use these links', 'i have links',
                  "I've got the links", 'These are the links:', 'the links are below', "I'll paste the links", 'actually I have the urls',
                  "here's the link", 'we have 53 links', 'use the following posts'):
            self.assertTrue(W.links_switch(t), t)

    def test_never_a_switch(self):
        for t in ('find the assets', 'you find them', "I don't have the links", 'do you have the links?', 'no links', 'Accept', 'Decline',
                  'the campaign dates are sep 1 - 8, 2026', 'use whatever links you can find', 'can you find the links', 'not sure'):
            self.assertFalse(W.links_switch(t), t)

    def test_a_message_that_is_mostly_urls_is_the_asset_list(self):
        self.assertEqual(W.links_message(BLOB), URLS)                                       # 53, verbatim, in order
        self.assertEqual(W.links_message(BLOB + '\n' + URLS[0] + '\n' + URLS[3]), URLS)     # duplicates folded
        self.assertEqual(W.links_message('here are the posts we ran:\n' + BLOB), URLS)      # a few words around it
        self.assertEqual(W.links_message(PHRASE + '\n' + BLOB), URLS)
        self.assertEqual(W.links_message('https://www.youtube.com/watch?v=ABC please use this one'), ['https://www.youtube.com/watch?v=ABC'])
        self.assertEqual(W.links_message('We ran a campaign across several channels and the anchor piece was this video '
                                         'https://www.youtube.com/watch?v=ABC which did well, and we would love to understand how it drove '
                                         'people to the site over the following weeks and what it was worth to the brand'), [])
        self.assertEqual(W.links_message('no links here'), [])
        platforms = {u.split('/')[2] for u in W.links_message(BLOB)}
        self.assertEqual(platforms, {'www.instagram.com', 'www.tiktok.com', 'www.youtube.com', 'www.facebook.com'})

    def test_take_links_dedupes_and_keeps_order_and_price(self):
        d = W.take_links(_card_draft(), URLS[:3] + [URLS[0]])
        self.assertEqual(d['links'], URLS[:3])
        self.assertEqual(d['asset_mode'], 'links')
        d2 = W.take_links(d, URLS[2:5])
        self.assertEqual(d2['links'], URLS[:5])
        self.assertEqual(W._price_draft(d2)['price'], W.price_usd(False))
        self.assertEqual(W.links_line(d2), 'Using your 5 links')
        self.assertEqual(W.links_line(W.take_links(_card_draft(), URLS[:1])), f'Using your 1 link: {URLS[0]}')


# ------------------------------------------------------------------ the step

class AtTheCard(unittest.TestCase):
    def test_the_phrase_alone_asks_for_the_links(self):
        d, err = W.apply_answer(_card_draft(), PHRASE)
        self.assertIsNone(err)
        self.assertEqual(d['asset_mode'], 'links')
        self.assertEqual(d['step'], W.STEP_LINKS)
        self.assertEqual(W.step_prompt(d)[0], W.ASK_LINKS)
        self.assertEqual(d['price'], W.price_usd(False))

    def test_links_in_every_shape_land_on_the_card_with_the_count(self):
        for text in (BLOB, PHRASE + '\n' + BLOB, 'here are the posts we ran:\n' + BLOB, BLOB + '\n' + URLS[0] + '\n' + URLS[7]):
            d, err = W.apply_answer(_card_draft(), text)
            self.assertIsNone(err, text[:40])
            self.assertEqual(d['step'], W.STEP_CHARGE)
            self.assertEqual(d['asset_mode'], 'links')
            self.assertEqual(d['links'], URLS)
            self.assertEqual(d['price'], 500)
            lines = _card_lines(W.recap_reply(d))
            self.assertIn('Using your 53 links', lines)            # many links are counted, never listed back
            self.assertNotIn("I'll find the assets", lines)
            self.assertIn('This is $500.', lines)
            self.assertNotIn('Links:', W.recap_reply(d))
            self.assertNotIn('instagram.com', W.recap_reply(d))
        d, _ = W.apply_answer(_card_draft(), URLS[0])
        self.assertIn(f'Using your 1 link: {URLS[0]}', _card_lines(W.recap_reply(d)))   # a few links are named
        d, _ = W.apply_answer(_card_draft(), '\n'.join(URLS[:3]))
        self.assertIn('Using your 3 links: ' + ', '.join(URLS[:3]), _card_lines(W.recap_reply(d)))
        d, _ = W.apply_answer(_card_draft(), '\n'.join(URLS[:4]))
        self.assertIn('Using your 4 links', _card_lines(W.recap_reply(d)))

    def test_the_phrase_then_the_links(self):
        d, _ = W.apply_answer(_card_draft(), PHRASE)
        d2, err = W.apply_answer(d, BLOB)
        self.assertIsNone(err)
        self.assertEqual(d2['step'], W.STEP_CHARGE)
        self.assertEqual(len(d2['links']), 53)
        self.assertIn('Using your 53 links', W.recap_reply(d2))

    def test_find_the_assets_at_the_card_goes_back_to_finding(self):
        d, _ = W.apply_answer(_card_draft(), BLOB)
        d2, err = W.apply_answer(d, 'actually just find the assets yourself')
        self.assertIsNone(err)
        self.assertEqual(d2['asset_mode'], 'find')
        self.assertEqual(d2['links'], [])
        self.assertIn("I'll find the assets", W.recap_reply(d2))

    def test_accept_and_decline_still_read_as_themselves(self):
        self.assertTrue(W._ACCEPT_RE.match('Accept $500'))
        self.assertTrue(W.is_decline('Decline'))
        self.assertFalse(W.links_switch('Accept $500'))
        self.assertEqual(W.links_message('Accept $500'), [])

    def test_the_engine_inputs_carry_the_links(self):
        d, _ = W.apply_answer(_card_draft(), BLOB)
        inputs = W.engine_inputs(d)
        got = inputs.get('links') or inputs.get('asset_links') or inputs.get('assets') or []
        self.assertEqual(list(got), URLS)


class BeforeTheCard(unittest.TestCase):
    def test_links_at_the_dates_step_are_kept_and_the_step_asked_again(self):
        d = W.empty_draft()
        d.update({'talent': TALENT, 'campaign': BRAND, 'brand_partner': BRAND, 'step': W.STEP_DATES})
        d2, err = W.apply_answer(d, BLOB[:400])
        self.assertTrue(err and err.startswith(W.LINK_KEPT_LINE), err)
        self.assertEqual(d2['step'], W.STEP_DATES)
        self.assertEqual(d2['asset_mode'], 'links')
        self.assertTrue(d2['links'])

    def test_links_at_the_control_step_too(self):
        d = _card_draft(control_group=None, asset_mode='', step=W.STEP_CONTROL)
        d2, err = W.apply_answer(d, BLOB)
        self.assertTrue(err and err.startswith(W.LINK_KEPT_LINE), err)
        self.assertEqual(d2['step'], W.STEP_CONTROL)
        self.assertEqual(len(d2['links']), 53)
        d3, err3 = W.apply_answer(d2, 'No')
        self.assertIsNone(err3)
        self.assertEqual(d3['step'], W.STEP_CHARGE)           # the asset step is already answered
        self.assertIn('Using your 53 links', W.recap_reply(d3))

    def test_the_phrase_alone_before_the_card_switches_the_mode(self):
        d = W.empty_draft()
        d.update({'talent': TALENT, 'campaign': BRAND, 'brand_partner': BRAND, 'step': W.STEP_DATES})
        d2, err = W.apply_answer(d, 'I have the links')
        self.assertIsNone(err)
        self.assertEqual(d2['step'], W.STEP_DATES)
        self.assertEqual(d2['asset_mode'], 'links')
        d3, _ = W.apply_answer(d2, '8/27/26-9/8/26')
        d4, _ = W.apply_answer(d3, 'Accept')
        d5, _ = W.apply_answer(d4, 'No')
        self.assertEqual(d5['step'], W.STEP_LINKS)             # the links are asked for when the walk reaches them

    def test_the_card_that_counts_the_links_reads_back_as_links(self):
        d, _ = W.apply_answer(_card_draft(), BLOB)
        seen = W._draft_from_recap(W.recap_reply(d))
        self.assertEqual(seen['asset_mode'], 'links')
        self.assertEqual(seen['step'], W.STEP_CHARGE)
        few, _ = W.apply_answer(_card_draft(), '\n'.join(URLS[:2]))
        seen2 = W._draft_from_recap(W.recap_reply(few))
        self.assertEqual(seen2['links'], URLS[:2])                 # a card that names the links gives them back
        # the thread replay keeps the pasted list behind a counted card
        thread = AT_CARD + [{'role': 'user', 'text': BLOB}, {'role': 'agent', 'text': W.recap_reply(d), 'meta': {'options': ['Accept $500']}}]
        again = W.quoted_draft(thread)
        self.assertEqual(again['asset_mode'], 'links')
        self.assertEqual(again['links'], URLS)

    def test_the_interrupt_rules_hold(self):
        d, _ = W.apply_answer(_card_draft(), BLOB)
        corr = WI.correction('the campaign dates are sep 1 - 8, 2026', d)
        self.assertEqual(corr, {'campaign_start': '2026-09-01', 'campaign_end': '2026-09-08'})
        d2 = WI.apply_correction(d, corr, 'the campaign dates are sep 1 - 8, 2026')
        self.assertEqual(d2['links'], URLS)                     # a correction replaces the dates, nothing else
        self.assertEqual(d2['asset_mode'], 'links')
        body = U.attach({'text': BLOB}, U.features(BLOB, None, AT_CARD, {}))
        self.assertFalse(WI.unrelated(body, BLOB, _card_draft()))
        body2 = U.attach({'text': PHRASE}, U.features(PHRASE, None, AT_CARD, {}))
        self.assertFalse(WI.unrelated(body2, PHRASE, _card_draft()))


# ------------------------------------------------------------------ the door

class ThroughTheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k in list(os.environ):
            if k.startswith('PM_U_OFF_') or k == 'PM_UNDERSTANDING':
                os.environ.pop(k, None)
        cls.stub = RC._Stub()
        cls.stub.bind()

    def _ask(self, text, history, view='brandPartnershipIQ'):
        return RC._service_once(self.stub, {'id': 'x', 'question': text, 'view': view, 'surface': 'analyze', 'subject': None,
                                            'history': list(history), 'reading': None})

    def test_the_two_live_turns(self):
        one = self._ask(PHRASE, AT_CARD)
        self.assertEqual(one['route'], 'bpiq_wizard', one['reply'][:200])
        self.assertEqual(one['reply'].strip(), W.ASK_LINKS)
        self.assertNotIn("I'll find the assets", one['reply'])
        self.assertNotIn('Say Accept to run it', one['reply'])
        then = AT_CARD + [{'role': 'user', 'text': PHRASE}, {'role': 'agent', 'text': one['reply'], 'meta': {'options': ['Cancel']}}]
        two = self._ask(BLOB, then)
        self.assertEqual(two['route'], 'bpiq_wizard', two['reply'][:200])
        lines = _card_lines(two['reply'])
        self.assertIn('Using your 53 links', lines)
        self.assertIn('This is $500.', lines)
        self.assertNotIn("I'll find the assets", lines)

    def test_links_alone_and_with_words_at_the_card(self):
        for text in (BLOB, PHRASE + ' ' + BLOB, 'here are the posts we ran:\n' + BLOB + '\n' + URLS[0]):
            live = self._ask(text, AT_CARD)
            self.assertEqual(live['route'], 'bpiq_wizard', live['reply'][:200])
            self.assertIn('Using your 53 links', live['reply'])
            self.assertIn('This is $500.', live['reply'])

    def test_links_before_the_card_count_on_the_card(self):
        a = self._ask(BLOB, TO_DATES)
        self.assertEqual(a['route'], 'bpiq_wizard')
        self.assertTrue(a['reply'].startswith(W.LINK_KEPT_LINE), a['reply'][:120])
        h = TO_DATES + [{'role': 'user', 'text': BLOB}, {'role': 'agent', 'text': a['reply'], 'meta': {'options': ['Cancel']}},
                        {'role': 'user', 'text': '8/27/26-9/8/26'},
                        {'role': 'agent', 'text': WINDOW_TEXT, 'meta': {'options': ['Accept', 'Use a different window', 'Cancel']}},
                        {'role': 'user', 'text': 'Accept'},
                        {'role': 'agent', 'text': 'Include a control group?', 'meta': {'options': ['Yes', 'No', 'Cancel']}}]
        card = self._ask('No', h)
        self.assertEqual(card['route'], 'bpiq_wizard', card['reply'][:200])
        self.assertIn('Using your 53 links', card['reply'])
        self.assertIn('This is $500.', card['reply'])

    def test_the_dates_correction_at_the_card_still_works(self):
        live = self._ask('the campaign dates are sep 1 - 8, 2026', AT_CARD)
        self.assertEqual(live['route'], 'bpiq_wizard')
        self.assertIn('September 1 to September 8, 2026', live['reply'])

    def test_nothing_open_never_starts_the_wizard(self):
        for text in (BLOB, PHRASE, 'here are the links\n' + BLOB[:300]):
            live = self._ask(text, [], view='')
            self.assertNotEqual(live['route'], 'bpiq_wizard', text[:40])
            self.assertNotIn('Who is the property or talent', live['reply'])

    def test_the_sanitized_fixtures_carry_the_case(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        with open(path, encoding='utf-8') as fh:
            cases = [json.loads(l) for l in fh if l.strip() and not l.startswith('#')]
        qs = {c['question']: c for c in cases}
        self.assertIn(PHRASE, qs)
        self.assertEqual(qs[PHRASE]['recorded']['route'], 'bpiq_wizard')
        self.assertTrue(any(c['question'].count('https://') >= 10 for c in cases), 'a pasted link list rides the fixtures')
        import re as _re
        for c in cases:
            blob = json.dumps(c).lower()
            for bad in (r'\bemi\b', r'\bhill\b', r'\buta\b', r'zarna', r'maybell', r'instagram\.com/p/d[a-z]'):
                self.assertIsNone(_re.search(bad, blob), bad)


if __name__ == '__main__':
    unittest.main(verbosity=1)
