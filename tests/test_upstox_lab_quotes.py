"""Upstox market-quote LTP parsing (upstox_lab/quotes.py) — no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upstox_lab.quotes import parse_quote_payload  # noqa: E402

MAP = {"INE749A01030": "PASUPTAC", "INE002A01018": "RELIANCE"}


def test_parses_last_price_and_pct_from_net_change():
    payload = {"status": "success", "data": {
        "NSE_EQ:PASUPTAC": {"instrument_token": "NSE_EQ|INE749A01030",
                            "last_price": 58.78, "net_change": 0.88,
                            "ohlc": {"close": 57.90}}}}
    assert parse_quote_payload(payload, MAP) == {"PASUPTAC": {"ltp": 58.78, "pct": 1.52}}


def test_falls_back_to_ohlc_close_when_net_change_absent():
    payload = {"data": {"NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018",
                                            "last_price": 1278.15,
                                            "ohlc": {"close": 1275.35}}}}
    assert parse_quote_payload(payload, MAP)["RELIANCE"]["pct"] == 0.22


def test_matches_by_isin_not_by_response_key():
    # Upstox may key the response by its own trading symbol; the ISIN wins
    payload = {"data": {"NSE_EQ:SOMETHING-ELSE": {"instrument_token": "NSE_EQ|INE002A01018",
                                                  "last_price": 100.0,
                                                  "ohlc": {"close": 100.0}}}}
    assert "RELIANCE" in parse_quote_payload(payload, MAP)


def test_untraded_segment_does_not_overwrite_a_real_price():
    payload = {"data": {
        "NSE_EQ:PASUPTAC": {"instrument_token": "NSE_EQ|INE749A01030",
                            "last_price": 58.78, "ohlc": {"close": 57.9}},
        "BSE_EQ:PASUPTAC": {"instrument_token": "BSE_EQ|INE749A01030",
                            "last_price": None},
    }}
    assert parse_quote_payload(payload, MAP)["PASUPTAC"]["ltp"] == 58.78


def test_unknown_isin_and_malformed_rows_are_skipped():
    payload = {"data": {
        "NSE_EQ:NOPE": {"instrument_token": "NSE_EQ|INE000X00000", "last_price": 5.0},
        "NSE_EQ:BAD": "not-a-dict",
        "NSE_EQ:NOPRICE": {"instrument_token": "NSE_EQ|INE002A01018"},
    }}
    assert parse_quote_payload(payload, MAP) == {}


def test_empty_and_missing_payloads_are_safe():
    assert parse_quote_payload({}, MAP) == {}
    assert parse_quote_payload({"data": {}}, MAP) == {}
    assert parse_quote_payload(None, MAP) == {}


def test_zero_base_does_not_divide_by_zero():
    payload = {"data": {"NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018",
                                            "last_price": 10.0, "ohlc": {"close": 0}}}}
    assert parse_quote_payload(payload, MAP)["RELIANCE"]["pct"] is None
