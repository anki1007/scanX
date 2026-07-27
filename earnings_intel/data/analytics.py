"""
Pure, deterministic analytics for the Fundamental Screener (no network, fully testable).

Given numeric series scraped from Screener, compute the automatic read-outs that
Screener shows: trend consistency, cyclical pattern, growth-vs-price, money flow,
and a standard two-stage DCF / reverse-DCF.

Everything here is automatic — inputs are derived from the company's own numbers.
"""
from __future__ import annotations

import math
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


# ------------------------------------------------------------- health ratios
def _stmt_map(stmt: dict, prefix: str) -> dict:
    """{year-header: float} for the first row whose label starts with prefix."""
    st = stmt or {}
    rows = st.get("rows") or {}
    headers = st.get("headers") or []
    for label, vals in rows.items():
        if str(label).lower().startswith(prefix.lower()):
            return {h: to_float(v) for h, v in zip(headers, vals)}
    return {}


def _latest_common(a: dict, b: dict):
    """(year, a[year], b[year]) for the newest year present in both, else None."""
    common = [y for y in a if y in b and a[y] is not None and b[y] is not None]
    if not common:
        return None
    y = max(common)  # 'Mar YYYY' sorts correctly by the year suffix within same month word
    return y, a[y], b[y]


# --- optional Upstox fill-ins (earnings_intel.data.ratios) -------------------
#: The six Upstox key ratios shown against their sector benchmark, mapped to
#: (display label, lower reading is the better one).  Valuation multiples are
#: better low; return ratios are better high.
PEER_RATIOS: dict = {
    "pe":        ("P/E", True),
    "pb":        ("P/B", True),
    "roa":       ("ROA", False),
    "roe":       ("ROE", False),
    "roce":      ("ROCE", False),
    "ev_ebitda": ("EV/EBITDA", True),
}

#: A reading within +/-10% of the sector benchmark is "in line", not a verdict.
PEER_BAND = 0.10


def _fnum(x) -> Optional[float]:
    """Finite float or None. Bools, blanks and junk are not numbers."""
    if x is None or isinstance(x, bool):
        return None
    v = to_float(x)
    return v if (v is not None and math.isfinite(v)) else None


def _peer_bias(value: Optional[float], sector: Optional[float], lower_is_better: bool) -> str:
    """'positive' beats the sector, 'negative' trails it, 'neutral' inside the
    +/-10% band, 'na' with no benchmark to compare against."""
    if value is None or sector is None:
        return "na"
    if abs(value - sector) <= abs(sector) * PEER_BAND:
        return "neutral"
    better = value < sector if lower_is_better else value > sector
    return "positive" if better else "negative"


def _peers_from_extra(extra: dict) -> dict:
    """{key: {value, unit, sector, source, bias}} for whichever of the six exist."""
    out: dict = {}
    for key, (_label, lower_is_better) in PEER_RATIOS.items():
        row = extra.get(key)
        if not isinstance(row, dict):
            continue
        value = _fnum(row.get("value"))
        if value is None:
            continue
        sector = _fnum(row.get("sector"))
        out[key] = {"value": value,
                    "unit": str(row.get("unit") or "x"),
                    "sector": sector,
                    "source": str(row.get("source") or "upstox"),
                    "bias": _peer_bias(value, sector, lower_is_better)}
    return out


def _extra_parts(row):
    """(value, period, sector, source) from one ratios.py entry, else None."""
    if not isinstance(row, dict):
        return None
    value = _fnum(row.get("value"))
    if value is None:
        return None
    return (value, str(row.get("period") or "").strip(),
            _fnum(row.get("sector")), str(row.get("source") or "upstox"))


def _tag(base: dict, period: str, sector: Optional[float], source: str) -> dict:
    """Same shape as the Screener-computed entries + provenance, so the UI is unchanged."""
    out = dict(base)
    if period:
        out["year"] = period
    out["source"] = source
    if sector is not None:
        out["sector"] = sector
    return out


