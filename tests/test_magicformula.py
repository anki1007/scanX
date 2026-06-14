import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel import magicformula as mf


def _rows():
    # name, roce, ev (mcap for tie-breaks)
    return [
        {"code": "A", "name": "Alpha",   "roce": 140.0, "ev": None, "ev_ebitda": 1.7, "mcap": 100},
        {"code": "B", "name": "Beta",    "roce": 89.0,  "ev_ebitda": 0.02, "mcap": 50},
        {"code": "C", "name": "Gamma",   "roce": 57.0,  "ev_ebitda": 3.9,  "mcap": 80},
        {"code": "D", "name": "Delta",   "roce": 26.0,  "ev_ebitda": 5.0,  "mcap": 35000},
        {"code": "E", "name": "BadEV",   "roce": 30.0,  "ev_ebitda": None, "mcap": 10},
        {"code": "F", "name": "NegRoce", "roce": -5.0,  "ev_ebitda": 2.0,  "mcap": 10},
        {"code": "A", "name": "Alpha dup", "roce": 140.0, "ev_ebitda": 1.7, "mcap": 100},
    ]


def test_compute_ranks_and_total():
    out = mf.compute(_rows())
    # invalid rows dropped (missing/negative), dup code dropped
    assert [r["code"] for r in sorted(out, key=lambda r: r["code"])] == ["A", "B", "C", "D"]
    by = {r["code"]: r for r in out}
    # ROCE ranks: A(140)=1, B(89)=2, C(57)=3, D(26)=4
    assert [by[c]["r_roce"] for c in "ABCD"] == [1, 2, 3, 4]
    # EV ranks: B(0.02)=1, A(1.7)=2, C(3.9)=3, D(5.0)=4
    assert [by[c]["r_ev"] for c in "ABCD"] == [2, 1, 3, 4]
    assert by["A"]["r_total"] == 3 and by["B"]["r_total"] == 3
    # sorted by total rank; A vs B tie broken by bigger mcap first
    assert [r["code"] for r in out][:2] == ["A", "B"]
    assert out[-1]["code"] == "D"


def test_compute_tie_same_roce():
    rows = [
        {"code": "X", "name": "X", "roce": 50.0, "ev_ebitda": 2.0, "mcap": 10},
        {"code": "Y", "name": "Y", "roce": 50.0, "ev_ebitda": 2.0, "mcap": 999},
    ]
    out = mf.compute(rows)
    by = {r["code"]: r for r in out}
    assert by["Y"]["r_roce"] == 1 and by["X"]["r_roce"] == 2   # bigger mcap wins tie


def test_sector_enrichment_and_fin_flag():
    def sector_of(code, name):
        return {"name": "Financial Services", "label": "NEUTRAL", "score": 0.1} if code == "B" \
            else {"name": "Capital Goods", "label": "TAILWIND", "score": 0.9}
    out = mf.compute(_rows(), sector_of=sector_of)
    by = {r["code"]: r for r in out}
    assert by["A"]["sector"] == "Capital Goods" and by["A"]["sec_sig"] == "TAILWIND"
    assert by["B"]["fin"] == 1 and by["A"]["fin"] == 0


def test_is_financial():
    assert mf.is_financial("Banks")
    assert mf.is_financial("Financial Services")
    assert mf.is_financial(None, "Shri Housing Finance Ltd")
    assert not mf.is_financial("Capital Goods", "Bharat Forge")


def test_sector_summary():
    def sector_of(code, name):
        return {"name": "Metals", "label": "HEADWIND", "score": -1.2}
    out = mf.compute(_rows(), sector_of=sector_of)
    s = mf.sector_summary(out)
    assert s[0]["sector"] == "Metals" and s[0]["n"] == 4 and s[0]["signal"] == "HEADWIND"


def test_compute_empty():
    assert mf.compute([]) == []
