"""The TechnoFunda board's universe: the scraped screen UNION what we hold.

The board used to take its universe from a screen scrape alone. Anything that
scrape missed -- a truncated page, a rate-limited request, a company the
vendor's own filter drops -- was invisible on the board even when a complete
bundle for it was sitting on disk. That gap was not theoretical: 178 companies
with a market cap over the floor, a live price and twelve quarters of results
were absent from a 5,183-row board, Gujarat Gas (26,710 Cr) among them.

So the screen is now one SOURCE of the universe rather than the definition of
it. A bundle joins only when it can actually be scored, and the screen wins any
code held by both -- its price was fetched live, a bundle's may be a day old.

PURE: no network, no clock. Everything here is arithmetic over dicts.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

# _num and _yoy carry the two corrections this repo has already paid for: a
# number parser that survives "Rs 26,710 Cr." and a growth rate measured
# year-on-year rather than against the previous quarter. Reused, not re-written.
from .industries import _num, _yoy

__all__ = ["row_from_bundle", "rows_from_bundles", "merge", "technical_of",
           "fii_change", "enrichment_of", "enrich"]


def technical_of(bundle: Any) -> dict:
    """The price block a screen cannot supply: relative strength and range.

    `rs_rating` is a 1-99 rating against the Nifty 500 computed from real price
    history, and it is the only honest momentum input the board has. Without it
    every row scored a flat 50.
    """
    out: dict = {}
    if not isinstance(bundle, Mapping):
        return out
    prices = bundle.get("prices")
    if not isinstance(prices, Mapping) or not prices.get("ok"):
        return out
    tech = prices.get("technical")
    if not isinstance(tech, Mapping):
        return out
    for src, dst in (("rs_rating", "rs_rating"), ("pos_52w", "pos_52w")):
        value = _num(tech.get(src))
        if value is not None:
            out[dst] = value
    return out


def fii_change(bundle: Any) -> dict:
    """Change in FII holding, latest disclosure against the one before it.

    In PERCENTAGE POINTS: a move from 3.70% to 3.98% is +0.28, not +7.6%. The
    score only reads the sign, but a ratio would be wildly unstable off a small
    base and is not what "FII inflow" means on a shareholding table.

    The third input found scoring nothing at all: `fii_chg` was null on every
    row of the board, so the FII term in the quality block had never once
    fired, while the code read as though it were doing something.
    """
    out: dict = {}
    if not isinstance(bundle, Mapping):
        return out
    fundamental = bundle.get("fundamental")
    fundamental = fundamental if isinstance(fundamental, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return out
    holding = fundamental.get("shareholding")
    rows = holding.get("rows") if isinstance(holding, Mapping) else None
    if not isinstance(rows, Mapping):
        return out
    for name, series in rows.items():
        if not str(name).strip().lower().startswith("fii"):
            continue
        values = [v for v in (_num(x) for x in (series or [])) if v is not None]
        if len(values) >= 2:
            out["fii_chg"] = round(values[-1] - values[-2], 2)
        break
    return out


def enrichment_of(bundle: Any) -> dict:
    """Everything a screen cannot supply, for one company."""
    out = technical_of(bundle)
    out.update(fii_change(bundle))
    return out


# Inputs board_signal actually scores on. A value that is absent or identical
# for the ENTIRE market is not a score -- it is a broken feed wearing one.
SCORED_INPUTS = ("profit_var", "sales_var", "roce", "pe", "rs_rating", "fii_chg")


def dead_inputs(rows: Iterable[Mapping] | None,
                fields: Iterable[str] = SCORED_INPUTS,
                min_rows: int = 50) -> dict:
    """Scoring inputs that are missing everywhere, or the same value everywhere.

    Three separate inputs reached production scoring nothing: momentum was the
    constant 50 on every row, the sector momentum term contributed 0 on every
    row, and `fii_chg` was null on all of them. Not one failed a bake, raised,
    or looked wrong on the page -- each simply stopped contributing, and the
    numbers stayed plausible. Nobody finds that by reading the board.

    So the bake asserts it instead. Returns {field: reason} for anything dead;
    empty means every input is doing work.

    Skipped under `min_rows`: a handful of rows can legitimately share a value,
    and a partial run must not look like a broken feed.
    """
    materialised = [r for r in (rows or []) if isinstance(r, Mapping)]
    if len(materialised) < int(min_rows):
        return {}
    dead: dict = {}
    for field in fields:
        values = [r.get(field) for r in materialised]
        present = [v for v in values if v is not None]
        if not present:
            dead[field] = f"null on all {len(values)} rows"
        elif len(set(present)) == 1 and len(present) > int(min_rows):
            dead[field] = f"the single value {present[0]!r} on all {len(present)} rows"
    return dead


def enrich(rows: Iterable[Mapping] | None,
           technicals: Mapping[str, Mapping] | None) -> list[dict]:
    """Fold the per-code price block into universe rows.

    Applied to EVERY row, not just the ones sourced from a bundle: if only the
    held-only companies carried a strength rating, the board would score 83
    rows on real momentum and the rest on a constant, which is the same defect
    wearing different clothes.
    """
    table = technicals if isinstance(technicals, Mapping) else {}
    out = []
    for row in (rows or []):
        if not isinstance(row, Mapping):
            continue
        merged = dict(row)
        extra = table.get(str(merged.get("code") or ""))
        if isinstance(extra, Mapping):
            merged.update(extra)
        out.append(merged)
    return out


def _pe(bundle: Mapping, overview: Mapping) -> float | None:
    """Feed first, scraped statement second -- the board's headline multiple.

    Deliberately NOT passed through the industry sanity band: a roll-up has to
    drop a 900x multiple so it cannot drag a median, but a company that really
    does trade at 900x should say so on its own row.
    """
    feed = bundle.get("upstox_ratios")
    if isinstance(feed, Mapping):
        row = feed.get("pe")
        if isinstance(row, Mapping):
            value = _num(row.get("value"))
            if value is not None:
                return value
    return _num(overview.get("Stock P/E"))


def _growth(rows: Mapping, label: str) -> float | None:
    for name, series in rows.items():
        if str(name).strip().lower().startswith(label.lower()):
            return _yoy(series)
    return None


def row_from_bundle(code: str, bundle: Any) -> dict | None:
    """A bundle rendered in the shape board_signal expects, or None.

    None means "cannot be scored", not "score it as zero" -- a company with no
    market cap or no price would otherwise land on the board as a row of
    blanks, which reads as data rather than as an absence.
    """
    if not isinstance(bundle, Mapping):
        return None
    fundamental = bundle.get("fundamental")
    fundamental = fundamental if isinstance(fundamental, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return None

    overview = fundamental.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}

    mcap = _num(overview.get("Market Cap"))
    cmp_ = _num(overview.get("Current Price"))
    if mcap is None or cmp_ is None:
        return None

    quarters = fundamental.get("quarters")
    rows = quarters.get("rows") if isinstance(quarters, Mapping) else None
    rows = rows if isinstance(rows, Mapping) else {}

    return {
        "code": code,
        "name": str(fundamental.get("name") or code),
        "mcap": mcap,
        "cmp": cmp_,
        "pe": _pe(bundle, overview),
        "roce": _num(overview.get("ROCE")),
        "sales_var": _growth(rows, "Sales"),
        "profit_var": _growth(rows, "Net Profit"),
    }


def rows_from_bundles(bundles: Iterable[tuple[str, Any]]) -> list[dict]:
    out = []
    for code, bundle in bundles:
        row = row_from_bundle(code, bundle)
        if row is not None:
            out.append(row)
    return out


def merge(screen: Iterable[Mapping] | None,
          held: Iterable[Mapping] | None) -> list[dict]:
    """Screen rows first, then any held company the screen did not return.

    Order matters twice over: the screen wins a contested code because its
    price is live, and the result keeps the screen's ordering so a bake that
    adds bundles does not reshuffle rows that were already correct.
    """
    merged: dict[str, dict] = {}
    for row in (screen or []):
        if isinstance(row, Mapping) and row.get("code"):
            merged[str(row["code"])] = dict(row)
    for row in (held or []):
        if isinstance(row, Mapping) and row.get("code"):
            merged.setdefault(str(row["code"]), dict(row))
    return list(merged.values())
