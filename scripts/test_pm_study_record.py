#!/usr/bin/env python3
"""A question about a published study is answered from the study
(Cara Dorfman at Amazon, 2026-11-16; Jenna the same day: "answer this and
make sure it gets done correctly for her").

Cara was checking the Amazon vs direct issue of The Read against her own
numbers. What she got, on the second half of that day:

  * ten numbered methodology questions were split apart; one came back as
    a row of a Disney journey, the rest ran as fresh reads on "Fire TV";
  * the reads drew figures the issue never carried (month-start account
    counts, a monthly-only curve, survival for months 5 to 12), and the
    churn she rebuilt from them did not tie to the published rates;
  * "Do not build anything" drew a persona fork, a profile draft with an
    Approve line, "Nothing is queued yet" and five read jobs;
  * "Answer with one of those two words" came back as "Answer is not in
    that read";
  * the issue's summary was replayed to questions it does not answer.

What holds it: prometheus.study_record answers each part from the record
(the packaged copy of the decision of record), in the fixed words of
prometheus.study_statements, says so where the study is silent, names a
quoted figure the study does not hold, and starts nothing.
prometheus.no_build honors "Do not build anything" for the whole ask.

Hermetic: no model, no S3, no app module. Her exact words, thread
90ecf3aebf turns 20, 22, 29, 31, 33 and 35.
"""
from __future__ import annotations

import json
import os
import re
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

FAILS = []


def check(ok, name, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f"  ({str(detail)[:500]})"))
    if not ok:
        FAILS.append(name)


T = json.load(open(os.path.join(HERE, 'fixtures', 'cara_dorfman_2026_11_16.json'), encoding='utf-8'))['threads']['90ecf3aebf']
TURNS = (20, 22, 29, 31, 33, 35)

from prometheus import study_record as SR, study_statements as ST, no_build as NB   # noqa: E402

print('[A] the packaged record is the banked record')
import build_study_record_pack as PACK   # noqa: E402
import bank_the_read_amazon_vs_direct as BANK   # noqa: E402
check(PACK.main(['--check']) == 0, 'the packaged file is what the bank script produces')
REC = next(r for r in SR.records() if r['id'] == BANK.CAMPAIGN_ID)
check(REC['subject'] == BANK.SUBJECT and REC['metrics'] == BANK.record_block()['metrics'], 'same subject and the same metrics, definitions included')
check(REC['rows']['amazon']['All ten services']['starts'] + REC['rows']['direct']['All ten services']['starts'] == BANK.TOTAL_STARTS,
      'the two paths add to the subscriptions followed')
check(SR._values(REC)['curve_a'] == '72/55/45' and SR._values(REC)['curve_d'] == '82/70/61', 'the rounded curves are the issue\'s own', SR._values(REC))

HELD = SR._record_numbers(REC)


def figures_in(text):
    """Counts and one-decimal percentages a reply states."""
    out = set(re.findall(r"(?<![\d,.$])\d{1,3}(?:,\d{3})+(?![\d,]*\.\d)", text))
    out |= {m + '%' for m in re.findall(r"(?<![\d.,$])(\d{1,3}\.\d{1,2})\s?%", text)}
    return out


BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'clickstream', 'anchor', 'calibrat',
          'reasoned', 'derived', 'research', 'panel', 'antenna', 'nielsen', 'confidence', 'tier 1', 'tier 2',
          '\u2014', '\u2013')
STARTED = ('on it', 'ready to run', 'approve to run', 'whole service', 'persona', 'which subject should', 'nothing is queued',
           'will land right here', 'working on it', 'pulling this data will cost', '$1', '$3', '$5')

