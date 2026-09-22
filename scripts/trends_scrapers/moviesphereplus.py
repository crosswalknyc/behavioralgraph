"""
MovieSphere+ trending scraper.

Jenna 2026-09-22: "for streaming let's add streaming services such as
MovieSphere+, and Lionsgate+". Both shipped; Lionsgate+ lives in
`lionsgateplus.py`. An earlier note here said Lionsgate+ had no US
service, which was wrong: it sells in the US at $6.99 through Prime
Video Channels. The catalog source carries no package for it, which
is what that claim was really describing.

MovieSphere+ is Lionsgate's ad-free on-demand subscription drawn from
the studio's roughly 20,000-title library. It launched in the US on
Prime Video Channels in May 2026 at $4.99 a month and reads the way
that lineage suggests: Lionsgate features (Django Unchained, Kill
Bill, the Hunger Games and John Wick rotations, the Saw and Evil Dead
horror catalog) alongside Lionsgate TV (Mad Men, Weeds, Nashville,
Black Sails, Party Down, Ash vs Evil Dead, Blue Mountain State).

Do not confuse it with its two free siblings. MovieSphere is the
24/7 ad-supported channel that runs on roughly twenty FAST platforms,
and MovieSphere Gold is the over-the-air digital network carried in
30M+ homes. Neither is this service and neither one's reach transfers
to it; borrowing those numbers would be exactly the total-brand-reach
mistake the audience rules rule out.

THIS IS A STANDALONE SERVICE TAB, NOT A PARENT WITH A BREAKOUT
--------------------------------------------------------------
Starz is the shape where a service tab has a distribution-path child:
the Starz tab is the whole service and "Starz on Amazon" is an
enforced subset of it, always strictly below, computed from the parent
at render time (`scripts/trends_scrapers/derived_rails.py`).

MovieSphere+ IS NOT THAT SHAPE, and a "MovieSphere+ on Amazon" rail
must never be added. Its US carriage is Prime Video Channels plus
YouTube Primetime Channels, JustWatch lists exactly one US package for
it (the Amazon one, below), and it has no app of its own to be sold
around. An Amazon breakout would therefore be all or nearly all of its
parent, which is the one thing a subset rail may never be: the subset
invariant requires the child to sit strictly below the parent, and a
child that is its parent violates it on the first render. If the
question ever comes up again, the answer is that this panel already IS
the Amazon-carried service, which is why the scope label says so.

JustWatch US package codes (verified against the live `packages`
query 2026-09-22):

    mse  id 2445  MovieSphere+ Amazon Channel  FLATRATE  <- used
        technicalName `amazontribecashortlist`, a leftover from the
        Amazon channel slot's Tribeca Shortlist days. The only
        MovieSphere package JustWatch carries for the US; there is
        no standalone one, because in the US there is no standalone
        service to carry.

Deliberately NOT unioned in:

    cev  id 2704  Cineverse Amazon Channel  FLATRATE
    clt  id 2079  Cineverse LiveTV          FAST
        Cineverse is a different company with a different catalog.
        These sit next to MovieSphere+ in the Amazon Channels
        storefront and nowhere else, so they are neighbours, not
        tiers, and unioning either one would blend two companies'
        libraries into one panel.

Same JustWatch path as Paramount+, Peacock and AMC+: public GraphQL,
no cookies, no datacenter-IP fingerprinting, so this runs from
Hetzner in the daily `run_all` batch with no residential hop. See
`_justwatch_svod.py` for the shared fetch, the degrade ladder, and the
never-ship-empty posture.

Depth is what the source gives. On 2026-09-22 that was 100 films (the
per-query limit) and 48 shows (the full US show catalog JustWatch
carries for the package). The 48 is a real source ceiling in the same
way BritBox's 62 films and MGM+'s 54 shows are, and it is reported
rather than padded.

`streaming_depth.py` deliberately does not cover this slug, for the
same reason it skips Paramount+ and Peacock: this scraper already
pulls its depth straight from JustWatch, so a second pass would only
re-fetch what is already here.

Audience anchors live in the `moviesphereplus` entry of
`scripts/trends_scrapers/stream_estimates.py::_STREAMING_PLATFORMS_META`.

Standalone:
    python3 -m scripts.trends_scrapers.moviesphereplus
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._justwatch_svod import fetch_svod_platform

logger = logging.getLogger(__name__)


SLUG     = 'moviesphereplus'
LABEL    = 'MovieSphere+'
PACKAGES = ['mse']


def fetch() -> dict[str, Any]:
    return fetch_svod_platform(SLUG, LABEL, PACKAGES)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
