"""
Deterministic parsing + financial extraction for Indian company filings.

NO LLM, no network, no global state — every function here is pure and takes its
input as an argument, so the same code runs inside the static GitHub-Pages bake
(``python -m ... > json``) and, unchanged, inside a FastAPI/Celery worker later.
Only :func:`extract_tables` touches IO, and it lazily imports ``pdfplumber`` and
degrades to ``[]`` when the dependency or the file is unavailable.

This is stage 1 of the agentic-RAG document pipeline: it turns a filing into
clean text, sections, chunks and a structured metric dict, so the model adapter
(``data/docanalysis.py`` today, anything tomorrow) only ever has to *read*.

Usage::

    from earnings_intel.docpipe import parse as dp

    dp.detect_type("May 2026 Concall Transcript", "https://bse…/tr.pdf")
    'concall_transcript'

    text = dp.clean_text(raw_pdf_text)              # de-hyphenated, de-headered
    for sec in dp.split_sections(text):
        print(sec.title, sec.start, sec.end)        # 'MD&A' 10432 28110

    for ch in dp.chunk(text, target_chars=1800, overlap=200, section="MD&A"):
        embed(ch.text)                              # ch.char_start/char_end map back

    fin = dp.extract_financials(text)
    fin["revenue"]
    {'value': 4521.0, 'unit': 'cr',
     'quote': 'Revenue from operations for the quarter stood at Rs. 4,521 crore.'}

Every extracted number carries the verbatim sentence it came from, so it can be
re-checked against the source exactly the way ``docanalysis.is_grounded`` does.
Nothing here ever emits a bare float.
"""
from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Iterable, Optional

log = logging.getLogger("technofunda.docpipe.parse")

__all__ = [
    "Section", "Chunk", "DOC_TYPES", "SECTION_TITLES",
    "detect_type", "clean_text", "split_sections", "chunk", "chunk_document",
    "extract_financials", "extract_tables", "parse_amount", "sentences",
]

# ---------------------------------------------------------------- doc types
DOC_TYPES = (
    "annual_report", "drhp", "integrated_report", "esg_report",
    "concall_transcript", "concall_ppt", "investor_presentation",
    "announcement", "quarterly_results", "unknown",
)

SECTION_TITLES = (
    "MD&A", "Directors' Report", "Risk Factors", "Auditor's Report",
    "Related Party", "Corporate Governance", "ESG/BRSR", "Notes to Accounts",
)

# ------------------------------------------------------------------ tunables
_MAX_QUOTE = 600          # a metric quote never grows past this many chars
_MAX_GUIDANCE = 8         # guidance sentences kept
_HEADER_MIN_REPEATS = 3   # a line must repeat this often to count as furniture
_HEADER_MAX_CHARS = 90
_HEADER_MAX_WORDS = 12
_HEADING_MAX_CHARS = 120
_HEADING_COVER = 0.6      # heading pattern must cover 60% of the line's letters
_LABEL_MAX_DIST = 100     # chars allowed between a metric label and its number

_ZERO_WIDTH = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)


# ============================================================== detect_type ==
# Precedence, highest first. The FIRST rule that matches title+url wins; only if
# nothing matches there do the text rules run; then "unknown".
#
#   1 drhp                  offer documents outrank everything they quote
#   2 integrated_report     "Integrated Annual Report" is an integrated report
#   3 esg_report            BRSR / sustainability, before plain annual report
#   4 annual_report
#   5 concall_transcript    transcript beats the deck published with it
#   6 concall_ppt
#   7 investor_presentation generic decks, after the earnings deck
#   8 quarterly_results     results outrank the board-meeting wrapper
#   9 announcement          catch-all for exchange filings
#  10 unknown
_TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("drhp", (
        r"\bdrhp\b", r"\brhp\b", r"draft\s+red\s+herring", r"red\s+herring\s+prospectus",
        r"\bprospectus\b", r"offer\s+document",
    )),
    ("integrated_report", (
        r"integrated\s+(?:annual\s+)?report", r"integrated\s+annual",
    )),
    ("esg_report", (
        r"\bbrsr\b", r"business\s+responsibilit\w*", r"sustainability\s+report",
        r"sustainability\s+and\s+business", r"\besg\b", r"climate\s+report",
    )),
    ("annual_report", (
        r"annual\s*report", r"annualreport", r"\bar\s*fy\s*\d{2}\b",
    )),
    ("concall_transcript", (
        r"transcript", r"con\s?call\s+(?:recording\s+)?text", r"earnings\s+call\s+text",
    )),
    ("concall_ppt", (
        r"con\s?call[^a-z]{0,12}(?:ppt|presentation|deck)",
        r"(?:earnings|results|quarterly|analyst)\s+(?:call\s+)?(?:ppt|presentation|deck)",
        r"\bq[1-4]\s*(?:fy)?\s*\d{2,4}[^a-z]{0,10}(?:ppt|presentation|deck)",
        r"\bppt\b",
    )),
    ("investor_presentation", (
        r"investor\s+(?:presentation|deck|update|meet|day)", r"corporate\s+presentation",
        r"company\s+presentation", r"analyst\s+meet", r"business\s+update\s+presentation",
    )),
    ("quarterly_results", (
        r"(?:un)?audited\s+(?:standalone\s+|consolidated\s+)?financial\s+results",
        r"financial\s+results\s+for\s+the\s+(?:quarter|half|year|period)",
        r"quarterly\s+results", r"results\s+for\s+the\s+quarter",
        r"\bq[1-4]\s*(?:fy)?\s*\d{2,4}[^a-z]{0,12}results",
        r"statement\s+of\s+(?:standalone\s+|consolidated\s+)?(?:un)?audited",
        r"integrated\s+filing\s+financial",
    )),
    ("announcement", (
        r"announcement", r"outcome\s+of\s+(?:the\s+)?board\s+meeting",
        r"board\s+meeting\s+(?:outcome|intimation|notice)", r"intimation",
        r"disclosure\s+under\s+regulation", r"\breg(?:ulation)?\s*30\b",
        r"press\s+release", r"newspaper\s+publication", r"\bagm\b", r"\begm\b",
        r"postal\s+ballot", r"shareholding\s+pattern", r"corporate\s+action",
        r"allotment", r"trading\s+window", r"\bdividend\b", r"notice\s+of",
    )),
)

