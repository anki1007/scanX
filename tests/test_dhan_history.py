import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from earnings_intel.data.dhan_provider import DhanProvider


def _prov():
    p = DhanProvider("cid", "tok"); p._by_key = {"NSE:RELIANCE": 2885}; return p


def test_resolve_id_numeric_is_bse():
    assert _prov().resolve_id("500325") == (500325, "BSE_EQ")


def test_resolve_id_symbol_is_nse():
    assert _prov().resolve_id("RELIANCE") == (2885, "NSE_EQ")


def test_resolve_id_unknown():
    assert DhanProvider("c", "t").resolve_id("NOPE_X")  # falls to master load (no net) -> (None,None) tolerated


def test_historical_parses(monkeypatch):
    p = _prov()
    class R:
        status_code = 200
        def json(self): return {"close": [1, 2, 3], "timestamp": [10, 20, 30]}
    p.s = types.SimpleNamespace(post=lambda *a, **k: R())
    assert p.historical(2885, "NSE_EQ")["close"] == [1, 2, 3]


def test_historical_http_error(monkeypatch):
    p = _prov()
    class R:
        status_code = 401
        def json(self): return {}
    p.s = types.SimpleNamespace(post=lambda *a, **k: R())
    assert p.historical(2885, "NSE_EQ") is None


def test_pricehist_series_from_dhan():
    pd = pytest.importorskip("pandas")
    from earnings_intel.data import pricehist as ph
    s = ph._series_from_dhan({"close": [float(i) for i in range(40)],
                              "timestamp": [1_700_000_000 + i * 86400 for i in range(40)]})
    assert s is not None and len(s) == 40 and str(s.index.dtype).startswith("datetime")
    assert ph._series_from_dhan({"close": [1, 2], "timestamp": [1]}) is None  # length mismatch


def test_pricehist_prefers_dhan(monkeypatch):
    pd = pytest.importorskip("pandas")
    from earnings_intel.data import pricehist as ph
    ser = pd.Series([10.0] * 60, index=pd.date_range("2024-01-01", periods=60))
    monkeypatch.setattr(ph, "_history_dhan", lambda c, o: (ser, "X (Dhan NSE)"))
    called = {"yf": False}
    def yf(c, o):
        called["yf"] = True; return (None, None)
    monkeypatch.setattr(ph, "_history_yf", yf)
    s, tk = ph._history("X", None)
    assert tk == "X (Dhan NSE)" and called["yf"] is False


def test_cooldown_breaker_stops_calls(monkeypatch, tmp_path):
    import types
    from earnings_intel.data import dhan_provider as dp
    monkeypatch.setattr(dp, "_BLOCK_FILE", tmp_path / "block.json")
    assert dp.dhan_cooldown_left() == 0
    dp._trip_cooldown(1800, "test 401")
    assert dp.dhan_cooldown_left() > 0
    p = dp.DhanProvider("c", "t")
    def boom(*a, **k):
        raise AssertionError("Dhan network called during cooldown!")
    p.s = types.SimpleNamespace(post=boom, get=boom)
    out = p.get_quotes(["NSE:RELIANCE"])
    assert out == {} and "paused" in (p.last_error or "")


def test_cooldown_clears_when_token_replaced(monkeypatch, tmp_path):
    from earnings_intel.data import dhan_provider as dp
    bf = tmp_path / "block.json"
    monkeypatch.setattr(dp, "_BLOCK_FILE", bf)
    # write a block whose recorded token_mtime won't match the current one
    import json, time
    bf.write_text(json.dumps({"until": time.time() + 999, "reason": "x", "token_mtime": -1}))
    assert dp.dhan_cooldown_left() == 0   # token "changed" -> block cleared
