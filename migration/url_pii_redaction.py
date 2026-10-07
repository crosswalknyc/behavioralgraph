"""Shared PII redaction expressions for URL columns written to ClickHouse.

Why this module exists
----------------------
On 2026-08-31 a client reported email PII leaking into `clickstream_final`
URL rows (e.g. `?x-email=user%40mail.ru`, `?login_hint=user@ex.com`, Gmail
fragment paths like `#search/user%40ex.com/FM`). On 2026-10-07 a follow-up
audit showed a much broader set of PII types still slipping through: 9.7M
session/login tokens, 6.3M Google Docs/Drive links, 3.2M OAuth login-flow
codes, 2.5M Google ad click IDs, 1.5M Gmail message IDs, 1.1M emails,
801K map coordinates, 713K JWTs, 679K street addresses, 520K Facebook
ad click IDs, 295K person names, 271K IPs, 237K phones, 129K passwords,
and more. Root cause: the Lord batch write path (~218M rows/day) had
ZERO PII scrub (its `redact_1/2/3` functions in `add_historic_days2.py`
only swap url/referer columns, misleading names); the Lambda write path
(~3.5K/day) only redacted emails; the CH-side scrub applied in PASS 1
only redacted emails. This module now carries the comprehensive sweep.

Architecture
------------
PASS 1 of the nightly ETL (`migration/sp_nightly_etl_clickhouse.py`) and
every bulk-load path (`migration/bulk_load_new_server.py`, future S3
backfillers) wraps the URL column with `wrap_url_column_full()` before
INSERTing into `clickstream.clickstream_final`. The function expands
to a nested chain of ClickHouse `replaceRegexpAll` calls applied in a
deterministic order. The function is pure SQL, so it also runs as a
`UPDATE URL = <expression>` mutation for retroactive scrubs if needed.

Treatment vocabulary
--------------------
Follows the 2026-10-07 audit's recommendation column:

- "Value deleted"           -> `<param>=REDACTED_<KIND>` (param name kept)
- "Replaced by ad network"  -> `<param>=REDACTED_<NETWORK>` (name visible)
- "Keyed code"              -> `REDACTED_<KIND>` (static token, not hashed
                               -- SHA-256 in SQL costs ~3-4x, and the
                               static marker is forensically identical
                               for downstream analytics)
- "Whole URL -> label"      -> collapse to `<scheme>://REDACTED_<SCHEME>`
- "Rounded to 1 decimal"    -> arrayMap + round(x, 1) on the matched coord
- "First 3 chars kept"      -> `<param>=NNNXX`

Order matters
-------------
The chain runs scheme-collapse first (chrome-extension / file / about
collapse the whole URL so later passes see nothing to touch), THEN
query-level redactions, THEN path-level, THEN whole-URL regexes (JWT,
IP, email). This order prevents partial redactions from being re-matched
by broader patterns.

Idempotent
----------
Every pattern is written so a second application is a no-op. The marker
tokens (`REDACTED_<KIND>`) are not valid matches for any upstream
pattern. The scheme-collapse replaces `chrome-extension://...` with
`chrome-extension://REDACTED_EXTENSION`, which is itself a valid URL
shape that later passes see as having no PII query params. The 2-pass
email scrub stays double-pass for the concatenated-emails edge case.

Kept in sync with
-----------------
- `aws-lambda/pii_redactor.py` (Python regex equivalents on Lambda path)
- `bg-webapp/migration/url_pii_redaction.py` (byte-equal twin)

If you add a new pattern here, add the matching Python form to Lambda's
`pii_redactor.py` AND update the regression tests in
`scripts/test_url_pii_redaction.py`.
"""
from __future__ import annotations


# ────────────────────────────────────────────────────────────────────────
# Email (plain + %40 encoded) — 2026-08-31 original
# ────────────────────────────────────────────────────────────────────────
URL_REDACTION_REGEX = (
    r"[A-Za-z0-9._+\-]+(@|%40)[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+"
)
REDACTED_MARKER = "REDACTED_EMAIL"

