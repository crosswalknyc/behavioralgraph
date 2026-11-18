"""Rankers boards and multi-year reads (2026-11-08, out of chat.py).

The overall Rankers reply and the board block a Rankers-view ask reads
from, the multi-year ask detector with its year-package proposal, and
the year-over-year trajectory read off the shelf's year files. Reads
the host through ``_H`` and the rest of the legacy chat module through
``_C`` (``prometheus.legacy.C``), both bound before this module is
imported, so nothing here imports chat.py and chat.py imports this
module last. Behavior is byte-for-byte the code that lived in chat.py;
only the indirection changed.
"""
from prometheus import file_names as _file_names   # a download's name is one a link can carry (2026-11-16)
import csv
import io
import re
import traceback
import uuid
from datetime import datetime, timezone

import pandas as pd

from prometheus.legacy import H as _H, C as _C  # noqa: E402

__all__ = ['_pm_overall_rankers_reply', '_PM_RANKER_FAMILIES', '_pm_rankers_board_block', '_PM_MY_RANGE_RE', '_PM_MY_LAST_RE', '_PM_MY_NYEAR_RE', '_PM_MY_SINCE_RE', '_PM_MY_SPAN_RE', '_PM_MY_YOY_RE', '_pm_detect_multi_year_ask', '_pm_year_files_for_subject', '_pm_year_package_reply', '_pm_trajectory_files', '_pm_trajectory_metrics', '_pm_trajectory_reply']


def _pm_overall_rankers_reply(n=10):
    """Top shows and movies by views, FAST then streaming.

    Reads the same Rankers cache the dashboard renders. Tries today,
    then the prior two days, because the live day is often still the
    prior board until the next refresh. Never raises.
    """
    try:
        if _H._trends_iq is None:
            raise RuntimeError("rankers unavailable")
        from datetime import timedelta
        payload = None
        for back in range(0, 3):
            day = (datetime.now(timezone.utc).date()
                   - timedelta(days=back)).isoformat()
            payload = _H._trends_iq._cache_get({
                'geo_type': 'National', 'geo_value': '',
                'lookback_days': _H._trends_iq.DEFAULT_LOOKBACK_DAYS,
                'asof': day})
            cards = (payload or {}).get('cards') or {}
            if cards.get('streaming_trending') or cards.get('fast_trending'):
                break
            payload = None
        if not payload:
            raise RuntimeError("rankers cache empty")
        cards = payload.get('cards') or {}
        asof = str((payload.get('filters') or {}).get('asof') or '')
        try:
            when = datetime.strptime(asof, '%Y-%m-%d').strftime('%B %-d, %Y')
        except Exception:
            when = asof

        def _ranked(fam):
            best = {}
            for slug, svc in (fam or {}).items():
                if not isinstance(svc, dict) or str(slug).endswith('_amazon'):
                    continue
                platform = str(svc.get('label') or slug)
                for bucket, kind in (('tv', 'Show'), ('films', 'Movie')):
                    for row in (svc.get(bucket) or []):
                        if not isinstance(row, dict):
                            continue
                        title = str(row.get('title') or '').strip()
                        us = row.get('us_streams')
                        if isinstance(us, dict):
                            us = us.get('us_estimate')
                        if not title or not isinstance(us, (int, float)) or us <= 0:
                            continue
                        cat = str(row.get('category')
                                  or row.get('category_display') or '').lower()
                        if cat == 'film':
                            kind_l = 'Movie'
                        elif cat == 'tv':
                            kind_l = 'Show'
                        else:
                            kind_l = kind
                        key = (title.lower(), platform.lower())
                        views = int(us)
                        prev = best.get(key)
                        if prev is None or views > prev[0]:
                            best[key] = (views, title, kind_l, platform)
            return sorted(best.values(), key=lambda r: -r[0])[:n]

        def _block(label, rows):
            if not rows:
                return ''
            lines = [f"{label}" + (f", {when}" if when else "")]
            for i, (views, title, kind, platform) in enumerate(rows, 1):
                lines.append(
                    f"{i}. {title} | {kind} | {platform} | {views:,} views")
            return "\n".join(lines)

        parts = [
            "These are the overall ranks, across services.",
            _block("FAST", _ranked(cards.get('fast_trending'))),
            _block("Streaming", _ranked(cards.get('streaming_trending'))),
            "For the full boards, open Rankers.",
        ]
        reply = "\n\n".join(p for p in parts if p)
        if "views" not in reply:
            raise RuntimeError("rankers reply empty")
        return reply
    except Exception:
        traceback.print_exc()
        return ("Working on it! I will email you the results when "
                "they are ready.")


