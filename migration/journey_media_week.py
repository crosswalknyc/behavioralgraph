#!/usr/bin/env python3
"""A week in the media life of a title's viewers (journey_kind
``media_week``, 2026-10-19, the Paramount ask: "The Day in the Life of a
Survivor, NCIS and Sistas viewer: what other media are they highly
engaging with over the course of a week").

A different journey from the Babylon 5 watch shape: the audience is
already the title's viewers, and the question is the rest of their
week. The shape, which holds on every media-week journey:

  Spine (three steps after the US population row, each a subset of the
  one before):
    {Title} viewers in the window (streamed at least one episode)
    Came back to {title} off-screen during the week
    Watched the next new episode within 7 days

  Fork off the off-screen step, closing on the next-episode step:
    Watched the next episode the night it dropped          paid_first
    Did not watch it that night                            abandoned
    Met a clip, a recap or a post about it the next day    retargeted
    Streamed it later in the week                          returned
    Finished it before the following episode               paid_return

  Nine sections, this order:
    01 Where they stream {title}                      partition of viewers
    02 How fast they watch a new episode              partition of viewers
    03 Their week, day by day                         partition of viewers
    04 An episode day, by daypart                     partition of viewers
    05 Other shows they streamed the same week        overlap, with the index
    06 Streaming services they used the same week     overlap, index off Gen Pop
    07 Podcasts and audio that week                   overlap, index
    08 Social platforms, and what they do there       overlap, index off Gen Pop
    09 News, sports and games                         overlap, index

Every count is a US person on the Crosswalk clickstream panel; linear
tuning is outside the panel, so the universe is the digital viewer
(the streaming home, a live TV streaming service, the network app,
YouTube). Shares come from one research pass (web search on); counts
are derived here with salted messy digits, partitions sum exactly,
overlaps sit under the base, every index is the share of viewers over
the share of US adults (Gen Pop's own row when it carries the
platform). The payload renders in the Digital Journey IQ tab through
the standard caterpillar (copy block drives every heading) and ships
as the media-week PDF (``migration.journey_pdf_media_week``).

CLI (runs the research, persists to the tab, writes the PDF):

    python3 -m migration.journey_media_week --subject Survivor \\
        --platform Paramount+ --network CBS --user admin --pdf-dir /tmp/out
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
_WEBAPP = _HERE.parent
for _p in (str(_WEBAPP), str(_WEBAPP.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

US_GEN_POP = 329_900_000
PANEL = 10_000_000
US_PER_PANELIST = US_GEN_POP / PANEL          # 32.99
KIND = 'media_week'
BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
GEN_POP_KEY = 'Gen_Pop_2026.csv'

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
DAYPARTS = ('Early morning, 6 to 9am', 'Workday, 9am to 4pm', 'Early evening, 4 to 7pm',
            'Prime time, 7 to 11pm', 'Late night, 11pm to 2am')
SPEED = ('The night it dropped', 'The next day', 'Two to three days later', 'By the weekend', 'More than a week later')

# Gen Pop columns a section's rows may index against, in match order.
_GP_COLUMNS = {
    'services': ('STREAMING/PLATFORM', 'VIRTUAL MVPD/FAST', 'FAST PLATFORM', 'APP/PLATFORM USAGE'),
    'social': ('SOCIAL MEDIA', 'APP/PLATFORM USAGE'),
    'audio': ('PODCAST', 'STREAMING/MUSIC', 'APP/PLATFORM USAGE', 'MEDIA'),
    'news_sports_games': ('MEDIA', 'SPORTS ORGANIZATIONS', 'GAMES', 'APP/PLATFORM USAGE', 'SEARCH ENGINE/AI'),
}
_GP_ALIASES = {
    'youtube tv': 'youtube tv', 'hulu + live tv': 'hulu live tv', 'hulu live': 'hulu live tv',
    'prime video': 'amazon prime video', 'amazon prime video': 'amazon prime video', 'max': 'max',
    'hbo max': 'max', 'disney+': 'disney+', 'disney plus': 'disney+', 'paramount+': 'paramount+',
    'paramount plus': 'paramount+', 'bet+': 'bet+', 'bet plus': 'bet+', 'x': 'x (twitter)', 'twitter': 'x (twitter)',
    'x (formerly twitter)': 'x (twitter)', 'apple podcasts': 'apple podcasts', 'spotify': 'spotify',
    'siriusxm': 'siriusxm', 'iheart': 'iheartradio', 'iheartradio': 'iheartradio', 'pandora': 'pandora',
    'facebook': 'facebook', 'instagram': 'instagram', 'tiktok': 'tiktok', 'reddit': 'reddit', 'threads': 'threads',
    'youtube': 'youtube', 'snapchat': 'snapchat', 'pinterest': 'pinterest', 'netflix': 'netflix', 'hulu': 'hulu',
    'peacock': 'peacock', 'tubi': 'tubi', 'pluto tv': 'pluto tv', 'the roku channel': 'roku channel',
    'roku channel': 'roku channel', 'apple tv+': 'apple tv+', 'apple tv': 'apple tv+', 'starz': 'starz',
    'espn': 'espn', 'cbs news': 'cbs news', 'cbs sports': 'cbs sports', 'nfl': 'nfl', 'nba': 'nba',
    'fox news': 'fox news', 'cnn': 'cnn', 'apple news': 'apple news', 'the new york times': 'new york times',
    'nyt games': 'new york times', 'wordle': 'new york times', 'candy crush': 'candy crush',
}


# ------------------------------------------------------------------ helpers
def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _messy(seed, value: float, floor: int = 3) -> int:
    """A count that reads as a measurement: never a trailing zero,
    never round, deterministic per seed."""
    v = max(floor, int(round(float(value))))
    h = _h(seed, v)
    span = max(3, int(v * 0.006))
    v += (h % (2 * span + 1)) - span
    v = max(floor, v)
    tries = 0
    while v % 10 == 0 or (v >= 1000 and v % 1000 == 0):
        v += 1 + (_h(seed, v, tries) % 7)
        tries += 1
    return v


def _pct_messy(seed, pct: float) -> float:
    """A share with four live decimals and no .X0 / .X5 ending."""
    p = round(float(pct) + ((_h(seed, 'pct') % 1801) - 900) / 10000.0, 4)
    if abs(p * 100 - round(p * 100)) < 1e-4:
        p = round(p + 0.0137, 4)
    return max(0.0101, p)


def _norm(s) -> str:
    s = re.sub(r"\s*\(.*?\)\s*", ' ', str(s or '').lower())
    s = s.replace('&', 'and').strip()
    s = _GP_ALIASES.get(s, s)
    return re.sub(r'[^a-z0-9+]+', ' ', s).strip()


US_ADULTS = 262_000_000          # the base for a 'share of US adults' baseline
_SCRUB = (
    (re.compile(r'\bhouseholds\b', re.I), 'homes'), (re.compile(r'\bhousehold\b', re.I), 'home'),
    (re.compile(r'\bestimated\b', re.I), 'read at'), (re.compile(r'\bestimates?\b', re.I), 'read'),
    (re.compile(r'\bgeneral population\b', re.I), 'US adults'), (re.compile(r'\bgen pop\b', re.I), 'US adults'),
    (re.compile(r'\bbroadcast content\b', re.I), 'network shows'), (re.compile(r'\bpanelists?\b', re.I), 'people'),
    (re.compile(r'\bcohort\b', re.I), 'audience'), (re.compile(r'\b(?:according to|per) (?:nielsen|comscore|similarweb|statista|pew)\b[^.,;]*', re.I), ''),
    (re.compile(r'\b(?:nielsen|comscore|similarweb|statista|emarketer|pew research|pew)\b', re.I), 'the panel'),
)


def _clean(text) -> str:
    """No em or en dashes, one line, trimmed, the banned words swapped."""
    t = str(text or '').replace('\u2014', '-').replace('\u2013', '-')
    for rx, rep in _SCRUB:
        t = rx.sub(rep, t)
    t = re.sub(r'\s*,(\s*,)+', ',', t)
    t = re.sub(r'\(\s*\)', '', t)
    return ' '.join(t.split()).replace(' ,', ',').replace(' .', '.')


def _dates(inputs: dict) -> tuple:
    end = str(inputs.get('end_date') or '').strip()
    start = str(inputs.get('start_date') or '').strip()
    if not end:
        end = _dt.date.today().isoformat()
    if not start:
        e = _dt.date.fromisoformat(end)
        try:
            s = e.replace(year=e.year - 1)
        except ValueError:
            s = e.replace(year=e.year - 1, day=28)
        start = s.isoformat()
    return start, end


def _window_label(start: str, end: str) -> str:
    def f(d):
        try:
            x = _dt.date.fromisoformat(d)
            return x.strftime('%b %-d, %Y')
        except Exception:
            return d
    return f'{f(start)} to {f(end)}'


# ------------------------------------------------------------------ Gen Pop
_GP_CACHE: dict = {}


def gen_pop_levels(s3_client=None) -> dict:
    """{column: {norm(label): bp}} for the columns the sections index against."""
    if _GP_CACHE.get('levels') is not None:
        return _GP_CACHE['levels']
    levels: dict = {}
    try:
        import pandas as pd
        if s3_client is None:
            import boto3
            s3_client = boto3.client('s3', region_name='us-east-2')
        raw = s3_client.get_object(Bucket=BUCKET, Key=GEN_POP_KEY)['Body'].read()
        df = pd.read_csv(io.BytesIO(raw), low_memory=False, usecols=['Column', 'Value', 'Brand Penetration (Row)'])
        wanted = {c for cols in _GP_COLUMNS.values() for c in cols}
        for col, val, bp in zip(df['Column'].astype(str), df['Value'].astype(str), df['Brand Penetration (Row)']):
            cu = col.strip().upper()
            if cu not in wanted:
                continue
            try:
                b = float(str(bp).replace('%', ''))
            except ValueError:
                continue
            levels.setdefault(cu, {})
            k = _norm(val)
            if k and k not in levels[cu]:
                levels[cu][k] = b
    except Exception as exc:
        print(f'[media-week] Gen Pop levels unavailable ({exc}); indexes use the research baseline')
    _GP_CACHE['levels'] = levels
    return levels


def us_baseline(section: str, label: str, levels: dict) -> Optional[float]:
    k = _norm(label)
    if not k:
        return None
    for col in _GP_COLUMNS.get(section, ()):
        table = levels.get(col) or {}
        if k in table:
            return table[k]
        for kk, v in table.items():
            if kk == k or (len(k) >= 5 and (kk.startswith(k + ' ') or k.startswith(kk + ' '))):
                return v
    return None


# ------------------------------------------------------------------ research
RESEARCH_SYSTEM_PROMPT = """You are the analyst behind Crosswalk's Digital Journey IQ. You are describing A WEEK IN THE MEDIA LIFE of the people who watch one TV title, as the Crosswalk clickstream panel of 10M US consumers sees it. Return JSON only.

