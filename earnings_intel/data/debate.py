"""
Adversarial BULL vs BEAR debate over ONE stock — a research artefact, not a chat.

Two agents argue the same company for several rounds, and neither is allowed to
say anything we did not hand them: `evidence_pack` first turns the already-baked
bundle (Screener fundamentals, Upstox peer ratios, DCF, prices, signal, sector
tailwind) and the already-grounded filing facts into a numbered, id-stable
evidence list, and that list is the ONLY admissible source. Every claim must
carry an id like ``[E7]``; a claim citing an id we never issued is stripped, a
turn with no valid citation at all is dropped, and both counts are published in
``_meta`` — which is what makes the transcript checkable instead of plausible.

On top of the model output sits a `scorecard` that no model touches: it is pure
arithmetic over who cited what, so it reports which side leaned on the heavier
evidence and — the genuinely useful part — which evidence NEITHER side used.
Blind spots cannot be faked.

The LLM runs ONLY here, server-side, during a bake. The published site reads the
baked JSON; the "Run Debate" button reveals it and never generates it.

Usage:
    from earnings_intel.data import debate

    bundle  = json.load(open("docs/data/fundamental/3MINDIA.json"))
    filings = json.load(open("docs/data/docs/3MINDIA.json"))
    sector  = {"sector": "Diversified", "signal": "TAILWIND", "score": 0.62}

    pack = debate.evidence_pack(bundle, filings=filings, sector=sector)
    pack[0]
    # {'id': 'E1', 'fact': 'Trades at a P/E of 72.7.', 'value': '72.7x',
    #  'source': 'Screener.in', 'url': 'https://www.screener.in/company/3MINDIA/',
    #  'side_hint': 'bear', 'family': 'valuation', 'weight': 3}

    out = debate.run_debate(bundle, filings=filings, sector=sector, rounds=3)
    out["rounds"][0]["cites"]            # ['E3', 'E11', 'E24']
    out["scorecard"]["blind_spots"]      # evidence neither side used
    out["_meta"]["turns_dropped"], out["_meta"]["cites_invalid"]

    # tests inject the model — no network, no key
    debate.run_debate(bundle, complete=fake_complete)

Contract:
  * `evidence_pack` is PURE and deterministic: same bundle -> same ids, same
    order. It emits only families that are actually present; nothing is invented
    and nothing is defaulted to zero.
  * `run_debate` NEVER raises. A missing key, a dead provider or a mangled turn
    comes back as {"error": ...} on an otherwise contract-shaped dict.
  * `complete` is injectable and defaults to `earnings_intel.llm.complete`, so
    the provider stays swappable and the tests stay offline.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from . import docanalysis as da

log = logging.getLogger("technofunda.debate")

__all__ = ["evidence_pack", "focus_pack", "strip_inverted_comparisons", "run_debate", "scorecard", "FAMILY_WEIGHT",
           "HIGH_WEIGHT"]

# --------------------------------------------------------------------- config
HIGH_WEIGHT = 3               # a "high-weight" citation for the scorecard

#: how much each evidence family counts for. Hard numbers the market can check
#: outrank narrative; management's own words outrank our derived labels.
FAMILY_WEIGHT = {
    "valuation": 3,
    "peer_ratio": 3,
    "growth": 3,
    "profitability": 3,
    "margin": 3,
    "cash": 3,
    "balance_sheet": 3,
    "capex": 2,
    "ownership": 2,
    "flow": 2,
    "dcf": 3,
    "technical": 2,
    "risk": 2,
    "insight": 2,
    "sector": 2,
    "screener_note": 1,
    "signal": 1,
    "filing": 3,
    "commitment": 3,
}

SIDES = ("bull", "bear")

_MAX_ROUNDS = 5
_MAX_ITEMS = 120              # prompt budget — the pack is truncated, not sampled
_MAX_FILING_FACTS = 24
_MAX_FACTS_PER_THEME = 3
_MAX_NOTES = 6                # Screener pros / cons kept per side
_MAX_REASONS = 4              # signal reasons kept per side
_MAX_FLAGS = 3                # bias-check flags kept
_MAX_TOKENS = 900
_TEMPERATURE = 0.2
_MAX_TURN_CHARS = 4000
_MIN_QUOTE = 12               # matches docanalysis._MIN_QUOTE_CHARS

_SCREENER = "Screener.in"
_PRICES_SRC = "scanX price history"
_SIGNAL_SRC = "scanX signal engine"
_SECTOR_SRC = "scanX sector tailwind"

_KIND_LABEL = {
    "concall_transcript": "concall transcript",
    "concall_ppt": "concall presentation",
    "concall_notes": "concall notes",
    "annual_report": "annual report",
    "announcement": "announcement",
}

# themes that argue for one side by construction
_THEME_SIDE = {
    "guidance": "bull", "demand_outlook": "bull", "capex_expansion": "bull",
    "orders_capacity": "bull", "margins_costs": "neutral",
    "capital_allocation": "neutral", "risks_headwinds": "bear",
}

_BIAS_SIDE = {"positive": "bull", "negative": "bear", "neutral": "neutral"}
_FLAG_SIDE = {"ok": "bull", "warn": "bear", "risk": "bear", "danger": "bear",
              "info": "neutral"}

_CITE_BLOCK = re.compile(r"\[([^\[\]]{1,80})\]")
_CITE_ID = re.compile(r"\bE(\d+)\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED = re.compile(r"[\"“]([^\"“”]{%d,400})[\"”]" % _MIN_QUOTE)
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


# ------------------------------------------------------------- pure: parsing
def _num(value) -> Optional[float]:
    """First number in a Screener-style string. '₹ 1,570' -> 1570.0, '-24%' -> -24.0."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = _NUM.search(str(value or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def _txt(value) -> str:
    return str(value or "").strip()


