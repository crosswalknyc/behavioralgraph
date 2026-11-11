#!/usr/bin/env python3
"""The Read's figures are the decision of record and Prometheus answers
from it (Jenna 2026-11-10: "confirming the figures given to liz in chat
match what was pulled for newsletter and anything pulled here is stored
for Prometheus").

Liz's three API threads on Amazon Prime Video Channels vs direct
(2026-11-11 00:41 to 01:09 UTC) exposed four defects:

  A. three re-asks with a changed scope were handed an EARLIER reply:
     "still active at day 30, 60 and 90 ... Nov 1 2025 through Oct 31
     2026" got the starts-and-share read (Jul 2025 to Jun 2026);
     "90 day survival ... one row per service" got the cancellers'
     switching read; "still active at day 30, 60 and 90 ... by service"
     got the churn-rate read. insights_ledger.find_semantic matched on
     family, dimension and topic overlap and never asked whether the
     stored read counted what the ask counts, or sat on the month window
     the ask named.
  B. "what share of each path's subscribers are on month-to-month pricing"
     read "Path's subscribers" as a named universe; the read shipped
     under "On Path's subscribers in the US:".
  C. one thread's second read re-sized the same two cohorts (52,605,814
     direct starts became 23,272,291; 15,808,113 Channels starts became
     9,187,463) on a basis change the ask never tied to a new window.
  D. the newsletter's figures were never stored, so nothing Prometheus
     said could agree with them.

Holds:
  1. none of Liz's three re-asks replays the earlier entry (consult.exact
     is None on each pair); the identical question still replays.
  2. a changed-scope re-ask on the same thread is never a thread replay.
  3. the subject parse keeps 'path' as an ordinary word; 'billing path',
     'basis', 'channel', 'route', 'cohort', 'segment' never name a
     universe; Costco members still does.
  4. the thread's cohort bases hold on the follow-up read (the metrics,
     the prose and the combined total), and a window named in the ask
     releases them.
  5. the issue banks as the decision of record: the natural asks resolve
     to it, consult hands it back, the divergent entries are retired, a
     later operator read cannot evict it, and its values never move.
  6. the newsletter bank step writes the campaign's figures block through
     the ledger; a campaign with no block falls back to the slices' alt
     text and warns; a campaign with nothing warns and writes nothing.

Hermetic: the ledger index is injected; no S3, no model.
"""
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import insights_ledger as il  # noqa: E402
from prometheus import question_universe as qu  # noqa: E402
from prometheus import smarter as sm  # noqa: E402
from prometheus import thread_bases as tb  # noqa: E402

FAILS = []
HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, 'fixtures', 'liz_amazon_vs_direct_2026_11_11.json')))


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def inject(doc):
    with il._cache_lock:
        il._index_cache['doc'] = doc
        il._index_cache['ts'] = time.time() + 3600


def clear():
    with il._cache_lock:
        il._index_cache['doc'] = None
        il._index_cache['ts'] = 0.0


def fixture_doc():
    return {'subjects': copy.deepcopy(FX['subjects']), 'updated': '2026-11-11T01:10:38Z'}


ASK_0041 = ("For the ten premium streaming services sold both through Amazon Prime Video Channels and on the services' own sites "
            "and apps (HBO Max, Peacock, Paramount+, STARZ, Apple TV, BET+, MGM+, AMC+, BritBox, Acorn TV): how many new US "
            "subscriptions started in the window, what share started through Amazon Channels versus direct, and what is the "
            "average monthly churn rate for subscribers on each path (Amazon versus direct)? State how churn is defined.")
ASK_0051_33 = ("Same ten services, same two paths (started through Amazon Prime Video Channels versus started direct on the service's "
               "own site or app). Please use the window Nov 1 2025 through Oct 31 2026. Three things, each split by path: (1) share of "
               "new subscribers still active at day 30, day 60 and day 90 after signup; (2) share of new signups who watched only one "
               "title and cancelled within 35 days, and say whether the 35 days is measured to the cancel request or to the day access "
               "ends; (3) average number of months kept in the first 12 months after signup, and say whether that is observed or "
               "projected for the later cohorts.")
ASK_0101 = ("For new subscribers to these ten streaming services (HBO Max, Peacock, Paramount+, STARZ, Apple TV, BET+, MGM+, AMC+, "
            "BritBox, Acorn TV), compare signups that started through Amazon Prime Video Channels with signups that started direct on "
            "the service's own site or app: what share are still active at day 30, day 60 and day 90 after signup, for the ten services "
            "combined and by service?")
