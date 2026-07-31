"""
Sector benchmarks computed from OUR OWN constituents — pure, offline.

Every "vs sector" figure on the site used to come from the `sector` field in the
Upstox key-ratios response. That field is not a sector median. Four Chemicals
companies carried four different "sector" values on the same day:

    company      sector ROCE   sector ROE   sector ROA   sector EV/EBITDA
    BEPL              70.68        -5.21        58.22            -2.16
    KRISHANA          10.44        18.47         5.83            13.95
    NITTAGELA         70.67        -5.22        58.19            -2.05
    JUBLCPL           12.94        18.17        -0.65            59.77

A sector cannot have a ROCE of 70.68% for one member and 10.44% for another, and
a chemicals sector does not earn -5% on equity or trade at a NEGATIVE EV/EBITDA.
It appears to be a per-company peer set rather than a sector aggregate, and it
was driving the P/E component of the analysis score, the SWOT peer comparisons
and the `compares_to` edges in the evidence graph.

This computes the real thing: the MEDIAN across the sector's own constituents,
from ratios already baked into the bundles. One value per sector per metric,
reproducible, and auditable down to the companies that went into it.

Median, not mean, on purpose — one company on a 900x P/E would drag a mean
somewhere no member of the sector actually is.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["METRICS", "sector_medians", "MIN_MEMBERS"]

#: (key, must be positive to be meaningful)
METRICS: tuple[tuple[str, bool], ...] = (
    ("pe", True),          # negative = loss-making, not a cheap multiple
    ("pb", True),          # negative = negative book value
    ("ev_ebitda", True),   # negative = negative EBITDA
    ("roe", False),        # a negative return IS a real reading
    ("roce", False),
    ("roa", False),
)

#: Below this many contributors a "sector median" is a small-sample accident.
MIN_MEMBERS = 5

#: Readings outside this are data errors rather than outliers.
SANE = {"pe": (0.1, 500.0), "pb": (0.01, 100.0), "ev_ebitda": (0.1, 200.0),
        "roe": (-200.0, 200.0), "roce": (-200.0, 200.0), "roa": (-200.0, 200.0)}


def _median(xs: Sequence[float]) -> float | None:
    values = sorted(xs)
    n = len(values)
    if not n:
        return None
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0


def _usable(key: str, value: Any, positive_only: bool) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    lo, hi = SANE.get(key, (-1e9, 1e9))
    if not (lo <= v <= hi):
        return None
    if positive_only and v <= 0:
        return None
    return v


def sector_medians(bundles: Mapping[str, Mapping] | None,
                   membership: Mapping[str, str] | None,
                   *, min_members: int = MIN_MEMBERS) -> dict:
    """{sector: {metric: {median, n}}} from baked ratios. PURE, never raises.

    `bundles`    {code: bundle-or-upstox_ratios dict}
    `membership` {code: sector name}

    A metric with fewer than `min_members` contributors is omitted entirely
    rather than published thin — an absent benchmark is honest, a benchmark
    built on three companies is not.
    """
    buckets: dict[str, dict[str, list]] = {}
    for code, bundle in (bundles or {}).items():
        sector = (membership or {}).get(str(code))
        if not sector or not isinstance(bundle, Mapping):
            continue
        ratios = bundle.get("upstox_ratios") if "upstox_ratios" in bundle else bundle
        if not isinstance(ratios, Mapping):
            continue
        for key, positive_only in METRICS:
            row = ratios.get(key)
            value = row.get("value") if isinstance(row, Mapping) else row
            v = _usable(key, value, positive_only)
            if v is not None:
                buckets.setdefault(sector, {}).setdefault(key, []).append(v)

    out: dict[str, dict] = {}
    for sector, metrics in buckets.items():
        row = {}
        for key, values in metrics.items():
            if len(values) < min_members:
                continue
            med = _median(values)
            if med is not None:
                row[key] = {"median": round(med, 2), "n": len(values)}
        if row:
            out[sector] = row
    return out


def apply_medians(peers: Mapping | None, sector: str,
                  medians: Mapping | None) -> dict:
    """Rewrite a bundle's `peers` block to use OUR sector median. PURE.

    Keeps the company's own value untouched — only the benchmark it is judged
    against changes, plus an `n` so a reader can see how many companies stand
    behind the comparison.
    """
    out: dict = {}
    table = (medians or {}).get(sector) or {}
    for key, row in (peers or {}).items():
        if not isinstance(row, Mapping):
            continue
        new = dict(row)
        ours = table.get(key)
        if isinstance(ours, Mapping) and ours.get("median") is not None:
            new["sector"] = ours["median"]
            new["sector_n"] = ours["n"]
            new["sector_basis"] = "scanX median of sector constituents"
        else:
            # No trustworthy benchmark: drop it rather than keep the old one.
            new.pop("sector", None)
            new["sector_basis"] = "no sector median available"
        out[key] = new
    return out
