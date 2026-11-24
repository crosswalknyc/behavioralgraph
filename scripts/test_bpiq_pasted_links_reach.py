#!/usr/bin/env python3
"""Brand Partnership IQ: the campaign links a reader pastes count toward
the campaign's reach in their own right, on any platform (Jenna
2026-11-24).

Emi Hill's Zarna Garg x Maybelline order pasted 52 campaign posts on
Instagram, Facebook, TikTok and YouTube; the research named Instagram and
Facebook as the talent's channels and returned three press pieces, and
the valuation counted the posts as nobody. The links rode the research
asks as context, the first 12 only, and nothing turned a link into a post
the reach could count.

Hermetic: a fake page fetcher, a fake research model, no network, no
mail. Before the fix the research asks carry 12 links and the pasted
posts add no people; after it every link rides the research, every post
is read on its own page, and the posts are their own reach line.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_TWIN_CHECK', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
from migration import bpiq_campaign as C  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim fixtures)

FAIL = 0
WINDOW = {'start': '2026-10-05', 'end': '2026-11-24'}
METHOD_WORDS = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'anthropic',
                'researched', 'calibrated', 'anchored', 'reasoned', 'cross_platform', 'views_basis', 'peers')


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    if '\u2014' in low or '\u2013' in low or re.search(r'\bdoors?\b', low):
        return False
    return not any(w in low for w in METHOD_WORDS)


# ---- the pasted links: 26 Instagram (login wall), 9 Facebook, 10 TikTok, 7 YouTube
def _tiktok_id(day, n):
    import datetime as dt
    secs = int(dt.datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), 15, 0, tzinfo=dt.timezone.utc).timestamp())
    return (secs << 32) | (1000 + n)


def _ig_code(day, n):
    import datetime as dt
    ms = int(dt.datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), 15, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    media_id = ((ms - 1314220021721) << 23) | (n << 10) | 7
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    s = ''
    while media_id:
        media_id, r = divmod(media_id, 64)
        s = alphabet[r] + s
    return s


IG = [f"https://www.instagram.com/p/{_ig_code(d, i)}/" for i, d in enumerate(
    ['2026-11-02'] * 5 + ['2026-11-01'] * 7 + ['2026-10-07', '2026-10-12', '2026-10-14', '2026-10-20', '2026-10-29',
                                               '2026-11-06', '2026-11-13'] + ['2026-09-16', '2026-09-24', '2026-10-01'] * 2 + ['2026-09-18'])]
TT_VIEWS = [278600, 110300, 112900, 105300, 93300, 92100, 71000, 68300, 65500, 57600]
TT_DAYS = ['2026-09-24', '2026-10-13', '2026-11-01', '2026-11-01', '2026-11-01', '2026-11-01', '2026-11-02',
           '2026-10-20', '2026-11-01', '2026-11-01']
TT = [f"https://www.tiktok.com/@talentx/video/{_tiktok_id(d, i)}" for i, d in enumerate(TT_DAYS)]
YT_VIEWS = {'aaa111': (139393, '2026-09-24'), 'bbb222': (100466, '2026-11-02'), 'ccc333': (96078, '2026-10-14'),
            'ddd444': (89763, '2026-10-20'), 'eee555': (23842, '2026-10-31'), 'fff666': (20487, '2026-11-01'),
            'ggg777': (16826, '2026-11-01')}
YT = [f"https://www.youtube.com/watch?v={k}" for k in YT_VIEWS]
FB_VIEWS = {'101': '400K', '102': '290K', '103': '319K', '104': '256K', '105': '190K', '106': '156K', '107': '147K',
            '108': '100K'}
FB = [f"https://www.facebook.com/video.php?v={k}" for k in FB_VIEWS] + \
     ["https://www.facebook.com/talentx/posts/pfbid0aaaaaaaaaaaaaaaaaaaaaaaaaa"]
LINKS = IG + FB + TT + YT + ["https://www.brand.example/collection"]


def fake_fetch(url):
    """Post and account pages the way each platform serves them; Instagram
    and the Facebook post behind a login show no counts."""
    if 'instagram.com' in url or 'pfbid' in url:
        return '<html><title>Instagram</title></html>'
    if 'tiktok.com/@talentx/video/' in url:
        i = TT.index(url)
        return ('<script>{"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{"id":"1",'
                f'"createTime":"1","author":{{"uniqueId":"talentx"}},"stats":{{"diggCount":{TT_VIEWS[i] // 30},'
                f'"shareCount":10,"commentCount":{TT_VIEWS[i] // 900},"playCount":{TT_VIEWS[i]}}}}}}}}}}}</script>')
    if url.rstrip('/').endswith('tiktok.com/@talentx'):
        return '<script>{"userInfo":{"stats":{"followerCount":1300000,"heartCount":9}}}</script>'
    if 'youtube.com/watch' in url:
        v, day = YT_VIEWS[url.split('v=')[1]]
        return (f'<script>var ytInitialPlayerResponse = {{"videoDetails":{{"videoId":"x","viewCount":"{v}"}},'
                f'"microformat":{{"uploadDate":"{day}T07:04:07-07:00"}}}}</script>'
                '<link itemprop="url" href="x"><script>"canonicalBaseUrl":"/@TalentX"</script>')
    if 'youtube.com/@TalentX' in url:
        return '<script>"subscriberCountText":"468K subscribers","viewCountText":"358,559,184 views"</script>'
    if 'facebook.com/video.php' in url:
        k = url.split('v=')[1]
        return f'<meta property="og:title" content="{FB_VIEWS[k]} views &#xb7; 2.7K reactions | Race day | Talent X" />'
    return ''


def order(**kw):
    d = T.inputs(brand='Maybelline', campaign='Maybelline', start='2026-10-05', end='2026-11-02',
                 post_end='2026-11-24', control=False, talent='Talent X')
    d.update(asset_mode='links', links=list(LINKS))
    d.update(kw)
    return d


class FakeResearch:
    """The three research asks: the campaign names Instagram and Facebook as
    the talent's channels; the footprint is three press pieces and nothing
    social; the research's own reading of the links is empty."""

    def __init__(self):
        self.asks = []

    def __call__(self, system, user_prompt, **kw):
        ask = json.loads(user_prompt)
        self.asks.append((kw.get('surface'), ask))
        surface = kw.get('surface')
        if surface == 'bpiq_campaign_research':
            return {'campaign_name': 'Team Maybelline', 'brand': 'Maybelline', 'talent': 'Talent X',
                    'drop_dates': ['2026-10-13'], 'talent_role': {'role': 'ambassador', 'channels': ['Instagram', 'Facebook']},
                    'category': 'BEAUTY', 'confidence': 'medium'}
        if surface == 'bpiq_sellout_check':
            return {'sold_out': False, 'evidence': ''}
        if surface == 'bpiq_campaign_footprint':
            return {'press': [{'outlet': 'People', 'title': 'a', 'url': 'https://people.example/a', 'date': '2026-10-15',
                               'country': 'US', 'outlet_us_monthly_uniques': 21_000_000},
                              {'outlet': 'Allure', 'title': 'b', 'url': 'https://allure.example/b', 'date': '2026-10-20',
                               'country': 'US', 'outlet_us_monthly_uniques': 4_100_000},
                              {'outlet': 'Runners World', 'title': 'c', 'url': 'https://rw.example/c', 'date': '2026-11-01',
                               'country': 'US', 'outlet_us_monthly_uniques': 2_300_000}],
                    'press_pieces_total': 3}
        return {}


