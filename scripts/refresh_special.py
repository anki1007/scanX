"""Refresh the scanX Special Situations tab (Screener full-text-search)."""
from __future__ import annotations
import argparse, json, os, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from earnings_intel.data.special import fetch_special  # noqa: E402
from earnings_intel.data.orders import ScreenerFundamentals, CompanyFundamentals  # noqa: E402

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


def build_rows(max_pages: int, max_companies: int = 150):
    sid = _sid()
    filings = fetch_special(sid, max_pages=max_pages)
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
                  "np_latest_q": cf.np_latest_q, "eps_latest_q": cf.eps_latest_q,
                  "np_growth_qoq": cf.np_growth_qoq, "eps_growth_qoq": cf.eps_growth_qoq})
        rows.append(d)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh scanX Special Situations")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--max-companies", type=int, default=150)
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    rows = build_rows(args.max_pages, args.max_companies)
    price_note = "no prices"
    try:
        import refresh_scanx as rs
        price_note = rs.enrich_prices(rows)
    except Exception as e:  # noqa: BLE001
        price_note = f"price err: {type(e).__name__}"
    rows.sort(key=lambda r: (r.get("date") or ""), reverse=True)
    (out / "special.json").write_text(json.dumps(rows, indent=2))
    now = datetime.now(IST)
    cats = Counter(r.get("category") for r in rows)
    meta = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"), "count": len(rows),
            "by_category": dict(cats), "source": "Screener full-text-search", "prices": price_note}
    (out / "special_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[special] {len(rows)} situations | {dict(cats)} | {price_note} | {now:%H:%M:%S IST}")


if __name__ == "__main__":
    main()
