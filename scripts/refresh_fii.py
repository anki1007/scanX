"""
FII sector-flow publisher.

Scrapes screener.in/fii/ and writes docs/data/fii.json (sector-wise FII net
flow). Also consumed by refresh_sectors.py to blend real FII flow into the
Sector Tailwind score, so run this BEFORE refresh_sectors.

    python scripts/refresh_fii.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import marketpulse as mp   # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(text); os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="FII sector-flow feed")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    rows = mp.fetch_fii(_sid())
    if not rows:
        print("[fii] no data (need a logged-in Screener session)"); return
    now = datetime.now(IST)
    tot_fn = round(sum(r["fortnight"] for r in rows if r.get("fortnight") is not None))
    tot_y1 = round(sum(r["oneY"] for r in rows if r.get("oneY") is not None))
    data = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "sectors": rows, "totals": {"fortnight": tot_fn, "oneY": tot_y1}}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    _atomic(out / "fii.json", json.dumps(data, separators=(",", ":")))
    print(f"[fii] {len(rows)} sectors | fortnight net Rs {tot_fn} Cr | 1Y net Rs {tot_y1} Cr")


if __name__ == "__main__":
    main()
