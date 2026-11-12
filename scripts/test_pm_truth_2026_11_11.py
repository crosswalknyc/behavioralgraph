#!/usr/bin/env python3
"""What Prometheus says is true (Jenna 2026-11-11: "these errors cannot
happen. fix and ensure itll be correct going forward").

Two readers were told things that were not true on 2026-11-11: Armine
at Lionsgate and Jessie on our team. Every case below is built from
their own words and from the replies as they were stored before they
were corrected by hand (scripts/fixtures/pm_truth_2026_11_11.json).

  1. A reply says a deck, a file, an email, a build or a read was sent,
     built or started only with the record of it on the reply.
                                                   -> AClaimNeedsARecord
  2. A progress line is posted only for a run that exists; a refusal, a
     failure or a cancel replaces it with the real outcome in the
     client's words; a promise has a record; the sweep finds a thread
     that ends on one with nothing behind it.     -> AProgressLineHasARun
  3. A PowerPoint asked for at the end of a question is carried through
     the deck path, never dropped.              -> ADeliverableRidesThrough
  4. An order for a profile, and a correction of the lane ("NO! not a
     read. a full profile"), go to the build interpret step; no flag
     rides the request.                       -> AnOrderForAProfileIsBuilt
  5. What a build carries is read from the file at answer time; a build
     with no file yet is still building.        -> ABuildIsReadFromTheFile
  6. The door rule is a deck rule: a chat reply is never scrubbed for
     it, and a title, a brand or a person's name is never touched.
                                                -> TheDoorRuleIsADeckRule
  7. Two saved journeys under one show's name are told apart by the
     words that differ; one short question only when they cannot be.
                                                   -> TwoJourneysOneName

Hermetic: the stub host and in-memory store of test_pm_new_chat_no_bleed.
No model, no S3, no app module, no mail.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

from prometheus import build_facts as BF  # noqa: E402
from prometheus import build_lane as BL  # noqa: E402
from prometheus import build_ready as BR  # noqa: E402
from prometheus import door_words as DW  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus import journey_pick as JP  # noqa: E402
from prometheus import orders as ORD  # noqa: E402
from prometheus import promise_truth as PT  # noqa: E402
from prometheus import reply_truth as RT  # noqa: E402
from prometheus import ride_along as RA  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus.host import host  # noqa: E402

APP, World, USER, THREADS = NB.APP, NB.World, NB.USER, NB.THREADS
FX = json.loads(Path(HERE, 'fixtures', 'pm_truth_2026_11_11.json').read_text(encoding='utf-8'))
JW, JR, AW, AR = FX['jessie_words'], FX['jessie_reads'], FX['armine_words'], FX['armine_reads']
JT = FX['jessie_thread']                       # thread ebf520b02f from 19:06 UTC on
CALM = 'Working on it. Confirmed your task is in progress and I will email you when it completes.'
# the engine's own record of run 4pKpMKnh7Lt77Q: started 19:07:45 UTC, refused 19:08:02, with this line for the client
REFUSAL = ("We can't see Georgie & Mandy's First Marriage on Paramount Plus at this time. Notifying Crosswalk and will get back to you "
           "soon.")
PAGE = {'view': 'profile-iq', 'primary': {'s3_key': 'GEORGIE__MANDYS_FIRST_MARRIAGE_Viewers_11_11_2026_22_26.csv',
                                           'name': "GEORGIE & MANDY'S FIRST MARRIAGE Viewers"},
        'view_context': {'view_id': 'profileIQ', 'view_title': "GEORGIE & MANDY'S FIRST MARRIAGE Viewers", 'data': {}}}
T_J = 'ebf520b02f'


def _before(words):
    """Jessie's thread as it stood when she sent these words."""
    for i, t in enumerate(JT):
        if t.get('role') == 'user' and t.get('text') == words:
            return JT[:i]
    raise AssertionError('not in the thread: ' + words[:40])


def _csv(n_inputs=None):
    rows = FX['file_rows']
    bi = list(rows['brand_input'])
    if n_inputs is not None:
        bi[1] = ', '.join([x.strip() for x in bi[1].split(',')][:n_inputs])
    buf = io.StringIO()
    import csv as _csv_mod
    w = _csv_mod.writer(buf)
    for r in (rows['header'], bi, rows['sample_size'], ['SUBJECT', "GEORGIE & MANDY'S FIRST MARRIAGE", '100.0000', '100.0', '38965', '1285455', '', ''],
              ['GENDER', 'FEMALE', '58.1234', '58.1234', '22648', '747148', '50.8', '114']):
        w.writerow(r)
    return buf.getvalue().encode('utf-8')


class _RangeS3(NB.FakeS3):
    """The fake store, with the Range read and LastModified a profile read uses."""

    def get_object(self, Bucket, Key, Range=None):
        out = super().get_object(Bucket, Key)
        import datetime as _dt
        out['LastModified'] = _dt.datetime(2026, 11, 11, 23, 48, 5, tzinfo=_dt.timezone.utc)
        return out


