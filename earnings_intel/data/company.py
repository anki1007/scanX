"""
Company search + rich fundamentals from Screener (the Fundamental Screener tab).

  search(q)            -> [{name, code, url}]   (Screener search API; name OR BSE code)
  fundamentals(code)   -> overview, growth, quarters, pros/cons, full statements
                          (P&L / balance sheet / cash flow / ratios), shareholding,
                          and AUTOMATIC analysis: trends, cyclical, growth-vs-price,
                          money flow, and an auto two-stage DCF / reverse-DCF.

Uses the cached Screener session. Read-only.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Optional

from .orders import _UA, _SCREENER
from . import analytics as A

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

log = logging.getLogger("technofunda.company")

_FCACHE = {}  # code -> (ts, result), 60s TTL to dedupe repeated lookups


def _client(session_id: Optional[str]):
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    if session_id:
        s.cookies.set("sessionid", session_id, domain=".screener.in")
    return s


def search(q: str, session_id: Optional[str] = None, timeout: int = 15) -> list:
    """Screener company search (autocomplete). Matches company name OR BSE code."""
    if not q or requests is None:
        return []
    try:
        r = _client(session_id).get(
            f"{_SCREENER}/api/company/search/?q=" + urllib.parse.quote(q), timeout=timeout)
        if r.status_code != 200:
            return []
        out = []
        for item in r.json():
            m = re.search(r"/company/([^/]+)/", item.get("url", ""))
            if m:
                out.append({"name": item.get("name", ""), "code": m.group(1),
                            "url": item.get("url", "")})
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("company search failed: %s", e)
        return []


# ------------------------------------------------------------------ parsers
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip().rstrip("+").strip()


def _overview(soup) -> dict:
    o = {}
    for li in soup.select("#top-ratios li"):
        nm = li.select_one(".name"); vl = li.select_one(".value")
        if nm:
            o[_clean(nm.get_text(" "))] = _clean(vl.get_text(" ")) if vl else ""
    return o


def _growth(soup) -> dict:
    out = {}
    for t in soup.select("table.ranges-table"):
        th = t.select_one("th")
        if not th:
            continue
        title = th.get_text(strip=True)
        vals = {}
        for tr in t.select("tr"):
            tds = tr.select("td")
            if len(tds) >= 2:
                vals[tds[0].get_text(strip=True).rstrip(":")] = tds[1].get_text(strip=True)
        if vals:
            out[title] = vals
    return out


def _statement(soup, sec_id: str, n: int = 12) -> dict:
    """Generic Screener statement table (#profit-loss/#balance-sheet/#cash-flow/#ratios)."""
    tbl = soup.select_one(f"#{sec_id} table")
    if not tbl:
        return {"headers": [], "rows": {}}
    head = [_clean(c.get_text(" ")) for c in tbl.select("thead th")]
    head = [h for h in head[1:]]  # drop leading blank label column
    rows = {}
    for tr in tbl.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        label = _clean(cells[0].get_text(" "))
        if not label:
            continue
        rows[label] = [c.get_text(strip=True) for c in cells[1:]][-n:]
    return {"headers": head[-n:], "rows": rows}


def _quarters(soup, n: int = 12) -> dict:
    st = _statement(soup, "quarters", n)
    keep = {}
    for lab in ("Sales", "OPM", "Net Profit", "EPS"):
        for k in st["rows"]:
            if k.lower().startswith(lab.lower()):
                keep[lab] = st["rows"][k]
                break
    return {"headers": st["headers"], "rows": keep}


def _shareholding(soup, n: int = 12) -> dict:
    """First (quarterly) shareholding table: Promoters/FIIs/DIIs/Government/Public."""
    tbl = soup.select_one("#shareholding table")
    if not tbl:
        return {"headers": [], "rows": {}}
    head = [_clean(c.get_text(" ")) for c in tbl.select("thead th")][1:]
    rows = {}
    for tr in tbl.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        rows[_clean(cells[0].get_text(" "))] = [c.get_text(strip=True) for c in cells[1:]][-n:]
    return {"headers": head[-n:], "rows": rows}


def _row(stmt: dict, *prefixes) -> list:
    """Find a statement row whose label starts with any prefix (case-insensitive)."""
    for p in prefixes:
        for k, v in (stmt.get("rows") or {}).items():
            if k.lower().startswith(p.lower()):
                return v
    return []


def _insights(soup) -> dict:
    """Screener AI 'Insights' (operational KPIs) - yearly + quarterly grids.

    Premium-gated on Screener: only rows whose values contain digits are kept, so
    a non-subscribed / masked session yields {} (and the UI hides the panel),
    while a subscribed session returns the real operational metrics.
    """
    def _grid(sec_id):
        sec = soup.select_one(f"#{sec_id}")
        t = sec.find("table") if sec else None
        if not t:
            return None
        periods = [th.get_text(strip=True) for th in t.select("thead th")]
        if periods and not periods[0]:
            periods = periods[1:]
        rows = []
        for tr in t.select("tbody tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) < 2:
                continue
            parts = [x.strip() for x in cells[0].get_text("\n").split("\n") if x.strip()]
            if not parts:
                continue
            metric = parts[0]
            sub = parts[1] if len(parts) > 1 else ""
            unit = sub.split("\u00b7")[0].strip() if sub else ""
            vals = [c.get_text(strip=True) for c in cells[1:]]
            if not any(re.search(r"\d", v) for v in vals):     # masked 'xx.xx' / empty -> skip
                continue
            n = min(len(vals), len(periods))
            valmap = {periods[len(periods) - n + i]: vals[len(vals) - n + i] for i in range(n)}
            rows.append({"metric": metric, "unit": unit, "values": valmap})
        return {"periods": periods, "rows": rows} if rows else None

    grids = {k: v for k, v in (("yearly", _grid("yearly-insights")),
                               ("quarterly", _grid("quarterly-insights"))) if v}
    return grids


def _analyze(overview, growth, quarters, pl, bs, cf, ratios, sh) -> dict:
    inst = []
    fii = _row(sh, "FII"); dii = _row(sh, "DII")
    if fii and dii:
        ff = A.floats(fii); dd = A.floats(dii)
        inst = [None if (a is None or b is None) else round(a + b, 2)
                for a, b in zip(ff, dd)]
    yearly = {
        "Sales": _row(pl, "Sales"), "Net Profit": _row(pl, "Net Profit"),
        "OPM%": _row(pl, "OPM"), "EPS": _row(pl, "EPS"),
        "Reserves": _row(bs, "Reserves"),
        "Net Cash Flow": _row(cf, "Net Cash Flow"),
        "Operating Cash Flow": _row(cf, "Cash from Operating"),
        "ROCE": _row(ratios, "ROCE"),
    }
    quarterly = {
        "Sales": quarters["rows"].get("Sales", []),
        "Net Profit": quarters["rows"].get("Net Profit", []),
        "OPM%": quarters["rows"].get("OPM", []),
        "EPS": quarters["rows"].get("EPS", []),
        "Promoter Holding": _row(sh, "Promoter"),
        "Institutional Holding": inst,
    }
    yearly = {k: v for k, v in yearly.items() if v}
    quarterly = {k: v for k, v in quarterly.items() if v}
    pcagr = (growth.get("Compounded Profit Growth", {}) or {})
    scagr = (growth.get("Stock Price CAGR", {}) or {})
    return {
        "trends": A.classify_trends(yearly, quarterly),
        "cyclical": A.cyclical(quarters["headers"], quarters["rows"].get("Net Profit", [])),
        "growth_insight": A.growth_vs_price(
            pcagr.get("5 Years"), scagr.get("5 Years"),
            pcagr.get("TTM"), scagr.get("1 Year")),
        "money_flow": A.money_flow(sh.get("headers", []), fii, dii),
        "dcf": A.auto_dcf(overview, growth, pl),
    }


def fundamentals(code: str, session_id: Optional[str] = None, timeout: int = 20) -> dict:
    if requests is None or BeautifulSoup is None:
        return {"error": "requests+bs4 required"}
    import time
    ck = str(code).upper()
    hit = _FCACHE.get(ck)
    if hit and (time.time() - hit[0] < 60) and "error" not in hit[1]:
        return hit[1]
    try:
        r = _client(session_id).get(f"{_SCREENER}/company/{code}/", timeout=timeout)
        if r.status_code != 200:
            # consolidated URL sometimes 404s for standalone-only names
            r = _client(session_id).get(f"{_SCREENER}/company/{code}/consolidated/", timeout=timeout)
        if r.status_code != 200:
            return {"error": f"http {r.status_code}"}
        soup = BeautifulSoup(r.text, "lxml")
        h1 = soup.select_one("h1")
        overview = _overview(soup)
        growth = _growth(soup)
        quarters = _quarters(soup)
        pl = _statement(soup, "profit-loss")
        bs = _statement(soup, "balance-sheet")
        cf = _statement(soup, "cash-flow")
        ratios = _statement(soup, "ratios")
        sh = _shareholding(soup)
        result = {
            "code": code,
            "name": h1.get_text(strip=True) if h1 else code,
            "url": f"{_SCREENER}/company/{code}/",
            "overview": overview,
            "growth": growth,
            "quarters": quarters,
            "profit_loss": pl,
            "balance_sheet": bs,
            "cash_flow": cf,
            "ratios": ratios,
            "shareholding": sh,
            "insights": _insights(soup),
            "pros": [li.get_text(" ", strip=True) for li in soup.select(".pros li")],
            "cons": [li.get_text(" ", strip=True) for li in soup.select(".cons li")],
            "analysis": _analyze(overview, growth, quarters, pl, bs, cf, ratios, sh),
        }
        _FCACHE[ck] = (time.time(), result)
        return result
    except Exception as e:  # noqa: BLE001
        log.warning("fundamentals failed (%s): %s", code, e)
        return {"error": str(e)}
