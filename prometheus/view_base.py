"""The open product is the base (2026-10-22, Alexia on The Influencer
Project).

With an Attribution IQ campaign on screen, a question about "the
Attribution report of The Influencer Project" was answered from the
Profile IQ file "Potential The Influencer Project Ticket Buyer"
(12,004,203 US people, a Jan to Dec 2025 audience read) instead of the
campaign payload on screen (6,671,723 exposed, 67,729 ticketing
visits). Two datasets crossed in one reply.

The rule: a self-contained product view (Attribution IQ, Digital
Journey IQ, Brand Partnership IQ) IS the base for every ask that refers
to it or names its own title. A same-title profile in the library is
context at most and is never mixed into the numbers. The counting block
comes from the open product's own domain.

This module holds the pieces chat.py calls:
- which views are self-contained and the vocabulary they carry
- whether an ask names the open view's own title or product
- the view base record (no s3 key, never a profile)
- the on-screen data block the reasoned read answers from
- the product's own counting block (attribution: window, exposed base,
  conversion definition, assets tracked)
"""
from __future__ import annotations

import json
import re

# view ids the widget sends for the self-contained products
SELF_CONTAINED = frozenset((
    'intentIQ', 'attributionIQ', 'attribution_iq',
    'journeyIQ', 'journey_iq',
    'brandPartnershipIQ', 'brand_partnership_iq',
    'digitalTwinsIQ', 'digital_twins_iq'))

_PRODUCT_NOUN = {
    'intentIQ': ('Attribution IQ', 'campaign'),
    'attributionIQ': ('Attribution IQ', 'campaign'),
    'attribution_iq': ('Attribution IQ', 'campaign'),
    'journeyIQ': ('Digital Journey IQ', 'journey'),
    'journey_iq': ('Digital Journey IQ', 'journey'),
    'brandPartnershipIQ': ('Brand Partnership IQ', 'partnership'),
    'brand_partnership_iq': ('Brand Partnership IQ', 'partnership'),
    'digitalTwinsIQ': ('Digital Twins IQ', 'twin'),
    'digital_twins_iq': ('Digital Twins IQ', 'twin'),
}

# words that name the product itself ("the Attribution report", "this
# campaign's journey"); one hit with the product open is the product
_PRODUCT_RX = {
    'campaign': re.compile(r"\b(?:attribution(?:\s+iq|\s+report|\s+dashboard|\s+data)?|campaign|touch[\s-]?points?|assets?|creatives?|phases?)\b", re.I),
    'journey': re.compile(r"\b(?:journey(?:\s+iq)?|digital\s+journey|funnel|steps?|stages?)\b", re.I),
    'partnership': re.compile(r"\b(?:brand\s+partnership(?:\s+iq)?|partnership|valuation|sponsorship)\b", re.I),
    'twin': re.compile(r"\b(?:digital\s+twins?(?:\s+iq)?|twin|greenlight\s+lab|launch\s+lab|spend\s+engine|panel\s+lab|the\s+room|lifetime\s+value|odds|levers?|runs?)\b", re.I),
}


def is_self_contained(view_id):
    return str(view_id or '').strip() in SELF_CONTAINED


def domain_for(view_id):
    """'attribution' | 'journey' | 'partnership' | '' for the counting block."""
    kind = (_PRODUCT_NOUN.get(str(view_id or '').strip()) or ('', ''))[1]
    return {'campaign': 'attribution', 'journey': 'journey', 'partnership': 'partnership'}.get(kind, '')


def product_label(view_id):
    return (_PRODUCT_NOUN.get(str(view_id or '').strip()) or ('', ''))[0]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


