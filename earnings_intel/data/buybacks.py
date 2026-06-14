"""
Buyback arbitrage screener (scanX 'Buybacks' tab).

Framework (from the flowchart):
  Screen buybacks -> focus on TENDER offers (open-market shown but de-prioritised)
  -> calculate the spread you can capture (buyback price vs CMP)
  -> >=8% premium = candidate -> see technicals + fundamentals -> invest.

Source: BSE corporate announcements filtered to 'Buy-back' (free, cookie-primed
like the Orders scraper). Buyback price / type / record date are pulled from the
announcement text (best-effort) - every row links to the filing to verify.
CMP comes from Dhan; fundamentals from Screener (both reused elsewhere).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .orders import parse_value_cr, _BSE_ANN, _BSE_PDF, _UA  # reuse leaf helpers

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

log = logging.getLogger("technofunda.buybacks")


# --------------------------------------------------------------- text parsing
def parse_buyback_price(text: str) -> Optional[float]:
    """Best-effort buyback price per share (INR) from announcement text."""
    if not text:
        return None
    t = text.replace(",", "")
    # Rs 500 (/-) per (equity/fully paid-up) share
    m = re.search(r"(?:rs\.?|inr|₹)\s*([\d.]+)\s*/?-?\s*per\s+"
                  r"(?:fully\s+paid[\s-]*up\s+)?(?:equity\s+)?share", t, re.I)
    if m:
        return round(float(m.group(1)), 2)
    # buy-back price ... Rs 500
    m = re.search(r"buy[\s-]?back\s+price\D{0,25}?(?:rs\.?|inr|₹)\s*([\d.]+)", t, re.I)
    if m:
        return round(float(m.group(1)), 2)
    # (maximum) price of Rs 500
    m = re.search(r"(?:maximum\s+)?price\s+of\s+(?:rs\.?|inr|₹)\s*([\d.]+)", t, re.I)
    if m:
        return round(float(m.group(1)), 2)
    return None


def parse_buyback_type(text: str) -> str:
    low = (text or "").lower()
    if "tender" in low:
        return "Tender"
    if "open market" in low or "stock exchange" in low or "open-market" in low:
        return "Open Market"
    return ""


def parse_record_date(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"record date\D{0,12}(\d{1,2}[\s./-][A-Za-z0-9]{2,9}[\s./-]\d{2,4})",
                  text, re.I)
    return m.group(1).strip() if m else None


def is_buyback(subcat: str, headline: str) -> bool:
    s = f"{subcat} {headline}".lower()
    return ("buyback" in s) or ("buy-back" in s) or ("buy back" in s)


def premium_pct(buyback_price: Optional[float], cmp: Optional[float]) -> Optional[float]:
    """The spread you can capture: (buyback price - CMP) / CMP, in %."""
    if buyback_price is None or not cmp:
        return None
    return round((buyback_price - cmp) / cmp * 100.0, 2)


# --- exact acceptance-ratio arbitrage math (from the user's sheet) ------------
SMALL_HOLDING_DEFAULT = 0.05      # assumed small-shareholder holding when unknown
PARTICIPATION_DEFAULT = 0.5       # assumed tender participation (sheet default)
GATE = 0.08                       # flowchart: >=8% = candidate


def acceptance_general(buyback_pct, small_holding, participation):
    """L = (1 - C) * B / D."""
    if None in (buyback_pct, small_holding, participation) or not participation:
        return None
    return (1 - small_holding) * buyback_pct / participation


def acceptance_small(buyback_pct, small_holding, participation):
    """M = B * 0.15 / C / D  (SEBI reserves 15% for small shareholders)."""
    if None in (buyback_pct, small_holding, participation) or not (small_holding and participation):
        return None
    return buyback_pct * 0.15 / small_holding / participation


def expected_money(buyback_price, price_post, buy_price, acceptance):
    """(G*AR + I*(1-AR) - F) / F  -> fractional return."""
    if None in (buyback_price, price_post, buy_price, acceptance) or not buy_price:
        return None
    return (buyback_price * acceptance + price_post * (1 - acceptance) - buy_price) / buy_price


def compute_buyback(buyback_price, cmp, size_cr, market_cap,
                    small_holding=SMALL_HOLDING_DEFAULT,
                    participation=PARTICIPATION_DEFAULT, price_post=None):
    """Run the sheet's math. CMP is the pre-record / buy price (F);
    price_post defaults to CMP (conservative); buyback % is estimated from
    offer size vs market cap when not supplied."""
    f, g = cmp, buyback_price
    i = price_post if price_post is not None else f
    b = (size_cr * f / (g * market_cap)) if (size_cr and g and market_cap and f) else None
    L = acceptance_general(b, small_holding, participation)
    M = acceptance_small(b, small_holding, participation)
    J = expected_money(g, i, f, L)
    K = expected_money(g, i, f, M)
    rnd = lambda x, n=4: round(x, n) if x is not None else None
    return {"buyback_pct": rnd(b), "small_holding": small_holding,
            "participation": participation, "pre_record_price": f, "price_post": i,
            "acc_general": rnd(L), "acc_small": rnd(M),
            "exp_money_general": rnd(J), "exp_money_small": rnd(K)}


# ------------------------------------------------------------------ dataclass
@dataclass
class BuybackFiling:
    code: str
    name: str
    exchange: str
    date: str
    buyback_type: str
    buyback_price: Optional[float]
    record_date: Optional[str]
    size_cr: Optional[float]
    headline: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ BSE source
class BSEBuybacks:
    def __init__(self, timeout: int = 20, delay: float = 0.4,
                 cache_path: Optional[str] = None, cache_minutes: int = 720):
        if requests is None:
            raise ImportError("requests required: pip install requests")
        self.timeout = timeout
        self.delay = delay
        self._cache = Path(cache_path) if cache_path else None
        self.cache_minutes = cache_minutes
        self.last_status = None
        self.last_raw = 0
        from .webscrap import http_session
        self.s = http_session()              # curl_cffi (Chrome-TLS) bypasses BSE's bot-block
        self.s.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.bseindia.com/corporates/ann.html",
            "Origin": "https://www.bseindia.com",
        })

    def _cached(self) -> Optional[list]:
        if not (self._cache and self._cache.exists()):
            return None
        try:
            obj = json.loads(self._cache.read_text())
            if datetime.now() - datetime.fromisoformat(obj["ts"]) < timedelta(minutes=self.cache_minutes):
                return [BuybackFiling(**d) for d in obj.get("rows", [])]
        except Exception:  # noqa: BLE001
            return None
        return None

    def _prime(self) -> None:
        for u in ("https://www.bseindia.com/",
                  "https://www.bseindia.com/corporates/ann.html"):
            try:
                self.s.get(u, timeout=self.timeout)
            except Exception:  # noqa: BLE001
                pass

    def _page(self, params: dict) -> list:
        from .webscrap import fetch_json
        payload = fetch_json(_BSE_ANN, params=params, timeout=self.timeout,
                             headers={"Referer": "https://www.bseindia.com/corporates/ann.html",
                                      "Origin": "https://www.bseindia.com"})
        self.last_status = 200 if payload is not None else -1
        if isinstance(payload, dict):
            return payload.get("Table", [])
        if isinstance(payload, list):
            return payload
        return []

    def fetch(self, months: int = 12, max_pages: int = 30) -> list:
        cached = self._cached()
        if cached is not None:
            return cached
        self._prime()
        to_d = date.today()
        from_d = to_d - timedelta(days=int(months * 30.5))
        base = {"strPrevDate": from_d.strftime("%Y%m%d"),
                "strToDate": to_d.strftime("%Y%m%d"),
                "strSearch": "P", "strscrip": "", "strType": "C"}
        strategies = [
            {**base, "strCat": "Company Update", "subcategory": "Buy-back"},
            {**base, "strCat": "-1"},
        ]
        out: list = []
        seen: set = set()
        raw = 0
        for si, extra in enumerate(strategies):
            got = 0
            for page in range(1, max_pages + 1):
                rows = self._page({**extra, "pageno": page})
                if not rows:
                    break
                raw += len(rows); got += len(rows)
                for row in rows:
                    code = str(row.get("SCRIP_CD", "") or "").strip()
                    name = (row.get("SLONGNAME") or "").strip()
                    head = (row.get("NEWSSUB") or row.get("HEADLINE") or "").strip()
                    more = (row.get("MORE") or "").strip()
                    sub = (row.get("SUBCATNAME") or row.get("CATEGORYNAME") or "").strip()
                    dt = (row.get("NEWS_DT") or "")[:10]
                    att = (row.get("ATTACHMENTNAME") or "").strip()
                    k = f"{code}:{dt}:{head[:50]}"
                    if not code or k in seen:
                        continue
                    seen.add(k)
                    if not is_buyback(sub, head):
                        continue
                    text = f"{head} {more}"
                    url = (_BSE_PDF + att) if att else \
                        f"https://www.bseindia.com/stock-share-price/x/x/{code}/"
                    out.append(BuybackFiling(
                        code=code, name=name, exchange="BSE", date=dt,
                        buyback_type=parse_buyback_type(text),
                        buyback_price=parse_buyback_price(text),
                        record_date=parse_record_date(text),
                        size_cr=parse_value_cr(text), headline=head, url=url))
                time.sleep(self.delay)
            log.warning("BSE buyback strategy %d: %d raw, %d matches (http %s)",
                        si, got, len(out), self.last_status)
            if out:
                break
        self.last_raw = raw
        if self._cache and out:
            try:
                self._cache.parent.mkdir(parents=True, exist_ok=True)
                self._cache.write_text(json.dumps(
                    {"ts": datetime.now().isoformat(),
                     "rows": [o.to_dict() for o in out]}))
            except Exception:  # noqa: BLE001
                pass
        return out
