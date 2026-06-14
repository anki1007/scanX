"""
Central configuration for the NSE/BSE Earnings Intelligence Agent.

Every weight and threshold below is traceable to the project spec
(`NSE + BSE Earnings Intelligence Agent.md`). Keeping them in one place
means the scoring behaviour can be tuned without touching engine code.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Agent 4 — PEAD score component weights (must sum to 1.0)
# ---------------------------------------------------------------------------
PEAD_WEIGHTS: dict[str, float] = {
    "revenue_surprise": 0.20,
    "pat_surprise": 0.20,
    "eps_surprise": 0.20,
    "guidance": 0.15,
    "volume_expansion": 0.10,
    "delivery": 0.05,
    "relative_strength": 0.05,
    "institutional_flow": 0.05,
}

# ---------------------------------------------------------------------------
# Agent 12 — Composite score component weights (must sum to 1.0)
# ---------------------------------------------------------------------------
COMPOSITE_WEIGHTS: dict[str, float] = {
    "pead": 0.30,
    "transcript": 0.15,
    "institutional": 0.15,
    "options": 0.10,
    "technical": 0.10,
    "valuation": 0.10,
    "corporate_event": 0.10,
}


# ---------------------------------------------------------------------------
# Signal generation thresholds (Signal Generation Rules in the spec)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StrongBuyRule:
    pead: float = 80.0
    sue: float = 80.0
    transcript: float = 75.0
    institutional: float = 70.0
    volume_x: float = 3.0          # >= 3x average volume
    delivery_pct: float = 45.0     # >= 45% delivery
    technical: float = 70.0


@dataclass(frozen=True)
class StrongSellRule:
    pead: float = 20.0             # PEAD below this
    technical: float = 35.0        # breakdown structure
    # qualitative gates (negative guidance, promoter/institutional selling,
    # volume spike) are evaluated as booleans in signals.py


# ---------------------------------------------------------------------------
# Risk management (Portfolio Risk Agent)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskConfig:
    account_equity: float = 1_000_000.0   # INR; override at runtime
    risk_per_trade_pct: float = 0.01      # 1% of equity at risk per position
    max_position_pct: float = 0.10        # cap any single name at 10% of equity
    max_sector_pct: float = 0.30          # cap sector exposure at 30%
    max_open_positions: int = 15
    atr_stop_mult: float = 2.0            # stop = entry - 2*ATR (longs)
    target1_R: float = 1.5               # first target at 1.5R
    target2_R: float = 3.0               # second target at 3R
    kelly_cap: float = 0.25              # never bet more than 1/4 Kelly


# ---------------------------------------------------------------------------
# Universe / scanning
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScanConfig:
    poll_seconds: int = 60                # Agent 1 polling cadence
    nightly_batch_ist: str = "21:00"      # nightly recompute time
    long_watchlist_pct: float = 0.05      # top 5% by SUE
    short_watchlist_pct: float = 0.05     # bottom 5% by SUE
    min_price: float = 50.0               # liquidity floor
    min_avg_turnover_cr: float = 5.0      # min avg daily turnover (INR cr)


@dataclass(frozen=True)
class Settings:
    pead_weights: dict = field(default_factory=lambda: dict(PEAD_WEIGHTS))
    composite_weights: dict = field(default_factory=lambda: dict(COMPOSITE_WEIGHTS))
    strong_buy: StrongBuyRule = field(default_factory=StrongBuyRule)
    strong_sell: StrongSellRule = field(default_factory=StrongSellRule)
    risk: RiskConfig = field(default_factory=RiskConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)


DEFAULTS = Settings()


def _validate() -> None:
    for name, weights in (("PEAD", PEAD_WEIGHTS), ("COMPOSITE", COMPOSITE_WEIGHTS)):
        total = round(sum(weights.values()), 6)
        if total != 1.0:
            raise ValueError(f"{name} weights must sum to 1.0, got {total}")


_validate()
