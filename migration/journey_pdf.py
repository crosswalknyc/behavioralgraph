"""The PDF analysis that ships with a Digital Journey.

Plain English on the Crosswalk design: one finding per page, a short
header, the table that proves it, one line on what it means. Every
number is read off the journey payload (the Babylon 5 shape in
``migration.journey_gold``), so the PDF, the workbook and the dashboard
always agree. No model call.

    from migration.journey_pdf import build_pdf, pdf_filename
    pdf_bytes = build_pdf(payload)
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = ['build_pdf', 'pdf_filename', 'analysis_points', 'recommendations']

GRAPHITE = '#0C1618'
SLATE = '#15252A'
OFFWHITE = '#E9E8E1'
GREEN = '#C7F23E'
OLIVE = '#5E7E12'
PAVEMENT = '#3B3D38'
BODY_DARK = '#9AA09B'
MUTED_DARK = '#7C878A'
BODY_LIGHT = '#5C6560'
MUTED_LIGHT = '#888C89'

_ROOT = Path(__file__).resolve().parents[1]
_FONT_DIR = _ROOT / 'static' / 'fonts'
_LOGO_WHITE = _ROOT / 'static' / 'crosswalk-logo-white-transparent.png'
_LOGO_BLACK = _ROOT / 'static' / 'crosswalk-logo-deck.png'


def _fmt(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return str(n or '')


def _pct(part, whole, nd=1) -> str:
    try:
        part, whole = float(part), float(whole)
        return f"{part / whole * 100:.{nd}f}%" if whole else '0%'
    except Exception:
        return ''


def _font_file(weight: str):
    """Crosswalk Inter from the crosswalk-design pack (2026-10-21, the
    locked skill); the legacy Inter 18pt file under static/fonts only when
    the pack is absent on this machine."""
    try:
        from migration import design_skill as _ds
        p = _ds.font_path(weight)
        if p:
            return Path(p)
    except Exception:
        pass
    p = _FONT_DIR / f'Inter_18pt-{weight}.ttf'
    return p if p.exists() else None


def _fonts():
    """Register the house family (Crosswalk Inter; Inter 18pt as the
    fallback) under the Inter18-* face names every renderer uses;
    Helvetica when neither is on the machine."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    weights = {'regular': 'Regular', 'medium': 'Medium', 'bold': 'Bold', 'light': 'Light'}
    out = {}
    for key, w in weights.items():
        path = _font_file(w)
        face = f"Inter18-{key}"
        try:
            if path is not None and path.exists():
                if face not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(face, str(path)))
                out[key] = face
            else:
                raise FileNotFoundError(w)
        except Exception:
            out[key] = {'regular': 'Helvetica', 'medium': 'Helvetica', 'bold': 'Helvetica-Bold', 'light': 'Helvetica'}[key]
    return out


# ------------------------------------------------------------ the reading
def _top(section: Optional[dict], n: int = 1) -> List[Tuple[str, int]]:
    rows = [(str(r.get('label') or ''), int(r.get('accounts') or 0)) for r in ((section or {}).get('rows') or [])]
    rows = [r for r in rows if r[0] and not r[0].lower().startswith('other')]
    return sorted(rows, key=lambda r: -r[1])[:n]


def _base(section: Optional[dict]) -> int:
    rows = (section or {}).get('rows') or []
    if (section or {}).get('kind') == 'overlap':
        return int((section or {}).get('base') or 0) or max([int(r.get('accounts') or 0) for r in rows] + [0])
    return sum(int(r.get('accounts') or 0) for r in rows)


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def analysis_points(payload: dict) -> List[str]:
    """The TLDR: what we found, why it matters, what to do. Three to five lines."""
    from migration import journey_gold as G
    s = G.ship_summary(payload)
    T, P = s['title'], s['platform']
    pts = []
    if s['social'] and s['watched']:
        pts.append(f"{_fmt(s['social'])} people saw a social post about {T}. {_fmt(s['watched'])} of them "
                   f"({s['conversion']:.1f}%) watched an episode on {P} within 30 days.")
    if s['watched'] and s['same_day']:
        pts.append(f"The decision is fast. {_pct(s['same_day'], s['watched'])} of the viewers pressed play the same day they saw the post.")
    fp, ws = _top(G.section(payload, 'first_post')), _top(G.section(payload, 'post_surface'))
    if fp and ws:
        pts.append(f"The post that opened the path was most often {_lower_first(fp[0][0])} ({_pct(fp[0][1], s['social'])} of first views), "
                   f"and it sat on {ws[0][0]} ({_pct(ws[0][1], s['social'])}).")
    pb = G.section(payload, 'posts_before')
    if pb and s['click']:
        one = next((int(r.get('accounts') or 0) for r in pb.get('rows') or [] if str(r.get('label', '')).startswith('1 post')), 0)
        pts.append(f"{_pct(s['click'] - one, s['click'])} of the people who clicked through needed two or more posts first. Plan a run, not a single post.")
    if s['viewers'] and s['no_post']:
        pts.append(f"{_fmt(s['viewers'])} people watched {T} on {P} in the window. {_pct(s['no_post'], s['viewers'])} of them never saw a post, "
                   f"so social is one door of several.")
    return pts[:5]


