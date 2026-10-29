"""Attribution IQ: Excel export of the tables on screen for a campaign.

The workbook mirrors the dashboard. A sheet exists only for a tab the
reader can see on that campaign, and each sheet carries only the columns
its on-screen table shows, under the same header labels. Rows run full
length (a table that shows its top 5 or top 20 on screen exports every
row). The only text that is not a table cell is the title line: the
campaign, the tab, and when the file was exported.

Where the rows come from
------------------------
* Attribution, Timing and Journey (the multi-touch slabs) and the
  Audiences district table show payload values as-is, so they are built
  here from the same reads the dashboard makes (``/mta`` with no as-of,
  ``/audiences``).
* Performance is computed in the browser (point-in-time views, lift vs
  the campaign median, the confidence bucket), so the client sends the
  rows its own table builder produced. They are accepted only for a live
  tab, only under the exact on-screen header set, and only as scalar
  cells.

Entry points: :func:`parse_tabs` validates the client's list of live
tabs, :func:`build_workbook` returns the ``.xlsx`` bytes plus a suggested
filename. Pure read; nothing here writes to S3 or ClickHouse.
"""

from __future__ import annotations

import datetime as _dt
import io
import math
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Crosswalk palette (deck system): Graphite Teal ground, Off-White type.
_GRAPHITE = "0C1618"
_OFFWHITE = "E9E8E1"

_FONT = "Inter"

_HEADER_FONT = Font(name=_FONT, bold=True, color=_OFFWHITE, size=10)
_HEADER_FILL = PatternFill("solid", fgColor=_GRAPHITE)
_SECTION_FONT = Font(name=_FONT, bold=True, color=_GRAPHITE, size=11)
_SUBSECTION_FONT = Font(name=_FONT, bold=True, size=10, color="5C6560")
_BODY_FONT = Font(name=_FONT, size=10)
_MUTED_FONT = Font(name=_FONT, size=9, color="5C6560")
_TITLE_FONT = Font(name=_FONT, bold=True, size=14, color=_GRAPHITE)

_MAX_SHEET_NAME = 31
_MAX_COL_WIDTH = 60

# Every tab id the client tab shell (_AIQ_TABS in templates/index.html)
# can render. Anything else in ?tabs= is dropped.
TAB_IDS: Tuple[str, ...] = (
    "summary", "q_q2", "l_assets", "assets", "audiences", "attribution", "timing",
    "q_journey", "q_q4", "q_demographics", "roi", "q_q3", "l_conversion",
)

_MAX_CLIENT_ROWS = 10000
_MAX_CELL_CHARS = 1000


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _sheet_title(name: str, taken: set) -> str:
    base = re.sub(r"[\[\]\:\*\?\/\\]", " ", str(name or "Sheet")).strip() or "Sheet"
    base = base[:_MAX_SHEET_NAME]
    title = base
    n = 2
    while title.lower() in taken:
        suffix = f" ({n})"
        title = base[: _MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    taken.add(title.lower())
    return title


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _pct_cell(v: Any) -> Optional[float]:
    """A percent-point value (12.5 meaning 12.5%) as an Excel fraction."""
    f = _safe_float(v)
    return None if f is None else f / 100.0


def _fmt_date(s: Any) -> str:
    try:
        d = _dt.date.fromisoformat(str(s)[:10])
        return d.strftime("%B %-d, %Y")
    except Exception:
        return str(s or "")


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p not in (None, ""))


def _autosize(ws, min_width: int = 8) -> None:
    widths: Dict[int, int] = {}
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            if cell.value is None:
                continue
            longest = max((len(part) for part in str(cell.value).split("\n")), default=0)
            widths[cell.column] = max(widths.get(cell.column, 0), longest)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(_MAX_COL_WIDTH, w + 2))


def _write_table(ws, headers: Sequence[str], rows: Iterable[Sequence[Any]], start_row: int,
                 number_formats: Optional[Dict[int, str]] = None, empty_text: str = "No rows for this campaign.") -> int:
    """Write a header + rows block. Returns the next free row index."""
    r = start_row
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    r += 1
    count = 0
    for row in rows:
        for c, v in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = _BODY_FONT
            if isinstance(v, str) and "\n" in v:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if number_formats and c in number_formats and isinstance(v, (int, float)):
                cell.number_format = number_formats[c]
        r += 1
        count += 1
    if count == 0:
        ws.cell(row=r, column=1, value=empty_text).font = _MUTED_FONT
        r += 1
    return r


