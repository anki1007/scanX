"""
Typed graph over the debate's evidence pack — deterministic, offline, no key.

``earnings_intel.data.debate.evidence_pack`` returns a FLAT list: 73 items for a
company like SUMICHEM, across 17 families. Flat is why the debate reads like two
monologues. Nothing in the list says that "Trades at a P/E of 46.1" and "sector
P/E 31.9" are the same argument seen twice, or that a management commitment to
expand margins sits directly opposite a reported margin that fell — so the model
has to notice, and it reliably notices the easiest thing instead of the sharpest.

This module adds the edges the list never had:

    compares_to   same topic, one side of it is a peer/sector benchmark
    contradicts   same topic, opposite direction -- the actual disputes
    supports      same topic, same direction, from a different family
    derives_from  a computed item and the inputs it was computed from

Everything here is pure and rule-based. No model runs, so the graph costs
nothing and can be checked against real bundles before a single token is spent —
which matters because this repo's LLM budget is a hard free-tier ceiling.

    from earnings_intel.data.evidence_graph import build_graph, clashes, subgraph

    g = build_graph(evidence_pack(bundle, ...))
    for c in clashes(g):          # what the two sides should actually argue over
        print(c["why"])
    ids = subgraph(g, ["E1"], hops=1)      # the prompt slice for one topic

Three uses, in the order they pay off:

1. ``clashes`` picks the dispute, so both sides are forced onto the same point
   instead of each answering whatever was weakest.
2. ``subgraph`` sends one topic's slice into a prompt instead of all 73 items,
   which is a large cut in input tokens per turn.
3. ``new_nodes`` powers convergence: when a round introduces no evidence that
   was not already cited, the debate is over and the remaining rounds are not
   worth paying for.

DELIBERATELY CONSERVATIVE about ``contradicts``. A false clash sends both agents
to argue a disagreement that does not exist, which is worse than missing one —
so an edge needs the same topic AND an opposed direction AND at least one item
the pack already considers heavyweight. Every edge carries a ``why`` naming the
rule that fired, so a bad edge can be found and killed rather than guessed at.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["build_graph", "clashes", "subgraph", "new_nodes", "topic_of",
           "metric_of", "direction_of"]

# Topic keywords, checked in order — first match wins, so put the specific ones
# first ("operating margin" must not be swallowed by "operating").
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("valuation", ("p/e", "pe ratio", "price to earnings", "p/b", "price to book",
                   "ev/ebitda", "valuation", "market cap", "intrinsic", "dcf",
                   "margin of safety", "overvalued", "undervalued")),
    ("margin", ("margin", "ebitda", "opm", "gross profit")),
    ("growth", ("growth", "cagr", "compounded", "revenue", "sales", "topline",
                "profit growth")),
    ("leverage", ("debt", "d/e", "leverage", "borrowing", "interest coverage",
                  "gearing")),
    ("liquidity", ("current ratio", "quick ratio", "working capital", "liquidity")),
    ("cash", ("cash flow", "ocf", "free cash", "fcf", "cash conversion",
              "operating cash")),
    ("returns", ("roce", "roe", "return on capital", "return on equity")),
    ("capex", ("capex", "cwip", "capital work", "expansion", "capacity")),
    ("ownership", ("promoter", "pledge", "holding", "fii", "dii", "shareholding")),
    ("orders", ("order book", "order inflow", "orders")),
    ("momentum", ("moving average", "rsi", "breakout", "52-week", "price cagr",
                  "technical", "momentum")),
    ("sector", ("sector", "tailwind", "headwind", "industry")),
)

# Direction words. A claim's SIGN is what makes two claims opposable.
# NOTE "growth" is absent on purpose: it names a metric, it does not describe a
# direction. With it here, "Sales growth" + value "-8%" read as POSITIVE, so
# every company with shrinking sales looked like it was growing.
_UP = ("expand", "expanded", "expansion", "rose", "rise", "rising", "grew", "grow",
       "improve", "improved", "improving", "increase", "increased",
       "higher", "up ", "strong", "robust", "positive", "beat", "record",
       "outperform", "healthy", "surplus", "debt free", "debt-free")
_DOWN = ("contract", "contracted", "fell", "fall", "falling", "decline", "declined",
         "declining", "drop", "dropped", "shrink", "shrank", "erode", "eroded",
         "weaken", "weakened", "lower", "down ", "negative", "miss", "missed",
         "poor", "weak", "loss", "deteriorat", "stress", "pledge", "overvalued")

# Metrics, finer than topic. Checked against real bundles this matters: DLF's
# pack put "P/E 47.5" and "P/B 2.74 vs sector 6.3" both under topic=valuation
# with opposite directions, and the graph called it a contradiction. P/E versus
# P/B is not a disagreement, it is two different measurements -- so a dispute
# now needs the SAME metric, not merely the same topic.
_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ev_ebitda", ("ev/ebitda", "ev to ebitda")),
    ("pe", ("p/e", "pe ratio", "price to earnings", "price-to-earnings")),
    ("pb", ("p/b", "price to book", "price-to-book", "book value")),
    ("dcf", ("discounted cash flow", "dcf", "intrinsic value", "margin of safety",
             "reverse dcf")),
    ("roce", ("roce", "return on capital")),
    ("roe", ("roe", "return on equity")),
    ("opm", ("opm", "operating margin", "ebitda margin", "operating profit margin")),
    ("gross_margin", ("gross margin", "gross profit margin")),
    ("debt_equity", ("d/e", "debt to equity", "debt-to-equity", "debt free", "debt-free")),
    ("current_ratio", ("current ratio",)),
    ("ocf", ("ocf", "operating cash flow", "cash conversion", "free cash")),
    ("sales_growth", ("sales growth", "revenue growth", "topline")),
    ("profit_growth", ("profit growth", "earnings growth", "pat growth")),
    ("promoter", ("promoter holding", "promoter stake", "pledge")),
)

# Two sources reporting the SAME metric with materially different numbers is a
# DATA problem, not an argument. DLF carries "P/E 47.5" (Screener, standalone)
# beside "P/E 28.25" (Upstox peer basis); sending two agents to argue which is
# real produces nothing. Surfaced as its own edge type instead, and kept out of
# the clash list.
_VALUE_CONFLICT = 0.10          # >10% apart on the same metric

# The SAME metric over DIFFERENT windows is a time series, not a disagreement.
# Across the full universe the conflict rule fired 124,557 times (~23 a company)
# and most of it was this: "sales growth over 10 years is 16%" beside "over 5
# years is 4%". Those sources agree completely -- they measure different periods,
# and the gap between them is itself an argument, so it earns its own edge.
_PERIOD_RE = re.compile(
    r"(?<![a-z])(ttm|q[1-4]|fy\s?\d{2,4}|\d{1,2}\s*(?:year|yr)s?|"
    r"(?:one|three|five|ten)\s*years?)", re.I)
# Tightened from 0.20 after EIEL: ROCE 17.04% (Upstox peer basis) against
# ROCE 20.9% (Screener) is 18.5% apart, and at the looser threshold the
# graph called that an argument rather than a sourcing difference.

_NEG_RE = re.compile(r"^\s*[-−]|\(\s*\d")          # -12% or (12)
_NUM_RE = re.compile(r"[-−]?\d+(?:[.,]\d+)?")
_BENCHMARK_FAMILIES = frozenset({"peer_ratio", "sector"})
_COMPUTED_FAMILIES = frozenset({"dcf", "signal", "technical"})
# Only a HUMAN forward statement counts here. "insight" and "signal" carry
# computed labels like "FUNDAMENTALS-LED", and treating those as promises
# produced the worst false clashes in the sample: "Trades at a P/E of 46.1"
# against "FUNDAMENTALS-LED - profit grew 10% vs price 5%" is not a
# contradiction, it is a ratio next to an unrelated computed verdict.
_FORWARD_FAMILIES = frozenset({"commitment", "filing"})
_HEAVY = 3          # FAMILY_WEIGHT's top tier


# Keywords are matched on a LEFT letter boundary, not as bare substrings.
# Found on real data: "pe ratio" matches inside "Sharpe ratio", so
# "The Sharpe ratio is 0.56" was filed as a P/E and then reported as a source
# conflict against the real P/E of 46.1. A right boundary is deliberately NOT
# required -- "margin"/"margins" and "order"/"orders" must both still match.
def _compile(groups):
    return tuple((name, tuple(re.compile(r"(?<![a-z])" + re.escape(k)) for k in keys))
                 for name, keys in groups)


_TOPIC_RE = _compile(_TOPICS)
_METRIC_RE = _compile(_METRICS)


def _text(item: Mapping) -> str:
    return f"{item.get('fact') or ''} {item.get('value') or ''} {item.get('quote') or ''}".lower()


def topic_of(item: Mapping) -> str:
    """The one topic an evidence item is about, or "". PURE.

    Topic is what makes two items comparable at all — without it every pair of
    items looks equally related and the graph is just noise.
    """
    blob = _text(item)
    for name, patterns in _TOPIC_RE:
        if any(rx.search(blob) for rx in patterns):
            return name
    return ""


def metric_of(item: Mapping) -> str:
    """The specific measurement an item reports, or "". PURE.

    Finer than topic_of: two items must agree on THIS before a disagreement
    between them means anything.
    """
    blob = _text(item)
    for name, patterns in _METRIC_RE:
        if any(rx.search(blob) for rx in patterns):
            return name
    return ""


def _magnitude(item: Mapping) -> float | None:
    """First number in the item's value field, sign-free. PURE, None when absent."""
    m = _NUM_RE.search(str(item.get("value") or ""))
    if not m:
        return None
    try:
        return abs(float(m.group().replace("−", "-").replace(",", "")))
    except ValueError:
        return None