def recommendations(payload: dict) -> List[str]:
    from migration import journey_gold as G
    s = G.ship_summary(payload)
    T, P = s['title'], s['platform']
    recs = []
    fp, ws = _top(G.section(payload, 'first_post')), _top(G.section(payload, 'post_surface'))
    if fp and ws:
        recs.append(f"Lead the next flight with {_lower_first(fp[0][0])} on {ws[0][0]}. That pairing opened the path for the most viewers "
                    f"({_fmt(fp[0][1])} first views).")
    pb = G.section(payload, 'posts_before')
    if pb and s['click']:
        rows = {str(r.get('label')): int(r.get('accounts') or 0) for r in pb.get('rows') or []}
        two_plus = s['click'] - rows.get('1 post', 0)
        recs.append(f"Budget for frequency. {_fmt(two_plus)} of the {_fmt(s['click'])} click-throughs ({_pct(two_plus, s['click'])}) came after two or more posts.")
    if s['page'] and s['watched_return']:
        recs.append(f"Keep the title page one tap from the post and follow up fast. {_fmt(s['watched_return'])} of the {_fmt(s['watched'])} viewers left the page first "
                    f"and came back before they played; the same-day window holds {_pct(s['same_day'], s['watched'])} of all first plays.")
    lb = _top(G.section(payload, 'looked_before'))
    if lb and s['click']:
        recs.append(f"Meet them where they check first. {lb[0][0]} was the most common stop between the post and {P} "
                    f"({_pct(lb[0][1], s['click'])} of click-throughs); make sure the title reads well there.")
    fe = _top(G.section(payload, 'first_episode'))
    if fe and s['watched']:
        recs.append(f"Point creative at the real entry point. {fe[0][0]} was the first episode for {_pct(fe[0][1], s['watched'])} of viewers.")
    return recs[:5]


# ------------------------------------------------------------- the pages
def pdf_filename(payload: dict) -> str:
    from migration import journey_gold as G
    T, P = G._title_platform(payload)
    if G.is_media_week_journey(payload):
        stem = re.sub(r'[^A-Za-z0-9]+', '_', f"{T} viewers a week in the life Digital Journey").strip('_')
        return f"{stem}.pdf"
    if G.is_premiere_journey(payload):
        stem = re.sub(r'[^A-Za-z0-9]+', '_', f"{T} premiere first episode audience Digital Journey").strip('_')
        return f"{stem}.pdf"
    meta = payload.get('meta') or {}
    if meta.get('title_noun'):
        # the file is named for the audience alone ("Online Soda Shoppers");
        # the noun ("soda") is for the sentences inside
        name = str(meta.get('project_name') or meta.get('target_name') or '')
        T = name.rsplit(' on ', 1)[0].strip() if ' on ' in name else (name.strip() or T)
        stem = re.sub(r'[^A-Za-z0-9]+', '_', f"{T} Digital Journey").strip('_')
        return f"{stem}.pdf"
    P = re.sub(r"\s*\(.*$", '', str(P or '')).strip() or P
    stem = re.sub(r'[^A-Za-z0-9]+', '_', f"{T} on {P} Digital Journey").strip('_')
    return f"{stem}.pdf"


