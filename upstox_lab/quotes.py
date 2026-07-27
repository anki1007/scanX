"""Live LTP from the Upstox Market Quote API using the Analytics token.

The Analytics token is read-only and GET-only, but Upstox documents Market
Quote as one of the groups it may call without a Static IP — so the same
1-year token that drives the fundamentals sync can also price the whole
board, with no daily OAuth dance and no trading capability attached.

    from upstox_lab.quotes import fetch_ltp
    fetch_ltp(["RELIANCE", "PASUPTAC"])   # -> {"RELIANCE": {"ltp": .., "pct": ..}}

Symbols are resolved to ISIN instrument keys with the same instrument master
the sync engine uses, so anything it can sync, it can price.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from .auth import load_token
from .config import UpstoxLabSettings
from .errors import MissingDependencyError
from .sync import InstrumentResolver

log = logging.getLogger("upstox_lab.quotes")

QUOTES_URL = "https://api.upstox.com/v2/market-quote/quotes"
BATCH = 100          # documented ceiling is higher; stay well inside it
SEGMENTS = ("NSE_EQ", "BSE_EQ")


def _session(session: Any | None = None) -> Any:
    if session is not None:
        return session
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MissingDependencyError(
            "requests is required for Upstox quotes — pip install requests") from exc
    return requests.Session()


def parse_quote_payload(payload: dict, isin_to_symbol: dict[str, str]) -> dict[str, dict]:
    """Upstox quote response -> {SYMBOL: {ltp, pct}}.

    Entries are matched back by the ISIN inside ``instrument_token`` rather
    than by the response key, which Upstox returns in its own
    ``<SEGMENT>:<TRADINGSYMBOL>`` form that need not match what we asked for.
    """
    out: dict[str, dict] = {}
    data = (payload or {}).get("data") or {}
    for key, row in data.items():
        if not isinstance(row, dict):
            continue
        token = str(row.get("instrument_token") or "")
        isin = token.split("|", 1)[1] if "|" in token else ""
        symbol = isin_to_symbol.get(isin)
        if not symbol:
            # fall back to the trading symbol in the response key
            tail = str(key).split(":", 1)[-1].strip().upper()
            if tail in isin_to_symbol.values():
                symbol = tail
        if not symbol:
            continue
        last = row.get("last_price")
        if last is None:
            continue
        prev = (row.get("ohlc") or {}).get("close")
        nc = row.get("net_change")
        base = (last - nc) if (nc is not None) else prev
        pct = round((last - base) / base * 100, 2) if base else None
        prior = out.get(symbol)
        # a symbol may resolve on both segments; keep the one that actually traded
        if prior is None or (prior.get("ltp") in (None, 0) and last):
            out[symbol] = {"ltp": round(float(last), 2), "pct": pct}
    return out


def fetch_ltp(symbols: Iterable[str], *, settings: UpstoxLabSettings | None = None,
              session: Any | None = None, pause: float = 0.25) -> dict[str, dict]:
    """{SYMBOL: {ltp, pct}} for as many symbols as Upstox can price.

    Never raises for network/auth problems — callers treat an empty dict as
    "use the fallback source".
    """
    settings = settings or UpstoxLabSettings.from_env()
    wanted = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not wanted:
        return {}
    try:
        token = load_token(settings)
    except Exception as exc:  # noqa: BLE001
        log.info("no Upstox token available (%s) — caller should fall back", type(exc).__name__)
        return {}

    sess = _session(session)
    resolver = InstrumentResolver(settings, session=sess)
    try:
        resolved, unresolved = resolver.resolve(wanted)
    except Exception as exc:  # noqa: BLE001
        log.warning("instrument resolution failed: %s", type(exc).__name__)
        return {}
    if unresolved:
        log.info("unresolved symbols: %d (BSE-only codes need an explicit mapping)",
                 len(unresolved))
    if not resolved:
        return {}

    isin_to_symbol = {isin: sym for sym, isin in resolved.items()}
    keys: list[str] = []
    for isin in isin_to_symbol:
        keys.extend(f"{seg}|{isin}" for seg in SEGMENTS)

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out: dict[str, dict] = {}
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        try:
            r = sess.get(QUOTES_URL, params={"instrument_key": ",".join(chunk)},
                         headers=headers, timeout=settings.request_timeout)
            if r.status_code == 401:
                log.warning("Upstox rejected the token (401) — falling back")
                return out
            if r.status_code != 200:
                log.warning("quote batch %d: HTTP %s", i // BATCH, r.status_code)
                continue
            out.update(parse_quote_payload(r.json(), isin_to_symbol))
        except Exception as exc:  # noqa: BLE001
            log.warning("quote batch %d failed: %s", i // BATCH, type(exc).__name__)
        time.sleep(pause)
    log.info("priced %d/%d symbols via Upstox", len(out), len(wanted))
    return out
