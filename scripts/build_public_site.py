#!/usr/bin/env python3
"""Build the public site served at dashboard.crosswalknyc.com/site.

Source of truth for the page designs is website/mockups (direction 5,
Ask-led). This script copies the approved pages into bg-webapp/site,
rewrites asset paths, and writes the two live pages that differ from
the mockups: signup.html (posts to /site/api/signup and hands off to
Stripe Checkout) and welcome.html (polls /site/api/signup/status).

Legal and panel-consent pages come across from website/ unchanged so
the footer links resolve.

Run:  python3 bg-webapp/scripts/build_public_site.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve()
BG = HERE.parents[1]                       # bg-webapp
ROOT = BG.parent                           # finished_codes
MOCK = ROOT / "website" / "mockups"
WEB = ROOT / "website"
OUT = BG / "site"

PAGES = {
    "05-ask.html": "index.html",
    "products.html": "products.html",
    "profile-iq.html": "profile-iq.html",
    "subscriber-iq.html": "subscriber-iq.html",
    "digital-journey-iq.html": "digital-journey-iq.html",
    "trends-iq.html": "trends-iq.html",
    "ranker-iq.html": "ranker-iq.html",
    "fin-iq.html": "fin-iq.html",
    "panel.html": "panel.html",
    "company.html": "company.html",
}
LEGAL = [
    "privacy-policy-1.html", "do-not-sell-my-personal-information.html",
    "optout.html", "terms-of-use.html", "tracking-transparency.html",
    "coppa.html", "deidentified-data.html", "why-we-track.html",
    "app.html", "parental-consent.html",
    "financial-transparency-consent.html",
    "medical-transparency-consent.html",
]
FONTS = ["Inter_18pt-Light.ttf", "Inter_18pt-Regular.ttf",
         "Inter_18pt-Medium.ttf", "Inter_18pt-Bold.ttf",
         "Inter_18pt-ExtraBold.ttf", "Inter_18pt-Black.ttf",
         "GeistMono-Regular.ttf", "OFL.txt"]
LOGOS = ["crosswalk-logo-black.png", "crosswalk-logo-white.png", "favicon.svg"]
IMAGES = ["panelist.jpg", "hero.jpg", "about.jpg"]

META = {
    "index.html": ("Crosswalk | Ask any audience question in plain English.",
                   "Prometheus answers from 30 million opted-in people and their entire digital life. Who they are, what they watch, what they buy, where they signed up, and what they did yesterday."),
    "products.html": ("Crosswalk | Products", "Six reports, one for each kind of question. Profile IQ, Subscriber IQ, Digital Journey IQ, Trends IQ, Ranker IQ, Fin IQ."),
    "panel.html": ("Crosswalk | The panel", "30 million people chose to be counted. How the opted-in panel works and what clients receive."),
    "company.html": ("Crosswalk | Company", "Crosswalk Technologies, Inc. Who we work with, how we write the numbers, The Read, and how to reach us."),
}


def rewrite(html: str, out_name: str) -> str:
    html = html.replace("../assets/", "assets/")
    html = html.replace('href="05-ask.html"', 'href="index.html"')
    html = html.replace('href="mock.css"', 'href="assets/css/crosswalk.css"')
    html = html.replace('src="mock.js"', 'src="assets/js/crosswalk.js"')
    html = html.replace("https://dashboard.crosswalknyc.com/the-read", "/the-read")
    title, desc = META.get(out_name, (None, None))
    if title:
        html = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", html, count=1)
    else:
        html = re.sub(r"<title>Crosswalk \| Mockup \d+: [^<]*</title>", "<title>Crosswalk</title>", html, count=1)
    if desc:
        html = html.replace('<link rel="stylesheet"',
                            f'<meta name="description" content="{desc}">\n<link rel="icon" type="image/svg+xml" href="assets/logos/favicon.svg">\n<link rel="stylesheet"', 1)
    else:
        html = html.replace('<link rel="stylesheet"',
                            '<link rel="icon" type="image/svg+xml" href="assets/logos/favicon.svg">\n<link rel="stylesheet"', 1)
    return html


SIGNUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crosswalk | Sign up</title>
<meta name="robots" content="noindex">
<link rel="icon" type="image/svg+xml" href="assets/logos/favicon.svg">
<link rel="stylesheet" href="assets/css/crosswalk.css">
</head>
<body>
<div id="nav"></div>

<section class="signup">
  <div class="left glow">
    <div>
      <div class="eyebrow"><span class="n">01</span> Sign up</div>
      <h1 class="h-l">Create your dashboard and ask your first question.</h1>
      <p class="lead" style="margin-top:18px">Your account opens with Prometheus and a $5,000 balance. Ask anything about any audience. Each report you pull draws from the balance, and the file is yours in the dashboard.</p>
      <div class="panel" style="margin-top:26px;padding:16px 18px;border-color:var(--green)">
        <div class="eyebrow" style="margin-bottom:8px"><span class="n">Your first question</span></div>
        <div id="q" style="font-size:17px;font-weight:600">Who watches Landman, and what do they buy?</div>
      </div>
    </div>
    <div class="ledger">
      <div class="li"><b>Profile IQ</b><span class="p">$300</span></div>
      <div class="li"><b>Ranker IQ</b><span class="p">$300</span></div>
      <div class="li"><b>Subscriber IQ</b><span class="p">$500</span></div>
      <div class="li"><b>Digital Journey IQ</b><span class="p">$500</span></div>
      <div class="li"><b>Fin IQ</b><span class="p">$500</span></div>
      <div class="li"><b>Trends IQ</b><span class="p">Included</span></div>
      <div class="li"><b>Chat with Prometheus</b><span class="p">about $0.15 to $0.84 per question</span></div>
      <div class="li" style="padding-top:10px;border-top:1px solid var(--rule-d)"><b>Top up</b><span class="p">$5,000 when the balance reaches $500</span></div>
    </div>
  </div>
  <div class="right light">
    <div class="steps-bar"><span class="on">1 Account</span><span>2 Opening balance</span><span>3 Ask Prometheus</span></div>
    <div id="notice" class="fieldset" style="display:none;border-color:var(--amethyst)"><p class="body" id="noticeText"></p></div>
    <form id="signupForm" novalidate>
      <div class="fieldset">
        <h3>Account</h3>
        <div class="form">
          <div class="grid-2">
            <div><label for="fn">First name</label><input id="fn" name="first_name" type="text" autocomplete="given-name" required></div>
            <div><label for="ln">Last name</label><input id="ln" name="last_name" type="text" autocomplete="family-name" required></div>
          </div>
          <div><label for="we">Work email</label><input id="we" name="email" type="email" autocomplete="email" placeholder="name@company.com" required></div>
          <div class="grid-2">
            <div><label for="co">Company</label><input id="co" name="company" type="text" autocomplete="organization" required></div>
            <div><label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="new-password" placeholder="At least 12 characters" minlength="12" required></div>
          </div>
        </div>
      </div>
      <div class="fieldset">
        <h3>Opening balance</h3>
        <div class="total" style="border-top:0;margin-top:0;padding-top:0"><span class="body">Charged on the next screen</span><span class="amt">$5,000.00</span></div>
        <p class="fine">Prepaid, not a fee. Every dollar is a report your team pulls or will pull. You enter the card on a secure payment page. It stays on file, and when the balance reaches $500 it tops up with another $5,000. Change or stop top-ups any time from the dashboard. Usage statement downloadable anytime.</p>
      </div>
      <p id="err" class="fine" style="display:none;color:var(--amethyst);font-size:13px"></p>
      <button class="btn btn-olive" type="submit" id="go" style="width:100%;min-height:48px">Continue to payment</button>
      <p class="fine">By continuing you agree to the <a href="terms-of-use.html" style="color:var(--olive-sm);text-decoration:underline">Terms of Use</a> and <a href="privacy-policy-1.html" style="color:var(--olive-sm);text-decoration:underline">Privacy Policy</a>. Already have an account? <a href="/login" style="color:var(--olive-sm);text-decoration:underline">Log in</a>.</p>
    </form>
  </div>
</section>

<div id="foot"></div>
<script src="assets/js/crosswalk.js"></script>
<script>
(function () {
  var params = new URLSearchParams(location.search);
  var q = params.get('q') || '';
  var qEl = document.getElementById('q');
  if (q) qEl.textContent = q;
  if (params.get('cancelled') === '1') {
    document.getElementById('notice').style.display = 'block';
    document.getElementById('noticeText').textContent = 'Payment was not completed. Your account details are saved. Enter the same email and password to continue to payment.';
  }
  var form = document.getElementById('signupForm');
  var err = document.getElementById('err');
  var go = document.getElementById('go');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    err.style.display = 'none';
    var body = {
      first_name: form.first_name.value.trim(),
      last_name: form.last_name.value.trim(),
      email: form.email.value.trim(),
      company: form.company.value.trim(),
      password: form.password.value,
      question: q || qEl.textContent,
      came_from: document.referrer || 'site/signup'
    };
    if (!body.first_name || !body.last_name || !body.email || !body.company) {
      err.textContent = 'Fill in every field.'; err.style.display = 'block'; return;
    }
    if (body.password.length < 12) {
      err.textContent = 'Choose a password of at least 12 characters.'; err.style.display = 'block'; return;
    }
    go.disabled = true; go.textContent = 'One moment';
    fetch('/site/api/signup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), credentials: 'same-origin'
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (res.ok && res.d.url) { location.href = res.d.url; return; }
        var d = res.d || {};
        if (d.error === 'account_exists') {
          err.innerHTML = (d.message || 'You already have an account.') + ' <a href="' + (d.login_url || '/login') + '" style="text-decoration:underline">Log in</a>.';
        } else if (d.error === 'payments_unavailable' || d.error === 'unavailable') {
          err.textContent = 'Payments are unavailable right now. Nothing was charged. Email hello@crosswalknyc.com and we will set you up.';
        } else {
          err.textContent = d.error || 'Something went wrong. Nothing was charged.';
        }
        err.style.display = 'block';
        go.disabled = false; go.textContent = 'Continue to payment';
      })
      .catch(function () {
        err.textContent = 'Could not reach the server. Nothing was charged. Try again.';
        err.style.display = 'block';
        go.disabled = false; go.textContent = 'Continue to payment';
      });
  });
})();
</script>
</body>
</html>
"""

WELCOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crosswalk | Welcome</title>
<meta name="robots" content="noindex">
<link rel="icon" type="image/svg+xml" href="assets/logos/favicon.svg">
<link rel="stylesheet" href="assets/css/crosswalk.css">
</head>
<body>
<div id="nav"></div>

<section class="band dark glow" style="min-height:calc(100vh - 64px);display:flex;align-items:center;text-align:center">
  <div class="wrap">
    <div class="orb" style="display:grid;width:72px;height:72px;font-size:26px;margin:0 auto 22px;box-shadow:0 0 60px rgba(142,63,168,0.45)">///</div>
    <div class="eyebrow" style="margin-bottom:22px"><span class="n">03</span> <span id="eb">Confirming your payment</span></div>
    <h1 class="h-xl" id="h" style="max-width:14ch;margin:0 auto">One moment while we confirm your opening balance.</h1>
    <p class="lead" id="p" style="margin:22px auto 0;text-align:center">This usually takes a few seconds.</p>
    <div id="ask" style="margin-top:30px;display:none">
      <form class="askbox" id="askForm" style="margin:0 auto;width:100%">
        <input type="text" id="askIn" placeholder="Who watches Landman, and what do they buy?">
        <button class="btn btn-green" type="submit">Ask Prometheus</button>
      </form>
    </div>
    <div id="login" style="margin-top:30px;display:none"><a class="btn btn-green" href="/login">Log in to the dashboard</a></div>
    <div class="stats" id="stats" style="max-width:760px;margin:56px auto 0;padding-top:24px;border-top:1px solid var(--rule-d);grid-template-columns:repeat(3,1fr);visibility:hidden">
      <div class="stat"><div class="lab">Balance</div><div class="fig acc" id="bal">$5,000.00</div></div>
      <div class="stat"><div class="lab">Reports pulled</div><div class="fig">0</div></div>
      <div class="stat"><div class="lab">Next top-up at</div><div class="fig">$500</div></div>
    </div>
  </div>