class _Doc:
    """A landscape-letter deck in reportlab, Crosswalk palette, plain headers."""

    def __init__(self):
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.pdfgen import canvas
        self.buf = io.BytesIO()
        self.W, self.H = landscape(letter)
        self.f = _fonts()
        # the canvas opens on the house face: reportlab's default preamble
        # would otherwise write Helvetica (never embedded) into every page
        self.c = canvas.Canvas(self.buf, pagesize=(self.W, self.H), initialFontName=self.f['regular'])
        self.page = 0
        self.m = 0.92 * 72                       # grid starts at 0.920 in
        self.right = self.W - 0.587 * 72          # ends at 12.413 in on an 11 x 8.5 sheet, scaled

    # chrome
    def _chrome(self, dark: bool, eyebrow: str):
        from reportlab.lib import colors
        c = self.c
        c.setFillColor(colors.HexColor(GRAPHITE if dark else OFFWHITE))
        c.rect(0, 0, self.W, self.H, stroke=0, fill=1)
        logo = _LOGO_WHITE if dark else _LOGO_BLACK
        try:
            if logo.exists():
                from reportlab.lib.utils import ImageReader
                from PIL import Image as _Im
                im = _Im.open(str(logo)).convert('RGBA')
                if not dark:                      # the lockup is ink on the light ground, graphite not pure black
                    r, g, b = int(GRAPHITE[1:3], 16), int(GRAPHITE[3:5], 16), int(GRAPHITE[5:7], 16)
                    tint = _Im.new('RGBA', im.size, (r, g, b, 0))
                    tint.putalpha(im.getchannel('A'))
                    im = tint
                c.drawImage(ImageReader(im), self.m, self.H - 0.72 * 72, width=1.25 * 72, height=0.26 * 72,
                            preserveAspectRatio=True, anchor='sw', mask='auto')
        except Exception:
            pass
        self._eyebrow = eyebrow
        c.setFont(self.f['medium'], 8.5)
        c.setFillColor(colors.HexColor(GREEN if dark else OLIVE))
        c.drawRightString(self.right, self.H - 0.62 * 72, eyebrow.upper())
        c.setFont(self.f['regular'], 7.5)
        c.setFillColor(colors.HexColor(MUTED_DARK if dark else MUTED_LIGHT))
        c.drawString(self.m, 0.42 * 72, 'CROSSWALK  /  BEHAVIORAL INTELLIGENCE ENGINE')
        c.drawRightString(self.right, 0.42 * 72, f"{self.page:02d}")

    def new_page(self, dark: bool, eyebrow: str, header: str, sub: str = '', text_width: float = 0):
        from reportlab.lib import colors
        if self.page:
            self.c.showPage()
        self.page += 1
        self._chrome(dark, eyebrow)
        c = self.c
        c.setFillColor(colors.HexColor(OFFWHITE if dark else GRAPHITE))
        c.setFont(self.f['bold'], 20)
        y = self.H - 1.35 * 72
        tw = text_width or (self.right - self.m)
        for line in self._wrap(header, self.f['bold'], 20, tw):
            c.drawString(self.m, y, line)
            y -= 26
        if sub:
            c.setFont(self.f['regular'], 11)
            c.setFillColor(colors.HexColor(BODY_DARK if dark else BODY_LIGHT))
            for line in self._wrap(sub, self.f['regular'], 11, tw):
                c.drawString(self.m, y - 4, line)
                y -= 15
        self.y = y - 18
        self.dark = dark
        return self.y

    def cover_photo(self, image, credit: str = '') -> bool:
        """The journey's still on the right half of the cover, bleeding to
        the page edge, with its credit in small muted type under it. A
        licensed photo keeps its author on the page (never in a header).
        ``image`` is a local path, bytes, or an http URL (fetched once into
        the temp dir). Returns False, drawing nothing, when it cannot load."""
        from reportlab.lib import colors
        try:
            from reportlab.lib.utils import ImageReader
            from PIL import Image as _Im
            import io as _io
            if isinstance(image, (bytes, bytearray)):
                raw = bytes(image)
            elif str(image).startswith(('http://', 'https://')):
                import hashlib as _hl
                import tempfile as _tf
                import urllib.request as _ur
                cache = Path(_tf.gettempdir()) / ('jiq_cover_' + _hl.sha1(str(image).encode()).hexdigest()[:12])
                if not cache.exists():
                    req = _ur.Request(str(image), headers={'User-Agent': 'Mozilla/5.0 (Crosswalk journey pdf)'})
                    with _ur.urlopen(req, timeout=30) as r:
                        cache.write_bytes(r.read())
                raw = cache.read_bytes()
            else:
                raw = Path(str(image)).read_bytes()
            im = _Im.open(_io.BytesIO(raw)).convert('RGB')
            x0 = self.W * 0.56
            w, h = self.W - x0, self.H
            scale = max(w / im.width, h / im.height)
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
            left, top = (im.width - w) // 2, (im.height - h) // 2
            im = im.crop((int(left), int(top), int(left + w), int(top + h)))
            self.c.drawImage(ImageReader(im), x0, 0, width=w, height=h)
            # a soft wash where the photo meets the type so the ground reads as one
            for i in range(28):
                a = 0.85 * (1 - i / 28.0)
                self.c.setFillColor(colors.Color(12 / 255.0, 22 / 255.0, 24 / 255.0, alpha=a))
                self.c.rect(x0 + i * 2.2, 0, 2.2, h, stroke=0, fill=1)
            # the chrome the photo covered goes back on top of it, each line
            # on a small graphite pill so it reads on a light photograph
            from reportlab.pdfbase.pdfmetrics import stringWidth as _sw

            def _pill(text, font, size, y, color):
                w = _sw(text, font, size)
                self.c.setFillColor(colors.Color(12 / 255.0, 22 / 255.0, 24 / 255.0, alpha=0.62))
                self.c.roundRect(self.right - w - 8, y - 4, w + 16, size + 8, 4, stroke=0, fill=1)
                self.c.setFont(font, size)
                self.c.setFillColor(color)
                self.c.drawRightString(self.right, y, text)

            eyebrow = str(getattr(self, '_eyebrow', '') or '').upper()
            if eyebrow:
                _pill(eyebrow, self.f['medium'], 8.5, self.H - 0.62 * 72, colors.HexColor(GREEN))
            _pill(f"{self.page:02d}", self.f['regular'], 7.5, 0.42 * 72, colors.Color(233 / 255.0, 232 / 255.0, 225 / 255.0, alpha=0.95))
            if credit:
                _pill(str(credit), self.f['regular'], 7.5, 0.42 * 72 + 16, colors.Color(233 / 255.0, 232 / 255.0, 225 / 255.0, alpha=0.9))
            return True
        except Exception:
            return False

    def _wrap(self, text: str, font: str, size: float, width: float) -> List[str]:
        from reportlab.pdfbase.pdfmetrics import stringWidth
        words, lines, cur = str(text).split(), [], ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if stringWidth(trial, font, size) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or ['']

    def paragraph(self, text: str, x: float, y: float, width: float, size: float = 10.5, color: str = '', font: str = '') -> float:
        from reportlab.lib import colors
        c = self.c
        font = font or self.f['regular']
        c.setFont(font, size)
        c.setFillColor(colors.HexColor(color or (BODY_DARK if self.dark else BODY_LIGHT)))
        for line in self._wrap(text, font, size, width):
            c.drawString(x, y, line)
            y -= size * 1.42
        return y

    def bullets(self, items: List[str], x: float, y: float, width: float, size: float = 12) -> float:
        from reportlab.lib import colors
        c = self.c
        for it in items:
            c.setFillColor(colors.HexColor(GREEN if self.dark else OLIVE))
            c.circle(x + 4, y + size * 0.32, 2.6, stroke=0, fill=1)
            y = self.paragraph(it, x + 16, y, width - 16, size=size,
                               color=OFFWHITE if self.dark else GRAPHITE, font=self.f['medium'])
            y -= 7
        return y

    def table(self, title: str, rows: List[Tuple[str, str, str]], x: float, y: float, width: float,
              head: Tuple[str, str, str] = ('', 'Accounts', 'Share'), note: str = '') -> float:
        from reportlab.lib import colors
        c = self.c
        ink = OFFWHITE if self.dark else GRAPHITE
        body = BODY_DARK if self.dark else BODY_LIGHT
        rule = PAVEMENT if self.dark else '#C9C8C0'
        if title:
            c.setFont(self.f['bold'], 11.5)
            c.setFillColor(colors.HexColor(ink))
            for line in self._wrap(title, self.f['bold'], 11.5, width):
                c.drawString(x, y, line)
                y -= 15
            y -= 2
        c.setFont(self.f['medium'], 8)
        c.setFillColor(colors.HexColor(MUTED_DARK if self.dark else MUTED_LIGHT))
        c.drawString(x, y, head[0].upper())
        c.drawRightString(x + width - 62, y, head[1].upper())
        c.drawRightString(x + width, y, head[2].upper())
        y -= 5
        c.setStrokeColor(colors.HexColor(rule))
        c.setLineWidth(0.6)
        c.line(x, y, x + width, y)
        y -= 13
        for label, count, share in rows:
            c.setFont(self.f['regular'], 9.5)
            c.setFillColor(colors.HexColor(ink))
            lab = label
            while self._sw(lab, self.f['regular'], 9.5) > width - 130 and len(lab) > 8:
                lab = lab[:-2]
            if lab != label:
                lab = lab.rstrip() + '...'
            c.drawString(x, y, lab)
            c.setFillColor(colors.HexColor(body))
            c.drawRightString(x + width - 62, y, count)
            c.setFillColor(colors.HexColor(GREEN if self.dark else OLIVE))
            c.setFont(self.f['medium'], 9.5)
            c.drawRightString(x + width, y, share)
            y -= 14.5
        if note:
            y -= 2
            y = self.paragraph(note, x, y, width, size=8.5, color=MUTED_DARK if self.dark else MUTED_LIGHT)
        return y

    def meaning(self, text: str):
        from reportlab.lib import colors
        c = self.c
        y = 0.78 * 72
        c.setFont(self.f['medium'], 8)
        c.setFillColor(colors.HexColor(GREEN if self.dark else OLIVE))
        c.drawString(self.m, y + 16, 'WHAT THIS MEANS')
        self.paragraph(text, self.m, y, self.right - self.m, size=10.5, color=OFFWHITE if self.dark else GRAPHITE, font=self.f['medium'])

    def _sw(self, s, font, size):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        return stringWidth(s, font, size)

    def finish(self) -> bytes:
        self.c.save()
        return self.buf.getvalue()


