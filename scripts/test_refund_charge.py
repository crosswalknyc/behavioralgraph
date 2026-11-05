#!/usr/bin/env python3
"""refund_charge: a refund restores the unit the charge took (2026-11-05).

Hermetic. Covers a credits seat, a company credit pool, a dollar-wallet
seat, a company wallet billed through a seat, the idempotent retry (same
job + reason, and the never-past-the-charge guard), the unknown-unit
default (seat's current billing mode, flagged), the bulk per-row refund
against a list charge, the partial refund proration, the app.py wrapper
+ run-id stamp, and a source assertion that nobody writes a refund
outside the helper.

    REGRESSION_TEST_MODE=1 python3 scripts/test_refund_charge.py
"""
from __future__ import annotations

import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import refund_charge as rc  # noqa: E402
import wallet  # noqa: E402


def _hist(desc, job_id, credits, usd=None, pull_type='Profile Analysis', **extra):
    row = {'used_at': '2026-11-01T10:00:00Z', 'description': desc, 'job_id': job_id,
           'pull_type': pull_type, 'credits_used': credits}
    if usd is not None:
        row['wallet_charged_usd'] = usd
        row['wallet_tool_key'] = extra.pop('tool', 'profile_iq_build')
    row.update(extra)
    return row


def _deduct(desc, job_id, usd, balance_after, via=''):
    t = {'ts': '2026-11-01T10:00:00Z', 'kind': 'deduct', 'amount_usd': -usd, 'balance_after_usd': balance_after,
         'description': desc, 'job_id': job_id, 'tool': 'profile_iq_build', 'stripe_ref': ''}
    if via:
        t['billed_via_username'] = via
    return t


def _data():
    return {
        'users': {
            'cred': {'credits': 10, 'credits_used': 5, 'credit_usage_history': [_hist('Profile IQ: X', 'r1', 5)]},
            'pooled': {'credits': 0, 'credits_used': 5, 'company': 'PoolCo',
                       'credit_usage_history': [_hist('Profile IQ: P', 'r2', 5)]},
            'dollar': {'credits': 0, 'credits_used': 5, 'paying_customer': True, 'billing_mode': 'prepay_only',
                       'wallet_balance_usd': 725.0,
                       'wallet_transactions': [_deduct('Profile IQ: Y', 'r3', 275.0, 725.0)],
                       'credit_usage_history': [_hist('Profile IQ: Y', 'r3', 5, usd=275.0)]},
            'seat': {'credits': 0, 'credits_used': 5, 'company': 'Acme', 'billing_source': 'company',
                     'credit_usage_history': [_hist('Profile IQ: Z', 'r4', 5, usd=275.0)]},
            'bulk': {'credits': 0, 'credits_used': 15, 'paying_customer': True, 'billing_mode': 'auto_reload',
                     'wallet_balance_usd': 175.0,
                     'wallet_transactions': [_deduct('Profile IQ list: 3 profiles', 'bulk1', 825.0, 175.0)],
                     'credit_usage_history': [_hist('Profile IQ list: 3 profiles', 'bulk1', 15, usd=825.0,
                                                    wallet_quantity=3)]},
            'unlim': {'credits': -1, 'credits_used': 5, 'credit_usage_history': [_hist('Profile IQ: U', 'r5', 5)]},
        },
        'companies': {
            'PoolCo': {'credit_pool': 100, 'credit_pool_used': 20},
            'Acme': {'paying_customer': True, 'billing_mode': 'auto_reload', 'wallet_balance_usd': 1000.0,
                     'wallet_transactions': [_deduct('Profile IQ: Z', 'r4', 275.0, 1000.0, via='seat')]},
        },
    }


