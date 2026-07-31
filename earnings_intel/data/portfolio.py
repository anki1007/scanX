"""
Rules-based buy / hold / exit book — which stocks, on what signal, and when out.

The agent comparison answers "which ALGORITHM", which is an academic question.
This answers the one an investor actually has: what do I hold, why did it get in,
what would take it out, and what replaces it.

Deterministic and offline. Every entry and every exit is a named rule over
numbers already baked into the bundles, so the book can be audited line by line
and reproduced exactly — no model, no key, no randomness.

    from earnings_intel.data.portfolio import rebalance
    rebalance(previous_book, candidates, size=15)
    -> {holdings, exits, entries, watchlist}

ENTRY needs every gate to pass. EXIT needs only one to fire — an investor should
need more conviction to buy than to sell, and the asymmetry is the whole point of
having written rules rather than judgement in the moment.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["entry_signal", "exit_signal", "rebalance", "DEFAULTS"]

DEFAULTS = {
    "size": 15,                 # target number of holdings
    "entry_score": 6.0,         # analysis score out of 10 to qualify
    "exit_score": 4.5,          # drop below this and it leaves
    "stop_pct": -20.0,          # hard stop from entry price
    "take_profit_pct": None,    # None = let winners run; the data supports it
    "max_pe_vs_sector": 2.5,    # richer than this multiple of the sector = out
    "min_mcap_cr": 500.0,       # below this, the exit itself may not be fillable
    "cooloff_days": 30,         # a name that exited cannot be re-bought this soon
}


def _num(value: Any) -> float | None:
    """Indian-formatted number or None. PURE."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and math.isfinite(value) else None
    text = str(value).strip()
    if not text:
        return None
    neg = text.startswith("(") and text.endswith(")")
    for ch in "₹%,()":
        text = text.replace(ch, "")
    text = text.replace("Cr.", "").replace("Cr", "").replace("−", "-").strip()
    try:
        out = float(text.split()[0]) if text.split() else None
    except (ValueError, IndexError):
        return None
    if out is None:
        return None
    return -out if neg and out > 0 else out


def _plus_days(day: str, days: int) -> str:
    """YYYY-MM-DD plus N days, or "" when the date is unusable. PURE."""
    if not day or not days:
        return ""
    try:
        import datetime
        d = datetime.date.fromisoformat(str(day)[:10])
        return (d + datetime.timedelta(days=int(days))).isoformat()
    except (ValueError, TypeError):
        return ""


def _dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def entry_signal(row: Mapping, *, rules: Mapping | None = None) -> dict:
    """Does this company qualify to BUY? {ok, reasons[], blockers[], score}. PURE.

    Every gate must pass. The reasons are kept even when a blocker fires, so the
    watchlist can show "would qualify except for X" rather than a bare no.
    """
    r = {**DEFAULTS, **(rules or {})}
    score = _num(row.get("score"))
    reasons: list[str] = []
    blockers: list[str] = []

    if score is None:
        blockers.append("no analysis score")
    elif score < r["entry_score"]:
        blockers.append(f"score {score:g} below {r['entry_score']:g}")
    else:
        reasons.append(f"analysis score {score:g}/10")

    sector_sig = str(row.get("sector_signal") or "").upper()
    if sector_sig == "HEADWIND":
        blockers.append(f"{row.get('sector') or 'sector'} is in a headwind")
    elif sector_sig == "TAILWIND":
        reasons.append(f"{row.get('sector') or 'sector'} tailwind")

    mcap = _num(row.get("mcap"))
    if mcap is not None and mcap < r["min_mcap_cr"]:
        blockers.append(f"market cap {mcap:,.0f} Cr below {r['min_mcap_cr']:,.0f} Cr")

    pe, pe_sector = _num(row.get("pe")), _num(row.get("pe_sector"))
    if pe is not None and pe <= 0:
        blockers.append("no meaningful P/E (loss-making)")
    elif pe and pe_sector and pe_sector > 0 and r["max_pe_vs_sector"]:
        rel = pe / pe_sector
        if rel > r["max_pe_vs_sector"]:
            blockers.append(f"P/E {pe:g} is {rel:.1f}x the sector {pe_sector:g}")
        elif rel < 1:
            reasons.append(f"P/E {pe:g} under the sector {pe_sector:g}")

    ocf = _num(_dig(row, "health", "ocf_np", "value"))
    if ocf is not None and ocf < 0:
        blockers.append("operating cash flow is negative")
    elif ocf is not None and ocf >= 1:
        reasons.append(f"profit backed by cash (OCF/PAT {ocf:g}x)")

    return {"ok": not blockers and score is not None,
            "score": score, "reasons": reasons, "blockers": blockers}


