"""
Dhan (DhanHQ v2) live market-data adapter - an alternative to KiteProvider for
the realtime intraday engine.

It exposes the methods the engine + analytics need:
    list_instruments()  -> [{exchange, symbol, token(security_id), name}]
    get_quotes(keys)    -> {"NSE:SYMBOL": {last_price, ohlc{...}, average_price,
                            volume, net_change}, ...}
    historical(sid,seg) -> daily OHLC candles
    resolve_id(code)    -> (security_id, segment) for a screener code

Auth: needs only your Dhan client id + access token (the data-read pair).
It never touches your PIN / API secret / TOTP - generate the daily access token
separately with scripts/dhan_login.py, which keeps those out of this code.

Docs: https://dhanhq.co/docs/v2/  (Market Quote + Historical + security master)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

log = logging.getLogger("technofunda.dhan")

_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
_HIST_URL = "https://api.dhan.co/v2/charts/historical"   # daily OHLC candles
_SEG = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}     # marketfeed exchange-segment codes


# ---- cross-process Dhan circuit breaker (shared by refresh_scanx + serve.py) ----
_ROOT = Path(__file__).resolve().parents[2]
_BLOCK_FILE = _ROOT / ".cache" / "dhan_block.json"


def _token_mtime():
    try:
        return int((_ROOT / "dhan_token.json").stat().st_mtime)
    except Exception:  # noqa: BLE001
        return 0


def dhan_cooldown_left() -> int:
    """Seconds left in a Dhan cooldown (0 = clear). Auto-clears once the token
    file is replaced, so a fixed token resumes live data immediately."""
    try:
        j = json.loads(_BLOCK_FILE.read_text())
    except Exception:  # noqa: BLE001
        return 0
    if j.get("token_mtime") != _token_mtime():
        return 0
    return max(0, int(j.get("until", 0) - time.time()))


def _trip_cooldown(seconds: int, reason: str):
    try:
        _BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BLOCK_FILE.write_text(json.dumps({"until": time.time() + seconds,
                                           "reason": reason, "token_mtime": _token_mtime()}))
        log.warning("Dhan paused %ss: %s", seconds, reason)
    except Exception:  # noqa: BLE001
        pass


def _col(fieldnames, *needles):
    """Find a CSV column whose name contains all needles (case-insensitive)."""
    for fn in fieldnames or []:
        low = fn.lower()
        if all(n in low for n in needles):
            return fn
    return None


class DhanProvider:
    def __init__(self, client_id: str, access_token: str, timeout: int = 15,
                 cache_path: Optional[str] = None):
        if requests is None:
            raise ImportError("requests is required for DhanProvider: pip install requests")
        self.client_id = str(client_id)
        self.access_token = access_token
        self.timeout = timeout
        self._cache = Path(cache_path) if cache_path else None
        self.s = requests.Session()
        self.s.headers.update({
            "access-token": access_token,
            "client-id": str(client_id),
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._by_key: dict = {}     # "NSE:INFY" -> security_id
        self._instruments: Optional[list] = None
        self.last_error = None

    # --------------------------------------------------------- security master
    def _load_cache(self) -> bool:
        if not (self._cache and self._cache.exists()):
            return False
        try:
            obj = json.loads(self._cache.read_text())
            if obj.get("date") != date.today().isoformat():
                return False
            self._instruments = obj.get("instruments") or []
            self._by_key = {k: int(v) for k, v in (obj.get("by_key") or {}).items()}
            return bool(self._instruments)
        except Exception:  # noqa: BLE001
            return False

    def _save_cache(self) -> None:
        if not self._cache:
            return
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps({
                "date": date.today().isoformat(),
                "by_key": self._by_key,
                "instruments": self._instruments or [],
            }))
        except Exception as e:  # noqa: BLE001
            log.warning("Dhan master cache write failed: %s", e)

    def _load_master(self) -> list:
        if self._instruments is not None:
            return self._instruments
        if self._load_cache():
            return self._instruments
        out: list = []
        try:
            r = self.s.get(_MASTER_URL, timeout=max(self.timeout, 30))
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text))
            fn = reader.fieldnames
            c_sid = _col(fn, "security", "id")
            c_sym = _col(fn, "trading", "symbol") or _col(fn, "symbol")
            c_exch = _col(fn, "exch", "id") or _col(fn, "exm", "exch")
            c_inst = _col(fn, "instrument", "name")
            c_name = _col(fn, "custom", "symbol") or c_sym
            for row in reader:
                exch = (row.get(c_exch, "") or "").strip().upper()
                inst = (row.get(c_inst, "") or "").strip().upper()
                if exch not in _SEG or inst not in ("EQUITY", "ES", "E"):
                    continue
                sym = (row.get(c_sym, "") or "").strip().upper()
                sid = (row.get(c_sid, "") or "").strip()
                if not sym or not sid.isdigit():
                    continue
                out.append({"exchange": exch, "symbol": sym, "token": int(sid),
                            "name": (row.get(c_name, "") or sym).strip()})
                self._by_key[f"{exch}:{sym}"] = int(sid)
        except Exception as e:  # noqa: BLE001
            log.warning("Dhan scrip-master load failed: %s", e)
        self._instruments = out
        if out:
            self._save_cache()
        return out

    def list_instruments(self, exchanges=("NSE", "BSE"), eq_only: bool = True) -> list:
        return [i for i in self._load_master() if i["exchange"] in exchanges]

    # ----------------------------------------------------------------- quotes
    def _resolve(self, key: str) -> Optional[int]:
        """Map 'EXCH:SYMBOL' -> security id, or accept 'EXCH:<numeric id>' directly."""
        sid = self._by_key.get(key.upper())
        if sid is not None:
            return sid
        _, _, rest = key.partition(":")
        rest = rest.strip()
        if rest.isdigit():           # e.g. BSE:543212  (scrip code == Dhan BSE id)
            return int(rest)
        return None

    def get_quotes(self, keys: list, batch: int = 900, pause: float = 0.4) -> dict:
        left = dhan_cooldown_left()
        if left > 0:
            self.last_error = (f"Dhan paused {left}s (prior auth/rate error) — "
                               "fix the token, then it resumes automatically")
            return {}
        if not self._by_key:
            self._load_master()
        id_to_key: dict = {}
        ordered = []
        for k in keys:
            sid = self._resolve(k)
            if sid is None:
                continue
            seg = _SEG.get(k.split(":", 1)[0].upper())
            if seg:
                ordered.append((seg, sid, k))

        out: dict = {}
        for i in range(0, len(ordered), batch):
            chunk = ordered[i:i + batch]
            body: dict = {}
            for seg, sid, k in chunk:
                body.setdefault(seg, []).append(sid)
                id_to_key[(seg, str(sid))] = k
            try:
                r = self.s.post(_QUOTE_URL, json=body, timeout=self.timeout)
                if r.status_code == 200:
                    data = r.json().get("data", {})
                else:
                    self.last_error = f"HTTP {r.status_code}: {r.text[:160]}"
                    log.warning("Dhan quote HTTP %s: %s", r.status_code, r.text[:300])
                    if r.status_code in (401, 403):
                        _trip_cooldown(1800, f"HTTP {r.status_code} invalid token")
                    elif r.status_code == 429:
                        _trip_cooldown(900, "HTTP 429 rate limited")
                    data = {}
                    break   # do not fire further batches this call
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("Dhan quote batch failed: %s", e)
                data = {}
            out.update(normalize_quote_payload(data, id_to_key))
            time.sleep(pause)
        return out

    # --------------------------------------------------------- daily history
    def historical(self, security_id, segment: str = "NSE_EQ",
                   instrument: str = "EQUITY", days: int = 1100) -> Optional[dict]:
        """Daily OHLC candles -> Dhan {open,high,low,close,volume,timestamp} or None."""
        to_d = date.today(); from_d = to_d - timedelta(days=days)
        body = {"securityId": str(security_id), "exchangeSegment": segment,
                "instrument": instrument,
                "fromDate": from_d.isoformat(), "toDate": to_d.isoformat()}
        try:
            r = self.s.post(_HIST_URL, json=body, timeout=max(self.timeout, 30))
            if r.status_code != 200:
                log.warning("Dhan historical %s %s -> HTTP %s", segment, security_id, r.status_code)
                return None
            j = r.json()
            return j if (isinstance(j, dict) and j.get("close")) else None
        except Exception as e:  # noqa: BLE001
            log.warning("Dhan historical failed (%s): %s", security_id, e)
            return None

    def resolve_id(self, code: str):
        """(security_id, segment) for a screener code: numeric -> BSE, else NSE symbol."""
        code = str(code).strip().upper()
        if code.isdigit():
            return int(code), "BSE_EQ"
        if not self._by_key:
            self._load_master()
        sid = self._by_key.get(f"NSE:{code}")
        if sid is not None:
            return sid, "NSE_EQ"
        sid = self._by_key.get(f"BSE:{code}")
        if sid is not None:
            return sid, "BSE_EQ"
        return None, None


def normalize_quote_payload(payload: dict, id_to_key: dict) -> dict:
    """Pure helper (testable): Dhan /marketfeed/quote 'data' -> engine quote dict."""
    out: dict = {}
    for seg, rows in (payload or {}).items():
        for sid, q in (rows or {}).items():
            key = id_to_key.get((seg, str(sid)))
            if not key:
                continue
            ohlc = q.get("ohlc") or {}
            out[key] = {
                "last_price": q.get("last_price"),
                "average_price": q.get("average_price"),
                "volume": q.get("volume"),
                "net_change": q.get("net_change"),
                "ohlc": {"open": ohlc.get("open"), "high": ohlc.get("high"),
                         "low": ohlc.get("low"), "close": ohlc.get("close")},
            }
    return out
