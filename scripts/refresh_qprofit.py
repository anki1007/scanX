#!/usr/bin/env python3
"""Bake docs/data/qprofit.json - four consecutive quarters of rising net profit.

    NP(latest) > NP(latest-1) > NP(latest-2) > NP(latest-3)   and   NP(latest) > 0

Only companies that PASS are written, so the file stays small: this is a screen,
not a board, and a reader never sorts the failures.

Pure arithmetic over bundles already on disk -- no network, no key. Must run
after refresh_fundamentals, whose quarterly tables it reads.

    python scripts/refresh_qprofit.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.qprofit import evaluate  # noqa: E402


def _num(value):
    if value is None:
        return None
    m = re.search(r"-?\d+\.?\d*", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build(fundamental_dir: Path) -> dict:
    rows = []
    scanned = no_data = 0
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

        result = evaluate(bundle)
        if not result["pass"]:
            if "fewer than four" in result["reason"] or "no figure" in result["reason"]:
                no_data += 1
            continue

        fundamental = bundle.get("fundamental") or {}
        overview = fundamental.get("overview") or {}
        upstox = bundle.get("upstox_ratios") or {}
        pe_row = upstox.get("pe") if isinstance(upstox.get("pe"), dict) else None
        pe = pe_row.get("value") if pe_row else _num(overview.get("Stock P/E"))

        rows.append({
            "code": path.stem,
            "name": fundamental.get("name") or path.stem,
            "mcap": _num(overview.get("Market Cap")),
            "ltp": _num(overview.get("Current Price")),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "np": result["quarters"],              # oldest first, as the rule reads
            "periods": result.get("periods") or [],
            # Clamped for display. A base of Rs 0.01 Cr produces percentages in
            # the thousands that describe the divisor, not the business.
            "growth": (None if result["growth_pct"] is None
                       else max(-999.0, min(999.0, result["growth_pct"]))),
            "turnaround": result["turnaround"],
        })

    # BIGGEST COMPANY FIRST, not steepest growth. Sorting on growth put a
    # company that went from Rs 0.01 Cr to Rs 1.56 Cr at the top on +15,500%,
    # and the whole first page was rounding error on a near-zero base. Growth
    # is still a column, so a reader can sort by it deliberately -- but it is
    # not what the screen leads with.
    rows.sort(key=lambda r: -(r["mcap"] or 0))
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "count": len(rows), "scanned": scanned, "no_data": no_data,
            "rule": ("Net profit rose in each of the last four quarters "
                     "and the latest quarter is profitable"),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "qprofit.json"))
    args = ap.parse_args()

    fdir = Path(args.fundamental)
    if not fdir.exists():
        print(f"[qprofit] no bundles at {fdir}", file=sys.stderr)
        return 1

    payload = build(fdir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic(out, json.dumps(payload, separators=(",", ":")))

    turn = sum(1 for r in payload["rows"] if r["turnaround"])
    print(f"[qprofit] {payload['count']} of {payload['scanned']} companies pass "
          f"({turn} are turnarounds off a loss), {payload['no_data']} lack four "
          f"quarters -> {out} ({out.stat().st_size/1024:.0f} KB)")
    for r in payload["rows"][:6]:
        cap = f"{r['mcap']:,.0f} Cr" if r["mcap"] else "-"
        print(f"   {r['name'][:30]:<32}{cap:>14}  {r['np']}"
              + ("  TURNAROUND" if r["turnaround"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
