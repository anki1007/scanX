"""
Screener "Documents" section scraper - concalls, annual reports, announcements.

The Screener company page carries a Documents section with three panels that
`company.fundamentals()` ignores entirely. This module turns those panels into
the `documents` list of the earnings-intel contract (newest first)::

    from earnings_intel.data.documents import fetch_documents

    docs = fetch_documents("TATAMOTORS", session_id=SESSION)
    docs[0]
    {'kind': 'concall_transcript', 'date': '2026-05-01',
     'title': 'May 2026 Concall Transcript',
     'url': 'https://www.bseindia.com/.../transcript.pdf', 'source': 'bse'}

`parse_documents_soup(soup, base_url)` is the pure half (no network), so the DOM
handling is unit-testable from an inline fixture::

    from bs4 import BeautifulSoup
    parse_documents_soup(BeautifulSoup(html, "lxml"),
                         "https://www.screener.in/company/TATAMOTORS/")

DOM (Screener company page):
  div.documents.concalls        ul.list-links li -> div "May 2026"
                                                    a "Transcript" | a "PPT"
                                                    a "REC" + button "AI Summary" (skipped)
  div.documents.annual-reports  ul.list-links li -> a "Financial Year 2025"
                                                      span "from bse"
  div.documents.announcements   ul.list-links li -> a "<headline>" span "25 Apr"

Read-only. Any panel may be absent, hrefs may be relative, and nothing raises:
a failed fetch degrades to an empty list so a bake never dies here.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import date, timedelta
from typing import Optional

from .orders import _UA, _SCREENER

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.documents")

__all__ = ["fetch_documents", "parse_documents_soup"]

# ------------------------------------------------------------------ constants
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I)
_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_DM_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\b")
_REL_RE = re.compile(r"\b(\d{1,3})\s*(h|d|w|mo)\b", re.I)
_FY_RE = re.compile(r"(?:financial\s+year|fy)\s*[-:]?\s*(\d{4})(?:\s*[-/]\s*(\d{2,4}))?", re.I)

# concall button labels that are NOT documents
_SKIP_LABELS = ("rec", "recording", "audio", "video", "ai summary", "summary",
                "add missing", "raise request")
_CONCALL_TITLES = {"concall_transcript": "Transcript", "concall_ppt": "PPT",
                   "concall_notes": "Notes"}


# -------------------------------------------------------------------- helpers
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _strings(el) -> list:
    """Visible text nodes of an element, in order ('Financial Year 2025', 'from bse')."""
    return [t for t in (_clean(x) for x in el.stripped_strings) if t]


def _abs(base_url: str, href: str) -> str:
    """Resolve a (possibly relative) href against the company page URL."""
    href = _clean(href)
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return ""
    return urllib.parse.urljoin(base_url or f"{_SCREENER}/", href)


def _source(url: str, note: str = "") -> str:
    """Contract allows 'screener' | 'bse' - anything served off BSE is 'bse'."""
    hay = f"{url} {note}".lower()
    return "bse" if ("bseindia" in hay or "from bse" in hay) else "screener"


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _iso_month(text: str) -> str:
    """Concall row label -> ISO first-of-month. 'May 2026' -> '2026-05-01'."""
    m = _MONTH_YEAR_RE.search(_clean(text))
    if not m:
        return ""
    mon = _MONTHS.get(m.group(1)[:3].lower())
    d = _safe_date(int(m.group(2)), mon, 1) if mon else None
    return d.isoformat() if d else ""


def _iso_date(text: str, today: Optional[date] = None) -> str:
    """Best-effort announcement date -> ISO. Handles '2 May 2026', '25 Apr', '13h', '3d'."""
    t = _clean(text)
    if not t:
        return ""
    today = today or date.today()
    m = _ISO_RE.search(t)
    if m:
        return m.group(1)
    m = _DMY_RE.search(t)
    if m:
        mon = _MONTHS.get(m.group(2)[:3].lower())
        d = _safe_date(int(m.group(3)), mon, int(m.group(1))) if mon else None
        if d:
            return d.isoformat()
    m = _REL_RE.search(t)                      # Screener's '13h' / '3d' recency badge
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = 0 if unit == "h" else n * {"d": 1, "w": 7, "mo": 30}[unit]
        return (today - timedelta(days=days)).isoformat()
    m = _DM_RE.search(t)                       # '25 Apr' - year omitted when recent
    if m:
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            d = _safe_date(today.year, mon, int(m.group(1)))
            if d and d > today + timedelta(days=7):
                d = _safe_date(today.year - 1, mon, int(m.group(1)))
            if d:
                return d.isoformat()
    return ""


def _fy_iso(text: str) -> str:
    """'Financial Year 2025' / 'FY 2024-25' -> Indian FY end '2025-03-31'."""
    m = _FY_RE.search(_clean(text))
    if not m:
        return ""
    start, tail = int(m.group(1)), m.group(2)
    end = start
    if tail:
        end = int(tail) if len(tail) == 4 else (start // 100) * 100 + int(tail)
    d = _safe_date(end, 3, 31)
    return d.isoformat() if d else ""


# --------------------------------------------------------------- panel lookup
def _panel_key(el) -> str:
    """Classify a Documents panel by its css classes, falling back to its heading."""
    tokens = " ".join(el.get("class") or []).lower().replace("_", "-")
    head = el.find(["h2", "h3", "h4"])
    label = _clean(head.get_text(" ")).lower() if head is not None else ""
    hay = f"{tokens} {label}"
    if "concall" in hay:
        return "concalls"
    if "annual-report" in hay or "annual report" in hay:
        return "annual_reports"
    if "announcement" in hay:
        return "announcements"
    return ""


def _container_after(head):
    """Sibling list/div that follows a heading (used when panel classes are absent)."""
    for sib in head.next_siblings:
        if getattr(sib, "name", None) in ("ul", "ol", "div", "table"):
            return sib
    return head.parent


def _panels(soup) -> dict:
    """{'concalls'|'annual_reports'|'announcements': element} - any may be missing."""
    found: dict = {}
    for el in soup.select("div.documents, section.documents"):
        key = _panel_key(el)
        if key and key not in found:
            found[key] = el
    if len(found) == 3:
        return found
    for head in soup.find_all(["h2", "h3", "h4"]):     # class-less / older markup
        label = _clean(head.get_text(" ")).lower()
        key = ("concalls" if "concall" in label else
               "annual_reports" if "annual report" in label else
               "announcements" if "announcement" in label else "")
        if key and key not in found:
            found[key] = _container_after(head)
    return found


# -------------------------------------------------------------- panel parsers
def _concall_kind(label: str, url: str) -> str:
    """Map a concall row button to a contract kind ('' = not a document)."""
    lab = (label or "").lower()
    if any(s in lab for s in _SKIP_LABELS):
        return ""
    if "transcript" in lab:
        return "concall_transcript"
    if "ppt" in lab or "presentation" in lab:
        return "concall_ppt"
    if "notes" in lab:
        # Screener's own generated notes are not a company document; an external
        # (BSE-hosted) notes file is.
        return "" if "screener.in" in (url or "").lower() else "concall_notes"
    return ""


def _parse_concalls(panel, base_url: str) -> list:
    out: list = []
    for li in panel.select("li"):
        iso = _iso_month(li.get_text(" "))
        label = _clean(li.get_text(" "))
        m = _MONTH_YEAR_RE.search(label)
        period = m.group(0) if m else ""
        for a in li.find_all("a", href=True):
            url = _abs(base_url, a.get("href"))
            if not url:
                continue
            kind = _concall_kind(_clean(a.get_text(" ")), url)
            if not kind:
                continue
            nice = _CONCALL_TITLES[kind]
            out.append({"kind": kind, "date": iso,
                        "title": _clean(f"{period} Concall {nice}"),
                        "url": url, "source": _source(url)})
    return out


def _parse_annual_reports(panel, base_url: str) -> list:
    out: list = []
    for li in panel.select("li"):
        a = li.find("a", href=True)
        if a is None:
            continue
        url = _abs(base_url, a.get("href"))
        if not url:
            continue
        parts = _strings(a)
        title = parts[0] if parts else _clean(li.get_text(" "))
        note = " ".join(parts[1:])
        out.append({"kind": "annual_report", "date": _fy_iso(f"{title} {note}"),
                    "title": title, "url": url, "source": _source(url, note)})
    return out


def _parse_announcements(panel, base_url: str) -> list:
    out: list = []
    for li in panel.select("li"):
        a = li.find("a", href=True)
        if a is None:
            continue
        url = _abs(base_url, a.get("href"))
        if not url:
            continue
        parts = _strings(a)
        title = parts[0] if parts else _clean(li.get_text(" "))
        note = " ".join(parts[1:])          # headline excluded: dates live in the sub-line
        out.append({"kind": "announcement", "date": _iso_date(note),
                    "title": title, "url": url, "source": _source(url, note)})
    return out


def parse_documents_soup(soup, base_url: str) -> list[dict]:
    """Pure DOM -> contract `documents` list, de-duplicated and newest first."""
    if soup is None:
        return []
    panels = _panels(soup)
    rows: list = []
    for key, fn in (("concalls", _parse_concalls),
                    ("annual_reports", _parse_annual_reports),
                    ("announcements", _parse_announcements)):
        panel = panels.get(key)
        if panel is None:
            continue
        try:
            rows.extend(fn(panel, base_url))
        except Exception as e:  # noqa: BLE001
            log.warning("documents: %s panel unparsable: %s", key, e)
    out, seen = [], set()
    for d in rows:
        key = (d["kind"], d["url"])
        if not d["url"] or key in seen:
            continue
        seen.add(key)
        out.append(d)
    out.sort(key=lambda d: d.get("date") or "", reverse=True)   # stable: undated last
    return out


# ---------------------------------------------------------------------- fetch
def _client(session_id: Optional[str]):
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    if session_id:
        s.cookies.set("sessionid", session_id, domain=".screener.in")
    return s


def fetch_documents(code: str, session_id: Optional[str] = None,
                    timeout: int = 20) -> list[dict]:
    """Concall / annual-report / announcement documents for a Screener code."""
    if not code or requests is None or BeautifulSoup is None:
        if code:
            log.warning("documents: requests+bs4 required")
        return []
    url = f"{_SCREENER}/company/{code}/"
    try:
        s = _client(session_id)
        r = s.get(url, timeout=timeout)
        if r.status_code != 200:
            # consolidated URL sometimes 404s for standalone-only names (see company.py)
            url = f"{_SCREENER}/company/{code}/consolidated/"
            r = s.get(url, timeout=timeout)
        if r.status_code != 200:
            log.warning("documents: http %s for %s", r.status_code, code)
            return []
        docs = parse_documents_soup(BeautifulSoup(r.text, "lxml"),
                                    _clean(getattr(r, "url", "")) or url)
        log.info("documents(%s): %d rows", code, len(docs))
        return docs
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_documents failed (%s): %s", code, e)
        return []
