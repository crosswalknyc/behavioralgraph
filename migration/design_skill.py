"""The Crosswalk design pack, resolved for code (2026-10-21).

Jenna, 2026-10-21 (verbatim): "make sure this updated design skill is
installed and always used in both my local workspace and prometheus".

The locked brand system lives in the repo as the ``crosswalk-design``
skill (``.cursor/skills/crosswalk-design/``: SKILL.md, references, the
nine Crosswalk Inter faces, the real logo, the 27-layout deck template,
the SOW / POC / proposal / newsletter / signature templates, the tokens
and the finishing scripts). Every builder that renders a client-facing
file (a deck, a PDF, a Word document, an HTML page, a chart) takes its
fonts, tokens, templates and finishing checks from here, never from a
hand-typed path or a hand-typed hex.

Where the pack is found, in order:

  1. ``CROSSWALK_DESIGN_SKILL`` (an explicit directory)
  2. the repo skill next to this checkout
     (``<repo>/.cursor/skills/crosswalk-design``): the Mac, the build box
  3. ``/root/finished_codes/.cursor/skills/crosswalk-design`` (the box)
  4. the packaged copy inside bg-webapp (``bg-webapp/design/crosswalk-design``):
     fonts, logo, tokens, metrics, the deck / SOW / POC templates and the
     finishing scripts, byte-identical to the skill (``scripts/
     sync_design_pack.py`` copies, ``test_design_skill.py`` holds them
     equal). Render deploys bg-webapp alone, so this is what prod uses.

Fonts resolve to Crosswalk Inter; when the pack is missing entirely the
legacy Inter 18pt files under ``static/fonts`` keep a build alive (the
report says so, and the file is never held).

Finishing (``finish``): a .pptx is finalized, its fonts embedded and
checked (``cw_embed_fonts.py``, which runs ``cw_pptx_finalize.py``,
``cw_pptx_embed.py`` and ``cw_pptx_check.py``), a .docx goes through
``cw_docx_finalize.py`` and the embed, a .pdf is verified for embedded
fonts, an .html gets its fonts inlined; then ``cw_brand_lint.py`` and
``readability_check.py`` run. Door metaphors are rewritten in place
(``cw_door_copy.scrub_door_metaphor``) before the lint. A finish NEVER
raises and never holds a file: the fixes are applied in place and the
report carries what ran.
``gate_attachment`` is the same for in-memory bytes (the outbound mail
door).

Pre-ship fixes on a deck (2026-11-10, the Las Culturistas deck: 14 lint
FAILs and 2 readability flags reached a Lionsgate seat). Before the embed
and the checks, ``finish`` now corrects in place what the pack's own
checks would fail:

  * widows: every paragraph is measured against its box with the pack's
    wrap model and font metrics (``assets/font-metrics.json``, the same
    model ``cw_brand_lint.py`` checks with); when the last line would
    carry one word, the last two words are bound with a non-breaking
    space (U+00A0) so they wrap together, and the box is widened (when
    there is room) or narrowed a little so the pack's check sees at least
    two words on that line too. Never a line break, never shortened copy.
  * backgrounds: a full-page shape or picture acting as the page (a
    photo under a scrim, a solid rectangle) becomes the slide background
    itself, the scrim baked into the image at 3840 px across (288 ppi on
    a 16:9 page), so no shape sits under the content and no image is
    low resolution.

The deck builders call the same helpers while they lay type
(``widow_fit``, ``set_slide_background_image``, ``gradient_image_bytes``)
so a fresh deck is already clean; the pass in ``finish`` is the net for
every other builder. The report carries what changed (``Report.fixes``)
and the lint / readability counts (``Report.counts``) for the job record.

Copy repaired in place, and a status that says what happened (2026-11-11:
two decks left the builder READY with ``readability: FAIL`` and were fixed
by hand). ``finish(copy_repair=True)`` ends with the plain-English check
passing, and every ``finish`` reports the state the file is in:

  * a builder that wrote the deck's copy asks for the repair (the
    Prometheus insights deck). Every other deck (a focus group export
    whose pages must match its PDF, verbatim answers, a Brand
    Partnership IQ deck built from a client's own record) and every
    attachment at the mail door is checked and reported, never
    rewritten.
  * the flagged text is rewritten in place before the layout fixes
    (``copy_fix``: the pack's ``cw_copy_fix.py``). When a rewriter is
    registered (``set_copy_rewriter``, or ``finish(copy_rewriter=...)``)
    the flagged units go to it for one or two passes, and a rewrite is
    taken only when it keeps every figure; whatever is still flagged gets
    the fixed repair for its kind (a long sentence split at its join, a
    phrase used in three places reworded or dropped from the later ones,
    stock filler replaced). Every rule repair is checked before it is
    written; one that would not read is not made. The file is never held.
  * ``ready`` is true only when the fonts are embedded, the lint has no
    FAIL and the plain-English check has no flag; the summary line prints
    the state of the file as it stands at the end ("readability: FAIL (2
    flag(s) left)" is NOT READY, never READY).
  * title-cased joining words are lowered where a caser raised them
    (``25 To 34`` -> ``25 to 34``): ``fix_label_casing`` for builders, and
    with the copy repair a pass over every paragraph in ``preship_fix``.
  * a photo behind type is measured: the luminance behind every text box
    is read from the background image and the graphite scrim is deepened
    until the type clears 4.5:1; past the deepest scrim, or when the photo
    is light all the way across the type, the page takes the pack's
    gradient and the photo is dropped (``bake_photo_for_text`` for
    builders, ``fix_photo_contrast_in_pptx`` in ``preship_fix``).
  * ``photo_url_reject_reason`` / ``photo_blob_reject_reason`` say why a
    candidate is not a cover photograph (a logo file, an icon or clip-art
    site, a screenshot of an interface, a flat graphic).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PACK_NAME = "crosswalk-design"
PACK_VERSION = "2026-10-21"
FONT_FAMILY = "Crosswalk Inter"
FONT_FILE_STEM = "CrosswalkInter"
WEIGHTS = ("Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold",
           "Bold", "ExtraBold", "Black")
LEGACY_FAMILY = "Inter 18pt"
LEGACY_FILE_STEM = "Inter_18pt"
LEGACY_WEIGHTS = ("Light", "Regular", "Medium", "Bold", "ExtraBold", "Black")

# This module is a twin (scripts/module_twins.json): the source lives in
# the parent repo's migration/ and the generated mirror in
# bg-webapp/migration/. The build box imports the parent copy (its worker
# runs from /root/finished_codes), Render imports the mirror (it deploys
# bg-webapp alone), so the paths below are worked out from wherever the
# file sits.
_HERE = Path(__file__).resolve().parent            # <base>/migration
_BASE = _HERE.parent
if (_BASE / "app.py").is_file() or (_BASE / "design").is_dir():
    _WEBAPP = _BASE                                 # bg-webapp
    _REPO = _BASE.parent                            # the parent checkout (when present)
else:
    _REPO = _BASE                                   # the parent repo
    _WEBAPP = _BASE / "bg-webapp"
PACKAGED = _WEBAPP / "design" / PACK_NAME
STATIC_FONTS = _WEBAPP / "static" / "fonts"

# Scripts the packaged copy carries (the finishing chain and the checks).
PACKAGED_SCRIPTS = (
    "cw_embed_fonts.py", "cw_pptx_finalize.py", "cw_pptx_embed.py",
    "cw_pptx_check.py", "cw_docx_finalize.py", "cw_brand_lint.py",
    "readability_check.py", "cw_pptx_tools.py", "cw_email_check.py",
    "render_preview.py", "cw_door_copy.py", "cw_copy_fix.py",
)
PACKAGED_TEMPLATES = (
    "Crosswalk_Deck_Templates.pptx", "Crosswalk_SOW_Template.pptx",
    "Crosswalk_30Day_POC_SOW_Template.pptx",
)
PACKAGED_ASSET_FILES = ("tokens.json", "font-metrics.json", "crosswalk-tokens.css")


def _is_pack(p: Path) -> bool:
    try:
        return (p / "assets" / "brand-files" / "fonts" / f"{FONT_FILE_STEM}-Regular.ttf").is_file()
    except Exception:
        return False


def candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("CROSSWALK_DESIGN_SKILL", "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(_REPO / ".cursor" / "skills" / PACK_NAME)
    out.append(Path("/root/finished_codes/.cursor/skills") / PACK_NAME)
    out.append(PACKAGED)
    return out


def skill_root() -> Optional[Path]:
    """The first directory that carries the pack (fonts present), or None."""
    for c in candidates():
        if _is_pack(c):
            return c
    return None


def full_skill_root() -> Optional[Path]:
    """The full skill (SKILL.md present), when this machine has it."""
    for c in candidates():
        if _is_pack(c) and (c / "SKILL.md").is_file():
            return c
    return None


def available() -> bool:
    return skill_root() is not None


# ---------------------------------------------------------------- fonts
def font_dir() -> Optional[Path]:
    r = skill_root()
    if r is not None:
        return r / "assets" / "brand-files" / "fonts"
    # the TTFs ship with the app as well (the dashboard serves them)
    if (STATIC_FONTS / f"{FONT_FILE_STEM}-Regular.ttf").is_file():
        return STATIC_FONTS
    return None


def font_path(weight: str = "Regular") -> Optional[Path]:
    """The Crosswalk Inter file for a weight ('Regular', 'Bold', 'Light',
    'Medium', 'SemiBold', ...). Falls back to the nearest legacy Inter 18pt
    file only when the pack is absent on this machine."""
    w = _weight_name(weight)
    d = font_dir()
    if d is not None:
        p = d / f"{FONT_FILE_STEM}-{w}.ttf"
        if p.is_file():
            return p
    return legacy_font_path(w)


def legacy_font_path(weight: str = "Regular") -> Optional[Path]:
    w = _weight_name(weight)
    if w not in LEGACY_WEIGHTS:
        w = {"Thin": "Light", "ExtraLight": "Light", "SemiBold": "Medium"}.get(w, "Regular")
    for d in (STATIC_FONTS, _REPO / ".cursor" / "skills" / "crosswalk-brand-standards" / "assets" / "fonts"):
        p = d / f"{LEGACY_FILE_STEM}-{w}.ttf"
        if p.is_file():
            return p
    return None


def fonts() -> dict:
    """{weight: path} for every Crosswalk Inter face present."""
    d = font_dir()
    if d is None:
        return {}
    return {w: d / f"{FONT_FILE_STEM}-{w}.ttf" for w in WEIGHTS if (d / f"{FONT_FILE_STEM}-{w}.ttf").is_file()}


def using_house_font() -> bool:
    p = font_path("Regular")
    return bool(p) and p.name.startswith(FONT_FILE_STEM)


def family_name(weight: str = "Regular") -> str:
    """The family name an Office file or CSS should carry for a weight:
    'Crosswalk Inter' for Regular and Bold (bold is a style of the family),
    'Crosswalk Inter Light' and so on for the other faces."""
    w = _weight_name(weight)
    fam = FONT_FAMILY if using_house_font() else LEGACY_FAMILY
    if w in ("Regular", "Bold"):
        return fam
    return f"{fam} {w}"


def _weight_name(weight) -> str:
    s = str(weight or "Regular").strip().replace("-", "").replace("_", "").replace(" ", "").lower()
    table = {w.lower(): w for w in WEIGHTS}
    table.update({"book": "Regular", "normal": "Regular", "demibold": "SemiBold", "heavy": "Black",
                  "ultralight": "ExtraLight", "ultrabold": "ExtraBold"})
    return table.get(s, "Regular")


def register_reportlab(prefix: str = "CrosswalkInter") -> dict:
    """Register the family with reportlab under ``prefix`` (``prefix``,
    ``prefix-Bold``, ``prefix-Medium``, ``prefix-Light``, ``prefix-SemiBold``).
    Returns {role: registered name}; {} when reportlab or the fonts are
    missing. Safe to call repeatedly."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        return {}
    out = {}
    for role, suffix in (("Regular", ""), ("Bold", "-Bold"), ("Medium", "-Medium"),
                         ("Light", "-Light"), ("SemiBold", "-SemiBold")):
        p = font_path(role)
        if not p:
            continue
        name = f"{prefix}{suffix}"
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(p)))
            out[role] = name
        except Exception:
            continue
    try:
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        if "Regular" in out:
            registerFontFamily(prefix, normal=out["Regular"], bold=out.get("Bold", out["Regular"]),
                               italic=out["Regular"], boldItalic=out.get("Bold", out["Regular"]))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- tokens
