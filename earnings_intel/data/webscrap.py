"""
Powerful fetch layer - the user's 'webscrap' (Scrapling) engine - with graceful
fallback so nothing hard-depends on it.

  http_session()           -> curl_cffi Session (Chrome-TLS impersonation) or requests
  fetch_json(url, ...)     -> curl_cffi -> requests          (JSON APIs, e.g. BSE)
  fetch_text(url, stealth) -> StealthyFetcher(Camoufox) -> curl_cffi -> requests  (HTML/PDF)

curl_cffi impersonates a real Chrome TLS handshake, which clears many anti-bot
blocks (incl. BSE's) without a browser. StealthyFetcher is the heavy fallback.

Install once on the machine that runs scanX.bat:
    pip install scrapling curl_cffi playwright
    python -m scrapling install      # downloads the Camoufox stealth browser
Without these it silently falls back to plain requests.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Optional

log = logging.getLogger("technofunda.webscrap")

try:
    from curl_cffi import requests as _curl
except Exception:  # noqa: BLE001
    _curl = None
try:
    import requests as _req
except Exception:  # noqa: BLE001
    _req = None

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def engines() -> dict:
    """Which fetch engines are available (for diagnostics/logging)."""
    scr = False
    try:
        import scrapling.fetchers  # noqa: F401
        scr = True
    except Exception:  # noqa: BLE001
        scr = False
    return {"curl_cffi": _curl is not None, "requests": _req is not None, "scrapling": scr}


def http_session():
    """A browser-TLS-impersonating session (curl_cffi) or a plain requests session."""
    if _curl is not None:
        try:
            return _curl.Session(impersonate="chrome")
        except Exception as e:  # noqa: BLE001
            log.warning("curl_cffi session failed (%s); using requests", e)
    if _req is not None:
        return _req.Session()
    raise ImportError("no HTTP library available (need curl_cffi or requests)")


def fetch_json(url, params=None, headers=None, timeout=25):
    h = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        h.update(headers)
    if _curl is not None:
        try:
            r = _curl.get(url, params=params, headers=h, timeout=timeout, impersonate="chrome")
            if r.status_code == 200:
                return r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("curl_cffi json failed: %s", e)
    if _req is not None:
        try:
            r = _req.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("requests json failed: %s", e)
    # last resort: render via the Camoufox stealth browser and parse JSON from the body
    try:
        from scrapling.fetchers import StealthyFetcher
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        page = StealthyFetcher.fetch(full, headless=True, network_idle=True, timeout=timeout * 1000)
        if page is not None and getattr(page, "status", 0) == 200:
            txt = page.get_all_text() or getattr(page, "body", "") or ""
            m = __import__("re").search(r"(\{.*\}|\[.*\])", txt, 16)  # 16 = re.S
            if m:
                return json.loads(m.group(1))
    except Exception as e:  # noqa: BLE001
        log.warning("StealthyFetcher json failed: %s", e)
    return None


def _stealth_text(url, timeout):
    try:
        from scrapling.fetchers import StealthyFetcher
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True,
                                     timeout=timeout * 1000)
        if page is not None and getattr(page, "status", 0) == 200:
            return getattr(page, "body", None) or page.get_all_text()
    except Exception as e:  # noqa: BLE001
        log.warning("StealthyFetcher failed: %s", e)
    return None


def fetch_text(url, params=None, headers=None, timeout=25, stealth=False):
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if stealth:
        t = _stealth_text(url, timeout)
        if t:
            return t
    if _curl is not None:
        try:
            r = _curl.get(url, params=params, headers=h, timeout=timeout, impersonate="chrome")
            if r.status_code == 200:
                return r.text
        except Exception as e:  # noqa: BLE001
            log.warning("curl_cffi text failed: %s", e)
    if _req is not None:
        try:
            r = _req.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 200:
                return r.text
        except Exception as e:  # noqa: BLE001
            log.warning("requests text failed: %s", e)
    return None


def fetch_bytes(url, headers=None, timeout=30):
    """Fetch raw bytes (e.g. a filing PDF) via curl_cffi (Chrome-TLS) -> requests."""
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if _curl is not None:
        try:
            r = _curl.get(url, headers=h, timeout=timeout, impersonate="chrome")
            if r.status_code == 200:
                return r.content
        except Exception as e:  # noqa: BLE001
            log.warning("curl_cffi bytes failed: %s", e)
    if _req is not None:
        try:
            r = _req.get(url, headers=h, timeout=timeout)
            if r.status_code == 200:
                return r.content
        except Exception as e:  # noqa: BLE001
            log.warning("requests bytes failed: %s", e)
    return None
