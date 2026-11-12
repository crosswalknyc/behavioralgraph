#!/usr/bin/env python3
"""What Prometheus says is true, round three (Jenna 2026-11-11: "these
errors cannot happen. fix and ensure itll be correct going forward").

What the first two rounds found and left:

  1. A probe leaves nothing behind: its files are kept apart from
     readers' at the store client itself, its job record is cleared by
     the regular pass, and the golden door can resolve a library
     profile for a deck request.                 -> AProbeLeavesNothing
  2. Every part of a question in several parts is quoted and run on the
     subject on record, with the platform as where and a genre as a
     cut; a part that names nothing inherits that subject, never the
     platform.                              -> EachPartNamesItsSubject
  3. A deck asked for with a question in several parts is built once,
     when every part has landed; a quoted part that is declined or
     lapses is named as not in it.           -> TheDeckWaitsForEveryPart
  4. "can we build profile" right after a read is a brief on that
     read's subject, for the reader to confirm.
                                       -> ABriefOnTheThreadsSubject
  5. Nothing is filed in the shared read store under a word that names
     nothing.                               -> NothingFiledUnderTheWord
  6. The one-or-several question draws its two replies as chips.
                                                   -> TheSmallThings

Hermetic: the stub host and in-memory store of test_pm_new_chat_no_bleed,
and botocore with its sender replaced (no request leaves the process).
No model, no S3, no app module, no mail.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import io
import json
import os
import re
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

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold_r3', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

import insights_ledger as IL  # noqa: E402
from prometheus import build_lane as BL  # noqa: E402
from prometheus import ledger_gate as LG  # noqa: E402
from prometheus import list_shape as LS  # noqa: E402
from prometheus import pending_offers as PO  # noqa: E402
from prometheus import platform_scope as PS  # noqa: E402
from prometheus import probe_files as PF  # noqa: E402
from prometheus import ride_along as RA  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus.host import host  # noqa: E402

APP, World, USER, THREADS = NB.APP, NB.World, NB.USER, NB.THREADS
FX2 = json.loads(Path(HERE, 'fixtures', 'pm_truth_round2_2026_11_11.json').read_text(encoding='utf-8'))
FX1 = json.loads(Path(HERE, 'fixtures', 'pm_truth_2026_11_11.json').read_text(encoding='utf-8'))
MULTI = FX1['armine_words']['summarize_in_powerpoint']
PARTS = ["Looking at YouTube. What type of categories do best for digital original content: Comedy, lifestyle/arts & culture, Faith, "
         "Young Adult, Drama, and Horror do in this space.?", "Can we give some examples?", "how is the audience like?"]
# the subject on record for her question, as the shared read store names it (index of 2026-11-12)
HELD = 'Digital original content categories on YouTube (Comedy, Lifestyle/Arts & Culture, Drama, Horror, Young Adult, Faith)'
HELD_KEY = 'digital_original_content_categories_on_youtube_comedy_lifestyle_arts_culture_dra'
CHAT_SRC = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
GOLDEN = {'username': 'canary:golden', 'email': '', 'role': 'super_admin', 'has_chatbot_profile_iq_access': True, 'unlimited': True,
          'company': 'Crosswalk'}


class _Store(NB.FakeS3):
    """The in-memory store with the write times a clean-up pass reads."""

    def __init__(self):
        super().__init__()
        self.at = {}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        super().put_object(Bucket, Key, Body, ContentType=ContentType, **kw)
        self.at[Key] = dt.datetime.now(dt.timezone.utc)

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k, 'LastModified': self.at.get(k) or dt.datetime.now(dt.timezone.utc)}
                             for k in sorted(self.objs) if k.startswith(Prefix)], 'IsTruncated': False}

    def generate_presigned_url(self, method, Params=None, **kw):
        return 'https://store.example/' + str((Params or {}).get('Key'))


def _world():
    w = World()
    w.s3 = _Store()
    w._bind()
    idx = {'active': NB.T_MAD, 'threads': []}
    w.put(f'{THREADS}/{USER}/index.json', idx)
    return w


def _writes_files(w):
    """Cores that do what the real ones do: the read step writes its table under generated_data/ and hands
    back a signed link; the deck step writes a deck under generated_decks/."""
    def analyze(user, body, text, history):
        w.calls.append(('analyze', text, dict(body), {}))
        key = f"generated_data/{len(w.calls):012x}/Reba McEntire Age band.csv"
        host.s3_client.put_object(Bucket=host.bucket, Key=key, Body=b'age,share\n25-34,31.2\n', ContentType='text/csv')
        url = host.s3_client.generate_presigned_url('get_object', Params={'Bucket': host.bucket, 'Key': key}, ExpiresIn=600)
        return {'success': True, 'action': 'answer', 'reply': 'Reba McEntire fans are 31.2% aged 25 to 34.', 'subject': 'Reba McEntire',
                'followups': [], 'file_link': {'url': url, 'key': key, 'label': 'Download Reba McEntire Age band.csv'}}

    def deck(user, body):
        w.calls.append(('deck', str(body.get('text') or ''), dict(body), {}))
        host.s3_client.put_object(Bucket=host.bucket, Key='generated_decks/d1e2c3k4j5b6/Reba_McEntire_Insights.pptx', Body=b'PK deck',
                                  ContentType='application/vnd.openxmlformats-officedocument.presentationml.presentation')
        return {'success': True, 'job_id': 'd1e2c3k4j5b6', 'subject': 'Reba McEntire', 'confirm_line': 'Building the Reba McEntire insights deck.'}
    host.bind(analyze_core=analyze, deck_core=deck)


def _ask(w, user, text, *, via='session', client='dashboard', surface='analyze', tid=None, history=None, extra=None, mark=None):
    body = {'text': text, 'client': client, 'surface': surface, '_client_surface_sent': surface, 'history': list(history or [])}
    if tid:
        body['thread_id'] = tid
    body.update(extra or {})
    with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
        for k, v in (mark or {}).items():
            setattr(flask.g, k, v)
        return RA.run(S.ask, dict(user), body, via=via)


def _reader_keys(w):
    return sorted(k for k in w.s3.objs if PF.reader_facing(k))


# ============================================================================ 1
class AProbeLeavesNothing(unittest.TestCase):
    def test_a_probe_replay_of_a_table_a_deck_and_a_file_leaves_the_listing_unchanged(self):
        """The night of 2026-11-11: one question replayed as canary:golden wrote seven CSV files under
        generated_data/. The same three asks a reader makes (a read that produces a table, a deck, the file)
        now leave every reader-facing prefix exactly as it was, and the whole listing once the pass has run."""
        w = _world()
        _writes_files(w)
        before = sorted(w.s3.objs)
        self.assertEqual(_reader_keys(w), [])
        probe = {'_pm_ask_user': 'canary:golden'}
        env, _ = _ask(w, GOLDEN, 'What age groups are Reba McEntire fans?', via='canary', client='golden', mark=probe)
        self.assertIn('31.2%', env['text'])
        link = env['raw']['file_link']['url']
        self.assertIn(PF.FILES_PREFIX + 'generated_data/', link)            # the replay can still open its file to grade it
        _ask(w, GOLDEN, 'Build a deck on Reba McEntire', via='canary', client='golden', surface='deck', mark=probe)
        _ask(w, GOLDEN, 'Send me the Reba McEntire age file as a CSV', via='canary', client='golden', mark=probe)
        self.assertEqual(_reader_keys(w), [])                               # nothing where a reader's files live
        kept = sorted(k for k in w.s3.objs if k.startswith(PF.PROBE_PREFIX))
        self.assertGreaterEqual(len(kept), 2)
        self.assertTrue(all(k.startswith(PF.FILES_PREFIX) for k in kept))
        self.assertEqual(sorted(set(w.s3.objs) - set(kept)), before)        # and nothing anywhere else
        # the content the replay grades is there, under the probe's own prefix
        got = host.s3_client.get_object(Bucket=host.bucket, Key=kept[0])['Body'].read()
        self.assertTrue(got)
        # under an hour old: left for the replay; past the hour: gone, and the listing is what it was
        self.assertEqual(PF.sweep(w.s3, host.bucket, now=time.time() + 600), {'files': 0, 'jobs': 0})
        out = PF.sweep(w.s3, host.bucket, now=time.time() + PF.KEEP_S + 60)
        self.assertEqual(out['files'], len(kept))
        self.assertEqual(sorted(w.s3.objs), before)

    def test_a_readers_same_asks_write_where_they_always_did(self):
        w = _world()
        _writes_files(w)
        env, _ = _ask(w, NB.ARMINE, 'What age groups are Reba McEntire fans?', tid=w.new_thread())
        self.assertTrue(_reader_keys(w)[0].startswith('generated_data/'))
        self.assertIn('https://store.example/generated_data/', env['raw']['file_link']['url'])
        self.assertFalse([k for k in w.s3.objs if k.startswith(PF.PROBE_PREFIX)])

    def test_the_rule_sits_on_the_store_client_itself(self):
        """Every writer builds its own key and calls the store itself; the rule is on the client, and on every
        client made after it. botocore's sender is replaced, so the request that would have gone out is read."""
        import boto3
        from botocore.awsrequest import AWSResponse
        sent = []

        def before_send(request, **kw):
            sent.append((request.method, request.url.split('?')[0].split('amazonaws.com')[1]))

            class _Raw:
                def stream(self, *a, **k):
                    return iter([b''])

                def read(self, *a, **k):
                    return b''
            return AWSResponse(request.url, 200, {'ETag': '"x"'}, _Raw())
        c = boto3.client('s3', region_name='us-east-2', aws_access_key_id='AKIAFAKEFAKEFAKEFAKE', aws_secret_access_key='fake')
        c.meta.events.register('before-send.s3', before_send)
        PF.install(c)
        PF.install(c)                                                       # twice is once
        with LG.as_asker('canary:golden', probe=True):
            c.put_object(Bucket='b', Key='generated_data/aaa/x.csv', Body=b'1')
            c.upload_fileobj(io.BytesIO(b'deck'), 'b', 'generated_decks/d.pptx')      # a managed transfer runs on another thread
            c.put_object(Bucket='b', Key='deliverables/r.pdf', Body=b'1')
            c.put_object(Bucket='b', Key='system/prometheus_reads/j.json', Body=b'1')  # a system record is not a reader's file
            url = c.generate_presigned_url('get_object', Params={'Bucket': 'b', 'Key': 'generated_data/aaa/x.csv'}, ExpiresIn=60)
        with LG.as_asker('jessie'):
            c.put_object(Bucket='b', Key='generated_data/bbb/y.csv', Body=b'1')
        c.put_object(Bucket='b', Key='generated_data/ccc/z.csv', Body=b'1')           # nobody named: an operator's script
        c2 = boto3.client('s3', region_name='us-east-2', aws_access_key_id='AKIAFAKEFAKEFAKEFAKE', aws_secret_access_key='fake')
        c2.meta.events.register('before-send.s3', before_send)
        with LG.as_asker('canary:probe', probe=True):
            c2.put_object(Bucket='b', Key='generated_data/ddd/w.csv', Body=b'1')      # a client made after the install
        paths = [p for m, p in sent if m == 'PUT']
        self.assertEqual(paths, ['/b/system/prometheus_probe/files/generated_data/aaa/x.csv',
                                 '/b/system/prometheus_probe/files/generated_decks/d.pptx',
                                 '/b/system/prometheus_probe/files/deliverables/r.pdf',
                                 '/b/system/prometheus_reads/j.json',
                                 '/b/generated_data/bbb/y.csv', '/b/generated_data/ccc/z.csv',
                                 '/b/system/prometheus_probe/files/generated_data/ddd/w.csv'])
        self.assertIn('/b/system/prometheus_probe/files/generated_data/aaa/x.csv', url)
        # a probe that reads a file it did not write (a reader's own deck, named in a golden case) reads it as it is
        gone = {'on': False}

        def missing_probe_copy(request, **kw):
            if gone['on'] and '/system/prometheus_probe/' in request.url:
                class _Raw:
                    def stream(self, *a, **k):
                        return iter([b''])

                    def read(self, *a, **k):
                        return b''
                return AWSResponse(request.url, 404, {}, _Raw())
            return None
        c.meta.events.register_first('before-send.s3', missing_probe_copy)
        gone['on'] = True
        with LG.as_asker('canary:golden', probe=True):
            theirs = c.generate_presigned_url('get_object', Params={'Bucket': 'b', 'Key': 'generated_decks/readers_own.pptx'}, ExpiresIn=60)
        self.assertIn('/b/generated_decks/readers_own.pptx', theirs)
        self.assertNotIn('prometheus_probe', theirs)

    def test_a_probes_job_record_is_cleared_by_the_pass_and_a_readers_is_kept(self):
        w = _world()

        @LG.job_scope()
        def job(job_id, username, probe=False):
            host.s3_client.put_object(Bucket=host.bucket, Key=f'system/prometheus_reads/{job_id}.json',
                                      Body=json.dumps({'job_id': job_id, 'user': username, 'status': 'done'}).encode())
            return 'ran'
        self.assertEqual(job('aaaaaaaaaaaa', 'sandbox_partner', probe=True), 'ran')
        self.assertEqual(job('bbbbbbbbbbbb', 'jessie'), 'ran')
        note = w.get(PF.JOBS_PREFIX + 'aaaaaaaaaaaa.json')
        self.assertIn('system/prometheus_reads/aaaaaaaaaaaa.json', note['records'])
        self.assertIsNone(w.get(PF.JOBS_PREFIX + 'bbbbbbbbbbbb.json'))
        # within the hour the smoke can still poll its job; after it the record and the note are gone
        PF.sweep(w.s3, host.bucket, now=time.time() + 300)
        self.assertTrue(w.get('system/prometheus_reads/aaaaaaaaaaaa.json'))
        self.assertEqual(PF.sweep(w.s3, host.bucket, now=time.time() + PF.KEEP_S + 60)['jobs'], 1)
        self.assertIsNone(w.get('system/prometheus_reads/aaaaaaaaaaaa.json'))
        self.assertIsNone(w.get(PF.JOBS_PREFIX + 'aaaaaaaaaaaa.json'))
        self.assertTrue(w.get('system/prometheus_reads/bbbbbbbbbbbb.json'))            # a reader's record is never touched
        # a job that raises still leaves its note
        @LG.job_scope()
        def bad(job_id, username, probe=False):
            raise RuntimeError('boom')
        with self.assertRaises(RuntimeError):
            bad('cccccccccccc', 'canary:golden', probe=True)
        self.assertTrue(w.get(PF.JOBS_PREFIX + 'cccccccccccc.json'))

    def test_the_golden_door_can_resolve_a_library_profile_for_a_deck_and_no_one_else(self):
        """A probe got "Not authenticated" on any deck that named a library profile: the access check reads the
        session, and the golden door has none."""
        calls = []

        def check(key):
            calls.append(key)
            return False, ({'success': False, 'error': 'Not authenticated'}, 401)
        with APP.test_request_context('/'):
            flask.g._pm_probe, flask.g._pm_ask_user = True, 'canary:golden'
            self.assertEqual(PF.run_access(check, 'Mistletoe_Murders_Viewers_11_11_2026_22_26.csv'), (True, None))
        self.assertEqual(calls, [])
        with APP.test_request_context('/'):                                 # a reader: the check, as before
            flask.g._pm_ask_user = 'armine'
            self.assertFalse(PF.run_access(check, 'k.csv')[0])
        with APP.test_request_context('/'):                                 # a session that calls itself a probe is still checked
            flask.g._pm_probe, flask.g._pm_ask_user = True, 'armine'
            self.assertFalse(PF.run_access(check, 'k.csv')[0])
        self.assertEqual(calls, ['k.csv', 'k.csv'])

    def test_the_wiring(self):
        self.assertEqual(CHAT_SRC.count('_probe_files.run_access(_H._require_profile_run_access,'), 2)
        self.assertNotRegex(CHAT_SRC[CHAT_SRC.find('def _pm_resolve_deck_subject'):CHAT_SRC.find('def _pm_deck_fuzzy')] or 'x',
                            r"=\s*_H\._require_profile_run_access\(")
        host_src = Path(ROOT, 'prometheus', 'host.py').read_text(encoding='utf-8')
        self.assertIn("_pf.install(caps['s3_client'])", host_src)
        self.assertIn('_probe_job_ended(a)', Path(ROOT, 'prometheus', 'ledger_gate.py').read_text(encoding='utf-8'))
        self.assertIn('_pf.sweep_with_host(host, now=now)', Path(ROOT, 'prometheus', 'build_ready.py').read_text(encoding='utf-8'))
        self.assertLess(len(CHAT_SRC.splitlines()), 19900)


