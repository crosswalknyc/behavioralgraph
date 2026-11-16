#!/usr/bin/env python3
"""A valuation order's today is the US Eastern date (Jenna 2026-11-13).

Samantha Kolton (CAA), thread 619ae4f806: at 4:40 PM Pacific on
November 12 (00:40 UTC on November 13) she typed "sept 29th  - current"
at the dates step. The window was read back as September 29 to
November 13, 2026, because the order took its day off the UTC clock. It
was still November 12 for her.

Held here:

  A. order_today is the Eastern calendar day, across the UTC midnight
     and across both halves of the year.
  B. Her words at her hour read back a window that ends November 12,
     and the same day is the day the order stores and hands to the run.
  C. The run's own clip uses the same day, so the day shown is the day
     used.
  D. Only the valuation order moved: the house default window is still
     the UTC day, and a caller's own ``today`` still wins.

Hermetic: memory store, a test identity, no charge, no model.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import bpiq_slots as SL  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from migration import bpiq_synthesis as SY  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402

FAIL = 0
N = 0
HER_HOUR = datetime(2026, 11, 13, 0, 40, tzinfo=timezone.utc)      # 7:40 PM Eastern, November 12


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


class _Clock(datetime):
    """datetime whose now() is her hour, for the modules that read the clock."""
    @classmethod
    def now(cls, tz=None):
        return HER_HOUR.astimezone(tz) if tz else HER_HOUR.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return HER_HOUR.replace(tzinfo=None)


class Walk:
    """One thread the way service.ask runs the wizard lane, with NO today
    handed in: the order reads its own clock, as production does."""

    def __init__(self, user='canary:valtoday', tid='t1'):
        self.user, self.tid = user, tid
        self.store = W.MemoryStore()
        self.hist = []
        self.charged = []
        self.started = []
        self.registry = MemoryRegistry()

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        mine = W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid)
        out = None
        if mine:
            out = W.analyze_hook(text, body, list(self.hist), self.user, {}, self.user, {},
                                 first=W.is_start(text), store=self.store,
                                 charge_fn=lambda *a: self.charged.append(a) or True,
                                 start_fn=lambda *a: self.started.append(a), registry=self.registry)
        self.hist.append({'role': 'user', 'text': text})
        if out is not None:
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])]}})
        return out

    def draft(self):
        return self.store.load(self.user, self.tid) or {}


def main():
    print('A. the order day is the Eastern calendar day')
    check('00:40 UTC on November 13 is November 12', SL.order_today(HER_HOUR) == date(2026, 11, 12))
    check('04:59 UTC is still the day before (standard time)', SL.order_today(datetime(2026, 11, 13, 4, 59, tzinfo=timezone.utc)) == date(2026, 11, 12))
    check('05:00 UTC is the new day (standard time)', SL.order_today(datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)) == date(2026, 11, 13))
    check('03:59 UTC in July is still the day before (daylight time)', SL.order_today(datetime(2026, 7, 1, 3, 59, tzinfo=timezone.utc)) == date(2026, 6, 30))
    check('04:00 UTC in July is the new day (daylight time)', SL.order_today(datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)) == date(2026, 7, 1))
    check('a clock time with no zone is read as UTC', SL.order_today(datetime(2026, 11, 13, 0, 40)) == date(2026, 11, 12))
    check('midday is the same day on both clocks', SL.order_today(datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)) == date(2026, 11, 12))
    check('the order day is never ahead of the UTC day', SL.order_today() <= datetime.now(timezone.utc).date())

    print('B. her words at her hour')
    with mock.patch.object(SL, 'datetime', _Clock):
        check('the wizard reads the same clock', W._today() == date(2026, 11, 12))
        w = Walk()
        w.say('Value a brand partnership')
        w.say('Matchbox the Film on Apple TV is the property and Leatherman is the brand')
        out = w.say('sept 29th  - current')
        r = (out or {}).get('reply') or ''
        d = w.draft()
        check('the window is read back', 'September 29' in r)
        check('it ends November 12, her day', 'November 12, 2026' in r)
        check('November 13 is nowhere in the reply', 'November 13' not in r)
        check('the order stores the same day as the campaign end', str(d.get('campaign_end'))[:10] == '2026-11-12')
        check('and as the end of the window it will run on', str(d.get('attr_end'))[:10] == '2026-11-12')
        w.say('correct')
        w.say('No')
        w.say('Find the assets')
        d = w.draft()
        check('the day is unchanged at the quote', str(d.get('attr_end'))[:10] == '2026-11-12')
        # a campaign that ended last week: plus 30 days stops at the order's today, in the same words
        a, b = W.attribution_window('2026-10-01', '2026-11-05')
        check('a window that would run past today stops at the order day', (a, b) == ('2026-10-01', '2026-11-12'))
        line = W.attr_reply({'campaign_start': '2026-10-01', 'campaign_end': '2026-11-05', 'attr_start': a, 'attr_end': b,
                             'talent': 'A', 'brand': 'B'})
        line = line if isinstance(line, str) else str((line or {}).get('reply') or line)
        check('the line that says so names the same day', 'through November 12, 2026' in line and 'November 13' not in line)

        print('C. the run clips to the same day')
        win = SY._dates({'event_start': '2026-09-29', 'event_end': '2026-11-12', 'post_start': '2026-09-29', 'post_end': '2026-11-12'})
        check('the window the run uses ends November 12', win['post']['end'] == '2026-11-12' and win['event']['end'] == '2026-11-12')
        check('nothing was clipped: the day shown is the day used', win['clipped'] is False)
        win2 = SY._dates({'event_start': '2026-09-29', 'event_end': '2026-11-05'})
        check('a run with no end named stops on the order day, not the UTC day', win2['post']['end'] == '2026-11-12')
        win3 = SY._dates({'event_start': '2026-09-29', 'event_end': '2026-11-05', 'post_end': '2026-11-13'})
        check('a day past the order day is clipped back to it', win3['post']['end'] == '2026-11-12' and win3['clipped'] is True)

    print('D. only the valuation order moved')
    check("a caller's own today still wins", W._today(date(2026, 11, 13)) == date(2026, 11, 13) and W._today('2026-11-13') == date(2026, 11, 13))
    try:
        from migration import event_window as EW
        src = Path(EW.__file__).read_text()
        check('the house default window is still the UTC day', 'order_today' not in src and 'America/New_York' not in src)
    except Exception as e:  # the module is always present; a failure here is a finding
        check(f'event_window loads ({e})', False)
    src_inline = (WEB / 'prometheus' / 'inline_dates.py').read_text()
    check('inline date reading elsewhere is unchanged', 'order_today' not in src_inline)

    print(f"\n{N - FAIL}/{N} checks passed")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