def _dget(obj, *path):
    """Nested dict lookup that never raises and never invents a level."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _series(section, name) -> tuple[list, list]:
    """``{"headers": [...], "rows": {name: [...]}}`` -> (headers, values)."""
    rows = _dget(section, "rows")
    if not isinstance(rows, dict):
        return [], []
    vals = rows.get(name)
    if not isinstance(vals, list) or not vals:
        return [], []
    heads = _dget(section, "headers")
    heads = heads if isinstance(heads, list) else []
    return list(heads), list(vals)


def _row_named(section, *needles) -> tuple[str, list, list]:
    """First row whose name contains any needle (case-insensitive)."""
    rows = _dget(section, "rows")
    if not isinstance(rows, dict):
        return "", [], []
    for name, vals in rows.items():
        low = str(name).lower()
        if any(n in low for n in needles) and isinstance(vals, list) and vals:
            heads = _dget(section, "headers")
            return str(name), (list(heads) if isinstance(heads, list) else []), list(vals)
    return "", [], []


def _at(heads: list, vals: list, idx: int) -> tuple[str, Optional[float], str]:
    """(period label, number, raw string) at `idx`, or ('', None, '')."""
    try:
        raw = vals[idx]
    except Exception:  # noqa: BLE001
        return "", None, ""
    period = ""
    try:
        period = _txt(heads[idx]) if heads and len(heads) == len(vals) else ""
    except Exception:  # noqa: BLE001
        period = ""
    return period, _num(raw), _txt(raw)


def _pct(x: Optional[float], nd: int = 1) -> str:
    return "" if x is None else f"{x:.{nd}f}%"


def _side_from_bias(bias) -> str:
    return _BIAS_SIDE.get(str(bias or "").strip().lower(), "neutral")


def _band(x: Optional[float], good: float, bad: float) -> str:
    """bull above `good`, bear below `bad`, else neutral. None -> neutral."""
    if x is None:
        return "neutral"
    if x >= good:
        return "bull"
    if x < bad:
        return "bear"
    return "neutral"


def _pretty_source(raw: str) -> str:
    s = _txt(raw)
    if s.startswith("upstox:"):
        return "Upstox " + s.split(":", 1)[1]
    return s


def _screener(period: str = "") -> str:
    p = _txt(period)
    return f"{_SCREENER} - {p}" if p else _SCREENER


# --------------------------------------------------------------- pure: bundle
def _view(bundle) -> dict:
    """Accept the baked file ({generated_at, fundamental, prices, signal, ...})
    or an already-flattened bundle. Never raises, never fabricates a section."""
    b = bundle if isinstance(bundle, dict) else {}
    fund = b.get("fundamental")
    fund = fund if isinstance(fund, dict) else b

    def pick(key):
        v = b.get(key)
        if isinstance(v, dict):
            return v
        v = fund.get(key)
        return v if isinstance(v, dict) else {}

    return {
        "fund": fund,
        "code": _txt(fund.get("code") or b.get("code")),
        "name": _txt(fund.get("name") or b.get("name")),
        "url": _txt(fund.get("url") or b.get("url")),
        "overview": pick("overview"),
        "growth": pick("growth"),
        "quarters": pick("quarters"),
        "profit_loss": pick("profit_loss"),
        "balance_sheet": pick("balance_sheet"),
        "cash_flow": pick("cash_flow"),
        "ratios": pick("ratios"),
        "shareholding": pick("shareholding"),
        "analysis": pick("analysis"),
        "prices": pick("prices"),
        "signal": pick("signal"),
        "upstox": pick("upstox_ratios"),
        "pros": [x for x in (fund.get("pros") or b.get("pros") or []) if _txt(x)],
        "cons": [x for x in (fund.get("cons") or b.get("cons") or []) if _txt(x)],
        "generated_at": _txt(b.get("generated_at") or fund.get("generated_at")),
    }


class _Pack:
    """Append-only collector that hands out the stable E-ids."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, family: str, fact: str, value: str = "", *, source: str,
            url: str = "", side_hint: str = "neutral", quote: str = "") -> None:
        fact = _txt(fact)
        if not fact or len(self.items) >= _MAX_ITEMS:
            return
        item = {
            "id": f"E{len(self.items) + 1}",
            "fact": fact[:400],
            "value": _txt(value)[:80],
            "source": _txt(source)[:120],
            "url": _txt(url)[:400],
            "side_hint": side_hint if side_hint in ("bull", "bear") else "neutral",
            "family": family,
            "weight": int(FAMILY_WEIGHT.get(family, 1)),
        }
        if quote:
            item["quote"] = _txt(quote)[:600]
        self.items.append(item)


# ------------------------------------------------------- pure: evidence families
def _ev_valuation(p: _Pack, v: dict) -> None:
    ov, url = v["overview"], v["url"]
    if not isinstance(ov, dict) or not ov:
        return
    price = _num(ov.get("Current Price"))
    pe = _num(ov.get("Stock P/E"))
    if pe is not None:
        # a negative or zero P/E means no earnings to capitalise — that is bearish,
        # not "cheap", so it never goes through the lower-is-better band.
        side = "bear" if pe <= 0 else _band(-pe, -25.0, -45.0)
        p.add("valuation", f"Trades at a P/E of {pe:g}.", f"{pe:g}x",
              source=_screener(), url=url, side_hint=side)
    bv = _num(ov.get("Book Value"))
    if bv and price:
        pb = price / bv
        p.add("valuation",
              f"Price-to-book is {pb:.2f}x (price {ov.get('Current Price')} on a "
              f"book value of {ov.get('Book Value')}).", f"{pb:.2f}x",
              source=_screener(), url=url, side_hint=_band(-pb, -3.0, -6.0))
    mc = _txt(ov.get("Market Cap")).rstrip(". ")
    if mc and _num(mc) is not None:
        p.add("valuation", f"Market capitalisation is {mc}.", mc,
              source=_screener(), url=url)
    dy = _num(ov.get("Dividend Yield"))
    if dy is not None:
        p.add("valuation", f"Dividend yield is {_pct(dy, 2)}.", _pct(dy, 2),
              source=_screener(), url=url, side_hint=_band(dy, 2.0, 0.25))
    hl = _NUM.findall(str(ov.get("High / Low") or "").replace(",", ""))
    if price and len(hl) >= 2:
        hi, lo = _num(hl[0]), _num(hl[1])
        if hi and lo and hi > lo:
            pos = (price - lo) / (hi - lo) * 100.0
            p.add("valuation",
                  f"The stock sits {pos:.0f}% of the way up its 52-week range "
                  f"({lo:,.0f} low to {hi:,.0f} high).", f"{pos:.0f}% of range",
                  source=_screener(), url=url, side_hint=_band(pos, 70.0, 30.0))


def _ev_profitability(p: _Pack, v: dict) -> None:
    ov, url = v["overview"], v["url"]
    for key, good, bad in (("ROCE", 15.0, 10.0), ("ROE", 15.0, 10.0)):
        x = _num(ov.get(key)) if isinstance(ov, dict) else None
        if x is not None:
            p.add("profitability", f"{key} is {_pct(x)}.", _pct(x),
                  source=_screener(), url=url, side_hint=_band(x, good, bad))


_PEER_LABEL = {"pe": "P/E", "pb": "P/B", "roa": "ROA", "roe": "ROE",
               "roce": "ROCE", "ev_ebitda": "EV/EBITDA"}
_PEER_ORDER = ("pe", "pb", "roa", "roe", "roce", "ev_ebitda")
_LOWER_IS_BETTER = ("pe", "pb", "ev_ebitda")


def _ev_peers(p: _Pack, v: dict) -> None:
    """analysis.health.peers first (it carries our bias), else raw upstox_ratios."""
    peers = _dget(v["analysis"], "health", "peers")
    fallback = v["upstox"] if isinstance(v["upstox"], dict) else {}
    src = peers if isinstance(peers, dict) and peers else fallback
    if not isinstance(src, dict):
        return
    for key in _PEER_ORDER:
        row = src.get(key)
        if not isinstance(row, dict):
            continue
        val, sec = _num(row.get("value")), _num(row.get("sector"))
        if val is None:
            continue
        unit = "%" if _txt(row.get("unit")) == "pct" else "x"
        label = _PEER_LABEL[key]
        side = _side_from_bias(row.get("bias"))
        if side == "neutral" and sec is not None:
            if key in _LOWER_IS_BETTER:
                # a negative multiple is not "cheap" — it means no earnings, so
                # the comparison is only meaningful when both sides are positive
                side = ("bull" if val < sec else "bear") if val > 0 and sec > 0 else "neutral"
            else:
                side = "bull" if val > sec else "bear"
        if sec is None:
            fact = f"{label} is {val:g}{unit}; no sector benchmark was published."
        else:
            fact = (f"{label} of {val:g}{unit} against a sector benchmark of "
                    f"{sec:g}{unit}.")
        p.add("peer_ratio", fact, f"{val:g}{unit}",
              source=_pretty_source(row.get("source")) or "Upstox key-ratios",
              side_hint=side)


