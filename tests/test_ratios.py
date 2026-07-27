"""Upstox ratio-source tests — fixture payloads only, no network, no token."""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from earnings_intel.data import ratios as R  # noqa: E402
from upstox_lab.config import TOKEN_ENV_VAR, UpstoxLabSettings  # noqa: E402

ISIN = "INE002A01018"


# ---------------------------------------------------------------------------
# fixtures shaped like the documented responses
# ---------------------------------------------------------------------------
def key_ratios_payload(entries=None) -> dict:
    """``GET /v2/fundamentals/{isin}/key-ratios`` — values are STRINGS."""
    if entries is None:
        entries = [
            {"name": "P/E", "company_value": "20.15", "sector_value": "12.46"},
            {"name": "P/B", "company_value": "2.31", "sector_value": "1.88"},
            {"name": "ROA", "company_value": "4.39%", "sector_value": "6.02%"},
            {"name": "ROE", "company_value": "8.94%", "sector_value": "16.46%"},
            {"name": "ROCE", "company_value": "11.20%", "sector_value": "14.03%"},
            {"name": "EV/EBITDA", "company_value": "9.77", "sector_value": "8.10"},
        ]
    return {"status": "success", "data": entries}


def balance_sheet_payload(full_statement) -> dict:
    """``GET /v2/fundamentals/{isin}/balance-sheet?type=consolidated&fs=true``."""
    return {
        "status": "success",
        "data": {
            "type": "consolidated",
            "units_in": "crore",
            "history": [{"total_asset": 1700.0, "total_liability": 1700.0,
                         "period": "2026-03-31"}],
            "full_statement": full_statement,
        },
    }


def full_statement(**overrides) -> list[dict]:
    """The eight documented particulars over three yearly periods."""
    rows = [
        {"particular": "Non-Current Assets",
         "2026-03-31": "1,380", "2025-03-31": "1,250", "2024-03-31": "1,100"},
        {"particular": "Current Assets",
         "2026-03-31": "320", "2025-03-31": "300", "2024-03-31": "250"},
        {"particular": "Total Assets",
         "2026-03-31": "1,700", "2025-03-31": "1,550", "2024-03-31": "1,350"},
        {"particular": "Current Liabilities",
         "2026-03-31": "160", "2025-03-31": "200", "2024-03-31": "125"},
        {"particular": "Net Current Asset",
         "2026-03-31": "160", "2025-03-31": "100", "2024-03-31": "125"},
        {"particular": "Non-Current Liabilities",
         "2026-03-31": "540", "2025-03-31": "500", "2024-03-31": "480"},
        {"particular": "Equity Capital",
         "2026-03-31": "1,000", "2025-03-31": "850", "2024-03-31": "745"},
        {"particular": "Total Equity & Liabilities",
         "2026-03-31": "1,700", "2025-03-31": "1,550", "2024-03-31": "1,350"},
    ]
    for row in rows:
        patch = overrides.get(row["particular"])
        if patch is not None:
            row.update(patch)
    return rows


def _settings(tmp_path) -> UpstoxLabSettings:
    """Settings that cannot reach a token or a cached instrument master."""
    return UpstoxLabSettings(
        token_file=tmp_path / "absent-token.txt",
        instruments_cache=tmp_path / "NSE.json.gz",
    )


class StubClient:
    """Stands in for UpstoxFundamentalsClient with canned payloads."""

    def __init__(self, key_ratios=None, balance_sheet=None):
        self._key_ratios = key_ratios
        self._balance_sheet = balance_sheet
        self.calls: list[tuple[str, str]] = []

    def get_key_ratios(self, isin):
        self.calls.append(("key_ratios", isin))
        if isinstance(self._key_ratios, Exception):
            raise self._key_ratios
        return self._key_ratios

    def get_balance_sheet(self, isin, **kwargs):
        self.calls.append(("balance_sheet", isin))
        if isinstance(self._balance_sheet, Exception):
            raise self._balance_sheet
        return self._balance_sheet


class DeadSession:
    """Any network call fails — proves the tests never touch the wire."""

    def get(self, *args, **kwargs):
        raise ConnectionError("offline")


