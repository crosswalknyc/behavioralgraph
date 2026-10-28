"""Digital Twins IQ, the read side (2026-10-26).

The index and the gz payloads under ``digital-twins/``, who may open a
twin, the CSV rows, the view summary Prometheus reads off the page, the
plain lines a deck restates, the pricing defaults, and the How to text
that is the in-product directions (the How to tab, the empty states and
the Prometheus answer to "how do I use Digital Twins IQ").

Every twin joins the shared library (``partner-api-shared-catalog``):
anyone with ``has_digital_twins_iq_access`` sees the list, gated by
``allowed_digital_twin_runs`` (default ``['*']``); the creator always
sees their own; admins see everything.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Optional

from migration.digital_twin_synthesis import S3_BUCKET, S3_INDEX_KEY, S3_PREFIX, load_twin, save_twin  # noqa: F401

JOBS_PREFIX = 'system/digital_twins/jobs/'
REQUESTS_PREFIX = 'system/digital_twins/requests/'
ACCESS_FLAG = 'has_digital_twins_iq_access'
RUNS_FIELD = 'allowed_digital_twin_runs'
PRODUCT_KEY = 'digitalTwinsIQ'
PRODUCT_LABEL = 'Digital Twins IQ'

# Pricing defaults (Jenna 2026-10-26). One line each; the live price an
# admin sets in the billing panel wins over these (wallet.tool_price_usd).
# Prometheus usage on a build and in a room is metered ON TOP of these
# (Jenna 2026-10-27: 'aside from these fees there needs to be billed
# prometheus metered pricing'): the model calls carry the seat's
# pay-per-use attribution, so the session sweep bills them at the
# published meter like any other Prometheus work.
PRICING = {
    'digital_twins_iq': 750.0,          # a twin build: the twin and three runs
    'digital_twin_run': 100.0,          # each further Greenlight, Launch, content or compare run
    'digital_twin_spend': 250.0,        # Spend Engine plan activation for the plan window, daily runs included
    'digital_twin_panel': 5500.0,       # a Focus Group IQ session, 12 questions included (Jenna 2026-10-26: 'A session is $5,500 (twelve questions, any room)')
    'digital_twin_panel_more': 5500.0,  # each further 12 questions is another session at the session price
    'digital_twin_panelist': 550.0,     # each panelist beyond the 12 a room seats, billed with the session (Jenna 2026-10-27: 'extra panelists for an extra $550 x panelist')
}
PANEL_QUESTIONS_INCLUDED = 12
PANEL_SEATS_INCLUDED = 12               # panelists a session covers; every seat past this is a priced panelist
PANEL_SEATS_MAX = 40
PULL_TYPES = {
    'digital_twins_iq': 'Digital Twins IQ', 'digital_twin_run': 'Digital Twins IQ Run', 'digital_twin_spend': 'Digital Twins IQ Spend',
    'digital_twin_panel': 'Digital Twins IQ Focus Group', 'digital_twin_panel_more': 'Digital Twins IQ Focus Group More',
    'digital_twin_panelist': 'Digital Twins IQ Focus Group Panelist',
}


def extra_panelists(in_room) -> int:
    """How many seats in a room are priced panelists: the seats past the
    included twelve, never negative."""
    try:
        n = int(in_room or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(n, PANEL_SEATS_MAX) - PANEL_SEATS_INCLUDED)


def panelists_for(payload: dict) -> int:
    """The seats a room on this twin opens with: the number chosen when
    the twin was built, else twelve."""
    try:
        n = int(((payload or {}).get('meta') or {}).get('panelists') or 0)
    except (TypeError, ValueError):
        n = 0
    return max(2, min(n, PANEL_SEATS_MAX)) if n else PANEL_SEATS_INCLUDED


# ------------------------------------------------------------- the index
def list_runs(s3, *, username: Optional[str] = None, limit: int = 500) -> list:
    if s3 is None:
        return []
    try:
        idx = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)['Body'].read().decode('utf-8')) or {}
        runs = [r for r in (idx.get('runs') or []) if isinstance(r, dict) and r.get('key')]
    except Exception:
        runs = []
    for r in runs:
        r.pop('ltv_5y', None)
        r.pop('ltv_10y', None)
        r.pop('has_ltv', None)
    runs.sort(key=lambda r: str(r.get('created_at') or ''), reverse=True)
    if username:
        runs = [r for r in runs if r.get('created_by') == username]
    return runs[:limit]


def _role(user) -> str:
    return str((user or {}).get('role') or 'user').lower().replace(' ', '_')


def is_admin(user) -> bool:
    return _role(user) in ('admin', 'super_admin', 'superadmin')


def has_access(user) -> bool:
    """The product flag, or admin."""
    if not isinstance(user, dict):
        return False
    if is_admin(user):
        return True
    return bool(user.get(ACCESS_FLAG))


def allowed_keys(user):
    """['*'] (everything), or the explicit list."""
    raw = (user or {}).get(RUNS_FIELD)
    if isinstance(raw, list):
        return raw
    return ['*']


def can_open(user, key: str, run: Optional[dict] = None) -> bool:
    if not isinstance(user, dict):
        return False
    uname = str(user.get('username') or '')
    if is_admin(user):
        return True
    if run and uname and str(run.get('created_by') or '') == uname:
        return True
    if not has_access(user):
        return False
    allowed = allowed_keys(user)
    if '*' in allowed:
        return True
    return key in allowed or (run and str(run.get('name') or '') in allowed)


def visible_runs(runs: list, user) -> list:
    return [r for r in runs if can_open(user, str(r.get('key') or ''), r)]


def find_twin(runs: list, name: str) -> Optional[dict]:
    """The index entry whose name or subject the words carry; the newest
    when several match."""
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(name or '').lower()) + ' '
    best, best_score = None, 0.0
    for r in runs:
        for field in ('name', 'subject'):
            toks = [t for t in re.sub(r"[^a-z0-9]+", ' ', str(r.get(field) or '').lower()).split() if len(t) > 1 and t not in ('the', 'viewers', 'fans', 'twin', 'audience', 'of')]
            if not toks:
                continue
            present = sum(1 for t in toks if f" {t} " in low)
            if present and present >= max(1, len(toks) - (1 if len(toks) > 2 else 0)):
                score = present / len(toks) + (0.1 if field == 'name' else 0.0)
                if score > best_score or (score == best_score and str(r.get('created_at') or '') > str((best or {}).get('created_at') or '')):
                    best, best_score = r, score
    return best


# -------------------------------------------------------------- the rows
def csv_rows(payload: dict) -> list:
    """Identity, traits, lifecycle, runs, the Spend Engine ledger
    and the Focus Group IQ sessions as flat rows."""
    m = payload.get('meta') or {}
    name = m.get('name') or m.get('subject')
    rows = [['Section', 'Item', 'Label', 'Value', 'Unit', 'Note']]
    rows.append(['Twin', 'name', name, m.get('size_us'), 'US people', f"{m.get('window_start')} to {m.get('window_end')}"])
    rows.append(['Twin', 'platform home', m.get('platform_home'), m.get('avid_share_pct'), 'avid share %', m.get('format')])
    ident = payload.get('identity') or {}
    for cat, items in (ident.get('demographics') or {}).items():
        for it in items:
            rows.append(['Identity', cat, it.get('label'), it.get('pct'), '%', ''])
    for it in ident.get('geography') or []:
        rows.append(['Identity', 'geography', it.get('label'), it.get('pct'), '%', ''])
    for it in ident.get('platforms') or []:
        rows.append(['Identity', 'platforms', it.get('label'), it.get('reach_pct'), '% reach', it.get('group') or ''])
    for it in ident.get('purchase') or []:
        rows.append(['Identity', 'purchase', it.get('label'), it.get('pct'), '%', it.get('group') or ''])
    for k, v in (payload.get('traits') or {}).items():
        rows.append(['Traits', k, v.get('label'), v.get('value'), '0-100', v.get('read')])
    for k, v in (payload.get('lifecycle') or {}).items():
        if k.endswith('_pct'):
            rows.append(['Lifecycle', k[:-4], k[:-4], v, '%', ''])
    for r in payload.get('runs') or []:
        o = r.get('outputs') or {}
        unit = 'USD' if o.get('unit') == 'usd' else (o.get('noun') or 'people')
        rows.append(['Runs', r.get('id'), f"{r.get('label')}: {r.get('question')}", o.get('expected'), unit, f"range {o.get('low')} to {o.get('high')}" + (f"; odds {o.get('odds_pct')}%" if o.get('odds_pct') is not None else '')])
        for lv in (o.get('levers') or [])[:6]:
            rows.append(['Runs', r.get('id'), f"lever: {lv.get('lever')}", lv.get('delta_people'), unit, f"{lv.get('delta_pct')}%"])
    for sp in ((payload.get('labs') or {}).get('spend')) or []:
        led = sp.get('ledger') or {}
        rows.append(['Spend Engine', sp.get('plan_id'), sp.get('label'), led.get('saved_total'), 'USD saved', led.get('read')])
        for d in sp.get('days') or []:
            for b in d.get('by_channel') or []:
                rows.append(['Spend Engine', sp.get('plan_id'), f"day {d.get('day')} {b.get('channel')}", b.get('first_actions'), 'first actions', f"spend {b.get('spend')}, cpa {b.get('cpa')}"])
    for ps in payload.get('panel_sessions') or []:
        for q in ps.get('questions') or []:
            for r in q.get('distribution') or []:
                rows.append(['Focus Group IQ', ps.get('id'), f"{q.get('q')}: {r.get('label')}", r.get('pct'), '%', f"{r.get('people')} people"])
    return rows


def csv_text(payload: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in csv_rows(payload):
        w.writerow(['' if v is None else v for v in r])
    return buf.getvalue()


def csv_filename(payload: dict) -> str:
    m = payload.get('meta') or {}
    return re.sub(r"[^A-Za-z0-9]+", '_', f"{m.get('name') or m.get('subject')} Digital Twin").strip('_') + '.csv'


# ------------------------------------------------------------- the summary
def view_summary(payload: dict, run_id: str = '', session_id: str = '') -> dict:
    """What the page hands Prometheus as view_context.data: small,
    every headline figure, the open run or session when there is one."""
    m = payload.get('meta') or {}
    out = {'twin': m.get('name'), 'subject': m.get('subject'), 'key': m.get('key'), 'size_us': m.get('size_us'), 'window': f"{m.get('window_start')} to {m.get('window_end')}",
           'platform_home': m.get('platform_home'), 'format': m.get('format'), 'ip_related': m.get('ip_related'),
           'source': m.get('source'), 'panelists': panelists_for(payload),
           'identity_read': (payload.get('identity') or {}).get('read'),
           'traits': {k: {'value': v.get('value'), 'read': v.get('read')} for k, v in (payload.get('traits') or {}).items()},
           'lifecycle': {k: v for k, v in (payload.get('lifecycle') or {}).items() if k.endswith('_pct')},
           'tldr': (payload.get('the_read') or {}).get('tldr'),
           'runs': [{'id': r.get('id'), 'kind': r.get('kind'), 'question': r.get('question'), 'expected': (r.get('outputs') or {}).get('expected'),
                     'low': (r.get('outputs') or {}).get('low'), 'high': (r.get('outputs') or {}).get('high'), 'odds_pct': (r.get('outputs') or {}).get('odds_pct'),
                     'recommendation': r.get('recommendation')} for r in (payload.get('runs') or [])[:8]]}
    if payload.get('standing_questions'):
        out['standing_questions'] = payload['standing_questions']
    if run_id:
        r = next((x for x in payload.get('runs') or [] if x.get('id') == run_id), None)
        if r:
            out['open_run'] = {k: r.get(k) for k in ('id', 'kind', 'question', 'outputs', 'drivers', 'recommendation', 'read')}
    if session_id:
        s = next((x for x in payload.get('panel_sessions') or [] if x.get('id') == session_id), None)
        if s:
            out['open_panel_session'] = {'id': s.get('id'), 'title': s.get('title'), 'in_room': (s.get('group') or {}).get('in_room'),
                                         'questions': [{'q': q.get('q'), 'kind': q.get('kind'), 'read': q.get('read'), 'distribution': q.get('distribution')} for q in (s.get('questions') or [])[-6:]]}
    spends = ((payload.get('labs') or {}).get('spend')) or []
    if spends:
        sp = spends[0]
        out['spend_engine'] = {'plan_id': sp.get('plan_id'), 'label': sp.get('label'), 'ledger': sp.get('ledger'), 'mode': sp.get('mode')}
    return out


def twin_lines(payload: dict) -> list:
    """The twin as plain lines a deck restates slide for slide."""
    from migration.digital_twin_engine import fmt_money, human_people
    m = payload.get('meta') or {}
    out = []
    rd = payload.get('the_read') or {}
    if rd.get('tldr'):
        out.append('TLDR:\n' + '\n'.join(rd['tldr']))
    ident = payload.get('identity') or {}
    demo = ident.get('demographics') or {}
    lines = [f"{m.get('name')}: {int(m.get('size_us') or 0):,} people in the US, {m.get('window_start')} to {m.get('window_end')}; home: {m.get('platform_home')}."]
    for cat in ('gender', 'age', 'income'):
        rows = demo.get(cat) or []
        if rows:
            lines.append(f"{cat.title()}: " + '; '.join(f"{r['label']} {float(r['pct']):.1f}%" for r in rows[:8]))
    if ident.get('platforms'):
        lines.append('Platforms: ' + '; '.join(f"{p['label']} {float(p['reach_pct']):.1f}%" for p in ident['platforms'][:8]))
    if ident.get('purchase'):
        lines.append('They buy: ' + '; '.join(f"{p['label']} {float(p['pct']):.1f}%" for p in ident['purchase'][:8]))
    out.append('WHO THEY ARE:\n' + '\n'.join(lines))
    traits = payload.get('traits') or {}
    if traits:
        out.append('TRAITS (0 to 100):\n' + '\n'.join(f"{v.get('label')}: {v.get('value')}; they {v.get('read')}" for v in traits.values()))
    lc = payload.get('lifecycle') or {}
    if lc:
        out.append('LIFECYCLE:\n' + '; '.join(f"{k[:-4]} {v}%" for k, v in lc.items() if k.endswith('_pct')))
    for r in (payload.get('runs') or [])[:6]:
        o = r.get('outputs') or {}
        unit = 'usd' if o.get('unit') == 'usd' else (o.get('noun') or 'people')
        fmt = (lambda v: fmt_money(v)) if unit == 'usd' else (lambda v: f"{int(v or 0):,} {unit}")
        blk = [f"{r.get('label')}: {r.get('question')}", f"Expected {fmt(o.get('expected'))}, range {fmt(o.get('low'))} to {fmt(o.get('high'))}"
               + (f"; odds {o.get('odds_pct')}% ({o.get('odds_words')})" if o.get('odds_pct') is not None else '') + '.']
        blk += list(r.get('drivers') or [])[:3]
        if r.get('recommendation'):
            blk.append('What to do: ' + r['recommendation'])
        out.append('RUN:\n' + '\n'.join(blk))
    sq = payload.get('standing_questions') or {}
    if sq:
        out.append('THE STANDING QUESTIONS:\n' + '\n'.join([sq.get('relative_popularity') or '', sq.get('demographics_line') or '',
                   f"Prime Video in the last 30 days: {sq.get('prime_video_t30d_pct')}%. SVOD churn: {sq.get('svod_churn_pct')}%. On Prime Video: {sq.get('on_prime_pct')}%; the rest sit on " +
                   ', '.join(f"{p['label']} {p['pct']:.1f}%" for p in (sq.get('off_prime_platforms') or [])[:5]) + '.']))
    for sp in (((payload.get('labs') or {}).get('spend')) or [])[:2]:
        led = sp.get('ledger') or {}
        out.append('SPEND ENGINE:\n' + '\n'.join([sp.get('label') or '', led.get('read') or '', (sp.get('holdout') or {}).get('lift_read') or '']))
    for ps in (payload.get('panel_sessions') or [])[:2]:
        qs = ps.get('questions') or []
        if qs:
            out.append('FOCUS GROUP IQ, ' + str(ps.get('title')) + ':\n' + '\n'.join(f"{q.get('q')}: " + '; '.join(f"{r['label']} {r['pct']}%" for r in (q.get('distribution') or [])[:5]) for q in qs[-5:]))
    if rd.get('what_to_do'):
        out.append('WHAT TO DO:\n' + '\n'.join(rd['what_to_do']))
    return [b for b in out if b.strip()]


def deck_context(run: dict, payload: dict) -> dict:
    """The deck core's ctx for a twin: view_context with material."""
    m = payload.get('meta') or {}
    name = str(m.get('name') or m.get('subject') or 'the twin')
    summ = view_summary(payload)
    data = {k: v for k, v in summ.items() if k in ('twin', 'subject', 'size_us', 'window', 'platform_home', 'format', 'ip_related')}
    material = [f"THE DIGITAL TWIN, {name}, part {i + 1}:\n{blk}" for i, blk in enumerate(twin_lines(payload))]
    return {'primary': None, 'cuts': [], 'extras': [], 'deck_source': 'twin',
            'view_context': {'view_id': PRODUCT_KEY, 'view_title': name[:80], 'data': data, 'material': material}}


