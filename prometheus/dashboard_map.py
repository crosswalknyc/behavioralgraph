"""The dashboard as a map Prometheus gives directions from (Jenna 2026-11-10).

Jenna, verbatim: "it eomeone asks something like this it should tell
them where to find it. like go to the drop dowb menu and click on
digital journey iq kinda thing". The case: a Sony Television seat asked
"where is the digital jounrey in the dashboard?" and was handed a
finished read instead of directions.

One source for every lane that talks about the dashboard itself:

* PRODUCTS: the SELECT PRODUCT dropdown (top right), each option named
  exactly as the dropdown shows it, with the picker and the tabs the
  product opens on, where its artifacts land, the deep link, and the
  access rule the Jinja template applies to the option.
* TABS: the tabs inside a product (Digital Twins IQ: Twins, Greenlight,
  Launch, Focus Group IQ, Pilot Testing, Trailers, Titles and Key Art,
  Norms; Profile IQ: Insights ... Workspace).
* ARTIFACTS: what a finished thing is called and where it lands (a
  journey under Select a journey, a profile in Select Profile, a twin
  on the Twins tab, exports from the page's export buttons, emailed
  files from Prometheus).
* RAIL: the Prometheus panel itself (Chats & Tasks on the left, New
  chat).
* PLACEHOLDERS: a picker's own placeholder ('Select a journey', 'Select
  Profile', 'Select a twin', 'Choose...') is never a subject label.

prometheus.wayfinding answers from this map; prometheus.product_facts,
prometheus.status_lane and the help lane read the same names. Pure
module: no Flask, no S3, no model. The access rules mirror
templates/index.html (#viewNavDropdown) and app.DEFAULT_HIDDEN_PRODUCTS;
when the host binds ``product_access`` and ``live_features`` the live
values are used, else the seat's own flags with the same defaults.
"""
from __future__ import annotations

import difflib
import os
import re

__all__ = ['DROPDOWN', 'DROPDOWN_WHERE', 'DASHBOARD_URL', 'PRODUCTS', 'TABS', 'ARTIFACTS', 'RAIL', 'PLACEHOLDERS',
           'is_placeholder', 'product', 'product_by_value', 'find_product', 'find_tab', 'find_artifact', 'names_dropdown',
           'names_rail', 'access_for', 'hidden_products', 'can_open', 'deep_link', 'where_lands', 'picker_of', 'tab_of', 'where_things_are']

DROPDOWN = 'SELECT PRODUCT'
DROPDOWN_WHERE = 'the SELECT PRODUCT dropdown at the top right of the dashboard'
DASHBOARD_URL = str(os.environ.get('PM_DASHBOARD_URL') or 'https://dashboard.crosswalknyc.com').rstrip('/')

_ADMIN_ROLES = ('admin', 'super_admin', 'superadmin')


def _role(user):
    return str((user or {}).get('role') or 'user').strip().lower().replace(' ', '_')


def _flag(access, key, default=False):
    v = (access or {}).get(key, default)
    return bool(v) if v is not None else bool(default)


def _modules(access):
    m = (access or {}).get('analysis_iq_modules')
    return [str(x) for x in m] if isinstance(m, list) else []


# ---------------------------------------------------------------- products
#
# The order is the dropdown's display order (templates/index.html,
# #viewNavDropdown, 2026-07-27 order plus the products added since). Each
# `rule` reads the resolved access dict and the role exactly the way the
# option's Jinja guard does. `aliases` are the ways people name the
# product in chat (lower case, typo tolerant matching in find_product).

def _r_profile(a, r):
    return _flag(a, 'has_profile_iq_access', True)


def _r_subscriber(a, r):
    return _flag(a, 'has_subscriber_iq_access')


def _r_journey(a, r):
    return _flag(a, 'has_journey_iq_access') or 'journey_iq' in _modules(a) or r in _ADMIN_ROLES


def _r_twins(a, r):
    return _flag(a, 'has_digital_twins_iq_access') or r in _ADMIN_ROLES


def _r_attribution(a, r):
    return _flag(a, 'has_intent_iq_access', True)


