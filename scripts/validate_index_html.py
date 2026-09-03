#!/usr/bin/env python3
# NOTE: The companion GitHub Action (.github/workflows/validate-index-html.yml)
# is path-filtered to templates/index.html, this validator, and the workflow
# file. PRs that touch none of those (e.g. login.html-only changes) leave the
# required `validate` check pending and unmergeable. Someone with `workflow`
# scope should broaden the workflow's pull_request trigger to run on all PRs.
# (Until that lands, non-index.html PRs touch this file to trigger the check.)
# Trigger touch: gate Trends-IQ data endpoint per product (Trends vs Rankers cards).
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
    ('id="jiqSpeTabBar"',
     "SPE Cross-Window story-mode tab bar (Journey IQ)"),
    ('id="jiqSpeCaseCard"',
     "SPE Cross-Window story-mode case card (Journey IQ)"),
    ('id="jiqAmcCaseCard"',
     "AMC story-mode case card (Journey IQ)"),
    ('id="brandTrackingIQView"',
     "Brand Tracking product view (CNBC Pro competitive set)"),
    ('showBrandTrackingIQ',
     "Brand Tracking showBrandTrackingIQ() entry point"),
    ('value="brandTrackingIQ"',
     "Brand Tracking SELECT PRODUCT dropdown option"),
]

# Anchors we DO NOT want in the file (false negatives). Left empty for
# now; add here if a bad string keeps re-appearing.
FORBIDDEN_ANCHORS: list[tuple[str, str]] = []


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


def head_size_bytes(path: Path) -> int | None:
    """Return the size of the file at HEAD (previous committed version).

    Returns None if there is no HEAD version (initial commit, unknown
    file), or if git isn't reachable. Callers must handle None as a
    "no comparison possible" signal, not a failure.
    """
    try:
        rel = str(path)
        # Try from cwd first; then try bg-webapp/-relative if we're in
        # the parent repo where the file path is prefixed.
        for git_path in (rel, rel.replace("bg-webapp/", "", 1)):
            try:
                out = subprocess.run(
                    ["git", "show", f"HEAD:{git_path}"],
                    capture_output=True, check=True,
                )
                return len(out.stdout)
            except subprocess.CalledProcessError:
                continue
        return None
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


def check_required_anchors(src: str) -> list[str]:
    """Return a list of error messages, one per missing required anchor."""
    errs: list[str] = []
    for anchor, desc in REQUIRED_ANCHORS:
        if anchor not in src:
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
    """Return the size of the file at ``origin/main`` (local refs only).

    Uses the LOCAL refs cache only - does NOT ``git fetch`` so
    pre-commit stays fast (< 500 ms). Callers who need up-to-date
    origin should fetch first (the pre-push hook does).

    Returns None if origin/main is not in the local refs, or if git
    isn't reachable.
    """
    try:
        rel = str(path)
        # Try both possible prefixes so this works from the submodule
        # root (templates/index.html) or from the parent repo
        # (bg-webapp/templates/index.html).
        for git_path in (rel, rel.replace("bg-webapp/", "", 1)):
            try:
                out = subprocess.run(
                    ["git", "cat-file", "-s", f"origin/main:{git_path}"],
                    capture_output=True, check=True, text=True, timeout=5,
                )
                return int(out.stdout.strip())
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                    ValueError):
                continue
        return None
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

    errors.extend(check_required_anchors(src))
    errors.extend(check_forbidden_anchors(src))

    js_err, js_warn = check_inline_js_syntax(src)
    if js_err:
        errors.append(js_err)
    if js_warn:
        warnings.append(js_warn)

    head_bytes = head_size_bytes(path)
    if head_bytes is not None:
        warn = check_shrink_vs_head(len(src_bytes), head_bytes)
        if warn:
            warnings.append(warn)

    # Freshness check vs origin/main. Pre-commit runs this as a warning
    # (fast, best-effort, no fetch). Pre-push runs the validator with
    # VALIDATE_STRICT_SHRINK=1 to promote the warning to a hard error,
    # because at push time we KNOW what's about to land on the remote
    # and a materially smaller tree is a clobber, not a refactor.
    origin_bytes = origin_main_size_bytes(path)
    if origin_bytes is not None:
        msg = check_shrink_vs_origin_main(len(src_bytes), origin_bytes)
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
          f"tags balanced, ends with </html>, {js_status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# trigger touch: force the validate workflow to run for app.py-only PRs
# (see fix/profile-image-hyphen-space - profile image hyphen/space lookup)