def direction_of(item: Mapping) -> int:
    """+1 good/rising, -1 bad/falling, 0 unsigned. PURE.

    Reads the words first and the number second: "margin contracted to 12%" is
    negative even though 12 is positive, while a bare "-8%" is negative on sign
    alone. side_hint breaks a tie because the pack already took a view.
    """
    value = str(item.get("value") or "")
    # An explicit minus is decisive and comes FIRST: -8% is negative whatever
    # the surrounding words say. A positive number is not decisive the other
    # way, because "margin contracted to 12%" is a fall reported as 12.
    if _NEG_RE.search(value):
        return -1
    blob = _text(item)
    up = sum(1 for w in _UP if w in blob)
    down = sum(1 for w in _DOWN if w in blob)
    if up != down:
        return 1 if up > down else -1
    m = _NUM_RE.search(value)
    if m and not value.strip().startswith(("-", "−", "(")):
        hint = str(item.get("side_hint") or "")
        if hint == "bull":
            return 1
        if hint == "bear":
            return -1
    hint = str(item.get("side_hint") or "")
    return 1 if hint == "bull" else -1 if hint == "bear" else 0


def _weight(item: Mapping) -> int:
    try:
        return int(item.get("weight") or 1)
    except (TypeError, ValueError):
        return 1


