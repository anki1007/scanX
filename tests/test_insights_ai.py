import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import insights_ai as ia

SRC = ("Iron Ore Production was 440.00 lakh tonnes in FY25 and 412.20 in FY24. "
       "Iron Ore Mining Capacity stood at 55.40 MTPA. Steel Production 2.70 MTPA in Q4 FY25.")

ROWS = [
    {"metric": "Iron Ore Production", "unit": "Lakh Tonnes", "freq": "yearly", "period": "FY24", "value": "412.20"},
    {"metric": "Iron Ore Production", "unit": "Lakh Tonnes", "freq": "yearly", "period": "FY25", "value": "440.00"},
    {"metric": "Iron Ore Mining Capacity", "unit": "MTPA", "freq": "yearly", "period": "FY25", "value": "55.40"},
    {"metric": "Hallucinated KPI", "unit": "x", "freq": "yearly", "period": "FY25", "value": "999.99"},   # not in src
    {"metric": "Phantom Sales", "unit": "LT", "freq": "quarterly", "period": "Q4 FY25", "value": "130"},  # not in src
    {"metric": "Steel Production", "unit": "MTPA", "freq": "quarterly", "period": "Q4 FY25", "value": "2.70"},
]


def test_grounding_drops_hallucinations():
    out = ia.verify_and_shape(ROWS, SRC)
    ymetrics = {r["metric"] for r in out["yearly"]["rows"]}
    assert "Iron Ore Production" in ymetrics and "Iron Ore Mining Capacity" in ymetrics
    assert "Hallucinated KPI" not in ymetrics            # 999.99 not in source -> dropped
    qmetrics = {r["metric"] for r in out.get("quarterly", {"rows": []})["rows"]}
    assert "Steel Production" in qmetrics                 # 2.70 grounded
    assert "Phantom Sales" not in qmetrics               # 130 not in source -> dropped


def test_shape_and_period_sort():
    out = ia.verify_and_shape(ROWS, SRC)
    y = out["yearly"]
    assert y["periods"] == ["FY24", "FY25"]               # chronological
    prod = next(r for r in y["rows"] if r["metric"] == "Iron Ore Production")
    assert prod["unit"] == "Lakh Tonnes"
    assert prod["values"]["FY25"] == "440.00" and prod["values"]["FY24"] == "412.20"


def test_grounding_with_comma_and_trailing_zero():
    src = "Order book of 1,45,774 cr; utilisation 90.00 %."
    rows = [
        {"metric": "Order Book", "unit": "cr", "freq": "yearly", "period": "FY25", "value": "1,45,774"},
        {"metric": "Utilisation", "unit": "%", "freq": "yearly", "period": "FY25", "value": "90.0"},   # src has 90.00
    ]
    out = ia.verify_and_shape(rows, src)
    m = {r["metric"] for r in out["yearly"]["rows"]}
    assert "Order Book" in m and "Utilisation" in m       # comma + trailing-zero variants matched


def test_extract_json_handles_fences_and_garbage():
    assert ia._extract_json('```json\n[{"metric":"X","value":"1"}]\n```')[0]["metric"] == "X"
    assert ia._extract_json('[{"a":1}]')[0]["a"] == 1
    assert ia._extract_json("not json at all") == []


def test_empty_inputs():
    assert ia.verify_and_shape([], "src") == {}
    assert ia.verify_and_shape(ROWS, "") != {}             # no source -> grounding skipped, keeps rows
