#!/usr/bin/env python3
"""Bank the segment reads of record into the insights ledger (Jenna
2026-11-11: "give me this answer and log it in prometheus brain so if
anyone asks again it's classified").

A segment read is a demographic segment sized in the data and in the US,
with a brand's touchpoint split when one was asked for. The figures,
definitions and windows live in prometheus/segment_reads.json; this
script writes each read to the insights ledger as a decision of record
(route decision_of_record_segment_read, cohort 'segment:<key>') with the
natural asks as exact questions, the subject's aliases, and the internal
trail (anchors, derivation) on the entry. The trail never renders: the
ledger's prompt block and every reply are built from the metrics, the
definitions and the reply text only.

Idempotent: re-banking a read replaces its earlier rows (same key and
question). Dry run by default.

    python3 scripts/bank_segment_reads.py            # show what would be written
    python3 scripts/bank_segment_reads.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# The trail behind each read, by read id. Internal: ledger audit fields
# only. The full notes sit in the parent repo under reference/segment_reads/.
TRAILS = {
    'blue_collar_men_30_55_hhi_50k_200k': {
        'anchors': [
            'CPS 2024 annual averages: men employed in construction/extraction 8.15M, install/maint/repair 4.68M, production 5.65M, transport/moving 9.34M',
            'CPS 2024 table 11b age by occupation: 30 to 55 by years covered = 54.3% of blue-collar men (15.12M)',
            'Gen Pop OCCUPATION blue-collar buckets 18.45% vs CPS 20.64% of employed: file-coherent scale 0.894 (13.52M)',
            'Household income $50k to $200k share of the group 69.9% (range 66 to 75%); Gen Pop INCOME is household income',
            'Dixxon: instagram.com/dixxonflannelco 573K followers (platform page); dixxon.com site visits; revenue track',
            'Own domain reference: www.dixxon.com 785 US person-days T12M vs carhartt.com 4,298, dickies.com 1,432, filson.com 1,041',
        ],
        'derivation': (
            'Segment: joint, not marginals. 27.83M blue-collar men (CPS 2024) x 54.3% aged 30-55 = 15.12M; scaled to our Gen Pop '
            'occupation share (x0.894) = 13.52M; x 69.9% household income $50-200k = 9.45M. Range 8.92M to 11.34M, landed in the '
            'bottom third (anchor low). Independent Gen Pop marginals would give 5.29M (too low: blue-collar is 84% male). '
            'Dixxon: US any-touch reach 2.32M (0.70% of US), segment = 11.0% of it (male 68% x 30-55 58% x blue-collar 40% x '
            'HHI 70%) = 256K, 2.7% of the segment, 3.9x the US rate. Nothing held on Dixxon before (library, Gen Pop, hostmap, ledger).'),
    },
}


def payloads():
    from prometheus import segment_sizing as seg
    out = []
    for read in seg.registry(force=True).values():
        for p in seg.ledger_payloads(read):
            trail = TRAILS.get(str(read.get('id')) or '', {})
            out.append(dict(p, anchors=list(trail.get('anchors') or []), derivation=str(trail.get('derivation') or '')))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--apply', action='store_true', help='write to the insights ledger (default: dry run)')
    args = ap.parse_args(argv)
    todo = payloads()
    if not args.apply:
        for p in todo:
            print(json.dumps({'subject': p['subject'], 'cohort': p['cohort'], 'window': [p['window_start'], p['window_end']],
                              'metrics': [(m['name'], m['value'], m.get('sample_people')) for m in p['metrics']],
                              'questions': 1 + len(p['questions']), 'aliases': p['subject_aliases']}, indent=1))
            print(p['reply'])
        print(f"dry run: {len(todo)} read(s); --apply writes")
        return 0
    import insights_ledger as il
    for p in todo:
        keys = il.bank_record(**p)
        print(f"banked {p['subject']}: {len(keys)} entr{'y' if len(keys) == 1 else 'ies'} under {sorted(set(keys))}")
        if not keys:
            return 1
    il._load_index(force=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
