#!/usr/bin/env python3
"""A paying reader approves a priced build inside the Prometheus app
(Jenna 2026-11-17: "Yes, build in-app approval for paying users").

Until now a paying seat read its price on the app's brief card and was
sent to the dashboard to approve. The brief is now held on the server
for the reader and the conversation, the card carries its id, and the
approval goes through the ask door and runs the dashboard's own approve
step on the stored order.

  APayingSeatApproves        the card carries an id and the quoted
                             price; one approval starts one build and
                             charges the quoted price once; a second
                             submit answers with the first result.
  NothingInTheRequestDecides a price, an order, a user, a company or a
                             switch in the request body changes nothing.
  ItIsThatReadersBrief       an id from another reader, another
                             conversation or thin air is refused.
  ItExpires                  after a day the brief is refused in plain
                             words, with no charge.
  ShortFunds                 the wallet's own refusal line, nothing
                             started, nothing charged, the card stays.
  AnIncludedSeat             no price anywhere, never asked to pay.
  ARepeatIsAnswered          an order already delivered or running is
                             answered, not run or charged again.
  TheTermsMoved              a price that changed since the quote starts
                             nothing.
  OneClickOneApproval        an approval in flight is not run twice; a
                             lost write is tried again.
  ATypedYes                  "approve" binds only to the one brief that
                             is the last thing said; else it asks which.
  Dismissing                 closes the brief for good.
  APartnerKeyCannot          a key that is not the app gets no id and
                             cannot approve here; its contract is as it
                             was.
  AProbeLeavesNothing        nothing on record, nothing started,
                             nothing charged.
  WhereTheAppStillCannot     a brief with a question open, or a profile
                             that already exists, carries no id.
  WhatTheReaderIsTold        every line: plain, no dash, no inside word.
  ThePage                    the card's buttons, and that it closes in
                             every end state (run in Node).
  TheWiring                  where the pieces sit.

Hermetic. The approve step is the one in the chat module's source, and
the charge functions are the dashboard's own, both lifted by the suites
that already test them (test_pm_repeat_approval_guard.py,
test_plan_allowance_included.py) and run here against a store, an engine
and a users file held in memory. No model, no live store, no mail.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('PM_PROBE_LOOKUP', '0')       # no sandbox-key lookup over the network
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = Path(os.path.abspath(__file__)).parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location('pm_' + name.replace('.py', '') + '_for_app_briefs', HERE / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RAW = _load('test_pm_repeat_approval_guard.py')     # the approve core, lifted; the engine; the store
PA = _load('test_plan_allowance_included.py')       # the dashboard's charge functions, lifted

import flask  # noqa: E402
import wallet as W  # noqa: E402

from prometheus import api_meter as METER  # noqa: E402
from prometheus import contract_v1 as C  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus import guards as GUARDS  # noqa: E402
from prometheus import legacy as LEGACY  # noqa: E402
from prometheus import partner_api as PAPI  # noqa: E402
from prometheus import pending_briefs as PB  # noqa: E402
from prometheus import plan_allowance as PLAN  # noqa: E402
from prometheus import repeat_approval as RA  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus.host import host  # noqa: E402

APP = flask.Flask('pm_app_briefs_test')
APP.secret_key = 'test-only'
BUCKET = RAW.BUCKET
THREADS = 'system/pm_threads'
APP_AGENT = PB.APP_UA + '1.4.2'
PARTNER_AGENT = 'python-requests/2.32.3'
BUILD_USD = float(PA.PRICING['per_tool_usd']['api_chatbot_profile_iq_build'])
CUT_USD = float(PA.PRICING['per_tool_usd'].get('api_profile_iq_cut') or 0)
PAGE = ROOT / 'prometheus' / 'desktop_ui' / 'index.html'
CHAT = _legacy_view()
NODE = shutil.which('node')
DASH_RX = re.compile('[\u2014\u2013]')
INSIDE_RX = re.compile(r"\b(synth\w*|modell?ed|estimated|pipeline|queue[ds]?|workers?|engine|build server|hostmap|claude|"
                       r"anthropic|credits?|run id|spec|draft id|token)\b", re.I)
FBI = 'FBI Most Wanted Viewers'
FBI2 = 'FBI International Viewers'


def money(v):
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


# ------------------------------------------------------------------ the people
def _company(balance, **over):
    rec = {'paying_customer': True, 'unlimited': False, 'credit_pool': 0, 'credit_pool_used': 0,
           'billing_mode': 'prepay_only', 'default_spend_scope': '*', 'wallet_balance_usd': float(balance),
           'wallet_transactions': []}
    rec.update(over)
    return rec


def _seat(username, company, **over):
    rec = PA._seat(username, company=company, email=f"{username.replace(':', '.')}@client.example", first_name=username.title(),
                   last_name='Reader', prometheus_mode='both', api_key_enabled=True,
                   api_key_sha256=hashlib.sha256(username.encode()).hexdigest())
    rec.update(over)
    return rec


def users():
    return {
        'users': {
            'payer': _seat('payer', 'Acme Studios'),
            'colleague': _seat('colleague', 'Acme Studios'),
            'shorty': _seat('shorty', 'Thin Co'),
            'ben': _seat('ben', 'Rate Card Co'),
            'naomi': _seat('naomi', PA.COMPANY),
            'liz': _seat('liz', '', billing_source='user', role='super_admin', email='liz@crosswalknyc.com', paying_customer=False,
                         credits=-1),
            'reader_only': _seat('reader_only', 'Acme Studios', prometheus_mode='analysis'),
            'ask_only': _seat('ask_only', 'Acme Studios', api_key_scopes=['ask']),
            'sandbox_key': _seat('sandbox_key', 'Acme Studios', api_key_sandbox=True),
            'canary:app': _seat('canary:app', 'Acme Studios'),
        },
        'companies': {
            'Acme Studios': _company(2000.0),
            'Thin Co': _company(120.0),
            'Rate Card Co': _company(2000.0, profile_pull_usd=275),
            PA.COMPANY: PA._allowance_company(),
        },
    }


def draft(subject=FBI, start='2025-11-18', end='2026-11-17', **over):
    """A brief as the build step drafts it."""
    d = {'decision': 'new_build', 'subject': subject, 'brand_category': 'SERIES - DRAMA',
         'date_range': {'start': start, 'end': end}, 'date_window_label': 'Nov 18, 2025 to Nov 17, 2026',
         'tu_demos': {'GENDER': {'FEMALE': 52.1837}}, 'run_avid': True,
         'user_prompt': f"build a profile of {subject}"}
    d.update(over)
    return d


# ------------------------------------------------------------------ the store
class Gone(Exception):
    def __init__(self):
        super().__init__('An error occurred (NoSuchKey): the key does not exist')
        self.response = {'Error': {'Code': 'NoSuchKey'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}


class LostRace(Exception):
    def __init__(self):
        super().__init__('An error occurred (PreconditionFailed): at least one of the pre-conditions did not hold')
        self.response = {'Error': {'Code': 'PreconditionFailed'}, 'ResponseMetadata': {'HTTPStatusCode': 412}}


class Store(RAW.Store):
    """The suite's store with what the real one adds: a version on every
    object and a write that can be made conditional on it."""

    def __init__(self):
        super().__init__()
        self.ver, self.lose_next, self.conditional = {}, {}, 0

    def get_object(self, Bucket, Key, **_k):
        if (Bucket, Key) not in self.objs:
            raise Gone()
        return {'Body': RAW.io.BytesIO(self.objs[(Bucket, Key)]), 'ETag': f'"{self.ver.get((Bucket, Key), 0)}"'}

    def head_object(self, Bucket, Key, **_k):
        if (Bucket, Key) not in self.objs:
            raise Gone()
        return {}

    def put_object(self, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **_k):
        cur = self.ver.get((Bucket, Key), 0)
        if IfMatch is not None or IfNoneMatch is not None:
            self.conditional += 1
            if self.lose_next.get(Key, 0) > 0:          # another writer landed between the read and this write
                self.lose_next[Key] -= 1
                self.ver[(Bucket, Key)] = cur + 1
                raise LostRace()
            if IfNoneMatch == '*' and (Bucket, Key) in self.objs:
                raise LostRace()
            if IfMatch is not None and str(IfMatch).strip('"') != str(cur):
                raise LostRace()
        super().put_object(Bucket, Key, Body)
        self.ver[(Bucket, Key)] = cur + 1

    def briefs(self):
        return [k for (_b, k) in self.objs if k.startswith(PB.STORE_PREFIX)]


# ------------------------------------------------------------------ the world
class World:
    """One service: the door, the approve step, the wallet, the engine."""

    def __init__(self, data=None):
        METER.reset_memory()
        self.data = data or users()
        self.raw = RAW.World()
        self.store = self.raw.store = Store()
        self.engine = self.raw.engine
        self.desk = PA.Desk(self.data)
        self.asked = []                 # what reached the ask service (a model would be behind it)
        self.cores = []                 # every body handed to the approve step
        self.next_drafts = None
        self.next_extra = {}
        h = self.raw.host
        h.s3_client = self.store
        h.load_users = lambda: self.data
        h.has_credits_for = lambda *a, **k: self.desk.app['has_credits_for'](*a, **k)
        h.consume_credit = self._consume
        h.check_user_credits = lambda u: self.desk.app['check_user_credits'](u)
        h.NO_FUNDS_MESSAGE = 'Your account is out of funds. Top up to keep going.'
        self.charges = []
        self.raw.ns['_plan'] = PLAN
        self.raw.ns['_pm_usd_label'] = lambda v, u=None: self.desk.app['_usd_label'](v, u)
        self.raw.ns['_api_threads'] = types.SimpleNamespace(is_api_request=lambda: True)
        LEGACY.bind(h)
        LEGACY.bind_core(types.SimpleNamespace(_pm_approve_core=self._core))
        w = self

        def _json(key, default=None):
            try:
                return w.store.get_json(key)
            except KeyError:
                return default

        def _put(key, doc):
            w.store.put_object(BUCKET, key, json.dumps(doc).encode('utf-8'))

        def _table(username=None):
            rec = (w.data['users'].get(username) or {}) if username else {}
            subject = W.resolve_billing_subject(rec, w.data)[0] if rec else None
            build = float(W.tool_price_usd('api_chatbot_profile_iq_build', subject=subject) or 0)
            cut = float(W.tool_price_usd('api_profile_iq_cut', subject=subject) or 0)
            return {'new_build': build, 'time_shifted_refresh': build, 'derive_cut': cut, 'cut_needs_parent': build + cut,
                    'subscriber_iq': float(W.tool_price_usd('api_subscriber_iq_build', subject=subject) or 0),
                    'existing_match': 0.0, 'addon_cut': cut}
        self.table = _table
        host.bind(gate_pull=lambda u: str((u or {}).get('prometheus_mode') or 'both') in ('pull', 'both'),
                  gate_analyze=lambda u: True, funds_gate=lambda u: None,
                  gate_refusal=lambda kind: ({'success': False, 'guidance': True,
                                              'error': 'This account covers analysis only. To unlock the ability to pull new '
                                                       'profiles, please contact your account manager.'}, 403),
                  queue_secret='secret-for-tests', queue_url='http://engine.test',
                  load_users=lambda: w.data, check_user_credits=lambda u: w.desk.app['check_user_credits'](u),
                  consume_credit=self._consume, price_table_for=_table,
                  s3_client=self.store, bucket=BUCKET, s3_json=_json, s3_put_json=_put, max_threads=40,
                  load_threads_index=lambda u: _json(f'{THREADS}/{u}/index.json') or {'threads': []},
                  thread_key=lambda u, t: f'{THREADS}/{u}/{t}.json', threads_index_key=lambda u: f'{THREADS}/{u}/index.json',
                  thread_title_from=lambda turns: next((str(t.get('text'))[:48] for t in turns if t.get('role') == 'user'), 'New chat'),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
                  ask_hint=lambda **k: None, pricing_question=lambda t: False, pricing_copy_for=lambda u: '', pricing_copy='')

    # the dashboard's own charge function, with a note of every call
    def _consume(self, *a, **k):
        ok = self.desk.app['consume_credit'](*a, **k)
        self.charges.append({'user': a[0] if a else k.get('username'), 'ok': bool(ok), 'pull_type': k.get('pull_type'),
                             'job_id': k.get('job_id')})
        return ok

    def _core(self, user, body):
        """The approve step, as the chat module has it, for an app reader (no browser session)."""
        self.cores.append(copy.deepcopy(body))
        saved = sys.modules.get('requests')
        sys.modules['requests'] = self.raw._requests
        self.raw.session.clear()
        try:
            return self.raw.ns['_pm_approve_core'](user, body)
        finally:
            if saved is not None:
                sys.modules['requests'] = saved
            else:
                sys.modules.pop('requests', None)

    # the ask service behind the door: a build order drafts a brief, anything else is a question
    def _ask(self, user, req, via='api_key'):
        self.asked.append(req.get('text'))
        uname = S.username_of(user)
        if self.next_drafts is not None:
            drafts, self.next_drafts = self.next_drafts, None
            for d in drafts:
                d['price_table'] = self.table(uname)
            raw = {'success': True, 'estimated_credits': 10, 'clarify_steps': [], 'chip_options': None,
                   'date_window': 'Nov 18, 2025 to Nov 17, 2026'}
            if len(drafts) == 1:
                raw['spec_draft'] = drafts[0]
            else:
                raw.update(batch=True, spec_drafts=drafts)
            raw.update(self.next_extra)
            self.next_extra = {}
            env = ENV.wrap(raw, surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
        else:
            env = ENV.wrap({'success': True, 'action': 'answer', 'reply': 'Here is the answer to that.', 'followups': []},
                           surface='analyze', decision={'reason': 'question'}, via='api_key')
        if req.get('thread_id') and env.get('text'):
            now = S._now()
            S.save_thread(uname, req['thread_id'], [{'role': 'user', 'text': req.get('text'), 'ts': now},
                                                    {'role': 'agent', 'text': env['text'], 'ts': now}])
        return env, 200

    def gated(self, username):
        """The reader as the door's gate hands them on: the record, the name, a key."""
        u = copy.deepcopy(self.data['users'][username])
        u.setdefault('username', username)
        u['_auth_via'] = 'api_key'
        return u

    def send(self, username, text, cid='chat-1118-a1b2', *, agent=APP_AGENT, body=None, headers=None):
        """One POST /ask. Returns (v1, http status)."""
        full = {'text': text}
        if cid:
            full['conversation_id'] = cid
        full.update(body or {})
        hdrs = {'User-Agent': agent, 'Idempotency-Key': hashlib.md5(os.urandom(8)).hexdigest(), 'Content-Type': 'application/json'}
        hdrs.update(headers or {})
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST', json=full, headers=hdrs):
            flask.g._pm_ask_user = flask.g._pm_api_key_owner = username
            with unittest.mock.patch.object(PAPI, '_notify_key_ask', lambda *a, **k: None):
                v1, status, _h = PAPI.handle_ask(self.gated(username), full, hdrs, ask=self._ask)
        return v1, status

    def order(self, username, drafts=None, cid='chat-1118-a1b2', agent=APP_AGENT, extra=None, text=None):
        """The reader orders a build; the reply is the brief card's document."""
        drafts = drafts or [draft()]
        self.next_drafts, self.next_extra = copy.deepcopy(drafts), dict(extra or {})
        v1, _st = self.send(username, text or f"Run a profile on {drafts[0]['subject']}", cid, agent=agent)
        return v1

    def rows(self, v1):
        return (v1.get('declined') or {}).get('briefs') or []

    def balance(self, company):
        return self.data['companies'][company]['wallet_balance_usd']

    def deducts(self, company):
        return [t for t in self.data['companies'][company].get('wallet_transactions') or [] if t.get('kind') == 'deduct']

    def held(self, username, cid='chat-1118-a1b2'):
        tid = PAPI.conversation_thread_id(self.gated(username), cid)
        try:
            return self.store.get_json(PB.key_for(username, tid)).get('briefs') or []
        except KeyError:
            return []

    def age_briefs(self, username, seconds, cid='chat-1118-a1b2'):
        key = PB.key_for(username, PAPI.conversation_thread_id(self.gated(username), cid))
        doc = self.store.get_json(key)
        for b in doc['briefs']:
            b['t'] = float(b['t']) - seconds
            b['expires'] = float(b['expires']) - seconds
        self.store.put_object(BUCKET, key, json.dumps(doc).encode('utf-8'))

    def thread(self, username, cid='chat-1118-a1b2'):
        tid = PAPI.conversation_thread_id(self.gated(username), cid)
        try:
            return self.store.get_json(f'{THREADS}/{username}/{tid}.json')
        except KeyError:
            return []


