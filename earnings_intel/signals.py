"""
Signal generation rules.

Turns a ScoreBundle into an Action. The STRONG BUY / STRONG SELL gates are the
exact multi-condition rules from the spec; everything else falls back to graded
BUY / HOLD / SELL on the composite. The Portfolio Risk Agent still has the final
say (it can reject any signal), so this layer is about *direction + conviction*.
"""
from __future__ import annotations

from .config import Settings, DEFAULTS
from .engines.scoring import ScoreBundle
from .models import Action, EarningsReport, Guidance, InstitutionalActivity


def classify(
    bundle: ScoreBundle,
    report: EarningsReport,
    institutional: InstitutionalActivity | None = None,
    settings: Settings = DEFAULTS,
) -> tuple[Action, float, list[str]]:
    c = bundle.components
    f = bundle.features
    sb = settings.strong_buy
    ss = settings.strong_sell
    rationale: list[str] = []

    # ---- STRONG BUY: every spec condition must hold ----------------------
    strong_buy = (
        c.pead >= sb.pead
        and c.sue >= sb.sue
        and c.transcript >= sb.transcript
        and c.institutional >= sb.institutional
        and f.rvol >= sb.volume_x
        and f.delivery_pct >= sb.delivery_pct
        and c.technical >= sb.technical
    )

    # ---- STRONG SELL: weak drift + corroborating distribution -----------
    promoter_sell = bool(institutional and institutional.promoter_sell)
    inst_selling = c.institutional <= 35 or (institutional and
                                             (institutional.fii_net_cr
                                              + institutional.dii_net_cr) < 0)
    strong_sell = (
        c.pead <= ss.pead
        and report.guidance == Guidance.LOWERED
        and inst_selling
        and f.rvol >= 2.0
        and c.technical <= ss.technical
    )

    if strong_buy:
        rationale = [
            f"PEAD {c.pead:.0f} & SUE {c.sue:.0f} (strong beat)",
            f"Transcript {c.transcript:.0f} ({bundle.transcript_sentiment})",
            f"Institutional flow {c.institutional:.0f}",
            f"Volume {f.rvol:.1f}x, delivery {f.delivery_pct:.0f}%",
            f"Technical {c.technical:.0f} (trend/breakout confirmed)",
        ]
        conf = min(99.0, 0.5 * bundle.composite + 0.5 * c.pead + 5)
        return Action.STRONG_BUY, round(conf, 1), rationale

    if strong_sell:
        rationale = [
            f"PEAD {c.pead:.0f} (weak/negative drift)",
            "Guidance lowered",
            "Institutional/promoter distribution",
            f"Volume {f.rvol:.1f}x on breakdown, technical {c.technical:.0f}",
        ]
        conf = min(99.0, 0.5 * (100 - bundle.composite) + 0.5 * (100 - c.pead) + 5)
        return Action.STRONG_SELL, round(conf, 1), rationale

    # ---- graded fallback -------------------------------------------------
    if bundle.composite >= 68 and c.pead >= 60 and c.technical >= 55:
        return Action.BUY, round(bundle.composite, 1), [
            f"Composite {bundle.composite:.0f}, PEAD {c.pead:.0f}, "
            f"technical {c.technical:.0f}"]
    if bundle.composite <= 35:
        return Action.SELL, round(100 - bundle.composite, 1), [
            f"Composite {bundle.composite:.0f} (weak across the board)"]

    return Action.HOLD, round(bundle.composite, 1), [
        f"Composite {bundle.composite:.0f} — no high-conviction edge"]
