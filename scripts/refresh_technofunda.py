"""
Build the TechnoFunda board over the WHOLE market in one fast pass.

Pulls Screener's screen for Market Cap > floor (a superset of BSE-1000 /
Nifty Total Market / Nifty SME once the floor is low enough), and scores every
company with signal.board_signal — outperforming results + price momentum
(relative-strength proxy) + quality — entirely from the screen columns, so no
per-stock fetch is needed and the full universe is covered in minutes.

Output docs/data/technofunda.json holds ALL ranked rows; the board shows the
top 100 by default with a "Show all" toggle. Click a stock for the deep
RS-vs-Nifty500 + DCF verdict on the Fundamental tab.

    python scripts/refresh_technofunda.py                      # full market
    python scripts/refresh_technofunda.py --mcap-floor 500     # large/mid only
    python scripts/refresh_technofunda.py --screen-pages 3     # quick test

NOT investment advice — a rules-based screen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import company as co        # noqa: E402  (deep verdict, optional)
from earnings_intel.data import pricehist as ph       # noqa: E402
from earnings_intel.data import signal as sg          # noqa: E402
from earnings_intel.data import sectorlookup as sl

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def universe(sid, query, pages):
    # reuse the classified market cached by refresh_sectors (one crawl/day) if fresh
    import time
    cache = ROOT / ".cache" / "universe.json"
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < 18 * 3600:
            raw = cache.read_bytes().rstrip(b"\x00").rstrip()
            rows = json.loads(raw)
            if rows:
                print(f"[techno] using cached universe ({len(rows)} rows from refresh_sectors)")
                return rows
    except Exception:  # noqa: BLE001
        pass
    from earnings_intel.data.screener import ScreenerClient
    try:
        return ScreenerClient(session_id=sid, delay=0.3).fetch_screen(query, max_pages=pages)
    except Exception as e:  # noqa: BLE001
        print(f"[techno] universe screen failed: {e}")
        return []


def build_row(base):
    bs = sg.board_signal(base)
    row = {
        "code": base["code"], "name": base["name"],
        "label": bs["label"], "composite": bs["composite"],
        "results": bs["results"], "momentum": bs["momentum"], "quality": bs["quality"],
        "ltp": base.get("cmp"), "pe": base.get("pe"), "mcap": base.get("mcap"),
        "sales_yoy": base.get("sales_var"), "np_yoy": base.get("profit_var"),
        "fii_chg": base.get("fii_chg"), "pos_ath": bs["pos_ath"],
    }
    sec = sl.sector_for(base.get("code"), base.get("name"))
    if sec and sec.get("label"):
        row["sector"] = sec.get("name") or base.get("sector")
        row["sector_sig"] = sec["label"]
    elif base.get("sector"):
        row["sector"] = base.get("sector")
    return row


def score_one(base, sid):
    """Deep per-stock verdict (real RS vs Nifty 500 + DCF) — optional enrichment."""
    code = base["code"]
    fund = co.fundamentals(code, sid)
    if "error" in fund:
        return None
    price = ph.price_analytics(code, overview=fund.get("overview"))
    v = sg.technofunda_signal(fund, price)
    b = v["blocks"]
    tech = (price or {}).get("technical") or {}
    return {
        "code": code, "name": fund.get("name") or base.get("name"),
        "label": v["label"], "composite": v["composite"], "confidence": v["confidence"],
        "results": b["results"]["score"], "technical": b["technical"]["score"],
        "fundamental": b["fundamental"]["score"], "rs_rating": tech.get("rs_rating"),
        "ltp": tech.get("price") or base.get("cmp"),
        "mcap": base.get("mcap"), "pe": base.get("pe"),
        "sales_yoy": base.get("sales_var"), "np_yoy": base.get("profit_var"),
        "ticker": (price or {}).get("ticker"),
    }


def main():
    ap = argparse.ArgumentParser(description="Market-wide TechnoFunda board")
    ap.add_argument("--mcap-floor", type=float, default=5, help="min market cap ₹Cr (low = incl SME)")
    ap.add_argument("--price-floor", type=float, default=1, help="min CMP ₹")
    ap.add_argument("--screen-pages", type=int, default=250, help="screen pages (~50/page)")
    ap.add_argument("--top", type=int, default=0, help="cap stored rows (0 = all)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    sid = _sid()
    query = f"Market Capitalization > {args.mcap_floor:g}"
    uni = universe(sid, query, args.screen_pages)
    uni = [r for r in uni if (r.get("mcap") or 0) >= args.mcap_floor
           and (r.get("cmp") or 0) >= args.price_floor]
    print(f"[techno] scored {len(uni)} companies (mcap>{args.mcap_floor:g}, cmp>{args.price_floor:g})")

    rows = [build_row(r) for r in uni]
    rows.sort(key=lambda r: r["composite"], reverse=True)
    if args.top:
        rows = rows[:args.top]
    for n, r in enumerate(rows, 1):
        r["rank"] = n

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    _atomic(out / "technofunda.json", json.dumps(rows, separators=(",", ":")))
    now = datetime.now(IST)
    meta = {
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "universe": len(uni), "ranked": len(rows), "mcap_floor": args.mcap_floor,
        "buy": sum(1 for r in rows if r["label"] == "BUY"),
        "neutral": sum(1 for r in rows if r["label"] == "NEUTRAL"),
        "sell": sum(1 for r in rows if r["label"] == "SELL"),
    }
    _atomic(out / "technofunda_meta.json", json.dumps(meta, indent=2))
    print(f"[techno] ranked {len(rows)} | BUY {meta['buy']} NEU {meta['neutral']} "
          f"SELL {meta['sell']} | {now:%H:%M:%S IST}")
    for r in rows[:8]:
        print(f"   #{r['rank']:<3} {r['label']:7} {r['composite']:>3}  {r['name'][:30]}")


if __name__ == "__main__":
    main()
