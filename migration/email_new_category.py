#!/usr/bin/env python3
"""Email the 4 stakeholders when a non-canonical BRAND CATEGORY is invented.

User mandate 2026-06-13 (see .cursor/rules/canonical-brand-category.mdc):
> "if someone doesn't fit within a canonical list and you need a new one
>  that's fine you can use it but then email jessie, me, anastasia and
>  liz and subject 'Create New Category' then list the category created"

Usage (CLI):
    python3 migration/email_new_category.py \
        --category "NEW_CATEGORY_NAME" \
        --subjects "Subject 1, Subject 2" \
        --rationale "Why none of the canonical options fit"

Usage (programmatic):
    from migration.email_new_category import notify_new_category
    notify_new_category(
        category="NEW_CATEGORY_NAME",
        subjects=["Subject 1", "Subject 2"],
        rationale="Why none of the canonical options fit",
    )

Best-effort: SES failures are logged, never raised - sending this email
must NEVER block the actual profile launch.
"""
from __future__ import annotations
import argparse, os, re, sys, json, time
from pathlib import Path
from typing import Iterable

RECIPIENTS = [
    "jessie@crosswalknyc.com",
    "jenna@crosswalknyc.com",
    "anastasia@crosswalknyc.com",
    "liz@crosswalknyc.com",
]
SOURCE = "BehavioralGraph <jenna@crosswalknyc.com>"
SUBJECT = "Create New Category"
AWS_REGION = "us-east-2"

# Dedupe: at most one email per (category, day) so the pipeline safety-net
# call doesn't spam if it fires on multiple profiles in the same batch.
#
# 2026-08-27: the stamp is now PRIMARILY an S3 object shared by every
# host that can send this email (the Mac, the box, any future runner).
# The old local file at $BG_STATE_DIR (default /var/lib/bg) silently
# never persisted on non-root hosts (mkdir /var/lib/bg fails on macOS
# and on Render's ephemeral filesystem), so the dedupe never held
# there. The local file is kept as a best-effort secondary for when S3
# is unreachable.
STAMP_DIR = Path(os.environ.get("BG_STATE_DIR", "/var/lib/bg"))
STAMP_FILE = STAMP_DIR / "new_category_seen.json"
STAMP_BUCKET = "dashboard-inputs"
STAMP_S3_KEY = "system/new_category_seen.json"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _stamp_helpers():
    """Import the shared ETag-CAS helper lazily (module must stay
    importable and CLI-runnable even where migration/ isn't a package
    on sys.path). Returns (read_json_with_etag, update_json) or None."""
    try:
        from migration.s3_json_state import read_json_with_etag, update_json
    except ImportError:
        try:
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.insert(0, here)
            from s3_json_state import read_json_with_etag, update_json  # type: ignore
        except ImportError:
            return None
    return read_json_with_etag, update_json


def _trim_old(data: dict) -> dict:
    """Drop stamp entries older than 30 days."""
    cutoff = time.strftime("%Y-%m-%d",
                           time.gmtime(time.time() - 30 * 86400))
    return {k: v for k, v in (data or {}).items()
            if k.split("|", 1)[0] >= cutoff}


def _already_sent_today(category: str) -> bool:
    key = f"{_today()}|{category.upper()}"
    # Primary: the shared S3 stamp.
    try:
        helpers = _stamp_helpers()
        if helpers is not None:
            read_json_with_etag, _ = helpers
            data, _etag = read_json_with_etag(STAMP_BUCKET, STAMP_S3_KEY)
            if isinstance(data, dict) and key in data:
                return True
            if isinstance(data, dict):
                return False  # S3 readable and key absent: authoritative
    except Exception as e:
        print(f"[email_new_category] S3 dedupe stamp read failed "
              f"(falling back to local): {e}", file=sys.stderr)
    # Fallback: the local file (only meaningful on hosts where the
    # write actually persists, e.g. the box running as root).
    try:
        if not STAMP_FILE.exists():
            return False
        data = json.loads(STAMP_FILE.read_text())
    except Exception:
        return False
    return key in data


