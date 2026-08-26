#!/usr/bin/env python3
"""Pay-as-you-go offer flow in the chat widget (2026-08-26, Jenna).

Splices four blocks into templates/index.html (byte-level per
index-html-safety.mdc - StrReplace truncates files this large):

1. `_pmPpuOffer` state var - stashes the original ask while the
   offer's Yes/No chips are on screen.
2. `_pmAnalyze` intercept - a `pay_per_use_offer` reply renders
   Jenna's exact copy with Yes/No chips instead of an answer.
3. `_pmStartDeck` intercept - same for the deck kickoff.
4. `synthChatSubmit` follow-through - Yes opts the user in via
   /api/brief-chat/pay-per-use then replays the stashed ask
   seamlessly; No politely declines with no charge; anything else
   drops the offer and routes normally.

Run from the bg-webapp root, then validate:
    python3 scripts/splice_ppu_offer_flow.py
    python3 scripts/validate_index_html.py
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_ppu_offer.html")

# --- 1. State var ----------------------------------------------------------
OLD_STATE = """        var _pmAnalysisActive = false;
        var _pmPendingDeckAngle = null;
        var _pmDeckInFlight = false;
"""
NEW_STATE = """        var _pmAnalysisActive = false;
        var _pmPendingDeckAngle = null;
        var _pmDeckInFlight = false;
        // Pay-as-you-go offer (2026-08-26, Jenna): when a pulls-only
        // user triggers an analysis feature the server answers with
        // the offer instead. The original ask is stashed here so a
        // Yes can replay it seamlessly after the opt-in.
        var _pmPpuOffer = null;
"""

# --- 2. Analyze intercept --------------------------------------------------
OLD_ANALYZE = """                    mode: mode || _pmModeForText(text) || ''
                });
                var data = _fr.data;
                if (!data || !_fr.r.ok || !data.success) {
"""
NEW_ANALYZE = """                    mode: mode || _pmModeForText(text) || ''
                });
                var data = _fr.data;
                if (data && data.success && data.pay_per_use_offer) {
                    // Not subscribed to analysis: show Jenna's offer
                    // with Yes/No chips and stash the ask for replay.
                    _pmPpuOffer = { kind: 'analyze', text: text,
                                    ctx: ctx, mode: mode || '' };
                    _synthChatPushTurnWithMeta('agent', data.reply, {
                        options: [ { label: 'Yes', send: 'Yes' },
                                   { label: 'No', send: 'No' } ] });
                    return 'done';
                }
                if (!data || !_fr.r.ok || !data.success) {
"""

# --- 3. Deck intercept -----------------------------------------------------
OLD_DECK = """                    history: synthChatHistory.slice(-14)
                });
                var data = _fr.data;
                if (data && data.success && data.clarify) {
"""
NEW_DECK = """                    history: synthChatHistory.slice(-14)
                });
                var data = _fr.data;
                if (data && data.success && data.pay_per_use_offer) {
                    // Decks are an analysis-tier feature. Same offer,
                    // same Yes/No chips; the ask replays after opt-in.
                    _pmPpuOffer = { kind: 'deck', angle: angle,
                                    text: String(text || '') };
                    _synthChatPushTurnWithMeta('agent', data.reply, {
                        options: [ { label: 'Yes', send: 'Yes' },
                                   { label: 'No', send: 'No' } ] });
                    return;
                }
                if (data && data.success && data.clarify) {
"""

# --- 4. Submit follow-through ----------------------------------------------
OLD_SUBMIT = """            if (!text) return;
            input.value = '';
            synthChatPushTurn('user', text);

            // Incidence-offer follow-through (2026-08-19). If the
"""
NEW_SUBMIT = """            if (!text) return;
            input.value = '';
            synthChatPushTurn('user', text);

            // Pay-as-you-go offer follow-through (2026-08-26, Jenna).
            // The last agent turn was "Not subscribed to this
            // feature. Turn it on for pay as you go?". Yes turns it
            // on (it persists until an admin changes the tier) and
            // replays the original ask seamlessly. No politely
            // declines - nothing is charged and the analyze
            // affordances stay visible for later.
            if (_pmPpuOffer) {
                var _ppuStash = _pmPpuOffer;
                var _ppuLow = text.trim().toLowerCase()
                    .replace(/[.!]+$/, '');
                if (_ppuLow === 'yes' || _ppuLow === 'y' ||
                    _ppuLow === 'yes please' || _ppuLow === 'sure' ||
                    _ppuLow === 'turn it on') {
                    _pmPpuOffer = null;
                    var _ppuOk = false;
                    try {
                        var _pr = await _synthChatFetchJson(
                            '/api/brief-chat/pay-per-use', {});
                        _ppuOk = !!(_pr.data && _pr.data.success &&
                                    (_pr.data.enabled ||
                                     _pr.data.already_subscribed));
                    } catch (_) { _ppuOk = false; }
                    if (!_ppuOk) {
                        synthChatPushTurn('agent', SYNTH_CHAT_CALM_MSG);
                        return;
                    }
                    if (_ppuStash.kind === 'deck') {
                        _pmPendingDeckAngle = _ppuStash.angle || null;
                        await _pmStartDeck(_ppuStash.text || '');
                    } else {
                        await _pmAnalyze(_ppuStash.text,
                                         _ppuStash.ctx ||
                                             _pmCollectPageContext(),
                                         _ppuStash.mode);
                    }
                    return;
                }
                if (_ppuLow === 'no' || _ppuLow === 'n' ||
                    _ppuLow === 'no thanks' || _ppuLow === 'not now' ||
                    _ppuLow === 'no thank you') {
                    _pmPpuOffer = null;
                    synthChatPushTurn('agent',
                        'No problem - nothing was turned on and nothing ' +
                        'was charged. The option is here whenever you ' +
                        'want it; profile pulls work as usual.');
                    return;
                }
                // Neither a yes nor a no: drop the offer and route
                // the message normally.
                _pmPpuOffer = null;
            }

            // Incidence-offer follow-through (2026-08-19). If the
"""

SPLICES = [
    (OLD_STATE, NEW_STATE, "ppu offer state var"),
    (OLD_ANALYZE, NEW_ANALYZE, "analyze offer intercept"),
    (OLD_DECK, NEW_DECK, "deck offer intercept"),
    (OLD_SUBMIT, NEW_SUBMIT, "submit yes/no follow-through"),
]


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    if "_pmPpuOffer" in src:
        print("[splice] _pmPpuOffer already present; nothing to do")
        return
    for old, new, desc in SPLICES:
        src = splice(src, old, new, desc)
        print(f"[splice] OK: {desc}")
    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote {INDEX} ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
