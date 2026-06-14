"""
TechnoFunda signal — a transparent, rules-based BUY / SELL / NEUTRAL screen.

It blends three disclosed sub-scores (each 0..100, 50 = neutral):
  • Results outperformance  — PEAD-style: latest-quarter sales/profit/EPS growth,
    QoQ + YoY, acceleration and margin expansion.
  • Relative strength / technical — RS rating vs Nifty-500, 50/200-DMA trend,
    golden cross, 52-week position.
  • Fundamental quality / valuation — multi-year trends, ROE/ROCE, DCF margin of
    safety, institutional money flow.

A BUY requires *confluence* (technical AND fundamental both supportive) — that is
the "technofunda" idea. Everything is deterministic and shown to the user.

NOT investment advice — a research/education screen. Do your own due diligence.
"""
from __future__ import annotations

from .analytics import to_float, floats

DISCLAIMER = ("Rules-based screen for research/education only — not personalized "
              "investment advice. Verify independently before any decision.")


def _clip(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _grow(series, lag):
    v = [x for x in floats(series)]
    if len(v) <= lag or v[-1] is None or v[-1 - lag] is None or v[-1 - lag] == 0:
        return None
    return (v[-1] - v[-1 - lag]) / abs(v[-1 - lag]) * 100


# ----------------------------------------------------- results outperformance
def score_results(fund: dict) -> dict:
    q = (fund.get("quarters") or {}).get("rows", {})
    sales, npf, eps, opm = q.get("Sales"), q.get("Net Profit"), q.get("EPS"), q.get("OPM")
    sy, sq = _grow(sales, 4), _grow(sales, 1)
    ny, nq = _grow(npf, 4), _grow(npf, 1)
    ey = _grow(eps, 4)
    # acceleration: latest YoY vs the prior quarter's YoY (needs 6 points)
    accel = None
    nv = floats(npf or [])
    if len(nv) >= 6 and None not in (nv[-1], nv[-5], nv[-2], nv[-6]) and nv[-5] and nv[-6]:
        yoy_now = (nv[-1] - nv[-5]) / abs(nv[-5]) * 100
        yoy_prev = (nv[-2] - nv[-6]) / abs(nv[-6]) * 100
        accel = yoy_now - yoy_prev
    ov = floats(opm or [])
    opm_exp = (ov[-1] - ov[-5]) if (len(ov) >= 5 and None not in (ov[-1], ov[-5])) else None

    s = 50.0; why = []
    def add(cond, pts, msg):
        nonlocal s
        if cond:
            s += pts; (why.append(msg) if pts > 0 else why.append(msg))
    add(sy is not None and sy > 0, 8, f"Sales +{sy:.0f}% YoY" if sy else "")
    add(sy is not None and sy > 15, 6, "strong sales growth")
    add(ny is not None and ny > 0, 10, f"Net profit +{ny:.0f}% YoY" if ny else "")
    add(ny is not None and ny > 20, 8, "profit growth >20% YoY")
    add(nq is not None and nq > 0, 5, "profit up QoQ")
    add(sq is not None and sq > 0, 3, "sales up QoQ")
    add(accel is not None and accel > 0, 8, "earnings growth accelerating")
    add(opm_exp is not None and opm_exp > 0, 5, "margins expanding")
    # penalties
    add(ny is not None and ny < 0, -14, f"Net profit {ny:.0f}% YoY" if ny else "")
    add(sy is not None and sy < 0, -8, f"Sales {sy:.0f}% YoY" if sy else "")
    add(accel is not None and accel < 0, -6, "earnings growth decelerating")
    return {"score": round(_clip(s)), "reasons": [w for w in why if w],
            "metrics": {"sales_yoy": _r(sy), "np_yoy": _r(ny), "np_qoq": _r(nq),
                        "eps_yoy": _r(ey), "accel": _r(accel), "opm_exp": _r(opm_exp)}}


# --------------------------------------------------- fundamental quality / val
def score_fundamental(fund: dict) -> dict:
    an = fund.get("analysis") or {}
    ov = fund.get("overview") or {}
    tr = (an.get("trends") or {}).get("yearly", {})
    dcf = an.get("dcf") or {}
    mf = an.get("money_flow") or {}
    roe = to_float(ov.get("ROE")); roce = to_float(ov.get("ROCE"))
    mos = dcf.get("margin_of_safety")

    s = 50.0; why = []
    def lab(k):
        return (tr.get(k) or {}).get("label")
    if lab("Sales") == "Increasing": s += 6; why.append("multi-year sales uptrend")
    if lab("Net Profit") == "Increasing": s += 7; why.append("multi-year profit uptrend")
    if lab("EPS") == "Increasing": s += 5; why.append("rising EPS trend")
    if roe is not None and roe >= 15: s += 7; why.append(f"ROE {roe:.0f}%")
    elif roe is not None and roe < 8: s -= 6; why.append(f"low ROE {roe:.0f}%")
    if roce is not None and roce >= 15: s += 5; why.append(f"ROCE {roce:.0f}%")
    if mos is not None and mos >= 25: s += 12; why.append(f"DCF margin of safety {mos:.0f}%")
    elif mos is not None and mos > 0: s += 5; why.append("trades below DCF value")
    elif mos is not None and mos <= -25: s -= 12; why.append(f"DCF overvalued ({mos:.0f}%)")
    if (mf.get("label") or "").startswith("POSITIVE"): s += 5; why.append("institutional inflow")
    elif (mf.get("label") or "").startswith("NEGATIVE"): s -= 3; why.append("institutional outflow")
    return {"score": round(_clip(s)), "reasons": why,
            "metrics": {"roe": roe, "roce": roce, "dcf_mos": mos}}


# ------------------------------------------------------ relative strength / tech
def score_technical(tech: dict) -> dict:
    if not tech:
        return {"score": 50, "reasons": [], "metrics": {}}
    rs = tech.get("rs_rating")
    s = float(rs) if rs is not None else 50.0
    why = []
    if rs is not None and rs >= 70: why.append(f"RS rating {rs} (market-beating)")
    elif rs is not None and rs <= 30: why.append(f"RS rating {rs} (lagging market)")
    if tech.get("excess_3m") is not None and tech["excess_3m"] > 0:
        why.append(f"+{tech['excess_3m']:.0f}% vs Nifty 500 (3M)")
    if tech.get("above_200dma"): s += 8; why.append("above 200-DMA")
    elif tech.get("above_200dma") is False: s -= 8; why.append("below 200-DMA")
    if tech.get("golden_cross"): s += 6; why.append("golden cross (50>200)")
    if tech.get("above_50dma"): s += 4
    if tech.get("pos_52w") is not None and tech["pos_52w"] > 70:
        s += 6; why.append("near 52-week high")
    elif tech.get("pos_52w") is not None and tech["pos_52w"] < 12:
        s -= 6; why.append("near 52-week low")
    return {"score": round(_clip(s)), "reasons": why,
            "metrics": {"rs_rating": rs, "excess_3m": tech.get("excess_3m"),
                        "excess_12m": tech.get("excess_12m"),
                        "above_200dma": tech.get("above_200dma"),
                        "golden_cross": tech.get("golden_cross"),
                        "pos_52w": tech.get("pos_52w")}}


def _r(x, n=1):
    return None if x is None else round(x, n)


# --------------------------------------------------------------- final verdict
WEIGHTS = {"results": 0.30, "technical": 0.35, "fundamental": 0.35}


def technofunda_signal(fund: dict, price: dict, sector: dict = None) -> dict:
    res = score_results(fund)
    fnd = score_fundamental(fund)
    tec = score_technical((price or {}).get("technical") or {})
    R, T, F = res["score"], tec["score"], fnd["score"]
    composite = round(WEIGHTS["results"] * R + WEIGHTS["technical"] * T
                      + WEIGHTS["fundamental"] * F)
    mos = (fund.get("analysis") or {}).get("dcf", {}).get("margin_of_safety")

    # confluence gates
    if composite >= 62 and T >= 52 and R >= 48 and F >= 50:
        label = "BUY"
    elif composite <= 42 or (T < 38 and (R < 42 or F < 45)) or (mos is not None and mos <= -40 and T < 45):
        label = "SELL"
    else:
        label = "NEUTRAL"

    spread = max(R, T, F) - min(R, T, F)
    dist = abs(composite - 52)
    conf = "High" if (spread <= 20 and dist >= 14) else ("Low" if spread >= 38 else "Medium")

    pos = []
    for blk in (res, fnd, tec):
        pos += blk["reasons"]
    NEG = ("low ", "below", "lagging", "overvalued", "outflow", "deceler")
    def is_neg(r):
        rl = r.lower()
        return any(w in rl for w in NEG) or r.startswith("Net profit -") or r.startswith("Sales -")
    pros = [r for r in pos if not is_neg(r)]
    cons = [r for r in pos if is_neg(r)]

    out = {
        "label": label, "composite": composite, "confidence": conf,
        "blocks": {"results": res, "technical": tec, "fundamental": fnd},
        "weights": WEIGHTS,
        "reasons_pos": [r for r in dict.fromkeys(pros)][:6],
        "reasons_neg": [r for r in dict.fromkeys(cons)][:6],
        "ticker": (price or {}).get("ticker"),
        "disclaimer": DISCLAIMER,
    }
    out["bias_check"] = bias_check(fund, price, out, sector)
    return out


# ------------------------------------------------------------- bias guardrails
# Turns the "Insider Bias" lessons into data-driven flags on the verdict, so a
# strong story never hides a rich price, a weak sector, or a missed turnaround.
_BIAS_PRINCIPLE = ("Markets price expectations, not your conviction \u2014 weigh "
                   "valuation, the macro and the data, not just the story.")


def bias_check(fund: dict, price: dict, sig: dict, sector: dict = None) -> dict:
    """Deterministic 'insider-bias' guardrails layered on the TechnoFunda verdict.

    Each flag maps a human bias from the article to a number we already compute:
      - rich valuation behind a strong chart   (KPIT)
      - chasing extended price / momentum       (expectations, not experience)
      - a strong company inside a weak sector   (Mazagon / Army officer macro)
      - improving results but a cautious tape   (Union Bank turnaround)
      - loyalty / no position cap on a BUY       (Amazon)
    Returns {risk, principle, flags:[{level,title,note,lesson}], source}.
    """
    ov = (fund or {}).get("overview") or {}
    an = (fund or {}).get("analysis") or {}
    tech = (price or {}).get("technical") or {}
    blocks = (sig or {}).get("blocks") or {}
    label = (sig or {}).get("label")
    R = (blocks.get("results") or {}).get("score")
    T = (blocks.get("technical") or {}).get("score")
    rmet = (blocks.get("results") or {}).get("metrics") or {}

    pe = to_float(ov.get("Stock P/E"))
    cmp_ = to_float(ov.get("Current Price"))
    bv = to_float(ov.get("Book Value"))
    pb = (cmp_ / bv) if (cmp_ and bv and bv > 0) else None
    mos = (an.get("dcf") or {}).get("margin_of_safety")     # +ve = undervalued
    rs = tech.get("rs_rating"); pos52 = tech.get("pos_52w"); dist_hi = tech.get("dist_52w_high")
    np_yoy = rmet.get("np_yoy"); accel = rmet.get("accel")

    flags = []
    def add(level, title, note, lesson):
        flags.append({"level": level, "title": title, "note": note, "lesson": lesson})

    # 1) valuation blind-spot (KPIT) -----------------------------------------
    rich = ((pe is not None and pe >= 60) or (pb is not None and pb >= 8)
            or (mos is not None and mos <= -25))
    very_rich = ((pe is not None and pe >= 90) or (pb is not None and pb >= 14)
                 or (mos is not None and mos <= -50))
    if rich:
        parts = []
        if pe is not None and pe > 0: parts.append("P/E %.0f" % pe)
        if pb is not None and pb > 0: parts.append("P/B %.1f" % pb)
        if mos is not None and mos <= -25: parts.append("DCF overvalued %.0f%%" % abs(mos))
        note = "Rich valuation \u2014 " + ", ".join(parts) + ". "
        note += ("A market-beating chart doesn't make a rich entry safe; the price "
                 "already discounts a lot of the good news."
                 if (label == "BUY" or (T is not None and T >= 60))
                 else "Even with a decent story, a lot of optimism is already in the price.")
        add("warn" if very_rich else "caution", "Valuation blind-spot", note,
            "KPIT: strong EV/software story, valuation ignored \u2192 about \u221230%.")

    # 2) chasing / extended (expectations, not experience) -------------------
    if (not very_rich and pos52 is not None and pos52 >= 88
            and rs is not None and rs >= 80):
        note = ("Extended \u2014 %.0f%% up its 52-week range, RS %s" % (pos52, rs)) \
            + (", %.0f%% from the high" % dist_hi if dist_hi is not None else "") \
            + ". Momentum isn't an edge by itself \u2014 define risk/stop before chasing."
        add("caution", "Chasing strength", note,
            "The market prices expectations; a late entry carries the drawdown, not the edge.")

    # 3) bigger-picture / macro (Mazagon, Army officer) ----------------------
    if sector and sector.get("label"):
        slabel = sector.get("label"); sname = sector.get("name") or "its sector"
        sc = sector.get("score")
        sc_txt = (" (tailwind score %+.2f)" % sc) if isinstance(sc, (int, float)) else ""
        if slabel == "HEADWIND":
            add("warn", "Sector headwind",
                "%s is in a HEADWIND%s. A strong company can still de-rate with a weak "
                "sector \u2014 weigh the bigger picture, not just the single name." % (sname, sc_txt),
                "Mazagon / Army officer: too close to one view, missed the macro that moved price.")
        elif slabel == "TAILWIND":
            add("ok", "Sector tailwind",
                "%s is in a TAILWIND%s \u2014 the macro backdrop supports the setup." % (sname, sc_txt),
                "Focus on cycles and change, not insider chatter (the author's actual edge).")

    # 4) don't sell the turnaround (Union Bank) ------------------------------
    if (label in ("SELL", "NEUTRAL") and R is not None and R >= 60
            and np_yoy is not None and np_yoy > 0):
        note = ("Results are improving (results score %s/100, net profit %+.0f%% YoY%s) "
                "while the price/verdict stays cautious. Don't dismiss a turnaround on "
                "stale sentiment." % (R, np_yoy,
                                      ", accelerating" if (accel is not None and accel > 0) else ""))
        add("caution", "Turnaround vs. sentiment", note,
            "Union Bank: sold the 'dull PSU' near the bottom (about \u20b948), missed the re-rating to \u20b9140+.")

    # 5) loyalty / position size on a BUY (Amazon) ---------------------------
    if label == "BUY":
        add("info", "Size it \u2014 don't fall in love",
            "Even a clean BUY deserves a position cap and an exit plan. Concentrated "
            "conviction (loyalty to one name) is how good theses become big drawdowns.",
            "Amazon: a great company held with no diversification became the biggest single loss.")

    warn = any(f["level"] == "warn" for f in flags)
    caution = any(f["level"] == "caution" for f in flags)
    risk = "ELEVATED" if warn else ("MODERATE" if caution else "LOW")
    if not flags:
        add("ok", "No major bias red-flags",
            "Valuation, momentum and the macro are broadly consistent with the verdict. "
            "Still do your own diligence.",
            "Being aware of your biases \u2014 not proximity \u2014 is the edge.")
    return {"risk": risk, "principle": _BIAS_PRINCIPLE, "flags": flags,
            "source": "Insider-Bias checklist"}


def prescreen_score(row: dict) -> int:
    """Cheap rank from Screener-screen columns only (no per-stock fetch).

    Used to shortlist a large universe down to the best candidates before the
    expensive full TechnoFunda scoring. Emphasises outperforming results +
    quality + institutional inflow; relative strength is refined later.
    """
    s = 50.0
    pv = row.get("profit_var"); sv = row.get("sales_var")
    roce = row.get("roce"); fii = row.get("fii_chg"); pe = row.get("pe")
    if pv is not None:
        s += 14 if pv > 20 else (7 if pv > 0 else (-14 if pv < -10 else -5))
    if sv is not None:
        s += 9 if sv > 15 else (4 if sv > 0 else -6)
    if roce is not None:
        s += 8 if roce >= 20 else (4 if roce >= 12 else (-5 if roce < 6 else 0))
    if fii is not None:
        s += 5 if fii > 0 else (-3 if fii < 0 else 0)
    if pe is not None and 0 < pe <= 35:
        s += 3
    return int(max(0, min(100, round(s))))


def board_signal(row: dict) -> dict:
    """Fast market-wide TechnoFunda score from Screener-screen columns only
    (no per-stock fetch) so the board can cover the ENTIRE universe.

    results  = Qtr profit/sales variation (outperforming results)
    momentum = price position in its range (CMP vs 52w-low and all-time-high)
    quality  = ROCE + FII inflow + valuation
    Returns composite + BUY/SELL/NEUTRAL with the three sub-scores.
    """
    pv = row.get("profit_var"); sv = row.get("sales_var")
    roce = row.get("roce"); fii = row.get("fii_chg"); pe = row.get("pe")
    cmp_ = row.get("cmp"); lo = row.get("low_52w"); ath = row.get("ath")

    # --- results (outperforming results) ---
    r = 50.0
    if pv is not None:
        r += 16 if pv > 20 else (8 if pv > 0 else (-16 if pv < -10 else -6))
    if sv is not None:
        r += 10 if sv > 15 else (5 if sv > 0 else -8)
    results = _clip(r)

    # --- momentum (relative-strength proxy from price position) ---
    mom = 50.0
    pos = None
    if cmp_ and lo and ath and ath > 0 and lo > 0:
        above_low = cmp_ / lo - 1.0           # how far above the 52w low
        near_ath = cmp_ / ath                 # 1.0 = at all-time high
        mom = _clip(50 * min(max(near_ath, 0), 1.1) + 50 * min(max(above_low, 0), 1))
        pos = round(near_ath * 100, 1)

    # --- quality / value ---
    q = 50.0
    if roce is not None:
        q += 14 if roce >= 20 else (7 if roce >= 12 else (-10 if roce < 6 else 0))
    if fii is not None:
        q += 8 if fii > 0 else (-5 if fii < 0 else 0)
    if pe is not None and 0 < pe <= 35:
        q += 6
    elif pe is not None and pe > 80:
        q -= 6
    quality = _clip(q)

    composite = round(0.35 * results + 0.35 * mom + 0.30 * quality)
    if composite >= 62 and mom >= 50 and results >= 48:
        label = "BUY"
    elif composite <= 40 or (mom < 35 and results < 45):
        label = "SELL"
    else:
        label = "NEUTRAL"
    return {"composite": composite, "label": label, "results": round(results),
            "momentum": round(mom), "quality": round(quality), "pos_ath": pos}
