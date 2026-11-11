#!/usr/bin/env python3
"""How others count a view (Jenna 2026-11-11: "where we talk about views
in methodology add a part that says how nelisen classifies a view ... and
then list this ... for how peacock classifys a view").

What is held here:

  * the counting block under a viewing read carries the Nielsen line; a
    read on Peacock, or one that names it, carries the Peacock line too;
    a Netflix read does not; a shopping, engagement or sizing read
    carries neither
  * a methodology question ("how does your view compare to Nielsen",
    "how does Peacock count a view", "why does your number differ from
    Nielsen ratings") gets the full part: our definition, the two
    entries, the line that ties them back; no read, no build, no model
  * the lines survive the read scrub, the read PDF, the mail door and
    the key-facing belt by exact text, while a stray sentence that names
    a ratings service anywhere else is still dropped
  * twin and focus group surfaces are unchanged: their guard still
    drops any sentence that names a ratings service
  * the copy: plain, no em or en dashes, people (never households) as
    our unit, nothing from the internal note beyond the facts, "has not
    published an official definition" in both Peacock forms

Hermetic: a stub host, no model, no S3, no app module, no mail.
"""
from __future__ import annotations

import io
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import methodology as M  # noqa: E402
from prometheus import methodology_answers as MA  # noqa: E402
from prometheus import guards as G  # noqa: E402
from prometheus import outbound_mail as OM  # noqa: E402
from prometheus import us_scope as US  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

USER = {'username': 'viewdefs', 'email': 'viewdefs@example.com', 'company': 'Example', 'role': 'user',
        'credits': -1, 'unlimited': True, 'paying_customer': True}
CALLS = []

PEACOCK_READ = ('On Poker Face viewers on Peacock:\n\n2,345,671 viewers watched Poker Face on Peacock in the window.')
NETFLIX_READ = ('On The Diplomat viewers on Netflix:\n\n3,187,243 viewers watched The Diplomat on Netflix in the window.')
STRAY = ' Per Nielsen, the finale drew 4.1M viewers. Nielsen reported 12 million minutes for it.'
VIEW_RES = {'metric_family': 'viewership', 'window_label': 'Jan 1 2026 to Oct 31 2026'}
PUBLISHED = (M.NIELSEN_LONG, M.PEACOCK_LONG, M.OTHERS_CLOSE, M.NIELSEN_LINE, M.PEACOCK_LINE, M.OTHERS_HEADER, M.OTHERS_PANEL)


def _viewing(text, subject, question, **extra):
    return M.ensure(text, dict(VIEW_RES, subject=subject, **extra), question=question)


def _bind_host():
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        CALLS.append(('analyze', text))
        return {'success': True, 'action': 'answer', 'read_job_id': 'j1',
                'reply': 'On it: reading now. The numbers land right here.', 'followups': []}

    def _interpret_core(user, body, text, history):
        CALLS.append(('interpret', text))
        return {'success': True, 'spec_draft': {'subject': 'x'}, 'estimated_credits': 5}

    host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
              deck_core=lambda *a: {'success': True, 'reply': 'deck'},
              ask_hint=lambda **k: None, gate_analyze=lambda u: True, gate_pull=lambda u: True,
              funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
              calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
              load_threads_index=lambda u: {'threads': []}, max_threads=50,
              s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
              thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
              thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
              job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
              load_users=lambda: {'users': {'viewdefs': dict(USER)}},
              check_user_credits=lambda u: (True, -1), consume_credit=lambda *a, **k: True,
              pricing_question=lambda t: bool(re.search(r"\b(?:cost|costs|price|pricing|rate card|charge)\b", str(t or ''), re.I)),
              pricing_copy_for=lambda u: RC.render('$'), pricing_copy=RC.render('$'))
    return host


def _ask(text, history=None):
    from prometheus import service
    CALLS.clear()
    env, st = service.ask(dict(USER), {'text': text, 'client': 'dashboard', 'history': list(history or [])})
    return env, st, env.get('raw') or {}


