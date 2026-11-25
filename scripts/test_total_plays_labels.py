#!/usr/bin/env python3
"""The short-form conversion screen and the Micro-Series screen call their
platform video counts plays, never views.

Jenna 2026-11-24 (rename_plays): "Yes, rename them 'Total plays' to match
our definition." A view is one person, counted once; a play is one start
(prometheus/methodology.py). Both screens show platform video counts,
which count starts, and labeled them "Total Views".

Checks:
- templates/index.html, inside each screen (its markup and its script): no
  "Total Views" and none of the other labels this change renamed; "Total
  plays" is there in Jenna's casing. The script still reads the stored
  report's own row names and the payload keys, the person counts keep their
  labels, and the short-form tile names viewers when an older report has
  no plays row.
- The Micro-Series CSV export headers say plays.
- play_count_labels: the short-form report download renames whole plays
  cells only and leaves every other byte alone; any other file comes back
  as it was.
- Prometheus reads both screens with a note that their views fields are
  plays (prometheus_analysis.render_view_context_block); other screens
  carry no note.
- app.py relabels the short-form download before it stamps it.

Hermetic: no app boot, no network, no store.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
WEBAPP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP))

import play_count_labels as plays  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name} {detail}')


# Each screen: its markup, then its script (start anchor, end anchor).
SCREENS = {
    'short-form conversion': (
        ('<div id="sfConversionView"', '<!-- IMPACT IQ VIEW -->'),
        ('async function loadSFConvProfiles()',
         '// 2026-07-27: canonical top-level product view list.'),
    ),
    'Micro-Series': (
        ('<div id="microdramasIQView">', '<!-- ATTRIBUTION IQ VIEW'),
        ('// ===================== MICRODRAMAS IQ VIEW =====================',
         'async function loadTrendsIQFilterOptions()'),
    ),
}

# The labels this change renamed. None may come back on either screen.
RETIRED = (
    re.compile(r'(?i)total\s+views'),
    re.compile(r'Short Form Views \(Duplicated\)'),
    re.compile(r"label:\s*'Views'"),
    re.compile(r"'(?:Window|28-day|Top Title) Views'"),
    re.compile(r'(?i)daily views'),
    re.compile(r'(?i)ranked by views'),
    re.compile(r'(?i)views by platform'),
    re.compile(r"' views (?:across|\()"),
    re.compile(r'>Views</th>'),
    re.compile(r'Same window as Views'),
    re.compile(r'public views'),
    re.compile(r'read down: views'),
)


def region(html, start, end):
    i = html.find(start)
    if i < 0:
        return None
    j = html.find(end, i)
    return html[i:j] if j > i else None


def shown(src):
    """What a reader can see: no comments, and none of the script's
    comparisons against the stored report's own row names."""
    src = re.sub(r'<!--.*?-->', '', src, flags=re.S)
    src = re.sub(r'(?m)^\s*//.*$', '', src)
    src = re.sub(r'(?m)(?<=\s)//\s.*$', '', src)
    return re.sub(r"(?:metric|val)\s*===\s*'[^']*'", '', src)


def header_rows(fn_src):
    """Every rows.push([...]) in the export whose cells are all literals."""
    out = []
    for m in re.finditer(r"rows\.push\(\[((?:\s*'[^']*'\s*,?)+)\]\);", fn_src):
        out.append(re.findall(r"'([^']*)'", m.group(1)))
    return out


