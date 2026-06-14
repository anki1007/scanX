"""Refresh Buybacks tab: BSE filings via webscrap (buyback price) -> Screener fallback."""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from earnings_intel.data.buybacks import (BSEBuybacks, BuybackFiling, compute_buyback, GATE,  # noqa: E402
    parse_buyback_price, parse_buyback_type, parse_record_date)
from earnings_intel.data.orders import ScreenerFundamentals, CompanyFundamentals, parse_value_cr  # noqa: E402
from earnings_intel.data.special import fetch_fulltext, BUYBACK_Q  # noqa: E402

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


def build_rows(max_pages: int, max_companies: int = 120, max_pdf: int = 20):
    sid = _sid()
    # 1) AUTHORITATIVE active-buyback list (BOTH Tender and Open Market) from
    #    Screener's /actions/buyback/ table — the source of truth for what is live.
    acts = {}
    try:
        from earnings_intel.data.marketpulse import fetch_buyback_actions
        acts = fetch_buyback_actions(sid)
    except Exception:  # noqa: BLE001
        acts = {}
    # 2) supplementary early signals: BSE filings -> Screener full-text fallback
    bse = BSEBuybacks(cache_path=str(_CACHE / "bse_buybacks.json"))
    filings = bse.fetch(months=12, max_pages=max_pages)
    src2 = "BSE buy-back filings"
    if not filings:
        filings = [BuybackFiling(code=r["code"], name=r["name"], exchange="", date=r["date"],
                                 buyback_type=parse_buyback_type(r["snippet"]),
                                 buyback_price=parse_buyback_price(r["snippet"]),
                                 record_date=parse_record_date(r["snippet"]),
                                 size_cr=parse_value_cr(r["snippet"]), headline=r["snippet"], url=(r.get("pdf_url") or r["url"]))
                   for r in fetch_fulltext(sid, BUYBACK_Q, max_pages=max_pages, announcements_only=True)]
        src2 = "Screener full-text search"
    source = (f"Screener /actions/buyback (authoritative) + {src2}" if acts else src2)

    # 3) base rows = every authoritative buyback (clean, already typed)
    rows_map = {}
    for code, a in acts.items():
        ot = (a.get("offer_type") or "").lower()
        bt = "Tender" if "tender" in ot else ("Open Market" if ("open" in ot or "exchange" in ot) else "")
        code = str(code)
        rows_map[code] = {
            "code": code, "name": a.get("company") or code, "exchange": "",
            "date": a.get("ex_date") or "", "buyback_type": bt,
            "buyback_price": a.get("max_price"), "record_date": a.get("ex_date"),
            "size_cr": a.get("amount_cr"), "close_date": a.get("end_date"),
            "headline": (f"{a.get('offer_type') or 'Buyback'} - max Rs {a.get('max_price')}, "
                         f"Rs {a.get('amount_cr')} Cr"
                         + (f", closes {a.get('end_date')}" if a.get('end_date') else "")),
            "url": f"https://www.screener.in/company/{code}/",
        }
    # 4) add genuine extras not already covered (a real price or a detected type);
    #    skip bare 'buyback' mentions (postal-ballot / compliance noise)
    for f in filings:
        if f.code in rows_map:
            continue
        d = f.to_dict()
        if d.get("buyback_price") is None and not d.get("buyback_type"):
            continue
        rows_map[f.code] = d

    rows = list(rows_map.values())
    # 5) enrich fundamentals (cached)
    fund = ScreenerFundamentals(session_id=sid, cache_path=str(_CACHE / "orders_fundamentals.json"))
    allowed = set(list(rows_map.keys())[:max_companies])
    for r in rows:
        cf = fund.fetch(r["code"]) if r["code"] in allowed else CompanyFundamentals(code=r["code"])
        r.update({"market_cap": cf.market_cap, "sales": cf.sales_latest_q, "opm": cf.opm_latest, "ltp": cf.cmp,
                  "np_latest_q": cf.np_latest_q, "eps_latest_q": cf.eps_latest_q,
                  "np_growth_qoq": cf.np_growth_qoq, "eps_growth_qoq": cf.eps_growth_qoq})
    # 6) PDF deep-fetch for any extra still missing a price
    from earnings_intel.data.deepfetch import buyback_price_from_pdf
    filled = 0
    for r in rows:
        if filled >= max_pdf:
            break
        if r.get("buyback_price") is None and ".pdf" in (r.get("url") or "").lower():
            pr = buyback_price_from_pdf(r["url"])
            if pr is not None:
                r["buyback_price"] = pr
                filled += 1
    return rows, source


