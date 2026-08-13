#!/usr/bin/env python3
"""Bake docs/data/industries.json - the industries overview.

scanX groups everything into 22 SECTORS, which is too coarse to be an
analytical unit: "Commodities" holds cement, steel, fertiliser and speciality
chemicals, which share neither a cycle nor a margin structure. This rolls the
universe up on the four-level classification the company pages carry, giving
roughly 190 industries instead.

All four levels are baked so the page can switch between them without another
fetch; the whole file is small because it is medians, not companies.

Pure arithmetic over bundles already on disk. Must run after the bundles carry
a `classification` -- scripts/backfill_classification.py fills in older ones.

    python scripts/refresh_industries.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.industries import LEVELS, aggregate, summarise  # noqa: E402


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build(fundamental_dir: Path) -> dict:
    companies = []
    scanned = unclassified = 0
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
        row = summarise(bundle)
        row["code"] = path.stem
        if not row["classification"]:
            unclassified += 1
            continue
        companies.append(row)

    levels = {lvl: aggregate(companies, lvl) for lvl in LEVELS}
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "scanned": scanned,
        "classified": len(companies),
        # Said out loud rather than left as a silent shortfall: a reader who
        # sees 190 industries should know how many companies never reached one.
        "unclassified": unclassified,
        "levels": {k: len(v) for k, v in levels.items()},
        "data": levels,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "industries.json"))
    args = ap.parse_args()

    fdir = Path(args.fundamental)
    if not fdir.exists():
        print(f"[industries] no bundles at {fdir}", file=sys.stderr)
        return 1

    payload = build(fdir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic(out, json.dumps(payload, separators=(",", ":")))

    print(f"[industries] {payload['classified']} of {payload['scanned']} companies "
          f"classified ({payload['unclassified']} without a breadcrumb) -> {out} "
          f"({out.stat().st_size/1024:.0f} KB)")
    print(f"[industries] levels: {payload['levels']}")
    for row in payload["data"]["industry"][:6]:
        pe = f"{row['pe']:.1f}" if row["pe"] is not None else "-"
        print(f"   {row['name'][:34]:<36}{row['members']:>4} cos  "
              f"P/E {pe:>6}  mcap {row['mcap']:,.0f} Cr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