_GROWTH_BLOCKS = (
    ("Compounded Sales Growth", "sales", 15.0, 5.0),
    ("Compounded Profit Growth", "profit", 15.0, 5.0),
    ("Stock Price CAGR", "share price", 15.0, 0.0),
    ("Return on Equity", "return on equity", 15.0, 10.0),
)
_PERIOD_ORDER = ("10 Years", "5 Years", "3 Years", "1 Year", "TTM", "Last Year")


def _ev_growth(p: _Pack, v: dict) -> None:
    g, url = v["growth"], v["url"]
    if not isinstance(g, dict):
        return
    for block, noun, good, bad in _GROWTH_BLOCKS:
        rows = g.get(block)
        if not isinstance(rows, dict):
            continue
        for period in _PERIOD_ORDER:
            if period not in rows:
                continue
            x = _num(rows.get(period))
            if x is None:
                continue
            window = "the trailing twelve months" if period == "TTM" else period.lower()
            p.add("growth",
                  f"Compounded {noun} growth over {window} is {_pct(x, 0)}."
                  if block != "Return on Equity" else
                  f"Median return on equity over {window} is {_pct(x, 0)}.",
                  _pct(x, 0), source=_screener(), url=url,
                  side_hint=_band(x, good, bad))


def _ev_margin(p: _Pack, v: dict) -> None:
    url = v["url"]
    heads, vals = _series(v["profit_loss"], "OPM %")
    if vals and len(vals) >= 4:
        now_p, now, _ = _at(heads, vals, -1)
        then_p, then, _ = _at(heads, vals, -4)
        if now is not None and then is not None:
            delta = now - then
            p.add("margin",
                  f"Operating margin is {_pct(now, 0)} in {now_p or 'the latest year'} "
                  f"against {_pct(then, 0)} three years earlier "
                  f"({delta:+.0f} percentage points).", f"{delta:+.0f} pp",
                  source=_screener(now_p), url=url,
                  side_hint=_band(delta, 1.0, -1.0))
        elif now is not None:
            p.add("margin", f"Operating margin is {_pct(now, 0)} in "
                            f"{now_p or 'the latest year'}.", _pct(now, 0),
                  source=_screener(now_p), url=url)
    qh, qv = _series(v["quarters"], "OPM")
    if qv and len(qv) >= 5:
        now_p, now, _ = _at(qh, qv, -1)
        then_p, then, _ = _at(qh, qv, -5)
        if now is not None and then is not None:
            delta = now - then
            p.add("margin",
                  f"Quarterly operating margin is {_pct(now, 0)} in "
                  f"{now_p or 'the latest quarter'} against {_pct(then, 0)} in the "
                  f"year-ago quarter ({delta:+.0f} percentage points).",
                  f"{delta:+.0f} pp", source=_screener(now_p), url=url,
                  side_hint=_band(delta, 1.0, -1.0))
    for scope in ("yearly", "quarterly"):
        label = _txt(_dget(v["analysis"], "trends", scope, "OPM%", "label"))
        if not label:
            continue
        n = _num(_dget(v["analysis"], "trends", scope, "OPM%", "n"))
        unit = _txt(_dget(v["analysis"], "trends", scope, "OPM%", "unit"))
        span = f" over the last {n:g} {unit or 'periods'}" if n is not None else ""
        side = ("bull" if "increas" in label.lower()
                else "bear" if "decreas" in label.lower() else "neutral")
        p.add("margin",
              f"The {scope} operating-margin trend reads {label}{span}.",
              label, source=_screener(), url=url, side_hint=side)


def _ev_cash(p: _Pack, v: dict) -> None:
    url = v["url"]
    ocf = _dget(v["analysis"], "health", "ocf_np")
    if isinstance(ocf, dict) and _num(ocf.get("value")) is not None:
        x = _num(ocf.get("value"))
        p.add("cash",
              f"Cash conversion is {x:.2f}x — operating cash flow against reported "
              f"profit for {_txt(ocf.get('year')) or 'the latest year'}. "
              f"{_txt(ocf.get('note'))}".strip(),
              f"{x:.2f}x", source=_screener(_txt(ocf.get("year"))), url=url,
              side_hint=_side_from_bias(ocf.get("bias")))
    heads, vals = _series(v["cash_flow"], "Cash from Operating Activity")
    if vals:
        period, x, raw = _at(heads, vals, -1)
        if x is not None:
            p.add("cash",
                  f"Operating cash flow was Rs {raw} Cr in "
                  f"{period or 'the latest year'}.", f"Rs {raw} Cr",
                  source=_screener(period), url=url,
                  side_hint="bull" if x > 0 else "bear")
    heads, vals = _series(v["cash_flow"], "Free Cash Flow")
    if vals:
        period, x, raw = _at(heads, vals, -1)
        if x is not None:
            p.add("cash",
                  f"Free cash flow was Rs {raw} Cr in {period or 'the latest year'}.",
                  f"Rs {raw} Cr", source=_screener(period), url=url,
                  side_hint="bull" if x > 0 else "bear")
    heads, vals = _series(v["cash_flow"], "CFO/OP")
    if vals:
        period, x, raw = _at(heads, vals, -1)
        if x is not None:
            p.add("cash",
                  f"Operating cash flow was {raw} of operating profit in "
                  f"{period or 'the latest year'}.", _txt(raw),
                  source=_screener(period), url=url,
                  side_hint=_band(x, 80.0, 50.0))


def _ev_balance(p: _Pack, v: dict) -> None:
    url = v["url"]
    for key, noun in (("debt_equity", "Debt to equity"),
                      ("current_ratio", "Current ratio")):
        row = _dget(v["analysis"], "health", key)
        if isinstance(row, dict) and _num(row.get("value")) is not None:
            x = _num(row.get("value"))
            note = _txt(row.get("note"))
            p.add("balance_sheet",
                  f"{noun} is {x:.2f}x ({_txt(row.get('year')) or 'latest'})."
                  + (f" {note}" if note else ""),
                  f"{x:.2f}x",
                  source=_pretty_source(row.get("source")) or _SCREENER,
                  url=url, side_hint=_side_from_bias(row.get("bias")))
    heads, vals = _series(v["balance_sheet"], "Borrowings")
    if len(vals) >= 2:
        period, now, raw = _at(heads, vals, -1)
        _, prev, praw = _at(heads, vals, -2)
        if now is not None and prev is not None:
            direction = "up" if now > prev else "down" if now < prev else "flat"
            p.add("balance_sheet",
                  f"Borrowings are Rs {raw} Cr in {period or 'the latest year'}, "
                  f"{direction} from Rs {praw} Cr a year earlier.", f"Rs {raw} Cr",
                  source=_screener(period), url=url,
                  side_hint="bear" if now > prev else "bull" if now < prev else "neutral")


