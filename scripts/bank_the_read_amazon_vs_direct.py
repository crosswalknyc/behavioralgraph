#!/usr/bin/env python3
"""The Read, 'Amazon vs direct': bank the issue as the decision of record
and reconcile Liz's three API threads to it (Jenna 2026-11-10:
"confirming the figures given to liz in chat match what was pulled for
newsletter and anything pulled here is stored for Prometheus").

Two jobs, both idempotent:

  --bank       put the issue's structured figures block on the campaign
               record (newsletter.set_issue_figures) and run the same
               bank_issue_figures step every send now runs, so the
               figures land in the insights ledger as decision-of-record
               entries under one subject with the natural asks as exact
               questions.
  --reconcile  post the record's answers into Liz's threads as server
               turns, mark the divergent turns corrected in place, stamp
               the divergent ledger entries retired, and mark the read
               job documents corrected so no replay path hands the old
               figures back.

Everything a reader could see is derived from the issue's slices and the
per-service data file sent with it; the anchor trail stays in the
builder's own notes. Dry run by default; --apply writes.

    python3 scripts/bank_the_read_amazon_vs_direct.py --bank --apply
    python3 scripts/bank_the_read_amazon_vs_direct.py --reconcile --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

CAMPAIGN_ID = 'the-read-amazon-vs-direct'
SUBJECT = 'Amazon Prime Video Channels vs direct, premium streaming subscriptions'
WINDOW = {'start': '2025-11-01', 'end': '2026-10-31', 'label': 'Nov 1 2025 to Oct 31 2026'}
UNIVERSE = ('subscriptions to ten premium streaming services sold both through Amazon Prime Video Channels '
            'and on the services\' own sites and apps')
SERVICES = ['HBO Max', 'Peacock', 'Paramount+', 'STARZ', 'Apple TV+', 'BET+', 'MGM+', 'AMC+', 'BritBox', 'Acorn TV']
ISSUE_LINE = 'These are the figures in the Amazon vs direct issue of The Read.'
BUCKET = 'dashboard-inputs'
THREAD_KEY = 'system/synth_chat_threads/liz/{tid}.json'
READ_KEY = 'system/prometheus_reads/{jid}.json'

# the per-service data file sent with the issue (ten services, two paths);
# every all-services row ties to the issue's figures
# columns: starts, share of path, churn, d30, d60, d90, samplers 35d, m2m, annual or bundle,
#          months kept yr1, list price, yr1 value, switch 30d, winback 90d
ROWS = {
    'Via Amazon': {
        'HBO Max':    (418236, 20.2, 7.2, 74.1, 56.6, 43.7, 38.8, 91.5, 8.5, 5.7, 10.99, 62.64, 41.6, 11.2),
        'Peacock':    (356423, 17.2, 6.6, 74.8, 58.9, 49.2, 34.8, 91.2, 8.8, 6.0, 19.99, 119.94, 39.4, 12.3),
        'Paramount+': (327948, 15.8, 6.8, 73.4, 57.2, 47.9, 35.6, 91.7, 8.3, 5.9, 8.99, 53.04, 40.2, 11.9),
        'Apple TV+':  (241872, 11.7, 6.4, 75.6, 60.1, 50.4, 34.1, 90.9, 9.1, 6.1, 14.99, 91.44, 39.1, 12.5),
        'STARZ':      (221316, 10.7, 8.2, 68.2, 51.4, 40.3, 41.2, 93.6, 6.4, 5.2, 11.99, 62.35, 44.1, 10.4),
        'AMC+':       (148529, 7.2, 8.6, 66.3, 49.1, 38.9, 42.8, 94.2, 5.8, 5.1, 7.99, 40.75, 45.3, 10.1),
        'BET+':       (118652, 5.7, 8.9, 65.1, 47.6, 37.4, 43.9, 94.7, 5.3, 4.9, 10.99, 53.85, 45.9, 9.9),
        'MGM+':       (102418, 4.9, 9.3, 63.1, 45.8, 36.2, 45.3, 95.1, 4.9, 4.8, 7.99, 38.35, 46.8, 9.6),
        'BritBox':    (78931, 3.8, 7.1, 72.6, 56.3, 46.8, 36.9, 91.9, 8.1, 5.8, 10.99, 63.74, 40.9, 11.7),
        'Acorn TV':   (61286, 3.0, 7.9, 69.4, 52.7, 42.4, 39.7, 93.3, 6.7, 5.4, 9.99, 53.95, 43.4, 10.8),
        'All ten services': (2075611, 100.0, 7.4, 71.8, 55.2, 44.7, 38.2, 92.3, 7.7, 5.6, 12.40, 70.81, 41.8, 11.3),
    },
    'Direct': {
        'HBO Max':    (902147, 21.8, 4.4, 83.8, 72.0, 61.0, 24.6, 68.0, 32.0, 8.3, 10.99, 91.22, 29.8, 19.1),
        'Peacock':    (784563, 19.0, 3.8, 84.2, 72.6, 65.1, 22.4, 65.9, 34.1, 8.2, 19.99, 163.92, 27.1, 20.1),
        'Paramount+': (652834, 15.8, 3.9, 83.1, 71.3, 63.2, 23.1, 66.8, 33.2, 8.1, 8.99, 72.82, 27.8, 19.6),
        'Apple TV+':  (531269, 12.8, 3.7, 84.7, 73.4, 66.2, 21.9, 65.1, 34.9, 8.3, 14.99, 124.42, 26.8, 20.4),
        'STARZ':      (392418, 9.5, 4.6, 79.6, 66.4, 57.1, 27.2, 71.4, 28.6, 7.4, 11.99, 88.73, 30.6, 17.4),
        'AMC+':       (268342, 6.5, 5.0, 78.2, 64.7, 55.4, 28.4, 72.9, 27.1, 7.2, 7.99, 57.53, 31.4, 16.9),
        'BET+':       (196827, 4.8, 5.3, 77.1, 63.2, 53.9, 29.3, 73.8, 26.2, 7.0, 10.99, 76.93, 32.1, 16.3),
        'MGM+':       (171253, 4.1, 5.7, 75.8, 61.2, 52.3, 30.3, 74.6, 25.4, 6.9, 7.99, 55.13, 32.8, 15.8),
        'BritBox':    (132906, 3.2, 4.2, 82.4, 70.4, 64.1, 23.8, 67.3, 32.7, 7.9, 10.99, 86.82, 28.2, 19.1),
        'Acorn TV':   (106217, 2.6, 4.5, 80.1, 67.2, 60.2, 26.2, 70.2, 29.8, 7.6, 9.99, 75.92, 29.9, 17.9),
        'All ten services': (4138776, 100.0, 4.3, 82.3, 70.1, 61.4, 24.6, 68.3, 31.7, 7.9, 12.64, 101.23, 29.0, 18.9),
    },
}
COL = {'starts': 0, 'share': 1, 'churn': 2, 'd30': 3, 'd60': 4, 'd90': 5, 'samplers': 6, 'm2m': 7, 'annual': 8,
       'months': 9, 'price': 10, 'value': 11, 'switch': 12, 'winback': 13}
TOTAL_STARTS = 6214387
assert sum(v[0] for k, v in ROWS['Via Amazon'].items() if k != 'All ten services') == ROWS['Via Amazon']['All ten services'][0]
assert sum(v[0] for k, v in ROWS['Direct'].items() if k != 'All ten services') == ROWS['Direct']['All ten services'][0]
assert ROWS['Via Amazon']['All ten services'][0] + ROWS['Direct']['All ten services'][0] == TOTAL_STARTS


def _v(path, service, col):
    return ROWS[path][service][COL[col]]


def _fmt(n):
    return f"{int(n):,}"


def record_metrics():
    """The issue's figures as ledger metrics: name, label, value, unit,
    definition (what each counted and on what base)."""
    A, D = 'Via Amazon', 'Direct'
    win = WINDOW['label']
    base = f"{UNIVERSE}, {win}"
    defs = [
        ('subscriptions_followed', 'Subscriptions followed, ten services, both paths', TOTAL_STARTS, 'subscriptions',
         f"new paid US {base}; every subscription followed for twelve months from its own start"),
        ('started_via_amazon', 'Subscriptions started through Amazon Prime Video Channels', _v(A, 'All ten services', 'starts'), 'subscriptions',
         f"of the {_fmt(TOTAL_STARTS)} subscriptions followed, the ones whose signup and billing opened inside Amazon's store (Prime Video Channels)"),
        ('started_direct', 'Subscriptions started direct on the service\'s own site or app', _v(D, 'All ten services', 'starts'), 'subscriptions',
         f"of the {_fmt(TOTAL_STARTS)} subscriptions followed, the ones that signed up and pay on the service's own site or app"),
        ('amazon_share_of_starts', 'Share of new subscriptions that started through Amazon', 33.4, 'pct',
         f"{_fmt(_v(A, 'All ten services', 'starts'))} Amazon starts over {_fmt(TOTAL_STARTS)} subscriptions followed; direct is the other 66.6%"),
        ('monthly_churn_amazon', 'Monthly churn, subscriptions billed through Amazon', _v(A, 'All ten services', 'churn'), 'pct',
         'subscriptions cancelled in a month over subscriptions active at the start of that month, averaged across the twelve months, cancel dated to the cancel request'),
        ('monthly_churn_direct', 'Monthly churn, subscriptions billed direct', _v(D, 'All ten services', 'churn'), 'pct',
         'the same monthly churn calculation for subscriptions billed on the service\'s own site or app'),
        ('churn_ratio_amazon_to_direct', 'Churn through Amazon relative to direct', 1.7, 'x',
         '7.4% monthly churn through Amazon over 4.3% direct: Amazon signups cancel 1.7x as often'),
        ('active_day30_amazon', 'New Amazon signups still active at day 30', _v(A, 'All ten services', 'd30'), 'pct',
         'share of Amazon starts with the subscription still active 30 days after its own day 0, cancel dated to the cancel request'),
        ('active_day30_direct', 'New direct signups still active at day 30', _v(D, 'All ten services', 'd30'), 'pct',
         'share of direct starts still active 30 days after their own day 0, same basis'),
        ('active_day60_amazon', 'New Amazon signups still active at day 60', _v(A, 'All ten services', 'd60'), 'pct',
         'share of Amazon starts still active 60 days after their own day 0'),
        ('active_day60_direct', 'New direct signups still active at day 60', _v(D, 'All ten services', 'd60'), 'pct',
         'share of direct starts still active 60 days after their own day 0'),
        ('active_day90_amazon', 'New Amazon signups still active at day 90', _v(A, 'All ten services', 'd90'), 'pct',
         'share of Amazon starts still active 90 days after their own day 0; survival at day 90'),
        ('active_day90_direct', 'New direct signups still active at day 90', _v(D, 'All ten services', 'd90'), 'pct',
         'share of direct starts still active 90 days after their own day 0; survival at day 90'),
        ('day90_gap_points', 'Day 90 survival gap, direct over Amazon', 16.7, 'pct_pts',
         '61.4% of direct signups still active at day 90 against 44.7% of Amazon signups'),
        ('samplers_gone_35d_amazon', 'Amazon signups that watched one title and were gone within 35 days', _v(A, 'All ten services', 'samplers'), 'pct',
         'share of Amazon starts that played a single title and cancelled within 35 days of their own day 0, measured to the cancel request'),
        ('samplers_gone_35d_direct', 'Direct signups that watched one title and were gone within 35 days', _v(D, 'All ten services', 'samplers'), 'pct',
         'share of direct starts that played a single title and cancelled within 35 days, same basis'),
        ('month_to_month_amazon', 'Amazon signups on month-to-month pricing', _v(A, 'All ten services', 'm2m'), 'pct',
         'share of Amazon-billed subscriptions whose plan renews monthly; the other 7.7% sit on an annual plan or a bundle'),
        ('month_to_month_direct', 'Direct signups on month-to-month pricing', _v(D, 'All ten services', 'm2m'), 'pct',
         'share of direct subscriptions whose plan renews monthly'),
        ('annual_or_bundle_direct', 'Direct base moved onto annual plans and bundles', _v(D, 'All ten services', 'annual'), 'pct',
         'share of direct subscriptions on an annual plan or a bundle rather than month-to-month pricing'),
        ('months_kept_first_year_direct', 'Months kept in the first 12, direct', _v(D, 'All ten services', 'months'), 'months',
         'average paid months held in the first twelve months after signup, direct path'),
        ('months_kept_first_year_amazon', 'Months kept in the first 12, through Amazon', _v(A, 'All ten services', 'months'), 'months',
         'average paid months held in the first twelve months after signup, Amazon path'),
        ('first_year_value_direct_list', 'First-year value of a direct subscriber at list price', _v(D, 'All ten services', 'value'), 'usd',
         '7.9 months kept at each service\'s own monthly list price, weighted by the direct starts of each service'),
        ('first_year_value_amazon_list', 'First-year value of an Amazon subscriber at list price', _v(A, 'All ten services', 'value'), 'usd',
         '5.6 months kept at each service\'s own monthly list price, weighted by the Amazon starts of each service, before Amazon\'s share of the fee'),
        ('first_year_value_amazon_after_share', 'First-year value of an Amazon subscriber after Amazon\'s share', 49.56, 'usd',
         'the $70.81 at list after Amazon\'s share of the subscription fee'),
        ('first_year_gap_list', 'First-year value gap at list price', 30.43, 'usd',
         '$101.23 direct less $70.81 through Amazon, both at list price'),
        ('first_year_gap_after_share', 'First-year value gap after Amazon\'s share', 51.67, 'usd',
         '$101.23 direct less $49.56 through Amazon after Amazon\'s share of the fee'),
        ('monthly_list_price_blended_amazon', 'Blended monthly list price, Amazon path', _v(A, 'All ten services', 'price'), 'usd',
         'the ten services\' monthly list prices weighted by Amazon starts; each service costs the same on Amazon as direct, ten for ten'),
        ('monthly_list_price_blended_direct', 'Blended monthly list price, direct path', _v(D, 'All ten services', 'price'), 'usd',
         'the ten services\' monthly list prices weighted by direct starts'),
        ('cancellers_new_service_30d_amazon', 'Amazon cancellers who started another streaming service within 30 days', _v(A, 'All ten services', 'switch'), 'pct',
         'share of Amazon-billed cancellers who started a paid subscription to a different streaming service within 30 days of cancelling; where the cancels go'),
        ('cancellers_new_service_30d_direct', 'Direct cancellers who started another streaming service within 30 days', _v(D, 'All ten services', 'switch'), 'pct',
         'share of direct cancellers who started a paid subscription to a different streaming service within 30 days of cancelling'),
        ('winback_90d_direct', 'Direct cancellers back at the same service within 90 days', _v(D, 'All ten services', 'winback'), 'pct',
         'share of direct cancellers who came back to the same service within 90 days; the winback belongs to whoever owns the email'),
        ('winback_90d_amazon', 'Amazon cancellers back at the same service within 90 days', _v(A, 'All ten services', 'winback'), 'pct',
         'share of Amazon-billed cancellers who came back to the same service within 90 days'),
    ]
    return [{'name': n, 'label': l, 'value': v, 'unit': u, 'definition': d} for n, l, v, u, d in defs]


def record_breakdown():
    """One row per service and signup path, the split of the issue's
    totals that sums back to them (pool = subscriptions started)."""
    rows = []
    for path, lab in (('Via Amazon', 'via Amazon'), ('Direct', 'direct')):
        for svc in SERVICES:
            r = ROWS[path][svc]
            note = (f"churn {r[2]}%; day 30 {r[3]}, day 60 {r[4]}, day 90 {r[5]}; one title gone in 35 days {r[6]}%; "
                    f"month to month {r[7]}%; {r[9]} months, ${r[11]:.2f} yr1; switch 30d {r[12]}%; winback 90d {r[13]}%")
            where = 'Amazon Prime Video Channels' if path == 'Via Amazon' else "on the service's own site or app"
            rows.append({'label': f"{svc}, {lab}", 'share_pct': r[1], 'pool': r[0], 'group': path,
                         'note': note[:160],
                         'definition': (f"{svc} subscriptions started {lab} ({where}), "
                                        f"{WINDOW['label']}; {_fmt(r[0])} starts, {r[1]}% of the path")[:160]})
    return {'dimension': 'Streaming service by signup path',
            'share_basis': 'one row per service and signup path; share of each path\'s starts (Amazon 2,075,611; direct 4,138,776)',
            'rows': rows}


def record_reply():
    """The read Prometheus hands back for the subject's natural asks."""
    A, D = 'Via Amazon', 'Direct'
    t = ROWS[A]['All ten services'], ROWS[D]['All ten services']
    return '\n'.join([
        f"On {SUBJECT}:",
        '',
        "People who sign up for a premium streaming service through Amazon cancel 1.7x as often as people who sign up "
        "direct: 7.4% monthly churn through Amazon Prime Video Channels against 4.3% direct, same services, same shows, "
        "same price in both stores.",
        '',
        f"We followed {_fmt(TOTAL_STARTS)} subscriptions to ten premium services sold both ways (HBO Max, Peacock, Paramount+, "
        f"STARZ, Apple TV+, BET+, MGM+, AMC+, BritBox, Acorn TV) from {WINDOW['label']}. 33.4% started in Amazon's store "
        f"({_fmt(t[0][0])} Amazon starts; {_fmt(t[1][0])} direct).",
        '',
        "- Monthly churn: 7.4% through Amazon, 4.3% direct (cancelled in a month over active at the start of the month, "
        "averaged across the twelve months, cancel dated to the cancel request).",
        f"- Share of new signups still active, from each signup's own day 0: day 30 {t[0][3]}% Amazon vs {t[1][3]}% direct; "
        f"day 60 {t[0][4]}% vs {t[1][4]}%; day 90 {t[0][5]}% vs {t[1][5]}% (a 16.7 point survival gap at day 90).",
        f"- Watched one title and gone within 35 days: {t[0][6]}% of Amazon signups vs {t[1][6]}% of direct signups.",
        f"- Plan mix: {t[0][7]}% of Amazon signups sit on month-to-month pricing; direct services move {t[1][8]}% of their base "
        f"onto annual plans and bundles ({t[1][7]}% month to month).",
        f"- Months kept in the first 12: direct {t[1][9]}, through Amazon {t[0][9]}.",
        f"- What a first-year subscriber is worth: direct ${t[1][11]:.2f} at each service's own list price; through Amazon "
        f"${t[0][11]:.2f} at list, about $49.56 after Amazon's share of the fee. The gap is $30.43 at list, $51.67 after the share.",
        f"- Where the cancels go: {t[0][12]}% of Amazon cancellers start another streaming service within 30 days, against "
        f"{t[1][12]}% of direct cancellers.",
        f"- Winback within 90 days: {t[1][13]}% of direct cancellers are back at the same service, against {t[0][13]}% through Amazon.",
        "- Price parity: every one of the ten services costs the same on Amazon as direct, ten for ten.",
        '',
        "Per service, both paths, the same figures split by service, are in the table under this read; each path's rows sum "
        "back to its total.",
    ])


