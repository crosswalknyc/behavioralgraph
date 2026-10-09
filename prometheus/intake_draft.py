"""Guided intakes propose, never interrogate (2026-10-09, item 4).

"Create a digital journey" and "Value a brand partnership" drew a
field-by-field intake five times in the week to 2026-10-08, every time
with a profile open on the screen. The profile already carries what the
brief needs: the subject, where its audience watches or buys, the
brands it over-indexes on, and the standing window. This module drafts
the full brief from the open profile so Prometheus asks for one
confirmation instead of three fields.

  journey_draft(ctx, s3, bucket)  -> parsed inputs or None
  bpiq_draft(ctx, s3, bucket)     -> parsed inputs or None
  draft_lead(kind, page)          -> the one line that says where the
                                     draft came from and how to change it
  prior_draft(history)            -> the last drafted brief as text, so
                                     an edit ("make it Hulu") merges with
                                     the draft instead of starting over

Pure data plus the profile loader; every function returns None / ''
on any trouble so the intake falls back to its ask copy.
"""
from __future__ import annotations

import re
import traceback

from prometheus.intake_reader import (PLATFORMS, _PLATFORM_KIND, _DEFAULT_STEP,
                                      find_platforms)

DRAFT_MARK = "from the profile on your screen"
_JOURNEY_LEAD = ("Here's the journey I'd run {mark} ({page}). Say 'Run the journey', "
                 "or tell me what to change and I will redraft it.")
_BPIQ_LEAD = ("Here's the valuation I'd run {mark} ({page}). Say 'Run it', or tell me "
              "what to change (the brand, the window) and I will redraft it.")

# Profile columns that carry a platform the audience uses, in the order
# a journey's end step should prefer them.
_PLATFORM_COLS = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'STREAMING MUSIC', 'WHERE THEY SHOP',
                  'APP/PLATFORM', 'APP/PLATFORM USAGE', 'GAMES', 'MOVIE THEATER', 'TICKETING')
# Brand columns a partnership is valued against, with the floor on the
# audience share a candidate partner must clear.
_PARTNER_COLS = ('MOST PURCHASED BRANDS', 'APPAREL/FOOTWEAR', 'AUTOMOBILE', 'QSR', 'BEVERAGE',
                 'BEAUTY/WELLNESS', 'TECHNOLOGY BRAND', 'TELECOM', 'BANKING', 'CREDIT PROVIDER',
                 'INSURANCE', 'TRAVEL', 'WHERE THEY SHOP')
_PARTNER_FLOOR_PCT = 4.0
_CANON_LOW = {c.lower(): c for c, _k, _a in PLATFORMS}
_CAT_PLATFORM_RX = re.compile(r"^\s*(?:SERIES|MOVIE|SHOW|FILM)\s*[-:]\s*(?P<p>.+?)\s*$", re.I)


def _page(ctx):
    prim = (ctx or {}).get('primary') if isinstance(ctx, dict) else None
    if not isinstance(prim, dict):
        return '', ''
    return str(prim.get('name') or '').strip(), str(prim.get('s3_key') or '').strip()


def _subject_of(page):
    return str(page or '').split(' - ')[0].strip()


def _load(s3, bucket, key):
    try:
        import prometheus_analysis as pma
        df, _etag = pma.load_profile_df(s3, bucket, key)
        return df, pma._bp_col(df), pma
    except Exception:
        return None, None, None


def _rows(df, bp_col, cols):
    """(label, bp) rows for the named columns, by BP desc."""
    out = []
    try:
        col = df['Column'].astype(str).str.strip().str.upper()
        for c in cols:
            grp = df[col == c]
            for label, bpv in zip(grp['Value'].tolist(), grp[bp_col].tolist()):
                try:
                    v = float(str(bpv).replace('%', '').replace(',', '').strip())
                except (TypeError, ValueError):
                    continue
                out.append((str(label or '').strip(), v, c))
    except Exception:
        pass
    out.sort(key=lambda r: -r[1])
    return out


def _canon_platform(label):
    """The intake's canonical platform for a profile row label, or ''."""
    hits = find_platforms(str(label or ''))
    if hits:
        return hits[0][0]
    return _CANON_LOW.get(str(label or '').strip().lower(), '')


def _brand_category(df):
    try:
        col = df['Column'].astype(str).str.strip().str.upper()
        vals = df[col == 'BRAND CATEGORY']['Value'].tolist()
        return str(vals[0] or '').strip() if vals else ''
    except Exception:
        return ''


def journey_draft(ctx, s3, bucket):
    """A complete journey brief from the open profile, or None."""
    try:
        page, key = _page(ctx)
        subject = _subject_of(page)
        if not subject or not key:
            return None
        df, bp_col, pma = _load(s3, bucket, key)
        if df is None:
            return None
        platform = ''
        # A title's own platform first (BRAND CATEGORY "SERIES - Netflix").
        m = _CAT_PLATFORM_RX.match(_brand_category(df))
        if m:
            platform = _canon_platform(m.group('p'))
        if not platform:
            subj_norm = re.sub(r'[^a-z0-9]+', '', subject.lower())
            for label, _v, _c in _rows(df, bp_col, _PLATFORM_COLS):
                if re.sub(r'[^a-z0-9]+', '', label.lower()) == subj_norm:
                    continue   # the subject is not its own destination
                cand = _canon_platform(label)
                if cand and _PLATFORM_KIND.get(cand) not in (None, 'social'):
                    platform = cand
                    break
        if not platform:
            return None
        pkind = _PLATFORM_KIND.get(platform) or 'video'
        jk, tmpl = _DEFAULT_STEP.get(pkind, _DEFAULT_STEP['video'])
        parsed = {'subject': subject, 'platform': platform,
                  'conversion_event': tmpl.format(subject=subject, platform=platform),
                  'journey_kind': jk, '_drafted_from': page}
        return parsed
    except Exception:
        traceback.print_exc()
        return None