class UnitRestored(unittest.TestCase):
    def test_credits_seat_gets_credits_back(self):
        d = _data()
        r = rc.apply_refund(d, 'cred', job_id='r1', credits=5, reason='engine refused')
        self.assertTrue(r['refunded'])
        self.assertEqual(r['unit'], rc.UNIT_CREDITS)
        self.assertEqual(d['users']['cred']['credits'], 15)
        self.assertEqual(d['users']['cred']['credits_used'], 0)
        row = d['users']['cred']['credit_usage_history'][0]
        self.assertEqual((row['pull_type'], row['job_id'], row['credits_used']), ('refund', 'r1', -5))
        self.assertEqual(row['description'], 'REFUND: engine refused')
        self.assertNotIn('amount_usd', row, 'a credits refund never fakes a dollar figure')

    def test_company_pool_gets_its_counter_back(self):
        d = _data()
        r = rc.apply_refund(d, 'pooled', job_id='r2', credits=5, reason='x')
        self.assertEqual(r['unit'], rc.UNIT_POOL)
        self.assertEqual(d['companies']['PoolCo']['credit_pool_used'], 15)
        self.assertEqual(d['users']['pooled']['credits'], 0, 'the seat itself holds no credits')
        self.assertEqual(d['users']['pooled']['credits_used'], 0)

    def test_dollar_seat_gets_wallet_balance_back_not_credits(self):
        d = _data()
        r = rc.apply_refund(d, 'dollar', job_id='r3', credits=5, reason='build failed')
        u = d['users']['dollar']
        self.assertEqual(r['unit'], rc.UNIT_WALLET)
        self.assertEqual(r['usd'], 275.0)
        self.assertEqual(u['wallet_balance_usd'], 1000.0)
        self.assertEqual(u['credits'], 0, 'a dollar seat never gains a free credit from a refund')
        self.assertEqual(u['credits_used'], 0)
        txn = u['wallet_transactions'][0]
        self.assertEqual((txn['kind'], txn['amount_usd'], txn['job_id'], txn['balance_after_usd']),
                         ('refund', 275.0, 'r3', 1000.0))
        self.assertEqual(txn['description'], 'REFUND: build failed')
        row = u['credit_usage_history'][0]
        self.assertEqual((row['amount_usd'], row['wallet_charged_usd'], row['refund_unit']), (-275.0, -275.0, 'wallet'))

    def test_company_wallet_restored_with_the_seat_stamped(self):
        d = _data()
        r = rc.apply_refund(d, 'seat', job_id='r4', credits=5, reason='stopped')
        self.assertEqual(r['unit'], rc.UNIT_COMPANY_WALLET)
        self.assertEqual(r['subject_key'], 'Acme')
        c = d['companies']['Acme']
        self.assertEqual(c['wallet_balance_usd'], 1275.0)
        self.assertEqual(c['wallet_transactions'][0]['billed_via_username'], 'seat')
        self.assertEqual(c['wallet_transactions'][0]['job_id'], 'r4')
        self.assertEqual(d['users']['seat']['credits'], 0)
        self.assertEqual(d['users']['seat']['credit_usage_history'][0]['wallet_subject'], 'Acme')
        rows, spend = wallet.credit_usage_for_modal(d['users']['seat'], 'seat', d)
        self.assertEqual(spend, 0.0, 'the modal nets the charge and the refund')

    def test_unlimited_seat_only_unwinds_the_counter(self):
        d = _data()
        r = rc.apply_refund(d, 'unlim', job_id='r5', credits=5, reason='x')
        self.assertEqual(r['unit'], rc.UNIT_UNLIMITED)
        self.assertEqual(d['users']['unlim']['credits'], -1)
        self.assertEqual(d['users']['unlim']['credits_used'], 0)


class Idempotent(unittest.TestCase):
    def test_same_job_and_reason_refunds_once(self):
        d = _data()
        rc.apply_refund(d, 'dollar', job_id='r3', credits=5, reason='build failed')
        r = rc.apply_refund(d, 'dollar', job_id='r3', credits=5, reason='build failed')
        self.assertTrue(r['already_refunded'])
        self.assertFalse(r['refunded'])
        self.assertEqual(d['users']['dollar']['wallet_balance_usd'], 1000.0)
        self.assertEqual(len(d['users']['dollar']['wallet_transactions']), 2)

    def test_never_past_the_charge_even_with_another_reason(self):
        d = _data()
        rc.apply_refund(d, 'cred', job_id='r1', credits=5, reason='first')
        r = rc.apply_refund(d, 'cred', job_id='r1', credits=5, reason='second')
        self.assertTrue(r['already_refunded'])
        self.assertEqual(d['users']['cred']['credits'], 15)

    def test_wallet_row_is_a_second_guard(self):
        d = _data()
        rc.apply_refund(d, 'dollar', job_id='r3', credits=5, reason='build failed')
        # Lose the history row (a stale writer); the wallet refund row still blocks a double credit.
        d['users']['dollar']['credit_usage_history'] = [h for h in d['users']['dollar']['credit_usage_history']
                                                        if h['pull_type'] != 'refund']
        r = rc.apply_refund(d, 'dollar', job_id='r3', credits=5, reason='build failed')
        self.assertTrue(r['refunded'], 'the history row is rewritten')
        self.assertEqual(d['users']['dollar']['wallet_balance_usd'], 1000.0, 'but the balance moved once')
        self.assertEqual(len([t for t in d['users']['dollar']['wallet_transactions'] if t['kind'] == 'refund']), 1)


