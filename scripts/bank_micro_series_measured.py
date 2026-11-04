#!/usr/bin/env python3
"""Bank The Read's Micro-Series, Measured figures where Prometheus reads.

Jenna, 2026-11-04: "check emma's questions and make sure they are being
answered this is the base level data she's working off of" (the Episode 06
Gmail package). Emma Friedenberg asked Prometheus, in Microdramas IQ,
whether micro-series titles that launch on a weekend do better than
weekday launches, on the series' own launch cohort (titles first seen
2026-01-01 to 2026-09-15, each over its first 28 days). The chat reasoned
cold: none of the series' figures (Episodes 01 to 06 as sent, the twelve
one-sheets of 2026-10-13) were banked under a subject her words resolve.

This script runs the delivered contract for the package she holds and
ingests the series' headline figures as binding anchors, all under the one
subject "Vertical Drama" (the bucket the one-sheets README already seeded
on 2026-10-30). insights_ledger resolves micro-series, microdramas,
vertical shorts and short dramas onto that subject (_SUBJECT_FAMILY_ALIASES).

    python3 scripts/bank_micro_series_measured.py --package /tmp/emma_pkg/Crosswalk_MicroSeriesMeasured_Ep06_Gmail
    python3 scripts/bank_micro_series_measured.py --anchors-only
    python3 scripts/bank_micro_series_measured.py --dry-run ...

Anchors are idempotent (the same source name replaces the prior entries).
The package step writes one delivery record per run; use it once. The
ledger index is backed up under system/insights_ledger/_backups/ first.
Nothing here emails anyone.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
SUBJECT = 'Vertical Drama'
USERNAME = 'emma'
PACKAGE_PREFIX = 'deliverables/emma/Micro_Series_Measured_Ep06/'
LEDGER_INDEX_KEY = 'system/insights_ledger/index.json'
LEDGER_BACKUP_PREFIX = 'system/insights_ledger/_backups/'

LAUNCH_WINDOW = {'start': '2026-01-01', 'end': '2026-09-15'}
LAUNCH_LABEL = 'Titles first seen 2026-01-01 to 2026-09-15, each over its own first 28 days'
DASH_WINDOW = {'start': '2026-09-16', 'end': '2026-10-13'}
DASH_LABEL = 'Last 28 days, 2026-09-16 to 2026-10-13 (matches the Microdramas IQ dashboard)'
T12_WINDOW = {'start': '2025-10-14', 'end': '2026-10-13'}
T12_LABEL = 'Trailing 12 months, 2025-10-14 to 2026-10-13'

COHORT_DEF = 'launch cohort: every micro-series title first seen on a tracked platform Jan 1 to Sep 15, 2026, read over its own first 28 days'


def m(name, label, value, unit, definition):
    return {'name': name, 'label': label, 'value': value, 'unit': unit, 'definition': definition[:220]}


# Episode 06 (Second screen) and Episode 05 (Shelf life): the launch cohort.
LAUNCH_ANCHORS = [
    m('launch_cohort_titles', 'Launch cohort titles', 1284, 'titles', COHORT_DEF),
    m('first_weekend_spike_share_pct', 'Launches with a first-weekend spike', 23.7, 'pct',
      'first-72-hour title name searches, actor name searches and clip shares out of TikTok and YouTube at 3x the title\'s own baseline or more; about 1 in 4 launches'),
    m('top_quarter_28d_audience_x', 'Top quarter of first-weekend activity, 28-day audience', 3.1, 'x the median launch',
      '28-day unique US viewers of titles in the top quarter of first-weekend searches and shares, as a multiple of the median launch'),
    m('second_quarter_28d_audience_x', 'Second quarter of first-weekend activity, 28-day audience', 1.4, 'x the median launch',
      '28-day unique US viewers as a multiple of the median launch'),
    m('third_quarter_28d_audience_x', 'Third quarter of first-weekend activity, 28-day audience', 0.8, 'x the median launch',
      '28-day unique US viewers as a multiple of the median launch'),
    m('bottom_quarter_28d_audience_x', 'Bottom quarter of first-weekend activity, 28-day audience', 0.4, 'x the median launch',
      '28-day unique US viewers as a multiple of the median launch; a quiet first weekend points to a quiet month'),
    m('signal_title_name_searches_pct', 'First 72 hours: title name searches', 46.3, 'pct',
      'share of first-72-hour activity for a top-quarter launch'),
    m('signal_tiktok_clip_shares_pct', 'First 72 hours: clip shares out of TikTok', 29.8, 'pct',
      'share of first-72-hour activity for a top-quarter launch'),
    m('signal_youtube_clip_shares_pct', 'First 72 hours: clip shares out of YouTube', 14.2, 'pct',
      'share of first-72-hour activity for a top-quarter launch'),
    m('signal_actor_name_searches_pct', 'First 72 hours: actor name searches', 9.7, 'pct',
      'share of first-72-hour activity for a top-quarter launch'),
    m('median_launch_peak_day', 'Median launch: peak day', 4, 'days after launch',
      'the median title\'s daily unique viewers peak on day 4'),
    m('median_launch_day14_share_of_peak_pct', 'Median launch: day 14 as a share of the peak day', 28.7, 'pct',
      'daily unique viewers on day 14 as a share of the peak day'),
    m('median_launch_day28_share_of_peak_pct', 'Median launch: day 28 as a share of the peak day', 13.1, 'pct',
      'daily unique viewers on day 28 as a share of the peak day'),
    m('first_14_day_share_of_first_60_day_views_pct', 'First 14 days: share of a title\'s first-60-day views', 71.4, 'pct',
      'views in days 1 to 14 as a share of views in days 1 to 60'),
    m('reelshort_top25_share_of_28d_views_pct', 'ReelShort: share of 28-day views in its top 25 titles', 54.2, 'pct',
      'last 28 days; the hits carry the catalog'),
    m('weekly_streaming_series_week4_hold_pct', 'Weekly streaming series: week-four hold of the launch-week audience', 58.3, 'pct',
      'yardstick for shelf life against the micro-series median of 13.1% of peak day on day 28'),
]

# Episode 06 chart leaders, views last 28 days (Microdramas IQ dashboard), read back to their first weekend.
LEADER_ANCHORS = [
    m('deny_me_dragon_king_views_28d', 'Deny Me, Dragon King (DramaBox): views, last 28 days', 24771921, 'episode plays',
      'leads DramaBox; first-weekend searches and shares 5.2x the median launch'),
    m('i_dont_need_no_alpha_brothers_views_28d', 'I Don\'t Need No Alpha Brothers (ReelShort): views, last 28 days', 19058772, 'episode plays',
      'leads ReelShort; first weekend 4.7x the median launch'),
    m('blood_and_bones_of_the_disowned_daughter_views_28d', 'Blood and Bones of the Disowned Daughter (GoodShort): views, last 28 days', 18087311, 'episode plays',
      'leads GoodShort; first weekend 3.9x the median launch'),
    m('do_not_disturb_lady_boss_in_disguise_views_28d', 'Do Not Disturb: Lady Boss in Disguise (Peacock hub): views, last 28 days', 11020508, 'episode plays',
      'leads the Peacock Microdramas hub; first weekend 2.8x the median launch'),
]

# Episodes 01 to 04 as sent, plus the Episode 07 teaser (the paywall), trailing 12 months.
T12_ANCHORS = [
    m('app_users_male_pct', 'Micro-series app users who are men', 37.1, 'pct',
      'people with a session on a dedicated vertical drama app or the Peacock Microdramas hub; up from 31.4% in the first quarter of the window to 39.8% in the last'),
    m('minutes_after_9pm_pct', 'Micro-series minutes after 9pm', 33.7, 'pct', '9pm to 6am local time; 27.8% land in the four hours from 9pm to 1am'),
    m('minutes_on_a_phone_pct', 'Micro-series minutes on a phone', 94.1, 'pct', 'tablets 4.3%, connected TV or web 1.6%'),
    m('new_users_from_paid_social_pct', 'New app users who arrived from a social ad or clip', 59.0, 'pct',
      'Facebook or Instagram 31.6%, TikTok 27.4%; median 9 days from a first clip to a first session; 72.4% opened the title they were shown'),
    m('paywall_leave_48h_pct', 'Viewers who reach a title\'s paywall and leave it within 48 hours', 47.3, 'pct',
      'coin apps, last 28 days; 10.7% pay money within 7 days; 24.1% of leavers watch the same title free on YouTube or TikTok within 14 days'),
    m('new_vertical_viewers_streaming_minutes_change_pct', 'New micro-series viewers: change in streaming minutes, 90 days after a first session', -3.4, 'pct',
      '13,124,287 adopters against matched never-starters at -0.6%; streaming sessions after 10pm -8.7%'),
]

# The Episode 06 package itself (what Emma holds): the delivered contract's figures (ten at most per entry).
PACKAGE_FIGURES = [
    {'name': 'Launch cohort titles', 'value': 1284, 'unit': 'titles', 'definition': COHORT_DEF},
    {'name': 'Launches with a first-weekend spike', 'value': 23.7, 'unit': 'pct', 'definition': 'first-72-hour searches and clip shares at 3x the title\'s own baseline or more'},
    {'name': 'Top quarter of first-weekend activity, 28-day audience', 'value': 3.1, 'unit': 'x the median launch', 'definition': '28-day unique US viewers as a multiple of the median launch'},
    {'name': 'Second quarter, 28-day audience', 'value': 1.4, 'unit': 'x the median launch', 'definition': '28-day unique US viewers as a multiple of the median launch'},
    {'name': 'Third quarter, 28-day audience', 'value': 0.8, 'unit': 'x the median launch', 'definition': '28-day unique US viewers as a multiple of the median launch'},
    {'name': 'Bottom quarter, 28-day audience', 'value': 0.4, 'unit': 'x the median launch', 'definition': '28-day unique US viewers as a multiple of the median launch'},
    {'name': 'First 72 hours: title name searches', 'value': 46.3, 'unit': 'pct', 'definition': 'share of first-72-hour activity, top-quarter launch'},
    {'name': 'First 72 hours: clip shares out of TikTok', 'value': 29.8, 'unit': 'pct', 'definition': 'share of first-72-hour activity, top-quarter launch'},
    {'name': 'First 72 hours: clip shares out of YouTube', 'value': 14.2, 'unit': 'pct', 'definition': 'share of first-72-hour activity, top-quarter launch'},
    {'name': 'First 72 hours: actor name searches', 'value': 9.7, 'unit': 'pct', 'definition': 'share of first-72-hour activity, top-quarter launch'},
]
PACKAGE_FACTS = [
    ('Launches that spike on their first weekend', '23.7%'),
    ('Top quarter of first-weekend activity', '3.1x the median launch\'s 28-day audience'),
    ('Launch cohort', '1,284 titles first seen Jan 1 to Sep 15, 2026'),
    ('Chart leaders, views last 28 days', 'Deny Me, Dragon King 24,771,921'),
]


def _s3():
    import boto3
    return boto3.client('s3')


def backup_index(s3, dry_run):
    key = f"{LEDGER_BACKUP_PREFIX}index.pre_micro_series_measured_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.json"
    if dry_run:
        print(f"[dry-run] would back up {LEDGER_INDEX_KEY} -> {key}")
        return key
    body = s3.get_object(Bucket=BUCKET, Key=LEDGER_INDEX_KEY)['Body'].read()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType='application/json')
    print(f"[bank] ledger index backed up -> s3://{BUCKET}/{key} ({len(body):,} bytes)")
    return key


def upload_package(s3, folder, dry_run):
    """Store the package files (never overwrites: the prefix is new)."""
    keys = []
    folder = Path(folder)
    for p in sorted(folder.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        key = PACKAGE_PREFIX + rel
        ctype = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        if dry_run:
            print(f"[dry-run] would upload {rel} -> {key}")
        else:
            try:
                s3.head_object(Bucket=BUCKET, Key=key)
                print(f"[bank] exists, kept: {key}")
            except Exception:
                s3.put_object(Bucket=BUCKET, Key=key, Body=p.read_bytes(), ContentType=ctype)
                print(f"[bank] uploaded {key}")
        keys.append(key)
    return keys


def bank_package(s3, folder, dry_run):
    import insights_ledger as il
    from prometheus import delivered
    il._SYNC_FOR_TESTS = True   # the write lands before this process exits
    keys = upload_package(s3, folder, dry_run) if folder else []
    web = next((k for k in keys if k.endswith('_web.html')), '')
    if dry_run:
        print('[dry-run] would run delivered.on_done(kind=package, email=False) with', len(PACKAGE_FIGURES), 'figures')
        return None
    rec = delivered.on_done(
        'package', username=USERNAME, subject=SUBJECT, s3_key=web or (keys[0] if keys else ''),
        s3_client=s3, bucket=BUCKET, figures=PACKAGE_FIGURES, facts=PACKAGE_FACTS,
        window=LAUNCH_WINDOW, email=False,
        intro='Micro-Series, Measured, Episode 06: first-weekend searches predict a title\'s 28-day audience.')
    rk = rec.get('record_key')
    if rk:
        rec = dict(rec, note='The Read / Micro-Series, Measured, Episode 06 (First-weekend searches predict a title\'s 28-day audience). '
                              'Gmail package Emma Friedenberg works from; figures from the launch cohort one-sheets of 2026-10-13.',
                   episode='06 of 12', series='Micro-Series, Measured', file_keys=keys,
                   inputs={'window': LAUNCH_LABEL, 'views_window': DASH_LABEL, 'subject_aliases': ['micro-series', 'microdramas', 'vertical drama', 'vertical shorts']})
        s3.put_object(Bucket=BUCKET, Key=rk, Body=json.dumps(rec, default=str).encode('utf-8'), ContentType='application/json')
        print(f"[bank] delivery record -> s3://{BUCKET}/{rk}")
    return rec


def bank_anchors(dry_run):
    import insights_ledger as il
    groups = [
        ('Micro-Series, Measured one-sheets: launch cohort (Episodes 05 and 06)', LAUNCH_ANCHORS, LAUNCH_WINDOW, LAUNCH_LABEL),
        ('Micro-Series, Measured one-sheets: chart leaders, last 28 days (Episode 06)', LEADER_ANCHORS, DASH_WINDOW, DASH_LABEL),
        ('Micro-Series, Measured one-sheets: trailing 12 months (Episodes 01 to 04 and 07)', T12_ANCHORS, T12_WINDOW, T12_LABEL),
    ]
    for source, metrics, win, label in groups:
        if dry_run:
            print(f"[dry-run] would ingest {len(metrics)} anchors from {source!r}")
            continue
        il.ingest_deck_anchors(subject=SUBJECT, metrics=metrics, source_name=source,
                               window_start=win['start'], window_end=win['end'], window_label=label,
                               metric_family='viewership', provenance=il.PROV_DELIVERED)
        print(f"[bank] anchors ingested: {len(metrics)} from {source!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', help='local folder of the Episode 06 Gmail package (runs the delivered contract, once)')
    ap.add_argument('--anchors-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    s3 = _s3()
    backup_index(s3, a.dry_run)
    if not a.anchors_only:
        if not a.package:
            ap.error('--package FOLDER is required unless --anchors-only')
        bank_package(s3, a.package, a.dry_run)
    bank_anchors(a.dry_run)
    if not a.dry_run:
        import insights_ledger as il
        led = il.consult(question='Do micro-series titles that launch on a weekend do better than weekday launches?')
        print(f"[bank] consult resolves -> {led.get('skey')} with {len(led.get('entries') or [])} entries; block lines: {len((led.get('block') or '').splitlines())}")


if __name__ == '__main__':
    main()
