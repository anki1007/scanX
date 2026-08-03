"""Deterministic, evidence-backed SWOT for a fundamental bundle.

``build_swot`` turns ``docs/data/fundamental/<CODE>.json`` (plus the optional
sector tailwind row and the grounded filing facts) into four ranked lists of
SWOT points.  There is no LLM anywhere in here: the bake runs this for ~5,500
companies with no API key and no network, so every point has to be produced by
an explicit rule and has to name the exact datum it came from.

Contract
--------
* A rule fires **only** when its underlying datum exists.  Nothing is inferred
  from a missing number, and every emitted item carries non-empty ``evidence``.
* Each quadrant is de-duplicated (exact, single-shot-metric and near-identical
  wording), sorted by ``weight`` descending (3 = decisive, 1 = minor) and capped
  at :data:`MAX_PER_QUADRANT`.
* ``build_swot`` is a pure function - no IO, no network, no mutable globals.
  The same bundle always yields the same output.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

log = logging.getLogger("technofunda.swot")

# --------------------------------------------------------------------- config
MAX_PER_QUADRANT = 12
NEAR_DUPE_RATIO = 0.6

QUADRANTS = ("strengths", "weaknesses", "opportunities", "threats")

#: metrics that may legitimately emit more than one point inside one quadrant
#: (list-shaped sources: Screener pros/cons, signal reasons, filing facts).
REPEATABLE_METRICS = frozenset({
    "flagged_pro", "flagged_con", "signal_flag", "bias_check",
    "filing_guidance", "filing_capex", "filing_demand", "filing_orders",
    "filing_capital_allocation", "filing_margins", "filing_commitment",
    "filing_risk", "filing_governance_risk", "filing_concentration_risk",
})

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z0-9.]+%?")
_NUMTOKEN_RE = re.compile(r"^\d+(?:\.\d+)?%?$")
_PLEDGE_RE = re.compile(r"pledg\w*\s+(?:about\s+)?(\d+(?:\.\d+)?)\s*%", re.I)

_STOPWORDS = frozenset("""
a an the and or but of in on at to for with from by is are was were be been being
its it this that these those has have had not no as than then over under up down
""".split())

_GOVERNANCE_WORDS = ("auditor", "qualified opinion", "qualification", "litigation",
                     "penalt", "fraud", "related party", "governance", "default",
                     "insolven", "non-compliance", "noncompliance", "sebi", "show cause")
_CONCENTRATION_WORDS = ("concentration", "single customer", "few customers",
                        "top customers", "dependence on", "dependent on",
                        "one client", "single client", "key customer")
_STRONG_PRO_WORDS = ("debt free", "debt-free", "healthy dividend", "good profit growth",
                     "good return on equity", "healthy return on equity",
                     "improving", "reducing debt")
_STRONG_CON_WORDS = ("pledged", "interest coverage", "poor sales growth",
                     "capitalizing the interest", "promoter holding is low",
                     "promoter holding has decreased", "contingent liabilities",
                     "debtor days", "working capital days", "low return on equity")

_TREND_NOUNS = {
    "sales": "Sales", "revenue": "Revenue", "net profit": "Net profit", "eps": "EPS",
    "opm%": "Operating margin", "opm": "Operating margin",
    "operating profit": "Operating profit", "reserves": "Reserves",
    "operating cash flow": "Operating cash flow", "net cash flow": "Net cash flow",
    "roce": "Return on capital", "roe": "Return on equity",
}
_UNIT_WORDS = {"yrs": "years", "yr": "years", "qtrs": "quarters", "qtr": "quarters"}
_HOLDING_TRENDS = {
    "promoter holding": ("Promoter holding", "promoter_holding_trend"),
    "institutional holding": ("Institutional holding", "institutional_holding_trend"),
}

_PEER_UNITS = {"x": "x", "pct": "%", "%": "%"}
_PEER_LABELS = {"pe": "P/E", "pb": "P/B", "roa": "ROA", "roe": "ROE",
                "roce": "ROCE", "ev_ebitda": "EV/EBITDA"}


# ---------------------------------------------------------------- primitives
def to_float(value: Any) -> Optional[float]:
    """Parse an Indian-formatted Screener cell into a float.

    ``'Rs 26,877 Cr.'`` -> 26877.0, ``'23.5 %'`` -> 23.5, ``'1,23,456'`` ->
    123456.0, ``'-3%'`` -> -3.0, ``'(1,234)'`` -> -1234.0, ``'%'``/``''``/
    ``'-'`` -> ``None``.  Never raises.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return None if (math.isnan(out) or math.isinf(out)) else out
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("−", "-").replace("–", "-").replace(",", "")
    bracketed = text.startswith("(") and text.endswith(")")
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        out = float(match.group(0))
    except ValueError:  # pragma: no cover - regex already guarantees a number
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return -out if (bracketed and out > 0) else out


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _fmt(value: Optional[float], unit: str = "") -> str:
    """Compact human number: 26360 -> '26,360', 0.8234 -> '0.82'."""
    if value is None:
        return ""
    if abs(value) >= 100:
        text = f"{value:,.0f}"
    elif abs(value) >= 10:
        text = f"{value:,.1f}"
    else:
        text = f"{value:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{unit}"


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _shorten(text: str, limit: int = 210) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


def _sentence(text: Any) -> str:
    text = _shorten(text)
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _norm_label(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _row(stmt: Any, *labels: str) -> list:
    """Raw cells of the first matching row label (label match is fuzzy)."""
    rows = _dict(_dict(stmt).get("rows"))
    if not rows:
        return []
    index = {_norm_label(k): v for k, v in rows.items()}
    for want in labels:
        vals = index.get(_norm_label(want))
        if isinstance(vals, list) and vals:
            return vals
    return []


def _pairs(stmt: Any, *labels: str) -> list:
    """``[(value, period-label)]`` oldest -> newest, unparseable cells dropped."""
    raw = _row(stmt, *labels)
    heads = _list(_dict(stmt).get("headers"))
    out = []
    for i, cell in enumerate(raw):
        val = to_float(cell)
        if val is None:
            continue
        out.append((val, _clean(heads[i]) if i < len(heads) else ""))
    return out


def _latest(series: Sequence) -> tuple:
    return series[-1] if series else (None, "")


def _back(series: Sequence, steps: int) -> tuple:
    """Value ``steps`` periods before the newest one (clamped to the oldest)."""
    if not series:
        return (None, "")
    idx = max(0, len(series) - 1 - steps)
    return series[idx]


def _pct_cell(stmt: Any, *labels: str) -> Optional[float]:
    """Latest value of a percent-ish row ('91%' -> 91.0, 0.91 -> 91.0)."""
    raw = _row(stmt, *labels)
    for cell in reversed(raw):
        val = to_float(cell)
        if val is None:
            continue
        if "%" in str(cell):
            return val
        return val * 100.0 if abs(val) <= 5 else val
    return None


# ------------------------------------------------------------------- de-dupe
def _tokens(text: str) -> frozenset:
    out = set()
    for tok in _WORD_RE.findall(text.lower()):
        tok = tok.strip(".")
        if not tok or tok in _STOPWORDS or len(tok) < 2:
            continue
        if _NUMTOKEN_RE.match(tok):
            num = to_float(tok)
            tok = "%g" % num if num is not None else tok
        out.add(tok)
    return frozenset(out)


def _jaccard(left: frozenset, right: frozenset) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


# ------------------------------------------------------------------- carrier
@dataclass(frozen=True)
class Point:
    """One SWOT point plus the datum that produced it."""

    point: str
    evidence: str
    metric: str
    weight: int

    def as_dict(self) -> dict:
        return {"point": self.point, "evidence": self.evidence,
                "metric": self.metric, "weight": self.weight}


@dataclass
class _Bag:
    """Collector handed to every rule; local to one ``build_swot`` call."""

    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)
    opportunities: list = field(default_factory=list)
    threats: list = field(default_factory=list)

    def _add(self, bucket: list, point: Any, evidence: Any,
             metric: str, weight: int) -> None:
        text, proof = _sentence(point), _clean(evidence)
        if not text or not proof or not metric:
            return  # never emit a point without the datum behind it
        bucket.append(Point(text, proof, str(metric),
                            max(1, min(3, int(weight)))))

    def s(self, point: Any, evidence: Any, metric: str, weight: int) -> None:
        self._add(self.strengths, point, evidence, metric, weight)

    def w(self, point: Any, evidence: Any, metric: str, weight: int) -> None:
        self._add(self.weaknesses, point, evidence, metric, weight)

    def o(self, point: Any, evidence: Any, metric: str, weight: int) -> None:
        self._add(self.opportunities, point, evidence, metric, weight)

    def t(self, point: Any, evidence: Any, metric: str, weight: int) -> None:
        self._add(self.threats, point, evidence, metric, weight)