class CountingBlock(unittest.TestCase):
    def test_a_viewing_block_carries_the_nielsen_line(self):
        lines = M.block(text='3,187,243 viewers watched it', base_label='The Diplomat', family='viewing')
        self.assertIn(f"- {M.NIELSEN_LINE}", lines)
        self.assertNotIn(f"- {M.PEACOCK_LINE}", lines)
        # right after our own definition, before threshold, carriage and sessions
        i = lines.index(f"- {M.VIEW_HEADER} {M.VIEW_DEFINITION}")
        self.assertEqual(lines[i + 1], f"- {M.NIELSEN_LINE}")
        self.assertTrue(any(ln.startswith('- Carriage paths') for ln in lines[i + 2:]))

    def test_one_line_each(self):
        for ln in (M.NIELSEN_LINE, M.PEACOCK_LINE):
            self.assertNotIn('\n', ln)
            self.assertLess(len(ln), 260, ln)
            self.assertLess(len(ln), len(M.VIEW_DEFINITION))

    def test_a_peacock_read_carries_the_peacock_line(self):
        out = _viewing(PEACOCK_READ, 'Poker Face', 'how many people watched Poker Face on Peacock')
        self.assertIn(f"- {M.NIELSEN_LINE}", out)
        self.assertIn(f"- {M.PEACOCK_LINE}", out)
        self.assertLess(out.index(M.VIEW_HEADER), out.index(M.NIELSEN_LINE))
        self.assertLess(out.index(M.NIELSEN_LINE), out.index(M.PEACOCK_LINE))
        self.assertLess(out.index(M.PEACOCK_LINE), out.index('Carriage paths'))

    def test_the_peacock_line_rides_when_only_the_ask_or_the_subject_names_peacock(self):
        plain = 'On Poker Face viewers:\n\n2,345,671 viewers watched Poker Face in the window.'
        out = _viewing(plain, 'Poker Face', 'how many watched it on peacock?')
        self.assertIn(M.PEACOCK_LINE, out)
        out = _viewing(plain, 'Poker Face', 'how many watched it', universe='Poker Face viewers on Peacock')
        self.assertIn(M.PEACOCK_LINE, out)
        self.assertIn(f"- {M.PEACOCK_LINE}",
                      M.block(text='1,204,553 viewers', base_label='Peacock', family='viewing'))
        self.assertIn(f"- {M.PEACOCK_LINE}",
                      M.block(text='1,204,553 viewers', base_label='Poker Face', family='viewing', context='on Peacock'))

    def test_a_netflix_read_does_not(self):
        out = _viewing(NETFLIX_READ, 'The Diplomat', 'how many people watched The Diplomat on Netflix')
        self.assertIn(f"- {M.NIELSEN_LINE}", out)
        self.assertNotIn(M.PEACOCK_LINE, out)
        self.assertNotIn('Peacock', out)

    def test_once_only(self):
        out = _viewing(PEACOCK_READ, 'Poker Face', 'how many people watched Poker Face on Peacock')
        again = _viewing(out, 'Poker Face', 'how many people watched Poker Face on Peacock')
        self.assertEqual(out, again)
        self.assertEqual(out.count(M.NIELSEN_LINE), 1)
        self.assertEqual(out.count(M.PEACOCK_LINE), 1)

    def test_a_block_that_already_carries_our_definition_gains_the_lines(self):
        opened = (PEACOCK_READ + f"\n\n{M.HEADER}\n- Window: Jan 1 2026 to Oct 31 2026.\n"
                  f"- {M.VIEW_HEADER} {M.VIEW_DEFINITION}\n- {M.CARRIAGE}")
        out = _viewing(opened, 'Poker Face', 'how many watched')
        lines = out.split('\n')
        i = lines.index(f"- {M.VIEW_HEADER} {M.VIEW_DEFINITION}")
        self.assertEqual(lines[i + 1:i + 3], [f"- {M.NIELSEN_LINE}", f"- {M.PEACOCK_LINE}"])
        self.assertEqual(lines[i + 3], f"- {M.CARRIAGE}")
        self.assertEqual(M.ensure(out, dict(VIEW_RES, subject='Poker Face'), question='how many watched'), out)
        # a definition that sits outside a house block is left as it is
        loose = PEACOCK_READ + '\n\n' + M.VIEW_HEADER + ' ' + M.VIEW_DEFINITION
        self.assertEqual(_viewing(loose, 'Poker Face', 'how many watched'), loose)

    def test_a_shopping_read_carries_neither(self):
        text = 'On Peacock subscribers:\n\nWhere they shop: Amazon 84.1189%, Walmart 61.2244%; 1,204,553 buyers ordered from Target.'
        out = M.ensure(text, {'metric_family': 'purchase', 'subject': 'Peacock subscribers'},
                       question='where do Peacock subscribers shop')
        self.assertIn(M.ORDER_HEADER, out)
        for gone in (M.NIELSEN_LINE, M.PEACOCK_LINE, 'Nielsen', 'How Peacock counts', M.OTHERS_HEADER):
            self.assertNotIn(gone, out)
        for fam in ('purchase', 'engagement', 'sizing'):
            blk = '\n'.join(M.block(text='1,204,553 people on Peacock', base_label='Peacock', family=fam, context='Peacock'))
            self.assertNotIn('Nielsen', blk, fam)
            self.assertNotIn('How Peacock counts', blk, fam)

    def test_engagement_and_sizing_reads_carry_neither(self):
        for fam, text in (('engagement', 'On Peacock:\n\n2,204,553 people searched for Peacock in the window.'),
                          ('audience', 'On Peacock:\n\n31,204,553 people are in the Peacock audience, 44.2% female.')):
            out = M.ensure(text, {'metric_family': fam, 'subject': 'Peacock'}, question='how big is Peacock')
            self.assertNotIn('Nielsen', out, fam)
            self.assertNotIn('How Peacock counts', out, fam)

    def test_the_lines_are_block_lines(self):
        for ln in (M.NIELSEN_LINE, M.PEACOCK_LINE):
            self.assertTrue(US._BLOCK_LINE_RX.match(f"- {ln}"), ln)


