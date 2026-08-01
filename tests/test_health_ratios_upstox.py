"""analytics.health_ratios(..., extra=...) — the Upstox fill-ins for the board.

Screener's compact statements can't produce a current ratio or (for many names)
a debt/equity, so the board showed "n/a". These tests pin the fallback: extra
fills ONLY what Screener could not, the debt/equity fill-in is labelled a proxy,
the six key ratios land under "peers" with the right bias direction, and every
partial/junk shape degrades instead of raising. No network, no API key.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import analytics as A  # noqa: E402
from earnings_intel.data import ratios as R     # noqa: E402

import pytest

HDRS = ["Mar 2024", "Mar 2025", "Mar 2026"]


def _stmt(rows):
    return {"headers": HDRS, "rows": rows}


def _compact():
    """A Screener bundle with NO current split and no borrowings — the "n/a" case."""
    return (_stmt({"Other Assets": ["1", "2", "3"], "Total Assets": ["100", "110", "120"]}),
            _stmt({}), _stmt({}))


def _extra(**over):
    base = {
        "current_ratio": {"value": 2.4, "period": "2026-03-31",
                          "source": R.SOURCE_BALANCE_SHEET,
                          "inputs": {"current_assets": 240.0, "current_liabilities": 100.0}},
        "debt_equity_proxy": {"value": 0.2, "period": "2026-03-31", "proxy": True,
                              "source": R.SOURCE_BALANCE_SHEET,
                              "inputs": {"non_current_liabilities": 20.0,
                                         "equity_capital": 100.0}},
        "_meta": {"isin": "INE002A01018", "fetched": ["key-ratios", "balance-sheet"]},
    }
    base.update(over)
    return base


def _peer(value, sector, unit="x"):
    return {"value": value, "unit": unit, "sector": sector, "source": R.SOURCE_KEY_RATIOS}


# ----------------------------------------------------------- unchanged today
def test_no_extra_is_exactly_todays_output():
    bs, cf, pl = _compact()
    assert A.health_ratios(bs, cf, pl) == A.health_ratios(bs, cf, pl, extra=None)


def test_no_extra_still_reports_na_and_no_peers_key():
    h = A.health_ratios(*_compact())
    assert h["current_ratio"]["bias"] == "na" and h["current_ratio"]["value"] is None
    assert h["debt_equity"]["bias"] == "na"
    assert "peers" not in h


def test_empty_extra_changes_nothing():
    bs, cf, pl = _compact()
    assert A.health_ratios(bs, cf, pl, extra={}) == A.health_ratios(bs, cf, pl)


# --------------------------------------------------------------- fill-ins
def test_extra_fills_current_ratio():
    h = A.health_ratios(*_compact(), extra=_extra())["current_ratio"]
    assert h["value"] == 2.4
    assert h["bias"] == "positive"
    assert h["source"] == R.SOURCE_BALANCE_SHEET
    assert h["year"] == "2026-03-31"
    assert "2026-03-31" in h["note"]


def test_filled_current_ratio_keeps_the_ui_shape():
    h = A.health_ratios(*_compact(), extra=_extra())["current_ratio"]
    for key in ("value", "bias", "note"):
        assert key in h, key


def test_extra_fills_debt_equity_from_the_proxy():
    h = A.health_ratios(*_compact(), extra=_extra())["debt_equity"]
    assert h["value"] == 0.2
    assert h["bias"] == "positive"          # <= 0.30 is the conservative band
    assert h["source"] == R.SOURCE_BALANCE_SHEET


def test_debt_equity_fill_in_is_labelled_a_proxy():
    h = A.health_ratios(*_compact(), extra=_extra())["debt_equity"]
    assert h["proxy"] is True
    assert "PROXY" in h["note"]
    assert "not borrowings / net worth" in h["note"]


def test_current_ratio_bias_bands_from_extra():
    for value, bias in ((2.0, "positive"), (1.4, "neutral"), (0.8, "negative")):
        h = A.health_ratios(*_compact(),
                            extra={"current_ratio": {"value": value}})["current_ratio"]
        assert (h["value"], h["bias"]) == (value, bias)


def test_debt_equity_proxy_bias_bands_from_extra():
    for value, bias in ((0.3, "positive"), (0.9, "neutral"), (2.5, "negative")):
        h = A.health_ratios(*_compact(),
                            extra={"debt_equity_proxy": {"value": value}})["debt_equity"]
        assert (h["value"], h["bias"]) == (value, bias)


def test_sector_benchmark_is_carried_through_when_present():
    h = A.health_ratios(*_compact(),
                        extra={"current_ratio": {"value": 1.5, "sector": 1.9}})["current_ratio"]
    assert h["sector"] == 1.9


def test_no_sector_means_no_sector_key():
    assert "sector" not in A.health_ratios(*_compact(), extra=_extra())["current_ratio"]


# ------------------------------------------------- screener keeps precedence
def test_screener_current_ratio_wins_over_extra():
    h = A.health_ratios(
        _stmt({"Current Assets": ["90", "80", "70"], "Current Liabilities": ["100", "100", "100"]}),
        _stmt({}), _stmt({}), extra=_extra())["current_ratio"]
    assert h["value"] == 0.7                 # Screener's number, not the 2.4 fill-in
    assert "source" not in h


def test_screener_debt_equity_wins_over_the_proxy():
    h = A.health_ratios(
        _stmt({"Equity Capital": ["10", "10", "10"], "Reserves": ["40", "40", "40"],
               "Borrowings": ["30", "60", "120"]}),
        _stmt({}), _stmt({}), extra=_extra())["debt_equity"]
    assert h["value"] == 2.4
    assert "proxy" not in h


def test_non_positive_networth_finding_is_not_overwritten_by_the_proxy():
    # value is None here, but the "na" bias is what gates the fill-in: this is a
    # real finding (balance-sheet stress), not missing data.
    h = A.health_ratios(
        _stmt({"Equity Capital": ["10", "10", "10"], "Reserves": ["-5", "-15", "-25"],
               "Borrowings": ["30", "60", "120"]}),
        _stmt({}), _stmt({}), extra=_extra())["debt_equity"]
    assert h["bias"] == "negative" and h["value"] is None
    assert "proxy" not in h


def test_extra_does_not_touch_ocf_or_cwip():
    bs, cf, pl = _compact()
    plain, filled = A.health_ratios(bs, cf, pl), A.health_ratios(bs, cf, pl, extra=_extra())
    assert filled["ocf_np"] == plain["ocf_np"]
    assert filled["cwip"] == plain["cwip"]


# ------------------------------------------------------------------- peers
def test_peers_lower_is_better_beats_the_sector():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(12.0, 20.0)})
    assert h["peers"]["pe"]["bias"] == "positive"


def test_peers_lower_is_better_trailing_the_sector_is_negative():
    for key in ("pe", "pb", "ev_ebitda"):
        h = A.health_ratios(*_compact(), extra={key: _peer(30.0, 12.0)})
        assert h["peers"][key]["bias"] == "negative", key


def test_peers_higher_is_better_beats_the_sector():
    for key in ("roa", "roe", "roce"):
        h = A.health_ratios(*_compact(), extra={key: _peer(24.0, 16.0, "pct")})
        assert h["peers"][key]["bias"] == "positive", key


def test_peers_higher_is_better_trailing_the_sector_is_negative():
    h = A.health_ratios(*_compact(), extra={"roe": _peer(8.94, 16.46, "pct")})
    assert h["peers"]["roe"]["bias"] == "negative"


def test_peers_within_ten_percent_is_in_line():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(19.0, 20.0),
                                            "roe": _peer(21.0, 20.0, "pct")})
    assert h["peers"]["pe"]["bias"] == "neutral"
    assert h["peers"]["roe"]["bias"] == "neutral"


def test_peers_band_edge_is_in_line_not_a_verdict():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(18.0, 20.0)})   # exactly -10%
    assert h["peers"]["pe"]["bias"] == "neutral"


def test_peers_without_a_benchmark_is_na_but_still_shown():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(20.15, None)})
    assert h["peers"]["pe"]["bias"] == "na"
    assert h["peers"]["pe"]["value"] == 20.15


def test_peers_entry_shape():
    h = A.health_ratios(*_compact(), extra={"roe": _peer(8.94, 16.46, "pct")})["peers"]["roe"]
    assert h == {"value": 8.94, "unit": "pct", "sector": 16.46,
                 "source": R.SOURCE_KEY_RATIOS, "bias": "negative"}


def test_peers_negative_sector_still_ranks_higher_is_better():
    h = A.health_ratios(*_compact(), extra={"roe": _peer(5.0, -10.0, "pct")})
    assert h["peers"]["roe"]["bias"] == "positive"


def test_all_six_peers_surface_together():
    extra = {"pe": _peer(20.15, 12.46), "pb": _peer(2.0, 3.0),
             "roa": _peer(6.0, 5.0, "pct"), "roe": _peer(8.94, 16.46, "pct"),
             "roce": _peer(18.0, 14.0, "pct"), "ev_ebitda": _peer(9.0, 11.0)}
    peers = A.health_ratios(*_compact(), extra=extra)["peers"]
    assert set(peers) == {"pe", "pb", "roa", "roe", "roce", "ev_ebitda"}


def test_unknown_extra_keys_are_ignored():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(12.0, 20.0), "wibble": _peer(1, 2)})
    assert set(h["peers"]) == {"pe"}


# -------------------------------------------------- partial / junk degrades
def test_peers_key_absent_when_only_balance_sheet_ratios_arrived():
    assert "peers" not in A.health_ratios(*_compact(), extra=_extra())


def test_only_key_ratios_arrived_leaves_the_na_ratios_alone():
    h = A.health_ratios(*_compact(), extra={"pe": _peer(12.0, 20.0)})
    assert h["current_ratio"]["bias"] == "na"
    assert h["debt_equity"]["bias"] == "na"
    assert h["peers"]["pe"]["value"] == 12.0


def test_valueless_extra_entries_are_skipped():
    h = A.health_ratios(*_compact(), extra={"current_ratio": {"value": None},
                                            "debt_equity_proxy": {},
                                            "pe": {"unit": "x", "sector": 12.0}})
    assert h["current_ratio"]["bias"] == "na"
    assert h["debt_equity"]["bias"] == "na"
    assert "peers" not in h


def test_junk_extra_shapes_never_raise():
    for junk in (None, {}, [], "nope", 7, {"current_ratio": "1.5"},
                 {"current_ratio": {"value": "n/a"}}, {"pe": ["12"]},
                 {"pe": {"value": float("inf"), "sector": 2}},
                 {"debt_equity_proxy": {"value": True}}):
        h = A.health_ratios(*_compact(), extra=junk)
        assert h["current_ratio"]["bias"] == "na"
        assert "peers" not in h


def test_string_numbers_from_a_baked_bundle_still_parse():
    h = A.health_ratios(*_compact(),
                        extra={"current_ratio": {"value": "2.40", "period": "Mar 2026"}})
    assert h["current_ratio"]["value"] == 2.4
    assert h["current_ratio"]["year"] == "Mar 2026"


def test_missing_period_omits_the_year_without_breaking():
    h = A.health_ratios(*_compact(), extra={"current_ratio": {"value": 1.2}})["current_ratio"]
    assert "year" not in h
    assert h["value"] == 1.2 and h["bias"] == "neutral"


# ---------------------------------------------- end-to-end from ratios.py
def test_real_ratios_payloads_flow_into_the_health_panel():
    """The documented Upstox shapes, parsed by ratios.py, land on the board."""
    extra = R.ratios_from_key_ratios({"status": "success", "data": [
        {"name": "P/E", "company_value": "20.15", "sector_value": "12.46"},
        {"name": "ROE", "company_value": "8.94%", "sector_value": "16.46%"},
    ]})
    extra.update(R.ratios_from_balance_sheet({"status": "success", "data": {
        "type": "consolidated", "units_in": "crore",
        "full_statement": [
            {"particular": "Current Assets", "2026-03-31": 320.0},
            {"particular": "Current Liabilities", "2026-03-31": 270.0},
            {"particular": "Non-Current Liabilities", "2026-03-31": 40.0},
            {"particular": "Equity Capital", "2026-03-31": 200.0},
        ]}}))
    h = A.health_ratios(*_compact(), extra=extra)
    assert h["current_ratio"]["value"] == 1.19 and h["current_ratio"]["bias"] == "neutral"
    assert h["debt_equity"]["value"] == 0.2 and h["debt_equity"]["proxy"] is True
    assert h["peers"]["pe"]["bias"] == "negative"     # 20.15 vs a 12.46 sector: expensive
    assert h["peers"]["roe"]["bias"] == "negative"    # 8.94% vs 16.46%: trailing
    assert h["peers"]["roe"]["unit"] == "pct"


# --------------------------------------- the real balance-sheet payload shape
def _bs(rows):
    return {"data": {"full_statement": rows}}


def test_periods_nested_under_history_are_parsed():
    """The CI log said this on EVERY run, 2,847 times:

        balance-sheet: no period columns recognised;
        row keys seen: ['history', 'particular']

    Periods are nested inside `history`, not spread across the row as sibling
    keys. Rejecting "history" as a period NAME was only half the fix — without
    descending into it every balance sheet parsed to nothing, which is why
    current_ratio sat at 30 of 5,498 companies."""
    from earnings_intel.data.ratios import ratios_from_balance_sheet
    out = ratios_from_balance_sheet(_bs([
        {"particular": "Total Current Assets",
         "history": [{"period": "2026-03-31", "value": 320.0},
                     {"period": "2025-03-31", "value": 300.0}]},
        {"particular": "Total Current Liabilities",
         "history": [{"period": "2026-03-31", "value": 270.0}]},
    ]))
    assert out["current_ratio"]["value"] == pytest.approx(320 / 270, abs=0.01)
    assert out["current_ratio"]["period"] == "2026-03-31"


def test_a_particular_is_matched_on_the_wording_the_payload_actually_uses():
    """The constants say "current assets"; real statements say "Total Current
    Assets". An exact match found none of them."""
    from earnings_intel.data.ratios import ratios_from_balance_sheet
    out = ratios_from_balance_sheet(_bs([
        {"particular": "Total Current Assets", "history": [{"period": "2026-03-31", "value": 100.0}]},
        {"particular": "Total Current Liabilities", "history": [{"period": "2026-03-31", "value": 50.0}]},
    ]))
    assert out["current_ratio"]["value"] == pytest.approx(2.0)