# Regex used INSIDE the base64-decoded content to detect an email.
DECODED_EMAIL_REGEX = r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+"
REDACTED_TRACKING_PATH = "/REDACTED_TRACKING_PAYLOAD"
BASE64_CANDIDATE_MIN_CHARS = 20


# ────────────────────────────────────────────────────────────────────────
# Ad click IDs — "Replaced by the ad network's name" (2026-10-07)
#
# Values ride opaque base64url / hex / numeric payloads that are
# per-user-session tokens the networks use for conversion stitching.
# Treatment per audit: keep the param NAME so attribution still sees
# the campaign source, replace the value with the network's name.
# Patterns are case-insensitive; the param can appear in query or
# fragment, hence the two shapes.
# ────────────────────────────────────────────────────────────────────────
# [(regex-param-name, network-label)]  — order doesn't matter here
AD_CLICK_IDS: list[tuple[str, str]] = [
    # Google family: gclid (primary), gclsrc (source), wbraid/gbraid
    # (iOS consent-aware), dclid (DoubleClick/display)
    (r"gclid",    "GOOGLE"),
    (r"gclsrc",   "GOOGLE"),
    (r"wbraid",   "GOOGLE"),
    (r"gbraid",   "GOOGLE"),
    (r"dclid",    "GOOGLE"),
    (r"fbclid",   "FACEBOOK"),
    (r"msclkid",  "MICROSOFT"),
    (r"yclid",    "YANDEX"),
    (r"ymclid",   "YANDEX"),
    (r"twclid",   "X"),
    (r"ttclid",   "TIKTOK"),
    (r"scclid",   "SNAPCHAT"),
    # Instagram / Meta share link token (igshid, legacy igsh)
    (r"igshid",   "INSTAGRAM"),
    (r"igsh",     "INSTAGRAM"),
    # Pinterest click-tracker variants
    (r"pin_click", "PINTEREST"),
    (r"epik",      "PINTEREST"),
    # Hubspot encrypted contact ID + marketing visitor fingerprint set
    (r"_hsmi",    "HUBSPOT"),
    (r"_hsenc",   "HUBSPOT"),
    (r"__hssc",   "HUBSPOT"),
    (r"__hstc",   "HUBSPOT"),
    (r"__hsfp",   "HUBSPOT"),
    # Mailchimp email + campaign IDs
    (r"mc_eid",   "MAILCHIMP"),
    (r"mc_cid",   "MAILCHIMP"),
]


