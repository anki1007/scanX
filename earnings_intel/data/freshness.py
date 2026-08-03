"""Has a company's story changed enough to be worth arguing again?

The debate board had two ways of deciding, and neither asked about the numbers:

    the headline names  re-argued on a CALENDAR - anything older than 7 days
    everything else     never re-argued at all, once baked

So a company could publish a quarter that halved its margin and its bull/bear
case would sit there unchanged, dated and confident, until someone noticed.
And the top names burned model calls every week restating an argument the
filings had not moved.

This makes staleness a property of the DATA. A fingerprint over the figures a
debate is actually built from - the filed quarters and the latest full year -
goes into the published file. When the fingerprint changes, the company is due
again; when it has not, it is not, however old the file is.

That gives the behaviour you want in both directions: a new quarterly result
re-opens the argument the same night, and a quiet quarter costs nothing.

Pure: no I/O, no clock. Never raises.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

__all__ = ["fingerprint", "is_stale", "FINGERPRINT_KEY"]

#: Where the fingerprint lives in a published debate file.
FINGERPRINT_KEY = "inputs"

#: Quarterly rows that would change what the two sides argue about.
_QUARTER_ROWS = ("Sales", "Net Profit", "EPS", "OPM")

#: Annual rows, for the full-year picture the debate also leans on.
_ANNUAL_ROWS = ("Sales", "Net Profit", "OPM")


def _rows(statement: Any, wanted: Sequence[str], last: int) -> list:
    """The tail of each wanted row, in a stable order."""
    if not isinstance(statement, Mapping):
        return []
    rows = statement.get("rows")
    if not isinstance(rows, Mapping):
        return []
    out = []
    for name in wanted:
        for label, values in rows.items():
            if str(label).strip().lower().startswith(name.lower()):
                if isinstance(values, Sequence) and not isinstance(values, str):
                    out.append([str(v) for v in list(values)[-last:]])
                break
        else:
            out.append([])
    return out


def fingerprint(bundle: Mapping | None) -> str:
    """A short, stable hash of the figures a debate is built from. PURE.

    Deliberately NOT a hash of the whole bundle. A bundle carries the live
    price and a bake timestamp, both of which move every single day; hashing
    those would mark every company stale every night and re-argue the entire
    universe daily, which is the expensive version of the bug this fixes.

    Returns "" when there is nothing to fingerprint, which callers must treat
    as "cannot tell" rather than as "changed".
    """
    fundamental: Mapping = {}
    if isinstance(bundle, Mapping):
        inner = bundle.get("fundamental")
        fundamental = inner if isinstance(inner, Mapping) else bundle
    if not isinstance(fundamental, Mapping):
        return ""

    quarters = fundamental.get("quarters")
    profit_loss = fundamental.get("profit_loss")

    payload: list = []
    if isinstance(quarters, Mapping):
        headers = quarters.get("headers")
        # The period LABELS matter as much as the values: a new quarter
        # appearing is the single most important trigger there is.
        payload.append([str(h) for h in list(headers)[-6:]]
                       if isinstance(headers, Sequence) and not isinstance(headers, str) else [])
        payload.append(_rows(quarters, _QUARTER_ROWS, 6))
    if isinstance(profit_loss, Mapping):
        headers = profit_loss.get("headers")
        payload.append([str(h) for h in list(headers)[-2:]]
                       if isinstance(headers, Sequence) and not isinstance(headers, str) else [])
        payload.append(_rows(profit_loss, _ANNUAL_ROWS, 2))

    if not any(any(x) for x in payload if isinstance(x, list)):
        return ""

    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def is_stale(debate: Mapping | None, bundle: Mapping | None) -> bool:
    """Should this company be argued again? PURE.

    True when the filings have moved since the debate was written.

    False when they have not, AND when we cannot tell -- an older debate
    carrying no fingerprint at all is left alone rather than re-argued, because
    treating "unknown" as "changed" would re-run the entire back catalogue the
    first time this ships, at real cost, for no new information. Those files
    gain a fingerprint the next time they are rebuilt for any other reason.
    """
    if not isinstance(debate, Mapping):
        return True                      # nothing published yet
    stored = debate.get(FINGERPRINT_KEY)
    if not stored or not isinstance(stored, str):
        return False                     # cannot tell; leave it alone
    current = fingerprint(bundle)
    if not current:
        return False                     # cannot tell
    return stored != current