def _numbers(node):
    """Every float anywhere in a nested result, for finiteness assertions."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _numbers(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _numbers(value)
    elif isinstance(node, float):
        yield node


# ---------------------------------------------------------------------------
# parse_ratio_value
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8.94%", (8.94, "pct")),
        ("16.46 %", (16.46, "pct")),
        ("-2.50%", (-2.5, "pct")),
        ("20.15", (20.15, "x")),
        ("1,234.5", (1234.5, "x")),
        ("-0.09", (-0.09, "x")),
        (12, (12.0, "x")),
        (3.5, (3.5, "x")),
    ],
)
def test_parse_ratio_value_reads_percentages_and_multiples(raw, expected):
    assert R.parse_ratio_value(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "-", "--", "n/a", "NA", "nil", None, "abc", True])
def test_parse_ratio_value_junk_is_no_value(raw):
    assert R.parse_ratio_value(raw) == (None, "")


# ---------------------------------------------------------------------------
# ratios_from_key_ratios
# ---------------------------------------------------------------------------
def test_all_six_documented_names_are_mapped_with_sector_values():
    out = R.ratios_from_key_ratios(key_ratios_payload())
    assert set(out) == {"pe", "pb", "roa", "roe", "roce", "ev_ebitda"}
    assert out["pe"] == {"value": 20.15, "unit": "x", "sector": 12.46,
                         "source": "upstox:key-ratios"}
    assert out["roe"] == {"value": 8.94, "unit": "pct", "sector": 16.46,
                          "source": "upstox:key-ratios"}
    assert out["ev_ebitda"]["value"] == 9.77
    assert out["roce"]["unit"] == "pct"
    assert all(entry["source"] == "upstox:key-ratios" for entry in out.values())


def test_unknown_ratio_name_is_ignored_not_fatal():
    out = R.ratios_from_key_ratios(key_ratios_payload([
        {"name": "P/E", "company_value": "20.15", "sector_value": "12.46"},
        {"name": "Dividend Yield", "company_value": "1.20%", "sector_value": "0.80%"},
        {"name": None, "company_value": "5", "sector_value": "5"},
        "not-a-mapping",
    ]))
    assert set(out) == {"pe"}


def test_key_ratio_name_spacing_is_tolerated():
    out = R.ratios_from_key_ratios(key_ratios_payload([
        {"name": " EV / EBITDA ", "company_value": "9.77", "sector_value": "8.10"},
    ]))
    assert out["ev_ebitda"]["value"] == 9.77


def test_key_ratio_without_company_value_is_dropped_and_missing_sector_is_none():
    out = R.ratios_from_key_ratios(key_ratios_payload([
        {"name": "P/E", "company_value": "-", "sector_value": "12.46"},
        {"name": "P/B", "company_value": "2.31", "sector_value": ""},
    ]))
    assert "pe" not in out
    assert out["pb"]["sector"] is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"status": "success"}, {"status": "success", "data": None},
     {"status": "success", "data": {"oops": 1}}, {"data": "garbage"}],
)
def test_malformed_key_ratio_payloads_return_empty(payload):
    assert R.ratios_from_key_ratios(payload) == {}


# ---------------------------------------------------------------------------
# ratios_from_balance_sheet
# ---------------------------------------------------------------------------
def test_current_ratio_from_the_documented_particulars():
    out = R.ratios_from_balance_sheet(balance_sheet_payload(full_statement()))
    current = out["current_ratio"]
    assert current["value"] == 2.0  # 320 / 160
    assert current["period"] == "2026-03-31"
    assert current["source"] == "upstox:balance-sheet"
    assert current["inputs"] == {"current_assets": 320.0, "current_liabilities": 160.0}
    assert "proxy" not in current


def test_debt_equity_is_flagged_as_a_proxy():
    out = R.ratios_from_balance_sheet(balance_sheet_payload(full_statement()))
    proxy = out["debt_equity_proxy"]
    assert proxy["proxy"] is True
    assert proxy["value"] == 0.54  # 540 / 1000
    assert proxy["period"] == "2026-03-31"
    assert proxy["inputs"] == {"non_current_liabilities": 540.0, "equity_capital": 1000.0}


def test_latest_period_wins_when_several_periods_exist():
    rows = [
        {"particular": "Current Assets",
         "2024-03-31": 250, "2026-03-31": 320, "2025-03-31": 300},
        {"particular": "Current Liabilities",
         "2024-03-31": 125, "2026-03-31": 160, "2025-03-31": 200},
    ]
    out = R.ratios_from_balance_sheet(balance_sheet_payload(rows))
    assert out["current_ratio"]["period"] == "2026-03-31"
    assert out["current_ratio"]["value"] == 2.0


def test_month_name_period_labels_are_ordered_by_date_not_text():
    rows = [
        # "Mar 2026" sorts before "Sep 2025" alphabetically — date order must win
        {"particular": "Current Assets", "Sep 2025": 300, "Mar 2026": 320},
        {"particular": "Current Liabilities", "Sep 2025": 200, "Mar 2026": 160},
    ]
    out = R.ratios_from_balance_sheet(balance_sheet_payload(rows))
    assert out["current_ratio"]["period"] == "Mar 2026"
    assert out["current_ratio"]["value"] == 2.0


def test_latest_period_falls_back_when_one_side_is_blank():
    rows = [
        {"particular": "Current Assets", "2026-03-31": "320", "2025-03-31": "300"},
        {"particular": "Current Liabilities", "2026-03-31": "", "2025-03-31": "200"},
    ]
    out = R.ratios_from_balance_sheet(balance_sheet_payload(rows))
    assert out["current_ratio"]["period"] == "2025-03-31"
    assert out["current_ratio"]["value"] == 1.5


def test_zero_denominator_omits_the_ratio_entirely():
    out = R.ratios_from_balance_sheet(balance_sheet_payload(
        full_statement(**{"Current Liabilities": {"2026-03-31": "0"},
                          "Equity Capital": {"2026-03-31": 0}})
    ))
    assert "current_ratio" not in out
    assert "debt_equity_proxy" not in out
    assert all(math.isfinite(value) for value in _numbers(out))


def test_missing_denominator_row_omits_the_ratio():
    rows = [row for row in full_statement() if row["particular"] != "Current Liabilities"]
    out = R.ratios_from_balance_sheet(balance_sheet_payload(rows))
    assert "current_ratio" not in out
    assert "debt_equity_proxy" in out  # the other pair is unaffected


def test_values_may_be_numbers_strings_commas_or_bracketed_negatives():
    rows = [
        {"particular": "Current Assets", "2026-03-31": "1,250.5"},
        {"particular": "Current Liabilities", "2026-03-31": 500},
        {"particular": "Non-Current Liabilities", "2026-03-31": "(200)"},
        {"particular": "Equity Capital", "2026-03-31": "1,000"},
    ]
    out = R.ratios_from_balance_sheet(balance_sheet_payload(rows))
    assert out["current_ratio"]["value"] == 2.501
    assert out["debt_equity_proxy"]["value"] == -0.2


def test_derived_ratios_are_always_finite():
    out = R.ratios_from_balance_sheet(balance_sheet_payload(full_statement()))
    values = list(_numbers(out))
    assert values and all(math.isfinite(value) for value in values)


@pytest.mark.parametrize(
    "payload",
    [{}, {"data": None}, {"data": []}, {"data": {"full_statement": None}},
     {"data": {"full_statement": ["junk", {"no_particular": 1}]}}],
)
def test_malformed_balance_sheet_payloads_return_empty(payload):
    assert R.ratios_from_balance_sheet(payload) == {}


# ---------------------------------------------------------------------------
# fetch_ratios — every failure mode degrades to {}
# ---------------------------------------------------------------------------
def test_fetch_ratios_returns_empty_without_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    assert R.fetch_ratios("RELIANCE", settings=_settings(tmp_path),
                          session=DeadSession()) == {}


def test_fetch_ratios_returns_empty_when_the_client_raises(tmp_path):
    client = StubClient(key_ratios=RuntimeError("HTTP 500"),
                        balance_sheet=RuntimeError("HTTP 500"))
    assert R.fetch_ratios(ISIN, settings=_settings(tmp_path), client=client) == {}
    assert [call[0] for call in client.calls] == ["key_ratios", "balance_sheet"]


def test_fetch_ratios_returns_empty_for_an_unresolvable_symbol(tmp_path):
    client = StubClient(key_ratios=key_ratios_payload())
    out = R.fetch_ratios("NOTATICKER", settings=_settings(tmp_path),
                         session=DeadSession(), client=client)
    assert out == {}
    assert client.calls == []


def test_fetch_ratios_returns_empty_for_a_blank_symbol(tmp_path):
    assert R.fetch_ratios("   ", settings=_settings(tmp_path)) == {}


def test_fetch_ratios_merges_both_endpoints_with_meta(tmp_path):
    client = StubClient(
        key_ratios=key_ratios_payload(),
        balance_sheet=balance_sheet_payload(full_statement()),
    )
    out = R.fetch_ratios(ISIN, settings=_settings(tmp_path), client=client)
    assert out["pe"]["value"] == 20.15
    assert out["current_ratio"]["value"] == 2.0
    assert out["debt_equity_proxy"]["proxy"] is True
    assert out["_meta"] == {"isin": ISIN, "fetched": ["key-ratios", "balance-sheet"]}
    assert client.calls == [("key_ratios", ISIN), ("balance_sheet", ISIN)]


def test_fetch_ratios_keeps_the_half_that_answered(tmp_path):
    client = StubClient(
        key_ratios=key_ratios_payload(),
        balance_sheet=RuntimeError("HTTP 503"),
    )
    out = R.fetch_ratios(ISIN, settings=_settings(tmp_path), client=client)
    assert out["_meta"]["fetched"] == ["key-ratios"]
    assert "current_ratio" not in out


def test_fetch_ratios_returns_empty_on_malformed_payloads(tmp_path):
    client = StubClient(key_ratios={"status": "success", "data": "garbage"},
                        balance_sheet={"status": "error"})
    assert R.fetch_ratios(ISIN, settings=_settings(tmp_path), client=client) == {}