</section>

<div id="foot"></div>
<script src="assets/js/crosswalk.js"></script>
<script>
(function () {
  var sid = new URLSearchParams(location.search).get('sid') || '';
  var tries = 0;
  function money(v) { return '$' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function done(d) {
    document.getElementById('eb').textContent = 'You are in';
    document.getElementById('h').textContent = 'Your balance is ' + money(d.balance_usd) + '. Ask your first question.';
    document.getElementById('p').textContent = 'Prometheus is open. Each report draws from the balance and lands in your library. A receipt is on its way to your inbox.';
    document.getElementById('bal').textContent = money(d.balance_usd);
    document.getElementById('stats').style.visibility = 'visible';
    var q = d.first_question || '';
    if (d.logged_in) {
      var box = document.getElementById('ask'); box.style.display = 'block';
      var inp = document.getElementById('askIn'); if (q) inp.value = q;
      document.getElementById('askForm').addEventListener('submit', function (e) {
        e.preventDefault();
        location.href = '/?ask=' + encodeURIComponent(inp.value || q);
      });
    } else {
      document.getElementById('login').style.display = 'block';
    }
  }
  function waiting() {
    document.getElementById('eb').textContent = 'Almost there';
    document.getElementById('h').textContent = 'Your payment is in. We are opening your account.';
    document.getElementById('p').textContent = 'If this page does not update in a minute, log in with the email and password you chose. Your balance will be there.';
    document.getElementById('login').style.display = 'block';
  }
  function poll() {
    if (!sid) { waiting(); return; }
    fetch('/site/api/signup/status?sid=' + encodeURIComponent(sid), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.paid) { done(d); return; }
        tries += 1;
        if (tries > 45) { waiting(); return; }
        setTimeout(poll, 2000);
      })
      .catch(function () { tries += 1; if (tries > 45) { waiting(); return; } setTimeout(poll, 3000); });
  }
  poll();
})();
</script>
</body>
</html>
"""

REDIRECTS = {
    "about.html": "company.html",
    "contact.html": "company.html#contact",
    "the-read.html": "/the-read",
}


def redirect_stub(target: str) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            f'<meta http-equiv="refresh" content="0; url={target}">'
            f'<link rel="canonical" href="{target}"><title>Crosswalk</title></head>'
            f'<body style="background:#0C1618;color:#E9E8E1;font-family:system-ui">'
            f'<p style="padding:2rem"><a href="{target}" style="color:#C7F23E">Continue</a></p></body></html>\n')


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets" / "css").mkdir(parents=True)
    (OUT / "assets" / "js").mkdir(parents=True)
    (OUT / "assets" / "fonts").mkdir(parents=True)
    (OUT / "assets" / "logos").mkdir(parents=True)
    (OUT / "assets" / "images").mkdir(parents=True)

    # Shared css/js from the mockups, with the nav wired to real routes.
    css = (MOCK / "mock.css").read_text(encoding="utf-8")
    (OUT / "assets" / "css" / "crosswalk.css").write_text(css, encoding="utf-8")
    js = (MOCK / "mock.js").read_text(encoding="utf-8")
    js = js.replace("../assets/", "assets/")
    js = js.replace('href="05-ask.html"', 'href="index.html"')
    js = js.replace("https://dashboard.crosswalknyc.com/the-read", "/the-read")
    js = js.replace("'<a class=\"btn btn-ghost\" href=\"' + DASH + '\">Log in</a>'",
                    "'<a class=\"btn btn-ghost\" href=\"/login\">Log in</a>'")
    js = js.replace("<a href=\"' + DASH + '\">Dashboard</a>", "<a href=\"/\">Dashboard</a>")
    js = js.replace('<a href="#">LinkedIn</a>',
                    '<a href="https://www.linkedin.com/company/crosswalk-technologies" target="_blank" rel="noopener">LinkedIn</a>')
    js = js.replace('<a href="#">Privacy Policy</a><a href="#">Your Privacy Choices</a><a href="#">Opt-Out</a><a href="#">Terms of Use</a><a href="#">Tracking consent</a><a href="#">COPPA</a>',
                    '<a href="privacy-policy-1.html">Privacy Policy</a><a href="do-not-sell-my-personal-information.html">Your Privacy Choices</a><a href="optout.html">Opt-Out</a><a href="terms-of-use.html">Terms of Use</a><a href="tracking-transparency.html">Tracking consent</a><a href="deidentified-data.html">De-identified data</a><a href="coppa.html">COPPA</a><a href="app.html">Join the panel</a>')
    (OUT / "assets" / "js" / "crosswalk.js").write_text(js, encoding="utf-8")

    # Legacy css/js for the legal pages copied from website/.
    shutil.copy(WEB / "assets" / "css" / "site.css", OUT / "assets" / "css" / "site.css")
    legacy_js = (WEB / "assets" / "js" / "site.js").read_text(encoding="utf-8")
    legacy_js = legacy_js.replace("https://dashboard.crosswalknyc.com/the-read", "/the-read")
    legacy_js = legacy_js.replace('var DASH = "https://dashboard.crosswalknyc.com";', 'var DASH = "/";')
    (OUT / "assets" / "js" / "site.js").write_text(legacy_js, encoding="utf-8")

    for f in FONTS:
        shutil.copy(WEB / "assets" / "fonts" / f, OUT / "assets" / "fonts" / f)
    for f in LOGOS:
        shutil.copy(WEB / "assets" / "logos" / f, OUT / "assets" / "logos" / f)
    for f in IMAGES:
        shutil.copy(WEB / "assets" / "images" / f, OUT / "assets" / "images" / f)

    for src_name, out_name in PAGES.items():
        html = (MOCK / src_name).read_text(encoding="utf-8")
        (OUT / out_name).write_text(rewrite(html, out_name), encoding="utf-8")
        print("wrote", out_name)

    for f in LEGAL:
        p = WEB / f
        if p.exists():
            html = p.read_text(encoding="utf-8")
            html = html.replace("https://dashboard.crosswalknyc.com/the-read", "/the-read")
            (OUT / f).write_text(html, encoding="utf-8")
            print("copied", f)

    (OUT / "signup.html").write_text(SIGNUP_HTML, encoding="utf-8")
    (OUT / "welcome.html").write_text(WELCOME_HTML, encoding="utf-8")
    for name, target in REDIRECTS.items():
        (OUT / name).write_text(redirect_stub(target), encoding="utf-8")
    (OUT / "robots.txt").write_text("User-agent: *\nAllow: /site/\nDisallow: /site/signup.html\nDisallow: /site/welcome.html\n", encoding="utf-8")
    print("site built at", OUT)


if __name__ == "__main__":
    main()
