"""
Price-history analytics for the Fundamental Screener (year-wise returns, monthly
returns heatmap, risk stats, and the relative-strength / technical block).

Price source priority:
  1. Dhan daily candles  (better BSE coverage; uses dhan_token.json / env)
  2. yfinance            (fallback)

Relative-strength benchmark: Nifty 500 (^CRSLDX), with ^NSEI as fallback.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger("technofunda.pricehist")
_CACHE = Path(__file__).resolve().parent.parent.parent / ".cache"


def _tickers(code: str, overview: Optional[dict]) -> list:
    code = str(code).strip().upper()
    if code.isdigit():
        return [f"{code}.BO"]
    return [f"{code}.NS", f"{code}.BO"]


def _round(x, n=2):
    try:
        return round(float(x), n)
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------- Dhan (preferred)
_DHAN = {"sig": None, "prov": None}


def _dhan_token_sig():
    """Identity of the current token source (env vars or token-file mtime).

    When the signature changes (e.g. scripts/dhan_login.py rewrote
    dhan_token.json), _dhan_provider() rebuilds — so long-running processes
    (serve.py, the realtime engine) pick up a fresh token WITHOUT a restart.
    """
    import os
    if os.environ.get("SCANX_NO_DHAN"):
        return ("off",)
    cid = os.environ.get("DHAN_CLIENT_ID"); tok = os.environ.get("DHAN_ACCESS_TOKEN")
    if cid and tok:
        return ("env", cid, tok)
    tf = Path(__file__).resolve().parent.parent.parent / "dhan_token.json"
    try:
        return ("file", tf.stat().st_mtime_ns)
    except OSError:
        return ("none",)


def _dhan_provider():
    """Load a DhanProvider from env or dhan_token.json (read-only market data).

    Cached per token signature: a refreshed dhan_token.json is hot-reloaded on
    the next call instead of serving 401s with the stale token forever.
    """
    sig = _dhan_token_sig()
    if _DHAN["sig"] == sig:
        return _DHAN["prov"]
    _DHAN["sig"] = sig
    _DHAN["prov"] = None
    if sig[0] in ("off", "none"):
        return None
    import os
    cid = os.environ.get("DHAN_CLIENT_ID"); tok = os.environ.get("DHAN_ACCESS_TOKEN")
    root = Path(__file__).resolve().parent.parent.parent
    tf = root / "dhan_token.json"
    if (not cid or not tok) and tf.exists():
        try:
            jj = json.loads(tf.read_text())
            cid = cid or jj.get("client_id"); tok = tok or jj.get("access_token")
        except Exception:  # noqa: BLE001
            pass
    if not cid or not tok:
        return None
    try:
        from .dhan_provider import DhanProvider
        _DHAN["prov"] = DhanProvider(cid, tok, cache_path=str(root / ".cache" / "dhan_master.json"))
    except Exception:  # noqa: BLE001
        _DHAN["prov"] = None
    return _DHAN["prov"]


def _series_from_dhan(j):
    """Dhan historical arrays -> pandas close Series with a DatetimeIndex."""
    import pandas as pd
    close = (j or {}).get("close") or []
    ts = (j or {}).get("timestamp") or (j or {}).get("start_Time") or []
    if not close or len(ts) != len(close):
        return None
    try:
        idx = pd.to_datetime([int(x) for x in ts], unit="s")
    except Exception:  # noqa: BLE001
        return None
    s = pd.Series([float(c) for c in close], index=idx).dropna()
    return s if len(s) > 30 else None


def _history_dhan(code, overview):
    prov = _dhan_provider()
    if prov is None:
        return None, None
    try:
        from .dhan_provider import dhan_cooldown_left
        if dhan_cooldown_left() > 0:        # dead/paused token: don't waste
            return None, None               # seconds per stock — go to yfinance
    except Exception:  # noqa: BLE001
        pass
    try:
        sid, seg = prov.resolve_id(code)
        if sid is None:
            return None, None
        j = prov.historical(sid, seg, instrument="EQUITY")
        s = _series_from_dhan(j) if j else None
        if s is None:
            return None, None
        return s, f"{str(code).upper()} (Dhan {seg.split('_')[0]})"
    except Exception as e:  # noqa: BLE001
        log.warning("Dhan history %s failed: %s", code, e)
        return None, None


# ----------------------------------------------------------- yfinance fallback
def _history_yf(code: str, overview: Optional[dict]):
    try:
        import yfinance as yf
    except ImportError:
        return None, None
    for t in _tickers(code, overview):
        try:
            h = yf.Ticker(t).history(period="max", interval="1d", auto_adjust=True)
            if h is not None and len(h) > 30 and "Close" in h:
                return h["Close"].dropna(), t
        except Exception as e:  # noqa: BLE001
            log.warning("yf history %s failed: %s", t, e)
    return None, None


def _history(code: str, overview: Optional[dict]):
    """Prefer Dhan (better BSE coverage); fall back to yfinance."""
    s, tk = _history_dhan(code, overview)
    if s is not None:
        return s, tk
    return _history_yf(code, overview)


# ----------------------------------------------------- benchmark (Nifty 500)
_BENCH = ["^CRSLDX", "^NSEI"]   # Nifty 500 (broad), then Nifty 50 fallback
_BENCH_CACHE = {"date": None, "series": None}


def _benchmark_close():
    if _BENCH_CACHE["date"] == date.today().isoformat():
        return _BENCH_CACHE["series"]
    series = None
    try:
        import yfinance as yf
        for b in _BENCH:
            try:
                h = yf.Ticker(b).history(period="3y", interval="1d", auto_adjust=True)
                if h is not None and len(h) > 260 and "Close" in h:
                    series = h["Close"].dropna(); break
            except Exception:  # noqa: BLE001
                continue
    except ImportError:
        series = None
    _BENCH_CACHE["date"] = date.today().isoformat()
    _BENCH_CACHE["series"] = series
    return series


def _ret(series, n):
    s = series.dropna()
    if len(s) <= n:
        return None
    a, b = float(s.iloc[-1]), float(s.iloc[-1 - n])
    return (a / b - 1) if b else None


def _technical(close, bench):
    """Relative strength + trend metrics (plain floats)."""
    out = {}
    W = {"3m": 63, "6m": 126, "12m": 252}
    sret = {k: _ret(close, n) for k, n in W.items()}
    bret = {k: (_ret(bench, n) if bench is not None else None) for k, n in W.items()}
    for k in W:
        out[f"ret_{k}"] = _round((sret[k] or 0) * 100) if sret[k] is not None else None
        out[f"excess_{k}"] = (_round((sret[k] - bret[k]) * 100)
                              if (sret[k] is not None and bret[k] is not None) else None)
    ex = [(out["excess_3m"], 0.5), (out["excess_6m"], 0.3), (out["excess_12m"], 0.2)]
    avail = [(e, w) for e, w in ex if e is not None]
    if avail:
        raw = sum(e / 100.0 * w for e, w in avail) / sum(w for _, w in avail)
        out["rs_rating"] = int(max(0, min(100, round(50 + 250 * raw))))
    else:
        out["rs_rating"] = None
    last = float(close.iloc[-1])
    ma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    ma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    out["price"] = _round(last)
    out["above_50dma"] = (last > ma50) if ma50 else None
    out["above_200dma"] = (last > ma200) if ma200 else None
    out["golden_cross"] = (ma50 > ma200) if (ma50 and ma200) else None
    win = close.tail(252)
    hi, lo = float(win.max()), float(win.min())
    out["pos_52w"] = _round((last - lo) / (hi - lo) * 100) if hi > lo else None
    out["dist_52w_high"] = _round((last / hi - 1) * 100) if hi else None
    out["benchmark"] = "Nifty 500"
    return out


def price_analytics(code: str, overview: Optional[dict] = None,
                    use_cache: bool = True) -> dict:
    cf = _CACHE / f"price_{str(code).upper()}.json"
    if use_cache and cf.exists():
        try:
            j = json.loads(cf.read_text())
            if j.get("date") == date.today().isoformat():
                return j["data"]
        except Exception:  # noqa: BLE001
            pass
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "pandas/yfinance not installed"}

    close, ticker = _history(code, overview)
    if close is None or len(close) < 30:
        return {"ok": False, "reason": "no price history"}

    # ---- year-wise return (calendar close-to-close)
    yr_last = close.groupby(close.index.year).last()
    yearwise = []
    prev = None
    for y, v in yr_last.items():
        if prev is not None:
            yearwise.append({"year": int(y), "ret": _round((v / prev - 1) * 100)})
        prev = v
    first_y = int(close.index.year.min())
    fy = close[close.index.year == first_y]
    if len(fy) > 1:
        yearwise.insert(0, {"year": first_y, "ret": _round((fy.iloc[-1] / fy.iloc[0] - 1) * 100)})

    # ---- monthly heatmap
    mclose = close.resample("ME").last()
    mret = mclose.pct_change() * 100
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    by_year: dict = {}
    for ts, val in mret.items():
        if math.isnan(val):
            continue
        by_year.setdefault(ts.year, [None] * 12)[ts.month - 1] = _round(val)
    heat_rows = []
    for y in sorted(by_year):
        vals = by_year[y]; comp = 1.0; have = False
        for v in vals:
            if v is not None:
                comp *= (1 + v / 100.0); have = True
        heat_rows.append({"year": int(y), "vals": vals,
                          "annual": _round((comp - 1) * 100) if have else None})

    # ---- risk stats (weekly)
    wk = close.resample("W").last().pct_change().dropna()
    risk = {}
    if len(wk) > 10:
        mean = wk.mean(); std = wk.std(); downside = wk[wk < 0].std()
        dd = (close / close.cummax() - 1).min()
        risk = {
            "avg_weekly": _round(mean * 100),
            "weekly_std": _round(std * 100),
            "ann_vol": _round(std * math.sqrt(52) * 100),
            "max_drawdown": _round(dd * 100),
            "pct_positive": _round((wk > 0).mean() * 100),
            "sharpe": _round(mean / std * math.sqrt(52)) if std else None,
            "sortino": _round(mean / downside * math.sqrt(52)) if downside else None,
        }

    technical = _technical(close, _benchmark_close())
    data = {"ok": True, "ticker": ticker, "yearwise": yearwise,
            "heatmap": {"months": months, "rows": heat_rows}, "risk": risk,
            "technical": technical}
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"date": date.today().isoformat(), "data": data}))
    except Exception:  # noqa: BLE001
        pass
    return data