# Body-text fallbacks, same precedence order, used only when title+url say nothing.
_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("drhp", (r"draft\s+red\s+herring\s+prospectus", r"this\s+is\s+an?\s+offer\s+document")),
    ("integrated_report", (r"integrated\s+report", r"six\s+capitals")),
    ("esg_report", (r"business\s+responsibility\s+and\s+sustainability\s+report", r"\bbrsr\b")),
    ("concall_transcript", (
        r"\bmoderator\b\s*:", r"ladies\s+and\s+gentlemen,?\s+(?:good|welcome)",
        r"question[\s\-]and[\s\-]answer\s+session", r"thank\s+you.{0,40}floor\s+is\s+now\s+open",
    )),
    ("annual_report", (
        r"(?:board|director)[’'s]{0,2}\s+report", r"independent\s+auditor[’']?s?\s+report",
        r"notice\s+of\s+the\s+annual\s+general\s+meeting",
    )),
    ("quarterly_results", (
        r"(?:un)?audited\s+financial\s+results\s+for\s+the\s+quarter",
        r"statement\s+of\s+(?:standalone|consolidated)\s+results",
    )),
    ("concall_ppt", (r"safe\s+harbou?r\s+statement", r"investor\s+presentation")),
)


def _hay(*parts: str) -> str:
    """Lowercased, punctuation-flattened haystack: 'AnnualReport_2025.pdf' -> 'annualreport 2025 pdf'."""
    raw = " ".join(str(p or "") for p in parts).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%&]+", " ", raw)).strip()


def _first_rule(hay: str, rules: Iterable[tuple[str, tuple[str, ...]]]) -> str:
    for kind, pats in rules:
        for pat in pats:
            if re.search(pat, hay):
                return kind
    return ""


def detect_type(title: str, url: str, text: str = "") -> str:
    """Classify a filing into one of :data:`DOC_TYPES`. PURE.

    Title and URL are treated as one haystack and are ALWAYS decisive when they
    say anything at all — a document titled "Investor Presentation" stays an
    investor presentation even if its body reads like a transcript. Only when
    they are silent does the first ~8k characters of ``text`` get a vote.
    Precedence inside each stage is the fixed ladder documented at
    ``_TITLE_RULES`` (drhp > integrated > esg > annual_report > transcript >
    concall ppt > investor presentation > quarterly results > announcement).

    >>> detect_type("Integrated Annual Report 2024-25", "")
    'integrated_report'
    >>> detect_type("", "https://x.com/AnnualReport_2025.pdf")
    'annual_report'
    """
    kind = _first_rule(_hay(title, url), _TITLE_RULES)
    if kind:
        return kind
    body = str(text or "")[:8000]
    if body.strip():
        kind = _first_rule(_hay(body), _TEXT_RULES)
        if kind:
            return kind
    return "unknown"


# =============================================================== clean_text ==
# A word split across a line break: 'inter-\nnational'. The continuation token is
# captured whole (hyphens included) so a compound broken AT its own hyphen —
# 'state-\nof-the-art' — can be told apart from a syllable break.
_HYPHEN_BREAK = re.compile(r"(?P<left>\w)[-‐‑][ \t]*\n[ \t]*(?P<right>\w[\w'’-]*)")

