"""
Analysis Score — one 0-10 verdict per company, built from components you can audit.

Deterministic and offline: it reads the bundle and the SWOT that
``earnings_intel.data.swot`` already derived, so it runs for every company with
no API key and no network, and every component states the numbers behind it.

    from earnings_intel.data.score import analysis_score
    analysis_score(bundle, swot=bundle.get("swot"), sector=sector_row)

Six components, each scored 0-10 and then weighted:

    valuation         cheap vs sector and vs intrinsic value
    growth            compounded and recent growth
    financial_health  leverage, liquidity, cash backing of profit
    momentum          price trend and the strength of the latest results
    sector_tailwind   does the sector help or hurt
    red_flags         a PENALTY, subtracted — pledge, bias-check warnings, ...

A component with no usable input is dropped and its weight redistributed, so a
thin bundle yields an honest score over fewer parts rather than a confident
score built on zeros. ``components_missing`` names what was unavailable.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

log = logging.getLogger("technofunda.score")

# component -> (label, weight). Weights are relative; they are renormalised over
# whichever components actually had data.
WEIGHTS: dict[str, tuple[str, float]] = {
    "valuation": ("Valuation", 0.22),
    "growth": ("Growth", 0.24),
    "financial_health": ("Financial Health", 0.22),
    "momentum": ("Momentum", 0.20),
    "sector_tailwind": ("Sector Tailwind", 0.12),
}
MAX_PENALTY = 2.5          # red flags can cost at most this much of the 10


def _num(value: Any) -> float | None:
    """Indian-formatted number out of a string/number; None when unusable."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    text = str(value).strip()
    if not text:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[₹%,()]|Rs\.?|Cr\.?|crore|INR", "", text, flags=re.I).strip()
    text = text.replace("−", "-")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        out = float(m.group())
    except ValueError:
        return None
    return -out if neg and out > 0 else out


def _positive(*candidates: Any) -> float | None:
    """First candidate that parses to a number GREATER THAN ZERO. PURE.

    Valuation multiples are only meaningful when positive: a negative P/E, P/B
    or EV/EBITDA means negative earnings, book value or EBITDA, not a bargain.
    Plain ``a or b`` cannot express this because -3033 is truthy.
    """
    for c in candidates:
        v = _num(c)
        if v is not None and v > 0:
            return v
    return None


def _band(value: float, lo: float, hi: float, *, invert: bool = False) -> float:
    """Map value onto 0-10 between lo and hi, clamped."""
    if hi == lo:
        return 5.0
    pos = (value - lo) / (hi - lo)
    pos = max(0.0, min(1.0, pos))
    return round((1 - pos if invert else pos) * 10, 1)


def _dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


# --------------------------------------------------------------- components
def _valuation(fund: Mapping, health: Mapping) -> dict | None:
    """Cheap or dear — versus the sector, and versus intrinsic value."""
    parts, notes = [], []
    peers = _dig(health, "peers") or {}
    # First POSITIVE reading wins, not merely the first non-None. A loss-making
    # company can carry an Upstox P/E of -3033 while Screener reports a usable
    # 39.7; `a or b` keeps -3033 (it is truthy), the `pe > 0` guard below then
    # rejects it, and the company silently loses its whole Valuation component
    # despite having a perfectly good P/E available.
    pe = _positive(_dig(peers, "pe", "value"), _dig(fund, "overview", "Stock P/E"))
    pe_sector = _num(_dig(peers, "pe", "sector"))
    if pe is not None and pe > 0:
        if pe_sector and pe_sector > 0:
            rel = pe / pe_sector
            parts.append(_band(rel, 2.0, 0.5))          # cheaper than sector scores higher
            notes.append(f"P/E {pe:g} vs sector {pe_sector:g}")
        else:
            parts.append(_band(pe, 60, 8))
            notes.append(f"P/E {pe:g}")
    mos = _num(_dig(fund, "analysis", "dcf", "margin_of_safety"))
    if mos is not None:
        parts.append(_band(mos, -60, 40))
        notes.append(f"DCF margin of safety {mos:g}%")
    pb = _positive(_dig(peers, "pb", "value"), _dig(fund, "overview", "Book Value"))
    pb_sector = _num(_dig(peers, "pb", "sector"))
    if pb is not None and pb_sector and pb_sector > 0 and pb > 0:
        parts.append(_band(pb / pb_sector, 2.0, 0.5))
        notes.append(f"P/B {pb:g} vs sector {pb_sector:g}")
    if not parts:
        return None
    return {"score": round(sum(parts) / len(parts), 1), "note": "; ".join(notes)}


