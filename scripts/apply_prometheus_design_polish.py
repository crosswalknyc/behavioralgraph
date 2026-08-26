#!/usr/bin/env python3
"""Prometheus widget design polish.

User directive 2026-08-26 (Jenna): "can you make the design of
prometheus better?"

Scope: the floating chatbot widget in the profile IQ dashboard
(#prometheusLauncher + #prometheusWidget + #prometheusHeader +
#prometheusBody). Applies Crosswalk Brand System v1.0 tokens more
consistently and rebuilds the empty state as a clean intro line
plus 4 clickable example chips that auto-fill the composer.

Zero behavior change. Every existing handler (synthChatSubmit,
prometheusCollapse, resize handles, etc.) is untouched. Only CSS
tokens and the empty-state HTML change.

Splices (all byte-safe with str.count == 1 anchor guards):

  A. Launcher shadow             - trim aggressive white ring
  B. Widget frame border         - #27393D -> #3B3D38 (Pavement)
  C. Header + subtitle           - proper brand eyebrow treatment
  D. Collapse button border      - #27393D -> #3B3D38
  E. Messages panel border       - explicit Pavement, softer radius
  F. Empty state HTML            - clickable example chips
  G. Textarea composer           - Pavement border + Cobalt focus ring
  H. New JS helper               - _pmFillFromChip() for chip clicks

Per index-html-safety.mdc: never StrReplace / Write on this file.
Every splice has a unique-anchor guard, and the file is fully
backed up to /tmp before any write.
"""

from pathlib import Path
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "templates" / "index.html"

STAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
BACKUP = Path("/tmp") / f"index.pre_prometheus_polish_{STAMP}.html"


# ---------------------------------------------------------------------------
# SPLICE A: Launcher shadow. Kill the aggressive white ring so it reads as
# a discreet call-to-action, not a smoke alarm.
# ---------------------------------------------------------------------------

A_OLD = (
    "        #prometheusLauncher {\n"
    "            position: fixed; right: 22px; bottom: 22px; width: 58px; height: 58px;\n"
    "            border-radius: 50%; background: #15252A; border: 1px solid #27393D;\n"
    "            display: none; align-items: center; justify-content: center;\n"
    "            cursor: grab; z-index: 99000;\n"
    "            box-shadow: 0 0 0 1px rgba(255,255,255,0.30),\n"
    "                        0 0 16px 4px rgba(255,255,255,0.35),\n"
    "                        0 6px 22px rgba(0,0,0,0.45);\n"
    "            touch-action: none; user-select: none; -webkit-user-select: none;\n"
    "            transition: background 120ms ease-out;\n"
    "        }\n"
)

A_NEW = (
    "        #prometheusLauncher {\n"
    "            position: fixed; right: 22px; bottom: 22px; width: 54px; height: 54px;\n"
    "            border-radius: 50%; background: #15252A; border: 1px solid #3B3D38;\n"
    "            display: none; align-items: center; justify-content: center;\n"
    "            cursor: grab; z-index: 99000;\n"
    "            /* Discreet outline + soft ground shadow. Removed the loud\n"
    "               inner white ring (2026-08-26 polish) so the launcher reads\n"
    "               as a small elegant target rather than a beacon. */\n"
    "            box-shadow: 0 0 0 1px rgba(233,232,225,0.18),\n"
    "                        0 10px 26px rgba(0,0,0,0.50);\n"
    "            touch-action: none; user-select: none; -webkit-user-select: none;\n"
    "            transition: background 140ms ease-out, transform 140ms ease-out,\n"
    "                        box-shadow 140ms ease-out;\n"
    "        }\n"
)


# ---------------------------------------------------------------------------
# SPLICE B: Widget frame border - use Pavement instead of the custom teal.
# ---------------------------------------------------------------------------

B_OLD = (
    "        #prometheusWidget {\n"
    "            position: fixed; right: 22px; bottom: 22px; width: 440px;\n"
    "            height: min(660px, calc(100vh - 44px));\n"
    "            min-width: 360px; min-height: 440px; max-width: 94vw; max-height: 94vh;\n"
    "            background: #0C1618; border: 1px solid #27393D; border-radius: 14px;\n"
)

B_NEW = (
    "        #prometheusWidget {\n"
    "            position: fixed; right: 22px; bottom: 22px; width: 440px;\n"
    "            height: min(660px, calc(100vh - 44px));\n"
    "            min-width: 360px; min-height: 440px; max-width: 94vw; max-height: 94vh;\n"
    "            background: #0C1618; border: 1px solid #3B3D38; border-radius: 14px;\n"
)


# ---------------------------------------------------------------------------
# SPLICE C: Header + subtitle. Loosen padding, treat subtitle as a proper
# brand eyebrow (tracked caps, muted).
# ---------------------------------------------------------------------------

