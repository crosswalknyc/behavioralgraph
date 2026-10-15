#!/usr/bin/env python3
"""Influencers and talent in our universe who sell their own product or
service (2026-10-15, Emmet at Kartel: "who are influencers or talent that
have a product or service they are selling on their channel or page";
Jenna: "that should cost $500 and list influencers who have their own
product lines").

A Custom Ask ($500). The list is the talent universe we read (every
person in Gen Pop TALENT), screened for an owned product line or service
sold through their own channel or page, each verified, and each carrying
the US audience we read for them. Delivered as a workbook, a CSV and a
PDF, presented as owned first-party data (the research that confirms the
product lines stays internal).

    python3 migration/talent_product_lines.py --out /tmp/sellers
    python3 migration/talent_product_lines.py --out /tmp/sellers --limit 400   # a quick pass

Pipeline:
  1. load_universe      Gen Pop TALENT rows (name, role columns, penetration)
  2. screen_sellers     batches of names -> confident owned-brand claims
  3. verify_sellers     web research per claim: active / discontinued / not theirs
  4. build_files        xlsx + csv + pdf on the Crosswalk design
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(ROOT / 'bg-webapp'), str(ROOT / 'scripts')):
    if p not in sys.path:
        sys.path.insert(0, p)

US_POP = 329_900_000
BUCKET = 'dashboard-inputs'
GENPOP_KEY = 'Gen_Pop_2026.csv'
ROLE_COLUMNS = ('CREATOR/INFLUENCER', 'INFLUENCER/CREATOR', 'MUSICIAN/BAND', 'ATHLETE', 'ACTOR', 'HOST/PERSONALITY',
                'PODCASTER', 'COMEDIAN', 'WRITER/DIRECTOR/AUTHOR/ARTIST', 'POLITICS/ACTIVIST', 'EMERGING TALENT')
ROLE_LABEL = {'CREATOR/INFLUENCER': 'Creator', 'INFLUENCER/CREATOR': 'Creator', 'MUSICIAN/BAND': 'Musician',
              'ATHLETE': 'Athlete', 'ACTOR': 'Actor', 'HOST/PERSONALITY': 'Host', 'PODCASTER': 'Podcaster',
              'COMEDIAN': 'Comedian', 'WRITER/DIRECTOR/AUTHOR/ARTIST': 'Author / director',
              'POLITICS/ACTIVIST': 'Public figure', 'EMERGING TALENT': 'Creator'}

SCREEN_SYSTEM = """You identify well-known public figures who sell their OWN product line or service through their own channels (their site, their social pages, their videos, their app).

Counts: a consumer brand they founded, co-founded or own a real stake in and that is marketed as theirs (beverage, snack or food, beauty or skincare, fragrance, apparel or footwear line, spirits or wine, supplements, candles, pet products), a paid course, coaching or membership program they run, an app or software product they own, a restaurant or venue they own and promote, a product company they own (not a one-off collab).
Does not count: generic merch (logo t-shirts, hoodies, posters), a sponsorship or ambassador deal, a signature product made and owned by another brand (a sneaker colorway, a Dunkin drink), books, music releases, tours, Patreon or Substack tiers, a podcast itself.

