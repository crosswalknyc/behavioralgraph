"""Overlap reads are a priced custom ask (2026-10-21, Bria).

'Of those viewers who watch Law & Order: SVU on Peacock, how many
subscribe to Netflix?' shipped a compiled overlap (usage vs payment,
ad-free vs ad-supported, the competitive stack) at the metered chat
rate because the title's profile was open. Opening a file does not
make a new cross-service count chat. Quote Custom Ask ($500) first,
charge on confirm, then generate.

A size question is its own $100 read (brain 26, Jenna 2026-10-21).
A Tania-style split that only asks who is 'on' a service, with no
subscribe / pay / incremental wording, stays the free cross (brain
48). A breakdown of a figure they already paid for stays the $100
derived read.
"""
from __future__ import annotations

import re

# Bria: of those viewers who watch X, how many subscribe to Y
_SUBSCRIBE_RX = re.compile(
    r"\b(?:subscribe[sd]?(?:\s+to)?|subscription|subscribers?|"
    r"pay(?:ing|s|ment)?\s+for|paid\s+for|"
    r"(?:hold|have|carry)\s+(?:an?\s+)?(?:active\s+)?(?:paid\s+)?"
    r"(?:[a-z0-9+./'-]+\s+){0,3}subscription)\b",
    re.I)
_OF_AUDIENCE_RX = re.compile(
    r"\b(?:of\s+(?:those|these|the|them)\b|"
    r"among\s+(?:those|these|the|them|whom)\b|"
    r"out\s+of\s+(?:those|these|the|them)\b|"
    r"how many\s+of\s+(?:those|these|them|the)\b|"
    r"what\s+(?:share|percent|percentage|portion)\s+of\b)",
    re.I)
_VIEWER_RX = re.compile(
    r"\b(?:viewers?|watchers?|audience|people who|users|fans|readers?|"
    r"listeners?|cohort)\b",
    re.I)
_HOW_MANY_SUB_RX = re.compile(
    r"\bhow many\b.{0,120}\b(?:subscribe|subscribers?|subscription|"
    r"pay(?:ing)?\s+for)\b",
    re.I)
_INCREMENTAL_RX = re.compile(
    r"\b(?:how\s+incremental(?:ly)?|incrementality|"
    r"incremental\s+(?:to|for|reach|lift|viewers?))\b",
    re.I)
_NEXT_WATCH_RX = re.compile(
    r"\b(?:what\s+(?:did|do|have)\b.{0,120}\b(?:watch(?:ed)?|stream(?:ed)?|read)\b"
    r".{0,80}\b(?:after|next|then|30[- ]days?|thirty\s+days?|t30d|post[- ]watch))\b",
    re.I)
_NOT_THIS_RX = re.compile(
    r"\b(?:what does (?:this|that|it|the (?:number|figure|percent))\s+mean|"
    r"craziest insights|"
    r"how big is|audience size|sample[- ]size|incidence|"
    r"where (?:do|can) i (?:find|see|get))\b",
    re.I)


def is_overlap_read(text):
    """True for a new-numbers overlap: of audience A, how many
    subscribe to / pay for B; incrementality of X to Y; what that
    audience watched next. False for size, insights, and 'what does
    this number mean'."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 800:
        return False
    if _NOT_THIS_RX.search(t):
        return False
    if _INCREMENTAL_RX.search(t) or _NEXT_WATCH_RX.search(t):
        return True
    if _HOW_MANY_SUB_RX.search(t) and (_OF_AUDIENCE_RX.search(t) or _VIEWER_RX.search(t)):
        return True
    if _OF_AUDIENCE_RX.search(t) and _SUBSCRIBE_RX.search(t):
        return True
    return False
