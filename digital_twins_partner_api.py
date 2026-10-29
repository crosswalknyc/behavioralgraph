"""Screenings for key callers: /api/v1/screenings (2026-10-28).

A studio tests a cut, a trailer, a title set, key art, a script or a
concept from its own tools, the way the partner API builds a profile:
a prompt and a file in, a status and the results out.

  POST /api/v1/screenings               open the test on the twin the prompt names, add the file or the
                                        words, start the watch, take the price. Returns a screening id.
  GET  /api/v1/screenings/<id>          status and, once the room has watched, the results.
  GET  /api/v1/screenings/<id>/report.pdf | report.pptx | dial.png   the report and the dial chart.
  GET  /api/v1/screenings               the caller's screenings, newest first.

Auth: ``X-Crosswalk-API-Key`` (the same key as the build endpoints; the
``build`` scope), or a dashboard session. Every string that leaves
here passes the partner belt (``prometheus.guards.PARTNER_BANNED_TOKENS``):
a field that carries an internal term is dropped, never rewritten. The
fields are an allowlist; nothing else travels.

Registered from ``digital_twins_api.register`` so app.py is untouched.
"""
from __future__ import annotations

import json
import re
import time
import traceback
import uuid

try:
    from flask import Blueprint, Response, jsonify, request
except Exception:   # the pure helpers (the scrub, the prompt reading) import without Flask (tests on a bare interpreter)
    Blueprint = Response = jsonify = request = None

from migration import digital_twins as DT

bp = Blueprint('digital_twins_partner_api', __name__) if Blueprint is not None else None
PREFIX = '/api/v1/screenings'
INDEX_PREFIX = 'system/digital_twins/screenings/'
MODE_WORDS = (('trailer', re.compile(r"\b(trailers?|teasers?)\b", re.I)), ('spot', re.compile(r"\b(tv spots?|spots?|promos?)\b", re.I)),
              ('key_art', re.compile(r"\b(key art|posters?|artwork|one-?sheets? art)\b", re.I)), ('title', re.compile(r"\b(titles?|loglines?|names? for)\b", re.I)),
              ('script', re.compile(r"\b(scripts?|screenplays?|table reads?)\b", re.I)), ('concept', re.compile(r"\b(concepts?|synops[ie]s|one-?sheets?|pitch|idea)\b", re.I)),
              ('feature', re.compile(r"\b(features?|films?|movies?|cut of the film)\b", re.I)), ('episode', re.compile(r"\b(episode\s*(?:two|three|2|3)|second episode|next episode)\b", re.I)),
              ('binge', re.compile(r"\b(binge|season|episodes)\b", re.I)))
STATUS_OUT = {'draft': 'queued', 'preparing': 'preparing', 'ready': 'ready', 'playing': 'ready', 'stopped': 'complete'}

try:
    from prometheus.guards import PARTNER_BANNED_TOKENS as _BANNED
except Exception:
    _BANNED = ('synth', 'claude', 'anthropic', 'hostmap', 'worker', 'traceback', 'panel', 'cohort', 'pipeline', 'modeled', 'estimated', 'render', 'reasoning')


# ---------------------------------------------------------------- scrub
def scrub_text(s, cap: int = 400) -> str:
    """Fail closed: a string carrying any banned token leaves as ''."""
    if s is None:
        return ''
    text = ' '.join(str(s).replace('\u2014', ', ').replace('\u2013', '-').split())[:cap]
    low = text.lower()
    for tok in _BANNED:
        if tok in low:
            return ''
    return text


def _num(v):
    try:
        return round(float(v), 1) if v is not None else None
    except (TypeError, ValueError):
        return None


def _rows(rows, keep=('label', 'pct', 'people')):
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        row = {}
        for k in keep:
            v = r.get(k)
            row[k] = scrub_text(v, 120) if isinstance(v, str) else (_num(v) if k == 'pct' else v)
        out.append(row)
    return out


# ------------------------------------------------------------------ host
def _dt_api():
    import digital_twins_api as A
    return A


