"""
NSE/BSE no-login quote provider — the free fallback when there is no Dhan
(or Kite) token, or the Dhan token has died mid-day.

NSE  : ONE batch call per poll — /api/equity-stockIndices?index=NIFTY TOTAL MARKET
       returns LTP + change for the ~750-name liquid universe in a single
       request. Cached 60s, so the exchange sees at most one hit per minute
       (effective delay 1-3 min, which is fine for the boards).
BSE  : per-scrip getScripHeaderData JSON, only for codes actually on screen,
       cached 120s with a small fresh-lookup budget per cycle.

Same contract as DhanProvider (get_quotes / list_instruments / last_error),
so serve.py /api/quote and the realtime engine use it interchangeably.
Polite by construction: batch + TTL caches + an error cooldown.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("technofunda.nsequotes")

_NSE_HOME = "https://www.nseindia.com/"
_NSE_API = ("https://www.nseindia.com/api/equity-stockIndices"
            "?index=NIFTY%20TOTAL%20MARKET")
_NSE_QUOTE = "https://www.nseindia.com/api/quote-equity?symbol={sym}"
_NSE_REFERER = "https://www.nseindia.com/market-data/live-equity-market"
_BSE_API = ("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
            "?Debtflag=&scripcode={code}&seriesid=")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_SNAP_TTL = 60.0          # one NSE batch call per minute max
_BSE_TTL = 120.0          # per-scrip cache
_BSE_BUDGET = 25          # max fresh BSE lookups per get_quotes call
_ERR_COOLDOWN = 300.0     # back off 5 min if the exchange blocks us


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ pure: parsers
def parse_nse_snapshot(j: dict) -> dict:
    """NSE equity-stockIndices payload -> {SYMBOL: {last_price, net_change,
    prev_close}}. The first row is the index itself — it has no real symbol
    price semantics, so rows without a usable lastPrice are skipped."""
    out = {}
    for row in (j or {}).get("data", []):
        sym = str(row.get("symbol") or "").strip().upper()
        lp = _num(row.get("lastPrice"))
        if not sym or lp is None:
            continue
        if row.get("priority") == 1 and " " in sym:      # index header row
            continue
        out[sym] = {
            "last_price": lp,
            "net_change": _num(row.get("change")),
            "prev_close": _num(row.get("previousClose")),
        }
    return out


def parse_nse_quote(j: dict) -> Optional[dict]:
    """NSE quote-equity payload (jugaad-data style, per symbol) -> quote dict."""
    p = (j or {}).get("priceInfo") or {}
    lp = _num(p.get("lastPrice"))
    if lp is None:
        return None
    return {"last_price": lp, "net_change": _num(p.get("change")),
            "prev_close": _num(p.get("previousClose"))}


def parse_bse_header(j: dict) -> Optional[dict]:
    """BSE getScripHeaderData payload -> {last_price, net_change, prev_close}.
    BSE renames keys between API versions, so scan the header dict defensively."""
    h = (j or {}).get("Header") or (j or {}).get("header") or j or {}
    if not isinstance(h, dict):
        return None
    flat = {}
    stack = [h]
    while stack:                 # flatten one or two nested levels
        d = stack.pop()
        for k, v in d.items():
            if isinstance(v, dict):
                stack.append(v)
            else:
                flat.setdefault(str(k).lower(), v)
    lp = next((_num(flat[k]) for k in ("ltp", "lasttradedprice", "currval", "rate")
               if k in flat and _num(flat[k]) is not None), None)
    if lp is None:
        return None
    chg = next((_num(flat[k]) for k in ("chg", "change") if k in flat), None)
    prev = next((_num(flat[k]) for k in ("prevclose", "previousclose", "pclose")
                 if k in flat), None)
    return {"last_price": lp, "net_change": chg, "prev_close": prev}


# ------------------------------------------------------------------ provider
class NseBseProvider:
    def __init__(self, timeout: int = 6):
        from . import webscrap
        self.s = webscrap.http_session()          # curl_cffi Chrome-TLS if available
        try:
            self.s.headers.update({"User-Agent": _UA, "Accept": "*/*",
                                   "Accept-Language": "en-US,en;q=0.9"})
        except Exception:  # noqa: BLE001
            pass
        self.timeout = timeout
        self.last_error: Optional[str] = None
        self._warm_ts = 0.0
        self._snap = {"ts": 0.0, "data": {}}
        self._bse: dict = {}                      # code -> {ts, q}
        self._nse1: dict = {}                     # off-index symbol -> {ts, q}
        self._cooldown_until = 0.0

    # ---- NSE
    def _warmup(self):
        """NSE wants homepage cookies before the JSON API answers."""
        if time.time() - self._warm_ts < 600:
            return
        try:
            self.s.get(_NSE_HOME, timeout=self.timeout)
            self._warm_ts = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("NSE warmup failed: %s", e)

    def _nse_snapshot(self) -> dict:
        now = time.time()
        if now - self._snap["ts"] < _SNAP_TTL or now < self._cooldown_until:
            return self._snap["data"]
        self._warmup()
        data = {}
        try:
            r = self.s.get(_NSE_API, timeout=self.timeout,
                           headers={"Referer": _NSE_REFERER})
            if getattr(r, "status_code", 0) != 200:
                raise RuntimeError(f"NSE HTTP {getattr(r, 'status_code', '?')}")
            data = parse_nse_snapshot(r.json())
        except Exception as e:  # noqa: BLE001
            self.last_error = f"NSE snapshot: {type(e).__name__}: {str(e)[:80]}"
            log.warning(self.last_error)
        if not data:
            # bot-walled? full fallback chain incl. the Camoufox stealth browser
            # (Scrapling) which renders the JSON like a real visitor.
            try:
                from . import webscrap
                j = webscrap.fetch_json(_NSE_API, headers={"Referer": _NSE_REFERER})
                data = parse_nse_snapshot(j or {})
            except Exception as e:  # noqa: BLE001
                log.warning("NSE stealth fallback failed: %s", e)
        if data:
            self._snap = {"ts": now, "data": data}
            self.last_error = None
        else:
            self._cooldown_until = now + _ERR_COOLDOWN
        return self._snap["data"]

    def _nse_single(self, sym: str) -> Optional[dict]:
        """Per-symbol quote-equity (covers SME/off-index names), cached + budgeted."""
        now = time.time()
        hit = self._nse1.get(sym)
        if hit and now - hit["ts"] < _BSE_TTL:
            return hit["q"]
        if now < self._cooldown_until:
            return hit["q"] if hit else None
        try:
            import urllib.parse
            r = self.s.get(_NSE_QUOTE.format(sym=urllib.parse.quote(sym)),
                           timeout=self.timeout, headers={"Referer": _NSE_REFERER})
            if getattr(r, "status_code", 0) != 200:
                raise RuntimeError(f"HTTP {getattr(r, 'status_code', '?')}")
            q = parse_nse_quote(r.json())
            self._nse1[sym] = {"ts": now, "q": q}
            return q
        except Exception as e:  # noqa: BLE001
            self.last_error = f"NSE {sym}: {type(e).__name__}: {str(e)[:60]}"
            return hit["q"] if hit else None

    # ---- BSE
    def _bse_quote(self, code: str) -> Optional[dict]:
        now = time.time()
        hit = self._bse.get(code)
        if hit and now - hit["ts"] < _BSE_TTL:
            return hit["q"]
        if now < self._cooldown_until:
            return hit["q"] if hit else None
        q = None
        try:
            r = self.s.get(_BSE_API.format(code=code), timeout=self.timeout,
                           headers={"Referer": "https://www.bseindia.com/"})
            if getattr(r, "status_code", 0) != 200:
                raise RuntimeError(f"BSE HTTP {getattr(r, 'status_code', '?')}")
            q = parse_bse_header(r.json())
            self._bse_fails = 0
        except Exception as e:  # noqa: BLE001
            self.last_error = f"BSE {code}: {type(e).__name__}: {str(e)[:60]}"
            # fail FAST: two consecutive failures = exchange is blocking us this
            # cycle -> trip the shared cooldown so we never stack 6s timeouts
            # per code and starve the rest of the local API.
            self._bse_fails = getattr(self, "_bse_fails", 0) + 1
            if self._bse_fails >= 2:
                self._cooldown_until = now + _ERR_COOLDOWN
        if q is not None:
            self._bse[code] = {"ts": now, "q": q}
            return q
        return hit["q"] if hit else None

    # ---- provider contract
    def list_instruments(self) -> list:
        """Tradable universe for the realtime engine: the NSE Total Market
        snapshot names (one request covers them all every cycle)."""
        snap = self._nse_snapshot()
        return [{"exchange": "NSE", "symbol": s} for s in sorted(snap)]

    def get_quotes(self, keys: list, batch: int = 900, pause: float = 0.0) -> dict:
        out: dict = {}
        snap = None
        fresh_bse = 0
        fresh_nse1 = 0
        for k in keys:
            ex, _, sym = str(k).partition(":")
            ex = ex.upper(); sym = sym.strip().upper()
            if not sym:
                continue
            if ex == "NSE":
                if snap is None:
                    snap = self._nse_snapshot()
                q = snap.get(sym)
                if q is None and (sym in self._nse1 or fresh_nse1 < 10):
                    if sym not in self._nse1 or time.time() - self._nse1[sym]["ts"] >= _BSE_TTL:
                        fresh_nse1 += 1
                    q = self._nse_single(sym)     # SME / off-index names
            elif ex == "BSE":
                if fresh_bse >= _BSE_BUDGET and sym not in self._bse:
                    continue
                if sym not in self._bse or time.time() - self._bse[sym]["ts"] >= _BSE_TTL:
                    fresh_bse += 1
                q = self._bse_quote(sym)
            else:
                continue
            if q and q.get("last_price") is not None:
                out[k] = {"last_price": q["last_price"],
                          "net_change": q.get("net_change"),
                          "ohlc": {"close": q.get("prev_close")}}
        return out


_PROV = {"p": None, "tried": False}


def provider() -> Optional[NseBseProvider]:
    """Cached singleton (the session + caches are reused across calls)."""
    if _PROV["tried"]:
        return _PROV["p"]
    _PROV["tried"] = True
    try:
        _PROV["p"] = NseBseProvider()
    except Exception as e:  # noqa: BLE001
        log.warning("NSE/BSE provider unavailable: %s", e)
        _PROV["p"] = None
    return _PROV["p"]
