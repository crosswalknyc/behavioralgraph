"""
IQ Rankers - scraped + researched daily signal layer
====================================================

This is the measurement layer that replaces the clickstream pass in
`iq_rankers.compute_layer1_metrics_for_day`. It produces the same two
inputs the CW IQ Score has always consumed:

    volume  - how much public activity there was around this entity today
    reach   - how many US people that activity actually reached

and it derives both from the scraped boards the nightly Trends suite
already collects, plus the researched real-world audience anchors that
suite already attaches to every board item. Nothing here reads the
clickstream. See `.cursor/rules/trends-rankers-never-clickstream.mdc`.

The scoring idea is unchanged and deliberately so. `compute_cw_iq_score`
is still the four-term composite (Volume 0.50, Reach 0.30, Momentum
0.15, Recency 0.05), still z-scored against each entity's own trailing
baseline, still cold-start damped. The board still answers "who is
unusually active for them right now", not "who is biggest". Only the
two inputs change.

What a "day board" is
---------------------
The estimate snapshots (`stream_estimates.json`, `headline_estimates.
json`) carry two different things and it matters which one you read:

  * `items` is a CUMULATIVE store. An item stays in it for up to
    `carry_forward.MAX_CARRY_AGE_DAYS` after it last appeared, with its
    value walked forward. Presence in `items` is NOT evidence the item
    was on a board that day.
  * `inputs` is the list of items collected from that day's boards,
    deduped across sub-charts. That is the day's activity.

So the board is `inputs`, and `items[key]` supplies the anchor for each
board entry. Reading `items` as though it were the day's board would
credit an entity with activity on days it had none, which is the same
class of error as reading a weekly figure as a daily one.

Cadence and apportionment
-------------------------
Anchors arrive on mixed units: "weekly US searchers", "daily US
households", "daily US streams". `normalize_daily_people` converts each
one to daily people before anything compares them. Weekly divides by 7.
Household-denominated figures convert at `HOUSEHOLD_TO_PEOPLE`. Units
that count events rather than people (streams, views, plays, airings)
are NOT converted into people at all: there is no published
plays-per-person figure to divide by, and inventing one would put a
fabricated audience on the page. Those items still count toward volume,
they just contribute no audience anchor.

Volume
------
Share of the day's public attention, in basis points:

    volume = 10000 * SUM_over_surfaces( weight[s] * share[s] )
    share[s] = (anchored audience of this entity's matched items in s)
               / (anchored audience of every item in s that day)

Sharing against the day's own total is what makes the series robust to
scraper yield. A night where the news scraper returned 470 headlines
instead of 620 does not move everyone's volume, because the denominator
moved with the numerator.

Reach
-----
The largest researched daily US audience the entity reached on any one
surface that day. Max, not sum: the people searching a name and the
people reading a headline about that name are largely the same people,
and adding the two would double count them. Max is the defensible floor.

Surface mix
-----------
`surface_mix` is the share of the entity's own activity coming from each
surface. It is the honest replacement for the EVC / TDL / BVP columns,
which were defined as shares of clickstream events on particular host
classes and therefore cannot survive the migration under their own
names. See the module docstring in
`scripts/trends_scrapers/iq_rankers_signal_daily.py` for the
recommendation.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Callable, Iterable

S3_DEFAULT_BUCKET = "dashboard-inputs"
SNAPSHOT_PREFIX = "trends_iq_snapshots"

# Compact per-day board cached back into the snapshot tree so a backfill
# or a re-score does not have to re-read the 42MB estimate store.
BOARD_FILENAME = "iq_ranker_board.json"


# ---------------------------------------------------------------------------
# Normalization - reuse the existing helpers, never a new fuzzy matcher
# ---------------------------------------------------------------------------
#
# `_cp_normalize` is the entity-identity normalizer the Trends surface
# already uses for every cross-source join it does (headline keys, stream
# keys, chart-to-estimate stamping). Importing it keeps both sides of this
# join on exactly one definition of "same string". The fallback below is
# byte-for-byte the same function, present only so this module still
# imports in a context where `trends_iq`'s own imports are unavailable.

try:  # pragma: no cover - exercised by import path, not by tests
    from trends_iq import _cp_normalize as cp_normalize  # type: ignore
except Exception:  # pragma: no cover
    _CP_STOPWORDS = {
        'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
        'trending', 'today', 'now', 'news', 'latest', 'best',
    }

    def cp_normalize(text: str) -> str:  # type: ignore[misc]
        """Byte-for-byte match with `trends_iq._cp_normalize`."""
        if not text:
            return ''
        s = str(text).lower().lstrip('#').strip()
        s = re.sub(r'[^\w\s]+', ' ', s)
        tokens = [t for t in s.split() if t and t not in _CP_STOPWORDS]
        return ' '.join(tokens)


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

SURFACE_SEARCH = "search"
SURFACE_NEWS = "news"
SURFACE_ENCYCLOPEDIA = "encyclopedia"
SURFACE_CHARTS = "charts"
SURFACE_SOCIAL = "social"

ALL_SURFACES = (
    SURFACE_SEARCH, SURFACE_NEWS, SURFACE_ENCYCLOPEDIA,
    SURFACE_CHARTS, SURFACE_SOCIAL,
)

# Which surface each board-item kind belongs to. Anything not listed is a
# chart / catalog item: podcasts, songs, films, tv, games, books, comics,
# FAST channels, Wattpad stories, Goodreads titles.
_KIND_TO_SURFACE: dict[str, str] = {
    "search_term": SURFACE_SEARCH,
    "trending_person": SURFACE_NEWS,
    "headline": SURFACE_NEWS,
    "wiki_topic": SURFACE_ENCYCLOPEDIA,
    "reddit_post": SURFACE_SOCIAL,
    "instagram_post": SURFACE_SOCIAL,
    "tiktok_post": SURFACE_SOCIAL,
    "x_post": SURFACE_SOCIAL,
    "youtube_video": SURFACE_SOCIAL,
}

# Weights across surfaces. Search is the broadest daily indicator of
# public attention and carries the most; charts and news follow; the
# encyclopedia board is only 100 rows a day so it carries less.
#
# The social scrapers are wired but their snapshots are not currently
# landing in the dated archive, so the social surface contributes 0 today
# and its weight is simply unspent. That depresses every entity's volume
# by the same proportion, which a self-relative score absorbs, and the
# surface lights up on its own the day those scrapers resume.
SURFACE_WEIGHTS: dict[str, float] = {
    SURFACE_SEARCH: float(os.environ.get("IQR_SIGNAL_W_SEARCH", "0.28")),
    SURFACE_NEWS: float(os.environ.get("IQR_SIGNAL_W_NEWS", "0.24")),
    SURFACE_ENCYCLOPEDIA: float(os.environ.get("IQR_SIGNAL_W_ENCYC", "0.18")),
    SURFACE_CHARTS: float(os.environ.get("IQR_SIGNAL_W_CHARTS", "0.24")),
    SURFACE_SOCIAL: float(os.environ.get("IQR_SIGNAL_W_SOCIAL", "0.06")),
}

# Volume is reported in basis points of the day's total attention so the
# stored integers are readable rather than 0.000031.
VOLUME_SCALE = 10_000_000


# ---------------------------------------------------------------------------
# Cadence and apportionment
# ---------------------------------------------------------------------------

# US persons per household. Used only to convert a household-denominated
# audience anchor to people, so that an item measured in households is
# comparable with one measured in individuals. Sits inside the 2.0 to 2.5
# band the workspace already uses for account-to-people conversion on
# subscription services.
HOUSEHOLD_TO_PEOPLE = float(os.environ.get("IQR_HOUSEHOLD_TO_PEOPLE", "2.2"))

_WEEKLY_RE = re.compile(r"\bweek(ly)?\b", re.I)
_MONTHLY_RE = re.compile(r"\bmonth(ly)?\b", re.I)
_HOUSEHOLD_RE = re.compile(r"\bhousehold", re.I)
# Event-denominated units. A stream is not a person and a view is not a
# person. Converting either into people needs a published plays-per-person
# figure we do not have, so these contribute activity but no audience.
_EVENT_RE = re.compile(r"\b(stream|streams|view|views|play|plays|airing|airings|impression|impressions)\b", re.I)


def normalize_daily_people(value: float | int | None,
                           unit_label: str | None) -> float:
    """Convert one anchored audience figure to DAILY US PEOPLE.

    Returns 0.0 when the figure cannot honestly be expressed as a count
    of people (an event-denominated unit, a missing value, a unit we do
    not recognise). A 0 here means "no audience anchor", not "no
    audience": the caller still counts the item as activity.
    """
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    unit = (unit_label or "").strip()
    if not unit:
        return 0.0
    if _EVENT_RE.search(unit):
        return 0.0
    if _WEEKLY_RE.search(unit):
        v = v / 7.0
    elif _MONTHLY_RE.search(unit):
        v = v / 30.0
    if _HOUSEHOLD_RE.search(unit):
        v = v * HOUSEHOLD_TO_PEOPLE
    return v


# ---------------------------------------------------------------------------
# Day board
# ---------------------------------------------------------------------------


class BoardItem:
    """One entry on one day's public board.

    `audience` is already daily US people (0 when the item's unit is
    event-denominated). `text` fields are what an entity name is matched
    against: the item's own title, and its artist / author / publisher /
    source line.
    """

    __slots__ = ("key", "kind", "surface", "title", "artist", "audience", "rank")

    def __init__(self, key: str, kind: str, surface: str, title: str,
                 artist: str, audience: float, rank: int | None):
        self.key = key
        self.kind = kind
        self.surface = surface
        self.title = title
        self.artist = artist
        self.audience = audience
        self.rank = rank

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "kind": self.kind, "surface": self.surface,
            "title": self.title, "artist": self.artist,
            "audience": round(self.audience, 2), "rank": self.rank,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BoardItem":
        return cls(d.get("key") or "", d.get("kind") or "",
                   d.get("surface") or SURFACE_CHARTS,
                   d.get("title") or "", d.get("artist") or "",
                   float(d.get("audience") or 0.0), d.get("rank"))


def _s3_get_json(s3_client, bucket: str, key: str) -> dict | None:
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _rank_of(entry: dict | None) -> int | None:
    if not isinstance(entry, dict):
        return None
    r = entry.get("best_rank")
    try:
        return int(r) if r is not None else None
    except (TypeError, ValueError):
        return None


def build_day_board(*, s3_client, day: str,
                    bucket: str = S3_DEFAULT_BUCKET) -> list[BoardItem]:
    """Assemble the day's board from the dated snapshot tree.

    Reads `inputs` (what was on a board that day) from the two estimate
    snapshots and joins each entry to its anchor in `items`. Returns an
    empty list when neither snapshot is present for that date, which the
    caller must treat as "no board", never as "everybody had a quiet day".
    """
    out: list[BoardItem] = []
    base = f"{SNAPSHOT_PREFIX}/{day}"

    stream = _s3_get_json(s3_client, bucket, f"{base}/stream_estimates.json")
    if stream:
        items = stream.get("items") or {}
        for row in stream.get("inputs") or []:
            key = row.get("key") or ""
            if not key:
                continue
            kind = row.get("kind") or key.split(":", 1)[0]
            entry = items.get(key) or {}
            out.append(BoardItem(
                key=key, kind=kind,
                surface=_KIND_TO_SURFACE.get(kind, SURFACE_CHARTS),
                title=row.get("title") or entry.get("display_title") or "",
                artist=row.get("artist") or entry.get("artist") or "",
                audience=normalize_daily_people(entry.get("us_estimate"),
                                                entry.get("unit_label")),
                rank=_rank_of(entry),
            ))

    headline = _s3_get_json(s3_client, bucket, f"{base}/headline_estimates.json")
    if headline:
        items = headline.get("items") or {}
        for row in headline.get("inputs") or []:
            key = row.get("key") or ""
            if not key:
                continue
            entry = items.get(key) or {}
            out.append(BoardItem(
                key=f"headline:{key}", kind="headline", surface=SURFACE_NEWS,
                title=row.get("title") or entry.get("display_title") or "",
                # For a headline the publisher sits in the second slot, so a
                # media-brand profile matches the stories it ran that day.
                artist=row.get("source") or entry.get("source") or "",
                audience=normalize_daily_people(entry.get("us_estimate"),
                                                entry.get("unit_label")),
                rank=_rank_of(entry),
            ))

    return out


def load_day_board(*, s3_client, day: str, bucket: str = S3_DEFAULT_BUCKET,
                   allow_build: bool = True,
                   cache_to_s3: bool = False) -> list[BoardItem]:
    """Cached day board. Falls back to building it from the snapshots."""
    key = f"{SNAPSHOT_PREFIX}/{day}/{BOARD_FILENAME}"
    cached = _s3_get_json(s3_client, bucket, key)
    if cached and isinstance(cached.get("items"), list):
        return [BoardItem.from_dict(d) for d in cached["items"]]
    if not allow_build:
        return []
    board = build_day_board(s3_client=s3_client, day=day, bucket=bucket)
    if board and cache_to_s3:
        try:
            s3_client.put_object(
                Bucket=bucket, Key=key,
                Body=json.dumps({"day": day, "count": len(board),
                                 "items": [b.as_dict() for b in board]}).encode(),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"[iq_ranker_signals] board cache write failed for {day}: {e}")
    return board


# ---------------------------------------------------------------------------
# Encyclopedia census
# ---------------------------------------------------------------------------
#
# The boards reach roughly 4,200 items a day, and on a typical day only
# about one ranker entity in fifteen is on one. Encyclopedia pageviews
# are the surface that covers the rest of the population honestly: the
# Wikimedia Foundation publishes a real daily reader count for almost
# every notable person, brand and title, so an actor who charted nothing
# still has a measured audience that moves day to day.
#
# The census is written per day by
# `scripts/trends_scrapers/iq_ranker_wiki_audience.py`, already converted
# to US readers, and is keyed by profile subject rather than by text, so
# it needs no matching step at all.

CENSUS_FILENAME = "iq_ranker_wiki_audience.json"


def load_encyclopedia_census(*, s3_client, day: str,
                             bucket: str = S3_DEFAULT_BUCKET
                             ) -> dict[str, dict[str, Any]]:
    """`{profile_subject: {article, views_global, us_readers}}` for a day."""
    snap = _s3_get_json(
        s3_client, bucket, f"{SNAPSHOT_PREFIX}/{day}/{CENSUS_FILENAME}")
    if not snap:
        return {}
    items = snap.get("items")
    return items if isinstance(items, dict) else {}


def surface_totals(board: Iterable[BoardItem]) -> dict[str, float]:
    """Total anchored daily US audience per surface for one day.

    This is the denominator that makes volume a share rather than a raw
    count, so a thin scrape night does not read as a quiet news day.
    """
    totals: dict[str, float] = defaultdict(float)
    for b in board:
        if b.audience > 0:
            totals[b.surface] += b.audience
    return dict(totals)


# ---------------------------------------------------------------------------
# Entity index
# ---------------------------------------------------------------------------

# A one-token entity name is only matched on WHOLE-TEXT equality, never as
# a fragment inside a longer title. Without that guard a profile called
# "Animated" matches "Mr. Bean: Animated" and a profile called "Max"
# matches every title with the word in it. Multi-token names are specific
# enough to match as a contiguous run of tokens.
_MAX_NAME_TOKENS = 6


class EntityIndex:
    """Maps board text to ranker entities.

    Built from the same terms the ranker already resolves for each
    profile (`iq_rankers._build_ranker_brand_terms`), normalized through
    the same `_cp_normalize` the Trends surface uses everywhere else. No
    new fuzzy matching: a name either appears in the text as a contiguous
    run of normalized tokens or it does not.
    """

    def __init__(self) -> None:
        self._multi: dict[str, set[str]] = defaultdict(set)
        self._single: dict[str, set[str]] = defaultdict(set)
        self._names: dict[str, set[str]] = defaultdict(set)

    def add(self, subject: str, names: Iterable[str]) -> None:
        for raw in names:
            n = cp_normalize(raw)
            if not n:
                continue
            toks = n.split()
            if len(toks) > _MAX_NAME_TOKENS:
                continue
            if len(toks) == 1:
                self._single[n].add(subject)
            else:
                self._multi[n].add(subject)
            self._names[subject].add(n)

    def __len__(self) -> int:
        return len(self._names)

    @property
    def subjects(self) -> set[str]:
        return set(self._names)

    def match(self, text: str) -> set[str]:
        """Subjects named in `text`."""
        n = cp_normalize(text)
        if not n:
            return set()
        hits: set[str] = set()
        hits |= self._single.get(n, set())
        if not self._multi:
            return hits
        toks = n.split()
        for i in range(len(toks)):
            for L in range(2, min(_MAX_NAME_TOKENS, len(toks) - i) + 1):
                got = self._multi.get(" ".join(toks[i:i + L]))
                if got:
                    hits |= got
        return hits


def build_entity_index(entities: Iterable[dict],
                       term_resolver: Callable[[dict], list[str]] | None = None
                       ) -> EntityIndex:
    """Index the ranker's entities by every name they can be called.

    `entities` are the job dicts `iq_rankers._iter_profile_jobs` yields.
    `term_resolver` supplies the profile's resolved terms (the BRAND INPUT
    row plus the name variants the ranker already derives); when it is
    absent the display name alone is indexed.
    """
    idx = EntityIndex()
    for job in entities:
        subject = job.get("profile_subject") or ""
        if not subject:
            continue
        display = (job.get("display_name") or job.get("project_name")
                   or subject or "")
        # A derived cut is named "{Subject} - {Cut}"; the entity is the
        # subject part. Everything after " - " is our label, not a name
        # anything public would ever call it.
        names = {display.split(" - ", 1)[0]}
        if term_resolver:
            try:
                for t in term_resolver(job) or []:
                    names.add(t)
            except Exception:
                pass
        idx.add(subject, names)
    return idx


# ---------------------------------------------------------------------------
# Per-entity daily metrics
# ---------------------------------------------------------------------------


def augment_totals_with_census(totals: dict[str, float],
                               census: dict[str, dict[str, Any]] | None
                               ) -> dict[str, float]:
    """Add the tracked population's encyclopedia readership to the day's
    denominators.

    An entity's encyclopedia share is then its share of encyclopedia
    attention across everything the ranker follows, plus whatever the
    trending encyclopedia board carried on top.
    """
    if not census:
        return dict(totals)
    out = dict(totals)
    out[SURFACE_ENCYCLOPEDIA] = (
        out.get(SURFACE_ENCYCLOPEDIA, 0.0)
        + sum(float(v.get("us_readers") or 0) for v in census.values()))
    return out


def compute_signal_metrics_for_day(*, board: list[BoardItem],
                                   index: EntityIndex,
                                   totals: dict[str, float] | None = None,
                                   census: dict[str, dict[str, Any]] | None = None
                                   ) -> dict[str, dict[str, Any]]:
    """Volume, reach and surface mix for every entity with signal today.

    Only entities with signal appear in the result. An entity absent from
    the result had none on the day, which is a real finding and must be
    rendered as such rather than scored.
    """
    if totals is None:
        totals = surface_totals(board)
    census = census or {}

    totals = augment_totals_with_census(totals, census)

    # audience the entity captured, per surface
    captured: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    best: dict[str, float] = defaultdict(float)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    top: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for subj, row in census.items():
        readers = float(row.get("us_readers") or 0)
        if readers <= 0:
            continue
        counts[subj][SURFACE_ENCYCLOPEDIA] += 1
        captured[subj][SURFACE_ENCYCLOPEDIA] += readers
        if readers > best[subj]:
            best[subj] = readers
        top[subj].append({"key": f"wiki:{row.get('article') or ''}",
                          "kind": "encyclopedia", "surface": SURFACE_ENCYCLOPEDIA,
                          "title": row.get("article") or "",
                          "audience": round(readers, 1), "rank": None})

    for item in board:
        subjects = index.match(item.title)
        if item.artist:
            subjects = subjects | index.match(item.artist)
        if not subjects:
            continue
        for subj in subjects:
            counts[subj][item.surface] += 1
            if item.audience > 0:
                captured[subj][item.surface] += item.audience
                if item.audience > best[subj]:
                    best[subj] = item.audience
            top[subj].append({"key": item.key, "kind": item.kind,
                              "surface": item.surface, "title": item.title,
                              "audience": round(item.audience, 1),
                              "rank": item.rank})

    out: dict[str, dict[str, Any]] = {}
    for subj, per_surface in counts.items():
        weighted = 0.0
        mix_raw: dict[str, float] = {}
        for surface, n in per_surface.items():
            total = totals.get(surface) or 0.0
            got = captured[subj].get(surface, 0.0)
            if total > 0 and got > 0:
                share = got / total
            elif n > 0:
                # The surface produced no usable audience anchor (every
                # matched item was event-denominated). Presence still
                # counts, at the smallest share the surface can express,
                # so the row does not silently vanish.
                share = 0.0
            else:
                share = 0.0
            contrib = SURFACE_WEIGHTS.get(surface, 0.0) * share
            mix_raw[surface] = contrib
            weighted += contrib

        volume = weighted * VOLUME_SCALE
        mix_total = sum(mix_raw.values())
        mix = ({k: round(100.0 * v / mix_total, 1)
                for k, v in mix_raw.items() if v > 0}
               if mix_total > 0 else {})
        items = sorted(top[subj], key=lambda d: -(d.get("audience") or 0))[:8]
        out[subj] = {
            "volume": round(volume, 4),
            "reach": int(round(best[subj])),
            "surface_counts": dict(per_surface),
            "surface_mix": mix,
            "matched_items": len(top[subj]),
            "top_items": items,
            "surfaces": sorted(per_surface),
        }
    return out


# ---------------------------------------------------------------------------
# Day helpers
# ---------------------------------------------------------------------------


def recent_days(end: str, n: int) -> list[str]:
    """`n` ISO dates ending at `end`, most recent first."""
    e = date.fromisoformat(end)
    return [(e - timedelta(days=i)).isoformat() for i in range(n)]


_board_cache: dict[str, list[BoardItem]] = {}
_board_lock = threading.Lock()


def cached_board(*, s3_client, day: str, bucket: str = S3_DEFAULT_BUCKET,
                 cache_to_s3: bool = False) -> list[BoardItem]:
    """Process-level board cache so a multi-day pass reads each day once."""
    with _board_lock:
        got = _board_cache.get(day)
    if got is not None:
        return got
    board = load_day_board(s3_client=s3_client, day=day, bucket=bucket,
                           cache_to_s3=cache_to_s3)
    with _board_lock:
        _board_cache[day] = board
    return board
