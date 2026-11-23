#!/usr/bin/env python3
"""Headless smoke for the Profile IQ worldwide layout (Jenna 2026-11-23).

Renders the real templates/index.html (Jinja, a super-admin seat), serves it
with its static files from this checkout, answers the profile reads from
fixtures, and drives Chromium through a global profile, a US profile and back:

  * global (the contract file, scripts/fixtures/global_profile_harbor_stout.json): the
    worldwide summary on Insights and Demographics; the country switcher
    (United States first, then by share, then Rest of world) and the picked
    country's own buckets; LOCATION grouped by country with each country's
    share, Show all, Find; COUNTRY and the per-country blocks never appear as
    behavioral cards;
  * the same file without COUNTRY rows or blocks (the shape the live
    Guinness - Global file had first): the switcher hides, the worldwide
    demographics show as written, the cities still group;
  * a US profile: none of it, the US LOCATION page as before;
  * a phone width: the sections stack inside the screen;
  * no page errors anywhere.

Usage:
    python3 scripts/smoke_global_profile_view.py [--out DIR]
    python3 scripts/smoke_global_profile_view.py --live-csv PATH --live-key KEY [--live-name NAME]
Needs Playwright with Chromium (pip install playwright; playwright install
chromium) and jinja2. Exit 0 when every check passes, 1 on a failure, 2 when
the tools are missing. Touches nothing outside its output folder.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, 'fixtures', 'global_profile_harbor_stout.json')

FAILS: list[str] = []


def check(cond, msg, detail=''):
    if cond:
        print(f'  ok   {msg}')
    else:
        FAILS.append(msg)
        print(f'  FAIL {msg}' + (f'\n       {detail}' if detail else ''))


def read_rows(path):
    with open(path, encoding='utf-8') as fh:
        if path.endswith('.json'):
            return json.load(fh)['rows']
        return [dict(r) for r in csv.DictReader(fh)]


def fallback_rows(rows):
    """The contract file minus COUNTRY and the per-country blocks."""
    return [r for r in rows if r['Column'] != 'COUNTRY' and ' | ' not in r['Column']]


def us_rows():
    head = ['Column', 'Value', 'Brand Penetration (Row)', 'Category Share', 'Original Raw Numbers',
            'US Gen Pop Projection', 'Gen Pop Penetration', 'Index vs Gen Pop']
    body = [
        ['BRAND INPUT', 'Lone Star Club', '100.0', '100.0', '894389', '29505893', '', ''],
        ['SAMPLE SIZE', 'SAMPLE SIZE (2025-01-01 TO 2025-12-31) | BEHAVIOR STUDY (2025-01-01 TO 2025-12-31)', '100.0', '100.0', '894389', '29505893', '', ''],
        ['BRAND CATEGORY', 'MLB', '', '', '', '', '', ''],
        ['AGE', '25-34', '22.1137', '22.1137', '197783', '6524861', '', ''],
        ['AGE', '35-44', '20.4419', '20.4419', '182829', '6031529', '', ''],
        ['GENDER', 'Male', '58.4417', '58.4417', '522703', '17243972', '', ''],
        ['GENDER', 'Female', '41.5583', '41.5583', '371686', '12261921', '', ''],
        ['LOCATION', 'Dallas Fort Worth, TX', '15.8661', '15.8661', '141905', '4681446', '', ''],
        ['LOCATION', 'New York, NY', '5.0993', '5.0993', '45608', '1504608', '', ''],
        ['LOCATION', 'Los Angeles, CA', '3.8041', '3.8041', '34023', '1122419', '', ''],
        ['LOCATION', 'Houston, TX', '2.7781', '2.7781', '24847', '819703', '', ''],
        ['SOCIAL MEDIA', 'Instagram', '61.2213', '61.2213', '547551', '18063709', '', ''],
        ['SOCIAL MEDIA', 'Facebook', '57.1137', '57.1137', '510820', '16851937', '', ''],
    ]
    return [dict(zip(head, r)) for r in body]


def render_page():
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, 'templates')),
                             undefined=jinja2.ChainableUndefined, autoescape=False)
    env.globals['url_for'] = lambda endpoint, filename='': '/static/' + filename
    return env.get_template('index.html').render(
        app_build_version='smoke', username='smoke', role='super_admin', credits=-1, credits_used=0,
        currency_symbol='$', seat_included=True, has_profile_iq_access=True, allowed_runs=['*'],
        allowed_behavioral_categories=['*'], allowed_categories=['*'], analysis_iq_modules=['*'],
        quick_select_behaviors_exclusions=[], rankers_iq_options=[], allowed_trends_tabs=['*'],
        allowed_rankers_tabs=['*'], allowed_digital_twins_tabs=['*'], first_name='Smoke', last_name='Test',
        company='Crosswalk', user_email='smoke@example.com', profile_picture='', company_logo='',
        view_definition='', view_note_short='', period_note='', data_floor='', data_floor_sentence='',
        has_chatbot_profile_iq_access=True).encode('utf-8')


def serve(page, profiles):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._send(200, '{"success": true}', 'application/json')

        def do_GET(self):
            p = unquote(urlparse(self.path).path)
            if p in ('/', '/index.html'):
                return self._send(200, page, 'text/html; charset=utf-8')
            if p.startswith('/static/'):
                fp = os.path.normpath(os.path.join(ROOT, p.lstrip('/')))
                if fp.startswith(os.path.join(ROOT, 'static')) and os.path.isfile(fp):
                    ctype = {'.css': 'text/css', '.js': 'application/javascript', '.json': 'application/json'}.get(
                        os.path.splitext(fp)[1], 'application/octet-stream')
                    with open(fp, 'rb') as fh:
                        return self._send(200, fh.read(), ctype)
                return self._send(404, 'missing', 'text/plain')
            if p.startswith('/api/get-csv-data/'):
                key = p[len('/api/get-csv-data/'):]
                if key in profiles:
                    name, rows = profiles[key]
                    return self._send(200, json.dumps({'success': True, 'data': rows, 'brand': name,
                                                       'date_range': '2025-11-23 to 2026-11-23'}), 'application/json')
                return self._send(404, '{"success": false}', 'application/json')
            if p.startswith('/api/'):
                return self._send(200, '{"success": true, "jobs": [], "profiles": [], "data": []}', 'application/json')
            return self._send(404, 'nope', 'text/plain')

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


STATE_JS = r'''() => {
  const id = (x) => document.getElementById(x);
  const vis = (el) => !!(el && el.getClientRects().length && getComputedStyle(el).display !== 'none' && !el.hidden);
  const d = (typeof currentDashboardData !== 'undefined' && currentDashboardData) || {};
  const sum = id('cwGlobalSummary'), demo = id('cwGlobalDemo'), loc = id('cwGlobalLocations');
  const tabLoc = id('tab-location'), tabDemo = id('tab-demographics');
  return {
    global: !!d._cwGlobal,
    summary: sum ? { visible: vis(sum), text: sum.innerText } : null,
    demo: demo ? { visible: vis(demo), text: demo.innerText,
                   opts: Array.from(demo.querySelectorAll('.cwg-opt')).map(b => b.getAttribute('data-cwg-country')),
                   on: (demo.querySelector('.cwg-opt.is-on') || {}).textContent || '',
                   cards: demo.querySelectorAll('.cwg-card').length } : null,
    demoMainVisible: vis(tabDemo && tabDemo.querySelector('.demo-main')),
    loc: loc ? { visible: vis(loc), groups: Array.from(loc.querySelectorAll('.cwg-group')).map(g => g.getAttribute('data-cwg-group')),
                 visibleGroups: Array.from(loc.querySelectorAll('.cwg-group')).filter(vis).map(g => g.getAttribute('data-cwg-group')),
                 shares: Array.from(loc.querySelectorAll('.cwg-group .share')).map(s => s.textContent),
                 visibleCities: Array.from(loc.querySelectorAll('.cwg-city')).filter(vis).length,
                 cities: loc.querySelectorAll('.cwg-city').length,
                 overflow: loc.scrollWidth - loc.clientWidth } : null,
    tabLocClass: tabLoc ? tabLoc.className : '',
    flatListVisible: vis(id('allLocationsContainer')),
    flatRows: document.querySelectorAll('#locationListAll .location-row').length,
    foreignMode: !!(tabLoc && tabLoc.classList.contains('foreign-mode')),
    usLocations: (d.locations || []).map(l => l.name),
    geoBehavioral: Object.keys(d.behavioral || {}).filter(k => k === 'COUNTRY' || k.indexOf(' | ') > -1),
    geoCards: Array.from(document.querySelectorAll('#tab-behavioral .behavioral-card[data-category]')).map(c => c.getAttribute('data-category')).filter(k => /^COUNTRY$| [|] /.test(k)),
    viewWidth: document.documentElement.clientWidth
  };
}'''


def tab(pg, name):
    pg.evaluate(f"() => {{ try {{ showTab('{name}'); }} catch (e) {{}} }}")
    pg.wait_for_timeout(700)


def state(pg):
    return pg.evaluate(STATE_JS)


def open_profile(pg, key):
    pg.evaluate("async (k) => { try { if (typeof showDashboard === 'function') showDashboard(); } catch (e) {} await selectDashboardProfile(k, true); }", key)
    pg.wait_for_timeout(1800)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default='/tmp/smoke_global_profile_view')
    ap.add_argument('--live-csv', help='also open a real worldwide file (read only, from disk)')
    ap.add_argument('--live-key', default='Live_Global.csv')
    ap.add_argument('--live-name', default='Live - Global')
    args = ap.parse_args()
    try:
        import jinja2  # noqa: F401
        from playwright.sync_api import sync_playwright
    except Exception as e:  # pragma: no cover
        print(f'SKIP: needs jinja2 and playwright ({e})')
        return 2
    os.makedirs(args.out, exist_ok=True)
    global_rows = read_rows(FIXTURE)
    profiles = {
        'Harbor_Stout_Global_11_23_2026_21_00.csv': ('Harbor Stout - Global', global_rows),
        'Harbor_Stout_Plain_Global_11_23_2026_21_01.csv': ('Harbor Stout Plain - Global', fallback_rows(global_rows)),
        'Lone_Star_Club_05_15_2026_11_29.csv': ('Lone Star Club', us_rows()),
    }
    if args.live_csv:
        profiles[args.live_key] = (args.live_name, read_rows(args.live_csv))
    srv = serve(render_page(), profiles)
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={'width': 1500, 'height': 1000})
        pg.on('pageerror', lambda e: errors.append(str(e)[:300]))
        pg.goto(url, wait_until='load', timeout=180000)
        pg.wait_for_timeout(2000)
        loaded = pg.evaluate("() => Array.from(document.querySelectorAll('script[src],link[rel=stylesheet]')).map(e => e.src || e.href).filter(s => s.includes('/static/global-profile/'))")
        check(len(loaded) == 2 and all('?v=smoke' in s for s in loaded), 'the page loads both files with the deploy version', str(loaded))

        print('global profile (the contract file)')
        open_profile(pg, 'Harbor_Stout_Global_11_23_2026_21_00.csv')
        tab(pg, 'insights')
        s = state(pg)
        check(s['global'], 'the file reads as worldwide')
        check(s['summary'] and s['summary']['visible'], 'the worldwide summary shows on Insights')
        txt = (s['summary'] or {}).get('text', '')
        check('About 3 in 10 people in this audience live in the United States.' in txt, 'the summary leads with the finding', txt[:200])
        check('11,063,279' in txt and 'Rest of world' in txt and 'Age, worldwide'.upper() in txt.upper(), 'people, where they live, worldwide age and gender', txt[:400])
        pg.screenshot(path=os.path.join(args.out, 'global_insights.png'))
        tab(pg, 'demographics')
        s = state(pg)
        check(s['summary']['visible'], 'the summary shows on Demographics')
        check(s['demo'] and s['demo']['opts'] == ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'],
              'switcher: United States first, then by share, then Rest of world', str(s['demo'] and s['demo']['opts']))
        check(not s['demoMainVisible'], 'the US-style cards step aside')
        check('Race and ethnicity' in s['demo']['text'] and 'Hispanic or Latino' in s['demo']['text'], 'the US view is in US terms')
        pg.click('#cwGlobalDemo .cwg-opt[data-cwg-country="United Kingdom"]')
        pg.wait_for_timeout(400)
        s = state(pg)
        check('People in the United Kingdom' in s['demo']['text'] and 'White British' in s['demo']['text']
              and '\u00a335,000 - \u00a349,999' in s['demo']['text'] and 'GCSE / O-Level' in s['demo']['text'],
              'picking the United Kingdom shows British groups, pounds and GCSE to degree', s['demo']['text'][:300])
        check(s['demo']['on'].startswith('United Kingdom'), 'the picked country is marked')
        pg.screenshot(path=os.path.join(args.out, 'global_demographics_uk.png'))
        pg.click('#cwGlobalDemo .cwg-opt[data-cwg-country="Nigeria"]')
        pg.wait_for_timeout(300)
        s = state(pg)
        check('Yoruba' in s['demo']['text'] and 'Hausa-Fulani' in s['demo']['text'] and '\u20a6' in s['demo']['text'],
              'Nigeria shows Yoruba, Hausa-Fulani, Igbo and naira bands')
        pg.screenshot(path=os.path.join(args.out, 'global_demographics_nigeria.png'))
        tab(pg, 'location')
        s = state(pg)
        check(not s['summary']['visible'], 'the summary steps aside on Location')
        check(s['loc'] and s['loc']['visible'], 'cities by country show on Location')
        check(s['loc']['groups'] == ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'], 'groups in order', str(s['loc']['groups']))
        check(s['loc']['shares'][:2] == ['31.4% of the audience', '21.1% of the audience'], 'each country shows its share', str(s['loc']['shares']))
        check(not s['flatListVisible'], 'the flat list steps aside')
        check('Birmingham, UK' not in s['usLocations'] and 'New York, USA' in s['usLocations'], 'the US DMA list keeps the US cities only', str(s['usLocations']))
        check(s['loc']['cities'] == 32, 'every city is listed', str(s['loc']['cities']))
        hidden_before = s['loc']['visibleCities']
        pg.click('#cwGlobalLocations .cwg-more[data-cwg-more="United States"]')
        pg.wait_for_timeout(200)
        check(state(pg)['loc']['visibleCities'] > hidden_before, 'Show all opens a country')
        pg.fill('#cwGlobalLocations .cwg-find', 'lagos')
        pg.wait_for_timeout(200)
        s = state(pg)
        check(s['loc']['visibleGroups'] == ['Nigeria'] and s['loc']['visibleCities'] == 1, 'Find narrows to the city', str(s['loc']))
        pg.fill('#cwGlobalLocations .cwg-find', '')
        pg.wait_for_timeout(200)
        pg.screenshot(path=os.path.join(args.out, 'global_location.png'))
        toggled = pg.evaluate("() => { try { toggleDashboardDisplayMode(); toggleDashboardDisplayMode(); return 'ok'; } catch (e) { return String(e).slice(0, 200); } }")
        check(toggled == 'ok', 'the US % / US POP # toggle redraws a worldwide profile without an error', toggled)
        tab(pg, 'behavioral')
        s = state(pg)
        check(not s['summary']['visible'], 'the summary steps aside on Behavioral')
        check(s['geoBehavioral'] == [] and s['geoCards'] == [], 'COUNTRY and the blocks are never behavioral cards', str(s['geoCards']))

        print('a US profile')
        open_profile(pg, 'Lone_Star_Club_05_15_2026_11_29.csv')
        tab(pg, 'location')
        s = state(pg)
        check(not s['global'] and s['summary'] is None and s['demo'] is None and s['loc'] is None, 'none of the worldwide layout')
        check('cw-global' not in s['tabLocClass'] and not s['foreignMode'] and s['flatListVisible'] and s['flatRows'] == 4,
              'the US LOCATION page as before', json.dumps({k: s[k] for k in ('tabLocClass', 'foreignMode', 'flatListVisible', 'flatRows')}))
        tab(pg, 'demographics')
        check(state(pg)['demoMainVisible'], 'the US demographics cards as before')

        print('back to the global profile')
        pg.evaluate("() => switchToProfileTab('Harbor_Stout_Global_11_23_2026_21_00.csv')")
        pg.wait_for_timeout(1500)
        tab(pg, 'demographics')
        s = state(pg)
        check(s['demo'] and s['demo']['on'].startswith('Nigeria'), 'the layout comes back, the picked country kept', str(s['demo'] and s['demo']['on']))

        print('a worldwide file with no country rows yet')
        open_profile(pg, 'Harbor_Stout_Plain_Global_11_23_2026_21_01.csv')
        tab(pg, 'demographics')
        s = state(pg)
        check(s['demo'] and s['demo']['opts'] == [] and 'Everyone in the audience' in s['demo']['text'],
              'no switcher; the worldwide demographics as written', s['demo'] and s['demo']['text'][:200])
        check('65+' in s['demo']['text'] and 'Under US$10,000' in s['demo']['text'], 'worldwide buckets keep their own labels')
        tab(pg, 'location')
        s = state(pg)
        check(s['loc'] and s['loc']['groups'][0] == 'United States' and s['loc']['groups'][-1] == 'Rest of world',
              'the cities still group by country', str(s['loc'] and s['loc']['groups']))
        pg.screenshot(path=os.path.join(args.out, 'plain_global_location.png'))

        if args.live_csv:
            print('the live file')
            open_profile(pg, args.live_key)
            for t in ('insights', 'demographics', 'location'):
                tab(pg, t)
                pg.screenshot(path=os.path.join(args.out, f'live_{t}.png'))
            s = state(pg)
            check(s['global'] and s['loc'] and s['loc']['cities'] > 0, 'the live file reads as worldwide with its cities grouped',
                  json.dumps({'groups': s['loc'] and s['loc']['groups'], 'shares': s['loc'] and s['loc']['shares']}))
            print('       groups:', s['loc'] and list(zip(s['loc']['groups'], s['loc']['shares'])))

        browser.close()

        print('a phone')
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on('pageerror', lambda e: errors.append(str(e)[:300]))
        pg.goto(url, wait_until='load', timeout=180000)
        pg.wait_for_timeout(2000)
        open_profile(pg, 'Harbor_Stout_Global_11_23_2026_21_00.csv')
        tab(pg, 'location')
        s = state(pg)
        check(s['loc'] and s['loc']['overflow'] <= 1, 'cities fit the phone screen', str(s['loc'] and s['loc']['overflow']))
        pg.evaluate("() => { const el = document.getElementById('cwGlobalLocations'); if (el) el.scrollIntoView({block: 'start'}); }")
        pg.wait_for_timeout(300)
        pg.screenshot(path=os.path.join(args.out, 'phone_location.png'))
        tab(pg, 'demographics')
        pg.evaluate("() => { const el = document.getElementById('cwGlobalSummary'); if (el) el.scrollIntoView({block: 'start'}); }")
        pg.wait_for_timeout(300)
        pg.screenshot(path=os.path.join(args.out, 'phone_demographics.png'))
        browser.close()
    srv.shutdown()
    check(not errors, 'no page errors', '; '.join(errors[:5]))
    print()
    print(f'screenshots: {args.out}')
    if FAILS:
        print(f'FAILED ({len(FAILS)}):')
        for f in FAILS:
            print('  -', f)
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
