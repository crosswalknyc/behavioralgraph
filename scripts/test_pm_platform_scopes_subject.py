#!/usr/bin/env python3
"""A platform named in a question scopes the subject; it does not become
the subject (2026-11-11, Armine Meseldzhyan at Lionsgate).

Armine held a read on digital original content categories. In a new chat
she wrote: "Let's pull performance data on YouTube original content
across five categories: Comedy, Lifestyle/Arts & Culture, Faith, Young
Adult, and Horror?" The base resolved to the YouTube profile, every
category was sized against everyone who uses YouTube (98,443,018 for
Comedy against the 4.7M on record), the ledger was asked for the subject
`youtube` and never saw the read on record, and a later pass carried the
all-platform shares into a YouTube-only table.

Holds, from her exact words:
  1. the reading: YouTube is where, not who, and a question about the
     platform's own audience still names the platform;
  2. base selection: the catalog names the YouTube profile, and the read
     lane's hook releases it for the subject on record, scoped to
     YouTube; a chip that chose YouTube and a question about YouTube's
     own audience keep the profile;
  3. the ledger: a lookup that resolved to `youtube` moves to the
     subject on record (the YouTube-only read first), so the held
     figures bound the answer; with only an all-platform read on record
     it rides as context and never replays as the YouTube answer;
  4. binding: a share on record binds a table on the same scope only;
  5. the thread: a follow-up that only adds a platform scopes the
     thread's subject; a question about people at large does not.

Hermetic: no network, no model, no S3.
"""
from __future__ import annotations

import os
import re
import sys
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

FAILS = []


def check(msg, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f"  [{detail}]" if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())


def _norm(s):
    s = re.sub(r"[^a-z0-9\s]+", " ", str(s or '').lower())
    return re.sub(r"\s+", " ", s).strip()


class _FakeHost:
    _normalize_for_match = staticmethod(_norm)
    S3_BUCKET = 'dashboard-inputs'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(_FakeHost())
from prometheus.legacy import chat as C  # noqa: E402
from prometheus import figure_binding as FB  # noqa: E402
from prometheus import platform_scope as PS  # noqa: E402
import insights_ledger as il  # noqa: E402

# her words, from thread 246092bbbb
ARMINE = ("Let's pull performance data on YouTube original content across five categories: Comedy, "
          "Lifestyle/Arts & Culture, Faith, Young Adult, and Horror?\n\nFor each category include audience size, "
          "viewership, examples, and engagement.")
ARMINE_2 = "Can we do all big original content categories on YouTube"
YT_KEY = 'YouTube_06_16_2026_16_53.csv'
CATALOG = [
    {'s3_key': YT_KEY, 'display_name': 'YouTube', 'subject': 'YouTube', 'category': 'SOCIAL MEDIA'},
    {'s3_key': 'TikTok_06_16_2026_02_53.csv', 'display_name': 'TikTok', 'subject': 'TikTok', 'category': 'SOCIAL MEDIA'},
    {'s3_key': 'Mad_Men_Viewers_11_09_2026.csv', 'display_name': 'Mad Men Viewers', 'subject': 'Mad Men Viewers', 'category': 'SERIES'},
]
C._profile_catalog_for_chat = lambda: CATALOG

ALL_SUBJ = 'Digital original content categories (Comedy, Lifestyle/Arts & Culture, Faith, Young Adult, Horror)'
YT_SUBJ = ('Digital original content categories on YouTube (Comedy, Lifestyle/Arts & Culture, Drama, Horror, '
           'Young Adult, Faith)')


def _entry(subject, q, metrics, rows, dim, basis, ts, universe='', cohort='', reply='x', route='reasoned_metrics'):
    return {'k': il.entry_key(subject, 'audience', '2025-10-21', '2026-10-21'), 'subject': subject, 'family': 'audience',
            'q': q, 'qn': il.normalize_question(q), 'route': route, 'ts': ts, 'ws': '2025-10-21', 'we': '2026-10-21',
            'wl': 'Oct 21 2025 to Oct 21 2026', 'metrics': metrics, 'anchors': [], 'base': '', 'cohort': cohort,
            'universe': universe, 'breakdown': {'dimension': dim, 'share_basis': basis, 'rows': rows}, 'reply': reply,
            'followups': [], 'derivation': '', 'prov': 'operator'}


