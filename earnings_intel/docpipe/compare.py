"""
Historical Comparison Agent (pipeline agent 9) — DETERMINISTIC period-over-period
diffs over structures that were already extracted upstream.

No LLM, no network, no global state: every function here is pure, so the same
inputs always produce the same JSON. That is what lets the same code run inside
the static-site bake (GitHub Actions -> plain JSON artefacts) and, unchanged,
behind a FastAPI/Celery worker later. Swapping model providers never touches
this module — it only ever sees dicts.

Inputs are the shapes the rest of the pipeline already emits:
  * financials  -> {"revenue": 1200, "ebitda_margin": 14.0, ...} (numbers, or
                   {"value": 1200, "unit": "cr"}, or strings like "1,200 cr")
  * facts       -> docanalysis FACT objects
                   {"claim", "quote", "doc_kind", "doc_date", "url"}

Usage:
    from earnings_intel.docpipe import compare

    fin = compare.diff_financials({"revenue": 1200.0, "net_debt": 4000.0},
                                  {"revenue": 1000.0, "net_debt": 5000.0})
    fin["metrics"]["revenue"]["pct_change"]      # -> 20.0
    fin["metrics"]["net_debt"]["direction"]      # -> "improved" (lower is better)

    gui = compare.diff_guidance(curr_facts, prev_facts)
    gui["changed"][0]["similarity"]              # -> 0.94  (difflib, no model)

    compare.summarise_changes({"financials": fin, "guidance": gui})
    # -> ["Revenue improved 20.0% (1,000 -> 1,200).",
    #     "EBITDA margin guidance moved from 12-14% to 14-16%.", ...]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

log = logging.getLogger("technofunda.docpipe.compare")

__all__ = [
    "MetricDiff",
    "compare_metric",
    "diff_financials",
    "diff_guidance",
    "diff_risks",
    "summarise_changes",
    "DEFAULT_MATERIAL_PCT",
    "DEFAULT_SAME_RATIO",
    "DEFAULT_CHANGE_RATIO",
]

# reuse the filing-text normaliser (typography/whitespace) instead of copying it
try:  # pragma: no cover - exercised implicitly by every call
    from ..data.docanalysis import normalise as normalise_text
except Exception:  # noqa: BLE001 - docpipe must stay importable standalone
    def normalise_text(text: Any) -> str:  # type: ignore[misc]
        """Fallback: collapse whitespace only (docanalysis unavailable)."""
        return re.sub(r"\s+", " ", str(text or "")).strip()

DEFAULT_MATERIAL_PCT = 10.0     # |pct change| at/above this is "material"
DEFAULT_SAME_RATIO = 0.97       # claim similarity at/above this = unchanged
DEFAULT_CHANGE_RATIO = 0.60     # ... at/above this = the same statement, reworded

# metric-name hints where a FALL is the good outcome
_LOWER_IS_BETTER = (
    "debt", "cost", "expense", "opex", "capex_overrun", "npa", "gnpa", "nnpa",
    "attrition", "churn", "leverage", "provision", "interest_out", "payable_days",
    "receivable_days", "inventory_days", "working_capital_days", "dso", "dio",
    "emission", "wastage", "shrinkage", "loss",
)

_ACRONYMS = {"ebitda", "ebit", "pat", "pbt", "eps", "roe", "roce", "roa", "roic",
             "nim", "npa", "gnpa", "nnpa", "aum", "fcf", "ocf", "yoy", "qoq",
             "ev", "pe", "pbv", "sga", "r&d", "arpu", "casa", "nii"}

# trimmed off the edges of a subject phrase so a bullet reads like English;
# used ONLY by `_subject`, never for matching
_STOP = {"a", "an", "the", "of", "to", "for", "in", "on", "at", "by", "and", "or",
         "is", "was", "are", "were", "be", "with", "from", "as", "that", "this",
         "its", "it", "we", "our", "they", "their", "has", "have", "had", "will",
         "would", "about", "over", "around", "up", "down", "per", "than", "into",
         "management", "company", "guided", "guiding", "expects", "expected",
         "flagged", "said", "stated", "reported", "noted", "highlighted", "sees",
         "added", "adds", "rose", "fell", "reached", "remains", "remain",
         "stayed", "stands", "stood", "grew", "increased", "decreased",
         "improved", "declined", "now", "still", "also", "approximately",
         "roughly", "versus", "vs", "including", "includes", "being"}

# numbers as an analyst writes them: "12-14%", "Rs 18,000 crore", "45,000 units"
_NUM_SPAN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?:Rs\.?|INR|₹)\s*)?"
    r"\d[\d,]*(?:\.\d+)?"
    r"(?:\s*-\s*\d[\d,]*(?:\.\d+)?)?"
    r"(?:\s*(?:%|bps|crore|cr|lakh|lakhs|billion|bn|million|mn|units|x))?",
    re.IGNORECASE,
)


# --------------------------------------------------------------- pure: numbers
def _num(value: Any) -> Optional[float]:
    """Best-effort number out of a metric cell. None when not a number.

    Accepts floats/ints, {"value": x} wrappers and strings carrying separators
    or units ("1,200", "14.0%", "Rs 18,000 crore"). Booleans are rejected —
    a flag is not a metric.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "val", "amount", "number"):
            if key in value:
                return _num(value.get(key))
        return None
    s = normalise_text(value)
    if not s:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def _fmt_num(value: Optional[float]) -> str:
    """Human number for a bullet: 1200.0 -> '1,200', 12.5 -> '12.5'."""
    if value is None:
        return "n/a"
    s = f"{value:,.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _label(metric: str) -> str:
    """'ebitda_margin' -> 'EBITDA margin'; 'net_debt' -> 'Net debt'."""
    parts = [p for p in re.split(r"[\s_]+", str(metric or "").strip()) if p]
    if not parts:
        return "Metric"
    out = []
    for p in parts:
        if p.lower() in _ACRONYMS:
            out.append(p.upper())
        elif p.isupper():
            out.append(p)
        else:
            out.append(p.lower())
    s = " ".join(out)
    return (s[0].upper() + s[1:]) if s[:1].islower() else s


