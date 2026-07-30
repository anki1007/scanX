"""STDRL performance metrics — pure, no torch, no GPU, no network."""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.rlmetrics import (  # noqa: E402
    evaluate, rank_agents, returns_from_equity,
)


def _curve(start, step_return, n):
    out, v = [start], start
    for _ in range(n):
        v *= (1 + step_return)
        out.append(v)
    return out


def test_sharpe_is_computed_from_returns_not_balance_levels():
    """The notebook used mean(net_worth)/std(net_worth). On synthetic series that
    scored a do-nothing agent 3301 against a profitable one's 27 — a 120x
    inversion — because a flat balance has almost no variance."""
    flat = [100000 + (i % 2) for i in range(200)]        # never really trades
    good = _curve(100000, 0.0015, 200)                   # compounds steadily
    assert evaluate(good)["total_return_pct"] > evaluate(flat)["total_return_pct"]
    # the discredited formula, for contrast
    def old(nw):
        m = sum(nw) / len(nw)
        sd = (sum((x - m) ** 2 for x in nw) / len(nw)) ** 0.5
        return m / sd if sd else float("inf")
    assert old(flat) > old(good)                         # the bug it replaces
    assert evaluate(good)["sharpe"] > 0


def test_a_losing_agent_has_a_negative_sharpe():
    """The old formula scored an 83% loss as POSITIVE."""
    losing = _curve(100000, -0.004, 200)
    m = evaluate(losing)
    assert m["sharpe"] < 0 and m["total_return_pct"] < 0


def test_episode_resets_are_not_counted_as_returns():
    """The evaluation loop restarts the env when an episode ends, so the raw
    curve jumps from e.g. 41,000 back to 100,000. Counting that as +144% hands
    every agent a fake gain and destroys the deviation."""
    curve = [100000, 90000, 80000, 41000, 100000, 99000, 98000]
    rets = returns_from_equity(curve)
    assert all(abs(r) < 0.6 for r in rets)
    assert len(rets) == 5          # the reset step is dropped, the rest kept


def test_drawdown_is_measured_from_the_running_peak():
    curve = [100, 150, 75, 120]                 # peak 150 -> trough 75 = 50%
    assert evaluate(curve)["max_drawdown_pct"] == pytest.approx(50.0, abs=0.01)


def test_volatility_and_sharpe_are_annualised_consistently():
    curve = _curve(100000, 0.001, 300)
    m = evaluate(curve, periods_per_year=252)
    assert m["volatility_pct"] is not None
    # a perfectly smooth compounding curve has ~zero deviation, so Sharpe is
    # either enormous or undefined — it must not silently come back as 0
    assert m["sharpe"] is None or m["sharpe"] > 0


def test_a_risk_free_rate_lowers_the_sharpe():
    import random
    random.seed(3)
    curve, v = [100000.0], 100000.0
    for _ in range(400):
        v *= (1 + random.gauss(0.0008, 0.01))
        curve.append(v)
    assert evaluate(curve, risk_free=0.07)["sharpe"] < evaluate(curve, risk_free=0.0)["sharpe"]


def test_sortino_ignores_upside_volatility():
    """A book that only ever jumps UP should not be punished for it."""
    import random
    random.seed(11)
    curve, v = [100000.0], 100000.0
    for i in range(300):
        v *= (1 + (0.05 if i % 20 == 0 else random.gauss(0.0004, 0.002)))
        curve.append(v)
    m = evaluate(curve)
    assert m["sortino"] is not None and m["sharpe"] is not None
    assert m["sortino"] > m["sharpe"]


@pytest.mark.parametrize("junk", [None, [], [1], ["x"], [None, None], [float("nan")]])
def test_a_thin_run_reports_unknown_rather_than_zero(junk):
    m = evaluate(junk)
    assert m["sharpe"] is None and m["total_return_pct"] is None
    assert m["cagr_pct"] is None


def test_ranking_puts_the_best_sharpe_first_and_unknowns_last():
    agents = {
        "loser": _curve(100000, -0.003, 200),
        "winner": _curve(100000, 0.002, 200),
        "no data": [1],
    }
    rows = rank_agents(agents)
    assert rows[0]["agent"] == "winner"
    assert rows[-1]["agent"] == "no data"       # unknown is not neutral
    assert [r["rank"] for r in rows] == [1, 2, 3]


def test_metrics_never_raise_on_garbage():
    for junk in (None, [], "nope", [{}], [[]], [float("inf")]):
        out = evaluate(junk)                     # type: ignore[arg-type]
        assert "sharpe" in out
    assert rank_agents(None) == []