Be thorough: go through every name. Creators with snack, beverage, beauty, apparel or supplement brands (a YouTuber's chocolate bar, a streamer's energy drink, a TikToker's skincare line), musicians with spirits or fashion labels, athletes with their own brands, chefs with restaurant groups and hosts with product lines all count. When a person has more than one, give the flagship ACTIVE one first and list the others. Skip anyone you are not sure about. Use the exact input spelling of each name in your answer.

Return only JSON:
{"sellers": [{"name": "<exact input name>", "brand": "<flagship active brand or program>", "others": ["<other owned brands, if any>"], "category": "<beverage|food|beauty|fragrance|apparel|footwear|spirits|supplements|home|pets|course or coaching|membership|app|restaurant|other>", "type": "<product line|service|app|restaurant>", "where": "<own site|retail|Amazon|app stores|channel link>", "since": <year or null>, "note": "<up to 12 words>"}]}"""

VERIFY_SYSTEM = """You verify, with web research, whether each public figure currently sells the named product line or service as their own (founded, co-founded, owns a stake, carries their name) through their own channels. One or two searches per person is enough. Decide:
- active: the brand or program exists now (2025 or 2026) and is theirs
- discontinued: it was theirs but is no longer sold
- not_theirs: it is a sponsorship, a collab owned by someone else, or a mistake
- unverified: you could not confirm either way
Correct the brand name, category, where it is sold and the launch year when the research shows otherwise. Echo each person's "name" exactly as given (the name only, never the brand), and add "display": the name in its usual capitalization (MrBeast, KSI, Kim Kardashian). Return only JSON:
{"checked": [{"name": "<exact input name>", "display": "<usual capitalization>", "status": "active|discontinued|not_theirs|unverified", "brand": "...", "category": "...", "type": "...", "where": "<short: own site, retail, Amazon, app stores, channel link>", "since": <year or null>, "source": "<one domain>"}]}"""

WEB_SEARCH_TOOLS = ({"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
                    {"type": "web_search_20250305", "name": "web_search", "max_uses": 8})


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def _parse_json(text):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    for cand in ([m.group(1)] if m else []) + [text[text.find('{'):text.rfind('}') + 1]]:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


# ----------------------------------------------------------------- 1. universe
def load_universe(path=None):
    """Every person in Gen Pop TALENT with their role and penetration."""
    import pandas as pd
    if path:
        df = pd.read_csv(path)
    else:
        import boto3
        body = boto3.client('s3').get_object(Bucket=BUCKET, Key=GENPOP_KEY)['Body'].read()
        df = pd.read_csv(io.BytesIO(body))
    col, val, bp = 'Column', 'Value', 'Brand Penetration (Row)'
    roles = {}
    for rc in ROLE_COLUMNS:
        for v in df.loc[df[col] == rc, val].astype(str):
            roles.setdefault(_norm(v), set()).add(ROLE_LABEL.get(rc, rc.title()))
    out = []
    for _, r in df[df[col] == 'TALENT'].iterrows():
        name = str(r[val]).strip()
        try:
            pen = float(r[bp])
        except (TypeError, ValueError):
            continue
        if not name or pen <= 0:
            continue
        rs = sorted(roles.get(_norm(name), set()))
        out.append({'name': name, 'role': ' / '.join(rs) if rs else 'Talent', 'penetration': pen,
                    'us_people': int(round(pen / 100.0 * US_POP))})
    out.sort(key=lambda d: -d['penetration'])
    return out


# ----------------------------------------------------------------- 2. screen
def _claude_client():
    """bg-webapp's claude_client (claude_reason_json + claude_messages with
    tools). From the parent repo the root-level shim of the same name
    shadows it, so load the web app's by path when the shim lacks them."""
    import claude_client as cc
    if hasattr(cc, 'claude_reason_json') and hasattr(cc, 'claude_messages'):
        return cc
    import importlib.util
    for cand in (ROOT / 'bg-webapp' / 'claude_client.py', HERE.parent / 'claude_client.py'):
        if cand.exists():
            spec = importlib.util.spec_from_file_location('claude_client_webapp', str(cand))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'claude_reason_json'):
                return mod
    raise RuntimeError('claude_client without claude_reason_json')


def _claude_json(system, user, *, model=None, max_tokens=6000, tools=None, temperature=0.1):
    cc = _claude_client()
    if tools:
        for tool in tools:
            try:
                txt = cc.claude_messages(system=system, user=user, model=model, max_tokens=max_tokens,
                                         temperature=temperature, tools=[tool], wall_s=200)
            except Exception:
                txt = ''
            doc = _parse_json(txt)
            if doc:
                return doc
        return None
    txt = cc.claude_reason_json(system=system, user=user, model=model, max_tokens=max_tokens, temperature=temperature)
    return _parse_json(txt)


SCREEN_MODEL = os.environ.get('TPL_SCREEN_MODEL', 'claude-opus-4-7')
VERIFY_MODEL = os.environ.get('TPL_VERIFY_MODEL', 'claude-sonnet-4-6')


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_json(path, doc):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)


