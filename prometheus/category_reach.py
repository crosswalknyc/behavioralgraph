"""Category reach in Prometheus (2026-11-11, Armine).

"What percent of Wicked Tuna viewers engage with workwear brands", "how
does the automobile category index for this audience", and the deck slide
that groups purchase behavior into named categories all want one figure:
the share of the audience with at least one brand in the group, and how
that compares with the US. The figure comes from one place,
``migration.category_rollup``; this module is only the plumbing that puts
it in front of a chat answer and onto a deck card, so the deck, the
Profiles page and the chat print the same number for the same profile
and the same group.

- ``digest_section``: one compact line per behavioral category, carried
  by every profile digest, so a whole-category ask is answered from it.
- ``block``: for an ask about a category or a named group of brands, the
  bound figure (and the brands it covers) the answer quotes.
- ``annotate_plan``: for a deck slide whose cards are groups of brands,
  the figure on each card and the line that says what it means.
- a named group of brands ("workwear and tools") is settled once per
  profile and kept, so the deck and a later chat answer use one member
  list for one name.

Fail-safe throughout: anything that cannot be settled returns '' or the
plan untouched; nothing here raises into a turn or a deck job.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback

try:
    from migration import category_rollup as cr
except Exception:   # a host whose `migration` package is the build box's own: load ours by path
    import importlib.util as _ilu
    import sys as _sys
    _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migration', 'category_rollup.py')
    _spec = _ilu.spec_from_file_location('category_rollup', _p)
    cr = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(cr)
    _sys.modules.setdefault('category_rollup', cr)

GROUP_PREFIX = 'system/prometheus/category_groups/'
MAX_MEMBERS = 30
MIN_CANDIDATE_PCT = 1.0
MAX_CANDIDATES = 1500

# Plain words for a behavioral category -> the column that carries it.
_ALIASES = (
    (r"auto(?:mobile|motive)?s?|cars?|car makers?|vehicles?", ('AUTOMOBILE',)),
    (r"(?:auto(?:motive)?|car) parts", ('AUTOMOTIVE PARTS',)),
    (r"qsr|quick[- ]serv(?:ice|e)(?: restaurants?)?|fast[- ]food", ('QSR',)),
    (r"restaurants?|casual dining|dining|where they dine", ('WHERE THEY DINE', 'CASUAL DINING', 'QSR')),
    (r"retail(?:ers?)?|stores?|shopping|where they shop", ('WHERE THEY SHOP', 'RETAILERS')),
    (r"most purchased(?: brands)?|purchased brands|purchases?", ('MOST PURCHASED BRANDS',)),
    (r"cpg|packaged goods|grocery brands?", ('CPG',)),
    (r"apparel|clothing|clothes|footwear|shoes?|fashion", ('APPAREL/FOOTWEAR', 'APPAREL', 'FOOTWEAR')),
    (r"beauty|wellness|cosmetics|skin ?care|personal care", ('BEAUTY/WELLNESS', 'BEAUTY')),
    (r"home|outdoor|home and outdoor|home goods", ('HOME/OUTDOOR',)),
    (r"accessor(?:y|ies)", ('ACCESSORIES',)),
    (r"pets?|pet care|pet food", ('PETS',)),
    (r"toys?", ('TOYS', 'TOY')),
    (r"beverages?|drinks?", ('BEVERAGE',)),
    (r"tech(?:nology)?(?: brands?| devices?)?|devices?|electronics", ('TECHNOLOGY BRAND', 'TECHNOLOGY/DEVICE')),
    (r"heavy machinery|machinery", ('HEAVY MACHINERY',)),
    (r"streaming(?: video| platforms?| services?)?|svod", ('STREAMING/PLATFORM', 'STREAMING VIDEO')),
    (r"(?:streaming )?music(?: streaming| services?)?|audio streaming", ('STREAMING/MUSIC', 'STREAMING MUSIC')),
    (r"social(?: media)?(?: platforms?| networks?)?", ('SOCIAL MEDIA',)),
    (r"search(?: engines?)?|ai (?:tools|assistants)|search and ai", ('SEARCH ENGINE/AI',)),
    (r"apps?|app usage|platforms?", ('APP/PLATFORM USAGE', 'APP/PLATFORM')),
    (r"media|news(?: outlets?| sites?)?|publishers?", ('MEDIA',)),
    (r"broadcast|cable|networks?|tv networks?", ('BROADCAST/CABLE',)),
    (r"vmvpd|fast|live tv|virtual mvpd", ('VIRTUAL MVPD FAST', 'VIRTUAL MVPD/FAST', 'VMVPD/FAST')),
    (r"bank(?:s|ing)?", ('BANKING', 'BANKS')),
    (r"digital bank(?:s|ing)?|neobanks?", ('DIGITAL BANKING',)),
    (r"credit(?: cards?| providers?)?|card issuers?", ('CREDIT PROVIDER',)),
    (r"invest(?:ing|ments?)|brokerages?", ('INVESTMENTS',)),
    (r"trading(?: apps?| platforms?)?", ('TRADING',)),
    (r"insur(?:ance|ers?)", ('INSURANCE',)),
    (r"telecom|wireless|carriers?|mobile carriers?|phone carriers?", ('TELECOM',)),
    (r"travel|airlines?|hotels?", ('TRAVEL',)),
    (r"betting|sports ?books?|sports betting|gambling", ('BETTING',)),
    (r"pharmac(?:y|ies)|drug ?stores?", ('PHARMACY',)),
    (r"gyms?|fitness clubs?|workout facilit(?:y|ies)", ('WORKOUT FACILITY',)),
    (r"ticket(?:ing|s)?", ('TICKETING',)),
    (r"games?|gaming|video games?", ('GAMES',)),
    (r"events?", ('EVENTS',)),
    (r"venues?", ('VENUE',)),
    (r"theme parks?|amusement parks?", ('AMUSEMENT PARKS',)),
    (r"movie theaters?|theaters?|cinemas?", ('MOVIE THEATER',)),
    (r"golf", ('GOLF',)),
    (r"tennis", ('TENNIS',)),
    (r"sports teams?|teams?", ('SPORTS TEAM',)),
    (r"podcasts?", ('PODCAST',)),
    (r"franchises?", ('FRANCHISE',)),
    (r"charit(?:y|ies)|non ?profits?", ('NON PROFIT/CHARITY',)),
    (r"colleges?|universit(?:y|ies)", ('COLLEGE/UNIVERSITY',)),
)
_ALIAS_RX = [(re.compile(r"^(?:" + rx + r")$", re.I), cols) for rx, cols in _ALIASES]

_MEASURE_RX = re.compile(
    r"\b(?:percent(?:age)?|share|portion|how many|how much|proportion|"
    r"index(?:es|ing)?|over[- ]?index(?:es|ing)?|under[- ]?index(?:es|ing)?|"
    r"reach|penetration|%)", re.I)
_GROUP_WORD_RX = re.compile(
    r"\b(?:categor(?:y|ies)|brands|at least one|any (?:one )?of|overall|"
    r"as a (?:group|whole|category)|combined|rolled? up|roll[- ]?up|in total|altogether)\b", re.I)
_VERB = (r"(?:engag(?:e|es|ed|ing) with|interact(?:s|ed|ing)? with|buy(?:s|ing)?|bought|"
         r"purchas(?:e|es|ed|ing)|shop(?:s|ped|ping)?(?: for| at| with)?|use[sd]?|using|"
         r"watch(?:es|ed|ing)?|stream(?:s|ed|ing)?|own(?:s|ed)?|wear(?:s|ing)?|visit(?:s|ed|ing)?|"
         r"touch(?:es|ed)?|spend(?:s|ing)? (?:on|with)|are (?:into|on)|is (?:into|on))")
_GROUP_TAIL = r"(?:brands?|categor(?:y|ies)|companies|retailers|chains|services|platforms|makers|labels)"
_PHRASE = r"[A-Za-z0-9][A-Za-z0-9 &/,'+\-]{1,58}?"
_GROUP_RXS = (
    re.compile(r"\b" + _VERB + r"\s+(?:with\s+)?(?:any\s+|at least one\s+|an?\s+|the\s+|some\s+)?(?:of the\s+)?"
               r"(?P<g>" + _PHRASE + r")\s+" + _GROUP_TAIL + r"\b", re.I),
    re.compile(r"\b(?:the|overall|whole|total|entire)\s+(?P<g>" + _PHRASE + r")\s+categor(?:y|ies)\b", re.I),
    re.compile(r"\b(?:overall|total|combined)\s+(?:percent(?:age)?|share|reach|index)\s+(?:for|of|on|in)\s+"
               r"(?:the\s+)?(?P<g>" + _PHRASE + r")(?:\s+" + _GROUP_TAIL + r")?\s*[?.!]*$", re.I),
    re.compile(r"\b(?P<g>" + _PHRASE + r")\s+(?:brands\s+)?(?:as a (?:group|whole|category)|overall|combined|in total)\b", re.I),
    re.compile(r"\b(?:does|do|did|is|are)\s+(?:the\s+)?(?P<g>" + _PHRASE + r")\s+" + _GROUP_TAIL +
               r"\s+(?:over[- ]?|under[- ]?)?index", re.I),
)
_LEAD_NOISE_RX = re.compile(
    r"^(?:(?:what|which|how)\b.*?\b(?:of|do|does|did|for|is|are)\s+)?(?:the\s+|a\s+|an\s+|any\s+|overall\s+|whole\s+)*", re.I)
_STOP = frozenset(('the', 'a', 'an', 'and', 'of', 'for', 'brands', 'brand', 'category', 'categories',
                   'overall', 'any', 'all', 'this', 'that', 'these', 'those', 'their', 'our', 'its'))
_NOT_A_GROUP = frozenset(('this', 'that', 'these', 'those', 'the', 'other', 'top', 'most', 'same', 'which',
                          'what', 'more', 'many', 'few', 'several', 'some', 'different', 'specific',
                          'competitor', 'competing', 'rival', 'similar', 'new', 'big', 'major', 'leading'))
_AUDIENCE_NOUNS = ('viewers', 'listeners', 'fans', 'buyers', 'shoppers', 'customers', 'users',
                   'subscribers', 'players', 'readers', 'owners', 'members', 'consumers', 'renters',
                   'purchasers', 'visitors', 'streamers', 'watchers', 'engagers', 'followers')


# ----------------------------------------------------------------- words
def _toks(s):
    s = str(s or '').lower().replace('&', ' and ')
    return [t for t in re.split(r"[^a-z0-9]+", s) if t]


def label_key(label):
    """One key for one group name: 'Workwear & Tools', 'workwear and
    tools brands' and 'tools and workwear' are the same group."""
    return ' '.join(sorted(t for t in _toks(label) if t not in _STOP))