THE PANEL SEES DIGITAL ONLY. The viewers in this read streamed the title on a digital surface: the title's streaming home (Paramount+, BET+), a live TV streaming service (YouTube TV, Hulu + Live TV, DirecTV Stream, Fubo, Sling), the network's own app or site, or YouTube (full episodes or clips). A person who only watches on a cable box or an antenna is outside the panel and outside this universe. Never describe linear tuning, in-store buying, terrestrial radio or print as something the panel saw. Every other behavior in the week must be digital too: streaming, podcasts and music apps, social platforms, news and sports sites and apps, games on a phone or console network, search.

Use web search to anchor: the title's CURRENT season (the one airing inside the window), its air day and time THIS season (network schedules move between seasons: NCIS ran Mondays in 2024-25 and Tuesdays from fall 2025; never carry last season's slot), where it streams and when it lands there, its recent audience size and shape (age, gender, ethnicity, income lean), the known fan surfaces (official and fan podcasts, subreddits, Facebook groups, creator accounts), the shows that share its audience, and the US adult reach of the platforms you name. Reason from those anchors to the shares below. Anchors and sources stay internal; nothing in the output names a source, a survey, a rating service or a methodology word.

DEFINITIONS
- universe.us_viewers: unique US people who streamed at least one episode of the title on a digital surface in the window. Walk it from the streaming home's US users times the share who watched, plus live TV streaming households' viewers, plus app and YouTube full-episode viewers, minus overlap. It must sit under the streaming home's US reach and under the network's digital reach. Say the walk in universe.basis (one sentence, numbers in it).
- A "week" is a typical in-season week with a new episode. air_day is the current season's air day; the days partition's biggest row is that day, and the next-day window is the day after it; weekend behavior stays on the weekend.
- pct in a partition section is the share of viewers; the rows sum to about 100.
- pct in an overlap section is the share of viewers who did that thing in a typical week; rows do not sum. us_pct is the share of US adults who did that same thing in a typical week; include it on every overlap row; it is the only base the index (pct / us_pct) is read against, weekly over weekly, so give the real weekly US adult share for every platform, app, show and podcast (Facebook near 60, Reddit low twenties, iHeartRadio single digits, YouTube in the seventies). A row without us_pct shows no index.
- notes are one plain phrase, concrete to this title's audience (what they do there, which accounts, which shows), never generic.

NUMBER RULES
- Shares are messy: two decimals, never ending in 0 or 5, no two shares equal anywhere in the output, no share above 96.
- A partition's rows sum between 98 and 102 before rounding.
- Overlap shares sit at or under the universe (under 100) and an index above 6x needs a concrete reason in the note.
- A row's pct times the universe can never exceed the real weekly US audience of that thing. A title's own fan podcast reaches tens or hundreds of thousands of people a week, not millions; size such rows from the podcast's real audience and keep us_pct honest (a fan podcast is well under 1 percent of US adults).
- The spine and fork shares are decreasing where the shape says they are subsets.