def bpiq_draft(ctx, s3, bucket):
    """A complete valuation brief from the open profile: the profile is
    the partner, its strongest brand affinity is the brand, the window
    is the standing trailing 12 months. None when no brand clears the
    floor."""
    try:
        page, key = _page(ctx)
        subject = _subject_of(page)
        if not subject or not key:
            return None
        df, bp_col, pma = _load(s3, bucket, key)
        if df is None:
            return None
        genpop = {}
        try:
            genpop = pma.load_genpop_map(s3, bucket) or {}
        except Exception:
            genpop = {}
        best, best_idx = '', 0.0
        subj_norm = re.sub(r'[^a-z0-9]+', '', subject.lower())
        for label, v, cat in _rows(df, bp_col, _PARTNER_COLS):
            if v < _PARTNER_FLOOR_PCT or not label:
                continue
            if re.sub(r'[^a-z0-9]+', '', label.lower()) == subj_norm:
                continue
            gp = None
            try:
                gp = genpop.get((cat, pma._norm_brand(label)))
            except Exception:
                gp = None
            idx = (v / gp * 100.0) if gp and gp >= 0.01 else v  # no US base: rank by share
            if idx > best_idx:
                best, best_idx = label, idx
        if not best:
            return None
        try:
            from migration.event_window import default_window
            start, end = default_window()
        except Exception:
            import datetime as _dt
            end = _dt.date.today()
            start = end.replace(year=end.year - 1)
        parsed = {'brand_partner': best, 'qualifier': subject,
                  'event_start': str(start)[:10], 'event_end': str(end)[:10],
                  'audience': f'{subject} audience', '_drafted_from': page}
        return parsed
    except Exception:
        traceback.print_exc()
        return None


def draft_lead(kind, page):
    tmpl = _BPIQ_LEAD if kind == 'bpiq' else _JOURNEY_LEAD
    return tmpl.format(mark=DRAFT_MARK, page=page)


def prior_draft(history, limit=6):
    """The last drafted brief in the thread as text (the agent turn that
    carried the draft lead), so an edit merges with it. '' when the
    thread has none in its last few agent turns."""
    seen = 0
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role == 'user':
            continue
        seen += 1
        if seen > limit:
            break
        txt = str(h.get('text') or h.get('content') or '')
        if DRAFT_MARK in txt:
            return txt
    return ''


# The ask copy both intakes fall back to when nothing can be drafted
# (moved out of the read core 2026-10-09).
JIQ_ASK_COPY = (
    "Happy to build a Digital Journey. Give me, in one message:\n"
    "1. The category or title the journey follows (e.g. luxury "
    "fragrance, running shoes, Young Sheldon). Or paste the clip URL "
    "when the file is before and after a specific post.\n"
    "2. The end step in one sentence. It can be a purchase (e.g. "
    "paid $95+ for a house bottle on TikTok Shop), a watch (e.g. "
    "watched a paid episode on Amazon after a clip), a ticketing-site "
    "visit for a film, a 20-minute before-and-after around a clip, "
    "new-to-platform vs already on it, or a music-first path into a "
    "title. 'Engaged with the category' is not an end step.\n"
    "3. Where that end step happens (e.g. TikTok Shop, Amazon, "
    "Peacock, Instagram, Pluto)\n"
    "Optional: a defined starting point (e.g. accounts that watched "
    "short-form clips of the title; default is US gen pop) and the "
    "window (default: trailing 12 months).\n"
    "Every journey includes a Clickstream last tab: the public URLs "
    "on each step, with people on each URL.\n\n"
    "Examples:\n"
    "\"Running shoes on Amazon, end step is paid $120+ for a "
    "performance shoe, trailing 12 months\"\n"
    "\"Young Sheldon, start from accounts that watched short-form "
    "clips of the show, end step is watched a paid episode on "
    "Amazon\"\n"
    "\"Build a journey of people who watched this video and what "
    "happened before and after: https://www.instagram.com/p/xxxxx/\"")


BPIQ_ASK_COPY = (
    "Happy to run a Brand Partnership Valuation. Give me, in one "
    "message:\n"
    "1. The brand being valued (e.g. RAM Trucks)\n"
    "2. The partner - talent, show, event, or franchise (e.g. Glen "
    "Powell)\n"
    "3. The campaign window (e.g. Apr 2024 - Dec 2024)\n"
    "Optional: a pre window (default: the year before), a post window "
    "(default: campaign end through today), and the audience to "
    "measure against (e.g. show viewers, ticket purchasers).\n\n"
    "Example: \"Glen Powell x RAM Trucks, campaign Apr 2024 - Dec "
    "2024, post through Jun 2025\"")
