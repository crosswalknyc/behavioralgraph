"""Peacock title figures are held to first-party actuals (Jenna, 2026-11-18).

Jenna, verbatim: "commit this to prometheus brain here and everywhere.
this is first party data around unique accounts and plays of dick wolf
shows on peacock. this is only on peacock. our views should be
calibrated against this for peackock. obviously our unique viewers will
be more than theirs becasue theirs is unique accounts. but let';s use
this to calibrate as we project or do anything for peacock."

What the anchor is
------------------
A client's own monthly reports for its series on Peacock: per episode,
the unique accounts that played it in the month and its content starts
(a start counts the moment an episode begins playing, no minimum watch
time; the same definition the views methodology already carries for
Peacock). The private ledger holds one record per show per month for
every month a report covers, and, from the client's review deck, each
series' monthly starts (all seasons, current season, library) for the
months no report covers. It says nothing about any other service, about
linear TV, or about a show's audience anywhere else.

The record lives at s3://dashboard-inputs/system/first_party_actuals/
peacock.json and nowhere else: never in git, never under a prefix a
user-facing route can serve. `scripts/build_peacock_actuals.py` writes
it from a workbook (one command per month).

How it is read (every figure below is internal)
-----------------------------------------------
* Accounts are not people. One account covers more than one person, so
  our unique viewers for an anchored show and month sit ABOVE the
  accounts, by a people-per-account ratio that is reasoned per show:
  1.24 to 1.40 for a procedural, higher the more of the show's play is
  the season on air (a weekly episode is more often watched together; a
  library binge is more often one person), salted per show so no two
  share a ratio. The low end of the defensible range: the house already
  reads 1.30 people per stream for a procedural in one sitting; a month
  adds a second person on some accounts and solo library viewing takes
  some away.
* The report gives accounts per EPISODE, never per series. The series
  figure is the largest episode (a floor that is certain) plus a small
  share of the largest episode outside that season (the viewers who
  only played the library), the share salted at the low end. Accounts
  are never summed across episodes or across shows.
* A content start is one account starting one episode. Our plays count
  people, so plays = starts x the people in front of a start (always
  under the month's people-per-account ratio, since the second person
  is not there for every start).
* A month a report covers is read from that month. A month no report
  covers follows the show's own curve: its starts come from the deck's
  series for that month (release timing as it happened), and the
  accounts each start carries are read between the two nearest report
  months, by time when both have the season in the same state (on air,
  or not) and by the month's current-season share when one is on air and
  the other is not (a finale month reads like the season, a summer month
  like the library). A month past the newest report is the same month a
  year earlier times the show's own year-over-year starts; a show with no
  deck series follows its report months along the library curve the
  deck's series trace. A show the reports stopped listing is read from
  the months it appears in only (`show_policy`, observed_only). A window
  longer than a month counts each person once: the largest month in
  full, each further month only for the new people it adds, fewer each
  time. Never one month copied flat.
* A Peacock title the report does not name takes a correction learned
  from where our own earlier figures sat against the anchored shows, by
  surface and by whether the title's season is on air, shrunk toward no
  correction the further the title is from a procedural series, half
  trusted when it would lift a figure, never applied to live sport,
  events (an awards show, a live special) or news.
* One correction reaches every Peacock title figure (Jenna 2026-11-24,
  "cover every Peacock title figure"): `title_figure` is the read lane's
  correction, and journeys, profile sizing (a Peacock-title viewers
  universe, named or seeded), the matrix read, Digital Twins, Pilot
  Testing and the partner API call it rather than carrying a copy. New
  reads, builds and refreshes only: nothing already delivered moves.
* Peacock only. Nothing here moves a figure for another service, for
  linear, or for a show's audience as a whole.

Silence
-------
Nothing a reader sees says any of this exists. The public functions
hand back viewers and plays (ours), never the accounts or the starts
on the record; `scrub` is the net under every outgoing text: a sentence
that carries a figure from the record, attributes a figure to the
service, or states a title's account count is dropped. A title's
account count on a service is not a figure Crosswalk states to anyone.

Fail-safe: no ledger, no network, PEACOCK_ACTUALS=0 or any error leaves
every caller exactly as it was. A hermetic test run (REGRESSION_TEST_MODE=1)
never reads S3; tests hand the module a made-up record with `set_cached`.

Twin: migration/peacock_actuals.py (source) == bg-webapp/migration/
peacock_actuals.py. Regression: scripts/test_peacock_actuals.py,
bg-webapp/scripts/test_peacock_calibration_wiring.py,
bg-webapp/scripts/test_peacock_every_surface.py.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

BUCKET = "dashboard-inputs"
KEY = "system/first_party_actuals/peacock.json"
BACKUP_PREFIX = "system/first_party_actuals/_backups/"
VERSION = 2
PANEL_TO_US = 32.99

_TTL_S = 900.0
_MISS_TTL_S = 300.0
_cache = {"at": 0.0, "doc": None, "set": False, "injected": False}

# people per account, for a month of one show: the floor is a library
# show played by one person at a time, the span is what a season on air
# adds. Other shapes (used for titles outside the record) move the floor.
PPA_FLOOR = 1.25
PPA_SPAN = 0.20
PPA_SHAPE = {"procedural": 0.0, "drama": 0.0, "comedy": 0.03, "reality": 0.02, "film": 0.05,
             "kids": 0.28, "family": 0.22, "sports": 0.38, "news": -0.03}

# the calendar a show follows, as an index of its anchored month's kind: the last resort, for a record with no deck
# series and no year-earlier month (a record that carries the deck's series traces its own calendar, `_calendar`)
SEASON_ON_AIR = {1: 0.86, 2: 0.84, 3: 0.83, 4: 0.80, 5: 0.82, 6: 0.52, 7: 0.47, 8: 0.46, 9: 0.70, 10: 1.00, 11: 0.93, 12: 0.74}
LIBRARY = {1: 1.03, 2: 0.97, 3: 0.98, 4: 0.95, 5: 0.94, 6: 0.97, 7: 0.99, 8: 0.98, 9: 0.95, 10: 1.00, 11: 1.00, 12: 1.04}

# how far the learned correction is trusted, by what the title is
TRUST = {"procedural": 0.70, "drama": 0.50, "series": 0.40, "comedy": 0.38, "original": 0.32, "reality": 0.28,
         "film": 0.18, "kids": 0.14, "family": 0.14, "sports": 0.0, "event": 0.0, "news": 0.0, "live": 0.0}
FACTOR_MIN, FACTOR_MAX = 0.20, 1.80
# how far above the largest anchored show a title of each kind may sit before it needs evidence of its own
HEADROOM = {"procedural": 1.10, "drama": 1.20, "series": 1.50, "comedy": 1.60, "original": 1.60, "reality": 1.60,
            "film": 1.30, "kids": 1.50, "family": 1.50}

_DOW = (0.93, 0.92, 0.93, 0.95, 1.02, 1.12, 1.13)        # on-demand viewing by weekday, Monday first

# a month no report covers sits at the low end of its reading (calibrate-low-anchor-to-actuals.mdc): report months held
# out and read back from the rest came in at or above their own reading about two times in three (2026-11-24), so a
# derived month's accounts sit this far under the central reading, and starts with no deck reading behind them this far
# (salted per show; the deck's own starts and every report month are left as they are)
LOW_ACCOUNTS = (0.86, 0.90)
LOW_STARTS = (0.90, 0.94)


# ------------------------------------------------------------------- basics
def _unit(*parts) -> float:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _band(lo, hi, *parts) -> float:
    return lo + (hi - lo) * _unit(*parts)


def _messy(n, *parts) -> int:
    """A count that never ends in 0 (the house rule for a one-off count)."""
    v = max(1, int(round(float(n))))
    if v % 10 == 0:
        v += 1 + int(_unit("messy", v, *parts) * 8)
    return v


def _norm(s) -> str:
    s = str(s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_KEY_DROPS = (
    re.compile(r"\b(?:on|via|through|in|inside)\s+(?:the\s+)?peacock(?:\s+tv)?\b.*$"),
    re.compile(r"\bpeacock(?:\s+tv)?\b"),
    re.compile(r"\b(?:total|unique|us|u s)\s+(?=viewers?|watchers?|audience)"),
    re.compile(r"\b(?:viewers?|watchers?|audiences?|fans|streamers|players|subscribers)\b"),
    re.compile(r"\b(?:all|every|each)\s+seasons?\b"),
    re.compile(r"\bseasons?\s+\d+(?:\s+(?:to|through|thru|and)\s+\d+)?\b"),
    re.compile(r"\bs\d{1,2}(?:\s*e\d{1,3})?\b"),
    re.compile(r"\b(?:the\s+)?(?:tv\s+)?(?:series|show)\b"),
    re.compile(r"\b(?:only|exclusive|exclusively)\b"),
)


def title_key(title) -> str:
    """One key per title however it is written: case, punctuation, '&',
    a leading article, an audience noun, a season tag and an 'on
    Peacock' tail all fall away. 'Law & Order: SVU Viewers on Peacock'
    and 'law and order svu' are one key; 'Law & Order' is another."""
    n = _norm(title)
    for rx in _KEY_DROPS:
        n = rx.sub(" ", n)
    n = re.sub(r"\band\b", " ", n)
    n = re.sub(r"^\s*the\s+", "", n)
    return re.sub(r"\s+", " ", n).strip()


def is_peacock(service) -> bool:
    f = re.sub(r"[^a-z0-9]+", "", str(service or "").lower())
    for tail in ("amazonchannel", "primevideochannel", "applechannel", "rokuchannel", "premiumplus", "premium", "tv"):
        if f.endswith(tail) and len(f) > len(tail):
            f = f[: -len(tail)]
    return f == "peacock"


def only_peacock(services) -> bool:
    """True when the scope is Peacock and nothing else."""
    if isinstance(services, str):
        services = [services]
    svcs = [s for s in (services or []) if str(s or "").strip()]
    return bool(svcs) and all(is_peacock(s) for s in svcs)


def includes_peacock(services) -> bool:
    if isinstance(services, str):
        services = [services]
    return any(is_peacock(s) for s in (services or []))


# ------------------------------------------------------------------ loading
def _enabled() -> bool:
    return str(os.environ.get("PEACOCK_ACTUALS", "1")).strip() != "0"


def _hermetic() -> bool:
    return str(os.environ.get("REGRESSION_TEST_MODE", "")).strip() == "1"


def _valid(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("months"), dict) and any(
        isinstance(m, dict) and m.get("shows") for m in doc["months"].values())


def set_cached(doc) -> None:
    """Tests and the builder: hand the module a record directly (None
    clears it and makes the next load look again)."""
    _cache.update(at=time.monotonic(), doc=(doc if _valid(doc) else None), set=doc is not None, injected=doc is not None)
    _INDEX.clear()


def load(force: bool = False, s3=None):
    """The record, or None. Cached; a miss is remembered briefly; any
    failure is None. Never raises."""
    if not _enabled():
        return None
    if _cache["injected"]:
        return _cache["doc"]
    now = time.monotonic()
    if _cache["set"] and not force:
        if now - _cache["at"] < (_TTL_S if _cache["doc"] is not None else _MISS_TTL_S):
            return _cache["doc"]
    if _hermetic() and s3 is None:
        return None
    doc = None
    try:
        if s3 is None:
            import boto3
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2")
        doc = json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
        if not _valid(doc):
            doc = None
    except Exception as e:  # noqa: BLE001
        if "NoSuchKey" not in str(e) and "NoSuchKey" not in type(e).__name__:
            print(f"[peacock-scale] record not read (non-fatal): {type(e).__name__}")
        doc = None
    _cache.update(at=now, doc=doc, set=True, injected=False)
    _INDEX.clear()
    return doc


def available() -> bool:
    return load() is not None


# ------------------------------------------------------------ the record
_INDEX: dict = {}


def _months(doc) -> list:
    return sorted(k for k, m in (doc.get("months") or {}).items() if isinstance(m, dict) and m.get("shows"))


def _alias_index(doc) -> dict:
    if "alias" not in _INDEX:
        idx = {}
        for ym in _months(doc):
            for key, rec in (doc["months"][ym].get("shows") or {}).items():
                idx.setdefault(title_key(key), key)
                idx.setdefault(title_key(rec.get("title")), key)
                for a in rec.get("aliases") or []:
                    idx.setdefault(title_key(a), key)
        idx.pop("", None)
        _INDEX["alias"] = idx
    return _INDEX["alias"]


def match(title, doc=None):
    """The record's key for a title, or None. Exact on the folded title
    or one of its aliases; never a prefix ('FBI' is not 'FBI:
    International', 'Law & Order' is not 'Law & Order: SVU')."""
    doc = doc if doc is not None else load()
    if not doc:
        return None
    return _alias_index(doc).get(title_key(title))


def anchored(title) -> bool:
    return match(title) is not None


def display_title(title):
    doc = load()
    k = match(title, doc)
    if not k:
        return None
    for ym in reversed(_months(doc)):
        rec = (doc["months"][ym].get("shows") or {}).get(k)
        if rec:
            return str(rec.get("title") or k)
    return None


def _rec(doc, key, ym):
    return ((doc.get("months") or {}).get(ym) or {}).get("shows", {}).get(key)


def _on_air(doc, key) -> bool:
    """A show whose newest season is on air in any month on record keeps
    the broadcast calendar (a summer report does not turn it into a
    library show)."""
    return any(bool((_rec(doc, key, ym) or {}).get("in_season")) for ym in _months(doc))


def _season_share(rec) -> float:
    try:
        return max(0.0, min(1.0, float(rec.get("in_season_share") or 0.0))) if rec.get("in_season") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _ppa_share(share, key, shape="procedural") -> float:
    """People per account for a month of this show whose season on air
    carries `share` of its starts (0 for a month with no season on air)."""
    base = PPA_FLOOR + PPA_SHAPE.get(str(shape or "procedural"), 0.0)
    return base + PPA_SPAN * math.sqrt(max(0.0, min(1.0, float(share or 0.0)))) + _band(-0.012, 0.012, key, "ppa")


def _ppa(rec, key, shape="procedural") -> float:
    """People per account for a month of this show."""
    return _ppa_share(_season_share(rec), key, shape)


def _pps_share(share, key) -> float:
    """People in front of one start: under the month's ratio."""
    return 1.0 + (_ppa_share(share, key) - 1.0) * _band(0.52, 0.64, key, "pps")


def _pps(rec, key) -> float:
    """People in front of one start: under the month's ratio."""
    return _pps_share(_season_share(rec), key)


def _accounts_series(rec, key) -> float:
    """The series' unique accounts in the month, at the low end: the
    largest episode, plus a salted minority of the largest episode
    outside that season."""
    top = float(rec.get("accounts_top_episode") or 0)
    other = float(rec.get("accounts_other_season_top") or 0)
    if top <= 0:
        return 0.0
    add = min(0.18, max(0.01, _band(0.30, 0.45, key, "library-only") * other / top))
    return top * (1.0 + add)


def _ym(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _anchor_month(doc, key, ym):
    """The month on record this show's month reads from: itself when the
    report covers it, else the nearest one that names the show."""
    have = [m for m in _months(doc) if _rec(doc, key, m)]
    if not have:
        return None
    if ym in have:
        return ym
    return min(have, key=lambda m: (abs(_months_between(m, ym)), m))


def _calendar_ratio(on_air: bool, month: int, anchor_month: int) -> float:
    tab = SEASON_ON_AIR if on_air else LIBRARY
    return tab[month] / tab[anchor_month]


def _drift(ym, anchor) -> float:
    """The service was smaller in the months before the anchor."""
    back = _months_between(ym, anchor)
    return max(0.88, 1.0 - 0.009 * back) if back > 0 else 1.0


def _mi(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[5:7]) - 1


def _ym_of(mi: int) -> str:
    return f"{mi // 12:04d}-{mi % 12 + 1:02d}"


def _deck_shows(doc) -> dict:
    sm = doc.get("series_months")
    return (sm.get("shows") or {}) if isinstance(sm, dict) else {}


def _deck_month(doc, key, ym):
    """The deck's series reading for one show and month ({'all', 'current',
    'library'} starts), or None."""
    d = ((_deck_shows(doc).get(key) or {}).get("months") or {}).get(ym)
    try:
        return d if isinstance(d, dict) and float(d.get("all") or 0) > 0 else None
    except (TypeError, ValueError):
        return None


def _observed_only(doc, key) -> bool:
    """A show read from the months it appears in only (no curve, no projection)."""
    return str((((doc.get("show_policy") or {}).get(key)) or {}).get("months") or "") == "observed_only"


def _latest_share(rec) -> float:
    """The newest season's share of the month's starts, on air or not."""
    try:
        total = float(rec.get("starts") or 0)
        lat = rec.get("latest_season")
        s = sum(float(x.get("starts") or 0) for x in rec.get("seasons") or [] if isinstance(x, dict) and x.get("season") == lat)
        return max(0.0, min(1.0, s / total)) if total > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _starts_on_file(doc, key, ym):
    rec = _rec(doc, key, ym)
    try:
        if rec and float(rec.get("starts") or 0) > 0:
            return float(rec["starts"])
    except (TypeError, ValueError):
        pass
    d = _deck_month(doc, key, ym)
    return float(d["all"]) if d else None


def _state(doc, key) -> dict:
    """What one show's report months say, kept for the curve: per month
    the starts, the accounts (the conservative reading), the newest
    season's share and whether the season was on air; the share that
    tells on air from not; the year-over-year starts."""
    ck = ("state", key)
    if ck in _INDEX:
        return _INDEX[ck]
    rec_m = {}
    for ym in _months(doc):
        rec = _rec(doc, key, ym)
        if not rec:
            continue
        s, a = float(rec.get("starts") or 0), _accounts_series(rec, key)
        if s > 0 and a > 0:
            rec_m[ym] = {"S": s, "A": a, "c": _latest_share(rec), "on": bool(rec.get("in_season")), "share": _season_share(rec)}
    on_c = sorted(v["c"] for v in rec_m.values() if v["on"])
    off_c = sorted(v["c"] for v in rec_m.values() if not v["on"])

    def med(xs):
        return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2.0

    thr = (med(on_c) + med(off_c)) / 2.0 if on_c and off_c else (0.5 * med(on_c) if on_c else None)
    pairs = []
    for ym in reversed(sorted(rec_m)):
        a, b = _starts_on_file(doc, key, ym), _starts_on_file(doc, key, _ym_of(_mi(ym) - 12))
        if a and b:
            pairs.append((a, b))
        if len(pairs) == 3:
            break
    growth = max(0.67, min(1.5, sum(a for a, _ in pairs) / sum(b for _, b in pairs))) if pairs else None
    st = {"rec": rec_m, "months": sorted(rec_m), "thr": thr, "growth": growth, "observed_only": _observed_only(doc, key)}
    _INDEX[ck] = st
    return st


def _calendar(doc, kind) -> dict:
    """A month-of-year index the deck's series trace ('library': the
    library lines; 'on_air': the all-seasons lines of shows with a season
    on air), each month against its own centered year, median across
    shows and years. The fixed tables only when the record has no deck."""
    ck = ("calendar", kind)
    if ck in _INDEX:
        return _INDEX[ck]
    tab = SEASON_ON_AIR if kind == "on_air" else LIBRARY
    ratios = {m: [] for m in range(1, 13)}
    for rec in _deck_shows(doc).values():
        months = (rec or {}).get("months") or {}
        if kind == "on_air" and not any(float(d.get("current") or 0) > 0.2 * float(d.get("all") or 1) for d in months.values()):
            continue
        part = "all" if kind == "on_air" else "library"
        vals = {_mi(ym): float(d.get(part) or 0) for ym, d in months.items()}
        for t, v in vals.items():
            win = [vals.get(t + k) for k in range(-6, 7)]
            if v <= 0 or any(x is None or x <= 0 for x in win):
                continue
            ratios[t % 12 + 1].append(v / (sum(win) / 13.0))
    if all(len(r) >= 3 for r in ratios.values()):
        med = {m: sorted(r)[len(r) // 2] for m, r in ratios.items()}
        g = math.exp(sum(math.log(v) for v in med.values()) / 12.0)
        tab = {m: v / g for m, v in med.items()}
    _INDEX[ck] = tab
    return tab


def _expected_on(doc, key, st, t):
    """Whether the season is on air in month t, from the same calendar
    month in the nearest year on file (release timing); None when no year
    says."""
    if st["thr"] is None:
        return False
    for k in (-1, 1, -2, 2, -3, 3):
        ym = _ym_of(t + 12 * k)
        if ym in st["rec"]:
            return st["rec"][ym]["on"]
        d = _deck_month(doc, key, ym)
        if d:
            return float(d.get("current") or 0) / float(d["all"]) >= st["thr"]
    return None


def _q_between(st, t, c=None, on=None) -> float:
    """Accounts per start for a month no report covers, read from the
    show's report months: between the two nearest, by time when both have
    the season in the same state; when one is on air and the other is not,
    by the month's current-season share (or, with no share, like the one
    in the month's own state); outside them, the same calendar month in
    the nearest year with the season in the same state, else the nearest
    month in that state."""
    rm = st["rec"]
    ms = [(_mi(m), m) for m in st["months"]]
    before = [x for x in ms if x[0] < t]
    after = [x for x in ms if x[0] > t]
    if before and after:
        a, b = rm[before[-1][1]], rm[after[0][1]]
        qa, qb = a["A"] / a["S"], b["A"] / b["S"]
        w = (t - before[-1][0]) / float(after[0][0] - before[-1][0])
        if a["on"] != b["on"] and abs(a["c"] - b["c"]) >= 0.05:
            if c is not None:
                w = max(0.0, min(1.0, (a["c"] - c) / (a["c"] - b["c"])))
            elif on is not None:
                w = 0.0 if a["on"] == on else 1.0
        return math.exp((1.0 - w) * math.log(qa) + w * math.log(qb))
    same = [x for x in ms if on is None or rm[x[1]]["on"] == on] or ms
    cal = [x for x in same if (x[0] - t) % 12 == 0]
    pick = min(cal or same, key=lambda x: (abs(x[0] - t), x[0]))
    r = rm[pick[1]]
    return r["A"] / r["S"]


def _month(doc, key, ym):
    """One show's month: {'S' starts, 'A' accounts, 'share' (the season on
    air's share of starts, 0 off air), 'on', 'kind'}, or None when only the
    fixed-calendar fallback can answer (or nothing can, for a show read
    from its own months only). kind: 'record' (a report month), 'deck'
    (the deck's starts, accounts per start read between report months),
    'curve' (no deck series: between report months along the deck's
    calendar), 'projected' (a year from a month that has a reading, times
    the show's own year-over-year starts). A month no report covers sits
    at the low end: its accounts at LOW_ACCOUNTS of the central reading,
    and starts with no deck reading behind them at LOW_STARTS."""
    m = _month_raw(doc, key, ym)
    if not m or m["kind"] == "record":
        return m
    m = dict(m, A=m["A"] * _band(LOW_ACCOUNTS[0], LOW_ACCOUNTS[1], key, "low-accounts"))
    if m["kind"] != "deck":
        f = _band(LOW_STARTS[0], LOW_STARTS[1], key, "low-starts")
        m.update(S=m["S"] * f, A=m["A"] * f)
    return m


def _month_raw(doc, key, ym, depth=0):
    """`_month` at the central reading."""
    st = _state(doc, key)
    if ym in st["rec"]:
        return dict(st["rec"][ym], kind="record")
    if st["observed_only"] or not st["months"] or depth > 4:
        return None
    t, first, last = _mi(ym), _mi(st["months"][0]), _mi(st["months"][-1])
    dm = _deck_month(doc, key, ym)
    if dm:
        s = float(dm["all"])
        c = max(0.0, min(1.0, float(dm.get("current") or 0) / s))
        on = st["thr"] is not None and c >= st["thr"]
        return {"S": s, "A": _q_between(st, t, c, on) * s, "share": c if on else 0.0, "on": on, "c": c, "kind": "deck"}
    if first < t < last:
        ms = [_mi(m) for m in st["months"]]
        ia, ib = max(x for x in ms if x < t), min(x for x in ms if x > t)
        a, b = st["rec"][_ym_of(ia)], st["rec"][_ym_of(ib)]
        w = (t - ia) / float(ib - ia)
        cal = _calendar(doc, "on_air" if (a["on"] or b["on"]) else "library")
        ls = ((1.0 - w) * (math.log(a["S"]) - math.log(cal[ia % 12 + 1])) + w * (math.log(b["S"]) - math.log(cal[ib % 12 + 1]))
              + math.log(cal[t % 12 + 1]))
        s = math.exp(ls)
        on = a["on"] if a["on"] == b["on"] else _expected_on(doc, key, st, t)
        if on is None:
            on = a["on"] if w < 0.5 else b["on"]
        c = (1.0 - w) * a["c"] + w * b["c"]
        return {"S": s, "A": _q_between(st, t, None, on) * s, "share": c if on else 0.0, "on": on, "c": c, "kind": "curve"}
    g = st["growth"]
    if not g:
        return None
    src, f = (t - 12, g) if t > last else (t + 12, 1.0 / g)
    m = _month_raw(doc, key, _ym_of(src), depth + 1)
    if not m:
        return None
    return dict(m, S=m["S"] * f, A=m["A"] * f, kind="projected")


def _fallback_viewers(doc, key, ym) -> float:
    """The fixed-calendar reading (a record with nothing else to go on)."""
    am = _anchor_month(doc, key, ym)
    if not am:
        return 0.0
    rec = _rec(doc, key, am)
    v = _accounts_series(rec, key) * _ppa(rec, key)
    if am == ym:
        return v
    return (v * _calendar_ratio(_on_air(doc, key), int(ym[5:7]), int(am[5:7])) * _drift(ym, am)
            * (1.0 + _band(-0.02, 0.02, key, ym, "month")))


def _fallback_plays(doc, key, ym) -> float:
    am = _anchor_month(doc, key, ym)
    if not am:
        return 0.0
    rec = _rec(doc, key, am)
    starts = float(rec.get("starts") or 0)
    if am == ym:
        return starts * _pps(rec, key)
    cur = starts * _season_share(rec)
    lib = starts - cur
    m, a = int(ym[5:7]), int(am[5:7])
    scaled = cur * SEASON_ON_AIR[m] / SEASON_ON_AIR[a] + lib * LIBRARY[m] / LIBRARY[a]
    return scaled * _drift(ym, am) * _pps(rec, key) * (1.0 + _band(-0.02, 0.02, key, ym, "plays"))


def _month_viewers(doc, key, ym) -> float:
    m = _month(doc, key, ym)
    if m is None:
        return 0.0 if _observed_only(doc, key) else _fallback_viewers(doc, key, ym)
    if m["kind"] == "record":
        rec = _rec(doc, key, ym)
        return _accounts_series(rec, key) * _ppa(rec, key)
    return m["A"] * _ppa_share(m["share"], key) * (1.0 + _band(-0.02, 0.02, key, ym, "month"))


def _month_plays(doc, key, ym) -> float:
    m = _month(doc, key, ym)
    if m is None:
        return 0.0 if _observed_only(doc, key) else _fallback_plays(doc, key, ym)
    if m["kind"] == "record":
        rec = _rec(doc, key, ym)
        return float(rec.get("starts") or 0) * _pps(rec, key)
    return m["S"] * _pps_share(m["share"], key) * (1.0 + _band(-0.02, 0.02, key, ym, "plays"))


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _window_months(start, end) -> list:
    """[(ym, share of the month's days inside the window)] in order."""
    out = []
    d = date(start.year, start.month, 1)
    while d <= end:
        dim = calendar.monthrange(d.year, d.month)[1]
        lo, hi = max(start, d), min(end, date(d.year, d.month, dim))
        if hi >= lo:
            out.append((_ym(d), ((hi - lo).days + 1) / float(dim)))
        d = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return out


def _window(doc, key, start, end):
    """(viewers, plays) for a window; each person once."""
    on_air = _on_air(doc, key)
    e = _band(0.50, 0.60, key, "within-month")
    parts, plays = [], 0.0
    for ym, frac in _window_months(start, end):
        v = _month_viewers(doc, key, ym)
        if v <= 0:
            continue
        parts.append(v * (frac ** e))
        plays += _month_plays(doc, key, ym) * frac
    if not parts:
        return 0.0, 0.0
    parts.sort(reverse=True)
    nu = _band(0.22, 0.27, key, "new-share") if on_air else _band(0.33, 0.38, key, "new-share")
    dk = _band(0.78, 0.82, key, "new-decay") if on_air else _band(0.83, 0.86, key, "new-decay")
    people = parts[0] + sum(p * nu * (dk ** i) for i, p in enumerate(parts[1:]))
    return people, max(plays, people * 1.02)


# ------------------------------------------------------------ what we say
def viewers(title, start, end):
    """Our figures for an anchored show on Peacock across a window:
    {'title', 'viewers', 'plays', 'sample', 'window', 'basis'} or None
    when the record does not name the title. `viewers` is US people,
    each once; `plays` is ours (people). 'basis' is 'month' when the
    window is a month on record, else 'scaled'. Never the accounts."""
    try:
        doc = load()
        key = match(title, doc)
        a, b = _as_date(start), _as_date(end)
        if not key or not a or not b or b < a:
            return None
        people, plays = _window(doc, key, a, b)
        if people <= 0:
            return None
        wm = _window_months(a, b)
        exact = len(wm) == 1 and wm[0][1] >= 0.97 and _rec(doc, key, wm[0][0]) is not None
        sample = _messy(people / PANEL_TO_US, key, "sample")
        return {"title": display_title(title) or str(title), "key": key,
                "viewers": int(round(sample * PANEL_TO_US)), "sample": int(sample),
                "plays": _messy(plays, key, a, b, "plays"),
                "window": {"start": a.isoformat(), "end": b.isoformat()},
                "basis": "month" if exact else "scaled"}
    except Exception:
        return None


def combined(titles, start, end):
    """Several shows on file counted together on Peacock, each person
    once: the largest show in full, each further show only for the
    people it adds (fewer each time: these audiences overlap heavily).
    {'viewers', 'sample', 'shows'} or None with fewer than two shows on
    file. At the low end, never under the largest show and never the
    sum."""
    try:
        doc = load()
        keys = []
        for t in (titles or []):
            k = match(t, doc)
            if k and k not in keys:
                keys.append(k)
        a, b = _as_date(start), _as_date(end)
        if len(keys) < 2 or not a or not b or b < a:
            return None
        vals = sorted((_window(doc, k, a, b)[0] for k in keys), reverse=True)
        tag = "+".join(sorted(keys))
        nu, dk = _band(0.42, 0.48, tag, "set-new-share"), _band(0.83, 0.87, tag, "set-new-decay")
        people = vals[0] + sum(v * nu * (dk ** i) for i, v in enumerate(vals[1:]))
        sample = _messy(people / PANEL_TO_US, tag, "sample")
        return {"viewers": int(round(sample * PANEL_TO_US)), "sample": int(sample), "shows": len(keys)}
    except Exception:
        return None


def latest_month_window(title=None):
    """(start, end) of the newest month on record (for the title when given)."""
    doc = load()
    if not doc:
        return None
    ms = _months(doc)
    if title is not None:
        key = match(title, doc)
        ms = [m for m in ms if key and _rec(doc, key, m)]
    if not ms:
        return None
    y, m = int(ms[-1][:4]), int(ms[-1][5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def daily_views(title, day):
    """An anchored show's daily account-views on Peacock for one day (the
    rankers' unit: one account that played the title that day, once), or
    None. A month's starts become account-days at the low end (a library
    binge is several starts in one sitting), then the weekday shape and
    a salt so the rail moves and no two days repeat."""
    try:
        doc = load()
        key = match(title, doc)
        d = _as_date(day)
        if not key or not d:
            return None
        ym = _ym(d)
        mo = _month(doc, key, ym)
        if mo is not None:
            cur = mo["S"] * mo["share"]
            lib = mo["S"] - cur
        elif _observed_only(doc, key):
            return None
        else:
            am = _anchor_month(doc, key, ym)
            rec = _rec(doc, key, am)
            starts = float(rec.get("starts") or 0)
            cur = starts * _season_share(rec)
            lib = starts - cur
            if am != ym:
                m, a = int(ym[5:7]), int(am[5:7])
                cur *= SEASON_ON_AIR[m] / SEASON_ON_AIR[a] * _drift(ym, am)
                lib *= LIBRARY[m] / LIBRARY[a] * _drift(ym, am)
        account_days = cur / _band(1.50, 1.70, key, "starts-per-day-on-air") + lib / _band(2.60, 3.00, key, "starts-per-day-library")
        per_day = account_days / calendar.monthrange(d.year, d.month)[1]
        per_day *= _DOW[d.weekday()] * (1.0 + _band(-0.03, 0.03, key, d.isoformat(), "day"))
        return max(1, int(round(per_day)))
    except Exception:
        return None


def episode_viewers(title, days: int = 7):
    """A typical new episode's audience on Peacock in its first `days`
    days, for an anchored show whose season is on air: {'people', 'low',
    'high'} in US viewers, or None (a library show has no first week)."""
    try:
        doc = load()
        key = match(title, doc)
        if not key:
            return None
        ms = [m for m in _months(doc) if (_rec(doc, key, m) or {}).get("in_season")]
        if not ms:
            return None
        rec = _rec(doc, key, ms[-1])
        eps = sorted(int(x) for x in (rec.get("in_season_episode_accounts") or []) if int(x or 0) > 0)
        if not eps:
            return None
        mid = eps[len(eps) // 2] if len(eps) % 2 else (eps[len(eps) // 2 - 1] + eps[len(eps) // 2]) / 2.0
        wk = _band(0.70, 0.78, key, "first-week") * min(1.0, (max(1, int(days)) / 7.0) ** 0.5)
        ppa = _ppa(rec, key)
        people = mid * wk * ppa
        return {"people": _messy(people, key, "ep"), "low": _messy(eps[0] * wk * ppa, key, "ep-low"),
                "high": _messy(eps[-1] * wk * ppa, key, "ep-high")}
    except Exception:
        return None


# ------------------------------------------------- titles outside the record
_KIND_RXES = (
    ("sports", re.compile(r"\b(?:nfl|nba|mlb|nhl|wwe|premier league|olympics?|sunday night football|golf|nascar|big ten|football|basketball|soccer|live sports?)\b", re.I)),
    ("news", re.compile(r"\b(?:news|msnbc|nbc nightly|today show|meet the press)\b", re.I)),
    # a live special is never adjusted, whatever else its genre says ("a comedy awards special")
    ("event", re.compile(r"\b(?:awards?|awards? shows?|ceremon(?:y|ies)|pageants?|telethons?|parades?|concerts?|galas?|"
                         r"red carpet|live (?:special|event|show|broadcast)s?|special events?)\b", re.I)),
    ("kids", re.compile(r"\b(?:kids|children|preschool|animated family|family animation)\b", re.I)),
    ("film", re.compile(r"\b(?:film|movie|feature)\b", re.I)),
    ("reality", re.compile(r"\b(?:reality|unscripted|competition|dating|housewives|docu[- ]?series|true crime doc)\b", re.I)),
    ("comedy", re.compile(r"\b(?:sitcom|comedy)\b", re.I)),
    ("procedural", re.compile(r"\b(?:procedural|crime drama|police|legal drama|medical drama)\b", re.I)),
    ("drama", re.compile(r"\b(?:drama|thriller|mystery)\b", re.I)),
    ("series", re.compile(r"\b(?:series|show|season)\b", re.I)),
)


def kind_of(*hints) -> str:
    """The kind a title is, from whatever the caller knows (a genre, a
    category, a medium). 'series' when nothing says more."""
    text = " ".join(str(h or "") for h in hints)
    for kind, rx in _KIND_RXES:
        if rx.search(text):
            return kind
    return "series"


def trust(kind="series", *, original=False, live=False) -> float:
    """How far the correction learned on the anchored shows is trusted
    for a title of this kind (0 = not at all)."""
    if live:
        return 0.0
    t = TRUST.get(str(kind or "series").lower(), TRUST["series"])
    return min(t, TRUST["original"]) if original and t > 0 else t


def _class_factor(doc, surface, on_air):
    obs = [o for o in (doc.get("observations") or []) if isinstance(o, dict) and o.get("surface") == surface
           and float(o.get("ours") or 0) > 0 and float(o.get("implied") or 0) > 0]
    if not obs:
        return None

    def gm(rows):
        return math.exp(sum(math.log(float(o["implied"]) / float(o["ours"])) for o in rows) / len(rows)) if rows else None
    cur, lib = gm([o for o in obs if o.get("on_air")]), gm([o for o in obs if not o.get("on_air")])
    if on_air is True:
        return cur or lib
    if on_air is False:
        return lib or cur
    both = [f for f in (cur, lib) if f]
    return math.exp(sum(math.log(f) for f in both) / len(both))


def platform_factor(title, surface="window_viewers", *, kind="series", on_air=None, original=False, live=False) -> float:
    """The correction for a Peacock title the record does not name: what
    the anchored shows taught on this surface, raised to the trust the
    title's kind earns, half that when it would lift the figure, salted
    per title. 1.0 when nothing was learned or nothing applies."""
    try:
        doc = load()
        if not doc or match(title, doc):
            return 1.0
        w = trust(kind, original=original, live=live)
        f = _class_factor(doc, surface, on_air)
        if not f or w <= 0:
            return 1.0
        if f > 1.0:
            w *= 0.5
        out = (f ** w) * (1.0 + _band(-0.025, 0.025, title_key(title), surface, "factor"))
        out = max(FACTOR_MIN, min(FACTOR_MAX, out))
        return 1.0 if abs(out - 1.0) < 0.004 else out
    except Exception:
        return 1.0


def calibrate(title, services, start, end, drafted_people=0, *, surface="window_viewers", kind="series",
              on_air=None, original=False, live=False):
    """The one call for a sizing path. None leaves the caller's figure
    as it was (not Peacock alone, no record, nothing to change). Else
    {'people', 'sample', 'basis', 'factor', 'plays'}: an anchored show
    takes its own figure for the window ('anchor'); any other Peacock
    title takes its drafted figure times the learned correction
    ('factor'). Never raises."""
    try:
        if not only_peacock(services) or not load():
            return None
        v = viewers(title, start, end)
        if v:
            return {"people": v["viewers"], "sample": v["sample"], "plays": v["plays"], "basis": "anchor", "factor": None}
        drafted = int(drafted_people or 0)
        if drafted <= 0:
            return None
        f = platform_factor(title, surface, kind=kind, on_air=on_air, original=original, live=live)
        people = drafted * f
        cap = ceiling(kind, start, end, live=live)
        if cap and people > cap:
            people = cap * (1.0 - _band(0.03, 0.10, title_key(title), "under-scale"))
            f = people / float(drafted)
        if abs(f - 1.0) < 0.004:
            return None
        sample = _messy(people / PANEL_TO_US, title_key(title), "sample")
        return {"people": int(round(sample * PANEL_TO_US)), "sample": int(sample), "plays": None, "basis": "factor", "factor": round(f, 4)}
    except Exception:
        return None


_ON_AIR_WORDS = ("weekly", "daily", "airing", "on air", "on-air", "in season", "premiering")
_LIBRARY_WORDS = ("library", "catalog", "catalogue", "archive", "ended", "binge library")


def title_type(*hints, cadence=None, on_air=None, live=None) -> dict:
    """The type of a Peacock title from whatever a caller knows (a genre,
    a category, a medium, how its episodes arrive): {'kind', 'on_air',
    'live'}. on_air is True for a season airing week to week, False for
    a library title, None when the caller cannot say (the correction then
    reads both). A live special, sport or news reads as live."""
    kind = kind_of(*hints)
    oa = on_air
    if oa is None and cadence is not None:
        c = str(cadence or "").strip().lower()
        if c in _ON_AIR_WORDS:
            oa = True
        elif c in _LIBRARY_WORDS:
            oa = False
    lv = bool(live) or kind in ("sports", "news", "event", "live")
    return {"kind": kind, "on_air": oa, "live": lv}


def title_figure(title, start, end, drafted_people=0, *, kind="series", on_air=None, live=False, original=False):
    """The one correction for a Peacock title figure, on every surface
    (Jenna 2026-11-24: "cover every Peacock title figure"). A show the
    record names reads its own figure for the window; any other Peacock
    title reads its drafted figure times the correction its type of title
    earns (a season on air, a library title, both when the caller cannot
    say), at the low end: a lift is half trusted, and a figure above the
    largest anchored show's scale for the window is held under it. A live
    special, sport or news is never adjusted. The read lane's correction:
    journeys, profile sizing, the matrix read, Digital Twins, Pilot
    Testing and the partner API call this, never a copy. None leaves the
    caller's figure as it is (no record, nothing to change). Never
    raises. {'people', 'sample', 'basis', 'factor', 'plays'} otherwise."""
    return calibrate(title, ["peacock"], start, end, drafted_people, surface="window_viewers",
                     kind=kind, on_air=on_air, original=original, live=live)


def ceiling(kind, start, end, *, live=False):
    """The most a Peacock title of this kind is read at across a window
    without evidence of its own: the largest anchored show's figure for
    the same window times the kind's headroom. None for live sport,
    events and news, and when there is no record."""
    try:
        k = str(kind or "series").lower()
        if live or k not in HEADROOM:
            return None
        doc = load()
        a, b = _as_date(start), _as_date(end)
        if not doc or not a or not b or b < a:
            return None
        keys = set()
        for ym in _months(doc):
            keys.update((doc["months"][ym].get("shows") or {}).keys())
        top = max((_window(doc, key, a, b)[0] for key in keys), default=0.0)
        return top * HEADROOM[k] if top > 0 else None
    except Exception:
        return None


def component(title, services, start, end):
    """The Peacock part of a scope that names Peacock among other
    services, for an anchored show: the scope is at least this large.
    None otherwise."""
    try:
        if not includes_peacock(services) or only_peacock(services):
            return None
        v = viewers(title, start, end)
        return int(v["viewers"]) if v else None
    except Exception:
        return None


# ------------------------------------------------------- the prompt's block
_WINDOW_WORDS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ACCOUNTS_ASK_RX = re.compile(r"\b(?:unique\s+)?accounts?\b", re.I)
_PEACOCK_RX = re.compile(r"\bpeacock\b", re.I)


def _fmt_day(d) -> str:
    return f"{_WINDOW_WORDS[d.month - 1]} {d.day} {d.year}"


def titles_named(text) -> list:
    """The anchored shows a text names (longest alias first, so 'Law &
    Order: SVU' is never also read as 'Law & Order')."""
    doc = load()
    if not doc:
        return []
    hay = " " + re.sub(r"\band\b", " ", _norm(text)) + " "
    hay = re.sub(r"\s+", " ", hay)
    found, taken = [], []
    for alias, key in sorted(_alias_index(doc).items(), key=lambda kv: -len(kv[0])):
        if len(alias) < 3:
            continue
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", hay):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            if key not in found:
                found.append(key)
    return found


def accounts_ask(text) -> bool:
    """A question that asks for a title's ACCOUNTS on Peacock."""
    t = str(text or "")
    return bool(_PEACOCK_RX.search(t) and _ACCOUNTS_ASK_RX.search(t))


VIEWERS_NOT_ACCOUNTS = ("Crosswalk counts a title's audience on a service in viewers (people), each counted once. "
                        "It does not state a title's account count on a service to anyone. Answer in viewers and say "
                        "in one plain sentence that the count is people, not accounts.")


def binding_block(text, windows=None) -> str:
    """Lines for a reasoned read that is on Peacock: the figures on file
    for each anchored show the question names (viewers and plays, ours),
    for the windows handed in and for the trailing twelve months, and
    the scale every other Peacock title is read against. '' when the
    text is not about Peacock or there is no record. The block carries
    no account figure and no word about where a figure came from."""
    try:
        if not _PEACOCK_RX.search(str(text or "")):
            return ""
        doc = load()
        if not doc:
            return ""
        today = datetime.now(timezone.utc).date()
        wins = []
        for w in (windows or []):
            a, b = (_as_date(w[0]), _as_date(w[1])) if isinstance(w, (list, tuple)) and len(w) == 2 else (None, None)
            if a and b and b >= a and (a, b) not in wins:
                wins.append((a, b))
        try:
            year = (today.replace(year=today.year - 1) + timedelta(days=1), today)
        except ValueError:
            year = (today - timedelta(days=364), today)
        if year not in wins:
            wins.append(year)
        lines = []
        named = titles_named(text)
        for key in named:
            name = display_title(key) or key
            lm = latest_month_window(key)
            for a, b in ([lm] if lm else []) + wins:
                v = viewers(key, a, b)
                if v:
                    lines.append(f"- {name} on Peacock, {_fmt_day(a)} to {_fmt_day(b)}: {v['viewers']:,} US viewers "
                                 f"(people, each once, one second or more); {v['plays']:,} plays.")
        if len(named) >= 2:
            for a, b in wins:
                c = combined(named, a, b)
                if c:
                    lines.append(f"- These {c['shows']} series counted together on Peacock, {_fmt_day(a)} to {_fmt_day(b)}: "
                                 f"{c['viewers']:,} US viewers (each person once across them; their audiences overlap heavily).")
        scale = scale_lines()
        if not lines and not scale:
            return ""
        out = ["PEACOCK TITLE FIGURES (on file, binding; these take precedence over any earlier figure for the same "
               "title on Peacock, and a figure for another window is consistent with them: a shorter window sits "
               "below a longer one, a 30 second view sits a few percent under the one second view)"]
        out += lines
        if scale:
            out.append("PEACOCK SCALE (binding for any Peacock title with no line above): " + " ".join(scale))
        if accounts_ask(text):
            out.append(VIEWERS_NOT_ACCOUNTS)
        return "\n".join(out)
    except Exception:
        return ""


def scale_lines() -> list:
    """Plain sentences on how large a series is on Peacock and how hard
    its viewers play it, from the shows on file, in viewers: what any
    other Peacock title is read against. The library shows on file are
    second-tier library titles, and the lines say so, so the service's
    most played library series is never read down to their level."""
    try:
        doc = load()
        if not doc:
            return []
        ms = _months(doc)
        ym = ms[-1]
        y, m = int(ym[:4]), int(ym[5:7])
        a, b = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        try:
            ya = b.replace(year=b.year - 1) + timedelta(days=1)
        except ValueError:
            ya = b - timedelta(days=364)
        cur, lib, per_m, per_y = [], [], [], []
        for key in (doc["months"][ym].get("shows") or {}):
            mv, yv = viewers(key, a, b), viewers(key, ya, b)
            if mv and yv:
                (cur if _on_air(doc, key) else lib).append((mv["viewers"], yv["viewers"]))
                per_m.append(mv["plays"] / float(mv["viewers"]))
                per_y.append(yv["plays"] / float(yv["viewers"]))
        out = []
        if cur:
            cur.sort()
            out.append(f"The largest long-running drama on Peacock, with a season on air, draws about {cur[-1][0]:,} US "
                       f"viewers in its peak month and about {cur[-1][1]:,} across twelve months; a typical network drama "
                       f"with a season on air about {cur[len(cur) // 2][0]:,} in a month and {cur[len(cur) // 2][1]:,} "
                       f"across twelve.")
        if lib:
            lib.sort()
            out.append(f"A second-tier library series with no season on air draws about {lib[len(lib) // 2][0]:,} US viewers "
                       f"in a month and about {lib[len(lib) // 2][1]:,} across twelve months. The service's most played "
                       f"library series sit well above that, between it and the largest drama with a season on air.")
        if per_m and per_y:
            out.append(f"Peacock series viewers play hard: a viewer plays about {int(round(min(per_m)))} to "
                       f"{int(round(max(per_m)))} episodes of a series in a month and about {int(round(min(per_y)))} to "
                       f"{int(round(max(per_y)))} across twelve months. A series' viewers are its total play divided by "
                       f"play per viewer at that level, never by a handful of plays each.")
        if out:
            out.append("A series figure on Peacock is read against this scale, and one that sits above the largest of "
                       "these needs clear evidence of its own; live events and sport are outside it, and films, reality "
                       "and kids titles are read on their own evidence with this scale as the sense check.")
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- the net
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_NUM_RX = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,])|(?<![\d.,])\d{6,}(?![\d,])")
_SOURCE_RXES = (
    re.compile(r"\b(?:usage|performance)\s+report\b", re.I),
    re.compile(r"\bcontent\s+starts\b", re.I),
    # a figure put at the client's door: "the client's report", "the first-party actuals". Our own first-party
    # data is the house voice, never a source at the door: "our first-party data", "Crosswalk's own first-party
    # data" (guards.PROVENANCE_REPLY) stay (stage 4 template check, 2026-11-20)
    re.compile(r"(?<!\bour )(?<!\bour own )(?<!crosswalk )(?<!crosswalk's )(?<!crosswalk's own )(?<!crosswalk\u2019s )(?<!crosswalk\u2019s own )"
               r"\b(?:first[- ]party|client(?:'s)?|studio(?:'s)?|producer(?:'s)?)\s+(?:usage\s+|performance\s+)?(?:actuals?|report|figures?|numbers?|file|workbook|data)\b", re.I),
    re.compile(r"\b(?:peacock|nbcuniversal|nbcu|nbc universal|comcast|wolf entertainment|wolf films|universal television)(?:'s|’s)?\s+"
               r"(?:own\s+|internal\s+|reported\s+|official\s+|first[- ]party\s+)*(?:actuals?|figures?|numbers?|counts?|data|dashboard|"
               r"report(?:s|ed|ing)?|says|said|shared|provided|sent|told|shows?\s+that)\b", re.I),
    re.compile(r"\b(?:according to|per|reported by|provided by|supplied by|from)\s+(?:peacock|nbcuniversal|nbcu|wolf entertainment)(?:'s|’s)?\b"
               r"(?=[^.]{0,60}\b(?:figures?|numbers?|data|report|counts?|accounts?|starts?)\b)", re.I),
    re.compile(r"\bpeacock\b[^.\n]{0,90}\b(?:calibrat\w+|benchmark\w+|anchored\s+to|matched\s+to|reconciled\s+(?:to|with|against)|trued\s+up)\b", re.I),
    re.compile(r"\b(?:calibrat\w+|benchmark\w+|anchored|matched|reconciled|trued\s+up)\s+(?:to|against|with)\b[^.\n]{0,60}\bpeacock\b", re.I),
)
_ACCOUNT_COUNT_RX = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:million|m|k)\b)\s+(?:us\s+|unique\s+|peacock\s+|distinct\s+)*accounts?\b"
    r"(?!\s+(?:signed|sign|that\s+signed|created|opened|reactivat|cancel|churn))"
    r"|\b(?:unique|distinct)\s+accounts?\b[^.\n]{0,40}(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:million|m|k)\b)", re.I)


def record_figures() -> frozenset:
    """Every large figure the record carries (accounts and starts), as
    integers: the set an outgoing text is checked against."""
    doc = load()
    if not doc:
        return frozenset()
    if "figures" not in _INDEX:
        figs = set()

        def add(v):
            try:
                n = int(round(float(v)))
            except (TypeError, ValueError):
                return
            if n >= 100_000:
                figs.add(n)
        for ym in _months(doc):
            mo = doc["months"][ym]
            for v in (mo.get("slate") or {}).values():
                add(v)
            for rec in (mo.get("shows") or {}).values():
                for k in ("accounts_top_episode", "accounts_other_season_top", "accounts_avg_episode", "starts", "downloads"):
                    add(rec.get(k))
                for v in rec.get("in_season_episode_accounts") or []:
                    add(v)
                for v in rec.get("in_season_episode_starts") or []:
                    add(v)
                for s in rec.get("seasons") or []:
                    if isinstance(s, dict):
                        for k in ("accounts_avg", "accounts_max", "starts"):
                            add(s.get(k))
                for row in rec.get("episodes") or []:
                    if isinstance(row, (list, tuple)):
                        for v in row[2:4]:
                            add(v)
        for rec in _deck_shows(doc).values():
            for d in ((rec or {}).get("months") or {}).values():
                if isinstance(d, dict):
                    for k in ("all", "current", "library"):
                        add(d.get(k))
        _INDEX["figures"] = frozenset(figs)
    return _INDEX["figures"]


def _allowlisted() -> tuple:
    try:
        from prometheus import methodology as _m
        return tuple(_m.OTHERS_ALLOWLIST)
    except Exception:
        return ()


def _about_peacock(text) -> bool:
    """A text that is about Peacock or names a show on the record: the
    only place a figure from the record could be."""
    s = str(text or "")
    return bool(_PEACOCK_RX.search(s)) or bool(titles_named(s))


def leak_hits(text, about=None) -> list:
    """What in a text must not go out: a figure from the record, a
    sentence that puts a figure at the service's or the client's door,
    a title's account count. [] when it is clean. A figure counts only
    in a text that is about Peacock or a show on the record (`about`
    carries that from the whole text down to one sentence), so an equal
    number in an unrelated read is never touched. The two definition
    sentences the views methodology carries are not hits."""
    s = str(text or "")
    if not s:
        return []
    for keep in _allowlisted():
        s = s.replace(keep, " ")
    hits = []
    for rx in _SOURCE_RXES:
        m = rx.search(s)
        if m:
            hits.append(("source", m.group(0)))
    figs = record_figures()
    if not figs:
        return hits
    found = []
    for m in _NUM_RX.finditer(s):
        try:
            if int(m.group(0).replace(",", "")) in figs:
                found.append(("figure", m.group(0)))
        except ValueError:
            pass
    acct = _ACCOUNT_COUNT_RX.search(s)
    if found or acct:
        if about is None:
            about = _about_peacock(s)
        if about:
            hits.extend(found)
            if acct and (about == "show" or titles_named(s)):
                # a title's account count on the service is never stated
                hits.append(("accounts", acct.group(0)))
    return hits


def scrub(text):
    """The net under every outgoing text: a sentence with a hit is
    dropped, the rest ships. Text with no hit comes back untouched
    (byte for byte). Never raises; never empties a text (when every
    sentence would go, the figures are cut out of the sentences in
    place)."""
    try:
        s = str(text or "")
        if not s or not leak_hits(s):
            return text
        about = ("show" if titles_named(s) else True) if _about_peacock(s) else False
        keep_tokens = []
        for i, keep in enumerate(_allowlisted()):
            if keep in s:
                tok = f"xqpkdefq{i}qx"
                s = s.replace(keep, tok)
                keep_tokens.append((tok, keep))
        pieces = re.split(r"(\n+)", s)
        out = []
        for piece in pieces:
            if not piece.strip():
                out.append(piece)
                continue
            sents = re.split(r"(?<=[.!?])\s+", piece)
            kept = [x for x in sents if not leak_hits(x, about)]
            out.append(" ".join(kept))
        res = "".join(out)
        res = re.sub(r"\n{3,}", "\n\n", res).strip()
        if not res:
            res = s
            for _kind, frag in leak_hits(s, about):
                res = res.replace(frag, "")
            res = re.sub(r"[ \t]{2,}", " ", res).strip()
        for tok, keep in keep_tokens:
            res = res.replace(tok, keep)
        return res
    except Exception:
        return text


# ------------------------------------------------------------ for the record
def observation(surface, title, ours, *, start=None, end=None, day=None, note=""):
    """One pair for the ledger: what our earlier method said for an
    anchored show against what the record implies, on one surface
    ('window_viewers': people in a window; 'daily_views': the rankers'
    daily account-views). None when the record cannot answer."""
    doc = load()
    key = match(title, doc)
    if not key or float(ours or 0) <= 0:
        return None
    if surface == "daily_views":
        implied = daily_views(key, day)
        when = {"day": str(day)[:10]}
    else:
        v = viewers(key, start, end)
        implied = v["viewers"] if v else None
        when = {"start": str(start)[:10], "end": str(end)[:10]}
    if not implied:
        return None
    return dict({"surface": surface, "title": display_title(key), "ours": int(ours), "implied": int(implied),
                 "on_air": bool(_on_air(doc, key)), "note": str(note)[:120]}, **when)


__all__ = ["BUCKET", "KEY", "load", "set_cached", "available", "title_key", "is_peacock", "only_peacock", "includes_peacock",
           "match", "anchored", "display_title", "viewers", "combined", "latest_month_window", "daily_views", "episode_viewers", "kind_of",
           "trust", "platform_factor", "calibrate", "title_type", "title_figure", "ceiling", "component", "titles_named", "accounts_ask",
           "binding_block", "scale_lines",
           "record_figures", "leak_hits", "scrub", "observation", "VIEWERS_NOT_ACCOUNTS"]
