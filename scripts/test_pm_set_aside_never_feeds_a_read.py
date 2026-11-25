#!/usr/bin/env python3
"""A set-aside answer never feeds a later read, for anyone (Jenna 2026-11-24).

Armine's Nov 23 skateboarding table was retired and its words set aside,
and it still came back in her next read through two channels: the shared
ring (prometheus.shared_memory) banked an explanation reply's quoted
figures as metrics named by fragments of prose ('read: The Nine Club sits
at' = 31.2), and the corpus catalog indexed every thread turn's numbers as
chat facts that anchors_for handed to the next read.

Holds:
  1. a figure is named by what it measures, never by the prose before it
  2. the shared ring: an explanation reply that quotes the retired table
     banks nothing; a reply that restates its figures banks nothing; words
     set aside for another seat bank nothing; a normal page answer banks,
     under its evidence's names
  3. the catalog: a thread turn carrying the retired figures is not indexed,
     through the write hook or the sweep; a normal turn is
  4. anchors_for: facts indexed before the fix from such a turn, and a live
     page answer that restates the retired figures, never reach a read; the
     normal facts and the record do
  5. consult (the shared ring's read point): the restatement never feeds the
     block or a replay; the record still does
  6. what an explanation ask is

Hermetic: REGRESSION_TEST_MODE=1, the ledger's in-memory store, a fake
catalog store, no model, no network.
"""
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['CORPUS_CATALOG'] = '1'

from botocore.exceptions import ClientError  # noqa: E402

import insights_ledger as il  # noqa: E402
from migration import corpus_catalog as cc  # noqa: E402
from migration import corpus_catalog_sync as ccs  # noqa: E402
from prometheus import shared_memory as sm  # noqa: E402

PASS, FAIL = 0, []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}  {detail}')


SUBJ = 'Paul Testrider'
RETIRED_TABLE = ('On Paul Testrider (pro skateboarder) - People that searched Paul Testrider in the US: 60,696 of the 1,742,869 '
                 'name searchers watched a skateboarding series in the window, and The Nine Club is the biggest single title '
                 'at 60,695 viewers. Skateboarding series mix, Nov 23 2025 to Nov 23 2026 - The Nine Club: 19.6% share, '
                 '31.2% penetration - Ridiculousness: 16.8% share, 28.1% penetration.')
RECORD = ('Skateboarding series the Paul Testrider searchers watch. The group is the 1,742,869 US people who searched for '
          'Paul Testrider in that year. 868,347 of the 1,742,869 searchers watched at least one skateboarding series, and '
          'The Nine Club leads at 412,935 viewers.')
STALE_PAGE = ('Two different denominators. Same row, both readings: The Nine Club sits at 31.2% penetration and 19.6% share; '
              'Ridiculousness reads 28.1% penetration and 16.8% share.')
EXPLAIN_Q = 'what metric is penetration vs share'
EXPLAIN_REPLY = ('Penetration counts people; share counts activity. From your own read: The Nine Club sits at 31.2% '
                 'penetration and 19.6% share, and Ridiculousness reads 28.1% penetration and 16.8% share.')
QUOTE_Q = 'tell me more about The Nine Club for this audience'
QUOTE_REPLY = ('60,696 people in this audience watched a skateboarding series. The Nine Club sits at 31.2% penetration '
               'and 19.6% share, with 60,695 viewers.')
NORMAL_Q = 'which platforms does this audience use'
NORMAL_REPLY = ('Reddit in APP/PLATFORM USAGE reads 73.6% penetration. The projected US audience is 2,851,821 people.')
NORMAL_FIGS = [{'value': 73.6, 'kind': 'pct', 'label': 'Reddit', 'source': 'profile_file', 'id': 'f1'},
               {'value': 2851821, 'kind': 'count', 'label': 'Projected audience (US people)', 'source': 'profile_file', 'id': 'f2'}]


