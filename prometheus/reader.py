"""The Reader: one understanding step before anything else (Jenna,
2026-10-14: "how can we make prometheus closer to your level of
intelligence in understanding what is being asked").

Until now an ask met about twenty pattern rules first, each looking at
the sentence through a keyhole, and whichever matched first claimed it.
What did not match fell to a model that had been told what job to do
("draft a build", "analyze the page") rather than asked what the
person wants. Every miss became another rule.

The Reader does what a good analyst does with a pasted question: it
reads the whole situation once, with everything in front of it, and
says what is being asked.

  in:  the ask, the last eight turns, who is asking (role, company,
       standing preferences), what is on screen with its key figures,
       the nearest library subjects (so a typo resolves), what is
       pending (a brief waiting, an offer open), the products and
       prices, and the nearest labeled examples from the bank
  out: a typed reading: intent, the subjects it means and which library
       entry each one is, window, platform, what the person is trying
       to decide, the shape of answer wanted, a one-line restatement,
       and a confidence

The lanes become executors chosen by the reading; the pattern rules
stay as a fast fallback when the Reader is unsure or unavailable.

Every correction Jenna pastes becomes a labeled example in the bank
(`system/pm_reader_bank.json`); the Reader is shown the nearest ones on
every ask, so a paste becomes training signal instead of a rule. The
nightly grades the Reader against the ask log and the bank.

Fail-safe: any failure returns None and routing proceeds as before.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback

INTENTS = (
    'exists',          # does the dashboard have X
    'size',            # how many people are in X: sample, incidence, projected band
    'status',          # where is my X, not received, still running
    'run_pending',     # run / approve the brief that is waiting
    'build_profile',   # build a Profile IQ audience
    'cut',             # a cut of an existing profile
    'journey',         # a Digital Journey
    'valuation',       # a Brand Partnership read
    'campaign',        # an Attribution IQ campaign tracker
    'tracker',         # a Subscriber IQ tracker
    'analyze_page',    # a question about what is on screen
    'analyze',         # an analytical question answered from the library and what we know
    'compare',         # two or more audiences side by side
    'capability',      # can we / is there a way to
    'command',         # email me the file, download, open X, send to
    'deck',            # build a deck
    'definition',      # what does this number mean / how is it calculated
    'pricing',         # what does X cost
    'decline',         # not now / no thanks
    'chat',            # greetings, thanks, anything else
)
# intents with a deterministic executor in the service
EXECUTABLE = frozenset(('exists', 'size', 'status', 'capability', 'decline'))
# intents that pick a model surface
ANALYZE_SET = frozenset(('analyze_page', 'analyze', 'compare', 'definition'))
BUILD_SET = frozenset(('build_profile', 'cut', 'journey', 'valuation', 'campaign', 'tracker'))

BANK_KEY = 'system/pm_reader_bank.json'
READ_TIMEOUT_S = 40.0
_BANK_TTL_S = 120.0
_bank_cache = {'ts': 0.0, 'doc': None}
_bank_lock = threading.Lock()

CHARTER = """You are the reader for Prometheus, the analyst inside the Crosswalk dashboard. Your only job is to say what the person is asking for, the way a careful senior analyst would after reading the whole thread. You do not answer the question.

Crosswalk holds a library of audiences (Profile IQ: a person, brand, title or behavior with demographics and the brands, platforms and talent they over-index on), Subscriber IQ trackers (a title on a streaming service: accounts that watched, new and reactivated subscribers, how long they stayed), Digital Journeys (the path from a post or clip to a watch, purchase or ticketing visit), Brand Partnership reads (a talent or title against a brand, one dollar figure), Attribution IQ campaigns (creators, posts and assets tracked to the people who reached a ticketing site, a product page or a signup), decks, and files. Every count is measured inside a 10 million person US sample and projected to the US (x 32.99).

How to read:
- A question is a question. "Do we have", "how big", "how many", "what is", "who skews", "which" are never build orders, even on the build screen. Only an order to build, pull, run, create or make is a build, and "don't build, answer from what exists" is answer mode.
- Names are what they are, in any case and with typos: "fanduel cuatomers" is FanDuel customers; "Kalshi Cutomers" is Kalshi customers. Match them to the library candidates when one clearly fits; otherwise keep the name as written.
- Audience nouns (customers, users, fans, viewers, buyers) attach to a brand; the subject is the brand with that qualifier.
- Several subjects can sit in one ask ("first X, the second Y"; "X and Y"; "these two"). List them all.
- "This", "that", "it", "this audience" point at the page on screen when one is open.
- "Like the one you did for X" / "the same analysis you did for X" names a precedent; put precedent:X in flags and keep the new subject as the subject. When the library shows X as a journey, valuation, campaign or tracker, the intent is that product for the new subject, not analyze.
- A size question (how big, size of, sample size of X, incidence, projected band) is intent size, never a paid read. "How many people watched X" is analyze (a consumption count).
- Where is my X, I don't see X, have not received, still waiting, is it done: status.
- A short yes / run it / approve / go ahead with nothing else: run_pending.
- Is there a way to, can we, could you, how do we set up: capability (unless it is a polite build order like "can you build me a Nike profile", which is build_profile).
- What does this number mean / how is this calculated: definition. What does X cost: pricing.
- Email me, send me the file, download the csv, open X: command. Build a deck / one-pager / slides: deck.
- Churn, retention, reactivation, overlap, demographics, over-indexing, trends on a title or audience: analyze (or analyze_page when it is about the page on screen).
- When two readings are plausible, prefer the one that answers rather than the one that spends the reader's money, and lower your confidence.

