"""Unit tests for the Screener Documents scraper (earnings_intel/data/documents.py)."""
import sys, types
from datetime import date, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import documents as dc   # noqa: E402

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

BASE = "https://www.screener.in/company/TATAMOTORS/"

# mirrors the live Screener company-page "Documents" section
FULL_HTML = """
<section id="documents" class="card card-large">
  <h2>Documents</h2>
  <div class="flex-row flex-space-between">

    <div class="documents announcements flex-column">
      <h3 class="margin-0">Announcements</h3>
      <div class="show-more-box"><ul class="list-links">
        <li><a href="https://www.bseindia.com/xml-data/corpfiling/AttachLive/aaa.pdf"
               target="_blank" rel="noopener noreferrer">
              Board Meeting Outcome for Audited Financial Results
              <div class="ink-600 smaller"><span>2 May 2026</span></div></a></li>
        <li><a href="/announcements/bbb/">
              Investor Presentation Q4 FY26
              <div class="ink-600 smaller"><span>29 Apr 2026</span></div></a></li>
      </ul></div>
    </div>

    <div class="documents annual-reports flex-column">
      <h3 class="margin-0">Annual reports</h3>
      <ul class="list-links">
        <li><a href="https://www.bseindia.com/bseplus/AnnualReport/500570/ar2025.pdf"
               target="_blank">Financial Year 2025
              <span class="ink-600 smaller">from bse</span></a></li>
        <li><a href="/company/source/ar-2024.pdf">Financial Year 2024
              <span class="ink-600 smaller">from bse</span></a></li>
      </ul>
    </div>

    <div class="documents concalls flex-column">
      <h3 class="margin-0">Concalls</h3>
      <ul class="list-links">
        <li class="flex flex-gap-8 flex-wrap">
          <div class="ink-600 font-size-15 nowrap">May 2026</div>
          <a class="concall-link" href="https://www.bseindia.com/concall/may26-transcript.pdf"
             target="_blank">Transcript</a>
          <button class="button-small concall-link">AI Summary</button>
          <a class="concall-link" href="/concall/ppt/may-2026.pdf">PPT</a>
          <a class="concall-link" href="https://www.bseindia.com/concall/may26.mp3">REC</a>
        </li>
        <li class="flex flex-gap-8 flex-wrap">
          <div class="ink-600 font-size-15 nowrap">Feb 2026</div>
          <a class="concall-link" href="https://www.bseindia.com/concall/feb26-transcript.pdf">Transcript</a>
          <button class="button-small concall-link">AI Summary</button>
        </li>
      </ul>
    </div>

  </div>
</section>
"""


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _parse(html=FULL_HTML, base=BASE):
    return dc.parse_documents_soup(_soup(html), base)


def _by_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


# ----------------------------------------------------------------- concalls
def test_concall_row_yields_transcript_and_ppt():
    if BeautifulSoup is None:
        return
    docs = _parse()
    may = [d for d in docs if d["date"] == "2026-05-01"]
    assert sorted(d["kind"] for d in may) == ["concall_ppt", "concall_transcript"]
    tr = _by_kind(may, "concall_transcript")[0]
    assert tr["url"] == "https://www.bseindia.com/concall/may26-transcript.pdf"
    assert tr["title"] == "May 2026 Concall Transcript"
    assert tr["source"] == "bse"


def test_concall_month_label_normalised_to_iso_first_of_month():
    if BeautifulSoup is None:
        return
    dates = {d["date"] for d in _parse() if d["kind"].startswith("concall_")}
    assert dates == {"2026-05-01", "2026-02-01"}
    assert dc._iso_month("May 2026") == "2026-05-01"
    assert dc._iso_month("Sept 2025") == "2025-09-01"
    assert dc._iso_month("no period here") == ""


def test_concall_skips_ai_summary_and_rec():
    if BeautifulSoup is None:
        return
    docs = _parse()
    urls = " ".join(d["url"] for d in docs).lower()
    assert ".mp3" not in urls                      # REC / audio dropped
    assert all("summary" not in d["title"].lower() for d in docs)
    assert len(_by_kind(docs, "concall_transcript")) == 2
    assert len(_by_kind(docs, "concall_ppt")) == 1


def test_concall_notes_screener_internal_skipped_external_kept():
    if BeautifulSoup is None:
        return
    html = """
    <div class="documents concalls"><h3>Concalls</h3><ul class="list-links">
      <li><div class="ink-600">Jan 2026</div>
        <a href="https://www.screener.in/concall-notes/?id=99">Notes</a>
        <a href="https://www.bseindia.com/concall/jan26-notes.pdf">Notes</a></li>
    </ul></div>
    """
    docs = _parse(html)
    assert [(d["kind"], d["url"]) for d in docs] == [
        ("concall_notes", "https://www.bseindia.com/concall/jan26-notes.pdf")]
    assert docs[0]["date"] == "2026-01-01"


# ----------------------------------------------------------- annual reports
def test_annual_report_parsed_with_fy_end_date():
    if BeautifulSoup is None:
        return
    ars = _by_kind(_parse(), "annual_report")
    assert [a["title"] for a in ars] == ["Financial Year 2025", "Financial Year 2024"]
    assert [a["date"] for a in ars] == ["2025-03-31", "2024-03-31"]
    assert ars[0]["url"] == "https://www.bseindia.com/bseplus/AnnualReport/500570/ar2025.pdf"
    assert ars[0]["source"] == "bse"


