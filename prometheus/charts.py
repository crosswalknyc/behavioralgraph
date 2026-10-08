"""Charts for generated reads (2026-10-08).

Casey Pearson asked for genre counts "in histogram format"; the read
came back right but as text. Jenna: "generate the graphic ... and
update so it creates the visual next time."

When an ask wants a visual (histogram, chart, graph, plot, visualize,
"as a chart") or a read carries a distribution (bucket counts inside
its rows), the read ships a PNG in the Crosswalk deck palette: small
multiples for distributions, a horizontal bar chart for a plain
breakdown. Rendered with matplotlib (Agg), Inter when the brand font
files are present, never a vendor name on the image. Fail-safe:
every entry point returns None / b'' on trouble.
"""
from __future__ import annotations

import io
import os
import re

GRAPHITE = '#0C1618'
SLATE = '#15252A'
OFF_WHITE = '#E9E8E1'
SIGNAL_GREEN = '#C7F23E'
ORCHID = '#E682FF'
BODY = '#9AA09B'
MUTED = '#7C878A'
PAVEMENT = '#3B3D38'

_VISUAL_RX = re.compile(
    r"\b(?:histogram|bar\s*chart|chart|graph|plot|visuali[sz]e|visual(?:ly)?|"
    r"in (?:a |the )?(?:chart|graph|graphic|visual)|as a (?:chart|graph|graphic|visual)|"
    r"distribution(?: format)?|show (?:me )?(?:a |the )?(?:chart|graph|graphic|histogram))\b", re.I)
_BUCKET_RX = re.compile(
    r"(?P<label>\d+\+?\s*(?:or more\s*)?[A-Za-z][A-Za-z ]{0,24}?)\s+(?P<count>\d[\d,]{2,})\s*\((?P<pct>\d+(?:\.\d+)?)%\)\s*(?P<mode>MODE)?",
    re.I)
_FONT_DIRS = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'fonts'),
    os.path.expanduser('~/Desktop/finished_codes/.cursor/skills/crosswalk-brand-standards/assets/fonts'),
    '/root/finished_codes/.cursor/skills/crosswalk-brand-standards/assets/fonts',
)


def wants_visual(text):
    return bool(_VISUAL_RX.search(str(text or '')))


def parse_buckets(note):
    """'1 genre 2,250,211 (4.7%) | 2 genres 4,691,927 (9.8%) MODE | ...' ->
    [{'label','count','share_pct','mode'}]. Partial tails are dropped."""
    out = []
    for m in _BUCKET_RX.finditer(str(note or '')):
        try:
            cnt = int(m.group('count').replace(',', ''))
        except ValueError:
            continue
        label = ' '.join(m.group('label').split())
        out.append({'label': label, 'count': cnt, 'share_pct': float(m.group('pct')),
                    'mode': bool(m.group('mode'))})
    return out


def row_buckets(row):
    """Structured buckets on the row, else parsed from its note."""
    b = row.get('buckets') if isinstance(row, dict) else None
    if isinstance(b, list) and len(b) >= 2:
        out = []
        for x in b:
            if not isinstance(x, dict) or not x.get('label'):
                continue
            try:
                out.append({'label': str(x['label'])[:40], 'count': int(x.get('count') or 0),
                            'share_pct': float(x.get('share_pct') or 0), 'mode': bool(x.get('mode'))})
            except (TypeError, ValueError):
                continue
        if len(out) >= 2:
            return out
    return parse_buckets((row or {}).get('note')) if isinstance(row, dict) else []


def has_distribution(res):
    rows = ((res or {}).get('breakdown') or {}).get('rows') if isinstance(res, dict) else None
    if not isinstance(rows, list):
        return False
    return sum(1 for r in rows if len(row_buckets(r)) >= 3) >= 2