def clean_label(label):
    s = ' '.join(str(label or '').replace('&', ' and ').split()).strip(' .,:;!?"\'')
    s = re.sub(r"\s+(?:brands?|categor(?:y|ies))$", '', s, flags=re.I)
    return s.lower()


def audience_phrase(subject):
    """'Wicked Tuna Viewers' -> 'Wicked Tuna viewers'; 'Nike' -> 'the
    Nike audience'."""
    s = ' '.join(str(subject or '').split(' - ')[0].split())
    if not s:
        return 'this audience'
    words = s.split(' ')
    if words[-1].lower() in _AUDIENCE_NOUNS:
        return ' '.join(words[:-1] + [words[-1].lower()])
    return f"the {s} audience"


def audience_noun(subject):
    w = ' '.join(str(subject or '').split(' - ')[0].split()).split(' ')
    return w[-1].lower() if w and w[-1].lower() in _AUDIENCE_NOUNS else 'people'


def column_for(phrase, columns):
    """The profile column a plain category word names, or None."""
    cols = {cr.norm_cat(c) for c in (columns or ())}
    p = clean_label(phrase)
    if not p:
        return None
    up = cr.norm_cat(p)
    if up in cols:
        return up
    for c in cols:
        parts = [x.strip() for x in re.split(r"[/&]", c) if x.strip()]
        if up == c.replace('/', ' ') or (len(parts) > 1 and up in parts):
            return c
    for rx, targets in _ALIAS_RX:
        if rx.match(p):
            for t in targets:
                if t in cols:
                    return t
    return None