ASK_0109 = ("For each of the ten services (HBO Max, Peacock, Paramount+, STARZ, Apple TV, BET+, MGM+, AMC+, BritBox, Acorn TV): 90 day "
            "survival of new signups that started through Amazon Prime Video Channels versus new signups that started direct on the "
            "service's own site or app. One row per service with both day 90 figures, on the same cancel-request basis for both paths.")
ASK_0051_27 = ("Same ten services and window (Nov 2025 through Oct 2026). Among subscribers who cancelled, split by billing path "
               "(Amazon Prime Video Channels versus direct on the service's own site or app): (1) what share started a subscription to "
               "a different streaming service within 30 days of cancelling; (2) what share came back to the same service within 90 "
               "days; (3) what share of each path's subscribers are on month-to-month pricing versus annual plans or bundles.")

K_STARTS = 'amazon|subscribers|2025-07-01|2026-06-30'
K_CHURN_SVC = 'amazon_prime_video_channels_vs_direct_billing_ten_streaming_services|subscribers|2025-11-11|2026-11-11'
K_CANCELLERS = 'amazon_prime_video_channels_vs_direct_billing_ten_streaming_services|subscribers|2025-11-01|2026-10-31'

# ---------------------------------------------------------------- 1. replay
print('1. a re-ask with a changed scope is a new read, never an earlier reply')
inject(fixture_doc())
r = il.consult(subject='Amazon Prime Video', question=ASK_0051_33)
check(r['skey'] == 'amazon' and len(r['entries']) >= 1, 'the Amazon bucket resolves for the 00:51:33 ask')
check(r['exact'] is None, '00:51:33 (day 30/60/90, one title, months kept; Nov 2025 to Oct 2026) does not replay the 00:41 starts read')
check(il.ask_month_window(ASK_0051_33) == (2025, 11, 2026, 10), 'the ask names its month window, Nov 2025 to Oct 2026')
e_starts = next(e for e in r['entries'] if e['k'] == K_STARTS)
check(not il._month_window_ok(e_starts, ASK_0051_33), 'a Jul 2025 to Jun 2026 entry is not the Nov 2025 to Oct 2026 window')
check('survival' in il.ask_measure_classes(ASK_0051_33) and 'months_kept' in il.ask_measure_classes(ASK_0051_33),
      'the ask names the survival and months-kept measures')
check(not il._measure_ok(e_starts, il.normalize_question(ASK_0051_33)), 'the starts read does not carry the survival measure')

r = il.consult(subject='Amazon Prime Video Channels vs direct billing, ten streaming services', question=ASK_0109)
check(r['exact'] is None, '01:09:25 (per-service day 90 survival) does not replay the 00:51 cancellers read')
e_canc = next(e for e in r['entries'] if e['k'] == K_CANCELLERS)
check(not il._measure_ok(e_canc, il.normalize_question(ASK_0109)), 'the cancellers read does not carry the survival measure')
check(il.find_semantic(r['entries'], ASK_0109) is None, 'find_semantic refuses every entry in the bucket for the per-service survival ask')

r = il.consult(subject='Amazon Prime Video Channels vs direct billing, ten streaming services', question=ASK_0101)
check(r['exact'] is None, '01:01:39 (day 30/60/90 combined and by service) does not replay the 00:43 churn read')

r = il.consult(subject='Amazon Prime Video', question=ASK_0041)
check(r['exact'] is not None and r['exact']['k'] == K_STARTS, 'the identical 00:41 question still replays its own entry')
# the key path is gated the same way
e_churn = next(e for sub in fixture_doc()['subjects'].values() for e in sub['entries'] if e['k'] == K_CHURN_SVC)
check(il.find_exact([e_churn], question=ASK_0101, key=K_CHURN_SVC) is None,
      'a key match (subject, family, window) still needs the stored read to count what the ask counts')
clear()

# ---------------------------------------------------------- 2. thread replay
print('2. the thread lane never replays a changed-scope re-ask')
hist = [
    {'role': 'user', 'text': ASK_0041, 'ts': '2026-11-11T00:41:03Z'},
    {'role': 'agent', 'text': 'On Amazon Prime Video:\n\nThe ten premium services took 68,413,927 new US subscription starts in the window, '
                              'and 15,808,113 of them (23.1%) started inside Amazon Prime Video Channels. New US subscription starts, ten services: 68,413,927.',
     'ts': '2026-11-11T00:48:23Z', 'meta': {'kind': 'read', 'read_job_id': '1f648cc05c41'}},
]
check(sm.thread_replay(ASK_0051_33, hist) is None, 'the 00:51:33 re-ask (new scope) is not a thread replay of the 00:41 read')
check(sm.thread_replay(ASK_0041, hist) is not None, 'the identical question in the thread is')

