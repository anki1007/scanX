"""
Agent 1 (live) — NSE & BSE corporate filings feed.

Polls the public NSE and BSE endpoints for corporate announcements, quarterly
results and board meetings, and normalises them into `Announcement` objects with
a coarse type classification (results / corporate-action / other).

Reality notes:
- NSE blocks non-browser requests, so we prime cookies by hitting the homepage
  with a browser User-Agent first, then call the JSON API with a Referer.
- BSE's API is friendlier but still wants a browser UA + Referer.
- Both can rate-limit or change shape without notice. Every call is wrapped so a
  failure logs and returns [] rather than crashing the scanner.

This module is intentionally dependency-light (just `requests`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

log = logging.getLogger("technofunda.feed")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_RESULT_KEYWORDS = (
    "financial result", "results", "outcome of board meeting", "earnings",
    "quarterly", "audited", "unaudited", "standalone", "consolidated",
)
_EVENT_KEYWORDS = (
    "order", "acqui", "merger", "buyback", "bonus", "split", "dividend",
    "fund rais", "qip", "preferential", "rating", "credit", "resignation",
    "appointment",
)


@dataclass
class Announcement:
    source: str                 # "NSE" or "BSE"
    symbol: str
    company: str
    headline: str
    category: str
    dt: Optional[datetime]
    url: str
    uid: str                    # stable id for dedup
    kind: str = "other"         # results | corporate_action | other

    def classify(self) -> str:
        text = f"{self.category} {self.headline}".lower()
        if any(k in text for k in _RESULT_KEYWORDS):
            return "results"
        if any(k in text for k in _EVENT_KEYWORDS):
            return "corporate_action"
        return "other"


class NseBseFeed:
    def __init__(self, timeout: int = 12):
        if requests is None:
            raise ImportError("requests is required for the live feed: pip install requests")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._nse_primed = False

    # ------------------------------------------------------------------- NSE
    def _prime_nse(self) -> None:
        try:
            self.s.get("https://www.nseindia.com/", timeout=self.timeout)
            self.s.get("https://www.nseindia.com/companies-listing/"
                       "corporate-filings-announcements", timeout=self.timeout)
            self._nse_primed = True
        except Exception as e:  # noqa: BLE001
            log.warning("NSE cookie priming failed: %s", e)

    def _nse_get(self, url: str) -> Optional[list | dict]:
        if not self._nse_primed:
            self._prime_nse()
        try:
            r = self.s.get(url, headers={
                "Referer": "https://www.nseindia.com/companies-listing/"
                           "corporate-filings-announcements"}, timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            log.warning("NSE %s -> HTTP %s", url, r.status_code)
        except Exception as e:  # noqa: BLE001
            log.warning("NSE fetch failed (%s): %s", url, e)
            self._nse_primed = False        # force re-prime next time
        return None

    def fetch_nse_announcements(self) -> list[Announcement]:
        data = self._nse_get("https://www.nseindia.com/api/corporate-announcements?index=equities")
        out: list[Announcement] = []
        for it in (data or []):
            sym = (it.get("symbol") or "").strip()
            headline = (it.get("desc") or it.get("attchmntText") or "").strip()
            cat = (it.get("smIndustry") or it.get("desc") or "").strip()
            att = it.get("attchmntFile") or ""
            dt = _parse_dt(it.get("an_dt") or it.get("sort_date"))
            a = Announcement(
                source="NSE", symbol=sym, company=(it.get("sm_name") or sym),
                headline=headline[:300], category=cat[:120], dt=dt, url=att,
                uid=f"NSE:{sym}:{it.get('an_dt') or att or headline[:40]}")
            a.kind = a.classify()
            out.append(a)
        return out

    def fetch_nse_results(self) -> list[Announcement]:
        data = self._nse_get("https://www.nseindia.com/api/corporates-financial-results"
                             "?index=equities&period=Quarterly")
        out: list[Announcement] = []
        for it in (data or []):
            sym = (it.get("symbol") or "").strip()
            dt = _parse_dt(it.get("re_broadcast_timestamp") or it.get("creation_Date"))
            a = Announcement(
                source="NSE", symbol=sym, company=(it.get("companyName") or sym),
                headline=f"Quarterly results filed ({it.get('period') or ''})",
                category="Financial Results", dt=dt,
                url=it.get("xbrl") or it.get("naturalPdfUrl") or "",
                uid=f"NSE-RES:{sym}:{it.get('re_broadcast_timestamp') or it.get('creation_Date')}",
                kind="results")
            out.append(a)
        return out

    # ------------------------------------------------------------------- BSE
    def fetch_bse_announcements(self, on: Optional[date] = None) -> list[Announcement]:
        on = on or date.today()
        ymd = on.strftime("%Y%m%d")
        url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
               f"?pageno=1&strCat=-1&strPrevDate={ymd}&strScrip=&strSearch=P"
               f"&strToDate={ymd}&strType=C&subcategory=-1")
        try:
            r = self.s.get(url, headers={
                "Referer": "https://www.bseindia.com/",
                "Origin": "https://www.bseindia.com"}, timeout=self.timeout)
            payload = r.json() if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            log.warning("BSE fetch failed: %s", e)
            return []

        rows = payload.get("Table", []) if isinstance(payload, dict) else []
        out: list[Announcement] = []
        for it in rows:
            scrip = str(it.get("SCRIP_CD") or "").strip()
            head = (it.get("NEWSSUB") or it.get("HEADLINE") or "").strip()
            cat = (it.get("CATEGORYNAME") or "").strip()
            att = it.get("ATTACHMENTNAME") or ""
            url_pdf = (f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}"
                       if att else "")
            dt = _parse_dt(it.get("NEWS_DT") or it.get("DT_TM"))
            a = Announcement(
                source="BSE", symbol=(it.get("SLONGNAME") or scrip),
                company=(it.get("SLONGNAME") or scrip),
                headline=head[:300], category=cat[:120], dt=dt, url=url_pdf,
                uid=f"BSE:{scrip}:{it.get('NEWSID') or it.get('NEWS_DT') or head[:40]}")
            a.kind = a.classify()
            out.append(a)
        return out

    # --------------------------------------------------------------- combined
    def fetch_all(self) -> list[Announcement]:
        items: list[Announcement] = []
        for fn in (self.fetch_nse_announcements, self.fetch_nse_results,
                   self.fetch_bse_announcements):
            try:
                items.extend(fn())
            except Exception as e:  # noqa: BLE001
                log.warning("feed %s failed: %s", getattr(fn, "__name__", fn), e)
        return items


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y",
                "%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt)
        except ValueError:
            continue
    return None
