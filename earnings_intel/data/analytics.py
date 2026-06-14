"""
Pure, deterministic analytics for the Fundamental Screener (no network, fully testable).

Given numeric series scraped from Screener, compute the automatic read-outs that
Screener shows: trend consistency, cyclical pattern, growth-vs-price, money flow,
and a standard two-stage DCF / reverse-DCF.

Everything here is automatic — inputs are derived from the company's own numbers.
"""
from __future__ import annotations

import re
from typing import Optional

# Calendar order for quarter labels like "Mar 2026"
_MON = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
        "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}


def to_float(s) -> Optional[float]:
    """'₹ 1,234 Cr' -> 1234.0 ; '-2%' -> -2.0 ; '' -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d[\d,]*(\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def floats(seq) -> list:
    return [to_float(x) for x in (seq or [])]


# --------------------------------------------------------------------- trends
def trend_of(series, unit: str = "yrs") -> dict:
    """Classify the recent direction of a numeric series (oldest->newest).

    Returns {label, n, unit}: label in Increasing/Decreasing/Inconsistent,
    n = how many recent periods that read holds for.
    """
    vals = [v for v in floats(series) if v is not None]
    if len(vals) < 2:
        return {"label": "n/a", "n": 0, "unit": unit}
    diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    last = diffs[-1]
    sign = (last > 0) - (last < 0)
    if sign == 0:
        return {"label": "Inconsistent", "n": 1, "unit": unit}
    run = 0
    for d in reversed(diffs):
        s = (d > 0) - (d < 0)
        if s == sign and s != 0:
            run += 1
        else:
            break
    # need a clean run of >=3 steps to call a sustained trend, else inconsistent
    if run >= 3:
        return {"label": "Increasing" if sign > 0 else "Decreasing",
                "n": run + 1, "unit": unit}
    return {"label": "Inconsistent", "n": run + 1, "unit": unit}


def classify_trends(yearly: dict, quarterly: dict) -> dict:
    """yearly/quarterly are {metric: series}. Returns {metric: trend_of(...)}.."""
    out = {"yearly": {}, "quarterly": {}}
    for k, ser in (yearly or {}).items():
        out["yearly"][k] = trend_of(ser, "yrs")
    for k, ser in (quarterly or {}).items():
        out["quarterly"][k] = trend_of(ser, "qtrs")
    return out


# ------------------------------------------------------------------ cyclical
def cyclical(headers: list, profit_series) -> dict:
    """Detect recurring positive quarters by calendar month.

    Looks at QoQ change of net profit and tallies, per calendar quarter-month,
    how often it was positive. A month positive in >=60% of years -> 'positive'.
    """
    vals = floats(profit_series)
    months = []
    for h in (headers or []):
        m = re.search(r"[A-Za-z]{3}", str(h))
        months.append(_MON.get(m.group(0).lower()) if m else None)
    tally: dict = {}
    for i in range(1, len(vals)):
        if vals[i] is None or vals[i - 1] is None or months[i] is None:
            continue
        up = vals[i] > vals[i - 1]
        t = tally.setdefault(months[i], [0, 0])
        t[0] += 1 if up else 0
        t[1] += 1
    pos, neg = [], []
    for mth, (u, tot) in sorted(tally.items()):
        if tot < 2:
            continue
        (pos if u / tot >= 0.6 else neg).append(mth)
    inv = {v: k for k, v in _MON.items()}
    name = lambda ms: [inv[m].capitalize() for m in ms]
    cyc = "CYCLICAL" if (pos and len(pos) <= 2) else "NON-CYCLICAL"
    return {"label": cyc, "positive_quarters": name(pos), "negative_quarters": name(neg)}


# --------------------------------------------------- growth vs price (insight)
def growth_vs_price(profit_cagr_5y, price_cagr_5y, profit_growth_recent=None,
                    price_growth_recent=None) -> dict:
    p = to_float(profit_cagr_5y)
    s = to_float(price_cagr_5y)
    long_txt = "Insufficient data."
    if p is not None and s is not None:
        if abs(p - s) <= 3:
            long_txt = "Long-term profit and price growth are balanced, suggesting fair valuation."
        elif p > s:
            long_txt = (f"Profit grew {p:.0f}% vs price {s:.0f}% (5Y) — fundamentals "
                        "outpacing the stock; potentially undervalued.")
        else:
            long_txt = (f"Price grew {s:.0f}% vs profit {p:.0f}% (5Y) — stock ahead of "
                        "fundamentals; watch valuation.")
    recent_txt = None
    pr, sr = to_float(profit_growth_recent), to_float(price_growth_recent)
    if pr is not None and sr is not None:
        gap = pr - sr
        ratio = (pr / sr) if (sr and sr > 0) else None
        if gap > 0:
            recent_txt = (f"Recently, profit growth exceeds 1-year price growth by "
                          f"{gap:.0f}%" + (f" (ratio {ratio:.2f})" if ratio else "") +
                          ", the company is starting to outperform expectations.")
        else:
            recent_txt = (f"Recently, price growth exceeds profit growth by {abs(gap):.0f}% "
                          "— expectations running ahead of delivery.")
    return {"label": "FUNDAMENTALS-LED" if (p and s and p >= s) else "PRICE-LED",
            "long": long_txt, "recent": recent_txt}


# ------------------------------------------------------------------ money flow
def money_flow(sh_headers: list, fii: list, dii: list) -> dict:
    """Approximate institutional money flow from change in FII+DII holding %."""
    f = floats(fii); d = floats(dii)
    def last2(x):
        v = [z for z in x if z is not None]
        return (v[-2], v[-1]) if len(v) >= 2 else (None, None)
    f0, f1 = last2(f); d0, d1 = last2(d)
    if None in (f0, f1, d0, d1):
        return {"label": "NEUTRAL", "change": None,
                "note": "Insufficient shareholding history."}
    change = (f1 + d1) - (f0 + d0)
    lab = "POSITIVE MONEY FLOW" if change > 0 else ("NEGATIVE MONEY FLOW" if change < 0 else "NEUTRAL")
    return {"label": lab, "change": round(change, 2),
            "note": "Tracks institutional (FII+DII) holding change — proxy for smart-money flow."}


# ---------------------------------------------------------------------- DCF
def dcf(earnings: float, growth_pct: float, discount_pct: float = 10.0,
        term_growth_pct: float = 2.0, years: int = 10,
        terminal_multiple: Optional[float] = None, terminal: str = "gordon") -> dict:
    """Two-stage DCF on earnings (textbook). Terminal value via Gordon growth by
    default (self-limiting, r>g), or an exit multiple if terminal='multiple'.
    Returns yearly rows + valuation summary."""
    g = growth_pct / 100.0; r = discount_pct / 100.0; tg = term_growth_pct / 100.0
    rows = []; pv_sum = 0.0; e = float(earnings)
    for y in range(1, years + 1):
        e = e * (1 + g)
        pv = e / (1 + r) ** y
        pv_sum += pv
        rows.append({"year": y, "earnings": round(e), "growth": round(growth_pct, 2),
                     "pv": round(pv)})
    tycf = e * (1 + tg)
    if terminal == "multiple" and terminal_multiple:
        terminal_value = e * terminal_multiple
        eff_mult = terminal_multiple
    else:  # Gordon growth: TV = E_n*(1+tg)/(r-tg)
        eff_mult = (1 + tg) / (r - tg) if r > tg else 12.0
        terminal_value = e * eff_mult
    pv_terminal = terminal_value / (1 + r) ** years
    total = pv_sum + pv_terminal
    return {"rows": rows, "pv_1_n": round(pv_sum), "terminal_year_cf": round(tycf),
            "terminal_value": round(terminal_value), "pv_terminal": round(pv_terminal),
            "effective_multiple": round(eff_mult, 1), "total_pv": round(total)}


def reverse_dcf(market_cap: float, earnings: float, discount_pct: float = 10.0,
                term_growth_pct: float = 2.0, years: int = 10,
                terminal_multiple: Optional[float] = None) -> dict:
    """Solve the earnings growth implied by the current market cap (bisection)."""
    def total(g):
        return dcf(earnings, g, discount_pct, term_growth_pct, years,
                   terminal_multiple)["total_pv"]
    lo, hi = -50.0, 100.0
    if total(lo) > market_cap:
        return {"implied_growth": lo, "note": "Price below model floor"}
    if total(hi) < market_cap:
        return {"implied_growth": hi, "note": "Price above model ceiling"}
    for _ in range(60):
        mid = (lo + hi) / 2
        if total(mid) < market_cap:
            lo = mid
        else:
            hi = mid
    g = round((lo + hi) / 2, 2)
    detail = dcf(earnings, g, discount_pct, term_growth_pct, years, terminal_multiple)
    detail["implied_growth"] = g
    return detail


def auto_dcf(overview: dict, growth: dict, profit_loss: dict) -> dict:
    """Derive DCF/Reverse-DCF inputs automatically from the company's own numbers."""
    mcap = to_float(overview.get("Market Cap"))
    price = to_float(overview.get("Current Price"))
    pe = to_float(overview.get("Stock P/E"))
    # latest FY net profit
    np_series = floats((profit_loss or {}).get("rows", {}).get("Net Profit", []))
    earnings = next((v for v in reversed(np_series) if v not in (None, 0)), None)
    # profit CAGR: prefer 5Y then 3Y then 10Y
    pg = (growth or {}).get("Compounded Profit Growth", {})
    g = next((to_float(pg.get(k)) for k in ("5 Years", "3 Years", "10 Years")
              if to_float(pg.get(k)) is not None), None)
    if earnings is None or earnings <= 0:
        return {"ok": False, "reason": "no positive earnings"}
    g_base = max(0.0, min(g if g is not None else 10.0, 18.0))  # conservative stage-1 cap
    shares = (mcap / price) if (mcap and price) else None
    base = dcf(earnings, g_base, 10, 2, 10)            # Gordon terminal (bounded)
    rev = reverse_dcf(mcap, earnings, 10, 2, 10) if mcap else {}
    intrinsic_ps = (base["total_pv"] / shares) if shares else None
    mos = (round((intrinsic_ps - price) / intrinsic_ps * 100, 1)
           if (intrinsic_ps and price and intrinsic_ps) else None)
    return {"ok": True, "inputs": {"earnings": round(earnings), "growth": round(g_base, 2),
            "discount": 10, "terminal_growth": 2, "years": 10,
            "terminal_multiple": base.get("effective_multiple")},
            "dcf": base, "reverse": rev,
            "intrinsic_total": base["total_pv"],
            "intrinsic_per_share": round(intrinsic_ps) if intrinsic_ps else None,
            "current_price": price, "market_cap": mcap,
            "margin_of_safety": mos}