@dataclass(frozen=True)
class _Ctx:
    """Everything the rules read, pre-dug and shape-checked once."""

    fund: dict
    overview: dict
    growth: dict
    quarters: dict
    profit_loss: dict
    balance_sheet: dict
    cash_flow: dict
    ratios: dict
    shareholding: dict
    pros: list
    cons: list
    trends: dict
    cyclical: dict
    growth_insight: dict
    money_flow: dict
    dcf: dict
    health: dict
    peers: dict
    technical: dict
    risk: dict
    signal: dict
    blocks: dict
    bias_flags: list
    upstox: dict
    sector: dict
    filings: dict
    themes: dict
    commitments: list


def _context(bundle: Any, sector: Any, filings: Any) -> _Ctx:
    bundle = _dict(bundle)
    fund = _dict(bundle.get("fundamental"))
    analysis = _dict(fund.get("analysis"))
    health = _dict(analysis.get("health"))
    prices = _dict(bundle.get("prices"))
    signal = _dict(bundle.get("signal"))
    filings = _dict(filings)
    fan = _dict(filings.get("analysis"))
    return _Ctx(
        fund=fund,
        overview=_dict(fund.get("overview")),
        growth=_dict(fund.get("growth")),
        quarters=_dict(fund.get("quarters")),
        profit_loss=_dict(fund.get("profit_loss")),
        balance_sheet=_dict(fund.get("balance_sheet")),
        cash_flow=_dict(fund.get("cash_flow")),
        ratios=_dict(fund.get("ratios")),
        shareholding=_dict(fund.get("shareholding")),
        pros=[_clean(p) for p in _list(fund.get("pros")) if _clean(p)],
        cons=[_clean(c) for c in _list(fund.get("cons")) if _clean(c)],
        trends=_dict(analysis.get("trends")),
        cyclical=_dict(analysis.get("cyclical")),
        growth_insight=_dict(analysis.get("growth_insight")),
        money_flow=_dict(analysis.get("money_flow")),
        dcf=_dict(analysis.get("dcf")),
        health=health,
        peers=_dict(health.get("peers")) or _peers_from_upstox(bundle),
        technical=_dict(prices.get("technical")),
        risk=_dict(prices.get("risk")),
        signal=signal,
        blocks=_dict(signal.get("blocks")),
        bias_flags=_list(_dig(signal, "bias_check", "flags")),
        upstox=_dict(bundle.get("upstox_ratios")),
        sector=_dict(sector),
        filings=filings,
        themes=_dict(fan.get("themes")),
        commitments=_list(fan.get("management_commitments")),
    )


def _peers_from_upstox(bundle: dict) -> dict:
    """Fall back to the raw upstox ratio block when analysis.health.peers is absent."""
    ups = _dict(bundle.get("upstox_ratios"))
    out = {}
    for key in _PEER_LABELS:
        row = _dict(ups.get(key))
        if row.get("value") is not None and row.get("sector") is not None:
            out[key] = row
    return out


# =========================================================== rules: earnings
def _rule_returns(ctx: _Ctx, bag: _Bag) -> None:
    """Absolute ROCE / ROE from the Screener overview + the 10y ROE record."""
    raw_roce = ctx.overview.get("ROCE")
    roce = to_float(raw_roce)
    if roce is not None:
        proof = f"ROCE {_clean(raw_roce) or _fmt(roce, '%')}"
        if roce >= 20:
            bag.s("Capital efficiency is high - the business earns well above a "
                  "normal cost of capital.", proof, "roce", 3)
        elif roce >= 15:
            bag.s("Return on capital employed is comfortably healthy.", proof, "roce", 2)
        elif roce < 5:
            bag.w("Return on capital is very poor - capital employed is barely "
                  "earning anything.", proof, "roce", 3)
        elif roce < 8:
            bag.w("Return on capital is weak.", proof, "roce", 2)
        elif roce < 12:
            bag.w("Return on capital is only modest.", proof, "roce", 1)

    raw_roe = ctx.overview.get("ROE")
    roe = to_float(raw_roe)
    if roe is not None:
        proof = f"ROE {_clean(raw_roe) or _fmt(roe, '%')}"
        if roe >= 18:
            bag.s("Shareholder capital is compounding at a high rate.", proof, "roe", 3)
        elif roe >= 15:
            bag.s("Return on equity is healthy.", proof, "roe", 2)
        elif roe < 5:
            bag.w("Return on equity is very low - shareholder capital is barely "
                  "productive.", proof, "roe", 3)
        elif roe < 10:
            bag.w("Return on equity is weak.", proof, "roe", 2)
        elif roe < 13:
            bag.w("Return on equity is only modest.", proof, "roe", 1)

    hist = _dict(ctx.growth.get("Return on Equity"))
    if hist:
        ten, five, three = (to_float(hist.get("10 Years")),
                            to_float(hist.get("5 Years")),
                            to_float(hist.get("3 Years")))
        proof = "Return on equity " + ", ".join(
            f"{lbl} {_clean(hist.get(lbl))}" for lbl in ("10 Years", "5 Years", "3 Years")
            if to_float(hist.get(lbl)) is not None)
        # "stayed high" has to include the recent window, not just the old ones
        if (ten is not None and five is not None and ten >= 15 and five >= 15
                and (three is None or three >= 12)):
            bag.s("Return on equity has stayed high across a full decade, not just "
                  "one good year.", proof, "roe_history", 3)
        if None not in (ten, five, three) and three < five < ten and (ten - three) >= 3:
            bag.w("Return on equity has been sliding across the 10/5/3-year record.",
                  proof, "roe_history", 2)


def _rule_margins(ctx: _Ctx, bag: _Bag) -> None:
    """Yearly operating-margin direction and the latest bottom line."""
    opm = _pairs(ctx.profit_loss, "OPM %", "OPM", "Financing Margin %")
    if len(opm) >= 4:
        new, new_lbl = _latest(opm)
        old, old_lbl = _back(opm, 3)
        delta = new - old
        proof = (f"Operating margin {_fmt(new, '%')} ({new_lbl}) vs "
                 f"{_fmt(old, '%')} ({old_lbl})")
        if delta >= 2:
            bag.s("Operating margin has widened over the last few years.",
                  proof, "opm_trend", 2)
        elif delta <= -2:
            bag.w("Operating margin has compressed over the last few years.",
                  proof, "opm_trend", 2)

    npf = _pairs(ctx.profit_loss, "Net Profit")
    if npf:
        val, lbl = _latest(npf)
        if val < 0:
            bag.w("The company is loss-making at the net level.",
                  f"Net profit {_fmt(val)} Cr ({lbl})", "net_profit", 3)

    op = _pairs(ctx.profit_loss, "Operating Profit", "Financing Profit")
    interest = _pairs(ctx.profit_loss, "Interest")
    if op and interest:
        oper, lbl = _latest(op)
        cost, _ = _latest(interest)
        if cost > 0:
            cover = oper / cost
            proof = (f"Operating profit {_fmt(oper)} Cr vs interest {_fmt(cost)} Cr "
                     f"= {_fmt(cover, 'x')} cover ({lbl})")
            if cover < 1.5:
                bag.w("Operating profit barely covers the interest bill.",
                      proof, "interest_coverage", 3)
                bag.t("Interest cover is thin - a slow year could put debt service "
                      "at risk.", proof, "interest_coverage", 3)
            elif cover < 3:
                bag.w("Interest cover is uncomfortably low.", proof,
                      "interest_coverage", 2)
                bag.t("Thin interest cover leaves little headroom if earnings dip.",
                      proof, "interest_coverage", 2)
            elif cover >= 10:
                bag.s("Interest cost is trivial next to operating profit.",
                      proof, "interest_coverage", 2)


def _rule_growth(ctx: _Ctx, bag: _Bag) -> None:
    """Compounded sales / profit / price growth from the Screener growth tables."""
    _compounding(ctx, bag, "Compounded Sales Growth", "sales", "Sales", 0)
    _compounding(ctx, bag, "Compounded Profit Growth", "profit", "Profit", 1)

    price = _dict(ctx.growth.get("Stock Price CAGR"))
    one = to_float(price.get("1 Year"))
    if one is not None and one <= -20:
        bag.w("The stock has de-rated hard over the past year.",
              f"Stock price CAGR 1 Year {_clean(price.get('1 Year'))}",
              "price_cagr_1y", 1)


