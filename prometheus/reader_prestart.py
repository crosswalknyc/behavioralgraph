"""The Reader's call, started while the door walks its lanes (Jenna's
standing ask, 2026-11-24: faster).

Measured on the live build asks of 2026-11-24: the door spent 3.2 to
3.9 s in the lanes ahead of the reader lane (the store reads behind
them) and the Reader itself 4.4 to 4.9 s, one after the other. The
Reader reads the ask's own words, the page context, the thread and the
seat; none of the lanes ahead of it change those (the wayfinding lane
can rewrite the words, and then the early call is not used). So the
call starts here, on its own thread, as soon as those inputs are
settled, and the reader lane takes its answer when it gets there.

What does not change: the reading (the same inputs, the same call, the
same model), when the Reader runs at all (the lane's own guard still
decides; an early call a guard rules out is left to finish and is
never read), one call per ask (an early call that came back empty for
the same inputs is the lane's answer, as a single inline call would
have been), the seat's attribution on the spend (captured on the
request thread and handed to the call), and the live progress line
(said on the request thread). Under REGRESSION_TEST_MODE there is no
early call unless PM_READER_PRESTART_FORCE=1: the suites and the replay
see the one Reader call at the lane, in the order they always did, and
the prestart has its own suite. Off with PM_READER_PRESTART=0.
"""
from __future__ import annotations

import json
import os
import threading
import time

__all__ = ['enabled', 'start', 'Prestart']


def enabled() -> bool:
    return str(os.environ.get('PM_READER_PRESTART', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _snap(ctx, history) -> str:
    try:
        return json.dumps([ctx, history], sort_keys=True, default=str)
    except Exception:
        return ''


class Prestart:
    """One Reader call in flight for one ask."""

    def __init__(self, text, ctx, history, user, uname, read, claude=None):
        self.text = str(text or '')
        self._snap = _snap(ctx, history)
        self.lines = []            # the progress lines the Reader would have said, replayed on the request thread
        self.result = None
        self.error = None
        self.started = time.monotonic()
        self.done = threading.Event()
        self.used = False

        def _progress(label, *a, **k):
            self.lines.append(str(label or ''))

        def _run():
            try:
                self.result = read(text, ctx, history, user, uname, claude=claude, progress=_progress)
            except BaseException as e:      # handed to the lane exactly as an inline call would have raised
                self.error = e
            finally:
                self.done.set()
        threading.Thread(target=_run, daemon=True, name='pm-reader-prestart').start()

    def matches(self, text, ctx, history) -> bool:
        """True when the words, the page context and the thread the lane
        has are the ones the early call read."""
        return str(text or '') == self.text and _snap(ctx, history) == self._snap

    def take(self, say=None):
        """The early call's reading (waiting for it as the lane would
        have waited for its own call). Raises what the call raised."""
        self.used = True
        self.done.wait()
        if callable(say):
            for label in self.lines[1:]:    # the first line was said at prestart
                try:
                    say(label)
                except Exception:
                    pass
        if self.error is not None:
            raise self.error
        return self.result


def start(text, ctx, history, user, uname, *, read, say=None, extras=None, claude=None):
    """Start the Reader for this ask now. Returns the Prestart, or None
    when the switch is off or the words are too short for the Reader.
    `extras` is the seat's attribution captured on the request thread;
    it rides the call so the spend stays under the seat."""
    if not enabled():
        return None
    if os.environ.get('REGRESSION_TEST_MODE') == '1' and os.environ.get('PM_READER_PRESTART_FORCE') != '1':
        return None
    if len(str(text or '').split()) < 2:
        return None
    if claude is None:
        try:
            from .host import host as _h
            base = _h.claude_data if _h.has('claude_data') else None
        except Exception:
            base = None
        if base is not None:
            _ex = dict(extras or {})

            def claude(system, user_prompt, **kw):
                if _ex and 'usage_extras' not in kw:
                    kw = dict(kw, usage_extras=_ex)
                return base(system, user_prompt, **kw)
    if callable(say):
        try:
            say('Reading your question')
        except Exception:
            pass
    try:
        return Prestart(text, ctx, history, user, uname, read, claude=claude)
    except Exception:
        return None