# ============================================================================ 1
class AClaimNeedsARecord(unittest.TestCase):
    def test_armines_read_said_a_deck_was_sent_and_none_existed(self):
        """21:09 UTC: "The deck is updated ... the PowerPoint linked above, 13 slides, sent to your inbox with the
        file attached. Crosswalk Inter is embedded and the brand check passed." No deck job, no send record."""
        read = AR['deck_sent_claim']
        self.assertIn('sent to your inbox', read)
        kinds = {k for k, _s in RT.claims(read)}
        self.assertTrue({'deck', 'email'} <= kinds)
        payload = {'success': True, 'action': 'answer', 'reply': read, 'followups': [],
                   'file_link': {'url': 'https://x/y.csv', 'label': 'Download CSV'}}
        RT.finish_read(payload, question=AW['summarize_in_powerpoint'])
        out = payload['reply']
        for gone in ('sent to your inbox', 'PowerPoint linked above', 'Crosswalk Inter is embedded', 'brand check passed', 'The deck is updated'):
            self.assertNotIn(gone, out)
        self.assertEqual(RT.claims(out), [])                        # nothing left that claims a thing happened
        for kept in ('14,862,537', '3,567,009', 'Commerce behavior skews', 'How these are counted:', 'Window: Oct 21 2025 to Oct 21 2026.'):
            self.assertIn(kept, out)                                # the read itself is whole
        self.assertIn('deck', payload['claims_removed'])

    def test_the_read_the_night_before_said_the_deck_was_built(self):
        read = AR['deck_built_claim']
        self.assertIn('The deck is built and in the thread', read)
        out, removed = RT.scrub(read, set())
        self.assertNotIn('The deck is built', out)
        self.assertNotIn('Slides in the deck', out)
        self.assertTrue(removed)

    def test_a_read_cannot_say_a_build_was_started(self):
        out, removed = RT.scrub('The audience is 2,114,208 US viewers. ' + FX['jay_build_claim'] + ' The core is 25 to 44.', set())
        self.assertEqual(out, 'The audience is 2,114,208 US viewers. The core is 25 to 44.')
        self.assertEqual([k for k, _s in removed], ['build'])

    def test_the_record_on_the_reply_backs_the_claim(self):
        line = 'The deck is built and attached above.'
        self.assertEqual(RT.scrub(line, {'deck'})[1], [])
        self.assertEqual(RT.backed({'deck_job_id': 'abc'}), {'deck', 'progress'})
        self.assertIn('file', RT.backed({'file_link': {'url': 'https://x/y.csv'}}))
        self.assertIn('email', RT.backed({'emailed': True}))
        self.assertEqual(RT.backed({'run_id': 'r1'}), {'build', 'progress'})
        self.assertEqual(RT.backed({'reply': 'x'}), set())
        # a read's own CSV link backs a sentence about its file, and nothing else
        p = {'success': True, 'reply': 'The CSV is attached above. The deck is attached above. Reach is 4,194,382.',
             'file_link': {'url': 'https://x/y.csv'}}
        RT.finish_read(p)
        self.assertEqual(p['reply'], 'The CSV is attached above. Reach is 4,194,382.')

    def test_the_systems_own_lines_are_not_touched(self):
        """A progress line with its job, a draft, an offer and guidance are written by the code that did the thing."""
        ack = {'success': True, 'action': 'answer', 'read_job_id': 'a1b2c3d4e5f6',
               'reply': 'On it: reading YouTube now. This one takes a real look at the data, so give me a moment - the read will land '
                        'right here when it is ready.'}
        self.assertEqual(RT.finish_answer(dict(ack))['reply'], ack['reply'])
        offer = {'success': True, 'action': 'answer', 'reply': 'I can pull that. I will email you the file.', 'panel_offer': {'x': 1}}
        self.assertEqual(RT.finish_answer(dict(offer))['reply'], offer['reply'])
        sent = {'success': True, 'action': 'answer', 'reply': 'Sent the profile to ameseldzhyan@lionsgate.com.', 'emailed': True}
        self.assertEqual(RT.finish_answer(dict(sent))['reply'], sent['reply'])
        # the core's own send line carries no model and no figure ledger: it is not a written answer
        core = {'success': True, 'action': 'answer', 'reply': 'Sent. YouTube originals category.csv is on its way to ameseldzhyan@lionsgate.com.',
                'followups': [], 'offer_deck': False, 'deck_angle': None}
        self.assertEqual(RT.finish_answer(dict(core))['reply'], core['reply'])
        written = dict(core, reply='Reach is 4,194,382. I have sent the deck to your inbox.', model='m', figures=[])
        self.assertEqual(RT.finish_answer(written)['reply'], 'Reach is 4,194,382.')
        # the calm promise is promise_truth's to back (a held record), never a sentence to cut out of a reply
        for calm in (CALM, 'Included with your plan. ' + CALM):
            held = {'success': True, 'action': 'answer', 'reply': calm, 'followups': []}
            self.assertEqual(RT.finish_read(dict(held))['reply'], calm)
            self.assertEqual(RT.finish_answer(dict(held, held=True))['reply'], calm)
            self.assertEqual(RT.finish_answer(dict(held))['reply'], calm)

    def test_a_plain_read_is_left_alone(self):
        for ok in ('The audience is built on Paramount+ and nowhere else.',
                   'Comedy leads because its formats are cheap to repeat and easy to clip.',
                   'The profile is running hot on TikTok: 61.2% penetration, index 214.',
                   'Viewers who sent a clip to a friend are 2.1x more likely to finish the season.',
                   'The PowerPoint user base is built around students and office workers.',
                   'The read below covers the window Oct 21 2025 to Oct 21 2026.'):
            self.assertEqual(RT.scrub(ok, set()), (ok, []), ok)

    def test_a_written_answer_from_the_core_is_checked_at_the_service_exit(self):
        w = World()
        said = ('Las Culturistas Culture Awards Viewers skew 25-34 at 32.5%. I have built the deck and sent it to your inbox. '
                'The female share is 57.6%.')

        def _core(user, body, text, history):
            w.calls.append(('analyze', text, dict(body), {}))
            return {'success': True, 'action': 'answer', 'reply': said, 'followups': [], 'figures': [], 'model': 'm',
                    'profile': 'Las Culturistas Culture Awards Viewers'}
        host.bind(analyze_core=_core)
        env, _st, _g = w.ask('Summary of demographics for Las Culturistas Culture Awards Viewers', thread_id=w.new_thread(), history=[],
                             page=NB.MAD_PAGE, surface='analyze')
        self.assertNotIn('inbox', env['text'])
        self.assertNotIn('built the deck', env['text'])
        self.assertIn('32.5%', env['text'])
        self.assertIn('57.6%', env['text'])

    def test_the_wiring(self):
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertIn("fromlist=['finish_read']).finish_read(payload, question=text)", chat)      # the finished read's exit
        svc = Path(ROOT, 'prometheus', 'service.py').read_text(encoding='utf-8')
        self.assertIn('_rtr.finish_answer(raw, question=text)', svc)                              # the written answer's exit
        self.assertLess(len(chat.splitlines()), 19900)                                            # the ratchet holds


