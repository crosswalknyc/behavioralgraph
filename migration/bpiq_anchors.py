"""A valuation never starts cold on a pairing we already hold, and a
read never contradicts a valuation we already saved (2026-11-11,
Samantha Kolton at CAA).

Her general read on Gap x JYT (September 22 to November 11, 2026) said
$4,217,683 on 2,184,637 people reached. The valuation engine, run for
the same pairing and window, came back at $5,884,866.46 on 444,425
people: it had looked for earlier figures under "JYT x Gap", the read
was filed under "Gap x JYT", and nothing held the two together. The
journey builder has had this rule since 2026-10-05 (`corpus_anchors`
and `apply_attribution_anchors` in `migration/journey_synthesis.py`);
this module is the same rule for Brand Partnership IQ.

Forward (a valuation after a read):

- `banked_read(inputs, windows)`: the read already delivered on this
  pairing, in either order of the two names, whose window overlaps the
  valuation's. Read from the insights ledger index.
- `prompt_fields(anchor)`: the read's figures for the research prompt,
  binding.
- `ceilings(anchor, windows)`: the total value and the people reached
  the valuation is held at or below. A valuation window inside the
  read's keeps the read's own figures; a longer one scales them.
- `hold(payload, anchor, windows, rebuild, salt)`: after the build, when
  the total value or the people reached sits above the read's, the
  payload is rebuilt at one factor so both land just under (a salted
  margin, never pinned). Every count moves by the same factor, so every
  lens, share and derived line still adds up. It only ever pulls down.

Reverse (a read after a valuation):

- `saved_valuation(text_or_pairing, window)`: the valuation already
  saved on the pairing a question names, in either order.
- `valuation_block(text, window)`: its figures as a binding block for
  the read's prompt.

Kill switch for hermetic tests: `BPIQ_CORPUS_ANCHORS=0`. Fail-safe in
every direction: no ledger, no match, or any error means the build
proceeds exactly as before.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
from typing import Callable, Optional

ENV = "BPIQ_CORPUS_ANCHORS"
_PAIR_SPLIT_RX = re.compile(r"\s+[xX\u00d7]\s+")
_TAIL_RX = re.compile(r"\s+(?:partnership|collab(?:oration)?|campaign|deal|sponsorship|valuation)$", re.I)
_YEARLY_RX = re.compile(r"12\s*months|twelve\s+months|year\s*one|first\s+year|annual|full[- ]year|per\s+year|yearly", re.I)
_VALUE_RX = re.compile(r"partnership|brand\s+value|\bvalue\b", re.I)
_REACH_RX = re.compile(r"audience|reach|reached|engagers?|people\s+reached", re.I)
_NEW_BUYER_RX = re.compile(r"first[- ]time|new[- ]to|\bnew\b", re.I)
_BUYER_RX = re.compile(r"buyers?|purchasers?", re.I)
_AUDIENCE_RX = re.compile(r"\baudience\b|\bfan\s*base\b|\bfollowing\b", re.I)
_REACHED_RX = re.compile(r"\breached\b|\bcampaign\s+reach\b", re.I)
REACH_LABEL = "People the campaign reached"
HOLD_MARGIN = (0.955, 0.992)       # the held figure lands this share of its ceiling, salted
MAX_REBUILDS = 6


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def _fold(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def pair_of(name) -> Optional[tuple]:
    """('Gap', 'JYT') for 'Gap x JYT' or 'Gap x JYT partnership'."""
    t = _TAIL_RX.sub("", " ".join(str(name or "").split()))
    parts = [p.strip() for p in _PAIR_SPLIT_RX.split(t, maxsplit=1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def pair_key(a, b) -> frozenset:
    return frozenset((_fold(a), _fold(b)))


def _date(s):
    try:
        return _dt.date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _overlap_days(a0, a1, b0, b1) -> int:
    if not (a0 and a1 and b0 and b1):
        return 0
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days + 1)


def _unit(salt, *parts) -> float:
    h = hashlib.sha256("|".join([str(salt)] + [str(p) for p in parts]).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def valuation_window(inputs: dict, windows: Optional[dict] = None) -> tuple:
    """(start, end) dates of what the valuation covers: the campaign
    start through the end of the attribution window."""
    w = windows or {}
    start = _date((w.get("event") or {}).get("start") or inputs.get("event_start"))
    end = _date((w.get("post") or {}).get("end") or inputs.get("post_end") or inputs.get("event_end"))
    ev_end = _date((w.get("event") or {}).get("end") or inputs.get("event_end"))
    if end and ev_end and end < ev_end:
        end = ev_end
    return start, end


# --------------------------------------------------------------- forward
def _load_ledger_index():
    """The insights ledger index, or None. Never reaches the network in
    a hermetic run."""
    if os.environ.get("REGRESSION_TEST_MODE"):
        return None
    try:
        import insights_ledger as _il
        return _il._load_index()
    except Exception as e:
        print(f"[bpiq-anchors] ledger index unavailable: {type(e).__name__}: {e}")
        return None


def _metric_num(m):
    try:
        return float(m.get("value"))
    except (TypeError, ValueError, AttributeError):
        return None


def figures_of(entry: dict) -> dict:
    """The figures a banked partnership read states: the value for its
    window, the people reached, the brand's buyers among them and the
    first-time buyers. Any may be missing."""
    out = {}
    usd, people = [], []
    for m in (entry.get("metrics") or []):
        if not isinstance(m, dict):
            continue
        v = _metric_num(m)
        if v is None or v <= 0:
            continue
        unit = str(m.get("unit") or "").lower()
        text = f"{m.get('label') or ''} {str(m.get('name') or '').replace('_', ' ')}"
        if unit in ("usd", "dollars", "$"):
            usd.append((v, text))
        elif unit in ("people", "individuals", "persons", "users", "viewers", "count"):
            people.append((v, text))
    window_usd = [v for v, t in usd if _VALUE_RX.search(t) and not _YEARLY_RX.search(t)]
    if window_usd:
        out["value_usd"] = window_usd[0]
    yearly = [v for v, t in usd if _YEARLY_RX.search(t)]
    if yearly:
        out["value_year_usd"] = yearly[0]
    new_b = [v for v, t in people if _BUYER_RX.search(t) and _NEW_BUYER_RX.search(t)]
    if new_b:
        out["first_time_buyers"] = int(round(new_b[0]))
    buyers = [v for v, t in people if _BUYER_RX.search(t) and not _NEW_BUYER_RX.search(t)]
    if buyers:
        out["brand_buyers"] = int(round(max(buyers)))
    reach = [v for v, t in people if _REACH_RX.search(t) and not _BUYER_RX.search(t)]
    if reach:
        out["people"] = int(round(max(reach)))
    elif people:
        out["people"] = int(round(max(v for v, _t in people)))
    # the talent's own audience, when the read states it as one ("JYT
    # projected US audience"): a different measure from the people a
    # campaign reached, kept so a valuation can name it beside its reach
    aud = [v for v, t in people if _AUDIENCE_RX.search(t) and not _BUYER_RX.search(t) and not _REACHED_RX.search(t)]
    if aud:
        out["talent_audience"] = int(round(max(aud)))
    return out


def banked_read(inputs: dict, windows: Optional[dict] = None, index: Optional[dict] = None) -> Optional[dict]:
    """The read already delivered on this pairing and window, or None.

    `index` is the insights ledger index document (tests pass one); with
    none given it is read from the ledger. The pairing matches in either
    order of the two names. Among the entries whose window overlaps the
    valuation's, the largest overlap wins, then the newest."""
    if not enabled():
        return None
    try:
        want = pair_key(inputs.get("qualifier"), inputs.get("brand_partner"))
        if len(want) != 2 or "" in want:
            return None
        doc = index if isinstance(index, dict) else _load_ledger_index()
        if not isinstance(doc, dict):
            return None
        v0, v1 = valuation_window(inputs, windows)
        best, best_rank = None, None
        for skey, bucket in (doc.get("subjects") or {}).items():
            if not isinstance(bucket, dict):
                continue
            pr = pair_of(bucket.get("subject") or str(skey).replace("_", " "))
            if not pr or pair_key(*pr) != want:
                continue
            for e in (bucket.get("entries") or []):
                if not isinstance(e, dict) or e.get("retired"):
                    continue
                e0, e1 = _date(e.get("ws")), _date(e.get("we"))
                ov = _overlap_days(v0, v1, e0, e1)
                if ov <= 0:
                    continue
                figs = figures_of(e)
                if not (figs.get("value_usd") or figs.get("people")):
                    continue
                rank = (ov, str(e.get("ts") or ""))
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best = {"subject": str(bucket.get("subject") or ""), "key": str(e.get("k") or ""),
                            "window": {"start": e0.isoformat(), "end": e1.isoformat()},
                            "as_of": str(e.get("ts") or ""), **figs}
        if best:
            print(f"[bpiq-anchors] banked read on {best['subject']} "
                  f"{best['window']['start']} to {best['window']['end']}: "
                  f"value {best.get('value_usd')}, people {best.get('people')}")
        return best
    except Exception as e:
        print(f"[bpiq-anchors] banked read lookup skipped: {type(e).__name__}: {e}")
        return None