print('[B] her words, answered from the record')
REPLIES = {}
for i in TURNS:
    q = T[i]['text']
    r = SR.respond(q, T[:i])
    check(bool(r) and r.get('study_record', {}).get('record') == BANK.CAMPAIGN_ID, f"turn {i}: answered from the record", r and r.get('study_record'))
    if not r:
        continue
    reply = r['reply']
    REPLIES[i] = reply
    sr = r['study_record']
    n_parts = len(SR.parts_of(q))
    check(sr['parts'] == n_parts and 'not_matched' not in sr['topics'], f"turn {i}: every one of its {n_parts} parts has an answer or a plain 'the study does not carry it'", sr)
    if n_parts > 1:
        for n in range(1, n_parts + 1):
            check(re.search(rf"(?m)^{n}\. \S", reply) is not None, f"turn {i}: part {n} is answered under its number")
    asked = figures_in(q)
    extra = {f for f in figures_in(reply) if f not in asked and f.rstrip('%') not in HELD and f.replace(',', '') not in HELD and f not in HELD}
    check(not extra, f"turn {i}: no figure outside the record", sorted(extra))
    low = reply.lower()
    check(not [b for b in BANNED if b in low] and not re.search(r"\bsamples?\b", low),
          f"turn {i}: no internal vocabulary, no source names, no dashes", [b for b in BANNED if b in low] + re.findall(r"\bsamples?\b", low))
    check(not [s for s in STARTED if s in low], f"turn {i}: nothing started, drafted, priced or asked toward a build", [s for s in STARTED if s in low])
    check(not r.get('read_job_id') and not r.get('panel_offer') and not r.get('draft') and r.get('action') == 'answer', f"turn {i}: an answer payload and nothing else")
    if NB.asked(q):
        check('Nothing was built for this.' in reply, f"turn {i}: says nothing was built")

print('[C] the answers themselves')
r20, r29, r31, r33, r35 = (REPLIES.get(i, '') for i in (20, 29, 31, 33, 35))
check('1. Active status\nBilling. ' in r20, 'active status leads with Billing', r20[:400])
check('2. Cohorts\nNew signups. ' in r20 and '71.8%, 55.2% and 44.7% through Amazon' in r20 and '72/55/45' in r20, 'cohorts: new signups, the published curve')
check('does not say how an account that cancels and later returns is treated' in r20 and '18.9% of direct cancellers' in r20, 'reactivations: silent, with the winback line the study does carry')
APPROVED = ST.SESSION_CREDIT.format(service='Paramount+')
check(APPROVED == ("Prime Video. The session is counted in the app where it happens. The title played is still credited to the title, and the "
                   "subscription is counted as a Paramount+ subscription on the Amazon path, because the signup and billing opened in Amazon's store."),
      'the approved session-credit statement is word for word')
check(APPROVED in r20 and APPROVED in r29 and APPROVED in r31, 'and it is what every ask about the Prime Video app gets')
check(r31.split('\n\n', 1)[1] == APPROVED, 'turn 31 is that statement and nothing else', r31)
check('does not describe how signups and viewing are observed by device' in r20, 'device coverage: not in the study, said plainly')
check('6. Headline churn\nAll active subscriptions on each path, not new signups only.' in r20 and 'before November 2025' in r20, 'headline churn: the base and the published definition')
check('7. Samplers\nCancelled. ' in r20, 'samplers: gone means cancelled')
check('does not carry a monthly-only curve' in r20 and 'does not carry a monthly-only curve' in r29 and 'does not carry a monthly-only curve' in r35,
      'a monthly-only curve is not in the study, each time it is asked for')
check('carries survival at day 30, day 60 and day 90 only' in r20 and 'does not carry survival values for later months' in r20, 'months 1 to 11: not in the study')
check('It does not publish the counts behind the projection.' in r20 and '327,948 on the Amazon path and 652,834 direct' in r20,
      'sample: projected subscriptions, and the counts behind them are not published')
check(r20.index('It does not publish the counts') < r20.index('No. A paying subscriber with no sessions'), 'the rider about no sessions is answered after the question it follows')
check('does not carry month-start active account counts' in r29 and 'will not tie to its rates' in r29, 'reconcile: the study carries no month-start counts')
check(all(f in r29 for f in ('1,214,863', '1,702,449', '2,836,517', '3,491,284')) and 'Not figures from the published study:' in r29,
      'the four account figures are named as not from the study')
