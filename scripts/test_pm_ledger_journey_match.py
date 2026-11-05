#!/usr/bin/env python3
"""A banked journey read is reused only for the same whole journey
(2026-11-05, Jenna: "I keep asking this question and it keeps giving me
the way wrong read").

Her ask: "how many power raising kanan season 5 viewers autoplay'ed into
fightland and then of those what percent completed that entire season of
fightland". Served three times (once on an Approve of the identical
question) with the banked Fightland -> S.W.A.T. Exiles autoplay read from
another thread: the two share a title and a family, and the semantic
match took the stored read. Her follow-up "what percent of those watched
the full season of fightland?" got the same stale read again.

Holds:
  1. the banked Fightland -> S.W.A.T. Exiles entry never satisfies the
     Kanan S5 -> Fightland ask (exact, key, semantic, global scan);
  2. a Kanan S5 -> Fightland ask matches a Kanan S5 -> Fightland entry,
     banked under a key that carries both titles and the metric;
  3. the follow-up binds to the question it follows (its own read), never
     to the newer banked entry that shares the title; with nothing to
     bind to it replays nothing;
  4. a repeat of the identical question reuses the identical numbers;
  5. the wrong side of the journey, a different season, a metric the
     stored read does not carry, and a plain ask against a journey read
     are all refused; plain reads keep their old behavior.

Hermetic: the index is injected into the module cache; no S3.
"""
import copy
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


JENNA = ("how many power raising kanan season 5 viewers autoplay'ed into fightland  and then of those "
         "what percent completed that entire season of fightland")
FOLLOWUP = "what percent of those watched the full season of fightland?"
BANKED_Q = ("what percentage of people who watched fightland on starz then proceeded to watch swat exiles "
            "directly after because it was auto played?")
BANKED_REPLY = (
    "On people who watched a Fightland title on Starz in the window:\n\n"
    "8.8% of Fightland viewers who were still playing episodes once S.W.A.T. Exiles was live rolled straight "
    "into it on the end card autoplay, 73,118 people.\n\n"
    "MEASURED READ (Jul 31 2026 to Oct 29 2026, handoff window Sep 25 to Oct 29 2026)\n"
    "- Fightland US viewers on Starz: 1,418,263 (distinct US people with at least one Fightland play on Starz)\n"
    "- Autoplay handoff into S.W.A.T. Exiles: 73,118 (people whose next Starz play after a Fightland episode was "
    "S.W.A.T. Exiles, started on the end card with no manual title selection in between)\n"
    "- Autoplay handoff rate: 8.8% (autoplay handoff viewers over Fightland viewers still playing after Sep 25 2026)\n")
STALE = {
    'k': 'fightland|engagement|2026-09-25|2026-10-29', 'subject': 'Fightland', 'family': 'engagement',
    'q': BANKED_Q, 'qn': il.normalize_question(BANKED_Q), 'route': 'reasoned_metrics', 'ts': '2026-11-05T21:19:09Z',
    'ws': '2026-09-25', 'we': '2026-10-29', 'wl': 'Jul 31 2026 to Oct 29 2026, handoff window Sep 25 to Oct 29 2026',
    'metrics': [
        {'name': 'fightland_viewers_starz', 'label': 'Fightland US viewers on Starz', 'value': 1418263, 'unit': 'viewers',
         'definition': 'distinct US people with at least one Fightland play on Starz, Jul 31 to Oct 29 2026', 'sample_people': 42991},
        {'name': 'autoplay_handoff_viewers', 'label': 'Autoplay handoff into S.W.A.T. Exiles', 'value': 73118, 'unit': 'viewers',
         'definition': 'people whose next Starz play after a Fightland episode was S.W.A.T. Exiles', 'sample_people': 2216},
        {'name': 'autoplay_handoff_rate', 'label': 'Autoplay handoff rate', 'value': 8.8, 'unit': 'pct',
         'definition': 'autoplay handoff viewers over Fightland viewers still playing after Sep 25 2026'}],
    'anchors': [], 'base': 'Fightland_10_30_2026_01_12.csv', 'cohort': '',
    'universe': 'people who watched a Fightland title on Starz in the window', 'breakdown': None,
    'reply': BANKED_REPLY, 'followups': ['How many of the 73,118 autoplay starts finished episode 1 of S.W.A.T. Exiles?'],
    'derivation': 'matrix read', 'prov': 'operator'}

