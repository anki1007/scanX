"""
Agent 12 — Composite scoring engine.

Runs every component engine and blends them into a single 0-100 composite using
`config.COMPOSITE_WEIGHTS`. This is the orchestration point for all scoring; the
pipeline and the backtester both call `ScoringEngine.score`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import COMPOSITE_WEIGHTS, Settings, DEFAULTS
from ..models import (
    ComponentScores,
    CorporateEvent,
    EarningsReport,
    InstitutionalActivity,
    OptionsSnapshot,
    PriceBar,
    TranscriptData,
    ValuationInputs,
)
from ._util import clamp
from .features import MarketFeatures, compute_features
from .institutional import score_institutional
from .pead import compute_pead
from .sue import compute_sue
from .technical import score_technical
from .transcript import score_transcript
from .valuation import score_valuation


def _score_options(o: OptionsSnapshot) -> float:
    if o.pcr is None and not o.put_oi and not o.call_oi:
        return 50.0
    s = 50.0
    if o.pcr is not None:
        s = clamp(50 + (o.pcr - 1) * 40, 0, 100)   # rising PCR = put writing = bullish
    s += clamp(o.put_oi_change * 0.15, -10, 10)     # put build-up supportive
    s -= clamp(o.call_oi_change * 0.15, -10, 10)    # call build-up = overhead supply
    return clamp(s, 0, 100)


def _score_corporate(c: CorporateEvent) -> float:
    s = 50.0
    s += 15 if c.order_win else 0
    s += 5 if c.acquisition else 0
    s += 10 if c.buyback else 0
    s += 5 if c.bonus_or_split else 0
    s += 12 if c.credit_upgrade else 0
    s -= 8 if c.fund_raise else 0
    s -= 15 if c.credit_downgrade else 0
    s -= 12 if c.management_exit else 0
    return clamp(s, 0, 100)


@dataclass
class ScoreBundle:
    symbol: str
    components: ComponentScores
    composite: float
    features: MarketFeatures
    sue: float
    pead_parts: dict
    transcript_sentiment: str


class ScoringEngine:
    def __init__(self, settings: Settings = DEFAULTS):
        self.settings = settings

    def score(
        self,
        report: EarningsReport,
        bars: list[PriceBar],
        institutional: Optional[InstitutionalActivity] = None,
        transcript: Optional[TranscriptData] = None,
        options: Optional[OptionsSnapshot] = None,
        corporate: Optional[CorporateEvent] = None,
        valuation: Optional[ValuationInputs] = None,
        benchmark: Optional[list[PriceBar]] = None,
    ) -> ScoreBundle:
        feats = compute_features(bars, benchmark)

        inst_score = score_institutional(institutional) if institutional else 50.0
        tr = score_transcript(transcript) if transcript else None
        sue = compute_sue(report)
        pead = compute_pead(report, feats, inst_score, self.settings.pead_weights)

        comp = ComponentScores(
            sue=sue.score,
            pead=pead.score,
            transcript=tr.score if tr else 50.0,
            institutional=inst_score,
            options=_score_options(options) if options else 50.0,
            technical=score_technical(feats),
            valuation=score_valuation(valuation) if valuation else 50.0,
            corporate_event=_score_corporate(corporate) if corporate else 50.0,
        )

        w = self.settings.composite_weights or COMPOSITE_WEIGHTS
        composite = (
            comp.pead * w["pead"]
            + comp.transcript * w["transcript"]
            + comp.institutional * w["institutional"]
            + comp.options * w["options"]
            + comp.technical * w["technical"]
            + comp.valuation * w["valuation"]
            + comp.corporate_event * w["corporate_event"]
        )

        return ScoreBundle(
            symbol=report.symbol,
            components=comp,
            composite=float(clamp(composite, 0, 100)),
            features=feats,
            sue=sue.score,
            pead_parts=pead.parts,
            transcript_sentiment=tr.sentiment if tr else "Neutral",
        )