def _compounding(ctx: _Ctx, bag: _Bag, key: str, slug: str,
                 noun: str, bump: int) -> None:
    """One 'level' point and at most one 'direction' point per growth table."""
    table = _dict(ctx.growth.get(key))
    if not table:
        return
    ten, five = to_float(table.get("10 Years")), to_float(table.get("5 Years"))
    three, ttm = to_float(table.get("3 Years")), to_float(table.get("TTM"))
    low = noun.lower()

    if five is not None:
        proof = f"{key} 5 Years {_clean(table.get('5 Years'))}"
        if five >= 20:
            bag.s(f"{noun} compounding has been strong over five years.",
                  proof, f"{slug}_cagr_5y", min(3, 2 + bump))
        elif five >= 12:
            bag.s(f"{noun} has compounded at a decent five-year clip.",
                  proof, f"{slug}_cagr_5y", 2)
        elif five < 0:
            bag.w(f"Five-year {low} growth is negative.",
                  proof, f"{slug}_cagr_5y", min(3, 2 + bump))
        elif five < 5:
            bag.w(f"Five-year {low} growth is close to flat.",
                  proof, f"{slug}_cagr_5y", 2)

    if ten is not None and five is not None and ten >= 15 and five >= 15:
        bag.s(f"{noun} growth has held up across both the 10-year and 5-year "
              f"windows.",
              f"{key} 10 Years {_clean(table.get('10 Years'))}, "
              f"5 Years {_clean(table.get('5 Years'))}",
              f"{slug}_cagr_10y", 2)

    # direction: the ladder subsumes the single-window reads, so pick one only
    ladder = None
    if None not in (ten, five, three):
        ladder = (f"{key} 10Y {_clean(table.get('10 Years'))} -> "
                  f"5Y {_clean(table.get('5 Years'))} -> "
                  f"3Y {_clean(table.get('3 Years'))}")
        if three < five - 3 and five < ten - 3:
            bag.w(f"{noun} growth is decelerating stage by stage.",
                  ladder, f"{slug}_growth_decel", 2)
            return
        if three > five + 3 and five >= 0:
            bag.s(f"{noun} growth has been picking up versus the longer record.",
                  ladder, f"{slug}_growth_accel", 2)
            return

    if three is not None and three < 0:
        bag.w(f"Three-year {low} growth is negative.",
              f"{key} 3 Years {_clean(table.get('3 Years'))}",
              f"{slug}_cagr_3y", 2)
    elif ttm is not None and ttm < 0:
        bag.w(f"Trailing-twelve-month {low} growth is negative.",
              f"{key} TTM {_clean(table.get('TTM'))}", f"{slug}_growth_ttm", 2)


def _rule_trends(ctx: _Ctx, bag: _Bag) -> None:
    """analysis.trends - multi-period direction calls, yearly and quarterly."""
    for scope, unit_word in (("yearly", "years"), ("quarterly", "quarters")):
        book = _dict(ctx.trends.get(scope))
        for raw_label, spec in book.items():
            spec = _dict(spec)
            label, count = _clean(spec.get("label")), spec.get("n")
            steps = to_float(count)
            if label not in ("Increasing", "Decreasing") or steps is None or steps < 2:
                continue
            key = _clean(raw_label).lower()
            holding = _HOLDING_TRENDS.get(key)
            noun = _TREND_NOUNS.get(key) or (holding[0] if holding else None)
            if noun is None:
                continue
            unit = _UNIT_WORDS.get(_clean(spec.get("unit")), unit_word)
            proof = (f"{_clean(raw_label)} trend ({scope}): {label} for "
                     f"{_fmt(steps)} {unit}")
            metric = holding[1] if holding else f"trend_{scope}_{_slug(raw_label)}"
            weight = 2 if (steps >= 4 and scope == "yearly") else 1
            if label == "Increasing":
                bag.s(f"The {noun.lower()} trend has been rising for "
                      f"{_fmt(steps)} {unit} in a row.", proof, metric, weight)
                if holding:
                    bag.o(f"{noun} keeps climbing - the buying has not stopped.",
                          proof, f"{metric}_flow", 1)
            else:
                bag.w(f"The {noun.lower()} trend has been falling for "
                      f"{_fmt(steps)} {unit} in a row.", proof, metric, weight)
                if holding:
                    bag.t(f"{noun} keeps falling - the selling has not stopped.",
                          proof, f"{metric}_flow", 2)


# ======================================================= rules: balance sheet
def _rule_leverage(ctx: _Ctx, bag: _Bag) -> None:
    """Debt / equity from analysis.health, falling back to the balance sheet."""
    node = _dict(ctx.health.get("debt_equity"))
    de, proof = to_float(node.get("value")), ""
    if de is not None:
        proof = (f"Debt/Equity {_fmt(de, 'x')}"
                 + (f" ({_clean(node.get('year'))})" if node.get("year") else "")
                 + (" - balance-sheet proxy" if node.get("proxy") else ""))
    else:
        borrow = _pairs(ctx.balance_sheet, "Borrowings")
        equity = _pairs(ctx.balance_sheet, "Equity Capital")
        reserves = _pairs(ctx.balance_sheet, "Reserves")
        if borrow and equity and reserves:
            debt, lbl = _latest(borrow)
            worth = _latest(equity)[0] + _latest(reserves)[0]
            if worth > 0:
                de = debt / worth
                proof = (f"Borrowings {_fmt(debt)} Cr vs net worth {_fmt(worth)} Cr "
                         f"= {_fmt(de, 'x')} ({lbl})")
    if de is None or not proof:
        return
    if de <= 0.1:
        bag.s("The balance sheet is effectively debt-free.", proof, "debt_equity", 3)
    elif de <= 0.5:
        bag.s("Leverage is modest and easily serviced.", proof, "debt_equity", 2)
    elif de >= 2:
        bag.w("The company is heavily geared.", proof, "debt_equity", 3)
        bag.t("High leverage magnifies any earnings or rate shock.",
              proof, "leverage_risk", 3)
    elif de >= 1:
        bag.w("Debt is larger than equity.", proof, "debt_equity", 2)
        bag.t("Borrowings above equity leave limited room if the cycle turns.",
              proof, "leverage_risk", 2)
    elif de >= 0.7:
        bag.w("Leverage is on the higher side.", proof, "debt_equity", 1)


def _rule_liquidity(ctx: _Ctx, bag: _Bag) -> None:
    node = _dict(ctx.health.get("current_ratio")) or _dict(ctx.upstox.get("current_ratio"))
    ratio = to_float(node.get("value"))
    if ratio is None:
        return
    year = _clean(node.get("year") or node.get("period"))
    proof = f"Current ratio {_fmt(ratio, 'x')}" + (f" ({year})" if year else "")
    if ratio >= 2:
        bag.s("Short-term liquidity is comfortable.", proof, "current_ratio", 2)
    elif ratio >= 1.5:
        bag.s("Current assets cover current liabilities with room to spare.",
              proof, "current_ratio", 1)
    elif ratio < 1:
        bag.w("Current liabilities exceed current assets - short-term liquidity "
              "is tight.", proof, "current_ratio", 3)
    elif ratio < 1.2:
        bag.w("The current ratio leaves very little liquidity cushion.",
              proof, "current_ratio", 2)


def _rule_debt_direction(ctx: _Ctx, bag: _Bag) -> None:
    borrow = _pairs(ctx.balance_sheet, "Borrowings")
    if len(borrow) < 3:
        return
    new, new_lbl = _latest(borrow)
    old, old_lbl = _back(borrow, 2)
    if old <= 1:
        return
    proof = f"Borrowings {_fmt(old)} Cr ({old_lbl}) -> {_fmt(new)} Cr ({new_lbl})"
    if new <= old * 0.75:
        bag.o("Debt is being paid down - interest cost should keep falling.",
              proof, "deleveraging", 2)
    elif new >= old * 1.5 and new >= 5:
        bag.w("Borrowings have climbed sharply over the last few years.",
              proof, "debt_buildup", 2)
        bag.t("A fast-growing debt load raises the stakes on execution.",
              proof, "debt_buildup", 2)


