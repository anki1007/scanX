"""
Validate the PEAD edge with the event-driven backtester (synthetic data).

    python scripts/run_backtest.py

Prints the full metric suite, a composite-score quintile analysis (does a higher
score actually predict a higher forward return?), and a few sample trades.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_intel.backtest import Backtester, BacktestConfig   # noqa: E402
from earnings_intel.data import SampleProvider                   # noqa: E402


def main() -> None:
    provider = SampleProvider(seed=7, years=6)
    bt = Backtester(provider)
    cfg = BacktestConfig(horizon=20, min_composite=62, enable_shorts=False)
    res = bt.run(cfg)

    print("=" * 78)
    print("PEAD BACKTEST  (long high-composite earnings beats, 20-day horizon)")
    print("=" * 78)
    for k, v in res.metrics.items():
        print(f"  {k:<22}: {v}")

    if res.quintiles:
        print("\nComposite-score quintile vs avg 20-day forward return")
        print("-" * 78)
        print(f"  {'Quintile':<10}{'Composite':<14}{'Avg fwd ret %':<16}{'N':<6}")
        for q in res.quintiles:
            print(f"  {q['quintile']:<10}{q['composite_range']:<14}"
                  f"{q['avg_fwd_return_pct']:<16}{q['n']:<6}")
        print("  (Monotonic increase => the composite score has predictive power.)")

    print(f"\nSample trades (first 8 of {len(res.trades)}):")
    print("-" * 78)
    print(f"  {'SYM':<10}{'PERIOD':<9}{'ENTRY':>9}{'EXIT':>9}{'R':>7}"
          f"{'RET%':>8}{'COMP':>6}  {'OUTCOME'}")
    for t in res.trades[:8]:
        print(f"  {t.symbol:<10}{t.period:<9}{t.entry:>9.1f}{t.exit:>9.1f}"
              f"{t.r_multiple:>7.2f}{t.ret_pct:>8.2f}{t.composite:>6.0f}  {t.outcome}")


if __name__ == "__main__":
    main()
