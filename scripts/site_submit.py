#!/usr/bin/env python3
"""Tell the search engines about the public site (www.crosswalknyc.com).

Run after a deploy, once the pages are live:

    python3 bg-webapp/scripts/site_submit.py            # everything
    python3 bg-webapp/scripts/site_submit.py --indexnow  # Bing, Yandex, Naver, Seznam, Yep
    python3 bg-webapp/scripts/site_submit.py --google    # Search Console: verify, add, submit sitemap
    python3 bg-webapp/scripts/site_submit.py --status    # Google: index status per URL (URL Inspection)
    python3 bg-webapp/scripts/site_submit.py --urls https://www.crosswalknyc.com/digital-twins.html ...

What it does

  IndexNow. One POST to api.indexnow.org with every URL in the live
  sitemap. The key is the file name of site/<key>.txt, which the
  engines fetch from the site root to prove we own it. Shared by
  Bing, Yandex, Naver, Seznam and Yep; Bing also feeds DuckDuckGo
  and Ecosia. No account needed. 200 or 202 means accepted.

  Google. Google retired the sitemap ping, so the only submission
  path is Search Console. This script uses the Google Cloud service
  account at ~/.crosswalk/google_sheets_credentials.json (or the
  GOOGLE_SITE_CREDENTIALS path): it asks the Site Verification API
  for a META token, which site_seo.py writes on the homepage as
  <meta name="google-site-verification">, confirms the verification
  once that is live, adds jenna@ as a verified owner so the property
  appears in her Search Console, adds the site to Search Console and
  submits the sitemap. Two APIs must be enabled on the project first
  (one click each in the Cloud console; the script prints the links
  when they are not):
      siteverification.googleapis.com
      searchconsole.googleapis.com

Nothing here edits a page. The verification token is stored at
site/.google-site-verification (committed) and site_seo.py puts it on
the homepage. No em dashes. Never prints a credential.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).resolve()
BG = HERE.parents[1]
SITE = BG / "site"
ORIGIN = "https://www.crosswalknyc.com"
HOST = "www.crosswalknyc.com"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
TOKEN_FILE = SITE / ".google-site-verification"
OWNERS_TO_ADD = ("jenna@crosswalknyc.com",)
CREDS_ENV = "GOOGLE_SITE_CREDENTIALS"
CREDS_DEFAULT = Path.home() / ".crosswalk" / "google_sheets_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/siteverification",
          "https://www.googleapis.com/auth/webmasters"]
ENABLE_LINKS = {
    "siteverification": "https://console.developers.google.com/apis/api/siteverification.googleapis.com/overview?project=617755165484",
    "searchconsole": "https://console.developers.google.com/apis/api/searchconsole.googleapis.com/overview?project=617755165484",
}


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def sitemap_urls(live: bool = True) -> list[str]:
    """URLs from the live sitemap (or the local file when the site is not up)."""
    text = ""
    if live:
        try:
            r = requests.get(ORIGIN + "/sitemap.xml", timeout=30)
            if r.ok and "<urlset" in r.text:
                text = r.text
        except requests.RequestException:
            text = ""
    if not text:
        text = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    return re.findall(r"<loc>(.*?)</loc>", text)


# ---------------------------------------------------------------------------
# IndexNow
# ---------------------------------------------------------------------------

def indexnow_key() -> str:
    keys = [p for p in SITE.glob("*.txt") if p.name != "robots.txt" and re.fullmatch(r"[a-f0-9]{32}\.txt", p.name)]
    if not keys:
        raise SystemExit("no IndexNow key file under site/ (expected <32 hex chars>.txt holding the same key)")
    key = keys[0].stem
    body = keys[0].read_text(encoding="utf-8").strip()
    if body != key:
        raise SystemExit(f"IndexNow key file {keys[0].name} must contain exactly its own name without .txt")
    return key


def submit_indexnow(urls: list[str]) -> int:
    key = indexnow_key()
    # The engines fetch the key file from the root; say so if it is not live yet.
    try:
        chk = requests.get(f"{ORIGIN}/{key}.txt", timeout=30)
        if not chk.ok or chk.text.strip() != key:
            print(f"[indexnow] key file not live yet at {ORIGIN}/{key}.txt (HTTP {chk.status_code}); deploy first")
            return 1
    except requests.RequestException as e:
        print(f"[indexnow] could not fetch the key file: {e}")
        return 1
    payload = {"host": HOST, "key": key, "keyLocation": f"{ORIGIN}/{key}.txt", "urlList": urls}
    r = requests.post(INDEXNOW_ENDPOINT, json=payload, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=60)
    ok = r.status_code in (200, 202)
    print(f"[indexnow] {len(urls)} URLs -> HTTP {r.status_code} {'accepted' if ok else 'rejected'}"
          + (f": {r.text.strip()[:200]}" if r.text.strip() and not ok else ""))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

def _google_session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    path = Path(os.environ.get(CREDS_ENV) or CREDS_DEFAULT)
    if not path.exists():
        raise SystemExit(f"no Google service account file at {path}")
    creds = service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)
    return AuthorizedSession(creds), json.loads(path.read_text()).get("client_email", "the service account")


def _api_blocked(resp) -> str | None:
    """Name of the API that must be enabled, or None."""
    if resp.status_code != 403:
        return None
    msg = resp.text
    if "siteverification.googleapis.com" in msg or "Site Verification API" in msg:
        return "siteverification"
    if "searchconsole.googleapis.com" in msg or "Search Console API" in msg or "webmasters" in msg:
        return "searchconsole"
    return None


def google_token(session) -> str | None:
    """Fetch (and store) the META verification token for the site."""
    r = session.post("https://www.googleapis.com/siteVerification/v1/token", json={
        "site": {"type": "SITE", "identifier": ORIGIN + "/"}, "verificationMethod": "META"}, timeout=30)
    blocked = _api_blocked(r)
    if blocked:
        print(f"[google] the {blocked} API is not enabled on the project. Enable it here, then rerun:\n         {ENABLE_LINKS[blocked]}")
        return None
    r.raise_for_status()
    tag = r.json().get("token", "")
    m = re.search(r'content="([^"]+)"', tag)
    token = m.group(1) if m else tag
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    print(f"[google] verification token stored at site/{TOKEN_FILE.name}; run scripts/site_seo.py and deploy so it is on the homepage")
    return token


def homepage_has_token(token: str) -> bool:
    try:
        r = requests.get(ORIGIN + "/", timeout=30)
        return token in r.text
    except requests.RequestException:
        return False


def google_verify_and_submit(session, actor: str) -> int:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else None
    if not token:
        token = google_token(session)
        if not token:
            return 1
        return 3  # token minted; needs a deploy before verification can pass
    if not homepage_has_token(token):
        print("[google] the verification tag is not on the live homepage yet; run scripts/site_seo.py, deploy, rerun")
        return 3
    r = session.post("https://www.googleapis.com/siteVerification/v1/webResource?verificationMethod=META",
                     json={"site": {"type": "SITE", "identifier": ORIGIN + "/"}}, timeout=60)
    blocked = _api_blocked(r)
    if blocked:
        print(f"[google] enable {ENABLE_LINKS[blocked]} then rerun")
        return 1
    if not r.ok:
        print(f"[google] verification failed: HTTP {r.status_code} {r.text[:300]}")
        return 1
    res = r.json()
    res_id = res.get("id")
    owners = sorted(set(res.get("owners", [])) | set(OWNERS_TO_ADD))
    if res_id and set(owners) != set(res.get("owners", [])):
        r2 = session.put(f"https://www.googleapis.com/siteVerification/v1/webResource/{urllib.parse.quote(res_id, safe='')}",
                         json={"id": res_id, "site": res.get("site"), "owners": owners}, timeout=60)
        print(f"[google] owners now: {', '.join(owners)}" if r2.ok else f"[google] could not add owners: HTTP {r2.status_code}")
    print(f"[google] {ORIGIN}/ verified for {actor}")
    enc = urllib.parse.quote(ORIGIN + "/", safe="")
    r3 = session.put(f"https://www.googleapis.com/webmasters/v3/sites/{enc}", timeout=60)
    blocked = _api_blocked(r3)
    if blocked:
        print(f"[google] enable {ENABLE_LINKS[blocked]} then rerun")
        return 1
    print(f"[google] Search Console property added (HTTP {r3.status_code})")
    sm = urllib.parse.quote(ORIGIN + "/sitemap.xml", safe="")
    r4 = session.put(f"https://www.googleapis.com/webmasters/v3/sites/{enc}/sitemaps/{sm}", timeout=60)
    print(f"[google] sitemap submitted (HTTP {r4.status_code})" if r4.ok else f"[google] sitemap submit failed: HTTP {r4.status_code} {r4.text[:200]}")
    return 0 if r4.ok else 1


def google_status(session, urls: list[str]) -> int:
    """Index status per URL from the URL Inspection API (verified property only)."""
    n_ok = 0
    for u in urls:
        r = session.post("https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                         json={"inspectionUrl": u, "siteUrl": ORIGIN + "/"}, timeout=60)
        if _api_blocked(r):
            print(f"[google] enable {ENABLE_LINKS['searchconsole']} then rerun")
            return 1
        if not r.ok:
            print(f"[google] {u}: HTTP {r.status_code} {r.text[:120]}")
            continue
        st = r.json().get("inspectionResult", {}).get("indexStatusResult", {})
        print(f"[google] {u}: {st.get('verdict', '?')}, {st.get('coverageState', '?')}, last crawl {st.get('lastCrawlTime', 'never')}")
        n_ok += 1
    return 0 if n_ok else 1


# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    want_indexnow = "--indexnow" in argv or not any(a in argv for a in ("--google", "--status"))
    want_google = "--google" in argv or not any(a in argv for a in ("--indexnow", "--status"))
    want_status = "--status" in argv
    urls = []
    if "--urls" in argv:
        urls = [a for a in argv[argv.index("--urls") + 1:] if a.startswith("http")]
    urls = urls or sitemap_urls()
    rc = 0
    if want_indexnow:
        rc |= submit_indexnow(urls)
    if want_google or want_status:
        try:
            session, actor = _google_session()
        except Exception as e:  # missing library or file
            print(f"[google] skipped: {e}")
            return rc | 1
        if want_google:
            rc |= google_verify_and_submit(session, actor)
        if want_status:
            rc |= google_status(session, urls)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