# Rankers board families: card key -> (family label, text keywords that
# imply the family when no service is named). Row shapes per family are
# rank/title (+artist, us_streams, weeks_in_top10 where the scraper
# carries them).
_PM_RANKER_FAMILIES = (
    ('streaming_trending', 'Streaming',
     ('series', 'show', 'season', 'stream', 'svod', 'episode')),
    ('music_trending', 'Music',
     ('song', 'artist', 'album', 'track', 'music')),
    ('podcasts_trending', 'Podcasts', ('podcast',)),
    ('fast_trending', 'FAST', ('fast channel', 'fast platform')),
    ('gaming_trending', 'Gaming', ('game', 'gaming')),
)


def _pm_rankers_board_block(text, view_id=''):
    """Compact Rankers board rows for a board-view ask (2026-09-21,
    Jenna: 'top 10 Starz series last 30 days' asked on the Rankers view
    must answer from the board, which never rode the prompt). Reads the
    same cached Trends IQ view the dashboard renders. Service named in
    the ask -> that service's boards (top 10 TV + films / items).
    Family keyword only -> top 5 per service across the family. Neither
    (but the view IS the Rankers board) -> streaming family top 5s.
    Returns '' on any miss; never raises."""
    if _H._trends_iq is None or view_id not in ('cultureRankerIQ',
                                             'trendsIQ'):
        return ''
    try:
        p = _H._trends_iq._cache_get({
            'geo_type': 'National', 'geo_value': '',
            'lookback_days': _H._trends_iq.DEFAULT_LOOKBACK_DAYS})
        cards = (p or {}).get('cards') or {}
        tl = str(text or '').lower()

        def _fmt_rows(rows, n):
            out = []
            for r in (rows or [])[:n]:
                if not isinstance(r, dict):
                    continue
                bits = [f"{r.get('rank', '?')}. "
                        f"{r.get('title') or r.get('name') or '?'}"]
                if r.get('artist'):
                    bits.append(f"- {r['artist']}")
                # us_streams is an int on some scrapers and an estimate
                # object on others - take the point estimate only,
                # never the method/source internals.
                us = r.get('us_streams')
                if isinstance(us, dict):
                    us = us.get('us_estimate')
                if isinstance(us, (int, float)) and us > 0:
                    bits.append(f"({int(us):,} US streams)")
                if r.get('weeks_in_top10'):
                    bits.append(f"[{r['weeks_in_top10']} wks in top 10]")
                out.append(' '.join(str(b) for b in bits))
            return out

        def _service_lines(svc, label, top_n):
            lines = []
            tv, films = svc.get('tv') or [], svc.get('films') or []
            items = svc.get('items') or []
            if tv or films:
                if tv:
                    lines.append(f"{label} - TV (yesterday's US ranks):")
                    lines += ['  ' + x for x in _fmt_rows(tv, top_n)]
                if films:
                    lines.append(f"{label} - Films:")
                    lines += ['  ' + x for x in _fmt_rows(films, top_n)]
            elif items:
                lines.append(f"{label}:")
                lines += ['  ' + x for x in _fmt_rows(items, top_n)]
            return lines

        named, family_hit = [], None
        for card_key, fam_label, kws in _PM_RANKER_FAMILIES:
            fam = cards.get(card_key)
            if not isinstance(fam, dict):
                continue
            for skey, svc in fam.items():
                if not isinstance(svc, dict):
                    continue
                label = str(svc.get('label') or skey)
                if re.search(r'\b' + re.escape(label.lower()) + r'\b',
                             tl):
                    named.append((fam_label, label, svc))
            if family_hit is None and any(k in tl for k in kws):
                family_hit = (card_key, fam_label)

        lines = []
        if named:
            for fam_label, label, svc in named[:3]:
                lines += _service_lines(svc, f"{fam_label} / {label}",
                                        12)
        else:
            card_key, fam_label = (family_hit or
                                   ('streaming_trending', 'Streaming'))
            fam = cards.get(card_key) or {}
            for skey, svc in list(fam.items())[:14]:
                if isinstance(svc, dict):
                    lines += _service_lines(
                        svc, str(svc.get('label') or skey), 5)
        if not lines:
            return ''
        block = ("RANKERS BOARD (the view the user is looking at; "
                 "yesterday's US ranks from the panel)\n"
                 "==========================================\n"
                 + "\n".join(lines))
        return block[:7000]
    except Exception:
        traceback.print_exc()
        return ''


