"""
Market Pulse publisher (screener.in logged-in feeds) -> three JSON files:
  docs/data/deals.json          bulk + block + insider + SAST trades
  docs/data/actions.json        dividend + bonus + split + right
  docs/data/announcements.json  market-wide corporate announcements

    python scripts/refresh_marketpulse.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
    ap = argparse.ArgumentParser(description="Market Pulse feeds")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--delay", type=float, default=0.8)
    args = ap.parse_args()
    sid = _sid()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    deals = []
    for k in mp.TRADE_KINDS:
        d = mp.fetch_trades(k, sid); deals += d
        print(f"[mp] trades/{k}: {len(d)}"); time.sleep(args.delay)
    _atomic(out / "deals.json", json.dumps({"generated_at_ist": now, "rows": deals}, separators=(",", ":")))

    actions = []
    for k in mp.ACTION_KINDS:
        a = mp.fetch_actions(k, sid); actions += a
        print(f"[mp] actions/{k}: {len(a)}"); time.sleep(args.delay)
    _atomic(out / "actions.json", json.dumps({"generated_at_ist": now, "rows": actions}, separators=(",", ":")))

    anns = mp.fetch_announcements(sid)
    print(f"[mp] announcements: {len(anns)}")
    _atomic(out / "announcements.json", json.dumps({"generated_at_ist": now, "rows": anns}, separators=(",", ":")))
    print(f"[mp] done: {len(deals)} deals, {len(actions)} actions, {len(anns)} announcements -> {out}")


if __name__ == "__main__":
    main()