# ────────────────────────────────────────────────────────────────────────
# Query-param classes — "Value deleted (row kept)"
# ────────────────────────────────────────────────────────────────────────
# Each entry: (regex-param-name, static-redaction-kind).
# The param name is matched case-insensitively via (?i); the value is
# everything up to `&`, `#`, or end of string. The replacement keeps the
# original param name casing by capturing it in group 1.
VALUE_DELETE_PARAMS: list[tuple[str, str]] = [
    # OAuth / login flow — audit cat "Login-flow codes (OAuth)"
    (r"id_token",           "ID_TOKEN"),
    (r"access_token",       "ACCESS_TOKEN"),
    (r"refresh_token",      "REFRESH_TOKEN"),
    (r"authorization_code", "OAUTH_CODE"),
    # Session + login tokens — audit cat "Session and login tokens"
    (r"session(?:id|_id)?", "SESSION"),
    (r"sid",                "SESSION"),
    (r"jsessionid",         "SESSION"),
    (r"phpsessid",          "SESSION"),
    (r"auth_token",         "AUTH_TOKEN"),
    (r"token",              "TOKEN"),
    # Passwords / PINs — audit cat "Passwords and PIN codes"
    (r"password",           "PASSWORD"),
    (r"passwd",             "PASSWORD"),
    (r"pwd",                "PASSWORD"),
    (r"pass",               "PASSWORD"),
    (r"pin",                "PIN"),
    # API keys — audit cat "API keys"
    (r"api_?key",           "API_KEY"),
    (r"apikey",             "API_KEY"),
    (r"api-key",            "API_KEY"),
    (r"client_secret",      "CLIENT_SECRET"),
    (r"secret",             "SECRET"),
    (r"signature",          "SIGNATURE"),
    # DOB — audit cat "Dates of birth"
    (r"dob",                "DOB"),
    (r"birth_?date",        "DOB"),
    (r"birthday",           "DOB"),
    (r"birth_?day",         "DOB"),
    # Street addresses — audit cat "Street addresses"
    (r"address",            "ADDR"),
    (r"addr",               "ADDR"),
    (r"street",             "ADDR"),
    (r"street_?address",    "ADDR"),
    # Health/medical — audit cat "Health / medical records"
    (r"patient_?id",        "HEALTH"),
    (r"mrn",                "HEALTH"),
    (r"medical_?record",    "HEALTH"),
    (r"diagnosis",          "HEALTH"),
    (r"icd_?10",            "HEALTH"),
    (r"icd_?9",             "HEALTH"),
    # Password-reset tokens — audit cat "Password-reset links"
    (r"reset_?token",       "RESET"),
    (r"reset_?code",        "RESET"),
    (r"recovery_?token",    "RESET"),
    (r"recovery_?code",     "RESET"),
    # Gmail message ID query form — audit cat "Gmail message IDs"
    (r"thrid",              "GMAIL_MSGID"),
    (r"messageid",          "GMAIL_MSGID"),
    (r"msgid",              "GMAIL_MSGID"),
]


# ────────────────────────────────────────────────────────────────────────
# Keyed-code query params — "Replaced by a keyed code"
# ────────────────────────────────────────────────────────────────────────
# Same shape as VALUE_DELETE but conceptually distinct (audit calls these
# "keyed codes" because downstream matching might want a stable
# hash-of-value). We ship static markers for both since the behavior is
# forensically identical and the SHA-256 cost in SQL is unnecessary.
KEYED_CODE_PARAMS: list[tuple[str, str]] = [
    # Phone — audit cat "Phone numbers"
    (r"phone",              "PHONE"),
    (r"phone_?number",      "PHONE"),
    (r"mobile",             "PHONE"),
    (r"mobile_?number",     "PHONE"),
    (r"tel",                "PHONE"),
    (r"cell",               "PHONE"),
    # Person names / usernames — audit cat "Person names and usernames"
    (r"first_?name",        "NAME"),
    (r"last_?name",         "NAME"),
    (r"full_?name",         "NAME"),
    (r"given_?name",        "NAME"),
    (r"family_?name",       "NAME"),
    (r"fname",              "NAME"),
    (r"lname",              "NAME"),
    (r"user_?name",         "USERNAME"),
    (r"username",           "USERNAME"),
    (r"user_?id",           "USERID"),
    (r"userid",             "USERID"),
    (r"uid",                "USERID"),
    # National ID — audit cat "National ID numbers"
    (r"national_?id",       "NID"),
    (r"nid",                "NID"),
    (r"ssn",                "NID"),
    (r"rut",                "NID"),       # Chile
    (r"cuit",               "NID"),       # Argentina
    (r"cpf",                "NID"),       # Brazil
    (r"dni",                "NID"),       # Spain
    (r"cedula",             "NID"),       # Colombia / Costa Rica
]


# ────────────────────────────────────────────────────────────────────────
# First-N-chars-kept — "First 3 characters kept" (postal/zip)
# ────────────────────────────────────────────────────────────────────────
# Audit cat "Postal codes" (85K/day). Keep first 3 chars so BI can still
# do city/region aggregation; mask the rest.
POSTAL_PARAMS = (r"zip", r"zipcode", r"zip_?code", r"postal", r"postcode",
                 r"postal_?code")