# ============================================================================ 2
class EachPartNamesItsSubject(unittest.TestCase):
    def setUp(self):
        self._load = IL._load_index
        self.doc = {'subjects': {
            'youtube': {'subject': 'youtube', 'entries': [{'k': 'youtube|audience|any|any', 'subject': 'youtube', 'q': 'who watches youtube',
                                                            'qn': 'who watches youtube', 'ts': '2026-11-10T20:00:00Z', 'route': 'reasoned_metrics',
                                                            'reply': 'x'}]},
            HELD_KEY: {'subject': HELD, 'entries': [{'k': HELD_KEY + '|viewership|any|any', 'subject': HELD, 'q': 'delivered anchors',
                                                      'qn': 'delivered anchors', 'ts': '2026-11-12T02:19:33Z', 'route': 'delivered_deck',
                                                      'reply': ''}]}}}
        IL._load_index = lambda force=False: self.doc

    def tearDown(self):
        IL._load_index = self._load

    def test_her_three_parts_split_as_she_wrote_them(self):
        from prometheus import multi_ask as MA
        self.assertEqual(MA.split(RA.split(MULTI)['question']), PARTS)

    def test_the_first_part_is_quoted_on_the_subject_on_record_not_on_a_genre_or_the_platform(self):
        """The canary's quote read "1. I can pull that read on Comedy. 2. I can pull that read on YouTube.": a
        genre and a platform, each standing in for her subject."""
        for given in ('Comedy', 'Horror', 'YouTube', ''):
            self.assertEqual(PS.quote_subject(PARTS[0], [], given, IL), HELD, given)
        self.assertTrue(PS.is_cut_of('Comedy', HELD, PARTS[0]))
        # a subject of its own stays what it is
        for given in ('Nike', 'Reba McEntire'):
            self.assertEqual(PS.quote_subject(PARTS[0], [], given, IL), given)
        self.assertEqual(PS.quote_subject('How big is the Comedy audience on YouTube?', [], 'Comedy', IL), 'Comedy')
        self.assertFalse(PS.is_cut_of('Comedy', HELD, 'How big is the Comedy audience on YouTube?'))
        self.assertFalse(PS.is_cut_of(HELD, HELD, PARTS[0]))

    def test_the_read_step_takes_a_genre_it_was_handed_as_a_cut_too(self):
        """After the quote was fixed the same question, asked on its own, still came back "I can pull that read on
        Comedy": the read step is handed the first thing the words list as its subject and makes its own quote."""
        base, digest, hint, bind, cohort, _sw = PS.apply_to_read(PARTS[0], [], IL, None, '', 'Comedy', '', '', None)
        self.assertEqual((base, hint, bind, cohort), (None, HELD, HELD, 'on YouTube'))
        # a subject of its own, and a genre asked about as the audience, stay what they are
        self.assertEqual(PS.apply_to_read(PARTS[0], [], IL, None, '', 'Nike', '', '', None)[2:4], ('Nike', ''))
        self.assertEqual(PS.apply_to_read('How big is the Comedy audience on YouTube?', [], IL, None, '', 'Comedy', '', '', None)[2:4],
                         ('Comedy', ''))
        # the line that says what is being read leads with the subject and does not list its cuts or its platform beside it
        rd = {'subjects': [{'as_written': 'Horror', 'catalog': 'Horror Streaming Enthusiasts', 'kind': 'audience'},
                           {'as_written': 'Comedy', 'catalog': '', 'kind': 'audience'}, {'as_written': 'YouTube', 'kind': 'platform'}]}
        line = SM.on_it_line(rd, None, bound=HELD)
        self.assertTrue(line.startswith(f'On it: reading {HELD} now.'), line[:160])
        self.assertIn('Horror Streaming Enthusiasts', SM.on_it_line(rd, None, bound=''))     # with nothing bound the line is what it was

    def test_a_part_bound_to_that_subject_never_runs_on_the_platforms_own_profile(self):
        """"Can we give some examples?" rode on the first question's subject, but the earlier words named
        YouTube, the library has a YouTube profile, and the part was quoted on it."""
        yt = {'subject': 'YouTube', 's3_key': 'YouTube_09_01_2026_10_00.csv', 'source': 'catalog'}
        hist = [{'role': 'user', 'text': PARTS[0]}]
        hit = PS.for_read(PARTS[1], hist, dict(yt), IL, HELD, HELD)
        self.assertEqual((hit['subject'], hit['platform'], hit['source']), (HELD, 'YouTube', 'bound'))
        base, digest, hint, bind, cohort, _sw = PS.apply_to_read(PARTS[2], hist, IL, dict(yt), 'ROWS OF THE YOUTUBE PROFILE', HELD, HELD, '', None)
        self.assertIsNone(base)
        self.assertEqual((digest, hint, bind), ('', HELD, HELD))            # no profile rows, the subject on record
        # with nothing bound the part is left to the lane's own reading, and a platform bound on purpose stays
        self.assertIsNone(PS.for_read(PARTS[1], [], dict(yt), IL, '', ''))
        self.assertIsNone(PS.for_read('how big is it', [], dict(yt), IL, 'YouTube', 'YouTube'))

    def test_through_the_door_the_later_parts_inherit_the_subject_and_never_the_platform(self):
        w = World()

        def analyze(user, body, text, history):
            w.calls.append(('analyze', text, dict(body), {}))
            out = {'success': True, 'action': 'answer', 'reply': 'On it.', 'read_job_id': 'job-' + str(len(w.calls)), 'followups': []}
            if text.startswith('Looking at YouTube'):
                out['subject'] = HELD
            elif text.startswith('Can we give'):
                out['subject'] = 'YouTube'             # what the step used to hand back for the second part
            return out
        host.bind(analyze_core=analyze)
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            S.ask(dict(NB.ARMINE), {'text': RA.split(MULTI)['question'], 'client': 'dashboard', 'surface': 'analyze',
                                    '_client_surface_sent': 'analyze', 'history': []})
        by = {c[1].strip()[:20]: c[2] for c in w.calls if c[0] == 'analyze'}
        self.assertEqual(by['Can we give some exa'].get('bind_subject'), HELD)
        self.assertEqual(by['how is the audience '].get('bind_subject'), HELD)     # never 'YouTube'


