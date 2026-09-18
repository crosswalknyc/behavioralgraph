# GameTool_hostmap — How-To

GameTool_hostmap is **StreamScout's sibling for video games**. Give it a game
title and it returns the product / purchase / download URL on every store it can
reach, then writes ONE hostmap-ready CSV to your Desktop:

```
BRAND · HOSTNAME · CATEGORY · SECTION
```

> **Why `_hostmap`?** This tool feeds the **hostmap**, so its terms are
> **punctuation-stripped** (non-alphanumerics → spaces): `p/mickey mouse big
> game`. Its sibling, **StreamScout Books**, feeds the **content map**, which
> **keeps punctuation** as URL-slug hyphens: `p/merciless-saints`. Same idea,
> different normalization — the `_hostmap` suffix is the reminder of which is
> which.

- **BRAND** (column A) = the **searched title, identical on every row** — the
  franchise all the terms roll up to.
- **HOSTNAME** = the normalized hostmap term, ready to drop into the hostmap. It
  keeps only the operative path segment(s), replaces every non-alphanumeric with
  a space, keeps **one `/` max** on the meaningful boundary, and drops locale
  prefixes / tracking suffixes. It anchors on the store's stable id where there
  is one (`app/413150`, `dp/B08F8KRRGL`, `A 1013051464`) and otherwise on
  `prefix/slug` at **franchise grain** — the base slug naturally absorbs
  sequels/editions (`p/mickey-mouse-big-game` also catches `…-big-game-2`).
  Locale is a prefix and tracking is a suffix, both outside the term, so
  region / referral / device don't affect the match; the id (or `prefix/`
  boundary) keeps passive media/social links from matching.
- **CATEGORY** / **SECTION** = constant for the whole file — default `Gaming` /
  `Games` (override with `--category` / `--section`).

### The per-store HOSTNAME recipes

| Store | HOSTNAME shape | Example |
|---|---|---|
| Steam | `app/<appid>` | `app/413150` |
| Epic Games Store | `p/<slug>` | `p/fall guys` |
| GOG | `game/<slug>` | `game/stardew valley` |
| Battle.net | `product/<slug>` | `product/diablo iv` |
| Nintendo eShop | `products/<slug>` | `products/stardew valley switch` |
| PlayStation Store | `product/<region titleId label>` | `product/UP2456 CUSA06840 00 STARDEW00000SIEA` |
| Xbox | `<slug>/<storeId>` | `stardew valley/c3d891z6tnqm` |
| Amazon Luna | `<slug>/<ASIN>` | `dead island 2 ultimate edition/B0F7CBF7BD` |
| Apple App Store | `<slug>/id<trackId>` | `stardew valley/id1406710800` |
| Google Play | `<package id>` | `com chucklefish stardewvalley` |
| Green Man Gaming | `games/<slug>` | `games/elden ring pc` |
| Eneba | `eneba com/<slug>` | `eneba com/steam stardew valley steam key global` |
| Loaded | `loaded com/<slug>` | `loaded com/stardew valley pc steam cd key` |
| G2A | `g2a com/<slug i…id>` | `g2a com/stardew valley steam key global i10000011727009` |
| Amazon | `dp/<ASIN>` | `dp/B08F8KRRGL` |
| Best Buy | `<slug>/<sku> p` | `stardew valley nintendo switch digital/6178111 p` |
| GameStop | `<slug>/<id>` | `stardew valley nintendo switch/220265` |
| Walmart | `<slug>/<itemId>` | `Stardew Valley Nintendo Switch/571396667` |
| Target | `A <TCIN>` | `A 1001236988` |

## Run it

```bash
python3 gametool_hostmap/gametool_hostmap.py                   # interactive
python3 gametool_hostmap/gametool_hostmap.py --title "Hades"   # one-shot, all stores
python3 gametool_hostmap/gametool_hostmap.py --title "Diablo IV" --stores steam,gog,battlenet
python3 gametool_hostmap/gametool_hostmap.py --url "https://store.steampowered.com/app/1145360/"
```

It asks one thing — the game title — then sweeps the stores and drops a
`gametool_hostmap_<title>_<stamp>.csv` on your Desktop.

## The stores (19)

**Live anonymous search (no login, no key):**

| Store | How |
|---|---|
| **Steam** | public `storesearch` JSON API → appid |
| **GOG** | `catalog.gog.com` v1 API → slug + publisher |
| **Apple App Store** | iTunes Search API → trackId + publisher (Games genre only) |
| **Google Play** | search HTML → package id (+ `og:title`) |
| **Nintendo eShop** | public Algolia index (`ncom_game_en_us`) → `products/<slug>` |
| **Battle.net** | built-in Blizzard catalog (fixed line-up) |
| **PlayStation Store** | headless Chromium → harvests the store's own `getSearchResults` JSON → **every** PS4 (`CUSA…`) + PS5 (`PPSA…`) edition/region SKU + demos, filtered to game classifications (costumes/passes/DLC dropped) |
| **Xbox** | headless Chromium → reads the server-rendered search grid → `<slug>/<12-char storeId>` per edition (Game Pass tiles dropped) |
| **Epic Games Store** | headless Chromium (clears the Cloudflare JS challenge) → the query-filtered `/browse?q=` grid → `p/<slug>` per edition (add-ons/DLC dropped) |

**Paste-a-URL stores** (bot-walled marketplaces / retailers — GameTool parses a
pasted product URL into a clean id, and offers a paste prompt at the end of an
interactive run):

Amazon Luna · Green Man Gaming · Eneba · Loaded · G2A ·
Amazon · Best Buy · GameStop · Walmart · Target

> A hands-off run now investigates all six first-party storefronts live
> (Steam, GOG, Nintendo, PlayStation, Xbox, Epic) plus the app stores — no URL
> pasting or store expertise required. The paste path remains as a fallback for
> the retail/key marketplaces and for any headless store that returns empty.

## Matching

Results are filtered to genuine title matches: exact/prefix titles win (so
"The Witcher 3" → *Wild Hunt*, not the REDkit modding tool), companion junk is
dropped (soundtracks, guides, trackers, map/tool apps, modkits, benchmarks), and
short 1–2 word queries require an exact/prefix hit so "Hades" won't drag in
"Zeus vs Hades". A game that isn't on a store yields **nothing** (no forced
junk) — an honest empty is correct.

## Files

`gametool_hostmap/` is self-contained:

- `gametool_hostmap.py` — the runner (search-all, aggregate, CSV, paste-URL fallback)
- `stores.py` — per-store search + URL parsing + the store registry
- `common.py` — HTTP + fuzzy-title-matching helpers
- `playstation_store.py` · `xbox_store.py` · `epic_store.py` — headless
  first-party search resolvers
