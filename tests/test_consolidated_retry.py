"""A rate-limited consolidated page must never become a standalone P/E.

Measured against the live source: a rapid 30-company sweep returned HTTP 429 on
7 of the consolidated URLs. Under the previous "any non-200 falls back to
standalone" rule those 7 silently published the STANDALONE multiple as the
company's headline P/E -- BAJAJFINSV read 146 against a real 31.6, ADANIENSOL
426 against 67.7. Bulk baking makes throttling the common case, so this is the
difference between a correct number and a confidently wrong one.

404 still falls back, because a 404 genuinely means the company files no
consolidated statement.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import earnings_intel.data.company as C  # noqa: E402


class _Resp:
    def __init__(self, status, text="<html><h1>X</h1></html>"):
        self.status_code = status
        self.text = text
        self.headers = {}


class _Client:
    """Records every URL requested and replays a scripted status sequence."""

    def __init__(self, script):
        self.script = list(script)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.script.pop(0) if self.script else _Resp(200)


@pytest.fixture(autouse=True)
def _no_sleep_no_cache(monkeypatch):
    monkeypatch.setattr(C.time if hasattr(C, "time") else __import__("time"),
                        "sleep", lambda *_: None, raising=False)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_: None)
    C._FCACHE.clear()
    yield
    C._FCACHE.clear()


def _install(monkeypatch, script):
    client = _Client(script)
    monkeypatch.setattr(C, "_client", lambda *_a, **_k: client)
    return client


def test_a_429_is_retried_not_absorbed(monkeypatch):
    client = _install(monkeypatch, [_Resp(429), _Resp(429), _Resp(200)])
    C._retry_get("https://x/consolidated/", None, 5)
    assert len(client.urls) == 3, "gave up instead of retrying a rate limit"


def test_a_404_is_not_retried(monkeypatch):
    client = _install(monkeypatch, [_Resp(404)])
    r = C._retry_get("https://x/consolidated/", None, 5)
    assert r.status_code == 404
    assert len(client.urls) == 1, "wasted attempts on a page that is simply absent"


def test_persistent_rate_limit_errors_rather_than_returning_standalone(monkeypatch):
    """The whole point: a throttled fetch must not publish standalone numbers."""
    client = _install(monkeypatch, [_Resp(429)] * 8)
    out = C.fundamentals("BAJAJFINSV", timeout=5)
    assert "error" in out, "a rate-limited fetch produced a bundle anyway"
    assert not any(u.endswith("/company/BAJAJFINSV/") for u in client.urls), \
        "fell back to the standalone statement after a rate limit"


def test_a_404_still_falls_back_to_standalone(monkeypatch):
    """Companies with no subsidiaries file no consolidated statement."""
    client = _install(monkeypatch, [_Resp(404), _Resp(200)])
    C.fundamentals("SOMESMALLCO", timeout=5)
    assert any(u.endswith("/consolidated/") for u in client.urls)
    assert any(u.endswith("/company/SOMESMALLCO/") for u in client.urls), \
        "never tried standalone for a company that has no consolidated page"


def test_consolidated_is_still_tried_first(monkeypatch):
    client = _install(monkeypatch, [_Resp(200)])
    C.fundamentals("RELIANCE", timeout=5)
    assert client.urls[0].endswith("/consolidated/")
