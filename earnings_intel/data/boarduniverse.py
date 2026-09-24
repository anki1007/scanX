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

import re
from collections.abc import Mapping
from typing import Any, Iterable

# _num and _yoy carry the two corrections this repo has already paid for: a
# number parser that survives "Rs 26,710 Cr." and a growth rate measured
# year-on-year rather than against the previous quarter. Reused, not re-written.
from .industries import _num, _yoy

__all__ = ["row_from_bundle", "rows_from_bundles", "merge", "technical_of",
           "fii_change", "enrichment_of", "enrich", "dedupe_listings",
           "sector_rows_from_bundles", "is_bank_format", "ebitda_of",
           "ev_ebitda_of", "magic_row_from_bundle", "quarter_index"]


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
            out[dst] = _round(value)
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


def _round(value, places: int = 2):
    """Two decimals, or None. Never turns a missing value into 0.0."""
    return None if value is None else round(float(value), places)


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

    # Rounded at the source. A growth rate is a ratio of two reported figures,
    # so it arrives with the full float tail -- 930.5555555555555 -- and the
    # board printed every digit of it. Two decimals is past the precision the
    # inputs justify, and it takes ~15 bytes a value off a 5,400-row file.
    return {
        "code": code,
        "name": str(fundamental.get("name") or code),
        "mcap": _round(mcap),
        "cmp": _round(cmp_),
        "pe": _round(_pe(bundle, overview)),
        "roce": _round(_num(overview.get("ROCE"))),
        "sales_var": _round(_growth(rows, "Sales")),
        "profit_var": _round(_growth(rows, "Net Profit")),
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


# ------------------------------------------------ one company, two listings
# The same company can sit on disk under its BSE number and its NSE symbol
# (500500 / HINDMOTORS, 524632 / SHUKRAPHAR ...). A board built from bundles
# then counts it twice: ranked twice on IV, and its market cap weighted twice
# into the sector score. Merge by code alone cannot see that.

_NAME_NOISE = re.compile(r"\b(ltd|limited|the|co|company|corp|corporation)\b|[^a-z0-9]+")


def _company_key(name: Any) -> str:
    return _NAME_NOISE.sub("", str(name or "").lower())


def dedupe_listings(rows: Iterable[Mapping] | None,
                    name_of: Any = None) -> list[dict]:
    """Drop the second code of a company that is listed twice.

    Conservative on purpose: two rows are the same company only when their
    names normalise to the same key AND one code is a BSE number while the
    other is not -- the exact signature of a dual listing. Two different
    companies that happen to share a name both keep their rows. The FIRST row
    wins, so callers order by preference (screen rows, then membership).

    `name_of(row)` supplies the name to compare when a row's own is not
    comparable: the screen abbreviates ("Permanent Magnet", "Shukra Pharma.")
    where the bundle for the other listing spells it out, so a screen row
    must be keyed by the full name in its own bundle or ~40 dual listings
    slip through.
    """
    out: list[dict] = []
    seen: dict[str, str] = {}
    for row in (rows or []):
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("code") or "")
        key = _company_key((name_of(row) if name_of else None) or row.get("name"))
        prior = seen.get(key) if key else None
        if prior is not None and prior.isdigit() != code.isdigit():
            continue
        if key and key not in seen:
            seen[key] = code
        out.append(dict(row))
    return out


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def quarter_index(header: Any) -> int | None:
    """'Jun 2026' -> 2026*12+6, a month count that orders and subtracts."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", str(header or ""))
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    return int(m.group(2)) * 12 + _MONTHS[m.group(1).lower()]


def _last_quarter(bundle: Any) -> int | None:
    heads = _table(bundle, "quarters").get("headers") or []
    return quarter_index(heads[-1]) if heads else None


def sector_rows_from_bundles(pairs: Iterable[tuple[str, Any]],
                             membership: Mapping[str, str] | None = None,
                             min_quarter: int | None = None
                             ) -> tuple[list[dict], int]:
    """Sector-board rows from bundles, when the live sector pages cannot say.

    The sector comes from the last good sector_stocks.json snapshot first, so
    membership stays continuous with the pages, then from the bundle's own
    industry classification, which is how a new listing gets on. A row with no
    sector is left out rather than grouped under "Unknown": that group would
    be a 23rd sector the board does not have.

    `min_quarter` (a quarter_index) blanks the growth of a bundle whose
    quarter table ends before it. ~150 bundles hold a consolidated table that
    stopped years ago -- one large cap's ends in March 2015 -- and a growth
    rate from then would sit in today's sector median as if it were news. The
    row stays: its market cap and 52-week position are current.

    Returns (rows, how many scorable companies could not be placed).
    """
    from .sectors import CODE_OF, industry_name
    member = membership if isinstance(membership, Mapping) else {}
    rows: list[dict] = []
    unplaced = 0
    for code, bundle in (pairs or []):
        row = row_from_bundle(code, bundle)
        if row is None:
            continue
        fundamental = bundle.get("fundamental") if isinstance(bundle, Mapping) else None
        cls = fundamental.get("classification") if isinstance(fundamental, Mapping) else None
        sec = member.get(str(code).upper()) or industry_name(cls)
        if sec not in CODE_OF:
            unplaced += 1
            continue
        if min_quarter is not None:
            last = _last_quarter(bundle)
            if last is None or last < min_quarter:
                row["sales_var"] = row["profit_var"] = None
        row.update(enrichment_of(bundle))
        row["sector"] = sec
        row["sector_code"] = CODE_OF[sec]
        row["src"] = "bundle"
        rows.append(row)
    # Membership codes first, so a dual listing keeps the code the pages used.
    rows.sort(key=lambda r: 0 if str(r["code"]).upper() in member else 1)
    return rows, unplaced


# ------------------------------------------------------------- magic formula
# Greenblatt ranks on ROCE and EV/EBITDA. When the screen that supplies both is
# unavailable, the bundles can: ROCE is the same overview figure, and EV/EBITDA
# comes from the ratio feed each bundle carries, measured at a median 3.2% from
# the screen's own value across 3,792 non-financials. Derived from statements
# instead it is 3.7-6.9% off (no bundle has a cash line, so EV is gross of
# cash), and for banks it is useless: 75% off, always far too cheap.

_FRESH_PL_YEAR = 2025


def _last_value(series: Any) -> float | None:
    if not isinstance(series, list) or not series:
        return None
    return _num(series[-1])


def _table(bundle: Any, name: str) -> Mapping:
    f = bundle.get("fundamental") if isinstance(bundle, Mapping) else None
    t = f.get(name) if isinstance(f, Mapping) else None
    return t if isinstance(t, Mapping) else {}


def is_bank_format(bundle: Any) -> bool:
    """A lender's P&L: "Financing Profit" instead of an operating profit."""
    rows = _table(bundle, "profit_loss").get("rows")
    return isinstance(rows, Mapping) and "Financing Profit" in rows


