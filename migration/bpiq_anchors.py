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


def _entry_words(e) -> dict:
    """The question a banked read answered and the labels of its lines
    (which side's audience it sizes, which side's buyers it counts)."""
    labels = [str(m.get("label") or "") for m in (e.get("metrics") or []) if isinstance(m, dict) and m.get("label")]
    out = {}
    if e.get("q"):
        out["question"] = str(e.get("q"))[:300]
    if labels:
        out["labels"] = labels[:12]
    return out


# --------------------------------------------------------------- forward
# A test (or a caller that already holds them) can supply the ledger
# index document and who a read was given to.
INDEX_FOR_TESTS = None
ASKER_LOOKUP = None          # (anchor) -> the username the read was given to, or ''
USERS_FOR_TESTS = None       # the users document ({'users': {...}}) for the company check


def _load_ledger_index():
    """The insights ledger index, or None. Never reaches the network in
    a hermetic run."""
    if isinstance(INDEX_FOR_TESTS, dict):
        return INDEX_FOR_TESTS
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
                            "as_of": str(e.get("ts") or ""), **_entry_words(e), **figs}
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
    """{'value_usd', 'people'}: what the valuation is held at or below:
    the read's own figures. Only a window whose end date the reader
    named in words (`inputs['end_named']`) and that runs longer than the
    read's scales them: the value by days, the people by the square
    root of days (the same window rule the catalog hold uses)."""
    out = {}
    if not anchor:
        return out
    v0, v1 = valuation_window(inputs, windows)
    a0, a1 = _date((anchor.get("window") or {}).get("start")), _date((anchor.get("window") or {}).get("end"))
    scale_v = scale_p = 1.0
    # The ceiling never rises because the window got longer (2026-11-11:
    # a "so far" figure given on Nov 11 was scaled to $4,548,481.67 for
    # an order placed Nov 15 and $4,796,580.67 by Nov 18). A later,
    # longer window is a new read only when the reader named a new end
    # date in words; an order that runs "to today" is the same read.
    if v0 and v1 and a0 and a1 and (inputs or {}).get("end_named"):
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


# ------------------------------------------------- the same read, pinned
def read_on_pair(a, b, index: Optional[dict] = None) -> Optional[dict]:
    """The first read delivered on the pairing, in either order of the
    names, whatever its window; None when there is none. Same shape as
    `banked_read`. First-held wins: the earliest figure given on the
    pairing is the one a later order is held to."""
    if not enabled():
        return None
    try:
        want = pair_key(a, b)
        if len(want) != 2 or "" in want:
            return None
        doc = index if isinstance(index, dict) else _load_ledger_index()
        if not isinstance(doc, dict):
            return None
        best = None
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
                figs = figures_of(e)
                if not (e0 and e1) or not (figs.get("value_usd") or figs.get("people")):
                    continue
                rec = {"subject": str(bucket.get("subject") or ""), "key": str(e.get("k") or ""),
                       "window": {"start": e0.isoformat(), "end": e1.isoformat()},
                       "as_of": str(e.get("ts") or ""), **_entry_words(e), **figs}
                if best is None or (rec["as_of"] or "~") < (best["as_of"] or "~"):
                    best = rec
        return best
    except Exception as e:
        print(f"[bpiq-anchors] pair read lookup skipped: {type(e).__name__}: {e}")
        return None


_ASKERS: dict = {}
ASKER_SCAN_CAP = 400      # the most ask records one lookup reads


