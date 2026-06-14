"""
Agent 3 — Standardized Unexpected Earnings (SUE).

SUE = (Actual EPS - Expected EPS) / dispersion

Dispersion is the std dev of analyst EPS estimates when available, else the std
of the trailing actual-minus-expected EPS surprises. Returns both the raw SUE
and a 0-100 score (50 == in line with expectations).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import EarningsReport
from ._util import sigmoid_score


@dataclass
class SUEResult:
    sue: float
    score: float


def compute_sue(report: EarningsReport) -> SUEResult:
    if report.eps_estimate is None:
        return SUEResult(sue=0.0, score=50.0)

    surprise = report.eps - report.eps_estimate

    dispersion = report.eps_std
    if not dispersion or dispersion <= 0:
        hist = [h for h in report.eps_surprise_history if h is not None]
        dispersion = float(np.std(hist)) if len(hist) >= 2 else abs(report.eps_estimate) * 0.1
    if not dispersion or dispersion <= 0:
        dispersion = max(abs(report.eps_estimate) * 0.1, 1e-6)

    sue = surprise / dispersion
    return SUEResult(sue=float(sue), score=float(sigmoid_score(sue)))
