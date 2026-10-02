"""Shared PII redaction expressions for URL columns written to ClickHouse.

Why this module exists
----------------------
On 2026-08-31 a client reported email PII leaking into `clickstream_final`
URL rows (e.g. `?x-email=user%40mail.ru`, `?login_hint=user@ex.com`, Gmail
fragment paths like `#search/user%40ex.com/FM`). Investigation showed:

- The Lambda write path (`aws-lambda/pii_redactor.py`) already redacted
  email PII from live user-agent posts, so its share of the leak was
  minor (fragment gap has since been closed too).
- The MAJOR leak source was the bulk-load / backfill path: multiple
  `migration/*.py` scripts `INSERT INTO clickstream.clickstream_final
  SELECT ... URL ... FROM s3(...)` with no sanitization, pulling raw
  URLs from years-old S3 archive CSVs and Parquet dumps.

Rather than duplicating the redaction expression across a dozen
scripts, this module exposes ClickHouse SQL expressions that wrap any
URL-column reference. All bulk writers now import these helpers and
use them in their SELECT lists.

Marker choices
--------------
- `REDACTED_EMAIL` for plain-text / URL-encoded email substrings
  (the visible-in-the-URL leak class).
- `REDACTED_TRACKING_PAYLOAD` for the entire path/query when the URL
  carries base64-encoded PII that only becomes visible after decoding
  (the invisible-in-the-URL leak class, added 2026-09-01).

Both markers are static tokens (not hashed forms) so retro-scrubbed
historical rows and freshly-inserted rows look identical downstream.
The Lambda still emits `REDACTED_<8-hex>` for its own live redactions,
that's a separate marker in a different codepath and doesn't affect
this module.

Regex shape (plain-text pass)
-----------------------------
`[A-Za-z0-9._+\\-]+(@|%40)[A-Za-z0-9\\-]+(\\.[A-Za-z0-9\\-]+)+`

- Local part: `[A-Za-z0-9._+\\-]+` (email-legal chars).
- Separator: `@` (plain) or `%40` (URL-encoded). Both forms leak.
- Domain: `[A-Za-z0-9\\-]+` followed by AT LEAST ONE
  `\\.[A-Za-z0-9\\-]+` group (i.e. domain must contain a `.`). The dot
  requirement is what prevents false positives on `Kohi%40123`,
  `youtube.com/@handle`, `maps/@47.3915,22.9485`, or numeric IDs like
  `%40123`.

Why two passes for plain-text
-----------------------------
On 2026-08-31 we found ~19 rows/day slipping through the export scrub
with the pattern `REDACTED_EMAIL%40<real-domain.tld>`. Root cause:
some source URLs contain TWO emails concatenated with no separator,
e.g. `?q=shayath%40gmail.comnijaz08%40gmail.com`. The greedy domain
match absorbs the second email's local part (`.comnijaz08`) into the
first email's domain, so a single `replaceRegexpAll` pass yields
`?q=REDACTED_EMAIL%40gmail.com`, still leaking a domain. A second
identical pass matches `REDACTED_EMAIL%40gmail.com` (the marker is
valid local-part chars) and collapses it to `REDACTED_EMAIL`. Running
the double pass on an already-clean value is a no-op, so it's safe to
apply unconditionally. Three+ concatenated emails leave a `%`-fragment
artifact but never a full domain, which is the property we care about.

Base64-encoded PII (added 2026-09-01)
-------------------------------------
2026-09-01: a partner (Christian at OpenAI) reported a SECOND class of
leak, PII that is not visible in the raw URL bytes but appears when
you base64url-decode a path segment or query param. Real example from
Chase's marketing platform (Sailthru wrapper):

    https://connect.chase.com/f/a/.../RgRnmpFzP0TR...WZW1pbHlhZGxlcjIzQGdtYWlsLmNvbVgEAAAADw~~

The `RgRnmpFzP0TR...` segment base64url-decodes to a Sailthru binary
tracking payload that contains `emilyadler23@gmail.com` in plain text.
The 2026-08-31 regex above only fires on `@` or `%40` in the raw URL
bytes, so this class of leak passes right through.

The `redact_base64_url_pii_sql()` helper below scans every URL for
base64url substrings >= 20 chars, decodes each with `tryBase64URLDecode`,
and checks whether the decoded content matches the email regex. If any
candidate does, the ENTIRE URL is collapsed to
`scheme://host/REDACTED_TRACKING_PAYLOAD`. Domain stays visible for
BI/attribution; everything after the host is wiped. Threshold of 20
chars catches ~everything realistic (smallest common tracker payload
is `?e=x@y.co&c=x` = ~15 chars = ~20 base64), while the email regex
acts as the gatekeeper so random 20+ char tokens don't trigger.
Measured on production clickstream_final: ~0.04% redaction rate, all
on domains where the pattern is expected (SSO redirects, calendar
invites, marketing trackers, JWT tokens with email claims). Perf on
CH: ~55M rows/sec, adding ~10s to the nightly clickstream_final export.

`wrap_url_column_full()` bundles both the plain-text and base64 passes
for callers that want maximum coverage. The bare `wrap_url_column()`
remains 2-pass-only for callers where we've explicitly opted OUT of
the base64 scrub (e.g. the Lord outgoing export, per scope decision
2026-09-01).

Kept in sync with:
- `aws-lambda/pii_redactor.py` (`PII_PATTERNS['email']`, live Lambda path)
- The 2026-08-31 ClickHouse ALTER TABLE UPDATE mutation on
  `clickstream_final` and `llmo_events` (historical scrub, static marker)
"""
from __future__ import annotations


URL_REDACTION_REGEX = (
    r"[A-Za-z0-9._+\-]+(@|%40)[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+"
)

REDACTED_MARKER = "REDACTED_EMAIL"