def prompt_fields(anchor: Optional[dict]) -> dict:
    """What the research prompt is handed: the delivered read's figures
    and the rule that binds the new work to them."""
    if not anchor:
        return {}
    blk = {"subject": anchor.get("subject"), "window": anchor.get("window")}
    for k_src, k_out in (("people", "people_reached_us"), ("value_usd", "partnership_value_usd"),
                         ("brand_buyers", "brand_buyers_among_them"), ("first_time_buyers", "first_time_brand_buyers")):
        if anchor.get(k_src):
            blk[k_out] = anchor[k_src]
    return {
        "delivered_read": blk,
        "delivered_read_rule": (
            "Binding. Crosswalk has already delivered a read on this same partnership for the window above. "
            "This valuation agrees with it: the people the partnership reached sit at or below "
            "people_reached_us, and every count here sits inside that group. The engagement shares, the "
            "platform shares and the conversion share are reasoned so that the total value this valuation "
            "adds up to lands at or below partnership_value_usd. Where this valuation counts the brand's "
            "buyers or first-time buyers among the people reached, it uses the delivered figures."),
    }


def ceilings(anchor: Optional[dict], inputs: dict, windows: Optional[dict] = None) -> dict:
    """{'value_usd', 'people'}: what the valuation is held at or below.
    A valuation window that sits inside the read's (within three days
    either side) keeps the read's figures as they are. A longer one
    scales them: the value by days, the people by the square root of
    days (the same window rule the catalog hold uses)."""
    out = {}
    if not anchor:
        return out
    v0, v1 = valuation_window(inputs, windows)
    a0, a1 = _date((anchor.get("window") or {}).get("start")), _date((anchor.get("window") or {}).get("end"))
    scale_v = scale_p = 1.0
    if v0 and v1 and a0 and a1:
        vd, ad = (v1 - v0).days + 1, (a1 - a0).days + 1
        if vd > ad + 3 and ad > 0:
            scale_v = float(vd) / float(ad)
            scale_p = scale_v ** 0.5
    if anchor.get("value_usd"):
        out["value_usd"] = float(anchor["value_usd"]) * scale_v
    if anchor.get("people"):
        out["people"] = float(anchor["people"]) * scale_p
    return out


