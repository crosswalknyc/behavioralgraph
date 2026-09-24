(function () {
  var DASH = "/";
  var MAIL = "hello@crosswalknyc.com";
  var PHONE = "+1 (818) 231-2610";
  var ADDR = "23465 Civic Center Way Bldg 9, Malibu, CA 90265";

  function el(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content;
  }

  function navHTML() {
    return (
      '<a class="skip" href="#main">Skip to main content</a>' +
      '<nav class="nav" id="mainNav">' +
        '<div class="nav-in">' +
          '<a class="logo-pill" href="index.html">' +
            '<img src="assets/logos/crosswalk-logo-black.png" alt="Crosswalk">' +
          '</a>' +
          '<div class="nav-r">' +
            '<button class="has-mega" type="button" id="prodBtn" aria-expanded="false" aria-controls="mega">Products</button>' +
            '<a href="panel.html" data-nav="panel.html">The panel</a>' +
            '<a href="/the-read" data-nav="the-read">The Read</a>' +
            '<a href="about.html" data-nav="about.html">About</a>' +
            '<a href="contact.html" data-nav="contact.html">Contact</a>' +
            '<a class="btn btn-ghost" href="' + DASH + '">Log in</a>' +
            '<button class="burger" type="button" id="hamBtn" aria-label="Open menu" aria-expanded="false">' +
              '<span></span><span></span><span></span>' +
            '</button>' +
          '</div>' +
        '</div>' +
        '<div class="mega" id="mega" aria-hidden="true">' +
          '<div class="wrap mega-grid">' +
            '<a href="profile-iq.html"><strong>Profile IQ</strong><span>What people do, not what they say.</span></a>' +
            '<a href="subscriber-iq.html"><strong>Subscriber IQ</strong><span>Signups, cancels, and switches.</span></a>' +
            '<a href="digital-journey-iq.html"><strong>Digital Journey IQ</strong><span>First search to the buy.</span></a>' +
            '<a href="trends-iq.html"><strong>Trends IQ</strong><span>What moved yesterday, ranked.</span></a>' +
            '<a href="ranker-iq.html"><strong>Ranker IQ</strong><span>Who is on top.</span></a>' +
            '<a href="fin-iq.html"><strong>Fin IQ</strong><span>Behavior before the market prices it.</span></a>' +
          '</div>' +
        '</div>' +
      '</nav>' +
      '<div class="drawer" id="drawer">' +
        '<a href="profile-iq.html">Profile IQ</a>' +
        '<a href="subscriber-iq.html">Subscriber IQ</a>' +
        '<a href="digital-journey-iq.html">Digital Journey IQ</a>' +
        '<a href="trends-iq.html">Trends IQ</a>' +
        '<a href="ranker-iq.html">Ranker IQ</a>' +
        '<a href="fin-iq.html">Fin IQ</a>' +
        '<a href="panel.html">The panel</a>' +
        '<a href="/the-read">The Read</a>' +
        '<a href="about.html">About</a>' +
        '<a href="contact.html">Contact</a>' +
        '<a href="' + DASH + '">Log in</a>' +
      '</div>'
    );
  }

  function footerHTML() {
    return (
      '<footer class="foot">' +
        '<div class="wrap">' +
          '<div class="foot-grid">' +
            '<div>' +
              '<strong>Crosswalk</strong>' +
              '<a href="about.html">About</a>' +
              '<a href="/the-read">The Read</a>' +
              '<a href="panel.html">The panel</a>' +
              '<a href="contact.html">Contact</a>' +
              '<a href="' + DASH + '">Dashboard</a>' +
              '<a href="https://www.linkedin.com/company/crosswalk-technologies" target="_blank" rel="noopener">LinkedIn</a>' +
            '</div>' +
            '<div>' +
              '<strong>Products</strong>' +
              '<a href="profile-iq.html">Profile IQ</a>' +
              '<a href="subscriber-iq.html">Subscriber IQ</a>' +
              '<a href="digital-journey-iq.html">Digital Journey IQ</a>' +
              '<a href="trends-iq.html">Trends IQ</a>' +
              '<a href="ranker-iq.html">Ranker IQ</a>' +
              '<a href="fin-iq.html">Fin IQ</a>' +
            '</div>' +
            '<div>' +
              '<strong>Panel</strong>' +
              '<a href="why-we-track.html">What we track</a>' +
              '<a href="app.html">Join the panel</a>' +
              '<a href="tracking-transparency.html">Tracking consent</a>' +
              '<a href="deidentified-data.html">De-identified data</a>' +
              '<a href="coppa.html">COPPA</a>' +
            '</div>' +
            '<div>' +
              '<strong>Legal</strong>' +
              '<a href="privacy-policy-1.html">Privacy Policy</a>' +
              '<a href="do-not-sell-my-personal-information.html">Your Privacy Choices</a>' +
              '<a href="optout.html">Opt-Out</a>' +
              '<a href="terms-of-use.html">Terms of Use</a>' +
              '<a href="mailto:' + MAIL + '">' + MAIL + '</a>' +
              '<span>' + PHONE + '<br>' + ADDR + '</span>' +
            '</div>' +
          '</div>' +
          '<div class="foot-bar">' +
            '<div>© 2026 Crosswalk Technologies, Inc.</div>' +
            '<div>Individual-level. Zero-party.</div>' +
          '</div>' +
        '</div>' +
      '</footer>' +
      '<div class="cookie" id="cookie">' +
        '<p>We use cookies to run this site. Panel software is separate. <a href="privacy-policy-1.html">Privacy Policy</a></p>' +
        '<div class="cookie-actions">' +
          '<button class="btn btn-ghost" type="button" id="cookieNo">Decline</button>' +
          '<button class="btn btn-lime" type="button" id="cookieYes">Accept</button>' +
        '</div>' +
      '</div>'
    );
  }

  function placeChrome() {
    var mount = document.getElementById("site-chrome");
    if (mount) mount.replaceWith(el(navHTML()));
    else document.body.prepend(el(navHTML()));
    var foot = document.getElementById("site-footer");
    if (foot) foot.replaceWith(el(footerHTML()));
    else document.body.appendChild(el(footerHTML()));
  }

  function markNav() {
    var file = (location.pathname.split("/").pop() || "index.html");
    document.querySelectorAll(".nav-r a[data-nav]").forEach(function (a) {
      if (a.getAttribute("data-nav") === file) a.classList.add("is-on");
    });
    if (/(-iq|fin-iq)\.html$/.test(file)) {
      var btn = document.getElementById("prodBtn");
      if (btn) btn.classList.add("is-on");
    }
  }

  function wireNav() {
    var mega = document.getElementById("mega");
    var prod = document.getElementById("prodBtn");
    var ham = document.getElementById("hamBtn");
    var drawer = document.getElementById("drawer");

    function setMega(on) {
      if (!mega || !prod) return;
      mega.classList.toggle("open", on);
      mega.setAttribute("aria-hidden", on ? "false" : "true");
      prod.setAttribute("aria-expanded", on ? "true" : "false");
    }
    function setDrawer(on) {
      if (!drawer || !ham) return;
      drawer.classList.toggle("open", on);
      ham.setAttribute("aria-expanded", on ? "true" : "false");
      document.body.style.overflow = on ? "hidden" : "";
    }
    if (prod) {
      prod.addEventListener("click", function (e) {
        e.stopPropagation();
        setMega(!mega.classList.contains("open"));
      });
    }
    if (ham) {
      ham.addEventListener("click", function (e) {
        e.stopPropagation();
        setDrawer(!drawer.classList.contains("open"));
      });
    }
    document.addEventListener("click", function (e) {
      if (mega && !mega.contains(e.target) && e.target !== prod) setMega(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        setMega(false);
        setDrawer(false);
      }
    });
  }

  function wireCookie() {
    var bar = document.getElementById("cookie");
    if (!bar) return;
    if (navigator.globalPrivacyControl === true) {
      localStorage.setItem("cwCookie", "decline");
    }
    var pref = localStorage.getItem("cwCookie");
    if (!pref) bar.classList.add("open");
    function set(v) {
      localStorage.setItem("cwCookie", v);
      bar.classList.remove("open");
    }
    var yes = document.getElementById("cookieYes");
    var no = document.getElementById("cookieNo");
    if (yes) yes.addEventListener("click", function () { set("accept"); });
    if (no) no.addEventListener("click", function () { set("decline"); });
  }

  function wireReveal() {
    var nodes = document.querySelectorAll(".rv");
    if (!nodes.length) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      nodes.forEach(function (n) { n.classList.add("vis"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("vis");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12 });
    nodes.forEach(function (n) { io.observe(n); });
  }

  function wireHero() {
    var vid = document.getElementById("heroVid");
    var hero = document.querySelector(".hero");
    if (!vid || !hero) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      hero.classList.add("still");
      vid.pause();
      return;
    }
    function tryPlay() {
      var play = vid.play();
      if (play && play.catch) play.catch(function () { hero.classList.add("still"); });
    }
    vid.muted = true;
    if (vid.readyState >= 2) tryPlay();
    else vid.addEventListener("loadeddata", tryPlay, { once: true });
  }

  placeChrome();
  markNav();
  wireNav();
  wireCookie();
  wireReveal();
  wireHero();
})();