def text_of(v1):
    for blk, field in (('answer', 'text'), ('declined', 'text'), ('clarify', 'question')):
        b = v1.get(blk)
        if isinstance(b, dict) and b.get(field):
            return b[field]
    return ''


class Case(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def untouched(self, company='Acme Studios', balance=2000.0, posts=0):
        self.assertEqual(len(self.w.engine.posts), posts, 'nothing more may be sent to the engine')
        self.assertEqual(self.w.balance(company), balance, 'nothing more may be charged')
        self.assertEqual(len(self.w.deducts(company)), int(round((2000.0 - balance) / BUILD_USD)) if company == 'Acme Studios' else len(self.w.deducts(company)))


# ============================================================================ 1
class APayingSeatApproves(Case):
    def test_the_card_carries_the_id_and_the_quoted_price(self):
        v1 = self.w.order('payer')
        self.assertEqual((v1['kind'], v1['declined']['reason']), ('declined', 'build_required'))
        (row,) = self.w.rows(v1)
        self.assertRegex(row['id'], r'^PB-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertEqual(row['approve'], f"Approve brief {row['id']}")
        self.assertEqual(row['dismiss'], f"Dismiss brief {row['id']}")
        self.assertEqual((row['price_usd'], row['price_label'], row['included']), (BUILD_USD, money(BUILD_USD), False))
        self.assertEqual(row['subject'], FBI)
        self.assertNotIn('included', v1['declined'])
        (held,) = self.w.held('payer')                                      # on the server, for this reader and conversation
        self.assertEqual((held['id'], held['state'], held['decision']), (row['id'], 'open', 'new_build'))
        self.assertEqual(held['draft']['subject'], FBI)                     # the exact order
        self.assertEqual(held['quote']['price_usd'], BUILD_USD)             # what was quoted
        self.assertAlmostEqual(held['expires'] - held['t'], 24 * 3600)      # and when it stops
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)            # a brief charges nothing

    def test_one_approval_starts_one_build_and_charges_the_quote_once(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.asked.clear()
        v1, status = self.w.send('payer', row['approve'])
        self.assertEqual((v1['kind'], status), ('answer', 200))
        self.assertEqual(v1['answer']['text'],
                         f"Approved. {FBI} is building now. The price is {money(BUILD_USD)}, as quoted on the brief. "
                         "I will email you when it is ready, and it will be in Profile IQ under Select Profile.")
        self.assertEqual(v1['brief'], {'id': row['id'], 'state': 'started', 'closed': True, 'subject': FBI, 'included': False,
                                       'price_usd': BUILD_USD})
        self.assertEqual(self.w.asked, [])                                  # the words never reached a model
        self.assertEqual(len(self.w.engine.posts), 1)
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - BUILD_USD)
        (tx,) = self.w.deducts('Acme Studios')                              # one ledger entry, at the quote
        self.assertEqual(abs(float(tx['amount_usd'])), BUILD_USD)
        (charge,) = self.w.charges
        self.assertEqual((charge['user'], charge['ok'], charge['job_id']), ('payer', True, 'RUN001'))
        (held,) = self.w.held('payer')
        self.assertEqual((held['state'], held['result']['state']), ('done', 'started'))
        self.assertNotIn('draft', held)                                     # single use: the order is gone from the record

    def test_it_runs_the_stored_order_through_the_dashboards_approve_step(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.send('payer', row['approve'])
        (body,) = self.w.cores
        self.assertEqual(sorted(body), ['email_to', 'prompt', 'run_avid', 'spec_draft', 'thread_id'])
        self.assertEqual(body['spec_draft']['subject'], FBI)
        self.assertEqual(body['email_to'], 'payer@client.example')          # from the reader's own record
        post = self.w.engine.posts[0]
        self.assertEqual((post['spec']['name'], post['decision'], post['username']), (FBI, 'new_build', 'payer'))
        self.assertEqual(post['spec']['date_range'], '2025-11-18 TO 2026-11-17')
        # the launch is on record against the app's own conversation, so the finished line posts there
        tid = PAPI.conversation_thread_id(self.w.gated('payer'), 'chat-1118-a1b2')
        launch = self.w.store.get_json('system/pm_build_launches/RUN001.json') if (BUCKET, 'system/pm_build_launches/RUN001.json') in self.w.store.objs else None
        launches = [json.loads(v.decode()) for (b, k), v in self.w.store.objs.items() if 'RUN001' in k and k.endswith('.json') and 'launch' in k]
        doc = launch or (launches[0] if launches else None)
        self.assertIsNotNone(doc, sorted(k for (_b, k) in self.w.store.objs))
        self.assertEqual((doc['thread_id'], doc['user'], doc['via']), (tid, 'payer', 'api'))
        # and the repeat approval record holds the order
        (entry,) = self.w.raw.entries()
        self.assertEqual((entry['u'], entry['r']), ('payer', 'RUN001'))

    def test_a_second_submit_answers_with_the_first_result(self):
        row = self.w.rows(self.w.order('payer'))[0]
        first, _ = self.w.send('payer', row['approve'])
        again, status = self.w.send('payer', row['approve'])
        third, _ = self.w.send('payer', row['approve'])
        for doc in (again, third):
            self.assertEqual(doc['answer']['text'], first['answer']['text'])
            self.assertEqual(doc['brief'], first['brief'])
            self.assertIs(doc['replayed'], True)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.w.cores), 1)                              # the approve step ran once
        self.assertEqual(len(self.w.engine.posts), 1)
        self.assertEqual(len(self.w.deducts('Acme Studios')), 1)
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - BUILD_USD)

    def test_the_same_request_replayed_by_its_key_charges_once(self):
        row = self.w.rows(self.w.order('payer'))[0]
        hdr = {'Idempotency-Key': 'one-click-0001'}
        a, _ = self.w.send('payer', row['approve'], headers=hdr)
        b, _ = self.w.send('payer', row['approve'], headers=hdr)
        self.assertEqual(b['answer']['text'], a['answer']['text'])
        self.assertEqual((len(self.w.cores), len(self.w.deducts('Acme Studios'))), (1, 1))

    def test_a_company_rate_is_the_figure_on_the_card_and_the_figure_charged(self):
        v1 = self.w.order('ben')
        (row,) = self.w.rows(v1)
        self.assertEqual((row['price_usd'], row['price_label']), (275.0, '$275'))
        self.assertIn('$275', v1['declined']['text'])
        self.assertNotIn(money(BUILD_USD), json.dumps(v1))
        out, _ = self.w.send('ben', row['approve'])
        self.assertIn('The price is $275, as quoted on the brief.', out['answer']['text'])
        (tx,) = self.w.deducts('Rate Card Co')
        self.assertEqual(abs(float(tx['amount_usd'])), 275.0)
        self.assertEqual(self.w.balance('Rate Card Co'), 2000.0 - 275.0)

    def test_two_briefs_are_two_approvals(self):
        v1 = self.w.order('payer', [draft(FBI), draft(FBI2)])
        a, b = self.w.rows(v1)
        self.assertNotEqual(a['id'], b['id'])
        self.w.send('payer', a['approve'])
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - BUILD_USD)
        self.assertEqual([x['state'] for x in self.w.held('payer')], ['done', 'open'])
        out, _ = self.w.send('payer', b['approve'])
        self.assertEqual(out['brief']['state'], 'started')
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - 2 * BUILD_USD)
        self.assertEqual([p['spec']['name'] for p in self.w.engine.posts], [FBI, FBI2])

    def test_the_conversation_reads_whole_when_it_is_opened_again(self):
        row = self.w.rows(self.w.order('payer'))[0]
        out, _ = self.w.send('payer', row['approve'])
        turns = self.w.thread('payer')
        self.assertEqual([t['role'] for t in turns[-2:]], ['user', 'agent'])
        self.assertEqual(turns[-2]['text'], f"Approve the brief for {FBI}")  # the reader's turn, without the id
        self.assertEqual(turns[-1]['text'], out['answer']['text'])
        self.assertNotIn('PB-', json.dumps(turns[-2:]))