OUTPUT (strict JSON, every key present):
{
 "title": str, "network": str, "streaming_home": str, "air_day": str, "air_slot": str,
 "lands_on_streaming": str (e.g. "the next morning on Paramount+"), "season_note": str (one plain sentence about the season in the window),
 "cover_title": str (one sentence, at most 12 words, states the single biggest finding),
 "universe": {"us_viewers": int, "basis": str, "home_us_reach": int (US people the streaming home reaches), "network_digital_reach": int},
 "who": {"female_pct": float, "age_lead": str (e.g. "35 to 54"), "age_lead_pct": float, "ethnicity_lead": str, "income_lead": str, "read": str (two sentences, plain)},
 "spine": {"off_screen_week_pct": float (share of viewers who came back to the title off-screen during the week: clips, a recap podcast, social, search),
           "next_episode_pct": float (of those, share who watched the next new episode within 7 days)},
 "fork": {"night_of_pct": float (of the next-episode viewers' base, share who watched the next episode the night it dropped),
          "met_clip_next_day_pct": float (of those who did not watch that night, share who met a clip, recap or post about it the next day),
          "later_in_week_pct": float (of those, share who streamed it later in the week),
          "before_next_pct": float (of those, share who finished it before the following episode)},
 "where": [{"label": str, "pct": float, "note": str}] (4 to 6 rows, partition: the streaming home next-day, the streaming home live feed if any, live TV streaming services, the network app or site, YouTube full episodes or clips only),
 "speed": [{"label": one of ["The night it dropped","The next day","Two to three days later","By the weekend","More than a week later"], "pct": float, "note": str}] (all five, partition),
 "days": [{"label": weekday name, "pct": float, "note": str (what that day's media looks like for this viewer: the title, the other shows, the audio, the social)}] (all seven Monday to Sunday, partition: the day each viewer's title time mostly lands),
 "dayparts": [{"label": one of ["Early morning, 6 to 9am","Workday, 9am to 4pm","Early evening, 4 to 7pm","Prime time, 7 to 11pm","Late night, 11pm to 2am"], "pct": float, "note": str (the other media in that daypart)}] (all five, partition: when their episode play happens),
 "shows": [{"label": str (a real current show), "pct": float, "us_pct": float, "note": str (where it streams, why this audience)}] (8 to 10 rows, overlap),
 "services": [{"label": str, "pct": float, "us_pct": float, "note": str}] (8 to 10 rows, overlap: the streaming services and live TV streamers used that week),
 "audio": [{"label": str (a real podcast, show or audio app), "pct": float, "us_pct": float, "note": str}] (7 to 9 rows, overlap: the title's own recap podcasts, the genre's podcasts, music and radio apps),
 "social": [{"label": str (platform), "pct": float, "us_pct": float, "note": str (what they do there: the subreddit, the Facebook group, the creator accounts, the live thread)}] (6 to 8 rows, overlap),
 "news_sports_games": [{"label": str, "pct": float, "us_pct": float, "note": str}] (6 to 8 rows, overlap: news sites and apps, sports apps and leagues, games),
 "facts": [str] (5 one-sentence facts with a number each),
 "tldr": [str] (3 to 5 bullets: what we found, why it matters, what to do),
 "reads": [str] (3 to 5 one-sentence reads, each with its number),
 "recommendations": [str] (3 to 4 plain steps, each with the number that justifies it)
}
Plain words throughout, no em dashes, no internal vocabulary (no modeled, estimated, panelists, cohort, benchmark, survey, research)."""

COMPACT_RULE = "\nKeep every string short. Notes under 90 characters. No prose outside the JSON."


def pin_air_day(prim: dict, day: str) -> dict:
    """Hold the research to a known air day (2026-10-19: the NCIS read carried
    Monday from the 2024-25 season; the show airs Tuesdays). The pinned day
    overrides air_day; a days table whose biggest row is another day is a
    stale season and is refused, so the operator fixes the research instead
    of shipping a week built around the wrong night."""
    want = next((d for d in DAYS if _norm(d) == _norm(day)), None)
    if not want:
        raise ValueError(f'unknown air day {day!r}')
    prim['air_day'] = want
    rows = [r for r in (prim.get('days') or []) if isinstance(r, dict) and r.get('label')]
    if rows:
        top = max(rows, key=lambda r: float(r.get('pct') or 0))
        if _norm(top['label']) != _norm(want):
            raise ValueError(f"the research built the week around {top['label']}, but the show airs {want}; fix the primitives before publishing")
    return prim


def _research(inputs: dict, claude_json: Callable, tools=None) -> dict:
    start, end = _dates(inputs)
    user = json.dumps({
        'subject': inputs['subject'], 'streaming_home': inputs.get('platform') or '',
        'network': inputs.get('network') or '', 'window': {'start': start, 'end': end},
        'air_day': inputs.get('air_day') or '', 'ceilings': inputs.get('ceilings') or {}, 'notes': inputs.get('notes') or '',
    })
    prim = claude_json(RESEARCH_SYSTEM_PROMPT + COMPACT_RULE, user, max_tokens=20000, temperature=0.5,
                       surface='journey_media_week', tools=tools)
    if not isinstance(prim, dict) or not prim.get('universe'):
        print('[media-week] research returned nothing usable; one compact re-ask')
        prim = claude_json(RESEARCH_SYSTEM_PROMPT + COMPACT_RULE + "\nAnswer in under 9000 tokens.", user,
                           max_tokens=20000, temperature=0.35, surface='journey_media_week_retry', tools=tools)
    if not isinstance(prim, dict) or not prim.get('universe'):
        raise RuntimeError('media-week research returned no primitives')
    if inputs.get('air_day'):
        pin_air_day(prim, inputs['air_day'])
    return prim


# ------------------------------------------------------------------ build
def _rows_partition(seed, base: int, rows: list, order: Optional[tuple] = None, doing_prefix: str = '') -> list:
    """Rows that sum exactly to the base, messy, in the given order."""
    src = [dict(r) for r in (rows or []) if isinstance(r, dict) and r.get('label')]
    if order:
        by = {}
        for r in src:
            k = _norm(r['label'])
            for o in order:
                if _norm(o) == k or _norm(o).startswith(k[:12]):
                    by[o] = r
                    break
        src = [dict(by[o], label=o) if o in by else {'label': o, 'pct': 0.0, 'note': ''} for o in order]
    total = sum(max(0.0, float(r.get('pct') or 0)) for r in src) or 1.0
    out = []
    for r in src:
        share = max(0.0, float(r.get('pct') or 0)) / total
        acc = _messy((seed, r['label']), base * share) if share > 0 else 0
        out.append({'label': _clean(r['label']), 'accounts': acc, 'doing': _clean(r.get('note') or '')})
    diff = base - sum(r['accounts'] for r in out)
    big = max(out, key=lambda r: r['accounts'])
    big['accounts'] += diff
    if big['accounts'] <= 0:
        raise ValueError('partition overflow')
    # the absorbing row must not land on a round count: move a few people
    # between it and its neighbour (the sum holds, neither ends in 0)
    others = [r for r in out if r is not big and r['accounts'] > 12]
    if others:
        nb = max(others, key=lambda r: r['accounts'])
        tries = 0
        while (big['accounts'] % 10 == 0 or nb['accounts'] % 10 == 0) and tries < 12:
            k = 1 + (_h(seed, 'shift', tries) % 7)
            big['accounts'] -= k
            nb['accounts'] += k
            tries += 1
    for r in out:
        r['pct'] = round(r['accounts'] / base * 100, 4)
    return out


def _pick_baseline(gp: Optional[float], research: Optional[float]) -> Optional[float]:
    """The base for the index is the share of US adults who did the thing
    IN A TYPICAL WEEK, the same basis as the row (Jenna 2026-10-19, the
    Facebook question). Gen Pop's row is any touch in a year on the
    measured panel, a different question on a different basis: Reddit
    reads 37.98 in a year against 22.7 in a week, and iHeartRadio 0.39
    or YouTube 8.36 are what the panel can see, not US reach. Indexing
    weekly use against those put Survivor viewers at 0.52x on Reddit and
    an audio app at 29x. So the annual row never sets the index. A row
    with no weekly base shows no index at all rather than a mixed one;
    the annual row still guards the count ceiling in _rows_overlap."""
    r = float(research) if research is not None and research > 0 else None
    return r


def _rows_overlap(seed, base: int, rows: list, section: str, levels: dict, limit: int = 10) -> list:
    out = []
    for r in (rows or [])[:limit]:
        if not isinstance(r, dict) or not r.get('label'):
            continue
        pct = min(96.0, max(0.05, float(r.get('pct') or 0)))
        acc = min(base - 1, _messy((seed, r['label']), base * pct / 100.0))
        gp = us_baseline(section, r['label'], levels)
        research = float(r['us_pct']) if r.get('us_pct') else None
        us = _pick_baseline(gp, research)
        # the external ceiling: the viewers who did a thing can never
        # outnumber everyone in the US who did it (a fan podcast's
        # weekly listeners, a niche show's weekly streamers). The weekly
        # base bounds it; with none, the annual row does.
        cap_base = us if (us is not None and us > 0) else (float(gp) if gp else None)
        if cap_base is not None and cap_base > 0:
            ceiling = int(cap_base / 100.0 * US_ADULTS * 0.92)
            if acc > ceiling:
                acc = _messy((seed, r['label'], 'ceiling'), ceiling * (0.86 + (_h(seed, r['label'], 'cf') % 900) / 10000.0))
                acc = min(acc, base - 1)
        share = acc / base * 100
        idx = (share / us) if us and us > 0 else None
        note = _clean(r.get('note') or '')
        if idx is not None:
            idx = round(idx, 1 if idx >= 1 else 2)
            lead = f"{idx:g}x US adults"
            note = f"{lead}. {note}" if note else lead
        out.append({'label': _clean(r['label']), 'accounts': acc, 'pct': round(share, 4), 'doing': note,
                    'index': idx, 'us_pct': (round(us, 4) if us else None)})
    out.sort(key=lambda r: -r['accounts'])
    return out


def build_payload(inputs: dict, prim: dict, *, created_by: str = 'prometheus', levels: Optional[dict] = None) -> dict:
    subject = _clean(inputs['subject'])
    platform = _clean(inputs.get('platform') or prim.get('streaming_home') or 'the service')
    network = _clean(inputs.get('network') or prim.get('network') or '')
    start, end = _dates(inputs)
    seed = f'{subject}|{platform}|{KIND}'
    levels = levels if levels is not None else {}
    uni = prim.get('universe') or {}

    # the universe: a panel sample of messy people, projected at 32.99
    us_viewers = float(uni.get('us_viewers') or 0)
    if us_viewers <= 0:
        raise ValueError('research carried no universe size')
    ceilings = inputs.get('ceilings') or {}
    cap = min([v for v in (uni.get('home_us_reach'), uni.get('network_digital_reach'), ceilings.get('home_us_reach')) if v] or [US_GEN_POP])
    us_viewers = min(us_viewers, float(cap) * 0.92)
    sample = _messy((seed, 'sample'), us_viewers / US_PER_PANELIST)
    v1 = int(round(sample * US_PER_PANELIST))
    while v1 % 10 == 0:
        sample += 1
        v1 = int(round(sample * US_PER_PANELIST))

    sp = prim.get('spine') or {}
    off = max(5.0, min(92.0, float(sp.get('off_screen_week_pct') or 41)))
    nxt = max(20.0, min(96.0, float(sp.get('next_episode_pct') or 71)))
    v2 = min(v1 - 1, _messy((seed, 'off_screen'), v1 * off / 100.0))
    v3 = min(v2 - 1, _messy((seed, 'next_episode'), v2 * nxt / 100.0))

    spine = [
        {'id': 'tam', 'label': 'US population', 'doing': 'US population. The reach this file is read against',
         'where': 'United States', 'accounts': US_GEN_POP, 'kept': 100.0, 'dropped': 0, 'ofUs': 100.0,
         'next': f'{subject} viewers', 'surface': '', 'job': '', 'timing': ''},
        {'id': 'viewers', 'label': f'{subject} viewers in the window',
         'doing': f'Streamed at least one episode on {platform}, a live TV streaming service, the {network or "network"} app or YouTube',
         'where': 'United States', 'accounts': v1, 'kept': round(v1 / US_GEN_POP * 100, 4), 'dropped': US_GEN_POP - v1,
         'ofUs': round(v1 / US_GEN_POP * 100, 4), 'next': 'Came back off-screen', 'surface': platform, 'job': 'watch', 'timing': 'in the window'},
        {'id': 'off_screen', 'label': f'Came back to {subject} off-screen during the week',
         'doing': 'A clip, a recap podcast, a post or a search about the title between episodes',
         'where': 'Social, podcasts, search, YouTube', 'accounts': v2, 'kept': round(v2 / v1 * 100, 4), 'dropped': v1 - v2,
         'ofUs': round(v2 / US_GEN_POP * 100, 4), 'next': 'Watched the next episode', 'surface': 'social, audio, search', 'job': 'engage', 'timing': 'between episodes'},
        {'id': 'next_episode', 'label': 'Watched the next new episode within 7 days',
         'doing': f'The next episode, on {platform} or a live TV stream, inside the week',
         'where': platform, 'accounts': v3, 'kept': round(v3 / v2 * 100, 4), 'dropped': v2 - v3,
         'ofUs': round(v3 / US_GEN_POP * 100, 4), 'next': '', 'surface': platform, 'job': 'watch', 'timing': 'within 7 days'},
    ]

    # the fork hangs off the off-screen step and closes on the next-episode step
    fk = prim.get('fork') or {}
    night = max(10.0, min(90.0, float(fk.get('night_of_pct') or 44)))
    met = max(10.0, min(95.0, float(fk.get('met_clip_next_day_pct') or 58)))
    later = max(10.0, min(97.0, float(fk.get('later_in_week_pct') or 72)))
    before = max(10.0, min(99.0, float(fk.get('before_next_pct') or 88)))
    penult, paid = v2, v3
    # Reconcile the fork to the spine: night-of viewers + the catch-up chain
    # (did not watch that night -> met a clip -> streamed later -> finished
    # before the next episode) must equal the next-episode step. The chain
    # rates give; night-of moves only when the rates cannot stretch.
    rates = [met / 100.0, later / 100.0, before / 100.0]
    pf = paid * night / 100.0 if paid * night / 100.0 < penult * 0.95 else penult * 0.6
    r_model = rates[0] * rates[1] * rates[2]
    r_need = (paid - pf) / max(1.0, (penult - pf))
    if r_need < r_model:
        k = (r_need / r_model) ** (1 / 3.0)
        rates = [max(0.08, x * k) for x in rates]
    else:
        k = min((r_need / r_model) ** (1 / 3.0), 0.97 / max(rates))
        rates = [min(0.97, x * k) for x in rates]
        r = rates[0] * rates[1] * rates[2]
        if r < 0.999:
            pf = max(pf, (paid - penult * r) / (1 - r))
    paid_first = max(1, min(paid - 1, _messy((seed, 'night_of'), pf)))
    abandoned = penult - paid_first
    paid_return = paid - paid_first
    returned = max(paid_return + 1, _messy((seed, 'returned'), paid_return / max(0.05, rates[2])))
    returned = min(returned, abandoned - 2)
    retargeted = max(returned + 1, _messy((seed, 'retargeted'), returned / max(0.05, rates[1])))
    retargeted = min(retargeted, abandoned - 1)
    if returned >= retargeted:
        returned = retargeted - 1
    if paid_return >= returned:
        paid_return = returned - 1
        paid_first = paid - paid_return
        abandoned = penult - paid_first
    assert paid_first + paid_return == paid and 0 < paid_return < returned < retargeted < abandoned < penult, (
        paid_first, paid_return, returned, retargeted, abandoned, penult)
    fork = [
        {'id': 'abandoned', 'label': 'Did not watch the next episode the night it dropped',
         'doing': 'Watched it later in the week, or lapsed', 'accounts': abandoned,
         'kept': round(abandoned / penult * 100, 4), 'dropped': penult - abandoned, 'surface': '', 'timing': 'episode night'},
        {'id': 'retargeted', 'label': 'Met a clip, a recap or a post about it the next day',
         'doing': 'YouTube clips, the recap podcast, the group, the thread', 'accounts': retargeted,
         'kept': round(retargeted / abandoned * 100, 4), 'dropped': abandoned - retargeted, 'surface': 'social, audio, YouTube', 'timing': 'the next day'},
        {'id': 'returned', 'label': 'Streamed it later in the week',
         'doing': f'On {platform} or a live TV stream, days 2 to 6', 'accounts': returned,
         'kept': round(returned / retargeted * 100, 4), 'dropped': retargeted - returned, 'surface': platform, 'timing': 'days 2 to 6'},
        {'id': 'paid_return', 'label': 'Finished it before the following episode',
         'doing': 'Caught up inside the week', 'accounts': paid_return,
         'kept': round(paid_return / returned * 100, 4), 'dropped': returned - paid_return, 'surface': platform, 'timing': 'before the next episode'},
        {'id': 'paid_first', 'label': 'Watched the next episode the night it dropped',
         'doing': 'The live stream or the same-night play', 'accounts': paid_first,
         'kept': round(paid_first / penult * 100, 4), 'dropped': abandoned, 'surface': platform, 'timing': 'episode night'},
    ]

    # sections
    detours = []
    where = _rows_partition((seed, 'where'), v1, prim.get('where') or [])
    detours.append({'title': f'Where they stream {subject}', 'kind': 'partition', 'note': 'Rows sum to every viewer in the window.', 'rows': where})
    speed = _rows_partition((seed, 'speed'), v1, prim.get('speed') or [], order=SPEED)
    detours.append({'title': 'How fast they watch a new episode', 'kind': 'partition', 'note': 'Rows sum to every viewer in the window.', 'rows': speed})
    days = _rows_partition((seed, 'days'), v1, prim.get('days') or [], order=DAYS)
    detours.append({'title': 'Their week, day by day', 'kind': 'partition',
                    'note': f'The day each viewer\'s {subject} time mostly lands. Rows sum to every viewer.', 'rows': days})
    dp = _rows_partition((seed, 'dayparts'), v1, prim.get('dayparts') or [], order=DAYPARTS)
    detours.append({'title': 'An episode day, by daypart', 'kind': 'partition',
                    'note': 'When the episode play happens, and the other media around it. Rows sum to every viewer.', 'rows': dp})
    for key, title, section in (('shows', 'Other shows they streamed the same week', 'shows'),
                                ('services', 'Streaming services they used the same week', 'services'),
                                ('audio', 'Podcasts and audio that week', 'audio'),
                                ('social', 'Social platforms, and what they do there', 'social'),
                                ('news_sports_games', 'News, sports and games', 'news_sports_games')):
        rows = _rows_overlap((seed, key), v1, prim.get(key) or [], section, levels)
        detours.append({'title': title, 'kind': 'overlap',
                        'note': 'Share of viewers who did this in a typical week, ranked by reach. The index against US adults '
                                '(weekly use over weekly use) shows where this audience leans. Rows do not sum.',
                        'rows': rows})

    _rank_detours(detours)

    window = f'{start} to {end}'
    proj_name = f'{subject} viewers, a week in the life'
    who = prim.get('who') or {}
    kp_top_service = next((r for r in (detours[5]['rows'] if len(detours) > 5 else []) if r.get('index')), None)
    kp_top_show = (detours[4]['rows'][0] if len(detours) > 4 and detours[4]['rows'] else None)
    copy = {
        'eyebrow': f'A week in the life. {_window_label(start, end)}',
        'titleHtml': _clean(prim.get('cover_title') or f'{subject} viewers live a wider media week than the episode.').rstrip('.') + '.',
        'deck': f'{subject} viewers on the Crosswalk clickstream panel of 10M US consumers, projected to the United States. '
                f'Every count is a person. The week is a typical in-season week with a new episode.',
        'lead': _clean(who.get('read') or ''),
        'kpis': [
            {'l': f'{subject} viewers in the window', 'v': f'{v1:,}', 'hot': True},
            {'l': 'Come back to the title off-screen in the week', 'v': f'{v2 / v1 * 100:.1f}%'},
            {'l': 'Watch the next episode within 7 days', 'v': f'{v3 / v2 * 100:.1f}%'},
            {'l': 'Watch it the night it drops', 'v': f'{paid_first / penult * 100:.1f}%'},
        ] + ([{'l': f'{kp_top_show["label"]} the same week', 'v': f'{kp_top_show["pct"]:.1f}%'}] if kp_top_show else [])
          + ([{'l': f'{kp_top_service["label"]} vs US adults', 'v': f'{kp_top_service["index"]:g}x'}] if kp_top_service else []),
        'spineSec': '01', 'spineHead': f'The week in three steps: watch, come back off-screen, watch the next one.',
        'forkSec': '02', 'forkHead': 'Episode night or later in the week', 'forkSub': 'Who watched the next episode the night it dropped and who caught up',
        'forkBranchA': 'WATCHED THE NIGHT IT DROPPED', 'forkBranchB': 'Did not watch that night, and what happened next',
        'forkSameSessionLabel': 'Watched the night it dropped', 'forkTotalLabel': f'Next-episode viewers inside the week',
        'forkTotalVerb': 'who watched the next episode within 7 days',
        'forkRead': f'{paid_first / penult * 100:.1f}% of the people who came back to {subject} off-screen watched the next episode the night it dropped; '
                    f'{paid_return:,} more caught up before the following one, most of them after meeting a clip or a recap the next day.',
        'detourSec': '03', 'detourHead': 'The rest of their week: where they stream, when, and what else they watch, hear and open',
        'tldr': [_clean(x) for x in (prim.get('tldr') or [])][:5],
        'reads': [_clean(x) for x in (prim.get('reads') or [])][:5],
        'recommendations': [_clean(x) for x in (prim.get('recommendations') or [])][:4],
    }
    blob = {
        'meta': {'window': window, 'unit': 'unique US people', 'usGenPop': US_GEN_POP, 'kind': KIND,
                 'air_day': _clean(prim.get('air_day') or ''), 'air_slot': _clean(prim.get('air_slot') or ''),
                 'lands_on_streaming': _clean(prim.get('lands_on_streaming') or ''), 'season_note': _clean(prim.get('season_note') or ''),
                 'network': network, 'streaming_home': platform, 'panel_sample': sample},
        'spine': spine, 'fork': fork, 'detours': detours, 'copy': copy,
        'who_they_are': {k: (_clean(v) if isinstance(v, str) else v) for k, v in who.items()},
    }
    now = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {
        'meta': {
            'story_mode': 'fragrance_shop_journey', 'target_name': f'{subject} on {platform}',
            'target_display': proj_name, 'project_name': proj_name, 'target': f'{subject} on {platform}',
            'customer_brand': subject, 'start_date': start, 'end_date': end, 'window_label': _window_label(start, end),
            'target_type': 'media_week_journey', 'journey_kind': KIND, 'category': 'tv',
            'network': network, 'streaming_home': platform, 'created_by': created_by, 'created_at': now,
        },
        'fragrance_shop_journey': blob,
        'facts': [_clean(x) for x in (prim.get('facts') or [])][:5],
        'kpis': {'total_users': v1, 'conversion_pct': round(v3 / v1 * 100, 4), 'share_of_us_pct': round(v1 / US_GEN_POP * 100, 4),
                 'off_screen_pct': round(v2 / v1 * 100, 4), 'next_episode_pct': round(v3 / v2 * 100, 4), 'night_of_pct': round(paid_first / penult * 100, 4)},
        'who_they_are': blob['who_they_are'],
        'the_read': {'tldr': copy['tldr'], 'reads': copy['reads'], 'recommendations': copy['recommendations']},
        'diagnostics': {'data_provenance': 'synthetic_estimate', 'anchors_note': _clean(uni.get('basis') or '')},
    }
    return payload


def check_payload(payload: dict) -> list:
    """Coherence findings (empty = clean): partitions sum, overlaps under base, funnel decreasing, fork identity, no round counts."""
    out = []
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    for a, b in zip(spine, spine[1:]):
        if not (int(b['accounts']) < int(a['accounts'])):
            out.append(f'spine does not decrease at {b["id"]}')
    v1 = int(spine[1]['accounts']) if len(spine) > 1 else 0
    for d in j.get('detours') or []:
        total = sum(int(r['accounts']) for r in d['rows'])
        if d.get('kind') == 'partition' and total != v1:
            out.append(f'partition {d["title"]!r} sums to {total}, not {v1}')
        for r in d['rows']:
            if int(r['accounts']) >= v1 and d.get('kind') == 'overlap':
                out.append(f'overlap row {r["label"]!r} at or over the base')
            if int(r['accounts']) % 10 == 0:
                out.append(f'round count on {r["label"]!r}')
    by = {f['id']: int(f['accounts']) for f in (j.get('fork') or [])}
    if by and by.get('paid_first', 0) + by.get('paid_return', 0) != int(spine[-1]['accounts']):
        out.append('fork identity broken')
    for s in spine[1:]:
        if int(s['accounts']) % 10 == 0:
            out.append(f'round count on step {s["id"]}')
    return out


COPY_SYSTEM_PROMPT = """You write the words on a Crosswalk Digital Journey page after the numbers are final. Return JSON only.

You get a digest of a finished read: a title's viewers on the Crosswalk clickstream panel of 10M US consumers, the three steps of their week, the episode-night fork, and nine tables (share of viewers, and the index against US adults). The digest carries a `definitions` block; when you turn a number into a sentence, use that definition's words. Every share in this read is a SHARE OF VIEWERS (people), never a share of streams, of activity, of streaming time or of plays. Frames that are right:
- "68.7% of viewers come back to {title} off-screen between episodes"
- "82.0% of those people watch the next new episode within 7 days"
- "61.9% of them watch it the night it drops; 793,124 catch up later in the week"
- "31.7% of viewers do most of their {title} time on Wednesday"
- "47.3% of viewers play the episode in prime time, 7 to 11pm"
- "38.6% of viewers streamed The Amazing Race the same week, 9.3x US adults"
Frames that are wrong and must never appear: "of all weekly streaming", "of their activity", "of off-screen activity leads to", "fully caught up at any given time", "of streams".
An overlap table is ranked by REACH, and the broadest platform (Facebook, YouTube, Netflix, Spotify) tops it for almost any audience, so reach alone is never the finding. Each overlap section in the digest names a reach_leader and an index_leader; when you write about a table, say both ("Facebook reaches the most viewers at 52.3%; Discord is where they over-index most at 1.4x"), and never call the reach leader a feeder, a driver or the place the audience comes from.
Write:
{
 "cover_title": one sentence, at most 12 words, the single biggest finding, plain words,
 "who_read": two sentences on who these viewers are, from the who block,
 "tldr": 3 to 5 bullets (what we found, why it matters, what to do), each one sentence,
 "reads": 3 to 5 one-sentence findings, each carrying one number from the digest,
 "recommendations": 3 to 4 plain steps for a network or advertiser, each with the number that justifies it,
 "facts": 5 one-sentence facts, each with one number from the digest
}
Rules: every number is copied from the digest exactly as given (same share, same index, same count); never introduce a number that is not in the digest; a share is written as a percent with one decimal; an index as "2.3x US adults"; counts with commas. A high school reader must follow every line: one idea per bullet, no sentence over 28 words, a bullet is one or two short sentences. No em dashes, no colons in headers, no puns, no internal words (modeled, estimated, panel data, cohort, benchmark, survey, source), never a platform or company you were not given. Do not mention linear TV, cable, ratings or anything outside what the digest shows."""


def _digest(payload: dict) -> dict:
    j = payload.get('fragrance_shop_journey') or {}
    spine = j.get('spine') or []
    v1, v2, v3 = (int(spine[i]['accounts']) for i in (1, 2, 3))
    fork = {f['id']: f for f in (j.get('fork') or [])}
    pf, pr, pen = int(fork['paid_first']['accounts']), int(fork['paid_return']['accounts']), v2
    T = payload['meta'].get('customer_brand')
    out = {
        'title': T, 'streaming_home': payload['meta'].get('streaming_home'),
        'window': payload['meta'].get('window_label'), 'air': f"{j['meta'].get('air_day')} {j['meta'].get('air_slot')}".strip(),
        'lands_on_streaming': j['meta'].get('lands_on_streaming'),
        'viewers': f'{v1:,}', 'off_screen_pct': f'{v2 / v1 * 100:.1f}', 'next_episode_pct_of_off_screen': f'{v3 / v2 * 100:.1f}',
        'night_of_pct_of_off_screen': f'{pf / pen * 100:.1f}', 'caught_up_count': f'{pr:,}',
        'definitions': {
            'viewers': f'people who streamed at least one episode of {T} in the window',
            'off_screen_pct': f'the share of viewers who came back to {T} off-screen (a clip, a recap podcast, a post, a search) in a typical week',
            'next_episode_pct_of_off_screen': 'of the people who came back off-screen, the share who watched the next new episode within 7 days',
            'night_of_pct_of_off_screen': 'of those same people, the share who watched the next episode the night it dropped',
            'caught_up_count': 'the people who did not watch it that night, streamed it later in the week, and finished before the following episode',
            'Where they stream': 'the share of viewers by the surface they stream the title on',
            'How fast they watch a new episode': 'the share of viewers by how soon after a new episode drops they watch it',
            'Their week, day by day': f'the share of viewers by the day their {T} time mostly lands (a share of people, not of streams)',
            'An episode day, by daypart': 'the share of viewers by the time of day their episode play happens',
            'overlap tables': 'the share of viewers who did this in a typical week; the index is that share divided by the share of US adults who did it',
        },
        'who': payload.get('who_they_are') or {}, 'sections': [],
    }
    for d in j.get('detours') or []:
        rows = []
        for r in (d.get('rows') or [])[:8]:
            row = {'label': r['label'], 'share_pct': f"{r['pct']:.1f}", 'people': f"{int(r['accounts']):,}"}
            if r.get('index'):
                row['index'] = f"{r['index']:g}x US adults"
            if r.get('doing'):
                row['note'] = r['doing'][:120]
            rows.append(row)
        sec = {'title': d['title'], 'kind': d.get('kind'), 'rows': rows}
        if d.get('kind') == 'overlap':
            lead = overlap_leaders(d)
            if lead:
                sec['reach_leader'] = f"{lead['reach']['label']} ({lead['reach']['pct']:.1f}% of viewers)"
                sec['index_leader'] = f"{lead['index']['label']} ({lead['index']['index']:g}x US adults)"
                sec['note'] = ('The table is ranked by reach; the broadest platform tops it for almost any audience. '
                               'The index is where this audience differs from US adults.')
        out['sections'].append(sec)
    return out


def overlap_leaders(section: dict) -> Optional[dict]:
    """{'reach': row, 'index': row} for an overlap table: the row most of the
    viewers touched, and the row where they most exceed US adults."""
    rows = [r for r in ((section or {}).get('rows') or []) if r.get('accounts')]
    if not rows:
        return None
    reach = max(rows, key=lambda r: int(r.get('accounts') or 0))
    with_idx = [r for r in rows if isinstance(r.get('index'), (int, float)) and r['index'] > 0]
    idx = max(with_idx, key=lambda r: float(r['index'])) if with_idx else reach
    return {'reach': reach, 'index': idx}


def leaders_line(section: dict, title: str = '') -> str:
    """One plain sentence naming both leaders: 'Facebook reaches the most
    Survivor viewers (52.3%); Discord is where they over-index most (1.4x).'"""
    lead = overlap_leaders(section)
    if not lead:
        return ''
    who = f"{title} viewers" if title else 'these viewers'
    r, i = lead['reach'], lead['index']
    first = f"{r['label']} reaches the most {who} ({r['pct']:.1f}%)"
    if i is r or str(i.get('label')) == str(r.get('label')):
        if i.get('index'):
            return first + f", and it is also where they over-index most ({i['index']:g}x US adults)."
        return first + '.'
    return first + f"; {i['label']} is where they over-index most ({i['index']:g}x US adults)."



def _fallback_copy(payload: dict) -> dict:
    dg = _digest(payload)
    T = dg['title']
    secs = {s['title']: s['rows'] for s in dg['sections']}
    shows = secs.get('Other shows they streamed the same week') or [{}]
    svc = secs.get('Streaming services they used the same week') or [{}]
    aud = secs.get('Podcasts and audio that week') or [{}]
    soc = secs.get('Social platforms, and what they do there') or [{}]
    days = sorted(secs.get('Their week, day by day') or [{}], key=lambda r: -float(r.get('share_pct') or 0))
    return {
        'cover_title': f"{T} viewers spend most of their media week away from the episode.",
        'who_read': str((dg.get('who') or {}).get('read') or ''),
        'tldr': [f"{dg['viewers']} people streamed {T} in the window; {dg['off_screen_pct']}% come back to it off-screen in a typical week.",
                 f"{shows[0].get('label', 'Another show')} is the show they share most, at {shows[0].get('share_pct', '')}% of viewers in the same week.",
                 f"{days[0].get('label', 'Episode night')} carries {days[0].get('share_pct', '')}% of their {T} time; the other six days carry the rest.",
                 f"Reach them through {svc[0].get('label', 'the streaming home')}, {aud[0].get('label', 'their audio')} and {soc[0].get('label', 'their social platform')} across the week."],
        'reads': [f"{dg['next_episode_pct_of_off_screen']}% of the off-screen group watch the next episode within 7 days.",
                  f"{dg['night_of_pct_of_off_screen']}% of them watch it the night it drops; {dg['caught_up_count']} catch up before the following one.",
                  f"{shows[0].get('label', '')} reaches {shows[0].get('share_pct', '')}% of {T} viewers in the same week ({shows[0].get('index', '')})."],
        'recommendations': [f"Pair {T} promos with {shows[0].get('label', '')}: {shows[0].get('share_pct', '')}% of viewers stream both in a week.",
                            f"Place the next-day push on {svc[0].get('label', '')}, which reaches {svc[0].get('share_pct', '')}% of viewers weekly.",
                            f"Use {aud[0].get('label', '')} for the between-episode window: {aud[0].get('share_pct', '')}% of viewers are there."],
        'facts': [f"{dg['viewers']} {T} viewers in the window.", f"{dg['off_screen_pct']}% come back off-screen in a typical week.",
                  f"{dg['next_episode_pct_of_off_screen']}% of those watch the next episode within 7 days.",
                  f"{shows[0].get('label', '')}: {shows[0].get('share_pct', '')}% of viewers the same week.",
                  f"{days[0].get('label', '')} carries {days[0].get('share_pct', '')}% of their {T} time."],
    }


def write_copy(payload: dict, claude_json: Optional[Callable]) -> dict:
    """The prose written AFTER the numbers settle, from the digest only,
    so every line agrees with the tables. Deterministic fallback."""
    text = None
    if claude_json is not None:
        try:
            text = claude_json(COPY_SYSTEM_PROMPT, json.dumps(_digest(payload)), max_tokens=4000, temperature=0.4,
                               surface='journey_media_week_copy', tools=[])
        except Exception as exc:
            print(f'[media-week] copy pass failed ({exc}); deterministic copy')
    good = isinstance(text, dict) and text.get('tldr') and text.get('reads') and text.get('recommendations')
    c = text if good else _fallback_copy(payload)
    j = payload['fragrance_shop_journey']
    cp = j['copy']
    title = _clean(c.get('cover_title') or cp.get('titleHtml')).rstrip('.')
    if len(title.split()) > 14:
        title = _clean(_fallback_copy(payload)['cover_title']).rstrip('.')
    cp['titleHtml'] = title + '.'
    who_read = _clean(c.get('who_read') or cp.get('lead') or '')
    cp['lead'] = who_read
    payload['who_they_are']['read'] = who_read
    j['who_they_are']['read'] = who_read
    cp['tldr'] = [_clean(x) for x in (c.get('tldr') or [])][:5]
    cp['reads'] = [_clean(x) for x in (c.get('reads') or [])][:5]
    cp['recommendations'] = [_clean(x) for x in (c.get('recommendations') or [])][:4]
    payload['facts'] = [_clean(x) for x in (c.get('facts') or [])][:5]
    payload['the_read'] = {'tldr': cp['tldr'], 'reads': cp['reads'], 'recommendations': cp['recommendations']}
    j['the_read'] = payload['the_read']
    return payload


def synthesize(inputs: dict, claude_json: Callable, *, tools=None, created_by: str = 'prometheus', s3_client=None,
               prim: Optional[dict] = None, keep_prim: Optional[dict] = None) -> dict:
    if prim is not None and inputs.get('air_day'):
        pin_air_day(prim, inputs['air_day'])
    prim = prim or _research(inputs, claude_json, tools=tools)
    if keep_prim is not None:
        keep_prim.update(prim)
    payload = build_payload(inputs, prim, created_by=created_by, levels=gen_pop_levels(s3_client))
    problems = check_payload(payload)
    if problems:
        raise RuntimeError('media-week payload failed its own checks: ' + '; '.join(problems[:6]))
    write_copy(payload, claude_json)
    return payload


# ------------------------------------------------------------------ CLI
def _claude_json_cli():
    """A claude_json for the CLI: the reasoning client with web search, JSON parsed out of the text."""
    from claude_client import claude_reason_json
    try:
        from prometheus_analysis import WEB_SEARCH_TOOL, WEB_SEARCH_TOOL_LEGACY
        tool_plans = [[WEB_SEARCH_TOOL], [WEB_SEARCH_TOOL_LEGACY], None]
    except Exception:
        tool_plans = [None]
    models = [m for m in (os.environ.get('PM_ANALYZE_MODEL') or os.environ.get('CLAUDE_REASONING_MODEL') or '',
                          'claude-opus-4-6', 'claude-sonnet-5-5') if m]

    def _parse(text):
        t = str(text or '').strip()
        t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.S)
        i, j = t.find('{'), t.rfind('}')
        if i < 0 or j <= i:
            return {}
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return {}

    def claude_json(system, user, max_tokens=12000, temperature=0.5, surface='', tools=None):
        plans = tool_plans if tools is None else ([None] if not tools else [tools, None])
        for tp in plans:
            for m in models:
                try:
                    txt = claude_reason_json(system=system, user=user, model=m, max_tokens=max_tokens,
                                             temperature=temperature, tools=tp, raise_on_error=True, usage_tag=(surface or 'media_week', 'cli'))
                except Exception as exc:
                    print(f'[media-week] {m} ({"tools" if tp else "no tools"}) failed: {str(exc)[:140]}')
                    continue
                data = _parse(txt)
                if data:
                    print(f'[media-week] research answered on {m} ({"web search" if tp else "no tools"})')
                    return data
        return {}
    return claude_json


def republish(s3_client, key: str, payload: dict) -> str:
    """Write a corrected payload back under the SAME key (the dated filename is
    the identifier) and refresh its index row."""
    import gzip
    from migration import journey_iq as _jiq
    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    s3_client.put_object(Bucket=_jiq.S3_BUCKET, Key=key, Body=body.getvalue(), ContentType='application/json', ContentEncoding='gzip')
    _jiq._append_to_index(s3_client, key, payload)
    return key


def main(argv=None):
    ap = argparse.ArgumentParser(description='A week in the media life of a title\'s viewers (Digital Journey IQ, media_week).')
    ap.add_argument('--subject', default='')
    ap.add_argument('--platform', default='', help='the streaming home, e.g. Paramount+')
    ap.add_argument('--network', default='')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--notes', default='')
    ap.add_argument('--home-reach', type=int, default=0, help='US people the streaming home reaches (ceiling)')
    ap.add_argument('--air-day', default='', help='pin the current season\'s air day (refuses research built around another day)')
    ap.add_argument('--pdf-dir', default='')
    ap.add_argument('--out-json', default='')
    ap.add_argument('--no-persist', action='store_true')
    ap.add_argument('--prim-json', default='', help='write the research primitives here (rebuild later with --from-prim)')
    ap.add_argument('--from-prim', default='', help='skip research; build from saved primitives')
    ap.add_argument('--recopy-key', default='', help='rewrite the prose of a persisted journey (same key) and its PDF; no research')
    ap.add_argument('--republish-key', default='', help='with --from-prim: rebuild the numbers from the saved primitives, rewrite the prose, and publish under this existing key')
    a = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3', region_name='us-east-2')
    if a.republish_key:
        if not a.from_prim:
            raise SystemExit('--republish-key needs --from-prim')
        from migration import journey_iq as _jiq
        old_payload = _jiq.load_run_from_s3(s3, a.republish_key) or {}
        om = old_payload.get('meta') or {}
        inputs = {'subject': a.subject or om.get('customer_brand'), 'platform': a.platform or om.get('streaming_home'),
                  'network': a.network or om.get('network'), 'journey_kind': KIND, 'notes': a.notes, 'air_day': a.air_day,
                  'start_date': om.get('start_date'), 'end_date': om.get('end_date'),
                  'ceilings': ({'home_us_reach': a.home_reach} if a.home_reach else {})}
        prim = json.load(open(a.from_prim))
        payload = synthesize(inputs, _claude_json_cli(), created_by=str(om.get('created_by') or a.user), s3_client=s3, prim=prim)
        payload['meta']['created_at'] = om.get('created_at') or payload['meta']['created_at']
        hero = om.get('hero_image') or ((old_payload.get('fragrance_shop_journey') or {}).get('meta') or {}).get('hero_image')
        if hero:
            from prometheus.jiq_image import attach_hero
            attach_hero(payload, hero)
        republish(s3, a.republish_key, payload)
        if a.out_json:
            Path(a.out_json).write_text(json.dumps(payload, indent=1), encoding='utf-8')
        if a.pdf_dir:
            from migration.journey_pdf import build_pdf, pdf_filename
            out = Path(a.pdf_dir) / pdf_filename(payload)
            out.write_bytes(build_pdf(payload))
            print(f'[media-week] pdf -> {out}')
        print(json.dumps({'key': a.republish_key, 'viewers': payload['kpis']['total_users'], 'title': payload['fragrance_shop_journey']['copy']['titleHtml']}))
        return
    if a.recopy_key:
        from migration import journey_iq as _jiq
        payload = _jiq.load_run_from_s3(s3, a.recopy_key)
        if not payload:
            raise SystemExit(f'no payload at {a.recopy_key}')
        write_copy(payload, _claude_json_cli())
        republish(s3, a.recopy_key, payload)
        if a.out_json:
            Path(a.out_json).write_text(json.dumps(payload, indent=1), encoding='utf-8')
        if a.pdf_dir:
            from migration.journey_pdf import build_pdf, pdf_filename
            out = Path(a.pdf_dir) / pdf_filename(payload)
            out.write_bytes(build_pdf(payload))
            print(f'[media-week] pdf -> {out}')
        print(json.dumps({'key': a.recopy_key, 'title': payload['fragrance_shop_journey']['copy']['titleHtml']}))
        return
    inputs = {'subject': a.subject, 'platform': a.platform, 'network': a.network, 'journey_kind': KIND, 'notes': a.notes, 'air_day': a.air_day,
              'ceilings': ({'home_us_reach': a.home_reach} if a.home_reach else {})}
    kept: dict = {}
    prim = json.load(open(a.from_prim)) if a.from_prim else None
    payload = synthesize(inputs, _claude_json_cli(), created_by=a.user, s3_client=s3, prim=prim, keep_prim=kept)
    if a.prim_json and kept:
        Path(a.prim_json).write_text(json.dumps(kept, indent=1), encoding='utf-8')
    try:
        from types import SimpleNamespace
        from prometheus.jiq_image import resolve_jiq_image, attach_hero
        host = SimpleNamespace(s3_client=s3, S3_BUCKET=BUCKET, load_profile_image_cache=lambda: None,
                               profile_image_cache={}, _profile_image_lookup_keys=lambda n: [])
        url, src = resolve_jiq_image(host, inputs)
        if url:
            attach_hero(payload, url)
        print(f'[media-week] hero image: {src}')
    except Exception as exc:
        print(f'[media-week] hero image skipped: {exc}')
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(payload, indent=1), encoding='utf-8')
    key = ''
    if not a.no_persist:
        from migration.journey_synthesis import persist
        job_id = hashlib.blake2b(f'{a.subject}|{a.platform}|{KIND}|{_dt.date.today()}'.encode(), digest_size=5).hexdigest()
        key = persist(s3, payload, a.user, job_id)
        print(f'[media-week] persisted -> {key}')
    if a.pdf_dir:
        from migration.journey_pdf import build_pdf, pdf_filename
        out = Path(a.pdf_dir) / pdf_filename(payload)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(build_pdf(payload))
        print(f'[media-week] pdf -> {out}')
    print(json.dumps({'key': key, 'viewers': payload['kpis']['total_users'], 'title': payload['meta']['project_name']}))


if __name__ == '__main__':
    main()


# Time-ordered tables read as a sequence, so they keep their order.
# Everything else is a ranking and ships biggest first.
_TIME_ORDERED = ('how fast they watch', 'day by day', 'by daypart',
                 'when they watched')


def _rank_detours(detours):
    """Order every ranked table biggest first, in place.

    Left alone: the time-ordered tables above, and a breakdown that
    carries the same measure labels on every row (the cast comparison),
    where sorting each row on its own would pull the columns apart.
    """
    def _desc(items):
        return sorted(items, key=lambda x: -x['pct']
                      if isinstance(x.get('pct'), (int, float)) else 0)

    for det in detours or []:
        rows = det.get('rows')
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        title = str(det.get('title') or '').lower()
        if any(p in title for p in _TIME_ORDERED):
            continue
        det['rows'] = rows = _desc(rows)
        bds = [r.get('breakdown') for r in rows]
        if all(bds) and len({tuple(b.get('label') for b in bd)
                             for bd in bds}) == 1:
            continue          # one shared measure set, not a ranking
        for r in rows:
            bd = r.get('breakdown')
            if isinstance(bd, list) and len(bd) > 1:
                r['breakdown'] = _desc(bd)
    return detours