def _ev_capex(p: _Pack, v: dict) -> None:
    cwip = _dget(v["analysis"], "health", "cwip")
    if not isinstance(cwip, dict):
        return
    pct = _num(cwip.get("pct_change"))
    latest = _num(cwip.get("latest"))
    if pct is None and latest is None:
        return
    note = _txt(cwip.get("note"))
    bits = []
    if latest is not None:
        bits.append(f"CWIP stands at Rs {latest:g} Cr "
                    f"({_txt(cwip.get('year')) or 'latest'})")
    if pct is not None:
        bits.append(f"a {pct:+.1f}% move on the prior year")
    p.add("capex", ", ".join(bits) + "." + (f" {note}" if note else ""),
          _pct(pct) if pct is not None else f"Rs {latest:g} Cr",
          source=_screener(_txt(cwip.get("year"))), url=v["url"],
          side_hint=_side_from_bias(cwip.get("bias")))


def _ev_ownership(p: _Pack, v: dict) -> None:
    sh, url = v["shareholding"], v["url"]
    heads, vals = _series(sh, "Promoters")
    if vals:
        last_p, last, raw = _at(heads, vals, -1)
        first_p, first, fraw = _at(heads, vals, 0)
        if last is not None and first is not None and len(vals) > 1:
            delta = last - first
            side = _band(delta, 0.5, -0.5)
            p.add("ownership",
                  f"Promoters hold {raw} as of {last_p or 'the latest quarter'}, "
                  f"against {fraw} in {first_p or 'the earliest quarter shown'} "
                  f"({delta:+.2f} percentage points).", _txt(raw),
                  source=_screener(last_p), url=url, side_hint=side)
        elif last is not None:
            p.add("ownership",
                  f"Promoters hold {raw} as of {last_p or 'the latest quarter'}.",
                  _txt(raw), source=_screener(last_p), url=url)
    name, heads, vals = _row_named(sh, "pledg")
    if vals:
        last_p, last, raw = _at(heads, vals, -1)
        if last is not None:
            p.add("ownership",
                  f"Promoter pledge stands at {raw} "
                  f"({last_p or 'the latest quarter'}).", _txt(raw),
                  source=_screener(last_p), url=url,
                  side_hint="bear" if last > 0 else "bull")


def _ev_flow(p: _Pack, v: dict) -> None:
    sh, url = v["shareholding"], v["url"]
    for key, who in (("FIIs", "Foreign institutions"), ("DIIs", "Domestic institutions")):
        heads, vals = _series(sh, key)
        if not vals:
            continue
        last_p, last, raw = _at(heads, vals, -1)
        first_p, first, fraw = _at(heads, vals, 0)
        if last is None:
            continue
        if first is not None and len(vals) > 1:
            delta = last - first
            p.add("flow",
                  f"{who} hold {raw} as of {last_p or 'the latest quarter'}, "
                  f"against {fraw} in {first_p or 'the earliest quarter shown'} "
                  f"({delta:+.2f} percentage points).", _txt(raw),
                  source=_screener(last_p), url=url,
                  side_hint=_band(delta, 0.25, -0.25))
        else:
            p.add("flow", f"{who} hold {raw} as of "
                          f"{last_p or 'the latest quarter'}.", _txt(raw),
                  source=_screener(last_p), url=url)
    mf = _dget(v["analysis"], "money_flow")
    if isinstance(mf, dict) and _txt(mf.get("label")):
        label = _txt(mf.get("label"))
        chg = _num(mf.get("change"))
        side = ("bull" if "positive" in label.lower()
                else "bear" if "negative" in label.lower() else "neutral")
        p.add("flow",
              f"Institutional money flow reads {label}"
              + (f" (holding change {chg:+.2f} percentage points)." if chg is not None else "."),
              label, source=_SIGNAL_SRC, url=url, side_hint=side)


def _ev_dcf(p: _Pack, v: dict) -> None:
    d = _dget(v["analysis"], "dcf")
    if not isinstance(d, dict) or d.get("ok") is False:
        return
    intr = _num(d.get("intrinsic_per_share"))
    price = _num(d.get("current_price"))
    mos = _num(d.get("margin_of_safety"))
    if intr is not None and price is not None:
        p.add("dcf",
              f"A discounted cash flow puts intrinsic value at Rs {intr:g} per share "
              f"against a market price of Rs {price:g}.", f"Rs {intr:g}",
              source="scanX DCF", url=v["url"],
              side_hint="bull" if intr > price else "bear")
    if mos is not None:
        p.add("dcf",
              f"That leaves a margin of safety of {_pct(mos)}.", _pct(mos),
              source="scanX DCF", url=v["url"], side_hint=_band(mos, 20.0, 0.0))
    implied = _num(_dget(d, "reverse", "implied_growth"))
    assumed = _num(_dget(d, "inputs", "growth"))
    if implied is not None:
        if assumed is not None:
            side = "bear" if implied > assumed else "bull"
            fact = (f"A reverse DCF says today's price already implies "
                    f"{_pct(implied)} earnings growth, against the {_pct(assumed)} "
                    f"the base case assumes.")
        else:
            side = "neutral"
            fact = (f"A reverse DCF says today's price already implies "
                    f"{_pct(implied)} earnings growth.")
        p.add("dcf", fact, _pct(implied), source="scanX reverse DCF",
              url=v["url"], side_hint=side)


def _ev_technical(p: _Pack, v: dict) -> None:
    t = _dget(v["prices"], "technical")
    if not isinstance(t, dict):
        return
    pos = _num(t.get("pos_52w"))
    if pos is not None:
        p.add("technical",
              f"The price sits {pos:.0f}% of the way up its 52-week range.",
              f"{pos:.0f}%", source=_PRICES_SRC, side_hint=_band(pos, 70.0, 30.0))
    dist = _num(t.get("dist_52w_high"))
    if dist is not None:
        p.add("technical", f"It is {dist:.1f}% away from its 52-week high.",
              _pct(dist), source=_PRICES_SRC, side_hint=_band(dist, -5.0, -25.0))
    if "above_50dma" in t or "above_200dma" in t:
        a50, a200 = bool(t.get("above_50dma")), bool(t.get("above_200dma"))
        gc = bool(t.get("golden_cross"))
        p.add("technical",
              f"The price is {'above' if a50 else 'below'} its 50-day moving average "
              f"and {'above' if a200 else 'below'} its 200-day moving average"
              + (", with the 50-day above the 200-day." if gc else "."),
              f"{'50DMA+' if a50 else '50DMA-'} {'200DMA+' if a200 else '200DMA-'}",
              source=_PRICES_SRC,
              side_hint="bull" if (a50 and a200) else "bear" if not (a50 or a200) else "neutral")
    rs = _num(t.get("rs_rating"))
    if rs is not None:
        p.add("technical",
              f"Relative-strength rating is {rs:g} against {_txt(t.get('benchmark')) or 'the market'}.",
              f"{rs:g}", source=_PRICES_SRC, side_hint=_band(rs, 70.0, 40.0))
    r12 = _num(t.get("ret_12m"))
    if r12 is not None:
        p.add("technical", f"Twelve-month price return is {_pct(r12)}.", _pct(r12),
              source=_PRICES_SRC, side_hint=_band(r12, 10.0, 0.0))


