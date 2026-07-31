"""
Bake the rules-based buy/hold/exit book -> docs/data/portfolio.json.

Answers the question the agent comparison does not: WHICH STOCKS to hold, on
what signal, what would take each one out, and what replaces it when one goes.

    python scripts/refresh_portfolio.py --size 15
    python scripts/refresh_portfolio.py --dry-run

Stateful by design. The previous book is read back from the published file so
entry prices and dates survive, which is what makes "-22% from entry" mean
anything. Deterministic otherwise: no model, no key, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.portfolio import DEFAULTS, rebalance  # noqa: E402

DATA = ROOT / "docs" / "data"
OUT = DATA / "portfolio.json"


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def candidates(limit: int = 1200) -> list[dict]:
    """Board rows joined to each bundle's analysis score and health block."""
    board = _read(DATA / "technofunda.json", [])
    sectors = _read(DATA / "sector_tailwind.json", {})
    signal = {s.get("sector"): s.get("signal") for s in (sectors.get("sectors") or [])}
    out = []
    for row in board[:limit]:
        code = str(row.get("code") or "")
        if not code:
            continue
        bundle = _read(DATA / "fundamental" / f"{code}.json", {})
        health = ((bundle.get("fundamental") or {}).get("analysis") or {}).get("health") or {}
        out.append({
            "code": code, "name": row.get("name"), "sector": row.get("sector"),
            "sector_signal": signal.get(row.get("sector")),
            "score": (bundle.get("score") or {}).get("score"),
            "ltp": row.get("ltp"), "mcap": row.get("mcap"), "pe": row.get("pe"),
            "pe_sector": ((health.get("peers") or {}).get("pe") or {}).get("sector"),
            "health": health,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake the rules-based holdings book")
    ap.add_argument("--size", type=int, default=DEFAULTS["size"])
    ap.add_argument("--limit", type=int, default=1200,
                    help="how deep into the board to look for candidates")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    cands = candidates(args.limit)
    if not cands:
        print("[book] no board on disk - nothing to screen"); return 0

    prior = _read(Path(args.out), {})
    previous = (prior.get("holdings") or []) + (prior.get("exits") or [])
    today = time.strftime("%Y-%m-%d")
    book = rebalance(previous, cands, size=args.size, today=today)

    payload = {"generated_at": today, "universe": len(cands), **book,
               "history": ((prior.get("history") or []) + [{
                   "date": today, "holdings": len(book["holdings"]),
                   "entries": len(book["entries"]), "exits": len(book["exits"]),
               }])[-120:]}

    if args.dry_run:
        print(f"[book] dry run: {len(book['holdings'])} holdings, "
              f"{len(book['entries'])} entries, {len(book['exits'])} exits")
        return 0

    _atomic(Path(args.out), json.dumps(payload, separators=(",", ":")))
    print(f"[book] {len(cands)} screened -> {len(book['holdings'])} holdings, "
          f"{len(book['entries'])} new, {len(book['exits'])} exited")
    for h in book["holdings"][:8]:
        print(f"   HOLD {h['code']:12} score {h.get('score')}  {str(h.get('change_pct'))}%")
    for e in book["exits"]:
        print(f"   EXIT {e['code']:12} {'; '.join(e['triggers'])[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