# ---------------------------------------------------------------- how to
def pricing_lines(price_fn=None) -> list:
    """The twin pricing lines in the seat's currency; defaults without
    a price function."""
    def p(key):
        d = PRICING[key]
        if price_fn is None:
            return d
        try:
            v = float(price_fn(key, d) or 0)
            return v if v > 0 else d
        except Exception:
            return d

    def money(v):
        return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"
    return [f"Digital Twin - {money(p('digital_twins_iq'))} for the twin and its first three runs",
            f"Each further Greenlight, Launch, content or compare run - {money(p('digital_twin_run'))}",
            f"Spend Engine - {money(p('digital_twin_spend'))} to activate a plan for its window, daily runs included",
            f"Focus Group IQ - {money(p('digital_twin_panel'))} a session with {PANEL_QUESTIONS_INCLUDED} questions, then {money(p('digital_twin_panel_more'))} for each further {PANEL_QUESTIONS_INCLUDED}",
            f"Extra panelist - {money(p('digital_twin_panelist'))} for each panelist past the {PANEL_SEATS_INCLUDED} a session seats, billed with the session",
            "Prometheus usage - metered at the published rate on top of these, for the build and for every question in a room"]


HOW_TO = [
    {
        'id': 'twin', 'title': 'Build a twin',
        'answers': 'Who an audience is and how it behaves: its people, platforms and buying, twelve dials for how it responds, and where it sits in its lifecycle.',
        'put_in': ('Build a twin on the Twins tab walks you through it. First, what the twin stands for: a defined group from a profile (the audience of a title, a talent or a brand, pulled the way '
                   'Profile IQ pulls it, with the panelists seated from that audience) or a group you shape by demographics (gender, age, income, ethnicity, markets). Then what you will use it for, '
                   f'and how many panelists sit in its focus group rooms ({PANEL_SEATS_INCLUDED} come with a session; each one past that is priced). Or type the ask to Prometheus with a window if it is not the last 12 months.'),
        'type_this': ['Build a twin of Gilmore Girls Hulu viewers', 'Build a twin of Mark Rober for a product launch', 'Build a twin of women 25 to 44 in Dallas who shop at Target'],
        'attach': 'Nothing is needed. If you have a brief or a deck on the subject, attach it with the paper clip and the twin reads it.',
        'comes_back': 'The twin lands on the Twins tab in a few minutes, with its first three runs. Prometheus confirms in the chat; there is no email.',
        'cost_key': 'digital_twins_iq', 'export': 'Twins tab: CSV, Deck or PDF at the top of the twin page.',
    },
    {
        'id': 'greenlight', 'title': 'Greenlight Lab',
        'answers': 'The odds on a title or a bare idea: a seven in ten chance it clears your goal, the range of first-month viewers, buyers or listeners per platform, completion and return, which cohorts show up, the levers ranked by what they earn, and one package to run. Two candidates side by side when you ask.',
        'put_in': 'A logline, a title, a script or treatment, a trailer link; the platforms you are weighing; the goal (for example 4M viewers in the first month).',
        'type_this': ['Run Greenlight Lab on the Gilmore Girls twin: a new season on Hulu and Netflix, goal 4M viewers in the first month',
                      'What are the odds this logline clears 2M viewers on Peacock: a chef returns to her hometown diner after a scandal',
                      'Compare the Gilmore Girls and Mark Rober twins on the same idea'],
        'attach': 'Attach the script, treatment or deck with the paper clip; its text becomes the inputs of the run.',
        'comes_back': 'The run appears on the Greenlight Lab tab and in the twin\'s Runs list within a minute or two; Prometheus answers in the chat with the odds and a chip to open it.',
        'cost_key': 'digital_twin_run', 'export': 'The run exports with the twin (CSV, Deck, PDF).',
    },
    {
        'id': 'launch', 'title': 'Launch Lab',
        'answers': 'What a plan and budget do before any money goes out: three plans side by side (as allocated, signal-led, and the minimum that still hits the goal), odds and ranges week by week, the levers ranked by what they earn, and a holdout sized to prove lift at the lowest spend.',
        'put_in': 'The channels with spend (Meta $120K, Google $80K, TikTok $40K), the assets, the window in weeks, the goal.',
        'type_this': ['Run Launch Lab on the Gilmore Girls twin: Meta $120K, Google $80K, TikTok $40K over 6 weeks, goal 3M viewers',
                      'Run Launch Lab on this plan'],
        'attach': 'Attach the budget sheet (xlsx or csv) or paste the channel list; the channels and spend are read from it.',
        'comes_back': 'The three plans land on the Launch Lab tab with the week-by-week band; Set it live sends the plan you choose to the Spend Engine.',
        'cost_key': 'digital_twin_run', 'export': 'The run exports with the twin; the chosen plan carries into the Spend Engine moves list.',
    },
    {
        'id': 'spend', 'title': 'Spend Engine',
        'answers': 'The plan run forward day by day on the twin: daily spend and the signal by channel, the moves that earn more each day (move money, raise or cut, swap the lead asset) with the delta in plain words, A/B tests to run inside the flight, a lift read against a matched holdout, and a ledger of what the moves saved against the plan as allocated.',
        'put_in': 'A plan (channels with spend, assets, window, goal) or the Launch Lab plan you approved. Pick a mode: approve each move by chip, or set a rule (max daily shift, a floor per channel) and the moves inside it approve on their own.',
        'type_this': ['Put the signal-led plan in the Spend Engine on the rule: max 15% a day', 'Activate this plan in the Spend Engine: Meta $60K, YouTube $40K over 14 days, approve by chip',
                      'Approve the day 3 move on the Gilmore Girls plan'],
        'attach': 'Attach the budget sheet (xlsx or csv) or paste the channels.',
        'comes_back': 'The day strip, the channel table, the moves feed and the ledger on the Spend Engine tab. Approving a move writes it to the ledger and the moves list for your buyers. Export moves gives the CSV; the Attribution IQ tracking intake measures the plan with the package prefilled. Connected-account execution is switched on per account by Crosswalk; the Connect accounts card files the request.',
        'cost_key': 'digital_twin_spend', 'export': 'Export moves (CSV) on the Spend Engine tab; the plan also exports with the twin.',
    },
    {
        'id': 'panel', 'title': 'Focus Group IQ',
        'answers': 'A focus group Prometheus moderates for you (Focus Group IQ). Pick the twins or the cohorts in the room, ask anything in plain words, and Prometheus runs the room: the %s and people counts, the split by segment, a one-line read and a few panelist voices, in a minute or two. Yes/no, multiple choice, rating 1 to 5, would you pay $X, A vs B, a show of hands, and probes (why? what would change your mind?) that follow the last question.',
        'put_in': f'A name for the room (you can change it later on the room page), the twins or cohorts in the room (for example Gilmore Girls Hulu viewers, women 25 to 44; lapsed Netflix subscribers in Dallas), how many panelists sit in the room ({PANEL_SEATS_INCLUDED} come with a session; each one past that is priced and billed with the session), then your questions.',
        'type_this': ['Open a focus group with the Mark Rober twin and ask whether they would pay $29 for the Build Box', 'Ask them why', 'Show of hands, who watched season 1?',
                      'Ask the room: trailer A vs trailer B', 'Re-ask at $19'],
        'attach': 'Nothing is needed. Attach a trailer link, two loglines or a price sheet if the question is about them.',
        'comes_back': 'The % table, the voices and the read in the chat and on the Focus Group IQ tab, where the room, the live bars and the transcript sit. The session is saved on the twin and resumes where you left it.',
        'cost_key': 'digital_twin_panel', 'export': 'Focus Group IQ tab: Export (CSV or PDF) on the session.',
    },
]


