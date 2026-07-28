"""
Pre-bake the full Fundamental bundle for the board's top stocks, so the
Fundamental tab works on the STATIC GitHub Pages site too (no local API).

For each top-N code in docs/data/technofunda.json it writes
docs/data/fundamental/<CODE>.json = {fundamental, prices, signal} — exactly what
/api/fundamental + /api/prices + /api/signal return live. fundamental.html falls
back to these files when the local API isn't present.

When an Upstox analytics token is configured the bundle also carries
"upstox_ratios" and its analysis.health gains the ratios Screener's compact
statements cannot produce (current ratio, a debt/equity proxy) plus the six
key ratios against their sector benchmark. Without a token nothing is fetched
and the bundle is byte-for-byte what it has always been.

    python scripts/refresh_fundamentals.py                 # top 120
    python scripts/refresh_fundamentals.py --top 200
    python scripts/refresh_fundamentals.py --limit 3       # quick test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import analytics as A        # noqa: E402
from earnings_intel.data import company as co        # noqa: E402
from earnings_intel.data import pricehist as ph       # noqa: E402
from earnings_intel.data import signal as sg          # noqa: E402
from earnings_intel.data import sectorlookup as sl

_SESSION = ROOT / "screener_session.json"

# Upstox key ratios + balance-sheet fill-ins for the health panel. Entirely
# optional: probed ONCE, and with no token configured the whole feature stays
# off so a token-less bake costs nothing and looks exactly like it always has.
_RATIOS = {"ready": None, "fetch": None, "settings": None}


def _ratio_extra(code: str) -> dict:
    """Upstox ratios for one code — {} when unavailable, and never raises.

    A ratio lookup must never fail a company: no token, no upstox_lab, an HTTP
    error or a malformed payload all degrade to {} and the bake carries on with
    the Screener-only numbers.
    """
    if _RATIOS["ready"] is None:
        _RATIOS["ready"] = False
        try:
            from earnings_intel.data.ratios import fetch_ratios
            from upstox_lab import auth
            from upstox_lab.config import UpstoxLabSettings
            settings = UpstoxLabSettings.from_env()
            auth.load_token(settings)          # absent/unreadable token -> stay off
            _RATIOS.update(ready=True, fetch=fetch_ratios, settings=settings)
            print("[fund] Upstox ratios ON - filling current ratio / D-E proxy / peer ratios")
        except Exception as e:  # noqa: BLE001 - optional feature, absent token is routine
            print(f"[fund] Upstox ratios off ({type(e).__name__}) - Screener-only health")
    if not _RATIOS["ready"]:
        return {}
    try:
        return _RATIOS["fetch"](code, settings=_RATIOS["settings"]) or {}
    except Exception as e:  # noqa: BLE001 - network boundary
        print(f"  ratios skipped for {code}: {type(e).__name__}")
        return {}


def _with_upstox_health(fund: dict, code: str) -> dict:
    """Recompute analysis.health with the Upstox fill-ins; return them for the bundle."""
    extra = _ratio_extra(code)
    if not extra:
        return {}
    try:
        health = A.health_ratios(fund.get("balance_sheet") or {},
                                 fund.get("cash_flow") or {},
                                 fund.get("profit_loss") or {},
                                 extra=extra)
        fund.setdefault("analysis", {})["health"] = health
    except Exception as e:  # noqa: BLE001 - a ratio must never break a bake
        print(f"  health merge skipped for {code}: {type(e).__name__}")
        return {}
    return extra


def _backfill_ratios(out, codes, today, max_minutes: float = 0) -> int:
    """Add Upstox ratios to bundles that already exist, without re-scraping Screener.

    A full bake skips any bundle already baked today, so bundles written before
    the ratio feature existed would never GAIN a current ratio — the whole
    universe would sit at "n/a" indefinitely. This pass rewrites just the health
    block from one cheap pair of API calls per company, so coverage catches up
    over a few runs instead of never.
    """
    done = skipped = fail = 0
    start = time.time()
    for i, code in enumerate(codes, 1):
        if max_minutes and (time.time() - start) > max_minutes * 60:
            print(f"[fund] ratios: budget {max_minutes:.0f}min reached at {i}/{len(codes)}")
            break
        bf = out / f"{code}.json"
        if not bf.exists():
            continue
        try:
            bundle = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            fail += 1
            continue
        # already carries ratios from today's run
        if bundle.get("upstox_ratios") and bundle.get("ratios_at") == today:
            skipped += 1
            continue
        fund = bundle.get("fundamental") or {}
        ratios = _with_upstox_health(fund, code)
        if not ratios:
            fail += 1
            continue
        bundle["fundamental"] = fund
        bundle["upstox_ratios"] = ratios
        bundle["ratios_at"] = today
        try:
            _atomic(bf, json.dumps(bundle, separators=(",", ":")))
            done += 1
            if i <= 5 or i % 50 == 0:
                cr = ((fund.get("analysis") or {}).get("health") or {}).get("current_ratio") or {}
                print(f"  [{i}/{len(codes)}] {code}: current ratio {cr.get('value')}")
        except Exception:  # noqa: BLE001
            fail += 1
    print(f"[fund] ratios backfill: updated {done}, already-fresh {skipped}, unavailable {fail}")
    return 0


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
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _baked_on(path):
    """Date a bundle was baked, from the generated_at stamp at the start of the file.

    File mtime is useless in CI (a fresh checkout stamps every file "today"), so
    freshness must live inside the bundle. Legacy bundles without the stamp
    return "" and are treated as stale, which re-bakes them exactly once.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        m = re.match(rb'\{"generated_at":"(\d{4}-\d{2}-\d{2})', head)
        return m.group(1).decode() if m else ""
    except Exception:  # noqa: BLE001
        return ""


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
    ap.add_argument("--skip-any", action="store_true",
                    help="skip codes already baked at all (resume a full-universe bake across runs/days)")
    ap.add_argument("--board", default=str(ROOT / "docs" / "data" / "technofunda.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop baking after N minutes (0=no limit) so the cloud commits incrementally")
    ap.add_argument("--ratios-only", action="store_true",
                    help="refresh ONLY the Upstox ratio/health block of bundles that already exist "
                         "(no Screener re-scrape) — backfills current ratio across the universe")
    args = ap.parse_args()

    board = _read_board(Path(args.board))
    if not board:
        print(f"[fund] board empty/unreadable: {args.board}"); return
    board.sort(key=lambda r: r.get("composite", 0), reverse=True)
    codes = [r["code"] for r in board[:args.top] if r.get("code")]

    seen = set(codes)

    def _add(seq):
        for c in seq:
            c = str(c or "").strip()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)

    # also bake the Magic Formula top-100 (the board's default view) AND every
    # PEAD-board name (the homepage) so clicking them works on GitHub Pages
    # without the local server
    try:
        mf = json.loads((ROOT / "docs" / "data" / "magicformula.json")
                        .read_text(encoding="utf-8", errors="replace"))
        rows = mf.get("rows") or []
        _add(r.get("code") for r in rows[:100])   # Magic Formula top-100
        # ... plus the 300 largest companies by market cap, so household
        # names (RELIANCE, TCS, banks, ...) always open on GitHub Pages
        big = sorted((r for r in rows if isinstance(r.get("mcap"), (int, float))),
                     key=lambda r: -r["mcap"])[:300]
        _add(r.get("code") for r in big)
    except Exception:  # noqa: BLE001
        pass
    try:
        pead = json.loads((ROOT / "docs" / "data" / "pead.json")
                          .read_text(encoding="utf-8", errors="replace"))
        _add(r.get("code") for r in (pead if isinstance(pead, list) else []))
    except Exception:  # noqa: BLE001
        pass

    # --- "bake everything": also bake every stock reachable from the Fair Value,
    # Special Situations and FII-sector screens, so clicking ANY of them opens on
    # the static GitHub Pages site (no local server needed).

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
    done, fail, skipped = 0, 0, 0
    today = time.strftime("%Y-%m-%d")
    _bake_start = time.time()

    if args.ratios_only:
        return _backfill_ratios(out, codes, today, args.max_minutes)
    for i, code in enumerate(codes, 1):
        if args.max_minutes and (time.time() - _bake_start) > args.max_minutes * 60:
            print(f'[fund] time budget {args.max_minutes:.0f}min reached at {i}/{len(codes)} - committing what is baked'); break
        bf = out / f"{code}.json"
        if args.skip_any and bf.exists():
            skipped += 1; continue
        if args.skip_existing and bf.exists() and _baked_on(bf) == today:
            skipped += 1; continue
        try:
            fund = co.fundamentals(code, sid)
            if "error" in fund:
                fail += 1; continue
            ratios = _with_upstox_health(fund, code)
            price = ph.price_analytics(code, overview=fund.get("overview"))
            sec = sl.sector_for(code, fund.get("name"))
            sigv = sg.technofunda_signal(fund, price, sec)
            bundle = {"generated_at": today, "fundamental": fund,
                      "prices": price, "signal": sigv}
            if ratios:
                bundle["upstox_ratios"] = ratios
            _atomic(out / f"{code}.json", json.dumps(bundle, separators=(",", ":")))
            done += 1
            if i <= 8 or i % 25 == 0:
                print(f"  [{i}/{len(codes)}] baked {fund.get('name','')[:30]} ({code})")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(codes)}] FAIL {code}: {type(e).__name__}")
        time.sleep(0.15)

    # index.json lists every bundle that exists on disk, not just this run's
    # visits — a --max-minutes break must never shrink the site's search index.
    baked = sorted(p.stem for p in out.glob("*.json") if p.name != "index.json")
    _atomic(out / "index.json", json.dumps(baked))
    print(f"[fund] baked {done}, skipped {skipped}, failed {fail}; index {len(baked)} -> {out}")


if __name__ == "__main__":
    main()