# ============================================================================ 2
class NothingInTheRequestDecides(Case):
    TAMPER = {
        'price_usd': 1, 'price': 0, 'amount_usd': 0.01, 'quote': {'price_usd': 0, 'included': True}, 'included': True,
        'spec_draft': {'decision': 'new_build', 'subject': 'Something Else Entirely', 'tu_demos': {'x': 1}},
        'draft': {'subject': 'Something Else Entirely'}, 'order': 'Run a profile on Something Else Entirely',
        'subject': 'Something Else Entirely', 'decision': 'existing_match',
        'username': 'colleague', 'user': 'liz', 'email': 'liz@crosswalknyc.com', 'email_to': 'someone@else.example',
        'company': PA.COMPANY, 'billing_source': 'user', 'bill_to': 'liz',
        'force': True, 'force_new_build': True, 'rerun': True, 'override': True, 'skip_checks': True, 'run_avid': False,
        'mode': 'free', 'surface': 'approve', 'extra': {'included': True, 'price_usd': 0}, 'brief_id': 'PB-AAAA-BBBB',
        '_pm_included': True, '_pm_no_auto_start': True, 'thread_id': 'someone-elses-thread', 'prompt': 'ignored',
    }

    def test_a_tampered_body_changes_nothing(self):
        row = self.w.rows(self.w.order('payer'))[0]
        out, status = self.w.send('payer', row['approve'], body=dict(self.TAMPER))
        self.assertEqual((out['kind'], status, out['brief']['state']), ('answer', 200, 'started'))
        self.assertEqual(out['brief']['price_usd'], BUILD_USD)
        (body,) = self.w.cores
        self.assertEqual(body['spec_draft']['subject'], FBI)                # the stored order
        self.assertIs(body['run_avid'], True)
        self.assertEqual(body['email_to'], 'payer@client.example')
        self.assertNotIn('force_new_build', body)
        self.assertEqual(body['prompt'], f"Run a profile on {FBI}")         # the question the brief was drafted from
        post = self.w.engine.posts[0]
        self.assertEqual((post['spec']['name'], post['username'], post['decision']), (FBI, 'payer', 'new_build'))
        (tx,) = self.w.deducts('Acme Studios')                              # the gated reader's wallet, the quoted price
        self.assertEqual(abs(float(tx['amount_usd'])), BUILD_USD)
        self.assertEqual(self.w.deducts(PA.COMPANY), [])
        self.assertEqual(self.w.balance(PA.COMPANY), 11000.0)
        self.assertEqual([c['user'] for c in self.w.charges], ['payer'])

    def test_the_turn_reads_only_the_gated_reader_the_conversation_and_the_words(self):
        import inspect
        src = inspect.getsource(PB)
        for banned in ('request.get_json', 'request.json', 'request.form', 'request.args', 'body.get(', 'body['):
            self.assertNotIn(banned, src, banned)
        params = list(inspect.signature(PB.turn).parameters)
        self.assertEqual(params, ['user', 'cid', 'text', 'headers', 'thread_id', 'now', 'core'])

    def test_a_subject_or_a_window_in_the_body_does_not_move_the_order(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.send('payer', row['approve'], body={'subject': 'Landman', 'window': {'start': '2024-01-01', 'end': '2024-12-31'}})
        self.assertEqual(self.w.engine.posts[0]['spec']['date_range'], '2025-11-18 TO 2026-11-17')
        self.assertEqual(self.w.engine.posts[0]['spec']['name'], FBI)


# ============================================================================ 3
class ItIsThatReadersBrief(Case):
    def test_another_reader_cannot_approve_it_even_on_the_same_wallet(self):
        row = self.w.rows(self.w.order('payer'))[0]
        out, status = self.w.send('colleague', row['approve'])              # same company, same conversation name
        self.assertEqual((out['kind'], out['declined']['reason'], status), ('declined', 'unsupported', 200))
        self.assertEqual(out['declined']['text'], PB.UNKNOWN)
        self.assertEqual(out['brief'], {'id': row['id'], 'state': 'unknown', 'closed': True, 'subject': ''})
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges), ([], [], []))
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)
        self.assertEqual(self.w.held('payer')[0]['state'], 'open')          # and the owner's brief is still theirs to approve

    def test_another_conversation_cannot_approve_it(self):
        row = self.w.rows(self.w.order('payer'))[0]
        out, _ = self.w.send('payer', row['approve'], cid='another-chat-1118')
        self.assertEqual(out['declined']['text'], PB.UNKNOWN)
        out, _ = self.w.send('payer', row['approve'], cid='')
        self.assertEqual(out['declined']['text'], PB.UNKNOWN)
        self.assertEqual((self.w.cores, self.w.charges), ([], []))
        self.assertEqual(self.w.held('payer')[0]['state'], 'open')

    def test_an_id_from_thin_air_is_refused(self):
        self.w.order('payer')
        for text in ('Approve brief PB-ZZZZ-9999', 'approve brief pb-aaaa-bbbb', 'Dismiss brief PB-ZZZZ-9999'):
            out, _ = self.w.send('payer', text)
            self.assertEqual(out['declined']['text'], PB.UNKNOWN, text)
            self.assertIs(out['brief']['closed'], True)
        self.assertEqual((self.w.cores, self.w.asked[1:]), ([], []))        # never a question for a model either
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)

    def test_the_record_sits_under_the_reader_and_the_conversation(self):
        self.w.order('payer')
        self.w.order('colleague', cid='chat-1118-zz99')
        keys = sorted(self.w.store.briefs())
        self.assertEqual(len(keys), 2)
        self.assertTrue(keys[0].startswith(PB.STORE_PREFIX + 'colleague/api-'))
        self.assertTrue(keys[1].startswith(PB.STORE_PREFIX + 'payer/api-'))


