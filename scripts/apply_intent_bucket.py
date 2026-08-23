#!/usr/bin/env python3
"""Move SHOPPING INTENT under BRAND > INTENT sub-bucket in the profile selector.

User directive 2026-08-23 (Jenna):
> "add intent as a category under brands on profile selector and put
>  shopping intent in it as a subcategory"

Changes:
  - templates/index.html
      * MASTER_CATEGORIES.BRAND gains 'SHOPPING INTENT' (alphabetical)
      * MASTER_CATEGORIES.TRENDS loses 'SHOPPING INTENT'
      * New CATEGORY_SUB_BUCKETS map added after MASTER_CATEGORY_ICONS
      * renderDashboardProfileTree else-branch refactored so any category
        that appears in CATEGORY_SUB_BUCKETS[master] renders under an
        intermediate collapsible header
  - templates/admin.html: same MASTER_CATEGORIES move (2 copies)
  - iq_rankers.py: same MASTER_CATEGORIES move
  - image_backfill.py: same MASTER_CATEGORIES move

Byte-safe (per index-html-safety.mdc): every splice is a Python
`str.replace` on a unique anchor with a count-guard before write. No
StrReplace / Write on templates/index.html.
"""

from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]  # bg-webapp/
INDEX = REPO / "templates" / "index.html"
ADMIN = REPO / "templates" / "admin.html"
IQ_RANKERS = REPO / "iq_rankers.py"
IMAGE_BACKFILL = REPO / "image_backfill.py"

STAMP = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
BACKUP_DIR = Path("/tmp")


def splice(src: str, old: str, new: str, desc: str) -> str:
    """Replace exactly one occurrence of `old` with `new`. Raise otherwise."""
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x, needs to be unique")
    return src.replace(old, new)


# --- index.html ------------------------------------------------------------

INDEX_OLD_BRAND = "'BRAND': ['ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'AUTOMOBILE', 'B2B', 'BANK', 'BANKS', 'BANKING', 'BEAUTY', 'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EVENTS', 'FESTIVAL', 'FOOTWEAR', 'FRANCHISE', 'GROCERY', 'INTIMATES', 'JEWELRY', 'LOYALTY PROGRAMS', 'MEMBERSHIP', 'NON PROFIT/CHARITY', 'PHARMA', 'QSR', 'RETAILERS', 'SECURITY', 'SWEEPSTAKES', 'TELECOM', 'TICKETING', 'TOY', 'TRAVEL', 'VENUE', 'WORKOUT FACILITY'],"

INDEX_NEW_BRAND = "'BRAND': ['ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'AUTOMOBILE', 'B2B', 'BANK', 'BANKS', 'BANKING', 'BEAUTY', 'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EVENTS', 'FESTIVAL', 'FOOTWEAR', 'FRANCHISE', 'GROCERY', 'INTIMATES', 'JEWELRY', 'LOYALTY PROGRAMS', 'MEMBERSHIP', 'NON PROFIT/CHARITY', 'PHARMA', 'QSR', 'RETAILERS', 'SECURITY', 'SHOPPING INTENT', 'SWEEPSTAKES', 'TELECOM', 'TICKETING', 'TOY', 'TRAVEL', 'VENUE', 'WORKOUT FACILITY'],"

INDEX_OLD_TRENDS = "            'TRENDS': ['TRENDS', 'SHOPPING INTENT'],"
INDEX_NEW_TRENDS = "            'TRENDS': ['TRENDS'],"

INDEX_OLD_ICONS_TAIL = """            'SVOD ACQUISITION': '📺'  // TV icon for SVOD
        };
        
        // Display labels for sub-categories. 2026-06-15 (Jenna): standardize"""

INDEX_NEW_ICONS_TAIL = """            'SVOD ACQUISITION': '📺'  // TV icon for SVOD
        };

        // Sub-bucket registry: categories under a master that render under
        // an intermediate collapsible header (a "sub-bucket") rather than
        // as direct siblings of other leaf sub-categories. Data-driven so
        // additional sub-buckets slot in without touching render code.
        //
        // Shape: { <master>: { <bucket display name>: [ <leaf category>, ... ] } }
        //
        // 2026-08-23 (Jenna): "add intent as a category under brands on
        // profile selector and put shopping intent in it as a subcategory".
        // SHOPPING INTENT now renders as:
        //   📦 BRAND > INTENT > SHOPPING INTENT > profiles
        // Prior placement was under TRENDS master; moved into BRAND to
        // keep intent audiences alongside brand pulls where operators
        // browse them.
        const CATEGORY_SUB_BUCKETS = {
            'BRAND': {
                'INTENT': ['SHOPPING INTENT']
            }
        };
        
        // Display labels for sub-categories. 2026-06-15 (Jenna): standardize"""

