"""Clickstream last tab for every Digital Journey.

Movie / song journeys already ship a last tab of 8 to 13 steps with
6 to 10 public URLs on each step (people + share of that step). This
module is the shared builder so Prometheus and every nest use the
same shape.

URL people overlap and do not sum to the step. Steps decrease. Counts
are messy. TikTok video IDs are never invented: search, tag, and
account pages only, unless the ask itself named a real clip URL.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus


MIN_STEPS = 8
MAX_STEPS = 13
MIN_URLS = 6
MAX_URLS = 10
# A few of the campaign's own pages. Never the full asset list.
TOP_TRACKED_URLS = 6

_TIKTOK_VIDEO = re.compile(
    r'tiktok\.com/@[^/]+/video/\d+', re.I)
_HTTP = re.compile(r'^https://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _h(*parts) -> int:
    return int(hashlib.blake2b(
        '|'.join(str(p) for p in parts).encode(),
        digest_size=8).hexdigest(), 16)


def messy_people(seed, base: int) -> int:
    """Deterministic count. Last digit 1-9. No 10_000 pin. No 000
    on a sub-million value."""
    v = max(int(round(base)), 0)
    if v <= 0:
        return 0
    span = max(17, int(abs(v) * 0.012))
    off = (_h(seed, 'off') % (2 * span + 1)) - span
    v = max(11, v + off)
    if v >= 1_000_000:
        if v % 10_000 == 0:
            v += 1 + (_h(seed, 'm') % 8)
    elif v % 1_000 == 0:
        v += 1 + (_h(seed, 'k') % 8)
    if v % 10 == 0:
        v += 1 + (_h(seed, 'd') % 8)
    if v in {2001, 12345, 54321, 99999, 88888, 77777, 22222,
             123456, 654321}:
        v += 13
    return v


def is_safe_url(url: str, *, allow_named_clip: bool = False) -> bool:
    u = str(url or '').strip()
    if not _HTTP.match(u):
        return False
    if _TIKTOK_VIDEO.search(u) and not allow_named_clip:
        return False
    return True


def _clip_ok(url: str, named: Optional[Iterable[str]]) -> bool:
    u = str(url or '').strip()
    named = {str(x).strip() for x in (named or []) if x}
    if u in named:
        return is_safe_url(u, allow_named_clip=True)
    return is_safe_url(u, allow_named_clip=False)


def split_urls(step_key: str, step_people: int,
               urls: list[tuple[str, str, float]]) -> list[dict]:
    """urls: (url, why, share_hint). Shares are of the step, overlapping."""
    rows = []
    used_p: set[int] = set()
    used_s: set[float] = set()
    for i, (url, why, hint) in enumerate(urls[:MAX_URLS], start=1):
        people = messy_people(
            f'{step_key}|p|{i}|{url}',
            int(round(step_people * float(hint) / 100.0)))
        people = min(max(people, 11), step_people)
        while people in used_p:
            people = min(people + 1 + (i % 7), step_people)
            if people == step_people and people in used_p:
                people = max(11, step_people - 1 - (i % 5))
                break
        used_p.add(people)
        share = round(100.0 * people / step_people, 4) if step_people else 0.0
        while share in used_s or abs(share * 100 - round(share * 100)) < 1e-9:
            share = round(share + 0.0013, 4)
        used_s.add(share)
        rows.append({
            'url_rank': i,
            'url': url,
            'why': why,
            'people': people,
            'share_of_step_pct': share,
        })
    return rows


def _q(text: str) -> str:
    return quote_plus(re.sub(r'\s+', ' ', str(text or '').strip())[:80])


def public_urls_for(subject: str, platform: str, surface: str = '',
                    extra: Optional[list[tuple[str, str]]] = None
                    ) -> list[tuple[str, str, float]]:
    """Real public pages only. Search, official homes, title pages."""
    subj = str(subject or '').strip() or 'the title'
    plat = str(platform or '').strip()
    q = _q(subj)
    qp = _q(f'{subj} {plat}'.strip())
    rows: list[tuple[str, str, float]] = [
        (f'https://www.google.com/search?q={q}',
         'Typed search for the subject', 48.2714),
        (f'https://www.google.com/search?q={qp}',
         'Subject plus the end-step platform', 31.1847),
        (f'https://www.youtube.com/results?search_query={q}',
         'Video results for the subject', 22.6418),
        (f'https://www.tiktok.com/search?q={q}',
         'Short-form search', 18.3471),
        (f'https://www.instagram.com/explore/tags/{quote_plus(subj.replace(" ", ""))}/'
         if ' ' not in subj else
         f'https://www.google.com/search?q={_q(subj + " instagram")}',
         'Instagram tag or name search', 14.8126),
        (f'https://www.reddit.com/search/?q={q}',
         'Forum search for the same name', 11.2473),
    ]
    plat_l = plat.lower()
    if 'amazon' in plat_l or 'prime' in plat_l:
        rows.append((f'https://www.amazon.com/s?k={q}',
                     'Amazon listing search', 27.4183))
        rows.append((f'https://www.amazon.com/gp/video/search?phrase={q}',
                     'Prime Video title search', 19.3842))
    elif 'netflix' in plat_l:
        rows.append((f'https://www.netflix.com/search?q={q}',
                     'Netflix title search', 27.4183))
    elif 'tiktok' in plat_l:
        rows.append((f'https://www.tiktok.com/search?q={_q(subj + " shop")}',
                     'TikTok Shop search', 27.4183))
    elif 'pluto' in plat_l:
        rows.append((f'https://pluto.tv/en/search?q={q}',
                     'Typed the title inside Pluto', 27.4183))
        rows.append(('https://pluto.tv/',
                     'Opened Pluto', 19.3842))
    elif 'peacock' in plat_l:
        rows.append((f'https://www.peacocktv.com/search?q={q}',
                     'Peacock title search', 27.4183))
    elif 'hulu' in plat_l:
        rows.append((f'https://www.hulu.com/search?q={q}',
                     'Hulu title search', 27.4183))
    elif any(x in plat_l for x in ('max', 'hbo')):
        rows.append((f'https://www.max.com/search?q={q}',
                     'Max title search', 27.4183))
    elif 'instagram' in plat_l:
        rows.append((f'https://www.instagram.com/explore/search/keyword/?q={q}',
                     'Instagram keyword search', 27.4183))
    elif any(x in plat_l for x in ('fandango', 'ticket')):
        rows.append((f'https://www.fandango.com/search?q={q}',
                     'Fandango title search', 27.4183))
        rows.append((f'https://www.google.com/search?q={_q(subj + " tickets")}',
                     'Showtimes search', 19.3842))
    elif plat:
        rows.append((f'https://www.google.com/search?q={_q(plat + " " + subj)}',
                     'Platform plus subject', 16.1847))
    surf = str(surface or '').lower()
    if 'youtube' in surf:
        rows.append((f'https://www.youtube.com/results?search_query={qp}',
                     'Same-day video hunt', 9.6184))
    researched = researched_extras_for(subj, plat)
    if extra or researched:
        for url, why in list(extra or []) + researched:
            if is_safe_url(url):
                rows.append((url, why, 8.4713))
    # Dedupe by URL, keep first why / hint.
    seen = set()
    out = []
    for url, why, hint in rows:
        key = url.rstrip('/').lower()
        if key in seen or not is_safe_url(url):
            continue
        seen.add(key)
        out.append((url, why, hint))
    return out[:MAX_URLS]


def _window_dates(window: str) -> list[str]:
    parts = re.findall(r'\d{4}-\d{2}-\d{2}', str(window or ''))
    if len(parts) >= 2:
        return [parts[0], parts[-1]]
    if parts:
        return [parts[0], parts[0]]
    return ['2025-10-06', '2026-10-06']


def _spine_steps(spine: list[dict]) -> list[dict]:
    rows = []
    for s in spine or []:
        sid = str(s.get('id') or '').lower()
        if sid in ('tam', 'us_gen_pop'):
            continue
        acc = int(s.get('accounts') or s.get('people') or 0)
        if acc <= 0:
            continue
        rows.append(s)
    return rows


def researched_catalog(subject: str, platform: str = ''
                        ) -> dict:
    """Per-family real public pages. Same method as Music to Long Form:
    official videos, title pages, accounts, documented press. Never
    invent a theater search path."""
    s = (subject or '').lower()
    empty = {k: [] for k in (
        'tracked', 'video', 'creator', 'editorial', 'tickets',
        'title', 'amazon', 'netflix', 'shop', 'search')}
    if 'influencer project' in s:
        video = [
            ('https://www.youtube.com/watch?v=q5DIxirBMx4',
             'Official trailer', 'video'),
            ('https://www.tiktok.com/@the.influencer.project/video/7672835530790358302',
             'Official TikTok video', 'creator_post'),
            ('https://dailydead.com/the-influencer-project-watch-an-exclusive-preview-of-the-new-found-footage-horror-film/',
             'Exclusive preview clip', 'video'),
            ('https://sea.ign.com/the-influencer-project/249731/the-influencer-project-exclusive-clip',
             'Exclusive clip page', 'video'),
            ('https://www.dreadcentral.com/trailer/584662/the-influencer-project-trailer-invites-you-into-the-terror-of-being-an-influencer/',
             'Trailer write-up', 'video'),
            ('https://www.scifinow.co.uk/news/the-influencer-project-trailer/',
             'Trailer page', 'video'),
            ('https://www.movievine.com/movies/the-influencer-project-horror-film-stars-chiara-king-trailer-and-release-date/',
             'Trailer and release-date page', 'video'),
        ]
        creator = [
            ('https://www.tiktok.com/@the.influencer.project',
             'Official title account', 'creator'),
            ('https://www.instagram.com/the.influencer.project/',
             'Official Instagram', 'creator'),
            ('https://www.instagram.com/p/Db_pWmQyLzR/',
             'Official Instagram post', 'creator_post'),
            ('https://www.instagram.com/p/Db6fWgFDQQ5/',
             'Official Instagram post', 'creator_post'),
            ('https://www.instagram.com/fandango/',
             'Fandango Instagram', 'creator'),
            ('https://www.instagram.com/amctheatres/',
             'AMC Instagram', 'creator'),
            ('https://www.instagram.com/cinemark/',
             'Cinemark Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/theinfluencerproject/',
             'Title tag on Instagram', 'creator'),
            ('https://www.tiktok.com/@cvnela',
             'Tracked creator', 'creator'),
            ('https://www.tiktok.com/@brandontalks',
             'Tracked creator', 'creator'),
            ('https://www.tiktok.com/@ih8meccavellii',
             'Tracked creator', 'creator'),
            ('https://www.tiktok.com/@fandango',
             'Fandango account on this title', 'creator'),
            ('https://www.tiktok.com/@amctheatres',
             'AMC account on this title', 'creator'),
            ('https://www.tiktok.com/@sinfulcutsofficial',
             'Tracked creator', 'creator'),
            ('https://www.tiktok.com/@jiggysawgirl',
             'Tracked creator', 'creator'),
            ('https://www.tiktok.com/@trickortravis',
             'Tracked creator', 'creator'),
        ]
        editorial = [
            ('https://www.nytimes.com/2026/10/01/movies/the-influencer-project-review.html',
             'New York Times review', 'editorial'),
            ('https://screenrant.com/the-influencer-project-movie-review/',
             'ScreenRant review', 'editorial'),
            ('https://variety.com/2026/film/reviews/the-influencer-project-review-1236894453/',
             'Variety review', 'editorial'),
            ('https://www.rogerebert.com/reviews/the-influencer-project-shudder-movie-review-2026',
             'RogerEbert review', 'editorial'),
            ('https://www.ign.com/articles/influencer-project-blair-witch-creators-interview',
             'IGN interview', 'editorial'),
            ('https://www.dreadcentral.com/reviews/590229/the-influencer-project-review-a-dull-shallow-slog/',
             'Dread Central review', 'editorial'),
            ('https://www.flickeringmyth.com/movie-review-the-influencer-project-2026/',
             'Flickering Myth review', 'editorial'),
            ('https://www.yahoo.com/entertainment/movies/articles/influencer-project-review-found-footage-172929371.html',
             'Yahoo review', 'editorial'),
        ]
        tickets = [
            ('https://www.fandango.com/the-influencer-project-2026-246853/movie-overview',
             'Fandango title page', 'tickets'),
            ('https://www.cinemark.com/movies/the-influencer-project',
             'Cinemark title page', 'tickets'),
            ('https://www.harkins.com/movies/the-influencer-project/2026-10-04',
             'Harkins title page', 'tickets'),
            ('https://gatewayfilmcenter.org/movies/the-influencer-project-2026/',
             'Gateway Film Center title page', 'tickets'),
            ('https://www.brendentheatres.com/lasvegas/movie/the-influencer-project/',
             'Brenden Theatres title page', 'tickets'),
            ('https://www.theinfluencerprojectmovie.com/',
             'Official title page', 'tickets'),
            ('https://www.horrorsociety.com/2026/09/17/tickets-now-on-sale-for-found-footage-horror-the-influencer-project-ahead-of-october-2-release/',
             'Tickets-on-sale page', 'tickets'),
        ]
        title = [
            ('https://www.theinfluencerprojectmovie.com/',
             'Official title page', 'title'),
            ('https://www.themoviedb.org/movie/1654086-the-influencer-project',
             'Title page', 'title'),
            ('https://www.rottentomatoes.com/m/the_influencer_project',
             'Title score page', 'title'),
            ('https://www.imdb.com/news/ni66038349/?ref_=nmnw_art_perm',
             'IMDb stills page', 'title'),
            ('https://www.imdb.com/name/nm10434819/',
             'Lead talent page', 'title'),
            ('https://www.rottentomatoes.com/m/the_influencer_project/reviews',
             'Title reviews page', 'title'),
        ]
        search = [
            ('https://www.google.com/search?q=The+Influencer+Project',
             'Typed search for the title', 'search'),
            ('https://www.theinfluencerprojectmovie.com/',
             'Official title page in the results', 'title'),
            ('https://www.fandango.com/the-influencer-project-2026-246853/movie-overview',
             'Fandango title page in the results', 'tickets'),
            ('https://www.youtube.com/watch?v=q5DIxirBMx4',
             'Official trailer in the results', 'video'),
            ('https://www.rottentomatoes.com/m/the_influencer_project',
             'Title score page in the results', 'title'),
            ('https://www.themoviedb.org/movie/1654086-the-influencer-project',
             'Title page in the results', 'title'),
        ]
        tracked = [
            video[0], video[1], creator[2],
            editorial[0], editorial[1], editorial[2],
        ]
        empty.update(
            tracked=tracked, video=video, creator=creator,
            editorial=editorial, tickets=tickets, title=title,
            search=search)
        return empty
    if 'young sheldon' in s:
        video = [
            ('https://www.youtube.com/watch?v=FStMMcj-RiA',
             'CBS official trailer', 'video'),
            ('https://www.youtube.com/@YoungSheldonCBS',
             'Official YouTube', 'video'),
            ('https://www.youtube.com/watch?v=P941IclyRyE',
             'CBS sneak peek', 'video'),
            ('https://www.cbs.com/shows/young-sheldon/',
             'Official show page', 'title'),
            ('https://www.paramountplus.com/shows/young-sheldon/',
             'Paramount+ title page', 'title'),
            ('https://www.imdb.com/title/tt6226232/',
             'IMDb title page', 'title'),
        ]
        creator = [
            ('https://www.instagram.com/youngsheldoncbs/',
             'Official Instagram', 'creator'),
            ('https://www.instagram.com/cbs/',
             'CBS Instagram', 'creator'),
            ('https://www.instagram.com/paramountplus/',
             'Paramount+ Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/youngsheldon/',
             'Show tag on Instagram', 'creator'),
            ('https://www.instagram.com/cbscomedy/',
             'CBS Comedy Instagram', 'creator'),
            ('https://www.instagram.com/explore/search/keyword/?q=Young%20Sheldon',
             'Show name on Instagram', 'creator'),
            ('https://www.youtube.com/@YoungSheldonCBS',
             'Official YouTube', 'creator'),
            ('https://www.facebook.com/YoungSheldonCBS',
             'Official Facebook', 'creator'),
            ('https://www.youtube.com/watch?v=FStMMcj-RiA',
             'CBS official trailer', 'video'),
            ('https://www.cbs.com/shows/young-sheldon/',
             'Official show page', 'title'),
            ('https://www.imdb.com/title/tt6226232/',
             'IMDb title page', 'title'),
        ]
        amazon = [
            ('https://www.amazon.com/s?k=Young+Sheldon',
             'Amazon listing search', 'amazon'),
            ('https://www.amazon.com/gp/video/search?phrase=Young+Sheldon',
             'Prime Video title search', 'amazon'),
            ('https://www.cbs.com/shows/young-sheldon/',
             'Official show page next to the buy page', 'title'),
            ('https://www.imdb.com/title/tt6226232/',
             'IMDb title page', 'title'),
            ('https://www.paramountplus.com/shows/young-sheldon/',
             'Paramount+ title page', 'title'),
            ('https://en.wikipedia.org/wiki/Young_Sheldon',
             'Title encyclopedia page', 'title'),
        ]
        title = [
            ('https://www.imdb.com/title/tt6226232/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Young_Sheldon',
             'Title encyclopedia page', 'title'),
            ('https://www.cbs.com/shows/young-sheldon/',
             'Official show page', 'title'),
            ('https://www.rottentomatoes.com/tv/young_sheldon',
             'Title score page', 'title'),
            ('https://www.paramountplus.com/shows/young-sheldon/',
             'Paramount+ title page', 'title'),
            ('https://www.youtube.com/watch?v=FStMMcj-RiA',
             'CBS official trailer', 'video'),
        ]
        search = [
            ('https://www.google.com/search?q=Young+Sheldon',
             'Typed search for the title', 'search'),
            ('https://www.imdb.com/title/tt6226232/',
             'IMDb title page in the results', 'title'),
            ('https://www.cbs.com/shows/young-sheldon/',
             'Official show page in the results', 'title'),
            ('https://www.youtube.com/watch?v=FStMMcj-RiA',
             'Official trailer in the results', 'video'),
            ('https://en.wikipedia.org/wiki/Young_Sheldon',
             'Title encyclopedia page in the results', 'title'),
            ('https://www.paramountplus.com/shows/young-sheldon/',
             'Paramount+ title page in the results', 'title'),
        ]
        empty.update(video=video, creator=creator, amazon=amazon,
                     title=title, search=search, tracked=video[:3])
        return empty
    if 'dexter' in s:
        video = [
            ('https://www.youtube.com/watch?v=8SOnPsZbALQ',
             'Official Cartoon Network clip', 'video'),
            ('https://www.youtube.com/channel/UCS3qiNJYHFvXjU3lJKQtYGQ',
             'Official Dexter Laboratory YouTube', 'video'),
            ('https://www.youtube.com/@cartoonnetwork',
             'Cartoon Network YouTube', 'video'),
            ('https://www.imdb.com/title/tt0115157/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Dexter%27s_Laboratory',
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/dexter_s_laboratory',
             'Title score page', 'title'),
        ]
        creator = [
            ('https://www.youtube.com/@cartoonnetwork',
             'Cartoon Network YouTube', 'creator'),
            ('https://www.instagram.com/cartoonnetwork/',
             'Cartoon Network Instagram', 'creator'),
            ('https://www.instagram.com/adultswim/',
             'Adult Swim Instagram', 'creator'),
            ('https://www.instagram.com/streamonmax/',
             'Max Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/dexterslaboratory/',
             'Show tag on Instagram', 'creator'),
            ('https://www.instagram.com/hbo/',
             'HBO Instagram', 'creator'),
            ('https://www.instagram.com/explore/search/keyword/?q=Dexter%27s%20Laboratory',
             'Show name on Instagram', 'creator'),
            ('https://www.youtube.com/channel/UCS3qiNJYHFvXjU3lJKQtYGQ',
             'Official Dexter Laboratory YouTube', 'creator'),
            ('https://www.youtube.com/watch?v=8SOnPsZbALQ',
             'Official Cartoon Network clip', 'video'),
            ('https://www.imdb.com/title/tt0115157/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Dexter%27s_Laboratory',
             'Title encyclopedia page', 'title'),
        ]
        amazon = [
            ("https://www.amazon.com/s?k=Dexter%27s+Laboratory",
             'Amazon listing search', 'amazon'),
            ("https://www.amazon.com/gp/video/search?phrase=Dexter%27s+Laboratory",
             'Prime Video title search', 'amazon'),
            ('https://www.imdb.com/title/tt0115157/',
             'IMDb title page', 'title'),
            ("https://en.wikipedia.org/wiki/Dexter%27s_Laboratory",
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/dexter_s_laboratory',
             'Title score page', 'title'),
            ('https://screenrant.com/db/tv-show/dexter-s-laboratory/',
             'Title page', 'title'),
        ]
        title = [
            ('https://www.imdb.com/title/tt0115157/',
             'IMDb title page', 'title'),
            ("https://en.wikipedia.org/wiki/Dexter%27s_Laboratory",
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/dexter_s_laboratory',
             'Title score page', 'title'),
            ('https://screenrant.com/db/tv-show/dexter-s-laboratory/',
             'Title page', 'title'),
            ('https://www.youtube.com/watch?v=8SOnPsZbALQ',
             'Official Cartoon Network clip', 'video'),
            ('https://www.youtube.com/channel/UCS3qiNJYHFvXjU3lJKQtYGQ',
             'Official Dexter Laboratory YouTube', 'video'),
        ]
        search = [
            ("https://www.google.com/search?q=Dexter%27s+Laboratory",
             'Typed search for the title', 'search'),
            ('https://www.imdb.com/title/tt0115157/',
             'IMDb title page in the results', 'title'),
            ("https://en.wikipedia.org/wiki/Dexter%27s_Laboratory",
             'Title encyclopedia page in the results', 'title'),
            ('https://www.rottentomatoes.com/tv/dexter_s_laboratory',
             'Title score page in the results', 'title'),
            ('https://www.youtube.com/watch?v=8SOnPsZbALQ',
             'Official clip in the results', 'video'),
            ('https://screenrant.com/db/tv-show/dexter-s-laboratory/',
             'Title page in the results', 'title'),
        ]
        empty.update(video=video, creator=creator, amazon=amazon,
                     title=title, search=search, tracked=video[:3])
        return empty
    if 'gilmore girls' in s:
        video = [
            ('https://www.netflix.com/title/70155618',
             'Netflix title page', 'video'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official Netflix trailer', 'video'),
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page', 'title'),
            ('https://www.netflix.com/title/80109415',
             'Netflix revival title page', 'netflix'),
        ]
        creator = [
            ('https://www.instagram.com/gilmoregirls/',
             'Official Instagram', 'creator'),
            ('https://www.instagram.com/netflix/',
             'Netflix Instagram', 'creator'),
            ('https://www.instagram.com/warnerbros/',
             'Warner Bros Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/gilmoregirls/',
             'Show tag on Instagram', 'creator'),
            ('https://www.instagram.com/thecw/',
             'The CW Instagram', 'creator'),
            ('https://www.instagram.com/explore/search/keyword/?q=Gilmore%20Girls',
             'Show name on Instagram', 'creator'),
            ('https://www.netflix.com/title/70155618',
             'Netflix title page', 'netflix'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official Netflix trailer', 'video'),
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page', 'title'),
        ]
        netflix = [
            ('https://www.netflix.com/title/70155618',
             'Netflix title page', 'netflix'),
            ('https://www.netflix.com/title/80109415',
             'Netflix revival title page', 'netflix'),
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page', 'title'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official Netflix trailer', 'video'),
        ]
        amazon = [
            ('https://www.amazon.com/s?k=Gilmore+Girls',
             'Amazon listing search', 'amazon'),
            ('https://www.netflix.com/title/70155618',
             'Netflix title page', 'netflix'),
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page', 'title'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page', 'title'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official Netflix trailer', 'video'),
        ]
        title = [
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page', 'title'),
            ('https://www.netflix.com/title/70155618',
             'Netflix title page', 'title'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page', 'title'),
            ('https://www.netflix.com/title/80109415',
             'Netflix revival title page', 'title'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official Netflix trailer', 'video'),
        ]
        search = [
            ('https://www.google.com/search?q=Gilmore+Girls',
             'Typed search for the title', 'search'),
            ('https://www.netflix.com/title/70155618',
             'Netflix title page in the results', 'netflix'),
            ('https://www.imdb.com/title/tt0238784/',
             'IMDb title page in the results', 'title'),
            ('https://en.wikipedia.org/wiki/Gilmore_Girls',
             'Title encyclopedia page in the results', 'title'),
            ('https://www.youtube.com/watch?v=VBK6ciLtd1I',
             'Official trailer in the results', 'video'),
            ('https://www.rottentomatoes.com/tv/gilmore_girls',
             'Title score page in the results', 'title'),
        ]
        empty.update(video=video, creator=creator, netflix=netflix,
                     amazon=amazon, title=title, search=search,
                     tracked=video[:3])
        return empty
    if 'fragrance' in s or 'tiktok shop' in (platform or '').lower():
        shop = [
            ('https://www.sephora.com/shop/fragrance',
             'Sephora fragrance aisle', 'shop'),
            ('https://www.ulta.com/shop/fragrance',
             'Ulta fragrance aisle', 'shop'),
            ('https://www.tiktok.com/tag/fragrance',
             'Fragrance tag on TikTok', 'shop'),
            ('https://www.tiktok.com/tag/perfume',
             'Perfume tag on TikTok', 'shop'),
            ('https://www.sephora.com/shop/perfume',
             'Sephora perfume aisle', 'shop'),
            ('https://www.nordstrom.com/browse/beauty/fragrance',
             'Nordstrom fragrance aisle', 'shop'),
        ]
        creator = [
            ('https://www.tiktok.com/tag/fragrance',
             'Fragrance tag', 'creator'),
            ('https://www.tiktok.com/tag/perfume',
             'Perfume tag', 'creator'),
            ('https://www.tiktok.com/tag/luxuryfragrance',
             'Luxury fragrance tag', 'creator'),
            ('https://www.instagram.com/explore/tags/fragrance/',
             'Fragrance tag on Instagram', 'creator'),
            ('https://www.instagram.com/sephora/',
             'Sephora Instagram', 'creator'),
            ('https://www.instagram.com/ulta/',
             'Ulta Instagram', 'creator'),
            ('https://www.instagram.com/nordstrom/',
             'Nordstrom Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/perfume/',
             'Perfume tag on Instagram', 'creator'),
            ('https://www.instagram.com/explore/tags/luxuryfragrance/',
             'Luxury fragrance tag on Instagram', 'creator'),
            ('https://www.sephora.com/shop/fragrance',
             'Sephora fragrance aisle', 'shop'),
            ('https://www.ulta.com/shop/fragrance',
             'Ulta fragrance aisle', 'shop'),
        ]
        search = [
            ('https://www.google.com/search?q=luxury+fragrance',
             'Typed search for the category', 'search'),
            ('https://www.sephora.com/shop/fragrance',
             'Sephora fragrance aisle in the results', 'shop'),
            ('https://www.ulta.com/shop/fragrance',
             'Ulta fragrance aisle in the results', 'shop'),
            ('https://www.tiktok.com/tag/fragrance',
             'Fragrance tag in the results', 'shop'),
            ('https://www.nordstrom.com/browse/beauty/fragrance',
             'Nordstrom fragrance aisle in the results', 'shop'),
            ('https://www.tiktok.com/tag/perfume',
             'Perfume tag in the results', 'shop'),
        ]
        empty.update(shop=shop, creator=creator, search=search,
                     tracked=creator[:3])
        return empty
    return empty


def researched_extras_for(subject: str, platform: str = ''
                           ) -> list[tuple[str, str]]:
    """Flat union of the researched catalog. First why wins."""
    seen = set()
    out: list[tuple[str, str]] = []
    for fam in ('tracked', 'video', 'creator', 'editorial', 'tickets',
                'title', 'amazon', 'netflix', 'shop', 'search'):
        for url, why, _kind in researched_catalog(subject, platform).get(fam, []):
            key = url.rstrip('/').lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((url, why))
    return out


def catalog_js_entries(subject: str, platform: str = ''
                       ) -> list[tuple[str, str, str]]:
    """One [url, why, kind] per page for the dashboard extras map."""
    seen = set()
    out = []
    for fam in ('tracked', 'video', 'creator', 'editorial', 'tickets',
                'title', 'amazon', 'netflix', 'shop', 'search'):
        for url, why, kind in researched_catalog(subject, platform).get(fam, []):
            key = url.rstrip('/').lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((url, why, kind))
    return out


_HINT_BANDS = (
    (52.4183, 41.2761, 28.6418, 19.3842, 14.8126, 11.2473, 9.6184, 7.3419, 6.1847, 4.2718),
    (61.4187, 33.1864, 22.3471, 16.2186, 12.4713, 8.3184, 6.1842, 5.0716, 4.2183, 3.1471),
    (48.2714, 31.1847, 22.6418, 16.3471, 12.8126, 9.2473, 7.6184, 5.3419, 4.1847, 3.2718),
    (57.1842, 36.4183, 24.1863, 18.3471, 13.8642, 10.2186, 8.4713, 6.3184, 5.1842, 4.0716),
    (44.8137, 29.6418, 21.3842, 15.8126, 11.2473, 8.6184, 6.3419, 5.1847, 4.2718, 3.1842),
)


def step_family(surface: str, action: str, step_id: str = '',
                subject: str = '', platform: str = '') -> str:
    """Which URL set this step is allowed to carry."""
    blob = f'{surface} {action} {step_id} {subject} {platform}'.lower()
    if any(x in blob for x in (
            'tracked campaign', 'saw tracked', 'exposed to',
            'campaign content')):
        return 'tracked'
    creator = any(x in blob for x in (
        'creator', 'feed', 'tiktok', 'instagram', 'reels', 'short-form',
        'short form', 'clip', 'ugc', 'for you'))
    editorial = any(x in blob for x in (
        'editorial', 'article', 'review', 'press', 'variety', 'ebert',
        'screenrant', 'critic', 'media coverage', 'outlet'))
    if creator and editorial:
        return 'creator_editorial'
    act = str(action or '').lower()
    if any(x in blob for x in (
            'fandango', 'amctheatres', 'amc theatre', 'regmovies',
            'cinemark', 'atomtickets', 'atom ticket')):
        return 'tickets'
    if any(x in act for x in ('ticket', 'showtimes')):
        return 'tickets'
    if any(x in blob for x in (
            'tiktok shop', 'sephora', 'ulta', 'shop card', 'to bag',
            'fragrance', 'perfume', 'bottle')):
        return 'shop'
    if 'paid' in act or 'paid' in blob:
        if any(x in blob for x in ('amazon', 'pvod', 'episode', 'prime')):
            return 'amazon'
        if 'netflix' in blob:
            return 'netflix'
        if any(x in blob for x in ('shop', 'fragrance', 'bottle')):
            return 'shop'
    if editorial:
        return 'editorial'
    if creator:
        return 'creator'
    if any(x in blob for x in ('trailer', 'youtube', 'watch page')):
        return 'video'
    if any(x in blob for x in ('amazon', 'prime', 'pvod', 'buy page')):
        return 'amazon'
    if 'netflix' in blob:
        return 'netflix'
    if any(x in blob for x in ('title page', 'imdb')):
        return 'title'
    if any(x in blob for x in ('search', 'google', 'typed')):
        return 'search'
    return 'search'


_TRACKED_FOR_TEST = None
_SKIP_URL_BITS = ('argentina-vs-argelia',)


def set_tracked_assets(rows):
    """Tests inject Attribution IQ assets. None clears."""
    global _TRACKED_FOR_TEST
    _TRACKED_FOR_TEST = None if rows is None else list(rows or [])


def classify_tracked_url(url: str, channel: str = '',
                         asset_type: str = '') -> str:
    u = str(url or '').lower()
    ch = str(channel or '').lower()
    typ = str(asset_type or '').lower()
    if any(b in u for b in _SKIP_URL_BITS):
        return 'skip'
    if 'press' in ch or any(h in u for h in (
            'screenrant.com', 'nytimes.com', 'variety.com', 'rogerebert.com',
            'ign.com', 'dreadcentral.com', 'yahoo.com', 'imdb.com/news',
            'flickeringmyth.com', 'movieguide.org', 'horrorsociety.com',
            'horror-fix.com', 'dailydead.com', 'pophorror.com',
            'scifinow.co.uk', 'movievine.com', 'thathollywoodshow.com')):
        return 'editorial'
    if 'youtube.com/watch' in u or 'youtube.com/shorts' in u:
        return 'video'
    if '/video/' in u or 'instagram.com/p/' in u or 'instagram.com/reel/' in u:
        return 'creator_post'
    if 'tiktok.com/@' in u or 'instagram.com/' in u:
        return 'creator'
    if any(h in u for h in (
            'fandango.com/search', 'amctheatres.com/search',
            'regmovies.com/search', 'cinemark.com/search',
            'atomtickets.com/search')):
        return 'other'
    if any(h in u for h in (
            'fandango.com/', 'cinemark.com/movies/',
            'harkins.com/movies', 'gatewayfilmcenter.org/movies',
            'brendentheatres.com', 'theinfluencerprojectmovie.com')):
        return 'tickets'
    if 'review' in typ or 'press' in typ:
        return 'editorial'
    return 'other'


def normalize_tracked_assets(rows) -> list[dict]:
    out = []
    seen = set()
    for raw in rows or []:
        if isinstance(raw, (list, tuple)):
            url = str(raw[0] if raw else '')
            why = str(raw[1] if len(raw) > 1 else '')
            kind = str(raw[2] if len(raw) > 2 else '')
            row = {'url': url, 'title': why, 'kind': kind,
                   'channel': '', 'asset_type': '', 'views': 0}
        elif isinstance(raw, dict):
            row = {
                'url': str(raw.get('url') or ''),
                'title': str(raw.get('title') or raw.get('action_label')
                             or raw.get('asset_title') or ''),
                'channel': str(raw.get('channel') or ''),
                'asset_type': str(raw.get('asset_type') or ''),
                'views': int(raw.get('views') or raw.get('ext_view_count')
                             or raw.get('exposed_n') or 0),
                'kind': str(raw.get('kind') or ''),
            }
        else:
            continue
        url = row['url'].strip()
        key = url.rstrip('/').lower()
        if not url.startswith('https://') or key in seen:
            continue
        kind = row['kind'] or classify_tracked_url(
            url, row['channel'], row['asset_type'])
        if kind == 'skip':
            continue
        row['url'] = url
        row['kind'] = kind
        seen.add(key)
        out.append(row)
    return out


def pick_tracked_urls(tracked: list[dict], family: str, step_i: int = 1
                      ) -> list[tuple[str, str, float]]:
    """The campaign URLs that belong on this step, ranked by views."""
    rows = normalize_tracked_assets(tracked)
    if not rows:
        return []
    if family in ('tracked', 'creator_editorial'):
        want = ('video', 'creator_post', 'creator', 'editorial')
    elif family in ('creator',):
        want = ('creator_post', 'creator')
    elif family == 'editorial':
        want = ('editorial',)
    elif family == 'video':
        want = ('video', 'creator_post')
    elif family == 'tickets':
        want = ('tickets',)
    else:
        return []
    picked = [r for r in rows if r['kind'] in want]
    picked.sort(key=lambda r: (-int(r.get('views') or 0), r['url']))
    # Exposure shows a few of the campaign's own pages: the top
    # video, a creator post, and an editorial, then a couple more.
    # Never the full asset list.
    if family in ('tracked', 'creator_editorial'):
        mix = []
        used = set()
        for kind in ('video', 'creator_post', 'editorial'):
            for r in picked:
                if r['kind'] != kind or r['url'] in used:
                    continue
                mix.append(r)
                used.add(r['url'])
                break
        for r in picked:
            if r['url'] in used or r['kind'] == 'creator':
                continue
            mix.append(r)
            used.add(r['url'])
            if len(mix) >= TOP_TRACKED_URLS:
                break
        picked = mix
    out = []
    for r in picked[:TOP_TRACKED_URLS]:
        why = r.get('title') or {
            'video': 'Tracked video on this title',
            'creator_post': 'Tracked creator post',
            'creator': 'Tracked creator page',
            'editorial': 'Tracked editorial page',
            'tickets': 'Tracked ticketing page',
        }.get(r['kind'], 'Tracked page on this step')
        out.append((r['url'], why, 12.0))
    return _apply_hints(out, step_i)


def looks_search_not_asset(urls: list[tuple[str, str, float]]) -> bool:
    """Search / tag pages with none of the tracked posts or articles."""
    blob = ' '.join(u[0] for u in urls).lower()
    search = sum(1 for t in (
        'google.com/search', 'tiktok.com/search', 'tiktok.com/tag',
        'instagram.com/explore', 'youtube.com/results') if t in blob)
    asset = any(t in blob for t in (
        'youtube.com/watch', 'instagram.com/p/', 'instagram.com/reel/',
        '/video/', 'screenrant.com/', 'rogerebert.com/', 'variety.com/',
        'nytimes.com/'))
    return search >= 2 and not asset


def load_tracked_assets(subject: str, prim: Optional[dict] = None,
                        inputs: Optional[dict] = None,
                        payload: Optional[dict] = None) -> list[dict]:
    """Attribution IQ assets already on this subject, if we hold them."""
    if _TRACKED_FOR_TEST is not None:
        return normalize_tracked_assets(_TRACKED_FOR_TEST)
    for src in (prim, inputs, payload,
                (payload or {}).get('fragrance_shop_journey'),
                (payload or {}).get('clickstream')):
        if not isinstance(src, dict):
            continue
        rows = src.get('tracked_assets')
        if not rows and isinstance(src.get('clickstream'), dict):
            rows = src['clickstream'].get('tracked_assets')
        if rows:
            return normalize_tracked_assets(rows)
    try:
        from migration.journey_synthesis import corpus_anchors
        camp = (corpus_anchors({'subject': subject}) or {}).get('attribution')
        return normalize_tracked_assets((camp or {}).get('assets') or [])
    except Exception:
        return []


def looks_generic_bag(urls: list[tuple[str, str, float]]) -> bool:
    """The old builder stamped google+youtube+tiktok+reddit on every step."""
    blob = ' '.join(u[0] for u in urls).lower()
    if 'reddit.com/search' not in blob:
        return False
    hits = sum(1 for t in (
        'google.com/search', 'youtube.com/results', 'tiktok.com/search',
        'reddit.com/search', 'imdb.com/find') if t in blob)
    return hits >= 4


def _apply_hints(urls: list[tuple[str, str, float]], step_i: int
                 ) -> list[tuple[str, str, float]]:
    band = _HINT_BANDS[(max(step_i, 1) - 1) % len(_HINT_BANDS)]
    out = []
    for n, (url, why, _old) in enumerate(urls[:MAX_URLS]):
        out.append((url, why, band[n] if n < len(band) else 3.1847))
    return out


def looks_invented_theater(urls: list) -> bool:
    blob = ' '.join(
        (u[0] if isinstance(u, (list, tuple)) else str(u.get('url') or ''))
        for u in (urls or [])).lower()
    return any(x in blob for x in (
        'cinemark.com/search', 'amctheatres.com/search',
        'fandango.com/search', 'regmovies.com/search',
        'atomtickets.com/search'))


def surface_host_lock(surface: str, action: str = '') -> str:
    """A TikTok-only step stays on TikTok. Instagram-only stays on Instagram.

    Mixed surfaces (Creator feeds, TikTok and Instagram) stay unlocked.
    """
    surf = str(surface or '').lower().strip()
    act = str(action or '').lower()
    if any(sep in surf for sep in (',', ' and ', '/', '+', '·')):
        return ''
    blob = f'{surf} {act}'
    if 'tiktok' in blob and 'instagram' not in blob:
        return 'tiktok.com'
    if 'instagram' in blob and 'tiktok' not in blob:
        return 'instagram.com'
    return ''


def urls_for_step(subject: str, platform: str, surface: str = '',
                  action: str = '', step_i: int = 1,
                  tracked: Optional[list] = None
                  ) -> list[tuple[str, str, float]]:
    """6 to 10 public pages that belong to THIS step only.

    Music to Long Form method: researched destination pages first.
    Attribution IQ tracked URLs win on exposure / creator / editorial
    / video. Invented theater search paths never ship.
    """
    family = step_family(surface, action, subject=subject, platform=platform)
    catalog = researched_catalog(subject, platform)
    lock = surface_host_lock(surface, action)
    named_ok = {u[0].rstrip('/').lower()
                for u in researched_extras_for(subject, platform)}
    named_ok |= {r['url'].rstrip('/').lower()
                 for r in normalize_tracked_assets(tracked or [])}
    rows: list[tuple[str, str]] = []

    def add(url: str, why: str) -> None:
        if looks_invented_theater([(url, why)]):
            return
        if lock and lock not in str(url or '').lower():
            return
        allow = url.rstrip('/').lower() in named_ok
        if is_safe_url(url, allow_named_clip=allow) and all(
                u[0].rstrip('/').lower() != url.rstrip('/').lower()
                for u in rows):
            rows.append((url, why))

    for url, why, _hint in pick_tracked_urls(tracked or [], family, step_i):
        add(url, why)
    want_fams = {
        'tracked': ('tracked', 'video', 'creator', 'editorial'),
        'creator_editorial': ('creator', 'editorial', 'video'),
        'creator': ('creator',),
        'editorial': ('editorial',),
        'video': ('video',),
        'tickets': ('tickets',),
        'title': ('title',),
        'search': ('search',),
        'amazon': ('amazon',),
        'netflix': ('netflix',),
        'shop': ('shop',),
    }.get(family, (family,))
    for fam in want_fams:
        for url, why, _kind in catalog.get(fam, []):
            add(url, why)
    if family == 'tracked':
        return _apply_hints(
            [(u, w, 12.0) for u, w in rows[:TOP_TRACKED_URLS]], step_i)
    fill_from = {
        'creator': ('video', 'title'),
        'editorial': ('title',),
        'video': ('creator', 'title'),
        'tickets': ('title',),
        'title': ('tickets', 'video'),
        'search': ('title', 'tickets', 'video', 'amazon', 'netflix'),
        'amazon': ('title', 'video'),
        'netflix': ('title', 'video'),
        'shop': ('creator',),
        'creator_editorial': ('video', 'title'),
    }.get(family, ('title',))
    if len(rows) < MIN_URLS:
        for fam in fill_from:
            if lock:
                break
            for url, why, _kind in catalog.get(fam, []):
                add(url, why)
                if len(rows) >= MIN_URLS:
                    break
            if len(rows) >= MIN_URLS:
                break
    if lock and len(rows) < MIN_URLS:
        for fam_rows in catalog.values():
            for url, why, _kind in fam_rows:
                add(url, why)
                if len(rows) >= MIN_URLS:
                    break
            if len(rows) >= MIN_URLS:
                break
    has_catalog = any(catalog.values())
    if lock and len(rows) < MIN_URLS:
        q = _q(str(subject or '').strip() or 'the title')
        tag = re.sub(r'[^a-z0-9]+', '', str(subject or '').lower())[:32] or 'fyp'
        if lock == 'tiktok.com':
            for url, why in (
                (f'https://www.tiktok.com/search?q={q}', 'On-app search'),
                (f'https://www.tiktok.com/tag/{tag}', 'On-app tag'),
                (f'https://www.tiktok.com/search?q={q}+clip',
                 'Clip search on the app'),
                (f'https://www.tiktok.com/search?q={q}+review',
                 'Review search on the app'),
                ('https://www.tiktok.com/explore', 'Opened Explore'),
                ('https://www.tiktok.com/discover', 'Opened Discover'),
            ):
                add(url, why)
        elif lock == 'instagram.com':
            for url, why in (
                (f'https://www.instagram.com/explore/search/keyword/?q={q}',
                 'On-app search'),
                (f'https://www.instagram.com/explore/tags/{tag}/',
                 'On-app tag'),
                (f'https://www.instagram.com/explore/search/keyword/?q={q}+reel',
                 'Reel search on the app'),
                (f'https://www.instagram.com/explore/search/keyword/?q={q}+review',
                 'Review search on the app'),
                ('https://www.instagram.com/explore/', 'Opened Explore'),
                ('https://www.instagram.com/reels/', 'Opened Reels'),
            ):
                add(url, why)
    elif not has_catalog and len(rows) < MIN_URLS:
        subj = str(subject or '').strip() or 'the title'
        q = _q(subj)
        add(f'https://www.google.com/search?q={q}',
            'Typed search for the subject')
        add(f'https://www.youtube.com/results?search_query={q}',
            'Video results for the subject')
        add(f'https://www.tiktok.com/search?q={q}',
            'Short-form search')
        add(f'https://www.instagram.com/explore/search/keyword/?q={q}',
            'Instagram name search')
        add(f'https://www.reddit.com/search/?q={q}',
            'Forum search for the same name')
        add(f'https://www.bing.com/search?q={q}',
            'Second typed search')
        if family == 'amazon':
            add(f'https://www.amazon.com/s?k={q}', 'Amazon listing search')
            add(f'https://www.amazon.com/gp/video/search?phrase={q}',
                'Prime Video title search')
        if family == 'netflix':
            add(f'https://www.netflix.com/search?q={q}', 'Netflix title search')
        if family == 'title':
            add(f'https://www.imdb.com/find/?q={q}', 'Title page search')
        n = 0
        while len(rows) < MIN_URLS and n < 6:
            add(f'https://www.google.com/search?q={_q(subj + " " + family + " " + str(n + 1))}',
                'More pages on this step')
            n += 1
    elif family == 'search' and len(rows) < MIN_URLS:
        add(f'https://www.google.com/search?q={_q(subject)}',
            'Typed search for the subject')
    hinted = [(u, w, 12.0) for u, w in rows[:MAX_URLS]]
    rot = (max(step_i, 1) - 1) % max(len(hinted), 1)
    if hinted and rot and family != 'tracked':
        hinted = hinted[rot:] + hinted[:rot]
    return _apply_hints(hinted[:MAX_URLS], step_i)


def _pad_urls(urls: list[tuple[str, str, float]],
              subject: str, platform: str, surface: str,
              action: str = '', step_i: int = 1,
              tracked: Optional[list] = None
              ) -> list[tuple[str, str, float]]:
    family_rows = urls_for_step(
        subject, platform, surface, action, step_i, tracked=tracked)
    if (looks_generic_bag(urls) or looks_search_not_asset(urls)
            or looks_invented_theater(urls) or not urls):
        return family_rows
    if len(urls) >= MIN_URLS:
        return _apply_hints(urls[:MAX_URLS], step_i)
    have = {u[0].rstrip('/').lower() for u in urls}
    for url, why, hint in family_rows:
        key = url.rstrip('/').lower()
        if key in have:
            continue
        urls.append((url, why, hint))
        have.add(key)
        if len(urls) >= MIN_URLS:
            break
    return _apply_hints(urls[:MAX_URLS], step_i)


def _step_from_raw(raw: dict, i: int, prev_people: Optional[int],
                   seed: str, subject: str, platform: str,
                   named_clips: Optional[Iterable[str]],
                   tracked: Optional[list] = None) -> Optional[dict]:
    people = int(raw.get('people') or raw.get('accounts') or 0)
    if people <= 0:
        return None
    if prev_people is not None and people >= prev_people:
        people = messy_people((seed, 'dec', i), int(prev_people * 0.71))
        people = min(people, prev_people - 1)
    people = messy_people((seed, 'step', i, raw.get('action') or raw.get('label')),
                          people)
    if prev_people is not None:
        people = min(people, prev_people - 1)
    date = str(raw.get('date') or raw.get('step_date') or '')
    if not _DATE.match(date):
        date = ''
    surface = str(raw.get('surface') or raw.get('where') or platform or 'Search')
    action = str(raw.get('action') or raw.get('label') or raw.get('doing')
                 or 'Opened the next page')
    named_clips = list(named_clips or [])
    named_clips.extend(r['url'] for r in normalize_tracked_assets(tracked or []))
    raw_urls = []
    for u in (raw.get('urls') or []):
        if isinstance(u, dict):
            url = str(u.get('url') or '')
            why = str(u.get('why') or u.get('why_this_url') or 'Page on this step')
            hint = float(u.get('share_of_step_pct') or u.get('hint') or 12.0)
        elif isinstance(u, (list, tuple)) and u:
            url = str(u[0])
            why = str(u[1] if len(u) > 1 else 'Page on this step')
            hint = float(u[2] if len(u) > 2 else 12.0)
        else:
            continue
        if _clip_ok(url, named_clips):
            raw_urls.append((url, why, hint))
    raw_urls = _pad_urls(
        raw_urls, subject, platform, surface, action, i, tracked=tracked)
    return {
        'step': i,
        'date': date,
        'surface': surface,
        'action': action,
        'people': people,
        'urls': split_urls(f'{seed}|{i}', people, raw_urls),
    }


def fallback_clickstream(spine: list[dict], subject: str, platform: str,
                         seed: str, window: str = '',
                         detours: Optional[list[dict]] = None,
                         extra_urls: Optional[list[tuple[str, str]]] = None,
                         named_clips: Optional[Iterable[str]] = None,
                         tracked: Optional[list] = None
                         ) -> dict:
    """Build a clickstream from the spine when research is thin."""
    if tracked is None:
        tracked = load_tracked_assets(subject)
    dates = _window_dates(window)
    start, end = dates[0], dates[-1]
    steps_src = _spine_steps(spine)
    extra = list(extra_urls or [])
    for d in (detours or []):
        for r in (d.get('rows') or [])[:4]:
            lab = str(r.get('label') or '')
            if lab:
                extra.append((
                    f'https://www.google.com/search?q={_q(lab + " " + subject)}',
                    f'{lab} on this step'))
    # Need 8-13 steps. Repeat timing across the window if the nest is short.
    if len(steps_src) < MIN_STEPS:
        fillers = [
            {'label': f'Searched {subject}', 'surface': 'Search',
             'doing': f'Typed {subject}'},
            {'label': 'Opened a short-form clip', 'surface': 'TikTok',
             'doing': 'Watched a short clip'},
            {'label': 'Opened Instagram', 'surface': 'Instagram',
             'doing': 'Opened the app'},
            {'label': 'Opened a title page', 'surface': 'IMDb',
             'doing': 'Opened the title page'},
            {'label': 'Came back the next day', 'surface': 'Return',
             'doing': 'Returned'},
            {'label': 'Compared a second page', 'surface': 'Search',
             'doing': 'Opened a second result'},
        ]
        for f in fillers:
            if len(steps_src) >= MIN_STEPS:
                break
            steps_src.append(f)
    steps_src = steps_src[:MAX_STEPS]
    out = []
    prev = None
    n = max(len(steps_src) - 1, 1)
    for i, src in enumerate(steps_src, start=1):
        # Spread dates from window start to end.
        if i == 1:
            date = start
        elif i == len(steps_src):
            date = end
        else:
            date = start if i <= n // 2 else end
        raw = {
            'people': src.get('accounts') or src.get('people') or (
                int(prev * 0.68) if prev else 1_000_000),
            'date': src.get('date') or date,
            'surface': src.get('surface') or src.get('where') or 'Search',
            'action': src.get('action') or src.get('label') or src.get('doing'),
            'urls': [],
        }
        raw['urls'] = []
        step = _step_from_raw(
            raw, i, prev, seed, subject, platform, named_clips, tracked)
        if not step:
            continue
        out.append(step)
        prev = step['people']
    # Guarantee decrease and length.
    for i in range(1, len(out)):
        if out[i]['people'] >= out[i - 1]['people']:
            out[i]['people'] = messy_people(
                (seed, 'fixdec', i), int(out[i - 1]['people'] * 0.67))
            out[i]['people'] = min(out[i]['people'], out[i - 1]['people'] - 1)
            out[i]['urls'] = split_urls(
                f'{seed}|{i+1}', out[i]['people'],
                [(u['url'], u['why'], u['share_of_step_pct'])
                 for u in out[i]['urls']])
    return {'steps': out}


def normalize_clickstream(raw: Any, spine: list[dict], subject: str,
                          platform: str, seed: str, window: str = '',
                          detours: Optional[list[dict]] = None,
                          named_clips: Optional[Iterable[str]] = None,
                          tracked: Optional[list] = None
                          ) -> dict:
    """Accept research JSON or a list of steps. Fail-safe to spine."""
    named = list(named_clips or [])
    tracked = list(tracked or [])
    named.extend(r['url'] for r in normalize_tracked_assets(tracked))
    steps_in = []
    if isinstance(raw, dict):
        steps_in = list(raw.get('steps') or [])
    elif isinstance(raw, list):
        steps_in = list(raw)
    out = []
    prev = None
    for i, src in enumerate(steps_in[:MAX_STEPS], start=1):
        if not isinstance(src, dict):
            continue
        step = _step_from_raw(
            src, i, prev, seed, subject, platform, named, tracked)
        if not step:
            continue
        out.append(step)
        prev = step['people']
    if len(out) < MIN_STEPS:
        return fallback_clickstream(
            spine, subject, platform, seed, window, detours,
            named_clips=named, tracked=tracked)
    result = {'steps': out}
    if clickstream_urls_repeat(result) or _needs_tracked_rewrite(result, tracked):
        return _rewrite_step_urls(
            result, subject, platform, seed, named, tracked)
    return result


def _needs_tracked_rewrite(cs: dict, tracked: list) -> bool:
    if not tracked:
        return False
    for s in cs.get('steps') or []:
        fam = step_family(s.get('surface') or '', s.get('action') or '')
        if fam not in ('tracked', 'creator', 'editorial',
                       'creator_editorial', 'video'):
            continue
        urls = [(u.get('url') or '', '', 1.0) for u in (s.get('urls') or [])]
        if looks_search_not_asset(urls):
            return True
    return False


def clickstream_urls_repeat(cs: dict) -> bool:
    """Same URL list or same lead share on 3+ steps."""
    steps = list((cs or {}).get('steps') or [])
    if len(steps) < 3:
        return False
    keys, shares = [], []
    for s in steps:
        urls = [str(u.get('url') or '').rstrip('/').lower()
                for u in (s.get('urls') or [])]
        keys.append(tuple(urls[:4]))
        if urls:
            shares.append(round(float(
                (s.get('urls') or [{}])[0].get('share_of_step_pct') or 0), 1))
    if keys and len(set(keys)) <= 2:
        return True
    if shares and len(set(shares)) <= 2:
        return True
    return False


def _rewrite_step_urls(cs: dict, subject: str, platform: str, seed: str,
                       named_clips=None, tracked=None) -> dict:
    prev = None
    out = []
    for i, s in enumerate(cs.get('steps') or [], start=1):
        raw = {
            'people': s.get('people'),
            'date': s.get('date'),
            'surface': s.get('surface'),
            'action': s.get('action'),
            'urls': [],
        }
        step = _step_from_raw(
            raw, i, prev, seed, subject, platform, named_clips, tracked)
        if not step:
            continue
        out.append(step)
        prev = step['people']
    return {'steps': out}


def attach_clickstream(payload: dict, prim: Optional[dict] = None,
                       inputs: Optional[dict] = None) -> dict:
    """Put clickstream on the payload and on the nest blob. Always."""
    inputs = inputs or {}
    prim = prim or {}
    blob = (payload.get('fragrance_shop_journey')
            or payload.get('arrow_pluto')
            or payload.get('politics_girl_reel')
            or {})
    spine = blob.get('spine') or payload.get('spine') or []
    detours = blob.get('detours') or payload.get('detours') or []
    meta = payload.get('meta') or blob.get('meta') or {}
    subject = (str(inputs.get('subject') or '')
               or str(meta.get('target_name') or meta.get('subject')
                      or meta.get('project_name') or 'this journey'))
    platform = (str(inputs.get('platform') or '')
                or str(meta.get('platform') or ''))
    window = str((blob.get('meta') or {}).get('window')
                 or meta.get('window')
                 or f"{meta.get('start_date') or ''} to {meta.get('end_date') or ''}")
    seed = f'{subject}|{platform}|clickstream'
    named = [inputs.get('clip_url'),
             (payload.get('meta') or {}).get('clip_url')]
    named = [u for u in named if u]
    tracked = load_tracked_assets(subject, prim, inputs, payload)
    named.extend(r['url'] for r in tracked)
    raw = (prim.get('clickstream')
           or blob.get('clickstream')
           or payload.get('clickstream'))
    cs = normalize_clickstream(
        raw, spine, subject, platform, seed, window, detours, named,
        tracked=tracked)
    if tracked:
        cs['tracked_assets'] = tracked
    payload['clickstream'] = cs
    if 'fragrance_shop_journey' in payload:
        payload['fragrance_shop_journey']['clickstream'] = cs
    return payload


def from_csv_rows(rows: Iterable[dict], subject: str, platform: str,
                  seed: str) -> dict:
    """Arrow-style flat CSV rows -> clickstream.steps."""
    grouped: dict[tuple, dict] = {}
    order = []
    for r in rows:
        key = (str(r.get('journey') or ''),
               int(r.get('step') or 0),
               str(r.get('step_action') or r.get('action') or ''))
        if key not in grouped:
            grouped[key] = {
                'people': int(r.get('step_people') or r.get('people') or 0),
                'date': str(r.get('step_date') or r.get('date') or ''),
                'surface': str(r.get('surface') or ''),
                'action': str(r.get('step_action') or r.get('action') or ''),
                'urls': [],
            }
            order.append(key)
        grouped[key]['urls'].append({
            'url': r.get('url'),
            'why': r.get('why_this_url') or r.get('why'),
            'share_of_step_pct': float(r.get('share_of_step_pct') or 12),
        })
    raw_steps = [grouped[k] for k in order]
    return normalize_clickstream(
        {'steps': raw_steps}, [], subject, platform, seed)


def politics_girl_clickstream() -> dict:
    """Seed for the June 9 reel nest. Real URLs only."""
    seed = 'Politics Girl|Instagram|clickstream'
    clip = 'https://www.instagram.com/p/DZYmGm6DSf3/'
    reel = 'https://www.instagram.com/reel/DZYmGm6DSf3/'
    yt = 'https://www.youtube.com/shorts/qQNc41IbfZ0'
    named = [clip, reel, yt]
    raw = {'steps': [
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Was already in the following feed',
         'people': 928847,
         'urls': [
             {'url': 'https://www.instagram.com/', 'why': 'Opened Instagram',
              'hint': 61.4},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Official account in the following stack', 'hint': 28.6},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Opened a suggested or Reels row',
         'people': 597641,
         'urls': [
             {'url': 'https://www.instagram.com/reels/',
              'why': 'Reels shelf', 'hint': 54.2},
             {'url': 'https://www.instagram.com/explore/',
              'why': 'Explore next to Reels', 'hint': 22.1},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Opened this June 9 reel',
         'people': 2417863,
         'urls': [
             {'url': clip, 'why': 'The /p/ URL for this reel', 'hint': 71.4},
             {'url': reel, 'why': 'The /reel/ twin of the same post',
              'hint': 48.2},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Account page sitting next to the reel', 'hint': 18.6},
         ]},
        {'date': '2026-06-09', 'surface': 'YouTube',
         'action': 'Opened the YouTube Shorts twin',
         'people': 1184271,
         'urls': [
             {'url': yt, 'why': 'Same June 9 clip on Shorts', 'hint': 62.8},
             {'url': 'https://www.youtube.com/results?search_query=politics+girl',
              'why': 'Name search after the Short', 'hint': 21.4},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Watched again in the next 20 minutes',
         'people': 769841,
         'urls': [
             {'url': clip, 'why': 'Replay on the same URL', 'hint': 58.3},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Hop to the profile after a replay', 'hint': 19.7},
         ]},
        {'date': '2026-06-10', 'surface': 'Search',
         'action': 'Googled Politics Girl or Leigh McGowan',
         'people': 184271,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl',
              'why': 'Handle search', 'hint': 44.2},
             {'url': 'https://www.google.com/search?q=leigh+mcgowan',
              'why': 'Talent-name search', 'hint': 31.8},
         ]},
        {'date': '2026-06-12', 'surface': 'Search',
         'action': 'Opened a research page in 14 days',
         'people': 41847,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl+leigh+mcgowan',
              'why': 'Full-name research', 'hint': 38.4},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Back to the account', 'hint': 22.1},
         ]},
        {'date': '2026-06-14', 'surface': 'Web',
         'action': 'Opened an action page',
         'people': 7841,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl+action',
              'why': 'Action-page hunt', 'hint': 41.7},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Link in bio after research', 'hint': 28.3},
         ]},
        {'date': '2026-10-06', 'surface': 'Instagram',
         'action': 'Still in the Politics Girl stack later in the window',
         'people': 342963,
         'urls': [
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Later visit to the account', 'hint': 51.2},
             {'url': clip, 'why': 'The June 9 reel still in the grid',
              'hint': 18.4},
         ]},
    ]}
    return normalize_clickstream(
        raw, [], 'Politics Girl', 'Instagram', seed,
        '2026-06-09 to 2026-10-06', named_clips=named)
