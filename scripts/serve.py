"""
Local API + static server for the scanX dashboard.

Serves docs/ as static files (so the dashboard works exactly like GitHub Pages)
AND adds two live endpoints the static Fundamental Screener tab calls:

    GET /api/search?q=<name or BSE code>   -> [{name, code, url}]
    GET /api/fundamental?code=<code>        -> {overview, growth, quarters, ...}

Both proxy Screener using the cached login session (screener_session.json or
SCREENER_SESSIONID), so GitHub Pages stays a pure static site while the local
scanX.bat run gets live, on-demand company lookups.

    python scripts/serve.py            # http://localhost:8777
    python scripts/serve.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import company as co       # noqa: E402
from earnings_intel.data import pricehist as ph     # noqa: E402
from earnings_intel.data import signal as sg        # noqa: E402
from earnings_intel.data import sectorlookup as sl  # noqa: E402

DOCS = ROOT / "docs"
_SESSION_CACHE = ROOT / "screener_session.json"


def _session_id():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION_CACHE.exists():
        try:
            sid = json.loads(_SESSION_CACHE.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


_Q = {"data": {}, "cooldown_until": 0.0, "last_err": None}   # shared quote cache + breaker
_QUOTE_TTL = 3.0          # serve a code's quote from cache for this many seconds
_QUOTE_COOLDOWN = 120.0   # after a 401/429, stop calling Dhan for this long (avoid a block)


def _pick_provider():
    """Dhan dependency removed by request — quotes come from the free NSE/BSE
    delayed provider only (~1-3 min lag, no broker account, no daily token)."""
    from earnings_intel.data import nsequotes as nq
    return nq.provider()


def _live_quotes(codes):
    """Live Dhan LTP + %chg with a shared cache + circuit breaker.

    Many page-pollers share one Dhan call per TTL; a 401/429 trips a cooldown so
    we stop hitting Dhan (which threatens to block the account on too many calls).
    """
    import time
    now = time.time()
    d = _Q["data"]

    # circuit breaker: cooling down -> serve cache, never call Dhan
    if now < _Q["cooldown_until"]:
        out = {c: {"ltp": d[c]["ltp"], "pct": d[c]["pct"]} for c in codes if c in d}
        out["_error"] = _Q["last_err"] or "live feed paused"
        out["_cooldown_s"] = int(_Q["cooldown_until"] - now)
        return out

    need = [c for c in codes if not (c in d and now - d[c]["ts"] < _QUOTE_TTL)]
    prov = None
    if need:
        prov = _pick_provider()
        if prov is None:
            return {"_error": "no Dhan token (live prices need a valid dhan_token.json)"}
        cand, keys = {}, set()
        for c in need:
            ks = [f"BSE:{c}"] if c.isdigit() else [f"NSE:{c}", f"BSE:{c}"]
            cand[c] = ks; keys.update(ks)
        try:
            q = prov.get_quotes(sorted(keys))
        except Exception as e:  # noqa: BLE001
            return {"_error": f"{type(e).__name__}: {e}"}
        err = getattr(prov, "last_error", None)
        if err and any(x in err for x in ("401", "429", "Authentication", "Too many")):
            _Q["cooldown_until"] = now + _QUOTE_COOLDOWN
            _Q["last_err"] = err
        for c in need:
            hit = next((q[k] for k in cand[c] if q.get(k) and q[k].get("last_price") is not None), None)
            if not hit:
                continue
            last = hit.get("last_price"); nc = hit.get("net_change")
            close = (hit.get("ohlc") or {}).get("close")
            prev = (last - nc) if (last is not None and nc is not None) else close
            pct = round((last - prev) / prev * 100, 2) if (last is not None and prev) else None
            d[c] = {"ltp": round(last, 2) if last is not None else None, "pct": pct, "ts": now}

    out = {c: {"ltp": d[c]["ltp"], "pct": d[c]["pct"]} for c in codes if c in d}
    if not out and _Q.get("last_err"):
        out["_error"] = _Q["last_err"]
    if not out and prov is not None:
        perr = getattr(prov, "last_error", None)
        if perr:
            out["_error"] = perr
    return out


_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class Handler(SimpleHTTPRequestHandler):
    def _host_ok(self) -> bool:
        """Reject requests whose Host header isn't loopback.

        The dashboard and its /api/* are served from the same loopback origin,
        so the browser never needs a cross-origin Host. Validating it blocks
        DNS-rebinding, where a malicious page resolves its own domain to
        127.0.0.1 and then scripts the local server through the victim's browser.
        """
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in _ALLOWED_HOSTS

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # No Access-Control-Allow-Origin: the API is consumed same-origin by the
        # dashboard this server hosts. A wildcard would let any website the user
        # visits read these endpoints (which proxy the authenticated Screener
        # session) cross-origin. Same-origin needs no CORS header at all.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass   # client navigated away / cancelled the poll - ignore

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._json({"error": "forbidden host"}, 403)
        u = urlparse(self.path)
        if u.path == "/api/search":
            q = (parse_qs(u.query).get("q") or [""])[0].strip()
            if not q:
                return self._json([])
            return self._json(co.search(q, _session_id()))
        if u.path == "/api/fundamental":
            code = (parse_qs(u.query).get("code") or [""])[0].strip()
            if not code:
                return self._json({"error": "code required"}, 400)
            return self._json(co.fundamentals(code, _session_id()))
        if u.path == "/api/prices":
            code = (parse_qs(u.query).get("code") or [""])[0].strip()
            if not code:
                return self._json({"error": "code required"}, 400)
            return self._json(ph.price_analytics(code))
        if u.path == "/api/signal":
            code = (parse_qs(u.query).get("code") or [""])[0].strip()
            if not code:
                return self._json({"error": "code required"}, 400)
            fund = co.fundamentals(code, _session_id())
            price = ph.price_analytics(code)
            sec = sl.sector_for(code, (fund.get("name") if isinstance(fund, dict) else None),
                                docs_dir=str(DOCS))
            return self._json(sg.technofunda_signal(fund, price, sec))
        if u.path == "/api/quote":
            codes = [c.strip().upper() for c in (parse_qs(u.query).get("codes") or [""])[0].split(",") if c.strip()]
            if not codes:
                return self._json({})
            return self._json(_live_quotes(codes[:500]))
        if u.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, fmt, *args):  # quieter logging
        if "/api/" in (self.path or ""):
            sys.stderr.write("[serve] %s\n" % (self.path,))


def main():
    ap = argparse.ArgumentParser(description="scanX local API + static server")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    handler = partial(Handler, directory=str(DOCS))
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    sid = _session_id()
    print(f"[serve] scanX on http://{args.host}:{args.port}  "
          f"(docs={DOCS}, screener session={'yes' if sid else 'NO'})")
    print(f"[serve]   live API: /api/search  /api/fundamental  /api/prices  /api/signal  /api/quote")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")


if __name__ == "__main__":
    main()