KANAN_REPLY = (
    "On Power: Raising Kanan season 5 viewers on Starz:\n\n"
    "6.1% of the season 5 viewers who were still playing episodes once Fightland was live rolled straight into "
    "Fightland on the end card autoplay, 61,427 people, and 38.3% of those people went on to finish all eight "
    "Fightland episodes, 23,527 people.\n\n"
    "MEASURED READ (Jul 31 2026 to Oct 29 2026)\n"
    "- Power: Raising Kanan season 5 US viewers on Starz: 1,631,208\n"
    "- Autoplay handoff into Fightland: 61,427\n"
    "- Finished the full Fightland season: 23,527 (38.3% of the autoplay handoff)\n")
KANAN_METRICS = [
    {'name': 'kanan_s5_viewers', 'label': 'Power: Raising Kanan season 5 US viewers on Starz', 'value': 1631208, 'unit': 'viewers',
     'definition': 'distinct US people with at least one season 5 play on Starz in the window'},
    {'name': 'autoplay_into_fightland', 'label': 'Autoplay handoff into Fightland', 'value': 61427, 'unit': 'viewers',
     'definition': 'season 5 viewers whose next Starz play after a Kanan episode was Fightland, started on the end card'},
    {'name': 'finished_fightland_season', 'label': 'Finished the full Fightland season', 'value': 23527, 'unit': 'viewers',
     'definition': 'autoplay handoff viewers who played all eight Fightland episodes'},
    {'name': 'finished_share', 'label': 'Finished share of the handoff', 'value': 38.3, 'unit': 'pct',
     'definition': 'finished the full season over the autoplay handoff'}]


def inject(doc):
    with il._cache_lock:
        il._index_cache['doc'] = doc
        il._index_cache['ts'] = time.time() + 3600


def clear():
    with il._cache_lock:
        il._index_cache['doc'] = None
        il._index_cache['ts'] = 0.0


print("[0] the chains read off the words")
sig = il.journey_signature(JENNA)
check(sig and sig['src'] == 'power raising kanan' and sig['src_season'] == 5 and sig['dst'] == 'fightland',
      f"Jenna's ask: Kanan S5 -> Fightland ({sig})")
check(sig and {'autoplay', 'completion'} <= set(sig['metrics']), "her ask names autoplay and completion")
ssig = il.journey_signature(BANKED_Q)
check(ssig and ssig['src'] == 'fightland' and ssig['dst'] == 'swat exiles' and ssig['platform'] == 'starz'
      and 'autoplay' in ssig['metrics'], f"the banked question: Fightland -> S.W.A.T. Exiles on Starz, autoplay ({ssig})")
fsig = il.journey_signature(FOLLOWUP)
check(fsig and fsig['anaphoric'] and fsig['dst'] == 'fightland' and fsig['metrics'] == ['completion'],
      f"the follow-up: 'of those' -> Fightland, completion ({fsig})")
check(il.journey_signature("how many people watched fightland on starz") is None, "a plain viewership ask is not a chain")
check(il.journey_signature("what toy categories are parents of kids 4-7 who watch paw patrol buying") is None,
      "a purchases ask with an audience clause is not a chain")
check(il.entry_journey(STALE) and il.entry_journey(STALE)['src'] == 'fightland', "an older entry's chain reads off its own question")

print("[1] the banked Fightland -> S.W.A.T. Exiles entry never satisfies the Kanan S5 -> Fightland ask")
doc1 = {'subjects': {'fightland': {'subject': 'Fightland', 'entries': [copy.deepcopy(STALE)]}}, 'updated': STALE['ts']}
inject(doc1)
try:
    for fam in (None, 'viewership', 'engagement'):
        r = il.consult(question=JENNA, metric_family=fam)
        check(r.get('exact') is None, f"consult(metric_family={fam!r}) replays nothing (got {r.get('match')!r})")
    r = il.consult(subject='Power Raising Kanan Season 5 viewers', question=JENNA)
    check(r.get('exact') is None, "consult with the ask's own subject replays nothing")
    check(len(r.get('entries') or []) == 1 and r.get('block'), "the Fightland figures still ride the constraint block")
    check(il.find_semantic([STALE], JENNA) is None, "find_semantic refuses the wrong journey")
    check(il.find_exact([STALE], question=JENNA, key=STALE['k']) is None, "find_exact by key refuses the wrong journey")
    check(il.journey_match(STALE, il.ask_journey(JENNA)) is False, "journey_match: Fightland on the wrong side is not a match")
    r = il.consult(question=BANKED_Q)
    check(r.get('exact') is not None and r.get('match') == 'exact', "the banked question itself still replays its own read")
    r = il.consult(question="how many fightland viewers on starz autoplayed into swat exiles?")
    check(r.get('exact') is not None, f"a rephrase of the same whole journey still replays it (got {r.get('match')!r})")
