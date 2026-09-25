(function () {
  var DASH = "https://dashboard.crosswalknyc.com";
  function el(html) { var t = document.createElement("template"); t.innerHTML = html.trim(); return t.content; }

  var nav =
    '<header class="nav"><div class="wrap nav-in">' +
      '<a class="logo-pill" href="index.html"><img src="assets/logos/crosswalk-logo-black.png" alt="Crosswalk"></a>' +
      '<nav class="nav-links">' +
        '<a href="products.html">Products</a>' +
        '<a href="panel.html">The panel</a>' +
        '<a href="/the-read">The Read</a>' +
        '<a href="company.html">Company</a>' +
        '<a class="btn btn-ghost" href="/login">Log in</a>' +
        '<a class="btn btn-green" href="signup.html">Sign up</a>' +
      '</nav>' +
    '</div></header>';

  var foot =
    '<footer class="foot"><div class="wrap">' +
      '<div class="foot-grid">' +
        '<div><div class="tag">Noise to knowledge.</div><div>Crosswalk Technologies, Inc.<br>23465 Civic Center Way Bldg 9, Malibu, CA 90265<br>hello@crosswalknyc.com</div></div>' +
        '<div><strong>Products</strong><a href="profile-iq.html">Profile IQ</a><a href="subscriber-iq.html">Subscriber IQ</a><a href="digital-journey-iq.html">Digital Journey IQ</a><a href="attribution-iq.html">Attribution IQ</a><a href="brand-partnership-iq.html">Brand Partnership IQ</a><a href="flywheel-iq.html">Flywheel IQ</a><a href="trends-iq.html">Trends IQ</a><a href="ranker-iq.html">Rankers IQ</a><a href="fin-iq.html">Fin IQ</a></div>' +
        '<div><strong>Company</strong><a href="company.html">About</a><a href="/the-read">The Read</a><a href="panel.html">The panel</a><a href="company.html#contact">Contact</a><a href="signup.html">Sign up</a><a href="/">Dashboard</a><a href="https://www.linkedin.com/company/crosswalk-technologies" target="_blank" rel="noopener">LinkedIn</a></div>' +
        '<div><strong>Legal</strong><a href="privacy-policy-1.html">Privacy Policy</a><a href="do-not-sell-my-personal-information.html">Your Privacy Choices</a><a href="optout.html">Opt-Out</a><a href="terms-of-use.html">Terms of Use</a><a href="tracking-transparency.html">Tracking consent</a><a href="deidentified-data.html">De-identified data</a><a href="coppa.html">COPPA</a><a href="/site/join">Join the panel</a></div>' +
      '</div>' +
      '<div class="foot-bar"><div>&copy; 2026 Crosswalk</div><div>All panel data is zero-party and revocable</div></div>' +
    '</div></footer>';

  var n = document.getElementById("nav"); if (n) n.replaceWith(el(nav));
  var f = document.getElementById("foot"); if (f) f.replaceWith(el(foot));
})();