_PAGE_LINE = re.compile(
    r"^[\s\-–—|:]*(?:page\s*)?\d{1,4}\s*(?:of|/)\s*\d{1,4}[\s\-–—|]*$", re.I)
_PAGE_WORD = re.compile(r"^[\s\-–—|:]*page\s*\d{1,4}[\s\-–—|]*$", re.I)
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*$")


def _dehyphenate(m: re.Match) -> str:
    """Join a line-broken word; keep the hyphen when it is a real compound."""
    left, right = m.group("left"), m.group("right")
    if not (left.isalpha() and left.islower()):     # 1,000-\ncrore / Tata-\nMotors
        return f"{left}-{right}"
    if not right[0].islower():                      # -\nMotors
        return f"{left}-{right}"
    if "-" in right:                                # state-\nof-the-art
        return f"{left}-{right}"
    return f"{left}{right}"                         # inter-\nnational


def _furniture_key(line: str) -> str:
    """Repeat key for a header/footer: case- and page-number-insensitive."""
    return re.sub(r"\d+", "#", line.strip().lower())


def _is_furniture(line: str, counts: dict) -> bool:
    """A short, non-sentence line that repeats across the document is page furniture."""
    s = line.strip()
    if not s:
        return False
    if _PAGE_LINE.match(s) or _PAGE_WORD.match(s):
        return True
    if len(s) > _HEADER_MAX_CHARS or len(s.split()) > _HEADER_MAX_WORDS:
        return False
    if counts.get(_furniture_key(s), 0) < _HEADER_MIN_REPEATS:
        return False
    # never drop something that reads like a sentence — that is content
    return not _SENTENCE_END.search(s)


