import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

pd = pytest.importorskip("pandas")
from earnings_intel.data import pricehist as ph


def _series():
    idx = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    # gentle uptrend with noise
    import math
    vals = [100 * (1.0003 ** i) * (1 + 0.02 * math.sin(i / 9)) for i in range(len(idx))]
    return pd.Series(vals, index=idx)


def test_price_analytics_computes(monkeypatch):
    monkeypatch.setattr(ph, "_history", lambda code, ov: (_series(), "TEST.NS"))
    d = ph.price_analytics("TEST", use_cache=False)
    assert d["ok"] and d["ticker"] == "TEST.NS"
    assert len(d["yearwise"]) >= 3
    assert d["heatmap"]["months"][0] == "Jan" and len(d["heatmap"]["rows"]) >= 3
    r = d["risk"]
    for k in ("avg_weekly", "weekly_std", "ann_vol", "max_drawdown", "pct_positive", "sharpe", "sortino"):
        assert k in r
    assert -100 <= r["max_drawdown"] <= 0


def test_price_analytics_no_history(monkeypatch):
    monkeypatch.setattr(ph, "_history", lambda code, ov: (None, None))
    assert ph.price_analytics("ZZZ", use_cache=False)["ok"] is False


def test_ticker_resolution():
    assert ph._tickers("500325", None) == ["500325.BO"]
    assert ph._tickers("RELIANCE", None) == ["RELIANCE.NS", "RELIANCE.BO"]
