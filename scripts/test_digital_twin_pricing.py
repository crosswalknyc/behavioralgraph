#!/usr/bin/env python3
"""Digital Twins IQ is priced per group (Jenna 2026-11-05, verbatim: "update
digital twins pricing to be this. regardless of if it is pilot testing or
focus group these should be the rates"). Hermetic: a fake app, a FakeS3,
no network, no real company.

The card: individual $750 (1-20 questions) / $1,000 (1-40); package $500 /
$750 on a $5,000 deposit; enterprise $200 (1-40) at $20K a month on a
12-month term. A group is one focus group room or one pilot test, any
mode; charged at open to the company wallet with the room id as the run
id; staff never charged; a 1-20 group refuses the 21st question until the
upgrade is accepted and then charges the difference; a failed open refunds;
a duplicate is charged; Pop Out is not; the chat answers with the card; the
admin control writes the company attribute.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pricing as PR  # noqa: E402
from migration import digital_twin_company as CO  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twins as DT  # noqa: E402
from test_digital_twin_pilot import FakeS3, _beals, _young  # noqa: E402

KEY = 'digital-twins/carol/Jennifer_Beals_20261028_120000_abc123def0.json.gz'


def _users(plan=None, balance=5000.0, card=True, mode='prepay_only', deposit=None, **meta):
    acme = {'paying_customer': True, 'wallet_balance_usd': balance, 'billing_mode': mode, 'wallet_transactions': [],
            'wallet_lifetime_topups_usd': 10000.0, 'credit_pool': 0, 'credit_pool_used': 0}
    if card:
        acme['stripe_customer_id'] = 'cus_test'
        acme['stripe_payment_method_id'] = 'pm_test'
    if plan:
        acme[PR.PLAN_FIELD] = plan
        acme[PR.PLAN_META_FIELD] = dict(meta)
        if plan == 'package':
            acme[PR.PLAN_META_FIELD].setdefault('package_deposit_usd', deposit or 5000.0)
    return {'users': {'carol': {'email': 'carol@acme.com', 'role': 'user', 'company': 'Acme', 'billing_source': 'company', 'credits': 0,
                                'has_digital_twins_iq_access': True},
                      'jenna': {'email': 'jenna@crosswalknyc.com', 'role': 'super_admin', 'credits': -1}},
            'companies': {'Acme': acme}}


class Harness:
    """A fake app + a Flask client with carol (Acme, wallet-billed) signed in."""

    def __init__(self, test, users=None, consume_ok=True):
        try:
            import flask  # noqa: F401
        except Exception:
            test.skipTest('flask not installed in this interpreter')
        import flask
        import digital_twins_api as A
        from migration import digital_twin_synthesis as SYN
        from prometheus.host import host
        self.A = A
        self.users = users if users is not None else _users()
        self.s3 = FakeS3()
        self.s3.list_objects_v2 = lambda **kw: {'Contents': [{'Key': k, 'LastModified': '2026'} for k in list(self.s3.objs) if k.startswith(kw.get('Prefix', ''))]}
        twin = _beals()
        twin['meta']['created_by'] = 'carol'
        twin['meta']['key'] = KEY
        SYN.save_twin(self.s3, KEY, twin)
        self.user = {'username': 'carol', 'email': 'carol@acme.com', 'role': 'user', 'company': 'Acme', 'billing_source': 'company',
                     'has_digital_twins_iq_access': True, 'credits': 0}
        self.charges, self.refunds = [], []
        self.consume_ok = consume_ok

        def consume(uname, **k):
            if callable(self.consume_ok):
                ok = self.consume_ok(k)
            else:
                ok = bool(self.consume_ok)
            if ok:
                self.charges.append(dict(k, username=uname))
            return ok

        def refund(uname, **k):
            self.refunds.append(dict(k, username=uname))
            return True
        self.app_mod = types.SimpleNamespace(s3_client=self.s3, get_current_user=lambda: self.user, _normalize_role=lambda r: r,
                                             consume_credit=consume, refund_credit=refund, load_users=lambda: self.users)
        sys.modules['app'] = self.app_mod
        CO.set_users_loader(lambda: self.users)
        host.bind(gate=lambda allow_api_key=False: (self.user, None))
        app = flask.Flask('t')
        app.secret_key = 'x'
        A.register(app)
        self.c = app.test_client()
        with self.c.session_transaction() as sess:
            sess['username'] = 'carol'

    def as_staff(self):
        self.user = {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'role': 'super_admin', 'has_digital_twins_iq_access': True, 'credits': -1}
        with self.c.session_transaction() as sess:
            sess['username'] = 'jenna'
        return self

    def twin(self):
        from migration import digital_twin_synthesis as SYN
        return SYN.load_twin(self.s3, KEY)

    def close(self):
        CO.set_users_loader(None)


# ----------------------------------------------------------------- the card
class RateCard(unittest.TestCase):
    def test_every_plan_and_tier(self):
        self.assertEqual(PR.rate('individual', 20), 750.0)
        self.assertEqual(PR.rate('individual', 40), 1000.0)
        self.assertEqual(PR.rate('package', 20), 500.0)
        self.assertEqual(PR.rate('package', 40), 750.0)
        self.assertEqual(PR.rate('enterprise', 40), 200.0)
        self.assertEqual(PR.rate('enterprise', 20), 200.0)          # enterprise has one tier: 1-40
        self.assertEqual(PR.upgrade_rate('individual'), 250.0)
        self.assertEqual(PR.upgrade_rate('package'), 250.0)
        self.assertEqual(PR.upgrade_rate('enterprise'), 0.0)
        self.assertEqual(PR.tiers_for('enterprise'), (40,))
        self.assertEqual(PR.default_tier('individual'), 20)
        self.assertEqual(PR.default_tier('enterprise'), 40)
        self.assertEqual((PR.PACKAGE_DEPOSIT_USD, PR.ENTERPRISE_MONTHLY_USD, PR.ENTERPRISE_TERM_MONTHS, PR.INDIVIDUAL_BALANCE_FLOOR_USD), (5000.0, 20000.0, 12, 2000.0))
        for v in ('1-20', '20', 20, 'small', None, ''):
            self.assertEqual(PR.normalize_tier(v, 'individual'), 20, v)
        for v in ('1-40', '40', 40, 'large', 25):
            self.assertEqual(PR.normalize_tier(v, 'package'), 40, v)
        self.assertEqual(PR.normalize_plan('Package Rate'), 'package')
        self.assertEqual(PR.normalize_plan('nonsense'), 'individual')

    def test_live_price_function_wins_and_falls_back(self):
        self.assertEqual(PR.rate('individual', 20, lambda k, d: 700.0 if k == 'digital_twin_group_individual_20' else d), 700.0)
        self.assertEqual(PR.rate('package', 40, lambda k, d: 0), 750.0)
        self.assertEqual(PR.rate('package', 40, lambda k, d: (_ for _ in ()).throw(RuntimeError('x'))), 750.0)

    def test_wallet_defaults_and_catalog_match_the_table(self):
        import wallet as w
        pt = w.DEFAULT_PRICING['per_tool_usd']
        for (plan, tier), key in PR.TOOL_KEYS.items():
            self.assertEqual(pt[key], PR.RATES[(plan, tier)], key)
            self.assertEqual(w.pull_type_to_tool_key(PR.PULL_TYPES[key]), key)
        for plan, key in PR.UPGRADE_TOOL_KEYS.items():
            self.assertEqual(pt[key], PR.UPGRADE_RATES[plan], key)
        self.assertEqual(pt['digital_twins_iq'], 0.0)                       # the twin build carries no charge
        catalog = {row[0]: row for row in w.MODULE_CATALOG}
        for key in list(PR.TOOL_KEYS.values()) + list(PR.UPGRADE_TOOL_KEYS.values()):
            self.assertIn(key, catalog)
            self.assertEqual(catalog[key][4], PR.RATES.get(next(k for k, v in PR.TOOL_KEYS.items() if v == key), None) if key in PR.TOOL_KEYS.values() else PR.UPGRADE_RATES['individual'])
        for old in PR.RETIRED_KEYS:
            self.assertIn(old, w.RETIRED_TOOL_KEYS, old)
            self.assertNotIn(old, pt, old)
            self.assertEqual(w.should_charge_wallet({'paying_customer': True, 'wallet_balance_usd': 9999.0}, old)[1], 'retired')
        self.assertEqual(DT.PRICING['digital_twin_group_individual_40'], 1000.0)
        self.assertEqual(DT.PRICING['digital_twins_iq'], 0.0)
        for key in PR.PULL_TYPES:
            self.assertEqual(DT.PULL_TYPES[key], PR.PULL_TYPES[key])
            self.assertIn(key, PR.CREDITS)

    def test_card_lines_and_quote(self):
        lines = PR.rate_card_lines()
        blob = '\n'.join(lines)
        for s in ('$750 a group for 1-20 questions, $1,000 a group for 1-40', '$500 a group for 1-20 questions, $750 a group for 1-40, on a $5,000 deposit',
                  '$200 a group, 1-40 questions, at $20,000 a month on a 12-month term', 'Same rates for both', 'A duplicate or a recut is a new group', '$2,000 balance'):
            self.assertIn(s, blob)
        self.assertNotIn('\u2014', blob)
        self.assertIn('$750 (1-20 questions, individual groups), charged when it opens', PR.quote_line('individual', 20))
        self.assertIn('move it to 1-40 for $250', PR.quote_line('package', 20))
        self.assertNotIn('move it', PR.quote_line('enterprise', 40))
        twin_lines = DT.pricing_lines()
        self.assertTrue(twin_lines[0].startswith('Digital Twin - included'))
        self.assertTrue(any(l.startswith('Individual groups - $750') for l in twin_lines))
        self.assertFalse(any('5,500' in l or 'panelist' in l.lower() for l in twin_lines))
        how = DT.how_to()
        for it in how['items']:
            if it['id'] in ('panel', 'pilot', 'trailer', 'titles', 'greenlight'):
                self.assertIn('$750 a group for 1-20 questions', it['cost'], it['id'])
                self.assertNotIn('5,500', it['cost'])
        self.assertIn('Included', next(i for i in how['items'] if i['id'] == 'twin')['cost'])


# ------------------------------------------------------------------ the plan
class Plan(unittest.TestCase):
    def test_default_package_enterprise_and_lapses(self):
        data = _users()
        self.assertEqual(PR.plan_for_company('Acme', data), 'individual')
        self.assertEqual(PR.plan_for_company('acme', data), 'individual')
        self.assertEqual(PR.plan_for_company('Nobody', data), 'individual')
        rec = PR.set_company_plan(data, 'Acme', 'package', by='jenna')
        self.assertEqual((rec['plan'], rec['active_plan'], rec['active']), ('package', 'package', True))
        self.assertEqual(data['companies']['Acme'][PR.PLAN_META_FIELD]['package_deposit_usd'], 5000.0)
        self.assertEqual(PR.plan_for_company('Acme', data), 'package')
        data['companies']['Acme']['wallet_balance_usd'] = 0.0            # the deposit is drawn down: the package lapses
        self.assertEqual(PR.plan_for_company('Acme', data), 'individual')
        self.assertFalse(PR.plan_record(data['companies']['Acme'])['active'])
        rec = PR.set_company_plan(data, 'Acme', 'enterprise', by='jenna', enterprise_started_at='2026-11-05')
        meta = data['companies']['Acme'][PR.PLAN_META_FIELD]
        self.assertEqual((meta['enterprise_monthly_usd'], meta['enterprise_term_months'], meta['enterprise_started_at'], meta['enterprise_ends_at']),
                         (20000.0, 12, '2026-11-05', '2027-11-05'))
        self.assertNotIn('package_deposit_usd', meta)
        self.assertEqual(PR.plan_for_company('Acme', data), 'enterprise')
        meta['enterprise_ends_at'] = '2026-01-01'                          # the term ended
        self.assertEqual(PR.plan_for_company('Acme', data), 'individual')
        PR.set_company_plan(data, 'Acme', 'individual', by='jenna')
        self.assertEqual(data['companies']['Acme'][PR.PLAN_META_FIELD].get('enterprise_ends_at'), None)
        with self.assertRaises(ValueError):
            PR.set_company_plan(data, 'Nobody', 'package')

    def test_plan_follows_the_seat_company_and_own_seats(self):
        data = _users(plan='package')
        self.assertEqual(PR.plan_for_user(data['users']['carol'], 'carol', data), 'package')
        data['users']['solo'] = {'email': 'solo@x.com', 'role': 'user', PR.PLAN_FIELD: 'enterprise',
                                 PR.PLAN_META_FIELD: {'enterprise_ends_at': '2099-01-01'}}
        self.assertEqual(PR.plan_for_user(data['users']['solo'], 'solo', data), 'enterprise')
        PR.set_company_plan(data, 'user:solo', 'individual')
        self.assertEqual(PR.plan_for_user(data['users']['solo'], 'solo', data), 'individual')

    def test_a_deposit_turns_on_the_package(self):
        import wallet as w
        rec = {'paying_customer': True, 'wallet_balance_usd': 100.0, 'wallet_transactions': []}
        w.apply_wallet_topup(rec, 4999.0)
        self.assertNotIn(PR.PLAN_FIELD, rec)                              # under the package size: nothing changes
        w.apply_wallet_topup(rec, 5000.0)
        self.assertEqual(rec[PR.PLAN_FIELD], 'package')
        self.assertEqual(rec[PR.PLAN_META_FIELD]['package_deposit_usd'], 5000.0)
        self.assertEqual(rec[PR.PLAN_META_FIELD]['package_activated_by'], 'deposit')
        self.assertEqual(PR.plan_record(rec)['active_plan'], 'package')
        ent = {'paying_customer': True, 'wallet_balance_usd': 0.0, 'wallet_transactions': [], PR.PLAN_FIELD: 'enterprise',
               PR.PLAN_META_FIELD: {'enterprise_ends_at': '2099-01-01'}}
        w.apply_wallet_topup(ent, 6000.0)
        self.assertEqual(ent[PR.PLAN_FIELD], 'enterprise')                # an active enterprise term is left alone
        w.apply_wallet_refund(rec, 5000.0)
        self.assertEqual(rec[PR.PLAN_META_FIELD]['package_deposit_usd'], 5000.0)   # a refund is not a deposit


# ------------------------------------------------------- the open charge
class OpenCharge(unittest.TestCase):
    def _open_room(self, h, tier=None, title='Season 2 pricing'):
        body = {'title': title}
        if tier is not None:
            body['tier'] = tier
        return h.c.post(f'/api/digital-twins/{KEY}/panel/open', json=body)

    def test_individual_room_charges_750_or_1000_to_the_company_wallet(self):
        h = Harness(self)
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            sess = r.get_json()['session']
            self.assertEqual(len(h.charges), 1)
            ch = h.charges[0]
            self.assertEqual(ch['pull_type'], 'Digital Twins IQ Group (Individual, 1-20 questions)')
            self.assertEqual(ch['job_id'], sess['id'])                 # the run id is the room id
            self.assertEqual(ch['credits_used'], PR.CREDITS['digital_twin_group_individual_20'])
            self.assertIn('Season 2 pricing', ch['description'])     # usage history carries the group title
            self.assertEqual(sess['group_charge']['usd'], 750.0)
            self.assertEqual((sess['group_tier'], sess['questions_paid'], sess['group_plan']), (20, 20, 'individual'))
            stored = h.twin()['panel_sessions'][0]
            self.assertEqual(stored['group_charge']['job_id'], sess['id'])
            import wallet as w
            self.assertEqual(w.pull_type_to_tool_key(ch['pull_type']), 'digital_twin_group_individual_20')
            self.assertEqual(w.tool_price_usd('digital_twin_group_individual_20'), 750.0)
            r2 = self._open_room(h, tier=40, title='The big room')
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Individual, 1-40 questions)')
            self.assertEqual(r2.get_json()['session']['group_charge']['usd'], 1000.0)
            # the same empty room again is reused, not charged twice
            r3 = self._open_room(h)
            self.assertTrue(r3.get_json()['reused'])
            self.assertEqual(len(h.charges), 2)
        finally:
            h.close()

    def test_package_and_enterprise_rates(self):
        h = Harness(self, users=_users(plan='package'))
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Package, 1-20 questions)')
            self.assertEqual(r.get_json()['session']['group_charge']['usd'], 500.0)
            r = self._open_room(h, tier=40, title='Forty')
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Package, 1-40 questions)')
            self.assertEqual(r.get_json()['session']['group_charge']['usd'], 750.0)
        finally:
            h.close()
        h = Harness(self, users=_users(plan='enterprise', enterprise_ends_at='2099-01-01'))
        try:
            r = self._open_room(h, tier=20)                              # enterprise has one tier
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Enterprise, 1-40 questions)')
            sess = r.get_json()['session']
            self.assertEqual((sess['group_charge']['usd'], sess['group_tier']), (200.0, 40))
        finally:
            h.close()

    def test_staff_never_charged(self):
        h = Harness(self).as_staff()
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(h.charges, [])
            sess = r.get_json()['session']
            self.assertTrue(sess['group_charge']['free'])
            self.assertEqual(sess['group_charge']['usd'], 0.0)
            self.assertTrue(PR.group_paid(sess))
            r2 = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Pilot', 'mode': 'pilot'})
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(h.charges, [])
            self.assertTrue(PR.seat_is_free(h.user, 'jenna'))
        finally:
            h.close()

    def test_pilot_opens_as_a_group_and_play_charges_nothing_more(self):
        h = Harness(self)
        try:
            r = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Season 2 pilot', 'mode': 'pilot', 'cut_title': 'Pilot', 'runtime_s': 600, 'tier': 40})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            sess = r.get_json()['session']
            self.assertEqual(len(h.charges), 1)
            self.assertEqual(h.charges[0]['pull_type'], 'Digital Twins IQ Group (Individual, 1-40 questions)')
            self.assertEqual(h.charges[0]['job_id'], sess['id'])
            self.assertEqual((sess['group_tier'], sess['question_cap'], sess['group_charge']['usd']), (40, 40, 1000.0))
            self.assertFalse(sess['play_charged'])
            r2 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{sess["id"]}/play', json={})
            self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True)[:300])
            self.assertEqual(len(h.charges), 1)                          # Play charges nothing more
            self.assertTrue(r2.get_json()['session']['play_charged'])
            # any mode is one group at the same rate (a concept test)
            r3 = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Concept', 'mode': 'concept', 'text': 'A chef returns to her hometown diner after a scandal.'})
            self.assertEqual(r3.status_code, 200, r3.get_data(as_text=True)[:300])
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Individual, 1-20 questions)')
            self.assertEqual(r3.get_json()['session']['group_charge']['usd'], 750.0)
        finally:
            h.close()

    def test_duplicate_and_recut_are_new_groups_pop_out_is_not(self):
        h = Harness(self)
        try:
            r = self._open_room(h)
            sid = r.get_json()['session']['id']
            n = len(h.charges)
            r2 = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/duplicate', json={})
            self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True)[:300])
            dup = r2.get_json()['session']
            self.assertEqual(len(h.charges), n + 1)
            self.assertEqual(h.charges[-1]['job_id'], dup['id'])
            self.assertEqual(dup['group_charge']['kind'], 'duplicate')
            # reading the room (what a popped-out window does) charges nothing
            for _ in range(3):
                self.assertEqual(h.c.get(f'/api/digital-twins/{KEY}/panel/{sid}').status_code, 200)
            self.assertEqual(h.c.get(f'/api/digital-twins/{KEY}').status_code, 200)
            self.assertEqual(len(h.charges), n + 1)
            # a recut (cut B on a pilot room) is a new group
            r3 = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Cut A', 'mode': 'pilot', 'cut_title': 'Cut A', 'runtime_s': 600})
            psid = r3.get_json()['session']['id']
            n = len(h.charges)
            r4 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{psid}/recut', json={})
            self.assertEqual(r4.status_code, 200, r4.get_data(as_text=True)[:300])
            self.assertEqual(len(h.charges), n + 1)
            self.assertEqual(r4.get_json()['session']['group_charge']['kind'], 'recut')
            self.assertEqual(h.charges[-1]['job_id'], r4.get_json()['session']['id'])
        finally:
            h.close()

    def test_a_failed_open_refunds_and_a_never_started_pilot_refunds_on_delete(self):
        h = Harness(self)
        try:
            from migration import digital_twins as _DT
            real_save = _DT.save_twin
            calls = {'n': 0}

            def boom(s3, key, payload):
                calls['n'] += 1
                raise RuntimeError('s3 down')
            _DT.save_twin = boom
            try:
                r = self._open_room(h)
            finally:
                _DT.save_twin = real_save
            self.assertEqual(r.status_code, 500)
            self.assertEqual(len(h.charges), 1)
            self.assertEqual(len(h.refunds), 1)
            self.assertEqual(h.refunds[0]['job_id'], h.charges[0]['job_id'])
            self.assertEqual(h.refunds[0]['credits'], h.charges[0]['credits_used'])
            self.assertEqual(h.refunds[0]['amount_usd'], 750.0)
            self.assertEqual(h.twin().get('panel_sessions') or [], [])            # nothing saved
            # a pilot opened and deleted before Play hands the group back; one that played does not
            r2 = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Never started', 'mode': 'pilot', 'cut_title': 'X', 'runtime_s': 300})
            sid = r2.get_json()['session']['id']
            n_ch, n_rf = len(h.charges), len(h.refunds)
            r3 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid}/delete', json={})
            self.assertEqual(r3.status_code, 200, r3.get_data(as_text=True)[:300])
            self.assertEqual(len(h.refunds), n_rf + 1)
            self.assertEqual(h.refunds[-1]['job_id'], sid)
            r4 = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Played', 'mode': 'pilot', 'cut_title': 'Y', 'runtime_s': 300})
            sid4 = r4.get_json()['session']['id']
            h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid4}/play', json={})
            n_rf = len(h.refunds)
            h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid4}/delete', json={})
            self.assertEqual(len(h.refunds), n_rf)
            # a room deleted before its first question hands the group back too
            r5 = self._open_room(h, title='Empty room')
            sid5 = r5.get_json()['session']['id']
            n_rf = len(h.refunds)
            h.c.post(f'/api/digital-twins/{KEY}/panel/{sid5}/delete', json={})
            self.assertEqual(len(h.refunds), n_rf + 1)
            self.assertEqual(h.refunds[-1]['job_id'], sid5)
        finally:
            h.close()

    def test_no_funds_blocks_the_open_and_nothing_is_saved(self):
        h = Harness(self, consume_ok=False)
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 402)
            self.assertIn('$750', r.get_json()['error'])
            self.assertEqual(h.twin().get('panel_sessions') or [], [])
            self.assertEqual(h.charges, [])
        finally:
            h.close()

    def test_the_card_and_balance_gate(self):
        h = Harness(self, users=_users(card=False))
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 402, r.get_data(as_text=True)[:300])
            out = r.get_json()
            self.assertTrue(out.get('needs_card'))
            self.assertEqual(out['add_funds_url'], '/wallet')
            self.assertIn('card on file', out['error'])
            self.assertIn('$2,000', out['error'])                          # the recommended floor, in the copy
            self.assertEqual(h.charges, [])
        finally:
            h.close()
        h = Harness(self, users=_users(balance=600.0))
        try:
            r = self._open_room(h)                                         # $600 covers nothing at $750
            self.assertEqual(r.status_code, 402)
            self.assertTrue(r.get_json().get('needs_funds'))
            self.assertEqual(h.charges, [])
            r = self._open_room(h, tier=20)
            self.assertEqual(r.status_code, 402)
            h.users['companies']['Acme']['wallet_balance_usd'] = 760.0      # the hard gate is the group being opened, not $2,000
            CO.reset_cache()
            r = self._open_room(h)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(len(h.charges), 1)
        finally:
            h.close()
        h = Harness(self, users=_users(balance=600.0, plan='package'))       # the package plan has no card gate, still the balance
        try:
            r = self._open_room(h)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])   # $600 covers a $500 package group
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group (Package, 1-20 questions)')
        finally:
            h.close()

    def test_twin_build_carries_no_charge(self):
        from prometheus import twin_url_list as TUL
        from prometheus import twin_named_pull as TNP
        self.assertEqual(TUL.TWIN_CREDITS, 0)
        self.assertTrue(TUL.twin_included())
        pr = TUL.price()
        self.assertEqual((pr['twin_usd'], pr['total_usd']), (0.0, 300.0))
        self.assertIn('the twin and its first two runs are included', pr['line'])
        calls = []
        charged, err = TNP.charge({'role': 'user'}, 'carol', 'Testy', pull=False, twin_job_id='j1',
                                  consume_credit=lambda *a, **k: calls.append(k) or True, is_free=lambda u, n: False)
        self.assertIsNone(err)
        self.assertEqual((charged, calls), ({'profile': 0, 'twin': 0}, []))
        charged, err = TUL.charge_both({'role': 'user'}, 'carol', 'Testy', profile_run_id='r1', twin_job_id='j1',
                                       consume_credit=lambda *a, **k: calls.append(k) or True, is_free=lambda u, n: False)
        self.assertIsNone(err)
        self.assertEqual(charged, {'profile': TUL.PROFILE_CREDITS, 'twin': 0})
        self.assertEqual(len(calls), 1)                                     # the audience build only
        self.assertEqual(calls[0]['pull_type'], TUL.PROFILE_PULL_TYPE)


# --------------------------------------------------------- the cap and the upgrade
class CapAndUpgrade(unittest.TestCase):
    def _room_with(self, h, asked, tier=20):
        r = h.c.post(f'/api/digital-twins/{KEY}/panel/open', json={'title': 'Capped', 'tier': tier})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        sid = r.get_json()['session']['id']
        twin = h.twin()
        sess = next(s for s in twin['panel_sessions'] if s['id'] == sid)
        sess['questions'] = [{'id': f'q{i}', 'q': f'Question {i}?', 'kind': 'yes_no', 'read': 'Half would.', 'stage': 'done'} for i in range(asked)]
        from migration import digital_twin_synthesis as SYN
        SYN.save_twin(h.s3, KEY, twin)
        return sid

    def test_a_1_20_group_refuses_the_21st_until_the_upgrade_then_charges_the_difference(self):
        h = Harness(self)
        try:
            sid = self._room_with(h, 20)
            n = len(h.charges)
            r = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/ask', json={'q': 'Would you pay $12.99?'})
            self.assertEqual(r.status_code, 402, r.get_data(as_text=True)[:300])
            out = r.get_json()
            self.assertEqual((out['upgrade']['from_tier'], out['upgrade']['to_tier'], out['upgrade']['usd']), (20, 40, 250.0))
            self.assertEqual((out['questions_asked'], out['question_cap']), (20, 20))
            self.assertEqual(len(h.charges), n)                             # nothing charged, nothing asked
            self.assertEqual(len(next(s for s in h.twin()['panel_sessions'] if s['id'] == sid)['questions']), 20)
            # accepted on the question itself: the difference is charged, the room moves to 1-40, the question runs
            r2 = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/ask', json={'q': 'Would you pay $12.99?', 'accept_upgrade': True})
            self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True)[:300])
            self.assertEqual(len(h.charges), n + 1)
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group Upgrade (Individual, to 1-40 questions)')
            self.assertEqual(h.charges[-1]['job_id'], f'{sid}-upgrade')
            self.assertEqual((r2.get_json()['question_cap'], r2.get_json()['group_tier']), (40, 40))
            sess = next(s for s in h.twin()['panel_sessions'] if s['id'] == sid)
            self.assertEqual(sess['group_upgrades'][0]['usd'], 250.0)
            self.assertEqual(len(sess['questions']), 21)
        finally:
            h.close()

    def test_the_upgrade_route_and_the_forty_cap(self):
        h = Harness(self, users=_users(plan='package'))
        try:
            sid = self._room_with(h, 20)
            r = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/upgrade', json={})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group Upgrade (Package, to 1-40 questions)')
            self.assertEqual(r.get_json()['question_cap'], 40)
            n = len(h.charges)
            r2 = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/upgrade', json={})    # already 1-40: nothing more
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(len(h.charges), n)
            sid40 = self._room_with(h, 40, tier=40)
            r3 = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid40}/ask', json={'q': 'One more?'})
            self.assertEqual(r3.status_code, 409)
            self.assertTrue(r3.get_json().get('cap'))
            self.assertIn('40 questions', r3.get_json()['error'])
            r4 = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid40}/ask', json={'q': 'One more?', 'accept_upgrade': True})
            self.assertEqual(r4.status_code, 409)                           # nothing to upgrade to
        finally:
            h.close()

    def test_enterprise_has_one_tier_and_no_offer(self):
        h = Harness(self, users=_users(plan='enterprise', enterprise_ends_at='2099-01-01'))
        try:
            sid = self._room_with(h, 20, tier=20)
            r = h.c.post(f'/api/digital-twins/{KEY}/panel/{sid}/ask', json={'q': 'Twenty-first?'})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])     # enterprise groups are 1-40
            self.assertEqual(r.get_json()['question_cap'], 40)
        finally:
            h.close()

    def test_pilot_questions_count_toward_the_tier(self):
        h = Harness(self)
        try:
            r = h.c.post(f'/api/digital-twins/{KEY}/pilot/open', json={'title': 'Pilot', 'mode': 'pilot', 'cut_title': 'P', 'runtime_s': 600})
            sid = r.get_json()['session']['id']
            h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid}/play', json={})
            twin = h.twin()
            sess = next(s for s in twin['pilot_sessions'] if s['id'] == sid)
            sess['questions'] = [{'id': f'q{i}', 'q': f'Q{i}?', 'kind': 'yes_no', 'read': 'x', 'stage': 'done'} for i in range(20)]
            from migration import digital_twin_synthesis as SYN
            SYN.save_twin(h.s3, KEY, twin)
            n = len(h.charges)
            r2 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid}/ask', json={'q': 'Would you keep watching?', 'playhead_s': 120})
            self.assertEqual(r2.status_code, 402, r2.get_data(as_text=True)[:300])
            self.assertEqual(r2.get_json()['upgrade']['usd'], 250.0)
            self.assertEqual(len(h.charges), n)
            r3 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid}/upgrade', json={})
            self.assertEqual(r3.status_code, 200, r3.get_data(as_text=True)[:300])
            self.assertEqual(h.charges[-1]['pull_type'], 'Digital Twins IQ Group Upgrade (Individual, to 1-40 questions)')
            self.assertEqual(r3.get_json()['session']['question_cap'], 40)
            r4 = h.c.post(f'/api/digital-twins/{KEY}/pilot/{sid}/ask', json={'q': 'Would you keep watching?', 'playhead_s': 120})
            self.assertEqual(r4.status_code, 200, r4.get_data(as_text=True)[:300])
        finally:
            h.close()

    def test_legacy_sessions_read_as_paid_1_40_groups(self):
        sess = P.open_session([_beals()], in_room=12, title='Old room', created_by='carol', session_id='old1')
        P.mark_paid(sess, 12)                                               # paid under the session card
        self.assertTrue(PR.legacy_paid(sess))
        self.assertTrue(PR.group_paid(sess))
        self.assertEqual(PR.question_cap(sess, 'individual'), 40)
        self.assertEqual(PR.question_gate(sess, 'individual')[0], 'ok')
        fresh = P.open_session([_beals()], in_room=12, title='New room', created_by='carol', session_id='new1')
        self.assertFalse(PR.group_paid(fresh))
        self.assertEqual(PR.question_cap(fresh, 'individual'), 20)
        pil = PI.open_session([_beals()], title='Old pilot', created_by='carol', session_id='op1')
        PI.mark_play_paid(pil, 12)
        self.assertTrue(PR.group_paid(pil))
        self.assertEqual(PR.question_cap(pil), 40)


# ------------------------------------------------------- the chat and the admin
class ChatAndAdmin(unittest.TestCase):
    def test_the_chat_pricing_answer_carries_the_card(self):
        from prometheus import product_facts as PF
        for q in ('what does a focus group cost', 'how much is a pilot test?', 'Digital Twins pricing', 'what is the price per group'):
            self.assertEqual(PF.family_of(q), 'twin_pricing', q)
            out = PF.answer(q)
            self.assertIn('$750 a group for 1-20 questions, $1,000 a group for 1-40', out['reply'])
            self.assertIn('$500 a group for 1-20 questions, $750 a group for 1-40, on a $5,000 deposit', out['reply'])
            self.assertIn('$200 a group, 1-40 questions, at $20,000 a month on a 12-month term', out['reply'])
            self.assertNotIn('5,500', out['reply'])
            self.assertNotIn('\u2014', out['reply'])
        for q in ('how much does a profile cost', 'what can you do', 'can I upload a list of names', 'price a brand partnership'):
            self.assertNotEqual(PF.family_of(q), 'twin_pricing', q)
        from prometheus import rate_card as RC
        card = RC.render('$')
        self.assertIn('Digital Twins IQ focus group or pilot test (one group, any mode) - individual groups $750 a group for 1-20 questions or $1,000 for 1-40', card)
        self.assertIn('package rate $500 / $750 a group on a $5,000 deposit', card)
        self.assertIn('enterprise $200 a group (1-40) at $20,000 a month', card)
        self.assertNotIn('5,500', card)
        self.assertNotIn('panelist', card)

    def test_the_twin_lane_answers_a_pricing_ask(self):
        from prometheus import digital_twin_lane as L
        from prometheus.host import host
        user = {'username': 'carol', 'role': 'user', 'company': 'Acme'}
        CO.set_users_loader(lambda: _users(plan='package'))
        try:
            host.bind(gate=lambda allow_api_key=False: (user, None))
            text = L.pricing_answer(user)
            self.assertIn('$500 a group for 1-20 questions', text)
            self.assertIn('Your company is on package rate', text)
            self.assertIn('$500 (1-20 questions, package rate), charged when it opens', text)
        finally:
            CO.set_users_loader(None)

    def test_admin_plan_control_writes_the_company_attribute(self):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
            app_src = fh.read()
        self.assertIn("/api/admin/companies/<path:company_name>/digital-twins-plan", app_src)
        i = app_src.find('def api_set_company_digital_twins_plan')
        self.assertGreater(i, 0)
        blk = app_src[i:i + 3000]
        self.assertIn('PR.set_company_plan(data, company_name, plan', blk)
        self.assertIn('_users_cas_mutate(_set)', blk)
        with open(os.path.join(ROOT, 'templates', 'admin.html'), encoding='utf-8') as fh:
            admin = fh.read()
        for s in ('id="twinPlanSelect"', 'digital-twins-plan', '_cdSaveTwinPlan', 'id="cdTwinPlanLabel"', 'option value="enterprise"', 'id="twinPlanMonths"'):
            self.assertIn(s, admin, s)
        # the write itself, as the route does it
        data = _users()
        out = PR.set_company_plan(data, 'Acme', 'enterprise', by='jenna', enterprise_monthly_usd=20000, enterprise_term_months=12, enterprise_started_at='2026-11-05')
        self.assertEqual(data['companies']['Acme'][PR.PLAN_FIELD], 'enterprise')
        self.assertEqual(out['status'], 'Enterprise: $20,000 a month, 12-month term, through 2027-11-05.')

    def test_page_copy_and_partner_docs_carry_the_card(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        for s in ('id="dtwPnTier"', 'id="dtwPiTier"', 'dtwPanelUpgrade', 'dtwPilotUpgrade', 'S.card = d.group_card', "/upgrade', { method: 'POST'",
                  'Digital Twins focus group or pilot test (one group, 1-40 questions)', '$750 a group for 1-20 questions or $1,000 for 1-40; $500 / $750 on a $5,000 package deposit'):
            self.assertIn(s, page, s)
        self.assertNotIn("'$5,500 a session with 12 questions, then $5,500 for each further 12'", page)
        self.assertNotIn("$5,500 a session (same as Focus Group IQ), charged on first Play", page)
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        for s in ('/panel/<sid>/upgrade', '/pilot/<sid>/upgrade', "'group_card': _group_card(user)", 'def _question_gate', 'PR.charge_group_open'):
            self.assertIn(s, api, s)


if __name__ == '__main__':
    unittest.main(verbosity=1)