C_OLD = (
    "        #prometheusHeader {\n"
    "            display: flex; align-items: center; justify-content: space-between; gap: 0.6rem;\n"
    "            padding: 0.65rem 0.85rem; background: #15252A; border-bottom: 1px solid #27393D;\n"
    "            cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none;\n"
    "            flex: 0 0 auto;\n"
    "        }\n"
    "        #prometheusHeader:active { cursor: grabbing; }\n"
    "        #prometheusTitle { font-size: 14px; font-weight: 700; color: #E9E8E1; line-height: 1.15; }\n"
    "        #prometheusSub { font-size: 10.5px; color: #9AA09B; line-height: 1.2; margin-top: 1px; }\n"
)

C_NEW = (
    "        #prometheusHeader {\n"
    "            display: flex; align-items: center; justify-content: space-between; gap: 0.6rem;\n"
    "            padding: 0.75rem 1rem; background: #15252A; border-bottom: 1px solid #3B3D38;\n"
    "            cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none;\n"
    "            flex: 0 0 auto;\n"
    "        }\n"
    "        #prometheusHeader:active { cursor: grabbing; }\n"
    "        #prometheusTitle { font-size: 14px; font-weight: 700; color: #E9E8E1; line-height: 1.15;\n"
    "            letter-spacing: 0.005em; }\n"
    "        /* Eyebrow-style subtitle per Crosswalk brand: tracked caps,\n"
    "           muted grey, sits under the title like a spec label. */\n"
    "        #prometheusSub { font-size: 9.5px; color: #9AA09B; line-height: 1.2;\n"
    "            margin-top: 3px; text-transform: uppercase;\n"
    "            letter-spacing: 0.14em; font-weight: 500; }\n"
)


# ---------------------------------------------------------------------------
# SPLICE D: Collapse button border - Pavement.
# ---------------------------------------------------------------------------

D_OLD = (
    "        #prometheusCollapseBtn {\n"
    "            width: 28px; height: 28px; border-radius: 8px; background: transparent;\n"
    "            border: 1px solid #27393D; color: #9AA09B; display: inline-flex;\n"
    "            align-items: center; justify-content: center; cursor: pointer; padding: 0;\n"
    "            transition: background 120ms ease-out, color 120ms ease-out; flex: 0 0 auto;\n"
    "        }\n"
)

D_NEW = (
    "        #prometheusCollapseBtn {\n"
    "            width: 28px; height: 28px; border-radius: 8px; background: transparent;\n"
    "            border: 1px solid #3B3D38; color: #9AA09B; display: inline-flex;\n"
    "            align-items: center; justify-content: center; cursor: pointer; padding: 0;\n"
    "            transition: background 120ms ease-out, color 120ms ease-out; flex: 0 0 auto;\n"
    "        }\n"
)


# ---------------------------------------------------------------------------
# SPLICE E: Messages panel - explicit Pavement border, softer radius.
# Only touches the inline style on #synthChatMessages inside prometheus body.
# ---------------------------------------------------------------------------

E_OLD = (
    '                <div id="synthChatMessages" style="flex: 1; overflow-y: auto; padding: 0.5rem;'
    ' background: var(--bg-input, #1a1a1a); border: 1px solid var(--border-color); border-radius: 8px;'
    ' margin-bottom: 0.5rem; min-height: 260px;">\n'
)

E_NEW = (
    '                <div id="synthChatMessages" style="flex: 1; overflow-y: auto; padding: 0.7rem;'
    ' background: var(--bg-input, #1a1a1a); border: 1px solid #3B3D38; border-radius: 10px;'
    ' margin-bottom: 0.55rem; min-height: 260px;">\n'
)


# ---------------------------------------------------------------------------
# SPLICE F: Empty state HTML. Replace the run-on grey paragraph with a
# clean intro line + 4 clickable example chips.
# ---------------------------------------------------------------------------

F_OLD = (
    '                                <div class="brief-chat-empty" style="text-align: center;'
    ' color: var(--text-secondary); padding: 3rem 1rem; font-size: 0.9rem;">\n'
    '                                    Start by typing what you\'re looking for below.<br>\n'
    '                                    <span style="font-size:0.8em; opacity:0.75;">'
    'Examples: "K-pop fans in the US", "cancelled Netflix, signed up for Max in the last 12 months",'
    ' "Sephora shoppers who bought at Ulta in the same window", "moms of tweens who buy Prime Video".</span>\n'
    '                                </div>\n'
)

