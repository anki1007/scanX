"""Tests for the Screener full-text-search client + special-situation tagging."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import special as sp   # noqa: E402
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


def test_detect_category():
    assert sp.detect_category("Intimation of open offer") == "Open Offer"
    assert sp.detect_category("Voluntary delisting of shares") == "Delisting"
    assert sp.detect_category("Scheme of arrangement approved") == "Scheme/Arrangement"
    assert sp.detect_category("Issue of warrants on preferential basis") == "Warrant"
    assert sp.detect_category("Preferential allotment") == "Preferential"
    assert sp.detect_category("something else") == "Special"


def test_parse_date():
    assert sp.parse_date("Announcement - 06 Jun 2026") == "06 Jun 2026"
    assert sp.parse_date("dated 2026-03-01") == "2026-03-01"
    assert sp.parse_date("no date") is None


def test_parse_results_real_dom():
    if BeautifulSoup is None:
        return
    # mirrors the live Screener full-text-search card structure
    html = '''
    <a href="/company/compare/00000001">Agro Chemicals</a>
    <div class="margin-top-20 margin-bottom-36">
      <div><a href="/company/MOL/consolidated/">Meghmani Organics Ltd</a></div>
      <div class="font-size-17 font-weight-500">Public Announcement - Open Offer</div>
      <div class="ink-700 font-size-16">Open offer for up to 1,56,89,957 shares NCLT</div>
      <div class="margin-top-4 ink-700 font-size-14">Announcement - 06 Jun 2026</div>
    </div>
    <div class="margin-top-20 margin-bottom-36">
      <div><a href="/company/531049/">Neelkanth Rockminerals Ltd</a></div>
      <div class="font-size-17">Voluntary delisting of equity shares</div>
      <div class="ink-700 font-size-16">delisting offer detail</div>
      <div class="font-size-14">Announcement - 05 Jun 2026</div>
    </div>'''
    rows = sp.parse_results(BeautifulSoup(html, "lxml"))
    codes = [r["code"] for r in rows]
    assert "compare" not in codes              # sector link excluded
    assert codes == ["MOL", "531049"]
    assert rows[0]["name"] == "Meghmani Organics Ltd"
    assert rows[0]["date"] == "06 Jun 2026"
    assert sp.detect_category(rows[0]["snippet"]) == "Open Offer"
