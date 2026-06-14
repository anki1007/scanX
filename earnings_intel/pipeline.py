"""
End-to-end orchestration (the spine of the system).

scan -> (per stock) pull price history + event feeds -> score every engine ->
composite -> classify signal -> size with the risk agent -> rank -> emit.

    from earnings_intel import Pipeline
    from earnings_intel.data import SampleProvider
    result = Pipeline(SampleProvider()).run()
    for s in result.signals[:5]:
        print(s.symbol, s.action.value, s.composite_score)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .config import Settings, DEFAULTS
from .data.base import DataProvider
from .engines.scoring import ScoringEngine
from .models import Action, ComponentScores, Signal
from .risk import RiskManager, build_trade_plan
from .signals import classify


@dataclass
class PipelineResult:
    signals: list[Signal] = field(default_factory=list)

    @property
    def actionable(self) -> list[Signal]:
        return [s for s in self.signals
                if s.action in (Action.STRONG_BUY, Action.BUY,
                                Action.STRONG_SELL, Action.SELL)]

    @property
    def longs(self) -> list[Signal]:
        return [s for s in self.signals
                if s.action in (Action.STRONG_BUY, Action.BUY) and s.plan]


class Pipeline:
    def __init__(self, provider: DataProvider, settings: Settings = DEFAULTS):
        self.provider = provider
        self.settings = settings
        self.engine = ScoringEngine(settings)

    def run(
        self,
        universe: Optional[list[str]] = None,
        equity: Optional[float] = None,
        lookback_days: int = 500,
    ) -> PipelineResult:
        asof = getattr(self.provider, "as_of", date.today())
        sectors = getattr(self.provider, "sectors", {})
        equity = equity if equity is not None else self.settings.risk.account_equity
        risk_mgr = RiskManager(self.settings.risk, equity)

        scored: list[tuple[Signal, str]] = []   # (signal, sector)

        for report in self.provider.iter_new_earnings():
            if universe and report.symbol not in universe:
                continue
            frm = report.report_datetime.date() - timedelta(days=lookback_days)
            to = report.report_datetime.date()
            bars = self.provider.get_history(report.symbol, frm, to)
            if len(bars) < 60:
                continue

            bundle = self.engine.score(
                report, bars,
                self.provider.get_institutional(report.symbol),
                self.provider.get_transcript(report.symbol),
                self.provider.get_options(report.symbol),
                self.provider.get_corporate_event(report.symbol),
                self.provider.get_valuation(report.symbol),
            )
            action, conf, rationale = classify(
                bundle, report, self.provider.get_institutional(report.symbol),
                self.settings)

            sig = Signal(
                symbol=report.symbol, action=action,
                composite_score=round(bundle.composite, 1), confidence=conf,
                components=bundle.components, rationale=rationale,
                as_of=report.report_datetime,
            )
            sig._atr = bundle.features.atr        # stash for sizing
            sig._close = bundle.features.close
            scored.append((sig, sectors.get(report.symbol, "NA")))

        # rank by composite (desc) and apply portfolio risk gates to longs
        scored.sort(key=lambda x: x[0].composite_score, reverse=True)
        for sig, sector in scored:
            if sig.action in (Action.STRONG_BUY, Action.BUY,
                              Action.STRONG_SELL, Action.SELL):
                plan = build_trade_plan(
                    sig._close, sig._atr, sig.action, self.settings.risk, equity)
                ok, reason = risk_mgr.approve(sig.symbol, sector, plan)
                if ok:
                    sig.plan = plan
                    risk_mgr.add(sig.symbol, sector, plan)
                else:
                    sig.rationale = sig.rationale + [f"Risk gate: {reason}"]

        return PipelineResult(signals=[s for s, _ in scored])