def _rule_capex(ctx: _Ctx, bag: _Bag) -> None:
    """CWIP direction: drawdown = capacity commissioning, build-up = capex underway."""
    node = _dict(ctx.health.get("cwip"))
    latest, prev = to_float(node.get("latest")), to_float(node.get("prev"))
    change, year = to_float(node.get("pct_change")), _clean(node.get("year"))
    if latest is None or prev is None:
        series = _pairs(ctx.balance_sheet, "CWIP")
        if len(series) >= 2:
            latest, year = _latest(series)
            prev = _back(series, 1)[0]
            change = _pct_change(latest, prev)
    if latest is None or prev is None:
        return
    if change is None:
        change = _pct_change(latest, prev)
    if change is None:
        return
    proof = (f"CWIP {_fmt(prev)} Cr -> {_fmt(latest)} Cr ({_fmt(change, '%')})"
             + (f" as of {year}" if year else ""))
    if change <= -25 and prev > 0:
        bag.s("The capex cycle has been completed - work-in-progress has been "
              "capitalised.", proof, "cwip_drawdown", 2)
        bag.o("Commissioned capacity should start contributing to revenue.",
              proof, "cwip_commissioning", 2)
    elif change >= 25 and latest > 0:
        bag.o("A capex build-up is underway, pointing to future capacity.",
              proof, "cwip_buildup", 2)


# ============================================================== rules: cash
def _rule_cash(ctx: _Ctx, bag: _Bag) -> None:
    """Cash conversion: OCF/NP, absolute operating cash flow, free cash flow."""
    node = _dict(ctx.health.get("ocf_np"))
    ratio, year = to_float(node.get("value")), _clean(node.get("year"))
    if ratio is None:
        ocf = _pairs(ctx.cash_flow, "Cash from Operating Activity")
        npf = _pairs(ctx.profit_loss, "Net Profit")
        if ocf and npf and _latest(npf)[0] not in (None, 0):
            ratio, year = _latest(ocf)[0] / abs(_latest(npf)[0]), _latest(ocf)[1]
    if ratio is not None:
        proof = (f"Operating cash flow / net profit {_fmt(ratio, 'x')}"
                 + (f" ({year})" if year else ""))
        if ratio >= 1:
            bag.s("Reported profit is fully backed by operating cash.",
                  proof, "ocf_np", 3)
        elif ratio >= 0.8:
            bag.s("Most of the reported profit converts into operating cash.",
                  proof, "ocf_np", 1)
        elif ratio >= 0.5:
            bag.w("Only part of the reported profit turns into operating cash.",
                  proof, "ocf_np", 2)
        elif ratio >= 0:
            bag.w("Cash conversion is poor - very little profit reaches operating "
                  "cash flow.", proof, "ocf_np", 3)
        else:
            bag.w("Operating cash flow is negative while the company books a "
                  "profit.", proof, "ocf_np", 3)

    ocf = _pairs(ctx.cash_flow, "Cash from Operating Activity")
    if ocf:
        val, lbl = _latest(ocf)
        if val < 0:
            bag.t("The business consumed cash from operations in the latest year.",
                  f"Cash from operating activity {_fmt(val)} Cr ({lbl})",
                  "operating_cash_flow", 3)

    cfo_op = _pct_cell(ctx.cash_flow, "CFO/OP")
    if cfo_op is not None:
        proof = f"CFO/Operating profit {_fmt(cfo_op, '%')}"
        if cfo_op >= 90:
            bag.s("Operating profit is converting into cash almost one for one.",
                  proof, "cfo_op", 2)
        elif cfo_op < 50:
            bag.w("Operating profit converts poorly into cash.", proof, "cfo_op", 2)

    fcf = _pairs(ctx.cash_flow, "Free Cash Flow")
    if len(fcf) >= 2:
        new, new_lbl = _latest(fcf)
        old, old_lbl = _back(fcf, 1)
        proof = f"Free cash flow {_fmt(old)} Cr ({old_lbl}) -> {_fmt(new)} Cr ({new_lbl})"
        if new > 0 and new > old:
            bag.o("Free cash flow is positive and improving, funding growth without "
                  "fresh debt.", proof, "free_cash_flow", 1)
        elif new < 0:
            bag.w("Free cash flow is negative.", proof, "free_cash_flow", 1)


def _rule_earnings_quality(ctx: _Ctx, bag: _Bag) -> None:
    """PAT rising while operating cash flow is not - the classic accrual gap."""
    npf = _pairs(ctx.profit_loss, "Net Profit")
    ocf = _pairs(ctx.cash_flow, "Cash from Operating Activity")
    if len(npf) < 2 or len(ocf) < 2:
        return
    np_new, np_lbl = _latest(npf)
    np_old = _back(npf, 1)[0]
    cf_new, cf_lbl = _latest(ocf)
    cf_old, cf_old_lbl = _back(ocf, 1)
    growth = _pct_change(np_new, np_old)
    if growth is None or growth <= 10 or cf_new >= cf_old:
        return
    bag.t("Profit is growing while operating cash flow is shrinking - an "
          "earnings-quality gap.",
          f"Net profit {_fmt(np_old)} -> {_fmt(np_new)} Cr ({np_lbl}) but operating "
          f"cash flow {_fmt(cf_old)} Cr ({cf_old_lbl}) -> {_fmt(cf_new)} Cr ({cf_lbl})",
          "earnings_quality", 3)


def _rule_operating_leverage(ctx: _Ctx, bag: _Bag) -> None:
    sales = _pairs(ctx.profit_loss, "Sales", "Revenue")
    costs = _pairs(ctx.profit_loss, "Expenses")
    if len(sales) < 2 or len(costs) < 2:
        return
    s_new, s_lbl = _latest(sales)
    s_old, s_old_lbl = _back(sales, 1)
    c_new, c_old = _latest(costs)[0], _back(costs, 1)[0]
    s_growth, c_growth = _pct_change(s_new, s_old), _pct_change(c_new, c_old)
    if s_growth is None or c_growth is None:
        return
    proof = (f"Sales {_fmt(s_growth, '%')} vs expenses {_fmt(c_growth, '%')} "
             f"({s_old_lbl} -> {s_lbl})")
    if s_growth > c_growth + 2 and s_growth > 0:
        bag.o("Sales are outgrowing costs - operating leverage should widen "
              "margins.", proof, "operating_leverage", 2)
    elif c_growth > s_growth + 2 and c_growth > 0:
        bag.w("Costs are rising faster than sales.", proof, "operating_leverage", 2)


# ====================================================== rules: working capital
def _rule_working_capital(ctx: _Ctx, bag: _Bag) -> None:
    _days(ctx, bag, "Debtor Days", "debtor_days", "Receivables",
          heavy=120, light=90, stretch=45)
    _days(ctx, bag, "Inventory Days", "inventory_days", "Inventory",
          heavy=180, light=120, stretch=60)

    wcd = _pairs(ctx.ratios, "Working Capital Days")
    if wcd:
        val, lbl = _latest(wcd)
        proof = f"Working capital days {_fmt(val)} ({lbl})"
        if val <= 0:
            bag.s("The business runs on negative working capital - customers fund "
                  "it.", proof, "working_capital_days", 2)
        elif val >= 150:
            bag.w("Working capital absorbs a very large slice of the year's sales.",
                  proof, "working_capital_days", 2)
        elif val >= 90:
            bag.w("Working capital intensity is on the higher side.",
                  proof, "working_capital_days", 1)

    ccc = _pairs(ctx.ratios, "Cash Conversion Cycle")
    if ccc:
        val, lbl = _latest(ccc)
        proof = f"Cash conversion cycle {_fmt(val)} days ({lbl})"
        if val <= 0:
            bag.s("Cash comes in before it goes out - a negative conversion cycle.",
                  proof, "cash_conversion_cycle", 2)
        elif val >= 150:
            bag.w("Cash stays locked in the working-capital cycle for a long time.",
                  proof, "cash_conversion_cycle", 2)

    roce_row = _pairs(ctx.ratios, "ROCE %", "ROE %")
    if len(roce_row) >= 4:
        new, new_lbl = _latest(roce_row)
        old, old_lbl = _back(roce_row, 3)
        proof = (f"ROCE {_fmt(old, '%')} ({old_lbl}) -> {_fmt(new, '%')} ({new_lbl})")
        if new - old >= 3:
            bag.s("Return on capital has improved over recent years.",
                  proof, "roce_trend", 2)
        elif new - old <= -3:
            bag.w("Return on capital has been eroding over recent years.",
                  proof, "roce_trend", 2)


def _days(ctx: _Ctx, bag: _Bag, label: str, metric: str, noun: str,
          heavy: float, light: float, stretch: float) -> None:
    series = _pairs(ctx.ratios, label)
    if not series:
        return
    val, lbl = _latest(series)
    proof = f"{label} {_fmt(val)} ({lbl})"
    if val >= heavy:
        bag.w(f"{noun} tie up a lot of cash.", proof, metric, 2)
    elif val >= light:
        bag.w(f"{noun} days are on the higher side.", proof, metric, 1)
    if len(series) >= 2:
        old, old_lbl = _back(series, 1)
        if old > 0 and val >= old * 1.3 and val >= stretch:
            bag.t(f"{noun} are stretching year on year.",
                  f"{label} {_fmt(old)} ({old_lbl}) -> {_fmt(val)} ({lbl})",
                  f"{metric}_trend", 2)


