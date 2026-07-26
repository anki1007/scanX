"""Normalizer tests against fixture payloads shaped like the documented API."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest

from quantlab.client import ENDPOINTS
from quantlab.normalize import NORMALIZERS, normalize, to_float

ISIN = "INE002A01018"
FETCHED = datetime(2026, 7, 27, 12, 0, 0)


def test_every_endpoint_has_a_normalizer():
    assert set(NORMALIZERS) == set(ENDPOINTS)


def test_to_float_tolerates_messy_inputs():
    assert to_float(12) == 12.0
    assert to_float("4.39%") == 4.39
    assert to_float("2,39,491") == 239491.0
    assert to_float("-0.09") == -0.09
    assert to_float(None) is None
    assert to_float("n/a") is None
    assert to_float(True) is None


# ---------------------------------------------------------------------------
# company profile
# ---------------------------------------------------------------------------
def test_company_profile_row():
    payload = {
        "status": "success",
        "data": {
            "company_profile": "Reliance Industries is ...",
            "sector": "Refineries",
            "sector_market_cap_inr": {"value": 2500000, "unit": "crore", "formatted": "25L Cr"},
            "sector_market_cap_usd": {"value": 300, "unit": "billion", "formatted": "$300B"},
        },
    }
    ds = normalize("company_profile", ISIN, payload, {}, FETCHED)
    assert ds.table == "company_profile"
    assert ds.scope == {"isin": ISIN}
    assert len(ds.rows) == 1
    row = dict(zip(ds.columns, ds.rows[0]))
    assert row["isin"] == ISIN
    assert row["sector"] == "Refineries"
    assert row["sector_mcap_inr"] == 2500000.0
    assert row["sector_mcap_usd_unit"] == "billion"


def test_company_profile_missing_fields_tolerated():
    ds = normalize("company_profile", ISIN, {"status": "success", "data": {}}, {}, FETCHED)
    row = dict(zip(ds.columns, ds.rows[0]))
    assert row["company_profile"] is None
    assert row["sector_mcap_inr"] is None


# ---------------------------------------------------------------------------
# balance sheet (summary + full statement)
# ---------------------------------------------------------------------------
BALANCE_PAYLOAD = {
    "status": "success",
    "data": {
        "type": "consolidated",
        "time_period": "yearly",
        "units_in": "crore",
        "history": [
            {"total_asset": 1755986, "total_liability": 1755986, "period": "Mar 2025"},
            {"total_asset": 1607431, "total_liability": 1607431, "period": "Mar 2024"},
        ],
        "full_statement": [
            {
                "particular": "Share Capital",
                "history": [
                    {"period": "Mar 2025", "value": 13532},
                    {"period": "Mar 2024", "value": 6766},
                ],
            },
            {"particular": "Empty Line", "history": []},
        ],
    },
}


def test_balance_sheet_rows_and_scope():
    params = {"type": "consolidated", "fs": "true"}
    ds = normalize("balance_sheet", ISIN, BALANCE_PAYLOAD, params, FETCHED)
    assert ds.table == "balance_sheet"
    assert ds.scope == {"isin": ISIN, "statement_type": "consolidated"}
    summary = [r for r in ds.rows if r[2] == "summary"]
    full = [r for r in ds.rows if r[2] == "full"]
    assert len(summary) == 4          # 2 periods x (total_asset, total_liability)
    assert len(full) == 2             # empty history contributes nothing
    row = dict(zip(ds.columns, full[0]))
    assert row["particular"] == "Share Capital"
    assert row["period"] == "Mar 2025"
    assert row["value"] == 13532.0
    assert row["units_in"] == "crore"


def test_balance_sheet_tolerates_malformed_items():
    payload = {
        "status": "success",
        "data": {
            "history": [{"period": None, "total_asset": 1}, "junk", {"total_asset": 2}],
            "full_statement": [{"history": [{"period": "Mar 2025", "value": 1}]}, 42],
        },
    }
    ds = normalize("balance_sheet", ISIN, payload, {"type": "standalone"}, FETCHED)
    # rows without a period or label are skipped, junk items ignored
    assert all(r[4] is not None for r in ds.rows)
    assert ds.scope["statement_type"] == "standalone"


# ---------------------------------------------------------------------------
# cash flow / income statement
# ---------------------------------------------------------------------------
def test_cash_flow_categories_and_full():
    payload = {
        "status": "success",
        "data": {
            "type": "consolidated",
            "units_in": "crore",
            "cash_flow": [
                {
                    "category": "operating",
                    "history": [{"value": 158788, "period": "Mar 2025", "change": "+12%"}],
                },
                {"category": "investing", "history": [{"value": -120000, "period": "Mar 2025"}]},
            ],
            "full_statement": [
                {"particular": "Net Cash Flow", "history": [{"period": "Mar 2025", "value": 5000}]},
            ],
        },
    }
    ds = normalize("cash_flow", ISIN, payload, {"type": "consolidated"}, FETCHED)
    assert len(ds.rows) == 3
    cat = dict(zip(ds.columns, ds.rows[0]))
    assert cat["section"] == "category"
    assert cat["particular"] == "operating"
    assert cat["change"] == "+12%"


def test_income_statement_includes_time_period_in_scope():
    payload = {
        "status": "success",
        "data": {
            "type": "consolidated",
            "time_period": "quarterly",
            "units_in": "crore",
            "income_statement": [
                {
                    "category": "revenue",
                    "history": [
                        {"value": 1086181, "period": "Jun 2026", "change": "+10.53%"},
                        {"value": 982671, "period": "Mar 2026"},
                    ],
                },
                {"category": "net_profit", "history": [{"value": 95610, "period": "Jun 2026"}]},
            ],
            "full_statement": [
                {"particular": "EPS - Basic", "history": [{"period": "Jun 2026", "value": 70.1}]},
            ],
        },
    }
    params = {"type": "consolidated", "time_period": "quarterly", "fs": "true"}
    ds = normalize("income_statement", ISIN, payload, params, FETCHED)
    assert ds.scope == {
        "isin": ISIN, "statement_type": "consolidated", "time_period": "quarterly",
    }
    assert len(ds.rows) == 4
    row = dict(zip(ds.columns, ds.rows[0]))
    assert row["time_period"] == "quarterly"
    assert row["particular"] == "revenue"
    assert row["value"] == 1086181.0


# ---------------------------------------------------------------------------
# share holdings / key ratios
# ---------------------------------------------------------------------------
def test_share_holdings_flatten():
    payload = {
        "status": "success",
        "data": [
            {
                "category": "promoters",
                "history": [
                    {"period": "Mar 2026", "value": 50.11},
                    {"period": "Dec 2025", "value": 50.13},
                ],
            },
            {"category": "fii", "history": [{"period": "Mar 2026", "value": 19.16}]},
            {"category": "broken", "history": "not-a-list"},
        ],
    }
    ds = normalize("share_holdings", ISIN, payload, {}, FETCHED)
    assert ds.table == "share_holdings"
    assert len(ds.rows) == 3
    row = dict(zip(ds.columns, ds.rows[0]))
    assert row["category"] == "promoters"
    assert row["holding_pct"] == 50.11


def test_key_ratios_parse_percent_and_keep_raw():
    payload = {
        "status": "success",
        "data": [
            {"name": "P/E", "company_value": "20.15", "sector_value": "12.46"},
            {"name": "ROE", "company_value": "8.94%", "sector_value": None},
        ],
    }
    ds = normalize("key_ratios", ISIN, payload, {}, FETCHED)
    rows = [dict(zip(ds.columns, r)) for r in ds.rows]
    assert rows[0]["ratio_name"] == "P/E"
    assert rows[0]["company_value"] == 20.15
    assert rows[1]["company_value"] == 8.94
    assert rows[1]["company_value_raw"] == "8.94%"
    assert rows[1]["sector_value"] is None


# ---------------------------------------------------------------------------
# corporate actions / competitors
# ---------------------------------------------------------------------------
def test_corporate_actions_event_details_roundtrip():
    payload = {
        "status": "success",
        "data": [
            {
                "name": "Dividend",
                "expiry_date": "14 Aug 2025",
                "amount": 5.5,
                "ratio": None,
                "event_details": [
                    {"name": "Record date", "value": "14 Aug 2025"},
                    {"name": "Dividend type", "value": "Final"},
                ],
            }
        ],
    }
    ds = normalize("corporate_actions", ISIN, payload, {}, FETCHED)
    row = dict(zip(ds.columns, ds.rows[0]))
    assert row["action_type"] == "Dividend"
    assert row["amount"] == 5.5
    details = json.loads(row["event_details_json"])
    assert {"name": "Dividend type", "value": "Final"} in details


def test_competitors_extract_isin_from_instrument_key():
    payload = {
        "status": "success",
        "data": [
            {
                "instrument_key": "NSE_EQ|INE242A01010",
                "company_profile": "IOC is ...",
                "sector": "Refineries",
                "sector_market_cap_inr": {"value": 2500000, "unit": "crore"},
            },
            {"instrument_key": None, "sector": "Unknown"},
        ],
    }
    ds = normalize("competitors", ISIN, payload, {}, FETCHED)
    rows = [dict(zip(ds.columns, r)) for r in ds.rows]
    assert rows[0]["competitor_instrument_key"] == "NSE_EQ|INE242A01010"
    assert rows[0]["competitor_isin"] == "INE242A01010"
    assert rows[1]["competitor_isin"] is None


def test_unknown_endpoint_raises():
    with pytest.raises(ValueError, match="no normalizer"):
        normalize("nope", ISIN, {}, {}, FETCHED)


def test_empty_or_error_payloads_yield_empty_datasets():
    for endpoint in NORMALIZERS:
        ds = normalize(endpoint, ISIN, {"status": "error"}, {}, FETCHED)
        assert ds.rows == [] or endpoint == "company_profile" and len(ds.rows) <= 1
        assert ds.scope["isin"] == ISIN
