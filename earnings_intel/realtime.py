"""
Realtime intraday engine (default window 09:15-15:30 IST).

Each cycle it pulls live quotes for the tradable NSE+BSE universe via the Kite
SDK, computes intraday metrics (% change, position vs VWAP, position in the day
range, volume), merges the scanX PEAD fundamental scores, ranks the movers, and
emits alerts for the ones that matter most (PEAD-strong names that are moving).

Screening + alerts only - it never places orders. A built-in synthetic provider
lets the whole loop run and be tested without a Kite login.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .alert_sink import AlertSink, SeenStore

log = logging.getLogger("technofunda.realtime")


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class SyntheticQuoteProvider:
    """Stand-in for KiteProvider so realtime runs without a login (testing)."""

    def __init__(self, pead_rows: list[dict]):
        self._rows = pead_rows or []
        self._rng = random.Random(7)

    def list_instruments(self, exchanges=("NSE", "BSE"), eq_only=True):
        return [{"exchange": "NSE", "symbol": r["code"], "token": 0,
                 "name": r.get("name", "")} for r in self._rows]

    def get_quotes(self, keys, batch=400, pause=0.0):
        out = {}
        for k in keys:
            prev = self._rng.uniform(50, 2000)
            chg = self._rng.uniform(-0.06, 0.09)
            last = round(prev * (1 + chg), 2)
            hi = round(max(last, prev) * (1 + abs(self._rng.uniform(0, 0.03))), 2)
            lo = round(min(last, prev) * (1 - abs(self._rng.uniform(0, 0.03))), 2)
            vwap = round((hi + lo + last) / 3, 2)
            out[k] = {"last_price": last, "average_price": vwap,
                      "volume": self._rng.randint(50_000, 5_000_000),
                      "net_change": round(last - prev, 2),
                      "ohlc": {"open": prev, "high": hi, "low": lo, "close": prev}}
        return out


class RealtimeEngine:
    def __init__(self, alerts_dir: Path, provider=None,
                 pead_path: Optional[Path] = None, sink: Optional[AlertSink] = None,
                 out_path: Optional[Path] = None):
        self.alerts_dir = Path(alerts_dir)
        self.sink = sink or AlertSink(self.alerts_dir)
        self.pead = self._load_pead(pead_path)
        self.provider = provider or SyntheticQuoteProvider(list(self.pead.values()))
        self.out_path = Path(out_path) if out_path else None
        self.seen = SeenStore(self.alerts_dir / "intraday_seen.json")
        self._universe = None

    @staticmethod
    def _load_pead(path) -> dict:
        if not path:
            return {}
        try:
            rows = json.loads(Path(path).read_text())
            return {str(r["code"]).upper(): r for r in rows}
        except Exception:  # noqa: BLE001
            return {}

    def _universe_keys(self):
        if self._universe is None:
            self._universe = self.provider.list_instruments()
        return [(u, f"{u['exchange']}:{u['symbol']}") for u in self._universe]

    @staticmethod
    def _now_min() -> int:
        n = datetime.now()
        return n.hour * 60 + n.minute

    def _metrics(self, u: dict, q: dict) -> Optional[dict]:
        try:
            last = float(q["last_price"])
            ohlc = q.get("ohlc") or {}
            prev = float(ohlc.get("close") or last)
            chg = float(q.get("net_change", last - prev))
            pct = (chg / prev * 100) if prev else 0.0
            vwap = q.get("average_price")
            hi, lo = float(ohlc.get("high", last)), float(ohlc.get("low", last))
            rng_pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
            pead = self.pead.get(str(u["symbol"]).upper())
            return {
                "symbol": u["symbol"], "exchange": u["exchange"],
                "last": round(last, 2), "pct_change": round(pct, 2),
                "open": round(float(ohlc.get("open")), 2) if ohlc.get("open") not in (None, "") else None,
                "high": round(hi, 2), "low": round(lo, 2), "prev_close": round(prev, 2),
                "vwap_pos": round((last / vwap - 1) * 100, 2) if vwap else None,
                "range_pos": round(rng_pos, 0),
                "volume": int(q.get("volume", 0) or 0),
                "pead_score": (pead or {}).get("pead_score"),
                "pead_category": (pead or {}).get("pead_category"),
            }
        except Exception:  # noqa: BLE001
            return None

    def screen_once(self, top_n: int = 25, min_abs_move: float = 3.0) -> int:
        uk = self._universe_keys()
        quotes = self.provider.get_quotes([k for _, k in uk])
        rows = []
        for u, k in uk:
            q = quotes.get(k)
            if q:
                m = self._metrics(u, q)
                if m:
                    rows.append(m)
        # rank: PEAD conviction + magnitude of the intraday move
        rows.sort(key=lambda m: ((m["pead_score"] or 0) / 100.0)
                  + abs(m["pct_change"]) / 10.0, reverse=True)

        if self.out_path:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self.out_path.write_text(json.dumps({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "rows": rows[:100]}, indent=1))

        alerts = 0
        for m in rows:
            if abs(m["pct_change"]) < min_abs_move:
                continue
            tag = f"{m['symbol']}:{datetime.now():%Y%m%d}:{'U' if m['pct_change'] > 0 else 'D'}"
            if self.seen.has(tag):
                continue
            pead = (f"PEAD {m['pead_score']:.0f} ({m['pead_category']})"
                    if m["pead_score"] is not None else "no PEAD score")
            body = (f"INTRADAY MOVER  [{m['exchange']}]\n"
                    f"Stock: {m['symbol']}   {m['pct_change']:+.1f}%  (LTP {m['last']})\n"
                    f"{pead}\n"
                    f"vs VWAP: {m['vwap_pos']}%   day-range pos: {m['range_pos']:.0f}%   "
                    f"vol: {m['volume']:,}")
            self.sink.emit(body, {"source": m["exchange"], "symbol": m["symbol"],
                                  "kind": "intraday", "action": "WATCH",
                                  "score": m["pead_score"], "headline":
                                  f"{m['pct_change']:+.1f}% intraday", "url": ""})
            self.seen.add(tag)
            alerts += 1
            if alerts >= top_n:
                break
        self.seen.save()
        self.sink.info(f"intraday cycle: {len(rows)} quotes, {alerts} alerts")
        return alerts

    def run(self, start="09:15", end="15:30", poll=60, run_once=False,
            top_n=25, min_abs_move=3.0):
        self.sink.info(f"Realtime engine up | window {start}-{end} IST | poll {poll}s "
                       f"| universe via {type(self.provider).__name__}")
        while True:
            if not run_once:
                nm = self._now_min()
                if nm > _hhmm(end):
                    self.sink.info("market window closed - exiting")
                    break
                if nm < _hhmm(start):
                    self.sink.info(f"before open ({start}); waiting...")
                    time.sleep(min(poll, 60))
                    continue
            try:
                self.screen_once(top_n=top_n, min_abs_move=min_abs_move)
            except Exception as e:  # noqa: BLE001
                self.sink.info(f"cycle error (continuing): {type(e).__name__}: {e}")
            if run_once:
                break
            time.sleep(poll)