# Words a view title shares with ordinary asks about any campaign, title
# or audience. They never count toward naming the open view by tokens;
# the whole title still does.
GENERIC_TITLE_WORDS = frozenset((
    'first', 'second', 'third', 'last', 'next', 'new', 'where', 'when', 'what', 'who', 'how', 'why', 'came',
    'come', 'from', 'with', 'into', 'their', 'they', 'this', 'that', 'these', 'those', 'there', 'here',
    'audience', 'audiences', 'viewer', 'viewers', 'people', 'fans', 'users', 'customers', 'subscribers',
    'episode', 'episodes', 'premiere', 'finale', 'season', 'seasons', 'week', 'weeks', 'weekend', 'opening',
    'launch', 'launched', 'campaign', 'campaigns', 'conversion', 'conversions', 'convert', 'converted',
    'trailer', 'trailers', 'review', 'reviews', 'clip', 'clips', 'post', 'posts', 'social', 'search',
    'journey', 'journeys', 'path', 'read', 'report', 'reports', 'data', 'profile', 'profiles', 'title',
    'titles', 'watch', 'watched', 'watching', 'stream', 'streaming', 'signup', 'signups', 'subscribe',
    'subscribers', 'ticket', 'tickets', 'ticketing', 'sales', 'live', 'night', 'day', 'days', 'year',
    'years', 'month', 'months', 'part', 'window', 'lift', 'value', 'brand', 'brands', 'partnership',
    'attribution', 'flywheel', 'subscriber', 'digital', 'youtube', 'tiktok', 'instagram', 'amazon',
    'netflix', 'hulu', 'peacock', 'paramount', 'prime', 'video', 'premium', 'free', 'paid',
))


def title_family(view_title):
    """'The Influencer Project - Hades' -> 'The Influencer Project'."""
    t = str(view_title or '').strip()
    return t.split(' - ')[0].strip() if t else ''


def _slug_words(view_ctx):
    """Readable words from the campaign slug the widget sends as data.title
    ('the_influencer_project_hades' -> 'the influencer project hades')."""
    data = (view_ctx or {}).get('data') or {}
    slug = str(data.get('title') or data.get('campaign') or '')
    return _norm(slug.replace('_', ' ').replace('-', ' '))


def names_view(text, view_ctx, product_nouns=True):
    """True when the words name the open view's own title (its family,
    before any ' - ' suffix) or, with `product_nouns`, the product itself
    ("the Attribution report", "this campaign"). Shared ordinary words
    are not a name: at least two distinctive title tokens must appear, or
    the whole family when it is one token."""
    vc = view_ctx if isinstance(view_ctx, dict) else {}
    view_id = str(vc.get('view_id') or '').strip()
    if view_id not in SELF_CONTAINED:
        return False
    t = _norm(text)
    if not t:
        return False
    fam = _norm(title_family(vc.get('view_title')))
    stop = {'the', 'a', 'an', 'of', 'and', 'on', 'in', 'for', 'to', 'iq', 'project', 'series', 'film', 'movie', 'show'}
    for cand in (fam, _slug_words(vc)):
        toks = [w for w in cand.split() if w not in stop and len(w) > 2]
        if not toks:
            continue
        if cand and f" {cand} " in f" {t} ":
            return True
        # Only distinctive title words count as naming the view. A
        # journey titled "NCIS: New York premiere, where the first-episode
        # audience came from" shares 'first' and 'audience' with an ask
        # about Colin and Samir's Lexus campaign; those are the product's
        # own words, not the title (Debi Pomerantz, 2026-10-21).
        distinct = [w for w in toks if w not in GENERIC_TITLE_WORDS and len(w) > 3]
        hits = [w for w in distinct if re.search(rf"\b{re.escape(w)}\b", t)]
        if hits and (len(hits) >= 2 or len(hits) == len(distinct)):
            return True
    if not product_nouns:
        return False
    kind = (_PRODUCT_NOUN.get(view_id) or ('', ''))[1]
    rx = _PRODUCT_RX.get(kind)
    return bool(rx and rx.search(str(text or '')))


def view_base(view_ctx):
    """The base record for a self-contained view: the product and its
    title, no s3 key, never a profile. None when the view is not one."""
    vc = view_ctx if isinstance(view_ctx, dict) else {}
    view_id = str(vc.get('view_id') or '').strip()
    if view_id not in SELF_CONTAINED:
        return None
    title = str(vc.get('view_title') or '').strip() or product_label(view_id)
    return {'subject': title, 's3_key': '', 'source': 'view',
            'view_id': view_id, 'product': product_label(view_id),
            'domain': domain_for(view_id)}


def is_view_base(base):
    return isinstance(base, dict) and base.get('source') == 'view'


def data_block(view_ctx, max_chars=14000):
    """The on-screen data as the prompt's data block. The reasoned read
    answers from this and nothing else when the base is a view."""
    vc = view_ctx if isinstance(view_ctx, dict) else {}
    view_id = str(vc.get('view_id') or '').strip()
    title = str(vc.get('view_title') or '').strip()
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    try:
        body = json.dumps(data, ensure_ascii=False, default=str, indent=0)
    except Exception:
        body = str(data)
    if len(body) > max_chars:
        body = body[:max_chars].rsplit('\n', 1)[0] + '\n...'
    head = (f"DATA ON SCREEN ({product_label(view_id) or 'open view'}: {title}). "
            "This is the base. Every figure in the answer comes from these "
            "numbers; a profile of a similar name in the library is a different "
            "dataset and is never mixed in.")
    return head + '\n' + body