# ============================================================================ 4
class ItExpires(Case):
    def test_after_a_day_it_is_refused_in_plain_words(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.age_briefs('payer', 24 * 3600 + 60)
        out, status = self.w.send('payer', row['approve'])
        self.assertEqual((out['kind'], status), ('declined', 200))
        self.assertEqual(out['declined']['text'], 'That brief has expired, so I did not start it and nothing was charged. '
                                                  'Ask for it again and I will draft a fresh one.')
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('expired', True))
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges), ([], [], []))
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)
        again, _ = self.w.send('payer', row['approve'])
        self.assertEqual(again['declined']['text'], out['declined']['text'])
        self.assertEqual(self.w.held('payer')[0]['state'], 'expired')

    def test_just_inside_the_day_it_still_runs(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.age_briefs('payer', 24 * 3600 - 120)
        out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['brief']['state'], 'started')

    def test_the_card_is_told_when(self):
        row = self.w.rows(self.w.order('payer'))[0]
        when = time.mktime(time.strptime(row['expires_at'], '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
        self.assertAlmostEqual(when - time.time(), 24 * 3600, delta=30)
        self.assertEqual(PB.LIFE_S, 24 * 3600)


# ============================================================================ 5
class ShortFunds(Case):
    def test_the_wallets_own_line_and_nothing_starts(self):
        row = self.w.rows(self.w.order('shorty'))[0]
        self.assertEqual(row['price_usd'], BUILD_USD)                       # the card still shows the price
        out, status = self.w.send('shorty', row['approve'])
        self.assertEqual((out['kind'], out['declined']['reason'], status), ('declined', 'insufficient_funds', 402))
        self.assertEqual(out['declined']['text'], f"This run costs {money(BUILD_USD)}. Wallet balance is $120. Top up to keep going.")
        self.assertEqual(out['declined']['text'], self.w.desk.app['_funds_refusal_copy']('shorty', 'Chatbot Profile IQ (new_build)')[0])
        self.assertEqual(out['declined']['next'], {'action': 'top_up', 'endpoint': '/wallet'})
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('open', False))   # the card stays
        self.assertEqual((self.w.engine.posts, self.w.charges), ([], []))
        self.assertEqual((self.w.balance('Thin Co'), self.w.deducts('Thin Co')), (120.0, []))
        self.assertEqual(self.w.raw.entries(), [])                          # no hold was left behind either

    def test_after_a_top_up_the_same_card_approves(self):
        row = self.w.rows(self.w.order('shorty'))[0]
        self.w.send('shorty', row['approve'])
        self.w.data['companies']['Thin Co']['wallet_balance_usd'] = 900.0
        out, _ = self.w.send('shorty', row['approve'])
        self.assertEqual(out['brief']['state'], 'started')
        self.assertEqual(self.w.balance('Thin Co'), 900.0 - BUILD_USD)
        self.assertEqual(len(self.w.deducts('Thin Co')), 1)

    def test_a_seat_that_reads_only_is_told_so_and_nothing_starts(self):
        # (a brief put on record before the seat was switched to analysis only)
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.data['users']['payer']['prometheus_mode'] = 'analysis'
        out, status = self.w.send('payer', row['approve'])
        self.assertEqual((out['declined']['reason'], status), ('scope', 403))
        self.assertIn('This account covers analysis only.', out['declined']['text'])
        self.assertEqual((self.w.cores, self.w.charges, out['brief']['closed']), ([], [], False))


# ============================================================================ 6
class AnIncludedSeat(Case):
    def test_a_staff_seat_reads_no_price_and_is_never_asked_to_pay(self):
        v1 = self.w.order('liz')
        (row,) = self.w.rows(v1)
        self.assertEqual((row['included'], row['price_usd']), (True, None))
        self.assertNotIn('price_label', row)
        self.assertIs(v1['declined']['included'], True)
        self.assertNotIn('$', json.dumps(v1))
        out, status = self.w.send('liz', row['approve'])
        self.assertEqual((out['kind'], status), ('answer', 200))
        self.assertEqual(out['answer']['text'], f"Included with your plan. {FBI} is building now. I will email you when it is ready, "
                                                "and it will be in Profile IQ under Select Profile.")
        self.assertNotIn('$', json.dumps(out))
        self.assertEqual((out['brief']['included'], out['brief']['price_usd']), (True, None))
        self.assertEqual(len(self.w.engine.posts), 1)
        for co in self.w.data['companies'].values():                        # no wallet anywhere was touched
            self.assertEqual([t for t in co.get('wallet_transactions') or [] if t.get('kind') == 'deduct'], [])
        self.assertEqual(self.w.data['users']['liz'].get('wallet_transactions') or [], [])

    def test_a_plan_allowance_reads_included_and_meters_behind_the_scenes(self):
        v1 = self.w.order('naomi')
        (row,) = self.w.rows(v1)
        self.assertEqual((row['included'], row['price_usd']), (True, None))
        self.assertNotIn('$', json.dumps(v1))
        out, _ = self.w.send('naomi', row['approve'])
        self.assertTrue(out['answer']['text'].startswith('Included with your plan. '))
        self.assertNotIn('$', json.dumps(out))
        self.assertNotIn('11,000', json.dumps(out))
        self.assertEqual(self.w.balance(PA.COMPANY), 11000.0 - BUILD_USD)   # the allowance meters at the list price
        self.assertEqual(len(self.w.deducts(PA.COMPANY)), 1)

    def test_an_allowance_that_ran_out_since_the_brief_reads_the_plans_own_line(self):
        row = self.w.rows(self.w.order('naomi'))[0]
        self.w.data['companies'][PA.COMPANY]['wallet_balance_usd'] = 40.0
        with unittest.mock.patch.object(W, 'note_allowance_shortfall', lambda *a, **k: False):
            out, status = self.w.send('naomi', row['approve'])
        self.assertEqual((out['declined']['reason'], status), ('insufficient_funds', 402))
        self.assertEqual(out['declined']['text'], W.funds_refusal_line(
            W.pull_funding(self.w.data['users']['naomi'], self.w.data, 'Chatbot Profile IQ (new_build)')))
        self.assertNotIn('$', out['declined']['text'])                      # no price and no balance for a plan seat
        self.assertEqual((self.w.engine.posts, self.w.balance(PA.COMPANY)), ([], 40.0))
        self.assertIs(out['brief']['closed'], False)


# ============================================================================ 7
class ARepeatIsAnswered(Case):
    def _deliver_first(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.send('payer', row['approve'])
        self.w.raw.age_entries(5 * 3600)
        self.w.engine.finish('RUN001', ago_s=4 * 3600, tu_key='FBI_Most_Wanted_Viewers_11_17_2026_10_00.csv')
        self.w.store.objs[(BUCKET, 'FBI_Most_Wanted_Viewers_11_17_2026_10_00.csv')] = b'Column,Value\n'

    def test_an_order_already_delivered_is_answered_not_charged(self):
        self._deliver_first()
        row = self.w.rows(self.w.order('payer', cid='chat-1118-later'))[0]   # the same order, asked again
        out, status = self.w.send('payer', row['approve'], cid='chat-1118-later')
        self.assertEqual((out['kind'], status), ('answer', 200))
        self.assertEqual(out['answer']['text'], f"{FBI} is already delivered. It is in Profile IQ under Select Profile, and there "
                                                "is nothing more to approve.")
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('already_delivered', True))
        self.assertEqual(len(self.w.engine.posts), 1)                       # only the first build
        self.assertEqual(len(self.w.deducts('Acme Studios')), 1)
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - BUILD_USD)
        again, _ = self.w.send('payer', row['approve'], cid='chat-1118-later')
        self.assertEqual(again['answer']['text'], out['answer']['text'])
        self.assertEqual(len(self.w.deducts('Acme Studios')), 1)

    def test_an_order_still_running_is_answered_not_charged(self):
        self.w.send('payer', self.w.rows(self.w.order('payer'))[0]['approve'])
        row = self.w.rows(self.w.order('payer', cid='chat-1118-later'))[0]
        out, _ = self.w.send('payer', row['approve'], cid='chat-1118-later')
        self.assertEqual(out['answer']['text'], f"{FBI} is already running, so I did not start it again. The ready notice comes "
                                                "when it finishes.")
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('already_running', True))
        self.assertEqual((len(self.w.engine.posts), len(self.w.deducts('Acme Studios'))), (1, 1))

    def test_a_colleague_on_the_same_wallet_is_answered_too(self):
        self._deliver_first()
        row = self.w.rows(self.w.order('colleague', cid='chat-1118-zz99'))[0]
        out, _ = self.w.send('colleague', row['approve'], cid='chat-1118-zz99')
        self.assertEqual(out['brief']['state'], 'already_delivered')
        self.assertEqual(len(self.w.deducts('Acme Studios')), 1)