def _section(ws, row: int, label: str, sub: bool = False) -> int:
    ws.cell(row=row, column=1, value=label).font = _SUBSECTION_FONT if sub else _SECTION_FONT
    return row + 1


def _title_lines(ws, ctx: dict, tab_label: str, as_of: bool = False) -> int:
    ws.cell(row=1, column=1, value=f"{ctx['name']}: {tab_label}").font = _TITLE_FONT
    meta = [f"Attribution IQ, exported {ctx['exported_label']}"]
    if as_of and ctx.get("as_of"):
        meta.append(f"As of {_fmt_date(ctx['as_of'])}")
    if ctx.get("audience_label"):
        meta.append(f"Audience: {ctx['audience_label']}")
    ws.cell(row=2, column=1, value=". ".join(meta) + ".").font = _MUTED_FONT
    return 4


# ----------------------------------------------------------------------
# Multi-touch slabs (Attribution, Timing, Journey tabs)
# ----------------------------------------------------------------------

def _mta_view(mta: dict, audience: Optional[str]) -> dict:
    """The slice the dashboard draws (_iiqMTAActivePayload): the picked
    audience when the payload has it, else everyone exposed, with the
    wrapper's labels riding through."""
    if not isinstance(mta, dict) or not mta:
        return {}
    auds = mta.get("audiences") or {}
    sl = auds.get(audience) if (audience and isinstance(auds, dict)) else None
    if not isinstance(sl, dict):
        sl = mta.get("overall")
    if not isinstance(sl, dict):
        return mta
    merged = dict(mta)
    merged.update(sl)
    return merged


def _paths_live(d: dict) -> dict:
    """The paths card draws nothing when it has no nest, forks or leaks."""
    paths = d.get("paths") or {}
    if not (paths.get("nest") or paths.get("forks") or paths.get("leaks")):
        return {}
    return paths


def _surface_rows(items: List[dict], key: str) -> List[list]:
    return [[it.get(key), it.get("us_accounts"), _pct_cell(it.get("pct"))] for it in items or []]


_SHARE_FMT = {2: "#,##0", 3: "0.0%"}


def _block_where(ws, row: int, d: dict) -> int:
    paths = _paths_live(d)
    where = paths.get("where") or {}
    ticket = where.get("ticketer_partition") or []
    info = where.get("infoseek_overlap") or []
    if not (ticket or info):
        return row
    is_film = str(d.get("title_type") or "film").lower() != "brand"
    row = _section(ws, row, "Where they went.")
    ticket_label = ("Showtimes surface (partition, sums to 100%)" if is_film
                    else "Conversion surface (partition, sums to 100%)")
    for label, items in ((ticket_label, ticket), ("Info-seek surface (overlap, may exceed 100%)", info)):
        row = _section(ws, row, label, sub=True)
        row = _write_table(ws, ["Surface", "US accounts", "Share"], _surface_rows(items, "surface"), row,
                           number_formats=_SHARE_FMT, empty_text="No rows.") + 1
    return row


def _block_attribution_cuts(ws, row: int, d: dict) -> int:
    attr = _paths_live(d).get("attribution") or {}
    cuts = (("First touch (partition)", attr.get("first_touch") or []),
            ("Last touch (partition)", attr.get("last_touch") or []),
            ("Assists (overlap)", attr.get("assists") or []))
    if not any(items for _, items in cuts):
        return row
    row = _section(ws, row, "Attribution on bottom funnel destination reach only.")
    for label, items in cuts:
        row = _section(ws, row, label, sub=True)
        row = _write_table(ws, ["Touchpoint", "US accounts", "Share"], _surface_rows(items, "touchpoint"), row,
                           number_formats=_SHARE_FMT, empty_text="No rows on the selected platforms.") + 1
    return row


def _block_leaks(ws, row: int, d: dict) -> int:
    leaks = _paths_live(d).get("leaks") or []
    if not leaks:
        return row
    row = _section(ws, row, "Leaks the pixel cannot see.")
    rows = [[lk.get("leak"), lk.get("note"), lk.get("us_accounts")] for lk in leaks]
    return _write_table(ws, ["Leak", "Note", "US accounts"], rows, row, number_formats={3: "#,##0"}) + 1


def _or_text(t: dict) -> str:
    def f2(v: Optional[float]) -> str:
        return f"{v:.2f}" if v is not None else "-"
    point = _safe_float(t.get("odds_ratio"))
    lo = _safe_float(t.get("odds_ratio_low_95"))
    hi = _safe_float(t.get("odds_ratio_high_95"))
    lo = point if lo is None else lo
    hi = point if hi is None else hi
    text = f"{f2(point)} (95% CI {f2(lo)} to {f2(hi)})"
    if lo is not None and hi is not None and lo <= 1.0 <= hi:
        text += "\nnot significant"
    return text