# ============================================================================ 2
class AProgressLineHasARun(unittest.TestCase):
    def test_the_outcome_is_the_clients_own_line(self):
        st = FX['jessie_refusal_status']
        self.assertEqual(PT.outcome_line(st), (REFUSAL + ' The charge for this run has been refunded.', 'failed'))
        self.assertEqual(PT.outcome_line({'status': 'failed', 'error': 'KeyError: tu_key in synth_engine_row_by_row.py:812'}),
                         ('This build could not be completed. Credits for this run have been refunded.', 'failed'))
        self.assertEqual(PT.outcome_line({'status': 'cancelled', 'cancel_note': 'Stopped at your request. The charge for this run was returned.'}),
                         ('Stopped at your request. The charge for this run was returned.', 'cancelled'))
        for alive in ('queued', 'running', 'tu_done', 'complete'):
            self.assertEqual(PT.outcome_line({'status': alive}), ('', ''))

    def test_jessies_refused_build_as_the_thread_stood_at_19_08(self):
        """"Profile building ... I will email you when it is complete", "... is building.", then the calm line,
        17 seconds after a refusal. The build's own line becomes the refusal; the two under it are withdrawn."""
        thread = FX['jessie_refused']
        self.assertEqual([t['role'] for t in thread], ['user', 'agent', 'agent', 'agent'])
        self.assertTrue(thread[1]['text'].startswith('Included with your plan. Profile building:'))
        self.assertTrue(thread[2]['text'].endswith('is building.'))
        self.assertEqual(thread[3]['text'], CALM)
        before = PT.thread_tail(thread)
        self.assertEqual((before['ends_on'], before['kind']), ('promise', 'calm'))
        out, how = PT.settle_history(thread, '4pKpMKnh7Lt77Q', FX['jessie_refusal_status'])
        self.assertEqual(how, 'settled')
        self.assertTrue(out[1]['text'].startswith(REFUSAL))
        self.assertEqual(out[1]['turn_id'], thread[1]['turn_id'])                       # the same turn, new words
        self.assertEqual(out[1]['meta']['build_outcome']['run_id'], '4pKpMKnh7Lt77Q')
        self.assertTrue(out[1]['meta']['corrected_at'] and out[1]['meta']['server_note'])
        self.assertIn('Profile building', out[1]['meta']['retired'])
        self.assertTrue(out[2]['meta']['withdrawn'] and out[3]['meta']['withdrawn'])
        self.assertNotIn('building', out[2]['text'])
        self.assertFalse(PT.is_calm(out[3]['text']))
        self.assertEqual(out[0], thread[0])                                             # her words are untouched
        after = PT.thread_tail(out)
        self.assertEqual(after['ends_on'], 'answer')                                    # the thread now ends on the outcome
        self.assertTrue(after['text'].startswith(REFUSAL))
        # once
        again, how2 = PT.settle_history(out, '4pKpMKnh7Lt77Q', FX['jessie_refusal_status'])
        self.assertEqual((how2, again), ('already', out))
        # a later calm line that belongs to another question is not withdrawn
        later = thread + [{'role': 'user', 'text': 'what about HBO Max?', 'ts': '2026-11-11T19:20:00Z'}, {'role': 'agent', 'text': CALM, 'ts': '2026-11-11T19:20:05Z'}]
        out2, _how = PT.settle_history(later, '4pKpMKnh7Lt77Q', FX['jessie_refusal_status'])
        self.assertEqual(out2[-1]['text'], CALM)

    def test_a_stale_tab_cannot_put_the_progress_line_back(self):
        """The server's reading of the turn wins every later merge (prometheus.thread_merge)."""
        from prometheus import thread_merge as TM
        thread = FX['jessie_refused']
        settled, _how = PT.settle_history(thread, '4pKpMKnh7Lt77Q', FX['jessie_refusal_status'])
        merged = TM.merge_turns(settled, thread)                                         # the tab pushes its old copy
        self.assertEqual(len(merged), 4)
        self.assertTrue(merged[1]['text'].startswith(REFUSAL))
        self.assertTrue(merged[3]['meta']['withdrawn'])

    def test_with_no_browser_open_the_sweep_puts_the_outcome_in_the_launching_thread(self):
        w = World()
        w.put(f'{THREADS}/{USER}/{T_J}.json', FX['jessie_refused'])
        idx = w.index()
        idx['threads'].append({'id': T_J, 'title': 'run a NEW profile', 'created': NB._iso(-600), 'updated': NB._iso(-500), 'turns': 4})
        w.put(f'{THREADS}/{USER}/index.json', idx)
        store = {}
        BR.note_launch(store.__setitem__, run_id='4pKpMKnh7Lt77Q', user=USER, thread_id=T_J,
                       subject="Georgie & Mandy's First Marriage Viewers - Paramount+", now=time.time() - 60)
        with APP.test_request_context('/'):
            out = BR.sweep(store_list=lambda p: [k for k in store if k.startswith(p)], store_get=store.get, store_put=store.__setitem__,
                           runs_for=lambda u: [FX['jessie_refusal_status']], exists=S.thread_exists, load=S.load_thread, save=S.save_thread,
                           relist=S.relist_thread, log=None)
        self.assertEqual(out, [('4pKpMKnh7Lt77Q', 'closed_failed_settled')])
        saved = w.get(f'{THREADS}/{USER}/{T_J}.json')
        self.assertTrue(saved[1]['text'].startswith(REFUSAL))
        self.assertEqual(PT.thread_tail(saved)['ends_on'], 'answer')
        self.assertEqual(store[BR._key('4pKpMKnh7Lt77Q')]['status'], 'closed')
        # a cancel reads as a cancel
        t2 = [dict(t) for t in FX['jessie_refused'][:2]]
        out2, how = PT.settle_history(t2, '4pKpMKnh7Lt77Q', {'status': 'cancelled', 'cancel_note': 'Stopped at your request. The charge for this run was returned.'})
        self.assertEqual((how, out2[1]['text']), ('settled', 'Stopped at your request. The charge for this run was returned.'))
        # a thread that never stored the build's line still gets the outcome, once
        out3, how3 = PT.settle_history([FX['jessie_refused'][0]], '4pKpMKnh7Lt77Q', FX['jessie_refusal_status'])
        self.assertEqual((how3, out3[-1]['turn_id']), ('appended', 'outcome-4pKpMKnh7Lt77Q'))

    def test_the_poll_and_the_tab_never_draw_the_calm_line_for_a_build_that_ended(self):
        jobs = Path(ROOT, 'prometheus', 'legacy', 'jobs.py').read_text(encoding='utf-8')
        self.assertIn('settle_from_status', jobs)
        self.assertIn("'outcome': {'text': _line, 'settled': _how}", jobs)
        html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')
        self.assertNotIn("if (cur === 'error' || cur === 'failed') {\n                            msg = SYNTH_CHAT_CALM_MSG;", html)
        self.assertNotIn("lines = [SYNTH_CHAT_CALM_MSG];", html)
        self.assertIn('function _pmBuildOutcomeText(data, st)', html)
        self.assertIn("if (turn && turn.meta && turn.meta.withdrawn) return '';", html)
        ready = Path(ROOT, 'prometheus', 'build_ready.py').read_text(encoding='utf-8')
        self.assertIn('_pt.settle_launch(doc, run', ready)

    def test_the_calm_line_goes_out_with_a_record_behind_it(self):
        """Armine, 21:14 UTC: "all of these should be on YouTube" drew the calm line with no job, no run and no
        held record. The one exit every reply passes puts the held record behind it."""
        opened = []
        orig_hold = PT.hold

        def _hold(uname, question, **kw):
            kw['writer'] = lambda key, doc: opened.append((key, doc))
            return orig_hold(uname, question, **kw)
        PT.hold = _hold
        try:
            with APP.test_request_context('/'):
                flask.g._pm_ask_text = AW['all_on_youtube']
                flask.g._pm_uname = 'ameseldzhyan'
                env = ENV.wrap({'success': True, 'action': 'answer', 'reply': CALM, 'followups': [], 'held': True},
                               surface='analyze', decision={}, thread_id='2ca57cf8d5')
            self.assertEqual(env['text'], CALM)
            self.assertEqual(len(opened), 1)
            key, doc = opened[0]
            self.assertTrue(key.startswith('system/ops/held_replies/'))
            self.assertEqual((doc['user'], doc['question'], doc['status'], doc['thread_id']),
                             ('ameseldzhyan', AW['all_on_youtube'], 'open', '2ca57cf8d5'))
            self.assertTrue(doc['due_by'] > doc['opened_at'])
            self.assertEqual(env['raw']['held_ref'], key)
            # a calm line that already has a job, a run or a record gets no second one
            with APP.test_request_context('/'):
                ENV.wrap({'success': True, 'reply': CALM, 'read_job_id': 'abcdefabcdef'}, surface='analyze', decision={})
                ENV.wrap({'success': True, 'reply': CALM, 'held_ref': 'system/ops/held_replies/x.json'}, surface='analyze', decision={})
                ENV.wrap({'success': True, 'action': 'answer', 'reply': 'Comedy leads at 34.6%.'}, surface='analyze', decision={})
            self.assertEqual(len(opened), 1)
        finally:
            PT.hold = orig_hold

    def test_a_calm_line_a_tab_wrote_on_its_own_gets_its_record_once(self):
        thread = FX['armine_calm_thread']
        self.assertEqual(thread[-1]['text'], CALM)
        self.assertEqual(thread[-2]['text'], AW['all_on_youtube'])
        opened = []
        w = lambda key, doc: opened.append(doc)   # noqa: E731
        incoming = [dict(t) for t in thread]
        none = lambda u, q: ''   # noqa: E731
        PT.back_thread('ameseldzhyan', '2ca57cf8d5', incoming, stored=thread[:-1], writer=w, finder=none)
        self.assertEqual(len(opened), 1)
        self.assertEqual((opened[0]['question'], opened[0]['kind']), (AW['all_on_youtube'], 'client'))
        self.assertTrue(incoming[-1]['meta']['held_ref'])
        # the same turn saved again (another tab, the next save) opens nothing; a thread with no calm line reads nothing
        PT.back_thread('ameseldzhyan', '2ca57cf8d5', [dict(t) for t in thread], stored=incoming, writer=w, finder=none)
        PT.back_thread('ameseldzhyan', '2ca57cf8d5', incoming, stored=[], writer=w, finder=none)
        loads = []
        PT.back_thread('ameseldzhyan', '2ca57cf8d5', [dict(t) for t in thread[:-1]], load=lambda: loads.append(1) or [], writer=w, finder=none)
        self.assertEqual((len(opened), loads), (1, []))
        # the record the reply's own exit opened for this ask is the one the tab's line takes: one promise, one record
        again = [dict(t) for t in thread]
        PT.back_thread('ameseldzhyan', '2ca57cf8d5', again, stored=thread[:-1], writer=w, finder=lambda u, q: 'system/ops/held_replies/2026-11-11/211404_abc.json')
        self.assertEqual((len(opened), again[-1]['meta']['held_ref']), (1, 'system/ops/held_replies/2026-11-11/211404_abc.json'))
        docs = {'system/ops/held_replies/2026-11-11/211404_abc.json': {'user': 'ameseldzhyan', 'question': AW['all_on_youtube'], 'status': 'open',
                                                                   'opened_at': '2026-11-11T21:14:04Z'}}
        at = 1794431700.0                                                                 # 2026-11-11T21:15:00Z
        find = lambda q, u='ameseldzhyan', now=at: PT.find_held(u, q, now=now, lister=lambda p: [k for k in docs if k.startswith(p)], getter=docs.get)   # noqa: E731
        self.assertEqual(find(AW['all_on_youtube']), 'system/ops/held_replies/2026-11-11/211404_abc.json')
        self.assertEqual(find('add drama category'), '')
        self.assertEqual(find(AW['all_on_youtube'], u='jessie'), '')
        self.assertEqual(find(AW['all_on_youtube'], now=at + 7200), '')
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertIn("fromlist=['back_thread']).back_thread(", chat)

    def test_a_job_that_died_does_not_leave_its_progress_line_standing(self):
        opened, landed = [], []
        ref = PT.job_failed('read', 'ameseldzhyan', 'bf5f3f10ab59', 'add drama category', land=landed.append,
                            writer=lambda key, doc: opened.append((key, doc)))
        self.assertEqual(len(opened), 1)
        self.assertEqual((opened[0][1]['job_id'], opened[0][1]['kind']), ('bf5f3f10ab59', 'read_failed'))
        self.assertEqual((landed[0]['reply'], landed[0]['held_ref']), (CALM, ref))
        from prometheus import read_landing as RL
        self.assertEqual(RL.read_meta('bf5f3f10ab59', landed[0])['held_ref'], ref)       # the record rides on the turn
        # a job whose answer had already landed owes nothing: no record
        self.assertEqual(PT.job_failed('read', 'ameseldzhyan', 'bf5f3f10ab59', 'q', land=lambda p: 'already',
                                       writer=lambda key, doc: opened.append((key, doc))), '')
        self.assertEqual(len(opened), 1)
        # a deck: the deck's own progress line is withdrawn and the held note lands under it
        w = World()
        tid = w.new_thread()
        w.put(f'{THREADS}/{USER}/{tid}.json', [
            {'role': 'user', 'text': 'Build a deck from this data', 'ts': '2026-11-11T22:26:30Z', 'turn_id': 'u1'},
            {'role': 'agent', 'text': 'Working on it. The deck will land in this thread when it is ready.', 'ts': '2026-11-11T22:26:35Z', 'turn_id': 'a1'}])
        with APP.test_request_context('/'):
            self.assertTrue(PT.land_note(tid, USER, 'dbe671212bcc', {'reply': CALM, 'held_ref': 'system/ops/held_replies/2026-11-11/x.json'}))
            self.assertFalse(PT.land_note(tid, USER, 'dbe671212bcc', {'reply': CALM, 'held_ref': 'system/ops/held_replies/2026-11-11/x.json'}))
            self.assertFalse(PT.land_note(tid, USER, 'dbe671212bcc', {'reply': CALM}))    # no record, no line
        saved = w.get(f'{THREADS}/{USER}/{tid}.json')
        self.assertEqual(len(saved), 3)
        self.assertTrue(saved[1]['meta']['withdrawn'])
        self.assertEqual((saved[2]['text'], saved[2]['meta']['held_ref']), (CALM, 'system/ops/held_replies/2026-11-11/x.json'))
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertEqual(chat.count("_PT.job_failed('read', pm_user, job_id, text"), 2)
        self.assertEqual(chat.count("_PT.job_failed('deck', username, job_id"), 1)

    def test_how_a_thread_ends_and_what_is_behind_it(self):
        refused = FX['jessie_refused']
        tail = PT.thread_tail(refused)
        self.assertEqual(PT.classify_tail(tail)[0], 'unbacked')                          # the calm line, nothing behind it
        self.assertEqual(PT.classify_tail(tail, held={'id': 'x'})[0], 'backed')
        build = PT.thread_tail(refused[:3])                                              # "... is building."
        self.assertEqual((build['kind'], build['refs'].get('run_id')), ('build', '4pKpMKnh7Lt77Q'))
        self.assertEqual(PT.classify_tail(build, run_status='running', age_s=600)[0], 'backed')
        self.assertEqual(PT.classify_tail(build, run_status='failed')[0], 'unbacked')
        self.assertEqual(PT.classify_tail(build, run_status='complete')[0], 'unbacked')
        self.assertEqual(PT.classify_tail(build, run_status='running', age_s=30 * 3600)[0], 'unbacked')
        ack = PT.thread_tail([{'role': 'user', 'text': 'add drama category'},
                              {'role': 'agent', 'text': 'On it: reading YouTube now. The read will land right here when it is ready.',
                               'meta': {'read_job_id': '58cce4136c64'}}])
        self.assertEqual(PT.classify_tail(ack, job_status='working')[0], 'backed')
        self.assertEqual(PT.classify_tail(ack, job_status='held')[0], 'backed')
        self.assertEqual(PT.classify_tail(ack, job_status='error')[0], 'unbacked')
        self.assertEqual(PT.classify_tail(ack, job_status='error', held={'id': 'x'})[0], 'backed')
        landed = PT.thread_tail([{'role': 'user', 'text': 'q'}, {'role': 'agent', 'text': 'On YouTube: Drama is 2,561,438 US viewers.',
                                                                  'meta': {'read_job_id': 'x', 'landed_at': '2026-11-11T21:12:39Z'}}])
        self.assertEqual((landed['ends_on'], PT.classify_tail(landed)[0]), ('answer', 'not_a_promise'))
        self.assertEqual(PT.thread_tail([])['ends_on'], 'empty')
        # the sweep reads and never writes
        src = Path(HERE, 'pm_unbacked_promise_sweep.py').read_text(encoding='utf-8')
        for banned in ('put_object', 'delete_object', 'save_thread', 'send_email', 'send_raw_email', 'copy_object'):
            self.assertNotIn(banned, src)


