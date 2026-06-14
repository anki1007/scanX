import sys, importlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
srv = importlib.import_module("scripts.serve")


import pytest


@pytest.fixture(autouse=True)
def _reset_quote_cache():
    srv._Q.update({"data": {}, "cooldown_until": 0.0, "last_err": None})
    yield
    srv._Q.update({"data": {}, "cooldown_until": 0.0, "last_err": None})


class _P:
    def __init__(self, q, err=None): self._q = q; self.last_error = err
    def get_quotes(self, keys): return self._q


def test_live_quotes_maps_ltp_and_pct(monkeypatch):
    monkeypatch.setattr(srv, "_pick_provider", lambda: _P({"NSE:RELIANCE": {"last_price": 1300, "net_change": 13,
                                                     "ohlc": {"close": 1287}}}))
    out = srv._live_quotes(["RELIANCE"])
    assert out["RELIANCE"]["ltp"] == 1300
    assert out["RELIANCE"]["pct"] == round((1300 - 1287) / 1287 * 100, 2)


def test_live_quotes_numeric_bse(monkeypatch):
    monkeypatch.setattr(srv, "_pick_provider", lambda: _P({"BSE:500325": {"last_price": 1290, "net_change": -10,
                                                   "ohlc": {"close": 1300}}}))
    out = srv._live_quotes(["500325"])
    assert out["500325"]["ltp"] == 1290 and out["500325"]["pct"] < 0


def test_live_quotes_no_provider(monkeypatch):
    monkeypatch.setattr(srv, "_pick_provider", lambda: None)
    from earnings_intel.data import nsequotes
    monkeypatch.setattr(nsequotes, "provider", lambda: None)
    assert "_error" in srv._live_quotes(["X"])


def test_live_quotes_surfaces_auth_error(monkeypatch):
    srv._Q.update({"data": {}, "cooldown_until": 0.0, "last_err": None})
    monkeypatch.setattr(srv, "_pick_provider", lambda: _P({}, err="HTTP 401: Authentication Failed"))
    out = srv._live_quotes(["RELIANCE"])
    # 401 is rewritten to an actionable message (dashboard truncates to 55 chars,
    # so the fix instruction must come first)
    assert "401" in out.get("_error", "")
    import time
    assert srv._Q["cooldown_until"] > time.time()   # breaker tripped



def test_live_quotes_trips_cooldown_on_429(monkeypatch):
    import time
    srv._Q.update({"data": {}, "cooldown_until": 0.0, "last_err": None})
    monkeypatch.setattr(srv, "_pick_provider", lambda: _P({}, err="HTTP 429: Too many requests"))
    out = srv._live_quotes(["RELIANCE"])
    assert out.get("_error", "").startswith("HTTP 429")
    assert srv._Q["cooldown_until"] > time.time()   # breaker tripped
    # next call must NOT hit Dhan (provider would raise if called)
    def boom():
        raise AssertionError("Dhan called during cooldown!")
    monkeypatch.setattr(srv.ph, "_dhan_provider", boom)
    out2 = srv._live_quotes(["RELIANCE"])
    assert "_error" in out2 and "_cooldown_s" in out2
    srv._Q["cooldown_until"] = 0.0   # reset for other tests
