r"""
Generate a Dhan access token for live market data (auto, run by scanX.bat).

Reads credentials in this order (no secrets are copied into other files):
  1. env vars  DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET
  2. the dhan\dhan_token_generator.py you placed in this folder
     (its DEFAULT_CLIENT_ID / DEFAULT_PIN / DEFAULT_TOTP_SECRET constants)
Then mints the daily token via Dhan and writes dhan_token.json (git-ignored).

SECURITY: keep the dhan\ folder OUT of git (it is gitignored). Those constants
are full account credentials - never commit or publish them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _from_folder() -> dict:
    f = ROOT / "dhan" / "dhan_token_generator.py"
    if not f.exists():
        return {}
    txt = f.read_text(errors="ignore")
    def grab(name):
        m = re.search(rf"{name}\s*=\s*[\"']([^\"']+)[\"']", txt)
        return m.group(1) if m else None
    return {"client_id": grab("DEFAULT_CLIENT_ID"),
            "pin": grab("DEFAULT_PIN"),
            "totp": grab("DEFAULT_TOTP_SECRET")}


def main() -> None:
    folder = _from_folder()
    client_id = os.environ.get("DHAN_CLIENT_ID") or folder.get("client_id")
    pin = os.environ.get("DHAN_PIN") or folder.get("pin")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET") or folder.get("totp")
    if not (client_id and pin and totp_secret):
        print("Dhan creds not found (set DHAN_* env vars or put dhan\\dhan_token_generator.py). Skipping.")
        sys.exit(1)
    try:
        import pyotp
        import requests
    except ImportError:
        print("Install deps:  pip install pyotp requests")
        sys.exit(1)

    totp = pyotp.TOTP(totp_secret).now()
    try:
        r = requests.post("https://auth.dhan.co/app/generateAccessToken",
                          params={"dhanClientId": client_id, "pin": pin, "totp": totp},
                          timeout=20)
        data = r.json() if r.status_code == 200 else {}
    except Exception as e:  # noqa: BLE001
        print(f"Dhan token request failed: {e}")
        sys.exit(1)

    token = (data.get("accessToken") or data.get("access_token")
             or (data.get("data") or {}).get("accessToken"))
    if not token:
        print(f"Could not parse access token (response: {data}).")
        sys.exit(1)

    (ROOT / "dhan_token.json").write_text(json.dumps(
        {"client_id": client_id, "access_token": token,
         "date": date.today().isoformat()}, indent=2))
    print("Dhan token saved (dhan_token.json). Realtime engine will use Dhan today.")


if __name__ == "__main__":
    main()