# Multi-year trend detection (2026-09-22, Scott's Will & Grace ask): a
# '4-5 year analysis' needs one profile per year. The read path had
# only the single-window base, invented the per-year numbers, and the
# verifier held the reply - the user paid attention and got nothing.
# These asks now route to a paid year-build package up front.
_PM_MY_RANGE_RE = re.compile(
    r'\b(\d)\s*(?:-|to|or)\s*(\d)\s*[- ]?year', re.I)


_PM_MY_LAST_RE = re.compile(
    r'\b(?:past|last|previous|over the (?:past|last))\s+(\d+)\s+years?',
    re.I)


_PM_MY_NYEAR_RE = re.compile(r'\b(\d+)[- ]year\b', re.I)


_PM_MY_SINCE_RE = re.compile(r'\bsince\s+(20\d\d)\b', re.I)


_PM_MY_SPAN_RE = re.compile(
    r'\b(20\d\d)\s*(?:-|to|through)\s*(20\d\d)\b', re.I)


_PM_MY_YOY_RE = re.compile(
    r'\byear[- ]over[- ]year\b|\byoy\b|\bannual trend\b', re.I)


_PM_MY_MONTH_BEFORE_RE = re.compile(
    r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|'
    r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?$', re.I)


def _pm_month_to_month_window(text, span_match):
    """True when a year span is a month-to-month window of about one
    year ("November 2025 - 2026", "Nov 2025 to 2026"): the first year
    follows a month name and the second is the very next year. A span
    of bare calendar years ("2025 - 2026"), or one that runs past the
    next year, is still a multi-year ask, and so is any ask that says
    year over year."""
    try:
        a, b = int(span_match.group(1)), int(span_match.group(2))
        if b != a + 1:
            return False
        if _PM_MY_YOY_RE.search(str(text or '')):
            return False
        return bool(_PM_MY_MONTH_BEFORE_RE.search(str(text or '')[:span_match.start(1)]))
    except Exception:
        return False


def _pm_detect_multi_year_ask(text):
    """Calendar years a multi-year trend ask spans (2+ years), newest
    ending this year, capped at 6. None when the ask is not a
    multi-year read ('4-5 year analysis', 'past 3 years', 'since
    2022', '2021-2025', 'year over year')."""
    t = str(text or '')
    try:
        # a brief that sizes named titles year by year is one read on those titles; the year-profile package is
        # for a year-over-year study of a profile and never takes the page for a subject the words name
        # (Grace Yin 2026-11-11, prometheus.read_brief)
        from prometheus import read_brief as _rb
        if _rb.is_brief(t) or (_rb.holds(t) and _rb.titles(t)):
            return None
        # words that state their own base and count engagement ("Audience (base): all US people ... each calendar
        # year separately: 2021 ... 2026") are a measurement of their own, whatever profile is open and whether or
        # not every title in them was recognized (Grace Yin 2026-11-17: six year profiles of Hazbin Hotel at $300)
        if _rb.stands_on_its_own(t):
            return None
    except Exception:
        pass
    this_year = datetime.now().year
    n = None
    m = _PM_MY_SPAN_RE.search(t)
    if m and _pm_month_to_month_window(t, m):
        # "November 2025 - 2026" is one window of about a year, month to
        # month, never a two-year study (Liz Huszarik 2026-11-17: "pull the
        # total viewers to FBI Most Wanted and FBI International ... across
        # November 2025 - 2026" drew a package of two year profiles)
        return None
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        years = list(range(a, min(b, this_year) + 1))
        return years if len(years) >= 2 else None
    m = _PM_MY_SINCE_RE.search(t)
    if m:
        a = int(m.group(1))
        years = list(range(a, this_year + 1))
        return years[-6:] if len(years) >= 2 else None
    m = _PM_MY_RANGE_RE.search(t)
    if m:
        n = max(int(m.group(1)), int(m.group(2)))
    if n is None:
        m = _PM_MY_LAST_RE.search(t) or _PM_MY_NYEAR_RE.search(t)
        if m:
            n = int(m.group(1))
    if n is None and _PM_MY_YOY_RE.search(t):
        n = 3
    if not n or n < 2:
        return None
    n = min(n, 6)
    return list(range(this_year - n + 1, this_year + 1))


def _pm_year_files_for_subject(subject, years):
    """(have, missing) year lists based on the catalog: a year is
    covered when a profile's display name carries the subject's
    distinctive tokens AND that year."""
    toks = [w for w in re.findall(r'[a-z0-9]+', str(subject).lower())
            if len(w) >= 3]
    have = set()
    try:
        for ent in (_C._profile_catalog_for_chat() or []):
            name = str(ent.get('display_name') or ent.get('s3_key')
                       or '').lower()
            if toks and all(t in name for t in toks):
                for y in years:
                    if str(y) in name:
                        have.add(y)
    except Exception:
        traceback.print_exc()
    return sorted(have), [y for y in years if y not in have]


def _pm_year_package_reply(subject, years, have, missing):
    """Proposal copy + chips for the year-build package. Live pricing
    from the billing panel."""
    try:
        each_usd = _C._pm_usd_label(_H._v1_price_usd_for('new_build', 0))
    except Exception:
        each_usd = _C._pm_usd_label(500)
    n = len(missing)
    year_list = ', '.join(str(y) for y in missing[:-1]) + \
        (f' and {missing[-1]}' if n > 1 else str(missing[0]))
    # A seat the plan covers reads no price on a build (Jenna 2026-11-08,
    # the one shared check; Liz Huszarik read "Included with your plan."
    # and then "at $300 each" in the same reply on 2026-11-17).
    try:
        from prometheus import ask_outcome as _ao_inc
        _included = bool(_ao_inc.seat_included())
    except Exception:
        _included = False
    lines = [
        f"A {len(years)}-year read of {subject} needs one profile per "
        f"year - each year's audience is measured in its own window, "
        f"and the file on hand covers a single window only. I will "
        f"not stretch one window across {len(years)} years.",
        '',
        f"The package: {n} year profile{'s' if n != 1 else ''} "
        f"({year_list})" + (", included with your plan." if _included else f" at {each_usd} each."),
    ]
    if have:
        lines.append(f"Already on the shelf: "
                     f"{', '.join(str(y) for y in have)} - those "
                     f"years ride free.")
    lines.append('')
    lines.append("Once the year files land, the year-over-year "
                 "comparison (composition, platform mix, performance) "
                 "runs across all of them.")
    build_chip = (f"Build {subject} year profiles for "
                  f"{', '.join(str(y) for y in missing)}")
    chips = [build_chip,
             f"What does the current {subject} profile show?"]
    return '\n'.join(lines), chips


def _pm_trajectory_files(subject, years):
    """{year: s3_key} for shelf files carrying the subject's tokens
    and that year in the display name. Newest catalog entry wins."""
    toks = [w for w in re.findall(r'[a-z0-9]+', str(subject).lower())
            if len(w) >= 3]
    out = {}
    try:
        for ent in (_C._profile_catalog_for_chat() or []):
            name = str(ent.get('display_name') or ent.get('s3_key')
                       or '').lower()
            key = str(ent.get('s3_key') or '').strip()
            if not key or not toks or not all(t in name for t in toks):
                continue
            for y in years:
                if str(y) in name and y not in out:
                    out[y] = key
    except Exception:
        traceback.print_exc()
    return out


def _pm_trajectory_metrics(s3_key):
    """Headline metrics from one year file: us_audience, sample,
    female_pct, u25_pct, top streaming (name, bp). None on trouble."""
    try:
        obj = _H.s3_client.get_object(Bucket=_H.S3_BUCKET, Key=s3_key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()))
    except Exception:
        return None
    cols = {re.sub(r'[^a-z]', '', str(c).lower()): c for c in df.columns}

    def col(*needles):
        for norm, orig in cols.items():
            if all(n in norm for n in needles):
                return orig
        return None
    c_col = col('column')
    c_val = col('value')
    c_bp = col('brandpenetration')
    c_raw = col('raw')
    c_proj = col('projection')
    if not all((c_col, c_val, c_bp)):
        return None

    def num(v):
        try:
            return float(re.sub(r'[%,$]', '', str(v)))
        except Exception:
            return None
    out = {}
    up = df[c_col].astype(str).str.strip().str.upper()
    vals = df[c_val].astype(str).str.strip()
    ss = df[(up == 'SAMPLE SIZE')]
    if len(ss) and c_proj:
        out['us_audience'] = num(ss.iloc[0][c_proj])
    if len(ss) and c_raw:
        out['sample'] = num(ss.iloc[0][c_raw])
    g = df[(up == 'GENDER') & (vals.str.upper() == 'FEMALE')]
    if len(g):
        out['female_pct'] = num(g.iloc[0][c_bp])
    a = df[up == 'AGE']
    u25 = 0.0
    seen_u25 = False
    for _, r in a.iterrows():
        lab = str(r[c_val]).strip()
        if lab in ('13-17', '18-24', 'UNDER 18', '13 TO 17', '18 TO 24'):
            v = num(r[c_bp])
            if v is not None:
                u25 += v
                seen_u25 = True
    if seen_u25:
        out['u25_pct'] = u25
    s = df[up.isin(('STREAMING/PLATFORM', 'STREAMING VIDEO'))].copy()
    if len(s):
        s['_bp'] = s[c_bp].map(num)
        s = s[(s['_bp'].notna()) & (s['_bp'] < 99.5)]
        if len(s):
            top = s.sort_values('_bp', ascending=False).iloc[0]
            out['top_stream'] = (str(top[c_val]).strip(),
                                 float(top['_bp']))
    return out or None