def _r_trends(a, r):
    return _flag(a, 'has_trends_iq_access')


def _r_rankers(a, r):
    return _flag(a, 'has_rankers_iq_access')


def _r_micro(a, r):
    return _flag(a, 'has_microdramas_iq_access')


def _r_blue(a, r):
    return _flag(a, 'has_blue_iq_access')


def _r_fin(a, r):
    return _flag(a, 'has_hedge_fund_iq_access')


def _r_brand_tracking(a, r):
    return _flag(a, 'has_brand_tracking_iq_access') or r in _ADMIN_ROLES


def _r_sf(a, r):
    return _flag(a, 'has_sf_conversion_access') or 'sf_lf_conversion' in _modules(a) or r == 'super_admin'


def _r_bpiq(a, r):
    return 'brand_partnership_iq' in _modules(a) or _flag(a, 'has_brand_partnership_iq_access') or r in _ADMIN_ROLES


def _r_flywheel_conversion(a, r):
    return _flag(a, 'has_flywheel_conversion_access') or 'flywheel_conversion' in _modules(a) or r == 'super_admin'


def _r_flywheel(a, r):
    return _flag(a, 'has_flywheel_iq_access') or 'flywheel_iq' in _modules(a) or r in _ADMIN_ROLES


def _r_admin(a, r):
    return r in _ADMIN_ROLES


