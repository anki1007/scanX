"""Two decimals, everywhere.

The board printed +930.5555555555555% and +78.33942005619498%. A growth rate
is a ratio of two reported figures, so it arrives with the full float tail,
and every page that wrote `${v}%` published all of it.

Two layers are pinned here because either alone is not enough. The bakers
round so the JSON does not carry meaningless digits (and is smaller for it);
the renderers cap so a board is safe whatever a feed hands it.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
PAGES = sorted(DOCS.glob("*.html"))


# ------------------------------------------------------------ the renderers

@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_page_interpolates_a_raw_value_into_a_percentage(page):
    """`${v}%` prints whatever precision the JSON held. Every one of these is
    a place a 15-digit number reached the screen."""
    html = page.read_text(encoding="utf-8")
    raw = re.findall(r"\$\{v\}%|\bv\s*\+\s*'%'", html)
    assert not raw, f"{page.name} publishes a raw value as a percentage: {raw[:3]}"


def test_the_formatter_caps_at_two_decimals_and_drops_a_dead_tail():
    """`(+(+v).toFixed(2)).toString()` -- toFixed alone would print 82.00 for
    a whole number, which is noise on a board of integers-as-percentages."""
    pages = [p for p in PAGES if "const fx=" in p.read_text(encoding="utf-8")]
    assert pages, "no page defines the formatter"
    for p in pages:
        html = p.read_text(encoding="utf-8")
        assert "toFixed(2)" in html, p.name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_a_page_that_uses_the_formatter_also_defines_it(page):
    """Calling fx() without defining it is a ReferenceError that empties the
    whole table, which is worse than the precision it was fixing."""
    html = page.read_text(encoding="utf-8")
    if "fx(" not in html:
        return
    assert "const fx=" in html, f"{page.name} calls fx() but never defines it"


# ----------------------------------------------------------------- the data

def _over_two_dp(value) -> bool:
    if not isinstance(value, float):
        return False
    text = repr(value)
    return "." in text and "e" not in text and len(text.split(".")[1]) > 2


def test_the_board_is_baked_with_two_decimals():
    """Skips on a fresh clone; the point is to catch a baker that stops
    rounding, not to require the data directory."""
    board = DOCS / "data" / "technofunda.json"
    if not board.exists():
        pytest.skip("no baked board in this checkout")
    rows = json.loads(board.read_text(encoding="utf-8"))
    bad = [(r.get("code"), k, v) for r in rows for k, v in r.items() if _over_two_dp(v)]
    assert not bad, f"{len(bad)} values carry more precision than the inputs justify: {bad[:4]}"


def test_the_universe_rounds_at_the_source():
    from earnings_intel.data.boarduniverse import row_from_bundle
    bundle = {"fundamental": {
        "name": "T", "overview": {"Market Cap": "1000", "Current Price": "250",
                                  "Stock P/E": "12.3456789", "ROCE": "18.987654 %"},
        "quarters": {"headers": list("abcde"),
                     "rows": {"Sales": [90, 1, 1, 1, 100],      # 11.111...%
                              "Net Profit": [9, 1, 1, 1, 100]}}}}
    row = row_from_bundle("T", bundle)
    for key in ("sales_var", "profit_var", "pe", "roce", "mcap", "cmp"):
        assert not _over_two_dp(row[key]), (key, row[key])
    assert row["sales_var"] == pytest.approx(11.11)


def test_rounding_never_invents_a_zero():
    """A missing growth rate must stay missing. round(None) would raise, and
    a default of 0.0 would read as "flat" on a board people screen with."""
    from earnings_intel.data.boarduniverse import row_from_bundle
    bundle = {"fundamental": {
        "name": "T", "overview": {"Market Cap": "1000", "Current Price": "250"},
        "quarters": {"headers": ["a"], "rows": {}}}}
    row = row_from_bundle("T", bundle)
    assert row["sales_var"] is None and row["profit_var"] is None
    assert row["pe"] is None and row["roce"] is None
