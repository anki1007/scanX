"""
Thin end-to-end demo of the Earnings Intelligence Agent on synthetic data.

Runs the full pipeline (scan -> score -> signal -> risk -> alert) over a sample
universe and prints the ranked board plus formatted alerts for the top setups.

    python scripts/run_demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_intel import Pipeline                      # noqa: E402
from earnings_intel.alerts import format_alert           # noqa: E402
from earnings_intel.data import SampleProvider           # noqa: E402
from earnings_intel.models import Action                 # noqa: E402


def main() -> None:
    provider = SampleProvider(seed=7)
    pipe = Pipeline(provider)
    result = pipe.run(equity=1_000_000)

    print("=" * 92)
    print("EARNINGS INTELLIGENCE — RANKED BOARD  (synthetic data, as of "
          f"{provider.as_of})")
    print("=" * 92)
    hdr = (f"{'SYM':<11}{'ACTION':<12}{'COMP':>5}{'PEAD':>6}{'SUE':>5}"
           f"{'TECH':>6}{'INST':>6}{'TRSC':>6}{'VAL':>5}{'CONF':>6}")
    print(hdr)
    print("-" * 92)
    for s in result.signals:
        c = s.components
        print(f"{s.symbol:<11}{s.action.value:<12}{s.composite_score:>5.0f}"
              f"{c.pead:>6.0f}{c.sue:>5.0f}{c.technical:>6.0f}{c.institutional:>6.0f}"
              f"{c.transcript:>6.0f}{c.valuation:>5.0f}{s.confidence:>6.0f}")

    print()
    print(f"Actionable signals: {len(result.actionable)}   "
          f"Longs sized & approved: {len(result.longs)}")

    print("\n" + "=" * 92)
    print("ALERTS (top approved longs)")
    print("=" * 92)
    shown = 0
    for s in result.signals:
        if s.action in (Action.STRONG_BUY, Action.BUY) and s.plan:
            print("\n" + format_alert(s, _find_report(provider, s.symbol)))
            shown += 1
        if shown >= 3:
            break
    if shown == 0:
        print("No long setups cleared the risk gate this run.")


def _find_report(provider: SampleProvider, symbol: str):
    events = provider._events.get(symbol)
    return events[-1] if events else None


if __name__ == "__main__":
    main()