# Replace the entire else-branch that renders per-category (line ~110660
# through the closing brace of `else { Object.keys(subcats).sort().forEach(...) }`).
INDEX_OLD_ELSE_BRANCH = """                } else {
                    Object.keys(subcats).sort().forEach(cat => {
                        const items = subcats[cat];
                        const catId = 'dash_' + cat.replace(/[^a-zA-Z0-9]/g, '_');
                        const isExpanded = expandedDashCategories.has(catId);
                        const sortedItems = [...items].sort((a, b) => {
                            const nameA = getProfileFullName(a).toLowerCase();
                            const nameB = getProfileFullName(b).toLowerCase();
                            return nameA.localeCompare(nameB);
                        });
                        const bySubject = {};
                        const displayNames = {};
                        sortedItems.forEach(p => {
                            const subj = getProfileGroupKey(p) || (getProfileFullName(p) || '').toLowerCase();
                            if (!bySubject[subj]) {
                                bySubject[subj] = [];
                                displayNames[subj] = getProfileSuffixInfo(p).canonicalName || getProfileFullName(p);
                            }
                            bySubject[subj].push(p);
                        });
                        mergeStubGroupsIntoCanonical(bySubject, displayNames);

                        // MOVIE sub-bucket by genre (per user request
                        // 2026-06-18). Backend extracts genre from the
                        // 'MOVIE - <Genre>' prefix on BRAND INPUT and
                        // surfaces it as `movie_genre` on each profile.
                        // We group the subject-level rows by genre and
                        // render genre as an intermediate, collapsible
                        // tree node. A subject with multiple runs uses
                        // its base run's genre (all cohorts of the same
                        // movie share genre). Subjects without a genre
                        // fall into 'OTHER' so nothing disappears.
                        const _isMovieCat = (cat || '').toUpperCase() === 'MOVIE';
                        const _renderSubjectRow = (subj) => {
                            const runs = bySubject[subj];
                            const first = runs[0];
                            if (runs.length === 1) {
                                return _profileItemHtml(first, escapeAttr(getProfileFullName(first)));
                            }
                            const displayNameAttr = escapeAttr(displayNames[subj] || getProfileSuffixInfo(first).canonicalName || getProfileFullName(first));
                            const base = _pickBaseRun(runs) || first;
                            return _profileItemHtml(base, displayNameAttr);
                        };

                        let itemsHtml = '';
                        if (_isMovieCat) {
                            // Group subjects by upper-cased genre key
                            // (display in caps to match the rest of
                            // the selector - user rule 2026-06-17:
                            // "display should be all caps all the
                            // time"). Track an original-cased label
                            // for display niceness if all profiles in
                            // a bucket agreed on casing.
                            const byGenre = {};
                            Object.keys(bySubject).forEach(subj => {
                                const runs = bySubject[subj];
                                const base = _pickBaseRun(runs) || runs[0];
                                const rawGenre = (base && (base.movie_genre || '')) || '';
                                const key = rawGenre.trim().toUpperCase() || 'OTHER';
                                if (!byGenre[key]) byGenre[key] = [];
                                byGenre[key].push(subj);
                            });
                            const genreKeys = Object.keys(byGenre).sort((a, b) => {
                                // 'OTHER' sinks to the bottom
                                if (a === 'OTHER' && b !== 'OTHER') return 1;
                                if (b === 'OTHER' && a !== 'OTHER') return -1;
                                return a.localeCompare(b);
                            });
                            genreKeys.forEach(gKey => {
                                const subjsInGenre = byGenre[gKey].sort((a, b) => a.localeCompare(b));
                                const genreId = 'dash_MOVIE_GENRE_' + gKey.replace(/[^a-zA-Z0-9]/g, '_');
                                const isGenreExpanded = expandedDashCategories.has(genreId);
                                let genreItemsHtml = '';
                                subjsInGenre.forEach(subj => { genreItemsHtml += _renderSubjectRow(subj); });
                                itemsHtml += `
                                    <div class="profile-cat-item ${isGenreExpanded ? 'open' : ''}" onclick="event.stopPropagation(); toggleDashCategory('${genreId}')" style="padding-left: 0.6rem;">
                                        <span class="cat-arrow">▸</span>
                                        <span class="profile-cat-label">${escapeAttr(gKey)}</span>
                                        <span class="profile-cat-count">(${subjsInGenre.length})</span>
                                    </div>
                                    <div class="profile-cat-children ${isGenreExpanded ? 'open' : ''}" id="cat-${genreId}" style="padding-left: 0.5rem;">
                                        ${genreItemsHtml}
                                    </div>
                                `;
                            });
                        } else {
                            Object.keys(bySubject).sort((a, b) => a.localeCompare(b)).forEach(subj => {
                                itemsHtml += _renderSubjectRow(subj);
                            });
                        }
                        // Subcat count = distinct subjects shown in the
                        // tree (post-mergeStubGroupsIntoCanonical), not
                        // file count. Matches the per-row grouping.
                        const _subcatCount = Object.keys(bySubject).length;
                        html += `
                            <div class="profile-cat-item ${isExpanded ? 'open' : ''}" onclick="event.stopPropagation(); toggleDashCategory('${catId}')">
                                <span class="cat-arrow">▸</span>
                                <span class="profile-cat-label">${getCategoryDisplayLabel(cat)}</span>
                                <span class="profile-cat-count">(${_subcatCount})</span>
                            </div>
                            <div class="profile-cat-children ${isExpanded ? 'open' : ''}" id="cat-${catId}" style="padding-left: 0.5rem;">
                                ${itemsHtml}
                            </div>
                        `;
                    });
                }"""

