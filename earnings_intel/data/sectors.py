"""
Sector/industry classification + per-industry universe from Screener /market/ pages.

Uses Screener's level-2 industry groups (Capital Goods, Auto, Chemicals, Power,
Realty, Metals & Mining, etc. - 22 of them) rather than the 12 broad sectors, so
the headwind/tailwind view matches the familiar sector breakdown. Each
/market/<parent>/<industry>/ page lists that industry's companies with the same
metric columns as a screen.
"""
from __future__ import annotations

import logging

log = logging.getLogger("technofunda.sectors")

# code -> display name (parent sector is the first 4 chars of the code)
SECTORS = {
    "IN0101": "Chemicals", "IN0102": "Construction Materials", "IN0103": "Metals & Mining",
    "IN0104": "Forest Materials",
    "IN0201": "Automobile & Auto Components", "IN0202": "Consumer Durables",
    "IN0203": "Textiles", "IN0204": "Media & Entertainment", "IN0205": "Realty",
    "IN0206": "Consumer Services",
    "IN0301": "Oil, Gas & Consumable Fuels",
    "IN0401": "FMCG",
    "IN0501": "Financial Services",
    "IN0601": "Healthcare",
    "IN0701": "Construction", "IN0702": "Capital Goods",
    "IN0801": "Information Technology",
    "IN0901": "Services",
    "IN1001": "Telecommunication",
    "IN1101": "Power", "IN1102": "Utilities",
    "IN1201": "Diversified",
}


def _path(code: str) -> str:
    return f"{code[:4]}/{code}" if len(code) > 4 else code


def fetch_sectors(session_id=None, max_pages: int = 200, only=None, delay: float = 0.8) -> list:
    """Return one dict per company tagged with its `sector` (industry) + metrics."""
    from .screener import ScreenerClient
    c = ScreenerClient(session_id=session_id, delay=delay)
    out = []
    items = [(k, v) for k, v in SECTORS.items() if (not only or k in only or v in only)]
    for code, name in items:
        try:
            rows = c.fetch_market(_path(code), max_pages=max_pages)
        except Exception as e:  # noqa: BLE001
            log.warning("industry %s (%s) fetch failed: %s", code, name, e); rows = []
        for r in rows:
            r["sector"] = name; r["sector_code"] = code
        out.extend(rows)
        log.info("industry %s %s: %d companies", code, name, len(rows))
    return out