def group_phrase(text):
    """The category or group of brands an ask is about, as the person
    wrote it, or None when the ask is not a category-level one."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400 or not _MEASURE_RX.search(t):
        return None
    for rx in _GROUP_RXS:
        m = rx.search(t)
        if not m:
            continue
        g = m.group('g').strip(" ,'-")
        g = _LEAD_NOISE_RX.sub('', g).strip(" ,'-")
        g = re.sub(r"^(?:of|in|on|for|with)\s+", '', g, flags=re.I)
        toks = _toks(g)
        if not toks or len(toks) > 6 or toks[0] in _NOT_A_GROUP:
            continue
        if all(x in _STOP or x in _NOT_A_GROUP for x in toks):
            continue
        return g
    return None


def is_category_ask(text):
    return bool(group_phrase(text))


# ------------------------------------------------------------ the store
def _store_key(s3_key):
    h = hashlib.sha1(str(s3_key or '').encode('utf-8')).hexdigest()[:24]
    return f"{GROUP_PREFIX}{h}.json"


def _s3():
    try:
        from . import host
        if host.has('s3_client') and host.has('bucket'):
            return host.s3_client, host.bucket
    except Exception:
        pass
    return None, None


def load_groups(s3_key, s3=None, bucket=None):
    """{label_key: {'label', 'brands', 'by', 'at'}} kept for a profile."""
    if not s3_key:
        return {}
    if s3 is None:
        s3, bucket = _s3()
    if s3 is None:
        return {}
    try:
        obj = s3.get_object(Bucket=bucket, Key=_store_key(s3_key))
        doc = json.loads(obj['Body'].read().decode('utf-8'))
        g = doc.get('groups')
        return g if isinstance(g, dict) else {}
    except Exception:
        return {}


def save_group(s3_key, label, brands, by='chat', s3=None, bucket=None):
    """Keep a group's member list for the profile (first writer wins: a
    name keeps the members it was first given). Best effort."""
    if not s3_key or not brands:
        return False
    if s3 is None:
        s3, bucket = _s3()
    if s3 is None:
        return False
    try:
        groups = load_groups(s3_key, s3, bucket)
        k = label_key(label)
        if not k or k in groups:
            return False
        groups[k] = {'label': clean_label(label), 'brands': list(brands)[:MAX_MEMBERS],
                     'by': str(by or ''), 'at': int(time.time())}
        s3.put_object(Bucket=bucket, Key=_store_key(s3_key), ContentType='application/json',
                      Body=json.dumps({'s3_key': s3_key, 'groups': groups}).encode('utf-8'))
        return True
    except Exception:
        return False


# ------------------------------------------------- candidates and picker
# The columns a named group of brands is drawn from: things a person
# buys, shops, drives, eats at, banks with. Never a person, a team, a
# place, a title, an app or a media outlet (those are whole categories
# of their own and are read as such).
GROUP_COLUMNS = frozenset((
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR', 'ACCESSORIES',
    'PETS', 'TOYS', 'TECHNOLOGY BRAND', 'TECHNOLOGY/DEVICE', 'HEAVY MACHINERY', 'BEVERAGE', 'AUTOMOBILE',
    'AUTOMOTIVE PARTS', 'WHERE THEY SHOP', 'QSR', 'WHERE THEY DINE', 'TRAVEL', 'INSURANCE', 'BANKING',
    'DIGITAL BANKING', 'CREDIT PROVIDER', 'INVESTMENTS', 'TRADING', 'TELECOM', 'BETTING', 'PHARMACY',
    'WORKOUT FACILITY'))


def candidates(df, subject=None, min_pct=MIN_CANDIDATE_PCT, limit=MAX_CANDIDATES):
    """[(brand, pct, COLUMN)] the brands a named group can be drawn
    from, one row per brand, largest first."""
    best = {}
    for cu, rows in cr.profile_rows(df, subject).items():
        if cu not in GROUP_COLUMNS:
            continue
        rank = cr._column_rank(cu, ())
        for b, p in rows:
            if p < min_pct:
                break
            k = cr.norm_brand(b)
            cur = best.get(k)
            if cur is None or rank < cur[3]:
                best[k] = (b, p, cu, rank)
    out = sorted(best.values(), key=lambda r: (-r[1], r[0]))[:limit]
    return [(b, p, cu) for b, p, cu, _ in out]


_PICK_SYSTEM = (
    "You sort brands into named groups. You get numbered GROUPS, each with a name and optional seed brands that "
    "belong to it, and a BRAND LIST. For each group return every brand on the list whose main business is that "
    "group: the same kind of product or service as its name and seeds say. A store counts only when it is a "
    "specialist in the group (an outdoor outfitter for outdoor gear); leave out general retailers and "
    "marketplaces, and leave out any brand you are not sure of. A brand may sit in more than one group. Use only "
    "brands on the list, spelled exactly as listed. Return strict JSON: "
    "{\"groups\": [{\"n\": 1, \"brands\": [\"...\"]}]}. JSON only.")


def _default_reason():
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return None          # hermetic: a test passes its own picker
    try:
        from claude_client import claude_reason_json as _crj
    except Exception:
        return None
    return lambda sys_, usr: _crj(system=sys_, user=usr, max_tokens=1800, temperature=0.0,
                                  usage_tag=('analyze', 'category-group'))


def pick_groups(groups, cands, reason=None):
    """Member lists for several named groups in one pass.

    ``groups``: [(label, seeds)]. Returns one list per group, in order.
    A group's seeds that are on the profile always stay; the picker may
    add to them. With no picker the seeds stand alone."""
    by_key = {cr.norm_brand(b): b for b, _, _ in cands}
    out = []
    for _label, seeds in groups:
        kept, seen = [], set()
        for s in seeds or ():
            k = cr.norm_brand(s)
            if k in by_key and k not in seen:
                seen.add(k)
                kept.append(by_key[k])
        out.append((kept, seen))
    if reason is None:
        reason = _default_reason()
    if reason is not None and cands and groups:
        user = ("GROUPS:\n" + '\n'.join(
            f"{i + 1}. {clean_label(label)} (seed brands: {', '.join(str(s) for s in seeds) or 'none'})"
            for i, (label, seeds) in enumerate(groups)) +
            "\n\nBRAND LIST:\n" + '\n'.join(b for b, _, _ in cands))
        try:
            raw = str(reason(_PICK_SYSTEM, user) or '')
            m = re.search(r"\{.*\}", raw, re.S)
            doc = json.loads(m.group(0)) if m else {}
            for g in (doc.get('groups') or []):
                try:
                    i = int(g.get('n')) - 1
                except (TypeError, ValueError, AttributeError):
                    continue
                if not (0 <= i < len(out)):
                    continue
                kept, seen = out[i]
                for name in (g.get('brands') or [])[:80]:
                    k = cr.norm_brand(name)
                    if k in by_key and k not in seen:
                        seen.add(k)
                        kept.append(by_key[k])
        except Exception:
            pass
    return [kept[:MAX_MEMBERS] for kept, _ in out]


def pick_members(label, cands, seeds=(), reason=None):
    """The brands on the profile that belong to one named group."""
    return pick_groups([(label, list(seeds or ()))], cands, reason)[0]


# ------------------------------------------------------ one group figure
def group_figure(df, genpop_map, label, *, s3_key='', seeds=(), subject=None,
                 reason=None, by='chat', expand=True, picked=None, s3=None, bucket=None):
    """The reach of a named group of brands on one profile: the rollup
    dict with 'members', or None. A name already settled for this
    profile keeps its member list; otherwise the seeds (and, when
    ``expand``, the brands the picker adds; ``picked`` hands in a list
    already picked) become the list and are kept for the next ask."""
    name = clean_label(label)
    stored = load_groups(s3_key, s3, bucket).get(label_key(name)) if s3_key else None
    if stored and stored.get('brands'):
        members = list(stored['brands'])
    else:
        members = list(picked or [])
        if not members and expand:
            members = pick_members(name, candidates(df, subject), seeds, reason)
        pairs, _ = cr.resolve_brands(df, seeds, genpop_map, subject)
        have = {cr.norm_brand(m) for m in members}
        members += [b for b, _, _, _ in pairs if cr.norm_brand(b) not in have]
    if len(members) < 2:
        return None
    r = cr.cluster_rollup(df, genpop_map, members, name=name, subject=subject)
    if not r or len(r.get('members') or []) < 2:
        return None
    if s3_key and not stored:
        save_group(s3_key, name, r['members'], by=by, s3=s3, bucket=bucket)
    return r


# ----------------------------------------------------------------- chat
_BLOCK_HEAD = (
    "CATEGORY REACH (measured on this profile; quote exactly)\n"
    "The share of this audience that engages with at least one brand in the group, the same share for the US, "
    "and the index (100 = US average). A percent or an index for a whole category or a group of brands is THIS "
    "figure: never add brand rows together, never average them, never use the largest brand. State it flat: the "
    "percent with one decimal, the index as a whole number with the plain comparison beside it (\"index 212, "
    "2.1x the US average\"). Never describe how the figure is produced.")


def _line(r, label, subject, members=None):
    aud = audience_phrase(subject)
    s = f"  {label}: {r['reach']:.1f}% of {aud} engage with at least one {label} brand"
    if r.get('genpop') is not None:
        s += f"; US {r['genpop']:.1f}%"
    if r.get('index') is not None:
        s += f"; index {r['index']} ({r['times']})"
    s += f". Largest single brand: {r['top']} {r['top_pct']:.1f}%."
    if members:
        s += " Brands counted: " + ', '.join(members[:MAX_MEMBERS]) + '.'
    else:
        s += f" {r['brands']} brands counted."
    return s


def block(df, genpop_map, text, *, s3_key='', subject=None, reason=None):
    """The bound figure for a category-level ask, as a prompt block, or
    ''. A whole behavioral category is read straight off the profile; a
    named group of brands is settled (and kept) first."""
    try:
        if df is None:
            return ''
        g = group_phrase(text)
        if not g:
            return ''
        s3_key = s3_key or str(getattr(df, 'attrs', {}).get('s3_key') or '')
        subject = subject or _subject_of(df)
        rows = cr.profile_rows(df, subject)
        col = column_for(g, rows.keys())
        if col:
            r = cr.category_rollups(_only(df, col), genpop_map, subject).get(col)
            if not r:
                return ''
            return _BLOCK_HEAD + '\n' + _line(r, col.lower(), subject)
        # one brand by name is a brand row, not a group
        if cr.resolve_brands(df, [g], genpop_map, subject)[0]:
            return ''
        r = group_figure(df, genpop_map, g, s3_key=s3_key, subject=subject, reason=reason, by='chat')
        if not r:
            return ''
        return _BLOCK_HEAD + '\n' + _line(r, r['name'], subject, r.get('members'))
    except Exception:
        traceback.print_exc()
        return ''


def _only(df, col):
    try:
        cols = df['Column'].astype(str).map(cr.norm_cat)
        return df[(cols == col) | (cols == 'SUBJECT')]
    except Exception:
        return df


def _subject_of(df):
    try:
        cols = df['Column'].astype(str).str.strip().str.upper()
        v = df.loc[cols == 'SUBJECT', 'Value'].astype(str)
        return str(v.iloc[0]) if len(v) else ''
    except Exception:
        return ''


# --------------------------------------------------------------- digest
def digest_section(df, genpop_map, subject=None, min_brands=3, max_cats=70):
    """The lines every profile digest carries: each behavioral category's
    reach, the US reach and the index. '' when there is nothing."""
    try:
        roll = cr.category_rollups(df, genpop_map, subject, min_brands=min_brands)
        rows = [(c, r) for c, r in roll.items() if r.get('index') is not None]
        if not rows:
            return ''
        rows.sort(key=lambda cr_: (-cr_[1]['brands'], cr_[0]))
        rows = sorted(rows[:max_cats], key=lambda cr_: cr_[0])
        head = ("CATEGORY REACH (share of this audience with at least one brand in the category | the same share "
                "for the US | index, 100 = US average). A percent or index for a WHOLE category is this figure, "
                "quoted exactly; never add or average brand rows, never use the largest brand:")
        body = '; '.join(f"{c} {r['reach']:.1f} | {r['genpop']:.1f} | {r['index']}" for c, r in rows)
        return head + '\n  ' + body
    except Exception:
        traceback.print_exc()
        return ''


# ----------------------------------------------------------------- deck
def _body_brands(text, index):
    """Brands a card's copy names, in the order named."""
    found, low = [], ' ' + re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()) + ' '
    for key, (name, toks) in index.items():
        needle = ' ' + ' '.join(toks) + ' '
        pos = low.find(needle)
        if pos >= 0:
            found.append((pos, -len(needle), name))
    found.sort()
    out, covered = [], []
    for pos, neg, name in found:
        end = pos - neg
        if any(a <= pos and end <= b for a, b in covered):
            continue
        covered.append((pos, end))
        out.append(name)
    return out


