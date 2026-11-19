#!/usr/bin/env python3
"""A delivered figure mirrored under two subjects is never redrawn (2026-11-19).

The Kansas City Chiefs stat sheet banked Kelce's audience outside the team
(89,523,717 people) on both the Travis Kelce bucket and the Kansas City
Chiefs bucket. The persist-time coherence pass read the second copy as a
cross-subject repeated-value fingerprint and redrew it to 89,792,288, so
the stored metric contradicted the stored reply and the sheet the reader
holds. A delivered figure (delivered_artifact or the decision of record)
is what the reader already has; the fingerprint check applies to
generated reads only.

Hermetic: no S3, no model. Run from bg-webapp/:
    python3 scripts/test_insights_ledger_delivered_mirror.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import insights_ledger as il  # noqa: E402

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


def _doc_with_kelce_anchor(value):
    kelce = il.make_entry(
        subject='Travis Kelce', metric_family='audience',
        question='delivered anchors: test sheet [Travis Kelce]',
        route=il.ANCHOR_ROUTE,
        metrics=[{'name': 'kelce_not_chiefs_people', 'label': 'Kelce audience outside the Chiefs',
                  'value': value, 'unit': 'people'}],
        window_start='2025-11-19', window_end='2026-11-19',
        provenance=il.PROV_DELIVERED)
    return {'subjects': {
        il.subject_key('Travis Kelce'): {'subject': 'Travis Kelce', 'entries': [kelce]},
        il.subject_key('Kansas City Chiefs'): {'subject': 'Kansas City Chiefs', 'entries': []},
    }}


def _chiefs_entry(value, provenance):
    return il.make_entry(
        subject='Kansas City Chiefs', metric_family='audience',
        question="How does Travis Kelce's audience compare with the Kansas City Chiefs?",
        route='delivered_read',
        metrics=[{'name': 'kelce_not_chiefs_people', 'label': 'Kelce audience outside the Chiefs',
                  'value': value, 'unit': 'people'},
                 {'name': 'kelce_not_chiefs_female_share_pct', 'label': 'Female share of that group',
                  'value': 68.1206, 'unit': 'pct'}],
        window_start='2025-11-19', window_end='2026-11-19',
        provenance=provenance)


def main():
    value = 89_523_717
    skey = il.subject_key('Kansas City Chiefs')

    # 1. Delivered provenance: the mirrored figure stays exactly as delivered.
    doc = _doc_with_kelce_anchor(value)
    e = _chiefs_entry(value, il.PROV_DELIVERED)
    il._enforce_ledger_coherence(doc, skey, e)
    got = {m['name']: m['value'] for m in e['metrics']}
    check(got['kelce_not_chiefs_people'] == value,
          f"delivered mirror kept exact ({got['kelce_not_chiefs_people']})")
    check(got['kelce_not_chiefs_female_share_pct'] == 68.1206,
          "delivered share untouched")

    # 2. Decision of record: same, the record never moves.
    doc = _doc_with_kelce_anchor(value)
    e = _chiefs_entry(value, il.PROV_RECORD)
    il._enforce_ledger_coherence(doc, skey, e)
    check({m['name']: m['value'] for m in e['metrics']}['kelce_not_chiefs_people'] == value,
          "record mirror kept exact")

    # 3. A generated read (operator provenance) still gets the fingerprint redraw.
    doc = _doc_with_kelce_anchor(value)
    e = _chiefs_entry(value, il.PROV_OPERATOR)
    il._enforce_ledger_coherence(doc, skey, e)
    redrawn = {m['name']: m['value'] for m in e['metrics']}['kelce_not_chiefs_people']
    check(redrawn != value, f"generated read redrawn ({redrawn})")
    check(isinstance(redrawn, int) and redrawn % 10 != 0, "redrawn value is a messy integer")

    # 4. A generated read with a different name never collides in the first place.
    doc = _doc_with_kelce_anchor(value)
    e = il.make_entry(
        subject='Kansas City Chiefs', metric_family='audience',
        question='different name', route='delivered_read',
        metrics=[{'name': 'kelce_outside_pool_people', 'label': 'x', 'value': value, 'unit': 'people'}],
        provenance=il.PROV_OPERATOR)
    il._enforce_ledger_coherence(doc, skey, e)
    check(e['metrics'][0]['value'] == value, "distinct metric name is not a collision")

    print('\n' + ('ALL OK' if not FAILS else f'{len(FAILS)} FAIL(S): {FAILS}'))
    return 0 if not FAILS else 1


if __name__ == '__main__':
    sys.exit(main())