INDEX_NEW_ELSE_BRANCH = """                } else {
                    // Sub-bucket registry: some sub-categories render under
                    // an intermediate collapsible header (a "sub-bucket")
                    // rather than as direct siblings. See CATEGORY_SUB_BUCKETS
                    // near MASTER_CATEGORIES. Data-driven so new buckets
                    // (e.g. INTENT > SHOPPING INTENT / PURCHASE INTENT)
                    // slot in without changing this render code.
                    const _bucketsForMaster = (typeof CATEGORY_SUB_BUCKETS !== 'undefined' && CATEGORY_SUB_BUCKETS[master]) || {};
                    const _catToBucket = {};
                    Object.keys(_bucketsForMaster).forEach(bname => {
                        (_bucketsForMaster[bname] || []).forEach(leaf => {
                            _catToBucket[(leaf || '').toUpperCase()] = bname;
                        });
                    });

                    // Renders one leaf sub-category (existing behavior
                    // factored into a closure so a bucket wrapper can
                    // call it too). Returns { html, subjectCount } so
                    // the caller can roll counts into a parent bucket.
                    const _emitLeafCatBlock = (cat, items) => {
                        const catId = 'dash_' + cat.replace(/[^a-zA-Z0-9]/g, '_');
                        const isExpanded = expandedDashCategories.has(catId);
                        const sortedItems = [...items].sort((a, b) => {
                            const nameA = getProfileFullName(a).toLowerCase();
                            const nameB = getProfileFullName(b).toLowerCase();
                            return nameA.localeCompare(nameB);
                        });
                        const bySubject = {};
                        const displayNames = {};
                        sortedItems.forEach(p => {
                            const subj = getProfileGroupKey(p) || (getProfileFullName(p) || '').toLowerCase();
                            if (!bySubject[subj]) {
                                bySubject[subj] = [];
                                displayNames[subj] = getProfileSuffixInfo(p).canonicalName || getProfileFullName(p);
                            }
                            bySubject[subj].push(p);
                        });
                        mergeStubGroupsIntoCanonical(bySubject, displayNames);

                        // MOVIE sub-bucket by genre (per user request
                        // 2026-06-18). Backend extracts genre from the
                        // 'MOVIE - <Genre>' prefix on BRAND INPUT and
                        // surfaces it as `movie_genre` on each profile.
                        // We group the subject-level rows by genre and
                        // render genre as an intermediate, collapsible
                        // tree node. A subject with multiple runs uses
                        // its base run's genre (all cohorts of the same
                        // movie share genre). Subjects without a genre
                        // fall into 'OTHER' so nothing disappears.
                        const _isMovieCat = (cat || '').toUpperCase() === 'MOVIE';
                        const _renderSubjectRow = (subj) => {
                            const runs = bySubject[subj];
                            const first = runs[0];
                            if (runs.length === 1) {
                                return _profileItemHtml(first, escapeAttr(getProfileFullName(first)));
                            }
                            const displayNameAttr = escapeAttr(displayNames[subj] || getProfileSuffixInfo(first).canonicalName || getProfileFullName(first));
                            const base = _pickBaseRun(runs) || first;
                            return _profileItemHtml(base, displayNameAttr);
                        };

                        let itemsHtml = '';
                        if (_isMovieCat) {
                            // Group subjects by upper-cased genre key
                            // (display in caps to match the rest of
                            // the selector - user rule 2026-06-17:
                            // "display should be all caps all the
                            // time"). Track an original-cased label
                            // for display niceness if all profiles in
                            // a bucket agreed on casing.
                            const byGenre = {};
                            Object.keys(bySubject).forEach(subj => {
                                const runs = bySubject[subj];
                                const base = _pickBaseRun(runs) || runs[0];
                                const rawGenre = (base && (base.movie_genre || '')) || '';
                                const key = rawGenre.trim().toUpperCase() || 'OTHER';
                                if (!byGenre[key]) byGenre[key] = [];
                                byGenre[key].push(subj);
                            });
                            const genreKeys = Object.keys(byGenre).sort((a, b) => {
                                // 'OTHER' sinks to the bottom
                                if (a === 'OTHER' && b !== 'OTHER') return 1;
                                if (b === 'OTHER' && a !== 'OTHER') return -1;
                                return a.localeCompare(b);
                            });
                            genreKeys.forEach(gKey => {
                                const subjsInGenre = byGenre[gKey].sort((a, b) => a.localeCompare(b));
                                const genreId = 'dash_MOVIE_GENRE_' + gKey.replace(/[^a-zA-Z0-9]/g, '_');
                                const isGenreExpanded = expandedDashCategories.has(genreId);
                                let genreItemsHtml = '';
                                subjsInGenre.forEach(subj => { genreItemsHtml += _renderSubjectRow(subj); });
                                itemsHtml += `
                                    <div class="profile-cat-item ${isGenreExpanded ? 'open' : ''}" onclick="event.stopPropagation(); toggleDashCategory('${genreId}')" style="padding-left: 0.6rem;">
                                        <span class="cat-arrow">▸</span>
                                        <span class="profile-cat-label">${escapeAttr(gKey)}</span>
                                        <span class="profile-cat-count">(${subjsInGenre.length})</span>
                                    </div>
                                    <div class="profile-cat-children ${isGenreExpanded ? 'open' : ''}" id="cat-${genreId}" style="padding-left: 0.5rem;">
                                        ${genreItemsHtml}
                                    </div>
                                `;
                            });
                        } else {
                            Object.keys(bySubject).sort((a, b) => a.localeCompare(b)).forEach(subj => {
                                itemsHtml += _renderSubjectRow(subj);
                            });
                        }
                        // Subcat count = distinct subjects shown in the
                        // tree (post-mergeStubGroupsIntoCanonical), not
                        // file count. Matches the per-row grouping.
                        const _subcatCount = Object.keys(bySubject).length;
                        const _leafHtml = `
                            <div class="profile-cat-item ${isExpanded ? 'open' : ''}" onclick="event.stopPropagation(); toggleDashCategory('${catId}')">
                                <span class="cat-arrow">▸</span>
                                <span class="profile-cat-label">${getCategoryDisplayLabel(cat)}</span>
                                <span class="profile-cat-count">(${_subcatCount})</span>
                            </div>
                            <div class="profile-cat-children ${isExpanded ? 'open' : ''}" id="cat-${catId}" style="padding-left: 0.5rem;">
                                ${itemsHtml}
                            </div>
                        `;
                        return { html: _leafHtml, subjectCount: _subcatCount };
                    };

                    // Partition present categories into standalone leaves
                    // vs bucket members, then build a unified render list
                    // sorted by display label so buckets interleave
                    // alphabetically with unbucketed leaves.
                    const _bucketedByBucket = {};
                    const _standaloneCats = [];
                    Object.keys(subcats).forEach(cat => {
                        const bname = _catToBucket[(cat || '').toUpperCase()];
                        if (bname) {
                            if (!_bucketedByBucket[bname]) _bucketedByBucket[bname] = {};
                            _bucketedByBucket[bname][cat] = subcats[cat];
                        } else {
                            _standaloneCats.push(cat);
                        }
                    });
                    const _renderables = [];
                    _standaloneCats.forEach(cat => {
                        _renderables.push({
                            kind: 'leaf',
                            sortKey: (getCategoryDisplayLabel(cat) || cat || '').toString().toUpperCase(),
                            cat: cat
                        });
                    });
                    Object.keys(_bucketedByBucket).forEach(bname => {
                        _renderables.push({
                            kind: 'bucket',
                            sortKey: (bname || '').toString().toUpperCase(),
                            bucketName: bname,
                            leafCats: _bucketedByBucket[bname]
                        });
                    });
                    _renderables.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

                    _renderables.forEach(r => {
                        if (r.kind === 'leaf') {
                            html += _emitLeafCatBlock(r.cat, subcats[r.cat]).html;
                        } else {
                            const bucketId = 'dash_bucket_' + master.replace(/[^a-zA-Z0-9]/g, '_') + '_' + r.bucketName.replace(/[^a-zA-Z0-9]/g, '_');
                            const isBucketExpanded = expandedDashCategories.has(bucketId);
                            let _bucketInnerHtml = '';
                            const _uniqueBucketSubjects = new Set();
                            Object.keys(r.leafCats).sort().forEach(leafCat => {
                                const emitted = _emitLeafCatBlock(leafCat, r.leafCats[leafCat]);
                                _bucketInnerHtml += emitted.html;
                                // Count distinct subjects across all leaf
                                // cats in this bucket for the bucket
                                // header count, matching how master count
                                // is computed above.
                                (r.leafCats[leafCat] || []).forEach(p => {
                                    const subj = getProfileGroupKey(p) || (getProfileFullName(p) || '').toLowerCase();
                                    _uniqueBucketSubjects.add(subj);
                                });
                            });
                            const _bucketCount = _uniqueBucketSubjects.size;
                            html += `
                                <div class="profile-cat-item ${isBucketExpanded ? 'open' : ''}" onclick="event.stopPropagation(); toggleDashCategory('${bucketId}')">
                                    <span class="cat-arrow">▸</span>
                                    <span class="profile-cat-label">${escapeAttr(r.bucketName)}</span>
                                    <span class="profile-cat-count">(${_bucketCount})</span>
                                </div>
                                <div class="profile-cat-children ${isBucketExpanded ? 'open' : ''}" id="cat-${bucketId}" style="padding-left: 0.5rem;">
                                    ${_bucketInnerHtml}
                                </div>
                            `;
                        }
                    });
                }"""