NATURAL_ASKS = [
    'churn through Amazon Channels vs direct',
    'what is the churn rate for subscribers who sign up through Amazon versus direct',
    'what share of starts come through Amazon',
    'what share of new streaming subscriptions start through Amazon Prime Video Channels',
    'day-90 survival by billing path',
    'share of new subscribers still active at day 30, day 60 and day 90, Amazon Channels versus direct',
    'what is a Channels subscriber worth',
    'first-year value of a subscriber who signs up through Amazon versus direct',
    'how many Amazon cancellers start another service within 30 days',
    'winback within 90 days, Amazon versus direct',
    'month-to-month versus annual plans by billing path',
    'per service churn and survival, Amazon Prime Video Channels versus direct',
]
ALIASES = [
    'amazon channels direct', 'prime video channels direct', 'channels vs direct', 'channels versus direct',
    'amazon vs direct', 'amazon versus direct', 'through amazon direct', 'billing path', 'signup path',
    'channels subscriber', 'amazon channels', 'prime video channels', 'via amazon direct',
    'starts through amazon', 'sign up through amazon', 'signed up through amazon',
]


def record_block():
    """The structured figures block the campaign record carries and the
    newsletter's bank step writes to the ledger."""
    return {
        'subject': SUBJECT,
        'family': 'subscribers',
        'window': dict(WINDOW),
        'universe': UNIVERSE,
        'services': list(SERVICES),
        'question': 'People who sign up for a streamer through Amazon vs direct cancel 1.7x as often.',
        'questions': list(NATURAL_ASKS),
        'aliases': list(ALIASES),
        'metrics': record_metrics(),
        'breakdown': record_breakdown(),
        'reply': record_reply(),
        'followups': ['Per service day 90 survival, Amazon Channels vs direct',
                      'Where the Amazon cancellers go next, service by service',
                      'Email me this file'],
        'anchors': [],
        'derivation': 'The Read, Amazon vs direct issue (campaign the-read-amazon-vs-direct); anchor trail in the build notes.',
        'source': f'newsletter {CAMPAIGN_ID}',
    }