def _lower_is_better(metric: str, overrides: Optional[Iterable[str]]) -> bool:
    key = re.sub(r"[^a-z0-9]+", "_", str(metric or "").lower())
    if overrides is not None:
        return key in {re.sub(r"[^a-z0-9]+", "_", str(o or "").lower())
                       for o in overrides}
    return any(h in key for h in _LOWER_IS_BETTER)


# ------------------------------------------------------------ pure: financials
@dataclass
class MetricDiff:
    """One metric, this period vs last — the atom of the financial diff."""
    metric: str
    old: float
    new: float
    change: float
    pct_change: Optional[float]          # None when the base is zero
    direction: str                       # improved | declined | flat
    material: bool
    lower_is_better: bool = False

    def as_dict(self) -> dict:
        return {"metric": self.metric, "old": self.old, "new": self.new,
                "change": self.change, "pct_change": self.pct_change,
                "direction": self.direction, "material": self.material,
                "lower_is_better": self.lower_is_better}


def _pct_change(old: float, new: float) -> Optional[float]:
    """Percent move off `old`, using |old| so negatives read naturally
    (-10 -> -5 is +50%). None when there is no base to compare against."""
    if old == 0:
        return 0.0 if new == 0 else None
    return round((new - old) / abs(old) * 100.0, 2)


def compare_metric(metric: str, old: Any, new: Any, *,
                   material_pct: float = DEFAULT_MATERIAL_PCT,
                   flat_pct: float = 0.0,
                   lower_is_better: Optional[Iterable[str]] = None
                   ) -> Optional[MetricDiff]:
    """One metric -> MetricDiff, or None when either side is not a number."""
    o, n = _num(old), _num(new)
    if o is None or n is None:
        return None
    change = round(n - o, 6)
    pct = _pct_change(o, n)
    lower = _lower_is_better(metric, lower_is_better)

    if change == 0 or (pct is not None and abs(pct) <= float(flat_pct)):
        direction = "flat"
    else:
        up = change > 0
        direction = "improved" if (up != lower) else "declined"

    material = True if pct is None else abs(pct) >= float(material_pct)
    if direction == "flat":
        material = False
    return MetricDiff(metric=str(metric), old=o, new=n, change=change,
                      pct_change=pct, direction=direction, material=material,
                      lower_is_better=lower)