def _ev_risk(p: _Pack, v: dict) -> None:
    r = _dget(v["prices"], "risk")
    if not isinstance(r, dict):
        return
    mdd = _num(r.get("max_drawdown"))
    if mdd is not None:
        p.add("risk", f"The worst peak-to-trough drawdown on record is {_pct(mdd)}.",
              _pct(mdd), source=_PRICES_SRC, side_hint=_band(mdd, -30.0, -50.0))
    vol = _num(r.get("ann_vol"))
    if vol is not None:
        p.add("risk", f"Annualised volatility is {_pct(vol)}.", _pct(vol),
              source=_PRICES_SRC, side_hint=_band(-vol, -30.0, -50.0))
    sh = _num(r.get("sharpe"))
    if sh is not None:
        p.add("risk", f"The Sharpe ratio is {sh:.2f}.", f"{sh:.2f}",
              source=_PRICES_SRC, side_hint=_band(sh, 1.0, 0.3))


def _ev_insight(p: _Pack, v: dict) -> None:
    gi = _dget(v["analysis"], "growth_insight")
    if isinstance(gi, dict) and _txt(gi.get("label")):
        for key in ("long", "recent"):
            note = _txt(gi.get(key))
            if note:
                p.add("insight", f"{_txt(gi.get('label'))} — {note}",
                      _txt(gi.get("label")), source=_SIGNAL_SRC, url=v["url"])
    cy = _dget(v["analysis"], "cyclical")
    if isinstance(cy, dict) and _txt(cy.get("label")):
        pos = [x for x in (cy.get("positive_quarters") or []) if _txt(x)]
        neg = [x for x in (cy.get("negative_quarters") or []) if _txt(x)]
        bits = []
        if pos:
            bits.append("seasonally strong in " + ", ".join(map(str, pos)))
        if neg:
            bits.append("seasonally weak in " + ", ".join(map(str, neg)))
        p.add("insight",
              f"The earnings profile reads {_txt(cy.get('label'))}"
              + (" — " + "; ".join(bits) + "." if bits else "."),
              _txt(cy.get("label")), source=_SIGNAL_SRC, url=v["url"])


def _ev_screener_notes(p: _Pack, v: dict) -> None:
    for text in v["pros"][:_MAX_NOTES]:
        p.add("screener_note", f"Screener flags a positive: {_txt(text)}",
              source=_screener(), url=v["url"], side_hint="bull")
    for text in v["cons"][:_MAX_NOTES]:
        p.add("screener_note", f"Screener flags a negative: {_txt(text)}",
              source=_screener(), url=v["url"], side_hint="bear")


def _ev_signal(p: _Pack, v: dict) -> None:
    sig = v["signal"]
    if not isinstance(sig, dict) or not sig:
        return
    comp = _num(sig.get("composite"))
    if comp is not None and _txt(sig.get("label")):
        p.add("signal",
              f"The rules-based screen scores {comp:g} out of 100 "
              f"({_txt(sig.get('label'))}, {_txt(sig.get('confidence')) or 'no'} confidence).",
              f"{comp:g}/100", source=_SIGNAL_SRC,
              side_hint=_band(comp, 70.0, 45.0))
    for key, side in (("reasons_pos", "bull"), ("reasons_neg", "bear")):
        for reason in (sig.get(key) or [])[:_MAX_REASONS]:
            if _txt(reason):
                p.add("signal", f"Screen reason ({side} side): {_txt(reason)}",
                      source=_SIGNAL_SRC, side_hint=side)
    for flag in (_dget(sig, "bias_check", "flags") or [])[:_MAX_FLAGS]:
        if not isinstance(flag, dict):
            continue
        note = _txt(flag.get("note"))
        if not note:
            continue
        p.add("signal",
              f"Bias check — {_txt(flag.get('title')) or 'flag'}: {note}",
              _txt(flag.get("level")), source="Insider-Bias checklist",
              side_hint=_FLAG_SIDE.get(_txt(flag.get("level")).lower(), "neutral"))


def _ev_sector(p: _Pack, sector) -> None:
    s = sector if isinstance(sector, dict) else None
    if not s:
        return
    name = _txt(s.get("sector") or s.get("name"))
    signal = _txt(s.get("signal")).upper()
    score = _num(s.get("score"))
    if not (name or signal):
        return
    side = "bull" if "TAILWIND" in signal else "bear" if "HEADWIND" in signal else "neutral"
    bits = [f"{name or 'The sector'} reads {signal or 'NEUTRAL'}"]
    if score is not None:
        bits.append(f"tailwind score {score:+.2f}")
    p.add("sector", " — ".join(bits) + ".", signal or (f"{score:+.2f}" if score is not None else ""),
          source=_SECTOR_SRC, side_hint=side)
    for key, label, good, bad in (("median_profit_var", "median profit growth", 10.0, 0.0),
                                  ("median_sales_var", "median sales growth", 10.0, 0.0),
                                  ("median_roce", "median ROCE", 15.0, 10.0)):
        x = _num(s.get(key))
        if x is not None:
            p.add("sector", f"Sector {label} is {_pct(x)}.", _pct(x),
                  source=_SECTOR_SRC, side_hint=_band(x, good, bad))


def _ev_filings(p: _Pack, filings) -> None:
    """Grounded facts the COMPANY itself published — quote and url travel along."""
    f = filings if isinstance(filings, dict) else {}
    analysis = f.get("analysis") if isinstance(f.get("analysis"), dict) else f
    if not isinstance(analysis, dict):
        return
    themes = analysis.get("themes")
    added = 0
    if isinstance(themes, dict):
        for theme in da.THEMES:
            for fact in (themes.get(theme) or [])[:_MAX_FACTS_PER_THEME]:
                if not isinstance(fact, dict) or added >= _MAX_FILING_FACTS:
                    continue
                claim, quote = _txt(fact.get("claim")), _txt(fact.get("quote"))
                if not claim or not quote:
                    continue
                kind = _txt(fact.get("doc_kind"))
                label = _KIND_LABEL.get(kind, kind.replace("_", " ") or "company filing")
                date = _txt(fact.get("doc_date"))
                p.add("filing", claim, source=f"{label} - {date}" if date else label,
                      url=_txt(fact.get("url")),
                      side_hint=_THEME_SIDE.get(theme, "neutral"), quote=quote)
                added += 1
    for fact in (analysis.get("management_commitments") or []):
        if not isinstance(fact, dict) or added >= _MAX_FILING_FACTS:
            continue
        claim, quote = _txt(fact.get("claim")), _txt(fact.get("quote"))
        if not claim or not quote:
            continue
        kind = _txt(fact.get("doc_kind"))
        label = _KIND_LABEL.get(kind, kind.replace("_", " ") or "company filing")
        date = _txt(fact.get("doc_date"))
        tf = _txt(fact.get("timeframe"))
        p.add("commitment",
              f"Management commitment{f' ({tf})' if tf else ''}: {claim}",
              tf, source=f"{label} - {date}" if date else label,
              url=_txt(fact.get("url")), quote=quote)
        added += 1


