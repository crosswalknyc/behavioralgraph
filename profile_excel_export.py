"""Profile IQ "All Data" as an Excel workbook, one sheet per category.

Client request via Jenna, 2026-11-19: the single CSV puts every
category in one column of one file, so reading or filtering one of
them means scrolling past all the others. The ask was tabs by broad
area, Demographics, Behavioral, Interests, Locations, and inside
Behavioral a tab per category (Most Purchased Brands, Automobile and
the rest).

The browser sends the same table text the CSV already carries, one
block per category, and this splits it into sheets. Going through the
rendered tables rather than re-deriving anything means the workbook
and the CSV can never disagree.

Numbers arrive as display strings ("1,234,567", "12.34%", "$1,200").
They are written as real numbers with a format that prints them the
same way, so a column sorts and filters as a number instead of as
text. Anything that is not cleanly numeric stays as text.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Crosswalk palette (deck system): Graphite Teal ground, Off-White type.
_GRAPHITE = "0C1618"
_OFFWHITE = "E9E8E1"
_FONT = "Inter"

_TITLE_FONT = Font(name=_FONT, bold=True, size=14, color=_GRAPHITE)
_GROUP_FONT = Font(name=_FONT, bold=True, size=9, color="5C6560")
_SUBTITLE_FONT = Font(name=_FONT, bold=True, size=11, color=_GRAPHITE)
_HEADER_FONT = Font(name=_FONT, bold=True, color=_OFFWHITE, size=10)
_HEADER_FILL = PatternFill("solid", fgColor=_GRAPHITE)
_BODY_FONT = Font(name=_FONT, size=10)
_MUTED_FONT = Font(name=_FONT, size=9, color="5C6560")
_LINK_FONT = Font(name=_FONT, size=10, color="0563C1", underline="single")

_MAX_SHEET_NAME = 31
_MAX_COL_WIDTH = 58
_MAX_SHEETS = 80
_MAX_ROWS = 20000
_MAX_CELL_CHARS = 1000

_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_PCT_RE = re.compile(r"^(-?[\d,]+(?:\.\d+)?)\s*%$")
_USD_RE = re.compile(r"^\$\s*(-?[\d,]+(?:\.\d+)?)$")
_IDX_RE = re.compile(r"^(-?[\d,]+(?:\.\d+)?)x$", re.I)


def _sheet_title(name: str, taken: set) -> str:
    """A legal, unique sheet name. Excel refuses []:*?/\\ and 31 chars."""
    base = re.sub(r"[\[\]\:\*\?\/\\]", " ", str(name or "Sheet"))
    base = re.sub(r"\s+", " ", base).strip() or "Sheet"
    base = base[:_MAX_SHEET_NAME]
    title, n = base, 2
    while title.lower() in taken:
        suffix = f" ({n})"
        title = base[: _MAX_SHEET_NAME - len(suffix)].rstrip() + suffix
        n += 1
    taken.add(title.lower())
    return title


def _cell_value(raw: Any) -> Tuple[Any, Optional[str]]:
    """A display string as (value, number_format).

    The format is chosen so the cell still reads exactly as it did in
    the CSV; only the underlying type changes, which is what makes a
    column sortable.
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        return "", None
    if len(s) > _MAX_CELL_CHARS:
        return s[:_MAX_CELL_CHARS], None

    m = _PCT_RE.match(s)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            dec = len((m.group(1).split(".") + [""])[1])
            return v, ('0.' + '0' * dec + '"%"') if dec else '0"%"'
        except ValueError:
            return s, None
    m = _USD_RE.match(s)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            return v, '$#,##0.00' if "." in m.group(1) else '$#,##0'
        except ValueError:
            return s, None
    m = _IDX_RE.match(s)
    if m:
        try:
            return float(m.group(1).replace(",", "")), '0.0"x"'
        except ValueError:
            return s, None
    if _NUM_RE.match(s):
        try:
            v = float(s.replace(",", ""))
            if v.is_integer() and "." not in s:
                return int(v), '#,##0'
            dec = len((s.split(".") + [""])[1])
            return v, '#,##0.' + '0' * dec
        except ValueError:
            return s, None
    return s, None


def _parse_block(text: str) -> List[List[str]]:
    """The CSV text for one category as rows."""
    if not text:
        return []
    rows: List[List[str]] = []
    for row in csv.reader(io.StringIO(text)):
        if any(str(c).strip() for c in row):
            rows.append([str(c) for c in row])
        if len(rows) >= _MAX_ROWS:
            break
    return rows


def _autosize(ws, first_row: int) -> None:
    widths: Dict[int, int] = {}
    for row in ws.iter_rows(min_row=first_row):
        for cell in row:
            if cell.value is None:
                continue
            longest = max((len(p) for p in str(cell.value).split("\n")), default=0)
            widths[cell.column] = max(widths.get(cell.column, 0), longest)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(9, min(_MAX_COL_WIDTH, w + 2))


def _sheet_tables(sheet: Dict[str, Any]) -> List[Tuple[str, List[List[str]]]]:
    """A sheet's tables as (sub-title, rows).

    A sheet is either one category (``csv``) or a group of small
    related tables stacked together (``tables``), which is how
    Demographics keeps its eleven breakdowns on one tab.
    """
    out: List[Tuple[str, List[List[str]]]] = []
    raw = sheet.get("tables")
    if isinstance(raw, list) and raw:
        for t in raw:
            if not isinstance(t, dict):
                continue
            rows = _parse_block(t.get("csv") or "")
            if rows:
                out.append((str(t.get("title") or "").strip(), rows))
        return out
    rows = _parse_block(sheet.get("csv") or "")
    if rows:
        out.append(("", rows))
    return out


