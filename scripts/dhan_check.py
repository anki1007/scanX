r"""
Dhan token self-test — verifies your dhan_token.json actually returns live quotes
BEFORE you rely on it, so a bad/expired/wrong-type token can never silently show
up as "LTP 0/25" again.

    python scripts/dhan_check.py
    python scripts/dhan_check.py --symbol RELIANCE

Exit code 0 = quotes flowing; 1 = something to fix (prints the exact reason).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _b64(s):
    return base64.urlsafe_b64decode((s + "=" * (-len(s) % 4)).encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="RELIANCE")
    args = ap.parse_args()

    tf = ROOT / "dhan_token.json"
    if not tf.exists():
        print("✗ dhan_token.json not found. Run scripts/dhan_login.py (or place a token).")
        return 1
    j = json.loads(tf.read_text())
    cid, tok = str(j.get("client_id") or ""), j.get("access_token") or ""
    if not cid or not tok:
        print("✗ token file missing client_id or access_token.")
        return 1

    # decode JWT for human-readable diagnostics (no secrets printed)
    segs = tok.split(".")
    if len(segs) == 3:
        try:
            p = json.loads(_b64(segs[1]))
            exp = p.get("exp")
            if exp:
                ed = datetime.fromtimestamp(exp, tz=timezone.utc)
                print(f"  token expiry (UTC): {ed:%Y-%m-%d %H:%M}  "
                      f"({'EXPIRED' if ed < datetime.now(timezone.utc) else 'valid'})")
            consumer = p.get("tokenConsumerType")
            if p.get("partnerId") or (consumer and str(consumer).upper() != "SELF"):
                print("  ⚠ this looks like a PARTNER token (has partnerId / tokenConsumerType). "
                      "The market-data API wants a SELF access token from web.dhan.co "
                      "(Profile → DhanHQ Trading APIs → Generate Access Token).")
        except Exception:  # noqa: BLE001
            pass
    else:
        print("  ⚠ access_token is not a 3-part JWT — likely malformed/truncated.")

    from earnings_intel.data.dhan_provider import DhanProvider
    prov = DhanProvider(cid, tok, cache_path=str(ROOT / ".cache" / "dhan_master.json"))
    code = args.symbol.strip().upper()
    key = f"BSE:{code}" if code.isdigit() else f"NSE:{code}"
    q = prov.get_quotes([key])
    hit = q.get(key)
    if hit and hit.get("last_price") is not None:
        print(f"✓ Dhan live quote OK — {code} LTP ₹{hit['last_price']} "
              f"(net_change {hit.get('net_change')})")
        print("  Live prices will flow. You're good.")
        return 0
    print(f"✗ No quote for {code}.")
    if getattr(prov, "last_error", None):
        print(f"  Dhan said: {prov.last_error}")
    print("  Fixes: (1) generate a SELF access token at web.dhan.co and replace the "
          "access_token in dhan_token.json; (2) ensure your Dhan Data API is active; "
          "(3) re-run this check.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
