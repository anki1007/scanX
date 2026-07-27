"""Health ratios (todo: Current Ratio, OCF/Net Profit, CWIP trend) — analytics.health_ratios."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import analytics as A  # noqa: E402

HDRS = ["Mar 2024", "Mar 2025", "Mar 2026"]


def _stmt(rows):
    return {"headers": HDRS, "rows": rows}


def test_ocf_np_positive_and_cash_backed():
    h = A.health_ratios(
        _stmt({"CWIP": ["10", "10", "10"], "Total Assets": ["100", "100", "100"]}),
        _stmt({"Cash from Operating Activity": ["50", "60", "90"]}),
        _stmt({"Net Profit": ["40", "50", "60"]}),
    )
    assert h["ocf_np"]["value"] == 1.5
    assert h["ocf_np"]["bias"] == "positive"
    assert h["ocf_np"]["year"] == "Mar 2026"


def test_ocf_np_negative_ocf_flags_red():
    h = A.health_ratios(
        _stmt({}), _stmt({"Cash from Operating Activity": ["10", "5", "-20"]}),
        _stmt({"Net Profit": ["40", "50", "60"]}),
    )
    assert h["ocf_np"]["bias"] == "negative"


def test_cwip_drawdown_is_positive_capex_completed():
    # todo.md's example: CWIP 100 -> 30 means the work is done
    h = A.health_ratios(
        _stmt({"CWIP": ["80", "100", "30"], "Total Assets": ["1000", "1000", "1000"]}),
        _stmt({}), _stmt({}),
    )
    assert h["cwip"]["bias"] == "positive"
    assert h["cwip"]["pct_change"] == -70.0


def test_cwip_tiny_base_is_not_a_signal():
    # a 1 -> 0.3 drawdown on a 100k-asset company is noise, not commissioning
    h = A.health_ratios(
        _stmt({"CWIP": ["2", "1", "0.3"], "Total Assets": ["100000", "100000", "100000"]}),
        _stmt({}), _stmt({}),
    )
    assert h["cwip"]["bias"] != "positive"


def test_current_ratio_when_schedule_rows_present():
    h = A.health_ratios(
        _stmt({"Total Current Assets": ["200", "220", "300"],
               "Total Current Liabilities": ["100", "110", "150"]}),
        _stmt({}), _stmt({}),
    )
    assert h["current_ratio"]["value"] == 2.0
    assert h["current_ratio"]["bias"] == "positive"


def test_current_ratio_below_one_is_negative():
    h = A.health_ratios(
        _stmt({"Current Assets": ["90", "80", "70"],
               "Current Liabilities": ["100", "100", "100"]}),
        _stmt({}), _stmt({}),
    )
    assert h["current_ratio"]["value"] == 0.7
    assert h["current_ratio"]["bias"] == "negative"


def test_debt_equity_conservative_is_positive():
    # Smart-Ratios doc: D/E < 0.30 ideal
    h = A.health_ratios(
        _stmt({"Equity Capital": ["10", "10", "10"], "Reserves": ["90", "140", "190"],
               "Borrowings": ["50", "40", "30"]}),
        _stmt({}), _stmt({}),
    )
    assert h["debt_equity"]["value"] == 0.15
    assert h["debt_equity"]["bias"] == "positive"


def test_debt_equity_leverage_risk_is_negative():
    h = A.health_ratios(
        _stmt({"Equity Capital": ["10", "10", "10"], "Reserves": ["40", "40", "40"],
               "Borrowings": ["30", "60", "120"]}),
        _stmt({}), _stmt({}),
    )
    assert h["debt_equity"]["value"] == 2.4
    assert h["debt_equity"]["bias"] == "negative"


def test_debt_equity_negative_networth_flagged():
    h = A.health_ratios(
        _stmt({"Equity Capital": ["10", "10", "10"], "Reserves": ["-5", "-15", "-25"],
               "Borrowings": ["30", "60", "120"]}),
        _stmt({}), _stmt({}),
    )
    assert h["debt_equity"]["bias"] == "negative"
    assert h["debt_equity"]["value"] is None


def test_compact_view_is_honest_na():
    h = A.health_ratios(
        _stmt({"Other Assets": ["1", "2", "3"]}), _stmt({}), _stmt({}),
    )
    assert h["current_ratio"]["bias"] == "na"
    assert h["ocf_np"]["bias"] == "na"
