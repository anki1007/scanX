"""
Alert Engine — formatting.

Renders a Signal into the human-readable alert from the spec. Delivery channels
(Telegram/Discord/email) are Phase 3; this module produces the payload they send.
"""
from __future__ import annotations

from .models import Action, EarningsReport, Signal


def _flow_label(score: float) -> str:
    return "Positive" if score >= 60 else "Negative" if score <= 40 else "Neutral"


def _sentiment_label(score: float) -> str:
    return "Bullish" if score >= 65 else "Bearish" if score <= 35 else "Neutral"


def _pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.0f}%"


def format_alert(signal: Signal, report: EarningsReport | None = None) -> str:
    c = signal.components
    head = "BUY ALERT" if signal.action in (Action.STRONG_BUY, Action.BUY) else (
        "SELL ALERT" if signal.action in (Action.STRONG_SELL, Action.SELL)
        else "WATCH")

    lines = [
        head,
        "",
        f"Stock: {signal.symbol}    [{signal.action.value}]",
    ]
    if report is not None:
        lines += [
            f"Revenue Growth: {_pct(report.revenue_yoy)}",
            f"PAT Growth: {_pct(report.pat_yoy)}",
        ]
    lines += [
        "",
        f"PEAD Score: {c.pead:.0f}",
        f"SUE Score: {c.sue:.0f}",
        f"Composite: {signal.composite_score:.0f}",
        f"Transcript Sentiment: {_sentiment_label(c.transcript)}",
        f"Institutional Flow: {_flow_label(c.institutional)}",
    ]
    if signal.plan and signal.plan.quantity > 0:
        p = signal.plan
        lines += [
            "",
            f"Entry: {p.entry}",
            f"Stop: {p.stop}",
            f"Target 1: {p.target1}",
            f"Target 2: {p.target2}",
            f"Qty: {p.quantity}   (risk Rs {p.risk_amount:,.0f}, "
            f"notional Rs {p.notional:,.0f})",
        ]
    lines += ["", f"Confidence: {signal.confidence:.0f}%"]
    return "\n".join(lines)