def _rows_for(section: Optional[dict], base: Optional[int] = None, limit: int = 8) -> List[Tuple[str, str, str]]:
    rows = (section or {}).get('rows') or []
    b = base if base else _base(section)
    out = []
    for r in rows[:limit]:
        n = int(r.get('accounts') or 0)
        out.append((str(r.get('label') or ''), _fmt(n), _pct(n, b)))
    if len(rows) > limit:
        rest = sum(int(r.get('accounts') or 0) for r in rows[limit:])
        out.append((f"{len(rows) - limit} more", _fmt(rest), _pct(rest, b)))
    return out


def build_pdf(payload: dict) -> bytes:
    """The analysis PDF for a journey payload (gold shape preferred; any
    journey renders). Every PDF leaves through the design pack's check
    (2026-10-21: embedded fonts verified; the file is never held)."""
    data = _build_pdf(payload)
    try:
        from migration import design_skill as _ds
        return _ds.finish_bytes(data, '.pdf')
    except Exception:
        return data


def _build_pdf(payload: dict) -> bytes:
    import copy
    from migration import journey_gold as G
    if G.is_media_week_journey(payload):
        from migration import journey_pdf_media_week as _pm
        return _pm.build_pdf(payload)
    if G.is_premiere_journey(payload):
        from migration import journey_pdf_premiere as _pp
        return _pp.build_pdf(payload)
    if not G.is_gold(payload):
        payload = G.apply(copy.deepcopy(payload))
    if not G.is_watch_journey(payload):
        from migration import journey_gold_purchase as _gp
        if _gp.is_purchase_journey(payload):
            from migration import journey_pdf_purchase as _pp
            return _pp.build_pdf(payload)
    from migration import journey_gold_watch as _gw
    if _gw.is_social_then_view(payload):
        return _build_pdf_social_then_view(payload)
    s = G.ship_summary(payload)
    T, P = s['title'], s['platform']
    meta = payload.get('meta') or {}
    window = s['window'].replace(' to ', ' to ') if s['window'] else str(meta.get('window_label') or '')
    d = _Doc()
    full_w = d.right - d.m
    half_w = (full_w - 0.28 * 72) / 2
    col2 = d.m + half_w + 0.28 * 72

    # 01 cover
    d.new_page(True, 'Digital Journey IQ', f"{T} on {P}: the social path to the first episode.",
               f"Unique US accounts, {window}. Every count is one person.")
    d.paragraph("How a social post about the title turned into a first play on the service, in four steps, "
                "and the ten tables behind them.", d.m, d.y - 10, full_w * 0.7, size=12, color=BODY_DARK)

    # 02 TLDR
    d.new_page(False, 'The short version', f"{_fmt(s['watched'])} people watched {T} within 30 days of a social post.",
               f"{s['conversion']:.1f}% of everyone who saw a post. Here is what matters and what to do.")
    y = d.bullets(analysis_points(payload), d.m, d.y, full_w, size=12.5)
    d.meaning(f"Social did real work for {T}: it reached {_fmt(s['social'])} people and {_fmt(s['watched'])} of them watched. "
              f"The rest of this file shows which posts, which platforms and how many touches it took.")

    # 03 the path
    d.new_page(False, 'The path', f"Four steps from a social post to the first episode.",
               f"Each step keeps a share of the one before it. {_fmt(s['social'])} saw a post; {_fmt(s['watched'])} watched.")
    spine = (payload.get('fragrance_shop_journey') or {}).get('spine') or []
    rows, prev = [], None
    for st in spine[1:]:
        n = int(st.get('accounts') or 0)
        rows.append((str(st.get('label') or ''), _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    y = d.table('The spine', rows, d.m, d.y, half_w, head=('Step', 'Accounts', 'Kept'))
    fork = (payload.get('fragrance_shop_journey') or {}).get('fork') or []
    frows = [(str(f.get('label') or f.get('id')), _fmt(f.get('accounts')), f"{float(f.get('kept') or 0):.1f}%") for f in fork]
    d.table('The title page: who left and who came back', frows, col2, d.y, half_w, head=('Branch', 'Accounts', 'Kept'),
            note='Kept is the share of the branch above it. Same-session plays and plays after a return add up to the watch step.')
    d.meaning(f"{_pct(s['page'], s['click'])} of the people who clicked through opened the {P} page, and {_pct(s['watched'], s['page'])} of those "
              f"watched within 30 days. The page is where the path is won or lost.")

    # 04 where it started
    fp, ps, pb = G.section(payload, 'first_post'), G.section(payload, 'post_surface'), G.section(payload, 'posts_before')
    top_fp, top_ps = _top(fp), _top(ps)
    hdr = (f"{top_fp[0][0]} on {top_ps[0][0]} opened the most paths." if top_fp and top_ps else "Where the path started.")
    d.new_page(False, 'Where it started', hdr[:1].upper() + hdr[1:],
               f"The first post each of the {_fmt(s['social'])} viewers saw, where it sat, and how many posts it took to click.")
    y1 = d.table('First post they saw', _rows_for(fp, s['social']), d.m, d.y, half_w)
    y2 = d.table('Where the first post sat', _rows_for(ps, s['social']), col2, d.y, half_w)
    d.table('How many posts before they clicked through', _rows_for(pb, s['click']), d.m, min(y1, y2) - 16, half_w,
            note=f"Share of the {_fmt(s['click'])} who clicked through.")
    one = next((int(r.get('accounts') or 0) for r in (pb or {}).get('rows') or [] if str(r.get('label', '')).startswith('1 post')), 0)
    d.meaning(f"One post was enough for {_pct(one, s['click'])} of the click-throughs. The rest needed a run of posts, "
              f"so frequency is part of the plan, not a nice-to-have.")

    # 05 the road to the platform
    lb, tp, fe = G.section(payload, 'looked_before'), G.section(payload, 'time_to_play'), G.section(payload, 'first_episode')
    d.new_page(False, 'From the click to the play', f"Most first plays came the same day as the post.",
               f"{_pct(s['same_day'], s['watched'])} the same day, {_pct(s['within_7'], s['watched'])} within a week, "
               f"{_pct(s['within_30'], s['watched'])} in days 8 to 30.")
    y1 = d.table(f"Where they looked before {P}", _rows_for(lb, s['click']), d.m, d.y, half_w,
                 note=f"Share of the {_fmt(s['click'])} click-throughs. A person can appear in more than one row.")
    y2 = d.table('Time from the social view to the first episode', _rows_for(tp, s['watched']), col2, d.y, half_w)
    d.table('First episode they played', _rows_for(fe, s['watched'], limit=6), col2, y2 - 16, half_w)
    top_lb = _top(lb)
    d.meaning((f"{top_lb[0][0]} was the most common stop between the post and {P}. " if top_lb else '') +
              f"The window to convert is short: after the first week only {_pct(s['within_30'], s['watched'])} of the plays were still to come.")

    # 06 what closed it
    lt, asst = G.section(payload, 'last_touch'), G.section(payload, 'assists')
    top_lt = _top(lt)
    d.new_page(False, 'What closed it', (f"{top_lt[0][0]} was the last touch before most plays." if top_lt else "The last touch before the play."),
               f"The final step before the {_fmt(s['watched'])} first plays, and what else was in the mix along the way.")
    d.table('Last touch before the watch', _rows_for(lt, s['watched']), d.m, d.y, half_w)
    d.table('What assisted the watch', _rows_for(asst, s['watched']), col2, d.y, half_w,
            note='Assists overlap: one viewer can have several.')
    d.meaning("Credit the last touch for the close and the assists for the warm-up. Both are needed to read the next campaign fairly.")

    # 07 every viewer
    ro, ev = G.section(payload, 'rest_overlap'), G.section(payload, 'every_viewer')
    d.new_page(False, 'The whole audience', f"{_fmt(s['viewers'])} people watched {T} on {P} in the window.",
               f"The journey keeps the {_fmt(s['watched'])} whose first play came within 30 days of a post. This page shows everyone else.")
    d.table('Social viewers outside the 30-day window', _rows_for(ro, s['carried']), d.m, d.y, half_w,
            note=f"Share of the {_fmt(s['carried'])} viewers who also engaged socially.")
    d.table(f"Every {T} viewer on {P} in the window", _rows_for(ev, s['viewers']), col2, d.y, half_w)
    d.meaning(f"{_pct(s['no_post'], s['viewers'])} of the viewers never saw a post. Social is the biggest door we can measure here, not the only one.")

    # 08 what to do
    d.new_page(True, 'What to do next', f"Three moves for the next {T} flight.", "Each one comes with the number that backs it.")
    d.bullets(recommendations(payload)[:4], d.m, d.y, full_w, size=12.5)
    d.meaning(f"Reply to the email this came with to run the same path for another title, or to cut this one by platform, post family or episode.")
    return d.finish()


def _build_pdf_social_then_view(payload: dict) -> bytes:
    """The analysis PDF for the saw-then-watched definition (Carolyn
    Bisson, 2026-10-22; Jenna: the click step stays on the spine, the
    version she reads): four steps from a social post to the first
    episode, the title page fork beside it, the same ten tables, with the
    short version and the moves read off the payload's own read."""
    from migration import journey_gold as G
    from migration import journey_gold_watch as GW
    s = GW.ship_summary(payload)
    T, P = s['title'], s['platform']
    meta = payload.get('meta') or {}
    window = s['window'] if s['window'] else str(meta.get('window_label') or '')
    read = payload.get('the_read') if isinstance(payload.get('the_read'), dict) else {}
    tldr = [str(x) for x in (read.get('tldr') or []) if str(x).strip()] or analysis_points(payload)
    recs = [str(x) for x in (read.get('recommendations') or []) if str(x).strip()] or recommendations(payload)
    d = _Doc()
    full_w = d.right - d.m
    half_w = (full_w - 0.28 * 72) / 2
    col2 = d.m + half_w + 0.28 * 72

    # 01 cover
    d.new_page(True, 'Digital Journey IQ', f"{T} on {P}: the social path to the first episode.",
               f"Unique US accounts, {window}. Every count is one person.")
    d.paragraph("How a social post about the title turned into a first play on the service, in four steps, "
                "and the ten tables behind them.", d.m, d.y - 10, full_w * 0.7, size=12, color=BODY_DARK)

    # 02 TLDR
    d.new_page(False, 'The short version', f"{_fmt(s['watched'])} people watched {T} within 30 days of a social post.",
               f"{s['conversion']:.1f}% of everyone who saw a post. Here is what matters and what to do.")
    y = d.bullets(tldr[:5], d.m, d.y, full_w, size=12.5)
    d.meaning(f"Social did real work for {T}: it reached {_fmt(s['social'])} people and {_fmt(s['watched'])} of them watched. "
              f"The rest of this file shows which posts, which platforms and how fast the watching came.")

    # 03 the path
    d.new_page(False, 'The path', f"Four steps from a social post to the first episode.",
               f"Each step keeps a share of the one before it. {_fmt(s['social'])} saw a post; {_fmt(s['watched'])} watched.")
    spine = (payload.get('fragrance_shop_journey') or {}).get('spine') or []
    rows, prev = [], None
    for st in spine[1:]:
        n = int(st.get('accounts') or 0)
        rows.append((str(st.get('label') or ''), _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    y = d.table('The spine', rows, d.m, d.y, half_w, head=('Step', 'Accounts', 'Kept'))
    fork = (payload.get('fragrance_shop_journey') or {}).get('fork') or []
    frows = [(str(f.get('label') or f.get('id')), _fmt(f.get('accounts')), f"{float(f.get('kept') or 0):.1f}%") for f in fork]
    d.table(f"The {P} page: who left and who came back", frows, col2, d.y, half_w, head=('Branch', 'Accounts', 'Kept'),
            note='Kept is the share of the branch above it. Same-session plays and plays after a return add up to the watch step.')
    d.meaning(f"{_pct(s['click'], s['social'])} of the people who saw a post clicked through to the title. {_pct(s['page'], s['click'])} of those opened the {P} page, "
              f"and {_pct(s['watched'], s['page'])} of the people on the page watched within 30 days. The page is where the path is won or lost.")

    # 04 where it started
    fp, ps, pb = GW.section(payload, 'first_post'), GW.section(payload, 'post_surface'), GW.section(payload, 'posts_before')
    top_fp, top_ps = _top(fp), _top(ps)
    hdr = (f"{top_fp[0][0]} on {top_ps[0][0]} opened the most paths." if top_fp and top_ps else "Where the path started.")
    d.new_page(False, 'Where it started', hdr[:1].upper() + hdr[1:],
               f"The first post each of the {_fmt(s['social'])} viewers saw, where it sat, and how many posts it took to click.")
    y1 = d.table('First post they saw', _rows_for(fp, s['social']), d.m, d.y, half_w)
    y2 = d.table('Where the first post sat', _rows_for(ps, s['social']), col2, d.y, half_w)
    d.table('How many posts before they clicked through', _rows_for(pb, s['click']), d.m, min(y1, y2) - 16, half_w,
            note=f"Share of the {_fmt(s['click'])} who clicked through.")
    one = next((int(r.get('accounts') or 0) for r in (pb or {}).get('rows') or [] if str(r.get('label', '')).startswith('1 post')), 0)
    d.meaning(f"One post was enough for {_pct(one, s['click'])} of the click-throughs. The rest needed a run of posts, "
              f"so frequency is part of the plan, not a nice-to-have.")

    # 05 from the post to the play
    lb, tp, fe = GW.section(payload, 'looked_before'), GW.section(payload, 'time_to_play'), GW.section(payload, 'first_episode')
    fast = (s['same_day'] + s['within_7']) >= 0.6 * max(s['watched'], 1)
    d.new_page(False, 'From the post to the play', ("Most first plays came within a week of the post." if fast else "The watching spread across the month."),
               f"{_pct(s['same_day'], s['watched'])} the same day, {_pct(s['within_7'], s['watched'])} within a week, "
               f"{_pct(s['within_30'], s['watched'])} in days 8 to 30.")
    y1 = d.table(f"Where they looked before {P}", _rows_for(lb, s['click']), d.m, d.y, half_w,
                 note=f"Share of the {_fmt(s['click'])} click-throughs. A person can appear in more than one row.")
    y2 = d.table('Time from the social view to the first episode', _rows_for(tp, s['watched']), col2, d.y, half_w)
    d.table('First episode they played', _rows_for(fe, s['watched'], limit=6), col2, y2 - 16, half_w)
    top_lb = _top(lb)
    d.meaning((f"{top_lb[0][0]} was the most common stop between the post and {P}. " if top_lb else '') +
              (f"The window to convert is short: after the first week only {_pct(s['within_30'], s['watched'])} of the plays were still to come." if fast else
               f"A post kept working for weeks: {_pct(s['within_30'], s['watched'])} of the plays landed in days 8 to 30."))

    # 06 what closed it
    lt, asst = GW.section(payload, 'last_touch'), GW.section(payload, 'assists')
    top_lt = _top(lt)
    d.new_page(False, 'What closed it', (f"{top_lt[0][0]} was the last touch before most plays." if top_lt else "The last touch before the play."),
               f"The final step before the {_fmt(s['watched'])} first plays, and what else was in the mix along the way.")
    d.table('Last touch before the watch', _rows_for(lt, s['watched']), d.m, d.y, half_w)
    d.table('What assisted the watch', _rows_for(asst, s['watched']), col2, d.y, half_w,
            note='Assists overlap: one viewer can have several.')
    d.meaning("Credit the last touch for the close and the assists for the warm-up. Both are needed to read the next campaign fairly.")

    # 07 every viewer
    ro, ev = GW.section(payload, 'rest_overlap'), GW.section(payload, 'every_viewer')
    d.new_page(False, 'The whole audience', f"{_fmt(s['viewers'])} people watched {T} on {P} in the window.",
               f"The journey keeps the {_fmt(s['watched'])} whose first play came within 30 days of a post. This page shows everyone else.")
    d.table('Social viewers outside the 30-day window', _rows_for(ro, s['outside_30'] + s['also']), d.m, d.y, half_w,
            note=f"Share of the {_fmt(s['outside_30'] + s['also'])} viewers who saw a post but watched outside the 30 days or before the posts.")
    d.table(f"Every {T} viewer on {P} in the window", _rows_for(ev, s['viewers']), col2, d.y, half_w)
    d.meaning(f"{_pct(s['no_post'], s['viewers'])} of the viewers never saw a post. Social is the biggest door we can measure here, not the only one.")

    # 08 what to do
    d.new_page(True, 'What to do next', f"Moves for the next {T} flight.", "Each one comes with the number that backs it.")
    d.bullets(recs[:4], d.m, d.y, full_w, size=12.5)
    d.meaning(f"Reply to the email this came with to run the same path for another title, or to cut this one by platform, post family or episode.")
    return d.finish()
