"""
Screener.in scraper (Step 1: "Go to Screener.in and see Results").

Parses https://www.screener.in/results/latest/ directly: one
<table class="data-table"> per company with columns [latest quarter, previous
quarter, year-ago quarter] for Sales / EBIDT / Net profit / EPS, beside a
"Price ... M.Cap ... PE ..." line. One page fetch yields ~25 scored companies.

`/results/latest/` needs a logged-in session. Two ways to authenticate:
  * Email/password  -> ScreenerClient().login(email, password)   (auto-login)
  * Reuse a cookie  -> ScreenerClient(session_id="...")

Dependencies: requests, beautifulsoup4, lxml.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.screener")

_BASE = "https://www.screener.in"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class QuarterSeries:
    labels: list[str] = field(default_factory=list)
    sales: list[float] = field(default_factory=list)
    op: list[float] = field(default_factory=list)
    opm: list[float] = field(default_factory=list)
    np: list[float] = field(default_factory=list)
    eps: list[float] = field(default_factory=list)


@dataclass
class ScreenerCompany:
    code: str
    name: str
    url: str
    market_cap: Optional[float] = None
    last_price: Optional[float] = None
    pe: Optional[float] = None
    result_date: Optional[str] = None
    quarters: Optional[QuarterSeries] = None


def _num(text) -> Optional[float]:
    if text is None:
        return None
    t = str(text).replace(",", "").replace("₹", "").replace("%", "").strip()
    if t in ("", "-", "—", "–", "None"):
        return None
    m = re.search(r"-?\d+\.?\d*", t)
    return float(m.group()) if m else None


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100.0, 2)


def _parse_price_line(text: str):
    if not text:
        return (None, None, None)
    t = text.replace(",", "")
    lp = re.search(r"Price\s*₹?\s*([\d.]+)", t)
    mc = re.search(r"M\.?Cap\s*₹?\s*([\d.]+)", t)
    pe = re.search(r"\bPE\s*([\d.]+)", t)
    f = lambda m: float(m.group(1)) if m else None
    return (f(lp), f(mc), f(pe))


def metrics_from_rows(name: str, code: str, rows: list[list[str]],
                      price_line: str = "") -> dict:
    header = rows[0] if rows else []
    latest_q = header[2] if len(header) > 2 else None
    data: dict[str, list] = {}
    for r in rows[1:]:
        if not r:
            continue
        label = r[0].strip().lower()
        data[label] = [_num(x) for x in r[2:5]]

    def trip(key):
        return data.get(key, [None, None, None])

    sales, npr, ebidt = trip("sales"), trip("net profit"), trip("ebidt")
    lp, mc, pe = _parse_price_line(price_line)
    return {
        "code": code, "name": name,
        "url": f"{_BASE}/company/{code}/",
        "last_price": lp, "market_cap": mc, "pe": pe, "result_date": latest_q,
        "sales_yoy": _pct(sales[0], sales[2]), "sales_qoq": _pct(sales[0], sales[1]),
        "np_yoy": _pct(npr[0], npr[2]), "np_qoq": _pct(npr[0], npr[1]),
        "ebitda_yoy": _pct(ebidt[0], ebidt[2]), "ebitda_qoq": _pct(ebidt[0], ebidt[1]),
    }


def parse_results_soup(soup) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for table in soup.select("table.data-table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.select("th,td")]
                for tr in table.select("tr")]
        if len(rows) < 2:
            continue
        # the company-name link (skip the row's "PDF" link and compare links)
        link = table.find_previous(
            lambda t: getattr(t, "name", None) == "a"
            and (t.get("href", "") or "").startswith("/company/")
            and "/compare/" not in (t.get("href", "") or "")
            and t.get_text(strip=True).upper() not in ("", "PDF"))
        if not link:
            continue
        name = link.get_text(strip=True)
        m = re.search(r"/company/([^/]+)/", link.get("href", ""))
        code = m.group(1) if m else ""
        if not code or code in seen:        # de-duplicate repeated tables
            continue
        seen.add(code)
        price_node = table.find_previous(string=re.compile(r"M\.?Cap"))
        price_line = ""
        if price_node:
            blk = price_node.parent
            for _ in range(4):                       # climb to the full Price/M.Cap/PE block
                if blk is None:
                    break
                txt = blk.get_text(" ", strip=True)
                if "Price" in txt and "Cap" in txt:
                    price_line = txt
                    break
                blk = blk.parent
            if not price_line and price_node.parent:
                price_line = price_node.parent.get_text(" ", strip=True)
        out.append(metrics_from_rows(name, code, rows, price_line))
    return out


def _screen_num(s):
    if not s:
        return None
    m = re.search(r"-?\d+(\.\d+)?", str(s).replace(",", "").replace("%", "").strip())
    return float(m.group()) if m else None


def _screen_colmap(headers):
    """Map Screener screen header labels -> our field keys (robust to reordering)."""
    m = {}
    for i, h in enumerate(headers):
        hl = h.lower().strip()
        if hl.startswith("cmp"): m["cmp"] = i
        elif hl.startswith("p/e"): m["pe"] = i
        elif "mar cap" in hl or "market cap" in hl: m.setdefault("mcap", i)
        elif "qtr profit var" in hl: m["profit_var"] = i
        elif "qtr sales var" in hl: m["sales_var"] = i
        elif "roce3yr" in hl: m["roce"] = i                       # 3Y ROCE (funnel + sector)
        elif hl.endswith("roce %") and "roce" not in m: m["roce"] = i
        elif "chg in fii" in hl: m["fii_chg"] = i
        elif "52w low" in hl: m["low_52w"] = i
        elif "all time high" in hl: m["ath"] = i
        elif "ev" in hl and "ebitda" in hl: m["ev_ebitda"] = i    # query-ratio column (magic formula)
    return m


class ScreenerClient:
    def __init__(self, session_id: Optional[str] = None, timeout: int = 15,
                 delay: float = 1.0):
        if requests is None or BeautifulSoup is None:
            raise ImportError("requests + beautifulsoup4 + lxml required: "
                              "pip install requests beautifulsoup4 lxml")
        self.timeout = timeout
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": _UA,
                               "Accept-Language": "en-US,en;q=0.9"})
        if session_id:
            self.s.cookies.set("sessionid", session_id, domain=".screener.in")

    def login(self, email: str, password: str) -> bool:
        """Email/password login -> sets the session cookie on this client."""
        try:
            r = self.s.get(f"{_BASE}/login/", timeout=self.timeout)
            soup = BeautifulSoup(r.text, "lxml")
            form = soup.find("form")
            payload: dict[str, str] = {}
            names: list[str] = []
            if form:
                for inp in form.find_all("input"):
                    n = inp.get("name")
                    if not n:
                        continue
                    names.append(n)
                    if (inp.get("type") or "").lower() == "hidden":
                        payload[n] = inp.get("value", "")
            csrf = payload.get("csrfmiddlewaretoken") or self.s.cookies.get("csrftoken")
            if csrf:
                payload["csrfmiddlewaretoken"] = csrf
            userfield = next((n for n in names if n.lower() in
                              ("username", "login", "email")), "username")
            payload[userfield] = email
            payload["password"] = password
            action = (form.get("action") if form else "") or "/login/"
            url = action if action.startswith("http") else _BASE + action
            self.s.post(url, data=payload, headers={"Referer": f"{_BASE}/login/"},
                        timeout=self.timeout, allow_redirects=True)
            if self.authenticated():
                return True
            log.warning("Screener email/password login failed - check credentials.")
            return False
        except Exception as e:  # noqa: BLE001
            log.warning("Screener login error: %s", e)
            return False

    def session_id(self) -> Optional[str]:
        return self.s.cookies.get("sessionid")

    def _get(self, url: str, retries: int = 3):
        for attempt in range(retries + 1):
            try:
                r = self.s.get(url, timeout=self.timeout)
                if "login" in r.url or "register" in r.url:
                    log.warning("Screener redirected to login - session missing/expired")
                    return None
                if r.status_code == 429:           # rate limited -> back off and retry
                    if attempt < retries:
                        time.sleep(6 * (attempt + 1)); continue
                    log.warning("Screener 429 (rate limited): %s", url); return None
                if r.status_code != 200:
                    log.warning("Screener %s -> HTTP %s", url, r.status_code)
                    return None
                return BeautifulSoup(r.text, "lxml")
            except Exception as e:  # noqa: BLE001
                if attempt < retries:
                    time.sleep(2); continue
                log.warning("Screener fetch failed (%s): %s", url, e)
                return None
        return None

    def authenticated(self) -> bool:
        return self._get(f"{_BASE}/dash/") is not None

    def fetch_latest_results(self, max_pages: int = 2) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            url = f"{_BASE}/results/latest/" + (f"?page={page}" if page > 1 else "")
            soup = self._get(url)
            if soup is None:
                break
            items = parse_results_soup(soup)
            fresh = [d for d in items if d.get("code") and d["code"] not in seen]
            for d in fresh:
                seen.add(d["code"])
            if not fresh:          # page empty or a repeat of an earlier page -> stop
                break
            out.extend(fresh)
            time.sleep(self.delay)
        return out

    def _fetch_table(self, url_for_page, max_pages: int) -> list:
        """Shared paginated table reader for /screen/raw/ and /market/ pages."""
        out, seen, colmap = [], set(), None
        for page in range(1, max_pages + 1):
            soup = self._get(url_for_page(page))
            if soup is None:
                break
            tbl = soup.select_one("table.data-table") or soup.select_one("table")
            if tbl is None:
                break
            trs = tbl.find_all("tr")
            if colmap is None:
                for tr in trs:
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                    if any(c == "Name" for c in cells) and any(c.startswith("CMP") for c in cells):
                        colmap = _screen_colmap(cells); break
                if not colmap:
                    break
            page_codes = 0
            for tr in trs:
                a = tr.find("a", href=re.compile(r"/company/"))
                cells = tr.find_all("td")
                if not a or len(cells) < 5:
                    continue
                m = re.search(r"/company/([^/]+)/", a["href"])
                code = m.group(1) if m else None
                if not code or code in seen:
                    continue
                seen.add(code); page_codes += 1
                vals = [c.get_text(" ", strip=True) for c in cells]

                def col(key):
                    i = colmap.get(key)
                    return vals[i] if (i is not None and i < len(vals)) else None

                row = {
                    "code": code, "name": a.get_text(strip=True),
                    "cmp": _screen_num(col("cmp")), "pe": _screen_num(col("pe")),
                    "mcap": _screen_num(col("mcap")),
                    "profit_var": _screen_num(col("profit_var")),
                    "sales_var": _screen_num(col("sales_var")),
                    "roce": _screen_num(col("roce")), "fii_chg": _screen_num(col("fii_chg")),
                    "low_52w": _screen_num(col("low_52w")), "ath": _screen_num(col("ath")),
                }
                if "ev_ebitda" in colmap:
                    row["ev_ebitda"] = _screen_num(col("ev_ebitda"))
                out.append(row)
            if page_codes == 0:
                break
            time.sleep(self.delay)
        return out

    def fetch_screen(self, query: str = "Market Capitalization > 100",
                     max_pages: int = 200) -> list:
        """Broad universe from Screener's screen (header-mapped columns)."""
        import urllib.parse
        q = urllib.parse.quote(query)
        return self._fetch_table(lambda p: f"{_BASE}/screen/raw/?query={q}&page={p}", max_pages)

    def fetch_market(self, sector_code: str, max_pages: int = 200) -> list:
        """All companies in a Screener top-level sector (/market/INxx/)."""
        return self._fetch_table(
            lambda p: f"{_BASE}/market/{sector_code}/" + (f"?page={p}" if p > 1 else ""),
            max_pages)
