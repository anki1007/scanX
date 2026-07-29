#!/usr/bin/env bash
# Commit and push whatever the previous step generated.
#
# Called once per refresh phase so the cheap deterministic boards are LIVE
# before the LLM-priced ones spend a token — a job timeout in the expensive
# tail then costs only that tail, instead of discarding every board's data
# because the single commit step at the end never ran.
set -u
msg="${1:-data: cloud refresh}"
git config user.name  "scanx-bot"
git config user.email "scanx@users.noreply.github.com"
git add -A docs FPI 2>/dev/null || true
if git diff --cached --quiet; then
  echo "no data changes"; exit 0
fi
git commit -m "$msg $(date -u +%FT%TZ)"
# push with retry; works on a detached HEAD; on a race with another publisher,
# rebase keeping OUR freshly generated data (-X theirs)
n=0
until git push origin HEAD:main; do
  n=$((n+1)); [ "$n" -gt 3 ] && { echo "push failed after retries"; exit 1; }
  git fetch origin main
  git rebase -X theirs origin/main || { git rebase --abort; exit 1; }
done