def _write_sheet(ws, sheet: Dict[str, Any], stamp: Sequence[str]) -> int:
    """One tab. Returns the number of data rows written."""
    tables = _sheet_tables(sheet)
    group = str(sheet.get("group") or "").strip()
    name = str(sheet.get("name") or "").strip()

    r = 1
    if group:
        ws.cell(row=r, column=1, value=group.upper()).font = _GROUP_FONT
        r += 1
    ws.cell(row=r, column=1, value=name or "Data").font = _TITLE_FONT
    r += 1
    for line in stamp:
        ws.cell(row=r, column=1, value=str(line)).font = _MUTED_FONT
        r += 1
    r += 1

    if not tables:
        ws.cell(row=r, column=1, value="Nothing on this category for this profile.").font = _MUTED_FONT
        _autosize(ws, 1)
        return 0

    total = 0
    first_head = 0
    last_head = 0
    widest = 1
    for title, rows in tables:
        if title:
            ws.cell(row=r, column=1, value=title).font = _SUBTITLE_FONT
            r += 1

        # Some tables open with a label/value preamble (the Age card
        # leads with its two medians). Those lines are narrower than the
        # table, so the first full-width row is the real column header.
        full = max(len(x) for x in rows)
        head_i = next((i for i, x in enumerate(rows) if len(x) == full), 0)
        for pre in rows[:head_i]:
            for c, raw in enumerate(pre, start=1):
                ws.cell(row=r, column=c, value=str(raw)).font = _MUTED_FONT
            r += 1
        header, body = rows[head_i], rows[head_i + 1:]
        widest = max(widest, len(header))
        for c, h in enumerate(header, start=1):
            cell = ws.cell(row=r, column=c, value=str(h))
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = Alignment(vertical="center")
        last_head = r
        first_head = first_head or r
        r += 1
        for line in body:
            for c, raw in enumerate(line, start=1):
                v, fmt = _cell_value(raw)
                cell = ws.cell(row=r, column=c, value=v)
                cell.font = _BODY_FONT
                if fmt:
                    cell.number_format = fmt
            r += 1
        total += len(body)
        r += 1  # a blank line between stacked tables

    # Column filters need one header row, so they go on the tabs that
    # carry a single table. That is every tab except Demographics,
    # which is the one that stacks.
    if len(tables) == 1:
        last_col = get_column_letter(max(1, widest))
        ws.auto_filter.ref = f"A{last_head}:{last_col}{max(last_head, r - 2)}"
    ws.freeze_panes = ws.cell(row=first_head + 1, column=1)
    _autosize(ws, first_head)
    return total


def _write_contents(ws, profile_name: str, stamp: Sequence[str],
                    entries: List[Tuple[str, str, str, int]]) -> None:
    ws.cell(row=1, column=1, value=str(profile_name or "Profile")).font = _TITLE_FONT
    r = 2
    for line in stamp:
        if line:
            ws.cell(row=r, column=1, value=str(line)).font = _MUTED_FONT
            r += 1
    r += 1
    for c, h in enumerate(("Section", "Tab", "Rows"), start=1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
    head = r
    r += 1
    for group, label, title, n in entries:
        ws.cell(row=r, column=1, value=group).font = _BODY_FONT
        link = ws.cell(row=r, column=2, value=label)
        link.font = _LINK_FONT
        # A quoted sheet name is required once it carries a space.
        link.hyperlink = f"#'{title}'!A1"
        cell = ws.cell(row=r, column=3, value=n)
        cell.font = _BODY_FONT
        cell.number_format = '#,##0'
        r += 1
    ws.freeze_panes = ws.cell(row=head + 1, column=1)
    _autosize(ws, head)


def build_workbook(profile_name: str, sheets: Iterable[Dict[str, Any]],
                   stamp: Optional[Sequence[str]] = None) -> Tuple[bytes, str]:
    """The workbook bytes and a filename.

    ``sheets`` is an ordered list of {name, group, csv}. Empty
    categories are kept out so the tab strip only shows what the
    profile actually has.
    """
    stamp = [s for s in (stamp or []) if str(s or "").strip()][:4]
    wb = Workbook()
    contents = wb.active
    contents.title = "Contents"

    taken = {"contents"}
    entries: List[Tuple[str, str, str, int]] = []
    written = 0
    for sheet in list(sheets)[:_MAX_SHEETS]:
        if not isinstance(sheet, dict):
            continue
        label = str(sheet.get("name") or "").strip() or "Data"
        title = _sheet_title(sheet.get("tab") or label, taken)
        ws = wb.create_sheet(title=title)
        n = _write_sheet(ws, sheet, stamp)
        entries.append((str(sheet.get("group") or "").strip(), label, title, n))
        written += 1

    if not written:
        ws = wb.create_sheet(title=_sheet_title("Data", taken))
        ws.cell(row=1, column=1, value="Nothing to export for this profile.").font = _MUTED_FONT

    _write_contents(contents, profile_name, stamp, entries)

    buf = io.BytesIO()
    wb.save(buf)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile_name or "Profile")).strip("_") or "Profile"
    return buf.getvalue(), f"{safe}_All_Data.xlsx"
