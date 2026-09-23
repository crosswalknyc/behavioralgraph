"""
Shared Amazon Music session helper.

Amazon Music added household profiles, and every navigation on a
signed-in account is intercepted by a profile chooser at

    https://music.amazon.com/identity/who-is-listening?returnTo=<url>

until a profile is picked. The chooser is served on the same
`music-app` shell as the real pages, returns HTTP 200, and renders
`music-image-row` skeletons with no `primary-text`, so a scraper that
waits on `music-image-row[primary-text]` sits there until its timeout
and then reports a dead session. The session is fine. It just never
got past the chooser.

Both the Amazon Music playlist scrape (`music_charts`) and the Amazon
Music podcasts scrape (`podcast_charts`) run on the same donated
`music.amazon.com` cookies and hit the same wall, so the dismissal
lives here rather than in either one.

Picking a profile writes the `av-profile` cookie into the live browser
context, so one dismissal covers every later navigation in that
context. We still re-check after each navigation because Amazon puts
the chooser back whenever the profile cookie is dropped or rotated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Substring of the chooser URL. Amazon keeps the real destination in
# `?returnTo=`, so this matches regardless of where we were headed.
PROFILE_PICKER_MARKER = '/identity/who-is-listening'

# The chooser's own buttons. Everything else on the card is a profile.
_NON_PROFILE_LABELS = {'add profile', 'add a new one', 'manage profiles',
                       'done', 'cancel', 'edit'}

# Text that only a signed-in Amazon Music shell renders. Passed to
# `classify_hydration_failure` so a real page that happens to carry the
# words "sign in" is never misread as a rejected session.
SIGNED_IN_MARKERS = ('choose a profile', 'library', 'podcasts')


def on_profile_picker(page) -> bool:
    """True when the current navigation was intercepted by the chooser."""
    try:
        return PROFILE_PICKER_MARKER in (page.url or '')
    except Exception:
        return False


def _read_picker_labels(page) -> list[str]:
    """Text of every clickable on the chooser, light DOM only, which is
    where Amazon puts the profile buttons."""
    try:
        return page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('button, music-link, a').forEach((el) => {
                const t = (el.innerText || '').trim();
                if (t) out.push(t);
            });
            return out;
        }""") or []
    except Exception:
        return []


def dismiss_profile_picker(page, *, settle_ms: int = 6000,
                           wait_ms: int = 24000) -> tuple[bool, str]:
    """Pick the first real profile so navigation can continue.

    Returns `(dismissed, note)`. `dismissed` is False both when we were
    never on the chooser (note `not_on_picker`) and when we were but
    could not get past it, which is a genuine failure worth logging.
    Never raises.

    The chooser paints a "Tuning in" shell first and only renders its
    profile buttons once the identity call returns, which took about
    ten seconds from the build server and under four from a home
    connection. Reading the buttons once is therefore a race that the
    slower host loses, so poll until they appear.
    """
    if not on_profile_picker(page):
        return False, 'not_on_picker'

    labels: list[str] = []
    candidates: list[str] = []
    waited = 0
    while True:
        labels = _read_picker_labels(page)
        candidates = [t for t in labels
                      if t.strip().lower() not in _NON_PROFILE_LABELS]
        if candidates or waited >= wait_ms:
            break
        if not on_profile_picker(page):
            return True, 'picker_cleared_itself'
        try:
            page.wait_for_timeout(1500)
        except Exception:
            break
        waited += 1500

    if not candidates:
        return False, (f'picker_had_no_profile_button after {waited}ms '
                       f'(saw {labels[:6]})')

    for label in candidates[:3]:
        try:
            loc = page.get_by_text(label, exact=True)
            if loc.count() == 0:
                continue
            loc.first.click(timeout=5000)
            page.wait_for_timeout(settle_ms)
            if not on_profile_picker(page):
                logger.info("amazon music: picked profile '%s', now at %s",
                            label, page.url)
                return True, f'picked:{label}'
        except Exception as e:
            logger.info("amazon music: profile click '%s' failed: %s",
                        label, e)
            continue

    return False, f'picker_click_did_not_clear (tried {candidates[:3]})'


def goto_past_picker(page, url: str, *, timeout_ms: int = 45000,
                     settle_ms: int = 6000) -> str:
    """`page.goto(url)` that lands on the page the caller actually asked
    for. If Amazon diverts to the chooser, pick a profile and retry the
    navigation once. Returns the final URL.
    """
    page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
    page.wait_for_timeout(settle_ms)
    if on_profile_picker(page):
        dismissed, note = dismiss_profile_picker(page, settle_ms=settle_ms)
        logger.info("amazon music: %s diverted to the profile chooser (%s)",
                    url, note)
        if dismissed:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)
    return page.url or url
