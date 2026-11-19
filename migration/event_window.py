"""Event-scoped date windows for profile builds and cuts.

2026-08-24 (Jenna, Rosie O'Donnell / Jimmy Kimmel Live defect): a cut
request tied to a real-world event or stint ("viewers of Rosie
ODonnell's week hosting") must carry the event's actual calendar dates,
not the default trailing-12-month window and not the parent file's
window. The interpret stage resolves the dates up front (model
knowledge, confirmed with the user when uncertain); when a spec still
reaches the engine host with an unresolved `event_window_query`, the
worker resolves it here with the same web-search Claude pattern the
refresh clamp uses, then stamps the final window onto the cut's
SAMPLE SIZE row.

Twin-sync note: this module lives in BOTH the parent repo
(`migration/event_window.py`, imported by the engine-host worker) and
`bg-webapp/migration/event_window.py` (imported by app.py on the web
tier for detection + label formatting only). Keep the two byte-equal.
"""
from __future__ import annotations

import calendar
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone

# Retired 2026-09-14: the 2026-08-17 frozen fiscal pair. Kept so
# is_default_window still treats leftover Claude drafts as "the
# default" (not a custom event window) and so old imports compile.
# New code should call default_window().
LEGACY_DEFAULT_START = "2025-07-01"
LEGACY_DEFAULT_END = "2026-06-30"
DEFAULT_WINDOW_START = LEGACY_DEFAULT_START
DEFAULT_WINDOW_END = LEGACY_DEFAULT_END

# Phrases that tie an audience window to a real-world event or stint.
# Conservative on purpose: a false positive turns into a clarify
# question or a no-op, but the list should still read like the ways
# people actually scope these asks.
_EVENT_PHRASES = [
    r"guest[\s-]*host(?:ing|ed|s)?",
    r"(?:week|night|day|days|dates?)\s+(?:that\s+)?(?:she|he|they|\w+)\s+(?:guest[\s-]*)?hosted",
    r"(?:her|his|their)\s+(?:week|night|days?|stint|run)\s+hosting",
    r"week\s+hosting",
    r"hosting\s+week",
    r"opening\s+weekend",
    r"premiere\s+(?:week(?:end)?|night)",
    r"debut\s+week(?:end)?",
    r"finale\s+(?:week(?:end)?|night)",
    r"(?:season|series)\s+finale",
    r"during\s+the\s+finale",
    r"residency",
    r"playoff\s+run",
    r"during\s+the\s+playoffs",
    r"championship\s+(?:run|week(?:end)?)",
    r"title\s+run",
    r"election\s+night",
    r"award[s]?\s+(?:night|show|week(?:end)?)",
    r"(?:super\s+bowl|world\s+series|olympics)\s+(?:week(?:end)?|night)",
    r"during\s+the\s+(?:super\s+bowl|world\s+series|olympics)",
    r"(?:her|his|their)\s+stint",
    r"the\s+(?:week|night|days?)\s+of\s+the\s+",
    r"farewell\s+(?:show|tour|week)",
    r"launch\s+week(?:end)?",
]
_EVENT_RE = re.compile("|".join(f"(?:{p})" for p in _EVENT_PHRASES),
                       re.IGNORECASE)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def detect_event_scoped_text(text):
    """Return the first event-scoped phrase found in `text`, else ''."""
    if not text:
        return ""
    m = _EVENT_RE.search(str(text))
    return m.group(0).strip() if m else ""


def parse_iso_date(value):
    """'2026-08-17' -> datetime.date, else None. Strict ISO only."""
    s = str(value or "").strip()
    if not _ISO_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def window_string(start, end):
    """ISO pair -> the engine's SAMPLE SIZE window form
    '2026-08-17 TO 2026-08-20', or '' when the pair is invalid."""
    d1, d2 = parse_iso_date(start), parse_iso_date(end)
    if not d1 or not d2 or d1 > d2:
        return ""
    return f"{d1.isoformat()} TO {d2.isoformat()}"


def parse_window_string(value):
    """'2026-08-17 TO 2026-08-20' -> ('2026-08-17', '2026-08-20'),
    else None. Case-insensitive on the TO."""
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s+TO\s+(\d{4}-\d{2}-\d{2})\s*$",
                 str(value or ""), re.IGNORECASE)
    if not m:
        return None
    if not window_string(m.group(1), m.group(2)):
        return None
    return m.group(1), m.group(2)


