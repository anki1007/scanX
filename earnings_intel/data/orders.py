"""
Order / contract-win intelligence (the scanX 'Orders' tab).

Two free sources, fetched from the user's machine like the Screener scraper:
  * BSE announcements API  -> 'Award of Order / Receipt of Order' filings
        https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w
  * Screener company page  -> fundamentals (market cap, sales, OPM%, quarterly
        Net Profit & EPS, annual revenue)   https://www.screener.in/company/<CODE>/

We extract contract value / customer / duration from the announcement text with
best-effort regex (free text is noisy), and keep the filing URL on every row so
a human can verify the number. Network failures degrade gracefully to [].
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

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.orders")

_BSE_ANN = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
_BSE_PDF = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
_SCREENER = "https://www.screener.in"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# --------------------------------------------------------------- text parsing
def parse_value_cr(text: str) -> Optional[float]:
    """Best-effort: pull an order value in INR crore from announcement / PDF text.

    Handles '1,734 crore', 'Rs 50 lakh', '500 million', and bare rupee amounts in
    Indian-comma format ('Rs. 1,05,30,600' -> 1.05 Cr).
    """
    if not text:
        return None
    t = text.replace(",", "")
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(r"(?:rs\.?|inr|₹)?\s*" + num + r"\s*(?:crore|crores|cr)\b", t, re.I)
    if m:
        return round(float(m.group(1)), 2)
    m = re.search(r"(?:rs\.?|inr|₹)?\s*" + num + r"\s*(?:lakh|lakhs|lacs?)\b", t, re.I)
    if m:
        return round(float(m.group(1)) * 0.01, 2)
    m = re.search(r"(?:rs\.?|inr|₹)?\s*" + num + r"\s*(?:million|mn)\b", t, re.I)
    if m:
        return round(float(m.group(1)) * 0.1, 2)
    # prefer a rupee amount sitting next to an order/value keyword; else the largest
    ctx = re.findall(r"(?:order|contract|value|worth|award|aggregat|total)[^.]{0,45}?"
                     r"(?:rs\.?|inr|₹)\s*([0-9]{7,})", t, re.I)
    pool = ctx or re.findall(r"(?:rs\.?|inr|₹)\s*([0-9]{7,})", t, re.I)
    if pool:
        return round(max(int(a) for a in pool) / 1e7, 2)
    return None


def parse_duration(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(\d+)\s*(months?|years?|yrs?|days?|weeks?)\b", text, re.I)
    return f"{m.group(1)} {m.group(2).lower()}" if m else None


def parse_customer(text: str) -> Optional[str]:
    """Grab a likely counterparty after 'from'/'by' (very best-effort)."""
    if not text:
        return None
    m = re.search(r"\b(?:from|by)\s+([A-Z][\w&.\-() ]{3,60}?)"
                  r"(?:\s+(?:for|worth|of|valued|amounting|to|towards)\b|[.,;]|$)", text)
    return m.group(1).strip() if m else None


_ORDER_TYPES = [
    ("purchase order", "Purchase Order"), ("work order", "Work Order"),
    ("letter of award", "Letter of Award"), ("letter of intent", "Letter of Intent"),
    ("loa", "LOA"), ("supply order", "Supply Order"), ("contract", "Contract"),
    ("order", "Order"),
]


def parse_order_type(text: str) -> str:
    low = (text or "").lower()
    for k, v in _ORDER_TYPES:
        if k in low:
            return v
    return "Order"


def looks_like_order(subcat: str, headline: str) -> bool:
    s = f"{subcat} {headline}".lower()
    return any(w in s for w in ("order", "contract", "letter of award",
                                "work order", "loa", "awarded", "bagged",
                                "secures", "wins ", "won "))


# ------------------------------------------------------------------ dataclasses
@dataclass
class OrderFiling:
    code: str
    name: str
    exchange: str
    date: str
    order_type: str
    headline: str
    value_cr: Optional[float]
    customer: Optional[str]
    duration: Optional[str]
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompanyFundamentals:
    code: str
    market_cap: Optional[float] = None       # Cr
    cmp: Optional[float] = None               # current price (Screener, Dhan fallback)
    revenue_fy: Optional[float] = None        # latest annual sales, Cr
    sales_latest_q: Optional[float] = None
    opm_latest: Optional[float] = None        # %
    np_prev_q: Optional[float] = None
    np_latest_q: Optional[float] = None
    eps_prev_q: Optional[float] = None
    eps_latest_q: Optional[float] = None
    np_growth_qoq: Optional[float] = None     # %
    eps_growth_qoq: Optional[float] = None     # %

    def to_dict(self) -> dict:
        return asdict(self)


def _num(t) -> Optional[float]:
    if t is None:
        return None
    t = str(t).replace(",", "").replace("₹", "").replace("%", "").replace("Cr", "").strip()
    if t in ("", "-", "—", "–"):
        return None
    m = re.search(r"-?\d+\.?\d*", t)
    return float(m.group()) if m else None


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100.0, 2)


# ------------------------------------------------------------------ BSE source
class BSEOrders:
    def __init__(self, timeout: int = 20, delay: float = 0.4,
                 cache_path: Optional[str] = None, cache_minutes: int = 30):
        if requests is None:
            raise ImportError("requests required: pip install requests")
        self.timeout = timeout
        self.delay = delay
        self._cache = Path(cache_path) if cache_path else None
        self.cache_minutes = cache_minutes
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
                return [OrderFiling(**d) for d in obj.get("rows", [])]
        except Exception:  # noqa: BLE001
            return None
        return None

    def _prime(self) -> None:
        """Establish BSE session cookies; the API returns nothing to a cold request."""
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

    def fetch(self, months: int = 6, max_pages: int = 25) -> list:
        cached = self._cached()
        if cached is not None:
            return cached
        self._prime()
        to_d = date.today()
        from_d = to_d - timedelta(days=int(months * 30.5))
        base = {"strPrevDate": from_d.strftime("%Y%m%d"),
                "strToDate": to_d.strftime("%Y%m%d"),
                "strSearch": "P", "strscrip": "", "strType": "C"}
        # A) targeted subcategory; B) all announcements + keyword filter (fallback)
        strategies = [
            {**base, "strCat": "Company Update",
             "subcategory": "Award of Order / Receipt of Order"},
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
                    if not looks_like_order(sub, head):
                        continue
                    text = f"{head} {more}"
                    url = (_BSE_PDF + att) if att else \
                        f"https://www.bseindia.com/stock-share-price/x/x/{code}/"
                    out.append(OrderFiling(
                        code=code, name=name, exchange="BSE", date=dt,
                        order_type=parse_order_type(text), headline=head,
                        value_cr=parse_value_cr(text), customer=parse_customer(text),
                        duration=parse_duration(text), url=url))
                time.sleep(self.delay)
            log.warning("BSE strategy %d: %d raw rows, %d order matches (http %s)",
                        si, got, len(out), getattr(self, "last_status", "?"))
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


# ------------------------------------------------- Screener company fundamentals
def _row_values(table, *labels) -> list:
    """Return numeric cells of the first body row whose label matches."""
    for tr in table.select("tr"):
        cells = tr.select("td")
        if not cells:
            continue
        head = cells[0].get_text(" ", strip=True).lower().replace("\xa0", " ")
        if any(head.startswith(lbl) for lbl in labels):
            return [_num(c.get_text(" ", strip=True)) for c in cells[1:]]
    return []


class ScreenerFundamentals:
    def __init__(self, session_id: Optional[str] = None, timeout: int = 15,
                 delay: float = 0.6, cache_path: Optional[str] = None):
        if requests is None or BeautifulSoup is None:
            raise ImportError("requests + beautifulsoup4 + lxml required")
        self.timeout = timeout
        self.delay = delay
        self._cache = Path(cache_path) if cache_path else None
        self._mem: dict = {}
        self._disk = self._load_disk()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
        if session_id:
            self.s.cookies.set("sessionid", session_id, domain=".screener.in")

    def _load_disk(self) -> dict:
        if self._cache and self._cache.exists():
            try:
                obj = json.loads(self._cache.read_text())
                if obj.get("date") == date.today().isoformat():
                    return obj.get("rows", {})
            except Exception:  # noqa: BLE001
                pass
        return {}

    def _save_disk(self) -> None:
        if not self._cache:
            return
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": date.today().isoformat(), "rows": self._disk}))
            tmp.replace(self._cache)        # atomic: no half-written/corrupt cache
        except Exception:  # noqa: BLE001
            pass

    def fetch(self, code: str) -> CompanyFundamentals:
        code = str(code).strip()
        if code in self._mem:
            return self._mem[code]
        if code in self._disk:
            f = CompanyFundamentals(**self._disk[code])
            self._mem[code] = f
            return f
        f = CompanyFundamentals(code=code)
        for attempt in range(3):
            try:
                r = self.s.get(f"{_SCREENER}/company/{code}/", timeout=self.timeout)
                if r.status_code == 200:
                    self._parse(BeautifulSoup(r.text, "lxml"), f)
                    break
                if r.status_code in (429, 503):          # throttled -> back off + retry
                    time.sleep(2.0 * (attempt + 1))
                    continue
            except Exception as e:  # noqa: BLE001
                log.warning("Screener fundamentals failed (%s): %s", code, e)
                time.sleep(1.0)
        self._mem[code] = f
        if f.market_cap is not None:                     # cache only hits; misses retry next run
            self._disk[code] = f.to_dict()
            self._save_disk()
        time.sleep(self.delay)
        return f

    @staticmethod
    def _parse(soup, f: CompanyFundamentals) -> None:
        # market cap from the top ratios list
        for li in soup.select("#top-ratios li, .company-ratios li"):
            name = li.select_one(".name")
            if not name:
                continue
            nm = name.get_text(strip=True).lower()
            num = li.select_one(".number") or li.select_one(".value")
            val = _num(num.get_text() if num else li.get_text())
            if "market cap" in nm and f.market_cap is None:
                f.market_cap = val
            elif "current price" in nm and f.cmp is None:
                f.cmp = val
        # quarterly results table
        q = soup.select_one("#quarters table") or soup.select_one("section#quarters table")
        if q:
            sales = _row_values(q, "sales", "revenue")
            opm = _row_values(q, "opm")
            npr = _row_values(q, "net profit")
            eps = _row_values(q, "eps")
            if len(sales) >= 1:
                f.sales_latest_q = sales[-1]
            if len(opm) >= 1:
                f.opm_latest = opm[-1]
            if len(npr) >= 2:
                f.np_latest_q, f.np_prev_q = npr[-1], npr[-2]
                f.np_growth_qoq = _pct(npr[-1], npr[-2])
            elif len(npr) == 1:
                f.np_latest_q = npr[-1]
            if len(eps) >= 2:
                f.eps_latest_q, f.eps_prev_q = eps[-1], eps[-2]
                f.eps_growth_qoq = _pct(eps[-1], eps[-2])
            elif len(eps) == 1:
                f.eps_latest_q = eps[-1]
        # latest annual revenue from the P&L table
        pl = soup.select_one("#profit-loss table") or soup.select_one("section#profit-loss table")
        if pl:
            sales_y = _row_values(pl, "sales", "revenue")
            if sales_y:
                f.revenue_fy = sales_y[-1]


def order_size_pct(value_cr: Optional[float], revenue_fy: Optional[float]) -> Optional[float]:
    if value_cr is None or not revenue_fy:
        return None
    return round(value_cr / revenue_fy * 100.0, 2)
