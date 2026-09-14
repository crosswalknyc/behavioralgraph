#!/usr/bin/env python3
"""Stamp uploader identity onto every admin image upload.

Per Jenna 2026-09-08: the profile_images_cache.json schema had no
uploader field, and the admin.html client didn't call /api/track-action
after a successful upload/removal, so we couldn't answer "who uploaded
this image" or "how often is <user> uploading images." This adds both.

BACKEND (bg-webapp/app.py):
  - POST /api/admin/profile-image: stamp `uploaded_by` (session username)
    and `uploaded_at` (ISO timestamp) onto every new cache entry, in
    addition to the existing `cached_at`. Older entries are left alone.
  - DELETE /api/admin/profile-image: log the acting username and the
    prior entry's `uploaded_by` in the print line so Render logs capture
    the who + when for removals too.

FRONTEND (bg-webapp/templates/admin.html):
  - After a successful upload in the main image-modal save path, fire a
    non-blocking /api/track-action call so the acting admin's activity
    log in users.json picks it up. Discriminates between profile,
    ticket-sales, ticket-sales-tracker, and bpiq (brand partnership)
    image uploads.
  - Same fire-and-forget track-action after a successful removal in
    the modal DELETE path and in the profile-images-list DELETE.

Idempotent. Every splice no-ops if already applied.
"""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
APP_PY = REPO / 'app.py'
ADMIN_HTML = REPO / 'templates' / 'admin.html'


def splice(src: str, old: str, new: str, desc: str) -> tuple[str, bool]:
    if new in src and old not in src:
        print(f"  [skip] {desc}: already applied")
        return src, False
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (must be unique)")
    print(f"  [apply] {desc}")
    return src.replace(old, new), True


# ---------------------------------------------------------------------------
# 1) app.py POST /api/admin/profile-image -- stamp uploaded_by + uploaded_at
# ---------------------------------------------------------------------------
APP_POST_OLD = """        # Store the entry we're about to add (so we don't lose it if cache gets reloaded)
        new_entry = {
            'image_url': image_url,
            'title': profile_name,
            'source': 'custom',
            'is_custom': True,
            'cached_at': datetime.now().isoformat()
        }"""

APP_POST_NEW = """        # Store the entry we're about to add (so we don't lose it if cache gets reloaded)
        # 2026-09-08 (Jenna): stamp the acting admin's username onto every
        # new upload so we can answer "who uploaded this and when" from the
        # cache alone. Older entries stay untouched (no backfill). The
        # `uploaded_at` field duplicates `cached_at` on new entries but
        # exists as a stable attribution timestamp so a future
        # cache-refresh pass that touches `cached_at` won't lose the
        # original upload moment.
        _uploader = (session.get('username') if session else None) or 'unknown'
        _upload_iso = datetime.now().isoformat()
        new_entry = {
            'image_url': image_url,
            'title': profile_name,
            'source': 'custom',
            'is_custom': True,
            'cached_at': _upload_iso,
            'uploaded_by': _uploader,
            'uploaded_at': _upload_iso
        }"""


# ---------------------------------------------------------------------------
# 2) app.py DELETE /api/admin/profile-image -- log who removed what
# ---------------------------------------------------------------------------
APP_DEL_OLD = """        # Remove from cache
        if cache_key in profile_image_cache:
            del profile_image_cache[cache_key]
            profile_image_cache_dirty = True
            saved = save_profile_image_cache(deleted_keys={cache_key})
            if not saved:
                print(f"   ⚠️ Warning: Cache save may have failed after removing {cache_key}")
            print(f"   ✅ Removed from cache: {cache_key}")
        else:
            print(f"   ℹ️ Cache key not found: {cache_key}")"""

APP_DEL_NEW = """        # Remove from cache
        if cache_key in profile_image_cache:
            # 2026-09-08 (Jenna): capture prior entry so the Render access
            # log records both the acting admin AND the original uploader
            # of the image being removed. No new cache field is written
            # (the entry itself is going away); attribution lives in the
            # per-request log line.
            _prev_entry = profile_image_cache.get(cache_key) or {}
            _actor = (session.get('username') if session else None) or 'unknown'
            del profile_image_cache[cache_key]
            profile_image_cache_dirty = True
            saved = save_profile_image_cache(deleted_keys={cache_key})
            if not saved:
                print(f"   ⚠️ Warning: Cache save may have failed after removing {cache_key}")
            print(f"   ✅ Removed from cache: {cache_key} (by={_actor} was_uploaded_by={_prev_entry.get('uploaded_by', '?')} was_uploaded_at={_prev_entry.get('uploaded_at', _prev_entry.get('cached_at', '?'))})")
        else:
            print(f"   ℹ️ Cache key not found: {cache_key}")"""


