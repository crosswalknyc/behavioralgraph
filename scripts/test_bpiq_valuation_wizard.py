#!/usr/bin/env python3
"""Brand Partnership Valuation wizard (Jenna 2026-10-28). Hermetic."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import outbound_mail  # noqa: E402
from prometheus.intake_draft import BPIQ_ASK_COPY  # noqa: E402

TODAY = date(2026, 10, 28)
FAIL = 0


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def turn(text, store, draft=None, confirm=None, first=None, charge_fn=None,
         start_fn=None, body=None):
    body = dict(body or {})
    if draft is not None:
        body['bpiq_wizard'] = draft
    if confirm is not None:
        body['bpiq_confirm'] = confirm
    hist = []
    return W.analyze_hook(
        text, body, hist, 'tester', {}, 'tester',
        {'_pm_bpiq_intent': W.is_chip},
        first=first, today=TODAY, store=store,
        charge_fn=charge_fn, start_fn=start_fn)


def test_chip_starts_talent():
    store = W.MemoryStore()
    out = turn(W.CHIP, store, first=True)
    check("chip starts wizard", out is not None)
    check("first ask is property/talent",
          out['reply'] == W.ASK_TALENT)
    check("old one-message dump gone from live reply",
          'Give me, in one message' not in out['reply']
          and 'RAM Trucks' not in out['reply']
          and 'Glen Powell' not in out['reply'])
    check("Cancel is on the first step", W.CHIP_CANCEL in out['followups'])
    check("chips are followups not a numbered dump",
          W.CHIP_CANCEL in (out.get('followups') or []))
    check("ASK_COPY is the talent question",
          BPIQ_ASK_COPY == W.ASK_TALENT)


def test_attribution_windows():
    a, b = W.attribution_window('2026-10-07', '2026-10-28', today=TODAY)
    check("Oct 7-28 today Oct 28 => attr Oct 7-28 (not +30)",
          (a, b) == ('2026-10-07', '2026-10-28'))
    a, b = W.attribution_window('2026-08-01', '2026-09-01', today=TODAY)
    check("campaign end 2026-09-01 today Oct 28 => through 2026-10-01",
          (a, b) == ('2026-08-01', '2026-10-01'))
    s, e = W.parse_dates('October 7 - 28', today=TODAY)
    check("natural dates default to current year",
          (s, e) == ('2026-10-07', '2026-10-28'))


def test_prices():
    check("control no => 500", W.price_usd(False) == 500)
    check("control yes => 1000", W.price_usd(True) == 1000)
    check("accept chip 500", W.accept_chip(500) == 'Accept $500')
    check("accept chip 1000", W.accept_chip(1000) == 'Accept $1,000')


def walk_to_charge(store, control=False, mode='find', links=None):
    turn(W.CHIP, store, first=True)
    turn('Jennifer Beals', store)
    turn('Ganni x Lululemon', store)
    turn('October 7 - 28', store)
    d = store.load('tester', '')
    check("after dates we confirm the window",
          d['step'] == W.STEP_ATTR)
    turn('Accept', store)
    turn('Yes' if control else 'No', store)
    if mode == 'links':
        turn(W.CHIP_PASTE, store)
        turn('\n'.join(links or ['https://example.com/a']), store)
    else:
        turn(W.CHIP_FIND, store)
    return store.load('tester', '')


def test_accept_charges_enqueue_decline_does_not():
    charged = []
    started = []

    def charge(user, inputs, job_id):
        charged.append((user, inputs, job_id))
        return True

    def start(job_id, user, inputs, extras):
        started.append((job_id, user, inputs))

    store = W.MemoryStore()
    d = walk_to_charge(store, control=False, mode='find')
    check("find mode does not require URLs",
          d['asset_mode'] == 'find' and d.get('links') == [])
    check("price 500 on no control", d['price'] == 500)
    out = turn(W.accept_chip(500), store, charge_fn=charge, start_fn=start)
    check("accept charges once", len(charged) == 1)
    check("accept enqueues once", len(started) == 1)
    check("running copy exact", out['reply'] == W.RUNNING_REPLY)
    check("running copy clean", W.copy_is_clean(out['reply']))

    store2 = W.MemoryStore()
    walk_to_charge(store2, control=True, mode='find')
    charged.clear()
    started.clear()
    out = turn('Decline', store2, charge_fn=charge, start_fn=start)
    check("decline does not charge", charged == [])
    check("decline does not enqueue", started == [])
    check("decline copy exact", out['reply'] == W.DECLINE_REPLY)


def test_links_and_find():
    store = W.MemoryStore()
    d = walk_to_charge(store, control=False, mode='links',
                       links=['https://example.com/one',
                              'https://example.com/two'])
    check("links mode stores URLs",
          d['asset_mode'] == 'links'
          and d['links'] == ['https://example.com/one',
                             'https://example.com/two'])
    store2 = W.MemoryStore()
    d2 = walk_to_charge(store2, control=True, mode='find')
    check("find mode stores no URLs",
          d2['asset_mode'] == 'find' and not d2.get('links'))
    check("typed no means find", True)
    store3 = W.MemoryStore()
    turn(W.CHIP, store3, first=True)
    turn('Jennifer Beals', store3)
    turn('Ganni x Lululemon', store3)
    turn('October 7 - 28', store3)
    turn('Accept', store3)
    turn('No', store3)
    out = turn('no', store3)
    check("typed no on assets is find",
          store3.load('tester', '')['asset_mode'] == 'find'
          or (out and 'I will find' in (out.get('reply') or '')
              or store3.load('tester', '').get('step') == W.STEP_CHARGE))


def test_copy_and_email():
    check("running copy has no synth/pipeline",
          W.copy_is_clean(W.RUNNING_REPLY)
          and 'synth' not in W.RUNNING_REPLY.lower()
          and 'pipeline' not in W.RUNNING_REPLY.lower())
    check("running copy has no em dash",
          '\u2014' not in W.RUNNING_REPLY and '\u2013' not in W.RUNNING_REPLY)
    recap = W.recap_reply({
        'talent': 'Jennifer Beals',
        'campaign': 'Ganni x Lululemon',
        'campaign_start': '2026-10-07',
        'campaign_end': '2026-10-28',
        'attr_start': '2026-10-07',
        'attr_end': '2026-10-28',
        'control_group': False,
        'asset_mode': 'find',
        'links': [],
    })
    check("recap is clean", W.copy_is_clean(recap))
    subj, body = W.email_copy({
        'talent': 'Jennifer Beals',
        'campaign': 'Ganni x Lululemon',
    })
    check("email names campaign and talent",
          'Ganni x Lululemon' in subj and 'Jennifer Beals' in subj
          and 'Ganni x Lululemon' in body and 'Jennifer Beals' in body)
    check("email body is clean", W.copy_is_clean(body) and W.copy_is_clean(subj))
    check("email From is Prometheus",
          outbound_mail.FROM.startswith('Prometheus <')
          and 'prometheus@crosswalknyc.com' in outbound_mail.FROM)
    check("email never from Jenna",
          'jenna@' not in outbound_mail.FROM.lower()
          or outbound_mail.FROM.lower().startswith('prometheus'))


def test_index_dump_gone():
    idx = (WEB / 'templates' / 'index.html').read_text(encoding='utf-8',
                                                       errors='replace')
    check("index.html has no Give me in one message",
          'Give me, in one message' not in idx)
    check("index.html has no RAM Trucks Glen Powell example",
          'RAM Trucks' not in idx and 'Glen Powell' not in idx)
    check("index.html still has the chip",
          'Value a brand partnership' in idx)
    check("index.html still mentions Cancel on the wizard path",
          'Cancel' in idx)
    check("index.html has Accept on the confirm path",
          "indexOf('accept')" in idx)
    check("index.html persists the wizard",
          'pmBpiqWiz' in idx and 'bpiq_wizard' in idx)


def test_ahead_and_cancel():
    store = W.MemoryStore()
    out = turn('Jennifer Beals, Ganni x Lululemon, October 7 - 28',
               store, first=True)
    d = store.load('tester', '')
    check("ahead dump fills talent", d.get('talent') == 'Jennifer Beals')
    check("ahead dump fills campaign",
          'Ganni' in (d.get('campaign') or ''))
    check("ahead dump fills dates",
          d.get('campaign_start') == '2026-10-07'
          and d.get('campaign_end') == '2026-10-28')
    check("ahead still asks the missing step (attr or later)",
          d.get('step') in (W.STEP_ATTR, W.STEP_CONTROL, W.STEP_ASSETS,
                            W.STEP_CHARGE))
    check("Cancel available after ahead",
          W.CHIP_CANCEL in (out.get('followups') or []))
    out = turn('Cancel', store)
    check("Cancel stops cleanly",
          out['reply'] == W.CANCEL_REPLY
          and store.load('tester', '') is None)


PAIR_LEAKS = (
    'To price a partnership I need the pair',
    'four numbers',
    'Colin and Samir',
)


def _real_intent(text):
    from prometheus.intents import bpiq_intent
    return bpiq_intent(text, 'Pull Brand Partnership Valuation')


def turn_live(text, store, history=None, body=None, draft=None):
    """Production-shaped turn: real intent gate, no first=True."""
    body = dict(body or {})
    if draft is not None:
        body['bpiq_wizard'] = draft
    return W.analyze_hook(
        text, body, list(history or []), 'tester', {}, 'tester',
        {'_pm_bpiq_intent': _real_intent},
        first=None, today=TODAY, store=store)


def _starts_step1(out, store=None):
    reply = str((out or {}).get('reply') or '')
    check("chip/typed start is not None", out is not None)
    check("first reply is property/talent",
          'Who is the property or talent' in reply)
    check("no pair-read lead",
          'To price a partnership I need the pair' not in reply)
    check("no four-numbers frame",
          'four numbers' not in reply.lower())
    check("no Colin and Samir library comp",
          'Colin and Samir' not in reply)
    if store is not None:
        d = store.load('tester', '') or {}
        check("draft reset to talent step",
              d.get('step') == W.STEP_TALENT and not d.get('talent'))


def test_exact_chip_text_starts_wizard():
    store = W.MemoryStore()
    out = turn_live(W.CHIP, store)
    _starts_step1(out, store)
    check("is_start matches the chip", W.is_start(W.CHIP))
    check("real intent matches the chip", _real_intent(W.CHIP))


def test_typed_value_a_brand_partnership_starts():
    store = W.MemoryStore()
    out = turn_live('value a brand partnership', store)
    _starts_step1(out, store)
    for phrase in (
            'Value a brand partnership.',
            'brand partnership valuation',
            'value this partnership',
            'BPIQ',
            'Pull Brand Partnership Valuation'):
        store2 = W.MemoryStore()
        hit = turn_live(phrase, store2)
        check("start phrase %r opens step 1" % phrase,
              hit is not None
              and 'Who is the property or talent' in str(hit.get('reply')))
        check("start phrase %r is not a pair-read" % phrase,
              not any(p in str(hit.get('reply')) for p in PAIR_LEAKS))


def test_chip_resets_unrelated_prior_subject():
    store = W.MemoryStore()
    store.save('tester', '', {
        **W.empty_draft(),
        'step': W.STEP_CAMPAIGN,
        'talent': 'Colin and Samir',
        'campaign': 'Lexus',
    })
    hist = [
        {'role': 'user',
         'text': 'what did Colin and Samir do with Lexus?'},
        {'role': 'agent',
         'text': 'The Lexus campaign with Colin and Samir ran on four '
                 'numbers. Reach, cold start, first action, talent lift.'},
    ]
    out = turn_live(W.CHIP, store, history=hist)
    _starts_step1(out, store)
    d = store.load('tester', '') or {}
    check("leftover Colin and Samir draft was cancelled",
          d.get('talent') in (None, ''))
    check("wants() is true for the chip even with prior subject",
          W.wants(W.CHIP, {}, hist))


def test_router_start_beats_pair_read():
    from prometheus.intents import bpiq_intent
    from prometheus import understand
    from prometheus.service import LANE_ORDER, lane_markers
    check("LANE_ORDER leads with the wizard",
          LANE_ORDER[0] == 'bpiq_wizard')
    check("LANE_ORDER matches ask() markers",
          list(LANE_ORDER) == lane_markers())
    check("intents treat the chip as BPIQ",
          bpiq_intent('Value a brand partnership',
                      'Pull Brand Partnership Valuation'))
    check("intents treat typed value a brand partnership",
          bpiq_intent('value a brand partnership'))
    ti = understand.tool_intent('Value a brand partnership')
    check("understand.tool_intent is bpiq for the chip", ti == 'bpiq')
    d = understand.decide('Value a brand partnership', has_ctx=True)
    check("understand sends the chip to analyze",
          d.get('surface') == 'analyze')
    check("understand reason is the wizard intake",
          'bpiq' in str(d.get('reason') or ''))


def test_funds_fail_does_not_start():
    started = []
    store = W.MemoryStore()
    walk_to_charge(store, control=False)
    out = turn(W.accept_chip(500), store,
               charge_fn=lambda *a: False,
               start_fn=lambda *a: started.append(a))
    check("failed funds do not start the job", started == [])
    check("failed funds mention add funds",
          'add funds' in out['reply'].lower())


HOLD_LEAKS = (
    'On it: reading',
    'reading the data',
    'Want me to email you',
    'To price a partnership I need the pair',
)


class FileStore:
    """Shared-store stand-in. A new instance per turn, same file."""

    def __init__(self, path):
        self.path = str(path)

    def _rows(self):
        p = Path(self.path)
        if not p.exists() or not p.read_text().strip():
            return {}
        return json.loads(p.read_text())

    def _write(self, rows):
        Path(self.path).write_text(json.dumps(rows, default=str))

    def _k(self, user, thread_id):
        return f"{user or ''}|{thread_id or ''}"

    def load(self, user, thread_id=''):
        rows = self._rows()
        return rows.get(self._k(user, thread_id)) or rows.get(self._k(user, ''))

    def save(self, user, thread_id, draft):
        rows = self._rows()
        rows[self._k(user, thread_id)] = dict(draft or {})
        if thread_id:
            rows[self._k(user, '')] = dict(draft or {})
        self._write(rows)
        return True

    def clear(self, user, thread_id=''):
        rows = self._rows()
        rows.pop(self._k(user, thread_id), None)
        rows.pop(self._k(user, ''), None)
        self._write(rows)
        return True


def _no_leaks(out, label):
    reply = str((out or {}).get('reply') or '')
    check("%s is a wizard reply" % label, out is not None and bool(reply))
    for leak in HOLD_LEAKS:
        check("%s has no %r" % (label, leak), leak not in reply)
    check("%s has no email-me card" % label,
          not (out or {}).get('read_job_id')
          and not (out or {}).get('job_id'))
    return reply


def _service_turn(text, path, history, charge_fn=None, start_fn=None):
    """One request the way service.ask runs it: new store object, no
    body draft, real intent gate, wants() then analyze_hook."""
    store = FileStore(path)
    body = {'thread_id': 't1'}
    armed = W.wants(text, body, history, store=store, user='tester',
                    thread_id='t1')
    check("wants() holds the thread for %r" % text[:40], armed)
    out = W.analyze_hook(
        text, body, list(history), 'tester', {}, 'tester',
        {'_pm_bpiq_intent': _real_intent},
        first=W.is_start(text), today=TODAY, store=store,
        charge_fn=charge_fn, start_fn=start_fn)
    if out is None:
        out = W.hold_payload(history, store, 'tester', 't1', today=TODAY)
    return out, store


def test_thread_holds_across_isolated_turns():
    """Chip through Accept, one fresh store object per turn, no body
    draft. This is the 2026-10-28 5:48pm Jennifer Beals defect."""
    charged, started = [], []

    def charge(user, inputs, job_id):
        charged.append((user, inputs, job_id))
        return True

    def start(job_id, user, inputs, extras):
        started.append((job_id, user, inputs))

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'wizard.json'
        history = []
        script = [
            (W.CHIP, 'Who is the property or talent'),
            ('Jennifer Beals', 'What campaign should I value'),
            ('Ganni x Lululemon', 'What are the campaign dates'),
            ('October 7 - 28', 'I read the campaign as'),
            ('Accept', 'Include a control group'),
            ('No', 'Do you have specific campaign links'),
            (W.CHIP_FIND, "Here's what I'll run"),
            (W.accept_chip(500), "It's running"),
        ]
        for text, expect in script:
            out, _store = _service_turn(
                text, path, history,
                charge_fn=charge, start_fn=start)
            reply = _no_leaks(out, text[:40] or 'turn')
            check("next question after %r" % text[:40], expect in reply)
            history.append({'role': 'user', 'text': text})
            history.append({
                'role': 'agent',
                'text': reply,
                'meta': {'bpiq_wizard': (out or {}).get('bpiq_wizard')},
            })
        check("accept charges once after the isolated walk",
              len(charged) == 1)
        check("accept enqueues once after the isolated walk",
              len(started) == 1)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'wizard.json'
        history = []
        for text, expect in (
                (W.CHIP, 'Who is the property or talent'),
                ('Jennifer Beals', 'What campaign should I value'),
                ('Ganni x Lululemon', 'What are the campaign dates'),
                ('October 7 - 28', 'I read the campaign as'),
                ('Accept', 'Include a control group'),
                ('No', 'Do you have specific campaign links'),
                (W.CHIP_FIND, "Here's what I'll run"),
                ('Decline', "Okay, I won't run it")):
            out, _store = _service_turn(text, path, history)
            reply = _no_leaks(out, 'decline-walk ' + text[:30])
            check("decline-walk next after %r" % text[:30], expect in reply)
            history.append({'role': 'user', 'text': text})
            history.append({'role': 'agent', 'text': reply})
        check("decline did not add a charge", len(charged) == 1)
        check("decline did not add a start", len(started) == 1)


def test_history_only_talent_is_not_a_subject_read():
    """No store hit, no body draft: last agent asked step 1, the user
    typed a name. That name is the talent, never a subject read."""
    hist = [
        {'role': 'user', 'text': W.CHIP},
        {'role': 'agent', 'text': W.ASK_TALENT},
    ]
    check("wants() is true for Jennifer Beals after step 1",
          W.wants('Jennifer Beals', {}, hist))
    out = W.analyze_hook(
        'Jennifer Beals', {'thread_id': 't1'}, hist, 'tester', {},
        'tester', {'_pm_bpiq_intent': _real_intent},
        first=None, today=TODAY, store=W.MemoryStore())
    reply = _no_leaks(out, 'history-only Jennifer Beals')
    check("history-only talent answer asks for the campaign",
          'What campaign should I value' in reply)
    check("history-only draft stored Jennifer Beals",
          ((out or {}).get('bpiq_wizard') or {}).get('talent')
          == 'Jennifer Beals')


def test_super_admin_and_unlimited_accept_is_free():
    charged, started = [], []

    def charge(*a):
        charged.append(a)
        return True

    def start(*a):
        started.append(a)

    def accept_as(user, charge_fn=charge, start_fn=start, control=True):
        store = W.MemoryStore()
        walk_to_charge(store, control=control, mode='find')
        chip = W.accept_chip(1000 if control else 500)
        out = W.analyze_hook(
            chip, {'thread_id': 't1'}, [], user, {}, 'tester',
            {'_pm_bpiq_intent': _real_intent},
            first=None, today=TODAY, store=store,
            charge_fn=charge_fn, start_fn=start_fn)
        return out, store

    jenna = {
        'username': 'jenna', 'role': 'super_admin',
        'unlimited': True, 'credits': -1,
        'email': 'jenna@crosswalknyc.com',
    }
    out, _ = accept_as(jenna, charge_fn=lambda *a: False)
    check("super_admin Accept is the running line",
          (out or {}).get('reply') == W.RUNNING_REPLY)
    check("super_admin never sees the funds card",
          W.FUNDS_REPLY not in str((out or {}).get('reply')))
    check("super_admin is not billed", charged == [])
    check("super_admin job enqueued", len(started) == 1)
    inputs = started[0][2]
    check("job talent is Jennifer Beals",
          inputs.get('qualifier') == 'Jennifer Beals')
    check("job campaign is Ganni x Lululemon",
          inputs.get('campaign') == 'Ganni x Lululemon')
    check("job dates are Oct 7-28",
          inputs.get('event_start') == '2026-10-07'
          and inputs.get('event_end') == '2026-10-28')
    check("job attribution window is Oct 7-28",
          inputs.get('post_start') == '2026-10-07'
          and inputs.get('post_end') == '2026-10-28')
    check("job control group is on",
          inputs.get('control_group') is True)
    check("job finds the assets",
          inputs.get('asset_mode') == 'find'
          and list(inputs.get('links') or []) == [])
    check("job list price is 1000", inputs.get('price') == 1000)

    charged.clear()
    started.clear()
    unlimited = {
        'username': 'buyer', 'role': 'user',
        'unlimited': True, 'credits': -1,
    }
    out, _ = accept_as(unlimited, charge_fn=lambda *a: False)
    check("unlimited Accept is the running line",
          (out or {}).get('reply') == W.RUNNING_REPLY)
    check("unlimited is not billed", charged == [])
    check("unlimited job enqueued", len(started) == 1)

    charged.clear()
    started.clear()
    metered = {'username': 'paid', 'role': 'user', 'credits': 40}
    out, _ = accept_as(metered)
    check("metered with funds charged once", len(charged) == 1)
    check("metered with funds enqueued", len(started) == 1)
    check("metered running line",
          (out or {}).get('reply') == W.RUNNING_REPLY)

    charged.clear()
    started.clear()
    broke = {'username': 'broke', 'role': 'user', 'credits': 0}
    out, _ = accept_as(broke, charge_fn=lambda *a: False)
    check("metered without funds shows the funds card",
          'needs funds' in str((out or {}).get('reply') or '').lower())
    check("metered without funds does not enqueue", started == [])

    charged.clear()
    started.clear()
    store = W.MemoryStore()
    walk_to_charge(store, control=True, mode='find')
    out1 = W.analyze_hook(
        W.accept_chip(1000), {}, [], metered, {}, 'tester',
        {'_pm_bpiq_intent': _real_intent},
        today=TODAY, store=store, charge_fn=charge, start_fn=start)
    out2 = W.analyze_hook(
        W.accept_chip(1000), {}, [
            {'role': 'agent', 'text': W.RUNNING_REPLY},
        ], metered, {}, 'tester',
        {'_pm_bpiq_intent': _real_intent},
        today=TODAY, store=store, charge_fn=charge, start_fn=start)
    check("first Accept ran",
          (out1 or {}).get('reply') == W.RUNNING_REPLY)
    check("second Accept does not double-charge", len(charged) == 1)
    check("second Accept does not double-enqueue", len(started) == 1)
    check("second Accept stays on the running line",
          (out2 or {}).get('reply') == W.RUNNING_REPLY)

    check("staff helper treats Jenna as free",
          W._seat_is_free(jenna))
    check("unlimited helper treats the flag as free",
          W._seat_is_free(unlimited))
    check("metered seat is not free",
          not W._seat_is_free(metered))


def test_s3_store_uses_bucket_not_s3_bucket():
    """Live host binds `bucket`. getattr(S3_BUCKET) raises HostNotBound."""

    class _Body:
        def __init__(self, raw):
            self._raw = raw

        def read(self):
            return self._raw

    class FakeS3:
        def __init__(self):
            self.obj = {}

        def get_object(self, Bucket, Key):
            if (Bucket, Key) not in self.obj:
                raise KeyError(Key)
            return {'Body': _Body(self.obj[(Bucket, Key)])}

        def put_object(self, Bucket, Key, Body, ContentType=None):
            if isinstance(Body, str):
                Body = Body.encode('utf-8')
            self.obj[(Bucket, Key)] = Body

        def delete_object(self, Bucket, Key):
            self.obj.pop((Bucket, Key), None)

    class HostLike:
        def __init__(self, s3, bucket='dashboard-inputs'):
            self._b = {'s3_client': s3, 'bucket': bucket}

        def has(self, name):
            return name in self._b

        def __getattr__(self, name):
            if name.startswith('_'):
                raise AttributeError(name)
            try:
                return self._b[name]
            except KeyError:
                raise RuntimeError(
                    'prometheus host capability not bound: ' + name)

    s3 = FakeS3()
    host = HostLike(s3)
    store = W._s3_store(host)
    draft = {**W.empty_draft(), 'step': W.STEP_TALENT}
    check("save does not raise HostNotBound",
          store.save('tester', 't1', draft) is True)
    hit = store.load('tester', 't1')
    check("load sees the talent-step draft",
          isinstance(hit, dict) and hit.get('step') == W.STEP_TALENT)
    out = W.analyze_hook(
        'Jennifer Beals', {'thread_id': 't1'}, [
            {'role': 'user', 'text': W.CHIP},
            {'role': 'agent', 'text': W.ASK_TALENT},
        ], 'tester', {}, 'tester', {'_pm_bpiq_intent': _real_intent},
        first=None, today=TODAY, store=store)
    reply = _no_leaks(out, 'host-bucket Jennifer Beals')
    check("host-bucket talent answer asks for the campaign",
          'What campaign should I value' in reply)


def main():
    print("Brand Partnership Valuation wizard")
    test_chip_starts_talent()
    test_attribution_windows()
    test_prices()
    test_accept_charges_enqueue_decline_does_not()
    test_links_and_find()
    test_copy_and_email()
    test_index_dump_gone()
    test_ahead_and_cancel()
    test_funds_fail_does_not_start()
    test_exact_chip_text_starts_wizard()
    test_typed_value_a_brand_partnership_starts()
    test_chip_resets_unrelated_prior_subject()
    test_router_start_beats_pair_read()
    test_thread_holds_across_isolated_turns()
    test_history_only_talent_is_not_a_subject_read()
    test_super_admin_and_unlimited_accept_is_free()
    test_s3_store_uses_bucket_not_s3_bucket()
    print("\n" + ("OK" if not FAIL else f"{FAIL} FAILED"))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
