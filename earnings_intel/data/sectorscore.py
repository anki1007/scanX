"""
Sector Headwind / Tailwind score (pure, deterministic, testable).

Each sector is scored from its constituents' Screener metrics:
  momentum  = median quarterly profit + sales growth  (earnings tailwind)
  strength  = breadth: share of stocks in the upper half of their 52w range
  flow      = median change in FII holding            (institutional flow)
  quality   = median 3Y ROCE
Composite ~ -2 (strong headwind) .. +2 (strong tailwind); 0 = neutral.
Full-market score = market-cap-weighted mean of sector scores.
"""
from __future__ import annotations

import re
from statistics import median


def _med(xs):
    v = [x for x in xs if x is not None]
    return median(v) if v else None


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _r(x, n=2):
    return None if x is None else round(x, n)


def _signal(score):
    return "TAILWIND" if score >= 0.5 else ("HEADWIND" if score <= -0.5 else "NEUTRAL")


def _range_position(r: dict):
    """Where a stock sits in its range, 0..1, or None.

    pos_52w first: it is the 52-week position computed from real price history
    in the bundle, and "upper half of the 52-week range" is what the board says
    breadth means. The cmp/low_52w/ath formula is kept for rows that carry
    those columns -- but no page the scraper reads has ever supplied low_52w
    or ath, so on its own it left breadth null for all 22 sectors and 35% of
    every sector score was a constant zero.
    """
    p52 = r.get("pos_52w")
    if p52 is not None:
        try:
            v = float(p52) / 100.0
        except (TypeError, ValueError):
            v = None
        if v is not None and 0.0 <= v <= 1.0:
            return v
    c, l, a = r.get("cmp"), r.get("low_52w"), r.get("ath")
    if c and l and a and a > l:
        return (c - l) / (a - l)
    return None


def _breadth(rows: list):
    pos = [p for p in (_range_position(r) for r in rows) if p is not None]
    return (sum(1 for p in pos if p > 0.5) / len(pos)) if pos else None


def sector_score(rows: list) -> dict:
    pv = _med([r.get("profit_var") for r in rows])
    sv = _med([r.get("sales_var") for r in rows])
    roce = _med([r.get("roce") for r in rows])
    fii = _med([r.get("fii_chg") for r in rows])
    breadth = _breadth(rows)

    mom = _clip(((pv or 0) + (sv or 0)) / 40.0)
    strength = _clip((breadth - 0.5) * 2) if breadth is not None else 0.0
    flow = _clip((fii or 0) / 1.0)
    quality = _clip(((roce if roce is not None else 12) - 12) / 12.0)
    score = round(2 * (0.40 * mom + 0.35 * strength + 0.15 * flow + 0.10 * quality), 3)
    return {
        "score": score, "signal": _signal(score), "n": len(rows),
        "median_profit_var": _r(pv), "median_sales_var": _r(sv),
        "median_roce": _r(roce), "median_fii_chg": _r(fii),
        "breadth_pct": (round(breadth * 100, 1) if breadth is not None else None),
        "components": {"momentum": round(mom, 2), "strength": round(strength, 2),
                       "flow": round(flow, 2), "quality": round(quality, 2)},
    }


def market_tailwind(rows: list) -> dict:
    by = {}
    for r in rows:
        by.setdefault(r.get("sector", "Unknown"), []).append(r)
    sectors, tot_mc, wsum = [], 0.0, 0.0
    for name, rs in by.items():
        sc = sector_score(rs); sc["sector"] = name
        sc["sector_code"] = next((r.get("sector_code") for r in rs if r.get("sector_code")), None)
        mc = sum((r.get("mcap") or 0) for r in rs); sc["mcap"] = round(mc)
        sectors.append(sc); tot_mc += mc; wsum += sc["score"] * mc
    sectors.sort(key=lambda s: s["score"], reverse=True)
    full = round(wsum / tot_mc, 3) if tot_mc else 0.0
    return {"full_market": {"score": full, "signal": _signal(full),
                            "companies": len(rows), "sectors": len(sectors)},
            "sectors": sectors}


