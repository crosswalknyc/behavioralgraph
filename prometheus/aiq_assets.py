"""Adding assets to an Attribution IQ campaign that already exists, from a
sheet on the thread (Alexia, 2026-10-16: uploaded influencer-project-
videos.csv, 1,156 creator clips, and asked "add these assets to the
attribution iq report for the influencer project"; the intake answered
"I still need the campaign URLs, the end step").

The contract:
- "add / append / include these assets (urls, links, videos, posts,
  clips) to the attribution (campaign, report, tracking) for <name>" is
  an ADD to an existing campaign when <name> resolves against the
  registry. Nothing is asked that the campaign already knows: the
  conversion is the campaign's, the window is the campaign's.
- The URLs come from the sheet on the thread when the ask says so or
  names no URLs itself: every link in the file, with the sheet's own
  platform, account, date and tag columns when present.
- Tags: the sheet's tag column; else the campaign's own convention for
  creator posts (earned, with the campaign's official handles tagged
  official). The confirm card says so; a user can say which were paid.
- The add runs as the day's read at the daily refresh price.
"""
from __future__ import annotations

import re

ADD_RX = re.compile(
    r"\b(add|append|attach|include|put|load|upload|import)\b.{0,80}\b(assets?|urls?|links?|videos?|posts?|clips?|rows?|creators?|content)\b"
    r".{0,80}\b(to|into|in|onto)\b.{0,60}\b(attribution|campaign|report|tracking|mta|multi.?touch)\b", re.I | re.S)
_NAME_RX = re.compile(
    r"\b(?:attribution(?: iq)?(?: report| campaign| tracking)?|campaign|report|tracking|mta)\s+(?:for|of|on|called|named)\s+(?P<name>[^.,;\n]{2,80})", re.I)
_THE_RX = re.compile(r"\bthe\s+(?P<name>(?:(?!\bthe\b)[\w'&.: -]){2,60}?)\s+(?:attribution|campaign|report|tracking)\b", re.I)
_STOP = {'the', 'a', 'an', 'of', 'for', 'and', 'x', 'campaign', 'report', 'attribution', 'iq', 'tracking', 'project'}


def is_add_assets_ask(text):
    return bool(ADD_RX.search(' '.join(str(text or '').split())))


def campaign_name_in(text):
    """The campaign the ask names ('... for the influencer project'), or ''."""
    t = ' '.join(str(text or '').split())
    m = _NAME_RX.search(t) or _THE_RX.search(t)
    if not m:
        return ''
    name = m.group('name').strip(' .?!')
    name = re.sub(r"^(?:the|our|my)\s+", '', name, flags=re.I)
    name = re.sub(r"\s+(?:please|now|today|asap)$", '', name, flags=re.I)
    return name


def _tokens(s):
    return [w for w in re.split(r"[^a-z0-9]+", str(s or '').lower()) if w and w not in _STOP]


def resolve_campaign(name, registry):
    """The registry entry whose display name or slug the words point at
    (every distinctive token of the name appears in it), or None. Ties go
    to the most recently updated campaign."""
    toks = _tokens(name)
    if not toks:
        return None
    best = []
    for t in (registry or {}).get('titles') or []:
        hay = set(_tokens(t.get('display_name') or '') + _tokens(t.get('title_slug') or ''))
        if all(tok in hay for tok in toks):
            best.append(t)
    if not best:
        return None
    # the closest name wins ('Chime' is Chime, not Chime Financial MyPay); then the most recently updated
    best.sort(key=lambda t: str(t.get('updated_at') or ''), reverse=True)
    best.sort(key=lambda t: len((set(_tokens(t.get('display_name') or '')) | set(_tokens(t.get('title_slug') or ''))) - set(toks)))
    return best[0]


def campaign_in_text(text, registry):
    """The registry entry the ask points at: the name after 'for' when
    it resolves, else the campaign whose distinctive words appear in the
    ask (Chime vs MyPay: the one with every word present wins)."""
    ent = resolve_campaign(campaign_name_in(text), registry)
    if ent:
        return ent
    words = set(_tokens(text))
    scored = []
    for t in (registry or {}).get('titles') or []:
        toks = set(_tokens(t.get('display_name') or '')) | set(_tokens(t.get('title_slug') or ''))
        hit = [w for w in toks if w in words]
        if hit and len(hit) / max(1, len(toks)) >= 0.5:
            scored.append((len(hit) / max(1, len(toks)), len(hit), str(t.get('updated_at') or ''), t))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return scored[0][3]


