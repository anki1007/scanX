"""
Agent 5 — Transcript NLP (lexicon scorer).

A transparent, dependency-free sentiment/guidance scorer over concall text. It
is deliberately simple and swappable: Phase 2 replaces `score_transcript` with a
finance-tuned transformer behind the same signature.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import TranscriptData
from ._util import clamp

_POSITIVE = [
    "strong demand", "robust", "record", "raising our guidance", "raise guidance",
    "confident", "healthy pipeline", "accelerating", "broad-based growth",
    "operating leverage", "margin expansion", "outperform", "momentum",
    "all-time high", "beat", "upgrade", "tailwind",
]
_NEGATIVE = [
    "weak demand", "headwinds", "margin pressure", "softness", "soft",
    "cautious outlook", "lowering guidance", "lower guidance", "challenging",
    "deceleration", "elevated costs", "subdued", "miss", "decline",
    "slowdown", "downgrade", "uncertain",
]


@dataclass
class TranscriptResult:
    score: float            # 0-100
    sentiment: str          # Bullish / Neutral / Bearish
    pos_hits: int
    neg_hits: int


def score_transcript(data: TranscriptData) -> TranscriptResult:
    text = (data.text or "").lower()
    if not text.strip():
        return TranscriptResult(score=50.0, sentiment="Neutral", pos_hits=0, neg_hits=0)

    pos = sum(text.count(p) for p in _POSITIVE)
    neg = sum(text.count(n) for n in _NEGATIVE)
    total = pos + neg
    if total == 0:
        return TranscriptResult(score=50.0, sentiment="Neutral", pos_hits=0, neg_hits=0)

    polarity = (pos - neg) / total          # -1 .. +1
    score = clamp(50 + 50 * polarity, 0, 100)
    sentiment = "Bullish" if score >= 65 else "Bearish" if score <= 35 else "Neutral"
    return TranscriptResult(score=float(score), sentiment=sentiment,
                            pos_hits=pos, neg_hits=neg)
