#!/usr/bin/env python3
"""Add profile "family" grouping tooling:

  - app.py: helpers that derive a durable family key + cut label from a
    profile's FILE identity (not its display name), plus two admin
    endpoints:
      * POST /api/admin/family-sync   (dry-run + apply)
      * GET  /api/admin/family-audit  (read-only orphaned-cuts scan)
  - admin.html: rename-time "offer to sync cuts" prompt + an
    "Audit Data Cuts" button and results modal.

Keeps a profile's data cuts labeled under their Total Universe even after
the base's display name is changed, so they don't split into a separate
title in the Profile IQ selector.
"""
import io
import py_compile
import sys

APP = "app.py"
ADMIN = "templates/admin.html"


def read(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def replace_once(text, old, new, label):
    n = text.count(old)
    assert n == 1, "anchor not unique (%d) for %s" % (n, label)
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# app.py: helpers + endpoints (inserted before the rename-file route)
# ---------------------------------------------------------------------------
APP_BLOCK = r'''# ---------------------------------------------------------------------------
# Profile "family" grouping (Total Universe + its data cuts).
#
# Grouping in the Profile IQ selector keys off each file's DISPLAY name
# (getProfileSuffixInfo in templates/index.html strips a trailing cut suffix
# like "- Avid Fan" and rolls everything with the same leftover base under one
# clickable title). Cuts are separate files, each carrying their own display
# name, so renaming a base's display name in Admin leaves the cuts behind and
# they split off into their own titles.
#
# The durable link between a base and its cuts is the FILE identity (the
# on-disk filename subject), NOT the display name. These helpers derive a
# family key + cut label from the filename so we can re-label drifted cuts as
# "<base display> - <cut label>" and keep the family together. The suffix
# regexes MIRROR the frontend (PROFILE_DASH_SUFFIX_REGEX / PROFILE_SUFFIX_REGEX
# / _isBaseUniverseSuffix) so backend and frontend agree on what a "cut" is.
# (Jessie 2026-09-08)
# ---------------------------------------------------------------------------
_FAM_DASH_SUFFIX_RE = re.compile(r'^(.+?)\s[\-\u2013\u2014]\s+(.+)$')
_FAM_TOKEN_SUFFIX_RE = re.compile(
    r'[\s_\-]+(20\d{2}|\d+\+|\d+\s*Plus|Avid(?:\s+Fans?)?|Casual(?:\s+Fans?)?|'
    r'Super\s*Fans?|Heavy(?:\s+Users?)?|Light(?:\s+Users?)?|Lite|Premium|Standard)$',
    re.I)
_FAM_BASE_UNIVERSE_RE = re.compile(r'^total universe(\s+\d{4})?$', re.I)


def _fam_norm_ws(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _fam_suffix_info(name):
    """Return (canonical_base, suffix_label). Mirrors getProfileSuffixInfo:
    dash suffix is checked first, then the cohort/year token allow-list. A
    trailing "- Total Universe" descriptor is treated as the base (no cut)."""
    name = _fam_norm_ws(name)
    if not name:
        return '', ''
    dm = _FAM_DASH_SUFFIX_RE.match(name)
    if dm:
        base = _fam_norm_ws(dm.group(1))
        suffix = _fam_norm_ws(dm.group(2))
        if base and suffix:
            if _FAM_BASE_UNIVERSE_RE.match(suffix):
                return base, ''
            return base, suffix
    m = _FAM_TOKEN_SUFFIX_RE.search(name)
    if not m:
        return name, ''
    base = _fam_norm_ws(name[:m.start()])
    if not base:
        return name, ''
    suffix = _fam_norm_ws(m.group(1))
    if _FAM_BASE_UNIVERSE_RE.match(suffix):
        return base, ''
    return base, suffix


def _fam_file_rawname(s3_key):
    """Filename subject: drop folder + .csv + trailing timestamp, then turn
    underscores into spaces so it lines up with display-style names."""
    stem = (s3_key or '').split('/')[-1]
    if stem.lower().endswith('.csv'):
        stem = stem[:-4]
    try:
        stem = remove_timestamp_from_name(stem)
    except Exception:
        pass
    return _fam_norm_ws(stem.replace('_', ' '))


def _fam_key_and_label(job):
    """Durable (family_key, cut_label) derived from the FILE (display-name
    independent). family_key is lowercased; cut_label is '' for a base/TU."""
    key = job.get('s3_key') or job.get('key') or ''
    canon, label = _fam_suffix_info(_fam_file_rawname(key))
    return canon.lower(), label


def _fam_display_name(job):
    return (job.get('display_name') or job.get('project_name')
            or job.get('name') or '').strip()


def _fam_cut_label(job):
    """Preferred cut label for building the new display name: the current
    display's suffix if it has one, otherwise the filename's suffix."""
    _, dlabel = _fam_suffix_info(_fam_display_name(job))
    if dlabel:
        return dlabel
    _, flabel = _fam_key_and_label(job)
    return flabel


def _fam_build_families(jobs):
    """Group ROOT dashboard-inputs csv profiles by durable family key.
    Returns { family_key: {'bases': [...], 'cuts': [...]} }."""
    fams = {}
    for j in jobs or []:
        key = j.get('s3_key') or j.get('key') or ''
        if not _is_root_csv_key(key):
            continue
        fkey, label = _fam_key_and_label(j)
        if not fkey:
            continue
        fam = fams.setdefault(fkey, {'bases': [], 'cuts': []})
        (fam['cuts'] if label else fam['bases']).append(j)
    return fams


def _fam_orphans_for(fam):
    """Given a family dict, return (base_job_or_None, [orphan,...]) where each
    orphan is {'job','current','suggested','label'} for cuts whose display base
    has drifted from the base's display name. Auto-fix requires exactly one
    base whose own display carries no dash suffix (a dashed base can't cleanly
    host dash-suffixed cuts under first-dash canonicalization)."""
    bases = fam.get('bases') or []
    cuts = fam.get('cuts') or []
    if len(bases) != 1 or not cuts:
        return (bases[0] if len(bases) == 1 else None), []
    base = bases[0]
    canonical = _fam_display_name(base)
    if not canonical:
        return base, []
    _, base_own_suffix = _fam_suffix_info(canonical)
    if base_own_suffix:
        return base, []  # base present but not auto-fixable
    canon_lc = _fam_norm_ws(canonical).lower()
    out = []
    for c in cuts:
        label = _fam_cut_label(c)
        if not label:
            continue
        cur = _fam_display_name(c)
        cur_base, _ = _fam_suffix_info(cur)
        if _fam_norm_ws(cur_base).lower() == canon_lc:
            continue  # already grouped correctly
        out.append({'job': c, 'current': cur,
                    'suggested': canonical + ' - ' + label, 'label': label})
    return base, out


@app.route('/api/admin/family-sync', methods=['POST'])
@requires_admin
def family_sync():
    """Keep a profile's data cuts labeled under its Total Universe. Given any
    file key in a family, resolve the family's single base (TU) and re-label
    each out-of-sync cut's display name to "<base display> - <cut label>".
    Pass apply=false (default) for a dry run that just returns the pending
    changes; apply=true to persist them to the profile cache."""
    try:
        data = request.get_json(silent=True) or {}
        base_key = (data.get('base_key') or data.get('key') or '').strip()
        apply = bool(data.get('apply'))
        if not base_key:
            return jsonify({'success': False, 'error': 'base_key required'}), 400
        try:
            load_persisted_cache()
        except Exception:
            pass
        jobs = s3_cache.get('jobs', [])
        target = None
        for j in jobs:
            if j.get('s3_key') == base_key or j.get('key') == base_key:
                target = j
                break
        if target is None:
            return jsonify({'success': False, 'error': 'File not found in cache'}), 404
        fkey, _ = _fam_key_and_label(target)
        fam = _fam_build_families(jobs).get(fkey)
        if not fam:
            return jsonify({'success': True, 'base_display': None, 'applied': False, 'changes': []})
        base, orphans = _fam_orphans_for(fam)
        changes = [{'key': o['job'].get('s3_key') or o['job'].get('key'),
                    'current': o['current'], 'suggested': o['suggested']} for o in orphans]
        did_apply = bool(apply and orphans)
        if did_apply:
            for o in orphans:
                c = o['job']
                newname = o['suggested']
                c['display_name'] = newname
                c['project_name'] = newname
                c['name'] = newname
                if c.get('brand'):
                    c['brand'] = newname
            try:
                save_persisted_cache()
            except Exception:
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': 'Failed to persist changes'}), 500
        return jsonify({'success': True,
                        'base_display': _fam_display_name(base) if base else None,
                        'applied': did_apply, 'changes': changes})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/family-audit', methods=['GET'])
@requires_admin
def family_audit():
    """Read-only scan of every profile family for data cuts whose display name
    has drifted from their Total Universe (so they'd split into a separate
    title in the selector). Returns one entry per family with >=1 orphan."""
    try:
        try:
            load_persisted_cache()
        except Exception:
            pass
        jobs = s3_cache.get('jobs', [])
        results = []
        for _fkey, fam in _fam_build_families(jobs).items():
            base, orphans = _fam_orphans_for(fam)
            if base is None or not orphans:
                continue
            results.append({
                'base_key': base.get('s3_key') or base.get('key'),
                'base_display': _fam_display_name(base),
                'cuts': [{'key': o['job'].get('s3_key') or o['job'].get('key'),
                          'current': o['current'], 'suggested': o['suggested']} for o in orphans],
            })
        results.sort(key=lambda r: (r.get('base_display') or '').lower())
        return jsonify({'success': True, 'families': results, 'count': len(results)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


'''

APP_ANCHOR = ("@app.route('/api/admin/rename-file', methods=['POST'])\n"
              "@requires_admin\n"
              "def rename_file():")


# ---------------------------------------------------------------------------
# admin.html edits
# ---------------------------------------------------------------------------
ADMIN_BTN_OLD = ('                        <button class="btn btn-small btn-secondary" '
                 'onclick="loadContentFiles()">↻ Refresh</button>')
ADMIN_BTN_NEW = (ADMIN_BTN_OLD + '\n'
                 '                        <button class="btn btn-small btn-secondary" '
                 'onclick="auditDataCutFamilies()" title="Find data cuts whose name '
                 'has drifted from their Total Universe">🧩 Audit Data Cuts</button>')

ADMIN_MODAL_ANCHOR = ('                <button class="btn btn-success" onclick="renameFile()" '
                      'style="flex: 1;">💾 Save</button>\n'
                      '            </div>\n'
                      '        </div>\n'
                      '    </div>')
ADMIN_MODAL_NEW = ADMIN_MODAL_ANCHOR + '''

    <!-- Data-Cut Family Audit Modal -->
    <div class="modal" id="familyAuditModal">
        <div class="modal-content" style="max-width: 760px;">
            <div class="modal-header">
                <h2>🧩 Data-Cut Family Audit</h2>
                <button class="modal-close" onclick="closeFamilyAuditModal()">&times;</button>
            </div>
            <p style="font-size:0.8rem;color:var(--text-secondary);margin:0 0 1rem;">Data cuts whose display name has drifted from their Total Universe (so they'd show up as a separate title). "Fix" re-labels each cut to "&lt;Total Universe name&gt; - &lt;cut&gt;" so it groups back under its profile.</p>
            <div id="familyAuditBody" style="max-height:60vh;overflow-y:auto;">Loading…</div>
        </div>
    </div>'''

ADMIN_JS_ANCHOR = ('''        function closeRenameModal() {
            document.getElementById('renameModal').classList.remove('show');
        }''')
ADMIN_JS_NEW = ADMIN_JS_ANCHOR + '''

        // ============ DATA-CUT FAMILY GROUPING ============
        // Keep a profile's data cuts labeled under their Total Universe so a
        // display-name rename doesn't split them into separate titles.
        function _famEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

        async function familyOfferSync(baseKey){
            try{
                const res = await fetch('/api/admin/family-sync', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ base_key: baseKey, apply:false }) });
                const data = await res.json();
                if(!data.success || !data.changes || !data.changes.length) return;
                const n = data.changes.length;
                if(!confirm('This profile has ' + n + ' data cut' + (n>1?'s':'') + ' whose name no longer matches the Total Universe. Rename ' + (n>1?'them':'it') + ' so ' + (n>1?'they stay':'it stays') + ' grouped under this profile?')) return;
                const res2 = await fetch('/api/admin/family-sync', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ base_key: baseKey, apply:true }) });
                const data2 = await res2.json();
                if(data2.success && data2.applied){ showToast('Re-labeled ' + n + ' data cut' + (n>1?'s':'') + ' to match'); loadContentFiles(); }
                else { showToast((data2 && data2.error) || 'Could not sync data cuts', true); }
            }catch(e){ console.error('family sync error', e); }
        }

        function closeFamilyAuditModal(){ document.getElementById('familyAuditModal').classList.remove('show'); }

        async function auditDataCutFamilies(){
            const modal = document.getElementById('familyAuditModal');
            const body = document.getElementById('familyAuditBody');
            modal.classList.add('show');
            body.innerHTML = 'Scanning…';
            try{
                const res = await fetch('/api/admin/family-audit', { credentials:'same-origin' });
                const data = await res.json();
                if(!data.success){ body.innerHTML = 'Error: ' + _famEsc(data.error||'failed'); return; }
                if(!data.families || !data.families.length){ body.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);">✅ All data cuts are grouped correctly under their Total Universe.</div>'; return; }
                body.innerHTML = data.families.map(function(f){
                    const cuts = f.cuts.map(function(c){ return '<div style="font-size:0.72rem;color:var(--text-secondary);margin:2px 0 0 0.5rem;">• ' + _famEsc(c.current) + ' &rarr; <span style="color:var(--accent-lime);">' + _famEsc(c.suggested) + '</span></div>'; }).join('');
                    return '<div style="border:1px solid var(--border-color);border-radius:8px;padding:0.7rem 0.8rem;margin-bottom:0.6rem;">'
                         + '<div style="display:flex;justify-content:space-between;align-items:center;gap:0.6rem;">'
                         + '<div style="font-weight:600;font-size:0.85rem;">' + _famEsc(f.base_display) + ' <span style="color:var(--text-secondary);font-weight:400;">&mdash; ' + f.cuts.length + ' cut' + (f.cuts.length>1?'s':'') + '</span></div>'
                         + '<button class="btn btn-small btn-success" data-basekey="' + _famEsc(f.base_key) + '" onclick="familyFixOne(this.getAttribute(\\'data-basekey\\'))">Fix</button>'
                         + '</div>' + cuts + '</div>';
                }).join('');
            }catch(e){ body.innerHTML = 'Error: ' + _famEsc(e.message); }
        }

        async function familyFixOne(baseKey){
            try{
                const res = await fetch('/api/admin/family-sync', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ base_key: baseKey, apply:true }) });
                const data = await res.json();
                if(data.success && data.applied){ showToast('Fixed ' + (data.changes ? data.changes.length : 0) + ' cut(s)'); auditDataCutFamilies(); loadContentFiles(); }
                else { showToast((data && data.error) || 'Nothing to fix', true); }
            }catch(e){ showToast('Error: ' + e.message, true); }
        }'''

ADMIN_HOOK_ANCHOR = ('''                    updateFileRowInPlace(fileKey, newKey, {
                        project_name: newDisplayName,
                        filename: newKey.split('/').pop()
                    });''')
ADMIN_HOOK_NEW = ADMIN_HOOK_ANCHOR + '''
                    // Offer to relabel this profile's data cuts so a display
                    // rename doesn't split them into separate titles.
                    familyOfferSync(newKey);'''


def main():
    # --- app.py ---
    app_txt = read(APP)
    assert '_FAM_DASH_SUFFIX_RE' not in app_txt, "app.py already patched"
    app_txt = replace_once(app_txt, APP_ANCHOR, APP_BLOCK + APP_ANCHOR, "app rename-file route")
    write(APP, app_txt)
    py_compile.compile(APP, doraise=True)
    print("app.py: patched + compiles OK")

    # --- admin.html ---
    adm = read(ADMIN)
    assert 'auditDataCutFamilies' not in adm, "admin.html already patched"
    adm = replace_once(adm, ADMIN_BTN_OLD, ADMIN_BTN_NEW, "audit button")
    adm = replace_once(adm, ADMIN_MODAL_ANCHOR, ADMIN_MODAL_NEW, "audit modal")
    adm = replace_once(adm, ADMIN_JS_ANCHOR, ADMIN_JS_NEW, "audit js")
    adm = replace_once(adm, ADMIN_HOOK_ANCHOR, ADMIN_HOOK_NEW, "rename hook")
    write(ADMIN, adm)
    print("admin.html: patched (4 anchors)")


if __name__ == "__main__":
    sys.exit(main())