def researched(fetch=fake_fetch, inputs=None):
    fr = FakeResearch()
    inputs = inputs or order()
    orig = S.with_pasted_links if hasattr(S, 'with_pasted_links') else None
    if orig is not None:
        S.with_pasted_links = lambda fp, inp, **kw: orig(fp, inp, fetch=fetch)
    try:
        camp = S.research_campaign(inputs, fr, tools=None)
    finally:
        if orig is not None:
            S.with_pasted_links = orig
    return camp, fr


# ------------------------------------------------------------------ tests
def test_research_carries_every_link():
    camp, fr = researched()
    for surface in ('bpiq_campaign_research', 'bpiq_sellout_check', 'bpiq_campaign_footprint'):
        sent = [a for s, a in fr.asks if s == surface]
        check(f"the {surface} ask carries every pasted link ({len(LINKS)})",
              bool(sent) and len(sent[0].get('campaign_links') or []) == len(LINKS))


def test_pasted_posts_count_on_every_platform():
    camp, _fr = researched()
    fp = camp.get('footprint') or {}
    salt = S.run_salt(order())
    r = C.derive_reach(fp, camp, salt, 29, window=WINDOW)
    plats = {ln.get('line') for ln in r.get('lines') or []}
    check("the pasted posts are a reach line of their own", 'the campaign posts' in plats)
    check("the posts reach people (they counted as nobody before)", int(r.get('link_people') or 0) > 0)
    check("the reach is more than the press alone", int(r.get('reach_us') or 0) > int(r.get('press_people') or 0))
    lr = C.link_reach(fp, salt, WINDOW)
    check("TikTok and YouTube posts count though the talent's listed channels are Instagram and Facebook",
          'TikTok' in lr['by_platform'] and 'YouTube' in lr['by_platform'])
    check("every pasted social post is a post (the brand page is not)", lr['posts_pasted'] == len(LINKS) - 1)
    reads = fp.get('link_reads') or []
    by = {r['platform']: r for r in reads}
    check("a TikTok post is read on its own page: plays, likes, comments",
          any(r['platform'] == 'TikTok' and r.get('views') == 110300 and r.get('likes') and r.get('comments') for r in reads))
    check("a YouTube post is read on its own page with its upload day",
          any(r['platform'] == 'YouTube' and r.get('views') == 100466 and r.get('published') == '2026-11-02' for r in reads))
    check("a Facebook post reads the page's own count ('400K views')",
          any(r['platform'] == 'Facebook' and r.get('views') == 400000 for r in reads))
    check("an Instagram post behind the login wall is kept with no count", by.get('Instagram', {}).get('views') is None)
    check("the TikTok and YouTube account sizes are read on their own pages",
          {a['platform']: a['followers'] for a in fp.get('talent_accounts') or []} == {'TikTok': 1300000, 'YouTube': 468000})
    check("the TikTok and Instagram post days come from the post ids",
          any(r['platform'] == 'Instagram' and r.get('published') == '2026-09-16' for r in reads)
          and any(r['platform'] == 'TikTok' and r.get('published') == '2026-09-24' for r in reads))