PRODUCTS = (
    {'value': 'profileIQ', 'label': 'Profile IQ', 'rule': _r_profile,
     'aliases': ('profile iq', 'profile i q', 'the profile tab', 'profile dashboard', 'audience profiles', 'profile page', 'profile iq dashboard'),
     'picker': 'Select Profile',
     'picker_line': "Pick the audience in the Select Profile dropdown; the Data Cuts list under it holds the cuts of that audience.",
     'tabs': ('Insights', 'Demographics', 'Behavioral', 'Interests', 'Location', 'Compare', 'Exec Summary', 'Marketing', 'Workspace'),
     'lands': 'a finished profile appears in the Select Profile dropdown',
     'blurb': 'Profile IQ is where an audience profile is read: who they are, what they watch, buy, search and subscribe to.'},
    {'value': 'subscriberIQ', 'label': 'Subscriber IQ', 'rule': _r_subscriber,
     'aliases': ('subscriber iq', 'subscriber i q', 'subscriber tracker', 'subscriber trackers', 'sub iq', 'subiq', 'subscriber dashboard'),
     'picker': 'the show list',
     'picker_line': "Pick the title from the show list at the top of the page; each open title is a tab.",
     'tabs': (),
     'lands': 'a finished Subscriber IQ read opens from the show list',
     'blurb': 'Subscriber IQ is where signups, churn and retention are read by title and window.'},
    {'value': 'journeyIQ', 'label': 'Digital Journey IQ', 'rule': _r_journey,
     'aliases': ('digital journey iq', 'digital journey', 'digital journeys', 'journey iq', 'dj iq', 'djiq', 'the journey tab', 'journey dashboard',
                 'customer journey', 'journey view'),
     'picker': 'Select a journey',
     'picker_line': "Your journeys are listed under Select a journey; a finished journey appears there the moment it lands.",
     'tabs': (),
     'lands': 'a finished journey appears under Select a journey in Digital Journey IQ',
     'blurb': 'Digital Journey IQ shows the path from a post, clip or search to the watch, purchase or ticketing visit, step by step.'},
    {'value': 'digitalTwinsIQ', 'label': 'Digital Twins IQ', 'rule': _r_twins,
     'aliases': ('digital twins iq', 'digital twins', 'digital twin', 'twins iq', 'twin iq', 'the twins tab', 'twin dashboard'),
     'picker': 'the Twins tab',
     'picker_line': "Your twins are listed on the Twins tab; Greenlight, Launch, Focus Group IQ, Pilot Testing, Trailers, Titles and Key Art and Norms are the tabs across the top.",
     'tabs': ('Twins', 'Greenlight', 'Launch', 'Focus Group IQ', 'Pilot Testing', 'Trailers', 'Titles and Key Art', 'Norms'),
     'lands': 'a finished twin appears on the Twins tab in Digital Twins IQ',
     'blurb': 'Digital Twins IQ is where a twin of an audience is built and asked questions, in a focus group, a pilot test or a lab.'},
    {'value': 'intentIQ', 'label': 'Attribution IQ', 'rule': _r_attribution,
     'aliases': ('attribution iq', 'attribution', 'attribution dashboard', 'the attribution tab', 'campaign attribution', 'intent iq'),
     'picker': 'the campaign list',
     'picker_line': "Pick the campaign from the list on the landing page; each campaign opens with its assets, first touch, last touch and assists.",
     'tabs': (),
     'lands': 'a finished Attribution IQ campaign appears in the campaign list in Attribution IQ',
     'blurb': 'Attribution IQ tracks a campaign\'s creators, posts and assets to the people who reached a ticketing site, a product page or a signup.'},
    {'value': 'trendsIQ', 'label': 'Trends', 'rule': _r_trends,
     'aliases': ('trends', 'trends iq', 'trends dashboard', 'the trends tab'),
     'picker': '',
     'picker_line': "The Trends page opens on today's board; the lenses across the top switch what it shows.",
     'tabs': (),
     'lands': '',
     'blurb': 'Trends is the daily board of what is moving across search, social, streaming, music, podcasts and gaming.'},
    {'value': 'cultureRankerIQ', 'label': 'Rankers', 'rule': _r_rankers,
     'aliases': ('rankers', 'ranker', 'the rankers tab', 'rankings', 'leaderboards'),
     'picker': '',
     'picker_line': "Rankers opens on its boards: FAST, Music, Podcasts, Streaming and Gaming, each a tab across the top.",
     'tabs': ('FAST', 'Music', 'Podcasts', 'Streaming', 'Gaming'),
     'lands': '',
     'blurb': 'Rankers is the ranked media boards: FAST, Music, Podcasts, Streaming and Gaming.'},
    {'value': 'microdramasIQ', 'label': 'Micro-Series', 'rule': _r_micro,
     'aliases': ('micro-series', 'micro series', 'microseries', 'microdramas', 'micro dramas', 'microdrama', 'vertical shorts', 'short dramas'),
     'picker': '',
     'picker_line': "Micro-Series opens on the leaderboard; the filters across the top narrow it.",
     'tabs': (),
     'lands': '',
     'blurb': 'Micro-Series is the leaderboard for short vertical drama apps and titles.'},
    {'value': 'blueIQ', 'label': 'Blue IQ', 'rule': _r_blue,
     'aliases': ('blue iq', 'blueiq'),
     'picker': '', 'picker_line': '', 'tabs': (), 'lands': '',
     'blurb': 'Blue IQ is its own board on the dashboard.'},
    {'value': 'hedgeFundIQ', 'label': 'Fin IQ', 'rule': _r_fin,
     'aliases': ('fin iq', 'finiq', 'hedge fund iq', 'the fin tab', 'financial iq', 'tickers'),
     'picker': 'the ticker list',
     'picker_line': "Pick the ticker from the list on the left; each ticker opens with its own tabs.",
     'tabs': (),
     'lands': '',
     'blurb': 'Fin IQ reads the digital engagement behind a ticker.'},
    {'value': 'brandTrackingIQ', 'label': 'Brand Tracking', 'rule': _r_brand_tracking,
     'aliases': ('brand tracking', 'brand tracker', 'brand tracking iq', 'the brand tracking tab'),
     'picker': '', 'picker_line': '', 'tabs': (), 'lands': '',
     'blurb': 'Brand Tracking is the competitive set tracker: paid accounts, audience mix, overlap and retention.'},
    {'value': 'sfConversion', 'label': 'SF Conversion', 'rule': _r_sf,
     'aliases': ('sf conversion', 'short form conversion', 'short-form conversion', 'sf to lf', 'short form to long form', 'sf lf conversion'),
     'picker': 'the study list',
     'picker_line': "Pick the study from the list on the landing page.",
     'tabs': (),
     'lands': 'a finished SF Conversion study appears in the study list',
     'blurb': 'SF Conversion reads how short-form views convert to the long-form watch.'},
    {'value': 'brandPartnershipIQ', 'label': 'Brand Partnership IQ', 'rule': _r_bpiq,
     'aliases': ('brand partnership iq', 'brand partnership', 'brand partnerships', 'bpiq', 'partnership iq', 'the partnership tab', 'partnership valuation',
                 'brand partnership valuation'),
     'picker': 'the valuation list',
     'picker_line': "Pick the valuation from the list on the landing page; each opens with its dollar figure and the four lenses behind it.",
     'tabs': (),
     'lands': 'a finished valuation appears in the valuation list in Brand Partnership IQ',
     'blurb': 'Brand Partnership IQ values a talent or title against a brand: audience fit, earned media, brand lift and conversion, one dollar figure.'},
    {'value': 'flywheelConversion', 'label': 'Flywheel Conversion', 'rule': _r_flywheel_conversion,
     'aliases': ('flywheel conversion', 'flywheel conversion dashboard'),
     'picker': 'the study list',
     'picker_line': "Pick the study from the list on the landing page.",
     'tabs': (),
     'lands': 'a finished Flywheel Conversion study appears in the study list',
     'blurb': 'Flywheel Conversion reads the conversion loop between a creator\'s surfaces and the watch.'},
    {'value': 'flywheelIQ', 'label': 'Flywheel', 'rule': _r_flywheel,
     'aliases': ('flywheel', 'flywheel iq', 'the flywheel tab'),
     'picker': 'the study list',
     'picker_line': "Pick the study from the list on the landing page; a run you asked for opens from there.",
     'tabs': (),
     'lands': 'a finished Flywheel appears in the study list in Flywheel',
     'blurb': 'Flywheel reads how a title\'s surfaces feed each other and the watch.'},
    {'value': 'admin', 'label': 'Admin', 'rule': _r_admin,
     'aliases': ('admin', 'the admin page', 'admin panel', 'admin settings', 'live features', 'user settings', 'user management'),
     'picker': '', 'picker_line': "Admin opens on its own page: Users, Settings and Live Features are the tabs.", 'tabs': ('Users', 'Settings', 'Live Features'), 'lands': '',
     'blurb': 'Admin is where seats, access and Live Features are managed.'},
)

