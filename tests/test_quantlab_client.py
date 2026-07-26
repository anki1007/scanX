"""Client tests: retries, backoff, Retry-After, rate limiting — no network."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import pytest

from quantlab.client import ENDPOINTS, TokenBucket, UpstoxFundamentalsClient, variant_key
from quantlab.config import QuantLabSettings
from quantlab.errors import AuthTokenError, QuantLabAPIError

FAKE_TOKEN = "unit-test-token-not-real"  # deliberately not a JWT; never a live value
ISIN = "INE002A01018"


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------
class StubResponse:
    def __init__(self, status_code: int, payload=None, headers=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class StubSession:
    """Queue-driven requests.Session lookalike (records every GET)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("stub exhausted — unexpected extra request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_settings(tmp_path: Path, **overrides) -> QuantLabSettings:
    defaults = dict(
        db_path=tmp_path / "test.duckdb",
        token_file=tmp_path / "quantlab_token.txt",
        rate_limit_rps=1000.0,   # effectively unlimited in tests
        rate_limit_burst=1000,
        max_retries=3,
        backoff_base=0.25,
        backoff_cap=8.0,
    )
    defaults.update(overrides)
    return QuantLabSettings(**defaults)


def make_client(tmp_path: Path, responses, **settings_overrides):
    settings = make_settings(tmp_path, **settings_overrides)
    session = StubSession(responses)
    sleeps: list[float] = []
    client = UpstoxFundamentalsClient(
        settings, token=FAKE_TOKEN, session=session, sleep=sleeps.append
    )
    return client, session, sleeps


OK = {"status": "success", "data": {"company_profile": "x", "sector": "Energy"}}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry_covers_all_documented_endpoints():
    assert set(ENDPOINTS) == {
        "company_profile", "balance_sheet", "cash_flow", "income_statement",
        "share_holdings", "key_ratios", "corporate_actions", "competitors",
    }
    for spec in ENDPOINTS.values():
        assert "{isin}" in spec.path
        assert spec.path.startswith("/v2/fundamentals/")


def test_variant_key_is_stable_and_ignores_fs():
    assert variant_key({}) == "default"
    assert variant_key({"fs": "true"}) == "default"
    a = variant_key({"type": "consolidated", "time_period": "yearly", "fs": "true"})
    b = variant_key({"time_period": "yearly", "type": "consolidated"})
    assert a == b == "time_period=yearly,type=consolidated"


# ---------------------------------------------------------------------------
# fetch behaviour
# ---------------------------------------------------------------------------
def test_success_first_try_sets_auth_header_and_url(tmp_path):
    client, session, sleeps = make_client(tmp_path, [StubResponse(200, OK)])
    payload = client.get_company_profile(ISIN)
    assert payload == OK
    assert session.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert session.headers["Accept"] == "application/json"
    url, params = session.calls[0]
    assert url == f"https://api.upstox.com/v2/fundamentals/{ISIN}/profile"
    assert params == {}
    assert sleeps == []


def test_typed_method_passes_query_params(tmp_path):
    client, session, _ = make_client(tmp_path, [StubResponse(200, OK)])
    client.get_income_statement(ISIN, statement_type="standalone", time_period="quarterly")
    url, params = session.calls[0]
    assert url.endswith(f"/v2/fundamentals/{ISIN}/income-statement")
    assert params == {"type": "standalone", "time_period": "quarterly", "fs": "true"}


def test_retry_on_429_honors_retry_after(tmp_path):
    responses = [
        StubResponse(429, headers={"Retry-After": "3"}, text="slow down"),
        StubResponse(200, OK),
    ]
    client, session, sleeps = make_client(tmp_path, responses)
    payload = client.fetch("key_ratios", ISIN)
    assert payload == OK
    assert len(session.calls) == 2
    assert 3.0 in sleeps  # exact Retry-After value, not the computed backoff


def test_retry_on_500_uses_exponential_backoff_with_jitter(tmp_path):
    responses = [StubResponse(500, text="boom"), StubResponse(502), StubResponse(200, OK)]
    client, session, sleeps = make_client(tmp_path, responses)
    payload = client.fetch("share_holdings", ISIN)
    assert payload == OK
    assert len(session.calls) == 3
    assert len(sleeps) == 2
    # attempt 1: base*2^0 in [0.5x, 1.5x]; attempt 2: base*2^1 in [0.5x, 1.5x]
    assert 0.25 * 0.5 <= sleeps[0] <= 0.25 * 1.5
    assert 0.50 * 0.5 <= sleeps[1] <= 0.50 * 1.5


def test_network_errors_are_retried_then_raise_api_error(tmp_path):
    import requests

    responses = [requests.ConnectionError("nope")] * 4  # max_retries=3 -> 4 attempts
    client, session, _ = make_client(tmp_path, responses)
    with pytest.raises(QuantLabAPIError) as excinfo:
        client.fetch("company_profile", ISIN)
    assert len(session.calls) == 4
    assert "giving up" in str(excinfo.value)


def test_no_retry_on_400_and_error_code_extracted(tmp_path):
    body = {"status": "error", "errors": [{"errorCode": "UDAPI1206", "message": "Invalid ISIN"}]}
    responses = [StubResponse(400, payload=body, text="Invalid ISIN")]
    client, session, sleeps = make_client(tmp_path, responses)
    with pytest.raises(QuantLabAPIError) as excinfo:
        client.fetch("company_profile", "BADISIN00000")
    assert len(session.calls) == 1
    assert sleeps == []
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_code == "UDAPI1206"


def test_401_raises_auth_error_without_retry(tmp_path):
    client, session, _ = make_client(tmp_path, [StubResponse(401, text="unauthorized")])
    with pytest.raises(AuthTokenError):
        client.fetch("company_profile", ISIN)
    assert len(session.calls) == 1


def test_unknown_endpoint_is_programmer_error(tmp_path):
    client, _, _ = make_client(tmp_path, [])
    with pytest.raises(ValueError, match="unknown endpoint"):
        client.fetch("not_an_endpoint", ISIN)


def test_token_never_appears_in_logs(tmp_path, caplog):
    client, _, _ = make_client(tmp_path, [StubResponse(200, OK)])
    with caplog.at_level("DEBUG", logger="quantlab"):
        client.get_company_profile(ISIN)
    assert FAKE_TOKEN not in caplog.text


# ---------------------------------------------------------------------------
# token bucket
# ---------------------------------------------------------------------------
def test_token_bucket_enforces_rate():
    now = {"t": 0.0}
    sleeps: list[float] = []

    def clock() -> float:
        return now["t"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds  # advancing the clock refills the bucket

    bucket = TokenBucket(rate=2.0, capacity=1, clock=clock, sleep=sleep)
    assert bucket.acquire() == 0.0          # burst token
    bucket.acquire()                        # must wait ~1/2s
    bucket.acquire()                        # and again
    assert len(sleeps) == 2
    for waited in sleeps:
        assert waited == pytest.approx(0.5, rel=1e-6)


def test_token_bucket_burst_capacity():
    now = {"t": 0.0}
    sleeps: list[float] = []
    bucket = TokenBucket(
        rate=1.0, capacity=3,
        clock=lambda: now["t"],
        sleep=lambda s: (sleeps.append(s), now.__setitem__("t", now["t"] + s)),
    )
    for _ in range(3):
        bucket.acquire()
    assert sleeps == []                     # burst absorbed
    bucket.acquire()
    assert len(sleeps) == 1                 # 4th call had to wait


def test_token_bucket_rejects_bad_params():
    with pytest.raises(ValueError):
        TokenBucket(rate=0.0, capacity=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=1.0, capacity=0)