class MethodologyAnswer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _bind_host()

    FULL = ("how does your view compare to Nielsen", "how does Peacock count a view", "how do you count a view",
            "why does your number differ from Nielsen", "how does that compare to Nielsen",
            "Why is your number different from Nielsen ratings?", "how does Nielsen classify a view?",
            "why don't your viewers match Peacock's numbers", "how does that compare to what Peacock reports?",
            "what counts as a view?")

    def _full(self, reply):
        self.assertEqual(reply, M.others_part())
        for part in (f"{M.VIEW_HEADER} {M.VIEW_DEFINITION}", M.OTHERS_HEADER, f"- {M.NIELSEN_LONG}",
                     f"- {M.PEACOCK_LONG}", M.OTHERS_CLOSE):
            self.assertIn(part, reply)
        self.assertLess(reply.index(M.VIEW_DEFINITION), reply.index(M.OTHERS_HEADER))
        self.assertLess(reply.index(M.NIELSEN_LONG), reply.index(M.PEACOCK_LONG))
        self.assertTrue(reply.endswith(M.OTHERS_CLOSE))

    def test_the_shape(self):
        for q in self.FULL:
            self.assertEqual(MA.shape_of(q), 'view_definition', q)
            self._full((MA.answer(q) or {}).get('reply') or '')

    def test_the_service_answers_directly(self):
        for q in self.FULL:
            env, st, raw = _ask(q)
            self.assertEqual(st, 200, q)
            self.assertEqual(env['decision']['reason'], 'standing', (q, env['decision']))
            self.assertEqual(CALLS, [], q)
            self._full(str(raw.get('reply') or ''))
            self.assertEqual(raw.get('followups') or [], [], q)
            self.assertIsNone(raw.get('not_quantifiable'), q)

    def test_in_a_numbered_list(self):
        raw = MA.answer("1. How do you count a view?\n2. Is age each person's own or a household guess?")
        reply = (raw or {}).get('reply') or ''
        self.assertIn('Taking your 2 questions in order.', reply)
        self.assertIn(M.NIELSEN_LONG, reply)
        self.assertIn(M.PEACOCK_LONG, reply)
        self.assertIn('never a household guess', reply)

    def test_reads_and_builds_keep_their_lanes(self):
        for q in ("how does that compare to Peacock", "how many people watched Love Island on Peacock",
                  "what were the Nielsen ratings for the Super Bowl", "build a profile of Peacock subscribers",
                  "compare Netflix and Peacock viewers of The Office", "how many views did the trailer get",
                  "pull viewers for Poker Face on Peacock", "what do Peacock viewers buy",
                  "what's your view on Netflix's ad tier", "show me Peacock's top titles by viewers"):
            self.assertNotEqual(MA.shape_of(q), 'view_definition', q)
            self.assertFalse(MA.is_view_definition_question(q), q)

    def test_linear_tune_in_is_still_declined(self):
        env, st, raw = _ask("what were the Nielsen ratings for the Super Bowl")
        self.assertNotIn(M.NIELSEN_LONG, str(raw.get('reply') or ''))
        self.assertEqual(pma.classify_quantifiability("what were the Nielsen ratings for the Super Bowl")['domain'], 'linear_tv')

    def test_the_service_checks_ahead_of_the_gate(self):
        with open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertLess(src.index('is_view_definition_question(text)'), src.index('classify_quantifiability(text)'))