def counting_lines(view_ctx):
    """The product's own counting block lines (header first) for a
    self-contained view; [] when the view carries no numbers."""
    vc = view_ctx if isinstance(view_ctx, dict) else {}
    view_id = str(vc.get('view_id') or '').strip()
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    dom = domain_for(view_id)
    title = title_family(vc.get('view_title')) or str(vc.get('view_title') or '').strip()
    if dom == 'attribution':
        cn = data.get('campaign_numbers') if isinstance(data.get('campaign_numbers'), dict) else {}
        lines = ['How these numbers are counted:']
        title = title if title.lower().startswith('the ') else f"the {title}"   # "the The Influencer Project" never
        win = str(cn.get('window') or data.get('window') or '').strip()
        if win:
            lines.append(f"- Window: {win}.")
        exp = cn.get('exposed_us_people')
        if isinstance(exp, (int, float)) and exp:
            lines.append(f"- Base: {title} campaign, {int(exp):,} US people who saw at least one of its assets, projected to the US.")
        else:
            lines.append(f"- Base: {title} campaign, the US people who saw at least one of its assets, projected to the US.")
        conv = re.sub(r"\b(\d+)\s*d\b", r"\1 days", str(cn.get('conversion_definition') or '').strip())
        if conv:
            lines.append(f"- Conversion: {conv[0].lower() + conv[1:]}.")
        assets = cn.get('assets_tracked')
        if isinstance(assets, (int, float)) and assets:
            lines.append(f"- Assets: {int(assets):,} tracked across {', '.join(str(p.get('platform')) for p in (cn.get('by_platform') or [])[:6] if isinstance(p, dict) and p.get('platform')) or 'the campaign channels'}.")
        return lines if len(lines) > 1 else []
    return []


def _phase_table(assets, formats):
    """Per-phase asset counts, posted spans and the tagging rule (phase =
    the asset's posted date against the phase start dates), plus the
    creative format mix per channel, from the campaign's own asset list.
    {} when the list is empty."""
    rows = [a for a in (assets or []) if isinstance(a, dict)]
    if not rows:
        return {}
    by_phase = {}
    for a in rows:
        ph = str(a.get('phase_name') or 'Unphased').strip() or 'Unphased'
        d = str(a.get('posted_date') or '')[:10]
        p = by_phase.setdefault(ph, {'phase': ph, 'assets': 0, 'first_posted': '', 'last_posted': '', 'by_asset_type': {}})
        p['assets'] += 1
        if d:
            p['first_posted'] = min(p['first_posted'] or d, d)
            p['last_posted'] = max(p['last_posted'], d)
        at = str(a.get('asset_type') or 'Other').strip() or 'Other'
        p['by_asset_type'][at] = p['by_asset_type'].get(at, 0) + 1
    phases = sorted(by_phase.values(), key=lambda p: (p['first_posted'] or '9999', p['phase']))
    for p in phases:
        p['by_asset_type'] = dict(sorted(p['by_asset_type'].items(), key=lambda kv: -kv[1])[:8])
    out = {'phases': phases}
    if len(phases) >= 2 and phases[1]['first_posted']:
        out['phase_rule'] = (f"Each asset is tagged to a phase by the date it posted: assets posted before "
                             f"{phases[1]['first_posted']} are {phases[0]['phase']}; assets posted from "
                             f"{phases[1]['first_posted']} on are {phases[1]['phase']}.")
    fm = formats if isinstance(formats, dict) else {}
    if fm:
        by_ch = {}
        for a in rows:
            f = (fm.get(str(a.get('asset_id') or '')) or {})
            label = str(f.get('format') or '').strip()
            if not label:
                continue
            ch = str(a.get('channel') or 'Other').strip() or 'Other'
            by_ch.setdefault(ch, {})
            by_ch[ch][label] = by_ch[ch].get(label, 0) + 1
        if by_ch:
            out['formats_by_channel'] = {ch: dict(sorted(v.items(), key=lambda kv: -kv[1])) for ch, v in by_ch.items()}
            out['format_rule'] = ("Every asset carries one creative format (what the clip is: a trailer or clip cut, a reaction or "
                                  "review, an interview, a giveaway or call to action, a vlog, a meme or text post, a skit, a "
                                  "challenge, behind the scenes, or other) alongside its asset type (official, paid creator, "
                                  "earned creator, press, exhibitor) and channel.")
    return out


