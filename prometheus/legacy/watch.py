"""Prometheus watch, attribution and context helpers (2026-10-06, third
family out of chat.py).

Leaf helpers with no routes: the ask-log user resolver, the catalog
and user prompt blocks, the real-time watch flag, the held-reply
promise, the repeat-guard options, and the welcome status line. They
read the host through ``_H`` and the legacy chat module through
``_C``; see threads.py for the pattern. Behavior unchanged.
"""
import json
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from flask import session

from prometheus.legacy import H as _H, C as _C  # noqa: E402

__all__ = ['_PM_WATCH_FLAGGED', '_PM_USER_BLOCK_CACHE', '_PM_USER_BLOCK_LOCK', '_pm_user_block', '_pm_catalog_block', '_pm_ask_log_user', '_pm_probe_caller', '_pm_is_probe_user', '_PM_COMMON_IDENTITY_WORDS', '_pm_thread_confirmed_page', '_pm_watch_flag', '_pm_record_held_reply', '_pm_gate_options', '_pm_open_status_line', '_pm_price_table', '_pm_usd', '_pm_usd_label', '_pm_money_symbol', '_pm_safe_user', '_pm_s3_json', '_pm_s3_put_json', '_PM_REPORT_ASK_RE', '_pm_looks_report_ask', '_pm_is_viewership_series_ask', '_pm_is_consumption_count_ask', '_pm_is_viewership_ask', '_pm_viewership_verb', '_pm_window_years', '_pm_viewership_read_price', '_pm_panel_price_label', '_pm_consumption_subject', '_pm_pending_q_tokens', '_pm_stash_pending_question']


_PM_WATCH_FLAGGED = frozenset({'clarified_repeat', 'empty', 'faulted', 'error',
                               'mismatched', 'failed_by_user'})


_PM_USER_BLOCK_CACHE = {}


_PM_USER_BLOCK_LOCK = threading.Lock()


def _pm_user_block(username):
    """ABOUT THIS USER block for the analysis prompts (2026-10-06,
    audit item 8). Built off-thread on first use (the ask-log scan is
    slow) and cached an hour per user; '' until it is ready."""
    uname = str(username or '').strip()
    if not uname:
        return ''
    now = time.time()
    with _PM_USER_BLOCK_LOCK:
        hit = _PM_USER_BLOCK_CACHE.get(uname)
        if hit and now - hit[0] < 3600:
            return hit[1]
        if hit and hit[1] == '__building__':
            return ''
        _PM_USER_BLOCK_CACHE[uname] = (now, '__building__')

    def _run():
        try:
            import prometheus_memory as _pmm
            prof = _pmm.user_profile(uname, users_doc=(_H.load_users() or {}))
            blk = _pmm.user_block(prof)
        except Exception:
            traceback.print_exc()
            blk = ''
        with _PM_USER_BLOCK_LOCK:
            _PM_USER_BLOCK_CACHE[uname] = (time.time(), blk)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass
    return ''


def _pm_catalog_block(subject, window=None, extra_subjects=()):
    """The corpus catalog's binding figures for a subject (2026-10-05,
    Jenna): every number Profile IQ, Digital Journey IQ, Attribution IQ,
    Brand Partnership IQ or an earlier chat already published on it.
    One cached dict hit plus one small page read; '' on any trouble."""
    names = [str(subject or '').strip()] + [str(x or '').strip() for x in extra_subjects]
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return ''
    try:
        from migration import corpus_catalog as _cc
        parts = []
        for n in names[:3]:
            anchors = _cc.anchors_for(n, window=window, with_ledger=False)
            blk = _cc.anchors_block(anchors)
            if blk:
                parts.append(blk)
        return '\n\n'.join(parts)
    except Exception:
        traceback.print_exc()
        return ''


_PM_PROBE_UA_RX = re.compile(r'canary|smoke|regression|probe', re.I)