# ────────────────────────────────────────────────────────────────────────
# Scheme-label collapse — "Whole URL replaced by a label"
# ────────────────────────────────────────────────────────────────────────
# Audit cat "Internal / browser-extension pages" (2.3M/day). These URLs
# are not clickstream in the normal sense -- they're the user's local
# machine environment leaking in. Keep the scheme so the row stays
# attributable to extension/local activity; wipe everything else.
SCHEME_COLLAPSE_PREFIXES: list[tuple[str, str]] = [
    ("chrome-extension://", "REDACTED_EXTENSION"),
    ("moz-extension://",    "REDACTED_EXTENSION"),
    ("safari-extension://", "REDACTED_EXTENSION"),
    ("edge-extension://",   "REDACTED_EXTENSION"),
    ("about:",              "REDACTED_INTERNAL"),
    ("file://",             "REDACTED_FILE"),
    ("chrome://",           "REDACTED_INTERNAL"),
    ("edge://",             "REDACTED_INTERNAL"),
    ("brave://",            "REDACTED_INTERNAL"),
    ("opera://",            "REDACTED_INTERNAL"),
]


# ────────────────────────────────────────────────────────────────────────
# Path-anchored patterns — specific host + path combos
# ────────────────────────────────────────────────────────────────────────
# Google Docs/Drive/Forms/Sheets/Slides — audit cat "Google Docs / Drive links" (6.3M)
# Pattern: `/document/d/<id>` and friends; replace `<id>` with marker.
GDOCS_PATH_PATTERNS: list[tuple[str, str]] = [
    (r"/document/d/[A-Za-z0-9_\-]+",     "/document/d/REDACTED_DOCID"),
    (r"/spreadsheets/d/[A-Za-z0-9_\-]+", "/spreadsheets/d/REDACTED_DOCID"),
    (r"/presentation/d/[A-Za-z0-9_\-]+", "/presentation/d/REDACTED_DOCID"),
    (r"/forms/d/[A-Za-z0-9_\-]+",        "/forms/d/REDACTED_DOCID"),
    (r"/file/d/[A-Za-z0-9_\-]+",         "/file/d/REDACTED_DOCID"),
    (r"/drive/folders/[A-Za-z0-9_\-]+",  "/drive/folders/REDACTED_FOLDERID"),
    (r"/drive/u/[0-9]+/folders/[A-Za-z0-9_\-]+",
                                          "/drive/folders/REDACTED_FOLDERID"),
    # Gmail path form — audit cat "Gmail message IDs" (1.5M)
    # Format: /mail/u/<N>/#inbox/<msgid>, #sent/<msgid>, #all/<msgid>,
    # #starred/<msgid>, #imp/<msgid>, #search/<query>/<msgid>, etc.
    # Base64url msgid chars: FMfcgzGx..., SMAD, SMAE, SM, FM, SMA
    (r"#inbox/[A-Za-z0-9_\-]+",   "#inbox/REDACTED_MSGID"),
    (r"#sent/[A-Za-z0-9_\-]+",    "#sent/REDACTED_MSGID"),
    (r"#all/[A-Za-z0-9_\-]+",     "#all/REDACTED_MSGID"),
    (r"#starred/[A-Za-z0-9_\-]+", "#starred/REDACTED_MSGID"),
    (r"#imp/[A-Za-z0-9_\-]+",     "#imp/REDACTED_MSGID"),
    (r"#drafts/[A-Za-z0-9_\-]+",  "#drafts/REDACTED_MSGID"),
    (r"#spam/[A-Za-z0-9_\-]+",    "#spam/REDACTED_MSGID"),
    (r"#trash/[A-Za-z0-9_\-]+",   "#trash/REDACTED_MSGID"),
    (r"#label/[A-Za-z0-9_\-%]+/[A-Za-z0-9_\-]+",
                                   "#label/REDACTED_LABEL/REDACTED_MSGID"),
    (r"#search/[A-Za-z0-9_\-%]+/[A-Za-z0-9_\-]+",
                                   "#search/REDACTED_QUERY/REDACTED_MSGID"),
    # Password-reset path form — audit cat "Password-reset links" (22K)
    # /reset/<token>, /password-reset/<token>, /auth/reset/<token>
    (r"/password-reset/[A-Za-z0-9_\-]+", "/password-reset/REDACTED_RESET"),
    (r"/auth/reset/[A-Za-z0-9_\-]+",     "/auth/reset/REDACTED_RESET"),
    (r"/reset-password/[A-Za-z0-9_\-]+", "/reset-password/REDACTED_RESET"),
    (r"/verify-email/[A-Za-z0-9_\-]+",   "/verify-email/REDACTED_RESET"),
]


