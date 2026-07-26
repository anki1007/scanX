"""Quant Lab — production-grade Upstox Fundamentals ingestion.

Read-only Analytics-token access to the Upstox ``/v2/fundamentals`` APIs,
normalized into DuckDB with incremental sync, retries, rate limiting and
APScheduler-based scheduling.  See ``quantlab/README.md``.

Heavy optional dependencies (``duckdb``, ``apscheduler``) are imported
lazily inside their modules, so ``import quantlab`` always works.
"""
from __future__ import annotations

from .auth import TokenInfo, inspect_token, load_token
from .client import ENDPOINTS, EndpointSpec, TokenBucket, UpstoxFundamentalsClient
from .config import TOKEN_ENV_VAR, QuantLabSettings
from .errors import (
    AuthTokenError,
    MissingDependencyError,
    QuantLabAPIError,
    QuantLabConfigError,
    QuantLabError,
)
from .normalize import NORMALIZERS, Dataset, normalize
from .store import FundamentalsStore, SyncState
from .sync import InstrumentResolver, SyncEngine, SyncReport, load_universe

__version__ = "0.1.0"

__all__ = [
    "ENDPOINTS",
    "NORMALIZERS",
    "TOKEN_ENV_VAR",
    "AuthTokenError",
    "Dataset",
    "EndpointSpec",
    "FundamentalsStore",
    "InstrumentResolver",
    "MissingDependencyError",
    "QuantLabAPIError",
    "QuantLabConfigError",
    "QuantLabError",
    "QuantLabSettings",
    "SyncEngine",
    "SyncReport",
    "SyncState",
    "TokenBucket",
    "TokenInfo",
    "UpstoxFundamentalsClient",
    "__version__",
    "inspect_token",
    "load_token",
    "load_universe",
    "normalize",
]
