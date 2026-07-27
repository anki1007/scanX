"""Upstox-sourced ratios for the board: key ratios + derived balance-sheet health.

Two documented Fundamentals endpoints feed this module:

* ``GET /v2/fundamentals/{isin}/key-ratios`` — exactly six names (P/E, P/B, ROA,
  ROE, ROCE, EV/EBITDA), each with the sector benchmark alongside the company
  value.  Values arrive as strings and may carry a trailing ``%``.
* ``GET /v2/fundamentals/{isin}/balance-sheet?type=consolidated&fs=true`` — the
  full statement.  Upstox does NOT publish a current ratio or debt/equity, so
  both are derived here; the debt/equity figure is an honest *proxy*
  (Non-Current Liabilities / Equity Capital), never presented as exact D/E.

Usage::

    from earnings_intel.data.ratios import fetch_ratios

    fetch_ratios("RELIANCE")
    # {"pe": {"value": 20.15, "unit": "x", "sector": 12.46,
    #         "source": "upstox:key-ratios"},
    #  ...,
    #  "current_ratio": {"value": 1.1852, "period": "2026-03-31",
    #                    "source": "upstox:balance-sheet",
    #                    "inputs": {"current_assets": 320.0,
    #                               "current_liabilities": 270.0}},
    #  "debt_equity_proxy": {..., "proxy": True},
    #  "_meta": {"isin": "INE002A01018",
    #            "fetched": ["key-ratios", "balance-sheet"]}}

The two parsers are pure and total: unknown ratio names, blank cells and absent
periods are skipped rather than raised on.  ``fetch_ratios`` returns ``{}`` for
every failure mode (no token, unresolvable symbol, HTTP error, malformed
payload) — a ratio lookup must NEVER break a bake.
"""
from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from upstox_lab.config import UpstoxLabSettings

log = logging.getLogger("technofunda.ratios")

__all__ = [
    "KEY_RATIO_NAMES",
    "SOURCE_BALANCE_SHEET",
    "SOURCE_KEY_RATIOS",
    "fetch_ratios",
    "parse_ratio_value",
    "ratios_from_balance_sheet",
    "ratios_from_key_ratios",
]

SOURCE_KEY_RATIOS = "upstox:key-ratios"
SOURCE_BALANCE_SHEET = "upstox:balance-sheet"

#: The six documented key-ratio names -> our snake_case keys.  Lookup is done
#: on a whitespace-stripped upper-case form so "EV / EBITDA" still matches.
KEY_RATIO_NAMES: dict[str, str] = {
    "P/E": "pe",
    "P/B": "pb",
    "ROA": "roa",
    "ROE": "roe",
    "ROCE": "roce",
    "EV/EBITDA": "ev_ebitda",
}

#: Documented ``full_statement`` particulars used by the derived ratios.
PARTICULAR_CURRENT_ASSETS = "current assets"
PARTICULAR_CURRENT_LIABILITIES = "current liabilities"
PARTICULAR_NON_CURRENT_LIABILITIES = "non current liabilities"
PARTICULAR_EQUITY_CAPITAL = "equity capital"

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")
_ISO_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")
_DMY_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$")
_ALPHA_RE = re.compile(r"[A-Za-z]{3,}")
_YEAR4_RE = re.compile(r"\b(\d{4})\b")
_YEAR2_RE = re.compile(r"\b(\d{2})\b")

#: Cell contents that mean "no value" rather than a number.
_BLANK_TOKENS = frozenset({"-", "--", "---", "n/a", "na", "nil", "none", "null", "nan"})

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

#: Rounding for derived ratios — keeps 2.0 as 2.0 instead of 2.0000000000000004.
_ROUND = 4


