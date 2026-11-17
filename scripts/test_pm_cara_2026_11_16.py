#!/usr/bin/env python3
"""Cara Dorfman's two threads of 2026-11-16 (Amazon), as a standing
regression set (Jenna 2026-11-16: "Its answering cara horribly. look at
her questions and fix so it doesnt happen again").

What went wrong in the first half of that day, and what holds it now:

  * "how do you estimate churn/retention for a service like paramount+?",
    "give me the math behind how you would calculate a retention curve"
    drew a $300 build and a $100 quote. A question about how a figure is
    worked out is answered from the house definitions
    (prometheus.how_computed).
  * "I DONT WANT THE TITLE. I WANT TO KNOW ABOUT THE SURVIVAL CURVE" was
    routed whole, and "THE TITLE. I WANT" was taken as a name to push out
    of the thread. A no and the ask said again are two things
    (prometheus.restated); a message in capitals is read in sentence case.
  * "thier churn" and "survival curve" were priced as audiences.
  * "Open Paramount+", the catalog lane's own chip, ran a read.
  * One question stored the same read twice (a read resumed after a
    deploy landed under two job ids).
  * A CSV link was broken: the file was named after the screen's label,
    which opens with an emoji, and the link's header could not carry it.
  * "Which QSR brands do Paramount+ subscribers over-index on" was
    answered with the Paramount+ subscriber base.

The second half of the day (her questions about the published study) is
scripts/test_pm_study_record.py.

Hermetic: no model, no S3, no app module. The door checks replay her own
words through the request handlers with everything outside them faked.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS = []


def check(ok, name, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f"  ({str(detail)[:400]})"))
    if not ok:
        FAILS.append(name)


FIX = json.load(open(os.path.join(HERE, 'fixtures', 'cara_dorfman_2026_11_16.json'), encoding='utf-8'))['threads']
T1, T2 = FIX['90ecf3aebf'], FIX['694f7eb888']

print('[A] how a figure is worked out is answered from the definitions')
from prometheus import how_computed as HC   # noqa: E402
a = HC.answer(T1[7]['text'])
check(bool(a) and a['reply'].startswith('Churn and retention for a service:') and 'How it is computed:' in a['reply'],
      'how do you estimate churn/retention for a service: the definition and how it is computed', a and a['reply'][:200])
check(bool(a) and 'A published study states its own rule' in a['reply'],
      'the entry says its rule is for a read from viewing activity, and a published study states its own', a and a['reply'][-260:])
a = HC.answer(T1[9]['text'])
check(bool(a) and a['reply'].startswith('Retention curve:'), 'the math behind a retention curve: the curve entry', a and a['reply'][:120])
check(HC.answer('how would you measure the lift from our campaign') is None, 'a hypothetical about something else is not answered from the glossary')
check(HC.answer('what is Paramount+ churn over the past 12 months') is None, 'a figure for a named service is a read, not this lane')
check(HC.answer('Which QSR brands do Paramount+ subscribers over-index on compared with the US general population?') is None,
      'an audience question that says subscribers is not this lane')
for banned in ('synth', 'modeled', 'estimated', 'pipeline', 'panel', '\u2014', '\u2013'):
    check(banned not in (HC.answer(T1[7]['text'])['reply'] + HC.answer(T1[9]['text'])['reply']).lower(), f"no '{banned}' in the definitions")

print('[B] a no and the ask said again are two things')
from prometheus import restated as R   # noqa: E402
rej, again = R.split(T2[2]['text'])
check(again.lower() == 'i want to know about the survival curve' and len(rej) == 1, 'the rejection and the restated ask come apart', (rej, again))
rej, again = R.split('no problem, send it when it is ready')
check(not rej, '"no problem" is not a no', (rej, again))
check(R.is_generic('the title') and R.is_generic('any single content title') and not R.is_generic('Nike'), 'a kind of thing is not a name')
check(R.clip_name('THE TITLE. I WANT') == 'THE TITLE', 'a name stops at the sentence')
check(R.is_shout(T2[4]['text']) and R.fold_shout(T2[4]['text']).startswith('No. Not titles. Subscriber counts.'), 'a message in capitals is read in sentence case')
check(R.fold_shout('What is NFL viewership on ESPN?') == 'What is NFL viewership on ESPN?', 'ordinary capitals are left alone')
from prometheus import continuation as C   # noqa: E402
check(not C._negated_names('I dont want the title. I want to know about the survival curve'), 'no name is pushed out of the thread on "the title"',
      C._negated_names('I dont want the title. I want to know about the survival curve'))
check('salish matter' in C._negated_names('why do you keep bringing up Salish matter'), 'a real name is still pushed away',
      C._negated_names('why do you keep bringing up Salish matter'))

print('[C] a metric is not an audience to price')
from prometheus import sizing_lane as SL   # noqa: E402
check(not SL._plausible('thier churn') and not SL._plausible('survival curve') and not SL._plausible('subscriber counts'), 'churn, a curve and a count are not audiences')
check(SL._plausible('Netflix subscribers') and SL._plausible('churned Netflix subscribers'), 'a real audience still sizes')
import subiq_intent as SI   # noqa: E402
m, subj = SI.ambiguous_churn_subject(T1[9]['text'])
check(m and subj == 'Paramount+', 'a long churn question names the one service it carries, never its own words', (m, subj))

print('[D] the subscriber-base lane takes a count, never an audience')
from prometheus import service_subscribers as SS   # noqa: E402
check(SS.is_ask(T1[0]['text']) and SS.is_ask('how many subscribers does Netflix have') and SS.is_ask('Netflix subscribers'), 'a count ask is taken')
check(not SS.is_ask('Which QSR brands do Paramount+ subscribers over-index on compared with the US general population?'),
      "Liz's QSR ask is about an audience")
check(not SS.is_ask('What are Hulu subscribers buying this holiday season across retail and grocery'), 'what subscribers buy is about an audience')
_reply, chips = SS.compose({'services': list(SS.SERVICES), 'all': True})
check('How is churn worked out for a service?' in chips and not any(c.startswith('Churn for one') for c in chips), 'the chip asks a question the definitions answer', chips)

print('[E] the Open chip of the catalog lane')
from prometheus import catalog_lane as CL   # noqa: E402
check(CL.open_chip_name('Open Paramount+', T1[:4]) == 'Paramount+', 'the chip pressed after the lane offered it is the chip')
check(CL.open_chip_name('Open Paramount+', T1[:1]) == '', 'the same words with no chip behind them keep their lanes')
check(CL.open_chip_name('Open Digital Journey IQ', [{'role': 'agent', 'text': 'x', 'meta': {'options': [{'send': 'Open Digital Journey IQ'}]}}]) == '',
      "another lane's Open chip is not taken")
o = CL.open_reply('Paramount+')
check('Select Profile' in o['reply'] and not o.get('read_job_id'), 'the reply says where it opens and starts nothing', o['reply'])

print('[F] one finished read is one turn, under whichever job id it landed')
from prometheus import thread_merge as TM   # noqa: E402
merged = TM.merge_turns(T1[:17], T1[17:19])
check(len(merged) == 18, 'the read stored twice six seconds apart is one turn', len(merged))
p1 = {'role': 'agent', 'ts': '2026-11-16T22:00:00Z', 'meta': {'read_job_id': 'a'},
      'text': 'On it: reading X now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready.'}
p2 = dict(p1, ts='2026-11-16T22:00:05Z', meta={'read_job_id': 'b'})
check(len(TM.merge_turns([p1], [p2])) == 2, 'two promises of two jobs stay two turns')

print('[G] a download name the link can carry')
from prometheus import file_names as FN   # noqa: E402
check(FN.safe_name('\U0001F465 Subscriber IQ.csv') == 'Subscriber IQ.csv', 'the emoji comes off', FN.safe_name('\U0001F465 Subscriber IQ.csv'))
check(FN.safe_name('Paramount+.csv') == 'Paramount+.csv' and FN.safe_name('\U0001F465.csv') == 'file.csv' and FN.safe_name('') == 'file', 'plain names pass; an empty one gets a name')
check(all(ord(ch) < 128 for ch in FN.disposition('Pok\u00e9mon \u2013 Viewers.csv')), 'the header is plain characters')
src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
check('attachment; filename="{' not in src, 'no signed link in chat.py writes a raw file name into its header')

print('[H] the period note and the People line')
from prometheus import methodology as M   # noqa: E402
check(not M.is_period_comparison('Monthly churn is 7.4%, averaged across the twelve months. 31.7% are on annual plans and bundles.'),
      'a churn definition and a plan term are not a period comparison')
check(not M.is_period_comparison('direct is 68.3% month to month; 5.6 months kept in the first 12 months; month 5 39.2%'), 'tenure months are not periods')
check(M.is_period_comparison('Reach by quarter: Q1 2026 4.1M, Q2 2026 3.9M, against 9.8M for the full year.'), 'quarters beside a full year still are')
check(M.is_period_comparison('Monthly viewers Jan 2026 2.1M, Feb 2026 2.4M; the trailing 12 months total is 8.8M'), 'months beside a trailing 12 months total still are')
res = {'metric_family': 'subscribers', 'window_label': 'Nov 1 2025 to Oct 31 2026', 'subject': 'Premium streaming subscriptions',
       'metrics': [{'label': 'Monthly churn', 'value': 7.4, 'unit': 'pct'}]}
blk = M.ensure('Monthly churn is 7.4% through Amazon across 2,075,611 starts.', res=res, question='churn by signup path')
check('they are not accounts' not in blk and 'Counts are US people' not in blk, 'a subscriptions read carries no People line', blk[-300:])
check(M.ensure(blk, res=res, question='churn by signup path') == blk, 'the block is added once')
res2 = {'metric_family': 'sizing', 'window_label': 'x', 'subject': 'Nike fans', 'metrics': [{'label': 'Fans', 'value': 5, 'unit': 'people'}]}
check('Counts are US people' in M.ensure('Nike fans: 2,075,611 people.', res=res2, question='how many nike fans'), 'a people read keeps its People line')

print('[I] a label, a name and an instruction')
from prometheus import drilldown as DD, read_answers as RA, multi_ask as MA   # noqa: E402
check(DD._label_match(['People who sign up for a streamer through Amazon vs direct cancel 1.7x as often,'], 'X') == 0,
      'the journey row "X" is not found inside "1.7x"')
check(DD._label_match(['saw a retarget'], 'Saw a retarget') == 3 and DD._label_match(['retarget'], 'Saw a retarget') == 2, 'a named row still matches')
check(RA.answer(T1[31]['text'], T1[:31]) is None, '"Answer with one of those two words" is not looked up as a name in the last read')
check('Answer' not in RA._names('Is Off Campus on the list? Answer yes or no.'), 'an instruction word is never a name')
check(MA.names_in('Fire TV', 'devices such as Fire TV, Roku') and not MA.names_in('BET - Paramount+ Viewers', 'Active status for the study'),
      "a part's answer subject is inherited only when the part named it")

print('[J] her own words through the door')
from prometheus import replay   # noqa: E402
items, meta = [], []
for tid, turns, view in (('90ecf3aebf', T1, 'profileIQ'), ('694f7eb888', T2, 'subscriberIQ')):
    for i, t in enumerate(turns):
        if t.get('role') != 'user' or (tid == '90ecf3aebf' and i >= 20):
            continue
        items.append({'question': t['text'], 'view_id': view, 'subject': 'Paramount+' if view == 'profileIQ' else '',
                      'history': [{'role': x.get('role'), 'text': x.get('text') or '', 'meta': x.get('meta') or {}} for x in turns[:i]]})
        meta.append((tid, i))
res = replay.probe_many(items, timeout_s=900)
WANT = {('90ecf3aebf', 4): 'catalog_lookup'}
for (tid, i), r in zip(meta, res):
    route, reply = str(r.get('route') or ''), str(r.get('reply') or '')
    want = WANT.get((tid, i), 'how_computed')
    check(route == want, f"{tid}:{i} routes to {want}", (route, r.get('outcome'), reply[:160]))
    check(not r.get('jobs'), f"{tid}:{i} starts no job", r.get('jobs'))
    low = reply.lower()
    check('$' not in reply and 'ready to run' not in low and 'approve' not in low and 'out of this thread' not in low
          and 'is watched' not in low and 'content map' not in low,
          f"{tid}:{i} carries no price, no draft, no where-to-watch and no pushed-out name", reply[:200])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print(' -', f)
    sys.exit(1)
print('ALL PASS')
