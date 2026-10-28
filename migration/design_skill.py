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
"""
from __future__ import annotations

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
    "render_preview.py", "cw_door_copy.py",
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

    def add(self, name: str, ok: bool, output: str = "") -> None:
        self.steps.append((name, bool(ok), str(output or "")[-4000:]))

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, str(why)))

    @property
    def summary(self) -> str:
        bits = [f"{n}: {'ok' if ok else 'FAIL'}" for n, ok, _o in self.steps]
        bits += [f"{n}: skipped ({w})" for n, w in self.skipped]
        return f"{Path(self.path).name}: " + ("READY" if self.ready else "NOT READY") + (" (" + "; ".join(bits) + ")" if bits else "")

    def as_dict(self) -> dict:
        return {"path": self.path, "ready": self.ready,
                "steps": [{"name": n, "ok": ok, "output": o} for n, ok, o in self.steps],
                "skipped": [{"name": n, "why": w} for n, w in self.skipped]}


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
    return rep


def readability(path, report: Optional[Report] = None) -> Report:
    rep = report or Report(str(path))
    got = _run("readability_check.py", [str(path)])
    if got is None:
        rep.skip("readability", "scripts missing")
        return rep
    rc, out = got
    rep.add("readability", rc == 0, out)
    return rep


def finish(path, *, render_check: bool = False, lint: bool = True, copy_check: bool = False) -> Report:
    """Finish one client-facing file in place: embed and check the fonts,
    then the brand lint (FAILs reported) and, when asked, the plain-English
    check. Never raises; never holds the file. ``ready`` means the embed
    passed and the lint found no FAIL."""
    rep = Report(str(path))
    try:
        embed_fonts(path, render_check=render_check, report=rep)
        ext = Path(str(path)).suffix.lower()
        if lint and ext in (".pptx", ".docx", ".html", ".htm", ".md", ".txt"):
            scrub_doors_in_file(path, report=rep)
        if lint and ext in (".pptx", ".docx", ".html", ".htm"):
            brand_lint(path, report=rep)      # the linter reads Office, HTML and text; a .pdf is verified above
        if copy_check and ext in (".pptx", ".docx", ".html", ".htm", ".md", ".txt"):
            readability(path, report=rep)
        embed_ok = any(n == "embed" and ok for n, ok, _o in rep.steps)
        lint_ok = all(ok for n, ok, _o in rep.steps if n == "brand_lint")
        rep.ready = embed_ok and lint_ok
    except Exception:
        rep.add("finish", False, traceback.format_exc()[-1500:])
    try:
        print(f"[design-skill] {rep.summary}")
    except Exception:
        pass
    return rep


def gate_attachment(name: str, data: bytes, *, lint: bool = True) -> tuple:
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
        rep = finish(p, lint=lint)
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
]
