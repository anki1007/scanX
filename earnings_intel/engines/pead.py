"""
Agent 4 — PEAD (Post-Earnings-Announcement Drift) score.

Weighted blend of the eight drivers in the spec, each first mapped to its own
0-100 sub-score. The weights live in `config.PEAD_WEIGHTS`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import PEAD_WEIGHTS
from ..models import EarningsReport, Guidance
from .features import MarketFeatures
from .sue import compute_sue
from ._util import clamp, linear_score


@dataclass
class PEADResult:
    score: float
    parts: dict[str, float]


_GUIDANCE_SCORE = {
    Guidance.RAISED: 90.0,
    Guidance.MAINTAINED: 55.0,
    Guidance.LOWERED: 15.0,
    Guidance.NONE: 50.0,
}


def _surprise_frac(actual, estimate) -> float:
    if estimate is None or estimate == 0:
        return 0.0
    return (actual - estimate) / abs(estimate)


def compute_pead(
    report: EarningsReport,
    features: MarketFeatures,
    institutional_score: float = 50.0,
    weights: dict[str, float] | None = None,
) -> PEADResult:
    w = weights or PEAD_WEIGHTS

    parts = {
        "revenue_surprise": linear_score(
            _surprise_frac(report.revenue, report.revenue_estimate), 0.0, 0.05),
        "pat_surprise": linear_score(
            _surprise_frac(report.pat, report.pat_estimate), 0.0, 0.08),
        "eps_surprise": compute_sue(report).score,
        "guidance": _GUIDANCE_SCORE.get(report.guidance, 50.0),
        "volume_expansion": clamp(50 + (features.rvol - 1) * 25, 0, 100),
        "delivery": clamp((features.delivery_pct - 20) * 1.6, 0, 100),
        "relative_strength": clamp(50 + features.rs_20 * 400, 0, 100),
        "institutional_flow": institutional_score,
    }

    score = sum(parts[k] * w[k] for k in w)
    return PEADResult(score=float(clamp(score, 0, 100)), parts=parts)
