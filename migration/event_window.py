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

import io
import json
import os
import re
from datetime import datetime, timezone

DEFAULT_WINDOW_START = "2025-07-01"
DEFAULT_WINDOW_END = "2026-06-30"

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


def is_default_window(start, end):
    return (str(start or "").strip() == DEFAULT_WINDOW_START
            and str(end or "").strip() == DEFAULT_WINDOW_END)


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
