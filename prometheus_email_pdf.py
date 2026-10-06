"""Branded PDF of a Prometheus email body, built to be shared.

Jenna, 2026-09-30: "attach pdfs of the prometheus emails of what the
email body says ... including a pdf of the email body she can take and
share." Same day: "I prefer this design for emails moving forward,"
pointing at the light system (soft off-white page, Signal Olive
eyebrow, near-Graphite ink, hairline tables). Every emailed read
carries a PDF of the same words in that system, so the recipient can
forward one clean artifact instead of a screenshot of an email.

The body grammar is shared with prometheus_email_html so one string
feeds both the email HTML and this PDF:

- A blank-line-separated block renders as one body paragraph.
- A standalone short line with no terminal punctuation renders as a
  bold section header (ALL-CAPS headers are honored too).
- Consecutive lines that share one ' / ' field count render as a
  table: 2 fields is a label/value list with right-aligned bold
  values, 3+ fields is a grid whose first row is the header.
  ``table_highlight_prefix`` bolds the matching row in Signal Olive.
- Lines starting with '- ' or '* ' render as bullets; a bullet's
  first sentence renders bold when more text follows.
- A block starting with "The short version" / "The short answer"
  bolds its lead-in.
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

# Light system, sampled from the approved reference email (2026-09-30).
PAGE = "#F4F3EE"
INK = "#0C1618"
BODY_C = "#3B3D38"
MUTED = "#888C89"
OLIVE = "#5E7E12"
HAIRLINE = "#E3E3E1"

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


def _is_header(block):
    """A standalone short line with no terminal punctuation reads as a
    section header ('The short answer', 'WHERE SHE SITS')."""
    if "\n" in block or not (3 <= len(block) <= 64):
        return False
    if block[-1:] in ".:!?,;":
        return False
    if " / " in block or block.startswith(("- ", "* ", "\u2022 ")):
        return False
    if block == block.upper() and any(c.isalpha() for c in block):
        return True
    return len(block.split()) >= 2


def _is_bullets(block):
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    return bool(lines) and all(ln.startswith(("- ", "* ", "\u2022 "))
                               for ln in lines)


def _expand_blocks(blocks):
    """A header line stacked directly on its table rows or bullets (no
    blank line between) splits into header block + content block so
    both render with their full treatment."""
    out = []
    for b in blocks:
        lines = [ln for ln in b.split("\n") if ln.strip()]
        if len(lines) >= 2:
            first = lines[0].strip()
            rest = "\n".join(lines[1:])
            if _is_header(first) and (_split_table(rest)
                                      or _is_bullets(rest)):
                out.append(first)
                out.append(rest)
                continue
        out.append(b)
    return out


def _split_table(block):
    """(rows, n_fields) when every line shares one ' / ' shape."""
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    rows = [[c.strip() for c in ln.split(" / ")] for ln in lines]
    n = len(rows[0])
    if n < 2 or any(len(r) != n for r in rows):
        return None
    if not all(_TABLE_LINE.match(ln) for ln in lines):
        return None
    return rows, n


def _scrub_outbound(title, body_text):
    """Every outbound email passes the same vocabulary scrub as a chat
    reply (2026-10-06): internal terms replaced, method sentences
    removed, figures kept. Fail-safe to the original text."""
    try:
        import prometheus_analysis as _pma
        return (_pma.scrub_user_text(str(title or "")),
                _pma.scrub_user_text(str(body_text or "")))
    except Exception:
        return title, body_text


def render_answer_pdf(title, body_text, date_label=None,
                      table_highlight_prefix=None):
    """The email body as branded PDF bytes. b'' on any failure."""
    try:
        if not str(body_text or "").strip():
            return b""
        title, body_text = _scrub_outbound(title, body_text)
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
                                    ListFlowable, ListItem, PageTemplate,
                                    Paragraph, Spacer, Table, TableStyle)

    fam = _register_inter()
    page_w, page_h = letter
    margin = 54.0
    content_w = page_w - 2 * margin

    ink = HexColor(INK)
    body_c = HexColor(BODY_C)
    muted = HexColor(MUTED)
    olive = HexColor(OLIVE)
    hairline = HexColor(HAIRLINE)

    s_title = ParagraphStyle(
        "t", fontName=_font(fam, "-Bold"), fontSize=19, leading=24.5,
        textColor=ink, spaceAfter=2)
    s_date = ParagraphStyle(
        "d", fontName=_font(fam), fontSize=9, leading=12,
        textColor=muted, spaceAfter=16)
    s_head = ParagraphStyle(
        "l", fontName=_font(fam, "-Bold"), fontSize=12, leading=15,
        textColor=ink, spaceBefore=14, spaceAfter=5)
    s_body = ParagraphStyle(
        "b", fontName=_font(fam), fontSize=10.2, leading=15.8,
        textColor=body_c, spaceAfter=9)
    s_sig = ParagraphStyle(
        "s", fontName=_font(fam, "-Medium"), fontSize=10.2, leading=15,
        textColor=ink, spaceBefore=13)

    def paint(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(HexColor(PAGE))
        canvas.rect(0, 0, page_w, page_h, stroke=0, fill=1)
        to = canvas.beginText(margin, 30)
        to.setCharSpace(0.7)
        to.setFont(_font(fam, "-Bold"), 6.6)
        to.setFillColor(muted)
        to.textOut("CROSSWALK")
        to.setFont(_font(fam, "-Light") if fam == _INTER_FAMILY
                   else fam, 6.6)
        to.textOut("   /   BEHAVIORAL INTELLIGENCE ENGINE")
        canvas.drawText(to)
        canvas.setFont(_font(fam), 6.6)
        canvas.setFillColor(muted)
        canvas.drawRightString(page_w - margin, 30,
                               str(canvas.getPageNumber()))
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
        textColor=olive, spaceAfter=9)
    story.append(Paragraph("P R O M E T H E U S", eyebrow))
    story.append(Paragraph(_esc(_clean(title)), s_title))
    when = date_label or datetime.now(timezone.utc).strftime("%B %d, %Y")
    story.append(Paragraph(_esc(_clean(when)), s_date))

    def _table_flowables(block):
        tab = _split_table(block)
        if not tab:
            return None
        rows, n = tab
        hi = table_highlight_prefix
        data = [[_esc(c) for c in r] for r in rows]
        if n == 2:
            # Label / value list: hairlines, right-aligned bold values.
            cw = [content_w * 0.62, content_w * 0.38]
            st = [
                ("FONTNAME", (0, 0), (0, -1), _font(fam)),
                ("TEXTCOLOR", (0, 0), (0, -1), body_c),
                ("FONTNAME", (1, 0), (1, -1), _font(fam, "-Bold")),
                ("TEXTCOLOR", (1, 0), (1, -1), ink),
                ("FONTSIZE", (0, 0), (-1, -1), 10.0),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, hairline),
            ]
            if hi:
                for ri, r in enumerate(rows):
                    if r[0].startswith(hi):
                        st += [("TEXTCOLOR", (0, ri), (-1, ri), olive),
                               ("FONTNAME", (0, ri), (-1, ri),
                                _font(fam, "-Bold"))]
            return [Spacer(0, 3),
                    Table(data, colWidths=cw, style=TableStyle(st)),
                    Spacer(0, 8)]
        c0 = min(132.0, content_w * 0.27)
        cw = [c0] + [(content_w - c0) / (n - 1)] * (n - 1)
        fs = 8.0 if n >= 6 else 9.0
        st = [
            ("FONTNAME", (0, 0), (-1, 0), _font(fam, "-Medium")),
            ("TEXTCOLOR", (0, 0), (-1, 0), muted),
            ("FONTNAME", (0, 1), (-1, -1), _font(fam)),
            ("TEXTCOLOR", (0, 1), (-1, -1), body_c),
            ("FONTSIZE", (0, 0), (-1, -1), fs),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, hairline),
        ]
        if hi:
            for ri, r in enumerate(rows):
                if ri and r[0].startswith(hi):
                    st += [("TEXTCOLOR", (0, ri), (-1, ri), olive),
                           ("FONTNAME", (0, ri), (-1, ri),
                            _font(fam, "-Bold"))]
        return [Spacer(0, 3),
                Table(data, colWidths=cw, style=TableStyle(st),
                      repeatRows=1),
                Spacer(0, 8)]

    def _bullet_flowable(block):
        items = []
        for ln in block.split("\n"):
            ln = ln.strip().lstrip("-*\u2022 ").strip()
            if not ln:
                continue
            m = re.match(r"^(.{8,140}?[.!?])\s+(\S.*)$", ln, re.DOTALL)
            if m:
                text = ("<font name='%s' color='%s'>%s</font> %s"
                        % (_font(fam, "-Bold"), INK,
                           _esc(m.group(1)), _esc(m.group(2))))
            else:
                text = _esc(ln)
            items.append(ListItem(Paragraph(text, s_body),
                                  leftIndent=14))
        return ListFlowable(items, bulletType="bullet", start="\u2022",
                            bulletColor=body_c, bulletFontSize=9,
                            leftIndent=14)

    blocks = _expand_blocks(
        [b.strip() for b in
         re.split(r"\n\s*\n", _clean(body_text)) if b.strip()])
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
        if _is_bullets(block):
            story.append(_bullet_flowable(block))
            story.append(Spacer(0, 4))
            i += 1
            continue
        if _is_header(block):
            # A header never strands away from what it labels: when a
            # table follows, the pair moves as one unit.
            next_tab = (_table_flowables(blocks[i + 1])
                        if i + 1 < len(blocks) else None)
            if next_tab:
                story.append(KeepTogether(
                    [Paragraph(_esc(block), s_head)] + next_tab))
                i += 2
                continue
            story.append(Paragraph(_esc(block), s_head))
            i += 1
            continue
        text = _esc(block).replace("\n", "<br/>")
        low = block.lower()
        if low.startswith(("the short version", "the short answer")):
            cut = block.find(":")
            if 0 < cut < 40:
                text = ("<font name='%s' color='%s'>%s</font>%s"
                        % (_font(fam, "-Bold"), INK,
                           _esc(block[:cut + 1]),
                           _esc(block[cut + 1:]).replace("\n", "<br/>")))
        story.append(Paragraph(text, s_body))
        i += 1

    doc.build(story)
    return buf.getvalue()
