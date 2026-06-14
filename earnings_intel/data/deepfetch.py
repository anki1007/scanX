"""
Deep value extraction from official filing PDFs (BSE/NSE attachments).

When an order/buyback row has no value in the announcement text, fetch the
attached PDF (webscrap curl_cffi, Chrome-TLS) and pull the number from the PDF
text with pdfplumber. Best-effort, capped by the caller so the daily scan stays
light. Never raises - returns None on any failure.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

log = logging.getLogger("technofunda.deepfetch")


def pdf_text(url: str, max_pages: int = 6, timeout: int = 30) -> Optional[str]:
    if not url or ".pdf" not in url.lower():
        return None
    try:
        from .webscrap import fetch_bytes
        data = fetch_bytes(url, timeout=timeout)
        if not data:
            return None
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages[:max_pages])
    except Exception as e:  # noqa: BLE001
        log.warning("pdf_text failed (%s): %s", url, e)
        return None


def value_cr_from_pdf(url: str) -> Optional[float]:
    from .orders import parse_value_cr
    t = pdf_text(url)
    return parse_value_cr(t) if t else None


def buyback_price_from_pdf(url: str) -> Optional[float]:
    from .buybacks import parse_buyback_price
    t = pdf_text(url)
    return parse_buyback_price(t) if t else None
