# Prometheus × StreamScout — How-To (for Jenna)

**Goal:** make Prometheus pull audience profiles from the **content_map first**,
so we never repeat the *"Baby, This Is Keke Palmer"* miss (that profile used a
10-row brand seed from the **host_map** when **1,420 episode URLs** were already
in the **content_map**).

> **Order of operations:** do the **StreamScout integration first** (the
> StreamScout MD you already have). *This* doc is step two — how Prometheus
> decides when to use the content_map and how it tops the content_map up from
> StreamScout. The full logic/spec is in **`Prometheus-StreamScout-Routing.md`**;
> this is the short "put it into action" version.

---

## The idea in one picture

```
Profile request
   │
   └─ ASK: entity, or audience (viewers/readers/players/listeners)?
        │                                   │
      ENTITY                             AUDIENCE
        │                                   │
   host_map, as today          ASK: single title or franchise?
                                            │
                          look up SHOW in reference.content_mapping
                              │                         │
                         FOUND                      NOT FOUND
                              │                         │
                    build profile from        query StreamScout →
                    the URL column            insert rows → build from URL
```

**One rule to remember:** *audience profiles = content_map. Entity profiles =
host_map. Never mix, and (for audience) never fall back to host_map.*

---

## What Prometheus does, in order

Every branch below is a **question Prometheus asks the user** — it never guesses.

1. **Ask:** *"Is this profile for an entity (person, brand) or an audience
   (viewers / readers / players / listeners)?"*
   - **Entity** → do exactly what Prometheus does today (host_map). **Stop.**
   - **Audience** → keep going.

2. **Ask:** *"Is this a single title or a franchise?"*
   - **Single title** → ask for the title. `SHOW` = that title.
   - **Franchise** → ask for **(1) the franchise name** and **(2) every title in
     it**. `SHOW` = the **franchise name** (for *all* rows). The individual
     titles are only used as **search terms** in step 4.

3. **Look in the content_map first.** Query the **`SHOW`** column of
   `reference.content_mapping` for that `SHOW` value.
   - **Found?** → build the profile from the **`URL`** column of those rows.
     **Done.**
   - **Not found?** → step 4.

4. **Top up from StreamScout, then build.**
   - Run StreamScout for the title (for a **franchise**, run it **once per
     entered title** and pool the results).
   - **Stamp `SHOW` = the franchise name** on every pooled row.
   - **Insert** the rows into `reference.content_mapping` (insert-only,
     idempotent — reuse the existing insert path).
   - **Email the added-lines CSV** to the team (see *Emails* below).
   - Build the profile from the **`URL`** column.

5. **If StreamScout can't find it / isn't confident** → **do not** use the
   host_map. **Hold** and email the team (see *Emails*).

---

## The two emails (copy/paste)

Both go to **`Jenna@crosswalknyc.com`** *and* **`Jessie@crosswalknyc.com`**.

**A — Stuck / not confident.** Show the user this exact line in Prometheus:

> Your request is specialized, we are working on it!

…and email the team that Prometheus needs help, **naming the property** it
couldn't resolve.

**B — Content_map was updated.** Any time Prometheus adds rows to
`content_mapping`, email the team the **CSV of the added lines**.

---

## What you're wiring (checklist)

Most of this already exists — you're mostly connecting it:

- [ ] **Branch on subject** — entity → host_map (unchanged); audience → below.
- [ ] **Two user prompts** — entity/audience, then single/franchise (+ collect
      franchise name and title list). *Asked, not inferred.*
- [ ] **`content_mapping_lookup(show)`** — normalized `SHOW` match against
      `reference.content_mapping`. Reuse the ingest's `norm_token` for the
      case/punctuation-insensitive compare.
- [ ] **`content_mapping_insert(rows)`** — reuse the insert-only / idempotent /
      S3-queue-on-outage path already in
      `migration/viewer_content_scope.py` (columns:
      `SHOW, URL, PRODUCTION, PLATFORM, SEASON, CATEGORY, SUB_CATEGORY`).
- [ ] **Franchise `SHOW` stamp** — set `SHOW` = franchise name on every row
      before insert.
- [ ] **`streamscout_query(title)`** — the one genuinely new piece: a
      non-interactive entry point to StreamScout (a "wrapper"). Each StreamScout
      resolver already exposes `resolve(title, kind, seasons)`; this just calls
      it without the menu and returns the standard rows. **Cousin can hand you
      this wrapper — just say the word.**
- [ ] **Notifications A + B** — the two emails above (Prometheus already has a
      low-confidence signal to hang A off of).

**Definition of done:** an audience profile for a title/franchise that's already
in `content_mapping` builds straight from its `URL` rows; one that isn't gets
fetched from StreamScout, inserted (team emailed the CSV), and then built — and
anything StreamScout can't resolve shows the "specialized" message and emails the
team. No audience profile ever silently uses the host_map.

---

## Where things live

- **This how-to** + the full spec (`Prometheus-StreamScout-Routing.md`) — repo
  root.
- **content_map table:** `reference.content_mapping` (ClickHouse).
- **Existing insert path to reuse:** `migration/viewer_content_scope.py`.
- **StreamScout resolvers:** `streamscout/*_identifier.py` (each has `resolve()`).

*Questions? Ping Cousin. 🛰️*
