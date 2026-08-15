"""The board's universe.

The bug these cover shipped: the board took its universe from a screen scrape
alone, so 178 companies we held complete bundles for -- Gujarat Gas at 26,710
Cr among them -- never appeared on a 5,183-row board. The screen is a source
of the universe now, not the definition of it.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.boarduniverse import (  # noqa: E402
    dead_inputs, enrichment_of, fii_change, merge, row_from_bundle,
    rows_from_bundles,
)


def _bundle(name="A", mcap="₹ 1,000 Cr.", price="₹ 250", pe="12.5",
            roce="18.0 %", np_series=None, sales_series=None, feed_pe=None):
    rows = {}
    if np_series is not None:
        rows["Net Profit +"] = list(np_series)
    if sales_series is not None:
        rows["Sales +"] = list(sales_series)
    bundle = {"fundamental": {
        "name": name,
        "overview": {"Market Cap": mcap, "Current Price": price,
                     "Stock P/E": pe, "ROCE": roce},
        "quarters": {"headers": ["a", "b", "c", "d", "e"], "rows": rows},
    }}
    if feed_pe is not None:
        bundle["upstox_ratios"] = {"pe": {"value": feed_pe}}
    return bundle


# ------------------------------------------------------- shape of a row

def test_a_held_bundle_becomes_a_screen_shaped_row():
    row = row_from_bundle("GUJGASLTD", _bundle("Gujarat Gas Ltd"))
    assert row["code"] == "GUJGASLTD"
    assert row["name"] == "Gujarat Gas Ltd"
    assert row["mcap"] == 1000.0
    assert row["cmp"] == 250.0
    assert row["roce"] == 18.0


def test_growth_columns_are_year_on_year():
    """The screen's Sales var / Profit var are year-on-year. A held row that
    measured against the previous quarter would score the season instead, and
    the two sources would silently disagree on the same column."""
    row = row_from_bundle("X", _bundle(np_series=[100, 5, 5, 5, 150],
                                       sales_series=[200, 9, 9, 9, 250]))
    assert row["profit_var"] == 50.0
    assert row["sales_var"] == 25.0


def test_growth_is_absent_rather_than_zero_when_history_is_short():
    row = row_from_bundle("X", _bundle(np_series=[5, 5, 150]))
    assert row["profit_var"] is None, "a short history scored as flat growth"


def test_the_feed_leads_on_price_to_earnings():
    row = row_from_bundle("X", _bundle(pe="99", feed_pe="21.4"))
    assert row["pe"] == 21.4


def test_a_high_multiple_is_carried_not_clipped():
    """The industry roll-up drops a 900x multiple so it cannot drag a median.
    A company that really trades at 900x still has to say so on its own row."""
    row = row_from_bundle("X", _bundle(pe="900"))
    assert row["pe"] == 900.0


# --------------------------------------------------- refusing to guess

def test_a_bundle_with_no_market_cap_is_not_admitted():
    assert row_from_bundle("X", _bundle(mcap="")) is None


def test_a_bundle_with_no_price_is_not_admitted():
    """It would otherwise reach the board as a row of blanks, which reads as
    data rather than as an absence."""
    assert row_from_bundle("X", _bundle(price="")) is None


def test_a_bundle_with_no_quarters_is_still_admitted():
    """Missing results cost it points; they are not grounds for hiding a
    company whose market cap and price are both known."""
    row = row_from_bundle("X", _bundle())
    assert row is not None and row["profit_var"] is None


def test_junk_never_raises():
    for junk in (None, {}, [], "x", 5, {"fundamental": []},
                 {"fundamental": {"overview": "no"}},
                 {"fundamental": {"quarters": "no"}}):
        assert row_from_bundle("X", junk) is None or isinstance(
            row_from_bundle("X", junk), dict)
    assert rows_from_bundles([]) == []
    assert rows_from_bundles([("A", None), ("B", "x")]) == []


# ----------------------------------------------------------- the union

def test_a_held_company_the_screen_missed_reaches_the_board():
    screen = [{"code": "AAA", "name": "A", "mcap": 10.0, "cmp": 5.0}]
    mine = rows_from_bundles([("GUJGASLTD", _bundle("Gujarat Gas Ltd"))])
    out = merge(screen, mine)
    assert [r["code"] for r in out] == ["AAA", "GUJGASLTD"]


def test_the_screen_wins_a_company_held_by_both():
    """Its price was fetched live this run; a bundle's may be a day old."""
    screen = [{"code": "AAA", "name": "A", "mcap": 10.0, "cmp": 5.0}]
    mine = rows_from_bundles([("AAA", _bundle("A", price="₹ 999"))])
    out = merge(screen, mine)
    assert len(out) == 1
    assert out[0]["cmp"] == 5.0, "a stale held price overwrote the live one"


def test_the_screens_ordering_survives():
    """A bake that adds bundles must not reshuffle rows already ranked."""
    screen = [{"code": c, "mcap": 1.0, "cmp": 1.0} for c in "CBA"]
    out = merge(screen, rows_from_bundles([("Z", _bundle())]))
    assert [r["code"] for r in out] == ["C", "B", "A", "Z"]


def test_a_dead_screen_still_produces_a_board():
    """The scrape failing used to empty the board. It should now fall back to
    everything we hold."""
    mine = rows_from_bundles([("AAA", _bundle()), ("BBB", _bundle())])
    assert len(merge([], mine)) == 2
    assert len(merge(None, mine)) == 2


def test_rows_without_a_code_are_dropped_rather_than_keyed_on_blank():
    assert merge([{"name": "no code"}], []) == []


# ------------------------------------------------- foreign institutions

