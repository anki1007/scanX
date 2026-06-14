"""
Entry point for the live round-the-clock scanner.

    python scripts/run_scanner.py --mode live  --start 09:00 --end 23:55 --poll 60
    python scripts/run_scanner.py --mode demo  --once        # offline smoke test

Kite credentials are read from (in order): environment variables
KITE_API_KEY / KITE_ACCESS_TOKEN, then a kite_token.json in the project root
(written by scripts/kite_login.py). If no valid Kite token is present, the
scanner still runs in announcement-only mode (no live price reaction read).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel import DEFAULTS                       # noqa: E402
from earnings_intel.scanner import LiveScanner           # noqa: E402
from earnings_intel.alert_sink import AlertSink          # noqa: E402


def _load_kite():
    api_key = os.environ.get("KITE_API_KEY")
    access_token = os.environ.get("KITE_ACCESS_TOKEN")
    tok_file = ROOT / "kite_token.json"
    if (not api_key or not access_token) and tok_file.exists():
        try:
            data = json.loads(tok_file.read_text())
            api_key = api_key or data.get("api_key")
            access_token = access_token or data.get("access_token")
        except Exception:  # noqa: BLE001
            pass
    if not (api_key and access_token):
        return None
    try:
        from earnings_intel.data.kite_provider import KiteProvider
        return KiteProvider(api_key=api_key, access_token=access_token,
                            allow_orders=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Kite init failed ({e}); running announcement-only.")
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Technofunda live earnings scanner")
    p.add_argument("--mode", choices=["live", "demo"], default="live")
    p.add_argument("--start", default="09:00")
    p.add_argument("--end", default="23:55")
    p.add_argument("--poll", type=int, default=60)
    p.add_argument("--equity", type=float, default=1_000_000.0)
    p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    p.add_argument("--alerts-dir", default=str(ROOT / "alerts"))
    args = p.parse_args()

    sink = AlertSink(Path(args.alerts_dir))

    if args.mode == "demo":
        from earnings_intel.data import SampleProvider
        scanner = LiveScanner(Path(args.alerts_dir), DEFAULTS, args.equity, sink=sink)
        scanner.run(mode="demo", run_once=True, demo_provider=SampleProvider(seed=7))
        return

    kite = _load_kite()
    if kite is None:
        sink.info("No Kite token found — announcement-only mode. "
                  "Run scripts/kite_login.py for the price-reaction read.")
    feed = None
    try:
        from earnings_intel.data.nse_bse import NseBseFeed
        feed = NseBseFeed()
    except Exception as e:  # noqa: BLE001
        sink.info(f"NSE/BSE feed unavailable ({e}); install requests.")

    scanner = LiveScanner(Path(args.alerts_dir), DEFAULTS, args.equity,
                          kite_provider=kite, feed=feed, sink=sink)
    scanner.run(start=args.start, end=args.end, poll=args.poll,
                mode="live", run_once=args.once)


if __name__ == "__main__":
    main()