def diff_financials(curr: dict, prev: dict, *,
                    material_pct: float = DEFAULT_MATERIAL_PCT,
                    flat_pct: float = 0.0,
                    lower_is_better: Optional[Iterable[str]] = None) -> dict:
    """Period-over-period financial diff. PURE, JSON-serialisable.

    For every metric present (and numeric) on BOTH sides you get old, new,
    absolute change, pct_change, a direction (improved | declined | flat) and a
    materiality flag (|pct| >= `material_pct`, default 10). Metrics whose fall is
    the good outcome (debt, costs, NPAs, working-capital days ...) are detected
    by name, or pinned explicitly with `lower_is_better=["net_debt", ...]`.

    Zero base: pct_change is None and the move counts as material (you cannot
    express "0 -> 15" as a percentage, but you must never hide it). Metrics
    present on only one side land in "added"/"removed"; cells that are not
    numbers at all (a text note) are ignored by both.
    """
    c = curr if isinstance(curr, dict) else {}
    p = prev if isinstance(prev, dict) else {}

    metrics: dict = {}
    added: dict = {}
    removed: dict = {}
    for name, value in c.items():
        if name in p and _num(p.get(name)) is not None and _num(value) is not None:
            d = compare_metric(name, p.get(name), value, material_pct=material_pct,
                               flat_pct=flat_pct, lower_is_better=lower_is_better)
            if d is not None:
                metrics[str(name)] = d.as_dict()
        elif _num(value) is not None:
            added[str(name)] = _num(value)
    for name, value in p.items():
        if str(name) not in metrics and str(name) not in added and _num(value) is not None:
            removed[str(name)] = _num(value)

    buckets: dict = {"improved": [], "declined": [], "flat": []}
    for name, d in metrics.items():
        buckets.setdefault(d["direction"], []).append(name)
    material = sorted(
        [n for n, d in metrics.items() if d["material"]],
        key=lambda n: (-_abs_pct(metrics[n]["pct_change"]), n),
    )
    return {"metrics": metrics, "material": material,
            "improved": buckets["improved"], "declined": buckets["declined"],
            "flat": buckets["flat"], "added": added, "removed": removed,
            "params": {"material_pct": float(material_pct),
                       "flat_pct": float(flat_pct)}}


def _abs_pct(pct: Optional[float]) -> float:
    return float("inf") if pct is None else abs(float(pct))


# ------------------------------------------------------------------ pure: facts
def _claim_key(claim: Any) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", normalise_text(claim).lower())
    return re.sub(r"\s+", " ", s).strip()


def _as_facts(items: Any) -> list[dict]:
    """Tolerant coercion: FACT dicts pass through, bare strings become claims."""
    out: list[dict] = []
    for f in items or []:
        if isinstance(f, dict):
            if str(f.get("claim") or "").strip():
                out.append(f)
        elif isinstance(f, str) and f.strip():
            out.append({"claim": f.strip(), "quote": "", "doc_kind": "",
                        "doc_date": "", "url": ""})
    return out


def _num_key(claim: Any) -> tuple:
    """The numbers a claim carries, order-insensitive. Two statements that read
    alike but quote different figures are NOT the same statement."""
    return tuple(sorted(s[2].replace(" ", "").lower() for s in _spans(normalise_text(claim))))


def _diff_facts(curr_facts: Any, prev_facts: Any, theme: str, *,
                same_ratio: float = DEFAULT_SAME_RATIO,
                change_ratio: float = DEFAULT_CHANGE_RATIO) -> dict:
    """Shared engine for guidance/risk diffs — difflib only, never a model."""
    curr = _as_facts(curr_facts)
    prev = _as_facts(prev_facts)
    ckeys = [_claim_key(f.get("claim")) for f in curr]
    pkeys = [_claim_key(f.get("claim")) for f in prev]

    # score every candidate pair, then assign best-first so the result does not
    # depend on the order facts happen to arrive in
    pairs: list[tuple] = []
    for i, ck in enumerate(ckeys):
        for j, pk in enumerate(pkeys):
            if not ck or not pk:
                continue
            ratio = 1.0 if ck == pk else SequenceMatcher(None, ck, pk).ratio()
            if ratio >= float(change_ratio):
                pairs.append((-round(ratio, 6), i, j, round(ratio, 3)))
    pairs.sort()

    used_c: set = set()
    used_p: set = set()
    changed: list = []
    unchanged: list = []
    for _, i, j, ratio in pairs:
        if i in used_c or j in used_p:
            continue
        used_c.add(i)
        used_p.add(j)
        entry = {"before": prev[j], "after": curr[i], "similarity": ratio}
        same = (ratio >= float(same_ratio)
                and _num_key(prev[j].get("claim")) == _num_key(curr[i].get("claim")))
        (unchanged if same else changed).append(entry)

    changed.sort(key=lambda e: -e["similarity"])
    added = [f for i, f in enumerate(curr) if i not in used_c]
    removed = [f for j, f in enumerate(prev) if j not in used_p]
    return {"theme": theme, "added": added, "removed": removed,
            "changed": changed, "unchanged": unchanged,
            "counts": {"added": len(added), "removed": len(removed),
                       "changed": len(changed), "unchanged": len(unchanged)},
            "params": {"same_ratio": float(same_ratio),
                       "change_ratio": float(change_ratio)}}


