"""
Delayed/real-time NSE/BSE quotes -> docs/data/quotes.json  (NO broker, NO login).

  * LOCAL (scanX.bat): NSE batch + BSE per-scrip via the Scrapling provider.
  * CLOUD (GitHub Actions, datacenter IP): set SCANX_QUOTES_CLOUD=1.
      NSE blocks datacenter IPs (403); BSE does not. So we pull REAL-TIME from BSE:
        - scrip master once -> {symbol -> scripcode, + mcap}
        - getScripHeaderData per scrip  -> ltp, %chg, open, high, low, prev_close
        - StockTrading for the top movers -> VWAP + volume
      Yahoo is only a last-resort gap-filler.  SCANX_QUOTES_YF=1 forces Yahoo.

The boards read this STATIC file, so quotes render the same on GitHub Pages and
locally. Cadence = the workflow cron (~15 min during market hours).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
_BSE_HDR = ("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
            "?Debtflag=&scripcode={code}&seriesid=")
_BSE_TRADE = "https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?scripcode={code}&flag=0"
_TIMEOUT = 7


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
    try:
        rows = _bse_session().get(_BSE_LIST, timeout=45).json()
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


def _bse_header(code: str):
    try:
        j = _bse_session().get(_BSE_HDR.format(code=code), timeout=_TIMEOUT).json() or {}
        h, cr = j.get("Header") or {}, j.get("CurrRate") or {}
        ltp = _num(h.get("LTP") or cr.get("LTP"))
        if ltp is None:
            return None
        return {"ltp": round(ltp, 2), "pct": _num(cr.get("PcChg")),
                "open": _num(h.get("Open")), "high": _num(h.get("High")),
                "low": _num(h.get("Low")), "prev_close": _num(h.get("PrevClose"))}
    except Exception:  # noqa: BLE001
        return None


def _bse_trade(code: str):
    try:
        j = _bse_session().get(_BSE_TRADE.format(code=code), timeout=_TIMEOUT).json() or {}
        vwap = _num(j.get("WAP"))
        ttq = _num(j.get("TTQ"))                       # total traded qty, in lakh
        return {"vwap": vwap, "vol": int(ttq * 100000) if ttq is not None else None}
    except Exception:  # noqa: BLE001
        return None


def _fetch_map(items, fn, workers, budget):
    out, deadline = {}, time.time() + budget
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, code): key for key, code in items}
        for fut in as_completed(futs):
            try:
                q = fut.result()
                if q:
                    out[futs[fut]] = q
            except Exception:  # noqa: BLE001
                pass
            if time.time() > deadline:
                for f2 in futs:
                    f2.cancel()
                break
    return out


def _bse_quotes(limit: int):
    sym2code, ranked = _bse_master()
    if not ranked:
        return {}
    targets = {}                                       # board-key -> scripcode
    for code, sym, _mc in ranked[:limit]:
        targets[(sym or code)] = code
    for c in board_codes():
        if c in targets:
            continue
        if c.isdigit():
            targets[c] = c
        elif c in sym2code:
            targets[c] = sym2code[c]
    # phase 1: OHLC + %chg for everyone (one call each)
    out = _fetch_map(list(targets.items()), _bse_header, workers=10, budget=130)
    # phase 2: VWAP + volume only for the top movers that will actually display
    top = sorted((k for k in out if out[k].get("pct") is not None),
                 key=lambda k: -abs(out[k]["pct"]))[:60]
    trades = _fetch_map([(k, targets[k]) for k in top], _bse_trade, workers=8, budget=30)
    for k, tr in trades.items():
        out[k].update(tr)
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
                if t in close.columns:
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

    if not cloud:
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

    if cloud and not yf_only:
        bq = _bse_quotes(limit)
        if bq:
            quotes.update(bq)
            src = "BSE real-time (direct)"

    if yf_only or (cloud and len(quotes) < 50):
        miss = [c for c in _universe(limit) if c.upper() not in quotes]
        yq = _yf_quotes(miss or _universe(limit))
        if yq:
            for k, v in yq.items():
                quotes.setdefault(k, v)
            src = "Yahoo delayed (~15 min)" if (yf_only or not src.startswith("BSE")) else "BSE real-time + Yahoo gaps"

    if not quotes:
        print("[quotes] nothing fetched — keeping previous file")
        return 1
    now = datetime.now(IST)
    _atomic(OUT, json.dumps({
        "ts": int(time.time()), "ist": now.strftime("%H:%M IST"),
        "date": now.strftime("%Y-%m-%d"), "source": src, "quotes": quotes,
    }, separators=(",", ":")))
    nfull = sum(1 for v in quotes.values() if v.get("open") is not None)
    print(f"[quotes] wrote {len(quotes)} quotes ({nfull} with OHLC) ({src}) | {now:%H:%M:%S IST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
