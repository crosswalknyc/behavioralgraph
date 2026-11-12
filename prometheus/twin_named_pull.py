"""A digital twin of a named subject that has no Profile IQ profile yet
(2026-11-04).

A named subject's twin always sits on a Total Universe (Jenna
2026-10-27). When none is on file the audience is pulled through the
profile engine first and the twin is built from it when it lands. Until
today that pull posted a body the queue refused (no subject_rows, no
tu_demos, a guessed category, no run id) from a worker that had no queue
address, and came back empty without a word; the twin then built on
research alone and the requester never knew the audience was missing.

This module builds the pull the way the URL-list build does
(prometheus.twin_url_list, the approve-rescue pattern): the brief goes
through the real interpret step, _spec_from_draft converts the draft,
the body carries the run id minted at confirm, the requester, the
standing trailing-12 window and the ``then`` block the engine hands back
to the read worker, with the landed profile key on the inputs, the
moment the Total Universe registers. Phase one of the twin job posts and
stops; phase two runs when the engine hands the job back. Nothing waits
on the read worker.

Two doors share it: the Twins tab's Build a twin walkthrough
(digital_twins_api.dt_build) and the Prometheus intake
(prometheus.digital_twin_lane). Both decide at confirm whether the
audience is on file, say so with the price, and charge once: the
Profile IQ build plus the twin with its first two runs when the audience
must be pulled, the twin alone when it is already on file. A genuine
upstream build failure returns both charges with the partner-safe line
(the audience charge through the engine's own refund by run id, the
twin's through the then block). Nothing is held, nothing says rebuild.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import traceback

from . import twin_url_list as _TUL

SOURCE = 'digital_twins_named'
TAG = 'twin-named-pull'
FAIL_LINE = _TUL.FAIL_LINE
PROFILE_CREDITS = _TUL.PROFILE_CREDITS
TWIN_CREDITS = _TUL.TWIN_CREDITS
BUCKET = _TUL.BUCKET
# a twin that asked for an audience the engine was already building for another twin leaves its then block here;
# the engine fires every waiting twin for a run when that run's Total Universe registers (or fails)
WAITING_PREFIX = 'system/digital_twins/waiting/'

# plain words for the brief, by the twin's subject kind and format
_KIND_WORDS = {'audience': 'an audience', 'ip': 'a title (a series, a film, a book, a game or a podcast)', 'brand': 'a brand',
               'talent': 'a person', 'idea': 'an idea for a title'}
_FORMAT_WORDS = {'film': 'a film', 'series': 'a series', 'book': 'a book', 'game': 'a game', 'podcast': 'a podcast', 'creator': 'a creator'}
# the canonical BRAND CATEGORY that stands in when the interpret gave none the catalog knows
_FALLBACK_BY_FORMAT = {'film': 'MOVIE', 'series': 'SERIES - DRAMA', 'book': 'MEDIA', 'game': 'GAMES', 'podcast': 'PODCAST', 'creator': 'INFLUENCER/CREATOR'}
_FALLBACK_BY_KIND = {'talent': 'INFLUENCER/CREATOR', 'brand': 'RETAILERS', 'ip': 'MOVIE', 'idea': 'MOVIE', 'audience': 'MEDIA'}
_STATIC_CANONICAL = frozenset({
    'ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'APPAREL/FOOTWEAR', 'AUTOMOBILE', 'AUTOMOTIVE PARTS', 'B2B', 'BANKS', 'BEAUTY',
    'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EVENTS', 'FOOTWEAR', 'FRANCHISE',
    'GROCERY', 'INTIMATES', 'JEWELRY', 'MEMBERSHIP', 'NON PROFIT/CHARITY', 'PHARMA', 'QSR', 'RETAILERS', 'SECURITY', 'SHOPPING INTENT', 'TELECOM',
    'TICKETING', 'TOY', 'TRADING', 'TRAVEL', 'VENUE', 'WHERE THEY SHOP', 'WORKOUT FACILITY',
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'EMERGING TALENT', 'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER',
    'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST',
    'GAME PLAYERS', 'GAMES', 'GAMES - PLAYERS', 'MOVIE', 'PODCAST', 'VERTICAL SHORTS',
    'APP/PLATFORM', 'BROADCAST/CABLE', 'FAST CHANNEL', 'FAST PLATFORM', 'MEDIA', 'MOVIE THEATER', 'PLATFORMS', 'SEARCH ENGINE/AI', 'SOCIAL MEDIA',
    'STREAMING MUSIC', 'STREAMING VIDEO', 'STREAMING/PLATFORM', 'VIRTUAL MVPD/FAST', 'VIRTUAL MVPD FAST', 'VMVPD/FAST', 'VMVPD',
    'MILB', 'MLB', 'NBA', 'NFL', 'SPORTS ORGANIZATIONS', 'SPORTS ORGANIZATION', 'WNBA', 'HEALTHCARE', 'TRENDS'})


# ------------------------------------------------------------ catalog
def canonical_categories():
    """The canonical BRAND CATEGORY values (iq_rankers.MASTER_CATEGORIES
    flattened, plus the documented aliases); the static list when the
    module is not importable here."""
    out = set(_STATIC_CANONICAL)
    try:
        from iq_rankers import MASTER_CATEGORIES
        for vals in (MASTER_CATEGORIES or {}).values():
            for v in vals or []:
                out.add(str(v).strip().upper())
    except Exception:
        pass
    return out


def is_canonical_category(cat):
    c = str(cat or '').strip().upper()
    if not c:
        return False
    if c.startswith('SERIES - '):
        return True
    return c in canonical_categories()


def fallback_category(kind='', fmt=''):
    """A canonical category from the twin's own words about the subject."""
    return _FALLBACK_BY_FORMAT.get(str(fmt or '').lower()) or _FALLBACK_BY_KIND.get(str(kind or 'audience').lower()) or 'MEDIA'