check('Not figures from the published study: 18.4%.' in r29, 'so is the 18.4%')
check('2,075,611' not in r29.split('Not figures from the published study:')[1].split('\n')[0], 'a figure the study does hold is never named as foreign')
check('direct means the subscription was signed up for and is paid on the service\'s own site or app: 4,138,776 starts' in r33
      and 'does not split direct into further signup paths' in r33, 'what direct includes: the definition, and no further split')
check('carries no separate counts for Fire TV app-store billing, Amazon retail or Alexa' in r35, 'what via Amazon includes: no further split')
check('Service | Path | Day 30 | Day 60 | Day 90\nHBO Max | Amazon | 74.1% | 56.6% | 43.7%' in r35, 'part 1: the day 30, 60, 90 table for the services named')
p1 = r35.split('\n2. ')[0]
check('Apple TV+' not in p1 and 'BET+' not in p1 and 'All ten services' not in p1 and p1.count(' | Amazon | ') == 8, 'only the eight services she named', p1[:300])
check('Service | Path | Starts | Monthly churn | Day 30 | Day 60 | Day 90 | Annual plans and bundles' in r35
      and 'Acorn TV | Direct | 106,217 | 4.5% | 80.1% | 67.2% | 60.2% | 29.8%' in r35
      and 'All ten services | Amazon | 2,075,611 | 7.4% | 71.8% | 55.2% | 44.7% | 7.7%' in r35, 'part 3: all ten services, both paths, the columns she listed')
check('The plan column is annual plans and bundles. The study has no separate prepaid line.' in r35, 'the plan column is named for what it is')
check('free trial or a discounted first month' in r35 and 'does not carry a plan tier mix' in r35 and 'does not name the services they start' in r35,
      'trials, tiers and next services: not in the study')
check('are in the table under part 3' in r35, 'the starts by service are pointed at, not typed twice')
check(T[20]['text'] == T[22]['text'] and SR.respond(T[22]['text'], T[:22])['reply'] == r20, 'the same message sent again gets the same answer')

print('[D] what the lane leaves alone, and what it says when there is no record')
for q in ('churn through Amazon Channels vs direct', 'what is the churn rate for subscribers who sign up through Amazon versus direct',
          'In the Amazon vs direct study what is the first-year value of a subscriber?'):
    check(SR.respond(q, None) is None, f"a general ask keeps the stored summary: {q[:50]}")
for q in ('Compare Nike sales on Amazon vs direct', 'what is Netflix churn', 'how many people watched The Bear', 'the issue is the deck is too long',
          'can you find a published study about streaming churn?', 'Build a profile of Amazon Channels subscribers'):
    check(SR.respond(q, None) is None, f"not a study question: {q[:50]}")
r = SR.respond('Per service day 90 survival, Amazon Channels vs direct', None)
check(bool(r) and 'Service | Path | Day 90' in r['reply'] and 'STARZ | Amazon | 40.3%' in r['reply'], "the record's own chip is answered with the table")
r = SR.respond('How is churn defined in the Amazon vs direct study?', None)
check(bool(r) and 'subscriptions cancelled in a month over subscriptions active on that path' in r['reply'], 'a definition is the record\'s definition')
H = [{'role': 'user', 'text': 'In the published Amazon vs direct study, how is churn defined?'},
     {'role': 'agent', 'text': 'From the Amazon vs direct issue of The Read (Nov 1 2025 to Oct 31 2026).\n\nAll active subscriptions on each path.'}]
