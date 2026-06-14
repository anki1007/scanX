"""
Magic Formula board (Joel Greenblatt) over the whole market in one pass.

Crawls Screener's screen  "ROCE > 0 AND EV/EBITDA > 0"  (the query terms force
both columns into the result table), ranks every company by
ROCE-rank + EV/EBITDA-rank, joins the sector head/tailwind signal from the
sector engine, and writes docs/data/magicformula.json (+ _meta).

    python scripts/refresh_magicformula.py                      # full market
    python scripts/refresh_magicformula.py --screen-pages 3     # quick test
    python scripts/refresh_magicformula.py --mcap-floor 100     # ₹100cr+ only

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

from earnings_intel import magicformula as mf            # noqa: E402
from earnings_intel.data import sectorlookup as sl       # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"


def _atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def _sector_join(rows: list[dict]):
    """Fill row['sector'] from the cached classified universe (refresh_sectors)."""
    try:
        uni = json.loads((ROOT / ".cache" / "universe.json").read_bytes()
                         .rstrip(b"\x00").rstrip())
        by_code = {u.get("code"): u.get("sector") for u in uni if u.get("code")}
    except Exception:  # noqa: BLE001
        by_code = {}
    for r in rows:
        if not r.get("sector"):
            sec = by_code.get(r.get("code"))
            if sec:
                r["sector"] = sec


def main():
    ap = argparse.ArgumentParser(description="Magic Formula board refresh")
    ap.add_argument("--query",
                    default="Return on capital employed > 0 AND EVEBITDA > 0")
    ap.add_argument("--screen-pages", type=int, default=250)
    ap.add_argument("--mcap-floor", type=float, default=0.0,
                    help="drop rows below this market cap (₹ cr); 0 keeps all")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"),
                    help="output directory (docs/data)")
    args = ap.parse_args()

    from earnings_intel.data.screener import ScreenerClient
    sid = _sid()
    print(f"[magic] screen: {args.query!r}  (session={'yes' if sid else 'NO'})")
    rows = ScreenerClient(session_id=sid, delay=0.4).fetch_screen(
        args.query, max_pages=args.screen_pages)
    print(f"[magic] screen rows: {len(rows)}")
    if not rows:
        print("[magic] nothing fetched — keeping previous board"); return 1
    have_ev = sum(1 for r in rows if r.get("ev_ebitda") is not None)
    print(f"[magic] rows with EV/EBITDA: {have_ev}")
    if not have_ev:
        print("[magic] EV/EBITDA column missing — check query/login; aborting"); return 1

    if args.mcap_floor > 0:
        rows = [r for r in rows if (r.get("mcap") or 0) >= args.mcap_floor]

    _sector_join(rows)
    docs = ROOT / "docs"

    def sector_of(code, name):
        return sl.sector_for(code, name, docs_dir=str(docs))

    ranked = mf.compute(rows, sector_of=sector_of)
    sectors = mf.sector_summary(ranked)

    out_rows = [{
        "code": r["code"], "name": r["name"], "cmp": r.get("cmp"),
        "mcap": r.get("mcap"), "pe": r.get("pe"),
        "roce": r.get("roce"), "ev": r.get("ev_ebitda"),
        "r_roce": r["r_roce"], "r_ev": r["r_ev"], "r_total": r["r_total"],
        "sector": r.get("sector"), "sec_sig": r.get("sec_sig"),
        "sec_score": r.get("sec_score"), "fin": r.get("fin", 0),
    } for r in ranked]

    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic(out_dir / "magicformula.json", json.dumps(
        {"rows": out_rows, "sectors": sectors}, separators=(",", ":")))
    _atomic(out_dir / "magicformula_meta.json", json.dumps({
        "generated_at_ist": now,
        "source": "screener.in screen",
        "query": args.query,
        "universe": len(rows),
        "ranked": len(out_rows),
        "financials_flagged": sum(1 for r in out_rows if r["fin"]),
        "mcap_floor": args.mcap_floor,
    }, indent=1))
    tail = sum(1 for s in sectors if s.get("signal") == "TAILWIND")
    head = sum(1 for s in sectors if s.get("signal") == "HEADWIND")
    print(f"[magic] wrote {len(out_rows)} ranked rows | sectors: "
          f"{tail} tailwind / {head} headwind | {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
