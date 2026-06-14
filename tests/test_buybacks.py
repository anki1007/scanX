"""Tests for the Buybacks tab — parsing + the exact acceptance-ratio arbitrage math.

Formulas verified against the user's sheet, row 15 (Prime Securities):
  B=0.0179 C=0.0737 D=0.5 F=240 G=350 I=240
  -> L=0.03316154 M=0.07286296 J=0.01519904 K=0.03339552
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data import buybacks as b   # noqa: E402


def test_parse_price():
    assert b.parse_buyback_price("Buy-back at Rs 1,200 per equity share") == 1200.0
    assert b.parse_buyback_price("buyback price of Rs. 350") == 350.0
    assert b.parse_buyback_price("maximum price of ₹105 per share") == 105.0
    assert b.parse_buyback_price("board to consider buyback") is None


def test_parse_type():
    assert b.parse_buyback_type("through the tender offer route") == "Tender"
    assert b.parse_buyback_type("via open market through stock exchange") == "Open Market"
    assert b.parse_buyback_type("buyback approved") == ""


def test_parse_record_date():
    assert b.parse_record_date("record date is 23 February 2023") == "23 February 2023"
    assert b.parse_record_date("no date given") is None


def test_is_buyback():
    assert b.is_buyback("Buy-back", "intimation")
    assert b.is_buyback("", "Board approves buyback of equity shares")
    assert not b.is_buyback("Board Meeting", "dividend declared")


def test_acceptance_and_expected_money_match_sheet():
    L = b.acceptance_general(0.0179, 0.0737, 0.5)
    M = b.acceptance_small(0.0179, 0.0737, 0.5)
    assert round(L, 6) == 0.033162
    assert round(M, 6) == 0.072863
    assert round(b.expected_money(350, 240, 240, L), 6) == 0.015199
    assert round(b.expected_money(350, 240, 240, M), 6) == 0.033396


def test_expected_money_handles_loss_post_drop():
    # if post-buyback price < buy price, unaccepted shares drag the return down
    em = b.expected_money(1025, 408, 760, 0.2873)   # Matrimony-like row
    assert em < 0


def test_compute_buyback_structure():
    r = b.compute_buyback(buyback_price=350, cmp=240, size_cr=100, market_cap=10000)
    assert r["buyback_pct"] is not None
    assert r["acc_small"] is not None and r["exp_money_small"] is not None
    assert r["pre_record_price"] == 240 and r["price_post"] == 240


def test_premium_pct():
    assert b.premium_pct(350, 240) == 45.83
    assert b.premium_pct(None, 100) is None
