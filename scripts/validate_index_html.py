#!/usr/bin/env python3
# NOTE: The companion GitHub Action (.github/workflows/validate-index-html.yml)
# is path-filtered to templates/index.html, this validator, and the workflow
# file. PRs that touch none of those (e.g. login.html-only changes) leave the
# required `validate` check pending and unmergeable. Someone with `workflow`
# scope should broaden the workflow's pull_request trigger to run on all PRs.
# (Until that lands, non-index.html PRs touch this file to trigger the check.)
# Trigger touch: books search all + avg
"""Validate structural invariants of ``templates/index.html``.

This file has been silently broken multiple times in a single week by
otherwise-unrelated feature commits that rewrote the file wholesale and
accidentally dropped its tail (twice) or an entire mobile-responsive
layer (once). Each regression only surfaced when a user hit a broken
UI. This validator turns those latent regressions into a hard fail at
commit time (and, via the sibling GitHub Action, at push/PR time).

Checks performed
----------------
1.  File exists and is non-trivial in size (>= 1 MB - anything smaller
    means the file was catastrophically truncated or someone confused
    it with a stub).
2.  File ends with ``</html>``. This is the classic tail-truncation
    signature: an unclosed ``<script>`` tag in the middle of the file
    halts browser parsing and the tail (including the closing tags)
    never renders.
3.  ``<script>`` open tag count equals ``</script>`` close tag count.
    An imbalance is the direct cause of the "dashboard stuck at
    Loading your dashboard - Waking up the server... 0%" freeze.
4.  Each known-critical anchor string is present. These are DOM ids /
    function names / block markers that have been dropped in past
    regressions. Adding a new anchor here is the fastest way to
    prevent a regression from recurring - as soon as a real bug is
    root-caused to a missing anchor, add its id/marker to
    ``REQUIRED_ANCHORS`` below and no future commit can drop it
    silently.
5.  Inline ``<script>`` blocks parse cleanly under ``node --check``.
    Extracts every inline block (skipping ``<script src=...>``),
    strips Jinja templating (``{{...}}`` and ``{%...%}``), and runs
    ``node --check`` against the combined source. A JS syntax error
    inside a script block leaves the tag balance intact and the file
    ending intact, so the earlier checks miss it; but the browser
    still halts every inline block on parse and the dashboard freezes
    at the loading spinner. Added 2026-08-18 after the UFC Methodology
    tab shipped a dangling `ufc_methodology:` property outside its
    parent object. Skips with a warning (does not fail) if ``node`` is
    not on PATH so environments without Node installed still pass.
6.  Optional (warn, do not fail): file shrunk by more than 5% vs the
    previous HEAD version. Big deletions in a single commit are
    almost always accidental; small trims are legitimate. Only warns
    because deliberate large refactors do happen.
7.  Optional (warn, or fail if ``VALIDATE_STRICT_SHRINK=1``): file
    shrunk by more than 5% vs ``origin/main``. Catches the specific
    "stale working tree about to clobber newer work" pattern that hit
    us on 2026-08-24 with commit 8c746970. Uses the LOCAL refs cache
    only (no ``git fetch``) so pre-commit stays fast; pre-push fetches
    origin/main itself before invoking the validator with strict mode.
8.  Split files (``SPLIT_FILES``, since 2026-11-20): the blocks that
    left the page for static/ exist, are at least their size floor,
    carry no Jinja, are referenced by the page exactly once, and parse
    under ``node --check``. Checks 4, 6 and 7 read the page together
    with these files (the anchors may sit in a split file; the shrink
    checks compare the bundle), so a split is a move, not a loss.
    ``--split-files`` prints the registered paths for the pre-push hook.

Exit codes
----------
    0 - all checks pass
    1 - one or more hard checks failed (blocks commit / CI)
    2 - internal error (unable to read file / etc.)

Usage
-----
Run standalone from either the repo root or the ``bg-webapp/`` dir::

    python3 scripts/validate_index_html.py

Wired into ``.githooks/pre-commit`` and
``.github/workflows/validate-index-html.yml`` in this repo.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---- CONFIG ----------------------------------------------------------------

# Minimum sane size for the current dashboard. Set well below the current
# ~8.5 MB so it flags only catastrophic truncation, not routine trims.
MIN_SIZE_BYTES = 1_000_000  # 1 MB

# Maximum acceptable shrinkage vs HEAD (fraction of HEAD size). Warns
# only - deliberate refactors can legitimately exceed this.
MAX_SHRINK_FRACTION = 0.05  # 5%

# DOM ids, function names, and block markers that MUST exist in the file.
# Add a new entry the moment a real bug is traced to a missing anchor,
# so no future commit can drop the same block silently.
#
# Each entry is a tuple of (anchor_string, human_description).
REQUIRED_ANCHORS: list[tuple[str, str]] = [
    # Mobile responsive layer - dropped by 5aaad71d (2026-07-30 or so).
    # User-visible failure: "mobile site formatting is gone" 2026-08-10.
    ('id="mobile-responsive-layer"',
     "mobile CSS block (@media max-width: 768px)"),
    ('id="mobile-nav-enhancements"',
     "mobile JS init IIFE (tools nav + sidebar accordion)"),
    ('id="mobileToolsNav"',
     "mobile <select> that replaces the Data & Analysis Tools button strip"),
    ('_initMobileSidebarCollapse',
     "mobile sidebar accordion init function"),
    # Core Profile IQ boot / navigation. Dropped historically by the
    # 2026-08-10 "Persona Lens" commit that truncated 958 tail lines.
    ('function selectDashboardProfile',
     "profile picker click handler - dashboard boot depends on it"),
    ('function _pickBaseRun',
     "resolves subject-click -> base run (Total Universe vs Avid Fan)"),
    ('function getProfileSuffixInfo',
     "parses profile filenames into base + cohort/year suffix"),
    ('function _baseCohortLabel',
     "single chokepoint for Total Universe / Casual Fan labeling"),
    # Prometheus floating chatbot (2026-08-20). Wiped 2026-08-24 by commit
    # 8c746970 ("resource center UI rework with payload cleanup") which
    # bulk-published a stale working-tree state that deleted 9,158 lines
    # from this file. User-visible failure: "I dont see the chatbot on my
    # screen" (Jenna, 2026-08-24 11:08 PDT). Fixed in 8e87a691. Adding
    # these anchors so any future commit that drops the widget or its
    # entry points gets blocked at pre-commit + CI.
    ('id="prometheusLauncher"',
     "Prometheus chatbot floating launcher (bottom-right on every page)"),
    ('id="prometheusWidget"',
     "Prometheus chatbot expanded chat widget"),
    ('id="prometheusCollapseBtn"',
     "Prometheus chatbot header collapse button"),
    ('prometheusExpand(',
     "prometheusExpand() JS entry point - opens the chat widget"),
    ('id="customAnalysisTabContentChatbotProfileIQ"',
     "Chatbot Profile IQ tab under Analysis IQ (landing stub for the widget)"),
    # Story-mode Journey IQ panels (SPE Cross-Window, UFC x Paramount+,
    # AMC). Same 2026-08-24 wipe. One anchor per major panel is enough
    # to catch a full-block deletion; individual card-level renames
    # inside a panel are legit and should not fire the check.
    ('id="jiqUfcTabBar"',
     "UFC x Paramount+ story-mode tab bar (Journey IQ)"),
    ('id="jiqUfcCaseCard"',
     "UFC x Paramount+ story-mode case card (Journey IQ)"),
    ('id="jiqUfcNestCard"',
     "UFC x Paramount+ nest walk card (Journey IQ)"),
    ('id="jiqArrowTabBar"',
     "Arrow on Pluto TV story-mode tab bar (Journey IQ)"),
    ('id="jiqArrowNestCard"',
     "Arrow on Pluto TV nest walk card (Journey IQ)"),
    ('function _jiqArrowHydrateRun',
     "Arrow on Pluto TV demo hydrate"),
    ('__demo_arrow_pluto__',
     "Arrow on Pluto TV Journey IQ demo picker key"),
    ('arrow_pluto: 1',
     "Arrow on Pluto TV force-hide story mode"),
    ('id="jiqPgrTabBar"',
     "Politics Girl reel story-mode tab bar (Journey IQ)"),
    ('id="jiqPgrNestCard"',
     "Politics Girl reel nest walk card (Journey IQ)"),
    ('function _jiqPgrHydrateRun',
     "Politics Girl reel demo hydrate"),
    ('__demo_politics_girl_reel__',
     "Politics Girl reel Journey IQ demo picker key"),
    ('politics_girl_reel: 1',
     "Politics Girl reel force-hide story mode"),
    ('id="jiqAnimCrNestCard"',
     "Digital Animation Creators nest walk card (Journey IQ)"),
    ('function _jiqAnimCrHydrateRun',
     "Digital Animation Creators demo hydrate"),
    ('anim-creators-hero.jpg',
     "Digital Animation Creators nest hero still"),
    ('id="jiqSpeTabBar"',
     "SPE Cross-Window story-mode tab bar (Journey IQ)"),
    ('id="jiqSpeCaseCard"',
     "SPE Cross-Window story-mode case card (Journey IQ)"),
    ('id="jiqAmcCaseCard"',
     "AMC story-mode case card (Journey IQ)"),
    ('id="jiqSongCard"',
     "Heated Rivalry x Barbie song-to-title journey card"),
    ('function renderJourneyIqSong',
     "Heated Rivalry x Barbie song-to-title renderer"),
    ('id="jiqSongTabBar"',
     "Heated Rivalry x Barbie song nest tab bar"),
    ('id="jiqSongHost"',
     "Heated Rivalry x Barbie song nest host"),
    ('function _jiqSongClip',
     "Song journey clickable clip chip"),
    ('function _jiqSongHydrateRun',
     "Heated Rivalry and Barbie song journey hydrate"),
    ('id="jiqMusicCatalogCard"',
     "Music to Long Form catalog card"),
    ('id="jiqMusicTabBar"',
     "Music to Long Form title tabs"),
    ('var _JIQ_MUSIC_CATALOG',
     "Music to Long Form catalog payload"),
    ('function renderJourneyIqMusicCatalog',
     "Music to Long Form renderer"),
    ('__demo_music_to_long_form__',
     "Music to Long Form demo picker key"),
    ('id="jiqFragCard"',
     "Purchase-journey caterpillar card (fragrance + Young Sheldon + Dexter)"),
    ('function renderJourneyIqFragrance',
     "Luxury fragrance TikTok Shop renderer"),
    ('__demo_young_sheldon_pvod__',
     "Young Sheldon Amazon PVOD Journey IQ demo picker key"),
    ('function renderJourneyIqYsPvod',
     "Young Sheldon Amazon PVOD journey renderer"),
    ('young_sheldon_pvod',
     "Young Sheldon Amazon PVOD story_mode"),
    ('__demo_dexters_lab_pvod__',
     "Dexter's Laboratory Amazon PVOD Journey IQ demo picker key"),
    ('function renderJourneyIqDexterPvod',
     "Dexter's Laboratory Amazon PVOD journey renderer"),
    ('dexters_lab_pvod',
     "Dexter's Laboratory Amazon PVOD story_mode"),
    ('id="brandTrackingIQView"',
     "Brand Tracking product view (CNBC Pro competitive set)"),
    ('showBrandTrackingIQ',
     "Brand Tracking showBrandTrackingIQ() entry point"),
    ('value="brandTrackingIQ"',
     "Brand Tracking SELECT PRODUCT dropdown option"),
    ('CW_SELF_SERVE_HAS_PRODUCTS',
     "Prometheus-only seats keep SELECT PRODUCT when they hold files"),
    ('selfServeProductRail',
     "Prometheus-only complimentary seats show SELECT PRODUCT next to the logo"),
    ('id="dtwLibrary"',
     "Digital Twins IQ Twins tab library (the entries)"),
    ('class="dtw-lib-list"',
     "Digital Twins IQ library list (editorial entries, 2026-11-19)"),
    ('function renderLibrary',
     "Digital Twins IQ library renderer (every twin an entry with six things on it)"),
    ('id="dtwFgActs"',
     "Digital Twins IQ Focus Group room pills (landing)"),
    ('id="dtwFgDock"',
     "Digital Twins IQ Focus Group freezes at the question, not the whole top"),
    ('is-fg-room',
     "Digital Twins IQ Focus Group: page scrolls, question docks, panes scroll"),
    ('id="dtwFgTallyQ"',
     "Digital Twins IQ Focus Group question picker on the split card"),
    ('id="dtwBuildDrawer"',
     "Digital Twins IQ Build a twin walkthrough (profile or demographics, panelists, review)"),
    # An open tab comes up to date after an update lands. The first version
    # (2026-07-31) was dropped from this page by 5aaad71d on 2026-08-07 and
    # nobody saw it go: on 2026-11-17 Jenna was looking at the Build a twin
    # audience step without the share fields that had shipped four days
    # before.
    ('id="cw-stale-tab-refresh"',
     "refreshes an open tab after an update, never over work in progress"),
    ('window.cwRefreshState',
     "state of the stale-tab refresh (page version, live version, holds)"),
    ('then set a share if you want one',
     "Build a twin audience step: a share field beside every demographic pick"),
    # A '<Subject> - Global' profile (MARKET: GLOBAL): the worldwide summary,
    # the country switcher and the cities by country (Jenna 2026-11-23).
    ('window.CWGlobal',
     "Profile IQ worldwide layout for MARKET: GLOBAL profiles (static/global-profile/)"),
]

# Anchors we DO NOT want in the file (false negatives).
FORBIDDEN_ANCHORS: list[tuple[str, str]] = [
    ('body.cw-self-serve [id$="IQView"]',
     "must not hide every IQ view for Prometheus-only seats with files"),
]

# Blocks that left index.html for their own static file, loaded by the page
# at the exact spot the inline block used to sit (the front-end split, Jenna
# 2026-10-14; the Digital Twins IQ CSS and JS, Jenna 2026-11-20: SPLIT). Each
# row: the URL the page references, the file in this repo, the kind of block,
# and the smallest size the file can honestly be. The REQUIRED_ANCHORS above
# are searched across the page AND these files (an anchor has to exist
# somewhere the browser loads, and several of the Digital Twins anchors live
# in its script); each file must exist, be at least its floor, carry no
# Jinja (static files are served as they are, never rendered), and be
# referenced by the page exactly once; a .js file must parse under node.
# The shrink checks compare the BUNDLE (page + these files), so moving a
# block out is not read as the stale-working-tree clobber it is meant to
# catch, while a page that lost a block for no file still is.
SPLIT_FILES: list[tuple[str, str, str, int]] = [
    ("/static/digital-twins/digital-twins.css",
     "static/digital-twins/digital-twins.css", "style", 100_000),
    ("/static/digital-twins/digital-twins.js",
     "static/digital-twins/digital-twins.js", "script", 400_000),
    # Profile IQ worldwide layout (Jenna 2026-11-23: a '<Subject> - Global'
    # profile reads as a worldwide summary, then country by country in each
    # country's own terms). New code, never inline: the stylesheet loads at
    # the end of <head>, the script right before </body>.
    ("/static/global-profile/global-profile.css",
     "static/global-profile/global-profile.css", "style", 6_000),
    ("/static/global-profile/global-profile.js",
     "static/global-profile/global-profile.js", "script", 20_000),
]


# ---- IMPLEMENTATION --------------------------------------------------------

def find_index_html() -> Path | None:
    """Locate templates/index.html whether invoked from repo root or bg-webapp/."""
    for candidate in (
        Path("bg-webapp/templates/index.html"),
        Path("templates/index.html"),
    ):
        if candidate.is_file():
            return candidate
    return None


def repo_root(path: Path) -> Path:
    """The directory that holds templates/ and static/ for this index.html."""
    return path.resolve().parent.parent


def split_file_paths(path: Path) -> list[tuple[str, Path, str, int]]:
    """(page reference, file on disk, kind, size floor) for every split file,
    located next to the given index.html."""
    root = repo_root(path)
    return [(ref, root / rel, kind, floor) for ref, rel, kind, floor in SPLIT_FILES]


def read_split_sources(path: Path) -> dict[str, str]:
    """The text of every split file that exists on disk, keyed by reference."""
    out: dict[str, str] = {}
    for ref, fp, _kind, _floor in split_file_paths(path):
        if fp.is_file():
            out[ref] = fp.read_text(encoding="utf-8", errors="replace")
    return out


def check_split_files(path: Path, src: str) -> list[str]:
    """Each split file exists, is at least its floor, carries no Jinja, and
    is referenced by the page exactly once."""
    errs: list[str] = []
    for ref, fp, kind, floor in split_file_paths(path):
        n_ref = src.count(ref)
        if n_ref != 1:
            errs.append(f"split {kind} {ref!r} is referenced {n_ref}x by the page (must be exactly once)")
        if not fp.is_file():
            errs.append(f"split {kind} file missing: {fp} (the page references {ref!r}); "
                        f"the block it holds would never load")
            continue
        size = fp.stat().st_size
        if size < floor:
            errs.append(f"split {kind} file {fp.name} is {size:,} bytes, under its {floor:,}-byte floor "
                        f"(truncated or emptied)")
        text = fp.read_text(encoding="utf-8", errors="replace")
        if "{{" in text or "{%" in text:
            errs.append(f"split {kind} file {fp.name} carries Jinja; static files are served unrendered")
    return errs


def _size_at(ref: str, git_path: str) -> int | None:
    try:
        out = subprocess.run(
            ["git", "cat-file", "-s", f"{ref}:{git_path}"],
            capture_output=True, check=True, text=True, timeout=5,
        )
        return int(out.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


def bundle_size_at(ref: str, path: Path) -> int | None:
    """Size of the front-end bundle (index.html plus every split file that
    exists) at a git ref. None when the page itself is not at that ref or
    git is not reachable. A split file absent at the ref (a commit before
    the split) counts as 0, so a split reads as a move, not a shrink."""
    rel = str(path)
    for prefix_page in (rel, rel.replace("bg-webapp/", "", 1)):
        page = _size_at(ref, prefix_page)
        if page is None:
            continue
        prefixed = prefix_page.startswith("bg-webapp/")
        total = page
        for _r, split_rel, _k, _f in SPLIT_FILES:
            gp = ("bg-webapp/" + split_rel) if prefixed else split_rel
            total += _size_at(ref, gp) or 0
        return total
    return None


def bundle_size_on_disk(path: Path, page_bytes: int) -> int:
    total = page_bytes
    for _ref, fp, _kind, _floor in split_file_paths(path):
        if fp.is_file():
            total += fp.stat().st_size
    return total


def head_size_bytes(path: Path) -> int | None:
    """Return the size of the front-end bundle at HEAD (previous committed
    version): index.html plus the split files that exist there.

    Returns None if there is no HEAD version (initial commit, unknown
    file), or if git isn't reachable. Callers must handle None as a
    "no comparison possible" signal, not a failure.
    """
    try:
        return bundle_size_at("HEAD", path)
    except Exception:
        return None


def check_ends_with_html(src: str) -> str | None:
    """Return an error message if the file doesn't end with </html>."""
    tail = src.rstrip()
    if not tail.endswith("</html>"):
        # Show the actual last 80 chars so debugging is trivial.
        return (f"file does not end with </html> - most recent bytes: "
                f"...{tail[-80:]!r}. Almost always a tail truncation "
                f"introduced by a wholesale file rewrite.")
    return None