def _period_of(item: Mapping) -> str:
    """The window an item measures over ("5 years", "TTM", "FY24"), or "". PURE."""
    m = _PERIOD_RE.search(str(item.get("label") or ""))
    return (m.group(1).lower().strip() if m else "")


def _conflicting(a: Mapping, b: Mapping) -> bool:
    """Same metric, SAME window, numbers more than _VALUE_CONFLICT apart. PURE.

    The window check is what separates "two sources disagree" (a data problem)
    from "the number moved between periods" (a trend, and often the argument).
    """
    pa, pb = a.get("period") or "", b.get("period") or ""
    if pa != pb:
        return False
    x, y = a.get("magnitude"), b.get("magnitude")
    if x is None or y is None:
        return False
    hi = max(abs(x), abs(y))
    return hi > 0 and abs(x - y) / hi > _VALUE_CONFLICT


def _trending(a: Mapping, b: Mapping) -> bool:
    """Same metric, DIFFERENT windows, materially different numbers. PURE."""
    pa, pb = a.get("period") or "", b.get("period") or ""
    if not pa or not pb or pa == pb:
        return False
    x, y = a.get("magnitude"), b.get("magnitude")
    if x is None or y is None:
        return False
    hi = max(abs(x), abs(y))
    return hi > 0 and abs(x - y) / hi > _VALUE_CONFLICT