def patch_index_html():
    print(f"[index.html] reading {INDEX}")
    src = INDEX.read_text(encoding="utf-8")
    backup = BACKUP_DIR / f"index.pre_intent_bucket_{STAMP}.html"
    backup.write_text(src, encoding="utf-8")
    print(f"[index.html] backed up to {backup} ({len(src):,} bytes)")

    src = splice(src, INDEX_OLD_BRAND, INDEX_NEW_BRAND,
                 "MASTER_CATEGORIES.BRAND +SHOPPING INTENT")
    src = splice(src, INDEX_OLD_TRENDS, INDEX_NEW_TRENDS,
                 "MASTER_CATEGORIES.TRENDS -SHOPPING INTENT")
    src = splice(src, INDEX_OLD_ICONS_TAIL, INDEX_NEW_ICONS_TAIL,
                 "add CATEGORY_SUB_BUCKETS after MASTER_CATEGORY_ICONS")
    src = splice(src, INDEX_OLD_ELSE_BRANCH, INDEX_NEW_ELSE_BRANCH,
                 "refactor renderDashboardProfileTree else-branch")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[index.html] wrote {len(src):,} bytes")


# --- admin.html ------------------------------------------------------------

ADMIN_OLD_QS_BRAND = "'BRAND': ['ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'AUTOMOBILE', 'B2B', 'BANKS', 'BEAUTY', 'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EVENTS', 'FOOTWEAR', 'FRANCHISE', 'GROCERY', 'INTIMATES', 'JEWELRY', 'LOYALTY PROGRAMS', 'MEMBERSHIP', 'NON PROFIT/CHARITY', 'PHARMA', 'QSR', 'RETAILERS', 'SWEEPSTAKES', 'TELECOM', 'TICKETING', 'TOY', 'TRAVEL', 'VENUE', 'WORKOUT FACILITY'],"