def check_script_tag_balance(src: str) -> str | None:
    """Return an error message if <script> and </script> counts differ.

    Uses a permissive regex for the open tag (matches attributes) and
    a literal count for the close tag (has no attributes). Raw counting
    includes any occurrences in HTML comments or template literals -
    those balance naturally too, and being strict here is fine.
    """
    opens = len(re.findall(r"<script(?:\s[^>]*)?>", src))
    closes = src.count("</script>")
    if opens != closes:
        return (f"<script> tag imbalance: {opens} open, {closes} close "
                f"(delta {opens - closes:+}). Browser will stop parsing "
                f"at the first unclosed tag and the dashboard will freeze "
                f"at the loading screen at 0%.")
    return None


def check_required_anchors(src: str, split_sources: dict[str, str] | None = None) -> list[str]:
    """Return a list of error messages, one per missing required anchor.

    An anchor counts as present when it is on the page or in one of the
    split files the page loads (``split_sources``: reference -> text)."""
    errs: list[str] = []
    extra = list((split_sources or {}).values())
    for anchor, desc in REQUIRED_ANCHORS:
        if anchor not in src and not any(anchor in t for t in extra):
            errs.append(f"missing required anchor {anchor!r} ({desc})")
    return errs


def check_forbidden_anchors(src: str) -> list[str]:
    """Return a list of error messages, one per present forbidden anchor."""
    errs: list[str] = []
    for anchor, desc in FORBIDDEN_ANCHORS:
        if anchor in src:
            errs.append(f"forbidden anchor {anchor!r} present ({desc})")
    return errs