# The custom analysis area (Analysis IQ) is a view inside Profile IQ, not
# a dropdown option; its modules are granted per seat.
ANALYSIS_IQ = {'value': 'analysisIQ', 'label': 'Analysis IQ', 'parent': 'profileIQ',
               'aliases': ('analysis iq', 'custom analysis', 'analysis tab', 'chatbot profile iq'),
               'line': "Analysis IQ is the custom analysis area inside Profile IQ: open Profile IQ from the SELECT PRODUCT dropdown and use the analysis "
                       "tabs there (Profile Analysis and the modules your admin granted). Anything built there lands in Select Profile or in the product it belongs to."}

_BY_VALUE = {p['value']: p for p in PRODUCTS}


def product(value):
    return _BY_VALUE.get(str(value or ''))


product_by_value = product


# -------------------------------------------------------------------- tabs
#
# (product value, tab id the page uses, label as the tab shows it, aliases)
TABS = (
    ('digitalTwinsIQ', 'twins', 'Twins', ('twins tab', 'the twins list')),
    ('digitalTwinsIQ', 'greenlight', 'Greenlight', ('greenlight', 'green light', 'script read', 'concept test', 'greenlight lab')),
    ('digitalTwinsIQ', 'launch', 'Launch', ('launch tab', 'launch lab', 'spend engine', 'the launch')),
    ('digitalTwinsIQ', 'panel', 'Focus Group IQ', ('focus group iq', 'focus group', 'focus groups', 'the room', 'panel lab', 'the panel')),
    ('digitalTwinsIQ', 'pilot', 'Pilot Testing', ('pilot testing', 'pilot test', 'pilot tests', 'the pilot tab', 'episode test', 'dial test', 'pilot')),
    ('digitalTwinsIQ', 'trailer', 'Trailers', ('trailers', 'trailer test', 'the trailer tab', 'trailer testing')),
    ('digitalTwinsIQ', 'titles', 'Titles and Key Art', ('titles and key art', 'key art', 'title test', 'title testing', 'key art test', 'titles tab')),
    ('digitalTwinsIQ', 'norms', 'Norms', ('norms', 'the norms tab', 'norm')),
    ('profileIQ', 'insights', 'Insights', ('insights tab', 'the insights')),
    ('profileIQ', 'demographics', 'Demographics', ('demographics tab', 'demographics', 'demo tab')),
    ('profileIQ', 'behavioral', 'Behavioral', ('behavioral tab', 'behavioral', 'behaviors tab')),
    ('profileIQ', 'interests', 'Interests', ('interests tab', 'the interests')),
    ('profileIQ', 'location', 'Location', ('location tab', 'the location', 'dma tab', 'geography tab')),
    ('profileIQ', 'comparison', 'Compare', ('compare tab', 'comparison tab', 'the compare')),
    ('profileIQ', 'exec', 'Exec Summary', ('exec summary', 'executive summary', 'exec tab')),
    ('profileIQ', 'marketing', 'Marketing', ('marketing tab', 'the marketing')),
    ('profileIQ', 'workspace', 'Workspace', ('workspace tab', 'the workspace')),
)


