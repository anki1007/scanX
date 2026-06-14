"""
The single data interface every part of the system depends on.

Concrete providers (`KiteProvider`, `SampleProvider`) implement the methods
they can. Optional feeds (institutional, transcript, options, corporate,
valuation) have neutral default implementations here so that a provider which
lacks a feed degrades gracefully instead of breaking the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Iterable

from ..models import (
    CorporateEvent,
    EarningsReport,
    InstitutionalActivity,
    OptionsSnapshot,
    PriceBar,
    TranscriptData,
    ValuationInputs,
)


class DataProvider(ABC):
    """Abstract market-data + events source."""

    # --- required: price data (Agents 8, 9, backtest, risk) ----------------
    @abstractmethod
    def get_history(
        self, symbol: str, from_date: date, to_date: date, interval: str = "day"
    ) -> list[PriceBar]:
        """Return OHLCV bars (oldest first) for `symbol`."""

    @abstractmethod
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        """Return last traded price per symbol."""

    # --- required: earnings scanner (Agents 1, 2) --------------------------
    @abstractmethod
    def iter_new_earnings(self) -> Iterable[EarningsReport]:
        """Yield earnings reports detected since the last scan."""

    # --- optional feeds: neutral defaults so the pipeline degrades nicely --
    def get_institutional(self, symbol: str) -> InstitutionalActivity:
        return InstitutionalActivity(symbol=symbol)

    def get_transcript(self, symbol: str) -> TranscriptData:
        return TranscriptData(symbol=symbol, text="")

    def get_options(self, symbol: str) -> OptionsSnapshot:
        return OptionsSnapshot(symbol=symbol)

    def get_corporate_event(self, symbol: str) -> CorporateEvent:
        return CorporateEvent(symbol=symbol)

    def get_valuation(self, symbol: str) -> ValuationInputs:
        return ValuationInputs(symbol=symbol)
