# Brand files

## logo/
- `crosswalk-logo-white.png`: the lockup in white (#F4F6F8), for Graphite Teal, Orchid and dark photography.
- `crosswalk-logo-black.png`: the lockup in near-black (#0A121D), for Off-White and the Signal Green gradient.

Both are 4736 x 600 px with transparent backgrounds, cropped tight to the artwork (aspect 7.893 : 1). At the standard deck header width of 1.330 in the height is 0.169 in. Always size from the file's own proportions. An older artwork file (2047 x 300) sat correctly at 1.330 x 0.195; putting this file into that box squishes it.

- `crosswalk-social-square.png` (1000 px) and `crosswalk-social-square-400.png`: the square avatar, black lockup stacked on the Signal gradient. For the LinkedIn company page, Gmail and other profile photos only; never on a page or deck.

- `crosswalk-slash-mark-black.png|svg` and `crosswalk-slash-mark-white.png|svg`: the three bars on their own (828 x 822, transparent, cropped tight; the same artwork as the slashes in the lockup). `crosswalk-slash-mark-signal-gradient.svg` and `-orchid-gradient.svg` are masters with a gradient across the bars. A graphic element only, never a logo, and used only now and then (most decks skip it; one page at most when used): faded into the background, filled with a gradient, or used as a wipe in motion. Rules in `references/components.md`, "The slash mark"; `scripts/make_slash_mark.py` draws it at any size, color, opacity or fade.

## shapes/
- `crosswalk-bullet-signal.png` / `.svg` and `crosswalk-bullet-olive.png` / `.svg`: the house bullet, a rounded parallelogram leaning like the logo's slashes. Signal on dark, Olive on light. Used occasionally for short lists (see `references/components.md`, "Bullets"). The SVG is traced from the supplied artwork; the PNG is the artwork itself, recolored to the exact tokens.

## fonts/
Crosswalk Inter, the nine upright faces (Thin, ExtraLight, Light, Regular, Medium, SemiBold, Bold, ExtraBold, Black) from Crosswalk-Inter-v2. The italic faces are left out on purpose: the brand uses no italics.

Crosswalk Inter is Inter (copyright 2016 The Inter Project Authors) renamed for clean enumeration in Office. It is distributed under the SIL Open Font License 1.1; see `fonts/OFL.txt`.

Use these files for:
- `scripts/cw_embed_fonts.py` (the default font folder), which embeds them in every delivered file
- `scripts/render_preview.py --fonts assets/brand-files/fonts` (the default when the folder exists)
- reference when checking a font menu. To install the family on a computer, use the full 18-face Crosswalk-Inter-v2 package (remove older Inter 18pt files first, restart, then install); the Brand System asks for every face installed.
