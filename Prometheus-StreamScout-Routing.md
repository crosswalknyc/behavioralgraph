# Prometheus × StreamScout — Profile Source Routing

**Purpose:** decide *where a profile's seed URLs come from* so we never repeat
the "Baby, This Is Keke Palmer" miss (that profile was built from a 10-row
brand seed in the **host_map** when 1,420 episode-level URLs were sitting in the
**content_map**). This document is the decision tree Prometheus follows, plus
the two standing notifications.

> **The one rule:** if the profile is about **who consumed a property**
> (viewers / readers / players / listeners), seed it from **content_map**
> (`reference.content_mapping`, the `URL` column) — *not* the host_map. The
> host_map stays the source only for **entity** profiles (a person, a brand).

> **Every fork is a QUESTION PUT TO THE USER — never intuited.** Prometheus
> *asks* at each node (Entity or audience? Single title or franchise? What is
> the franchise name? What are the titles?) and waits for the answer. It does
> **not** guess the subject kind, or which titles belong to a franchise, on the
> user's behalf. The tree is an interview, not an inference.

> **No host_map safety net on the audience path.** If StreamScout can't confidently
> resolve an audience property, Prometheus does **not** silently fall back to the
> host_map — it **holds** and emails the team (Notification A below).

---

## Decision tree

