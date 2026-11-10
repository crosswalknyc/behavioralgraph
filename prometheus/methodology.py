"""Methodology line under every viewership figure (2026-10-08, Liz's
notes on the Paramount+ browse-only read, entered by Jenna).

Liz, verbatim (output rules): "always print window dates, panel base,
unit (persons vs accounts), view threshold, carriage paths included,
and session definition in a methodology line under any viewership
figure, without being asked."

The house definitions live here once. The block is deterministic and
appended at the reply formatter, so a viewership read never ships
without it, whatever the model wrote. Vocabulary follows the standing
rules: no vendor names (the citation scrub would drop the sentence),
"panel" never appears (the scrub rewrites it), counts are persons.

The block fits the read's family (2026-11-06): the family the lane
stamps on the read (metric_family, the ledger family) picks the lines.
Viewing reads keep the view, threshold, carriage and session lines;
purchase reads carry the order definition; engagement reads the
engagement definition; sizing reads the people, window and base lines.
`pool_noun` is the one helper for the noun on a pool count (viewers,
people, buyers, shoppers, listeners, readers, players, subscribers).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from prometheus import subject_display as _display

HEADER = 'How these are counted:'

# What a viewership figure looks like in a reply or an ask.
VIEWERSHIP_RX = re.compile(
    r"\b(?:viewers?|viewed|viewing|viewership|watch(?:ed|es|ing)?|"
    r"watch time|hours watched|minutes watched|streams?|streamed|streaming|"
    r"sessions?|browse[- ]only|browsed|playback|title starts?|started a title|"
    r"plays|played|listeners?|listened|listenership|binge[ds]?|tuned in|"
    r"opened the (?:app|service)|dwell)\b", re.I)
# Figures that depend on a title-start threshold: both thresholds show.
THRESHOLD_RX = re.compile(
    r"\b(?:browse[- ]only|bounce[ds]?|without starting|did not start|"
    r"no playback|never (?:started|pressed play)|title starts?|started a "
    r"title|first play|starts?)\b", re.I)

UNIT = ("Counts are US viewers (persons), any screen, credited at one "
        "second of viewing; they are not comparable to a service's "
        "reported subscriber count, to TV ratings, or to platform view "
        "counts that tally plays instead of people.")
THRESHOLD = ("A title start counts at one second of playback; autoplayed "
             "previews and trailers on the home screen do not count as "
             "starts. Where a figure depends on that threshold, the 30 "
             "seconds or more view is shown beside it.")
SESSION = ("A session closes after 30 minutes without activity; a "
           "backgrounded app is not an open.")
CARRIAGE = ("Carriage paths: direct app and site sessions plus channel "
            "storefronts (Prime Video Channels, Apple TV Channels, Roku) "
            "where the title is attributable.")
BASE_SAMPLE = "measured across 10M US consumers and projected to the US"

# What we count as a view, for every generated artifact that carries one
# (Jenna 2026-10-08: "anytime something is generated that includes views
# we give what we measure as a view from our methodology somewhere
# attached so they know it's not apples to apples to other metrics").
VIEW_HEADER = 'What we count as a view:'
VIEW_DEFINITION = (
    "A view is one person, on any screen, who played a title for at least one "
    "second inside the window, counted once however many devices or sessions "
    "they used. Autoplayed previews and trailers are not views. Counts are US "
    "viewers (persons), not accounts, so they are not comparable "
    "to a service's reported subscriber count, to TV ratings, or to platform "
    "view counts that tally plays instead of people.")
VIEW_NOTE_SHORT = ("Views here are US viewers (persons) credited at one second of "
                   "playback, counted once per person; not comparable to subscriber "
                   "counts, TV ratings, or play counts.")


# What we count as a purchase (Jenna 2026-11-04: "most purchased brands IS
# confirmed by an order confirmation so it is brands they purchased").
# Most Purchased Brands and the rows that mirror it are confirmed orders,
# never a touchpoint, a browse, a consideration or "reach". The line rides
# the counting block of any reply that carries a purchase-family figure.
PURCHASE_HEADER = 'What we count as a purchase:'
PURCHASE_DEFINITION = (
    "A purchased brand is confirmed by an order confirmation. Most Purchased Brands, and "
    "the category rows that mirror it, are the brands the audience actually purchased online "
    "in the window: the share of the audience with at least one confirmed order that included "
    "the brand, counted once per person. Browsing, consideration and visits to the brand are "
    "not purchases and are never counted here.")
PURCHASE_RX = re.compile(
    r"\b(?:most purchased brands?|purchased brands?|purchase family|confirmed orders?|order confirmations?|"
    r"where (?:this|the|that|these|they) (?:shared |whole |same )?(?:audience|people|viewers|fans|group|cohort)? ?(?:buy|buys|bought|shop|shops|shopped)\b|"
    r"brands (?:they|the audience|this audience) (?:bought|buy|buys|purchased?)|"
    r"(?:bought|purchased) (?:from|at|on) [A-Z][\w&'+.-]*)", re.I)


def mentions_views(text):
    return bool(VIEWERSHIP_RX.search(str(text or '')))


def mentions_purchase(text):
    return bool(PURCHASE_RX.search(str(text or '')))


def carries_purchase_definition(text):
    t = str(text or '')
    return PURCHASE_HEADER in t or 'confirmed by an order confirmation' in t


def carries_purchase_figure(text):
    """A purchase-family mention and a count or a share in the same
    sentence ("Where this shared audience buys: Amazon 84.1189% ...")."""
    t = str(text or '')
    if not t:
        return False
    for sent in re.split(r"(?<=[.;!?])\s+|\n+", t):
        if PURCHASE_RX.search(sent) and (_FIGURE_RX.search(sent) or re.search(r"\d+(?:\.\d+)?\s?%", sent)):
            return True
    return False


def ensure_purchase_note(reply):
    """Append the purchase definition once to a reply that carries a
    purchase-family figure: as a bullet under an existing counting block,
    else as its own line. Idempotent; never raises."""
    try:
        text = str(reply or '')
        if not text.strip() or carries_purchase_definition(text) or not carries_purchase_figure(text):
            return reply
        body = text.rstrip()
        if HEADER in body:
            return body + '\n' + f"- {PURCHASE_HEADER} {PURCHASE_DEFINITION}"
        return body + '\n\n' + f"{PURCHASE_HEADER} {PURCHASE_DEFINITION}"
    except Exception:
        return reply


def carries_definition(text):
    t = str(text or '')
    return VIEW_HEADER in t or 'Counts are US viewers (persons)' in t or VIEW_NOTE_SHORT in t


def attach_view_definition(text, short=False):
    """Append the view definition to a text artifact that mentions views
    and does not already carry it. Idempotent."""
    t = str(text or '')
    if not t.strip() or not mentions_views(t) or carries_definition(t):
        return text
    body = t.rstrip()
    if short:
        return body + '\n\n' + VIEW_NOTE_SHORT
    return body + '\n\n' + VIEW_HEADER + ' ' + VIEW_DEFINITION


MODEL_RULES = (
    'VIEWERSHIP FIGURES (house rules, always):\n'
    '- Count US viewers (persons), never accounts or households. Say '
    '"viewers" or "people".\n'
    '- Every viewership figure carries its window dates.\n'
    '- When a figure depends on a title-start threshold (browse-only, '
    'bounce, started a title, first play), report BOTH the one-second '
    'figure and the 30-seconds-or-more figure, side by side.\n'
    '- Name the carriage paths the figure includes when they differ '
    '(direct app and site; channel storefronts such as Prime Video '
    'Channels). Never claim a path the measurement cannot see.\n'
    '- The server appends the counting block for the read\'s family '
    '(window, base, and the view, order, engagement or people definition; '
    'threshold, carriage and session rules on a viewing read); do not '
    'restate it, and never name a ratings vendor.\n'
    '- Most Purchased Brands and the rows that mirror it (CPG, Apparel/'
    'Footwear, Beauty/Wellness, Home/Outdoor, Accessories, Pets, Toys, '
    'Technology Brand, Where They Shop) are confirmed purchases: an order '
    'confirmation that included the brand. Say "bought" or "purchased"; '
    'never describe these rows as browsing, consideration, shopping '
    'interest, reach or a touchpoint.\n')


def _fmt_date(v):
    if v is None or v == '':
        return ''
    if isinstance(v, (date, datetime)):
        return v.strftime('%B %-d, %Y')
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%B %-d, %Y')
        except ValueError:
            continue
    return s


# A viewership FIGURE: a count of at least a thousand (or a million-form
# figure) within a few words of a viewing word. The word alone is not
# one (Jay Patel 2026-10-30: a build quote that said "4 or more views"
# and "the streaming surge" drew the whole counting block).
# a figure: a comma-grouped count, four or more digits, or a number with a
# unit word ("2.4 billion hours" is a viewership figure too)
_FIGURE_RX = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d{4,}|\d+(?:\.\d+)?\s*(?:billion|million|bn\b|m\b|k\b))", re.I)


def carries_view_figure(text):
    """A count and a viewing word in the same sentence."""
    t = str(text or '')
    if not t:
        return False
    for sent in re.split(r"(?<=[.;!?])\s+|\n+", t):
        if VIEWERSHIP_RX.search(sent) and _FIGURE_RX.search(sent):
            return True
    return False


def is_viewership_read(text='', res=None, question=''):
    """True when the reply or the ask carries a viewership figure: a
    metric family that is viewing, a metric labeled as one, or a count
    standing next to a viewing word in the reply or the question."""
    res = res if isinstance(res, dict) else {}
    fam = str(res.get('metric_family') or '').lower()
    if fam in ('viewership', 'consumption', 'journey', 'streaming'):
        return True
    labels = ' '.join(str(m.get('label') or '') + ' ' + str(m.get('name') or '')
                      for m in (res.get('metrics') or []) if isinstance(m, dict))
    if labels and VIEWERSHIP_RX.search(labels):
        return True
    return carries_view_figure(text) or carries_view_figure(question)


# ---------------------------------------------------------------------------
# The counting family (2026-11-06). The block under a read describes what
# the read counted, and the family the lane stamps on the read decides
# which lines it carries. A holiday toy shopping read (admin, thread
# 2b1e503248, job 4706e7014b72) shipped with the view, threshold,
# carriage and session lines; the only viewing word in it was the pool
# noun. Viewing and streaming reads keep the view, threshold, carriage
# and session lines; shopping and purchase reads carry the order
# definition; search and social engagement reads carry the engagement
# definition; sizing and audience reads carry the people, window and
# base lines only. The words decide only when nothing is stamped, and
# then the viewing set needs a viewing figure in the read or the ask.
# ---------------------------------------------------------------------------
VIEWING, PURCHASE, ENGAGEMENT, SIZING = 'viewing', 'purchase', 'engagement', 'sizing'
FAMILIES = (VIEWING, PURCHASE, ENGAGEMENT, SIZING)

# the lane's metric_family vocabulary and the ledger's canonical families,
# plus the other stamps a read can carry; 'strategy' stays unstamped so the
# words decide, as before
_FAMILY_MAP = (
    (VIEWING, ('viewing', 'viewership', 'consumption', 'journey', 'streaming',
               'view', 'watch', 'stream', 'play', 'listen', 'tune', 'binge')),
    (PURCHASE, ('purchase', 'purchases', 'shopping', 'shop', 'buy', 'bought',
                'order', 'sale', 'ecommerce', 'e-commerce', 'transaction',
                'rent', 'revenue', 'spend', 'checkout')),
    (ENGAGEMENT, ('engagement', 'engag', 'search', 'social', 'session',
                  'visit', 'follow', 'quer', 'demand', 'app activity')),
    (SIZING, ('sizing', 'size', 'audience', 'reach', 'fans', 'cohort',
              'profile', 'subscri', 'signup', 'sign up', 'sign-up', 'member',
              'churn', 'dimension split', 'brand_partnership', 'delivered',
              'twin')),
)

ORDER_HEADER = 'What we count as an order:'
ORDER_DEFINITION = (
    "An order is one person with at least one checkout, confirmed by an order confirmation, "
    "that named the brand or category inside the window, counted once however many orders, "
    "devices or sessions they placed. Browsing, carting or visiting a store or a brand site "
    "without a checkout is not an order. Marketplaces and brand sites both count where the "
    "brand is attributable. Counts are US buyers (persons), not accounts or transactions, so "
    "they are not comparable to a retailer's order totals or unit sales.")
ENGAGEMENT_HEADER = 'What we count as engagement:'
ENGAGEMENT_DEFINITION = (
    "An engaged person is one person with at least one search or social touch on the subject "
    "inside the window: a search naming it, a visit to its own site or app, or a social view, "
    "follow, like, comment, share or click on it, counted once however many touches, devices "
    "or sessions they used. Counts are US people (persons), not accounts, impressions or "
    "sessions, so they are not comparable to follower counts, impression totals or search "
    "volume.")
PEOPLE = ("Counts are US people (persons), each counted once inside the window however many "
          "devices or sessions they used; they are not accounts, households or impressions.")
ORDER_NOTE_SHORT = ("Orders here are US buyers (persons) with at least one checkout naming the brand or "
                    "category in the window, counted once per person; browsing without a checkout is not "
                    "an order.")
ENGAGEMENT_NOTE_SHORT = ("Engagement here is US people (persons) with at least one search or social touch on "
                         "the subject in the window, counted once per person; not impressions, sessions or "
                         "follower counts.")

_SESSIONS_RX = re.compile(r"\bsessions?\b", re.I)


def canon_counting_family(family):
    """One of FAMILIES for a lane or ledger family label, '' when the
    label is empty or names no counting family ('strategy')."""
    f = str(family or '').strip().lower()
    if not f:
        return ''
    if f in FAMILIES:
        return f
    for fam, keys in _FAMILY_MAP:
        if f in keys:
            return fam
    for fam, keys in _FAMILY_MAP:
        if any(k in f for k in keys):
            return fam
    return ''


def stamped_family(res):
    """The counting family the lane stamped on the read (metric_family,
    the ledger family), '' when none is stamped."""
    res = res if isinstance(res, dict) else {}
    for key in ('counting_family', 'metric_family', 'family', '_family'):
        fam = canon_counting_family(res.get(key))
        if fam:
            return fam
    return ''


def read_family(res=None, text='', question=''):
    """The counting family of a read: what the lane stamped, else the
    words. Unstamped, the viewing set needs a viewing figure in the
    reply or the ask (a title, a platform, a count beside a viewing
    word); a purchase figure reads purchase; everything else sizes
    people."""
    fam = stamped_family(res)
    if fam:
        return fam
    if is_viewership_read(text, res, question):
        return VIEWING
    if carries_purchase_figure(text) or mentions_purchase(question):
        return PURCHASE
    return SIZING


def counts_sessions(text=''):
    """True when the read counts sessions (the Sessions line rides)."""
    return bool(_SESSIONS_RX.search(str(text or '')))


# The noun for a pool count (2026-11-06): viewers for viewing, people for
# sizing and audiences, buyers or shoppers for purchase reads (buyers when
# the pool is people who ordered, shoppers when it is people who browsed),
# listeners for audio, readers for text, players for games, subscribers
# where the read is subscriptions. One helper for the reply formatter, the
# ledger block, the chart and the CSV header.
# the medium words are the ones that name the act or the surface, never
# a bare 'book', 'song' or 'game' (a film adapted from a book is watched,
# 'the game' on a sports read is watched, a music study can count film
# viewers); a text or print word only moves a sizing or engagement pool
_AUDIO_RX = re.compile(
    r"\b(?:podcasts?|podcasters?|listen(?:ed|ers?|ing|s)?|listenership|audio|radio|"
    r"spotify|apple music|amazon music|pandora|sirius ?xm|iheart(?:radio)?|audible|"
    r"audiobooks?|streaming music)\b", re.I)
_TEXT_RX = re.compile(
    r"\b(?:readers?|readership|e-?books?|kindle|booktok|webtoons?|manga|graphic novels?)\b", re.I)
_TEXT_SOFT_RX = re.compile(
    r"\b(?:books?|novels?|articles?|newsletters?|substack|blogs?|comics?|magazines?)\b", re.I)
_GAMES_RX = re.compile(
    r"\b(?:video ?games?|mobile games?|gamers?|players?|gameplay|playthroughs?|xbox|playstation|"
    r"nintendo|roblox|fortnite|minecraft|esports|consoles?)\b", re.I)
# a pool is sometimes a quantity, not a crowd: a table of viewing hours
# by month says "Pool 5,886,000,022 hours", never "viewers" (Cara Dorfman
# 2026-11-10, hours by month for Prime Video and YouTube)
_UNIT_NOUNS = ('viewers', 'listeners', 'readers', 'players', 'subscribers', 'buyers', 'shoppers', 'hours', 'minutes')
_TIME_POOL_RX = re.compile(r"\b(?:total\s+)?(?:hours|minutes)\b|\bwatch\s*time\b|\btime\s+spent\b|\bviewing\s+time\b", re.I)
_SUBSCRIPTION_RX = re.compile(
    r"\b(?:subscribers?|subscriptions?|subs|sign-?ups?|signed up|churn(?:ed|ers?)?|cancel(?:led|lations?|s)?)\b", re.I)
_BROWSED_RX = re.compile(
    r"\b(?:brows(?:e|ed|es|ing|ers?)|shopped around|window[- ]?shopp\w*|consider(?:ed|ing|ation)|visit(?:ed|ors?|s)?|"
    r"carted|cart abandon\w*|without (?:a )?(?:checkout|purchase|buying|an order))\b", re.I)
_ORDERED_RX = re.compile(
    r"\b(?:order(?:ed|s)?|bought|purchas(?:e|ed|es|ers?|ing)|buyers?|checkouts?|checked out|paid|"
    r"transactions?|spent|spend)\b", re.I)
_PURCHASE_WORDS_RX = re.compile(r"\b(?:bought|buy|buys|buyers?|purchas\w*|shop(?:s|ped|pers?|ping)?|orders?|ordered|checkout)\b", re.I)
# 'without a checkout' names no order; the phrase comes off before the
# ordered words are looked for
_WITHOUT_RX = re.compile(r"\b(?:without|no|never|not)\s+(?:a |an |any )?(?:checkout|purchas\w*|order\w*|buying|bought|paid|transaction\w*)", re.I)


def _ordered_or_browsed(ctx):
    """'buyers' when the words say the pool ordered, 'shoppers' when they
    say it only browsed, 'buyers' when they say neither (a purchase
    family counts confirmed orders)."""
    if _ORDERED_RX.search(_WITHOUT_RX.sub(' ', ctx)):
        return 'buyers'
    return 'shoppers' if _BROWSED_RX.search(ctx) else 'buyers'


def pool_noun(family=None, context=''):
    """The noun for a pool count in the read's family. `family` is the
    lane or ledger family label (or one of FAMILIES); `context` is the
    words around the pool (question, dimension, share basis, labels,
    subject) that pick the medium and, for a purchase read, whether the
    pool ordered or browsed. Never raises."""
    try:
        raw = str(family or '').strip().lower()
        ctx = str(context or '')
        fam = canon_counting_family(raw)
        if raw and ('subscri' in raw or 'signup' in raw or 'sign up' in raw or 'sign-up' in raw or 'churn' in raw):
            return 'subscribers'
        if fam == PURCHASE:
            return _ordered_or_browsed(ctx)
        if _AUDIO_RX.search(ctx):
            return 'listeners'
        if _TEXT_RX.search(ctx) or (fam != VIEWING and _TEXT_SOFT_RX.search(ctx)):
            return 'readers'
        if _GAMES_RX.search(ctx):
            return 'players'
        if fam == VIEWING:
            return 'viewers'
        if fam in (SIZING, ENGAGEMENT):
            if fam == SIZING and _SUBSCRIPTION_RX.search(ctx) and not VIEWERSHIP_RX.search(ctx):
                return 'subscribers'
            return 'people'
        # nothing stamped: the words
        if _PURCHASE_WORDS_RX.search(ctx):
            return _ordered_or_browsed(ctx)
        if VIEWERSHIP_RX.search(ctx):
            return 'viewers'
        return 'people'
    except Exception:
        return 'people'


def pool_context(res):
    """The words around a read's pools, for pool_noun: question, subject,
    cohort, universe, headline, dimension, share basis, metric labels
    and units."""
    res = res if isinstance(res, dict) else {}
    bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
    bits = [res.get('question'), res.get('_question'), res.get('subject'), res.get('cohort'),
            res.get('universe'), res.get('headline'), bd.get('dimension'), bd.get('share_basis')]
    for m in (res.get('metrics') or [])[:12]:
        if isinstance(m, dict):
            bits.extend([m.get('label'), m.get('unit')])
    return ' '.join(str(b) for b in bits if b)


def pool_noun_for(res, question=''):
    """pool_noun for a read dict (the formatter, the CSV, the chart). A
    count metric whose unit already names the people (viewers,
    listeners, readers, players, subscribers) is the lane's own
    statement of what it counted and wins over the words; a purchase
    read keeps buyers / shoppers."""
    res = res if isinstance(res, dict) else {}
    fam = ''
    for key in ('counting_family', 'metric_family', 'family', '_family'):
        if res.get(key):
            fam = str(res.get(key))
            break
    if canon_counting_family(fam) != PURCHASE:
        for m in (res.get('metrics') or [])[:12]:
            unit = str((m or {}).get('unit') or '').strip().lower() if isinstance(m, dict) else ''
            if unit in _UNIT_NOUNS:
                return unit
        # the breakdown's own basis says the pool is time ("share of 2025
        # adult viewing hours across both platforms")
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        basis = ' '.join(str(bd.get(k) or '') for k in ('share_basis', 'dimension'))
        if _TIME_POOL_RX.search(basis):
            return 'minutes' if re.search(r"\bminutes\b", basis, re.I) and not re.search(r"\bhours\b", basis, re.I) else 'hours'
    return pool_noun(fam, ' '.join([str(question or ''), pool_context(res)]))


def block(window_start=None, window_end=None, window_label='',
          base_label='', base_people=None, text='', family=None, noun=''):
    """The counting block as lines (header first). `family` is one of
    FAMILIES (or a lane family label); callers that name none get the
    viewing block, as before. `noun` is the Base line's people noun
    (pool_noun); unset, viewing reads say viewers and the other
    families take the noun their words pick."""
    fam = canon_counting_family(family) or VIEWING
    noun = str(noun or '').strip() or ('viewers' if fam == VIEWING else pool_noun(fam, text))
    win = str(window_label or '').strip()
    if not win and window_start and window_end:
        win = f"{_fmt_date(window_start)} to {_fmt_date(window_end)}"
    # The window prints whole or not at all (2026-11-05): a label that
    # reads cut (an open parenthesis, a three-digit year, a trailing
    # 'to') is rebuilt from the read's dates or trimmed to its last
    # whole token before it reaches the reader.
    try:
        from . import window_label as _wl
        if win and _wl.cut_mid_token(win):
            win = _wl.repair_window_label(win, window_start, window_end) or win
    except Exception:
        pass
    lines = [HEADER]
    if win:
        lines.append(f"- Window: {win}.")
    # A reader sees a name, never a key (2026-10-22: "Base: the
    # BEST_OF_THE_BEST audience" reached Casey Pearson's thread from a
    # file stem).
    base = _display.display(str(base_label or '').strip())
    people = None
    try:
        people = int(float(base_people)) if base_people not in (None, '') else None
    except (TypeError, ValueError):
        people = None
    if base:
        who = base if _NAMES_PEOPLE_RX.search(base) else f"the {base} audience"
        if people:
            lines.append(f"- Base: {who} ({people:,} US {noun}), "
                         f"{BASE_SAMPLE}.")
        else:
            lines.append(f"- Base: {who}, {BASE_SAMPLE}.")
    else:
        lines.append(f"- Base: {BASE_SAMPLE[0].upper() + BASE_SAMPLE[1:]}.")
    # the family's own definition; the shared Sessions line rides where
    # sessions are counted (always on a viewing read)
    if fam == VIEWING:
        lines.append(f"- {VIEW_HEADER} {VIEW_DEFINITION}")
        if THRESHOLD_RX.search(str(text or '')):
            lines.append(f"- Threshold: {THRESHOLD}")
        lines.append(f"- {CARRIAGE}")
        lines.append(f"- Sessions: {SESSION}")
        return lines
    if fam == PURCHASE:
        lines.append(f"- {ORDER_HEADER} {ORDER_DEFINITION}")
    elif fam == ENGAGEMENT:
        lines.append(f"- {ENGAGEMENT_HEADER} {ENGAGEMENT_DEFINITION}")
    else:
        lines.append(f"- People: {PEOPLE}")
    if counts_sessions(text):
        lines.append(f"- Sessions: {SESSION}")
    return lines


def carries_family_definition(text, family=None):
    """True when the text already carries the family's own definition
    line (the view definition for viewing or an unnamed family)."""
    t = str(text or '')
    fam = canon_counting_family(family) or VIEWING
    if fam == PURCHASE:
        return ORDER_HEADER in t
    if fam == ENGAGEMENT:
        return ENGAGEMENT_HEADER in t
    if fam == SIZING:
        return f"- People: {PEOPLE}" in t
    return carries_definition(t)


_ANY_FIGURE_RX = re.compile(r"\d+(?:\.\d+)?\s?%|\d{1,3}(?:,\d{3})+|\d{4,}")


# Period totals next to their quarters (2026-10-09, item 6; Bria's
# Starz ticket: Tobias Menzies at 18.42% for the year with every quarter
# under 5% was a correct number that looked wrong). One plain line
# wherever a full-window figure sits next to sub-period figures.
PERIOD_HEADER = 'Why the full-window number sits above each period:'
PERIOD_NOTE = ("A person is counted once for the whole window but only in the periods they "
               "were active, so each quarter or month can only equal or fall below the "
               "full-window share, and the periods do not add up to it. A brand whose "
               "audience spreads across the year reads higher for the year than for any one "
               "quarter.")
PERIOD_RX = re.compile(r"\b(?:q[1-4]\b|quarter(?:s|ly)?|month(?:s|ly)?\b|month by month|by quarter|"
                       r"per quarter|each quarter|h[12]\s*20\d\d|ytd|year to date|"
                       r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+20\d\d)", re.I)
WHOLE_RX = re.compile(r"\b(?:total universe|full[- ]window|full year|whole year|the year\b|annual|"
                      r"trailing 12|12 months|twelve months|year total|for 20\d\d\b)", re.I)
# "overall" reads as the whole window only beside a run of periods; in
# prose ("leans into streaming overall") it is an ordinary word (Cara
# Dorfman, Amazon, 2026-11-10: an October 2026 overlap read, one month
# and no quarters, carried the period note on the strength of that word)
_OVERALL_RX = re.compile(r"\boverall\b", re.I)
_PERIOD_RUN_RX = re.compile(r"\b(?:q[1-4]\b|quarter(?:s|ly)?|month(?:s|ly)\b|month by month|by month|per month|"
                            r"each month|by quarter|per quarter|each quarter|h[12]\s*20\d\d|ytd|year to date)\b", re.I)
_MONTH_YEAR_RX = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(20\d\d)\b", re.I)
_PERIOD_CUT_NAME_RX = re.compile(r"\s-\s(?:Q[1-4]\s+20\d\d|H[12]\s+20\d\d|20\d\d(?:\s+YTD)?|"
                                 r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d\d)\s*$", re.I)


def is_period_comparison(*texts):
    """True when the words put a full-window figure next to sub-period
    figures (quarters, months, halves, YTD). One named month that is the
    window itself ("October 2026"), with no run of periods beside it, is
    not a comparison whatever else the prose says."""
    joined = ' '.join(str(t or '') for t in texts)
    if not PERIOD_RX.search(joined):
        return False
    run = bool(_PERIOD_RUN_RX.search(joined))
    if not run:
        months = {m.lower() for m in re.findall(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+20\d\d\b",
                                                 joined, re.I)}
        if len(months) <= 1:
            return False      # a single named month is the window, not a period inside one
    if WHOLE_RX.search(joined):
        return True
    return run and bool(_OVERALL_RX.search(joined))


def is_period_cut_name(name):
    return bool(_PERIOD_CUT_NAME_RX.search(str(name or '')))


def ensure_period_note(reply, question=''):
    """Append the period line once when the reply compares a window
    total with its periods. Never raises."""
    try:
        text = str(reply or '')
        if not text.strip() or PERIOD_HEADER in text:
            return reply
        if not is_period_comparison(text, question):
            return reply
        return text.rstrip() + '\n\n' + f"{PERIOD_HEADER} {PERIOD_NOTE}"
    except Exception:
        return reply


def audience_label(res=None, base_label=''):
    """The audience the Base line names: the subject the read is about
    (with its cohort), as the reader would say it. The profile the read
    was derived from is a fallback, never a label when it is a key, and
    never shown over the audience itself (2026-10-22: a Jackass: Best &
    Last read derived off another file named that file's stem)."""
    res = res if isinstance(res, dict) else {}
    # The universe the read counted, when it was named apart from the
    # subject (2026-11-04, Jenna on Jay Patel's Statham thread: a profile
    # of a person counts people with digital touchpoints to him; viewers
    # of his films are another universe, and the opener and the Base
    # line say which one the figures describe).
    uni = str(res.get('universe') or '').strip()
    if uni:
        return uni
    subj = _display.display(str(res.get('subject') or '').strip())
    cohort = _display.display(str(res.get('cohort') or '').strip())
    if subj:
        if not cohort or cohort.lower() in subj.lower():
            return subj
        if subj.lower() in cohort.lower():
            return cohort          # 'Paramount+ - Avid Fan' already names the subject
        if cohort[:1].islower():
            return f"{subj} {cohort}"   # 'Jackass: Best & Last viewers on Paramount+'
        return f"{subj} - {cohort}"
    return _display.display(str(base_label or res.get('_base_label') or '').strip())


# A label that already names people reads as itself on the Base line
# ('Base: Jackass: Best & Last viewers on Paramount+'), a bare subject
# takes the 'the X audience' frame.
_NAMES_PEOPLE_RX = re.compile(
    r"\b(?:viewers?|fans?|audience|subscribers?|buyers?|shoppers?|listeners?|"
    r"players?|users?|members?|customers?|readers?|voters?|households?|people)\b", re.I)


def ensure(reply, res=None, question='', base_label='', base_people=None,
           domain='', view_context=None):
    """Append the counting block to a viewership reply once. A reply
    that already carries the header gets the missing bullets merged in
    (reply_shape may have opened the block for definitions). Never
    raises; returns the input on any failure.

    `domain` names the open product when the base is a self-contained
    view (2026-10-22, Alexia / The Influencer Project): an attribution
    read gets the campaign's own block (window, exposed base, the
    conversion definition, assets tracked) and never the viewing block,
    whose "viewers" and "carriage" lines describe a different product."""
    try:
        text = str(reply or '')
        if not text.strip():
            return reply
        res = res if isinstance(res, dict) else {}
        reply = ensure_period_note(text, question)
        text = str(reply)
        if str(domain or '') in ('attribution', 'journey', 'partnership'):
            if HEADER in text or carries_definition(text):
                return reply
            try:
                from . import view_base as _vb
                lines = _vb.counting_lines(view_context)
            except Exception:
                lines = []
            return (text.rstrip() + '\n\n' + '\n'.join(lines)) if lines else reply
        # The family the lane stamped picks the block (2026-11-06). A
        # stamped read with figures always carries its family's block;
        # an unstamped reply keeps the old trigger: the viewing block
        # when it carries a viewing figure, else only the purchase line
        # when it carries a purchase figure.
        fam = stamped_family(res)
        if not fam:
            if not is_viewership_read(text, res, question):
                return ensure_purchase_note(reply)
            fam = VIEWING
        elif fam != VIEWING and not _ANY_FIGURE_RX.search(text):
            return ensure_purchase_note(reply)
        if carries_definition(text) or carries_family_definition(text, fam):
            return ensure_purchase_note(reply)
        lines = block(window_start=res.get('window_start'),
                      window_end=res.get('window_end'),
                      window_label=res.get('window_label') or '',
                      base_label=audience_label(res, base_label),
                      base_people=base_people if base_people is not None
                      else res.get('_base_people'),
                      text=text, family=fam,
                      noun=pool_noun_for(res, question))
        body = text.rstrip()
        if HEADER in body:
            # merge under the existing header
            return ensure_purchase_note(body + '\n' + '\n'.join(lines[1:]))
        # a purchase-family figure in the same reply (Jay Patel's overlap read
        # carried "Where this shared audience buys ...") gets the purchase line
        # in the same block (Jenna 2026-11-04)
        return ensure_purchase_note(body + '\n\n' + '\n'.join(lines))
    except Exception:
        return reply
