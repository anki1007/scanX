"""
Magic Formula board (Joel Greenblatt) over the whole market in one pass.

Crawls Screener's screen  "ROCE > 0 AND EV/EBITDA > 0"  (the query terms force
both columns into the result table), ranks every company by
ROCE-rank + EV/EBITDA-rank, joins the sector head/tailwind signal from the
sector engine, and writes docs/data/magicformula.json (+ _meta).

When the screen cannot be read the board is rebuilt from the bundles under
docs/data/fundamental instead of left to freeze: ROCE is the same overview
figure, and EV/EBITDA comes from the ratio feed each bundle carries (a median
3.2% from the screen's own value), else from the statements. The step still
exits non-zero then, so the outage is seen.

    python scripts/refresh_magicformula.py                      # full market
    python scripts/refresh_magicformula.py --screen-pages 3     # quick test
    python scripts/refresh_magicformula.py --mcap-floor 100     # ₹100cr+ only
    python scripts/refresh_magicformula.py --source bundles     # no network

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
from earnings_intel.data import boarduniverse as bu      # noqa: E402
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


def _universe_codes() -> set:
    try:
        uni = json.loads((ROOT / ".cache" / "universe.json").read_bytes()
                         .rstrip(b"\x00").rstrip())
        return {str(u.get("code")) for u in uni if u.get("code")}
    except Exception:  # noqa: BLE001
        return set()


def bundle_rows(fdir: Path, prefer=frozenset()) -> list[dict]:
    """Magic Formula rows from the bundles on disk: ROCE > 0 and EV/EBITDA > 0.

    The same two conditions the screen query imposes. A company on disk under
    both its BSE number and its NSE symbol is ranked once, under the code the
    sector board uses when it has one.
    """
    rows = []
    if fdir.is_dir():
        for p in sorted(fdir.glob("*.json")):
            if p.stem == "index":
                continue
            try:
                row = bu.magic_row_from_bundle(
                    p.stem, json.loads(p.read_bytes().rstrip(b"\x00").rstrip()))
            except Exception:  # noqa: BLE001
                continue
            if row and (row["roce"] or 0) > 0 and (row["ev_ebitda"] or 0) > 0:
                rows.append(row)
    rows.sort(key=lambda r: (r["code"] not in prefer, r["code"]))
    return bu.dedupe_listings(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Magic Formula board refresh")
    ap.add_argument("--query",
                    default="Return on capital employed > 0 AND EVEBITDA > 0")
    ap.add_argument("--screen-pages", type=int, default=250)
    ap.add_argument("--mcap-floor", type=float, default=0.0,
                    help="drop rows below this market cap (₹ cr); 0 keeps all")
    ap.add_argument("--source", choices=("auto", "screen", "bundles"), default="auto",
                    help="auto: the screen, bundles if it cannot be read (default); "
                         "screen: screen only; bundles: no network")
    ap.add_argument("--min-rows", type=int, default=1500,
                    help="refuse to publish a bundle-built board with fewer rows")
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"),
                    help="output directory (docs/data)")
    args = ap.parse_args(argv)

    rows, why = [], None
    if args.source != "bundles":
        from earnings_intel.data.screener import ScreenerClient
        sid = _sid()
        print(f"[magic] screen: {args.query!r}  (session={'yes' if sid else 'NO'})")
        try:
            rows = ScreenerClient(session_id=sid, delay=0.4).fetch_screen(
                args.query, max_pages=args.screen_pages)
        except Exception as e:  # noqa: BLE001  -- a layout change raises here
            why = f"{type(e).__name__}: {e}"
        print(f"[magic] screen rows: {len(rows)}")
        have_ev = sum(1 for r in rows if r.get("ev_ebitda") is not None)
        if rows:
            print(f"[magic] rows with EV/EBITDA: {have_ev}")
        if not rows or not have_ev:
            why = why or ("the screen returned no rows" if not rows
                          else "the EV/EBITDA column is missing (query or login)")
            rows = []
    screen_ok = bool(rows)

    ev_src: dict = {}
    if not screen_ok:
        if args.source == "screen":
            print(f"[magic] {why} — keeping previous board"); return 1
        if why:
            print(f"[magic] {why} — rebuilding from bundles", file=sys.stderr)
        rows = bundle_rows(Path(args.fundamental), _universe_codes())
        for r in rows:
            ev_src[r["ev_src"]] = ev_src.get(r["ev_src"], 0) + 1
        print(f"[magic] bundle rows: {len(rows)} (EV/EBITDA from {ev_src})")
        if len(rows) < args.min_rows:
            print(f"[magic] ABORT - {len(rows)} bundle rows, under the {args.min_rows} "
                  f"floor — keeping previous board", file=sys.stderr)
            return 1

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
    # Vendor-free on purpose: this file is served to the page as-is.
    meta = {
        "generated_at_ist": now,
        "source": "stock screen" if screen_ok else "company filings",
        "screen_ok": screen_ok,
        "query": args.query if screen_ok else "ROCE > 0 AND EV/EBITDA > 0",
        "universe": len(rows),
        "ranked": len(out_rows),
        "financials_flagged": sum(1 for r in out_rows if r["fin"]),
        "mcap_floor": args.mcap_floor,
    }
    if ev_src:
        meta["ev_ebitda_from"] = ev_src
    _atomic(out_dir / "magicformula_meta.json", json.dumps(meta, indent=1))
    tail = sum(1 for s in sectors if s.get("signal") == "TAILWIND")
    head = sum(1 for s in sectors if s.get("signal") == "HEADWIND")
    print(f"[magic] wrote {len(out_rows)} ranked rows | sectors: "
          f"{tail} tailwind / {head} headwind | source {meta['source']} | {now}")
    if not screen_ok and args.source != "bundles":
        # Published, and still red: a green run on a fallback board is how the
        # last outage went unnoticed for three weeks.
        print(f"::warning title=Magic Formula screen unreadable::board rebuilt from "
              f"bundles ({len(out_rows)} ranked); {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