ADMIN_NEW_QS_BRAND = "'BRAND': ['ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'AUTOMOBILE', 'B2B', 'BANKS', 'BEAUTY', 'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EVENTS', 'FOOTWEAR', 'FRANCHISE', 'GROCERY', 'INTIMATES', 'JEWELRY', 'LOYALTY PROGRAMS', 'MEMBERSHIP', 'NON PROFIT/CHARITY', 'PHARMA', 'QSR', 'RETAILERS', 'SHOPPING INTENT', 'SWEEPSTAKES', 'TELECOM', 'TICKETING', 'TOY', 'TRAVEL', 'VENUE', 'WORKOUT FACILITY'],"

# The no-trailing-comma variant is a substring of the with-comma
# variant, so a bare .count() returns 2 for the no-comma form. Anchor
# it with 3 lines including the following `};` to force uniqueness.
# The with-comma form is naturally unique because the second line
# after it differs.
ADMIN_OLD_TRENDS_A = "            'TRENDS': ['TRENDS', 'SHOPPING INTENT']\n        };"
ADMIN_NEW_TRENDS_A = "            'TRENDS': ['TRENDS']\n        };"

ADMIN_OLD_TRENDS_B = "            'TRENDS': ['TRENDS', 'SHOPPING INTENT'],"
ADMIN_NEW_TRENDS_B = "            'TRENDS': ['TRENDS'],"


