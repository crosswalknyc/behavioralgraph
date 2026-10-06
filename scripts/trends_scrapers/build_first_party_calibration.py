"""Turn a partner's monthly title-level stream workbook into the
calibration document `first_party_calibration` reads.

    python3 -m scripts.trends_scrapers.build_first_party_calibration \\
        --slug lionsgateplus --label 'Lionsgate+' \\
        --xlsx '~/Downloads/Lionsgate+ US Monthly Streams.xlsx'

Expected shape (an Excel pivot): a header row whose first two cells
are a rank column and 'Row Labels', followed by one datetime column
per month; one row per title with monthly stream counts; a trailing
'Grand Total' row. The pivot is filtered to movies. Nothing else about
the layout is assumed: the header row is found by its 'Row Labels'
cell, months by their datetime type.

The document carries no copy of the workbook, only what the rails
need: the latest full month's service total, a rank-to-daily table,
each title's latest reading and trend, and the month range. It is
written to S3, never to the repo.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import sys
from typing import Any, Optional

from scripts.trends_scrapers import first_party_calibration as fpc
from scripts.trends_scrapers import stream_estimates as se

# A month with fewer titles than this share of the busiest month is a
# partial month (the export was cut mid-month) and is not "latest".
_FULL_MONTH_TITLE_SHARE = 0.6


def _read_pivot(path: str) -> tuple[list[dt.date], list[tuple[str, list[Optional[float]]]]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows)
                 if r and any(str(c).strip() == 'Row Labels' for c in r if c))
    hdr = rows[hdr_i]
    label_col = next(j for j, c in enumerate(hdr) if c and str(c).strip() == 'Row Labels')
    month_cols = [(j, c.date() if isinstance(c, dt.datetime) else c)
                  for j, c in enumerate(hdr) if isinstance(c, (dt.datetime, dt.date))]
    months = [m for _, m in month_cols]
    data = []
    for r in rows[hdr_i + 1:]:
        if not r or r[label_col] is None:
            continue
        title = str(r[label_col]).strip()
        if title.lower() == 'grand total':
            continue
        vals = []
        for j, _m in month_cols:
            v = r[j] if j < len(r) else None
            vals.append(float(v) if isinstance(v, (int, float)) else None)
        data.append((title, vals))
    return months, data


def build(slug: str, label: str, path: str, note: str = '') -> dict[str, Any]:
    months, data = _read_pivot(path)
    if not months or not data:
        raise SystemExit('workbook carried no months or no titles')
    counts = [sum(1 for _t, v in data if v[i]) for i in range(len(months))]
    busiest = max(counts)
    full = [i for i, c in enumerate(counts) if c >= busiest * _FULL_MONTH_TITLE_SHARE]
    latest_i = full[-1]
    latest = months[latest_i]
    days = calendar.monthrange(latest.year, latest.month)[1]

    col = sorted([(v[latest_i], t) for t, v in data if v[latest_i]], key=lambda z: -z[0])
    bands = [max(1, int(round(s / days))) for s, _t in col]
    total = sum(s for s, _t in col)

    titles: dict[str, dict] = {}
    for t, v in data:
        # Latest month with a real reading for this title; a handful of
        # streams in a month means the title was barely on the service.
        idx = [i for i in range(len(months)) if v[i] and v[i] >= 30]
        if not idx:
            continue
        i = idx[-1]
        m = months[i]
        d = calendar.monthrange(m.year, m.month)[1]
        monthly = int(v[i])
        prev = v[i - 1] if i > 0 and v[i - 1] else None
        trend = ((v[i] - prev) / prev * 100.0) if prev else None
        rank_in_month = 1 + sum(1 for _t2, v2 in data if v2[i] and v2[i] > v[i])
        norm = se._cp_normalize(t)
        if not norm:
            continue
        entry = {'title': t, 'month': m.strftime('%Y-%m'), 'monthly': monthly,
                 'daily': max(1, int(round(monthly / d))), 'rank': rank_in_month,
                 'trend_pct': round(trend, 1) if trend is not None else None}
        # Two spellings collapsing on one key keep the larger reading.
        if norm not in titles or titles[norm]['daily'] < entry['daily']:
            titles[norm] = entry

    daily_totals = []
    title_daily_max = 0
    for i, m in enumerate(months):
        if i not in full:
            continue
        d = calendar.monthrange(m.year, m.month)[1]
        tot = sum(v[i] for _t, v in data if v[i])
        daily_totals.append(tot / d)
        top = max((v[i] for _t, v in data if v[i]), default=0)
        title_daily_max = max(title_daily_max, top / d)

    doc = {
        'slug': slug, 'label': label, 'unit': 'streams',
        'scope': 'US, films, all offers, platform-reported monthly title-level streams',
        'built_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'source_file': os.path.basename(path), 'note': note,
        'months': [m.strftime('%Y-%m') for m in months],
        'service': {'month': latest.strftime('%Y-%m'), 'days': days,
                    'monthly_total': int(total),
                    'daily_total': int(round(total / days)), 'titles': len(col)},
        'bands': bands,
        'titles': titles,
        'range': {'months': len(full),
                  'daily_total_min': int(round(min(daily_totals))),
                  'daily_total_max': int(round(max(daily_totals))),
                  'title_daily_max': int(round(title_daily_max))},
    }
    return doc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--slug', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--xlsx', required=True)
    ap.add_argument('--note', default='')
    ap.add_argument('--dry-run', action='store_true', help='print, do not upload')
    args = ap.parse_args(argv)
    doc = build(args.slug, args.label, os.path.expanduser(args.xlsx), args.note)
    svc = doc['service']
    print(f"{doc['label']}: latest full month {svc['month']}, "
          f"{svc['monthly_total']:,} streams / {svc['titles']} films "
          f"= {svc['daily_total']:,} a day; #1 {doc['bands'][0]:,}/day, "
          f"#10 {fpc.band_for_rank(doc, 10):,}, #25 {fpc.band_for_rank(doc, 25):,}, "
          f"#100 {fpc.band_for_rank(doc, 100):,}; {len(doc['titles'])} titles "
          f"with a reading; range {doc['range']}")
    if args.dry_run:
        return 0
    body = json.dumps(doc, ensure_ascii=False).encode('utf-8')
    fpc._s3().put_object(Bucket=fpc.BUCKET, Key=fpc.key_for(args.slug),
                         Body=body, ContentType='application/json')
    print(f'wrote s3://{fpc.BUCKET}/{fpc.key_for(args.slug)} ({len(body):,} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