def check_size(src_bytes: int) -> str | None:
    """Return an error message if the file is impossibly small."""
    if src_bytes < MIN_SIZE_BYTES:
        return (f"file size {src_bytes:,} bytes is under the {MIN_SIZE_BYTES:,}"
                f"-byte floor. This almost always means catastrophic truncation.")
    return None


# Regex to pull out every inline <script>...</script> body. Skips any
# tag that carries a `src=` attribute (those load external files and
# have no inline body to check).
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
    re.DOTALL,
)

# Jinja templating that Node cannot parse. `{{ ... }}` is a print
# expression; `{% ... %}` is a control block. Both are server-side
# rendered before the browser sees the file, so we substitute inert
# placeholders before running `node --check`.
_JINJA_PRINT_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_BLOCK_RE = re.compile(r"\{%.*?%\}", re.DOTALL)


def check_inline_js_syntax(src: str) -> tuple[str | None, str | None]:
    """Run ``node --check`` against the concatenated inline JS.

    Returns ``(error, warning)``:
        error   - hard failure string (blocks commit)
        warning - soft skip string (does not block)
    Exactly one of the two is non-None on any given call.

    This catches JS syntax errors inside an otherwise-balanced
    ``<script>`` block, which the tag-balance check above misses.
    Example real bug: a dangling ``ufc_methodology:`` property outside
    its parent object (see commit 4ee091fe / hotfix 78e14472,
    2026-08-18). File ends with ``</html>``, tags balance, all anchors
    present - and the dashboard still freezes because ``SyntaxError``
    halts inline JS at parse.

    Skips with a warning (not an error) when ``node`` is not on PATH,
    so environments without Node installed still pass the validator.
    """
    node = shutil.which("node")
    if node is None:
        return (None, "node not on PATH - skipping inline JS syntax "
                      "check. Install Node to enable this check locally "
                      "(GitHub Action always runs it).")

    scripts = _INLINE_SCRIPT_RE.findall(src)
    if not scripts:
        return (None, "no inline <script> blocks found - JS syntax "
                      "check skipped.")

    # Join with a semicolon-newline so a missing terminator in one
    # block doesn't bleed into the next and produce a misleading error
    # location. Also substitute Jinja templating so `{{...}}` doesn't
    # look like an object literal with an unexpected `{`.
    combined = "\n;\n".join(scripts)
    combined = _JINJA_PRINT_RE.sub("null", combined)
    combined = _JINJA_BLOCK_RE.sub("", combined)

    tmp_dir = tempfile.mkdtemp(prefix="validate_index_js_")
    tmp_js = os.path.join(tmp_dir, "inline.js")
    try:
        with open(tmp_js, "w", encoding="utf-8") as f:
            f.write(combined)
        result = subprocess.run(
            [node, "--check", tmp_js],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return (None, None)
        # Trim stderr to the first ~1500 chars so the error printout
        # stays scannable but includes the SyntaxError and its location.
        stderr = (result.stderr or "").strip()[:1500]
        return (
            f"inline JS syntax check failed via `node --check`. "
            f"A <script> block contains a JS parse error - the browser "
            f"will halt every inline block and the dashboard will freeze "
            f"at the loading screen.\n{stderr}",
            None,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def check_split_js_syntax(path: Path) -> tuple[list[str], str | None]:
    """``node --check`` on every split .js file that exists (the Digital
    Twins IQ script left the inline blocks on 2026-11-20, so the inline
    check above no longer sees it). Returns (errors, warning)."""
    node = shutil.which("node")
    if node is None:
        return ([], None)   # the inline check already warned once about node
    errs: list[str] = []
    for ref, fp, kind, _floor in split_file_paths(path):
        if kind != "script" or not fp.is_file():
            continue
        result = subprocess.run([node, "--check", str(fp)], capture_output=True, text=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:1500]
            errs.append(f"split script {fp.name} fails `node --check`; the browser would halt the "
                        f"Digital Twins IQ page at parse.\n{stderr}")
    return (errs, None)


def check_shrink_vs_head(cur_bytes: int, head_bytes: int) -> str | None:
    """Return a WARNING message (not error) if the file shrank sharply.

    Callers should print this as a warning and NOT fail on it - some
    refactors genuinely delete a lot in one commit. Failing here would
    cause too many false positives.
    """
    if head_bytes <= 0:
        return None
    shrink = 1.0 - (cur_bytes / head_bytes)
    if shrink > MAX_SHRINK_FRACTION:
        return (f"file shrunk by {shrink*100:.1f}% vs HEAD "
                f"({head_bytes:,} -> {cur_bytes:,} bytes). "
                f"If this is intentional (large refactor), ignore. "
                f"Otherwise check for accidental tail truncation.")
    return None


def origin_main_size_bytes(path: Path) -> int | None:
    """Return the size of the front-end bundle at ``origin/main`` (local
    refs only): index.html plus the split files that exist there.

    Uses the LOCAL refs cache only - does NOT ``git fetch`` so
    pre-commit stays fast (< 500 ms). Callers who need up-to-date
    origin should fetch first (the pre-push hook does).

    Returns None if origin/main is not in the local refs, or if git
    isn't reachable.
    """
    try:
        return bundle_size_at("origin/main", path)
    except Exception:
        return None


def check_shrink_vs_origin_main(cur_bytes: int, origin_bytes: int) -> str | None:
    """Return a WARNING message if the file shrank sharply vs origin/main.

    Distinct from ``check_shrink_vs_head`` because HEAD is the LAST
    LOCAL commit - if your session has been open for a while, HEAD is
    stale and comparing against it hides clobbers. origin/main is
    "what is actually live" and comparing against it catches the
    stale-working-tree clobber pattern that hit us on 2026-08-24 with
    commit 8c746970 ("Publish pending work to main") which reverted
    9,158 lines of newer work because the committing session had a
    stale index.html.

    When ``VALIDATE_STRICT_SHRINK=1`` is set in the environment, the
    caller (e.g. the pre-push hook) should treat this as a hard error
    instead of a warning. The caller decides - this function only
    returns the message.
    """
    if origin_bytes <= 0:
        return None
    shrink = 1.0 - (cur_bytes / origin_bytes)
    if shrink > MAX_SHRINK_FRACTION:
        return (f"file is {shrink*100:.1f}% smaller than origin/main "
                f"({origin_bytes:,} -> {cur_bytes:,} bytes). This is the "
                f"'stale working tree about to clobber newer work' "
                f"signature. Run `git fetch && git status` and confirm "
                f"your tree is up to date before committing. If this is "
                f"an intentional large deletion, document it in the "
                f"commit message.")
    return None


def main() -> int:
    if "--split-files" in sys.argv[1:]:
        # The pre-push hook asks the tip's validator which files travel with
        # the page, so it can extract them next to index.html and sum the
        # bundle the same way this file does.
        for _ref, rel, _kind, _floor in SPLIT_FILES:
            print(rel)
        return 0

    path = find_index_html()
    if path is None:
        print("[validate] templates/index.html not found from cwd - "
              "run from repo root or bg-webapp/ directory")
        return 2

    try:
        src_bytes = path.read_bytes()
    except OSError as e:
        print(f"[validate] failed to read {path}: {e}")
        return 2

    src = src_bytes.decode("utf-8", errors="replace")
    print(f"[validate] checking {path} ({len(src_bytes):,} bytes, "
          f"{src.count(chr(10)):,} lines)")

    errors: list[str] = []
    warnings: list[str] = []

    err = check_size(len(src_bytes))
    if err:
        errors.append(err)

    err = check_ends_with_html(src)
    if err:
        errors.append(err)

    err = check_script_tag_balance(src)
    if err:
        errors.append(err)

    split_sources = read_split_sources(path)
    errors.extend(check_split_files(path, src))
    errors.extend(check_required_anchors(src, split_sources))
    errors.extend(check_forbidden_anchors(src))

    js_err, js_warn = check_inline_js_syntax(src)
    if js_err:
        errors.append(js_err)
    if js_warn:
        warnings.append(js_warn)
    split_js_errs, _ = check_split_js_syntax(path)
    errors.extend(split_js_errs)

    # The shrink checks read the BUNDLE (page + split files), so a block
    # that moved into its own file is a move, not a shrink; a page that
    # lost a block with no file to show for it still trips them.
    bundle_bytes = bundle_size_on_disk(path, len(src_bytes))
    head_bytes = head_size_bytes(path)
    if head_bytes is not None:
        warn = check_shrink_vs_head(bundle_bytes, head_bytes)
        if warn:
            warnings.append(warn)

    # Freshness check vs origin/main. Pre-commit runs this as a warning
    # (fast, best-effort, no fetch). Pre-push runs the validator with
    # VALIDATE_STRICT_SHRINK=1 to promote the warning to a hard error,
    # because at push time we KNOW what's about to land on the remote
    # and a materially smaller tree is a clobber, not a refactor.
    origin_bytes = origin_main_size_bytes(path)
    if origin_bytes is not None:
        msg = check_shrink_vs_origin_main(bundle_bytes, origin_bytes)
        if msg:
            if os.environ.get("VALIDATE_STRICT_SHRINK") == "1":
                errors.append("(strict-shrink) " + msg)
            else:
                warnings.append(msg)

    if warnings:
        print("[validate] WARNINGS (not blocking):")
        for w in warnings:
            print(f"  ! {w}")

    if errors:
        print(f"[validate] FAIL: {len(errors)} error(s):")
        for e in errors:
            print(f"  x {e}")
        print()
        print("[validate] Commit blocked. The fastest recovery is usually:")
        print("           1. git show HEAD:templates/index.html > /tmp/prev.html")
        print("           2. Diff to find what got dropped.")
        print("           3. Splice the missing block back in and re-stage.")
        return 1

    js_status = "JS parses" if shutil.which("node") else "JS check skipped (no node)"
    print(f"[validate] OK ({len(REQUIRED_ANCHORS)} anchors present, "
          f"tags balanced, ends with </html>, {js_status}, "
          f"{len(SPLIT_FILES)} split files in place)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# trigger touch: force the validate workflow to run for app.py-only PRs
# (see fix/profile-image-hyphen-space - profile image hyphen/space lookup)
# (feat/profile-family-grouping 2026-09-08 - data-cut family sync + audit)
# (fix/bpiq-superadmin-journeys 2026-09-08 - super_admin resolves to '*')
# (title-lookup-tool 2026-09-14 - StreamScout 19-platform identifier tool, PR #75)
# (audible-platform 2026-09-16 - StreamScout #20 Audible: direct + via-Amazon links)
# (jenna-package-20 2026-09-16 - StreamScout Jenna package: 20-platform how-to + Excel)
# (gametool-package 2026-09-17 - GameTool: 19-store game URL resolver + hostmap terms)
# (gametool-ps-relevance 2026-09-17 - GameTool: tighten PlayStation match, no inflation)
# (gametool-hostmap-output 2026-09-17 - GameTool: BRAND/HOSTNAME/CATEGORY/SECTION sheet)
# (books-platform 2026-09-18 - StreamScout #21 Books: buy/listen/Libby + retail proxies)
# (books-clean-platform-labels 2026-09-18 - Books: clean retailer names in PLATFORM col)
# (gametool-live-first-party 2026-09-18 - GameTool: live Xbox/Nintendo/Epic search + Apple games-only)
# (gametool-tighten-googleplay 2026-09-18 - GameTool: Play games-only + sharper short-query prefix)
# (gametool-hostmap-rename 2026-09-18 - rename gametool/ -> gametool_hostmap/ (vs Books content-map))
# (streamscout-gametool-content 2026-09-18 - StreamScout gametool_content: content-map game terms)
# (jenna-package-21 2026-09-18 - Jenna package refresh: 21-platform Books + games content-map how-to/Excel)
# (jenna-package-games-row 2026-09-18 - coverage Excel: add Games (gametool_content) companion row)
# (games-as-platform 2026-09-18 - StreamScout #22 Games: gametool_content as a first-class menu resolver)
# (jenna-md-tidy 2026-09-18 - how-to: Books is resolver #21 wording (Games #22 is newest))
# (prometheus-routing-docs 2026-09-18 - Prometheus content_map-first routing spec + Jenna how-to)
# (streamscout-query-wrapper 2026-09-18 - streamscout_query() non-interactive entry point for Prometheus)
# (youtube-shorts-innertube 2026-09-21 - YouTube: reliable Shorts via InnerTube browse (paginated) for --include-shorts)
# (ss-match-gate-overmatch 2026-09-21 - shared relevance floor stops podcast/audio/YouTube over-match catalog dumps)
# (books-apple-english-base 2026-09-21 - Books/Apple: keep only US-English base editions (drop foreign-lang + dramatized/graphic-novel noise))
# (books-apple-keep-foreign-drop-variants 2026-09-21 - Books/Apple: keep foreign-language real editions, drop variants/fan-fic + buried-mention title matches)
# (audible-drop-variant-editions 2026-09-21 - shared variant/derivative drop in match_gate; applied on Audible + Apple)
# (appletv-relevance-gate 2026-09-22 - Apple TV resolver: match_gate floor, no least-bad fallback)
# (site-hero-kevin-hart-chip 2026-09-28 - Site: new Prometheus hero line; Kevin Hart Netflix chip on homepage + Subscriber IQ)
# (site-hero-lead-three-lines 2026-09-28 - Site: widen homepage hero lead to 68ch so it sets on three lines)
# (site-hero-10m-nbsp-gen-pop-matched 2026-09-28 - Site: keep "10 million" together; gen-pop-matched)
# (site-nav-panel-products-hero 2026-09-28 - Site: The Panel in top nav; Products hero headline + lead)
# (site-products-table-align-billing 2026-09-28 - Site: Products question column left-aligned; Attribution price shortened; billing note rewrite)
# (site-products-report-pricing 2026-09-28 - Site: Products 02 eyebrow Report pricing; headline One report, one price.)
# (site-signup-close-panel-lead 2026-09-28 - Site: sign-up close headline on 12 pages; Panel hero lead rewrite)
# (site-panel-how-it-works 2026-09-28 - Site: Panel section 02 How it works; single What clients receive card)
# (site-categories-35-plus 2026-09-28 - Site: behavioral categories 40+ -> 35+ everywhere)
# (site-panel-card-align-counted 2026-09-28 - Site: Panel card top-aligned with headline; What gets counted copy)
# (site-home-answer-section 2026-09-28 - Site: homepage The answer: question pill below eyebrow; new headline + lead)
# (site-home-answer-drop-so-what 2026-09-28 - Site: remove What this means note + rule from homepage The answer)
# (site-signup-rate-card 2026-09-29 - Site: signup rate card Flywheel $500; Trends/Rankers/Fin starts at $5000/mo)
# (pricing-flywheel-500-monthly-5000 2026-09-29 - Flywheel IQ $500/pull; Trends/Rankers/Fin $5000/mo defaults; site + Prometheus copy)
# (pricing-consistent-everywhere 2026-09-29 - Flywheel $500 + Trends/Rankers/Fin $5000/mo on budget pages, report pages, Prometheus pricing copy)
# (site-panel-drop-so-what 2026-10-01 - Site: remove What this means note + rule from Panel page)
# (site-panel-restore-so-what 2026-10-01 - Site: restore What this means note on Panel page per Jenna)
# (streamscout-npr-podcast 2026-10-14 - StreamScout: add NPR podcast resolver + wire YouTube/NPR into the podcasts group; CI trigger only)
# (streamscout-youtube-clips 2026-10-14 - StreamScout YouTube: full-vs-clips client choice + fair-survey even-stride cap (default 600); CI trigger only)
# (streamscout-jenna-package 2026-10-14 - StreamScout: explicit Jenna/Promy how-to MD + refreshed 23-platform xlsx (NPR + YouTube clips/cap); CI trigger only)
# (streamscout-siriusxm-anon-discovery 2026-10-16 - StreamScout SiriusXM: login-free title->show discovery via a shared web-search API key (Brave / Google Programmable Search) so client queries resolve anonymously; enumeration already anonymous; CI trigger only)
# (streamscout-platform-scope-guard 2026-10-16 - StreamScout: strict explicit-platform scope in streamscout_query - hard-block (PlatformScopeError / CLI exit 3) when a directly-named platform returns 0 seeds, so a client 'Hulu-only' request never silently profiles the wrong platform; CI trigger only)
# (streamscout-availability-advisory 2026-10-16 - StreamScout: advisory current-availability check (streamscout_availability.check_availability) via the shared web-search key - 'where to watch {title}' lookup, needs no resolve, flags (never blocks) when a requested platform doesn't currently carry the title so preloaded-seed profiles can't silently use a platform it has left; CI trigger only)

# ci-trigger: streamscout-scope-notify

# ci-trigger: streamscout-always-rescan

# ci-trigger: streamscout-viewer-terms

# ci-trigger: harden-cold-start-prewarm (pre-compile index.html at import; app.py-only PR #325)

# ci-trigger: streamscout-lookalike-guard-max-skip

# ci-trigger: streamscout-max-headless-replay

# ci-trigger: streamscout-netflix-profile-click-graphql-episodes

# ci-trigger: streamscout-lookalike-ambiguous-hold-for-review

# ci-trigger: streamscout-same-title-variant-disambiguation

# ci-trigger: streamscout-netflix-matched-show-name

# ci-trigger: streamscout-howto-amazon-multiid-note

# ci-trigger: streamscout-howto-netflix-headless-login-refresh

# ci-trigger: streamscout-howto-operator-tldr-box

# ci-trigger: streamscout-howto-amazon-pinned-url-required

# ci-trigger: streamscout-retire-scope-miss-email

# ci-trigger: streamscout-netflix-max-always-searching

# ci-trigger: streamscout-donation-backed-auth

# ci-trigger: streamscout-peacock-netflix-discovery

# ci-trigger: streamscout-doc-peacock-netflix