def _gate():
    """(user, err). A key with the build scope, or a signed-in seat with
    the product."""
    try:
        from prometheus.host import host
        user, err = host.gate(allow_api_key=True)
    except Exception:
        return None, (jsonify({'success': False, 'error': 'Sign in or send X-Crosswalk-API-Key.'}), 401)
    if err:
        return None, err
    user = dict(user or {})
    try:
        A = _dt_api()
        user['role'] = A._role(user)
    except Exception:
        pass
    if str(user.get('_auth_via') or '') == 'api_key':
        try:
            from prometheus import partner_keys as K
            if not K.has_scope(user, 'build'):
                return None, (jsonify({'success': False, 'error': 'This key cannot run a screening. Its scopes do not include build.'}), 403)
        except Exception:
            pass
    if not DT.has_access(user):
        return None, (jsonify({'success': False, 'error': 'Digital Twins IQ access is required for screenings. Ask your Crosswalk admin to switch it on.'}), 403)
    return user, None


def _key_id(user) -> str:
    try:
        from prometheus import partner_keys as K
        return K.key_id(user)
    except Exception:
        return str((user or {}).get('username') or '')


def _rate(user, endpoint: str):
    try:
        from prometheus import api_meter, partner_keys as K
        lim = K.limits(user)
        r = api_meter.check_rate(_key_id(user), endpoint, int(lim.get('ask_per_minute') or 0), int(lim.get('ask_per_day') or 0))
        if not r.get('allowed'):
            return jsonify({'success': False, 'error': 'Too many requests. Try again shortly.', 'retry_after': r.get('retry_after')}), 429
    except Exception:
        return None
    return None


def _s3():
    return _dt_api()._s3()


def _index_key(screening_id: str) -> str:
    return f"{INDEX_PREFIX}{re.sub(r'[^A-Za-z0-9_-]+', '', screening_id)}.json"