_TOKENS: Optional[dict] = None


def tokens() -> dict:
    """``assets/tokens.json`` from the pack ({} when absent). Cached."""
    global _TOKENS
    if _TOKENS is not None:
        return _TOKENS
    for base in (skill_root(), PACKAGED):
        if base is None:
            continue
        p = base / "assets" / "tokens.json"
        if p.is_file():
            try:
                _TOKENS = json.loads(p.read_text(encoding="utf-8"))
                return _TOKENS
            except Exception:
                pass
    _TOKENS = {}
    return _TOKENS


def token(path: str, default=None):
    """Dotted lookup into the tokens ('color.signal_green')."""
    cur = tokens()
    for part in str(path or "").split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def color(name: str, default: str = "") -> str:
    """A palette hex by its token name, searched through the color groups
    ('signal_green', 'graphite_teal', 'off_white', ...). Falls back to the
    locked v6 values so a missing pack never changes a brand color."""
    locked = {
        "graphite_teal": "#0C1618", "slate_teal": "#15252A", "off_white": "#E9E8E1",
        "signal_green": "#C7F23E", "signal_olive": "#5E7E12", "orchid": "#E682FF",
        "amethyst": "#8E3FA8", "cobalt": "#3358FF", "dusk": "#B7B3D8", "pavement": "#3B3D38",
        "muted_dark": "#7C878A", "muted_light": "#888C89", "body_dark": "#9AA09B", "body_light": "#5C6560",
    }
    key = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    t = tokens()

    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kk = str(k).lower().replace("-", "_").replace(" ", "_")
                if kk == key and isinstance(v, str) and v.startswith("#"):
                    return v
                if kk == key and isinstance(v, dict):
                    for vk in ("hex", "value", "color"):
                        if isinstance(v.get(vk), str) and v[vk].startswith("#"):
                            return v[vk]
                r = _walk(v)
                if r:
                    return r
        return None
    return _walk(t) or locked.get(key, default)


# ------------------------------------------------------------- templates
def template_path(name: str) -> Optional[Path]:
    """A template by file name, case-insensitive (the old TEMPLATE casing
    still resolves), searched in the full skill then the packaged copy."""
    want = str(name or "").strip().lower()
    for base in (full_skill_root(), skill_root(), PACKAGED):
        if base is None:
            continue
        d = base / "assets" / "templates"
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.name.lower() == want:
                return p
    return None


def deck_template() -> Optional[Path]:
    return template_path("Crosswalk_Deck_Templates.pptx")


def logo_path(variant: str = "white") -> Optional[Path]:
    """The real logo files: 'white' / 'black' lockups, 'slash-white',
    'slash-black', 'social'."""
    names = {
        "white": "crosswalk-logo-white.png", "black": "crosswalk-logo-black.png",
        "slash-white": "crosswalk-slash-mark-white.png", "slash-black": "crosswalk-slash-mark-black.png",
        "social": "crosswalk-social-square.png",
    }
    fn = names.get(str(variant or "white").lower(), names["white"])
    for base in (skill_root(), PACKAGED):
        if base is None:
            continue
        p = base / "assets" / "brand-files" / "logo" / fn
        if p.is_file():
            return p
    for p in (_WEBAPP / "static" / fn, _WEBAPP / "static" / "img" / fn):
        if p.is_file():
            return p
    return None


def scripts_dir() -> Optional[Path]:
    for base in (skill_root(), PACKAGED):
        if base is None:
            continue
        d = base / "scripts"
        if (d / "cw_embed_fonts.py").is_file():
            return d
    return None


# ------------------------------------------------------------- finishing
@dataclass
class Report:
    path: str
    ready: bool = False
    steps: list = field(default_factory=list)      # (name, ok, output)
    skipped: list = field(default_factory=list)    # (name, why)
    fixes: dict = field(default_factory=dict)      # what the pre-ship pass changed in place
    counts: dict = field(default_factory=dict)     # lint_fail, lint_warn, readability_flags

    def add(self, name: str, ok: bool, output: str = "") -> None:
        self.steps.append((name, bool(ok), str(output or "")[-4000:]))

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, str(why)))

    @property
    def copy_ok(self) -> bool:
        """The plain-English check ran and its last run found nothing to flag."""
        runs = [ok for n, ok, _o in self.steps if n == "readability"]
        return bool(runs) and bool(runs[-1])

    @property
    def summary(self) -> str:
        """One line that says what state the file is in now. A step that
        ran more than once (the plain-English check runs before and after
        the copy repair) prints its last result."""
        last = {}
        order = []
        for n, ok, _o in self.steps:
            if n not in last:
                order.append(n)
            last[n] = ok
        bits = []
        for n in order:
            bit = f"{n}: {'ok' if last[n] else 'FAIL'}"
            if n == "readability":
                left = self.counts.get("readability_flags")
                fixed = self.fixes.get("copy_units_repaired")
                if not last[n] and left:
                    bit += f" ({left} flag(s) left)"
                elif last[n] and fixed:
                    bit += f" ({self.fixes.get('copy_flags_before', '?')} flag(s) repaired in place)"
            bits.append(bit)
        bits += [f"{n}: skipped ({w})" for n, w in self.skipped]
        return f"{Path(self.path).name}: " + ("READY" if self.ready else "NOT READY") + (" (" + "; ".join(bits) + ")" if bits else "")

    def as_dict(self) -> dict:
        return {"path": self.path, "ready": self.ready, "copy_ok": self.copy_ok,
                "fixes": dict(self.fixes), "counts": dict(self.counts),
                "steps": [{"name": n, "ok": ok, "output": o} for n, ok, o in self.steps],
                "skipped": [{"name": n, "why": w} for n, w in self.skipped]}

    def job_record(self) -> dict:
        """The short form that rides on a deck job record: ready, the
        check counts, and what the pre-ship pass changed. No tool output."""
        return {"ready": self.ready, "copy_ok": self.copy_ok, "counts": dict(self.counts),
                "fixes": dict(self.fixes),
                "steps": {n: ("ok" if ok else "FAIL") for n, ok, _o in self.steps},
                "skipped": {n: w for n, w in self.skipped}}


def _run(script: str, args: list, timeout: int = 600):
    d = scripts_dir()
    if d is None:
        return None
    try:
        r = subprocess.run([sys.executable, str(d / script), *[str(a) for a in args]],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _have_fonttools() -> bool:
    try:
        import fontTools  # noqa: F401
        return True
    except Exception:
        return False


def soffice_available() -> bool:
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))


def verify_pdf_fonts(path) -> tuple:
    """(ok, text): every font the PDF uses is embedded, and which faces
    they are. Pure Python (pypdf) so it runs on Render and the box; the
    pack's own check shells out to poppler's pdffonts, which those hosts
    do not carry."""
    try:
        from pypdf import PdfReader
    except Exception:
        return True, "pypdf not installed; pdf fonts not checked"
    seen, missing, lines = set(), [], []
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            res = page.get("/Resources") or {}
            fonts = res.get("/Font") or {}
            try:
                fonts = fonts.get_object()
            except Exception:
                pass
            for key in list(fonts.keys()):
                try:
                    f = fonts[key].get_object()
                except Exception:
                    continue
                name = str(f.get("/BaseFont") or key)
                if name in seen:
                    continue
                seen.add(name)
                desc = f.get("/FontDescriptor")
                if desc is None and f.get("/DescendantFonts"):
                    try:
                        desc = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
                    except Exception:
                        desc = None
                try:
                    desc = desc.get_object() if desc is not None else None
                except Exception:
                    pass
                embedded = bool(desc) and any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
                base14 = any(b in name for b in ("Helvetica", "Times", "Courier", "Symbol", "ZapfDingbats"))
                if not embedded:
                    missing.append(name)
                house = any(h in name for h in ("Crosswalk", "Inter", "Geist"))
                lines.append(f"{name}: {'embedded' if embedded else ('standard' if base14 else 'NOT embedded')}"
                             + ("" if house else "  (not a house face)"))
    except Exception as e:
        return False, f"pdf read failed: {type(e).__name__}: {e}"
    ok = not missing
    lines.append("pdf fonts: " + ("all embedded" if ok else "NOT ready to deliver"))
    return ok, "\n".join(lines)


