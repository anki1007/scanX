#!/usr/bin/env python3
"""Bake docs/data/pullback.json - quality growth caught on a quiet pullback.

Pure arithmetic over bundles already on disk, so it costs no network and can
run in the same phase as the other deterministic boards.

The four price conditions (1-month up, 1-week down, volume cooling, volume
still elevated) only become testable once a bundle has been re-baked since the
price layer started storing ret_1w / ret_1m / vol_*. Until then they are
reported as untested rather than treated as passes, and `conditions_active`
says how many of the fifteen the run could actually apply.

    python scripts/refresh_pullback.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.pullback import CONDITIONS, evaluate  # noqa: E402


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build(fundamental_dir: Path) -> dict:
    rows = []
    scanned = 0
    untested_counts = {k: 0 for k, _ in CONDITIONS}

    for path in sorted(fundamental_dir.glob("*.json")):
        if path.stem == "index":
            continue
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(bundle, dict):
            continue
        scanned += 1
        verdict = evaluate(bundle)
        for key in verdict["untested"]:
            untested_counts[key] += 1
        if not verdict["pass"]:
            continue
        f = verdict["facts"]
        rows.append({
            "code": path.stem,
            "name": f["name"] or path.stem,
            "mcap": f["mcap"],
            "ltp": f["ltp"],
            "sales": f["sales"],
            "q_sales_yoy": _r(f["q_sales_yoy"]),
            "q_profit_yoy": _r(f["q_profit_yoy"]),
            "q_opm": _r(f["q_opm"]),
            "opm_5y": _r(f["opm_5y"]),
            "sales_growth": _r(f["sales_growth"]),
            "profit_growth": _r(f["profit_growth"]),
            "ret_1w": _r(f["ret_1w"]),
            "ret_1m": _r(f["ret_1m"]),
            "tested": verdict["tested"],
            "untested": verdict["untested"],
        })

    # Biggest profit acceleration first: it is the condition the screen is
    # really built around, and it orders the list the way it is read.
    rows.sort(key=lambda r: (r["q_profit_yoy"] is None, -(r["q_profit_yoy"] or 0)))
    for n, r in enumerate(rows, 1):
        r["rank"] = n

    # A condition counts as active when it could be applied to most of the
    # universe; one that is untestable everywhere is not filtering anything.
    active = [k for k, _ in CONDITIONS
              if scanned and untested_counts[k] < scanned * 0.5]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "scanned": scanned,
        "passed": len(rows),
        "conditions_total": len(CONDITIONS),
        "conditions_active": len(active),
        "inactive": [k for k, _ in CONDITIONS if k not in active],
        "untested_counts": untested_counts,
        "labels": {k: label for k, label in CONDITIONS},
        "rows": rows,
    }


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "pullback.json"))
    args = ap.parse_args()

    fdir = Path(args.fundamental)
    if not fdir.exists():
        print(f"[pullback] no bundles at {fdir}", file=sys.stderr)
        return 1

    payload = build(fdir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic(out, json.dumps(payload, separators=(",", ":")))

    print(f"[pullback] {payload['passed']} of {payload['scanned']} companies pass "
          f"({payload['conditions_active']}/{payload['conditions_total']} conditions "
          f"applicable) -> {out} ({out.stat().st_size/1024:.0f} KB)")
    if payload["inactive"]:
        print("[pullback] NOT yet applied (no data on most companies): "
              + ", ".join(payload["inactive"]))
        print("[pullback] these need a price re-bake since ret_1w/vol_* were added")
    for r in payload["rows"][:8]:
        print(f"   #{r['rank']:<3} {r['name'][:26]:<28}"
              f"NP {r['q_profit_yoy']:>7}%  Sales {r['q_sales_yoy']:>6}%  "
              f"{r['mcap']:>9,.0f} Cr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
