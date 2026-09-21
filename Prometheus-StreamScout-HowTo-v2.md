# Prometheus × StreamScout — How-To **v2 (bugfix)** for Jenna

> **This supersedes the previous Prometheus how-to.** The routing *design* was
> right; the *wiring* let three things through. This version is built around the
> real failure we just caught, with a **mandatory self-test** so we can certify
> the fix.

---

## What went wrong (the test that failed)

I ran a softball to certify content_map-first routing: **audience = "Listeners",
franchise = "Lady Miss Jacqueline Series"** — a title already seeded in
`reference.content_mapping` with **8 curated rows** (Audible + Amazon, tagged
`Audible Originals` / `Amazon Original Stories`).

**Expected:** read those 8 rows and build. Fast. Done.
**Actual:** Prometheus emitted **2,142 rows** across Apple TV, YouTube, Amazon
Podcasts, iHeart, Books retailers — and **inserted them into `content_mapping`**
(the `content_map_added_…csv` diff proves the write). Almost none of it was Lady
Miss Jacqueline: 1,464 episodes of an unrelated podcast **"The Lady Vanishes"**
(matched on the word *"Lady"*), a geopolitics podcast's back catalog, **German
crime audiobooks**, a wrong YouTube channel. Zero of the curated Audible `/pd/`
URLs; **0** of the `Audible Originals` PRODUCTION tags.

**Verdict:** content_map was **never used**, and the live path **polluted the
table**. Certification = FAIL.

---

## The three bugs (all in Prometheus wiring)

| # | Bug | Symptom | Fix |
|---|-----|---------|-----|
| 🔴 1 | **`content_mapping_lookup(show)` returned empty on an exact-name seed** — so Step 5 (live) ran when it never should have | The 8 seeded rows were ignored despite an exact `SHOW` match | Make the lookup actually find them (see **Fix 1**). This is the certification blocker. |
| 🟠 2 | **`streamscout_query()` was called with no `platforms=`** → defaulted to **`all`** | A *Listeners* ask pulled video, YouTube, Books retail, games… | Map the **audience type → platform group** and pass it every time (see **Fix 2**). |
| 🟠 3 | **No guard before insert** — a 2,142-row catalog dump got written | `content_mapping` polluted with junk | **Never insert** when empty / low-confidence / oversized (see **Fix 3**). |

*(Separately, I'm hardening the StreamScout resolvers so a one-word match can't
vacuum a whole unrelated podcast/channel. That reduces the blast radius, but
Prometheus still needs Fixes 1–3.)*

---

## ✅ The mandatory self-test (do this FIRST, before anything else)

Before you trust the router, prove the lookup works in isolation:

```python
rows = content_mapping_lookup(show="Lady Miss Jacqueline Series")
assert len(rows) == 8, f"content_map lookup broken: got {len(rows)}, expected 8"
assert all("audible.com" in r["URL"] or "amazon.com/dp" in r["URL"] for r in rows)
print("content_map lookup OK — 8 curated rows")
```

**If this doesn't return exactly 8, stop — bug #1 is live and nothing else
matters yet.** The full router must **never** reach StreamScout for this title.

---

## Fix 1 — `content_mapping_lookup(show)` (the blocker)

The seed is stored under the literal `SHOW` string **`Lady Miss Jacqueline
Series`** — the *same* string the user typed. So an exact match should hit.
Debug in this order:

1. **Is it even being called before the live path?** Add a log line:
   `log("content_map lookup", show=show, hits=len(rows))`. If you never see it,
   the router is skipping Step 3 entirely.
2. **Right table / right environment?** Confirm you're querying
   `reference.content_mapping` in the **same** ClickHouse the seed lives in (not
   a dev/stale instance).
3. **Normalization mismatch.** If you normalize with `norm_token`, make sure you
   normalize **both sides the same way** — the stored `SHOW` *and* the query.
   A one-sided normalize (e.g., lowercasing the query but comparing to a
   raw-cased column) returns empty. Verify:
   `norm_token("Lady Miss Jacqueline Series")` equals the normalized stored value.
4. **Trim/encoding.** Watch for a trailing space or a **BOM/zero-width** char in
   either the query or the stored value.

**Done when:** the self-test above returns 8.

---

## Fix 2 — audience type → platform group (REQUIRED `platforms=`)

`streamscout_query()` defaults to `platforms="all"`. **Never** call it bare for
an audience profile. Map the audience choice to the right group:

| Audience choice | `platforms=` | Why |
|---|---|---|
| **Listeners** | `["podcasts", "audio"]` | podcasts + Audible — *not* video/books/games |
| **Viewers** | `"video"` | Hulu/Netflix/Max/Disney/… |
| **Readers** | `"books"` | buy/listen/Libby |
| **Players** | `"games"` | store buy/play URLs |