def _font():
    try:
        from matplotlib import font_manager
        for d in _FONT_DIRS:
            if not os.path.isdir(d):
                continue
            added = False
            for fn in os.listdir(d):
                if fn.lower().endswith('.ttf') and 'inter' in fn.lower():
                    font_manager.fontManager.addfont(os.path.join(d, fn))
                    added = True
            if added:
                names = {f.name for f in font_manager.fontManager.ttflist}
                for cand in ('Inter 18pt', 'Inter', 'Crosswalk Inter'):
                    if cand in names:
                        return cand
    except Exception:
        pass
    return 'DejaVu Sans'


def _short_label(s, n=22):
    s = str(s or '')
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def _fmt_people(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ''
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{int(v)}"


def render_distribution(rows, *, title, subtitle='', footer='', x_label='', y_label='Share of each pool'):
    """Small multiples: one histogram per row (service), bars = bucket
    share of that row's pool, the mode in Orchid. PNG bytes."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fam = _font()
    plt.rcParams['font.family'] = fam
    panels = [(r, row_buckets(r)) for r in rows if len(row_buckets(r)) >= 2][:8]
    if not panels:
        return b''
    ncol = 4 if len(panels) > 4 else max(1, len(panels))
    nrow = (len(panels) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.0 * ncol, 3.5 * nrow + 1.7), dpi=170)
    fig.patch.set_facecolor(GRAPHITE)
    axes = list(axes.flat) if hasattr(axes, 'flat') else [axes]
    for ax in axes[len(panels):]:
        ax.axis('off')
    for ax, (row, buckets) in zip(axes, panels):
        ax.set_facecolor(SLATE)
        labels = [_short_label(b['label'], 10) for b in buckets]
        vals = [b['share_pct'] for b in buckets]
        colors = [ORCHID if b.get('mode') else SIGNAL_GREEN for b in buckets]
        if not any(b.get('mode') for b in buckets) and vals:
            colors[vals.index(max(vals))] = ORCHID
        bars = ax.bar(range(len(vals)), vals, color=colors, width=0.72, zorder=3)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, f"{v:.0f}%",
                    ha='center', va='bottom', fontsize=7.2, color=OFF_WHITE)
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels(labels, fontsize=7, color=BODY, rotation=0)
        ax.set_ylim(0, max(vals) * 1.28 if vals else 1)
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(axis='x', length=0)
        pool = ''
        m = re.search(r"pool\s+([\d,]+)", str(row.get('note') or ''), re.I)
        if m:
            pool = f"{_fmt_people(m.group(1).replace(',', ''))} viewers"
        elif row.get('pool'):
            pool = f"{_fmt_people(row.get('pool'))} viewers"
        head = str(row.get('label') or '')
        ax.text(0, 1.13, head, transform=ax.transAxes, fontsize=10.5, color=OFF_WHITE,
                fontweight='bold', ha='left', va='bottom')
        if pool:
            ax.text(0, 1.03, pool, transform=ax.transAxes, fontsize=7.4, color=MUTED, ha='left', va='bottom')
    fig.suptitle(title, x=0.035, y=0.985, ha='left', fontsize=14, color=OFF_WHITE, fontweight='bold')
    if subtitle:
        fig.text(0.035, 0.945, subtitle, ha='left', fontsize=9, color=BODY)
    if x_label or y_label:
        fig.text(0.035, 0.065, f"{x_label}{'  |  ' if x_label and y_label else ''}{y_label}", ha='left', fontsize=8, color=MUTED)
    if footer:
        fig.text(0.035, 0.022, footer, ha='left', fontsize=7.2, color=MUTED, wrap=True)
    fig.text(0.965, 0.022, 'CROSSWALK', ha='right', fontsize=8, color=MUTED, fontweight='bold')
    plt.tight_layout(rect=(0.02, 0.09, 0.98, 0.91), h_pad=3.2)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_bars(rows, *, title, subtitle='', footer='', value_key='share_pct', unit='%'):
    """Horizontal bars for a plain breakdown (label by share). PNG."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = _font()
    data = [(str(r.get('label') or ''), float(r.get(value_key) or 0)) for r in rows
            if isinstance(r, dict) and r.get('label') and r.get(value_key) is not None][:14]
    if len(data) < 2:
        return b''
    data.sort(key=lambda x: x[1])
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(data) + 2.4), dpi=170)
    fig.patch.set_facecolor(GRAPHITE)
    ax.set_facecolor(GRAPHITE)
    labels = [_short_label(d[0], 28) for d in data]
    vals = [d[1] for d in data]
    colors = [SIGNAL_GREEN] * len(vals)
    colors[-1] = ORCHID
    bars = ax.barh(range(len(vals)), vals, color=colors, height=0.68, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(v + max(vals) * 0.012, b.get_y() + b.get_height() / 2, f"{v:.1f}{unit}",
                va='center', fontsize=8, color=OFF_WHITE)
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels(labels, fontsize=8.5, color=BODY)
    ax.set_xticks([])
    ax.set_xlim(0, max(vals) * 1.18)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(axis='y', length=0)
    fig.suptitle(title, x=0.035, y=0.975, ha='left', fontsize=13, color=OFF_WHITE, fontweight='bold')
    if subtitle:
        fig.text(0.035, 0.925, subtitle, ha='left', fontsize=8.6, color=BODY)
    if footer:
        fig.text(0.035, 0.02, footer, ha='left', fontsize=7, color=MUTED)
    fig.text(0.965, 0.02, 'CROSSWALK', ha='right', fontsize=8, color=MUTED, fontweight='bold')
    plt.tight_layout(rect=(0.02, 0.07, 0.98, 0.9))
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def chart_for_read(res, question='', force=False):
    """(png_bytes, filename, alt) for a read that wants or carries a
    visual, else None."""
    try:
        res = res if isinstance(res, dict) else {}
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        rows = [r for r in (bd.get('rows') or []) if isinstance(r, dict)]
        if not rows:
            return None
        if not (force or wants_visual(question) or has_distribution(res)):
            return None
        subject = str(res.get('subject') or '').strip()
        window = str(res.get('window_label') or '').strip()
        if not window and res.get('window_start') and res.get('window_end'):
            window = f"{res['window_start']} to {res['window_end']}"
        try:
            from prometheus import methodology as _meth
            footer = _meth.VIEW_NOTE_SHORT if _meth.mentions_views(
                ' '.join([question, str(bd.get('dimension') or ''), str(bd.get('share_basis') or '')])) else ''
        except Exception:
            footer = ''
        dim = str(bd.get('dimension') or 'Breakdown').strip()
        if has_distribution(res):
            title = f"{subject}: {dim}" if subject else dim
            png = render_distribution(rows, title=title, subtitle=window, footer=footer,
                                      x_label=str(bd.get('bucket_label') or ''), y_label='Share of each pool')
            kind = 'histogram'
        else:
            title = f"{subject}: {dim}" if subject else dim
            png = render_bars(rows, title=title, subtitle=window, footer=footer)
            kind = 'chart'
        if not png:
            return None
        slug = re.sub(r'[^a-z0-9]+', '_', f"{subject}_{dim}".lower()).strip('_')[:70] or 'crosswalk_chart'
        return png, f"{slug}_{kind}.png", f"{title} ({kind})"
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def publish(res, question, user, s3, bucket, expires=7 * 24 * 3600):
    """Render, upload to system/prometheus_charts/, and hand back the
    payload fragment {'chart': {url, alt, filename, s3_key}} or {}."""
    import hashlib
    ch = chart_for_read(res, question=question)
    if not ch:
        return {}
    png, name, alt = ch
    key = "system/prometheus_charts/" + hashlib.sha1(
        f"{user}|{name}|{question}".encode()).hexdigest()[:16] + ".png"
    s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType='image/png')
    url = s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key}, ExpiresIn=expires)
    return {'chart': {'url': url, 'alt': alt, 'filename': name, 's3_key': key}}


def attachments_for(data, s3, bucket):
    """[(png_bytes, filename)] for the chart a payload carries, else []."""
    try:
        ch = (data or {}).get('chart') or {}
        key = str(ch.get('s3_key') or '')
        if not key:
            return []
        png = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
        return [(png, str(ch.get('filename') or 'chart.png'))]
    except Exception:
        return []
