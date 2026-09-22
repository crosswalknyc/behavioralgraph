"""How each streaming service reaches its US audience, stated per service.

Jenna 2026-09-22, approving the design this module implements:

  1. A service's MAIN RAIL is that service's WHOLE US audience across
     every distribution path: its own app plus carriage through Amazon
     Prime Video Channels, Apple TV Channels, The Roku Channel and
     YouTube Primetime Channels. "Max" means all Max viewing however
     the viewer reached it.
  2. The distribution mix is EXPLICIT, per service, researched once,
     rather than arrived at by accident inside a per-title call.
  3. PRIME VIDEO's own rail is Prime Video's OWN licensed catalog
     only, never the channels sold through it. A Starz title watched
     inside Prime Video counts to Starz.
  4. An "X on Amazon" breakout rail exists only where the split is
     researchable, and is always strictly inside its parent.

WHY THIS FILE EXISTS
--------------------
The Vampire Diaries ranked implausibly high on the Max rail because a
Prime Video reading landed on the Max row. A title can sit in Prime
Video's own licensed catalog AND on Max as two separate deals, and
nothing in the reasoning said which audience was being counted. The
mix below is the missing statement. It goes into the research prompt
verbatim (`prompt_line`), so every per-title call is told what the
number is supposed to cover before it reasons.

WHAT IS AND IS NOT CLAIMED
--------------------------
`amazon_share` is filled in ONLY where a published per-service figure
exists or can be derived from one. Where a service is demonstrably
carried on Amazon but nobody publishes its split, the field is None
and `basis` says so in words. That is the standing posture in
`.cursor/rules/trends-rankers-never-clickstream.mdc`: a reasoned
estimate anchored to a published figure is the product, a split we
invented because it seemed plausible is not.

Never read off a clickstream. Everything here is public reporting.

A NOTE ON JUSTWATCH
-------------------
JustWatch's catalog is NOT a reliable index of Amazon carriage and is
not used as one here. A literal string match for "amazon channel" in
its package names returns a short and incomplete list, while primary
sources put Starz, Max, AMC+, MGM+, BritBox, Lionsgate+ and
MovieSphere+ on the storefront. Every entry below is sourced from the
service's own filings, earnings, press releases or FAQ, or from
Antenna's published subscription research.

THE INDUSTRY FRAME THE PER-SERVICE READS SIT IN
-----------------------------------------------
  - Antenna, Q1 2025, US premium SVOD gross adds by point of sale:
    direct on the service's own page roughly 45%, Amazon Prime Video
    Channels 24% (21% a year earlier), other channels storefronts
    (The Roku Channel, YouTube Primetime, Hulu, YouTube TV) 9%, the
    balance through the Apple and Google app stores. Antenna's CEO
    gave the rounded version at Amazon's 2025 Engage summit: Prime
    Video Channels is 25% of all US SVOD sign-ups, about half of
    sign-ups are direct, and the rest come through app stores and the
    Roku and YouTube marketplaces.
  - Antenna, "Distribution Dynamics in Specialty SVOD": two of every
    three SPECIALTY SVOD subscriptions it measures come through
    Amazon Channels, against just 9% for premium SVOD services, and
    "only five of the 10 premium SVODs are distributed via Amazon
    Channels, and Netflix and Hulu are almost completely Direct."
    Antenna flags that its specialty direct coverage is understated
    (direct is measured only for Acorn TV, BET+ and BritBox), so the
    two-in-three figure is an upper bound on any one specialty
    service and is never applied as a per-service share here.
  - Antenna, State of Subscriptions Q3 2026 (Q2 2026 data): Amazon
    Channels took 67% of specialty SVOD gross adds, up from 61% a
    year earlier; The Roku Channel 13%, having roughly doubled from
    7% in Q1 2024; YouTube Primetime specialty gross adds up 47% year
    over year. Gross adds are a flow and a subscriber base is a
    stock, and Amazon-sold subscriptions churn faster, so a base
    share always reads below the gross-add share for the same
    service.
  - Amazon Channels rev share runs about 30% on a typical deal, with
    the largest programmers keeping 65% to 75%. A dollar share of a
    service's revenue therefore understates the head count behind it,
    which is the correction applied in the Starz reasoning.
"""

from __future__ import annotations

from typing import NamedTuple, Optional


