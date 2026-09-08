#!/usr/bin/env python3
"""Total Universe Composition: force 100% Media / 0% everything else for the
Blair Witch & Adjacencies (Horror Streaming Enthusiasts) profile.

This profile qualifies purely on media consumption, so Cousin wants Media
100% and Social/Search/Owned/Retail as literal 0% (not the "-" that CONTENT
profiles show). Add a per-subject FORCE-MEDIA override in _computeAudienceShare
that returns a normal share object (archetype != CONTENT/PLATFORM, noOwned
false) so _renderAudienceShareBoxes prints 0% for the four and 100% for Media.

Matched by normalized-subject startsWith so the Total Universe and every
cohort/year cut collapse to the same treatment. Both the display name
("Blair Witch & Adjacencies (Horror Streaming Enthusiasts)") and the
underlying brand ("Horror Streaming Enthusiasts") are covered.
"""
import io

PATH = "templates/index.html"

# 1) Define the force-media set next to the existing content-override set.
OLD_SET = """        const _AUDIENCE_SHARE_CONTENT_OVERRIDE = new Set([
            'catinthehat',
            'bluecrush',
            'grinch'
        ]);
"""
NEW_SET = OLD_SET + """        // Profiles forced to 100% Media / 0% everything else - as LITERAL
        // 0%, not the "-" that CONTENT profiles render. Matched by
        // normalized-subject startsWith so the Total Universe and every
        // cohort/year cut collapse together. Both the display name and the
        // underlying brand form are listed. (2026-09-08)
        const _AUDIENCE_SHARE_FORCE_MEDIA = new Set([
            'blairwitchadjacencies',
            'horrorstreamingenthusiasts'
        ]);
"""

# 2) Short-circuit inside _computeAudienceShare, right after archetype detect.
OLD_CHK = """            let archetype = _detectAudienceShareArchetype(brandCat, profileCat);

            // 2026-06-24: per-subject opt-out of the CONTENT 100%-Media"""
NEW_CHK = """            let archetype = _detectAudienceShareArchetype(brandCat, profileCat);

            // Per-subject FORCE MEDIA=100% override (2026-09-08). Some
            // profiles qualify purely on media consumption (e.g. Blair Witch
            // & Adjacencies (Horror Streaming Enthusiasts)) and should read
            // 100% Media with the other four channels at LITERAL 0% - not the
            // "-" the CONTENT short-circuit renders. Returning a normal share
            // object (archetype left as-is, noOwned false) makes
            // _renderAudienceShareBoxes print "0%" for the four and "100%"
            // for Media. startsWith match collapses the TU + every cut.
            const _forceMediaKey = _normSubjectKeyForAudienceShare(subjectName);
            if (_forceMediaKey) {
                for (const ovr of _AUDIENCE_SHARE_FORCE_MEDIA) {
                    if (_forceMediaKey.startsWith(ovr)) {
                        return { social: 0, search: 0, retail: 0, media: 100, owned: 0,
                                 archetype: 'MEDIA_BRAND', noOwned: false, dataActive: false };
                    }
                }
            }

            // 2026-06-24: per-subject opt-out of the CONTENT 100%-Media"""


def main():
    with io.open(PATH, "r", encoding="utf-8") as fh:
        txt = fh.read()
    before = txt.count("\n")

    assert txt.count("_AUDIENCE_SHARE_FORCE_MEDIA") == 0, "already patched"
    for label, old, new in (("set", OLD_SET, NEW_SET), ("check", OLD_CHK, NEW_CHK)):
        n = txt.count(old)
        assert n == 1, "anchor not unique (%d) for %s" % (n, label)
        txt = txt.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8") as fh:
        fh.write(txt)

    after = txt.count("\n")
    assert txt.rstrip().endswith("</html>"), "missing trailing </html>"
    assert txt.count("_AUDIENCE_SHARE_FORCE_MEDIA") == 2, "expected 2 references"
    print("OK: lines %d -> %d (%+d); trailing </html> present" % (before, after, after - before))


if __name__ == "__main__":
    main()