# ---------------------------------------------------------------------------
# tolerant scalar parsing
# ---------------------------------------------------------------------------
def _to_number(value: Any) -> float | None:
    """Coerce ``12``, ``"1,234.5"``, ``"(230)"``, ``""``, ``"-"`` -> float | None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in _BLANK_TOKENS:
        return None
    negated = text.startswith("(") and text.endswith(")")
    match = _NUM_RE.search(text)
    if match is None:
        return None
    try:
        number = float(match.group())
    except ValueError:  # pragma: no cover - _NUM_RE already guarantees a float
        return None
    if not math.isfinite(number):
        return None
    return -abs(number) if negated else number


def parse_ratio_value(raw: str) -> tuple[float | None, str]:
    """``"8.94%"`` -> ``(8.94, "pct")``; ``"20.15"`` -> ``(20.15, "x")``.

    Blank, ``"-"`` and unparseable cells return ``(None, "")`` — the empty unit
    is the caller's signal that there is no value at all.
    """
    number = _to_number(raw)
    if number is None:
        return None, ""
    unit = "pct" if isinstance(raw, str) and raw.strip().endswith("%") else "x"
    return number, unit


def _normalize_ratio_name(name: Any) -> str:
    """``"EV / EBITDA"`` -> ``"EV/EBITDA"`` for tolerant name matching."""
    return _WS_RE.sub("", str(name or "")).upper()


def _normalize_particular(label: Any) -> str:
    """``"Non-Current Liabilities"`` -> ``"non current liabilities"``."""
    text = str(label or "").replace("-", " ").replace("_", " ").strip(" :*")
    return _WS_RE.sub(" ", text).strip().lower()


_KEY_RATIO_LOOKUP = {_normalize_ratio_name(name): key for name, key in KEY_RATIO_NAMES.items()}


# ---------------------------------------------------------------------------
# key ratios
# ---------------------------------------------------------------------------
def ratios_from_key_ratios(payload: dict) -> dict:
    """Pure: key-ratios payload -> ``{"pe": {...}, ..., "ev_ebitda": {...}}``.

    Each entry is ``{"value", "unit", "sector", "source"}``.  Names outside the
    six documented ones are ignored, and rows without a usable company value are
    dropped, so a schema tweak degrades instead of raising.
    """
    out: dict[str, Any] = {}
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, Mapping):
            continue
        key = _KEY_RATIO_LOOKUP.get(_normalize_ratio_name(item.get("name")))
        if key is None:
            log.debug("ignoring unknown key ratio name=%r", item.get("name"))
            continue
        value, unit = parse_ratio_value(item.get("company_value"))
        if value is None:
            continue
        sector, _ = parse_ratio_value(item.get("sector_value"))
        out[key] = {
            "value": value,
            "unit": unit,
            "sector": sector,
            "source": SOURCE_KEY_RATIOS,
        }
    return out


# ---------------------------------------------------------------------------
# balance-sheet derived ratios
# ---------------------------------------------------------------------------
def _period_sort_key(period: str) -> tuple[int, int, int, int]:
    """Sortable ``(parsed, year, month, day)``; unparseable periods sort oldest."""
    text = str(period or "").strip()
    if not text:
        return (0, 0, 0, 0)

    iso = _ISO_RE.match(text)
    if iso:
        year, month, day = iso.groups()
        return (1, int(year), int(month or 0), int(day or 0))

    dmy = _DMY_RE.match(text)
    if dmy:
        day, month, year = dmy.groups()
        return (1, int(year), int(month), int(day))

    alpha = _ALPHA_RE.search(text)
    month_no = _MONTHS.get(alpha.group()[:3].lower()) if alpha else None
    if month_no is None:
        return (0, 0, 0, 0)
    tail = text[alpha.end():] if alpha else text
    year4 = _YEAR4_RE.search(tail) or _YEAR4_RE.search(text)
    if year4:
        return (1, int(year4.group(1)), month_no, 0)
    year2 = _YEAR2_RE.search(tail)
    if year2:
        return (1, 2000 + int(year2.group(1)), month_no, 0)
    return (0, 0, 0, 0)


def _statement_index(full_statement: Any) -> dict[str, dict[str, float]]:
    """``full_statement`` rows -> ``{normalized particular: {period: value}}``.

    Period columns are discovered generically: every key on a row that is not
    ``particular`` is treated as a period label.
    """
    index: dict[str, dict[str, float]] = {}
    if not isinstance(full_statement, list):
        return index
    for row in full_statement:
        if not isinstance(row, Mapping):
            continue
        label = ""
        for key, value in row.items():
            if str(key).strip().lower() == "particular":
                label = _normalize_particular(value)
                break
        if not label:
            continue
        bucket = index.setdefault(label, {})
        for key, value in row.items():
            period = str(key).strip()
            if not period or period.lower() == "particular":
                continue
            number = _to_number(value)
            if number is None:
                continue
            bucket.setdefault(period, number)
    return index


def _latest_common_period(
    numerator: Mapping[str, float], denominator: Mapping[str, float]
) -> str | None:
    """Newest period label carrying a usable value on BOTH sides."""
    common = set(numerator) & set(denominator)
    if not common:
        return None
    return max(common, key=lambda period: (_period_sort_key(period), period))


def _derived_ratio(
    index: Mapping[str, dict[str, float]],
    numerator_label: str,
    denominator_label: str,
    numerator_key: str,
    denominator_key: str,
) -> dict[str, Any] | None:
    """Compute one derived ratio, or ``None`` when it cannot be stated honestly."""
    numerator = index.get(numerator_label) or {}
    denominator = index.get(denominator_label) or {}
    period = _latest_common_period(numerator, denominator)
    if period is None:
        return None
    below = denominator[period]
    if not below:  # zero (or -0.0) denominator: omit rather than emit inf/NaN
        log.debug("skipping %s/%s: zero denominator period=%s",
                  numerator_label, denominator_label, period)
        return None
    value = numerator[period] / below
    if not math.isfinite(value):  # pragma: no cover - guarded by the zero check
        return None
    return {
        "value": round(value, _ROUND),
        "period": period,
        "source": SOURCE_BALANCE_SHEET,
        "inputs": {
            numerator_key: numerator[period],
            denominator_key: below,
        },
    }


def ratios_from_balance_sheet(payload: dict) -> dict:
    """Pure: balance-sheet payload -> current ratio + debt/equity **proxy**.

    Both ratios use the latest period that carries a value for both inputs.
    ``debt_equity_proxy`` is flagged ``"proxy": True`` because Non-Current
    Liabilities / Equity Capital is not borrowings / net worth.
    """
    out: dict[str, Any] = {}
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return out
    index = _statement_index(data.get("full_statement"))
    if not index:
        return out

    current = _derived_ratio(
        index,
        PARTICULAR_CURRENT_ASSETS,
        PARTICULAR_CURRENT_LIABILITIES,
        "current_assets",
        "current_liabilities",
    )
    if current is not None:
        out["current_ratio"] = current

    proxy = _derived_ratio(
        index,
        PARTICULAR_NON_CURRENT_LIABILITIES,
        PARTICULAR_EQUITY_CAPITAL,
        "non_current_liabilities",
        "equity_capital",
    )
    if proxy is not None:
        proxy["proxy"] = True
        out["debt_equity_proxy"] = proxy
    return out


# ---------------------------------------------------------------------------
# live fetch
# ---------------------------------------------------------------------------
def fetch_ratios(
    symbol: str,
    *,
    settings: "UpstoxLabSettings | None" = None,
    session: Any | None = None,
    client: Any | None = None,
) -> dict:
    """All available Upstox ratios for ``symbol``; ``{}`` when unavailable.

    Resolves the symbol to an ISIN with the shared instrument master, then calls
    both endpoints through :class:`upstox_lab.client.UpstoxFundamentalsClient`.
    Never raises: no token, an unresolvable symbol, an HTTP error or a malformed
    payload all degrade to ``{}`` (or to the half that did answer).
    """
    name = str(symbol or "").strip().upper()
    if not name:
        return {}

    try:
        from upstox_lab import auth
        from upstox_lab.client import UpstoxFundamentalsClient
        from upstox_lab.config import UpstoxLabSettings
        from upstox_lab.sync import InstrumentResolver
    except Exception as exc:  # noqa: BLE001 - optional dependency boundary
        log.info("upstox_lab unavailable (%s) — no Upstox ratios", type(exc).__name__)
        return {}

    if settings is None:
        try:
            settings = UpstoxLabSettings.from_env()
        except Exception as exc:  # noqa: BLE001 - bad env must not break a bake
            log.info("upstox settings unusable (%s)", type(exc).__name__)
            return {}

    token: str | None = None
    if client is None:
        try:
            token = auth.load_token(settings)
        except Exception as exc:  # noqa: BLE001 - absent token is routine
            log.info("no Upstox analytics token (%s) — skipping ratios for %s",
                     type(exc).__name__, name)
            return {}

    try:
        resolved, unresolved = InstrumentResolver(settings, session=session).resolve([name])
    except Exception as exc:  # noqa: BLE001 - instrument master is a network call
        log.warning("symbol resolution failed for %s: %s", name, type(exc).__name__)
        return {}
    isin = next(iter(resolved.values()), "")
    if not isin:
        log.info("could not resolve %s to an ISIN (unresolved=%s)", name, unresolved)
        return {}

    if client is None:
        try:
            client = UpstoxFundamentalsClient(settings, token=token, session=session)
        except Exception as exc:  # noqa: BLE001 - client construction touches auth
            log.warning("could not build the Upstox client: %s", type(exc).__name__)
            return {}

    out: dict[str, Any] = {}
    fetched: list[str] = []
    calls = (
        (SOURCE_KEY_RATIOS.split(":", 1)[1], "get_key_ratios", ratios_from_key_ratios),
        (SOURCE_BALANCE_SHEET.split(":", 1)[1], "get_balance_sheet",
         ratios_from_balance_sheet),
    )
    for label, method, parse in calls:
        try:
            part = parse(getattr(client, method)(isin))
        except Exception as exc:  # noqa: BLE001 - HTTP/parse failures degrade
            log.warning("%s failed for %s (%s): %s", label, name, isin, type(exc).__name__)
            continue
        if part:
            out.update(part)
            fetched.append(label)

    if not out:
        log.info("no Upstox ratios for %s (%s)", name, isin)
        return {}
    out["_meta"] = {"isin": isin, "fetched": fetched}
    return out