def patch_admin_html():
    print(f"[admin.html] reading {ADMIN}")
    src = ADMIN.read_text(encoding="utf-8")
    backup = BACKUP_DIR / f"admin.pre_intent_bucket_{STAMP}.html"
    backup.write_text(src, encoding="utf-8")
    print(f"[admin.html] backed up to {backup} ({len(src):,} bytes)")

    # There are TWO BRAND arrays in admin.html (QS_MASTER_CATEGORIES and
    # MASTER_CATEGORIES) but they share the SAME literal text on the BRAND
    # line, so a straight str.replace with count=2 would trip our unique-
    # anchor guard. Do them one-at-a-time using neighboring context.
    old_brand_qs_context = "const QS_MASTER_CATEGORIES = {\n            'TALENT': ['ACTOR', 'ATHLETE', 'COMEDIAN', 'CREATOR/INFLUENCER', 'INFLUENCER/CREATOR', 'EMERGING TALENT', 'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST'],\n            " + ADMIN_OLD_QS_BRAND
    new_brand_qs_context = "const QS_MASTER_CATEGORIES = {\n            'TALENT': ['ACTOR', 'ATHLETE', 'COMEDIAN', 'CREATOR/INFLUENCER', 'INFLUENCER/CREATOR', 'EMERGING TALENT', 'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST'],\n            " + ADMIN_NEW_QS_BRAND
    src = splice(src, old_brand_qs_context, new_brand_qs_context,
                 "admin.html QS_MASTER_CATEGORIES.BRAND +SHOPPING INTENT")

    old_brand_ms_context = "const MASTER_CATEGORIES = {\n            " + ADMIN_OLD_QS_BRAND
    new_brand_ms_context = "const MASTER_CATEGORIES = {\n            " + ADMIN_NEW_QS_BRAND
    src = splice(src, old_brand_ms_context, new_brand_ms_context,
                 "admin.html MASTER_CATEGORIES.BRAND +SHOPPING INTENT")

    # And the two TRENDS lines. The first is trailing-comma-less (line 6718),
    # the second has a trailing comma (line 11746) so their string forms
    # already differ and each is unique.
    src = splice(src, ADMIN_OLD_TRENDS_A, ADMIN_NEW_TRENDS_A,
                 "admin.html QS_MASTER_CATEGORIES.TRENDS -SHOPPING INTENT")
    src = splice(src, ADMIN_OLD_TRENDS_B, ADMIN_NEW_TRENDS_B,
                 "admin.html MASTER_CATEGORIES.TRENDS -SHOPPING INTENT")

    ADMIN.write_text(src, encoding="utf-8")
    print(f"[admin.html] wrote {len(src):,} bytes")


# --- iq_rankers.py ---------------------------------------------------------