class Guards(unittest.TestCase):
    def setUp(self):
        self.read = _viewing(PEACOCK_READ + STRAY, 'Poker Face', 'how many people watched Poker Face on Peacock')
        self.assertIn('Per Nielsen', self.read)

    def _held(self, out):
        self.assertIn(f"- {M.NIELSEN_LINE}", out)
        self.assertIn(f"- {M.PEACOCK_LINE}", out)
        self.assertIn('2,345,671 viewers watched Poker Face on Peacock', out)
        self.assertNotIn('Per Nielsen', out)
        self.assertNotIn('4.1M', out)
        self.assertNotIn('12 million minutes', out)
        self.assertNotIn('xqviewdef', out)

    def test_the_read_scrub(self):
        out = pma.scrub_user_text(self.read)
        self._held(out)
        self.assertEqual(pma.scrub_user_text(out), out)
        full = M.others_part()
        self.assertEqual(pma.scrub_user_text(full), full)
        self.assertEqual(pma.scrub_user_text(M.view_definition_panel()), M.view_definition_panel())

    def test_only_the_exact_lines_are_held(self):
        for stray in ("Nielsen counts time viewed, so our figure is higher.",
                      "According to Nielsen, streaming charts total the minutes watched on TV screens.",
                      M.NIELSEN_LINE.replace('time viewed, not views', 'time viewed and views'),
                      M.NIELSEN_LONG.replace('typical minute', 'average minute'),
                      "Comscore counts it differently.", "Samba TV saw 2.1M households."):
            out = pma.scrub_user_text('The read stands at 2,345,671 viewers. ' + stray)
            self.assertNotIn('Nielsen', out, stray)
            self.assertNotIn('Comscore', out, stray)
            self.assertNotIn('Samba', out, stray)
            self.assertIn('2,345,671 viewers', out)
        text, held = M.shield_definitions('Nielsen says so. ' + M.NIELSEN_LINE)
        self.assertEqual(len(held), 1)
        self.assertIn('Nielsen says so.', text)            # a stray sentence is never held
        self.assertEqual(M.unshield_definitions(text, held), 'Nielsen says so. ' + M.NIELSEN_LINE)
        # a token typed into a message is not a way past the scrub
        self.assertNotIn('xqviewdef', pma.scrub_user_text('hello xqviewdefq0qx and ' + M.NIELSEN_LINE))

    def test_the_read_pdf(self):
        import prometheus_email_pdf as PDF
        import pypdf
        data = PDF.render_answer_pdf('Poker Face on Peacock', self.read)
        self.assertTrue(data)
        text = ' '.join(' '.join((pg.extract_text() or '') for pg in pypdf.PdfReader(io.BytesIO(data)).pages).split())
        for ln in (M.NIELSEN_LINE, M.PEACOCK_LINE):
            self.assertIn(' '.join(ln.split()), text)
        self.assertNotIn('Per Nielsen', text)
        self.assertNotIn('12 million minutes', text)

    def test_the_mail_door(self):
        logged = []
        keep = OM._log
        OM._log = logged.append
        try:
            self.assertEqual(OM.cause_sentences(self.read), [])
            body = OM.scrub(self.read)
            self._held(body)
            self.assertEqual(OM.banned_tokens(body), [])
            rec = OM.send_user_email(to='reader@example.com', subject='Your read on Poker Face', body=self.read,
                                     instructed=True, dry_run=True, html='<p>read</p>', caller='test')
            self.assertEqual(rec.get('reason'), 'dry run', rec)
            full = M.others_part()
            self.assertEqual(OM.scrub(full), full)
            self.assertEqual(OM.banned_tokens(full), [])
            self.assertEqual(OM.cause_sentences(full), [])
            rec = OM.send_user_email(to='reader@example.com', subject='How a view is counted', body=full,
                                     instructed=True, dry_run=True, html='<p>x</p>', caller='test')
            self.assertEqual(rec.get('reason'), 'dry run', rec)
        finally:
            OM._log = keep
        self.assertFalse(any(r.get('sent') for r in logged))

    def test_the_key_facing_belt(self):
        # the belt takes the attribution clause and keeps the figure, and it
        # drops a clause that names a vendor outright; the two lines stay
        out = G.scrub_partner_text(self.read)
        self.assertIn(f"- {M.NIELSEN_LINE}", out)
        self.assertIn(f"- {M.PEACOCK_LINE}", out)
        self.assertNotIn('Per Nielsen', out)
        self.assertNotIn('12 million minutes', out)
        self.assertEqual(out.count('Nielsen'), 1)
        self.assertNotIn('xqviewdef', out)
        # a held line that opens a sentence the belt re-joins comes back whole
        mixed = 'Per Nielsen, the finale drew 4.1M viewers. ' + M.PEACOCK_LONG + ' ' + M.NIELSEN_LONG
        back = G.scrub_partner_text(mixed)
        self.assertIn(M.PEACOCK_LONG, back)
        self.assertIn(M.NIELSEN_LONG, back)
        self.assertNotIn('Per Nielsen', back)
        self.assertIsNone(re.search(r"xqviewdef", back, re.I), back)
        back = pma.scrub_user_text(mixed)
        self.assertIn(M.PEACOCK_LONG, back)
        self.assertIn(M.NIELSEN_LONG, back)
        self.assertNotIn('4.1M', back)
        self.assertEqual(G.partner_violations(M.others_part()), [])
        self.assertEqual(G.partner_violations(out), [])
        self.assertTrue(G.partner_violations('Nielsen says the finale drew 4.1M.'))
        self.assertNotIn('Nielsen', G.scrub_partner_text('The read stands. According to Nielsen, the finale drew 4.1M viewers.'))

    def test_twin_surfaces_are_unchanged(self):
        from migration import digital_twin_copy as T
        for s in (M.NIELSEN_LINE, M.NIELSEN_LONG, 'Week one drew 4.1M people, per Nielsen.'):
            self.assertNotIn('Nielsen', T.strip_calibration_language(s), s)
        self.assertTrue(T.calibration_hits(M.NIELSEN_LINE))
        with open(os.path.join(ROOT, 'migration', 'digital_twin_copy.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('shield_definitions', src)
        self.assertNotIn('OTHERS_ALLOWLIST', src)
        # a twin read sizes people; it never takes the viewing block
        out = M.ensure('On the room:\n\n1,204,553 people would watch the pilot.', {'metric_family': 'twin', 'subject': 'Pilot'},
                       question='who would watch')
        self.assertNotIn('Nielsen', out)


class Copy(unittest.TestCase):
    def test_no_dashes_no_households_no_method_words(self):
        for s in PUBLISHED:
            self.assertNotIn('\u2014', s)
            self.assertNotIn('\u2013', s)
            self.assertIsNone(re.search(r"\bhouseholds?\b|\bHHs?\b|\bhomes?\b", s, re.I), s)
            self.assertEqual(OM.banned_tokens(s), [], s)
            self.assertIsNone(re.search(r"\b(?:panel\w*|sample|survey|source[sd]?|according to|per Nielsen|meter\w*)\b", s, re.I), s)

    def test_our_unit_is_people(self):
        self.assertIn('one person, counted once', M.OTHERS_CLOSE)
        self.assertIn('at least one second of play', M.OTHERS_CLOSE)
        self.assertIn('people, not starts or minutes', M.OTHERS_CLOSE)
        self.assertIn('will not match either figure', M.OTHERS_CLOSE)
        self.assertNotIn('account', M.OTHERS_CLOSE)

    def test_peacock_says_it_has_not_published_a_definition(self):
        for s in (M.PEACOCK_LONG, M.PEACOCK_LINE):
            self.assertIn('has not published an official definition', s)
            self.assertIn('programming starts and unique accounts', s)
            self.assertIn('no minimum watch time', s)
        self.assertIn('pausing and resuming the same title on one account is not a second start', M.PEACOCK_LONG)
        self.assertIn('one minute and thirty minutes count the same', M.PEACOCK_LONG)

    def test_nothing_from_the_internal_note_but_the_facts(self):
        # no company or person behind the description, no date of it, no
        # address, and never the unconfirmed different-day recollection
        for s in PUBLISHED:
            self.assertIsNone(re.search(r"NBCU|NBCUniversal|Comcast|Universal\b|@|\b20\d\d\b|coordinator|producer said|"
                                        r"told us|we were told|different day|each day|next day|per day|recollect|unconfirmed|"
                                        r"email|forwarded", s, re.I), s)

    def test_nielsen_is_two_sentences_and_time_based(self):
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", M.NIELSEN_LONG) if x.strip()]
        self.assertLessEqual(len(sentences), 2)
        self.assertIn('time viewed rather than a per-view threshold', M.NIELSEN_LONG)
        self.assertIn('minutes', M.NIELSEN_LONG)
        self.assertIn('TV screens', M.NIELSEN_LONG)
        self.assertIn('phones and computers', M.NIELSEN_LONG)
        self.assertIn('time viewed, not views', M.NIELSEN_LINE)
        # no figure is attributed to anyone: the entries carry no numbers at all
        for s in PUBLISHED:
            self.assertIsNone(re.search(r"\d", s), s)

    def test_plain_sentences(self):
        for s in (M.NIELSEN_LONG, M.PEACOCK_LONG, M.OTHERS_CLOSE, M.NIELSEN_LINE, M.PEACOCK_LINE):
            for sent in re.split(r"(?<=[.!?])\s+", s):
                self.assertLessEqual(len(sent.split()), 40, sent)

    def test_the_standing_panel(self):
        panel = M.view_definition_panel()
        self.assertTrue(panel.startswith(M.VIEW_DEFINITION))
        for part in (M.OTHERS_HEADER, M.NIELSEN_LONG, M.PEACOCK_LONG, M.OTHERS_CLOSE):
            self.assertIn(part, panel)
        self.assertNotIn('Nielsen', M.VIEW_NOTE_SHORT)          # the short footnote stays as it was
        self.assertNotIn('Nielsen', M.VIEW_DEFINITION)
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('return _meth.view_definition_panel()', src)

    def test_the_allowlist_is_the_methodology_lines_and_nothing_else(self):
        self.assertEqual(set(M.OTHERS_ALLOWLIST), {M.NIELSEN_LONG, M.PEACOCK_LONG, M.NIELSEN_LINE, M.PEACOCK_LINE})
        # the guards themselves still name the service
        self.assertTrue(pma.contains_source_citation('Nielsen says so.'))
        self.assertTrue(G._PARTNER_VENDOR_RX.search('Nielsen says so.'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