class PartialAndBulk(unittest.TestCase):
    def test_bulk_rows_refund_against_the_list_charge(self):
        d = _data()
        for i in range(3):
            r = rc.apply_refund(d, 'bulk', job_id=f'run{i}', credits=5, reason='row failed',
                                charge_job_id='bulk1', amount_usd=275.0)
            self.assertTrue(r['refunded'], i)
            self.assertEqual(r['unit'], rc.UNIT_WALLET)
        self.assertEqual(d['users']['bulk']['wallet_balance_usd'], 1000.0)
        r = rc.apply_refund(d, 'bulk', job_id='run3', credits=5, reason='row failed',
                            charge_job_id='bulk1', amount_usd=275.0)
        self.assertTrue(r['already_refunded'], 'a fourth row cannot refund past the three charged')
        self.assertEqual(d['users']['bulk']['wallet_balance_usd'], 1000.0)
        rows = d['users']['bulk']['credit_usage_history']
        self.assertEqual(rows[0]['charge_job_id'], 'bulk1')

    def test_partial_credits_prorate_the_dollars(self):
        d = _data()
        r = rc.apply_refund(d, 'bulk', job_id='bulk1', credits=5, reason='one row')
        self.assertEqual(r['usd'], 275.0)
        self.assertEqual(d['users']['bulk']['wallet_balance_usd'], 450.0)


class UnknownUnit(unittest.TestCase):
    def test_no_charge_record_uses_the_seats_current_mode_and_flags_it(self):
        d = _data()
        r = rc.apply_refund(d, 'cred', job_id='ghost', credits=2, reason='legacy')
        self.assertTrue(r['refunded'])
        self.assertTrue(r['unit_assumed'])
        self.assertEqual(r['unit'], rc.UNIT_CREDITS)
        self.assertEqual(d['users']['cred']['credits'], 12)
        self.assertTrue(d['users']['cred']['credit_usage_history'][0].get('refund_unit_assumed'))

    def test_dollar_seat_without_record_takes_the_given_dollars(self):
        d = _data()
        r = rc.apply_refund(d, 'dollar', job_id='ghost', credits=5, reason='legacy', amount_usd=300.0)
        self.assertTrue(r['unit_assumed'])
        self.assertEqual(r['unit'], rc.UNIT_WALLET)
        self.assertEqual(d['users']['dollar']['wallet_balance_usd'], 1025.0)
        self.assertEqual(d['users']['dollar']['credits'], 0)

    def test_description_locates_a_job_less_charge(self):
        d = _data()
        d['users']['dollar']['credit_usage_history'][0]['job_id'] = ''
        d['users']['dollar']['wallet_transactions'][0]['job_id'] = ''
        r = rc.apply_refund(d, 'dollar', job_id='run-late', credits=5, reason='engine rejected',
                            charge_description='Profile IQ: Y')
        self.assertFalse(r['unit_assumed'])
        self.assertEqual(r['unit'], rc.UNIT_WALLET)
        self.assertEqual(d['users']['dollar']['wallet_balance_usd'], 1000.0)

    def test_writer_notifies_ops_when_the_unit_was_assumed(self):
        d = _data()
        notes = []

        def mutate(fn):
            return fn(d)
        r = rc.refund_charge('cred', 'ghost', 1, 'legacy', mutate=mutate, notify=notes.append, log=lambda *_: None)
        self.assertTrue(r['refunded'])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['username'], 'cred')
        r2 = rc.refund_charge('cred', 'r1', 5, 'known', mutate=mutate, notify=notes.append, log=lambda *_: None)
        self.assertTrue(r2['refunded'])
        self.assertEqual(len(notes), 1, 'a charge with a record never alerts')

    def test_unknown_user_writes_nothing(self):
        d = _data()
        r = rc.apply_refund(d, 'nobody', job_id='r1', credits=5, reason='x')
        self.assertFalse(r['refunded'])
        self.assertEqual(r['unit'], rc.UNIT_NONE)