def _growth(fund: Mapping) -> dict | None:
    parts, notes = [], []
    growth = _dig(fund, "growth") or {}
    for block, label in (("Compounded Sales Growth", "sales"),
                         ("Compounded Profit Growth", "profit")):
        row = growth.get(block) or {}
        for window in ("5 Years", "3 Years", "TTM"):
            val = _num(row.get(window))
            if val is not None:
                parts.append(_band(val, -10, 35))
                notes.append(f"{label} {window} {val:g}%")
                break
    if not parts:
        return None
    return {"score": round(sum(parts) / len(parts), 1), "note": "; ".join(notes)}


def _financial_health(health: Mapping) -> dict | None:
    parts, notes = [], []
    de = _dig(health, "debt_equity", "value")
    if de is not None:
        parts.append(_band(float(de), 2.0, 0.0))
        notes.append(f"D/E {float(de):g}x")
    cr = _dig(health, "current_ratio", "value")
    if cr is not None:
        parts.append(_band(float(cr), 0.6, 2.5))
        notes.append(f"current ratio {float(cr):g}x")
    ocf = _dig(health, "ocf_np", "value")
    if ocf is not None:
        parts.append(_band(float(ocf), -0.5, 1.5))
        notes.append(f"OCF/PAT {float(ocf):g}x")
    if not parts:
        return None
    return {"score": round(sum(parts) / len(parts), 1), "note": "; ".join(notes)}


def _momentum(bundle: Mapping, fund: Mapping) -> dict | None:
    parts, notes = [], []
    blocks = _dig(bundle, "signal", "blocks") or {}
    for key, label in (("technical", "technical"), ("results", "results")):
        val = _num(_dig(blocks, key, "score"))
        if val is not None:
            parts.append(round(val / 10.0, 1))          # blocks are 0-100
            notes.append(f"{label} block {val:g}/100")
    cagr = _num(_dig(fund, "growth", "Stock Price CAGR", "1 Year"))
    if cagr is not None:
        parts.append(_band(cagr, -40, 60))
        notes.append(f"1Y price {cagr:g}%")
    if not parts:
        return None
    return {"score": round(sum(parts) / len(parts), 1), "note": "; ".join(notes)}


def _sector(sector: Mapping | None) -> dict | None:
    if not isinstance(sector, Mapping):
        return None
    label = str(sector.get("signal") or sector.get("label") or "").upper()
    name = str(sector.get("sector") or sector.get("name") or "sector")
    if not label:
        return None
    mapping = {"TAILWIND": 8.5, "NEUTRAL": 5.0, "HEADWIND": 2.0}
    if label not in mapping:
        return None
    return {"score": mapping[label], "note": f"{name}: {label.title()}"}


