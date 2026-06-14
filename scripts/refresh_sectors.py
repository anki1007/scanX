"""
Sector Headwind/Tailwind publisher.

Classifies the whole market via Screener /market/ sector pages, scores each
sector + the full market, and writes:
  docs/data/sector_tailwind.json   - current scores + per-sector detail
  docs/data/sector_history.json    - daily score history (for the trend line)
  .cache/universe.json             - classified universe (reused by IV ranking)

    python scripts/refresh_sectors.py
    python scripts/refresh_sectors.py --only IN03 IN12   # quick test
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

from earnings_intel.data import sectors as S          # noqa: E402
from earnings_intel.data import sectorscore as SC     # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(text); os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Sector headwind/tailwind")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--only", nargs="*", default=None, help="sector codes e.g. IN03 IN12")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    rows = S.fetch_sectors(_sid(), max_pages=args.max_pages, only=args.only)
    if not rows:
        print("[sectors] no data fetched"); return
    res = SC.market_tailwind(rows)
    now = datetime.now(IST)
    res["generated_at_ist"] = now.strftime("%Y-%m-%d %H:%M:%S IST")

    # blend real per-sector FII fortnight net flow (from refresh_fii -> fii.json)
    try:
        _fii = json.loads((Path(args.out) / "fii.json").read_text()).get("sectors") or []
        SC.blend_fii(res, _fii)
        _nb = sum(1 for s in res["sectors"] if "fii_fortnight" in s)
        if _nb:
            print(f"[sectors] blended real FII flow into {_nb} sectors")
    except Exception:  # noqa: BLE001  (fii.json optional)
        pass

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    _atomic(out / "sector_tailwind.json", json.dumps(res, separators=(",", ":")))

    # daily history for the trend line (replace today's, cap 400 days)
    hp = out / "sector_history.json"
    try:
        hist = json.loads(hp.read_text())
    except Exception:  # noqa: BLE001
        hist = []
    today = now.strftime("%Y-%m-%d")
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "full": res["full_market"]["score"],
                 "sectors": {s["sector"]: s["score"] for s in res["sectors"]}})
    hist = hist[-400:]
    _atomic(hp, json.dumps(hist, separators=(",", ":")))

    # per-sector constituents with a per-stock signal (drill-down)
    bysec = {}
    for r in rows:
        sig = SC.stock_signal(r)
        bysec.setdefault(r.get("sector", "Unknown"), []).append({
            "code": r["code"], "name": r.get("name"), "signal": sig["signal"],
            "sscore": sig["sscore"], "result": sig["result"],
            "np": r.get("profit_var"), "sales": r.get("sales_var"), "pos": sig["pos"],
            "ltp": r.get("cmp"), "mcap": r.get("mcap"), "pe": r.get("pe")})
    for k in bysec:
        bysec[k].sort(key=lambda x: x["sscore"], reverse=True)
    _atomic(out / "sector_stocks.json", json.dumps(
        {"generated_at_ist": res["generated_at_ist"], "sectors": bysec}, separators=(",", ":")))

    # cache classified universe for the IV ranking tools (gitignored)
    cache = ROOT / ".cache"; cache.mkdir(parents=True, exist_ok=True)
    _atomic(cache / "universe.json", json.dumps(rows, separators=(",", ":")))

    fm = res["full_market"]
    print(f"[sectors] {fm['companies']} companies, {fm['sectors']} sectors | "
          f"FULL MARKET {fm['score']:+} {fm['signal']} | {now:%H:%M:%S IST}")
    for s in res["sectors"]:
        print(f"   {s['score']:+6}  {s['signal']:<8} {s['sector'][:24]:<24} "
              f"(n={s['n']}, breadth {s['breadth_pct']}%, NPg {s['median_profit_var']})")


if __name__ == "__main__":
    main()