def tab_of(product_value, tab_id):
    for pv, tid, label, _al in TABS:
        if pv == product_value and tid == tab_id:
            return {'product': pv, 'id': tid, 'label': label}
    return None


# --------------------------------------------------------------- artifacts
#
# noun -> (product value or '', where it lands, the picker/tab to look in)
ARTIFACTS = {
    'journey': ('journeyIQ', 'under Select a journey in Digital Journey IQ', 'Select a journey'),
    'profile': ('profileIQ', 'in the Select Profile dropdown in Profile IQ', 'Select Profile'),
    'cut': ('profileIQ', 'in the Data Cuts list under its audience in Profile IQ', 'Select Profile'),
    'twin': ('digitalTwinsIQ', 'on the Twins tab in Digital Twins IQ', 'the Twins tab'),
    'focus group': ('digitalTwinsIQ', 'on the Focus Group IQ tab in Digital Twins IQ', 'the Focus Group IQ tab'),
    'pilot test': ('digitalTwinsIQ', 'on the Pilot Testing tab in Digital Twins IQ', 'the Pilot Testing tab'),
    'script read': ('digitalTwinsIQ', 'on the Greenlight tab in Digital Twins IQ', 'the Greenlight tab'),
    'trailer test': ('digitalTwinsIQ', 'on the Trailers tab in Digital Twins IQ', 'the Trailers tab'),
    'campaign': ('intentIQ', 'in the campaign list in Attribution IQ', 'the campaign list'),
    'valuation': ('brandPartnershipIQ', 'in the valuation list in Brand Partnership IQ', 'the valuation list'),
    'flywheel': ('flywheelIQ', 'in the study list in Flywheel', 'the study list'),
    'tracker': ('subscriberIQ', 'in the show list in Subscriber IQ', 'the show list'),
    'export': ('', "from the export buttons on the page you are looking at (the download arrow or Export at the top of the view); every data answer in this chat also carries its own CSV link under the answer", ''),
    'email': ('', "in your inbox, sent from Prometheus; say 'email me the file' on any answer and it goes out", ''),
    'deck': ('', "in your inbox, sent from Prometheus when it is built; say 'build a deck from this' on any answer", ''),
    'read': ('', 'in this chat, as its own message, with its CSV link under it', ''),
    'chat': ('', 'in Chats & Tasks, the rail on the left of this panel', ''),
}

