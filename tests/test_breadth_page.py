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


# ------------------------------------- discoverability, not just reachability
def test_home_and_breadth_sit_above_the_first_collapsing_group():
    """A link that exists but is hidden is not a feature.

    nav.js groups every .nav-i that FOLLOWS a .grpL heading, and groups start
    COLLAPSED by request. Market Breadth was added inside "Market Pulse" (the 4th
    group) and the route home was inside "Screens", so both shipped live and
    invisible — reported as "I could not find this on the site". Anything above
    the first heading is never grouped, so it can never be collapsed shut.
    """
    problems = []
    for p in _pages():
        text = p.read_text(encoding="utf-8")
        first_group = text.find('class="grpL"')
        for label, needle in (("Home", 'href="index.html"><span class="ic">\U0001F3E0'),
                              ("Market Breadth", 'href="breadth.html"')):
            at = text.find(needle)
            if at < 0:
                problems.append(f"{p.name}: {label} missing")
            elif 0 <= first_group < at:
                problems.append(f"{p.name}: {label} is inside a collapsing group")
    assert not problems, "hidden nav items:\n  " + "\n  ".join(problems)


def test_the_breadth_link_is_not_duplicated():
    """It was moved out of the group, not copied — two entries is confusing."""
    for p in _pages():
        assert p.read_text(encoding="utf-8").count('href="breadth.html"') == 1, \
            f"{p.name} links breadth.html more than once"


def test_nav_js_only_groups_items_after_a_heading():
    """The guarantee the fix above relies on. If nav.js ever starts grouping
    items that PRECEDE the first .grpL, both links silently become hidden again."""
    js = (DOCS / "vendor" / "nav.js").read_text(encoding="utf-8")
    assert re.search(r"else\s+if\s*\(\s*cur\s*&&", js), \
        "nav.js must only collect nav-i into a group once a heading has been seen"


# ------------------------------------------------------- interaction contract
def test_the_field_can_be_driven_by_hand():
    """Reported as "not animating or cannot move using mouse".

    Two causes. The engine had no drag at all — only a slow auto-rotation — and
    that rotation was disabled whenever the OS asked for reduced motion, which
    Windows sets by default often enough that most people saw a frozen picture.
    """
    js = ENGINE.read_text(encoding="utf-8")
    for handler in ("pointerdown", "pointermove", "pointerup", "wheel", "keydown"):
        assert handler in js, f"no {handler} handler — the field cannot be driven"
    assert "setPointerCapture" in js, "a drag must survive leaving the canvas"
    assert "touchAction" in js, "a touchscreen must be able to drag it too"


def test_dragging_is_never_blocked_by_the_reduced_motion_preference():
    """A rotation the user performs with their own hand is not motion imposed on
    them. Only the AUTO spin may consult the preference."""
    js = ENGINE.read_text(encoding="utf-8")
    drag = js[js.index("pointermove"):js.index("pointermove") + 900]
    assert "reduced" not in drag, "the drag path must not consult prefers-reduced-motion"


def test_auto_spin_is_pausable_and_the_choice_persists():
    """WCAG 2.2.2 asks that motion over five seconds be pausable, not that it
    never start. So it starts, and one click stops it for good."""
    js = ENGINE.read_text(encoding="utf-8")
    assert "scanx.breadth.spin" in js, "the spin choice is not persisted"
    assert "spin:" in js and "spinning:" in js, "no pause control is exposed"
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="spinBtn"' in html and 'id="resetBtn"' in html


def test_a_drag_is_not_mistaken_for_a_click_on_a_particle():
    """Releasing a drag over a particle must not navigate to that company."""
    js = ENGINE.read_text(encoding="utf-8")
    click = js[js.index('addEventListener("click"'):]
    assert "!moved" in click[:260], "click handler does not exclude drags"


def test_the_loop_settles_instead_of_redrawing_a_static_picture():
    """With auto-spin off and no momentum left there is nothing to animate, and
    holding a rAF loop open to redraw identical frames burns a core."""
    js = ENGINE.read_text(encoding="utf-8")
    assert re.search(r"if\s*\(\s*autoSpin\s*\|\|\s*dragging\s*\|\|\s*velY\s*\|\|\s*velX\s*\)", js)


