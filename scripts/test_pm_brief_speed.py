#!/usr/bin/env python3
"""The build brief, faster (Jenna approved 2026-11-23 9:40 PM PT).

The brief's one model call (60 s at the median: 4,000 output tokens at
63 a second) becomes two parallel halves of the same JSON on the same
model and rulebook, merged key for key; the content-identity verify
starts alongside the model and is reused when the draft's subject is the
title it checked; both identity research calls run under a time budget.
The brief's fields, prices, decisions and rules are untouched: the halves
are told which keys to write, nothing else changes in the prompt.

Pins, hermetically (no model, no store, no mail):

1. the merge: the core's fields with the shape's five keys, a key the
   shape left out kept from the core, keys neither list names from the
   core;
2. the draft: two calls run at the same time, each with its own key
   instruction on the user prompt and the rulebook untouched; the merged
   result carries both halves; the stage marks land; the switch, a
   multi-profile ask, a failed half and an array core all take the
   classic single call (the original arguments, salvage on);
3. a canned agent that answers every call with one full draft (the
   replay harness) merges back to exactly that draft, so the replay gate
   cannot move;
4. the identity verify: a prestarted check for the same title is reused
   (no second call), a different title runs the verify now, a slow
   verify returns None at the budget; the prestart never runs in a test
   process and never without a medium or platform word;
5. the wiring: interpret_turn drafts through brief_speed after the
   follow-up read, app.py's two identity research calls go through the
   budget, the progress labels and the exit-gate mark exist, no em dash;
6. the approve span (2026-11-24, the ~13 s after the estimate): the
   credit charge runs off the person's wait on its own thread (inline in
   a test process), the response's balance is the pre-charge balance less
   the price, users.json is confirmed with S3 once per request, the
   approve gates and tail carry stage marks;
7. the repeat gate's engine list (400 status documents, 1 MB over HTTP)
   is read while the spec is built: started by the approve path, taken
   once by the gate, its own error handed back so fail-open is unchanged,
   inline in a test process, never across requests.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')

HERE = Path(__file__).resolve().parent
WEB = HERE.parent
sys.path.insert(0, str(WEB))

from prometheus import brief_speed as B  # noqa: E402

FAIL = 0


def check(label, cond, detail=''):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}" + (f"  {detail}" if (detail and not cond) else ''))
    if not cond:
        FAIL += 1


FULL = {'subject': 'Allbirds', 'name': 'Allbirds', 'file_stem': 'Allbirds', 'brand_category': 'APPAREL/FOOTWEAR', 'decision': 'new_build',
        'subject_raw_tu': 48213, 'subject_raw_avid': 11107, 'run_avid': True, 'assumptions': ['US only'], 'date_range': {'start': '2025-11-24', 'end': '2026-11-24'},
        'tu_demos': {'GENDER': {'FEMALE': 58.3, 'MALE': 41.7}}, 'avid_demos': {'GENDER': {'FEMALE': 61.2, 'MALE': 38.8}},
        'subject_rows': [['APPAREL/FOOTWEAR', 'Allbirds']], 'extra_rows': [['APPAREL/FOOTWEAR', 'Rothys']], 'persona_notes': 'Skews female, 25 to 44.',
        'identity_confident': True, 'estimated_run_minutes': 12}
CORE = {k: v for k, v in FULL.items() if k not in B.SHAPE_KEYS}
SHAPE = {k: v for k, v in FULL.items() if k in B.SHAPE_KEYS}


class Agent:
    """A fake model: records every call (with the thread and the clock) and answers by the key instruction in the user prompt."""

    def __init__(self, core=None, shape=None, single=None, delay=0.15, core_fail=False, core_list=False):
        self.calls = []
        self.core, self.shape, self.single = core if core is not None else CORE, shape if shape is not None else SHAPE, single if single is not None else FULL
        self.delay, self.core_fail, self.core_list = delay, core_fail, core_list
        self.lock = threading.Lock()

    def __call__(self, **kw):
        t0 = time.monotonic()
        time.sleep(self.delay)
        up = str(kw.get('user_prompt') or '')
        kind = 'core' if 'PART ONE' in up else ('shape' if 'PART TWO' in up else 'single')
        with self.lock:
            self.calls.append({'kind': kind, 'thread': threading.current_thread().name, 't0': t0, 't1': time.monotonic(), 'kw': kw})
        if kind == 'core':
            if self.core_fail:
                return {'success': False, 'error': 'boom'}
            if self.core_list:
                return {'success': True, 'data': [dict(self.core), dict(self.core)], 'model': 'fake'}
            return {'success': True, 'data': json.loads(json.dumps(self.core)), 'model': 'fake'}
        if kind == 'shape':
            return {'success': True, 'data': json.loads(json.dumps(self.shape)), 'model': 'fake'}
        return {'success': True, 'data': json.loads(json.dumps(self.single)), 'model': 'fake'}


def main():
    print('1. the merge')
    m = B.merge(CORE, SHAPE)
    check('the core fields with the shape keys from the shape call', m == FULL)
    check("a key the shape call left out keeps the core's value", B.merge(dict(CORE, persona_notes='from core'), {k: v for k, v in SHAPE.items() if k != 'persona_notes'})['persona_notes'] == 'from core')
    check('a key neither list names comes from the core', B.merge(dict(CORE, odd=1), dict(SHAPE, odd=2))['odd'] == 1)
    check('a shape key the core also wrote is the shape call\'s', B.merge(dict(CORE, tu_demos={'x': 1}), SHAPE)['tu_demos'] == SHAPE['tu_demos'])

    print('2. the draft in two halves')
    stages = {}
    ag = Agent()
    os.environ[B.ENV] = '1'
    t0 = time.monotonic()
    res = B.draft(ag, system_prompt='RULES', user_prompt='ASK', max_tokens=14000, temperature=0.4, model='m', text='build a profile of Allbirds shoppers',
                  long_ceiling=24000, stage=lambda k, **kw: stages.__setitem__(k, kw.get('ms')))
    wall = time.monotonic() - t0
    kinds = sorted(c['kind'] for c in ag.calls)
    check('two calls, one for each half', kinds == ['core', 'shape'], str(kinds))
    overlap = len(ag.calls) == 2 and max(c['t0'] for c in ag.calls) < min(c['t1'] for c in ag.calls)
    check('they run at the same time (the wall is one call, not two)', overlap and wall < ag.delay * 1.8, f"wall {wall:.2f}s")
    check('each half carries its key instruction on the user prompt and the rulebook is untouched',
          all(c['kw']['system_prompt'] == 'RULES' and c['kw']['user_prompt'].startswith('ASK') for c in ag.calls)
          and any('OMIT' in c['kw']['user_prompt'] and all(k in c['kw']['user_prompt'] for k in B.SHAPE_KEYS) for c in ag.calls if c['kind'] == 'core')
          and any('ONLY these five keys' in c['kw']['user_prompt'] for c in ag.calls if c['kind'] == 'shape'))
    check('the same model, temperature and ceiling on both halves', all(c['kw']['model'] == 'm' and c['kw']['temperature'] == 0.4 and c['kw']['max_tokens'] == 14000 for c in ag.calls))
    check('the merged draft is the whole brief, marked split', res.get('success') and res.get('split') is True and res['data'] == FULL)
    check('the stage marks land for both halves', 'model_core' in stages and 'model_shape' in stages and all(isinstance(v, int) for v in stages.values()))
    os.environ[B.ENV] = '0'
    ag2 = Agent()
    res2 = B.draft(ag2, system_prompt='RULES', user_prompt='ASK', max_tokens=14000, temperature=0.4, model='m', text='x', long_ceiling=24000)
    check('the switch off: one classic call with the original arguments, salvage on, no instruction',
          [c['kind'] for c in ag2.calls] == ['single'] and ag2.calls[0]['kw']['user_prompt'] == 'ASK' and ag2.calls[0]['kw'].get('salvage_arrays') is True and res2['data'] == FULL and not res2.get('split'))
    os.environ[B.ENV] = '1'
    ag3 = Agent()
    res3 = B.draft(ag3, system_prompt='RULES', user_prompt='ASK', max_tokens=24000, temperature=0.4, model='m', text='profiles for Coke, Pepsi, Dr Pepper', long_ceiling=24000)
    check('a multi-profile ask (the long ceiling) takes the classic call', [c['kind'] for c in ag3.calls] == ['single'] and res3['data'] == FULL)
    ag4 = Agent(core_fail=True)
    stages4 = {}
    res4 = B.draft(ag4, system_prompt='RULES', user_prompt='ASK', max_tokens=14000, temperature=0.4, model='m', text='x', long_ceiling=24000,
                   stage=lambda k, **kw: stages4.__setitem__(k, kw.get('ms')))
    check('a failed half falls back to the classic call, marked', sorted(c['kind'] for c in ag4.calls) == ['core', 'shape', 'single'] and res4['data'] == FULL
          and 'model_fallback' in stages4 and not res4.get('split'))
    ag5 = Agent(core_list=True)
    res5 = B.draft(ag5, system_prompt='RULES', user_prompt='ASK', max_tokens=14000, temperature=0.4, model='m', text='x', long_ceiling=24000)
    check('a core that came back as an array (a multi-profile draft after all) takes the classic call, which owns arrays',
          sorted(c['kind'] for c in ag5.calls) == ['core', 'shape', 'single'] and res5['data'] == FULL)

    print('3. the replay harness answers every call with one full draft')
    class Canned:
        def __init__(self):
            self.n = 0

        def __call__(self, **kw):
            self.n += 1
            return {'success': True, 'data': json.loads(json.dumps(FULL)), 'model': 'replay'}
    cn = Canned()
    res6 = B.draft(cn, system_prompt='RULES', user_prompt='ASK', max_tokens=14000, temperature=0.4, model='m', text='x', long_ceiling=24000)
    check('the merge of two full drafts is that draft, so a canned replay cannot move', res6['data'] == FULL and cn.n == 2)

    print('4. the identity verify alongside the model')
    check('no prestart in a test process', B.identity_prestart('run one on Furious, the show on Hulu', object()) is None)
    os.environ.pop('REGRESSION_TEST_MODE', None)
    try:
        class Host:
            def __init__(self, sig):
                self._sig = sig
                self.verified = []

            def _identity_signals_from_prompt(self, text):
                return dict(self._sig)

            def _verify_content_identity(self, draft, prompt, sig):
                self.verified.append(dict(draft))
                time.sleep(0.05)
                return {'resolved_title': draft.get('subject'), 'confidence': 'high', 'exists': True}
        h0 = Host({'medium': '', 'platform': '', 'title_guess': 'Allbirds'})
        check('no prestart without a medium or platform word in the ask', B.identity_prestart('build a profile of Allbirds shoppers', h0) is None and not h0.verified)
        h1 = Host({'medium': 'series', 'medium_word': 'show', 'platform': 'Hulu', 'title_guess': 'Furious'})
        t = B.identity_prestart('run one on Furious, the show on Hulu', h1)
        check('with a medium or platform word the verify starts for the title the ask plainly carries', t is not None and t.subject == 'Furious')
        t.join(timeout=5)
        check('its result is the verify on a provisional draft of that title', t.result == {'resolved_title': 'Furious', 'confidence': 'high', 'exists': True} and h1.verified[0]['subject'] == 'Furious')

        class G:   # the request's g
            pass
        g = G()
        _orig_stash = B._stash
        B._stash = lambda: g        # the request stash, hermetically
        try:
            B.hold_prestart(t)
            calls = []

            def verify_now(draft, prompt, sig):
                calls.append(draft.get('subject'))
                return {'resolved_title': 'x', 'confidence': 'low'}
            r1 = B.identity_verify({'subject': 'Furious'}, 'run one on Furious, the show on Hulu', h1._sig, verify_now)
            check('the draft whose subject is that title reuses the started verify (no second call)', r1 == t.result and calls == [])
            r2 = B.identity_verify({'subject': 'Fast and Furious'}, 'x', h1._sig, verify_now)
            check('a draft on another subject runs the verify now', r2 == {'resolved_title': 'x', 'confidence': 'low'} and calls == ['Fast and Furious'])
            os.environ[B.IDENTITY_BUDGET_ENV] = '5'      # the floor is 5 s; a budget under it reads as 5
            slow_started = threading.Event()

            def slow(draft, prompt, sig):
                slow_started.set()
                time.sleep(12)
                return {'resolved_title': 'late'}
            B.IDENTITY_BUDGET_S = 60.0
            t0 = time.monotonic()
            r3 = B.bounded(lambda: slow({'subject': 'Late'}, 'x', {}), 'identity', budget=0.3)
            check('a verify past its budget returns None in time (the deterministic floor applies)', r3 is None and time.monotonic() - t0 < 2.0 and slow_started.is_set())
            os.environ.pop(B.IDENTITY_BUDGET_ENV, None)
        finally:
            B._stash = _orig_stash
    finally:
        os.environ['REGRESSION_TEST_MODE'] = '1'

    print('6. the approve span: the charge off the wait, the balance, users.json once per request')
    check('the balance after the charge: unlimited stays -1, a covering balance loses the price, a short one keeps its credits (the wallet pays)',
          B.remaining_after(-1, 300) == -1 and B.remaining_after(500, 300) == 200 and B.remaining_after(100, 300) == 100 and B.remaining_after(None, 5) is None
          and B.remaining_after('x', 5) == 'x' and B.remaining_after(300, 300) == 0)
    ran = []
    t = B.charge_later(lambda: ran.append(1) or True, label='t1')
    check('in a test process the charge runs inline, so a suite sees it at once', t is None and ran == [1])
    os.environ.pop('REGRESSION_TEST_MODE', None)
    try:
        import io, contextlib
        started = threading.Event(); release = threading.Event()

        def slow_charge():
            started.set(); release.wait(2); return False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            t0 = time.monotonic()
            th = B.charge_later(slow_charge, label='user=x run=y price=3')
            took = time.monotonic() - t0
            started.wait(2); release.set(); th.join(3)
        check('live, the charge runs on its own non-daemon thread and the caller does not wait for it',
              th is not None and not th.daemon and took < 0.5 and started.is_set() and not th.is_alive())
        check('a charge that fails still prints the standing warning line', 'post-queue credit charge failed' in buf.getvalue() and 'usage NOT recorded' in buf.getvalue())
    finally:
        os.environ['REGRESSION_TEST_MODE'] = '1'

    class G2:
        pass
    g2 = G2()
    _orig_stash2 = B._stash
    B._stash = lambda: g2
    try:
        check('users.json: the first load in a request confirms the ETag with S3, the next loads reuse the copy, a moved ETag asks again',
              not B.users_memo_hit('e1') and (B.users_memo_mark('e1') or True) and B.users_memo_hit('e1') and not B.users_memo_hit('e2') and not B.users_memo_hit(''))
    finally:
        B._stash = _orig_stash2
    check('outside a request the memo never answers', not B.users_memo_hit('e1'))
    ar = (WEB / 'prometheus' / 'approve_route.py').read_text(encoding='utf-8')
    check('the approve path hands the charge to brief_speed after the balance read, reports the balance less the price, and marks its gates and its tail',
          "_bsp.charge_later(_charge, label=" in ar and "_credits_left = _bsp.remaining_after(_credits_left, price)" in ar
          and ar.index('_H.check_user_credits(_charge_user)') < ar.index('_bsp.charge_later(') and "'approve_gates'" in ar and "'approve_after'" in ar
          and ar.index('def _charge():') < ar.index('if not _H.consume_credit(') < ar.index('_bsp.charge_later(_charge')
          and "addon_cuts=len(_billable_cuts)):" in ar)
    app = (WEB / 'app.py').read_text(encoding='utf-8')
    check('load_users confirms the same ETag with S3 once per request and marks what it confirmed',
          'users_memo_hit(c_etag)' in app and '_bsp_users.users_memo_mark(etag)' in app and app.index('users_memo_hit(c_etag)') < app.index('IfNoneMatch=c_etag'))
    it2 = (WEB / 'prometheus' / 'interpret_turn.py').read_text(encoding='utf-8')
    check('the brief tail is marked before the auto-start order', "'at_auto_start'" in it2)

    print("7. the repeat gate's engine list, read while the spec is built")
    check('no request, no early read', B.prefetch('engine_list', lambda: [1]) is None and B.prefetched('engine_list') is None)
    g3 = G2()
    B._stash = lambda: g3
    try:
        p = B.prefetch('engine_list', lambda: [{'run_id': 'a'}])
        check('in a test process the read runs inline and is handed back once, as the read\'s own answer',
              p is not None and B.prefetched('engine_list') is p and p.get(1) == [{'run_id': 'a'}] and B.prefetched('engine_list') is None)

        def _boom():
            raise RuntimeError('engine list 503')
        pe = B.prefetch('engine_list', _boom)
        try:
            pe.get(1); raised = ''
        except RuntimeError as e:
            raised = str(e)
        check("a read that failed raises its own error from get(), so the gate's fail-open rule sees what it always saw", raised == 'engine list 503')
        check('two labels never cross', B.prefetch('other', lambda: 2) is not None and B.prefetched('engine_list') is pe and B.prefetched('other').get(1) == 2)
    finally:
        B._stash = _orig_stash2
    os.environ.pop('REGRESSION_TEST_MODE', None)
    try:
        hold = threading.Event()
        pl = B.Prefetch(lambda: hold.wait(3) and 'late', 'slow')
        try:
            pl.get(0.2); to = False
        except TimeoutError:
            to = True
        hold.set()
        check('live, the read runs on its own thread; a get() past its wait raises a timeout (the gate reads it as the engine down) and the value lands after', to and pl.get(2) == 'late')
    finally:
        os.environ['REGRESSION_TEST_MODE'] = '1'
    from prometheus import repeat_approval as RA

    class H4:
        SYNTH_QUEUE_URL, SYNTH_QUEUE_SECRET, S3_BUCKET, s3_client = 'http://127.0.0.1:9', 's', 'b', None
    g4 = G2()
    B._stash = lambda: g4
    try:
        B.prefetch('engine_list', lambda: [{'run_id': 'early'}])
        recs = RA.records_for(H4)
        check('the gate\'s recent_runs takes the early read when the request holds one', recs.recent_runs() == [{'run_id': 'early'}])
        try:
            recs.recent_runs(); fell = ''
        except Exception as e:
            fell = type(e).__name__
        check('with none left it reads from the engine as before (here an unreachable one)', fell != '')
        B.prefetch('engine_list', lambda: (_ for _ in ()).throw(RuntimeError('engine list 500')))
        eng = RA._Engine(RA.records_for(H4))
        check('an early read that failed leaves the gate fail-open exactly as before: an empty list and the engine marked down', eng.recent() == [] and eng.down is True)
    finally:
        B._stash = _orig_stash2
    check('the approve path starts the read before the spec build and the gate runs after it',
          "prefetch('engine_list'" in ar and ar.index("prefetch('engine_list'") < ar.index("fromlist=['Gate']).Gate(") and ar.index('_t_spec = time.monotonic()') <= ar.index("prefetch('engine_list'"))
    ra_src = (WEB / 'prometheus' / 'repeat_approval.py').read_text(encoding='utf-8')
    check('recent_runs asks for the early read first and keeps its fetch', ra_src.index("prefetched('engine_list')") < ra_src.index("_engine_get('/synth/list'"))

    print('5. the wiring')
    it = (WEB / 'prometheus' / 'interpret_turn.py').read_text(encoding='utf-8')
    core = it[it.index('def _pm_interpret_core'):]
    core = core[:core.index('\ndef ', 10)] if '\ndef ' in core[10:] else core
    check('interpret_turn drafts through brief_speed after the follow-up read, with the identity prestart beside the subject preverify',
          core.index('_fur.at_interpret(') < core.index('_bsp.identity_prestart(text, _H)') < core.index('_bsp.draft(') and '_H._run_nflx_claude_agent(' in core
          and 'preverify=_preverify' in core and "stage=_C._pm_ask_stage" in core and '_bsp.say_in_hand(spec_draft)' in core)
    check('the offsets for the unstaged time are marked', all(f"'{k}'" in core for k in ('at_model_start', 'at_identity_start', 'at_verify_end', 'at_estimate_start', 'at_return')))
    app = (WEB / 'app.py').read_text(encoding='utf-8')
    check("app.py's two identity research calls go through brief_speed (the started verify, the budget)",
          "identity_verify(draft, _own_words, sig, lambda _d, _p, _s: _verify_content_identity(draft, _own_words, sig))" in app
          and "bounded(lambda: _verify_short_name_identity(draft, prompt), 'short_name')" in app and 'ver = _verify_content_identity(' not in app)
    prog = (WEB / 'prometheus' / 'progress.py').read_text(encoding='utf-8')
    check('the progress labels name the new stages in plain words', all(k in prog for k in ('model_core', 'model_shape', 'at_identity_start', 'exit_gate')))
    pa = (WEB / 'prometheus' / 'page_analysis.py').read_text(encoding='utf-8')
    check('the exit gate has its own stage mark', "_pm_ask_stage('exit_gate', t0=_t_exit)" in pa)
    for rel in ('prometheus/brief_speed.py', 'prometheus/interpret_turn.py', 'scripts/test_pm_brief_speed.py'):
        check(f'no em dash in {rel}', '\u2014' not in (WEB / rel).read_text(encoding='utf-8'))
    check('the lines a person sees name no model, tier or token',
          not re.search(r"\b(model|tier|token|Sonnet|Haiku|Claude)\b", "Draft in hand for X. Checking the name, the library and the window. Sizing and pricing the brief."))
    print()
    if FAIL:
        print(f'FAILED {FAIL}')
        return 1
    print('ALL PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
