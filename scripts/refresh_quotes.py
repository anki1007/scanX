"""
Delayed/real-time NSE/BSE quotes -> docs/data/quotes.json  (NO broker, NO login).

Fetch path is picked automatically:

  * LOCAL (scanX.bat, your own IP): NSE Total-Market batch + BSE per-scrip via
    the Scrapling provider. Real-time, broker-matched. (default)

  * CLOUD (GitHub Actions, datacenter IP):  set SCANX_QUOTES_CLOUD=1.
    NSE blocks datacenter IPs (403), but BSE does NOT — so we pull REAL-TIME
    prices straight from BSE's public API:
      1. download BSE's scrip master once  -> {symbol -> scripcode, + mcap}
      2. rank the most-liquid universe, resolve each board/NSE symbol to its
         BSE scripcode, and fetch getScripHeaderData per scrip (threaded).
    Yahoo Finance is only a last-resort gap-filler for the rare NSE-only name.
    Set SCANX_QUOTES_YF=1 to force the Yahoo-only path instead.

The boards read this STATIC file, so quotes render identically on GitHub Pages
and locally. Cadence = the workflow cron (~15 min during market hours).

    python scripts/refresh_quotes.py
    SCANX_QUOTES_CLOUD=1 python scripts/refresh_quotes.py     # BSE-direct (cloud)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
OUT = ROOT / "docs" / "data" / "quotes.json"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BSE_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
             "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
_BSE_QUOTE = ("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
              "?Debtflag=&scripcode={code}&seriesid=")


def _atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def board_codes() -> list:
    try:
        rows = json.loads((ROOT / "docs" / "data" / "pead.json")
                          .read_text(encoding="utf-8", errors="replace"))
        return sorted({str(r.get("code") or "").upper() for r in rows if r.get("code")})
    except Exception:  # noqa: BLE001
        return []


def _num(x):
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------- BSE (cloud)
_tl = threading.local()


def _bse_session():
    s = getattr(_tl, "s", None)
    if s is None:
        from curl_cffi import requests as cc
        s = cc.Session(impersonate="chrome")
        s.headers.update({"User-Agent": _UA, "Accept": "application/json, text/plain, */*",
                          "Referer": "https://www.bseindia.com/"})
        _tl.s = s
    return s


def _bse_master():
    """BSE scrip master -> (symbol->scripcode map, ranked list of (code,symbol,mcap))."""
    try:
        r = _bse_session().get(_BSE_LIST, timeout=45)
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[quotes] BSE master failed: {type(e).__name__}: {e}")
        return {}, []
    sym2code, ranked = {}, []
    for x in rows if isinstance(rows, list) else []:
        code = str(x.get("SCRIP_CD") or "").strip()
        sym = str(x.get("scrip_id") or "").strip().upper()
        if not code:
            continue
        if sym:
            sym2code.setdefault(sym, code)
        ranked.append((code, sym, _num(x.get("Mktcap")) or 0))
    ranked.sort(key=lambda t: -t[2])
    return sym2code, ranked


def _bse_quote_one(code: str):
    try:
        r = _bse_session().get(_BSE_QUOTE.format(code=code), timeout=15)
        cr = (r.json() or {}).get("CurrRate") or {}
        ltp, pct = _num(cr.get("LTP")), _num(cr.get("PcChg"))
        if ltp is not None:
            return {"ltp": round(ltp, 2), "pct": (round(pct, 2) if pct is not None else None)}
    except Exception:  # noqa: BLE001
        return None
    return None


def _bse_quotes(limit: int):
    """Real-time BSE quotes for the most-liquid universe + the PEAD board."""
    sym2code, ranked = _bse_master()
    if not ranked:
        return {}
    targets = {}                                   # board-key -> scripcode
    for code, sym, _mc in ranked[:limit]:
        targets[(sym or code)] = code
    for c in board_codes():                        # always include the board
        if c in targets:
            continue
        if c.isdigit():
            targets[c] = c
        elif c in sym2code:
            targets[c] = sym2code[c]
    out = {}

    def work(kv):
        key, code = kv
        q = _bse_quote_one(code)
        time.sleep(0.05)
        return key, q

    with ThreadPoolExecutor(max_workers=8) as ex:
        for key, q in ex.map(work, list(targets.items())):
            if q:
                out[key] = q
    return out


# ------------------------------------------------------------- Yahoo (gap-fill)
def _universe(limit: int) -> list:
    sel = {}
    try:
        tf = json.loads((ROOT / "docs" / "data" / "technofunda.json")
                        .read_text(encoding="utf-8", errors="replace"))
        for r in (tf if isinstance(tf, list) else []):
            c = str(r.get("code") or "").upper().strip()
            if c:
                sel[c] = r.get("mcap") or 0
    except Exception:  # noqa: BLE001
        pass
    ranked = [c for c, _ in sorted(sel.items(), key=lambda kv: -(kv[1] or 0))][:limit]
    return sorted(set(ranked) | set(board_codes()))


def _yf_quotes(codes: list, batch: int = 150) -> dict:
    try:
        import yfinance as yf
        import pandas as pd
    except Exception as e:  # noqa: BLE001
        print(f"[quotes] yfinance unavailable: {e}")
        return {}
    tick = {(c + ".BO") if str(c).isdigit() else (c + ".NS"): str(c).upper()
            for c in codes if str(c).strip()}
    syms, out = list(tick), {}
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        try:
            df = yf.download(chunk, period="2d", interval="1d", auto_adjust=False,
                             progress=False, threads=True)
            close = df["Close"]
            if isinstance(close, pd.Series):
                close = close.to_frame(name=chunk[0])
            for t in chunk:
                if t not in close.columns:
                    continue
                ser = close[t].dropna()
                if len(ser) >= 2 and float(ser.iloc[-2]):
                    prev, last = float(ser.iloc[-2]), float(ser.iloc[-1])
                    out[tick[t]] = {"ltp": round(last, 2),
                                    "pct": round((last - prev) / prev * 100, 2)}
        except Exception as e:  # noqa: BLE001
            print(f"[quotes] yahoo batch {i // batch} failed: {type(e).__name__}")
        time.sleep(0.3)
    return out


# ------------------------------------------------------------------------ main
def main() -> int:
    yf_only = os.environ.get("SCANX_QUOTES_YF") == "1"
    cloud = yf_only or os.environ.get("SCANX_QUOTES_CLOUD") == "1"
    limit = int(os.environ.get("SCANX_QUOTES_LIMIT", "500"))
    quotes, src = {}, "NSE/BSE delayed (~1-3 min)"

    if not cloud:                                  # LOCAL: Scrapling NSE/BSE provider
        try:
            from earnings_intel.data.nsequotes import NseBseProvider
            prov = NseBseProvider()
            for sym, q in (prov._nse_snapshot() or {}).items():
                lp, prev = q.get("last_price"), q.get("prev_close")
                quotes[sym] = {"ltp": lp, "pct": round((lp - prev) / prev * 100, 2) if (lp and prev) else None}
            extra = [c for c in board_codes() if c not in quotes][:30]
            if extra:
                keys = [f"BSE:{c}" if c.isdigit() else f"NSE:{c}" for c in extra]
                for k, v in prov.get_quotes(keys).items():
                    code = k.split(":", 1)[1]
                    lp, nc = v.get("last_price"), v.get("net_change")
                    prev = (v.get("ohlc") or {}).get("close")
                    base = (lp - nc) if (lp is not None and nc is not None) else prev
                    quotes[code] = {"ltp": lp, "pct": round((lp - base) / base * 100, 2) if (lp and base) else None}
        except Exception as e:  # noqa: BLE001
            print(f"[quotes] local NSE path failed ({type(e).__name__}: {e})")

    if cloud and not yf_only:                      # CLOUD: real-time BSE direct
        bq = _bse_quotes(limit)
        if bq:
            quotes.update(bq)
            src = "BSE real-time (direct)"

    if yf_only or (cloud and len(quotes) < 50):    # Yahoo: forced or gap-fill
        miss = [c for c in _universe(limit) if c.upper() not in quotes]
        yq = _yf_quotes(miss or _universe(limit))
        if yq:
            for k, v in yq.items():
                quotes.setdefault(k, v)
            src = "Yahoo delayed (~15 min)" if (yf_only or not src.startswith("BSE")) \
                else "BSE real-time + Yahoo gaps"

    if not quotes:
        print("[quotes] nothing fetched — keeping previous file")
        return 1
    now = datetime.now(IST)
    _atomic(OUT, json.dumps({
        "ts": int(time.time()),
        "ist": now.strftime("%H:%M IST"),
        "date": now.strftime("%Y-%m-%d"),
        "source": src,
        "quotes": quotes,
    }, separators=(",", ":")))
    print(f"[quotes] wrote {len(quotes)} quotes ({src}) | {now:%H:%M:%S IST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