# Regex used INSIDE the base64-decoded content to detect an email. Different
# from URL_REDACTION_REGEX (no %40 here because decoded content is not URL-
# encoded). Doesn't need the double-pass workaround because we're not doing
# in-place substitution on the decoded string, we're just probing for a match.
DECODED_EMAIL_REGEX = r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+"

# When base64-encoded PII is detected, we collapse the URL to this shape.
# Domain stays visible so BI / attribution can still see the user visited
# `chase.com`; everything after the host is wiped.
REDACTED_TRACKING_PATH = "/REDACTED_TRACKING_PAYLOAD"

# Threshold for what "looks base64" enough to be worth decoding + probing.
# 20 chars of base64url encodes 15 bytes of source content, which is the
# smallest realistic tracker payload that carries an email (e.g.
# `e=x@y.co&c=x`). Longer thresholds miss short Mailchimp-style payloads.
BASE64_CANDIDATE_MIN_CHARS = 20


def wrap_url_column(inner_sql: str) -> str:
    """Wrap a ClickHouse SQL expression that yields a URL string with an
    email-substring redaction (plain-text and URL-encoded emails only).

    `inner_sql` can be a bare column reference (`URL`), a qualified
    reference (`t.URL`), or a cast expression (`toString(URL)`). The
    return value is a ClickHouse expression suitable for use inside any
    SELECT list.

    Example:
        wrap_url_column("URL")
        -> "replaceRegexpAll(replaceRegexpAll(URL, '<regex>', 'REDACTED_EMAIL'), '<regex>', 'REDACTED_EMAIL')"

    Callers that already know they want an `AS URL` alias should pass
    only the raw expression; the returned string does NOT include an
    alias. Add `AS URL` (or whatever the destination column name is)
    at the call site.

    Two passes are applied unconditionally. See module docstring for
    the concatenated-emails edge case that motivates it. Idempotent on
    already-clean input.

    Does NOT cover base64-encoded PII. Callers that need that too should
    use `wrap_url_column_full()` instead.
    """
    # Backslashes need escaping when we drop the regex into a Python
    # single-quoted SQL literal that then ships to ClickHouse; the
    # regex parser at CH side sees `\-` (which in a character class is
    # a literal hyphen) and `\.` (literal dot). We double each backslash
    # here so the SQL literal contains the same escape sequences.
    ch_regex = URL_REDACTION_REGEX.replace("\\", "\\\\")
    pass1 = (
        f"replaceRegexpAll({inner_sql}, "
        f"'{ch_regex}', '{REDACTED_MARKER}')"
    )
    return (
        f"replaceRegexpAll({pass1}, "
        f"'{ch_regex}', '{REDACTED_MARKER}')"
    )


def redact_base64_url_pii_sql(inner_sql: str) -> str:
    """Wrap a URL expression with a base64-decoded-email detector + host-collapse.

    For every URL:
      1. `extractAll` all substrings of >= BASE64_CANDIDATE_MIN_CHARS base64url
         alphabet chars (`A-Z a-z 0-9 _ -`).
      2. `tryBase64URLDecode` each candidate.
      3. `match` the decoded bytes against DECODED_EMAIL_REGEX.
      4. If ANY candidate matches, replace the whole URL with
         `scheme://host/REDACTED_TRACKING_PAYLOAD`.
      5. Otherwise pass the URL through unchanged.

    Idempotent: an already-redacted URL (ends in REDACTED_TRACKING_PAYLOAD)
    contains no long base64 blobs so re-applying is a no-op.

    Safe on empty/null/non-URL input: `extractAll` on empty returns an empty
    array, `arrayExists` on empty is false, so the URL passes through
    unchanged.

    Caller responsibility: wrap this only around expressions that yield a
    String. The `inner_sql` is referenced twice in the output SQL (once
    inside `arrayExists` for the probe, once inside `replaceRegexpOne` for
    the host extraction), so a side-effecting expression would evaluate
    twice. Every ClickHouse column reference is pure, so this is only a
    concern for exotic UDFs.
    """
    ch_email_re = DECODED_EMAIL_REGEX.replace("\\", "\\\\")
    return (
        f"if(\n"
        f"  arrayExists(\n"
        f"    b64 -> match(tryBase64URLDecode(b64), '{ch_email_re}'),\n"
        f"    extractAll({inner_sql}, '[A-Za-z0-9_-]{{{BASE64_CANDIDATE_MIN_CHARS},}}')\n"
        f"  ),\n"
        f"  concat(\n"
        f"    replaceRegexpOne({inner_sql}, '^(https?://[^/?#]+).*$', '\\\\1'),\n"
        f"    '{REDACTED_TRACKING_PATH}'\n"
        f"  ),\n"
        f"  {inner_sql}\n"
        f")"
    )


def wrap_url_column_full(inner_sql: str) -> str:
    """Wrap a URL expression with BOTH the plain-text email scrub AND the
    base64-decoded-email scrub.

    Applied in this order:
      1. `wrap_url_column`  - two passes of plain-text / %40 email substring
         redaction. Preserves the surrounding URL shape.
      2. `redact_base64_url_pii_sql`  - if any base64-encoded PII remains
         after step 1 (it will, since step 1 only sees `@`/`%40` in the raw
         URL bytes), collapse the whole URL to
         `scheme://host/REDACTED_TRACKING_PAYLOAD`.

    Order matters: doing the base64 pass first would sometimes redact a URL
    that already has a visible email in it, which is a legitimate outcome
    (host stays visible either way), but wrapping the two-pass first keeps
    a bit more of the URL structure intact when the plain-text pass alone
    already scrubs cleanly.
    """
    two_pass = wrap_url_column(inner_sql)
    return redact_base64_url_pii_sql(two_pass)