def _pm_trajectory_reply(username, subject, years, text):
    """Deterministic multi-year series answer from the shelf's year
    files: headline trend, per-year lines, and the answer CSV. None
    when fewer than 2 year files load cleanly (the ask then falls
    through to normal routing)."""
    files = _pm_trajectory_files(subject, years)
    series = []
    for y in sorted(files):
        m = _pm_trajectory_metrics(files[y])
        if m and m.get('us_audience'):
            m['year'] = y
            series.append(m)
    if len(series) < 2:
        return None
    first, last = series[0], series[-1]
    lines = [f"{subject}, year over year:"]
    prev = None
    for m in series:
        aud = int(m['us_audience'])
        line = f"- {m['year']}: {aud:,} US audience"
        if prev:
            d = (aud - prev) / prev * 100.0
            line += f" ({'up' if d >= 0 else 'down'} {abs(d):.1f}%)"
        prev = aud
        lines.append(line)
    net = ((last['us_audience'] - first['us_audience'])
           / first['us_audience'] * 100.0)
    lines.append('')
    lines.append(
        f"Net: {'up' if net >= 0 else 'down'} {abs(net):.1f}% from "
        f"{first['year']} to {last['year']}.")
    comp_bits = []
    if first.get('female_pct') and last.get('female_pct'):
        comp_bits.append(
            f"female share moved {first['female_pct']:.1f}% to "
            f"{last['female_pct']:.1f}%")
    if first.get('u25_pct') and last.get('u25_pct'):
        comp_bits.append(
            f"under-25 share moved {first['u25_pct']:.1f}% to "
            f"{last['u25_pct']:.1f}%")
    if comp_bits:
        lines.append("Composition: " + "; ".join(comp_bits) + ".")
    tops = [m['top_stream'][0] for m in series if m.get('top_stream')]
    if tops:
        if len(set(tops)) == 1:
            lines.append(f"{tops[0]} led streaming in every year.")
        else:
            lines.append(
                "Streaming leader by year: "
                + ", ".join(f"{m['year']} {m['top_stream'][0]}"
                            for m in series if m.get('top_stream'))
                + ".")
    reply = "\n".join(lines)
    payload = {'success': True, 'action': 'answer', 'reply': reply,
               'followups': [], 'offer_deck': False, 'deck_angle': None}
    try:
        hdr = ['Year', 'US Audience', 'Female %', 'Under-25 %',
               'Top Streaming Platform', 'Top Platform %']
        rows = []
        for m in series:
            ts = m.get('top_stream') or ('', '')
            rows.append([
                m['year'], int(m['us_audience']),
                (f"{m['female_pct']:.1f}" if m.get('female_pct')
                 else ''),
                (f"{m['u25_pct']:.1f}" if m.get('u25_pct') else ''),
                ts[0], (f"{ts[1]:.1f}" if ts[0] else '')])
        buf = io.StringIO()
        _w = csv.writer(buf)
        _w.writerow(hdr)
        _w.writerows(rows)
        csv_text = buf.getvalue()
        safe = re.sub(r'[^A-Za-z0-9]+', '_', str(subject)).strip('_')
        fname = (f"{safe}_Year_Over_Year_"
                 f"{series[0]['year']}_{series[-1]['year']}.csv")
        s3_key = f"{_C._PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
        _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=s3_key,
                             Body=csv_text.encode('utf-8'),
                             ContentType='text/csv')
        url = _H.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        _file_names.disposition(fname)},
            ExpiresIn=7 * 24 * 3600)
        _C._pm_file_stash_write(username, url, fname, s3_key,
                             subject=subject, question=text)
        payload['file_link'] = {'url': url, 'label': f"Download {fname}"}
        payload['followups'] = ['Email me this file']
    except Exception:
        traceback.print_exc()
    return payload