def diff_guidance(curr_facts: list, prev_facts: list, *,
                  same_ratio: float = DEFAULT_SAME_RATIO,
                  change_ratio: float = DEFAULT_CHANGE_RATIO) -> dict:
    """What management now guides vs what it guided last time. PURE.

    Facts are docanalysis FACT objects. Claims are matched on normalised text
    with difflib: >= `same_ratio` AND the same figures is a reiteration
    ("unchanged"), >= `change_ratio` is the same statement with different
    content ("changed", carrying before/after/similarity), anything left over is
    "added" or "removed". The figures check matters: "Rs 15,000 crore capex" and
    "Rs 18,000 crore capex" read 97% alike but are not the same guidance. Both
    sides of every pair keep their quote and URL so a caller can always cite the
    source document.
    """
    return _diff_facts(curr_facts, prev_facts, "guidance",
                       same_ratio=same_ratio, change_ratio=change_ratio)


def diff_risks(curr_facts: list, prev_facts: list, *,
               same_ratio: float = DEFAULT_SAME_RATIO,
               change_ratio: float = DEFAULT_CHANGE_RATIO) -> dict:
    """New / dropped / reworded risks — identical shape to `diff_guidance`."""
    return _diff_facts(curr_facts, prev_facts, "risks_headwinds",
                       same_ratio=same_ratio, change_ratio=change_ratio)


# ---------------------------------------------------- pure: plain-language text
def _spans(text: str) -> list[tuple]:
    """[(start, end, "12-14%"), ...] for the numeric spans in a claim."""
    out = []
    for m in _NUM_SPAN.finditer(text or ""):
        piece = re.sub(r"\s+", " ", m.group(0)).strip()
        if piece:
            out.append((m.start(), m.end(), piece))
    return out


def _clip(text: Any, limit: int = 160) -> str:
    s = normalise_text(text)
    return s if len(s) <= limit else s[: max(0, limit - 3)].rstrip() + "..."


def _words(fragment: str) -> list[str]:
    """Word list of a claim fragment, edge punctuation stripped."""
    cleaned = re.sub(r"[^\w%&/.\- ]+", " ", fragment or "")
    return [w for w in (x.strip(".,;:-") for x in cleaned.split()) if w]


def _trim(words: list[str]) -> list[str]:
    """Drop filler and reporting verbs off both ends so the phrase reads."""
    while words and words[-1].lower() in _STOP:
        words.pop()
    while words and words[0].lower() in _STOP:
        words.pop(0)
    return words


def _subject(claim: str, start: int, end: int, max_words: int = 4) -> str:
    """Name the thing a changed number belongs to: '...an EBITDA margin of
    12-14%' -> 'EBITDA margin'. Reads the words just BEFORE the figure, falling
    back to the words just after, and never reaches across another figure.
    Casing is the document's own."""
    head = claim[:start]
    prior = _spans(head)
    if prior:
        head = head[prior[-1][1]:]
    words = _trim(_trim(_words(head))[-max_words:])

    if not words:
        tail = claim[end:]
        nxt = _spans(tail)
        if nxt:
            tail = tail[: nxt[0][0]]
        words = _trim(_words(tail)[:max_words])

    if not words:
        return ""
    s = " ".join(words)
    return (s[0].upper() + s[1:]) if s[:1].islower() else s