_FAMILY_BUILDERS = (
    _ev_valuation, _ev_peers, _ev_profitability, _ev_growth, _ev_margin,
    _ev_cash, _ev_balance, _ev_capex, _ev_ownership, _ev_flow, _ev_dcf,
    _ev_technical, _ev_risk, _ev_insight, _ev_screener_notes, _ev_signal,
)


def evidence_pack(bundle: dict, *, filings: dict | None = None,
                  sector: dict | None = None) -> list[dict]:
    """Bundle (+ filings, + sector row) -> the numbered evidence both sides share.

    PURE and deterministic: the same input always produces the same items in the
    same order with the same ids, which is what makes `_meta` counts and the
    blind-spot list reproducible across bakes. Only families actually present in
    the input are emitted — a missing section produces no item rather than a
    zero, because a fabricated zero is a lie an agent would happily cite.
    """
    p = _Pack()
    try:
        v = _view(bundle)
    except Exception as e:  # noqa: BLE001
        log.warning("evidence_pack: unreadable bundle: %s", e)
        return []
    for build in _FAMILY_BUILDERS:
        try:
            build(p, v)
        except Exception as e:  # noqa: BLE001
            log.warning("evidence_pack: %s degraded: %s", build.__name__, e)
    for build, arg in ((_ev_sector, sector), (_ev_filings, filings)):
        try:
            build(p, arg)
        except Exception as e:  # noqa: BLE001
            log.warning("evidence_pack: %s degraded: %s", build.__name__, e)
    return p.items


# ------------------------------------------------------------- pure: grounding
def _ids_in(text: str) -> list[str]:
    """Every evidence id referenced in `text`, in order of first appearance.

    Handles ``[E7]``, ``[E7, E9]`` and ``[e7]``; ignores any other bracket text.
    """
    out: list[str] = []
    for block in _CITE_BLOCK.findall(text or ""):
        for num in _CITE_ID.findall(block):
            cid = "E" + str(int(num))
            if cid not in out:
                out.append(cid)
    return out


