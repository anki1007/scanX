"""
Intrinsic-Value ranking (pure, testable).

iv_metrics(fund)  -> per-stock P/B, EV/EBITDA(approx), 3Y sales & profit growth,
                     ROCE, mcap  (derived from a company.fundamentals() dict).
rank_funnel(...)  -> the infographic method: rank by (3Y sales + 3Y ROCE) desc and
                     by P/B asc, add the two ranks (lower total = better).
rank_magic(...)   -> Greenblatt: rank by ROCE desc + EV/EBITDA asc, add ranks.
"""
from __future__ import annotations

from .analytics import to_float, floats


def _row(rows_dict, *prefixes):
    for p in prefixes:
        for k, v in (rows_dict or {}).items():
            if k.lower().startswith(p.lower()):
                return v
    return []


def _last(row):
    v = [x for x in floats(row) if x is not None]
    return v[-1] if v else None


def iv_metrics(fund: dict) -> dict:
    ov = fund.get("overview", {}) or {}
    price = to_float(ov.get("Current Price"))
    bv = to_float(ov.get("Book Value"))
    mcap = to_float(ov.get("Market Cap"))
    roce = to_float(ov.get("ROCE"))
    pb = round(price / bv, 2) if (price and bv) else None
    bs = (fund.get("balance_sheet") or {}).get("rows", {})
    pl = (fund.get("profit_loss") or {}).get("rows", {})
    borrow = _last(_row(bs, "Borrowings"))
    op = _last(_row(pl, "Operating Profit"))
    ev_ebitda = round((mcap + (borrow or 0)) / op, 2) if (mcap and op and op > 0) else None
    g = fund.get("growth", {}) or {}
    sg = g.get("Compounded Sales Growth", {}) or {}
    pg = g.get("Compounded Profit Growth", {}) or {}
    pc = g.get("Stock Price CAGR", {}) or {}
    return {"code": fund.get("code"), "name": fund.get("name"),
            "pb": pb, "ev_ebitda": ev_ebitda,
            "sales_3y": to_float(sg.get("3 Years")), "sales_10y": to_float(sg.get("10 Years")),
            "profit_3y": to_float(pg.get("3 Years")), "profit_10y": to_float(pg.get("10 Years")),
            "price_cagr_5y": to_float(pc.get("5 Years")), "price_cagr_10y": to_float(pc.get("10 Years")),
            "roce": roce, "mcap": mcap, "price": price}


def rank_funnel(stocks: list) -> list:
    """Funnel: rank by (3Y sales + 3Y ROCE) desc, by P/B asc, add ranks (lower=better)."""
    valid = [s for s in stocks if s.get("sales_3y") is not None
             and s.get("roce") is not None and s.get("pb") and s["pb"] > 0]
    for i, s in enumerate(sorted(valid, key=lambda x: x["sales_3y"] + x["roce"], reverse=True), 1):
        s["gq_rank"] = i
    for i, s in enumerate(sorted(valid, key=lambda x: x["pb"]), 1):
        s["pb_rank"] = i
    for s in valid:
        s["funnel_rank"] = s["gq_rank"] + s["pb_rank"]
    valid.sort(key=lambda s: s["funnel_rank"])
    return valid


def rank_magic(stocks: list) -> list:
    """Magic Formula: rank by ROCE desc + EV/EBITDA asc, add ranks (lower=better)."""
    valid = [s for s in stocks if s.get("roce") is not None
             and s.get("ev_ebitda") and s["ev_ebitda"] > 0]
    for i, s in enumerate(sorted(valid, key=lambda x: x["roce"], reverse=True), 1):
        s["roce_rank"] = i
    for i, s in enumerate(sorted(valid, key=lambda x: x["ev_ebitda"]), 1):
        s["ev_rank"] = i
    for s in valid:
        s["magic_rank"] = s["roce_rank"] + s["ev_rank"]
    valid.sort(key=lambda s: s["magic_rank"])
    return valid


def top_per_sector(ranked: list, rank_key: str, n: int = 3, mcap_floor: float = 200) -> list:
    """Top N per sector by the given rank, MCap > floor."""
    out, seen = [], {}
    for s in sorted(ranked, key=lambda x: x.get(rank_key, 1e9)):
        if (s.get("mcap") or 0) < mcap_floor:
            continue
        sec = s.get("sector", "?")
        if seen.get(sec, 0) >= n:
            continue
        seen[sec] = seen.get(sec, 0) + 1
        out.append(s)
    return out