_ARTIFACT_RX = (
    ('journey', re.compile(r"\b(?:digital\s+)?journeys?\b", re.I)),
    ('cut', re.compile(r"\b(?:data\s+cuts?|avid\s+(?:fan\s+)?cuts?|cuts?\s+of\b|gender\s+cuts?|age\s+cuts?)", re.I)),
    ('profile', re.compile(r"\bprofiles?\b|\btotal\s+universe\b|\bavid\s+fan\b", re.I)),
    ('focus group', re.compile(r"\bfocus\s+groups?\b|\bthe\s+room\b", re.I)),
    ('pilot test', re.compile(r"\bpilot\s+(?:tests?|testing)\b|\bpilots?\b|\bepisode\s+tests?\b|\bdial\s+tests?\b", re.I)),
    ('script read', re.compile(r"\bscript\s+reads?\b|\bconcept\s+tests?\b|\bgreenlight\b", re.I)),
    ('trailer test', re.compile(r"\btrailer\s+tests?\b|\btrailers?\b", re.I)),
    ('twin', re.compile(r"\b(?:digital\s+)?twins?\b", re.I)),
    ('campaign', re.compile(r"\bcampaigns?\b|\battribution\s+(?:read|report)s?\b", re.I)),
    ('valuation', re.compile(r"\bvaluations?\b|\bpartnership\s+(?:read|value)s?\b", re.I)),
    ('flywheel', re.compile(r"\bflywheels?\b", re.I)),
    ('tracker', re.compile(r"\b(?:subscriber\s+)?trackers?\b|\bsubscriber\s+reads?\b", re.I)),
    ('export', re.compile(r"\b(?:csvs?|exports?|downloads?|xlsx|spreadsheets?|excel|workbooks?)\b|\bexport\s+button\b|\bdownload\s+button\b", re.I)),
    ('deck', re.compile(r"\b(?:decks?|powerpoints?|pptx|slides?|one[- ]pagers?|pdfs?)\b", re.I)),
    ('email', re.compile(r"\b(?:e-?mails?|inbox|emailed\s+files?)\b", re.I)),
    ('read', re.compile(r"\b(?:reads?|answers?|results?|reports?|write[- ]?ups?|summar(?:y|ies))\b", re.I)),
    ('chat', re.compile(r"\b(?:chats?|threads?|conversations?|tasks?|history)\b", re.I)),
)


def where_lands(noun):
    """'under Select a journey in Digital Journey IQ' for 'journey'; the
    product's own landing line for a product value; '' when unknown."""
    key = str(noun or '').strip().lower()
    if key in ARTIFACTS:
        return ARTIFACTS[key][1]
    p = product(noun)
    return (p or {}).get('lands') or ''


def picker_of(value):
    return (product(value) or {}).get('picker') or ''


# -------------------------------------------------------------------- rail
RAIL = {
    'label': 'Chats & Tasks',
    'line': ("Your chats and tasks are in the Chats & Tasks rail on the left edge of this panel: click Chats & Tasks to open it, "
             "pick a chat to go back to it, or press New chat to start a fresh one. The search field at the top of the panel finds any question you asked."),
}

_RAIL_RX = re.compile(r"\b(?:chats?\s*(?:&|and)\s*tasks|my\s+(?:chats?|threads?|conversations?|tasks?|history|old\s+chats?|past\s+chats?|previous\s+chats?)|"
                      r"(?:other|previous|earlier|old|past)\s+(?:chats?|threads?|conversations?)|new\s+chat|chat\s+history|thread\s+rail|the\s+rail)\b", re.I)
_DROPDOWN_RX = re.compile(r"\b(?:select\s+product|product\s+(?:menu|dropdown|drop\s*down|list|switcher|selector)|(?:the\s+)?(?:drop\s*down|dropdown)\s+menu|"
                          r"(?:product|module|tool)\s+(?:picker|chooser)|switch\s+(?:products?|modules?|tools?)|main\s+menu|top\s+menu|navigation)\b", re.I)


def names_rail(text):
    return bool(_RAIL_RX.search(str(text or '')))


def names_dropdown(text):
    return bool(_DROPDOWN_RX.search(str(text or '')))


# -------------------------------------------------------------- matching

def _words(text):
    return re.sub(r"[^a-z0-9+&]+", " ", str(text or '').lower()).split()


