"""Refresh Orders tab: BSE filings via webscrap (contract values) -> Screener fallback."""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from earnings_intel.data.orders import (BSEOrders, OrderFiling, ScreenerFundamentals,  # noqa: E402
    CompanyFundamentals, parse_value_cr, parse_order_type, parse_customer, parse_duration, order_size_pct)
from earnings_intel.data.special import fetch_fulltext, ORDERS_Q  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION_CACHE = ROOT / "screener_session.json"; _CACHE = ROOT / ".cache"


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION_CACHE.exists():
        try:
            sid = json.loads(_SESSION_CACHE.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def build_rows(max_pages: int, max_companies: int = 150, max_pdf: int = 20):
    sid = _sid()
    bse = BSEOrders(cache_path=str(_CACHE / "bse_orders.json"))
    filings = bse.fetch(months=3, max_pages=max_pages)        # BSE w/ values (via curl_cffi)
    source = "BSE filings (webscrap)"
    if not filings:                                           # fallback: Screener full-text-search
        filings = [OrderFiling(code=r["code"], name=r["name"], exchange="", date=r["date"],
                               order_type=parse_order_type(r["snippet"]), headline=r["snippet"],
                               value_cr=parse_value_cr(r["snippet"]), customer=parse_customer(r["snippet"]),
                               duration=parse_duration(r["snippet"]), url=(r.get("pdf_url") or r["url"]))
                   for r in fetch_fulltext(sid, ORDERS_Q, max_pages=max_pages, announcements_only=True)]
        source = "Screener full-text-search (fallback)"
    fund = ScreenerFundamentals(session_id=sid, cache_path=str(_CACHE / "orders_fundamentals.json"))
    allowed = set()
    for f in filings:
        if f.code not in allowed and len(allowed) < max_companies:
            allowed.add(f.code)
    rows = []
    for f in filings:
        cf = fund.fetch(f.code) if f.code in allowed else CompanyFundamentals(code=f.code)
        d = f.to_dict()
        d.update({"market_cap": cf.market_cap, "sales": cf.sales_latest_q, "opm": cf.opm_latest, "ltp": cf.cmp,
                  "revenue_fy": cf.revenue_fy, "np_prev_q": cf.np_prev_q, "np_latest_q": cf.np_latest_q,
                  "eps_prev_q": cf.eps_prev_q, "eps_latest_q": cf.eps_latest_q,
                  "np_growth_qoq": cf.np_growth_qoq, "eps_growth_qoq": cf.eps_growth_qoq,
                  "order_size_pct": order_size_pct(f.value_cr, cf.revenue_fy)})
        rows.append(d)
    # PDF deep-fetch: pull contract value from the filing PDF when the text lacked it
    from earnings_intel.data.deepfetch import value_cr_from_pdf
    filled = 0
    for r in rows:
        if filled >= max_pdf:
            break
        if r.get("value_cr") is None and ".pdf" in (r.get("url") or "").lower():
            v = value_cr_from_pdf(r["url"])
            if v is not None:
                r["value_cr"] = v
                r["order_size_pct"] = order_size_pct(v, r.get("revenue_fy"))
                filled += 1
    return rows, source


def consolidate(rows: list) -> list:
    by = defaultdict(lambda: {"orders": 0, "value": 0.0}); info = {}
    for r in rows:
        c = r["code"]; by[c]["orders"] += 1
        if r.get("value_cr"):
            by[c]["value"] += r["value_cr"]
        info[c] = {"name": r.get("name"), "revenue_fy": r.get("revenue_fy"), "market_cap": r.get("market_cap")}
    out = []
    for c, g in by.items():
        rev = info[c]["revenue_fy"]
        pct = round(g["value"] / rev * 100.0, 2) if (rev and g["value"]) else None
        out.append({"code": c, "name": info[c]["name"], "order_count": g["orders"],
                    "total_value_cr": round(g["value"], 2), "revenue_fy": rev,
                    "market_cap": info[c]["market_cap"], "orders_pct_revenue": pct})
    out.sort(key=lambda x: (x["orders_pct_revenue"] is not None, x["orders_pct_revenue"] or -1), reverse=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh scanX Orders tab")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--max-companies", type=int, default=150)
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    rows, source = build_rows(args.max_pages, args.max_companies)
    price_note = "no prices"
    try:
        import refresh_scanx as rs
        price_note = rs.enrich_prices(rows)
    except Exception as e:  # noqa: BLE001
        price_note = f"price err: {type(e).__name__}"
    rows.sort(key=lambda r: (r.get("order_size_pct") is not None, r.get("order_size_pct") or -1), reverse=True)
    companies = consolidate(rows)
    (out / "orders.json").write_text(json.dumps(rows, indent=2))
    (out / "orders_companies.json").write_text(json.dumps(companies, indent=2))
    now = datetime.now(IST)
    meta = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"), "orders": len(rows),
            "companies": len(companies), "source": source, "prices": price_note}
    (out / "orders_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[orders] {len(rows)} orders / {len(companies)} cos | {source} | {price_note} | {now:%H:%M:%S IST}")


if __name__ == "__main__":
    main()
