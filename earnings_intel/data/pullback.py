"""Quality growth in an uptrend, caught on a quiet pullback.

The screen, condition for condition:

    Market Capitalization > 1000            AND Market Capitalization < 100000
    Sales > 200
    YOY Quarterly sales growth  > 20
    YOY Quarterly profit growth > 40
    YOY Quarterly profit growth > YOY Quarterly sales growth
    OPM latest quarter > OPM 5Year
    YOY Quarterly sales growth  > Sales growth
    YOY Quarterly profit growth > Profit growth
    Current price > DMA 50                  AND DMA 50 > DMA 200
    Return over 1month > 0                  AND Return over 1week < 0
    Volume 1week average < Volume 1month average
    Volume 1month average > Volume 1year average

The last five read as one idea: a name in a confirmed uptrend that has drifted
back for a week on drying volume, while still trading heavier than it did a
year ago. The fundamental conditions ahead of them demand that the drift is
not the business turning.

A condition is PASS, FAIL or UNTESTED. Untested is never a pass. Roughly one
company in eight is a bank or an NBFC that carries no "Sales" and no "OPM"
line at all, and volume is absent until a bundle has been re-baked since the
price layer started storing it. Those shortfalls ride along on every row
rather than being quietly admitted.

PURE: no network, no clock.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Paren-aware and already tested: "(4.2)" is -4.2, which a plainer regex reads
# as +4.2 and turns a loss into a profit.
from .qprofit import _num

__all__ = ["CONDITIONS", "THRESHOLDS", "facts", "evaluate", "passes"]

THRESHOLDS = {
    "mcap_min": 1000.0,          # Rs Cr
    "mcap_max": 100000.0,
    "sales_min": 200.0,          # Rs Cr, latest full year
    "q_sales_yoy_min": 20.0,     # %
    "q_profit_yoy_min": 40.0,    # %
}

CONDITIONS = (
    ("mcap_over", "Market cap > 1,000 Cr"),
    ("mcap_under", "Market cap < 1,00,000 Cr"),
    ("sales_over", "Sales > 200 Cr"),
    ("q_sales_growth", "Qtr sales YoY > 20%"),
    ("q_profit_growth", "Qtr profit YoY > 40%"),
    ("profit_beats_sales", "Qtr profit YoY > qtr sales YoY"),
    ("opm_expanding", "Qtr OPM > 5-year OPM"),
    ("sales_accel", "Qtr sales YoY > annual sales growth"),
    ("profit_accel", "Qtr profit YoY > annual profit growth"),
    ("above_50dma", "Price > 50 DMA"),
    ("golden_cross", "50 DMA > 200 DMA"),
    ("up_1m", "1-month return > 0"),
    ("down_1w", "1-week return < 0"),
    ("vol_cooling", "1-week volume < 1-month volume"),
    ("vol_elevated", "1-month volume > 1-year volume"),
)


def _rows(block: Any) -> dict:
    r = block.get("rows") if isinstance(block, Mapping) else None
    return r if isinstance(r, Mapping) else {}


def _row(rows: Mapping, *names) -> list:
    """A statement row by any of its aliases.

    Quarters label it "OPM" and annuals "OPM %", so an exact match on one of
    them silently loses the other. Exact match first, prefix second.
    """
    for want in names:
        for key, series in rows.items():
            if str(key).strip().lower() == want.lower():
                return list(series or [])
    for want in names:
        for key, series in rows.items():
            if str(key).strip().lower().startswith(want.lower()):
                return list(series or [])
    return []


def _drop_ttm(headers: Any, series: list) -> list:
    """Annual tables end in a TTM column.

    Comparing TTM with the year before it is not a year-on-year growth rate;
    it is a part year measured against a full one.
    """
    heads = list(headers or [])
    if heads and len(heads) == len(series) and str(heads[-1]).strip().upper() == "TTM":
        return series[:-1]
    return series


def _yoy_q(series: list) -> float | None:
    """Latest quarter against the SAME quarter a year earlier (q[-1] vs q[-5]).

    Not against the previous quarter: most Indian businesses are seasonal and
    a June-vs-March comparison measures the season, not the business.
    """
    vals = [v for v in (_num(x) for x in series) if v is not None]
    if len(vals) < 5:
        return None
    now, then = vals[-1], vals[-5]
    if abs(then) < 1e-9:
        return None
    return (now - then) / abs(then) * 100.0


def _yoy_annual(series: list) -> float | None:
    vals = [v for v in (_num(x) for x in series) if v is not None]
    if len(vals) < 2:
        return None
    now, then = vals[-1], vals[-2]
    if abs(then) < 1e-9:
        return None
    return (now - then) / abs(then) * 100.0


def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def facts(bundle: Any) -> dict:
    """Every input the screen needs. None means "not available", never zero."""
    out: dict = {k: None for k in (
        "mcap", "sales", "q_sales_yoy", "q_profit_yoy", "q_opm", "opm_5y",
        "sales_growth", "profit_growth", "above_50dma", "golden_cross",
        "ret_1m", "ret_1w", "vol_1w", "vol_1m", "vol_1y", "name", "ltp")}
    if not isinstance(bundle, Mapping):
        return out
    fundamental = bundle.get("fundamental")
    fundamental = fundamental if isinstance(fundamental, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return out

    overview = fundamental.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}
    out["name"] = str(fundamental.get("name") or "")
    out["mcap"] = _num(overview.get("Market Cap"))
    out["ltp"] = _num(overview.get("Current Price"))

    qrows = _rows(fundamental.get("quarters"))
    out["q_sales_yoy"] = _yoy_q(_row(qrows, "Sales", "Revenue"))
    out["q_profit_yoy"] = _yoy_q(_row(qrows, "Net Profit"))
    qopm = [v for v in (_num(x) for x in _row(qrows, "OPM")) if v is not None]
    out["q_opm"] = qopm[-1] if qopm else None

    pl = fundamental.get("profit_loss")
    prows = _rows(pl)
    pheads = pl.get("headers") if isinstance(pl, Mapping) else []
    sales = _drop_ttm(pheads, _row(prows, "Sales", "Revenue"))
    profit = _drop_ttm(pheads, _row(prows, "Net Profit"))
    opm_ann = _drop_ttm(pheads, _row(prows, "OPM %", "OPM"))
    svals = [v for v in (_num(x) for x in sales) if v is not None]
    out["sales"] = svals[-1] if svals else None
    out["sales_growth"] = _yoy_annual(sales)
    out["profit_growth"] = _yoy_annual(profit)
    ovals = [v for v in (_num(x) for x in opm_ann) if v is not None]
    out["opm_5y"] = _mean(ovals[-5:]) if ovals else None

    prices = bundle.get("prices")
    tech = prices.get("technical") if isinstance(prices, Mapping) else None
    tech = tech if isinstance(tech, Mapping) else {}
    for key in ("above_50dma", "golden_cross"):
        v = tech.get(key)
        out[key] = v if isinstance(v, bool) else None
    for key in ("ret_1m", "ret_1w", "vol_1w", "vol_1m", "vol_1y"):
        out[key] = _num(tech.get(key))
    return out


def _gt(a, b):
    return None if (a is None or b is None) else a > b


def _lt(a, b):
    return None if (a is None or b is None) else a < b


def evaluate(bundle: Any) -> dict:
    """PASS / FAIL / UNTESTED per condition, plus the overall verdict."""
    f = facts(bundle)
    t = THRESHOLDS
    v = {
        "mcap_over": _gt(f["mcap"], t["mcap_min"]),
        "mcap_under": _lt(f["mcap"], t["mcap_max"]),
        "sales_over": _gt(f["sales"], t["sales_min"]),
        "q_sales_growth": _gt(f["q_sales_yoy"], t["q_sales_yoy_min"]),
        "q_profit_growth": _gt(f["q_profit_yoy"], t["q_profit_yoy_min"]),
        "profit_beats_sales": _gt(f["q_profit_yoy"], f["q_sales_yoy"]),
        "opm_expanding": _gt(f["q_opm"], f["opm_5y"]),
        "sales_accel": _gt(f["q_sales_yoy"], f["sales_growth"]),
        "profit_accel": _gt(f["q_profit_yoy"], f["profit_growth"]),
        "above_50dma": f["above_50dma"],
        "golden_cross": f["golden_cross"],
        "up_1m": _gt(f["ret_1m"], 0.0),
        "down_1w": _lt(f["ret_1w"], 0.0),
        "vol_cooling": _lt(f["vol_1w"], f["vol_1m"]),
        "vol_elevated": _gt(f["vol_1m"], f["vol_1y"]),
    }
    failed = [k for k, _ in CONDITIONS if v[k] is False]
    untested = [k for k, _ in CONDITIONS if v[k] is None]
    return {
        "conditions": v,
        "failed": failed,
        "untested": untested,
        "pass": not failed,
        "tested": len(CONDITIONS) - len(untested),
        "facts": f,
    }


def passes(bundle: Any) -> bool:
    return evaluate(bundle)["pass"]
