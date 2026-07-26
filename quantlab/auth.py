"""Analytics-token loading and expiry inspection.

The Upstox *Analytics token* is a long-lived (1-year), read-only JWT
generated from the Upstox Developer Apps page.  It grants access to the
Fundamentals APIs without any trading scope.

Security rules enforced here:

* The token value is read ONLY from the ``UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN``
  environment variable, with a gitignored ``quantlab_token.txt`` at the repo
  root as fallback.  It is never hardcoded and NEVER logged — log lines carry
  only a one-way SHA-256 fingerprint prefix.
* Expiry is decoded from the JWT payload WITHOUT verifying the signature —
  this is purely an early-warning aid, never an authentication step.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .config import TOKEN_ENV_VAR, QuantLabSettings
from .errors import AuthTokenError

logger = logging.getLogger("quantlab.auth")

#: Warn when the token expires within this window.
EXPIRY_WARNING = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """Non-sensitive metadata about the loaded token (safe to log)."""

    fingerprint: str
    expires_at: datetime | None
    is_expired: bool
    expires_soon: bool


def load_token(
    settings: QuantLabSettings,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the Analytics token from env var, else the gitignored file.

    Raises :class:`AuthTokenError` with setup guidance when absent.
    """
    if env is None:
        import os

        env = os.environ
    token = env.get(TOKEN_ENV_VAR, "").strip()
    if token:
        logger.debug("token loaded source=env fingerprint=%s", token_fingerprint(token))
        return token

    token_file = settings.token_file
    try:
        if token_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:  # unreadable file — treat as absent but say why
        logger.warning("token file unreadable path=%s error=%s", token_file, exc)
    if token:
        logger.debug("token loaded source=file fingerprint=%s", token_fingerprint(token))
        return token

    raise AuthTokenError(
        "No Upstox Analytics token found. Set the "
        f"{TOKEN_ENV_VAR} environment variable, or place the token in "
        f"{token_file} (that file is gitignored). Generate one from the "
        "Upstox Developer Apps page > Analytics tab."
    )


def token_fingerprint(token: str) -> str:
    """One-way, non-reversible short identifier for correlating log lines."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def decode_jwt_expiry(token: str) -> datetime | None:
    """Best-effort decode of the JWT ``exp`` claim WITHOUT signature checks.

    Returns ``None`` when the token is not a decodable JWT — that is not an
    error (Upstox may change token formats); it only disables expiry warnings.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_raw = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(payload_raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def inspect_token(token: str, *, now: datetime | None = None) -> TokenInfo:
    """Build :class:`TokenInfo` and emit expiry warnings (never the token)."""
    now = now or datetime.now(timezone.utc)
    expires_at = decode_jwt_expiry(token)
    is_expired = expires_at is not None and expires_at <= now
    expires_soon = (
        expires_at is not None and not is_expired and expires_at - now <= EXPIRY_WARNING
    )
    info = TokenInfo(
        fingerprint=token_fingerprint(token),
        expires_at=expires_at,
        is_expired=is_expired,
        expires_soon=expires_soon,
    )
    if is_expired:
        logger.warning(
            "analytics token EXPIRED fingerprint=%s expired_at=%s — generate a "
            "fresh token from the Upstox Developer Apps page",
            info.fingerprint,
            expires_at.isoformat() if expires_at else "?",
        )
    elif expires_soon:
        logger.warning(
            "analytics token expires soon fingerprint=%s expires_at=%s",
            info.fingerprint,
            expires_at.isoformat() if expires_at else "?",
        )
    elif expires_at is None:
        logger.debug(
            "token expiry not decodable fingerprint=%s (not a standard JWT?)",
            info.fingerprint,
        )
    return info