# ============================================================================ 8
class TheTermsMoved(Case):
    def test_a_price_that_changed_since_the_quote_starts_nothing(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.data['companies']['Acme Studios']['profile_pull_usd'] = 450      # a new rate lands after the brief
        out, status = self.w.send('payer', row['approve'])
        self.assertEqual((out['kind'], status), ('declined', 200))
        self.assertEqual(out['declined']['text'], PB.OUT_OF_DATE)
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('out_of_date', True))
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges), ([], [], []))
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)
        again, _ = self.w.send('payer', row['approve'])                     # closed for good, even if the rate goes back
        self.w.data['companies']['Acme Studios'].pop('profile_pull_usd')
        final, _ = self.w.send('payer', row['approve'])
        self.assertEqual((again['declined']['text'], final['declined']['text']), (PB.OUT_OF_DATE, PB.OUT_OF_DATE))
        self.assertEqual(self.w.cores, [])

    def test_a_seat_that_stopped_being_included_is_never_charged_an_unquoted_price(self):
        row = self.w.rows(self.w.order('liz'))[0]
        liz = self.w.data['users']['liz']
        liz.update(role='user', email='liz@elsewhere.example', paying_customer=True, wallet_balance_usd=5000.0,
                   billing_mode='prepay_only', credits=0)
        out, _ = self.w.send('liz', row['approve'])
        self.assertEqual(out['declined']['text'], PB.OUT_OF_DATE)
        self.assertEqual((self.w.cores, liz['wallet_balance_usd']), ([], 5000.0))

    def test_a_profile_that_now_exists_is_not_built_or_charged(self):
        row = self.w.rows(self.w.order('payer'))[0]
        with unittest.mock.patch.object(type(self.w.raw.host), '_normalize_v1_decision',
                                        lambda self_, d: ('existing_match', 'FBI_Most_Wanted_Viewers_11_17_2026_10_00.csv', '')):
            out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['declined']['text'], PB.OUT_OF_DATE)
        self.assertEqual((self.w.cores, self.w.balance('Acme Studios')), ([], 2000.0))


# ============================================================================ 9
class OneClickOneApproval(Case):
    def test_an_approval_in_flight_is_not_run_a_second_time(self):
        row = self.w.rows(self.w.order('payer'))[0]
        seen = {}
        real = self.w._core

        def _slow(user, body):
            seen['second'] = self.w.send('payer', row['approve'])            # the same card, clicked again before the first lands
            return real(user, body)
        LEGACY.bind_core(types.SimpleNamespace(_pm_approve_core=_slow))
        first, _ = self.w.send('payer', row['approve'])
        second, status = seen['second']
        self.assertEqual(second['declined']['text'], 'That approval is already going through. It will not be charged twice.')
        self.assertEqual((second['brief']['state'], second['brief']['closed'], status), ('approving', False, 200))
        self.assertEqual(first['brief']['state'], 'started')
        self.assertEqual((len(self.w.engine.posts), len(self.w.deducts('Acme Studios'))), (1, 1))

    def test_the_claim_is_a_conditional_write_and_a_lost_one_is_tried_again(self):
        row = self.w.rows(self.w.order('payer'))[0]
        key = self.w.store.briefs()[0]
        before = self.w.store.conditional
        self.w.store.lose_next[key] = 1
        out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['brief']['state'], 'started')
        self.assertGreaterEqual(self.w.store.conditional - before, 3)        # the claim (twice) and the result
        self.assertEqual((len(self.w.engine.posts), len(self.w.deducts('Acme Studios'))), (1, 1))

    def test_a_claim_that_was_never_settled_is_answered_by_the_repeat_check(self):
        # the process died after the engine took the order and before the result was written
        row = self.w.rows(self.w.order('payer'))[0]
        real_settle = PB._settle
        with unittest.mock.patch.object(PB, '_settle', lambda *a, **k: None):
            self.w.send('payer', row['approve'])
        self.assertEqual(self.w.held('payer')[0]['state'], 'approving')
        out, _ = self.w.send('payer', row['approve'])                       # inside the claim's life: in flight
        self.assertEqual(out['brief']['state'], 'approving')
        key = self.w.store.briefs()[0]
        doc = self.w.store.get_json(key)
        doc['briefs'][0]['claim_t'] -= PB.CLAIM_S + 5
        self.w.store.put_object(BUCKET, key, json.dumps(doc).encode())
        self.assertIs(PB._settle, real_settle)
        out, _ = self.w.send('payer', row['approve'])                       # past it: the approve step's own check answers
        self.assertEqual(out['brief']['state'], 'already_running')
        self.assertEqual((len(self.w.engine.posts), len(self.w.deducts('Acme Studios'))), (1, 1))

    def test_an_order_the_engine_did_not_take_leaves_the_card_open_and_uncharged(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.engine.refuse = True
        out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['kind'], 'declined')
        self.assertIn('Nothing is running and nothing was charged.', out['declined']['text'])
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('open', False))
        self.assertEqual((self.w.charges, self.w.balance('Acme Studios')), ([], 2000.0))
        self.w.engine.refuse = False
        out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['brief']['state'], 'started')
        self.assertEqual(len(self.w.deducts('Acme Studios')), 1)


# ============================================================================ 10
class ATypedYes(Case):
    def test_approve_typed_right_after_the_one_brief_approves_it(self):
        for word in ('approve', 'Approve', 'yes', 'Yes, go ahead', 'approve it', 'ok do it'):
            w = World()
            w.order('payer')
            w.asked.clear()
            out, _ = w.send('payer', word)
            self.assertEqual((out['kind'], out['brief']['state']), ('answer', 'started'), word)
            self.assertEqual(w.asked, [], word)
            self.assertEqual(len(w.deducts('Acme Studios')), 1, word)
            self.assertEqual(w.thread('payer')[-2]['text'], word)

    def test_with_two_briefs_waiting_it_asks_which(self):
        v1 = self.w.order('payer', [draft(FBI), draft(FBI2)])
        a, b = self.w.rows(v1)
        out, _ = self.w.send('payer', 'approve')
        self.assertEqual(out['kind'], 'clarify')
        self.assertEqual(out['clarify']['question'], '2 briefs are waiting in this conversation. Which one should I approve?')
        self.assertEqual(out['clarify']['options'], [
            {'label': f"Approve {FBI} ({money(BUILD_USD)})", 'send': a['approve']},
            {'label': f"Approve {FBI2} ({money(BUILD_USD)})", 'send': b['approve']}])
        self.assertEqual((self.w.cores, self.w.charges), ([], []))

    def test_a_yes_to_something_said_since_is_not_about_the_brief(self):
        self.w.order('payer')
        self.w.send('payer', 'How many of them are women?')                 # the conversation moved on
        self.w.asked.clear()
        out, _ = self.w.send('payer', 'yes')
        self.assertEqual(self.w.asked, ['yes'])                             # an ordinary turn, as it always was
        self.assertNotIn('brief', out)
        self.assertEqual((self.w.cores, self.w.charges), ([], []))

    def test_approve_after_the_conversation_moved_on_asks_first(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.send('payer', 'How many of them are women?')
        out, _ = self.w.send('payer', 'approve')
        self.assertEqual(out['kind'], 'clarify')
        self.assertEqual(out['clarify']['question'], f"One brief is waiting in this conversation: {FBI}. Approve it?")
        self.assertEqual(out['clarify']['options'], [{'label': f"Approve {FBI} ({money(BUILD_USD)})", 'send': row['approve']}])
        self.assertEqual((self.w.cores, self.w.charges), ([], []))

    def test_a_priced_read_offered_since_keeps_its_own_approve(self):
        # the brief is still open, then a read is quoted with Approve / Cancel: "Approve" is that offer's, as it always was
        self.w.order('payer')
        self.w.send('payer', 'How many of them are women?')
        tid = PAPI.conversation_thread_id(self.w.gated('payer'), 'chat-1118-a1b2')
        from prometheus import pending_offers as PO
        with APP.test_request_context('/'):
            PO.record('payer', tid, {'panel_offer': {'question': 'How many of them are women?', 'subject': FBI, 'kind': 'read',
                                                     'price_usd': 100, 'price_label': '$100'}, 'subject': FBI},
                      'How many of them are women?')
        self.assertTrue(PO.anything_open('payer', [], tid))
        for word in ('Approve', 'approve it', 'yes'):
            self.w.asked.clear()
            METER.reset_memory()                                            # (the door's own repeat replay is not what is under test)
            out, _ = self.w.send('payer', word)
            self.assertEqual(self.w.asked, [word], word)                    # on to the service, which binds the offer
            self.assertNotIn('brief', out)
        self.assertEqual((self.w.cores, self.w.charges), ([], []))

    def test_with_nothing_waiting_the_words_are_an_ordinary_turn(self):
        for word in ('approve', 'yes', 'dismiss'):
            self.w.asked.clear()
            out, _ = self.w.send('payer', word)
            self.assertEqual(self.w.asked, [word])
            self.assertNotIn('brief', out)

    def test_a_brief_in_another_conversation_is_not_approved_from_here(self):
        self.w.order('payer', cid='chat-1118-other')
        self.w.asked.clear()
        self.w.send('payer', 'approve')
        self.assertEqual((self.w.asked, self.w.cores), (['approve'], []))

    def test_only_an_unambiguous_yes_counts(self):
        for text in ('approve?', 'should I approve it', 'yes but make it a 90 day window', 'approve the Landman one', 'no', 'maybe'):
            self.assertIsNone(PB.parse(text) if not PB.parse(text) or PB.parse(text)['verb'] != 'approve' else PB.parse(text), text)
        for text, want in (('approve', True), ('Approve it', True), ('approve and run it', True), ('build it', True),
                           ('yes', False), ('go ahead', False), ('ok do it', False)):
            got = PB.parse(text)
            self.assertEqual((got['verb'], got['id'], got['explicit']), ('approve', '', want), text)


# ============================================================================ 11
class Dismissing(Case):
    def test_dismiss_closes_the_brief_for_good(self):
        row = self.w.rows(self.w.order('payer'))[0]
        out, _ = self.w.send('payer', row['dismiss'])
        self.assertEqual((out['kind'], out['answer']['text']), ('answer', 'Dismissed. Nothing was started and nothing was charged.'))
        self.assertEqual((out['brief']['state'], out['brief']['closed']), ('dismissed', True))
        late, _ = self.w.send('payer', row['approve'])
        self.assertEqual(late['declined']['text'], PB.WAS_DISMISSED)
        self.assertIs(late['brief']['closed'], True)
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges), ([], [], []))
        self.assertNotIn('draft', self.w.held('payer')[0])

    def test_a_started_build_cannot_be_dismissed_into_a_refund(self):
        row = self.w.rows(self.w.order('payer'))[0]
        first, _ = self.w.send('payer', row['approve'])
        out, _ = self.w.send('payer', row['dismiss'])
        self.assertEqual(out['answer']['text'], first['answer']['text'])    # the first result stands
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0 - BUILD_USD)


