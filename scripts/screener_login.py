"""
Establish a fresh Screener.in session at scanX.bat startup and cache it to
screener_session.json, so every scanX component (PEAD 60s loop, Orders /
Buybacks / Special daily jobs, fundamentals) reuses one valid login and never
falls back to sample data.

Run from the main scanX.bat scope, where credentials.ps1 (SCREENER_EMAIL /
SCREENER_PASSWORD) is loaded. Order of preference:
  0. if Screener is UNREACHABLE (network blip / temporary throttle after a
     heavy crawl) -> keep the cached session and say so honestly; this is
     NOT a credentials problem and everything resumes when the site responds
  1. fresh email/password login   (best - never expires mid-day)
  2. SCREENER_SESSIONID env cookie
  3. keep an existing valid cached session
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data.screener import ScreenerClient, _BASE  # noqa: E402

CACHE = ROOT / "screener_session.json"


def _cache(sid: str) -> None:
    try:
        CACHE.write_text(json.dumps({"sessionid": sid}))
    except Exception as e:  # noqa: BLE001
        print(f"Screener: could not write session cache: {e}")


def _valid(sid: str) -> bool:
    """Reliable check: can we load the results page with this session?"""
    try:
        return ScreenerClient(session_id=sid)._get(f"{_BASE}/results/latest/") is not None
    except Exception:  # noqa: BLE001
        return False


def _reachable(timeout: int = 8) -> bool:
    """Can we even open a TCP/HTTP connection to Screener right now?

    Any HTTP response (200/405/403/...) counts as reachable — only a network
    error (connect timeout, DNS, reset) means unreachable. Separates "site or
    route is down / IP throttled" from "wrong credentials" so the startup
    message never blames credentials.ps1 for a network problem.
    """
    try:
        import requests
        requests.head(f"{_BASE}/login/", timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0"})
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    email = os.environ.get("SCREENER_EMAIL")
    pw = os.environ.get("SCREENER_PASSWORD")
    sid = os.environ.get("SCREENER_SESSIONID")

    cached = None
    if CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            cached = None

    if not _reachable():
        if cached or sid:
            if sid:
                _cache(sid)
            print("Screener: site UNREACHABLE right now (network blip or temporary "
                  "rate-limit after a heavy crawl) - NOT a credentials problem. "
                  "Keeping the cached session; boards resume automatically once "
                  "Screener responds.")
        else:
            print("Screener: site unreachable and no cached session yet - boards "
                  "use sample data until the next refresh cycle reaches Screener.")
        return

    if email and pw:
        c = ScreenerClient()
        if c.login(email, pw) and c.session_id():
            _cache(c.session_id())
            print("Screener: fresh login OK - session cached for all tabs.")
            return
        print("Screener: email/password login was REJECTED by the site - "
              "check credentials.ps1.")

    if sid and _valid(sid):
        _cache(sid)
        print("Screener: SCREENER_SESSIONID valid - cached.")
        return

    if cached and _valid(cached):
        print("Screener: existing cached session still valid.")
        return

    print("Screener: NO valid login - set SCREENER_EMAIL/PASSWORD in credentials.ps1 "
          "(boards will use sample data until then).")


if __name__ == "__main__":
    main()