def format_window_label(start, end):
    """ISO pair -> plain-language label: 'Aug 17 to Aug 20, 2026',
    'Jul 1, 2025 to Jun 30, 2026' across years. '' when invalid."""
    d1, d2 = parse_iso_date(start), parse_iso_date(end)
    if not d1 or not d2 or d1 > d2:
        return ""
    if d1.year == d2.year:
        if d1 == d2:
            return f"{_MONTHS[d1.month - 1]} {d1.day}, {d1.year}"
        return (f"{_MONTHS[d1.month - 1]} {d1.day} to "
                f"{_MONTHS[d2.month - 1]} {d2.day}, {d2.year}")
    return (f"{_MONTHS[d1.month - 1]} {d1.day}, {d1.year} to "
            f"{_MONTHS[d2.month - 1]} {d2.day}, {d2.year}")


def default_window(today=None):
    """Standing default: trailing 12 calendar months ending today UTC.

    Jenna 2026-09-14: the confirm prompt was still offering the
    retired Jul 1 2025 to Jun 30 2026 fiscal pair as "the last 12
    months". The default is the rolling trailing year, not that
    frozen window.
    """
    rel = resolve_relative_window("trailing 12 months", today=today)
    if rel:
        return rel[0], rel[1]
    return LEGACY_DEFAULT_START, LEGACY_DEFAULT_END


def default_window_string(today=None):
    """ISO pair as the engine SAMPLE SIZE form 'START TO END'."""
    start, end = default_window(today)
    return f"{start} TO {end}"


def is_default_window(start, end, today=None):
    """True for the current trailing-12 pair OR the retired fiscal pair.

    The fiscal pair stays in the set so leftover interpret drafts
    that still emit Jul 2025-Jun 2026 are treated as 'no custom
    window' (event-scoped confident checks, etc.), not as an
    explicit event range.
    """
    s, e = str(start or "").strip(), str(end or "").strip()
    d1, d2 = default_window(today)
    return (s, e) in {
        (d1, d2),
        (LEGACY_DEFAULT_START, LEGACY_DEFAULT_END),
    }


# ---- Relative windows (2026-08-24, Florida/Iowa trailing-60-days
# defect). A request stating a relative window ("trailing 60 days",
# "last 90 days", "past 3 months") must BIND: concrete dates computed
# at interpret time relative to today, never the standing default.

_REL_NUM_RE = re.compile(
    r"\b(?:trailing|last|past|previous|prior)\s+(\d{1,3})\s*"
    r"(day|week|month|year)s?\b", re.IGNORECASE)
_REL_BARE_RE = re.compile(
    r"\b(?:last|past|previous|prior)\s+(week|month|year)\b",
    re.IGNORECASE)


def _shift_months(d, months_back):
    """Calendar-correct month subtraction (day clamped to month end)."""
    y, m = d.year, d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