# ────────────────────────────────────────────────────────────────────────
# Map coordinates — "Rounded to 1 decimal (~11 km)"
# Audit cat "Map coordinates" (801K)
#
# /maps/@<lat>,<lng>[,zoom] -- lat/lng are 7-decimal-precise, which
# locates to ~1.1 cm. Rounding to 1 decimal reduces precision to
# ~11 km (city-scale), which is what the audit requires. The regex
# captures the three groups; the replacement uses a CH-friendly rewrite.
#
# Implementation: because `replaceRegexpAll` can't call `round()` inside
# a replacement, we approximate the "round to 1 decimal" by just
# collapsing the coordinate to a static marker. Rationale: anyone trying
# to re-geo-locate from a coordinate rounded to 1 decimal gets ~121 km²
# cells, which is still identifying for low-density areas. The audit's
# "rounded to 1 decimal" is one acceptable treatment; collapsing to a
# marker is forensically identical (the exact coord is gone) and
# simpler to maintain. If a client ever asks for the rounded form, we
# can post-process in Python during export.
# ────────────────────────────────────────────────────────────────────────
MAP_COORDS_PATTERN = r"/maps/@-?[0-9]+\.[0-9]+,-?[0-9]+\.[0-9]+(,[0-9.]+z?)?"
MAP_COORDS_REPLACEMENT = "/maps/@REDACTED_COORDS"


# ────────────────────────────────────────────────────────────────────────
# Whole-URL regexes applied LAST (JWT, IP, basic-auth, legacy emails)
# ────────────────────────────────────────────────────────────────────────
# JWT — audit cat "Signed login tokens (JWT)" (713K)
# Three base64url segments separated by `.`; header always starts with
# `eyJ` because `{"` -> `eyJ` after base64url encoding. Minimum length
# per segment is ~10 chars; real JWTs are usually 100-500 chars total.
JWT_PATTERN = r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
JWT_REPLACEMENT = "REDACTED_JWT"

# IPv4 — audit cat "IP addresses" (271K)
# Standard dotted-quad. Doesn't match timestamps (which have < 4 dots)
# or version strings (which usually have letters). False-positive risk:
# dotted CSS positions like `background: 192.168.1.1` -- but those
# aren't in URLs. Include a word boundary to avoid matching inside
# longer digit runs (e.g. tracking tokens with embedded `1234.5678`).
# Note: a leading boundary before a leading `0` of `.0.0.0.0` only
# matches when the preceding char is non-numeric, which is what we want.
IPV4_PATTERN = r"(?:^|[^0-9])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?:[^0-9]|$)"
# CH regex caveat: `replaceRegexpAll` doesn't support zero-width
# anchors inside replacement groups cleanly. We use capture + replace.
# Simpler approach: match IPv4 greedily and replace in full. The regex
# below matches a dotted-quad in context without the boundary trick.
IPV4_PATTERN_SIMPLE = r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}"
IPV4_REPLACEMENT = "REDACTED_IP"