def _total(payload) -> float:
    try:
        return float(((payload or {}).get("valuation") or {}).get("total_brand_value") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _people(payload) -> float:
    try:
        return float((payload or {}).get("projected_audience_size") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def hold(payload: dict, anchor: Optional[dict], inputs: dict, windows: Optional[dict],
         rebuild: Callable, salt: str = "", factor: Optional[float] = None) -> tuple:
    """(payload, record). The payload held at or below the delivered
    read: `rebuild(f)` returns the valuation with every count at the
    share `f` of what the research gave, so every lens, share and line
    is recomputed and the page adds up. Pulls down only; a valuation
    already at or under the read is returned untouched with an empty
    record. `factor` replays a hold saved with the run (no new draw)."""
    if factor is not None:
        try:
            f = float(factor)
        except (TypeError, ValueError):
            f = 1.0
        if 0.0 < f < 1.0:
            return rebuild(f), {"factor": round(f, 6), "replayed": True}
        return payload, {}
    if not anchor or not enabled():
        return payload, {}
    cap = ceilings(anchor, inputs, windows)
    tot, ppl = _total(payload), _people(payload)
    over_v = bool(cap.get("value_usd")) and tot > cap["value_usd"]
    over_p = bool(cap.get("people")) and ppl > cap["people"]
    if not (over_v or over_p):
        return payload, {}
    margin = HOLD_MARGIN[0] + (HOLD_MARGIN[1] - HOLD_MARGIN[0]) * _unit(salt, "bpiq-banked-hold")
    f = 1.0
    if over_v and tot > 0:
        f = min(f, cap["value_usd"] * margin / tot)
    if over_p and ppl > 0:
        f = min(f, cap["people"] * margin / ppl)
    if not (0.0 < f < 1.0):
        return payload, {}
    held = payload
    for _ in range(MAX_REBUILDS):
        held = rebuild(f)
        ok_v = not cap.get("value_usd") or _total(held) <= cap["value_usd"]
        ok_p = not cap.get("people") or _people(held) <= cap["people"]
        if ok_v and ok_p:
            break
        f *= 0.994          # a count's last digit nudged it over: one small step down
    record = {
        "factor": round(f, 6),
        "read_key": anchor.get("key"), "read_subject": anchor.get("subject"), "read_window": anchor.get("window"),
        "value_ceiling_usd": round(cap["value_usd"], 2) if cap.get("value_usd") else None,
        "people_ceiling": int(round(cap["people"])) if cap.get("people") else None,
        "total_before_usd": round(tot, 2), "total_after_usd": round(_total(held), 2),
        "people_before": int(round(ppl)), "people_after": int(round(_people(held))),
    }
    print(f"[bpiq-anchors] held to the delivered read on {anchor.get('subject')}: "
          f"total ${tot:,.2f} -> ${_total(held):,.2f}, people {int(ppl):,} -> {int(_people(held)):,} (factor {f:.4f})")
    return held, record


def label_people(payload: dict, anchor: Optional[dict], inputs: dict) -> dict:
    """Say what each people count is, so a valuation and the read it was
    held to never read as two answers to one question (2026-11-11: the
    held valuation showed 311,618 people beside a read that gave JYT's
    projected US audience as 2,184,637; they measure different things).

    The campaign block gains `reach_label` ("People the campaign
    reached") and, when the delivered read states the talent's own
    audience and it is larger than the reach, `talent_audience_held`
    (the read's figure, as it is) and one `people_line` naming both.
    The line rides `reach_line` once. No count moves. Returns what was
    set; {} when there is nothing to label. Never raises."""
    try:
        if not isinstance(payload, dict):
            return {}
        cb = payload.get("campaign")
        if not isinstance(cb, dict):
            return {}
        reach = int(round(float(payload.get("projected_audience_size") or 0)))
        if reach <= 0:
            return {}
        out = {"reach_label": REACH_LABEL}
        cb["reach_label"] = REACH_LABEL
        talent = str((inputs or {}).get("qualifier") or "").strip() or "The talent"
        held = int((anchor or {}).get("talent_audience") or 0) if isinstance(anchor, dict) else 0
        if held > reach:
            line = (f"The campaign reached {reach:,} people in the window. {talent}'s projected US audience is "
                    f"{held:,}; the campaign reached a part of it.")
            cb["talent_audience_held"] = held
            cb["people_line"] = line
            out.update({"talent_audience_held": held, "people_line": line})
            tail = f"{talent}'s projected US audience is {held:,}; the campaign reached a part of it."
            rl = str(cb.get("reach_line") or "").strip()
            if tail not in rl:
                cb["reach_line"] = (rl + " " + tail).strip() if rl else line
        return out
    except Exception as e:
        print(f"[bpiq-anchors] people labels skipped: {type(e).__name__}: {e}")
        return {}


# --------------------------------------------------------------- reverse
_TEXT_PAIR_RX = re.compile(
    r"(?<![\w&'])([A-Z0-9][\w&'.\-]*(?:\s+[A-Z0-9][\w&'.\-]*){0,3})\s+[xX\u00d7]\s+([A-Z0-9][\w&'.\-]*(?:\s+[A-Z0-9][\w&'.\-]*){0,3})")


_TEXT_PAIR_LOWER_RX = re.compile(r"(?<![\w&'])([a-z][\w&'.\-]{1,30})\s+x\s+([a-z][\w&'.\-]{1,30})(?![\w&'])")


def pairing_in(text) -> Optional[tuple]:
    """The talent and brand a question names as a pairing: 'Gap x JYT',
    'JYT is the talent and GAP is the brand', 'the partnership between
    JYT and Gap'. None when the words name no pairing."""
    t = " ".join(str(text or "").split())
    if not t:
        return None
    try:
        from prometheus import bpiq_slots as _slots
        got = _slots.parse(t)
        if got.get("talent") and got.get("brand"):
            return str(got["talent"]), str(got["brand"])
    except Exception:
        pass
    m = _TEXT_PAIR_RX.search(t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _TEXT_PAIR_LOWER_RX.search(t)       # typed in lower case: one word each side ("gap x jyt")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def saved_valuation(pairing, window: Optional[dict] = None, anchors_for: Optional[Callable] = None) -> Optional[dict]:
    """The Brand Partnership valuation already saved on a pairing, in
    either order of the names, or None. `pairing` is (a, b) or a text
    that names one. `anchors_for(subject, window=..., products=[...])`
    defaults to the corpus catalog's. With a `window`, a valuation whose
    own window does not overlap it is not returned."""
    if not enabled():
        return None
    try:
        pr = pairing if isinstance(pairing, (tuple, list)) and len(pairing) == 2 else pairing_in(pairing)
        if not pr or not _fold(pr[0]) or not _fold(pr[1]):
            return None
        if anchors_for is None:
            if os.environ.get("REGRESSION_TEST_MODE"):
                return None
            from migration import corpus_catalog as _cc
            anchors_for = _cc.anchors_for
        w0, w1 = _date((window or {}).get("start")), _date((window or {}).get("end"))
        best = None
        for name in (f"{pr[0]} x {pr[1]}", f"{pr[1]} x {pr[0]}"):
            got = anchors_for(name, window=window, products=["bpiq"], with_ledger=False) or {}
            by_source = {}
            for f in (got.get("facts") or []):
                if isinstance(f, dict) and f.get("product") == "bpiq":
                    by_source.setdefault(str(f.get("source_key") or ""), []).append(f)
            for src, facts in by_source.items():
                fw = next((f.get("window") for f in facts if isinstance(f.get("window"), dict)), {}) or {}
                f0, f1 = _date(fw.get("start")), _date(fw.get("end"))
                if w0 and w1 and f0 and f1 and _overlap_days(w0, w1, f0, f1) <= 0:
                    continue
                rec = {"subject": got.get("subject") or name, "source_key": src,
                       "window": {"start": fw.get("start"), "end": fw.get("end")},
                       "as_of": max((str(f.get("as_of") or "") for f in facts), default="")}
                for f in facts:
                    lbl = str(f.get("label") or "").lower()
                    try:
                        val = float(f.get("value"))
                    except (TypeError, ValueError):
                        continue
                    if "total brand value" in lbl:
                        rec["total_brand_value"] = val
                    elif "attributable to the partnership" in lbl:
                        rec["attributable_to_partnership"] = val
                    elif "incremental" in lbl:
                        rec["incremental_users"] = int(round(val))
                    elif f.get("kind") == "audience_size":
                        rec["people"] = int(round(val))
                if not (rec.get("total_brand_value") or rec.get("people")):
                    continue
                if best is None or rec["as_of"] > best["as_of"]:
                    best = rec
        return best
    except Exception as e:
        print(f"[bpiq-anchors] saved valuation lookup skipped: {type(e).__name__}: {e}")
        return None


def valuation_block(text, window: Optional[dict] = None, anchors_for: Optional[Callable] = None) -> str:
    """The binding block a read's prompt carries when the question names
    a pairing Crosswalk has already valued; '' otherwise."""
    v = saved_valuation(text, window=window, anchors_for=anchors_for)
    if not v:
        return ""
    w = v.get("window") or {}
    lines = []
    if v.get("total_brand_value"):
        lines.append(f"- total brand value = ${float(v['total_brand_value']):,.2f}")
    if v.get("people"):
        lines.append(f"- US people the partnership reached = {int(v['people']):,}")
    if v.get("attributable_to_partnership"):
        lines.append(f"- value attributable to the partnership = ${float(v['attributable_to_partnership']):,.2f}")
    if v.get("incremental_users"):
        lines.append(f"- new US people engaging with the brand = {int(v['incremental_users']):,}")
    if not lines:
        return ""
    return ("BRAND PARTNERSHIP VALUATION ALREADY ON THE DASHBOARD FOR THIS PAIRING (binding). "
            f"{v.get('subject')}, {w.get('start') or '?'} to {w.get('end') or '?'}. A read on this partnership "
            "states these figures as they are for that window: the same total value and the same people reached, "
            "never a second figure for either. For a different window, derive from these and say which window "
            "you are on.\n" + "\n".join(lines))


__all__ = ["enabled", "banked_read", "prompt_fields", "ceilings", "hold", "label_people", "figures_of", "pair_of", "pair_key",
           "pairing_in", "saved_valuation", "valuation_block", "valuation_window", "ENV"]