def _norm_q(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def asker_of(anchor: Optional[dict]) -> str:
    """The seat a banked read was given to, from the ask log of the day
    it landed (the ledger does not keep who asked). '' when it cannot
    be told. Cached per process; never reaches the network in a hermetic
    run."""
    if not isinstance(anchor, dict):
        return ""
    if callable(ASKER_LOOKUP):
        try:
            return str(ASKER_LOOKUP(anchor) or "")
        except Exception:
            return ""
    key = str(anchor.get("key") or "")
    if key in _ASKERS:
        return _ASKERS[key]
    if os.environ.get("REGRESSION_TEST_MODE"):
        return ""
    who = ""
    try:
        import json
        import insights_ledger as _il
        want = _norm_q(anchor.get("question"))
        d0 = _date(str(anchor.get("as_of") or "")[:10])
        if want and d0:
            s3, bucket = _il._client(), _il.S3_BUCKET
            # the read landed minutes after its question: the ninety
            # minutes before it are read, newest first, and nothing else
            # (a chat turn waits on this once per read, then it is cached)
            m = re.search(r"T(\d{2}):(\d{2}):(\d{2})", str(anchor.get("as_of") or ""))
            end = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + 120) if m else 86400
            start = end - 5400

            def _sec(k):
                mm = re.search(r"/(\d{2})(\d{2})(\d{2})_[^/]*$", k)
                return (int(mm.group(1)) * 3600 + int(mm.group(2)) * 60 + int(mm.group(3))) if mm else -1

            spans = [(d0, max(start, 0), end)]
            if start < 0:
                spans.append((d0 - _dt.timedelta(days=1), 86400 + start, 86400))
            for d, lo, hi in spans:
                hit = None
                try:
                    body = s3.get_object(Bucket=bucket, Key=f"system/usage/ask_log/{d.isoformat()}.jsonl")["Body"].read().decode("utf-8")
                    for ln in body.splitlines():
                        if not ln.strip():
                            continue
                        r = json.loads(ln)
                        if isinstance(r, dict) and _norm_q(r.get("question")) == want and r.get("user"):
                            hit = r
                            break
                except Exception:
                    hit = None
                if hit is None:
                    keys = []
                    for page in s3.get_paginator("list_objects_v2").paginate(
                            Bucket=bucket, Prefix=f"system/usage/ask_log/{d.isoformat()}/"):
                        keys += [c["Key"] for c in page.get("Contents") or []]
                    near = sorted([k for k in keys if lo <= _sec(k) <= hi], reverse=True)
                    for k in near[:ASKER_SCAN_CAP]:
                        try:
                            r = json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read().decode("utf-8"))
                        except Exception:
                            continue
                        if isinstance(r, dict) and _norm_q(r.get("question")) == want and r.get("user"):
                            hit = r
                            break
                if hit:
                    who = str(hit["user"])
                    break
    except Exception as e:
        print(f"[bpiq-anchors] asker lookup skipped: {type(e).__name__}: {e}")
        who = ""
    _ASKERS[key] = who
    return who


def _company_of(username, users=None) -> str:
    try:
        doc = users if isinstance(users, dict) else USERS_FOR_TESTS
        if not isinstance(doc, dict):
            if os.environ.get("REGRESSION_TEST_MODE"):
                return ""
            doc = None
            try:
                from prometheus.host import host as _host
                doc = _host.load_users() if (not callable(getattr(_host, "has", None)) or _host.has("load_users")) else None
            except Exception:
                doc = None
            if not isinstance(doc, dict) or not doc.get("users"):
                # a job off the dashboard: the seat list is read as it is stored
                import json
                import insights_ledger as _il
                doc = json.loads(_il._client().get_object(Bucket=_il.S3_BUCKET, Key="system/users.json")["Body"].read())
        rec = ((doc or {}).get("users") or {}).get(str(username or "")) or {}
        return " ".join(str(rec.get("company") or "").lower().split())
    except Exception:
        return ""


def given_to(anchor: Optional[dict], username, history=None, users=None) -> bool:
    """True when the read was given to this reader or to a seat at the
    reader's company: it sits in this thread (its figure is on an agent
    turn), or the ask log names who asked and the companies agree."""
    if not isinstance(anchor, dict) or not anchor.get("value_usd"):
        return False
    try:
        shown = f"{int(round(float(anchor['value_usd']))):,}"
        for t in (history or []):
            if isinstance(t, dict) and str(t.get("role") or "") in ("agent", "assistant") \
                    and shown in str(t.get("text") or t.get("content") or ""):
                return True
        who = asker_of(anchor)
        me = str(username or "").strip()
        if not who or not me:
            return False
        if who.lower() == me.lower():
            return True
        ca, cb = _company_of(who, users), _company_of(me, users)
        return bool(ca) and ca == cb
    except Exception:
        return False