# ============================================================================ 3
class TheDeckWaitsForEveryPart(unittest.TestCase):
    def setUp(self):
        self.w = _world()
        self.tid = self.w.new_thread()
        self.decks = []
        self._jt = S.job_thread_id
        S.job_thread_id = lambda j: self.tid
        self.plan = {'kinds': ['deck'], 'words': 'Summarize in powerpoint', 'question': RA.split(MULTI)['question']}
        self.offer_raw = {'success': True, 'action': 'answer', 'reply': 'x', 'read_job_id': 'job-3', 'read_job_ids': ['job-3'],
                          'subject': HELD,
                          'panel_offer': {'kind': 'multi', 'question': self.plan['question'], 'subject': HELD, 'price_label': '$100',
                                          'parts': [{'no': 1, 'status': 'priced', 'text': PARTS[0], 'subject': HELD, 'price_label': '$100'},
                                                    {'no': 2, 'status': 'included', 'text': PARTS[1], 'subject': HELD}]}}
        self.oid = PO.record(USER, self.tid, self.offer_raw, self.plan['question'])
        env = {'kind': 'job', 'thread_id': self.tid, 'text': '3) how is the audience like?\nOn it: reading now.', 'raw': dict(self.offer_raw),
               'job': {'type': 'read', 'id': 'job-3'}}
        with APP.test_request_context('/'):
            self.env = RA._carry(env, self.plan, dict(NB.ARMINE), {'text': MULTI, 'client': 'dashboard', 'thread_id': self.tid}, 'session')
        self.key = RA._key(USER, self.tid)
        self.turns = [{'role': 'user', 'text': MULTI, 'ts': NB._iso(-60)},
                      {'role': 'agent', 'text': 'On it: reading now.', 'ts': NB._iso(-50), 'meta': {'read_job_id': 'job-3'}}]
        self.w.put(f'{THREADS}/{USER}/{self.tid}.json', self.turns)

    def tearDown(self):
        S.job_thread_id = self._jt

    def _deck_core(self, user, body):
        self.decks.append(dict(body))
        return {'success': True, 'job_id': f'deck{len(self.decks):08d}', 'subject': HELD,
                'confirm_line': 'Building an insights deck from the read you already have. The download link lands here when it is ready.'}

    def _land(self, job_id):
        turns = self.w.get(f'{THREADS}/{USER}/{self.tid}.json')
        for t in turns:
            if (t.get('meta') or {}).get('read_job_id') == job_id:
                t['meta'].update(kind='read', landed_at=NB._iso(), subject=HELD)
                t['text'] = f'On {HELD}:\n\nComedy leads at 4,713,882 US viewers.'
        self.w.put(f'{THREADS}/{USER}/{self.tid}.json', turns)
        return RA.on_read_landed(USER, job_id, {'reply': 'x'}, deck_core=self._deck_core, request_context=lambda: APP.test_request_context('/'),
                                 user=dict(NB.ARMINE))

    def test_the_line_says_once_and_the_record_holds_the_quoted_part(self):
        self.assertTrue(self.env['text'].rstrip().endswith(RA.DECK_ALL_PARTS_LINE))
        rec = self.w.get(self.key)
        self.assertEqual((rec['state'], rec['job_ids'], rec['waits_on_quote'], rec['quoted']), ('pending', ['job-3'], True, [PARTS[0]]))
        self.assertEqual(RA.quote_state(USER, self.tid, rec), 'open')

    def test_the_first_read_landing_no_longer_starts_the_deck(self):
        """What happened before: the deck was built when the running part landed, with the quoted parts unanswered."""
        self.assertEqual(self._land('job-3'), 'waiting')
        self.assertEqual(self.decks, [])
        self.assertEqual(self.w.get(self.key)['state'], 'pending')

    def test_approved_the_deck_is_built_once_when_the_last_part_lands(self):
        self.assertEqual(self._land('job-3'), 'waiting')
        PO.close(USER, self.oid, 'approved')
        approve_env = {'kind': 'job', 'thread_id': self.tid, 'raw': {'read_job_ids': ['job-1', 'job-2']}}
        with APP.test_request_context('/'):
            RA._quote_answered(dict(NB.ARMINE), {'text': 'Approve', 'thread_id': self.tid}, (approve_env, 200))
        rec = self.w.get(self.key)
        self.assertEqual((rec['quote'], sorted(rec['job_ids'])), ('approved', ['job-1', 'job-2']))
        turns = self.w.get(f'{THREADS}/{USER}/{self.tid}.json')
        turns += [{'role': 'agent', 'text': 'On it: reading now.', 'ts': NB._iso(-20), 'meta': {'read_job_id': j}} for j in ('job-1', 'job-2')]
        self.w.put(f'{THREADS}/{USER}/{self.tid}.json', turns)
        self.assertEqual(self._land('job-1'), 'waiting')
        self.assertEqual(self.decks, [])
        self.assertEqual(self._land('job-2'), 'started')
        self.assertEqual(len(self.decks), 1)                                # once
        started = [t for t in self.w.get(f'{THREADS}/{USER}/{self.tid}.json') if (t.get('meta') or {}).get('kind') == 'deck_started']
        self.assertEqual(len(started), 1)
        self.assertNotIn('does not include', started[0]['text'])            # every part is in it
        self.assertEqual(self._land('job-2'), 'none')                       # and never a second deck
        self.assertEqual(len(self.decks), 1)

    def test_declined_the_deck_is_built_on_what_landed_and_says_what_is_not_in_it(self):
        self.assertEqual(self._land('job-3'), 'waiting')
        PO.close(USER, self.oid, 'cancelled')
        with APP.test_request_context('/'):
            host.bind(deck_core=self._deck_core)
            orig = RA.start_deck
            RA.start_deck = lambda *a, **k: orig(*a, **dict(k, request_context=lambda: APP.test_request_context('/')))
            try:
                RA._quote_answered(dict(NB.ARMINE), {'text': 'Cancel', 'thread_id': self.tid}, ({'kind': 'answer', 'thread_id': self.tid, 'raw': {}}, 200))
            finally:
                RA.start_deck = orig
        self.assertEqual(len(self.decks), 1)
        last = self.w.get(f'{THREADS}/{USER}/{self.tid}.json')[-1]
        self.assertIn('It does not include the part that was not approved: "Looking at YouTube. What type of categories do best', last['text'])
        self.assertEqual(last['meta']['ride_along']['not_included'], [RA._short(PARTS[0])])
        rec = self.w.get(self.key)
        self.assertEqual((rec['state'], rec['not_included']), ('started', [PARTS[0]]))

    def test_declined_while_the_read_is_still_running_waits_for_it(self):
        PO.close(USER, self.oid, 'cancelled')
        with APP.test_request_context('/'):
            RA._quote_answered(dict(NB.ARMINE), {'text': 'Cancel', 'thread_id': self.tid}, ({'kind': 'answer', 'thread_id': self.tid, 'raw': {}}, 200))
        self.assertEqual(self.decks, [])                                    # nothing has landed yet
        self.assertEqual(self._land('job-3'), 'started')
        self.assertIn('does not include', self.w.get(f'{THREADS}/{USER}/{self.tid}.json')[-1]['text'])

    def test_a_quote_nobody_answers_lapses_and_the_pass_builds_the_deck(self):
        self.assertEqual(self._land('job-3'), 'waiting')
        self.assertEqual(RA.sweep_with_host(host, force=True), 0)           # still open: still waiting
        doc = PO._load(USER)
        for o in doc['offers']:
            o['ts'] = NB._iso(-26 * 3600)                                   # a day and two hours old
        PO._save(USER, doc)
        rec = dict(self.w.get(self.key), created_at=time.time() - 26 * 3600)
        self.w.put(self.key, rec)
        self.assertEqual(RA.quote_state(USER, self.tid, rec), 'closed')
        orig = RA.start_deck
        RA.start_deck = lambda *a, **k: orig(*a, **dict(k, deck_core=self._deck_core, request_context=lambda: APP.test_request_context('/')))
        try:
            self.assertEqual(RA.sweep_with_host(host, force=True), 1)
            self.assertEqual(RA.sweep_with_host(host, force=True), 0)       # once
        finally:
            RA.start_deck = orig
        self.assertEqual(len(self.decks), 1)
        self.assertIn('does not include', self.w.get(f'{THREADS}/{USER}/{self.tid}.json')[-1]['text'])

    def test_one_read_and_no_quote_is_what_it_was(self):
        w = _world()
        tid = w.new_thread('eeeeeeeeee')
        S.job_thread_id = lambda j: tid
        env = {'kind': 'job', 'thread_id': tid, 'text': 'On it.', 'raw': {'read_job_id': 'job-9'}, 'job': {'type': 'read', 'id': 'job-9'}}
        with APP.test_request_context('/'):
            out = RA._carry(env, self.plan, dict(NB.ARMINE), {'text': MULTI, 'client': 'dashboard', 'thread_id': tid}, 'session')
        self.assertTrue(out['text'].rstrip().endswith(RA.DECK_NEXT_LINE))
        w.put(f'{THREADS}/{USER}/{tid}.json', [{'role': 'agent', 'text': f'On {HELD}:\n\nx', 'ts': NB._iso(),
                                                'meta': {'read_job_id': 'job-9', 'kind': 'read', 'landed_at': NB._iso(), 'subject': HELD}}])
        got = RA.on_read_landed(USER, 'job-9', {'reply': 'x'}, deck_core=self._deck_core,
                                request_context=lambda: APP.test_request_context('/'), user=dict(NB.ARMINE))
        self.assertEqual(got, 'started')
        self.assertEqual(RA.missing_line([]), '')


