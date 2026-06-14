"""
Realtime intraday engine entry point (09:15-15:30 IST by default).

    python scripts/run_realtime.py            # live if Dhan/Kite token present, else synthetic
    python scripts/run_realtime.py --once     # one cycle and exit (smoke test)

Live-feed provider is chosen automatically: Dhan first, then Kite, else a
synthetic feed over the scanX PEAD names so the loop always runs.
Screening + alerts only - never places orders.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.realtime import RealtimeEngine          # noqa: E402
from earnings_intel.alert_sink import AlertSink             # noqa: E402


def _load_dhan():
    client_id = os.environ.get("DHAN_CLIENT_ID")
    token = os.environ.get("DHAN_ACCESS_TOKEN")
    tok_file = ROOT / "dhan_token.json"
    if (not client_id or not token) and tok_file.exists():
        try:
            d = json.loads(tok_file.read_text())
            client_id = client_id or d.get("client_id")
            token = token or d.get("access_token")
        except Exception:  # noqa: BLE001
            pass
    if not (client_id and token):
        return None
    try:
        from earnings_intel.data.dhan_provider import DhanProvider
        return DhanProvider(client_id=client_id, access_token=token)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Dhan init failed ({e}); will try Kite/synthetic.")
        return None


def _load_kite():
    api_key = os.environ.get("KITE_API_KEY")
    token = os.environ.get("KITE_ACCESS_TOKEN")
    tok_file = ROOT / "kite_token.json"
    if (not api_key or not token) and tok_file.exists():
        try:
            d = json.loads(tok_file.read_text())
            api_key = api_key or d.get("api_key")
            token = token or d.get("access_token")
        except Exception:  # noqa: BLE001
            pass
    if not (api_key and token):
        return None
    try:
        from earnings_intel.data.kite_provider import KiteProvider
        return KiteProvider(api_key=api_key, access_token=token, allow_orders=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Kite init failed ({e}); using synthetic feed.")
        return None


def _load_nse():
    """Free no-login fallback: NSE Total-Market batch snapshot (~1-3 min delay)."""
    try:
        from earnings_intel.data.nsequotes import NseBseProvider
        return NseBseProvider()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] NSE/BSE provider init failed ({e}).")
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Technofunda realtime intraday engine")
    p.add_argument("--start", default="09:15")
    p.add_argument("--end", default="15:30")
    p.add_argument("--poll", type=int, default=60)
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--min-move", type=float, default=3.0, help="min abs %% move to alert")
    p.add_argument("--once", action="store_true")
    p.add_argument("--alerts-dir", default=str(ROOT / "alerts"))
    args = p.parse_args()

    sink = AlertSink(Path(args.alerts_dir))
    provider = _load_dhan() or _load_kite() or _load_nse()
    if provider is None:
        sink.info("No Dhan/Kite token and NSE/BSE fallback unavailable - running "
                  "SYNTHETIC realtime feed (for testing). Add Dhan/Kite creds to "
                  "credentials.ps1, or check network for the free NSE/BSE feed.")
    else:
        sink.info(f"Live feed provider: {type(provider).__name__}")

    engine = RealtimeEngine(
        alerts_dir=Path(args.alerts_dir),
        provider=provider,
        pead_path=ROOT / "docs" / "data" / "pead.json",
        sink=sink,
        out_path=ROOT / "docs" / "data" / "intraday.json",
    )
    engine.run(start=args.start, end=args.end, poll=args.poll,
               run_once=args.once, top_n=args.top, min_abs_move=args.min_move)


if __name__ == "__main__":
    main()