def _pm_probe_caller():
    """The caller label when the current request is a probe: a canary,
    smoke, regression or operator probe driving the real ask path.
    Signalled by the `X-Prometheus-Caller` header or a user agent that
    names itself as one. '' for a real user's request, or outside a
    request. A probe label wins over the session user (2026-10-06:
    an operator probe run under the admin session was mailed to Jenna
    and Liz as 'Jenna Menking asked a question'; the copies go to
    Jenna with Liz and Jessie on BCC since 2026-10-07)."""
    try:
        from flask import has_request_context as _hrc, request as _rq
        if not _hrc():
            return ''
        caller = str(_rq.headers.get('X-Prometheus-Caller') or '').strip()
        if caller:
            return re.sub(r'[^a-z0-9_-]+', '', caller.lower())[:40] or 'probe'
        if _PM_PROBE_UA_RX.search(str(_rq.headers.get('User-Agent') or '')):
            return 'probe'
    except Exception:
        pass
    return ''


def _pm_is_probe_user(username):
    """True for the synthetic labels a probe is logged under."""
    return str(username or '').strip().lower().startswith('canary')


def _pm_ask_log_user(default='unknown'):
    """The user label an ask is logged under (2026-10-06). A probe
    caller first (so canary, smoke and operator probes never read as
    a real user even under a session), then the session user, then
    the API-key / job owner the route set on g, then a synthetic
    caller marker, else `default`."""
    _probe = _pm_probe_caller()
    if _probe:
        return 'canary:' + _probe
    try:
        u = session.get('username')
        if u:
            return str(u)
    except Exception:
        pass
    try:
        from flask import g as _g, request as _rq
        u = getattr(_g, '_pm_ask_user', None) or getattr(_g, '_pm_api_key_owner', None)
        if u:
            return str(u)
        ua = str(_rq.headers.get('User-Agent') or '')
        caller = str(_rq.headers.get('X-Prometheus-Caller') or '')
        if caller:
            return 'canary:' + re.sub(r'[^a-z0-9_-]+', '', caller.lower())[:40]
        if 'canary' in ua.lower() or 'smoke' in ua.lower() or 'regression' in ua.lower():
            return 'canary'
        if getattr(_g, '_pm_api_key_id', None):
            return 'apikey:' + str(getattr(_g, '_pm_api_key_id'))[:24]
    except Exception:
        pass
    return default


def _pm_watch_flag(user, question, route, outcome, extra=None):
    """Real-time watch feed (2026-10-06): a flagged ask (repeated
    clarify, empty / faulted reply, error, a build drafted for a task,
    a user rejecting the previous answer) lands in
    system/ops/pm_watch_recent.json, bounded to the last 80, which the
    admin System Status tile reads. Off the request thread; never
    raises."""
    try:
        sig = str((extra or {}).get('user_signal') or '')
        if outcome not in _PM_WATCH_FLAGGED and sig not in ('rejected', 'wrong', 'no'):
            return
        if str(user or '').startswith('canary') or str(user or '') in ('', 'unknown', 'replay'):
            return
    except Exception:
        return

    def _run():
        try:
            key = 'system/ops/pm_watch_recent.json'
            doc = _pm_s3_json(key, {}) or {}
            items = [x for x in (doc.get('items') or []) if isinstance(x, dict)]
            items.append({'ts': _C._pm_iso_now(), 'user': str(user or '')[:60],
                          'route': str(route or '')[:40], 'outcome': str(outcome or '')[:30],
                          'signal': sig[:20], 'question': str(question or '')[:200]})
            doc['items'] = items[-80:]
            doc['updated_at'] = _C._pm_iso_now()
            _pm_s3_put_json(key, doc)
        except Exception:
            traceback.print_exc()
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


