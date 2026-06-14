"""
Screener.in full-text-search client + Special-situations screener.

This is the spreadsheet's data source. One reusable fetch_fulltext() powers the
Orders, Buybacks and Special Situations tabs (the user's exact keyword queries),
so everything runs off the (already working) Screener session instead of the
blocked BSE API.

Result-card DOM (verified live):
  div  > a[href="/company/<CODE>/..."]  (company name; skip /company/compare/)
       > div  (announcement title)
       > div  (snippet)
       > div  "Announcement - 06 Jun 2026"   (type + date)
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Optional

from .orders import _UA, _SCREENER

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.special")

# ---- the user's exact screener keyword queries ----------------------------
ORDERS_Q = ('-"Commissioner" -"tax" -"gst" "order received" or "award of order" or '
            '"Notification of Award" or "letter of intent" or "large order" or '
            '"Order for Procurement" or "Awarding of order" or "bagged an order" or '
            '"Letter of Award" or "repeat order" or "additional order" or "Contract Award"')
BUYBACK_Q = "buyback"
SPECIAL_QUERIES = {
    "events": '"open offer" or "delisting" or "demerger" or "scheme of arrangement" or "NCLT"',
    "warrants": '"Preferential Issue" or "Issue Of Warrants" or "Preferential Allotment"',
}


def parse_date(text: str) -> Optional[str]:
    if not text:
        return None
    m = (re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", text)
         or re.search(r"(\d{4}-\d{2}-\d{2})", text))
    return m.group(1) if m else None


def _client(session_id: Optional[str]):
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    if session_id:
        s.cookies.set("sessionid", session_id, domain=".screener.in")
    return s


def parse_results(soup) -> list:
    """Parse a Screener full-text-search page -> [{code,name,snippet,date,url}]."""
    out: list = []
    seen: set = set()
    for a in soup.select('a[href^="/company/"]'):
        href = a.get("href", "") or ""
        if "/company/compare/" in href:          # sector filter links, not results
            continue
        m = re.search(r"/company/([^/]+)/", href.rstrip("/") + "/")
        if not m:
            continue
        code = m.group(1).strip()
        name = a.get_text(" ", strip=True)
        if not code or code.lower() == "compare" or not name or code in seen:
            continue
        card = a
        full = ""
        for _ in range(6):
            card = card.parent
            if card is None:
                break
            t = card.get_text(" ", strip=True)
            if re.search(r"(Announcement|Concall|Result|Transcript)\s*-\s*\d", t):
                full = t
                break
        full = full or name
        snippet = full[len(name):].strip() if full.startswith(name) else full
        pdf = ""
        if card is not None:
            a2 = card.find("a", href=re.compile(r"\.pdf|AnnPdfOpen|nsearchives", re.I))
            if a2:
                pdf = a2.get("href", "") or ""
        seen.add(code)
        out.append({"code": code, "name": name, "snippet": snippet[:300],
                    "date": parse_date(full), "url": f"{_SCREENER}/company/{code}/",
                    "pdf_url": pdf})
    return out


def fetch_fulltext(session_id: Optional[str], query: str, max_pages: int = 1,
                   timeout: int = 15, delay: float = 1.0,
                   announcements_only: bool = False) -> list:
    """Screener full-text-search -> de-duplicated result rows (needs a session)."""
    if requests is None or BeautifulSoup is None:
        raise ImportError("requests + beautifulsoup4 + lxml required")
    s = _client(session_id)
    out: list = []
    seen: set = set()
    status = None
    for page in range(1, max_pages + 1):
        url = (f"{_SCREENER}/full-text-search/?q=" + urllib.parse.quote_plus(query)
               + ("&type=announcements" if announcements_only else "")
               + (f"&page={page}" if page > 1 else ""))
        try:
            r = s.get(url, timeout=timeout)
            status = r.status_code
            if "login" in r.url or r.status_code != 200:
                log.warning("full-text-search needs login / http %s", r.status_code)
                break
            rows = parse_results(BeautifulSoup(r.text, "lxml"))
        except Exception as e:  # noqa: BLE001
            log.warning("full-text-search failed: %s", e)
            break
        fresh = [d for d in rows if d["code"] not in seen]
        for d in fresh:
            seen.add(d["code"])
        if not fresh:
            break
        out.extend(fresh)
        time.sleep(delay)
    log.warning("full-text-search '%s...': %d rows (http %s)", query[:22], len(out), status)
    return out


# ---- Special-situations layer ---------------------------------------------
_CATS = [("open offer", "Open Offer"), ("delist", "Delisting"),
         ("demerg", "Demerger"), ("scheme of arrangement", "Scheme/Arrangement"),
         ("nclt", "NCLT"), ("warrant", "Warrant"), ("preferential", "Preferential")]


def detect_category(text: str) -> str:
    low = (text or "").lower()
    for needle, label in _CATS:
        if needle in low:
            return label
    return "Special"


@dataclass
class SpecialFiling:
    code: str
    name: str
    category: str
    headline: str
    date: Optional[str]
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_special(session_id: Optional[str], max_pages: int = 1) -> list:
    out: list = []
    seen: set = set()
    for q in SPECIAL_QUERIES.values():
        for d in fetch_fulltext(session_id, q, max_pages=max_pages, announcements_only=True):
            if d["code"] in seen:
                continue
            seen.add(d["code"])
            out.append(SpecialFiling(code=d["code"], name=d["name"],
                                     category=detect_category(d["snippet"]),
                                     headline=d["snippet"], date=d["date"], url=d["url"]))
    return out