def resolve_relative_window(text, today=None):
    """Parse a relative window phrase out of request text and compute
    concrete dates anchored to today. 'trailing 60 days' ->
    ('2026-06-25', '2026-08-24', 'trailing 60 days') when today is
    2026-08-24. Handles day/week/month/year units with an explicit
    count, plus bare 'last week' / 'past month' / 'last year'.
    Returns (start_iso, end_iso, matched_phrase) or None when the text
    states no relative window."""
    if not text:
        return None
    s = str(text)
    m = _REL_NUM_RE.search(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        phrase = m.group(0).strip()
    else:
        m2 = _REL_BARE_RE.search(s)
        if not m2:
            return None
        n = 1
        unit = m2.group(1).lower()
        phrase = m2.group(0).strip()
    if n <= 0 or n > 400:
        return None
    if today is None:
        today = datetime.now(timezone.utc).date()
    if unit == "day":
        start = today - timedelta(days=n)
    elif unit == "week":
        start = today - timedelta(days=7 * n)
    elif unit == "month":
        start = _shift_months(today, n)
    else:
        start = _shift_months(today, 12 * n)
    return start.isoformat(), today.isoformat(), phrase


def resolve_event_window_via_search(query, run_id=""):
    """Worker-side fallback: resolve an event's calendar dates with the
    same web-search Claude pattern as the refresh clamp
    (synth_queue_worker._refresh_window_research). Returns
    {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD', 'label': '<plain>'}
    or None on any failure. Never raises."""
    q = str(query or "").strip()
    if not q:
        return None
    try:
        from migration.claude_client import claude_messages
    except ImportError:
        try:
            from claude_client import claude_messages  # twin layout
        except ImportError:
            return None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system = (
        "You are verifying the exact calendar dates of a real-world "
        "event, stint, or run (a guest-hosting week, a finale, an "
        "opening weekend, a residency leg, a playoff run). Use the "
        "web_search tool and cross-check at least two sources. "
        "Respond with one JSON object and nothing else:\n"
        '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", '
        '"label": "<plain-language framing, e.g. her guest-host week>", '
        '"confident": true|false}\n'
        "start/end are the first and last calendar days of the event "
        "itself. If the event spans one day, start equals end. If you "
        "cannot pin the dates from real sources, set confident to "
        "false. Never invent dates. Plain hyphens only, never em "
        "dashes."
    )
    user = (f"Event to date-resolve: {q}\n"
            f"Today is {today}. If the event happened more than once, "
            f"use the most recent occurrence. JSON only.")
    model = (os.environ.get("CLAUDE_EVENT_WINDOW_MODEL")
             or "claude-sonnet-4-6")
    web_tool = {"type": "web_search_20260209", "name": "web_search",
                "max_uses": 6}
    web_tool_legacy = {"type": "web_search_20250305", "name": "web_search",
                       "max_uses": 6}
    try:
        raw = claude_messages(system=system, user=user, model=model,
                              max_tokens=900, temperature=0.1,
                              tools=[web_tool])
        if not raw:
            print(f"[{run_id}] event window research: retrying with "
                  f"legacy web-search descriptor")
            raw = claude_messages(system=system, user=user,
                                  model="claude-sonnet-4-6",
                                  max_tokens=900, temperature=0.1,
                                  tools=[web_tool_legacy])
    except Exception as e:
        print(f"[{run_id}] event window research raised: {e}")
        return None
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(cleaned[i:j + 1])
    except Exception:
        return None
    if data.get("confident") is False:
        print(f"[{run_id}] event window research not confident for "
              f"{q!r}; leaving window unresolved")
        return None
    start = str(data.get("start") or "").strip()
    end = str(data.get("end") or "").strip()
    if not window_string(start, end):
        return None
    label = " ".join(str(data.get("label") or "").split())[:120]
    label = label.replace("\u2014", "-").replace("\u2013", "-")
    return {"start": start, "end": end, "label": label}


def ensure_event_window_resolved(spec, run_id=""):
    """Engine-host pre-build hook. When the spec carries an
    `event_window_query` but no resolved window (neither `date_range`
    nor `cut_date_range`), resolve via web search and write the result
    back onto the spec:
      * spec['date_range']     = 'START TO END'  (fresh builds)
      * spec['cut_date_range'] = 'START TO END'  (derived cuts)
      * spec['cut_window_label'] / spec['date_window_label'] = plain label
    Mutates spec in place; returns the resolved dict or None. Never
    raises - an unresolved event window falls back to existing
    behavior (default window / parent window)."""
    try:
        query = str(spec.get("event_window_query") or "").strip()
        if not query:
            return None
        has_engine_window = bool(parse_window_string(spec.get("date_range")))
        has_cut_window = bool(parse_window_string(spec.get("cut_date_range")))
        if has_engine_window or has_cut_window:
            return None  # already resolved upstream
        resolved = resolve_event_window_via_search(query, run_id=run_id)
        if not resolved:
            return None
        ws = window_string(resolved["start"], resolved["end"])
        lbl = format_window_label(resolved["start"], resolved["end"])
        if resolved.get("label"):
            lbl = f"{lbl} ({resolved['label']})" if lbl else resolved["label"]
        spec["date_range"] = ws
        spec["cut_date_range"] = ws
        spec["date_window_label"] = lbl
        spec["cut_window_label"] = lbl
        print(f"[{run_id}] event window resolved via search: "
              f"{query!r} -> {ws} ({lbl})")
        return resolved
    except Exception as e:
        print(f"[{run_id}] ensure_event_window_resolved failed "
              f"(non-fatal): {e}")
        return None


def stamp_window_on_df(df, start, end):
    """Rewrite the SAMPLE SIZE row's window text on a profile DataFrame
    to 'SAMPLE SIZE (START TO END) | BEHAVIOR STUDY (START TO END)'.
    Returns the number of rows rewritten (0 when the pair is invalid or
    no SAMPLE SIZE row exists). Mutates df in place."""
    ws = window_string(start, end)
    if not ws:
        return 0
    n = 0
    for idx, row in df.iterrows():
        col = str(row.get("Column", "")).strip().upper()
        val = str(row.get("Value", ""))
        if col == "SAMPLE SIZE" and "SAMPLE SIZE (" in val.upper():
            df.at[idx, "Value"] = (f"SAMPLE SIZE ({ws}) | "
                                   f"BEHAVIOR STUDY ({ws})")
            n += 1
    return n


def stamp_window_on_cut_s3(s3_key, start, end, run_id="",
                           bucket="dashboard-inputs"):
    """Stamp an event/explicit window onto an already-uploaded cut CSV's
    SAMPLE SIZE row. Backs up the pre-stamp object to _backups/ first.
    Returns True when the stamp landed, False otherwise. Never raises -
    a failed stamp leaves the cut with its inherited window (the
    pre-2026-08-24 behavior)."""
    ws = window_string(start, end)
    if not ws or not s3_key:
        return False
    try:
        import boto3
        import pandas as pd
        s3 = boto3.client("s3", region_name="us-east-2")
        body = s3.get_object(Bucket=bucket, Key=s3_key)["Body"].read()
        df = pd.read_csv(io.BytesIO(body))
        n = stamp_window_on_df(df, start, end)
        if not n:
            print(f"[{run_id}] window stamp: no SAMPLE SIZE row found "
                  f"on {s3_key!r}; leaving file untouched")
            return False
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3.put_object(
            Bucket=bucket,
            Key=f"_backups/{s3_key}.pre_event_window_stamp_{ts}.csv",
            Body=body, ContentType="text/csv")
        out = io.StringIO()
        df.to_csv(out, index=False)
        s3.put_object(Bucket=bucket, Key=s3_key,
                      Body=out.getvalue().encode("utf-8"),
                      ContentType="text/csv")
        print(f"[{run_id}] window stamp: {s3_key!r} SAMPLE SIZE -> "
              f"({ws}) [{n} row(s)]")
        return True
    except Exception as e:
        print(f"[{run_id}] window stamp failed (non-fatal) on "
              f"{s3_key!r}: {e}")
        return False


# ---- The data floor (Jenna 2026-11-18, after Paula Vazquez at Dick
# Wolf Productions was handed 2020 figures): "It should always say no
# data can be pulled from before jan 1 2021." The date lives HERE and
# nowhere else. Every surface reads it from this module: the chat reads
# and their window parsing, the brief interpret / confirm / spec stamp,
# the key surface, Subscriber IQ trackers, journeys, valuations and
# their before windows, Digital Twins, the dashboard forms, and the
# engine host's own check before a build starts. A request that reaches
# before the floor is told so in plain words: never a silent clamp,
# never figures presented as covering earlier time, never a build run
# quietly on another window. Rule file: .cursor/rules/no-data-before-2021.mdc

DATA_FLOOR = "2021-01-01"

_MONTHS_LONG = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November",
                "December"]


