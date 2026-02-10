# Patch deck export to match deck builder visual
path = 'templates/index.html'
with open(path, 'r', encoding='utf-8') as f:
    s = f.read()

# Helper to escape HTML in export
helper = r"""
        function escapeExportHtml(str) {
            if (str == null || str === undefined) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }
        function getDeckExportHtml(deck, userName) {
            const titleSlide = deck.titleSlide || { title: deck.name || 'Presentation', subtitle: '', image: '' };
            const slides = deck.slides || [];
            const accentLime = '#c8e600';
            const bgDark = '#1a1a1a';
            const textSecondary = '#94a3b8';
            let html = '<!DOCTYPE html><html><head><meta charset="UTF-8"><style>';
            html += 'body{font-family:Calibri,Arial,sans-serif;background:#0a0a0a;color:#e6f1ff;margin:0;padding:20px;}';
            html += '.slide{width:960px;height:540px;background:linear-gradient(135deg,#1a1a1a 0%,#2d2d2d 50%,#1a1a1a 100%);page-break-after:always;padding:2rem;box-sizing:border-box;margin:0 auto 20px;border-radius:8px;box-shadow:0 4px 24px rgba(0,0,0,0.4);overflow:hidden;position:relative;}';
            html += '.title-slide{display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;}';
            html += '.title-slide .slide-title{font-size:2rem;font-weight:700;color:#fff;margin:0.5rem 0;}';
            html += '.title-slide .slide-subtitle{font-size:1rem;color:' + textSecondary + ';margin:0.25rem 0;}';
            html += '.title-slide img{max-width:150px;max-height:150px;border-radius:8px;margin-bottom:1rem;}';
            html += '.slide-footer{position:absolute;bottom:1rem;right:1.5rem;font-size:0.7rem;color:' + textSecondary + ';text-align:right;}';
            html += '.content-slide .slide-header{font-size:1.25rem;font-weight:600;margin-bottom:1rem;color:' + accentLime + ';}';
            html += '.content-slide .slide-body{font-size:0.9rem;}';
            html += 'table{width:100%;border-collapse:collapse;margin-top:0.5rem;}';
            html += 'th{background:rgba(200,230,0,0.15);color:' + accentLime + ';padding:0.5rem 0.75rem;text-align:left;font-size:0.8rem;}';
            html += 'td{padding:0.4rem 0.75rem;border-bottom:1px solid rgba(255,255,255,0.08);font-size:0.8rem;}';
            html += '.demo-box{background:#1e293b;padding:0.5rem 0.75rem;border-radius:6px;display:inline-block;margin:0.25rem;}';
            html += '.demo-box .val{font-size:1.1rem;font-weight:700;color:' + accentLime + ';}';
            html += '.demo-box .lbl{font-size:0.75rem;color:' + textSecondary + ';}';
            html += '</style></head><body>';
            // Title slide
            html += '<div class="slide title-slide">';
            if (titleSlide.image) html += '<img src="' + escapeExportHtml(titleSlide.image) + '" alt="">';
            html += '<div class="slide-title">' + escapeExportHtml(titleSlide.title || '') + '</div>';
            if (titleSlide.subtitle) html += '<div class="slide-subtitle">' + escapeExportHtml(titleSlide.subtitle) + '</div>';
            html += '<div class="slide-footer">Crosswalk Profile IQ<br>' + escapeExportHtml(userName || '') + '<br>Crosswalk</div></div>';
            // Content slides
            slides.forEach((item, idx) => {
                const title = item.customTitle || item.title || item.category || ('Slide ' + (idx + 2));
                html += '<div class="slide content-slide"><div class="slide-header">' + escapeExportHtml(title) + '</div><div class="slide-body">';
                if (item.data) {
                    if (item.type === 'demographic' && item.data.values) {
                        const entries = Object.entries(item.data.values).sort((a,b) => (b[1]||0) - (a[1]||0)).slice(0, 12);
                        entries.forEach(([k, v]) => {
                            const val = typeof v === 'number' ? v.toFixed(1) + '%' : v;
                            html += '<div class="demo-box"><div class="lbl">' + escapeExportHtml(k) + '</div><div class="val">' + escapeExportHtml(val) + '</div></div>';
                        });
                    } else if (item.data.items && item.data.items.length) {
                        html += '<table><tr><th>Item</th><th>Profile</th><th>Gen Pop</th><th>Index</th></tr>';
                        item.data.items.slice(0, 12).forEach(i => {
                            html += '<tr><td>' + escapeExportHtml(i.name) + '</td><td>' + (i.pct != null ? Number(i.pct).toFixed(1) + '%' : '-') + '</td><td>' + (i.genPopPct != null ? Number(i.genPopPct).toFixed(1) + '%' : '-') + '</td><td>' + (i.index != null ? i.index : '-') + '</td></tr>';
                        });
                        html += '</table>';
                    } else {
                        html += '<p style="color:' + textSecondary + ';">No data</p>';
                    }
                }
                html += '<div class="slide-footer">Crosswalk Profile IQ<br>' + escapeExportHtml(userName || '') + '<br>Crosswalk</div></div></div>';
            });
            html += '</body></html>';
            return html;
        }
"""