def _pm_record_held_reply(username, question, told, reason, kind):
    """A held or repeated reply opens a task with a due time (2026-10-06,
    dead ends): system/ops/held_replies/<day>/<ts>_<id>.json, read by the
    ops watch so an unanswered promise is flagged, never forgotten."""
    try:
        import hashlib as _hl
        now = datetime.now(timezone.utc)
        rid = _hl.sha1(f"{username}|{question}|{now.isoformat()}".encode()).hexdigest()[:10]
        key = (f"system/ops/held_replies/{now.strftime('%Y-%m-%d')}/"
               f"{now.strftime('%H%M%S')}_{rid}.json")
        doc = {'id': rid, 'user': username or '', 'question': str(question or '')[:400],
               'told': str(told or '')[:400], 'reason': str(reason or '')[:400], 'kind': kind,
               'opened_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'due_by': (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
               'status': 'open'}
        _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=key,
                                Body=json.dumps(doc).encode('utf-8'),
                                ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _pm_gate_options(history):
    """The choices the previous agent turn offered (chip labels, or the
    names in a 'Do you mean for X, or Y?' line), minus utility chips."""
    try:
        prev = None
        for t in reversed(history or []):
            if isinstance(t, dict) and str(t.get('role') or '') == 'agent':
                prev = t
                break
        if not prev:
            return []
        out = []
        meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
        mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
        for o in (mc.get('options') or meta.get('options') or []):
            lbl = str((o.get('label') if isinstance(o, dict) else o) or '').strip()
            if lbl and not re.match(r'^(?:something else|email me|send me|cancel|no\b|none\b|skip)', lbl, re.I):
                out.append(lbl)
        if not out:
            m = re.match(r'^\s*Do you mean (?:for )?(.+?)(?:,? or (.+?))?\s*\?\s*$',
                         str(prev.get('text') or ''), re.I | re.S)
            if m:
                out = [g.strip() for g in (m.group(1), m.group(2)) if g and g.strip()]
        return out[:4]
    except Exception:
        return []


