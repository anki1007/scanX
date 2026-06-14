"""
Live data + execution adapter backed by the official Zerodha Kite SDK.

This is the deployable path. It is intentionally thin: it translates Kite's API
shapes into our typed models and nothing more. Order placement is **guarded** by
`allow_orders` (default False) so the system is analysis-only until you opt in.

Auth: create a Kite Connect app, complete the login flow to obtain an
`access_token`, then:

    provider = KiteProvider(api_key="xxx", access_token="yyy")

`kiteconnect` is imported lazily so the rest of the project runs without it.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Iterable, Optional

from ..models import EarningsReport, PriceBar
from .base import DataProvider


class KiteProvider(DataProvider):
    def __init__(
        self,
        api_key: str,
        access_token: str,
        allow_orders: bool = False,
        default_exchange: str = "NSE",
    ):
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise ImportError(
                "kiteconnect is required for KiteProvider. "
                "Install it with `pip install kiteconnect`."
            ) from exc

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.allow_orders = allow_orders
        self.default_exchange = default_exchange
        self._token_cache: dict[str, int] = {}

    # ------------------------------------------------------------------ utils
    def _resolve_token(self, symbol: str, exchange: Optional[str] = None) -> int:
        """Map a trading symbol to its Kite instrument_token (cached)."""
        exchange = exchange or self.default_exchange
        key = f"{exchange}:{symbol}"
        if key in self._token_cache:
            return self._token_cache[key]
        # ltp returns instrument_token alongside the quote; cheapest lookup.
        data = self.kite.ltp([key])
        token = int(data[key]["instrument_token"])
        self._token_cache[key] = token
        return token

    # ------------------------------------------------------------- price data
    def get_history(
        self, symbol: str, from_date: date, to_date: date, interval: str = "day"
    ) -> list[PriceBar]:
        token = self._resolve_token(symbol)
        candles = self.kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )
        bars: list[PriceBar] = []
        for c in candles:
            d = c["date"]
            bars.append(
                PriceBar(
                    date=d.date() if isinstance(d, datetime) else d,
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=float(c["volume"]),
                    delivery_pct=None,  # not provided by Kite candles
                )
            )
        return bars

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        keys = [f"{self.default_exchange}:{s}" for s in symbols]
        data = self.kite.ltp(keys)
        out: dict[str, float] = {}
        for s, k in zip(symbols, keys):
            if k in data:
                out[s] = float(data[k]["last_price"])
        return out

    # ---------------------------------------------------- realtime universe
    def list_instruments(self, exchanges=("NSE", "BSE"), eq_only: bool = True) -> list[dict]:
        """Tradable instruments as [{exchange, symbol, token, name}]."""
        out: list[dict] = []
        for ex in exchanges:
            try:
                for inst in self.kite.instruments(ex):
                    if eq_only and inst.get("instrument_type") != "EQ":
                        continue
                    out.append({
                        "exchange": ex,
                        "symbol": inst["tradingsymbol"],
                        "token": int(inst["instrument_token"]),
                        "name": inst.get("name", ""),
                    })
            except Exception:  # noqa: BLE001
                continue
        return out

    def get_quotes(self, keys: list[str], batch: int = 400,
                   pause: float = 0.34) -> dict:
        """Full quote (last price, OHLC, VWAP, volume, net_change) in batches.

        `keys` are 'EXCHANGE:SYMBOL' strings. Kite caps a quote call at ~500
        instruments and ~3 req/s, so we batch and throttle.
        """
        out: dict = {}
        for i in range(0, len(keys), batch):
            chunk = keys[i:i + batch]
            try:
                out.update(self.kite.quote(chunk))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(pause)
        return out

    # --------------------------------------------------------------- scanner
    def iter_new_earnings(self) -> Iterable[EarningsReport]:
        """
        Kite does not expose corporate filings / results. Wire the NSE results
        calendar or BSE announcements feed here (Phase 1). Until then this
        yields nothing; feed EarningsReport objects into the pipeline directly.
        """
        return []

    # ------------------------------------------------------------- portfolio
    def get_holdings(self) -> list[dict]:
        return self.kite.holdings()

    def get_positions(self) -> dict:
        return self.kite.positions()

    def get_margins(self) -> dict:
        return self.kite.margins()

    # ------------------------------------------------------------- execution
    def place_order(self, **kwargs):
        """
        Place an order. Disabled unless the provider was constructed with
        allow_orders=True. Even then, the Portfolio Risk Agent should have
        approved the trade upstream.
        """
        if not self.allow_orders:
            raise PermissionError(
                "Order placement is disabled. Construct KiteProvider("
                "allow_orders=True) to enable live execution."
            )
        return self.kite.place_order(**kwargs)
