"""
One refresh cycle for the scanX dashboard.

Logs into Screener.in and scrapes /results/latest/, scores every company with
the fundamental PEAD screen, and writes the dashboard data files:

    docs/data/pead.json   - full scored list (the dashboard fetches this)
    docs/data/pead.csv    - same, Excel-friendly
    docs/data/meta.json   - generated_at, source, counts

Auth resolution order (so it logs in rarely, not every cycle):
    1. SCREENER_SESSIONID env cookie (manual override)
    2. cached session from a previous login (screener_session.json, git-ignored)
    3. SCREENER_EMAIL + SCREENER_PASSWORD -> auto-login, cache the session
    4. else fall back to the bundled real sample so the dashboard still works.

Live LTP + % change come from Dhan (best-effort) when a token is present.

    python scripts/refresh_scanx.py
    python scripts/refresh_scanx.py --sample   # force the bundled sample
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel import screener_screen as ss   # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
_SESSION_CACHE = ROOT / "screener_session.json"


def load_sample() -> list:
    data = json.loads((ROOT / "earnings_intel" / "data" / "sample_screener.json").read_text())
    return [ss.from_metrics(d) for d in data]


def _screener_client():
    """Return (authenticated client, source label) or (None, None)."""
    from earnings_intel.data.screener import ScreenerClient

    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION_CACHE.exists():
        try:
            sid = json.loads(_SESSION_CACHE.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    if sid:
        return ScreenerClient(session_id=sid), "screener.in (saved session)"

    email = os.environ.get("SCREENER_EMAIL")
    pw = os.environ.get("SCREENER_PASSWORD")
    if email and pw:
        c = ScreenerClient()
        if c.login(email, pw):
            new = c.session_id()
            if new:
                try:
                    _SESSION_CACHE.write_text(json.dumps({"sessionid": new}))
                except Exception:  # noqa: BLE001
                    pass
            return c, "screener.in (auto-login)"
    return None, None


def scrape_live(client, max_pages: int) -> list:
    dicts = client.fetch_latest_results(max_pages=max_pages)   # ~25 companies/page
    return [ss.from_metrics(d) for d in dicts]


# --------------------------------------------------------------- live prices (Dhan)
def _dhan_provider():
    """DhanProvider if a Dhan client id + access token is available, else None.

    Reads env DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN first, then dhan_token.json
    (written by scripts/dhan_login.py). Never touches the PIN/secret/TOTP.
    """
    cid = os.environ.get("DHAN_CLIENT_ID")
    tok = os.environ.get("DHAN_ACCESS_TOKEN")
    tf = ROOT / "dhan_token.json"
    if (not cid or not tok) and tf.exists():
        try:
            j = json.loads(tf.read_text())
            cid = cid or j.get("client_id")
            tok = tok or j.get("access_token")
        except Exception:  # noqa: BLE001
            pass
    if not cid or not tok:
        return None
    try:
        from earnings_intel.data.dhan_provider import DhanProvider
        cache = ROOT / ".cache" / "dhan_master.json"
        return DhanProvider(cid, tok, cache_path=str(cache))
    except Exception:  # noqa: BLE001
        return None


def _yf_fill(rows: list) -> int:
    """Delayed (free) LTP via yfinance when no live broker feed is available.
    Fills r['ltp'] + r['pct_change']; returns how many got a price."""
    try:
        import yfinance as yf
        import logging as _lg
        _lg.getLogger("yfinance").setLevel(_lg.CRITICAL)   # mute "possibly delisted" spam
    except ImportError:
        return 0
    tmap = {}
    for r in rows:
        c = str(r.get("code", "")).strip().upper()
        if c:
            tmap[c] = f"{c}.BO" if c.isdigit() else f"{c}.NS"
    tickers = sorted(set(tmap.values()))
    if not tickers:
        return 0
    try:
        data = yf.download(tickers, period="2d", group_by="ticker",
                           progress=False, threads=True, raise_errors=False)
    except Exception:  # noqa: BLE001
        return 0
    hits = 0
    for r in rows:
        c = str(r.get("code", "")).strip().upper()
        tk = tmap.get(c)
        if not tk:
            continue
        try:
            df = data[tk] if len(tickers) > 1 else data
            closes = df["Close"].dropna()
            if len(closes) >= 1:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
                r["ltp"] = round(last, 2)
                r["pct_change"] = round((last - prev) / prev * 100, 2) if prev else None
                hits += 1
        except Exception:  # noqa: BLE001
            continue
    return hits


def enrich_prices(rows: list, provider=None) -> str:
    """Attach live `ltp` + `pct_change` (vs prev close) to each row, best-effort.

    Returns a short status string for meta. Rows always get the two keys so the
    dashboard JSON shape is stable (None -> shown as a dash).
    """
    for r in rows:
        r.setdefault("ltp", None)
        r.setdefault("pct_change", None)
    if provider is None and os.environ.get("SCANX_NO_PRICES"):
        return "prices skipped (SCANX_NO_PRICES)"
    prov = provider
    if prov is None and not os.environ.get("SCANX_NO_DHAN"):
        prov = _dhan_provider()
    if prov is None:                       # Dhan removed -> free NSE/BSE feed
        try:
            from earnings_intel.data.nsequotes import provider as _nse_provider
            prov = _nse_provider()
        except Exception:  # noqa: BLE001
            prov = None
    if prov is None:
        n = _yf_fill(rows)
        return f"yfinance delayed {n}/{len(rows)}"

    # screener code -> candidate quote keys. Numeric code = BSE scrip code.
    cand: dict = {}
    for r in rows:
        code = str(r.get("code", "")).strip().upper()
        if not code:
            continue
        cand[code] = [f"BSE:{code}"] if code.isdigit() else [f"NSE:{code}", f"BSE:{code}"]

    keys = sorted({k for ks in cand.values() for k in ks})
    if not keys:
        return "no resolvable symbols"
    try:
        quotes = prov.get_quotes(keys)
    except Exception as e:  # noqa: BLE001
        return f"Dhan quote error: {type(e).__name__}"

    hits = 0
    for r in rows:
        code = str(r.get("code", "")).strip().upper()
        q = next((quotes[k] for k in cand.get(code, [])
                  if quotes.get(k) and quotes[k].get("last_price") is not None), None)
        if not q:
            continue
        last = q.get("last_price")
        nc = q.get("net_change")
        ohlc_close = (q.get("ohlc") or {}).get("close")
        # prev close from Dhan's day-change (works after hours too); fall back to ohlc close
        prev = (last - nc) if (last is not None and nc is not None) else ohlc_close
        pct = round((last - prev) / prev * 100, 2) if (last is not None and prev) else None
        r["ltp"] = round(last, 2) if last is not None else None
        r["pct_change"] = pct
        hits += 1
    if hits:
        return f"{type(prov).__name__} LTP {hits}/{len(rows)}"
    err = getattr(prov, "last_error", None)
    n = _yf_fill(rows)   # Dhan gave nothing -> show delayed prices, never blank
    reason = err or "no quotes (enable Dhan Data API / use a self access token)"
    return f"yfinance delayed {n}/{len(rows)} — Dhan: {str(reason)[:80]}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh scanx dashboard data")
    ap.add_argument("--pages", type=int, default=3,
                    help="results pages to scrape (~25 companies each)")
    ap.add_argument("--sample", action="store_true", help="force bundled sample data")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.sample:
        stocks, source = load_sample(), "sample (forced)"
    else:
        client, label = _screener_client()
        if client is not None:
            try:
                stocks = scrape_live(client, args.pages)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] live scrape failed ({e}); using sample")
                stocks = []
            source = label if stocks else f"sample ({label} returned nothing)"
            if not stocks:
                stocks = load_sample()
        else:
            stocks = load_sample()
            source = "sample (no Screener login - set SCREENER_EMAIL/PASSWORD)"

    stocks.sort(key=lambda s: s.pead_score, reverse=True)
    rows = [s.to_dict() for s in stocks]

    # never let the bundled SAMPLE overwrite a real crawl on disk/GitHub Pages:
    # if Screener is unreachable this cycle, keep the previous good board.
    if source.startswith("sample") and not args.sample:
        prev = out / "pead.json"
        try:
            if prev.exists() and len(json.loads(prev.read_text(encoding="utf-8"))) > len(rows):
                print(f"[scanx] Screener unreachable — keeping previous real board "
                      f"(skipping sample overwrite) | {datetime.now(IST):%H:%M:%S IST}")
                return
        except Exception:  # noqa: BLE001
            pass

    for r in rows:
        r["ltp"] = r.get("last_price")   # Screener price baseline (Dhan overwrites if live)
    price_note = enrich_prices(rows)   # live LTP + % change via Dhan (best-effort)

    _atomic(out / "pead.json", json.dumps(rows, indent=2))
    if rows:
        with (out / "pead.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    now = datetime.now(IST)
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "source": source,
        "prices": price_note,
        "total": len(rows),
        "high": sum(1 for r in rows if r["pead_category"] == "HIGH"),
        "growth_gate": sum(1 for r in rows if r["growth_gate"]),
    }
    _atomic(out / "meta.json", json.dumps(meta, indent=2))

    print(f"[scanx] {len(rows)} companies | source: {source} | {price_note} | {now:%H:%M:%S IST}")
    for r in rows[:6]:
        print(f"   {r['pead_score']:>5}  {r['pead_category']:<6} {r['name'][:26]:<26} "
              f"SalesYoY {r['sales_yoy']}  NPYoY {r['np_yoy']}")


if __name__ == "__main__":
    main()
