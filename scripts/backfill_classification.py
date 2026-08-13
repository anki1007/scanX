#!/usr/bin/env python3
"""Add sector / industry / group / sub-industry to bundles that predate it.

The classification comes from a breadcrumb on the company page, which the
nightly bake already downloads -- so from now on every refreshed bundle carries
it for free. This fills in the ones written before the parser existed.

Only the `classification` key is touched. Everything else in the bundle is left
exactly as it is: this is a one-field backfill, not a re-bake, and rewriting
statements would discard work and bury the diff.

    python scripts/backfill_classification.py --dry-run
    python scripts/backfill_classification.py --max-minutes 55
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import company  # noqa: E402


def needs_it(bundle: dict) -> bool:
    if not isinstance(bundle, dict):
        return False
    return not ((bundle.get("fundamental") or {}).get("classification") or {})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=0)
    args = ap.parse_args()

    root = Path(args.dir)
    targets = []
    for path in sorted(root.glob("*.json")):
        if path.stem == "index":
            continue
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if needs_it(bundle):
            targets.append(path)

    print(f"[class] {len(targets)} bundles have no classification")
    if args.dry_run:
        return 0

    done = none = failed = 0
    start = time.time()
    for i, path in enumerate(targets, 1):
        if args.max_minutes and (time.time() - start) / 60 > args.max_minutes:
            print(f"[class] budget reached at {i}/{len(targets)}")
            break
        code = path.stem
        try:
            company._FCACHE.clear()
            fresh = company.fundamentals(code, timeout=40)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if fresh.get("error"):
            failed += 1
            continue

        cls = fresh.get("classification") or {}
        if not cls:
            # No breadcrumb on the page. Recorded as genuinely absent rather
            # than retried forever on the next run.
            none += 1
            continue

        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
            bundle.setdefault("fundamental", {})["classification"] = cls
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
            done += 1
        except Exception:  # noqa: BLE001
            failed += 1
            continue

        if done <= 4 or done % 250 == 0:
            print(f"  [{i}/{len(targets)}] {code}: {cls.get('sector')} > "
                  f"{cls.get('industry')} > {cls.get('subgroup')}")

    print(f"[class] classified {done}, no breadcrumb {none}, failed {failed} "
          f"in {(time.time() - start) / 60:.1f}min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
