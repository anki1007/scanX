"""Industries overview -- roll the universe up by industry, not by sector.

scanX groups everything into 22 SECTORS. That is too coarse to be an analytical
unit: "Commodities" holds cement, steel, fertiliser and speciality chemicals,
which do not share a cycle, a margin structure or a customer. The company pages
carry a four-level classification, and this aggregates on it:

    Energy > Oil, Gas & Consumable Fuels > Petroleum Products > Refineries & Marketing
    sector      industry                    group              subgroup

Every figure here is a MEDIAN, not a mean. One company on a 900x P/E drags a
mean somewhere no member of the industry actually is -- the same reason the
sector-median engine uses medians, and the same mistake that put four
contradictory "sector P/E" values on the same day before it existed.

An industry with fewer members than `MIN_MEMBERS` is still published, but
flagged thin: a three-company median is an anecdote, and hiding it entirely
would silently drop coverage a reader has no way to notice.

Pure: no I/O, no clock, never raises.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

__all__ = ["LEVELS", "MIN_MEMBERS", "aggregate", "summarise"]

#: Coarsest first. Each is a valid grouping key.
LEVELS = ("sector", "industry", "group", "subgroup")

#: Below this, the medians are an anecdote rather than a benchmark.
MIN_MEMBERS = 5

#: Readings outside this are data errors, not outliers. Mirrors sectormedian.
SANE = {"pe": (0.1, 500.0), "roce": (-200.0, 200.0), "roe": (-200.0, 200.0),
        "opm": (-500.0, 100.0), "np_growth": (-1000.0, 1000.0),
        "sales_growth": (-1000.0, 1000.0)}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).replace(",", "").replace("₹", "").replace("%", "").strip()
    if not text or text in {"-", "--", "NA", "n/a"}:
        return None
    m = re.search(r"-?\d+\.?\d*", text)
    if not m:
        return None
    out = float(m.group())
    return out if math.isfinite(out) else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _sane(key: str, value: float | None) -> float | None:
    if value is None:
        return None
    lo, hi = SANE.get(key, (-1e9, 1e9))
    return value if lo <= value <= hi else None


def _yoy(series: Iterable[Any]) -> float | None:
    """Latest quarter against the SAME quarter a year earlier.

    Not against the previous quarter: most Indian businesses are seasonal, and
    a June-vs-March comparison measures the season rather than the business.
    Needs five quarters, because comparing q[-1] with q[-5] is what "year on
    year" means.
    """
    values = [_num(x) for x in (series or [])]
    values = [v for v in values if v is not None]
    if len(values) < 5:
        return None
    now, then = values[-1], values[-5]
    if abs(then) < 1e-9:
        return None
    return (now - then) / abs(then) * 100.0


def summarise(bundle: Mapping | None) -> dict:
    """One company's contribution to an industry roll-up. PURE, never raises."""
    out: dict = {"code": "", "name": "", "mcap": None, "pe": None, "roce": None,
                 "roe": None, "opm": None, "np_growth": None, "sales_growth": None,
                 "classification": {}}
    if not isinstance(bundle, Mapping):
        return out

    fundamental = bundle.get("fundamental")
    fundamental = fundamental if isinstance(fundamental, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return out

    overview = fundamental.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}
    cls = fundamental.get("classification")
    out["classification"] = cls if isinstance(cls, Mapping) else {}
    out["name"] = str(fundamental.get("name") or "")
    out["mcap"] = _num(overview.get("Market Cap"))

    # P/E from the feed first -- that is the site's source of record for the
    # headline multiple -- falling back to the scraped statement.
    upstox = bundle.get("upstox_ratios") if isinstance(bundle, Mapping) else None
    if isinstance(upstox, Mapping):
        row = upstox.get("pe")
        if isinstance(row, Mapping):
            out["pe"] = _sane("pe", _num(row.get("value")))
    if out["pe"] is None:
        out["pe"] = _sane("pe", _num(overview.get("Stock P/E")))

    out["roce"] = _sane("roce", _num(overview.get("ROCE")))
    out["roe"] = _sane("roe", _num(overview.get("ROE")))

    quarters = fundamental.get("quarters")
    rows = quarters.get("rows") if isinstance(quarters, Mapping) else None
    rows = rows if isinstance(rows, Mapping) else {}
    for key, label in (("np_growth", "Net Profit"), ("sales_growth", "Sales")):
        for name, series in rows.items():
            if str(name).strip().lower().startswith(label.lower()):
                out[key] = _sane(key, _yoy(series))
                break
    for name, series in rows.items():
        if str(name).strip().lower().startswith("opm"):
            values = [v for v in (_num(x) for x in (series or [])) if v is not None]
            if values:
                out["opm"] = _sane("opm", values[-1])
            break
    return out


def aggregate(companies: Iterable[Mapping], level: str = "industry",
              *, min_members: int = MIN_MEMBERS) -> list[dict]:
    """Roll company summaries up to one classification level. PURE.

    Returns rows sorted by total market cap, largest industry first.
    """
    if level not in LEVELS:
        level = "industry"

    buckets: dict[str, list[dict]] = {}
    for c in companies:
        if not isinstance(c, Mapping):
            continue
        name = str((c.get("classification") or {}).get(level) or "").strip()
        if not name:
            continue
        buckets.setdefault(name, []).append(dict(c))

    out = []
    for name, members in buckets.items():
        row: dict = {"name": name, "level": level, "members": len(members)}
        row["mcap"] = round(sum(m["mcap"] for m in members if m.get("mcap")), 1)
        for key in ("pe", "roce", "roe", "opm", "np_growth", "sales_growth"):
            values = [m[key] for m in members if m.get(key) is not None]
            med = _median(values)
            row[key] = round(med, 2) if med is not None else None
            row[key + "_n"] = len(values)
        # Thin, not hidden. A three-company median is an anecdote, and dropping
        # it would remove coverage a reader cannot see is missing.
        row["thin"] = len(members) < min_members
        biggest = max(members, key=lambda m: m.get("mcap") or 0, default=None)
        row["top"] = (biggest or {}).get("name") or ""
        row["top_code"] = (biggest or {}).get("code") or ""
        # The parent, so a reader can see which sector an industry sits in.
        parent_level = LEVELS[LEVELS.index(level) - 1] if LEVELS.index(level) else ""
        if parent_level:
            parents = {str((m.get("classification") or {}).get(parent_level) or "")
                       for m in members}
            parents.discard("")
            row["parent"] = sorted(parents)[0] if len(parents) == 1 else ""
        out.append(row)

    out.sort(key=lambda r: -(r["mcap"] or 0))
    return out