# ============================================================================ 4
class ABriefOnTheThreadsSubject(unittest.TestCase):
    hist = FX2['armine_deck']['thread_before'][:9]                          # through the read that landed at 22:07:53 UTC

    def test_her_words_got_the_product_description_and_now_name_the_threads_subject(self):
        """22:08:51 UTC, one turn after a read on Mistletoe Murders Viewers: "can we build profile". The reply
        was the Profile IQ description, ending "Tell me the subject and the window"."""
        words = FX2['armine_deck']['thread_before'][9]['text']
        self.assertEqual(words, 'can we build profile')
        self.assertTrue(FX2['armine_deck']['thread_before'][10]['text'].startswith('Yes. A Profile IQ profile builds the audience'))
        self.assertTrue(SM.capability_ask(words))                           # the cause: a can-we that names a product
        self.assertEqual(BL.kind(words, self.hist), 'thread_ask')
        out = BL.rewrite(words, self.hist)
        self.assertEqual(out['text'], 'Please queue one Profile IQ: Mistletoe Murders Viewers (all seasons)')
        self.assertTrue(out['confirm'])
        self.assertEqual(out['source'], 'thread_read')

    def test_through_the_door_it_is_a_brief_for_the_reader_to_confirm(self):
        w = World()
        tid = w.new_thread()
        w.calls.clear()
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            env, _st = S.ask(dict(NB.ARMINE), {'text': 'can we build profile', 'client': 'dashboard', 'thread_id': tid, 'surface': 'interpret',
                                               '_client_surface_sent': 'interpret', 'history': list(self.hist)})
        built = [c for c in w.calls if c[0] == 'interpret']
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0][1], 'Please queue one Profile IQ: Mistletoe Murders Viewers (all seasons)')
        self.assertTrue(built[0][2].get('_pm_no_auto_start'))               # nothing starts on its own: it was a question
        self.assertNotIn('A Profile IQ profile builds', env.get('text') or '')
        i = CHAT_SRC.find("body.get('_pm_no_auto_start')")
        self.assertTrue(0 < i < CHAT_SRC.find('resp = _pm_approve_core(user, {', i))   # read before the build would start

    def test_with_no_read_on_the_thread_and_with_a_subject_named_nothing_changes(self):
        self.assertIsNone(BL.rewrite('can we build profile', []))
        self.assertIsNone(BL.rewrite('can we build profile', [{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}]))
        for words in ('can we build a profile for Nike', 'can we build a deck', 'Can we track a campaign?', 'can we build profile for mistletoe murders'):
            self.assertNotEqual(BL.kind(words, self.hist), 'thread_ask', words)
        for words in ('could we get a profile', 'Can you run the full profile now?', 'can we build a Profile IQ'):
            self.assertEqual(BL.kind(words, self.hist), 'thread_ask', words)