_STRENGTH = {"strong": "Strong", "moderate": "Moderate", "weak": "Weak"}


def _block_touchpoints(ws, row: int, d: dict) -> int:
    tps = d.get("touchpoints") or []
    if not tps:
        return row
    bfl = str(d.get("bottom_funnel_label") or "Ticketing site visit")
    row = _section(ws, row, f"Touchpoint contribution to {bfl.lower()}.")
    rows = []
    for t in tps:
        sub = " \u00b7 ".join(str(x) for x in (t.get("channel"), t.get("phase")) if x not in (None, ""))
        rows.append([_lines(t.get("asset_title"), sub), _or_text(t),
                     _STRENGTH.get(str(t.get("significance") or "").lower(), "Weak")])
    return _write_table(ws, ["Touchpoint", "Odds ratio (95% CI)", "Read strength"], rows, row) + 1


def _block_time_to_convert(ws, row: int, d: dict) -> int:
    ttc = _paths_live(d).get("time_to_conversion") or []
    if not ttc:
        return row
    row = _section(ws, row, "Time to convert.")
    rows = [[t.get("bucket"), t.get("us_accounts"), _pct_cell(t.get("pct"))] for t in ttc]
    return _write_table(ws, ["Window", "US accounts", "Share"], rows, row, number_formats=_SHARE_FMT) + 1


def _block_archetypes(ws, row: int, d: dict) -> int:
    arche = _paths_live(d).get("path_archetypes") or []
    if not arche:
        return row
    row = _section(ws, row, "Path archetypes.")
    rows = [[a.get("archetype"), a.get("description"), a.get("us_accounts"), _pct_cell(a.get("pct"))] for a in arche]
    return _write_table(ws, ["Shape", "What they did", "US accounts", "% of bottom funnel destination"], rows, row,
                        number_formats={3: "#,##0", 4: "0.0%"}) + 1


def _run_blocks(ws, ctx: dict, label: str, blocks: Sequence[Callable]) -> bool:
    d = ctx.get("mta_view") or {}
    if not d:
        return False
    start = _title_lines(ws, ctx, label)
    row = start
    for b in blocks:
        row = b(ws, row, d)
    if row == start:
        return False
    _autosize(ws)
    ws.column_dimensions["A"].width = 60
    return True


def _sheet_attribution(ws, ctx: dict) -> bool:
    return _run_blocks(ws, ctx, "Attribution",
                       (_block_where, _block_attribution_cuts, _block_leaks, _block_touchpoints))


def _sheet_timing(ws, ctx: dict) -> bool:
    blocks: List[Callable] = [_block_time_to_convert]
    if "q_journey" not in ctx["tabs"]:
        blocks.append(_block_archetypes)
    return _run_blocks(ws, ctx, "Timing", blocks)


def _sheet_journey(ws, ctx: dict) -> bool:
    return _run_blocks(ws, ctx, "Journey", (_block_archetypes,))


# ----------------------------------------------------------------------
# Audiences tab: the congressional district table
# ----------------------------------------------------------------------

def _district_cards(ctx: dict) -> List[dict]:
    cards = ctx.get("audience_cards") or []
    out = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        if str(c.get("category") or "").upper() != "CONGRESSIONAL DISTRICT":
            continue
        if (_safe_float(c.get("adults_18_plus")) or 0) <= 0:
            continue
        out.append(c)
    return out


def _seat_text(seat: str) -> str:
    if seat == "open seat":
        return "Open seat"
    if seat == "district tbd":
        return "District to be set"
    return "Challenger"


def _opponent_text(seat: str, opp: str) -> str:
    if seat == "open seat":
        return re.sub(r"^Open seat \((.*)\)$", r"Open seat, \1 retiring", opp)
    if seat == "district tbd":
        return opp
    if opp and opp != "Incumbent":
        return "vs. " + opp
    return "vs. the incumbent"