# ========================================================= rules: valuation
def _rule_dcf(ctx: _Ctx, bag: _Bag) -> None:
    if not ctx.dcf.get("ok"):
        return
    mos = to_float(ctx.dcf.get("margin_of_safety"))
    intrinsic = to_float(ctx.dcf.get("intrinsic_per_share"))
    price = to_float(ctx.dcf.get("current_price"))
    if mos is not None and intrinsic is not None and price is not None:
        proof = (f"DCF intrinsic {_fmt(intrinsic)} vs price {_fmt(price)} - "
                 f"margin of safety {_fmt(mos, '%')}")
        if mos >= 25:
            bag.s("The DCF puts intrinsic value well above the market price.",
                  proof, "dcf_mos", 3)
            bag.o("Buying below the model's intrinsic value leaves room for a "
                  "re-rating.", proof, "dcf_valuation", 3)
        elif mos > 0:
            bag.s("The DCF puts intrinsic value above the market price.",
                  proof, "dcf_mos", 2)
            bag.o("The shares trade under the model's intrinsic value.",
                  proof, "dcf_valuation", 2)
        elif mos <= -30:
            bag.w("The DCF says the market price is far above intrinsic value.",
                  proof, "dcf_mos", 3)
            bag.t("A rich price versus intrinsic value is vulnerable to any "
                  "de-rating.", proof, "valuation_derating", 2)
        elif mos < 0:
            bag.w("The DCF puts intrinsic value below the market price.",
                  proof, "dcf_mos", 2)

    implied = to_float(_dig(ctx.dcf, "reverse", "implied_growth"))
    if implied is None:
        return
    delivered = to_float(_dig(ctx.growth, "Compounded Profit Growth", "5 Years"))
    if delivered is not None:
        proof = (f"Reverse DCF implies {_fmt(implied, '%')} earnings growth vs "
                 f"{_fmt(delivered, '%')} delivered over 5 years")
        if delivered - implied >= 3:
            bag.o("The price embeds less growth than the company has actually "
                  "delivered.", proof, "reverse_dcf", 2)
            return
        if implied - delivered >= 5:
            bag.t("The price already embeds growth well above the delivered "
                  "record.", proof, "reverse_dcf", 2)
            return
    if implied <= 6:
        bag.o("Expectations baked into the price are low, so little has to go "
              "right.", f"Reverse DCF implied growth {_fmt(implied, '%')}",
              "reverse_dcf", 2)
    elif implied >= 25:
        bag.t("The price demands a very high growth rate to be justified.",
              f"Reverse DCF implied growth {_fmt(implied, '%')}", "reverse_dcf", 2)


def _rule_multiples(ctx: _Ctx, bag: _Bag) -> None:
    raw_pe = ctx.overview.get("Stock P/E")
    pe = to_float(raw_pe)
    if pe is not None and pe > 0:
        proof = f"Stock P/E {_clean(raw_pe)}"
        if pe >= 60:
            bag.w("The stock carries a very rich earnings multiple.",
                  proof, "pe_absolute", 2)
            bag.t("A very high multiple gives the price a long way to fall on any "
                  "disappointment.", proof, "valuation_derating", 2)
        elif pe >= 40:
            bag.w("The earnings multiple is demanding.", proof, "pe_absolute", 2)
        elif pe <= 12:
            bag.o("The headline earnings multiple is low, leaving room if profits "
                  "hold.", proof, "pe_absolute", 1)

    price = to_float(ctx.overview.get("Current Price"))
    book = to_float(ctx.overview.get("Book Value"))
    if price is not None and book is not None and book > 0:
        pb = price / book
        proof = (f"Price {_clean(ctx.overview.get('Current Price'))} vs book value "
                 f"{_clean(ctx.overview.get('Book Value'))} = {_fmt(pb, 'x')}")
        if pb < 1:
            bag.o("The shares change hands below their stated book value.",
                  proof, "pb_absolute", 2)
        elif pb >= 8:
            bag.w("The price is many times the accounting book value.",
                  proof, "pb_absolute", 1)

    raw_yield = ctx.overview.get("Dividend Yield")
    div = to_float(raw_yield)
    if div is not None and div > 0:
        proof = f"Dividend Yield {_clean(raw_yield)}"
        bag.s("The company pays a dividend to shareholders."
              if div < 2 else "The dividend yield is meaningful.",
              proof, "dividend_yield", 2 if div >= 2 else 1)

    payout = _pairs(ctx.profit_loss, "Dividend Payout %")
    if payout:
        val, lbl = _latest(payout)
        if val >= 20:
            bag.s("A healthy share of profit is returned to shareholders.",
                  f"Dividend payout {_fmt(val, '%')} ({lbl})", "dividend_payout", 1)

    mcap = to_float(ctx.overview.get("Market Cap"))
    if mcap is not None and 0 < mcap < 500:
        bag.t("Small size brings liquidity and volatility risk.",
              f"Market Cap {_clean(ctx.overview.get('Market Cap'))}", "market_cap", 1)


def _rule_peers(ctx: _Ctx, bag: _Bag) -> None:
    """analysis.health.peers - the company's ratio next to its sector median."""
    for key in ("roce", "roe", "roa"):
        node = _dict(ctx.peers.get(key))
        val, sec = to_float(node.get("value")), to_float(node.get("sector"))
        if val is None or sec is None or sec <= 0:
            continue
        unit = _PEER_UNITS.get(_clean(node.get("unit")), "")
        name = _PEER_LABELS[key]
        proof = f"{name} {_fmt(val, unit)} vs sector median {_fmt(sec, unit)}"
        if val >= sec * 1.15:
            bag.s(f"{name} runs well ahead of the sector median.",
                  proof, f"{key}_vs_sector", 2)
        elif val <= sec * 0.85:
            bag.w(f"{name} trails the sector median.", proof, f"{key}_vs_sector", 2)
            if key in ("roce", "roe"):
                bag.t(f"Peers earn a better {name} - the competitive position looks "
                      f"weaker.", proof, f"competition_{key}", 2)

    for key in ("pe", "pb", "ev_ebitda"):
        node = _dict(ctx.peers.get(key))
        val, sec = to_float(node.get("value")), to_float(node.get("sector"))
        if val is None or sec is None or val <= 0 or sec <= 0:
            continue
        unit = _PEER_UNITS.get(_clean(node.get("unit")), "")
        name = _PEER_LABELS[key]
        proof = f"{name} {_fmt(val, unit)} vs sector median {_fmt(sec, unit)}"
        if val >= sec * 1.2:
            bag.w(f"{name} is expensive against the sector median.",
                  proof, f"{key}_vs_sector", 2)
            if key == "pe":
                bag.t("A premium multiple versus peers can unwind quickly if growth "
                      "slips.", proof, "peer_derating", 2)
        elif val <= sec * 0.8:
            bag.o(f"{name} is cheaper than the sector median.",
                  proof, f"{key}_vs_sector", 2)


