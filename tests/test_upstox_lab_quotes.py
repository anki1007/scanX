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


# --------------------------------------------------- BSE scrip-code resolution
def _write_master(path, records):
    import gzip, json
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(records, fh)


def _resolver(tmp_path, nse, bse):
    from upstox_lab.config import UpstoxLabSettings
    from upstox_lab.sync import InstrumentResolver
    nse_c = tmp_path / "instruments.json.gz"
    bse_c = tmp_path / "instruments_bse.json.gz"
    _write_master(nse_c, nse)
    _write_master(bse_c, bse)
    return InstrumentResolver(UpstoxLabSettings(instruments_cache=nse_c))


NSE_ROWS = [{"segment": "NSE_EQ", "trading_symbol": "RELIANCE",
             "isin": "INE002A01018", "exchange_token": "2885"}]
# In Upstox's BSE master the exchange_token IS the scrip code Screener uses.
BSE_ROWS = [{"segment": "BSE_EQ", "trading_symbol": "RELIANCE",
             "isin": "INE002A01018", "exchange_token": "500325"},
            {"segment": "BSE_EQ", "trading_symbol": "MOONGIPA",
             "isin": "INE651C01018", "exchange_token": "530167"}]


def test_a_bse_scrip_code_resolves_without_a_hand_written_mapping(tmp_path):
    """2,457 of 5,488 companies are BSE numeric codes. The resolver filtered
    segment != NSE_EQ and only ever downloaded the NSE master, so every one of
    them fell out — which is why the daily universe stopped at ~2,960 of 5,368
    and the logs carried an 'unresolved symbols' line on every run."""
    resolved, unresolved = _resolver(tmp_path, NSE_ROWS, BSE_ROWS).resolve(["530167"])
    assert resolved == {"530167": "INE651C01018"}
    assert unresolved == []


def test_nse_keeps_the_name_when_a_company_is_on_both_exchanges(tmp_path):
    """Same ISIN either way, but the NSE symbol is what the rest of scanX calls it."""
    resolved, _ = _resolver(tmp_path, NSE_ROWS, BSE_ROWS).resolve(["RELIANCE", "500325"])
    assert resolved["RELIANCE"] == "INE002A01018"
    assert resolved["500325"] == "INE002A01018"


def test_a_genuinely_unknown_code_is_still_reported_unresolved(tmp_path):
    """Widening the search must not turn a delisted scrip into a silent pass."""
    resolved, unresolved = _resolver(tmp_path, NSE_ROWS, BSE_ROWS).resolve(["999999"])
    assert resolved == {} and unresolved == ["999999"]


def test_a_missing_bse_master_degrades_to_nse_only(tmp_path):
    """The BSE download must never take the NSE path down with it."""
    from upstox_lab.config import UpstoxLabSettings
    from upstox_lab.sync import InstrumentResolver
    nse_c = tmp_path / "instruments.json.gz"
    _write_master(nse_c, NSE_ROWS)
    st = UpstoxLabSettings(instruments_cache=nse_c,
                           bse_instruments_url="http://127.0.0.1:9/nope.json.gz")
    resolved, unresolved = InstrumentResolver(st).resolve(["RELIANCE", "530167"])
    assert resolved.get("RELIANCE") == "INE002A01018"
    assert unresolved == ["530167"]


def test_the_quote_request_covers_both_segments():
    """Resolution was the only missing link — the market-quote call already asks
    for BSE_EQ alongside NSE_EQ."""
    from upstox_lab.quotes import SEGMENTS
    assert "NSE_EQ" in SEGMENTS and "BSE_EQ" in SEGMENTS