# ============================================================================ 3
class ADeliverableRidesThrough(unittest.TestCase):
    def test_armines_words_are_a_question_and_a_powerpoint(self):
        for key in ('summarize_in_powerpoint', 'can_we_summarize_tail', 'can_we_summarize_head'):
            plan = RA.split(AW[key])
            self.assertIsNotNone(plan, key)
            self.assertEqual(plan['kinds'], ['deck'])
            self.assertNotRegex(plan['question'], r"(?i)power\s?point|summari[sz]e")
            self.assertIn('What type of categories do best for digital original content', plan['question'])
            self.assertIn('how is the audience like?', plan['question'])
        self.assertEqual(RA.split(AW['summarize_in_powerpoint'])['words'], 'Summarize in powerpoint')
        # the deliverable ask on its own is the deck surface's, and the deck surface now knows it
        for alone in ('Summarize in powerpoint', 'Can we summarize this in a powerpoint?', 'make this a deck', 'Build a deck from this data'):
            self.assertIsNone(RA.split(alone), alone)
            self.assertTrue(U.looks_like_deck_ask(alone), alone)
        # not a deliverable ask at all
        for no in ('Which decks did you send me last week?', 'What share of viewers use PowerPoint at work?',
                   'How big is the Reba audience? Send it as a CSV.', 'Run a profile on Nike buyers and put it in a deck',
                   'Compare Reba and Dolly fans and tell me which skews older'):
            self.assertIsNone(RA.split(no), no)
        self.assertEqual(RA.split('What are the top five QSRs for Gilmore Girls viewers and email it to me')['kinds'], ['email'])
        self.assertEqual(RA.split('what do Peacock viewers watch, as a pdf please')['question'], 'what do Peacock viewers watch')

    def _ask(self, w, text, *, tid, history=None, page=None, surface='analyze', client='dashboard'):
        w.calls.clear()
        body = {'text': text, 'client': client, 'thread_id': tid, 'surface': surface, '_client_surface_sent': surface}
        if history is not None:
            body['history'] = list(history)
        if page is not None:
            body['context'] = page
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            env, st = RA.run(S.ask, dict(NB.ARMINE), body)
        return env, st

    def test_the_question_is_answered_and_the_deck_is_on_record_for_the_read(self):
        """Her 21:07 message: the read lane never sees the PowerPoint words; the reply says the deck is built from
        the read when it lands, and the record that makes that true is in the store."""
        w = World()
        tid = w.new_thread()
        env, _st = self._ask(w, AW['summarize_in_powerpoint'], tid=tid, history=[])
        self.assertTrue(w.calls)
        for surface, text, _body, _seen in w.calls:
            self.assertNotEqual(surface, 'deck')
            self.assertNotRegex(text, r"(?i)power\s?point")                # nothing for a read to make a claim about
        self.assertTrue(env['text'].rstrip().endswith(RA.DECK_NEXT_LINE))
        self.assertEqual(env['ride_along']['state'], 'pending')
        rec = w.get(RA._key(USER, tid))
        self.assertEqual((rec['state'], rec['kinds'], rec['thread_id']), ('pending', ['deck'], tid))
        self.assertTrue(rec['job_ids'])                                    # the read jobs the deck waits on
        self.assertEqual(env['ride_along']['record'], RA._key(USER, tid))
        self.assertEqual(env['raw']['reply'], env['text'])                 # the tab renders the same words
        # the reply's promise is on record, so the truth exit has nothing to take out and the sweep reads it as backed
        self.assertEqual(PT.classify_tail({'ends_on': 'promise', 'kind': 'read', 'refs': {}}, ride_along=rec)[0], 'backed')

    def test_when_the_read_lands_the_deck_starts_through_the_deck_path(self):
        w = World()
        tid = w.new_thread()
        env, _st = self._ask(w, AW['summarize_in_powerpoint'], tid=tid, history=[])
        rec = w.get(RA._key(USER, tid))
        jobs = list(rec['job_ids'])
        # the thread as the server holds it once the reads have landed
        turns = [{'role': 'user', 'text': AW['summarize_in_powerpoint'], 'ts': '2026-11-11T21:07:10Z', 'turn_id': 'u1'}]
        for n, j in enumerate(jobs):
            turns.append({'role': 'agent', 'text': 'On Digital original content categories on YouTube:\n\nComedy leads at 4,713,882 US viewers.',
                          'ts': f'2026-11-11T21:09:0{n}Z', 'meta': {'read_job_id': j, 'kind': 'read', 'landed_at': f'2026-11-11T21:09:0{n}Z',
                                                                     'subject': 'Digital original content categories on YouTube'}})
            w.put(f'system/prometheus_reads/{j}.json', {'job_id': j, 'user': USER, 'thread_id': tid, 'status': 'done'})
        w.put(f'{THREADS}/{USER}/{tid}.json', turns)
        seen = []

        def _deck_core(user, body):
            seen.append(dict(body))
            return {'success': True, 'job_id': 'd1e2c3k4j5b6', 'subject': 'Digital original content categories on YouTube',
                    'confirm_line': 'Building an insights deck from the Digital original content categories on YouTube read you already have. '
                                    'The download link lands here when it is ready.'}
        orig = S.job_thread_id
        S.job_thread_id = lambda j: tid
        try:
            with APP.test_request_context('/'):
                kw = dict(deck_core=_deck_core, request_context=lambda: APP.test_request_context('/'), user=dict(NB.ARMINE))
                # a held read starts nothing; every read but the last leaves it waiting
                self.assertEqual(RA.on_read_landed(USER, jobs[0], {}, held=True, **kw), 'held')
                for j in jobs[:-1]:
                    self.assertEqual(RA.on_read_landed(USER, j, {}, **kw), 'waiting')
                self.assertEqual(seen, [])
                self.assertEqual(RA.on_read_landed(USER, jobs[-1], {}, **kw), 'started')
                self.assertEqual(RA.on_read_landed(USER, jobs[-1], {}, **kw), 'none')          # once
                self.assertEqual(RA.on_read_landed(USER, jobs[-1], {}, probe=True, **kw), 'none')
        finally:
            S.job_thread_id = orig
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0]['bind_subject'], seen[0]['confirm_open_screen'], seen[0]['thread_id']),
                         ('Digital original content categories on YouTube', True, tid))
        self.assertTrue(any((t.get('meta') or {}).get('read_job_id') for t in seen[0]['history']))   # the deck is from the read
        saved = w.get(f'{THREADS}/{USER}/{tid}.json')
        self.assertTrue(saved[-1]['text'].startswith('Building an insights deck from the Digital original content categories on YouTube read'))
        self.assertEqual(saved[-1]['meta']['ride_along']['deck_job'], 'd1e2c3k4j5b6')            # the line carries its job
        self.assertEqual(w.get(RA._key(USER, tid))['state'], 'started')
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertIn("fromlist=['on_read_landed']).on_read_landed(pm_user, job_id, payload, held=held, probe=probe)", chat)
        bp = Path(ROOT, 'prometheus', 'blueprint.py').read_text(encoding='utf-8')
        self.assertIn('_ride.run(service.ask, user, body, via=via)', bp)

    def test_an_answer_in_hand_takes_the_deck_path_now_with_its_own_question(self):
        w = World()
        tid = w.new_thread()

        def _core(user, body, text, history):
            w.calls.append(('analyze', text, dict(body), {}))
            return {'success': True, 'action': 'answer', 'reply': 'On Abbott Elementary viewers on Hulu: 58.1% are women.', 'followups': [],
                    'subject': 'Abbott Elementary viewers on Hulu'}
        host.bind(analyze_core=_core)
        env, _st = self._ask(w, 'Who watches Abbott Elementary on Hulu? Put it in a deck please.', tid=tid, history=[])
        decks = [c for c in w.calls if c[0] == 'deck']
        self.assertEqual(len(decks), 1)                                    # the deck path ran, once, after the answer
        self.assertEqual(decks[0][2]['bind_subject'], 'Abbott Elementary viewers on Hulu')
        self.assertIn('58.1% are women', env['text'])
        self.assertIn('Which profile should the deck cover?', env['text'])  # the deck path's own question (the scaffold's)
        self.assertEqual(env['ride_along']['state'], 'asked')

    def test_email_it_to_me_on_a_running_read_is_the_readers_opt_in_for_that_job(self):
        w = World()
        tid = w.new_thread()
        wrote = []
        orig = RA._notify_write
        RA._notify_write = lambda job, email, uname: wrote.append((job, email, uname))
        try:
            env, _st = self._ask(w, 'What are the top five QSRs for Gilmore Girls viewers and email it to me', tid=tid, history=[])
        finally:
            RA._notify_write = orig
        self.assertTrue(env['text'].rstrip().endswith(RA.EMAIL_NEXT_LINE))
        self.assertEqual(len(wrote), 1)
        self.assertEqual(wrote[0][1:], ('armine@example.com', USER))
        self.assertTrue(all('email' not in c[1].lower() for c in w.calls))

    def test_a_probe_starts_nothing_and_a_plain_ask_is_untouched(self):
        w = World()
        tid = w.new_thread()
        body = {'text': AW['summarize_in_powerpoint'], 'client': 'probe', 'thread_id': tid}
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            env, _st = RA.run(S.ask, dict(NB.ARMINE), body, via='canary')
        self.assertIsNone(w.get(RA._key(USER, tid)))
        self.assertTrue((env.get('ride_along') or {}).get('probe'))
        called = []
        out = RA.run(lambda u, b, via='session': (called.append(b) or ({'text': 'x'}, 200)), {}, {'text': 'How big is the Reba audience?'})
        self.assertEqual((out, called[0]), (({'text': 'x'}, 200), {'text': 'How big is the Reba audience?'}))