# --------------------------------------------------------- 3. subject parse
print('3. "by billing path" never yields a subject named Path')
u = qu.question_universe(ASK_0051_27)
check('path' not in u.label.lower(), f"Liz's cancellers sentence reads no 'Path' universe (label {u.label!r})")
check(all(p[0].lower() != "path's subscribers" for p in qu.named_universe(ASK_0051_27)), "named_universe drops \"path's subscribers\"")
for w in ('path', "path's", 'basis', 'channel', 'channels', 'route', 'cohort', 'segment', 'tier', 'bucket', 'billing'):
    check(qu.ordinary_word(w), f'{w!r} is an ordinary word')
for probe in ("what share of each path's subscribers are on month-to-month pricing", 'Day 90 survival by billing path',
              "each segment's buyers", "the cohort's viewers who switched", 'split by route and basis'):
    lab = qu.question_universe(probe).label
    check(not any(x in lab.lower() for x in ("path", 'segment', 'cohort', 'route', 'basis')), f'{probe!r} names no cut-word universe ({lab!r})')
check(qu.question_universe('what do Costco members buy').label == 'Costco members', 'Costco members still names its universe')
check(qu.question_universe('potential Paw Patrol consumers in the US').label == 'potential Paw Patrol consumers', 'potential Paw Patrol consumers still does')

# ------------------------------------------------------------ 4. thread bases
print('4. a named base holds across the thread')
JOBS = FX['jobs']
history = [
    {'role': 'user', 'text': 'Track new signups as cohorts from their own day 0. ...', 'ts': '2026-11-11T01:04:51Z'},
    {'role': 'agent', 'text': 'On it: reading HBO Max, Peacock, Paramount+ and more now. The read will land right here when it is ready.',
     'ts': '2026-11-11T01:04:51Z', 'meta': {'kind': 'job', 'read_job_id': 'b774c5c8e45b'}},
    {'role': 'user', 'text': 'Show the same two paths on an access-end cancel basis instead of request date', 'ts': '2026-11-11T01:08:12Z'},
]
reads = tb.thread_reads(history, lambda jid: JOBS.get(jid))
check([r['job_id'] for r in reads] == ['b774c5c8e45b'], 'the promise turn resolves to its landed job document')
bases = tb.bases_from_reads(reads)
by = {frozenset(b['tokens']): b['n'] for b in bases}
check(by.get(frozenset({'direct'})) == 52605814, 'the direct cohort base is 52,605,814')
check(by.get(frozenset({'amazon', 'prime', 'video', 'channels'})) == 15808113, 'the Channels cohort base is 15,808,113')
check(not any(b['n'] in (9216127, 37665766, 7066227) for b in bases), 'a still-active or gone count is never a base')
p2 = JOBS['eed820b0c9ac']['payload']
res = {'metrics': copy.deepcopy(p2['metrics']), 'breakdown': copy.deepcopy(p2['breakdown']),
       'headline': 'at day 90 direct holds 58.7% of its 23,272,291 signups against 51.9% of the 9,187,463 that started through Prime Video Channels'}
res2, _r, held = tb.hold(res, '', bases, ask_text=history[-1]['text'])
vals = {m['name']: m['value'] for m in res2['metrics']}
check(vals['channels_cohort'] == 15808113 and vals['direct_cohort'] == 52605814, 'the follow-up read keeps the thread\'s cohort bases in its metrics')
check('52,605,814' in res2['headline'] and '15,808,113' in res2['headline'] and '23,272,291' not in res2['headline'],
      'the prose follows the held bases')
check('68,413,927' in res2['breakdown']['share_basis'] and '32,459,754' not in res2['breakdown']['share_basis'],
      'the combined total follows the two held bases')
check(tb.held_line(held).startswith('Cohort bases kept as first reported in this thread:') and '52,605,814' in tb.held_line(held),
      'one plain line says what was kept')
res3 = {'metrics': copy.deepcopy(p2['metrics'])}
res3, _r, held3 = tb.hold(res3, '', bases, ask_text='Same two paths but for cohorts opened Jan 1 2026 through Mar 31 2026 only')
check(not held3 and res3['metrics'][0]['value'] == 9187463, 'a window named in the ask releases the hold')
check(tb.ask_names_window('Please use the window Nov 1 2025 through Oct 31 2026') and not tb.ask_names_window('same two paths, same window'),
      'ask_names_window reads a named window and not "same window"')