F_NEW = (
    '                                <div class="brief-chat-empty pm-empty">\n'
    '                                    <div class="pm-empty-title">Start with a natural-language brief.</div>\n'
    '                                    <div class="pm-empty-sub">Try one of these or write your own.</div>\n'
    '                                    <div class="pm-empty-chips">\n'
    '                                        <button type="button" class="pm-empty-chip" onclick="_pmFillFromChip(this)"'
    ' data-example="K-pop fans in the US">K-pop fans in the US</button>\n'
    '                                        <button type="button" class="pm-empty-chip" onclick="_pmFillFromChip(this)"'
    ' data-example="Cancelled Netflix, signed up for Max in the last 12 months">Netflix cancels who moved to Max</button>\n'
    '                                        <button type="button" class="pm-empty-chip" onclick="_pmFillFromChip(this)"'
    ' data-example="Sephora shoppers who bought at Ulta in the same window">Sephora shoppers who also bought at Ulta</button>\n'
    '                                        <button type="button" class="pm-empty-chip" onclick="_pmFillFromChip(this)"'
    ' data-example="Moms of tweens who buy Prime Video">Moms of tweens who buy Prime Video</button>\n'
    '                                    </div>\n'
    '                                </div>\n'
)


# ---------------------------------------------------------------------------
# SPLICE G: Textarea composer - Pavement border + Cobalt focus ring.
# ---------------------------------------------------------------------------

G_OLD = (
    '                                <textarea id="synthChatInput" placeholder="What profile do you want?'
    ' (audience, cohort, brand, talent, or segment)" rows="3"'
    ' style="width: 100%; box-sizing: border-box; padding: 0.6rem 0.75rem; background: var(--bg-input);'
    ' border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-primary);'
    ' font-size: 0.9rem; resize: vertical; min-height: 72px; font-family: inherit;"'
)

G_NEW = (
    '                                <textarea id="synthChatInput" placeholder="What profile do you want?'
    ' (audience, cohort, brand, talent, or segment)" rows="3"'
    ' style="width: 100%; box-sizing: border-box; padding: 0.65rem 0.8rem; background: var(--bg-input);'
    ' border: 1px solid #3B3D38; border-radius: 8px; color: var(--text-primary);'
    ' font-size: 0.9rem; resize: vertical; min-height: 72px; font-family: inherit;'
    ' outline: none; transition: border-color 120ms ease-out, box-shadow 120ms ease-out;"'
    ' onfocus="this.style.borderColor=\'#3358FF\';this.style.boxShadow=\'0 0 0 2px rgba(51,88,255,0.28)\';"'
    ' onblur="this.style.borderColor=\'#3B3D38\';this.style.boxShadow=\'none\';"'
)


# ---------------------------------------------------------------------------
# SPLICE H: New CSS + JS block for the empty state chips + helper.
# Inserted right after the closing </style> of the prometheus block
# (line ~21358) so it sits with sibling widget styles. Also adds the
# _pmFillFromChip() JS helper inside its own <script>.
# ---------------------------------------------------------------------------

H_OLD = (
    "        body[data-theme=\"light\"] #synthChatSubmitBtn.btn-primary:hover {\n"
    "            background: linear-gradient(135deg, #D3F95E, #B5E61A);\n"
    "            border-color: #B5E61A;\n"
    "            color: #14330A;\n"
    "        }\n"
    "        </style>\n"
)

