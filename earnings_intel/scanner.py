"""
Live scanner service (Agent 1 + orchestration, continuous mode).

Runs a polling loop inside a daily time window (default 09:00-23:55). Each cycle:
  1. Pull fresh NSE + BSE filings (results + corporate actions).
  2. De-duplicate against what we've already alerted (persisted seen.json).
  3. For each new relevant filing, add a technical/volume reaction read from Kite
     (if a token is available), then emit an alert to log + CSV + Telegram.

It is **screening + alerts only** — it never places orders.

Full fundamental SUE/PEAD scoring on a *live* filing needs the PDF/XBRL
extraction agent (Phase 1). Until that is wired, live alerts carry the event +
the price/volume reaction; the full scorer is exercised end-to-end in demo mode
(SampleProvider) and in the backtester.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import Settings, DEFAULTS
from .alert_sink import AlertSink, SeenStore
from .engines.features import compute_features
from .engines.technical import score_technical

log = logging.getLogger("technofunda.scanner")


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class LiveScanner:
    def __init__(
        self,
        alerts_dir: Path,
        settings: Settings = DEFAULTS,
        equity: float = 1_000_000.0,
        kite_provider=None,
        feed=None,
        sink: Optional[AlertSink] = None,
    ):
        self.alerts_dir = Path(alerts_dir)
        self.settings = settings
        self.equity = equity
        self.kite = kite_provider
        self.feed = feed
        self.sink = sink or AlertSink(self.alerts_dir)
        self.seen = SeenStore(self.alerts_dir / "seen.json")

    # ----------------------------------------------------------- time window
    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    def _window_state(self, start: str, end: str) -> str:
        now_min = self._now().hour * 60 + self._now().minute
        if now_min < _hhmm_to_min(start):
            return "before"
        if now_min > _hhmm_to_min(end):
            return "after"
        return "open"

    # --------------------------------------------------------- technical read
    def _technical_read(self, symbol: str) -> Optional[dict]:
        if not self.kite:
            return None
        try:
            to = date.today()
            frm = to - timedelta(days=300)
            bars = self.kite.get_history(symbol, frm, to)
            if len(bars) < 60:
                return None
            f = compute_features(bars)
            return {
                "tech_score": round(score_technical(f), 0),
                "rvol": round(f.rvol, 2),
                "close": round(f.close, 2),
                "breakout": f.breakout_20,
                "trend_up": f.trend_up,
                "rsi": round(f.rsi, 0),
            }
        except Exception as e:  # noqa: BLE001
            log.warning("technical read failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------ formatting
    @staticmethod
    def _format_event_alert(a, tech: Optional[dict]) -> str:
        head = "RESULTS FILED" if a.kind == "results" else "CORPORATE ACTION"
        lines = [
            f"{head}  [{a.source}]",
            f"Stock: {a.symbol}  ({a.company})",
            f"Filing: {a.headline}",
            f"Category: {a.category}",
            f"Time: {a.dt or 'n/a'}",
        ]
        if tech:
            flags = []
            if tech["breakout"]:
                flags.append("20d breakout")
            if tech["trend_up"]:
                flags.append("uptrend (EMA stack)")
            lines += [
                "",
                f"Price reaction: close {tech['close']}, RVOL {tech['rvol']}x, "
                f"RSI {tech['rsi']:.0f}",
                f"Technical score: {tech['tech_score']:.0f}/100"
                + (f"  ({', '.join(flags)})" if flags else ""),
            ]
        else:
            lines += ["", "(Connect Kite for the live price/volume reaction read.)"]
        if a.url:
            lines += ["", f"Doc: {a.url}"]
        return "\n".join(lines)

    # --------------------------------------------------------------- screening
    def screen_once_live(self) -> int:
        if not self.feed:
            self.sink.info("no live feed configured; nothing to screen")
            return 0
        items = self.feed.fetch_all()
        new = [a for a in items if not self.seen.has(a.uid)]
        relevant = [a for a in new if a.kind in ("results", "corporate_action")]

        for a in relevant:
            tech = self._technical_read(a.symbol)
            body = self._format_event_alert(a, tech)
            self.sink.emit(body, {
                "source": a.source, "symbol": a.symbol, "kind": a.kind,
                "action": "WATCH", "score": (tech or {}).get("tech_score", ""),
                "headline": a.headline, "url": a.url})

        for a in new:
            self.seen.add(a.uid)
        self.seen.save()
        self.sink.info(f"live cycle: {len(items)} filings, {len(new)} new, "
                       f"{len(relevant)} relevant alerts")
        return len(relevant)

    def screen_once_demo(self, provider) -> int:
        """Exercise the full pipeline on synthetic data (proves the loop)."""
        from .pipeline import Pipeline
        from .alerts import format_alert
        from .models import Action

        res = Pipeline(provider, self.settings).run(equity=self.equity)
        events = getattr(provider, "_events", {})
        n = 0
        for s in res.signals:
            if s.action in (Action.STRONG_BUY, Action.BUY,
                            Action.STRONG_SELL, Action.SELL):
                rep = events.get(s.symbol, [None])[-1] if events else None
                self.sink.emit(format_alert(s, rep), {
                    "source": "DEMO", "symbol": s.symbol, "kind": "signal",
                    "action": s.action.value, "score": s.composite_score,
                    "headline": "synthetic earnings signal", "url": ""})
                n += 1
        self.sink.info(f"demo cycle: {len(res.signals)} scored, {n} actionable alerts")
        return n

    # --------------------------------------------------------------- main loop
    def run(self, start: str = "09:00", end: str = "23:55", poll: int = 60,
            mode: str = "live", run_once: bool = False, demo_provider=None) -> None:
        self.sink.info(f"Technofunda scanner up | mode={mode} | window {start}-{end} "
                       f"IST | poll {poll}s | kite={'on' if self.kite else 'off'}")
        while True:
            state = "open" if run_once else self._window_state(start, end)
            if state == "after":
                self.sink.info("trading window closed for the day — exiting")
                break
            if state == "before":
                self.sink.info(f"before window ({start}); waiting…")
                time.sleep(min(poll, 60))
                continue

            try:
                if mode == "demo":
                    self.screen_once_demo(demo_provider)
                else:
                    self.screen_once_live()
            except Exception as e:  # noqa: BLE001
                self.sink.info(f"cycle error (continuing): {type(e).__name__}: {e}")

            if run_once:
                break
            time.sleep(poll)