# ============================================================================ 4
class AnOrderForAProfileIsBuilt(unittest.TestCase):
    def test_her_three_messages_are_orders_the_gate_did_not_know(self):
        for key in ('one_profile', 'its_wrong', 'not_a_read', 'use_all_49'):
            self.assertFalse(ORD.is_profile_order(BL.strip_pasted(JW[key])[0]), key)
        self.assertEqual(BL.kind(JW['one_profile'], _before(JW['one_profile'])), 'noun_order')
        self.assertEqual(BL.kind(JW['its_wrong'], _before(JW['its_wrong'])), 'instruction')
        self.assertEqual(BL.kind(JW['not_a_read'], _before(JW['not_a_read'])), 'correction')
        self.assertEqual(BL.kind(JW['use_all_49'], _before(JW['use_all_49'])), 'instruction')
        self.assertEqual(BL.kind(JW['full_profile_order'], _before(JW['full_profile_order'])), '')   # the gate already has it

    def test_each_becomes_the_order_she_gave(self):
        r = BL.rewrite(JW['one_profile'], _before(JW['one_profile']))
        self.assertEqual(r['text'], "Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers ONLY on Paramount+. "
                                    "Using the 49 Paramount Plus Placements you see in the content_map")
        r = BL.rewrite(JW['not_a_read'], _before(JW['not_a_read']))
        self.assertEqual(r['text'], "Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers ONLY on Paramount+, "
                                    "Oct 25 2024 to Nov 10 2026")
        self.assertEqual((r['kind'], r['source']), ('correction', 'thread_order'))
        r = BL.rewrite(JW['its_wrong'], _before(JW['its_wrong']))
        self.assertTrue(r['text'].startswith("Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers ONLY on Paramount+, "
                                              "Oct 25 2024 to Nov 10 2026."))
        self.assertIn('needs to be run on all 49 Paramount Plus placements', r['text'])
        self.assertNotIn('ALVE01M297TRFZEG38DWMJ4JJXSX34', r['text'])                    # a pasted id is not a subject
        r = BL.rewrite(JW['use_all_49'], _before(JW['use_all_49']))
        self.assertTrue(r['text'].startswith("run a NEW profile on Viewers of GEORGIE & MANDY'S FIRST MARRIAGE, viewers ONLY on Paramount Plus."))
        self.assertIn('use all 49 placements you saw in the content_map', r['text'])
        for out in (BL.rewrite(JW[k], _before(JW[k]))['text'] for k in ('one_profile', 'its_wrong', 'not_a_read', 'use_all_49')):
            self.assertTrue(ORD.is_profile_order(out), out)                               # each is an order the build flow reads
        self.assertEqual(BL.strip_pasted(JW['its_wrong'])[1], 49)

    def test_the_other_ways_to_say_it(self):
        read = [{'role': 'user', 'text': 'who are they'},
                {'role': 'agent', 'text': "On Georgie & Mandy's First Marriage Viewers - Paramount+:\n\n1,285,455 projected US viewers.",
                 'meta': {'kind': 'read', 'read_job_id': 'eee1fe06f65b', 'subject': "Georgie & Mandy's First Marriage Viewers - Paramount+",
                          'landed_at': '2026-11-11T23:05:22Z'}}]
        for words in ('a full profile', 'I want the profile', 'build it', 'No. Not a read.', 'i asked for a profile not a read',
                      'not a read, the full profile please'):
            r = BL.rewrite(words, read)
            self.assertEqual(r['text'], "Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers - Paramount+", words)
        for words in ('what does the profile say about age', 'can we build profile', 'a full profile?', 'not a read of the room',
                      "don't build a new profile, answer from the page", 'show me the full profile breakdown by age', 'I want the deck',
                      'summarize the profile', 'is this a read', 'was the profile run on all 49 of these inputs?'):
            self.assertEqual(BL.kind(words, read), '', words)
        # "build it" after an offer of a deck is that offer's
        deck = read + [{'role': 'user', 'text': 'nice'}, {'role': 'agent', 'text': 'On X: 1,2,3. Want this as a deck?', 'meta': {'kind': 'read'}}]
        self.assertEqual(BL.kind('build it', deck), '')
        # nothing names a subject: one plain line, never a read
        self.assertEqual(BL.rewrite('NO! not a read. a full profile', []), {'ask': BL.ASK_LINE, 'kind': 'correction'})

    def test_not_a_read_reaches_the_build_step_and_no_flag_rides_it(self):
        """23:05 UTC, the profile open on her screen, the tab on the analyze surface: the reply was a read."""
        w = World()
        hist = _before(JW['not_a_read'])
        w.put(f'{THREADS}/{USER}/{T_J}.json', hist)
        idx = w.index()
        idx['threads'].append({'id': T_J, 'title': 'run a NEW profile', 'created': NB._iso(-600), 'updated': NB._iso(-5), 'turns': len(hist)})
        w.put(f'{THREADS}/{USER}/index.json', idx)
        env, _st, _g = w.ask(JW['not_a_read'], thread_id=T_J, history=hist, page=PAGE, surface='analyze')
        self.assertEqual([c[0] for c in w.calls], [])                                     # no read was started
        self.assertEqual(env['decision']['reason'], 'handoff_to_build_flow')
        self.assertEqual(env['raw'].get('route_hint'), 'interpret')
        # the tab re-sends her words on the build surface; the build step reads the order
        env, _st, _g = w.ask(JW['not_a_read'], thread_id=T_J, history=hist, page=PAGE, surface='interpret')
        self.assertEqual([c[0] for c in w.calls], ['interpret'])
        text, body = w.calls[0][1], w.calls[0][2]
        self.assertEqual(text, "Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers ONLY on Paramount+, Oct 25 2024 to Nov 10 2026")
        self.assertEqual(env['kind'], 'draft')                                            # the interpret step's brief, to approve
        for flag in ('force_new', 'mode', 'force_refresh', 'reuse_only', 'bypass_cache', 'decision', 'override', 'enable_hostmap_augment'):
            self.assertFalse(body.get(flag), flag)                                        # the interpret decision is the authority
        self.assertIn({'route': 'build_order', 'outcome': 'lane_correction'}, w.hints)

    def test_one_profile_of_x_and_the_run_instruction_reach_it_too(self):
        for key in ('one_profile', 'its_wrong'):
            w = World()
            hist = _before(JW[key])
            w.put(f'{THREADS}/{USER}/{T_J}.json', hist)
            idx = w.index()
            idx['threads'].append({'id': T_J, 'title': 't', 'created': NB._iso(-600), 'updated': NB._iso(-5), 'turns': len(hist)})
            w.put(f'{THREADS}/{USER}/index.json', idx)
            env, _st, _g = w.ask(JW[key], thread_id=T_J, history=hist, page=PAGE, surface='interpret')
            self.assertEqual([c[0] for c in w.calls], ['interpret'], key)
            self.assertTrue(w.calls[0][1].startswith("Please queue one Profile IQ: Georgie & Mandy's First Marriage Viewers ONLY on Paramount+"), key)

    def test_a_question_about_the_page_is_still_a_question(self):
        w = World()
        env, _st, _g = w.ask('what share of this audience is female', thread_id=w.new_thread(), history=[], page=PAGE, surface='analyze')
        self.assertEqual([c[0] for c in w.calls], ['analyze'])