def _sheet_audiences(ws, ctx: dict) -> bool:
    cards = _district_cards(ctx)
    if not cards:
        return False
    cards.sort(key=lambda c: -(_safe_float(c.get("penetration_pct")) or 0))
    rows = []
    for c in cards:
        seat = str(c.get("seat_status") or "")
        chip = "Named in a post" if c.get("named_in_post") else ("State-wide post" if c.get("statewide_post") else "")
        candidate = " \u00b7 ".join(x for x in (str(c.get("candidate") or ""), chip) if x)
        rows.append([
            _lines(c.get("district_label") or c.get("district_code"), _seat_text(seat)),
            _lines(candidate, _opponent_text(seat, str(c.get("opponent") or ""))),
            _safe_float(c.get("adults_18_plus")),
            _safe_float(c.get("reached")),
            _pct_cell(c.get("penetration_pct") or 0),
            _pct_cell(c.get("researched_pct") or 0),
            _pct_cell(c.get("action_pct") or 0),
        ])
    row = _title_lines(ws, ctx, "Audiences")
    headers = ["District", "Campaign", "Adults in district", "Adults reached", "Penetration", "Researched", "Action page"]
    _write_table(ws, headers, rows, row,
                 number_formats={3: "#,##0", 4: "#,##0", 5: "0.00%", 6: "0.0%", 7: "0.00%"})
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    _autosize(ws)
    return True


# ----------------------------------------------------------------------
# Performance tab: rows the client's own table builder produced
# ----------------------------------------------------------------------

def _assets_headers_ok(h: List[str]) -> bool:
    return (len(h) == 6 and h[0] == "Asset" and h[1] == "Exposure"
            and h[2] in ("Info-seek %", "Ticketing site visit %")
            and h[3:] == ["Lift vs benchmark", "Sample size", "Confidence"])


def _grid_headers_ok(h: List[str]) -> bool:
    if h[:1] != ["Asset"]:
        return False
    rest = h[2:] if h[1:2] == ["Posted"] else h[1:]
    return (len(rest) == 5 and rest[:4] == ["Views", "Engagement", "Eng. rate", "Info-seek %"]
            and rest[4].endswith(" %") and 2 < len(rest[4]) <= 60)


def _assets_formats(h: List[str]) -> Dict[int, str]:
    return {2: "#,##0", 3: "0.00%", 5: "#,##0"}


def _grid_formats(h: List[str]) -> Dict[int, str]:
    off = 1 if h[1:2] == ["Posted"] else 0
    return {2 + off: "#,##0", 3 + off: "#,##0", 4 + off: "0.00%", 5 + off: "0.00%", 6 + off: "0.00%"}


# table id -> (header check, number formats, which columns arrive as percent points)
_CLIENT_TABLES: Dict[str, Tuple[Callable, Callable, Callable]] = {
    "assets": (_assets_headers_ok, _assets_formats, lambda h: {3}),
    "l_assets": (_grid_headers_ok, _grid_formats,
                 lambda h: {c for c, f in _grid_formats(h).items() if f.endswith("%")}),
}


def _clean_cell(v: Any) -> Tuple[bool, Any]:
    if v is None:
        return True, None
    if isinstance(v, bool):
        return False, None
    if isinstance(v, (int, float)):
        return (True, v) if math.isfinite(v) else (True, None)
    if isinstance(v, str):
        return True, v[:_MAX_CELL_CHARS]
    return False, None


def clean_client_table(table_id: str, raw: Any) -> Optional[dict]:
    """Validate one client table. Returns ``{"headers", "rows"}`` or None."""
    spec = _CLIENT_TABLES.get(table_id)
    if not spec or not isinstance(raw, dict):
        return None
    headers = raw.get("headers")
    rows = raw.get("rows")
    if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
        return None
    if not spec[0](headers) or not isinstance(rows, list) or len(rows) > _MAX_CLIENT_ROWS:
        return None
    pct_cols = spec[2](headers)
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) != len(headers):
            return None
        clean = []
        for c, v in enumerate(r, start=1):
            ok, val = _clean_cell(v)
            if not ok:
                return None
            if c in pct_cols and isinstance(val, (int, float)):
                val = val / 100.0
            clean.append(val)
        out.append(clean)
    return {"headers": list(headers), "rows": out}


def _client_sheet(table_id: str) -> Callable:
    def build(ws, ctx: dict) -> bool:
        tbl = (ctx.get("tables") or {}).get(table_id)
        if not tbl:
            return False
        row = _title_lines(ws, ctx, "Performance", as_of=True)
        _write_table(ws, tbl["headers"], tbl["rows"], row, number_formats=_CLIENT_TABLES[table_id][1](tbl["headers"]))
        ws.freeze_panes = ws.cell(row=row + 1, column=1)
        _autosize(ws)
        return True
    return build


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------