finally:
    clear()

print("[2] a Kanan S5 -> Fightland ask matches a Kanan S5 -> Fightland entry")
written = []
_orig_record, _orig_sync = il._record_entry_now, il._SYNC_FOR_TESTS
il._record_entry_now = lambda entry: written.append(entry)
il._SYNC_FOR_TESTS = True
try:
    il.persist(subject='Power: Raising Kanan', metric_family='viewership', question=JENNA, route='reasoned_metrics',
               metrics=KANAN_METRICS, window_start='2026-07-31', window_end='2026-10-29',
               window_label='Jul 31 2026 to Oct 29 2026', reply=KANAN_REPLY,
               universe='Power: Raising Kanan season 5 viewers on Starz')
finally:
    il._record_entry_now, il._SYNC_FOR_TESTS = _orig_record, _orig_sync
check(len(written) == 1, "the fresh read banked")
KANAN = written[0]
check('|j:power_raising_kanan_s5>fightland:' in KANAN['k'] and 'autoplay' in KANAN['k'] and 'completion' in KANAN['k'],
      f"its key carries both titles and the metric: {KANAN['k']}")
check((KANAN.get('journey') or {}).get('src') == 'power raising kanan' and KANAN['journey'].get('dst') == 'fightland',
      "the chain is banked on the entry")
# the stale entry is NEWER than the Kanan read in this bucket layout
KANAN['ts'] = '2026-11-05T23:10:00Z'
NEWER_STALE = dict(copy.deepcopy(STALE), ts='2026-11-05T23:20:00Z')
doc2 = {'subjects': {
    'fightland': {'subject': 'Fightland', 'entries': [NEWER_STALE]},
    'power_raising_kanan': {'subject': 'Power: Raising Kanan', 'entries': [KANAN]},
}, 'updated': '2026-11-05T23:20:00Z'}
inject(doc2)
try:
    r = il.consult(question=JENNA)
    check(r.get('exact') is KANAN or (r.get('exact') or {}).get('k') == KANAN['k'],
          f"her question replays the Kanan S5 -> Fightland read (match={r.get('match')!r}, subject={r.get('subject')!r})")
    r = il.consult(subject='Fightland', question=JENNA)
    check((r.get('exact') or {}).get('k') == KANAN['k'],
          f"even consulted under 'Fightland', the Kanan read wins and the stale one never serves (match={r.get('match')!r})")
    r = il.consult(question="how many Power: Raising Kanan S5 viewers on Starz autoplayed into Fightland and what share of them finished the whole Fightland season")
    check((r.get('exact') or {}).get('k') == KANAN['k'], f"a rephrase of the same whole journey replays it ({r.get('match')!r})")

    print("[3] the follow-up binds to the question it follows")
    hist = [{'role': 'user', 'text': JENNA}, {'role': 'agent', 'text': KANAN_REPLY}, {'role': 'user', 'text': FOLLOWUP}]
    r = il.consult(question=FOLLOWUP, history=hist)
    check((r.get('exact') or {}).get('k') == KANAN['k'],
          f"'what percent of those watched the full season' after her question -> her question's own read ({r.get('match')!r})")
    r = il.consult(question=FOLLOWUP, prior_question=JENNA)
    check((r.get('exact') or {}).get('k') == KANAN['k'], "the same through prior_question")
    r = il.consult(question=FOLLOWUP)
    check(r.get('exact') is None, "with nothing to bind to, the follow-up replays nothing (never the newer Fightland entry)")
    r = il.consult(subject='Fightland', question=FOLLOWUP)
    check(r.get('exact') is None, "nor under the Fightland bucket")
    r = il.consult(question="of those, what percent completed the entire season?", history=hist[:2])
    check((r.get('exact') or {}).get('k') == KANAN['k'], "a follow-up that names no title still binds through the prior question")
    other_hist = [{'role': 'user', 'text': BANKED_Q}, {'role': 'agent', 'text': BANKED_REPLY}]
    r = il.consult(question="what percent of those watched the full season of swat exiles?", history=other_hist)
    check(r.get('exact') is None, "a completion follow-up on the Fightland -> S.W.A.T. Exiles thread does not get the autoplay-only read")

    print("[4] a repeat of the identical question reuses the identical numbers")
    a = il.consult(question=JENNA)
    b = il.consult(question=JENNA)
    check(a.get('exact') and b.get('exact') and a['exact']['metrics'] == b['exact']['metrics'] == KANAN['metrics']
          and a['exact']['reply'] == KANAN_REPLY, "same entry, same metrics, same reply")

    print("[5] what is refused")
    r = il.consult(question="how many fightland viewers autoplayed into power raising kanan season 5?")
    check(r.get('exact') is None, "the reverse journey (Fightland -> Kanan) matches neither entry")
    r = il.consult(question="how many power raising kanan season 4 viewers autoplayed into fightland?")
    check(r.get('exact') is None, "another season of the source is not the same journey")
    r = il.consult(question="how many power raising kanan season 5 viewers discovered fightland through search?")
    check(r.get('exact') is None, "a metric the stored read does not carry (discovery) is not served from it")
    r = il.consult(question="how many power raising kanan season 5 viewers autoplayed into fightland?")
    check((r.get('exact') or {}).get('k') == KANAN['k'], "a subset of the stored metrics (autoplay only) is served from the fuller read")
    r = il.consult(question="how many people watched fightland on starz?")
    check(r.get('exact') is None, "a plain viewership ask never replays a journey read")
    check(il.journey_match(KANAN, il.ask_journey("how many power raising kanan season 5 viewers autoplayed into fightland on netflix")) is False,
          "a different platform is not the same journey")
