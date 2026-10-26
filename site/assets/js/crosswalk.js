(function () {
  var DASH = "https://dashboard.crosswalknyc.com";
  function el(html) { var t = document.createElement("template"); t.innerHTML = html.trim(); return t.content; }

  var nav =
    '<header class="nav"><div class="wrap nav-in">' +
      '<a class="logo-pill" href="index.html"><img src="assets/logos/crosswalk-logo-black.png" alt="Crosswalk"></a>' +
      '<nav class="nav-links">' +
        '<a href="products.html">Products</a>' +
        '<a href="panel.html">The Panel</a>' +
        '<a href="digital-twins.html">Digital twins</a>' +
        '<a href="micro-series.html">Micro-series</a>' +
        '<a href="' + DASH + '/the-read">The Read</a>' +
        '<a href="' + DASH + '/the-read/papers">White papers</a>' +
        '<a href="company.html">Company</a>' +
        '<a href="clients.html">Clients</a>' +
        '<button class="nav-menu-btn" type="button" aria-expanded="false" aria-controls="navMenu"><i></i>Menu</button>' +
        '<a class="btn btn-ghost" href="' + DASH + '/login">Log in</a>' +
        '<a class="btn btn-green" href="signup.html">Sign up</a>' +
      '</nav>' +
    '</div>' +
    // Same links again for the narrow layout. Below 1050px the row
    // cannot hold eight of them without breaking the words.
    '<div class="nav-menu" id="navMenu"><div class="wrap nav-menu-in">' +
      '<a href="products.html">Products</a>' +
      '<a href="panel.html">The Panel</a>' +
      '<a href="digital-twins.html">Digital twins</a>' +
      '<a href="micro-series.html">Micro-series</a>' +
      '<a href="' + DASH + '/the-read">The Read</a>' +
      '<a href="' + DASH + '/the-read/papers">White papers</a>' +
      '<a href="company.html">Company</a>' +
      '<a href="clients.html">Clients</a>' +
      '<a href="' + DASH + '/login">Log in</a>' +
    '</div></div>' +
  '</header>';

  var foot =
    '<footer class="foot"><div class="wrap">' +
      '<div class="foot-grid">' +
        '<div><div class="tag">Noise to knowledge.</div><div>Crosswalk Technologies, Inc.<br>23465 Civic Center Way Bldg 9, Malibu, CA 90265<br>hello@crosswalknyc.com</div></div>' +
        '<div><strong>Products</strong><a href="profile-iq.html">Profile IQ</a><a href="subscriber-iq.html">Subscriber IQ</a><a href="digital-journey-iq.html">Digital Journey IQ</a><a href="attribution-iq.html">Attribution IQ</a><a href="brand-partnership-iq.html">Brand Partnership IQ</a><a href="flywheel-iq.html">Flywheel IQ</a><a href="trends-iq.html">Trends IQ</a><a href="ranker-iq.html">Rankers IQ</a><a href="fin-iq.html">Fin IQ</a><a href="digital-twins.html">Digital twins</a><a href="micro-series.html">Micro-series</a></div>' +
        '<div><strong>Company</strong><a href="company.html">About</a><a href="clients.html">Clients</a><a href="' + DASH + '/the-read">The Read</a><a href="' + DASH + '/the-read/papers">White papers</a><a href="panel.html">The panel</a><a href="company.html#contact">Contact</a><a href="signup.html">Sign up</a><a href="' + DASH + '">Dashboard</a><a href="https://www.linkedin.com/company/crosswalk-technologies" target="_blank" rel="noopener">LinkedIn</a></div>' +
        '<div><strong>Legal</strong><a href="privacy-policy-1.html">Privacy Policy</a><a href="do-not-sell-my-personal-information.html">Your Privacy Choices</a><a href="optout.html">Opt-Out</a><a href="terms-of-use.html">Terms of Use</a><a href="tracking-transparency.html">Tracking consent</a><a href="deidentified-data.html">De-identified data</a><a href="coppa.html">COPPA</a><a href="/site/join">Join the panel</a></div>' +
      '</div>' +
      '<div class="foot-bar"><div>&copy; 2026 Crosswalk</div><div>All panel data is zero-party and revocable</div></div>' +
    '</div></footer>';

  var n = document.getElementById("nav"); if (n) n.replaceWith(el(nav));
  var f = document.getElementById("foot"); if (f) f.replaceWith(el(foot));

  // The narrow-window menu. Opens on the button, closes on a link, on
  // Escape, on a click outside, and whenever the window grows back to
  // a width that shows the full row again.
  var header = document.querySelector("header.nav");
  var btn = header && header.querySelector(".nav-menu-btn");
  if (header && btn) {
    var setOpen = function (open) {
      header.classList.toggle("is-menu-open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!header.classList.contains("is-menu-open"));
    });
    header.querySelectorAll(".nav-menu a").forEach(function (a) {
      a.addEventListener("click", function () { setOpen(false); });
    });
    document.addEventListener("click", function (e) {
      if (!header.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 1049) setOpen(false);
    });
  }
})();