def embed_fonts(path, render_check: bool = False, report: Optional[Report] = None) -> Report:
    """``cw_embed_fonts.py FILE`` (in place): finalize, embed, check.
    A .pdf is verified only (in-process). Never raises."""
    rep = report or Report(str(path))
    ext = Path(str(path)).suffix.lower()
    if ext == ".pdf":
        ok, text = verify_pdf_fonts(path)
        rep.add("embed", ok, text)
        return rep
    if scripts_dir() is None or font_dir() is None or not using_house_font():
        rep.skip("embed", "design pack not on this machine")
        return rep
    if ext in (".pptx", ".docx") and not _have_fonttools():
        rep.skip("embed", "fonttools not installed")
        return rep
    args = [str(path), "--fonts", str(font_dir())]
    if ext == ".pptx" and render_check and soffice_available():
        args.append("--render-check")
    got = _run("cw_embed_fonts.py", args, timeout=900)
    if got is None:
        rep.skip("embed", "scripts missing")
        return rep
    rc, out = got
    rep.add("embed", rc == 0 and "READY" in out, out)
    return rep


_DOOR_MOD = None


def door_copy_module():
    """The pack's door-metaphor helper. None when the pack is absent."""
    global _DOOR_MOD
    if _DOOR_MOD is not None:
        return _DOOR_MOD or None
    sd = scripts_dir()
    if sd is None or not (sd / "cw_door_copy.py").is_file():
        _DOOR_MOD = False
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cw_door_copy", str(sd / "cw_door_copy.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _DOOR_MOD = mod
    except Exception:
        _DOOR_MOD = False
        return None
    return _DOOR_MOD


def _fallback_scrub_door(text) -> str:
    s = "" if text is None else str(text)
    if not s:
        return s
    held = []

    def hold(m):
        if re.match(r"\s+(to|for)\b", s[m.end():], re.I):
            return m.group(0)
        held.append(m.group(0))
        return f"\x00DOOR{len(held) - 1}\x00"

    out = re.sub(r"\bThe Door(?:\s+NYC)?\b", hold, s)
    frames = (
        (r"\bopens?\s+the\s+doors?\s+to\s+", "brings "),
        (r"\bopen(?:ed|ing)\s+the\s+doors?\s+to\s+", "brings "),
        (r"\bopens?\s+the\s+doors?\b", "brings"),
        (r"\ba\s+doors?\s+for\s+", ""),
        (r"\b(?:the\s+)?doors?\s+to\s+", ""),
    )
    for pat, repl in frames:
        out = re.sub(pat, repl, out, flags=re.I)
    out = re.sub(r"\bdoors?\b", "", out, flags=re.I)
    for i, name in enumerate(held):
        out = out.replace(f"\x00DOOR{i}\x00", name)
    return re.sub(r"\s{2,}", " ", out).strip()


def scrub_door_metaphor(text) -> str:
    """Rewrite door metaphors in one string. Never raises."""
    try:
        mod = door_copy_module()
        if mod is not None:
            return mod.scrub_door_metaphor(text)
    except Exception:
        pass
    try:
        return _fallback_scrub_door(text)
    except Exception:
        return "" if text is None else str(text)


def scrub_doors_in_file(path, report: Optional[Report] = None) -> Report:
    """Rewrite door metaphors in a deck or document in place. Never holds."""
    rep = report or Report(str(path))
    try:
        mod = door_copy_module()
        n = 0
        if mod is not None:
            n = int(mod.scrub_file(path) or 0)
        rep.add("door_scrub", True, f"rewrote {n} unit(s)" if n else "clean")
    except Exception:
        rep.add("door_scrub", True, "skipped")
    return rep


def brand_lint(path, report: Optional[Report] = None) -> Report:
    rep = report or Report(str(path))
    got = _run("cw_brand_lint.py", [str(path)])
    if got is None:
        rep.skip("brand_lint", "scripts missing")
        return rep
    rc, out = got
    rep.add("brand_lint", rc == 0, out)
    m = re.search(r"(\d+) failure\(s\), (\d+) warning\(s\)", out or "")
    if m:
        rep.counts["lint_fail"] = int(m.group(1))
        rep.counts["lint_warn"] = int(m.group(2))
    return rep


def readability(path, report: Optional[Report] = None) -> Report:
    rep = report or Report(str(path))
    got = _run("readability_check.py", [str(path)])
    if got is None:
        rep.skip("readability", "scripts missing")
        return rep
    rc, out = got
    rep.add("readability", rc == 0, out)
    m = re.search(r"(\d+) flag\(s\)", out or "")
    if m:
        rep.counts["readability_flags"] = int(m.group(1))
    return rep


_COPY_MOD = None
_COPY_REWRITER = None