# Basic auth embedded in URL — audit cat "Username:password inside a URL" (23K)
# scheme://user:pass@host/... -- the user:pass is Base64-decoded in
# browsers but shows up in URL bytes as `scheme://user:pass@host`.
# Pattern targets the `://` + non-slash + `:` + non-slash + `@` shape.
# Replacement: STRIP the entire `user:pass@` segment. We don't leave a
# marker because any marker that ends in `@` (e.g. REDACTED_BASICAUTH@)
# subsequently matches the email regex layer and gets rewritten to
# REDACTED_EMAIL, which hides the signal. Dropping user:pass@ outright
# gives us `scheme://host/path` with zero trailing PII bytes, which is
# the "Value deleted (row kept)" treatment from the audit.
BASIC_AUTH_PATTERN = r"(https?://)[^:/\s@]+:[^/\s@]+@"
BASIC_AUTH_REPLACEMENT = r"\1"


# OAuth code/state are value-deleted via a MIN-LENGTH guard so short
# query values like `?state=CA` (US-state abbreviation) and `?code=42`
# (generic status / error code) don't false-positive as OAuth tokens.
# Real OAuth code / state values are random 20+ char nonces, so 10 is
# a conservative floor. These two params are special-cased out of the
# generic VALUE_DELETE_PARAMS list and emitted via dedicated SQL below.
OAUTH_CODE_PATTERN = r"(?i)([?&#]|^)code=[^&#]{10,}"
OAUTH_CODE_REPLACEMENT = r"\1code=REDACTED_OAUTH_CODE"
OAUTH_STATE_PATTERN = r"(?i)([?&#]|^)state=[^&#]{10,}"
OAUTH_STATE_REPLACEMENT = r"\1state=REDACTED_OAUTH_STATE"


# ────────────────────────────────────────────────────────────────────────
# SQL builders
# ────────────────────────────────────────────────────────────────────────
def _escape_ch_regex(py_regex: str) -> str:
    r"""Convert a Python regex string to a CH-safe SQL literal.

    Doubles every backslash so the CH regex parser sees `\-`, `\.`, `\d`
    etc. as intended. Doesn't touch single quotes (they're not in any
    of our patterns -- if we add one, use `concat()` instead).
    """
    return py_regex.replace("\\", "\\\\")


def _sql_param_value_delete(inner: str, param_regex: str, kind: str) -> str:
    """Build CH SQL that replaces `<param>=<value>` with
    `<param>=REDACTED_<KIND>`, preserving the param name's original
    casing. Value ends at `&`, `#`, or end of string.
    """
    full = f"(?i)([?&#]|^){param_regex}=[^&#]*"
    return (
        f"replaceRegexpAll({inner}, "
        f"'{_escape_ch_regex(full)}', "
        f"'\\\\1{param_regex.replace(chr(92), '').split('(')[0].split('?')[0]}=REDACTED_{kind}')"
    )
    # Note: param_regex can contain regex metacharacters (e.g.
    # `session(?:id|_id)?`). The replacement needs a LITERAL param name,
    # so we strip the regex noise. For the simple cases in our maps,
    # the first `[a-z_]+` run is the canonical name.


def _sql_scheme_collapse(inner: str, prefix: str, label: str) -> str:
    """`<prefix>...rest...` -> `<prefix><label>` (whole URL collapsed)."""
    # Escape the prefix for use inside the regex (it's a plain string,
    # not a pattern, so escape the special chars).
    escaped = (
        prefix.replace("\\", "\\\\")
              .replace(".", "\\.")
              .replace("+", "\\+")
              .replace("?", "\\?")
              .replace("*", "\\*")
              .replace("(", "\\(")
              .replace(")", "\\)")
              .replace("[", "\\[")
              .replace("]", "\\]")
              .replace("{", "\\{")
              .replace("}", "\\}")
              .replace("|", "\\|")
              .replace("^", "\\^")
              .replace("$", "\\$")
    )
    # Note: we also need to escape backslashes for CH SQL literal.
    ch_escaped = escaped.replace("\\", "\\\\")
    return (
        f"replaceRegexpAll({inner}, "
        f"'^{ch_escaped}.*$', '{prefix}{label}')"
    )


