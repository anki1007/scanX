"""Order book (backlog) read out of company filings.

Every case here is a sentence from a real filing that the first version got
wrong or right: forecasts, order inflows, segment slices and export books must
not become the backlog; the headline figure must.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data.orderbook import (  # noqa: E402
    document_figure, mentions, quarter_end_before, quarterly_series)


def _vals(s):
    return [m[0] for m in mentions(s)]


def test_headline_statements_are_read():
    assert _vals("Diversified Order Book of Rs. 5,143.3 Cr as on 30thJun 2026") == [5143.3]
    assert _vals("Order book at end of Q1: INR 1,529 Cr (1,514 Cr in Q4FY26)") == [1529.0]
    assert _vals("Our order book stands at approximately INR61,500 crores, the highest") == [61500.0]
    assert _vals("Order Book Build Rs. 8140 Mn") == [814.0]


def test_the_as_on_date_is_taken_from_the_sentence():
    (_, asof, _), = mentions("Order book by segment · as on 30 June 2026 · ₹ 13,245 Crore")
    assert asof == date(2026, 6, 30)


def test_forecasts_are_not_the_backlog():
    assert _vals("the estimated closing order book would be close to about INR5,000 crores") == []
    assert _vals("our total order book could reach around Rs. 16,000 crore by March 2026") == []
    assert _vals("with an outstanding order book of around Rs. 15,000 crore, we should be well-positioned") == []


def test_flows_and_slices_are_not_the_backlog():
    assert _vals("Order inflow of Rs 900 crore during the quarter") == []
    assert _vals("Order book Build-Up Existing Business Received Rs. 1368.8 Crs of orders") == []
    assert _vals("Our export order book remains healthy at around INR80 crores") == []


def test_a_quarter_takes_the_figure_its_documents_agree_on():
    """Waaree: the presentation headlined one segment (~5,300 Cr); the press
    release and the call both said ~61,500 Cr."""
    figs = [{"as_of": "2026-06-30", "value_cr": 5300.0, "kind": "presentation", "filed": "2026-07-30"},
            {"as_of": "2026-06-30", "value_cr": 61500.0, "kind": "press", "filed": "2026-07-29"},
            {"as_of": "2026-06-30", "value_cr": 61500.0, "kind": "transcript", "filed": "2026-08-06"},
            {"as_of": "2026-03-31", "value_cr": 53000.0, "kind": "presentation", "filed": "2026-04-30"}]
    s = quarterly_series(figs)
    assert [(x["as_of"], x["value_cr"]) for x in s] == [("2026-03-31", 53000.0), ("2026-06-30", 61500.0)]


def test_without_a_stated_date_the_quarter_is_the_one_before_filing():
    assert quarter_end_before(date(2026, 7, 30)) == date(2026, 6, 30)
    assert quarter_end_before(date(2026, 2, 5)) == date(2025, 12, 31)
    fig = document_figure("Order book at end of Q1: INR 1,529 Cr", date(2026, 7, 27))
    assert fig["as_of"] == "2026-06-30" and fig["value_cr"] == 1529.0


def test_the_board_row_reports_growth_and_cover():
    import refresh_orderbook as ro
    series = [{"as_of": "2025-06-30", "value_cr": 1000.0, "filed": "2025-07-30", "kind": "press"},
              {"as_of": "2026-06-30", "value_cr": 1500.0, "filed": "2026-07-30", "kind": "press"}]
    row = ro.summarise("ABC", series, {"name": "Abc", "revenue_cr": 750.0, "mcap_cr": 9000.0})
    assert row["growth_1y_pct"] == 50.0 and row["book_to_revenue"] == 2.0
    assert row["as_of"] == "2026-06-30" and len(row["history"]) == 2


def test_only_presentations_releases_and_transcripts_are_read():
    import refresh_orderbook as ro
    assert ro.doc_kind({"desc": "Investor Presentation", "attchmntFile": "x.pdf"}) == "presentation"
    assert ro.doc_kind({"desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
                        "attchmntFile": "X_Transcript.pdf"}) == "transcript"
    assert ro.doc_kind({"desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
                        "attchmntFile": "X_Intimation.pdf"}) is None
    assert ro.doc_kind({"desc": "Trading Window", "attchmntFile": "x.pdf"}) is None


def test_second_round_of_real_misreads():
    """From the first full bake: inflow as 'bookings', revenue beside a title,
    a dollar figure, segment and export slices."""
    assert _vals("Order bookings were INR 11.4 billion, against INR 16.2 billion") == []
    assert _vals("Strengthens Order Book Revenues of Rs. 5,024 crore and PAT of Rs. 73 crore") == []
    assert _vals("supported by an open order book of more than $2 billion, providing") == []
    assert _vals("Healthy order book for cable exports of INR 8,216 Mn. as on July, 2026") == []
    assert _vals("Our order backlog stood at INR209 billion as of June 2026") == [20900.0]


def test_an_old_comparison_date_does_not_date_the_figure():
    fig = document_figure("The order book further improved to INR2,54,538 crores against the "
                          "previous year order book as on 1 April 2025", date(2026, 5, 22))
    assert fig["as_of"] == "2026-03-31" and fig["value_cr"] == 254538.0
    fig = document_figure("Order book as on 1st January 2026 Rs 73,015 crore", date(2026, 1, 15))
    assert fig["as_of"] == "2025-12-31"
