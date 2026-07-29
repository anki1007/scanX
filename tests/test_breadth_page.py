"""Market Breadth page + the home link — wiring only, no browser required.

Two separate reports are covered here:

1. There was no way back to the board from any of the other 21 pages. The scanX
   wordmark was a <div>, and the only index.html link was the "PEAD Board" nav
   item, which sits inside a nav group that collapses by default — so the one
   route home was hidden behind a click nobody knew to make.

2. docs/breadth.html is new, and it computes everything client-side from files
   the daily refresh already publishes. These tests pin that contract: if a bake
   renames a key the page reads, this fails here rather than rendering an empty
   dashboard in production.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
DATA = DOCS / "data"
PAGE = DOCS / "breadth.html"
ENGINE = DOCS / "vendor" / "breadth3d.js"


def _pages():
    return sorted(DOCS.glob("*.html"))


# --------------------------------------------------------------- the way home
def test_every_page_has_a_clickable_home_wordmark():
    missing = [p.name for p in _pages()
               if not re.search(r'<a[^>]*class="brand"[^>]*href="index\.html"', p.read_text(encoding="utf-8"))]
    assert not missing, f"no home link on: {missing}"


def test_the_wordmark_is_a_link_not_a_div():
    """It was a <div>, which is why there was no way home."""
    for p in _pages():
        text = p.read_text(encoding="utf-8")
        assert not re.search(r'<div class="brand"', text), f"{p.name} still has a div wordmark"


def test_the_home_link_is_not_hidden_inside_a_collapsing_nav_group():
    """The old route home was the PEAD Board nav item, inside a group that
    nav.js collapses by default. The wordmark must sit OUTSIDE any group."""
    text = (DOCS / "technofunda.html").read_text(encoding="utf-8")
    brand = re.search(r'<a[^>]*class="brand"[^>]*>', text)
    assert brand
    before = text[:brand.start()]
    # a nav group opens with data-grp / class="grp"; none may be open at this point
    assert before.count('class="grp"') == before.count("</div>") or 'data-grp' not in before


def test_the_breadth_page_is_reachable_from_every_page():
    missing = [p.name for p in _pages() if 'href="breadth.html"' not in p.read_text(encoding="utf-8")]
    assert not missing, f"Market Breadth not linked from: {missing}"


# ------------------------------------------------------- the data the page reads
def test_the_page_only_reads_files_the_daily_refresh_publishes():
    html = PAGE.read_text(encoding="utf-8")
    wanted = set(re.findall(r'j\("data/([\w./-]+)"', html))
    assert wanted, "page fetches nothing"
    missing = [w for w in wanted if not (DATA / w).exists()]
    assert not missing, f"page fetches files that are not baked: {missing}"


def test_quotes_wide_still_carries_the_pct_field_the_page_plots():
    """The particle colour IS this field. If a bake renames it the dashboard
    renders an empty universe and looks merely quiet rather than broken."""
    q = json.loads((DATA / "quotes_wide.json").read_text(encoding="utf-8"))
    quotes = q.get("quotes")
    assert isinstance(quotes, dict) and quotes
    sample = next(iter(quotes.values()))
    assert "pct" in sample and "ltp" in sample


def test_sector_stocks_still_maps_sectors_to_constituents_with_codes():
    s = json.loads((DATA / "sector_stocks.json").read_text(encoding="utf-8"))
    sectors = s.get("sectors")
    assert isinstance(sectors, dict) and len(sectors) >= 10
    first = sectors[next(iter(sectors))]
    assert isinstance(first, list) and first
    assert {"code", "name"} <= set(first[0])


def test_marketmood_latest_breadth_keys_the_kpis_use():
    m = json.loads((DATA / "marketmood.json").read_text(encoding="utf-8"))
    latest = m.get("latest") or {}
    assert {"score", "label", "breadth"} <= set(latest)
    assert {"up", "down", "n"} <= set(latest["breadth"])


def test_the_join_actually_prices_a_meaningful_share_of_constituents():
    """The page joins sector constituents to quotes by code. If that join were
    mostly missing, the dashboard would silently show a tiny universe."""
    sectors = json.loads((DATA / "sector_stocks.json").read_text(encoding="utf-8"))["sectors"]
    quotes = json.loads((DATA / "quotes_wide.json").read_text(encoding="utf-8"))["quotes"]
    codes = {s["code"] for lst in sectors.values() for s in lst if isinstance(s, dict) and s.get("code")}
    priced = {c for c in codes if isinstance(quotes.get(c), dict)
              and isinstance(quotes[c].get("pct"), (int, float))}
    assert len(priced) >= 500, f"only {len(priced)} of {len(codes)} constituents priced"


# ------------------------------------------------------------------ the engine
def test_the_particle_engine_ships_with_no_external_dependency():
    """A static site with a strict offline story cannot pull a 3D library from a
    CDN, and a WebGL build would be ~600KB for a scatter canvas draws in 8KB."""
    js = ENGINE.read_text(encoding="utf-8")
    assert "import " not in js and "require(" not in js
    assert "//cdn" not in js and "https://" not in js
    assert len(js) < 40_000


def test_the_engine_respects_reduced_motion_and_stops_when_unseen():
    js = ENGINE.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in js
    assert "visibilitychange" in js
    assert "IntersectionObserver" in js


def test_the_engine_reads_theme_colours_rather_than_hardcoding_them():
    """It must follow the four themes, including the light one."""
    js = ENGINE.read_text(encoding="utf-8")
    assert "--green" in js and "--red" in js and "getPropertyValue" in js


def test_the_page_escapes_everything_it_interpolates():
    """Company names land in innerHTML; they come from a scrape."""
    html = PAGE.read_text(encoding="utf-8")
    assert "function esc(" in html
    assert "encodeURIComponent" in html


def test_sampling_preserves_the_advancing_ratio_not_just_the_count():
    """The engine caps particles for frame rate. A naive slice() of a list that
    happens to be sorted by move would draw an all-green or all-red sphere — a
    picture that contradicts the number printed under it."""
    js = ENGINE.read_text(encoding="utf-8")
    assert "upKeep" in js and "downKeep" in js
    # the ratio must be derived from the real split, not assumed 50/50
    assert re.search(r"upKeep\s*=\s*Math\.round\(\s*keep\s*\*\s*\(\s*up\.length\s*/\s*total", js)
