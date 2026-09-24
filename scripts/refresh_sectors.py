"""
Sector Headwind/Tailwind publisher.

Classifies the whole market, scores each sector + the full market, and writes:
  docs/data/sector_tailwind.json   - current scores + per-sector detail
  docs/data/sector_history.json    - daily score history (for the trend line)
  docs/data/sector_stocks.json     - per-sector constituents (drill-down)
  .cache/universe.json             - classified universe (reused by IV ranking)

Two sources, one board. The /market/ sector pages are read live, and the
bundles under docs/data/fundamental hold the same fundamentals for every
company we cover. In the default `auto` mode the pages lead and the bundles
fill whatever they missed. So a scrape that fails outright -- as it did for
three weeks in September 2026, when a renamed column header read as an empty
market -- now leaves a board built from bundles instead of a frozen one, and
the IV, return-map and fair-value boards that read .cache/universe.json keep
running. The step still exits non-zero when the pages gave nothing, so the
failure is seen rather than papered over.

    python scripts/refresh_sectors.py
    python scripts/refresh_sectors.py --source bundles          # no network
    python scripts/refresh_sectors.py --only IN0101 IN0702      # quick look, writes nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import boarduniverse as BU   # noqa: E402
from earnings_intel.data import sectors as S          # noqa: E402
from earnings_intel.data import sectorscore as SC     # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION = ROOT / "screener_session.json"

# What sector_score reads. pos_52w is breadth; it was null board-wide for
# weeks without anything noticing, which is the failure this list exists for.
SECTOR_INPUTS = ("profit_var", "sales_var", "roce", "pos_52w", "fii_chg")


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, path)


def membership(path: Path) -> dict:
    """code -> sector from the last published drill-down.

    That file is the sector pages' own grouping from the last good run, so a
    board rebuilt from bundles keeps every company in the sector it was in
    yesterday instead of re-deriving it from a coarser classification.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    sectors = data.get("sectors") if isinstance(data, dict) else None
    for sector, rows in (sectors or {}).items():
        if sector not in S.CODE_OF:
            continue
        for r in rows or []:
            code = r.get("code") if isinstance(r, dict) else None
            if code:
                out.setdefault(str(code).upper(), sector)
    return out


def bundles(fdir: Path):
    """(code, bundle) for every readable bundle.

    A generator on purpose: ~5,700 bundles held in memory at once is a few
    hundred MB for no reason, and a single pass serves every use of them.
    """
    if not fdir.is_dir():
        return
    for p in sorted(fdir.glob("*.json")):
        if p.stem == "index":
            continue
        try:
            yield p.stem, json.loads(p.read_bytes().rstrip(b"\x00").rstrip())
        except Exception:  # noqa: BLE001
            continue


def build_universe(screen, pairs, member, only=None, fill=True, min_quarter=None):
    """(rows, stats): the pages' rows first, then every bundle they missed.

    `fill=False` keeps the bundles to enrichment only (--source screen).
    `min_quarter` blanks growth from quarter tables that ended before it.
    """
    technicals: dict = {}
    full_names: dict = {}

    def walk():
        # The screen carries no price history, so every row -- the pages' as
        # well as the bundles' -- takes its strength rating and 52-week
        # position from the bundle we hold. Costs no network.
        for code, bundle in pairs:
            extra = BU.enrichment_of(bundle)
            if extra:
                technicals[code] = extra
            f = bundle.get("fundamental") if isinstance(bundle, dict) else None
            if isinstance(f, dict) and f.get("name"):
                full_names[str(code)] = f["name"]
            yield code, bundle
    held, unplaced = BU.sector_rows_from_bundles(walk(), member, min_quarter=min_quarter)
    for r in screen:
        r["src"] = "screen"
    if not fill:
        held = []
    if only is not None:
        held = [r for r in held if r["sector_code"] in only or r["sector"] in only]
    rows = BU.enrich(BU.merge(screen, held), technicals)
    # Keyed by each code's full bundle name: the pages abbreviate, the bundles
    # do not, and a dual listing must match across the two.
    rows = BU.dedupe_listings((r for r in rows if r.get("sector") in S.CODE_OF),
                              name_of=lambda r: full_names.get(str(r.get("code"))))
    n_screen = sum(1 for r in rows if r.get("src") == "screen")
    return rows, {"screen_rows": n_screen, "bundle_rows": len(rows) - n_screen,
                  "unplaced": unplaced}


def screen_gaps(rows, only=None) -> list:
    """Sectors the pages returned nobody for. Bundles fill them silently, so
    without this a scrape that failed for 21 of 22 sectors looked healthy."""
    wanted = [n for c, n in S.SECTORS.items() if not only or c in only or n in only]
    covered = {r.get("sector") for r in rows if r.get("src") == "screen"}
    return [n for n in wanted if n not in covered]