def _split_claims(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _ungrounded_quotes(text: str, corpus: str) -> list[str]:
    """Verbatim-looking quotations in `text` that are NOT in the evidence corpus.

    Reuses `docanalysis.is_grounded` — the same check that keeps baked filing
    facts honest — so an agent cannot dress an invention up as a quotation.
    """
    bad = []
    for span in _QUOTED.findall(text or ""):
        if not da.is_grounded(span, corpus):
            bad.append(da.normalise(span)[:200])
    return bad


def _clean_turn(text: str, valid: set, corpus: str) -> tuple[str, list, list, int]:
    """Strip claims citing ids we never issued.

    Returns (kept_text, valid_cites, invalid_ids, claims_stripped). A sentence
    referencing ANY unknown id is removed whole — a claim half-supported by an
    invented source is not evidence. Sentences carrying no id at all survive
    (they are connectives and concessions), but a turn that ends up with no valid
    citation is dropped by the caller.
    """
    kept: list[str] = []
    cites: list[str] = []
    invalid: list[str] = []
    stripped = 0
    for claim in _split_claims(text):
        ids = _ids_in(claim)
        bad = [i for i in ids if i not in valid]
        if bad:
            stripped += 1
            invalid.extend(bad)
            log.debug("stripped claim citing %s: %.70s", ",".join(bad), claim)
            continue
        kept.append(claim)
        for i in ids:
            if i not in cites:
                cites.append(i)
    out = " ".join(kept).strip()
    # Arithmetic the model got wrong survives the citation check, because the
    # ids are real -- only the direction between them is false. Dropped here for
    # the same reason an invented citation is: a published research artefact
    # must not assert that 4% is higher than 37.5%.
    out, flipped = strip_inverted_comparisons(out)
    stripped += flipped
    if not out.strip():
        cites = []
    if not cites:
        out = ""
    return out, cites, invalid, stripped


# --------------------------------------------- pure: arithmetic the model got wrong
# Words that assert a DIRECTION between two numbers in the same sentence.
_CMP_HIGHER = ("higher than", "above", "exceeds", "exceeding", "greater than",
               "outperforms", "ahead of", "more than")
_CMP_LOWER = ("lower than", "below", "less than", "trails", "behind",
              "underperforms", "short of", "weaker than")
_CMP_RE = re.compile("|".join(re.escape(w) for w in _CMP_HIGHER + _CMP_LOWER), re.I)
_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _comparison_is_inverted(sentence: str) -> bool:
    """Does this sentence claim A > B when the numbers say otherwise? PURE.

    A small local model gets citations right and arithmetic wrong. Observed on
    the very first local bake: "profit growth ... stands at 4%, which is HIGHER
    than the sector median of 37.5%". The citation was valid -- the pack really
    does say 37.5% -- so the grounding check passed it cleanly. The claim is
    still false, and on 5,000 companies that is thousands of inverted statements
    published as research.

    Deliberately conservative. It fires only when the sentence carries EXACTLY
    two percentages and one unambiguous direction word, because a false positive
    deletes a true sentence and that is the worse error.
    """
    text = str(sentence or "")
    hit = _CMP_RE.search(text)
    if not hit:
        return False
    nums = _PCT_RE.findall(text)
    if len(nums) != 2:
        return False
    try:
        first, second = float(nums[0]), float(nums[1])
    except ValueError:
        return False
    if first == second:
        return False
    word = hit.group(0).lower()
    claims_higher = any(word.startswith(w) for w in _CMP_HIGHER)
    return claims_higher != (first > second)


def strip_inverted_comparisons(text: str) -> tuple[str, int]:
    """Drop sentences whose numeric comparison runs backwards. PURE.

    Returns (kept_text, dropped_count). Consistent with how an invented citation
    is handled: the sentence is deleted before publication rather than published
    with a caveat, because a research artefact that states 4% > 37.5% is worse
    than one that says less.
    """
    body = str(text or "").strip()
    if not body:
        return "", 0
    sentences = _SENT_RE.split(body)
    kept = [s for s in sentences if not _comparison_is_inverted(s)]
    dropped = len(sentences) - len(kept)
    return (" ".join(kept).strip(), dropped)


# ------------------------------------------------------- pure: focusing the pack
def focus_pack(evidence: list, *, limit: int = 26, floor: int = 8) -> list:
    """Narrow the pack to the sharpest DISPUTE plus the heavyweight items. PURE.

    The full pack is ~73 items and every turn ships all of them, which on a
    local 7B model is most of the wall-clock cost: prompt tokens dominate when
    the generation is only ~700 tokens a turn. Sending one topic's
    neighbourhood instead measured at ~11% of the pack across 5,488 companies.

    It is not only cheaper, it is sharper. Round 2 previously handed each side
    the opponent's text and said "answer those points", so the model chose what
    to engage with and reliably chose the weakest thing said. Seeding from a
    `contradicts` edge puts both sides on the same disputed point.

    Narrows the PACK, not just the prompt, so the published evidence, the
    grounding check and the scorecard all describe the same set — a pack that
    listed items no turn could see would make the coverage number a lie.

    Falls back to the full pack when the graph finds too little to focus on;
    a thin company should still get whatever argument it can support.
    """
    items = [e for e in (evidence or []) if isinstance(e, dict) and e.get("id")]
    if len(items) <= floor:
        return list(evidence or [])
    try:
        from .evidence_graph import build_graph, clashes, subgraph
        graph = build_graph(items)
        keep: list = []
        for clash in clashes(graph, limit=2):
            keep.extend(subgraph(graph, [clash["src"], clash["dst"]],
                                 hops=1, limit=limit))
    except Exception as e:  # noqa: BLE001 - focusing is an optimisation, never a blocker
        log.debug("focus_pack degraded: %s", type(e).__name__)
        return list(items)

    # Always carry the heaviest evidence, whatever the clash was about. A debate
    # that only ever saw one dispute would miss a debt-free balance sheet or a
    # pledge sitting in another family entirely.
    heavy = [e["id"] for e in sorted(items, key=lambda x: -(x.get("weight") or 0))[:floor]]
    wanted = set(keep) | set(heavy)
    order = {e["id"]: i for i, e in enumerate(items)}
    out = [e for e in items if e["id"] in wanted]
    out.sort(key=lambda e: order.get(e["id"], 10**6))     # keep the pack's own order
    return out if len(out) >= floor else list(items)


# ---------------------------------------------------------------- pure: prompt
def _render_evidence(evidence: list) -> str:
    lines = []
    for it in evidence:
        head = f"[{it.get('id')}] ({it.get('side_hint', 'neutral')}) {it.get('fact')}"
        val = _txt(it.get("value"))
        if val:
            head += f" | value: {val}"
        src = _txt(it.get("source"))
        if src:
            head += f" | source: {src}"
        quote = _txt(it.get("quote"))
        if quote:
            head += f'\n      company said, verbatim: "{quote}"'
        lines.append(head)
    return "\n".join(lines)


_RULES = (
    "RULES — this is a published research artefact, not a chat:\n"
    "1. Argue ONLY from the numbered evidence above. Nothing else is admissible: "
    "not your background knowledge of the company, not the sector, not the news.\n"
    "2. Cite the evidence id in square brackets inside EVERY claim, like "
    "\"cash conversion is weak [E12]\". A sentence with no [id] carries no weight "
    "and a sentence citing an id that is not listed above is deleted before "
    "publication.\n"
    "3. Never invent a number, a date, a percentage or a quotation. If it is not "
    "in the evidence, you cannot say it.\n"
    "4. No price target, no fair-value number of your own, no buy/sell/hold "
    "recommendation, no position sizing, no timeframe advice.\n"
    "5. Concede explicitly when the evidence genuinely favours the other side — "
    "write \"conceded:\" and the id. A case that cannot lose a point is worthless.\n"
    "6. Plain prose. 4 to 7 sentences. No headings, no bullet points, no markdown, "
    "no preamble such as \"As the bull...\"."
)

_ROLE = {
    "bull": ("You are the BULL. Build the strongest honest case that this "
             "business is being under-appreciated on the evidence supplied."),
    "bear": ("You are the BEAR. Build the strongest honest case that this "
             "business carries risks or a valuation the evidence does not "
             "support."),
}
_OPPONENT = {"bull": "bear", "bear": "bull"}


def _build_prompt(side: str, company: str, evidence: list, rnd: int,
                  rounds: int, opponent_text: str) -> str:
    header = (f"{_ROLE[side]}\n\nCOMPANY: {company}\n"
              f"ROUND {rnd} of {rounds}.\n\n"
              f"EVIDENCE — the complete and only admissible record:\n"
              f"{_render_evidence(evidence)}\n\n")
    if rnd <= 1 or not _txt(opponent_text):
        task = ("TASK: open your case. Pick the evidence that carries the most "
                "weight and say what it implies. Do not answer arguments that "
                "have not been made yet.\n\n")
    else:
        task = (f"THE {_OPPONENT[side].upper()} JUST ARGUED, VERBATIM:\n"
                f'"""\n{_txt(opponent_text)}\n"""\n\n'
                f"TASK: REBUT that turn. Take the {_OPPONENT[side]}'s named points "
                f"and the specific ids it cited and answer them one by one — show "
                f"where the evidence does not support the reading, or where other "
                f"evidence outweighs it. Do NOT restate your own opening case, and "
                f"do not raise a fresh topic unless it directly defeats a point "
                f"just made.\n\n")
    return header + task + _RULES


# ------------------------------------------------------------- pure: scorecard
def scorecard(evidence: list, rounds: list) -> dict:
    """Deterministic, model-free audit of who used what.

    Counts citations by side, weights them by evidence family, and lists the
    evidence NEITHER side touched. The blind-spot list is the part a model cannot
    fake: it is computed from the ids that never appear in any surviving turn.
    """
    ev = [e for e in (evidence or []) if isinstance(e, dict) and e.get("id")]
    by_id = {e["id"]: e for e in ev}
    tally = {s: {"turns": 0, "cites": 0, "unique": [], "weight": 0,
                 "high_weight": 0} for s in SIDES}

    for turn in (rounds or []):
        if not isinstance(turn, dict):
            continue
        side = turn.get("side")
        if side not in tally:
            continue
        tally[side]["turns"] += 1
        for cid in (turn.get("cites") or []):
            item = by_id.get(cid)
            if item is None:
                continue
            tally[side]["cites"] += 1
            if cid not in tally[side]["unique"]:
                tally[side]["unique"].append(cid)
                w = int(item.get("weight") or 1)
                tally[side]["weight"] += w
                if w >= HIGH_WEIGHT:
                    tally[side]["high_weight"] += 1

    used = {c for s in SIDES for c in tally[s]["unique"]}
    blind = [e["id"] for e in ev if e["id"] not in used]
    both = [e["id"] for e in ev
            if e["id"] in tally["bull"]["unique"] and e["id"] in tally["bear"]["unique"]]

    edge = "tie"
    for key in ("high_weight", "weight", "cites"):
        if tally["bull"][key] != tally["bear"][key]:
            edge = "bull" if tally["bull"][key] > tally["bear"][key] else "bear"
            break

    out = {s: {"turns": tally[s]["turns"], "cites": tally[s]["cites"],
               "unique_cites": len(tally[s]["unique"]),
               "unique_ids": list(tally[s]["unique"]),
               "weight": tally[s]["weight"],
               "high_weight_cites": tally[s]["high_weight"]} for s in SIDES}
    out["evidence_total"] = len(ev)
    out["evidence_used"] = len(used)
    out["coverage_pct"] = round(100.0 * len(used) / len(ev), 1) if ev else 0.0
    out["evidence_edge"] = edge
    out["contested"] = both
    out["blind_spots"] = blind
    out["blind_spot_facts"] = [
        {"id": by_id[i]["id"], "fact": by_id[i]["fact"],
         "side_hint": by_id[i].get("side_hint", "neutral"),
         "family": by_id[i].get("family", "")} for i in blind
    ]
    out["note"] = ("Computed arithmetically from the citations that survived "
                   "grounding — no model input.")
    return out


# ----------------------------------------------------------------- model call
def _default_complete(prompt: str, **kw):
    """Lazy so importing this module never pulls the provider layer in."""
    from ..llm import complete as _complete    # noqa: PLC0415
    return _complete(prompt, **kw)


def _resp(raw) -> tuple[str, bool, str, str, str]:
    """Any complete() return -> (text, ok, error, model, provider). Tolerant."""
    if raw is None:
        return "", False, "model returned nothing", "", ""
    if isinstance(raw, str):
        return raw, bool(raw.strip()), "" if raw.strip() else "empty response", "", ""
    if isinstance(raw, dict):
        text = _txt(raw.get("text"))
        return (text, bool(raw.get("ok", bool(text))), _txt(raw.get("error")),
                _txt(raw.get("model")), _txt(raw.get("provider")))
    text = _txt(getattr(raw, "text", ""))
    return (text, bool(getattr(raw, "ok", bool(text))),
            _txt(getattr(raw, "error", "")), _txt(getattr(raw, "model", "")),
            _txt(getattr(raw, "provider", "")))


def _ask(fn: Callable, prompt: str, provider) -> tuple:
    """One completion. Degrades through narrower signatures; never raises."""
    attempts = (
        dict(provider=provider, json_mode=False, temperature=_TEMPERATURE,
             max_tokens=_MAX_TOKENS),
        dict(provider=provider),
        {},
    )
    last = "no completion callable answered"
    for kw in attempts:
        try:
            return _resp(fn(prompt, **kw))
        except TypeError as e:  # noqa: PERF203 - signature probe, cheap
            last = f"TypeError: {str(e)[:160]}"
            continue
        except Exception as e:  # noqa: BLE001
            return "", False, f"{type(e).__name__}: {str(e)[:200]}", "", ""
    return "", False, last, "", ""


# -------------------------------------------------------------------- pipeline
def _shell(code: str, name: str, when: str, evidence: list, rounds: list,
           meta: dict, error: str = "") -> dict:
    out = {"code": code, "name": name, "generated_at": when,
           "evidence": evidence, "rounds": rounds,
           "scorecard": scorecard(evidence, rounds), "_meta": meta}
    if error:
        out["error"] = error[:400]
        out["_meta"]["note"] = error[:400]
    return out


def run_debate(bundle: dict, *, filings: dict | None = None,
               sector: dict | None = None, rounds: int = 3,
               complete: Optional[Callable] = None,
               provider: Optional[str] = None, focus: bool = False) -> dict:
    """Run the BULL/BEAR debate over the shared evidence pack. NEVER raises.

    Round 1 is two openings. Rounds 2..N are rebuttals: each side receives the
    opponent's previous turn verbatim and is told to answer THOSE points. Every
    turn is then ground against the pack — claims citing an id we never issued
    are stripped, a turn left with no valid citation is dropped — and the counts
    land in `_meta`. The `scorecard` is computed afterwards without the model.

    `complete(prompt, *, provider=None, json_mode=False, temperature=..., max_tokens=...)`
    defaults to `earnings_intel.llm.complete`; inject a fake in tests so nothing
    touches a network or a key. Any failure (no credentials, dead provider, empty
    transcript) returns {"error": ...} on an otherwise contract-shaped dict.
    """
    try:
        v = _view(bundle)
    except Exception as e:  # noqa: BLE001
        log.warning("run_debate: unreadable bundle: %s", e)
        v = {"code": "", "name": "", "generated_at": ""}

    code, name = v.get("code", ""), v.get("name", "")
    when = v.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_rounds = max(1, min(int(rounds or 1), _MAX_ROUNDS)) if isinstance(rounds, (int, float)) else 3

    meta = {"model": "", "provider": _txt(provider), "rounds": n_rounds,
            "turns_dropped": 0, "cites_invalid": 0, "cites_invalid_ids": [],
            "claims_stripped": 0, "quotes_unverified": 0, "turns_failed": 0,
            "evidence_count": 0,
            "note": ("Every claim is checked against the evidence pack; claims "
                     "citing an id we never issued are removed before publication.")}

    try:
        evidence = evidence_pack(bundle, filings=filings, sector=sector)
        if focus:
            # Narrow to the sharpest dispute plus the heavyweight items. Off by
            # default so the cloud bake is unchanged; on for the local run,
            # where prompt tokens are the wall-clock cost.
            evidence = focus_pack(evidence)
    except Exception as e:  # noqa: BLE001
        log.warning("run_debate: evidence pack failed: %s", e)
        evidence = []
    meta["evidence_count"] = len(evidence)
    if not evidence:
        return _shell(code, name, when, [], [], meta,
                      "no evidence could be assembled from this bundle — "
                      "nothing to debate")

    fn = complete or _default_complete
    company = f"{name} ({code})".strip() if name or code else "the company"
    corpus = "\n".join(f"{e.get('fact')} {e.get('value')} {e.get('quote', '')}"
                       for e in evidence)
    valid = {e["id"] for e in evidence}

    turns: list[dict] = []
    last_text = {"bull": "", "bear": ""}
    problems: list[str] = []

    try:
        for rnd in range(1, n_rounds + 1):
            for side in SIDES:
                prompt = _build_prompt(side, company, evidence, rnd, n_rounds,
                                       last_text[_OPPONENT[side]])
                text, ok, err, model, prov = _ask(fn, prompt, provider)
                if model and not meta["model"]:
                    meta["model"] = model
                if prov:
                    meta["provider"] = prov
                if not ok or not _txt(text):
                    meta["turns_failed"] += 1
                    problems.append(f"r{rnd} {side}: {err or 'empty response'}")
                    log.warning("debate turn failed (r%s %s): %s", rnd, side, err)
                    continue

                raw = _txt(text)[:_MAX_TURN_CHARS]
                last_text[side] = raw            # opponent always sees what was said
                kept, cites, invalid, stripped = _clean_turn(raw, valid, corpus)
                meta["claims_stripped"] += stripped
                meta["cites_invalid"] += len(invalid)
                for cid in invalid:
                    if cid not in meta["cites_invalid_ids"]:
                        meta["cites_invalid_ids"].append(cid)
                if not cites or not kept:
                    meta["turns_dropped"] += 1
                    log.warning("debate turn dropped (r%s %s): no valid citation",
                                rnd, side)
                    continue

                unverified = _ungrounded_quotes(kept, corpus)
                meta["quotes_unverified"] += len(unverified)
                turn = {"round": rnd, "side": side, "text": kept, "cites": cites}
                if unverified:
                    turn["quotes_unverified"] = unverified
                turn["conceded"] = "conced" in kept.lower()
                turns.append(turn)
                last_text[side] = kept
    except Exception as e:  # noqa: BLE001
        log.warning("run_debate: aborted mid-debate: %s", e)
        problems.append(f"{type(e).__name__}: {str(e)[:160]}")

    meta["cites_invalid_ids"] = sorted(meta["cites_invalid_ids"],
                                       key=lambda s: int(s[1:]))
    if not turns:
        return _shell(code, name, when, evidence, [], meta,
                      "; ".join(problems)[:300] or "no debate turn survived grounding")
    return _shell(code, name, when, evidence, turns, meta)