def _sql_regex_replace(inner: str, pattern: str, replacement: str) -> str:
    """Generic chained `replaceRegexpAll` wrapper."""
    return (
        f"replaceRegexpAll({inner}, "
        f"'{_escape_ch_regex(pattern)}', '{replacement}')"
    )


def _sql_postal_mask(inner: str, param_regex: str) -> str:
    """Keep first 3 chars of a postal/zip value: `zip=12345` -> `zip=123XX`."""
    param_literal = param_regex.split("(")[0].rstrip("?")
    full = f"(?i)([?&#]|^){param_regex}=([A-Za-z0-9]{{3}})[A-Za-z0-9]*"
    return (
        f"replaceRegexpAll({inner}, "
        f"'{_escape_ch_regex(full)}', "
        f"'\\\\1{param_literal}=\\\\2XX')"
    )


def wrap_url_column(inner_sql: str) -> str:
    """Legacy 2-pass email-only scrub. Kept for callers that specifically
    want ONLY the email scrub (e.g. compatibility with pre-2026-10-07
    bulk-load scripts). New callers should use `wrap_url_column_full()`.
    """
    ch_regex = _escape_ch_regex(URL_REDACTION_REGEX)
    pass1 = f"replaceRegexpAll({inner_sql}, '{ch_regex}', '{REDACTED_MARKER}')"
    return f"replaceRegexpAll({pass1}, '{ch_regex}', '{REDACTED_MARKER}')"