# ====================================================== rules: ownership flow
def _rule_shareholding(ctx: _Ctx, bag: _Bag) -> None:
    prom = _pairs(ctx.shareholding, "Promoters")
    if prom:
        val, lbl = _latest(prom)
        proof = f"Promoter holding {_fmt(val, '%')} ({lbl})"
        if val >= 60:
            bag.s("Promoters hold a controlling majority - skin in the game.",
                  proof, "promoter_holding", 2)
        elif val >= 50:
            bag.s("Promoters still own more than half the company.",
                  proof, "promoter_holding", 1)
        elif val < 25:
            bag.w("Promoter holding is very low.", proof, "promoter_holding", 3)
        elif val < 40:
            bag.w("Promoter holding is on the low side.", proof, "promoter_holding", 2)

        if len(prom) >= 2:
            old, old_lbl = _back(prom, min(4, len(prom) - 1))
            delta = val - old
            move = (f"Promoter holding {_fmt(old, '%')} ({old_lbl}) -> "
                    f"{_fmt(val, '%')} ({lbl})")
            if delta >= 0.5:
                bag.s("Promoters have been adding to their stake.",
                      move, "promoter_holding_change", 2)
                bag.o("Continued promoter buying signals confidence in what is "
                      "coming.", move, "promoter_buying", 1)
            elif delta <= -0.5:
                bag.w("Promoters have been trimming their stake.",
                      move, "promoter_holding_change", 2)
                bag.t("Promoter selling is a warning about insider conviction.",
                      move, "promoter_selling", 2)

    _institution(ctx, bag, "FIIs", "fii_holding", "Foreign institutions")
    _institution(ctx, bag, "DIIs", "dii_holding", "Domestic institutions")

    fii, dii = _pairs(ctx.shareholding, "FIIs"), _pairs(ctx.shareholding, "DIIs")
    if len(fii) >= 2 and len(dii) >= 2:
        f_delta = _latest(fii)[0] - _back(fii, min(4, len(fii) - 1))[0]
        d_delta = _latest(dii)[0] - _back(dii, min(4, len(dii) - 1))[0]
        if f_delta <= -0.25 and d_delta <= -0.25:
            bag.t("Both foreign and domestic institutions have been reducing.",
                  f"FII {_fmt(f_delta, ' pp')}, DII {_fmt(d_delta, ' pp')} over "
                  f"the recent quarters", "institutional_exit", 2)

    label = _clean(ctx.money_flow.get("label")).upper()
    change = to_float(ctx.money_flow.get("change"))
    if label and change is not None:
        proof = f"{label} - institutional holding change {_fmt(change, ' pp')}"
        if "POSITIVE" in label:
            bag.s("Institutional money has been flowing in.", proof, "money_flow", 2)
            bag.o("Rising institutional interest can keep supporting the price.",
                  proof, "institutional_flow", 2)
        elif "NEGATIVE" in label:
            bag.w("Institutional money has been flowing out.", proof, "money_flow", 2)

    _pledge(ctx, bag)


def _institution(ctx: _Ctx, bag: _Bag, label: str, metric: str, noun: str) -> None:
    series = _pairs(ctx.shareholding, label)
    if len(series) < 2:
        return
    val, lbl = _latest(series)
    old, old_lbl = _back(series, min(4, len(series) - 1))
    delta = val - old
    proof = f"{label} {_fmt(old, '%')} ({old_lbl}) -> {_fmt(val, '%')} ({lbl})"
    if delta >= 0.5:
        bag.o(f"{noun} have been building a position.", proof, metric, 1)
    elif delta <= -0.5:
        bag.w(f"{noun} have been cutting their stake.", proof, metric, 1)


def _pledge(ctx: _Ctx, bag: _Bag) -> None:
    """Pledge only fires on an explicit datum - a row, a field or a Screener con."""
    pledge, proof = None, ""
    series = _pairs(ctx.shareholding, "Pledged", "Promoter Pledge", "Pledged %")
    if series:
        pledge, lbl = _latest(series)
        proof = f"Promoter pledge {_fmt(pledge, '%')} ({lbl})"
    elif to_float(ctx.fund.get("pledge")) is not None:
        pledge = to_float(ctx.fund.get("pledge"))
        proof = f"Promoter pledge {_fmt(pledge, '%')}"
    else:
        for con in ctx.cons:
            hit = _PLEDGE_RE.search(con)
            if hit:
                pledge, proof = to_float(hit.group(1)), f"Screener: {con}"
                break
    if pledge is None or not proof:
        return
    if pledge <= 0:
        bag.s("No promoter shares are pledged.", proof, "promoter_pledge", 2)
    else:
        weight = 3 if pledge >= 20 else 2
        bag.w("Promoters have pledged part of their holding.",
              proof, "promoter_pledge", weight)
        bag.t("Pledged promoter shares can be sold into a falling market, "
              "amplifying a decline.", proof, "promoter_pledge", weight)


# ======================================================= rules: price / risk
def _rule_technical(ctx: _Ctx, bag: _Bag) -> None:
    tech = ctx.technical
    above50, above200 = tech.get("above_50dma"), tech.get("above_200dma")
    cross = tech.get("golden_cross")
    if isinstance(above50, bool) and isinstance(above200, bool):
        proof = (f"Price {'above' if above50 else 'below'} the 50-DMA and "
                 f"{'above' if above200 else 'below'} the 200-DMA")
        if above50 and above200:
            bag.s("The price is trading above both its key moving averages.",
                  proof, "moving_averages", 2)
        elif not above200:
            bag.w("The price sits below its long-term moving average.",
                  proof, "moving_averages", 2)
    elif isinstance(above200, bool) and not above200:
        bag.w("The price sits below its long-term moving average.",
              "Price below the 200-DMA", "moving_averages", 2)
    if cross is True:
        bag.s("The 50-day average has crossed above the 200-day average.",
              "Golden cross (50-DMA > 200-DMA)", "golden_cross", 1)

    rs = to_float(tech.get("rs_rating"))
    if rs is not None:
        proof = f"RS rating {_fmt(rs)} vs {_clean(tech.get('benchmark')) or 'the benchmark'}"
        if rs >= 80:
            bag.s("Relative strength is in the top tier of the market.",
                  proof, "rs_rating", 2)
        elif rs <= 30:
            bag.w("Relative strength is in the bottom tier of the market.",
                  proof, "rs_rating", 2)

    pos = to_float(tech.get("pos_52w"))
    dist = to_float(tech.get("dist_52w_high"))
    if pos is not None:
        proof = f"{_fmt(pos, '%')} up the 52-week range" + (
            f", {_fmt(dist, '%')} from the high" if dist is not None else "")
        if pos >= 80:
            bag.s("The stock is trading near the top of its 52-week range.",
                  proof, "pos_52w", 2)
        elif pos <= 25:
            bag.w("The stock is languishing near the bottom of its 52-week range.",
                  proof, "pos_52w", 2)
    if dist is not None and dist <= -35:
        bag.w("The price is a long way below its 52-week high.",
              f"{_fmt(dist, '%')} from the 52-week high", "dist_52w_high", 2)

    excess = to_float(tech.get("excess_12m"))
    if excess is not None:
        proof = f"12-month excess return {_fmt(excess, '%')} vs the benchmark"
        if excess >= 20:
            bag.s("The stock has beaten its benchmark over the past year.",
                  proof, "relative_return", 1)
        elif excess <= -20:
            bag.w("The stock has lagged its benchmark over the past year.",
                  proof, "relative_return", 2)


def _rule_risk(ctx: _Ctx, bag: _Bag) -> None:
    drawdown = to_float(ctx.risk.get("max_drawdown"))
    if drawdown is not None:
        proof = f"Maximum drawdown {_fmt(drawdown, '%')}"
        if drawdown <= -50:
            bag.t("The stock has a history of very deep drawdowns.",
                  proof, "max_drawdown", 3)
        elif drawdown <= -35:
            bag.t("The stock has drawn down sharply in the past.",
                  proof, "max_drawdown", 2)

    vol = to_float(ctx.risk.get("ann_vol"))
    if vol is not None:
        proof = f"Annualised volatility {_fmt(vol, '%')}"
        if vol >= 50:
            bag.t("Price volatility is very high.", proof, "ann_vol", 2)
        elif vol >= 35:
            bag.t("Price volatility is above average.", proof, "ann_vol", 1)

    sharpe = to_float(ctx.risk.get("sharpe"))
    if sharpe is not None:
        proof = f"Sharpe ratio {_fmt(sharpe)}"
        if sharpe < 0:
            bag.t("Risk-adjusted returns have been negative.", proof, "sharpe", 2)
        elif sharpe >= 1:
            bag.s("Returns have been strong relative to the risk taken.",
                  proof, "sharpe", 1)


def _rule_cyclical(ctx: _Ctx, bag: _Bag) -> None:
    label = _clean(ctx.cyclical.get("label")).upper()
    pos = [_clean(q) for q in _list(ctx.cyclical.get("positive_quarters")) if _clean(q)]
    neg = [_clean(q) for q in _list(ctx.cyclical.get("negative_quarters")) if _clean(q)]
    detail = []
    if pos:
        detail.append("strong: " + ", ".join(pos))
    if neg:
        detail.append("weak: " + ", ".join(neg))
    proof = (label + (" - " + "; ".join(detail) if detail else "")) if label else ""
    if label == "CYCLICAL" and proof:
        bag.w("Earnings follow a seasonal cycle rather than a steady line.",
              proof, "cyclical", 1)
    if pos:
        bag.o(f"Historically strong quarters ({', '.join(pos)}) are still in the "
              f"cycle.", f"Positive quarters: {', '.join(pos)}",
              "cyclical_positive", 1)
    if neg:
        bag.t(f"Historically weak quarters ({', '.join(neg)}) are still in the "
              f"cycle.", f"Negative quarters: {', '.join(neg)}",
              "cyclical_negative", 1)


