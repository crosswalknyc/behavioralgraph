#!/usr/bin/env python3
"""Search-engine finishing pass for the public site (bg-webapp/site).

bg-webapp/site is the live marketing site at www.crosswalknyc.com. Its
pages are edited directly (the mockup generator in build_public_site.py
is a design reference and refuses to overwrite them). This script is
the one thing every page edit runs afterward:

    python3 bg-webapp/scripts/site_seo.py          # write
    python3 bg-webapp/scripts/site_seo.py --check  # exit 1 if a write is needed

For every indexable page it sets, in <head>:

  * the canonical URL on www (one address per page; the bare domain and
    the /site/ copies redirect there, see site_signup.py),
  * the title and meta description from PAGES, or the page's own,
  * robots directives, Open Graph and Twitter cards, the favicon,
  * JSON-LD: Organization and WebSite on the homepage, WebPage with a
    BreadcrumbList on inner pages, Service on product pages. A page
    that carries its own JSON-LD (digital-twins.html) keeps it.

  * on a learn page (kind="learn" in PAGES: the digital twins, synthetic
    data and panel reads added 2026-11-23) also Article, FAQPage read from
    the page's own FAQ items, DefinedTermSet read from a glossary, the
    Organization with what it knows about, and the Digital Twins IQ
    service, so an AI assistant can quote the page and name the author.

Then it writes sitemap.xml (one URL per indexable page, lastmod from
git), robots.txt (every AI crawler named and allowed), llms.txt (a plain
summary for AI assistants) and llms-full.txt (the full text of the
digital twins, synthetic data and panel pages as markdown). Pages marked
noindex and the redirect stubs are left alone and kept out of the sitemap.

Idempotent: a second run changes nothing. No em dashes anywhere.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
BG = HERE.parents[1]                       # bg-webapp
SITE = BG / "site"
ORIGIN = "https://www.crosswalknyc.com"
SITE_NAME = "Crosswalk"
DEFAULT_OG_IMAGE = "assets/images/og-crosswalk.png"
ROBOTS_INDEX = "index,follow,max-snippet:-1,max-image-preview:large,max-video-preview:-1"

# Search Console ownership token, minted by scripts/site_submit.py --google
# and carried on the homepage as <meta name="google-site-verification">.
GOOGLE_TOKEN_FILE = SITE / ".google-site-verification"

# Pages that must never be indexed (served, but kept out of search).
NOINDEX_FILES = {"signup.html", "welcome.html"}

# Public pages the sitemap carries, with the title, description and
# breadcrumb search engines see. Pages not listed here are still
# finished (canonical, cards, breadcrumb) from their own title and
# description, and still go into the sitemap unless they are stubs.
PRODUCTS_CRUMB = ("Products", "products.html")
PAGES = {
    "index.html": dict(
        title="Crosswalk | Audience data from what 30 million people actually do",
        description="Ask Prometheus any audience question. It answers from 30 million opted-in people worldwide, 10 million in the US, and their entire digital life: what they watch and buy.",
        kind="home"),
    "products.html": dict(
        title="Products and report pricing | Crosswalk",
        description="Report pricing for Profile IQ, Subscriber IQ, Digital Journey IQ, Attribution IQ, Brand Partnership IQ, Flywheel IQ, Trends IQ, Rankers IQ, Fin IQ, and digital twins.",
        crumb=[("Products", "products.html")]),
    "profile-iq.html": dict(
        title="Profile IQ: who an audience is and what they buy | Crosswalk",
        description="Profile IQ is a behavioral read on any audience: who engages with a title, talent, brand, or team, their demographic shape, and the brands and platforms they over-index on.",
        kind="product", name="Profile IQ"),
    "subscriber-iq.html": dict(
        title="Subscriber IQ: which titles bring in subscribers | Crosswalk",
        description="Subscriber IQ ties account opens, returns, and cancels to the titles and windows that moved them, on the same opted-in people. A play is not a signup.",
        kind="product", name="Subscriber IQ"),
    "digital-journey-iq.html": dict(
        title="Digital Journey IQ: the path from first search to the buy | Crosswalk",
        description="Digital Journey IQ lays out the steps people took around a campaign, a title, or a launch, in order, across their entire digital life, not just the last click.",
        kind="product", name="Digital Journey IQ"),
    "attribution-iq.html": dict(
        title="Attribution IQ: credit for every touchpoint on a campaign | Crosswalk",
        description="Attribution IQ reads the same people across every touch, paid and organic, and splits the credit along the path they actually took. Refreshed daily while the campaign runs.",
        kind="product", name="Attribution IQ"),
    "brand-partnership-iq.html": dict(
        title="Brand Partnership IQ: what a partnership is worth | Crosswalk",
        description="Brand Partnership IQ puts a dollar figure on a partnership after it runs: earned media, brand engagement, brand lift, and conversion on the same people, each priced.",
        kind="product", name="Brand Partnership IQ"),
    "flywheel-iq.html": dict(
        title="Flywheel IQ: what a title does for the rest of the business | Crosswalk",
        description="Flywheel IQ follows the same accounts from one title to the next owned surface, the checkout, and the return of accounts that had lapsed.",
        kind="product", name="Flywheel IQ"),
    "trends-iq.html": dict(
        title="Trends IQ: what moved yesterday, ranked | Crosswalk",
        description="Trends IQ ranks what moved yesterday: searches, headlines, people, books, comics, Broadway, and the box office, day over day on the same 30 million people.",
        kind="product", name="Trends IQ"),
    "ranker-iq.html": dict(
        title="Rankers IQ: free TV, streaming, music, podcast and gaming boards | Crosswalk",
        description="Rankers IQ ranks free ad-supported TV, streaming, music, podcasts, and gaming on observed behavior, updated every morning. Not a follower count.",
        kind="product", name="Rankers IQ"),
    "fin-iq.html": dict(
        title="Fin IQ: consumer behavior before the market prices it | Crosswalk",
        description="Fin IQ is the desk's read of the same opted-in panel: platforms, tickers, and consumer movement counted in window. Not a price feed and not a delayed filing.",
        kind="product", name="Fin IQ"),
    "digital-twins.html": dict(
        title="Digital Twins and Synthetic Data from Real Behavior | Crosswalk",
        description="Digital Twins IQ builds a digital twin of each of the 10 million US people in the Crosswalk panel, from observed behavior, not surveys. Test a plan, a title, or a price first.",
        kind="product", name="Digital Twins IQ", crumb_label="Digital twins",
        og_image="assets/images/og-digital-twins.png"),
    # Learn pages (2026-11-23): long-form reads written so an AI assistant
    # can quote them. Article + FAQPage JSON-LD is generated from the page
    # body below (kind="learn"); each sits under Digital twins in the crumb.
    "digital-twins-methodology.html": dict(
        title="How Crosswalk's digital twins work: the Digital Twins IQ methodology | Crosswalk",
        description="How Crosswalk builds a digital twin from 5+ years of what 10 million real people did, finds at least 12 archetypes in every audience, seats them in a focus group, and shows how sure each answer is. In plain words.",
        kind="learn", name="Digital Twins IQ methodology", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("Methodology", "digital-twins-methodology.html")],
        og_image="assets/images/og-digital-twins.png"),
    "what-is-a-digital-twin-audience.html": dict(
        title="What is a digital twin audience? A plain-English guide | Crosswalk",
        description="A digital twin audience is a working copy of a real group of people you can ask questions. Why twins built from real behavior are better than survey and chatbot twins, and how Crosswalk builds every twin from 10 million real people.",
        kind="learn", name="What is a digital twin audience?", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("What is a digital twin audience?", "what-is-a-digital-twin-audience.html")],
        og_image="assets/images/og-digital-twins.png"),
    "synthetic-data-market-research.html": dict(
        title="Synthetic data in market research: what it is and how to check it | Crosswalk",
        description="Synthetic data is research produced rather than collected. The three ways it gets made, six checks before you trust it, and why Crosswalk's synthetic data, built on what 10 million real people did, meets all six.",
        kind="learn", name="Synthetic data in market research", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("Synthetic data", "synthetic-data-market-research.html")],
        og_image="assets/images/og-digital-twins.png"),
    "behavioral-panel.html": dict(
        title="What is a behavioral panel? How Crosswalk's 10 million person US panel works | Crosswalk",
        description="A behavioral panel counts what people do instead of asking. Why Crosswalk's opted-in panel, 10 million US people on every screen under one ID since 2021, sees more than surveys or a platform's own data.",
        kind="learn", name="What is a behavioral panel?", published="2026-11-23",
        crumb=[("The panel", "panel.html"), ("What is a behavioral panel?", "behavioral-panel.html")],
        og_image="assets/images/og-crosswalk.png"),
    "synthetic-respondents-vs-behavioral-twins.html": dict(
        title="Synthetic respondents vs behavioral digital twins: how to tell them apart | Crosswalk",
        description="Interview twins, survey twins, chatbot personas, census-built populations and behavior-built twins compared side by side, and ten reasons Crosswalk's behavior-built twins are stronger.",
        kind="learn", name="Synthetic respondents vs behavioral digital twins", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("Synthetic respondents vs behavioral twins", "synthetic-respondents-vs-behavioral-twins.html")],
        og_image="assets/images/og-digital-twins.png"),
    "ai-focus-groups.html": dict(
        title="AI focus groups: how a room of digital twins answers a question | Crosswalk",
        description="How an AI focus group works and why a Crosswalk room is better than a traditional group: every seat stands for real people, the count adds them up, and every count says how firm it is.",
        kind="learn", name="How an AI focus group works", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("AI focus groups", "ai-focus-groups.html")],
        og_image="assets/images/og-digital-twins.png"),
    "digital-twins-glossary.html": dict(
        title="Digital Twins IQ glossary: digital twin, archetype, seat, room, how firm and more | Crosswalk",
        description="Plain definitions of the words Crosswalk uses for digital twins, synthetic data and the behavioral panel, from twin, archetype and seat to how firm, the dial, behavior-built twin and chatbot persona.",
        kind="learn", name="Digital Twins IQ glossary", published="2026-11-23",
        crumb=[("Digital twins", "digital-twins.html"), ("Glossary", "digital-twins-glossary.html")],
        og_image="assets/images/og-digital-twins.png"),
    "twins-track-record.html": dict(
        title="Digital Twins: How We Score Ourselves | Crosswalk",
        description="Every call the Crosswalk twins make goes on the record before the outcome, dated and hashed, then scored when it lands. The method, how firm each count is, and the record.",
        kind="product", name="Digital twins track record", crumb_label="Track record",
        og_image="assets/images/og-digital-twins.png"),
    "prometheus.html": dict(
        title="Prometheus: Ask Anything About 10 Million Real People | Crosswalk",
        description="Prometheus is Crosswalk's research assistant. Ask any audience question in plain English and it answers from what 10 million US people do, recorded day by day since 2021.",
        kind="product", name="Prometheus", og_image="assets/images/og-prometheus.jpg"),
    "micro-series.html": dict(
        title="Micro-series: who watches vertical dramas, and where | Crosswalk",
        description="Who watches vertical dramas on ReelShort, DramaBox, GoodShort, NetShort and Peacock, who pays, and where the paywall sits. Counted from 10 million US people, updated daily.",
        kind="product", name="Micro-series", og_image="assets/images/og-micro-series.png"),
    "panel.html": dict(
        title="The Crosswalk panel: 30 million opted-in people worldwide | Crosswalk",
        description="30 million people worldwide chose to be counted, 10 million of them in the US. How the panel works, what gets counted, your rights, and what clients receive.",
        crumb=[("The panel", "panel.html")]),
    "company.html": dict(
        title="Company | Crosswalk Technologies, Inc.",
        description="Crosswalk Technologies, Inc. Who we work with, how we write the numbers, The Read, and how to reach us.",
        crumb=[("Company", "company.html")]),
    "clients.html": dict(
        title="Clients | Crosswalk",
        description="The media companies, brands, agencies, finance firms, and public offices that work with Crosswalk.",
        crumb=[("Clients", "clients.html")]),
    "join.html": dict(
        title="Join the panel: download the Crosswalk app | Crosswalk",
        description="Download the Crosswalk panel app for Mac or Windows. It is free, and it explains what it collects before you install it.",
        crumb=[("The panel", "panel.html"), ("Join the panel", "join.html")]),
    "privacy-policy-1.html": dict(
        description="How Crosswalk Technologies, Inc. collects, uses, shares, and protects information on its opted-in US digital panel, and how you can exercise your rights.",
        crumb=[("Privacy Policy", "privacy-policy-1.html")]),
    "terms-of-use.html": dict(
        description="The Terms of Use for the Crosswalk panel software, the dashboard, and the website: the contract between you and Crosswalk Technologies, Inc.",
        crumb=[("Terms of Use", "terms-of-use.html")]),
    "why-we-track.html": dict(
        description="What the Crosswalk panel software captures, why we capture it, and what clients actually receive from the opted-in US digital panel.",
        crumb=[("The panel", "panel.html"), ("What we track", "why-we-track.html")]),
    "do-not-sell-my-personal-information.html": dict(
        description="Opt out of the sale or sharing of your personal information, targeted advertising, and the use of sensitive personal information, where those rights apply to you.",
        crumb=[("Your Privacy Choices", "do-not-sell-my-personal-information.html")]),
    "optout.html": dict(
        description="How to stop new collection, delete the panel record Crosswalk holds, and opt out of sale, share, or targeted advertising of your personal information.",
        crumb=[("Opt-Out", "optout.html")]),
    "tracking-transparency.html": dict(
        description="The consent notice for Crosswalk panel software: what the browser extension and apps collect, and what to read before you install or keep them running.",
        crumb=[("The panel", "panel.html"), ("Tracking consent", "tracking-transparency.html")]),
    "deidentified-data.html": dict(
        description="Crosswalk Technologies, Inc.'s public commitment about aggregated dashboard products: group statistics that cannot reasonably be linked back to a person.",
        crumb=[("De-identified data", "deidentified-data.html")]),
    "coppa.html": dict(
        description="Children's privacy at Crosswalk: personal information from people under 18 is collected only with parental or guardian consent, and only where that is legal.",
        crumb=[("COPPA", "coppa.html")]),
    "parental-consent.html": dict(
        description="The Crosswalk panel is built for adults. Where the law allows it, a parent or legal guardian may consent to collection from a person under 18. How that works.",
        crumb=[("Parental consent", "parental-consent.html")]),
    "financial-transparency-consent.html": dict(
        description="What Crosswalk may observe about financial and payment behavior on the panel, and what clients receive. Part of panel consent with the Privacy Policy and Terms of Use.",
        crumb=[("Financial transparency", "financial-transparency-consent.html")]),
    "medical-transparency-consent.html": dict(
        description="What Crosswalk may observe about health-related digital behavior on the panel, and what clients receive. Part of panel consent with the Privacy Policy and Terms of Use.",
        crumb=[("Medical transparency", "medical-transparency-consent.html")]),
    "app.html": dict(
        description="Read this before you join the Crosswalk panel: accepting a panel offer or installing the software means agreeing to the Terms of Use, the Privacy Policy, and tracking consent.",
        crumb=[("The panel", "panel.html"), ("Join the panel", "app.html")]),
}

# One sentence about the company, used everywhere a machine reads who we are
# (Organization JSON-LD, llms.txt). Keep the panel sizes in step with the pages.
ORG_DESCRIPTION = (
    "Crosswalk is an opted-in consumer panel of 30 million people worldwide, 10 million of them in the US. It reads what audiences actually do across their entire digital life, and Digital Twins IQ builds a digital twin of each US panelist from observed behavior."
)
# What the company is an authority on, as the machines read it. Each entry
# names a concept and, where one exists, its public reference page, so an
# assistant can tie "Crosswalk" to the topic it already knows.
ORG_KNOWS_ABOUT = [
    {"@type": "Thing", "name": "Digital twin audiences", "sameAs": "https://en.wikipedia.org/wiki/Digital_twin",
     "url": ORIGIN + "/what-is-a-digital-twin-audience.html"},
    {"@type": "Thing", "name": "Synthetic data for market research", "sameAs": "https://en.wikipedia.org/wiki/Synthetic_data",
     "url": ORIGIN + "/synthetic-data-market-research.html"},
    {"@type": "Thing", "name": "Behavioral consumer panels", "sameAs": "https://en.wikipedia.org/wiki/Clickstream",
     "url": ORIGIN + "/behavioral-panel.html"},
    {"@type": "Thing", "name": "AI focus groups", "url": ORIGIN + "/ai-focus-groups.html"},
    {"@type": "Thing", "name": "Audience measurement", "sameAs": "https://en.wikipedia.org/wiki/Audience_measurement"},
    {"@type": "Thing", "name": "Marketing attribution", "sameAs": "https://en.wikipedia.org/wiki/Attribution_(marketing)"},
    {"@type": "Thing", "name": "Streaming subscriber behavior"},
    {"@type": "Thing", "name": "Pilot testing for television and film"},
]
ORG = {
    "@type": "Organization",
    "@id": ORIGIN + "/#organization",
    "name": "Crosswalk Technologies, Inc.",
    "alternateName": ["Crosswalk", "Crosswalk Technologies", "Crosswalk NYC"],
    "legalName": "Crosswalk Technologies, Inc.",
    "description": ORG_DESCRIPTION,
    "slogan": "Noise to knowledge.",
    "url": ORIGIN + "/",
    "logo": ORIGIN + "/assets/logos/crosswalk-logo-black.png",
    "sameAs": ["https://www.linkedin.com/company/crosswalk-technologies"],
    "email": "hello@crosswalknyc.com",
    "telephone": "+1-818-231-2610",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "23465 Civic Center Way Bldg 9",
        "addressLocality": "Malibu",
        "addressRegion": "CA",
        "postalCode": "90265",
        "addressCountry": "US",
    },
    "knowsAbout": ORG_KNOWS_ABOUT,
    "areaServed": {"@type": "Country", "name": "United States"},
}
# The digital twins product as one node every learn page points at.
TWINS_SERVICE_ID = ORIGIN + "/digital-twins.html#service"
TWINS_SERVICE = {
    "@type": "Service",
    "@id": TWINS_SERVICE_ID,
    "name": "Digital Twins IQ",
    "alternateName": ["Twin IQ", "Crosswalk Digital Twins", "Crosswalk synthetic audiences"],
    "serviceType": "Digital twin audience simulation and synthetic data built from observed consumer behavior",
    "url": ORIGIN + "/digital-twins.html",
    "provider": {"@id": ORIGIN + "/#organization"},
}
WEBSITE = {
    "@type": "WebSite",
    "@id": ORIGIN + "/#website",
    "url": ORIGIN + "/",
    "name": SITE_NAME,
    "publisher": {"@id": ORIGIN + "/#organization"},
}

_TAG_RES = [
    re.compile(r'[ \t]*<link[^>]*rel="canonical"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<link[^>]*rel="icon"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*name="description"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*name="robots"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*name="keywords"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*name="google-site-verification"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*property="og:[^"]*"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<meta[^>]*name="twitter:[^"]*"[^>]*>[ \t]*\n?', re.I),
    re.compile(r'[ \t]*<script type="application/ld\+json" data-seo="site">.*?</script>[ \t]*\n?', re.I | re.S),
]
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_VIEWPORT_RE = re.compile(r'<meta[^>]*name="viewport"[^>]*>', re.I)
_CHARSET_RE = re.compile(r'<meta[^>]*charset[^>]*>', re.I)
_DESC_RE = re.compile(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', re.I)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_STRIP_TAGS = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_FAQ_ITEM_RE = re.compile(r'<div class="item">\s*<h3>(.*?)</h3>\s*<p>(.*?)</p>', re.I | re.S)
_TERM_RE = re.compile(r'<div class="term"(?: id="([^"]*)")?>\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>', re.I | re.S)


def _attr(s: str) -> str:
    return htmllib.escape(s, quote=True)


def _text(fragment: str) -> str:
    """Visible words of an HTML fragment, one line, entities resolved."""
    t = re.sub(r"<small>.*?</small>", "", fragment, flags=re.I | re.S)
    t = htmllib.unescape(_STRIP_TAGS.sub("", t))
    return re.sub(r"\s+", " ", t).strip()


def page_h1(html: str) -> str:
    m = _H1_RE.search(html)
    return _text(m.group(1)) if m else ""


def faq_pairs(html: str) -> list[tuple[str, str]]:
    """(question, answer) for every FAQ item on the page."""
    return [(_text(q), _text(a)) for q, a in _FAQ_ITEM_RE.findall(html)]


def glossary_terms(html: str) -> list[tuple[str, str, str]]:
    """(anchor, term, definition) for every glossary entry on the page."""
    return [(aid or "", _text(dt), _text(dd)) for aid, dt, dd in _TERM_RE.findall(html)]


def is_stub(html: str) -> bool:
    return 'http-equiv="refresh"' in html


def is_noindex(name: str, html: str) -> bool:
    if name in NOINDEX_FILES:
        return True
    return bool(re.search(r'<meta[^>]*name="robots"[^>]*content="[^"]*noindex', html, re.I))


def indexable_pages() -> list[str]:
    out = []
    for p in sorted(SITE.glob("*.html")):
        html = p.read_text(encoding="utf-8")
        if is_stub(html) or is_noindex(p.name, html):
            continue
        out.append(p.name)
    return out


def _derive_description(html: str) -> str:
    m = _DESC_RE.search(html)
    if m and m.group(1).strip():
        return htmllib.unescape(m.group(1).strip())
    for pm in _P_RE.finditer(html):
        text = htmllib.unescape(_STRIP_TAGS.sub("", pm.group(1)))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 60:
            if len(text) <= 158:
                return text
            out = ""
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if out and len(out) + 1 + len(sent) > 158:
                    break
                out = (out + " " + sent).strip()
                if len(out) > 158:
                    break
            return out if 40 <= len(out) <= 158 else text[:155].rsplit(" ", 1)[0]
    return ""


def _page_label(name: str, title: str) -> str:
    head = title.split("|")[0].strip()
    head = re.sub(r"^Crosswalk\s*", "", head).strip(" :")
    return head or name.replace(".html", "").replace("-", " ").title()


def finish_page(name: str, html: str) -> str:
    """Return the page with its search-engine head block in place."""
    spec = dict(PAGES.get(name, {}))
    m = _TITLE_RE.search(html)
    own_title = htmllib.unescape(m.group(1).strip()) if m else SITE_NAME
    title = spec.get("title") or own_title
    description = spec.get("description") or _derive_description(html)
    kind = spec.get("kind", "page")
    url = ORIGIN + "/" + ("" if name == "index.html" else name)
    og_image = ORIGIN + "/" + spec.get("og_image", DEFAULT_OG_IMAGE)

    # Title in place.
    if m and title != own_title:
        html = html[:m.start()] + f"<title>{htmllib.escape(title)}</title>" + html[m.end():]
    elif not m:
        html = html.replace("<head>", f"<head>\n<title>{htmllib.escape(title)}</title>", 1)

    # Drop every tag this script owns, then write them back in one block.
    for rx in _TAG_RES:
        html = rx.sub("", html)

    lines = [
        f'<meta name="description" content="{_attr(description)}">',
    ]
    if name == "index.html" and GOOGLE_TOKEN_FILE.exists():
        token = GOOGLE_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            lines.append(f'<meta name="google-site-verification" content="{_attr(token)}">')
    lines += [
        f'<meta name="robots" content="{ROBOTS_INDEX}">',
        f'<link rel="canonical" href="{url}">',
        '<link rel="icon" type="image/svg+xml" href="assets/logos/favicon.svg">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        '<meta property="og:locale" content="en_US">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:title" content="{_attr(title)}">',
        f'<meta property="og:description" content="{_attr(description)}">',
        f'<meta property="og:image" content="{og_image}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_attr(title)}">',
        f'<meta name="twitter:description" content="{_attr(description)}">',
        f'<meta name="twitter:image" content="{og_image}">',
    ]

    # JSON-LD, unless the page brings its own.
    has_own_ld = 'application/ld+json' in html
    if not has_own_ld:
        graph: list[dict] = []
        if kind == "home":
            graph += [ORG, WEBSITE]
            graph.append({
                "@type": "WebPage", "@id": url + "#webpage", "url": url, "name": title,
                "description": description, "isPartOf": {"@id": ORIGIN + "/#website"},
                "about": {"@id": ORIGIN + "/#organization"}, "inLanguage": "en-US",
            })
        else:
            label = spec.get("crumb_label") or spec.get("name") or _page_label(name, title)
            crumb = spec.get("crumb")
            if not crumb:
                crumb = ([PRODUCTS_CRUMB] if kind == "product" else []) + [(label, name)]
            items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": ORIGIN + "/"}]
            for i, (lab, href) in enumerate(crumb, start=2):
                items.append({"@type": "ListItem", "position": i, "name": lab, "item": ORIGIN + "/" + href})
            page_node = {
                "@type": "WebPage", "@id": url + "#webpage", "url": url, "name": title,
                "description": description, "isPartOf": {"@id": ORIGIN + "/#website"},
                "breadcrumb": {"@id": url + "#breadcrumb"}, "inLanguage": "en-US",
            }
            graph.append(page_node)
            graph.append({"@type": "BreadcrumbList", "@id": url + "#breadcrumb", "itemListElement": items})
            if kind == "learn":
                # A long-form read: the article, its FAQ, and (on the
                # glossary) the defined terms, each tied to the digital
                # twins service and the organization so an assistant
                # can quote the page and know who wrote it.
                published = spec.get("published") or date.today().isoformat()
                modified = _git_lastmod(name, _sitemap_lastmods().get(url, ""), _shallow_commits())
                if modified < published:
                    modified = published
                headline = page_h1(html) or title.split("|")[0].strip()
                page_node.update({
                    "datePublished": published, "dateModified": modified,
                    "about": {"@id": TWINS_SERVICE_ID},
                    "speakable": {"@type": "SpeakableSpecification", "cssSelector": ["h1", ".answer"]},
                })
                graph.append(ORG)
                graph.append(TWINS_SERVICE)
                graph.append({
                    "@type": "Article", "@id": url + "#article", "headline": headline[:110],
                    "description": description, "url": url, "mainEntityOfPage": {"@id": url + "#webpage"},
                    "author": {"@id": ORIGIN + "/#organization"}, "publisher": {"@id": ORIGIN + "/#organization"},
                    "datePublished": published, "dateModified": modified, "inLanguage": "en-US",
                    "image": og_image, "about": {"@id": TWINS_SERVICE_ID},
                    "isAccessibleForFree": True,
                })
                faqs = faq_pairs(html)
                if faqs:
                    graph.append({
                        "@type": "FAQPage", "@id": url + "#faq",
                        "mainEntity": [{"@type": "Question", "name": q,
                                        "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs],
                    })
                terms = glossary_terms(html)
                if terms:
                    graph.append({
                        "@type": "DefinedTermSet", "@id": url + "#terms", "name": headline,
                        "hasDefinedTerm": [{"@type": "DefinedTerm", "@id": url + ("#" + aid if aid else ""),
                                            "name": t, "description": d,
                                            "inDefinedTermSet": {"@id": url + "#terms"}} for aid, t, d in terms],
                    })
            if kind == "product":
                page_node["about"] = {"@id": url + "#service"}
                graph.append({
                    "@type": "Service", "@id": url + "#service", "name": spec.get("name", label),
                    "serviceType": "Audience research report built on observed consumer behavior",
                    "description": description, "url": url,
                    "provider": {"@id": ORIGIN + "/#organization"},
                    "areaServed": {"@type": "Country", "name": "United States"},
                })
        ld = json.dumps({"@context": "https://schema.org", "@graph": graph}, indent=1, ensure_ascii=False)
        lines.append(f'<script type="application/ld+json" data-seo="site">\n{ld}\n</script>')

    block = "\n".join(lines) + "\n"
    anchor = _TITLE_RE.search(html) or _VIEWPORT_RE.search(html) or _CHARSET_RE.search(html)
    if anchor:
        # Keep the page's own indentation style: insert right after the anchor line.
        end = html.find("\n", anchor.end())
        end = anchor.end() if end < 0 else end + 1
        html = html[:end] + block + html[end:]
    else:
        html = html.replace("<head>", "<head>\n" + block, 1)
    return html


def _shallow_commits() -> set:
    """The commits where this checkout's history is cut (a shallow clone).
    A file last touched before the cut reports the cut commit's date, which
    is the clone's depth and says nothing about the page."""
    try:
        gd = subprocess.run(["git", "-C", str(BG), "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True, timeout=20).stdout.strip()
        f = (Path(gd) if Path(gd).is_absolute() else BG / gd) / "shallow"
        return {ln.strip() for ln in f.read_text().splitlines() if ln.strip()} if f.is_file() else set()
    except Exception:
        return set()


def _sitemap_lastmods() -> dict:
    """{loc: lastmod} from the sitemap already on disk."""
    try:
        text = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    except Exception:
        return {}
    return dict(re.findall(r"<loc>(.*?)</loc><lastmod>(\d{4}-\d{2}-\d{2})</lastmod>", text))


def _git_lastmod(name: str, existing: str = "", shallow: set | None = None) -> str:
    """The page's last change. A lastmod never moves backward and never
    depends on how much history this checkout carries: the date already
    published stands unless the page changed after it (2026-11-11: the
    check failed on a clean tree because the published dates had been
    written from a shallow clone, whose cut commit dated every older
    page)."""
    try:
        dirty = subprocess.run(["git", "-C", str(BG), "status", "--porcelain", "--", f"site/{name}"],
                               capture_output=True, text=True, timeout=20).stdout.strip()
        if dirty:
            return date.today().isoformat()
        out = subprocess.run(["git", "-C", str(BG), "log", "-1", "--format=%H %cs", "--", f"site/{name}"],
                             capture_output=True, text=True, timeout=20).stdout.split()
        sha, when = (out + ["", ""])[:2]
        if existing and (not when or sha in (shallow or set()) or existing > when):
            return existing
        return when or existing or date.today().isoformat()
    except Exception:
        return existing or date.today().isoformat()


def build_sitemap(pages: list[str]) -> str:
    ordered = sorted(pages, key=lambda n: (n != "index.html", n not in PAGES, n))
    have, shallow = _sitemap_lastmods(), _shallow_commits()
    rows = []
    for name in ordered:
        loc = ORIGIN + "/" + ("" if name == "index.html" else name)
        rows.append(f"  <url><loc>{loc}</loc><lastmod>{_git_lastmod(name, have.get(loc, ''), shallow)}</lastmod></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")


# The crawlers behind the AI assistants, named so each one finds its own
# token and the site's intent is on the record (2026-11-23). Every one is
# allowed the whole public site; the two noindex pages stay out for all.
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",            # OpenAI: training, search, live browsing
    "ClaudeBot", "Claude-SearchBot", "Claude-User", "anthropic-ai",  # Anthropic
    "PerplexityBot", "Perplexity-User",                   # Perplexity
    "Google-Extended", "Googlebot",                       # Gemini and AI Overviews, Search
    "Bingbot", "DuckAssistBot",                           # Copilot (Bing index), DuckDuckGo AI
    "Applebot", "Applebot-Extended",                      # Apple Intelligence, Siri
    "Meta-ExternalAgent", "Meta-ExternalFetcher",         # Meta AI
    "Amazonbot",                                          # Alexa, Rufus
    "CCBot",                                              # Common Crawl, used in many training sets
    "cohere-ai", "MistralAI-User", "YouBot", "Bytespider",
]


def build_robots() -> str:
    lines = ["# Crosswalk. Every page here is written to be read, quoted and cited.",
             f"# Plain-text summaries for AI assistants: {ORIGIN}/llms.txt and {ORIGIN}/llms-full.txt",
             "", "User-agent: *", "Allow: /"]
    for f in sorted(NOINDEX_FILES):
        lines.append(f"Disallow: /{f}")
        lines.append(f"Disallow: /site/{f}")
    lines.append("")
    for ua in AI_CRAWLERS:
        lines += [f"User-agent: {ua}", "Allow: /"]
        for f in sorted(NOINDEX_FILES):
            lines.append(f"Disallow: /{f}")
        lines.append("")
    lines += [f"Sitemap: {ORIGIN}/sitemap.xml", ""]
    return "\n".join(lines)


def _llms_row(name: str, pages_spec: dict | None = None) -> str:
    spec = (pages_spec or PAGES).get(name, {})
    crumb = spec.get("crumb") or []
    label = spec.get("name") or (crumb[-1][0] if crumb else _page_label(name, spec.get("title", name)))
    url = ORIGIN + "/" + ("" if name == "index.html" else name)
    return f"- [{label}]({url}): {spec.get('description', '').strip()}"


LEARN_ORDER = [
    "digital-twins-methodology.html", "what-is-a-digital-twin-audience.html",
    "synthetic-data-market-research.html", "synthetic-respondents-vs-behavioral-twins.html",
    "ai-focus-groups.html", "behavioral-panel.html", "digital-twins-glossary.html",
]

# The pages whose full text rides in llms-full.txt, in reading order: the
# digital twins cluster first, then the panel and Prometheus.
LLMS_FULL_ORDER = ["digital-twins.html", "twins-track-record.html"] + LEARN_ORDER + ["panel.html", "prometheus.html"]


def build_llms_txt(pages: list[str]) -> str:
    """llms.txt (llmstxt.org): a plain summary for AI assistants, from PAGES."""
    row = _llms_row
    products = sorted((n for n in pages if PAGES.get(n, {}).get("kind") == "product"),
                      key=lambda n: n != "digital-twins.html")
    learn = [n for n in LEARN_ORDER if n in pages]
    company = [n for n in ("panel.html", "company.html", "clients.html", "products.html", "join.html")
               if n in pages]
    policies = [n for n in ("privacy-policy-1.html", "terms-of-use.html", "why-we-track.html",
                            "deidentified-data.html") if n in pages]
    out = [
        "# Crosswalk",
        "",
        f"> {ORG_DESCRIPTION}",
        "",
        "Crosswalk Technologies, Inc. serves studios, streamers, brands, agencies, and finance teams. "
        "Prometheus answers audience questions in plain English from the same panel. "
        "Clients receive group statistics projected to the US population; no individual is identified.",
        "",
        "Key facts: 30 million opted-in people worldwide, 10 million in the US, census matched and projected to 329.9 million Americans. "
        "One ID per person across phone, laptop and TV. Recorded day by day since January 2021; yesterday's behavior is on file today. "
        "Digital Twins IQ (also called Twin IQ) builds a digital twin of any audience from 5+ years of observed behavior; a twin is made of at least 12 archetypes; "
        "a focus group seats one archetype representative per archetype; every count carries a range and says how firm it is (firm, leaning or early); "
        "every call is dated before its outcome and scored on a public track record.",
        "",
        "How it differs from other digital twins and synthetic respondents: it is built from what real people did, not from surveys, interviews or chatbot personas. "
        "10 million real people stand behind every answer, about 1 in 33 Americans, with more than 2,000 days of history. "
        "Answers are counted in people and are checked against real results in public: "
        "on the first six scored calls, all three winners were called right and the counts landed within 3.2% on average.",
        "",
        f"The full text of the digital twins, synthetic data and panel pages is at {ORIGIN}/llms-full.txt.",
        "",
        "## Learn: digital twins, synthetic data and the behavioral panel",
        *[row(n) for n in learn],
        "",
        "## Products",
        *[row(n) for n in products],
        "",
        "## The panel and the company",
        *[row(n) for n in company],
        "",
        "## Policies",
        *[row(n) for n in policies],
        "",
        "## Contact",
        f"- [Talk to us]({ORIGIN}/company.html#contact): hello@crosswalknyc.com",
        "",
    ]
    return "\n".join(out)


_BLOCK_RE = re.compile(
    r"<(h1|h2|h3|p|li|dt|dd|figcaption|th|td)(?:\s[^>]*)?>(.*?)</\1>|<(/?)(table|ul|ol|tr)(?:\s[^>]*)?>",
    re.I | re.S)


def page_markdown(name: str, html: str) -> str:
    """The page body as plain markdown: headings, paragraphs, lists and
    tables, in page order, without the nav, scripts or styles."""
    body = html.split("<body", 1)[1] if "<body" in html else html
    body = re.sub(r"<(script|style|aside)[^>]*>.*?</\1>", "", body, flags=re.I | re.S)
    body = re.sub(r'<section class="band dark">\s*<div class="wrap">\s*<div class="eyebrow"><span class="n">Read next</span>.*?</section>', "", body, flags=re.I | re.S)
    out: list[str] = []
    row: list[str] = []
    in_head = False
    for m in _BLOCK_RE.finditer(body):
        tag = (m.group(1) or m.group(4) or "").lower()
        if m.group(4):
            if tag == "table":
                if m.group(3):  # closing
                    out.append("")
                else:
                    in_head = True
                continue
            if tag == "tr":
                if m.group(3) and row:
                    out.append("| " + " | ".join(row) + " |")
                    if in_head:
                        out.append("|" + "---|" * len(row))
                        in_head = False
                    row = []
                continue
            continue
        text = _text(m.group(2))
        if not text:
            continue
        if tag in ("th", "td"):
            row.append(text.replace("|", "/"))
        elif tag == "tr":
            continue
        elif tag == "h1":
            out += [f"# {text}", ""]
        elif tag == "h2":
            out += ["", f"## {text}", ""]
        elif tag == "h3":
            out += ["", f"### {text}", ""]
        elif tag == "li":
            out.append(f"- {text}")
        elif tag == "dt":
            out += ["", f"**{text}**"]
        elif tag == "dd":
            out.append(text)
        elif tag == "figcaption":
            out.append(f"_{text}_")
        else:
            if text.lower().startswith("cite this page"):
                continue
            out += [text, ""]
    md = "\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    url = ORIGIN + "/" + name
    return f"<!-- {url} -->\n\n{md}\n\nSource: {url}\n"


def build_llms_full(pages: list[str]) -> str:
    """llms-full.txt: the whole text of the key pages, in markdown, so an
    assistant that reads one file gets the method, the definitions and the
    panel without crawling."""
    parts = ["# Crosswalk: digital twins, synthetic data and the behavioral panel", "",
             f"> {ORG_DESCRIPTION}", "",
             f"This file carries the full text of the pages listed in {ORIGIN}/llms.txt that explain Crosswalk's "
             "digital twins, synthetic data and behavioral panel. Every page is also live at the address above each section.", ""]
    for name in LLMS_FULL_ORDER:
        if name not in pages:
            continue
        html = (SITE / name).read_text(encoding="utf-8")
        parts += ["", "---", "", page_markdown(name, html)]
    return "\n".join(parts).rstrip() + "\n"


def run(check: bool = False) -> int:
    pages = indexable_pages()
    changes: list[str] = []
    for name in pages:
        p = SITE / name
        before = p.read_text(encoding="utf-8")
        after = finish_page(name, before)
        if "\u2014" in after or "\u2013" in after:
            print(f"[site_seo] {name}: em or en dash in the page; fix the copy first", file=sys.stderr)
            return 2
        if after != before:
            changes.append(name)
            if not check:
                p.write_text(after, encoding="utf-8")
    sitemap = build_sitemap(pages)
    sm = SITE / "sitemap.xml"
    if not sm.exists() or sm.read_text(encoding="utf-8") != sitemap:
        changes.append("sitemap.xml")
        if not check:
            sm.write_text(sitemap, encoding="utf-8")
    robots = build_robots()
    rb = SITE / "robots.txt"
    if not rb.exists() or rb.read_text(encoding="utf-8") != robots:
        changes.append("robots.txt")
        if not check:
            rb.write_text(robots, encoding="utf-8")
    llms = build_llms_txt(pages)
    lt = SITE / "llms.txt"
    if not lt.exists() or lt.read_text(encoding="utf-8") != llms:
        changes.append("llms.txt")
        if not check:
            lt.write_text(llms, encoding="utf-8")
    full = build_llms_full(pages)
    lf = SITE / "llms-full.txt"
    if not lf.exists() or lf.read_text(encoding="utf-8") != full:
        changes.append("llms-full.txt")
        if not check:
            lf.write_text(full, encoding="utf-8")
    verb = "would change" if check else "updated"
    print(f"[site_seo] {len(pages)} indexable pages; {len(changes)} file(s) {verb}" + (": " + ", ".join(changes) if changes else ""))
    return 1 if (check and changes) else 0


if __name__ == "__main__":
    sys.exit(run(check="--check" in sys.argv[1:]))
