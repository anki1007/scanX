#!/usr/bin/env bash
# The market-hours loop: quotes every 15 minutes, the event boards every hour,
# until shortly after the close -- all from ONE job.
#
# Why a loop and not a */15 cron: GitHub's shared scheduler fired the old
# "7,27,47 3-10" quotes cron about twice a weekday (once at ~14:00 IST and once
# long after the close), not 24 times. Whichever firing lands first now keeps
# going until END_UTC. A job may run 6 hours and the session plus a margin is
# longer than that, so a loop that reaches MAX_MINUTES before the close
# dispatches its own successor, which queues behind it and carries on. One
# early dispatch therefore covers the whole day:
#     gh workflow run market-quotes.yml -R anki1007/scanX
#
# Fundamentals are not here: they only change when a company files results,
# and the nightly bake owns them. These boards are announcement-driven, so an
# hour is the useful cadence.
set -u

END_UTC="${END_UTC:-10:10}"        # 15:40 IST - one cycle after the 15:30 close
MAX_MINUTES="${MAX_MINUTES:-330}"  # stay well inside the 6-hour job cap
QUOTES_EVERY="${QUOTES_EVERY:-15}" # minutes
BOARDS_EVERY="${BOARDS_EVERY:-60}" # minutes

QUOTE_FILES="docs/data/quotes.json docs/data/quotes_wide.json"
BOARD_FILES="docs/data/pead.json docs/data/pead.csv docs/data/meta.json
  docs/data/deals.json docs/data/actions.json docs/data/announcements.json
  docs/data/demergers.json docs/data/demergers_meta.json
  docs/data/special.json docs/data/special_meta.json
  docs/data/buybacks.json docs/data/buybacks_meta.json
  docs/data/orders.json docs/data/orders_companies.json docs/data/orders_meta.json"

git config user.name  "scanx-bot"
git config user.email "scanx@users.noreply.github.com"

# Publish exactly the files this loop owns. The old push retry rebased with
# -X theirs, which can splice two generations of a JSON file hunk by hunk
# into something that parses as neither. Instead: set our files aside, move
# to the current remote head, lay them back down, commit, push -- a replay,
# never a textual merge. The checkout also stays current between cycles, so
# the scripts read tonight's bundles rather than this morning's.
publish() {
  msg="$1"; shift
  keep="$(mktemp -d)"; owned=""
  # Only files this cycle actually rewrote. A board that failed left the copy
  # from the last reset in place; replaying that over the remote would put an
  # older file back over a newer one the nightly job may have pushed since.
  # (Existing files only, too: one missing path fails a whole git add.)
  for f in $(git status --porcelain --untracked-files=all -- "$@" | cut -c4-); do
    if [ -f "$f" ]; then
      mkdir -p "$keep/$(dirname "$f")" && cp "$f" "$keep/$f" && owned="$owned $f"
    fi
  done
  n=0
  while [ -n "$owned" ]; do
    if git fetch -q origin main && git reset -q --hard origin/main; then
      for f in $owned; do cp "$keep/$f" "$f"; done
      # shellcheck disable=SC2086
      git add -- $owned
      if git diff --cached --quiet; then echo "no changes to publish"; break; fi
      if git commit -q -m "$msg" && git push -q origin HEAD:main; then
        echo "published: $msg"; break
      fi
    fi
    n=$((n+1))
    if [ "$n" -gt 4 ]; then echo "::warning::push failed after retries ($msg)"; break; fi
    sleep $((n * 7))
  done
  rm -rf "$keep"
}

declare -A FAILS=()
BOARD_RUNS=0
board() {  # board <name> <command...>: one failure never stops the loop
  name="$1"; shift
  echo "::group::$name"
  "$@"; rc=$?
  echo "::endgroup::"
  if [ $rc -ne 0 ]; then
    FAILS[$name]=$(( ${FAILS[$name]:-0} + 1 ))
    echo "::warning::$name failed (rc=$rc)"
  fi
  return 0
}

start=$(date -u +%s)
close=$(date -u -d "today $END_UTC" +%s)
stop=$(( start + MAX_MINUTES * 60 ))
[ "$close" -lt "$stop" ] && stop=$close
# No session on a weekend: a manual run there is one refresh, not a day's loop.
[ "$(date -u +%u)" -ge 6 ] && { stop=$start; close=$start; }
last_boards=0
cycle=0

while :; do
  now=$(date -u +%s); cycle=$((cycle + 1))
  echo "== cycle $cycle at $(date -u +%H:%MZ)"
  files="$QUOTE_FILES"

  python scripts/refresh_quotes.py || echo "quotes unavailable this cycle"
  if [ -n "${UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN:-}" ]; then
    python scripts/refresh_quotes.py --wide || echo "wide quotes unavailable this cycle"
  fi

  if [ $(( now - last_boards )) -ge $(( BOARDS_EVERY * 60 )) ]; then
    last_boards=$now; BOARD_RUNS=$((BOARD_RUNS + 1))
    board pead       python scripts/refresh_scanx.py --pages 8
    board pulse      python scripts/refresh_marketpulse.py
    board demergers  python scripts/refresh_demergers.py
    board special    python scripts/refresh_special.py
    board buybacks   python scripts/refresh_buybacks.py --months 12
    board orders     python scripts/refresh_orders.py --months 3
    files="$files $BOARD_FILES"
  fi

  # The site never names its data vendor. The nightly job redacts the whole
  # tree; this redacts what the loop is about to publish, quotes included --
  # the wide feed writes its source line on every cycle.
  # shellcheck disable=SC2086
  python scripts/redact_sources.py $files || true

  # shellcheck disable=SC2086
  publish "intraday: $(date -u +%H:%MZ)" $files

  next=$(( now + QUOTES_EVERY * 60 ))
  [ "$next" -ge "$stop" ] && break
  wait_s=$(( next - $(date -u +%s) ))
  [ "$wait_s" -gt 0 ] && sleep "$wait_s"
done

echo "loop done after $cycle cycle(s), $BOARD_RUNS board pass(es)"
if [ "$(date -u +%s)" -lt "$(( close - QUOTES_EVERY * 60 ))" ]; then
  # Stopped on MAX_MINUTES with the session still open. A workflow_dispatch
  # made with the job token does start a run; it queues behind this one in
  # the concurrency group and picks the loop up where this one stops.
  if gh workflow run market-quotes.yml --ref main >/dev/null 2>&1; then
    echo "session still open - dispatched the next loop"
  else
    echo "::warning::session still open but the next loop could not be dispatched"
  fi
fi
# A board that failed once in a day of hourly runs is a blip and the next hour
# repairs it. One that failed on EVERY pass is down, and the run goes red so
# it is seen: a silent green run is how the last outage lasted three weeks.
# A single-pass run (a firing that landed after the close) is left to the
# nightly job, which runs the same boards and fails on the same outage.
down=""
for name in "${!FAILS[@]}"; do
  echo "  $name failed ${FAILS[$name]}/$BOARD_RUNS"
  if [ "$BOARD_RUNS" -ge 2 ] && [ "${FAILS[$name]}" -ge "$BOARD_RUNS" ]; then
    down="$down $name"
  fi
done
if [ -n "$down" ]; then
  echo "::error::down on every pass today:$down"
  exit 1
fi
exit 0