def wants_sheet(text):
    """Does the ask point at the file for its URLs ('these assets', 'the
    urls are in the csv', 'from the file', 'attached')?"""
    return bool(re.search(r"\b(these|those|the attached|attached|uploaded|the file|the csv|the sheet|the spreadsheet|in the (?:csv|file|sheet)|from the (?:csv|file|sheet))\b",
                          str(text or ''), re.I))


def sheet_for(recs):
    """The most recent attached sheet that carries links, or None."""
    for r in reversed(list(recs or [])):
        if r.get('kind') == 'sheet' and any((s or {}).get('url_count') for s in (r.get('sheets') or [])):
            return r
    for r in reversed(list(recs or [])):
        if r.get('kind') == 'sheet':
            return r
    return None


def summarize_rows(rows):
    """Counts the confirm card reads out: by platform, by tag, the date span, the accounts."""
    plat, tags, dates, accts = {}, {}, [], set()
    for r in rows or []:
        p = str(r.get('platform') or r.get('channel') or '').strip() or 'Other'
        plat[p] = plat.get(p, 0) + 1
        tg = str(r.get('tag') or '').strip().lower() or 'untagged'
        tags[tg] = tags.get(tg, 0) + 1
        d = str(r.get('posted_date') or '').strip()[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            dates.append(d)
        a = str(r.get('account') or '').strip()
        if a:
            accts.add(a.lower())
    return {'platforms': dict(sorted(plat.items(), key=lambda kv: -kv[1])), 'tags': tags,
            'first': min(dates) if dates else '', 'last': max(dates) if dates else '', 'accounts': len(accts), 'n': len(rows or [])}


def _pretty(iso):
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(iso).strftime('%b %-d, %Y')
    except Exception:
        return str(iso or '')


def confirm_reply(entry, rows, file_name, price_label, run_label='Add the assets'):
    """The confirm card for an add to an existing campaign."""
    s = summarize_rows(rows)
    plats = ', '.join(f"{n:,} {p}" for p, n in list(s['platforms'].items())[:5])
    untagged = s['tags'].get('untagged', 0)
    tagged = {k: v for k, v in s['tags'].items() if k != 'untagged'}
    tag_line = ('- Tagged in the file: ' + ', '.join(f"{v:,} {k}" for k, v in tagged.items()) + '\n') if tagged else ''
    default_line = ('- Tags: creator posts count as earned; the campaign\'s own account counts as official. '
                    'Say which were paid placements and I will tag those paid.\n') if untagged else ''
    span = f" posted {_pretty(s['first'])} to {_pretty(s['last'])}" if s['first'] else ''
    acct = f" from {s['accounts']:,} accounts" if s['accounts'] else ''
    conv = str((entry or {}).get('conversion_event') or '').strip()
    lines = (f"Here's the update to {entry.get('display_name') or entry.get('title_slug')}:\n"
             f"- Add {s['n']:,} assets from {file_name}{acct}{span}\n"
             f"- {plats}\n"
             f"{tag_line}{default_line}"
             + (f"- Conversion stays: {conv}\n" if conv else '')
             + f"\nThe campaign keeps its history; today's read is recomputed with the new assets in it, at the daily refresh price ({price_label}). "
             f"It lands in the Attribution IQ tab and I will confirm here when it is in. Say '{run_label}' to go.")
    return lines


def _slug_words(entry):
    return [t for t in re.split(r"[^a-z0-9]+", str((entry or {}).get('title_slug') or '').lower()) if len(t) >= 4 and t not in ('hades', 'films', 'campaign')]


def tag_for(entry, row):
    """The sheet's tag when it has one; else the campaign's convention for
    creator posts: the campaign's own account is official, everyone else
    is earned."""
    tg = str((row or {}).get('tag') or '').strip().lower()
    if tg in ('paid', 'organic', 'earned', 'official'):
        return tg
    handle = str(row.get('account') or '').strip().lower().lstrip('@').replace('.', '').replace('_', '')
    words = _slug_words(entry)
    if handle and words and all(w in handle for w in words[:2]):
        return 'official'
    return 'earned'


def inputs_for_add(entry, rows, file_name, requested_by=''):
    """The aiq_confirm payload the approve branch runs: an add, not a new campaign."""
    return {'add_to_slug': entry.get('title_slug'), 'campaign_name': entry.get('display_name') or entry.get('title_slug'),
            'conversion_event': str(entry.get('conversion_event') or '') or 'the campaign\'s conversion',
            'urls': [{'url': r.get('url'), 'tag': tag_for(entry, r), 'label': r.get('label') or '',
                      'platform': r.get('platform') or '', 'account': r.get('account') or '', 'posted_date': r.get('posted_date') or ''}
                     for r in rows],
            'daily_refresh': False, 'source_file': file_name, 'requested_by': requested_by}


ASK_COPY = (
    "Happy to set up Attribution tracking. Give me, in one message:\n"
    "1. The campaign name\n"
    "2. Every campaign URL, each tagged paid or organic - paste them "
    "line by line (URL, tag, optional asset name), paste your CSV "
    "with URL / Tag / Label columns, or attach the file\n"
    "3. The conversion - one sentence on what counts (a signup, a "
    "purchase, a ticket, an install)\n"
    "4. Daily tracking on or off. If on: the date tracking should "
    "STOP - you are charged up front through that date\n\n"
    "Example: \"Campaign: Fall Launch. Conversion: signed up on the "
    "landing page. Daily tracking through 2026-10-15.\n"
    "https://youtube.com/watch?v=abc paid Hero spot\n"
    "https://instagram.com/p/xyz organic Launch teaser\"")


def confirm_card(parsed, days, setup_lbl, daily_lbl, usd_label):
    """The confirm card for a NEW campaign (moved out of chat.py 2026-10-16)."""
    urls = parsed.get('urls') or []
    paid = sum(1 for u in urls if str(u.get('tag')).lower() == 'paid')
    org = len(urls) - paid
    lines = ["Here's the Attribution tracking I'll set up:",
             f"- Campaign: {parsed['campaign_name']}",
             f"- {len(urls)} URLs ({paid} paid, {org} organic)" + (f" from {parsed['source_file']}" if parsed.get('source_file') else ''),
             f"- Conversion: {parsed['conversion_event']}"]
    if parsed.get('_tags_defaulted'):
        lines.append("- The file carried no paid / organic tags, so every URL reads organic; say which were paid placements and I will tag those paid.")
    if parsed.get('daily_refresh'):
        end = parsed.get('end_tracking_date')
        try:
            _dl = float(str(daily_lbl).replace('$', '').replace('\u00a3', '').replace(',', ''))
            _sl = float(str(setup_lbl).replace('$', '').replace('\u00a3', '').replace(',', ''))
            total_lbl = usd_label(_sl + _dl * days)
        except Exception:
            total_lbl = f'{setup_lbl} + {days} x {daily_lbl}'
        lines.append(f"- Daily tracking through {end}: {days} prepaid refresh day(s) at {daily_lbl} each")
        lines.append('')
        lines.append(f"Total today: {total_lbl} ({setup_lbl} setup + {days} x {daily_lbl}). The window is prepaid through "
                     f"{end} - tracking stops there on its own, and stopping early does not refund the remaining days.")
    else:
        lines.append('')
        lines.append(f"One-time setup: {setup_lbl}. You can turn on daily tracking later from this chat.")
    lines.append('')
    lines.append("The campaign lands in the Attribution IQ tab with the Multi-Touch read on every URL, and each daily "
                 "refresh adds that day's numbers alongside. Start tracking?")
    return '\n'.join(lines)


_URL_IN_TEXT = re.compile(r"https?://[^\s,;<>\"')\]]+")


def _thread_sheet_rows(body, user, host):
    """(rows, file_name) from the most recent sheet with links on the thread, or ([], '')."""
    try:
        from . import documents as _docs
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip() if isinstance(user, dict) else str(user or '')
        tid = str((body or {}).get('thread_id') or '').strip()
        s3, bucket = getattr(host, 's3_client', None), getattr(host, 'S3_BUCKET', None)
        recs = _docs.attachments(uname, tid, s3, bucket) if uname else []
        rec = sheet_for(recs)
        if not rec:
            return [], ''
        return _docs.sheet_urls(rec, s3, bucket), str(rec.get('name') or '')
    except Exception:
        import traceback
        traceback.print_exc()
        return [], ''


def _registry(host):
    try:
        import json
        s3, bucket = getattr(host, 's3_client', None), getattr(host, 'S3_BUCKET', None) or 'dashboard-inputs'
        if s3 is None:
            import boto3
            s3 = boto3.client('s3')
        return json.loads(s3.get_object(Bucket=bucket, Key='intent/registry.json')['Body'].read().decode('utf-8'))
    except Exception:
        return {}


def intake_turn(text, body, user, history, *, host, price_label, stash=None):
    """The add-to-existing turn, or None when the ask is not one (the
    normal intake runs). A confirm card with the aiq_confirm payload when
    the campaign resolves and the thread (or the message) carries the
    URLs; a plain ask for the file when it does not."""
    # the ask itself, or the add this open intake started with ("the urls
    # are in the csv file" answering "I still need the campaign URLs")
    joined = text
    if not is_add_assets_ask(text):
        prior = [str(h.get('text') or '') for h in (history or [])[-6:] if isinstance(h, dict) and str(h.get('role') or '') == 'user']
        joined = '\n'.join(prior + [str(text or '')])
        if not (prior and is_add_assets_ask(joined) and (wants_sheet(text) or _URL_IN_TEXT.search(str(text or '')))):
            return None
    entry = campaign_in_text(joined, _registry(host))
    if not entry:
        return None
    rows, file_name = _thread_sheet_rows(body, user, host)
    pasted = [{'url': u.rstrip('.,;)'), 'tag': ''} for u in _URL_IN_TEXT.findall(str(text or ''))]
    if pasted and not rows:
        rows, file_name = pasted, 'your message'
    name = entry.get('display_name') or entry.get('title_slug')
    if not rows:
        return {'reply': f"I have {name}. Attach the CSV with the URLs (or paste them) and I will add every one to the campaign.",
                'aiq_collect': True, 'followups': ['Cancel']}
    uname = str((user or {}).get('username') or '') if isinstance(user, dict) else str(user or '')
    payload = inputs_for_add(entry, rows, file_name, requested_by=uname)
    if stash:
        try:
            stash('stash', user, 'attribution', payload, 'Add the assets')
        except Exception:
            pass
    return {'reply': confirm_reply(entry, rows, file_name, price_label), 'aiq_confirm_payload': payload,
            'followups': ['Add the assets', 'Cancel']}


def with_sheet_urls(parse_fn, body, user, text, *, host):
    """The attribution parser with the sheet on the thread as its URL source
    when the message names none (a new campaign set up from an attached
    file). Tags come from the sheet; without a tag column every URL reads
    organic and the card says so."""
    def _parse(t, usage_extras=None):
        parsed = parse_fn(t, usage_extras=usage_extras) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        tagged = [u for u in (parsed.get('urls') or []) if isinstance(u, dict) and u.get('url') and str(u.get('tag') or '').lower() in ('paid', 'organic')]
        if tagged or (not wants_sheet(text) and _URL_IN_TEXT.search(str(text or ''))):
            return parsed
        rows, file_name = _thread_sheet_rows(body, user, host)
        if not rows:
            return parsed
        if not any(r.get('tag') in ('paid', 'organic') for r in rows):
            parsed['_tags_defaulted'] = True
        parsed['urls'] = [{'url': r['url'], 'tag': (r.get('tag') if r.get('tag') in ('paid', 'organic') else 'organic'),
                           'label': r.get('label') or r.get('account') or ''} for r in rows]
        parsed['source_file'] = file_name
        parsed['missing'] = [k for k in ('campaign_name', 'urls', 'conversion_event') if not parsed.get(k)]
        return parsed
    return _parse


__all__ = ['ADD_RX', 'ASK_COPY', 'confirm_card', 'intake_turn', 'with_sheet_urls', 'is_add_assets_ask', 'campaign_name_in', 'resolve_campaign', 'campaign_in_text', 'wants_sheet', 'sheet_for',
           'summarize_rows', 'confirm_reply', 'inputs_for_add', 'tag_for']