IQ_OLD_BRAND_TAIL = '''        "B2B", "BANK", "BANKS", "BANKING",
        "BEAUTY", "BETTING", "BEVERAGE", "CASUAL DINING", "CPG",
        "CREDIT PROVIDERS", "CREDIT PROVIDER", "DIGITAL BANKING", "EVENTS",
        "FESTIVAL",
        "FOOTWEAR", "FRANCHISE", "GROCERY", "INTIMATES", "JEWELRY",
        "LOYALTY PROGRAMS",
        "MEMBERSHIP",
        "NON PROFIT/CHARITY", "PHARMA", "QSR", "RETAILERS", "SECURITY",
        "SWEEPSTAKES",
        "TELECOM", "TICKETING", "TOY", "TRAVEL", "VENUE", "WHERE THEY SHOP",
        "WORKOUT FACILITY",
    ],'''

IQ_NEW_BRAND_TAIL = '''        "B2B", "BANK", "BANKS", "BANKING",
        "BEAUTY", "BETTING", "BEVERAGE", "CASUAL DINING", "CPG",
        "CREDIT PROVIDERS", "CREDIT PROVIDER", "DIGITAL BANKING", "EVENTS",
        "FESTIVAL",
        "FOOTWEAR", "FRANCHISE", "GROCERY", "INTIMATES", "JEWELRY",
        "LOYALTY PROGRAMS",
        "MEMBERSHIP",
        "NON PROFIT/CHARITY", "PHARMA", "QSR", "RETAILERS", "SECURITY",
        "SHOPPING INTENT",
        "SWEEPSTAKES",
        "TELECOM", "TICKETING", "TOY", "TRAVEL", "VENUE", "WHERE THEY SHOP",
        "WORKOUT FACILITY",
    ],'''

IQ_OLD_TRENDS = '    "TRENDS": ["TRENDS", "SHOPPING INTENT"],'
IQ_NEW_TRENDS = '    "TRENDS": ["TRENDS"],'


def patch_iq_rankers():
    print(f"[iq_rankers.py] reading {IQ_RANKERS}")
    src = IQ_RANKERS.read_text(encoding="utf-8")
    backup = BACKUP_DIR / f"iq_rankers.pre_intent_bucket_{STAMP}.py"
    backup.write_text(src, encoding="utf-8")
    print(f"[iq_rankers.py] backed up to {backup} ({len(src):,} bytes)")

    src = splice(src, IQ_OLD_BRAND_TAIL, IQ_NEW_BRAND_TAIL,
                 "iq_rankers MASTER_CATEGORIES BRAND +SHOPPING INTENT")
    src = splice(src, IQ_OLD_TRENDS, IQ_NEW_TRENDS,
                 "iq_rankers MASTER_CATEGORIES TRENDS -SHOPPING INTENT")

    IQ_RANKERS.write_text(src, encoding="utf-8")
    print(f"[iq_rankers.py] wrote {len(src):,} bytes")


# --- image_backfill.py -----------------------------------------------------

IB_OLD_TRENDS = '    "TRENDS": {"TRENDS", "SHOPPING INTENT"},'
IB_NEW_TRENDS = '    "TRENDS": {"TRENDS"},'


def patch_image_backfill():
    print(f"[image_backfill.py] reading {IMAGE_BACKFILL}")
    src = IMAGE_BACKFILL.read_text(encoding="utf-8")
    backup = BACKUP_DIR / f"image_backfill.pre_intent_bucket_{STAMP}.py"
    backup.write_text(src, encoding="utf-8")
    print(f"[image_backfill.py] backed up to {backup} ({len(src):,} bytes)")

    # image_backfill.MASTER_CATEGORIES has NO explicit BRAND set; the
    # master_category() function defaults unmatched values to "BRAND"
    # (see the last `return "BRAND"` line). So removing SHOPPING INTENT
    # from TRENDS is enough: master_category("SHOPPING INTENT") will
    # then fall through to the BRAND default. No BRAND-side edit needed.
    src = splice(src, IB_OLD_TRENDS, IB_NEW_TRENDS,
                 "image_backfill MASTER_CATEGORIES TRENDS -SHOPPING INTENT")

    IMAGE_BACKFILL.write_text(src, encoding="utf-8")
    print(f"[image_backfill.py] wrote {len(src):,} bytes")


if __name__ == "__main__":
    patch_index_html()
    patch_admin_html()
    patch_iq_rankers()
    patch_image_backfill()
    print("\nAll patches applied. Now run:")
    print("  python3 bg-webapp/scripts/validate_index_html.py")
