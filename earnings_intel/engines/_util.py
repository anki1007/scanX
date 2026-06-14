"""Small scoring helpers shared by the engines. All outputs are on 0-100."""
from __future__ import annotations

import math


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def sigmoid_score(x: float, scale: float = 1.45) -> float:
    """Map an unbounded z-like value to 0-100 with 0 -> 50.

    `scale` chosen so that x == +2 -> ~80 and x == -2 -> ~20 by default.
    """
    return 100.0 / (1.0 + math.exp(-x / scale))


def linear_score(value: float, neutral: float, full: float) -> float:
    """Linear map: `neutral` -> 50, `neutral+full` -> 100, `neutral-full` -> 0."""
    if full == 0:
        return 50.0
    return clamp(50.0 + 50.0 * (value - neutral) / full, 0.0, 100.0)
