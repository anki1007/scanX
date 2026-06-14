"""Data-provider layer: one interface, swappable live/synthetic backends."""
from __future__ import annotations

from .base import DataProvider
from .sample_provider import SampleProvider

__all__ = ["DataProvider", "SampleProvider", "KiteProvider"]


def __getattr__(name: str):
    # Lazy import so `kiteconnect` is only required if you actually use it.
    if name == "KiteProvider":
        from .kite_provider import KiteProvider
        return KiteProvider
    raise AttributeError(name)
