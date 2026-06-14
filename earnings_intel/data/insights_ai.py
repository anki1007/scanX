"""
AI Insights extractor — scanX's own LLM agent that does what Screener's premium
"Insights" does: pull OPERATIONAL KPIs (capacity, production, sales volume,
realisations, utilisation, order book, segment metrics) out of a company's
investor presentation / annual report and shape them into the SAME grid the
Fundamental tab already renders — with no Screener-premium dependency.

Provider: Google Gemini (google-genai SDK). The API key is read LOCALLY from
gemin_api_key.md or $GEMINI_API_KEY and is never written to output or logs, and
never committed (gitignored). Any user can drop in their own key.

Anti-hallucination: every extracted number is GROUNDED — it must literally
appear in the source document, else the row is dropped. The pure shaping /
grounding logic is unit-tested without any network call.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("technofunda.insights_ai")

_ROOT = Path(__file__).resolve().parents[2]
_SCREENER = "https://www.screener.in"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_DEFAULT_MODEL = "gemini-2.5-flash"

_PROMPT = """You extract OPERATIONAL KPIs from an Indian listed company's investor presentation or annual report.

Company: {company}

From the DOCUMENT TEXT below, extract only concrete OPERATIONAL metrics that have a numeric value for a specific period — e.g. production, installed capacity, sales/dispatch volume, realisation, capacity utilisation, order book, store/branch count, ARPU, subscribers, segment volumes. Do NOT extract standard financials already found in financial statements (revenue, net profit, EPS, margins) unless they are operational/segment volumes.

For every datapoint output one JSON object with EXACTLY these keys:
- "metric": KPI name, e.g. "Iron Ore Production"
- "unit": unit as printed, e.g. "MTPA", "Lakh Tonnes", "%", "units"
- "freq": "yearly" or "quarterly"
- "period": period label exactly as written, e.g. "FY24", "Mar 2024", "Q3 FY25"
- "value": the number EXACTLY as printed in the document (keep decimals and commas; do not round or convert)

Rules: include ONLY numbers that literally appear in the text. Never invent or infer values. If unsure, omit it. Return a JSON array of objects and nothing else.

DOCUMENT TEXT:
\"\"\"
{text}
\"\"\""""