def _name_index(df, subject):
    idx = {}
    for b, p, _ in candidates(df, subject, min_pct=0.5, limit=4000):
        toks = cr._tokens(b)
        if not toks or (len(toks) == 1 and len(toks[0]) < 4):
            continue
        idx[cr.norm_brand(b)] = (b, toks)
        # the short form a deck writes ('Milwaukee' for 'Milwaukee Tools')
        if len(toks) > 1 and toks[-1] in ('tools', 'sunglasses', 'shoes', 'boots', 'optics', 'brand', 'co', 'inc'):
            short = toks[:-1]
            k = ''.join(short)
            if len(k) >= 5 and k not in idx:
                idx[k] = (b, short)
    return idx


def read_line(subject='', n_cards=4):
    """The line under a group-of-brands slide: what the figures mean, in
    plain words."""
    return ("Each percent is the share of this audience that engages with at least one brand in the group. "
            "An index of 100 is the US average.")


def annotate_plan(plan, df, genpop_map, *, s3_key='', subject='', reason=None, expand=False):
    """Put the reach and the index on every card that is a group of
    brands. A slide qualifies when every one of its cards names at least
    two brands the profile carries (the plan's "brands" list, else the
    brands its copy names). The card gains 'reach' ('41.2%'), 'index'
    (237) and 'times' ('2.4x the US average'); the slide gains the line
    that says what the figures mean. Slides that do not qualify are left
    exactly as they were. Returns the plan."""
    try:
        if not isinstance(plan, dict) or df is None:
            return plan
        index = None
        for sl in plan.get('slides') or []:
            if not isinstance(sl, dict) or str(sl.get('type') or '') != 'argument':
                continue
            cards = [c for c in (sl.get('cards') or []) if isinstance(c, dict)][:4]
            if len(cards) < 2:
                continue
            seeds_per = []
            for c in cards:
                given = [str(b) for b in (c.get('brands') or []) if str(b or '').strip()] \
                    if isinstance(c.get('brands'), (list, tuple)) else []
                if index is None:
                    index = _name_index(df, subject)
                named = _body_brands(f"{c.get('head') or ''}. {c.get('body') or ''}", index)
                seeds = list(dict.fromkeys(given + named))
                pairs, _ = cr.resolve_brands(df, seeds, genpop_map, subject)
                seeds_per.append([b for b, _, _, _ in pairs])
            if not all(len(s) >= 2 for s in seeds_per):
                for c in cards:
                    c.pop('brands', None)
                continue
            picked = [None] * len(cards)
            if expand:
                stored = load_groups(s3_key) if s3_key else {}
                todo = [i for i, c in enumerate(cards)
                        if label_key(clean_label(c.get('head'))) not in stored]
                if todo:
                    got = pick_groups([(str(cards[i].get('head') or ''), seeds_per[i]) for i in todo],
                                      candidates(df, subject), reason)
                    for i, members in zip(todo, got):
                        picked[i] = members
            figs = []
            for c, seeds, pk in zip(cards, seeds_per, picked):
                figs.append(group_figure(df, genpop_map, str(c.get('head') or ''), s3_key=s3_key, seeds=seeds,
                                         subject=subject, reason=reason, by='deck', expand=False, picked=pk))
            if not all(f and f.get('index') is not None for f in figs):
                for c in cards:
                    c.pop('brands', None)
                continue
            for c, f in zip(cards, figs):
                c['reach'] = f"{f['reach']:.1f}%"
                c['index'] = int(f['index'])
                c['times'] = f['times']
                c['brands'] = list(f.get('members') or [])
            sl['read'] = read_line(subject, len(cards))
            sl['_reach'] = True
        return plan
    except Exception:
        traceback.print_exc()
        return plan


def annotate_deck(plan, ctx, subject=''):
    """The deck job's one call: load the open profile and annotate."""
    try:
        key = str(((ctx or {}).get('primary') or {}).get('s3_key') or '')
        if not key.lower().endswith('.csv'):
            return plan
        import prometheus_analysis as pma
        s3, bucket = _s3()
        if s3 is None:
            return plan
        df, _ = pma.load_profile_df(s3, bucket, key)
        gp = pma.load_genpop_map(s3, bucket)
        name = str(((ctx or {}).get('primary') or {}).get('name') or subject or '')
        # The plan's own brand lists are the groups: the card names them,
        # so the figure is for brands the reader can see, and the deck
        # job makes no extra call. A name already settled on this profile
        # (an earlier deck or a chat answer) keeps its list.
        return annotate_plan(plan, df, gp, s3_key=key, subject=name or subject, expand=False)
    except Exception:
        traceback.print_exc()
        return plan