# ============================================================================ 12
class APartnerKeyCannot(Case):
    def test_a_key_that_is_not_the_app_gets_the_contract_it_had(self):
        v1 = self.w.order('payer', agent=PARTNER_AGENT)
        (row,) = self.w.rows(v1)
        self.assertEqual(sorted(row), ['decision', 'included', 'order', 'price_usd', 'subject', 'window'])   # as before
        self.assertEqual(v1['declined']['next'], {'action': 'build', 'endpoint': '/api/v1/profiles/run'})
        self.assertNotIn('brief', v1)
        self.assertEqual(self.w.store.briefs(), [])                         # nothing was put on record for it

    def test_it_cannot_approve_here_even_with_a_real_id(self):
        row = self.w.rows(self.w.order('payer'))[0]                         # drafted in the app
        self.w.asked.clear()
        out, status = self.w.send('payer', row['approve'], agent=PARTNER_AGENT)
        self.assertEqual((out['kind'], out['declined']['reason'], status), ('declined', 'unsupported', 200))
        self.assertEqual(out['declined']['text'], 'Approving a brief is done in the Prometheus app. Through the API, start the '
                                                  'build with POST /api/v1/profiles/run.')
        self.assertEqual(out['declined']['next'], {'action': 'build', 'endpoint': '/api/v1/profiles/run'})
        self.assertNotIn('brief', out)
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges, self.w.asked), ([], [], [], []))
        self.assertEqual(self.w.held('payer')[0]['state'], 'open')
        for agent in ('', 'curl/8.6', 'Mozilla/5.0 Crosswalk-Prometheus-Desktop/1.4.2'):
            out, _ = self.w.send('payer', row['approve'], agent=agent)
            self.assertNotIn('brief', out, agent)
        self.assertEqual(self.w.cores, [])

    def test_a_typed_approve_from_a_partner_is_an_ordinary_turn(self):
        self.w.order('payer')
        self.w.asked.clear()
        self.w.send('payer', 'approve', agent=PARTNER_AGENT)
        self.assertEqual((self.w.asked, self.w.cores), (['approve'], []))

    def test_a_key_that_may_not_build_gets_no_id(self):
        for who in ('ask_only', 'sandbox_key'):
            v1 = self.w.order(who, cid='chat-1118-' + who.replace('_', ''))
            self.assertNotIn('id', self.w.rows(v1)[0], who)
        self.assertEqual(self.w.store.briefs(), [])
        self.w.data['users']['payer']['api_key_app_approve'] = False        # an admin switched this key out
        self.assertNotIn('id', self.w.rows(self.w.order('payer'))[0])

    def test_a_key_switched_out_after_the_brief_cannot_approve_it(self):
        row = self.w.rows(self.w.order('payer'))[0]
        self.w.data['users']['payer']['api_key_scopes'] = ['ask']
        out, status = self.w.send('payer', row['approve'])
        self.assertEqual((out['declined']['reason'], status, out['declined']['text']), ('scope', 403, PB.NO_BUILDS))
        self.assertEqual((self.w.cores, self.w.charges), ([], []))

    def test_the_profile_api_is_not_touched(self):
        import inspect
        src = inspect.getsource(PB) + inspect.getsource(PAPI.handle_ask)
        self.assertNotIn("'/api/v1/profiles/check'", src)
        app_src = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertNotIn('pending_briefs', app_src)                         # the dashboard and the profile API know nothing of it
        self.assertNotIn('pending_briefs', CHAT)
        self.assertNotIn('pending_briefs', (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8'))


# ============================================================================ 13
class AProbeLeavesNothing(Case):
    def test_a_probe_identity_gets_no_id_and_nothing_is_written(self):
        writes = list(self.w.store.writes)
        v1 = self.w.order('canary:app')
        self.assertNotIn('id', self.w.rows(v1)[0])
        self.assertEqual(self.w.store.briefs(), [])
        self.assertEqual([k for k in self.w.store.writes[len(writes):] if PB.STORE_PREFIX in k], [])

    def test_a_probe_cannot_approve_and_leaves_nothing_behind(self):
        row = self.w.rows(self.w.order('payer'))[0]
        writes = list(self.w.store.writes)
        for who, hdrs in (('canary:app', {}), ('payer', {'X-Prometheus-Caller': 'canary'}),
                          ('payer', {'User-Agent': PB.APP_UA + '1.4.2 smoke'})):
            out, _ = self.w.send(who, row['approve'], headers=hdrs)
            self.assertEqual(out['declined']['text'], PB.UNKNOWN, (who, hdrs))
        out, _ = self.w.send('payer', 'approve', headers={'X-Prometheus-Caller': 'canary'})
        self.assertNotIn('brief', out)
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges), ([], [], []))
        new = self.w.store.writes[len(writes):]
        self.assertEqual([k for k in new if not k.startswith(THREADS)], [])  # no record, no hold, no launch, no usage
        self.assertEqual(self.w.asked[-1], 'approve')                       # the bare word went on as an ordinary turn
        self.assertEqual(self.w.balance('Acme Studios'), 2000.0)
        self.assertEqual(self.w.held('payer')[0]['state'], 'open')

    def test_a_probe_never_reaches_the_approve_step_even_with_a_brief_on_record(self):
        # a brief written under a probe name by hand: the turn still refuses before the approve step, so nothing is charged
        tid = PAPI.conversation_thread_id(self.w.gated('canary:app'), 'chat-1118-a1b2')
        rec = {'id': 'PB-AAAA-BBBB', 'state': 'open', 't': time.time(), 'expires': time.time() + 3600, 'decision': 'new_build',
               'order': {'subject': FBI, 'kind': 'profile'}, 'quote': {'included': False, 'price_usd': BUILD_USD, 'label': money(BUILD_USD)},
               'draft': draft()}
        self.w.store.put_json(PB.key_for('canary:app', tid), {'briefs': [rec]})
        out, _ = self.w.send('canary:app', 'Approve brief PB-AAAA-BBBB')
        self.assertEqual(out['declined']['text'], PB.UNKNOWN)
        self.assertEqual((self.w.cores, self.w.engine.posts, self.w.charges, self.w.balance('Acme Studios')), ([], [], [], 2000.0))


