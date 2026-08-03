#!/usr/bin/env python3
"""Bake docs/data/advanced.json - the advanced fundamental checklist board.

One row per company: the score, the pass/warn/fail/na counts, and every check
as a two-element [verdict, value] pair.

The detail SENTENCES are not stored. They are rebuilt on the page from the key
and the value, because storing them would multiply the file by six for text
that is entirely derivable - 25 checks of prose per company across 5,499
companies is megabytes of duplicated English. The numbers are the data; the
wording is presentation.

    python scripts/refresh_advanced.py
    python scripts/refresh_advanced.py --limit 50      # a quick look
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

from earnings_intel.data.advanced import CHECKS, evaluate  # noqa: E402

#: verdict -> one character, because it is repeated 25 times per company
SHORT = {"pass": "p", "warn": "w", "fail": "f", "na": "n"}


def _num(value):
    if value is None:
        return None
    m = re.search(r"-?\d+\.?\d*", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build(fundamental_dir: Path, limit: int = 0) -> dict:
    rows = []
    skipped = 0
    for path in sorted(fundamental_dir.glob("*.json")):
        if path.stem == "index":
            continue
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        if not isinstance(bundle, dict):
            skipped += 1
            continue

        result = evaluate(bundle)
        by_key = {c["key"]: [SHORT[c["verdict"]], c["value"]] for c in result["checks"]}
        # A company whose statements are too thin to decide anything is not a
        # zero-scoring company, it is an unassessable one. Publishing it as a
        # row would put it at the bottom of a "worst fundamentals" sort on the
        # strength of missing data.
        if result["score"] is None or result["counts"]["decided"] < 8:
            skipped += 1
            continue

        fundamental = bundle.get("fundamental") or {}
        overview = fundamental.get("overview") or {}
        upstox = bundle.get("upstox_ratios") or {}
        pe_row = upstox.get("pe") if isinstance(upstox.get("pe"), dict) else None
        pe = pe_row.get("value") if pe_row else None
        if pe is None:
            pe = _num(overview.get("Stock P/E"))

        rows.append({
            "code": path.stem,
            "name": fundamental.get("name") or path.stem,
            "mcap": _num(overview.get("Market Cap")),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "score": result["score"],
            "p": result["counts"]["pass"],
            "w": result["counts"]["warn"],
            "f": result["counts"]["fail"],
            "n": result["counts"]["na"],
            # POSITIONAL against the `keys` legend written once at the top of
            # the file. Storing the key name on every row cost 2.7 MB of
            # repeated English -- 25 names x 5,444 companies -- for information
            # that is identical in every row.
            "c": [by_key.get(k, ["n", None]) for k in CHECKS],
        })
        if limit and len(rows) >= limit:
            break

    rows.sort(key=lambda r: (-(r["score"] or 0), -(r["mcap"] or 0)))
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "keys": list(CHECKS), "count": len(rows), "skipped": skipped, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "advanced.json"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    fdir = Path(args.fundamental)
    if not fdir.exists():
        print(f"[advanced] no bundles at {fdir}", file=sys.stderr)
        return 1

    payload = build(fdir, args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic(out, json.dumps(payload, separators=(",", ":")))

    size = out.stat().st_size / 1024
    print(f"[advanced] {payload['count']} companies scored, "
          f"{payload['skipped']} too thin to assess -> {out} ({size:.0f} KB)")
    if payload["rows"]:
        best = payload["rows"][0]
        print(f"[advanced] top: {best['name']} score {best['score']} "
              f"({best['p']} pass / {best['w']} warn / {best['f']} fail)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
