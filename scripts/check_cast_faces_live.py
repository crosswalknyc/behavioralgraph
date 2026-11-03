#!/usr/bin/env python3
"""Live check of the cast face read (network + AWS; run on the build
server or anywhere with the dashboard's AWS credentials).

    python3 scripts/check_cast_faces_live.py "NCIS: New York Pilot" "LL Cool J" "Scott Cahn" "Jennifer Beals"

Resolves the names (one model call when a model is available), fetches
the headshots, probes the face index, then reads a few public photos
the way a frame is read and prints who it found. Nothing is written
except the headshot cache under system/digital_twins/cast_faces/.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import digital_twin_cast as C   # noqa: E402

UA = {'User-Agent': 'Crosswalk-PilotTesting/1.0 (jenna@crosswalknyc.com)'}


def commons_photos(query: str, n: int = 4) -> list:
    """A few Commons file URLs for a search, at a size a frame would be."""
    q = urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'srnamespace': 6, 'srlimit': n * 2, 'format': 'json'})
    hits = json.load(urllib.request.urlopen(urllib.request.Request('https://commons.wikimedia.org/w/api.php?' + q, headers=UA), timeout=15))['query']['search']
    out = []
    for h in hits:
        title = h['title']
        if not title.lower().endswith(('.jpg', '.jpeg')):
            continue
        q2 = urllib.parse.urlencode({'action': 'query', 'titles': title, 'prop': 'imageinfo', 'iiprop': 'url|size', 'iiurlwidth': 800, 'format': 'json'})
        info = json.load(urllib.request.urlopen(urllib.request.Request('https://commons.wikimedia.org/w/api.php?' + q2, headers=UA), timeout=15))
        page = list(info['query']['pages'].values())[0]
        ii = (page.get('imageinfo') or [{}])[0]
        url = ii.get('thumburl') if int(ii.get('width') or 0) > 800 else ii.get('url')
        if url:
            out.append((title, url))
        if len(out) >= n:
            break
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    title, cast = sys.argv[1], sys.argv[2:]
    sess = {'id': 'check', 'cast': cast}
    t0 = time.time()
    people = C.people_for(sess, title=title)
    print(f"resolved in {time.time() - t0:.1f}s:")
    for p in people:
        print(f"  {p['entered']!r:22} -> {p['actor']!r:20} character={p.get('character')!r:14} headshot={'yes' if p.get('has_headshot') else 'no':3} index={p.get('celebrity') or '-'}")
    for p in people:
        for name, url in commons_photos(p['actor'], 3):
            try:
                raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
            except Exception as e:
                print(f"  skip {name[:40]}: {e}")
                continue
            t1 = time.time()
            hits = C.faces_in_frame(raw, people)
            print(f"  photo {name[:48]!r:50} -> {[(h['actor'], h['confidence'], h['how'], h['pos']) for h in hits]}  ({time.time() - t1:.1f}s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