def main():
    html = (WEBAPP / 'templates' / 'index.html').read_text(encoding='utf-8')
    text = {}
    for screen, parts in SCREENS.items():
        pieces = [region(html, a, b) for a, b in parts]
        check(f'{screen}: both parts of the screen found', all(pieces))
        text[screen] = ''.join(p or '' for p in pieces)

    print('neither screen shows a retired label')
    for screen, src in text.items():
        seen = shown(src)
        for rx in RETIRED:
            m = rx.search(seen)
            check(f'{screen}: no {rx.pattern!r}', m is None,
                  repr(seen[max(0, m.start() - 80):m.end() + 20]) if m else '')
        check(f'{screen}: shows "Total plays"', 'Total plays' in seen)
        check(f'{screen}: "Total plays" in Jenna\'s casing', 'Total Plays' not in seen)

    sf = text['short-form conversion']
    miq = text['Micro-Series']

    print('short-form conversion labels')
    for label in (
        'id="sfConvKpiTotalViewersLabel"',
        '>Short form plays</div>',
        '<option value="total_desc">Total plays (high \u2192 low)</option>',
        'background: var(--bg-dark);">Total plays</th>',
        'border-bottom: 1px solid var(--border-color);">Total plays</th>',
        'Total plays:</span>',
        'public play counts',
    ):
        check(f'short-form: {label!r}', label in sf)
    check('short-form: the tile names viewers when an older report has no plays row',
          "(totalShortFormViewsGenPop > 0 || totalShortFormViewsSample > 0) ? 'Short form plays' : 'Short form viewers'" in sf)

    print('Micro-Series labels')
    chips = miq.count("label: 'Plays'")
    check('Micro-Series: every card chip says Plays (default, Peacock, competitor, All Platforms)',
          chips == 4, f'found {chips}')
    for label in (
        'Every title ranked by plays in the window you pick',
        'read down: plays, episodes',
        'aria-label="Show daily plays"',
        'Click to open the daily plays breakdown',
        "'Daily plays (last 7 days):",
        "'Daily plays',",
        'Same window as Plays.',
        "'<div class=\"miq-rollup-card-views-label\">Total plays</div>'",
        "'<span>Plays by platform &middot; '",
        "' total plays</span>'",
        "' plays (' + (p.share_pct || 0)",
        'ranked by plays \\u00B7',
        "' plays across '",
        "'<th style=\"text-align:right;\">Plays</th>'",
    ):
        check(f'Micro-Series: {label!r}', label in miq)

    print('Micro-Series CSV export headers')
    fn = region(html, 'function _miqExportRowsForPanel(panel, payload) {',
                'function renderMicrodramasIQPlatformRollup(d) {') or ''
    heads = [row for row in header_rows(fn) if len(row) > 1]
    flat = [c for row in heads for c in row]
    check('export: four header rows found', len(heads) == 4, f'found {len(heads)}')
    check('export: no header says views', not any(re.search(r'(?i)\bviews\b', c) for c in flat),
          repr([c for c in flat if re.search(r'(?i)\bviews\b', c)]))
    for want in ('Total plays', 'Top title plays', 'Window plays', '28-day plays'):
        check(f'export: header {want!r}', want in flat)
    check('export: the All Platforms rows carry Total plays twice', flat.count('Total plays') == 2)
    check('export: Unique Viewers stays a viewers column', 'Unique Viewers' in flat)

    print('what the screens read and the person counts are unchanged')
    for wire in (
        "metric === 'Total Views (Events)'",
        "val === 'Total Views'",
        "val === 'Total SF Views (Duplicated)'",
        "metric === 'Unique Views' || metric === 'Unique Viewers (UIDs)'",
        'id="sfConvKpiTotalViewers"',
    ):
        check(f'short-form still reads {wire!r}', wire in sf)
    for wire in ('p.total_views', 'd.grand_total_views', 'p.top_title_views',
                 't.view_window_estimate', 't.view_28d_estimate', 'miq-rollup-card-views-label'):
        check(f'Micro-Series still reads {wire!r}', wire in miq)
    for person in ('Unique Viewers (UIDs)</th>', '>Unique Views</th>', 'Converted Viewers (UIDs)</th>',
                   'Unique Viewers:</span>', 'Short Form<br>Views'):
        check(f'short-form person count keeps {person!r}', person in sf)
    for person in ('<span class="miq-comp-chip-label">Unique Viewers</span>',
                   '<div class="miq-rollup-flow-label">Unique Viewers</div>'):
        check(f'Micro-Series person count keeps {person!r}', person in miq)

    print('the short-form report download')
    check('the label is Jenna\'s', plays.TOTAL_PLAYS == 'Total plays')
    stored = (
        'Column,Value,Metric,Count,Percentage,Gen_Pop_Projection,Data_Source,Creator\n'
        'PROJECT_INFO,Demo,,,,,,\n'
        'PLATFORM,YouTube,Unique Viewers (UIDs),12,,3961,PANEL,\n'
        'PLATFORM,YouTube,Total Views (Events),31,,10231,PANEL,\n'
        'INPUT_URL,"https://www.youtube.com/shorts/abc?x=1,2",Total Views (Events),7,,2311,PANEL,"Luke, Ross"\n'
        'OVERALL_CONVERSION,Total SF Views (Duplicated),,31,,10231,,\n'
        'YOUTUBE_CONVERSION,Total Views,,31,,10231,,\n'
        'URL,https://www.tiktok.com/@a/video/1,Duplicated Views,5,,1653,PANEL,\n'
        'ANALYSIS_SUMMARY,"He said ""Total Views"" twice, then left",,,,,,\n'
    )
    out = plays.relabel_sf_report_csv(stored)
    a, b = stored.split('\n'), out.split('\n')
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    check('download: the same number of lines', len(a) == len(b))
    check('download: only the five plays rows change', changed == [3, 4, 5, 6, 7], f'changed {changed}')
    check('download: each plays row reads Total plays', all('Total plays' in b[i] for i in changed))
    check('download: no stored plays name is left', not re.search(r'Total Views \(Events\)|Total SF Views|,Total Views,|Duplicated Views', out))
    check('download: a quoted URL and creator keep their bytes',
          b[4] == 'INPUT_URL,"https://www.youtube.com/shorts/abc?x=1,2",Total plays,7,,2311,PANEL,"Luke, Ross"', repr(b[4]))
    check('download: free text that mentions the old name is left alone', b[8] == a[8])
    other = 'a,b\n1,Total Views\n'
    check('download: a file without Metric or Value columns comes back as it was', plays.relabel_sf_report_csv(other) is other)
    check('download: bytes are relabeled the same way', plays.relabel_sf_report_bytes(stored.encode('utf-8')) == out.encode('utf-8'))
    junk = b'\xff\xfe\x00bad'
    check('download: bytes that are not text come back as they were', plays.relabel_sf_report_bytes(junk) is junk)

    print('app.py relabels the download before it stamps it')
    app_src = (WEBAPP / 'app.py').read_text(encoding='utf-8')
    i = app_src.find('def download_sf_lf_conversion(')
    j = app_src.find('\n@app.route', i)
    body = app_src[i:j] if i >= 0 and j > i else ''
    k = body.find('relabel_sf_report_bytes(')
    check('app.py: the download relabels then stamps', 0 <= k < body.find('_stamp_csv_bytes('), f'relabel at {k}')

    print('Prometheus reads both screens as plays')
    for vid in ('microdramasIQ', 'sfConversion'):
        note = plays.screen_count_note(vid)
        check(f'{vid}: a count-name note', bool(note) and 'plays' in note and 'Total plays' in note)
        check(f'{vid}: the note keeps viewers as people', 'people' in note)
    check('other screens carry no note', plays.screen_count_note('subscriberIQ') == '' and plays.screen_count_note(None) == '')
    import prometheus_analysis as pma
    for vid, data in (('microdramasIQ', {'platform_totals': [{'platform': 'ReelShort', 'total_views': 41237}]}),
                      ('sfConversion', {'platforms': [{'platform': 'YouTube', 'duplicatedViews': 31, 'uniqueViews': 12}]})):
        block = pma.render_view_context_block({'view_id': vid, 'view_title': vid, 'data': data})
        note = plays.screen_count_note(vid)
        check(f'{vid}: the screen block carries the note before the data',
              note in block and block.find(note) < block.find('{'), block[:200])
    plain = pma.render_view_context_block({'view_id': 'subscriberIQ', 'view_title': 'Subscriber IQ', 'data': {'show': 'X'}})
    check('subscriberIQ: no count-name note', 'COUNT NAMES ON THIS SCREEN' not in plain and 'DATA CURRENTLY ON SCREEN' in plain)

    print('no dash characters in the new copy')
    mod_src = (WEBAPP / 'play_count_labels.py').read_text(encoding='utf-8')
    check('play_count_labels.py: no em or en dash', '\u2014' not in mod_src and '\u2013' not in mod_src)

    print(f'\n{PASS} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