def _fuzzy_hit(words, alias, *, cutoff=0.84):
    """True when the alias appears in the words, allowing a typo per word
    ('digital jounrey' names Digital Journey IQ). Exact phrase first;
    then a sliding window compared by SequenceMatcher."""
    al = alias.split()
    n = len(al)
    if not n or len(words) < n:
        return False
    joined = ' ' + ' '.join(words) + ' '
    if f" {alias} " in joined:
        return True
    if len(alias) < 6 or n == 1:
        return False                    # a single word is exact or nothing ('profile' never spells 'profiles')
    target = ''.join(al)
    for i in range(len(words) - n + 1):
        win = ''.join(words[i:i + n])
        if abs(len(win) - len(target)) > max(2, len(target) // 4):
            continue
        if difflib.SequenceMatcher(None, win, target).ratio() >= cutoff:
            return True
    return False


def find_product(text):
    """The product the words name, longest alias first; None when none.
    'the digital jounrey' -> Digital Journey IQ; 'twins' -> Digital
    Twins IQ; 'profile' alone names Profile IQ only with 'iq', 'tab',
    'page' or 'dashboard' beside it (a bare 'profile' is an artifact)."""
    words = _words(text)
    if not words:
        return None
    best, best_len = None, 0
    for p in PRODUCTS:
        for alias in p['aliases']:
            if len(alias) > best_len and _fuzzy_hit(words, alias):
                best, best_len = p, len(alias)
    if best is None:
        for alias in ANALYSIS_IQ['aliases']:
            if _fuzzy_hit(words, alias):
                return dict(ANALYSIS_IQ)
    return best


def find_tab(text):
    """{'product', 'id', 'label'} for a tab the words name, else None."""
    words = _words(text)
    if not words:
        return None
    best, best_len = None, 0
    for pv, tid, label, aliases in TABS:
        for alias in (label.lower(),) + tuple(aliases):
            if len(alias) > best_len and _fuzzy_hit(words, alias):
                best, best_len = {'product': pv, 'id': tid, 'label': label}, len(alias)
    return best


def find_artifact(text):
    """The artifact noun the words carry ('journey', 'profile', 'twin',
    'export', ...), the most specific first; '' when none."""
    t = str(text or '')
    for noun, rx in _ARTIFACT_RX:
        if rx.search(t):
            return noun
    return ''


# ----------------------------------------------------------------- access

def access_for(user, host=None):
    """The resolved product access for a seat: the host's own resolver
    (app.compute_product_access_flags) when bound, else the seat's flags
    with the template's defaults. Never raises."""
    u = user if isinstance(user, dict) else {}
    role = _role(u)
    try:
        if host is not None and host.has('product_access'):
            acc = host.product_access(u, role)
            if isinstance(acc, dict):
                return dict(acc, _role=role)
    except Exception:
        pass
    acc = {k: v for k, v in u.items() if str(k).startswith('has_') or k in ('analysis_iq_modules', 'allowed_runs', 'allowed_journey_iq_runs')}
    if role == 'super_admin':
        for key in _FLAG_KEYS:
            acc[key] = True
    acc['_role'] = role
    return acc


_FLAG_KEYS = ('has_profile_iq_access', 'has_subscriber_iq_access', 'has_journey_iq_access', 'has_digital_twins_iq_access', 'has_intent_iq_access',
              'has_trends_iq_access', 'has_rankers_iq_access', 'has_microdramas_iq_access', 'has_blue_iq_access', 'has_hedge_fund_iq_access',
              'has_brand_tracking_iq_access', 'has_sf_conversion_access', 'has_brand_partnership_iq_access', 'has_flywheel_conversion_access',
              'has_flywheel_iq_access')


def hidden_products(host=None):
    """The admin's global hide list (Live Features), {} when unbound."""
    try:
        if host is not None and host.has('live_features'):
            lf = host.live_features()
            hp = (lf or {}).get('hidden_products') if isinstance(lf, dict) else None
            return {k: bool(v) for k, v in (hp or {}).items()} if isinstance(hp, dict) else {}
    except Exception:
        pass
    return {}


def can_open(value, access, hidden=None, *, locked=False):
    """'open' | 'no_access' | 'hidden' | 'locked' | 'unknown' for one
    dropdown option and one seat, the way the page decides it."""
    p = product(value)
    if p is None:
        if value == ANALYSIS_IQ['value']:
            return 'open'
        return 'unknown'
    if hidden and hidden.get(value):
        return 'hidden'
    if locked and value == 'digitalTwinsIQ':
        return 'locked'
    role = str((access or {}).get('_role') or '').lower()
    try:
        return 'open' if p['rule'](access or {}, role) else 'no_access'
    except Exception:
        return 'no_access'


def deep_link(value, **params):
    """The dashboard's deep link for a product: ?view=<value>, plus the
    params the view reads (Digital Twins IQ: twin, tab; Flywheel: run)."""
    if not product(value):
        return ''
    q = f"view={value}"
    for k, v in params.items():
        if v:
            q += f"&{k}={v}"
    return f"{DASHBOARD_URL}/?{q}"


# ----------------------------------------------------------- placeholders
#
# A picker's own placeholder read as a subject once (2026-11-10: a read
# bound to "Select a Journey", the Digital Journey IQ picker's text the
# reader had quoted). These never become a subject label, a thread label
# or a page name; the binders fall through to the real selection or to
# nothing.
PLACEHOLDERS = frozenset((
    'select a journey', 'select journey', 'select a journey to view', 'select a journey from the dropdown above', 'select a journey.',
    'select profile', 'select a profile', 'select your profile', 'select a run', 'select run', 'select a twin', 'select twin',
    'select a title', 'select a show', 'select a campaign', 'select a study', 'select a module', 'select category', 'select a category',
    'select module to navigate', 'select product', 'select a product', 'select an option', 'select one', 'select',
    'choose', 'choose...', 'choose a journey', 'choose a profile', 'choose a twin', 'choose a run', 'choose one', 'choose an option',
    'pick a journey', 'pick a profile', 'pick a twin', 'pick one', 'none selected', 'no profile selected', 'no journey selected',
    'nothing selected', 'no selection', 'new chat', 'untitled',
))
_PLACEHOLDER_RX = re.compile(
    r"^(?:please\s+)?(?:select|choose|pick)(?:\s+(?:a|an|the|your|one))?(?:\s+(?:journey|profile|twin|run|title|show|campaign|study|module|category|"
    r"product|option|item|file|cut|audience|tab|view|dataset|report|one))?(?:\s+(?:to\s+view|to\s+(?:get\s+)?start(?:ed)?|from\s+the\s+(?:dropdown|list|menu)(?:\s+above)?|below|above))?\s*(?:\.{2,}|\u2026|\.)?\s*$",
    re.I)


def where_things_are(access=None, hidden=None):
    """One plain paragraph of the dashboard's layout, for the help brief
    and the product facts: the dropdown, each product with its picker,
    where finished things land, and the rail. With ``access`` the list
    is the seat's own; without it every product."""
    parts = []
    for p in PRODUCTS:
        if p['value'] == 'admin':
            continue
        if access is not None and can_open(p['value'], access, hidden) != 'open':
            continue
        bit = p['label']
        if p.get('picker'):
            bit += f" (pick from {p['picker']})" if not p['picker'].lower().startswith('select') else f" ({p['picker']})"
        parts.append(bit)
    lands = [ARTIFACTS['journey'][1], ARTIFACTS['profile'][1], ARTIFACTS['twin'][1]]
    return (f"Every product is in {DROPDOWN_WHERE}: " + ', '.join(parts) + ". "
            f"A finished journey lands {lands[0]}, a finished profile {lands[1]}, a twin {lands[2]}; exports download {ARTIFACTS['export'][1].split(' (')[0]}, "
            f"and emailed files arrive from Prometheus. {RAIL['line']}")


def is_placeholder(label):
    """True when the text is a picker placeholder, not a thing's name."""
    s = ' '.join(str(label or '').split()).strip()
    if not s:
        return False
    low = s.lower().rstrip('.').strip()
    low = low.replace('\u2026', '').strip()
    if low in PLACEHOLDERS or low + '.' in PLACEHOLDERS:
        return True
    if s.endswith(('...', '\u2026')) and re.match(r"^(?:select|choose|pick)\b", low):
        return True
    return bool(_PLACEHOLDER_RX.match(s))
