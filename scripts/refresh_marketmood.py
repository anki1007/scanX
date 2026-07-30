"""
Market Mood board publisher  ->  docs/data/marketmood.json (+ _meta).

Replicates the useful core of a "Market Mood Index": a 0-100 composite
momentum gauge with daily history, market-breadth counts, and %-below-DMA
series over the quotes.json universe (~500 top NSE/BSE names — a good
breadth proxy for the whole market).

  * Breadth TODAY comes from docs/data/quotes.json pct values (advances /
    declines and the 3% / 5% / 10% mover buckets).
  * DMA breadth comes from Yahoo (yfinance, no auth): ~260 trading days of
    daily closes per name -> % of stocks below 20/50/200 DMA, counts near
    52w high/low (within 5%), RSI14 overbought (>70) / oversold (<30).
  * Composite mood 0-100 = 0.4*(100-pct_below_50dma)
                         + 0.3*(100-pct_below_200dma)
                         + 0.3*(50 + clamp(avg_pct_change*10, -50, 50))
    Bands: >=75 "Ex Strong" | >=50 "Strong" | >=25 "Weak" | else "Ex Weak".

History: one entry per date, appended/replaced on each run, last 400 kept.
Keep-last-good: if Yahoo gives nothing, the previous file is left untouched
(never blank the site) — same failure philosophy as refresh_quotes.py.

    python scripts/refresh_marketmood.py
    python scripts/refresh_marketmood.py --limit 40     # quick test run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
QUOTES = ROOT / "docs" / "data" / "quotes.json"
HISTORY_KEEP = 400          # daily entries retained
LOOKBACK_DAYS = 380         # calendar days -> ~260 trading days
MIN_BARS = 30               # ignore tickers with fewer daily closes


def _atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def yf_symbol(code: str) -> str:
    """Same mapping as refresh_scanx._yf_fill: numeric = BSE scrip, else NSE."""
    c = str(code).strip().upper()
    return f"{c}.BO" if c.isdigit() else f"{c}.NS"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ------------------------------------------------------------- pure functions
def breadth_from_quotes(quotes: dict) -> dict:
    """Advance/decline + mover-bucket counts from a quotes.json 'quotes' dict."""
    pcts = []
    for q in (quotes or {}).values():
        if isinstance(q, dict) and q.get("pct") is not None:
            try:
                pcts.append(float(q["pct"]))
            except (TypeError, ValueError):
                pass
    n = len(pcts)
    return {
        "n": n,
        "up": sum(1 for p in pcts if p > 0),
        "down": sum(1 for p in pcts if p < 0),
        "flat": sum(1 for p in pcts if p == 0),
        "up3": sum(1 for p in pcts if p > 3),
        "down3": sum(1 for p in pcts if p < -3),
        "up5": sum(1 for p in pcts if p > 5),
        "down5": sum(1 for p in pcts if p < -5),
        "up10": sum(1 for p in pcts if p > 10),
        "down10": sum(1 for p in pcts if p < -10),
        "avg_pct": round(sum(pcts) / n, 2) if n else 0.0,
    }


def rsi14(closes, period: int = 14):
    """Wilder RSI from a sequence/Series of closes. None if too short."""
    import pandas as pd
    s = pd.Series(list(closes), dtype="float64").dropna()
    if len(s) < period + 1:
        return None
    d = s.diff()
    gain = d.clip(lower=0.0)
    loss = (-d).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    ag, al = float(avg_gain.iloc[-1]), float(avg_loss.iloc[-1])
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 2)


def mood_score(pct_below_50, pct_below_200, avg_pct) -> float:
    """Composite 0-100 momentum blend (higher = stronger market)."""
    p50 = 50.0 if pct_below_50 is None else float(pct_below_50)
    p200 = 50.0 if pct_below_200 is None else float(pct_below_200)
    avg = 0.0 if avg_pct is None else float(avg_pct)
    s = (0.4 * (100.0 - p50)
         + 0.3 * (100.0 - p200)
         + 0.3 * (50.0 + clamp(avg * 10.0, -50.0, 50.0)))
    return round(clamp(s, 0.0, 100.0), 1)


def mood_label(score: float) -> str:
    if score >= 75:
        return "Ex Strong"
    if score >= 50:
        return "Strong"
    if score >= 25:
        return "Weak"
    return "Ex Weak"


def dma_stats(frames: dict) -> dict:
    """Aggregate DMA / 52w / RSI breadth from {code: pandas.Series-of-closes}.

    The three DMA counts are independent, so "how many are above 20 AND 50 AND
    200" cannot be derived from them afterwards -- a market can have 60% above
    the 20 and 60% above the 200 with very few names above both. That
    "above every moving average" figure is the one traders actually use to call
    a trend intact, so it is counted here, per stock, in the same pass.

    Its denominator is only the stocks that HAVE all three averages (>=200
    bars). A name with 60 days of history is not "below its 200 DMA"; it has no
    200 DMA, and folding it into the denominator would quietly understate the
    reading every time a batch of new listings arrives.
    """
    n20 = n50 = n200 = b20 = b50 = b200 = 0
    near_hi = near_lo = ob = osold = 0
    n_all = above_all = below_all = 0
    for ser in frames.values():
        vals = ser.astype(float)
        last = float(vals.iloc[-1])
        a20 = a50 = a200 = None
        if len(vals) >= 20:
            n20 += 1
            a20 = last >= float(vals.tail(20).mean())
            b20 += not a20
        if len(vals) >= 50:
            n50 += 1
            a50 = last >= float(vals.tail(50).mean())
            b50 += not a50
        if len(vals) >= 200:
            n200 += 1
            a200 = last >= float(vals.tail(200).mean())
            b200 += not a200
        if a20 is not None and a50 is not None and a200 is not None:
            n_all += 1
            if a20 and a50 and a200:
                above_all += 1
            elif not a20 and not a50 and not a200:
                below_all += 1
        win = vals.tail(252)
        hi, lo = float(win.max()), float(win.min())
        if hi > 0 and last >= hi * 0.95:
            near_hi += 1
        if lo > 0 and last <= lo * 1.05:
            near_lo += 1
        r = rsi14(vals)
        if r is not None:
            if r > 70:
                ob += 1
            elif r < 30:
                osold += 1

    def pct(hits, n):
        return round(100.0 * hits / n, 1) if n else None

    return {"n": len(frames),
            "pct_below_20dma": pct(b20, n20),
            "pct_below_50dma": pct(b50, n50),
            "pct_below_200dma": pct(b200, n200),
            # published explicitly rather than left as 100-below: the counts have
            # DIFFERENT denominators (n20 != n200 whenever young listings are in
            # the universe), so subtracting in the UI would be subtly wrong
            "pct_above_20dma": pct(n20 - b20, n20),
            "pct_above_50dma": pct(n50 - b50, n50),
            "pct_above_200dma": pct(n200 - b200, n200),
            "n_all_dma": n_all,
            "pct_above_all_dma": pct(above_all, n_all),
            "pct_below_all_dma": pct(below_all, n_all),
            "near_52w_high": near_hi, "near_52w_low": near_lo,
            "overbought": ob, "oversold": osold}


# ------------------------------------------------------------------ yfinance
def _download_closes(codes: list, chunk: int = 100) -> dict:
    """~260 trading days of daily closes per code -> {code: Series}."""
    try:
        import logging as _lg
        import pandas as pd
        import yfinance as yf
        _lg.getLogger("yfinance").setLevel(_lg.CRITICAL)   # mute delisted spam
    except Exception as e:  # noqa: BLE001
        print(f"[mood] yfinance unavailable: {e}")
        return {}
    tick = {yf_symbol(c): str(c).upper() for c in codes if str(c).strip()}
    syms = sorted(tick)
    start = (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    frames = {}
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        try:
            df = yf.download(part, start=start, interval="1d", auto_adjust=False,
                             progress=False, threads=True, group_by="column")
        except Exception as e:  # noqa: BLE001
            print(f"[mood] yahoo chunk {i // chunk} failed: {type(e).__name__}")
            continue
        if df is None or len(df) == 0 or "Close" not in df:
            continue
        close = df["Close"]
        if isinstance(close, pd.Series):                   # single-ticker shape
            close = close.to_frame(name=part[0])
        for t in part:
            if t in close.columns:
                ser = close[t].dropna()
                if len(ser) >= MIN_BARS:
                    frames[tick[t]] = ser
        time.sleep(0.3)
    return frames


# ---------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Market Mood board feed")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--limit", type=int, default=0,
                    help="cap universe size (testing); 0 = full universe")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mood_path = out / "marketmood.json"

    try:
        qj = json.loads(QUOTES.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        print(f"[mood] quotes.json unreadable ({type(e).__name__}: {e}) — keeping previous file")
        return 1
    quotes = qj.get("quotes") or {}
    qdate = qj.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
    codes = sorted(quotes)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print("[mood] empty quote universe — keeping previous file")
        return 1

    breadth = breadth_from_quotes({c: quotes[c] for c in codes})
    print(f"[mood] universe {len(codes)} | today {breadth['up']} up / {breadth['down']} down "
          f"| avg {breadth['avg_pct']}%")

    frames = _download_closes(codes)
    if not frames:
        print("[mood] yfinance gave nothing — keeping previous file")
        return 1
    if len(frames) < len(codes):
        print(f"[mood] {len(codes) - len(frames)} tickers unresolved on Yahoo (proceeding with {len(frames)})")

    dma = dma_stats(frames)
    score = mood_score(dma["pct_below_50dma"], dma["pct_below_200dma"], breadth["avg_pct"])
    entry = {"date": qdate, "score": score, "label": mood_label(score),
             "breadth": breadth, "dma": dma}

    hist = []
    try:
        prev = json.loads(mood_path.read_text(encoding="utf-8", errors="replace"))
        hist = [h for h in (prev.get("history") or [])
                if isinstance(h, dict) and h.get("date")]
    except Exception:  # noqa: BLE001
        hist = []
    hist = [h for h in hist if h["date"] != qdate]
    hist.append(entry)
    hist.sort(key=lambda h: h["date"])
    hist = hist[-HISTORY_KEEP:]

    latest = dict(entry)
    latest.update({"universe": len(codes), "resolved": len(frames)})
    now = datetime.now(IST)
    _atomic(mood_path, json.dumps({"history": hist, "latest": latest},
                                  separators=(",", ":")))
    _atomic(out / "marketmood_meta.json", json.dumps({
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "source": "quotes.json breadth + Yahoo daily closes (yfinance)",
        "universe": len(codes), "resolved": len(frames),
        "history_days": len(hist),
    }, indent=1))
    print(f"[mood] {qdate} score {score} ({mood_label(score)}) | "
          f"above all three DMA {dma['pct_above_all_dma']}% of {dma['n_all_dma']} | "
          f"below 20/50/200 DMA {dma['pct_below_20dma']}/{dma['pct_below_50dma']}/"
          f"{dma['pct_below_200dma']}% | 52w hi/lo {dma['near_52w_high']}/{dma['near_52w_low']} | "
          f"RSI ob/os {dma['overbought']}/{dma['oversold']} | history {len(hist)}d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