def entry(subject, k, q, reply, route, prov, metrics, retired=None, ts='2026-11-23T18:54:39Z'):
    e = {'k': k, 'subject': subject, 'family': 'viewership', 'q': q, 'qn': il.normalize_question(q), 'route': route,
         'ts': ts, 'ws': '2025-11-23', 'we': '2026-11-23', 'wl': '', 'metrics': metrics, 'anchors': [], 'base': '',
         'cohort': '', 'universe': '', 'breakdown': None, 'reply': reply, 'followups': [], 'derivation': '', 'prov': prov}
    if retired:
        e['retired'] = retired
    return e


def ledger_index():
    return {'updated': '2026-11-25T00:00:00Z', 'subjects': {
        'paul_testrider': {'subject': SUBJ, 'entries': [
            entry(SUBJ, 'paul_testrider|viewership|2025-11-17|2026-11-17', 'wat about series about skateboarding', RECORD,
                  'decision_of_record_skate', 'decision_of_record',
                  [{'name': 'cohort_base', 'label': 'People who searched Paul Testrider', 'value': 1742869, 'unit': 'people'},
                   {'name': 'series_viewers', 'label': 'Skateboarding series viewers', 'value': 868347, 'unit': 'people'}],
                  ts='2026-11-25T03:27:20Z'),
            entry(SUBJ, 'paul_testrider|audience|any|any', EXPLAIN_Q + ' on the series read', STALE_PAGE, 'page_analysis',
                  'operator', [{'name': 'read_the_nine_club_sits_at', 'label': 'read: The Nine Club sits at', 'value': 31.2,
                                'unit': 'pct'}], ts='2026-11-23T23:38:28Z')]},
        'paul_testrider_pro_skateboarder': {'subject': 'Paul Testrider (pro skateboarder)', 'entries': [
            entry('Paul Testrider (pro skateboarder)', 'paul_testrider_pro_skateboarder|viewership|2025-11-23|2026-11-23',
                  'wat about series about skateboarding', RETIRED_TABLE, 'reasoned_metrics', 'operator',
                  [{'name': 'skate_series_watchers', 'label': 'Watched a skateboarding series', 'value': 60696, 'unit': 'people'},
                   {'name': 'series_watch_rate', 'label': 'Series watch rate', 'value': 59.5, 'unit': 'pct'}],
                  retired={'by': 'jenna', 'note': 'set aside', 'ts': '2026-11-25T01:36:45Z'})]},
        'harbor_lights': {'subject': 'Harbor Lights', 'entries': [
            entry('Harbor Lights', 'harbor_lights|viewership|a|b', 'how did Harbor Lights do',
                  'Harbor Lights reached 31.2% penetration and 19.6% share among Northstar viewers.', 'reasoned_metrics',
                  'operator', [], ts='2026-11-20T10:00:00Z')]}}}


