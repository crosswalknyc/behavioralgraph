# gametool_content — StreamScout's game resolver (content-map edition)

This is the **content-map twin** of the top-level `gametool_hostmap/` tool. Give
it a game title and it returns the product / purchase / download URL on every
store it can reach, then writes ONE **StreamScout-schema** CSV to your Desktop:

```
SHOW · URL · PRODUCTION · PLATFORM · SEASON
```

> **hostmap vs content — the only difference is the print.** Both tools use the
> exact same search mechanisms. `gametool_hostmap` feeds the **hostmap**, so its
> terms are **punctuation-stripped** (non-alphanumerics → spaces). This one
> feeds the **content map**, which **keeps the real URL punctuation** (slug
> hyphens / underscores / dots):
>
> | | hostmap | content (this tool) |
> |---|---|---|
> | Xbox | `marvel vs capcom fighting collection arcade classics/9nwfm3hdjc94` | `marvel-vs-capcom-fighting-collection-arcade-classics/9nwfm3hdjc94` |
> | Epic | `p/fall guys` | `p/fall-guys` |
> | Nintendo | `products/stardew valley switch` | `products/stardew-valley-switch` |
> | Google Play | `com blizzard diablo immortal` | `com.blizzard.diablo.immortal` |
> | Target | `A 1001236988` | `A-1001236988` |
>
> This aligns with StreamScout Books, which also feeds the content map with
> punctuation-preserving slugs (`p/merciless-saints`).

## Columns

- **SHOW** (column A) = the **searched title, identical on every row** — the
  franchise all the terms roll up to.
- **URL** = the normalized content-map term (operative path segment(s), real
  punctuation kept, one `/` max, region/tracking dropped, id- or slug-anchored).
- **PRODUCTION** = the store-reported publisher/studio when available (or the
  `--production` override).
- **PLATFORM** = the store (Steam, Xbox, Epic, …).
- **SEASON** = unused for games (blank).

## Run it

```bash
python3 streamscout/gametool_content/gametool_content.py                 # interactive
python3 streamscout/gametool_content/gametool_content.py --title "Hades"  # one-shot, all stores
python3 streamscout/gametool_content/gametool_content.py --title "Diablo IV" --stores steam,gog,battlenet
python3 streamscout/gametool_content/gametool_content.py --url "https://store.steampowered.com/app/1145360/"
```

It drops a `lookup_games_<title>_<stamp>.csv` on your Desktop.

## The stores

Live anonymous search (no login) on **every query**: Steam · GOG · Apple App
Store · Google Play · Nintendo eShop · PlayStation · Xbox · Epic, plus the
Battle.net catalog. The bot-walled marketplaces/retailers (Amazon Luna · Green
Man Gaming · Eneba · Loaded · G2A · Amazon · Best Buy · GameStop · Walmart ·
Target) parse a pasted product URL into a clean id — paste one when prompted, or
pass `--url`.

## Files

`gametool_content/` is self-contained (copied from `gametool_hostmap/`, so the
two can evolve independently):

- `gametool_content.py` — the runner (search-all, aggregate, StreamScout CSV)
- `stores.py` — per-store search + URL parsing + the **content-map** term builders
- `common.py` — HTTP + fuzzy-title-matching helpers
- `playstation_store.py` · `xbox_store.py` · `epic_store.py` — headless
  first-party search resolvers
