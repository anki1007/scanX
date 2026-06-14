import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import nsequotes as nq


def _nse_payload():
    return {"data": [
        {"symbol": "NIFTY TOTAL MARKET", "priority": 1, "lastPrice": 12345.0,
         "change": 10.0, "previousClose": 12335.0},
        {"symbol": "RELIANCE", "lastPrice": "1,258.80", "change": 12.4,
         "pChange": 1.0, "previousClose": "1,246.40"},
        {"symbol": "TCS", "lastPrice": 2153.9, "change": -10.1, "previousClose": 2164.0},
        {"symbol": "BROKEN", "lastPrice": None},
    ]}


def test_parse_nse_snapshot():
    out = nq.parse_nse_snapshot(_nse_payload())
    assert "NIFTY TOTAL MARKET" not in out          # index header row skipped
    assert "BROKEN" not in out                      # no price -> skipped
    assert out["RELIANCE"]["last_price"] == 1258.8  # comma string parsed
    assert out["RELIANCE"]["prev_close"] == 1246.4
    assert out["TCS"]["net_change"] == -10.1
    assert nq.parse_nse_snapshot({}) == {}


def test_parse_nse_quote_single():
    q = nq.parse_nse_quote({"priceInfo": {"lastPrice": 2635, "change": -49.05,
                                          "previousClose": 2684.05}})
    assert q["last_price"] == 2635 and q["prev_close"] == 2684.05
    assert nq.parse_nse_quote({"priceInfo": {}}) is None
    assert nq.parse_nse_quote({}) is None


def test_parse_bse_header_variants():
    q = nq.parse_bse_header({"Header": {"LTP": "123.45", "Chg": "1.20",
                                        "PrevClose": "122.25"}})
    assert q == {"last_price": 123.45, "net_change": 1.2, "prev_close": 122.25}
    # nested / renamed keys still found
    q2 = nq.parse_bse_header({"Header": {"CurrRate": {"LTP": "55.4"},
                                         "PClose": "54.0"}})
    assert q2["last_price"] == 55.4 and q2["prev_close"] == 54.0
    assert nq.parse_bse_header({"Header": {"noprice": "x"}}) is None
    assert nq.parse_bse_header({}) is None


def test_get_quotes_contract(monkeypatch):
    p = nq.NseBseProvider.__new__(nq.NseBseProvider)   # skip network __init__
    p.last_error = None
    p._nse1 = {}
    p._cooldown_until = 9e12                            # block per-symbol lookups
    p._bse = {"543212": {"ts": 9e12, "q": {"last_price": 99.0, "net_change": 1.0,
                                           "prev_close": 98.0}}}
    monkeypatch.setattr(p, "_nse_snapshot",
                        lambda: {"RELIANCE": {"last_price": 1258.8,
                                              "net_change": 12.4, "prev_close": 1246.4}})
    out = p.get_quotes(["NSE:RELIANCE", "BSE:543212", "NSE:UNKNOWN"])
    assert out["NSE:RELIANCE"]["last_price"] == 1258.8
    assert out["NSE:RELIANCE"]["ohlc"]["close"] == 1246.4
    assert out["BSE:543212"]["last_price"] == 99.0
    assert "NSE:UNKNOWN" not in out


def test_serve_pick_provider_is_nse_only(monkeypatch):
    import importlib
    srv = importlib.import_module("scripts.serve")
    sentinel = object()
    monkeypatch.setattr(nq, "provider", lambda: sentinel)
    assert srv._pick_provider() is sentinel