def test_current_liabilities_is_not_confused_with_NON_current_liabilities():
    """"Total Non Current Liabilities" CONTAINS "current liabilities", so a
    naive substring match would compute a completely different ratio and publish
    it as the current ratio. Shortest containing match wins."""
    from earnings_intel.data.ratios import ratios_from_balance_sheet
    out = ratios_from_balance_sheet(_bs([
        {"particular": "Total Current Assets", "history": [{"period": "2026-03-31", "value": 320.0}]},
        {"particular": "Total Non Current Liabilities", "history": [{"period": "2026-03-31", "value": 90.0}]},
        {"particular": "Total Current Liabilities", "history": [{"period": "2026-03-31", "value": 270.0}]},
        {"particular": "Total Equity Capital", "history": [{"period": "2026-03-31", "value": 450.0}]},
    ]))
    assert out["current_ratio"]["value"] == pytest.approx(320 / 270, abs=0.01)
    assert out["debt_equity_proxy"]["value"] == pytest.approx(0.2, abs=0.01)


def test_the_sibling_key_shape_still_works():
    """Some payloads DO spread periods across the row; both shapes must parse."""
    from earnings_intel.data.ratios import ratios_from_balance_sheet
    out = ratios_from_balance_sheet(_bs([
        {"particular": "Current Assets", "2026-03-31": 200.0},
        {"particular": "Current Liabilities", "2026-03-31": 100.0},
    ]))
    assert out["current_ratio"]["value"] == pytest.approx(2.0)
