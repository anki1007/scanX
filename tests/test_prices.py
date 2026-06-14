"""Tests for live-price wiring: Dhan symbol/numeric resolution + dashboard enrichment."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data.dhan_provider import DhanProvider, normalize_quote_payload  # noqa: E402
import refresh_scanx as rs  # noqa: E402


def _provider_no_net():
    """DhanProvider with by_key injected (no __init__, no network)."""
    p = DhanProvider.__new__(DhanProvider)
    p._by_key = {"NSE:INFY": 111}
    return p


def test_resolve_symbol_and_numeric():
    p = _provider_no_net()
    assert p._resolve("NSE:INFY") == 111
    assert p._resolve("nse:infy") == 111            # case-insensitive
    assert p._resolve("BSE:543212") == 543212       # numeric BSE scrip code == Dhan id
    assert p._resolve("NSE:NOPE") is None


def test_normalize_quote_payload():
    payload = {"NSE_EQ": {"111": {"last_price": 100.5, "net_change": 0.5,
                                  "volume": 1000, "average_price": 100.0,
                                  "ohlc": {"open": 99, "high": 101, "low": 98, "close": 100}}}}
    out = normalize_quote_payload(payload, {("NSE_EQ", "111"): "NSE:INFY"})
    assert out["NSE:INFY"]["last_price"] == 100.5
    assert out["NSE:INFY"]["ohlc"]["close"] == 100


class FakeProv:
    def __init__(self, quotes):
        self.quotes = quotes

    def get_quotes(self, keys, **kw):
        return {k: self.quotes[k] for k in keys if k in self.quotes}


def test_enrich_prices_nse_bse_and_miss():
    quotes = {
        "NSE:INFY": {"last_price": 110.0, "net_change": 10.0, "ohlc": {"close": 100.0}},
        "BSE:543212": {"last_price": 49.5, "net_change": -0.5, "ohlc": {"close": 50.0}},
    }
    rows = [{"code": "INFY"}, {"code": "543212"}, {"code": "NOMATCH"}]
    note = rs.enrich_prices(rows, provider=FakeProv(quotes))
    assert rows[0]["ltp"] == 110.0 and rows[0]["pct_change"] == 10.0   # +10%
    assert rows[1]["ltp"] == 49.5 and rows[1]["pct_change"] == -1.0    # -1%
    assert rows[2]["ltp"] is None and rows[2]["pct_change"] is None    # unmatched
    assert "2/3" in note


def test_enrich_prices_skip_guard(monkeypatch):
    monkeypatch.setenv("SCANX_NO_PRICES", "1")
    rows = [{"code": "INFY"}]
    note = rs.enrich_prices(rows, provider=None)   # must NOT touch network
    assert rows[0]["ltp"] is None and rows[0]["pct_change"] is None
    assert "skipped" in note
