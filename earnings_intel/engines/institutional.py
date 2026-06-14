"""
Agent 6 — Institutional flow score.

Combines FII/DII/MF net flows, block & bulk deals, promoter activity, pledge and
holding changes into a single 0-100 read of "smart money" direction.
"""
from __future__ import annotations

from ..models import InstitutionalActivity
from ._util import clamp, sigmoid_score


def score_institutional(act: InstitutionalActivity) -> float:
    # Net rupee flow, scaled so ~100 cr of net buying reads strongly positive.
    net = (act.fii_net_cr + act.dii_net_cr + act.mf_net_cr
           + act.block_deal_net_cr + 0.5 * act.bulk_deal_net_cr)
    score = sigmoid_score(net / 80.0)          # 0 -> 50, +160cr -> ~88

    # Promoter signal is high conviction.
    if act.promoter_buy:
        score += 8
    if act.promoter_sell:
        score -= 10

    # Rising institutional holding is constructive; more pledging is a red flag.
    score += clamp(act.holding_change_pct * 3, -6, 6)
    score -= clamp(act.pledge_change_pct * 2, -6, 6)

    return clamp(score, 0.0, 100.0)