# ---------------------------------------------------------------------------
# 3) admin.html main save path -- track successful upload
# ---------------------------------------------------------------------------
ADMIN_SAVE_OLD = """                if (data.success) {
                    console.log('✅ Image saved successfully!', data);
                    let toastLabel = 'Profile image saved!';
                    if (isTicketSales) toastLabel = 'Ticket Sales image saved!';
                    else if (isTicketSalesTracker) toastLabel = 'Ticket Sales Tracker image saved!';
                    else if (isBpiq) toastLabel = 'Brand Partnership image saved!';
                    showToast(toastLabel);"""

ADMIN_SAVE_NEW = """                if (data.success) {
                    console.log('✅ Image saved successfully!', data);
                    let toastLabel = 'Profile image saved!';
                    if (isTicketSales) toastLabel = 'Ticket Sales image saved!';
                    else if (isTicketSalesTracker) toastLabel = 'Ticket Sales Tracker image saved!';
                    else if (isBpiq) toastLabel = 'Brand Partnership image saved!';
                    showToast(toastLabel);
                    // 2026-09-08 (Jenna): record image upload in the acting
                    // admin's activity log so future audits can answer
                    // "how often is <user> uploading images?" from
                    // users.json. Fire-and-forget: never blocks the
                    // modal close on failure.
                    try {
                        const _imgActionType = isTicketSales
                            ? 'ticket_sales_image_upload'
                            : (isTicketSalesTracker
                                ? 'ticket_sales_tracker_image_upload'
                                : (isBpiq
                                    ? 'bpiq_image_upload'
                                    : 'profile_image_upload'));
                        fetch('/api/track-action', {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                action: _imgActionType,
                                details: profileName || fileKey || ''
                            })
                        }).catch(() => { /* non-critical */ });
                    } catch (_e) { /* non-critical */ }"""


# ---------------------------------------------------------------------------
# 4) admin.html modal DELETE (removeProfileImage inside save-modal) -- track
# ---------------------------------------------------------------------------
ADMIN_MODAL_DEL_OLD = """                const data = await response.json();
                
                if (data.success) {
                    showToast('Custom image removed');
                    closeProfileImageModal();"""

ADMIN_MODAL_DEL_NEW = """                const data = await response.json();
                
                if (data.success) {
                    showToast('Custom image removed');
                    // 2026-09-08 (Jenna): record image removal in acting
                    // admin's activity log so audits can answer
                    // "how often is <user> removing images?" from
                    // users.json. Fire-and-forget.
                    try {
                        fetch('/api/track-action', {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                action: isBpiq ? 'bpiq_image_remove' : 'profile_image_remove',
                                details: profileName || fileKey || ''
                            })
                        }).catch(() => { /* non-critical */ });
                    } catch (_e) { /* non-critical */ }
                    closeProfileImageModal();"""


# ---------------------------------------------------------------------------
# 5) admin.html list-view DELETE (removeProfileImage(encodedName)) -- track
# ---------------------------------------------------------------------------
ADMIN_LIST_DEL_OLD = """                const data = await response.json();
                if (data.success) {
                    alert('Image removed successfully');
                    loadProfileImages();
                } else {
                    alert('Error: ' + (data.error || 'Failed to remove image'));
                }"""

ADMIN_LIST_DEL_NEW = """                const data = await response.json();
                if (data.success) {
                    // 2026-09-08 (Jenna): record image removal from the
                    // profile-images list view too (separate call site
                    // from the modal-DELETE path).
                    try {
                        fetch('/api/track-action', {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                action: 'profile_image_remove',
                                details: profileName || ''
                            })
                        }).catch(() => { /* non-critical */ });
                    } catch (_e) { /* non-critical */ }
                    alert('Image removed successfully');
                    loadProfileImages();
                } else {
                    alert('Error: ' + (data.error || 'Failed to remove image'));
                }"""


def edit_app_py() -> int:
    print("app.py:")
    src = APP_PY.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, APP_POST_OLD, APP_POST_NEW, "POST: stamp uploaded_by + uploaded_at")
    src, _ = splice(src, APP_DEL_OLD, APP_DEL_NEW, "DELETE: log actor + prior uploader")
    if src != orig:
        APP_PY.write_text(src, encoding='utf-8')
        print(f"  wrote {APP_PY}")
        return 1
    return 0


def edit_admin_html() -> int:
    print("templates/admin.html:")
    src = ADMIN_HTML.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, ADMIN_SAVE_OLD, ADMIN_SAVE_NEW, "SAVE modal: track upload")
    src, _ = splice(src, ADMIN_MODAL_DEL_OLD, ADMIN_MODAL_DEL_NEW, "MODAL DELETE: track removal")
    src, _ = splice(src, ADMIN_LIST_DEL_OLD, ADMIN_LIST_DEL_NEW, "LIST DELETE: track removal")
    if src != orig:
        ADMIN_HTML.write_text(src, encoding='utf-8')
        print(f"  wrote {ADMIN_HTML}")
        return 1
    return 0


def main() -> int:
    changed = 0
    changed += edit_app_py()
    changed += edit_admin_html()
    if not changed:
        print("[skip] all splices already applied")
    return 0


if __name__ == '__main__':
    sys.exit(main())
