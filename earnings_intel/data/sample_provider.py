"""
Synthetic data provider.

This is what lets the entire system run, be demoed and be backtested with **zero
credentials**. It is not random noise: it embeds a genuine-but-noisy
relationship between earnings surprise and forward price drift (the PEAD effect
the strategy targets), so the backtest produces realistic, non-trivial results
rather than either pure noise or a rigged 100% win rate.

Everything is seeded, so runs are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from ..models import (
    CorporateEvent,
    EarningsReport,
    Guidance,
    InstitutionalActivity,
    OptionsSnapshot,
    PriceBar,
    TranscriptData,
    ValuationInputs,
)
from .base import DataProvider

# (symbol, sector, base_price, base_revenue_cr, base_pat_cr, quality[-1..1])
_UNIVERSE = [
    ("INFY", "IT", 1500, 38000, 7000, 0.6),
    ("TCS", "IT", 3800, 60000, 11000, 0.7),
    ("RELIANCE", "Energy", 2900, 230000, 18000, 0.5),
    ("HDFCBANK", "Banking", 1650, 70000, 16000, 0.6),
    ("ICICIBANK", "Banking", 1100, 45000, 11000, 0.5),
    ("TATAMOTORS", "Auto", 950, 110000, 7000, 0.2),
    ("MARUTI", "Auto", 12500, 38000, 3700, 0.4),
    ("SUNPHARMA", "Pharma", 1550, 13000, 2600, 0.4),
    ("DRREDDY", "Pharma", 6200, 7500, 1400, 0.3),
    ("LT", "Capital Goods", 3600, 55000, 3500, 0.5),
    ("TITAN", "Consumer", 3500, 12000, 1000, 0.5),
    ("ASIANPAINT", "Consumer", 2800, 9000, 1300, 0.3),
]

_SECTOR_MEDIAN_PE = {
    "IT": 26, "Energy": 18, "Banking": 17, "Auto": 22,
    "Pharma": 30, "Capital Goods": 35, "Consumer": 55,
}

_POS_WORDS = [
    "strong demand", "robust order book", "margin expansion", "record quarter",
    "raising our guidance", "confident", "healthy pipeline", "accelerating",
    "broad-based growth", "operating leverage",
]
_NEG_WORDS = [
    "weak demand", "headwinds", "margin pressure", "softness",
    "cautious outlook", "lowering guidance", "challenging environment",
    "deceleration", "elevated costs", "subdued",
]


@dataclass
class _Extras:
    institutional: InstitutionalActivity
    transcript: TranscriptData
    options: OptionsSnapshot
    corporate: CorporateEvent
    valuation: ValuationInputs


class SampleProvider(DataProvider):
    def __init__(self, seed: int = 7, years: int = 6, end: Optional[date] = None):
        self.as_of = end or date(2026, 5, 29)
        start = date(self.as_of.year - years, self.as_of.month, self.as_of.day)
        self._days = [d.date() for d in pd.bdate_range(start, self.as_of)]
        self._history: dict[str, list[PriceBar]] = {}
        self._events: dict[str, list[EarningsReport]] = {}
        self._extras: dict[tuple[str, str], _Extras] = {}
        self._latest_extras: dict[str, _Extras] = {}
        self.sectors: dict[str, str] = {}

        for i, (sym, sector, px, rev, pat, quality) in enumerate(_UNIVERSE):
            self.sectors[sym] = sector
            self._build_stock(seed + i * 101, sym, sector, px, rev, pat, quality)

    # ------------------------------------------------------------- generation
    def _build_stock(self, seed, sym, sector, base_px, base_rev, base_pat, quality):
        rng = np.random.default_rng(seed)
        n = len(self._days)

        sigma = 0.020                       # ~32% annualised vol
        mu = 0.0003 + 0.0002 * quality      # mild upward drift for quality names
        rets = rng.normal(mu, sigma, n)
        vol = rng.lognormal(mean=np.log(1_000_000), sigma=0.4, size=n)

        # quarterly earnings roughly every 63 business days
        first = int(rng.integers(25, 45))
        event_idx = list(range(first, n - 25, 63))
        H = 20                              # PEAD drift horizon (trading days)

        events: list[EarningsReport] = []
        fy_q = [("Q1", "JUN"), ("Q2", "SEP"), ("Q3", "DEC"), ("Q4", "MAR")]

        for k, e in enumerate(event_idx):
            z = float(rng.normal(quality * 0.4, 1.0))   # standardized surprise
            z = float(np.clip(z, -3.0, 3.0))

            # --- inject PEAD into the price path -------------------------
            rets[e + 1] += 0.015 * z                     # announcement gap
            drift_daily = (0.030 * z) / H                # drift over the window
            rets[e + 2 : e + 2 + H] += drift_daily
            vspike = 1.5 + 0.7 * abs(z)
            vol[e + 1 : e + 4] *= vspike

            # --- build the earnings report ------------------------------
            yr = self._days[e].year
            qlabel, _ = fy_q[k % 4]
            period = f"{qlabel}FY{(yr % 100):02d}"

            eps_est = base_pat / 100.0       # toy EPS proxy
            eps_std = max(0.05 * abs(eps_est), 0.5)
            eps = eps_est + z * eps_std
            rev_actual = base_rev * (1 + 0.015 * z + rng.normal(0, 0.004))
            pat_actual = base_pat * (1 + 0.025 * z + rng.normal(0, 0.01))

            if z > 0.9:
                guidance = Guidance.RAISED if rng.random() < 0.8 else Guidance.MAINTAINED
            elif z < -0.9:
                guidance = Guidance.LOWERED if rng.random() < 0.7 else Guidance.MAINTAINED
            else:
                guidance = Guidance.MAINTAINED

            report = EarningsReport(
                symbol=sym,
                period=period,
                report_datetime=pd.Timestamp(self._days[e]).to_pydatetime(),
                revenue=rev_actual,
                pat=pat_actual,
                eps=eps,
                revenue_estimate=base_rev,
                pat_estimate=base_pat,
                eps_estimate=eps_est,
                eps_std=eps_std,
                revenue_yoy=0.10 + 0.05 * z + float(rng.normal(0, 0.02)),
                pat_yoy=0.12 + 0.08 * z + float(rng.normal(0, 0.03)),
                guidance=guidance,
                ebitda_margin=0.18 + 0.02 * z + float(rng.normal(0, 0.01)),
                promoter_holding=float(np.clip(52 + rng.normal(0, 1), 40, 75)),
                institutional_holding=float(np.clip(22 + 0.5 * z + rng.normal(0, 1), 5, 45)),
                eps_surprise_history=[float(rng.normal(quality * 0.4, 1.0)) * eps_std
                                      for _ in range(4)],
            )
            events.append(report)
            self._extras[(sym, period)] = self._make_extras(rng, sym, sector, z,
                                                            report)

        # --- realise the price path & volume ----------------------------
        price = base_px * np.cumprod(1 + rets)
        bars: list[PriceBar] = []
        for j in range(n):
            c = float(price[j])
            o = c / (1 + rets[j]) if rets[j] > -0.99 else c
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.004)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.004)))
            deliv = float(np.clip(45 + 8 * (vol[j] / np.median(vol) - 1)
                                  + rng.normal(0, 5), 20, 90))
            bars.append(PriceBar(self._days[j], round(o, 2), round(hi, 2),
                                 round(lo, 2), round(c, 2), round(vol[j], 0), deliv))

        self._history[sym] = bars
        self._events[sym] = events
        if events:
            self._latest_extras[sym] = self._extras[(sym, events[-1].period)]

    def _make_extras(self, rng, sym, sector, z, report) -> _Extras:
        inst = InstitutionalActivity(
            symbol=sym,
            fii_net_cr=float(50 * z + rng.normal(0, 30)),
            dii_net_cr=float(30 * z + rng.normal(0, 20)),
            mf_net_cr=float(20 * z + rng.normal(0, 15)),
            block_deal_net_cr=float((40 * z) if abs(z) > 1.4 else rng.normal(0, 8)),
            bulk_deal_net_cr=float(rng.normal(0, 6)),
            promoter_buy=bool(z > 1.4 and rng.random() < 0.5),
            promoter_sell=bool(z < -1.4 and rng.random() < 0.5),
            pledge_change_pct=float(np.clip(-0.5 * z + rng.normal(0, 0.5), -3, 3)),
            holding_change_pct=float(0.4 * z + rng.normal(0, 0.3)),
        )

        if z > 0.5:
            words = list(rng.choice(_POS_WORDS, size=4, replace=False))
        elif z < -0.5:
            words = list(rng.choice(_NEG_WORDS, size=4, replace=False))
        else:
            words = [str(rng.choice(_POS_WORDS)), str(rng.choice(_NEG_WORDS))]
        transcript = TranscriptData(
            symbol=sym,
            text=(f"Management commentary for {sym}: we saw {words[0]} this quarter. "
                  + " ".join(f"We note {w}." for w in words[1:])),
        )

        pcr = float(np.clip(1.0 + 0.15 * z + rng.normal(0, 0.1), 0.4, 2.2))
        options = OptionsSnapshot(
            symbol=sym,
            call_oi=float(rng.uniform(1e5, 5e5)),
            put_oi=float(rng.uniform(1e5, 5e5)),
            call_oi_change=float(-20 * z + rng.normal(0, 10)),
            put_oi_change=float(20 * z + rng.normal(0, 10)),
            pcr=pcr,
        )

        corporate = CorporateEvent(
            symbol=sym,
            order_win=bool(z > 1.3 and rng.random() < 0.5),
            acquisition=bool(rng.random() < 0.04),
            buyback=bool(rng.random() < 0.05),
            bonus_or_split=bool(rng.random() < 0.03),
            fund_raise=bool(z < -1.0 and rng.random() < 0.2),
            credit_upgrade=bool(z > 2.0 and rng.random() < 0.5),
            credit_downgrade=bool(z < -2.0 and rng.random() < 0.5),
            management_exit=bool(z < -1.8 and rng.random() < 0.3),
        )

        smed = _SECTOR_MEDIAN_PE.get(sector, 25)
        pe = float(np.clip(smed * (1 + 0.06 * z + rng.normal(0, 0.08)), 5, 120))
        growth = max(report.pat_yoy or 0.1, 0.01)
        valuation = ValuationInputs(
            symbol=sym,
            pe=pe,
            ev_ebitda=float(np.clip(pe * 0.6, 4, 60)),
            peg=float(np.clip(pe / (growth * 100), 0.3, 5)),
            pb=float(np.clip(pe / 12, 0.5, 15)),
            fcf_yield=float(np.clip(0.05 - 0.01 * z + rng.normal(0, 0.01), -0.02, 0.12)),
            sector_median_pe=smed,
        )
        return _Extras(inst, transcript, options, corporate, valuation)

    # ----------------------------------------------------------- DataProvider
    def get_history(self, symbol, from_date, to_date, interval="day"):
        bars = self._history.get(symbol, [])
        return [b for b in bars if from_date <= b.date <= to_date]

    def get_ltp(self, symbols):
        out = {}
        for s in symbols:
            bars = self._history.get(s)
            if bars:
                out[s] = bars[-1].close
        return out

    def iter_new_earnings(self) -> Iterable[EarningsReport]:
        """Simulate a fresh results batch: the latest event for each stock."""
        for sym, events in self._events.items():
            if events:
                yield events[-1]

    def get_institutional(self, symbol):
        return self._latest_extras[symbol].institutional

    def get_transcript(self, symbol):
        return self._latest_extras[symbol].transcript

    def get_options(self, symbol):
        return self._latest_extras[symbol].options

    def get_corporate_event(self, symbol):
        return self._latest_extras[symbol].corporate

    def get_valuation(self, symbol):
        return self._latest_extras[symbol].valuation

    # ----------------------------------------------------- backtest helpers
    def all_events(self) -> list[EarningsReport]:
        out: list[EarningsReport] = []
        for events in self._events.values():
            out.extend(events)
        out.sort(key=lambda r: r.report_datetime)
        return out

    def get_extras(self, symbol: str, period: str) -> _Extras:
        return self._extras[(symbol, period)]
