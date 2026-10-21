"""Creative format of every campaign asset (Attribution IQ, 2026-10-20).

Alexia on The Influencer Project: "it's not giving us the analysis on the
type of creative that converted best." The platform (TikTok, Instagram)
and the placement (creator clip, official trailer) were already read;
what she wanted is the FORMAT of the creative: a skit, a reaction, a
trailer cut, a behind-the-scenes clip, an interview, a trend, a vlog, a
meme, a giveaway.

Every asset is classified once into a fixed vocabulary from its title,
caption, note, placement type and channel: one batched model call per
~200 unclassified assets (never one per asset), with keyword rules as
the fallback and as the answer when the model is not available. The
result is cached in the campaign folder
(``intent/<slug>/mta/creative_formats.json``) so it never re-runs for an
asset already classified; new assets are classified on the next read.
Rules-based entries are upgraded to model entries when a model is
available later.

Deterministic, fail-safe: any failure leaves the rules answer in place
and never blocks the attribution read.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

FORMATS = (
    "Skit or comedy bit",
    "Reaction or review",
    "Trailer or clip cut",
    "Behind the scenes or on set",
    "Interview or Q&A",
    "Challenge or trend",
    "Vlog or get ready with me",
    "Meme or text post",
    "Giveaway or call to action",
    "Other",
)

CACHE_KEY_FMT = "intent/{slug}/mta/creative_formats.json"
BATCH = 200

_RULES = (
    ("Giveaway or call to action", r"\bgiveaway\b|\bwin tickets\b|\bfree tickets\b|\benter to win\b|\bpresale\b|\bget tickets\b|\btickets on sale\b|\blink in bio\b|\bbuy tickets\b|\bin theaters\b.*\bnow\b"),
    ("Trailer or clip cut", r"\btrailer\b|\bteaser\b|\bofficial (video|clip)\b|\bclip\b|\bscene\b|\bexclusive clip\b|\bsneak peek\b|\bfirst look\b|\bedit\b|\bfan edit\b"),
    ("Behind the scenes or on set", r"\bbehind the scenes\b|\bbts\b|\bon set\b|\bon-set\b|\bmaking of\b|\bset visit\b|\bpremiere\b|\bred carpet\b|\bscreening\b"),
    ("Interview or Q&A", r"\binterview\b|\bq&a\b|\bq and a\b|\btalks\b|\bsits down\b|\bconversation with\b|\bpress junket\b|\bcast on\b"),
    ("Reaction or review", r"\breview\b|\breaction\b|\breact(s|ing)?\b|\brating\b|\bthoughts on\b|\bverdict\b|\bworth (watching|seeing)\b|\bspoiler\b|\bletterboxd\b|\bcritic\b|\bmy honest\b"),
    ("Challenge or trend", r"\bchallenge\b|\btrend\b|\bpov\b|\bduet\b|\bstitch\b|\bsound\b|\btransition\b|\bdance\b"),
    ("Vlog or get ready with me", r"\bvlog\b|\bgrwm\b|\bget ready with me\b|\bday in (my|the) life\b|\bcome with me\b|\bmovie night\b|\bdate night\b|\bwhat i wore\b"),
    ("Skit or comedy bit", r"\bskit\b|\bsketch\b|\bcomedy\b|\bparody\b|\bbit\b|\bwhen you\b|\bme when\b|\bpov:\s|\bimpression\b|\bfunny\b"),
    ("Meme or text post", r"\bmeme\b|\btext post\b|\bthread\b|\btweet\b|\bquote\b|\bcaption\b|\bposter\b|\bstill\b"),
)


def _norm_label(s: str) -> Optional[str]:
    t = re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()
    for f in FORMATS:
        if re.sub(r"[^a-z0-9]+", " ", f.lower()).strip() == t:
            return f
    head = {
        "skit": "Skit or comedy bit", "comedy": "Skit or comedy bit",
        "reaction": "Reaction or review", "review": "Reaction or review",
        "trailer": "Trailer or clip cut", "clip": "Trailer or clip cut",
        "behind": "Behind the scenes or on set", "bts": "Behind the scenes or on set",
        "interview": "Interview or Q&A", "q a": "Interview or Q&A",
        "challenge": "Challenge or trend", "trend": "Challenge or trend",
        "vlog": "Vlog or get ready with me", "grwm": "Vlog or get ready with me",
        "meme": "Meme or text post", "text": "Meme or text post",
        "giveaway": "Giveaway or call to action", "cta": "Giveaway or call to action",
        "other": "Other",
    }
    for k, v in head.items():
        if t.startswith(k):
            return v
    return None


def _asset_text(a: dict) -> str:
    """The caption and the note: what the creator wrote. The placement type
    and the channel are read separately (a 'Creator Clip' placement must
    not make every caption a clip cut)."""
    parts = [str(a.get("action_label") or ""), str(a.get("note") or "")]
    return " ".join(p for p in parts if p)


def rules_format(a: dict) -> str:
    """Keyword classification of one asset. The placement type decides
    first (an official trailer is a trailer cut, a press review is a
    review, an interview is an interview); the caption decides the rest."""
    at = str(a.get("asset_type") or "").lower()
    ch = str(a.get("channel") or "").lower()
    if "trailer" in at or "official video" in at or "exclusive clip" in at:
        return "Trailer or clip cut"
    if "review" in at:
        return "Reaction or review"
    if "interview" in at:
        return "Interview or Q&A"
    if "press" in ch and ("news" in at or "feature" in at):
        return "Meme or text post" if "x" == ch else "Reaction or review"
    text = _asset_text(a).lower()
    for label, pat in _RULES:
        if re.search(pat, text, re.I):
            return label
    if "creator clip" in at or "creator post" in at:
        # a clip account (@xehclips, @movie.cut.clip) posts cuts of the film;
        # any other creator clip with no signal in its caption stays Other
        handle = str(a.get("action_label") or "") + " " + str(a.get("url") or "")
        if re.search(r"clip|cut|scene|edit|film|movie|cinema|screen", handle, re.I):
            return "Trailer or clip cut"
    return "Other"


def _s3():
    try:
        import intent_iq as _intent_iq  # type: ignore
        return _intent_iq._s3()
    except Exception:
        try:
            import boto3
            return boto3.client("s3", region_name=os.environ.get("AWS_REGION") or "us-east-2")
        except Exception:
            return None


def _bucket() -> str:
    return os.environ.get("INTENT_S3_BUCKET", "dashboard-inputs")


def load_cache(slug: str, s3=None) -> dict:
    """``s3=False`` means no store at all (hermetic tests); None means the default client."""
    if s3 is False:
        return {}
    s3 = s3 or _s3()
    if not s3:
        return {}
    try:
        obj = s3.get_object(Bucket=_bucket(), Key=CACHE_KEY_FMT.format(slug=slug))
        d = json.loads(obj["Body"].read().decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cache(slug: str, entries: dict, s3=None) -> bool:
    if s3 is False:
        return False
    s3 = s3 or _s3()
    if not s3:
        return False
    try:
        s3.put_object(Bucket=_bucket(), Key=CACHE_KEY_FMT.format(slug=slug),
                      Body=json.dumps(entries, indent=1, sort_keys=True).encode("utf-8"),
                      ContentType="application/json")
        return True
    except Exception as e:
        logger.warning("creative formats: cache save failed for %s: %s", slug, e)
        return False


def _model_available() -> bool:
    if (os.environ.get("REGRESSION_TEST_MODE") or "").strip() in ("1", "true", "yes"):
        return False
    try:
        from claude_client import is_claude_reasoning_enabled
        return bool(is_claude_reasoning_enabled())
    except Exception:
        return False


_SYSTEM = (
    "You classify promotional social assets for a film campaign into exactly one creative format each. "
    "The formats, use these labels verbatim: " + "; ".join(FORMATS) + ". "
    "Read the caption, the note and the placement type. A cut of the film's footage, a trailer or a teaser is "
    "'Trailer or clip cut'. A creator talking about whether the film is good, reacting, rating or spoiling it is "
    "'Reaction or review'. A comedy bit, parody or 'when you...' sketch is 'Skit or comedy bit'. Set visits, "
    "premieres, red carpets and making-of are 'Behind the scenes or on set'. Cast or director conversations are "
    "'Interview or Q&A'. Trends, sounds, duets, POV formats and dances are 'Challenge or trend'. Vlogs, movie-night "
    "and get-ready-with-me are 'Vlog or get ready with me'. Stills, posters, text-only posts and memes are 'Meme or "
    "text post'. Giveaways, ticket pushes and link-in-bio calls are 'Giveaway or call to action'. Use 'Other' only "
    "when nothing fits. Answer with one JSON object mapping asset_id to the format label and nothing else."
)


def classify_with_model(items: list[dict]) -> dict:
    """One batched model call for up to BATCH assets. {asset_id: format}
    for the labels it returned in the vocabulary; {} on any failure."""
    if not items:
        return {}
    try:
        from claude_client import claude_reason_json
    except Exception:
        return {}
    lines = []
    for a in items:
        lines.append(json.dumps({
            "asset_id": str(a.get("asset_id")),
            "channel": str(a.get("channel") or ""),
            "placement": str(a.get("asset_type") or ""),
            "caption": str(a.get("action_label") or "")[:220],
            "note": str(a.get("note") or "")[:220],
        }, ensure_ascii=False))
    user = "Assets, one per line:\n" + "\n".join(lines) + "\n\nReturn the JSON object now."
    try:
        raw = claude_reason_json(system=_SYSTEM, user=user, max_tokens=12000, temperature=0.0,
                                 usage_tag="mta_creative_formats")
    except Exception as e:
        logger.warning("creative formats: model call failed: %s", e)
        return {}
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            f = _norm_label(v)
            if f:
                out[str(k)] = f
    return out


def formats_for_campaign(slug: str, cards: list, s3=None, use_model: Optional[bool] = None) -> dict:
    """asset_id -> creative format for every asset of the campaign.
    Cache-first; rules for anything new; one batched model call per 200
    unclassified (or rules-classified) assets when a model is available;
    the cache is rewritten only when something changed."""
    cards = [c for c in (cards or []) if isinstance(c, dict) and c.get("asset_id")]
    flat: list[dict] = []
    for c in cards:
        if c.get("members") and c.get("member_views") is not None:
            continue   # grouped cards are built from their members; classify the members
        flat.append(c)
    if s3 is None:
        s3 = _s3()
    cache = load_cache(slug, s3)
    changed = False
    out: dict = {}
    for a in flat:
        aid = str(a["asset_id"])
        e = cache.get(aid)
        if isinstance(e, dict) and _norm_label(e.get("format")):
            out[aid] = _norm_label(e.get("format"))
        else:
            out[aid] = rules_format(a)
            cache[aid] = {"format": out[aid], "basis": "rules"}
            changed = True
    want_model = _model_available() if use_model is None else bool(use_model)
    if want_model:
        pending = [a for a in flat if (cache.get(str(a["asset_id"])) or {}).get("basis") != "model"]
        for i in range(0, len(pending), BATCH):
            got = classify_with_model(pending[i:i + BATCH])
            for aid, f in got.items():
                if aid in out:
                    out[aid] = f
                    cache[aid] = {"format": f, "basis": "model"}
                    changed = True
    if changed:
        save_cache(slug, cache, s3)
    return out


def format_summary(formats: dict) -> dict:
    """{format: asset count}, for logs and the report."""
    out: dict = {}
    for f in formats.values():
        out[f] = out.get(f, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
