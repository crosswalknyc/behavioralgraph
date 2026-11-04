"""Derived read: a figure already delivered, split by a dimension the panel
observes (device, daypart, geography, demographic).

Jenna, 2026-10-12 (Jordan / East Tree, three unanswered asks for The
Office US hours by device): "it cant be page dependent by default
ever" and "jordan should be charged 100gbp for the device breakdown".

Two rules live here:

1. The page is context, never a gate. The subject and the figures come
   from the ask first, then the thread, then whatever is open on
   screen. The same question through the API, with nothing open, gets
   the same read. When nothing names the subject the lane asks for it
   (no charge, no dead end).
2. A derived read is priced at the derived-cut price (100 in the
   seat's currency, rate card line 'Breakdown of a delivered figure').
   The offer states the price; the charge lands on confirm; then the
   read runs. The read core carries the pricing (``kind='derived'``).

``plan`` is pure (no I/O, no model call) so it is testable alone; the
analyze core calls it before anything page-shaped runs.
"""
from __future__ import annotations

import re


def _article(name, text):
    """Keep the article the reader wrote ("The Office US")."""
    if not name:
        return name
    m = re.search(r"\b(?:The|A|An)\s+" + re.escape(name) + r"\b", str(text or ''), re.I)
    return m.group(0) if m else name


BRIEF_WORDS = 60


def is_brief(text):
    """A multi-part ask (a brief) rather than a one-line question: sixty
    words or more, or three or more lines or numbered items."""
    t = str(text or '')
    if len(t.split()) >= BRIEF_WORDS:
        return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    items = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+", t)
    return len(lines) >= 3 or len(items) >= 3


def plan(text, history, ctx, body, *, pma, ns, normalize, stop, plausible, consumption):
    """None when the ask is not a derived read; otherwise

        {'subject': str, 'dimension': str, 'series': [(label, int)],
         'metric_request': {...}, 'panel_confirm': dict | None,
         'ask': payload | None}

    ``ask`` is set (and the rest still filled) when no subject could be
    found anywhere: the caller returns it instead of running.

    ns is prometheus.named_subject; normalize / stop / plausible /
    consumption are the host's matcher pieces (the same ones every
    other lane binds subjects with).
    """
    def named_subject(t):
        return (consumption(t) or ns.mentioned_subject(t, '', normalize, stop, plausible) or '')

    def thread_subject(h):
        return ns.thread_subject(h, normalize, stop, plausible, consumption)

    pc = body.get('panel_confirm') if isinstance(body, dict) and isinstance(body.get('panel_confirm'), dict) else None
    confirm_derived = bool(pc and str(pc.get('kind') or '') == 'derived')
    if not (confirm_derived or pma.is_dimension_split_ask(text)):
        return None
    # every dimension the ask names, in the reader's own words ('age, gender, ethnicity, household income
    # and region'), never the first table hit alone (Jay Patel 2026-11-04 was offered 'by geography')
    dim = str((pc or {}).get('dimension') or '').strip().lower()
    if not dim:
        dims = pma.split_dimensions(text) if hasattr(pma, 'split_dimensions') else []
        dim = (pma.dimension_phrase(dims, text) if dims else '') or pma.split_dimension(text) or 'device'

    # subject: the ask, then the confirm / bind, then the thread, then the page
    subject = ''
    try:
        subject = _article(str(named_subject(text) or '').strip(), text)
    except Exception:
        subject = ''
    if not subject and isinstance(body, dict):
        subject = str(body.get('bind_subject') or '').strip()
    if not subject and pc:
        subject = str(pc.get('subject') or '').strip()
    if not subject:
        try:
            subject = str(thread_subject(history) or '').strip()
        except Exception:
            subject = ''
    if not subject and isinstance(ctx, dict):
        vc = (ctx.get('view_context') or {}).get('data') or {}
        subject = (str(vc.get('show') or vc.get('title') or '').strip()
                   or str((ctx.get('primary') or {}).get('name') or '').strip())

    # figures: pasted into the ask, else the thread's delivered read
    series = []
    try:
        series = list(pma.extract_pasted_series(text) or [])
        if not series:
            series = list(pma.series_from_history(history) or [])
    except Exception:
        series = []

    # A derived read splits a figure the reader HOLDS. A brief that names
    # no subject and carries no figure (nothing pasted, nothing delivered
    # in the thread) is a new question for the other lanes, not a split
    # waiting for its subject (Emma Friedenberg, 2026-11-04: a 300-word
    # launch-day brief drew "which title are the figures for?" twice).
    if not confirm_derived and not subject and not series and is_brief(text):
        return None

    out = {
        'subject': subject, 'dimension': dim, 'series': series,
        'panel_confirm': pc,
        'metric_request': {
            'subject': subject, 'metric_family': 'dimension split',
            'dimension': dim, 'bound_series': series,
            'needed': (f"the figure the reader holds split by {dim}, one row per period "
                       "with buckets that sum exactly to it, plus the whole-window total")},
        'ask': None,
    }
    if not subject:
        out['ask'] = {
            'success': True, 'action': 'answer',
            'reply': (f"Which title or audience are the figures for? Name it, or paste the "
                      f"figures, and I will split them by {dim}."),
            'followups': [], 'offer_deck': False, 'deck_angle': None}
    return out


__all__ = ['plan', 'is_brief', 'BRIEF_WORDS']
