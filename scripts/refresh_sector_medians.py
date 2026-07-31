"""
Compute real sector benchmarks from our own constituents -> docs/data/sector_medians.json
and rewrite every bundle's `peers.*.sector` to use them.

The Upstox key-ratios `sector` field is not a sector aggregate: four Chemicals
companies carried four different values on the same day, including a sector ROCE
of 70.68%, a sector ROE of -5.21% and a NEGATIVE sector EV/EBITDA. That field was
driving every "vs sector" line on the site, the P/E component of the analysis
score, the SWOT peer comparisons and the compares_to edges in the evidence graph.

    python scripts/refresh_sector_medians.py            # compute + rewrite bundles
    python scripts/refresh_sector_medians.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.sectormedian import (  # noqa: E402
    MIN_MEMBERS, apply_medians, sector_medians,
)

DATA = ROOT / "docs" / "data"
BUNDLES = DATA / "fundamental"
OUT = DATA / "sector_medians.json"


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def membership() -> dict:
    try:
        sec = json.loads((DATA / "sector_stocks.json").read_text(encoding="utf-8"))["sectors"]
    except Exception:  # noqa: BLE001
        return {}
    return {s["code"]: name for name, lst in sec.items()
            for s in lst if isinstance(s, dict) and s.get("code")}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute sector medians from our own bundles")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-members", type=int, default=MIN_MEMBERS)
    args = ap.parse_args()

    member = membership()
    if not member:
        print("[sectors] no sector_stocks.json - nothing to do"); return 0

    loaded = {}
    for path in BUNDLES.glob("*.json"):
        if path.stem == "index":
            continue
        try:
            loaded[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

    medians = sector_medians(loaded, member, min_members=args.min_members)
    print(f"[sectors] {len(loaded):,} bundles -> medians for {len(medians)} sectors")
    for name in sorted(medians)[:6]:
        pe = (medians[name].get("pe") or {})
        print(f"   {name[:30]:32} P/E {pe.get('median')} from {pe.get('n')} companies")

    if args.dry_run:
        return 0

    _atomic(OUT, json.dumps({"generated_at": time.strftime("%Y-%m-%d"),
                             "min_members": args.min_members,
                             "basis": "median of the sector's own constituents",
                             "sectors": medians}, separators=(",", ":")))

    rewritten = 0
    for code, bundle in loaded.items():
        sector = member.get(code)
        health = ((bundle.get("fundamental") or {}).get("analysis") or {}).get("health")
        if not sector or not isinstance(health, dict) or not health.get("peers"):
            continue
        new_peers = apply_medians(health["peers"], sector, medians)
        if new_peers != health["peers"]:
            health["peers"] = new_peers
            _atomic(BUNDLES / f"{code}.json", json.dumps(bundle, separators=(",", ":")))
            rewritten += 1
    print(f"[sectors] rewrote peer benchmarks in {rewritten:,} bundles -> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