def how_to(price_fn=None) -> dict:
    """The directions as data: five things, each with what it answers,
    what to put in, the lines to type, how to attach, what comes back,
    the cost and how to export. The dashboard renders it; Prometheus
    reads it aloud."""
    prices = {}
    for k, d in PRICING.items():
        v = d
        if price_fn is not None:
            try:
                v = float(price_fn(k, d) or 0) or d
            except Exception:
                v = d
        prices[k] = v

    def money(v):
        return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"
    items = []
    for it in HOW_TO:
        row = dict(it)
        if it['id'] == 'panel':
            row['cost'] = (f"{money(prices['digital_twin_panel'])} a session with {PANEL_QUESTIONS_INCLUDED} questions, then {money(prices['digital_twin_panel_more'])} for each further {PANEL_QUESTIONS_INCLUDED}; "
                           f"{money(prices['digital_twin_panelist'])} for each panelist past {PANEL_SEATS_INCLUDED}; Prometheus usage metered on top")
        elif it['id'] == 'twin':
            row['cost'] = f"{money(prices['digital_twins_iq'])}, with three runs included; Prometheus usage on the build metered on top"
        elif it['id'] == 'spend':
            row['cost'] = f"{money(prices['digital_twin_spend'])} for the plan window, daily runs included"
        else:
            row['cost'] = f"{money(prices[it['cost_key']])} a run"
        items.append(row)
    return {'title': 'How to use Digital Twins IQ',
            'intro': ('Digital Twins IQ builds a twin of an audience from what people actually do, then runs your plan, title or idea forward on it. '
                      'Everything here can be typed to Prometheus or done on this page. Type the example lines as written, with your own subject, '
                      'and attach files with the paper clip in the chat.'),
            'items': items, 'pricing': pricing_lines(price_fn),
            'where': 'Twins land on the Twins tab; lab runs on their lab tab and in the twin\'s Runs list; focus group sessions on the Focus Group IQ tab. Prometheus confirms every one in the chat.'}