# --------------------------------------------------------------- 5. record
print('5. the issue banks as the decision of record')
sys.path.insert(0, HERE)
import bank_the_read_amazon_vs_direct as rec  # noqa: E402

doc = fixture_doc()
inject(doc)
written = []
_orig = (il._update_index, il._put_entry_object)


def _update_index_local(mutate_fn, max_retries=5):
    out = mutate_fn(doc)
    return out


il._update_index = _update_index_local
il._put_entry_object = lambda e: written.append(e)
try:
    block = rec.record_block()
    keys = il.bank_record(
        subject=block['subject'], source=block['source'], metrics=block['metrics'], question=block['question'],
        reply=block['reply'], window_start=block['window']['start'], window_end=block['window']['end'],
        window_label=block['window']['label'], universe=block['universe'], breakdown=block['breakdown'],
        metric_family=block['family'], followups=block['followups'], questions=block['questions'],
        subject_aliases=block['aliases'])
    check(len(keys) == 1 + len(block['questions']), f'one record entry per natural ask plus the headline ({len(keys)} written)')
    check(all(k.startswith('amazon_prime_video_channels_vs_direct') and '|subscribers|2025-11-01|2026-10-31' in k for k in keys),
          'every record key carries the subject, the family and the published window')
    skey = il.bucket_key_for(doc, block['subject'])
    ents = doc['subjects'][skey]['entries']
    check(all(il.is_record_entry(e) for e in ents), 'the bucket holds record entries only')
    check(all(il.provenance_rank(e) == 3 for e in ents), 'a record outranks delivered, operator and distilled entries')
    head = next(e for e in ents if e.get('breakdown'))
    names = {m['name']: m['value'] for m in head['metrics']}
    check(names['monthly_churn_amazon'] == 7.4 and names['monthly_churn_direct'] == 4.3 and names['subscriptions_followed'] == 6214387
          and names['active_day90_direct'] == 61.4 and names['first_year_gap_after_share'] == 51.67,
          'the record carries the issue\'s figures unchanged (7.4 / 4.3, 6,214,387, 61.4, $51.67)')
    check(len(head['breakdown']['rows']) == 20 and sum(r['pool'] for r in head['breakdown']['rows'] if r['group'] == 'Via Amazon') == 2075611
          and sum(r['pool'] for r in head['breakdown']['rows'] if r['group'] == 'Direct') == 4138776,
          'the per-service rows split the record\'s totals and sum back to them')
    il.retire_entries(keys=rec.DIVERGENT_KEYS, by='decision_of_record', note='test')
    gone = [e for sub in doc['subjects'].values() for e in sub['entries'] if e['k'] in rec.DIVERGENT_KEYS]
    check(gone and all(e.get('retired') for e in gone), f'the {len(gone)} divergent entries are stamped retired')
    for ask in ('churn through Amazon Channels vs direct', 'what share of starts come through Amazon',
                'day-90 survival by billing path', 'what is a Channels subscriber worth', ASK_0109, ASK_0051_33, ASK_0101):
        r = il.consult(question=ask)
        ok = r['skey'] == skey and r['entries'] and r['exact'] is not None and il.is_record_entry(r['exact'])
        check(bool(ok), f'consult hands the record back for {ask[:60]!r}')
    r = il.consult(subject='Amazon Prime Video Channels vs direct billing, ten streaming services', question=ASK_0109)
    check(r['exact'] is None or il.is_record_entry(r['exact']), 'the retired cancellers read never replays for the per-service survival ask')
    check('[decision of record]' in il.render_block(ents) and '7.4%' in il.render_block(ents), 'the prompt block tags the record')
    # a later operator read on the same subject cannot evict or move the record
    later = il.make_entry(subject=block['subject'], metric_family='subscribers', question=block['question'], route='reasoned_metrics',
                          metrics=[{'name': 'monthly_churn_amazon', 'value': 8.7, 'unit': 'pct'}], reply='On x: churn 8.7%',
                          window_start='2025-11-01', window_end='2026-10-31')
    check(not il._supersedes(later, head), 'an operator read with the same question does not supersede the record')
    before = copy.deepcopy(head['metrics'])
    il._enforce_ledger_coherence(doc, skey, head)
    check(head['metrics'] == before, 'the coherence pass never moves a record value')
finally:
    il._update_index, il._put_entry_object = _orig
    clear()

