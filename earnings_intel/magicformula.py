"""
Joel Greenblatt "Magic Formula" ranking (quality x cheapness).

    a. Rank every company by ROCE        (higher  = better rank, 1 = best)
    b. Rank every company by EV/EBITDA   (lower   = better rank, 1 = best)
    c. Total rank = a + b; lowest total wins.

Pure functions — no I/O — so the engine is unit-testable. The refresh script
(scripts/refresh_magicformula.py) feeds Screener screen rows in and writes
docs/data/magicformula.json out. Sector head/tailwind labels come from the
sector engine (docs/data/sector_tailwind.json via sectorlookup).

NOT investment advice — a rules-based screen.
"""
from __future__ import annotations

import re
from typing import Optional

# EV/EBITDA is not meaningful for lenders/insurers; Greenblatt excludes
# financials. Rows are FLAGGED (fin=1), the UI excludes them by default
# with a toggle to include.
_FIN_RX = re.compile(r"bank|financ|insur|nbfc|broking|broker|capital market|"
                     r"asset management|amc\b|housing finance|microfinance",
                     re.IGNORECASE)


def is_financial(sector: Optional[str], name: Optional[str] = None) -> bool:
    if sector and _FIN_RX.search(sector):
        return True
    if name and _FIN_RX.search(name):
        return True
    return False


def _valid(r: dict) -> bool:
    roce, ev = r.get("roce"), r.get("ev_ebitda")
    try:
        return roce is not None and ev is not None and float(roce) > 0 and float(ev) > 0
    except (TypeError, ValueError):
        return False


def compute(rows: list[dict], sector_of=None) -> list[dict]:
    """Rank screen rows by the magic formula.

    rows      : Screener screen rows (code, name, cmp, pe, mcap, roce, ev_ebitda)
    sector_of : optional callable(code, name) -> {"name","label","score"} | None
                (sectorlookup.sector_for) for sector + head/tailwind enrichment.

    Returns rows sorted by total rank (best first) with:
        r_roce, r_ev, r_total, sector, sec_sig, sec_score, fin
    Ties get the same ordinal position resolved deterministically
    (secondary key: bigger mcap first, then code).
    """
    rs = [dict(r) for r in rows if _valid(r)]

    # de-dup by code (paginated crawls can repeat)
    seen: set = set()
    rs = [r for r in rs if not (r["code"] in seen or seen.add(r["code"]))]

    def mcap_key(r):
        m = r.get("mcap")
        return -(m if isinstance(m, (int, float)) else 0.0)

    by_roce = sorted(rs, key=lambda r: (-float(r["roce"]), mcap_key(r), r["code"]))
    for i, r in enumerate(by_roce, 1):
        r["r_roce"] = i
    by_ev = sorted(rs, key=lambda r: (float(r["ev_ebitda"]), mcap_key(r), r["code"]))
    for i, r in enumerate(by_ev, 1):
        r["r_ev"] = i
    for r in rs:
        r["r_total"] = r["r_roce"] + r["r_ev"]

    if sector_of is not None:
        for r in rs:
            sec = None
            try:
                sec = sector_of(r.get("code"), r.get("name"))
            except Exception:  # noqa: BLE001
                sec = None
            if sec:
                r["sector"] = sec.get("name") or r.get("sector")
                r["sec_sig"] = sec.get("label")
                r["sec_score"] = sec.get("score")

    for r in rs:
        r["fin"] = 1 if is_financial(r.get("sector"), r.get("name")) else 0

    rs.sort(key=lambda r: (r["r_total"], mcap_key(r), r["code"]))
    return rs


def sector_summary(ranked: list[dict]) -> list[dict]:
    """Per-sector chip data: signal + how many of the ranked names sit in it."""
    agg: dict = {}
    for r in ranked:
        sec = r.get("sector")
        if not sec:
            continue
        a = agg.setdefault(sec, {"sector": sec, "n": 0,
                                 "signal": r.get("sec_sig"), "score": r.get("sec_score")})
        a["n"] += 1
        if a.get("signal") is None and r.get("sec_sig"):
            a["signal"] = r.get("sec_sig"); a["score"] = r.get("sec_score")
    out = list(agg.values())
    out.sort(key=lambda s: (-(s["score"] if isinstance(s.get("score"), (int, float)) else -99),
                            s["sector"]))
    return out
