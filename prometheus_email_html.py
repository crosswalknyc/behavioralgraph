#!/usr/bin/env python3
"""The Prometheus answer email, rendered in the light Crosswalk system.

Jenna 2026-09-30: "I prefer this design for emails moving forward."
The reference is the Influencer Project conversion email: soft
off-white page, white content column, PROMETHEUS eyebrow in Signal
Olive, a plain bold headline in near-Graphite ink, bold sentence-case
section headers, label/value tables with hairline rules and
right-aligned bold values, and bullets whose first sentence is bold.

`render_answer_email_html(title, body_text)` takes the same plain-text
body grammar as prometheus_email_pdf.render_answer_pdf, so one body
string feeds the email HTML and the shareable PDF and the two always
match. Grammar:

- blank-line-separated paragraphs
- a single short line with no terminal punctuation is a section header
  (ALL-CAPS headers are also honored)
- consecutive lines containing ' / ' with a consistent field count are
  a table; 2 fields render as a label/value list, 3+ fields render as
  a grid whose first row is the header
- lines starting with '- ' or '* ' inside a block are bullets; a
  bullet's first sentence renders bold when more text follows
- a final block of exactly 'Prometheus\nCrosswalk' is the signature

Email-client safe: inline styles only, single centered column.
Never raises: any failure returns '' so callers can fall back.
"""
import html as _html
import re

# Sampled from the approved reference email (2026-09-30).
PAGE = "#F4F3EE"        # soft off-white page behind the column
CARD = "#FFFFFF"        # white content column
INK = "#0C1618"         # headlines, bold values (Graphite Teal as ink)
BODY = "#3B3D38"        # body copy (Pavement)
MUTED = "#888C89"       # date line, footer, signature second line
OLIVE = "#5E7E12"       # Signal Olive: eyebrow + light-surface accent
HAIRLINE = "#E3E3E1"    # table rules

FONT = ("'Inter 18pt',Inter,-apple-system,'Segoe UI',"
        "Helvetica,Arial,sans-serif")

_SIG = ("prometheus\ncrosswalk",)


def _clean(text):
    text = str(text or "")
    for bad, good in (("\u2014", " - "), ("\u2013", "-"), ("\u2019", "'"),
                      ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                      ("\r\n", "\n")):
        text = text.replace(bad, good)
    return text.strip()


def _esc(text):
    return _html.escape(str(text or ""), quote=False)


def _is_header(block):
    """A standalone short line with no terminal punctuation reads as a
    section header ('The short answer', 'WHERE SHE SITS')."""
    if "\n" in block or len(block) > 64:
        return False
    if block[-1:] in ".:!?,;":
        return False
    if " / " in block or block.startswith(("- ", "* ")):
        return False
    return len(block.split()) >= 2 or block.isupper()


def _split_table(block):
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    rows = [[c.strip() for c in ln.split(" / ")] for ln in lines]
    n = len(rows[0])
    if n < 2 or any(len(r) != n for r in rows):
        return None
    return rows, n


def _is_bullets(block):
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    return bool(lines) and all(ln.startswith(("- ", "* ", "\u2022 "))
                               for ln in lines)


def _bold_lead(text):
    """Bold a bullet's first sentence when more text follows it."""
    m = re.match(r"^(.{8,140}?[.!?])\s+(\S.*)$", text, re.DOTALL)
    if not m:
        return _esc(text)
    return (f"<strong style='color:{INK}'>{_esc(m.group(1))}</strong> "
            + _esc(m.group(2)))


def render_answer_email_html(title, body_text, date_label=None,
                             table_highlight_prefix=None,
                             cta_url=None, cta_text=None,
                             eyebrow="PROMETHEUS"):
    """The email body as light-system HTML. '' on any failure."""
    try:
        if not str(body_text or "").strip():
            return ""
        return _render(title, body_text, date_label,
                       table_highlight_prefix, cta_url, cta_text, eyebrow)
    except Exception:
        import traceback
        traceback.print_exc()
        return ""