def canonical_category(cat, kind='', fmt=''):
    c = str(cat or '').strip().upper()
    return c if is_canonical_category(c) else fallback_category(kind, fmt)


def audience_on_file(s3, subject, window=None):
    """The subject's Total Universe key when one is already in the
    catalog (the corpus anchor first, then the dashboard catalog by
    name, each needing the subject's identity words), else ''. Never
    raises; without a catalog read it answers ''."""
    subject = ' '.join(str(subject or '').split())
    if not subject:
        return ''
    try:
        from migration.digital_twin_synthesis import find_profile_key
    except Exception:
        return ''
    anchors = None
    try:
        from migration import corpus_catalog as _cc
        if getattr(_cc, 'enabled', lambda: True)():
            anchors = _cc.anchors_for(subject, window=window)
    except Exception:
        anchors = None
    try:
        return str(find_profile_key(s3, subject, anchors) or '')
    except Exception:
        return ''


def pullable(inputs):
    """A build whose audience can sit on a Total Universe: a named subject
    (a group shaped by demographics is Gen Pop cut to the picks; an idea
    has no audience on file by definition and reads from research) with
    no profile picked."""
    inputs = inputs if isinstance(inputs, dict) else {}
    if str(inputs.get('source') or '').lower() == 'demographics' or inputs.get('demographics'):
        return False
    if str(inputs.get('subject_kind') or '').lower() == 'idea':
        return False
    if str(inputs.get('profile_key') or '').strip():
        return False
    return bool(str(inputs.get('subject') or inputs.get('name') or '').strip())


def needs_pull(inputs, s3):
    """True when the build must pull the audience first: a pullable
    subject with no audience on file."""
    inputs = inputs if isinstance(inputs, dict) else {}
    if not pullable(inputs):
        return False
    subject = str(inputs.get('subject') or inputs.get('name') or '').strip()
    if not subject:
        return False
    return not audience_on_file(s3, subject, inputs.get('window'))


# ---------------------------------------------------------------- copy
def price(username=None):
    """The same three numbers the list build prices: the audience build,
    the twin with its first two runs, each further run."""
    return _TUL.price(username)


def audience_line(subject, on_file=''):
    if on_file:
        name = _display_name(on_file)
        return f"- Audience: built on the {name} audience already on file"
    return (f"- Audience: no {subject} audience is on file yet, so it is pulled first (usually under an hour), "
            f"then the twin is built from it")


