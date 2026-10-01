"""Proactive openers for the chat widget.

2026-10-01 (Jenna: approve of proactive mode). When the widget opens,
the welcome bubble can lead with up to three account-specific openers
instead of only the generic starters:

1. a quarter-close read on the account's most recent subject (first
   days of Jan / Apr / Jul / Oct),
2. a what-moved refresher on a subject the account asked about
   recently, and
3. a measured tracker mover (the engagement score that jumped or
   cooled the most in the latest week), preferring one the account
   already follows.

Surfacing respects the 2026-09-02 directive: openers render only
inside the on-widget-open welcome bubble (an allowed surface) and the
button-gated suggestions flow - never an idle re-offer, never an
unprompted email.

Everything here is read-only and fail-safe: any miss degrades to
fewer (or zero) chips, never an error in the widget.
"""

import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone

S3_BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
S3_REGION = os.environ.get('S3_REGION', 'us-east-2')
ASK_PREFIX = 'system/usage/ask_log/'

_TTL_SECONDS = 3600
_cache = {}

# Subjects too generic to make a useful opener.
_SKIP_SUBJECTS = {'', 'general', 'unknown', 'none', 'n/a', 'dashboard'}

_SUBJ_STOP = {'the', 'a', 'an', 'and', 'of', 'show', 'series', 'movie',
              'audience', 'fans', 'viewers', 'profile'}


def _subject_fits(question, subject):
    """True when the stamped subject is what the question is about.
    Long asks that never name the subject were stamped with whatever
    was open on screen (a titles-list ask filed under the open
    profile); those make wrong openers. The check is whole-phrase:
    single tokens collide with unrelated titles in list asks ("8
    Seconds" and "One Crazy Summer" both hit "5 Seconds of Summer").
    Short asks read as deictic ("analyze this data") and keep the
    page subject."""
    q = str(question or '')
    if len(q) < 60:
        return True
    ql = f" {_norm(q)} "
    phrase = _norm(subject)
    if not phrase:
        return True
    return f" {phrase} " in ql


def _s3():
    import boto3
    return boto3.client('s3', region_name=S3_REGION)


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def _day_records(s3, day):
    """Ask records for one day: the folded JSONL when the weekly
    miner has run, else the raw per-ask objects still sitting under
    the day prefix. [] when the day is empty."""
    try:
        body = s3.get_object(
            Bucket=S3_BUCKET,
            Key=f"{ASK_PREFIX}{day}.jsonl")['Body'].read()
        out = []
        for line in body.decode('utf-8', 'replace').splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        pass
    out = []
    try:
        resp = s3.list_objects_v2(
            Bucket=S3_BUCKET, Prefix=f"{ASK_PREFIX}{day}/", MaxKeys=60)
        keys = [o['Key'] for o in resp.get('Contents') or []]
        for key in keys[-20:]:
            try:
                out.append(json.loads(
                    s3.get_object(Bucket=S3_BUCKET,
                                  Key=key)['Body'].read()))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _display_subject(subj):
    """Human spelling for an opener: stored keys sometimes carry
    underscores (5_SECONDS_OF_SUMMER)."""
    s = str(subj or '').replace('_', ' ').strip()
    if s.isupper() and len(s) > 4:
        s = s.title()
    return s


def _recent_subjects(uname, days=10, cap=12):
    """Subjects this account asked about recently, newest first,
    deduped. Folded day files plus the raw objects the weekly fold
    has not reached yet; missing days skip. Records answered while
    the asker was away from the stamped view are dropped: before
    2026-10-01 those carried the open page's subject rather than the
    question's, so they mis-describe what the account asked about."""
    rows = []
    seen = set()
    now = datetime.now(timezone.utc)
    s3 = _s3()
    for i in range(days):
        day = (now - timedelta(days=i)).strftime('%Y-%m-%d')
        for d in _day_records(s3, day):
            if str(d.get('user') or '').strip() != uname:
                continue
            if str(d.get('outcome') or '') == 'answered_away':
                continue
            subj = str(d.get('subject') or '').strip()
            if len(subj) < 3 or subj.lower() in _SKIP_SUBJECTS:
                continue
            if not _subject_fits(d.get('question'), subj):
                continue
            key = _norm(subj)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append((str(d.get('ts') or ''), _display_subject(subj)))
    rows.sort(reverse=True)
    return [s for _ts, s in rows[:cap]]


def _quarter_close(now=None):
    """(label, start phrase, end phrase) during the first 12 days of a
    new quarter, else None."""
    now = now or datetime.now(timezone.utc)
    if now.month not in (1, 4, 7, 10) or now.day > 12:
        return None
    qend = now.replace(day=1) - timedelta(days=1)
    q = (qend.month - 1) // 3 + 1
    qstart = qend.replace(month=qend.month - 2, day=1)
    fmt = '%B %d'
    return (f"Q{q}",
            qstart.strftime(fmt).replace(' 0', ' '),
            qend.strftime(fmt).replace(' 0', ' '))


def suggestions(username):
    """{'chips': [{'label', 'send'}]} for this account, at most three.
    Cached one hour per account. Never raises."""
    uname = str(username or '').strip()
    if not uname:
        return {'chips': []}
    hit = _cache.get(uname)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]
    chips = []
    try:
        subs = _recent_subjects(uname)
    except Exception:
        traceback.print_exc()
        subs = []
    qc = _quarter_close()
    if qc and subs:
        qlabel, qs, qe = qc
        chips.append({
            'label': f"{qlabel} wrap on {subs[0]}"[:60],
            'send': (f"{qlabel} just closed. Give me the quarter-end "
                     f"read on {subs[0]} for {qs} to {qe}: the "
                     f"headline numbers and what changed inside the "
                     f"quarter."),
        })
    if subs:
        pick = subs[1] if (chips and len(subs) > 1) else subs[0]
        chips.append({
            'label': f"What moved for {pick}"[:60],
            'send': (f"What moved for {pick} since my last read? "
                     f"Latest window, biggest shifts first."),
        })
    try:
        import panel_fact_store as pfs
        movers = pfs.top_movers(k=12)
    except Exception:
        movers = []
    if movers:
        sub_norms = {_norm(s) for s in subs}
        picked = None
        for name, prev, cur, delta in movers:
            disp = str(name).replace('_', ' ').strip()
            if _norm(disp) in sub_norms:
                picked = (disp, delta)
                break
        if picked is None:
            picked = (str(movers[0][0]).replace('_', ' ').strip(),
                      movers[0][3])
        disp, delta = picked
        word = 'jumped' if float(delta or 0) > 0 else 'cooled'
        chips.append({
            'label': f"{disp} {word} this week"[:60],
            'send': (f"{disp}'s engagement {word} in the latest "
                     f"tracked week. What is behind the move, and "
                     f"does it change the read?"),
        })
    payload = {'chips': chips[:3]}
    _cache[uname] = (time.time(), payload)
    return payload
