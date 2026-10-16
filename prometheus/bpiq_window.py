"""The campaign context a Brand Partnership ask carries (2026-10-16,
Jenna, MMG x Lowe's: "my ask was so explicit"): the named initiative,
the partner's own links, a relative period. The intake reader turns
them into a proposed campaign window (prometheus.intake_reader
.resolve_campaign_window); this module holds the research call that
dates a named flight and the confirm-card lines.
"""
from __future__ import annotations

import json
import re
import traceback

FLIGHT_PROMPT = """You date a brand's named marketing campaign from public sources (web search).
Return STRICT JSON only: {"start": "YYYY-MM-DD"|null, "end": "YYYY-MM-DD"|null, "basis": str}.
start = the campaign's public launch date. end = the date the flight ended; when the brand said it ran
"throughout the season" or similar, use that season's final date (an NFL season ends with the Super Bowl).
A still-running campaign has end null. Never guess: when the sources do not date the campaign, return nulls."""

_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def flight_research(brand, campaign, qualifier='', *, claude_data=None):
    """{'start','end','basis'} for a brand's named initiative, dated by one
    web-search call, or None on any failure (the window resolver then
    falls back to the ask's own period). `claude_data(system, user,
    **kw)` is the chat's JSON model call."""
    if not (brand and campaign and claude_data):
        return None
    try:
        import prometheus_analysis as _pma
        for attempt in (1, 2):   # a second look when the first search came back undated
            out = claude_data(FLIGHT_PROMPT, json.dumps({'brand': brand, 'campaign': campaign, 'partner': qualifier}),
                              max_tokens=600, temperature=0.0 if attempt == 1 else 0.3, surface='bpiq_flight', tools=[_pma.WEB_SEARCH_TOOL])
            if isinstance(out, dict) and _ISO.match(str(out.get('start') or '')):
                end = str(out.get('end') or '')
                return {'start': out['start'], 'end': end if _ISO.match(end) else None, 'basis': str(out.get('basis') or '')}
    except Exception:
        traceback.print_exc()
    return None


def link_host(url):
    m = re.search(r'https?://(?:www\.)?([^/]+)', str(url or ''))
    return (m.group(1) if m else str(url or ''))[:40]


def context_lines(parsed):
    """The confirm-card lines for the campaign and the partner links, or ''."""
    p = parsed or {}
    out = ''
    if p.get('campaign'):
        out += f"- Campaign: {p['campaign']}\n"
    links = [str(u) for u in (p.get('links') or []) if u]
    if links:
        hosts = list(dict.fromkeys(link_host(u) for u in links))
        out += f"- Partner links: {len(links)} ({', '.join(hosts[:4])})\n"
    return out


__all__ = ['FLIGHT_PROMPT', 'flight_research', 'link_host', 'context_lines']