def data_floor_date():
    """The floor as a datetime.date."""
    return datetime.strptime(DATA_FLOOR, "%Y-%m-%d").date()


def long_date_label(value):
    """'2021-12-14' (or a date) -> 'December 14, 2021'. '' when the
    value is not a date."""
    d = _floor_as_date(value)
    if d is None:
        return ""
    return f"{_MONTHS_LONG[d.month - 1]} {d.day}, {d.year}"


def data_floor_label():
    """'January 1, 2021'."""
    return long_date_label(DATA_FLOOR)


def data_floor_sentence():
    """The one sentence a reader is told, verbatim on every surface."""
    return f"No data can be pulled from before {data_floor_label()}."


def _floor_as_date(value):
    """A date from a date, a datetime, or text that opens with an ISO
    day ('2021-12-14', '2021-12-14T00:00:00'). None otherwise."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") \
            and hasattr(value, "day") and not isinstance(value, str):
        try:
            return datetime(value.year, value.month, value.day).date()
        except (TypeError, ValueError):
            return None
    return parse_iso_date(str(value).strip()[:10])


def floor_status(start, end=None):
    """Where a window sits against the floor.

    'ok'        it starts on or after the floor
    'before'    all of it ends before the floor
    'straddle'  it starts before the floor and ends on or after it (an
                open end, with no end date given, reads this way too)
    ''          the start is not a date (nothing to judge)
    """
    s = _floor_as_date(start)
    if s is None:
        return ""
    floor = data_floor_date()
    if s >= floor:
        return "ok"
    e = _floor_as_date(end)
    if e is not None and e < floor:
        return "before"
    return "straddle"


def window_string_floor_status(value):
    """floor_status for the engine's 'START TO END' form, a {start,
    end} dict, or a two-item list. '' when no window can be read."""
    if isinstance(value, dict):
        return floor_status(value.get("start"), value.get("end"))
    if isinstance(value, (list, tuple)):
        vals = [v for v in value if str(v or "").strip()]
        if len(vals) >= 2:
            return floor_status(vals[0], vals[-1])
        return ""
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:TO|->|,|-)\s*"
                 r"(\d{4}-\d{2}-\d{2})\s*$", str(value or ""),
                 re.IGNORECASE)
    if not m:
        return ""
    return floor_status(m.group(1), m.group(2))


def clamp_to_floor(start, end):
    """(start_iso, end_iso, status). A straddling window comes back
    starting on the floor; every other window comes back as given.
    The caller owns saying so: a clamp is never silent."""
    status = floor_status(start, end)
    s, e = _floor_as_date(start), _floor_as_date(end)
    s_iso = s.isoformat() if s else str(start or "")
    e_iso = e.isoformat() if e else str(end or "")
    if status == "straddle":
        return DATA_FLOOR, e_iso, status
    return s_iso, e_iso, status


def floor_refusal_line(offer=True):
    """What a reader is told when the whole window is before the floor."""
    line = data_floor_sentence()
    if offer:
        line += (f" I can run the same question from {data_floor_label()} "
                 f"forward.")
    return line


def floor_window_line(end=None, noun="window"):
    """What a reader is told when the window crossed the floor and now
    starts on it: the sentence, then the dates actually used."""
    line = f"{data_floor_sentence()} The {noun} starts {data_floor_label()}"
    end_label = long_date_label(end)
    if end_label:
        line += f" and runs to {end_label}"
    return line + "."


_SPEC_WINDOW_KEYS = ("date_range", "cut_date_range", "engine_date_range")


def spec_floor_status(spec):
    """The worst floor status across the windows a build spec carries:
    'before' beats 'straddle' beats 'ok'; '' when it carries none.
    Reads date_range / cut_date_range / engine_date_range in any of
    their shapes, a {start, end} event_window, and a Subscriber IQ
    air_window."""
    if not isinstance(spec, dict):
        return ""
    seen = []
    for key in _SPEC_WINDOW_KEYS:
        if spec.get(key):
            seen.append(window_string_floor_status(spec.get(key)))
    ew = spec.get("event_window")
    if isinstance(ew, dict) and ew.get("start"):
        seen.append(floor_status(ew.get("start"), ew.get("end")))
    subiq = spec.get("subiq")
    if isinstance(subiq, dict) and isinstance(subiq.get("air_window"), dict):
        aw = subiq["air_window"]
        seen.append(floor_status(aw.get("start"),
                                 subiq.get("through_date") or aw.get("end")))
    for worst in ("before", "straddle", "ok"):
        if worst in seen:
            return worst
    return ""


class DataFloorRefusal(ValueError):
    """A build, tracker or read was asked for on a window that reaches
    before the data floor. Raised before anything is drawn; the text is
    the sentence the requester reads."""

    def __init__(self, detail=""):
        super().__init__(data_floor_sentence())
        self.detail = str(detail or "")
        self.client_message = data_floor_sentence()


def refuse_spec_before_floor(spec, run_id=""):
    """Engine-host check, run before a build starts and again after an
    event window is resolved. A spec whose window starts before the
    floor is refused whole (the web tier corrects a crossing window
    with the requester before approval, so one that still arrives here
    was never approved on real dates). Raises DataFloorRefusal; returns
    None when the spec is clear."""
    status = spec_floor_status(spec)
    if status in ("before", "straddle"):
        windows = {k: (spec or {}).get(k) for k in _SPEC_WINDOW_KEYS
                   if (spec or {}).get(k)}
        raise DataFloorRefusal(
            f"window {status} the {DATA_FLOOR} floor: {windows!r}"
            + (f" [{run_id}]" if run_id else ""))
    return None
