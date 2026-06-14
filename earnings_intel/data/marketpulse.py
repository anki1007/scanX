"""
Screener.in "Market Pulse" + FII feeds (logged-in pages).

Provides four data pulls, each a pure parser (testable on saved HTML) plus a
fetch wrapper that uses the cached Screener session:

  fetch_fii(sid)          -> /fii/            sector-wise FII net flow
  fetch_trades(kind,sid)  -> /trades/<kind>/  bulk | block | insiders | sast
  fetch_actions(kind,sid) -> /actions/<kind>/ dividend | bonus | split | right
  fetch_announcements(sid)-> /announcements/   market-wide corporate filings

All pages need a logged-in session (sessionid cookie), same as the rest of the
Screener scraping. Network runs on the user's machine (where screener.in is
reachable); parsers are pure so they unit-test offline.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.marketpulse")

_BASE = "https://www.screener.in"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TRADE_KINDS = ("bulk", "block", "insiders", "sast")
ACTION_KINDS = ("dividend", "bonus", "split", "right")


# --------------------------------------------------------------- http helpers
def _client(session_id: Optional[str]):
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    if session_id:
        s.cookies.set("sessionid", session_id, domain=".screener.in")
    return s


def _soup(sess, url: str, timeout: int = 20, retries: int = 3):
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            if "login" in r.url or "register" in r.url:
                log.warning("Screener redirect to login (session missing/expired): %s", url)
                return None
            if r.status_code == 429:
                if attempt < retries:
                    time.sleep(6 * (attempt + 1)); continue
                log.warning("Screener 429 rate-limited: %s", url); return None
            if r.status_code != 200:
                log.warning("Screener %s -> HTTP %s", url, r.status_code); return None
            return BeautifulSoup(r.text, "lxml")
        except Exception as e:  # noqa: BLE001
            if attempt < retries:
                time.sleep(2); continue
            log.warning("Screener fetch failed (%s): %s", url, e); return None
    return None


# --------------------------------------------------------------- small parsers
def _money(s) -> Optional[float]:
    """'₹ -1,45,774 Cr' / '+ 154' / '11.69 crore' -> float (in the unit shown)."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _code_from(tag) -> Optional[str]:
    a = tag.find("a", href=True) if tag else None
    if not a:
        return None
    m = re.search(r"/company/([^/]+)/", a["href"])
    return m.group(1) if m else None


# --------------------------------------------------------------------- FII
def parse_fii(soup) -> list:
    """/fii/ -> [{sector, code, aum, fortnight, oneY}] (₹ Cr net flows)."""
    out = []
    for art in soup.select("article.box"):
        a = art.select_one(".box-title a")
        if not a:
            continue
        txt = art.get_text(" ", strip=True)
        if "of AUM" not in txt:
            continue
        href = a.get("href", "") or ""
        code = [p for p in href.split("/") if p][-1] if "/market/" in href else None
        aum = re.search(r"([\d.]+)\s*%\s*of AUM", txt)
        fn = re.search(r"([▲▼])\s*([+\-]?\s*[\d,]+)\s*Cr", txt)        # badge = fortnight
        y1 = re.search(r"([+\-]?[\d,]+)\s*Cr\s*1Y net flow", txt)
        fortnight = _money(fn.group(2)) if fn else None
        if fn and fn.group(1) == "▼" and fortnight is not None and fortnight > 0:
            fortnight = -fortnight
        out.append({
            "sector": a.get_text(strip=True),
            "code": code,
            "aum": float(aum.group(1)) if aum else None,
            "fortnight": fortnight,
            "oneY": _money(y1.group(1)) if y1 else None,
        })
    return out


