"""The screen / market-page table reader.

In early September 2026 the vendor renamed the first column header from "Name"
to "Company". The reader required a cell exactly equal to "Name", found none,
and returned zero rows from every screen and every /market/ page. Sector
tailwind, IV ranking, return maps, fair value and magic formula froze for three
weeks, and the steps that read them still exited 0 because "no rows" looked
the same as "nothing matched".

These pin both halves: the current header parses, and a header that cannot be
read raises instead of passing for an empty market. No network: the fetch is
replaced with fixed HTML.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

bs4 = pytest.importorskip("bs4")
from earnings_intel.data import screener as sc  # noqa: E402


def _page(header, rows, table_class="data-table"):
    head = "".join(f"<th>{h}</th>" for h in header)
    body = ""
    for code, name, *vals in rows:
        tds = f"<td>1.</td><td><a href=\"/company/{code}/consolidated/\">{name}</a></td>"
        tds += "".join(f"<td>{v}</td>" for v in vals)
        body += f"<tr>{tds}</tr>"
    return (f"<html><body><table class=\"{table_class}\"><tr>{head}</tr>{body}"
            f"</table></body></html>")


# The layout the vendor serves as of September 2026.
CURRENT = ["S.No.", "Company", "CMP Rs.", "P/E", "Mar Cap Rs.Cr.", "Div Yld %",
           "NP Qtr Rs.Cr.", "Qtr Profit Var %", "Sales Qtr Rs.Cr.", "Qtr Sales Var %",
           "ROCE %"]
ROWS = [("SOLARINDS", "Solar Industries", "19730.00", "89.65", "178536.88", "0.06",
         "666.37", "92.66", "3668.20", "70.26", "38.08"),
        ("SRF", "SRF", "2550.00", "33.73", "75588.33", "0.28",
         "400.10", "75.53", "3500.00", "31.81", "14.61")]


def _client(monkeypatch, pages):
    """A client whose fetch returns the given HTML pages in order, then nothing."""
    c = sc.ScreenerClient(delay=0)
    it = iter(pages)

    def fake_get(url, retries=3):
        html = next(it, None)
        return None if html is None else bs4.BeautifulSoup(html, "lxml")
    monkeypatch.setattr(c, "_get", fake_get)
    return c


def test_the_current_company_header_is_read(monkeypatch):
    rows = _client(monkeypatch, [_page(CURRENT, ROWS)]).fetch_market("IN01/IN0101", max_pages=3)
    assert [r["code"] for r in rows] == ["SOLARINDS", "SRF"]
    solar = rows[0]
    assert solar["cmp"] == 19730.0 and solar["pe"] == 89.65
    assert solar["mcap"] == 178536.88
    assert solar["profit_var"] == 92.66 and solar["sales_var"] == 70.26
    assert solar["roce"] == 38.08


def test_the_old_name_header_still_reads(monkeypatch):
    """Older cached pages and any rollback on the vendor side."""
    old = ["S.No.", "Name"] + CURRENT[2:]
    rows = _client(monkeypatch, [_page(old, ROWS)]).fetch_screen("x", max_pages=2)
    assert len(rows) == 2


def test_an_unreadable_header_on_a_populated_table_raises(monkeypatch):
    """The failure that shipped: a table full of companies read as empty."""
    renamed = ["#", "Stock", "Last", "PE"] + CURRENT[4:]
    c = _client(monkeypatch, [_page(renamed, ROWS)])
    with pytest.raises(sc.ScreenLayoutError) as e:
        c.fetch_market("IN01/IN0101", max_pages=2)
    assert "Stock" in str(e.value), "the error must say what the header now reads"


def test_a_genuinely_empty_screen_is_empty_not_an_error(monkeypatch):
    """No company rows is a legitimate answer to a narrow query."""
    html = "<html><body><table class='data-table'><tr><th>Name</th><th>CMP Rs.</th></tr></table></body></html>"
    assert _client(monkeypatch, [html]).fetch_screen("x", max_pages=2) == []


def test_a_price_to_book_column_is_not_mistaken_for_price(monkeypatch):
    """A bare "price" prefix would bind CMP to the wrong column."""
    hdr = ["S.No.", "Company", "Price to Book", "CMP Rs.", "P/E"]
    rows = [("ABC", "Abc Ltd", "3.10", "250.00", "12.00")]
    got = _client(monkeypatch, [_page(hdr, rows)]).fetch_screen("x", max_pages=2)
    assert got[0]["cmp"] == 250.0


def test_pagination_stops_when_a_page_adds_nothing(monkeypatch):
    rows = _client(monkeypatch, [_page(CURRENT, ROWS), _page(CURRENT, ROWS)]).fetch_market(
        "IN01/IN0101", max_pages=5)
    assert len(rows) == 2, "a repeated page must not duplicate or loop"


def test_fetch_sectors_surfaces_a_layout_change_once(monkeypatch):
    """Not 22 per-industry warnings and an empty market."""
    from earnings_intel.data import sectors

    def boom(self, code, max_pages=200):
        raise sc.ScreenLayoutError("header unrecognised: ['#', 'Stock']")
    monkeypatch.setattr(sc.ScreenerClient, "fetch_market", boom)
    with pytest.raises(sc.ScreenLayoutError):
        sectors.fetch_sectors(delay=0)
