"""Keep data-vendor names off the site.

The rule for scanX is that a page never says where a number came from. That is
a product decision, and it has to hold on every surface, not just the ones
someone remembered to write carefully: page copy, empty-state messages, error
messages, the "source:" line under a table, and the `source` strings baked into
docs/data/*.json by the refresh scripts.

This module is the single place that knows the vendor names, so a new page
cannot leak one by accident and a new refresh script cannot bake one in.

What is NOT redacted, deliberately:

  * NSE and BSE. Those are the exchanges the securities trade on, not the
    vendor we bought the data from. "NSE_EQ" is a segment identifier and
    "BSE 500325" is a scrip code; both are facts about the instrument.
  * Vahan. The auto page is *about* RTO registration counts -- the dataset is
    the subject matter, and stripping the name leaves the page meaningless.

Pure, no I/O, never raises.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["VENDORS", "redact", "redact_deep", "is_vendor_url"]

#: Vendor name -> what to say instead. Order matters: longest first, so
#: "screener.in" is consumed before the bare "screener" pattern sees it.
_RULES: tuple[tuple[re.Pattern, str], ...] = (
    # These two run FIRST because the generic substitution below turns them
    # into "the data provider flags a negative: ..." and "the data
    # provider_note", which are worse than what they replace.
    (re.compile(r"\bscreener\s+flags\s+a\s+(positive|negative)\b", re.I),
     lambda m: f"Flagged as a {m.group(1).lower()}"),
    # NO \b AFTER THESE. Written through a non-raw string it becomes a
    # literal backspace (0x08) and then matches nothing at all -- exactly
    # how it first shipped here, and how the quota regex in
    # refresh_debate.py shipped before it. A negative lookahead says the
    # same thing and cannot be mangled by an escape.
    (re.compile(r"screener_note(?![a-z])", re.I), "flagged_note"),
    (re.compile(r"screener_pro(?![a-z])", re.I), "flagged_pro"),
    (re.compile(r"screener_con(?![a-z])", re.I), "flagged_con"),
    (re.compile(r"screener\s+(pro|con):", re.I),
     lambda m: "Flagged as a " + ("positive:" if m.group(1).lower() == "pro" else "negative:")),
    (re.compile(r"\bscreener\.in\s+screen\b", re.I), "stock screen"),
    (re.compile(r"\bscreener\s+full[-\s]?text[-\s]?search\b", re.I), "full-text search"),
    (re.compile(r"\bscreener\s+/fii/", re.I), "institutional-flow filings"),
    (re.compile(r"\bscreener\s+/actions/buyback\b", re.I), "corporate-action filings"),
    (re.compile(r"\blogged[-\s]?in\s+screener\s+session\b", re.I), "logged-in session"),
    (re.compile(r"\bscreener\s+bundles\b", re.I), "baked bundles"),
    (re.compile(r"\bscreener\.in\b", re.I), "the data provider"),
    # Match the whole "Yahoo daily closes (yfinance)" phrase first. Replacing
    # the two names independently left "daily closes (daily closes)".
    (re.compile(r"\byahoo\s+daily\s+closes\s*\(\s*yfinance\s*\)", re.I), "daily closes"),
    (re.compile(r"\byahoo\s+daily\s+closes\b", re.I), "daily closes"),
    (re.compile(r"\byfinance\b", re.I), "daily closes"),
    (re.compile(r"\b(?:screener|upstox|yahoo|tradingview)\b", re.I),
     "the data provider"),
)

#: Hosts whose links would identify the provider if rendered.
#:
#: bseindia.com and nseindia.com are deliberately absent. A link to those is a
#: link to the EXCHANGE FILING -- an annual report, a concall transcript, a
#: disclosure -- which is the primary document the numbers are derived from and
#: the most useful thing on the page. Dropping those would remove real evidence
#: to satisfy a rule that is about not naming the data VENDOR.
VENDORS: tuple[str, ...] = ("screener.in", "upstox.com",
                            "tradingview.com", "finance.yahoo.com")

_URL_RE = re.compile(r"https?://\S+", re.I)

#: "Does this string name a vendor at all?" -- the gate for rewriting a nested
#: value. Deliberately NOT the catch-all rule above, which uses word boundaries
#: and therefore misses "screener_note(?![a-z])": an underscore is a word character, so
#: there is no boundary between "screener" and "_note".
_MENTIONS = re.compile(r"screener|upstox|yahoo|tradingview|yfinance", re.I)


def is_vendor_url(value: Any) -> bool:
    """True if `value` is a URL pointing at a data vendor."""
    if not isinstance(value, str):
        return False
    low = value.lower()
    return low.startswith(("http://", "https://")) and any(v in low for v in VENDORS)


def redact(text: Any) -> Any:
    """Replace vendor names in a display string. Non-strings pass through.

    A bare vendor URL becomes an empty string -- there is no neutral way to
    show a link that spells the host in the address bar.
    """
    if not isinstance(text, str) or not text:
        return text
    out = _URL_RE.sub(lambda m: "" if is_vendor_url(m.group()) else m.group(), text)
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    # Tidy the punctuation a removal can strand: "(  )", " ,", doubled spaces.
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip(" ·-—,")


#: JSON keys whose values reach a reader's eye.
_DISPLAY_KEYS = frozenset({"source", "prices", "note", "notes", "basis",
                           "caption", "subtitle", "description", "label",
                           "footer", "credit", "provider"})


def redact_deep(obj: Any, *, drop_vendor_urls: bool = True,
                _depth: int = 0) -> Any:
    """Walk a decoded-JSON structure and redact display strings. PURE.

    Two different scopes, on purpose:

    * `_DISPLAY_KEYS` are rewritten only at the TOP LEVEL of a document. Those
      are the "source: ..." lines the pages actually print. Nested `source`
      tags -- `upstox_ratios.pe.source`, and the like -- are provenance for our
      own debugging and never reach the DOM. Rewriting them would rechurn all
      5,499 fundamental bundles on every bake for no visible gain, burying real
      diffs in noise.

    * Vendor URLs are dropped at ANY depth, because a link is rendered with its
      host visible in the status bar and address bar no matter what the anchor
      text says, and these sit in per-row objects inside lists.

    A blanket string sweep is deliberately avoided: it would mangle company
    names and free-text announcements that legitimately contain a vendor word.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            k = str(key).lower()
            if drop_vendor_urls and k in ("url", "href", "link") and is_vendor_url(value):
                continue
            # A `source` at ANY depth is rewritten when its value actually
            # names a vendor. Debate evidence carries one per item, nested in a
            # list, and every published debate file shipped "Screener.in" in it.
            # Gating on the VALUE keeps the top-level-only rule for everything
            # else, so an ordinary nested string is still left alone.
            # `text` is the debate transcript, which IS rendered. The model
            # repeated "source: Screener.in" back out of a prompt that used to
            # carry it, so 361 published debates showed the vendor name inside
            # the argument itself. New debates are built from neutral source
            # labels; these are the ones already on disk.
            if (k in ("source", "fact", "family", "text", "evidence", "metric", "note")
                    and isinstance(value, str)
                    and _MENTIONS.search(value)):
                out[key] = redact(value)
            elif _depth == 0 and k in _DISPLAY_KEYS and isinstance(value, str):
                out[key] = redact(value)
            else:
                out[key] = redact_deep(value, drop_vendor_urls=drop_vendor_urls,
                                       _depth=_depth + 1)
        return out
    if isinstance(obj, list):
        # A list at the top level holds the document's rows; keep them at the
        # same depth so a row's own `source` is still treated as top level.
        return [redact_deep(v, drop_vendor_urls=drop_vendor_urls, _depth=_depth)
                for v in obj]
    return obj