def _rule_growth_insight(ctx: _Ctx, bag: _Bag) -> None:
    label = _clean(ctx.growth_insight.get("label")).upper()
    detail = _clean(ctx.growth_insight.get("long")) or _clean(ctx.growth_insight.get("recent"))
    if not label or not detail:
        return
    if label == "FUNDAMENTALS-LED":
        bag.s("Fundamentals have been running ahead of the share price.",
              detail, "growth_insight", 2)
    elif label == "PRICE-LED":
        bag.w("The share price has run ahead of the fundamentals.",
              detail, "growth_insight", 3)
        bag.t("A price that has outrun earnings needs the growth to catch up or it "
              "de-rates.", detail, "price_led_derating", 2)


# ========================================================== rules: the signal
def _rule_signal(ctx: _Ctx, bag: _Bag) -> None:
    _block(ctx, bag, "results", "results_score", "The latest results are strong.",
           "The latest results are weak.", 85, 70, 30, 15)
    _block(ctx, bag, "fundamental", "fundamental_score",
           "The fundamental block scores well.", "The fundamental block scores badly.",
           78, 65, 35, 20)
    _block(ctx, bag, "technical", "technical_score",
           "The technical block scores well.", "The technical block scores badly.",
           85, 75, 25, 15)

    metrics = _dict(_dig(ctx.blocks, "results", "metrics"))
    np_yoy = to_float(metrics.get("np_yoy"))
    if np_yoy is not None:
        proof = f"Net profit {_fmt(np_yoy, '%')} YoY (latest quarter)"
        if np_yoy >= 20:
            bag.s("Net profit is growing fast year on year.", proof, "np_yoy", 3)
        elif np_yoy < 0:
            bag.w("Net profit fell year on year in the latest quarter.",
                  proof, "np_yoy", 3)
    sales_yoy = to_float(metrics.get("sales_yoy"))
    if sales_yoy is not None:
        proof = f"Sales {_fmt(sales_yoy, '%')} YoY (latest quarter)"
        if sales_yoy >= 15:
            bag.s("Sales are growing at a healthy year-on-year rate.",
                  proof, "sales_yoy", 2)
        elif sales_yoy < 0:
            bag.w("Sales fell year on year in the latest quarter.",
                  proof, "sales_yoy", 2)
    np_qoq = to_float(metrics.get("np_qoq"))
    if np_qoq is not None and np_qoq < 0:
        bag.w("Profit slipped versus the previous quarter.",
              f"Net profit {_fmt(np_qoq, '%')} QoQ", "np_qoq", 1)
    accel = to_float(metrics.get("accel"))
    if accel is not None:
        proof = f"Earnings acceleration {_fmt(accel)} (YoY growth vs the prior quarter)"
        if accel > 0:
            bag.s("Earnings growth is accelerating.", proof, "earnings_accel", 2)
        elif accel < 0:
            bag.w("Earnings growth is decelerating.", proof, "earnings_accel", 1)
    opm_exp = to_float(metrics.get("opm_exp"))
    if opm_exp is not None:
        proof = f"Operating margin change {_fmt(opm_exp, ' pp')} in the latest quarter"
        if opm_exp > 0:
            bag.s("Margins expanded in the latest quarter.", proof, "opm_expansion", 2)
        elif opm_exp < 0:
            bag.w("Margins contracted in the latest quarter.", proof, "opm_expansion", 2)

    label = _clean(ctx.signal.get("label")).upper()
    composite = to_float(ctx.signal.get("composite"))
    if label in ("BUY", "SELL") and composite is not None:
        proof = (f"TechnoFunda signal {label} (composite {_fmt(composite)}"
                 + (f", {_clean(ctx.signal.get('confidence'))} confidence"
                    if ctx.signal.get("confidence") else "") + ")")
        if label == "BUY":
            bag.s("The rules-based TechnoFunda screen reads BUY.",
                  proof, "signal_label", 2)
        else:
            bag.w("The rules-based TechnoFunda screen reads SELL.",
                  proof, "signal_label", 2)

    for reason in [_clean(r) for r in _list(ctx.signal.get("reasons_pos")) if _clean(r)][:6]:
        bag.s(reason[0].upper() + reason[1:], f"Signal reason: {reason}",
              "signal_flag", 1)
    for reason in [_clean(r) for r in _list(ctx.signal.get("reasons_neg")) if _clean(r)][:6]:
        bag.w(reason[0].upper() + reason[1:], f"Signal flag: {reason}",
              "signal_flag", 1)

    for flag in ctx.bias_flags:
        flag = _dict(flag)
        level, title = _clean(flag.get("level")).lower(), _clean(flag.get("title"))
        note = _clean(flag.get("note"))
        if not title or not note or level not in ("warn", "caution"):
            continue
        bag.w(f"{title} - flagged by the insider-bias checklist.",
              f"bias check ({level}): {note}", "bias_check", 3 if level == "warn" else 2)


def _block(ctx: _Ctx, bag: _Bag, name: str, metric: str, good: str, bad: str,
           great_at: float, good_at: float, bad_at: float, awful_at: float) -> None:
    node = _dict(ctx.blocks.get(name))
    score = to_float(node.get("score"))
    if score is None:
        return
    reasons = [_clean(r) for r in _list(node.get("reasons")) if _clean(r)][:3]
    proof = (f"{name.capitalize()} score {_fmt(score)}/100"
             + (" - " + "; ".join(reasons) if reasons else ""))
    if score >= great_at:
        bag.s(good, proof, metric, 3)
    elif score >= good_at:
        bag.s(good, proof, metric, 2)
    elif score <= awful_at:
        bag.w(bad, proof, metric, 3)
    elif score <= bad_at:
        bag.w(bad, proof, metric, 2)


# ==================================================== rules: text-shaped input
def _rule_screener_text(ctx: _Ctx, bag: _Bag) -> None:
    for pro in ctx.pros[:8]:
        low = pro.lower()
        weight = 2 if any(word in low for word in _STRONG_PRO_WORDS) else 1
        bag.s(pro, f"Flagged as a positive: {pro}", "flagged_pro", weight)
    for con in ctx.cons[:8]:
        low = con.lower()
        weight = 2 if any(word in low for word in _STRONG_CON_WORDS) else 1
        bag.w(con, f"Flagged as a negative: {con}", "flagged_con", weight)
        if "contingent liabilit" in low:
            bag.t("Contingent liabilities sit off the reported balance sheet.",
                  f"Flagged as a negative: {con}", "contingent_liabilities", 2)
        if "interest coverage" in low:
            bag.t("A low interest-coverage ratio is a solvency risk.",
                  f"Flagged as a negative: {con}", "interest_coverage", 2)


def _rule_sector(ctx: _Ctx, bag: _Bag) -> None:
    label = _clean(ctx.sector.get("label") or ctx.sector.get("signal")).upper()
    name = _clean(ctx.sector.get("name") or ctx.sector.get("sector")) or "The sector"
    score = to_float(ctx.sector.get("score"))
    if label not in ("TAILWIND", "HEADWIND"):
        return
    proof = f"{name}: {label}" + (f" (score {_fmt(score)})" if score is not None else "")
    if label == "TAILWIND":
        bag.s(f"{name} is in a tailwind - the macro backdrop is supportive.",
              proof, "sector_tailwind", 2)
        bag.o(f"A {name.lower()} tailwind can lift earnings without the company "
              f"doing anything different.", proof, "sector_tailwind", 2)
    else:
        bag.w(f"{name} is in a headwind.", proof, "sector_headwind", 2)
        bag.t("A sector headwind can de-rate even a well-run company.",
              proof, "sector_headwind", 3)


_THEME_RULES = (
    ("guidance", "filing_guidance", 3, "Management guidance"),
    ("capex_expansion", "filing_capex", 3, "Capex / expansion plan"),
    ("demand_outlook", "filing_demand", 2, "Demand outlook"),
    ("orders_capacity", "filing_orders", 2, "Orders / capacity"),
    ("capital_allocation", "filing_capital_allocation", 1, "Capital allocation"),
    ("margins_costs", "filing_margins", 1, "Margins / costs"),
)