def pin_window(inputs: dict, anchor: Optional[dict]) -> tuple:
    """(inputs, pinned). An order whose window is open-ended ("to
    today", "so far", an end read as today) on a pairing and start date
    the reader already holds a figure for runs on that read's window:
    the same start, the read's end, the attribution window the same.
    The order placed on a later day is the same read, not a longer one
    (2026-11-11). Anything else is returned as given."""
    d = dict(inputs or {})
    try:
        if not isinstance(anchor, dict) or not d.get("window_open"):
            return d, False
        w = anchor.get("window") or {}
        a0, a1 = _date(w.get("start")), _date(w.get("end"))
        s0 = _date(d.get("event_start"))
        if not (a0 and a1 and s0) or s0 != a0:
            return d, False
        d["event_end"] = a1.isoformat()
        d["post_start"], d["post_end"] = a0.isoformat(), a1.isoformat()
        d["pinned_read"] = {"key": anchor.get("key"), "start": a0.isoformat(), "end": a1.isoformat(),
                            "value_usd": anchor.get("value_usd")}
        d.pop("window_open", None)
        return d, True
    except Exception:
        return dict(inputs or {}), False


def same_window(anchor: Optional[dict], inputs: dict, windows: Optional[dict] = None) -> bool:
    """The valuation's campaign window is the read's own window."""
    try:
        w = (anchor or {}).get("window") or {}
        a0, a1 = _date(w.get("start")), _date(w.get("end"))
        ev = (windows or {}).get("event") or {}
        v0 = _date(ev.get("start") or (inputs or {}).get("event_start"))
        v1 = _date(ev.get("end") or (inputs or {}).get("event_end"))
        return bool(a0 and a1 and v0 and v1 and a0 == v0 and a1 == v1)
    except Exception:
        return False


# ------------------------------------------- the saved research, either order
def record_keys(inputs: dict, run_key: Callable) -> list:
    """Every key the saved research for this order could sit under: the
    order as given, then the pairing written either way round as the
    campaign name, then the two names in the other roles. Same window,
    same control flag."""
    d = dict(inputs or {})
    t, b = str(d.get("qualifier") or "").strip(), str(d.get("brand_partner") or "").strip()
    out, seen = [], set()

    def _add(**over):
        try:
            k = run_key(dict(d, **over))
        except Exception:
            return
        if k and k not in seen:
            seen.add(k)
            out.append(k)

    _add()
    if t and b:
        for talent, brand in ((t, b), (b, t)):
            for camp in (f"{talent} x {brand}", f"{brand} x {talent}", ""):
                _add(qualifier=talent, brand_partner=brand, campaign=camp)
    return out


def saved_record(inputs: dict, underpinnings, run_key: Callable) -> tuple:
    """(document, key) of the research already saved for this pairing
    and window, in either order of the names; (None, '') when there is
    none. The order's own key is tried first."""
    if underpinnings is None:
        return None, ""
    for k in record_keys(inputs, run_key):
        try:
            doc = underpinnings.load(k)
        except Exception:
            doc = None
        if isinstance(doc, dict) and isinstance(doc.get("primitives"), dict) and doc["primitives"].get("audience_size"):
            return doc, k
    return None, ""


def adopt_record(inputs: dict, doc: Optional[dict]) -> dict:
    """The order named the way its saved research is named (who is the
    talent, who is the brand, how the campaign is spelled), so the same
    research is read back and every draw repeats. Dates, the control
    flag and everything else stay the order's own."""
    d = dict(inputs or {})
    saved = (doc or {}).get("inputs") if isinstance(doc, dict) else None
    if not isinstance(saved, dict):
        return d
    for k in ("qualifier", "brand_partner", "campaign"):
        if saved.get(k) is not None:
            d[k] = saved.get(k)
    return d


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
        # a valuation whose audience is the talent's own audience in the
        # window is not a campaign reach: nothing here is its label
        if str(cb.get("audience_basis") or "").strip().lower().startswith("talent audience"):
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


__all__ = ["enabled", "banked_read", "read_on_pair", "asker_of", "given_to", "pin_window", "same_window", "record_keys",
           "saved_record", "adopt_record", "prompt_fields", "ceilings", "hold", "label_people", "figures_of", "pair_of", "pair_key",
           "pairing_in", "saved_valuation", "valuation_block", "valuation_window", "ENV"]
