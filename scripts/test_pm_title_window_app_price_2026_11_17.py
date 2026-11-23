#!/usr/bin/env python3
"""Three leftovers of the same ask (Liz Huszarik, the Prometheus app,
2026-11-17; sibling of test_pm_viewers_count_not_build_2026_11_17.py).

  ATitleKeepsItsName   "FBI Most Wanted viewers" was read as "most" of
                       the "Wanted viewers" and the read in her thread
                       opened "On Wanted viewers:". A quantifier-like
                       word written with a capital, inside the sentence,
                       in a capitalised run in front of the audience
                       noun is the title's own; so is one the library
                       spells that way. In lower case, or only opening
                       the sentence, it is still a quantifier.
  OneWindow            "across November 2025 - 2026", "Nov 2025-Nov
                       2026" and "November 2025 to November 2026" are one
                       window, the start of the first month to the end of
                       the same month a year on; a read ends it on the
                       last full day when that end has not come yet, and
                       says so in its window line. A plain "November
                       2025" is still that one month.
  AppUserPrice         the price helpers read the reader from the
                       browser session alone; a Prometheus app user has
                       none. They fall back to the name the ask door
                       stamped on the request, never to a field of the
                       request body: a covered seat reads "Included with
                       your plan.", a paying seat and a probe are as
                       they were.

Hermetic: pure modules, and the three helpers lifted from the chat
source and run against stubs. No app, no model, no S3, no mail.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
import unittest
import unittest.mock
from datetime import date, datetime, timezone

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines, legacy_lines as _legacy_lines, LEGACY_CEILING as _LEGACY_CEILING  # noqa: E402  (stage 5: chat.py plus its extracted cores; stage 7: the ratchet counts the legacy code)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_nb_for_title_window_price', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

import flask  # noqa: E402

from prometheus import ask_outcome as AO  # noqa: E402
from prometheus import inline_dates as ID  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import question_universe as QU  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import window_scope as WS  # noqa: E402
from prometheus.host import host  # noqa: E402

APP = NB.APP
APP.secret_key = APP.secret_key or 'test-only'

LIZ = ("I need the Total Viewers to FBI Most Wanted and FBI International, All Seasons on Peacock.  "
       "The window is Nov 2025-Nov 2026. Need the viewers for each series reported independently.")
LIZ_FIRST = ("pull the total viewers to FBI Most Wanted and FBI International independently on Peacock, "
             "all seasons across November 2025 - 2026.")
PASTED = ("Brief drafted: FBI Most Wanted Viewers (new build, $300); Brief drafted: FBI International Viewers "
          "(new build, $300)")
TODAY = date(2026, 11, 18)


def _read(name):
    with open(os.path.join(ROOT, *name.split('/')), encoding='utf-8') as fh:
        return fh.read()


def _grab(src, name):
    i = src.index(f"def {name}(")
    j = src.index("\ndef ", i + 1)
    return src[i:j]


def _label(q, **kw):
    return QU.question_universe(q, **kw).label


def _phrases(q, known=None):
    return [p for p, _pop in QU.named_universe(q, known=known)]


# ============================================================================ 1
class ATitleKeepsItsName(unittest.TestCase):
    def test_the_label_that_opened_her_read(self):
        self.assertEqual(_label(PASTED), 'FBI Most Wanted viewers')
        self.assertEqual(_phrases(PASTED), ['FBI Most Wanted Viewers', 'FBI International Viewers'])
        for q in ('What do FBI Most Wanted viewers buy?', 'Demo profile of FBI: Most Wanted viewers on Peacock',
                  'how old are FBI Most Wanted viewers'):
            self.assertNotIn('Wanted viewers', [p for p in _phrases(q)], q)
            self.assertTrue(_label(q).startswith('FBI'), (q, _label(q)))
        self.assertEqual(_label('Demo profile of FBI: Most Wanted viewers on Peacock'), 'FBI: Most Wanted viewers')

    def test_a_capitalised_title_inside_the_sentence_is_whole(self):
        for q, want in (
                ('what do All American viewers also watch', 'All American viewers'),
                ('How old are Most Dangerous Game viewers', 'Most Dangerous Game viewers'),
                ('what share of Everybody Loves Raymond viewers are women', 'Everybody Loves Raymond viewers'),
                ('what do The Last of Us viewers buy', 'The Last of Us viewers'),
                ('The Last of Us viewers skew young', 'The Last of Us viewers'),
                ('how many No Time to Die viewers are there', 'No Time to Die viewers'),
                ('what do NCIS: New York viewers buy', 'NCIS: New York viewers'),
                ('compare Top Gun viewers with Landman viewers', 'Top Gun viewers'),
                ('what do Last Man Standing viewers buy', 'Last Man Standing viewers'),
                ('what do Dexter: New Blood viewers buy', 'Dexter: New Blood viewers'),
                ('what do Best Buy shoppers watch', 'Best Buy shoppers')):
            self.assertEqual(_label(q), want, q)

    def test_a_quantifier_is_still_a_quantifier(self):
        for q, want in (
                ('what do most Landman viewers buy', ['Landman viewers']),
                ('all Peacock viewers', ['Peacock viewers']),
                ('most viewers of Landman', ['viewers of Landman']),
                ('show me the first Yellowstone viewers', ['Yellowstone viewers']),
                ('the top Netflix viewers', ['Netflix viewers']),
                # a capital that only opens the sentence says nothing
                ('Most Landman viewers are women', ['Landman viewers']),
                ('All Peacock viewers skew older', ['Peacock viewers']),
                ('Question: Most Landman viewers are women', ['Landman viewers']),
                # typed with a capital on every word: the capitals say nothing
                ('What Do Most Landman Viewers Buy Online Today', ['Landman Viewers']),
                # a "to" between two names being compared joins nothing
                ('compare Landman to Yellowstone viewers', ['Yellowstone viewers']),
                ('compare Landman to All American viewers', ['All American viewers'])):
            self.assertEqual(_phrases(q), want, q)

    def test_the_library_settles_a_title_that_opens_the_sentence(self):
        held = lambda n: n.lower().startswith(('most dangerous game', 'all american'))   # noqa: E731
        self.assertEqual(_phrases('Most Dangerous Game viewers skew male', known=held), ['Most Dangerous Game viewers'])
        self.assertEqual(_phrases('All American viewers skew young', known=held), ['All American viewers'])
        self.assertEqual(_phrases('Most Landman viewers are women', known=held), ['Landman viewers'])
        self.assertEqual(_phrases('What Do All American Viewers Buy Online Today', known=held), ['All American Viewers'])
        # with no library to ask (a hermetic process), the words alone decide and nothing raises
        self.assertEqual(_phrases('Most Dangerous Game viewers skew male'), ['Dangerous Game viewers'])

    def test_what_was_right_is_untouched(self):
        for q, want in (
                ('what do Costco members buy', 'Costco members'),
                ('compare Gunna fans', 'Gunna fans'),
                ('potential Paw Patrol consumers', 'potential Paw Patrol consumers'),
                ('Potential Paw Patrol Consumers', 'potential Paw Patrol consumers'),
                ('What do Paw Patrol viewers buy', 'Paw Patrol viewers'),
                ('US households', 'US households'),
                ('Chime users who shop at Target', 'Chime users'),
                ('Top 10 brands for Yellowstone fans', 'Yellowstone fans'),
                ('when people start christmas holiday toy shopping', 'US people'),
                ('parents of kids 2 to 6', 'parents of kids 2 to 6')):
            self.assertEqual(_label(q), want, q)
        self.assertEqual(QU.question_universe(LIZ).kind, 'none')            # her own words name no universe; the binder names the titles
        self.assertEqual(_phrases('Peacock US viewers'), [])

    def test_the_binder_keeps_the_title_too(self):
        for q, want in (
                ('What do Most Dangerous Game viewers buy', 'Most Dangerous Game'),
                ('the share of All American viewers who are women', 'All American'),
                ('What do FBI Most Wanted viewers buy?', 'FBI Most Wanted'),
                ('What do Top Gun viewers buy', 'Top Gun'),
                ('How many New Girl viewers are there', 'New Girl'),
                ('Show Most Dangerous Game viewers by age', 'Most Dangerous Game'),
                # quantifiers
                ('Most Landman viewers are women', 'Landman'),
                ('what do most Landman viewers buy', 'Landman'),
                ('All Peacock viewers skew older', 'Peacock'),
                ('Total Yellowstone viewers', 'Yellowstone'),
                # as before
                ('What did Landman viewers watch next', 'Landman'),
                ('What do The Rainmaker viewers buy', 'The Rainmaker')):
            self.assertEqual(SR.sentence_audience(q)[0], want, q)
        self.assertEqual(SR.sentence_audience('All American viewers skew young', casing={'all american': 'All American'})[0],
                         'All American')


# ============================================================================ 2
class OneWindow(unittest.TestCase):
    def _win(self, q):
        w = ID.parse_inline_window(q, today=TODAY)
        return (w['start'], w['end'], w['kind']) if w else None

    def test_the_three_wordings_are_one_window(self):
        want = ('2025-11-01', '2026-11-30', 'range')
        for q in (LIZ_FIRST, LIZ, 'viewers of FBI Most Wanted from November 2025 to November 2026',
                  'FBI International viewers Nov 2025 to 2026', 'total viewers across Nov. 2025 - 2026'):
            self.assertEqual(self._win(q), want, q)
        self.assertEqual(ID.parse_inline_window(LIZ_FIRST, today=TODAY)['phrase'], 'November 2025 - 2026')
        self.assertFalse(ID.parse_inline_window(LIZ_FIRST, today=TODAY)['coarse'])

    def test_a_plain_month_is_still_that_month(self):
        for q in ('viewers of FBI Most Wanted in November 2025', 'November 2025', 'total viewers for November 2025 on Peacock',
                  'viewers November 2025 - 2025'):
            self.assertEqual(self._win(q), ('2025-11-01', '2025-11-30', 'month'), q)
        self.assertTrue(ID.parse_inline_window('November 2025', today=TODAY)['coarse'])

    def test_on_a_service_is_not_an_open_end(self):
        # "November 2025 on Peacock" read as November 2025 through yesterday: the bare "on" was taken for "onward"
        self.assertEqual(self._win('viewers in November 2025 on Netflix'), ('2025-11-01', '2025-11-30', 'month'))
        self.assertEqual(self._win('viewers for Q4 2025 on Hulu'), ('2025-10-01', '2025-12-31', 'quarter'))
        for q in ('from March 2025 on', 'viewers from March 2025 on, by month', 'March 2025 onward', 'March 2025 onwards on Hulu',
                  'since March 2025'):
            self.assertEqual(self._win(q), ('2025-03-01', '2026-11-17', 'since'), q)

    def test_the_shapes_around_it_read_as_before(self):
        self.assertEqual(self._win('viewers 2024 to 2026'), ('2024-01-01', '2026-12-31', 'year_range'))
        self.assertEqual(self._win('viewers Jan 2025 to Mar 2025'), ('2025-01-01', '2025-03-31', 'range'))
        self.assertEqual(self._win('viewers from 2023 to March 2024'), ('2024-03-01', '2024-03-31', 'month'))   # a year first is not this shape
        self.assertIsNone(self._win('the 2025-2026 season'))
        self.assertEqual(self._win('from January 19 and February 15, 2024'), ('2024-01-19', '2024-02-15', 'range'))
        # the same month, or the same day, of the later year
        self.assertEqual(self._win('viewers January 2024 - 2026'), ('2024-01-01', '2026-01-31', 'range'))
        self.assertEqual(self._win('viewers Nov 15, 2025 - 2026'), ('2025-11-15', '2026-11-15', 'range'))

    def test_a_read_ends_it_on_the_last_full_day(self):
        for q in (LIZ_FIRST, LIZ, 'viewers of FBI Most Wanted from November 2025 to November 2026'):
            wins = WS.read_windows(q, today=TODAY)
            self.assertEqual([(w['start'], w['end'], w['open']) for w in wins], [('2025-11-01', '2026-11-17', True)], q)
            note = WS.read_window_note(q, today=TODAY)
            self.assertIn('Window 1: 2025-11-01 to 2026-11-17 (Nov 1 2025 to Nov 17 2026), open end', note)
            self.assertIn('never today, never a later date', note)
            self.assertIn('Use exactly these dates in every label and line.', note)
            self.assertNotIn("('')", note)                                  # no empty quote where an open phrase would sit

    def test_the_finished_read_states_that_window(self):
        res = {'window_start': '2025-11-01', 'window_end': '2026-11-30', 'window_label': 'Nov 1 2025 to Nov 30 2026',
               'headline': 'FBI: Most Wanted drew viewers through Nov 30, 2026',
               'metrics': [{'label': 'Total US viewers', 'definition': 'seasons 1 to 6, Nov 1 2025 to Nov 30 2026'}]}
        out = WS.hold_read_window(res, LIZ, today=TODAY)
        self.assertEqual((out['window_start'], out['window_end']), ('2025-11-01', '2026-11-17'))
        self.assertEqual(out['window_label'], 'Nov 1 2025 to Nov 17 2026')  # the window line echoes what was read
        self.assertIn('Nov 17, 2026', out['headline'])
        self.assertIn('Nov 17 2026', out['metrics'][0]['definition'])
        self.assertNotEqual((out['window_start'], out['window_end']), WS.standing_default(TODAY))   # not the default: it is stated

    def test_a_window_that_has_ended_is_left_to_the_read(self):
        self.assertEqual(WS.read_windows('viewers from Nov 2024 to Nov 2025', today=TODAY), [])
        self.assertEqual(WS.read_windows('viewers in November 2025', today=TODAY), [])
        self.assertEqual(WS.read_windows(LIZ, today=date(2027, 1, 10)), [])   # asked once the range has closed
        self.assertEqual(WS.read_window_note('viewers from Nov 2024 to Nov 2025', today=TODAY), '')
        # an open end still reads as it did
        wins = WS.read_windows('compare 2025 vs 2026 to date', today=TODAY)
        self.assertEqual([(w['start'], w['end'], w['open']) for w in wins],
                         [('2025-01-01', '2025-12-31', False), ('2026-01-01', '2026-11-17', True)])

    def test_the_offer_counts_it_as_one_year(self):
        src = _read('prometheus/legacy/watch.py')
        ns = {'re': re, 'datetime': datetime, 'timezone': timezone}
        a = src.index('_PM_YEAR_WORDS = {')
        exec(src[a:src.index('\n\n', a)], ns)
        exec(_grab(src, '_pm_window_years'), ns)
        years = ns['_pm_window_years']
        self.assertEqual(years(LIZ_FIRST), 1)
        self.assertEqual(years(LIZ), 1)
        self.assertEqual(years('viewers Nov 2025 to 2026'), 1)
        self.assertEqual(years('viewers 2025 - 2026'), 2)                    # two calendar years is still two
        self.assertEqual(years('viewers 2023 to 2025'), 3)
        self.assertEqual(years('January 2024 - 2026 trend'), 3)

    def test_a_build_keeps_its_own_rule_for_a_future_end(self):
        app = _read('app.py')
        bind = _grab(app, '_bind_inline_date_window')
        self.assertIn('_idw.parse_inline_window(', bind)                    # a build binds the same reading
        guard = _grab(app, '_guard_future_window')
        self.assertIn('(through today)', guard)                             # and pulls a future end back to today, with the echo
        self.assertIn('window runs through today', guard)


# ============================================================================ 3
STAFF = {'username': 'liz', 'email': 'liz@crosswalknyc.com', 'company': 'Crosswalk', 'role': 'super_admin', 'credits': -1}
PAYER = {'username': 'payer', 'email': 'pat@example.com', 'company': 'Acme Studios', 'role': 'user', 'credits': 12}
USERS = {'users': {'liz': STAFF, 'payer': PAYER}}
CHAT = _legacy_view()


def _helpers():
    """The three helpers as the chat module defines them, against stubs."""
    ns = {'session': flask.session,
          '_H': types.SimpleNamespace(load_users=lambda: USERS),
          '_faults': types.SimpleNamespace(note=lambda *a, **k: 0),
          '_pm_usd_label': lambda v, u=None: f"${float(v):,.0f}"}
    for name in ('_pm_price_sentence', '_pm_included_seat', '_pm_tool_price_label'):
        exec(_grab(CHAT, name), ns)
    return ns


class AppUserPrice(unittest.TestCase):
    def setUp(self):
        NB.World()
        host.bind(load_users=lambda: USERS,
                  check_user_credits=lambda u: (True, int((USERS['users'].get(u) or {}).get('credits', 0) or 0)))
        self.h = _helpers()
        import wallet
        self._p = unittest.mock.patch.object(wallet, 'tool_price_usd', lambda key, subject=None: 500.0)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def _call(self, *, door=None, key_owner=None, session_user=None, body=None, username=None):
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST', json=(body or {})):
            if door:
                flask.g._pm_ask_user = door
            if key_owner:
                flask.g._pm_api_key_owner = key_owner
            if session_user:
                flask.session['username'] = session_user
            return (self.h['_pm_price_sentence']('journey_iq', '$500', username),
                    self.h['_pm_included_seat'](username),
                    self.h['_pm_tool_price_label']('journey_iq', '$500', username))

    def test_a_covered_app_user_reads_the_included_line(self):
        for kw in ({'door': 'liz'}, {'key_owner': 'liz'}):
            sentence, included, _label_ = self._call(**kw)                   # no browser session at all
            self.assertEqual(sentence, PR.INCLUDED_LINE, kw)
            self.assertIs(included, True, kw)
            self.assertNotIn('$', sentence)

    def test_a_paying_app_user_reads_the_price(self):
        sentence, included, label = self._call(door='payer')
        self.assertEqual(sentence, 'It prices at $500.')
        self.assertIs(included, False)
        self.assertEqual(label, '$500')

    def test_a_probe_reads_the_paying_shape(self):
        for who in ('canary:golden', 'canary:smoke'):
            sentence, included, _l = self._call(door=who)
            self.assertEqual(sentence, 'It prices at $500.', who)
            self.assertIs(included, False, who)

    def test_the_request_body_names_nobody(self):
        body = {'username': 'liz', 'user': 'liz', 'email': 'liz@crosswalknyc.com', 'bind_user': 'liz',
                'extra': {'username': 'liz'}, 'context': {'user': 'liz'}}
        sentence, included, _l = self._call(body=body)                       # the door stamped nobody
        self.assertEqual(sentence, 'It prices at $500.')
        self.assertIs(included, False)
        sentence, included, _l = self._call(door='payer', body=body)         # the door's name stands, whatever the body says
        self.assertEqual(sentence, 'It prices at $500.')
        self.assertIs(included, False)
        for name in ('_pm_price_sentence', '_pm_included_seat', '_pm_tool_price_label'):
            src = _grab(CHAT, name)
            for banned in ('request.', 'get_json', 'body.get', 'request_body'):
                self.assertNotIn(banned, src, (name, banned))

    def test_the_session_and_a_named_user_read_as_before(self):
        self.assertEqual(self._call(session_user='liz')[0], PR.INCLUDED_LINE)
        self.assertEqual(self._call(session_user='payer')[0], 'It prices at $500.')
        self.assertEqual(self._call(session_user='payer', door='payer')[0], 'It prices at $500.')
        self.assertEqual(self._call(username='liz')[0], PR.INCLUDED_LINE)    # a caller that names the reader (a job) is believed first
        self.assertEqual(self._call(username='payer', door='liz')[0], 'It prices at $500.')

    def test_the_order_is_named_then_session_then_door(self):
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST', json={'username': 'liz'}):
            self.assertEqual(AO.seat_username(), '')                         # nobody: the body's word is not a name
            flask.g._pm_ask_user = 'liz'
            self.assertEqual(AO.seat_username(), 'liz')
            flask.session['username'] = 'payer'
            self.assertEqual(AO.seat_username(), 'payer')
            self.assertEqual(AO.seat_username('someone'), 'someone')

    def test_outside_a_request_nothing_is_guessed(self):
        self.assertEqual(AO.door_username(), '')
        self.assertEqual(AO.seat_username(), '')
        self.assertEqual(self.h['_pm_price_sentence']('journey_iq', '$500'), 'It prices at $500.')
        self.assertIs(self.h['_pm_included_seat'](), False)

    def test_the_door_name_is_the_one_the_gate_stamped(self):
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST', json={'username': 'liz'}):
            self.assertEqual(AO.door_username(), '')
            flask.g._pm_ask_user = 'unknown'
            self.assertEqual(AO.door_username(), '')
            flask.g._pm_ask_user = 'anon'
            self.assertEqual(AO.door_username(), '')
            flask.g._pm_api_key_owner = 'liz'
            self.assertEqual(AO.door_username(), 'liz')
            flask.g._pm_ask_user = 'payer'
            self.assertEqual(AO.door_username(), 'payer')
        bp = _read('prometheus/blueprint.py')
        self.assertIn('g._pm_ask_user = service.username_of(user)', bp)      # set from the gated user, session or key
        self.assertIn('user, err = host.gate(allow_api_key=True)', bp)

    def test_it_is_the_one_shared_check(self):
        for name in ('_pm_price_sentence', '_pm_tool_price_label'):
            self.assertIn("fromlist=['seat_username']).seat_username(username)", _grab(CHAT, name), name)
            self.assertNotIn("session.get('username')", _grab(CHAT, name), name)   # one resolver, not a copy of the session read
        self.assertLess(_legacy_lines(), _LEGACY_CEILING)                              # the file's line ratchet holds
        self.assertIn('_pr.included_with_plan(user, uname, _H)', _grab(CHAT, '_pm_price_sentence'))
        calls = []
        real = PR.included_with_plan
        with unittest.mock.patch.object(PR, 'included_with_plan',
                                        lambda user, username=None, host=None, amount_usd=None: (calls.append(username), real(user, username, None))[1]):
            self._call(door='liz')
        self.assertIn('liz', calls)


if __name__ == '__main__':
    unittest.main(verbosity=1)
