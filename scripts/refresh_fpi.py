"""
FPI publisher — surfaces NSDL fortnightly FPI sector data inside scanX.

1) Best-effort runs FPI/fpi_update.py (fetches any newly-published NSDL fortnight;
   a no-op (~1s) when nothing new — NSDL posts after the 15th and month-end, often
   with a 2-4 day lag, so running daily safely catches it).
2) Publishes FPI/fpi_data.json -> docs/data/fpi.json (atomic) for the FPI tab.

    python scripts/refresh_fpi.py            # update + publish
    python scripts/refresh_fpi.py --no-update  # just publish the existing file
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FPI = ROOT / "FPI"
SRC = FPI / "fpi_data.json"
DST = ROOT / "docs" / "data" / "fpi.json"


def _atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Publish NSDL FPI fortnightly data into scanX")
    ap.add_argument("--no-update", action="store_true", help="skip the NSDL fetch, just publish existing JSON")
    args = ap.parse_args()

    upd = FPI / "fpi_update.py"
    if upd.exists() and not args.no_update:
        try:
            r = subprocess.run([sys.executable, "fpi_update.py"], cwd=str(FPI),
                               timeout=420, capture_output=True, text=True)
            tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-1:] or [""]
            print(f"[fpi] updater rc={r.returncode}: {tail[0][:90]}")
        except Exception as e:  # noqa: BLE001
            print(f"[fpi] updater skipped ({type(e).__name__}: {e}) — publishing existing data")

    if not SRC.exists():
        print("[fpi] no FPI/fpi_data.json yet — run FPI/fpi_update.py once"); return
    try:
        j = json.loads(SRC.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[fpi] could not read FPI/fpi_data.json: {e}"); return
    rows = j.get("rows", [])
    DST.parent.mkdir(parents=True, exist_ok=True)
    _atomic(DST, json.dumps(j, separators=(",", ":")))
    fortnights = len({r.get("end") for r in rows})
    print(f"[fpi] published {len(rows)} rows ({fortnights} fortnights) -> docs/data/fpi.json")


if __name__ == "__main__":
    main()