def test_unread_posts_and_the_window():
    camp, _ = researched()
    fp = camp['footprint']
    salt = S.run_salt(order())
    posts = C.link_posts(fp['link_reads'], window=WINDOW, accounts=fp['talent_accounts'], salt=salt)
    read = sorted(p['views_used'] for p in posts if p['views_basis'] == 'read')
    ig = [p for p in posts if p['platform'] == 'Instagram']
    check("an unread Instagram post takes a low figure, never zero, never above the read posts' low quartile",
          all(0 < p['views_used'] <= C._quantile(read, 0.25) for p in ig))
    fb_unread = [p for p in posts if p['platform'] == 'Facebook' and p['views_basis'] != 'read']
    check("an unread Facebook post takes the low quartile of the read Facebook posts",
          len(fb_unread) == 1 and fb_unread[0]['views_basis'] == 'peers'
          and fb_unread[0]['views_used'] <= sorted(p['views_used'] for p in posts
                                                   if p['platform'] == 'Facebook' and p['views_basis'] == 'read')[2])
    early = [p for p in posts if p['published'] and p['published'] < '2026-09-25']
    check("a post that went up weeks before the window counts only the tail inside it",
          early and all(p['window_share'] < 0.05 for p in early))
    inside = [p for p in posts if p['published'] and p['published'] >= '2026-10-05']
    check("a post inside the window counts in full", inside and all(p['window_share'] == 1.0 for p in inside))
    late = C.link_posts([{'url': YT[1], 'views': 5000, 'published': '2026-12-30'}], window=WINDOW, salt=salt)
    check("a post after the attribution window counts nothing", late and late[0]['views_in_window'] == 0)
    lr = C.link_reach(fp, salt, WINDOW)
    check("people never exceed the views inside the window", lr['people'] < lr['views'])
    check("people on a platform never exceed the account's own audience and a few beyond it",
          lr['by_platform']['TikTok']['people'] <= 1300000 * C._LINK_FOLLOWER_POOL
          and lr['by_platform']['YouTube']['people'] <= 468000 * C._LINK_FOLLOWER_POOL)
    one = C.link_reach({'link_reads': [{'url': TT[1], 'views': 100000, 'published': '2026-10-20'}]}, salt, WINDOW)
    ten = C.link_reach({'link_reads': [{'url': u, 'views': 100000, 'published': '2026-10-20'} for u in TT]}, salt, WINDOW)
    check("ten posts by one account reach more people than one, far fewer than ten times as many",
          one['people'] < ten['people'] < 5 * one['people'])
    capped = C.link_reach({'link_reads': [{'url': u, 'views': 900000, 'published': '2026-10-20'} for u in TT],
                           'talent_accounts': [{'platform': 'TikTok', 'account': 'talentx', 'followers': 200000}]}, salt, WINDOW)
    check("a small account's posts are held to its audience", capped['people'] <= 200000 * C._LINK_FOLLOWER_POOL + 1)


