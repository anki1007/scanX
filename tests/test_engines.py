"""
Unit tests for the scoring math and a smoke test of the full pipeline + backtest.

Runs under pytest, or standalone:  python tests/test_engines.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_intel.engines._util import sigmoid_score, linear_score, clamp
from earnings_intel.engines.sue import compute_sue
from earnings_intel.engines.features import compute_features
from earnings_intel.engines.technical import score_technical
from earnings_intel.engines.transcript import score_transcript
from earnings_intel.engines.valuation import score_valuation
from earnings_intel.engines.institutional import score_institutional
from earnings_intel.risk import build_trade_plan, kelly_fraction
from earnings_intel.models import (
    Action, EarningsReport, Guidance, InstitutionalActivity, PriceBar,
    TranscriptData, ValuationInputs,
)
from earnings_intel.data import SampleProvider
from earnings_intel import Pipeline
from earnings_intel.backtest import Backtester, BacktestConfig


def test_util_scores():
    assert abs(sigmoid_score(0) - 50) < 1e-6
    assert 78 < sigmoid_score(2) < 82
    assert sigmoid_score(-2) < 22
    assert linear_score(0, 0, 0.05) == 50
    assert linear_score(0.05, 0, 0.05) == 100
    assert linear_score(-0.05, 0, 0.05) == 0
    assert clamp(150, 0, 100) == 100


def test_sue():
    beat = EarningsReport("X", "Q1", datetime(2026, 1, 1), 100, 10, 12,
                          eps_estimate=10, eps_std=1)
    miss = EarningsReport("X", "Q1", datetime(2026, 1, 1), 100, 10, 8,
                          eps_estimate=10, eps_std=1)
    assert compute_sue(beat).score > 70
    assert compute_sue(miss).score < 30
    assert compute_sue(beat).sue == 2.0


def test_features_trend():
    bars = [PriceBar(date(2025, 1, 1), 100 + i, 101 + i, 99 + i, 100 + i,
                     1_000_000 + (5_000_000 if i == 259 else 0), 55)
            for i in range(260)]
    f = compute_features(bars)
    assert f.trend_up is True
    assert f.rvol > 3
    assert score_technical(f) > 60


def test_transcript():
    pos = score_transcript(TranscriptData("X", "We see strong demand and robust "
                                          "growth, raising our guidance, confident."))
    neg = score_transcript(TranscriptData("X", "Weak demand, margin pressure, "
                                          "cautious outlook, lowering guidance."))
    assert pos.score > 65 and pos.sentiment == "Bullish"
    assert neg.score < 35 and neg.sentiment == "Bearish"


def test_valuation():
    cheap = ValuationInputs("X", pe=14, peg=0.8, fcf_yield=0.06, sector_median_pe=25)
    rich = ValuationInputs("X", pe=60, peg=3.0, fcf_yield=-0.01, sector_median_pe=25)
    assert score_valuation(cheap) > score_valuation(rich)
    assert score_valuation(cheap) > 60


def test_institutional():
    buy = InstitutionalActivity("X", fii_net_cr=120, dii_net_cr=60, promoter_buy=True)
    sell = InstitutionalActivity("X", fii_net_cr=-120, dii_net_cr=-40, promoter_sell=True)
    assert score_institutional(buy) > 70
    assert score_institutional(sell) < 30


def test_risk_sizing():
    capped = build_trade_plan(entry=1000, atr=20, action=Action.BUY, equity=1_000_000)
    assert capped.stop < capped.entry < capped.target1 < capped.target2
    assert capped.quantity == 100
    assert capped.notional <= 1_000_000 * 0.10 + 1

    risk_bound = build_trade_plan(entry=100, atr=8, action=Action.BUY, equity=1_000_000)
    assert risk_bound.quantity == 625
    assert abs(risk_bound.risk_amount - 10_000) < 50

    assert kelly_fraction(0.6, 2.0, 0.25) == 0.25
    assert kelly_fraction(0.5, 1.0, 0.25) == 0.0


def test_risk_short_plan():
    plan = build_trade_plan(entry=1000, atr=20, action=Action.STRONG_SELL,
                            equity=1_000_000)
    assert plan.stop > plan.entry > plan.target1 > plan.target2


def test_pipeline_smoke():
    res = Pipeline(SampleProvider(seed=7)).run(equity=1_000_000)
    assert len(res.signals) == 12
    comps = [s.composite_score for s in res.signals]
    assert comps == sorted(comps, reverse=True)
    assert all(0 <= s.composite_score <= 100 for s in res.signals)


def test_backtest_edge():
    res = Backtester(SampleProvider(seed=7, years=6)).run(
        BacktestConfig(horizon=20, min_composite=60))
    assert res.metrics["trades"] > 20
    assert res.metrics["expectancy_R"] > 0
    if res.quintiles:
        assert (res.quintiles[-1]["avg_fwd_return_pct"]
                > res.quintiles[0]["avg_fwd_return_pct"])


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} tests passed")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
