"""Small scoring helpers shared by the engines. All outputs are on 0-100."""
from __future__ import annotations

import math


def clamp(x: float, lo: float, hi: float) -> float:
    # NaN must read as the midpoint (neutral), never as `hi`: Python's
    # min(hi, nan) returns hi, which silently turned missing data into the
    # MAXIMUM bullish score everywhere clamp is used.
    if x != x:
        return (lo + hi) / 2.0
    return max(lo, min(hi, x))


def sigmoid_score(x: float, scale: float = 1.45) -> float:
    """Map an unbounded z-like value to 0-100 with 0 -> 50.

    `scale` chosen so that x == +2 -> ~80 and x == -2 -> ~20 by default.
    NaN input scores neutral; extreme inputs saturate instead of overflowing.
    """
    if x != x:
        return 50.0
    z = x / scale
    if z < -60.0:
        return 0.0
    if z > 60.0:
        return 100.0
    return 100.0 / (1.0 + math.exp(-z))


def linear_score(value: float, neutral: float, full: float) -> float:
    """Linear map: `neutral` -> 50, `neutral+full` -> 100, `neutral-full` -> 0."""
    if full == 0:
        return 50.0
    return clamp(50.0 + 50.0 * (value - neutral) / full, 0.0, 100.0)