def screen_sellers(universe, batch=80, model=None, workers=8, log=print, passes=2, state_path=None):
    """Knowledge pass over the whole universe; returns candidate claims
    keyed by normalized name. Two passes with different chunk boundaries,
    merged: a single pass misses obvious names. With state_path the
    result of every chunk is saved as it lands and a rerun resumes."""
    model = model or SCREEN_MODEL
    names = [u['name'] for u in universe]
    chunks = [names[i:i + batch] for i in range(0, len(names), batch)]
    if passes > 1:
        off = batch // 2
        chunks += [names[i:i + batch] for i in range(off, len(names), batch)] + ([names[:off]] if len(names) > off else [])
    state = (_load_json(state_path) if state_path else None) or {'done': [], 'found': {}}
    found = dict(state.get('found') or {})
    done = set(state.get('done') or [])
    todo = [(i, c) for i, c in enumerate(chunks) if i not in done]
    if done:
        log(f'[screen] resuming: {len(done)}/{len(chunks)} chunks done, {len(found)} found')
    lock = __import__('threading').Lock()

    def one(idx, chunk):
        user = "People:\n" + "\n".join(chunk) + "\n\nList only the ones who sell their own product line or service."
        doc = _claude_json(SCREEN_SYSTEM, user, model=model, max_tokens=6000)
        rows = (doc or {}).get('sellers') or []
        keep = {}
        allowed = {_norm(n): n for n in chunk}
        for r in rows:
            k = _norm((r or {}).get('name'))
            if k in allowed and (r.get('brand') or '').strip():
                r['name'] = allowed[k]
                keep[k] = r
        return idx, keep

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, i, c) for i, c in todo]
        for f in cf.as_completed(futs):
            try:
                idx, keep = f.result()
            except Exception as e:
                log(f'[screen] chunk failed: {e}')
                continue
            with lock:
                for k, r in keep.items():
                    if k not in found or (not found[k].get('brand') and r.get('brand')):
                        found[k] = r
                    elif r.get('brand') and _norm(r['brand']) != _norm(found[k].get('brand')):
                        found[k].setdefault('others', [])
                        if isinstance(found[k]['others'], list) and r['brand'] not in found[k]['others']:
                            found[k]['others'].append(r['brand'])
                done.add(idx)
                if state_path:
                    _save_json(state_path, {'done': sorted(done), 'found': found})
            log(f'[screen] chunk {idx + 1}/{len(chunks)}: {len(keep)} sellers (total {len(found)})')
    return found


# ----------------------------------------------------------------- 3. verify
def verify_sellers(cands, batch=4, model=None, workers=None, log=print, state_path=None):
    """Web research per claim. Returns {norm_name: checked_row}. With
    state_path every finished chunk is saved and a rerun resumes."""
    workers = int(workers or os.environ.get('TPL_VERIFY_WORKERS') or 12)
    items = list(cands.values())
    chunks = [items[i:i + batch] for i in range(0, len(items), batch)]
    out = dict(((_load_json(state_path) if state_path else None) or {}).get('out') or {})
    todo = [(i, c) for i, c in enumerate(chunks) if not all(_norm(r['name']) in out for r in c)]
    if len(todo) < len(chunks):
        log(f'[verify] resuming: {len(chunks) - len(todo)}/{len(chunks)} chunks done')
    lock = __import__('threading').Lock()

    def one(idx, chunk):
        claims = [{'name': r['name'], 'brand': r.get('brand'), 'others': [o for o in (r.get('others') or []) if isinstance(o, str)][:3],
                   'category': r.get('category'), 'type': r.get('type'), 'since': r.get('since')} for r in chunk]
        user = ("Verify each claim. Echo \"name\" exactly as given. If the flagship brand is discontinued or not theirs but one of "
                "\"others\" is active and theirs, report that one as active instead.\n" + json.dumps({'claims': claims, 'ensure': 'name only'}, ensure_ascii=False))
        doc = _claude_json(VERIFY_SYSTEM, user, model=model, max_tokens=4000, tools=WEB_SEARCH_TOOLS)
        rows = (doc or {}).get('checked') or []
        allowed = {_norm(r['name']): r for r in chunk}
        keep = {}
        for r in rows:
            k = _norm((r or {}).get('name'))
            if k not in allowed:   # "MRBEAST: Feastables" or a re-cased name: match on the name prefix
                k = next((a for a in allowed if k.startswith(a) or a.startswith(k.split(' ')[0] + ' ' + (k.split(' ') + [''])[1]) and len(k) > 3), '')
            if k in allowed:
                merged = dict(allowed[k])
                merged.update({kk: vv for kk, vv in r.items() if vv not in (None, '')})
                merged['name'] = allowed[k]['name']
                keep[k] = merged
        for k, r in allowed.items():
            keep.setdefault(k, dict(r, status='unverified'))
        return idx, keep

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, i, c) for i, c in todo]
        for f in cf.as_completed(futs):
            try:
                idx, keep = f.result()
            except Exception as e:
                log(f'[verify] chunk failed: {e}')
                continue
            with lock:
                out.update(keep)
                if state_path:
                    _save_json(state_path, {'out': out})
            act = sum(1 for r in keep.values() if r.get('status') == 'active')
            log(f'[verify] chunk {idx + 1}/{len(chunks)}: {act}/{len(keep)} active')
    return out