class ServiceCarriage(NamedTuple):
    """One service's US distribution mix."""

    slug: str
    label: str
    # Paths the service is actually sold through in the US, in the
    # order a reader would name them. Reader-facing wording, because
    # this is what goes into the prompt.
    paths: tuple
    # Share of the service's US streaming AUDIENCE that reaches it
    # through Amazon Prime Video Channels. None means the service is
    # carried there but nobody publishes the split, or the service is
    # not on Amazon at all (see `on_amazon`).
    amazon_share: Optional[float]
    on_amazon: bool
    # True only where a breakout rail is defensible: carried on
    # Amazon, a researched share, and a real non-Amazon path so the
    # child can sit strictly inside the parent.
    breakout: bool
    # Why the numbers above are what they are, and where they came
    # from. Read this before changing one.
    basis: str


_MIX: dict = {
    # ---------------------------------------------------------------
    # No Amazon path at all. Nothing to break out and no carriage
    # component in the main rail beyond the service's own app, its
    # app-store billing and its bundle partners.
    # ---------------------------------------------------------------
    'netflix': ServiceCarriage(
        slug='netflix', label='Netflix',
        paths=('the Netflix app and site', 'the Apple and Google app stores',
               'operator bundles including T-Mobile, Verizon and Comcast'),
        amazon_share=None, on_amazon=False, breakout=False,
        basis=('Netflix has never sold through Prime Video Channels. '
               'Antenna puts Netflix in the direct camp from its first '
               'distribution study and says flatly that "Netflix and Hulu '
               'are almost completely Direct"; Netflix is absent from the '
               'seven premium services Antenna lists on Amazon Channels. '
               'Its non-direct volume is app-store billing and operator '
               'bundles, neither of which is a separate viewing surface.'),
    ),
    'disneyplus': ServiceCarriage(
        slug='disneyplus', label='Disney+',
        paths=('the Disney+ app and site',
               'the Apple and Google app stores',
               'the Disney bundle with Hulu and ESPN',
               'operator bundles including Charter Spectrum'),
        amazon_share=None, on_amazon=False, breakout=False,
        basis=('Disney+ is not sold through Prime Video Channels and is '
               'absent from the seven premium services Antenna lists '
               'there. Antenna groups it with the app-store camp: a '
               'direct relationship augmented by Apple and Google '
               'billing, plus the Disney bundle and the Charter '
               'agreement.'),
    ),
    'hulu': ServiceCarriage(
        slug='hulu', label='Hulu',
        paths=('the Hulu app and site', 'the Apple and Google app stores',
               'the Disney bundle'),
        amazon_share=None, on_amazon=False, breakout=False,
        basis=('Antenna: "Netflix and Hulu are almost completely Direct." '
               'Hulu is itself a channels storefront for other services '
               'and is not sold on Amazon.'),
    ),
    'espnplus': ServiceCarriage(
        slug='espnplus', label='ESPN+',
        paths=('the ESPN app and site', 'the Apple and Google app stores',
               'the Disney bundle'),
        amazon_share=None, on_amazon=False, breakout=False,
        basis=('Not sold through Prime Video Channels and absent from '
               "Antenna's Amazon Channels list. Its non-direct volume is "
               'the Disney bundle.'),
    ),

    # ---------------------------------------------------------------
    # The storefront itself.
    # ---------------------------------------------------------------
    'primevideo': ServiceCarriage(
        slug='primevideo', label='Prime Video',
        paths=("Prime Video's own licensed catalog, included with Prime "
               'or the standalone Prime Video subscription',),
        amazon_share=None, on_amazon=False, breakout=False,
        basis=('Prime Video is the storefront, not a service carried on '
               'one. Its rail is its OWN licensed catalog and Amazon MGM '
               'originals ONLY. The hundred-plus subscriptions sold '
               'inside the Prime Video app (Max, Paramount+, Starz, AMC+, '
               'MGM+, BritBox, Peacock Premium Plus, Lionsgate+, '
               'MovieSphere+ and the rest) are NOT Prime Video viewing '
               'and belong to the service that was subscribed to. This '
               'distinction is the whole point of the carriage model: a '
               'title can sit in both catalogs under two separate deals, '
               'and conflating them is what put a Prime Video reading on '
               'the Max row.'),
    ),

    # ---------------------------------------------------------------
    # Carried on Amazon with a published per-service split.
    # ---------------------------------------------------------------
    'starz': ServiceCarriage(
        slug='starz', label='Starz',
        paths=('the Starz app and site', 'Prime Video Channels',
               'The Roku Channel', 'Apple TV Channels',
               'YouTube Primetime Channels', 'Hulu',
               'operator-sold streaming through Xfinity and DirecTV'),
        amazon_share=0.44, on_amazon=True, breakout=True,
        basis=('Starz Entertainment Form 10-KT filed 2026-02-26: "Starz '
               'generated 29.0% of its revenue from Amazon.com, Inc. and '
               'its subsidiaries" over the nine months to 2025-12-31, '
               'against 29.7% the prior fiscal year. Amazon is the only '
               'distributor Starz has to name under customer '
               'concentration. Nine-month revenue was $963.4M of which '
               'OTT was $654.2M, so the disclosed Amazon dollars are '
               '42.7% of OTT revenue. Amazon sells on wholesale '
               'economics and below the retail card rate, so the head '
               'count behind those dollars sits at or a little above the '
               'dollar share; pulling the other way, management describes '
               'the company as "two-thirds wholesale, one-third retail" '
               'across a base that includes 4.97M linear subscribers, and '
               'the non-Amazon wholesale paths are real. Those bracket '
               'the answer at about 44% of the Starz US streaming '
               'audience. Full working in '
               'scripts/trends_scrapers/starz_amazon.py.'),
    ),
    'paramountplus': ServiceCarriage(
        slug='paramountplus', label='Paramount+',
        paths=('the Paramount+ app and site', 'Prime Video Channels',
               'the Apple and Google app stores',
               'The Roku Channel', 'YouTube Primetime Channels',
               'the Walmart+ bundle', 'operator packages'),
        amazon_share=0.27, on_amazon=True, breakout=True,
        basis=('Antenna, Q1 2025, reported per service: Paramount+ takes '
               '30% of its subscriptions through Amazon Prime Video and '
               '39% direct, with the balance across the app stores, the '
               'other channels storefronts and operator deals. That 30% '
               'is a subscription share on a base Antenna measures '
               'excluding MVPD and telco distribution and some bundles, '
               'and Paramount+ carries a large bundled base outside that '
               'frame, chiefly Paramount+ Essential inside Walmart+. '
               'Widening the denominator to the whole US base pulls the '
               'Amazon share below the measured 30%, and Amazon-sold '
               'subscriptions churn faster than direct ones, so the '
               'viewing share sits a little under the subscription share '
               'again. About 27% of the Paramount+ US streaming audience '
               'reaches it inside Prime Video. Antenna separately '
               'measured the Paramount+ Essential launch on Prime Video '
               'Channels as one of four cases where 89% of sign-ups would '
               'not have happened off the storefront, which is why the '
               'Amazon slice is a real and additive audience rather than '
               'a re-billing of the direct one.'),
    ),

    # ---------------------------------------------------------------
    # Carried on Amazon, split NOT published. Stated in the main
    # rail's scope, never broken out. See `_HELD_BREAKOUTS` below.
    # ---------------------------------------------------------------
    'max': ServiceCarriage(
        slug='max', label='HBO Max',
        paths=('the HBO Max app and site', 'Prime Video Channels',
               'the Apple and Google app stores',
               'operator bundles including Charter Spectrum',
               'Xfinity packages'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('Carriage is certain and the size of it is not. HBO Max is '
               'one of the seven premium services Antenna lists on Amazon '
               'Channels, having left the storefront in 2021 and returned '
               'in December 2022. The published quantities are all event '
               'deltas against an undisclosed US base: Antenna put the '
               '2021 exit at about 5.1M subscribers lost, with fewer than '
               'one in ten resubscribing in the following eight months; '
               'the December 2022 return drove about 3M sign-ups in its '
               'first three months, and Amazon counted about 2.3M over '
               'the following year. Warner Bros. Discovery reports global '
               'subscribers and does not break out a US distribution mix, '
               'and Antenna has published no per-service split for HBO '
               'Max the way it has for Paramount+. So the main rail says '
               'the Amazon path is there and material, and no breakout '
               'rail ships until a share is published.'),
    ),
    'peacock': ServiceCarriage(
        slug='peacock', label='Peacock',
        paths=('the Peacock app and site',
               'the Apple and Google app stores',
               'Prime Video Channels (the ad-free tier only, and only '
               'since August 2025)',
               'Xfinity and Charter Spectrum packages'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('Peacock launched in 2020 without Amazon and only reached '
               'Prime Video Channels on 2025-08-28, when Comcast and '
               'Amazon announced a package of agreements putting Peacock '
               'Premium Plus, the ad-free tier, on the storefront at '
               '$16.99 a month. Comcast reported 41M Peacock subscribers '
               'that quarter with about 80% of them on the ad-supported '
               'Premium tier, so the tier that is on Amazon at all is '
               'roughly a fifth of the base. The last published mix, '
               'Antenna Q1 2025, predates the deal: 55% direct with '
               'iTunes the largest third party at 18%. A share this new '
               'and this narrow is not researchable to a defensible '
               'number, so the main rail names the path and no breakout '
               'ships.'),
    ),
    'amcplus': ServiceCarriage(
        slug='amcplus', label='AMC+',
        paths=('the AMC+ app and site', 'Prime Video Channels',
               'Apple TV Channels', 'The Roku Channel',
               'YouTube Primetime Channels',
               'operator packages including Charter Spectrum, DirecTV '
               'and Philo'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('AMC Networks Form 10-K for FY2025 names the paths and no '
               'shares: AMC+ "is available to subscribers through either '
               'ad-supported or commercial free plans through our '
               'direct-to-consumer applications, as well as through '
               'MVPDs and virtual MVPDs, and digital streaming platforms '
               'such as Amazon Prime Video Channels, Apple TV Channels '
               'and The Roku Channel." Its customer-concentration note '
               'reports one unnamed domestic customer at 18% of '
               'consolidated revenue and does not identify it, so it '
               'cannot be read as the Amazon line the way the Starz '
               'disclosure can. The operator path is large and '
               'specifically disclosed: more than 1.1M Spectrum TV '
               'customers have activated ad-supported AMC+ since launch, '
               'against 10.4M streaming subscribers across all seven AMC '
               'services. Antenna carries AMC+ twice, Direct and '
               'Non-Direct, and names it among the leaders on BOTH The '
               'Roku Channel and YouTube Primetime, so its channels '
               'volume is spread across three storefronts rather than '
               'concentrated on Amazon. The category figure of two in '
               'three specialty subscriptions through Amazon is an upper '
               'bound that Antenna itself flags as overstated, and '
               'applying it to a service this diversified would '
               'overstate Amazon badly. Held.'),
    ),
    'mgmplus': ServiceCarriage(
        slug='mgmplus', label='MGM+',
        paths=('the MGM+ app and site', 'Prime Video Channels',
               'Apple TV Channels', 'The Roku Channel',
               'YouTube Primetime Channels', 'cable and satellite carriage'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('Amazon owns MGM+ outright through the MGM acquisition, '
               'which makes the Prime Video Channels path the natural '
               'one and also means Amazon publishes nothing about it: '
               'MGM+ is inside Amazon results and is never broken out. '
               'The majority of the subscriber base arrives through '
               'cable-bundle carriage rather than any storefront. '
               'Antenna lists MGM+ on Amazon Channels, The Roku Channel '
               'and YouTube Primetime, and names it among the leaders on '
               'the latter two, so the channels volume is spread. No '
               'per-service split is published. Held.'),
    ),
    'britbox': ServiceCarriage(
        slug='britbox', label='BritBox',
        paths=('the BritBox app and site', 'Prime Video Channels',
               'The Roku Channel', 'Apple TV Channels',
               'the Apple and Google app stores'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('BritBox International passed 3.75M subscribers across the '
               'US, Canada, Australia and Scandinavia at the 2024 sale of '
               "ITV's half to BBC Studios, and reported 3.8M shortly "
               'after. Neither owner has published a distribution split '
               'since, and BBC Studios does not file one. Antenna lists '
               'BritBox on Amazon Channels and The Roku Channel and is '
               'one of only three specialty services whose direct '
               'subscriptions Antenna measures at all, which means the '
               'category two-in-three Amazon figure is understated for '
               'the category and still not a BritBox number. Held.'),
    ),

    # ---------------------------------------------------------------
    # Amazon-only in the US. A breakout would equal the parent, which
    # the subset invariant in `derived_rails.child_ceiling` exists to
    # make unrepresentable. See the long note in that module.
    # ---------------------------------------------------------------
    'lionsgateplus': ServiceCarriage(
        slug='lionsgateplus', label='Lionsgate+',
        paths=('Prime Video Channels, the only way to subscribe in the US',),
        amazon_share=1.0, on_amazon=True, breakout=False,
        basis=('The service FAQ answers "how do I subscribe" with "add it '
               'as an additional channel to Amazon Prime Video" and "how '
               'do I cancel" with amazon.com/yms. There is no app of its '
               'own and no second storefront, so its whole US audience '
               'already IS its Amazon audience and the main rail already '
               'IS the Amazon rail. A "Lionsgate+ on Amazon" breakout '
               'would be 100% of its parent on the first render, which '
               'the subset invariant forbids.'),
    ),
    'moviesphereplus': ServiceCarriage(
        slug='moviesphereplus', label='MovieSphere+',
        paths=('Prime Video Channels', 'YouTube Primetime Channels'),
        amazon_share=None, on_amazon=True, breakout=False,
        basis=('Sold through Prime Video Channels and YouTube Primetime '
               'Channels, with no app of its own. Antenna tracks it among '
               'its 31 specialty services on both storefronts and '
               'publishes no count for it. The only US package JustWatch '
               'lists is the Amazon one, which is what the panel is built '
               'from, so this panel already IS the Amazon-carried service '
               'and a breakout would be all or nearly all of its parent.'),
    ),
}


# Services carried on Amazon whose breakout is deliberately NOT
# shipped, and the one sentence that says why. Kept beside the
# registry so a later reader can see the decision without reading
# every `basis` in full, and so a future run can check whether the
# reason still holds.
_HELD_BREAKOUTS = {
    'max':      'carried since December 2022, no published share',
    'peacock':  'ad-free tier only, on the storefront since August 2025, '
                'no published share',
    'amcplus':  'carried on four storefronts plus operators, no published '
                'share, and the category figure would overstate Amazon',
    'mgmplus':  'Amazon-owned and never broken out, majority arrives '
                'through cable carriage',
    'britbox':  'no published split since the 2024 change of ownership',
}


def mix_for(slug: str) -> Optional[ServiceCarriage]:
    return _MIX.get((slug or '').strip())


def is_on_amazon(slug: str) -> bool:
    m = mix_for(slug)
    return bool(m and m.on_amazon)


def amazon_share(slug: str) -> Optional[float]:
    m = mix_for(slug)
    return m.amazon_share if m else None


def breakout_services() -> tuple:
    return tuple(sorted(k for k, m in _MIX.items() if m.breakout))


def held_breakouts() -> dict:
    return dict(_HELD_BREAKOUTS)


def _join(paths: tuple) -> str:
    items = [p for p in paths if p]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def scope_line(slug: str) -> str:
    """The sentence the research prompt carries for this service.

    One line per service, stating what the number is supposed to
    cover. This is the explicit part of the design: the mix is said
    out loud before the model reasons, rather than being left to fall
    out of a per-title call.
    """
    m = mix_for(slug)
    if not m:
        return ''
    if m.slug == 'primevideo':
        return ("SCOPE: Prime Video's OWN licensed catalog and Amazon MGM "
                'originals only. Subscriptions sold inside the Prime Video '
                'app (Max, Paramount+, Starz, AMC+, MGM+, BritBox, Peacock '
                'Premium Plus, Lionsgate+, MovieSphere+ and the rest) are '
                'NOT Prime Video viewing. A title that is on one of those '
                'channels AND in the Prime Video catalog is two separate '
                'deals; count only the Prime Video one here, and return 0 '
                'if the title reaches US viewers only as part of a channel '
                'sold through Amazon.')
    reach = _join(m.paths)
    line = ('SCOPE: the whole US audience for this service however the '
            f'viewer reached it, across {reach}.')
    if m.amazon_share is not None and m.on_amazon and m.slug != 'lionsgateplus':
        pct = int(round(m.amazon_share * 100))
        line += (f' About {pct}% of that audience watches inside Prime '
                 f'Video rather than the app, and this number INCLUDES '
                 f'them.')
    elif m.on_amazon:
        line += (' Viewing that happens inside Prime Video Channels, Apple '
                 'TV Channels, The Roku Channel or YouTube Primetime is '
                 'part of this number, not part of Prime Video.')
    return line


def prime_video_exclusion_note() -> str:
    """The block the streaming prompt carries once, above the per
    service lines. Says the same thing the Prime Video scope line
    says, in the place a model reads before it starts apportioning."""
    return (
        'DISTRIBUTION SCOPE (read before you apportion):\n'
        '  Each service on the list below is ONE number covering that '
        "service's whole US audience, its own app plus everyone who "
        'reaches it through Prime Video Channels, Apple TV Channels, The '
        'Roku Channel or YouTube Primetime Channels.\n'
        '  Prime Video is the exception and is the storefront itself. Its '
        'number is its OWN licensed catalog and Amazon MGM originals '
        'only. Somebody watching Max or Starz or AMC+ inside the Prime '
        'Video app is Max or Starz or AMC+ viewing, never Prime Video '
        'viewing.\n'
        '  A title frequently sits in Prime Video\'s own catalog AND on '
        'another service under a separate deal. Those are two audiences. '
        'Give each service the audience of its own deal and never carry '
        "one service's number across to another.\n"
    )