def price_line(username=None, pull=True):
    pr = price(username)
    if pull:
        return f"- {pr['line']}"
    if pr['twin_usd'] <= 0:
        return f"- Price: the twin and its first two runs are included (each group you open on it is priced per group); each further run {pr['run_label']}"
    return f"- Price: {pr['twin_label']} for the twin and its first two runs; each further run {pr['run_label']}"


def _display_name(key):
    stem = str(key or '').rsplit('/', 1)[-1]
    stem = re.sub(r"\.csv$", '', stem, flags=re.I)
    stem = re.sub(r"_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?$", '', stem)
    return stem.replace('_', ' ').strip() or 'that'


def promise(subject):
    return (f"On it. No {subject} audience is on file yet, so I am pulling it first (that is the long part, usually under an hour), "
            f"then building the twin from it. It lands on the Twins tab of Digital Twins IQ and I will confirm here and by email when it is ready.")


def waiting_stage(subject):
    return f"pulling the {subject} audience first (usually under an hour); the twin follows"


def ready_message(name, people, subject):
    return (f"Your twin is ready: {name} is live on the Twins tab of Digital Twins IQ, {people} people in the US, built on the {subject} "
            f"audience pulled first; that audience is in Select Profile too. Ask the twin anything here, or open it.")


# ------------------------------------------------------------ charging
def charge(user, uname, subject, *, pull, profile_run_id='', twin_job_id='', consume_credit=None, is_free=None):
    """Charge once at confirm: the audience build and the twin when the
    audience must be pulled (the list build's charge_both, the audience
    charge carrying the run id the engine refunds by), the twin alone
    when it is on file. Returns (charged, funds_error)."""
    if pull:
        return _TUL.charge_both(user, uname, subject, profile_run_id=profile_run_id, twin_job_id=twin_job_id,
                                consume_credit=consume_credit, is_free=is_free)
    charged = {'profile': 0, 'twin': 0}
    free, consume_credit = _TUL.billing(user, uname, consume_credit=consume_credit, is_free=is_free)
    if free or consume_credit is None:
        return charged, None
    if _TUL.twin_included(uname) or TWIN_CREDITS <= 0:
        return charged, None          # the twin is included (Jenna 2026-11-05): the group is the unit
    try:
        from migration.digital_twins import PULL_TYPES
        ok = bool(consume_credit(uname, description=f"Digital Twin: {subject}", pull_type=PULL_TYPES['digital_twins_iq'],
                                 credits_used=TWIN_CREDITS, job_id=str(twin_job_id)))
    except Exception:
        traceback.print_exc()
        ok = False
    if not ok:
        pr = price(uname)
        return charged, (f"That step runs {pr['twin_label']} and your account cannot cover it right now. "
                         f"Add funds or ask your admin, and it runs the moment you are set.")
    charged['twin'] = TWIN_CREDITS
    return charged, None


refund = _TUL.refund


# ------------------------------------------------------- the interpret
def named_scope(subject, ask_text=''):
    """The services the twin's own audience definition names as where its
    people watch or listen ('Gilmore Girls Hulu viewers', 'Yellowstone
    viewers on Peacock', 'X Viewers (HBO Max & Amazon Prime)'), as the
    house names them; [] when it names none. The subject's words first,
    then the words the twin was asked for in."""
    try:
        from . import named_platforms as _N
        for text in (subject, ask_text):
            hit = _N.find(text) if str(text or '').strip() else None
            if hit and hit.get('how') == 'audience' and hit.get('platforms'):
                return list(hit['platforms'])
        # a name that carries its services after the audience noun in brackets
        m = re.search(r"\b(?:viewers?|watchers?|streamers?|listeners?)\s*\(([^()]+)\)\s*$", str(subject or ''), re.I)
        if m:
            return _N._platforms_in(m.group(1), True)
        # ... or in front of the title ('Hulu Gilmore Girls Viewers'): only a name that can be nothing but the service
        m = re.match(r"^\s*(.+?)\s+(?:viewers?|watchers?|streamers?|listeners?)\s*$", str(subject or ''), re.I)
        if m:
            head = m.group(1).lower()
            for alias, canon in sorted(_N.PLATFORM_ALIASES.items(), key=lambda kv: -len(kv[0])):
                if alias not in _N._LOOSE and re.match(re.escape(alias) + r"(?![a-z0-9+])\s+\S", head):
                    return [canon]
    except Exception:
        pass
    return []


