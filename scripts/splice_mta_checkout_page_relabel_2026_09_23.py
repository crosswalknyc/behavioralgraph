#!/usr/bin/env python3
"""Attribution IQ vocabulary shift to checkout-page language.

Standing directive from Jenna 2026-09-23 (verbatim): "I dont want to
ever say bought a ticket, we dont want to model ticketing. we just
want to say traffic that went to the site and got to the checkoutpage
or something".

Attribution IQ is a clickstream-only read. We observe URL hits (a
checkout page view), never purchase completion. Every user-visible
string that reads "buyer" / "purchase" / "paid" as a completion claim
gets swapped to checkout-page-facing framing.

Paid MEDIA vocabulary stays (paid ads / paid influencer post / paid
social retarget), because "paid" there is paid ADVERTISING, not
purchase completion. The `4_paid` stage id also stays because it is
an internal identifier that code branches on.

Splices applied:

  bg-webapp/mta_iq.py:
    line 1281:  "ticket buyer"                     -> "checkout page visit"
    line 1949:  "Conversion: Ticket purchased"     -> "Reached the checkout page within 7d"
    line 1957:  "Conversion: {conv_action}"        -> "Reached: {conv_action}"
    line 2016:  "...but never paid, the bag..."    -> "...but never reached the checkout page..."
    line 2513:  "ticket purchase"                  -> "checkout page visit"
    lines 1830, 1852: "Paid a competing film"      -> "Reached checkout for a competing film"
    line 1841:  "Paid a competing family film"     -> "Reached checkout for a competing family film"
    lines 1831, 1853: leak_competing_note "bought" -> "reached the checkout page for"
    line 1842: leak_competing_note (family film)   -> same swap
    plus the film-default fallback near line 2200+

  bg-webapp/intent_iq.py:
    line 84:    "ticket buyer"                     -> "checkout page visit"
    line 85:    "buy a ticket"                     -> "reach the checkout page"
    line 86:    "ticketing sites"                  -> "checkout pages"

  bg-webapp/scripts/precompute_mta_v5_caches.py:
    line 72:    "Conversion:"                      -> "Reached the checkout page"

  bg-webapp/templates/index.html:
    ~line 33406: "Unique potential ticket buyers reached"
                        -> "Unique potential viewers reached"
    ~line 34663: attrPaidNoun ternary
                        isFilm ? "paid ticket purchase" : ...
                        -> isFilm ? "checkout page visit" : ...
    ~line 34665: "completed a " + attrPaidNoun
                        -> "reached a " + attrPaidNoun
    ~line 35000: "share of exposed viewers who visited the " + bfl + " page..."
                        (rewritten to hard-code "reached the checkout page"
                         for film campaigns; brand campaigns keep the
                         bfl-driven phrasing but "visited" -> "reached")

Every splice is anchored on a unique substring so re-runs are safe
(second run finds no match, exits clean). templates/index.html uses
the byte-level Python splice per index-html-safety.mdc; StrReplace on
that file corrupts the tail above ~8 MB.

Standing rules honored:
  * no-em-dashes.mdc: no em dashes anywhere in the new strings.
  * no-modeled-or-source-language.mdc: no "modeled" / "estimated" /
    "predicted" language introduced.
  * partner-api-no-internal-terms.mdc: no internal terminology
    exposed on the tab.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
ROOT = HERE.parent                              # finished_codes/

MTA_IQ         = HERE / "mta_iq.py"
INTENT_IQ      = HERE / "intent_iq.py"
PRECOMPUTE_V5  = HERE / "scripts" / "precompute_mta_v5_caches.py"
INDEX_HTML     = HERE / "templates" / "index.html"
BACKUP_DIR     = Path("/tmp")


def _splice(src: str, old: str, new: str, desc: str, path: Path) -> str:
    n = src.count(old)
    if n == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND in {path}\n  old={old!r}")
    if n > 1:
        raise RuntimeError(f"[{desc}] anchor found {n}x in {path}, not unique\n  old={old!r}")
    return src.replace(old, new)


def _try_splice(src: str, old: str, new: str, desc: str, path: Path) -> tuple[str, bool]:
    """Splice, but tolerate a missing anchor (idempotent re-runs)."""
    if new in src and old not in src:
        return src, False  # already applied
    if src.count(old) == 0:
        # Not found and replacement not present either. Report but do
        # not raise; the caller will decide.
        return src, False
    return _splice(src, old, new, desc, path), True


# ============ 1. mta_iq.py ============================================
def patch_mta_iq(dry_run: bool) -> int:
    src = MTA_IQ.read_text(encoding="utf-8")
    backup = BACKUP_DIR / "mta_iq.py.pre_checkout_relabel.py"
    if not dry_run:
        backup.write_text(src, encoding="utf-8")

    fixes = 0

    # 1a. conversion_noun film default (line 1281 region)
    old = '"signup" if ttype == "brand" else "ticket buyer"'
    new = '"signup" if ttype == "brand" else "checkout page visit"'
    src, ok = _try_splice(src, old, new, "conversion_noun film default", MTA_IQ)
    fixes += int(ok)

    # 1b. Nest stage-4 label (film branch, line 1949)
    old = '"4_paid":     "Conversion: Ticket purchased",'
    new = '"4_paid":     f"Reached the checkout page within {window_days}d",'
    src, ok = _try_splice(src, old, new, "nest stage-4 film label", MTA_IQ)
    fixes += int(ok)

    # 1c. Nest stage-4 label (non-film branch, line 1957)
    old = '"4_paid":     f"Conversion: {conv_action}",'
    new = '"4_paid":     f"Reached: {conv_action}",'
    src, ok = _try_splice(src, old, new, "nest stage-4 non-film label", MTA_IQ)
    fixes += int(ok)

    # 1d. leak note for "conv_visit_no_pay" (line 2016)
    old = '"Opened a ticketer within 7d but never paid, the bag-abandon equivalent",'
    new = '"Opened a ticketer within 7d but never reached the checkout page, the bag-abandon equivalent",'
    src, ok = _try_splice(src, old, new, "leak conv_visit_no_pay note", MTA_IQ)
    fixes += int(ok)

    # 1e. paths_conversion_noun (line 2513)
    old = '        paths_conversion_noun = "ticket purchase"'
    new = '        paths_conversion_noun = "checkout page visit"'
    src, ok = _try_splice(src, old, new, "paths_conversion_noun film", MTA_IQ)
    fixes += int(ok)

    # 1f. Every film-config leak_competing_label + leak_competing_note.
    # Two variants: "Paid a competing film" and "Paid a competing family
    # film". Both appear multiple times in _CARD_CONFIG so we do a global
    # replace_all-style pass by iterating manually.
    for old, new, desc in [
        ('"leak_competing_label":        "Paid a competing film",',
         '"leak_competing_label":        "Reached checkout for a competing film",',
         "leak_competing_label film"),
        ('"leak_competing_label":        "Paid a competing family film",',
         '"leak_competing_label":        "Reached checkout for a competing family film",',
         "leak_competing_label family film"),
        # leak_competing_note: "bought a different film" -> "reached the
        # checkout page for a different film". Two variants share the same
        # substring so a substring swap catches both.
        ('bought a "\n                                          "different film',
         'reached the checkout page for a "\n                                          "different film',
         "leak_competing_note bought a different film"),
        ('bought a "\n                                          "different family film',
         'reached the checkout page for a "\n                                          "different family film',
         "leak_competing_note bought a different family film"),
    ]:
        n = src.count(old)
        if n == 0 and new not in src:
            print(f"  [warn] {desc}: anchor not found and replacement not present")
            continue
        src = src.replace(old, new)
        fixes += n

    if not dry_run:
        MTA_IQ.write_text(src, encoding="utf-8")
    print(f"[mta_iq.py] {fixes} splice(s); backup /tmp/mta_iq.py.pre_checkout_relabel.py")
    return fixes


# ============ 2. intent_iq.py =========================================
def patch_intent_iq(dry_run: bool) -> int:
    src = INTENT_IQ.read_text(encoding="utf-8")
    backup = BACKUP_DIR / "intent_iq.py.pre_checkout_relabel.py"
    if not dry_run:
        backup.write_text(src, encoding="utf-8")

    fixes = 0
    for old, new, desc in [
        ('"conversion_noun":            "ticket buyer",',
         '"conversion_noun":            "checkout page visit",',
         "conversion_noun film"),
        ('"conversion_verb":            "buy a ticket",',
         '"conversion_verb":            "reach the checkout page",',
         "conversion_verb film"),
        ('"conversion_endpoint_label":  "ticketing sites",',
         '"conversion_endpoint_label":  "checkout pages",',
         "conversion_endpoint_label film"),
    ]:
        src, ok = _try_splice(src, old, new, desc, INTENT_IQ)
        fixes += int(ok)

    if not dry_run:
        INTENT_IQ.write_text(src, encoding="utf-8")
    print(f"[intent_iq.py] {fixes} splice(s); backup /tmp/intent_iq.py.pre_checkout_relabel.py")
    return fixes


# ============ 3. precompute_mta_v5_caches.py ==========================
def patch_precompute_v5(dry_run: bool) -> int:
    src = PRECOMPUTE_V5.read_text(encoding="utf-8")
    backup = BACKUP_DIR / "precompute_mta_v5_caches.py.pre_checkout_relabel.py"
    if not dry_run:
        backup.write_text(src, encoding="utf-8")

    old = '"4_paid":     "Conversion:",'
    new = '"4_paid":     "Reached the checkout page",'
    src, ok = _try_splice(src, old, new, "v5 stage-prefix 4_paid", PRECOMPUTE_V5)

    if not dry_run:
        PRECOMPUTE_V5.write_text(src, encoding="utf-8")
    print(f"[precompute_mta_v5_caches.py] {int(ok)} splice(s); "
          f"backup /tmp/precompute_mta_v5_caches.py.pre_checkout_relabel.py")
    return int(ok)


# ============ 4. templates/index.html (byte-level splice) =============
def patch_index_html(dry_run: bool) -> int:
    src = INDEX_HTML.read_text(encoding="utf-8")
    backup = BACKUP_DIR / "index.html.pre_checkout_relabel.html"
    if not dry_run:
        backup.write_text(src, encoding="utf-8")

    fixes = 0
    original_size = len(src)

    # 4a. Conversion IQ hero tile: "Unique potential ticket buyers reached"
    # This is the CONV-IQ tile, not the MTA tile. It's a REACH count of
    # people who could potentially reach a checkout page, not a checkout
    # count. Rename to "Unique potential viewers reached" so it stops
    # promising anything about ticket purchase.
    old = "iiqSummaryTile('Unique potential ticket buyers reached', fmtCompact(uniqueReached),"
    new = "iiqSummaryTile('Unique potential viewers reached', fmtCompact(uniqueReached),"
    src, ok = _try_splice(src, old, new, "convIQ unique reach tile", INDEX_HTML)
    fixes += int(ok)

    # 4b. MTA attribution paid noun (line ~34663)
    old = "var attrPaidNoun = isFilm ? 'paid ticket purchase' : escapeHtml(overallConvNoun);"
    new = "var attrPaidNoun = isFilm ? 'checkout page visit' : escapeHtml(overallConvNoun);"
    src, ok = _try_splice(src, old, new, "MTA attrPaidNoun ternary", INDEX_HTML)
    fixes += int(ok)

    # 4c. MTA attribution table sentence: "completed a X"
    # Full anchor is on a single line at ~34667; the closing is
    # `.</div>` not `.'` alone.
    old = "' US accounts that completed a ' + attrPaidNoun + '.</div>'"
    new = "' US accounts that reached a ' + attrPaidNoun + '.</div>'"
    src, ok = _try_splice(src, old, new, "MTA attribution completed-a sentence", INDEX_HTML)
    fixes += int(ok)

    # 4d. Baseline conversion rate tile subtitle
    # Rewrite: for film campaigns, hard-code "reached the checkout page";
    # for other types, keep bfl-driven phrasing but "visited" -> "reached".
    # Anchor is a single line at column ~35000; splice preserves it as one line.
    old = "iiqSummaryTile('Baseline conversion rate', _iiqMTAFmtPct(activePayload.conversion_rate, 2), 'share of exposed viewers who visited the ' + bfl.toLowerCase() + ' page in the attribution window'),"
    new = "iiqSummaryTile('Baseline conversion rate', _iiqMTAFmtPct(activePayload.conversion_rate, 2), ((activePayload.title_type === 'film') ? 'share of exposed viewers who reached the checkout page in the attribution window' : 'share of exposed viewers who reached the ' + bfl.toLowerCase() + ' page in the attribution window')),"
    src, ok = _try_splice(src, old, new, "MTA baseline conversion tile subtitle", INDEX_HTML)
    fixes += int(ok)

    if not dry_run:
        INDEX_HTML.write_text(src, encoding="utf-8")
    print(f"[templates/index.html] {fixes} splice(s); "
          f"size {original_size:,} -> {len(src):,} bytes  "
          f"(delta {len(src) - original_size:+,})")
    print(f"  backup: /tmp/index.html.pre_checkout_relabel.html")
    return fixes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    print("=== Attribution IQ checkout-page relabel ===")
    print(f"  --dry-run: {args.dry_run}")
    print()

    total = 0
    total += patch_mta_iq(args.dry_run)
    total += patch_intent_iq(args.dry_run)
    total += patch_precompute_v5(args.dry_run)
    total += patch_index_html(args.dry_run)

    print()
    print(f"TOTAL splices: {total}")

    if not args.dry_run:
        # Run the index.html validator to confirm no tail truncation.
        import subprocess
        r = subprocess.run(
            ["python3", str(HERE / "scripts" / "validate_index_html.py")],
            capture_output=True, text=True,
        )
        print()
        print("== validate_index_html.py ==")
        print(r.stdout.strip() or "(no stdout)")
        if r.returncode != 0:
            print(r.stderr.strip())
            print("[ABORT] validator failed; roll back with the /tmp backups")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
