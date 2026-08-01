#!/usr/bin/env python3
"""Strip data-vendor names out of the baked JSON before it ships.

Runs LAST in the bake, after every refresh script has written its output. That
placement is deliberate: it means a refresh script can be written without
thinking about the rule, and adding a new one cannot reintroduce the leak.

Idempotent -- running it twice changes nothing the second time.

    python scripts/redact_sources.py --check     # report, change nothing
    python scripts/redact_sources.py             # rewrite in place
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.redact import redact_deep  # noqa: E402

DATA = ROOT / "docs" / "data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report what would change and exit non-zero if anything would")
    ap.add_argument("--data-dir", default=str(DATA))
    args = ap.parse_args()

    root = Path(args.data_dir)
    if not root.exists():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    scanned = changed = failed = 0
    names: list[str] = []

    for path in sorted(root.rglob("*.json")):
        scanned += 1
        try:
            raw = path.read_text(encoding="utf-8")
            obj = json.loads(raw)
        except Exception:
            # A malformed or half-written bundle is the bake's problem, not
            # ours. Skip it rather than take the whole publish down.
            failed += 1
            continue

        cleaned = redact_deep(obj)
        if cleaned == obj:
            continue

        changed += 1
        names.append(str(path.relative_to(root)))
        if not args.check:
            path.write_text(json.dumps(cleaned, ensure_ascii=False,
                                       separators=(",", ":")), encoding="utf-8")

    verb = "would change" if args.check else "redacted"
    print(f"scanned {scanned} file(s), {verb} {changed}"
          + (f", {failed} unreadable" if failed else ""))
    for n in names[:20]:
        print(f"  {n}")
    if len(names) > 20:
        print(f"  ... and {len(names) - 20} more")

    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
