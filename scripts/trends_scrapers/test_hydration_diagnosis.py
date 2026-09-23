#!/usr/bin/env python3
"""Regression: a hydration timeout only claims what it can prove.

Covers the 2026-09-23 defect. Amazon Music started diverting every
navigation to a household profile chooser, the row selector never
appeared, and the scraper reported "cookies likely expired, re-donate"
and fired the cookie-gap email. The cookies were twelve minutes old.
An operator re-donated a working session for nothing, twice.

The invariants that stop that coming back:

  * three causes, three verdicts: nothing donated, session bounced to
    a sign-in wall, session accepted but the page changed
  * only the sign-in bounce and the missing donation notify
  * the page-changed message names the URL and the selector, and says
    plainly that re-donating will not help
  * a page that merely contains the words "sign in" is not a rejected
    session, and a signed-in marker vetoes the sign-in verdict
  * the Amazon profile chooser is recognised by URL and cleared by
    picking a profile rather than by clicking "Add profile"

No network. Pure functions plus a stub page.

    python3 -m scripts.trends_scrapers.test_hydration_diagnosis
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers._base import (  # noqa: E402
    HYDRATION_NO_COOKIES, HYDRATION_PAGE_CHANGED, HYDRATION_SESSION_REJECTED,
    classify_hydration_failure)
from scripts.trends_scrapers import _amazon_music as am  # noqa: E402

FAILURES: list = []

URL = 'https://music.amazon.com/playlists/B01M11SBC8'
SEL = 'music-image-row[primary-text]'


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


class StubPage:
    """Minimal stand-in for a Playwright page on the profile chooser."""

    def __init__(self, labels, clears_on):
        self.url = ('https://music.amazon.com/identity/who-is-listening'
                    '?returnTo=https%3A%2F%2Fmusic.amazon.com%2F')
        self._labels = labels
        self._clears_on = clears_on
        self.clicked: list = []

    def evaluate(self, _js):
        return self._labels

    def wait_for_timeout(self, _ms):
        pass

    def get_by_text(self, text, exact=False):
        page = self

        class Loc:
            def count(self):
                return 1 if text in page._labels else 0

            @property
            def first(self):
                return self

            def click(self, timeout=None):
                page.clicked.append(text)
                if text == page._clears_on:
                    page.url = 'https://music.amazon.com/'
        return Loc()


def main() -> int:
    # 1. Nothing donated.
    kind, msg, notify = classify_hydration_failure(
        target_url=URL, selector=SEL, cookie_count=0)
    check(kind == HYDRATION_NO_COOKIES, 'no cookies reads as no_cookies')
    check(notify is True, 'no cookies notifies')

    # 2. Session bounced to a sign-in wall.
    kind, msg, notify = classify_hydration_failure(
        target_url=URL, selector=SEL, cookie_count=28,
        final_url='https://www.amazon.com/ap/signin?openid.return_to=x',
        page_text='Sign in\nEnter your password')
    check(kind == HYDRATION_SESSION_REJECTED,
          'sign-in redirect reads as session_rejected')
    check(notify is True, 'session_rejected notifies')
    check('re-donate' in msg.lower(),
          'session_rejected is the only verdict that asks for a re-donation')

    # 3. Session accepted, page changed. This is the Amazon case.
    kind, msg, notify = classify_hydration_failure(
        target_url=URL, selector=SEL, cookie_count=28,
        final_url=('https://music.amazon.com/identity/who-is-listening'
                   '?returnTo=x'),
        page_text='Choose a profile\nChoose an existing profile or add a new one\njenna',
        signed_in_markers=am.SIGNED_IN_MARKERS)
    check(kind == HYDRATION_PAGE_CHANGED,
          'a rendered page that lacks the selector reads as page_changed')
    check(notify is False, 'page_changed does not fire the cookie-gap email')
    check(URL in msg and SEL in msg,
          'page_changed names the URL and the selector')
    check('re-donat' in msg.lower() and 'not' in msg.lower(),
          'page_changed says re-donating will not fix it')

    # 4. A nav-bar "Sign in" link is not evidence of anything.
    kind, _msg, notify = classify_hydration_failure(
        target_url=URL, selector=SEL, cookie_count=28,
        final_url=URL, page_text='Home Podcasts Library Sign in')
    check(kind == HYDRATION_PAGE_CHANGED,
          'the words "sign in" alone do not make a rejected session')
    check(notify is False, 'that case does not notify either')

    # 5. A signed-in marker vetoes a sign-in verdict.
    kind, _msg, _n = classify_hydration_failure(
        target_url=URL, selector=SEL, cookie_count=28, final_url=URL,
        page_text='Library Podcasts Forgot your password',
        signed_in_markers=am.SIGNED_IN_MARKERS)
    check(kind == HYDRATION_PAGE_CHANGED,
          'a signed-in marker outranks sign-in form text')

    # 6. The chooser is recognised and cleared by picking a profile.
    page = StubPage(['jenna', 'Add profile'], clears_on='jenna')
    check(am.on_profile_picker(page), 'the chooser URL is recognised')
    dismissed, note = am.dismiss_profile_picker(page, settle_ms=0)
    check(dismissed is True, 'the chooser is cleared')
    check(page.clicked == ['jenna'],
          'a profile is picked, never the "Add profile" button')
    check('jenna' in note, 'the note names the profile that was picked')

    # 7. Off the chooser, the helper is a no-op.
    page.url = 'https://music.amazon.com/podcasts'
    dismissed, note = am.dismiss_profile_picker(page, settle_ms=0)
    check(dismissed is False and note == 'not_on_picker',
          'the helper no-ops when there is no chooser')

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
