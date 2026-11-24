"""The build brief, faster (Jenna approved 2026-11-23 9:40 PM PT).

The audit of Nov 23 read the brief's model call at 54 to 64 s on every
build and the first answer at 94 to 110 s. Measured on the ask log (8
days to Nov 24, 48 build asks): model stage median 59.9 s, p90 71.2 s;
the content-identity verify (web search) 0 at the median and 35 s at
p90; total median 84.5 s, p90 113.5 s. One real Sonnet 4.6 call on the
live prompt wrote 3,990 output tokens in 63 s: 63 tokens a second, first
token at 1.2 s, no preamble. The rulebook rides cached and is not the
cost; the draft's own size is. Half of it is the audience shape
(persona_notes, tu_demos, avid_demos, extra_rows, subject_rows), half is
the decision, the subject, the sizing, the identity, the windows and the
scope.

So the one draft becomes two parallel calls to the SAME model on the
SAME cached rulebook, each told which keys to write, merged key for key.
Nothing moves to a weaker tier (Haiku on the same prompt broke a rule in
the one probe), no field changes, no rule changes, the deterministic
steps after the model are untouched. A multi-profile ask (the array
shape) or a failed half falls back to the classic single call.
PM_BRIEF_SPLIT=0 restores the single call.

Alongside: the content-identity verify starts while the model runs when
the ask carries a medium or platform word (the exact case the verify
can fire in), for the title the ask plainly carries, and is reused only
when the draft's subject is that title (the preverify pattern). Both
identity research calls run under a time budget; past it the
deterministic floor applies, exactly as on a failure today.

Nothing a reader sees names any of this.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

ENV = 'PM_BRIEF_SPLIT'
IDENTITY_BUDGET_ENV = 'PM_IDENTITY_BUDGET_S'
IDENTITY_BUDGET_S = 60.0

# the audience shape: the half of the draft that reads the subject's real audience; everything else
# (decision, subject, sizing, identity, windows, scope, cuts, notes) is the core
SHAPE_KEYS = ('tu_demos', 'avid_demos', 'subject_rows', 'extra_rows', 'persona_notes')

_CORE_TAIL = (
    "\n\nTHIS CALL WRITES PART ONE OF THE JSON OBJECT (the brief: decision, subject, sizing, identity, windows, scope, cuts, notes, "
    "assumptions and every other field). A parallel call on the same rules writes the audience shape. Write EVERY field of the "
    "JSON object EXCEPT these five, which you must OMIT entirely (do not write them as null or empty): "
    + ", ".join(SHAPE_KEYS) + ". Every rule above stands; return ONLY the JSON object."
)
_SHAPE_TAIL = (
    "\n\nTHIS CALL WRITES PART TWO OF THE JSON OBJECT: the audience shape. Return a JSON object with ONLY these five keys: "
    + ", ".join(SHAPE_KEYS) + ". Resolve the subject and the universe exactly as the rules above say (the subject's AUDIENCE, "
    "never the subject itself; the canonical demographic buckets; the persona and anchor-brand rules; the window as the rules set "
    "it) and reason the shape fully, but write no other key. Keep every length rule exactly as written above (persona_notes 200-500 "
    "words, extra_rows 15-40 rows); write nothing beyond them. A parallel call writes the rest of the brief. Every rule above stands; "
    "return ONLY the JSON object."
)


def enabled() -> bool:
    return str(os.environ.get(ENV, '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def is_multi(text: str, ceiling: int, long_ceiling: int) -> bool:
    """A list of subjects asks for the array shape, which cannot be written in halves."""
    return int(ceiling or 0) >= int(long_ceiling or 0) and int(long_ceiling or 0) > 0


def merge(core: dict, shape: dict) -> dict:
    """The core's fields, with the shape's five keys from the shape call
    (a key the shape call left out keeps the core's value when the core
    wrote one). Keys neither list names come from the core."""
    out = {k: v for k, v in (core or {}).items() if k not in SHAPE_KEYS}
    for k in SHAPE_KEYS:
        if isinstance(shape, dict) and k in shape and shape[k] is not None:
            out[k] = shape[k]
        elif isinstance(core, dict) and k in core:
            out[k] = core[k]
    return out


def _one(agent, *, system_prompt, user_prompt, max_tokens, temperature, model, tail, salvage_arrays):
    t0 = time.monotonic()
    try:
        res = agent(system_prompt=system_prompt, user_prompt=str(user_prompt or '') + tail,
                    max_tokens=max_tokens, temperature=temperature, model=model, salvage_arrays=salvage_arrays)
    except Exception as exc:
        res = {'success': False, 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
    return res, int((time.monotonic() - t0) * 1000)


def draft(agent, *, system_prompt, user_prompt, max_tokens, temperature, model, text='', long_ceiling=0, stage=None, say=None):
    """The brief draft: two parallel calls merged, or the classic single
    call when the split is off, the ask wants the array shape, or a half
    did not come back as one object. Returns the agent's result shape
    ({'success', 'data', 'model', ...}) with 'split' set when the halves
    were used; records `model_core` / `model_shape` stage marks through
    `stage(key, ms=)` when given."""
    single = dict(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens, temperature=temperature,
                  model=model, salvage_arrays=True)
    if not enabled() or is_multi(text, max_tokens, long_ceiling):
        return agent(**single)
    try:
        half = max(4000, min(int(max_tokens or 14000), 14000))
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='pm-brief') as pool:
            f_core = pool.submit(_one, agent, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=half, temperature=temperature,
                                 model=model, tail=_CORE_TAIL, salvage_arrays=False)
            f_shape = pool.submit(_one, agent, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=half, temperature=temperature,
                                  model=model, tail=_SHAPE_TAIL, salvage_arrays=False)
            core, core_ms = f_core.result()
            shape, shape_ms = f_shape.result()
        if callable(stage):
            stage('model_core', ms=core_ms)
            stage('model_shape', ms=shape_ms)
        cd = core.get('data') if isinstance(core, dict) and core.get('success') else None
        sd = shape.get('data') if isinstance(shape, dict) and shape.get('success') else None
        if isinstance(cd, list):
            # the model answered the core with the array shape after all: the classic call owns multi-profile drafts
            cd = None
        if not isinstance(cd, dict) or not cd or not isinstance(sd, dict):
            print(f"[brief-speed] a half did not come back as one object (core {type(cd).__name__}, shape {type(sd).__name__}); classic call")
            if callable(stage):
                stage('model_fallback', ms=0)
            return agent(**single)
        merged = merge(cd, sd)
        out = dict(core)
        out['data'] = merged
        out['split'] = True
        out['shape_model'] = shape.get('model')
        return out
    except Exception:
        traceback.print_exc()
        return agent(**single)


# ------------------------------------------------------------ identity alongside the model
def _norm(s) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


class _Pre(threading.Thread):
    result = None
    subject = ''

    def __init__(self, subject, fn):
        super().__init__(daemon=True)
        self.subject = subject
        self._fn = fn

    def run(self):
        try:
            self.result = self._fn()
        except Exception:
            self.result = None


def identity_prestart(text, host):
    """Start the content-identity verify for the title the ask plainly
    carries while the draft model runs, when the ask carries a medium or
    platform word (the only case the verify can fire in). Returns the
    thread, or None. Never raises."""
    try:
        if os.environ.get('PM_CHAT_SUBJECT_VERIFY', '1') == '0' or os.environ.get('REGRESSION_TEST_MODE') == '1':
            return None
        sig_fn = getattr(host, '_identity_signals_from_prompt', None)
        verify = getattr(host, '_verify_content_identity', None)
        if not callable(sig_fn) or not callable(verify):
            return None
        sig = sig_fn(text) or {}
        if not (sig.get('medium') or sig.get('platform')):
            return None
        guess = str(sig.get('title_guess') or '').strip()
        if not guess:
            try:
                import prometheus_analysis as _pma
                guess = str(_pma.guess_subject_from_text(text) or '').strip()
            except Exception:
                guess = ''
        if len(guess) < 2:
            return None
        provisional = {'subject': guess, 'resolved_title': guess, 'medium': sig.get('medium') or '', 'platform': sig.get('platform') or ''}
        t = _Pre(guess, lambda: verify(provisional, text, sig))
        t.start()
        return t
    except Exception:
        return None


def _budget() -> float:
    try:
        return max(5.0, float(os.environ.get(IDENTITY_BUDGET_ENV) or IDENTITY_BUDGET_S))
    except Exception:
        return IDENTITY_BUDGET_S


def _stash():
    try:
        from flask import g as _g, has_request_context
        if has_request_context():
            return _g
    except Exception:
        pass
    return None


def hold_prestart(thread) -> None:
    """Keep the started verify where the identity step can find it (the request's own g)."""
    g = _stash()
    if g is not None:
        try:
            g._pm_pre_identity = thread
        except Exception:
            pass


def identity_verify(draft, prompt, sig, verify_fn):
    """The content-identity verify, reusing the verify started alongside
    the model when its title is this draft's subject, otherwise running
    `verify_fn(draft, prompt, sig)` now; either way under the budget.
    Past the budget: None, the deterministic floor, as on a failure."""
    budget = _budget()
    subj = _norm(str((draft or {}).get('subject') or '').split(' - ', 1)[0])
    g = _stash()
    pre = getattr(g, '_pm_pre_identity', None) if g is not None else None
    if pre is not None and _norm(getattr(pre, 'subject', '')) == subj and subj:
        try:
            pre.join(timeout=budget)
            if not pre.is_alive():
                return pre.result
            print('[brief-speed] identity verify past its budget; the deterministic floor applies')
            return None
        except Exception:
            pass
    return bounded(lambda: verify_fn(draft, prompt, sig), 'identity', budget=budget)


def bounded(fn, label='research', budget=None):
    """Run `fn()` on its own thread and wait up to the budget; None past
    it (the call finishes on its own and is discarded)."""
    budget = float(budget or _budget())
    box = {}

    def _run():
        try:
            box['r'] = fn()
        except Exception:
            box['r'] = None

    t = threading.Thread(target=_run, daemon=True, name=f'pm-{label}')
    t.start()
    t.join(timeout=budget)
    if t.is_alive():
        print(f"[brief-speed] {label} research past its {int(budget)}s budget; the deterministic floor applies")
        return None
    return box.get('r')


# ------------------------------------------------------------ the approve span (2026-11-24, the ~13 s after the estimate)
# Measured on the first live build order after the split: the span from the estimate mark to the
# response was 14.9 s; two users.json writes (13.7 MB each: a full read, a full put, two local
# copies) sat inside it, the credit charge among them. The charge was already post-queue and
# failure-tolerant ("run continues, usage NOT recorded"), so it moves off the person's wait: it runs
# on its own thread, the response's remaining balance is the pre-charge balance less the price.
_charge_threads = []


def charge_later(fn, label='charge'):
    """Run the charge on its own (non-daemon) thread so the interpreter
    waits for it on shutdown; the failure line prints as before. Returns
    the thread. Under REGRESSION_TEST_MODE the charge runs inline so the
    suites see its effect at once."""
    def _run():
        try:
            ok = fn()
            if ok is False:
                print(f"[synth_chat_run] WARNING: post-queue credit charge failed ({label}) - run continues, usage NOT recorded")
        except Exception:
            traceback.print_exc()
    if os.environ.get('REGRESSION_TEST_MODE') == '1' or os.environ.get('PM_CHARGE_INLINE') == '1':
        _run()
        return None
    t = threading.Thread(target=_run, daemon=False, name=f'pm-{label}')
    t.start()
    _charge_threads.append(t)
    del _charge_threads[:-50]
    return t


def remaining_after(before, price):
    """The balance the approve response reports once the charge lands:
    an unlimited seat stays -1; a balance that covers the price loses it;
    a balance under the price (the wallet pays) keeps its credits."""
    try:
        b = int(before)
    except (TypeError, ValueError):
        return before
    try:
        p = max(0, int(price or 0))
    except (TypeError, ValueError):
        p = 0
    if b < 0:
        return -1
    return b - p if b >= p else b


# ------------------------------------------------------------ users.json once per request
# users.json is 13.7 MB; load_users already answers from an ETag-checked copy, but every call still
# pays the conditional round trip to S3 (about 100 ms from the web host) and a build brief loads it
# eight to ten times (the gate, the funds check, the price table, the quote, the seat, the charge, the
# balance). Within one request the same ETag is confirmed once; later loads reuse the copy. A save in
# this process moves the ETag, so the memo stops matching on its own.
def users_memo_hit(etag) -> bool:
    g = _stash()
    return g is not None and bool(etag) and getattr(g, '_pm_users_etag_seen', None) == etag


def users_memo_mark(etag) -> None:
    g = _stash()
    if g is not None and etag:
        try:
            g._pm_users_etag_seen = etag
        except Exception:
            pass


# ------------------------------------------------------------ a read started early
# The repeat-approval gate asks the engine for its recent runs (400 status documents, 1 MB over HTTP
# from the web host to the build server) after the spec is built. The list depends on nothing in the
# spec, so the approve path starts the read before the spec build and the gate takes the answer when
# it gets there. The answer, or the read's own error, is handed back exactly as if the read had run in
# place, so the gate's fail-open rule is unchanged. One request, one use; a thread with no request
# (the charge) never sees another request's read. Inline under REGRESSION_TEST_MODE.
class Prefetch:
    def __init__(self, fn, label=''):
        self.label, self._r, self._e, self.done = label, None, None, threading.Event()

        def _run():
            try:
                self._r = fn()
            except BaseException as e:      # handed back from get(), never lost
                self._e = e
            finally:
                self.done.set()
        if os.environ.get('REGRESSION_TEST_MODE') == '1' or os.environ.get('PM_PREFETCH_INLINE') == '1':
            _run()
        else:
            threading.Thread(target=_run, daemon=True, name=f'pm-prefetch-{label}').start()

    def get(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError(f'{self.label} read still running after {timeout}s')
        if self._e is not None:
            raise self._e
        return self._r


def prefetch(label, fn):
    """Start ``fn`` now for this request; None when there is no request to hold it."""
    g = _stash()
    if g is None:
        return None
    try:
        p = Prefetch(fn, label)
        setattr(g, f'_pm_prefetch_{label}', p)
        return p
    except Exception:
        return None


def prefetched(label):
    """The read this request started under ``label``, taken once; None when none was started."""
    g = _stash()
    if g is None:
        return None
    p = getattr(g, f'_pm_prefetch_{label}', None)
    if p is not None:
        try:
            delattr(g, f'_pm_prefetch_{label}')
        except Exception:
            pass
    return p


def say_in_hand(spec_draft) -> None:
    """The live line the moment the model returns: the draft is in hand, the checks run next."""
    try:
        from . import progress as _progress
        subj = str((spec_draft or {}).get('subject') or '').split(' - ', 1)[0].strip() if isinstance(spec_draft, dict) else ''
        _progress.say(f"Draft in hand for {subj}. Checking the name, the library and the window." if subj
                      else "Draft in hand. Checking the name, the library and the window.")
    except Exception:
        pass


def say_pricing(go=True) -> None:
    if not go:
        return
    try:
        from . import progress as _progress
        _progress.say("Sizing and pricing the brief.")
    except Exception:
        pass


__all__ = ['enabled', 'is_multi', 'merge', 'draft', 'identity_prestart', 'hold_prestart', 'identity_verify', 'bounded', 'say_in_hand', 'say_pricing', 'charge_later', 'remaining_after', 'users_memo_hit', 'users_memo_mark',
           'Prefetch', 'prefetch', 'prefetched', 'SHAPE_KEYS', 'ENV', 'IDENTITY_BUDGET_ENV']