# ----------------------------------------------------------------- key (local)
def _api_key() -> Optional[str]:
    for ev in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(ev)
        if v and v.strip():
            return v.strip()
    for fn in ("gemin_api_key.md", "gemini_api_key.md", "gemini_api_key.txt"):
        p = _ROOT / fn
        if p.exists():
            try:
                txt = p.read_text(errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            m = re.search(r"AIza[0-9A-Za-z_\-]{20,}", txt)        # Gemini keys start AIza...
            if m:
                return m.group(0)
            for line in txt.splitlines():
                s = line.strip().strip("`").strip()
                if s and not s.startswith("#") and " " not in s and len(s) >= 20:
                    return s
    return None


def have_key() -> bool:
    return bool(_api_key())


# ----------------------------------------------------------- pure: parse/shape
def _extract_json(s: str) -> list:
    if not s:
        return []
    m = re.search(r"\[.*\]", s, re.S)
    raw = m.group(0) if m else s
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:  # noqa: BLE001
        return []


def _num_variants(v: str) -> set:
    s = str(v).strip()
    out = {s, s.replace(",", "")}
    try:
        out.add("%g" % float(s.replace(",", "")))   # 55.40 -> 55.4
    except ValueError:
        pass
    return {x for x in out if x}


_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _sort_key(p: str):
    pl = (p or "").lower()
    y = re.search(r"(20\d{2})", p or "")
    if y:
        yr = int(y.group(1))
    else:
        fy = re.search(r"fy\s?'?(\d{2})", pl)
        yr = 2000 + int(fy.group(1)) if fy else 0
    mq = 0
    for k, v in _MON.items():
        if k in pl:
            mq = v; break
    qm = re.search(r"q([1-4])", pl)
    if qm:
        mq = int(qm.group(1)) * 3
    return (yr, mq)


def _sort_periods(plist: list) -> list:
    try:
        return sorted(plist, key=_sort_key)
    except Exception:  # noqa: BLE001
        return plist


def verify_and_shape(rows: list, source_text: str) -> dict:
    """Drop ungrounded numbers (value must appear in the source), then shape into
    {yearly:{periods,rows}, quarterly:{periods,rows}} — the company._insights shape."""
    src = source_text or ""
    buckets = {"yearly": {}, "quarterly": {}}
    plists = {"yearly": [], "quarterly": []}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        metric = (r.get("metric") or "").strip()
        period = (r.get("period") or "").strip()
        val = str(r.get("value", "")).strip()
        unit = (r.get("unit") or "").strip()
        freq = "quarterly" if str(r.get("freq", "")).lower().startswith("q") else "yearly"
        if not metric or not period or not val:
            continue
        if src and not any(v in src for v in _num_variants(val)):   # grounding
            continue
        key = (metric, unit)
        buckets[freq].setdefault(key, {})[period] = val
        if period not in plists[freq]:
            plists[freq].append(period)

    def shape(freq):
        b = buckets[freq]
        if not b:
            return None
        return {"periods": _sort_periods(plists[freq]),
                "rows": [{"metric": k[0], "unit": k[1], "values": v} for k, v in b.items()]}

    out = {}
    for freq in ("yearly", "quarterly"):
        s = shape(freq)
        if s:
            out[freq] = s
    return out


# --------------------------------------------------------------- live pipeline
def find_source(code: str, session_id: Optional[str] = None):
    """Find the best operational source PDF (investor presentation > annual report)."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return None, None
    try:
        s = requests.Session(); s.headers.update({"User-Agent": _UA})
        if session_id:
            s.cookies.set("sessionid", session_id, domain=".screener.in")
        r = s.get(f"{_SCREENER}/company/{code}/", timeout=20)
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:  # noqa: BLE001
        return None, None
    cands = []
    for a in soup.find_all("a", href=True):
        h = a["href"]; t = a.get_text(" ", strip=True) or ""
        low = (h + " " + t).lower()
        is_doc = (".pdf" in low or "annualreport" in low or "bseindia" in low
                  or "nseindia" in low or t.lower() == "ppt" or "present" in low)
        if not is_doc:
            continue
        score = 0
        if "present" in low or t.lower() == "ppt":
            score += 5
        if "investor" in low:
            score += 4
        if "annual" in low and "report" in low:
            score += 3
        if ".pdf" in low:
            score += 1
        if any(w in low for w in ("transcript", "notes", "rating", "audio")):
            score -= 4
        yr = re.search(r"(20\d{2})", low)
        if yr:
            score += (int(yr.group(1)) - 2000) * 0.1     # prefer more recent
        if score > 0:
            cands.append((score, h, t))
    if not cands:
        return None, None
    cands.sort(key=lambda x: x[0], reverse=True)
    _, url, label = cands[0]
    if url.startswith("/"):
        url = _SCREENER + url
    return url, (label or "source document")


def pdf_text(url: str, max_pages: int = 40, max_chars: int = 140000):
    try:
        import requests
    except Exception:  # noqa: BLE001
        return None, "requests not installed"
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return None, "pdfplumber not installed (pip install pdfplumber)"
    try:
        r = requests.get(url, timeout=45, headers={"User-Agent": _UA})
        if r.status_code != 200 or b"%PDF" not in r.content[:2048]:
            return None, f"could not download a PDF (HTTP {r.status_code})"
        parts = []
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for pg in pdf.pages[:max_pages]:
                parts.append(pg.extract_text() or "")
                for tb in (pg.extract_tables() or []):
                    parts.append("\n".join(" | ".join((c or "") for c in row) for row in tb))
        return "\n".join(parts)[:max_chars], None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# Free-tier quotas are PER MODEL, so each fallback is a fresh bucket:
# demand-spiked 2.5-flash -> 2.5-flash-lite -> 2.0-flash.
_FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash"]


def _retryable(e: Exception) -> bool:
    s = str(e)
    return any(x in s for x in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
                                "overloaded", "high demand"))


class GeminiBusy(RuntimeError):
    """Every model in the fallback chain was overloaded or out of quota."""


def _gemini(prompt: str, key: str, model: str, json_mode: bool = True,
            temperature: float = 0) -> str:
    """One Gemini call with automatic model fallbacks when the primary is
    overloaded / out of free-tier quota (503/429) — keeps Insights and
    Ask-AI alive during demand spikes."""
    from google import genai           # google-genai SDK
    client = genai.Client(api_key=key)
    cfg = {"temperature": temperature}
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    last = None
    for m in dict.fromkeys([model] + _FALLBACK_MODELS):
        try:
            resp = client.models.generate_content(model=m, contents=prompt, config=cfg)
            return resp.text or ""
        except Exception as e:  # noqa: BLE001
            last = e
            if not _retryable(e):
                raise
            log.warning("Gemini %s unavailable (%s) — trying fallback", m, str(e)[:80])
    raise GeminiBusy(
        "all Gemini models busy or out of free-tier quota right now — wait ~1 min "
        "and retry, or switch provider (OpenAI/Anthropic) with your own key in the "
        f"AMA panel. Last error: {str(last)[:100]}")


def extract(text: str, company: str, key: Optional[str] = None, model: Optional[str] = None):
    key = key or _api_key()
    if not key:
        return [], "no Gemini API key (set GEMINI_API_KEY or put it in gemin_api_key.md)"
    model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
    try:
        raw = _gemini(_PROMPT.format(company=company, text=text[:140000]), key, model)
    except Exception as e:  # noqa: BLE001
        return [], f"Gemini error ({type(e).__name__}): {str(e)[:140]}"
    return _extract_json(raw), None


def build_insights(code: str, session_id: Optional[str] = None) -> dict:
    """Full on-demand pipeline -> insights dict (company._insights shape) + _meta, or {error}."""
    if not have_key():
        return {"error": "no Gemini API key — add it to gemin_api_key.md (kept local, never published)"}
    url, label = find_source(code, session_id)
    if not url:
        return {"error": "no investor-presentation / annual-report PDF found for this company"}
    text, err = pdf_text(url)
    if err:
        return {"error": err, "source": label}
    rows, err = extract(text, code)
    if err:
        return {"error": err, "source": label}
    shaped = verify_and_shape(rows, text)
    if not shaped:
        return {"error": "no grounded operational KPIs found in the source document", "source": label}
    shaped["_meta"] = {
        "source": label, "source_url": url,
        "model": os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL),
        "generated_by": "Gemini", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "AI-extracted from the source document and grounded against it. Verify before relying on it.",
    }
    return shaped