ALL_ROWS = [{'label': 'Comedy', 'share_pct': 32.6}, {'label': 'Lifestyle / Arts & Culture', 'share_pct': 25.4},
            {'label': 'Horror', 'share_pct': 16.9}, {'label': 'Young Adult', 'share_pct': 14.2}, {'label': 'Faith', 'share_pct': 10.9}]
YT_ROWS = [{'label': 'Comedy', 'share_pct': 28.2308}, {'label': 'Lifestyle / Arts & Culture', 'share_pct': 22.2336},
           {'label': 'Drama', 'share_pct': 15.6}, {'label': 'Horror', 'share_pct': 13.6035}, {'label': 'Faith', 'share_pct': 10.3854},
           {'label': 'Young Adult', 'share_pct': 9.9466}]
E_ALL = _entry(ALL_SUBJ, "Looking at the digital original content such as YouTube, TikTok, etc. What type of categories do best "
               "and how is the audience like.",
               [{'name': 'five_category_unduplicated_audience', 'value': 14862537, 'unit': 'viewers'},
                {'name': 'comedy_digital_original_audience', 'value': 6579974, 'unit': 'viewers'}],
               ALL_ROWS, 'Digital original content category', 'share of the five-category digital original viewing signals in the window',
               '2026-11-11T20:16:31Z', universe=ALL_SUBJ)
E_YT = _entry(YT_SUBJ, "For digital original content on YouTube only, Oct 21 2025 to Oct 21 2026: add Drama as a sixth category",
              [{'name': 'comedy_youtube_audience', 'value': 4713882, 'unit': 'viewers'}],
              YT_ROWS, 'Digital original category, YouTube only', 'share of YouTube digital original viewing across the six categories',
              '2026-11-11T21:38:19Z', universe=YT_SUBJ, cohort='digital original content viewers')
E_PLAT = _entry('YouTube', 'who uses youtube', [{'name': 'us_unique_users', 'value': 247184763, 'unit': 'count'}], [], '', '',
                '2026-09-15T01:21:02Z', route='delivered_deck')
E_OTHER = _entry('Mad Men Viewers', 'how big is the mad men audience', [{'name': 'aud', 'value': 5231877, 'unit': 'viewers'}], [], '', '',
                 '2026-11-09T01:00:00Z')


def index(*pairs):
    doc = {'subjects': {}, 'updated': '2026-11-11T22:00:00Z'}
    for key, subject, entries in pairs:
        doc['subjects'][key] = {'subject': subject, 'entries': list(entries)}
    return doc


FULL = index(('youtube', 'YouTube', [E_PLAT]),
             ('digital_original_content_categories_comedy_lifestyle', ALL_SUBJ, [E_ALL]),
             ('digital_original_content_categories_on_youtube', YT_SUBJ, [E_YT]),
             ('mad_men_viewers', 'Mad Men Viewers', [E_OTHER]))
ALL_ONLY = index(('youtube', 'YouTube', [E_PLAT]),
                 ('digital_original_content_categories_comedy_lifestyle', ALL_SUBJ, [E_ALL]),
                 ('mad_men_viewers', 'Mad Men Viewers', [E_OTHER]))


def use(doc):
    with il._cache_lock:
        il._index_cache['doc'] = doc
        il._index_cache['ts'] = time.time() + 3600


print("[1] the reading")
check("her words name YouTube as where", PS.named(ARMINE).get('YouTube') == 'scope', str(PS.named(ARMINE)))
check("the follow-up too", PS.named(ARMINE_2).get('YouTube') == 'scope')
for t in ('who uses YouTube', 'what do YouTube users buy', 'the YouTube audience by age', 'how big is the audience of YouTube',
          'profile of YouTube viewers', 'people on YouTube who shop at Target'):
    check(f"{t!r}: the platform is who", PS.named(t).get('YouTube') == 'subject', str(PS.named(t)))
check("platforms listed as examples are no scope",
      PS.scope_of("Looking at the digital original content such as YouTube, TikTok, etc. What type of categories do best") == frozenset())
