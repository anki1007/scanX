"""
Agent 11 — Valuation score.

Objective from the spec: *prevent buying overvalued earnings beats*. A rich
multiple relative to the sector lowers the score; cheap valuation, a reasonable
PEG and a healthy FCF yield raise it. Missing inputs read neutral (50).
"""
from __future__ import annotations

from ..models import ValuationInputs
from ._util import clamp


def score_valuation(v: ValuationInputs) -> float:
    parts: list[tuple[float, float]] = []   # (score, weight)

    # Relative PE vs sector: cheaper than peers is better.
    if v.pe and v.sector_median_pe:
        rel = v.pe / v.sector_median_pe
        parts.append((clamp(50 + (1 - rel) * 100, 0, 100), 0.5))

    # PEG: <1 cheap for the growth, >2 expensive.
    if v.peg is not None:
        if v.peg <= 1:
            peg_score = 90
        elif v.peg >= 2:
            peg_score = 25
        else:
            peg_score = 90 - (v.peg - 1) * 65
        parts.append((clamp(peg_score, 0, 100), 0.3))

    # FCF yield: higher is better.
    if v.fcf_yield is not None:
        parts.append((clamp(50 + v.fcf_yield * 500, 0, 100), 0.2))

    if not parts:
        return 50.0
    wsum = sum(w for _, w in parts)
    return float(sum(s * w for s, w in parts) / wsum)