old_export = """        async function exportToPowerPoint() {
            const items = currentDeckData?.slides || deckWorkspaceItems;
            if (items.length === 0) {
                alert('No items in deck. Add items first using the ➕ buttons on charts and tabs.');
                return;
            }
            
            trackActivity('export_powerpoint', items.length);
            
            // For PowerPoint, we'll generate an XML-based PPTX structure
            // Since we can't create actual PPTX in browser without a library,
            // we'll create an HTML file that can be opened in PowerPoint
            
            let content = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<style>
    body { font-family: Calibri, Arial, sans-serif; }
    .slide { page-break-after: always; padding: 50px; min-height: 500px; }
    h1 { font-size: 32px; color: #2c3e50; }
    h2 { font-size: 24px; color: #34495e; }
    table { width: 100%; border-collapse: collapse; }
    th { background: #3498db; color: white; padding: 12px; }
    td { padding: 10px; border-bottom: 1px solid #ecf0f1; }
</style>
</head>
<body>
`;
            
            // Title slide
            content += `<div class="slide"><h1>CROSSWALK Audience Analysis</h1><h2>${formatDatePST(new Date())}</h2><p>${items.length} slides</p></div>`;
            
            items.forEach((item, idx) => {
                content += `<div class="slide"><h1>${item.title || item.category || 'Slide ' + (idx + 1)}</h1><h2>${item.profileName || ''}</h2>`;
                
                if (item.data) {
                    content += '<table><tr><th>Item</th><th>Profile</th><th>Gen Pop</th><th>Index</th></tr>';
                    
                    if (item.type === 'demographic' && item.data.values) {
                        Object.entries(item.data.values).slice(0, 8).forEach(([k, v]) => {
                            content += `<tr><td>${k}</td><td>${typeof v === 'number' ? v.toFixed(1) : v}%</td><td>${(item.data.genPop?.[k] || 0).toFixed(1)}%</td><td>${item.data.index?.[k] || 100}</td></tr>`;
                        });
                    } else if (item.data.items) {
                        item.data.items.slice(0, 8).forEach(i => {
                            content += `<tr><td>${i.name}</td><td>${i.pct?.toFixed(1) || 0}%</td><td>${i.genPopPct?.toFixed(1) || 0}%</td><td>${i.index || 100}</td></tr>`;
                        });
                    }
                    content += '</table>';
                }
                content += '</div>';
            });
            
            content += '</body></html>';
            
            const blob = new Blob([content], { type: 'application/vnd.ms-powerpoint' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `Crosswalk_Deck_${new Date().toISOString().split('T')[0]}.ppt`;
            link.click();
            
            showNotification('✅ PowerPoint file downloaded! Open in PowerPoint to edit.', 'success');
            document.getElementById('deckExportDropdown').style.display = 'none';
        }"""

new_export = """        async function exportToPowerPoint() {
            const deck = currentEditingDeck || currentDeckData;
            const slides = deck?.slides || deckWorkspaceItems;
            const hasTitle = deck && (deck.titleSlide || deck.slides);
            if (!deck && !slides.length) {
                alert('No items in deck. Add items first using the ➕ buttons on charts and tabs.');
                return;
            }
            const itemCount = (deck?.titleSlide ? 1 : 0) + (slides?.length || 0);
            if (itemCount === 0) {
                alert('No items in deck. Add items first using the ➕ buttons on charts and tabs.');
                return;
            }
            trackActivity('export_powerpoint', itemCount);
            const deckForExport = deck || { name: 'Deck', titleSlide: { title: 'Crosswalk Audience Analysis', subtitle: formatDatePST(new Date()) }, slides: slides };
            const userName = (typeof username !== 'undefined' && username) ? username : (document.body.getAttribute('data-username') || '');
            const content = getDeckExportHtml(deckForExport, userName);
            const blob = new Blob([content], { type: 'text/html;charset=utf-8' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'Crosswalk_Deck_' + new Date().toISOString().split('T')[0] + '.html';
            link.click();
            showNotification('✅ Deck exported! Open the HTML in PowerPoint (File > Open) or print to PDF.', 'success');
            const dd = document.getElementById('deckExportDropdown');
            if (dd) dd.style.display = 'none';
        }"""

# Add helper if not present
if helper.strip() not in s:
    # Find a place to inject - before exportToPowerPoint or before any deck export
    if 'async function exportToPowerPoint()' in s:
        s = s.replace('        async function exportToPowerPoint() {', helper.strip() + '\n\n        async function exportToPowerPoint() {', 1)
    elif 'function exportToPowerPoint()' in s:
        s = s.replace('        function exportToPowerPoint() {', helper.strip() + '\n\n        function exportToPowerPoint() {', 1)
    else:
        raise SystemExit('Could not find exportToPowerPoint to add helper before')

# Replace old export with new if found
if old_export in s:
    s = s.replace(old_export, new_export, 1)
elif 'async function exportToPowerPoint()' in s or 'function exportToPowerPoint()' in s:
    # Partial match - try to replace just the function body
    pass  # Skip if structure differs - helper was added

with open(path, 'w', encoding='utf-8') as f:
    f.write(s)
print('Patched export')