def _rule_filings(ctx: _Ctx, bag: _Bag) -> None:
    """Grounded filing facts - every point carries the company's own words."""
    for theme, metric, weight, noun in _THEME_RULES:
        for fact in _list(ctx.themes.get(theme))[:2]:
            claim, proof = _fact(fact, noun)
            if claim and proof:
                bag.o(claim, proof, metric, weight)

    for fact in ctx.commitments[:3]:
        fact = _dict(fact)
        claim, proof = _fact(fact, "Management commitment")
        timeframe = _clean(fact.get("timeframe"))
        if claim and proof:
            bag.o(claim, proof + (f" [{timeframe}]" if timeframe else ""),
                  "filing_commitment", 2)

    for fact in _list(ctx.themes.get("risks_headwinds"))[:3]:
        claim, proof = _fact(fact, "Risk disclosed in the filings")
        if not claim or not proof:
            continue
        blob = (claim + " " + proof).lower()
        if any(word in blob for word in _GOVERNANCE_WORDS):
            bag.t(claim, proof, "filing_governance_risk", 3)
        elif any(word in blob for word in _CONCENTRATION_WORDS):
            bag.t(claim, proof, "filing_concentration_risk", 3)
        else:
            bag.t(claim, proof, "filing_risk", 2)


def _fact(fact: Any, noun: str) -> tuple:
    fact = _dict(fact)
    claim = _clean(fact.get("claim"))
    quote = _clean(fact.get("quote"))
    if not claim or not quote:
        return "", ""   # ungrounded fact -> no point
    where = " - ".join(x for x in (_clean(fact.get("doc_kind")).replace("_", " "),
                                   _clean(fact.get("doc_date"))) if x)
    proof = f'{noun}: "{_shorten(quote, 260)}"' + (f" ({where})" if where else "")
    return _shorten(claim), proof


_RULES = (
    _rule_returns, _rule_margins, _rule_growth, _rule_trends,
    _rule_leverage, _rule_liquidity, _rule_debt_direction, _rule_capex,
    _rule_cash, _rule_earnings_quality, _rule_operating_leverage,
    _rule_working_capital, _rule_dcf, _rule_multiples, _rule_peers,
    _rule_shareholding, _rule_technical, _rule_risk, _rule_cyclical,
    _rule_growth_insight, _rule_signal, _rule_screener_text, _rule_sector,
    _rule_filings,
)


# ============================================================== finalisation
def _finalise(items: Sequence) -> list:
    """De-dupe, sort by weight desc (stable) and cap one quadrant.

    Three de-dupe passes, in order of confidence:
      * identical wording once normalised;
      * the same single-shot metric twice (list-shaped sources are exempt);
      * near-identical wording, but only where at least one side came from a
        free-text source - two computed rules with different metrics are
        different points even when they read alike ("Sales has compounded..."
        vs "Profit has compounded...").
    """
    kept: list = []
    keys: list = []
    for cand in items:
        cand_key = _tokens(cand.point)
        text_pair = cand.metric in REPEATABLE_METRICS
        hit = -1
        for i, prev in enumerate(kept):
            same_text = _slug(prev.point) == _slug(cand.point)
            same_metric = (prev.metric == cand.metric
                           and cand.metric not in REPEATABLE_METRICS)
            near = ((text_pair or prev.metric in REPEATABLE_METRICS)
                    and _jaccard(keys[i], cand_key) >= NEAR_DUPE_RATIO)
            if same_text or same_metric or near:
                hit = i
                break
        if hit < 0:
            kept.append(cand)
            keys.append(cand_key)
        elif cand.weight > kept[hit].weight:
            kept[hit] = cand           # keep the heavier reading, keep its slot
            keys[hit] = cand_key
    kept.sort(key=lambda p: -p.weight)
    return kept[:MAX_PER_QUADRANT]


def _coverage(ctx: _Ctx) -> dict:
    probes = (
        ("overview", bool(ctx.overview)),
        ("growth", bool(ctx.growth)),
        ("quarters", bool(_dict(ctx.quarters).get("rows"))),
        ("profit_loss", bool(_dict(ctx.profit_loss).get("rows"))),
        ("balance_sheet", bool(_dict(ctx.balance_sheet).get("rows"))),
        ("cash_flow", bool(_dict(ctx.cash_flow).get("rows"))),
        ("ratios", bool(_dict(ctx.ratios).get("rows"))),
        ("shareholding", bool(_dict(ctx.shareholding).get("rows"))),
        ("pros", bool(ctx.pros)),
        ("cons", bool(ctx.cons)),
        ("analysis.trends", bool(ctx.trends)),
        ("analysis.cyclical", bool(ctx.cyclical)),
        ("analysis.growth_insight", bool(ctx.growth_insight)),
        ("analysis.money_flow", bool(ctx.money_flow)),
        ("analysis.dcf", bool(ctx.dcf.get("ok"))),
        ("analysis.health", bool(ctx.health)),
        ("analysis.health.peers", bool(ctx.peers)),
        ("prices.technical", bool(ctx.technical)),
        ("prices.risk", bool(ctx.risk)),
        ("signal", bool(ctx.blocks or ctx.signal.get("label"))),
        ("signal.bias_check", bool(ctx.bias_flags)),
        ("upstox_ratios", bool(ctx.upstox)),
        ("sector", bool(ctx.sector.get("label") or ctx.sector.get("signal"))),
        ("filings", bool(ctx.themes or ctx.commitments)),
    )
    return {"inputs_used": [name for name, ok in probes if ok],
            "inputs_missing": [name for name, ok in probes if not ok]}


_VERDICTS = {
    "strengths": ("Strengths dominate the evidence (S{s}/W{w}/O{o}/T{t}), led by "
                  "'{lead}', with {other} points of weight still sitting on the "
                  "weakness, opportunity and threat side."),
    "weaknesses": ("Weaknesses dominate the evidence (S{s}/W{w}/O{o}/T{t}), led by "
                   "'{lead}', and the positives on record do not currently "
                   "outweigh them."),
    "opportunities": ("Opportunities dominate the evidence (S{s}/W{w}/O{o}/T{t}), "
                      "led by '{lead}', so the case leans on things that are not "
                      "in the reported numbers yet."),
    "threats": ("Threats dominate the evidence (S{s}/W{w}/O{o}/T{t}), led by "
                "'{lead}', so risk currently outweighs the rest of the picture."),
}


def _verdict(quads: dict, score: dict) -> str:
    if not any(quads.values()):
        return ("No SWOT point could be evidenced from this bundle - the inputs "
                "this engine reads were missing or empty.")
    best = max(score.values())
    winners = [q for q in QUADRANTS if score[_QKEY[q]] == best and quads[q]]
    counts = {"s": score["s"], "w": score["w"], "o": score["o"], "t": score["t"]}
    if len(winners) != 1:
        names = ", ".join(q for q in winners)
        return (f"No quadrant dominates - {names} carry equal weight "
                f"(S{counts['s']}/W{counts['w']}/O{counts['o']}/T{counts['t']}), so "
                f"the evidence does not favour one side.")
    quad = winners[0]
    lead = quads[quad][0].point.rstrip(".")
    other = sum(counts[k] for k in ("s", "w", "o", "t")) - counts[_QKEY[quad]]
    return _VERDICTS[quad].format(lead=lead, other=other, **counts)


_QKEY = {"strengths": "s", "weaknesses": "w", "opportunities": "o", "threats": "t"}


# ==================================================================== public
def build_swot(bundle: dict, *, sector: dict | None = None,
               filings: dict | None = None) -> dict:
    """Build a deterministic, evidence-backed SWOT for one fundamental bundle.

    Args:
        bundle: a ``docs/data/fundamental/<CODE>.json`` payload.
        sector: optional sector tailwind row - ``{"name"/"sector",
            "label"/"signal", "score"}``.
        filings: optional grounded filing facts - ``docs/data/docs/<CODE>.json``.

    Returns:
        ``{"strengths", "weaknesses", "opportunities", "threats", "score",
        "verdict", "coverage"}``.  Each quadrant is a list of
        ``{"point", "evidence", "metric", "weight"}`` sorted by weight
        descending and capped at :data:`MAX_PER_QUADRANT`.  Missing inputs
        simply produce fewer points - never invented ones.
    """
    ctx = _context(bundle, sector, filings)
    bag = _Bag()
    for rule in _RULES:
        rule(ctx, bag)

    quads = {
        "strengths": _finalise(bag.strengths),
        "weaknesses": _finalise(bag.weaknesses),
        "opportunities": _finalise(bag.opportunities),
        "threats": _finalise(bag.threats),
    }
    score = {_QKEY[q]: sum(p.weight for p in items) for q, items in quads.items()}
    out = {q: [p.as_dict() for p in items] for q, items in quads.items()}
    out["score"] = score
    out["verdict"] = _verdict(quads, score)
    out["coverage"] = _coverage(ctx)
    log.debug("swot %s: S%d W%d O%d T%d",
              ctx.fund.get("code") or "?", score["s"], score["w"],
              score["o"], score["t"])
    return out