def test_fy_iso_variants():
    assert dc._fy_iso("Financial Year 2025") == "2025-03-31"
    assert dc._fy_iso("FY 2024-25") == "2025-03-31"
    assert dc._fy_iso("Annual Report") == ""


# ------------------------------------------------------------ announcements
def test_announcement_parsed():
    if BeautifulSoup is None:
        return
    anns = _by_kind(_parse(), "announcement")
    assert anns[0]["title"] == "Board Meeting Outcome for Audited Financial Results"
    assert anns[0]["date"] == "2026-05-02"
    assert anns[0]["source"] == "bse"
    assert anns[1]["title"] == "Investor Presentation Q4 FY26"
    assert anns[1]["date"] == "2026-04-29"


def test_announcement_relative_date_badge():
    if BeautifulSoup is None:
        return
    html = """
    <div class="documents announcements"><h3>Announcements</h3><ul class="list-links">
      <li><a href="/ann/1/">Trading window closure<div class="smaller"><span>3d</span></div></a></li>
      <li><a href="/ann/2/">Analyst meet intimation<div class="smaller"><span>13h</span></div></a></li>
    </ul></div>
    """
    docs = _parse(html)
    today = date.today()
    got = {d["title"]: d["date"] for d in docs}
    assert got["Analyst meet intimation"] == today.isoformat()
    assert got["Trading window closure"] == (today - timedelta(days=3)).isoformat()


def test_iso_date_helper():
    today = date(2026, 5, 10)
    assert dc._iso_date("2 May 2026", today) == "2026-05-02"
    assert dc._iso_date("2026-05-02", today) == "2026-05-02"
    assert dc._iso_date("25 Apr", today) == "2026-04-25"
    assert dc._iso_date("25 Dec", today) == "2025-12-25"     # future -> previous year
    assert dc._iso_date("", today) == ""
    assert dc._iso_date("no date at all", today) == ""


# -------------------------------------------------------------------- misc
def test_relative_hrefs_resolved_to_absolute():
    if BeautifulSoup is None:
        return
    docs = _parse()
    assert all(d["url"].startswith("https://") for d in docs)
    ppt = _by_kind(docs, "concall_ppt")[0]
    assert ppt["url"] == "https://www.screener.in/concall/ppt/may-2026.pdf"
    rel_ann = [d for d in docs if d["title"] == "Investor Presentation Q4 FY26"][0]
    assert rel_ann["url"] == "https://www.screener.in/announcements/bbb/"
    assert rel_ann["source"] == "screener"


def test_missing_panels_are_tolerated():
    if BeautifulSoup is None:
        return
    assert _parse("<html><body><h1>Nothing here</h1></body></html>") == []
    only_ar = """
    <div class="documents annual-reports"><h3>Annual reports</h3><ul class="list-links">
      <li><a href="/ar/2026.pdf">Financial Year 2026</a></li></ul></div>
    """
    docs = _parse(only_ar)
    assert [d["kind"] for d in docs] == ["annual_report"]
    assert docs[0]["url"] == "https://www.screener.in/ar/2026.pdf"


def test_duplicates_removed_by_kind_and_url():
    if BeautifulSoup is None:
        return
    html = """
    <div class="documents concalls"><h3>Concalls</h3><ul class="list-links">
      <li><div>May 2026</div>
        <a href="/c/may26.pdf">Transcript</a>
        <a href="/c/may26.pdf">Transcript</a>
        <a href="/c/may26.pdf">PPT</a></li>
      <li><div>May 2026</div><a href="/c/may26.pdf">Transcript</a></li>
    </ul></div>
    """
    docs = _parse(html)
    assert len(docs) == 2                       # same url, two distinct kinds
    assert sorted(d["kind"] for d in docs) == ["concall_ppt", "concall_transcript"]


def test_sorted_newest_first_and_contract_keys():
    if BeautifulSoup is None:
        return
    docs = _parse()
    dates = [d["date"] for d in docs]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == "2026-05-02"
    for d in docs:
        assert set(d) == {"kind", "date", "title", "url", "source"}
        assert d["kind"] in {"concall_transcript", "concall_ppt", "concall_notes",
                             "annual_report", "announcement"}
        assert d["source"] in {"screener", "bse"}


def test_panels_found_without_class_names():
    if BeautifulSoup is None:
        return
    html = """
    <section><h3>Concalls</h3>
      <ul><li><div>Mar 2026</div><a href="/c/mar26.pdf">Transcript</a></li></ul></section>
    """
    docs = _parse(html)
    assert [(d["kind"], d["date"]) for d in docs] == [("concall_transcript", "2026-03-01")]


# -------------------------------------------------------------- fetch layer
class _Resp:
    def __init__(self, status=200, text="", url=BASE):
        self.status_code = status; self.text = text; self.url = url


def test_fetch_documents_parses_page(monkeypatch):
    if BeautifulSoup is None:
        return
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(200, text=FULL_HTML)
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(dc, "_client", lambda sid: S())
    docs = dc.fetch_documents("TATAMOTORS", "sid")
    assert len(docs) == 7
    assert docs[0]["kind"] == "announcement"


def test_fetch_documents_http_error_degrades(monkeypatch):
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(503, text="")
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(dc, "_client", lambda sid: S())
    assert dc.fetch_documents("X", "sid") == []


def test_fetch_documents_network_failure_degrades(monkeypatch):
    class S:
        headers = {}
        def get(self, *a, **k): raise RuntimeError("boom")
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(dc, "_client", lambda sid: S())
    assert dc.fetch_documents("X", "sid") == []


def test_fetch_documents_empty_code_no_network():
    assert dc.fetch_documents("") == []