r = SR.respond('and for STARZ, day 90?', H)
check(bool(r) and 'STARZ | Amazon | 40.3%' in r['reply'] and 'STARZ | Direct | 57.1%' in r['reply'], 'a follow-up in a thread about the study stays with the study')
r = SR.respond('In the Amazon vs direct study, how many angels fit on a pin? Do not build anything.', None)
check(bool(r) and ST.NOT_MATCHED in r['reply'], 'a question the record cannot place says so, and makes nothing up')
check(SR.respond('can you run the same study for Netflix against Hulu?', H) is None, 'a new ask in a thread about the study is not answered as the study')
r = SR.respond('In the published creator economy study, how is a creator counted? Do not build anything.', None)
check(bool(r) and r['reply'].startswith(SR.UNKNOWN) and 'Nothing was built for this.' in r['reply'], 'a published study we do not hold: one plain line')

print('[E] a decision of record in the ledger answers the same way')
import insights_ledger as IL   # noqa: E402
IL.use_test_ledger({})
keys = IL.bank_record(
    subject='Creator economy, paid subscriptions to creators', source='newsletter the-read-creator-test',
    metrics=[{'name': 'paying_fans', 'label': 'Paying fans of creators', 'value': 3184527, 'unit': 'people',
              'definition': 'US people with at least one paid creator subscription in the window'},
             {'name': 'monthly_spend', 'label': 'Average monthly spend per paying fan', 'value': 11.42, 'unit': 'usd',
              'definition': 'paid creator subscriptions per paying fan, at list price'}],
    question='Three million people now pay a creator every month.', reply='On the creator study: 3,184,527 paying fans.',
    window_start='2025-11-01', window_end='2026-10-31', window_label='Nov 1 2025 to Oct 31 2026',
    subject_aliases=['creator economy paid subscriptions'], asker='jenna')
SR._PACKS['cache'] = None
if keys:
    q = 'In the published creator economy paid subscriptions study, what is the average monthly spend per paying fan, and how many are on annual plans? Do not build anything.'
    r = SR.respond(q, None)
    check(bool(r) and '$11.42' in r['reply'] and 'Nothing was built for this.' in r['reply'], 'the ledger record answers from its own metric and definition', r and r['reply'])
    r = SR.respond('1. In the published creator economy paid subscriptions study, how many paying fans are there?\n\n2. In that study, what share churn each month?', None)
    check(bool(r) and '3,184,527' in r['reply'] and ST.NOT_MATCHED in r['reply'], 'and says so for the part it does not hold', r and r['reply'])
else:
    print('  skip the ledger refused the test record (banking is gated in this process)')
IL.use_test_ledger({})

print('[F] "Do not build anything" is honored')
for q in ('Do not build anything. What is X?', 'Do not run a build', "don't run anything, just answer", 'Text table only. Give day 90',
          'This is a methodology question about the study', 'no build please. what is churn', 'do not start a read', 'Please do not build.'):
    check(NB.asked(q), f"forbids work: {q}")
for q in ("don't make it too long", "Don't run it yet", 'do not start until I say go', 'how do I build a profile', 'run the read',
          'build a profile of Nike but do not include kids', 'just tell me the churn', T[29]['text'],
          "Don't build a new profile. Answer this from the existing ones: who skews older, Gunna or Drake?"):
    check(not NB.asked(q), f"does not: {q[:50]}")
for raw in ({'action': 'build_profile', 'route_hint': 'interpret', 'reply': ''}, {'reply': 'x', 'panel_offer': {'subject': 'y'}},
            {'reply': 'On it: reading Paramount+ now.', 'read_job_id': 'abc'}, {'reply': 'New profile, ready to run.\n\nApprove to run, or send another message to adjust.'},
            {'reply': "Reply 'whole service' to build all Paramount+ viewers"}, {'reply': 'Which subject should this be on? Name the show, brand or person'},
            {'error': 'Nothing is queued yet. Tell me who or what you want pulled, a person, a brand or a title, and I will draft the brief for you to approve.'}):
    check(NB.refuses(raw), f"never leaves on such an ask: {str(raw)[:70]}")