def _moved_bullet(before: str, after: str, noun: str) -> Optional[str]:
    """'EBITDA margin guidance moved from 12-14% to 14-16%.' — or None when the
    change cannot be pinned to exactly one number on each side."""
    b_spans = _spans(before)
    a_spans = _spans(after)
    b_only = [s for s in b_spans if s[2] not in {x[2] for x in a_spans}]
    a_only = [s for s in a_spans if s[2] not in {x[2] for x in b_spans}]
    if len(b_only) != 1 or len(a_only) != 1:
        return None
    subject = _subject(after, a_only[0][0], a_only[0][1])
    if not subject:
        return None
    low = subject.lower()
    skip = ("guid" in low) if noun == "guidance" else ("risk" in low or "headwind" in low)
    head = subject if skip else f"{subject} {noun}"
    return f"{head} moved from {b_only[0][2]} to {a_only[0][2]}."


def _financial_bullets(fin: dict) -> list[str]:
    metrics = (fin or {}).get("metrics") or {}
    # a flat metric is not news, and a malformed row never becomes a sentence
    names = [n for n, d in metrics.items()
             if isinstance(d, dict)
             and d.get("direction") in ("improved", "declined")
             and isinstance(d.get("old"), (int, float))
             and isinstance(d.get("new"), (int, float))]
    names.sort(key=lambda n: (0 if metrics[n].get("material") else 1,
                              -_abs_pct(metrics[n].get("pct_change")), n))
    out = []
    for n in names:
        d = metrics[n]
        label, direction = _label(n), d.get("direction")
        old, new = _fmt_num(d.get("old")), _fmt_num(d.get("new"))
        pct = d.get("pct_change")
        if pct is None:
            out.append(f"{label} {direction} from {old} to {new} (no prior base).")
        else:
            out.append(f"{label} {direction} {abs(float(pct)):.1f}% ({old} -> {new}).")
    return out


def _fact_bullets(block: dict, theme: str) -> list[str]:
    noun = "risk" if str(theme or "").startswith("risk") else "guidance"
    out: list[str] = []
    for entry in (block or {}).get("changed") or []:
        before = normalise_text((entry.get("before") or {}).get("claim"))
        after = normalise_text((entry.get("after") or {}).get("claim"))
        bullet = _moved_bullet(before, after, noun)
        if bullet is None:
            bullet = (f"{noun.capitalize()} changed: \"{_clip(before, 90)}\" -> "
                      f"\"{_clip(after, 90)}\".")
        out.append(bullet)
    for f in (block or {}).get("added") or []:
        out.append(f"New {noun}: {_clip(f.get('claim'))}.")
    for f in (block or {}).get("removed") or []:
        out.append(f"{noun.capitalize()} dropped: {_clip(f.get('claim'))}.")
    return out


def _blocks(diffs: dict) -> tuple:
    """Accept the composite dict, or a single diff on its own."""
    d = diffs if isinstance(diffs, dict) else {}
    fin = d.get("financials") if isinstance(d.get("financials"), dict) else None
    facts: list = []
    for key in ("guidance", "risks", "risks_headwinds"):
        blk = d.get(key)
        if isinstance(blk, dict):
            facts.append((str(blk.get("theme") or key), blk))
    if fin is None and not facts:
        if "metrics" in d:
            fin = d
        elif any(k in d for k in ("added", "removed", "changed")):
            facts.append((str(d.get("theme") or "guidance"), d))
    return fin, facts


def summarise_changes(diffs: dict, *, limit: int = 20) -> list[str]:
    """Diffs -> plain-language bullets, TEMPLATE-BUILT (never a model call).

    Accepts the composite {"financials": ..., "guidance": ..., "risks": ...} or
    any single diff on its own. Financial moves come first (material ones ahead
    of the rest, largest move first), then guidance, then risks — and inside a
    theme: changed, added, dropped.

        >>> summarise_changes({"guidance": diff_guidance(curr, prev)})[0]
        'EBITDA margin guidance moved from 12-14% to 14-16%.'
    """
    fin, facts = _blocks(diffs)
    out: list[str] = []
    try:
        if fin:
            out.extend(_financial_bullets(fin))
        for theme, blk in facts:
            out.extend(_fact_bullets(blk, theme))
    except Exception as e:  # noqa: BLE001 - a summary must never break a bake
        log.warning("summarise_changes degraded: %s", e)
    n = int(limit or 0)
    return out[:n] if n > 0 else out
