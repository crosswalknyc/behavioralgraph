#!/usr/bin/env python3
"""The line under a valuation's title prints the window the counts cover
(2026-11-18, Coach x WNBA, Jenna: "fix it, for every valuation").

The dashboard prints one line of dates under a valuation's title:

    Campaign: A -> B . Attribution: Nd . Pre-window: ... . Post-window: ...

Until 2026-11-18 the page wrote the after window itself, as the
campaign's first day through `post_period.end` with the two lengths
added. That is right for one way a valuation is stored (the live pull:
the campaign plus the days after it) and wrong for the rest: a 177-day
season read "2026-05-08 -> 2026-11-17 (194d)", an order's window read
twice its length, a read of the days after an airing read from the
campaign's first day. The "Attribution" count printed the second
window's length, which is the days after the campaign only in the live
pull.

The line is now made in one place, `migration/bpiq_windows.py`, by the
function the deck builder reads its windows from, and the results route
sends it to the page finished. These checks hold:

  [1] one resolver: the deck builder's window function is this
      module's, and for every way a valuation is stored the page's
      after window is the deck's, date for date and day for day
  [2] the line, shape by shape: a campaign with a later tail of days
      (Coach x WNBA) or of months (MMG x Lowe's), a campaign valued on
      its last day (Willow Smith x Free People), an order's window, the
      days after an airing; and the lines that were already right come
      out byte for byte as they were (the live pull behind DoorDash x
      The Goat, on a one-person valuation; the multi-year valuation
      with no days after behind the Dodge pair, whole and year by year)
  [3] "Attribution" is the days the counts run past the campaign's last
      day, never the second window's length
  [4] the before window is the valuation's own, its length from the
      dates when the valuation states none
  [5] the response carries the view and the stored valuation is not
      changed; the view holds dates and day counts only; a malformed
      valuation never raises
  [6] the page prints the server's line and works out no window of its
      own (checked on the template's source, and run in node when node
      is on the machine); the year view takes the year's own line; the
      route attaches the view and writes nothing

Made-up names and figures. Hermetic: no S3, no network, no model, no
mail, no app import. Run:
    python3 scripts/test_bpiq_page_window_line.py
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("CORPUS_CATALOG", "0")
os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ.setdefault("PM_MAIL_OFF", "1")

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / "scripts"))
sys.modules.setdefault("app", None)          # the dashboard app never boots in a test

from migration import bpiq_deck_builder as B  # noqa: E402
from migration import bpiq_windows as W  # noqa: E402

import test_bpiq_deck_names_windows as T  # noqa: E402  (the valuation fixtures and their window shapes)

FAIL = 0
ARROW, DOT = "\u2192", "\u00b7"


def check(label, cond, detail=""):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({str(detail)[:500]})" if detail else ""))


def line(campaign, extra, pre, post) -> str:
    """The line as the page prints it, written out in full."""
    return (f"Campaign: {campaign[0]} {ARROW} {campaign[1]} {DOT} Attribution: {extra}d {DOT} "
            f"Pre-window: {pre[0]} {ARROW} {pre[1]} ({pre[2]}d) {DOT} "
            f"Post-window: {post[0]} {ARROW} {post[1]} ({post[2]}d)")


def page_before(d: dict) -> str:
    """What the page printed until 2026-11-18, from the valuation's own
    fields: the campaign's first day through the second window's end,
    the two lengths added; the second window's stated length as
    'Attribution'."""
    pre, ev, po = d.get("pre_period") or {}, d.get("event_period") or {}, d.get("post_period") or {}
    return line((d.get("start_date") or "", d.get("end_date") or ""), d.get("attribution_window_days") or 0,
                (pre.get("start") or "", pre.get("end") or "", pre.get("days") or 0),
                (ev.get("start") or "", po.get("end") or "", (ev.get("days") or 0) + (po.get("days") or 0)))


# what the line must read for each way a valuation is stored: (days after the campaign, the after window)
EXPECT = {
    "season":   (0,  ("2026-02-10", "2026-07-19", 160)),
    "tail":     (0,  ("2025-10-07", "2025-12-27", 82)),
    "last_day": (0,  ("2026-04-29", "2026-08-03", 97)),
    "order":    (15, ("2026-02-10", "2026-08-03", 175)),
    "after":    (7,  ("2026-02-15", "2026-02-21", 7)),
    "pull":     (30, ("2026-02-10", "2026-05-10", 90)),
    "slice":    (0,  ("2026-02-10", "2026-07-19", 160)),
}
ALREADY_RIGHT = ("pull", "slice")


def multi_year() -> dict:
    """A valuation of several years with no days after, as the Dodge pair
    is stored: the whole window is the campaign's, and each year is cut
    from it on the page."""
    d = T.valuation(T.PEOPLE[:1], shape="slice", qtype="movie franchise")
    d.update(start_date="2023-01-01", end_date="2025-06-24", attribution_window_days=0, post_period_days=906,
             pre_period={"start": "2022-07-01", "end": "2022-12-31", "days": 184}, pre_period_days=184,
             event_period={"start": "2023-01-01", "end": "2025-06-24", "days": 906},
             post_period={"start": "2025-06-24", "end": "2025-06-24", "days": 0})
    d["totals"]["post_hits_per_day"] = round(d["totals"]["post_hits"] / 906, 2)
    d["yearly_breakdown"] = [
        {"year": 2023, "label": "2023", "start": "2023-01-01", "end": "2023-12-31", "days": 365},
        {"year": 2024, "label": "2024", "start": "2024-01-01", "end": "2024-12-31", "days": 366},
        {"year": 2025, "label": "2025 first half", "start": "2025-01-01", "end": "2025-06-24", "days": 175},
    ]
    return d


def year_before(d: dict, y: dict) -> str:
    """The year view's line until 2026-11-18: the page's year cut, then
    the old line on it."""
    s = dict(d)
    s.update(start_date=y.get("start") or d.get("start_date"), end_date=y.get("end") or d.get("end_date"),
             event_period={"start": y.get("start"), "end": y.get("end"), "days": y.get("days")},
             post_period={"start": y.get("end"), "end": y.get("end"), "days": 0})
    return page_before(s)


# ------------------------------------------------------------------ [1]
def test_one_resolver():
    print("[1] one resolver: the page and the deck read the same window")
    check("the deck builder's window functions are this module's own",
          B._after_window is W.after_window and B._before_window is W.before_window and B._ymd is W.ymd)
    src = (WEB / "migration" / "bpiq_deck_builder.py").read_text(encoding="utf-8")
    check("the deck builder defines no window resolver of its own",
          "def _after_window" not in src and "def _span_days" not in src and "post_hits_per_day" not in src)
    for shape in T.WINDOWS:
        doc = T.valuation(T.PEOPLE[:1], shape=shape)
        with contextlib.redirect_stdout(io.StringIO()):
            ctx = B._derive_context(copy.deepcopy(doc), None, "FASHION")
        view = W.window_view(doc)
        a, b = view["after"], view["before"]
        check(f"{shape}: the page's after window is the deck's ({a['start']} to {a['end']}, {a['days']} days)",
              (a["start"], a["end"], a["days"]) == (ctx.post_start, ctx.post_end, ctx.post_days)
              and (a["start"], a["end"], a["days"]) == T.WINDOWS[shape]["after"], (a, ctx.post_start, ctx.post_end))
        check(f"{shape}: the page's before window is the deck's",
              b["days"] == ctx.pre_days and B._fmt_range(b["start"], b["end"]) == ctx.pre_range, (b, ctx.pre_range))


# ------------------------------------------------------------------ [2]
def test_the_line():
    print("[2] the line, for each way a valuation is stored")
    for shape, (extra, post) in EXPECT.items():
        w = T.WINDOWS[shape]
        doc = T.valuation(T.PEOPLE[:1], shape=shape)
        want = line((w["event"][0], w["event"][1]), extra, w["pre"], post)
        got = W.window_view(doc)["line"]
        check(f"{shape}: {want.split(DOT)[-1].strip()}", got == want, got)
        was = page_before(doc)
        if shape in ALREADY_RIGHT:
            check(f"{shape}: the line was already right and is byte for byte what it was", got == was, (was, got))
        else:
            check(f"{shape}: the line the page printed before was wrong and has changed", got != was, was)
    # the Coach x WNBA shape, said in full: a campaign, then the days up to when the valuation was made
    season = T.valuation(T.PEOPLE[:3], shape="season")
    check("a campaign with a later tail: the season's own dates, 160 days, no days after",
          W.window_view(season)["line"] == "Campaign: 2026-02-10 \u2192 2026-07-19 \u00b7 Attribution: 0d \u00b7 "
          "Pre-window: 2025-02-10 \u2192 2026-02-09 (365d) \u00b7 Post-window: 2026-02-10 \u2192 2026-07-19 (160d)"
          and page_before(season).endswith("Post-window: 2026-02-10 \u2192 2026-08-03 (175d)")
          and "Attribution: 15d" in page_before(season), W.window_view(season)["line"])
    check("the tail never prints: not its last day, not its length, not the two lengths added",
          not re.search(r"2026-08-03|\b15d\b|\b175d\b", W.window_view(season)["line"]))
    # who the valuation names does not move its dates
    check("the line is the same for one name, three names and a property",
          len({W.window_view(T.valuation(n, shape="season", qtype=q))["line"]
               for n, q in ((T.PEOPLE[:1], "talent"), (T.PEOPLE[:3], "talent"), ([T.PROPERTY], "league"))}) == 1)

    # a multi-year valuation with no days after: whole, and year by year
    d = multi_year()
    view = W.window_view(d)
    check("several years, no days after: the whole line is byte for byte what it was",
          view["line"] == page_before(d) and view["line"].endswith("Post-window: 2023-01-01 \u2192 2025-06-24 (906d)"),
          (view["line"], page_before(d)))
    years = view.get("by_year") or {}
    check("each year has its own line, keyed by the year", sorted(years) == ["2023", "2024", "2025"], sorted(years))
    same = [(y["year"], years[str(y["year"])]["line"], year_before(d, y)) for y in d["yearly_breakdown"]
            if years.get(str(y["year"]), {}).get("line") != year_before(d, y)]
    check("each year's line is byte for byte what the year view printed", not same, same[:1])
    check("a year's line carries the year's dates and length, never the whole valuation's",
          years["2024"]["line"] == "Campaign: 2024-01-01 \u2192 2024-12-31 \u00b7 Attribution: 0d \u00b7 "
          "Pre-window: 2022-07-01 \u2192 2022-12-31 (184d) \u00b7 Post-window: 2024-01-01 \u2192 2024-12-31 (366d)",
          years["2024"]["line"])
    # a multi-year valuation whose counts are a later window (the last year of a long partnership)
    late = multi_year()
    late.update(post_period={"start": "2024-06-25", "end": "2025-06-24", "days": 365}, post_period_days=365)
    late["totals"]["post_hits_per_day"] = round(late["totals"]["post_hits"] / 365, 2)
    check("a long partnership read on its last year: that year, not the campaign's first day plus both lengths",
          W.window_view(late)["line"].endswith("Post-window: 2024-06-25 \u2192 2025-06-24 (365d)")
          and page_before(late).endswith("Post-window: 2023-01-01 \u2192 2025-06-24 (1271d)"),
          W.window_view(late)["line"])


# ------------------------------------------------------------------ [3]
def test_days_after_the_campaign():
    print("[3] 'Attribution' is the days the counts run past the campaign's last day")
    for shape, (extra, post) in EXPECT.items():
        doc = T.valuation(T.PEOPLE[:1], shape=shape)
        end = T.WINDOWS[shape]["event"][1]
        by_hand = 0 if post[1] <= end else W.span_days(max(W.day_after(end), post[0]), post[1])
        check(f"{shape}: {extra} day(s) of the after window fall after {end}",
              W.window_view(doc)["days_after_campaign"] == extra == by_hand, (W.window_view(doc), by_hand))
    season = T.valuation(T.PEOPLE[:1], shape="season")
    check("the second window's length is not printed as days after the campaign when the counts stop with it",
          season["attribution_window_days"] == 15 and W.window_view(season)["days_after_campaign"] == 0)
    undated = T.valuation(T.PEOPLE[:1], shape="season")
    for k in ("post_period", "event_period", "start_date", "end_date"):
        undated.pop(k, None)
    check("with no dates to read, the valuation's stated length stands",
          W.days_after_campaign(undated) == undated["attribution_window_days"])


# ------------------------------------------------------------------ [4]
def test_before_window():
    print("[4] the before window is the valuation's own")
    for shape, w in T.WINDOWS.items():
        doc = T.valuation(T.PEOPLE[:1], shape=shape)
        b = W.before_window(doc)
        check(f"{shape}: {w['pre'][0]} to {w['pre'][1]}, {w['pre'][2]} days",
              (b["start"], b["end"], b["days"]) == w["pre"], b)
    doc = T.valuation(T.PEOPLE[:1], shape="season")
    doc["pre_period"].pop("days")
    doc.pop("pre_period_days")
    check("a before window with no stated length takes it from its dates", W.before_window(doc)["days"] == 365,
          W.before_window(doc))
    doc["pre_period"]["days"] = 0
    doc["pre_period_days"] = 364
    check("the valuation's own stated length is used when it has one", W.before_window(doc)["days"] == 364)


# ------------------------------------------------------------------ [5]
def test_response_view():
    print("[5] the response carries the view; the stored valuation is not changed")
    doc = multi_year()
    frozen = json.dumps(doc, sort_keys=True)
    out = W.with_window_view(doc)
    check("the valuation handed in is byte for byte what it was", json.dumps(doc, sort_keys=True) == frozen
          and W.VIEW_KEY not in doc)
    check("the response is a copy that carries the view under _bpiq_window",
          out is not doc and out[W.VIEW_KEY]["line"] == W.window_view(doc)["line"]
          and {k: v for k, v in out.items() if k != W.VIEW_KEY} == doc)
    check("every figure of the valuation is the same object in the response (nothing recomputed)",
          out["valuation"] is doc["valuation"] and out["totals"] is doc["totals"]
          and out["control_group"] is doc["control_group"])

    def leaves(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from leaves(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                yield from leaves(v)
        else:
            yield o
    view = out[W.VIEW_KEY]
    bad = [v for v in leaves(view) if not (isinstance(v, int) or (isinstance(v, str) and (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}|", v) or v.startswith("Campaign: "))))]
    check("the view holds dates, day counts and the line, nothing else", not bad, bad[:3])
    figures = {str(doc["projected_audience_size"]), f"{doc['valuation']['total_brand_value']:.2f}",
               str(doc["valuation"]["incremental_users"]), str(doc["totals"]["post_users_projected"])}
    check("no figure of the valuation is in the view", not any(f in json.dumps(view) for f in figures))
    check("the view is JSON a response can carry", json.loads(json.dumps(view)) == view)
    for junk in (None, [], "x", 7, {}, {"post_period": "x", "event_period": 7, "totals": [], "yearly_breakdown": "y"},
                 {"start_date": "soon", "yearly_breakdown": [None, {"year": None}, {"year": 2024, "start": "x"}]}):
        try:
            got = W.with_window_view(junk)
            ok = (got is junk) if not isinstance(junk, dict) else isinstance(got.get(W.VIEW_KEY), dict)
        except Exception as e:  # pragma: no cover
            ok = repr(e)
        check(f"a malformed valuation never raises and never loses its response ({str(junk)[:40]})", ok is True, ok)
    words = re.compile(r"synth|model|estimat|panel|pipeline|hostmap|claude|\u2014|\u2013", re.I)
    text = " ".join(W.window_view(T.valuation(T.PEOPLE[:1], shape=s))["line"] for s in T.WINDOWS)
    check("the line carries no dash and no word for how a figure was made", not words.search(text), words.findall(text))


# ------------------------------------------------------------------ [6]
def _function_body(src: str, name: str) -> str:
    i = src.index(f"def {name}(")
    j = src.find("\n@app.route", i)
    return src[i:j if j > 0 else i + 6000]


def test_page_and_route():
    print("[6] the page prints the server's line and works out no window of its own")
    html = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    i = html.index("function renderBpiqDashboard(data)")
    j = html.index("document.getElementById('bpiqDashMeta').innerHTML = meta;", i)
    render = html[i:j]
    code = "\n".join(ln for ln in render.splitlines() if not ln.strip().startswith("//"))   # comments aside
    check("the line under the title is the server's (_bpiq_window.line), escaped",
          "data._bpiq_window" in code and "_bpiqWin.line" in code and ".replace(/</g, '&lt;')" in code)
    used = [w for w in ("evP", "poP", "preP", "post_period", "event_period", "pre_period", "attribution_window_days",
                        "Post-window", "Pre-window", "Attribution:", ".days") if w in code]
    check("the page adds no lengths and reads no window field for that line", not used, used)
    check("the old expression is gone from the whole template",
          "(evP.days || 0) + (poP.days || 0)" not in html and "' \u00b7 Post-window: '" not in html
          and "' \u00b7 Attribution: '" not in html and "' \u00b7 Pre-window: '" not in html)
    k = html.index("function _bpiqApplyYearCut(orig, yearNum)")
    cut = html[k:k + 3500]
    check("the year view takes the year's own line from the server's by_year",
          "orig._bpiq_window.by_year" in cut and "sliced._bpiq_window = _bpiqYearWin[String(y.year)] || null;" in cut)

    app_src = (WEB / "app.py").read_text(encoding="utf-8")
    body = _function_body(app_src, "get_brand_partnership_iq_result")
    check("the results route attaches the view from the one module, before it answers",
          "from migration.bpiq_windows import with_window_view" in body
          and body.index("with_window_view(data)") < body.index("return jsonify({\n            'success': True,"))
    check("the results route writes nothing", not re.search(r"put_object|upload_file|copy_object|delete_object", body))

    node = shutil.which("node")
    if not node:
        print("  skip  node is not on this machine; the page's snippet was checked on its source only")
        return
    a = render.index("var _bpiqWin = (data && data._bpiq_window) || null;")
    b = render.index("}", render.index("} else {", a) + len("} else {")) + 1
    snippet = render[a:b]
    y0 = cut.index("var sliced = Object.assign({}, orig);")
    y1 = cut.index("sliced._bpiq_window = _bpiqYearWin[String(y.year)] || null;") + len(
        "sliced._bpiq_window = _bpiqYearWin[String(y.year)] || null;")
    # only the lines of the year cut that set dates and the window (the rest reads page state)
    year_lines = "\n".join(ln for ln in cut[y0:y1].splitlines()
                           if re.search(r"var sliced|sliced\.(start_date|end_date|event_period|post_period|_bpiq_window)|"
                                        r"var _bpiqYearWin|_bpiq_active_year", ln))
    cases = {
        "season": W.with_window_view(T.valuation(T.PEOPLE[:3], shape="season")),
        "pull": W.with_window_view(T.valuation(T.PEOPLE[:1], shape="pull")),
        "years": W.with_window_view(multi_year()),
        "no_view": T.valuation(T.PEOPLE[:1], shape="season"),
        "markup": dict(T.valuation(T.PEOPLE[:1]), _bpiq_window={"line": "Campaign: <b>x</b> & y"}),
    }
    js = ("var cases = " + json.dumps(cases) + ";\nvar out = {};\n"
          "function lineOf(data) { var meta = '';\n" + snippet + "\nreturn meta; }\n"
          "Object.keys(cases).forEach(function (k) { out[k] = lineOf(cases[k]); });\n"
          "var orig = cases.years; var y = orig.yearly_breakdown[1];\n" + year_lines + "\n"
          "out.year_2024 = lineOf(sliced);\n"
          "y = {year: 1999, start: '1999-01-01', end: '1999-12-31', days: 365};\n" + year_lines + "\n"
          "out.year_unknown = lineOf(sliced);\n"
          "console.log(JSON.stringify(out));\n")
    try:
        run = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60)
        got = json.loads(run.stdout)
    except Exception as e:
        check("the page's snippet runs in node", False, f"{e!r} {getattr(run, 'stderr', '')[:300]}" if 'run' in dir() else repr(e))
        return
    check("in node: the page prints the server's line for a campaign with a later tail",
          got["season"] == cases["season"][W.VIEW_KEY]["line"] and got["season"].endswith("(160d)"), got["season"])
    check("in node: a line that was already right prints as it was",
          got["pull"] == page_before(T.valuation(T.PEOPLE[:1], shape="pull")), got["pull"])
    check("in node: the year view prints the year's own line",
          got["year_2024"] == cases["years"][W.VIEW_KEY]["by_year"]["2024"]["line"], got["year_2024"])
    check("in node: a year the server did not send prints only that year's campaign dates",
          got["year_unknown"] == "Campaign: 1999-01-01 \u2192 1999-12-31", got["year_unknown"])
    check("in node: a response with no view prints only the campaign's own dates, never a window worked out here",
          got["no_view"] == "Campaign: 2026-02-10 \u2192 2026-07-19", got["no_view"])
    check("in node: the line is escaped before it is set as markup",
          got["markup"] == "Campaign: &lt;b&gt;x&lt;/b&gt; &amp; y", got["markup"])


def main() -> int:
    test_one_resolver()
    test_the_line()
    test_days_after_the_campaign()
    test_before_window()
    test_response_view()
    test_page_and_route()
    print()
    if FAIL:
        print(f"FAIL: {FAIL} check(s)")
        return 1
    print("PASS: the line under a valuation's title prints the window the counts cover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
