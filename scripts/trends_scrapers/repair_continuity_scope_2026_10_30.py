"""Repair stored days the continuity guard pinned to the wrong scope.

Until 2026-10-30 the day-over-day continuity guard compared today's item
total with yesterday's item total. When an item's platform set changed
overnight (five platforms yesterday, Audible alone today) the two totals
measure different things, so the guard read the change as a collapse or
a surge and set the item to 0.47-0.77x (or 1.30-2.15x) of yesterday's
total, rescaling every platform with it. House of Sky and Breath went
from a researched 127 Audible listeners to 5,547 on 2026-10-27, and each
later day continued from there.

The guard is deterministic, so a firing is recognisable exactly: the
stored total equals the guard's output for (key, date, yesterday's
total). This script walks the archive oldest first and, for each firing
across a platform-set change:

  - a platform also present yesterday continues from its own corrected
    prior level (a small deterministic day step), since the raw research
    value was not kept;
  - a platform new that day is divided by the inflation measured on the
    shared platforms; with no shared platform it is left as stored and
    counted;
  - later days stay in the chain and are rescaled by the same factor
    when they were carried forward (their `prev_day_date` is older than
    the day before) and moved within the guard band, or when they were
    researched fresh and the guard fired on them again. A fresh day the
    guard left alone stands on its own and ends the chain, as does the
    platform dropping off.

Item totals rescale with their platforms, and day-over-day trend fields
are recomputed wherever a day or the day before it changed. Each
written day is backed up first and its lean window index rebuilt.

Run:
    python3 -m scripts.trends_scrapers.repair_continuity_scope_2026_10_30 --dry-run
    python3 -m scripts.trends_scrapers.repair_continuity_scope_2026_10_30
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, timedelta

import boto3

from scripts.trends_scrapers import stream_estimates as se
from scripts.trends_scrapers import stream_window_index as swi
from scripts.trends_scrapers.reanchor_podcast_book_comic_2026_09_15 import _day_walk

BUCKET = 'dashboard-inputs'
DATED_KEY = 'trends_iq_snapshots/{d}/stream_estimates.json'
LATEST_KEY = 'trends_iq_snapshots/latest/stream_estimates.json'
BACKUP_KEY = 'trends_iq_snapshots/_backups/{d}.stream_estimates.pre_continuity_scope.json'
WATCH = ('book:house sky breath sarah j maas', 'book:kingdom ash sarah j maas')


def _guard_outputs(prev_total: int, key: str, target: str) -> set:
    out = set()
    u = se._h01(f'{key}|{target}|continuity')
    for f in (0.47 + u * 0.30, 1.30 + u * 0.85):
        a = max(1, int(round(prev_total * f)))
        a = se._ensure_non_zero_last_digit(a, key, f'{target}|continuity')
        if a == prev_total:
            a += 1 + int(u * 8)
        out.add(a)
    return out


def _levels(entry: dict) -> tuple[int, dict]:
    total = int(entry.get('us_estimate') or 0) if isinstance(entry, dict) else 0
    bp = {}
    for p, b in ((entry or {}).get('by_platform') or {}).items():
        v = (b or {}).get('us_estimate') if isinstance(b, dict) else None
        if isinstance(v, (int, float)) and v > 0:
            bp[p] = int(v)
    return total, bp


def _scale_block(blk: dict, new_mid: int) -> None:
    old = blk.get('us_estimate') or 0
    if old <= 0:
        return
    s = new_mid / old
    blk['us_estimate'] = new_mid
    for fld in ('us_estimate_low', 'us_estimate_high'):
        if isinstance(blk.get(fld), (int, float)):
            blk[fld] = max(1, int(round(blk[fld] * s)))
    if isinstance(blk.get('us_estimate_low'), int) and blk['us_estimate_low'] > new_mid:
        blk['us_estimate_low'] = new_mid
    if isinstance(blk.get('us_estimate_high'), int) and blk['us_estimate_high'] < new_mid:
        blk['us_estimate_high'] = new_mid
    lens = blk.get('us_estimate_by_lens')
    if isinstance(lens, dict):
        for k, v in lens.items():
            if isinstance(v, (int, float)):
                lens[k] = max(0, int(round(v * s)))


class Repair:
    def __init__(self):
        self.factor: dict = {}          # (key, platform) -> active chain factor
        self.orig_prev: dict = {}       # key -> (total, bp) as stored, previous day
        self.corr_prev: dict = {}       # key -> (total, bp) as repaired, previous day
        self.samples: dict = {k: [] for k in WATCH}

    def day(self, label: str, snap: dict, commit_state: bool = True) -> dict:
        items = snap.get('items') or {}
        target = str(snap.get('target_date') or '')[:10] or label
        targets = {target, label}
        day_before = (date.fromisoformat(target) - timedelta(days=1)).isoformat()
        stats = {'fired': 0, 'chained': 0, 'unrepairable': 0, 'trend': 0, 'entries': 0}
        orig_today, corr_today, changed = {}, {}, set()
        factor = dict(self.factor)

        for key, entry in items.items():
            if not isinstance(entry, dict):
                continue
            total, bp = _levels(entry)
            orig_today[key] = (total, bp)
            if not bp:
                corr_today[key] = (total, bp)
                continue
            op = self.orig_prev.get(key)
            cp = self.corr_prev.get(key)
            new_bp = dict(bp)
            fired = bool(op and op[0] and total and total != op[0] and op[1]
                         and set(op[1]) != set(bp)
                         and any(total in _guard_outputs(op[0], key, t) for t in targets))
            if fired:
                stats['fired'] += 1
                shared = [p for p in bp if p in op[1] and cp and cp[1].get(p)]
                infl = [bp[p] / cp[1][p] for p in shared]
                s_est = statistics.median(infl) if infl else None
                for p in bp:
                    if p in shared:
                        nv = _day_walk(cp[1][p], key, p, label)
                    elif s_est:
                        nv = max(1, int(round(bp[p] / s_est)))
                    else:
                        stats['unrepairable'] += 1
                        nv = bp[p]
                    new_bp[p] = nv
                    if nv != bp[p]:
                        factor[(key, p)] = nv / bp[p]
                    else:
                        factor.pop((key, p), None)
            else:
                fresh = entry.get('prev_day_date') == day_before
                if fresh and not (op and op[0] and any(
                        total in _guard_outputs(op[0], key, t) for t in targets)):
                    for p in [p for (k, p) in list(factor) if k == key]:
                        factor.pop((key, p), None)
                for p in bp:
                    fac = factor.get((key, p))
                    if fac is None:
                        continue
                    prev_orig = (op[1].get(p) if op else None) or 0
                    if not prev_orig or not (0.4 <= bp[p] / prev_orig <= 2.5):
                        factor.pop((key, p), None)
                        continue
                    new_bp[p] = max(1, int(round(bp[p] * fac)))
                    stats['chained'] += 1
            for p in [p for (k, p) in list(factor) if k == key and p not in bp]:
                factor.pop((key, p), None)

            if new_bp != bp:
                prev_c = (cp[1] if cp else {})
                for p, v in new_bp.items():
                    if v == prev_c.get(p) and v != bp[p]:
                        new_bp[p] = v + 1
                for p, v in new_bp.items():
                    if v != bp[p]:
                        _scale_block(entry['by_platform'][p], v)
                old_sum, new_sum = sum(bp.values()), sum(new_bp.values())
                if total and old_sum:
                    _scale_block(entry, max(1, int(round(total * new_sum / old_sum))))
                changed.add(key)
                stats['entries'] += 1
            corr_today[key] = (int(entry.get('us_estimate') or 0), new_bp)

        prev_changed = {k for k in corr_today
                        if self.corr_prev.get(k) != self.orig_prev.get(k)}
        for key in changed | prev_changed:
            entry = items.get(key)
            cp = self.corr_prev.get(key)
            if not isinstance(entry, dict) or not cp:
                continue
            if 'delta_pct' in entry and cp[0]:
                d, pct = se._direction_and_delta(entry.get('us_estimate') or 0, cp[0])
                entry['direction'], entry['delta_pct'], entry['prev_estimate'] = d, pct, cp[0]
            for p, blk in (entry.get('by_platform') or {}).items():
                if isinstance(blk, dict) and 'delta_pct' in blk and cp[1].get(p):
                    d, pct = se._direction_and_delta(blk.get('us_estimate') or 0, cp[1][p])
                    blk['direction'], blk['delta_pct'], blk['prev_estimate'] = d, pct, cp[1][p]
            stats['trend'] += 1

        for k in WATCH:
            if k in orig_today:
                self.samples[k].append((label, orig_today[k][1], corr_today[k][1]))
        if commit_state:
            self.factor = factor
            self.orig_prev, self.corr_prev = orig_today, corr_today
        stats['changed_keys'] = changed | prev_changed
        return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--end', default=date.today().isoformat())
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    s3 = boto3.client('s3', region_name='us-east-1')
    end = date.fromisoformat(args.end)
    days = [(end - timedelta(days=i)).isoformat() for i in range(args.days, -1, -1)]
    print(f'continuity scope repair {days[1]} .. {days[-1]} (base {days[0]})'
          f'{"  [DRY RUN]" if args.dry_run else ""}')
    rp = Repair()
    last_snap, last_etag = None, None
    for i, d in enumerate(days):
        key = DATED_KEY.format(d=d)
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except Exception as e:
            print(f'  {d}: unreadable ({type(e).__name__}); chain state kept')
            continue
        etag = obj.get('ETag')
        snap = json.loads(obj['Body'].read())
        if i == 0:
            rp.day(d, json.loads(json.dumps(snap)))
            continue
        st = rp.day(d, snap)
        print(f'  {d}: fired {st["fired"]:3d}  chained {st["chained"]:4d}  '
              f'entries {st["entries"]:4d}  trend {st["trend"]:4d}  '
              f'unrepairable {st["unrepairable"]}')
        if st['entries'] or st['trend']:
            if not args.dry_run:
                s3.copy_object(Bucket=BUCKET, Key=BACKUP_KEY.format(d=d),
                               CopySource={'Bucket': BUCKET, 'Key': key})
                s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(snap).encode(),
                              ContentType='application/json')
                swi.write_index(d, snap, s3=s3)
        last_snap, last_etag = snap, etag

    try:
        head = s3.head_object(Bucket=BUCKET, Key=LATEST_KEY)
        same = head.get('ETag') == last_etag
    except Exception:
        same = False
    print(f'latest/: {"same object as newest day, mirrored" if same else "differs from newest day, left as is"}')
    if same and not args.dry_run and last_snap is not None:
        s3.copy_object(Bucket=BUCKET, Key=BACKUP_KEY.format(d='latest'),
                       CopySource={'Bucket': BUCKET, 'Key': LATEST_KEY})
        s3.put_object(Bucket=BUCKET, Key=LATEST_KEY, Body=json.dumps(last_snap).encode(),
                      ContentType='application/json')

    for k, rows in rp.samples.items():
        print(f'\n{k}')
        for label, o, c in rows[-10:]:
            print(f'  {label}  stored {o}  ->  repaired {c}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