def _render(title, body_text, date_label, table_highlight_prefix,
            cta_url, cta_text, eyebrow):
    parts = []
    blocks = [b.strip() for b in
              re.split(r"\n\s*\n", _clean(body_text)) if b.strip()]
    for i, block in enumerate(blocks):
        if (i == len(blocks) - 1) and block.lower() in _SIG:
            parts.append(
                f"<div style='margin-top:30px;color:{INK};font-size:15px;"
                f"font-weight:600'>Prometheus<br>"
                f"<span style='color:{MUTED};font-weight:400'>Crosswalk"
                "</span></div>")
            continue
        tab = _split_table(block)
        if tab:
            rows, n = tab
            hi = table_highlight_prefix
            tr = []
            if n == 2:
                # Label / value list: hairlines, right-aligned bold values.
                for label, value in rows:
                    strong = hi and label.startswith(hi)
                    lc = OLIVE if strong else BODY
                    vc = OLIVE if strong else INK
                    tr.append(
                        "<tr>"
                        f"<td style='padding:11px 0;border-bottom:1px solid "
                        f"{HAIRLINE};color:{lc};font-size:15px'>"
                        f"{_esc(label)}</td>"
                        f"<td style='padding:11px 0;border-bottom:1px solid "
                        f"{HAIRLINE};color:{vc};font-size:15px;"
                        "font-weight:700;text-align:right'>"
                        f"{_esc(value)}</td></tr>")
            else:
                head = rows[0]
                tr.append("<tr>" + "".join(
                    f"<td style='padding:8px 8px 8px 0;border-bottom:1px "
                    f"solid {HAIRLINE};color:{MUTED};font-size:12px;"
                    f"{'' if ci == 0 else 'text-align:right;'}'>"
                    f"{_esc(c)}</td>"
                    for ci, c in enumerate(head)) + "</tr>")
                for r in rows[1:]:
                    strong = hi and r[0].startswith(hi)
                    color = OLIVE if strong else BODY
                    weight = "700" if strong else "400"
                    tr.append("<tr>" + "".join(
                        f"<td style='padding:9px 8px 9px 0;border-bottom:"
                        f"1px solid {HAIRLINE};color:{color};font-size:13px;"
                        f"font-weight:{weight};"
                        f"{'' if ci == 0 else 'text-align:right;'}'>"
                        f"{_esc(c)}</td>"
                        for ci, c in enumerate(r)) + "</tr>")
            parts.append(
                "<table role='presentation' width='100%' cellpadding='0' "
                "cellspacing='0' style='border-collapse:collapse;"
                "margin:6px 0 14px'>" + "".join(tr) + "</table>")
            continue
        if _is_bullets(block):
            items = []
            for ln in block.split("\n"):
                ln = ln.strip().lstrip("-*\u2022 ").strip()
                if ln:
                    items.append(
                        f"<li style='margin:0 0 10px;color:{BODY};"
                        f"font-size:15px;line-height:1.6'>"
                        f"{_bold_lead(ln)}</li>")
            parts.append("<ul style='margin:6px 0 14px;padding-left:22px'>"
                         + "".join(items) + "</ul>")
            continue
        if _is_header(block):
            parts.append(
                f"<div style='margin:26px 0 8px;color:{INK};font-size:17px;"
                "font-weight:700'>" + _esc(block) + "</div>")
            continue
        text = _esc(block).replace("\n", "<br>")
        low = block.lower()
        if low.startswith(("the short version", "the short answer")):
            cut = block.find(":")
            if 0 < cut < 40:
                text = (f"<strong style='color:{INK}'>"
                        f"{_esc(block[:cut + 1])}</strong>"
                        + _esc(block[cut + 1:]).replace("\n", "<br>"))
        parts.append(
            f"<p style='margin:0 0 14px;color:{BODY};font-size:15px;"
            "line-height:1.65'>" + text + "</p>")

    if cta_url and str(cta_url).lower().startswith("https://"):
        parts.append(
            f"<p style='margin:20px 0'><a href='{_html.escape(cta_url)}' "
            f"style='display:inline-block;background:{INK};color:#E9E8E1;"
            "padding:12px 26px;border-radius:8px;text-decoration:none;"
            "font-weight:600;font-size:14px'>"
            f"{_esc(cta_text or 'Open')}</a></p>")

    date_html = (f"<div style='color:{MUTED};font-size:13px;"
                 f"margin:4px 0 0'>{_esc(date_label)}</div>"
                 if date_label else "")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{PAGE};font-family:{FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:{PAGE}"><tr><td align="center"
       style="padding:26px 12px;">
<table role="presentation" width="660" cellpadding="0" cellspacing="0"
       style="max-width:660px;width:100%;background:{CARD};
              border-radius:12px;"><tr>
<td style="padding:34px 38px 30px;">
<div style="font-size:11px;letter-spacing:.24em;text-transform:uppercase;
            color:{OLIVE};font-weight:700;">{_esc(eyebrow)}</div>
<h1 style="margin:10px 0 2px;color:{INK};font-size:23px;line-height:1.25;
           font-weight:800;">{_esc(title)}</h1>
{date_html}
<div style="margin-top:20px;">
{"".join(parts)}
</div>
<div style="margin-top:34px;padding-top:16px;border-top:1px solid
            {HAIRLINE};font-size:10px;letter-spacing:.18em;
            color:{MUTED};text-transform:uppercase;">
CROSSWALK&nbsp;&nbsp;/&nbsp;&nbsp;BEHAVIORAL INTELLIGENCE ENGINE</div>
</td></tr></table>
</td></tr></table>
</body>
</html>"""