def stock_signal(r: dict) -> dict:
    """Per-stock tailwind read for the sector drill-down: price momentum + earnings,
    a TAILWIND/NEUTRAL/HEADWIND signal, and a PEAD-style result score (0..100)."""
    pv = r.get("profit_var"); sv = r.get("sales_var")
    c = r.get("cmp"); lo = r.get("low_52w"); a = r.get("ath")
    # A screen returns neither a 52-week low nor an all-time high, so this used
    # to leave pos None and mom 0.0 on EVERY row: the "% of ATH" column was
    # empty market-wide and "price momentum + earnings" was earnings alone.
    # rs_rating is a 0-100 strength rating against the index; centring it on 50
    # maps it onto this function's -1..+1 momentum scale.
    rs = r.get("rs_rating")
    pos = (c - lo) / (a - lo) if (c and lo and a and a > lo) else None
    if rs is not None:
        try:
            mom = _clip(float(rs) / 50.0 - 1.0)
        except (TypeError, ValueError):
            mom = 0.0
    else:
        mom = _clip((pos - 0.5) * 2) if pos is not None else 0.0
    if r.get("pos_52w") is not None:
        try:
            pos = float(r["pos_52w"]) / 100.0
        except (TypeError, ValueError):
            pass
    earn = _clip(((pv or 0) + (sv or 0)) / 40.0)
    score = round(2 * (0.5 * mom + 0.5 * earn), 2)
    res = 50.0
    if pv is not None:
        res += 16 if pv > 20 else (8 if pv > 0 else -16)
    if sv is not None:
        res += 9 if sv > 15 else (4 if sv > 0 else -8)
    return {"signal": _signal(score), "sscore": score,
            "result": int(max(0, min(100, round(res)))),
            "pos": (round(pos * 100, 1) if pos is not None else None)}


# ----------------------------------------------------- real FII flow injection
_FII_ALIASES = {
    "fast moving consumer goods": "fmcg",
    "automobile and auto components": "automobile auto components",
    "oil gas and consumable fuels": "oil gas consumable fuels",
}


def _norm_name(s):
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return _FII_ALIASES.get(s, s)


def blend_fii(result: dict, fii_rows: list, scale: float = 3000.0) -> dict:
    """Inject real per-sector FII *fortnight* net flow (₹ Cr, from /fii/) into each
    sector's previously-zero 'flow' component, then recompute score/signal and the
    market-cap-weighted full-market score. Joins by sector code first, else name.
    Mutates and returns `result`. Pure/deterministic given inputs."""
    by_code, by_name = {}, {}
    for f in fii_rows or []:
        if f.get("fortnight") is None:
            continue
        if f.get("code"):
            by_code[f["code"]] = f
        by_name[_norm_name(f.get("sector"))] = f
    tot_mc = wsum = 0.0
    for sc in result.get("sectors", []):
        f = by_code.get(sc.get("sector_code")) or by_name.get(_norm_name(sc.get("sector")))
        if f:
            flow = _clip(f["fortnight"] / scale)
            comp = sc["components"]; comp["flow"] = round(flow, 2)
            sc["fii_fortnight"] = round(f["fortnight"])
            sc["fii_1y"] = (round(f["oneY"]) if f.get("oneY") is not None else None)
            sc["fii_aum"] = f.get("aum")
            sc["score"] = round(2 * (0.40 * comp["momentum"] + 0.35 * comp["strength"]
                                     + 0.15 * flow + 0.10 * comp["quality"]), 3)
            sc["signal"] = _signal(sc["score"])
        mc = sc.get("mcap") or 0
        tot_mc += mc; wsum += sc["score"] * mc
    result.get("sectors", []).sort(key=lambda s: s["score"], reverse=True)
    if tot_mc and result.get("full_market"):
        full = round(wsum / tot_mc, 3)
        result["full_market"]["score"] = full
        result["full_market"]["signal"] = _signal(full)
    return result
