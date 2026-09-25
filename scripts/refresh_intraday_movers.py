"""
Intraday movers (docs/data/intraday.json) from the quotes already on disk.

intraday.json used to come only from the desktop realtime engine
(scripts/run_realtime.py), so on the static site it was last written in June
and the home page showed the module stale every day. That engine is not run in
CI on purpose: with no broker token and the exchange feed blocked on a
datacenter IP it falls back to a SYNTHETIC feed, and made-up prices must never
be published. This builds the same rows from the real quotes the market-hours
loop fetches every cycle -- no extra network, nothing invented -- and stamps
them with the quotes' own time, not the time this script ran.

    python scripts/refresh_intraday_movers.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _f(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _row(code: str, q: dict, pead: dict) -> dict | None:
    last, pct = _f(q.get("ltp")), _f(q.get("pct"))
    # An untraded or suspended scrip reports LTP 0 and a -100% "move", which
    # would top a ranking by the size of the move.
    if last is None or pct is None or last <= 0 or pct <= -100:
        return None
    hi, lo, wap = _f(q.get("high")), _f(q.get("low")), _f(q.get("wap"))
    p = pead.get(code.upper()) or {}
    return {
        "symbol": code, "exchange": q.get("ex") or "BSE",
        "last": round(last, 2), "pct_change": round(pct, 2),
        "open": _f(q.get("open")), "high": hi, "low": lo,
        "prev_close": _f(q.get("prev_close")),
        "vwap_pos": round((last / wap - 1) * 100, 2) if wap else None,
        "range_pos": round((last - lo) / (hi - lo) * 100, 0)
        if hi is not None and lo is not None and hi > lo else None,
        "volume": int(q["vol"]) if _f(q.get("vol")) is not None else None,
        "pead_score": p.get("pead_score"), "pead_category": p.get("pead_category"),
    }


def build(quotes: dict, wide: dict, pead_rows: list, top: int = 100) -> dict | None:
    """The intraday.json payload, or None when there is no quote to build from."""
    pead = {str(r.get("code") or "").upper(): r for r in pead_rows or [] if isinstance(r, dict)}
    merged: dict = {}
    for src in (wide, quotes):              # the exchange read, with OHLC, wins
        for code, q in (src.get("quotes") or {}).items():
            if isinstance(q, dict):
                merged[str(code)] = q
    rows = [r for r in (_row(c, q, pead) for c, q in merged.items()) if r]
    if not rows:
        return None
    # the engine's ranking: PEAD conviction plus the size of today's move
    rows.sort(key=lambda m: ((m["pead_score"] or 0) / 100.0) + abs(m["pct_change"]) / 10.0,
              reverse=True)
    ts = max(int(s.get("ts") or 0) for s in (quotes, wide))
    if not ts:
        return None
    return {"generated_at": datetime.fromtimestamp(ts, IST).isoformat(timespec="seconds"),
            "source": "quotes", "rows": rows[:top]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args(argv)
    data = Path(args.data)
    pead = []
    try:
        pead = json.loads((data / "pead.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    out = build(_load(data / "quotes.json"), _load(data / "quotes_wide.json"),
                pead if isinstance(pead, list) else [], args.top)
    if out is None:
        print("[movers] no quotes to build from - keeping the last file")
        return 1
    tmp = data / "intraday.json.tmp"
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    os.replace(tmp, data / "intraday.json")
    print(f"[movers] {len(out['rows'])} movers as of {out['generated_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
