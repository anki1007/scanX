"""Normalize raw Upstox fundamentals payloads into flat, typed table rows.

Each endpoint family maps to exactly one DuckDB table.  A normalizer takes
``(isin, payload, params, fetched_at)`` and returns a :class:`Dataset`:
column names, row tuples, and the natural-key *scope* the store deletes
before inserting (making every write idempotent).

All parsing is tolerant: missing/malformed fields become ``None`` or are
skipped — an upstream schema tweak must never crash the sync run.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

logger = logging.getLogger("quantlab.normalize")

Row = tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class Dataset:
    """One table-shaped batch of rows plus its idempotency scope."""

    table: str
    columns: tuple[str, ...]
    rows: list[Row]
    #: Column -> value; the store deletes matching rows before inserting,
    #: so re-running a sync replaces the scope instead of duplicating it.
    scope: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# tolerant coercion helpers
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def to_float(value: Any) -> float | None:
    """Coerce numbers, '4.39%', '2,391.5', '' -> float or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUM_RE.search(value.replace(",", ""))
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None
    return None


def to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _data(payload: Mapping[str, Any]) -> Any:
    return payload.get("data") if isinstance(payload, Mapping) else None


def _history_items(node: Any) -> list[Mapping[str, Any]]:
    history = node.get("history") if isinstance(node, Mapping) else None
    if not isinstance(history, list):
        return []
    return [item for item in history if isinstance(item, Mapping)]


# ---------------------------------------------------------------------------
# per-endpoint normalizers
# ---------------------------------------------------------------------------
def normalize_company_profile(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    rows: list[Row] = []
    if isinstance(data, Mapping):
        mcap_inr = data.get("sector_market_cap_inr")
        mcap_usd = data.get("sector_market_cap_usd")
        mcap_inr = mcap_inr if isinstance(mcap_inr, Mapping) else {}
        mcap_usd = mcap_usd if isinstance(mcap_usd, Mapping) else {}
        rows.append(
            (
                isin,
                to_str(data.get("company_profile")),
                to_str(data.get("sector")),
                to_float(mcap_inr.get("value")),
                to_str(mcap_inr.get("unit")),
                to_float(mcap_usd.get("value")),
                to_str(mcap_usd.get("unit")),
                fetched_at,
            )
        )
    return Dataset(
        table="company_profile",
        columns=(
            "isin", "company_profile", "sector",
            "sector_mcap_inr", "sector_mcap_inr_unit",
            "sector_mcap_usd", "sector_mcap_usd_unit",
            "fetched_at",
        ),
        rows=rows,
        scope={"isin": isin},
    )


def _statement_rows(
    isin: str,
    statement_type: str,
    time_period: str | None,
    units_in: str | None,
    section: str,
    items: Any,
    label_field: str,
    fetched_at: datetime,
) -> list[Row]:
    """Flatten [{category|particular, history:[{period, value, change?}]}]."""
    rows: list[Row] = []
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, Mapping):
            continue
        label = to_str(item.get(label_field)) or to_str(item.get("particular")) or to_str(
            item.get("category")
        )
        if label is None:
            continue
        for point in _history_items(item):
            period = to_str(point.get("period"))
            if period is None:
                continue
            base = (isin, statement_type, section, label, period,
                    to_float(point.get("value")), to_str(point.get("change")),
                    units_in, fetched_at)
            if time_period is not None:
                rows.append((base[0], base[1], time_period) + base[2:])
            else:
                rows.append(base)
    return rows


