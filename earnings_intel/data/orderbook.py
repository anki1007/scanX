"""
Order book (backlog) from a company's own quarterly disclosures. PURE parsing.

Companies state their outstanding order book in investor presentations, results
press releases and earnings-call transcripts ("order book stood at Rs 5,143
crore as on 30 June 2026"). That figure -- the unexecuted backlog -- is a
different number from the order WINS the Orders board lists, and no structured
feed carries it. So it is read out of the filings: the sentence that names the
order book, the amount next to it, and the quarter it refers to.

Conservative by design. An amount is taken only within a short window after an
order-book phrase, units are explicit (crore / cr / million / billion / lakh),
and a document's figure is the one it states most often, so a passing mention
("order inflow of Rs 900 crore") does not become the backlog.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

_PHRASE = re.compile(
    r"(?:unexecuted|outstanding|pending|current|total|consolidated|closing|healthy|robust|strong)?\s*"
    r"order[\s\-]*(?:book(?!ing)|backlog)(?:\s+position)?", re.I)
_AMOUNT = re.compile(
    r"(?:rs\.?|inr|₹|rupees)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(lakh\s+crores?|lac\s+crores?|lakh\s+cr\b|trillion|crores?|cr\.?|cr\b|"
    r"billion|bn|million|mn|lakhs?|lacs?)?", re.I)
_BARE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakh\s+crores?|trillion|crores?|cr\b|cr\.|billion|bn|million|mn)", re.I)
_NOT_BACKLOG = re.compile(r"\b(inflow|intake|received|win|wins|won|bagged|addition|added|executed|"
                          r"execution|revenues?|turnover|bid|pipeline|tender|L1|receipt|booked|bookings|sales?|"
                          r"increased|exports?|EBITDA|PAT|profit)\b", re.I)
# A forecast is not a backlog: "would be close to", "could reach ... by March".
# ("by end of this quarter" is a statement about the quarter, so it stays.)
_FORWARD = re.compile(r"\b(would|could|should|will|expect\w*|estimat\w*|target\w*|aim\w*|"
                      r"guid\w*|reach|projected|likely|hope|plan\w*|"
                      r"by (?:march|june|september|december|fy))\b", re.I)
_ASON = re.compile(r"as\s+(?:on|of|at)\s+(\d{1,2})(?:st|nd|rd|th)?[\s\-]*([A-Za-z]{3,9})[\s,\-']+(\d{2,4})", re.I)

_UNIT = {"lakh crore": 100000.0, "lakh crores": 100000.0, "lac crore": 100000.0,
         "lac crores": 100000.0, "lakh cr": 100000.0, "trillion": 100000.0, "crore": 1.0, "crores": 1.0, "cr": 1.0, "cr.": 1.0,
         "billion": 100.0, "bn": 100.0, "million": 0.1, "mn": 0.1,
         "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01}
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                    "sep", "oct", "nov", "dec"], 1)}


def _to_cr(num: str, unit: Optional[str]) -> Optional[float]:
    if not unit:
        return None                        # an amount with no unit is not trusted
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    u = " ".join(unit.lower().rstrip(".").split())
    v *= _UNIT.get(u, 0)
    return round(v, 2) if v > 0 else None


def mentions(text: str, window: int = 140) -> list:
    """[(value_cr, as_of date|None, quote)] for each order-book statement."""
    out = []
    flat = re.sub(r"\s+", " ", text or "")
    for m in _PHRASE.finditer(flat):
        if "order" not in m.group(0).lower():
            continue
        tail = flat[m.end(): m.end() + window]
        head = flat[max(0, m.start() - 40): m.start()]
        # a flow, or one slice of the book ("export order book of Rs 80 crore")
        if re.search(r"\b(inflow|intake|export|international|overseas)\s*$", head, re.I):
            continue
        a = _AMOUNT.search(tail) or _BARE.search(tail)
        if not a:
            continue
        # the figure has to sit next to the phrase; 90 characters on it is
        # usually a different sentence's number
        if a.start() > 90:
            continue
        # a dollar amount is not rupees ("more than $2 billion" is not 200 Cr)
        if re.search(r"(\$|usd|us\$)\s*$", tail[max(0, a.start() - 6):a.start()], re.I):
            continue
        # "order book" must be the subject of THIS amount: no flow word and no
        # forecast between the phrase and the figure, nor just before it.
        between = tail[:a.start()]
        # ...and no forecast right after it either ("with an order book of
        # around Rs 15,000 crore, we should be well-positioned").
        after = tail[a.end(): a.end() + 35]
        if (_NOT_BACKLOG.search(between) or _FORWARD.search(between)
                or _FORWARD.search(head[-25:]) or _FORWARD.search(after)):
            continue
        v = _to_cr(a.group(1), a.group(2))
        if v is None or v < 1:
            continue
        asof = None
        d = _ASON.search(flat[m.start(): m.end() + window + 60])
        if d:
            mon = _MON.get(d.group(2)[:3].lower())
            yr = int(d.group(3)); yr = yr + 2000 if yr < 100 else yr
            if mon:
                try:
                    asof = date(yr, mon, int(d.group(1)))
                except ValueError:
                    asof = None
        quote = flat[max(0, m.start() - 20): m.end() + a.end() + 30].strip()
        out.append((v, asof, quote[:240]))
    return out


def quarter_end_before(d: date) -> date:
    """The last quarter end on or before the month before `d` (filings lag)."""
    y, m = d.year, d.month
    q = ((m - 1) // 3) * 3          # month index of the quarter's first month - 1
    if q == 0:
        return date(y - 1, 12, 31)
    end_m = q
    last = {3: 31, 6: 30, 9: 30, 12: 31}[end_m]
    return date(y, end_m, last)


def document_figure(text: str, filed: date) -> Optional[dict]:
    """The order book a document states: {value_cr, as_of, quote} or None.

    The most frequent value wins (presentations repeat the headline figure);
    ties go to the largest, which is the total rather than a segment. The
    quarter is the stated "as on" date when there is one, else the quarter
    end before the filing date.
    """
    found = mentions(text)
    if not found:
        return None
    counts = Counter(v for v, _, _ in found)
    best = max(counts, key=lambda v: (counts[v], v))
    asofs = [a for v, a, _ in found if v == best and a]
    quote = next(q for v, _, q in found if v == best)
    # A stated date counts only if it is recent: "against the previous year's
    # order book as on 1 April 2025" in a 2026 filing is a comparison, not the
    # date of the figure. It is snapped to its quarter end (1 April -> 31 March).
    recent = [a for a in asofs if 0 <= (filed - a).days <= 200]
    as_of = (quarter_end_before(max(recent) + timedelta(days=1)) if recent
             else quarter_end_before(filed))
    return {"value_cr": best, "as_of": as_of.isoformat(), "quote": quote}


def quarterly_series(figures: Iterable[dict]) -> list:
    """One value per quarter end: the figure the quarter's documents agree on.

    `figures` carry value_cr, as_of, filed, kind, url, quote. A quarter usually
    has a press release, a presentation and a call transcript; the value most
    of them state wins. That beats any single document's quirks -- one Waaree
    presentation's headline was a single segment's backlog (~5,300 Cr) while
    its press release and call both said ~61,500 Cr. Ties go to the press
    figure (a segment's backlog is smaller than the whole), then the press
    release, the presentation, the transcript.
    """
    rank = {"press": 0, "presentation": 1, "transcript": 2}
    groups: dict = {}
    for f in figures:
        if f and f.get("value_cr"):
            groups.setdefault(f["as_of"], []).append(f)
    out = []
    for q in sorted(groups):
        docs = groups[q]
        votes = Counter(round(float(d["value_cr"])) for d in docs)

        def key(d):
            return (votes[round(float(d["value_cr"]))], float(d["value_cr"]),
                    -rank.get(d.get("kind"), 3), datetime.fromisoformat(d["filed"]).toordinal())
        out.append(max(docs, key=key))
    return drop_spikes(out)


def drop_spikes(series: list, factor: float = 3.0) -> list:
    """Remove a quarter that jumps away from BOTH its neighbours and back.

    A backlog moves with execution and wins; it does not go 10,000 -> 282 ->
    10,000 Cr in two quarters. That shape is one document's stray number.
    The latest quarter, having one neighbour, goes only when the two before
    it agree with each other and it is `factor` away from them.
    """
    v = [float(s["value_cr"]) for s in series]
    far = lambda a, b: a > b * factor or a * factor < b  # noqa: E731
    agree = lambda a, b: a <= b * 1.5 and b <= a * 1.5  # noqa: E731
    keep = []
    last = len(v) - 1
    for i, s in enumerate(series):
        interior = 0 < i < last
        if (interior and far(v[i], v[i - 1]) and far(v[i], v[i + 1])
                and not far(v[i - 1], v[i + 1])):
            continue
        if i == last and i >= 2 and far(v[i], v[i - 1]) and agree(v[i - 1], v[i - 2]):
            continue
        keep.append(s)
    return keep
