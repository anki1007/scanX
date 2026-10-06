"""
Is an FPI fortnight due? Exit 0 = yes (fetch), 1 = no (already have it).

NSDL publishes sector-wise FPI data for each fortnight (1-15, 16-month end) a
few days after it closes. The watch job runs every night but only fetches
while the latest CLOSED fortnight is missing from docs/data/fpi.json. Once it
lands the job goes quiet until the next fortnight closes.

    python scripts/fpi_due.py            # prints the decision
"""
from __future__ import annotations

import calendar
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def latest_closed_fortnight(today: date) -> date:
    """The most recent fortnight end (15th or month end) strictly before today."""
    if today.day > 15:
        return today.replace(day=15)
    prev = today.replace(day=1) - timedelta(days=1)
    return prev.replace(day=calendar.monthrange(prev.year, prev.month)[1])


def have_ends(path: Path) -> set:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    except Exception:  # noqa: BLE001
        return set()
    return {str(r.get("end")) for r in rows if isinstance(r, dict)}


def main(argv=None) -> int:
    today = datetime.now(IST).date()
    want = latest_closed_fortnight(today)
    if want.isoformat() in have_ends(ROOT / "docs" / "data" / "fpi.json"):
        print(f"[fpi-watch] fortnight ending {want} already published - nothing to do "
              f"until the next one closes")
        return 1
    print(f"[fpi-watch] fortnight ending {want} not published yet - fetching")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