def _current_ratio_from_extra(row) -> Optional[dict]:
    """Upstox Current Assets / Current Liabilities -> a current_ratio entry."""
    got = _extra_parts(row)
    if got is None:
        return None
    value, period, sector, source = got
    v = round(value, 2)
    bias = "positive" if v >= 2.0 else "neutral" if v >= 1.0 else "negative"
    reading = ("comfortable short-term liquidity" if v >= 2.0 else
               "adequate but thin buffer" if v >= 1.0 else
               "current liabilities exceed current assets")
    where = f" ({period})" if period else ""
    note = f"{v}x{where}: {reading} — from the Upstox balance sheet"
    return _tag({"value": v, "bias": bias, "note": note}, period, sector, source)


def _debt_equity_from_extra(row) -> Optional[dict]:
    """Upstox Non-Current Liabilities / Equity Capital -> a debt_equity PROXY entry.

    Flagged ``"proxy": True`` and said plainly in the note: this is not
    borrowings / net worth, so it must never be read as an exact D/E.
    """
    got = _extra_parts(row)
    if got is None:
        return None
    value, period, sector, source = got
    v = round(value, 2)
    bias = "positive" if v <= 0.30 else "neutral" if v <= 1.0 else "negative"
    reading = ("conservatively financed" if v <= 0.30 else
               "moderate leverage" if v <= 1.0 else
               "non-current liabilities exceed equity capital — leverage risk")
    where = f" ({period})" if period else ""
    note = (f"{v}x{where}: {reading} · PROXY — non-current liabilities / equity capital "
            "from the Upstox balance sheet, not borrowings / net worth")
    out = _tag({"value": v, "bias": bias, "note": note}, period, sector, source)
    out["proxy"] = True
    return out