check("'YouTube only' and 'on YouTube' are a scope",
      PS.scope_of('Digital original category, YouTube only') == {'YouTube'} and PS.scope_of(YT_SUBJ) == {'YouTube'})
check("an all-platform table has none", PS.scope_of(ALL_SUBJ, 'Digital original content category') == frozenset())
check("a platform inside a longer product name is that product, not a place",
      PS.named("signups that started through Amazon Prime Video Channels versus direct").get('Prime Video') != 'scope'
      and PS.named("what do people watch on YouTube TV").get('YouTube') != 'scope'
      and PS.named("signups on Prime Video in October").get('Prime Video') == 'scope')
check("'max' alone is a word, 'on Max' is the service",
      'HBO Max' not in PS.named('the max share of comedy') and PS.named('viewers of Hacks on Max').get('HBO Max') == 'scope')

print("[2] base selection")
base = C._pm_generation_base('', ARMINE, prefer_catalog=True)
check("the catalog names the YouTube profile for her words", bool(base) and base.get('s3_key') == YT_KEY, str(base))
use(FULL)
hit = PS.for_read(ARMINE, [], base, il)
check("the read lane releases it: the subject on record, scoped to YouTube",
      bool(hit) and hit['platform'] == 'YouTube' and hit['subject'] == YT_SUBJ and hit['cohort'] == 'on YouTube'
      and hit['source'] == 'ledger', str(hit))
use(ALL_ONLY)
hit = PS.for_read(ARMINE, [], base, il)
check("with only the all-platform read on record, that subject is scoped to YouTube",
      bool(hit) and hit['subject'] == ALL_SUBJ and hit['same_scope'] is False, str(hit))
use(FULL)
check("a chip that chose the YouTube profile keeps it", PS.for_read(ARMINE, [], base, il, bind_subject='YouTube') is None)
for t in ('who uses YouTube', 'what do YouTube users buy most', 'the YouTube audience by age and gender'):
    b = C._pm_generation_base('', t, prefer_catalog=True)
    check(f"{t!r}: the YouTube profile is the base", bool(b) and b.get('s3_key') == YT_KEY and PS.for_read(t, [], b, il) is None)
use(index(('youtube', 'YouTube', [E_PLAT]), ('mad_men_viewers', 'Mad Men Viewers', [E_OTHER])))
check("nothing on record and no thread: the base stands", PS.for_read(ARMINE, [], base, il) is None)
check("a base that is not a platform is never touched",
      PS.for_read(ARMINE, [], {'subject': 'Mad Men Viewers', 's3_key': 'x.csv', 'source': 'catalog'}, il) is None)
src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
i = src.index('def _pm_generate_metrics_response(')
body = src[i:i + 60000]
check("the hook sits in the read lane after the base is resolved and before the universe guard",
      0 < body.index("base = _pm_generation_base(subj_hint, text, ctx=ctx,") < body.index("platform_scope', fromlist=['for_read'])")
      < body.index("_qu = _question_universe.question_universe(text, _qu_page)"))
check("a resumed job keeps the base it was started on", "_ps = None if _pinned_base else" in body)
check("the released read runs on the held subject, with no profile rows riding the prompt",
      "base, digest_block, subj_hint, bind_subject, bind_cohort = None, '', _ps['subject']" in body)

print("[3] the ledger")
use(FULL)
led = il.consult(subject='YouTube', question=ARMINE)
check("a lookup keyed on youtube lands on the subject on record", led.get('skey') == 'digital_original_content_categories_on_youtube',
      str(led.get('skey')))
vals = {m['name']: m['value'] for e in led.get('entries') or [] for m in e.get('metrics') or []}
check("the held figure bounds the answer (Comedy on YouTube 4,713,882)", vals.get('comedy_youtube_audience') == 4713882, str(vals))
check("and it is in the block the prompt reads", '4,713,882' in (led.get('block') or ''), (led.get('block') or '')[:200])
led = il.consult(question=ARMINE)
check("the same with no subject passed", led.get('skey') == 'digital_original_content_categories_on_youtube', str(led.get('skey')))
use(ALL_ONLY)
led = il.consult(subject='YouTube', question=ARMINE)
check("only the all-platform read on record: it is consulted", led.get('skey') == 'digital_original_content_categories_comedy_lifestyle',
      str(led.get('skey')))