def _err(code, status=404):
    return ClientError({'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'Op')


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, body):
        return hashlib.md5(body).hexdigest()

    def get_object(self, Bucket, Key, **_kw):
        if Key not in self.objs:
            raise _err('NoSuchKey')
        return {'Body': _Body(self.objs[Key]), 'ETag': '"%s"' % self._etag(self.objs[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _err('404')
        return {'ETag': '"%s"' % self._etag(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None, **_kw):
        cur = self.objs.get(Key)
        if IfNoneMatch == '*' and cur is not None:
            raise _err('PreconditionFailed', 412)
        if IfMatch is not None and (cur is None or self._etag(cur) != IfMatch):
            raise _err('PreconditionFailed', 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else Body.encode('utf-8')
        return {}

    def list_objects_v2(self, Bucket, Prefix='', **_kw):
        return {'Contents': [{'Key': k, 'ETag': '"%s"' % self._etag(v)} for k, v in sorted(self.objs.items())
                             if k.startswith(Prefix)], 'IsTruncated': False}


RETIRED_TOKENS = {'60696', '60695', '31.2', '19.6', '28.1', '16.8'}


def fact_tokens(facts):
    out = set()
    for f in facts or []:
        out.add(il.value_tokens(f.get('value')))
    return out - {''}


def ledger_entries_now():
    return [e for v in il._load_index(force=True)['subjects'].values() for e in v.get('entries') or []]


def main():
    il.use_test_ledger(copy.deepcopy(ledger_index()))

    print('1. a figure is named by what it measures')
    figs = sm.figures_from_reply('Same row, both readings, read: The Nine Club sits at 31.2% penetration and 19.6% share. '
                                 'Unique US viewers: 416,287. They logged 912,447 sessions.')
    names = {f['value']: f['label'] for f in figs}
    check("'read: The Nine Club sits at' is never a name", names.get(31.2) == 'The Nine Club penetration', str(names))
    check('a figure the reply never names has no name', names.get(19.6) == '', str(names))
    check("'Unique US viewers: 416,287' and '912,447 sessions' keep their names",
          names.get(416287) == 'Unique US viewers' and names.get(912447) == 'sessions', str(names))
    check('no name is a fragment of prose', not any(w in (f.get('label') or '') for f in figs for w in (' sits ', 'reads', 'read:')))

    print('2. the shared ring')
    n0 = len(ledger_entries_now())
    check('the retired table is seen as retired', il.held_back(QUOTE_REPLY, subject=SUBJ) == 'quotes_retired')
    check('an explanation reply that quotes it banks nothing',
          sm.persist_answer(il, subject=SUBJ, question=EXPLAIN_Q, reply=EXPLAIN_REPLY) is False and len(ledger_entries_now()) == n0)
    check('a reply that restates its figures banks nothing',
          sm.persist_answer(il, subject=SUBJ, question=QUOTE_Q, reply=QUOTE_REPLY) is False and len(ledger_entries_now()) == n0)
    elsewhere = ('Paul Testrider audiences watch 402,117 hours of skate video on YouTube a month, about 6.1 hours per viewer.')
    il._set_aside_registry.update(doc={'replies': [{'sig': s, 'scope': {'user': 'another_seat'}, 'head': elsewhere[:160]}
                                                   for s in il.reply_sigs(elsewhere)]}, ts=time.time())
    il._set_aside_cache.update(doc=None, ts=0.0, sigs=frozenset(), scoped=())
    check('words set aside for another seat are set aside for every seat', il.words_set_aside(elsewhere)
          and not il.reply_set_aside(elsewhere))
    check('and bank nothing', sm.persist_answer(il, subject=SUBJ, question='how many hours do they watch', reply=elsewhere) is False
          and len(ledger_entries_now()) == n0)
    il._set_aside_registry.update(doc={'replies': []}, ts=time.time())
    il._set_aside_cache.update(doc=None, ts=0.0, sigs=frozenset(), scoped=())
    ok = sm.persist_answer(il, subject=SUBJ, question=NORMAL_Q, reply=NORMAL_REPLY, figures=NORMAL_FIGS)
    new = [e for e in ledger_entries_now() if e.get('reply') == NORMAL_REPLY]
    mnames = {m.get('label') for e in new for m in e.get('metrics') or []}
    check('a normal page answer still banks', ok is True and len(new) == 1, str(ok))
    check("under its evidence's names", mnames == {'Reddit penetration', 'Projected audience (US people)'}, str(mnames))
    plain = 'Paul Testrider reads 1.6x the US rate on the lead brand; the second brand sits at 0.8x.'
    ok = sm.persist_answer(il, subject=SUBJ, question='what does this data tell me about my audience?', reply=plain)
    kept = [e for e in ledger_entries_now() if e.get('reply') == plain]
    check('a page answer whose figures are not named keeps its reply for the same ask, with no metric',
          ok is True and len(kept) == 1 and kept[0].get('metrics') == [], str([(e.get('metrics')) for e in kept]))

    print('3. the catalog')
    fake = FakeS3()
    cc.set_client(fake)
    cc.upsert_source('profile', 'Paul_Testrider.csv', SUBJ,
                     [cc.make_fact('profile', 'sample_size', 'Sample', 31207, 'people', source_key='Paul_Testrider.csv')])
    check('a thread turn carrying the retired figures is not indexed (write hook)',
          cc.record_answer(SUBJ, QUOTE_REPLY, user='seat_a', thread_id='t1') == '')
    page = cc.load_subject_page(cc.subject_key(SUBJ), force=True) or {}
    check('none of its figures is on the page', not (fact_tokens(page.get('facts')) & RETIRED_TOKENS),
          str(fact_tokens(page.get('facts'))))
    normal2 = 'For Paul Testrider, 2,409,187 unique US viewers played a skate video on YouTube in the window.'
    tkey = 'system/synth_chat_threads/seat_a/t2.json'
    fake.objs[tkey] = json.dumps([{'role': 'user', 'text': 'Paul Testrider: ' + QUOTE_Q}, {'role': 'agent', 'text': QUOTE_REPLY},
                                  {'role': 'user', 'text': 'and on YouTube for Paul Testrider?'}, {'role': 'agent', 'text': normal2}]).encode()
    n = ccs.backfill_threads(dict(cc.load_sources()))
    page = cc.load_subject_page(cc.subject_key(SUBJ), force=True) or {}
    toks = fact_tokens(page.get('facts'))
    check('the sweep skips it and banks the normal turn', n == 1 and '2409187' in toks and not (toks & RETIRED_TOKENS),
          f'{n} {sorted(toks)}')

    print('4. anchors_for')
    old = cc.facts_from_answer(SUBJ, QUOTE_REPLY, user='seat_b', thread_id='old')
    okey = f"thread/seat_b/old/{hashlib.sha1(QUOTE_REPLY.encode('utf-8')).hexdigest()[:10]}"
    for f in old:
        f['source']['key'] = okey
    cc.upsert_source('chat', okey, SUBJ, old)   # indexed before this fix
    page = cc.load_subject_page(cc.subject_key(SUBJ), force=True) or {}
    check('(the old facts are on the page)', bool(fact_tokens(page.get('facts')) & RETIRED_TOKENS))
    a = cc.anchors_for(SUBJ, with_ledger=False)
    got = fact_tokens(a.get('facts'))
    check('anchors_for hands none of them to a read', not (got & RETIRED_TOKENS), str(sorted(got)))
    check('the normal facts still reach it', {'2409187', '31207'} <= got, str(sorted(got)))
    blk = cc.anchors_block(a)
    check('nor does the binding block', not any(t in blk for t in ('60,696', '31.2%', '19.6%')), blk[:200])
    a2 = cc.anchors_for(SUBJ)
    qs = [e.get('reply') for e in a2.get('ledger') or []]
    check('the live page answer that restates the retired table never reaches it', STALE_PAGE not in qs, str(len(qs)))
    check('the record does', RECORD in qs)
    check('nor its figures the ledger block', '31.2' not in (a2.get('ledger_block') or ''), (a2.get('ledger_block') or '')[:200])
    cc.set_client(None)

    print('5. consult')
    led = il.consult(subject=SUBJ, question=EXPLAIN_Q + ' on the series read')
    check('the restatement is not replayed, even for its own question',
          (led.get('exact') or {}).get('reply') != STALE_PAGE, str((led.get('exact') or {}).get('route')))
    check('nor in the entries or the block', STALE_PAGE not in [e.get('reply') for e in led.get('entries') or []]
          and '31.2' not in (led.get('block') or ''), (led.get('block') or '')[:200])
    rec = il.consult(subject=SUBJ, question='wat about series about skateboarding')
    check('the record still answers its own question', (rec.get('exact') or {}).get('reply') == RECORD)
    other = il.consult(subject='Harbor Lights', question='how did Harbor Lights do')
    check("another subject's 31.2% is its own", (other.get('exact') or {}).get('reply', '').startswith('Harbor Lights reached'))

    print('6. what an explanation ask is')
    for q in (EXPLAIN_Q, 'what is share', 'can you explain how to read this', 'why are the views higher than plays',
              'what does cohort mean in this'):
        check(f'explains: {q!r}', il.is_explanation_ask(q))
    for q in ('how many people watched Moonfall on YouTube', 'what is the audience size of Dogma',
              'Why is Moonfall bigger than Dogma?', 'size the potential U.S. audience for Moonfall'):
        check(f'asks for a read: {q!r}', not il.is_explanation_ask(q))

    src = Path(__file__).read_text(encoding='utf-8')
    check('no long dash in this file', '\u2014' not in src and '\u2013' not in src)
    print(f'\n{PASS} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
