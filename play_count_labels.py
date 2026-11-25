"""Platform play counts are called plays (Jenna 2026-11-24, rename_plays:
"Yes, rename them 'Total plays' to match our definition").

A view is one person, counted once; a play is one start
(prometheus/methodology.py). The short-form conversion screen and the
Micro-Series screen show platform video counts, which count starts, so
both screens label them plays. Their data keeps its field names
(total_views, view_window_estimate, duplicatedViews, the stored report's
"Total Views (Events)" rows): the screens parse those names.

Two readers sit outside the page and need the same words:
  * Prometheus reads a screen through
    prometheus_analysis.render_view_context_block; screen_count_note
    names the fields on these two screens that are plays.
  * The short-form report download serves the stored CSV;
    relabel_sf_report_bytes renames its plays rows on the way out.
"""
from __future__ import annotations

import csv
import io

TOTAL_PLAYS = 'Total plays'

# Stored short-form report cell -> the label in the downloaded file.
# 'Duplicated Views' is the metric name reports carried before 'Total
# Views (Events)'.
SF_REPORT_LABELS = {
    'Total Views (Events)': TOTAL_PLAYS,
    'Duplicated Views': TOTAL_PLAYS,
    'Total Views': TOTAL_PLAYS,
    'Total SF Views (Duplicated)': TOTAL_PLAYS,
}
SF_REPORT_LABEL_COLUMNS = ('Metric', 'Value')

SCREEN_COUNT_NOTES = {
    'microdramasIQ': (
        "COUNT NAMES ON THIS SCREEN: every count the data names views "
        "(total_views, grand_total_views, top_title_views, "
        "view_window_estimate, view_28d_estimate, sort_views and the daily "
        "views series) is plays: one start each, and one person can start "
        "many times. The screen labels them plays (Total plays). Call them "
        "plays, never views or viewers. The unique viewer counts are "
        "people."),
    'sfConversion': (
        "COUNT NAMES ON THIS SCREEN: duplicatedViews, duplicatedViewsGenPop "
        "and totalViews are plays: one start each, and one person can "
        "start many times. The screen labels them plays (Total plays). "
        "Call them plays, never views or viewers. uniqueViews, the viewer "
        "counts and the converted counts are people."),
}


def screen_count_note(view_id) -> str:
    """The count-name note for a screen, '' for any other screen."""
    return SCREEN_COUNT_NOTES.get(str(view_id or ''), '')


def relabel_sf_report_csv(text: str) -> str:
    """The stored short-form report with its plays rows labeled plays.

    Only whole Metric / Value cells listed in SF_REPORT_LABELS change. Any
    other file, or any failure, comes back as the same object."""
    try:
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return text
        cols = [i for i, name in enumerate(rows[0])
                if name in SF_REPORT_LABEL_COLUMNS]
        if not cols:
            return text
        changed = False
        for row in rows[1:]:
            for i in cols:
                if i < len(row) and row[i] in SF_REPORT_LABELS:
                    row[i] = SF_REPORT_LABELS[row[i]]
                    changed = True
        if not changed:
            return text
        out = io.StringIO()
        csv.writer(out, lineterminator='\n').writerows(rows)
        return out.getvalue()
    except Exception:
        return text


def relabel_sf_report_bytes(content: bytes) -> bytes:
    """relabel_sf_report_csv for the raw bytes a download serves."""
    try:
        text = content.decode('utf-8')
    except Exception:
        return content
    out = relabel_sf_report_csv(text)
    return content if out is text else out.encode('utf-8')
