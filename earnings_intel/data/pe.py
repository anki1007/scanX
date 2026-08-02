"""Trailing P/E computed from filed quarterly results, not taken on trust.

The definition is not controversial:

    trailing P/E = price / trailing-twelve-month consolidated EPS

What is controversial is every vendor's precomputed version of it. Checked
against independent verification from primary filings on seven large caps:

    company      filings   from quarters   statement headline   broker feed
    TATASTEEL      21.4        21.5              20.0               21.4
    DLF            37.0        37.0              39.0               28.2
    SHRIRAMFIN     21.8        18.5              21.8               21.4
    BHARTIARTL     42.9        42.9              46.2                 --
    HINDUNILVR     33.0        33.0              44.8               33.1
    JSWSTEEL       12.5        12.5              25.8               11.1
    TORNTPHARM     81.8        80.4              87.3               86.7

Computing it ourselves lands within a median 0.5%. Both precomputed sources
miss badly somewhere -- the statement headline read 25.8 for JSWSTEEL against a
filed 12.5 and 44.8 for HINDUNILVR against 33.0; the feed read 28.2 for DLF
against 37.0. Neither is wrong often, but neither is safe to publish unchecked,
and their errors are largest exactly where a reader would act on them.

The other reason to compute it: the inputs are auditable. `quarters_used` says
which four quarters produced the number, so a disputed P/E can be argued about
with the filings rather than with a vendor.

Pure, no I/O, never raises.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

__all__ = ["ttm_eps", "trailing_pe", "MIN_QUARTERS", "EXCEPTIONAL_MULTIPLE"]

#: A trailing twelve months needs four quarters. Three is not a year.
MIN_QUARTERS = 4

#: A quarter more than this multiple of the historical median is treated as
#: carrying a one-off. JSWSTEEL booked EPS of 66.94 against a ~8 run rate --
#: a revaluation, not earnings power. We still report the number (reported TTM
#: is the market convention) but we flag it, because a P/E of 12.5 built on a
#: one-time gain will not survive the next four quarters.
EXCEPTIONAL_MULTIPLE = 6.0


def _num(value: Any) -> float | None:
    """Parse a figure off a statement cell. Returns None for anything unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).replace(",", "").replace("₹", "").replace("%", "").strip()
    if not text or text in {"-", "--"}:
        return None
    m = re.search(r"-?\d+\.?\d*", text)
    if not m:
        return None
    try:
        out = float(m.group())
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def ttm_eps(quarterly_eps: Sequence[Any]) -> dict:
    """Sum the last four quarterly EPS figures. PURE.

    Returns {"value", "n", "used", "exceptional"}. `value` is None when there
    are not four usable quarters -- which is an honest gap, not a zero.
    """
    values = [v for v in (_num(x) for x in (quarterly_eps or [])) if v is not None]
    if len(values) < MIN_QUARTERS:
        return {"value": None, "n": len(values), "used": [], "exceptional": False}

    used = values[-MIN_QUARTERS:]
    history = values[:-MIN_QUARTERS]
    exceptional = False
    if history:
        ordered = sorted(abs(v) for v in history)
        median = ordered[len(ordered) // 2]
        if median > 0 and max(abs(v) for v in used) > EXCEPTIONAL_MULTIPLE * median:
            exceptional = True

    return {"value": sum(used), "n": len(values), "used": used,
            "exceptional": exceptional}


def reconciles_with_annual(quarterly_eps: Sequence[Any],
                           annual_eps: Any, *, tolerance: float = 0.02) -> bool | None:
    """Do four filed quarters add up to the filed full-year EPS? PURE.

    This is the check that decides whether summing quarters is legitimate AT
    ALL for a given company. Quarterly EPS figures are each struck on that
    quarter's WEIGHTED-AVERAGE share count. If a rights issue, merger or bonus
    lands mid-year, the four quarters are denominated differently and their sum
    is not a year's earnings per current share.

    That is not hypothetical -- it is where our own method failed. SHRIRAMFIN
    summed to 18.5 against a true 21.8, a 15% error, and ADANIENT restated
    Q2 FY26 basic EPS from 27.38 down to 26.58 for the bonus element in a
    rights issue. Independent verification of JSWSTEEL used exactly this test
    the other way: its four FY26 quarters sum to 91.44 against a filed annual
    91.43, which is what licensed the quarter-sum there.

    Returns None when there is nothing to compare against -- unknown is not the
    same as fine.
    """
    values = [v for v in (_num(x) for x in (quarterly_eps or [])) if v is not None]
    annual = _num(annual_eps)
    if len(values) < MIN_QUARTERS or annual is None or annual == 0:
        return None
    return abs(sum(values[-MIN_QUARTERS:]) - annual) <= abs(annual) * tolerance


def trailing_pe(price: Any, quarterly_eps: Sequence[Any],
                headers: Sequence[Any] | None = None) -> dict:
    """price / TTM EPS, with the evidence attached. PURE, never raises.

    Returns a dict with `value` None when the P/E is not computable. The two
    reasons are kept apart on purpose:

        "loss-making"   TTM EPS <= 0. A P/E here is not missing data, it is a
                        meaningless quantity -- a negative multiple ranks a
                        loss-maker as "cheap", which is how 125 companies once
                        ended up at the top of a value screen.
        "insufficient"  fewer than four filed quarters.
    """
    px = _num(price)
    eps = ttm_eps(quarterly_eps)

    out: dict = {
        "value": None,
        "ttm_eps": eps["value"],
        "quarters_used": [],
        "exceptional": eps["exceptional"],
        "reason": None,
    }

    if headers and eps["used"]:
        tail = [str(h) for h in headers][-MIN_QUARTERS:]
        if len(tail) == len(eps["used"]):
            out["quarters_used"] = tail

    if eps["value"] is None:
        out["reason"] = "insufficient"
        return out
    if eps["value"] <= 0:
        out["reason"] = "loss-making"
        return out
    if px is None or px <= 0:
        out["reason"] = "no-price"
        return out

    out["value"] = round(px / eps["value"], 2)
    return out


def from_bundle(bundle: Mapping | None) -> dict:
    """Convenience: pull price and quarterly EPS out of a baked bundle. PURE."""
    fundamental = {}
    if isinstance(bundle, Mapping):
        inner = bundle.get("fundamental")
        fundamental = inner if isinstance(inner, Mapping) else bundle

    overview = fundamental.get("overview") if isinstance(fundamental, Mapping) else None
    quarters = fundamental.get("quarters") if isinstance(fundamental, Mapping) else None
    overview = overview if isinstance(overview, Mapping) else {}
    quarters = quarters if isinstance(quarters, Mapping) else {}

    rows = quarters.get("rows")
    rows = rows if isinstance(rows, Mapping) else {}
    eps_row: Sequence[Any] = []
    for key, value in rows.items():
        if str(key).strip().lower().startswith("eps") and isinstance(value, Sequence):
            eps_row = value
            break

    return trailing_pe(overview.get("Current Price"), eps_row, quarters.get("headers"))
