import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

pd = pytest.importorskip("pandas")
from earnings_intel.data import pricehist as ph


def _volume():
    idx = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    # heavier a year ago, cooling into the last week
    vals = [1_000_000 - 400 * i for i in range(len(idx))]
    return pd.Series(vals, index=idx)


def _series():
    idx = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    # gentle uptrend with noise
    import math
    vals = [100 * (1.0003 ** i) * (1 + 0.02 * math.sin(i / 9)) for i in range(len(idx))]
    return pd.Series(vals, index=idx)


def test_price_analytics_computes(monkeypatch):
    monkeypatch.setattr(ph, "_history",
                        lambda code, ov: (_series(), _volume(), "TEST.NS"))
    d = ph.price_analytics("TEST", use_cache=False)
    assert d["ok"] and d["ticker"] == "TEST.NS"
    assert len(d["yearwise"]) >= 3
    assert d["heatmap"]["months"][0] == "Jan" and len(d["heatmap"]["rows"]) >= 3
    r = d["risk"]
    for k in ("avg_weekly", "weekly_std", "ann_vol", "max_drawdown", "pct_positive", "sharpe", "sortino"):
        assert k in r
    assert -100 <= r["max_drawdown"] <= 0

    # a pullback screen needs both of these, and neither can be derived from
    # the 3m/6m/12m returns that were here before
    tech = d["technical"]
    for k in ("ret_1w", "ret_1m", "vol_1w", "vol_1m", "vol_1y"):
        assert k in tech, k
    assert tech["vol_1w"] < tech["vol_1m"] < tech["vol_1y"], "volume trend lost"
    # excess_* stays on the windows rs_rating is built from
    assert "excess_1w" not in tech and "excess_3m" in tech


def test_price_analytics_no_history(monkeypatch):
    monkeypatch.setattr(ph, "_history", lambda code, ov: (None, None, None))
    assert ph.price_analytics("ZZZ", use_cache=False)["ok"] is False


def test_ticker_resolution():
    assert ph._tickers("500325", None) == ["500325.BO"]
    assert ph._tickers("RELIANCE", None) == ["RELIANCE.NS", "RELIANCE.BO"]


def test_volume_is_optional_and_never_reads_as_a_pass(monkeypatch):
    """A feed with no volume must leave the fields None. Zero or a default
    would quietly satisfy "1w average < 1m average"."""
    monkeypatch.setattr(ph, "_history",
                        lambda code, ov: (_series(), None, "TEST.NS"))
    tech = ph.price_analytics("TEST", use_cache=False)["technical"]
    assert tech["vol_1w"] is None and tech["vol_1m"] is None and tech["vol_1y"] is None
    assert tech["ret_1w"] is not None, "returns do not depend on volume"
