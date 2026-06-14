"""
Event-driven PEAD backtester.

For every historical earnings event it scores the stock using **only data
available up to that day** (no lookahead), opens the resulting long/short the
next session, and manages it with the same ATR stop / targets the live system
would use. It reports the full metric suite from the spec, plus a composite-score
quintile analysis that validates whether the signal is actually monotonic
(higher score -> higher forward return).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Optional

from .config import Settings, DEFAULTS
from .engines.scoring import ScoringEngine
from .models import Action, PriceBar
from .risk import build_trade_plan
from .signals import classify
from .data.sample_provider import SampleProvider


@dataclass
class BacktestConfig:
    horizon: int = 20          # max holding period (trading days)
    min_composite: float = 62  # go long at/above this
    short_below: float = 32    # go short at/below this
    enable_shorts: bool = False
    cost_bps: float = 10.0     # round-trip cost+slippage in basis points
    min_history: int = 90      # bars required before we'll trade an event


@dataclass
class Trade:
    symbol: str
    period: str
    action: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    r_multiple: float
    ret_pct: float
    composite: float
    outcome: str               # stop / target / timeout
    win: bool


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    quintiles: list[dict] = field(default_factory=list)


def _date_index(dates: list, d) -> Optional[int]:
    i = bisect_right(dates, d) - 1
    return i if i >= 0 else None


def _simulate(window: list[PriceBar], entry: float, stop: float, target: float,
              is_short: bool) -> tuple[float, str, str]:
    """Walk forward; exit on stop/target intrabar else at the last close."""
    for b in window[1:]:
        if not is_short:
            if b.low <= stop:
                return stop, b.date.isoformat(), "stop"
            if b.high >= target:
                return target, b.date.isoformat(), "target"
        else:
            if b.high >= stop:
                return stop, b.date.isoformat(), "stop"
            if b.low <= target:
                return target, b.date.isoformat(), "target"
    last = window[-1]
    return last.close, last.date.isoformat(), "timeout"


class Backtester:
    def __init__(self, provider: SampleProvider, settings: Settings = DEFAULTS):
        self.provider = provider
        self.settings = settings
        self.engine = ScoringEngine(settings)

    def run(self, config: BacktestConfig = BacktestConfig()) -> BacktestResult:
        result = BacktestResult()
        cost = config.cost_bps / 10_000.0
        equity = self.settings.risk.account_equity
        start_equity = equity
        risk_frac = self.settings.risk.risk_per_trade_pct

        trade_returns: list[float] = []
        equity_curve: list[float] = [equity]
        signal_quintile_data: list[tuple[float, float]] = []  # (composite, fwd_ret)
        first_date = last_date = None

        for report in self.provider.all_events():
            sym = report.symbol
            bars = self.provider._history[sym]
            dates = [b.date for b in bars]
            ev_i = _date_index(dates, report.report_datetime.date())
            if ev_i is None or ev_i < config.min_history:
                continue
            if ev_i + 1 + config.horizon >= len(bars):
                continue

            hist = bars[: ev_i + 1]                      # no lookahead
            ex = self.provider.get_extras(sym, report.period)
            bundle = self.engine.score(
                report, hist, ex.institutional, ex.transcript,
                ex.options, ex.corporate, ex.valuation)
            action, conf, _ = classify(bundle, report, ex.institutional, self.settings)

            entry_i = ev_i + 1
            entry_price = bars[entry_i].open
            window = bars[entry_i: entry_i + config.horizon + 1]

            # raw forward return for quintile validation (no stops)
            fwd = bars[entry_i + config.horizon].close / entry_price - 1
            signal_quintile_data.append((bundle.composite, fwd))

            go_long = bundle.composite >= config.min_composite and action in (
                Action.BUY, Action.STRONG_BUY)
            go_short = (config.enable_shorts
                        and bundle.composite <= config.short_below
                        and action in (Action.SELL, Action.STRONG_SELL))
            if not (go_long or go_short):
                continue

            is_short = go_short
            plan = build_trade_plan(entry_price, bundle.features.atr, action,
                                    self.settings.risk, equity)
            if plan.quantity <= 0:
                continue

            exit_price, exit_date, outcome = _simulate(
                window, entry_price, plan.stop, plan.target2, is_short)

            gross = (exit_price / entry_price - 1) if not is_short else (
                entry_price / exit_price - 1)
            ret_pct = gross - cost
            risk_per_share = abs(entry_price - plan.stop)
            r_multiple = ((exit_price - entry_price) if not is_short
                          else (entry_price - exit_price)) / max(risk_per_share, 1e-9)
            r_multiple -= cost * entry_price / max(risk_per_share, 1e-9)

            # Each trade risks `risk_frac` of equity; PnL = R-multiple * risk amount.
            pnl = r_multiple * (equity * risk_frac)
            prev = equity
            equity += pnl
            trade_returns.append(pnl / prev)
            equity_curve.append(equity)

            d0 = bars[entry_i].date
            first_date = d0 if first_date is None else min(first_date, d0)
            last_date = max(last_date or d0, bars[entry_i + config.horizon].date)

            result.trades.append(Trade(
                symbol=sym, period=report.period, action=action.value,
                entry_date=d0.isoformat(), exit_date=exit_date,
                entry=round(entry_price, 2), exit=round(exit_price, 2),
                r_multiple=round(r_multiple, 3), ret_pct=round(ret_pct * 100, 2),
                composite=round(bundle.composite, 1), outcome=outcome,
                win=r_multiple > 0))

        result.metrics = self._metrics(result.trades, trade_returns, equity_curve,
                                       start_equity, equity, first_date, last_date)
        result.quintiles = self._quintiles(signal_quintile_data)
        return result

    # --------------------------------------------------------------- metrics
    @staticmethod
    def _metrics(trades, returns, curve, start_eq, end_eq, first, last) -> dict:
        n = len(trades)
        if n == 0:
            return {"trades": 0, "note": "no trades met the entry criteria"}

        wins = [t for t in trades if t.win]
        losses = [t for t in trades if not t.win]
        gross_profit = sum(t.r_multiple for t in wins)
        gross_loss = abs(sum(t.r_multiple for t in losses))

        years = max((last - first).days / 365.25, 1e-9) if first and last else 1.0
        tpy = n / years
        mu = mean(returns) if returns else 0.0
        sd = pstdev(returns) if len(returns) > 1 else 0.0
        downside = [r for r in returns if r < 0]
        dsd = pstdev(downside) if len(downside) > 1 else 0.0

        peak = curve[0]
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak)

        cagr = (end_eq / start_eq) ** (1 / years) - 1 if start_eq > 0 else 0.0

        return {
            "trades": n,
            "win_rate_pct": round(100 * len(wins) / n, 1),
            "avg_win_R": round(mean([t.r_multiple for t in wins]), 3) if wins else 0.0,
            "avg_loss_R": round(mean([t.r_multiple for t in losses]), 3) if losses else 0.0,
            "expectancy_R": round(mean([t.r_multiple for t in trades]), 3),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "sharpe": round(mu / sd * (tpy ** 0.5), 2) if sd > 0 else 0.0,
            "sortino": round(mu / dsd * (tpy ** 0.5), 2) if dsd > 0 else 0.0,
            "max_drawdown_pct": round(100 * max_dd, 1),
            "total_return_pct": round(100 * (end_eq / start_eq - 1), 1),
            "cagr_pct": round(100 * cagr, 1),
            "final_equity": round(end_eq, 0),
            "span_years": round(years, 1),
        }

    @staticmethod
    def _quintiles(data: list[tuple[float, float]]) -> list[dict]:
        if len(data) < 10:
            return []
        data = sorted(data, key=lambda x: x[0])
        q = len(data) // 5
        out = []
        for i in range(5):
            chunk = data[i * q: (i + 1) * q] if i < 4 else data[i * q:]
            comps = [c for c, _ in chunk]
            rets = [r for _, r in chunk]
            out.append({
                "quintile": i + 1,
                "composite_range": f"{min(comps):.0f}-{max(comps):.0f}",
                "avg_fwd_return_pct": round(100 * mean(rets), 2),
                "n": len(chunk),
            })
        return out
