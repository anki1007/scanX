"""
Fundamental PEAD screen (the financiallyfree "Result Screening" step).

Turns scraped Screener.in quarters into the columns shown on the dashboard:
Sales / Net-Profit / EBITDA growth (YoY + QoQ), a 0-100 fundamental PEAD score,
a HIGH/MEDIUM/LOW category, and the headline gates:

  * >25% growth in BOTH Sales and Earnings (YoY)
  * "Sudden Shift" -- a sharp QoQ acceleration (the inflection PEAD rides)

`screen()` then applies the dashboard filters (market cap, PEAD score, forward PE,
YoY/QoQ minimums) and returns candidates sorted by PEAD score.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from .data.screener import ScreenerCompany


# PEAD score component weights (sum to 1.0)
_WEIGHTS = {
    "sales_yoy": 0.20, "np_yoy": 0.25, "ebitda_yoy": 0.15,
    "sales_qoq": 0.10, "np_qoq": 0.15, "ebitda_qoq": 0.10, "cf_profit": 0.05,
}


@dataclass
class ScreenerStock:
    code: str
    name: str
    url: str
    market_cap: Optional[float]
    last_price: Optional[float]
    pe: Optional[float]
    result_date: Optional[str]
    sales_yoy: Optional[float]
    sales_qoq: Optional[float]
    np_yoy: Optional[float]
    np_qoq: Optional[float]
    ebitda_yoy: Optional[float]
    ebitda_qoq: Optional[float]
    opm_latest: Optional[float]
    cf_profit: Optional[float]
    pead_score: float
    pead_category: str
    sudden_shift: bool
    growth_gate: bool
    calculation_date: str

    def to_dict(self) -> dict:
        return asdict(self)


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _growth_score(g: Optional[float], full: float = 60.0) -> float:
    """0% -> 50, +full% -> 100, -full% -> 0 (clamped)."""
    if g is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + 50.0 * g / full))


def _round(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 2)


def _score(sales_yoy, sales_qoq, np_yoy, np_qoq, ebitda_yoy, ebitda_qoq,
           cf_profit) -> tuple[float, str]:
    parts = {
        "sales_yoy": _growth_score(sales_yoy),
        "np_yoy": _growth_score(np_yoy),
        "ebitda_yoy": _growth_score(ebitda_yoy),
        "sales_qoq": _growth_score(sales_qoq, full=40),
        "np_qoq": _growth_score(np_qoq, full=40),
        "ebitda_qoq": _growth_score(ebitda_qoq, full=40),
        "cf_profit": 50.0 if cf_profit is None else max(0, min(100, cf_profit * 60)),
    }
    score = sum(parts[k] * _WEIGHTS[k] for k in _WEIGHTS)
    category = "HIGH" if score >= 50 else "MEDIUM" if score >= 30 else "LOW"
    return score, category


def _gate(sales_yoy, np_yoy) -> bool:
    return bool((sales_yoy or 0) >= 25 and (np_yoy or 0) >= 25)


def _shift(sales_qoq, np_qoq) -> bool:
    return bool((np_qoq or 0) >= 20 or (sales_qoq or 0) >= 20)


def derive(company: ScreenerCompany, cf_profit: Optional[float] = None) -> ScreenerStock:
    q = company.quarters
    sales_yoy = sales_qoq = np_yoy = np_qoq = ebitda_yoy = ebitda_qoq = None
    opm_latest = None

    if q:
        def yoy(series):
            return _pct(series[-1], series[-5]) if len(series) >= 5 else None

        def qoq(series):
            return _pct(series[-1], series[-2]) if len(series) >= 2 else None

        sales_yoy, sales_qoq = yoy(q.sales), qoq(q.sales)
        np_yoy, np_qoq = yoy(q.np), qoq(q.np)
        ebitda_yoy, ebitda_qoq = yoy(q.op), qoq(q.op)
        opm_latest = q.opm[-1] if q.opm else None

    score, category = _score(sales_yoy, sales_qoq, np_yoy, np_qoq,
                             ebitda_yoy, ebitda_qoq, cf_profit)
    return ScreenerStock(
        code=company.code, name=company.name, url=company.url,
        market_cap=company.market_cap, last_price=company.last_price,
        pe=company.pe, result_date=company.result_date,
        sales_yoy=_round(sales_yoy), sales_qoq=_round(sales_qoq),
        np_yoy=_round(np_yoy), np_qoq=_round(np_qoq),
        ebitda_yoy=_round(ebitda_yoy), ebitda_qoq=_round(ebitda_qoq),
        opm_latest=_round(opm_latest), cf_profit=_round(cf_profit),
        pead_score=round(score, 1), pead_category=category,
        sudden_shift=_shift(sales_qoq, np_qoq), growth_gate=_gate(sales_yoy, np_yoy),
        calculation_date=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def from_metrics(d: dict) -> ScreenerStock:
    """Build a scored ScreenerStock directly from pre-computed growth metrics.

    Used for the bundled sample dataset and any upstream source that already
    provides Sales/NP/EBITDA growth, so live and sample data score identically.
    """
    g = d.get
    score, category = _score(g("sales_yoy"), g("sales_qoq"), g("np_yoy"),
                             g("np_qoq"), g("ebitda_yoy"), g("ebitda_qoq"),
                             g("cf_profit"))
    return ScreenerStock(
        code=str(g("code", "")), name=g("name", ""), url=g("url", ""),
        market_cap=g("market_cap"), last_price=g("last_price"), pe=g("pe"),
        result_date=g("result_date"),
        sales_yoy=_round(g("sales_yoy")), sales_qoq=_round(g("sales_qoq")),
        np_yoy=_round(g("np_yoy")), np_qoq=_round(g("np_qoq")),
        ebitda_yoy=_round(g("ebitda_yoy")), ebitda_qoq=_round(g("ebitda_qoq")),
        opm_latest=_round(g("opm_latest")), cf_profit=_round(g("cf_profit")),
        pead_score=round(score, 1), pead_category=category,
        sudden_shift=_shift(g("sales_qoq"), g("np_qoq")),
        growth_gate=_gate(g("sales_yoy"), g("np_yoy")),
        calculation_date=datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class ScreenFilters:
    market_cap_min: float = 0
    market_cap_max: float = 1e12
    pead_min: float = 20.0
    pe_min: float = 0.8
    pe_max: float = 200.0
    sales_yoy_min: float = -1000
    sales_qoq_min: float = -1000
    np_yoy_min: float = -1000
    np_qoq_min: float = -1000
    require_growth_gate: bool = False


def screen(stocks: list[ScreenerStock],
           f: ScreenFilters = ScreenFilters()) -> list[ScreenerStock]:
    def ok(s: ScreenerStock) -> bool:
        if s.market_cap is not None and not (f.market_cap_min <= s.market_cap <= f.market_cap_max):
            return False
        if s.pead_score < f.pead_min:
            return False
        if s.pe is not None and not (f.pe_min <= s.pe <= f.pe_max):
            return False
        if (s.sales_yoy or -9999) < f.sales_yoy_min:
            return False
        if (s.sales_qoq or -9999) < f.sales_qoq_min:
            return False
        if (s.np_yoy or -9999) < f.np_yoy_min:
            return False
        if (s.np_qoq or -9999) < f.np_qoq_min:
            return False
        if f.require_growth_gate and not s.growth_gate:
            return False
        return True

    return sorted([s for s in stocks if ok(s)],
                  key=lambda s: s.pead_score, reverse=True)
