"""
Order book (backlog) board -> docs/data/orderbook.json.

For each company in scope, walks its NSE filings -- investor presentations,
results press releases and earnings-call transcripts -- reads the outstanding
order book each one states (earnings_intel.data.orderbook) and keeps one figure
per quarter, so the board shows today's backlog AND how it got there.

Every document read is remembered in docs/data/orderbook_docs.json (url ->
figure or null), committed with the board, so each night opens only filings
it has not seen: the first runs build history under a time budget, later runs
cost a few PDFs.

Scope: companies on the Orders board (they file order wins, so they report a
backlog), Capital Goods and Construction names over a market-cap floor, and
anyone already on this board.

    python scripts/refresh_orderbook.py --max-minutes 30
    python scripts/refresh_orderbook.py --codes MTARTECH TEJASNET
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.orderbook import document_figure, quarterly_series  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
DATA = ROOT / "docs" / "data"
OUT = DATA / "orderbook.json"
DOCS = DATA / "orderbook_docs.json"
BUNDLES = DATA / "fundamental"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"
SCOPE_SECTORS = ("Capital Goods", "Construction")


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def doc_kind(row: dict) -> str | None:
    """'presentation' / 'press' / 'transcript' for a filing worth reading, else None."""
    desc = str(row.get("desc") or "")
    url = str(row.get("attchmntFile") or "").lower()
    if not url.endswith(".pdf"):
        return None
    if desc == "Investor Presentation":
        return "presentation"
    if desc == "Press Release":
        return "press"
    if desc.startswith("Analysts/Institutional") and "transcript" in url:
        return "transcript"
    return None


def scope(mcap_floor: float) -> list:
    """NSE symbols to cover, most relevant first."""
    codes: list = []

    def add(c):
        c = str(c or "").strip().upper()
        if c and not c.isdigit() and c not in codes:
            codes.append(c)

    for r in _load(DATA / "orders.json", []):
        add(r.get("code"))
    for r in (_load(OUT, {}).get("companies") or []):
        add(r.get("code"))
    secs = (_load(DATA / "sector_stocks.json", {}).get("sectors") or {})
    big = [r for s in SCOPE_SECTORS for r in (secs.get(s) or [])
           if (r.get("mcap") or 0) >= mcap_floor]
    for r in sorted(big, key=lambda r: -(r.get("mcap") or 0)):
        add(r.get("code"))
    return codes


def bundle_facts(code: str) -> dict:
    """name, latest annual revenue (Cr) with its basis, market cap, from the bundle."""
    b = _load(BUNDLES / f"{code}.json", {})
    f = b.get("fundamental") or {}
    out = {"name": f.get("name") or code, "basis": f.get("basis")}
    pl = f.get("profit_loss") or {}
    heads, rows = pl.get("headers") or [], pl.get("rows") or {}
    sales = next((v for k, v in rows.items() if str(k).lower().startswith(("sales", "revenue"))), None)
    if sales and heads:
        idx = [i for i, h in enumerate(heads) if str(h).upper() != "TTM"]
        for i in reversed(idx):
            try:
                out["revenue_cr"] = round(float(str(sales[i]).replace(",", "")), 2)
                out["revenue_fy"] = str(heads[i])
                break
            except (ValueError, IndexError, TypeError):
                continue
    try:
        mc = str((f.get("overview") or {}).get("Market Cap") or "")
        out["mcap_cr"] = round(float("".join(ch for ch in mc if ch.isdigit() or ch == ".")), 2)
    except ValueError:
        pass
    return out


def summarise(code: str, series: list, facts: dict) -> dict | None:
    """One board row from a company's quarterly series. PURE."""
    if not series:
        return None
    last = series[-1]
    year_ago = next((s for s in reversed(series)
                     if s["as_of"] <= f"{int(last['as_of'][:4]) - 1}{last['as_of'][4:]}"), None)
    growth = (round((last["value_cr"] / year_ago["value_cr"] - 1) * 100, 2)
              if year_ago and year_ago["value_cr"] else None)
    rev = facts.get("revenue_cr")
    return {
        "code": code, "name": facts.get("name") or code,
        "order_book_cr": last["value_cr"], "as_of": last["as_of"],
        "last_updated": last["filed"], "growth_1y_pct": growth,
        "revenue_cr": rev, "revenue_fy": facts.get("revenue_fy"), "basis": facts.get("basis"),
        "book_to_revenue": round(last["value_cr"] / rev, 2) if rev else None,
        "mcap_cr": facts.get("mcap_cr"),
        "history": [{k: s.get(k) for k in ("as_of", "value_cr", "kind", "filed", "url", "quote")}
                    for s in series],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", nargs="*", default=None)
    ap.add_argument("--years", type=int, default=3, help="filing history to walk")
    ap.add_argument("--mcap-floor", type=float, default=500.0)
    ap.add_argument("--max-docs", type=int, default=16, help="new PDFs per company per run")
    ap.add_argument("--max-minutes", type=float, default=30.0)
    args = ap.parse_args(argv)

    from curl_cffi import requests as cr
    import pdfplumber

    t0 = time.time()
    codes = [c.upper() for c in args.codes] if args.codes else scope(args.mcap_floor)
    seen_docs: dict = _load(DOCS, {})
    s = cr.Session(impersonate="chrome")
    s.get(NSE_PAGE, timeout=30)
    to_d = date.today()
    from_d = to_d - timedelta(days=365 * args.years + 30)
    opened = failed = 0
    print(f"[orderbook] {len(codes)} companies in scope, {len(seen_docs)} documents already read")

    for i, code in enumerate(codes, 1):
        if (time.time() - t0) / 60 > args.max_minutes:
            print(f"[orderbook] time budget reached at {i - 1}/{len(codes)} - the rest next run")
            break
        try:
            r = s.get(NSE_API, timeout=60, headers={"Referer": NSE_PAGE},
                      params={"index": "equities", "symbol": code,
                              "from_date": from_d.strftime("%d-%m-%Y"),
                              "to_date": to_d.strftime("%d-%m-%Y")})
            rows = r.json() if r.status_code == 200 else []
        except Exception as e:  # noqa: BLE001
            print(f"  {code}: filings unavailable ({type(e).__name__})")
            continue
        new = 0
        for row in sorted(rows if isinstance(rows, list) else [],
                          key=lambda x: str(x.get("sort_date") or ""), reverse=True):
            kind = doc_kind(row)
            url = str(row.get("attchmntFile") or "")
            if not kind or url in seen_docs:
                continue
            if new >= args.max_docs:
                break
            new += 1
            try:
                filed = datetime.strptime(str(row.get("an_dt"))[:11], "%d-%b-%Y").date()
                pdf_bytes = s.get(url, timeout=60).content
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    text = "\n".join((p.extract_text() or "") for p in pdf.pages[:60])
                fig = document_figure(text, filed)
                seen_docs[url] = ({**fig, "kind": kind, "filed": filed.isoformat(), "code": code}
                                  if fig else {"code": code, "none": True})
                opened += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  {code}: could not read {url.rsplit('/', 1)[-1]} ({type(e).__name__})")
        if new:
            print(f"  [{i}/{len(codes)}] {code}: read {new} new filing(s)")
        _atomic(DOCS, json.dumps(seen_docs, separators=(",", ":")))

    # board: every company with at least one figure, from the whole cache
    by_code: dict = {}
    for url, f in seen_docs.items():
        if f and not f.get("none") and f.get("value_cr"):
            by_code.setdefault(f["code"], []).append({**f, "url": url})
    companies = []
    for code, figs in by_code.items():
        row = summarise(code, quarterly_series(figs), bundle_facts(code))
        if row:
            companies.append(row)
    companies.sort(key=lambda c: (c["last_updated"], c["order_book_cr"]), reverse=True)
    now = datetime.now(IST)
    _atomic(OUT, json.dumps({"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
                             "companies": companies, "documents_read": len(seen_docs)},
                            separators=(",", ":")))
    print(f"[orderbook] opened {opened} new filings ({failed} unreadable); "
          f"board: {len(companies)} companies with an order book")
    if not companies:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