# ============================================================================ 5
class NothingFiledUnderTheWord(unittest.TestCase):
    def test_the_note_was_filed_under_the_and_is_refused_now(self):
        """Two rows from a deliverable's note sat in the shared read store under "The": its first line, "The Read /
        Vertical Drama. Twelve one-sheets ...", was cut at the slash and trimmed of its report noun."""
        the = FX2['ledger']['subjects']['the']
        self.assertEqual(the['subject'], 'The')
        self.assertTrue(all(e['q'].startswith('delivered anchors: Vertical_Drama_OneSheets_2026_10_13_README.txt') for e in the['entries']))
        for s in ('The', 'The Audience', 'Viewers', ''):
            self.assertTrue(IL.nameless_subject(s), s)
        for s in ('You', 'This Is Us', 'Normal', 'Vertical Drama', 'Reba McEntire'):
            self.assertFalse(IL.nameless_subject(s), s)
        wrote = []
        orig_put, orig_upd = IL._put_entry_object, IL._update_index
        IL._put_entry_object = lambda e: wrote.append(('object', e.get('subject')))
        IL._update_index = lambda fn, **k: wrote.append(('index', None))
        try:
            IL._record_entry_now(dict(the['entries'][0], asker=None))
            self.assertEqual(wrote, [])                                     # refused at the last step before the store
            IL._record_entry_now({'subject': 'Vertical Drama', 'q': 'delivered anchors', 'ts': '2026-11-12T05:28:08Z', 'metrics': []})
            self.assertEqual([k for k, _s in wrote], ['object', 'index'])
        finally:
            IL._put_entry_object, IL._update_index = orig_put, orig_upd

    def test_the_note_reader_never_offers_a_leftover_word_as_the_subject(self):
        path = Path(ROOT).parent / 'migration' / 'prometheus_artifact_ingester.py'
        if not path.is_file():
            self.skipTest('the note reader lives in the parent repo')
        os.environ['PROMETHEUS_ARTIFACT_MODEL_SUBJECTS'] = '0'
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('pm_artifact_ingester_r3', str(path))
        ing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ing)
        first = 'The Read / Vertical Drama. Twelve one-sheets for the microdrama newsletter.'
        got = ing._fallback_subject(first, 'Vertical_Drama_OneSheets_2026_10_13')
        self.assertNotEqual(got, 'The')
        self.assertTrue(ing.names_a_subject(got))
        self.assertEqual(ing._fallback_subject('Paige Bueckers - audience value', 'Paige_Bueckers_2026_09_01'), 'Paige Bueckers')
        self.assertFalse(ing.names_a_subject('The'))
        self.assertFalse(ing.names_a_subject('The Read'))