H_NEW = (
    "        body[data-theme=\"light\"] #synthChatSubmitBtn.btn-primary:hover {\n"
    "            background: linear-gradient(135deg, #D3F95E, #B5E61A);\n"
    "            border-color: #B5E61A;\n"
    "            color: #14330A;\n"
    "        }\n"
    "\n"
    "        /* ---- Empty-state design (2026-08-26 polish) ---------------------\n"
    "           Replaces the old run-on grey paragraph. Two-line intro on top,\n"
    "           four clickable example chips underneath. Chips are Slate Teal\n"
    "           on Pavement border, muted title text; hover raises the border\n"
    "           to Signal Green so it reads as a real affordance rather than a\n"
    "           label. Click fills #synthChatInput and focuses the textarea. */\n"
    "        #prometheusBody .pm-empty {\n"
    "            display: flex; flex-direction: column; align-items: stretch;\n"
    "            padding: 1.8rem 1rem 1.2rem 1rem; color: #9AA09B;\n"
    "        }\n"
    "        #prometheusBody .pm-empty-title {\n"
    "            font-size: 14px; font-weight: 700; color: #E9E8E1;\n"
    "            line-height: 1.35; letter-spacing: 0.005em;\n"
    "            text-align: center; margin-bottom: 0.35rem;\n"
    "        }\n"
    "        #prometheusBody .pm-empty-sub {\n"
    "            font-size: 9.5px; color: #9AA09B; text-transform: uppercase;\n"
    "            letter-spacing: 0.14em; font-weight: 500; text-align: center;\n"
    "            margin-bottom: 1.1rem;\n"
    "        }\n"
    "        #prometheusBody .pm-empty-chips {\n"
    "            display: grid; grid-template-columns: 1fr; gap: 0.5rem;\n"
    "        }\n"
    "        @media (min-width: 420px) {\n"
    "            #prometheusBody .pm-empty-chips {\n"
    "                grid-template-columns: 1fr 1fr;\n"
    "            }\n"
    "        }\n"
    "        #prometheusBody .pm-empty-chip {\n"
    "            appearance: none; background: #15252A; color: #E9E8E1;\n"
    "            border: 1px solid #3B3D38; border-radius: 999px;\n"
    "            padding: 0.55rem 0.85rem; font-size: 0.8rem; font-weight: 500;\n"
    "            font-family: inherit; text-align: left; cursor: pointer;\n"
    "            line-height: 1.25; letter-spacing: 0.005em;\n"
    "            transition: border-color 140ms ease-out, background 140ms ease-out,\n"
    "                        color 140ms ease-out, transform 120ms ease-out;\n"
    "        }\n"
    "        #prometheusBody .pm-empty-chip:hover {\n"
    "            border-color: #C7F23E; background: #1B2F35; color: #E9E8E1;\n"
    "        }\n"
    "        #prometheusBody .pm-empty-chip:active {\n"
    "            transform: translateY(1px);\n"
    "        }\n"
    "        #prometheusBody .pm-empty-chip:focus-visible {\n"
    "            outline: 2px solid #3358FF; outline-offset: 2px;\n"
    "        }\n"
    "        </style>\n"
    "        <script>\n"
    "            // Empty-state chip -> composer helper. Fills #synthChatInput\n"
    "            // with the chip's data-example, focuses it, and moves the\n"
    "            // caret to the end so the user can edit before sending.\n"
    "            // Idempotent: safe to call before/after the chat has any\n"
    "            // messages (chips only render in the empty state).\n"
    "            function _pmFillFromChip(el) {\n"
    "                try {\n"
    "                    var ex = (el && el.getAttribute('data-example')) || '';\n"
    "                    var input = document.getElementById('synthChatInput');\n"
    "                    if (!input || !ex) return;\n"
    "                    input.value = ex;\n"
    "                    input.focus();\n"
    "                    var n = ex.length;\n"
    "                    try { input.setSelectionRange(n, n); } catch (_) {}\n"
    "                    // Fire input event so any autosizers / hint text\n"
    "                    // downstream (send button enable, char counter,\n"
    "                    // etc.) update as if the user typed it.\n"
    "                    input.dispatchEvent(new Event('input', { bubbles: true }));\n"
    "                } catch (e) { console.warn('[prometheus] chip fill failed', e); }\n"
    "            }\n"
    "        </script>\n"
)


SPLICES = [
    ("A launcher shadow",       A_OLD, A_NEW),
    ("B widget frame border",   B_OLD, B_NEW),
    ("C header + subtitle",     C_OLD, C_NEW),
    ("D collapse btn border",   D_OLD, D_NEW),
    ("E messages panel border", E_OLD, E_NEW),
    ("F empty state HTML",      F_OLD, F_NEW),
    ("G textarea composer",     G_OLD, G_NEW),
    ("H chips CSS + JS",        H_OLD, H_NEW),
]


def main() -> int:
    if not INDEX.is_file():
        print(f"[polish] {INDEX} not found")
        return 2

    src = INDEX.read_text(encoding="utf-8")
    orig_bytes = len(src.encode("utf-8"))
    print(f"[polish] {INDEX} ({orig_bytes:,} bytes)")

    # ---- Preflight: every splice's anchor must match EXACTLY once ----
    for label, old, _new in SPLICES:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(
                f"[{label}] anchor count = {n} (expected 1). "
                f"Widen or fix the anchor. Nothing written."
            )
    print(f"[polish] preflight OK - all {len(SPLICES)} anchors unique")

    # Backup BEFORE any change.
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[polish] backup written to {BACKUP}")

    # Apply.
    for label, old, new in SPLICES:
        src = src.replace(old, new)
        print(f"[polish]   {label}: applied")

    # Postflight sanity: no OLD anchor still present, each NEW present once.
    for label, old, new in SPLICES:
        if src.count(old) != 0:
            raise RuntimeError(f"[{label}] old anchor still present after replace")
        if src.count(new) != 1:
            raise RuntimeError(f"[{label}] new block not inserted exactly once")

    INDEX.write_text(src, encoding="utf-8")
    new_bytes = len(src.encode("utf-8"))
    delta = new_bytes - orig_bytes
    print(f"[polish] wrote {new_bytes:,} bytes ({delta:+,} bytes vs original)")
    print("[polish] run: python3 scripts/validate_index_html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
