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

__all__ = ["fingerprint", "is_stale", "is_incomplete", "attempts_of",
           "FINGERPRINT_KEY", "ATTEMPTS_KEY", "MAX_ATTEMPTS"]

#: Where the fingerprint lives in a published debate file.
FINGERPRINT_KEY = "inputs"

#: How many times this company has been argued into the current file.
ATTEMPTS_KEY = "attempts"

#: Give up re-arguing an incomplete debate after this many tries.
#:
#: Some companies genuinely cannot fill four turns -- too little evidence for
#: a bear to say anything, and the argument stops early every time. Retrying
#: those forever would burn a slot on every run and never improve. Three tries
#: is enough to clear a transient cause (a timed-out turn, a rate-limited
#: provider) without chasing a permanent one.
MAX_ATTEMPTS = 3

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


def is_incomplete(debate: Mapping | None) -> bool:
    """Did this debate stop short of the rounds it was asked for? PURE.

    A round is one turn each side, so N rounds should produce 2N turns. Fewer
    means turns were dropped -- a timed-out call on a slow local model, a
    rate-limited provider, a reply with no valid citation. The file is still
    published, still dated, still confident, and reads as a finished argument.

    486 of the 2,004 debates on disk are in exactly that state: 250 with three
    turns out of four, 69 with one. That is the "half baked" complaint, and
    nothing re-queued them, because their fingerprints were perfectly valid.

    Returns False when the counts are missing or cannot be trusted -- unknown
    is not incomplete.

    The trust test matters. `rounds_run` in refresh_debate derives the round
    count from a `round` field on each turn, and FALLS BACK to the turn count
    when the turns do not carry one. So `turns == rounds` is the signature of a
    shape this rule cannot read, not of a half-finished argument: reading it as
    incomplete would re-queue perfectly good debates up to the retry cap. Those
    are left alone unless the debate module says outright that it dropped a
    turn, which is authoritative whatever the shape.
    """
    if not isinstance(debate, Mapping):
        return False
    meta = debate.get("_meta")
    if not isinstance(meta, Mapping):
        return False

    dropped = meta.get("turns_dropped")
    if isinstance(dropped, int) and dropped > 0:
        return True

    turns, rounds = meta.get("turns"), meta.get("rounds")
    if not isinstance(turns, int) or not isinstance(rounds, int) or rounds <= 0:
        return False
    if turns == rounds:
        return False                     # cannot tell; see above
    return turns < rounds * 2


def attempts_of(debate: Mapping | None) -> int:
    """How many times this company has been argued into its current file."""
    if not isinstance(debate, Mapping):
        return 0
    value = debate.get(ATTEMPTS_KEY)
    return value if isinstance(value, int) and value >= 0 else 0


def is_stale(debate: Mapping | None, bundle: Mapping | None,
             *, max_attempts: int = MAX_ATTEMPTS) -> bool:
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

    # A debate that stopped short is due again whatever its fingerprint says.
    # This is the case the fingerprint alone gets wrong: the filings have not
    # moved, so the numbers agree, but the argument was never finished. Capped
    # so a company that simply cannot fill four turns is not retried forever.
    if is_incomplete(debate) and attempts_of(debate) < max_attempts:
        return True

    stored = debate.get(FINGERPRINT_KEY)
    if not stored or not isinstance(stored, str):
        return False                     # cannot tell; leave it alone
    current = fingerprint(bundle)
    if not current:
        return False                     # cannot tell
    return stored != current