for raw in ({'reply': 'Paramount+ reads 48,572,906 US subscribers.', 'action': 'answer'}, NB.hold(), {'reply': 'x', 'study_record': {'record': 'r'}}):
    check(not NB.refuses(raw), f"an answer passes: {str(raw)[:60]}")

print('[G] her words through the door')
from prometheus import replay   # noqa: E402
items = [{'question': T[i]['text'], 'view_id': 'profileIQ', 'subject': 'Paramount+',
          'history': [{'role': x.get('role'), 'text': x.get('text') or '', 'meta': x.get('meta') or {}} for x in T[:i]]} for i in TURNS]
items.append({'question': 'Do not build anything. Which grocery chains do Paramount+ viewers in Ohio shop at most?', 'view_id': '', 'subject': '', 'history': []})
items.append({'question': 'Do not build anything. Build me a profile of Ohio grocery shoppers.', 'view_id': '', 'subject': '', 'history': []})
items.append({'question': 'In the published creator economy study, how is a creator counted? Do not run a build.', 'view_id': '', 'subject': '', 'history': []})
res = replay.probe_many(items, timeout_s=900)
for i, r in zip(TURNS, res):
    check(r.get('route') == 'study_record' and r.get('outcome') == 'answered', f"turn {i}: route study_record", (r.get('route'), r.get('outcome')))
    check(not r.get('jobs'), f"turn {i}: zero jobs", r.get('jobs'))
    check(str(r.get('reply') or '') == REPLIES.get(i), f"turn {i}: what leaves the door is the lane's answer, unchanged by any scrub",
          [ln for ln in str(r.get('reply') or '').splitlines() if ln not in (REPLIES.get(i) or '').splitlines()][:3])
    check(not [o for o in (r.get('options') or []) if str(o).lower() in ('approve', 'cancel', 'accept', 'decline')], f"turn {i}: no approve chip", r.get('options'))
for r, name in zip(res[len(TURNS):len(TURNS) + 2], ('a question no lane holds', 'an order under a do-not-build')):
    check(not r.get('jobs') and str(r.get('reply') or '') == NB.REPLY, f"{name}: the one plain line, nothing started",
          (r.get('route'), r.get('jobs'), str(r.get('reply') or '')[:200]))
r = res[-1]
check(not r.get('jobs') and str(r.get('reply') or '').startswith(SR.UNKNOWN), 'a published study we do not hold: the plain line at the door, nothing started',
      (r.get('route'), str(r.get('reply') or '')[:200]))

print('[H] wiring')
from prometheus import service as SV   # noqa: E402
order = list(SV.LANE_ORDER)
check(SV.lane_markers() == order, 'the lane markers follow LANE_ORDER')
check('study_record' in order and all(order.index('study_record') < order.index(x) for x in
                                     ('offer_acceptance', 'read_brief', 'reconcile', 'continuation', 'standing', 'multi_ask', 'sizing_quote', 'reader', 'surface_decision', 'work_gate')),
      'study_record runs before every lane that can draft, price, split or start a read')
check('study_record' in SV.DETERMINISTIC_LANES and 'study_record' in SV.NEVER_PRICED_LANES, 'deterministic and never priced')
chat = _legacy_view()
a = chat.find('_study_record.respond(text, history)')
b = chat.find("'reply': exact['reply'],", a)
check(a > 0 and 0 < b - a < 2500, 'the stored summary of a record is replayed only after the record lane has left the ask', (a, b))
a, b = chat.find('_no_build.active()'), chat.find('_pm_read_inflight_mark(_pm_user, text, job_id, _promised)')
check(0 < a < b, 'no read job is started on a do-not-build ask')
src = open(os.path.join(ROOT, 'prometheus', 'study_record.py'), encoding='utf-8').read()
check('persist(' not in src and 'bank_record' not in src and 'claude' not in src.lower() and 'enqueue' not in src,
      'the lane banks nothing, calls no model and queues nothing')

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print(' -', f)
    sys.exit(1)
print('ALL PASS')
