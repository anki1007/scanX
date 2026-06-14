"""
Intrinsic-Value ranking publisher (funnel + Magic Formula).

Universe: the classified market from refresh_sectors (.cache/universe.json).
Per sector, pre-ranks by ROCE+sales, takes the top N candidates, derives P/B,
EV/EBITDA, 3Y sales/profit growth (from baked fundamentals if available, else a
fresh fetch), then ranks the whole candidate set by:
  funnel = rank(3Y sales + 3Y ROCE) + rank(P/B)
  magic  = rank(ROCE) + rank(EV/EBITDA)
Writes docs/data/iv_ranking.json (funnel, magic, top-3-per-sector).

    python scripts/refresh_iv.py --per-sector 30
    python scripts/refresh_iv.py --baked-only        # no network, use baked bundles
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import ivrank as IV          # noqa: E402
from earnings_intel.data import company as co          # noqa: E402
from earnings_intel.data import analytics as AN        # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"
_BUNDLES = ROOT / "docs" / "data" / "fundamental"
_UNIVERSE = ROOT / ".cache" / "universe.json"


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


def _read_universe():
    try:
        raw = _UNIVERSE.read_bytes().rstrip(b"\x00").rstrip()
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return []


def _fundamentals(code, sid, baked_only):
    bf = _BUNDLES / f"{code}.json"
    if bf.exists():
        try:
            raw = bf.read_bytes().rstrip(b"\x00").rstrip()
            return json.loads(raw).get("fundamental")
        except Exception:  # noqa: BLE001
            pass
    if baked_only:
        return None
    f = co.fundamentals(code, sid)
    return None if "error" in f else f


def main():
    ap = argparse.ArgumentParser(description="Intrinsic-Value ranking")
    ap.add_argument("--per-sector", type=int, default=30, help="candidates per sector")
    ap.add_argument("--mcap-floor", type=float, default=200)
    ap.add_argument("--baked-only", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    uni = _read_universe()
    if not uni:
        print("[iv] no universe (.cache/universe.json) - run refresh_sectors first"); return
    # pre-rank per sector by cheap (3Y ROCE + qtr sales var), pick candidates
    by_sec = {}
    for r in uni:
        if (r.get("mcap") or 0) < args.mcap_floor:
            continue
        by_sec.setdefault(r.get("sector", "?"), []).append(r)
    cands = []
    for sec, rows in by_sec.items():
        rows.sort(key=lambda x: (x.get("roce") or 0) + (x.get("sales_var") or 0), reverse=True)
        cands.extend(rows[:args.per_sector])
    print(f"[iv] {len(cands)} candidates across {len(by_sec)} sectors")

    sid = _sid()
    recs, fair, n = [], [], 0
    for base in cands:
        code = base["code"]
        fund = _fundamentals(code, sid, args.baked_only)
        if not fund:
            continue
        m = IV.iv_metrics(fund)
        roce = base.get("roce")
        if roce is None:
            roce = m.get("roce")
        rec = {"code": code, "name": base.get("name"), "sector": base.get("sector"),
               "mcap": base.get("mcap"), "roce": roce,
               "sales_3y": m.get("sales_3y"), "profit_3y": m.get("profit_3y"),
               "sales_10y": m.get("sales_10y"), "profit_10y": m.get("profit_10y"),
               "price_cagr_10y": m.get("price_cagr_10y"), "price_cagr_5y": m.get("price_cagr_5y"),
               "pb": m.get("pb"), "ev_ebitda": m.get("ev_ebitda"),
               "pe": base.get("pe"), "cmp": base.get("cmp")}
        recs.append(rec)
        # fair value via auto two-stage DCF (intrinsic/share + margin of safety)
        d = AN.auto_dcf(fund.get("overview", {}), fund.get("growth", {}), fund.get("profit_loss", {}))
        if d.get("ok") and d.get("intrinsic_per_share") is not None:
            fair.append({"code": code, "name": base.get("name"), "sector": base.get("sector"),
                         "mcap": base.get("mcap"), "price": d.get("current_price"),
                         "intrinsic": d.get("intrinsic_per_share"), "mos": d.get("margin_of_safety"),
                         "implied_growth": (d.get("reverse") or {}).get("implied_growth"),
                         "growth_used": d.get("inputs", {}).get("growth")})
        n += 1
        if not args.baked_only:
            time.sleep(0.15)
    funnel = IV.rank_funnel([dict(r) for r in recs])
    magic = IV.rank_magic([dict(r) for r in recs])
    tops = IV.top_per_sector(funnel, "funnel_rank", n=3, mcap_floor=args.mcap_floor)
    fair.sort(key=lambda x: (x["mos"] if x.get("mos") is not None else -1e9), reverse=True)
    for i, s in enumerate(fair, 1):
        s["rank"] = i
    # return map: only rows with a price CAGR + at least one factor
    rmap = [{"code": r["code"], "name": r["name"], "sector": r["sector"],
             "cagr": r.get("price_cagr_10y") if r.get("price_cagr_10y") is not None else r.get("price_cagr_5y"),
             "sales_10y": r.get("sales_10y"), "profit_10y": r.get("profit_10y"),
             "roce": r.get("roce"), "pe": r.get("pe")} for r in recs
            if (r.get("price_cagr_10y") is not None or r.get("price_cagr_5y") is not None)]

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    data = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "candidates": len(recs), "funnel": funnel, "magic": magic, "top_per_sector": tops}
    _atomic(out / "iv_ranking.json", json.dumps(data, separators=(",", ":")))
    _atomic(out / "iv_fairvalue.json", json.dumps(
        {"generated_at_ist": data["generated_at_ist"], "rows": fair}, separators=(",", ":")))
    _atomic(out / "iv_returnmap.json", json.dumps(
        {"generated_at_ist": data["generated_at_ist"], "rows": rmap}, separators=(",", ":")))
    print(f"[iv] ranked {len(recs)} stocks | funnel {len(funnel)} magic {len(magic)} "
          f"top/sector {len(tops)} | {now:%H:%M:%S IST}")
    for s in funnel[:6]:
        print(f"   #{s['funnel_rank']:<4} {s['name'][:24]:<24} {s.get('sector','')[:14]:<14} "
              f"sales3 {s.get('sales_3y')} roce {s.get('roce')} pb {s.get('pb')}")


if __name__ == "__main__":
    main()