def problems_with(rows, min_rows: int) -> list:
    """Reasons not to publish a full-market board. Empty means publish."""
    out = []
    if len(rows) < min_rows:
        out.append(f"{len(rows)} companies, under the {min_rows} floor")
    missing = sorted(set(S.SECTORS.values()) - {r.get("sector") for r in rows})
    if missing:
        out.append(f"no companies in {len(missing)} sector(s): {', '.join(missing)}")
    dead = BU.dead_inputs(rows, fields=SECTOR_INPUTS)
    if dead:
        out.append("dead input(s): " + "; ".join(f"{k} {v}" for k, v in sorted(dead.items())))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sector headwind/tailwind")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--only", nargs="*", default=None,
                    help="sector codes e.g. IN0101 IN0702 (prints, writes nothing)")
    ap.add_argument("--source", choices=("auto", "screen", "bundles"), default="auto",
                    help="auto: live pages, bundles fill the gaps (default); "
                         "screen: pages only; bundles: no network")
    ap.add_argument("--min-rows", type=int, default=4000,
                    help="refuse to publish a full-market board with fewer companies")
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--cache", default=str(ROOT / ".cache"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    only = set(args.only) if args.only else None

    screen, scrape_error = [], None
    if args.source != "bundles":
        try:
            screen = S.fetch_sectors(_sid(), max_pages=args.max_pages, only=args.only)
        except Exception as e:  # noqa: BLE001  -- a layout change raises here
            scrape_error = f"{type(e).__name__}: {e}"
            print(f"[sectors] sector pages unreadable - {scrape_error}", file=sys.stderr)
        if not screen and not scrape_error:
            scrape_error = "the sector pages returned no companies"

    # Growth counts as current only from a quarter ending within ~9 months:
    # the latest reportable quarter plus one still being filed.
    now = datetime.now(IST)
    rows, stats = build_universe(screen, bundles(Path(args.fundamental)),
                                 membership(out / "sector_stocks.json"), only,
                                 fill=args.source != "screen",
                                 min_quarter=now.year * 12 + now.month - 9)
    n_screen = stats["screen_rows"]
    gaps = screen_gaps(rows, only) if args.source != "bundles" else []
    if not n_screen:
        mode = "bundles"
    elif gaps:
        mode = "partial"
        print(f"[sectors] the pages returned nobody for {len(gaps)} sector(s), "
              f"filled from bundles: {', '.join(gaps)}", file=sys.stderr)
    else:
        mode = "screen" if not stats["bundle_rows"] else "screen+bundles"
    screen_ok = bool(n_screen) and not gaps
    print(f"[sectors] universe: {n_screen} from the sector pages + {stats['bundle_rows']} "
          f"from bundles = {len(rows)} ({stats['unplaced']} scorable bundles have no sector)")
    _rated = sum(1 for r in rows if r.get("rs_rating") is not None)
    print(f"[sectors] relative strength on {_rated}/{len(rows)} constituents")

    if not only:
        problems = problems_with(rows, args.min_rows)
        if problems:
            for p in problems:
                print(f"[sectors] ABORT - {p}", file=sys.stderr)
            print("[sectors] nothing written; the last good board stays up.", file=sys.stderr)
            return 1

    res = SC.market_tailwind(rows)
    res["generated_at_ist"] = now.strftime("%Y-%m-%d %H:%M:%S IST")
    # Which source built this board, in words a page can show. No error text:
    # an exception message can carry a vendor URL, and this file is public.
    res["source"] = {"mode": mode, "screen_ok": screen_ok, **stats}
    if gaps and n_screen:
        res["source"]["filled_sectors"] = gaps

    # blend real per-sector FII fortnight net flow (from refresh_fii -> fii.json)
    try:
        _fii = json.loads((out / "fii.json").read_text()).get("sectors") or []
        SC.blend_fii(res, _fii)
        _nb = sum(1 for s in res["sectors"] if "fii_fortnight" in s)
        if _nb:
            print(f"[sectors] blended real FII flow into {_nb} sectors")
    except Exception:  # noqa: BLE001  (fii.json optional)
        pass

    fm = res["full_market"]
    print(f"[sectors] {fm['companies']} companies, {fm['sectors']} sectors | "
          f"FULL MARKET {fm['score']:+} {fm['signal']} | source {mode} | {now:%H:%M:%S IST}")
    for s in res["sectors"]:
        print(f"   {s['score']:+6}  {s['signal']:<8} {s['sector'][:24]:<24} "
              f"(n={s['n']}, breadth {s['breadth_pct']}%, NPg {s['median_profit_var']})")

    if only:
        print("[sectors] --only: partial market, nothing written")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    _atomic(out / "sector_tailwind.json", json.dumps(res, separators=(",", ":")))

    # daily history for the trend line (replace today's, cap 400 days)
    hp = out / "sector_history.json"
    try:
        hist = json.loads(hp.read_text())
    except Exception:  # noqa: BLE001
        hist = []
    today = now.strftime("%Y-%m-%d")
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "full": res["full_market"]["score"],
                 "sectors": {s["sector"]: s["score"] for s in res["sectors"]},
                 "source": mode})
    hist = hist[-400:]
    _atomic(hp, json.dumps(hist, separators=(",", ":")))

    # per-sector constituents with a per-stock signal (drill-down)
    bysec = {}
    for r in rows:
        sig = SC.stock_signal(r)
        bysec.setdefault(r.get("sector", "Unknown"), []).append({
            "code": r["code"], "name": r.get("name"), "signal": sig["signal"],
            "sscore": sig["sscore"], "result": sig["result"],
            "np": r.get("profit_var"), "sales": r.get("sales_var"), "pos": sig["pos"],
            "ltp": r.get("cmp"), "mcap": r.get("mcap"), "pe": r.get("pe")})
    for k in bysec:
        bysec[k].sort(key=lambda x: x["sscore"], reverse=True)
    _atomic(out / "sector_stocks.json", json.dumps(
        {"generated_at_ist": res["generated_at_ist"], "sectors": bysec}, separators=(",", ":")))

    # cache classified universe for the IV ranking tools (gitignored)
    cache = Path(args.cache); cache.mkdir(parents=True, exist_ok=True)
    _atomic(cache / "universe.json", json.dumps(rows, separators=(",", ":")))

    if args.source != "bundles" and not screen_ok:
        # Published, and still red. A green run on a fallback board is how the
        # last outage stayed invisible for three weeks.
        what = (f"board built from bundles only ({len(rows)} companies); {scrape_error}"
                if not n_screen else f"{len(gaps)} sector(s) filled from bundles")
        print(f"::warning title=Sector pages unreadable::{what}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