class AppWrapper(unittest.TestCase):
    """app.py's refund_credit is a thin wrapper and the v1 route stamps
    the run id back onto the charge. Source-level, no Flask boot."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
            cls.src = fh.read()

    def test_refund_credit_delegates(self):
        i = self.src.index('def refund_credit(')
        body = self.src[i:self.src.index('\ndef ', i + 10)]
        self.assertIn('import refund_charge as _rc', body)
        self.assertIn('_rc.refund_charge(', body)
        self.assertIn('mutate=_users_cas_mutate', body)
        self.assertIn('notify=_refund_unit_assumed_alert', body)
        self.assertNotIn("user['credits'] =", body, 'the wrapper no longer moves balances itself')

    def test_v1_run_stamps_and_passes_the_description(self):
        i = self.src.index('def api_v1_profiles_run(')
        body = self.src[i:i + 40000]
        self.assertIn("payload['charge_description'] = _v1_charge_desc", body)
        self.assertIn('_stamp_charge_run_id(username, _v1_charge_desc', body)
        self.assertEqual(body.count('charge_description=_v1_charge_desc'), 3, 'all three pre-run refunds locate the charge')

    def test_alert_uses_the_ops_email(self):
        i = self.src.index('def _refund_unit_assumed_alert(')
        body = self.src[i:self.src.index('\ndef ', i + 10)]
        self.assertIn('_chatbot_error_email(', body)


class OnlyTheHelperWritesRefunds(unittest.TestCase):
    """Nobody but refund_charge.py moves a balance for a refund."""

    def _py_files(self):
        skip = ('scripts/', 'tests/', '_backups', 'migration/_')
        for base, _dirs, files in os.walk(ROOT):
            if '/.git' in base or '/node_modules' in base or '/_backups' in base:
                continue
            for f in files:
                if not f.endswith('.py'):
                    continue
                p = os.path.join(base, f)
                rel = os.path.relpath(p, ROOT)
                if rel.startswith(skip):
                    continue
                yield rel, p

    def test_apply_wallet_refund_only_from_the_helper_or_stripe_webhook(self):
        offenders = []
        for rel, p in self._py_files():
            with open(p, encoding='utf-8', errors='ignore') as fh:
                src = fh.read()
            for m in re.finditer(r'apply_wallet_refund\(', src):
                line = src[:m.start()].count('\n') + 1
                if rel == 'wallet.py' and 'def apply_wallet_refund' in src.splitlines()[line - 1]:
                    continue
                if rel in ('refund_charge.py',):
                    continue
                # billing_routes reconciles a STRIPE refund (money left the
                # card) onto the wallet; that is a payment event, not a
                # charge refund.
                if rel == 'billing_routes.py' and '_handle_charge_refunded' in src[max(0, m.start() - 1200):m.start()]:
                    continue
                offenders.append(f'{rel}:{line}')
        self.assertEqual(offenders, [])

    def test_no_handwritten_refund_history_rows(self):
        offenders = []
        for rel, p in self._py_files():
            if rel == 'refund_charge.py':
                continue
            with open(p, encoding='utf-8', errors='ignore') as fh:
                src = fh.read()
            for m in re.finditer(r"""['"]pull_type['"]\s*:\s*['"]refund['"]""", src):
                line = src[:m.start()].count('\n') + 1
                ctx = src[max(0, m.start() - 600):m.start() + 200]
                # Readers (dedupe / display) mention the literal in comparisons, not in a row they insert.
                if 'history.insert' in ctx or 'credit_usage_history' in ctx and '.insert(' in ctx:
                    offenders.append(f'{rel}:{line}')
        self.assertEqual(offenders, [])

    def test_callers_pass_job_ids(self):
        with open(os.path.join(ROOT, 'prometheus', 'bulk_feeder.py'), encoding='utf-8') as fh:
            bf = fh.read()
        self.assertIn('charge_job_id=bulk_id', bf)
        self.assertIn('amount_usd=usd or None', bf)
        with open(os.path.join(ROOT, 'prometheus', 'twin_url_list.py'), encoding='utf-8') as fh:
            tul = fh.read()
        self.assertIn("def refund(uname, credits, reason, refund_credit=None, job_id='')", tul)
        with open(os.path.join(ROOT, 'prometheus', 'offers.py'), encoding='utf-8') as fh:
            off = fh.read()
        self.assertIn('job_id=f"offer-{oid}-{i}"', off)
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            dta = fh.read()
        self.assertEqual(dta.count('job_id=f"{sid}-seats"'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