def _edge(src: str, dst: str, kind: str, topic: str, why: str) -> dict:
    return {"src": src, "dst": dst, "type": kind, "topic": topic, "why": why}


def build_graph(items: Sequence[Mapping] | None, *, max_edges: int = 400) -> dict:
    """Evidence list -> {"nodes", "edges", "by_topic"}. PURE, never raises.

    Edges are undirected in meaning but stored src->dst with src the lower id,
    so a pair is never emitted twice and the output is stable for a given pack
    (which is what lets a baked debate be diffed against yesterday's).
    """
    nodes: list[dict] = []
    for it in (items or []):
        if not isinstance(it, Mapping) or not it.get("id"):
            continue
        nodes.append({
            "id": str(it["id"]),
            "family": str(it.get("family") or ""),
            "topic": topic_of(it),
            "metric": metric_of(it),
            "magnitude": _magnitude(it),
            "period": _period_of({"label": _text(it)}),
            "direction": direction_of(it),
            "weight": _weight(it),
            "side_hint": str(it.get("side_hint") or "neutral"),
        })
    index = {n["id"]: n for n in nodes}
    by_topic: dict[str, list[str]] = {}
    for n in nodes:
        if n["topic"]:
            by_topic.setdefault(n["topic"], []).append(n["id"])

    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def push(a: dict, b: dict, kind: str, why: str) -> None:
        lo, hi = sorted((a["id"], b["id"]), key=lambda s: (len(s), s))
        key = (lo, hi, kind)
        if key in seen or len(edges) >= max_edges:
            return
        seen.add(key)
        edges.append(_edge(lo, hi, kind, a["topic"], why))

    for topic, ids in by_topic.items():
        group = [index[i] for i in ids]
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                same_dir = a["direction"] == b["direction"] != 0
                opposed = a["direction"] * b["direction"] < 0
                a_bench = a["family"] in _BENCHMARK_FAMILIES
                b_bench = b["family"] in _BENCHMARK_FAMILIES

                same_metric = bool(a["metric"]) and a["metric"] == b["metric"]

                if a_bench != b_bench and same_metric:
                    push(a, b, "compares_to",
                         f"{a['metric']}: one item is a peer/sector benchmark for the other")

                # Same metric, materially different numbers = the SOURCES
                # disagree. Real, worth surfacing, but not something two agents
                # can argue to a conclusion -- so it never reaches clashes().
                if same_metric and _trending(a, b):
                    push(a, b, "trend",
                         f"{a['metric']}: {a['period']} reads {a['magnitude']:g} against "
                         f"{b['period']} at {b['magnitude']:g}")

                elif same_metric and _conflicting(a, b):
                    push(a, b, "source_conflict",
                         f"{a['metric']}: sources report {a['magnitude']:g} and "
                         f"{b['magnitude']:g} for the same measure")

                # The dispute rule. Needs the SAME metric (or a forward claim
                # against a reported outcome on the same topic), an opposed
                # direction, and real weight -- a false clash costs both agents a
                # whole round arguing a disagreement that does not exist.
                elif opposed and max(a["weight"], b["weight"]) >= _HEAVY:
                    forward = a["family"] in _FORWARD_FAMILIES or b["family"] in _FORWARD_FAMILIES
                    if same_metric:
                        push(a, b, "contradicts",
                             f"{a['metric']}: the two items point in opposite directions")
                    elif forward:
                        push(a, b, "contradicts",
                             f"{topic}: a stated commitment runs against a reported number")

                elif same_dir and a["family"] != b["family"] and not (a_bench or b_bench):
                    push(a, b, "supports",
                         f"{topic}: independent families agreeing in the same direction")

                # parenthesised deliberately: `x in S != y in S` is a CHAINED
                # comparison in Python -- (x in S) and (S != (y in S)) -- not the
                # exclusive-or this rule needs.
                if (a["family"] in _COMPUTED_FAMILIES) != (b["family"] in _COMPUTED_FAMILIES):
                    computed, base = (a, b) if a["family"] in _COMPUTED_FAMILIES else (b, a)
                    push(computed, base, "derives_from",
                         f"{topic}: {computed['family']} is computed from inputs like this one")

    return {"nodes": nodes, "edges": edges, "by_topic": by_topic}