def how_to_text(topic: str = '', price_fn=None) -> str:
    """The directions as plain text for a chat answer: one thing when
    the question names it, all five otherwise."""
    doc = how_to(price_fn)
    low = str(topic or '').lower()
    pick = []
    if re.search(r"\bdigital twins? iq\b|\btwin iq\b|\b(all|everything|whole|five)\b", low):
        pick = []            # the whole product: all five things
    elif re.search(r"\b(green ?light|odds|logline|idea)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'greenlight']
    elif re.search(r"\b(launch lab|launch|plan|budget)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'launch']
    elif re.search(r"\b(spend|moves?|daily|rule)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'spend']
    elif re.search(r"\b(panel|focus group|survey|room|moderat)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'panel']
    elif re.search(r"\b(build|make|create)\b.*\btwin\b|\btwin\b.*\b(build|built)\b", low):
        pick = [it for it in doc['items'] if it['id'] == 'twin']
    items = pick or doc['items']
    out = [doc['intro']] if not pick else []
    for it in items:
        out.append(f"{it['title']}. {it['answers']}")
        out.append(f"Put in: {it['put_in']}")
        out.append('Type to Prometheus: ' + ' | '.join(f'"{t}"' for t in it['type_this'][:3]))
        out.append(f"Files: {it['attach']}")
        out.append(f"What comes back: {it['comes_back']}")
        out.append(f"Cost: {it['cost']}. Export: {it['export']}")
    if not pick:
        out.append(doc['where'])
    return '\n\n'.join(out)


HOW_TO_RX = re.compile(r"\b(how (?:do|can|would|should) (?:i|we|you) (?:use|run|open|build|start|work|get)|how does .* work|what (?:is|does) (?:digital twins?|twin iq|greenlight lab|launch lab|spend engine|panel lab|focus group iq|focus group)|directions|instructions|walk me through|how to (?:use|run|build|open))\b", re.I)
TWIN_WORDS_RX = re.compile(r"\b(digital twins?|twins? iq|twin|greenlight lab|launch lab|spend engine|panel lab|focus group iq|focus group)\b", re.I)


def is_how_to_ask(text: str) -> bool:
    t = ' '.join(str(text or '').split())
    return bool(TWIN_WORDS_RX.search(t) and HOW_TO_RX.search(t))


__all__ = ['list_runs', 'load_twin', 'save_twin', 'has_access', 'is_admin', 'can_open', 'visible_runs', 'allowed_keys', 'find_twin', 'csv_rows', 'csv_text', 'csv_filename',
           'view_summary', 'twin_lines', 'deck_context', 'how_to', 'how_to_text', 'is_how_to_ask', 'pricing_lines', 'PRICING', 'PULL_TYPES', 'PANEL_QUESTIONS_INCLUDED',
           'PANEL_SEATS_INCLUDED', 'PANEL_SEATS_MAX', 'extra_panelists', 'panelists_for',
           'ACCESS_FLAG', 'RUNS_FIELD', 'PRODUCT_KEY', 'PRODUCT_LABEL', 'JOBS_PREFIX', 'REQUESTS_PREFIX', 'S3_PREFIX', 'S3_INDEX_KEY', 'S3_BUCKET', 'HOW_TO']