def _index_write(s3, rec: dict):
    if s3 is None:
        return
    try:
        s3.put_object(Bucket=DT.S3_BUCKET, Key=_index_key(rec['screening_id']), Body=json.dumps(rec, default=str).encode('utf-8'), ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _index_read(s3, screening_id: str):
    if s3 is None:
        return None
    try:
        return json.loads(s3.get_object(Bucket=DT.S3_BUCKET, Key=_index_key(screening_id))['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _index_list(s3, key_id: str, limit: int = 50) -> list:
    out = []
    if s3 is None:
        return out
    try:
        resp = s3.list_objects_v2(Bucket=DT.S3_BUCKET, Prefix=INDEX_PREFIX, MaxKeys=1000)
        for obj in sorted(resp.get('Contents', []) or [], key=lambda o: str(o.get('LastModified') or ''), reverse=True):
            rec = None
            try:
                rec = json.loads(s3.get_object(Bucket=DT.S3_BUCKET, Key=obj['Key'])['Body'].read().decode('utf-8'))
            except Exception:
                continue
            if rec and str(rec.get('key_id') or '') == key_id:
                out.append(rec)
            if len(out) >= limit:
                break
    except Exception:
        traceback.print_exc()
    return out


# ------------------------------------------------------------- the prompt
def mode_from_prompt(prompt: str, explicit: str = '') -> str:
    from migration.digital_twin_modes import MODE_SPECS
    m = str(explicit or '').strip().lower().replace(' ', '_').replace('-', '_')
    if m in MODE_SPECS:
        return m
    for mode, rx in MODE_WORDS:
        if rx.search(prompt or ''):
            return mode
    return 'pilot'


def twin_from_prompt(prompt: str, catalog: list, explicit: str = ''):
    """The catalog entry the words name: the explicit twin field, else
    the longest twin name found in the prompt."""
    want = str(explicit or '').strip().lower()
    low = str(prompt or '').lower()
    best = None
    for r in catalog or []:
        for cand in (str(r.get('name') or ''), str(r.get('subject') or '')):
            c = cand.strip().lower()
            if not c or len(c) < 3:
                continue
            if want and (c == want or want in c or c in want):
                return r
            if re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", low):
                if best is None or len(c) > len(str(best.get('name') or best.get('subject') or '')):
                    best = r
    return best


def _options_from(body: dict, form) -> list:
    raw = body.get('options') if isinstance(body, dict) else None
    if raw is None and form is not None:
        v = form.get('options')
        if v:
            try:
                raw = json.loads(v)
            except Exception:
                raw = [x.strip() for x in str(v).split('|') if x.strip()]
    return list(raw) if isinstance(raw, list) else []


# ---------------------------------------------------------------- results
def _result(sess: dict, twin_name: str) -> dict:
    from migration import digital_twin_norms as N
    sc = sess.get('scorecard') or {}
    bat = sc.get('battery') or {}
    head = bat.get('headline') or {}
    norm_cols = sc.get('norm_columns') or {}
    status = STATUS_OUT.get(str(sess.get('status') or 'draft'), 'queued')
    out = {'screening_id': str(sess.get('screening_id') or sess.get('id') or ''), 'status': status, 'mode': str(sess.get('mode') or 'pilot'),
           'title': scrub_text(sess.get('title'), 160), 'audience': scrub_text(twin_name, 120), 'cut_label': scrub_text(sess.get('cut_label'), 40),
           'genre': scrub_text(sess.get('genre'), 40), 'format': scrub_text(sc.get('format') or sess.get('format'), 20),
           'seats': int((sess.get('group') or {}).get('in_room') or len((sess.get('group') or {}).get('roster') or [])), 'people': sc.get('people'),
           'runtime_s': (sess.get('media') or {}).get('runtime_s'), 'created_at': sess.get('created_at'), 'updated_at': sess.get('updated_at'),
           'price_usd': sess.get('price_usd'), 'progress': scrub_text(sess.get('prepare_stage') or sess.get('prepare_note'), 120) if status != 'complete' else 'complete'}
    if status not in ('ready', 'complete') or head.get('total_positive_pct') is None and sc.get('overall_like') is None:
        return out
    out['scorecard'] = {'excellent_pct': _num(head.get('excellent_pct')), 'total_positive_pct': _num(head.get('total_positive_pct')), 'definitely_recommend_pct': _num(head.get('definitely_recommend_pct')),
                        'finish_pct': _num(head.get('finish_pct')), 'tune_in_pct': _num(head.get('tune_in_pct')), 'urgency_opening_pct': _num(head.get('urgency_opening_pct')),
                        'better_than_expected_pct': _num(head.get('better_than_expected_pct')), 'full_price_pct': _num(head.get('full_price_pct')), 'confusion_pct': _num(head.get('confusion_pct')),
                        'dial_average': _num(sc.get('overall_like')), 'lines': [l for l in (scrub_text(x, 240) for x in (sc.get('norm_lines') or [])) if l]}
    out['norms'] = {m: {'norm': _num((norm_cols.get(m) or {}).get('norm')), 'genre_norm': _num((norm_cols.get(m) or {}).get('genre_norm'))} for m in N.METRICS if norm_cols.get(m) and (norm_cols.get(m) or {}).get('norm') is not None}
    out['questions'] = [{'id': q.get('id'), 'label': scrub_text(q.get('label'), 80), 'rows': _rows(q.get('rows'))} for q in (bat.get('questions') or []) if isinstance(q, dict)]
    out['quadrants'] = [{'label': scrub_text(q.get('label'), 40), 'seats': q.get('n'), 'people': q.get('people'), 'excellent_pct': _num(q.get('excellent_pct')), 'total_positive_pct': _num(q.get('total_positive_pct')),
                         'definitely_recommend_pct': _num(q.get('definitely_recommend_pct')), 'finish_pct': _num(q.get('finish_pct'))} for q in (bat.get('quadrants') or [])]
    fav, dead = sc.get('favorite_moment') or {}, sc.get('dead_spot') or {}
    out['moments'] = {'favorite': {'clock': fav.get('clock'), 'line': scrub_text(fav.get('line'), 200)}, 'dead_spot': {'clock': dead.get('clock'), 'line': scrub_text(dead.get('line'), 200)}}
    dial = sess.get('dial') or {}
    out['dial'] = {'t': dial.get('t') or [], 'value': dial.get('value') or []}
    out['reactions'] = [{'t': e.get('t'), 'type': e.get('type'), 'line': scrub_text(e.get('line'), 160)} for e in (sess.get('events') or [])[:300]]
    mr = sess.get('mode_read') or {}
    if mr:
        keep = ('mode', 'interest_before_pct', 'interest_after_pct', 'definite_interest_lift_pp', 'would_watch_pct', 'recommend_pct', 'definitely_interested_pct', 'interested_pct', 'unique_pct',
                'commitment_pct', 'better_than_prior_pct', 'tune_in_delta_pp', 'story_pct', 'structure_pct', 'character_pct', 'trend_pp')
        m_out = {k: (_num(mr[k]) if isinstance(mr.get(k), (int, float)) else mr[k]) for k in keep if mr.get(k) is not None}
        m_out['read'] = scrub_text(mr.get('read'), 400)
        if isinstance(mr.get('who_for'), dict):
            m_out['who_for'] = scrub_text(mr['who_for'].get('line'), 120)
        if mr.get('options'):
            m_out['options'] = [{'rank': o.get('rank'), 'label': scrub_text(o.get('label'), 120), 'preference_pct': _num(o.get('preference_pct')), 'fit_pct': _num(o.get('fit_pct')), 'unique_pct': _num(o.get('unique_pct')),
                                 'attention_pct': _num(o.get('attention_pct')), 'interest_lift_pp': _num(o.get('interest_lift_pp')),
                                 'reasons': [l for l in (scrub_text(r.get('line'), 160) for r in (o.get('reasons') or [])) if l][:3]} for o in mr['options']]
        if mr.get('sold_it'):
            m_out['sold_it'] = [{'clock': s.get('clock'), 'line': scrub_text(s.get('line'), 160)} for s in mr['sold_it']]
        if mr.get('strongest'):
            m_out['strongest'] = _rows(mr['strongest'])
        if mr.get('doubt'):
            m_out['doubt'] = _rows(mr['doubt'])
        if mr.get('scenes') and mr.get('mode') == 'script':
            m_out['scenes'] = [{'clock': s.get('clock'), 'slug': scrub_text(s.get('slug'), 80), 'dial': _num(s.get('dial')), 'notes': {k: scrub_text(v, 160) for k, v in (s.get('notes') or {}).items() if v}} for s in mr['scenes'][:40]]
        if (mr.get('compare') or {}).get('ranked'):
            m_out['compare'] = {'winner': scrub_text(mr['compare'].get('winner'), 80), 'read': scrub_text(mr['compare'].get('read'), 300), 'ranked': mr['compare']['ranked']}
        out['mode_read'] = m_out
    d = sess.get('diagnostics') or {}
    ch = d.get('characters') or {}
    if ch.get('characters'):
        out['characters'] = {'rows': [{'name': scrub_text(c.get('name'), 60), 'role': c.get('role'), 'excellent_pct': _num(c.get('excellent_pct')), 'total_positive_pct': _num(c.get('total_positive_pct')), 'norm': _num(c.get('norm')),
                                       'screen_time_s': c.get('screen_s'), 'screen_time_share_pct': _num(c.get('share_pct'))} for c in ch['characters']],
                             'relationship': ({'pair': ch['relationship'].get('pair'), 'excellent_pct': _num(ch['relationship'].get('excellent_pct'))} if ch.get('relationship') else None),
                             'follow_into_second_episode': _rows(ch.get('follow') or [], ('name', 'pct', 'people')), 'read': scrub_text(ch.get('read'), 400)}
    pace = d.get('pace') or {}
    if pace.get('acts'):
        out['pace'] = {'acts': [{'label': scrub_text(a.get('label'), 40), 'from_clock': a.get('from_clock'), 'to_clock': a.get('to_clock'), 'too_slow_pct': _num(a.get('too_slow_pct')), 'just_right_pct': _num(a.get('just_right_pct')),
                                 'too_fast_pct': _num(a.get('too_fast_pct'))} for a in pace['acts']],
                       'confusion_pct': _num(pace.get('confusion_pct')), 'confusions': [{'clock': c.get('clock'), 'line': scrub_text(c.get('line'), 160)} for c in (pace.get('confusions') or [])[:10]],
                       'beginning_excellent_pct': _num((pace.get('beginning') or {}).get('excellent_pct')), 'ending_excellent_pct': _num((pace.get('ending') or {}).get('excellent_pct')),
                       'running_time': scrub_text(pace.get('running_time_read'), 240), 'read': scrub_text(pace.get('read'), 400)}
    sc_ = d.get('scenes') or {}
    if sc_.get('liked_best') or sc_.get('liked_least'):
        def scn(rows):
            return [{'clock': s.get('clock'), 'on_screen': scrub_text(s.get('on_screen'), 140), 'mentions': s.get('mentions'), 'people': s.get('people'),
                     'quotes': [l for l in (scrub_text(q.get('line'), 160) for q in (s.get('quotes') or [])) if l][:3]} for s in rows or []]
        out['scenes'] = {'liked_best': scn(sc_.get('liked_best')), 'liked_least': scn(sc_.get('liked_least')),
                         'not_recommending': [l for l in (scrub_text(n.get('line'), 160) for n in (sc_.get('not_recommending') or [])) if l][:8],
                         'similar_titles': [{'title': scrub_text(t.get('title'), 80), 'mentions': t.get('mentions')} for t in (sc_.get('similar_titles') or [])],
                         'appeal': scrub_text((sc_.get('appeal') or {}).get('line'), 120)}
    comps = d.get('components') or {}
    if comps.get('rows'):
        out['components'] = [{'label': scrub_text(c.get('label'), 40), 'excellent_pct': _num(c.get('excellent_pct')), 'norm': _num(c.get('norm')), 'genre_norm': _num(c.get('genre_norm'))} for c in comps['rows']]
    mk = sess.get('marketing_read') or {}
    if mk:
        out['marketing'] = {'who_for': scrub_text((mk.get('who_for') or {}).get('line'), 120), 'urgency_opening_pct': _num(mk.get('urgency_opening_pct')),
                            'trailer_moments': [{'clock': m.get('clock'), 'line': scrub_text(m.get('line'), 160)} for m in (mk.get('trailer_moments') or [])], 'read': scrub_text(mk.get('read'), 300)}
    rec = sess.get('recruit') or {}
    if rec:
        w = rec.get('walkouts') or {}
        out['recruit'] = {'concept': scrub_text(rec.get('concept'), 240), 'invited': rec.get('invited'), 'seated': rec.get('seated'), 'invite_ratio': scrub_text(rec.get('invite_ratio'), 60),
                          'walkouts': {'count': w.get('count'), 'pct': _num(w.get('pct'))}, 'quotas_met': rec.get('quotas_met') or []}
    cr = sess.get('critics') or {}
    if cr.get('critic_score') is not None:
        out['critics'] = {'critic_score': cr.get('critic_score'), 'range': cr.get('range'), 'awards': scrub_text(cr.get('awards_read'), 240)}
    out['exports'] = {'report_pdf': f"{PREFIX}/{out['screening_id']}/report.pdf", 'report_pptx': f"{PREFIX}/{out['screening_id']}/report.pptx", 'dial_png': f"{PREFIX}/{out['screening_id']}/dial.png"}
    return out


def _finalize_if_ready(sess, s3):
    """A key caller has no live clock: the moment the room has watched,
    the cut has 'played' and the scorecard is final. Norms grow once."""
    from migration import digital_twin_pilot as PI
    if sess.get('status') in ('ready', 'playing') and sess.get('play_charged') and (sess.get('dial') or {}).get('value'):
        PI.mark_playing(sess)
        PI.stop_session(sess)
        PI.record_norms(sess, s3=s3)
        return True
    return False


def _load(user, screening_id: str):
    """(payload, twin_key, sess, err)."""
    s3 = _s3()
    rec = _index_read(s3, screening_id)
    if not rec:
        return None, None, None, (jsonify({'success': False, 'error': 'screening not found'}), 404)
    if str(rec.get('key_id') or '') != _key_id(user) and not DT.is_admin(user):
        return None, None, None, (jsonify({'success': False, 'error': 'screening not found'}), 404)
    A = _dt_api()
    payload = DT.load_twin(s3, rec['twin_key'])
    if not payload:
        return None, None, None, (jsonify({'success': False, 'error': 'screening not found'}), 404)
    sess = A._pilot(payload, rec['session_id'])
    if not sess:
        return None, None, None, (jsonify({'success': False, 'error': 'screening not found'}), 404)
    sess['screening_id'] = screening_id
    return payload, rec['twin_key'], sess, None


# ----------------------------------------------------------------- routes
def _route(*a, **k):
    if bp is None:
        return lambda fn: fn
    return bp.route(*a, **k)


@_route(PREFIX, methods=['POST'])
def v1_screening_open():
    user, err = _gate()
    if err:
        return err
    rl = _rate(user, 'screenings')
    if rl is not None:
        return rl
    A = _dt_api()
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_modes as MODES
    is_multipart = bool(request.files) or (request.content_type or '').startswith('multipart/')
    body = (request.get_json(silent=True) or {}) if not is_multipart else {k: v for k, v in request.form.items()}
    prompt = ' '.join(str(body.get('prompt') or body.get('text_prompt') or '').split())
    if not prompt and not body.get('twin'):
        return jsonify({'success': False, 'error': 'Say what to test and on which audience: for example "Test this trailer for Hurry Up Tomorrow on the Gilmore Girls twin".'}), 400
    s3 = _s3()
    try:
        catalog = DT.visible_runs(DT.list_runs(s3), user)
    except Exception:
        catalog = []
    entry = twin_from_prompt(prompt, catalog, explicit=str(body.get('twin') or body.get('audience') or ''))
    if not entry:
        return jsonify({'success': False, 'error': 'No twin by that name. Name an audience that has a twin, or build the twin first in Digital Twins IQ.'}), 404
    payload, perr = A._twin_or_404(user, str(entry.get('key') or ''))
    if perr:
        return perr
    key = str(entry.get('key') or '')
    mode = mode_from_prompt(prompt, str(body.get('mode') or ''))
    f = request.files.get('file') if is_multipart else None
    if f is not None and getattr(f, 'filename', ''):
        fn = str(f.filename)
        if MODES.accept_image_name(fn, getattr(f, 'mimetype', '') or '') and mode not in ('key_art',):
            mode = 'key_art'
        elif MODES.accept_text_name(fn, getattr(f, 'mimetype', '') or '') and mode not in ('script', 'concept'):
            mode = 'script'
    title = ' '.join(str(body.get('title') or '').split())[:120] or f"{PI.MODE_TITLE_WORD.get(mode, 'Screening')}: {prompt[:70]}"
    try:
        in_room = int(body.get('seats') or body.get('in_room') or 0) or DT.panelists_for(payload)
    except (TypeError, ValueError):
        in_room = DT.panelists_for(payload)
    options = _options_from(body, request.form if is_multipart else None)
    text = str(body.get('text') or body.get('script') or body.get('concept') or '')
    if mode == 'concept' and not text:
        text = prompt
    sess = PI.open_session([payload], in_room=in_room, title=title, created_by=A._uname(user) or _key_id(user), session_id=uuid.uuid4().hex[:10], mode=mode,
                           logline=str(body.get('logline') or ''), text=text, options=options or None, rough_cut=str(body.get('rough_cut') or '').lower() in ('1', 'true', 'yes'))
    sess['twin_keys'] = [key]
    sess['screening_id'] = f"scr_{uuid.uuid4().hex[:14]}"
    sess['via'] = 'api'
    if body.get('genre'):
        PI.set_options(sess, {'genre': str(body['genre'])})
    payload.setdefault('pilot_sessions', []).insert(0, sess)
    try:
        from migration import digital_twin_room as ROOM
        ROOM.ensure_personas(sess, {key: payload}, session_seed=str(sess.get('id') or ''))
    except Exception:
        traceback.print_exc()
    # the file: a cut (slot 0), key art (an option) or a script (its text)
    extra_files = []
    if is_multipart:
        extra_files = [x for x in request.files.getlist('files') if getattr(x, 'filename', '')]
        if f is not None and getattr(f, 'filename', ''):
            extra_files = [f] + extra_files
    for i, fx in enumerate(extra_files[:6]):
        raw = fx.read()
        if not raw or len(raw) < 16:
            continue
        fn = str(fx.filename)
        ctype = getattr(fx, 'mimetype', '') or ''
        if mode == 'key_art' and MODES.accept_image_name(fn, ctype):
            if s3 is None or len(sess.get('options') or []) >= MODES.MAX_OPTIONS:
                continue
            mk = PI.media_s3_key(A._uname(user) or 'api', sess['id'], f"art{i}_{fn}")
            s3.put_object(Bucket=DT.S3_BUCKET, Key=mk, Body=raw, ContentType=ctype or 'image/jpeg')
            opts = list(sess.get('options') or [])
            opts.append({'id': 'abcdef'[len(opts)], 'label': f"Art {chr(65 + len(opts))}", 'caption': '', 's3_key': mk, 'filename': fn, 'content_type': ctype or 'image/jpeg', 'bytes': len(raw)})
            sess['options'] = opts
        elif mode in ('script', 'concept') and MODES.accept_text_name(fn, ctype):
            tx = MODES.text_from_upload(raw, fn)
            if tx.strip():
                PI.set_options(sess, {'text': tx})
        elif PI.accept_upload_name(fn, ctype):
            if s3 is None:
                continue
            if len(raw) > 2 * 1024 * 1024 * 1024:
                return jsonify({'success': False, 'error': 'Use a cut under 2 GB.'}), 400
            slot = i
            mk = PI.media_s3_key(A._uname(user) or 'api', sess['id'], f"a{slot}_{fn}")
            s3.put_object(Bucket=DT.S3_BUCKET, Key=mk, Body=raw, ContentType=ctype or 'video/mp4')
            runtime = 0.0
            try:
                import os
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(fn)[1] or '.mp4')
                os.close(fd)
                with open(tmp, 'wb') as fh:
                    fh.write(raw)
                runtime = float(PI.probe_duration(tmp) or 0)
                os.remove(tmp)
            except Exception:
                pass
            label = f"Trailer {chr(65 + slot)}" if mode in ('trailer', 'spot') else (f"Episode {slot + 1}" if mode == 'binge' else f"Cut {chr(65 + slot)}")
            rec = {'id': uuid.uuid4().hex[:6], 'kind': 'video', 'slot': slot, 'label': label, 'title': label, 's3_key': mk, 'filename': fn, 'content_type': ctype or 'video/mp4', 'bytes': len(raw), 'runtime_s': runtime,
                   'hash': PI.media_hash(label, runtime, fn, str(len(raw)))}
            sess.setdefault('assets', []).append(rec)
            if slot == 0:
                sess['media'].update({'filename': fn, 's3_key': mk, 'content_type': rec['content_type'], 'bytes': len(raw), 'source': 'upload', 'title': str(body.get('cut_title') or title)[:160], 'hash': rec['hash']})
                if runtime:
                    sess['media']['runtime_s'] = runtime
    try:
        runtime_in = float(body.get('runtime_s') or 0)
    except (TypeError, ValueError):
        runtime_in = 0.0
    if runtime_in and not (sess.get('media') or {}).get('runtime_s'):
        sess['media']['runtime_s'] = runtime_in
    # the price, on the first Play a key caller never presses: it is taken now, before the watch
    funds = A._charge_pilot(user, payload, sess, for_play=True)
    if funds is not None:
        payload['pilot_sessions'] = [s for s in payload.get('pilot_sessions') or [] if s.get('id') != sess['id']]
        return funds
    try:
        from migration.digital_twins import PRICING
        qty = int(sess.get('play_quantity') or 1)
        sess['price_usd'] = round(float(PRICING.get(str(sess.get('play_price_key') or 'digital_twin_pilot'), 0.0)) * qty, 2)
    except Exception:
        pass
    DT.save_twin(s3, key, payload)
    _index_write(s3, {'screening_id': sess['screening_id'], 'twin_key': key, 'session_id': sess['id'], 'key_id': _key_id(user), 'owner': A._uname(user), 'mode': mode, 'created_at': sess['created_at']})
    # the watch: a file goes to the read worker; words and options run here
    job_id = None
    if (sess.get('media') or {}).get('s3_key'):
        try:
            job_id = A._start_prepare(user, key, sess, payload, {'title': title})
        except Exception:
            traceback.print_exc()
            job_id = None
    if not job_id:
        try:
            A._caption_art_for(sess, user)
        except Exception:
            traceback.print_exc()
        try:
            PI.prepare_watch(sess, {key: payload}, duration_s=float((sess.get('media') or {}).get('runtime_s') or 0), title=str((sess.get('media') or {}).get('title') or title),
                             transcript=sess.get('transcript'), usage_extras=A._usage_extras(user), s3=s3)
            _finalize_if_ready(sess, s3)
        except Exception:
            traceback.print_exc()
        DT.save_twin(s3, key, payload)
    out = _result(sess, str((payload.get('meta') or {}).get('name') or ''))
    out['success'] = True
    return jsonify(out), 202 if out['status'] in ('queued', 'preparing') else 200


@_route(f'{PREFIX}/<screening_id>', methods=['GET'])
def v1_screening_get(screening_id):
    user, err = _gate()
    if err:
        return err
    payload, key, sess, lerr = _load(user, screening_id)
    if lerr:
        return lerr
    A = _dt_api()
    s3 = _s3()
    changed = False
    if sess.get('status') == 'preparing':
        A._prepare_job_live(sess)
        if sess.get('status') == 'draft' and sess.get('play_charged'):
            # the file could not be read this time: the room runs on the clock, nothing more is charged
            try:
                from migration import digital_twin_pilot as PI
                PI.prepare_watch(sess, {key: payload}, duration_s=float((sess.get('media') or {}).get('runtime_s') or 0), title=str((sess.get('media') or {}).get('title') or sess.get('title') or ''),
                                 transcript=sess.get('transcript'), usage_extras=A._usage_extras(user), s3=s3)
            except Exception:
                traceback.print_exc()
            changed = True
    if _finalize_if_ready(sess, s3):
        changed = True
    if changed:
        DT.save_twin(s3, key, payload)
    out = _result(sess, str((payload.get('meta') or {}).get('name') or ''))
    out['success'] = True
    resp = jsonify(out)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@_route(f'{PREFIX}/<screening_id>/report.pdf', methods=['GET'])
@_route(f'{PREFIX}/<screening_id>/report.pptx', methods=['GET'])
@_route(f'{PREFIX}/<screening_id>/dial.png', methods=['GET'])
def v1_screening_report(screening_id):
    user, err = _gate()
    if err:
        return err
    payload, key, sess, lerr = _load(user, screening_id)
    if lerr:
        return lerr
    if not (sess.get('dial') or {}).get('value'):
        return jsonify({'success': False, 'error': 'The room has not finished watching. Poll the screening until its status is complete.'}), 409
    from migration import digital_twin_report as R
    path = request.path
    if path.endswith('dial.png'):
        png = R.dial_chart_png(sess)
        if not png:
            return jsonify({'success': False, 'error': 'The dial chart is not available for this screening.'}), 404
        return Response(png, mimetype='image/png')
    if path.endswith('.pdf'):
        return Response(R.build_report_pdf(payload, sess), mimetype='application/pdf', headers={'Content-Disposition': f'attachment; filename="{R.report_filename(payload, sess, ".pdf")}"'})
    return Response(R.build_report_deck(payload, sess), mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    headers={'Content-Disposition': f'attachment; filename="{R.report_filename(payload, sess, ".pptx")}"'})


@_route(PREFIX, methods=['GET'])
def v1_screening_list():
    user, err = _gate()
    if err:
        return err
    recs = _index_list(_s3(), _key_id(user))
    return jsonify({'success': True, 'screenings': [{'screening_id': r.get('screening_id'), 'mode': r.get('mode'), 'created_at': r.get('created_at'), 'status_url': f"{PREFIX}/{r.get('screening_id')}"} for r in recs]})


def register(app):
    if bp is not None and 'digital_twins_partner_api' not in app.blueprints:
        app.register_blueprint(bp)
    return bp


__all__ = ['bp', 'register', 'PREFIX', 'scrub_text', 'mode_from_prompt', 'twin_from_prompt', '_result', 'STATUS_OUT']