# ============================================================================ 14
class WhereTheAppStillCannot(Case):
    def test_a_brief_with_a_question_open_carries_no_id(self):
        for extra in ({'clarify_steps': ['identity']}, {'chip_options': [{'label': 'Someone else'}]}):
            w = World()
            v1 = w.order('payer', extra=extra)
            self.assertNotIn('id', w.rows(v1)[0], extra)
            self.assertEqual(w.store.briefs(), [], extra)
        v1 = self.w.order('payer', [draft(ask_viewer_scope=True, viewer_scope_data={'seasons': [1, 2]})])
        self.assertNotIn('id', self.w.rows(v1)[0])

    def test_a_subject_the_build_step_could_not_verify_carries_no_id(self):
        # the approve step would research it first and might ask, or settle on another subject: never from the app
        v1 = self.w.order('payer', [draft('Amandaland', tu_demos={})])
        self.assertNotIn('id', self.w.rows(v1)[0])
        self.assertEqual(self.w.store.briefs(), [])

    def test_a_profile_that_exists_has_nothing_to_approve(self):
        v1 = self.w.order('payer', [draft(decision='existing_match', existing_match_s3_key='FBI_Most_Wanted_Viewers_11_17_2026_10_00.csv')])
        self.assertNotIn('id', self.w.rows(v1)[0])
        self.assertEqual(self.w.store.briefs(), [])

    def test_in_a_batch_only_the_settled_briefs_carry_one(self):
        v1 = self.w.order('payer', [draft(FBI), draft(FBI2, ask_identity=True, identity_data={'options': ['a', 'b']})])
        a, b = self.w.rows(v1)
        self.assertIn('id', a)
        self.assertNotIn('id', b)
        self.assertEqual(len(self.w.held('payer')), 1)

    def test_a_cut_and_a_tracker_are_quoted_at_what_the_wallet_meters(self):
        sub = draft('Outlander: Blood of My Blood Viewers on Starz', decision='subscriber_iq', subiq=RAW.subiq_block())
        v1 = self.w.order('payer', [sub])
        (row,) = self.w.rows(v1)
        want = W.pull_funding(self.w.data['users']['payer'], self.w.data, 'Chatbot Profile IQ (subscriber_iq)')['usd']
        self.assertEqual((row['price_usd'], row['decision']), (want, 'subscriber_iq'))
        out, _ = self.w.send('payer', row['approve'])
        self.assertEqual(out['brief']['state'], 'started')
        self.assertTrue(out['answer']['text'].endswith('it will be in the Subscriber IQ tab.'))
        (tx,) = self.w.deducts('Acme Studios')
        self.assertEqual(abs(float(tx['amount_usd'])), want)


# ============================================================================ 15
class WhatTheReaderIsTold(Case):
    def _lines(self):
        w = self.w
        out = [PB.DISMISSED, PB.EXPIRED, PB.UNKNOWN, PB.OUT_OF_DATE, PB.WAS_DISMISSED, PB.IN_FLIGHT, PB.NOT_HERE, PB.NO_BUILDS,
               PB.SANDBOX]
        v1 = w.order('payer', [draft(FBI), draft(FBI2)])
        a, b = w.rows(v1)
        out.append(v1['declined']['text'])
        out.append(text_of(w.send('payer', 'approve')[0]))
        out.append(text_of(w.send('payer', a['approve'])[0]))
        out.append(text_of(w.send('payer', b['dismiss'])[0]))
        out.append(text_of(w.send('liz', w.rows(w.order('liz'))[0]['approve'])[0]))
        out.append(text_of(w.send('shorty', w.rows(w.order('shorty'))[0]['approve'])[0]))
        return out

    def test_every_line_is_plain(self):
        for line in self._lines():
            self.assertTrue(line.strip(), line)
            self.assertIsNone(DASH_RX.search(line), line)
            self.assertIsNone(INSIDE_RX.search(line), line)
            self.assertEqual(GUARDS.partner_violations(line), [], line)
            self.assertNotIn('  ', line)

    def test_the_words_on_the_card_pass_the_belt_unchanged(self):
        v1 = self.w.order('payer')
        row = self.w.rows(v1)[0]
        doc = {'approve': row['approve'], 'dismiss': row['dismiss'], 'id': row['id']}
        GUARDS.scrub_partner_payload(doc)
        self.assertEqual(doc, {'approve': row['approve'], 'dismiss': row['dismiss'], 'id': row['id']})

    def test_money_reads_as_the_price_table_has_it(self):
        self.assertEqual(self.w.rows(self.w.order('payer'))[0]['price_label'], money(BUILD_USD))
        self.assertNotIn('.00', self.w.rows(self.w.order('ben'))[0]['price_label'])

    def test_every_reply_fits_the_published_schema(self):
        try:
            import jsonschema
        except Exception:
            self.skipTest('jsonschema is not installed')
        schema = json.loads((ROOT / 'prometheus' / 'schemas' / 'ask.v1.json').read_text(encoding='utf-8'))
        w = self.w
        docs = [w.order('payer', [draft(FBI), draft(FBI2)])]
        a, b = w.rows(docs[0])
        docs += [w.send('payer', 'approve')[0], w.send('payer', a['approve'])[0], w.send('payer', a['approve'])[0],
                 w.send('payer', b['dismiss'])[0], w.send('payer', 'Approve brief PB-ZZZZ-9999')[0],
                 w.send('payer', a['approve'], agent=PARTNER_AGENT)[0],
                 w.send('shorty', w.rows(w.order('shorty'))[0]['approve'])[0]]
        for doc in docs:
            jsonschema.validate(doc, schema)
        brief = schema['properties']['brief']
        self.assertEqual(brief['required'], ['id', 'state', 'closed'])
        self.assertNotIn('brief', schema['required'])                       # nothing new is required of anyone
        self.assertEqual(schema['properties']['declined']['properties']['briefs']['items']['required'], ['subject', 'decision'])


# ============================================================================ 16
def _page_script():
    html = PAGE.read_text(encoding='utf-8')
    return html[html.index('<script>') + len('<script>'):html.rindex('</script>')]


STUB = r"""
const _els = {};
function _el(id) {
  return { id: id || '', _html: '', textContent: '', value: '', disabled: false, style: {}, scrollTop: 0, scrollHeight: 0, className: '',
    classList: { add(){}, remove(){} }, _kids: {},
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); },
    querySelector(sel) { if (sel === '.empty') return null; return this._kids[sel] || (this._kids[sel] = _el()); },
    appendChild(c) { if (c && c.id) _els[c.id] = c; }, focus(){}, remove(){} };
}
const document = { getElementById: id => _els[id] || (_els[id] = _el(id)), createElement: () => _el() };
const _asks = [];
let _replies = [];
const window = { addEventListener(){}, pywebview: { api: {
  ask: async (text, cid) => { _asks.push(text); await new Promise(r => setTimeout(r, 5)); return _replies.shift() || { kind: 'declined', declined: { reason: 'network', text: 'Could not reach Crosswalk.' } }; },
  new_conversation: async () => ({ conversation_id: 'chat-1118-a1b2' }), conversations: async () => ({ conversations: [] }),
  open_dashboard(){ _asks.push('OPEN_DASHBOARD'); } } } };
function _slot(id) { return (_els['brief-' + id] || { _html: '' })._html; }
function _finish(out) { process.stdout.write(JSON.stringify(out), () => process.exit(0)); }
"""