# ----------------------------------------------------------- DMA participation
def test_above_all_dma_is_counted_per_stock_not_derived():
    """It CANNOT be derived from the three independent figures: a market can be
    75% above its 20 DMA and 33% above its 200 with only 33% above both. The
    bake counts it in the same pass, per stock."""
    src = (ROOT / "scripts" / "refresh_marketmood.py").read_text(encoding="utf-8")
    assert "pct_above_all_dma" in src and "pct_below_all_dma" in src
    assert "n_all_dma" in src, "the strict reading needs its own denominator"


def test_the_above_percentages_are_published_not_subtracted():
    """100-below is subtly wrong: the counts have different denominators, since a
    60-day-old listing has a 20 DMA but no 200 DMA."""
    src = (ROOT / "scripts" / "refresh_marketmood.py").read_text(encoding="utf-8")
    for key in ("pct_above_20dma", "pct_above_50dma", "pct_above_200dma"):
        assert key in src, f"{key} is not published by the bake"


def test_the_page_degrades_when_the_new_dma_fields_are_absent():
    """The published file predates these fields, so the page must still render
    the three it does have rather than showing an empty panel."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'pct_above_' in html and 'pct_below_' in html
    assert "pending" in html, "no honest placeholder for the not-yet-baked figure"


# ------------------------------------------------------------ breadth calendar
def test_the_calendar_sits_between_participation_and_sectors():
    """Requested position: above Sectors, below Participation."""
    html = PAGE.read_text(encoding="utf-8")
    part = html.index("Participation \u2014 stocks above")
    cal = html.index("Breadth calendar")
    secs = html.index("Sectors and their top movers")
    assert part < cal < secs, "the calendar is in the wrong place on the page"


def test_the_calendar_is_collapsible_and_starts_shut():
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="calToggle"' in html and 'id="calBody"' in html
    assert 'aria-expanded="false"' in html, "it must start collapsed"
    assert "hidden" in html.split('id="calBody"')[1][:40]
    # reachable without a mouse
    assert 'tabindex="0"' in html and "onkeydown" in html


def test_the_calendar_shows_todays_session_before_the_bake_folds_it_in():
    """marketmood keeps `latest` separate from `history`, so a naive read of
    history alone would miss the session the rest of the page is describing."""
    html = PAGE.read_text(encoding="utf-8")
    assert "mood.latest.date" in html and "hist.push(mood.latest)" in html


def test_the_calendar_is_ordered_newest_first():
    html = PAGE.read_text(encoding="utf-8")
    assert "localeCompare" in html and "b.date" in html


# --------------------------------------------------- price coverage of the universe
def test_the_quote_window_is_wide_enough_for_illiquid_scrips():
    """period="2d" returns at most two rows and the caller needs two CONSECUTIVE
    closes, which an illiquid BSE scrip does not have because it did not trade
    yesterday. Measured effect: BSE numeric codes priced 4 of 2,457 (0%) while
    NSE symbols priced 97%."""
    # Strip comments first: the note explaining the fix quotes the old value, and
    # a whole-file substring check reads that explanation as the bug itself.
    # (Same trap caught the !important assertion in test_theme_contrast.)
    src = (ROOT / "scripts" / "refresh_quotes.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert 'period="2d"' not in code, "the 2-day window silently drops illiquid names"
    assert 'period="1mo"' in code


def test_a_single_traded_close_yields_a_price_but_no_invented_move():
    """One close in the window is a real price and worth showing; a percentage
    move needs two, and reporting 0% would be a fabrication."""
    src = (ROOT / "scripts" / "refresh_quotes.py").read_text(encoding="utf-8")
    tail = src[src.index("elif len(ser) == 1"):]
    assert '"ltp"' in tail[:400] and '"pct"' not in tail[:400]


def test_the_bse_instrument_master_is_configured():
    """Screener identifies BSE-only listings by numeric scrip code, and 2,457 of
    5,488 companies are such codes. With only the NSE master loaded they can
    never resolve to an Upstox instrument key."""
    cfg = (ROOT / "upstox_lab" / "config.py").read_text(encoding="utf-8")
    assert "DEFAULT_BSE_INSTRUMENTS_URL" in cfg
    assert "exchange/BSE.json.gz" in cfg
    assert "bse_instruments_url" in cfg