def carry_audience_scope(draft, subject, ask_text=''):
    """The twin's audience definition reaches the build (2026-11-12).

    'Gilmore Girls Hulu viewers' was ordered from a twin on Nov 5 and
    built as a general audience named that way: no title, no service, no
    watch placements, and Hulu read 73.5% on a Hulu universe. The pull's
    brief went through the model read alone; the step that binds a named
    service on every other order never ran.

    The service the definition names is bound the way the interpret step
    binds it (prometheus.named_platforms): strict on those services, a
    viewers universe of the title, seeded from its watch placements. The
    twin keeps its name and its window. Mutates and returns the draft;
    never raises; a definition that names no service is left as it is."""
    try:
        plats = named_scope(subject, ask_text)
        if not plats or not isinstance(draft, dict):
            return draft
        from . import named_platforms as _N
        if not _N._is_title_audience(draft):
            return draft            # a brand, a person or a persona: the service is not a viewing scope
        title = str(draft.get('content_show') or '').strip() or _N._title_without(_N._bare_title(subject), plats)
        if not title or _N._canon(title)[0]:
            return draft            # the service's own audience ('Netflix viewers') is not a title on the service
        draft.setdefault('subject', subject)
        _N._apply(draft, plats, 'audience')
        verb = str(draft.get('consumer_verb') or 'viewers').lower()
        if str(draft.get('audience_type') or 'general').strip().lower() in ('', 'general'):
            draft['audience_type'] = verb
        # the anchor row is the title in its own category, never the audience's name as a title
        title = str(draft.get('content_show') or '').strip()
        if title:
            rows = []
            for row in (draft.get('subject_rows') or []):
                try:
                    col, val = row[0], row[1]
                except Exception:
                    continue
                if ' '.join(str(val).lower().split()) == ' '.join(str(subject).lower().split()):
                    val = title
                rows.append([col, val])
            draft['subject_rows'] = rows or draft.get('subject_rows')
    except Exception:
        traceback.print_exc()
    return draft


def audience_prompt(subject, kind='audience', purpose='', window=None, platform_home='', fmt='', ask_text=''):
    """The brief the interpret reads: a fresh Total Universe of the
    subject on the twin's window, named by the house naming rule."""
    w = _TUL._normalize_window(window)
    what = _FORMAT_WORDS.get(str(fmt or '').lower()) or _KIND_WORDS.get(str(kind or 'audience').lower(), 'an audience')
    lines = [f"run a profile on {subject}", '',
             (f"VERIFIED CONTEXT (already researched, treat as fact): no Profile IQ profile of {subject} is on file, so this is a fresh "
              f"new_build, never an existing_match and never a cut: emit no addon_cuts and use the window {w['start']} to {w['end']}. "
              f"{subject} is {what}. The universe is the US people who engaged with {subject} in that window; size it from researched "
              f"public anchors and describe who they are in persona_notes.")]
    scope = named_scope(subject, ask_text)
    if scope:
        on = scope[0] if len(scope) == 1 else ', '.join(scope[:-1]) + ' and ' + scope[-1]
        lines.append(f"The audience is defined by where it watches: only the people who watched or listened on {on} in that window. "
                     f"List exactly {on} in named_platforms, and size and describe that audience, never the title's audience everywhere.")
    if str(platform_home or '').strip():
        lines.append(f"Its home platform is {str(platform_home).strip()}.")
    p = ' '.join(str(purpose or '').split()).strip(' .')
    if p:
        lines.append(f"The profile is pulled for a digital twin that will be used to {p}; that use never narrows the universe.")
    return '\n'.join(lines)


def interpret_draft(prompt, usage_extras=None, draft_fn=None):
    return _TUL.interpret_draft(prompt, usage_extras=usage_extras, draft_fn=draft_fn, tag=TAG)


