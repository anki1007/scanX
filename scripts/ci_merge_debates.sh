#!/usr/bin/env bash
# Lay the shard artifacts onto docs/data/debate and rebuild the index.
#
# IDEMPOTENT, and that is the point: the push step re-runs this after resetting
# the tree to origin/main, so a concurrent push can be absorbed by replaying
# rather than by merging. Two runs adding the same company produced an add/add
# conflict that `git rebase` could not resolve, the push loop gave up, and two
# runs of ~4.8 hours each were thrown away with every debate they had baked.
#
# cp -n: origin's copy wins if the company is already there. Both sides are
# valid debates for that company, so the tie-break only has to be deterministic.
set -euo pipefail

SHARDS="${1:-/tmp/shards}"
OUT="docs/data/debate"

mkdir -p "$OUT"

# find|wc, not ls|grep -c: grep exits 1 when it matches nothing, so
# `grep -c ... || echo 0` prints BOTH grep's "0" and the fallback "0" and the
# variable becomes the two-line string "0\n0".
count() { find "$OUT" -maxdepth 1 -name '*.json' ! -name 'index.json' | wc -l; }
before=$(count)

# A shard's own index.json describes only that shard; the real one is rebuilt
# from what is on disk afterwards.
if [ -d "$SHARDS" ]; then
  find "$SHARDS" -name '*.json' ! -name 'index.json' \
    -exec cp -n {} "$OUT"/ \; 2>/dev/null || true
fi

python - <<'PY'
import sys
sys.path.insert(0, "scripts")
from refresh_debate import write_index
write_index("docs/data/debate")
PY

python scripts/redact_sources.py >/dev/null 2>&1 || true

after=$(count)
echo "merged shards: ${before} -> ${after} debates on disk"
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "ADDED=$((after - before))" >> "$GITHUB_ENV"
  echo "TOTAL=${after}" >> "$GITHUB_ENV"
fi