def exit_signal(holding: Mapping, row: Mapping | None, *,
                rules: Mapping | None = None) -> dict:
    """Should this holding be SOLD? {ok, triggers[]}. PURE.

    Any single trigger is enough. A holding whose company has vanished from the
    candidate set entirely also leaves — silence is not a reason to keep owning
    something.
    """
    r = {**DEFAULTS, **(rules or {})}
    triggers: list[str] = []

    entry_price = _num(holding.get("entry_price"))
    last = _num((row or {}).get("ltp")) or _num(holding.get("last_price"))
    change = None
    if entry_price and entry_price > 0 and last:
        change = (last - entry_price) / entry_price * 100.0
        if r["stop_pct"] is not None and change <= r["stop_pct"]:
            triggers.append(f"stop hit: {change:+.1f}% from entry")
        if r["take_profit_pct"] is not None and change >= r["take_profit_pct"]:
            triggers.append(f"target hit: {change:+.1f}% from entry")

    if row is None:
        triggers.append("no longer in the screened universe")
    else:
        score = _num(row.get("score"))
        if score is None:
            triggers.append("analysis score no longer available")
        elif score < r["exit_score"]:
            triggers.append(f"score fell to {score:g} (exit below {r['exit_score']:g})")
        if str(row.get("sector_signal") or "").upper() == "HEADWIND":
            triggers.append(f"{row.get('sector') or 'sector'} turned to a headwind")
        pe, pe_sector = _num(row.get("pe")), _num(row.get("pe_sector"))
        if pe and pe_sector and pe_sector > 0 and r["max_pe_vs_sector"]:
            rel = pe / pe_sector
            if rel > r["max_pe_vs_sector"]:
                triggers.append(f"valuation stretched to {rel:.1f}x the sector")

    return {"ok": bool(triggers), "triggers": triggers,
            "change_pct": round(change, 2) if change is not None else None,
            "last_price": last}


def rebalance(previous: Sequence[Mapping] | None, candidates: Sequence[Mapping] | None,
              *, size: int | None = None, rules: Mapping | None = None,
              today: str = "") -> dict:
    """One rebalance pass. PURE — same inputs, same book.

    Order matters and is deliberate: EXITS ARE PROCESSED FIRST, so the seats they
    free are available to entries in the same pass. Doing entries first would cap
    the book at its target size while holding names that are already on their way
    out.
    """
    r = {**DEFAULTS, **(rules or {})}
    target = int(size or r["size"])
    by_code = {str(c.get("code")): c for c in (candidates or []) if c.get("code")}

    holdings: list[dict] = []
    exits: list[dict] = []
    for h in (previous or []):
        code = str(h.get("code") or "")
        row = by_code.get(code)
        verdict = exit_signal(h, row, rules=r)
        if verdict["ok"]:
            exits.append({**dict(h), "exit_date": today,
                          "exit_price": verdict["last_price"],
                          "change_pct": verdict["change_pct"],
                          "cooloff_until": _plus_days(today, r["cooloff_days"]),
                          "triggers": verdict["triggers"]})
        else:
            holdings.append({**dict(h),
                             "last_price": verdict["last_price"],
                             "change_pct": verdict["change_pct"],
                             "name": (row or {}).get("name") or h.get("name"),
                             "score": _num((row or {}).get("score")),
                             "sector": (row or {}).get("sector") or h.get("sector")})

    held = {str(h.get("code")) for h in holdings}
    # A name that just EXITED must not be re-bought in the same pass. Without
    # this a stop-loss is not a stop at all: the first run sold AAA at -22% and
    # bought it straight back, paying costs twice and leaving the position
    # exactly where the rule said it should not be. The cool-off keeps it out
    # for a while afterwards too, so the book cannot oscillate on one name.
    barred = {str(e.get("code")) for e in exits}
    for e in (previous or []):
        until = str(e.get("cooloff_until") or "")
        if until and today and until > today:
            barred.add(str(e.get("code")))
    ranked = sorted(
        ({**c, "_sig": entry_signal(c, rules=r)} for c in (candidates or [])),
        key=lambda c: -(c["_sig"]["score"] or 0),
    )

    entries: list[dict] = []
    watchlist: list[dict] = []
    for cand in ranked:
        code = str(cand.get("code") or "")
        if not code or code in held or code in barred:
            continue
        sig = cand["_sig"]
        if not sig["ok"]:
            if len(watchlist) < 15 and sig["score"] is not None:
                watchlist.append({"code": code, "name": cand.get("name"),
                                  "score": sig["score"], "sector": cand.get("sector"),
                                  "blockers": sig["blockers"]})
            continue
        if len(holdings) >= target:
            if len(watchlist) < 15:
                watchlist.append({"code": code, "name": cand.get("name"),
                                  "score": sig["score"], "sector": cand.get("sector"),
                                  "blockers": ["book is full"]})
            continue
        row = {"code": code, "name": cand.get("name"), "sector": cand.get("sector"),
               "entry_date": today, "entry_price": _num(cand.get("ltp")),
               "last_price": _num(cand.get("ltp")), "change_pct": 0.0,
               "score": sig["score"], "reasons": sig["reasons"]}
        holdings.append(row)
        entries.append(row)
        held.add(code)

    holdings.sort(key=lambda h: -(h.get("score") or 0))
    return {"holdings": holdings, "exits": exits, "entries": entries,
            "watchlist": watchlist, "target_size": target,
            "rules": {k: r[k] for k in DEFAULTS}}