def _holding(*fii):
    return {"fundamental": {"shareholding": {
        "headers": [f"Q{i}" for i in range(len(fii))],
        "rows": {"FIIs": list(fii), "Promoters": ["50%"] * len(fii)}}}}


def test_fii_change_is_percentage_points_not_a_ratio():
    """3.70% -> 3.98% is +0.28pp. As a ratio it would be +7.6%, which is not
    what a shareholding table means and is unstable off a small base."""
    assert fii_change(_holding("3.70%", "3.98%"))["fii_chg"] == 0.28


def test_fii_change_signs_an_exit():
    assert fii_change(_holding("8.00%", "6.50%"))["fii_chg"] == -1.5


def test_fii_change_needs_two_disclosures():
    assert fii_change(_holding("3.70%")) == {}
    assert fii_change(_holding()) == {}


def test_fii_change_survives_junk():
    for junk in (None, {}, [], "x", {"fundamental": {"shareholding": "no"}},
                 {"fundamental": {"shareholding": {"rows": "no"}}}):
        assert fii_change(junk) == {}


def test_enrichment_carries_both_price_and_holding():
    bundle = dict(_holding("3.70%", "3.98%"))
    bundle["prices"] = {"ok": True, "technical": {"rs_rating": 61, "pos_52w": 44.0}}
    out = enrichment_of(bundle)
    assert out == {"rs_rating": 61.0, "pos_52w": 44.0, "fii_chg": 0.28}


# ------------------------------------------- the guard against the class
# Three scoring inputs reached production contributing nothing, and not one
# failed a bake. These pin the check that refuses to publish a fourth.

def _rows(n=200, **fixed):
    return [{"code": str(i), "profit_var": i, "sales_var": i, "roce": i,
             "pe": i, "rs_rating": i % 100, "fii_chg": i / 10.0, **fixed}
            for i in range(n)]


def test_a_healthy_board_reports_no_dead_inputs():
    assert dead_inputs(_rows()) == {}


def test_an_input_null_market_wide_is_caught():
    """fii_chg, exactly as it shipped."""
    dead = dead_inputs(_rows(fii_chg=None))
    assert "fii_chg" in dead and "null on all" in dead["fii_chg"]


def test_an_input_constant_market_wide_is_caught():
    """momentum's rs_rating, had it arrived as one value for everyone."""
    dead = dead_inputs(_rows(rs_rating=50))
    assert "rs_rating" in dead and "single value" in dead["rs_rating"]


def test_several_dead_inputs_are_all_named_not_just_the_first():
    dead = dead_inputs(_rows(fii_chg=None, roce=None))
    assert {"fii_chg", "roce"} <= set(dead)


def test_a_small_run_is_not_judged():
    """A handful of rows can legitimately share a value; a partial run must
    not look like a broken feed."""
    assert dead_inputs(_rows(3, fii_chg=None)) == {}


def test_the_guard_reads_the_inputs_the_score_actually_uses():
    import inspect

    from earnings_intel.data import signal as sg
    from earnings_intel.data.boarduniverse import SCORED_INPUTS
    src = inspect.getsource(sg.board_signal)
    for field in SCORED_INPUTS:
        assert f'"{field}"' in src, f"{field} is guarded but never scored"


def test_the_baker_refuses_to_publish_a_dead_input():
    """A guard that cannot stop the bake is not a guard. main() must return
    non-zero AND the entrypoint must propagate it."""
    src = (ROOT / "scripts" / "refresh_technofunda.py").read_text(encoding="utf-8")
    assert "dead_inputs(" in src, "the bake no longer checks"
    assert "raise SystemExit(main())" in src, "the exit code is swallowed"


# ------------------------------------------------------------ the baker

def test_the_baker_unions_rather_than_replacing_the_screen():
    """Guards the actual defect: if universe() is ever wired straight into the
    filter again, the held companies vanish and nobody notices for weeks."""
    src = (ROOT / "scripts" / "refresh_technofunda.py").read_text(encoding="utf-8")
    assert "bu.merge(" in src, "the baker no longer unions held bundles"
    assert "held(args.fundamental)" in src


def test_the_real_bundles_produce_more_rows_than_the_baked_board():
    """Integration: the board on disk must not be smaller than what we hold.

    Only meaningful once the pipeline has actually run WITH the union. The
    board is a generated artefact committed by CI, so between this code landing
    and the next bake it legitimately lags -- asserting then would just paint
    the suite red for a data-freshness reason and train everyone to ignore it.
    `no_rs` in the meta is the marker of a post-fix bake.
    """
    fdir = ROOT / "docs" / "data" / "fundamental"
    board = ROOT / "docs" / "data" / "technofunda.json"
    meta = ROOT / "docs" / "data" / "technofunda_meta.json"
    if not fdir.exists() or not board.exists() or not meta.exists():
        pytest.skip("no baked board in this checkout")
    if "no_rs" not in json.loads(meta.read_text(encoding="utf-8")):
        pytest.skip("board predates the union; re-bake to enforce this")
    rows = json.loads(board.read_text(encoding="utf-8"))
    on_board = {r["code"] for r in rows}
    floor = 5.0
    missing = []
    for path in sorted(fdir.glob("*.json")):
        if path.stem == "index" or path.stem in on_board:
            continue
        try:
            row = row_from_bundle(path.stem, json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
        if row and (row["mcap"] or 0) >= floor and (row["cmp"] or 0) >= 1:
            missing.append(path.stem)
    assert not missing, (
        f"{len(missing)} scoreable companies are absent from the board "
        f"(e.g. {missing[:5]}) -- re-run scripts/refresh_technofunda.py")
