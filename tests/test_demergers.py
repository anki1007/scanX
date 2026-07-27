"""Tests for the Demerger Tracking board (stage rules, dates, aggregation)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import refresh_demergers as rd   # noqa: E402


# ---- classify_stage: ordered keyword rules ----------------------------------
def test_classify_stage_basics():
    assert rd.classify_stage("Scheme of arrangement for demerger of the pharma undertaking") == "ANNOUNCED"
    assert rd.classify_stage("Board approved the scheme of arrangement for demerger") == "BOARD_APPROVED"
    assert rd.classify_stage("NCLT sanctions the scheme of arrangement (demerger)") == "NCLT"
    assert rd.classify_stage("Hon'ble National Company Law Tribunal convened meeting") == "NCLT"
    assert rd.classify_stage("Record date fixed for the demerger entitlement") == "RECORD_DATE"
    assert rd.classify_stage("Listing of equity shares of the demerged company") == "LISTED"
    assert rd.classify_stage("") == "ANNOUNCED"
    assert rd.classify_stage(None) == "ANNOUNCED"


def test_classify_stage_ordering():
    # record date beats NCLT
    assert rd.classify_stage("NCLT approved the scheme; record date fixed as 15 July 2026") == "RECORD_DATE"
    # listing wins over everything
    assert rd.classify_stage("Shares listed post demerger; record date was 12 May, "
                             "NCLT sanctioned, board approved earlier") == "LISTED"
    # NCLT beats board approval
    assert rd.classify_stage("Board approved; petition filed with NCLT") == "NCLT"
    # 'approved' without 'board' (e.g. by shareholders) stays ANNOUNCED
    assert rd.classify_stage("Scheme of arrangement approved by shareholders") == "ANNOUNCED"


def test_classify_stage_ignores_lodr_boilerplate():
    # SEBI (Listing Obligations and Disclosure Requirements) must not read as LISTED
    t = ("Regulation 30 of SEBI (Listing Obligations and Disclosure Requirements) "
         "Regulations - Board approved the scheme of arrangement for demerger")
    assert rd.classify_stage(t) == "BOARD_APPROVED"
    # 'delisting' must not read as LISTED either
    assert rd.classify_stage("Voluntary delisting of equity shares NCLT") == "NCLT"


# ---- date extraction ---------------------------------------------------------
def test_extract_date():
    assert rd.extract_date("record date fixed as 15 July 2026") == "2026-07-15"
    assert rd.extract_date("record date is July 5, 2026") == "2026-07-05"
    assert rd.extract_date("effective 2026-03-01") == "2026-03-01"
    assert rd.extract_date("w.e.f. 01/08/2026") == "2026-08-01"
    assert rd.extract_date("6 Jun 2026") == "2026-06-06"
    assert rd.extract_date("no date in here") is None
    assert rd.extract_date("") is None


def test_iso_normalises_screener_dates():
    assert rd._iso("06 Jun 2026") == "2026-06-06"
    assert rd._iso("2026-03-01") == "2026-03-01"
    assert rd._iso(None) is None
    assert rd._iso("garbage") is None


# ---- per-company aggregation ---------------------------------------------------
def _item(code, stage, date, text, **kw):
    d = {"code": code, "name": f"{code} Ltd", "stage": stage,
         "date": date, "text": text, "url": f"https://x/{code}"}
    d.update(kw)
    return d


def test_aggregate_picks_furthest_stage():
    items = [
        _item("ABC", "ANNOUNCED", "01 Jan 2026", "scheme of arrangement demerger"),
        _item("ABC", "NCLT", "01 Mar 2026", "NCLT approved the demerger"),
        _item("ABC", "BOARD_APPROVED", "01 Feb 2026", "board approved the scheme"),
        _item("XYZ", "LISTED", "05 Mar 2026", "listing of demerged shares"),
    ]
    rows = rd.aggregate(items)
    assert [r["code"] for r in rows] == ["XYZ", "ABC"]        # most advanced first
    abc = rows[1]
    assert abc["stage"] == "NCLT"                              # furthest stage seen
    assert abc["stage_date"] == "2026-03-01"                   # date of that stage
    assert abc["name"] == "ABC Ltd"
    assert abc["headlines"][0]["date"] == "01 Mar 2026"        # newest headline first
    assert abc["headlines"][0]["url"] == "https://x/ABC"


def test_aggregate_prefers_extracted_stage_date():
    items = [_item("PQR", "RECORD_DATE", "01 Jun 2026",
                   "record date fixed as 15 July 2026", stage_date="2026-07-15")]
    rows = rd.aggregate(items)
    assert rows[0]["stage_date"] == "2026-07-15"


def test_aggregate_keeps_newest_six_headlines_and_dedups():
    items = [_item("A", "ANNOUNCED", f"{d:02d} Jan 2026", f"filing {d}") for d in range(1, 10)]
    items.append(_item("A", "ANNOUNCED", "09 Jan 2026", "filing 9"))   # exact dup text
    rows = rd.aggregate(items)
    assert len(rows) == 1
    hl = rows[0]["headlines"]
    assert len(hl) == 6
    assert hl[0]["text"] == "filing 9"
    assert [h["text"] for h in hl] == [f"filing {d}" for d in range(9, 3, -1)]


def test_aggregate_sorts_same_stage_by_newest_date():
    items = [
        _item("OLD", "NCLT", "01 Jan 2026", "NCLT order old"),
        _item("NEW", "NCLT", "01 May 2026", "NCLT order new"),
    ]
    rows = rd.aggregate(items)
    assert [r["code"] for r in rows] == ["NEW", "OLD"]


def test_aggregate_skips_blank_codes():
    rows = rd.aggregate([_item("", "NCLT", "01 Jan 2026", "x"),
                         _item("OK", "ANNOUNCED", "02 Jan 2026", "y")])
    assert [r["code"] for r in rows] == ["OK"]
