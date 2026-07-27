"""
Document ANALYSIS engine — turns a company's own filings (concall transcript /
PPT / notes, annual report) into GROUNDED FACTS, never links and never opinion.

Each selected document is read once by an LLM (Gemini by default, OpenAI /
Anthropic hooks kept from ai_chat) with one prompt that demands, for every
claim, a VERBATIM quote copied out of that document. The model's JSON is then
run through `enforce_grounding`, a pure function that checks each quote against
the document's own text (whitespace/typography-normalised) and DROPS anything it
cannot find — that is what makes the output "facts informed by the company"
rather than model opinion. Facts are then merged newest-first across documents
and near-identical claims are deduped.

Everything except the fetch and the model call is pure and unit-tested; tests
inject a fake `fetch` and a fake model response, so no network and no API key.

Usage:
    from earnings_intel.data import docanalysis as da

    docs = [{"kind": "concall_transcript", "date": "2026-05-01",
             "title": "Q4 FY26 Earnings Call Transcript",
             "url": "https://…/transcript.pdf", "source": "screener"}]
    out = da.analyse_documents(docs, "Tata Motors Ltd")
    meta = out.pop("_meta")            # -> contract "analysis" + "_meta"
    print(meta["facts_kept"], meta["facts_dropped_ungrounded"])

    # pure helpers
    da.is_grounded("EBITDA margin of 12-14%", "…we guided to an\nEBITDA margin of 12-14%.")
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Optional

from . import insights_ai as ia

log = logging.getLogger("technofunda.docanalysis")

# the seven contract themes, in contract order
THEMES = ("guidance", "demand_outlook", "capex_expansion", "margins_costs",
          "orders_capacity", "capital_allocation", "risks_headwinds")

_THEME_ALIASES = {
    "guidance_targets": "guidance", "targets": "guidance", "outlook": "guidance",
    "demand": "demand_outlook", "demand_outlook_": "demand_outlook",
    "demand_and_outlook": "demand_outlook", "volume_outlook": "demand_outlook",
    "capex": "capex_expansion", "expansion": "capex_expansion",
    "capex_and_expansion": "capex_expansion", "capacity_expansion": "capex_expansion",
    "margins": "margins_costs", "costs": "margins_costs", "margin": "margins_costs",
    "margins_and_costs": "margins_costs", "cost": "margins_costs",
    "orders": "orders_capacity", "order_book": "orders_capacity",
    "orderbook": "orders_capacity", "capacity": "orders_capacity",
    "orders_and_capacity": "orders_capacity",
    "capital": "capital_allocation", "capital_allocation_dividend": "capital_allocation",
    "dividend": "capital_allocation", "balance_sheet": "capital_allocation",
    "risks": "risks_headwinds", "risk": "risks_headwinds", "headwinds": "risks_headwinds",
    "risks_and_headwinds": "risks_headwinds",
}

# announcements are one-liners — used as context only, never deep-analysed
_SKIP_KINDS = ("announcement",)
_KIND_RANK = {"concall_transcript": 4, "concall_ppt": 3, "annual_report": 2,
              "concall_notes": 1}

_MAX_DOCS = 4                 # documents sent to the model per company
_MAX_PAGES = 40               # PDF pages read per document
_DOC_CHARS = 140000           # prompt budget per document
_MIN_QUOTE_CHARS = 12         # a "quote" shorter than this proves nothing
_DEDUPE_RATIO = 0.88          # near-identical claim threshold
_MAX_HEADLINES = 6

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("‚"): "'",
           ord("“"): '"', ord("”"): '"', ord("„"): '"'}
_DROP = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)


# ------------------------------------------------------------- pure: grounding
def normalise(text: str) -> str:
    """Whitespace/typography-collapse used for the grounding comparison.

    NFKC-folds the text, maps smart quotes and dash variants to ASCII, drops
    zero-width/soft-hyphen artefacts of PDF extraction, then collapses every run
    of whitespace (incl. the newlines pdfplumber sprinkles mid-sentence) to a
    single space. Case is preserved — a quote must still be verbatim.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.translate(_DROP).translate(_DASHES).translate(_QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def is_grounded(quote: str, source_text: str) -> bool:
    """True when `quote` appears VERBATIM in `source_text` (both normalised).

    Anything shorter than _MIN_QUOTE_CHARS, or checked against no source text,
    is treated as ungrounded — we can only publish what the company wrote.
    """
    q = normalise(quote)
    if len(q) < _MIN_QUOTE_CHARS:
        return False
    src = normalise(source_text)
    if not src:
        return False
    return q in src


# ------------------------------------------------------------------ pure: fact
@dataclass
class Fact:
    """One stated fact + the verbatim sentence from the filing that proves it."""
    claim: str
    quote: str
    doc_kind: str = ""
    doc_date: str = ""
    url: str = ""
    timeframe: Optional[str] = None      # set for management_commitments only

    def as_dict(self) -> dict:
        d = {"claim": self.claim, "quote": self.quote, "doc_kind": self.doc_kind,
             "doc_date": self.doc_date, "url": self.url}
        if self.timeframe is not None:
            d["timeframe"] = self.timeframe
        return d


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _coerce_fact(raw, kind: str, date: str, url: str,
                 commitment: bool = False) -> Optional[Fact]:
    """Model object -> Fact. Provenance always comes from OUR document row, never
    from the model. Returns None when claim or quote is missing."""
    if not isinstance(raw, dict):
        return None
    claim = str(raw.get("claim") or raw.get("fact") or "").strip()
    quote = str(raw.get("quote") or raw.get("evidence") or "").strip()
    if not claim or not quote:
        return None
    tf = None
    if commitment:
        tf = str(raw.get("timeframe") or raw.get("period") or "").strip()
    return Fact(claim=claim[:600], quote=quote[:1200], doc_kind=kind,
                doc_date=date, url=url, timeframe=tf)


def _canon_theme(key) -> Optional[str]:
    k = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if k in THEMES:
        return k
    return _THEME_ALIASES.get(k)


def enforce_grounding(parsed: dict, doc: dict, source_text: str) -> tuple[dict, int]:
    """Drop every fact whose quote is not verbatim in `source_text`. PURE.

    Returns ({summary, themes, management_commitments}, facts_dropped). A fact
    missing its claim or quote counts as dropped too — no claim may be emitted
    without supporting evidence.
    """
    d = doc or {}
    kind = str(d.get("kind") or "")
    date = str(d.get("date") or "")
    url = str(d.get("url") or "")
    src = source_text or ""
    dropped = 0

    raw_themes = (parsed or {}).get("themes")
    buckets: dict = {t: [] for t in THEMES}
    if isinstance(raw_themes, dict):
        for key, vals in raw_themes.items():
            t = _canon_theme(key)
            if t:
                buckets[t].extend(_as_list(vals))

    themes: dict = {}
    for t in THEMES:
        kept = []
        for raw in buckets[t]:
            f = _coerce_fact(raw, kind, date, url)
            if f is None or not is_grounded(f.quote, src):
                dropped += 1
                if f is not None:
                    log.debug("ungrounded fact dropped (%s): %.60s", t, f.quote)
                continue
            kept.append(f.as_dict())
        themes[t] = kept

    commitments = []
    for raw in _as_list((parsed or {}).get("management_commitments")):
        f = _coerce_fact(raw, kind, date, url, commitment=True)
        if f is None or not is_grounded(f.quote, src):
            dropped += 1
            continue
        commitments.append(f.as_dict())

    return ({"summary": str((parsed or {}).get("summary") or "").strip(),
             "themes": themes, "management_commitments": commitments}, dropped)


# ---------------------------------------------------------- pure: merge/dedupe
def _claim_key(claim: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", normalise(claim).lower())
    return re.sub(r"\s+", " ", s).strip()


def dedupe_facts(facts: list) -> list:
    """Drop repeats of the same claim (near-identical wording) or the same quote."""
    out: list = []
    keys: list = []
    quotes: set = set()
    for f in facts or []:
        if not isinstance(f, dict):
            continue
        k = _claim_key(f.get("claim") or "")
        q = normalise(f.get("quote") or "").lower()
        if not k or not q:
            continue
        if q in quotes:
            continue
        if any(k == kk or SequenceMatcher(None, k, kk).ratio() >= _DEDUPE_RATIO
               for kk in keys):
            continue
        out.append(f)
        keys.append(k)
        quotes.add(q)
    return out


def merge_facts(groups: list) -> list:
    """Flatten per-document fact lists newest-first, then dedupe."""
    flat = [f for g in (groups or []) for f in (g or [])]
    flat.sort(key=lambda f: str((f or {}).get("doc_date") or ""), reverse=True)
    return dedupe_facts(flat)


def merge_summaries(summaries: list, max_sentences: int = 5) -> str:
    """Per-document summaries (newest first) -> one 3-5 sentence synthesis."""
    out: list = []
    seen: set = set()
    for s in summaries or []:
        for sent in re.split(r"(?<=[.!?])\s+", normalise(s)):
            sent = sent.strip()
            if not sent:
                continue
            k = _claim_key(sent)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(sent)
            if len(out) >= max_sentences:
                return " ".join(out)
    return " ".join(out)


# ------------------------------------------------------- pure: doc selection
def select_documents(docs: list, limit: int = _MAX_DOCS) -> list:
    """Most recent analysable documents, preferring transcript > ppt > annual
    report > notes when several share a date. Announcements are never deep-read."""
    picked = []
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        kind = str(d.get("kind") or "").strip().lower()
        if kind in _SKIP_KINDS or not str(d.get("url") or "").strip():
            continue
        picked.append(d)
    picked.sort(key=lambda d: (str(d.get("date") or ""),
                               _KIND_RANK.get(str(d.get("kind") or "").lower(), 1)),
                reverse=True)
    return picked[:max(0, int(limit or 0))]


def announcement_headlines(docs: list, limit: int = _MAX_HEADLINES) -> list:
    """Announcement titles, newest first — cheap context, never deep-analysed."""
    anns = [d for d in (docs or [])
            if isinstance(d, dict)
            and str(d.get("kind") or "").strip().lower() in _SKIP_KINDS
            and str(d.get("title") or "").strip()]
    anns.sort(key=lambda d: str(d.get("date") or ""), reverse=True)
    return [f"{str(d.get('date') or '')} — {str(d.get('title')).strip()}"[:220]
            for d in anns[:limit]]


# ---------------------------------------------------------- pure: prompt build
_SYSTEM = """You are an equity research desk analyst reading ONE document published by an Indian listed company (NSE/BSE).

Report ONLY what the company or its management actually stated in this document.

HARD RULES
- Every claim MUST carry a "quote" copied character-for-character from the DOCUMENT TEXT below. Never paraphrase, correct, translate, shorten or stitch together separated sentences inside a quote. A claim whose quote cannot be found verbatim in the document is DISCARDED.
- No inference, no opinion, no outside knowledge, no estimates of your own, no price targets, no buy/sell/hold recommendation, no peer comparison.
- If a theme has nothing stated in this document, return an empty list for it. Never pad a theme.
- Keep each quote to at most ~60 words, and at most 6 facts per theme.
- Return ONE JSON object and nothing else."""

_SCHEMA = """OUTPUT (one JSON object with exactly these keys):
- "summary": 3-5 sentences, factual, describing what management said in THIS document.
- "themes": an object with exactly these seven keys, each a list of FACT objects:
    guidance, demand_outlook, capex_expansion, margins_costs, orders_capacity,
    capital_allocation, risks_headwinds
- "management_commitments": a list of FACT objects, each with one extra key
    "timeframe" ("FY27", "H2 FY26", "next 2 years", or "" if no period was stated).

A FACT object has exactly these keys:
- "claim": one sentence stating what the company said (no number that is not in the quote).
- "quote": the VERBATIM sentence(s) from the DOCUMENT TEXT that prove the claim."""

_THEME_GUIDE = """THEMES:
- guidance: explicit forward guidance, targets or outlook management gave (revenue, volume, margin, growth).
- demand_outlook: what management said about end-market demand, order inflow enquiry levels, volumes, pricing environment.
- capex_expansion: capex amounts, new plants/lines, greenfield/brownfield, commissioning timelines, acquisitions.
- margins_costs: gross/EBITDA margin commentary, raw material and input costs, operating leverage, cost programmes.
- orders_capacity: order book, order inflow, executable order value, installed capacity and utilisation.
- capital_allocation: debt reduction, dividend/buyback, fund raise, working capital, cash flow deployment.
- risks_headwinds: risks, headwinds, regulatory/tariff/currency issues, one-offs and disruptions management flagged."""


def build_prompt(company: str, doc: dict, text: str,
                 headlines: Optional[list] = None) -> str:
    """One document -> one grounded-extraction prompt. PURE."""
    d = doc or {}
    parts = [_SYSTEM, _THEME_GUIDE, _SCHEMA]
    parts.append("==== COMPANY ====\n" + (str(company or "").strip() or "(unknown)"))
    parts.append("==== DOCUMENT ====\n"
                 f"kind: {d.get('kind') or ''}\n"
                 f"date: {d.get('date') or ''}\n"
                 f"title: {d.get('title') or ''}\n"
                 f"source: {d.get('source') or ''}")
    if headlines:
        parts.append("==== RECENT EXCHANGE ANNOUNCEMENTS (context only — never quote these) ====\n"
                     + "\n".join(str(h) for h in headlines))
    parts.append('==== DOCUMENT TEXT ====\n"""\n' + (text or "")[:_DOC_CHARS] + '\n"""')
    return "\n\n".join(parts)


def _extract_obj(s: str) -> dict:
    """First JSON object in a model response (tolerates ``` fences and prose)."""
    if not s:
        return {}
    m = re.search(r"\{.*\}", s, re.S)
    raw = m.group(0) if m else s
    try:
        out = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return out if isinstance(out, dict) else {}


# -------------------------------------------------------------- fetch / model
def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html or "")


def _default_fetch(url: str, max_pages: int = _MAX_PAGES,
                   max_chars: int = _DOC_CHARS):
    """(text, err) for a filing URL — bot-resistant.

    Uses deepfetch.pdf_text (curl_cffi Chrome-TLS) because BSE blocks the plain
    requests path insights_ai.pdf_text uses; falls back to HTML text.
    """
    if not str(url or "").strip():
        return None, "no url"
    try:
        from . import deepfetch as dfx
        txt = dfx.pdf_text(url, max_pages=max_pages)
        if txt and txt.strip():
            return txt[:max_chars], None
    except Exception as e:  # noqa: BLE001
        log.warning("pdf fetch failed (%s): %s", url, e)
    try:
        from .webscrap import fetch_text
        html = fetch_text(url, timeout=45)
        txt = _strip_html(html) if html else ""
        if txt and txt.strip():
            return txt[:max_chars], None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    return None, "could not extract text from the document"


def _as_text_err(res):
    """Accept fetch() returning (text, err) or a bare text/None."""
    if isinstance(res, tuple):
        text, err = (list(res) + [None, None])[:2]
        return text, err
    return res, None


def _call_model(prompt: str, provider: str = "gemini", key: Optional[str] = None,
                model: Optional[str] = None) -> str:
    """One JSON-mode LLM call. Gemini by default (with insights_ai's model
    fallback chain); the OpenAI / Anthropic hooks are ai_chat's."""
    p = (provider or "gemini").strip().lower()
    if p in ("openai", "anthropic"):
        if not key:
            raise RuntimeError(f"{p} selected but no API key provided")
        from . import ai_chat as ac
        fn = ac._call_openai if p == "openai" else ac._call_anthropic
        return fn(prompt, key, model, 0)
    k = key or ia._api_key()
    if not k:
        raise RuntimeError("no Gemini API key")
    return ia._gemini(prompt, k, model or os.environ.get("GEMINI_MODEL", ia._DEFAULT_MODEL),
                      json_mode=True, temperature=0)


def _model_name(provider: str, model: Optional[str]) -> str:
    if (provider or "gemini").strip().lower() == "gemini":
        return model or os.environ.get("GEMINI_MODEL", ia._DEFAULT_MODEL)
    return model or provider


def _empty(error: str, model_name: str) -> dict:
    """Contract-shaped analysis with nothing in it — callers degrade gracefully."""
    return {"summary": "", "themes": {t: [] for t in THEMES},
            "management_commitments": [],
            "coverage": {"docs_analysed": 0, "kinds": []},
            "_meta": {"model": model_name, "grounded": True, "facts_kept": 0,
                      "facts_dropped_ungrounded": 0, "note": error},
            "error": error}


# ------------------------------------------------------------------ pipeline
def analyse_documents(docs: list, company: str, *,
                      fetch: Optional[Callable] = None,
                      key: Optional[str] = None,
                      model: Optional[str] = None,
                      provider: str = "gemini",
                      limit: int = _MAX_DOCS,
                      max_chars: int = _DOC_CHARS) -> dict:
    """Contract `documents` list -> contract `analysis` dict (with `_meta`).

    Reads up to `limit` documents (transcript > ppt > annual report, newest
    first), asks the model for quoted facts under the seven themes, enforces
    grounding against each document's own text, then merges newest-first and
    dedupes. Never raises: any failure comes back as {"error": ...} on an
    otherwise contract-shaped dict. `fetch(url) -> (text, err)` is injectable
    so the whole pipeline is testable without network or a key.
    """
    prov = (provider or "gemini").strip().lower()
    mname = _model_name(prov, model)

    if prov == "gemini" and not (key or ia.have_key()):
        return _empty("no Gemini API key — set GEMINI_API_KEY or add it to "
                      "gemin_api_key.md (kept local, never published)", mname)
    if prov in ("openai", "anthropic") and not key:
        return _empty(f"{prov} selected but no API key provided", mname)

    picked = select_documents(docs, limit=limit)
    if not picked:
        return _empty("no analysable documents (need a concall transcript, "
                      "presentation, notes or annual report)", mname)

    fetch = fetch or _default_fetch
    heads = announcement_headlines(docs)
    per_theme: dict = {t: [] for t in THEMES}
    commitments: list = []
    summaries: list = []
    kinds: list = []
    problems: list = []
    analysed = 0
    dropped = 0

    for doc in picked:
        url = str(doc.get("url") or "")
        label = str(doc.get("kind") or "document")
        try:
            text, err = _as_text_err(fetch(url))
        except Exception as e:  # noqa: BLE001
            text, err = None, f"{type(e).__name__}: {str(e)[:120]}"
        if err or not text:
            log.warning("skipping %s (%s): %s", label, url, err or "no text")
            problems.append(f"{label}: {err or 'no text'}")
            continue
        try:
            raw = _call_model(build_prompt(company, doc, text[:max_chars], heads),
                              provider=prov, key=key, model=model)
        except Exception as e:  # noqa: BLE001
            log.warning("model call failed for %s: %s", label, e)
            problems.append(f"{label}: {type(e).__name__}: {str(e)[:100]}")
            continue
        clean, ndrop = enforce_grounding(_extract_obj(raw), doc, text)
        dropped += ndrop
        analysed += 1
        kinds.append(label)
        if clean["summary"]:
            summaries.append(clean["summary"])
        for t in THEMES:
            per_theme[t].append(clean["themes"].get(t) or [])
        commitments.append(clean["management_commitments"])

    themes = {t: merge_facts(per_theme[t]) for t in THEMES}
    merged_commitments = merge_facts(commitments)
    kept = sum(len(v) for v in themes.values()) + len(merged_commitments)

    note = ("Facts are verbatim-grounded against the company's own documents; "
            "claims whose quote was not found were dropped.")
    if problems:
        note += " Skipped — " + "; ".join(problems[:3])
    out = {
        "summary": merge_summaries(summaries),
        "themes": themes,
        "management_commitments": merged_commitments,
        "coverage": {"docs_analysed": analysed, "kinds": list(dict.fromkeys(kinds))},
        "_meta": {"model": mname, "grounded": True, "facts_kept": kept,
                  "facts_dropped_ungrounded": dropped, "note": note[:400]},
    }
    if analysed == 0:
        out["error"] = ("; ".join(problems)[:300]
                        or "no document could be read or analysed")
    return out
