"""Tests for the webscrap fetch layer (engine detection + session, no network)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import webscrap as w   # noqa: E402


def test_engines():
    e = w.engines()
    assert set(e) == {"curl_cffi", "requests", "scrapling"}
    assert all(isinstance(v, bool) for v in e.values())


def test_http_session_has_get():
    s = w.http_session()
    assert hasattr(s, "get")
