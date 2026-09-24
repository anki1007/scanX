#!/usr/bin/env python3
"""Strip data-vendor names out of the baked JSON before it ships.

Runs LAST in the bake, after every refresh script has written its output. That
placement is deliberate: it means a refresh script can be written without
thinking about the rule, and adding a new one cannot reintroduce the leak.

Idempotent -- running it twice changes nothing the second time.

    python scripts/redact_sources.py --check     # report, change nothing
    python scripts/redact_sources.py             # rewrite in place
    python scripts/redact_sources.py docs/data/pead.json docs/data/meta.json
                                                 # just these files (intraday loop)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.redact import is_vendor_url, redact, redact_deep  # noqa: E402

DATA = ROOT / "docs" / "data"
KINDS = (".json", ".csv")


def _clean_csv(raw: str) -> str:
    """A CSV with every vendor URL blanked and every cell redacted.

    pead.csv carried a vendor company URL on every row, and this script used
    to read JSON only, so the nightly pass never saw it.
    """
    rows = list(csv.reader(io.StringIO(raw)))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in rows:
        w.writerow(["" if is_vendor_url(c) else redact(c) for c in row])
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report what would change and exit non-zero if anything would")
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("files", nargs="*",
                    help="only these files; the whole data dir when omitted. The "
                         "intraday loop publishes a handful of boards every hour "
                         "and has no reason to re-read ~5,700 bundles each time")
    args = ap.parse_args()

    root = Path(args.data_dir)
    if not root.exists():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2
    if args.files:
        paths = [Path(f) for f in args.files if f.endswith(KINDS) and Path(f).is_file()]
        for f in args.files:
            if not (f.endswith(KINDS) and Path(f).is_file()):
                print(f"skipped (missing or not JSON/CSV): {f}")
    else:
        paths = sorted(p for kind in KINDS for p in root.rglob("*" + kind))

    scanned = changed = failed = 0
    names: list[str] = []

    for path in paths:
        scanned += 1
        try:
            raw = path.read_text(encoding="utf-8")
            if path.suffix == ".csv":
                cleaned_csv = _clean_csv(raw)
                if cleaned_csv != raw.replace("\r\n", "\n"):
                    changed += 1
                    names.append(path.name)
                    if not args.check:
                        path.write_text(cleaned_csv, encoding="utf-8")
                continue
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
        try:
            names.append(str(path.resolve().relative_to(root.resolve())))
        except ValueError:
            names.append(str(path))
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