def _mark_sent_today(category: str) -> None:
    key = f"{_today()}|{category.upper()}"
    now = int(time.time())
    # Primary: shared S3 stamp via the ETag-CAS helper (last writer
    # loses nothing across concurrent senders).
    try:
        helpers = _stamp_helpers()
        if helpers is not None:
            _, update_json = helpers

            def _mutate(obj):
                obj = _trim_old(obj if isinstance(obj, dict) else {})
                obj[key] = now
                return obj

            update_json(STAMP_BUCKET, STAMP_S3_KEY, _mutate, default={})
    except Exception as e:
        print(f"[email_new_category] S3 dedupe stamp update failed: {e}",
              file=sys.stderr)
    # Secondary: best-effort local file.
    try:
        STAMP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(STAMP_FILE.read_text()) if STAMP_FILE.exists() else {}
        except Exception:
            data = {}
        data = _trim_old(data)
        data[key] = now
        STAMP_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[email_new_category] local dedupe stamp update failed "
              f"(non-fatal; S3 stamp is primary): {e}", file=sys.stderr)


def _matching_hostmap_section(category: str) -> str | None:
    """Return the hostmap brand-section name the new dashboard category
    mirrors (case + punctuation insensitive), or None.

    The dashboard profile-category list and the hostmap brand taxonomy
    are separate lists; a new dashboard value can share a name with a
    hostmap section that already exists (TECHNOLOGY/DEVICE, 2026-08-27
    confusion). When that happens the email calls it out so nobody
    reads the notification as a hostmap change.

    Sources: the maintained local section mirror SECTION_TO_COLUMNS in
    migration/synth_hostmap_augment.py first (no network), then the
    live section list when reachable. Compound sections ('Talent,
    Actor') match on the full string or any comma component.
    Best-effort: any failure returns None and the email still sends.
    """
    def _norm(s) -> str:
        return re.sub(r"[^a-z0-9+&]", "", str(s).lower())

    want = _norm(category)
    if not want:
        return None

    def _first_match(section_names) -> str | None:
        for section in section_names:
            parts = [str(section)] + [p.strip()
                                      for p in str(section).split(",")]
            for part in parts:
                if _norm(part) == want:
                    return part
        return None

    try:
        try:
            from migration.synth_hostmap_augment import (SECTION_TO_COLUMNS,
                                                         _load_hostmap)
        except ImportError:
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.insert(0, here)
            from synth_hostmap_augment import (SECTION_TO_COLUMNS,  # type: ignore
                                               _load_hostmap)
        hit = _first_match(SECTION_TO_COLUMNS.keys())
        if hit:
            return hit
        return _first_match(_load_hostmap().keys())
    except Exception as e:
        print(f"[email_new_category] hostmap-section mirror check skipped: {e}",
              file=sys.stderr)
        return None