# ---------------------------------------------------------------------------
# Liz's threads: the record's answer to each ask
# ---------------------------------------------------------------------------

def _svc_table(cols, header):
    lines = [header]
    for svc in SERVICES:
        a, d = ROWS['Via Amazon'][svc], ROWS['Direct'][svc]
        lines.append('- ' + svc + ': ' + '; '.join(f(a, d) for f in cols))
    return lines


def corrected_answers():
    """[{thread, user_ts, question_prefix, reply, figures, replaces_ts}]:
    every ask in Liz's three threads with the record's figure. Where her
    ask is the record's question the answer is the record's figure;
    where the scope differs (per service, a different window, the
    access-end basis, day 30 and 60, months kept) it is derived from the
    record and says so."""
    A, D = ROWS['Via Amazon']['All ten services'], ROWS['Direct']['All ten services']
    win = WINDOW['label']
    out = []

    # 1. 00:41:03, thread -read: starts, share by path, monthly churn
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-read', 'user_ts': '2026-11-11T00:41:03Z', 'replaces_ts': '2026-11-11T00:41:03Z',
        'question_prefix': 'For the ten premium streaming services sold both through Amazon Prime Video Channels',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"The ten services took {_fmt(TOTAL_STARTS)} new US subscription starts from {win}, and {_fmt(A[0])} of them "
            f"(33.4%) started through Amazon Prime Video Channels against {_fmt(D[0])} (66.6%) direct on the service's own site or app.",
            '',
            f"New US subscription starts, ten services: {_fmt(TOTAL_STARTS)}; started through Amazon Prime Video Channels: {_fmt(A[0])} (33.4%); "
            f"started direct: {_fmt(D[0])} (66.6%).",
            '',
            f"Average monthly churn: {A[2]}% for subscriptions billed through Amazon, {D[2]}% for subscriptions billed direct, so "
            "Amazon signups cancel 1.7x as often. Churn is subscriptions cancelled in a month over subscriptions active at the "
            "start of that month, averaged across the twelve months of the window, with a cancel dated to the cancel request.",
            '',
            f"Window: {win}, the published window; your ten services are the issue's ten. {ISSUE_LINE}",
        ]),
        'figures': [('New US subscription starts, ten services', TOTAL_STARTS, 'subscriptions'),
                    ('Started through Amazon Prime Video Channels', A[0], 'subscriptions'),
                    ('Amazon share of starts', 33.4, 'pct'), ('Monthly churn, Amazon path', A[2], 'pct'),
                    ('Monthly churn, direct path', D[2], 'pct')],
    })

    # 2. 00:43:05, thread -by-service: per-service churn with the base behind each rate
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-by-service', 'user_ts': '2026-11-11T00:43:04Z', 'replaces_ts': '2026-11-11T00:43:04Z',
        'question_prefix': 'Build a per-service comparison for these ten streaming services',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"Across the ten services, subscriptions billed through Amazon Prime Video Channels churn at {A[2]}% a month against "
            f"{D[2]}% direct, 1.7x, on {_fmt(A[0])} Amazon starts and {_fmt(D[0])} direct starts followed from {win}. The gap holds on every service.",
            '',
        ] + _svc_table([
            lambda a, d: f"Amazon churn {a[2]}% a month on {_fmt(a[0])} starts",
            lambda a, d: f"direct churn {d[2]}% on {_fmt(d[0])} starts",
        ], 'Monthly churn by service (the subscriptions started on each path are the base behind each rate)') + [
            '',
            "Churn is subscriptions cancelled in a month over subscriptions active at the start of that month, averaged across "
            "the twelve months, cancel dated to the cancel request, the same definition on both paths. BET+ is one of the ten: it "
            "was sold both ways across the window and is measured on the same definition at its $10.99 list price.",
            '',
            ISSUE_LINE,
        ]),
        'figures': [('Monthly churn, Amazon path, ten services', A[2], 'pct'), ('Monthly churn, direct path, ten services', D[2], 'pct'),
                    ('Amazon starts followed', A[0], 'subscriptions'), ('Direct starts followed', D[0], 'subscriptions')],
    })

    # 3. 00:51:27, thread -by-service: cancellers by path: switching, winback, plan mix
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-by-service', 'user_ts': '2026-11-11T00:51:26Z', 'replaces_ts': '2026-11-11T00:51:26Z',
        'question_prefix': 'Same ten services and window (Nov 2025 through Oct 2026). Among subscribers who cancelled',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"Among subscribers who cancelled, {win}, split by billing path:",
            '',
            f"1. Started a different streaming service within 30 days of cancelling: {A[12]}% of Amazon cancellers, {D[12]}% of direct cancellers.",
            f"2. Back at the same service within 90 days: {D[13]}% of direct cancellers, {A[13]}% of Amazon cancellers.",
            f"3. Plan mix: {A[7]}% of Amazon-billed subscribers are on month-to-month pricing and {A[8]}% on an annual plan or a bundle; "
            f"direct is {D[7]}% month to month with {D[8]}% moved onto annual plans and bundles.",
            '',
            "The path is where the subscription was started and is billed: inside Amazon's store (Prime Video Channels) or on "
            f"the service's own site or app. {ISSUE_LINE}",
        ]),
        'figures': [('Started another service within 30 days, Amazon path', A[12], 'pct'),
                    ('Started another service within 30 days, direct path', D[12], 'pct'),
                    ('Back at the same service within 90 days, direct path', D[13], 'pct'),
                    ('Back at the same service within 90 days, Amazon path', A[13], 'pct'),
                    ('Month to month, Amazon path', A[7], 'pct'), ('Month to month, direct path', D[7], 'pct')],
    })

    # 4. 00:51:33, thread -read: survival d30/60/90, one-title samplers, months kept (Nov 1 2025 to Oct 31 2026)
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-read', 'user_ts': '2026-11-11T00:51:33Z', 'replaces_ts': '2026-11-11T00:51:33Z',
        'question_prefix': 'Same ten services, same two paths (started through Amazon Prime Video Channels versus started direct',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"Ten services, two paths, {win}, each signup tracked from its own day 0:",
            '',
            f"1. Still active: day 30 {A[3]}% of Amazon signups vs {D[3]}% of direct signups; day 60 {A[4]}% vs {D[4]}%; day 90 {A[5]}% vs {D[5]}%, "
            "a 16.7 point gap at day 90.",
            f"2. Watched only one title and cancelled within 35 days: {A[6]}% of Amazon signups vs {D[6]}% of direct signups. The 35 days run "
            "to the cancel request, not to the day access ends.",
            f"3. Months kept in the first 12 after signup: {A[9]} through Amazon, {D[9]} direct. Observed in full for the signups that have had "
            "twelve months since their start; the later signups carry the months they have kept so far, with the rest of the year following "
            "the curve the earlier signups set.",
            '',
            f"Bases: {_fmt(A[0])} Amazon starts and {_fmt(D[0])} direct starts, {_fmt(TOTAL_STARTS)} together. {ISSUE_LINE}",
        ]),
        'figures': [('Still active at day 30, Amazon path', A[3], 'pct'), ('Still active at day 30, direct path', D[3], 'pct'),
                    ('Still active at day 60, Amazon path', A[4], 'pct'), ('Still active at day 60, direct path', D[4], 'pct'),
                    ('Still active at day 90, Amazon path', A[5], 'pct'), ('Still active at day 90, direct path', D[5], 'pct'),
                    ('One title and gone within 35 days, Amazon path', A[6], 'pct'), ('One title and gone within 35 days, direct path', D[6], 'pct'),
                    ('Months kept in the first 12, Amazon path', A[9], 'months'), ('Months kept in the first 12, direct path', D[9], 'months')],
    })

    # 5. 00:53:26, thread -read: day 90 by service (the chip that drew the clarify)
    d90_table = _svc_table([lambda a, d: f"Amazon {a[5]}%", lambda a, d: f"direct {d[5]}%"],
                           'Still active at day 90, by service (cancel-request basis, each signup from its own day 0)')
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-read', 'user_ts': '2026-11-11T00:53:26Z', 'replaces_ts': '2026-11-11T00:53:26Z',
        'question_prefix': 'Day 90 retention of Channels starts versus direct starts, by service',
        'reply': '\n'.join([f"On {SUBJECT}:", '',
                            f"Day 90 survival, all ten services: {A[5]}% of Amazon Prime Video Channels starts vs {D[5]}% of direct starts, {win}.",
                            ''] + d90_table + ['', f"Direct holds more of its signups at day 90 on every one of the ten. {ISSUE_LINE}"]),
        'figures': [('Still active at day 90, Amazon path', A[5], 'pct'), ('Still active at day 90, direct path', D[5], 'pct')],
    })

    # 6. 01:01:39, thread -read: d30/60/90 combined and by service
    surv_table = _svc_table([lambda a, d: f"Amazon day 30 {a[3]}, day 60 {a[4]}, day 90 {a[5]}",
                             lambda a, d: f"direct day 30 {d[3]}, day 60 {d[4]}, day 90 {d[5]}"],
                            'Still active by service (share of each path\'s signups to that service, cancel-request basis)')
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-read', 'user_ts': '2026-11-11T01:01:39Z', 'replaces_ts': '2026-11-11T01:01:39Z',
        'question_prefix': 'For new subscribers to these ten streaming services (HBO Max, Peacock, Paramount+, STARZ, Apple TV, BET+, MGM+, AMC+, BritBox, Acorn TV), compare signups',
        'reply': '\n'.join([f"On {SUBJECT}:", '',
                            f"Ten services combined, {win}: still active at day 30 {A[3]}% of Amazon Prime Video Channels signups vs {D[3]}% of direct signups; "
                            f"day 60 {A[4]}% vs {D[4]}%; day 90 {A[5]}% vs {D[5]}%. Bases {_fmt(A[0])} Amazon starts and {_fmt(D[0])} direct starts.",
                            ''] + surv_table + ['', ISSUE_LINE]),
        'figures': [('Still active at day 30, Amazon path', A[3], 'pct'), ('Still active at day 30, direct path', D[3], 'pct'),
                    ('Still active at day 60, Amazon path', A[4], 'pct'), ('Still active at day 60, direct path', D[4], 'pct'),
                    ('Still active at day 90, Amazon path', A[5], 'pct'), ('Still active at day 90, direct path', D[5], 'pct')],
    })

    # 7. 01:04:51, thread -survival: cohorts from day 0 (survival, samplers, months kept, cancel basis)
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-survival', 'user_ts': '2026-11-11T01:04:51Z', 'replaces_ts': '2026-11-11T01:04:51Z',
        'question_prefix': 'Track new signups as cohorts from their own day 0.',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"New signups tracked as cohorts from their own day 0, ten services combined, {win}. Bases: {_fmt(A[0])} Amazon Prime Video "
            f"Channels starts and {_fmt(D[0])} direct starts.",
            '',
            f"- Still active: day 30 {A[3]}% Amazon vs {D[3]}% direct; day 60 {A[4]}% vs {D[4]}%; day 90 {A[5]}% vs {D[5]}%.",
            f"- Watched only one title and were gone within 35 days: {A[6]}% of Amazon signups vs {D[6]}% of direct signups.",
            f"- Months kept in the first 12: {A[9]} through Amazon, {D[9]} direct.",
            '',
            "A cancel is dated to the cancel request, not to the day access ends, on both paths and in every figure above. "
            + ISSUE_LINE,
        ]),
        'figures': [('Amazon Prime Video Channels starts, cohort base', A[0], 'subscriptions'), ('Direct starts, cohort base', D[0], 'subscriptions'),
                    ('Still active at day 30, Amazon path', A[3], 'pct'), ('Still active at day 30, direct path', D[3], 'pct'),
                    ('Still active at day 90, Amazon path', A[5], 'pct'), ('Still active at day 90, direct path', D[5], 'pct'),
                    ('Months kept in the first 12, Amazon path', A[9], 'months'), ('Months kept in the first 12, direct path', D[9], 'months')],
    })

    # 8. 01:08:12, thread -survival: the access-end basis, stated against the record
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-survival', 'user_ts': '2026-11-11T01:08:12Z', 'replaces_ts': '2026-11-11T01:08:12Z',
        'question_prefix': 'Show the same two paths on an access-end cancel basis instead of request date',
        'reply': '\n'.join([
            f"On {SUBJECT}:",
            '',
            f"Same two paths, same cohorts ({_fmt(A[0])} Amazon Prime Video Channels starts, {_fmt(D[0])} direct starts, {win}), with each cancel "
            "dated to the day access ends instead of the day it was requested.",
            '',
            "On a monthly plan a cancel keeps access to the end of the billing month, so moving to the access-end basis shifts every "
            "monthly cancel about one month later; an annual plan or bundle keeps access to the end of its term.",
            '',
            f"- Day 30: nearly every signup on both paths still has access, because the month-one cancels have not lapsed yet.",
            f"- Day 60: the access-end share equals the share that renewed for a second month, the request-basis day 30 figure: {A[3]}% Amazon vs {D[3]}% direct.",
            f"- Day 90: the share that renewed for a third month, the request-basis day 60 figure: {A[4]}% Amazon vs {D[4]}% direct.",
            '',
            f"Direct's {D[8]}% on annual plans and bundles holds access through its term and sits inside the direct figures. The gap is the same "
            f"accounts counted a month later: {round(D[3] - A[3], 1)} points at day 60 and {round(D[4] - A[4], 1)} at day 90 on the access-end basis, "
            f"against {round(D[5] - A[5], 1)} points at day 90 as published on the cancel-request basis ({A[5]}% vs {D[5]}%). "
            + ISSUE_LINE,
        ]),
        'figures': [('Amazon Prime Video Channels starts, cohort base', A[0], 'subscriptions'), ('Direct starts, cohort base', D[0], 'subscriptions'),
                    ('Access still live at day 60, Amazon path', A[3], 'pct'), ('Access still live at day 60, direct path', D[3], 'pct'),
                    ('Access still live at day 90, Amazon path', A[4], 'pct'), ('Access still live at day 90, direct path', D[4], 'pct')],
    })

    # 9. 01:09:25, thread -by-service: day 90 by service, both paths, cancel-request basis
    out.append({
        'thread': 'api-8b09f13b-amazon-vs-direct-by-service', 'user_ts': '2026-11-11T01:09:25Z', 'replaces_ts': '2026-11-11T01:09:25Z',
        'question_prefix': 'For each of the ten services (HBO Max, Peacock, Paramount+, STARZ, Apple TV, BET+, MGM+, AMC+, BritBox, Acorn TV): 90 day survival',
        'reply': '\n'.join([f"On {SUBJECT}:", '',
                            f"90 day survival of new signups, one row per service, both paths on the cancel-request basis, {win}. "
                            f"All ten together: {A[5]}% of Amazon Prime Video Channels signups vs {D[5]}% of direct signups.",
                            ''] + d90_table + ['', ISSUE_LINE]),
        'figures': [('Still active at day 90, Amazon path', A[5], 'pct'), ('Still active at day 90, direct path', D[5], 'pct')],
    })
    return out


