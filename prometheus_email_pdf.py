"""Branded PDF of a Prometheus email body, built to be shared.

Jenna, 2026-09-30: "attach pdfs of the prometheus emails of what the
email body says ... including a pdf of the email body she can take and
share." Every emailed read now carries a PDF of the same words, set on
the Crosswalk system (Graphite Teal ground, Inter 18pt, Signal Green
accent), so the recipient can forward one clean artifact instead of a
screenshot of an email.

Layout rules, kept deliberately small:

- A blank-line-separated block renders as one body paragraph.
- A single short ALL-CAPS line renders as a section label.
- Consecutive lines that share the same ' / ' field count render as a
  table (first row is the header). ``table_highlight_prefix`` bolds
  the matching row in Signal Green (the subject row).
- A block starting with "The short version" renders in Signal Green.
- A final "Prometheus\nCrosswalk" block renders as the signature.

``render_answer_pdf`` never raises: any failure returns b'' and the
caller ships the email without the attachment.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path

GRAPHITE = "#0C1618"
OFF_WHITE = "#E9E8E1"
MUTED = "#9AA09B"
FAINT = "#7C878A"
SIGNAL_GREEN = "#C7F23E"
RULE = "#2A3A3E"

_INTER_REGISTERED = False
_INTER_FAMILY = "Inter18pt"
_HELV_FAMILY = "Helvetica"

_DASHES = {"\u2014": " - ", "\u2013": "-", "\u2015": " - ", "\u2012": "-"}
_SMART = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
          "\u00a0": " "}


def _clean(text):
    t = str(text or "")
    for k, v in _DASHES.items():
        t = t.replace(k, v)
    for k, v in _SMART.items():
        t = t.replace(k, v)
    return t


def _find_font_dir():
    """Inter 18pt TTF bundle. Env override, then the skill checkout,
    then the production copy shipped with the webapp."""
    candidates = []
    env = os.environ.get("PROMETHEUS_PDF_FONT_DIR")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    candidates.append(here.parent / ".cursor" / "skills"
                      / "crosswalk-brand-standards" / "assets" / "fonts")
    candidates.append(here / "static" / "fonts")
    for c in candidates:
        if c.is_dir() and (c / "Inter_18pt-Regular.ttf").is_file():
            return c
    return None


def _register_inter():
    global _INTER_REGISTERED
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if _INTER_REGISTERED:
        return _INTER_FAMILY
    font_dir = _find_font_dir()
    if font_dir is None:
        return _HELV_FAMILY
    try:
        for suffix, fname in (("", "Inter_18pt-Regular.ttf"),
                              ("-Bold", "Inter_18pt-Bold.ttf"),
                              ("-Medium", "Inter_18pt-Medium.ttf"),
                              ("-Light", "Inter_18pt-Light.ttf")):
            fp = font_dir / fname
            if fp.is_file():
                pdfmetrics.registerFont(TTFont(_INTER_FAMILY + suffix,
                                               str(fp)))
    except Exception:
        return _HELV_FAMILY
    _INTER_REGISTERED = True
    return _INTER_FAMILY


def _font(family, weight=""):
    if family == _INTER_FAMILY:
        return family + weight
    if weight in ("-Bold",):
        return family + "-Bold"
    return family


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


_TABLE_LINE = re.compile(r".+ / .+")


def _is_caps_label(block):
    return ("\n" not in block and 3 <= len(block) <= 64
            and block == block.upper()
            and any(c.isalpha() for c in block))


def _split_table(block):
    """(rows, n_fields) when every line shares one ' / ' shape."""
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    rows = [[c.strip() for c in ln.split(" / ")] for ln in lines]
    n = len(rows[0])
    if n < 3 or any(len(r) != n for r in rows):
        return None
    if not all(_TABLE_LINE.match(ln) for ln in lines):
        return None
    return rows, n


def render_answer_pdf(title, body_text, date_label=None,
                      table_highlight_prefix=None):
    """The email body as branded PDF bytes. b'' on any failure."""
    try:
        if not str(body_text or "").strip():
            return b""
        return _render(title, body_text, date_label,
                       table_highlight_prefix)
    except Exception:
        import traceback
        traceback.print_exc()
        return b""


def _render(title, body_text, date_label, table_highlight_prefix):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                    PageTemplate, Paragraph, Spacer, Table,
                                    TableStyle)

    fam = _register_inter()
    page_w, page_h = letter
    margin = 54.0
    content_w = page_w - 2 * margin

    ink = HexColor(OFF_WHITE)
    muted = HexColor(MUTED)
    faint = HexColor(FAINT)
    green = HexColor(SIGNAL_GREEN)
    rule = HexColor(RULE)

    s_title = ParagraphStyle(
        "t", fontName=_font(fam, "-Bold"), fontSize=19, leading=24.5,
        textColor=ink, spaceAfter=2)
    s_date = ParagraphStyle(
        "d", fontName=_font(fam), fontSize=9, leading=12,
        textColor=faint, spaceAfter=16)
    s_label = ParagraphStyle(
        "l", fontName=_font(fam, "-Medium"), fontSize=8.6, leading=11,
        textColor=muted, spaceBefore=15, spaceAfter=5)
    s_body = ParagraphStyle(
        "b", fontName=_font(fam), fontSize=10.2, leading=15.8,
        textColor=ink, spaceAfter=9)
    s_hi = ParagraphStyle(
        "h", fontName=_font(fam, "-Medium"), fontSize=11.4, leading=16.6,
        textColor=green, spaceBefore=2, spaceAfter=11)
    s_sig = ParagraphStyle(
        "s", fontName=_font(fam, "-Medium"), fontSize=10.2, leading=15,
        textColor=ink, spaceBefore=13)

    def paint(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(GRAPHITE))
        canvas.rect(0, 0, page_w, page_h, stroke=0, fill=1)
        to = canvas.beginText(margin, 30)
        to.setCharSpace(0.7)
        to.setFont(_font(fam, "-Bold"), 6.6)
        to.setFillColor(faint)
        to.textOut("CROSSWALK")
        to.setFont(_font(fam, "-Light") if fam == _INTER_FAMILY
                   else fam, 6.6)
        to.textOut("   /   BEHAVIORAL INTELLIGENCE ENGINE")
        canvas.drawText(to)
        canvas.setFont(_font(fam), 6.6)
        canvas.setFillColor(faint)
        canvas.drawRightString(page_w - margin, 30, str(canvas.getPageNumber()))
        canvas.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=letter, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin + 12,
        title=_clean(title), author="Crosswalk")
    frame = Frame(margin, margin + 12, content_w,
                  page_h - 2 * margin - 12, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame],
                                       onPage=paint)])

    story = []
    eyebrow = ParagraphStyle(
        "e", fontName=_font(fam, "-Medium"), fontSize=8.2, leading=10,
        textColor=green, spaceAfter=9)
    story.append(Paragraph("P R O M E T H E U S", eyebrow))
    story.append(Paragraph(_esc(_clean(title)), s_title))
    when = date_label or datetime.now(timezone.utc).strftime("%B %d, %Y")
    story.append(Paragraph(_esc(_clean(when)), s_date))

    def _table_flowables(block):
        tab = _split_table(block)
        if not tab:
            return None
        rows, n = tab
        data = [[_esc(c) for c in r] for r in rows]
        c0 = min(132.0, content_w * 0.27)
        cw = [c0] + [(content_w - c0) / (n - 1)] * (n - 1)
        fs = 8.0 if n >= 6 else 9.0
        st = [
                ("FONTNAME", (0, 0), (-1, 0), _font(fam, "-Medium")),
                ("TEXTCOLOR", (0, 0), (-1, 0), muted),
                ("FONTNAME", (0, 1), (-1, -1), _font(fam)),
                ("TEXTCOLOR", (0, 1), (-1, -1), ink),
                ("FONTSIZE", (0, 0), (-1, -1), fs),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, rule),
            ]
        if table_highlight_prefix:
            for ri, r in enumerate(rows):
                if ri and r[0].startswith(table_highlight_prefix):
                    st += [("TEXTCOLOR", (0, ri), (-1, ri), green),
                           ("FONTNAME", (0, ri), (-1, ri),
                            _font(fam, "-Bold"))]
        return [Spacer(0, 3),
                Table(data, colWidths=cw, style=TableStyle(st),
                      repeatRows=1),
                Spacer(0, 8)]

    blocks = [b.strip() for b in
              re.split(r"\n\s*\n", _clean(body_text)) if b.strip()]
    i = 0
    while i < len(blocks):
        block = blocks[i]
        is_last = (i == len(blocks) - 1)
        if is_last and block.lower() in ("prometheus\ncrosswalk",):
            story.append(Paragraph(
                "Prometheus<br/><font color='%s'>Crosswalk</font>" % MUTED,
                s_sig))
            i += 1
            continue
        tab_flow = _table_flowables(block)
        if tab_flow:
            story.append(KeepTogether(tab_flow))
            i += 1
            continue
        if _is_caps_label(block):
            # A label never strands away from what it labels: when a
            # table follows, the pair moves as one unit.
            next_tab = (_table_flowables(blocks[i + 1])
                        if i + 1 < len(blocks) else None)
            if next_tab:
                story.append(KeepTogether(
                    [Paragraph(_esc(block), s_label)] + next_tab))
                i += 2
                continue
            story.append(Paragraph(_esc(block), s_label))
            i += 1
            continue
        text = _esc(block).replace("\n", "<br/>")
        if block.lower().startswith("the short version"):
            story.append(Paragraph(text, s_hi))
        else:
            story.append(Paragraph(text, s_body))
        i += 1

    doc.build(story)
    return buf.getvalue()
