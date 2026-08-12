#!/usr/bin/env python3
"""Re-fetch bundles whose overview carries units but no numbers.

613 companies were published with every field blank -- "Rs Cr.", "%", "" --
and empty charts, because the consolidated URL answers 200 with a page of
labels for a company that files no consolidated statement, and the fetcher
only fell back on 404. company.fundamentals now tests for NUMBERS instead of
for a status code; this repairs what was already written.

Only the `fundamental` block is replaced. upstox_ratios, swot, score and the
ratio timestamps are left exactly as they are -- they were never the problem,
and rewriting them would throw away work and churn the diff.

    python scripts/repair_blank_bundles.py --dry-run
    python scripts/repair_blank_bundles.py
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

from earnings_intel.data import company  # noqa: E402

DIGIT = re.compile(r"\d")


def is_blank(bundle: dict) -> bool:
    """Is this bundle missing data a complete page would have carried? PURE.

    Two shapes, both from a partially-filled consolidated page:

      * no number anywhere in the overview -- "Rs Cr." and "%" only. A
        truthiness check passes that, which is how 613 shipped.
      * ratios present but NO quarterly table, which is how another 121
        shipped: blank charts, no computed P/E, no freshness fingerprint.
    """
    if not isinstance(bundle, dict):
        return False
    fundamental = bundle.get("fundamental") or {}
    overview = fundamental.get("overview") or {}
    if not overview:
        return True
    if not any(DIGIT.search(str(v)) for v in overview.values()):
        return True
    quarters = fundamental.get("quarters") or {}
    return not quarters.get("headers")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
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
        if isinstance(bundle, dict) and is_blank(bundle):
            targets.append((path, bundle))

    print(f"[repair] {len(targets)} bundles carry no numbers")
    if args.dry_run:
        for path, _ in targets[:15]:
            print(f"   {path.stem}")
        return 0
    if args.limit:
        targets = targets[:args.limit]

    fixed = still = failed = 0
    start = time.time()
    for i, (path, bundle) in enumerate(targets, 1):
        if args.max_minutes and (time.time() - start) / 60 > args.max_minutes:
            print(f"[repair] budget reached at {i}/{len(targets)}")
            break
        code = path.stem
        try:
            company._FCACHE.clear()
            fresh = company.fundamentals(code, timeout=40)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [{i}/{len(targets)}] {code}: {type(exc).__name__}")
            continue
        if fresh.get("error"):
            failed += 1
            print(f"  [{i}/{len(targets)}] {code}: {fresh['error']}")
            continue

        overview = fresh.get("overview") or {}
        improved = (any(DIGIT.search(str(v)) for v in overview.values())
                    and (fresh.get("quarters") or {}).get("headers"))
        if not improved:
            # Genuinely has nothing on either statement -- a suspended or
            # newly listed shell. Leave it rather than write blanks again.
            still += 1
            continue

        bundle["fundamental"] = fresh
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        fixed += 1
        if fixed <= 5 or fixed % 50 == 0:
            print(f"  [{i}/{len(targets)}] {code}: {overview.get('Market Cap')} "
                  f"P/E {overview.get('Stock P/E')} basis={fresh.get('basis')}")

    print(f"[repair] fixed {fixed}, still blank on both statements {still}, failed {failed} "
          f"in {(time.time() - start) / 60:.1f}min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