def fallback_draft(subject, kind='audience', fmt='', window=None, s3=None, gen_pop_rows=None):
    """A US-shaped draft when the model read fails: the Gen Pop demo shape
    (the engine's own passes reshape it from the brief), a canonical
    category from the twin's words, a messy size. Never a round sample."""
    cat = fallback_category(kind, fmt)
    rows = gen_pop_rows
    if rows is None:
        try:
            from migration.digital_twin_synthesis import _gen_pop_rows
            rows = _gen_pop_rows(s3) if s3 is not None else []
        except Exception:
            rows = []
    demos = {}
    for r in rows or []:
        col = str(r.get('Column') or '').strip().upper()
        if col in _TUL._DEMO_COLUMNS:
            try:
                bp = float(str(r.get('Brand Penetration (Row)') or '').replace('%', '').strip())
            except (TypeError, ValueError):
                continue
            demos.setdefault(col, {})[str(r.get('Value') or '').strip()] = round(bp, 4)
    if not demos:
        demos = {'GENDER': {'FEMALE': 50.7391, 'MALE': 48.1227, 'NON-BINARY': 1.1382},
                 'AGE': {'18-24': 11.4731, '25-34': 17.6218, '35-44': 17.2109, '45-54': 15.8873, '55-64': 16.3312, '65 OR OLDER': 21.4757}}
    h = int(hashlib.sha256(f"{subject}|named-pull|{kind}|{fmt}".encode()).hexdigest()[:8], 16)
    base = 9_400 + h % 41_000
    sample = base + 1 if base % 10 == 0 else base
    w = _TUL._normalize_window(window)
    return {'subject': subject, 'brand_category': cat, 'audience_type': 'general', 'decision': 'new_build', 'run_avid': True,
            'subject_rows': [[cat, subject]], 'extra_rows': [], 'tu_demos': demos, 'avid_demos': demos,
            'subject_raw_tu': sample, 'subject_raw_avid': max(600, int(sample * 0.23) + 3),
            'universe_anchor': int(sample * 32.99), 'engaged_share': 1.0,
            'anchor_source': f"US people who engaged with {subject} in the window",
            'persona_notes': f"{subject}: the US people who engaged with {subject} between {w['start']} and {w['end']}.",
            'date_range': {'start': w['start'], 'end': w['end']}, 'date_range_explicit': True}


def _draft_hygiene(draft, subject, kind='audience', fmt='', window=None):
    """The parts the twin decides, whatever the model said: a fresh build,
    no cuts, the twin's window, a canonical category, a non-empty anchor
    row. The interpret's own subject naming stands (the house rule names
    the Total Universe; the twin keeps the requester's words)."""
    d = dict(draft or {})
    d['subject'] = ' '.join(str(d.get('subject') or subject).split())[:140] or subject
    d.pop('name', None)
    d['decision'] = 'new_build'
    d['run_avid'] = True
    for k in ('addon_cuts', 'existing_match_s3_key', 'existing_match_display_name', 'existing_match_days_old', 'subiq', 'derive_type', 'cut_label',
              'universe_mode', 'universe_note', 'event_window'):
        d.pop(k, None)
    d['addon_cuts'] = []
    d['brand_category'] = canonical_category(d.get('brand_category'), kind, fmt)
    w = _TUL._normalize_window(window)
    d['date_range'] = {'start': w['start'], 'end': w['end']}
    d['date_range_explicit'] = True
    rows = []
    for row in (d.get('subject_rows') or []):
        try:
            col, val = (row[0], row[1]) if isinstance(row, (list, tuple)) else (row.get('column'), row.get('value'))
        except Exception:
            continue
        if str(col or '').strip() and str(val or '').strip():
            rows.append([col, val])
    if not rows:
        rows = [[d['brand_category'], d['subject']]]
    d['subject_rows'] = rows
    if not isinstance(d.get('tu_demos'), dict) or not d.get('tu_demos'):
        d['tu_demos'] = fallback_draft(subject, kind, fmt, window)['tu_demos']
    if not isinstance(d.get('avid_demos'), dict) or not d.get('avid_demos'):
        d['avid_demos'] = d['tu_demos']
    return d


