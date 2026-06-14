"""Lean, purely-incremental bundle baker for the static site.
Bakes a PRIORITISED code list (Special -> Fair Value -> sector-by-mcap),
skips any code that already has a bundle file (never re-bakes), and stops at
a wall-clock budget so it fits the sandbox's per-call limit. Resumable:
just run it again. Merges (never clobbers) docs/data/fundamental/index.json.
"""
from __future__ import annotations
import json, sys, time, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import company as co, pricehist as ph, signal as sg, sectorlookup as sl

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 38.0
D = ROOT / "docs" / "data"
OUT = D / "fundamental"; OUT.mkdir(parents=True, exist_ok=True)

def _sid():
    f = ROOT / "screener_session.json"
    try: return json.loads(f.read_text()).get("sessionid")
    except Exception: return None

def _load(name):
    try: return json.loads((D / name).read_text(encoding="utf-8", errors="replace"))
    except Exception: return None

# ---- priority order ----
codes, seen = [], set()
def add(seq):
    for c in seq:
        c = str(c or "").strip()
        if c and c not in seen:
            seen.add(c); codes.append(c)

sp = _load("special.json")
add(r.get("code") for r in (sp or []) if isinstance(r, dict))            # 1) Special (flagged, small)
fv = _load("iv_fairvalue.json") or {}
add(r.get("code") for r in sorted((fv.get("rows") or []),
        key=lambda r: r.get("rank") or 1e9))                            # 2) Fair Value by rank
ss = _load("sector_stocks.json") or {}
sec_rows = [r for lst in (ss.get("sectors") or {}).values() for r in (lst or []) if isinstance(r, dict)]
add(r.get("code") for r in sorted(sec_rows,
        key=lambda r: -(r.get("mcap") or 0)))                           # 3) sector by mcap desc

todo = [c for c in codes if not (OUT / f"{c}.json").exists()]
print(f"queue: {len(codes)} prioritised | already baked: {len(codes)-len(todo)} | todo: {len(todo)}")

sid = _sid(); t0 = time.time(); done = fail = 0
for code in todo:
    if time.time() - t0 > BUDGET:
        break
    try:
        fund = co.fundamentals(code, sid)
        if "error" in fund:
            fail += 1; continue
        price = ph.price_analytics(code, overview=fund.get("overview"))
        sec = sl.sector_for(code, fund.get("name"))
        sigv = sg.technofunda_signal(fund, price, sec)
        tmp = OUT / f"{code}.json.tmp"
        tmp.write_text(json.dumps({"fundamental": fund, "prices": price, "signal": sigv},
                                  separators=(",", ":")))
        os.replace(tmp, OUT / f"{code}.json")
        done += 1
    except Exception as e:
        fail += 1
        print("  FAIL", code, type(e).__name__)

# merge index.json (never clobber)
have = sorted(p.stem for p in OUT.glob("*.json") if p.stem != "index")
(OUT / "index.json").write_text(json.dumps(have))
print(f"baked {done} this run | fail {fail} | total bundles now {len(have)}")