```python
AUDIENCE_PLATFORMS = {
    "listeners": ["podcasts", "audio"],
    "viewers":   "video",
    "readers":   "books",
    "players":   "games",
}
plats = AUDIENCE_PLATFORMS[audience]          # chosen in the Step-1/2 prompts
fetched += streamscout_query(title=t, platforms=plats, show=show)
```

*(For "Lady Miss Jacqueline Series / Listeners" this alone would have dropped the
result from 2,142 rows spanning 6 media types to just the podcast/audio lanes.)*

---

## Fix 3 — guard the insert (never write junk)

Step 5 must refuse to insert on a bad batch:

```python
if not fetched or low_confidence(fetched):
    raise NotConfident(show)                  # → Notification A, HOLD
```

Make `low_confidence()` actually mean something. Flag the batch when **any** of:

- **Empty** — no rows.
- **Off-topic** — the searched title's distinctive tokens don't appear in the
  row titles/URLs (e.g., searching *"Lady Miss Jacqueline"* but the rows are
  *"The Lady Vanishes"* / *"Bodenstein-Kirchhoff-Krimi"*).
- **Catalog-dump shape** — one `PLATFORM` (or one podcast/channel id) dominates
  with an implausible count for a single title (a franchise of 4–5 audiobooks is
  ~a dozen URLs, **not 1,400**). A simple cap like *">80 rows from a single
  platform for one entered title → low confidence"* would have caught this.

When low-confidence: **do not insert**, show the user *"Your request is
specialized, we are working on it!"*, and email the team (Notification A). **No
host_map fallback.**

> **Server-side backstop (already live in StreamScout).** StreamScout now applies
> its own relevance floor before it will enumerate a show/podcast/channel, so a
> weak or absent match returns **no rows** instead of an unrelated catalog (this
> is the fix for the *"Lady" → the whole "The Lady Vanishes" podcast* dump). Two
> things follow for you:
> 1. Your `low_confidence()` / size guard above is now a **second** line of
>    defense, not the only one — keep it, but it should rarely need to fire.
> 2. **An empty result from a platform is expected and correct** when the title
>    genuinely isn't there. Don't treat empty as a bug or retry it — if *every*
>    platform comes back empty, that's the **Notification A / "specialized" hold**
>    path, exactly as intended.

---

## Corrected router (drop-in shape)

```python
def route(profile_request):
    # Step 1–2 — ASK (never infer)
    subject = ask_user("Entity or audience (viewers/readers/players/listeners)?")
    if subject == "entity":
        return host_map_profile(profile_request)          # unchanged

    audience = subject                                     # one of the four
    kind = ask_user("Single title or franchise?")
    if kind == "franchise":
        show   = ask_user("Enter the FRANCHISE NAME:")
        titles = ask_user("Enter ALL TITLES within the franchise:")
    else:
        show   = ask_user("Enter the TITLE:")
        titles = [show]

    # Step 3 — content_map FIRST  (Fix 1: this MUST find a seeded show)
    rows = content_mapping_lookup(show=show)
    if rows:
        return build_profile(seed_urls=[r["URL"] for r in rows])   # DONE, no live

    # Step 5 — only if content_map truly has nothing
    plats = AUDIENCE_PLATFORMS[audience]                   # Fix 2: scoped, never "all"
    fetched = []
    for t in titles:
        fetched += streamscout_query(title=t, platforms=plats, show=show)

    if not fetched or low_confidence(fetched):             # Fix 3: guard the write
        notify_user("Your request is specialized, we are working on it!")
        email_team_needs_help(property=show)               # Notification A
        return SPECIALIZED_HOLD                            # NO host_map fallback

    added = content_mapping_insert(fetched)                # insert-only, idempotent
    if added:
        email_team_added_csv(added)                        # Notification B
    return build_profile(seed_urls=[r["URL"] for r in fetched])
```

---

## Definition of done (re-certification)

1. **Self-test passes:** `content_mapping_lookup("Lady Miss Jacqueline Series")`
   → **8 rows**.
2. **Re-run the profile** (Listeners / franchise / "Lady Miss Jacqueline
   Series"). It must build **straight from the 8 seeded URLs**, make **zero**
   StreamScout calls, and **write nothing** new. Turnout = the curated
   Audible/Amazon set (incl. **Bereavement Committee**).
3. **Negative test:** a brand-new, unseeded audience title fetches **scoped**
   (only its audience group), passes the confidence guard, inserts, emails the
   diff — and an unresolvable one shows the "specialized" message with **no
   host_map fallback**.

*Questions? Ping Cousin. 🛰️*