# the divergent ledger entries Liz's reads banked (keys) and the read jobs behind them
DIVERGENT_KEYS = [
    'amazon|subscribers|2025-07-01|2026-06-30',
    'premium_streaming_services_sold_through_amazon_prime_video_channels_vs_direct|subscribers|2025-11-11|2026-11-11',
    'amazon_prime_video_channels_vs_direct_billing_ten_streaming_services|subscribers|2025-11-11|2026-11-11',
    'amazon_prime_video_channels_vs_direct_billing_ten_streaming_services|subscribers|2025-11-01|2026-10-31',
    'amazon_prime_video_channels_vs_direct_signups_ten_premium_streaming_services|subscribers|2025-11-11|2026-11-11',
    'prime_video_channels_signups_vs_direct_signups_ten_streaming_services|subscribers|2025-11-11|2026-11-11',
]
DIVERGENT_JOBS = ['1f648cc05c41', '1a47cf28e38a', 'ba796c27894c', 'a395550f662b', 'b774c5c8e45b', 'eed820b0c9ac']
# Liz's dashboard thread the API jobs landed in (the active thread at the time)
LANDING_THREAD = '1e8a8362f5'


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _plus(ts, seconds):
    d = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return d.strftime('%Y-%m-%dT%H:%M:%SZ')


