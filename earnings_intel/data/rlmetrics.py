"""
Performance metrics for the STDRL agents — pure, deterministic, no RL imports.

Kept separate from the training script on purpose. Training needs torch and
stable-baselines3 (~2.5GB); the maths that decides which agent is BEST needs
neither, so it can be unit-tested on every run of the suite and audited without
a GPU.

    from earnings_intel.data.rlmetrics import evaluate
    evaluate(net_worths, periods_per_year=252)
    -> {total_return_pct, cagr_pct, sharpe, sortino, max_drawdown_pct, ...}

THE SHARPE RATIO HERE IS COMPUTED FROM RETURNS, NOT FROM BALANCE LEVELS.

The notebook this came from used ``mean(net_worth) / std(net_worth)`` — the mean
and standard deviation of the balance ITSELF. That is not a Sharpe ratio and it
inverts the ranking it is used for. Measured on synthetic series:

    portfolio               end value    that formula    real Sharpe
    does nothing              100,040          3301.1           0.40
    good, +40%                108,665            27.3           0.53
    reckless, same trades      17,498             1.8          -2.09

An agent that never trades scores ~120x "better" than a profitable one, because
a flat balance has almost no variance. An agent that lost 83% still scores
positive. Every "best agent by Sharpe" conclusion drawn from that number is an
artefact of the formula rather than a finding about the agent.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["returns_from_equity", "evaluate", "rank_agents",
           "TRADING_DAYS", "DEFAULT_RISK_FREE"]

TRADING_DAYS = 252
#: Indian 10y G-Sec is the sensible risk-free anchor for an INR book. Passed in
#: explicitly by the bake so the published number can state what it assumed.
DEFAULT_RISK_FREE = 0.0


def _clean(series: Sequence[Any] | None) -> list[float]:
    """Finite floats only, order preserved. PURE."""
    out: list[float] = []
    for v in (series or []):
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def returns_from_equity(equity: Sequence[Any] | None) -> list[float]:
    """Period-over-period simple returns from an equity curve. PURE.

    Drops the step across an episode RESET. The evaluation loop restarts the
    environment whenever an episode ends, so the raw curve contains jumps from
    (say) 41,000 back to 100,000 — counting those as a +144% period return would
    hand every agent an enormous fake gain and wreck both mean and deviation.
    A drop to exactly the starting balance after a fall is the signature.
    """
    values = _clean(equity)
    out: list[float] = []
    for prev, cur in zip(values, values[1:]):
        if prev <= 0:
            continue
        change = (cur - prev) / prev
        # a reset looks like an implausible single-period jump; real daily equity
        # moves of >|60%| in one step do not happen on a diversified book
        if abs(change) > 0.6:
            continue
        out.append(change)
    return out


def _std(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))   # sample sd


def evaluate(equity: Sequence[Any] | None, *, periods_per_year: int = TRADING_DAYS,
             risk_free: float = DEFAULT_RISK_FREE) -> dict:
    """Risk and return of one equity curve. PURE, never raises.

    `risk_free` is an ANNUAL rate (0.07 for 7%); it is de-annualised internally.
    Every field is None rather than 0 when it cannot be computed, so a thin run
    reports "unknown" instead of a confident zero.
    """
    values = _clean(equity)
    blank = {"start": None, "end": None, "periods": len(values),
             "total_return_pct": None, "cagr_pct": None, "sharpe": None,
             "sortino": None, "max_drawdown_pct": None, "volatility_pct": None,
             "win_rate_pct": None, "risk_free_pct": round(risk_free * 100, 2)}
    if len(values) < 3:
        return blank

    start, end = values[0], values[-1]
    rets = returns_from_equity(values)
    out = dict(blank)
    out["start"], out["end"] = round(start, 2), round(end, 2)

    if start > 0:
        out["total_return_pct"] = round((end / start - 1) * 100, 2)
        years = len(rets) / float(periods_per_year or TRADING_DAYS)
        if years > 0 and end > 0:
            out["cagr_pct"] = round(((end / start) ** (1 / years) - 1) * 100, 2)

    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        sd = _std(rets)
        rf_period = (1 + risk_free) ** (1 / periods_per_year) - 1 if risk_free else 0.0
        ann = math.sqrt(periods_per_year)
        out["volatility_pct"] = round(sd * ann * 100, 2)
        if sd > 0:
            out["sharpe"] = round((mean - rf_period) / sd * ann, 3)
        # Sortino punishes only DOWNSIDE deviation, which is what a trader
        # actually minds; a Sharpe penalises violent upside equally.
        downside = [r for r in rets if r < rf_period]
        dsd = _std(downside) if len(downside) >= 2 else 0.0
        if dsd > 0:
            out["sortino"] = round((mean - rf_period) / dsd * ann, 3)
        out["win_rate_pct"] = round(100 * sum(1 for r in rets if r > 0) / len(rets), 1)

    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    out["max_drawdown_pct"] = round(worst * 100, 2)
    return out


def rank_agents(agents: Mapping[str, Sequence[Any]] | None, **kw) -> list[dict]:
    """[{agent, ...metrics}] sorted best-first. PURE.

    Ranked on SHARPE, then CAGR. An agent whose Sharpe could not be computed
    sorts last rather than being treated as zero — unknown is not neutral.
    """
    rows = []
    for name, curve in (agents or {}).items():
        rows.append({"agent": str(name), **evaluate(curve, **kw)})
    rows.sort(key=lambda r: (
        r["sharpe"] is None,
        -(r["sharpe"] if r["sharpe"] is not None else 0),
        -(r["cagr_pct"] if r["cagr_pct"] is not None else 0),
    ))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
