"""
Pre-bake the full Fundamental bundle for the board's top stocks, so the
Fundamental tab works on the STATIC GitHub Pages site too (no local API).

For each top-N code in docs/data/technofunda.json it writes
docs/data/fundamental/<CODE>.json = {fundamental, prices, signal} — exactly what
/api/fundamental + /api/prices + /api/signal return live. fundamental.html falls
back to these files when the local API isn't present.

    python scripts/refresh_fundamentals.py                 # top 120
    python scripts/refresh_fundamentals.py --top 200
    python scripts/refresh_fundamentals.py --limit 3       # quick test
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

from earnings_intel.data import company as co        # noqa: E402
from earnings_intel.data import pricehist as ph       # noqa: E402
from earnings_intel.data import signal as sg          # noqa: E402
from earnings_intel.data import sectorlookup as sl

_SESSION = ROOT / "screener_session.json"


def _read_board(path):
    """Read the board JSON, recovering from a truncated/null-padded concurrent write."""
    try:
        raw = path.read_bytes().rstrip(b"\x00").rstrip()
    except Exception:  # noqa: BLE001
        return []
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        i = raw.rfind(b"}")               # close the array at the last complete object
        if i > 0:
            try:
                return json.loads(raw[:i + 1] + b"]")
            except Exception:  # noqa: BLE001
                return []
        return []


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def main():
    ap = argparse.ArgumentParser(description="Pre-bake fundamentals for the public site")
    ap.add_argument("--top", type=int, default=120, help="how many top board stocks to bake")
    ap.add_argument("--limit", type=int, default=0, help="cap (testing)")
    ap.add_argument("--skip-existing", action="store_true", help="skip codes already baked today (resumable)")
    ap.add_argument("--board", default=str(ROOT / "docs" / "data" / "technofunda.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop baking after N minutes (0=no limit) so the cloud commits incrementally")
    args = ap.parse_args()

    board = _read_board(Path(args.board))
    if not board:
        print(f"[fund] board empty/unreadable: {args.board}"); return
    board.sort(key=lambda r: r.get("composite", 0), reverse=True)
    codes = [r["code"] for r in board[:args.top] if r.get("code")]

    # also bake the Magic Formula top-100 (the board's default view) AND every
    # PEAD-board name (the homepage) so clicking them works on GitHub Pages
    # without the local server
    try:
        mf = json.loads((ROOT / "docs" / "data" / "magicformula.json")
                        .read_text(encoding="utf-8", errors="replace"))
        rows = mf.get("rows") or []
        for r in rows[:100]:                      # Magic Formula top-100
            c = str(r.get("code") or "")
            if c and c not in codes:
                codes.append(c)
        # ... plus the 300 largest companies by market cap, so household
        # names (RELIANCE, TCS, banks, ...) always open on GitHub Pages
        big = sorted((r for r in rows if isinstance(r.get("mcap"), (int, float))),
                     key=lambda r: -r["mcap"])[:300]
        for r in big:
            c = str(r.get("code") or "")
            if c and c not in codes:
                codes.append(c)
    except Exception:  # noqa: BLE001
        pass
    try:
        pead = json.loads((ROOT / "docs" / "data" / "pead.json")
                          .read_text(encoding="utf-8", errors="replace"))
        for r in pead if isinstance(pead, list) else []:
            c = str(r.get("code") or "")
            if c and c not in codes:
                codes.append(c)
    except Exception:  # noqa: BLE001
        pass

    # --- "bake everything": also bake every stock reachable from the Fair Value,
    # Special Situations and FII-sector screens, so clicking ANY of them opens on
    # the static GitHub Pages site (no local server needed).
    def _add(seq):
        for c in seq:
            c = str(c or "").strip()
            if c and c not in codes:
                codes.append(c)

    try:                                    # Fair Value  (docs/data/iv_fairvalue.json)
        fv = json.loads((ROOT / "docs" / "data" / "iv_fairvalue.json")
                        .read_text(encoding="utf-8", errors="replace"))
        _add(r.get("code") for r in (fv.get("rows") or []))
    except Exception:  # noqa: BLE001
        pass
    try:                                    # Special Situations  (docs/data/special.json)
        sp = json.loads((ROOT / "docs" / "data" / "special.json")
                        .read_text(encoding="utf-8", errors="replace"))
        _add(r.get("code") for r in (sp if isinstance(sp, list) else []))
    except Exception:  # noqa: BLE001
        pass
    try:                                    # FII sector stocks  (docs/data/sector_stocks.json)
        ss = json.loads((ROOT / "docs" / "data" / "sector_stocks.json")
                        .read_text(encoding="utf-8", errors="replace"))
        for _lst in (ss.get("sectors") or {}).values():
            _add(r.get("code") for r in (_lst or []) if isinstance(r, dict))
    except Exception:  # noqa: BLE001
        pass

    if args.limit:
        codes = codes[:args.limit]

    sid = _sid()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    done, fail, avail = 0, 0, []
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    _bake_start = time.time()
    for i, code in enumerate(codes, 1):
        if args.max_minutes and (time.time() - _bake_start) > args.max_minutes * 60:
            print(f'[fund] time budget {args.max_minutes:.0f}min reached at {i}/{len(codes)} - committing what is baked'); break
        bf = out / f"{code}.json"
        if args.skip_existing and bf.exists() and _t.strftime("%Y-%m-%d", _t.localtime(bf.stat().st_mtime)) == today:
            avail.append(code); continue
        try:
            fund = co.fundamentals(code, sid)
            if "error" in fund:
                fail += 1; continue
            price = ph.price_analytics(code, overview=fund.get("overview"))
            sec = sl.sector_for(code, fund.get("name"))
            sigv = sg.technofunda_signal(fund, price, sec)
            _atomic(out / f"{code}.json", json.dumps(
                {"fundamental": fund, "prices": price, "signal": sigv},
                separators=(",", ":")))
            avail.append(code); done += 1
            if i <= 8 or i % 25 == 0:
                print(f"  [{i}/{len(codes)}] baked {fund.get('name','')[:30]} ({code})")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(codes)}] FAIL {code}: {type(e).__name__}")
        time.sleep(0.15)

    _atomic(out / "index.json", json.dumps(sorted(avail)))
    print(f"[fund] baked {done} bundles, {fail} failed -> {out}")


if __name__ == "__main__":
    main()
