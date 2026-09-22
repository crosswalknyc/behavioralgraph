#!/usr/bin/env python3
"""Content-mapping ingest + one-click approval (Jenna 2026-09-21).

Sibling of ``migration/hostmap_ingest.py``. The hostmap gap email now
carries a secondary section, ``content mapping``, with the researched
content rows the build surfaced for its BRAND INPUT (viewer-scoped
titles, franchise films, consumption-scoped URLs). The email ships up
to four buttons:

    Approve all       -> ingests both hostmap CSV and content CSV
    Approve hostmap only -> hostmap side only; content stays pending
    Approve content only -> content side only; hostmap stays pending
    Reject            -> rejects whichever side(s) are still pending

Both sides ride the SAME approval id + SAME S3 state document, so the
tri-state buttons are a UI over one shared package (see
``migration/hostmap_ingest.persist_approval`` for the package builder).

This module owns:

1. ``parse_content_mapping_csv`` - parse a five-column CSV
   (SHOW,URL,PRODUCTION,PLATFORM,SEASON) into row dicts.
2. ``ingest_content_mapping_rows`` - insert into
   ``reference.content_mapping`` with the same dedupe semantics
   ``viewer_content_scope.insert_content_mapping_rows`` already uses:
   case + punctuation insensitive on (PLATFORM, URL) and on
   (SHOW, PLATFORM, SEASON, URL). Existing rows are never modified.
3. ``append_content_rows_to_sheet`` - append to the ``content map`` tab
   (gid=1321298530 on Jenna's approved sheet). Reuses the shared
   Google credentials + sheet helper from ``hostmap_ingest``.

Backward-compat guarantees:

- Rows already in ``reference.content_mapping`` are skipped, never
  reinserted, never overwritten. Rerunning the same approval CSV is a
  no-op (the CAS single-use ledger also blocks it upstream, this
  dedupe is the belt-and-suspenders backstop).
- Every call is fail-safe: connectivity failures return
  ``{"error": "..."}`` and never raise, so the endpoint page can render
  a "try again in a moment" and the S3 state records the failure.
- The sheet-append leg mirrors ``hostmap_ingest.append_rows_to_sheet``:
  graceful skip when credentials are unresolvable, never blocks the
  ClickHouse ingest.

TWIN NOTE: ``bg-webapp/migration/content_mapping_ingest.py`` must stay
byte-identical to this file. ``scripts/test_module_twin_sync.py`` (add
this file to the manifest when landing) enforces it.

Ingest safety mirrors the existing content-mapping insert in
``viewer_content_scope.py``. Standing rule: NEVER auto-modify or
delete existing content_mapping rows; only insert. If a future
correction is needed for a specific row, it goes through a separate
one-off script that emits a manual approval email.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re

# Reuse the hostmap ingest module for anything shared: ClickHouse
# helpers, secret loader, S3 client, approval store, and the Google
# Sheets append helper. Keeps the twin lightweight and the wiring
# consistent across both flows.
try:
    from migration import hostmap_ingest as _hi
    # Also reuse viewer_content_scope.norm_token for the DEDUPE FOLD so
    # this module and the in-line insert path in viewer_content_scope
    # score the exact same collisions. If they drift, dupes leak through
    # the ingest silently.
    from migration.viewer_content_scope import norm_token as _fold
except ImportError:  # engine-host / worker sys.path variant
    import hostmap_ingest as _hi  # type: ignore
    from viewer_content_scope import norm_token as _fold  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTENT_COLUMNS = ("SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON")
CH_TABLE = "reference.content_mapping"
# Column list on the target CH table. Matches viewer_content_scope's
# CONTENT_MAPPING_COLUMNS but scoped to what the approval flow inserts;
# CATEGORY + SUB_CATEGORY are always blank at insert time (they are
# taxonomy fields populated by a downstream enrichment pass).
CH_INSERT_COLUMNS = (
    "SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON",
    "CATEGORY", "SUB_CATEGORY",
)

# Content-map tab on the mapping sheet (Jenna 2026-09-21, verified
# against the native-Sheet workbook
# 1I5F5at4hJ92krhgYdew68pcwd7_gqpoVfWLAHKnLpzA). The tab is literally
# titled ``CONTENT_MAPPING`` at gid=1321298530 (index 1). The append
# helper targets by title with the gid as a robust fallback in case
# the title is ever renamed.
CONTENT_MAP_TAB_GID = 1321298530
CONTENT_MAP_TAB_TITLE = "CONTENT_MAPPING"


# ---------------------------------------------------------------------------
# Fold (dedupe key) - IMPORTED from viewer_content_scope
# ---------------------------------------------------------------------------
# The fold used to score duplicates against reference.content_mapping
# is the SAME function viewer_content_scope.insert_content_mapping_rows
# uses inline (``norm_token``: accent + case + punctuation insensitive,
# strips to uppercase alphanumerics). Sharing the fold guarantees the
# CSV-approval ingest and the in-line insert path score IDENTICAL
# collisions - a URL that would dedupe under the in-line path also
# dedupes here, so a proposed row already sitting in the table cannot
# leak through as a "new" insert.
def fold_token(s) -> str:
    """Delegates to viewer_content_scope.norm_token. Kept as a named
    export so ingest tests can assert the delegation is intact."""
    return _fold(s)


# ---------------------------------------------------------------------------
# CSV parse
# ---------------------------------------------------------------------------
def parse_content_mapping_csv(text) -> list:
    """Parse a content-mapping CSV into row dicts. Raises ValueError
    when the header does not carry the five content columns. Extra
    columns are tolerated and ignored (CATEGORY, SUB_CATEGORY defaults
    to blank at insert)."""
    reader = csv.DictReader(io.StringIO(str(text or "").lstrip("\ufeff")))
    fields = [f.strip().upper() for f in (reader.fieldnames or [])]
    missing = [c for c in CONTENT_COLUMNS if c not in fields]
    if missing:
        raise ValueError(f"content-mapping CSV is missing column(s): "
                         f"{missing}")
    rows = []
    for raw in reader:
        row = {k.strip().upper(): (v or "").strip()
               for k, v in raw.items() if k}
        if not row.get("SHOW") or not row.get("URL") \
                or not row.get("PLATFORM"):
            continue
        rows.append({c: row.get(c, "") for c in CONTENT_COLUMNS})
    return rows


def content_csv_text(rows) -> str:
    """Serialize content rows to the canonical CSV shape. Row dicts may
    use lowercase or uppercase keys interchangeably. Used by the email
    builder to attach the proposal file."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CONTENT_COLUMNS)
    for r in rows:
        w.writerow([
            r.get("SHOW") or r.get("show") or "",
            r.get("URL") or r.get("url") or "",
            r.get("PRODUCTION") or r.get("production") or "",
            r.get("PLATFORM") or r.get("platform") or "",
            r.get("SEASON") or r.get("season") or "",
        ])
    return buf.getvalue()


