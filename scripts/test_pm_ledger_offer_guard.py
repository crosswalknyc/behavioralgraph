#!/usr/bin/env python3
"""A priced quote is never an answer the ledger replays (2026-11-05).

Liz Huszarik, with the Music to Long Form study open on Digital
Journey IQ, asked "in the Music to Longform data set, did the
Instagram Social touchpoints convert any movie viewing." The reply
was "I can pull that read on 🗺️ Digital Journey IQ. Pulling this data
will cost $100.": a banked ledger entry (distilled from a read job
whose stored reply was the offer line, filed under the tab label with
its icon) found by the semantic match and replayed as the answer.

Holds:
  1. offer copy is recognized (the offer sentence, the cost line, the
     staff chip, the bare Approve chip) and ordinary reads are not;
  2. make_entry blanks an offer reply and strips the tab icon from the
     subject; persist banks nothing for an offer with no metrics;
  3. consult() on a bucket that still holds a poisoned entry never
     returns it as `exact`, by question match or by semantic match.

Hermetic: the index is injected into the module cache; no S3.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import insights_ledger as il  # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


OFFER = "I can pull that read on 🗺️ Digital Journey IQ. Pulling this data will cost $100."
LIZ = "in the Music to Longform data set, did the Instagram Social touchpoints convert any movie viewing."

print("[1] offer copy is recognized; reads are not")
check(il.is_offer_reply(OFFER), "the offer sentence + cost line")
check(il.is_offer_reply("I can size the Babylon 5 audience on Prime Video. Pulling this data will cost $100."), "a sizing quote")
check(il.is_offer_reply("Approve ($100, not billed to a staff account)"), "the staff chip text")
check(il.is_offer_reply("Approve"), "the bare Approve chip")
check(il.is_offer_reply("It runs $500 for the year."), "the 'It runs $' line")
check(il.is_retired_surface_reply(OFFER), "is_retired_surface_reply covers offer copy")
check(not il.is_offer_reply("On the Music to Long Form study: 2,498,420 of 7,022,543 watched a film, 35.6%."), "a read with figures is not an offer")
check(not il.is_offer_reply("Pulling the figures from the study: social carried 7,022,543 people."), "'pulling' in prose is not the cost line")

print("[2] make_entry / persist never bank a quote; the subject loses its icon")
e = il.make_entry(subject='🗺️ Digital Journey IQ', metric_family='audience', question=LIZ, route='reasoned_metrics',
                  metrics=[], reply=OFFER, provenance='distilled')
check(e['reply'] == '', "offer reply blanked on the entry")
check(e['subject'] == 'Digital Journey IQ', f"subject stripped of the icon: {e['subject']!r}")
check(e['k'].startswith('digital_journey_iq|'), f"key still folds to the same bucket: {e['k']}")

written = []
_orig_record = il._record_entry_now
il._record_entry_now = lambda entry: written.append(entry)
_orig_sync = il._SYNC_FOR_TESTS
il._SYNC_FOR_TESTS = True
try:
    il.persist(subject='🗺️ Digital Journey IQ', metric_family='audience', question=LIZ, route='reasoned_metrics',
               metrics=[], reply=OFFER, provenance='distilled')
    check(not written, "persist wrote nothing for an offer with no metrics")
    il.persist(subject='Music to Long Form', metric_family='audience', question='how many watched after social',
               route='journey_file', metrics=[{'name': 'watched', 'value': 2498420, 'unit': 'count'}],
               reply='2,498,420 watched a film.')
    check(len(written) == 1 and written[0]['reply'].startswith('2,498,420'), "a real read still banks")
finally:
    il._record_entry_now = _orig_record
    il._SYNC_FOR_TESTS = _orig_sync

print("[3] consult never replays a poisoned entry")
poison = {
    'k': 'digital_journey_iq|audience|any|any', 'subject': '🗺️ Digital Journey IQ', 'family': 'audience',
    'q': LIZ, 'qn': il.normalize_question(LIZ), 'route': 'reasoned_metrics', 'ts': '2026-11-05T05:11:29Z',
    'ws': '', 'we': '', 'wl': '', 'metrics': [], 'anchors': [], 'base': '', 'cohort': '', 'breakdown': None,
    'reply': OFFER, 'followups': [], 'derivation': 'distilled from read job b44c7f0580c7', 'prov': 'distilled'}
semantic = dict(poison, q='Try it as one dimension only. For the Music to Long Form study (61 titles), the series viewers with no music touch cut, split by Income only',
                qn=il.normalize_question('Try it as one dimension only. For the Music to Long Form study (61 titles), the series viewers with no music touch cut, split by Income only'),
                ts='2026-11-05T05:11:30Z')
doc = {'subjects': {'digital_journey_iq': {'subject': '🗺️ Digital Journey IQ', 'entries': [semantic, poison]}}, 'updated': '2026-11-05T05:11:30Z'}
with il._cache_lock:
    il._index_cache['doc'] = doc
    il._index_cache['ts'] = time.time() + 3600
try:
    res = il.consult(subject='🗺️ Digital Journey IQ', question=LIZ)
    check(res.get('exact') is None, f"exact-question match on the poisoned entry is refused (got {res.get('match')!r})")
    res2 = il.consult(subject='🗺️ Digital Journey IQ', question='did the social touchpoints convert any movie viewing in the Music to Longform set')
    check(res2.get('exact') is None, f"semantic match on the poisoned bucket is refused (got {res2.get('match')!r})")
    check(il.find_exact([poison, semantic], question=LIZ) is None, "find_exact skips offer replies")
    check(il.find_semantic([poison, semantic], LIZ) is None, "find_semantic skips offer replies")
    gk, ge = il._find_exact_anywhere(doc['subjects'], LIZ)
    check(ge is None, "the global exact scan skips offer replies")
finally:
    with il._cache_lock:
        il._index_cache['doc'] = None
        il._index_cache['ts'] = 0.0

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall green")
