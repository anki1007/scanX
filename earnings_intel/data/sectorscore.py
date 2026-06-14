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


def sector_score(rows: list) -> dict:
    pv = _med([r.get("profit_var") for r in rows])
    sv = _med([r.get("sales_var") for r in rows])
    roce = _med([r.get("roce") for r in rows])
    fii = _med([r.get("fii_chg") for r in rows])
    pos = []
    for r in rows:
        c, l, a = r.get("cmp"), r.get("low_52w"), r.get("ath")
        if c and l and a and a > l:
            pos.append((c - l) / (a - l))
    breadth = (sum(1 for p in pos if p > 0.5) / len(pos)) if pos else None

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
    pos = (c - lo) / (a - lo) if (c and lo and a and a > lo) else None
    mom = _clip((pos - 0.5) * 2) if pos is not None else 0.0
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