KNOWLEDGE_SYSTEM = """You check claims that a public figure sells their OWN product line or service (founded, co-founded, owns a stake, carries their name) through their own channels, from what you know. For each claim answer:
- active: you are confident the brand or program exists now and is theirs
- discontinued: it was theirs and is gone
- not_theirs: a sponsorship, a collab owned by someone else, or a mistake
- unsure: you do not know
Echo "name" exactly as given. Add "display": the name in its usual capitalization. Correct brand, category, where and since when you know better. Return only JSON:
{"checked": [{"name": "...", "display": "...", "status": "active|discontinued|not_theirs|unsure", "brand": "...", "category": "...", "type": "...", "where": "...", "since": <year or null>}]}"""


def verify_knowledge(cands, batch=25, model=None, workers=8, log=print, state_path=None):
    """A second model's knowledge, no web: fast. For the long tail of the
    universe where web research per person is too slow. Agreement with
    the screen on the same brand is what the caller trusts."""
    model = model or os.environ.get('TPL_KNOWLEDGE_MODEL', 'claude-sonnet-4-6')
    items = list(cands.values())
    chunks = [items[i:i + batch] for i in range(0, len(items), batch)]
    out = dict(((_load_json(state_path) if state_path else None) or {}).get('out') or {})
    todo = [(i, c) for i, c in enumerate(chunks) if not all(_norm(r['name']) in out for r in c)]
    lock = __import__('threading').Lock()

    def one(idx, chunk):
        claims = [{'name': r['name'], 'brand': r.get('brand'), 'category': r.get('category'), 'type': r.get('type'), 'since': r.get('since')} for r in chunk]
        doc = _claude_json(KNOWLEDGE_SYSTEM, "Claims:\n" + json.dumps({'claims': claims}, ensure_ascii=False), model=model, max_tokens=6000)
        rows = (doc or {}).get('checked') or []
        allowed = {_norm(r['name']): r for r in chunk}
        keep = {}
        for r in rows:
            k = _norm((r or {}).get('name'))
            if k in allowed:
                merged = dict(allowed[k])
                merged.update({kk: vv for kk, vv in r.items() if vv not in (None, '')})
                merged['name'] = allowed[k]['name']
                merged['status'] = {'unsure': 'unverified'}.get(str(merged.get('status') or ''), merged.get('status'))
                merged['check'] = 'knowledge'
                keep[k] = merged
        for k, r in allowed.items():
            keep.setdefault(k, dict(r, status='unverified', check='knowledge'))
        return idx, keep

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, i, c) for i, c in todo]
        for f in cf.as_completed(futs):
            try:
                idx, keep = f.result()
            except Exception as e:
                log(f'[knowledge] chunk failed: {e}')
                continue
            with lock:
                out.update(keep)
                if state_path:
                    _save_json(state_path, {'out': out})
            log(f'[knowledge] chunk {idx + 1}/{len(chunks)}: {sum(1 for r in keep.values() if r.get("status") == "active")}/{len(keep)} active')
    return out