def clashes(graph: Mapping | None, limit: int = 5) -> list[dict]:
    """The `contradicts` edges worth a round, heaviest first. PURE.

    This is what turns two monologues into a debate: the graph names the
    disputed point, so the next round is spent on it rather than on whichever
    sentence the model found easiest to answer.
    """
    g = graph if isinstance(graph, Mapping) else {}
    index = {n["id"]: n for n in (g.get("nodes") or []) if isinstance(n, Mapping)}
    out = []
    for e in (g.get("edges") or []):
        if not isinstance(e, Mapping) or e.get("type") != "contradicts":
            continue
        a, b = index.get(e.get("src")), index.get(e.get("dst"))
        if not a or not b:
            continue
        out.append({**e, "strength": a["weight"] + b["weight"]})
    out.sort(key=lambda c: (-c["strength"], c["src"]))
    return out[:max(0, int(limit or 0))]


def subgraph(graph: Mapping | None, seeds: Iterable[str] | None, *,
             hops: int = 1, limit: int = 40) -> list[str]:
    """Evidence ids within `hops` of any seed, seeds included. PURE.

    The prompt slice. Sending one topic's neighbourhood instead of all 73 items
    is the single biggest lever on tokens-per-turn, which on a free-tier quota
    is the difference between covering the universe and not.
    """
    g = graph if isinstance(graph, Mapping) else {}
    adj: dict[str, set[str]] = {}
    for e in (g.get("edges") or []):
        if not isinstance(e, Mapping):
            continue
        s, d = str(e.get("src") or ""), str(e.get("dst") or "")
        if s and d:
            adj.setdefault(s, set()).add(d)
            adj.setdefault(d, set()).add(s)
    # Only ids the graph actually holds. A seed that is not a node -- a stale id,
    # a typo, a caller passing yesterday's pack -- must never reach a prompt: the
    # debate's whole contract is that a cited id exists, and grounding strips
    # claims citing ids we never issued. Inventing one HERE would launder it past
    # that check.
    known = {str(n.get("id")) for n in (g.get("nodes") or [])
             if isinstance(n, Mapping) and n.get("id")}
    frontier = {str(s) for s in (seeds or []) if s and str(s) in known}
    keep: list[str] = []
    seen: set[str] = set()
    for _ in range(max(0, int(hops)) + 1):
        nxt: set[str] = set()
        for node in sorted(frontier):
            if node in seen:
                continue
            seen.add(node)
            keep.append(node)
            if len(keep) >= limit:
                return keep[:limit]
            nxt |= adj.get(node, set())
        frontier = nxt - seen
        if not frontier:
            break
    return keep[:limit]


def new_nodes(cited_before: Iterable[str] | None,
              cited_now: Iterable[str] | None) -> set[str]:
    """Evidence introduced this round that was never cited before. PURE.

    Convergence: an empty result means neither side found anything new to say,
    so the remaining rounds would restate the argument at full token price.
    """
    before = {str(x) for x in (cited_before or []) if x}
    now = {str(x) for x in (cited_now or []) if x}
    return now - before
