#!/usr/bin/env python3
"""Unlimited seats: included with your plan (Jenna 2026-11-08).

Jenna, verbatim: "for anyone with unlimited credit it just should say
included with your plan instead of giving a price or staff chip."

An unlimited seat (credits -1, the unlimited / no_cap flags, billing_mode
unlimited, or an unlimited company pool with no ceiling on the seat) and
Crosswalk staff never see an offer: no price, no Approve chip, no staff
chip. The reply opens "Included with your plan." and the read starts in
the same turn; the charge stays at zero. A paying seat still reads the
price and the Approve / Cancel chips.

Hermetic: a stub host carrying its own users document (the live-row
lookup never imports the web app), a stub analyze core, no model, no S3.
Covered:
  1. the one gate (priced_read.offer_or_charge): unlimited and staff ->
     charged at zero with `included`; a payer -> the offer
  2. the sizing lane (service_turn) opens with the line and runs the read
  3. service.ask on the analyze surface: a custom ask runs in the same turn
     for an unlimited seat (one core call, the included opener, no chips);
     the payer gets the quote and the chips and no core call; staff reads
     the same as unlimited
  4. the pricing question answers "Included with your plan." for an
     unlimited seat; the payer still reads the card
  5. product facts (twin pricing, valuation, bulk list, overview) carry no
     price for an included seat
  6. a Digital Twins group open for an unlimited company charges nothing
     and the per-group quote reads included
  7. every other priced surface: the talent list offer, the Brand
     Partnership recap and Accept chip, the re-run card, the bulk read-back,
     the twin list brief, the rate card, the rerun charge
  8. the wiring in chat.py, app.py and the pricing modal
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

from prometheus import priced_read as PR  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402
from prometheus import product_facts as PF  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402

USERS = {
    'unlim': {'username': 'unlim', 'email': 'unlim@acme.com', 'company': 'Acme', 'credits': -1, 'paying_customer': True},
    'flagged': {'username': 'flagged', 'email': 'f@acme.com', 'company': 'Acme', 'credits': 40, 'unlimited': True},
    'pooled': {'username': 'pooled', 'email': 'p@acme.com', 'company': 'Acme', 'credits': 0},
    'payer': {'username': 'payer', 'email': 'payer@studio.com', 'company': 'Studio', 'credits': 100, 'paying_customer': True},
    'jenna': {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'company': 'Crosswalk', 'credits': 50},
}
POOL_UNLIMITED = {'pooled'}
PRICE = re.compile(r"[$\u00a3]\s?\d")
STAFF_CHIP = re.compile(r"not billed to a staff account", re.I)


def _check_credits(u):
    rec = USERS.get(u) or {}
    if rec.get('credits') == -1 or rec.get('unlimited') or u in POOL_UNLIMITED:
        return True, -1
    return (rec.get('credits', 0) or 0) > 0, rec.get('credits', 0) or 0


CHARGES = []


def _consume(username, **kw):
    CHARGES.append((username, dict(kw)))
    return True


def _bind_host(analyze_calls):
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        analyze_calls.append(('analyze', text, dict(body)))
        return {'success': True, 'action': 'answer', 'read_job_id': 'j1',
                'reply': 'On it: reading the Gilmore Girls viewers now. The numbers land right here.', 'followups': []}

    def _interpret_core(user, body, text, history):
        analyze_calls.append(('interpret', text, dict(body)))
        return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

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
              load_users=lambda: {'users': dict(USERS)}, check_user_credits=_check_credits,
              consume_credit=_consume,
              pricing_question=lambda t: bool(re.search(r"\b(?:cost|costs|price|pricing|rate card|charge)\b", str(t or ''), re.I)),
              pricing_copy_for=lambda u: RC.render('$'),
              pricing_copy=RC.render('$'))
    return host


def _user(name):
    return dict(USERS[name])


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        _bind_host(cls.calls)

    def test_seat_recognition(self):
        for name in ('unlim', 'flagged', 'pooled', 'jenna'):
            self.assertTrue(PR.included_with_plan(_user(name), name, None), name)
            self.assertTrue(PR.included_with_plan({'username': name}, name, None), name + ' (thin session object)')
        self.assertFalse(PR.included_with_plan(_user('payer'), 'payer', None))
        self.assertFalse(PR.included_with_plan({'username': 'payer'}, 'payer', None))
        self.assertTrue(PR.seat_is_free({'username': 'pooled'}, 'pooled', None))   # the unlimited company pool

    def test_unlimited_seat_skips_the_offer(self):
        CHARGES.clear()
        for name in ('unlim', 'flagged', 'pooled'):
            state, payload = PR.offer_or_charge(text='top 10 QSRs for Gilmore Girls viewers', subject='Gilmore Girls viewers',
                                                kind='custom', panel_confirm=None, username=name, user=_user(name), history=[])
            self.assertEqual(state, 'charged', name)
            self.assertTrue(payload.get('included'), name)
            self.assertEqual(payload.get('credits'), 0, name)
            self.assertEqual(payload.get('label'), PR.INCLUDED_LABEL, name)
            self.assertNotIn('reply', payload)
            self.assertNotIn('followups', payload)
        self.assertEqual(CHARGES, [])

    def test_staff_seat_reads_the_same(self):
        state, payload = PR.offer_or_charge(text='top 10 QSRs for Gilmore Girls viewers', subject='Gilmore Girls viewers',
                                            kind='custom', panel_confirm=None, username='jenna', user=_user('jenna'), history=[])
        self.assertEqual(state, 'charged')
        self.assertTrue(payload.get('included'))
        self.assertEqual(payload.get('credits'), 0)
        # the confirm path for a staff seat too: zero, no staff chip anywhere
        state2, payload2 = PR.offer_or_charge(text='top 10 QSRs for Gilmore Girls viewers', subject='Gilmore Girls viewers',
                                              kind='custom', panel_confirm={'kind': 'custom', 'subject': 'Gilmore Girls viewers'},
                                              username='jenna', user=_user('jenna'), history=[])
        self.assertEqual(state2, 'charged')
        self.assertEqual(payload2.get('credits'), 0)
        self.assertTrue(payload2.get('included'))

    def test_paying_seat_still_gets_the_price_and_the_approve_chip(self):
        state, payload = PR.offer_or_charge(text='top 10 QSRs for Gilmore Girls viewers', subject='Gilmore Girls viewers',
                                            kind='custom', panel_confirm=None, username='payer', user=_user('payer'), history=[])
        self.assertEqual(state, 'offer')
        self.assertTrue(payload['reply'].endswith(PR.COST_LINE.format(price='$100')))
        self.assertEqual(payload['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
        self.assertTrue(payload.get('panel_offer'))
        self.assertNotIn(PR.INCLUDED_LINE, payload['reply'])

    def test_sizing_lane_opens_with_the_line_and_runs(self):
        real = SZ.answer
        SZ.answer = lambda *a, **k: {'success': True, 'action': 'answer', 'followups': [],
                                     'reply': 'Chipotle: 1,204,117 of the 10 million; 39,723,820 US people in the window.'}
        try:
            out = PR.service_turn('how big is Chipotle in the dataset', user=_user('unlim'), ctx=None, history=[], host=None)
            self.assertTrue(out['reply'].startswith(PR.INCLUDED_LINE + ' Chipotle: 1,204,117'), out['reply'])
            self.assertEqual(out['followups'], [])
            self.assertFalse(out.get('panel_offer'))
            self.assertTrue(out['priced_paid']['included'])
            self.assertEqual(out['priced_paid']['usd'], 0)
            self.assertIsNone(PRICE.search(out['reply']), out['reply'])
            # the payer is quoted first
            pay = PR.service_turn('how big is Chipotle in the dataset', user=_user('payer'), ctx=None, history=[], host=None)
            self.assertTrue(pay.get('panel_offer'))
            self.assertEqual(pay['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
            self.assertIn('$100', pay['reply'])
        finally:
            SZ.answer = real

    def test_interpret_sizing_precheck_has_no_quote_for_an_included_seat(self):
        self.assertIsNone(PR.sizing_offer_payload('how big is Chipotle in the dataset', [], 'unlim', user=_user('unlim')))
        pay = PR.sizing_offer_payload('how big is Chipotle in the dataset', [], 'payer', user=_user('payer'))
        self.assertTrue(pay and pay.get('panel_offer'))

    def test_included_charge_shape_and_opener(self):
        c = PR.included_charge('unlim', 'Gilmore Girls viewers', 'custom', 'Panel Report', '$100')
        self.assertEqual((c['credits'], c['label'], c['included']), (0, PR.INCLUDED_LABEL, True))
        self.assertEqual(PR.with_included_line('On it.'), 'Included with your plan. On it.')
        self.assertEqual(PR.with_included_line(PR.with_included_line('On it.')), 'Included with your plan. On it.')
        self.assertEqual(PR.with_included_line(''), PR.INCLUDED_LINE)


class ServiceEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        _bind_host(cls.calls)
        from prometheus import service
        cls.service = service

    def _ask(self, name, text, surface='analyze'):
        self.calls.clear()
        CHARGES.clear()
        return self.service.ask(_user(name), {'text': text, 'surface': surface, 'client': 'dashboard', 'history': []})

    ASK = 'top 10 QSRs for Gilmore Girls viewers'

    def test_unlimited_seat_runs_the_read_in_the_same_turn(self):
        env, st = self._ask('unlim', self.ASK)
        self.assertEqual(st, 200)
        raw = env['raw']
        self.assertEqual(len(self.calls), 1, self.calls)
        self.assertEqual(self.calls[0][0], 'analyze')
        self.assertTrue(raw['reply'].startswith('Included with your plan. On it: reading the Gilmore Girls viewers now.'), raw['reply'])
        self.assertTrue(raw.get('included_with_plan'))
        self.assertEqual(raw.get('followups') or [], [])
        self.assertFalse(raw.get('panel_offer'))
        self.assertIsNone(PRICE.search(raw['reply']), raw['reply'])
        self.assertIsNone(STAFF_CHIP.search(str(raw)), raw)
        self.assertEqual(CHARGES, [])
        self.assertNotEqual(env['decision']['reason'], 'custom_ask_offer')

    def test_paying_seat_gets_the_quote_and_the_chips(self):
        env, _st = self._ask('payer', self.ASK)
        raw = env['raw']
        self.assertEqual(env['decision']['reason'], 'custom_ask_offer')
        self.assertEqual(self.calls, [])
        self.assertIn('Pulling this data will cost $100.', raw['reply'])
        self.assertEqual(raw['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
        self.assertNotIn(PR.INCLUDED_LINE, raw['reply'])
        self.assertEqual(CHARGES, [])

    def test_staff_seat_reads_included_too(self):
        env, _st = self._ask('jenna', self.ASK)
        raw = env['raw']
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(raw['reply'].startswith(PR.INCLUDED_LINE), raw['reply'])
        self.assertIsNone(STAFF_CHIP.search(str(raw)), raw)
        self.assertEqual(raw.get('followups') or [], [])
        self.assertEqual(CHARGES, [])

    def test_pricing_question_answers_included(self):
        env, _st = self._ask('unlim', 'what does a custom ask cost?')
        raw = env['raw']
        self.assertEqual(env['decision']['reason'], 'product_fact')
        self.assertEqual(raw['reply'], RC.INCLUDED_CARD)
        self.assertTrue(raw['reply'].startswith(PR.INCLUDED_LINE))
        self.assertIsNone(PRICE.search(raw['reply']), raw['reply'])
        self.assertEqual(raw['followups'], [])
        self.assertEqual(self.calls, [])
        # the payer still reads the card
        env2, _st2 = self._ask('payer', 'what does a custom ask cost?')
        self.assertEqual(env2['decision']['reason'], 'product_fact')
        self.assertIn('Pricing is:', env2['raw']['reply'])
        self.assertIsNotNone(PRICE.search(env2['raw']['reply']))

    def test_pricing_question_on_the_interpret_surface(self):
        env, _st = self._ask('unlim', 'how much does a profile cost', surface='interpret')
        self.assertEqual(env['decision']['reason'], 'product_fact')
        self.assertIn(PR.INCLUDED_LINE, str(env['raw'].get('error') or env['raw'].get('reply') or ''))
        self.assertIsNone(PRICE.search(str(env['raw'])), env['raw'])


class ProductFacts(unittest.TestCase):
    def test_twin_pricing_valuation_bulk_and_overview(self):
        for q in ('what does a focus group cost', 'how do I value a brand partnership', 'can I upload a list of names',
                  'what can you do'):
            out = PF.answer(q, price_fn=None, included=True)
            self.assertIsNotNone(out, q)
            self.assertIsNone(PRICE.search(out['reply']), (q, out['reply']))
            self.assertIn('included with your plan', out['reply'].lower(), q)
            # the payer's answer still carries a price on the priced families
            pay = PF.answer(q, price_fn=None)
            if pay['family'] in ('twin_pricing', 'valuation', 'bulk_list', 'overview'):
                self.assertIsNotNone(PRICE.search(pay['reply']), (q, pay['reply']))
        twin = PF.answer('what does a focus group cost', included=True)
        self.assertTrue(twin['reply'].startswith(PR.INCLUDED_LINE))

    def test_rate_card_included(self):
        card = RC.render(included=True)
        self.assertEqual(card, RC.INCLUDED_CARD)
        self.assertTrue(card.startswith('Included with your plan.'))
        self.assertIsNone(PRICE.search(card))
        self.assertIn('Pricing is:', RC.render('$'))


class DigitalTwins(unittest.TestCase):
    def test_group_open_for_an_unlimited_company_charges_nothing(self):
        from migration import digital_twin_pricing as DTP
        sess = {'id': 'room1', 'title': 'The Office pilot', 'questions': []}
        calls = []
        out = DTP.charge_group_open(_user('unlim'), {'meta': {'name': 'The Office'}}, sess, plan='individual', tier=20,
                                    charge_fn=lambda key, desc, job_id: calls.append((key, desc, job_id)) or None,
                                    username='unlim', kind='pilot')
        self.assertIsNone(out)
        self.assertEqual(calls, [])
        gc = sess['group_charge']
        self.assertTrue(gc['free'])
        self.assertEqual((gc['credits'], gc['usd']), (0, 0.0))
        self.assertTrue(DTP.group_paid(sess))
        # a paying seat is charged through charge_fn
        sess2 = {'id': 'room2', 'title': 'x', 'questions': []}
        DTP.charge_group_open(_user('payer'), {'meta': {'name': 'y'}}, sess2, plan='individual', tier=20,
                              charge_fn=lambda key, desc, job_id: calls.append((key, desc, job_id)) or None, username='payer', kind='panel')
        self.assertEqual(len(calls), 1)
        self.assertFalse(sess2['group_charge']['free'])

    def test_group_quote_reads_included(self):
        from migration import digital_twin_pricing as DTP
        line = DTP.quote_line('individual', 20, included=True, kind='pilot')
        self.assertTrue(line.startswith(PR.INCLUDED_LINE), line)
        self.assertIn('This pilot test is one group', line)
        self.assertIsNone(PRICE.search(line), line)
        self.assertIsNotNone(PRICE.search(DTP.quote_line('individual', 20)))
        from prometheus import digital_twin_lane as DTL
        self.assertEqual(DTL.group_quote(_user('unlim'), 'group'), PR.INCLUDED_LINE)
        ans = DTL.pricing_answer(_user('unlim'))
        self.assertTrue(ans.startswith(PR.INCLUDED_LINE))
        self.assertIsNone(PRICE.search(ans), ans)
        self.assertIsNotNone(PRICE.search(DTL.group_quote(_user('payer'), 'group')))


class OtherSurfaces(unittest.TestCase):
    def test_talent_list_offer(self):
        from prometheus import talent_list_job as TL
        reply, chips, off = TL.offer('influencers', 'have a product line', True, included=True)
        self.assertTrue(reply.endswith(PR.INCLUDED_LINE), reply)
        self.assertEqual(chips[0], 'Pull the list of influencers who have a product line')
        self.assertIsNone(PRICE.search(reply + ' '.join(chips)))
        self.assertTrue(off['included'])
        self.assertTrue(TL._CHIP_RX.match(chips[0]))
        r2, c2, _o2 = TL.offer('influencers', 'have a product line', True)
        self.assertIn('Pulling this data will cost', r2)
        self.assertTrue(TL._CHIP_RX.match(c2[0]))

    def test_brand_partnership_recap(self):
        from prometheus import bpiq_wizard as BW
        d = {'talent': 'Gunna', 'campaign': 'Nike Air', 'campaign_start': '2026-09-01', 'campaign_end': '2026-09-30',
             'attr_start': '2026-09-01', 'attr_end': '2026-10-30', 'control_group': True, 'asset_mode': 'find', 'step': BW.STEP_CHARGE}
        lines = BW.recap_lines(dict(d, included=True))
        self.assertEqual(lines[-1], PR.INCLUDED_LINE)
        self.assertIsNone(PRICE.search('\n'.join(lines)))
        reply, chips = BW.step_prompt(dict(d, included=True))
        self.assertEqual(chips, [BW.CHIP_ACCEPT, BW.CHIP_DECLINE, BW.CHIP_CANCEL])
        self.assertIn(PR.INCLUDED_LINE, reply)
        reply2, chips2 = BW.step_prompt(d)
        self.assertEqual(chips2[0], 'Accept $1,000')
        self.assertIn('This is $1,000.', reply2)

    def test_rerun_card_and_charge(self):
        from prometheus import continuation as C
        deliv = {'subject': 'Gilmore Girls', 'kind': 'read', 'question': 'how many people watched Gilmore Girls'}
        out = C.rerun_confirm(deliv, ('2026-01-01', '2026-03-31'), '$100', included=True)
        self.assertTrue(out['reply'].startswith(PR.INCLUDED_LINE), out['reply'])
        self.assertIsNone(PRICE.search(out['reply']))
        self.assertEqual(out['rerun_confirm']['price_label'], 'Included with your plan')
        self.assertTrue(out['rerun_confirm']['included'])
        pay = C.rerun_confirm(deliv, ('2026-01-01', '2026-03-31'), '$100')
        self.assertIn('$100', pay['reply'])
        self.assertEqual(pay['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
        nowin = C.rerun_confirm(deliv, None, '$100', included=True)
        self.assertIsNone(PRICE.search(nowin['reply']), nowin['reply'])
        self.assertIn('included with your plan', nowin['reply'])

    def test_bulk_and_twin_list_briefs(self):
        from prometheus import bulk_pull as BP
        from prometheus import twin_url_list as TUL
        rows = [{'state': 'queued'}, {'state': 'queued'}, {'state': 'reused'}]
        read = {'items': ['a', 'b', 'c']}
        inc = BP.confirm_reply(read, rows, BP.default_window(), 'unlim', file_name='names.csv', included=True)
        self.assertIn('Price: included with your plan', inc)
        self.assertIsNone(PRICE.search(inc), inc)
        pay = BP.confirm_reply(read, rows, BP.default_window(), 'payer', file_name='names.csv')
        self.assertIn('charged now', pay)
        self.assertIsNotNone(PRICE.search(pay))
        line = TUL.price('unlim', included=True)['line']
        self.assertIn('included with your plan', line)
        self.assertIsNone(PRICE.search(line), line)
        self.assertIsNotNone(PRICE.search(TUL.price('payer')['line']))

    def test_rerun_charge_is_free_for_an_included_seat(self):
        _bind_host([])
        import flask
        app = flask.Flask('t')
        with app.app_context():
            flask.g._pm_rerun_charge = {'subject': 'Gilmore Girls', 'window': ['2026-01-01', '2026-03-31'], 'price_key': 'panel_report',
                                        'price_label': 'Included with your plan', 'included': True}
            calls = []
            self.assertEqual(PR.charge_rerun('unlim', 'Gilmore Girls', lambda *a, **k: calls.append(1) or True), {})
            self.assertEqual(calls, [])


class Wiring(unittest.TestCase):
    def test_chat_app_and_modal(self):
        chat = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn("if _state != 'charged':\n                _pm_ask_hint(outcome='panel_offer', subject=subj_name)", chat)
        self.assertIn("if isinstance(panel_charge, dict) and panel_charge.get('included'):", chat)
        self.assertIn("def _pm_pricing_copy(username=None, user=None):", chat)
        self.assertIn("_pm_pricing_copy(_pm_ask_log_user(''), user)", chat)
        self.assertIn("sizing_offer_payload(text, history, _pm_ask_log_user(''), user=user)", chat)
        service = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertIn("if body.get('_pm_included') and isinstance(raw, dict) and raw.get('success') and raw.get('reply'):", service)
        self.assertIn("_rcp.render(included=True)", service)
        self.assertIn("included=_pf_incl", service)
        app_src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertIn("seat_included=_seat_included,", app_src)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn('{% if _pm_incl %}Included with your plan{% else %}$300{% endif %}', html)
        self.assertEqual(html.count('Included with your plan'), 11)
        api = open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8').read()
        self.assertIn("'labels': {str(t): PR.INCLUDED_LABEL for t in PR.tiers_for(plan)}", api)
        for path in ('prometheus/priced_read.py', 'prometheus/unprojected.py', 'prometheus/rate_card.py', 'prometheus/product_facts.py'):
            src = open(os.path.join(ROOT, path), encoding='utf-8').read()
            self.assertNotIn('\u2014', src, path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