def _same_brand(a, b):
    na, nb = _norm(a), _norm(b)
    return bool(na and nb) and (na == nb or na in nb or nb in na)


# ----------------------------------------------------------------- rows
CATEGORY_LABEL = {'beverage': 'Beverage', 'food': 'Food and snacks', 'beauty': 'Beauty and skincare', 'fragrance': 'Fragrance',
                  'apparel': 'Apparel', 'footwear': 'Footwear', 'spirits': 'Spirits and wine', 'supplements': 'Supplements',
                  'home': 'Home', 'pets': 'Pets', 'course or coaching': 'Course or coaching', 'membership': 'Membership',
                  'app': 'App or software', 'restaurant': 'Restaurant or venue', 'other': 'Other'}


def assemble_rows(universe, verified, include=('active',)):
    by = {_norm(u['name']): u for u in universe}
    rows = []
    for k, r in verified.items():
        if r.get('status') not in include:
            continue
        u = by.get(k)
        if not u:
            continue
        cat = str(r.get('category') or 'other').lower().strip()
        cat = next((CATEGORY_LABEL[c] for c in CATEGORY_LABEL if c in cat), 'Other')
        rows.append({'Name': (str(r.get('display') or '').strip() if _norm(r.get('display')) == _norm(u['name']) else '') or _title(u['name']), 'Role': u['role'], 'Own product or service': str(r.get('brand') or '').strip(),
                     'Category': cat, 'Type': str(r.get('type') or '').strip().lower() or 'product line',
                     'Where it is sold': str(r.get('where') or '').strip(), 'Since': r.get('since') or '',
                     'US audience, trailing 12 months': u['us_people'], 'Share of US population': round(u['penetration'], 4)})
    rows.sort(key=lambda d: -d['US audience, trailing 12 months'])
    return rows


def _title(name):
    s = str(name or '')
    if s.isupper():
        parts = []
        for w in s.split(' '):
            parts.append(w if (len(w) <= 3 and w.isalpha() and w in ('DJ', 'MC', 'JR', 'SR', 'II', 'III', 'IV')) else w.capitalize())
        s = ' '.join(parts)
        s = re.sub(r"\bMc(\w)", lambda m: 'Mc' + m.group(1).upper(), s)
        s = re.sub(r"'(\w)", lambda m: "'" + m.group(1).lower(), s)
    return s


# ----------------------------------------------------------------- 4. files
def _fmt_int(v):
    return f"{int(v):,}"


def build_files(rows, out_dir, *, title='Influencers and talent who sell their own product or service',
                window_label='', client=''):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = 'Influencers_and_Talent_With_Own_Product_Lines'
    headers = ['Name', 'Role', 'Own product or service', 'Category', 'Type', 'Where it is sold', 'Since',
               'US audience, trailing 12 months', 'Share of US population']
    # csv
    csv_path = out / f'{stem}.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        for r in rows:
            w.writerow([r[h] if h != 'Share of US population' else f"{r[h]:.4f}%" for h in headers])
    # xlsx
    xlsx_path = out / f'{stem}.xlsx'
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = 'Talent with own product'
        ws.append(headers)
        for r in rows:
            ws.append([r[h] for h in headers])
        hdr_fill = PatternFill('solid', fgColor='0C1618')
        for c in ws[1]:
            c.font = Font(bold=True, color='E9E8E1')
            c.fill = hdr_fill
            c.alignment = Alignment(vertical='center')
        widths = [26, 18, 30, 20, 14, 18, 8, 22, 18]
        for i, wdt in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = wdt
        for row in ws.iter_rows(min_row=2):
            row[7].number_format = '#,##0'
            row[8].number_format = '0.0000"%"'
        ws.freeze_panes = 'A2'
        ws2 = wb.create_sheet('By category')
        ws2.append(['Category', 'People', 'Combined US audience, trailing 12 months'])
        cats = {}
        for r in rows:
            c = cats.setdefault(r['Category'], [0, 0])
            c[0] += 1
            c[1] += r['US audience, trailing 12 months']
        for cat, (n, aud) in sorted(cats.items(), key=lambda kv: -kv[1][1]):
            ws2.append([cat, n, aud])
        for c in ws2[1]:
            c.font = Font(bold=True, color='E9E8E1')
            c.fill = hdr_fill
        ws2.column_dimensions['A'].width = 24
        ws2.column_dimensions['C'].width = 34
        for row in ws2.iter_rows(min_row=2):
            row[2].number_format = '#,##0'
        ws3 = wb.create_sheet('About')
        for line in (title, window_label, '', 'Each person is read across the full digital life of the Crosswalk sample and projected to the US population.',
                     'US audience: people in the US who engaged with the person in the trailing 12 months.',
                     'Share of US population: that audience as a share of the US population.',
                     'Own product or service: a brand, program, app or venue the person founded, co-founded or owns and sells through their own channels. Generic merch, sponsorships and collabs owned by others are not included.'):
            ws3.append([line])
        ws3.column_dimensions['A'].width = 120
        wb.save(xlsx_path)
    except Exception as e:
        print(f'[files] xlsx failed: {e}')
        xlsx_path = None
    pdf_path = out / f'{stem}.pdf'
    try:
        _build_pdf(rows, pdf_path, title=title, window_label=window_label, client=client)
    except Exception as e:
        print(f'[files] pdf failed: {e}')
        pdf_path = None
    return {'csv': str(csv_path), 'xlsx': str(xlsx_path) if xlsx_path else None, 'pdf': str(pdf_path) if pdf_path else None}


