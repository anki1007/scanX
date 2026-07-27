"""HTTP client for the Upstox Fundamentals API (Analytics-token access).

Endpoint paths verified 2026-07-27 against the official documentation:

* https://upstox.com/developer/api-documentation/fundamentals/
* https://upstox.com/developer/api-documentation/get-company-profile/
* https://upstox.com/developer/api-documentation/get-balance-sheet/
* https://upstox.com/developer/api-documentation/get-cash-flow/
* https://upstox.com/developer/api-documentation/get-income-statement/
* https://upstox.com/developer/api-documentation/get-share-holdings/
* https://upstox.com/developer/api-documentation/get-key-ratios/
* https://upstox.com/developer/api-documentation/get-corporate-actions/
* https://upstox.com/developer/api-documentation/get-competitors/

All endpoints are ``GET https://api.upstox.com/v2/fundamentals/{isin}/...``
authenticated with ``Authorization: Bearer <analytics token>``.

The client provides:

* a data-driven ``ENDPOINTS`` registry (add new endpoints declaratively);
* retries with exponential backoff + jitter, honouring ``Retry-After`` on 429;
* a configurable token-bucket rate limiter;
* structured logging of every call (endpoint, isin, status, latency) —
  the token itself is NEVER logged.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import requests

from . import auth
from .config import UpstoxLabSettings
from .errors import AuthTokenError, UpstoxLabAPIError

logger = logging.getLogger("upstox_lab.client")

#: HTTP statuses that are worth retrying.
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Endpoint registry — add new fundamentals endpoints HERE, declaratively.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """Declarative description of one fundamentals endpoint.

    ``variants`` lists the query-parameter combinations a full sync ingests
    (e.g. consolidated + standalone).  ``{isin}`` in ``path`` is substituted
    per instrument.
    """

    name: str
    path: str
    description: str = ""
    variants: tuple[Mapping[str, str], ...] = field(default_factory=lambda: ({},))


_FS = {"fs": "true"}  # request the detailed full-statement breakdown

ENDPOINTS: dict[str, EndpointSpec] = {
    spec.name: spec
    for spec in (
        EndpointSpec(
            name="company_profile",
            path="/v2/fundamentals/{isin}/profile",
            description="Business description, sector, sector market cap.",
        ),
        EndpointSpec(
            name="balance_sheet",
            path="/v2/fundamentals/{isin}/balance-sheet",
            description="Yearly balance sheet; fs=true adds line items.",
            variants=(
                {"type": "consolidated", **_FS},
                {"type": "standalone", **_FS},
            ),
        ),
        EndpointSpec(
            name="cash_flow",
            path="/v2/fundamentals/{isin}/cash-flow",
            description="Operating/investing/financing cash flows.",
            variants=(
                {"type": "consolidated", **_FS},
                {"type": "standalone", **_FS},
            ),
        ),
        EndpointSpec(
            name="income_statement",
            path="/v2/fundamentals/{isin}/income-statement",
            description="Yearly or quarterly P&L.",
            variants=(
                {"type": "consolidated", "time_period": "yearly", **_FS},
                {"type": "consolidated", "time_period": "quarterly", **_FS},
                {"type": "standalone", "time_period": "yearly", **_FS},
                {"type": "standalone", "time_period": "quarterly", **_FS},
            ),
        ),
        EndpointSpec(
            name="share_holdings",
            path="/v2/fundamentals/{isin}/share-holdings",
            description="Quarterly shareholding pattern by holder type.",
        ),
        EndpointSpec(
            name="key_ratios",
            path="/v2/fundamentals/{isin}/key-ratios",
            description="P/E, P/B, ROA, ROE, ROCE, EV/EBITDA vs sector.",
        ),
        EndpointSpec(
            name="corporate_actions",
            path="/v2/fundamentals/{isin}/corporate-actions",
            description="Dividends, bonuses, splits, rights issues.",
        ),
        EndpointSpec(
            name="competitors",
            path="/v2/fundamentals/{isin}/competitors",
            description="Competitor instrument keys and profiles.",
        ),
    )
}


def variant_key(params: Mapping[str, str]) -> str:
    """Stable identifier for a query-parameter variant (used in sync state)."""
    material = {k: v for k, v in sorted(params.items()) if k != "fs"}
    if not material:
        return "default"
    return ",".join(f"{k}={v}" for k, v in material.items())


# ---------------------------------------------------------------------------
# Token-bucket rate limiter
# ---------------------------------------------------------------------------
class TokenBucket:
    """Thread-safe token bucket: ``rate`` tokens/second, ``capacity`` burst."""

    def __init__(
        self,
        rate: float,
        capacity: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a token is available; return seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                deficit = (1.0 - self._tokens) / self.rate
            self._sleep(deficit)
            waited += deficit


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class UpstoxFundamentalsClient:
    """Typed, rate-limited, retrying client over the ENDPOINTS registry."""

    def __init__(
        self,
        settings: UpstoxLabSettings,
        *,
        token: str | None = None,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        if token is None:
            token = auth.load_token(settings)
        self._token_info = auth.inspect_token(token)
        self.session = session if session is not None else requests.Session()
        # Merge instead of replace so a stubbed session's dict survives.
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )
        self.rate_limiter = TokenBucket(
            settings.rate_limit_rps,
            settings.rate_limit_burst,
            clock=clock,
            sleep=sleep,
        )

    # -- typed convenience methods -----------------------------------------
    def get_company_profile(self, isin: str) -> dict[str, Any]:
        return self.fetch("company_profile", isin)

    def get_balance_sheet(
        self, isin: str, *, statement_type: str = "consolidated", full_statement: bool = True
    ) -> dict[str, Any]:
        params = {"type": statement_type}
        if full_statement:
            params["fs"] = "true"
        return self.fetch("balance_sheet", isin, params=params)

    def get_cash_flow(
        self, isin: str, *, statement_type: str = "consolidated", full_statement: bool = True
    ) -> dict[str, Any]:
        params = {"type": statement_type}
        if full_statement:
            params["fs"] = "true"
        return self.fetch("cash_flow", isin, params=params)

    def get_income_statement(
        self,
        isin: str,
        *,
        statement_type: str = "consolidated",
        time_period: str = "yearly",
        full_statement: bool = True,
    ) -> dict[str, Any]:
        params = {"type": statement_type, "time_period": time_period}
        if full_statement:
            params["fs"] = "true"
        return self.fetch("income_statement", isin, params=params)

    def get_share_holdings(self, isin: str) -> dict[str, Any]:
        return self.fetch("share_holdings", isin)

    def get_key_ratios(self, isin: str) -> dict[str, Any]:
        return self.fetch("key_ratios", isin)

    def get_corporate_actions(self, isin: str) -> dict[str, Any]:
        return self.fetch("corporate_actions", isin)

    def get_competitors(self, isin: str) -> dict[str, Any]:
        return self.fetch("competitors", isin)

    # -- core fetch ----------------------------------------------------------
    def fetch(
        self,
        endpoint: str,
        isin: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one endpoint for one ISIN, with rate limiting and retries.

        Raises :class:`UpstoxLabAPIError` on non-retryable HTTP errors or when
        retries are exhausted, and :class:`AuthTokenError` on 401/403.
        A bad ``endpoint`` name is a programmer error -> ``ValueError``.
        """
        spec = ENDPOINTS.get(endpoint)
        if spec is None:
            raise ValueError(
                f"unknown endpoint {endpoint!r}; known: {sorted(ENDPOINTS)}"
            )
        url = self.settings.base_url + spec.path.format(isin=isin)
        query = dict(params) if params else {}
        attempts = self.settings.max_retries + 1
        last_error: str = "unknown error"
        last_status: int | None = None

        for attempt in range(1, attempts + 1):
            self.rate_limiter.acquire()
            started = self._clock()
            try:
                response = self.session.get(
                    url, params=query, timeout=self.settings.request_timeout
                )
            except requests.RequestException as exc:
                # Broad network failure: expected operationally -> log + retry.
                latency_ms = (self._clock() - started) * 1000.0
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
                logger.warning(
                    "fetch error endpoint=%s isin=%s attempt=%d/%d latency_ms=%.0f error=%s",
                    endpoint, isin, attempt, attempts, latency_ms, last_error,
                )
                if attempt < attempts:
                    self._sleep(self._backoff_delay(attempt))
                continue

            latency_ms = (self._clock() - started) * 1000.0
            status = response.status_code
            last_status = status
            logger.info(
                "fetch endpoint=%s isin=%s params=%s status=%s attempt=%d/%d latency_ms=%.0f",
                endpoint, isin, variant_key(query), status, attempt, attempts, latency_ms,
            )

            if status == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    last_error = f"invalid JSON body: {exc}"
                    if attempt < attempts:
                        self._sleep(self._backoff_delay(attempt))
                    continue
                if not isinstance(payload, dict):
                    raise UpstoxLabAPIError(
                        f"{endpoint}/{isin}: unexpected non-object JSON payload",
                        status_code=status,
                    )
                return payload

            if status in (401, 403):
                raise AuthTokenError(
                    f"{endpoint}/{isin}: HTTP {status} — the Analytics token was "
                    "rejected (expired or revoked?). Generate a fresh token from "
                    "the Upstox Developer Apps page."
                )

            body_snippet = _safe_snippet(response)
            if status in RETRYABLE_STATUSES:
                last_error = f"HTTP {status} {body_snippet}"
                if attempt < attempts:
                    delay = self._backoff_delay(attempt)
                    if status == 429:
                        retry_after = _parse_retry_after(response)
                        if retry_after is not None:
                            delay = max(retry_after, 0.0)
                    logger.warning(
                        "retrying endpoint=%s isin=%s status=%s delay_s=%.2f",
                        endpoint, isin, status, delay,
                    )
                    self._sleep(delay)
                continue

            # Non-retryable client error (400, 404, UDAPI12xx, ...)
            raise UpstoxLabAPIError(
                f"{endpoint}/{isin}: HTTP {status} {body_snippet}",
                status_code=status,
                error_code=_extract_error_code(response),
            )

        raise UpstoxLabAPIError(
            f"{endpoint}/{isin}: giving up after {attempts} attempts ({last_error})",
            status_code=last_status,
        )

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with +/-50% jitter, capped."""
        base = self.settings.backoff_base * (2.0 ** (attempt - 1))
        capped = min(base, self.settings.backoff_cap)
        return capped * self._rng.uniform(0.5, 1.5)


# ---------------------------------------------------------------------------
# response helpers
# ---------------------------------------------------------------------------
def _parse_retry_after(response: Any) -> float | None:
    raw = ""
    try:
        raw = str(response.headers.get("Retry-After", "")).strip()
    except (AttributeError, TypeError):
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form: fall back to computed backoff


def _extract_error_code(response: Any) -> str | None:
    """Pull an Upstox error code (e.g. UDAPI1206) out of an error body."""
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return None
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("errorCode") or errors[0].get("error_code")
        return str(code) if code else None
    code = body.get("errorCode") or body.get("error_code")
    return str(code) if code else None


def _safe_snippet(response: Any, limit: int = 200) -> str:
    try:
        text = str(response.text or "")
    except (AttributeError, UnicodeDecodeError):
        return ""
    return text[:limit].replace("\n", " ")
