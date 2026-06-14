"""
Typed data structures shared across the whole system.

These are deliberately plain dataclasses (no pandas in the public surface) so
they are easy to construct in tests, serialise to JSON, and reason about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
@dataclass
class PriceBar:
    """A single OHLCV candle, optionally with NSE delivery percentage."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    delivery_pct: Optional[float] = None  # % of traded qty taken to delivery


# ---------------------------------------------------------------------------
# Earnings (Agent 2 output)
# ---------------------------------------------------------------------------
class Guidance(str, Enum):
    RAISED = "raised"
    MAINTAINED = "maintained"
    LOWERED = "lowered"
    NONE = "none"


@dataclass
class EarningsReport:
    """
    A parsed quarterly result plus the consensus it is measured against.

    `*_estimate` fields are street consensus; `eps_std` is the dispersion of
    analyst EPS estimates, used by the SUE engine. `*_history` holds the trailing
    sequence of surprises used when consensus dispersion is unavailable.
    """
    symbol: str
    period: str                         # e.g. "Q4FY26"
    report_datetime: datetime

    revenue: float
    pat: float                          # profit after tax
    eps: float

    revenue_estimate: Optional[float] = None
    pat_estimate: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_std: Optional[float] = None     # std dev of analyst EPS estimates

    # year-on-year growth (fractions, e.g. 0.28 == +28%)
    revenue_yoy: Optional[float] = None
    pat_yoy: Optional[float] = None

    guidance: Guidance = Guidance.NONE
    ebitda_margin: Optional[float] = None
    promoter_holding: Optional[float] = None
    institutional_holding: Optional[float] = None

    # trailing actual-minus-expected EPS surprises (most recent last)
    eps_surprise_history: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Institutional flow (Agent 6)
# ---------------------------------------------------------------------------
@dataclass
class InstitutionalActivity:
    symbol: str
    fii_net_cr: float = 0.0             # net FII flow (INR crore)
    dii_net_cr: float = 0.0
    mf_net_cr: float = 0.0
    block_deal_net_cr: float = 0.0
    bulk_deal_net_cr: float = 0.0
    promoter_buy: bool = False
    promoter_sell: bool = False
    pledge_change_pct: float = 0.0      # +ve == more pledging (bad)
    holding_change_pct: float = 0.0     # change in institutional holding


# ---------------------------------------------------------------------------
# Transcript (Agent 5)
# ---------------------------------------------------------------------------
@dataclass
class TranscriptData:
    symbol: str
    text: str = ""                      # raw concall transcript text


# ---------------------------------------------------------------------------
# Options flow (Agent 7)
# ---------------------------------------------------------------------------
@dataclass
class OptionsSnapshot:
    symbol: str
    call_oi: float = 0.0
    put_oi: float = 0.0
    call_oi_change: float = 0.0
    put_oi_change: float = 0.0
    pcr: Optional[float] = None         # put/call ratio


# ---------------------------------------------------------------------------
# Corporate events (Agent 10)
# ---------------------------------------------------------------------------
@dataclass
class CorporateEvent:
    symbol: str
    order_win: bool = False
    acquisition: bool = False
    buyback: bool = False
    bonus_or_split: bool = False
    fund_raise: bool = False
    credit_upgrade: bool = False
    credit_downgrade: bool = False
    management_exit: bool = False


# ---------------------------------------------------------------------------
# Valuation (Agent 11)
# ---------------------------------------------------------------------------
@dataclass
class ValuationInputs:
    symbol: str
    pe: Optional[float] = None
    ev_ebitda: Optional[float] = None
    peg: Optional[float] = None
    pb: Optional[float] = None
    fcf_yield: Optional[float] = None
    sector_median_pe: Optional[float] = None


# ---------------------------------------------------------------------------
# Scoring output
# ---------------------------------------------------------------------------
@dataclass
class ComponentScores:
    """Every engine writes its 0-100 score here (50 == neutral)."""
    sue: float = 50.0
    pead: float = 50.0
    transcript: float = 50.0
    institutional: float = 50.0
    options: float = 50.0
    technical: float = 50.0
    valuation: float = 50.0
    corporate_event: float = 50.0


class Action(str, Enum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG SELL"


@dataclass
class TradePlan:
    entry: float
    stop: float
    target1: float
    target2: float
    quantity: int
    risk_amount: float                  # INR at risk if stopped
    notional: float                     # INR position size


@dataclass
class Signal:
    symbol: str
    action: Action
    composite_score: float
    confidence: float                   # 0-100
    components: ComponentScores
    plan: Optional[TradePlan] = None
    rationale: list[str] = field(default_factory=list)
    as_of: Optional[datetime] = None


@dataclass
class ScoredStock:
    symbol: str
    composite_score: float
    components: ComponentScores
    report: Optional[EarningsReport] = None