# -------------------------------------------------------- 6. newsletter hook
print('6. the newsletter banks the issue before it sends')
import newsletter as nl  # noqa: E402


class FakeLedger:
    def __init__(self):
        self.calls = []

    def bank_record(self, **kw):
        self.calls.append(kw)
        return [f"{il.subject_key(kw['subject'])}|{kw.get('metric_family') or 'x'}|{kw.get('window_start')}|{kw.get('window_end')}"]


state = {'version': 2, 'campaigns': [
    {'id': 'c-figures', 'name': 'The Read / Amazon vs Direct', 'subject': 'People who sign up through Amazon vs direct cancel 1.7x as often.',
     'figures': rec.record_block()},
    {'id': 'c-alt', 'name': 'The Read / Test', 'subject': 'A test issue.'},
    {'id': 'c-empty', 'name': 'The Read / Empty', 'subject': 'Nothing here.'},
]}
_marked = []
_orig_mark = nl._mark_banked
nl._mark_banked = lambda cid, keys, source: _marked.append((cid, list(keys), source))
try:
    fl = FakeLedger()
    rep = nl.bank_issue_figures('c-figures', ledger=fl, state=state, html='<p>no slices</p>')
    check(rep['source'] == 'figures' and rep['banked'] and not rep['warning'], 'a campaign with a figures block banks it (no warning)')
    kw = fl.calls[-1]
    check(kw['subject'] == rec.SUBJECT and kw['window_start'] == '2025-11-01' and kw['window_end'] == '2026-10-31'
          and len(kw['metrics']) == len(rec.record_metrics()) and kw['source'] == 'newsletter c-figures', 'the block\'s subject, window and metrics reach bank_record')
    check(_marked and _marked[-1][0] == 'c-figures', 'the campaign record notes what was banked')
    alt_html = ('<img alt="Crosswalk. The Read" src="a.png"><img alt="7.4% monthly churn when the subscription runs through Amazon. '
                '4.3% when the subscriber signed up direct. Same services, same shows." src="b.png">'
                '<img alt="We followed 6,214,387 subscriptions to ten premium services sold both ways. 33.4% started in Amazon\'s store." src="c.png">')
    figs = nl.figures_from_alt_text(alt_html)
    vals = sorted(f['value'] for f in figs)
    check(vals == [4.3, 7.4, 33.4, 6214387.0], f'the alt-text fallback reads the figures out of the slices ({vals})')
    rep = nl.bank_issue_figures('c-alt', ledger=fl, state=state, html=alt_html)
    check(rep['source'] == 'alt_text' and rep['banked'] and 'no figures block' in rep['warning'], 'a campaign with no block banks the alt text and warns')
    rep = nl.bank_issue_figures('c-empty', ledger=fl, state=state, html='<p>none</p>')
    check(not rep['banked'] and 'nothing banked' in rep['warning'], 'a campaign with no figures anywhere warns and writes nothing')
    check(fl.calls[-1]['subject'] == 'The Read / Test', 'the fallback banks under the campaign name')
finally:
    nl._mark_banked = _orig_mark

# the corrected answers for Liz's threads carry the record's figures
ans = rec.corrected_answers()
check(len(ans) == 9, f'nine asks across the three threads get the record\'s answer ({len(ans)})')
check(all(rec.ISSUE_LINE in a['reply'] for a in ans), 'every corrected turn says these are the figures in the issue')
check(all('Path\'s' not in a['reply'] for a in ans), 'no corrected turn carries the Path subject')
d90 = next(a for a in ans if a['question_prefix'].startswith('For each of the ten services'))
check('44.7%' in d90['reply'] and '61.4%' in d90['reply'] and '- Apple TV+: Amazon 50.4%; direct 66.2%' in d90['reply'],
      'the per-service day 90 answer is the record\'s split, Apple TV+ included')
acc = next(a for a in ans if 'access-end' in a['question_prefix'])
check('2,075,611' in acc['reply'] and '4,138,776' in acc['reply'] and '71.8%' in acc['reply'] and '82.3%' in acc['reply'],
      'the access-end answer keeps the record\'s bases and is stated against its figures')
banned = ('synth', 'modeled', 'estimated', 'nielsen', 'antenna', 'clickstream', '\u2014', '\u2013')
check(all(not any(b in a['reply'].lower() for b in banned) for a in ans), 'no corrected turn names a method, a source or carries a dash')

print()
if FAILS:
    print(f'{len(FAILS)} FAILED')
    for f in FAILS:
        print(' -', f)
    sys.exit(1)
print('ALL OK')