def run_page(js):
    out = subprocess.run([NODE, '-e', STUB + _page_script() + '\n' + js], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-3000:])
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, 'node is not on the path')
class ThePage(Case):
    def _card(self, who='payer', drafts=None, **kw):
        v1 = self.w.order(who, drafts, **kw)
        return v1, self.w.rows(v1)

    def test_a_paying_seat_gets_approve_and_dismiss_and_no_trip_to_the_dashboard(self):
        v1, (row,) = self._card()
        out = run_page('S.cid = "c"; _finish({html: briefHtml(' + json.dumps(v1['declined']) + ')});')
        html = out['html']
        self.assertIn(f"Approve {FBI}</button>", html)
        self.assertIn('>Dismiss</button>', html)
        self.assertIn(money(BUILD_USD), html)
        self.assertIn('Nothing is charged until you approve. The price is the one on the brief.', html)
        self.assertNotIn('dashboard', html.lower())
        self.assertNotIn('not available in this window', html)
        self.assertIn(f'approveBrief(&quot;{row["id"]}&quot;)', html)
        self.assertNotIn(row['approve'], html)                              # the card's buttons carry the id, nothing else

    def test_an_included_seat_gets_build_and_no_price(self):
        v1, (row,) = self._card('liz')
        html = run_page('S.cid = "c"; _finish({html: briefHtml(' + json.dumps(v1['declined']) + ')});')['html']
        self.assertIn(f"Build {FBI}</button>", html)
        self.assertIn('Included with your plan', html)
        self.assertNotIn('$', html)
        self.assertNotIn('dashboard', html.lower())

    def test_one_click_sends_one_approval_and_the_card_closes(self):
        v1, (row,) = self._card()
        started, _ = self.w.send('payer', row['approve'])
        js = ('(async () => { S.cid = "c"; const d = ' + json.dumps(v1['declined']) + '; briefHtml(d); const id = ' + json.dumps(row['id']) + ';'
              '_replies = [' + json.dumps(started) + '];'
              'const a = approveBrief(id); const mid = _slot(id); const b = approveBrief(id); const c = dismissBrief(id);'
              'await Promise.all([a, b, c]); const after = _slot(id);'
              'await approveBrief(id); await dismissBrief(id);'
              '_finish({asks: _asks, mid, after, again: briefButtons(d.briefs[0]), bubble: Object.values(_els).map(e => (e._kids[".bubble"] || {})._html || "").join("|"), log: S.log}); })();')
        out = run_page(js)
        self.assertEqual(out['asks'], [row['approve']])                     # three clicks, one ask
        self.assertIn('disabled', out['mid'])                               # off while it is in flight
        self.assertEqual(out['after'], '<span class="meta">Approved. Building now.</span>')
        self.assertEqual(out['again'], out['after'])                        # drawn again, it is still closed
        self.assertNotIn('onclick', out['after'])
        self.assertIn(started['answer']['text'].replace("'", '&#39;') if "'" in started['answer']['text'] else started['answer']['text'], out['bubble'])
        self.assertNotIn('No charge', out['bubble'])                        # never "No charge" under a build that has a price
        self.assertEqual(out['log'][0], {'role': 'user', 'text': f"Approve: {FBI}"})   # the reader's bubble, without the id
        self.assertNotIn('PB-', json.dumps(out['log']))

    def test_every_end_state_closes_the_card(self):
        v1, (row,) = self._card()
        rid = row['id']
        ends = {'started': 'Approved. Building now.', 'already_running': 'Already running.', 'already_delivered': 'Already delivered.',
                'dismissed': 'Dismissed.', 'expired': 'Expired. Ask again for a fresh brief.',
                'out_of_date': 'Out of date. Ask again for a fresh brief.', 'unknown': 'Closed.'}
        for state, label in ends.items():
            kind = 'answer' if state in ('started', 'already_running', 'already_delivered', 'dismissed') else 'declined'
            reply = {'kind': kind, 'brief': {'id': rid, 'state': state, 'closed': True, 'subject': FBI}}
            reply[kind] = {'text': 'line', 'facts': [], 'followups': []} if kind == 'answer' else {'reason': 'unsupported', 'text': 'line'}
            verb = 'dismissBrief' if state == 'dismissed' else 'approveBrief'
            js = ('(async () => { S.cid = "c"; briefHtml(' + json.dumps(v1['declined']) + '); _replies = [' + json.dumps(reply) + '];'
                  f'await {verb}({json.dumps(rid)}); const n = _asks.length; await approveBrief({json.dumps(rid)}); await dismissBrief({json.dumps(rid)});'
                  f'_finish({{slot: _slot({json.dumps(rid)}), n, after: _asks.length}}); }})();')
            out = run_page(js)
            self.assertEqual(out['slot'], f'<span class="meta">{label}</span>', state)
            self.assertEqual((out['n'], out['after']), (1, 1), state)       # closed: it cannot be clicked again

    def test_short_funds_leaves_the_card_open_with_the_way_to_top_up(self):
        v1, (row,) = self._card('shorty')
        refused, _ = self.w.send('shorty', row['approve'])
        js = ('(async () => { S.cid = "c"; briefHtml(' + json.dumps(v1['declined']) + '); _replies = [' + json.dumps(refused) + ', ' + json.dumps(refused) + '];'
              f'await approveBrief({json.dumps(row["id"])}); const slot = _slot({json.dumps(row["id"])}); await approveBrief({json.dumps(row["id"])});'
              '_finish({slot, asks: _asks.length, bubble: Object.values(_els).map(e => (e._kids[".bubble"] || {})._html || "").join("|")}); })();')
        out = run_page(js)
        self.assertIn('approveBrief(', out['slot'])                         # still there, and live again
        self.assertNotIn('disabled', out['slot'])
        self.assertEqual(out['asks'], 2)
        self.assertIn('Top up to keep going.', out['bubble'])
        self.assertIn('Add funds', out['bubble'])

    def test_a_reply_that_never_arrived_gives_the_buttons_back(self):
        v1, (row,) = self._card()
        js = ('(async () => { S.cid = "c"; briefHtml(' + json.dumps(v1['declined']) + '); _replies = [];'
              f'await approveBrief({json.dumps(row["id"])}); _finish({{slot: _slot({json.dumps(row["id"])})}}); }})();')
        slot = run_page(js)['slot']
        self.assertIn('approveBrief(', slot)
        self.assertNotIn('disabled', slot)

    def test_the_card_closes_by_itself_when_the_brief_expires(self):
        v1, (row,) = self._card()
        d = copy.deepcopy(v1['declined'])
        js = ('(async () => { S.cid = "c"; const d = ' + json.dumps(d) + '; d.briefs[0].expires_at = new Date(Date.now() + 60).toISOString();'
              'briefHtml(d); await new Promise(r => setTimeout(r, 160));'
              f'const slot = _slot({json.dumps(row["id"])}); await approveBrief({json.dumps(row["id"])});'
              'const old = Object.assign({}, d.briefs[0], {id: "PB-OLD2-OLD2", expires_at: "2026-11-01T00:00:00Z"});'
              'const html = briefHtml({briefs: [old]}); await approveBrief("PB-OLD2-OLD2");'
              '_finish({slot, asks: _asks.length, html}); })();')
        out = run_page(js)
        self.assertEqual(out['slot'], '<span class="meta">Expired. Ask again for a fresh brief.</span>')
        self.assertEqual(out['asks'], 0)
        self.assertIn('Expired. Ask again for a fresh brief.', out['html'])  # one that is already past its time is drawn closed
        self.assertNotIn('approveBrief(', out['html'])

    def test_a_typed_approval_closes_the_card_too(self):
        v1, (row,) = self._card()
        started, _ = self.w.send('payer', 'approve')
        js = ('(async () => { S.cid = "c"; briefHtml(' + json.dumps(v1['declined']) + '); _replies = [' + json.dumps(started) + '];'
              f'await sendText("approve"); _finish({{slot: _slot({json.dumps(row["id"])}), asks: _asks}}); }})();')
        out = run_page(js)
        self.assertEqual(out['slot'], '<span class="meta">Approved. Building now.</span>')
        self.assertEqual(out['asks'], ['approve'])

    def test_a_brief_the_app_cannot_approve_keeps_its_way_out(self):
        v1 = self.w.order('payer', extra={'clarify_steps': ['identity']})
        html = run_page('S.cid = "c"; _finish({html: briefHtml(' + json.dumps(v1['declined']) + ')});')['html']
        self.assertIn('Review and approve in the dashboard', html)
        self.assertIn('Nothing is charged until you approve. This brief has a step that is finished in the dashboard.', html)
        self.assertNotIn('approveBrief(', html)
        self.assertNotIn('not available in this window', html)

    def test_which_one_chips_send_the_cards_own_words(self):
        v1, (a, b) = self._card(drafts=[draft(FBI), draft(FBI2)])
        which, _ = self.w.send('payer', 'approve')
        started, _ = self.w.send('payer', b['approve'])
        js = ('(async () => { S.cid = "c"; briefHtml(' + json.dumps(v1['declined']) + '); _replies = [' + json.dumps(which) + ', ' + json.dumps(started) + '];'
              'await sendText("approve"); await sendText(' + json.dumps(b['approve']) + ');'
              f'_finish({{a: _slot({json.dumps(a["id"])}), b: _slot({json.dumps(b["id"])}), asks: _asks}}); }})();')
        out = run_page(js)
        self.assertEqual(out['asks'], ['approve', b['approve']])
        self.assertEqual(out['b'], '<span class="meta">Approved. Building now.</span>')
        self.assertEqual(out['a'], '')                                      # the other card was not touched


# ============================================================================ 17
class TheWiring(unittest.TestCase):
    def test_the_door_reads_an_approval_before_a_model_and_before_the_repeat_replay(self):
        import inspect
        src = inspect.getsource(PAPI.handle_ask)
        self.assertLess(src.index('_pb.wants(text, headers)'), src.index('api_meter.repeat_count('))
        self.assertLess(src.index('_pb.wants(text, headers)'), src.index('env, _status = ask('))
        self.assertLess(src.index("has_scope(user, 'ask')"), src.index('_pb.wants(text, headers)'))
        self.assertLess(src.index('_pb.stash('), src.index('v1 = C.to_v1(env, raw'))
        self.assertIn("env.get('kind') == 'draft'", src)

    def test_the_approve_step_is_the_chat_modules_own(self):
        import inspect
        self.assertIn('_chat._pm_approve_core', inspect.getsource(PB._core))
        self.assertIn('def _pm_approve_core(user, body):', CHAT)
        core = RAW.APPROVE_SRC
        for step in ("_rpa.check(decision, spec, draft, d_type, ex_key)", '_H.has_credits_for(', '_plan.refusal_payload(',
                     '_rpa.hold()', '_H.consume_credit(', '_build_ready.note_launch('):
            self.assertIn(step, core, step)                                 # every safeguard the app's approval inherits

    def test_the_chat_module_did_not_grow(self):
        self.assertLess(len(CHAT.splitlines()), 19900)
        self.assertNotIn('pending_briefs', CHAT)

    def test_the_brief_price_table_is_the_app_readers_own(self):
        watch = (ROOT / 'prometheus' / 'legacy' / 'watch.py').read_text(encoding='utf-8')
        i = watch.index('def _pm_price_table():')
        self.assertIn("seat_username(None)", watch[i:watch.index('\ndef ', i + 1)])

    def test_the_life_and_the_reason_are_written_down(self):
        doc = PB.__doc__
        self.assertIn('24 hours', doc)
        self.assertIn('pending_offers.MAX_AGE_H', doc)
        from prometheus import pending_offers as PO
        self.assertEqual(PB.LIFE_S, PO.MAX_AGE_H * 3600)
        self.assertEqual(PB.LIFE_S, RA.WINDOW_S)

    def test_the_app_is_told_apart_by_its_own_signature(self):
        self.assertTrue(PB.is_app({'User-Agent': 'Crosswalk-Prometheus-Desktop/1.4.2'}))
        self.assertTrue(PB.is_app({'user-agent': 'Crosswalk-Prometheus-Desktop/2.0'}))
        for ua in ('', 'python-requests/2.32', 'Mozilla/5.0', 'x Crosswalk-Prometheus-Desktop/1.4.2'):
            self.assertFalse(PB.is_app({'User-Agent': ua}), ua)
        self.assertFalse(PB.is_app(None))

    def test_the_page_never_sends_an_order_or_a_price(self):
        src = PAGE.read_text(encoding='utf-8')
        self.assertIn('function approveBrief(id)', src)
        self.assertIn('function dismissBrief(id)', src)
        self.assertNotIn('Approving a priced build is not available in this window yet', src)
        i = src.index('async function actOnBrief(')
        body = src[i:src.index('\nfunction approveBrief', i)]
        self.assertIn('row.approve', body)
        for banned in ('price_usd', 'spec', 'order', 'JSON.stringify(row'):
            self.assertNotIn(banned, body, banned)


if __name__ == '__main__':
    unittest.main(verbosity=1)
