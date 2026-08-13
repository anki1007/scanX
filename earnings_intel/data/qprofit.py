"""Four consecutive quarters of rising net profit.

    NP(latest)   > NP(latest-1)
    NP(latest-1) > NP(latest-2)
    NP(latest-2) > NP(latest-3)
    NP(latest)   > 0

A strictly increasing four-quarter run, with the newest quarter profitable.

One judgement worth stating, because the rule as written permits both and they
are not the same investment:

    -12  ->  -4  ->  3  ->  9     a TURNAROUND
      8  ->  11  -> 15  -> 21     COMPOUNDING

Both satisfy "strictly rising, latest positive". The screen returns both, as
specified, and marks which is which -- a turnaround off a loss-making base is a
different risk from four quarters of widening profit, and collapsing them into
one list would hide that.

Pure: no I/O, no clock, never raises.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

__all__ = ["QUARTERS_NEEDED", "streak", "evaluate"]

#: A four-quarter comparison needs four quarters.
QUARTERS_NEEDED = 4


def _num(value: Any) -> float | None:
    """Parse a statement cell. Returns None for anything unusable.

    Note a MISSING quarter must not silently become 0.0 -- that would turn a
    gap into an artificial trough and manufacture a rising streak out of it.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).replace(",", "").replace("₹", "").strip()
    if not text or text in {"-", "--", "NA", "n/a"}:
        return None
    m = re.fullmatch(r"\(?\s*(-?\d+\.?\d*)\s*\)?", text)
    if not m:
        m = re.search(r"-?\d+\.?\d*", text)
        if not m:
            return None
        out = float(m.group())
    else:
        out = float(m.group(1))
        # Accounting parentheses mean negative: "(4.2)" is -4.2.
        if text.startswith("(") and out > 0:
            out = -out
    return out if math.isfinite(out) else None


def streak(net_profit: Sequence[Any]) -> dict:
    """Does the last four quarters rise strictly, ending positive? PURE.

    Returns {"pass", "quarters", "reason", "turnaround", "growth_pct"}.
    `quarters` is oldest-first, so it reads the way the rule is written.
    """
    out: dict = {"pass": False, "quarters": [], "reason": "",
                 "turnaround": False, "growth_pct": None}

    values = [_num(x) for x in (net_profit or [])]
    # Only trailing values matter, but a None INSIDE the window is fatal --
    # dropping it would compare quarters that are not adjacent.
    window = values[-QUARTERS_NEEDED:]
    if len(window) < QUARTERS_NEEDED:
        out["reason"] = "fewer than four quarters reported"
        return out
    if any(v is None for v in window):
        out["reason"] = "a quarter in the window has no figure"
        return out

    out["quarters"] = [round(v, 2) for v in window]

    if not all(window[i] < window[i + 1] for i in range(QUARTERS_NEEDED - 1)):
        out["reason"] = "net profit did not rise in every quarter"
        return out
    if window[-1] <= 0:
        out["reason"] = "latest quarter is not profitable"
        return out

    out["pass"] = True
    # Rising and positive, but off a loss: a recovery, not four quarters of
    # compounding. Same rule, different animal.
    out["turnaround"] = any(v <= 0 for v in window[:-1])
    first = window[0]
    if abs(first) > 1e-9:
        out["growth_pct"] = round((window[-1] - first) / abs(first) * 100, 1)
    return out


def evaluate(bundle: Mapping | None) -> dict:
    """Run the screen over one baked bundle. PURE, never raises."""
    fundamental: Mapping = {}
    if isinstance(bundle, Mapping):
        inner = bundle.get("fundamental")
        fundamental = inner if isinstance(inner, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return streak([])

    quarters = fundamental.get("quarters")
    quarters = quarters if isinstance(quarters, Mapping) else {}
    rows = quarters.get("rows")
    rows = rows if isinstance(rows, Mapping) else {}

    series: Sequence[Any] = []
    for key, value in rows.items():
        if str(key).strip().lower().startswith("net profit"):
            if isinstance(value, Sequence) and not isinstance(value, str):
                series = value
            break

    result = streak(series)
    headers = quarters.get("headers")
    if result["quarters"] and isinstance(headers, Sequence) and not isinstance(headers, str):
        tail = [str(h) for h in headers][-QUARTERS_NEEDED:]
        if len(tail) == QUARTERS_NEEDED:
            result["periods"] = tail
    return result