# (tab id, sheet name, builder). Order follows the tab rail.
_SHEETS: List[Tuple[str, str, Callable]] = [
    ("l_assets", "Performance", _client_sheet("l_assets")),
    ("assets", "Performance", _client_sheet("assets")),
    ("audiences", "Audiences", _sheet_audiences),
    ("attribution", "Attribution", _sheet_attribution),
    ("timing", "Timing", _sheet_timing),
    ("q_journey", "Journey", _sheet_journey),
]

_MTA_TABS = {"attribution", "timing", "q_journey"}
_DEFAULT_TABS = frozenset({"audiences", "attribution", "timing"})


def parse_tabs(raw: Any) -> Optional[Set[str]]:
    """Live tab ids from ``?tabs=a,b`` or a JSON list, whitelisted.
    None when the client sent nothing (an old page)."""
    if raw is None:
        return None
    parts = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    allowed = set(TAB_IDS)
    return {p.strip() for p in parts if isinstance(p, str) and p.strip() in allowed}


def _try(label: str, fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive by design
        import logging
        logging.getLogger(__name__).warning("attribution export: %s failed: %s", label, exc)
        return None


def gather(slug: str, intent_iq, mta_iq=None, as_of: Optional[str] = None, tabs: Optional[Set[str]] = None,
           audience: Optional[str] = None, tables: Optional[dict] = None) -> dict:
    """Pull only what the live tabs draw, the same way the dashboard reads it."""
    tabs = set(_DEFAULT_TABS if tabs is None else tabs)
    ov = _try("overview", intent_iq.get_overview, slug) or {}
    ctx: Dict[str, Any] = {"slug": slug, "as_of": as_of, "tabs": tabs, "overview": ov}
    ctx["name"] = ov.get("display_name") or slug
    mta: dict = {}
    if mta_iq is not None and tabs & _MTA_TABS and (ov.get("enabled_tabs") or {}).get("mta"):
        mta = _try("mta", mta_iq.compute_mta_coefficients, slug, as_of=None) or {}
    ctx["mta_view"] = _mta_view(mta, audience)
    aud_slice = ((mta.get("audiences") or {}).get(audience)) if (audience and mta) else None
    if isinstance(aud_slice, dict):
        ctx["audience_label"] = (aud_slice.get("cohort_meta") or {}).get("audience_label") or audience
    if "audiences" in tabs:
        ctx["audience_cards"] = ((_try("audiences", intent_iq.get_audiences, slug) or {}).get("cards")) or []
    ctx["tables"] = {}
    for tid, raw in (tables or {}).items():
        if tid in tabs:
            clean = clean_client_table(tid, raw)
            if clean:
                ctx["tables"][tid] = clean
    now = _dt.datetime.utcnow()
    ctx["exported_label"] = now.strftime("%B %-d, %Y")
    ctx["exported_stamp"] = now.strftime("%Y_%m_%d")
    return ctx


def build_workbook(slug: str, intent_iq, mta_iq=None, as_of: Optional[str] = None, tabs: Optional[Set[str]] = None,
                   audience: Optional[str] = None, tables: Optional[dict] = None) -> Tuple[bytes, str]:
    """Return ``(xlsx_bytes, filename)``: one sheet per live tab that has a table."""
    ctx = gather(slug, intent_iq, mta_iq=mta_iq, as_of=as_of, tabs=tabs, audience=audience, tables=tables)
    wb = Workbook()
    wb.remove(wb.active)
    taken: set = set()
    for tab_id, title, fn in _SHEETS:
        if tab_id not in ctx["tabs"]:
            continue
        ws = wb.create_sheet(_sheet_title(title, taken))
        ok = False
        try:
            ok = bool(fn(ws, ctx))
        except Exception as exc:  # pragma: no cover - defensive by design
            import logging
            logging.getLogger(__name__).warning("attribution export: sheet %s failed: %s", title, exc)
        if not ok:
            taken.discard(ws.title.lower())
            wb.remove(ws)
    if not wb.worksheets:
        ws = wb.create_sheet("Attribution IQ")
        _title_lines(ws, ctx, "Attribution IQ")
        ws.cell(row=4, column=1, value="No table on screen for this campaign yet.").font = _MUTED_FONT
        ws.column_dimensions["A"].width = 60

    buf = io.BytesIO()
    wb.save(buf)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", str(ctx["name"])).strip("_") or "Campaign"
    filename = f"{safe}_Attribution_IQ_{ctx['exported_stamp']}.xlsx"
    return buf.getvalue(), filename


__all__ = ["TAB_IDS", "build_workbook", "clean_client_table", "gather", "parse_tabs"]