def spec_body(draft, *, subject, kind='audience', fmt='', window=None, run_id, requester, spec_from_draft=None, prompt=''):
    """The queue body for the audience build. ``spec_from_draft`` is the
    chat module's converter (injected in tests)."""
    d = _draft_hygiene(draft, subject, kind, fmt, window)
    d = carry_audience_scope(d, subject, prompt)          # the service the twin's audience is defined on reaches the build
    if spec_from_draft is None:
        spec_from_draft = SPEC_FROM_DRAFT or _converter()
    spec = dict(spec_from_draft(d) or {})
    w = _TUL._normalize_window(window)
    spec['name'] = ' '.join(str(spec.get('name') or d['subject']).split())[:140] or subject
    if not str(spec.get('file_stem') or '').strip():
        spec['file_stem'] = re.sub(r"[^A-Za-z0-9]+", '_', spec['name']).strip('_')[:80] or 'Subject'
    spec['brand_category'] = canonical_category(spec.get('brand_category') or d['brand_category'], kind, fmt)
    spec['date_range'] = f"{w['start']} TO {w['end']}"
    spec['addon_cuts'] = []
    if not isinstance(spec.get('subject_rows'), list) or not spec['subject_rows']:
        spec['subject_rows'] = [(spec['brand_category'], spec['name'])]
    if not isinstance(spec.get('tu_demos'), dict) or not spec['tu_demos']:
        spec['tu_demos'] = d['tu_demos']
    if not isinstance(spec.get('avid_demos'), dict) or not spec['avid_demos']:
        spec['avid_demos'] = spec['tu_demos']
    spec['subject_raw_tu'] = _messy(spec['name'], spec.get('subject_raw_tu') or d.get('subject_raw_tu'))
    av = spec.get('subject_raw_avid') or d.get('subject_raw_avid')
    spec['subject_raw_avid'] = min(_messy(f"{spec['name']}|avid", av, default_if_missing=2937), spec['subject_raw_tu'])
    req = requester if isinstance(requester, dict) else {}
    uname = str(req.get('username') or '').strip()
    email = str(req.get('email') or '').strip()
    body = {'run_id': run_id, 'decision': 'new_build', 'run_avid': True, 'spec': spec,
            'user_email': email or uname or 'prometheus', 'username': uname, 'email_to': '',
            'prompt': (prompt or f"Build a twin of {subject}")[:4000],
            'source': SOURCE, 'requested_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        from . import seams as _seams
        body = _seams.tag_queue(body)
    except Exception:
        pass
    return body


SPEC_FROM_DRAFT = None      # test hook: a converter standing in for the chat module's _spec_from_draft


def _converter():
    from prometheus.legacy.chat import _spec_from_draft  # type: ignore
    return _spec_from_draft


def _messy(subject, value, default_if_missing=9873):
    """An int sample size with a non-zero last digit. A value the
    converter already sized and jittered (anchor-derived, messy) stands;
    only a missing, round or placeholder value is jittered here."""
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size, _looks_round
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            v = 0
        if v > 0 and not _looks_round(v):
            return v
        return int(ensure_messy_sample_size(subject, value, default_if_missing=default_if_missing))
    except Exception:
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            v = 0
        if v <= 0:
            v = int(default_if_missing)
        v += int(hashlib.sha256(f"{subject}|{v}".encode()).hexdigest()[:6], 16) % 97
        return v + 1 if v % 10 == 0 else v


def then_block(inputs, *, job_id, username, extras=None):
    """What rides the queue body as ``then``: the twin that waits on the
    audience. The engine hands it back to the read worker with the landed
    profile key on the inputs the moment the Total Universe registers; on
    a genuine build failure it ends the twin with the same line and
    returns the twin's own charge."""
    keep = {k: v for k, v in dict(inputs or {}).items() if k not in ('pull_profile',)}
    keep.update({'from_list': False, 'pull_audience': True, 'profile_key': '', 'source': 'profile', 'op': 'build'})
    return {'kind': 'twin_build', 'job_id': str(job_id), 'username': str(username or ''), 'inputs': keep,
            'extras': dict(extras or {}), 'charged': dict((inputs or {}).get('charged') or {})}


def waiting_key(run_id, job_id):
    return f"{WAITING_PREFIX}{re.sub(r'[^A-Za-z0-9_-]+', '', str(run_id or ''))}/{re.sub(r'[^A-Za-z0-9_-]+', '', str(job_id or ''))}.json"


def register_waiting(s3, bucket, run_id, then, log=print):
    """A twin whose audience the engine is already building for someone
    else (the listener's per-subject lock answered 409 with that run id)
    leaves its then block where the engine looks when that run lands."""
    if s3 is None or not run_id or not then:
        return ''
    key = waiting_key(run_id, then.get('job_id'))
    try:
        s3.put_object(Bucket=bucket or BUCKET, Key=key, Body=json.dumps(then, default=str).encode('utf-8'), ContentType='application/json')
        return key
    except Exception as e:
        log(f"[{run_id}] named pull: waiting record skipped: {e}")
        return ''


queue_profile = _TUL.queue_profile
profile_status = _TUL.profile_status
landed_key = _TUL.landed_key


def queue_profile_for_twin_job(inputs, *, job_id, username, run_id='', s3=None, bucket=None, progress=None, usage_extras=None,
                               draft_fn=None, spec_from_draft=None, post=None, status_fn=None, on_file=None, log=print):
    """Phase one of a named build, run inside the twin job: the brief, the
    draft, the spec body with the ``then`` block, the queue post. Returns
    a dict with state 'queued' (run_id set), 'landed' (tu_key set: an
    earlier attempt's audience already registered), 'on_file' (tu_key
    set: the catalog holds it after all), 'failed' / 'cancelled' (the
    engine ended an earlier attempt), or 'not_queued' (error set). Never
    waits: the engine hands the twin back when the audience lands."""
    subject = ' '.join(str(inputs.get('subject') or inputs.get('name') or '').split())[:140]
    if not subject:
        return {'tu_key': '', 'run_id': '', 'state': 'not_queued', 'error': 'a twin needs a subject', 'body': None}
    window = _TUL._normalize_window(inputs.get('window'))
    kind = str(inputs.get('subject_kind') or 'audience')
    fmt = str(inputs.get('format') or '')
    run_id = run_id or str(inputs.get('profile_run_id') or '') or secrets.token_urlsafe(10)
    # a restarted job never posts the same audience twice: the run it already queued is the run
    prior = profile_status(run_id, status_fn=status_fn)
    if prior:
        key = landed_key(prior)
        st = str(prior.get('status') or '')
        if key:
            return {'tu_key': key, 'run_id': run_id, 'state': 'landed', 'body': None}
        if st in ('error', 'failed', 'cancelled'):
            return {'tu_key': '', 'run_id': run_id, 'state': 'cancelled' if st == 'cancelled' else 'failed', 'body': None}
        if st and st != 'unknown':
            log(f"[{run_id}] named pull: the {subject} audience is already {st}; waiting on it")
            return {'tu_key': '', 'run_id': run_id, 'state': 'queued', 'body': None, 'resumed': True}
    # the catalog may hold it after all (built since the confirm, or an anchor the confirm's lookup missed)
    try:
        found = (on_file or audience_on_file)(s3, subject, window)
    except Exception:
        found = ''
    if found:
        return {'tu_key': found, 'run_id': run_id, 'state': 'on_file', 'body': None}
    if callable(progress):
        progress(f"reading the {subject} audience")
    prompt = audience_prompt(subject, kind, str(inputs.get('purpose') or ''), window, platform_home=str(inputs.get('platform_home') or ''), fmt=fmt,
                             ask_text=str(inputs.get('ask_text') or ''))
    draft = interpret_draft(prompt, usage_extras=usage_extras, draft_fn=draft_fn)
    if not draft:
        log(f"[{run_id}] named pull: model read unavailable; the US-shaped draft stands in")
        draft = fallback_draft(subject, kind, fmt, window, s3=s3)
    requester = {'username': username, 'email': str(inputs.get('requester_email') or '')}
    try:
        body = spec_body(draft, subject=subject, kind=kind, fmt=fmt, window=window, run_id=run_id, requester=requester,
                         spec_from_draft=spec_from_draft, prompt=str(inputs.get('ask_text') or ''))
    except Exception as e:
        # the converter refused the subject (a request line is not an audience): nothing is queued, both charges come back
        traceback.print_exc()
        log(f"[{run_id}] named pull: the brief could not become a build: {e}")
        return {'tu_key': '', 'run_id': run_id, 'state': 'not_queued', 'error': f"the brief could not become a build: {str(e)[:200]}", 'body': None}
    body['then'] = then_block(inputs, job_id=job_id, username=username, extras=usage_extras)
    if callable(progress):
        progress('the audience is lined up')
    rid, err = queue_profile(body, post=post)
    if not rid:
        if 'already in progress' in str(err):
            # the engine is already building this audience for another twin: wait on that run, and leave the then
            # block where the engine fires every waiting twin when the run lands
            other = ''
            m = re.search(r"existing_run_id['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_-]{4,64})", str(err))
            if m:
                other = m.group(1)
            if other and other != run_id:
                register_waiting(s3, bucket, other, body['then'], log=log)
                log(f"[{run_id}] named pull: the {subject} audience is already building as {other}; the twin waits on it")
                return {'tu_key': '', 'run_id': other, 'state': 'queued', 'body': body, 'resumed': True, 'joined': True}
            log(f"[{run_id}] named pull: the audience is already building; waiting on it")
            return {'tu_key': '', 'run_id': run_id, 'state': 'queued', 'body': body, 'resumed': True}
        log(f"[{run_id}] named pull: {err}")
        return {'tu_key': '', 'run_id': run_id, 'state': 'not_queued', 'error': err, 'body': body}
    return {'tu_key': '', 'run_id': rid, 'state': 'queued', 'body': body}


def run_pull_in_job(inputs, username, job_id, rec, status_write, *, s3=None, bucket=None, usage_extras=None, log=print, **kw):
    """The twin job's first phase for a named subject with no audience on
    file. Returns the profile key when the audience is there (the build
    continues), None when the audience is queued (the job stops here; the
    engine hands the twin back with the key when the Total Universe
    registers), or '' after a genuine failure (refund, the partner-safe
    line)."""
    subject = ' '.join(str(inputs.get('subject') or inputs.get('name') or 'the audience').split())

    def _progress(msg):
        rec['stage'] = str(msg or f"pulling the {subject} audience first")
        status_write(job_id, rec)
    res = queue_profile_for_twin_job(inputs, job_id=job_id, username=username, s3=s3, bucket=bucket, progress=_progress,
                                     usage_extras=usage_extras, log=log, **kw)
    rec['profile_run_id'] = res.get('run_id') or inputs.get('profile_run_id') or ''
    rec['profile_state'] = res.get('state')
    charged = inputs.get('charged') if isinstance(inputs.get('charged'), dict) else {}
    if res.get('tu_key'):
        if res.get('state') == 'on_file' and int(charged.get('profile') or 0) > 0:
            # the audience was never pulled for this twin: its charge comes back here
            refund(username, int(charged.get('profile') or 0), f"Digital Twin audience build: {subject} (already on file)", job_id=str(rec.get('profile_run_id') or ''))
            charged['profile'] = 0
            inputs['charged'] = charged
        rec['profile_key'] = res['tu_key']
        rec['stage'] = 'the audience is in; building the twin'
        status_write(job_id, rec)
        return res['tu_key']
    if res.get('state') == 'queued':
        rec.update({'status': 'waiting', 'queued': False, 'stage': waiting_stage(subject), 'waiting_on_profile': True})
        if res.get('joined'):
            rec['joined_run'] = True
        status_write(job_id, rec)
        return None
    if res.get('state') == 'not_queued':
        # the audience never reached the engine: both charges come back here. (A run the engine itself refused
        # or the requester stopped returns both from the engine: the audience charge by its run id, the twin's
        # through the then block.)
        refund(username, int(charged.get('twin') or 0), f"Digital Twin: {subject}", job_id=str(job_id))
        refund(username, int(charged.get('profile') or 0), f"Digital Twin audience build: {subject}", job_id=str(rec.get('profile_run_id') or ''))
    rec.update({'status': 'error', 'stage': 'stopped', 'finished_at': time.time(), 'error': FAIL_LINE,
                'detail': str(res.get('error') or res.get('state') or '')[:300]})
    status_write(job_id, rec)
    return ''


__all__ = ['SOURCE', 'TAG', 'FAIL_LINE', 'WAITING_PREFIX', 'canonical_categories', 'is_canonical_category', 'fallback_category', 'canonical_category',
           'audience_on_file', 'pullable', 'needs_pull', 'price', 'audience_line', 'price_line', 'promise', 'waiting_stage', 'ready_message', 'charge', 'refund',
           'audience_prompt', 'interpret_draft', 'fallback_draft', 'spec_body', 'then_block', 'waiting_key', 'register_waiting',
           'queue_profile_for_twin_job', 'run_pull_in_job']
