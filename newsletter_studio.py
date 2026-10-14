"""The Read studio login and standalone White Papers.

Staff at a crosswalknyc.com address can open the newsletter desk without
seeing the rest of Admin. White papers are priced files that are not
tied to a send.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import traceback
import uuid
from pathlib import Path

from flask import Response, jsonify, redirect, render_template, request, session
from werkzeug.utils import secure_filename

STUDIO_PATH = "/the-read/studio"
STUDIO_LOGIN_PATH = "/the-read/studio/login"
PAPERS_PATH = "/the-read/papers"
STAFF_DOMAIN = "crosswalknyc.com"
CODE_TTL_SEC = 15 * 60
CODE_RESEND_SEC = 45
CODE_MAX_TRIES = 5
CODES_KEY = "system/newsletter/studio_codes.json"
PAPER_FILE_KEY = "system/newsletter/white_papers/{pid}/{name}"
PAPER_LEADS_KEY = "system/newsletter/white_papers/{pid}/leads.json"
RESERVED_SLUGS = {
    "studio", "papers", "login", "logout", "signup", "archive",
    "images", "index", "index.html",
}
PROM_FROM = "Prometheus <prometheus@crosswalknyc.com>"
PROM_ADDR = "prometheus@crosswalknyc.com"


def _nl():
    import newsletter as nl
    return nl


def staff_email(value):
    nl = _nl()
    email = nl._valid_email(value)
    if not email:
        return ""
    if not email.endswith("@" + STAFF_DOMAIN):
        return ""
    return email


def _user_staff_email(user):
    if not user:
        return ""
    for key in ("email", "username"):
        hit = staff_email(user.get(key) or "")
        if hit:
            return hit
    return ""


def studio_actor():
    """Email of the person allowed on the newsletter desk, or empty."""
    parked = staff_email(session.get("nl_studio") or "")
    if parked:
        return parked
    if "username" not in session:
        return ""
    try:
        from app import get_current_user
        user = get_current_user()
    except Exception:
        user = None
    if not user:
        return ""
    role = (user.get("role") or "").strip()
    if role in ("admin", "super_admin"):
        return _user_staff_email(user) or staff_email(session.get("username") or "") or "admin@crosswalknyc.com"
    return _user_staff_email(user)


def studio_ok():
    return bool(studio_actor())


def _codes_store():
    nl = _nl()
    raw = nl._get_json(CODES_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    codes = raw.get("codes")
    if not isinstance(codes, dict):
        codes = {}
    return {"codes": codes}


def _put_codes_store(store):
    _nl()._put_json(CODES_KEY, store)


def _hash_code(email, code):
    secret = os.environ.get("SECRET_KEY") or "newsletter-studio"
    return hashlib.sha256(f"{secret}|{email}|{code}".encode("utf-8")).hexdigest()


def _new_code():
    n = int(uuid.uuid4().hex[:8], 16) % 1_000_000
    return f"{n:06d}"


def request_studio_code(email):
    email = staff_email(email)
    if not email:
        return False, "Use your crosswalknyc.com email."
    store = _codes_store()
    row = store["codes"].get(email) or {}
    try:
        sent_at = float(row.get("sent_at") or 0)
    except (TypeError, ValueError):
        sent_at = 0
    if sent_at and (time.time() - sent_at) < CODE_RESEND_SEC:
        return False, "A code was just sent. Check that inbox, or wait a minute."
    code = _new_code()
    store["codes"][email] = {
        "hash": _hash_code(email, code),
        "until": time.time() + CODE_TTL_SEC,
        "sent_at": time.time(),
        "tries": 0,
    }
    _put_codes_store(store)
    try:
        _send_studio_code(email, code)
    except Exception:
        traceback.print_exc()
        return False, "Could not send the code. Try again, or write hello@crosswalknyc.com."
    payload = {"ok": True, "message": "Check that inbox for a six-digit code."}
    if _nl()._use_local():
        payload["dev_code"] = code
    return True, payload


def verify_studio_code(email, code):
    email = staff_email(email)
    code = re.sub(r"\D", "", str(code or ""))
    if not email:
        return False, "Use your crosswalknyc.com email."
    if len(code) != 6:
        return False, "Enter the six-digit code."
    store = _codes_store()
    row = store["codes"].get(email) or {}
    try:
        until = float(row.get("until") or 0)
        tries = int(row.get("tries") or 0)
    except (TypeError, ValueError):
        until, tries = 0, 0
    if not row.get("hash") or until < time.time():
        return False, "That code expired. Send a new one."
    if tries >= CODE_MAX_TRIES:
        return False, "Too many tries. Send a new code."
    if not hmac.compare_digest(row.get("hash") or "", _hash_code(email, code)):
        row["tries"] = tries + 1
        store["codes"][email] = row
        _put_codes_store(store)
        return False, "That code does not match."
    store["codes"].pop(email, None)
    _put_codes_store(store)
    return True, email


def open_studio_session(email):
    session["nl_studio"] = email
    session.permanent = True
    session.modified = True


def close_studio_session():
    session.pop("nl_studio", None)


def _send_studio_code(email, code):
    nl = _nl()
    if nl._use_local():
        return
    subject = "Your Read studio code"
    text = (
        f"Your code is {code}.\n"
        "It expires in 15 minutes.\n\n"
        "If you did not ask for this, ignore the email."
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#0C1618;">'
        f"<p>Your code is <strong>{code}</strong>.</p>"
        "<p>It expires in 15 minutes.</p>"
        "<p style=\"color:#5C6560;font-size:13px;\">If you did not ask for this, ignore the email.</p>"
        "</div>"
    )
    nl._ses_client().send_email(
        Source=PROM_ADDR,
        Destination={
            "ToAddresses": [email],
            "BccAddresses": ["jenna@crosswalknyc.com"],
        },
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text, "Charset": "UTF-8"},
                "Html": {"Data": html, "Charset": "UTF-8"},
            },
        },
    )


def empty_paper():
    return {
        "id": "",
        "slug": "",
        "title": "",
        "description": "",
        "price_usd": 0.0,
        "filename": "",
        "original_name": "",
        "enabled": True,
        "created_at": "",
        "updated_at": "",
        "created_by": "",
    }


def _papers(state=None):
    st = state if isinstance(state, dict) else _nl().load_state_raw()
    rows = st.get("white_papers")
    return rows if isinstance(rows, list) else []


def _paper(state, pid):
    pid = re.sub(r"[^A-Za-z0-9._-]", "", pid or "")
    for row in _papers(state):
        if row.get("id") == pid or row.get("slug") == pid:
            return row
    return None


def _slugify(title, taken):
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:48] or "paper"
    if base in RESERVED_SLUGS:
        base = "paper-" + base
    slug = base
    n = 2
    while slug in taken or slug in RESERVED_SLUGS:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _public_paper(row, settings=None, include_stats=True):
    nl = _nl()
    pid = row.get("id") or ""
    price = 0.0
    try:
        price = float(row.get("price_usd") or 0)
    except (TypeError, ValueError):
        price = 0.0
    stats = {"leads": 0, "paid": 0, "downloads": 0, "unique_downloads": 0, "revenue_usd": 0}
    if include_stats and pid:
        stats = nl.download_stats_from_store(nl._get_json(PAPER_LEADS_KEY.format(pid=pid)) or {})
    return {
        "id": pid,
        "slug": row.get("slug") or "",
        "title": row.get("title") or "White paper",
        "description": row.get("description") or "",
        "price_usd": round(price, 2),
        "paid": price >= 0.50,
        "filename": row.get("filename") or "",
        "original_name": row.get("original_name") or "",
        "enabled": bool(row.get("enabled", True)),
        "has_file": bool(row.get("filename")),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "created_by": row.get("created_by") or "",
        "url": f"{PAPERS_PATH}/{row.get('slug') or pid}",
        "download_stats": stats,
    }


def public_papers(state=None):
    return [
        _public_paper(row, include_stats=False)
        for row in _papers(state)
        if row.get("enabled") and row.get("filename")
    ]


def _save_paper_fields(row, body, taken_slugs):
    title = (body.get("title") or row.get("title") or "").strip()[:160]
    if title:
        row["title"] = title
    if "description" in body:
        row["description"] = (body.get("description") or "").strip()[:800]
    if "price_usd" in body:
        try:
            price = float(body.get("price_usd") or 0)
        except (TypeError, ValueError):
            price = 0.0
        row["price_usd"] = max(0.0, round(price, 2))
    if "enabled" in body:
        row["enabled"] = bool(body.get("enabled"))
    wanted = re.sub(r"[^a-z0-9-]+", "", (body.get("slug") or row.get("slug") or "").lower())[:48]
    if wanted and wanted not in RESERVED_SLUGS and wanted not in (taken_slugs - {row.get("slug")}):
        row["slug"] = wanted
    elif not row.get("slug"):
        row["slug"] = _slugify(row.get("title") or "paper", taken_slugs)
    return row


def create_paper(body, actor=""):
    nl = _nl()
    now = nl._utcnow()
    pid = "wp-" + uuid.uuid4().hex[:10]
    created = {"ok": None}

    def mutate(st):
        rows = st.setdefault("white_papers", [])
        taken = {r.get("slug") for r in rows if r.get("slug")}
        row = empty_paper()
        row["id"] = pid
        row["created_at"] = now
        row["updated_at"] = now
        row["created_by"] = actor or ""
        row["enabled"] = True
        _save_paper_fields(row, body or {}, taken)
        if not row.get("title"):
            created["ok"] = "Add a title."
            return None
        rows.insert(0, row)
        created["ok"] = row
        return st

    nxt = nl._cas_update_state(mutate)
    if isinstance(created["ok"], str):
        return None, created["ok"]
    row = _paper(nxt, pid)
    if not row:
        return None, "Could not save that paper."
    return row, None


def update_paper(pid, body):
    nl = _nl()

    def mutate(st):
        row = _paper(st, pid)
        if not row:
            return None
        taken = {r.get("slug") for r in _papers(st) if r.get("slug")}
        _save_paper_fields(row, body or {}, taken)
        row["updated_at"] = nl._utcnow()
        return st

    nxt = nl._cas_update_state(mutate)
    row = _paper(nxt, pid)
    if not row:
        return None, "paper not found"
    return row, None


def delete_paper(pid):
    nl = _nl()

    def mutate(st):
        before = len(st.get("white_papers") or [])
        st["white_papers"] = [r for r in (st.get("white_papers") or []) if r.get("id") != pid]
        return st if len(st.get("white_papers") or []) != before else None

    nxt = nl._cas_update_state(mutate)
    return _paper(nxt, pid) is None


def store_paper_file(pid, raw, filename):
    nl = _nl()
    name = nl._safe_filename(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in nl.ALLOWED_ATTACH_EXT:
        return None, "use a pdf, deck, spreadsheet, doc, or zip"
    if not raw:
        return None, "that file is empty"
    if len(raw) > nl.MAX_ATTACH_BYTES:
        return None, "file is over 25 MB"
    nl._put_bytes(PAPER_FILE_KEY.format(pid=pid, name=name), raw, nl._content_type_for(name))

    def mutate(st):
        row = _paper(st, pid)
        if not row:
            return None
        row["filename"] = name
        row["original_name"] = (filename or name).strip()
        if not row.get("title"):
            row["title"] = Path(filename or name).stem.replace("_", " ")
        row["updated_at"] = nl._utcnow()
        return st

    nxt = nl._cas_update_state(mutate)
    row = _paper(nxt, pid)
    if not row:
        return None, "paper not found"
    return row, None


def record_paper_lead(pid, email, name="", paid=False, amount_cents=0, session_id=""):
    nl = _nl()
    store = nl._get_json(PAPER_LEADS_KEY.format(pid=pid)) or {"leads": {}}
    leads = store.setdefault("leads", {})
    row = leads.get(email) or {
        "email": email,
        "name": name,
        "paid": False,
        "amount_cents": 0,
        "session_id": "",
        "download_count": 0,
        "first_at": nl._utcnow(),
    }
    if name:
        row["name"] = name
    if paid:
        row["paid"] = True
        row["amount_cents"] = int(amount_cents or row.get("amount_cents") or 0)
        row["session_id"] = session_id or row.get("session_id") or ""
        row["paid_at"] = nl._utcnow()
    leads[email] = row
    nl._put_json(PAPER_LEADS_KEY.format(pid=pid), store)
    return row


def mark_paper_downloaded(pid, email):
    nl = _nl()
    store = nl._get_json(PAPER_LEADS_KEY.format(pid=pid)) or {"leads": {}}
    leads = store.setdefault("leads", {})
    row = leads.get(email) or {"email": email, "download_count": 0}
    row["download_count"] = int(row.get("download_count") or 0) + 1
    row["last_download_at"] = nl._utcnow()
    leads[email] = row
    nl._put_json(PAPER_LEADS_KEY.format(pid=pid), store)


def fulfill_paid_paper(obj):
    md = (obj or {}).get("metadata") or {}
    if (md.get("purpose") or "") != "newsletter_white_paper":
        return
    pid = (md.get("paper_id") or "").strip()
    email = _nl()._valid_email(md.get("email") or (obj or {}).get("customer_email") or "")
    if not pid or not email:
        print("[newsletter] paid white paper missing paper or email")
        return
    record_paper_lead(
        pid, email,
        paid=True,
        amount_cents=int((obj or {}).get("amount_total") or 0),
        session_id=(obj or {}).get("id") or "",
    )
    print(f"[newsletter] paid white paper unlocked {pid} -> {email}")


def _paper_file_response(row, email):
    nl = _nl()
    pid = row.get("id")
    filename = row.get("original_name") or row.get("filename")
    key = PAPER_FILE_KEY.format(pid=pid, name=row["filename"])
    mark_paper_downloaded(pid, email)
    url = nl._presigned_get(key, filename)
    if url:
        return redirect(url, code=302)
    raw = nl._get_bytes(key)
    if raw is None:
        return nl._download_page("The file is missing. Write hello@crosswalknyc.com.", ok=False), 404
    return Response(
        raw,
        mimetype=nl._content_type_for(filename),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _start_paper_checkout(row, email, settings):
    nl = _nl()
    title = row.get("title") or "Crosswalk white paper"
    price = float(row.get("price_usd") or 0)
    try:
        import billing
        if not billing.is_enabled():
            return nl._download_page(
                "Card checkout is not available right now. Write hello@crosswalknyc.com.",
                title=title, ok=False,
            ), 503
        base = nl._public_base(settings)
        slug = row.get("slug") or row.get("id")
        sess = billing.create_guest_checkout_session(
            amount_usd=price,
            email=email,
            success_url=f"{base}{PAPERS_PATH}/{slug}/paid?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}{PAPERS_PATH}/{slug}",
            product_name=title,
            metadata={
                "purpose": "newsletter_white_paper",
                "paper_id": row.get("id"),
                "email": email,
            },
        )
        url = sess.get("url")
        if not url:
            return nl._download_page(
                "Checkout did not start. Try again.",
                title=title, form=True, cid=slug, paid=True, price=price, ok=False,
            ), 502
        return redirect(url, code=302)
    except Exception as e:
        print(f"[newsletter] paper checkout failed: {e}")
        return nl._download_page(
            "Card checkout failed. Write hello@crosswalknyc.com if it happens again.",
            title=title, form=True, cid=row.get("slug") or row.get("id"), paid=True, price=price, ok=False,
        ), 502


_ATTACHED = False


def attach_studio(bp):
    """Add studio login and white-paper routes to the newsletter blueprint."""
    global _ATTACHED
    if _ATTACHED:
        return
    _ATTACHED = True

    @bp.route("/the-read/studio/login", methods=["GET", "POST"])
    def studio_login_page():
        if request.method == "GET":
            if studio_ok():
                return redirect(STUDIO_PATH)
            return render_template(
                "newsletter_studio.html",
                mode="login",
                actor="",
                address=_nl()._company_address(),
            )
        body = request.get_json(silent=True) or request.form or {}
        ok, payload = request_studio_code(body.get("email") or "")
        if not ok:
            return jsonify({"success": False, "error": payload}), 400
        return jsonify({"success": True, **payload})

    @bp.route("/the-read/studio/verify", methods=["POST"])
    def studio_verify():
        body = request.get_json(silent=True) or request.form or {}
        ok, payload = verify_studio_code(body.get("email") or "", body.get("code") or "")
        if not ok:
            return jsonify({"success": False, "error": payload}), 400
        open_studio_session(payload)
        return jsonify({"success": True, "redirect": STUDIO_PATH})

    @bp.route("/the-read/studio/logout", methods=["GET", "POST"])
    def studio_logout():
        close_studio_session()
        if request.method == "POST":
            return jsonify({"success": True, "redirect": STUDIO_LOGIN_PATH})
        return redirect(STUDIO_LOGIN_PATH)

    @bp.route("/the-read/studio")
    @bp.route("/the-read/studio/")
    def studio_home():
        actor = studio_actor()
        if not actor:
            return redirect(STUDIO_LOGIN_PATH)
        return render_template(
            "newsletter_studio.html",
            mode="desk",
            actor=actor,
            address=_nl()._company_address(),
        )

    @bp.route("/api/admin/newsletter/papers", methods=["POST"])
    def api_create_paper():
        guard = _nl()._admin_guard()
        if guard is not None:
            return guard
        body = request.get_json(silent=True) or {}
        row, err = create_paper(body, actor=studio_actor())
        if err:
            return jsonify({"success": False, "error": err}), 400
        return jsonify({"success": True, "paper": _public_paper(row), **_nl()._overview_payload()})

    @bp.route("/api/admin/newsletter/papers/<pid>", methods=["PUT"])
    def api_update_paper(pid):
        guard = _nl()._admin_guard()
        if guard is not None:
            return guard
        row, err = update_paper(pid, request.get_json(silent=True) or {})
        if err:
            return jsonify({"success": False, "error": err}), 404
        return jsonify({"success": True, "paper": _public_paper(row), **_nl()._overview_payload()})

    @bp.route("/api/admin/newsletter/papers/<pid>", methods=["DELETE"])
    def api_delete_paper(pid):
        guard = _nl()._admin_guard()
        if guard is not None:
            return guard
        if not delete_paper(pid):
            return jsonify({"success": False, "error": "paper not found"}), 404
        return jsonify(_nl()._overview_payload())

    @bp.route("/api/admin/newsletter/papers/<pid>/file", methods=["POST"])
    def api_upload_paper_file(pid):
        guard = _nl()._admin_guard()
        if guard is not None:
            return guard
        if not _paper(_nl().load_state_raw(), pid):
            return jsonify({"success": False, "error": "paper not found"}), 404
        up = request.files.get("file")
        if not up or not up.filename:
            return jsonify({"success": False, "error": "choose a file to attach"}), 400
        row, err = store_paper_file(pid, up.read(), secure_filename(up.filename) or up.filename)
        if err:
            return jsonify({"success": False, "error": err}), 400
        return jsonify({
            "success": True,
            "paper": _public_paper(row),
            **_nl()._overview_payload(),
        })

    @bp.route("/the-read/papers")
    @bp.route("/the-read/papers/")
    def public_papers_index():
        state = _nl().load_state_raw()
        return render_template(
            "the_read_papers.html",
            papers=public_papers(state),
            address=_nl()._company_address(),
        )

    @bp.route("/the-read/papers/<slug>", methods=["GET", "POST"])
    def public_paper_gate(slug):
        nl = _nl()
        state = nl.load_state_raw()
        row = _paper(state, slug)
        if not row or not row.get("enabled") or not row.get("filename"):
            return nl._download_page("This paper is not available.", ok=False), 404
        settings = state.get("settings") or {}
        title = row.get("title") or "White paper"
        price = float(row.get("price_usd") or 0)
        paid = price >= 0.50
        action = f"{PAPERS_PATH}/{row.get('slug') or row.get('id')}"
        if request.method == "GET":
            return _paper_form_page(
                f"Enter your email to download {title}." if not paid
                else f"Enter your email and pay ${price:.2f} to download {title}.",
                title=title, form=True, action=action, paid=paid, price=price,
            )
        email = nl._valid_email(request.form.get("email") or (request.get_json(silent=True) or {}).get("email"))
        if not email:
            return _paper_form_page(
                "That email is not valid. Try again.",
                title=title, form=True, action=action, paid=paid, price=price, ok=False,
            ), 400
        name = (request.form.get("name") or "").strip()[:80]
        record_paper_lead(row.get("id"), email, name=name)
        if paid:
            return _start_paper_checkout(row, email, settings)
        token = nl.sign_token({"c": row.get("id"), "e": email, "p": "p"})
        return redirect(f"{action}/file/{token}", code=302)

    @bp.route("/the-read/papers/<slug>/paid")
    def public_paper_paid(slug):
        nl = _nl()
        state = nl.load_state_raw()
        row = _paper(state, slug)
        if not row:
            return nl._download_page("This paper is not available.", ok=False), 404
        title = row.get("title") or "White paper"
        session_id = (request.args.get("session_id") or "").strip()
        if not session_id:
            return _paper_form_page(
                "Payment did not finish. Enter your email again to retry.",
                title=title, form=True,
                action=f"{PAPERS_PATH}/{row.get('slug') or row.get('id')}",
                paid=True, price=float(row.get("price_usd") or 0),
            ), 400
        try:
            import billing
            sess = billing.retrieve_checkout_session(session_id)
        except Exception as e:
            print(f"[newsletter] retrieve paper checkout failed: {e}")
            return nl._download_page(
                "We could not confirm that payment. Try again or write hello@crosswalknyc.com.",
                title=title, ok=False,
            ), 400
        if (sess.get("payment_status") or "") != "paid":
            return nl._download_page(
                "Payment is not complete yet. If you were charged, write hello@crosswalknyc.com.",
                title=title, ok=False,
            ), 400
        md = sess.get("metadata") or {}
        if (md.get("purpose") or "") != "newsletter_white_paper" or (md.get("paper_id") or "") != row.get("id"):
            return nl._download_page("That payment does not match this file.", title=title, ok=False), 400
        email = nl._valid_email(md.get("email") or sess.get("customer_email") or "")
        fulfill_paid_paper({
            "id": sess.get("id"),
            "amount_total": sess.get("amount_total") or 0,
            "customer_email": email,
            "metadata": md,
        })
        if not email:
            return nl._download_page(
                "Payment landed. Write hello@crosswalknyc.com and we will send the file.",
                title=title,
            ), 200
        token = nl.sign_token({"c": row.get("id"), "e": email, "p": "p"})
        return redirect(f"{PAPERS_PATH}/{row.get('slug') or row.get('id')}/file/{token}", code=302)

    @bp.route("/the-read/papers/<slug>/file/<token>")
    def public_paper_file(slug, token):
        nl = _nl()
        state = nl.load_state_raw()
        row = _paper(state, slug)
        payload = nl.verify_token(token)
        if not row or not payload or payload.get("p") != "p" or payload.get("c") != row.get("id"):
            return nl._download_page("This download link is not valid.", ok=False), 400
        email = nl._valid_email(payload.get("e"))
        if float(row.get("price_usd") or 0) >= 0.50:
            lead = (((nl._get_json(PAPER_LEADS_KEY.format(pid=row.get("id"))) or {}).get("leads") or {}).get(email) or {})
            if not lead.get("paid"):
                return _paper_form_page(
                    "Payment is still required for this file.",
                    title=row.get("title") or "",
                    form=True,
                    action=f"{PAPERS_PATH}/{row.get('slug') or row.get('id')}",
                    paid=True,
                    price=float(row.get("price_usd") or 0),
                ), 402
        return _paper_file_response(row, email)


def _paper_form_page(message, ok=True, form=False, action="", title="", paid=False, price=0):
    """Same gate as a newsletter download, pointed at a paper URL."""
    nl = _nl()
    tone = "#5E7E12" if ok else "#8E3FA8"
    heading = _esc(title or "White paper")
    box = ""
    if form and action:
        price_line = ""
        button = "Download"
        if paid and price:
            price_line = (
                f'<div style="font-size:14px;color:#5C6560;margin:0 0 16px;">'
                f"${price:.2f} · card checkout</div>"
            )
            button = f"Pay ${price:.2f} and download"
        box = (
            f'<form method="post" action="{_esc(action)}" style="margin-top:22px;">'
            '<label style="display:block;font-size:12px;color:#5C6560;margin:0 0 6px;">Email</label>'
            '<input type="email" name="email" required placeholder="you@company.com" '
            'style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #C9C6BA;'
            'border-radius:8px;font-size:16px;background:#fff;color:#0C1618;">'
            '<label style="display:block;font-size:12px;color:#5C6560;margin:14px 0 6px;">Name (optional)</label>'
            '<input type="text" name="name" placeholder="Your name" '
            'style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #C9C6BA;'
            'border-radius:8px;font-size:16px;background:#fff;color:#0C1618;">'
            f"{price_line}"
            f'<button type="submit" style="margin-top:18px;width:100%;background:#0C1618;'
            "color:#E9E8E1;border:0;border-radius:8px;padding:14px 18px;"
            f'font-size:15px;font-weight:700;cursor:pointer;">{_esc(button)}</button></form>'
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading} · Crosswalk</title></head>
<body style="margin:0;background:#E9E8E1;color:#0C1618;
font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:480px;margin:72px auto;padding:0 24px;">
<div style="font-size:11px;letter-spacing:2.6px;text-transform:uppercase;
color:#5C6560;">Crosswalk / White Papers</div>
<h1 style="font-size:28px;margin:16px 0 12px;">{heading}</h1>
<p style="font-size:16px;line-height:1.5;color:#5C6560;">{_esc(message)}</p>
{box}
<p style="margin-top:36px;font-size:12px;color:#888C89;">
<span style="color:{tone};">&#9679;</span> {_esc(nl._company_address())}
</p>
</div></body></html>"""


def _esc(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
