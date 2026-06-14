"""
Price/microstructure feature computation (Agents 8 & 9 inputs).

Turns a list of OHLCV bars into the indicators the technical, microstructure and
PEAD engines consume. Pure numpy/pandas; no scoring here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..models import PriceBar


@dataclass
class MarketFeatures:
    close: float
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    adx: float
    atr: float
    vwap: float
    vwap_pos: float           # (close - vwap) / vwap
    rvol: float               # last volume / trailing average volume
    delivery_pct: float
    rs_20: float              # 20-bar return minus benchmark 20-bar return
    ret_20: float             # raw 20-bar return
    breakout_20: bool         # close above prior 20-bar high
    trend_up: bool            # ema20 > ema50 > ema200
    n_bars: int


def _ema(s: pd.Series, span: int) -> float:
    if len(s) == 0:
        return float("nan")
    span = min(span, len(s))
    return float(s.ewm(span=span, adjust=False).mean().iloc[-1])


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _atr(high, low, close, period: int = 14) -> float:
    if len(close) < 2:
        return 0.0
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    period = min(period, len(tr.dropna()))
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def _adx(high, low, close, period: int = 14) -> float:
    if len(close) < period + 1:
        return 0.0
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(0.0 if pd.isna(adx) else adx)


def compute_features(
    bars: list[PriceBar], benchmark: Optional[list[PriceBar]] = None
) -> MarketFeatures:
    if not bars:
        raise ValueError("compute_features requires at least one bar")

    df = pd.DataFrame(
        {
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "deliv": [b.delivery_pct if b.delivery_pct is not None else np.nan
                      for b in bars],
        }
    )
    close = df["close"]
    n = len(df)

    # volume / VWAP over the last 20 bars
    look = min(20, n)
    recent = df.iloc[-look:]
    vwap = float((recent["close"] * recent["volume"]).sum()
                 / max(recent["volume"].sum(), 1e-9))
    avg_vol = float(df["volume"].iloc[-(look + 1):-1].mean()) if n > 1 else float(df["volume"].iloc[-1])
    rvol = float(df["volume"].iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

    ret_20 = float(close.iloc[-1] / close.iloc[-look] - 1) if n >= look and close.iloc[-look] else 0.0
    bench_ret = 0.0
    if benchmark and len(benchmark) >= look:
        bc = [b.close for b in benchmark]
        bench_ret = bc[-1] / bc[-look] - 1 if bc[-look] else 0.0
    rs_20 = ret_20 - bench_ret

    prior_high = float(df["high"].iloc[-(look + 1):-1].max()) if n > look else float(df["high"].max())
    breakout = bool(close.iloc[-1] > prior_high)

    ema20, ema50, ema200 = _ema(close, 20), _ema(close, 50), _ema(close, 200)
    deliv = float(df["deliv"].iloc[-3:].mean()) if df["deliv"].notna().any() else 50.0

    return MarketFeatures(
        close=float(close.iloc[-1]),
        ema20=ema20, ema50=ema50, ema200=ema200,
        rsi=_rsi(close), adx=_adx(df["high"], df["low"], close),
        atr=_atr(df["high"], df["low"], close),
        vwap=vwap, vwap_pos=float(close.iloc[-1] / vwap - 1),
        rvol=rvol, delivery_pct=deliv, rs_20=rs_20, ret_20=ret_20,
        breakout_20=breakout, trend_up=bool(ema20 > ema50 > ema200), n_bars=n,
    )