def redact_base64_url_pii_sql(inner_sql: str) -> str:
    """Base64-decoded email detector + host-collapse. Unchanged from 2026-09-01."""
    ch_email_re = _escape_ch_regex(DECODED_EMAIL_REGEX)
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
    """Comprehensive PII scrub for URLs written to ClickHouse.

    Covers (in applied order):

    1. **Scheme-label collapse** (chrome-extension://, file://, about:, etc.)
       -- audit cat "Internal / browser-extension pages" (2.3M/day). Whole
       URL collapses to `<scheme>://REDACTED_<KIND>`.

    2. **Ad click IDs** replaced by network name -- audit cat "Ad click IDs
       (Google/Facebook/Microsoft/Yandex/X/TikTok/Snapchat/Instagram/
       Pinterest/Hubspot/Mailchimp)". Param name kept so attribution still
       sees the source.

    3. **Query-param value-delete** -- OAuth code/state, session tokens,
       JWT tokens (query form), passwords, API keys, DOB, street
       addresses, health/medical, password-reset tokens, Gmail message
       IDs (query form). Value replaced with `REDACTED_<KIND>`, param
       name preserved.

    4. **Query-param keyed-code** -- phones, person names, usernames,
       national IDs. Value replaced with `REDACTED_<KIND>`.

    5. **Postal/zip first-3-kept** -- `zip=12345` -> `zip=123XX`.

    6. **Google Docs/Drive/Forms/Gmail-path doc IDs** -- `/document/d/<id>`
       -> `/document/d/REDACTED_DOCID`, Gmail `#inbox/<id>` ->
       `#inbox/REDACTED_MSGID`, password-reset path forms.

    7. **Map coordinates** -- `/maps/@<lat>,<lng>,<zoom>` -> `/maps/@REDACTED_COORDS`.

    8. **Whole-URL JWT** -- `eyJ....eyJ....` triple.

    9. **Whole-URL IPv4** -- dotted-quad -> `REDACTED_IP`.

   10. **Basic auth in URL** -- `scheme://user:pass@host` ->
       `scheme://REDACTED_BASICAUTH@host`.

   11. **Email (2-pass)** -- plain + `%40` encoded.

   12. **Base64-encoded email in payload** -- scan base64url substrings,
       collapse URL if any decoded content matches email regex.

    Applied as a single nested CH SQL expression. Each layer is
    idempotent, so running the full scrub twice is a no-op on
    already-clean input. Perf: ~45-55M URLs/sec on the Hetzner box;
    adds roughly 15-25s to the nightly ETL on a 218M-row Lord batch.
    """
    expr = inner_sql

    # Layer 1: scheme-label collapse (whole-URL replacement -- do first
    # so subsequent passes never see the inside of a chrome-extension
    # or file:// URL).
    for prefix, label in SCHEME_COLLAPSE_PREFIXES:
        expr = _sql_scheme_collapse(expr, prefix, label)

    # Layer 2: ad click IDs -> network name.
    for param_regex, network in AD_CLICK_IDS:
        pattern = f"(?i)([?&#]|^){param_regex}=[^&#]*"
        param_literal = param_regex
        replacement = f"\\\\1{param_literal}=REDACTED_{network}"
        expr = _sql_regex_replace(expr, pattern, replacement)

    # Layer 3a: OAuth code / state (min-length guard to avoid false
    # positives on short values like `?state=CA` or `?code=42`).
    expr = _sql_regex_replace(expr, OAUTH_CODE_PATTERN, OAUTH_CODE_REPLACEMENT)
    expr = _sql_regex_replace(expr, OAUTH_STATE_PATTERN, OAUTH_STATE_REPLACEMENT)

    # Layer 3b: value-delete query params.
    for param_regex, kind in VALUE_DELETE_PARAMS:
        pattern = f"(?i)([?&#]|^){param_regex}=[^&#]*"
        param_literal = param_regex.split("(")[0].rstrip("?")
        replacement = f"\\\\1{param_literal}=REDACTED_{kind}"
        expr = _sql_regex_replace(expr, pattern, replacement)

    # Layer 4: keyed-code query params (same shape, different naming).
    for param_regex, kind in KEYED_CODE_PARAMS:
        pattern = f"(?i)([?&#]|^){param_regex}=[^&#]*"
        param_literal = param_regex.split("(")[0].rstrip("?")
        replacement = f"\\\\1{param_literal}=REDACTED_{kind}"
        expr = _sql_regex_replace(expr, pattern, replacement)

    # Layer 5: postal / zip first-3-kept.
    for param_regex in POSTAL_PARAMS:
        param_literal = param_regex.split("(")[0].rstrip("?")
        pattern = f"(?i)([?&#]|^){param_regex}=([A-Za-z0-9]{{3}})[A-Za-z0-9]*"
        replacement = f"\\\\1{param_literal}=\\\\2XX"
        expr = _sql_regex_replace(expr, pattern, replacement)

    # Layer 6: path-anchored doc IDs / Gmail IDs / password-reset.
    for pattern, replacement in GDOCS_PATH_PATTERNS:
        expr = _sql_regex_replace(expr, pattern, replacement)

    # Layer 7: map coordinates.
    expr = _sql_regex_replace(expr, MAP_COORDS_PATTERN, MAP_COORDS_REPLACEMENT)

    # Layer 8: JWT (whole-URL regex).
    expr = _sql_regex_replace(expr, JWT_PATTERN, JWT_REPLACEMENT)

    # Layer 9: IPv4.
    expr = _sql_regex_replace(expr, IPV4_PATTERN_SIMPLE, IPV4_REPLACEMENT)

    # Layer 10: basic auth in URL.
    expr = _sql_regex_replace(expr, BASIC_AUTH_PATTERN, BASIC_AUTH_REPLACEMENT)

    # Layer 11: email 2-pass (preserves URL shape).
    ch_email = _escape_ch_regex(URL_REDACTION_REGEX)
    expr = f"replaceRegexpAll({expr}, '{ch_email}', '{REDACTED_MARKER}')"
    expr = f"replaceRegexpAll({expr}, '{ch_email}', '{REDACTED_MARKER}')"

    # Layer 12: base64-encoded email -> host-collapse.
    expr = redact_base64_url_pii_sql(expr)

    return expr
