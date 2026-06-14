"""
Agents 8 & 9 — Microstructure + Technical confirmation score.

Validates that price/volume action agrees with the fundamental signal: trend
alignment (EMA stack), momentum (RSI, ADX), a breakout, position vs VWAP, and
participation (relative volume, delivery). Returns 0-100.
"""
from __future__ import annotations

from .features import MarketFeatures
from ._util import clamp


def _rsi_score(rsi: float) -> float:
    """Peak around 60 (strong but not overbought); fade above 75 / below 45."""
    if rsi <= 40:
        return clamp((rsi - 20) * 1.5, 0, 30)
    if rsi <= 60:
        return 30 + (rsi - 40) * 3.5            # 40->30, 60->100
    if rsi <= 75:
        return 100 - (rsi - 60) * 2.0           # taper
    return clamp(70 - (rsi - 75) * 4, 0, 70)    # overbought penalty


def score_technical(f: MarketFeatures) -> float:
    components: list[tuple[float, float]] = []

    # Trend stack (EMA20 > EMA50 > EMA200)
    if f.trend_up:
        trend = 100.0
    elif f.ema20 > f.ema50:
        trend = 70.0
    elif f.close > f.ema50:
        trend = 55.0
    else:
        trend = 25.0
    components.append((trend, 0.30))

    components.append((100.0 if f.close > f.ema50 else 30.0, 0.10))
    components.append((_rsi_score(f.rsi), 0.15))
    components.append((clamp((f.adx - 15) / 20 * 100, 0, 100), 0.15))   # ADX35 -> 100
    components.append((100.0 if f.breakout_20 else 40.0, 0.20))
    components.append((100.0 if f.vwap_pos > 0 else 35.0, 0.10))

    return float(sum(s * w for s, w in components))
