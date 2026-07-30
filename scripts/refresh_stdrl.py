"""
STDRL — Stock Trading with Deep Reinforcement Learning, baked for the static site.

Trains DRL agents on a basket of NSE/BSE names, evaluates them out of sample and
writes docs/data/stdrl.json. The published page RENDERS that file; it never
trains anything, because the site is static and a browser cannot run torch.

    python scripts/refresh_stdrl.py --timesteps 20000
    python scripts/refresh_stdrl.py --agents PPO,A2C,DDPG,TD3,SAC
    python scripts/refresh_stdrl.py --dry-run          # no training, shape only

Needs torch + stable-baselines3 + gymnasium (~2.5GB). They are imported LAZILY,
so this file is importable and testable without them; with them absent it prints
why and exits 0 rather than failing the daily job.

PRICES COME FROM THE BUNDLES SCANX HAS ALREADY BAKED. The original notebook
re-downloaded OHLCV per ticker from a vendor API on every run; this repo already
holds a monthly return history for ~4,000 companies, so the training set is assembled from
disk. No API token, no rate limit, no second source of truth to drift.

THREE CORRECTIONS TO THE ORIGINAL, each of which changes the published result:

1. Sharpe is computed from RETURNS (earnings_intel.data.rlmetrics), not from
   mean/std of the balance level. The original formula scored a do-nothing agent
   3301 against a profitable agent's 27 and rated an 83% loss as positive, so
   every "best agent by Sharpe" ranking it produced was an artefact.

2. Transaction costs are charged. The stated objective was "include transaction
   costs for realistic simulations" and the environment charged none, which
   flatters any agent that trades often — exactly the behaviour RL discovers.

3. Reward is the CHANGE in net worth, not the level. Rewarding the level pays
   the agent for wealth it already had at every step, which swamps the signal
   from the decision it just made.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.rlmetrics import evaluate, rank_agents  # noqa: E402

#: The bundles carry MONTHLY returns, so everything annualises over 12,
#: not 252. Using the daily constant here would overstate every Sharpe by
#: sqrt(252/12) = 4.6x.
PERIODS_PER_YEAR = 12

OUT = ROOT / "docs" / "data" / "stdrl.json"
BUNDLES = ROOT / "docs" / "data" / "fundamental"

#: Round-trip cost per traded rupee: brokerage + STT + exchange + stamp + GST.
#: A discount-broker delivery round trip in India lands near this; it is the
#: single assumption that most changes whether a high-turnover agent looks good.
DEFAULT_COST_BPS = 25.0

DEFAULT_AGENTS = ("PPO", "A2C", "DDPG", "TD3", "SAC")
DEFAULT_BASKET = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
                  "BHARTIARTL", "ITC", "LT", "KOTAKBANK", "AXISBANK", "HINDUNILVR",
                  "BAJFINANCE", "MARUTI", "SUNPHARMA", "TITAN", "TATAMOTORS",
                  "TATASTEEL", "NTPC", "POWERGRID")


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------- price history
def load_history(code: str) -> list[dict]:
    """[{period, ret_pct, close}] for one company, from its baked bundle.

    The bundles do not keep a daily close series — they keep a MONTHLY return
    heatmap (`prices.heatmap.rows[].vals[]`, one row per year, twelve columns)
    plus yearly totals. That is ~370 monthly observations going back three
    decades for a name like RELIANCE, and 3,978 of the 5,494 bundles carry at
    least five years of it.

    So STDRL trains on MONTHLY bars rather than daily. Stated plainly because it
    changes what the Sharpe means: the metrics are annualised with
    periods_per_year=12, and a monthly-bar agent cannot learn intraday or
    swing behaviour. It is the honest ceiling of the data already on disk, and
    it needs no API token, no rate limit and no second source to drift from.

    An equity curve is rebuilt by compounding the returns from a notional 100,
    since the absolute price level is not what the agent trades on.
    """
    path = BUNDLES / f"{code}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    prices = raw.get("prices")
    if not isinstance(prices, dict) or not prices.get("ok"):
        return []
    heat = prices.get("heatmap")
    if not isinstance(heat, dict):
        return []
    rows = [r for r in (heat.get("rows") or []) if isinstance(r, dict)]
    if len(rows) < 5:
        return []

    out: list[dict] = []
    level = 100.0
    for row in sorted(rows, key=lambda r: r.get("year") or 0):
        year = row.get("year")
        vals = row.get("vals")
        if not isinstance(vals, list):
            continue
        for month, v in enumerate(vals, 1):
            if v is None:
                continue          # month has not happened yet, or no trade
            try:
                pct = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(pct) or abs(pct) > 95:
                continue          # a >95% monthly move is a split artefact
            level *= (1 + pct / 100.0)
            out.append({"period": f"{year}-{month:02d}",
                        "ret_pct": pct, "close": round(level, 4)})
    return out if len(out) >= 36 else []      # at least three years of months


def build_basket(codes) -> dict:
    """{code: [{date, close}]} for every code that has usable history."""
    out = {}
    for code in codes:
        hist = load_history(code)
        if hist:
            out[code] = hist
    return out


# --------------------------------------------------------------- the published file
def build_payload(results: dict, *, meta: dict) -> dict:
    """{generated_at, meta, agents:[ranked metrics ]} — the on-disk contract. PURE."""
    ranked = rank_agents(results, periods_per_year=meta.get("periods_per_year", PERIODS_PER_YEAR),
                         risk_free=meta.get("risk_free", 0.0))
    best = next((r["agent"] for r in ranked if r.get("sharpe") is not None), None)
    return {
        "generated_at": meta.get("generated_at") or time.strftime("%Y-%m-%d"),
        "meta": meta,
        "best_agent": best,
        "agents": ranked,
        "equity": {name: [round(float(v), 2) for v in curve]
                   for name, curve in (results or {}).items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake the STDRL agent comparison")
    ap.add_argument("--agents", default=",".join(DEFAULT_AGENTS))
    ap.add_argument("--timesteps", type=int, default=20000)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS,
                    help="round-trip transaction cost in basis points (default 25)")
    ap.add_argument("--risk-free", type=float, default=0.0,
                    help="annual risk-free rate as a decimal, e.g. 0.07")
    ap.add_argument("--codes", default=",".join(DEFAULT_BASKET))
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble the basket and report shape without training")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    basket = build_basket(codes)
    print(f"[stdrl] basket: {len(basket)}/{len(codes)} codes have usable monthly history")
    if not basket:
        print("[stdrl] no price history on disk — nothing to train on. "
              "Exiting 0 so the daily job stays green.")
        return 0

    if args.dry_run:
        n = min(len(v) for v in basket.values())
        print(f"[stdrl] dry run: {len(basket)} tickers, {n} aligned MONTHS, "
              f"agents={args.agents}, cost={args.cost_bps}bps")
        return 0

    try:
        import gymnasium  # noqa: F401
        import stable_baselines3  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[stdrl] stable-baselines3/gymnasium not installed ({type(exc).__name__}). "
              f"Install with: pip install gymnasium stable-baselines3 torch")
        print("[stdrl] exiting 0 — the daily job must not fail for an optional board")
        return 0

    from earnings_intel.data.rlenv import train_and_evaluate  # lazy: needs torch
    wanted = [a.strip().upper() for a in args.agents.split(",") if a.strip()]
    results, trained = train_and_evaluate(
        basket, agents=wanted, timesteps=args.timesteps, cost_bps=args.cost_bps)

    meta = {
        "generated_at": time.strftime("%Y-%m-%d"),
        "tickers": sorted(basket),
        "months": min(len(v) for v in basket.values()),
        "bar": "monthly",
        "timesteps": args.timesteps,
        "cost_bps": args.cost_bps,
        "risk_free": args.risk_free,
        "periods_per_year": PERIODS_PER_YEAR,
        "agents_trained": trained,
        "note": ("Sharpe and Sortino are computed from period RETURNS, not from the "
                 "balance level. Transaction costs are charged on every trade."),
    }
    payload = build_payload(results, meta=meta)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic(out, json.dumps(payload, separators=(",", ":")))

    print(f"[stdrl] wrote {out.name}: {len(payload['agents'])} agents, "
          f"best={payload['best_agent']}")
    for row in payload["agents"]:
        print(f"   {row['rank']}. {row['agent']:16} sharpe {str(row['sharpe']):>8}  "
              f"return {str(row['total_return_pct']):>8}%  maxDD {str(row['max_drawdown_pct']):>7}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