# ============================================================================ 6
class TheSmallThings(unittest.TestCase):
    def test_the_one_or_several_question_draws_its_two_replies_as_chips(self):
        ask = LS.settle('run a profile on this audience:\nNike\nAdidas\nWindow: Jan 1, 2025 to Dec 31, 2025',
                        ['Nike', 'Adidas', 'Window: Jan 1, 2025 to Dec 31, 2025'])['ask']
        self.assertTrue(ask['guidance'] and not ask['success'])
        self.assertEqual(len(ask['followups']), 2)
        self.assertTrue(all(isinstance(f, str) and f for f in ask['followups']))
        # the build surface draws a guidance reply's followups as chips, each sending its own words
        idx = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')
        i = idx.find('function _pmPushGuidanceTurn(text, data) {')
        body = idx[i:i + 2600]
        self.assertTrue(i > 0)
        self.assertIn('Array.isArray(data.followups)', body)
        self.assertIn('_gMeta.options = _fuOpts', body)
        self.assertRegex(idx, r"_pmPushGuidanceTurn\(_gMsg \|\| SYNTH_CHAT_CALM_MSG, data\);")
        # each chip, sent back, is an order the build step reads on its own
        from prometheus import orders as ORD
        self.assertTrue(all(ORD.is_order(f) for f in ask['followups']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
