"""Typed exception hierarchy for the Quant Lab fundamentals pipeline.

Network failures are *expected* operational events: they are caught, logged
and recorded in ``_sync_state`` so the next run retries them.  Programmer
errors (bad endpoint name, malformed configuration) raise immediately and
loudly — they must never be swallowed.
"""
from __future__ import annotations


class QuantLabError(Exception):
    """Base class for every error raised by the ``quantlab`` package."""


class QuantLabConfigError(QuantLabError):
    """Invalid or missing configuration (bad env var value, bad cron, ...)."""


class AuthTokenError(QuantLabError):
    """The Analytics token is missing, rejected (401/403) or expired."""


class MissingDependencyError(QuantLabError):
    """An optional runtime dependency (duckdb / apscheduler) is not installed."""


class QuantLabAPIError(QuantLabError):
    """A non-retryable (or retries-exhausted) Upstox API failure.

    Attributes
    ----------
    status_code:
        HTTP status of the last response, if one was received.
    error_code:
        Upstox error code (e.g. ``UDAPI1206`` for an invalid ISIN) when the
        response body contained one.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