def _pl_is_fresh(bundle: Any) -> bool:
    """The annual table must reach a recent year, and so must the quarters.

    A TTM column alone is not enough: 140 bundles carry one over a last quarter
    from 2019-2025.
    """
    heads = _table(bundle, "profit_loss").get("headers") or []
    qheads = _table(bundle, "quarters").get("headers") or []
    if not heads or not qheads:
        return False
    years = [int(y) for y in re.findall(r"(20\d\d)", " ".join(map(str, heads[-2:])))]
    qyear = re.findall(r"(20\d\d)", str(qheads[-1]))
    return (bool(years) and max(years) >= _FRESH_PL_YEAR
            and bool(qyear) and int(qyear[0]) >= _FRESH_PL_YEAR)


def ebitda_of(bundle: Any) -> float | None:
    """PBT + interest + depreciation from the latest annual column."""
    if not _pl_is_fresh(bundle):
        return None
    rows = _table(bundle, "profit_loss").get("rows") or {}
    pbt = _last_value(rows.get("Profit before tax"))
    if pbt is None:
        return None
    return pbt + (_last_value(rows.get("Interest")) or 0.0) + (_last_value(rows.get("Depreciation")) or 0.0)


def ev_ebitda_of(bundle: Any, mcap: float | None) -> tuple[float | None, str | None]:
    """(EV/EBITDA, where it came from): the feed first, statements second.

    Never derived for a lender: the feed has no value for the big banks, and a
    statement-derived figure makes every bank look several times cheaper than
    it is, which moves every other company's EV rank as well.
    """
    feed = bundle.get("upstox_ratios") if isinstance(bundle, Mapping) else None
    row = feed.get("ev_ebitda") if isinstance(feed, Mapping) else None
    value = _num(row.get("value")) if isinstance(row, Mapping) else None
    if value is not None and value > 0:
        return _round(value), "feed"
    if is_bank_format(bundle) or not mcap:
        return None, None
    ebitda = ebitda_of(bundle)
    if ebitda is None or ebitda <= 0:
        return None, None
    brow = _table(bundle, "balance_sheet").get("rows") or {}
    debt = _last_value(brow.get("Borrowings") or brow.get("Borrowing")) or 0.0
    return _round((mcap + debt) / ebitda), "derived"


def magic_row_from_bundle(code: str, bundle: Any) -> dict | None:
    """A Magic Formula row in the screen's shape, or None if it cannot rank."""
    base = row_from_bundle(code, bundle)
    if base is None:
        return None
    ev, src = ev_ebitda_of(bundle, base["mcap"])
    return {
        "code": base["code"], "name": base["name"], "cmp": base["cmp"],
        "mcap": base["mcap"], "pe": base["pe"], "roce": base["roce"],
        "ev_ebitda": ev, "ev_src": src, "bank_fmt": is_bank_format(bundle),
    }