*(Each fork below is an explicit prompt Prometheus shows the user; it advances
only on the user's answer.)*

```
                              RUN A PROFILE
                                    │
         Is this profile for an ENTITY (person, brand, etc.)
         OR for VIEWERS / READERS / PLAYERS / LISTENERS, etc.?
                    │                             │
                 ENTITY                  VIEWERS/READERS/PLAYERS/LISTENERS
                    │                             │
        Proceed exactly as            Is this a SINGLE TITLE or a FRANCHISE?
        Prometheus does today:              │                    │
        build the profile from       SINGLE TITLE           FRANCHISE
        the HOST_MAP.                       │              (enter: 1. franchise
                                            │               name  2. ALL titles
                                            │               within the franchise)
                                            └──────────┬─────────┘
                                                       ▼
                        Prometheus searches the `SHOW` column of
                        reference.content_mapping (ClickHouse)
                                       │
                        ┌──────────────┴───────────────┐
                   MATCH FOUND                    NO MATCH
                        │                              │
             Build the profile from          1. Query StreamScout for the
             the `URL` column of the             title / franchise
             matching content_mapping         2. INSERT the returned rows
             rows.                                into content_mapping
                                              3. Build the profile from the
                                                 `URL` column (of the rows
                                                 just inserted)
```

---

## Step-by-step logic

> Steps 1–2 are **questions asked of the user**. Prometheus does not infer the
> answers.

1. **ASK THE USER — "Is this profile for an entity, or for an audience?"**
   - **Entity** (a person, a brand, a company) → **no change**. Build the
     profile the way Prometheus does today, from the **host_map**. *(End of the
     content_map path — everything below is audience-only.)*
   - **Audience** (viewers, readers, players, listeners of a property) →
     continue. **Remember which audience they picked** — it selects the
     StreamScout **platform group** in step 5 (see the mapping there).

2. **ASK THE USER — "Is this a single title or a franchise?"**
   - **Single title** → **ask the user to enter the one title.** The `SHOW`
     value is that title.
   - **Franchise** → **ask the user to enter (1) the franchise name and
     (2) every title within the franchise.** The **franchise name becomes the
     `SHOW` value for every row**; the **entered titles are the search terms**
     Prometheus feeds StreamScout to populate the content_map (see step 5).

3. **Look in the content_map first.** Search the **`SHOW`** column of
   `reference.content_mapping` for the **`SHOW` value from step 2** (the single
   title, or the franchise name). Normalized match — case- and
   punctuation-insensitive, the same natural-key normalization the ingest uses.

4. **If a match is found** → build the profile from the **`URL`** column of the
   matching rows. Done.

5. **If no match is found:**
   1. **Query StreamScout — scoped to the audience's platform group.** For a
      **single title**, search that title; for a **franchise**, run **one
      StreamScout search per entered title** and pool the results. **Always pass
      `platforms=`** — the group that matches the audience from step 1. **Never
      call it bare** (it defaults to `all`, which is what pulled a *Listeners*
      ask across video/books/games and dumped 2,142 junk rows):

      | Audience (step 1) | `platforms=` |
      |---|---|
      | **Viewers** | `"video"` |
      | **Readers** | `"books"` |
      | **Players** | `"games"` |
      | **Listeners** | `["podcasts", "audio"]` |

      StreamScout returns the standard rows
      (`SHOW · URL · PRODUCTION · PLATFORM · SEASON`).
   2. **Stamp the `SHOW` column** = the step-2 `SHOW` value on **every** pooled
      row (so a whole franchise lands under the **one** franchise `SHOW`,
      regardless of which entered title fetched the row).
   3. **Insert** those rows into `reference.content_mapping` (insert-only,
      idempotent — see *Data & safety* below), and **email the added-lines CSV**
      to the team (Notification B).
   4. **Build the profile** from the **`URL`** column of the rows just inserted.
   5. If StreamScout can't find it or isn't confident → **do not** fall back to
      the host_map. **Hold** the request and fire **Notification A**.

---

## Data & safety (already true today)

- **Table:** `reference.content_mapping`.
- **Columns:** `SHOW, URL, PRODUCTION, PLATFORM, SEASON, CATEGORY,
  SUB_CATEGORY` — StreamScout emits the first five verbatim; `CATEGORY /
  SUB_CATEGORY` are set by the ingest.
- **Seed column:** `URL` is the profile seed (the episode/edition/store
  identifier — a podcast episode link, a Books buy/listen/borrow term, a game
  store term, etc.).
- **Match column:** `SHOW`. **One value per franchise** — a franchise's rows are
  all stamped with the **franchise name** on insert, regardless of which entered
  title fetched them, so lookup + profile both key off that single `SHOW`.
  Normalized on lookup.
- **Insert-only & idempotent:** dedupe is on the natural key,
  case/punctuation-insensitive — re-running a title never double-writes.
- **Fail-open:** if ClickHouse is unreachable, rows **queue to S3** and retry;
  the build continues rather than blocking. (`migration/viewer_content_scope.py`
  already implements exactly this insert path — reuse it.)
- **Server-side relevance floor (live in StreamScout):** the resolvers now reject
  weak/incidental matches instead of enumerating an unrelated show's whole
  catalog (the fix for *"Lady" → the entire "The Lady Vanishes" podcast*). So
  **an empty result from a platform is expected and correct** when the title
  isn't there — that's the Notification A / "specialized" path, not a bug to
  retry. `low_confidence()` becomes a *second* backstop, not the only one.

---

## Two standing notifications (from the sketch)

**A. Low-confidence / can't-find → tell the user, email the team.**
If at **any** point Prometheus is having trouble finding a property via
StreamScout, or is **not confident** in the result:

- **Print to the user in Prometheus:**
  > "Your request is specialized, we are working on it!"
- **Email** `Jenna@crosswalknyc.com` **and** `Jessie@crosswalknyc.com` that
  Prometheus needs help, **listing the problematic property** in the email.

**B. Any content_map write → email the team the diff.**
**Any time** Prometheus uses StreamScout and **adds lines to
content_mapping**, it emails `Jenna@crosswalknyc.com` **and**
`Jessie@crosswalknyc.com` with the **CSV of the added lines**.

---

## Reference pseudocode

```python
# Audience (step 1)  ->  StreamScout platform group (step 5). NEVER "all".
AUDIENCE_PLATFORMS = {
    "viewers":   "video",
    "readers":   "books",
    "players":   "games",
    "listeners": ["podcasts", "audio"],
}


def run_a_profile():
    # ── Step 1 — ASK the user (never inferred) ────────────────────────────────
    subject = ask_user("Is this profile for an ENTITY (person, brand, etc.) "
                       "or for an AUDIENCE (viewers/readers/players/listeners)?")
    if subject == "entity":
        return build_profile_from_hostmap()          # unchanged, current path
    audience = subject                                # remember: viewers/readers/…

    # ── Step 2 — ASK the user ─────────────────────────────────────────────────
    kind = ask_user("Is this a SINGLE TITLE or a FRANCHISE?")
    if kind == "franchise":
        show   = ask_user("Enter the FRANCHISE NAME:")          # -> SHOW for all
        titles = ask_user("Enter ALL TITLES within the franchise:")  # = searches
    else:
        show   = ask_user("Enter the TITLE:")
        titles = [show]

    # ── Step 3 — content_map FIRST ────────────────────────────────────────────
    rows = content_mapping_lookup(show=show)         # normalized SHOW match
    if rows:                                         # already seeded -> use it,
        return build_profile(seed_urls=[r["URL"] for r in rows])   # no live call

    # ── Step 5 — only if content_map truly has nothing ────────────────────────
    plats = AUDIENCE_PLATFORMS[audience]             # SCOPED — never "all"
    try:
        fetched = []
        for t in titles:                             # ONE search per entered
            fetched += streamscout_query(title=t,    # title; pool them
                                         platforms=plats, show=show)
        if not fetched or low_confidence(fetched):
            raise NotConfident(show)
        added = content_mapping_insert(fetched)      # insert-only, idempotent
        if added:
            email_team_added_csv(added)              # Notification B → Jenna+Jessie
        rows = fetched
    except (NotFound, NotConfident) as e:
        notify_user("Your request is specialized, we are working on it!")
        email_team_needs_help(property=str(e))       # Notification A → Jenna+Jessie
        return SPECIALIZED_HOLD                       # NO host_map fallback

    # ── Step 4 — build the profile from the URL column ────────────────────────
    return build_profile(seed_urls=[r["URL"] for r in rows])
```

> `show=` stamps the franchise `SHOW` on every returned row inside
> `streamscout_query`, so a whole franchise lands under the one `SHOW` in a
> single call — no separate stamping loop needed.

---

## Open build items for Jenna (the only new plumbing)

1. ✅ **`streamscout_query(title, platforms=…, show=…)` — DONE.** Provided at
   `streamscout/streamscout_query.py`. Non-interactive entry point that calls the
   same resolvers the menu uses and returns the standard
   `SHOW · URL · PRODUCTION · PLATFORM · SEASON` rows. `platforms` takes a key, a
   list, or a group (`video|podcasts|audio|books|games|all`); pass `show=` to
   stamp the franchise name on every row. The caller loops it over the
   franchise's title list. **You must pass the audience-scoped `platforms=`
   (see the `AUDIENCE_PLATFORMS` map / step 5) — never let it default to `all`.**
2. **`content_mapping_lookup(show)`** — normalized `SHOW` match against
   `reference.content_mapping` (reuse the ingest's `norm_token`).
3. **`content_mapping_insert(rows)` + `email_team_added_csv`** — reuse the
   existing insert-only/S3-queue path in `migration/viewer_content_scope.py`;
   add the "added-lines CSV" email hook (Notification B).
4. **`low_confidence()` + notifications A** — Prometheus already has a
   low-confidence concept; wire the user print + team email here.

---

## Confirmed decisions (signed off)

1. **Entity path is untouched** — entity profiles keep using the host_map as-is.
2. **Audience path is content_map-first, host_map NEVER** — no host_map safety
   net. If StreamScout can't find it / isn't confident, Prometheus **holds** and
   emails Jenna + Jessie (Notification A).
3. **Franchise `SHOW`** — the **franchise name is printed in `SHOW` for every
   row**; the **entered titles are used only as the StreamScout searches** that
   populate the content_map (one search per title, results pooled and stamped
   with the franchise `SHOW`).
4. **Emails** go to `Jenna@crosswalknyc.com` **and** `Jessie@crosswalknyc.com`
   on **both** triggers.
5. **Every fork is asked of the user, not intuited** — the tree is an interview:
   Prometheus prompts for entity-vs-audience, single-vs-franchise, the franchise
   name, and the title list, and only advances on the user's answers.
