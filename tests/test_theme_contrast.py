"""Theme palettes (docs/vendor/theme.css) — contrast and wiring, no browser needed.

The palette used to live in a :root block copy-pasted into all 22 pages, in three
quietly divergent variants. These tests exist so that stays fixed: one file owns
the colours, every page links it, and no theme can ship text below WCAG AA.

Contrast is computed here rather than eyeballed because the failure is invisible
in review — #8b9bb4 on #0c111d measures 5.76, which looks fine until any opacity
is applied and it drops to 4.02.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
THEME_CSS = DOCS / "vendor" / "theme.css"
THEME_JS = DOCS / "vendor" / "theme.js"

AA = 4.5            # WCAG 2.1 normal-size body text
AAA = 7.0

# Foregrounds that carry TEXT, against every surface they can land on. Border and
# the *-soft fills are excluded: they are non-text, and WCAG's 3.0 rule covers
# meaningful controls rather than decorative rules.
FOREGROUNDS = ("text", "muted", "teal", "teal2", "green", "red", "amber", "purple")
SURFACES = ("bg", "panel", "panel2", "chip")


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 2)


def themes() -> dict[str, dict[str, str]]:
    """{theme name: {var: #hex}} parsed straight from the stylesheet."""
    css = THEME_CSS.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for selector, name, body in re.findall(
            r'(:root(?:\[data-theme="([\w-]+)"\])?[^{]*)\{([^}]*)\}', css):
        pairs = dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", body))
        if "bg" in pairs:
            out[name or "dark"] = pairs
    return out


def test_all_four_themes_are_defined():
    assert set(themes()) == {"dark", "emerald-blue", "emerald-green", "light-beige"}


@pytest.mark.parametrize("theme", sorted(themes()))
def test_every_text_pair_clears_wcag_aa(theme):
    palette = themes()[theme]
    failures = []
    for fg in FOREGROUNDS:
        for bg in SURFACES:
            if fg not in palette or bg not in palette:
                continue
            ratio = contrast(palette[fg], palette[bg])
            if ratio < AA:
                failures.append(f"{fg} on {bg} = {ratio}")
    assert not failures, f"{theme} ships text below AA: {failures}"


@pytest.mark.parametrize("theme", ["dark", "emerald-blue", "emerald-green"])
def test_body_text_on_the_dark_themes_reaches_aaa(theme):
    """The dark themes have the headroom, so they should use it."""
    palette = themes()[theme]
    assert contrast(palette["text"], palette["bg"]) >= AAA
    assert contrast(palette["muted"], palette["bg"]) >= AAA


def test_secondary_text_survives_being_faded():
    """--muted was #8b9bb4: 5.76 on --bg, but 4.02 once opacity:.8 is applied,
    and the pages do fade it. Every theme must still clear AA at .8."""
    for name, palette in themes().items():
        fg, bg = palette["muted"].lstrip("#"), palette["bg"].lstrip("#")
        f = [int(fg[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(bg[i:i + 2], 16) for i in (0, 2, 4)]
        faded = "#%02x%02x%02x" % tuple(round(f[i] * 0.8 + b[i] * 0.2) for i in range(3))
        assert contrast(faded, palette["bg"]) >= AA, f"{name} muted fails when faded"


def test_the_light_theme_really_is_light():
    """A light theme whose surfaces are darker than its text is a broken invert."""
    palette = themes()["light-beige"]
    assert _luminance(palette["bg"]) > _luminance(palette["text"])
    for other in ("dark", "emerald-blue", "emerald-green"):
        p = themes()[other]
        assert _luminance(p["bg"]) < _luminance(p["text"])


# ------------------------------------------------------------------- wiring
def _pages():
    return sorted(DOCS.glob("*.html"))


def test_no_page_defines_its_own_palette_any_more():
    """22 pages x 3 divergent :root blocks is how the drift started."""
    offenders = [p.name for p in _pages()
                 if re.search(r":root\s*\{[^}]*--bg\s*:", p.read_text(encoding="utf-8"))]
    assert not offenders, f"pages redefining the palette: {offenders}"


def test_every_page_links_the_shared_theme():
    missing = [p.name for p in _pages()
               if "vendor/theme.css" not in p.read_text(encoding="utf-8")]
    assert not missing, f"pages not linked to the theme: {missing}"


def test_the_theme_loader_is_not_deferred():
    """A deferred load paints the default palette first, then repaints — which
    reads as a flash on every navigation of a multi-page static site."""
    for p in _pages():
        text = p.read_text(encoding="utf-8")
        m = re.search(r"<script[^>]*vendor/theme\.js[^>]*>", text)
        assert m, f"{p.name} does not load theme.js"
        assert "defer" not in m.group(0) and "async" not in m.group(0), \
            f"{p.name} loads theme.js deferred, which will flash"


def test_aliases_exist_for_the_names_fpi_uses():
    """fpi.html had grown --accent/--pos/--neg/--grid/--warn of its own; the
    shared file must keep serving them or that page loses every colour."""
    palette_css = THEME_CSS.read_text(encoding="utf-8")
    for alias in ("--accent", "--accent-2", "--border", "--grid",
                  "--pos", "--neg", "--warn", "--pos-soft", "--neg-soft"):
        assert f"{alias}:" in palette_css, f"missing alias {alias}"


def test_small_type_is_lifted_rather_than_guessed_at():
    """81 selectors set 9-12px text. The overrides are generated from the page
    stylesheets; a hand-written list reached about a third of them."""
    css = THEME_CSS.read_text(encoding="utf-8")
    assert css.count(".t-legible ") >= 60
    assert "font-size:12.5px" in css
    # the buggy form must not come back: 1em resolves against the PARENT, so a
    # 10px label inside 14px body text jumps to 14px instead of to the floor
    assert "max(var(--fs-floor), 1em)" not in css


def test_theme_js_persists_and_defaults_safely():
    js = THEME_JS.read_text(encoding="utf-8")
    assert "scanx.theme.v1" in js
    for theme in ("emerald-blue", "emerald-green", "light-beige"):
        assert theme in js, f"{theme} missing from the picker"
    # storage can throw in private mode; the page must still render
    assert "catch" in js


# ------------------------------------------------- evidence text, not chrome
def test_every_theme_declares_the_evidence_tier():
    for name, palette in themes().items():
        assert "evidence" in palette, f"{name} has no --evidence"


def test_evidence_text_is_brighter_than_muted_on_every_theme():
    """The score component notes, the why-invest evidence lines and the SWOT
    evidence carry the NUMBERS behind every claim on the page. They were styled
    var(--muted) at 11.5px, which is de-emphasised chrome styling applied to the
    substance. --evidence sits close to body text instead."""
    for name, palette in themes().items():
        for surface in ("panel", "panel2", "chip"):
            ev = contrast(palette["evidence"], palette[surface])
            mu = contrast(palette["muted"], palette[surface])
            assert ev > mu, f"{name}: evidence ({ev}) is not brighter than muted ({mu})"
            assert ev >= AAA, f"{name}: evidence on {surface} is only {ev}"


def test_the_evidence_rules_outrank_the_page_stylesheets():
    """theme.css is linked BEFORE each page's <style> so page layout rules win.
    That also means a bare `.cnote` here LOSES to the page's own
    .cnote{color:var(--muted)} — which is exactly what happened first time.
    The :root prefix makes it (0,2,0) against the page's (0,1,0)."""
    # Comments must be stripped FIRST. The note above the rule explains that the
    # colour wins "without !important", and a whole-file substring check reads
    # that sentence as a use of it — which is how this test first failed.
    css = re.sub(r"/\*.*?\*/", "", THEME_CSS.read_text(encoding="utf-8"), flags=re.S)
    block = re.search(r"([^{}]*\.cnote[^{]*)\{\s*color:\s*var\(--evidence\)", css)
    assert block, "no --evidence colour rule for .cnote"
    assert ":root .cnote" in block.group(1), \
        "the rule must be :root-prefixed or the page stylesheet wins"
    assert "!important" not in css, "specificity should do this, not !important"
