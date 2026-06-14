"""
Generate a fresh Kite access token (run once each morning).

Zerodha access tokens expire daily, so unattended trading-data access requires a
new token every market day. This helper walks you through the OAuth exchange and
writes kite_token.json, which run_scanner.py picks up automatically.

Usage:
    set KITE_API_KEY=your_api_key
    set KITE_API_SECRET=your_api_secret
    python scripts/kite_login.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    api_key = os.environ.get("KITE_API_KEY")
    api_secret = os.environ.get("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("Set KITE_API_KEY and KITE_API_SECRET environment variables first.")
        sys.exit(1)

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("kiteconnect not installed. Run: pip install kiteconnect")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    print("\n1) Open this URL, log in, and authorise:\n")
    print("   " + kite.login_url() + "\n")
    print("2) After login you'll be redirected to your redirect URL with a")
    print("   `request_token=...` in the address bar. Paste that token here.\n")
    request_token = input("request_token: ").strip()

    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    out = ROOT / "kite_token.json"
    out.write_text(json.dumps({
        "api_key": api_key,
        "access_token": access_token,
        "date": date.today().isoformat(),
    }, indent=2))
    print(f"\nSaved {out}. The scanner will use this token today.")
    print("Tip: tokens expire daily — re-run this each morning before 09:00.")


if __name__ == "__main__":
    main()
