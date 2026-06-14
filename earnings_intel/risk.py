"""
Portfolio Risk Agent.

Two responsibilities:
1. Size a single trade (ATR-based stop, fixed-fractional risk, Kelly-capped,
   position-value capped) -> a concrete TradePlan.
2. Enforce portfolio-level limits (max open positions, per-name cap, sector cap)
   with the authority to REJECT a trade.

This is the gate every signal must pass before it could ever reach execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import RiskConfig, DEFAULTS
from .models import Action, TradePlan


def kelly_fraction(win_rate: float, win_loss_ratio: float, cap: float) -> float:
    """Classic Kelly f* = W - (1-W)/R, floored at 0 and capped."""
    if win_loss_ratio <= 0:
        return 0.0
    f = win_rate - (1 - win_rate) / win_loss_ratio
    return max(0.0, min(f, cap))


def build_trade_plan(
    entry: float,
    atr: float,
    action: Action,
    risk: RiskConfig = DEFAULTS.risk,
    equity: Optional[float] = None,
    win_rate: Optional[float] = None,
    win_loss_ratio: Optional[float] = None,
) -> TradePlan:
    equity = equity if equity is not None else risk.account_equity
    if atr <= 0:
        atr = max(entry * 0.02, 0.01)
    stop_dist = risk.atr_stop_mult * atr

    risk_frac = risk.risk_per_trade_pct
    if win_rate is not None and win_loss_ratio is not None:
        kf = kelly_fraction(win_rate, win_loss_ratio, risk.kelly_cap)
        if kf <= 0:
            return TradePlan(round(entry, 2), round(entry, 2), round(entry, 2),
                             round(entry, 2), 0, 0.0, 0.0)
        risk_frac = min(risk_frac, kf)

    risk_amount = equity * risk_frac
    qty = int(risk_amount // stop_dist)

    max_notional = equity * risk.max_position_pct
    if qty * entry > max_notional:
        qty = int(max_notional // entry)
    qty = max(qty, 0)

    is_short = action in (Action.SELL, Action.STRONG_SELL)
    if is_short:
        stop = entry + stop_dist
        t1 = entry - risk.target1_R * stop_dist
        t2 = entry - risk.target2_R * stop_dist
    else:
        stop = entry - stop_dist
        t1 = entry + risk.target1_R * stop_dist
        t2 = entry + risk.target2_R * stop_dist

    return TradePlan(
        entry=round(entry, 2), stop=round(stop, 2),
        target1=round(t1, 2), target2=round(t2, 2),
        quantity=qty, risk_amount=round(qty * stop_dist, 2),
        notional=round(qty * entry, 2),
    )


@dataclass
class Position:
    symbol: str
    sector: str
    notional: float


@dataclass
class RiskManager:
    risk: RiskConfig = DEFAULTS.risk
    equity: Optional[float] = None
    positions: dict[str, Position] = field(default_factory=dict)

    def __post_init__(self):
        if self.equity is None:
            self.equity = self.risk.account_equity

    def sector_exposure(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for p in self.positions.values():
            agg[p.sector] = agg.get(p.sector, 0.0) + p.notional
        return agg

    def approve(self, symbol: str, sector: str, plan: TradePlan) -> tuple[bool, str]:
        if plan.quantity <= 0:
            return False, "zero position size"
        if (symbol not in self.positions
                and len(self.positions) >= self.risk.max_open_positions):
            return False, f"max open positions ({self.risk.max_open_positions}) reached"
        if plan.notional > self.equity * self.risk.max_position_pct + 1:
            return False, "exceeds per-name position cap"
        projected = self.sector_exposure().get(sector, 0.0) + plan.notional
        if projected > self.equity * self.risk.max_sector_pct + 1:
            return False, f"exceeds {sector} sector cap"
        return True, "approved"

    def add(self, symbol: str, sector: str, plan: TradePlan) -> None:
        self.positions[symbol] = Position(symbol, sector, plan.notional)
