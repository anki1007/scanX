"""Industry roll-ups.

The traps here are the ones that have already bitten this repo once each: a
mean dragged by one absurd multiple, a percentage change measured against the
wrong quarter, and a thin bucket published as though it were a benchmark.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.industries import (  # noqa: E402
    LEVELS, aggregate, summarise,
)


def _co(name, sector, industry, mcap, pe=None, np_series=None, opm=None):
    rows = {}
    if np_series:
        rows["Net Profit"] = list(np_series)
    if opm is not None:
        rows["OPM %"] = [opm]
    return {"fundamental": {
        "name": name,
        "classification": {"sector": sector, "industry": industry,
                           "group": industry, "subgroup": industry},
        "overview": {"Market Cap": f"₹ {mcap} Cr.", "Stock P/E": pe},
        "quarters": {"headers": ["a", "b", "c", "d", "e"], "rows": rows},
    }}


# ------------------------------------------------------------ aggregation

def test_industries_roll_up_and_sort_by_total_market_cap():
    cos = [summarise(_co("A", "Energy", "Oil & Gas", 1000, "20")),
           summarise(_co("B", "Energy", "Oil & Gas", 500, "30")),
           summarise(_co("C", "Financials", "Banks", 4000, "15"))]
    out = aggregate(cos, "industry")
    assert [r["name"] for r in out] == ["Banks", "Oil & Gas"]
    assert out[1]["members"] == 2
    assert out[1]["mcap"] == 1500.0


def test_the_median_is_used_not_the_mean():
    """One company on a 900x P/E drags a mean somewhere no member is. That
    exact mistake put four contradictory sector P/Es on the same day."""
    cos = [summarise(_co(n, "S", "I", 100, pe))
           for n, pe in (("a", "10"), ("b", "12"), ("c", "14"), ("d", "900"))]
    row = aggregate(cos, "industry")[0]
    # 900 is outside the sanity band, so the survivors are 10, 12, 14 and the
    # median of three values is the middle one. A MEAN of the same four would
    # be 234 -- a "typical" multiple no member of the industry trades on.
    assert row["pe"] == 12.0
    assert row["pe_n"] == 3, "the absurd multiple was counted"


def test_growth_is_year_on_year_not_quarter_on_quarter():
    """Most Indian businesses are seasonal: June against March measures the
    season, not the business. q[-1] vs q[-5] is what year-on-year means."""
    co = summarise(_co("A", "S", "I", 100, "10", np_series=[100, 5, 5, 5, 150]))
    assert co["np_growth"] == 50.0


def test_growth_needs_five_quarters():
    co = summarise(_co("A", "S", "I", 100, "10", np_series=[100, 5, 5, 150]))
    assert co["np_growth"] is None


def test_a_zero_base_does_not_produce_an_infinite_growth_rate():
    co = summarise(_co("A", "S", "I", 100, "10", np_series=[0, 1, 2, 3, 40]))
    assert co["np_growth"] is None


# ------------------------------------------------------------ honest gaps

def test_a_thin_industry_is_flagged_not_hidden():
    """Dropping it would remove coverage a reader cannot see is missing."""
    cos = [summarise(_co(n, "S", "Tiny", 100, "10")) for n in "ab"]
    row = aggregate(cos, "industry")[0]
    assert row["members"] == 2
    assert row["thin"] is True


def test_a_full_industry_is_not_flagged_thin():
    cos = [summarise(_co(str(i), "S", "Big", 100, "10")) for i in range(6)]
    assert aggregate(cos, "industry")[0]["thin"] is False


def test_a_company_with_no_classification_is_not_bucketed_under_blank():
    cos = [summarise({"fundamental": {"name": "X", "overview": {"Market Cap": "₹ 5 Cr."}}}),
           summarise(_co("A", "S", "I", 100, "10"))]
    out = aggregate(cos, "industry")
    assert [r["name"] for r in out] == ["I"]


def test_every_level_is_a_valid_grouping():
    cos = [summarise(_co("A", "Energy", "Oil & Gas", 100, "10"))]
    for level in LEVELS:
        out = aggregate(cos, level)
        assert len(out) == 1, f"{level} did not group"


def test_an_unknown_level_falls_back_to_industry_rather_than_returning_nothing():
    cos = [summarise(_co("A", "Energy", "Oil & Gas", 100, "10"))]
    assert aggregate(cos, "nonsense")[0]["name"] == "Oil & Gas"


def test_the_parent_sector_is_carried_for_context():
    cos = [summarise(_co("A", "Energy", "Oil & Gas", 100, "10")),
           summarise(_co("B", "Energy", "Oil & Gas", 200, "12"))]
    assert aggregate(cos, "industry")[0]["parent"] == "Energy"


def test_the_biggest_member_is_named():
    cos = [summarise(_co("Small", "S", "I", 10, "10")),
           summarise(_co("Huge", "S", "I", 9000, "12"))]
    assert aggregate(cos, "industry")[0]["top"] == "Huge"


# ---------------------------------------------------------------- hygiene

def test_junk_never_raises():
    for junk in (None, {}, [], "x", {"fundamental": []},
                 {"fundamental": {"quarters": "no"}},
                 {"fundamental": {"classification": "no"}}):
        summarise(junk)
    assert aggregate([None, "x", 5], "industry") == []
    assert aggregate([], "industry") == []


# ------------------------------------------------------------ the page

def test_the_page_exists_and_is_reachable_from_every_other_page():
    """A board nothing links to is a board nobody finds."""
    docs = ROOT / "docs"
    assert (docs / "industries.html").exists()
    pages = sorted(docs.glob("*.html"))
    missing = [p.name for p in pages
               if 'href="industries.html"' not in p.read_text(encoding="utf-8")]
    assert not missing, f"no nav link on: {missing}"


def test_the_page_offers_all_four_levels():
    html = (ROOT / "docs" / "industries.html").read_text(encoding="utf-8")
    for level in LEVELS:
        assert f'value="{level}"' in html, f"{level} is not selectable"


def test_the_page_says_the_figures_are_medians():
    """A reader comparing industries has to know a mean was not used."""
    html = (ROOT / "docs" / "industries.html").read_text(encoding="utf-8")
    assert "MEDIAN" in html
    assert "year on year" in html.lower()


def test_the_baker_is_scheduled():
    wf = "\n".join(p.read_text(encoding="utf-8")
                   for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert "refresh_industries.py" in wf


def test_the_default_level_matches_the_first_option():
    """The select and the table must agree on load. They are set in two places
    -- the first <option> and a JS variable -- so they can silently disagree,
    showing one level in the control while rendering another."""
    import re
    html = (ROOT / "docs" / "industries.html").read_text(encoding="utf-8")
    first = re.search(r'<select id="lvl".*?<option value="([a-z]+)"', html, re.S)
    js = re.search(r'LEVEL\s*=\s*"([a-z]+)"', html)
    assert first and js, "could not read the two defaults"
    assert first.group(1) == js.group(1), (
        f'select opens on "{first.group(1)}" but the table renders '
        f'"{js.group(1)}"')


def test_the_finest_level_leads():
    """"Industries overview" means the ~192-entry level. The 22-entry one is
    barely finer than the sector boards that already exist."""
    import re
    html = (ROOT / "docs" / "industries.html").read_text(encoding="utf-8")
    first = re.search(r'<select id="lvl".*?<option value="([a-z]+)"', html, re.S)
    assert first.group(1) == "subgroup"