def clean_text(raw: str) -> str:
    """PDF text -> readable text, WITHOUT touching sentence content. PURE.

    Does exactly five things, in this order:

    1. NFKC-normalises and drops PDF artefacts (soft hyphen, zero-width joiners,
       BOM) — the same folding ``docanalysis.normalise`` applies before grounding.
    2. Normalises line endings and form feeds.
    3. De-hyphenates words broken over a line break. A compound broken at its own
       hyphen keeps it (``state-\\nof-the-art`` -> ``state-of-the-art``); a
       syllable break is closed up (``inter-\\nnational`` -> ``international``).
    4. Drops page furniture: page-number lines, and short non-sentence lines that
       repeat at least three times (running headers/footers).
    5. Collapses runs of spaces/tabs and of blank lines. Newlines survive — the
       section and chunk heuristics read line structure.

    No word is ever reordered, rewritten or truncated, so a quote taken from the
    output is still verbatim company language.
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).translate(_ZERO_WIDTH)
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    s = _HYPHEN_BREAK.sub(_dehyphenate, s)

    lines = s.split("\n")
    counts: dict = {}
    for ln in lines:
        t = ln.strip()
        if t and len(t) <= _HEADER_MAX_CHARS:
            counts[_furniture_key(t)] = counts.get(_furniture_key(t), 0) + 1

    kept = []
    for ln in lines:
        if _is_furniture(ln, counts):
            continue
        kept.append(re.sub(r"[ \t ]+", " ", ln).strip())

    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ============================================================ split_sections ==
@dataclass(frozen=True)
class Section:
    """One titled span of a document. ``text == source[start:end]`` always holds."""
    title: str
    text: str
    start: int
    end: int

    def as_dict(self) -> dict:
        return {"title": self.title, "text": self.text,
                "start": self.start, "end": self.end}


# Canonical title -> heading patterns seen in Indian annual reports / DRHPs.
_SECTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat, re.I)) for name, pat in (
        ("MD&A",
         r"management(?:['’]s)?\s+discussion(?:\s+(?:and|&)\s+analysis)?"
         r"|\bmd\s*&\s*a\b|\bmda\b|management\s+discussion\s+report"),
        ("Directors' Report",
         r"(?:report\s+of\s+the\s+)?(?:board\s+of\s+)?directors['’]?s?\s+report"
         r"|board['’]?s\s+report|report\s+of\s+the\s+board\s+of\s+directors"),
        ("Risk Factors",
         r"risk\s+factors?|risks?\s+and\s+concerns|key\s+risks?"
         r"|risk\s+management(?:\s+(?:report|framework))?"),
        ("Auditor's Report",
         r"(?:independent\s+)?auditors?['’]?s?\s+report"
         r"|report\s+of\s+the\s+(?:statutory\s+)?auditors?"
         r"|auditors?['’]?s?\s+(?:qualifications?|remarks?|observations?)"),
        ("Related Party",
         r"related\s+part(?:y|ies)(?:\s+(?:transactions?|disclosures?))?"),
        ("Corporate Governance",
         r"(?:report\s+on\s+)?corporate\s+governance(?:\s+report)?"),
        ("ESG/BRSR",
         r"business\s+responsibilit\w*(?:\s+(?:and|&)\s+sustainability)?(?:\s+report)?"
         r"|\bbrsr\b|sustainability\s+report|\besg\b(?:\s+report)?"
         r"|environmental[,\s]+social(?:\s+and\s+governance)?"),
        ("Notes to Accounts",
         r"notes?\s+(?:to|forming\s+part\s+of)\s+(?:the\s+)?"
         r"(?:accounts?|financial\s+statements?|standalone|consolidated)"
         r"|significant\s+accounting\s+policies"),
    )
)

_LEAD_LABEL = re.compile(
    r"^\s*(?:annexure|annex|schedule|part|chapter|section)\s+[ivxlcdm0-9]+\s*[\-–—:.]?\s*",
    re.I)
_LEAD_NUM = re.compile(
    r"^\s*(?:\(?\d+(?:\.\d+)*\)?|\([a-z]\)|[IVXLCDM]{1,6}\.)\s*[.)\-–—:]?\s+")
_TRAIL_JUNK = re.compile(r"[\s.…\-–—_|]*\d*\s*$")


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def _heading_of(line: str) -> str:
    """Canonical section title for a heading line, or "" when it is prose."""
    s = line.strip()
    if not s or len(s) > _HEADING_MAX_CHARS:
        return ""
    core = _TRAIL_JUNK.sub("", _LEAD_NUM.sub("", _LEAD_LABEL.sub("", s)))
    body = _alnum(core)
    if not body:
        return ""
    for name, pat in _SECTION_PATTERNS:
        m = pat.search(core)
        # the heading must BE the line, not a mention inside a sentence
        if m and len(_alnum(m.group(0))) >= _HEADING_COVER * len(body):
            return name
    return ""


def split_sections(text: str) -> list[Section]:
    """Split a filing at its section headings. PURE.

    Recognises the eight sections that carry signal in Indian filings — MD&A,
    Directors' Report, Risk Factors, Auditor's Report, Related Party, Corporate
    Governance, ESG/BRSR and Notes to Accounts — using line-level heuristics: a
    line is a heading only when it is short, is (after stripping "5." /
    "ANNEXURE III -" style prefixes and trailing page numbers) essentially just
    the heading, and matches a canonical pattern.

    Text before the first heading is returned as an untitled preamble section,
    and adjacent sections with the same title are merged. A document with no
    recognised heading comes back as one untitled section, so callers never
    special-case the empty result.
    """
    src = text or ""
    if not src.strip():
        return []

    marks: list[tuple[int, str]] = []
    pos = 0
    for line in src.split("\n"):
        name = _heading_of(line)
        if name:
            marks.append((pos, name))
        pos += len(line) + 1

    if not marks:
        return [Section(title="", text=src, start=0, end=len(src))]

    # merge repeats of the same heading that follow one another
    merged: list[tuple[int, str]] = []
    for start, name in marks:
        if merged and merged[-1][1] == name:
            continue
        merged.append((start, name))

    out: list[Section] = []
    if merged[0][0] > 0:
        out.append(Section(title="", text=src[:merged[0][0]], start=0, end=merged[0][0]))
    for i, (start, name) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(src)
        out.append(Section(title=name, text=src[start:end], start=start, end=end))
    return out


# ==================================================================== chunk ==
@dataclass(frozen=True)
class Chunk:
    """One embeddable window. ``text == source[char_start:char_end]`` always holds."""
    text: str
    idx: int
    section: str
    char_start: int
    char_end: int

    def as_dict(self) -> dict:
        return {"text": self.text, "idx": self.idx, "section": self.section,
                "char_start": self.char_start, "char_end": self.char_end}


# tokens that end in "." without ending a sentence
_ABBREV = frozenset("""
rs rs. inr no nos ltd pvt pte co inc corp llp mr mrs ms dr prof shri smt sh jr sr
vs viz etc eg ie approx est fig vol pg pp cap dept mfg hon st sec reg regn yr qtr
jan feb mar apr jun jul aug sep sept oct nov dec fy ay u p q
""".split())

_SENT_TERM = re.compile(r"([.!?]+[\"'”’)\]]*)(\s+)")
_PARA_BREAK = re.compile(r"\n[ \t]*\n\s*")


def _is_sentence_end(text: str, m: re.Match) -> bool:
    if not m.group(1).startswith("."):
        return True                                   # ! and ? always end one
    i = m.start(1)
    j = i
    while j > 0 and (text[j - 1].isalnum() or text[j - 1] in "&"):
        j -= 1
    word = text[j:i]
    if len(word) == 1 and word.isalpha():             # initials: "A. K. Sharma"
        return False
    if word.lower() in _ABBREV:                       # "Rs. 4,521 crore"
        return False
    nxt = text[m.end():m.end() + 1]
    if not nxt:
        return True
    return bool(nxt.isupper() or nxt.isdigit() or nxt in "\"'“‘(₹\n")


def sentences(text: str) -> list[tuple[int, int]]:
    """Contiguous (start, end) sentence spans covering ``text`` exactly. PURE.

    Abbreviation-aware ("Rs. 4,521 crore" is one sentence), decimal-safe and
    paragraph-aware. The spans tile the input with no gaps, so any slice taken
    from them is verbatim source text.
    """
    if not text:
        return []
    n = len(text)
    cuts = {m.end() for m in _SENT_TERM.finditer(text) if _is_sentence_end(text, m)}
    cuts |= {m.end() for m in _PARA_BREAK.finditer(text)}
    spans, prev = [], 0
    for c in sorted(c for c in cuts if 0 < c < n):
        if c > prev:
            spans.append((prev, c))
            prev = c
    if n > prev:
        spans.append((prev, n))
    return spans


def _split_long(text: str, s: int, e: int, target: int) -> list[tuple[int, int]]:
    """Hard-split a single sentence longer than the chunk target, on whitespace."""
    out, pos = [], s
    while e - pos > target:
        cut = pos + target
        lo = pos + max(1, target * 4 // 5)
        w = text.rfind(" ", lo, cut)
        w = cut if w == -1 else w + 1
        out.append((pos, w))
        pos = w
    if e > pos:
        out.append((pos, e))
    return out


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def chunk(text: str, *, target_chars: int = 1800, overlap: int = 200,
          section: str = "") -> list[Chunk]:
    """Sentence-boundary-aware chunking with character-accurate provenance. PURE.

    Packs whole sentences up to ``target_chars`` and starts the next chunk far
    enough back to repeat at least ``overlap`` characters of context, so a fact
    straddling a boundary survives retrieval. A sentence longer than the target
    is hard-split on whitespace (and then cannot overlap). ``section`` is carried
    onto every chunk; ``char_start``/``char_end`` index the text passed in, so
    ``text[c.char_start:c.char_end] == c.text`` for every chunk.

    >>> [c.idx for c in chunk("A b. C d. E f.", target_chars=8, overlap=0)]
    [0, 1]
    """
    src = text or ""
    if not src.strip():
        return []
    target = max(1, int(target_chars or 1))
    ov = max(0, int(overlap or 0))
    if ov >= target:
        ov = target // 4

    units: list[tuple[int, int]] = []
    for s, e in sentences(src):
        if e - s <= target:
            units.append((s, e))
        else:
            units.extend(_split_long(src, s, e, target))
    if not units:
        return []

    out: list[Chunk] = []
    i, n, idx = 0, len(units), 0
    while i < n:
        start = units[i][0]
        end = units[i][1]
        j = i + 1
        while j < n and (units[j][1] - start) <= target:
            end = units[j][1]
            j += 1
        cs, ce = _trim(src, start, end)
        if ce > cs:
            out.append(Chunk(text=src[cs:ce], idx=idx, section=section,
                             char_start=cs, char_end=ce))
            idx += 1
        if j >= n:
            break
        k, back = j, 0
        while k - 1 > i and back < ov:                 # walk back whole sentences
            back += units[k - 1][1] - units[k - 1][0]
            k -= 1
        i = k if k > i else j                          # always makes progress
    return out


def chunk_document(text: str, *, target_chars: int = 1800,
                   overlap: int = 200) -> list[Chunk]:
    """Section-aware chunking of a whole filing: offsets stay absolute. PURE.

    Convenience wrapper — :func:`split_sections` then :func:`chunk` per section,
    with ``idx`` renumbered across the document and ``char_start``/``char_end``
    rebased onto the full text.
    """
    out: list[Chunk] = []
    for sec in split_sections(text):
        for c in chunk(sec.text, target_chars=target_chars, overlap=overlap,
                       section=sec.title):
            out.append(replace(c, idx=len(out),
                               char_start=c.char_start + sec.start,
                               char_end=c.char_end + sec.start))
    return out


# ======================================================= extract_financials ==
# One number, in Indian notation, with whatever unit followed it.
_NUM_RE = re.compile(
    r"(?<![\w.])(?P<open>\()?\s*(?P<sign>[-−])?\s*"
    r"(?P<cur>₹|₨|rs\.?|inr|\$|usd)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)",
    re.I)
_CLOSE_RE = re.compile(r"^\s*\)")
_PCT_RE = re.compile(r"^\s*(?:%|per\s?cent\w*|percentage\s+points?|pp\b)", re.I)
_BPS_RE = re.compile(r"^\s*(?:bps\b|basis\s+points?\b)", re.I)
_SCALE_RE = re.compile(
    r"^\s*(?:rupees\s+)?(crores?|crs?\.?|cr\.?|lakhs?|lacs?|millions?|mn\.?|mio|mm|"
    r"billions?|bn\.?|trillions?|tn\.?|thousands?|'000|k)\b\.?", re.I)
_PER_SHARE_RE = re.compile(r"^[\s/]*(?:per\s+(?:equity\s+)?share|/\s*share|ps\b)", re.I)


def _scale_factor(tok: str) -> Optional[float]:
    """Indian magnitude word -> multiplier that lands the value in crore."""
    t = tok.strip().lower().rstrip(".").rstrip("s")
    if t.startswith("crore") or t in ("cr", "crs"):
        return 1.0
    if t.startswith("lakh") or t.startswith("lac"):
        return 0.01
    if t.startswith("million") or t in ("mn", "mio", "mm"):
        return 0.1
    if t.startswith("billion") or t == "bn":
        return 100.0
    if t.startswith("trillion") or t == "tn":
        return 100000.0
    if t.startswith("thousand") or t in ("'000", "000", "k"):
        return 0.0001
    return None


@dataclass(frozen=True)
class _Num:
    """A number found in a sentence, already scaled, with how it was read."""
    start: int
    end: int
    value: float
    unit: str          # "cr" | "pct" | "rs"
    via: str           # "scale" | "pct" | "bps" | "plain"
    currency: bool
    per_share: bool


def _scan_numbers(sent: str) -> list[_Num]:
    """Every number in a sentence, with Indian grouping, brackets and units resolved."""
    out: list[_Num] = []
    for m in _NUM_RE.finditer(sent):
        try:
            val = float(m.group("num").replace(",", ""))
        except ValueError:  # pragma: no cover - regex guarantees a number
            continue
        end = m.end()
        neg = bool(m.group("sign"))
        # "(1,234) crore" — close the bracket before reading the unit
        if m.group("open"):
            mm = _CLOSE_RE.match(sent[end:end + 6])
            if mm:
                neg, end = True, end + mm.end()
        tail = sent[end:end + 28]
        unit, via = "rs", "plain"
        mm = _PCT_RE.match(tail)
        if mm:
            unit, via, end = "pct", "pct", end + mm.end()
        else:
            mm = _BPS_RE.match(tail)
            if mm:
                val, unit, via, end = val / 100.0, "pct", "bps", end + mm.end()
            else:
                mm = _SCALE_RE.match(tail)
                if mm:
                    f = _scale_factor(mm.group(1))
                    if f is not None:
                        val, unit, via, end = val * f, "cr", "scale", end + mm.end()
        rest = sent[end:end + 20]
        if m.group("open") and not neg and _CLOSE_RE.match(rest):   # "(1,234 crore)"
            neg = True
        out.append(_Num(start=m.start("num"), end=end,
                        value=-val if neg else val, unit=unit, via=via,
                        currency=bool(m.group("cur")),
                        per_share=bool(_PER_SHARE_RE.match(rest))))
    return out


def parse_amount(raw: str) -> Optional[tuple[float, str]]:
    """First number in ``raw`` -> ``(value, unit)`` with Indian formats resolved. PURE.

    Money is normalised to crore, percentages and bps to percentage points, a
    plain rupee figure stays in rupees. ``None`` when there is no number.

    >>> parse_amount("Rs. 1,23,456 lakh")
    (1234.56, 'cr')
    >>> parse_amount("(1,234) crore")
    (-1234.0, 'cr')
    >>> parse_amount("120 bps")
    (1.2, 'pct')
    """
    nums = _scan_numbers(str(raw or ""))
    if not nums:
        return None
    n = nums[0]
    return (round(n.value, 6), n.unit)


@dataclass(frozen=True)
class _Metric:
    key: str
    kind: str                  # "money" (-> cr) | "pct" | "rs" (per-share/mixed)
    label: re.Pattern


def _m(key: str, kind: str, pat: str) -> _Metric:
    return _Metric(key=key, kind=kind, label=re.compile(pat, re.I))


# Labels are ordered longest-first inside each alternation so "net debt" wins
# over "debt". Money metrics only accept a figure that carried a magnitude word
# (crore/lakh/mn/bn) — a bare "4,521" is too ambiguous to publish.
_METRIC_RULES: tuple[_Metric, ...] = (
    _m("revenue", "money",
       r"revenue\s+from\s+operations|total\s+(?:income|revenue)|net\s+sales|"
       r"\brevenues?\b|\bturnover\b|top[\s-]?line"),
    _m("ebitda", "money", r"\bebitda\b|operating\s+profit|\bebit\b"),
    _m("pat", "money",
       r"profit\s+after\s+tax|net\s+profit|net\s+loss|profit\s+for\s+the\s+"
       r"(?:year|quarter|period)|loss\s+for\s+the\s+(?:year|quarter|period)|"
       r"\bpat\b|bottom[\s-]?line"),
    _m("margin_pct", "pct",
       r"(?:ebitda|ebit|operating|gross|net|pbt|contribution)\s+margins?|"
       r"margins?\b|\bopm\b|\bnpm\b"),
    _m("eps", "rs", r"earnings\s+per\s+share|\beps\b"),
    _m("debt", "money",
       r"net\s+debt|gross\s+debt|total\s+debt|\bdebt\b|borrowings?"),
    _m("cash", "money",
       r"cash\s+and\s+(?:cash\s+)?equivalents|cash\s+and\s+bank|cash\s+balance|"
       r"net\s+cash|cash\s+on\s+(?:the\s+)?books?|treasury\s+(?:corpus|balance)"),
    _m("capex", "money", r"\bcapex\b|capital\s+expenditure|capital\s+outlay"),
    _m("roe", "pct", r"return\s+on\s+equity|\broe\b"),
    _m("roce", "pct", r"return\s+on\s+capital\s+employed|\broce\b|return\s+on\s+capital"),
    _m("working_capital", "money", r"working\s+capital"),
    _m("fcf", "money", r"free\s+cash\s*flows?|\bfcf\b"),
    _m("dividend", "rs", r"dividends?\b|payout\b"),
)

_GUIDANCE_RE = re.compile(
    r"\bguidance\b|\bwe\s+(?:expect|anticipate|aim|target|guide|are\s+guiding|"
    r"plan|intend|hope|remain\s+confident)\b|\bwe\s+are\s+targeting\b|"
    r"\bon\s+track\s+to\b|\bwe\s+should\s+(?:be\s+able\s+to|do|deliver|see)\b|"
    r"\boutlook\s+(?:for|remains|is)\b|\bexpect\s+to\s+(?:reach|achieve|grow|clock|"
    r"deliver|close)\b|\bgoing\s+forward\b|\bwe\s+have\s+guided\b", re.I)


def _window(text: str, s: int, e: int, focus: int, limit: int = _MAX_QUOTE) -> str:
    """Verbatim slice of ``text`` around ``focus``, capped at ``limit`` chars."""
    if e - s <= limit:
        return text[s:e].strip()
    half = limit // 2
    a = max(s, focus - half)
    b = min(e, a + limit)
    a = max(s, b - limit)
    while a > s and not text[a - 1].isspace():
        a -= 1
    while b < e and not text[b].isspace():
        b += 1
    return text[a:b].strip()


def _compatible(rule: _Metric, n: _Num) -> bool:
    if rule.kind == "money":
        return n.unit == "cr"
    if rule.kind == "pct":
        return n.unit == "pct"
    # "rs": per-share rupees, or an explicitly scaled/percentage payout
    if n.unit in ("cr", "pct"):
        return True
    return n.currency or n.per_share


def _best_number(rule: _Metric, lm: re.Match, nums: list[_Num]) -> Optional[tuple[int, _Num]]:
    """Nearest compatible number for one label hit -> (score, num); lower is better."""
    best: Optional[tuple[int, _Num]] = None
    for n in nums:
        if not _compatible(rule, n):
            continue
        if n.start >= lm.end():
            dist = n.start - lm.end()
            penalty = 0
        else:
            dist = lm.start() - n.end
            penalty = 25                    # "14.2% EBITDA margin" reads backwards
        if dist < 0 or dist > _LABEL_MAX_DIST:
            continue
        score = dist + penalty + (40 if n.via == "bps" else 0)
        if best is None or score < best[0]:
            best = (score, n)
    return best


def extract_financials(text: str) -> dict:
    """Pull headline financials out of filing text, deterministically. PURE.

    Regex + number parsing only — no model, no lookup table, same input always
    gives the same output. Understands Indian notation: ``Rs.``/``₹``/``INR``,
    lakh-grouped digits (``1,23,456``), ``cr``/``crore``/``lakh``/``mn``/``bn``
    magnitudes, bracketed negatives (``(1,234)`` -> ``-1234``), ``%``/``per
    cent`` and ``bps`` (converted to percentage points).

    Returns a sparse dict — only what was actually stated — keyed by
    ``revenue, ebitda, pat, margin_pct, eps, debt, cash, capex, roe, roce,
    working_capital, fcf, dividend``, each::

        {"value": float, "unit": "cr" | "pct" | "rs", "quote": "<verbatim sentence>"}

    Money is normalised to crore, percentages to percentage points, per-share
    figures stay in rupees. ``quote`` is a verbatim substring of ``text`` (the
    sentence the number was read from), so every figure can be re-grounded with
    ``docanalysis.is_grounded``. The one non-numeric key, ``guidance_text``, is a
    list of verbatim forward-looking sentences.

    A money metric is only reported when the figure carried a magnitude word;
    an unqualified "revenue of 4,521" is dropped rather than guessed at. When a
    metric is stated more than once the FIRST statement wins (headline numbers
    lead a filing), except that a bps movement only ever wins when no absolute
    figure was stated anywhere; within one sentence the number nearest its label
    is taken, preferring one that follows the label.
    """
    src = text or ""
    if not src.strip():
        return {}

    best: dict = {}          # key -> (bps_rank, abs_pos, payload)
    guidance: list[str] = []
    seen_guidance: set = set()

    for s, e in sentences(src):
        sent = src[s:e]
        if _GUIDANCE_RE.search(sent) and len(guidance) < _MAX_GUIDANCE:
            g = _window(src, s, e, s)
            key = re.sub(r"\s+", " ", g.lower())
            if len(g) >= 20 and key not in seen_guidance:
                seen_guidance.add(key)
                guidance.append(g)
        nums = _scan_numbers(sent)
        if not nums:
            continue
        for rule in _METRIC_RULES:
            for lm in rule.label.finditer(sent):
                hit = _best_number(rule, lm, nums)
                if hit is None:
                    continue
                n = hit[1]
                value = n.value
                if rule.key == "pat" and "loss" in lm.group(0).lower():
                    value = -abs(value)
                unit = "cr" if rule.kind == "money" else (
                    "pct" if rule.kind == "pct" else n.unit)
                rank = (1 if n.via == "bps" else 0, s + n.start)
                prev = best.get(rule.key)
                if prev is None or rank < prev[:2]:
                    best[rule.key] = rank + ({
                        "value": round(value, 6),
                        "unit": unit,
                        "quote": _window(src, s, e, s + n.start),
                    },)

    out = {r.key: best[r.key][2] for r in _METRIC_RULES if r.key in best}
    if guidance:
        out["guidance_text"] = guidance
    return out


# ============================================================ extract_tables ==
def _cell(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _open_pdf(src):
    """pdfplumber handle for a path, bytes or file-like. Caller closes it."""
    import pdfplumber                       # lazy: heavy, optional at bake time
    if isinstance(src, (bytes, bytearray, memoryview)):
        return pdfplumber.open(io.BytesIO(bytes(src)))
    if isinstance(src, (str, os.PathLike)):
        return pdfplumber.open(src)
    return pdfplumber.open(src)             # already a file-like object


def extract_tables(pdf_path_or_bytes, *, max_pages: int = 30,
                   max_tables: int = 60) -> list[dict]:
    """Tables out of a PDF via pdfplumber, as plain JSON-able dicts.

    The only IO in this module and the only place a heavy dependency is touched:
    ``pdfplumber`` is imported lazily and ANY failure (missing package, encrypted
    or scanned PDF, unreadable bytes) is logged and degrades to ``[]`` so a bake
    never dies on one bad filing.

    Each row is ``{"page", "index", "n_rows", "n_cols", "header", "rows"}`` with
    every cell collapsed to a single-spaced string and ``None`` cells as ``""``.
    """
    out: list[dict] = []
    if pdf_path_or_bytes is None:
        return out
    try:
        pdf = _open_pdf(pdf_path_or_bytes)
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_tables: cannot open pdf (%s)", exc)
        return out
    try:
        with pdf:
            for pno, page in enumerate(pdf.pages[:max(0, int(max_pages))], start=1):
                try:
                    tables = page.extract_tables() or []
                except Exception as exc:  # noqa: BLE001
                    log.warning("extract_tables: page %d unreadable (%s)", pno, exc)
                    continue
                for tno, raw in enumerate(tables):
                    rows = [[_cell(c) for c in (r or [])] for r in (raw or [])]
                    rows = [r for r in rows if any(r)]
                    if not rows:
                        continue
                    out.append({
                        "page": pno,
                        "index": tno,
                        "n_rows": len(rows),
                        "n_cols": max(len(r) for r in rows),
                        "header": rows[0],
                        "rows": rows,
                    })
                    if len(out) >= max_tables:
                        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_tables failed: %s", exc)
    return out