def test_payload_and_text():
    camp, _ = researched()
    inp = order()
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    p = S.build_payload(inp, pr, created_by='tester', campaign=camp)
    reach = (p.get('campaign') or {}).get('reach') or {}
    check("the audience valued is the campaign's reach, posts included",
          p['campaign']['audience_basis'] == 'campaign reach' and p['projected_audience_size'] == reach.get('reach_us'))
    check("the saved reach carries the posts' line and count", reach.get('link_people', 0) > 0 and reach.get('link_posts') == len(LINKS) - 1)
    line = p['campaign'].get('reach_line') or ''
    check("the reach line names the campaign posts plainly", 'through the campaign posts' in line and clean(line))
    v = p['valuation']
    check("the total is the four parts",
          abs(v['total_brand_value'] - (v['brand_engagement_value'] + v['earned_media_value'] + v['brand_lift_value']
                                       + v['conversion_value'])) < 0.05)
    blob = json.dumps(p.get('campaign') or {})
    check("nothing about how an unread post was figured reaches the saved valuation",
          'views_basis' not in blob and 'cross_platform' not in blob and 'link_reads' not in json.dumps(p))
    check("the reach lines read clean", all(clean(ln.get('basis')) for ln in reach.get('lines') or []))
    from migration import bpiq_deck_builder as DB
    txt = DB._campaign_account(DB._derive_context(p, None, 'BEAUTY'))
    check("the deck says the posts reached people through the campaign posts",
          'through the campaign posts' in txt and 'around the campaign posts' not in txt and clean(txt))


def test_one_line_is_the_whole_group():
    fp = C.normalize_footprint({
        'press': [{'outlet': 'Regional Daily', 'title': 'a', 'url': 'https://r.example/a', 'date': '2026-10-15', 'country': 'IN'}],
        'link_reads': [{'url': u, 'views': 150000, 'published': '2026-10-20'} for u in TT],
        'talent_accounts': [{'platform': 'TikTok', 'account': 'talentx', 'followers': 1300000}]})
    salt = S.run_salt(order())
    r = C.derive_reach(fp, {}, salt, 29, window=WINDOW)
    check("the posts line leads when it is the whole group, at the same count as the union",
          r['lines'][0]['line'] == 'the campaign posts' and r['lines'][0]['people'] == r['reach_us'])
    camp = {'footprint': fp, 'talent_role': {'channels': ['Instagram']}}
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    p = S.build_payload(order(), pr, created_by='tester', campaign=camp)
    line = p['campaign']['reach_line']
    check("the reach line says all of them came through the posts and the press reached some of the same people",
          'all of them through the campaign posts' in line and 'were also reached through press' in line
          and 'counted once each' not in line and clean(line))
    check("no sentence in the reach line runs past 25 words",
          all(len(s.split()) <= 25 for s in re.split(r'(?<=[.!?])\s+', line) if s.strip()))
    from migration import bpiq_deck_builder as DB
    txt = DB._campaign_account(DB._derive_context(p, None, 'BEAUTY'))
    check("the deck says every person was reached through the posts, and some also through press",
          'were reached through the campaign posts' in txt and 'also reached through press' in txt)


def test_campaign_named_by_the_brand():
    from migration import bpiq_campaign_assets as CA
    kw = {'talent': ['Talent X'], 'campaign': ['Maybelline'], 'brand': ['Maybelline'], 'pre_end': '2026-10-04',
          'drop_dates': [], 'event_start': '2026-10-05'}
    check("a campaign named only by the brand leaves the brand's homepage its history",
          not CA.no_history_before('maybelline.com homepage', **kw)
          and not CA.no_history_before('Target Maybelline brand shelf', **kw))
    check("a page that names the talent is still the campaign's own",
          CA.no_history_before('Talent X race-day Instagram posts', **kw))
    check("a real campaign name still marks its own page",
          CA.no_history_before('gap.com/browse/gap-x-jyt', talent=['JYT'], campaign=['Gap x JYT'], brand=['Gap'],
                               pre_end='2026-09-01', drop_dates=['2026-09-10'], event_start='2026-09-10'))
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    pr['top_brand_properties'] = [{'common_name': 'maybelline.com homepage', 'pre_hits': 180, 'post_hits': 213},
                                  {'common_name': 'Talent X race-day Instagram posts', 'pre_hits': 40, 'post_hits': 142}]
    camp, _ = researched()
    p = S.build_payload(order(), pr, created_by='tester', campaign=camp)
    pre = {r['common_name']: r['hits'] for r in p['top_brand_properties_pre']}
    check("the payload keeps the homepage's visits before launch", pre.get('maybelline.com homepage', 0) > 0)
    check("the talent's own new posts still show no visits before", pre.get('Talent X race-day Instagram posts') == 0)
    from migration import bpiq_deck_builder as DB
    ctx = DB._derive_context(p, None, 'BEAUTY')
    short, full = DB._next_step(ctx)
    check("the next step never sends people to the talent's social posts for orders",
          'Instagram posts' not in short and 'Instagram posts' not in full)


