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

Then it writes sitemap.xml (one URL per indexable page, lastmod from
git) and robots.txt. Pages marked noindex and the redirect stubs are
left alone and kept out of the sitemap.

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
        description="Ask Prometheus any audience question. It answers from 30 million opted-in people and their entire digital life: who they are, what they watch, what they buy.",
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
        description="Twin IQ builds a digital twin of each of 10 million US people from observed behavior, not surveys. Test a plan, a title, or a price on synthetic audiences before you spend.",
        kind="product", name="Twin IQ", crumb_label="Digital twins",
        og_image="assets/images/og-digital-twins.png"),
    "panel.html": dict(
        title="The Crosswalk panel: 30 million opted-in people | Crosswalk",
        description="30 million people chose to be counted. How the opted-in panel works, what gets counted, your rights, and what clients receive.",
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

ORG = {
    "@type": "Organization",
    "@id": ORIGIN + "/#organization",
    "name": "Crosswalk Technologies, Inc.",
    "alternateName": "Crosswalk",
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


def _attr(s: str) -> str:
    return htmllib.escape(s, quote=True)


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


def _git_lastmod(name: str) -> str:
    try:
        dirty = subprocess.run(["git", "-C", str(BG), "status", "--porcelain", "--", f"site/{name}"],
                               capture_output=True, text=True, timeout=20).stdout.strip()
        if dirty:
            return date.today().isoformat()
        out = subprocess.run(["git", "-C", str(BG), "log", "-1", "--format=%cs", "--", f"site/{name}"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out or date.today().isoformat()
    except Exception:
        return date.today().isoformat()


def build_sitemap(pages: list[str]) -> str:
    ordered = sorted(pages, key=lambda n: (n != "index.html", n not in PAGES, n))
    rows = []
    for name in ordered:
        loc = ORIGIN + "/" + ("" if name == "index.html" else name)
        rows.append(f"  <url><loc>{loc}</loc><lastmod>{_git_lastmod(name)}</lastmod></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")


def build_robots() -> str:
    lines = ["User-agent: *", "Allow: /"]
    for f in sorted(NOINDEX_FILES):
        lines.append(f"Disallow: /{f}")
        lines.append(f"Disallow: /site/{f}")
    lines += ["", f"Sitemap: {ORIGIN}/sitemap.xml", ""]
    return "\n".join(lines)


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
    verb = "would change" if check else "updated"
    print(f"[site_seo] {len(pages)} indexable pages; {len(changes)} file(s) {verb}" + (": " + ", ".join(changes) if changes else ""))
    return 1 if (check and changes) else 0


if __name__ == "__main__":
    sys.exit(run(check="--check" in sys.argv[1:]))