check("its figures ride as context", '6,579,974' in (led.get('block') or ''))
check("and it never replays as the YouTube answer", led.get('exact') is None, str((led.get('exact') or {}).get('q')))
use(FULL)
led = il.consult(subject='YouTube', question='who uses youtube')
check("a question about YouTube's own audience stays on the YouTube bucket", led.get('skey') == 'youtube', str(led.get('skey')))
led = il.consult(subject='Mad Men Viewers', question='how big is the mad men audience on YouTube')
check("a subject that is not a platform is never moved", led.get('skey') == 'mad_men_viewers', str(led.get('skey')))

print("[4] a share on record binds a table on the same scope only")


def new_table(universe, dim, basis):
    return {'subject': 'x', 'cohort': 'digital original content viewers', 'universe': universe, 'window_start': '2025-10-21',
            'window_end': '2026-10-21', 'metrics': [],
            'breakdown': {'dimension': dim, 'share_basis': basis,
                          'rows': [{'label': 'Comedy', 'share_pct': 30.0}, {'label': 'Lifestyle / Arts & Culture', 'share_pct': 24.0},
                                   {'label': 'Horror', 'share_pct': 18.0}, {'label': 'Young Adult', 'share_pct': 15.0},
                                   {'label': 'Faith', 'share_pct': 13.0}]}}


e_all = dict(E_ALL, cohort='digital original content viewers')
yt_only = new_table('people who watched digital original content on YouTube', 'Digital original category, YouTube only',
                    'share of YouTube digital original viewing')
out, bound = FB.bind_to_stored(yt_only, [e_all], ask_text=ARMINE)
check("the all-platform shares are not carried into a YouTube-only table",
      not bound and [r['share_pct'] for r in out['breakdown']['rows']] == [30.0, 24.0, 18.0, 15.0, 13.0], str(bound))
yt_again = new_table('people who watched digital original content on YouTube', 'Digital original category, YouTube only',
                     'share of YouTube digital original viewing')
out, bound = FB.bind_to_stored(yt_again, [E_YT, e_all], ask_text=ARMINE)
comedy = next(r for r in out['breakdown']['rows'] if r['label'] == 'Comedy')
check("the YouTube-only shares on record bind the YouTube-only table", bool(bound) and comedy.get('bound_from') == '2026-11-11', str(bound))
all_again = new_table(ALL_SUBJ, 'Digital original content category', 'share of the five-category digital original viewing signals')
out, bound = FB.bind_to_stored(all_again, [E_YT, e_all], ask_text='how do the five digital original content categories split')
check("an all-platform table still binds to the all-platform read",
      [r['share_pct'] for r in out['breakdown']['rows']] == [32.6, 25.4, 16.9, 14.2, 10.9], str([r['share_pct'] for r in out['breakdown']['rows']]))
unlabeled = new_table('', 'Category', 'share')
out, bound = FB.bind_to_stored(unlabeled, [e_all], ask_text=ARMINE)
check("a table that names no scope takes the question's", not bound)

print("[5] the thread")
THREAD = [{'role': 'user', 'text': 'how do the digital original content categories split'},
          {'role': 'agent', 'text': 'On Digital original content categories:\n\nComedy leads.', 'meta': {'subject': 'Digital original content categories'}}]
yt_base = {'subject': 'YouTube', 's3_key': YT_KEY, 'source': 'catalog'}
use(index(('youtube', 'YouTube', [E_PLAT])))
hit = PS.for_read('and on YouTube only?', THREAD, yt_base, il)
check("a follow-up that only adds a platform scopes the thread's subject",
      bool(hit) and hit['subject'] == 'Digital original content categories' and hit['source'] == 'thread', str(hit))
check("a question about people at large is its own universe",
      PS.for_read('what do people watch on YouTube', THREAD, yt_base, il) is None)
check("a thread already on the platform is not rescoped",
      PS.for_read('and on YouTube only?', [{'role': 'agent', 'text': 'On YouTube:', 'meta': {'subject': 'YouTube'}}], yt_base, il) is None)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all checks passed")