# ============================================================================ 5
class ABuildIsReadFromTheFile(unittest.TestCase):
    def setUp(self):
        self.w = World()
        rs = _RangeS3()
        rs.objs = self.w.s3.objs                # the same store, read the way a profile file is read
        self.w.s3 = rs
        host.bind(s3_client=self.w.s3, user_runs=lambda user, limit=40, active=False, status=None: self.runs)
        self.runs = []
        self.key = PAGE['primary']['s3_key']

    def test_her_question_is_about_the_build(self):
        self.assertTrue(BF.wants(JW['was_it_run']))
        for yes in ('how many placements are in the build?', 'what was the profile built on?', 'did you use all 49 placements',
                    'which inputs did the build use?'):
            self.assertTrue(BF.wants(yes), yes)
        for no in ('what brands does this audience buy', 'is it done', 'which platforms does this audience use most',
                   'did the audience watch more episodes on Hulu', JW['use_all_49'], JW['its_wrong'], JW['not_a_read']):
            self.assertFalse(BF.wants(no), no[:40])

    def test_the_answer_is_what_the_file_lists(self):
        """23:31 UTC: "was the profile run on all 49 of these inputs or did you just add them to the BRAND field?"
        was answered "All of them were run" with the file unopened."""
        self.w.s3.objs[self.key] = _csv()
        hist = _before(JW['not_a_read']) + [{'role': 'user', 'text': JW['not_a_read']}]     # her pasted list is up the thread
        raw = BF.answer(JW['was_it_run'], ctx=PAGE, history=hist, user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertIn('I opened the file for', raw['reply'])
        self.assertIn('Inputs the file lists: 49', raw['reply'])
        self.assertIn('all 49 of the ones you sent are in the file', raw['reply'])
        self.assertIn('1,285,455 projected US people (38,965 people in the data), Oct 25 2024 to Nov 10 2026', raw['reply'])
        self.assertIn('File last written: Nov 11, 2026 at 3:48 PM PT', raw['reply'])
        self.assertEqual((raw['build_facts']['state'], raw['build_facts']['n_inputs'], raw['build_facts']['listed']), ('read', 49, 49))
        self.assertNotIn('All of them were run', raw['reply'])
        for word in ('BRAND INPUT', 'synth', 'pipeline', 'S3', 'csv'):
            self.assertNotIn(word, raw['reply'])

    def test_the_file_as_it_was_that_afternoon_listed_five(self):
        self.w.s3.objs[self.key] = _csv(5)
        hist = _before(JW['not_a_read'])
        raw = BF.answer(JW['was_it_run'], ctx=PAGE, history=hist, user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertIn('Inputs the file lists: 5', raw['reply'])
        self.assertIn('5 of the 49 you sent are in the file; 44 are not', raw['reply'])
        self.assertEqual(raw['build_facts']['line'], 'The file lists 5 inputs; 5 of the 49 you sent are in it and 44 are not.')

    def test_a_build_with_no_file_yet_is_still_building(self):
        hist = [{'role': 'user', 'text': 'run a NEW profile on Viewers of X, viewers ONLY on Paramount Plus'},
                {'role': 'agent', 'text': 'Profile building: X Viewers - Paramount+. I will email you when it is complete.',
                 'meta': {'run_id': 'ykCjqwHzA4E3jQ', 'subject': 'X Viewers - Paramount+', 'status': 'running'}}]
        self.runs = [{'run_id': 'ykCjqwHzA4E3jQ', 'status': 'running', 'subject': 'X Viewers - Paramount+'}]
        raw = BF.answer('did you use all 49 placements?', ctx=None, history=hist, user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertIn('X Viewers - Paramount+ is still building', raw['reply'])
        self.assertIn('I will not say what it carries until I can open it', raw['reply'])
        self.assertEqual(raw['build_facts']['state'], 'building')
        # finished: the run record names the file, and the file is read
        self.runs = [{'run_id': 'ykCjqwHzA4E3jQ', 'status': 'complete', 'subject': 'X Viewers - Paramount+', 'tu_key': 'X_Viewers.csv'}]
        self.w.s3.objs['X_Viewers.csv'] = _csv()
        raw = BF.answer('did you use all 49 placements?', ctx=None, history=hist, user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertIn('Inputs the file lists: 49', raw['reply'])
        # neither a file nor a run: said plainly, nothing claimed
        self.runs = []
        raw = BF.answer('did you use all 49 placements?', ctx=None, history=[], user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertIn('I will not say what it carries', raw['reply'])

    def test_a_written_answer_cannot_describe_the_build(self):
        """The four replies she got, each written with the file unopened."""
        for key, gone, kept in (('r2207', 'placements carried into the build', '1,051,523'),
                                ('r2214', 'placements carried into the build', '99.4%'),
                                ('r2304', 'The build already carries all 49', '1,285,455'),
                                ('r2305', 'The full profile is built across all 49', '223,669'),
                                ('r2331', 'All of them were run', '1,285,455')):
            out, removed = RT.scrub(JR[key], set())
            self.assertNotIn(gone, out, key)
            self.assertIn(kept, out, key)
            self.assertIn('build_contents', {k for k, _s in removed}, key)
        # with the file read, its own line stands where the model's was
        out, _removed = RT.scrub(JR['r2331'], set(), {'line': 'The file lists 5 inputs; 5 of the 49 you sent are in it and 44 are not.'})
        self.assertTrue(out.startswith('The file lists 5 inputs; 5 of the 49 you sent are in it and 44 are not.'))
        # the lane's own reply is read from the file and is not second-guessed
        self.w.s3.objs[self.key] = _csv()
        raw = BF.answer(JW['was_it_run'], ctx=PAGE, history=[], user=dict(NB.ARMINE), uname=USER, host=host)
        self.assertEqual(RT.finish_answer(dict(raw))['reply'], raw['reply'])

    def test_the_lane_answers_before_any_core(self):
        self.w.s3.objs[self.key] = _csv()
        host.bind(s3_client=self.w.s3)
        env, _st, _g = self.w.ask(JW['was_it_run'], thread_id=self.w.new_thread(), history=[], page=PAGE, surface='analyze')
        self.assertEqual(self.w.calls, [])
        self.assertEqual(env['decision']['reason'], 'build_facts')
        self.assertIn('Inputs the file lists: 49', env['text'])
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        self.assertLess(S.LANE_ORDER.index('build_fix'), S.LANE_ORDER.index('build_facts'))
        self.assertIn('build_facts', S.DETERMINISTIC_LANES)
        self.assertIn('build_facts', S.NEVER_PRICED_LANES)


# ============================================================================ 6
class TheDoorRuleIsADeckRule(unittest.TestCase):
    def test_what_reached_armine(self):
        """The stored reply: "Architectural Digest brings, Tiny Desk", "AD brings", "the broader into the originals
        slate". The read as written said Open Door and doorway."""
        self.assertIn('Architectural Digest brings', AR['open_door_mangled'])
        self.assertIn('AD brings', AR['open_door_mangled'])
        self.assertIn('the broader into the originals slate', AR['open_door_mangled'])
        self.assertIn('AD Open Door', AR['open_door_read'])

    def test_a_chat_reply_is_never_scrubbed_for_it(self):
        import prometheus_analysis as pma
        read = AR['open_door_read']
        self.assertEqual(pma.scrub_user_text(read).count('AD Open Door'), 2)
        env = ENV.wrap({'success': True, 'action': 'answer', 'reply': read, 'followups': ['Show AD Open Door viewers by age']},
                       surface='analyze', decision={})
        self.assertEqual(env['text'].count('AD Open Door'), 2)
        self.assertEqual(env['options'][0]['label'], 'Show AD Open Door viewers by age')
        for line in ('Hot Ones, Architectural Digest Open Door, Tiny Desk, Vogue 73 Questions, Binging with Babish, Yes Theory; engagement 49.8',
                     'so comedy reads as the deeper per-viewer habit and lifestyle as the broader doorway into the originals slate.',
                     FX['liz_front_door'], 'The Doors and Three Doors Down fans skew 45 to 64.'):
            env = ENV.wrap({'success': True, 'action': 'answer', 'reply': line}, surface='analyze', decision={})
            self.assertEqual(env['text'], line)
            self.assertEqual(pma.scrub_user_text(line), line)
        raw = {'reply': 'Retail door mix', 'filename': 'Retail_door_mix.csv', 'file_link': {'name': 'Retail door mix.csv', 'label': 'Download Retail door mix.csv'}}
        self.assertEqual(DW.scrub_payload(dict(raw)), raw)                                 # a data file keeps its name

    def test_deck_copy_keeps_names_and_still_loses_the_metaphor(self):
        from migration import design_skill as ds
        for name in ('Hot Ones, NPR Tiny Desk, AD Open Door, Vogue 73 Questions, Babish lead',
                     'Architectural Digest Open Door leads the block', 'The Doors, Three Doors Down and Door County',
                     'Sliding Doors, Stage Door, Behind Closed Doors', 'The Girl Next Door fans', 'DoorDash and Glassdoor',
                     'They played at The Door NYC last night'):
            self.assertEqual(ds.scrub_door_metaphor(name), name, name)
            self.assertEqual(DW.scrub_read(name), name, name)
            self.assertFalse(DW.has_door(name), name)
        self.assertEqual(ds.scrub_door_metaphor('This opens the door to new viewers.'), 'This brings new viewers.')
        self.assertEqual(ds.scrub_door_metaphor('01 The Door to New Viewers'), '01 New Viewers')
        self.assertEqual(ds.scrub_door_metaphor('01 The door to new viewers'), '01 New viewers')
        self.assertEqual(ds.scrub_door_metaphor('AD Open Door opens the door to new viewers.'), 'AD Open Door brings new viewers.')
        self.assertEqual(ds.scrub_door_metaphor('Retail Door Mix'), 'Retail Mix')
        self.assertEqual(ds._fallback_scrub_door('Hot Ones, AD Open Door; The Doors; opens the door to new viewers; 01 The Door to New Viewers'),
                         'Hot Ones, AD Open Door; The Doors; brings new viewers; 01 New Viewers')
        mod = ds.door_copy_module()
        if mod is not None:
            self.assertEqual(mod.door_hits('Hot Ones, AD Open Door, Tiny Desk'), [])       # the lint does not flag a name
            self.assertTrue(mod.door_hits('the door to new viewers'))
        # a reply that IS deck copy is still scrubbed, names held
        deck = {'reply': 'SLIDE 1\nAD Open Door opens the door to new viewers.'}
        self.assertEqual(DW.scrub_payload(deck)['reply'], 'SLIDE 1\nAD Open Door brings new viewers.')
        self.assertEqual(DW.scrub_filename('AD_Open_Door_Insights.pptx'), 'AD_Open_Door_Insights.pptx')
        self.assertEqual(DW.scrub_filename('Retail_door_mix_Insights.pptx'), 'Retail_store_mix_Insights.pptx')

    def test_the_wiring(self):
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertEqual(chat.count('_door_words.scrub_filename('), 1)                    # the deck's own file name
        pma_src = Path(ROOT, 'prometheus_analysis.py').read_text(encoding='utf-8')
        self.assertNotIn('_dw.scrub_read(s)', pma_src)
        pack = Path(ROOT, 'design', 'crosswalk-design', 'scripts', 'cw_door_copy.py')
        if pack.is_file():
            self.assertIn('def proper_name_spans', pack.read_text(encoding='utf-8'))


# ============================================================================ 7
class TwoJourneysOneName(unittest.TestCase):
    RUNS = FX['journeys']
    S6 = 'Abbott Elementary Season 6 on Hulu, Hulu on Disney+ and the ABC app'
    LIB = 'Abbott Elementary Library (Seasons 1 to 5) on Hulu, Hulu on Disney+ and HBO Max'

    def _name(self, words):
        got = JP.pick(words, self.RUNS)
        return JP.name_of(got['item']) if got.get('item') else None

    def test_the_words_that_differ_pick_one(self):
        for words in ('the Abbott Elementary library journey', 'Open the Abbott Elementary library journey',
                      'what does the Abbott Elementary library journey say about where viewers came from',
                      'the Abbott Elementary back catalog journey', 'Abbott Elementary season 3 journey',
                      'the Abbott Elementary seasons 1 to 5 journey', 'Abbott Elementary on HBO Max journey'):
            self.assertEqual(self._name(words), self.LIB, words)
        for words in ('email me the Abbott Elementary season 6 journey', 'the current Abbott Elementary journey',
                      'the new season of Abbott Elementary journey', 'the latest Abbott Elementary journey', 'Abbott Elementary ABC app journey'):
            self.assertEqual(self._name(words), self.S6, words)
        self.assertEqual(self._name('the Young Sheldon journey'), 'Young Sheldon on Netflix')
        self.assertEqual(JP.own_name(self.LIB), 'Abbott Elementary')
        self.assertEqual(JP.own_name('Babylon 5 on Prime Video'), 'Babylon 5')              # a number glued to a name is the name

    def test_one_short_question_only_when_the_words_cannot_tell(self):
        got = JP.pick('the Abbott Elementary journey', self.RUNS)
        self.assertIsNone(got.get('item'))
        self.assertEqual(got['ask'], 'Which Abbott Elementary journey: Season 6 or Library (Seasons 1 to 5)?')
        self.assertEqual(got['options'], [self.S6, self.LIB])
        self.assertEqual(JP.pick('where is the digital journey', self.RUNS), {})
        self.assertEqual(JP.pick('the Gilmore Girls journey', self.RUNS), {})

    def test_the_three_readers_of_a_journey_name(self):
        from prometheus import journey_lane as JL
        from prometheus import send_file as SF
        from prometheus import wayfinding as WF
        import migration.journey_iq as jiq
        orig = jiq.list_runs
        jiq.list_runs = lambda s3, username=None, limit=100: list(self.RUNS)
        try:
            admin = {'username': 'admin', 'role': 'super_admin'}
            q = 'On the Abbott Elementary library journey, where did the first post sit?'
            run = JL.find_journey(q, s3=object(), user=admin, uname='admin')
            self.assertEqual(run['project_name'], self.LIB)                                # it resolved to neither before
            self.assertEqual(JL.find_journey('the Abbott Elementary season 6 journey conversion', s3=object(), user=admin, uname='admin')['project_name'], self.S6)
            ask = {}
            self.assertIsNone(JL.find_journey('what is the conversion on the Abbott Elementary journey', s3=object(), user=admin, uname='admin', ask_out=ask))
            self.assertEqual(ask['ask'], 'Which Abbott Elementary journey: Season 6 or Library (Seasons 1 to 5)?')
            self.assertEqual(JL.find_journey('what is the conversion on the Young Sheldon journey', s3=object(), user=admin, uname='admin')['project_name'],
                             'Young Sheldon on Netflix')
        finally:
            jiq.list_runs = orig
        items = [{'name': r['project_name'], 'key': r['key'], 'created_at': r['created_at']} for r in self.RUNS]
        # "library" used to be dropped as "in my library", so the name pointed at both
        self.assertEqual([j['name'] for j in SF.match_journeys('Abbott Elementary library', items)], [self.LIB])
        self.assertEqual([j['name'] for j in SF.match_journeys('current Abbott Elementary', items)], [self.S6])
        self.assertEqual(len(SF.match_journeys('Abbott Elementary', items)), 2)            # the caller asks its one question
        self.assertEqual([j['name'] for j in SF.match_journeys('Young Sheldon', items)], ['Young Sheldon on Netflix'])
        self.assertEqual([j['name'] for j in WF._match('Abbott Elementary library', items)], [self.LIB])
        self.assertEqual([j['name'] for j in WF._match('Abbott Elementary season 6', items)], [self.S6])


if __name__ == '__main__':
    unittest.main(verbosity=2)
