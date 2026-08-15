#!/usr/bin/env bash
# Lay the shard artifacts onto docs/data/debate and rebuild the index.
#
# IDEMPOTENT, and that is the point: the push step re-runs this after resetting
# the tree to origin/main, so a concurrent push can be absorbed by replaying
# rather than by merging. Two runs adding the same company produced an add/add
# conflict that `git rebase` could not resolve, the push loop gave up, and two
# runs of ~4.8 hours each were thrown away with every debate they had baked.
#
# The tie-break is COMPLETENESS, not "whoever got there first". A no-clobber
# copy meant a company already on disk could never be replaced, which nullified
# the re-queue path: a half-baked debate was marked incomplete, re-argued
# by a shard over several minutes, then dropped on the floor here -- so it
# stayed half-baked and was re-queued again next run, four times a day, while
# the run reported success. merge_shards.py keeps whichever debate has more
# turns, which is still a deterministic tie-break and still safe to replay.
set -euo pipefail

SHARDS="${1:-/tmp/shards}"
OUT="docs/data/debate"

mkdir -p "$OUT"

# A shard's own index.json describes only that shard; the real one is rebuilt
# from what is on disk afterwards. merge_shards.py also exports ADDED/TOTAL,
# counting what CHANGED rather than the change in file count -- an improved
# debate replaces a file, so the old arithmetic scored it 0 and the commit
# step concluded there was "nothing new to publish".
python scripts/merge_shards.py "$SHARDS" "$OUT"

python - <<'PY'
import sys
sys.path.insert(0, "scripts")
from refresh_debate import write_index
write_index("docs/data/debate")
PY

python scripts/redact_sources.py >/dev/null 2>&1 || true
