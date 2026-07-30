"""Environment-driven configuration for the Upstox Lab fundamentals pipeline.

Every knob is read from a ``UPSTOX_LAB_*`` environment variable with a safe
default, so the pipeline runs out of the box and is fully tunable in
production without code changes.  ``UpstoxLabSettings.from_env()`` is the
single entry point; passing an explicit mapping makes it trivially testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import UpstoxLabConfigError

#: Environment variable holding the Upstox Analytics token (read-only token
#: generated from the Upstox Developer Apps page — NOT a trading token).
TOKEN_ENV_VAR = "UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN"

#: Gitignored fallback file at the repo root that may hold the token.
TOKEN_FILE_NAME = "upstox_lab_token.txt"

#: Repository root (…/scanX).  upstox_lab/ lives directly under it.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: Upstox instruments masters — used to resolve trading symbols to ISINs.
#: Documented at https://upstox.com/developer/api-documentation/instruments/
#:
#: NSE alone is not enough for scanX. Screener identifies BSE-only listings by
#: their numeric scrip code (500012, 544291, ...), and 2,457 of the 5,488
#: companies on the platform are such codes. With only the NSE master loaded
#: they can never resolve, so they fall through to the Yahoo path and the daily
#: breadth universe stops at ~2,960 of 5,368 constituents. The BSE master keys
#: those codes to the same ISINs the rest of the pipeline already uses.
DEFAULT_INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)
DEFAULT_BSE_INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
)


def _get_str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key, "").strip()
    return value or default


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise UpstoxLabConfigError(f"{key} must be a number, got {raw!r}") from exc


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise UpstoxLabConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise UpstoxLabConfigError(f"{key} must be a boolean flag, got {raw!r}")


@dataclass(frozen=True, slots=True)
class UpstoxLabSettings:
    """Immutable runtime settings for the fundamentals pipeline."""

    # --- storage -----------------------------------------------------------
    db_path: Path = field(default_factory=lambda: REPO_ROOT / "upstox_lab.duckdb")

    # --- Upstox API --------------------------------------------------------
    base_url: str = "https://api.upstox.com"
    token_file: Path = field(default_factory=lambda: REPO_ROOT / TOKEN_FILE_NAME)
    request_timeout: float = 30.0

    # --- rate limiting (token bucket) ---------------------------------------
    rate_limit_rps: float = 1.0   # polite default: the token is shared with the VPS Quant Lab platform
    rate_limit_burst: int = 5

    # --- retries -------------------------------------------------------------
    max_retries: int = 5
    backoff_base: float = 0.5
    backoff_cap: float = 60.0

    # --- incremental sync -----------------------------------------------------
    #: Skip a (isin, endpoint, variant) fetched successfully within this window
    #: unless --full is requested.
    refresh_after_hours: float = 24.0
    #: Also ingest standalone statements (default: consolidated only).
    include_standalone: bool = False

    # --- universe / symbol resolution ------------------------------------------
    #: "index" -> docs/data/fundamental/index.json, otherwise a path to a
    #: symbols file (one symbol, "SYMBOL,ISIN" pair or JSON list per line).
    universe_source: str = "index"
    instruments_url: str = DEFAULT_INSTRUMENTS_URL
    bse_instruments_url: str = DEFAULT_BSE_INSTRUMENTS_URL
    instruments_cache: Path = field(
        default_factory=lambda: REPO_ROOT / ".cache" / "upstox_lab" / "NSE.json.gz"
    )
    instruments_max_age_hours: float = 168.0  # refresh instrument master weekly

    # --- scheduling / logging ----------------------------------------------------
    #: Standard 5-field cron expression; default = 18:30 IST Mon-Fri (post close).
    schedule_cron: str = "30 18 * * 1-5"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "UpstoxLabSettings":
        """Build settings from ``os.environ`` (or an explicit mapping)."""
        e = os.environ if env is None else env
        settings = cls(
            db_path=Path(_get_str(e, "UPSTOX_LAB_DB_PATH", str(REPO_ROOT / "upstox_lab.duckdb"))),
            base_url=_get_str(e, "UPSTOX_LAB_BASE_URL", "https://api.upstox.com").rstrip("/"),
            token_file=Path(_get_str(e, "UPSTOX_LAB_TOKEN_FILE", str(REPO_ROOT / TOKEN_FILE_NAME))),
            request_timeout=_get_float(e, "UPSTOX_LAB_REQUEST_TIMEOUT", 30.0),
            rate_limit_rps=_get_float(e, "UPSTOX_LAB_RATE_LIMIT_RPS", 1.0),
            rate_limit_burst=_get_int(e, "UPSTOX_LAB_RATE_LIMIT_BURST", 5),
            max_retries=_get_int(e, "UPSTOX_LAB_MAX_RETRIES", 5),
            backoff_base=_get_float(e, "UPSTOX_LAB_BACKOFF_BASE", 0.5),
            backoff_cap=_get_float(e, "UPSTOX_LAB_BACKOFF_CAP", 60.0),
            refresh_after_hours=_get_float(e, "UPSTOX_LAB_REFRESH_AFTER_HOURS", 24.0),
            include_standalone=_get_bool(e, "UPSTOX_LAB_INCLUDE_STANDALONE", False),
            universe_source=_get_str(e, "UPSTOX_LAB_UNIVERSE_SOURCE", "index"),
            instruments_url=_get_str(e, "UPSTOX_LAB_INSTRUMENTS_URL", DEFAULT_INSTRUMENTS_URL),
            bse_instruments_url=_get_str(e, "UPSTOX_LAB_BSE_INSTRUMENTS_URL",
                                         DEFAULT_BSE_INSTRUMENTS_URL),
            instruments_cache=Path(
                _get_str(
                    e,
                    "UPSTOX_LAB_INSTRUMENTS_CACHE",
                    str(REPO_ROOT / ".cache" / "upstox_lab" / "NSE.json.gz"),
                )
            ),
            instruments_max_age_hours=_get_float(e, "UPSTOX_LAB_INSTRUMENTS_MAX_AGE_HOURS", 168.0),
            schedule_cron=_get_str(e, "UPSTOX_LAB_SCHEDULE_CRON", "30 18 * * 1-5"),
            log_level=_get_str(e, "UPSTOX_LAB_LOG_LEVEL", "INFO").upper(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Raise :class:`UpstoxLabConfigError` on impossible values."""
        if self.rate_limit_rps <= 0:
            raise UpstoxLabConfigError("rate_limit_rps must be > 0")
        if self.rate_limit_burst < 1:
            raise UpstoxLabConfigError("rate_limit_burst must be >= 1")
        if self.max_retries < 0:
            raise UpstoxLabConfigError("max_retries must be >= 0")
        if self.backoff_base <= 0 or self.backoff_cap < self.backoff_base:
            raise UpstoxLabConfigError("backoff_base must be > 0 and <= backoff_cap")
        if self.request_timeout <= 0:
            raise UpstoxLabConfigError("request_timeout must be > 0")
        if len(self.schedule_cron.split()) != 5:
            raise UpstoxLabConfigError(
                f"schedule_cron must be a 5-field cron expression, got {self.schedule_cron!r}"
            )
