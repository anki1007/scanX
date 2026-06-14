"""Test PDF deep-fetch wiring: pdf_text -> value/price parsers (network mocked)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import deepfetch as df   # noqa: E402


def test_value_cr_from_pdf(monkeypatch):
    monkeypatch.setattr(df, "pdf_text", lambda url, **k: "Company has bagged an order worth Rs. 1,734 crore from NTPC")
    assert df.value_cr_from_pdf("http://x/abc.pdf") == 1734.0


def test_buyback_price_from_pdf(monkeypatch):
    monkeypatch.setattr(df, "pdf_text", lambda url, **k: "Buyback at a price of Rs 4500 per equity share via tender")
    assert df.buyback_price_from_pdf("http://x/bb.pdf") == 4500.0


def test_pdf_text_skips_non_pdf():
    assert df.pdf_text("https://www.screener.in/company/MOL/") is None
