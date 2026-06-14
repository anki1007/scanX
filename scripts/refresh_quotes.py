"""
Delayed NSE/BSE quotes -> docs/data/quotes.json  (NO broker, NO login).

One NSE Total-Market batch call (~750 liquid names) + budgeted BSE per-scrip
lookups for the codes currently on the PEAD board. The boards read this STATIC
file, so quotes render identically on GitHub Pages and on the local server —
scanX.bat is not required. Effective delay 1-3 minutes + the refresh cadence
(GitHub Actions: every ~15 min during market hours).

    python scripts/refresh_quotes.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.nsequotes import NseBseProvider   # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
OUT = ROOT / "docs" / "data" / "quotes.json"


def _atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def board_codes() -> list:
    """Codes the PEAD board displays (NSE symbols + BSE numeric codes)."""
    try:
        rows = json.loads((ROOT / "docs" / "data" / "pead.json")
                          .read_text(encoding="utf-8", errors="replace"))
        return sorted({str(r.get("code") or "").upper() for r in rows
                       if r.get("code")})
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    prov = NseBseProvider()
    snap = prov._nse_snapshot()                     # one batch call, cached 60s
    quotes = {}
    for sym, q in snap.items():
        lp, prev = q.get("last_price"), q.get("prev_close")
        pct = round((lp - prev) / prev * 100, 2) if (lp and prev) else None
        quotes[sym] = {"ltp": lp, "pct": pct}

    extra = [c for c in board_codes() if c not in quotes][:30]
    if extra:
        keys = [f"BSE:{c}" if c.isdigit() else f"NSE:{c}" for c in extra]
        for k, v in prov.get_quotes(keys).items():
            code = k.split(":", 1)[1]
            lp = v.get("last_price")
            prev = (v.get("ohlc") or {}).get("close")
            nc = v.get("net_change")
            base = (lp - nc) if (lp is not None and nc is not None) else prev
            pct = round((lp - base) / base * 100, 2) if (lp and base) else None
            quotes[code] = {"ltp": lp, "pct": pct}

    if not quotes:
        print(f"[quotes] nothing fetched ({prov.last_error}) — keeping previous file")
        return 1
    now = datetime.now(IST)
    _atomic(OUT, json.dumps({
        "ts": int(time.time()),
        "ist": now.strftime("%H:%M IST"),
        "date": now.strftime("%Y-%m-%d"),
        "source": "NSE/BSE delayed (~1-3 min)",
        "quotes": quotes,
    }, separators=(",", ":")))
    print(f"[quotes] wrote {len(quotes)} quotes | {now:%H:%M:%S IST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