Return JSON only:
{"intent": "<one of the intents>", "confidence": <0 to 1>,
 "subjects": [{"as_written": "<text>", "catalog": "<library subject it means, or empty>", "kind": "<person|brand|title|audience|platform|unknown>"}],
 "window": "<the window as written, or empty>", "platform": "<platform named, or empty>",
 "wants": "<one sentence: what they are trying to get or decide>",
 "answer_shape": "<number|list|explanation|file|deck|brief|status|yes_no>",
 "restate": "<one plain sentence restating the ask in the second person, e.g. You want the size of two audiences, FanDuel customers and Kalshi customers.>",
 "flags": ["<negated_build|multiple_subjects|typo|refers_to_page|precedent:<name>|custom_window|urgent>"]}
Intents: """ + ', '.join(INTENTS) + '.'


# ------------------------------------------------------------- bank
SEED_EXAMPLES = [
    {"text": "i need the size of two data sets, first FanDuel cuatomers, the second Kalshi Cutomers", "intent": "size", "subjects": ["FanDuel customers", "Kalshi customers"], "note": "Ben, Kartel: drew a $500 read offer"},
    {"text": "how big is the fanduel audience size", "intent": "size", "subjects": ["FanDuel"], "note": "drew a viewership read offer"},
    {"text": "no i need to know the sample size in the data set of how many fanduel customers you have to decide if i want to buy the data set", "intent": "size", "subjects": ["FanDuel customers"], "note": "drew the ten million panel line"},
    {"text": "FanDuel is the brand, i want that size", "intent": "size", "subjects": ["FanDuel"], "note": ""},
    {"text": "do we have a data pull on fanduel cusotmers?", "intent": "exists", "subjects": ["FanDuel customers"], "note": "typo; FanDuel sat on the dashboard"},
    {"text": "do we have any MMA fighters in profile iq?", "intent": "exists", "subjects": ["MMA fighters"], "note": "a library lookup, never a build"},
    {"text": "any boxers?", "intent": "exists", "subjects": ["boxers"], "note": "follow-up to a library lookup"},
    {"text": "Do you see the SWAT Exiles Season 1 Subscriber IQ?", "intent": "exists", "subjects": ["S.W.A.T. Exiles Season 1"], "note": "drafted a duplicate tracker"},
    {"text": "what is the size of teh Crosswalk sample audience from January 1, 2026 to date", "intent": "definition", "subjects": [], "note": "the ten million panel itself, not an audience"},
    {"text": "I don't see No Reservations - only Babylon 5 and have not received an email yet", "intent": "status", "subjects": ["Anthony Bourdain No Reservations"], "note": "Carolyn: drew a clarifier"},
    {"text": "is the Gunna profile done?", "intent": "status", "subjects": ["Gunna"], "note": ""},
    {"text": "where is the howdy deck", "intent": "command", "subjects": ["Howdy"], "note": "a named artifact; the deliverable lane"},
    {"text": "yes, run it", "intent": "run_pending", "subjects": [], "note": "runs the brief waiting in the thread"},
    {"text": "approve", "intent": "run_pending", "subjects": [], "note": ""},
    {"text": "Build a digital journey for Anthony Bourdain No Reservations on Amazon Prime just like the one you did for Babylon 5", "intent": "journey", "subjects": ["Anthony Bourdain No Reservations"], "note": "precedent:Babylon 5; the first message is complete"},
    {"text": "audience of Taylor Swift fans", "intent": "build_profile", "subjects": ["Taylor Swift"], "note": ""},
    {"text": "can you build me a Nike profile", "intent": "build_profile", "subjects": ["Nike"], "note": "a polite build order"},
    {"text": "pull an audience of yoga clothing buyers", "intent": "build_profile", "subjects": ["yoga clothing buyers"], "note": ""},
    {"text": "Pull Subscriber IQ for Livestream from Hell (movie) on Prime Video", "intent": "tracker", "subjects": ["Livestream from Hell"], "note": ""},
    {"text": "can you value Gunna x Nike?", "intent": "valuation", "subjects": ["Gunna", "Nike"], "note": ""},
    {"text": "set up attribution for the Influencer Project campaign", "intent": "campaign", "subjects": ["The Influencer Project"], "note": ""},
    {"text": "female only cut of the Reba profile", "intent": "cut", "subjects": ["Reba McEntire"], "note": ""},
    {"text": "Is there a way to see which creators drove ticket sales for a campaign?", "intent": "capability", "subjects": [], "note": "drew the opener"},
    {"text": "can we track churn for Landman on Paramount+?", "intent": "capability", "subjects": ["Landman"], "note": ""},
    {"text": "Don't build a new profile. Answer this from the existing ones: who skews older, Gunna or Drake?", "intent": "compare", "subjects": ["Gunna", "Drake"], "note": "negated_build"},
    {"text": "who skews older, Gunna or Drake?", "intent": "compare", "subjects": ["Gunna", "Drake"], "note": ""},
    {"text": "What changed in the last 48 hours with Live stream from hell", "intent": "analyze_page", "subjects": ["Livestream from Hell"], "note": "asked on the Subscriber IQ page"},
    {"text": "How many audience members in the Starz profile engaged with the Livestream from Hell, which launched on September 29-30.", "intent": "analyze", "subjects": ["Starz", "Livestream from Hell"], "note": ""},
    {"text": "what is the 90 day retention of the Obsession signup cohort on Peacock", "intent": "analyze", "subjects": ["Obsession"], "note": "churn and retention are digital behaviors; never declined"},
    {"text": "how many people watched Landman", "intent": "analyze", "subjects": ["Landman"], "note": "a consumption count, not a size"},
    {"text": "Is that 55% of total viewers or of new and reactivated watchers?", "intent": "definition", "subjects": [], "note": "a question about the page's figure"},
    {"text": "how is the conversion rate calculated here", "intent": "definition", "subjects": [], "note": ""},
    {"text": "what does a profile cost", "intent": "pricing", "subjects": [], "note": ""},
    {"text": "email me the Babylon 5 breakdown", "intent": "command", "subjects": ["Babylon 5"], "note": ""},
    {"text": "download the csv", "intent": "command", "subjects": [], "note": ""},
    {"text": "build the Paige Bueckers insights deck", "intent": "deck", "subjects": ["Paige Bueckers"], "note": ""},
    {"text": "make this a deck", "intent": "deck", "subjects": [], "note": "refers_to_page"},
    {"text": "not now", "intent": "decline", "subjects": [], "note": ""},
    {"text": "thanks!", "intent": "chat", "subjects": [], "note": ""},
    {"text": "Section 4 of the Digital Journey page, please remove the clickstream tab", "intent": "command", "subjects": [], "note": "a design request"},
    {"text": "how big is this audience", "intent": "size", "subjects": ["<page>"], "note": "refers_to_page"},
    {"text": "size of Nike runners and Adidas runners", "intent": "size", "subjects": ["Nike runners", "Adidas runners"], "note": "multiple_subjects"},
    {"text": "I would like to see the same analysis you did for Babylon 5 for No Reservations", "intent": "journey", "subjects": ["Anthony Bourdain No Reservations"], "note": "precedent:Babylon 5"},
    {"text": "Pull the same-day, 7-day, and 30-day timing breakdown for No Reservations", "intent": "analyze_page", "subjects": ["Anthony Bourdain No Reservations"], "note": "a breakdown of a delivered read"},
    {"text": "gen pop who watched a social clip for No Reservations and then watched an episode", "intent": "analyze", "subjects": ["Anthony Bourdain No Reservations"], "note": "a count from the journey on screen"},
]


def _toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", str(s or '').lower()) if len(t) > 2)


def load_bank(s3=None, bucket='dashboard-inputs', force=False):
    """The labeled bank, cached two minutes; the seed when S3 is away."""
    now = time.time()
    with _bank_lock:
        if not force and _bank_cache['doc'] is not None and now - _bank_cache['ts'] < _BANK_TTL_S:
            return _bank_cache['doc']
    doc = None
    try:
        if s3 is None:
            from .host import host as _h
            s3 = _h.s3_client
            bucket = _h.bucket
        body = s3.get_object(Bucket=bucket, Key=BANK_KEY)['Body'].read()
        doc = json.loads(body.decode('utf-8') or '{}')
    except Exception:
        doc = None
    if not isinstance(doc, dict) or not doc.get('examples'):
        doc = {'examples': list(SEED_EXAMPLES), 'seeded': True}
    with _bank_lock:
        _bank_cache.update({'ts': now, 'doc': doc})
    return doc


def bank_add(text, intent, subjects=(), note='', *, s3=None, bucket='dashboard-inputs', added_by='correction'):
    """A labeled example joins the bank (dedupe on the text). Returns the
    bank size, or 0 when nothing was written."""
    try:
        if not str(text or '').strip() or intent not in INTENTS:
            return 0
        if s3 is None:
            from .host import host as _h
            s3 = _h.s3_client
            bucket = _h.bucket
        doc = load_bank(s3, bucket, force=True)
        key = ' '.join(str(text).split()).lower()
        ex = [e for e in (doc.get('examples') or []) if ' '.join(str(e.get('text') or '').split()).lower() != key]
        ex.append({'text': ' '.join(str(text).split())[:400], 'intent': intent, 'subjects': [str(x)[:120] for x in (subjects or [])][:6],
                   'note': str(note or '')[:200], 'added': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'added_by': added_by})
        doc = {'examples': ex[-600:], 'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        s3.put_object(Bucket=bucket, Key=BANK_KEY, Body=json.dumps(doc, ensure_ascii=False).encode('utf-8'), ContentType='application/json')
        with _bank_lock:
            _bank_cache.update({'ts': time.time(), 'doc': doc})
        return len(ex)
    except Exception:
        traceback.print_exc()
        return 0


def nearest_examples(text, bank=None, k=12):
    """The k bank examples closest to the ask (token overlap), always
    including a spread of intents."""
    bank = bank or load_bank()
    ex = [e for e in (bank.get('examples') or []) if e.get('text') and e.get('intent') in INTENTS]
    q = _toks(text)
    scored = []
    for e in ex:
        t = _toks(e['text'])
        inter = len(q & t)
        union = len(q | t) or 1
        scored.append((inter / union + (0.05 if inter else 0.0), e))
    scored.sort(key=lambda x: -x[0])
    out = [e for _s, e in scored[:k]]
    seen = {e['intent'] for e in out}
    for _s, e in scored[k:]:
        if len(out) >= k + 6:
            break
        if e['intent'] not in seen:
            out.append(e)
            seen.add(e['intent'])
    return out


# ---------------------------------------------------------- context
def _page(ctx):
    try:
        prim = (ctx or {}).get('primary') or {}
        vc = (ctx or {}).get('view_context') or {}
        name = str(prim.get('name') or '').strip()
        view = str(vc.get('view_id') or vc.get('view_title') or '').strip()
        summ = str(vc.get('summary') or vc.get('view_summary') or '').strip()
        data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
        keys = []
        for k, v in list(data.items())[:12]:
            if isinstance(v, (int, float, str)) and str(v).strip():
                keys.append(f"{k}={str(v)[:40]}")
        cuts = [str(c.get('name') or '') for c in ((ctx or {}).get('cuts') or []) if isinstance(c, dict)][:4]
        return {'name': name, 'view': view, 'summary': summ[:600], 'keys': keys, 'cuts': cuts}
    except Exception:
        return {'name': '', 'view': '', 'summary': '', 'keys': [], 'cuts': []}


def _candidates(text, ctx):
    """Library subjects the ask could mean: exact, token-contained and
    near-spelled, each with the products we hold."""
    out = []
    try:
        from migration import corpus_catalog as cc
    except Exception:
        try:
            import corpus_catalog as cc  # type: ignore
        except Exception:
            return out
    try:
        from . import sizing_lane as _szl
    except Exception:
        _szl = None
    phrases = []
    try:
        if _szl is not None:
            phrases += _szl.subjects(text, ctx)
    except Exception:
        pass
    # capitalised runs and quoted phrases
    phrases += [a or b for a, b in re.findall(r"\"([^\"]{2,60})\"|'([^']{2,60})'", text)]
    phrases += [m.group(0) for m in re.finditer(r"\b[A-Z][\w'&.+-]*(?:\s+(?:of|the|and|&|on|from)?\s*[A-Z0-9][\w'&.+-]*){0,5}", text)]
    # lowercase brand-ish tokens before an audience noun
    phrases += [m.group(1) for m in re.finditer(r"\b([a-z][\w'&.+-]{2,}(?:\s+[a-z][\w'&.+-]{2,}){0,2})\s+(?:customers?|users?|fans?|viewers?|buyers?|subscribers?|members?|shoppers?|owners?|bettors?|cu[a-z]*tomers?)\b", text, re.I)]
    seen = set()
    for ph in phrases:
        ph = ' '.join(str(ph or '').split()).strip(' ,.?!')
        if not ph or len(ph) < 3 or ph.lower() in seen:
            continue
        seen.add(ph.lower())
        names = []
        try:
            a = cc.anchors_for(ph, with_ledger=False)
            if a.get('facts'):
                names.append((a.get('subject') or ph, sorted(a.get('products') or [])))
            for r in (a.get('related') or [])[:3]:
                names.append((r.get('subject'), sorted({f.get('product') for f in (r.get('facts') or []) if f.get('product')})))
        except Exception:
            pass
        try:
            if _szl is not None:
                bare = _szl.strip_audience_nouns(ph)
                for cand in {ph, bare}:
                    fz = _szl.fuzzy_catalog_name(cc, cand, cutoff=0.84) if cand else ''
                    if fz and fz not in [n for n, _p in names]:
                        a2 = cc.anchors_for(fz, with_ledger=False)
                        names.append((fz, sorted(a2.get('products') or [])))
        except Exception:
            pass
        for n, prods in names:
            if n and n not in [o['subject'] for o in out]:
                out.append({'subject': n, 'products': prods, 'for': ph})
        if len(out) >= 10:
            break
    return out[:10]


def _who(user, uname):
    try:
        u = user if isinstance(user, dict) else {}
        bits = [f"username {uname}"]
        if u.get('company'):
            bits.append(f"company {u.get('company')}")
        if u.get('role'):
            bits.append(f"role {u.get('role')}")
        try:
            from . import shared_memory as _shm
            prefs = _shm.recall_preferences(uname)
            if prefs:
                bits.append('standing preferences: ' + ' | '.join(prefs[:6]))
        except Exception:
            pass
        return '; '.join(bits)
    except Exception:
        return f"username {uname}"


def _pending(uname, text, history):
    out = []
    try:
        from . import pending_intake as _pi
        from .host import host as _h
        if uname and _pi.waiting(_h.s3_client, _h.bucket, uname, text):
            out.append('a brief is waiting for approval in this thread')
    except Exception:
        pass
    try:
        from . import offers as _offers
        if _offers.pending_in_history(history):
            out.append('an offer is open on the previous turn')
    except Exception:
        pass
    try:
        prev = next((t for t in reversed(list(history or [])) if str(t.get('role') or '') != 'user'), None)
        if prev and (prev.get('meta') or {}).get('options'):
            labels = [str(o.get('label') or o) for o in (prev['meta']['options'] or [])][:5]
            out.append('the previous turn offered chips: ' + ' / '.join(labels))
    except Exception:
        pass
    return out


def _history_lines(history, n=8):
    lines = []
    for t in list(history or [])[-n:]:
        role = 'user' if str(t.get('role') or '') == 'user' else 'prometheus'
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        if txt:
            lines.append(f"{role}: {txt[:320]}")
    return lines


def build_prompt(text, ctx, history, user, uname):
    page = _page(ctx)
    cands = _candidates(text, ctx)
    examples = nearest_examples(text)
    parts = [f"WHO IS ASKING: {_who(user, uname)}"]
    if page['name'] or page['view']:
        pl = f"ON SCREEN: {page['view'] or 'dashboard'}"
        if page['name']:
            pl += f"; open profile or subject: {page['name']}"
        if page['cuts']:
            pl += f"; cuts open: {', '.join(page['cuts'])}"
        if page['keys']:
            pl += f"; figures on the page: {', '.join(page['keys'][:10])}"
        if page['summary']:
            pl += f"\nPAGE SUMMARY: {page['summary']}"
        parts.append(pl)
    else:
        parts.append("ON SCREEN: nothing open")
    pend = _pending(uname, text, history)
    if pend:
        parts.append('PENDING: ' + '; '.join(pend))
    if cands:
        parts.append('LIBRARY SUBJECTS THE ASK COULD MEAN: ' + '; '.join(
            f"{c['subject']} ({', '.join(c['products']) or 'profile'}) for \"{c['for']}\"" for c in cands))
    else:
        parts.append('LIBRARY SUBJECTS THE ASK COULD MEAN: none matched')
    hl = _history_lines(history)
    if hl:
        parts.append('LAST TURNS:\n' + '\n'.join(hl))
    if examples:
        parts.append('LABELED EXAMPLES (how earlier asks were meant):\n' + '\n'.join(
            f"- \"{e['text']}\" -> {e['intent']}" + (f" subjects {e['subjects']}" if e.get('subjects') else '') + (f" ({e['note']})" if e.get('note') else '')
            for e in examples))
    parts.append(f"THE ASK:\n{text}")
    return '\n\n'.join(parts), cands


# ------------------------------------------------------------- read
def _normalize(d, text, cands):
    if not isinstance(d, dict):
        return None
    intent = str(d.get('intent') or '').strip().lower().replace(' ', '_')
    if intent not in INTENTS:
        return None
    try:
        conf = float(d.get('confidence'))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    subs = []
    cand_names = {c['subject'].lower(): c['subject'] for c in (cands or [])}
    for s in (d.get('subjects') or [])[:6]:
        if isinstance(s, str):
            s = {'as_written': s}
        if not isinstance(s, dict):
            continue
        aw = ' '.join(str(s.get('as_written') or '').split())[:120]
        cat = ' '.join(str(s.get('catalog') or '').split())[:120]
        if cat and cat.lower() in cand_names:
            cat = cand_names[cat.lower()]
        elif cat and cat.lower() not in cand_names:
            cat = ''   # never invent a library subject
        if aw or cat:
            subs.append({'as_written': aw or cat, 'catalog': cat, 'kind': str(s.get('kind') or 'unknown')[:16]})
    flags = [str(f)[:60] for f in (d.get('flags') or []) if str(f).strip()][:8]
    return {'intent': intent, 'confidence': round(conf, 2), 'subjects': subs,
            'window': str(d.get('window') or '')[:80], 'platform': str(d.get('platform') or '')[:60],
            'wants': str(d.get('wants') or '')[:240], 'answer_shape': str(d.get('answer_shape') or '')[:20],
            'restate': str(d.get('restate') or '')[:240], 'flags': flags, 'text': text[:400]}


def read(text, ctx=None, history=None, user=None, uname='', *, claude=None, progress=None):
    """The reading for one ask, or None when it cannot be made."""
    t = ' '.join(str(text or '').split())
    if not t:
        return None
    try:
        if progress:
            progress('Reading your question')
    except Exception:
        pass
    try:
        user_prompt, cands = build_prompt(t, ctx, history, user, uname)
    except Exception:
        traceback.print_exc()
        return None
    try:
        if claude is None:
            from .host import host as _h
            claude = _h.claude_data if _h.has('claude_data') else standalone_claude()
            if claude is None:
                return None
        t0 = time.monotonic()
        data = claude(CHARTER, user_prompt, max_tokens=900, temperature=0.0, surface='reader') or {}
        ms = int((time.monotonic() - t0) * 1000)
    except Exception:
        traceback.print_exc()
        return None
    out = _normalize(data, t, cands)
    if out is None:
        return None
    out['ms'] = ms
    out['candidates'] = [c['subject'] for c in cands][:10]
    try:
        print(f"[reader] {out['intent']} ({out['confidence']}) subjects={[(s['as_written'], s['catalog']) for s in out['subjects']]} flags={out['flags']} {ms}ms :: {t[:80]!r}")
    except Exception:
        pass
    try:
        if progress and out.get('restate'):
            progress(out['restate'].rstrip('.'))
    except Exception:
        pass
    return out


def standalone_claude():
    """A (system, user, **kw) -> dict callable on the strongest model when
    no Flask host is bound (the nightly grade, scripts). None when the
    client is unavailable."""
    try:
        from claude_client import claude_reason_json
    except Exception:
        try:
            from migration.claude_client import claude_reason_json  # type: ignore
        except Exception:
            return None

    def _call(system, user, **kw):
        txt = claude_reason_json(system=system, user=user, max_tokens=int(kw.get('max_tokens', 900)),
                                 temperature=float(kw.get('temperature', 0.0)))
        try:
            return json.loads(txt)
        except Exception:
            m = re.search(r"\{.*\}", str(txt or ''), re.S)
            try:
                return json.loads(m.group(0)) if m else {}
            except Exception:
                return {}
    return _call


def subjects_of(reading, prefer_catalog=True):
    """The subject names a reading carries, library spelling first."""
    out = []
    for s in (reading or {}).get('subjects') or []:
        n = (s.get('catalog') if prefer_catalog else '') or s.get('as_written') or ''
        if n and n not in out:
            out.append(n)
    return out


def refers_to_page(reading):
    return 'refers_to_page' in ((reading or {}).get('flags') or []) or any(
        (s.get('as_written') or '').lower() in ('<page>', 'this', 'that', 'it', 'this audience') for s in (reading or {}).get('subjects') or [])


# ------------------------------------------------------------ steer
_PRODUCT_FOR = {
    'journey': ('a Digital Journey', 'shows the path from a social post, clip or search to the watch, purchase or ticketing visit, step by step', 'Build a Digital Journey'),
    'valuation': ('Brand Partnership IQ', 'values a talent or title against a brand: audience fit, earned media, brand lift and conversion lenses, one dollar figure', 'Value a brand partnership'),
    'campaign': ('Attribution IQ', "tracks a campaign's creators, posts and assets to the people who reached a ticketing site, a product page or a signup", 'Set up Attribution IQ'),
    'tracker': ('Subscriber IQ', 'tracks a title on a streaming service: accounts that watched, new and reactivated subscribers, how long they stayed', 'Pull a Subscriber IQ'),
    'build_profile': ('a Profile IQ profile', 'builds the audience of any person, title, brand or behavior with demographics and the brands, platforms and talent they over-index on', 'Run a profile'),
    'cut': ('a cut', 'splits a profile we hold by gender, age, geography or behavior', 'Cut the profile'),
    'deck': ('a deck', 'turns any read or profile on screen into slides', 'Build a deck'),
    'analyze': ('an answer in the chat', 'reads the library and what we know and answers right here, with the figures', 'Ask it'),
}


def capability_reply(reading):
    """A can-we answer shaped by what the Reader saw the person wanting."""
    wants = str((reading or {}).get('wants') or '').strip()
    low = wants.lower()
    key = 'analyze'
    for k, words in (('campaign', ('creator', 'campaign', 'asset', 'attribut')), ('journey', ('journey', 'path', 'clip', 'post')),
                     ('valuation', ('partnership', 'sponsor', 'worth', 'value')), ('tracker', ('subscri', 'churn', 'retention', 'signup')),
                     ('cut', ('cut', 'split', 'female', 'male', 'age')), ('deck', ('deck', 'slides')),
                     ('build_profile', ('audience', 'profile', 'fans', 'buyers', 'who'))):
        if any(w in low for w in words):
            key = k
            break
    name, what, chip = _PRODUCT_FOR[key]
    reply = (f"Yes. {name[0].upper() + name[1:]} {what}. "
             + (f"From what you describe ({wants.rstrip('.')}), that is the one. " if wants else '')
             + "Tell me the subject and the window and I will line it up; it comes back as a brief to approve before anything is charged.")
    return {'reply': reply, 'followups': [chip]}


def steer(reading, decision, *, has_ctx=False, min_conf=0.7):
    """(decision, bind_subject) when a confident reading should pick the
    surface; None to keep the router's decision. Only overrides the
    analyze / interpret / deck choice; armed steps never reach here."""
    try:
        r = reading if isinstance(reading, dict) else None
        if not r or float(r.get('confidence') or 0) < min_conf:
            return None
        intent = r.get('intent')
        cur = str((decision or {}).get('surface') or '')
        want = None
        if intent in ANALYZE_SET or intent in ('compare', 'pricing', 'definition'):
            want = 'analyze'
        elif intent in BUILD_SET:
            want = 'interpret'
        elif intent == 'deck':
            want = 'deck'
        if not want or want == cur:
            # the surface already agrees; still hand over a single library subject
            bind = ''
            if want == 'analyze' and intent in ('analyze',) and not refers_to_page(r):
                cats = [s['catalog'] for s in r.get('subjects') or [] if s.get('catalog')]
                if len(cats) == 1:
                    bind = cats[0]
            return (dict(decision, reason=f"{(decision or {}).get('reason') or 'router'}+reader:{intent}"), bind) if bind else None
        new = dict(decision or {}, surface=want, reason=f"reader:{intent}", client_hint=None)
        bind = ''
        if want == 'analyze' and intent == 'analyze' and not refers_to_page(r):
            cats = [s['catalog'] for s in r.get('subjects') or [] if s.get('catalog')]
            if len(cats) == 1:
                bind = cats[0]
        print(f"[reader] steer {cur or '?'} -> {want} ({intent}, {r.get('confidence')})")
        return new, bind
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------- grade
# The route family a reading's intent should end in (the ask log's
# route / outcome vocabulary); several routes may satisfy one intent.
ROUTE_FAMILY = {
    'exists': ('catalog_lookup', 'subiq_lookup'),
    'size': ('sizing', 'catalog_lookup', 'incidence', 'sample_check'),
    'status': ('status_lookup', 'deliverable_lookup'),
    'run_pending': ('approve', 'command', 'run', 'profile_build', 'approved'),
    'build_profile': ('profile_build', 'interpret', 'new_build', 'existing_match', 'derive_cut', 'time_shifted_refresh', 'cut_needs_parent'),
    'cut': ('derive_cut', 'profile_build', 'cut_needs_parent'),
    'journey': ('journey', 'jiq', 'digital_journey', 'intake'),
    'valuation': ('bpiq', 'brand_partnership', 'intake'),
    'campaign': ('aiq', 'attribution', 'intake'),
    'tracker': ('subscriber_iq', 'subiq', 'profile_build'),
    'analyze_page': ('page_analysis', 'reasoned_metrics', 'analyze', 'screen_bind', 'open_screen_confirm'),
    'analyze': ('page_analysis', 'reasoned_metrics', 'analyze', 'ledger_replay', 'catalog_lookup', 'cohort_churn'),
    'compare': ('page_analysis', 'reasoned_metrics', 'analyze', 'compare'),
    'capability': ('capability', 'capability_question', 'cant_do'),
    'command': ('command', 'deliverable_lookup', 'csv_download', 'send_file', 'design_request', 'open_screen'),
    'deck': ('deck',),
    'definition': ('product_fact', 'kpi_definition', 'sample_size', 'page_analysis', 'methodology'),
    'pricing': ('pricing',),
    'decline': ('offer_declined', 'short_reply', 'offer_answer'),
    'chat': ('short_reply', 'chat'),
}


def agrees(intent, route, outcome=''):
    fam = ROUTE_FAMILY.get(str(intent or ''), ())
    r = str(route or '').lower()
    o = str(outcome or '').lower()
    return any(f in r or f in o for f in fam)


def grade_log_rows(rows):
    """{'n', 'agree', 'disagree': [...]} over ask-log rows that carry a
    reading (extra.reading)."""
    n = agree = 0
    dis = []
    for r in rows or []:
        rd_ = ((r.get('extra') or {}).get('reading') or {}) if isinstance(r.get('extra'), dict) else {}
        if not rd_.get('intent'):
            continue
        n += 1
        if agrees(rd_.get('intent'), r.get('route'), r.get('outcome')):
            agree += 1
        else:
            dis.append({'user': r.get('user'), 'question': str(r.get('question') or '')[:120], 'intent': rd_.get('intent'),
                        'confidence': rd_.get('confidence'), 'route': r.get('route'), 'outcome': r.get('outcome')})
    return {'n': n, 'agree': agree, 'disagree': dis[:25]}


INTENT_FAMILY = {
    'analyze': 'answer', 'analyze_page': 'answer', 'compare': 'answer', 'definition': 'answer',
    'build_profile': 'build', 'cut': 'build',
}


def same_family(a, b):
    return bool(a) and bool(b) and (a == b or INTENT_FAMILY.get(a, a) == INTENT_FAMILY.get(b, b))


def grade_bank(claude=None, limit=60, bank=None):
    """Run the labeled bank through the Reader with each example held
    out; {'n', 'correct', 'family', 'misses'}. `correct` is the exact
    intent, `family` counts a reading that lands on the same surface
    (analyze / analyze_page / compare / definition; build / cut). One
    model call per example; the nightly runs it where the key lives."""
    bank = bank or load_bank()
    ex = [e for e in (bank.get('examples') or []) if e.get('text') and e.get('intent') in INTENTS][-int(limit):]
    n = correct = family = 0
    misses = []
    for e in ex:
        hold = {'examples': [x for x in bank.get('examples') or [] if x is not e]}
        try:
            r = read(e['text'], {}, [], {}, 'grader', claude=claude) if claude else None
            if r is None:
                r = _read_with_bank(e['text'], hold)
        except Exception:
            r = None
        n += 1
        got = (r or {}).get('intent')
        if got == e['intent']:
            correct += 1
            family += 1
        elif same_family(got, e['intent']):
            family += 1
        else:
            misses.append({'text': e['text'][:120], 'expected': e['intent'], 'got': got, 'confidence': (r or {}).get('confidence')})
    return {'n': n, 'correct': correct, 'family': family, 'misses': misses[:30]}


def _read_with_bank(text, bank):
    """read() with a held-out bank (the example itself never in view)."""
    global _bank_cache
    saved = dict(_bank_cache)
    with _bank_lock:
        _bank_cache.update({'ts': time.time(), 'doc': bank})
    try:
        return read(text, {}, [], {}, 'grader')
    finally:
        with _bank_lock:
            _bank_cache.update(saved)


def render_grade_lines(log_grade, bank_grade):
    lines = ['Reader (one understanding step before routing):']
    if log_grade and log_grade.get('n'):
        pct = 100.0 * log_grade['agree'] / max(log_grade['n'], 1)
        lines.append(f"- yesterday: {log_grade['agree']} of {log_grade['n']} readings agreed with where the ask ended up ({pct:.0f}%)")
        for d in (log_grade.get('disagree') or [])[:8]:
            lines.append(f"  - {d['user']}: \"{d['question']}\" read as {d['intent']} ({d['confidence']}) but routed {d['route']} / {d['outcome']}")
    else:
        lines.append('- yesterday: no readings on the log yet')
    if bank_grade and bank_grade.get('n'):
        pct = 100.0 * bank_grade['correct'] / max(bank_grade['n'], 1)
        fam = bank_grade.get('family', bank_grade['correct'])
        lines.append(f"- labeled bank: {bank_grade['correct']} of {bank_grade['n']} read exactly ({pct:.0f}%), {fam} land on the right surface ({100.0 * fam / max(bank_grade['n'], 1):.0f}%)")
        for m in (bank_grade.get('misses') or [])[:8]:
            lines.append(f"  - \"{m['text']}\" expected {m['expected']}, got {m['got']} ({m['confidence']})")
    return lines