def _build_bodies(category: str, subjects: list[str],
                  rationale: str) -> tuple[str, str]:
    """Render the (html, text) email bodies. Split out from the send so
    the copy can be previewed without an SES call."""
    mirror = _matching_hostmap_section(category)

    subj_html = (
        "<ul>" + "".join(f"<li>{s}</li>" for s in subjects) + "</ul>"
        if subjects else "<p><em>(no subjects supplied)</em></p>"
    )
    scope_html = (
        f"<p>This adds a value to the <b>dashboard profile-category "
        f"list</b> (the canonical list that labels profiles in the "
        f"Select Profile tree), <b>not</b> the hostmap brand "
        f"taxonomy.</p>"
    )
    if mirror:
        scope_html += (
            f"<p><b>{category}</b> mirrors the existing hostmap brand "
            f"section of the same name; the hostmap itself is "
            f"unchanged.</p>"
        )
    body_html = (
        f"<p>The Behavioral Graph batch runner just used a new "
        f"<b>BRAND CATEGORY</b> value that is not in the canonical "
        f"list.</p>"
        f"{scope_html}"
        f"<table style='border-collapse:collapse;border:1px solid #ddd;"
        f"font-size:14px;margin:12px 0;'>"
        f"  <tr><td style='padding:6px 12px;border:1px solid #ddd;'>New category</td>"
        f"      <td style='padding:6px 12px;border:1px solid #ddd;"
        f"font-weight:600;font-family:Menlo,monospace;'>{category}</td></tr>"
        f"  <tr><td style='padding:6px 12px;border:1px solid #ddd;'>Triggering subject(s)</td>"
        f"      <td style='padding:6px 12px;border:1px solid #ddd;'>{subj_html}</td></tr>"
        f"  <tr><td style='padding:6px 12px;border:1px solid #ddd;'>Rationale</td>"
        f"      <td style='padding:6px 12px;border:1px solid #ddd;'>"
        f"{rationale or '<em>(none supplied)</em>'}</td></tr>"
        f"</table>"
        f"<p>Decide whether to formally add it to "
        f"<code>MASTER_CATEGORIES</code> in "
        f"<code>bg-webapp/iq_rankers.py</code>, "
        f"<code>bg-webapp/templates/index.html</code>, and "
        f"<code>bg-webapp/templates/admin.html</code> so it surfaces "
        f"in the dashboard dropdowns + sidebar.</p>"
        f"<p style='color:#888;font-size:12px;'>- "
        f"BehavioralGraph batch runner</p>"
    )
    scope_text = (
        "This adds a value to the dashboard profile-category list (the\n"
        "canonical list that labels profiles in the Select Profile tree),\n"
        "not the hostmap brand taxonomy.\n"
    )
    if mirror:
        scope_text += (
            f"{category} mirrors the existing hostmap brand section of the\n"
            f"same name; the hostmap itself is unchanged.\n"
        )
    body_text = (
        f"Create New Category\n"
        f"-------------------\n"
        f"New category:      {category}\n"
        f"Triggering subjects: {', '.join(subjects) if subjects else '(none)'}\n"
        f"Rationale:         {rationale or '(none)'}\n\n"
        f"{scope_text}\n"
        f"To formalize, add to MASTER_CATEGORIES in:\n"
        f"  - bg-webapp/iq_rankers.py\n"
        f"  - bg-webapp/templates/index.html\n"
        f"  - bg-webapp/templates/admin.html\n"
    )
    return body_html, body_text


def notify_new_category(category: str,
                        subjects: Iterable[str] | None = None,
                        rationale: str = "",
                        *,
                        force: bool = False,
                        recipients: Iterable[str] | None = None) -> bool:
    """Send the 'Create New Category' SES email. Returns True if sent (or
    skipped via dedupe), False on SES failure. Never raises."""
    category = (category or "").strip()
    if not category:
        print("[email_new_category] empty category - nothing to do",
              file=sys.stderr)
        return False
    if not force and _already_sent_today(category):
        print(f"[email_new_category] dedupe: already emailed {category!r} "
              f"today; skipping")
        return True

    subjects = list(subjects or [])
    to = list(recipients) if recipients else list(RECIPIENTS)

    body_html, body_text = _build_bodies(category, subjects, rationale)

    try:
        import boto3
        ses = boto3.client("ses", region_name=AWS_REGION)
        ses.send_email(
            Source=SOURCE,
            Destination={"ToAddresses": to},
            Message={
                "Subject": {"Data": SUBJECT},
                "Body": {
                    "Html": {"Data": body_html},
                    "Text": {"Data": body_text},
                },
            },
        )
        print(f"[email_new_category] sent 'Create New Category' to "
              f"{', '.join(to)}  (category={category!r})")
        _mark_sent_today(category)
        return True
    except Exception as e:
        print(f"[email_new_category] SES send failed: {e}", file=sys.stderr)
        return False


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--category", required=True,
                   help="The new BRAND CATEGORY value being introduced.")
    p.add_argument("--subjects", default="",
                   help="Comma-separated list of subjects triggering this.")
    p.add_argument("--rationale", default="",
                   help="One-line rationale for why no canonical value fits.")
    p.add_argument("--force", action="store_true",
                   help="Send even if dedupe says already-sent-today.")
    p.add_argument("--to", default="",
                   help="Override recipients (comma-separated). Defaults "
                        "to the 4 stakeholders.")
    args = p.parse_args(argv)
    subjects = [s.strip() for s in args.subjects.split(",") if s.strip()]
    to = [r.strip() for r in args.to.split(",") if r.strip()] or None
    ok = notify_new_category(args.category, subjects, args.rationale,
                             force=args.force, recipients=to)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
