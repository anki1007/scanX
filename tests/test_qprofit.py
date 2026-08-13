"""Four consecutive quarters of rising net profit, latest positive."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.qprofit import evaluate, streak  # noqa: E402


def _bundle(net_profit, headers=None):
    return {"fundamental": {"quarters": {
        "headers": headers or ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"],
        "rows": {"Sales": ["1"] * len(net_profit), "Net Profit": list(net_profit)},
    }}}


# ------------------------------------------------------------ the rule itself

def test_four_rising_quarters_ending_positive_passes():
    r = streak(["8", "11", "15", "21"])
    assert r["pass"] is True
    assert r["quarters"] == [8.0, 11.0, 15.0, 21.0]


def test_a_single_dip_anywhere_fails():
    assert streak(["8", "11", "10", "21"])["pass"] is False
    assert streak(["8", "7", "15", "21"])["pass"] is False
    assert streak(["8", "11", "15", "14"])["pass"] is False


def test_flat_is_not_rising():
    """The rule says >, not >=. Two equal quarters are not growth."""
    assert streak(["8", "11", "11", "21"])["pass"] is False


def test_rising_but_still_loss_making_fails():
    assert streak(["-40", "-30", "-20", "-5"])["pass"] is False
    assert streak(["-40", "-30", "-20", "0"])["pass"] is False, "zero is not > 0"


def test_only_the_last_four_quarters_are_judged():
    """Twelve quarters on file, the rule is about the most recent four."""
    r = streak(["99", "1", "50", "2", "3", "8", "11", "15", "21"])
    assert r["pass"] is True
    assert r["quarters"] == [8.0, 11.0, 15.0, 21.0]


# ---------------------------------------------- turnaround vs compounding

def test_a_turnaround_passes_but_is_marked():
    """-12 -> -4 -> 3 -> 9 satisfies the rule as written. It is not the same
    investment as four quarters of widening profit, so it says which it is."""
    r = streak(["-12", "-4", "3", "9"])
    assert r["pass"] is True
    assert r["turnaround"] is True


def test_consistent_profit_is_not_marked_a_turnaround():
    assert streak(["8", "11", "15", "21"])["turnaround"] is False


def test_growth_is_measured_from_the_oldest_quarter_in_the_window():
    r = streak(["10", "12", "15", "20"])
    assert r["growth_pct"] == 100.0


# --------------------------------------------------- data that must not lie

def test_a_missing_quarter_is_not_treated_as_zero():
    """Reading a gap as 0.0 would invent a trough and manufacture a rising
    streak out of nothing."""
    r = streak(["8", "", "15", "21"])
    assert r["pass"] is False
    assert "no figure" in r["reason"]


def test_fewer_than_four_quarters_cannot_pass():
    r = streak(["8", "11", "15"])
    assert r["pass"] is False
    assert "fewer than four" in r["reason"]


def test_accounting_parentheses_are_negative():
    """(4.2) is -4.2 on a filed statement, not +4.2."""
    r = streak(["(40)", "(30)", "(20)", "9"])
    assert r["quarters"] == [-40.0, -30.0, -20.0, 9.0]
    assert r["pass"] is True and r["turnaround"] is True


def test_commas_and_currency_parse():
    r = streak(["1,200", "₹ 1,300", "1,450", "1,900"])
    assert r["pass"] is True
    assert r["quarters"][0] == 1200.0


# ----------------------------------------------------------------- bundles

def test_evaluate_reads_a_baked_bundle_and_names_the_periods():
    r = evaluate(_bundle(["8", "11", "15", "21"]))
    assert r["pass"] is True
    assert r["periods"] == ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]


def test_evaluate_never_raises_on_junk():
    for junk in (None, {}, [], "x", {"fundamental": []},
                 {"fundamental": {"quarters": "no"}},
                 {"fundamental": {"quarters": {"rows": None}}}):
        out = evaluate(junk)
        assert out["pass"] is False


def test_a_bundle_with_no_net_profit_row_does_not_pass():
    b = {"fundamental": {"quarters": {"headers": ["a", "b", "c", "d"],
                                      "rows": {"Sales": ["1", "2", "3", "4"]}}}}
    assert evaluate(b)["pass"] is False


# ------------------------------------------------- the screen on the page

def test_the_dropdown_offers_the_screen():
    from pathlib import Path
    html = (ROOT / "docs" / "technofunda.html").read_text(encoding="utf-8")
    assert 'value="QP"' in html, "the option is not in the dropdown"
    assert "loadQProfit" in html, "nothing fetches the board"
    assert "QP_COLS" in html


def test_the_page_sorts_by_market_cap_not_growth():
    """Growth-first put a company that went from Rs 0.01 Cr to Rs 1.56 Cr at
    the top on +15,500%, and the whole first page was rounding error."""
    html = (ROOT / "docs" / "technofunda.html").read_text(encoding="utf-8")
    assert "view==='QP'?{k:'mcap',d:-1}" in html


def test_the_baker_is_scheduled():
    """A screen nothing refreshes freezes the day it ships."""
    wf = "\n".join(p.read_text(encoding="utf-8")
                   for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert "refresh_qprofit.py" in wf