def _red_flags(bundle: Mapping, fund: Mapping, swot: Mapping | None) -> dict:
    """Penalty out of MAX_PENALTY, with every reason named."""
    reasons: list[str] = []
    # signal.bias_check is a DICT {risk, principle, flags:[{level,title,note}]},
    # not a list — iterating it directly yields its keys and produced a useless
    # red flag that just read "risk".
    bias = _dig(bundle, "signal", "bias_check")
    flags = bias.get("flags") if isinstance(bias, Mapping) else bias
    for flag in (flags or []):
        if isinstance(flag, Mapping):
            if str(flag.get("level", "")).lower() not in {"warn", "risk", "bad"}:
                continue
            title = str(flag.get("title") or "").strip()
            note = str(flag.get("note") or "").strip()
            text = f"{title} — {note}" if title and note else (title or note)
        else:
            text = str(flag)
            if not re.search(r"\b(warn|risk|blind|caution)\b", text, re.I):
                continue
        text = text.strip()
        if len(text) >= 12:                     # skip bare labels like "risk"
            reasons.append(text[:160])
    ocf = _dig(fund, "analysis", "health", "ocf_np", "value")
    if ocf is not None and float(ocf) < 0:
        reasons.append(f"operating cash flow is negative (OCF/PAT {float(ocf):g}x)")
    de = _dig(fund, "analysis", "health", "debt_equity", "value")
    if de is not None and float(de) > 1.5:
        reasons.append(f"leverage is high (D/E {float(de):g}x)")
    for item in ((swot or {}).get("threats") or []):
        if isinstance(item, Mapping) and item.get("weight") == 3:
            reasons.append(str(item.get("point"))[:120])
    seen, unique = set(), []
    for r in reasons:
        key = r.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    penalty = min(MAX_PENALTY, 0.6 * len(unique))
    return {"penalty": round(penalty, 2), "reasons": unique[:6]}


def _why(swot: Mapping | None, quadrant: str, limit: int = 5) -> list[dict]:
    out = []
    for item in ((swot or {}).get(quadrant) or [])[:limit]:
        if isinstance(item, Mapping) and item.get("point"):
            out.append({"point": item.get("point"), "evidence": item.get("evidence"),
                        "weight": item.get("weight", 1)})
    return out


def analysis_score(bundle: Mapping, *, swot: Mapping | None = None,
                   sector: Mapping | None = None) -> dict:
    """0-10 analysis score with auditable components. Never raises."""
    try:
        fund = bundle.get("fundamental") if isinstance(bundle, Mapping) else None
        fund = fund if isinstance(fund, Mapping) else {}
        health = _dig(fund, "analysis", "health") or {}
        swot = swot if isinstance(swot, Mapping) else (bundle.get("swot") if isinstance(bundle, Mapping) else None)

        raw = {
            "valuation": _valuation(fund, health),
            "growth": _growth(fund),
            "financial_health": _financial_health(health),
            "momentum": _momentum(bundle, fund),
            "sector_tailwind": _sector(sector),
        }
        components, missing, weight_sum, weighted = [], [], 0.0, 0.0
        for key, (label, weight) in WEIGHTS.items():
            part = raw.get(key)
            if not part:
                missing.append(label)
                continue
            components.append({"key": key, "label": label, "score": part["score"],
                               "weight": weight, "note": part["note"]})
            weight_sum += weight
            weighted += part["score"] * weight

        flags = _red_flags(bundle, fund, swot)
        if not components:
            return {"score": None, "label": "Not enough data",
                    "components": [], "components_missing": missing,
                    "red_flags": flags, "why_invest": [], "why_not": [],
                    "note": "no component had usable inputs"}

        base = weighted / weight_sum                     # renormalised over what we had
        score = max(0.0, min(10.0, base - flags["penalty"]))
        label = ("Strong" if score >= 7.5 else "Constructive" if score >= 6
                 else "Mixed" if score >= 4.5 else "Weak" if score >= 3 else "Poor")
        return {
            "score": round(score, 1),
            "base": round(base, 1),
            "label": label,
            "components": components,
            "components_missing": missing,
            "red_flags": flags,
            "why_invest": _why(swot, "strengths"),
            "why_not": _why(swot, "weaknesses"),
        }
    except Exception as e:  # noqa: BLE001 - a score must never break a bake
        log.warning("analysis_score failed: %s", type(e).__name__)
        return {"score": None, "label": "unavailable", "components": [],
                "components_missing": [], "red_flags": {"penalty": 0, "reasons": []},
                "why_invest": [], "why_not": []}
