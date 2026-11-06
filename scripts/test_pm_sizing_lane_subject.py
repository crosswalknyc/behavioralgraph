#!/usr/bin/env python3
"""The sizing lane binds to the open page only when the words point at it.

Jenna, 2026-11-05, with the Howie Mandel profile open: "how many power
raising kanan season 5 viewers autoplay'ed into fightland  and then of
those what percent completed that entire season of fightland" came back
as a sizing of Howie Mandel ("About 3.9M people in the US ... Sized from
the profile on the dashboard") with a chip to build a profile on him.
Two faults: "how many ... viewers" tripped the sizing lane's how-many
trigger, and the word "that" (in "that entire season") bound the open
profile as the subject. Jenna: "this is not right either".

The rule: a sizing binds to the open profile only when the question
names no other subject (a pronoun, a bare "how many people are in this
audience", "how big is it") or names the open subject itself. A question
that names titles and the steps between them (autoplayed into, then
watched, proceeded to watch, completed the season, of those ...) is a
content-journey question: its subject comes from the words and it goes
to the reasoned-metrics content-journey path through the work gate's
offer, never to a sizing. And a sizing reply never ships when the words
are about a subject other than the one being sized. Hermetic.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')


class _NoAppModule:
    """A meta-path finder that refuses a fresh ``import app`` for this
    process. The priced read's seat lookup (``priced_read.live_user``)
    falls back to the app module when the host binds no ``load_users``,
    which is the case on the bare host the Routing class calls
    ``work_gate`` against and was the case for the stub host below. On a
    worker with the full dependency set that import succeeds and app.py's
    tail runs ``prometheus.init_app``, which binds the production host
    (the live read registry, users.json, the thread store, the model)
    into this process; a later ``host.bind`` merges over it and the
    production ``recent_read`` stays. Jenna's question then replayed a
    landed read ('read_replay') instead of leaving as the offer. Here the
    import fails the way it does on a machine without the webapp's
    dependencies. A module already in sys.modules is never consulted."""

    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import priced_read as P    # noqa: E402
from prometheus import sizing_lane as SZ   # noqa: E402

JENNA = ("how many power raising kanan season 5 viewers autoplay'ed into fightland  and then of those "
         "what percent completed that entire season of fightland")
SWAT = "what percentage of people who watched fightland on starz then proceeded to watch swat exiles directly after"
CARRIE = "how many people watched Carrie on Prime Video"
WALTER = "how many people who read the walter boys on wattpad watched the series on netflix"
PAGE = {'view': 'profile-iq', 'primary': {'name': 'Howie Mandel', 's3_key': 'Howie_Mandel_06_09_2026_12_11.csv'}}
CHAINS = (
    JENNA,
    "how many fightland viewers then watched swat exiles",
    "of the people who watched the pilot, how many binged the whole season",
    "how many Landman viewers went back and watched Yellowstone",
    "what share of Tulsa King viewers converted into Landman viewers",
    "how many season 1 viewers finished the season of Fightland",
)


class Trigger(unittest.TestCase):
    def test_jennas_question_is_a_viewing_chain_never_a_sizing(self):
        self.assertTrue(SZ.is_viewing_chain_ask(JENNA))
        self.assertFalse(SZ.wants_sizing(JENNA))
        self.assertFalse(P.sizes_open_page(JENNA, PAGE))
        self.assertFalse(P._panel_self(JENNA, PAGE))

    def test_the_subject_is_the_titles_in_the_words_not_the_page(self):
        self.assertEqual(SZ.subjects(JENNA, PAGE), [])
        self.assertFalse(SZ.points_at_page(JENNA))
        titles = SZ.chain_titles(JENNA)
        self.assertEqual(len(titles), 2, titles)
        self.assertIn('raising kanan', titles[0].lower())
        self.assertIn('season 5', titles[0].lower())
        self.assertEqual(titles[1].lower(), 'fightland')
        bound = P.bound_subject(JENNA, [], PAGE, fallback='')
        self.assertIn('Raising Kanan', bound)
        self.assertNotIn('Howie', bound)

    def test_every_chain_shape_is_a_chain(self):
        for t in CHAINS:
            self.assertTrue(SZ.is_viewing_chain_ask(t), t)
            self.assertFalse(SZ.wants_sizing(t), t)
            self.assertNotIn('Howie Mandel', SZ.subjects(t, PAGE), t)

    def test_plain_sizing_asks_still_size(self):
        for t in ('how big is the FanDuel audience', 'what is the sample size for Godzilla Minus One',
                  'i need the size of two data sets, first FanDuel cuatomers, the second Kalshi Cutomers',
                  'how many people are in the Ordeal readers audience'):
            self.assertFalse(SZ.is_viewing_chain_ask(t), t)
            self.assertTrue(SZ.wants_sizing(t), t)


class PageBinding(unittest.TestCase):
    def test_a_bare_ask_sizes_the_open_profile(self):
        for t in ('how many people are in this audience', 'how big is it', 'how big is this one',
                  "what's the size of this audience", 'size this'):
            self.assertTrue(SZ.wants_sizing(t), t)
            self.assertEqual(SZ.subjects(t, PAGE), ['Howie Mandel'], t)
            self.assertTrue(P.sizes_open_page(t, PAGE), t)
            self.assertIsNone(P.classify(t, [], PAGE), t)     # a figure on screen: the free lookup, never a quote

    def test_naming_the_open_subject_sizes_it(self):
        t = "how big is Howie Mandel's audience"
        self.assertTrue(SZ.wants_sizing(t))
        self.assertEqual(SZ.subjects(t, PAGE), ['Howie Mandel'])
        self.assertTrue(P.sizes_open_page(t, PAGE))
        self.assertEqual(SZ.subjects('how many people are in the Howie Mandel audience', PAGE), ['Howie Mandel'])

    def test_a_pronoun_before_a_noun_is_not_a_pointer_at_the_page(self):
        for t in (JENNA, 'how big is that season of Fightland', 'how many people watched that show'):
            self.assertFalse(SZ.points_at_page(t), t)
            self.assertNotIn('Howie Mandel', SZ.subjects(t, PAGE), t)
        self.assertEqual(SZ.subjects('how big is the FanDuel audience', PAGE), ['FanDuel'])

    def test_no_page_means_no_page(self):
        self.assertEqual(SZ.subjects('how big is it', {}), [])
        self.assertEqual(SZ.subjects(JENNA, {}), [])


class OutputGuard(unittest.TestCase):
    def test_a_sizing_reply_never_ships_on_another_subject(self):
        self.assertTrue(SZ.names_other_subject(JENNA, ['Howie Mandel'], PAGE))
        self.assertTrue(SZ.names_other_subject(SWAT, ['Howie Mandel'], PAGE))
        self.assertIsNone(SZ.answer(JENNA, PAGE, subjects=['Howie Mandel']))
        self.assertIsNone(SZ.answer(JENNA, PAGE))
        self.assertIsNone(SZ.answer(JENNA, PAGE, subjects=['Power Raising Kanan Season 5']))   # a chain is never a size

    def test_the_guard_leaves_the_named_subject_alone(self):
        self.assertFalse(SZ.names_other_subject('how big is the FanDuel audience', ['FanDuel'], PAGE))
        self.assertFalse(SZ.names_other_subject("how big is Howie Mandel's audience", ['Howie Mandel'], PAGE))
        self.assertFalse(SZ.names_other_subject('how many people are in this audience', ['Howie Mandel'], PAGE))
        self.assertFalse(SZ.names_other_subject('i need the size of two data sets, first FanDuel cuatomers, the second Kalshi Cutomers',
                                                ['FanDuel cuatomers', 'Kalshi Cutomers'], PAGE))


class Routing(unittest.TestCase):
    def test_jennas_question_is_the_content_journey_custom_ask(self):
        hit = P.classify(JENNA, [], PAGE)
        self.assertEqual((hit['kind'], hit['via'], hit['usd']), ('custom', 'generate', 100))
        out = P.work_gate(JENNA, [], PAGE, user={'username': 'admin'}, body={}, username='admin')
        self.assertTrue(out and out.get('panel_offer'), out)
        self.assertIn('Raising Kanan', out['reply'])
        self.assertNotIn('Howie Mandel', out['reply'])
        self.assertIn('$100', out['reply'])

    def test_the_other_asks_route_as_before(self):
        self.assertEqual(P.classify(SWAT, [], PAGE)['kind'], 'custom')
        out = P.work_gate(SWAT, [], PAGE, user={'username': 'admin'}, body={}, username='admin')
        self.assertTrue(out and out.get('panel_offer'))
        self.assertIn('Fightland', out['reply'])
        self.assertNotIn('Howie Mandel', out['reply'])
        self.assertEqual(P.classify(CARRIE, [], PAGE)['kind'], 'viewership')
        self.assertEqual(P.classify(WALTER, [], PAGE)['kind'], 'viewership')
        for t in (SWAT, CARRIE, WALTER):
            self.assertIsNone(SZ.answer(t, PAGE), t)

    def test_a_bare_page_sizing_is_still_the_lookup(self):
        self.assertIsNone(P.work_gate('how many people are in this audience', [], PAGE,
                                      user={'username': 'admin'}, body={}, username='admin'))
        self.assertTrue(P.custom_work(JENNA))
        self.assertFalse(P.custom_work('how many people are in this audience'))
        self.assertFalse(P.custom_work("how big is Howie Mandel's audience"))


class LedgerReplay(unittest.TestCase):
    """The banked Fightland into S.W.A.T. Exiles read (thread d438bcf8a8,
    21:19Z) was replayed for Jenna's Raising Kanan into Fightland question
    when it ran fresh through the lane: the topic words overlapped and the
    base title was nowhere in the stored read. A chain between other
    titles is another read."""

    ENTRY = {
        'subject': 'Fightland', 'k': 'fightland|engagement|2026-09-25|2026-10-29', 'family': 'engagement',
        'q': 'what percentage of people who watched fightland on starz then proceeded to watch swat exiles directly after because it was auto played?',
        'qn': 'what percentage of people who watched fightland on starz then proceeded to watch swat exiles directly after because it was auto played',
        'cohort': '', 'ws': '2026-09-25', 'we': '2026-10-29', 'wl': 'Jul 31 2026 to Oct 29 2026, handoff window Sep 25 to Oct 29 2026',
        'route': 'reasoned_metrics', 'ts': '2026-11-05T21:19:09Z',
        'reply': ('On people who watched a Fightland title on Starz in the window:\n\n8.8% of Fightland viewers who were still '
                  'playing episodes once S.W.A.T. Exiles was live rolled straight into it on the end card autoplay, 73,118 people.\n'
                  '- Fightland US viewers on Starz: 1,418,263\n- Autoplay handoff into S.W.A.T. Exiles: 73,118\n'
                  '- Second screen on S.W.A.T. Exiles runs 61.1% and completion runs 64.8%.'),
    }

    def test_a_chain_between_other_titles_never_replays(self):
        import insights_ledger as il
        self.assertIsNone(il.find_semantic([self.ENTRY], JENNA))
        self.assertIsNone(il.find_exact([self.ENTRY], question=JENNA, key=self.ENTRY['k']))
        self.assertFalse(il._chain_titles_ok(self.ENTRY, JENNA))

    def test_the_same_chain_still_replays(self):
        import insights_ledger as il
        self.assertIs(il.find_exact([self.ENTRY], question=self.ENTRY['q'], key=self.ENTRY['k']), self.ENTRY)
        self.assertIs(il.find_semantic([self.ENTRY], 'what share of fightland viewers on starz autoplayed into swat exiles'), self.ENTRY)
        self.assertTrue(il._chain_titles_ok(self.ENTRY, SWAT))
        self.assertTrue(il._chain_titles_ok(self.ENTRY, 'how big is the Fightland audience'))   # no chain: the gate stays open


class _NoS3:
    """An S3 client with no credentials: every call raises, which is what
    the hermetic runner's environment does. Injected everywhere a module
    builds its own client, so the case reads the same on a machine that
    holds AWS credentials as on one that does not."""

    def __getattr__(self, name):
        def _call(*a, **k):
            from botocore.exceptions import NoCredentialsError
            raise NoCredentialsError()
        return _call


class ServiceEndToEnd(unittest.TestCase):
    """service.ask with a stub host: Jenna's question leaves through the
    work gate's content-journey offer, not through either sizing lane.

    Hermetic by construction (2026-11-05: the case flickered once on a
    live registry hit). ``host.bind`` merges, so the class starts from an
    empty host and restores whatever the process had bound before. Every
    seam that reaches outside the process is pinned: the seat lookup gets
    a ``load_users`` and the registry a ``recent_read`` on the host, the
    sandbox-owner cache never reads users.json, the insights ledger and
    the corpus catalog hold a client that has no credentials, any other
    boto3 client is that same client, a fresh ``import app`` is refused
    (module level, see _NoAppModule), and no socket connects."""

    @classmethod
    def setUpClass(cls):
        import socket
        import boto3
        import insights_ledger as _il
        from migration import corpus_catalog as _cc
        from prometheus import probe_identity as _pid
        from prometheus.host import host
        cls.calls = []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text))
            return {'success': True, 'reply': 'core', 'followups': []}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        # the host is exactly this class's stubs: nothing an earlier suite
        # in the process bound (a production recent_read, a real s3_client)
        # rides along, and it all goes back in tearDownClass
        cls._host_before = dict(host._b)
        cls._host_bound_before = host.bound
        host._b.clear()
        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: None, gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True,
                  claude_data=lambda *a, **k: {},
                  # the billed seat, from the host and never from an `import app`
                  load_users=lambda: {'users': {'admin': {'username': 'admin', 'role': 'user'}}},
                  # the caller's read registry: nothing landed, nothing running
                  recent_read=lambda u, t: None)
        # the sandbox-owner lookup (turn_gate -> probe_identity) never reads users.json
        _pid.set_sandbox_owners(())
        # the insights ledger (and prometheus_memory, which shares its client)
        # and the corpus catalog hold a credential-less client and no cached index
        cls._il_before = (_il._s3, dict(_il._index_cache))
        _il._s3 = _NoS3()
        _il._index_cache.update({'ts': 0.0, 'doc': None})
        cls._cc_before = dict(_cc._state)
        _cc.set_client(_NoS3())
        # any other module that builds its own client gets the same one
        cls._boto_before = (boto3.client, boto3.resource, boto3.session.Session.client)
        boto3.client = lambda *a, **k: _NoS3()
        boto3.resource = lambda *a, **k: _NoS3()
        boto3.session.Session.client = lambda self, *a, **k: _NoS3()
        # no socket connects: a reach that got past every stub above fails
        # here the way it does with no network
        cls._socket_before = (socket.socket.connect, socket.create_connection, socket.getaddrinfo)

        def _blocked(*a, **k):
            raise OSError('no network inside this hermetic case')

        socket.socket.connect = _blocked
        socket.create_connection = _blocked
        socket.getaddrinfo = _blocked
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'admin'}

    @classmethod
    def tearDownClass(cls):
        import socket
        import boto3
        import insights_ledger as _il
        from migration import corpus_catalog as _cc
        from prometheus.host import host
        socket.socket.connect, socket.create_connection, socket.getaddrinfo = cls._socket_before
        boto3.client, boto3.resource, boto3.session.Session.client = cls._boto_before
        _cc._state.clear()
        _cc._state.update(cls._cc_before)
        _il._s3, _cache = cls._il_before
        _il._index_cache.update(_cache)
        host._b.clear()
        host._b.update(cls._host_before)
        host.bound = cls._host_bound_before

    def _ask(self, text):
        self.calls.clear()
        env, st = self.service.ask(self.user, {'text': text, 'surface': 'analyze', 'client': 'dashboard',
                                               'history': [], 'page_context': PAGE})
        return env, st

    def test_jennas_question_leaves_as_the_content_journey_offer(self):
        env, st = self._ask(JENNA)
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'custom_ask_offer')
        raw = env['raw']
        self.assertTrue(raw.get('panel_offer'))
        self.assertIn('Raising Kanan', raw['reply'])
        self.assertNotIn('Howie Mandel', raw['reply'])
        self.assertNotIn('Sized from the profile', raw['reply'])
        self.assertEqual(self.calls, [])

    def test_a_bare_page_sizing_never_reaches_the_offer(self):
        env, st = self._ask('how many people are in this audience')
        self.assertEqual(st, 200)
        self.assertNotEqual(env['decision']['reason'], 'custom_ask_offer')
        self.assertNotIn('Raising Kanan', str(env['raw'].get('reply') or ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