def _norm_name(s: str) -> str:
    """Loose key for matching 'Zydus Lifesciences Ltd' ~ 'Zydus Lifesci.'."""
    s = (s or "").lower()
    for w in (" limited", " ltd", " ltd.", ".", ",", "&", "  "):
        s = s.replace(w, " ")
    return " ".join(s.split())[:14]


def apply_offer_types(rows: list, acts: dict) -> int:
    """Stamp the authoritative Tender/Open-Market flag (and fill missing
    price/size/record-date) from Screener's /actions/buyback/ table.
    Match by code first, then by a loose company-name key."""
    by_name = {_norm_name(a.get("company", "")): a for a in acts.values()}
    matched = 0
    for r in rows:
        a = acts.get(str(r.get("code", "")).upper()) or by_name.get(_norm_name(r.get("name", "")))
        if not a:
            if not r.get("buyback_type"):          # not in the table: try the announcement text
                bt = parse_buyback_type(r.get("headline", ""))
                if bt:
                    r["buyback_type"] = bt; matched += 1
            continue
        ot = (a.get("offer_type") or "").lower()
        if "tender" in ot:
            r["buyback_type"] = "Tender"; matched += 1
        elif "open" in ot or "stock exchange" in ot:
            r["buyback_type"] = "Open Market"; matched += 1
        if r.get("buyback_price") in (None, 0) and a.get("max_price"):
            r["buyback_price"] = a["max_price"]
        if r.get("size_cr") in (None, 0) and a.get("amount_cr"):
            r["size_cr"] = a["amount_cr"]
        if not r.get("record_date") and a.get("ex_date"):
            r["record_date"] = a["ex_date"]      # tender: ex-date == record date
        if a.get("end_date"):
            r["close_date"] = a["end_date"]       # offer close (extra context)
    return matched


def apply_workflow(rows: list) -> None:
    for r in rows:
        r.update(compute_buyback(r.get("buyback_price"), r.get("ltp"), r.get("size_cr"), r.get("market_cap")))
        bt = r.get("buyback_type")
        k = r.get("exp_money_small")
        if bt == "Tender":
            r["decision"] = ("—" if k is None else ("Apply (>=8%)" if k >= GATE else "<8%"))
        elif bt == "Open Market":
            r["decision"] = "Open Market"
        else:
            r["decision"] = "—"          # unknown type: don't mislabel as Open Market
        r["candidate"] = bool(bt == "Tender" and k is not None and k >= GATE)


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh scanX Buybacks tab")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--max-companies", type=int, default=120)
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    rows, source = build_rows(args.max_pages, args.max_companies)
    # authoritative Tender/Open-Market flag from Screener's /actions/buyback/ table
    offer_note = "offer-type: skipped"
    try:
        from earnings_intel.data.marketpulse import fetch_buyback_actions
        acts = fetch_buyback_actions(_sid())
        n = apply_offer_types(rows, acts)
        offer_note = f"offer-type: {n}/{len(rows)} tagged from {len(acts)} actions"
    except Exception as e:  # noqa: BLE001
        offer_note = f"offer-type err: {type(e).__name__}"
    price_note = "no prices"
    try:
        import refresh_scanx as rs
        price_note = rs.enrich_prices(rows)
    except Exception as e:  # noqa: BLE001
        price_note = f"price err: {type(e).__name__}"
    apply_workflow(rows)
    rows.sort(key=lambda r: (r.get("candidate", False), r.get("exp_money_small") is not None,
                             r.get("exp_money_small") or -1), reverse=True)
    (out / "buybacks.json").write_text(json.dumps(rows, indent=2))
    now = datetime.now(IST)
    cands = sum(1 for r in rows if r.get("candidate"))
    meta = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"), "buybacks": len(rows),
            "candidates": cands, "source": source, "offer_types": offer_note, "prices": price_note}
    (out / "buybacks_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[buybacks] {len(rows)} buybacks / {cands} tender>=8% | {source} | {offer_note} | {price_note} | {now:%H:%M:%S IST}")


if __name__ == "__main__":
    main()