def _pm_open_status_line(user, hours=72):
    """One plain sentence for the welcome bubble. '' when nothing to say."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return ''
    user_id = (user.get('email') or user.get('username') or '').strip()
    if not user_id:
        return ''
    import requests as _requests
    try:
        resp = _requests.get(f"{_H.SYNTH_QUEUE_URL}/synth/list",
                             params={'user': user_id, 'limit': 40},
                             headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=8)
        raw = resp.json() if resp.status_code == 200 else []
    except Exception:
        return ''
    if not isinstance(raw, list):
        return ''
    cutoff = time.time() - hours * 3600

    def _ts(doc):
        for k in ('completed_at', 'updated_at', 'requested_at'):
            v = doc.get(k)
            if v:
                try:
                    return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
                except Exception:
                    continue
        return 0

    done, running, reused = [], [], []
    for doc in raw:
        if not isinstance(doc, dict):
            continue
        st = str(doc.get('status') or '')
        subj = str(doc.get('subject') or doc.get('profile_name') or '').strip()
        if st in ('complete', 'completed', 'done'):
            if _ts(doc) >= cutoff and subj:
                done.append(subj)
                if doc.get('existing_window_match') or doc.get('quarter_cuts_reused'):
                    reused.append(subj)
        elif st not in ('failed', 'error', 'canceled', 'cancelled', ''):
            running.append(subj or 'a build')
    parts = []
    if done:
        shown = done[:3]
        more = len(done) - len(shown)
        parts.append("Since you were last here, " + ('1 build finished' if len(done) == 1 else f"{len(done)} builds finished")
                     + ': ' + ', '.join(shown) + (f" and {more} more" if more > 0 else '') + '.')
    if reused:
        parts.append(('One of them was' if len(reused) == 1 else f"{len(reused)} of them were")
                     + ' already on the dashboard for that window, so I reused the file instead of rebuilding it.')
    if running:
        parts.append(('1 build is' if len(running) == 1 else f"{len(running)} builds are") + ' still in motion.')
    return ' '.join(parts)


# A single ordinary English word is never a subject identity on its own
# (2026-10-06: "the best demo overlap with the Avid tier" bound the
# catalog's 'Best of the Best - Avid Fan' on the word 'best' and the
# read shipped under the wrong subject). The ALL-CAPS initialism bypass
# (BET, CNN) still applies; a multi-token name is unaffected.
_PM_COMMON_IDENTITY_WORDS = frozenset({
    'best', 'good', 'great', 'new', 'old', 'big', 'little', 'small', 'top',
    'first', 'last', 'love', 'life', 'home', 'house', 'game', 'games',
    'music', 'news', 'world', 'time', 'day', 'night', 'people', 'man',
    'woman', 'girl', 'boy', 'kids', 'family', 'friends', 'money', 'work',
    'school', 'city', 'country', 'war', 'star', 'stars', 'light', 'dark',
    'black', 'white', 'red', 'blue', 'green', 'gold', 'one', 'two', 'three',
    'real', 'true', 'free', 'happy', 'bad', 'mad', 'hot', 'cold', 'wild',
    'young', 'party', 'power', 'future', 'american', 'america', 'united',
    'states', 'modern', 'super', 'team', 'club', 'project', 'story',
    'stories', 'land', 'king', 'queen', 'lost', 'found', 'dead', 'alive',
    'fire', 'water', 'earth', 'sun', 'moon', 'summer', 'winter', 'spring',
    'fall', 'live', 'living', 'daily', 'weekly', 'morning', 'tonight',
    'today', 'tomorrow', 'open', 'next', 'final', 'late', 'early', 'high',
    'low', 'long', 'short', 'more', 'most', 'less', 'only', 'other',
    'thing', 'things', 'place', 'street', 'road', 'way', 'line', 'point',
    'simple', 'plain', 'perfect', 'better', 'greatest', 'original'})


def _pm_thread_confirmed_page(history, page):
    """True when this thread already answered the open-screen confirm
    for this page ("Yes, {page}" as a user turn). The user said it once;
    asking again on every question in the same thread is noise, not
    caution (2026-10-06, Emmet's thread: four confirms in ten minutes on
    the same profile)."""
    try:
        want = _H._normalize_for_match(f"yes {page}")
        for t in reversed([h for h in (history or []) if isinstance(h, dict)]):
            if str(t.get('role') or '').lower() != 'user':
                continue
            if _H._normalize_for_match(str(t.get('text') or '')) == want:
                return True
    except Exception:
        pass
    return False


def _pm_price_table():
    """Dollar prices by decision tier for the signed-in caller, so the
    brief card prints money, never credits (Jenna 2026-10-07). None
    when the host cannot price (the widget then prints nothing)."""
    try:
        tab = _H._v1_price_table_for(session.get('username'))
        if not isinstance(tab, dict):
            return None
        out = {str(k): float(v) for k, v in tab.items()
               if isinstance(v, (int, float))}
        return out or None
    except Exception:
        return None


def _pm_money_symbol(username=None):
    """The seat's currency symbol ($ or £); East Tree Media and Omaze
    see £ everywhere (Jenna 2026-10-08)."""
    try:
        return _H._session_money_symbol(username)
    except Exception:
        return '$'


def _pm_usd_label(v, username=None):
    """$300 / £1,000 / £12.50 in the seat's currency - whole units when
    they are whole."""
    try:
        v = float(v)
    except Exception:
        return ''
    sym = _pm_money_symbol(username)
    return f"{sym}{v:,.0f}" if abs(v - round(v)) < 0.009 else f"{sym}{v:,.2f}"


def _pm_usd(tool_key, fallback_usd, username=None):
    """Dollar price of one tool for the caller (company rates through
    the billing subject), as a float. Standard pricing, never credits
    (Jenna 2026-10-07). Falls back to the published rate."""
    try:
        import wallet as _w
        uname = (username or '').strip() or (session.get('username') or '').strip()
        subject = None
        if uname:
            data = _H.load_users()
            user = (data.get('users') or {}).get(uname) or {}
            if user:
                subject, _k, _n = _w.resolve_billing_subject(user, data)
        v = float(_w.tool_price_usd(tool_key, subject=subject) or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return float(fallback_usd)


# Small S3 JSON helpers (moved from chat.py 2026-10-08 to keep the
# legacy module under its line ratchet). Behavior unchanged.
def _pm_safe_user(username):
    return ''.join(c for c in (username or 'anon')
                   if c.isalnum() or c in '-_.@').lower()


def _pm_s3_json(key, default):
    try:
        obj = _H.s3_client.get_object(Bucket=_H.S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        if 'NoSuchKey' not in str(e):
            print(f"[synth-chat] read failed {key}: {e}")
        return default


def _pm_s3_put_json(key, obj):
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=key,
        Body=json.dumps(obj, indent=2).encode('utf-8'),
        ContentType='application/json')


_PM_REPORT_ASK_RE = re.compile(
    r'\b(report|deck|one.?pager|write.?up|whitepaper|whitesheet'
    r'|full (analysis|read)|research (report|read))\b', re.I)


def _pm_looks_report_ask(text):
    """True when the ask wants a put-together deliverable (keeps the
    2026-09-14 priced research-report flow); False for plain questions,
    which take the 2026-09-24 build-first flow. A metric asked over
    time ("monthly consumption for The Office", "viewership month by
    month") is a put-together read too: a profile cannot answer it
    (2026-10-08, East Tree Media)."""
    t = str(text or '')
    if _PM_REPORT_ASK_RE.search(t):
        return True
    if _pm_is_consumption_count_ask(t):
        return True
    try:
        from prometheus.understand import _TIME_SERIES_RX
        return bool(_TIME_SERIES_RX.search(t))
    except Exception:
        return False


# Viewership over time (2026-10-08 Jenna, verbatim: "viewership asks is
# always 500 per year in the company's requested currency"). A monthly
# or over-time read of viewers / hours / consumption is priced per year
# of window (quantity = years), never the un-priced $550.
_PM_VIEWERSHIP_METRIC_RX = re.compile(
    r"\b(?:viewership|viewing|viewers?|watch(?:ing)?\s*time|hours watched|"
    r"minutes watched|watch(?:ed|es)?|viewed|views|consumption|streams?|"
    r"streamed|streaming|plays|played|listens|listened|listenership|"
    r"listeners?|audience)\b", re.I)
_PM_YEAR_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                  'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}


# A consumption COUNT is a viewership ask too (Jenna 2026-10-08: "how
# many people read the walsh family book series in the us last year?"
# drew a build offer; it "would just go towards a viewership charge").
_PM_CONSUME_VERB_RX = (
    r"(?:read|reads|watch|watched|watches|listen|listened|listens|"
    r"stream|streamed|streams|play|played|plays|view|viewed|views|"
    r"tuned in|binged?|bought|buy|buys|purchased|downloaded|"
    r"subscribed?|subscribes|follow|followed|follows)")
_PM_CONSUME_COUNT_RX = re.compile(
    r"\bhow many\b[^.?!]{0,60}\b" + _PM_CONSUME_VERB_RX + r"\b"
    r"|\b(?:readership|viewership|listenership|audience size|"
    r"total (?:viewers|readers|listeners|players|audience)|"
    r"number of (?:viewers|readers|listeners|players|streams|plays|"
    r"people who " + _PM_CONSUME_VERB_RX + r"))\b"
    r"|\bhow (?:big|large) (?:is|was) (?:the )?(?:audience|readership|"
    r"viewership|listenership)\b", re.I)
_PM_VERB_LABELS = (
    (r"\bread(?:s)?\b", "read"), (r"\blisten", "listened to"),
    (r"\bplay", "played"), (r"\bstream", "streamed"),
    (r"\bbinge", "binged"), (r"\b(?:bought|buy|buys|purchased)\b", "bought"),
    (r"\bdownload", "downloaded"), (r"\bsubscribe", "subscribed to"),
    (r"\bfollow", "followed"), (r"\b(?:watch|view|tuned)", "watched"))


def _pm_is_viewership_series_ask(text):
    """True for a viewership metric asked over time ("monthly
    consumption for The Office", "viewership month by month")."""
    t = str(text or '')
    try:
        from prometheus.understand import _TIME_SERIES_RX
        if not _TIME_SERIES_RX.search(t):
            return False
    except Exception:
        return False
    return bool(_PM_VIEWERSHIP_METRIC_RX.search(t))


def _pm_is_consumption_count_ask(text):
    """True for "how many people read / watched / listened to X" and
    readership / viewership / audience-size asks."""
    return bool(_PM_CONSUME_COUNT_RX.search(str(text or '')))


def _pm_is_viewership_ask(text):
    """A viewership ask: a consumption count or a consumption metric
    over time. Priced at 500 per year of window."""
    return _pm_is_viewership_series_ask(text) or _pm_is_consumption_count_ask(text)


def _pm_viewership_verb(text):
    """The consumption verb the ask used, for the offer copy
    ("how many people read it"). 'watched' when none is named."""
    t = str(text or '')
    for rx, label in _PM_VERB_LABELS:
        if re.search(rx, t, re.I):
            return label
    return 'watched'


def _pm_window_years(text):
    """Years of window the ask names; 1 for the trailing-12 default.
    "last three years" -> 3; "2023 to 2025" -> 3 (calendar years,
    inclusive); "since 2024" -> through this year; "18 months" -> 2."""
    t = str(text or '').lower()
    m = re.search(r"\b(\d{1,2}|" + '|'.join(_PM_YEAR_WORDS) + r")\s*(?:-|\s)?\s*(?:years?|yrs?)\b", t)
    if m:
        tok = m.group(1)
        n = int(tok) if tok.isdigit() else _PM_YEAR_WORDS.get(tok, 1)
        return max(1, min(n, 10))
    m = re.search(r"\b(\d{1,3})\s*months?\b", t)
    if m:
        months = int(m.group(1))
        return max(1, min(-(-months // 12), 10))
    m = re.search(r"\b(20\d\d)\s*(?:to|through|thru|-|until|and)\s*(20\d\d)\b", t)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi >= lo:
            return max(1, min(hi - lo + 1, 10))
    m = re.search(r"\bsince\s+(20\d\d)\b", t)
    if m:
        return max(1, min(datetime.now(timezone.utc).year - int(m.group(1)) + 1, 10))
    # "last year", "this year", "in 2025", "12 months": one year.
    return 1


def _pm_viewership_read_price(text, username=None):
    """(label, credits, years) for a viewership-over-time ask in the
    seat's currency, or None when the ask is not one. 500 per year
    (live table key viewership_read), symbol from the seat."""
    if not _pm_is_viewership_ask(text):
        return None
    years = _pm_window_years(text)
    each = _pm_usd('viewership_read', 500.0, username=username)
    try:
        each = float(each or 500.0)
    except (TypeError, ValueError):
        each = 500.0
    try:
        credits_each = int(_H.get_credit_cost('viewership_read') or 5)
    except Exception:
        credits_each = 5
    return _pm_usd_label(each * years, username), max(credits_each, 1) * years, years


def _pm_panel_price_label(username):
    """User-facing price for the Prometheus research report, plus the
    credit count the charge will consume. Internal-credit holders see
    the credit count; a paying customer whose credits will not cover
    it sees the dollar price the wallet will absorb ($500 default,
    admin-tunable in the billing panel). Never raises."""
    credits_price = _H.CREDITS_PANEL_REPORT
    try:
        credits_price = int(_H.get_credit_cost('panel_report')
                            or _H.CREDITS_PANEL_REPORT)
    except Exception:
        pass
    # Standard pricing, never credits (Jenna 2026-10-07): everyone sees
    # the dollar price; the charge still consumes the internal units.
    usd = _pm_usd('panel_report', float(getattr(_H, 'PANEL_REPORT_USD', 500.0) or 500.0),
                  username=username)
    return _pm_usd_label(usd), credits_price


def _pm_pending_q_tokens(s):
    return {w for w in _H._normalize_for_match(s).split()
            if w and w not in _C._PM_BASE_GENERIC_TOKENS}


_PM_CONSUME_SUBJECT_RX = re.compile(
    r"\bhow (?:many|much)\b[^.?!]{0,50}?\b" + _PM_CONSUME_VERB_RX +
    r"\s+(?:to\s+)?(?:the\s+)?(?P<subj>.+?)"
    r"(?=\s+(?:in|on|across|within|during|over|since|last|this|past|"
    r"each|every|per|monthly|weekly|yearly|by|between|from|for|"
    r"so far|to date)\b|\s*[?.!]|$)", re.I)
_PM_SUBJ_SMALL_WORDS = {'a', 'an', 'the', 'of', 'and', 'or', 'in', 'on',
                        'for', 'to', 'at', 'by', 'with', 'vs', 'de', 'la'}


def _pm_consumption_subject(text):
    """The object of a consumption-count ask, title-cased when the user
    typed it in lowercase: "how many people read the walsh family book
    series in the us last year" -> "Walsh Family Book Series". '' when
    the ask is not shaped that way or the object is only ordinary
    words."""
    m = _PM_CONSUME_SUBJECT_RX.search(str(text or ''))
    if not m:
        return ''
    raw = re.sub(r"\s+", " ", m.group('subj')).strip(" ,;:'\"")
    # The regex swallowed a lowercase "the"; a capitalized "The" the
    # user typed is part of the title ("The Pitt") and is restored.
    head = str(text or '')[:m.start('subj')]
    if re.search(r"\bThe\s*$", head):
        raw = 'The ' + raw
    elif re.search(r"\bthe\s*$", head) and len(raw.split()) == 1:
        raw = 'The ' + raw           # "the office" -> The Office
    if not raw or len(raw) > 90:
        return ''
    words = raw.split()
    out = []
    for i, w in enumerate(words):
        if w.lower() in _PM_SUBJ_SMALL_WORDS and i not in (0, len(words) - 1):
            out.append(w.lower())
        elif w.isupper() and len(w) <= 5:
            out.append(w)                      # acronyms stay
        elif any(ch.isupper() for ch in w[1:]):
            out.append(w)                      # user casing stays (iPhone)
        else:
            out.append(w[:1].upper() + w[1:])
    subj = ' '.join(out)
    try:
        from prometheus import referents as _refs
        if not _refs.plausible_subject(subj):
            return ''
    except Exception:
        pass
    return subj


def _pm_stash_pending_question(username, subject, question,
                               thread_id=None):
    """Remember the question that triggered a build-first offer so the
    completed run can answer it automatically (2026-09-24 Jenna). Kept
    per user, newest first, capped at 5, 7-day expiry. The thread the
    question came from rides along (2026-10-06) so the server-side
    follow-through (prometheus.pending_answers) answers on that thread
    even when the tab is closed."""
    uname = str(username or '').strip().lower()
    if not uname or not subject or not question:
        return
    import time as _t
    if thread_id is None:
        thread_id = str(getattr(_C._PM_REQ_THREAD, 'tid', '') or '')

    def _mut(doc):
        doc = doc if isinstance(doc, dict) else {}
        now = _t.time()
        lst = [e for e in (doc.get(uname) or [])
               if isinstance(e, dict)
               and now - float(e.get('ts') or 0) < 7 * 24 * 3600]
        lst = [e for e in lst
               if str(e.get('question') or '') != str(question)]
        lst.insert(0, {'subject': str(subject)[:160],
                       'question': str(question)[:500], 'ts': now,
                       'thread_id': str(thread_id or '')[:64]})
        doc[uname] = lst[:5]
        return doc
    try:
        _H._s3_json_cas_update(_H.S3_BUCKET, _C._PM_PENDING_Q_S3_KEY, _mut,
                            default=dict,
                            log_name='pm_pending_questions')
    except Exception:
        traceback.print_exc()
