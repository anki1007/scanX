#!/usr/bin/env python3
"""Lay shard debates onto docs/data/debate, keeping the BETTER of the two.

The predecessor used `cp -n`, which meant a company already on disk could never
be replaced. That quietly nullified the whole re-queue path: freshness marks a
half-baked debate (fewer turns than rounds*2) as incomplete, the planner hands
it to a shard, the shard spends minutes re-arguing it properly -- and then the
merge refused to overwrite the half-baked file, so the improvement was thrown
away and the company was re-queued again next run. Four runs a day, ~14 hours
of compute each, producing "+0".

`cp -n` was there so a replay onto a new tip is deterministic. Preferring the
more complete debate keeps that property -- completeness is a total order on
the pair, so replaying lands on the same file either way -- while letting real
improvements through.

STDLIB ONLY, and deliberately not importing earnings_intel: doing that in a CI
step pulled numpy in through the package __init__ and killed the job.

    python scripts/merge_shards.py /tmp/shards docs/data/debate
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def completeness(bundle) -> tuple:
    """How much debate is actually in here. Higher is better.

    turns first: a debate cut short mid-argument is the exact thing being
    replaced. Evidence breaks ties between two debates of equal length.
    """
    if not isinstance(bundle, dict):
        return (0, 0, 0)
    meta = bundle.get("_meta")
    meta = meta if isinstance(meta, dict) else {}

    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return (_int(meta.get("turns")), _int(meta.get("rounds")),
            _int(meta.get("evidence_items")))


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def should_replace(incoming, existing) -> bool:
    """True when the incoming debate is strictly more complete.

    Ties keep what is already on disk, so a replay is idempotent and two runs
    that baked the same company do not fight.
    """
    if incoming is None:
        return False
    if existing is None:
        return True
    return completeness(incoming) > completeness(existing)


def merge(shards: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    added = improved = kept = unreadable = 0

    if shards.exists():
        for src in sorted(shards.rglob("*.json")):
            if src.name == "index.json":
                continue
            incoming = _load(src)
            if incoming is None:
                unreadable += 1
                continue
            dst = out / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
                added += 1
                continue
            if should_replace(incoming, _load(dst)):
                shutil.copyfile(src, dst)
                improved += 1
            else:
                kept += 1

    total = sum(1 for p in out.glob("*.json") if p.name != "index.json")
    return {"added": added, "improved": improved, "kept": kept,
            "unreadable": unreadable, "total": total}


def main(argv) -> int:
    shards = Path(argv[1] if len(argv) > 1 else "/tmp/shards")
    out = Path(argv[2] if len(argv) > 2 else "docs/data/debate")
    stats = merge(shards, out)

    print(f"[merge] {stats['added']} new, {stats['improved']} improved, "
          f"{stats['kept']} kept (already as good or better), "
          f"{stats['unreadable']} unreadable -> {stats['total']} on disk")

    # ADDED is what CHANGED, not the change in file count: an improved debate
    # replaces a file, so counting files reported 0 and the commit step then
    # said "nothing new to publish" and exited green on a discarded run.
    env = os.environ.get("GITHUB_ENV")
    if env:
        with open(env, "a", encoding="utf-8") as fh:
            fh.write(f"ADDED={stats['added'] + stats['improved']}\n")
            fh.write(f"NEW_DEBATES={stats['added']}\n")
            fh.write(f"IMPROVED={stats['improved']}\n")
            fh.write(f"TOTAL={stats['total']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