def health_ratios(bs: dict, cf: dict, pl: dict, extra: Optional[dict] = None) -> dict:
    """Financial-health ratios with a +/-/neutral bias tag each (todo.md items a-c).

    - current_ratio: Current Assets / Current Liabilities. Screener's compact
      balance sheet doesn't carry the current split, so this computes only when
      the expanded schedule rows are present and is honestly 'na' otherwise.
    - ocf_np: Operating Cash Flow / Net Profit for the latest common year;
      >= 1 means reported profit is fully backed by cash.
    - cwip: capex-completion signal — a large CWIP drawdown means construction
      finished and capacity is about to commission (todo.md's 100 -> 30 case);
      a large build-up means an investment phase is underway.

    ``extra`` is an optional :func:`earnings_intel.data.ratios.fetch_ratios`
    result.  With it absent the output is exactly what it has always been.  With
    it present:

    - a current_ratio / debt_equity that Screener's compact statements could NOT
      produce (bias 'na') is filled from ``extra["current_ratio"]`` /
      ``extra["debt_equity_proxy"]`` — same keys as always plus ``source`` (and
      ``sector`` when a benchmark exists) so a reader can see where the number
      came from.  Screener wins whenever it has an answer, and a real finding
      such as a non-positive net worth is never overwritten by the proxy.
    - the six Upstox key ratios land under ``peers`` as
      ``{key: {value, unit, sector, source, bias}}``; bias is 'positive' when the
      company beats its sector, allowing for a +/-10% in-line band, with LOWER
      better for pe/pb/ev_ebitda and HIGHER better for roa/roe/roce.

    ``peers`` is omitted entirely when there is nothing to show, so a bundle
    baked without a token looks exactly like today's.
    """
    out: dict = {}

    ca = _stmt_map(bs, "Total Current Assets") or _stmt_map(bs, "Current Assets")
    cl = _stmt_map(bs, "Total Current Liabilities") or _stmt_map(bs, "Current Liabilities")
    got = _latest_common(ca, cl) if (ca and cl) else None
    if got and got[2]:
        y, a, l = got
        v = round(a / l, 2)
        # Smart-Ratios doc Tier 2: ideal Current Ratio > 2
        bias = "positive" if v >= 2.0 else "neutral" if v >= 1.0 else "negative"
        note = (f"{v}x ({y}): " +
                ("comfortable short-term liquidity" if v >= 2.0 else
                 "adequate but thin buffer" if v >= 1.0 else
                 "current liabilities exceed current assets"))
        out["current_ratio"] = {"value": v, "year": y, "bias": bias, "note": note}
    else:
        out["current_ratio"] = {"value": None, "bias": "na",
                                "note": "needs the expanded balance-sheet schedule "
                                        "(not in Screener's compact view)"}

    ocf = _stmt_map(cf, "Cash from Operating")
    np_ = _stmt_map(pl, "Net Profit")
    got = _latest_common(ocf, np_) if (ocf and np_) else None
    if got and got[2]:
        y, o, n = got
        v = round(o / n, 2) if n else None
        if o < 0:
            bias, note = "negative", f"operating cash flow is NEGATIVE in {y}"
        elif n < 0:
            bias, note = "neutral", f"loss year {y} — ratio not meaningful, but OCF is positive"
        elif v >= 1.0:
            bias, note = "positive", f"{v}x ({y}): profits fully backed by cash"
        elif v >= 0.6:
            bias, note = "neutral", f"{v}x ({y}): moderate cash conversion"
        else:
            bias, note = "negative", f"{v}x ({y}): profits far ahead of cash — check receivables/accruals"
        out["ocf_np"] = {"value": v, "year": y, "bias": bias, "note": note}
    else:
        out["ocf_np"] = {"value": None, "bias": "na", "note": "cash-flow or P&L data missing"}

    # Debt / Equity = Borrowings / (Equity Capital + Reserves).
    # Smart-Ratios doc: < 0.30 is the multibagger ideal (Tier 2 / top-10 #7).
    borrow = _stmt_map(bs, "Borrowings")
    eqc = _stmt_map(bs, "Equity Capital")
    resv = _stmt_map(bs, "Reserves")
    nw = {y: eqc[y] + resv[y] for y in eqc
          if y in resv and eqc[y] is not None and resv[y] is not None}
    got = _latest_common(borrow, nw) if (borrow and nw) else None
    if got is not None:
        y, b, w = got
        if w <= 0:
            out["debt_equity"] = {"value": None, "year": y, "bias": "negative",
                                  "note": f"net worth is non-positive in {y} — balance-sheet stress"}
        else:
            v = round(b / w, 2)
            bias = "positive" if v <= 0.30 else "neutral" if v <= 1.0 else "negative"
            note = (f"{v}x ({y}): " +
                    ("conservatively financed" if v <= 0.30 else
                     "moderate leverage" if v <= 1.0 else
                     "debt exceeds net worth — leverage risk"))
            out["debt_equity"] = {"value": v, "year": y, "bias": bias, "note": note}
    else:
        out["debt_equity"] = {"value": None, "bias": "na", "note": "balance-sheet data missing"}

    cwip = _stmt_map(bs, "CWIP")
    ta = _stmt_map(bs, "Total Assets")
    series = [(y, v) for y, v in sorted(cwip.items()) if v is not None]
    if len(series) >= 2:
        (py, pv), (ly, lv) = series[-2], series[-1]
        assets = ta.get(ly) or 0
        signif = pv > 0 and (not assets or pv >= 0.01 * assets)
        pct = round((lv - pv) / pv * 100, 1) if pv else None
        if pct is not None and pct <= -30 and signif:
            bias = "positive"
            note = (f"CWIP down {abs(pct)}% ({py.split()[-1]}->{ly.split()[-1]}): "
                    "capex largely completed — new capacity coming online")
        elif pct is not None and pct >= 30:
            bias = "neutral"
            note = (f"CWIP up {pct}% ({py.split()[-1]}->{ly.split()[-1]}): "
                    "capex build-up phase — growth spend underway, watch for completion")
        else:
            bias = "neutral"
            note = f"CWIP steady ({py.split()[-1]}->{ly.split()[-1]})"
        out["cwip"] = {"latest": lv, "prev": pv, "pct_change": pct,
                       "year": ly, "bias": bias, "note": note}
    else:
        out["cwip"] = {"latest": series[-1][1] if series else None, "prev": None,
                       "pct_change": None, "bias": "na", "note": "CWIP history unavailable"}

    if not isinstance(extra, dict) or not extra:
        return out

    def _missing(key: str) -> bool:
        """True only when Screener had NO data — a real finding is never overwritten."""
        cur = out.get(key) or {}
        return cur.get("value") is None and cur.get("bias") == "na"

    if _missing("current_ratio"):
        filled = _current_ratio_from_extra(extra.get("current_ratio"))
        if filled:
            out["current_ratio"] = filled
    if _missing("debt_equity"):
        filled = _debt_equity_from_extra(extra.get("debt_equity_proxy"))
        if filled:
            out["debt_equity"] = filled

    peers = _peers_from_extra(extra)
    if peers:
        out["peers"] = peers
    return out