def intent_vocabulary(doc, assets=None, formats=None, phases_in=None):
    """Compact creative table + the campaign's own words, from the
    coefficients doc (and the asset list when given). Rides under
    data.campaign_numbers so an ask that uses the screen's labels
    (phases, formats, channels, asset types) is the screen's ask, and so
    the read answers from the real table."""
    out = {}
    try:
        out.update(_phase_table(assets, formats))
        ov = (doc or {}).get('overall') or {}
        ex = (doc or {}).get('exposure') or ov.get('exposure') or {}
        if isinstance(ex.get('us_people'), (int, float)):
            out['exposed_us_people'] = int(ex['us_people'])
        if isinstance(ex.get('assets'), (int, float)):
            out['assets_tracked'] = int(ex['assets'])
        if isinstance(ex.get('views'), (int, float)):
            out['views'] = int(ex['views'])
        if isinstance(ex.get('by_platform'), list):
            out['by_platform'] = [{'platform': p.get('platform'), 'us_people': p.get('us_people'),
                                   'share_of_exposed_pct': p.get('share_of_exposed'), 'assets': p.get('assets'),
                                   'views': p.get('views')}
                                  for p in ex['by_platform'][:8] if isinstance(p, dict)]
        nest = ((ov.get('paths') or {}).get('nest') or [])
        for n in nest:
            if isinstance(n, dict) and n.get('stage') == '4_paid' and isinstance(n.get('us_accounts'), (int, float)):
                out['ticketing_us_accounts'] = int(n['us_accounts'])
                out['conversion_definition'] = str(n.get('label') or '').strip() or 'went to a ticketing site or app for a ticket within 7 days'
        # The campaign's own window rides with its numbers (2026-10-26): its
        # first phase start (else first posted asset) through the latest
        # read, so the counting block and a read on this base never show a
        # profile's window or a default.
        try:
            from . import campaign_drilldown as _cd
            _win = _cd.window_of({'assets_doc': {'phases': phases_in or [], 'assets': assets or []},
                                  'as_of': str((doc or {}).get('as_of') or '')[:10], 'key': ''})
            if _win.get('label'):
                out['window'] = _win['label']
                out['window_start'], out['window_end'] = _win['start'], _win['end']
        except Exception:
            pass
        cr = ov.get('creative') or {}
        dims = []
        for d in (cr.get('dimensions') or []):
            if not isinstance(d, dict):
                continue
            rows = []
            for r in (d.get('rows') or [])[:14]:
                if not isinstance(r, dict):
                    continue
                rows.append({'label': r.get('label'), 'assets': r.get('assets'), 'views': r.get('views'),
                             'share_of_exposed_pct': r.get('share_of_exposed'),
                             'conversion_rate_pct': (round(float(r['conversion_rate']) * 100, 2)
                                                     if isinstance(r.get('conversion_rate'), (int, float)) else None),
                             'index_vs_campaign': r.get('index_vs_cohort'),
                             'share_of_converters_pct': r.get('share_of_converters'),
                             'enough_to_call': r.get('enough_to_call', True)})
            if rows:
                dims.append({'dimension': str(d.get('label') or ''), 'rows': rows})
        if dims:
            out['creative'] = dims
            if cr.get('note'):
                out['creative_note'] = str(cr['note'])[:600]
        words = set()
        for p in out.get('phases') or []:
            words.add(str(p.get('phase') or ''))
            words.update(str(k) for k in (p.get('by_asset_type') or {}))
        for ch, mix in (out.get('formats_by_channel') or {}).items():
            words.add(str(ch))
            words.update(str(k) for k in mix)
        for d in dims:
            words.add(d['dimension'])
            for r in d['rows']:
                if r.get('label'):
                    words.add(str(r['label']))
        for p in out.get('by_platform') or []:
            if p.get('platform'):
                words.add(str(p['platform']))
        for t in (ov.get('touchpoints') or [])[:120]:
            if isinstance(t, dict):
                for k in ('phase', 'asset_type', 'channel'):
                    if t.get(k):
                        words.add(str(t[k]))
        if words:
            out['vocabulary'] = sorted(words)[:120]
    except Exception:
        return out
    return out