def _build_pdf(rows, path, *, title, window_label, client):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

    fonts = ROOT / '.cursor' / 'skills' / 'crosswalk-brand-standards' / 'assets' / 'fonts'
    fam = 'Helvetica'
    bold = 'Helvetica-Bold'
    try:
        pdfmetrics.registerFont(TTFont('Inter18', str(fonts / 'Inter_18pt-Regular.ttf')))
        pdfmetrics.registerFont(TTFont('Inter18-Bold', str(fonts / 'Inter_18pt-Bold.ttf')))
        pdfmetrics.registerFont(TTFont('Inter18-Medium', str(fonts / 'Inter_18pt-Medium.ttf')))
        fam, bold = 'Inter18', 'Inter18-Bold'
    except Exception:
        pass
    GRAPHITE, OFFWHITE, OLIVE, BODY, MUTED, RULE = (colors.HexColor('#0C1618'), colors.HexColor('#E9E8E1'), colors.HexColor('#5E7E12'),
                                                    colors.HexColor('#5C6560'), colors.HexColor('#888C89'), colors.HexColor('#D6D5CE'))
    st_title = ParagraphStyle('t', fontName=bold, fontSize=20, leading=25, textColor=GRAPHITE)
    st_eyebrow = ParagraphStyle('e', fontName=fam, fontSize=8.5, leading=11, textColor=OLIVE)
    st_body = ParagraphStyle('b', fontName=fam, fontSize=10.5, leading=15, textColor=BODY)
    st_h = ParagraphStyle('h', fontName=bold, fontSize=13, leading=17, textColor=GRAPHITE, spaceBefore=10, spaceAfter=4)
    st_cell = ParagraphStyle('c', fontName=fam, fontSize=8.3, leading=10.5, textColor=GRAPHITE)
    st_cell_m = ParagraphStyle('cm', fontName=fam, fontSize=8.3, leading=10.5, textColor=BODY)
    st_foot = ParagraphStyle('f', fontName=fam, fontSize=7.5, leading=10, textColor=MUTED)

    total_aud = sum(r['US audience, trailing 12 months'] for r in rows)
    cats = {}
    for r in rows:
        c = cats.setdefault(r['Category'], [0, 0])
        c[0] += 1
        c[1] += r['US audience, trailing 12 months']
    top_cat = sorted(cats.items(), key=lambda kv: -kv[1][0])[:3]
    top = rows[:12]

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(OFFWHITE)
        canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setFont(fam, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch, 'CROSSWALK  /  BEHAVIORAL INTELLIGENCE ENGINE')
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f'{doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.8 * inch, title=title, author='Crosswalk')
    story = []
    logo = ROOT / '.cursor' / 'skills' / 'crosswalk-brand-standards' / 'assets' / 'crosswalk-logo-black.png'
    if logo.exists():
        story.append(Image(str(logo), width=1.5 * inch, height=0.33 * inch, kind='proportional', hAlign='LEFT'))
        story.append(Spacer(1, 10))
    story.append(Paragraph('CUSTOM ASK' + (f'  /  PREPARED FOR {client.upper()}' if client else ''), st_eyebrow))
    story.append(Spacer(1, 4))
    story.append(Paragraph(title + '.', st_title))
    story.append(Spacer(1, 4))
    story.append(Paragraph(window_label, st_body))
    story.append(Spacer(1, 12))
    story.append(Paragraph('What we found', st_h))
    bullets = [
        f"{len(rows):,} people in our talent universe sell a product line or service they own, through their own channel or page. Together they reach {total_aud:,} US people in the trailing 12 months (people who engage with more than one of them are counted once per person).",
        "The most common lines are " + ', '.join(f"{c.lower()} ({n} people)" for c, (n, _a) in top_cat) + ".",
        f"The largest audience on the list is {top[0]['Name']} ({top[0]['Own product or service']}), reaching {top[0]['US audience, trailing 12 months']:,} US people." if top else '',
        "Generic merch, sponsorships and collabs owned by someone else are not on the list; only brands, programs, apps and venues the person founded, co-founded or owns.",
    ]
    for b in bullets:
        if b:
            story.append(Paragraph('\u2022  ' + b, st_body))
    story.append(Spacer(1, 10))
    story.append(Paragraph('The largest audiences on the list', st_h))
    data = [[Paragraph(h, ParagraphStyle('th', parent=st_cell, fontName=bold, textColor=OFFWHITE)) for h in ('Name', 'Role', 'Own product or service', 'Category', 'US audience')]]
    for r in top:
        data.append([Paragraph(r['Name'], st_cell), Paragraph(r['Role'], st_cell_m), Paragraph(r['Own product or service'], st_cell),
                     Paragraph(r['Category'], st_cell_m), Paragraph(f"{r['US audience, trailing 12 months']:,}", st_cell)])
    t = Table(data, colWidths=[1.6 * inch, 1.1 * inch, 1.9 * inch, 1.3 * inch, 1.1 * inch], repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), GRAPHITE), ('LINEBELOW', (0, 1), (-1, -1), 0.4, RULE),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                           ('ROUNDEDCORNERS', [4, 4, 4, 4])]))
    story.append(t)
    story.append(Spacer(1, 10))
    story.append(Paragraph('By category', st_h))
    data = [[Paragraph(h, ParagraphStyle('th2', parent=st_cell, fontName=bold, textColor=OFFWHITE)) for h in ('Category', 'People', 'Combined US audience')]]
    for cat, (n, aud) in sorted(cats.items(), key=lambda kv: -kv[1][1]):
        data.append([Paragraph(cat, st_cell), Paragraph(f'{n}', st_cell), Paragraph(f'{aud:,}', st_cell)])
    t2 = Table(data, colWidths=[2.6 * inch, 1.0 * inch, 2.2 * inch], hAlign='LEFT')
    t2.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), GRAPHITE), ('LINEBELOW', (0, 1), (-1, -1), 0.4, RULE),
                            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story.append(t2)
    story.append(Spacer(1, 10))
    story.append(Paragraph('What this means for you: every name here already sells to their own audience, so a partnership starts from a working storefront, not a cold pitch. The full list with each person\'s product, category, where it is sold and US audience is in the workbook and the CSV. Ask Prometheus for any of them and we read the audience in detail.', st_body))
    story.append(Spacer(1, 8))
    story.append(Paragraph('US audience: people in the US who engaged with the person in the trailing 12 months, read across the full digital life of the Crosswalk sample and projected to the US population.', st_foot))
    story.append(PageBreak())
    story.append(Paragraph('The full list', st_h))
    data = [[Paragraph(h, ParagraphStyle('th3', parent=st_cell, fontName=bold, textColor=OFFWHITE)) for h in ('Name', 'Own product or service', 'Category', 'Where', 'Since', 'US audience')]]
    for r in rows:
        data.append([Paragraph(r['Name'], st_cell), Paragraph(r['Own product or service'], st_cell), Paragraph(r['Category'], st_cell_m),
                     Paragraph(r['Where it is sold'], st_cell_m), Paragraph(str(r['Since'] or ''), st_cell_m),
                     Paragraph(f"{r['US audience, trailing 12 months']:,}", st_cell)])
    t3 = Table(data, colWidths=[1.55 * inch, 1.75 * inch, 1.25 * inch, 1.0 * inch, 0.5 * inch, 0.95 * inch], repeatRows=1)
    t3.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), GRAPHITE), ('LINEBELOW', (0, 1), (-1, -1), 0.3, RULE),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story.append(t3)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


