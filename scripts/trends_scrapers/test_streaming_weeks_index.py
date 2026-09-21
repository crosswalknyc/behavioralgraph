#!/usr/bin/env python3
"""The weeks index reports coverage per day and never tallies short.

The rendered `weeks_in_top10` tally depends on the reader seeing
every day it asks for. An index cannot hold a day that had not
happened when it was built, so the contract is that it reports
which days it is missing and the reader goes and gets those,
rather than answering with a shorter tally.

    python3 -m scripts.trends_scrapers.test_streaming_weeks_index

No network, no S3, no clickstream: the index is a plain dict here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from datetime import date, timedelta
from scripts.trends_scrapers import streaming_weeks_index as swi

anchor = date(2026, 9, 21)
idx = {
    'index_version': swi.INDEX_VERSION,
    'anchor_date': anchor.isoformat(),
    'cover_days': 91,
    'platforms': {'hulu': {'titles': {'severance': [1, 8, 84],
                                      'oldest': [84]}}},
}

def days(today, n=84):
    return [today - timedelta(days=i) for i in range(1, n + 1)]

w, miss = swi.weeks_covered(idx, 'hulu', days(anchor))
assert miss == [], 'build day should need nothing'
assert swi.iso_week_key(anchor - timedelta(days=1)) in w['severance']
assert swi.iso_week_key(anchor - timedelta(days=84)) in w['oldest']
print('build day          : nothing missing, %d title(s)' % len(w))

for k, want in ((1, 0), (2, 1), (3, 2), (8, 7)):
    w, miss = swi.weeks_covered(idx, 'hulu', days(anchor + timedelta(days=k)))
    assert w is not None and len(miss) == want, (k, len(miss), want)
    print('%d day(s) behind    : %d day(s) to read, not 84' % (k, len(miss)))

# The strict form still refuses anything it cannot fully answer.
assert swi.weeks_for(idx, 'hulu', days(anchor)) is not None
assert swi.weeks_for(idx, 'hulu', days(anchor + timedelta(days=2))) is None
print('strict form        : refuses a partial answer')

w, miss = swi.weeks_covered(idx, 'netflix', days(anchor))
assert w is None and miss is None
print('unindexed platform : refused, caller scans it all')

w, _ = swi.weeks_covered(idx, 'hulu', days(anchor, 5))
assert 'oldest' not in w and 'severance' in w
print('out-of-range title : absent, same as the scan')

print()
print('PASS: coverage is per day, and a short tally is never returned')