# ------------------------------------------------------------ generic table
def parse_table(soup) -> tuple:
    """First <table> -> (headers, [row-dict ...]); adds 'code' from a /company/ link."""
    t = soup.find("table")
    if not t:
        return [], []
    heads = [th.get_text(" ", strip=True) for th in t.select("thead th")]
    rows = []
    for tr in t.select("tbody tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        vals = [c.get_text(" ", strip=True) for c in cells]
        d = dict(zip(heads, vals)) if heads else {"cells": vals}
        code = _code_from(tr)
        if code:
            d["code"] = code
        rows.append(d)
    return heads, rows


def parse_trades(soup, kind: str) -> list:
    """/trades/<kind>/ -> [{deal, company, code, person, date, type, value_cr, qty}]."""
    heads, rows = parse_table(soup)
    out = []
    for d in rows:
        g = lambda *ks: next((d[k] for k in ks if d.get(k)), "")  # noqa: E731
        val = g("Value")
        out.append({
            "deal": kind,
            "company": g("Company"),
            "code": d.get("code"),
            "person": g("Person", "Acquirer/Seller", "Client", "Acquirer"),
            "date": g("Date"),
            "type": g("Type", "Action", "Transaction"),
            "value_cr": _money(re.search(r"[\d.,]+\s*crore", val).group(0)) if re.search(r"[\d.,]+\s*crore", val) else _money(val),
            "qty": (re.search(r"crore\s*([\d,]+)", val).group(1) if re.search(r"crore\s*([\d,]+)", val) else None),
        })
    return out


def parse_actions(soup, kind: str) -> list:
    """/actions/<kind>/ -> [{action, company, code, ex_date, detail, ...raw}]."""
    heads, rows = parse_table(soup)
    out = []
    for d in rows:
        ex = d.get("Ex date") or d.get("Ex-date") or d.get("Date") or ""
        extra = [f"{k}: {v}" for k, v in d.items()
                 if k not in ("Company", "code", "Ex date", "Ex-date", "Date") and v]
        out.append({
            "action": kind,
            "company": d.get("Company", ""),
            "code": d.get("code"),
            "ex_date": ex,
            "detail": " · ".join(extra)[:160],
        })
    return out


def parse_announcements(soup, limit: int = 120) -> list:
    """/announcements/ -> [{company, code, title, when}] (best-effort card feed)."""
    out, seen = [], set()
    for a in soup.select('a[href*="/company/"]'):
        m = re.search(r"/company/([^/]+)/", a.get("href", "") or "")
        if not m:
            continue
        company = a.get_text(" ", strip=True)
        if not company or len(company) > 60:
            continue
        # climb to the announcement card (a container a bit larger than the name)
        item = a
        for _ in range(5):
            if item.parent is None:
                break
            item = item.parent
            if len(item.get_text(" ", strip=True)) > len(company) + 20:
                break
        full = item.get_text(" ", strip=True)
        title = full.replace(company, "", 1).strip(" -|·:•")
        # split a trailing date if present
        when = ""
        dm = re.search(r"(Today|Yesterday|\d{1,2}\s+\w+\s+\d{4}|\d{1,2}:\d{2}\s*[ap]m)$", title)
        if dm:
            when = dm.group(1); title = title[:dm.start()].strip(" -|·:•")
        key = (m.group(1), title[:50])
        if key in seen:
            continue
        seen.add(key)
        out.append({"company": company, "code": m.group(1), "title": title[:200], "when": when})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------- fetch wrappers
def fetch_fii(session_id: Optional[str]) -> list:
    if requests is None:
        return []
    soup = _soup(_client(session_id), f"{_BASE}/fii/")
    return parse_fii(soup) if soup else []


def fetch_trades(kind: str, session_id: Optional[str]) -> list:
    if requests is None or kind not in TRADE_KINDS:
        return []
    soup = _soup(_client(session_id), f"{_BASE}/trades/{kind}/")
    return parse_trades(soup, kind) if soup else []


def fetch_actions(kind: str, session_id: Optional[str]) -> list:
    if requests is None or kind not in ACTION_KINDS:
        return []
    soup = _soup(_client(session_id), f"{_BASE}/actions/{kind}/")
    return parse_actions(soup, kind) if soup else []


def fetch_announcements(session_id: Optional[str]) -> list:
    if requests is None:
        return []
    soup = _soup(_client(session_id), f"{_BASE}/announcements/")
    return parse_announcements(soup) if soup else []


def parse_buyback_actions(soup) -> dict:
    """/actions/buyback/ -> {CODE: {company, offer_type, max_price, end_date,
    ex_date, amount_cr}}.  This table is the AUTHORITATIVE Tender-vs-Open-Market
    flag (the announcement-text heuristic misses it for most filings)."""
    heads, rows = parse_table(soup)
    out = {}
    for d in rows:
        code = d.get("code")
        if not code:
            continue
        out[str(code).upper()] = {
            "company": d.get("Company", ""),
            "offer_type": d.get("Offer Type") or d.get("Type") or "",
            "max_price": _money(d.get("Max Price")),
            "ex_date": d.get("Ex date") or d.get("Ex-date") or "",
            "end_date": d.get("End date") or "",
            "amount_cr": _money(d.get("Amount in Cr") or d.get("Amount")),
        }
    return out


def fetch_buyback_actions(session_id):
    """Live pull of Screener's /actions/buyback/ table (most-recent first)."""
    if requests is None:
        return {}
    soup = _soup(_client(session_id), f"{_BASE}/actions/buyback/?o=-1")
    return parse_buyback_actions(soup) if soup else {}