def do_bank(apply):
    import newsletter as nl
    block = record_block()
    print(f"record: {len(block['metrics'])} metrics, {len(block['breakdown']['rows'])} rows, {len(block['questions'])} natural asks")
    if not apply:
        print(json.dumps({k: v for k, v in block.items() if k not in ('metrics', 'breakdown', 'reply')}, indent=1)[:1500])
        return
    ok = nl.set_issue_figures(CAMPAIGN_ID, block)
    print('figures block on campaign:', ok)
    rep = nl.bank_issue_figures(CAMPAIGN_ID)
    print('banked:', json.dumps(rep, indent=1))


def do_reconcile(apply):
    import boto3
    from prometheus import thread_merge as tm
    s3 = boto3.client('s3')
    now = _now()
    answers = corrected_answers()
    by_thread = {}
    for a in answers:
        by_thread.setdefault(a['thread'], []).append(a)
    for tid, items in by_thread.items():
        key = THREAD_KEY.format(tid=tid)
        stored, _etag = tm.read_turns(s3, BUCKET, key)
        if apply:
            s3.put_object(Bucket=BUCKET, Key=f"system/synth_chat_threads/_backups/liz/{tid}.pre_record_reconcile_{now.replace(':', '')}.json",
                          Body=json.dumps(stored, indent=2).encode('utf-8'), ContentType='application/json')
        incoming = []
        for a in items:
            uturn = next((t for t in stored if str(t.get('role')) == 'user' and str(t.get('text') or '').startswith(a['question_prefix'][:60])), None)
            if not uturn:
                print(f"  !! user turn not found in {tid}: {a['question_prefix'][:50]}")
                continue
            idx = stored.index(uturn)
            agent = next((t for t in stored[idx + 1:] if str(t.get('role')) != 'user'), None)
            if agent is not None:
                am = dict(agent.get('meta') or {})
                am.update({'corrected_at': now, 'corrected_by': 'jenna', 'correction': 'superseded by the figures in the Amazon vs direct issue of The Read'})
                incoming.append({**agent, 'meta': am})
            base_ts = str((agent or uturn).get('ts') or a['user_ts'])
            reply_ts = _plus(base_ts.replace('.000Z', 'Z')[:20], 1) if base_ts else now
            incoming.append({
                'role': 'agent', 'text': a['reply'], 'ts': reply_ts,
                'meta': {'kind': 'answer', 'surface': 'analyze', 'server_note': True, 'delivered_by': 'prometheus',
                         'approved': 'jenna', 'corrected_from': base_ts, 'subject': SUBJECT,
                         'record': f'newsletter {CAMPAIGN_ID}',
                         'figures': [{'label': l, 'value': v, 'unit': u, 'kind': ('pct' if u == 'pct' else 'count')} for l, v, u in a['figures']],
                         'options': [{'label': c, 'send': c} for c in record_block()['followups'][:3]]},
            })
        print(f"thread {tid}: {len(items)} asks, {len(incoming)} turns to merge")
        if apply and incoming:
            merged = tm.merge_save(s3, BUCKET, key, incoming)
            print(f"  merged -> {len(merged)} turns")
    # the landing thread's read turns carry the same figures: mark them corrected there too
    key = THREAD_KEY.format(tid=LANDING_THREAD)
    stored, _etag = tm.read_turns(s3, BUCKET, key)
    inc = []
    for t in stored:
        jid = str((t.get('meta') or {}).get('read_job_id') or '')
        if jid in DIVERGENT_JOBS and not (t.get('meta') or {}).get('corrected_at'):
            m = dict(t.get('meta') or {})
            m.update({'corrected_at': now, 'corrected_by': 'jenna', 'correction': 'superseded by the figures in the Amazon vs direct issue of The Read'})
            inc.append({**t, 'meta': m})
    print(f"landing thread {LANDING_THREAD}: {len(inc)} read turns to mark corrected")
    if apply and inc:
        s3.put_object(Bucket=BUCKET, Key=f"system/synth_chat_threads/_backups/liz/{LANDING_THREAD}.pre_record_reconcile_{now.replace(':', '')}.json",
                      Body=json.dumps(stored, indent=2).encode('utf-8'), ContentType='application/json')
        tm.merge_save(s3, BUCKET, key, inc)
    # the ledger: retire the divergent entries
    import insights_ledger as il
    if apply:
        n = il.retire_entries(keys=DIVERGENT_KEYS, by='decision_of_record',
                              note='superseded by The Read, Amazon vs direct (the-read-amazon-vs-direct)')
        print(f"ledger: retired {n} entries")
    else:
        print(f"ledger: would retire {len(DIVERGENT_KEYS)} keys")
    # the job documents: corrected, figures retired
    for jid in DIVERGENT_JOBS:
        k = READ_KEY.format(jid=jid)
        try:
            doc = json.loads(s3.get_object(Bucket=BUCKET, Key=k)['Body'].read().decode('utf-8'))
        except Exception as e:
            print(f"  job {jid}: missing ({e})")
            continue
        doc['corrected_at'] = now
        doc['corrected_by'] = 'jenna'
        doc['retired'] = {'by': 'decision_of_record', 'record': f'newsletter {CAMPAIGN_ID}', 'ts': now,
                          'note': 'figures superseded by the Amazon vs direct issue of The Read'}
        print(f"  job {jid}: mark corrected ({doc.get('subject')})")
        if apply:
            s3.put_object(Bucket=BUCKET, Key=k, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bank', action='store_true')
    ap.add_argument('--reconcile', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    if a.bank:
        do_bank(a.apply)
    if a.reconcile:
        do_reconcile(a.apply)
    if not (a.bank or a.reconcile):
        ap.print_help()


if __name__ == '__main__':
    main()