def content_csv_filename(subject_name) -> str:
    """Attachment name convention. Mirrors mapping_csv_filename in
    hostmap_gap_mapping - dated + subject-slugged."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H_%M")
    slug = re.sub(r"[^A-Za-z0-9]+", "_",
                  str(subject_name or "profile")).strip("_")[:60] \
        or "profile"
    return f"{slug}_content_map_proposals_{stamp}.csv"


# ---------------------------------------------------------------------------
# ClickHouse ingest
# ---------------------------------------------------------------------------
def load_existing_content_index() -> dict:
    """Return a dedupe index over the current content_mapping table:

        {(fold(PLATFORM), fold(URL)): (SHOW, URL, PLATFORM, SEASON),
         (fold(SHOW), fold(PLATFORM), fold(SEASON), fold(URL)): (...)}

    Presence in either key set counts as a duplicate. Same shape the
    in-line insert path uses via ``viewer_content_scope.norm_token``.
    """
    text = _hi.ch_query(
        f"SELECT SHOW, URL, PRODUCTION, PLATFORM, SEASON FROM "
        f"{CH_TABLE} FORMAT JSONEachRow", timeout=120)
    index: dict = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        show = str(obj.get("SHOW") or "")
        url = str(obj.get("URL") or "")
        plat = str(obj.get("PLATFORM") or "")
        season = str(obj.get("SEASON") or "")
        k2 = (fold_token(plat), fold_token(url))
        k4 = (fold_token(show), fold_token(plat),
              fold_token(season), fold_token(url))
        marker = (show, url, plat, season)
        index.setdefault(k2, marker)
        index.setdefault(k4, marker)
    return index


def ingest_content_mapping_rows(rows, dry_run: bool = False,
                                existing_index=None) -> dict:
    """Insert content-mapping rows into reference.content_mapping with
    the same dedupe semantics viewer_content_scope already applies.

    Returns {"inserted": int, "skipped": [{show, url, platform, reason}],
    "inserted_rows": [...], "dry_run": bool, "error": str|None}. Never
    raises: connectivity failures land as {"error": "..."} so the
    endpoint records a retryable failure state.

    Row dicts may use uppercase or lowercase keys; the ingest lifts to
    the canonical uppercase shape before comparing.
    """
    summary = {"inserted": 0, "skipped": [], "inserted_rows": [],
               "dry_run": bool(dry_run), "error": None}
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return summary
    try:
        index = (existing_index if existing_index is not None
                 else load_existing_content_index())
    except Exception as e:
        summary["error"] = f"content-map index load failed: {str(e)[:200]}"
        return summary

    batch_keys: set = set()
    to_insert: list = []
    for raw in rows:
        show = str(raw.get("SHOW") or raw.get("show") or "").strip()
        url = str(raw.get("URL") or raw.get("url") or "").strip()
        prod = str(raw.get("PRODUCTION") or raw.get("production") or "").strip()
        plat = str(raw.get("PLATFORM") or raw.get("platform") or "").strip()
        season = str(raw.get("SEASON") or raw.get("season") or "").strip()
        if not show or not url or not plat:
            summary["skipped"].append({
                "show": show, "url": url, "platform": plat,
                "reason": "missing required field (SHOW / URL / PLATFORM)"})
            continue
        k2 = (fold_token(plat), fold_token(url))
        k4 = (fold_token(show), fold_token(plat),
              fold_token(season), fold_token(url))
        if k2 in index or k4 in index:
            hit = index.get(k2) or index.get(k4)
            summary["skipped"].append({
                "show": show, "url": url, "platform": plat,
                "reason": ("already in content_mapping as "
                           f"{hit[0]!r} / {hit[1]!r} / {hit[2]!r} / "
                           f"{hit[3]!r}")})
            continue
        if k2 in batch_keys or k4 in batch_keys:
            summary["skipped"].append({
                "show": show, "url": url, "platform": plat,
                "reason": "duplicate within this file"})
            continue
        batch_keys.add(k2)
        batch_keys.add(k4)
        to_insert.append({
            "SHOW": show, "URL": url, "PRODUCTION": prod,
            "PLATFORM": plat, "SEASON": season,
            "CATEGORY": "", "SUB_CATEGORY": "",
        })

    if to_insert and not dry_run:
        try:
            cols = ", ".join(f"`{c}`" for c in CH_INSERT_COLUMNS)
            body = "\n".join(json.dumps(r, ensure_ascii=False)
                             for r in to_insert)
            _hi.ch_query(
                f"INSERT INTO {CH_TABLE} ({cols}) FORMAT JSONEachRow",
                data=body, timeout=120)
        except Exception as e:
            summary["error"] = f"content-map insert failed: {str(e)[:200]}"
            return summary

    summary["inserted"] = len(to_insert)
    summary["inserted_rows"] = [
        {"show": r["SHOW"], "url": r["URL"], "production": r["PRODUCTION"],
         "platform": r["PLATFORM"], "season": r["SEASON"]}
        for r in to_insert
    ]
    return summary


# ---------------------------------------------------------------------------
# Sheet append (content map tab)
# ---------------------------------------------------------------------------
def append_content_rows_to_sheet(rows) -> dict:
    """Append content rows to the ``content map`` tab of the mapping
    sheet. Graceful skip (never raises, never blocks the CH ingest)."""
    return _hi.append_rows_to_sheet(
        rows,
        tab_gid=CONTENT_MAP_TAB_GID,
        tab_title=CONTENT_MAP_TAB_TITLE,
        columns=CONTENT_COLUMNS,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify_shows_in_table(shows) -> list:
    """Post-insert verification: current content_mapping rows for these
    shows. Never raises; empty on any failure."""
    try:
        quoted = ", ".join(
            "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"
            for s in sorted({str(s) for s in (shows or []) if str(s).strip()})
        )
        if not quoted:
            return []
        text = _hi.ch_query(
            f"SELECT SHOW, URL, PRODUCTION, PLATFORM, SEASON "
            f"FROM {CH_TABLE} WHERE SHOW IN ({quoted}) "
            f"ORDER BY SHOW, PLATFORM, SEASON, URL FORMAT JSONEachRow",
            timeout=60)
        out = []
        for line in text.splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out
    except Exception:
        return []