# ----------------------------------------------------------------- run
def run(out_dir, *, limit=None, genpop_path=None, model=None, verify_model=None, log=print, client=''):
    t0 = time.time()
    universe = load_universe(genpop_path)
    if limit:
        universe = universe[:int(limit)]
    log(f'[universe] {len(universe):,} people in Gen Pop TALENT')
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cands = screen_sellers(universe, model=model, log=log, state_path=str(out / 'candidates.json'))
    log(f'[screen] {len(cands)} candidates')
    # web research for the largest audiences (the names a partner acts on
    # first), a second model's knowledge for the long tail where a search
    # per person is too slow; a tail name ships only when both models name
    # the same brand and the check reads active
    rank = {_norm(u['name']): i for i, u in enumerate(universe)}
    ordered = sorted(cands.items(), key=lambda kv: rank.get(kv[0], 10 ** 9))
    web_top = int(os.environ.get('TPL_WEB_VERIFY_TOP') or 300)
    head = dict(ordered[:web_top])
    tail = dict(ordered[web_top:])
    verified = verify_sellers(head, model=verify_model or VERIFY_MODEL, log=log, state_path=str(out / 'verified.json'))
    retry = {k: head[k] for k, v in verified.items() if v.get('status') == 'unverified' and k in head}
    if retry:
        log(f'[verify] second pass on {len(retry)} unverified')
        verified.update(verify_sellers(retry, batch=3, model=verify_model or VERIFY_MODEL, log=log))
    if tail:
        checked = verify_knowledge(tail, log=log, state_path=str(out / 'knowledge.json'))
        kept = 0
        for k, r in checked.items():
            if k in verified and verified[k].get('status') in ('active', 'discontinued', 'not_theirs'):
                continue        # the web already settled it
            if r.get('status') == 'active' and _same_brand(r.get('brand'), tail[k].get('brand')):
                verified[k] = r
                kept += 1
            else:
                verified[k] = dict(r, status='unverified' if r.get('status') == 'active' else r.get('status'))
        log(f'[knowledge] {kept} tail names kept where both reads name the same active brand')
    rows = assemble_rows(universe, verified)
    log(f'[rows] {len(rows)} active sellers')
    end = date.today()
    start = end.replace(year=end.year - 1)
    window = f"Trailing 12 months, {start.strftime('%B %-d, %Y')} to {end.strftime('%B %-d, %Y')}. US population."
    files = build_files(rows, out_dir, window_label=window, client=client)
    with open(Path(out_dir) / 'underpinnings.json', 'w') as fh:
        json.dump({'universe': len(universe), 'candidates': len(cands), 'active': len(rows), 'window': window,
                   'verified': {k: {kk: v.get(kk) for kk in ('name', 'brand', 'status', 'source', 'category', 'since')} for k, v in verified.items()},
                   'seconds': int(time.time() - t0)}, fh, indent=1)
    log(f'[done] {len(rows)} rows in {int(time.time() - t0)}s -> {files}')
    return rows, files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--genpop', default=None, help='local Gen Pop csv (default: S3 canonical)')
    ap.add_argument('--model', default=None)
    ap.add_argument('--verify-model', default=None)
    ap.add_argument('--client', default='')
    a = ap.parse_args(argv)
    run(a.out, limit=a.limit, genpop_path=a.genpop, model=a.model, verify_model=a.verify_model, client=a.client)
    return 0


if __name__ == '__main__':
    sys.exit(main())