def copy_fix_module():
    """The pack's copy repair (``cw_copy_fix.py``), imported once. None when
    the pack is absent."""
    global _COPY_MOD
    if _COPY_MOD is not None:
        return _COPY_MOD or None
    sd = scripts_dir()
    if sd is None or not (sd / "cw_copy_fix.py").is_file():
        _COPY_MOD = False
        return None
    try:
        import importlib.util
        if str(sd) not in sys.path:
            sys.path.insert(0, str(sd))      # it imports readability_check and cw_door_copy beside it
        spec = importlib.util.spec_from_file_location("cw_copy_fix", str(sd / "cw_copy_fix.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _COPY_MOD = mod
    except Exception:
        _COPY_MOD = False
        return None
    return _COPY_MOD


def set_copy_rewriter(fn) -> None:
    """Register the model pass ``finish`` hands flagged copy to: a callable
    that takes ``[{"id", "text", "role", "problems", "keep"}]`` and returns
    ``{id: new text}``. The web app registers its own; a process with none
    (a test, a local script) uses the fixed repairs alone."""
    global _COPY_REWRITER
    _COPY_REWRITER = fn


def copy_rewriter():
    return _COPY_REWRITER


def fix_label_casing(text) -> str:
    """Lower the joining words a title-caser raised (``25 To 34`` ->
    ``25 to 34``, ``Arts And Culture`` -> ``Arts and Culture``). The pack's
    ``small_word_case``; the text comes back as it was without the pack."""
    try:
        mod = copy_fix_module()
        if mod is not None:
            return mod.small_word_case(text)
    except Exception:
        pass
    return "" if text is None else str(text)


def scrub_stock_phrases(text) -> str:
    """Replace stock filler ("does the work") with the plain verb. The
    pack's ``scrub_stock``; unchanged without the pack."""
    try:
        mod = copy_fix_module()
        if mod is not None:
            return mod.scrub_stock(text)
    except Exception:
        pass
    return "" if text is None else str(text)


def _copy_fits(shape, old: str, new: str) -> bool:
    """May a repaired line go into this shape? Yes when it wraps to no more
    lines than the line it replaces (measured with the pack's wrap model
    on the shape's own width, face and size), or when the box has the
    height for one more; failing that the type steps down, by no more
    than an eighth and never under 10 pt; failing that the repair is not
    written (a line is never pushed out of its box). A shape that cannot
    be measured takes the repair only when it is not longer."""
    try:
        tf = shape.text_frame
        sizes = [r.font.size.pt for p_ in tf.paragraphs for r in p_.runs if r.font.size]
        if lint_module() is None or not sizes:
            return len(new) <= len(old) + 2
        size = max(sizes)
        runs = [r for p_ in tf.paragraphs for r in p_.runs]
        face = runs[0].font.name if runs and runs[0].font.name else None
        bold = bool(runs and runs[0].font.bold)
        ml = tf.margin_left if tf.margin_left is not None else 91440
        mr = tf.margin_right if tf.margin_right is not None else 91440
        mt = tf.margin_top if tf.margin_top is not None else 45720
        mb = tf.margin_bottom if tf.margin_bottom is not None else 45720
        width_in = (int(shape.width) - int(ml) - int(mr)) / EMU_PER_IN
        height_in = (int(shape.height) - int(mt) - int(mb)) / EMU_PER_IN
        if width_in <= 0:
            return len(new) <= len(old) + 2
        _l, n_old = widow_lines(old, size, width_in, face=face, bold=bold)
        _l, n_new = widow_lines(new, size, width_in, face=face, bold=bold)
        if n_new <= n_old:
            return True
        if n_new * size * 1.2 / 72.0 <= height_in + 0.01:
            return True
        floor = max(10.0, size * 0.875)
        s_try = size - 0.5
        while s_try >= floor - 1e-6:
            _l, n_try = widow_lines(new, s_try, width_in, face=face, bold=bold)
            if n_try <= n_old:
                from pptx.util import Pt
                for r in runs:
                    if r.font.size:
                        r.font.size = Pt(s_try * (r.font.size.pt / size))
                return True
            s_try -= 0.5
        return False
    except Exception:
        return len(new) <= len(old) + 2


def copy_fix(path, report: Optional[Report] = None, rewriter=None) -> Report:
    """Repair the copy the plain-English check flags, in place (a .pptx).
    ``rewriter`` (or the registered one) gets the flagged units first; the
    fixed repairs take whatever is left. Never raises; never holds."""
    rep = report or Report(str(path))
    if Path(str(path)).suffix.lower() != ".pptx":
        return rep
    mod = copy_fix_module()
    if mod is None:
        rep.skip("copy_fix", "scripts missing")
        return rep
    try:
        res = mod.fix_file(path, rewriter if rewriter is not None else _COPY_REWRITER, guard=_copy_fits)
        before, after = res.get("before"), res.get("after")
        n = int(res.get("rewritten") or 0) + int(res.get("fallback") or 0)
        if before:
            rep.fixes["copy_flags_before"] = int(before)
        if n:
            rep.fixes["copy_units_repaired"] = n
            if res.get("rewritten"):
                rep.fixes["copy_units_rewritten"] = int(res["rewritten"])
        if after:
            rep.fixes["copy_flags_left"] = int(after)
        rep.add("copy_fix", not after,
                (f"{before} flag(s) on entry, {n} unit(s) repaired, {after or 0} left"
                 if before else "nothing flagged")
                + ("".join(f"\n  left: {x}" for x in (res.get("notes") or []))))
    except Exception as e:
        rep.add("copy_fix", False, f"{type(e).__name__}: {e}"[:300])
    return rep


def preship_fix(path, report: Optional[Report] = None, casing: bool = False) -> Report:
    """The in-place corrections on a deck before the checks run: page
    backgrounds set as backgrounds (a full-page shape or photo under a
    scrim becomes the slide background at 3840 px), a photo behind type
    scrimmed until the type reads, joining words a title-caser raised
    lowered (``casing``, with the copy repair), then widows bound and
    their boxes fitted. Never raises; never holds the file."""
    rep = report or Report(str(path))
    if Path(str(path)).suffix.lower() != ".pptx":
        return rep
    try:
        bg = fix_backgrounds_in_pptx(path)
        rep.fixes.update({f"background_{k}": v for k, v in bg.items() if v})
    except Exception as e:
        rep.fixes["background_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        pc = fix_photo_contrast_in_pptx(path)
        rep.fixes.update({f"photo_{k}": v for k, v in pc.items() if v})
    except Exception as e:
        rep.fixes["photo_error"] = f"{type(e).__name__}: {e}"[:200]
    if casing:
        try:
            n = fix_label_casing_in_pptx(path)
            if n:
                rep.fixes["casing_lowered"] = n
        except Exception as e:
            rep.fixes["casing_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        wd = fix_widows_in_pptx(path)
        rep.fixes.update({f"widow_{k}": v for k, v in wd.items() if v})
    except Exception as e:
        rep.fixes["widow_error"] = f"{type(e).__name__}: {e}"[:200]
    rep.add("preship_fix", not any(k.endswith("_error") for k in rep.fixes),
            ", ".join(f"{k}={v}" for k, v in sorted(rep.fixes.items())) or "nothing to fix")
    return rep


def finish(path, *, render_check: bool = False, lint: bool = True,
           copy_check: Optional[bool] = None, copy_repair: Optional[bool] = None,
           copy_rewriter=None) -> Report:
    """Finish one client-facing file in place: door metaphors rewritten,
    then on a deck the pre-ship fixes (backgrounds, photo contrast,
    widows), then embed and check the fonts, then the brand lint (FAILs
    reported) and the plain-English check on the file as it now stands
    (on every deck; on other files when asked). Never raises; never
    holds the file.

    ``copy_repair=True`` is for a builder that wrote the deck's copy:
    whatever the plain-English check flags is repaired in place before
    the layout fixes (``copy_fix``; ``copy_rewriter`` is the model pass
    over only the flagged lines), and joining words a title-caser raised
    are lowered. Without it the copy is checked and reported, never
    rewritten (a deck whose copy is data, an attachment at the mail door).

    ``ready`` means the file is clean at the end: the embed passed, the
    lint found no FAIL, and the plain-English check (when it ran) found
    no flag. ``copy_ok`` is that last check alone."""
    rep = Report(str(path))
    try:
        ext = Path(str(path)).suffix.lower()
        if copy_check is None:
            copy_check = ext == ".pptx"
        copy_repair = bool(copy_repair) and bool(copy_check) and ext == ".pptx"
        if lint and ext in (".pptx", ".docx", ".html", ".htm", ".md", ".txt"):
            scrub_doors_in_file(path, report=rep)
        if copy_repair:
            copy_fix(path, report=rep, rewriter=copy_rewriter)
        if lint and ext == ".pptx":
            preship_fix(path, report=rep, casing=copy_repair)
        embed_fonts(path, render_check=render_check, report=rep)
        if lint and ext in (".pptx", ".docx", ".html", ".htm"):
            brand_lint(path, report=rep)      # the linter reads Office, HTML and text; a .pdf is verified above
        copy_ran = False
        if copy_check and ext in (".pptx", ".docx", ".html", ".htm", ".md", ".txt"):
            readability(path, report=rep)     # the file as it stands now: this is the state the summary prints
            copy_ran = any(n == "readability" for n, _ok, _o in rep.steps)
        embed_ok = any(n == "embed" and ok for n, ok, _o in rep.steps)
        lint_ok = all(ok for n, ok, _o in rep.steps if n == "brand_lint")
        rep.ready = embed_ok and lint_ok and (rep.copy_ok or not copy_ran)
    except Exception:
        rep.add("finish", False, traceback.format_exc()[-1500:])
    try:
        print(f"[design-skill] {rep.summary}")
    except Exception:
        pass
    return rep


def gate_attachment(name: str, data: bytes, *, lint: bool = True, **kw) -> tuple:
    """The outbound door's gate for an attached deck / document / PDF:
    writes the bytes, runs ``finish`` (fixes applied in place), returns
    (bytes, Report). Anything that is not a .pptx / .docx / .pdf / .html
    passes through untouched. A failure never blocks the send."""
    ext = Path(str(name or "")).suffix.lower()
    if ext not in (".pptx", ".docx", ".pdf", ".html", ".htm") or not data:
        rep = Report(str(name)); rep.skip("gate", "not a design artifact"); rep.ready = True
        return data, rep
    tmp = tempfile.mkdtemp(prefix="cw_gate_")
    p = Path(tmp) / Path(str(name)).name
    try:
        p.write_bytes(data)
        rep = finish(p, lint=lint, **kw)
        try:
            data = p.read_bytes()
        except Exception:
            pass
        return data, rep
    except Exception:
        rep = Report(str(name)); rep.add("gate", False, traceback.format_exc()[-1500:])
        return data, rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def finish_bytes(data: bytes, ext: str = ".pptx", **kw) -> bytes:
    """``finish`` for in-memory file bytes (the builders that return bytes).
    Returns the finished bytes, or the input untouched on any failure."""
    out, _rep = gate_attachment(f"deck{ext}", data, **kw)
    return out or data


_LINT_MOD = None


def lint_module():
    """The pack's brand-lint module, imported once (its wrap model and font
    metrics are what the pre-ship check measures with). None when the pack
    is absent."""
    global _LINT_MOD
    if _LINT_MOD is not None:
        return _LINT_MOD or None
    sd = scripts_dir()
    if sd is None or not (sd / "cw_brand_lint.py").is_file():
        _LINT_MOD = False
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cw_brand_lint", str(sd / "cw_brand_lint.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LINT_MOD = mod
    except Exception:
        _LINT_MOD = False
        return None
    return _LINT_MOD


def widow_safe_width(text: str, size_pt: float, width_in: float, *, bold: bool = False,
                     face: Optional[str] = None, tracking_pt: float = 0.0, min_scale: float = 0.80) -> float:
    """A text-box width (inches) at which this paragraph's last line carries
    at least two words under the pack's own wrap model (the width is only
    ever narrowed, in 0.02 in steps, never below ``min_scale`` of the
    original). Returns the original width when the pack is absent, when
    the paragraph is short, or when no narrower width helps."""
    mod = lint_module()
    words = str(text or "").split()
    if mod is None or len(words) < 4 or size_pt < 10 or width_in <= 0:
        return width_in
    try:
        wt = mod._weight_key(face or FONT_FAMILY, bold)
        chars = [(ch, wt, float(size_pt), float(tracking_pt)) for ch in str(text).replace("\n", " ")]

        def lone(width_pt):
            for k in mod.WRAP_SCALES:
                ls = mod._wrap(chars, width_pt * k)
                if len(ls) >= 2 and len(ls[-1][0]) == 1:
                    return True
            return False

        if not lone(width_in * 72):
            return width_in
        w = width_in
        while w >= width_in * min_scale:
            w -= 0.02
            if not lone(w * 72):
                return round(w, 3)
    except Exception:
        pass
    return width_in


# --------------------------------------------------- pre-ship fixes (2026-11-10)
NBSP = "\u00a0"
MONO_FAMILY = "Geist Mono"
EMU_PER_IN = 914400
PAGE_PX = (3840, 2160)          # a 16:9 page at about 288 ppi, the pack's high-res bar
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "{%s}" % _NS_A
_P = "{%s}" % _NS_P
_R = "{%s}" % _NS_R


@dataclass
class WidowFit:
    text: str
    width_in: float
    size_pt: float
    action: str = "none"        # none | bound | widened | narrowed | resized | bound-only
    lines: int = 1

    @property
    def bound(self) -> bool:
        return NBSP in self.text and self.action != "none"


def bind_last_two(text) -> str:
    """Join the last two words of a paragraph with a non-breaking space so
    they wrap together. Only the final gap between two words changes; a
    paragraph of fewer than two words, or one already bound there, comes
    back as it was."""
    s = "" if text is None else str(text)
    body = s.rstrip()
    tail = s[len(body):]
    m = re.search(r"[ \t]+(?=[^\s\u00a0]+$)", body)
    if not m:
        return s
    return body[:m.start()] + NBSP + body[m.end():] + tail


def _model_lines(mod, text, size_pt, width_in, wt, tracking_pt, scale):
    chars = [(ch, wt, float(size_pt), float(tracking_pt)) for ch in str(text).replace("\n", " ")]
    return mod._wrap(chars, float(width_in) * 72.0 * scale)


def widow_lines(text, size_pt, width_in, *, face: Optional[str] = None, bold: bool = False,
                tracking_pt: float = 0.0) -> tuple:
    """(lone, lines) under the pack's wrap model across its renderer scales:
    ``lone`` when the last line carries one word at any scale, ``lines``
    the most lines any scale needs. A bound pair (U+00A0) is one word to
    the model, exactly as the lint reads it. (False, 1) without the pack."""
    mod = lint_module()
    if mod is None or width_in <= 0 or size_pt <= 0:
        return False, 1
    wt = mod._weight_key(face or FONT_FAMILY, bold)
    lone, n = False, 1
    for k in mod.WRAP_SCALES:
        ls = _model_lines(mod, text, size_pt, width_in, wt, tracking_pt, k)
        n = max(n, len(ls))
        if len(ls) >= 2 and len(ls[-1][0]) == 1:
            lone = True
    return lone, n


def widow_fit(text, size_pt: float, width_in: float, *, height_in: Optional[float] = None,
              face: Optional[str] = None, bold: bool = False, tracking_pt: float = 0.0,
              leading: float = 1.22, max_width_in: Optional[float] = None,
              min_scale: float = 0.60) -> WidowFit:
    """Widow-proof one paragraph for a text box ``width_in`` wide.

    Measured with the pack's wrap model (the metrics the lint checks with).
    When the last line would carry one word: the last two words are bound
    with U+00A0 so they wrap together in every renderer; then, because the
    pack's check reads a bound pair as one word, the box is widened (up to
    ``max_width_in`` when the caller has room) or narrowed in 0.02 in steps
    (never below ``min_scale`` of the width) until the check sees two words
    on the last line without adding a line the layout did not budget for
    (``height_in`` allows one more line when the box has the room); failing
    that, the type steps down a half point or two (never below 10 pt on
    body copy). The copy is never shortened and no line break is inserted.
    Chrome (under 9 pt), short labels (under 3 words) and Geist Mono rows
    come back untouched."""
    s = "" if text is None else str(text)
    out = WidowFit(s, float(width_in), float(size_pt))
    words = s.split()
    if (lint_module() is None or len(words) < 3 or size_pt < 9 or width_in <= 0
            or (face or "") == MONO_FAMILY or "\n" in s):
        return out
    lone0, n0 = widow_lines(s, size_pt, width_in, face=face, bold=bold, tracking_pt=tracking_pt)
    out.lines = n0
    if not lone0:
        return out
    bound = bind_last_two(s)
    out.text = bound
    out.action = "bound-only"
    if size_pt < 10:                      # notes: the check skips them; the binding is the whole fix
        out.action = "bound"
        return out

    def ok(t, size, w, allow):
        lone, n = widow_lines(t, size, w, face=face, bold=bold, tracking_pt=tracking_pt)
        return (not lone) and n <= allow, n

    allowed = n0
    # one more line only when the box clearly has the room for it (0.15 in
    # of slack beyond the extra line), so a read above a source line or a
    # label in a tight slot never grows into its neighbor
    if height_in is not None and (n0 + 1) * size_pt * leading / 72.0 <= float(height_in) - 0.15:
        allowed = n0 + 1
    fine, n = ok(bound, size_pt, width_in, allowed)
    if fine:
        out.action, out.lines = "bound", n
        return out
    # widen when the caller has room to the right
    if max_width_in is not None and max_width_in > width_in + 0.005:
        w = width_in
        while w + 0.02 <= max_width_in + 1e-6:
            w = round(w + 0.02, 3)
            fine, n = ok(bound, size_pt, w, allowed)
            if fine:
                out.width_in, out.action, out.lines = w, "widened", n
                return out
    # narrow a little: the same copy, the last line carrying two words
    floor = width_in * min_scale
    w = width_in
    while w - 0.02 >= floor - 1e-6:
        w = round(w - 0.02, 3)
        fine, n = ok(bound, size_pt, w, allowed)
        if fine:
            out.width_in, out.action, out.lines = w, "narrowed", n
            return out
    # The pair cannot be kept together at any width (a long last word: the
    # line before it would have to be nearly empty). The same copy without
    # the binding, at a width where the natural wrap leaves two words on the
    # last line at every renderer scale (2026-11-11: "... an under-25,
    # female-leaning audience." failed the lint as a bound pair alone).
    def ok_plain(w):
        lone, n = widow_lines(s, size_pt, w, face=face, bold=bold, tracking_pt=tracking_pt)
        return (not lone) and n <= allowed, n

    if max_width_in is not None and max_width_in > width_in + 0.005:
        w = width_in
        while w + 0.02 <= max_width_in + 1e-6:
            w = round(w + 0.02, 3)
            fine, n = ok_plain(w)
            if fine:
                out.text, out.width_in, out.action, out.lines = s, w, "widened", n
                return out
    w = width_in
    while w - 0.02 >= floor - 1e-6:
        w = round(w - 0.02, 3)
        fine, n = ok_plain(w)
        if fine:
            out.text, out.width_in, out.action, out.lines = s, w, "narrowed", n
            return out
    # a half point or two smaller, then the same search
    steps = (size_pt - 0.5, size_pt - 1.0) if size_pt < 20 else (size_pt - 2.0, size_pt - 3.0)
    for sz in steps:
        if sz < 10:
            break
        fine, n = ok(bound, sz, width_in, allowed)
        if fine:
            out.size_pt, out.action, out.lines = sz, "resized", n
            return out
        w = width_in
        while w - 0.02 >= floor - 1e-6:
            w = round(w - 0.02, 3)
            fine, n = ok(bound, sz, w, allowed)
            if fine:
                out.size_pt, out.width_in, out.action, out.lines = sz, w, "resized", n
                return out
    return out


def _slide_parts(z) -> tuple:
    """(slide part names in deck order, slide width EMU, slide height EMU)."""
    pres = z.read("ppt/presentation.xml").decode("utf8", "ignore")
    m = re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres)
    sw, sh = (int(m.group(1)), int(m.group(2))) if m else (12192000, 6858000)
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf8", "ignore")
    rid2t = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
    rid2t.update({a: b for b, a in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', rels)})
    order = [rid2t[r] for r in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres) if r in rid2t]
    slides = ["ppt/" + t.lstrip("/").replace("ppt/", "") for t in order]
    return slides, sw, sh


def _rewrite_zip(path, replace: dict, add: Optional[dict] = None) -> None:
    """Write the package back with ``replace`` {name: bytes} swapped in and
    ``add`` {name: bytes} appended. Entry order is kept."""
    import zipfile
    src = Path(str(path))
    data = src.read_bytes()
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            payload = replace.get(item.filename, zin.read(item.filename))
            zout.writestr(item, payload)
        for name, payload in (add or {}).items():
            zout.writestr(name, payload)
    src.write_bytes(out.getvalue())


def _shape_rects(root) -> list:
    """Every placed shape as (x, y, cx, cy, filled_without_text), the lint's
    own view of the page for ``room_right``."""
    rects = []
    for shp in root.iter(f"{_P}sp", f"{_P}pic", f"{_P}graphicFrame"):
        if shp.tag == f"{_P}graphicFrame":
            off, ext = shp.find(f"{_P}xfrm/{_A}off"), shp.find(f"{_P}xfrm/{_A}ext")
        else:
            off, ext = shp.find(f".//{_A}xfrm/{_A}off"), shp.find(f".//{_A}xfrm/{_A}ext")
        if off is None or ext is None:
            continue
        sp_ = shp.find(f"{_P}spPr")
        filled = shp.tag == f"{_P}pic" or (sp_ is not None and (
            sp_.find(f"{_A}solidFill") is not None or sp_.find(f"{_A}gradFill") is not None))
        tb = shp.find(f"{_P}txBody")
        has_txt = tb is not None and "".join(t.text or "" for t in tb.iter(f"{_A}t")).strip() != ""
        if filled or has_txt or shp.tag != f"{_P}sp":
            rects.append((int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")),
                          filled and not has_txt))
    return rects


def fix_widows_in_pptx(path) -> dict:
    """Bind and fit every widowed paragraph in a saved deck, in place (the
    net under every builder). Returns counts: bound, widened, narrowed,
    resized, unresolved."""
    import zipfile
    from lxml import etree
    mod = lint_module()
    counts = {"bound": 0, "widened": 0, "narrowed": 0, "resized": 0, "unresolved": 0}
    if mod is None:
        return counts
    z = zipfile.ZipFile(str(path))
    names = z.namelist()
    slides, sw, sh = _slide_parts(z)
    portrait = sh > sw
    replace = {}
    for sp in slides:
        if sp not in names:
            continue
        root = etree.fromstring(z.read(sp))
        rects = _shape_rects(root)
        touched = False
        for shp in root.iter(f"{_P}sp"):
            tx = shp.find(f"{_P}txBody")
            off, ext = shp.find(f".//{_A}xfrm/{_A}off"), shp.find(f".//{_A}xfrm/{_A}ext")
            if tx is None or off is None or ext is None:
                continue
            bp = tx.find(f"{_A}bodyPr")
            if bp is not None and (bp.get("wrap") == "none" or bp.get("vert") not in (None, "horz")):
                continue
            lins = int(bp.get("lIns", 91440)) if bp is not None else 91440
            rins = int(bp.get("rIns", 91440)) if bp is not None else 91440
            tins = int(bp.get("tIns", 45720)) if bp is not None else 45720
            bins = int(bp.get("bIns", 45720)) if bp is not None else 45720
            sx, sy, cx, cy = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
            width_in = (cx - lins - rins) / EMU_PER_IN
            if width_in <= 0:
                continue
            paras = [p_ for p_ in tx.iter(f"{_A}p")
                     if "".join(t.text or "" for t in p_.iter(f"{_A}t")).strip()]
            single = len(paras) == 1
            for p_ in paras:
                kids = [el for el in p_ if el.tag in (f"{_A}r", f"{_A}fld", f"{_A}br")]
                runs = [el for el in kids if el.tag == f"{_A}r"]
                if not runs or len(runs) != len(kids):
                    continue                      # fields and manual breaks: leave the paragraph alone
                texts = [(r.find(f"{_A}t").text or "") if r.find(f"{_A}t") is not None else "" for r in runs]
                text = "".join(texts)
                rprs = [r.find(f"{_A}rPr") for r in runs]
                sizes = [int(rp.get("sz")) / 100 for rp in rprs if rp is not None and rp.get("sz")]
                size = max(sizes) if sizes else 18.0
                rp0 = rprs[0]
                tracking = int(rp0.get("spc")) / 100 if rp0 is not None and rp0.get("spc") else 0.0
                lat = rp0.find(f"{_A}latin") if rp0 is not None else None
                face = lat.get("typeface") if lat is not None else None
                bold = rp0 is not None and rp0.get("b") == "1"
                if face == MONO_FAMILY:
                    continue
                algn = (p_.find(f"{_A}pPr").get("algn") if p_.find(f"{_A}pPr") is not None else None) or "l"
                room = None
                if single:
                    try:
                        limit = mod.room_right(sx, sy, cx, cy, sw, portrait, rects)
                        room = (limit - sx - lins - rins) / EMU_PER_IN
                    except Exception:
                        room = None
                fit = widow_fit(text, size, width_in, height_in=(cy - tins - bins) / EMU_PER_IN,
                                face=face, bold=bold, tracking_pt=tracking,
                                max_width_in=room if (single and algn == "l") else None)
                if fit.action == "none":
                    continue
                # the binding lands in whichever run holds the final gap (a fit
                # made without the binding leaves the gap a plain space)
                m = re.search(r"[ \t]+(?=[^\s\u00a0]+\s*$)", text) if NBSP in fit.text else None
                if m is not None:
                    pos = 0
                    first = True
                    for r, t in zip(runs, texts):
                        a, b = max(m.start(), pos), min(m.end(), pos + len(t))
                        if a < b:
                            la, lb = a - pos, b - pos
                            t_el = r.find(f"{_A}t")
                            t_el.text = t[:la] + (NBSP if first else "") + t[lb:]
                            first = False
                        pos += len(t)
                    counts["bound"] += 1
                    touched = True
                if fit.action in ("widened", "narrowed", "resized") and single:
                    if abs(fit.width_in - width_in) > 1e-6:
                        new_cx = int(round(fit.width_in * EMU_PER_IN)) + lins + rins
                        if algn == "r":
                            off.set("x", str(sx + (cx - new_cx)))
                        elif algn == "ctr":
                            off.set("x", str(sx + (cx - new_cx) // 2))
                        ext.set("cx", str(new_cx))
                    if abs(fit.size_pt - size) > 1e-6:
                        for rp in rprs:
                            if rp is not None:
                                rp.set("sz", str(int(round(fit.size_pt * 100))))
                    counts[fit.action] += 1
                elif fit.action == "bound-only":
                    counts["unresolved"] += 1
        if touched:
            replace[sp] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    z.close()
    if replace:
        _rewrite_zip(path, replace)
    return counts


def _hex_to_rgb(h: str) -> tuple:
    h = str(h or "").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cover_crop(im, size=PAGE_PX, top_bias: float = 0.22):
    """Cover-fill a Pillow image into ``size`` with no distortion: a wide
    source loses equal strips left and right; a tall one keeps the window
    toward the top (``top_bias`` of the lost height above) so faces stay
    in frame. Upscaled with Lanczos when the source is smaller."""
    from PIL import Image
    im = im.convert("RGB")
    tw, th = size
    sw_, sh_ = im.size
    tgt, src = tw / th, sw_ / sh_
    if src > tgt + 1e-6:
        new_w = int(round(sh_ * tgt))
        left = (sw_ - new_w) // 2
        im = im.crop((left, 0, left + new_w, sh_))
    elif src < tgt - 1e-6:
        new_h = int(round(sw_ / tgt))
        top = int(round((sh_ - new_h) * top_bias))
        im = im.crop((0, top, sw_, top + new_h))
    return im.resize((tw, th), Image.LANCZOS)


def bake_photo_background(image_bytes: bytes, *, scrim_hex: str = "#0C1618", alpha: float = 0.64,
                          size=PAGE_PX, quality: int = 88, crop=None) -> bytes:
    """A photo as a page background the house way: cover-cropped to the
    page at 3840 x 2160, the graphite scrim baked into the pixels (never a
    shape over the picture). ``crop`` is an optional (l, t, r, b) fraction
    set already applied in the deck (an a:srcRect) to honor first."""
    from PIL import Image
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if crop and any(crop):
        l, t, r_, b = crop
        W0, H0 = im.size
        im = im.crop((int(round(l * W0)), int(round(t * H0)), int(round(W0 * (1 - r_))), int(round(H0 * (1 - b)))))
    im = cover_crop(im, size)
    if alpha and alpha > 0:
        veil = Image.new("RGB", im.size, _hex_to_rgb(scrim_hex))
        im = Image.blend(im, veil, min(max(float(alpha), 0.0), 1.0))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=int(quality), optimize=True, progressive=True)
    return out.getvalue()


_GRADIENT_CACHE: dict = {}


def gradient_image_bytes(name: str = "graphiteWash", size=PAGE_PX, fmt: str = "JPEG") -> Optional[bytes]:
    """The pack's gradient (``assets/tokens.json`` -> gradient.<name>) built
    at full resolution and dithered, the way ``make_gradients.py`` builds
    it: a page gradient goes into the slide background as an image, never
    as a shape. Names: signal, orchid, signalDeep, orchidDeep, offWhiteEdge,
    graphiteWash (also accepted in the script's dashed spelling). Cached
    per process; None when the pack carries no such gradient."""
    key = (str(name), tuple(size), fmt)
    if key in _GRADIENT_CACHE:
        return _GRADIENT_CACHE[key]
    g = token("gradient")
    if not isinstance(g, dict):
        return None
    aliases = {"signal-deep": "signalDeep", "orchid-deep": "orchidDeep", "offwhite-edge": "offWhiteEdge",
               "graphite-wash": "graphiteWash", "glow-card": "glowCard"}
    spec = g.get(aliases.get(str(name), str(name)))
    if not isinstance(spec, dict) or not spec.get("stops"):
        return None
    try:
        import math
        import numpy as np
        from PIL import Image
        w, h = size
        stops = spec["stops"]
        if isinstance(stops[0], str):
            stops = [[0.0, stops[0]], [1.0, stops[1]]]
        a = math.radians(float(spec.get("angle", 47)))
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
        p = xs * math.cos(a) + ys * math.sin(a)
        t = (p - p.min()) / max(p.max() - p.min(), 1e-9)
        pos = np.array([float(s[0]) for s in stops])
        cols = np.array([_hex_to_rgb(s[1]) for s in stops], dtype=np.float64)
        rgb = np.stack([np.interp(t, pos, cols[:, c]) for c in range(3)], axis=-1)
        rng = np.random.default_rng(7)
        d = (rng.random((h, w)) - rng.random((h, w)))[..., None]      # triangular noise, one level
        arr = np.clip(np.floor(rgb + d + 0.5), 0, 255).astype(np.uint8)
        out = io.BytesIO()
        if fmt.upper() in ("JPG", "JPEG"):
            Image.fromarray(arr, "RGB").save(out, format="JPEG", quality=95, optimize=True, progressive=True)
        else:
            Image.fromarray(arr, "RGB").save(out, format="PNG", compress_level=6)
        _GRADIENT_CACHE[key] = out.getvalue()
        return _GRADIENT_CACHE[key]
    except Exception:
        return None


def set_slide_background_image(slide, image_bytes: bytes) -> str:
    """Set a python-pptx slide's background to an image (``p:bg`` with a
    blip fill, stretched to the page). Returns the relationship id."""
    from lxml import etree
    image_part, rId = slide.part.get_or_add_image_part(io.BytesIO(image_bytes))
    cSld = slide._element.find(f"{_P}cSld")
    old = cSld.find(f"{_P}bg")
    if old is not None:
        cSld.remove(old)
    xml = (f'<p:bg xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}"><p:bgPr>'
           f'<a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="{rId}"/>'
           f'<a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>')
    cSld.insert(0, etree.fromstring(xml))
    return rId


def _bg_xml_image(rid: str) -> bytes:
    return (f'<p:bg xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}"><p:bgPr>'
            f'<a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="{rid}"/>'
            f'<a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>').encode()


def _bg_xml_solid(hex6: str) -> bytes:
    return (f'<p:bg xmlns:p="{_NS_P}" xmlns:a="{_NS_A}"><p:bgPr><a:solidFill><a:srgbClr val="{hex6}"/>'
            f'</a:solidFill><a:effectLst/></p:bgPr></p:bg>').encode()


def fix_backgrounds_in_pptx(path) -> dict:
    """Turn full-page shapes acting as the page into real slide backgrounds,
    in place. A solid rectangle becomes the background color; a full-page
    picture (with any scrim rectangles over it) is baked into one 3840 px
    background image, scrim included; a translucent veil over a solid page
    becomes the blended color. The leading full-page shapes come off the
    page; everything above them stays. Returns counts: solid, image."""
    import zipfile
    from lxml import etree
    from PIL import Image
    counts = {"solid": 0, "image": 0}
    z = zipfile.ZipFile(str(path))
    names = z.namelist()
    slides, sw, sh = _slide_parts(z)
    replace, add = {}, {}
    media_n = 0
    ct_name = "[Content_Types].xml"
    ct_xml = z.read(ct_name).decode("utf8", "ignore") if ct_name in names else ""
    need_jpg_default = False
    for sp in slides:
        if sp not in names:
            continue
        root = etree.fromstring(z.read(sp))
        cSld = root.find(f"{_P}cSld")
        tree = cSld.find(f"{_P}spTree") if cSld is not None else None
        if tree is None:
            continue
        rels_name = sp.replace("slides/", "slides/_rels/") + ".rels"
        rels_xml = z.read(rels_name).decode("utf8", "ignore") if rels_name in names else ""
        rmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels_xml))
        rmap.update({a: b for b, a in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', rels_xml)})

        def full_page(el):
            off, ext = el.find(f".//{_A}xfrm/{_A}off"), el.find(f".//{_A}xfrm/{_A}ext")
            if off is None or ext is None:
                return False
            x, y, cx, cy = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
            return cx >= 0.95 * sw and cy >= 0.95 * sh and abs(x) <= 0.03 * sw and abs(y) <= 0.03 * sh

        stack = []
        for el in tree:
            if el.tag in (f"{_P}nvGrpSpPr", f"{_P}grpSpPr"):
                continue
            if el.tag == f"{_P}pic" and full_page(el):
                stack.append(el)
                continue
            if el.tag == f"{_P}sp" and full_page(el):
                tb = el.find(f"{_P}txBody")
                has_txt = tb is not None and "".join(t.text or "" for t in tb.iter(f"{_A}t")).strip() != ""
                sppr = el.find(f"{_P}spPr")
                if not has_txt and sppr is not None and sppr.find(f"{_A}solidFill/{_A}srgbClr") is not None:
                    stack.append(el)
                    continue
            break
        if not stack:
            continue
        # the page color under the stack (the existing background, when solid)
        base_hex = None
        bg = cSld.find(f"{_P}bg")
        if bg is not None:
            c = bg.find(f".//{_A}solidFill/{_A}srgbClr")
            if c is not None and bg.find(f".//{_A}blip") is None and bg.find(f".//{_A}gradFill") is None:
                base_hex = c.get("val")
        image = None
        color = _hex_to_rgb(base_hex) if base_hex else None
        for el in stack:
            if el.tag == f"{_P}pic":
                blip = el.find(f".//{_A}blip")
                target = rmap.get(blip.get(f"{_R}embed")) if blip is not None else None
                part = None
                if target:
                    import posixpath
                    part = posixpath.normpath(posixpath.join(posixpath.dirname(sp), target))
                if part and part in names:
                    try:
                        crop = None
                        src = el.find(f".//{_A}srcRect")
                        if src is not None:
                            crop = tuple(int(src.get(k, "0")) / 100000 for k in ("l", "t", "r", "b"))
                        im = Image.open(io.BytesIO(z.read(part))).convert("RGB")
                        if crop and any(crop):
                            l, t, r_, b = crop
                            W0, H0 = im.size
                            im = im.crop((int(round(l * W0)), int(round(t * H0)),
                                          int(round(W0 * (1 - r_))), int(round(H0 * (1 - b)))))
                        image = cover_crop(im, PAGE_PX)
                        color = None
                    except Exception:
                        image = None
                continue
            srgb = el.find(f"{_P}spPr").find(f"{_A}solidFill/{_A}srgbClr")
            rgb = _hex_to_rgb(srgb.get("val"))
            alpha_el = srgb.find(f"{_A}alpha")
            alpha = int(alpha_el.get("val")) / 100000 if alpha_el is not None else 1.0
            alpha = min(max(alpha, 0.0), 1.0)
            if image is not None:
                veil = Image.new("RGB", image.size, rgb)
                image = Image.blend(image, veil, alpha)
            elif color is not None:
                color = tuple(int(round(c0 * (1 - alpha) + c1 * alpha)) for c0, c1 in zip(color, rgb))
            else:
                color = rgb
        if image is None and color is None:
            continue
        for el in stack:
            tree.remove(el)
        if bg is not None:
            cSld.remove(bg)
        if image is not None:
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
            media_n += 1
            media_name = f"ppt/media/cwbg{media_n}.jpg"
            while media_name in names or media_name in add:
                media_n += 1
                media_name = f"ppt/media/cwbg{media_n}.jpg"
            add[media_name] = out.getvalue()
            ids = set(re.findall(r'Id="(rId\d+)"', rels_xml))
            n = 1
            while f"rId{n}" in ids:
                n += 1
            rid = f"rId{n}"
            rel = (f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   f'relationships/image" Target="../media/{media_name.split("/")[-1]}"/>')
            if rels_xml:
                rels_xml = rels_xml.replace("</Relationships>", rel + "</Relationships>")
            else:
                rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                            + rel + "</Relationships>")
            replace[rels_name] = rels_xml.encode("utf8")
            cSld.insert(0, etree.fromstring(_bg_xml_image(rid)))
            need_jpg_default = True
            counts["image"] += 1
        else:
            cSld.insert(0, etree.fromstring(_bg_xml_solid("%02X%02X%02X" % color)))
            counts["solid"] += 1
        replace[sp] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    z.close()
    if need_jpg_default and ct_xml and 'Extension="jpg"' not in ct_xml:
        ct_xml = ct_xml.replace("<Default ", '<Default Extension="jpg" ContentType="image/jpeg"/><Default ', 1)
        replace[ct_name] = ct_xml.encode("utf8")
    if replace or add:
        _rewrite_zip(path, replace, add)
    return counts


# ------------------------------------------- casing, photos behind type (2026-11-11)
def fix_label_casing_in_pptx(path) -> int:
    """Lower the joining words a title-caser raised, on every paragraph of
    a saved deck, in place (``25 To 34`` -> ``25 to 34``). Only the case of
    a letter changes, so runs keep their lengths and formatting. Returns
    the number of paragraphs changed."""
    import zipfile
    from lxml import etree
    mod = copy_fix_module()
    if mod is None:
        return 0
    z = zipfile.ZipFile(str(path))
    names = z.namelist()
    slides, _sw, _sh = _slide_parts(z)
    replace, n = {}, 0
    for sp in slides:
        if sp not in names:
            continue
        raw = z.read(sp)
        if not re.search(rb"\b(To|And|Or|Of|The|Vs|Through|Thru)\b", raw):
            continue
        root = etree.fromstring(raw)
        touched = False
        for p_ in root.iter(f"{_A}p"):
            ts = [t for t in p_.iter(f"{_A}t")]
            text = "".join(t.text or "" for t in ts)
            if not text.strip():
                continue
            new = mod.small_word_case(text)
            if new == text or len(new) != len(text):
                continue
            pos = 0
            for t in ts:
                k = len(t.text or "")
                t.text = new[pos:pos + k]
                pos += k
            n += 1
            touched = True
        if touched:
            replace[sp] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    z.close()
    if replace:
        _rewrite_zip(path, replace)
    return n


MIN_TEXT_CONTRAST = 4.5          # type over a photograph, against the brightest part behind it
MAX_SCRIM_ALPHA = 0.86           # the deepest scrim a photo takes; past it the page takes the gradient
LIGHT_PHOTO_MEDIAN = 0.50        # a photo this light behind the type (median, 0 to 1) is all scrim at that depth
_MEASURE_PX = (384, 216)


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(rgb) -> float:
    """WCAG relative luminance of an (r, g, b) color, 0 to 1."""
    r, g, b = (float(x) for x in tuple(rgb)[:3])
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def _lum_array(im):
    """Relative luminance of every pixel of a Pillow image, measured on a
    384 x 216 copy (enough to see what sits behind a line of type)."""
    import numpy as np
    from PIL import Image
    small = im.convert("RGB").resize(_MEASURE_PX, Image.BILINEAR)
    a = np.asarray(small, dtype=np.float64) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2], np.asarray(small, dtype=np.float64)


def _zone_slice(zone, shape):
    h, w = shape[:2]
    x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in zone[:4])
    a, b = int(x0 * w), max(int(x0 * w) + 1, int(round(x1 * w)))
    c, d = int(y0 * h), max(int(y0 * h) + 1, int(round(y1 * h)))
    return slice(c, min(d, h)), slice(a, min(b, w))


def zone_contrast(im, zones) -> float:
    """The lowest contrast any zone's type gets against the bright part of
    the image behind it. ``zones`` is a list of (x0, y0, x1, y1, (r, g, b)):
    a box in page fractions and the color of the type in it. The image is
    read at its 95th percentile inside each box, so a white patch behind
    three words counts even when the rest of the box is dark."""
    import numpy as np
    lum, _rgb = _lum_array(im)
    worst = 99.0
    for z in zones or []:
        ys, xs = _zone_slice(z, lum.shape)
        patch = lum[ys, xs]
        if patch.size == 0:
            continue
        behind = float(np.percentile(patch, 95))
        worst = min(worst, contrast_ratio(rel_luminance(z[4]), behind))
    return worst


def zone_median_luminance(im, zones) -> float:
    """The median relative luminance of the image across the text zones
    (0 to 1): how light the picture is behind the type as a whole."""
    import numpy as np
    lum, _rgb = _lum_array(im)
    vals = []
    for z in zones or []:
        ys, xs = _zone_slice(z, lum.shape)
        vals.append(lum[ys, xs].ravel())
    return float(np.median(np.concatenate(vals))) if vals else 0.0


def scrim_alpha_for_text(im, zones, *, scrim_hex: str = "#0C1618", alpha: float = 0.64,
                         max_alpha: float = MAX_SCRIM_ALPHA, min_contrast: float = MIN_TEXT_CONTRAST):
    """The lightest scrim, from ``alpha`` up in 0.02 steps, under which
    every zone's type clears ``min_contrast`` on this image; None when
    even ``max_alpha`` leaves the type unreadable."""
    from PIL import Image
    small = im.convert("RGB").resize(_MEASURE_PX, Image.BILINEAR)
    veil = Image.new("RGB", small.size, _hex_to_rgb(scrim_hex))
    a = min(max(float(alpha), 0.0), 1.0)
    while a <= max_alpha + 1e-9:
        if zone_contrast(Image.blend(small, veil, a), zones) >= min_contrast:
            return round(a, 2)
        a += 0.02
    return None


def bake_photo_for_text(image_bytes: bytes, zones, *, scrim_hex: str = "#0C1618", alpha: float = 0.64,
                        max_alpha: float = MAX_SCRIM_ALPHA, min_contrast: float = MIN_TEXT_CONTRAST,
                        size=PAGE_PX, quality: int = 88, crop=None) -> tuple:
    """``bake_photo_background`` with the scrim set by what is behind the
    type: the luminance behind each text zone is measured on the cropped
    photo and the scrim is deepened from ``alpha`` until every zone
    clears ``min_contrast``. Returns (image bytes, alpha used); (None,
    None) when no scrim up to ``max_alpha`` makes the type readable, or
    when the photo is so light behind the type that the scrim it needs
    would leave nothing of it, so the caller sets the gradient page
    instead of the photo."""
    from PIL import Image
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if crop and any(crop):
        l, t, r_, b = crop
        W0, H0 = im.size
        im = im.crop((int(round(l * W0)), int(round(t * H0)), int(round(W0 * (1 - r_))), int(round(H0 * (1 - b)))))
    im = cover_crop(im, size)
    use = scrim_alpha_for_text(im, zones, scrim_hex=scrim_hex, alpha=alpha, max_alpha=max_alpha,
                               min_contrast=min_contrast) if zones else alpha
    if use is None:
        return None, None
    # A photo that is light all the way across the type (a white page, an
    # interface, a sky) would be nothing but scrim at the depth it needs:
    # no photo, the gradient page instead.
    if zones and use >= max_alpha - 0.04 and zone_median_luminance(im, zones) > LIGHT_PHOTO_MEDIAN:
        return None, None
    veil = Image.new("RGB", im.size, _hex_to_rgb(scrim_hex))
    im = Image.blend(im, veil, min(max(float(use), 0.0), 1.0))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=int(quality), optimize=True, progressive=True)
    return out.getvalue(), use


PHOTO_DETAIL_MIN = 2.0           # image_detail at or above this is a photograph, below it a gradient or a flat ground


def image_detail(im) -> float:
    """How much picture an image carries: the page is read as a 32 x 18
    grid of block averages and the measure is the mean bend from block to
    block (the second difference, 0 to 255). A gradient changes evenly, so
    it reads under 1 whatever its colors; a photograph reads 4 and up even
    under the deepest scrim."""
    import numpy as np
    from PIL import Image
    g = np.asarray(im.convert("L").resize((32, 18), Image.BOX), dtype=np.float64)
    d2x = np.abs(g[:, 2:] - 2 * g[:, 1:-1] + g[:, :-2]).mean()
    d2y = np.abs(g[2:, :] - 2 * g[1:-1, :] + g[:-2, :]).mean()
    return float((d2x + d2y) / 2.0)


def fix_photo_contrast_in_pptx(path) -> dict:
    """The net under every builder for a photo behind type. On each slide
    whose background is a photograph, the image is read behind every
    light text box (9 pt and up, not sitting on its own panel); when any
    box falls under 4.5:1 the graphite scrim is deepened in the image
    until all of them clear it, and when no scrim short of solid does,
    the slide takes the pack's graphite gradient instead. A gradient or a
    flat background is left alone. Returns counts: deepened, gradient."""
    import posixpath
    import zipfile
    from lxml import etree
    from PIL import Image
    counts = {"deepened": 0, "gradient": 0}
    z = zipfile.ZipFile(str(path))
    names = z.namelist()
    slides, sw, sh = _slide_parts(z)
    replace, add = {}, {}
    n_media = 0
    ct_name = "[Content_Types].xml"
    ct_xml = z.read(ct_name).decode("utf8", "ignore") if ct_name in names else ""
    for sp in slides:
        if sp not in names:
            continue
        raw = z.read(sp)
        if b"<p:bg" not in raw or b"blip" not in raw:
            continue
        root = etree.fromstring(raw)
        bg = root.find(f"{_P}cSld/{_P}bg")
        blip = bg.find(f".//{_A}blip") if bg is not None else None
        if blip is None:
            continue
        rid = blip.get(f"{_R}embed")
        rels_name = sp.replace("slides/", "slides/_rels/") + ".rels"
        rels_xml = z.read(rels_name).decode("utf8", "ignore") if rels_name in names else ""
        m = re.search(r'<Relationship\b[^>]*\bId="%s"[^>]*>' % re.escape(rid or ""), rels_xml)
        tm = re.search(r'Target="([^"]+)"', m.group(0)) if m else None
        if not tm:
            continue
        part = posixpath.normpath(posixpath.join(posixpath.dirname(sp), tm.group(1)))
        if part not in names:
            continue
        try:
            im = Image.open(io.BytesIO(z.read(part))).convert("RGB")
        except Exception:
            continue
        if image_detail(im) < PHOTO_DETAIL_MIN:
            continue                                  # a gradient or a flat ground, not a photograph
        rects = _shape_rects(root)
        panels = [(x, y, cx, cy) for x, y, cx, cy, flat in rects if flat and cx < 0.95 * sw]
        zones = []
        for shp in root.iter(f"{_P}sp"):
            tx = shp.find(f"{_P}txBody")
            off, ext = shp.find(f".//{_A}xfrm/{_A}off"), shp.find(f".//{_A}xfrm/{_A}ext")
            if tx is None or off is None or ext is None:
                continue
            if not "".join(t.text or "" for t in tx.iter(f"{_A}t")).strip():
                continue
            x, y, cx, cy = (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
            if any(px <= x + cx // 2 <= px + pcx and py <= y + cy // 2 <= py + pcy for px, py, pcx, pcy in panels):
                continue                              # this type sits on its own panel
            size, rgb = 0.0, None
            for rp in tx.iter(f"{_A}rPr"):
                if rp.get("sz"):
                    size = max(size, int(rp.get("sz")) / 100)
                c = rp.find(f"{_A}solidFill/{_A}srgbClr")
                if rgb is None and c is not None:
                    rgb = _hex_to_rgb(c.get("val"))
            if size < 9 or rgb is None or rel_luminance(rgb) < 0.18:
                continue
            zones.append((x / sw, y / sh, (x + cx) / sw, (y + cy) / sh, rgb))
        if not zones or zone_contrast(im, zones) >= MIN_TEXT_CONTRAST - 0.2:
            continue
        extra = scrim_alpha_for_text(im, zones, alpha=0.04, max_alpha=0.80)
        if extra is not None:
            veil = Image.new("RGB", im.size, _hex_to_rgb("#0C1618"))
            out = io.BytesIO()
            Image.blend(im, veil, extra).save(out, format="JPEG", quality=88, optimize=True, progressive=True)
            data, kind = out.getvalue(), "deepened"
        else:
            data, kind = gradient_image_bytes("graphiteWash"), "gradient"
            if not data:
                continue
        n_media += 1
        media_name = f"ppt/media/cwfx{n_media}.jpg"
        while media_name in names or media_name in add:
            n_media += 1
            media_name = f"ppt/media/cwfx{n_media}.jpg"
        add[media_name] = data
        new_rel = re.sub(r'Target="[^"]+"', f'Target="../media/{media_name.split("/")[-1]}"', m.group(0))
        replace[rels_name] = rels_xml.replace(m.group(0), new_rel).encode("utf8")
        counts[kind] += 1
    z.close()
    if add and ct_xml and 'Extension="jpg"' not in ct_xml:
        replace[ct_name] = ct_xml.replace("<Default ", '<Default Extension="jpg" ContentType="image/jpeg"/><Default ', 1).encode("utf8")
    if replace or add:
        _rewrite_zip(path, replace, add)
    return counts


# ------------------------------------------------ cover photo candidates (2026-11-11)
# A deck about YouTube opened on a Wikipedia screenshot of YouTube's search
# page (the same address twice) with a logo from a free-icon site next in
# line. A cover takes a photograph or the gradient, never an interface, a
# logo, an icon or clip art.
_PHOTO_BAD_HOSTS = (
    "freeiconspng", "vecteezy", "flaticon", "iconfinder", "icons8", "iconscout", "iconarchive", "thenounproject",
    "pngtree", "pngwing", "pngegg", "pngmart", "pngimg", "pngall", "pngkey", "pngitem", "pngfind", "pngarts",
    "pngplay", "kindpng", "cleanpng", "stickpng", "seekpng", "nicepng", "toppng", "clipart", "clipground",
    "seeklogo", "logos-world", "1000logos", "logodownload", "logowik", "brandslogos", "logolook", "logotyp",
    "freepnglogos", "freelogovectors", "logo.wine", "worldvectorlogo", "vectorstock", "vectorlogo", "freepik",
    "shutterstock", "istockphoto", "gettyimages", "alamy", "dreamstime", "depositphotos", "123rf",
    "stock.adobe", "adobestock", "bigstockphoto",
)
_PHOTO_BAD_WORDS = re.compile(
    r"(?<![a-z])(logo|logos|logotype|icon|icons|clip[-_ +]?art|vector|wordmark|emblem|favicon|sprite|badge|"
    r"screenshot|screenshots|screen[-_ +]?shot|screen[-_ +]?capture|screen[-_ +]?grab|interface|mockup|"
    r"wireframe|transparent|sticker|infographic|diagram|flowchart|placeholder|watermark|"
    r"wallpaper|wallpapers|background|backgrounds|banner|banners|thumbnail|thumbnails|template|templates|"
    r"illustration|illustrations|artwork|graphic|graphics)(?![a-z])")
_PHOTO_BAD_EXT = (".svg", ".gif", ".ico", ".webp", ".bmp", ".tif", ".tiff")


def photo_url_key(url) -> str:
    """One address for one picture: host and path, lower case, without the
    scheme, the query or a size suffix, so the same file reached two ways
    (a resolver and a wiki lookup, a tracking query) counts once."""
    import urllib.parse as up
    u = str(url or "").strip()
    try:
        pr = up.urlsplit(u)
        host = (pr.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host + up.unquote(pr.path or "").lower().rstrip("/")
    except Exception:
        return u.lower().split("?", 1)[0]


def photo_url_reject_reason(url):
    """Why this address is not a cover photograph, or None. Read from the
    address alone: an icon, clip-art, logo or stock-preview site; a file
    named as a logo, an icon, a screenshot, an interface, a wallpaper, a
    background, a banner or an illustration (a graphic made to sit behind
    something, not a picture of the subject); a format a photograph does
    not come in."""
    key = photo_url_key(url)
    if not key:
        return "no address"
    host = key.split("/", 1)[0]
    path = key[len(host):]
    for bad in _PHOTO_BAD_HOSTS:
        if bad in host:
            return f"an icon, logo, clip-art or stock-preview site ({bad})"
    if path.endswith(_PHOTO_BAD_EXT):
        return f"not a photograph format ({path.rsplit('.', 1)[-1]})"
    m = _PHOTO_BAD_WORDS.search(path.replace("%20", " "))
    if m:
        return f"the file name says {m.group(1)}"
    return None


def photo_blob_reject_reason(blob):
    """Why these image bytes are not a cover photograph, or None. Read from
    the pixels: transparency (a cut-out graphic), a page that is mostly
    white (an interface or a document), one flat color over a third of the
    frame that is not shadow (a logo or an icon on its ground), or too few
    colors to be a photograph."""
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            alpha = np.asarray(im.convert("RGBA").resize((160, 160)))[..., 3]
            if float((alpha < 250).mean()) > 0.02:
                return "a cut-out graphic (transparent ground)"
        small = im.convert("RGB")
        small.thumbnail((200, 200))
        a = np.asarray(small, dtype=np.uint8).reshape(-1, 3)
        if a.shape[0] < 400:
            return "too small to read"
        white = float(((a[:, 0] > 235) & (a[:, 1] > 235) & (a[:, 2] > 235)).mean())
        if white >= 0.40:
            return "mostly white (an interface, a document or a graphic on white)"
        q = (a >> 4).astype(np.int32)
        bins = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
        vals, cnts = np.unique(bins, return_counts=True)
        share = cnts / float(a.shape[0])
        top = int(np.argmax(cnts))
        top_rgb = ((vals[top] // 256) * 16 + 8, ((vals[top] // 16) % 16) * 16 + 8, (vals[top] % 16) * 16 + 8)
        if share[top] >= 0.35 and max(top_rgb) > 72:
            return "one flat color over a third of the frame (a logo or an icon on its ground)"
        if int((share >= 0.002).sum()) < 20:
            return "too few colors for a photograph (a flat graphic)"
    except Exception:
        return "unreadable image"
    return None


def render_preview(path, out_dir=None, dpi: int = 90) -> Optional[Path]:
    """Page images through the pack's ``render_preview.py`` (needs
    LibreOffice). Returns the output directory or None."""
    if not soffice_available():
        return None
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="cw_preview_"))
    args = [str(path), "--out", str(out), "--dpi", str(dpi)]
    if font_dir() is not None:
        args += ["--fonts", str(font_dir())]
    got = _run("render_preview.py", args, timeout=900)
    if got is None or got[0] != 0:
        return None
    return out


def status() -> dict:
    """What this machine has (the System Status card, tests)."""
    r = skill_root()
    return {
        "pack": PACK_NAME, "version": PACK_VERSION,
        "root": str(r) if r else None,
        "full_skill": bool(full_skill_root()),
        "packaged_copy": _is_pack(PACKAGED),
        "font_family": family_name("Regular"),
        "fonts": sorted(fonts().keys()),
        "deck_template": str(deck_template()) if deck_template() else None,
        "fonttools": _have_fonttools(),
        "soffice": soffice_available(),
        "scripts": str(scripts_dir()) if scripts_dir() else None,
    }


__all__ = [
    "PACK_NAME", "PACK_VERSION", "FONT_FAMILY", "WEIGHTS", "PACKAGED", "PACKAGED_SCRIPTS",
    "PACKAGED_TEMPLATES", "PACKAGED_ASSET_FILES", "skill_root", "full_skill_root", "available",
    "font_dir", "font_path", "legacy_font_path", "fonts", "using_house_font", "family_name",
    "register_reportlab", "tokens", "token", "color", "template_path", "deck_template", "logo_path",
    "scripts_dir", "lint_module", "door_copy_module", "widow_safe_width", "Report", "verify_pdf_fonts", "embed_fonts", "brand_lint", "readability", "scrub_door_metaphor", "scrub_doors_in_file", "finish", "gate_attachment",
    "finish_bytes", "render_preview", "soffice_available", "status",
    # pre-ship fixes (2026-11-10)
    "NBSP", "MONO_FAMILY", "PAGE_PX", "WidowFit", "bind_last_two", "widow_lines", "widow_fit",
    "fix_widows_in_pptx", "fix_backgrounds_in_pptx", "preship_fix", "cover_crop", "bake_photo_background",
    "gradient_image_bytes", "set_slide_background_image",
    # copy repaired in place, casing, photos behind type, cover candidates (2026-11-11)
    "copy_fix_module", "copy_fix", "set_copy_rewriter", "copy_rewriter", "fix_label_casing",
    "scrub_stock_phrases", "fix_label_casing_in_pptx", "MIN_TEXT_CONTRAST", "MAX_SCRIM_ALPHA",
    "rel_luminance", "contrast_ratio", "zone_contrast", "zone_median_luminance", "LIGHT_PHOTO_MEDIAN",
    "scrim_alpha_for_text", "bake_photo_for_text",
    "image_detail", "PHOTO_DETAIL_MIN", "fix_photo_contrast_in_pptx", "photo_url_key", "photo_url_reject_reason",
    "photo_blob_reject_reason",
]