def test_read_back_and_determinism():
    camp, _ = researched()
    salt = S.run_salt(order())
    r1 = C.derive_reach(camp['footprint'], camp, salt, 29, window=WINDOW)
    saved = json.loads(json.dumps(camp))
    back = S.normalize_campaign(saved, order())
    check("the readings survive the saved research read back",
          len(back['footprint'].get('link_reads') or []) == len(LINKS) - 1 and back['footprint'].get('talent_accounts'))
    r2 = C.derive_reach(back['footprint'], back, salt, 29, window=WINDOW)
    check("the reach read back is the same reach (nothing re-drawn)", r1['reach_us'] == r2['reach_us'])
    r3 = C.derive_reach(camp['footprint'], camp, S.run_salt(order(qualifier='talent x', brand_partner='maybelline',
                                                                    campaign='MAYBELLINE')), 29, window=WINDOW)
    check("the same order in another casing gives the same reach", r3['reach_us'] == r1['reach_us'])
    check("messy last digits on the posts' people", r1['link_people'] % 10 != 0 and r1['reach_us'] % 10 != 0)


def test_research_reads_and_nothing_countable():
    research_fp = C.normalize_footprint({'link_reads': [
        {'url': IG[0], 'likes': 4100, 'source': 'research'},
        {'url': TT[1], 'views': 1, 'source': 'research'}]})
    out = S.with_pasted_links(research_fp, order(), fetch=fake_fetch)
    by = {r['url']: r for r in out['link_reads']}
    check("the research's reading fills a count the page did not show", by[IG[0]].get('likes') == 4100
          and by[IG[0]].get('source') == 'research')
    check("a page reading wins over the research's", by[TT[1]].get('views') == 110300)
    blank = S.with_pasted_links({}, order(links=IG[:3]), fetch=lambda u: '')
    check("posts none of which could be read, with no account size, are not a footprint (the order is asked about)",
          not C.has_footprint(blank))
    check("with no fetch (tests, switched off) the posts are kept unread and nothing is fetched",
          all(not r['read'] for r in S.with_pasted_links({}, order(links=TT[:2]), fetch=None)['link_reads']))


def test_reader_parsing():
    from migration import bpiq_link_reads as R
    check("the platform comes from the address",
          [R.platform_of(u) for u in (IG[0], FB[0], TT[0], YT[0], 'https://x.com/a/status/1', 'https://www.threads.net/@a/post/x',
                                      'https://brand.example/x')] == ['Instagram', 'Facebook', 'TikTok', 'YouTube', 'X (Twitter)',
                                                                      'Threads', ''])
    check("the same post pasted twice is one post", len(R.social_links([TT[0], TT[0] + '/', TT[0].upper().replace('HTTPS', 'https')])) <= 2
          and R.link_key('https://www.youtube.com/watch?v=abc&t=3s') == R.link_key('https://youtube.com/watch?v=abc'))
    check("counts as pages print them", R.count_of('400K') == 400000 and R.count_of('2.7K') == 2700
          and R.count_of('1,234') == 1234 and R.count_of('') is None)
    check("the account comes from the address when it names one",
          R.account_of(TT[0]) == 'talentx' and R.account_of(FB[-1]) == 'talentx' and R.account_of(FB[0]) == '')
    check("an X post's day comes from its id", str(R.published_from_id('https://x.com/a/status/1856000000000000000')) == '2024-11-11')
    check("no fetch runs in tests", R.read_link(TT[0])['read'] is False)


def main():
    for t in (test_research_carries_every_link, test_pasted_posts_count_on_every_platform, test_unread_posts_and_the_window,
              test_payload_and_text, test_one_line_is_the_whole_group, test_campaign_named_by_the_brand,
              test_read_back_and_determinism,
              test_research_reads_and_nothing_countable, test_reader_parsing):
        print(t.__name__)
        try:
            t()
        except Exception as e:     # a crash before the fix is a failure, never a skip
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran ({type(e).__name__}: {e})", False)
    print(f"\n{'FAIL' if FAIL else 'PASS'}: {FAIL} failure(s)")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