finally:
    clear()

print("[6] plain reads keep their behavior; an unresolved follow-up banks no chain")
PLAIN = {'k': 'landman|viewership|2026-01-01|2026-10-31', 'subject': 'Landman', 'family': 'viewership',
         'q': 'how many people watched landman this year', 'qn': il.normalize_question('how many people watched landman this year'),
         'route': 'reasoned_metrics', 'ts': '2026-11-01T00:00:00Z', 'ws': '2026-01-01', 'we': '2026-10-31', 'wl': '',
         'metrics': [{'name': 'viewers', 'label': 'Landman US viewers', 'value': 12345679, 'unit': 'viewers', 'definition': ''}],
         'anchors': [], 'base': '', 'cohort': '', 'universe': '', 'breakdown': None,
         'reply': 'On Landman viewers: 12,345,679 people watched Landman this year.', 'followups': [], 'derivation': '', 'prov': 'operator'}
inject({'subjects': {'landman': {'subject': 'Landman', 'entries': [PLAIN]}}, 'updated': PLAIN['ts']})
try:
    r = il.consult(question='how many people watched landman this year')
    check(r.get('match') == 'exact', "a plain exact repeat still replays")
    check(il.journey_match(PLAIN, None) is True, "a plain entry is unconstrained for a plain ask")
    check(il.journey_match(PLAIN, il.ask_journey(JENNA)) is False, "a plain entry never answers a journey ask")
finally:
    clear()
e_an = il.make_entry(subject='Fightland', metric_family='viewership', question=FOLLOWUP, route='reasoned_metrics',
                     metrics=KANAN_METRICS[2:], reply='38.3% finished.')
check('journey' not in e_an and '|j:' not in e_an['k'], "an entry made from a bare 'of those' question carries no chain")
e_res = il.make_entry(subject='Fightland', metric_family='viewership', question=FOLLOWUP, route='reasoned_metrics',
                      metrics=KANAN_METRICS[2:], reply='38.3% finished.', journey=il.ask_journey(FOLLOWUP, prior_question=JENNA))
check((e_res.get('journey') or {}).get('src') == 'power raising kanan' and '|j:power_raising_kanan_s5>fightland:completion' in e_res['k'],
      f"the same entry with the chain resolved from the prior question banks it: {e_res['k']}")
check(il._prior_user_question([{'role': 'user', 'text': JENNA}, {'role': 'agent', 'text': 'x'}, {'role': 'user', 'text': FOLLOWUP}], FOLLOWUP) == JENNA,
      "the prior user question skips the ask's own turn")

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall green")
