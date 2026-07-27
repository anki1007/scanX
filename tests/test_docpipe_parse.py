"""Deterministic doc pipeline: type detection, cleaning, sections, chunks, financials.

Fixture strings only — no PDFs, no network, no API key. Everything under test is
a pure function, so these tests are the contract for the LLM-free half of the
document pipeline.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.docpipe import parse as dp   # noqa: E402


# ------------------------------------------------------------------ fixtures
ANNUAL = """ABC Industries Limited Annual Report 2024-25

5. MANAGEMENT DISCUSSION AND ANALYSIS
Revenue from operations grew during the year. Our management discussion and analysis of segment performance follows the usual format and is set out in the pages below.

BOARD'S REPORT
The directors present the report for the year under review.

RISK FACTORS
Commodity inflation remains the principal risk to the business.
As set out in the Management Discussion and Analysis, demand improved.

ANNEXURE III - REPORT ON CORPORATE GOVERNANCE
The board met four times during the year.

Related Party Transactions
There were no material related party transactions during the year.

Business Responsibility and Sustainability Report
Scope 1 emissions declined by a fifth during the year.

INDEPENDENT AUDITOR'S REPORT
We have audited the accompanying financial statements.

Notes to the Financial Statements
Note 1 covers the significant accounting policies.
"""

RESULTS = """Revenue from operations for the quarter stood at Rs. 4,521 crore, up 18% year on year.
EBITDA was ₹1,23,456 lakh for the period under review.
The company reported a net loss of Rs 512 crore in the quarter.
EBITDA margin came in at 14.2% against 12.9% a year ago.
Basic EPS for the quarter was Rs. 12.50 per share.
Net debt stood at (1,234) crore after the repayment.
Cash and cash equivalents were Rs 2,000 crore at the end of March 2026.
Capex of Rs 18,000 crore is planned for FY27 across products and capacity.
Return on equity improved to 18.4% while ROCE was 21 per cent.
Working capital was Rs 1,500 crore at the year end.
Free cash flow of Rs 900 crore was generated during the year.
The Board recommended a dividend of Rs 5 per share.
We expect revenue growth of 15% in FY27 and we are guiding to a 16% EBITDA margin.
"""

TRANSCRIPT = ("Moderator: Ladies and gentlemen, good day and welcome to the "
              "Q4 FY26 earnings conference call of ABC Industries Limited.")


# ============================================================== detect_type ==
def test_detect_type_every_branch():
    cases = [
        ("Draft Red Herring Prospectus", "", "drhp"),
        ("Integrated Report 2024-25", "", "integrated_report"),
        ("Business Responsibility and Sustainability Report", "", "esg_report"),
        ("Annual Report 2024-25", "", "annual_report"),
        ("May 2026 Concall Transcript", "", "concall_transcript"),
        ("May 2026 Concall PPT", "", "concall_ppt"),
        ("Investor Presentation - May 2026", "", "investor_presentation"),
        ("Unaudited Financial Results for the quarter ended June 30, 2026", "",
         "quarterly_results"),
        ("Outcome of Board Meeting", "", "announcement"),
        ("", "", "unknown"),
    ]
    for title, url, want in cases:
        assert dp.detect_type(title, url) == want, title
    for want in cases:
        assert want[2] in dp.DOC_TYPES


def test_detect_type_precedence_ladder():
    # integrated / esg outrank the plain annual-report signal they contain
    assert dp.detect_type("Integrated Annual Report 2024-25", "") == "integrated_report"
    assert dp.detect_type("BRSR Annual Report 2024-25", "") == "esg_report"
    # the offer document outranks everything
    assert dp.detect_type("DRHP - Annual Report extracts", "") == "drhp"
    # transcript beats the deck published alongside it
    assert dp.detect_type("Q4 FY26 Concall Transcript and PPT", "") == "concall_transcript"
    # earnings deck beats the generic investor deck
    assert dp.detect_type("Q4FY26 Earnings Presentation", "") == "concall_ppt"
    # results beat the board-meeting wrapper they arrive in
    assert dp.detect_type(
        "Outcome of Board Meeting - Audited Financial Results for the quarter "
        "ended 30 June 2026", "") == "quarterly_results"


def test_detect_type_url_signal_and_text_fallback():
    # url alone is enough, punctuation and glued words included
    assert dp.detect_type("", "https://x.com/files/AnnualReport_2025.pdf") == "annual_report"
    assert dp.detect_type("", "https://bse.in/concall-transcript-may26.pdf") == "concall_transcript"
    # nothing in title/url -> body text votes
    assert dp.detect_type("Document", "https://x.com/y.pdf", TRANSCRIPT) == "concall_transcript"
    assert dp.detect_type("", "", "This Draft Red Herring Prospectus is filed with SEBI.") == "drhp"
    # title/url always beat the body
    assert dp.detect_type("Investor Presentation", "", TRANSCRIPT) == "investor_presentation"
    # no signal anywhere
    assert dp.detect_type("Untitled", "https://x.com/f.pdf", "Some prose.") == "unknown"


# =============================================================== clean_text ==
def test_clean_text_dehyphenates_without_corrupting_words():
    assert "international" in dp.clean_text("The inter-\nnational business grew.")
    assert "inter-national" not in dp.clean_text("The inter-\nnational business grew.")
    # a compound broken AT its own hyphen keeps the hyphen
    assert "state-of-the-art" in dp.clean_text("A new state-\nof-the-art plant.")
    assert "year-on-year" in dp.clean_text("Growth was strong year-\non-year.")
    # digits and proper nouns are never glued together
    assert "1,000-crore" in dp.clean_text("A Rs 1,000-\ncrore capex plan.")
    assert "Tata-Motors" in dp.clean_text("The Tata-\nMotors joint venture.")
    # a hyphen inside a line is left completely alone
    assert dp.clean_text("A state-of-the-art plant.") == "A state-of-the-art plant."


def test_clean_text_strips_pdf_artefacts():
    out = dp.clean_text("inter­national ​growth﻿ was strong")
    assert out == "international growth was strong"
    assert dp.clean_text("") == ""
    assert dp.clean_text(None) == ""


def test_clean_text_drops_page_furniture_but_never_sentences():
    raw = (
        "ABC Industries Limited\nAnnual Report 2024-25\nPage 1 of 3\n"
        "Revenue from operations grew during the year.\n"
        "ABC Industries Limited\nAnnual Report 2024-25\nPage 2 of 3\n"
        "Margins expanded on a better product mix.\n"
        "ABC Industries Limited\nAnnual Report 2024-25\nPage 3 of 3\n"
        "Thank you.\nThank you.\nThank you.\n"
    )
    out = dp.clean_text(raw)
    assert "ABC Industries Limited" not in out       # running header
    assert "Annual Report 2024-25" not in out        # running footer
    assert "Page 1 of 3" not in out and "3" not in out.split("\n")
    # content is untouched, including a short line that repeats but IS a sentence
    assert "Revenue from operations grew during the year." in out
    assert "Margins expanded on a better product mix." in out
    assert out.count("Thank you.") == 3


def test_clean_text_collapses_whitespace_only():
    raw = "Revenue   grew\t\t18%.\n\n\n\nMargins   expanded.\n"
    out = dp.clean_text(raw)
    assert out == "Revenue grew 18%.\n\nMargins expanded."
    assert "  " not in out


# ============================================================ split_sections ==
def test_split_sections_finds_indian_filing_headings():
    titles = [s.title for s in dp.split_sections(ANNUAL)]
    for want in dp.SECTION_TITLES:
        assert want in titles, want
    assert titles[0] == ""                                   # preamble first


def test_split_sections_offsets_are_exact_and_contiguous():
    secs = dp.split_sections(ANNUAL)
    assert secs[0].start == 0
    assert secs[-1].end == len(ANNUAL)
    for i, s in enumerate(secs):
        assert ANNUAL[s.start:s.end] == s.text               # provenance holds
        assert s.end > s.start
        if i:
            assert s.start == secs[i - 1].end                # no gaps, no overlap
        assert set(s.as_dict()) == {"title", "text", "start", "end"}


def test_split_sections_ignores_inline_mentions_and_bare_text():
    secs = dp.split_sections(ANNUAL)
    mda = [s for s in secs if s.title == "MD&A"][0]
    risk = [s for s in secs if s.title == "Risk Factors"][0]
    # a long prose line that mentions the heading stays INSIDE its section
    assert "Our management discussion and analysis of segment performance" in mda.text
    # and so does a SHORT one, which only the coverage guard can reject
    assert "As set out in the Management Discussion and Analysis" in risk.text
    assert [s.title for s in secs].count("MD&A") == 1

    plain = dp.split_sections("Just some prose. No headings at all here.")
    assert len(plain) == 1 and plain[0].title == ""
    assert dp.split_sections("") == []
    assert dp.split_sections("   \n  ") == []


# ==================================================================== chunk ==
CHUNK_SRC = ("Revenue from operations grew 18% in the quarter. "
             "EBITDA rose to Rs. 4,521 crore during the same period. "
             "Margins expanded on better mix and lower input costs. "
             "The company commissioned a new line at Pune. "
             "Net debt fell sharply through the year. "
             "Management remains confident about FY27 demand.")


def test_chunk_respects_sentence_boundaries_and_target():
    chunks = dp.chunk(CHUNK_SRC, target_chars=120, overlap=40, section="MD&A")
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 120                            # target honoured
        assert c.text[-1] in ".!?"                           # never mid-sentence
        assert not c.text[0].isspace()
    # "Rs. 4,521 crore" must not be read as a sentence end
    assert any("Rs. 4,521 crore during the same period." in c.text for c in chunks)


def test_chunk_metadata_and_provenance():
    chunks = dp.chunk(CHUNK_SRC, target_chars=120, overlap=40, section="MD&A")
    assert [c.idx for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.section == "MD&A"
        assert CHUNK_SRC[c.char_start:c.char_end] == c.text  # traceable back
        assert set(c.as_dict()) == {"text", "idx", "section", "char_start", "char_end"}


def test_chunk_overlap_repeats_context():
    chunks = dp.chunk(CHUNK_SRC, target_chars=120, overlap=40)
    for a, b in zip(chunks, chunks[1:]):
        assert b.char_start < a.char_end                     # windows overlap
        assert b.char_start > a.char_start                   # but always advance
    # overlap=0 tiles the text without repeating anything
    tiled = dp.chunk(CHUNK_SRC, target_chars=120, overlap=0)
    for a, b in zip(tiled, tiled[1:]):
        assert b.char_start >= a.char_end
    assert all(c.section == "" for c in tiled)


def test_chunk_splits_an_over_long_sentence_and_handles_empty():
    long_one = "word " * 200                                 # a single "sentence"
    chunks = dp.chunk(long_one, target_chars=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)
    assert "".join(long_one[c.char_start:c.char_end] for c in chunks).replace(" ", "") \
        == long_one.replace(" ", "")                         # nothing lost
    assert dp.chunk("") == [] and dp.chunk("   \n ") == []


def test_chunk_document_rebases_offsets_onto_the_whole_filing():
    chunks = dp.chunk_document(ANNUAL, target_chars=140, overlap=30)
    assert [c.idx for c in chunks] == list(range(len(chunks)))
    assert {"MD&A", "Risk Factors"} <= {c.section for c in chunks}
    for c in chunks:
        assert ANNUAL[c.char_start:c.char_end] == c.text     # absolute offsets


def test_sentences_tile_the_input_and_survive_abbreviations():
    spans = dp.sentences(RESULTS)
    assert "".join(RESULTS[s:e] for s, e in spans) == RESULTS
    one = dp.sentences("EBITDA rose to Rs. 4,521 crore. Margins expanded.")
    assert len(one) == 2
    assert dp.sentences("") == []


# ======================================================= extract_financials ==
def test_parse_amount_handles_indian_formats():
    cases = [
        ("Rs. 1,23,456 lakh", (1234.56, "cr")),              # lakh grouping + scale
        ("₹4,521 crore", (4521.0, "cr")),
        ("Rs 4,521Cr", (4521.0, "cr")),
        ("Rs 45,120 mn", (4512.0, "cr")),
        ("2 bn", (200.0, "cr")),
        ("(1,234) crore", (-1234.0, "cr")),                  # bracketed negative
        ("(2,500 crore)", (-2500.0, "cr")),
        ("-512 crore", (-512.0, "cr")),
        ("14.2%", (14.2, "pct")),
        ("21 per cent", (21.0, "pct")),
        ("120 bps", (1.2, "pct")),                           # bps -> pct points
        ("Rs 12.50", (12.5, "rs")),
    ]
    for raw, want in cases:
        assert dp.parse_amount(raw) == want, raw
    assert dp.parse_amount("no digits here") is None
    assert dp.parse_amount("") is None


def test_extract_financials_reads_every_headline_metric():
    fin = dp.extract_financials(RESULTS)
    want = {
        "revenue": (4521.0, "cr"),
        "ebitda": (1234.56, "cr"),                           # 1,23,456 lakh
        "pat": (-512.0, "cr"),                               # "net loss of"
        "margin_pct": (14.2, "pct"),
        "eps": (12.5, "rs"),
        "debt": (-1234.0, "cr"),                             # (1,234)
        "cash": (2000.0, "cr"),
        "capex": (18000.0, "cr"),
        "roe": (18.4, "pct"),
        "roce": (21.0, "pct"),                               # "per cent"
        "working_capital": (1500.0, "cr"),
        "fcf": (900.0, "cr"),
        "dividend": (5.0, "rs"),
    }
    for key, (value, unit) in want.items():
        assert key in fin, key
        assert fin[key]["value"] == value, key
        assert fin[key]["unit"] == unit, key


def test_extract_financials_every_value_carries_a_verbatim_quote():
    fin = dp.extract_financials(RESULTS)
    assert fin                                               # not a vacuous pass
    for key, val in fin.items():
        if key == "guidance_text":
            for sentence in val:
                assert sentence in RESULTS, sentence         # verbatim
            continue
        assert set(val) == {"value", "unit", "quote"}, key
        assert isinstance(val["value"], float), key
        assert val["unit"] in ("cr", "pct", "rs"), key
        assert val["quote"] in RESULTS, key                  # never a bare float
        assert len(val["quote"]) >= 10, key


def test_extract_financials_scales_lakh_crore_and_millions():
    assert dp.extract_financials(
        "Revenue was Rs 1,23,456 lakh for the year.")["revenue"]["value"] == 1234.56
    assert dp.extract_financials(
        "Revenue was Rs 45,120 mn for the year.")["revenue"]["value"] == 4512.0
    assert dp.extract_financials(
        "Revenue was USD 2 billion for the year.")["revenue"]["value"] == 200.0
    # an unqualified number is NOT guessed at - a money metric needs a magnitude
    assert "revenue" not in dp.extract_financials("Revenue was 4,521 for the year.")


def test_extract_financials_handles_bracketed_negatives_and_losses():
    neg = dp.extract_financials("Net debt stood at (1,234) crore after repayment.")
    assert neg["debt"]["value"] == -1234.0
    loss = dp.extract_financials("The company reported a net loss of Rs 512 crore.")
    assert loss["pat"]["value"] == -512.0
    profit = dp.extract_financials("Net profit was Rs 512 crore for the quarter.")
    assert profit["pat"]["value"] == 512.0


def test_extract_financials_percentages_beat_bps_movements():
    only_bps = dp.extract_financials("Gross margin expanded by 120 bps in the quarter.")
    assert only_bps["margin_pct"] == {
        "value": 1.2, "unit": "pct",
        "quote": "Gross margin expanded by 120 bps in the quarter."}
    # an absolute level always wins over a movement, whichever came first
    both = dp.extract_financials(
        "Gross margin expanded by 120 bps in the quarter. "
        "EBITDA margin was 14.2% for the year.")
    assert both["margin_pct"]["value"] == 14.2


def test_extract_financials_guidance_is_verbatim_and_optional():
    fin = dp.extract_financials(RESULTS)
    assert any("we are guiding to a 16% EBITDA margin" in g
               for g in fin["guidance_text"])
    for g in fin["guidance_text"]:
        assert g in RESULTS
    assert "guidance_text" not in dp.extract_financials("Revenue was Rs 10 crore.")
    assert dp.extract_financials("") == {}
    assert dp.extract_financials("   ") == {}


# =========================================================== extract_tables ==
class _FakePage:
    def __init__(self, tables, boom=False):
        self._tables, self._boom = tables, boom

    def extract_tables(self):
        if self._boom:
            raise RuntimeError("unreadable page")
        return self._tables


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_extract_tables_shapes_rows_and_skips_bad_pages(monkeypatch):
    pdf = _FakePdf([
        _FakePage([[["Particulars", "Q4  FY26", None],
                    ["Revenue", "4,521", ""],
                    [None, None, None]]]),
        _FakePage(None, boom=True),
        _FakePage([[["Segment", "Sales"], ["Auto", "3,100"]]]),
    ])
    monkeypatch.setattr(dp, "_open_pdf", lambda src: pdf)
    tables = dp.extract_tables(b"%PDF-fake")
    assert len(tables) == 2                                  # the bad page degraded
    first = tables[0]
    assert first["page"] == 1 and first["index"] == 0
    assert first["header"] == ["Particulars", "Q4 FY26", ""]  # cells collapsed
    assert first["rows"] == [["Particulars", "Q4 FY26", ""], ["Revenue", "4,521", ""]]
    assert first["n_rows"] == 2 and first["n_cols"] == 3
    assert tables[1]["page"] == 3


def test_extract_tables_degrades_to_empty_list(monkeypatch):
    assert dp.extract_tables(None) == []

    def _boom(_src):
        raise ImportError("No module named 'pdfplumber'")

    monkeypatch.setattr(dp, "_open_pdf", _boom)
    assert dp.extract_tables("missing.pdf") == []
    assert dp.extract_tables(b"not really a pdf") == []