def normalize_balance_sheet(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    data = data if isinstance(data, Mapping) else {}
    statement_type = to_str(params.get("type")) or to_str(data.get("type")) or "consolidated"
    units_in = to_str(data.get("units_in"))
    rows: list[Row] = []
    # Summary series: history: [{"total_asset": n, "total_liability": n, "period": p}]
    history = data.get("history")
    if isinstance(history, list):
        for point in history:
            if not isinstance(point, Mapping):
                continue
            period = to_str(point.get("period"))
            if period is None:
                continue
            for metric in ("total_asset", "total_liability"):
                if metric in point:
                    rows.append(
                        (isin, statement_type, "summary", metric, period,
                         to_float(point.get(metric)), None, units_in, fetched_at)
                    )
    rows.extend(
        _statement_rows(isin, statement_type, None, units_in, "full",
                        data.get("full_statement"), "particular", fetched_at)
    )
    return Dataset(
        table="balance_sheet",
        columns=("isin", "statement_type", "section", "particular", "period",
                 "value", "change", "units_in", "fetched_at"),
        rows=rows,
        scope={"isin": isin, "statement_type": statement_type},
    )


def normalize_cash_flow(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    data = data if isinstance(data, Mapping) else {}
    statement_type = to_str(params.get("type")) or to_str(data.get("type")) or "consolidated"
    units_in = to_str(data.get("units_in"))
    rows = _statement_rows(isin, statement_type, None, units_in, "category",
                           data.get("cash_flow"), "category", fetched_at)
    rows.extend(
        _statement_rows(isin, statement_type, None, units_in, "full",
                        data.get("full_statement"), "particular", fetched_at)
    )
    return Dataset(
        table="cash_flow",
        columns=("isin", "statement_type", "section", "particular", "period",
                 "value", "change", "units_in", "fetched_at"),
        rows=rows,
        scope={"isin": isin, "statement_type": statement_type},
    )


def normalize_income_statement(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    data = data if isinstance(data, Mapping) else {}
    statement_type = to_str(params.get("type")) or to_str(data.get("type")) or "consolidated"
    time_period = (
        to_str(params.get("time_period")) or to_str(data.get("time_period")) or "yearly"
    )
    units_in = to_str(data.get("units_in"))
    rows = _statement_rows(isin, statement_type, time_period, units_in, "category",
                           data.get("income_statement"), "category", fetched_at)
    rows.extend(
        _statement_rows(isin, statement_type, time_period, units_in, "full",
                        data.get("full_statement"), "particular", fetched_at)
    )
    return Dataset(
        table="income_statement",
        columns=("isin", "statement_type", "time_period", "section", "particular",
                 "period", "value", "change", "units_in", "fetched_at"),
        rows=rows,
        scope={"isin": isin, "statement_type": statement_type, "time_period": time_period},
    )


def normalize_share_holdings(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    rows: list[Row] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            category = to_str(item.get("category"))
            if category is None:
                continue
            for point in _history_items(item):
                period = to_str(point.get("period"))
                if period is None:
                    continue
                rows.append((isin, category, period, to_float(point.get("value")), fetched_at))
    return Dataset(
        table="share_holdings",
        columns=("isin", "category", "period", "holding_pct", "fetched_at"),
        rows=rows,
        scope={"isin": isin},
    )


def normalize_key_ratios(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    rows: list[Row] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            name = to_str(item.get("name"))
            if name is None:
                continue
            company_raw = to_str(item.get("company_value"))
            sector_raw = to_str(item.get("sector_value"))
            rows.append(
                (isin, name, to_float(company_raw), company_raw,
                 to_float(sector_raw), sector_raw, fetched_at)
            )
    return Dataset(
        table="key_ratios",
        columns=("isin", "ratio_name", "company_value", "company_value_raw",
                 "sector_value", "sector_value_raw", "fetched_at"),
        rows=rows,
        scope={"isin": isin},
    )


def normalize_corporate_actions(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    rows: list[Row] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            name = to_str(item.get("name"))
            if name is None:
                continue
            details = item.get("event_details")
            details_json: str | None = None
            if isinstance(details, list):
                try:
                    details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    details_json = None
            rows.append(
                (isin, name, to_str(item.get("expiry_date")),
                 to_float(item.get("amount")), to_str(item.get("ratio")),
                 details_json, fetched_at)
            )
    return Dataset(
        table="corporate_actions",
        columns=("isin", "action_type", "expiry_date", "amount", "ratio",
                 "event_details_json", "fetched_at"),
        rows=rows,
        scope={"isin": isin},
    )


def normalize_competitors(
    isin: str, payload: Mapping[str, Any], params: Mapping[str, str], fetched_at: datetime
) -> Dataset:
    data = _data(payload)
    rows: list[Row] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            instrument_key = to_str(item.get("instrument_key"))
            competitor_isin: str | None = None
            if instrument_key and "|" in instrument_key:
                competitor_isin = instrument_key.split("|", 1)[1] or None
            mcap_inr = item.get("sector_market_cap_inr")
            mcap_inr = mcap_inr if isinstance(mcap_inr, Mapping) else {}
            rows.append(
                (isin, instrument_key, competitor_isin, to_str(item.get("sector")),
                 to_str(item.get("company_profile")), to_float(mcap_inr.get("value")),
                 fetched_at)
            )
    return Dataset(
        table="competitors",
        columns=("isin", "competitor_instrument_key", "competitor_isin", "sector",
                 "company_profile", "sector_mcap_inr", "fetched_at"),
        rows=rows,
        scope={"isin": isin},
    )


#: Endpoint name -> normalizer.  Keys mirror ``quantlab.client.ENDPOINTS``.
Normalizer = Callable[[str, Mapping[str, Any], Mapping[str, str], datetime], Dataset]

NORMALIZERS: dict[str, Normalizer] = {
    "company_profile": normalize_company_profile,
    "balance_sheet": normalize_balance_sheet,
    "cash_flow": normalize_cash_flow,
    "income_statement": normalize_income_statement,
    "share_holdings": normalize_share_holdings,
    "key_ratios": normalize_key_ratios,
    "corporate_actions": normalize_corporate_actions,
    "competitors": normalize_competitors,
}


def normalize(
    endpoint: str,
    isin: str,
    payload: Mapping[str, Any],
    params: Mapping[str, str],
    fetched_at: datetime,
) -> Dataset:
    """Dispatch to the endpoint's normalizer (programmer error if unknown)."""
    normalizer = NORMALIZERS.get(endpoint)
    if normalizer is None:
        raise ValueError(f"no normalizer for endpoint {endpoint!r}")
    return normalizer(isin, payload, params, fetched_at)
